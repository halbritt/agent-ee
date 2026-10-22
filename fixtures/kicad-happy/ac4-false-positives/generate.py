"""Author the original AGENTEE-219 analyzer reproducer schematic from scratch.

Every symbol, pin list, net name and coordinate here is written for this
fixture; no third-party design, symbol library or footprint bytes are copied.
Values such as ``LSM6DSO32`` are part-number facts used only so the analyzer's
keyword classifier engages exactly as it does on the real pod. The schematic
is the full reproducer: three PP-001 and one PR-001 false-positive mechanisms,
one real PP-001 and one real PR-001 violation that must stay reported, and the
two agent-469 adversarial controls (a P-MOSFET gate divider with a competing
pull-up to the source, and an I2C device whose chip-select carries both a
passive pull-up and an MCU GPIO) that must stay reported too.
"""
import json
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parent
NAMESPACE = UUID('3b0f6c2e-219a-4c4f-9a4e-ac4f00000219')
PITCH = 2.54

# name -> (reference prefix, value, [(pin number, pin name, electrical type)])
SYMBOLS = {
    'OwnedBuck': ('U', 'OwnedBuck', [('1', 'VIN', 'power_in'), ('2', 'GND', 'power_in'),
                                     ('3', 'SW', 'power_out')]),
    'OwnedLDO': ('U', 'OwnedLDO', [('1', 'IN', 'power_in'), ('2', 'GND', 'power_in'),
                                   ('3', 'OUT', 'power_out')]),
    'OwnedRefBuffer': ('U', 'OwnedRefBuffer', [('1', 'OUT', 'power_out'), ('2', 'V+', 'power_in'),
                                               ('3', 'GND', 'power_in'), ('4', '-IN', 'input')]),
    'OwnedADC': ('U', 'OwnedADC', [('1', 'VDD', 'power_in'), ('2', 'VREF', 'power_in'),
                                   ('3', 'GND', 'power_in')]),
    'OwnedLoad': ('U', 'OwnedLoad', [('1', 'VIN', 'power_in'), ('2', 'GND', 'power_in')]),
    'OwnedMCU': ('U', 'OwnedMCU', [('1', 'VDD', 'power_in'), ('2', 'GND', 'power_in')]
                 + [(str(2 + i), f'IO{i}', 'bidirectional') for i in range(1, 12)]),
    'OwnedIMU': ('U', 'LSM6DSO32', [('1', 'VDD', 'power_in'), ('2', 'GND', 'power_in'),
                                    ('3', 'CS', 'input'), ('4', 'SCL', 'input'),
                                    ('5', 'SDA', 'bidirectional')]),
    'OwnedSpiAdc': ('U', 'OwnedSpiAdc', [('1', 'VDD', 'power_in'), ('2', 'GND', 'power_in'),
                                         ('3', 'CS', 'input'), ('4', 'CLK', 'input'),
                                         ('5', 'DIN', 'input'), ('6', 'DOUT', 'output')]),
    'Q_PMOS_Owned': ('Q', 'OwnedPMOS', [('1', 'G', 'input'), ('2', 'D', 'passive'),
                                        ('3', 'S', 'passive')]),
    'OwnedConn2': ('J', 'OwnedConn2', [('1', 'Pin_1', 'passive'), ('2', 'Pin_2', 'passive')]),
    'OwnedR': ('R', 'R', [('1', '~', 'passive'), ('2', '~', 'passive')]),
    'OwnedC': ('C', 'C', [('1', '~', 'passive'), ('2', '~', 'passive')]),
    'OwnedL': ('L', 'L', [('1', '1', 'passive'), ('2', '2', 'passive')]),
    'OwnedZener': ('D', 'OwnedZener', [('1', 'K', 'passive'), ('2', 'A', 'passive')]),
}

# reference, symbol, value, nets in pin order
PARTS = [
    # Finding (a1): buck switch node power_out reaches MID_NODE through L1.
    ('U1', 'OwnedBuck', 'OwnedBuck', ['+12V', 'GND', 'SW_NODE']),
    ('L1', 'OwnedL', '15uH', ['SW_NODE', 'MID_NODE']),
    ('C1', 'OwnedC', '10uF', ['MID_NODE', 'GND']),
    ('U2', 'OwnedLDO', 'OwnedLDO', ['MID_NODE', 'GND', '+3V3']),
    # Finding (a2): reference buffer power_out directly on the ADC VREF net.
    ('U3', 'OwnedRefBuffer', 'OwnedRefBuffer', ['REF_BUF', '+3V3', 'GND', 'REF_BUF']),
    ('C2', 'OwnedC', '1uF', ['REF_BUF', 'GND']),
    ('U4', 'OwnedADC', 'OwnedADC', ['+3V3', 'REF_BUF', 'GND']),
    # Finding (a3): connector -> P-MOSFET reverse protection -> load rail.
    ('J1', 'OwnedConn2', 'OwnedConn2', ['IN_RAW', 'GND']),
    ('Q1', 'Q_PMOS_Owned', 'OwnedPMOS', ['GATE_NODE', 'IN_RAW', 'PROT_NODE']),
    ('R1', 'OwnedR', '100k', ['GATE_NODE', 'GND']),
    ('D1', 'OwnedZener', 'OwnedZener', ['PROT_NODE', 'GATE_NODE']),
    ('C3', 'OwnedC', '4.7uF', ['PROT_NODE', 'GND']),
    ('U5', 'OwnedLoad', 'OwnedLoad', ['PROT_NODE', 'GND']),
    # Finding (b): dual-mode IMU wired for SPI: chip select driven by the MCU
    # and an SPI peripheral's CLK/DIN on the same clock and data nets.
    ('U6', 'OwnedMCU', 'OwnedMCU', ['+3V3', 'GND', 'SPI_MOSI', 'SPI_SCK', 'IMU_CS',
                                    'I2C_SDA', 'I2C_SCL', 'GATE_CTRL', 'SPI_MISO',
                                    'ADC_CS', 'I2C2_SDA', 'I2C2_SCL', 'CS_NODE']),
    ('U7', 'OwnedIMU', 'LSM6DSO32', ['+3V3', 'GND', 'IMU_CS', 'SPI_SCK', 'SPI_MOSI']),
    ('R2', 'OwnedR', '10k', ['IMU_CS', '+3V3']),
    ('U13', 'OwnedSpiAdc', 'OwnedSpiAdc', ['+3V3', 'GND', 'ADC_CS', 'SPI_SCK', 'SPI_MOSI',
                                           'SPI_MISO']),
    # Control (PP-001): supply pin only AC-coupled to +12V; a real violation.
    ('C4', 'OwnedC', '100nF', ['+12V', 'AC_NODE']),
    ('C5', 'OwnedC', '100nF', ['AC_NODE', 'GND']),
    ('U8', 'OwnedLoad', 'OwnedLoad', ['AC_NODE', 'GND']),
    # Control (PR-001): same IMU in I2C mode (CS tied high), no pull-ups.
    ('U9', 'OwnedIMU', 'LSM6DSO32', ['+3V3', 'GND', '+3V3', 'I2C_SCL', 'I2C_SDA']),
    # Boundary (PP-001): MCU-driven P-MOSFET gate does not prove conduction.
    ('Q2', 'Q_PMOS_Owned', 'OwnedPMOS', ['GATE_CTRL', 'IN_RAW', 'SWITCHED_NODE']),
    ('C6', 'OwnedC', '4.7uF', ['SWITCHED_NODE', 'GND']),
    ('U10', 'OwnedLoad', 'OwnedLoad', ['SWITCHED_NODE', 'GND']),
    # agent-469 control (1): gate divider, 100 ohm to the source rail against
    # 1 Mohm to ground, holds VGS near zero; conduction is not proven.
    ('J2', 'OwnedConn2', 'OwnedConn2', ['IN_RAW2', 'GND']),
    ('Q3', 'Q_PMOS_Owned', 'OwnedPMOS', ['GATE_DIV', 'PROT_DIV', 'IN_RAW2']),
    ('R3', 'OwnedR', '1M', ['GATE_DIV', 'GND']),
    ('R4', 'OwnedR', '100', ['GATE_DIV', 'IN_RAW2']),
    ('C7', 'OwnedC', '1uF', ['PROT_DIV', 'GND']),
    ('U11', 'OwnedLoad', 'OwnedLoad', ['PROT_DIV', 'GND']),
    # agent-469 control (2): I2C-named bus, CS with a passive 10k pull-up and
    # an MCU GPIO, no SPI topology: the missing pull-ups stay errors.
    ('U12', 'OwnedIMU', 'LSM6DSO32', ['+3V3', 'GND', 'CS_NODE', 'I2C2_SCL', 'I2C2_SDA']),
    ('R5', 'OwnedR', '10k', ['CS_NODE', '+3V3']),
]


def uid(name):
    return str(uuid5(NAMESPACE, name))


def font():
    return '(effects (font (size 1.27 1.27)))'


def lib_symbol(name):
    prefix, value, pins = SYMBOLS[name]
    body = ''.join(
        f'\n          (pin {kind} line (at {i * PITCH:.2f} 0 90) (length 2.54)'
        f' (name {json.dumps(pin_name)} {font()}) (number {json.dumps(number)} {font()}))'
        for i, (number, pin_name, kind) in enumerate(pins))
    return (f'\n    (symbol "Owned:{name}" (in_bom yes) (on_board yes)'
            f'\n      (property "Reference" "{prefix}" (at 0 0 0) {font()})'
            f'\n      (property "Value" "{value}" (at 0 0 0) {font()})'
            f'\n      (symbol "{name}_1_1"{body}))')


def schematic():
    used = sorted({symbol for _, symbol, _, _ in PARTS})
    sheet = uid('sheet')
    items = []
    for row, (ref, symbol, value, nets) in enumerate(PARTS):
        pins = SYMBOLS[symbol][2]
        if len(nets) != len(pins):
            raise ValueError(f'{ref}: {len(nets)} nets for {len(pins)} pins')
        x, y = 25.4, 25.4 + row * 10.16
        fields = [('Reference', ref), ('Value', value), ('Footprint', ''),
                  ('MPN', value), ('Manufacturer', 'agent-ee fixture')]
        props = ''.join(f'\n      (property {json.dumps(k)} {json.dumps(v)} (at {x} {y} 0) {font()})'
                        for k, v in fields)
        pin_uuids = ''.join(f'\n      (pin {json.dumps(number)} (uuid "{uid(ref + "." + number)}"))'
                            for number, _, _ in pins)
        items.append(f'\n  (symbol (lib_id "Owned:{symbol}") (at {x} {y} 0) (unit 1)'
                     f'\n      (in_bom yes) (on_board yes) (dnp no) (uuid "{uid(ref)}"){props}{pin_uuids}'
                     f'\n      (instances (project "reproducer" (path "/{sheet}" (reference "{ref}") (unit 1)))))')
        for i, net in enumerate(nets):
            items.append(f'\n  (label {json.dumps(net)} (at {x + i * PITCH:.2f} {y} 0) {font()}'
                         f' (uuid "{uid(ref + ":" + str(i))}"))')
    return (f'(kicad_sch (version 20250114) (generator "agent_ee_owned_fixture")'
            f'\n  (uuid "{sheet}") (paper "A3")'
            f'\n  (lib_symbols{"".join(lib_symbol(name) for name in used)})'
            f'{"".join(items)})\n')


def main():
    (ROOT / 'reproducer.kicad_sch').write_text(schematic())
    (ROOT / 'reproducer.kicad_pro').write_text('{}\n')


if __name__ == '__main__':
    main()
