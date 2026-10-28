"""kicadgen - Python-based KiCad schematic generator.

Converts a YAML/JSON *or pure-Python* circuit specification into a native,
editable ``.kicad_sch`` file, then validates it with ``kicad-cli``.

Pipeline (one folder per context under the package):

    spec (core/model.py | api.py)
         -> symbols (symbols/resolve.py, symbols/custom.py)
         -> drawing (drawing/layout.py)
         -> writer  (drawing/writer.py)
         -> checks  (export/validate.py)
    driven by cli.py, exposed to project specs via api.py

Python API::

    from kicadgen import Design, Component, connect

    design = Design(name="demo", paper="A4")
    r1 = Component(ref="R1", symbol="Device:R")
    design.add(r1)
    connect(r1.pin(1), r1.pin(2))
    design.generate("output/demo.kicad_sch")
"""

from kicadgen.core.model import Circuit, Connection, Net
from kicadgen.core.model import Component as ModelComponent
from .api import Component, Design, PinHandle, connect, custom_symbol, pin

__all__ = [
    "Circuit",
    "Component",
    "ModelComponent",
    "Connection",
    "Net",
    "Design",
    "PinHandle",
    "connect",
    "custom_symbol",
    "pin",
]
__version__ = "0.2.0"
