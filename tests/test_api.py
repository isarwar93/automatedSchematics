"""Unit tests for the Python-native specification API (kicadgen.api)."""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen import Component, Design, connect, custom_symbol, pin
from kicadgen.core.sexpr import parse


class TestDesign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kicadgen-api-")
        Design(name="api-test", paper="A4")  # resets the active design

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_graphic_lines_and_texts(self):
        """Design.line()/text() become polyline/text nodes in the output."""
        d = Design(name="graphics-test", paper="A4")
        d.line(10, 20, 10, 60, width=0.1524)
        d.text(50, 30, "TEST HEADING")
        out = os.path.join(self.tmp, "graphics.kicad_sch")
        d.generate(out)
        with open(out, encoding="utf-8") as fh:
            tree = parse(fh.read())
        polylines = [n for n in tree if isinstance(n, list) and n and n[0] == "polyline"]
        texts = [n for n in tree if isinstance(n, list) and n and n[0] == "text"]
        self.assertEqual(len(polylines), 1)
        self.assertEqual(len(texts), 1)
        self.assertIn("TEST HEADING", str(texts[0]))

    def test_mark_blocks_in_marking(self):
        d = Design(name="frames", paper="A4")
        d.mark_blocks({"power": "SUPPLY"})
        self.assertTrue(d.block_frames)
        self.assertEqual(d.block_headings, {"power": "SUPPLY"})
        circuit = d.to_circuit()
        self.assertTrue(circuit.block_frames)
        self.assertEqual(circuit.block_headings["power"], "SUPPLY")

    def test_named_nets_and_pin_lookup(self):
        d = Design(name="nets")
        r1 = Component(ref="R1", symbol="Device:R")
        u1 = Component(ref="U1", symbol="Device:D")
        d.add(r1, u1)
        connect(r1.pin(2), u1.pin("A"), name="SIGNAL")
        connect(u1.pin("K"), r1.pin(1), name="RETURN")
        connect(u1.pin("K"), r1.pin(1), name="RETURN")  # merge, no duplicates
        circuit = d.to_circuit()
        names = [n.name for n in circuit.nets]
        self.assertEqual(names, ["SIGNAL", "RETURN"])
        by_name = {n.name: n for n in circuit.nets}
        self.assertEqual(len(by_name["SIGNAL"].connections), 2)
        self.assertEqual(len(by_name["RETURN"].connections), 2)
        self.assertEqual(
            {(c.component, c.pin) for c in by_name["RETURN"].connections},
            {("U1", "1"), ("R1", "1")},  # K = 1 on Device:D
        )

    def test_auto_net_names(self):
        d = Design(name="auto")
        r1 = Component(ref="R1", symbol="Device:R")
        r2 = Component(ref="R2", symbol="Device:R")
        d.add(r1, r2)
        connect(r1.pin(1), r2.pin(1))
        connect(r1.pin(2), r2.pin(2))
        circuit = d.to_circuit()
        self.assertEqual([n.name for n in circuit.nets], ["N$1", "N$2"])

    def test_attribute_pin_access(self):
        d = Design(name="attrs")
        u1 = Component(ref="U1", symbol="Device:D")
        d.add(u1)
        handle = u1.A
        self.assertEqual((handle.component.ref, handle.number), ("U1", "2"))
        bare = u1.pin(2)
        self.assertEqual(bare.number, "2")
        self.assertEqual(u1.pin("K").number, "1")

    def test_unknown_pin_raises(self):
        d = Design(name="badpin")
        r1 = Component(ref="R1", symbol="Device:R")
        d.add(r1)
        with self.assertRaises(ValueError):
            r1.pin(99)
        with self.assertRaises(AttributeError):
            _ = r1.GPIO99
        with self.assertRaises(ValueError):
            connect(r1.pin(1), r1.pin(99))

    def test_custom_symbol_lib_id(self):
        head = custom_symbol(
            "Test_Board",
            pins=[pin("3V3", etype="power_in", side="left"),
                  pin("SCL", etype="output", side="right")],
            prefix="U",
            value="Test board",
        )
        d = Design(name="custom")
        u1 = Component(ref="U1", symbol=head)
        d.add(u1)
        defs = d.custom_defs()
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0].lib_id, "Custom:Test_Board")
        self.assertEqual(defs[0].pins["3V3"].etype, "power_in")

    def test_generate_writes_schematic(self):
        head = custom_symbol(
            "Test_Board",
            nickname="TestLib",
            pins=[pin("3V3", etype="power_in", side="left"),
                  pin("IO0", etype="bidirectional", side="right")],
            prefix="U",
            value="Test board",
        )
        d = Design(name="api-gen")
        u1 = Component(ref="U1", symbol=head)
        j1 = Component(ref="J1", symbol="Connector_Generic:Conn_01x02")
        d.add(u1, j1)
        connect(u1.pin("3V3"), j1.pin(1), name="RAIL")
        out = os.path.join(self.tmp, "gen.kicad_sch")
        rc = d.generate(out)
        self.assertEqual(rc, 0)
        text = open(out, encoding="utf-8").read()
        tree = parse(text)
        self.assertEqual(str(tree[0]), "kicad_sch")
        libs = [n for n in tree if isinstance(n, list) and str(n[0]) == "lib_symbols"]
        embedded = {str(n[1]) for n in libs[0]
                    if isinstance(n, list) and str(n[0]) == "symbol"}
        self.assertIn("TestLib:Test_Board", embedded)
        self.assertIn("Connector_Generic:Conn_01x02", embedded)
        self.assertIn('(paper "A4")', text)
        nets = re.findall(r'\(label "(RAIL)"', text)
        self.assertTrue(nets)
        self.assertFalse(os.path.exists(out.replace(".kicad_sch", ".erc.rpt")))

    def test_connect_before_design_fails(self):
        import kicadgen.api as api
        old = api._active_design
        api._active_design = None
        try:
            with self.assertRaises(RuntimeError):
                connect()  # nothing wired anywhere
        finally:
            api._active_design = old

    def test_bad_symbol_id(self):
        with self.assertRaises(ValueError):
            Component(ref="X1", symbol="Device_R")  # missing library nickname


if __name__ == "__main__":
    unittest.main()
