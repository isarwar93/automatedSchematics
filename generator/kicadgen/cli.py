"""Command line interface: ties the pipeline together.

Usage:
    python -m kicadgen generate <spec.yaml> [-o out.kicad_sch] [--export] [--no-validate]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from kicadgen.symbols.custom import inline_symbols
from kicadgen.drawing.layout import place
from kicadgen.core.model import circuit_from_dict
from kicadgen.symbols.resolve import SymbolDef, SymbolResolver
from kicadgen.export.validate import (
    ValidationError,
    check_kicad_cli,
    export_netlist,
    export_svg,
    netlist_nets,
    run_erc,
    validate_spec,
)
from kicadgen.drawing.writer import (
    SchematicWriter,
    WriterOptions,
    new_uuid,
    sheet_direction,
    write_project_file,
    write_schematic,
)


def load_spec(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit(
                "PyYAML is required for YAML specs: pip install pyyaml"
            )
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: specification must be a mapping")
    return data


def generate(spec_path: str, output: str, export: bool = False) -> int:
    data = load_spec(spec_path)
    try:
        circuit = circuit_from_dict(data)
    except ValueError as exc:
        print(f"error: {spec_path}: {exc}", file=sys.stderr)
        return 1
    return generate_circuit(
        circuit, output, export=export,
        inline_defs=list(inline_symbols(data).values()),
    )


def generate_circuit(
    circuit: Circuit,
    output: str,
    export: bool = False,
    inline_defs=(),
    sheets=(),
) -> int:
    """Run the full pipeline for an already-built :class:`~kicadgen.model.Circuit`.

    ``inline_defs`` are pre-built custom symbol definitions (spec-declared
    libraries). ``sheets`` are :class:`~kicadgen.model.SheetSpec` sub-sheets:
    each is placed, written as its own ``.kicad_sch`` next to the parent file
    and referenced from a ``(sheet ...)`` box in the parent. Shared by the
    CLI and the Python API (``kicadgen.api``).
    """
    resolver = SymbolResolver()
    resolver.register(inline_defs)
    try:
        resolved = {}
        for comp in circuit.components:
            resolved[comp.ref] = resolver.resolve(comp.symbol)
        sheet_resolved: list[dict[str, SymbolDef]] = []
        for spec in sheets:
            si = {}
            for comp in spec.circuit.components:
                si[comp.ref] = resolver.resolve(comp.symbol)
            sheet_resolved.append(si)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    for circuit_i, resolved_i in (
            [(circuit, resolved)]
            + [(s.circuit, r) for s, r in zip(sheets, sheet_resolved, strict=True)]
    ):
        spec_errors, spec_warnings = validate_spec(circuit_i, resolved_i)
        errors.extend(spec_errors)
        warnings.extend(spec_warnings)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print("Specification invalid; no schematic written.", file=sys.stderr)
        return 1

    root_uuid = new_uuid()
    layout_result = place(circuit, resolved, sheets=list(sheets))
    options = WriterOptions(project=os.path.splitext(os.path.basename(output))[0],
                            root_uuid=root_uuid)
    text = write_schematic(circuit, resolved, layout_result, output, options)
    print(f"wrote {output} ({len(text.splitlines())} lines)")
    # One project file per folder, named for the root schematic; it marks
    # output as a KiCad project a future PCB layout can join.
    project = write_project_file(output)
    if project:
        print(f"wrote {project}")

    # Sub-sheets: place, write, and reference from the parent sheet boxes.
    for index, spec in enumerate(sheets):
        box = layout_result.sheets[index]
        child_path = os.path.join(
            os.path.dirname(os.path.abspath(output)) or ".", spec.filename)
        hier = {name: sheet_direction(direction) for name, direction in spec.pins}
        child_layout = place(spec.circuit, sheet_resolved[index],
                             hier_labels=hier)
        child_options = WriterOptions(
            project=options.project,
            title=spec.title,
            omit_sheet_instances=True,
            instance_path=f"/{root_uuid}/{box.uuid}",
        )
        child_text = SchematicWriter(
            spec.circuit, sheet_resolved[index], child_layout, child_options,
        ).render()
        with open(child_path, "w", encoding="utf-8") as fh:
            fh.write(child_text)
        print(f"wrote {child_path} ({len(child_text.splitlines())} lines)")

    if export:
        base = os.path.splitext(output)[0]
        try:
            version = check_kicad_cli()
            print(f"kicad-cli {version}")
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        erc_report = base + ".erc.rpt"
        count, _lines = run_erc(output, erc_report)
        print(f"ERC: {count} violation(s) -> {erc_report}")

        netlist = base + ".net"
        export_netlist(output, netlist)
        nets = netlist_nets(netlist)
        print(f"netlist: {len(nets)} net(s) -> {netlist}")
        for name, nodes in nets.items():
            print(f"  {name}: {', '.join(nodes)}")

        svg_dir = base + "-svg"
        svgs = export_svg(output, svg_dir)
        print(f"SVG: {', '.join(svgs)}")

        if count:
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kicadgen",
        description="Generate and validate a KiCad schematic from a YAML/JSON spec.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate output/design.kicad_sch from a spec")
    gen.add_argument("spec", help="YAML or JSON circuit specification")
    gen.add_argument("-o", "--output", default="output/design.kicad_sch")
    gen.add_argument("--export", action="store_true",
                     help="run ERC, netlist and SVG export afterwards")

    args = parser.parse_args(argv)
    if args.command == "generate":
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        try:
            return generate(args.spec, args.output, export=args.export)
        except ValidationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
