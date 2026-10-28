"""Native .kicad_sch writer.

Takes the circuit model, resolved symbol definitions, placements and wire
routes, and produces a valid KiCad 9 schematic file. All output is built as
S-expression trees (see ``core/sexpr.py``) — never raw string
concatenation — so quoting, escaping and structure are handled in one place.

The writer generates a fresh UUID for every object and embeds every used
symbol definition into the file's ``lib_symbols`` section, as KiCad requires.

It also creates the KiCad project file (``.kicad_pro``) next to the root
schematic: a folder with a ``.kicad_pro`` is a KiCad project, so the
generated schematic can later be joined by a PCB layout (``.kicad_pcb``)
under the same project.
"""

from __future__ import annotations

import json
import os
import uuid as _uuid
from dataclasses import dataclass, field

from kicadgen.core import sexpr
from kicadgen.core.sexpr import Q
from .layout import LayoutResult, Placement, Routing, snap
from kicadgen.core.model import Circuit, GraphicLine, GraphicText
from kicadgen.symbols.resolve import SymbolDef

VERSION = "20250114"
GENERATOR = "kicadgen"
GENERATOR_VERSION = "9.0"


def new_uuid() -> str:
    """Fresh unique identifier for a newly created object."""
    return str(_uuid.uuid4())


def N(*items) -> list:
    """Build an S-expression node: ``N("wire", (atom, value), [child], ...)``.

    Items are either:
    - a ``list``            -> nested node
    - an ``atom, value`` 2-tuple -> bare atom pair, e.g. ``("width", 0)``
    - a str/int/float       -> plain atom
    """
    node: list = []
    for item in items:
        if isinstance(item, tuple):
            node.append(str(item[1]))
        else:
            node.append(item)
    return node


def Nq(*items) -> list:
    """Like :func:`N` but string values become quoted atoms."""
    node: list = []
    for item in items:
        if isinstance(item, tuple):
            node.append(Q(str(item[1])))
        elif isinstance(item, str):
            node.append(Q(item))
        else:
            node.append(item)
    return node


def xy(p: tuple[float, float]) -> list:
    return Nq("pts", Nq("xy", round(p[0], 4), round(p[1], 4)))


# Valid KiCad sheet pin directions (sheet box pins and hierarchical label
# shapes). Symbol-style ``power_in``/``power_out`` do not exist as sheet
# direction (verified: a demo sheet node with ``power_out`` refuses to
# load), so they are mapped to the closest allowed direction.
_SHEET_DIRECTIONS = {
    "power_in": "input",
    "power_out": "output",
}


def sheet_direction(direction: str) -> str:
    """Map a spec direction to a valid KiCad sheet-pin direction."""
    if direction not in {"input", "output", "bidirectional", "tri_state",
                         "passive"}:
        mapped = _SHEET_DIRECTIONS.get(direction)
        if mapped is None:
            raise ValueError(
                f"unknown sheet pin direction {direction!r}; expected one of"
                " input, output, bidirectional, tri_state, passive,"
                " power_in, power_out"
            )
        return mapped
    return direction


@dataclass
class WriterOptions:
    project: str = "design"
    title: str = ""
    revision: str = ""
    company: str = ""
    # Root document uuid of this file (default: generated fresh).
    root_uuid: str | None = None
    # Symbol instance path prefix inside the hierarchy ("/<root>" for a
    # root sheet, "/<parent-root>/<sheet-uuid>" for a sub-sheet file).
    instance_path: str | None = None
    # Sub-sheet boxes to draw (already positioned by the layout engine).
    sheets: list = field(default_factory=list)
    # Ordered page numbers for the sheets (parallel to ``sheets``).
    sheet_pages: list[str] = field(default_factory=list)
    # Omit the ``(sheet_instances ...)`` node (sub-sheet files).
    omit_sheet_instances: bool = False


class SchematicWriter:
    """Writes the whole schematic tree and serializes it."""

    def __init__(
        self,
        circuit: Circuit,
        resolved: dict[str, SymbolDef],
        layout_result: LayoutResult,
        options: WriterOptions | None = None,
    ):
        self.circuit = circuit
        self.resolved = resolved
        self.layout = layout_result
        self.options = options or WriterOptions(project=circuit.design.name)
        self.root_uuid = options.root_uuid or new_uuid()

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def build(self) -> list:
        sch: list = [Q("kicad_sch")]
        sch.append(N("version", VERSION))
        sch.append(Nq("generator", GENERATOR))
        sch.append(Nq("generator_version", GENERATOR_VERSION))
        sch.append(N("uuid", Q(self.root_uuid)))
        design = self.circuit.design
        if design.is_custom_size():
            # Verified KiCad 9 custom-page syntax: the type first, then both
            # sizes in one node. A bare ``(paper <w> <h>)`` fails to load.
            sch.append(Nq("paper", "User",
                          round(design.width, 2), round(design.height, 2)))
        else:
            sch.append(Nq("paper", design.paper))
        title_node = Nq("title_block", Nq("title", self.options.title or self.circuit.design.name))
        if self.circuit.design.revision:
            title_node.append(Nq("rev", self.circuit.design.revision))
        if self.options.company:
            title_node.append(Nq("company", self.options.company))
        sch.append(title_node)

        sch.append(N("lib_symbols", *[self._embed_symbol(d) for d in self._used_defs()]))

        for ref, placement in self.layout.placements.items():
            sch.append(self._symbol_instance(placement))

        for polyline in self.layout.routing.wires:
            sch.append(self._wire(polyline))
        for text, pos, rot, justify, shape in self.layout.routing.labels:
            sch.append(self._label(text, pos, rot, justify, shape))

        # Sheet annotations: layout-computed block frames (separator lines
        # + headings) plus the spec-declared lines and texts.
        for item in self.layout.decorations:
            if isinstance(item, GraphicLine):
                sch.append(self._graphic_line(item))
            else:
                sch.append(self._graphic_text(item))
        for line_ in self.circuit.graphic_lines:
            sch.append(self._graphic_line(line_))
        for text_ in self.circuit.graphic_texts:
            sch.append(self._graphic_text(text_))

        for page, box in enumerate(
                self.layout.sheets, start=2):
            sch.append(self._sheet_node(box, page))

        if not self.options.omit_sheet_instances:
            sch.append(
                N("sheet_instances", Nq("path", "/", Nq("page", "1")))
            )
        sch.append(N("embedded_fonts", "no"))
        return sch

    def _used_defs(self) -> list[SymbolDef]:
        seen: list[SymbolDef] = []
        for comp in self.circuit.components:
            definition = self.resolved[comp.ref]
            if definition not in seen:
                seen.append(definition)
        return seen

    def _embed_symbol(self, definition: SymbolDef) -> list:
        """Copy the resolved library subtree into ``lib_symbols``."""
        return definition.subtree

    def _symbol_field(
        self, name: str, value: str, at: tuple[float, float], angle: int, hidden: bool
    ) -> list:
        node = Nq("property", name, value)
        pos = N("at", round(at[0], 4), round(at[1], 4), angle)
        node.append(pos)
        effects = N("effects", N("font", Nq("size", 1.27, 1.27)))
        if hidden:
            effects.append(N("hide", "yes"))
        node.append(effects)
        return node

    def _symbol_instance(self, placement: Placement) -> list:
        definition = placement.symbol
        node: list = [Q("symbol")]
        node.append(Nq("lib_id", definition.lib_id))
        node.append(N("at", placement.x, placement.y, placement.rot))
        node.append(N("unit", placement.unit))
        node.append(N("exclude_from_sim", "yes" if definition.exclude_from_sim else "no"))
        node.append(N("in_bom", "yes" if definition.in_bom else "no"))
        node.append(N("on_board", "yes" if definition.on_board else "no"))
        node.append(N("dnp", "no"))
        node.append(N("uuid", Q(new_uuid())))

        component = self.circuit.component(placement.ref)
        value = component.value if component and component.value else definition.lib_id.split(":", 1)[1]
        footprint = component.footprint if component else ""

        # Field positions: clear of the symbol body, angle keeps text upright.
        extent = self._vertical_extent(definition)
        ref_angle, val_angle = self._field_angles(placement.rot)
        if placement.rot in (90, 270):
            ref_at = (snap(placement.x - extent - 5.08), snap(placement.y))
            val_at = (snap(placement.x + extent + 5.08), snap(placement.y))
        else:
            ref_at = (snap(placement.x), snap(placement.y - extent - 5.08))
            val_at = (snap(placement.x), snap(placement.y + extent + 5.08))
        node.append(self._symbol_field("Reference", placement.ref, ref_at, ref_angle, False))
        node.append(self._symbol_field("Value", value, val_at, val_angle, False))
        node.append(self._symbol_field("Footprint", footprint, (placement.x, placement.y), 0, True))
        node.append(self._symbol_field("Datasheet", "~", (placement.x, placement.y), 0, True))
        node.append(self._symbol_field("Description", "", (placement.x, placement.y), 0, True))

        for number in definition.pins:
            node.append(N("pin", Q(number), N("uuid", Q(new_uuid()))))

        instance_path = self.options.instance_path or f"/{self.root_uuid}"
        instances = N(
            "instances",
            Nq("project", self.options.project,
               N("path", Q(instance_path),
                 Nq("reference", placement.ref),
                 N("unit", placement.unit))),
        )
        node.append(instances)
        return node

    @staticmethod
    def _vertical_extent(definition: SymbolDef) -> float:
        ys = [abs(p.y) for p in definition.pins.values()]
        return max(ys) if ys else 5.08

    @staticmethod
    def _field_angles(rot: int) -> tuple[int, int]:
        """Field text angles that keep text readable (per format notes)."""
        if rot == 90:
            return 270, 270
        if rot == 270:
            return 90, 90
        return 0, 0

    def _wire(self, polyline: list[tuple[float, float]]) -> list:
        node = N("wire")
        pts = N("pts")
        for p in polyline:
            pts.append(Nq("xy", round(p[0], 4), round(p[1], 4)))
        node.append(pts)
        node.append(N("stroke", N("width", 0), N("type", "default")))
        node.append(N("uuid", Q(new_uuid())))
        return node

    def _label(self, text: str, pos: tuple[float, float], rot: int,
               justify: str = "left", shape: str | None = None) -> list:
        token = "hierarchical_label" if shape else "label"
        node = N(token, Q(text))
        if shape:
            node.append(N("shape", sheet_direction(shape)))
        node.append(N("at", round(pos[0], 4), round(pos[1], 4), rot))
        effects = N("effects", N("font", Nq("size", 1.27, 1.27)))
        if rot == 180:
            effects.append(N("justify", "right"))
        elif rot in (90, 270):
            effects.append(N("justify", "left", "bottom"))
        else:
            effects.append(N("justify", justify))
        node.append(effects)
        node.append(N("uuid", Q(new_uuid())))
        return node

    def _graphic_line(self, line: GraphicLine) -> list:
        """A graphical line, stored as a ``(polyline ...)`` node — the
        token KiCad schematics use for sheet graphics (no electrical role)."""
        node = N("polyline")
        pts = N("pts")
        pts.append(Nq("xy", round(line.x1, 4), round(line.y1, 4)))
        pts.append(Nq("xy", round(line.x2, 4), round(line.y2, 4)))
        node.append(pts)
        node.append(N("stroke", N("width", line.width),
                      N("type", line.style)))
        node.append(N("uuid", Q(new_uuid())))
        return node

    def _graphic_text(self, text: GraphicText) -> list:
        """A graphical text; anchored at its horizontal centre, baseline at
        ``y``. Underlines (KiCad ``~`` escaping) follow sexpr quoting via
        :func:`Q`."""
        node = N("text", Q(text.content))
        node.append(N("at", round(text.x, 4), round(text.y, 4), 0))
        font = N("font", Nq("size", round(text.size, 4), round(text.size, 4)))
        if text.bold:
            font.append(N("bold", "yes"))
        effects = N("effects", font)
        if text.justify:
            effects.append(N("justify", text.justify))
        node.append(effects)
        node.append(N("uuid", Q(new_uuid())))
        return node

    def _sheet_node(self, box, page: int) -> list:
        """One ``(sheet ...)`` hierarchical sub-sheet box (verified syntax:
        KiCad demos, ``multichannel_mixer``). KiCad sheet pins accept only
        input/output/bidirectional/tri_state/passive directions; a symbol
        ``power_in``/``power_out`` direction must be mapped (verified by
        transplanting a demo sheet node: ``power_out`` refuses to load)."""
        node: list = [Q("sheet")]
        node.append(N("at", box.x, box.y))
        node.append(N("size", box.w, box.h))
        node.append(N("exclude_from_sim", "no"))
        node.append(N("in_bom", "yes"))
        node.append(N("on_board", "yes"))
        node.append(N("dnp", "no"))
        node.append(N(
            "stroke", N("width", 0.254), N("type", "solid")))
        node.append(N("fill", N("color", "0", "0", "0", "0.0000")))
        node.append(N("uuid", Q(box.uuid)))
        # Sheet name centred above the box, file name centred below it:
        # no left/right justify token means centred horizontally in KiCad.
        node.append(Nq(
            "property", "Sheetname", box.title,
            N("at", round(box.x + box.w / 2, 4), round(box.y - 1.27, 4), 0),
            N("effects", N("font", Nq("size", 1.905, 1.905)),
              N("justify", "bottom")),
        ))
        node.append(Nq(
            "property", "Sheetfile", box.filename,
            N("at", round(box.x + box.w / 2, 4),
              round(box.y + box.h + 0.762, 4), 0),
            N("effects", N("font", Nq("size", 1.27, 1.27)),
              N("justify", "top")),
        ))
        for name, direction, pos, side in box.pins:
            # Edge encoding follows the KiCad demos: left-edge pins carry
            # rotation 180 + justify left, right-edge pins rotation 0 +
            # justify right. A rotation-0 left-edge pin makes KiCad mirror
            # the pin name onto the opposite (right) border, where it
            # overlaps the border and the connection bubbles.
            rot = 180 if side == "left" else 0
            node.append(N(
                "pin", Q(name), sheet_direction(direction),
                N("at", round(pos[0], 4), round(pos[1], 4), rot),
                N("uuid", Q(new_uuid())),
                N("effects", N("font", Nq("size", 1.27, 1.27)),
                  N("justify", "left" if side == "left" else "right")),
            ))
        node.append(N(
            "instances",
            Nq("project", self.options.project,
               N("path", Q(f"/{self.root_uuid}"),
                 Nq("page", str(page)))),
        ))
        return node

    # ------------------------------------------------------------------

    def render(self) -> str:
        return sexpr.dump(self.build())


def write_schematic(
    circuit: Circuit,
    resolved: dict[str, SymbolDef],
    layout_result: LayoutResult,
    path: str,
    options: WriterOptions | None = None,
) -> str:
    """Full pipeline step: write ``path`` and return the rendered text."""
    writer = SchematicWriter(circuit, resolved, layout_result, options)
    text = writer.render()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# ----------------------------------------------------------------------
# KiCad project file (.kicad_pro)
#
# A schematic is only a full "KiCad project" when a .kicad_pro with the
# same stem sits in its folder; that is also what makes the folder ready
# to hold a PCB layout (.kicad_pcb) later. The project body below keeps to
# the defaults KiCad itself writes for a new, empty project; every missing
# KiCad-internal setting falls back to KiCad's own defaults when the
# project is loaded and saved.
# ----------------------------------------------------------------------

def _project_body(name: str) -> dict:
    return {
        "board": {},
        "boards": [],
        "cvpcb": {
            "equivalence_files": [],
        },
        "libraries": {
            "pinned_footprint_libs": [],
            "pinned_symbol_libs": [],
        },
        "meta": {
            "filename": name + ".kicad_pro",
            "version": 3,
        },
        "net_settings": {
            "classes": [
                {
                    "bus_width": 12,
                    "clearance": 0.2,
                    "diff_pair_gap": 0.25,
                    "diff_pair_via_gap": 0.25,
                    "diff_pair_width": 0.2,
                    "line_style": 0,
                    "microvia_diameter": 0.3,
                    "microvia_drill": 0.1,
                    "name": "Default",
                    "pcb_color": "rgba(0, 0, 0, 0.000)",
                    "schematic_color": "rgba(0, 0, 0, 0.000)",
                    "track_width": 0.2,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "wire_width": 6,
                }
            ],
            "meta": {
                "version": 3,
            },
        },
        "pcbnew": {
            "last_paths": {
                "gencad": "",
                "idf": "",
                "netlist": "",
                "specctra_dsn": "",
                "step": "",
                "vrml": "",
            },
            "page_layout_descr_file": "",
        },
        "schematic": {
            "annotate_start_num": 0,
            "legacy_lib_dir": "",
            "legacy_lib_list": [],
        },
        "sheets": [],
        "text_variables": {},
    }


def write_project_file(schematic: str) -> str | None:
    """Create ``<stem>.kicad_pro`` next to a root schematic, once.

    Returns the project path when created, ``None`` when it already
    exists (re-runs never overwrite user-edited project settings, and
    sub-sheets never get a project file of their own — one project per
    folder).
    """
    base = os.path.splitext(schematic)[0]
    project = base + ".kicad_pro"
    if os.path.exists(project):
        return None
    name = os.path.basename(base)
    with open(project, "w", encoding="utf-8") as fh:
        json.dump(_project_body(name), fh, indent=2)
        fh.write("\n")
    return project
