"""Symbol resolver: reads KiCad's installed symbol libraries.

Responsibilities (per BUILD_PLAN 6):
1. Split ``Device:R`` into library nickname and symbol name.
2. Locate the library file (KiCad's symbol directory).
3. Read the symbol definition.
4. Extract pins (numbers, names, electrical types, coordinates).
5. Resolve ``extends`` (inherited) symbols by flattening.
6. Return a normalized internal representation (:class:`SymbolDef`).

Prototype scope: ordinary single-unit symbols (resistors, capacitors,
connectors, and small ICs). Multi-unit symbols are handled generically by
gathering the pins of every unit; instances only place one unit.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

from kicadgen.core import sexpr
from kicadgen.core.sexpr import Q

DEFAULT_SEARCH_PATHS = [
    os.environ.get("KICAD9_SYMBOL_DIR", ""),
    os.environ.get("KICAD_SYMBOL_DIR", ""),
    "/usr/share/kicad/symbols",
    os.path.expanduser("~/.local/share/kicad/9.0/symbols"),
    os.path.expanduser("~/.local/share/kicad/symbols"),
]


@dataclass
class Pin:
    number: str
    name: str
    x: float  # library coordinates, Y-up
    y: float
    angle: int  # 0 / 90 / 180 / 270
    etype: str  # passive, input, power_in, ...


@dataclass
class SymbolDef:
    lib_id: str  # e.g. "Device:R"
    pins: dict[str, Pin] = field(default_factory=dict)
    subtree: list = field(default_factory=list)  # embeddable lib_symbols tree
    units: int = 1
    in_bom: bool = True
    on_board: bool = True
    exclude_from_sim: bool = False

    @property
    def name(self) -> str:
        return self.lib_id.split(":", 1)[1]


def library_paths(extra: list[str] | None = None) -> list[str]:
    paths = [p for p in DEFAULT_SEARCH_PATHS if p and os.path.isdir(p)]
    if extra:
        paths.extend(p for p in extra if os.path.isdir(p))
    if not paths:
        raise FileNotFoundError(
            "No KiCad symbol directory found; set KICAD9_SYMBOL_DIR."
        )
    return paths


def find_library(nickname: str, paths: list[str] | None = None) -> str:
    """Return the path of ``<nickname>.kicad_sym``."""
    for base in paths or library_paths():
        cand = os.path.join(base, nickname + ".kicad_sym")
        if os.path.isfile(cand):
            return cand
    bases = ", ".join(paths or library_paths())
    raise FileNotFoundError(f"Symbol library '{nickname}' not found in: {bases}")


def _num(value) -> float:
    return float(str(value))


def _child(node, tag: str):
    """First direct child list whose tag equals ``tag``."""
    for sub in node:
        if isinstance(sub, list) and sub and str(sub[0]) == tag:
            return sub
    return None


class SymbolResolver:
    """Loads and caches symbol definitions from KiCad libraries."""

    def __init__(self, extra_paths: list[str] | None = None):
        self.paths = extra_paths or library_paths()
        self._cache: dict[str, SymbolDef] = {}

    def register(self, definitions) -> None:
        """Add pre-built definitions (e.g. spec-defined custom symbols)."""
        for definition in definitions:
            self._cache[definition.lib_id] = definition

    def resolve(self, lib_id: str) -> SymbolDef:
        if lib_id in self._cache:
            return self._cache[lib_id]

        if ":" not in lib_id:
            raise ValueError(f"Symbol id must be 'Library:Name', got '{lib_id}'")
        nickname, name = lib_id.split(":", 1)
        path = find_library(nickname, self.paths)
        with open(path, "r", encoding="utf-8") as fh:
            tree = sexpr.parse(fh.read())

        top_level = [s for s in tree if isinstance(s, list) and str(s[0]) == "symbol"]
        node = next((s for s in top_level if str(s[1]) == name), None)
        if node is None:
            raise KeyError(f"Symbol '{name}' not found in library '{nickname}'")

        subtree = node
        # Handle inheritance: copy the parent, overlay own properties, rename.
        extends = _child(node, "extends")
        if extends is not None:
            parent_name = str(extends[1])
            parent = next((s for s in top_level if str(s[1]) == parent_name), None)
            if parent is None:
                raise KeyError(
                    f"Symbol '{name}' extends '{parent_name}', which is missing"
                )
            subtree = _flatten(parent_name, parent, node)
            # Overlay the derived symbol's own non-property attributes.
            for ctr in node:
                if not (isinstance(ctr, list) and str(ctr[0]) in
                        ("in_bom", "on_board", "exclude_from_sim")):
                    continue
                existing = _child(subtree, str(ctr[0]))
                if existing is not None:
                    subtree[subtree.index(existing)] = copy.deepcopy(ctr)

        subtree = _rename_embedded(subtree, name)
        subtree = _embeddable(subtree, lib_id)
        definition = _to_def(lib_id, subtree)
        self._cache[lib_id] = definition
        return definition


def _to_def(lib_id: str, subtree: list) -> SymbolDef:
    name = lib_id.split(":", 1)[1]
    pins: dict[str, Pin] = {}
    unit_numbers: set[int] = set()

    for sub in subtree:
        if not (isinstance(sub, list) and str(sub[0]) == "symbol"):
            continue
        sub_name = str(sub[1])
        # Unit sub-symbols look like "R_0_1", "R_1_1", "U_2_1", ...
        parts = sub_name.rsplit("_", 2)
        if len(parts) != 3 or parts[0] != name:
            continue
        # Pins live in "<name>_<unit>_1" unit blocks and in the common
        # "<name>_0_0" block (power symbols such as PWR_FLAG carry their
        # pin there).
        if parts[2] != "1" and not (parts[1] == "0" and parts[2] == "0"):
            continue
        unit_tag = parts[1]
        if unit_tag.isdigit():
            unit = int(unit_tag)
            unit_numbers.add(unit)
        else:
            unit = 0  # common pins shared by every unit ("Name_0_1")
        for pin_sub in sub:
            if not (isinstance(pin_sub, list) and str(pin_sub[0]) == "pin"):
                continue
            _add_pin(pins, pin_sub)

    units = max(max(unit_numbers, default=1), 1)

    return SymbolDef(
        lib_id=lib_id,
        pins=pins,
        subtree=subtree,
        units=units,
        in_bom=_flag(subtree, "in_bom", default=True),
        on_board=_flag(subtree, "on_board", default=True),
        exclude_from_sim=_flag(subtree, "exclude_from_sim", default=False),
    )


def _add_pin(pins: dict[str, Pin], pin_sub: list) -> None:
    at = _child(pin_sub, "at")
    number = _child(pin_sub, "number")
    pname = _child(pin_sub, "name")
    if at is None:
        return
    px, py = _num(at[1]), _num(at[2])
    angle = int(_num(at[3])) if len(at) > 3 else 0
    etype = str(pin_sub[1]) if len(pin_sub) > 1 and not isinstance(pin_sub[1], list) else "passive"
    if number is None:
        return
    pin = Pin(
        number=str(number[1]),
        name=str(pname[1]) if pname else "~",
        x=px,
        y=py,
        angle=angle,
        etype=etype,
    )
    pins[pin.number] = pin


def _flag(subtree: list, key: str, default: bool) -> bool:
    node = _child(subtree, key)
    if node is None:
        return default
    return len(node) > 1 and str(node[1]) == "yes"


def _embeddable(subtree: list, lib_id: str) -> list:
    """Prepare a resolved subtree for the schematic's ``lib_symbols`` section.

    - The outer symbol must be named ``Library:Name``.
    - Library-level noise such as ``embedded_fonts`` is removed.
    """
    out = copy.deepcopy(subtree)
    out[1] = Q(lib_id)
    out[:] = [
        c
        for c in out
        if not (isinstance(c, list) and str(c[0]) == "embedded_fonts")
    ]
    return out


def _flatten(parent_name: str, parent: list, derived: list) -> list:
    """Merge a parent symbol with one that ``extends`` it."""
    out = copy.deepcopy(parent)
    for node in derived:
        if not (isinstance(node, list) and node and isinstance(node[0], str)):
            continue
        tag = str(node[0])
        if tag == "property":
            prop_name = str(node[1])
            for i, existing in enumerate(out):
                if (
                    isinstance(existing, list)
                    and str(existing[0]) == "property"
                    and str(existing[1]) == prop_name
                ):
                    out[i] = copy.deepcopy(node)
                    break
            else:
                out.append(copy.deepcopy(node))
        elif tag == "extends":
            continue
        elif tag in ("in_bom", "on_board", "exclude_from_sim", "uuid"):
            continue
        elif tag != "symbol":
            existing = _child(out, tag)
            if existing is not None:
                out[out.index(existing)] = copy.deepcopy(node)
            else:
                out.append(copy.deepcopy(node))
    # Parent unit sub-symbols must carry the derived symbol's name prefix.
    for sub in out:
        if isinstance(sub, list) and str(sub[0]) == "symbol":
            old = str(sub[1])
            parts = old.rsplit("_", 2)
            if len(parts) == 3 and parts[0] == parent_name:
                sub[1] = f"{parts[0]}_{parts[1]}_{parts[2]}"
    return out


def _rename_embedded(subtree: list, name: str) -> list:
    """Prefix every unit sub-symbol with the symbol name (e.g. ``R_0_1``)."""
    subtree = copy.deepcopy(subtree)
    for sub in subtree:
        if isinstance(sub, list) and str(sub[0]) == "symbol":
            old = str(sub[1])
            parts = old.rsplit("_", 2)
            if len(parts) == 3 and parts[1].isdigit():
                sub[1] = f"{name}_{parts[1]}_{parts[2]}"
    return subtree
