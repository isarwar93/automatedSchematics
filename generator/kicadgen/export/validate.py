"""Validation: specification checks plus ``kicad-cli``-based checks.

Three levels, per BUILD_PLAN 9:
1. Spec validation - references, pin numbers, symbol resolution.
2. Export checks - SVG, netlist and ERC via ``kicad-cli sch ...``.
3. Electrical sanity - flags components with no connections and power nets
   without a driver (knowledge the software cannot infer for itself).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from kicadgen.core.model import Circuit
from kicadgen.symbols.resolve import SymbolDef


class ValidationError(Exception):
    """Raised only for fatal spec problems; warnings do not abort."""


# ---------------------------------------------------------------------
# Level 1: specification
# ---------------------------------------------------------------------


def validate_spec(
    circuit: Circuit, resolved: dict[str, SymbolDef]
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the circuit specification.

    ``errors`` must be empty before a schematic is generated.
    """
    errors: list[str] = []
    warnings: list[str] = []

    refs = [c.ref for c in circuit.components]
    if len(refs) != len(set(refs)):
        errors.append(f"Duplicate component references: {[r for r in refs if refs.count(r) > 1]}")

    # Custom sheet size checks (design.paper.width/height in mm). KiCad's
    # hard page limit is 1254.9 x 809.19 mm; larger sheets will not load.
    design = circuit.design
    if design.width is not None or design.height is not None:
        if not design.is_custom_size():
            errors.append(
                "design.paper: custom sheet size requires both 'width' and 'height' in mm"
            )
        else:
            if design.width <= 0 or design.height <= 0:
                errors.append(
                    f"design.paper: custom sheet size must be positive "
                    f"(got {design.width} x {design.height} mm)"
                )
            elif design.width > 1254.9 or design.height > 809.19:
                warnings.append(
                    f"design.paper: custom sheet {design.width} x {design.height} mm "
                    f"exceeds KiCad's maximum page size (1254.9 x 809.19 mm)"
                )

    known = {c.ref: c for c in circuit.components}
    for net in circuit.nets:
        for conn in net.connections:
            comp = known.get(conn.component)
            if comp is None:
                errors.append(f"Net '{net.name}' references unknown component '{conn.component}'")
                continue
            definition = resolved.get(comp.ref)
            if definition is None:
                continue
            if comp.rot not in (0, 90, 180, 270):
                errors.append(f"Component '{comp.ref}' has unsupported rotation {comp.rot}")
            if conn.pin not in definition.pins:
                errors.append(
                    f"Net '{net.name}': pin '{conn.pin}' does not exist on "
                    f"{comp.ref} ({comp.symbol}); available: {sorted(definition.pins)}"
                )
        if len(net.connections) == 0:
            warnings.append(f"Net '{net.name}' has no connections")
        elif len(net.connections) == 1:
            warnings.append(f"Net '{net.name}' connects only one pin; it will dangle")

    for comp in circuit.components:
        if not circuit.pins_connected(comp.ref):
            warnings.append(
                f"Component '{comp.ref}' has no connected pins (potentially unconnected)"
            )

    return errors, warnings


# ---------------------------------------------------------------------
# Level 2/3: kicad-cli based checks
# ---------------------------------------------------------------------


def check_kicad_cli() -> str:
    exe = shutil.which("kicad-cli")
    if not exe:
        raise FileNotFoundError("kicad-cli not found on PATH")
    return subprocess.run([exe, "version"], capture_output=True, text=True).stdout.strip()


def run_erc(schematic: str, report: str) -> tuple[int, list[str]]:
    """Run ERC; return (violation_count, report_lines).

    ``kicad-cli sch erc --exit-code-violations`` exits non-zero when
    violations exist; that is an expected result, not an error.
    """
    result = subprocess.run(
        ["kicad-cli", "sch", "erc", "--severity-all", "--exit-code-violations",
         "-o", report, schematic],
        capture_output=True, text=True,
    )
    if "Failed" in (result.stderr or ""):
        raise RuntimeError(f"kicad-cli sch erc failed:\n{result.stderr}")
    with open(report, encoding="utf-8") as fh:
        lines = [line.rstrip() for line in fh if line.strip()]
    count = 0
    text = "\n".join(lines)
    m = re.search(r"\*\*\s+ERC messages:\s*(\d+)", text)
    if m:
        count = int(m.group(1))
    else:
        m = re.search(r"\*\* Found (\d+) ERC violations", text)
        if m:
            count = int(m.group(1))
        else:
            count = sum(1 for l in lines if re.match(r"^\s*\[\w+\]:", l))
    return count, lines


def export_netlist(schematic: str, netlist: str) -> None:
    run(["kicad-cli", "sch", "export", "netlist", "-o", netlist, schematic])


def export_svg(schematic: str, outdir: str) -> list[str]:
    run(["kicad-cli", "sch", "export", "svg", "-o", outdir, schematic])
    return sorted(
        os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".svg")
    )


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} failed ({result.returncode}):\n{result.stderr or result.stdout}"
        )
    return result


def netlist_nets(netlist_path: str) -> dict[str, list[str]]:
    """Parse the exported netlist; return net name -> ['REF/pin', ...]."""
    with open(netlist_path, encoding="utf-8") as fh:
        text = fh.read()
    nets: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r'\(net \(code "[^"]+"\) \(name "([^"]+)"\)', stripped)
        if m:
            current = m.group(1)
            nets.setdefault(current, [])
            continue
        if current and re.match(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', stripped):
            m2 = re.match(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', stripped)
            nets[current].append(f"{m2.group(1)}/{m2.group(2)}")
        if stripped.startswith("(nets)") or stripped.startswith(")"):
            continue
    return nets
