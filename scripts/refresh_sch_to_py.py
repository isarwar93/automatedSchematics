#!/usr/bin/env python3
"""refresh_sch_to_py.py - rebuild project .py specs from the generated
schematics (thin CLI).

Companion of ``refresh_py_to_sch.py``: forward goes python -> schematic,
this script goes the other way:

    python3 scripts/refresh_sch_to_py.py kitchen_analyzer

reads ``output/<slug>/<slug>.kicad_sch`` (plus every sub-sheet file beside
it) and rewrites the project's Python spec files from them. Each
overwritten spec is backed up first, to
``output/<slug>/backups/<name>-<timestamp>.py.bak``; only the newest 3
backups are kept (``--backup-dir`` moves the folder).

Scripts stay thin: every reconstruction rule lives in the generator
package, see ``generator/kicadgen/reverse.py`` (parse + rebuild + module
rendering, backup storage + pruning, and its module docstring for the
exact recovery rules and the documented fidelity losses). Exit codes:
0 = specs written, 1 = error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "projects"
OUTPUT_DIR = ROOT / "output"

sys.path.insert(0, str(ROOT / "generator"))

from kicadgen.reverse import (  # noqa: E402
    BACKUP_KEEP,
    prune_backups,
    rebuild_project,
    store_backup,
)


def list_schematics() -> list[Path]:
    """One generated root schematic per project: output/<slug>/<slug>.kicad_sch."""
    return sorted(
        p for p in OUTPUT_DIR.glob("*/*.kicad_sch")
        if p.parent.name == p.stem and not p.stem.startswith("_")
    )


def resolve_schematic(token: str) -> Path:
    """Match a slug, file name or path to a generated root schematic."""
    path = Path(token)
    if path.suffix != ".kicad_sch":
        path = path.with_suffix(".kicad_sch")
    if path.is_file():
        return path
    for candidate in (OUTPUT_DIR / path.stem / path.name,
                      OUTPUT_DIR / path.name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no generated schematic matches {token!r}; generate one with "
        f"scripts/refresh_py_to_sch.py first, or see "
        f"`python3 scripts/refresh_sch_to_py.py --list`"
    )


def write_back(project_dir: Path, modules: list[tuple[str, str]],
               backup_dir: Path) -> list[Path]:
    """Write the rebuilt modules; overwritten ones go to ``backup_dir``."""
    project_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, source in modules:
        target = project_dir / f"{stem}.py"
        if store_backup(target, backup_dir) is not None:
            prune_backups(backup_dir)
        target.write_text(source, encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="refresh_sch_to_py.py",
        description="Rebuild project .py specs from the generated schematics.",
    )
    parser.add_argument(
        "project", nargs="?", metavar="SLUG|FILE",
        help="project slug or generated .kicad_sch path; empty lists choices",
    )
    parser.add_argument(
        "--sch", metavar="FILE",
        help="root schematic to read (default: output/<slug>/<slug>.kicad_sch)",
    )
    parser.add_argument(
        "--project-dir", metavar="DIR",
        help="folder for the rebuilt .py files (default: projects/<slug>)",
    )
    parser.add_argument(
        "--backup-dir", metavar="DIR",
        help="folder for .py backups "
             f"(default: output/<slug>/backups, last {BACKUP_KEEP} kept)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list the generated schematics and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        for schematic in list_schematics():
            print(schematic.relative_to(ROOT))
        return 0
    if not args.project and not args.sch:
        parser.error("pass a project slug (or --sch FILE); see --list")

    try:
        if args.sch:
            schematic = Path(args.sch)
            if not schematic.is_file():
                raise FileNotFoundError(f"no such schematic: {args.sch!r}")
            slug = args.project or schematic.parent.name
        else:
            schematic = resolve_schematic(args.project)
            slug = args.project
            if Path(args.project).suffix == ".kicad_sch":
                slug = schematic.stem
        project_dir = (Path(args.project_dir) if args.project_dir
                       else PROJECTS_DIR / slug)
        backup_dir = (Path(args.backup_dir) if args.backup_dir
                      else OUTPUT_DIR / slug / "backups")
        modules = rebuild_project(schematic)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    written = write_back(project_dir, modules, backup_dir)
    for path in written:
        location = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"wrote {location}")
    backups = sorted(backup_dir.glob("*.bak"))
    if backups:
        latest = backups[-1].relative_to(ROOT) \
            if backups[-1].is_relative_to(ROOT) else backups[-1]
        keep = min(len(backups), BACKUP_KEEP)
        print(f"backups: {keep} kept in {backup_dir} (latest: {latest})")
    print(f"done: {len(written)} module(s) rebuilt from "
          f"{schematic.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
