"""KitchenAnalyzer — LOLIN32 internal (D32) sub-sheet design, pure Python.

Child-sheet design of the KitchenAnalyzer project: the main spec
(kitchen_analyzer.py in this folder) imports this module and registers
``design`` as the "LOLIN32 internal (D32)" hierarchical sheet. Double
clicking that sheet box next to U1 in KiCad opens the generated child
file lolin32_internal.kicad_sch.

Source: https://github.com/isarwar93/kitchenAnalyzer
The child sheet redraws the D32 board electronics, all verified against
the LOLIN D32 rev 1.0.0 PDF (local/sch_d32_v1.0.0.pdf, crops read at
600 dpi). Both headers are 1x16P and number their pins 1 to 16:

  JP1: 1=3V3, 2=RESET, 3=VP, 4=VN, 5=IO34, 6=IO32, 7=IO33, 8=IO25,
       9=IO26, 10=IO27, 11=IO14, 12=IO12, 13=IO13, 14=EN, 15=VBUS,
       16=VBAT
  JP2: 1=GND, 2=IO23, 3=IO22, 4=TXD0, 5=RXD0, 6=IO21, 7=IO19, 8=IO18,
       9=IO5, 10=IO17, 11=IO16, 12=IO4, 13=IO0, 14=IO2, 15=IO15, 16=GND

- Power: battery rail VBAT feeds the system rail VIN through P-MOSFET Q1
  (CJ2301; drawn as Transistor_FET:AO3401A with value CJ2301, gate to the
  VBUS node through R5 100k pull-down), while diode D2 (B5819) feeds VIN
  from VBUS. VIN (C3 1uF) powers U3 ME6211 (drawn with the 3V3 version
  ME6211C33M5: IN, GND, CE, NC, VOUT); CE is tied to VIN through R4 100k,
  VOUT is the 3V3 rail with C1 1uF.
- Battery: U6 TP4054 (KiCad has no TP4054 symbol; the project symbol
  follows the module's own five-pin footprint: 1 CHRG, 2 GND, 3 BAT,
  4 VCC, 5 PROG). VBUS charges through VCC (C6 10uF), the CHRG open-
  drain output pulls LED1's cathode low (LED1 anode to VBUS via R6 2k),
  BAT drives VBAT (C2 10uF), PROG sets the current via R7 2k. P1 is the
  board's PH-2-W0 battery connector: pin 1 VBAT, pin 2 GND. The IO35
  battery divider is R12 100k from VBAT and R17 100k into GND.
- USB UART: P2 USB-B-Micro receptacle (5P: 1 VBUS, 2 D-, 3 D+, 4 ID,
  5 GND; the KiCad symbol's extra Shield pad floats like the PDF, VERIFY
  on hardware), U4 CH340C (TXD pin 2 to module RXD0, RXD pin 3 from
  module TXD0, V3 pin 4 and VCC pin 16 both on 3V3 as the KiCad symbol
  document requires for a 3.3 V supply; R232, CTS#, DSR#, RI# and DCD#
  float like in the PDF; XI/XO are no-connect pins in the KiCad symbol).
  C9 10uF and C10 100nF decouple 3V3/GND here.
- Auto flash: U5 UMH3N (Transistor_BJT:UMH3N). With KiCad's pin naming:
  E1(1) and B2(5) on RTS, B1(2) and E2(4) on DTR, C1(6) on RESET and
  C2(3) on IO0: the CH340C modem-control pair drives EN reset and the
  download (IO0) strap, matching the PDF's printed pin wiring.
- Reset: R1 10k pulls EN up to 3V3, C4 100nF decouples EN, K1 (Switch:
  SW_Push) is the EN/RESET button; R1/C4/K1 sit on the RESET net.
- Board LED: R9 2k from 3V3 into LED2, LED2 cathode on IO5: the onboard
  LED2 is driven when IO5 is low.
- Core: U2 = RF_Module:ESP32-WROOM-32D, value ESP-WROOM-32. All module
  pins wired as printed in the PDF: GND 1/15/38, 3V3 2, RESET to EN 3,
  VP to SENSOR_VP 4, VN to SENSOR_VN 5, IO34 6, IO35 7, IO32 8, IO33 9,
  IO25 10, IO26 11, IO27 12, IO14 13, IO12 14, IO13 16, IO15 23, IO2 24,
  IO0 25 (also the auto-flash output), IO4 26, IO16 27, IO17 28, IO5 29
  (also the LED2 cathode), IO18 30, IO19 31, IO21 33, RXD0 34, TXD0 35,
  IO22 36, IO23 37. Pin 32 is NC in the module symbol. The flash data
  pins (17 to 22: SD2, SD3, CMD, CLK, SD0, SD1) are inside the module
  and unconnected, exactly like the PDF's symbol omits them.

The nine signals the kitchen analyzer uses cross the sheet boundary by
name (sheet pin = parent net = child net): 3V3, GND, VBAT, VBUS, GAS_A0
(module IO32), DHT22_DATA (IO13), PIR_OUT (IO34), LCD_SDA (IO17),
LCD_SCL (IO19). The remaining LOLIN32 header pins float; the onboard
LED2 on IO5 needs no external wiring, so no status-LED circuit is drawn
and the firmware's status-LED GPIO is UNKNOWN.

Deliberately unconnected on the child sheet (documented, not forgotten):
the USB shield pad (the PDF symbol has no shield wire; tie strategy is a
hardware decision, VERIFY), CH340C R232 and its CTS#/DSR#/RI#/DCD# modem
inputs (dangling in the PDF), and the USB ID pin (pin 4: dangling in the
PDF). The module's flash data pins are listed above.

ERC notes (child sheet): a PWR_FLAG marks VIN (the rail is built from
passive parts only, Q1/D2, so ERC would otherwise report the ME6211 VCC
power input undriven). The lib_symbol_issues warnings about the
KitchenAnalyzer library are expected for a project-namespace library.
ERC errors kept on purpose because the D32 PDF ties them (faithfulness
over a clean ERC): 3V3 carries three power outputs at once (the board's
own regulator feed ME6211 VOUT, CH340C's internal-regulator pin V3 and
the LOLIN32 header pin 3V3), and CH340C's Rs-232 pin R232 floats.

Run (the script drives generation from the main spec):
    python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export
"""

from kicadgen import Component, Design, connect, custom_symbol, pin

KitchenAnalyzer = "KitchenAnalyzer"  # library nickname shared with the main spec

# ----------------------------------------------------------------------
# LOLIN32 internal circuit, hierarchical sub-sheet.
#
# Everything below is verified wire-for-wire against the D32 rev 1.0.0
# PDF (600 dpi crops). Refs follow the D32 schematic where they are free
# in this project; the module itself gets U2 because the parent's board
# symbol owns U1. TP4054 has no KiCad symbol; the same for none of the
# rest of the parts: everything is a real KiCad library symbol except
# U6 (TP4054).
# ----------------------------------------------------------------------

design = Design(
    name="KitchenAnalyzer_LOLIN32_internal",
    paper="A3",
    orientation="landscape",
    revision="DRAFT-2",
)

# Block separators with headings, computed by the layout engine.
design.mark_blocks({
    "power": "POWER",
    "controller": "ESP32 MODULE",
    "connector": "HEADERS",
    "usb": "USB / UART",
    "autoflash": "AUTO FLASH",
    "reset": "RESET",
    "led": "ONBOARD LED",
    "battery": "BATTERY / CHARGING",
})

MODULE_LIB = "RF_Module:ESP32-WROOM-32D"  # library: ESP32-WROOM-32D variant

# Core
module = Component(ref="U2", symbol=MODULE_LIB,
                   value="ESP-WROOM-32", block="controller")

# Power (VBAT/VBUS to the VIN rail, then the 3V3 regulator)
q1 = Component(ref="Q1", symbol="Transistor_FET:AO3401A", value="CJ2301",
               block="power")  # P-MOSFET, the part is CJ2301 per the PDF
d2 = Component(ref="D2", symbol="Device:D_Schottky", value="B5819",
               block="power")
r5 = Component(ref="R5", symbol="Device:R", value="100k", block="power")
r4 = Component(ref="R4", symbol="Device:R", value="100k", block="power")
c3 = Component(ref="C3", symbol="Device:C", value="1uF", block="power")
ldo = Component(ref="U3", symbol="Regulator_Linear:ME6211C33M5",
                value="ME6211", block="power")
c1 = Component(ref="C1", symbol="Device:C", value="1uF", block="power")

# USB UART
usb = Component(ref="P2", symbol="Connector:USB_B_Micro",
                value="USB-MICRO_5P", block="usb")
uart = Component(ref="U4", symbol="Interface_USB:CH340C", block="usb")
c9 = Component(ref="C9", symbol="Device:C", value="10uF", block="usb")
c10 = Component(ref="C10", symbol="Device:C", value="100nF", block="usb")

# Auto flash (CH340 modem-control pair to RESET / IO0)
dump = Component(ref="U5", symbol="Transistor_BJT:UMH3N", block="autoflash")

# Reset circuit
r1 = Component(ref="R1", symbol="Device:R", value="10k", block="reset")
c4 = Component(ref="C4", symbol="Device:C", value="100nF", block="reset")
k1 = Component(ref="K1", symbol="Switch:SW_Push", value="RESET",
               block="reset")

# Board LED (onboard LED2, active low on IO5)
r9 = Component(ref="R9", symbol="Device:R", value="2k", block="led")
led2 = Component(ref="LED2", symbol="Device:LED", block="led")

# Core rail decoupling (3V3/GND, near the module and the USB section)
c7 = Component(ref="C7", symbol="Device:C", value="10uF", block="power")
c8 = Component(ref="C8", symbol="Device:C", value="100nF", block="power")

# Battery charge + connector + IO35 divider
TP4054 = custom_symbol(  # noqa: N806  (KiCad has no TP4054 symbol)
    "TP4054",
    nickname=KitchenAnalyzer,
    prefix="U",
    value="TP4054",
    pins=[
        # Real TP4054 (SOT-23-5) pinout, per the D32 PDF symbol.
        pin(1, name="CHRG", etype="open_collector", side="right"),
        pin(2, name="GND", etype="power_in", side="left"),
        pin(3, name="BAT", etype="power_out", side="right"),
        pin(4, name="VCC", etype="power_in", side="left"),
        pin(5, name="PROG", etype="passive", side="right"),
    ],
)
charger = Component(ref="U6", symbol=TP4054, value="TP4054",
                    block="battery")
r6 = Component(ref="R6", symbol="Device:R", value="2k", block="battery")
led1 = Component(ref="LED1", symbol="Device:LED", block="battery")
c6 = Component(ref="C6", symbol="Device:C", value="10uF", block="battery")
r7 = Component(ref="R7", symbol="Device:R", value="2k", block="battery")
c2 = Component(ref="C2", symbol="Device:C", value="10uF", block="battery")
p1 = Component(ref="P1", symbol="Connector_Generic:Conn_01x02",
               value="PH-2-W0", block="battery")
r12 = Component(ref="R12", symbol="Device:R", value="100k", block="battery")
r17 = Component(ref="R17", symbol="Device:R", value="100k", block="battery")

# Headers (the board's own 1x16P parts)
jp1 = Component(ref="JP1", symbol="Connector_Generic:Conn_01x16",
                value="1x16P", block="connector")
jp2 = Component(ref="JP2", symbol="Connector_Generic:Conn_01x16",
                value="1x16P", block="connector")

# Child-sheet PWR_FLAG: the VIN rail is built from passive parts only
# (Q1/D2), so ERC would report the ME6211 VCC power input undriven.
vin_flag = Component(ref="#FLG4", symbol="power:PWR_FLAG", value="PWR_FLAG",
                     block="power")

design.add(
    module, q1, d2, r5, r4, c3, ldo, c1, c7, c8,
    usb, uart, c9, c10, dump,
    r1, c4, k1, r9, led2,
    charger, r6, led1, c6, r7, c2, p1, r12, r17,
    jp1, jp2, vin_flag,
)

# -- Core power and strap ------------------------------------------------

connect(
    module.pin(2), ldo.pin(5), c1.pin(1),
    c7.pin(1), c8.pin(1), c9.pin(1), c10.pin(1),
    r1.pin(1), r9.pin(1),
    uart.pin("VCC"), uart.pin("V3"),
    jp1.pin(1),
    name="3V3",
)
connect(
    module.pin(1), module.pin(15), module.pin(38),
    ldo.pin(2), c1.pin(2), c3.pin(2),
    c7.pin(2), c8.pin(2), c9.pin(2), c10.pin(2),
    k1.pin(2), c4.pin(2),
    uart.pin("GND"),
    usb.pin(5), r5.pin(2),
    r7.pin(2), c6.pin(2), c2.pin(2), charger.pin(2),
    p1.pin(2), r17.pin(1),
    jp2.pin(1), jp2.pin(16),
    name="GND",
)
connect(
    usb.pin(1), q1.pin(1), r5.pin(1), d2.pin(2),
    charger.pin(4), c6.pin(1), r6.pin(1),
    name="VBUS",
)
# Charging and the battery node.
connect(
    r6.pin(2), led1.pin(2),
    name="LED1_ANODE",
)
connect(led1.pin(1), charger.pin(1), name="CHRG")
connect(
    charger.pin(3), c2.pin(1), p1.pin(1), r12.pin(1), q1.pin(3),
    name="VBAT",
)
connect(r7.pin(1), charger.pin(5), name="PROG")
# System rail: VBAT through Q1 (source on the rail), VBUS through D2,
# into the LDO. Q1's body diode must conduct battery-to-rail and block
# rail-to-battery: with AO3401A numbering (1 G, 2 S, 3 D) the diode runs
# D to S, so pin 3 on VBAT and pin 2 on VIN.
connect(q1.pin(2), d2.pin(1), r4.pin(1), ldo.pin(1), c3.pin(1),
        vin_flag.pin(1), name="VIN")
connect(r4.pin(2), ldo.pin(3), name="VIN_EN")

# -- Reset and auto (flash) ----------------------------------------------

connect(
    r1.pin(2), c4.pin(1), k1.pin(1), module.pin(3), dump.pin(6),
    name="RESET",
)
connect(
    dump.pin(3), module.pin(25),
    jp2.pin(13),
    name="IO0",
)
# CH340C modem lines: DTR/RTS drive the UMH3N transistor pair; with the
# KiCad UMH3N pin naming: E1(1) and B2(5) on RTS, B1(2) and E2(4) on DTR,
# C1(6) on RESET, C2(3) on IO0 (matches the PDF's printed wiring).
connect(uart.pin("~{RTS}"), dump.pin(1), dump.pin(5), name="RTS")
connect(uart.pin("~{DTR}"), dump.pin(2), dump.pin(4), name="DTR")

# -- Board LED -----------------------------------------------------------

connect(r9.pin(2), led2.pin(2), name="LED_ANODE")
connect(led2.pin(1), module.pin(29), jp2.pin(9), name="IO5")

# -- Battery voltage sense divider ---------------------------------------

connect(r12.pin(2), r17.pin(2), module.pin(7), name="IO35")

# -- Header IO nets (each also reaches the module) ------------------------

connect(module.pin(4), jp1.pin(3), name="VP")
connect(module.pin(5), jp1.pin(4), name="VN")
connect(module.pin(6), jp1.pin(5), name="PIR_OUT")
connect(module.pin(8), jp1.pin(6), name="GAS_A0")
connect(module.pin(9), jp1.pin(7), name="IO33")
connect(module.pin(10), jp1.pin(8), name="IO25")
connect(module.pin(11), jp1.pin(9), name="IO26")
connect(module.pin(12), jp1.pin(10), name="IO27")
connect(module.pin(13), jp1.pin(11), name="IO14")
connect(module.pin(14), jp1.pin(12), name="IO12")
connect(module.pin(16), jp1.pin(13), name="DHT22_DATA")
connect(module.pin(23), jp2.pin(15), name="IO15")
connect(module.pin(24), jp2.pin(14), name="IO2")
connect(module.pin(26), jp2.pin(12), name="IO4")
connect(module.pin(27), jp2.pin(11), name="IO16")
connect(module.pin(28), jp2.pin(10), name="LCD_SDA")
connect(module.pin(30), jp2.pin(8), name="IO18")
connect(module.pin(31), jp2.pin(7), name="LCD_SCL")
connect(module.pin(33), jp2.pin(6), name="IO21")
connect(module.pin(34), uart.pin(2), jp2.pin(5), name="RXD0")
connect(module.pin(35), uart.pin(3), jp2.pin(4), name="TXD0")
connect(module.pin(36), jp2.pin(3), name="IO22")
connect(module.pin(37), jp2.pin(2), name="IO23")

# Deliberately unconnected on the child sheet: the module's flash data
# pins 17 to 22 (SD2, SWP/SD3, SCS/CMD, SCK/CLK, SDO/SD0, SDI/SD1: the
# flash is inside the module) and module pin 32 (NC), the USB shield pad
# (the PDF symbol has no shield wire; tie strategy is a hardware
# decision, VERIFY), CH340C R232 and its CTS#/DSR#/RI#/DCD# modem inputs
# (dangling in the PDF), and the USB ID pin (pin 4: dangling in the
# PDF). The regulator's CE enable net is wired through R4 from VIN,
# exactly as drawn in the PDF; "VIN_EN" is only the name of that
# resistor-to-CE net, not a separate control rail.

# Registered by the main spec (kitchen_analyzer.py in this folder) with
# the nine boundary names: 3V3, GND, VBAT, VBUS, GAS_A0, DHT22_DATA,
# PIR_OUT, LCD_SDA, LCD_SCL. The script drives generation:
#     python3 scripts/refresh_py_to_sch.py kitchen_analyzer --export
