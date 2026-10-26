"""AGENTEE-64 DFA/DFT access predicates, native saved boards and the bring-up plan.

Pure tests use synthetic probe records; native tests build owned boards through
pcbnew, save and reload them, and run the real sandboxed probe through
`checks.run_checks`. The bench round-trip uses the real coordinator, sandbox
and measurement adapter fixture with scripted reviewers; it establishes the
plan-to-sample contract, not physical validation.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent_ee import candidate, checks
from agent_ee.capabilities import describe_check
from agent_ee.dfa_dft import (acceptance_measurements, bench_sample, bringup_plan, connector_orientation,
                              locate, parameters, plan_measurement, testpoint_access, testpoint_coverage)
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain, toolchain_test

MM = 1_000_000
EXPOSED_F = ['F.Cu', 'F.Mask', 'F.Paste']
EXPOSED_B = ['B.Cu', 'B.Mask', 'B.Paste']


def box(cx, cy, hw, hh):
    return {'status': 'proven', 'polygons': [[[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh],
                                              [cx - hw, cy + hh]]]}


def nm(value):
    return int(round(value * MM))


def fp(ref, x, y, *, side='F', rot=0, courtyard=(1, 1), pads=(), courtyard_side=None):
    records = {} if courtyard is None else {courtyard_side or side: box(nm(x), nm(y), nm(courtyard[0]),
                                                                        nm(courtyard[1]))}
    return {'ref': ref, 'uuid': f'uuid-{ref}', 'value': 'owned', 'fpid': '', 'side': side, 'locked': False,
            'position_nm': [nm(x), nm(y)], 'rotation_deg': rot, 'courtyards': records,
            'pads': [{'uuid': f'{ref}-{n}', 'number': n, 'net': net, 'position_nm': [nm(x + dx), nm(y + dy)],
                      'layers': layers, 'attribute': 'SMD'} for n, dx, dy, net, layers in pads]}


def facts(*footprints, outline=None):
    return {'schema': 'agent-ee.placement-probe/1', 'board_sha256': '0' * 64, 'pcbnew_version': 'synthetic',
            'outline': outline or box(20 * MM, 15 * MM, 20 * MM, 15 * MM), 'rule_areas': [],
            'footprints': list(footprints)}


QFN = fp('U1', 10, 10, courtyard=(2.5, 2.5), pads=[('1', -2, 0, '+1V8', ['F.Cu']), ('EP', 0, 0, 'GND', EXPOSED_F)])
RAILS = [{'id': '3v3', 'net': '+3V3', 'return_net': 'GND', 'nominal_v': 3.3, 'tolerance_v': 0.1},
         {'id': 'vbus', 'net': 'VBUS', 'return_net': 'GND', 'nominal_v': 5, 'tolerance_v': 0.25}]


def coverage_params(**overrides):
    params = {'criterion': 'testpoint-coverage/1', 'test_points': ['TP1', 'TP2'],
              'probe_pads': [{'reference': 'J1', 'pad': '1'}], 'rails': copy.deepcopy(RAILS),
              'first_power': {'rail': 'vbus', 'current_limit_a': 0.1}}
    return {**params, **overrides}


def board_facts(tp1=(20, 20), tp1_side='F'):
    return facts(QFN,
                 fp('TP1', *tp1, side=tp1_side, courtyard=(0.75, 0.75),
                    pads=[('1', 0, 0, '+3V3', EXPOSED_F if tp1_side == 'F' else EXPOSED_B)]),
                 fp('TP2', 25, 20, courtyard=(0.75, 0.75), pads=[('1', 0, 0, 'GND', EXPOSED_F)]),
                 fp('J1', 37, 15, courtyard=(3, 4), pads=[('1', 0, -1, 'VBUS', EXPOSED_F), ('2', 0, 1, 'GND', EXPOSED_F)]),
                 fp('R1', 30, 5, courtyard=None, pads=[('1', -0.5, 0, '+3V3', EXPOSED_F), ('2', 0.5, 0, 'OUT', EXPOSED_F)]))


class ConnectorOrientationTests(unittest.TestCase):
    def evaluate(self, declaration, **placed):
        return connector_orientation(facts(fp('J1', placed.pop('x', 37), placed.pop('y', 15), **placed)),
                                     [{'reference': 'J1', **declaration}])

    def test_from_left_on_right_edge_facing_inward_is_a_defect_recommending_x_plus(self):
        result = self.evaluate({'insertion': 'from_left'})
        self.assertEqual(result['status'], 'defect')
        violation, = result['defects'][0]['violations']
        self.assertEqual((violation['kind'], violation['faces'], violation['recommended']), ('facing', 'x-', 'x+'))
        self.assertEqual(self.evaluate({'insertion': 'from_left'}, rot=180)['status'], 'pass')
        self.assertEqual(self.evaluate({'insertion': 'from_right'})['status'], 'pass')

    def test_no_declared_or_derivable_direction_is_unknown(self):
        result = self.evaluate({})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['unknown'][0]['reasons'], ['no declared or derivable insertion direction'])
        self.assertEqual(self.evaluate({'cable_insertion_mm': ['1', '-1']})['status'], 'unknown')

    def test_cable_insertion_point_derives_the_local_axis(self):
        result = self.evaluate({'cable_insertion_mm': ['-4.5', '0.5']})
        self.assertEqual(result['connectors'][0]['measured']['insertion'], 'from_left')
        self.assertEqual(result['status'], 'defect')

    def test_vertical_insertion_needs_the_matching_side_and_no_edge(self):
        self.assertEqual(self.evaluate({'insertion': 'from_top'}, x=20)['status'], 'pass')
        result = self.evaluate({'insertion': 'from_top'}, side='B')
        self.assertEqual(result['defects'][0]['violations'][0]['kind'], 'insertion_side')

    def test_unprovable_geometry_is_unknown(self):
        self.assertEqual(self.evaluate({'insertion': 'from_left'}, rot=45)['status'], 'unknown')
        corner = self.evaluate({'insertion': 'from_right'}, x=37, y=27)
        self.assertEqual(corner['status'], 'unknown')
        self.assertIn('equidistant', corner['unknown'][0]['reasons'][0])
        open_outline = connector_orientation(facts(fp('J1', 37, 15), outline={'status': 'outline_unproven'}),
                                             [{'reference': 'J1', 'insertion': 'from_right'}])
        self.assertEqual(open_outline['status'], 'unknown')
        missing = connector_orientation(facts(fp('J2', 37, 15)), [{'reference': 'J1', 'insertion': 'from_right'}])
        self.assertEqual(missing['unknown'][0]['reasons'], ['declared reference missing'])


class TestPointAccessTests(unittest.TestCase):
    def test_test_point_under_qfn_courtyard_is_a_defect_and_opposite_side_passes(self):
        under = testpoint_access(board_facts(tp1=(11, 11)), ['TP1'])
        self.assertEqual(under['status'], 'defect')
        self.assertEqual(under['defects'][0]['pads'][0]['sides'][0]['under_courtyards'], ['U1'])
        self.assertEqual(testpoint_access(board_facts(tp1=(11, 11), tp1_side='B'), ['TP1'])['status'], 'pass')
        self.assertEqual(testpoint_access(board_facts(), ['TP1', 'TP2'])['status'], 'pass')

    def test_footprint_without_courtyard_is_an_info_finding(self):
        result = testpoint_access(board_facts(), ['TP1'])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual([(i['kind'], i['ref']) for i in result['info']], [('missing_courtyard', 'R1')])

    def test_boundary_unexposed_unproven_and_missing_records(self):
        self.assertEqual(testpoint_access(board_facts(tp1=(12.5, 11)), ['TP1'])['status'], 'unknown')
        hidden = facts(fp('TP1', 20, 20, pads=[('1', 0, 0, '+3V3', ['F.Cu'])]))
        self.assertEqual(testpoint_access(hidden, ['TP1'])['status'], 'defect')
        arc = facts(fp('TP1', 20, 20, pads=[('1', 0, 0, '+3V3', EXPOSED_F)]),
                    {**fp('U2', 20, 20), 'courtyards': {'F': {'status': 'courtyard_unproven'}}})
        self.assertEqual(testpoint_access(arc, ['TP1'])['status'], 'unknown')
        self.assertEqual(testpoint_access(board_facts(), ['TP9'])['status'], 'unknown')

    def test_exact_point_location(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertEqual([locate(p, square) for p in ((5, 5), (10, 5), (11, 5), (0, 0))],
                         ['inside', 'boundary', 'outside', 'boundary'])


class CoverageAndPlanTests(unittest.TestCase):
    def evaluate(self, board, params, prior=None, scope=None):
        spec = {'id': 'dft', 'kind': 'dfa_dft', 'params': params}
        scope = scope or {'checks': [spec], 'baseline': {'requirements': []}}
        if spec not in scope['checks']:
            scope['checks'].append(spec)
        items, gaps = acceptance_measurements(scope, spec, params)
        probes, defects, unknown, info, notes = testpoint_coverage(board, params, items, gaps)
        plan, plan_unknown = bringup_plan(board, params, items, probes, prior or {}, 'cand', 'dft')
        return probes, defects, unknown + plan_unknown, plan

    def test_rail_without_test_point_or_exposed_pad_is_a_defect_naming_the_net(self):
        params = coverage_params(rails=RAILS + [{'id': '1v8', 'net': '+1V8', 'return_net': 'GND',
                                                 'nominal_v': 1.8, 'tolerance_v': 0.05}])
        probes, defects, unknown, _ = self.evaluate(board_facts(), params)
        self.assertEqual([d['net'] for d in defects], ['+1V8'])
        self.assertIn('+1V8', defects[0]['reason'])
        self.assertEqual(unknown, [])
        record = next(p for p in probes if p['net'] == '+1V8')
        self.assertEqual(record['undeclared_exposed_pads'], [])

    def test_covered_rails_pass_and_undeclared_exposed_pads_are_only_diagnostics(self):
        probes, defects, unknown, plan = self.evaluate(board_facts(), coverage_params())
        self.assertEqual((defects, unknown), ([], []))
        self.assertEqual(plan['status'], 'complete')
        self.assertEqual({p['net']: p['accessible'][0]['reference'] for p in probes},
                         {'+3V3': 'TP1', 'GND': 'TP2', 'VBUS': 'J1'})
        covered_only_by_r1 = coverage_params(test_points=['TP2'])
        probes, defects, _, _ = self.evaluate(board_facts(), covered_only_by_r1)
        self.assertEqual([d['net'] for d in defects], ['+3V3'])
        self.assertEqual(next(p for p in probes if p['net'] == '+3V3')['undeclared_exposed_pads'],
                         [{'reference': 'R1', 'pad': '1', 'fpid': ''}, {'reference': 'TP1', 'pad': '1', 'fpid': ''}])

    def test_test_point_under_a_courtyard_does_not_cover_its_net(self):
        _, defects, _, _ = self.evaluate(board_facts(tp1=(11, 11)), coverage_params())
        self.assertEqual([d['net'] for d in defects], ['+3V3'])

    def test_current_limit_and_nominal_come_only_from_scope(self):
        rails = copy.deepcopy(RAILS)
        del rails[1]['nominal_v']
        del rails[0]['tolerance_v']
        _, defects, unknown, plan = self.evaluate(board_facts(), coverage_params(rails=rails,
                                                                                  first_power={'rail': 'vbus'}))
        self.assertEqual(defects, [])
        self.assertEqual(plan['status'], 'unknown')
        self.assertEqual((plan['first_power']['current_limit_a'], plan['first_power']['supply_v']), (None, None))
        self.assertEqual(sorted(plan['first_power']['missing']), ['current_limit_a', 'nominal_v'])
        fields = sorted((u.get('rail', ''), u['field']) for u in unknown)
        self.assertEqual(fields, [('', 'current_limit_a'), ('', 'nominal_v'), ('3v3', 'tolerance_v'),
                                  ('vbus', 'nominal_v')])
        rail = next(m for m in plan['measurements'] if m['id'] == 'rail:3v3')
        self.assertEqual((rail['expected']['nominal'], rail['expected']['tolerance']), (3.3, None))

    def test_acceptance_measured_nets_need_access_and_plan_ids_round_trip(self):
        response = {'id': 'response', 'kind': 'measurements', 'params': {
            'limits': {'output': {'unit': 'V', 'condition': 'load', 'min': 0, 'max': 10}}}}
        rc = {'id': 'rc', 'kind': 'rc_transient', 'params': {
            'input_net': 'VBUS', 'output_net': 'OUT', 'ground_net': 'GND',
            'measurements': [{'name': 'V_1ms', 'at_ms': 1, 'min': 0.5}, {'name': 'free', 'at_ms': 2}]}}
        scope = {'checks': [response, rc], 'baseline': {'requirements': [
            {'id': 'R1', 'checks': ['response']}, {'id': 'R2', 'checks': ['rc']}]}}
        params = coverage_params(measurements=[{'check': 'response', 'metric': 'output', 'net': 'OUT',
                                                'return_net': 'GND'}])
        prior = {'response': {'candidate_id': 'cand', 'execution': 'ok', 'details': {'metrics': {
                     'output': {'value': 5, 'unit': 'V', 'condition': 'load'}}}},
                 'rc': {'candidate_id': 'cand', 'execution': 'ok', 'details': {'metrics': {
                     'v_1ms': {'value': 0.63, 'unit': 'V', 'condition': 'ideal-unloaded-1.0V-step-at-1.0ms'}}}}}
        probes, defects, unknown, plan = self.evaluate(board_facts(), params, prior, scope)
        self.assertEqual([d['net'] for d in defects], ['OUT'])
        self.assertEqual(next(p for p in probes if p['net'] == 'OUT')['roles'],
                         ['measure:rc:v_1ms', 'measure:response:output'])
        with_out = coverage_params(measurements=params['measurements'],
                                   probe_pads=params['probe_pads'] + [{'reference': 'R1', 'pad': '2'}])
        _, defects, unknown, plan = self.evaluate(board_facts(), with_out, prior, copy.deepcopy(scope))
        self.assertEqual((defects, unknown), ([], []))
        self.assertEqual([m['id'] for m in plan['measurements']],
                         ['rail:3v3', 'rail:vbus', 'response:output', 'rc:v_1ms'])
        for mid in ('response:output', 'rc:v_1ms'):
            sample = bench_sample(plan, mid, 1.0, 0.1)
            self.assertEqual(plan_measurement(plan, 'cand', sample), mid)
        self.assertEqual(bench_sample(plan, 'rc:v_1ms', 1.0, 0.1)['condition'], 'ideal-unloaded-1.0V-step-at-1.0ms')
        with self.assertRaisesRegex(ValueError, 'different candidate'):
            plan_measurement(plan, 'other', bench_sample(plan, 'rc:v_1ms', 1.0, 0.1))
        with self.assertRaisesRegex(ValueError, 'no executed bench binding'):
            bench_sample(plan, 'rail:3v3', 3.3, 0.01)
        failed = {**prior, 'rc': {'candidate_id': 'cand', 'execution': 'failed', 'details': {}}}
        _, _, unknown, plan = self.evaluate(board_facts(), with_out, failed, copy.deepcopy(scope))
        self.assertEqual([u['kind'] for u in unknown], ['bench_binding_unavailable'])
        undeclared = coverage_params()
        _, _, unknown, _ = self.evaluate(board_facts(), undeclared, prior, copy.deepcopy(scope))
        self.assertEqual(unknown[0]['kind'], 'measured_net_undeclared')

    def test_declaration_validation(self):
        bad = [{'criterion': 'other'},
               {'criterion': 'connector-orientation/1', 'connectors': []},
               {'criterion': 'connector-orientation/1', 'connectors': [{'reference': 'J1', 'insertion': 'sideways'}]},
               {'criterion': 'connector-orientation/1',
                'connectors': [{'reference': 'J1', 'insertion': 'from_left', 'cable_insertion_mm': ['1', '0']}]},
               {'criterion': 'testpoint-access/1', 'test_points': []},
               {'criterion': 'testpoint-access/1', 'test_points': ['TP1', 'TP1']},
               coverage_params(first_power={'rail': 'nope'}),
               coverage_params(first_power={'rail': 'vbus', 'current_limit_a': 0}),
               coverage_params(rails=[{**RAILS[0], 'return_net': '+3V3'}]),
               coverage_params(rails=[{**RAILS[0], 'nominal_v': True}]),
               coverage_params(extra=1)]
        for params in bad:
            with self.subTest(params=params), self.assertRaises(ValueError):
                parameters(params)
        self.assertEqual(parameters(coverage_params()), 'testpoint-coverage/1')

    def test_capability_predicates_prerequisites_and_gaps(self):
        files = {'board.kicad_pcb': '0' * 64}
        response = {'id': 'response', 'kind': 'measurements', 'params': {
            'limits': {'output': {'unit': 'V', 'condition': 'load', 'max': 10}}}}
        dft = {'id': 'dft', 'kind': 'dfa_dft', 'params': coverage_params(
            measurements=[{'check': 'response', 'metric': 'output', 'net': 'OUT', 'return_net': 'GND'}])}
        scope = {'board': 'board.kicad_pcb', 'checks': [response, dft],
                 'baseline': {'requirements': [{'id': 'R1', 'checks': ['response']}]}}
        result = describe_check(dft, check_specs=scope['checks'], files=files, scope=scope)
        self.assertEqual(result['status'], 'supported', result)
        self.assertEqual(result['predicates'], ['dfa-dft:testpoint-coverage/1'])
        self.assertEqual(result['prerequisites'], [{'check_id': 'response', 'kind': 'measurements'}])
        gap = {**dft, 'params': coverage_params()}
        scope['checks'][1] = gap
        refused = describe_check(gap, check_specs=scope['checks'], files=files, scope=scope)
        self.assertEqual(refused['status'], 'unsupported')
        self.assertIn('no declared probe net', refused['unsupported_reasons'][0]['message'])


# ------------------------------------------------------------------ native

def save_board(path, footprints, outline=((0, 0), (40, 0), (40, 30), (0, 30))):
    """Owned board through pcbnew: segment outline, square courtyards, SMD pads with explicit layers."""
    import pcbnew as k

    def vec(x, y):
        return k.VECTOR2I(int(round(x * MM)), int(round(y * MM)))

    def line(parent, layer, a, b):
        shape = k.PCB_SHAPE(parent)
        shape.SetShape(k.SHAPE_T_SEGMENT)
        shape.SetLayer(layer)
        shape.SetStart(vec(*a))
        shape.SetEnd(vec(*b))
        shape.SetWidth(50000)
        parent.Add(shape)
    board = k.BOARD()
    for i, a in enumerate(outline):
        line(board, k.Edge_Cuts, a, outline[(i + 1) % len(outline)])
    nets = {}
    for spec in footprints:
        fpr = k.FOOTPRINT(board)
        fpr.SetReference(spec['ref'])
        fpr.SetValue('owned')
        board.Add(fpr)
        if spec.get('courtyard'):
            hw, hh = spec['courtyard']
            corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
            for i, a in enumerate(corners):
                line(fpr, k.F_CrtYd, a, corners[(i + 1) % 4])
        for number, dx, dy, net, exposed in spec['pads']:
            pad = k.PAD(fpr)
            pad.SetNumber(number)
            pad.SetShape(k.PAD_SHAPE_CIRCLE if spec['ref'].startswith('TP') else k.PAD_SHAPE_RECT)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(vec(1, 1))
            layers = k.LSET()
            for layer in ((k.F_Cu, k.F_Mask, k.F_Paste) if exposed else (k.F_Cu,)):
                layers.AddLayer(layer)
            pad.SetLayerSet(layers)
            pad.SetPosition(vec(dx, dy))
            if net not in nets:
                nets[net] = k.NETINFO_ITEM(board, net)
                board.Add(nets[net])
            pad.SetNet(nets[net])
            fpr.Add(pad)
        if spec.get('flip'):
            fpr.Flip(vec(0, 0), False)
        fpr.SetPosition(vec(spec['x'], spec['y']))
        fpr.SetOrientationDegrees(spec.get('rot', 0))
    k.SaveBoard(str(path), board)
    return path


def native_layout(tp1=(20, 20), tp1_flip=False, j1_rot=0, extra=()):
    return [{'ref': 'U1', 'x': 10, 'y': 10, 'courtyard': (2.5, 2.5),
             'pads': [('1', -2, 0, '+1V8', False), ('EP', 0, 0, 'GND', True)]},
            {'ref': 'TP1', 'x': tp1[0], 'y': tp1[1], 'flip': tp1_flip, 'courtyard': (0.75, 0.75),
             'pads': [('1', 0, 0, '+3V3', True)]},
            {'ref': 'TP2', 'x': 25, 'y': 20, 'courtyard': (0.75, 0.75), 'pads': [('1', 0, 0, 'GND', True)]},
            {'ref': 'TP3', 'x': 25, 'y': 25, 'courtyard': (0.75, 0.75), 'pads': [('1', 0, 0, 'OUT', True)]},
            {'ref': 'J1', 'x': 37, 'y': 15, 'rot': j1_rot, 'courtyard': (3, 4),
             'pads': [('1', 0, -1, 'VBUS', True), ('2', 0, 1, 'GND', True)]},
            {'ref': 'R1', 'x': 30, 'y': 5, 'pads': [('1', -1, 0, '+3V3', True), ('2', 1, 0, 'OUT', True)]},
            *extra]


class NativeSavedBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-dfa-dft-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0

    def extract(self, layout):
        from agent_ee.placement_probe import extract
        self.count += 1
        return extract(save_board(self.root / f'board-{self.count}.kicad_pcb', layout))

    def test_saved_pcbnew_geometry_matches_the_declared_access_cases(self):
        control = self.extract(native_layout())
        tp1 = next(f for f in control['footprints'] if f['ref'] == 'TP1')
        self.assertEqual(tp1['pads'][0]['layers'], ['F.Cu', 'F.Mask', 'F.Paste'])
        self.assertEqual(tp1['pads'][0]['attribute'], 'SMD')
        self.assertEqual(testpoint_access(control, ['TP1', 'TP2', 'TP3'])['status'], 'pass')
        under = testpoint_access(self.extract(native_layout(tp1=(11, 11))), ['TP1'])
        self.assertEqual(under['defects'][0]['pads'][0]['sides'][0]['under_courtyards'], ['U1'])
        flipped = self.extract(native_layout(tp1=(11, 11), tp1_flip=True))
        tp1 = next(f for f in flipped['footprints'] if f['ref'] == 'TP1')
        self.assertEqual((tp1['side'], tp1['pads'][0]['layers']), ('B', ['B.Cu', 'B.Mask', 'B.Paste']))
        self.assertEqual(testpoint_access(flipped, ['TP1'])['status'], 'pass')
        self.assertEqual([i['ref'] for i in testpoint_access(control, ['TP1'])['info']], ['R1'])

    def test_saved_usb_c_on_the_right_edge_facing_inward(self):
        declared = [{'reference': 'J1', 'insertion': 'from_left'}]
        inward = connector_orientation(self.extract(native_layout()), declared)
        self.assertEqual(inward['defects'][0]['violations'][0]['recommended'], 'x+')
        self.assertEqual(connector_orientation(self.extract(native_layout(j1_rot=180)), declared)['status'], 'pass')
        quarter = connector_orientation(self.extract(native_layout(j1_rot=90)), declared)
        self.assertEqual(quarter['connectors'][0]['measured']['faces'], 'y+')
        self.assertEqual(quarter['status'], 'defect')

    def test_saved_rail_without_access_names_the_net(self):
        params = coverage_params(test_points=['TP1', 'TP2', 'TP3'], rails=RAILS + [
            {'id': '1v8', 'net': '+1V8', 'return_net': 'GND', 'nominal_v': 1.8, 'tolerance_v': 0.05}])
        board = self.extract(native_layout())
        probes, defects, unknown, _, _ = testpoint_coverage(board, params, [], [])
        self.assertEqual(([d['net'] for d in defects], unknown), (['+1V8'], []))


DFT_CHECKS = [
    {'id': 'connector', 'kind': 'dfa_dft', 'params': {'criterion': 'connector-orientation/1',
                                                      'connectors': [{'reference': 'J1', 'insertion': 'from_left'}]}},
    {'id': 'access', 'kind': 'dfa_dft', 'params': {'criterion': 'testpoint-access/1', 'test_points': ['TP1', 'TP2']}},
    {'id': 'coverage', 'kind': 'dfa_dft', 'params': coverage_params(test_points=['TP1', 'TP2', 'TP3'])}]


class NativeCheckTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-dfa-dft-checks-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = 0

    def run_scope(self, write_board, specs):
        self.runs += 1
        source = self.root / f'source-{self.runs}'
        source.mkdir()
        write_board(source / 'board.kicad_pcb')
        (source / 'requirements.md').write_text('Owned AGENTEE-64 access requirements.\n')
        requirements = [{'id': f"R-{s['id']}", 'statement': s['id'], 'intent': 'acceptance',
                         'source': {'path': 'design/requirements.md', 'locator': s['id']}, 'checks': [s['id']]}
                        for s in specs]
        scope = {'schema': 'agent-ee.scope/3', 'inputs': [{'path': str(source), 'at': 'design'}],
                 'board': 'design/board.kicad_pcb', 'requirements': ['design/requirements.md'], 'checks': specs,
                 'baseline': {'requirements': requirements, 'targets': ['design/requirements.md'],
                              'reference_architectures': ['design/requirements.md']}}
        path = self.root / f'scope-{self.runs}.json'
        path.write_text(json.dumps(scope))
        run = self.root / f'run-{self.runs}'
        run.mkdir()
        candidate.capture(path, run)
        return run, {r['id']: r for r in checks.run_checks(run, time.monotonic() + 600)}

    def test_sandboxed_checks_emit_judgments_and_a_bound_plan(self):
        run, results = self.run_scope(lambda p: save_board(p, native_layout(tp1=(11, 11))), DFT_CHECKS)
        self.assertEqual((results['connector']['execution'], results['connector']['outcome']), ('ok', 'defect'))
        self.assertEqual(results['connector']['details']['defects'][0]['violations'][0]['recommended'], 'x+')
        self.assertEqual((results['access']['execution'], results['access']['outcome']), ('ok', 'defect'))
        coverage = results['coverage']
        self.assertEqual((coverage['execution'], coverage['outcome']), ('ok', 'defect'), coverage)
        self.assertEqual([d['net'] for d in coverage['details']['defects']], ['+3V3'])
        plan = read_json(run / 'work/coverage/bringup_plan.json')
        manifest = read_json(run / 'candidate.json')
        self.assertEqual((plan['candidate_id'], plan['board_sha256']),
                         (manifest['candidate_id'], manifest['files']['design/board.kicad_pcb']))
        self.assertEqual(plan['first_power'], {**plan['first_power'], 'rail': 'vbus', 'supply_v': 5,
                                               'current_limit_a': 0.1, 'status': 'declared'})
        self.assertIn('work/coverage/bringup_plan.json', coverage['evidence'])
        self.assertEqual(coverage['details']['bringup_plan']['sha256'], digest(run / 'work/coverage/bringup_plan.json'))
        _, passing = self.run_scope(lambda p: save_board(p, native_layout(j1_rot=180)), DFT_CHECKS)
        self.assertEqual({cid: (r['execution'], r['outcome']) for cid, r in passing.items()},
                         {cid: ('ok', 'pass') for cid in passing})

    def test_board_without_probe_data_fails_execution(self):
        _, results = self.run_scope(lambda p: p.write_text('unreadable board bytes\n'), DFT_CHECKS[:1])
        self.assertEqual((results['connector']['execution'], results['connector']['outcome']), ('failed', 'unknown'))

    def test_undeclared_values_leave_the_check_unknown(self):
        spec = {'id': 'coverage', 'kind': 'dfa_dft', 'params': coverage_params(
            test_points=['TP1', 'TP2', 'TP3'], first_power={'rail': 'vbus'})}
        run, results = self.run_scope(lambda p: save_board(p, native_layout()), [spec])
        self.assertEqual((results['coverage']['execution'], results['coverage']['outcome']), ('unsupported', 'unknown'))
        self.assertIsNone(read_json(run / 'work/coverage/bringup_plan.json')['first_power']['current_limit_a'])


class BenchRoundTripTests(unittest.TestCase):
    """The plan's bench-bound measurement IDs ingest as samples of the same candidate."""
    import test_campaign as _fixture
    setUp = _fixture.CampaignIntegrationTests.setUp
    agent = _fixture.CampaignIntegrationTests.agent

    @toolchain_test
    def test_plan_measurement_round_trips_through_bench_ingestion(self):
        from agent_ee.run_record import run_review
        from agent_ee.empirical import ingest
        require_toolchain(native=True)
        source = self.root / 'source'
        save_board(source / 'board.kicad_pcb', native_layout())
        write_json(source / 'design.json', {'value': 5})
        scope = read_json(self.root / 'scope.json')
        scope['board'] = 'design/board.kicad_pcb'
        scope['checks'].append({'id': 'dft', 'kind': 'dfa_dft', 'params': coverage_params(
            test_points=['TP1', 'TP2', 'TP3'],
            measurements=[{'check': 'response', 'metric': 'output', 'net': 'OUT', 'return_net': 'GND'}])})
        scope['baseline']['requirements'].append({'id': 'R2', 'statement': 'Probe access and bring-up plan',
                                                  'source': {'path': 'design/requirements', 'locator': 'R2'},
                                                  'checks': ['dft']})
        write_json(self.root / 'scope.json', scope)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', self.root / 'initial', 120, host=self.host), 0)
        run = self.root / 'initial'
        dft = read_json(run / 'checks/dft.json')
        self.assertEqual((dft['execution'], dft['outcome']), ('ok', 'pass'), dft)
        plan = read_json(run / 'work/dft/bringup_plan.json')
        cid = read_json(run / 'candidate.json')['candidate_id']
        sample = bench_sample(plan, 'response:output', 6, 0.5)
        write_json(self.root / 'raw.json', {'samples': [sample]})
        report = {'schema': 'agent-ee.bench/1', 'candidate_id': plan['candidate_id'], 'prototype': 'synthetic',
                  'instrument': 'synthetic', 'instrument_calibration': 'synthetic', 'procedure': 'bring-up plan',
                  'observed_utc': '2026-10-17',
                  'artifacts': [{'path': 'raw.json', 'sha256': digest(self.root / 'raw.json')}],
                  'observations': [{**sample, 'artifact': 'raw.json', 'locator': '0'}]}
        write_json(self.root / 'bench.json', report)
        bundle = ingest(run, self.root / 'bench.json', self.root / 'bundle', host=self.host)
        observation, = bundle['observations']
        self.assertEqual(observation['candidate_id'], cid)
        self.assertEqual(plan_measurement(plan, observation['candidate_id'], observation), 'response:output')
        write_json(self.root / 'bench-other.json', {**report, 'candidate_id': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'measured candidate'):
            ingest(run, self.root / 'bench-other.json', self.root / 'bundle-other', host=self.host)


if __name__ == '__main__':
    unittest.main()
