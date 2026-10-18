"""Original standalone rung-2 board around the captured fome-mc PSU sheet (AGENTEE-106).

Writes the root schematic, an MCU-side stub sheet, an empty saved board, a
project file and the board's interface contract. None of them copies fome-mc
content. The root reuses three
facts of the pinned design so that the captured ``sheets/psu.kicad_sch`` keeps
its native identity: the fome-mc root sheet UUID and the ``psu`` sheet UUID
(the captured symbols' instance paths), and the sheet name ``psu``. The stub is
named ``mcu`` because the captured contract names the power-good and CAN nets
from the MCU side (``/mcu/5V_SENSOR_1_PG``), as the pinned hierarchy does.

The project's ERC rule severities restate the pinned fome-mc project's
declared policy, so ERC grades the block under the design's own rules.

The board contract is the captured block contract (``capture/psu/interface.json``)
with each invariant source moved from the block's pinned-external citation to the
captured ``block-contract.json``, which preserves that citation. A board check
needs a captured requirement source; the reference-block validator expects
exactly this one for an externally cited block requirement.

    python3 fixtures/fome-mc/psu/board/generate.py   # rewrite the five files
"""
import json
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent
NAME = 'fome-mc-psu'
# Facts of fome-mc df304f3: root sheet UUID and the /psu/ sheet instance UUID.
FOME_ROOT_UUID = '3b9c5ffd-e59b-402d-8c5e-052f7ca643a4'
PSU_SHEET_UUID = '00000000-0000-0000-0000-00005da72eff'
PSU_SHEET_FILE = 'sheets/psu.kicad_sch'
STUB_FILE = 'mcu-stub.kicad_sch'

# Captured PSU hierarchical labels (name, shape) and the root conductor each meets.
# 'label' names a root local label; 'mcu' wires the pin to the stub's same-shape partner.
PSU_PORTS = [
    ('12V_RAW', 'input', 'label', '12V_RAW'),
    ('12V_PROT', 'output', 'label', '12V_PROT'),
    ('VBOOST', 'output', 'label', 'VBOOST'),
    ('5V_SENSOR_1', 'output', 'label', '5V_SENSOR_1'),
    ('5V_SENSOR_2', 'output', 'label', '5V_SENSOR_2'),
    ('CANH', 'bidirectional', 'label', 'CAN+'),
    ('CANL', 'bidirectional', 'label', 'CAN-'),
    ('CANH2', 'bidirectional', 'label', 'CAN2+'),
    ('CANL2', 'bidirectional', 'label', 'CAN2-'),
    ('5V_SENSOR_1_PG', 'output', 'mcu', '5V_SENSOR_1_PG'),
    ('5V_SENSOR_2_PG', 'output', 'mcu', '5V_SENSOR_2_PG'),
    ('RXDCAN', 'output', 'mcu', 'CAN_RX'),
    ('RXDCAN2', 'output', 'mcu', 'CAN2_RX'),
    ('TXDCAN', 'input', 'mcu', 'CAN_TX'),
    ('TXDCAN2', 'input', 'mcu', 'CAN2_TX'),
]
OPPOSITE = {'input': 'output', 'output': 'input', 'bidirectional': 'bidirectional'}

# The pinned fome-mc.kicad_pro ERC rule severities (erc.rule_severities at df304f3).
ERC_SEVERITIES = {
    'bus_definition_conflict': 'error', 'bus_entry_needed': 'error', 'bus_to_bus_conflict': 'error',
    'bus_to_net_conflict': 'error', 'conflicting_netclasses': 'error', 'different_unit_footprint': 'error',
    'different_unit_net': 'error', 'duplicate_reference': 'error', 'duplicate_sheet_names': 'error',
    'endpoint_off_grid': 'ignore', 'extra_units': 'error', 'footprint_link_issues': 'ignore',
    'global_label_dangling': 'warning', 'hier_label_mismatch': 'error', 'isolated_pin_label': 'error',
    'label_dangling': 'error', 'lib_symbol_issues': 'ignore', 'lib_symbol_mismatch': 'ignore',
    'missing_bidi_pin': 'warning', 'missing_input_pin': 'warning', 'missing_power_pin': 'error',
    'missing_unit': 'warning', 'multiple_net_names': 'error', 'net_not_bus_member': 'warning',
    'no_connect_connected': 'warning', 'no_connect_dangling': 'warning', 'pin_not_connected': 'error',
    'pin_not_driven': 'error', 'pin_to_pin': 'warning', 'power_pin_not_driven': 'error',
    'same_local_global_label': 'error', 'similar_labels': 'warning', 'simulation_model_issue': 'ignore',
    'unannotated': 'error', 'unconnected_wire_endpoint': 'error', 'unit_value_mismatch': 'error',
    'unresolved_variable': 'error', 'wire_dangling': 'error',
}


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-original-fome-psu-board/' + name))


def effects(hide=False):
    return '(effects (font (size 1.27 1.27))' + (' (hide yes)' if hide else '') + ')'


def prop(key, value, x=0, y=0, hide=True):
    return f'(property "{key}" "{value}" (at {x} {y} 0) {effects(hide)})'


def header_symbol(pins):
    # Original single-row header; pin N at (-5.08, -2.54 (N - 1)), passive.
    name = f'Header_{pins}'
    body = ''.join(f'(pin passive line (at -5.08 {-2.54 * i} 0) (length 2.54) '
                   f'(name "{i + 1}" {effects()}) (number "{i + 1}" {effects()}))' for i in range(pins))
    return (f'(symbol "ConnectorOwned:{name}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes) '
            + prop('Reference', 'J') + prop('Value', name)
            + f'(symbol "{name}_0_1" (rectangle (start -2.54 1.27) (end 2.54 {-2.54 * pins + 1.27}) '
              '(stroke (width 0.254) (type default)) (fill (type background))))'
            + f'(symbol "{name}_1_1" {body}))')


def power_symbol(name, pin_type):
    # Original power markers: GND joins the PSU sheet's global GND, PWR_FLAG marks a driven net.
    return (f'(symbol "power:{name}" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + prop('Reference', '#PWR' if name == 'GND' else '#FLG') + prop('Value', name)
            + f'(symbol "{name}_0_1" (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) '
              '(xy -1.27 -1.27) (xy 0 -1.27)) (stroke (width 0) (type default)) (fill (type none))))'
            + f'(symbol "{name}_1_1" (pin {pin_type} line (at 0 0 270) (length 0) (hide yes) '
              f'(name "{name}" {effects()}) (number "1" {effects()}))))')


def placed(lib_id, ref, value, x, y, pins, path):
    return (f'(symbol (lib_id "{lib_id}") (at {x} {y} 0) (unit 1) (exclude_from_sim no) (in_bom yes) '
            f'(on_board yes) (dnp no) (uuid "{uid(ref)}") '
            + prop('Reference', ref, x, y - 3.81, hide=ref.startswith('#'))
            + prop('Value', value, x, y + 3.81, hide=False) + prop('Footprint', '') + prop('Datasheet', '')
            + ''.join(f'(pin "{i}" (uuid "{uid(ref + "/pin" + str(i))}"))' for i in range(1, pins + 1))
            + f'(instances (project "{NAME}" (path "{path}" (reference "{ref}") (unit 1)))))')


def header(ref, nets, x, y, path, markers):
    """A header whose pin N meets net N at its endpoint: a label, or a GND marker for GND."""
    items = [placed(f'ConnectorOwned:Header_{len(nets)}', ref, 'Header_' + str(len(nets)), x, y, len(nets), path)]
    for i, net in enumerate(nets):
        px, py = round(x - 5.08, 2), round(y + 2.54 * i, 2)
        if net == 'GND':
            items.append(placed('power:GND', f'#PWR{ref[1:]}{i + 1:02d}', 'GND', px, py, 1, path))
        else:
            items.append(f'(label "{net}" (at {px} {py} 180) (effects (font (size 1.27 1.27)) '
                         f'(justify right bottom)) (uuid "{uid(ref + "/label/" + net)}"))')
        if net in markers:
            items.append(placed('power:PWR_FLAG', f'#FLG{ref[1:]}{i + 1:02d}', 'PWR_FLAG', px, py, 1, path))
    return items


def sheet(name, file, sheet_uuid, x, y, height, pins):
    body = ''.join(f'(pin "{n}" {shape} (at {px} {py} {angle}) {effects()} (uuid "{uid(name + "/pin/" + n)}"))'
                   for n, shape, px, py, angle in pins)
    return (f'(sheet (at {x} {y}) (size 30.48 {height}) (fields_autoplaced yes) '
            '(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0)) '
            f'(uuid "{sheet_uuid}") '
            + prop('Sheetname', name, x, y - 0.7, hide=False)
            + prop('Sheetfile', file, x, y + height + 0.6, hide=False)
            + body
            + f'(instances (project "{NAME}" (path "/{FOME_ROOT_UUID}" (page "{2 if name == "psu" else 3}")))))')


def root_schematic(psu_file=PSU_SHEET_FILE):
    """The root sheet; ``psu_file`` names the PSU child (another path for a block adaptation)."""
    items, psu_pins, mcu_pins = [], [], []
    left, right, mcu_x, top = 60.96, 91.44, 132.08, 40.64
    for index, (port, shape, kind, net) in enumerate(PSU_PORTS):
        y = round(top + 5.08 + 2.54 * index, 2)
        x, angle = (left, 180) if shape == 'input' and kind == 'label' else (right, 0)
        psu_pins.append((port, shape, x, y, angle))
        if kind == 'label':
            justify = 'right' if angle == 180 else 'left'
            lx = x - 5.08 if angle == 180 else x + 5.08
            items.append(f'(wire (pts (xy {x} {y}) (xy {lx} {y})) (stroke (width 0) (type default)) '
                         f'(uuid "{uid("wire/" + port)}"))')
            items.append(f'(label "{net}" (at {lx} {y} {0 if angle == 0 else 180}) '
                         f'(effects (font (size 1.27 1.27)) (justify {justify} bottom)) (uuid "{uid("label/" + port)}"))')
        else:
            mcu_pins.append((net, OPPOSITE[shape], mcu_x, y, 180))
            items.append(f'(wire (pts (xy {right} {y}) (xy {mcu_x} {y})) (stroke (width 0) (type default)) '
                         f'(uuid "{uid("wire/" + port)}"))')
    height = round(5.08 * 2 + 2.54 * len(PSU_PORTS), 2)
    items.append(sheet('psu', psu_file, PSU_SHEET_UUID, left, top, height, psu_pins))
    items.append(sheet('mcu', STUB_FILE, uid('sheet/mcu'), mcu_x, top, height, mcu_pins))
    # Standalone boundary: J1 supplies 12V_RAW and its return. The PSU sheet already
    # flags 12V_RAW; the root flags J1's GND return and Q1's switched 12V_PROT, which
    # KiCad cannot trace through the MOSFET. J2 exposes the protected and regulated
    # rails, J3 both CAN buses.
    root = '/' + FOME_ROOT_UUID
    items += header('J1', ['12V_RAW', 'GND'], 25.4, 45.72, root, {'GND'})
    items += header('J2', ['12V_PROT', 'VBOOST', '5V_SENSOR_1', '5V_SENSOR_2', 'GND'], 25.4, 76.2, root,
                    {'12V_PROT'})
    items += header('J3', ['CAN+', 'CAN-', 'CAN2+', 'CAN2-'], 25.4, 101.6, root, set())
    return ('(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") '
            f'(uuid "{FOME_ROOT_UUID}") (paper "A4") '
            '(title_block (title "fome-mc PSU block, standalone rung-2 board (agent-ee original)")) '
            f'(lib_symbols {header_symbol(2)} {header_symbol(4)} {header_symbol(5)} '
            f'{power_symbol("GND", "power_in")} {power_symbol("PWR_FLAG", "power_out")}) '
            + ' '.join(items)
            + f' (sheet_instances (path "/" (page "1"))))\n')


def stub_schematic():
    # The MCU side of each power-good and CAN logic net: a hierarchical label on a
    # J4 header pin, standing in for the MCU sheet of the full design.
    nets = [(net, OPPOSITE[shape]) for port, shape, kind, net in PSU_PORTS if kind == 'mcu']
    x, y = 76.2, 50.8
    items = [placed(f'ConnectorOwned:Header_{len(nets)}', 'J4', f'Header_{len(nets)}', x, y, len(nets),
                    f'/{FOME_ROOT_UUID}/{uid("sheet/mcu")}')]
    for i, (net, shape) in enumerate(nets):
        items.append(f'(hierarchical_label "{net}" (shape {shape}) (at {round(x - 5.08, 2)} {round(y + 2.54 * i, 2)} 180) '
                     f'(effects (font (size 1.27 1.27)) (justify right)) (uuid "{uid("stub/" + net)}"))')
    return ('(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") '
            f'(uuid "{uid("stub")}") (paper "A4") '
            '(title_block (title "MCU-side stub for the PSU boundary (agent-ee original)")) '
            f'(lib_symbols {header_symbol(len(nets))}) '
            + ' '.join(items) + ')\n')


def board():
    return ('(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0") '
            '(general (thickness 1.6) (legacy_teardrops no)) (paper "A4") '
            '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)) '
            '(setup (pad_to_mask_clearance 0)))\n')


def project():
    return json.dumps({
        'erc': {'erc_exclusions': [], 'meta': {'version': 0}, 'rule_severities': ERC_SEVERITIES},
        'meta': {'filename': NAME + '.kicad_pro', 'version': 3},
        'sheets': [[FOME_ROOT_UUID, 'Root'], [PSU_SHEET_UUID, 'psu'], [uid('sheet/mcu'), 'mcu']],
    }, indent=2, sort_keys=True) + '\n'


def interface():
    contract = json.loads((ROOT.parent / 'capture/psu/interface.json').read_text())
    for invariant in contract['invariants']:
        invariant['source'] = {'path': 'block-contract.json',
                               'locator': 'requirements/' + invariant['requirement'] + '/source'}
    return json.dumps(contract, indent=2, sort_keys=True) + '\n'


def files():
    return {NAME + '.kicad_sch': root_schematic(), STUB_FILE: stub_schematic(),
            NAME + '.kicad_pcb': board(), NAME + '.kicad_pro': project(), 'interface.json': interface()}


def main(argv=None):
    for name, text in files().items():
        (ROOT / name).write_text(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
