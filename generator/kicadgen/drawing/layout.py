"""Layout engine: turns the circuit model into coordinates.

Strategy (per BUILD_PLAN 7):
1. Group components into functional blocks (columns, left to right).
2. Place the controller on the left, interfaces in the middle, connectors
   on the right; power circuitry in the leftmost column.
3. Keep every origin on the 1.27 mm grid.
4. Compute each pin's absolute schematic position from the real pin
   coordinates of the resolved symbol (never guessed).
5. Route wires: two-pin nets get a straight or L-shaped routed wire with a
   net label; nets with more pins get a short stub per pin with a local
   label at the stub end (labels connect electrically by name).

KiCad connects by exact coordinate match, so all endpoints are derived from
the same pin-position function the writer uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from kicadgen.core.model import Circuit, GraphicLine, GraphicText
from kicadgen.symbols.resolve import Pin, SymbolDef

GRID = 1.27  # mm
STUB = 2.54  # wire stub length from a pin towards its net label

# Prefix heuristics for functional blocks (left -> right).
BLOCK_ORDER = ["power", "controller", "interface", "connector", "misc"]

BLOCK_BY_PREFIX = {
    "U": "controller",
    "IC": "controller",
    "J": "connector",
    "P": "connector",
    "K": "connector",
    "SW": "connector",
}

POWER_NET_HINTS = ("GND", "AGND", "VSS", "VCC", "VDD", "+3V", "+5V", "+12V", "VBUS")

# Vertical spacing inside a column: dynamic — each successive row sits
# below the previous symbol's lowest pin plus a breathing gap and the next
# symbol's upward pin reach, so small parts (caps) stack tightly while big
# ones still never collide.
ROW_GAP = 15.24
# Vertical distance between horizontal bands when a row of columns wraps.
BAND_STEP = 76.2
X0 = 63.5
Y0 = 63.5
# Gap between the furthest reach of one column and the next column.
COLUMN_GAP = 5.08
# Right edge of the usable drawing area of an A3 landscape sheet (mm).
PAGE_RIGHT_LIMIT = 396.2
# Conservative text advance used to reserve room for net labels; the KiCad
# default font at 1.27 mm measures ~0.8 mm per character.
LABEL_ADVANCE = 1.0
# Minimum distance reserved on each side of a column, even without labels.
MIN_EXTENT = 10.16

# Hierarchical sheet box: kept compact (halved from the earlier 63.5 mm
# width / 7.62 mm pitch).
SHEET_BOX_WIDTH = 38.1
SHEET_PIN_PITCH = 6.35
SHEET_PIN_FIRST = 7.62

# Vertical end of the usable drawing area of an A3 landscape sheet: frames
# and routing must stay above it (the page border sits ~10 mm in).
PAGE_BOTTOM_LIMIT = 279.4


@dataclass
class Placement:
    ref: str
    x: float
    y: float
    rot: int
    unit: int
    pins: dict[str, tuple[float, float]]  # absolute schematic coordinates
    symbol: SymbolDef


@dataclass
class Routing:
    wires: list[list[tuple[float, float]]] = field(default_factory=list)  # polylines
    # label text, position, rotation, justification, shape (or None for a
    # plain local label; a shape turns it into a hierarchical label)
    labels: list[tuple[str, tuple[float, float], int, str, str | None]] = field(default_factory=list)


@dataclass
class SheetBox:
    """A hierarchical sheet drawn on the parent sheet."""

    uuid: str
    title: str
    filename: str
    x: float  # left edge
    y: float  # top edge
    w: float
    h: float
    # name, direction, position, edge side ("left" / "right")
    pins: list[tuple[str, str, tuple[float, float], str]]


@dataclass
class LayoutResult:
    placements: dict[str, Placement]
    routing: Routing
    sheets: list[SheetBox] = field(default_factory=list)
    # GraphicLine / GraphicText decoration computed by the layout engine
    # (block separator frames), emitted by the writer alongside the
    # spec-declared graphics.
    decorations: list = field(default_factory=list)


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def _block_of(prefix: str, explicit: str) -> str:
    if explicit:
        return explicit
    return BLOCK_BY_PREFIX.get(prefix, "misc")


def _stub_direction(pin: Pin, rot: int,
                    rects: Sequence[tuple[float, float, float, float]] | None = None,
                    ) -> tuple[int, int]:
    """Unit vector, in schematic space, pointing away from the symbol body.

    With the symbol's drawn body rectangle(s) available the stub leaves the
    face the pin sits next to: a tall custom symbol keeps all sixteen left
    pins on the left face, even when a pin's vertical offset dominates. The
    "dominant axis" fallback handles symbols without a rectangle body
    (connectors, power symbols): there the pin offset's dominant axis is
    the outward direction because the pin ``(at)`` is the connection point.
    """
    if rects:
        best_dir: tuple[int, int] | None = None
        best_dist: float | None = None
        for x1, y1, x2, y2 in rects:
            face = min(
                ((-1, 0), abs(pin.x - x1)),
                ((1, 0), abs(pin.x - x2)),
                ((0, -1), abs(pin.y - y2)),  # library Y-up: top face stubs up
                ((0, 1), abs(pin.y - y1)),
                key=lambda pair: pair[1],
            )
            if best_dist is None or face[1] < best_dist:
                best_dist = face[1]
                best_dir = face[0]
        return _rotate_vector(*best_dir, rot) if rot else best_dir  # type: ignore[return-value]
    if abs(pin.x) > abs(pin.y):
        dx = 1 if pin.x > 0 else -1
        dy = 0
    elif abs(pin.y) > abs(pin.x):
        dx = 0
        dy = 1 if pin.y < 0 else -1  # library Y-up -> schematic Y-down
    else:
        dx, dy = 1, 0  # centre pin; pick a deterministic default
    return _rotate_vector(dx, dy, rot) if rot else (dx, dy)


def _body_rects(symbol: SymbolDef) -> list[tuple[float, float, float, float]]:
    """Library-space ``(x1, y1, x2, y2)`` rectangles drawn as the symbol body."""
    rects: list[tuple[float, float, float, float]] = []

    def walk(node) -> None:
        for child in node:
            if not isinstance(child, list):
                continue
            if child and str(child[0]) == "rectangle":
                start = end = None
                for item in child:
                    if isinstance(item, list) and item and item[0] == "start":
                        start = item
                    elif isinstance(item, list) and item and item[0] == "end":
                        end = item
                if (start is not None and end is not None
                        and len(start) >= 3 and len(end) >= 3):
                    x1, y1 = float(start[1]), float(start[2])
                    x2, y2 = float(end[1]), float(end[2])
                    rects.append((min(x1, x2), min(y1, y2),
                                  max(x1, x2), max(y1, y2)))
            else:
                walk(child)

    walk(symbol.subtree)
    return rects


def _rotate_vector(dx: int, dy: int, rot: int) -> tuple[int, int]:
    """Counter-clockwise rotation in schematic space (Y grows down)."""
    steps = (rot // 90) % 4
    for _ in range(steps):
        dx, dy = dy, -dx
    return dx, dy


def place(circuit: Circuit, resolved: dict[str, SymbolDef],
          sheets: list | None = None,
          hier_labels: dict[str, str] | None = None) -> LayoutResult:
    """Assign coordinates to every component and calculate pin positions.

    Functional blocks become columns, left to right. The column pitch is not
    a fixed step: each column gets exactly as much room as its widest pin
    offset plus wire stub and net-label text needs, so wide symbols (custom
    board symbols can be 55 mm and more) never overlap their neighbours.
    Rows that would run past the sheet's right edge wrap into a new
    horizontal band below.

    ``sheets`` is a list of :class:`~kicadgen.model.SheetSpec`; every one is
    drawn as a sub-sheet box, placed right of all placed components (or on a
    lower band if the sheet runs out of width). ``hier_labels`` is passed on
    to :func:`route` (net name -> hierarchical label shape) and matters for
    sub-sheet circuits, whose shared nets connect by name.
    """
    placements: dict[str, Placement] = {}

    ordered = sorted(
        circuit.components,
        key=lambda c: (
            BLOCK_ORDER.index(_block_of(c.prefix(), c.block))
            if _block_of(c.prefix(), c.block) in BLOCK_ORDER
            else len(BLOCK_ORDER),
            c.ref,
        ),
    )

    # Bucket components into functional blocks, preserving first-seen
    # order. A block that is too tall for one page column flows over into
    # a second sub-column inside the same frame (the D32 reference draws
    # its power section exactly so: several part columns behind one
    # dashed border with a single heading).
    blocks: list[list[list]] = []   # block -> sub-columns -> components
    block_names: list[str] = []
    index_of: dict[str, int] = {}
    for comp in ordered:
        block = _block_of(comp.prefix(), comp.block)
        if block not in index_of:
            index_of[block] = len(blocks)
            blocks.append([[]])
            block_names.append(block)
        blocks[index_of[block]][-1].append(comp)

    # Net name per (component, pin) for label-width estimation.
    label_of = {
        (c.component, c.pin): net.name
        for net in circuit.nets
        for c in net.connections
    }

    # Vertical capacity of one part column: from the first row origin
    # (Y0) down to the bottom limit, minus the under-label clearance.
    avail = PAGE_BOTTOM_LIMIT - Y0 - 6.35

    def _row_step(prev_comp, next_comp) -> float:
        """Vertical origin delta between two successive placements."""
        prev_down = max((-pin.y for pin
                         in resolved[prev_comp.ref].pins.values()),
                        default=0.0)
        up = max((pin.y for pin in resolved[next_comp.ref].pins.values()),
                 default=0.0)
        return prev_down + ROW_GAP + up

    # Flow very tall blocks into a second sub-column: walk the component
    # list, tracking the running pin-to-pin depth; when the next part
    # would not fit above the page bottom, close the sub-column.
    for bi in range(len(blocks)):
        comps = blocks[bi][0]
        subs: list[list] = [[]]
        cursor = 0.0
        prev = None
        for comp in comps:
            up = max((pin.y for pin in resolved[comp.ref].pins.values()),
                     default=0.0)
            step = 0.0 if prev is None else _row_step(prev, comp)
            if prev is not None and cursor + step + up > avail:
                subs.append([])
                cursor = 0.0
                step = 0.0
            subs[-1].append(comp)
            cursor += step
            prev = comp
        blocks[bi] = subs

    extents: list[list[tuple[float, float]]] = [
        [_column_extent(group, resolved, label_of) for group in block]
        for block in blocks
    ]

    # Sub-column origins and placements, left to right. A sub-column
    # starts right of the previous sub-column of its own block (frame
    # flow) or of the last column of the previous block; past the page's
    # right edge it wraps into a new horizontal band below, whose top
    # clears every already-placed part (tall columns keep their x slot,
    # so a wrapped sub-column must not overlap them).
    sub_origins: list[list[float]] = []
    sub_bands: list[list[int]] = []
    band = 0
    band_tops = {0: Y0}
    last_origin: float | None = None
    last_right = 0.0
    content_bottom = 0.0
    for bi, block in enumerate(blocks):
        origins_i: list[float] = []
        bands_i: list[int] = []
        for si, group in enumerate(block):
            ext_left, ext_right = extents[bi][si]
            if last_origin is None:
                origin = X0
            else:
                origin = last_origin + last_right + COLUMN_GAP + ext_left
                if origin + ext_right > PAGE_RIGHT_LIMIT:
                    # Start a new horizontal band below.
                    band += 1
                    band_tops[band] = max(
                        Y0 + band * BAND_STEP,
                        snap(content_bottom + 6.35 + ROW_GAP))
                    origin = X0
            origins_i.append(snap(origin))
            bands_i.append(band)
            last_origin = snap(origin)
            last_right = ext_right

            # Place the sub-column's components: rows stack with the
            # dynamic pitch, starting at the band's top row.
            cursor = band_tops[band]
            for r, comp in enumerate(group):
                symbol = resolved[comp.ref]
                if r > 0:
                    cursor += _row_step(group[r - 1], comp)
                x = origins_i[-1]
                y = snap(cursor)
                pins = {
                    num: pin_projected(symbol, pin, x, y, comp.rot)
                    for num, pin in symbol.pins.items()
                }
                placements[comp.ref] = Placement(
                    ref=comp.ref, x=x, y=y, rot=comp.rot, unit=comp.unit,
                    pins=pins, symbol=symbol,
                )
                content_bottom = max(
                    content_bottom, max((p[1] for p in pins.values()),
                                        default=y))
        sub_origins.append(origins_i)
        sub_bands.append(bands_i)

    routing = route(circuit, placements, hier_labels=hier_labels)
    sheet_boxes = place_sheets(circuit, placements, routing, sheets or ())
    decorations = (
        _block_frames(circuit, blocks, block_names, sub_origins, extents,
                      placements)
        if circuit.block_frames else []
    )
    return LayoutResult(
        placements=placements, routing=routing, sheets=sheet_boxes,
        decorations=decorations,
    )


def _column_extent(
    comps: list, resolved: dict[str, SymbolDef],
    label_of: dict[tuple[str, str], str],
) -> tuple[float, float]:
    """How far a column reaches to the left and right of its origin.

    Foundations: the real pin offsets of every symbol in the column, plus a
    wire stub (STUB) and, for pins on a net, room for the label text. Sides
    assume the dominant horizontal axis of the pin; unrotated placements put
    all stubs on that axis, and the generous row spacing leaves vertical
    room for the rest.
    """
    left = right = MIN_EXTENT
    for comp in comps:
        symbol = resolved[comp.ref]
        for num, pin in symbol.pins.items():
            reach = abs(pin.x) + STUB
            net = label_of.get((comp.ref, num))
            if net:
                reach += LABEL_ADVANCE * len(net)
            else:
                reach += 1.27  # margin for the bare pin, no stub/label
            if pin.x >= 0:
                right = max(right, reach)
            else:
                left = max(left, reach)
    return (snap(left), snap(right))


def pin_position(symbol: SymbolDef, number: str, x: float, y: float, rot: int):
    pin = symbol.pins.get(number)
    if pin is None:
        raise KeyError(f"Pin '{number}' not defined on {symbol.lib_id}")
    return pin_projected(symbol, pin, x, y, rot)


def pin_projected(symbol, pin: Pin, x: float, y: float, rot: int):
    """Absolute schematic position of a library pin (verified transform)."""
    px, py = pin.x, pin.y
    if rot == 0:
        sx, sy = x + px, y - py
    elif rot == 90:
        sx, sy = x - py, y - px
    elif rot == 180:
        sx, sy = x - px, y + py
    elif rot == 270:
        sx, sy = x + py, y + px
    else:
        raise ValueError(f"Unsupported rotation: {rot}")
    return (snap(sx), snap(sy))


def route(circuit: Circuit, placements: dict[str, Placement],
          hier_labels: dict[str, str] | None = None) -> Routing:
    """Route every net by outward stubs with local labels.

    Each pin gets a short wire stub pointing away from its symbol body and a
    local label carrying the net name. KiCad connects labels electrically by
    name, so this is intrinsically collision-free: no wire can ever cross a
    symbol body or another net's routing. Net names also land directly in
    the exported netlist.

    ``hier_labels`` maps net names to hierarchical label shapes. The first
    stub of such a net gets a hierarchical label (so a parent sheet pin can
    join the net); the other stubs keep plain local labels.
    """
    routing = Routing()
    hier_done: set[str] = set()

    # Body rectangles per symbol: stubs leave the body face a pin sits on
    # (see _stub_direction). Cached per symbol object.
    body_cache: dict[int, list[tuple[float, float, float, float]]] = {}

    for net in circuit.nets:
        conns = [c for c in net.connections if c.component in placements]
        if not conns:
            continue
        shape = None
        if hier_labels and net.name in hier_labels and net.name not in hier_done:
            hier_done.add(net.name)
            shape = hier_labels[net.name]
        for conn in conns:
            placement = placements[conn.component]
            symbol = placement.symbol
            rects = body_cache.get(id(symbol))
            if rects is None:
                rects = _body_rects(symbol)
                body_cache[id(symbol)] = rects
            pin = symbol.pins[conn.pin]
            direction = _stub_direction(pin, placement.rot, rects)
            start = placement.pins[conn.pin]
            end = (snap(start[0] + direction[0] * STUB),
                   snap(start[1] + direction[1] * STUB))
            routing.wires.append([start, end])
            # Horizontal text keeps labels readable regardless of stub direction,
            # but the justification follows it: a leftward stub's label grows
            # to the left so it never runs across the symbol body.
            rot = 0
            justify = "right" if direction[0] < 0 else "left"
            routing.labels.append((net.name, end, rot, justify, shape))
            shape = None  # only the first stub of a hier net is the port
    return routing


def place_sheets(circuit, placements, routing, sheets) -> list[SheetBox]:
    """Position sub-sheet boxes and route their pin stubs.

    A sheet box goes right of everything already placed, so its pin stubs
    (which carry the same net label the parent wire already has) sit in free
    space. Boxes that would pass the right page edge move to a lower row.
    Pin names become the net labels: KiCad joins a parent net to its
    sub-sheet circuit through the shared name (sheet pin + hierarchical
    label in the child file).
    """
    if not sheets:
        return []
    boxes: list[SheetBox] = []
    content_right = _content_right(placements, routing)
    content_bottom = _content_bottom(placements, routing)
    for spec in sheets:
        # Pins on both edges (KiCad demo style): outputs to the right,
        # everything else fills the left to half the pins. Height covers
        # the fuller edge.
        count = len(spec.pins)
        n_left = (count + 1) // 2
        cap_right = count - n_left
        outputs = [p for p in spec.pins if p[1] == "output"]
        rest = [p for p in spec.pins if p[1] != "output"]
        left: list[tuple[str, str]] = []
        right: list[tuple[str, str]] = []
        for pin_spec in outputs:
            if len(right) < cap_right:
                right.append(pin_spec)
            else:
                left.append(pin_spec)
        for pin_spec in rest:
            (left if len(left) < n_left else right).append(pin_spec)
        rows = max(len(left), len(right), 1)
        height = snap(SHEET_PIN_FIRST + (rows - 1) * SHEET_PIN_PITCH
                      + SHEET_PIN_FIRST)
        x = snap(content_right + 15.24)
        y = snap(Y0)
        if x + SHEET_BOX_WIDTH + 5.0 > PAGE_RIGHT_LIMIT:
            x = snap(X0)
            y = snap(min(content_bottom + 15.24, 190.5))
        pins: list[tuple[str, str, tuple[float, float], str]] = []
        for i, (name, direction) in enumerate(left):
            pos = (snap(x), snap(y + SHEET_PIN_FIRST + i * SHEET_PIN_PITCH))
            pins.append((name, direction, pos, "left"))
        for i, (name, direction) in enumerate(right):
            pos = (snap(x + SHEET_BOX_WIDTH),
                   snap(y + SHEET_PIN_FIRST + i * SHEET_PIN_PITCH))
            pins.append((name, direction, pos, "right"))
        box = SheetBox(
            uuid=new_sheet_uuid(), title=spec.title,
            filename=spec.filename, x=x, y=y, w=SHEET_BOX_WIDTH, h=height,
            pins=pins,
        )
        for name, _direction, pos, side in box.pins:
            end = (snap(pos[0] - STUB if side == "left" else pos[0] + STUB),
                   pos[1])
            routing.wires.append([pos, end])
            routing.labels.append((name, end, 0,
                                   "right" if side == "left" else "left",
                                   None))
        content_bottom = max(content_bottom, y + height)
        content_right = max(content_right, x + SHEET_BOX_WIDTH)
        boxes.append(box)
    return boxes


def _block_frames(circuit, blocks, block_names, sub_origins, extents,
                  placements) -> list:
    """Dashed rectangle + heading per functional block.

    Duplicated edges towards a neighbour block show as two parallel
    dashed lines (the D32 reference sheet draws several such doubled
    section borders). A block's frame spans all of its sub-columns
    (over-tall blocks flow into a sibling part column inside the same
    frame) and is sized directly from the placed pin coordinates, so it
    always wraps the real symbol bodies plus room for their net-name
    labels and stays within the page.
    """
    lines: list = []
    texts: list = []
    for bi, subs in enumerate(blocks):
        pin_ys: list[float] = []
        left = 1e9
        right = -1e9
        for si, group in enumerate(subs):
            ext_left, ext_right = extents[bi][si]
            left = min(left, sub_origins[bi][si] - ext_left)
            right = max(right, sub_origins[bi][si] + ext_right)
            for comp in group:
                for x, y in placements[comp.ref].pins.values():
                    pin_ys.append(y)
        if not pin_ys:
            continue
        # 13.97 above the topmost pin: heading strip plus clearance for
        # the name/ref/value field text KiCad renders over the pins
        # (value fields reach ~4.4 mm above the origin); 6.35 below the
        # bottom-most pin clears the under-symbols label text.
        top = snap(min(pin_ys) - 13.97)
        bottom = min(snap(max(pin_ys) + 6.35), PAGE_BOTTOM_LIMIT)
        lines.extend([
            GraphicLine(left, top, right, top, style="dash"),
            GraphicLine(left, bottom, right, bottom, style="dash"),
            GraphicLine(left, top, left, bottom, style="dash"),
            GraphicLine(right, top, right, bottom, style="dash"),
        ])
        heading = circuit.block_headings.get(
            block_names[bi], block_names[bi].upper())
        texts.append(GraphicText(
            left + 2.54, top + 3.81, heading, justify="left",
        ))
    return lines + texts


def new_sheet_uuid() -> str:
    import uuid as _uuid
    return str(_uuid.uuid4())


def _content_right(placements, routing) -> float:
    """Right reach of placed symbols, wires and label text."""
    right = X0
    for pl in placements.values():
        right = max(right, pl.x + 15.24)
    for wire in routing.wires:
        right = max(right, wire[-1][0])
    for text, pos, _rot, justify, _shape in routing.labels:
        reach = pos[0] + (LABEL_ADVANCE * len(text) if justify == "left" else 1.27)
        right = max(right, reach)
    return right


def _content_bottom(placements, routing) -> float:
    """Bottom reach of placed symbols, wires and label text."""
    bottom = Y0
    for pl in placements.values():
        bottom = max(bottom, max((p[1] for p in pl.pins.values()), default=pl.y))
    for wire in routing.wires:
        bottom = max(bottom, wire[-1][1])
    for _text, pos, _rot, _justify, _shape in routing.labels:
        bottom = max(bottom, pos[1] + 1.27)
    return bottom


def is_power_net(name: str) -> bool:
    return any(name.startswith(h) or name == h for h in POWER_NET_HINTS)
