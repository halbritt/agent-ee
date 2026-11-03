"""Captured evidence, rather than author choices, establishes connector orientation."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import cpl_rotation as c
from agent_ee.design import DesignError
from agent_ee.design_compile import cpl_policy
from agent_ee.easyeda_footprint import parse_package, symbol_pins, text
from agent_ee.land_pattern import PatternError, extract_footprint, register
from agent_ee.pad_correspondence import derive
from agent_ee.symbol_library import children, parse_sexpr
from tests.land_pattern_fixtures import component, pad, kicad_footprint, SOT_KICAD
from tests.test_pad_correspondence import connector, NETS, policy_inputs, supplier_symbol, _sexpr
from tests.test_cpl_rotation import board, footprint, probe, supplier, diode_supplier, DIODE_PADS


LAYOUT = [('A1', '1', -2, -3), ('A2', '2', 0, -3), ('A3', '3', 2, -3),
          ('B1', '4', 2, 3), ('B2', '5', 0, 3), ('B3', '6', -2, 3)]


def header(*, symmetric=True, named=False):
    positions = [tuple(row) for row in LAYOUT]
    if not symmetric:
        positions[-1] = ('B3', '6', -4, 3)
    mm = lambda value: text(Fraction(str(value))*Fraction('0.254'))
    fp = kicad_footprint('Owned2x3', ' '.join(
        f'(pad "{n}" smd rect (at {mm(x)} {mm(y)}) (size {mm(1)} {mm(2)}) (layers "F.Cu" "F.Mask"))'
        for n, _, x, y in positions))
    raw = component('C900000055', [pad('RECT', x, y, 1, 2, s) for _, s, x, y in positions], origin=(0, 0))
    native_names = {n: 'FUNCTION_' + s for n, s, _, _ in positions}
    if named:
        raw = supplier_symbol(raw, {s: 'FUNCTION_' + s for _, s, _, _ in positions})
    native = extract_footprint(parse_sexpr(fp), side='F.Cu', frame_angle=0, label='owned')
    package = parse_package(raw, 'C900000055', 200)
    return raw, fp, native, package, native_names


def mapping(angle=0):
    numbers = [n for n, _, _, _ in LAYOUT]
    if angle == 180:
        numbers = numbers[3:] + numbers[:3]
    return {str(i+1): [number] for i, number in enumerate(numbers)}


def signal_lug_connector():
    raw, fp, _, _ = connector()
    document = json.loads(raw)
    records = document['packageDetail']['dataStr']['shape']
    for index, record in enumerate(records):
        fields = record.split('~')
        if fields[8] in ('71', '72'):
            fields[8] = {'71': 'VB', '72': 'ID'}[fields[8]]
            records[index] = '~'.join(fields)
    raw = supplier_symbol(json.dumps(document).encode(), {'VB': 'SUPPLY', 'ID': 'IDENTIFY'})
    fp = fp.replace(b'"CASE"', b'"VB"')
    native = extract_footprint(parse_sexpr(fp), side='F.Cu', frame_angle=0, label='owned')
    return raw, fp, native, parse_package(raw, 'C900000017', 200)


class OrientationAuthorityTests(unittest.TestCase):
    def test_symmetric_2x3_declarations_cannot_choose_either_rotation_at_compile(self):
        for angle in (0, 180):
            with self.subTest(angle=angle), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                raw, fp, _, _, names = header()
                design = policy_inputs(root, raw, fp, ref='J1', lcsc='C900000055',
                                       footprint_name='Owned2x3', native_names=names)
                design['parts']['J1']['cpl_registration'] = dict(rotation_degrees=angle, pad_map=mapping(angle))
                with self.assertRaisesRegex(DesignError, r'parts.J1.lcsc:.*\[tool_gap\].*found 2 geometric') as caught:
                    cpl_policy(design, root)
                self.assertNotIn('supply cpl_registration', str(caught.exception))
                self.assertFalse((root / 'cpl/jlc-cpl-policy.json').exists())

    def test_author_map_needs_no_rotation_when_geometry_has_a_single_turn(self):
        raw, _, native, package, _ = header(symmetric=False)
        result = derive(native['pattern'], package['pattern'], declaration=dict(pad_map=mapping()))
        self.assertEqual(result['rotation_degrees'], 0)
        self.assertEqual(result['orientation_proof'], 'unique_copper_geometry')
        with self.assertRaisesRegex(PatternError, 'rotation assertion disagrees'):
            derive(native['pattern'], package['pattern'], declaration=dict(rotation_degrees=180, pad_map=mapping()))

    def test_matching_captured_symbol_names_resolve_symmetric_renumbered_geometry(self):
        raw, _, native, package, names = header(named=True)
        result = derive(native['pattern'], package['pattern'],
            supplier_pins=symbol_pins(raw, 'C900000055')['pins'],
            native_pins=[dict(number=n, name=name) for n, name in names.items()])
        self.assertEqual(result['rotation_degrees'], 0)
        self.assertEqual(result['orientation_proof'], 'captured_symbol_pin_names')
        self.assertEqual(set(result['identity_proof'].values()), {'captured_symbol_name_and_copper'})

    def test_repeated_or_contradictory_symbol_names_cannot_resolve_ambiguity(self):
        raw, _, native, package, names = header(named=True)
        for native_names in ({n: 'ALL_THE_SAME' for n in names},
                             {**names, 'A1': 'WRONG_FUNCTION'}):
            with self.subTest(names=native_names), self.assertRaisesRegex(PatternError, r'\[tool_gap\].*found 2 geometric'):
                derive(native['pattern'], package['pattern'],
                    supplier_pins=symbol_pins(raw, 'C900000055')['pins'],
                    native_pins=[dict(number=n, name=name) for n, name in native_names.items()])

    def test_quarter_turns_agree_with_pin_one_convention_at_90_and_270(self):
        native = extract_footprint(parse_sexpr(kicad_footprint('OwnedSOT', SOT_KICAD)),
                                   side='F.Cu', frame_angle=0, label='owned')['pattern']
        for turn, expected in ((90, 270), (270, 90)):
            with self.subTest(turn=turn):
                _, package = register(native, native, dict(translation='declared_origin', rotation=str(turn), mirror=False))
                old = c.pin1_offset(c.classify_pin1(c._top_view(package))[0], c.classify_pin1(c._top_view(native))[0])
                self.assertEqual(old, expected)
                self.assertEqual(derive(native, package)['rotation_degrees'], expected)

    def test_captured_separate_signal_pin_cannot_be_positionally_renamed_onto_a_repeated_terminal(self):
        raw, _, native, package = signal_lug_connector()
        with self.assertRaisesRegex(PatternError, 'ID is a separate captured symbol pin'):
            derive(native['pattern'], package['pattern'], nets={**NETS, 'VB': 'SUPPLY'},
                   supplier_pins=symbol_pins(raw, 'C900000017')['pins'])


class PackageOrientationTests(unittest.TestCase):
    def test_compile_reads_separate_supplier_symbol_pins_before_positional_assignment(self):
        raw, fp, _, _ = signal_lug_connector()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = policy_inputs(root, raw, fp, native_names={**{p: '~' for p in NETS if p != 'CASE'}, 'VB': 'SUPPLY'})
            design['nets'] = {net: ['J7.' + pin] for pin, net in NETS.items() if pin != 'CASE'}
            design['nets']['SUPPLY'] = ['J7.VB']
            # Preserve both joined native terminals on the connected return net.
            design['nets']['RETURN'] = ['J7.K1', 'J7.K2']
            with self.assertRaisesRegex(DesignError, 'parts.J7.lcsc:.*ID is a separate captured symbol pin'):
                cpl_policy(design, root)

    def test_package_reads_separate_supplier_symbol_pins_before_positional_assignment(self):
        raw, fp, _, _ = signal_lug_connector()
        pads = ' '.join(_sexpr(p) for p in children(parse_sexpr(fp), 'pad'))
        placed = board(footprint('Owned:OwnedConnector', 'J7', 70, 40, 0, pads))
        geometry = [probe('J7', 'Owned:OwnedConnector', 70000000, 40000000, [[67000000, 38000000, 73000000, 43000000]])]
        policy = c.load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
                                               footprint_rules=[], component_overrides=[])).encode())
        records, _, _ = c.evaluate([['J7', '70', '-40', 'Top', '0']], geometry, placed,
            {'J7': 'C900000017'}, policy, {'C900000017': supplier(raw)}, 200,
            pad_nets={'J7': {**NETS, 'VB': 'SUPPLY'}})
        self.assertEqual(records[0]['outcome'], 'unknown')
        self.assertEqual(records[0]['modes'][0]['mode'], 'pad_correspondence')
        self.assertIn('ID is a separate captured symbol pin', records[0]['modes'][0]['message'])

    def test_package_symbol_names_require_matching_sealed_part_and_pad_nets(self):
        raw, fp, _, _, names = header(named=True)
        pads = ' '.join(_sexpr(p) for p in children(parse_sexpr(fp), 'pad'))
        placed = board(footprint('Owned:Owned2x3', 'J1', 70, 40, 0, pads))
        geometry = [probe('J1', 'Owned:Owned2x3', 70000000, 40000000, [[68000000, 38000000, 72000000, 42000000]])]
        policy = c.load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
                                               footprint_rules=[], component_overrides=[])).encode())
        nets = {pin: 'NET_' + pin for pin in names}
        for lcsc, pin_nets, outcome in (('C900000055', nets, 'pass'), ('C900000099', nets, 'unknown'),
                                        ('C900000055', {pin: 'WRONG_NET' for pin in names}, 'unknown')):
            records, _, _ = c.evaluate([['J1', '70', '-40', 'Top', '0']], geometry, placed,
                {'J1': 'C900000055'}, policy, {'C900000055': supplier(raw)}, 200,
                netlist={'J1': dict(lcsc=lcsc, pins=names, nets=pin_nets)}, pad_nets={'J1': nets})
            with self.subTest(lcsc=lcsc, nets=pin_nets):
                self.assertEqual(records[0]['outcome'], outcome, records[0]['modes'])
                if outcome == 'pass':
                    self.assertEqual(records[0]['pin1']['orientation_proof'], 'captured_symbol_pin_names')
                else:
                    self.assertIn('[tool_gap]', records[0]['modes'][0]['message'])

    def test_symmetric_author_policy_cannot_supply_package_orientation(self):
        raw, fp, _, _, _ = header()
        pads = ' '.join(_sexpr(p) for p in children(parse_sexpr(fp), 'pad'))
        placed = board(footprint('Owned:Owned2x3', 'J1', 70, 40, 0, pads))
        geometry = [probe('J1', 'Owned:Owned2x3', 70000000, 40000000, [[68000000, 38000000, 72000000, 42000000]])]
        for angle in (0, 180):
            policy = c.load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
                footprint_rules=[dict(id='r', footprint_prefix='Owned2x3', rotation_degrees=angle)],
                component_overrides=[], pad_registrations={'J1': dict(rotation_degrees=angle, pad_map=mapping(angle))})).encode())
            records, _, _ = c.evaluate([['J1', '70', '-40', 'Top', '0']], geometry, placed,
                {'J1': 'C900000055'}, policy, {'C900000055': supplier(raw)}, 200)
            with self.subTest(angle=angle):
                self.assertEqual(records[0]['outcome'], 'unknown')
                self.assertEqual(records[0]['modes'][0]['mode'], 'pad_correspondence')
                self.assertIn('[tool_gap]', records[0]['modes'][0]['message'])

    def test_polarized_compile_declaration_cannot_replace_pin_one_gate(self):
        pads = DIODE_PADS.format(k='K', a='A')
        raw = diode_supplier('C2', 'K', pin2_name='A')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = policy_inputs(root, raw, kicad_footprint('Chip', pads), ref='D1', lcsc='C2',
                footprint_name='Chip', native_names={'1': 'K', '2': 'A'}, symbol_library='Device', symbol='D')
            design['parts']['D1']['cpl_registration'] = dict(pad_map={'1': ['1'], '2': ['2']})
            with self.assertRaisesRegex(DesignError, 'parts.D1.lcsc: C2: pad registration cannot replace the polarized pin-1 gate'):
                cpl_policy(design, root)

    def test_polarized_package_declaration_cannot_replace_pin_one_gate(self):
        pads = DIODE_PADS.format(k='K', a='A')
        raw = diode_supplier('C2', 'K', pin2_name='A')
        policy = c.load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
            footprint_rules=[], component_overrides=[],
            pad_registrations={'D1': dict(rotation_degrees=0, pad_map={'1': ['1'], '2': ['2']})})).encode())
        placed = board(footprint('Owned:Chip', 'D1', 70, 40, 0, pads))
        geometry = [probe('D1', 'Owned:Chip', 70000000, 40000000, [[67000000, 38000000, 73000000, 42000000]])]
        records, _, _ = c.evaluate([['D1', '70', '-40', 'Top', '0']], geometry, placed, {'D1': 'C2'}, policy,
                                   {'C2': supplier(raw)}, 200)
        self.assertEqual(records[0]['outcome'], 'unknown')
        self.assertEqual(records[0]['modes'][0]['mode'], 'polarity_unknown')
        self.assertIn('cannot replace a polarized pin-1 check', records[0]['modes'][0]['message'])

    def test_back_side_connector_recovers_library_frame_and_bottom_cpl_convention(self):
        raw, fp, _, _ = connector()
        pads = copy.deepcopy(children(parse_sexpr(fp), 'pad'))
        for p in pads:
            for field in p:
                if isinstance(field, list) and field[0] == 'at':
                    field[2] = text(-Fraction(field[2]))
                if isinstance(field, list) and field[0] == 'layers':
                    field[1:] = [{'F.Cu': 'B.Cu', 'B.Cu': 'F.Cu', 'F.Mask': 'B.Mask'}.get(x, x) for x in field[1:]]
        placed = board(footprint('Owned:OwnedConnector', 'J7', 70, 40, 0,
                                ' '.join(_sexpr(p) for p in pads), side='B.Cu'))
        geometry = [probe('J7', 'Owned:OwnedConnector', 70000000, 40000000,
                          [[67000000, 38000000, 73000000, 43000000]], layer='B.Cu')]
        policy = c.load_policy(json.dumps(dict(schema_version=1, policy_id='owned', provenance='synthetic',
                                               footprint_rules=[], component_overrides=[])).encode())
        records, _, _ = c.evaluate([['J7', '70', '-40', 'Bottom', '0']], geometry, placed,
            {'J7': 'C900000017'}, policy, {'C900000017': supplier(raw)}, 200, pad_nets={'J7': NETS})
        self.assertEqual(records[0]['outcome'], 'pass', records[0]['modes'])
        self.assertEqual(records[0]['pin1']['rotation_degrees'], 0)
        self.assertEqual(records[0]['cpl'][3:], ['Bottom', '180.000000'])
