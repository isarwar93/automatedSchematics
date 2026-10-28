#!/usr/bin/env python3
"""run_tests.py - run all or only selected tests from ``tests/``.

Purpose: run the unittest suite of this repository, all at once or module
by module (and single test cases), with automatic discovery, without
having to type the ``python3 -m unittest discover`` incantation. Exit
code 0 means every selected test passed, 1 means a failure or error.

How to start it (from anywhere, paths are resolved against the repo):

    python3 scripts/run_tests.py                          # run every test in tests/
    python3 scripts/run_tests.py test_model               # one module
    python3 scripts/run_tests.py test_model test_api      # several modules
    python3 scripts/run_tests.py model                    # 'test_' prefix optional
    python3 scripts/run_tests.py test_model.TestModel     # one test case
    python3 scripts/run_tests.py test_model.TestModel.test_component
                                                          # one method
    python3 scripts/run_tests.py --list                   # list modules and counts
    python3 scripts/run_tests.py -v test_sexpr            # verbose pass-through

The thin-script rule applies: this file only maps names to unittest
modules and drives ``unittest``; everything under test lives in
``generator/kicadgen`` and ``tests/``.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def test_modules() -> list[str]:
    """Stems of the runnable test modules: tests/test_*.py."""
    return sorted(path.stem for path in TESTS.glob("test_*.py"))


def normalize(name: str) -> str:
    """Accept ``model``, ``test_model`` and ``model.TestModel`` alike."""
    stem = name.partition(".")[0]
    if not stem.startswith("test_"):
        name = "test_" + name
    return name


def build_suite(selection: list[str]) -> unittest.TestSuite:
    """All discovered tests, or the selected modules / cases in order."""
    loader = unittest.TestLoader()
    if not selection:
        return loader.discover(str(TESTS))
    suite = unittest.TestSuite()
    for raw in selection:
        name = normalize(raw)
        stem = name.partition(".")[0]
        if not (TESTS / f"{stem}.py").is_file():
            known = ", ".join(test_modules())
            raise ValueError(
                f"unknown test module: {stem!r} (from {raw!r}); "
                f"available: {known}"
            )
        sys.path.insert(0, str(TESTS))
        try:
            importlib.import_module(stem)
            suite.addTests(loader.loadTestsFromName(name))
        finally:
            sys.path.remove(str(TESTS))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description="Run all or selected tests from tests/ (unittest).",
    )
    parser.add_argument(
        "names", nargs="*", metavar="TEST",
        help="test module stem (test_model, model) or dotted case/method "
             "name; empty runs everything",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="verbose unittest output",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list the test modules with case counts and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        loader = unittest.TestLoader()
        sys.path.insert(0, str(TESTS))
        for stem in test_modules():
            suite = loader.loadTestsFromName(stem)
            print(f"{stem} ({suite.countTestCases()} tests)")
        return 0

    try:
        suite = build_suite(args.names)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
