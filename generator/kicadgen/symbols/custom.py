"""Synthesize symbol definitions declared inside a specification.

Some specs (e.g. ``projects/kitchenAnalyzer.yaml``) define board/breakout
symbols inline via ``symbol_definitions`` plus a
``libraries.custom_library`` nickname instead of shipping a ``.kicad_sym``
file. The generator converts those definitions into the same normalized
:class:`~kicadgen.symbols.SymbolDef` objects the resolver produces for
installed KiCad libraries, so the rest of the pipeline (layout, validation,
writing) needs no special cases.

Pin geometry is derived deterministically:

- pins are laid out by ``side`` (``left``/``right``), first listed pin
  outermost (top), on a 2.54 mm pitch,
- connection points sit one lead length plus the body edge away from the
  origin, so the layout engine's outward stub direction (dominant axis of
  the pin coordinate) is always away from the body,
- the drawn pin line (angle + length) always points into the body, ending
  exactly at the rectangle edge,
- electrical types use KiCad tokens directly (``power_in``, ``output``, ...).

The produced subtree matches what the resolver would extract from a real
library file, so it embeds cleanly into the schematic's ``lib_symbols``
section.
"""

from __future__ import annotations

from kicadgen.core import sexpr
from kicadgen.drawing.layout import snap  # schematic grid; pin coordinates must land on it
from kicadgen.core.sexpr import Q
from kicadgen.symbols.resolve import Pin, SymbolDef, _to_def

PITCH = 2.54  # vertical distance between neighbouring pins
LEAD = 2.54  # drawn pin length outside of the body edge
HALF_WIDTH = 10.16  # minimum symbol body half width
MIN_HALF_HEIGHT = 5.08  # symbol body half height when pins do not demand more

NAME_ADVANCE = 1.15  # estimated per-character pin-name width at 1.27 mm font
NAME_MARGIN = 1.27  # pin-name inset from the body edge, per side

VALID_ETYPES = {
    "input",
    "output",
    "bidirectional",
    "tri_state",
    "passive",
    "unspecified",
    "open_collector",
    "open_emitter",
    "free",
    "power_in",
    "power_out",
    "no_connect",
    "non_logic",
}


def _N(*items) -> list:
    """Build a raw S-expression node (atoms stay bare unless pre-quoted)."""
    node: list = []
    for item in items:
        node.append(item[1] if isinstance(item, tuple) else item)
    return node


def _Nq(*items) -> list:
    """Like :func:`_N` but plain strings become quoted atoms."""
    node = _N(*items)
    for i, item in enumerate(node):
        if isinstance(item, str) and not isinstance(item, Q):
            node[i] = Q(item)
    return node


def inline_symbols(data: dict) -> dict[str, SymbolDef]:
    """Build every ``symbol_definitions`` entry of a parsed spec.

    Returns a mapping of ``lib_id`` -> :class:`SymbolDef`, ready to be
    registered with the resolver. Returns an empty mapping for specs without
    inline definitions.
    """
    definitions = data.get("symbol_definitions") or []
    if not definitions:
        return {}
    custom = (data.get("libraries") or {}).get("custom_library") or {}
    nickname = str(custom.get("nickname") or "Custom")
    return {d.lib_id: d for d in (_build(spec, nickname) for spec in definitions)}


def build(spec: dict, nickname: str = "Custom") -> SymbolDef:
    """Public builder: one ``symbol_definitions``-style dict -> :class:`SymbolDef`."""
    return _build(spec, nickname)


def _build(spec: dict, nickname: str) -> SymbolDef:
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError("symbol_definitions: every entry needs a 'name'")
    lib_id = f"{nickname}:{name}"

    pin_specs = spec.get("pins") or []
    if not pin_specs:
        raise ValueError(f"{lib_id}: symbol has no 'pins'")

    left = [p for p in pin_specs if str(p.get("side") or "left") == "left"]
    right = [p for p in pin_specs if str(p.get("side") or "right") == "right"]

    # Spec-defined symbols may use signal names instead of numeric pin
    # numbers (e.g. "GPIO13"); both are kept as strings.
    seen: set[str] = set()
    for ps in pin_specs:
        number = str(ps.get("number") or "")
        if not number:
            raise ValueError(f"{lib_id}: pin without a 'number'")
        if number in seen:
            raise ValueError(f"{lib_id}: duplicate pin number '{number}'")
        seen.add(number)
        _check_etype(ps, lib_id)

    value = str(spec.get("value") or name)
    prefix = str(spec.get("reference_prefix") or "U")
    footprint = str(spec.get("footprint") or "")

    half_height = _half_height(len(left), len(right))
    half_width = _half_width(left, right)

    subtree = _subtree(
        lib_id=lib_id,
        name=name,
        prefix=prefix,
        value=value,
        footprint=footprint,
        half_height=half_height,
        half_width=half_width,
        left=left,
        right=right,
    )
    return _to_def(lib_id, subtree)


def _check_etype(ps: dict, lib_id: str) -> None:
    etype = str(ps.get("electrical_type") or _DEFAULT_ETYPE)
    if etype not in VALID_ETYPES:
        valid = ", ".join(sorted(VALID_ETYPES))
        raise ValueError(
            f"{lib_id}: pin '{ps.get('number')}' has unknown electrical_type"
            f" '{etype}' (valid: {valid})"
        )


_DEFAULT_ETYPE = "passive"


def _half_height(n_left: int, n_right: int) -> float:
    """Body half height fitting the tallest pin side plus a margin."""
    n = max(n_left, n_right)
    span = (n - 1) * PITCH / 2 if n else 0.0
    return max(MIN_HALF_HEIGHT, span + PITCH)


def _half_width(left: list, right: list) -> float:
    """Body half width fitting the longest pin names of both sides.

    Pin names draw inside the body next to their pins, so the left side
    occupies the longest left name plus an inset, likewise the right side.
    """
    def width(pins: list) -> float:
        if not pins:
            return NAME_MARGIN
        longest = max(len(str(p.get("name") or p.get("number"))) for p in pins)
        return longest * NAME_ADVANCE + NAME_MARGIN

    # The width becomes the pin's x offset and must stay on the schematic
    # grid (1.27 mm), or wire stubs would miss the pin connection points.
    return snap(max(HALF_WIDTH, width(left) + width(right)))


def _subtree(
    lib_id: str,
    name: str,
    prefix: str,
    value: str,
    footprint: str,
    half_height: float,
    half_width: float,
    left: list,
    right: list,
) -> list:
    top_y = half_height + 1.27
    outer: list = [Q("symbol"), Q(lib_id)]
    outer.append(_N("pin_numbers", "hide"))
    outer.append(_N("pin_names", _N("offset", 0.254)))
    outer.append(_N("exclude_from_sim", "no"))
    outer.append(_N("in_bom", "yes"))
    outer.append(_N("on_board", "yes"))
    outer.append(_property("Reference", prefix, top_y))
    outer.append(_property("Value", value, -top_y))
    outer.append(_property("Footprint", footprint, 0, hidden=True))
    outer.append(_property("Datasheet", "~", 0, hidden=True))
    outer.append(_property("Description", value, 0, hidden=True))

    body = [Q("symbol"), Q(f"{name}_0_1"), _body_rectangle(half_height, half_width)]
    outer.append(body)

    unit: list = [Q("symbol"), Q(f"{name}_1_1")]
    unit.extend(_pin_node(p) for p in _place_pins(half_width, left, right))
    outer.append(unit)
    return outer


def _body_rectangle(half_height: float, half_width: float) -> list:
    return _N(
        "rectangle",
        _N("start", -half_width, -half_height),
        _N("end", half_width, half_height),
        _N("stroke", _N("width", 0.254), _N("type", "default")),
        _N("fill", _N("type", "background")),
    )


def _place_pins(half_width: float, left: list, right: list) -> list[Pin]:
    """Pin objects laid out left-most-first, top to bottom per side."""
    pins: list[Pin] = []
    outer_x = half_width + LEAD
    for i, ps in enumerate(left):
        pins.append(_pin_obj(ps, x=-outer_x, y=_y(i, len(left)), angle=0))
    for i, ps in enumerate(right):
        pins.append(_pin_obj(ps, x=outer_x, y=_y(i, len(right)), angle=180))
    return pins


def _y(index: int, count: int) -> float:
    """Y-up library geometry: index 0 sits topmost, centred on the origin."""
    return round((count - 1) * PITCH / 2 - index * PITCH, 4)


def _pin_obj(ps: dict, x: float, y: float, angle: int) -> Pin:
    number = str(ps["number"])
    return Pin(
        number=number,
        name=str(ps.get("name") or number),
        x=x,
        y=y,
        angle=angle,
        etype=str(ps.get("electrical_type") or _DEFAULT_ETYPE),
    )


def _pin_node(pin: Pin) -> list:
    node = _N("pin", pin.etype, "line",
              _N("at", pin.x, pin.y, pin.angle),
              _N("length", LEAD))
    node.append(_Nq("name", pin.name))
    node[-1].append(_N("effects", _N("font", _Nq("size", 1.27, 1.27))))
    node.append(_Nq("number", pin.number))
    node[-1].append(_N("effects", _N("font", _Nq("size", 1.27, 1.27))))
    return node


def _property(prop: str, value: str, y: float, hidden: bool = False) -> list:
    node = _Nq("property", prop, value)
    node.append(_N("at", 0, y, 0))
    effects = _N("effects", _N("font", _Nq("size", 1.27, 1.27)))
    if hidden:
        effects.append(_N("hide", "yes"))
    node.append(effects)
    return node
