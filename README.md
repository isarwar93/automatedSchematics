# kicadgen

Python-based KiCad schematic generator. A pure-Python circuit spec in
`projects/<slug>/<slug>.py` describes the design; `scripts/refresh_py_to_sch.py` loads
it and drives the generator, which writes a native, editable KiCad
`.kicad_sch` file and validates it with `kicad-cli` (ERC, netlist, SVG).
See `.agent/BUILD_PLAN.md` for the design.

## Layout

```
generator/kicadgen/
├── core/           # circuit model + S-expression primitives
│   ├── model.py    # circuit model: components, pins, nets (connectivity only)
│   └── sexpr.py    # S-expression parser/serializer shared by all modules
├── symbols/        # symbol handling
│   ├── resolve.py  # resolves symbols + pins from installed KiCad libraries
│   └── custom.py   # synthesizes project custom symbols from the spec
├── drawing/        # placement, routing, file output
│   ├── layout.py   # placement (functional-block columns) + routing (stubs + labels)
│   └── writer.py   # native .kicad_sch writer + .kicad_pro project file
├── export/
│   └── validate.py # spec checks, ERC / netlist / SVG via kicad-cli
├── api.py          # spec-facing API (Design, Component, connect, ...)
└── cli.py          # command-line entry point, ties the pipeline together
projects/        # one folder per project — pure-Python specs, `from kicadgen import ...`
scripts/refresh_py_to_sch.py# runs the generator against projects/<slug>/<slug>.py
tests/           # unittest suite
```

## KiCad project layout

```
KICAD PROJECT
(.kicad_pro)
      |
  +---+-----------+
  |           |
  v           v
SCHEMATIC     PCB
(.kicad_sch)  (.kicad_pcb)
  |             |
  v             v
SYMBOL        FOOTPRINT
  |             |
  v             v
PINS          PADS
  |             |
  v             v
NETS          TRACKS
  |             |
  +-----+-------+
        |
        v
  PHYSICAL CIRCUIT
```

## Usage

Runs the generator against every project spec in `projects/` (one folder
per project, entry spec `projects/<slug>/<slug>.py`):

```
python3 scripts/refresh_py_to_sch.py
```

One project, plus `kicad-cli` ERC / netlist / SVG export:

```
python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export
```

`--export` runs `kicad-cli sch erc` / `netlist` / `svg` and reports the
resulting net names and violations. Exit codes: 0 = clean, 1 = spec or
tooling error, 2 = ERC violations found.

The reverse pass goes the other way: `refresh_sch_to_py.py` reads a
generated schematic hierarchy and rebuilds the project's Python specs from
it (each overwritten spec backed up under `output/<slug>/backups/`, last
3 kept):

```
python3 scripts/refresh_sch_to_py.py kitchen_analyzer
```

The generated file opens as-is in KiCad (tested against KiCad 9.0.8); all
objects carry fresh UUIDs. The generator also writes a `.kicad_pro`
project file next to each root schematic — one KiCad project per output
folder, so a PCB layout (`.kicad_pcb`) can join the schematic later.

## Spec format

Project specs are pure Python (see `projects/kitchen_analyzer/` for a full
example). Each project gets its own folder under `projects/` — the entry
spec is named after the folder (`projects/<slug>/<slug>.py`), and any extra
Python files for the same project (helpers, shared parts) live alongside
it; the script only picks up the entry spec. Declare components with
`Component`, expose their pins as attributes (`mcu.GPIO13`) or with
`.pin(n)`, and join pins into named nets with `connect(...)`; the layout
engine decides where everything appears on the page. Symbols that no
library file covers (boards, breakout modules) are declared with
`custom_symbol`. Components accept an optional `block` field (`power`,
`controller`, `interface`, `connector`) for column ordering.

Sheet annotations: `design.line(x1, y1, x2, y2, width=0.254, style="dash")`
draws a plain graphical line (stroke styles: solid, dash, long_dash, dot,
dash_dot, ...) and `design.text(x, y, "LABEL")` draws a bold text (both at
absolute sheet mm coordinates, no electrical role).
`design.mark_blocks({"power": "POWER", ...})` instead lets the layout
engine draw the annotation in the style of the LOLIN D32 reference sheet:
one dashed rectangle per functional block wrapping its real geometry,
with the block heading inside the top-left corner and a doubled dashed
edge where two blocks sit next to each other. A block too tall for one
part column flows into a sibling sub-column inside the same frame
(kitchen_analyzer uses this to frame POWER / GAS / SMOKE / CLIMATE /
MOTION / DISPLAY on the parent and the eight board sections on the child
sheet).

## Prototype scope

Single-unit symbols on one sheet, nets routed as labelled pin stubs. Multi-unit
placement, hierarchical sheets, wire-level routing for two-pin nets, and
updating existing schematics (UUID preservation) are the next stages in
`.agent/BUILD_PLAN.md` §10.

## command to generate the KitchenAnalyzer Schematic
python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export