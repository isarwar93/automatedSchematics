"""Internal circuit model.

This model deliberately separates *electrical connectivity* from *drawing*.
A :class:`Net` says which pins are connected; it says nothing about where
those pins appear on the page. Layout is decided later by ``layout.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Component:
    ref: str
    symbol: str  # library id, e.g. "Device:R"
    value: str = ""
    footprint: str = ""
    # Optional functional block used by the layout engine:
    # "power", "controller", "interface", "connector", ...
    block: str = ""
    unit: int = 1  # which unit of a multi-unit symbol
    rot: int = 0  # 0 / 90 / 180 / 270 (counter-clockwise)

    def prefix(self) -> str:
        return "".join(ch for ch in self.ref if ch.isalpha())


@dataclass
class Connection:
    component: str  # component ref, e.g. "R1"
    pin: str  # pin number as string, e.g. "1"


@dataclass
class Net:
    name: str
    connections: list[Connection] = field(default_factory=list)


@dataclass
class GraphicLine:
    """A plain graphical line drawn onto the sheet (no electrical role)."""
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.254
    style: str = "solid"  # KiCad stroke type: solid, dash, long_dash, ...


@dataclass
class GraphicText:
    """A plain graphical text drawn onto the sheet (no electrical role)."""
    x: float
    y: float
    content: str
    size: float = 1.905
    bold: bool = True
    justify: str | None = None  # e.g. "left"; None emits no justify node


@dataclass
class Design:
    name: str = "design"
    paper: str = "A4"
    orientation: str = "landscape"
    # Custom sheet size in mm. When both are set the writer emits KiCad's
    # custom paper node ``(paper "User" <width> <height>)`` instead of the
    # named size in ``paper``.
    width: float | None = None
    height: float | None = None
    revision: str = ""

    def is_custom_size(self) -> bool:
        return self.width is not None and self.height is not None


@dataclass
class Circuit:
    design: Design = field(default_factory=Design)
    components: list[Component] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    # Graphic annotations positioned by the spec, drawn as-is.
    graphic_lines: list[GraphicLine] = field(default_factory=list)
    graphic_texts: list[GraphicText] = field(default_factory=list)
    # When True the layout engine draws a separator line between adjacent
    # functional-block columns and a heading above each column; block
    # headingsoverride the default per-block heading text.
    block_frames: bool = False
    block_headings: dict[str, str] = field(default_factory=dict)

    def component(self, ref: str) -> Component | None:
        return next((c for c in self.components if c.ref == ref), None)

    def pins_connected(self, ref: str) -> set[str]:
        """Pin numbers of ``ref`` that appear in at least one net."""
        return {
            c.pin
            for net in self.nets
            for c in net.connections
            if c.component == ref
        }


@dataclass
class SheetSpec:
    """One hierarchical sub-sheet of a project (one generated ``.kicad_sch``).

    ``pins`` are ``(name, direction)`` tuples. The name must be a net name in
    both the parent circuit and the sub-sheet circuit: KiCad joins parents
    and child sheets through the same-named sheet pin and hierarchical label.
    ``direction`` is a KiCad sheet-pin direction (``input``, ``output``,
    ``bidirectional``, ``power_in``, ``power_out``, ``passive``).
    """

    title: str
    filename: str  # child file name, relative to the parent file
    circuit: "Circuit"
    pins: list[tuple[str, str]] = field(default_factory=list)
    child_design: object | None = None  # api.Design, for custom-def lookup


def parse_paper(paper) -> tuple[str, str, float | None, float | None]:
    """Parse the ``design.paper`` value.

    Accepted forms (the custom width/height win over the named size):

    - ``paper: A4``                                 -> named size, landscape
    - ``paper: {size: A3, orientation: portrait}``  -> named size
    - ``paper: {width: 200, height: 200}``          -> custom user size (mm)
    - ``paper: {size: custom, width: 297, height: 210, orientation: landscape}``

    Returns ``(name, orientation, width, height)``; ``width``/``height`` are
    ``None`` unless a custom size was requested.
    """
    if isinstance(paper, dict):
        orientation = str(paper.get("orientation") or "landscape")
        width, height = paper.get("width"), paper.get("height")
        if width is not None or height is not None:
            try:
                w = float(width) if width is not None else None
                h = float(height) if height is not None else None
            except (TypeError, ValueError):
                raise ValueError(
                    "design.paper: 'width' and 'height' must be numbers in mm"
                )
            return "User", orientation, w, h
        return str(paper.get("size") or "A4"), orientation, None, None
    return str(paper or "A4"), "landscape", None, None


def circuit_from_dict(data: dict) -> Circuit:
    """Build a :class:`Circuit` from parsed YAML/JSON data."""
    design_data = data.get("design") or {}
    paper_name, orientation, width, height = parse_paper(
        design_data.get("paper", "A4")
    )
    design = Design(
        name=str(design_data.get("name", "design")),
        paper=paper_name,
        orientation=orientation,
        width=width,
        height=height,
        revision=str(design_data.get("revision", "") or ""),
    )

    circuit = Circuit(design=design)
    for comp in data.get("components", []):
        circuit.components.append(
            Component(
                ref=str(comp["ref"]),
                symbol=str(comp["symbol"]),
                value=str(comp.get("value", "")),
                footprint=str(comp.get("footprint", "") or ""),
                block=str(comp.get("block", "") or ""),
                unit=int(comp.get("unit", 1) or 1),
                rot=int(comp.get("rot", 0) or 0),
            )
        )
    for net in data.get("nets", []):
        conns = [
            Connection(component=str(c["component"]), pin=str(c["pin"]))
            for c in net.get("connections", [])
        ]
        circuit.nets.append(Net(name=str(net["name"]), connections=conns))
    return circuit
