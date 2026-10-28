"""Tests for scripts/refresh_py_to_sch.py — drives the generator over projects/*/*.py.

Only `kitchen_analyzer` remains in `projects/`, so every positive case
runs against it (parent schematic + `lolin32_internal` child sheet).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "refresh_py_to_sch.py")
PROJECTS = os.path.join(ROOT, "projects")
SLUG = "kitchen_analyzer"
SPEC = os.path.join(PROJECTS, SLUG, f"{SLUG}.py")


def run_tool(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestPyToSchList(unittest.TestCase):
    """Test `refresh_py_to_sch.py --list` shows every project spec."""

    def test_list_prints_entry_specs(self):
        result = run_tool("--list")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"{SLUG}/{SLUG}.py", result.stdout)

    def test_helper_files_are_not_listed(self):
        """Non-entry .py files next to a spec are not project specs."""
        result = run_tool("--list")
        self.assertNotIn("lolin32_internal.py", result.stdout)


class TestPyToSchSingle(unittest.TestCase):
    """Test `refresh_py_to_sch.py <spec>` generates one schematic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="py2sch-single-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = os.path.join(self.tmp, "kitchen.kicad_sch")

    def test_generates_by_slug(self):
        result = run_tool(SLUG, "-o", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.out))
        text = _read(self.out)
        self.assertIn("kicad_sch", text)
        self.assertIn("KitchenAnalyzer", text)
        # The child sheet is referenced as design.add_sheet("...", ...).
        self.assertIn("lolin32_internal.kicad_sch", text)

    def test_project_file_created_next_to_schematic(self):
        result = run_tool(SLUG, "-o", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            os.path.isfile(self.out[:-len(".kicad_sch")] + ".kicad_pro"),
        )

    def test_block_frames_and_headings(self):
        """mark_blocks() draws dashed frames + one heading text per block."""
        result = run_tool(SLUG, "-o", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        sch = _read(self.out)
        self.assertIn('(text "POWER"', sch)
        self.assertIn('(text "GAS / SMOKE"', sch)
        self.assertIn('(text "DISPLAY"', sch)

    def test_generates_by_path(self):
        result = run_tool(SPEC, "-o", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.out))

    def test_out_dir_mode_includes_child_sheet(self):
        """The project folder keeps both schematics and one .kicad_pro."""
        result = run_tool(SLUG, "--out-dir", self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = os.path.join(self.tmp, SLUG)
        self.assertTrue(
            os.path.isfile(os.path.join(folder, f"{SLUG}.kicad_sch")),
        )
        self.assertTrue(
            os.path.isfile(os.path.join(folder, "lolin32_internal.kicad_sch")),
        )
        # One KiCad project per folder, named for the root schematic; the
        # child sheet must not get a project file of its own.
        self.assertTrue(
            os.path.isfile(os.path.join(folder, f"{SLUG}.kicad_pro")),
        )
        self.assertEqual(
            [name for name in os.listdir(folder) if name.endswith(".kicad_pro")],
            [f"{SLUG}.kicad_pro"],
        )

    def test_unknown_spec_fails(self):
        result = run_tool("no_such_project")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no project spec", result.stderr)

    def test_output_with_multiple_specs_is_rejected(self):
        result = run_tool(SLUG, SPEC, "-o", self.out)
        self.assertNotEqual(result.returncode, 0)


class TestPyToSchAll(unittest.TestCase):
    """Test `refresh_py_to_sch.py` with no arguments runs every project spec."""

    def test_out_dir_generates_every_spec(self):
        tmp = tempfile.mkdtemp(prefix="py2sch-all-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = run_tool("--out-dir", tmp)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        name = os.path.join(SLUG, f"{SLUG}.kicad_sch")
        self.assertTrue(
            os.path.isfile(os.path.join(tmp, name)),
            f"missing schematic for {name}",
        )
        self.assertIn("done", result.stdout)

    def test_helper_files_in_project_folder_are_ignored(self):
        """Non-entry .py files next to a spec must not become projects."""
        helper = os.path.join(PROJECTS, SLUG, "kitchen_helpers.py")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write("# helper module, not a project spec\n")
        self.addCleanup(os.remove, helper)
        result = run_tool("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("kitchen_helpers.py", result.stdout)
        self.assertIn(f"{SLUG}.py", result.stdout)


@unittest.skipUnless(shutil.which("kicad-cli"), "kicad-cli not available")
class TestPyToSchExport(unittest.TestCase):
    """Test `refresh_py_to_sch.py <spec> --export` runs ERC / netlist / SVG.

    `kitchen_analyzer` dangles power flags and unterminated module pins on
    purpose, so the report ends with ERC violations instead of a clean rc.
    """

    def test_export_artifacts(self):
        tmp = tempfile.mkdtemp(prefix="py2sch-export-")
        rc_report = tempfile.mkdtemp(prefix="py2sch-rc-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rc_report, ignore_errors=True)
        out = os.path.join(tmp, "kitchen.kicad_sch")
        result = run_tool(SLUG, "-o", out, "--export")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERC", result.stdout)
        report = out[:-len(".kicad_sch")] + ".erc.rpt"
        self.assertTrue(os.path.isfile(report))
        net = out[:-len(".kicad_sch")] + ".net"
        self.assertTrue(os.path.isfile(net))
        svg_dir = out[:-len(".kicad_sch")] + "-svg"
        self.assertTrue(os.path.isdir(svg_dir))
        for text in ('(text "POWER"', '(text "DISPLAY"'):
            self.assertIn(text, _read(out))


if __name__ == "__main__":
    unittest.main()
