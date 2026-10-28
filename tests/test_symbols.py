"""Unit tests for the symbol resolver (requires KiCad symbol libraries)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen.symbols.resolve import SymbolResolver

LIBS_AVAILABLE = os.path.isdir("/usr/share/kicad/symbols")


@unittest.skipUnless(LIBS_AVAILABLE, "KiCad symbol libraries not installed")
class TestSymbolResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = SymbolResolver()

    def test_simple_symbol(self):
        definition = self.resolver.resolve("Device:R")
        self.assertEqual(definition.lib_id, "Device:R")
        self.assertEqual(sorted(definition.pins), ["1", "2"])
        pin1 = definition.pins["1"]
        self.assertEqual((pin1.x, pin1.y), (0.0, 3.81))

    def test_embedded_subtree_names(self):
        definition = self.resolver.resolve("Connector:Conn_01x02_Pin")
        # The outer symbol must carry the full Library:Name for lib_symbols.
        self.assertEqual(str(definition.subtree[1]), "Connector:Conn_01x02_Pin")
        unit_names = [
            str(sub[1])
            for sub in definition.subtree
            if isinstance(sub, list) and str(sub[0]) == "symbol"
        ]
        self.assertIn("Conn_01x02_Pin_1_1", unit_names)
        # Library-level noise must not be embedded.
        self.assertNotIn("embedded_fonts", [str(sub[0]) for sub in definition.subtree])

    def test_derived_symbol_flattens(self):
        definition = self.resolver.resolve("Amplifier_Operational:LM358")
        self.assertGreaterEqual(definition.units, 3)
        self.assertIn("1", definition.pins)
        self.assertIn("8", definition.pins)

    def test_power_flag_common_block_pin(self):
        # PWR_FLAG keeps its pin in the common "PWR_FLAG_0_0" block, not
        # a "<name>_1_1" unit block; the resolver must still find it.
        definition = self.resolver.resolve("power:PWR_FLAG")
        self.assertEqual(sorted(definition.pins), ["1"])
        self.assertEqual(definition.pins["1"].etype, "power_out")
        self.assertEqual(definition.units, 1)

    def test_unknown_symbol(self):
        with self.assertRaises((KeyError, FileNotFoundError)):
            self.resolver.resolve("Device:Not_A_Symbol")

    def test_bad_lib_id(self):
        with self.assertRaises(ValueError):
            self.resolver.resolve("Device")


if __name__ == "__main__":
    unittest.main()
