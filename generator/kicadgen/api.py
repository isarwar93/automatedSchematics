"""Python-native circuit specification API — an alternative to YAML/JSON specs.

Circuits are described as plain Python, with pins exposed as attributes and
net connections expressed as direct ``connect(...)`` calls::

    from kicadgen import Design, Component, connect

    design = Design(name="liquid_level", paper="A4")
    mcu = Component(ref="U1", symbol="RF_Module:RFM95W_915MHZ")
    rpt = Component(ref="R1", symbol="Device:R")
    design.add(mcu, rpt)
    connect(mcu.DIO0, rpt.pin(2), name="ALARM")
    design.generate("output/liquid_level.kicad_sch", export=True)

Standard KiCad symbols are referenced by ``"Library:Name"``; symbols that do
not exist as a library file (project boards, breakout modules) are declared
with :func:`custom_symbol` and referenced directly.

Conventions:

- every :class:`Design` constructed becomes the *active* design, and
  :func:`connect` attaches to it (one active design per script covers the
  normal use of building a circuit top-level, statement by statement);
- naming a net (``connect(..., name="GND")``) makes the name appear as the
  schematic label and netlist net name; without a name each connect forms a
  distinct auto-named net (``N$1``, ``N$2``, ...);
- references follow KiCad conventions: the prefix drives the layout's
  functional-block column order ("U", "J", "R", "BT", ...), and an optional
  ``block=`` groups components explicitly.
"""

from __future__ import annotations

import os
from typing import Iterable

from .cli import generate_circuit
from kicadgen.symbols.custom import build as build_custom
from kicadgen.core.model import Circuit, Connection, Component as ModelComponent
from kicadgen.core.model import Design as ModelDesign
from kicadgen.core.model import (
    GraphicLine,
    GraphicText,
    Net,
    SheetSpec,
    parse_paper,
)
from kicadgen.symbols.resolve import Pin, SymbolDef, SymbolResolver

__all__ = [
    "Design",
    "Component",
    "PinHandle",
    "connect",
    "custom_symbol",
    "pin",
]


# ----------------------------------------------------------------------
# Symbol declarations
# ----------------------------------------------------------------------


def pin(number, name: str | None = None, etype: str = "passive",
        side: str = "right") -> dict:
    """One pin of a :func:`custom_symbol` declaration.

    ``number`` is the pin identifier (signal names are fine, e.g. "GPIO13"),
    ``name`` overrides the displayed pin name, ``etype`` is a KiCad
    electrical type (``power_in``, ``output``, ``bidirectional``, ...) and
    ``side`` is ``left`` or ``right``.
    """
    return {
        "number": str(number),
        "name": str(name) if name is not None else str(number),
        "electrical_type": etype,
        "side": side,
    }


class CustomSymbol:
    """A project symbol declared in Python instead of a ``.kicad_sym`` file.

    Built by :func:`custom_symbol`; pass an instance where a
    ``"Library:Name"`` string would go (``Component(symbol=LOLIN32)``).
    """

    def __init__(self, name: str, pins: Iterable[dict], prefix: str = "U",
                 value: str | None = None, nickname: str = "Custom",
                 footprint: str | None = None):
        self.name = name
        self.nickname = nickname
        self.lib_id = f"{nickname}:{name}"
        self.spec = {
            "name": name,
            "reference_prefix": prefix,
            "value": value or name,
            "footprint": footprint,
            "pins": list(pins),
        }

    @property
    def value(self) -> str:
        return str(self.spec["value"])

    def definition(self) -> SymbolDef:
        """Pre-build the normalized definition for the pipeline."""
        return build_custom(self.spec, self.nickname)


def custom_symbol(name: str, pins: Iterable[dict], prefix: str = "U",
                  value: str | None = None, nickname: str = "Custom",
                  footprint: str | None = None) -> CustomSymbol:
    """Declare a custom symbol in Python (board symbols, breakout modules)."""
    return CustomSymbol(name, pins, prefix=prefix, value=value,
                        nickname=nickname, footprint=footprint)


# ----------------------------------------------------------------------
# Components and pin handles
# ----------------------------------------------------------------------

_resolver: SymbolResolver | None = None


def _default_resolver() -> SymbolResolver:
    """Process-wide resolver for standard-library pin lookups."""
    global _resolver
    if _resolver is None:
        _resolver = SymbolResolver()
    return _resolver


class PinHandle:
    """A specific pin of a component, produced by :meth:`Component.pin`."""

    def __init__(self, component: "Component", number: str, name: str):
        self.component = component
        self.number = number
        self.name = name

    def __repr__(self) -> str:
        return f"<pin {self.component.ref}.{self.name or self.number}>"


def _gpio_aliases(key: str) -> tuple[str, ...]:
    """``GPIO<n>``/``IO<n>`` spellings (ESP32 pin names use ``IO<n>``)."""
    if key.startswith("GPIO"):
        return ("IO" + key[4:],)
    if key.startswith("IO"):
        return ("GPIO" + key[2:],)
    return ()


class Component:
    """One placed component of a design.

    ``symbol`` is a ``"Library:Name"`` id or a :class:`CustomSymbol` built
    with :func:`custom_symbol`. Pins can be accessed by name as attributes
    (``mcu.GPIO13``, ``can.TXD``) or with :meth:`pin` (``j1.pin(1)``,
    ``mcu.pin("3V3")`` for names that are not valid attribute names).
    """

    def __init__(self, ref: str, symbol, value: str = "",
                 footprint: str = "", block: str = "", unit: int = 1,
                 rot: int = 0):
        if isinstance(symbol, CustomSymbol):
            self.custom: CustomSymbol | None = symbol
            lib_id = symbol.lib_id
            self.value = value or symbol.value
        else:
            self.custom = None
            if ":" not in str(symbol):
                raise ValueError(
                    f"{ref}: symbol must be 'Library:Name' or a custom_symbol,"
                    f" got '{symbol}'"
                )
            lib_id = str(symbol)
            self.value = value
        self.ref = str(ref)
        self.lib_id = lib_id
        self.footprint = footprint
        self.block = block
        self.unit = unit
        self.rot = rot
        self._pin_index: dict[str, str] | None = None

    # -- pin access ----------------------------------------------------

    def pin(self, key) -> PinHandle:
        """Return a :class:`PinHandle` for a pin name or pin number.

        Matching is exact, then case-insensitive; ESP32-style ``GPIO<n>``
        and KiCad's ``IO<n>`` spellings are treated as aliases.
        """
        number = self._lookup(str(key))
        if number is None:
            raise ValueError(
                f"{self.ref} ({self.lib_id}): no pin '{key}'; pin numbers: "
                f"{sorted(set(self._pins().values()))}"
            )
        return PinHandle(self, number, str(key))

    def _lookup(self, key: str) -> str | None:
        index = self._pins()
        for candidate in (key, key.upper(), *_gpio_aliases(key)):
            number = index.get(candidate)
            if number is not None:
                return number
        return None

    def _pins(self) -> dict[str, str]:
        """Map name -> pin number (and number -> number) for this symbol."""
        if self._pin_index is None:
            index: dict[str, str] = {}
            if isinstance(self.custom, CustomSymbol):
                pins = [
                    Pin(number=str(p["number"]), name=str(p.get("name") or ""),
                        x=0.0, y=0.0, angle=0, etype=str(p.get("electrical_type") or "passive"))
                    for p in self.custom.spec["pins"]
                ]
            else:
                pins = list(_default_resolver().resolve(self.lib_id).pins.values())
            for p in pins:
                index.setdefault(p.number, p.number)
                if p.name and p.name != "~":
                    index.setdefault(p.name, p.number)
                    index.setdefault(p.name.upper(), p.number)
            self._pin_index = index
        return self._pin_index

    def __getattr__(self, attr: str) -> PinHandle:
        # Only reached for attributes the class does not already define:
        # treat plausible pin identifiers as pin lookups.
        try:
            return self.pin(attr)
        except ValueError as exc:
            raise AttributeError(
                f"{self.ref}: no attribute or pin '{attr}'; pins are listed "
                f"in the message of connect()/pin() errors"
            ) from exc
        except FileNotFoundError as exc:  # symbol library missing
            raise AttributeError(f"{self.ref}: {exc}") from exc

    # -- model casting -------------------------------------------------

    def to_model(self) -> ModelComponent:
        return ModelComponent(
            ref=self.ref,
            symbol=self.lib_id,
            value=self.value,
            footprint=self.footprint,
            block=self.block,
            unit=self.unit,
            rot=self.rot,
        )


# ----------------------------------------------------------------------
# Connections
# ----------------------------------------------------------------------

_active_design: "Design | None" = None


def connect(*pins: PinHandle, name: str | None = None) -> None:
    """Join two or more pins into one net on the active :class:`Design`.

    ``name`` becomes the net's label in the schematic and the netlist.
    Without a name the net gets an auto-generated name, still shared by all
    its pins.

    A single pin creates a one-pin labeled stub net that dangles by design
    (a wire going off-board or reserved for future use); validation reports
    it as a warning, not an error.
    """
    if _active_design is None:
        raise RuntimeError(
            "connect() before any Design: construct a Design first"
        )
    _active_design.connect(*pins, name=name)


class Design:
    """A circuit design: components plus nets built via :func:`connect`."""

    def __init__(self, name: str = "design", paper: str = "A4",
                 orientation: str = "landscape", revision: str = "",
                 width: float | None = None, height: float | None = None):
        self.name = name
        self.paper = paper
        self.orientation = orientation
        self.revision = revision
        self.width = width
        self.height = height
        self.components: list[Component] = []
        # Each entry: (net name or None, [PinHandle, ...])
        self.connections: list[tuple[str | None, list[PinHandle]]] = []
        self.sheets: list[SheetSpec] = []
        # Graphic annotations, positioned at absolute sheet coordinates.
        self.graphic_lines: list[GraphicLine] = []
        self.graphic_texts: list[GraphicText] = []
        # Functional-block decoration: separator lines between block
        # columns plus a heading above each column (drawn by the layout
        # engine, so they track the computed geometry).
        self.block_frames = False
        self.block_headings: dict[str, str] = {}
        global _active_design
        _active_design = self

    def add(self, *components: Component) -> "Design":
        for comp in components:
            if not isinstance(comp, Component):
                raise TypeError(f"add(): expected Component, got {comp!r}")
            self.components.append(comp)
        return self

    # -- sheet annotations ---------------------------------------------

    def line(self, x1: float, y1: float, x2: float, y2: float,
             width: float = 0.254, style: str = "solid") -> "Design":
        """Draw a plain graphical line (no electrical role) on the sheet.

        ``style`` is a KiCad stroke type ("solid", "dash", "long_dash",
        "dot", "dash_dot", ...).
        """
        self.graphic_lines.append(GraphicLine(
            float(x1), float(y1), float(x2), float(y2),
            float(width), str(style)))
        return self

    def text(self, x: float, y: float, content: str, size: float = 1.905,
             bold: bool = True, justify: str | None = None) -> "Design":
        """Draw a plain graphical text on the sheet.

        Anchor: ``x`` is the left edge of the text, ``y`` its vertical
        centre. The default style (bold 1.905 mm) suits block headings.
        ``justify`` optionally sets a KiCad justification token
        ("left", "right", "top", "bottom", or space-separated combos).
        """
        self.graphic_texts.append(GraphicText(
            float(x), float(y), str(content), float(size), bool(bold),
            justify))
        return self

    def mark_blocks(self, headings: dict[str, str] | None = None) -> "Design":
        """Draw a dashed frame around every functional-block column.

        In the style of the LOLIN D32 reference sheet: one dashed
        rectangle per block column (horizontal edges plus vertical
        separator edges towards the neighbouring columns) with the block
        heading inside its top-left corner. ``headings`` maps a block
        name to its heading text ("power" -> "Power"); blocks absent from
        the mapping get their block name in upper case.
        """
        if headings is not None:
            if not isinstance(headings, dict):
                raise TypeError("mark_blocks(): headings must be a dict "
                                "of block name -> heading text")
            self.block_headings = dict(headings)
        self.block_frames = True
        return self

    def component(self, ref: str) -> Component:
        found = next((c for c in self.components if c.ref == ref), None)
        if found is None:
            raise KeyError(f"no component '{ref}' in design '{self.name}'")
        return found

    def connect(self, *pins: PinHandle, name: str | None = None) -> None:
        if len(pins) < 1:
            raise ValueError(
                f"connect(): at least one pin required, got {len(pins)}"
            )
        for p in pins:
            if not isinstance(p, PinHandle):
                raise TypeError(f"connect(): expected PinHandle, got {p!r}")
        self.connections.append((name, list(pins)))

    def add_sheet(self, title: str, filename: str, child: "Design",
                  pins: Iterable[tuple[str, str]] = ()) -> "Design":
        """Add a hierarchical sub-sheet (a ``.kicad_sch`` opened on click).

        ``child`` must be a separate :class:`Design` built *after* this
        parent (creating it switches the active design). Every pin name must
        be a net of the child; on the parent side the same-named local label
        joins the sheet pin to the parent net, so name the pins exactly like
        the parent nets you want to expose.

        ``pins`` are ``(name, direction)`` with a KiCad sheet-pin direction
        (``input``, ``output``, ``bidirectional``, ``power_in``,
        ``power_out``, ``passive``).
        """
        pins = list(pins)
        if not pins:
            raise ValueError("add_sheet(): at least one sheet pin required")
        if child is self:
            raise ValueError("add_sheet(): child design must differ")
        child_circuit = child.to_circuit()
        child_nets = {net.name for net in child_circuit.nets}
        # Sheet pins join parent and child by name: KiCad matches the sheet
        # pin label to the hierarchical labels of the sub-sheet.
        for name, _direction in pins:
            if name not in child_nets:
                raise ValueError(
                    f"add_sheet(): sheet pin '{name}' is not a net of the"
                    f" sub-sheet '{child.name}'"
                )
        self.sheets.append(SheetSpec(
            title=title, filename=filename, circuit=child_circuit,
            pins=pins, child_design=child,
        ))
        return self

    # -- output --------------------------------------------------------

    def to_circuit(self) -> Circuit:
        """Cast to the pipeline's internal model (same model as YAML specs)."""
        paper = {"size": self.paper, "orientation": self.orientation}
        if self.width is not None or self.height is not None:
            paper["width"] = self.width
            paper["height"] = self.height
        paper_name, orientation, width, height = parse_paper(paper)
        design = ModelDesign(
            name=self.name,
            paper=paper_name,
            orientation=orientation,
            width=width,
            height=height,
            revision=self.revision,
        )
        circuit = Circuit(design=design)
        circuit.components = [c.to_model() for c in self.components]
        circuit.graphic_lines = list(self.graphic_lines)
        circuit.graphic_texts = list(self.graphic_texts)
        circuit.block_frames = self.block_frames
        circuit.block_headings = dict(self.block_headings)

        named: dict[str, Net] = {}
        auto = 0
        for name, handles in self.connections:
            if name is None:
                auto += 1
                name = f"N${auto}"
            if name in named:
                net = named[name]
            else:
                net = Net(name=name)
                named[name] = net
                circuit.nets.append(net)
            present = {(c.component, c.pin) for c in net.connections}
            for handle in handles:
                key = (handle.component.ref, handle.number)
                if key not in present:
                    present.add(key)
                    net.connections.append(Connection(*key))
        return circuit

    def custom_defs(self) -> list[SymbolDef]:
        """Every distinct :class:`CustomSymbol` used by added components."""
        out: list[SymbolDef] = []
        seen: set[int] = set()
        for comp in self.components:
            symbol = comp.custom
            if symbol is None or id(symbol) in seen:
                continue
            seen.add(id(symbol))
            out.append(symbol.definition())
        return out

    def generate(self, output: str = "output/design.kicad_sch",
                 export: bool = False) -> int:
        """Write a ``.kicad_sch`` file; optionally run ERC/netlist/SVG export.

        Returns the generator's exit-code convention: 0 success, 1 invalid
        spec, 2 written but ERC violations found.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
        # Single resolver pool: parent custom defs plus every sub-sheet's,
        # de-duplicated by symbol name so both files resolve the same defs.
        inline = list(self.custom_defs())
        seen = {d.lib_id for d in inline}
        for sheet in self.sheets:
            child_design = sheet.child_design
            if child_design is None:
                continue
            for definition in child_design.custom_defs():
                if definition.lib_id not in seen:
                    seen.add(definition.lib_id)
                    inline.append(definition)
        return generate_circuit(
            self.to_circuit(), output, export=export,
            inline_defs=inline, sheets=self.sheets,
        )
