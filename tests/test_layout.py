"""Unit tests for the layout engine (stub direction and stub collisions)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "generator"))

from kicadgen.symbols.custom import build
from kicadgen.drawing.layout import _body_rects, _stub_direction, place

from kicadgen.core.model import Circuit, Component, Connection, Net
from kicadgen.symbols.resolve import SymbolResolver

LIBS_AVAILABLE = os.path.isdir("/usr/share/kicad/symbols")


def _tall_symbol():
    """A 16-left/16-right-pin body like the LOLIN32 header symbol."""
    pins = []
    for i in range(16):
        pins.append({"number": f"L{i}", "name": f"IO{i}", "side": "left"})
        pins.append({"number": f"R{i}", "name": f"Q{i}", "side": "right"})
    return build(
        {"name": "Tall16", "reference_prefix": "U", "value": "Tall16",
         "pins": pins}
    )


class TestStubDirection(unittest.TestCase):
    def test_tall_symbol_body_rect_found(self):
        rects = _body_rects(_tall_symbol())
        self.assertEqual(len(rects), 1)

    def test_side_pins_stub_outwards_even_at_the_bottom(self):
        # Regression: on a tall body a bottom pin's vertical offset beats
        # the pin offset's dominant axis; the stub must still leave the
        # side face. The old behaviour dropped a pin's stub onto the
        # neighbouring pin, which shorted VBUS into VBAT and merged the
        # LCD control rows in the kitchen-analyzer schematic.
        symbol = _tall_symbol()
        rects = _body_rects(symbol)
        self.assertEqual(_stub_direction(symbol.pins["L0"], 0, rects), (-1, 0))
        self.assertEqual(_stub_direction(symbol.pins["L8"], 0, rects), (-1, 0))
        self.assertEqual(_stub_direction(symbol.pins["L15"], 0, rects), (-1, 0))
        self.assertEqual(_stub_direction(symbol.pins["R15"], 0, rects), (1, 0))

    @unittest.skipUnless(LIBS_AVAILABLE, "KiCad symbol libraries not installed")
    def test_bodyless_symbol_falls_back_to_pin_offset(self):
        # power:PWR_FLAG has no rectangle body and a centre pin; the
        # deterministic default stub points right.
        symbol = SymbolResolver().resolve("power:PWR_FLAG")
        self.assertEqual(_body_rects(symbol), [])
        self.assertEqual(_stub_direction(symbol.pins["1"], 0), (1, 0))


class TestRoutingCollisions(unittest.TestCase):
    def test_stub_ends_do_not_land_on_other_pins(self):
        symbol = _tall_symbol()
        resolved = {"U1": symbol, "U2": symbol}
        circuit = Circuit()
        circuit.components = [
            Component(ref="U1", symbol="x:Tall16"),
            Component(ref="U2", symbol="x:Tall16"),
        ]
        circuit.nets = [
            Net(name="A", connections=[
                Connection("U1", "L15"), Connection("U1", "L12"),
            ]),
            Net(name="B", connections=[
                Connection("U2", "R15"), Connection("U2", "R12"),
            ]),
        ]
        lay = place(circuit, resolved)
        placed = {
            tuple(pos)
            for pl in lay.placements.values()
            for pos in pl.pins.values()
        }
        for wire in lay.routing.wires:
            self.assertNotIn(tuple(wire[-1]), placed)


if __name__ == "__main__":
    unittest.main()
