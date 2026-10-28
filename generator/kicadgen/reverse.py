"""Reverse pass: rebuild a Python spec from a generated .kicad_sch.

The forward direction (``api.py`` driving ``layout.py`` + ``writer.py``)
turns a :class:`~kicadgen.api.Design` into a schematic. This module reads
the schematic back and reconstructs an equivalent spec: the file records
placement, symbol instances, net labels, wire stubs, block frames and
hierarchical sheet boxes, and every one of them has a forward API call
attached. Reconstruction rules per element:

- symbol instance            -> ``Component(ref=..., symbol=..., ...)``;
  the lib_id, origin ``at``, rotation, unit and the displayed
  ``Value``/``Footprint`` properties are all in the instance;
- a lib id the installed KiCad libraries do not resolve (the project's
  board/breakout symbols) is declared again with
  ``custom_symbol(...) + pin(...)``, decoded from the embedded
  ``lib_symbols`` subtree (number, name, electrical type, side);
- net labels + wire stubs    -> ``connect(...)``: every pin stub carries a
  label of the net name at its free end, and the stub's other endpoint
  lands on exactly one symbol pin (the same ``pin_projected`` position the
  forward engine computed), so all stubs sharing a label text form one
  net. Sheet-box stubs carry the same names but join through the box, so
  their box-edge wire origins are excluded from the clustering. Labels of
  auto-generated nets (``N$<n>``) emit a nameless ``connect()``, which the
  forward pass re-numbers identically because the label order preserves
  the original net-creation order;
- dashed-frame rectangles    -> ``mark_blocks({...})``; the forward engine
  draws each frame edge as a separate 2-point dashed polyline whose
  endpoints are shared, so point union-find reassembles the rectangles
  and the heading it stamps at ``(left + 2.54, top + 3.81)`` is recovered.
  Headings survive, but block keys are not text in the file: blocks are
  renumbered ``block1, block2, ...``;
- ``(sheet ...)`` boxes      -> ``add_sheet(title, filename, child,
  pins=[(name, direction), ...])``; every child file is rebuilt the same
  way and imported by the entry module as a sibling spec.

Documented fidelity losses: hand-written comments/docstrings, the original
statement order (only net order is preserved, via label order), the
original spec-level sheet-pin terms (``power_in``/``power_out`` are stored
as KiCad directions ``input``/``output``), and the original block names.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from kicadgen.core import sexpr
from kicadgen.core.sexpr import Q
from kicadgen.drawing.layout import pin_projected
from kicadgen.symbols.resolve import SymbolResolver, _to_def

__all__ = ["parse_schematic", "build_spec", "render_module", "rebuild_project"]

LINE_LIMIT = 88
AUTO_NET = re.compile(r"^N\$\d+$")


# ----------------------------------------------------------------------
# Parsing helpers (direct child nodes only; KiCad nests features flat)
# ----------------------------------------------------------------------


def _child(node, tag: str):
    for sub in node:
        if isinstance(sub, list) and sub and str(sub[0]) == tag:
            return sub
    return None


def _value(node, tag: str) -> str | None:
    """Directly attached ``(<tag> <atom>)`` value, e.g. ``(unit 2)``."""
    sub = _child(node, tag)
    if sub is not None and len(sub) > 1 and isinstance(sub[1], (str, Q)):
        return str(sub[1])
    return None


def _property(node, name: str) -> str | None:
    for sub in node:
        if (isinstance(sub, list) and sub and str(sub[0]) == "property"
                and len(sub) > 2 and str(sub[1]) == name):
            return str(sub[2])
    return None


def _at(node, tag: str) -> tuple[float, float, int]:
    """Parse a subordinate ``(at x y [rot])`` node."""
    sub = _child(node, tag)
    x, y = float(sub[1]), float(sub[2])
    rot = int(float(sub[3])) if len(sub) > 3 else 0
    return x, y, rot


def _key(p: tuple[float, float]) -> tuple[float, float]:
    """Endpoint key at the 2-decimal precision the writer emits."""
    return round(p[0], 2), round(p[1], 2)


def _line_pts(node) -> list[tuple[float, float]]:
    pts = _child(node, "pts")
    out = []
    for xy in pts or ():
        if isinstance(xy, list) and xy and str(xy[0]) == "xy":
            out.append((float(xy[1]), float(xy[2])))
    return out


def _text_style(node) -> tuple[float, bool, str | None]:
    """(font size, bold?, justify string) of a text node."""
    effects = _child(node, "effects") or []
    font = _child(effects, "font") or []
    size = _child(font, "size")
    bold = _child(font, "bold") is not None
    justify = ""
    jt = _child(effects, "justify")
    if jt is not None:
        justify = " ".join(str(a) for a in jt[1:] if isinstance(a, str))
    size_value = float(size[1]) if size is not None else 1.27
    return size_value, bold, (justify or None)


# ----------------------------------------------------------------------
# Parsed file model
# ----------------------------------------------------------------------


@dataclass
class PlacedComponent:
    ref: str
    lib_id: str
    x: float
    y: float
    rot: int
    unit: int
    value: str
    footprint: str


@dataclass
class SheetBoxParsed:
    title: str
    filename: str
    x: float
    y: float
    w: float
    h: float
    pins: list[tuple[str, str]]                       # name, direction
    pin_positions: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Parsed:
    """Everything the reconstruction needs from one .kicad_sch file."""
    path: Path
    name: str = "design"
    revision: str = ""
    paper: str | None = None
    width: float | None = None
    height: float | None = None
    defs: dict[str, list] = field(default_factory=dict)   # lib_id -> subtree
    components: list[PlacedComponent] = field(default_factory=list)
    wires: list[list[tuple[float, float]]] = field(default_factory=list)
    # label text, position, source tag ("label" | "hierarchical_label")
    labels: list[tuple[str, tuple[float, float], str]] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)      # polylines
    texts: list[dict] = field(default_factory=list)      # graphic texts
    sheets: list[SheetBoxParsed] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)     # dashed rectangles
    frame_line_ids: set[int] = field(default_factory=set)
    heading_text_ids: set[int] = field(default_factory=set)


def parse_schematic(path: Path) -> Parsed:
    """Read one native .kicad_sch into the reconstruction model."""
    parsed = Parsed(path=path)
    tree = sexpr.parse(path.read_text(encoding="utf-8"))

    block = _child(tree, "title_block")
    if block is not None:
        title = _value(block, "title")
        if title:
            parsed.name = title
        parsed.revision = _value(block, "rev") or ""
    paper = _child(tree, "paper")
    if paper is not None and len(paper) > 1:
        if str(paper[1]) == "User":
            parsed.width = float(paper[2])
            parsed.height = float(paper[3])
        else:
            parsed.paper = str(paper[1])

    lib = _child(tree, "lib_symbols")
    if lib is not None:
        for sub in lib:
            if isinstance(sub, list) and sub and str(sub[0]) == "symbol":
                lib_id = str(sub[1])
                parsed.defs[lib_id] = sub

    for sub in tree:
        if not (isinstance(sub, list) and sub and str(sub[0]) == "symbol"):
            continue
        lib_id = _value(sub, "lib_id")
        if lib_id is None:            # a lib_symbols body, not a placement
            continue
        x, y, rot = _at(sub, "at")
        parsed.components.append(PlacedComponent(
            ref=_property(sub, "Reference") or "?",
            lib_id=lib_id, x=x, y=y, rot=rot,
            unit=int(_value(sub, "unit") or 1),
            value=_property(sub, "Value") or "",
            footprint=_property(sub, "Footprint") or "",
        ))

    for sub in tree:
        if not (isinstance(sub, list) and sub):
            continue
        tag = str(sub[0])
        if tag == "wire":
            parsed.wires.append(_line_pts(sub))
        elif tag in ("label", "hierarchical_label"):
            x, y, _rot = _at(sub, "at")
            parsed.labels.append((str(sub[1]), (x, y), tag))

    for sub in tree:
        if not (isinstance(sub, list) and sub):
            continue
        tag = str(sub[0])
        if tag == "polyline":
            stroke = _child(sub, "stroke") or []
            parsed.lines.append({
                "pts": _line_pts(sub),
                "style": _value(stroke, "type") or "default",
                "width": float(_value(stroke, "width") or 0),
            })
        elif tag == "text":
            x, y, _rot = _at(sub, "at")
            size, bold, justify = _text_style(sub)
            parsed.texts.append({"content": str(sub[1]), "x": x, "y": y,
                                 "size": size, "bold": bold,
                                 "justify": justify})

    for sub in tree:
        if not (isinstance(sub, list) and sub and str(sub[0]) == "sheet"):
            continue
        x, y, _rot = _at(sub, "at")
        size = _child(sub, "size")
        pins: list[tuple[str, str]] = []
        positions: list[tuple[float, float]] = []
        for p in sub:
            if (isinstance(p, list) and p and str(p[0]) == "pin"
                    and len(p) > 2):
                pins.append((str(p[1]), str(p[2])))          # name, direction
                positions.append(_at(p, "at")[:2])
        parsed.sheets.append(SheetBoxParsed(
            title=_property(sub, "Sheetname") or "",
            filename=_property(sub, "Sheetfile") or "",
            x=x, y=y, w=float(size[1]), h=float(size[2]),
            pins=pins, pin_positions=[_key(p) for p in positions],
        ))

    _rebuild_frames(parsed)
    return parsed


def _rebuild_frames(parsed: Parsed) -> None:
    """Reassemble dashed block frames; identify their heading texts.

    The engine draws every frame edge as its own 2-point dashed polyline;
    edges of one frame share endpoints, so a point union-find rebuilds the
    rectangles. The heading text is stamped at exactly
    ``(left + 2.54, top + 3.81)`` with ``justify left``.
    """
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    edges: list[tuple[int, tuple, tuple]] = []
    for i, line_ in enumerate(parsed.lines):
        if line_["style"] != "dash" or len(line_["pts"]) != 2:
            continue
        a, b = _key(line_["pts"][0]), _key(line_["pts"][1])
        edges.append((i, a, b))
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    groups: dict[tuple, list[tuple[int, tuple, tuple]]] = {}
    for edge in edges:
        groups.setdefault(find(edge[1]), []).append(edge)

    for group in groups.values():
        xs = {c for _i, a, b in group for c in (a[0], b[0])}
        ys = {c for _i, a, b in group for c in (a[1], b[1])}
        if len(xs) != 2 or len(ys) != 2:
            continue                          # not a dashed rectangle
        left, right = sorted(xs)
        top, bottom = sorted(ys)
        frame = {"left": left, "right": right, "top": top, "bottom": bottom,
                 "heading": None}
        parsed.frames.append(frame)
        for i, _a, _b in group:
            parsed.frame_line_ids.add(i)
        for j, text in enumerate(parsed.texts):
            if (abs(text["x"] - (left + 2.54)) < 0.5
                    and abs(text["y"] - (top + 3.81)) < 0.5
                    and text["justify"] in (None, "left")):
                if frame["heading"] is None:
                    frame["heading"] = str(text["content"])
                    parsed.heading_text_ids.add(j)
    parsed.frames.sort(key=lambda f: (f["top"], f["left"]))


# ----------------------------------------------------------------------
# Reconstruction: Parsed -> BuiltSpec
# ----------------------------------------------------------------------


@dataclass
class CustomDecl:
    nickname: str
    name: str
    prefix: str
    value: str | None            # None -> keep the custom_symbol default
    pins: list[dict] = field(default_factory=list)  # number/name/etype/side


@dataclass
class ComponentDecl:
    ref: str
    lib_id: str
    symbol_arg: str              # custom-symbol variable or quoted lib_id
    value: str | None
    footprint: str | None
    block: str | None
    unit: int
    rot: int


@dataclass
class NetDecl:
    name: str | None             # None -> forward pass auto-names it
    pins: list[tuple[str, str]]  # (ref, pin number), recovered order


@dataclass
class GraphicDecl:
    kind: str                    # "line" | "text"
    args: list[str]              # pre-rendered call arguments


@dataclass
class SheetDecl:
    title: str
    filename: str
    pins: list[tuple[str, str]]
    child_var: str


@dataclass
class BuiltSpec:
    """A reconstructed spec, ready for :func:`render_module`."""
    name: str
    paper: str | None
    width: float | None
    height: float | None
    revision: str
    blocks: dict[str, str] = field(default_factory=dict)    # blockN -> heading
    customs: list[CustomDecl] = field(default_factory=list)
    components: list[ComponentDecl] = field(default_factory=list)
    nets: list[NetDecl] = field(default_factory=list)
    graphics: list[GraphicDecl] = field(default_factory=list)
    sheets: list[SheetDecl] = field(default_factory=list)
    child_imports: list[tuple[str, str]] = field(default_factory=list)
    pin_keys: dict[tuple[str, str], str] = field(default_factory=dict)


class _Reconstructor:
    """Per-file reconstruction state: symbol class, pin lookup, nets."""

    def __init__(self, parsed: Parsed):
        self.parsed = parsed
        self.resolver = SymbolResolver()
        self.custom_ids: set[str] = set()
        for comp in parsed.components:
            if comp.lib_id in self.custom_ids:
                continue
            try:
                self.resolver.resolve(comp.lib_id)
            except (FileNotFoundError, KeyError, ValueError):
                self.custom_ids.add(comp.lib_id)
        self.positions: dict[tuple[float, float], list[tuple[str, str]]] = {}
        for comp in parsed.components:
            definition = self.definition(comp.lib_id)
            for number, pin_obj in definition.pins.items():
                x, y = pin_projected(definition, pin_obj,
                                     comp.x, comp.y, comp.rot)
                # Co-located pads (an ESP32 module carries four GND pads at
                # one library position) stack: every label at that point
                # consumes one candidate, in definition order.
                self.positions.setdefault(_key((x, y)), []).append(
                    (comp.ref, number))
        # Pin names that repeat within one symbol (three "GND" pins on an
        # ESP32 module): such names must be addressed by their number,
        # because ``Component.pin(name)`` resolves to the first match only.
        self.ambiguous: dict[str, set[str]] = {}
        for comp in parsed.components:
            definition = self.definition(comp.lib_id)
            names: dict[str, int] = {}
            for pin_obj in definition.pins.values():
                if pin_obj.name and pin_obj.name != "~":
                    names[pin_obj.name] = names.get(pin_obj.name, 0) + 1
            repeats = {name for name, count in names.items() if count > 1}
            if repeats:
                self.ambiguous.setdefault(comp.ref, set()).update(repeats)

    def definition(self, lib_id: str):
        """Embedded lib definition as a SymbolDef (forward pin data)."""
        subtree = self.parsed.defs.get(lib_id)
        if subtree is not None:
            return _to_def(lib_id, subtree)
        return self.resolver.resolve(lib_id)

    def custom_decl(self, lib_id: str) -> CustomDecl:
        nickname, name = lib_id.split(":", 1)
        subtree = self.parsed.defs[lib_id]
        prefix, decl_value = "U", name
        for sub in subtree:
            if (isinstance(sub, list) and sub and str(sub[0]) == "property"
                    and len(sub) > 2):
                if str(sub[1]) == "Reference":
                    prefix = str(sub[2])
                elif str(sub[1]) == "Value":
                    decl_value = str(sub[2])
        definition = _to_def(lib_id, subtree)
        pins = []
        for pin_obj in definition.pins.values():
            side = "left" if pin_obj.angle == 0 or pin_obj.x < 0 else "right"
            pins.append({"number": pin_obj.number,
                         "name": pin_obj.name or pin_obj.number,
                         "etype": pin_obj.etype, "side": side})
        return CustomDecl(
            nickname=nickname, name=name, prefix=prefix,
            value=decl_value if decl_value != name else None,
            pins=pins,
        )

    def pin_call_key(self, ref: str, number: str) -> str:
        """Identifier for ``.pin(...)``: the pin name when unambiguous
        (how specs usually address pins), else the pin number — repeated
        names (``GND`` on three ESP32 pads) must be addressed by number."""
        comp = next(c for c in self.parsed.components if c.ref == ref)
        pin_obj = self.definition(comp.lib_id).pins.get(number)
        if (pin_obj is not None and pin_obj.name and pin_obj.name != "~"
                and pin_obj.name not in self.ambiguous.get(ref, set())):
            return pin_obj.name
        return number

    def nets(self) -> list[NetDecl]:
        """Cluster labels into nets, in first-appearance (file) order.

        Wires are stubs the layout engine wrote as ``[pin end, label end]``
        (order preserved by the writer). Labels sit at the stub ends, so
        each label resolves through its own wire: the label end points at
        exactly one ``wire[0]`` pin position. Co-located pad geometry (a
        connector's adjacent pins can meet head-on at one coordinate, an
        ESP32 module has four GND pads at one position) stacks both maps:
        the writer emits the wires and the labels in the same pipeline
        order, so a FIFO pop per level re-pairs them exactly.
        """
        stub_pins: dict[tuple[float, float], list[tuple[float, float]]] = {}
        for wire in self.parsed.wires:
            if len(wire) < 2:
                continue
            stub_pins.setdefault(_key(wire[-1]), []).append(_key(wire[0]))

        box_edges: set[tuple[float, float]] = set()
        for sheet in self.parsed.sheets:
            box_edges.update(tuple(map(tuple, sheet.pin_positions)))

        nets: dict[str, tuple[str | None, list[tuple[str, str]]]] = {}
        for text, pos, _tag in self.parsed.labels:
            key = _key(pos)
            pin_key_list = stub_pins.get(key)
            if not pin_key_list or pin_key_list[0] in box_edges:
                continue      # no stub, or a sub-sheet box stub (by name)
            pin_key = pin_key_list.pop(0)
            candidates = self.positions.get(pin_key)
            if not candidates:
                continue
            pin_ref = candidates.pop(0)
            name = None if AUTO_NET.match(text) else text
            bucket = nets.setdefault(text, (name, []))
            if pin_ref not in bucket[1]:
                bucket[1].append(pin_ref)
        return [NetDecl(name=collected[0], pins=collected[1])
                for collected in nets.values() if collected[1]]


def _frame_of(comp: PlacedComponent, parsed: Parsed) -> int | None:
    """1-based frame index whose rectangle contains the placement origin."""
    for i, frame in enumerate(parsed.frames, start=1):
        if (frame["left"] <= round(comp.x, 2) <= frame["right"]
                and frame["top"] <= round(comp.y, 2) <= frame["bottom"]):
            return i
    return None


def build_spec(parsed: Parsed,
               child_imports: list[tuple[str, str]] | None = None) -> BuiltSpec:
    """Reconstruct one module's spec from its parsed schematic.

    ``child_imports`` carries the ``(stem, variable)`` pairs of already
    rebuilt sub-sheet modules, used by the parent's ``add_sheet`` calls.
    """
    recon = _Reconstructor(parsed)
    spec = BuiltSpec(
        name=parsed.name, paper=parsed.paper, width=parsed.width,
        height=parsed.height, revision=parsed.revision,
        child_imports=child_imports or [],
    )

    # Frame rectangles are renumbered block1..blockN by their recovered
    # geometry (x, then y); blocks without a heading keep no entry, so
    # mark_blocks() renders their upper-case name, like the forward pass.
    spec.blocks = {
        f"block{i}": frame["heading"]
        for i, frame in enumerate(parsed.frames, start=1)
        if frame["heading"]
    }

    custom_vars: dict[str, str] = {}
    for comp in parsed.components:
        if comp.lib_id in recon.custom_ids and comp.lib_id not in custom_vars:
            decl = recon.custom_decl(comp.lib_id)
            custom_vars[comp.lib_id] = _var(decl.name)
            spec.customs.append(decl)

    for comp in parsed.components:
        frame = _frame_of(comp, parsed)
        spec.components.append(ComponentDecl(
            ref=comp.ref, lib_id=comp.lib_id,
            symbol_arg=custom_vars.get(comp.lib_id) or f'"{comp.lib_id}"',
            value=comp.value or None,
            footprint=comp.footprint or None,
            block=f"block{frame}" if frame else None,
            unit=comp.unit, rot=comp.rot,
        ))

    spec.nets = recon.nets()
    spec.pin_keys = {
        (ref, number): recon.pin_call_key(ref, number)
        for net in spec.nets for ref, number in net.pins
    }

    # Spec graphics: everything the frame engine did not draw itself.
    for line_ in parsed.lines:
        if line_["style"] == "dash" and len(line_["pts"]) == 2:
            continue
        pts = line_["pts"]
        if len(pts) < 2:
            continue
        for a, b in zip(pts, pts[1:]):
            args = [repr(a[0]), repr(a[1]), repr(b[0]), repr(b[1])]
            if abs(line_["width"] - 0.254) > 1e-9:
                args.append(f"width={line_['width']}")
            if line_["style"] not in ("solid", "default"):
                args.append(f'style="{line_["style"]}"')
            spec.graphics.append(GraphicDecl("line", args))
    for i, text in enumerate(parsed.texts):
        if i in parsed.heading_text_ids:
            continue
        args = [repr(text["x"]), repr(text["y"]), _q(text["content"])]
        if not (1.905 - 1e-9 <= text["size"] <= 1.905 + 1e-9):
            args.append(f"size={text['size']}")
        if not text["bold"]:
            args.append("bold=False")
        if text["justify"] and text["justify"] != "left":
            args.append(f'justify="{text["justify"]}"')
        spec.graphics.append(GraphicDecl("text", args))

    for sheet in parsed.sheets:
        child_var = next(
            (var for stem, var in spec.child_imports
             if stem == Path(sheet.filename).stem), None)
        spec.sheets.append(SheetDecl(
            title=sheet.title, filename=sheet.filename,
            pins=list(sheet.pins),
            child_var=child_var or _var(Path(sheet.filename).stem) + "_sheet",
        ))
    return spec


# ----------------------------------------------------------------------
# Rendering: BuiltSpec -> Python module text
# ----------------------------------------------------------------------


def _var(text: str) -> str:
    name = re.sub(r"\W+", "", text).lower() or "item"
    if name[0].isdigit():
        name = "c_" + name
    return name


def _q(text: str) -> str:
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _call(head: str, args: list[str]) -> str:
    """``head(arg, arg, ...)``, wrapped one-arg-per-line when long.

    ``head`` includes the receiver, e.g. ``design.mark_blocks`` or
    ``battery = Component``.
    """
    joined = f"{head}({', '.join(a for a in args if a)})"
    if len(joined.splitlines()[0]) <= LINE_LIMIT and "\n" not in joined:
        return joined
    lines = [f"{head}("]
    for arg in args:
        if not arg:
            continue
        for item in arg.splitlines():
            lines.append(f"    {item},")
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append(")")
    return "\n".join(lines)


def render_module(spec: BuiltSpec) -> str:
    """Render the reconstructed spec as a Python module."""
    out: list[str] = []
    out.append('"""Reconstructed spec (kicadgen reverse pass).')
    out.append("")
    out.append("Rebuilt from the generated schematic; hand-written comments,")
    out.append("docstrings and the original block names are not recoverable")
    out.append("from the schematic format. Regenerate with:")
    out.append("")
    out.append("    python3 scripts/refresh_py_to_sch.py <slug>")
    out.append('"""')
    out.append("")

    imports = ["Design", "Component"]
    if spec.customs:
        imports += ["custom_symbol", "pin"]
    if spec.nets:
        imports.append("connect")
    out.append(f"from kicadgen import {', '.join(imports)}")
    for stem, var in spec.child_imports:
        out.append("")
        out.append(f"from {stem} import design as {var}")
    out.append("")

    fields = [f"name={_q(spec.name)}"]
    if spec.paper:
        fields.append(f"paper={_q(spec.paper)}")
    if spec.revision:
        fields.append(f"revision={_q(spec.revision)}")
    if spec.width is not None:
        fields.append(f"width={spec.width}")
    if spec.height is not None:
        fields.append(f"height={spec.height}")
    out.append(_call("design = Design", fields))
    out.append("")

    if spec.blocks:
        out.append("# Functional-block frames with recovered headings; the")
        out.append("# block keys themselves are reconstructed names.")
        entries = [f'{_q(key)}: {_q(text)}'
                   for key, text in spec.blocks.items()]
        if max(len(e) for e in entries) + 25 > LINE_LIMIT:
            out.append("design.mark_blocks({")
            for entry in entries:
                out.append(f"    {entry},")
            out.append("})")
        else:
            out.append(f"design.mark_blocks({{{', '.join(entries)}}})")
        out.append("")

    for decl in spec.customs:
        var = _var(decl.name)
        lines = [f"{var} = custom_symbol("]
        lines.append(f'    {_q(decl.name)},')
        lines.append(f'    nickname={_q(decl.nickname)},')
        lines.append(f'    prefix={_q(decl.prefix)},')
        if decl.value:
            lines.append(f'    value={_q(decl.value)},')
        lines.append("    pins=[")
        for p in decl.pins:
            number, name = p["number"], p["name"]
            # ``name`` defaults to the number in the API; only repeat it
            # when it adds information (e.g. pin("1", name="VCC")).
            name_arg = f' name={_q(name)},' if name != number else ""
            lines.append(f'    pin({_q(number)},{name_arg} '
                         f'etype={_q(p["etype"])}, side="{p["side"]}"),')
        lines.append("    ],")
        lines.append(")")
        out.extend(lines)
        out.append("")

    add_list = []
    for comp in spec.components:
        var = _var(comp.ref)
        add_list.append(var)
        fields = [f"ref={_q(comp.ref)}", f"symbol={comp.symbol_arg}"]
        if comp.value:
            fields.append(f"value={_q(comp.value)}")
        if comp.footprint:
            fields.append(f"footprint={_q(comp.footprint)}")
        if comp.block:
            fields.append(f"block={_q(comp.block)}")
        if comp.unit != 1:
            fields.append(f"unit={comp.unit}")
        if comp.rot:
            fields.append(f"rot={comp.rot}")
        out.append(_call(f"{var} = Component", fields))
        out.append("")
    out.append(_call("design.add", add_list))
    out.append("")

    out.append("# Nets: one connect() per label-clustered net, in net order;")
    out.append("# nameless calls re-create the auto-named (N$<n>) nets.")
    for net in spec.nets:
        args = [f"{_var(ref)}.pin({_q(spec.pin_keys[(ref, key)])})"
                for ref, key in net.pins]
        if net.name is not None:
            args.append(f"name={_q(net.name)}")
        out.append(_call("connect", args))
    if spec.nets:
        out.append("")

    out.append("# Spec graphics; block-frame decorations are regenerated")
    out.append("# by design.mark_blocks, not repeated here.")
    for graphic in spec.graphics:
        out.append(_call(f"design.{graphic.kind}", graphic.args))
    out.append("")

    for sheet in spec.sheets:
        lines = ["design.add_sheet("]
        lines.append(f'    {_q(sheet.title)}, {_q(sheet.filename)},')
        lines.append(f'    {sheet.child_var},')
        lines.append("    pins=[")
        for name, direction in sheet.pins:
            lines.append(f'        ({_q(name)}, {_q(direction)}),')
        lines.append("    ],")
        lines.append(")")
        out.extend(lines)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# ----------------------------------------------------------------------
# Project-level rebuild: root schematic + sub-sheet files
# ----------------------------------------------------------------------

BACKUP_KEEP = 3


def store_backup(target: Path, backup_dir: Path) -> Path | None:
    """Copy an existing file into ``backup_dir`` with a timestamp suffix.

    Returns the backup path, or ``None`` when there is nothing to back up
    (the target does not exist yet). Timestamps in the name keep repeated
    backups from overwriting each other.
    """
    target, backup_dir = Path(target), Path(backup_dir)
    if not target.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{target.stem}-{stamp}{target.suffix}.bak"
    # Fast repeats land in the same second: number the colliding backups
    # instead of overwriting the previous one.
    run = 1
    while backup.exists():
        backup = backup_dir / f"{target.stem}-{stamp}-{run}{target.suffix}.bak"
        run += 1
    shutil.copy2(target, backup)
    return backup


def prune_backups(backup_dir: Path, keep: int = BACKUP_KEEP) -> list[Path]:
    """Keep only the newest ``keep`` ``*.bak`` files in ``backup_dir``.

    Returns the removed paths. Oldest by modification time go first.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    backups = sorted(backup_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime,
                     reverse=True)
    removed = []
    for stale in backups[keep:]:
        stale.unlink()
        removed.append(stale)
    return removed


def rebuild_project(root: Path) -> list[tuple[str, str]]:
    """Rebuild every spec module of a generated schematic hierarchy.

    ``root`` is the generated root schematic
    (``output/<slug>/<slug>.kicad_sch``). Returns ``(module stem, source
    code)`` pairs, children first: the entry module imports the sub-sheet
    modules built alongside it.
    """
    modules: list[tuple[str, str]] = []
    child_imports: list[tuple[str, str]] = []

    parsed = parse_schematic(root)
    for sheet in parsed.sheets:
        if not sheet.filename:
            continue
        child_path = root.parent / sheet.filename
        if not child_path.is_file():
            raise FileNotFoundError(
                f"sub-sheet file {sheet.filename!r} not found next to "
                f"{root.name}; regenerate with refresh_py_to_sch.py first"
            )
        stem = Path(sheet.filename).stem
        child = parse_schematic(child_path)
        var = _var(stem) + "_sheet"
        child_imports.append((stem, var))
        modules.append((stem, render_module(build_spec(child, []))))

    entry = build_spec(parsed, child_imports)
    modules.append((root.stem, render_module(entry)))
    return modules
