"""KitchenAnalyzer schematic specification, pure Python.

Source: https://github.com/isarwar93/kitchenAnalyzer
Design: ESP32 LOLIN32 kitchen monitor with an MQ-2 gas/smoke module, DHT22
temperature/humidity sensor, PIR motion sensor, 16x2 I2C LCD, and a
single-cell LiPo battery input.

The LOLIN32 board is not a custom symbol on this sheet: it is represented
by the "LOLIN32 internal (D32)" hierarchical sub-sheet box. The board
electronics follow the LOLIN D32 rev 1.0.0 schematic
(local/sch_d32_v1.0.0.pdf, title "D32", dated 2018-04-08, ESP-WROOM-32);
they live in the sibling file lolin32_internal.py in this folder, which
also carries the verified header pin tables (both headers are 1x16P and
number their pins 1 to 16) and the wire-for-wire PDF verification notes.
Double clicking the "LOLIN32 internal (D32)" sheet box in KiCad opens the
child file lolin32_internal.kicad_sch.

The nine signals the kitchen analyzer uses cross the sheet boundary by
name (sheet pin = parent net = child net): 3V3, GND, VBAT, VBUS, GAS_A0
(module IO32), DHT22_DATA (IO13), PIR_OUT (IO34), LCD_SDA (IO17),
LCD_SCL (IO19). Every boundary net exists on this sheet as a labelled
stub at the module symbol and on the sheet box; KiCad joins them by name.

Module and display symbols (parent sheet):

- The 16x2 LCD is the real KiCad symbol Display_Character:LCD-016N002L
  (its description reads: LCD 12x2, 8 bit parallel bus, 3V or 5V VDD).
  Because the firmware drives it over I2C, the PCF8574 I2C backpack is
  its own custom symbol (pin style mirrors Interface_Expansion:PCF8574T:
  SCL input, SDA/P0 to P7 bidirectional), and the chain is ESP32 I2C,
  backpack, then the LCD's parallel bus in 4-bit mode.
- No DHT22 symbol ships with KiCad, so the DHT22 module symbol mirrors
  Sensor:DHT11: pin 1 VDD (power_in), pin 2 DATA (bidirectional), pin 3
  NC (no_connect), pin 4 GND (power_in).
- No MQ-2 symbol ships with KiCad; the MQ-2 module symbol is modelled on
  Sensor_Gas:MQ-6's pin style (short pin names, signal pins passive-side,
  power pins power_in), but keeps the module's actual four wires.

Deliberately unconnected (documented, not forgotten): the MQ-2 DO pin
(firmware uses the analog output), LCD DB0 to DB3 (4-bit mode uses DB4
to DB7), LCD VO/A/K and backpack P3 (trim pot and backlight circuitry
live on the backpack board, VERIFY), and the DHT22 NC pad. The spare
LOLIN32 header pins float — they were removed together with the U1
symbol block and are documented on the sub-sheet (lolin32_internal.py)
together with the onboard LED2 on IO5 and the UNKNOWN firmware
status-LED GPIO.

ERC notes (parent sheet): the deliberate float above is reported by
ERC. PWR_FLAG markers on GND, VBAT and VBUS mark where power enters the
circuit nets. The lib_symbol_issues warnings about the KitchenAnalyzer
library are expected for a project-namespace library; the child sheet's
ERC sanctions are documented in lolin32_internal.py. On the parent, VBUS
carries both PWR_FLAG and the sheet pin output (faithful to reality:
USB power enters an otherwise unwired rail).

Hardware review items (VERIFY/UNKNOWN, not invented): MQ-2 heater and
output levels on a 3V3 rail, LCD and backpack supplied at 3.3 V (the
LCD tolerates 3 V VDD per the KiCad symbol description; most I2C
backpacks want 5 V), DHT22 DATA pull-up, PIR supply taken from VBAT
(battery, 3.0 to 4.2 V, because no 5V pin exists on the header) and the
PIR OUT high level feeding IO34 (not 5 V tolerant, input only), the
firmware status-LED GPIO, and the battery connector wiring. Both BT1
(parent sheet, the off-board battery source for the analyzer) and P1
(child sheet, the board's own PH-2-W0 connector) sit on VBAT: the first
represents the user's battery, the second the board's wiring.

Run:
    python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export
"""

from kicadgen import Component, Design, connect, custom_symbol, pin

from lolin32_internal import design as lolin32_internal_sheet

design = Design(
    name="KitchenAnalyzer",
    paper="A3",
    orientation="landscape",
    revision="DRAFT-2",
)

# Block separators with headings: a light vertical line between the
# functional-block columns and a bold heading above each one, computed
# from the real column geometry by the layout engine.
design.mark_blocks({
    "power": "POWER",
    "gas_sensor": "GAS / SMOKE",
    "climate_sensor": "CLIMATE",
    "motion_sensor": "MOTION",
    "display": "DISPLAY",
})

# ----------------------------------------------------------------------
# Project symbols. Only the LCD uses a real KiCad library symbol; the
# other boards/modules get project symbols in the KitchenAnalyzer
# namespace. The LOLIN32 board itself is not a symbol here: it is the
# "LOLIN32 internal (D32)" hierarchical sub-sheet, whose nine boundary
# pins carry the same net names the analyzer uses.
# ----------------------------------------------------------------------

KitchenAnalyzer = "KitchenAnalyzer"  # library nickname shared by all

MQ2 = custom_symbol(
    "MQ2_Module",
    nickname=KitchenAnalyzer,
    prefix="J",
    value="MQ-2 Gas Sensor Module",
    pins=[
        # Pin style mirrors Sensor_Gas:MQ-6 (compact names, signal pins
        # horizontally on the sides, power pins power_in).
        pin(1, name="VCC", etype="power_in", side="left"),
        pin(2, name="GND", etype="power_in", side="left"),
        pin(3, name="AO", etype="output", side="right"),
        pin(4, name="DO", etype="output", side="right"),
    ],
)

DHT22 = custom_symbol(
    "DHT22_Module",
    nickname=KitchenAnalyzer,
    prefix="J",
    value="DHT22 Temperature / Humidity",
    pins=[
        # Mirrors Sensor:DHT11 (KiCad has no DHT22 symbol).
        pin(1, name="VDD", etype="power_in", side="left"),
        pin(2, name="DATA", etype="bidirectional", side="right"),
        pin(3, name="NC", etype="no_connect", side="right"),
        pin(4, name="GND", etype="power_in", side="left"),
    ],
)

PIR = custom_symbol(
    "PIR_Module",
    nickname=KitchenAnalyzer,
    prefix="J",
    value="PIR Motion Sensor (SUPPLY VERIFY)",
    pins=[
        pin("VCC", etype="power_in", side="left"),
        pin("OUT", etype="output", side="right"),
        pin("GND", etype="power_in", side="left"),
    ],
)

LCD_BACKPACK = custom_symbol(
    "I2C_Backpack",
    nickname=KitchenAnalyzer,
    prefix="J",
    value="PCF8574 I2C Backpack (0x27)",
    pins=[
        # Pin style mirrors Interface_Expansion:PCF8574T.
        pin(1, name="VCC", etype="power_in", side="left"),
        pin(2, name="GND", etype="power_in", side="left"),
        pin(3, name="SDA", etype="bidirectional", side="left"),
        pin(4, name="SCL", etype="input", side="left"),
        pin(5, name="P0", etype="bidirectional", side="right"),
        pin(6, name="P1", etype="bidirectional", side="right"),
        pin(7, name="P2", etype="bidirectional", side="right"),
        pin(8, name="P3", etype="bidirectional", side="right"),
        pin(9, name="P4", etype="bidirectional", side="right"),
        pin(10, name="P5", etype="bidirectional", side="right"),
        pin(11, name="P6", etype="bidirectional", side="right"),
        pin(12, name="P7", etype="bidirectional", side="right"),
    ],
)

LCD = "Display_Character:LCD-016N002L"  # 16x2 character LCD, real symbol

# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------

battery = Component(
    ref="BT1",
    symbol="Connector_Generic:Conn_01x02",
    value="Single-cell LiPo battery input",
    block="power",
    # Schematic representation only; connector type is not specified.
)

gas = Component(ref="J1", symbol=MQ2, value="MQ-2 gas sensor module",
                block="gas_sensor")

climate = Component(ref="J2", symbol=DHT22, value="DHT22",
                    block="climate_sensor")

motion = Component(ref="J3", symbol=PIR, value="PIR motion sensor",
                   block="motion_sensor")

display = Component(ref="J4", symbol=LCD,
                    value="16x2 character LCD, 4-bit bus", block="display")

backpack = Component(ref="J5", symbol=LCD_BACKPACK,
                     value="PCF8574 I2C backpack, address 0x27",
                     block="display")

# PWR_FLAG markers: ERC wants to know where power enters the circuit.
# The battery and the USB rail (VBUS) arrive from off-board, which the
# schematic represents only as header pins.
ground_flag = Component(
    ref="#FLG1", symbol="power:PWR_FLAG", value="PWR_FLAG", block="power",
)
battery_flag = Component(
    ref="#FLG2", symbol="power:PWR_FLAG", value="PWR_FLAG", block="power",
)
usb_flag = Component(
    ref="#FLG3", symbol="power:PWR_FLAG", value="PWR_FLAG", block="power",
)

design.add(battery, gas, climate, motion, display, backpack,
           ground_flag, battery_flag, usb_flag)

# ----------------------------------------------------------------------
# Nets. Repository wiring as-is; the flagged nets carry the review
# concerns listed in the module docstring. The board joins every net at
# its sheet box: the parent stub label and the child's hierarchical
# label share the net name, so KiCad joins them through the sheet pin.
# ----------------------------------------------------------------------

# The ground return of the off-board battery and of every module; the
# board's own grounds live on the sub-sheet.
connect(
    gas.pin("GND"),
    climate.pin("GND"),
    motion.pin("GND"),
    backpack.pin("GND"),
    display.pin("VSS"),
    battery.pin(2),
    ground_flag.pin(1),
    name="GND",
)

# Review: verify MQ-2 accepts 3.3 V; the LCD tolerates 3 V VDD per the
# KiCad symbol description; most I2C backpacks want 5 V. The board LDO
# drives this rail through the sheet box (dir output in the child).
connect(
    gas.pin("VCC"),
    climate.pin("VDD"),
    backpack.pin("VCC"),
    display.pin("VDD"),
    name="3V3",
)

# Review: the D32 header has no 5V pin, so the PIR supply comes from the
# unregulated battery rail (VBAT, 3.0 to 4.2 V). The board builds its
# internal system rail from VBAT and VBUS, but that rail is not exposed.
# Verify the PIR tolerates the VBAT range. The PWR_FLAG marks the
# off-board battery as the source.
connect(
    battery.pin(1),
    motion.pin("VCC"),
    battery_flag.pin(1),
    name="VBAT",
)

# The board's USB power rail: arrived at from off-board, not used by
# this battery-powered design.
connect(usb_flag.pin(1), name="VBUS")

# Review: MQ-2 analog output must not exceed the IO32 ADC range.
connect(gas.pin("AO"), name="GAS_A0")

# Review: bare DHT22 needs an external DATA pull-up.
connect(climate.pin("DATA"), name="DHT22_DATA")

# Review: PIR OUT high level vs IO34 (not 5 V tolerant, input only).
connect(motion.pin("OUT"), name="PIR_OUT")

# LCD chain: ESP32 I2C to the PCF8574 backpack, then the backpack's
# parallel bus to the LCD in 4-bit mode (DB4 to DB7 only).
connect(backpack.pin("SDA"), name="LCD_SDA")
connect(backpack.pin("SCL"), name="LCD_SCL")
connect(backpack.pin("P0"), display.pin("RS"), name="LCD_RS")
connect(backpack.pin("P1"), display.pin("R/~{W}"), name="LCD_RW")
connect(backpack.pin("P2"), display.pin("E"), name="LCD_E")
connect(backpack.pin("P4"), display.pin("DB4"), name="LCD_DB4")
connect(backpack.pin("P5"), display.pin("DB5"), name="LCD_DB5")
connect(backpack.pin("P6"), display.pin("DB6"), name="LCD_DB6")
connect(backpack.pin("P7"), display.pin("DB7"), name="LCD_DB7")

# Deliberately unconnected: the MQ-2 DO pin, LCD DB0 to DB3 (4-bit
# mode), the LCD VO/A/K pins and backpack P3 (contrast pot and backlight
# circuitry are inside the backpack board, VERIFY on real hardware and
# re-draw if the backpack is replaced by discrete parts), and the DHT22
# NC pad. The spare LOLIN32 header pins float on the sub-sheet, together
# with the onboard LED2 on IO5, which needs no external wiring; the
# firmware's status-LED GPIO is UNKNOWN (see the child docstring).


# ----------------------------------------------------------------------
# LOLIN32 internal circuit: hierarchical sub-sheet.
#
# The child design is built by the sibling file lolin32_internal.py and
# imported at the top of this file; it is verified wire-for-wire against
# the D32 rev 1.0.0 PDF (600 dpi crops). Sheet registration: pin names =
# parent net names; the child carries the same names as hierarchical
# labels. Clicking the sheet box opens lolin32_internal.kicad_sch.
# ----------------------------------------------------------------------

design.add_sheet(
    "LOLIN32 internal (D32)", "lolin32_internal.kicad_sch",
    lolin32_internal_sheet,
    pins=[
        ("3V3", "power_out"),
        ("GND", "power_in"),
        ("VBAT", "power_out"),
        ("VBUS", "power_out"),
        ("GAS_A0", "bidirectional"),
        ("DHT22_DATA", "bidirectional"),
        ("PIR_OUT", "bidirectional"),
        ("LCD_SDA", "bidirectional"),
        ("LCD_SCL", "bidirectional"),
    ],
)

# The script drives generation; this file only describes the circuit:
#     python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export
