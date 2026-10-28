#!/usr/bin/env python3
"""refresh_py_to_sch.py - run the generator against the project .py specs.

Every project spec in ``projects/<slug>/<slug>.py`` is pure Python
(``from kicadgen import Design, Component, connect``) that describes *what*
the circuit is; this script loads each spec, grabs the ``Design`` it builds,
and drives the generator so every project is written and validated the same
way (python -> schematic). The reverse direction lives in
``refresh_sch_to_py.py``. One folder per project: extra helper .py files for the same project
simply live next to the entry spec (e.g. the child-sheet design in
``projects/kitchen_analyzer/lolin32_internal.py``); the folder is put on
sys.path for the run, so the entry spec can import sibling modules
directly.

Example lines to run:

    python3 scripts/refresh_py_to_sch.py                            # generate all projects/*/*.py
    python3 scripts/refresh_py_to_sch.py kitchen_analyzer           # one project, by slug
    python3 scripts/refresh_py_to_sch.py projects/kitchen_analyzer/kitchen_analyzer.py
    python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export  # + kicad-cli ERC/netlist/SVG
    python3 scripts/refresh_py_to_sch.py kitchen_analyzer -o /tmp/demo.kicad_sch
    python3 scripts/refresh_py_to_sch.py --out-dir /tmp/scratch     # generate all into a folder
    python3 scripts/refresh_py_to_sch.py --list                     # show the project specs and exit

Outputs default to ``output/<project-slug>/<project-slug>.kicad_sch`` —
one folder per project, so every artifact of a project (schematic, ERC
report, netlist, SVG) stays together. Exit codes: 0 =
every project written clean, 1 = spec/tooling error, 2 = written but ERC
violations found with ``--export``.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "projects"
OUTPUT_DIR = ROOT / "output"

# The project specs do `from kicadgen import ...`; make the package
# importable without the caller having to set PYTHONPATH.
sys.path.insert(0, str(ROOT / "generator"))

from kicadgen.api import Design  # noqa: E402  (needs the sys.path tweak above)


def list_specs() -> list[Path]:
    """One entry spec per project folder: projects/<slug>/<slug>.py."""
    return sorted(
        p for p in PROJECTS_DIR.glob("*/*.py")
        if p.parent.name == p.stem and not p.stem.startswith("_")
    )


def resolve_spec(token: str) -> Path:
    """Match a slug ("kitchen_analyzer"), a file name or a path to a spec."""
    path = Path(token)
    if path.suffix != ".py":
        path = path.with_suffix(".py")
    candidates = [
        # inside projects/: one folder per project, spec named after it
        PROJECTS_DIR / path.stem / path.name,
        # legacy/flat layout: projects/<name>.py
        PROJECTS_DIR / path.name,
    ]
    if path.is_file():  # a full path to a spec file
        return path
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no project spec matches {token!r}; "
        f"see `python3 scripts/refresh_py_to_sch.py --list`"
    )


def extract_design(namespace: dict) -> Design | None:
    """Find the Design a spec built.

    Prefers a variable literally named ``design``; otherwise the last
    Design constructed while the file executed.
    """
    for candidate in (namespace.get("design"), *reversed(list(namespace.values()))):
        if isinstance(candidate, Design):
            return candidate
    return None


def run_spec(spec: Path, output: str, export: bool) -> int:
    location = spec.relative_to(ROOT) if spec.is_relative_to(ROOT) else spec
    print(f"== {location}")
    # One main spec per project; further design files may live next to it
    # and be imported by the main spec (e.g. kitchen_analyzer.py pulls in
    # the child-sheet design from lolin32_internal.py). Put the project
    # folder on sys.path for the run, then undo it afterwards.
    parent = str(spec.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        namespace = runpy.run_path(str(spec))
        design = extract_design(namespace)
    finally:
        if added:
            sys.path.remove(parent)
        # Drop sibling modules imported during this run so a later spec
        # never sees a stale, cached design.
        project_dir = Path(parent).resolve()
        for key, module in list(sys.modules.items()):
            module_file = getattr(module, "__file__", None)
            if module_file and Path(module_file).is_relative_to(project_dir):
                del sys.modules[key]
    if design is None:
        raise RuntimeError(
            f"{spec.name}: builds no Design; it must construct a "
            f"kicadgen.api.Design to generate"
        )
    return design.generate(output, export=export)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="refresh_py_to_sch.py",
        description="Run the generator against one or every project .py spec.",
    )
    parser.add_argument(
        "specs", nargs="*",
        help="project slug or .py path (e.g. kitchen_analyzer); "
             "empty means every projects/*/*.py",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="output .kicad_sch (single project only); default output/<slug>.kicad_sch",
    )
    parser.add_argument(
        "--out-dir", metavar="DIR", default=str(OUTPUT_DIR),
        help=f"folder for generated schematics (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--export", action="store_true",
        help="run kicad-cli ERC / netlist / SVG export afterwards",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the project specs and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        for spec in list_specs():
            print(spec.relative_to(ROOT))
        return 0

    try:
        specs = [resolve_spec(token) for token in args.specs] or list_specs()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output and len(specs) != 1:
        parser.error("-o/--output only works with exactly one project spec")

    failures = []
    for spec in specs:
        output = args.output or str(
            Path(args.out_dir) / spec.stem / f"{spec.stem}.kicad_sch"
        )
        try:
            rc = run_spec(spec, output, export=args.export)
        except Exception as exc:  # one bad spec must not block the others
            print(f"error: {spec.name}: {exc}", file=sys.stderr)
            rc = 1
        if rc != 0:
            failures.append(spec.name)

    if failures:
        print(f"failed: {', '.join(failures)}")
        return 1
    print(f"done: {len(specs)}/{len(specs)} project(s) ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
