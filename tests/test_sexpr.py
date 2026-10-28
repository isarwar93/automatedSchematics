"""Unit tests for the S-expression reader/writer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen.core import sexpr


class TestSexpr(unittest.TestCase):
    def test_parse_simple(self):
        tree = sexpr.parse('(wire (pts (xy 1.27 2.54)) (name "net"))')
        self.assertEqual(str(tree[0]), "wire")  # single top-level node returned as-is
        self.assertEqual(str(tree[1][1][1]), "1.27")

    def test_parse_nested(self):
        tree = sexpr.parse("(a (b (d 2)))")
        self.assertEqual(str(tree[0]), "a")
        self.assertEqual(str(tree[1][1][1]), "2")

    def test_round_trip(self):
        text = '(label "UART_TX"\n\t(at 10 20 0)\n\t(justify left)\n)'
        tree = sexpr.parse(text)
        self.assertEqual(str(tree[1]), "UART_TX")
        out = sexpr.dump(tree)
        reparsed = sexpr.parse(out)
        self.assertEqual(str(reparsed[1]), "UART_TX")

    def test_dump_quotes_when_needed(self):
        out = sexpr.dump(["label", sexpr.Q("has space")])
        self.assertIn('"has space"', out)

    def test_dump_bare_atoms(self):
        out = sexpr.dump(["version", "20250114"])
        self.assertNotIn('"', out)

    def test_unbalanced(self):
        with self.assertRaises(ValueError):
            sexpr.parse("(a (b")

    def test_find_all(self):
        tree = sexpr.parse("(a (b (at 1 1)) (b (at 2 2)))")
        self.assertEqual(len(sexpr.find_all(tree, "b")), 2)


if __name__ == "__main__":
    unittest.main()
