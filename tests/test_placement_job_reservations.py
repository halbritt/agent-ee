"""Declared overhang and native drill-rule regressions, on original fixtures."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import placement_job as job
from tests.toolchain import require_toolchain
from tests.test_placement_job import MM, _box, build_board, intent, groups
from tests.test_placement_job_clearance import evaluate


class ReservationTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        evidence = os.environ.get('AGENT_EE_RESERVATION_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=evidence))
        if not evidence:
            self.addCleanup(shutil.rmtree, self.root)

    def board(self, moving='round', fixed='copper', q=0, fixed_q=0, body=False):
        import pcbnew as k
        board = k.BOARD()
        for edge in _box(k, board, k.Edge_Cuts, 0, 0, 30 * MM, 30 * MM):
            board.Add(edge)
        for ref, x, kind, rotation in (('M', 10, moving, q), ('F', 14, fixed, fixed_q)):
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            board.Add(fp)
            pad = k.PAD(fp)
            fp.Add(pad)
            pad.SetNumber('1')
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            if kind == 'copper':
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_CIRCLE)
                pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(MM, MM))
                pad.SetLayerSet(layers)
            else:
                pad.SetAttribute(k.PAD_ATTRIB_NPTH)
                slot = kind == 'slot'
                pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_OVAL if slot else k.PAD_SHAPE_CIRCLE)
                pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(2 * MM if slot else MM, MM))
                pad.SetDrillSize(k.VECTOR2I(2 * MM if slot else MM, MM))
                pad.SetDrillShape(k.PAD_DRILL_SHAPE_OBLONG if slot else k.PAD_DRILL_SHAPE_CIRCLE)
                pad.SetLayerSet(pad.UnplatedHoleMask())
            fp.SetPosition(k.VECTOR2I(x * MM, 10 * MM))
            fp.SetOrientationDegrees(rotation * 90)
            if body and ref == 'M':
                for shape in _box(k, fp, k.F_Fab, x * MM - 2 * MM, 8 * MM,
                                  x * MM + 2 * MM, 12 * MM):
                    fp.Add(shape)
        path = self.root / 'board.kicad_pcb'
        k.SaveBoard(str(path), board)
        self.project = {'board': {'design_settings': {'rules': {
            'min_clearance': 0.15, 'min_hole_clearance': 0.2, 'min_hole_to_hole': 0.4}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.15}]}}
        path.with_suffix('.kicad_pro').write_text(json.dumps(self.project))
        fixed_intent = {'fixed_poses': [{'ref': 'F', 'x': 14, 'y': 10, 'rot': fixed_q * 90, 'side': 'F'}]}
        return path, fixed_intent

    def native(self, path, problem, moves, label):
        import pcbnew as k
        result = evaluate(problem, [moves])[0]
        directory = self.root / label
        directory.mkdir()
        board = k.LoadBoard(str(path))
        for fp in board.GetFootprints():
            if fp.m_Uuid.AsString() in moves:
                x, y, q = moves[fp.m_Uuid.AsString()]
                fp.SetOrientationDegrees(q * 90)
                fp.SetPosition(k.VECTOR2I(x, y))
        k.SaveBoard(str(directory / 'board.kicad_pcb'), board)
        shutil.copyfile(path.with_suffix('.kicad_pro'), directory / 'board.kicad_pro')
        report, execution = job.drc(directory / 'board.kicad_pcb', directory)
        self.assertEqual(execution['exit_code'], 0)
        receipt = {'engine': result, 'native': report, 'execution': execution}
        (directory / 'agreement.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print('RESERVATION_CONTROL ' + json.dumps({'test': self.id(), 'label': label,
              'engine_feasible': result['feasible'], 'native_exit': execution['exit_code'],
              'native_hole_items': [v for v in report['violations'] if v['type'] in (
                  'hole_clearance', 'hole_to_hole')], 'receipt': str(directory / 'agreement.json')}), flush=True)
        return result, report

    def hole_controls(self, moving, fixed, q, fixed_q=0):
        path, fixed_intent = self.board(moving, fixed, q, fixed_q)
        problem, _ = job.capture(path, fixed_intent, {'groups': []})
        m = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
        f = next(f for f in problem['footprints'] if f['native_ref'] == 'F')
        self.assertEqual(m['copper_local_nm'], [])
        self.assertEqual(len(m['holes_local_nm']), 1)
        self.assertEqual(m['holes_local_nm'][0]['clearance_nm'], 200_000)
        self.assertEqual(problem['rules']['hole_to_hole_clearance_nm'], 400_000)
        self.assertEqual(f['occupancy']['status'], 'none', 'bodyless mark remains bodyless')
        mx = 1.0 if moving == 'slot' and q % 2 == 0 else 0.5
        fx = 1.0 if fixed == 'slot' and fixed_q % 2 == 0 else 0.5
        rule = 'hole_clearance' if fixed == 'copper' else 'hole_to_hole'
        for index, gap in enumerate((0.05, 0.3, 0.6)):
            bad = gap < (0.2 if fixed == 'copper' else 0.4)
            x = round((14 - mx - fx - gap) * MM)
            result, report = self.native(path, problem, {m['uuid']: [x, 10 * MM, q]}, f'seat-{index}')
            native_bad = any(v['type'] == rule for v in report['violations'])
            # Native 10.0.7's drilled-hole spacing provider does not emit
            # hole_to_hole for slots. Keep their board-minimum reservation
            # conservative, without inventing a native violation.
            native_expected = bad and (fixed == 'copper' or moving == fixed == 'round')
            self.assertEqual(native_bad, native_expected, report)
            self.assertEqual(result['feasible'], not bad, result)

    def test_round_and_slot_hole_to_copper_under_all_quarter_turns(self):
        for kind in ('round', 'slot'):
            for q in range(4):
                with self.subTest(kind=kind, q=q):
                    sub = self.root / f'{kind}-{q}'
                    sub.mkdir()
                    previous, self.root = self.root, sub
                    try:
                        self.hole_controls(kind, 'copper', q)
                    finally:
                        self.root = previous

    def test_round_and_slot_hole_to_hole_under_rotation(self):
        for a, b, q, fixed_q in (('round', 'round', 0, 0), ('slot', 'round', 1, 0),
                                  ('round', 'slot', 0, 1), ('slot', 'slot', 2, 2)):
            with self.subTest(a=a, b=b, q=q, fixed_q=fixed_q):
                sub = self.root / f'{a}-{b}-{q}-{fixed_q}'
                sub.mkdir()
                previous, self.root = self.root, sub
                try:
                    self.hole_controls(a, b, q, fixed_q)
                finally:
                    self.root = previous

    def test_reviewer_r1_h1_false_feasible_regression(self):
        path = build_board(self.root / 'board.kicad_pcb')
        path.with_suffix('.kicad_pro').write_text(json.dumps({
            'board': {'design_settings': {'rules': {'min_clearance': 0.2, 'min_hole_clearance': 0.2}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.2}]}}))
        problem, _ = job.capture(path, intent(), groups())
        ids = {f['native_ref']: f['uuid'] for f in problem['footprints']}
        for index, (x, bad) in enumerate(((14_700_000, True), (14_300_000, False))):
            result, report = self.native(path, problem, {ids['R1']: [x, 9 * MM, 0],
                ids['C1']: [6 * MM, 3 * MM, 0], ids['U1']: [6 * MM, 9 * MM, 0]}, f'R1-H1-{index}')
            items = [v for v in report['violations'] if v['type'] == 'hole_clearance']
            self.assertEqual(bool(items), bad, report)
            self.assertEqual(result['feasible'], not bad, result)

    def test_overhang_only_at_declared_edge_and_for_declared_component(self):
        path, fixed = self.board('copper', body=True)
        source_bytes = path.read_bytes()
        original, _ = job.capture(path, fixed, {'groups': []})
        occupancy = next(f for f in original['footprints'] if f['native_ref'] == 'M')['occupancy']
        for edge, pose, wrong in (
                ('west', [750_000, 10 * MM, 0], [29_250_000, 10 * MM, 0]),
                ('east', [29_250_000, 10 * MM, 0], [750_000, 10 * MM, 0]),
                ('north', [10 * MM, 750_000, 0], [10 * MM, 29_250_000, 0]),
                ('south', [10 * MM, 29_250_000, 0], [10 * MM, 750_000, 0])):
            declared = dict(fixed, edge_connectors=[{'ref': 'M', 'edge': edge,
                'max_setback_mm': 0.75, 'overhang_mm': {'min': 0, 'max': 2}}])
            problem, _ = job.capture(path, declared, {'groups': []})
            m = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
            self.assertEqual(m['occupancy'], occupancy, 'capture keeps derived occupancy unchanged')
            problem['rules']['copper_edge_clearance_nm'] = 200_000
            with self.subTest(edge=edge):
                self.assertTrue(evaluate(problem, [{m['uuid']: pose}])[0]['feasible'])
                beyond = list(pose)
                axis = 0 if edge in ('west', 'east') else 1
                beyond[axis] += -MM if edge in ('west', 'north') else MM
                self.assertTrue(any(v['kind'] == 'occupancy_outside_outline' for v in
                    evaluate(problem, [{m['uuid']: beyond}])[0]['violations']))
                self.assertTrue(any(v['kind'] == 'occupancy_outside_outline' for v in
                    evaluate(problem, [{m['uuid']: wrong}])[0]['violations']))
                corner = list(pose)
                corner[1 - axis] = 750_000
                self.assertTrue(any(v['kind'] == 'occupancy_outside_outline' for v in
                    evaluate(problem, [{m['uuid']: corner}])[0]['violations']))
                undeclared = copy.deepcopy(problem)
                next(f for f in undeclared['footprints'] if f['uuid'] == m['uuid']).pop('edge')
                self.assertFalse(evaluate(undeclared, [{m['uuid']: pose}])[0]['feasible'])
                inset = list(pose)
                inset[axis] = 3 * MM if edge in ('west', 'north') else 27 * MM
                self.assertFalse(evaluate(problem, [{m['uuid']: inset}])[0]['feasible'], '0.75 mm setback retained')
                copper_out = list(pose)
                copper_out[axis] = 500_000 if edge in ('west', 'north') else 29_500_000
                self.assertTrue(any(v['kind'] == 'copper_edge_clearance' for v in
                    evaluate(problem, [{m['uuid']: copper_out}])[0]['violations']))
        self.assertEqual(path.read_bytes(), source_bytes)

    def test_hole_clearance_is_not_fiducial_copper_override(self):
        import pcbnew as k
        path, fixed = self.board()
        board = k.LoadBoard(str(path))
        fid = next(f for f in board.GetFootprints() if f.GetReference() == 'F')
        next(iter(fid.Pads())).SetLocalClearance(1_016_000)
        k.SaveBoard(str(path), board)
        problem, _ = job.capture(path, fixed, {'groups': []})
        m = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
        result, report = self.native(path, problem, {m['uuid']: [12_700_000, 10 * MM, 0]}, 'override')
        self.assertFalse(any(v['type'] == 'hole_clearance' for v in report['violations']), report)
        self.assertTrue(result['feasible'], result)

    def test_unsupported_drill_geometry_refuses(self):
        import pcbnew as k
        path, _ = self.board()
        board = k.LoadBoard(str(path))
        pad = next(iter(next(f for f in board.GetFootprints() if f.GetReference() == 'M').Pads()))
        pad.SetDrillSize(k.VECTOR2I(0, MM))
        with self.assertRaisesRegex(job.JobRefusal, 'unsupported drill geometry'):
            job._pad_hole(pad, (0, 0), 0, 200_000)
        pad.SetDrillSize(k.VECTOR2I(MM, MM))
        pad.SetSecondaryDrillSize(k.VECTOR2I(MM, MM))
        with self.assertRaisesRegex(job.JobRefusal, 'secondary/tertiary drill unsupported'):
            job._pad_hole(pad, (0, 0), 0, 200_000)

    def test_malformed_overhang_refuses(self):
        path, fixed = self.board('copper')
        for interval in (None, {}, {'min': -1, 'max': 2}, {'min': 3, 'max': 2},
                         {'min': 0, 'max': True}, {'min': 0, 'max': float('inf')}):
            with self.subTest(interval=interval), self.assertRaisesRegex(job.JobRefusal, 'overhang'):
                job.capture(path, dict(fixed, edge_connectors=[{'ref': 'M', 'edge': 'west',
                    'overhang_mm': interval}]), {'groups': []})

    def test_open_zero_minimum_does_not_grant_undeclared_overhang(self):
        path, fixed = self.board('copper', body=True)
        declared = dict(fixed, edge_connectors=[{'ref': 'M', 'edge': 'west', 'overhang_mm': {'min': 0}}])
        problem, _ = job.capture(path, declared, {'groups': []})
        m = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
        self.assertEqual(m['edge']['overhang_nm'], {'min': 0, 'max': None})
        self.assertTrue(evaluate(problem, [{m['uuid']: [4 * MM, 10 * MM, 0]}])[0]['feasible'])
        self.assertFalse(evaluate(problem, [{m['uuid']: [MM, 10 * MM, 0]}])[0]['feasible'])
        declared['edge_connectors'][0]['overhang_mm'] = {'min': 1}
        with self.assertRaisesRegex(job.JobRefusal, 'finite maximum'):
            job.capture(path, declared, {'groups': []})

    def test_declared_edge_without_setback_rejects_wrong_edge_on_saved_board(self):
        import pcbnew
        path, fixed = self.board('copper', body=True)
        poses = {'west': [4 * MM, 15 * MM, 0], 'north': [15 * MM, 4 * MM, 0],
                 'east': [26 * MM, 15 * MM, 0], 'south': [15 * MM, 26 * MM, 0]}
        for edge in poses:
            declared = dict(fixed, edge_connectors=[{'ref': 'M', 'edge': edge,
                'class': 'edge_receptacle', 'overhang_mm': {'min': 0}}])
            problem, _ = job.capture(path, declared, {'groups': []})
            moving = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
            for actual, pose in poses.items():
                expected = actual == edge
                label = f'{edge}-at-{actual}'
                with self.subTest(declared=edge, actual=actual):
                    result, native = self.native(path, problem, {moving['uuid']: pose}, label)
                    self.assertFalse(any(v['severity'] == 'error' for v in native['violations']))
                    self.assertEqual(result['feasible'], expected, result)
                    directory = self.root / label
                    saved = directory / 'board.kicad_pcb'
                    checked = job.check_edge_intent(saved, declared)
                    self.assertEqual(checked['status'], 'pass' if expected else 'failed', checked)
                    self.assertEqual(checked['connectors'][0]['basis'], 'body:fab')
                    self.assertEqual(checked['connectors'][0]['nearest'], actual)
                    (directory / 'native-drc.json').write_text(json.dumps(native))
                    final = job.final_checks({'status': 'routed', 'board': str(saved)},
                        {'board': str(saved), 'directory': str(directory)},
                        job._owners(pcbnew.LoadBoard(str(saved))), set(), self.root / f'final-{label}',
                        {'groups': []}, declared)
                    (directory / 'final-checks.json').write_text(json.dumps(final, indent=2) + '\n')
                    self.assertEqual(final['integrity'], 'preserved')
                    self.assertEqual(final['opens'], 0)
                    self.assertFalse(any(e['count'] for e in final['errors'].values()))
                    self.assertEqual(final['eligibility'], 'eligible' if expected else 'INELIGIBLE')

    def test_connector_body_seat_is_distinct_from_courtyard_reservation(self):
        import pcbnew as k
        path, fixed = self.board('copper', body=True)
        board = k.LoadBoard(str(path))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'M')
        fp.SetPosition(k.VECTOR2I(26 * MM, 15 * MM))
        for shape in _box(k, fp, k.F_CrtYd, 24 * MM, 13 * MM, 29 * MM, 29_900_000):
            fp.Add(shape)
        k.SaveBoard(str(path), board)
        declared = dict(fixed, edge_connectors=[{'ref': 'M', 'edge': 'east', 'class': 'edge_receptacle'}])
        problem, _ = job.capture(path, declared, {'groups': []})
        row = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
        self.assertEqual(row['occupancy']['status'], 'courtyard')
        self.assertNotEqual(row['edge']['seat_local_nm'], row['occupancy']['local_rect_nm'])
        court_problem = copy.deepcopy(problem)
        court_row = next(f for f in court_problem['footprints'] if f['native_ref'] == 'M')
        court_row['edge']['seat_local_nm'] = row['occupancy']['local_rect_nm']
        self.assertEqual(evaluate(court_problem, [{}])[0]['edge_intent']['connectors'][0]['nearest'], 'south')
        result, _ = self.native(path, problem, {}, 'body-seat')
        self.assertTrue(result['feasible'], result)
        checked = job.check_edge_intent(self.root / 'body-seat' / 'board.kicad_pcb', declared)
        self.assertEqual(checked['status'], 'pass', checked)
        self.assertEqual(checked['connectors'][0]['basis'], 'body:fab')

    def test_fixed_connector_edge_is_checked_without_moving_it(self):
        path, fixed = self.board('copper', body=True)
        declared = dict(fixed, edge_connectors=[{'ref': 'M', 'edge': 'east', 'class': 'edge_receptacle'}])
        declared['fixed_poses'] = fixed['fixed_poses'] + [
            {'ref': 'M', 'x': 10, 'y': 10, 'rot': 0, 'side': 'F'}]
        problem, _ = job.capture(path, declared, {'groups': []})
        result = evaluate(problem, [{}])[0]
        self.assertTrue(any(v['kind'] == 'edge_identity' and v['ref'] == 'M'
                            for v in result['violations']), result)
        self.assertEqual(job.check_edge_intent(path, declared)['status'], 'failed')

    def test_larger_nondefault_netclass_clearance_is_preserved_in_search(self):
        import pcbnew as k
        path, fixed = self.board('copper')
        board = k.LoadBoard(str(path))
        net = k.NETINFO_ITEM(board, 'WIDE')
        board.Add(net)
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'M')
        next(iter(fp.Pads())).SetNet(net)
        fp.SetPosition(k.VECTOR2I(12_700_000, 10 * MM))  # copper gap .3 mm
        k.SaveBoard(str(path), board)
        self.project['net_settings']['classes'].append({'name': 'Wide', 'clearance': 0.8})
        self.project['net_settings']['netclass_patterns'] = [{'netclass': 'Wide', 'pattern': 'WIDE'}]
        path.with_suffix('.kicad_pro').write_text(json.dumps(self.project))
        report, execution = job.drc(path, self.root)
        self.assertEqual(execution['exit_code'], 0)
        clearance = [v for v in report['violations'] if v['type'] == 'clearance']
        self.assertTrue(clearance, report)
        self.assertTrue(any('0.8000' in v['description'] for v in clearance), clearance)
        print('NONDEFAULT_NEGATIVE ' + json.dumps({'native': clearance, 'exit': execution['exit_code']}), flush=True)
        problem, _ = job.capture(path, fixed, {'groups': []})
        self.assertEqual(problem['rules']['copper_clearance_nm'], 800_000)
        self.assertFalse(evaluate(problem, [{}])[0]['feasible'])

    def test_larger_default_clearance_is_included_in_supported_domain(self):
        path, fixed = self.board('copper')
        self.project['net_settings']['classes'][0]['clearance'] = 0.8
        path.with_suffix('.kicad_pro').write_text(json.dumps(self.project))
        problem, _ = job.capture(path, fixed, {'groups': []})
        self.assertEqual(problem['rules']['copper_clearance_nm'], 800_000)
        m = next(f for f in problem['footprints'] if f['native_ref'] == 'M')
        result, report = self.native(path, problem, {m['uuid']: [12_700_000, 10 * MM, 0]}, 'default')
        self.assertFalse(result['feasible'], result)
        self.assertTrue(any(v['type'] == 'clearance' and '0.8000' in v['description']
                            for v in report['violations']), report)


if __name__ == '__main__':
    unittest.main()
