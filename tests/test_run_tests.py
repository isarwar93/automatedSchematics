"""Tests for scripts/run_tests.py — the thin unittest runner script.

Only the cheap paths are covered here (never the full suite): --list,
single-module selection and the unknown-name error. The full suite is
what `python3 scripts/run_tests.py` is for.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "run_tests.py")


def run_tool(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, cwd=ROOT,
    )


class TestRunTests(unittest.TestCase):
    """Test the runner CLI: list, select, and error handling."""

    def test_list_shows_modules_with_counts(self):
        result = run_tool("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("test_model (", result.stdout)
        self.assertIn("test_sexpr (", result.stdout)

    def test_runs_one_selected_module(self):
        result = run_tool("test_sexpr")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # unittest reports on stderr by default.
        self.assertIn("Ran 7 tests", result.stderr)

    def test_stem_without_test_prefix_is_accepted(self):
        result = run_tool("sexpr")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Ran 7 tests", result.stderr)

    def test_runs_one_selected_test_case(self):
        """Dotted case selection: only that case runs."""
        result = run_tool("test_model.TestModel")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Ran 7 tests", result.stderr)

    def test_unknown_module_fails_with_available_list(self):
        result = run_tool("test_nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown test module", result.stderr)
        self.assertIn("available:", result.stderr)

    def test_no_args_flagged_in_help_only(self):
        """Empty selection runs everything; do not run the suite here."""
        help_text = run_tool("--help")
        self.assertEqual(help_text.returncode, 0)
        self.assertIn("empty runs everything", help_text.stdout)


if __name__ == "__main__":
    unittest.main()
