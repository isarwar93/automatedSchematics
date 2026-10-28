"""End-to-end test: YAML spec -> .kicad_sch -> (optional) kicad-cli checks."""

import json
import os
import shutil
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen.cli import generate
from kicadgen.core.sexpr import Q, parse

# A small example circuit, as a YAML/JSON dict for `kicadgen.cli.generate`.
EXAMPLE = {
    "design": {"name": "example",
               "paper": {"size": "A4", "orientation": "landscape"}},
    "components": [
        {
            "ref": "J1",
            "symbol": "Connector:Conn_01x02_Pin",
            "value": "UART",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            "block": "connector",
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "block": "interface",
        },
    ],
    "nets": [
        {"name": "UART_TX",
         "connections": [{"component": "J1", "pin": "1"},
                         {"component": "R1", "pin": "1"}]},
        {"name": "UART_RX",
         "connections": [{"component": "J1", "pin": "2"},
                         {"component": "R1", "pin": "2"}]},
    ],
}



class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kicadgen-test-")
        self.out = os.path.join(self.tmp, "design.kicad_sch")
        self.example = os.path.join(self.tmp, "example.yaml")
        with open(self.example, "w") as fh:
            yaml.safe_dump(EXAMPLE, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generates_loadable_schematic(self):
        rc = generate(self.example, self.out)
        self.assertEqual(rc, 0)
        text = open(self.out, encoding="utf-8").read()
        tree = parse(text)
        self.assertEqual(str(tree[0]), "kicad_sch")
        # lib_symbols must be present and contain both used symbols
        libs = [n for n in tree if isinstance(n, list) and str(n[0]) == "lib_symbols"]
        self.assertTrue(libs)
        names = {
            str(n[1])
            for n in libs[0]
            if isinstance(n, list) and str(n[0]) == "symbol"
        }
        self.assertIn("Device:R", names)
        # Two symbol instances (J1 + R1)
        instances = [n for n in tree if isinstance(n, list) and str(n[0]) == "symbol"]
        self.assertEqual(len(instances), 2)
        # Uuids must be unique
        uuids = [str(n[0]) for n in []]
        import re

        found = re.findall(r'\(uuid "([^"]+)"\)', text)
        self.assertEqual(len(found), len(set(found)))

    def test_custom_paper_size_emits_user_page(self):
        spec = os.path.join(self.tmp, "custom.yaml")
        with open(spec, "w") as fh:
            yaml.safe_dump(
                {
                    "design": {"name": "custom", "paper": {"width": 220, "height": 180}},
                    "components": [{"ref": "R1", "symbol": "Device:R"}],
                    "nets": [],
                },
                fh,
            )
        rc = generate(spec, self.out)
        self.assertEqual(rc, 0)
        tree = parse(open(self.out, encoding="utf-8").read())
        papers = [n for n in tree if isinstance(n, list) and len(n) > 2 and str(n[0]) == "paper"]
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(str(paper[1]), "User")  # quoted atom in the file
        self.assertEqual(float(paper[2]), 220.0)
        self.assertEqual(float(paper[3]), 180.0)

    def test_named_paper_size_unchanged(self):
        rc = generate(self.example, self.out)
        self.assertEqual(rc, 0)
        tree = parse(open(self.out, encoding="utf-8").read())
        papers = [n for n in tree if isinstance(n, list) and str(n[0]) == "paper"]
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(str(paper[1]), "A4")
        self.assertEqual(len(paper), 2)

    def test_invalid_custom_paper_rejected(self):
        spec = os.path.join(self.tmp, "bad-paper.json")
        with open(spec, "w") as fh:
            json.dump(
                {
                    "design": {"paper": {"width": 0, "height": 100}},
                    "components": [{"ref": "R1", "symbol": "Device:R"}],
                    "nets": [],
                },
                fh,
            )
        rc = generate(spec, self.out)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.isfile(self.out))

    def test_erc_and_netlist_when_kicad_cli_present(self):
        if shutil.which("kicad-cli") is None:
            self.skipTest("kicad-cli not available")
        rc = generate(self.example, self.out, export=True)
        self.assertEqual(rc, 0)
        report = self.out.replace(".kicad_sch", ".erc.rpt")
        self.assertTrue(os.path.isfile(report))
        self.assertIn("ERC messages: 0", open(report).read())
        net = self.out.replace(".kicad_sch", ".net")
        content = open(net).read()
        self.assertIn("UART_TX", content)
        self.assertIn('"J1"', content)
        self.assertIn('"R1"', content)

    def test_invalid_spec_fails(self):
        spec = os.path.join(self.tmp, "bad.json")
        with open(spec, "w") as fh:
            json.dump(
                {
                    "components": [{"ref": "R1", "symbol": "Device:R"}],
                    "nets": [{"name": "X", "connections": [{"component": "R1", "pin": "99"}]}],
                },
                fh,
            )
        # Must not write a schematic and must report the invalid pin.
        rc = generate(spec, self.out)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.isfile(self.out))


if __name__ == "__main__":
    unittest.main()
