"""Tests for scripts/refresh_sch_to_py.py — the reverse pass (sch to py).

The tool is a thin CLI over generator/kicadgen/reverse.py; these tests
exercise it end to end:
- rebuild the kitchen_analyzer specs into a scratch folder,
- regenerate schematics from the rebuilt specs (forward pass) and compare
  wire / symbol / label profiles against the committed baselines,
- spec backups under output/<slug>/backups (last 3 kept), --list,
  error handling.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

SCRIPT = os.path.join(ROOT, "scripts", "refresh_sch_to_py.py")
FORWARD = os.path.join(ROOT, "scripts", "refresh_py_to_sch.py")
SLUG = "kitchen_analyzer"
BASELINE = os.path.join(ROOT, "output", SLUG, f"{SLUG}.kicad_sch")

from kicadgen.reverse import BACKUP_KEEP, prune_backups  # noqa: E402


def run_tool(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def run_forward(*args):
    return subprocess.run(
        [sys.executable, FORWARD, *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def profile(path: str) -> tuple[Counter, int, int]:
    """Label histogram plus the wire and symbol counts of a schematic."""
    text = _read(path)
    labels = Counter(re.findall(
        r'^\t\((?:hierarchical_)?label "([^"]+)"', text, re.M))
    wires = len(re.findall(r'^\t\(wire', text, re.M))
    symbols = len(re.findall(r'^\t\(symbol\n', text, re.M))
    return labels, wires, symbols


class TestSchToPyList(unittest.TestCase):
    """Test `refresh_sch_to_py.py --list` shows the generated schematics."""

    def test_list_prints_root_schematics(self):
        result = run_tool("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"output/{SLUG}/{SLUG}.kicad_sch", result.stdout)

    def test_list_ignores_child_sheets(self):
        """Only folder-named roots listed, not the sub-sheet files."""
        result = run_tool("--list")
        self.assertNotIn("lolin32_internal.kicad_sch", result.stdout)


class TestSchToPyRebuild(unittest.TestCase):
    """Test `refresh_sch_to_py.py <slug>` rebuilds the project specs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sch2py-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project_dir = os.path.join(self.tmp, "specs")
        self.backup_dir = os.path.join(self.tmp, "backups")

    def test_writes_entry_and_child_modules(self):
        result = run_tool(SLUG, "--project-dir", self.project_dir,
                          "--backup-dir", self.backup_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = os.path.join(self.project_dir, f"{SLUG}.py")
        child = os.path.join(self.project_dir, "lolin32_internal.py")
        self.assertTrue(os.path.isfile(entry))
        self.assertTrue(os.path.isfile(child))
        entry_text = _read(entry)
        # Reconstructed specs use the same public API.
        self.assertIn("Design(name=", entry_text)
        self.assertIn("connect(", entry_text)
        self.assertIn("Component(", entry_text)
        self.assertIn("custom_symbol(", entry_text)
        # Children come first as sibling modules the entry imports.
        self.assertIn("from lolin32_internal import design",
                      entry_text)
        self.assertIn("design.add_sheet(", entry_text)

    def test_rebuilt_specs_compile(self):
        result = run_tool(SLUG, "--project-dir", self.project_dir,
                          "--backup-dir", self.backup_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        compile(_read(os.path.join(self.project_dir, f"{SLUG}.py")),
                f"{SLUG}.py", "exec")
        compile(_read(os.path.join(self.project_dir, "lolin32_internal.py")),
                "lolin32_internal.py", "exec")

    def test_backup_created_for_replaced_specs(self):
        """First run writes fresh files; a second run backs them up."""
        result = run_tool(SLUG, "--project-dir", self.project_dir,
                          "--backup-dir", self.backup_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.isdir(self.backup_dir),
                         "no backups expected on the first run")

        result2 = run_tool(SLUG, "--project-dir", self.project_dir,
                           "--backup-dir", self.backup_dir)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        # One timestamped .bak per overwritten spec, outside the projects
        # folder: backups live under output/ (or --backup-dir).
        backups = os.listdir(self.backup_dir)
        self.assertEqual(len(backups), 2)
        for name in backups:
            self.assertRegex(
                name,
                r"^(" + re.escape(f"{SLUG}") + r"|lolin32_internal)"
                r"-\d{8}-\d{6}\.py\.bak$")
        self.assertIn("backups: 2 kept", result2.stdout)

    def test_backup_filename_carries_spec_name_and_timestamp(self):
        run_tool(SLUG, "--project-dir", self.project_dir,
                 "--backup-dir", self.backup_dir)
        run_tool(SLUG, "--project-dir", self.project_dir,
                 "--backup-dir", self.backup_dir)
        year = time.strftime("%Y")
        for stem in (SLUG, "lolin32_internal"):
            matches = [name for name in os.listdir(self.backup_dir)
                       if re.match(rf"^{re.escape(stem)}-{year}", name)]
            self.assertEqual(len(matches), 1,
                             f"one backup expected for {stem}")


class TestBackupRetention(unittest.TestCase):
    """`prune_backups()` keeps only the newest BACKUP_KEEP *.bak files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sch2py-keep-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _make(self, count, base):
        """count fake .bak files with staggered mtimes."""
        for i in range(count):
            path = os.path.join(self.tmp, f"{base}-{i:02d}-x.py.bak")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("old spec\n")
            os.utime(path, (time.time() + i, time.time() + i))

    def test_keeps_last_three(self):
        self._make(5, "kitchen_analyzer")
        removed = prune_backups(self.tmp)
        self.assertEqual({path.name for path in removed}, {
            "kitchen_analyzer-00-x.py.bak",
            "kitchen_analyzer-01-x.py.bak"})
        remaining = sorted(os.listdir(self.tmp))
        self.assertEqual(remaining, [
            "kitchen_analyzer-02-x.py.bak",
            "kitchen_analyzer-03-x.py.bak",
            "kitchen_analyzer-04-x.py.bak"])

    def test_keeps_backup_keep_by_default(self):
        self._make(6, "spec")
        prune_backups(self.tmp)
        self.assertEqual(len(os.listdir(self.tmp)), BACKUP_KEEP)

    def test_no_zero_keep(self):
        with self.assertRaises(ValueError):
            prune_backups(self.tmp, keep=0)

    def test_missing_dir_is_not_an_error(self):
        self.assertEqual(
            prune_backups(os.path.join(self.tmp, "absent")), [])


class TestSchToPyRoundTrip(unittest.TestCase):
    """Reverse then forward must reproduce the committed baselines."""

    def test_regen_from_rebuilt_specs_matches_baseline(self):
        specs = tempfile.mkdtemp(prefix="sch2py-specs-")
        out = tempfile.mkdtemp(prefix="sch2py-out-")
        self.addCleanup(shutil.rmtree, specs, ignore_errors=True)
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)

        result = run_tool(SLUG, "--project-dir", specs)
        self.assertEqual(result.returncode, 0, result.stderr)

        entry = os.path.join(specs, f"{SLUG}.py")
        result = run_forward(entry, "--out-dir", out)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        regenerated = os.path.join(out, SLUG)
        for name in (f"{SLUG}.kicad_sch", "lolin32_internal.kicad_sch"):
            original = os.path.join(ROOT, "output", SLUG, name)
            new = os.path.join(regenerated, name)
            la, wa, sa = profile(original)
            lb, wb, sb = profile(new)
            self.assertEqual((wa, sa), (wb, sb),
                             f"{name}: wire/symbol counts differ")
            self.assertEqual(sum(la.values()), sum(lb.values()),
                             f"{name}: label count differs")
            self.assertEqual(la - lb, Counter(), f"{name}: labels lost")
            self.assertEqual(lb - la, Counter(), f"{name}: labels gained")


class TestSchToPyErrors(unittest.TestCase):
    """Error paths exit 1 with a message, not a traceback."""

    def test_unknown_slug_fails(self):
        result = run_tool("no_such_project")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no generated schematic matches", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_sch_file_fails(self):
        tmp = tempfile.mkdtemp(prefix="sch2py-err-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = run_tool("--sch", os.path.join(tmp, "absent.kicad_sch"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such schematic", result.stderr)


if __name__ == "__main__":
    unittest.main()
