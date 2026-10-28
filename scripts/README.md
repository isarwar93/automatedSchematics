# scripts

Helper scripts for the **automatedSchematics** KiCad schematic generator.


## scripts/refresh_py_to_sch.py

Run the generator (`generator/kicadgen`) against the pure-Python project
specs in `projects/<slug>/<slug>.py` — every spec builds a `Design`, and
the script writes and validates the resulting `.kicad_sch` the same way for
all of them.

```bash
./scripts/refresh_py_to_sch.py                            # generate all projects/*/*.py
./scripts/refresh_py_to_sch.py kitchen_analyzer           # one project, by slug
./scripts/refresh_py_to_sch.py projects/kitchen_analyzer/kitchen_analyzer.py
./scripts/refresh_py_to_sch.py kitchen_analyzer --export  # + kicad-cli ERC/netlist/SVG
./scripts/refresh_py_to_sch.py kitchen_analyzer -o /tmp/demo.kicad_sch
./scripts/refresh_py_to_sch.py --out-dir /tmp/scratch     # generate all into a folder
./scripts/refresh_py_to_sch.py --list                     # show the project specs
```

Project specs only describe the circuit; the script drives
`Design.generate()` so every project shares the same defaults:

- Output: `output/<slug>/<slug>.kicad_sch` plus a `<slug>.kicad_pro`
  project file — one folder (one KiCad project) per project, keeping the
  schematic, project file, ERC report, netlist and SVG together.
  (Override with `-o` / `--out-dir`.)
- Exit codes: 0 = clean, 1 = spec/tooling error, 2 = written but ERC
  violations found with `--export`.

## scripts/refresh_sch_to_py.py

The reverse pass. Reads a generated schematic hierarchy from
`output/<slug>/` (root plus every sub-sheet file beside it) and rebuilds
the project's Python spec files from it, children first:

```bash
./scripts/refresh_sch_to_py.py --list                     # list generated schematics
./scripts/refresh_sch_to_py.py kitchen_analyzer           # rebuild projects/<slug>/*.py
./scripts/refresh_sch_to_py.py kitchen_analyzer --project-dir /tmp/scratch
./scripts/refresh_sch_to_py.py --sch output/kitchen_analyzer/kitchen_analyzer.kicad_sch
./scripts/refresh_sch_to_py.py --sch /tmp/out/kitchen.kicad_sch --project kitchen_analyzer
```

Each overwritten `.py` is backed up first, into `output/<slug>/backups/`
as `<name>-<timestamp>.py.bak`; only the last 3 backups are kept, older
ones are pruned automatically (`--backup-dir` moves the folder). The
rewritten specs compile and generate again, but hand-written comments,
docstrings and original block keys do not survive the round trip (the
recovery rules and fidelity losses are documented in
`generator/kicadgen/reverse.py`). Useful when schematics have been edited
in KiCad and the specs should follow, and as a round-trip correctness
check for the generator itself.

Exit codes: 0 = specs written, 1 = error (missing schematic or sub-sheet).

## scripts/run_tests.py

Run the unittest suite from `tests/`, all of it or selected modules and
single test cases, with automatic discovery.

```bash
./scripts/run_tests.py                          # every test in tests/
./scripts/run_tests.py test_model               # one module
./scripts/run_tests.py test_model test_sexpr    # several modules
./scripts/run_tests.py model                    # test_ prefix optional
./scripts/run_tests.py test_model.TestModel     # one test case / method
./scripts/run_tests.py --list                   # modules with case counts
./scripts/run_tests.py -v test_symbols          # verbose unittest output
```

Exit codes: 0 = every selected test passed, 1 = failure or error (or
unknown test name). unittest progress goes to stderr, like
`python3 -m unittest`.

## scripts/search.sh

Search KiCad symbol libraries for a symbol or library matching a pattern.

```bash
./scripts/search.sh --search esp                  # find every symbol/lib containing "esp"
./scripts/search.sh --search "Device:R"           # find exact symbol across all libs
./scripts/search.sh --lib Device --search R       # search only inside Device.kicad_sym
./scripts/search.sh --list-libs                   # list all installed symbol libraries
```

Results show the library path, symbol name, and a short summary so you can
copy the correct `lib_id` (e.g. `Device:R`) into a project spec.

## Run → Export → Validate

```bash
# 1. Build from a project spec
./scripts/refresh_py_to_sch.py kitchen_analyzer

# 2. Run KiCad CLI checks (ERC, netlist, SVG) — or just use --export
./scripts/refresh_py_to_sch.py kitchen_analyzer --export
kicad-cli sch erc --severity-error --severity-warning -o output/<slug>/<slug>.erc.rpt output/<slug>/<slug>.kicad_sch
kicad-cli sch export netlist -o output/<slug>/<slug>.net output/<slug>/<slug>.kicad_sch
kicad-cli sch export svg --exclude-drawing-sheet -o output/<slug>/<slug>-svg output/<slug>/<slug>.kicad_sch
```

Generated files go to `output/` (not tracked in git). Only `projects/` specs
and `generator/` source are committed.

## Project folder convention

Each project lives in its own folder under `projects/`. There is always
one main file — the entry spec named after the folder (`<slug>.py`) —
plus one design file per generated schematic:

```
projects/
└── kitchen_analyzer/
    ├── kitchen_analyzer.py     # main spec — the script picks this up
    └── lolin32_internal.py     # child-sheet design, imported by the main
```

Two output schematics (`kitchen_analyzer.kicad_sch` +
`lolin32_internal.kicad_sch`) come from these two files: the main spec
builds the top-level `Design` and registers the imported child designs as
hierarchical sheets. The main output file is named after the folder; each
child file is named after its sheet (`<name>.py` → `<name>.kicad_sch`).
More output schematics for the same project simply mean more design files
next to the main spec — the script only executes the main file and puts
the project folder on `sys.path` so the imports work.

The generated artifacts follow the same convention — the script writes
everything for a project into its own folder under `output/`:

```
output/
└── kitchen_analyzer/
    ├── kitchen_analyzer.kicad_pro   # KiCad project file — ready for a .kicad_pcb
    ├── kitchen_analyzer.kicad_sch   # the schematic
    ├── kitchen_analyzer.erc.rpt     # ERC report (--export)
    ├── kitchen_analyzer.net         # netlist (--export)
    └── kitchen_analyzer-svg/        # SVG export (--export)
```
