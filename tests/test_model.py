"""Unit tests for the circuit model."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen.core.model import Component, Connection, Net, circuit_from_dict


DATA = {
    "design": {"name": "demo", "paper": {"size": "A4", "orientation": "landscape"}},
    "components": [
        {"ref": "R1", "symbol": "Device:R", "value": "10k",
         "footprint": "Resistor_SMD:R_0603_1608Metric"},
        {"ref": "J1", "symbol": "Connector:Conn_01x02_Pin", "value": "UART"},
    ],
    "nets": [
        {"name": "UART_TX", "connections": [
            {"component": "J1", "pin": "1"},
            {"component": "R1", "pin": "1"},
        ]},
        {"name": "UART_RX", "connections": [{"component": "J1", "pin": "2"}]},
    ],
}


class TestModel(unittest.TestCase):
    def test_circuit_from_dict(self):
        circuit = circuit_from_dict(DATA)
        self.assertEqual(circuit.design.name, "demo")
        self.assertEqual(circuit.design.paper, "A4")
        self.assertFalse(circuit.design.is_custom_size())
        self.assertEqual(len(circuit.components), 2)
        self.assertEqual(len(circuit.nets), 2)
        self.assertEqual(circuit.component("R1").value, "10k")

    def test_custom_paper_size(self):
        circuit = circuit_from_dict(
            {"design": {"name": "t", "paper": {"width": 220, "height": 180}},
             "components": [], "nets": []}
        )
        self.assertEqual(circuit.design.paper, "User")
        self.assertEqual(circuit.design.width, 220.0)
        self.assertEqual(circuit.design.height, 180.0)
        self.assertTrue(circuit.design.is_custom_size())

    def test_custom_paper_with_named_size_key(self):
        circuit = circuit_from_dict(
            {"design": {"name": "t",
                        "paper": {"size": "custom", "width": 200, "height": 250}},
             "components": [], "nets": []}
        )
        self.assertEqual(circuit.design.paper, "User")
        self.assertEqual(circuit.design.orientation, "landscape")
        self.assertEqual(circuit.design.width, 200.0)
        self.assertEqual(circuit.design.height, 250.0)

    def test_paper_as_plain_string(self):
        circuit = circuit_from_dict(
            {"design": {"name": "t", "paper": "A3"}, "components": [], "nets": []}
        )
        self.assertEqual(circuit.design.paper, "A3")
        self.assertEqual(circuit.design.orientation, "landscape")
        self.assertFalse(circuit.design.is_custom_size())

    def test_non_numeric_custom_paper_rejected(self):
        with self.assertRaises(ValueError):
            circuit_from_dict(
                {"design": {"name": "t", "paper": {"width": "wide", "height": 180}},
                 "components": [], "nets": []}
            )

    def test_pins_connected(self):
        circuit = circuit_from_dict(DATA)
        self.assertEqual(circuit.pins_connected("J1"), {"1", "2"})
        self.assertEqual(circuit.pins_connected("R1"), {"1"})

    def test_connections_are_strings(self):
        circuit = circuit_from_dict(DATA)
        conn = circuit.nets[0].connections[0]
        self.assertIsInstance(conn.pin, str)


if __name__ == "__main__":
    unittest.main()
