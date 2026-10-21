"""Saved KiCad and networkless writer evidence for AGENTEE-55."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.executor import execute
from agent_ee.placement_plans import plan
from agent_ee.placement_probe import extract
from agent_ee.placer_runner import place_candidate
import test_placement_native as native_fixtures
from test_placement_plans import row, scoring
from test_placer import request
from toolchain import require_toolchain


class NativePlansTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        import pcbnew as k
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-targeted-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = native_fixtures.NativePlacementTest()
        fixture.root = self.root
        self.source = fixture.board('control')
        native = k.LoadBoard(str(self.source))
        net = k.NETINFO_ITEM(native, 'VCC')
        native.Add(net)
        for fp in native.GetFootprints():
            fp.SetLocked(fp.GetReference() == 'U1')
            for pad in fp.Pads():
                pad.SetNet(net)
        k.SaveBoard(str(self.source), native)
        self.saved = self.source.read_bytes()
        self.netlist = self.root / 'netlist.xml'
        # An original XML-dialect declaration, not claimed as a native export.
        self.netlist.write_text('<export><components><comp ref="U1"/><comp ref="C1"/></components>'
                                '<nets><net name="VCC"><node ref="U1" pin="1"/>'
                                '<node ref="C1" pin="1"/></net></nets></export>')
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        judge_policy = scoring()
        del judge_policy['capacitors']['C2']
        self.config = {'policy': request(fixed_refs=[]), 'targeted': row(
            capacitor_refs=['C1'], judge_policy=judge_policy), 'authorization': {
                'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                'source_sha256': hashlib.sha256(self.saved).hexdigest(), 'writable_root': str(self.scratch)}}

    def test_missing_keys_refuse_without_any_write(self):
        for key in self.config['targeted']:
            if key == 'mode':
                continue
            config = copy.deepcopy(self.config)
            del config['targeted'][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                place_candidate(self.source, self.netlist, self.scratch / 'missing', config)
            self.assertEqual(list(self.scratch.iterdir()), [])
            self.assertEqual(self.source.read_bytes(), self.saved)

    def test_conflicting_request_paths_refuse_without_any_write(self):
        self.config['targeted_plans'] = [self.config['targeted']]
        with self.assertRaisesRegex(ValueError, 'cannot be combined'):
            place_candidate(self.source, self.netlist, self.scratch / 'conflict', self.config)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_cluster_seed_applies_through_saved_board_and_native_drc(self):
        self.config['targeted'] = {'mode': 'net_cluster_seed', 'refs': ['C1'],
            'origin_nm': [6_000_000, 3_000_000], 'spacing_nm': [3_000_000, 3_000_000],
            'judge_policy': self.config['targeted']['judge_policy']}
        result = place_candidate(self.source, self.netlist, self.scratch / 'cluster', self.config)
        self.assertEqual((result['status'], result['plan']['mode']), ('placed', 'net_cluster_seed'), result)
        self.assertEqual(result['judgment']['score'], 100)
        self.assertEqual(result['native_courtyard_overlaps'], [])
        self.assertEqual(result['write_scope']['status'], 'allowed')
        self.assertNotIn('seed_only', result['plan'])
        self.assertEqual(self.source.read_bytes(), self.saved)

    def test_mixed_judge_policies_refuse_before_lease_or_candidate_write(self):
        first = self.config.pop('targeted')
        second = copy.deepcopy(first)
        second['judge_policy']['capacitors']['C1']['deduction'] = 1
        self.config['targeted_plans'] = [first, second]
        with self.assertRaisesRegex(ValueError, 'one shared judge_policy'):
            place_candidate(self.source, self.netlist, self.scratch / 'mixed', self.config)
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.saved)

    def _mark_imu_axes(self, position):
        import pcbnew as k
        native = k.LoadBoard(str(self.source))
        marking = k.PCB_TEXT(native)
        marking.SetText('IMU_AXES')
        marking.SetLayer(k.F_SilkS)
        marking.SetPosition(k.VECTOR2I(*position))
        marking.SetTextSize(k.VECTOR2I(800000, 800000))
        native.Add(marking)
        self.assertTrue(k.SaveBoard(str(self.source), native))
        marked = self.source.read_bytes()
        self.config['authorization']['source_sha256'] = hashlib.sha256(marked).hexdigest()
        return marked

    def test_imu_axes_blocks_targeted_modes_before_lease_or_candidate_write(self):
        target = next(p for p in plan(extract(self.source), self.config['policy'],
                                     self.config['targeted'])['placements'] if p['ref'] == 'C1')
        marked = self._mark_imu_axes(target['position_nm'])
        for mode in ('targeted', 'targeted_plans'):
            config = copy.deepcopy(self.config)
            if mode == 'targeted_plans':
                config[mode] = [config.pop('targeted')]
            with self.subTest(mode=mode):
                result = place_candidate(self.source, self.netlist, self.scratch / mode, config)
                self.assertEqual(result['status'], 'blocked', result)
                self.assertTrue(any('target conflicts with obstacle' in r for r in result['reasons']))
                self.assertTrue(any(o.get('source', '').startswith('IMU_AXES/')
                                    for o in result['policy']['obstacles']))
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertEqual(self.source.read_bytes(), marked)

    def test_clear_imu_targeted_modes_retain_reservation_in_saved_plan(self):
        marked = self._mark_imu_axes([15_000_000, 15_000_000])
        for mode in ('targeted', 'targeted_plans'):
            config = copy.deepcopy(self.config)
            if mode == 'targeted_plans':
                config[mode] = [config.pop('targeted')]
            with self.subTest(mode=mode):
                result = place_candidate(self.source, self.netlist, self.scratch / mode, config)
                self.assertEqual(result['status'], 'placed', result)
                reservations = [o for o in result['plan']['policy']['obstacles']
                                if o.get('source', '').startswith('IMU_AXES/')]
                self.assertEqual(len(reservations), 1)
                self.assertEqual(self.source.read_bytes(), marked)

    def test_unmarked_targeted_modes_still_place(self):
        for mode in ('targeted', 'targeted_plans'):
            config = copy.deepcopy(self.config)
            if mode == 'targeted_plans':
                config[mode] = [config.pop('targeted')]
            with self.subTest(mode=mode):
                result = place_candidate(self.source, self.netlist, self.scratch / mode, config)
                self.assertEqual(result['status'], 'placed', result)
                self.assertEqual(result['judgment']['score'], 100)
                self.assertEqual(result['native_courtyard_overlaps'], [])
                self.assertEqual(self.source.read_bytes(), self.saved)

    def test_selection_without_targeted_key_and_no_fallback_on_blocked(self):
        good = self.config.pop('targeted')
        self.config['targeted_plans'] = [{**good, 'offset_nm': [12_000_000, 0]}, good]
        result = place_candidate(self.source, self.netlist, self.scratch / 'selected', self.config)
        self.assertEqual(result['status'], 'placed', result)
        self.assertEqual(result['plan']['selection']['selected_index'], 1)
        self.config['targeted_plans'] = self.config['targeted_plans'][:1]
        remaining = sorted(p.name for p in self.scratch.iterdir())
        result = place_candidate(self.source, self.netlist, self.scratch / 'blocked', self.config)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(sorted(p.name for p in self.scratch.iterdir()), remaining)

    def test_fixed_overhang_preview_and_saved_native_judge_agree(self):
        import pcbnew as k
        native = k.LoadBoard(str(self.source))
        fixed = next(fp for fp in native.GetFootprints() if fp.GetReference() == 'U1')
        fixed.SetPosition(k.VECTOR2I(0, 3_000_000))
        next(iter(fixed.Pads())).SetPosition(k.VECTOR2I(500_000, 3_000_000))
        k.SaveBoard(str(self.source), native)
        self.config['authorization']['source_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.config['policy']['fixed_overhangs'] = [{'reference': 'U1',
            'edges': [{'name': 'left', 'start_nm': [0, 0], 'end_nm': [0, 20_000_000],
                       'outward': 'x-', 'max_nm': 1_000_000}],
            'source': {'envelope_id': 'original', 'envelope_sha256': 'a'*64, 'pointer': '/fixed_footprints/0'}}]
        result = place_candidate(self.source, self.netlist, self.scratch / 'overhang', self.config)
        self.assertEqual(result['status'], 'placed', result)
        self.assertEqual(result['plan']['after']['declared_overhangs'], result['judgment']['declared_overhangs'])
        self.assertEqual(result['plan']['after']['verdict'], result['judgment']['verdict'])
        self.assertEqual(result['judgment']['score'], 100)

    def test_saved_row_improves_and_locked_coordinates_identical(self):
        before = extract(self.source)
        plans = []
        for name in ('one', 'two'):
            result = place_candidate(self.source, self.netlist, self.scratch / name, self.config)
            self.assertEqual(result['status'], 'placed', result)
            self.assertEqual(result['judgment']['score'], 100)
            self.assertEqual(result['plan']['before']['score'], 85)
            self.assertEqual(result['native_courtyard_overlaps'], [])
            self.assertEqual(result['write_scope']['status'], 'allowed')
            after = extract(self.scratch / name / 'candidate.kicad_pcb')
            old = next(f for f in before['footprints'] if f['ref'] == 'U1')
            new = next(f for f in after['footprints'] if f['ref'] == 'U1')
            self.assertEqual(new, old)
            plans.append((self.scratch / name / 'placement-plan.json').read_bytes())
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(self.source.read_bytes(), self.saved)

    def test_outside_tie_decrease_missing_coverage_and_obstacle_have_no_write(self):
        for name, variant in [('outside', {'offset_nm': [30_000_000, 0]}),
                              ('tie', {'offset_nm': [12_000_000, 0]}),
                              ('obstacle', {'offset_nm': [1_000_000, 0]})]:
            config = copy.deepcopy(self.config)
            config['targeted'].update(variant)
            result = place_candidate(self.source, self.netlist, self.scratch / name, config)
            self.assertEqual(result['status'], 'blocked', result)
            self.assertEqual(list(self.scratch.iterdir()), [], result)
            self.assertEqual(self.source.read_bytes(), self.saved)
        # A proved good close row gets worse if moved away again.
        import pcbnew as k
        native = k.LoadBoard(str(self.source))
        next(fp for fp in native.GetFootprints() if fp.GetReference() == 'C1').SetPosition(k.VECTOR2I(6_000_000, 3_000_000))
        k.SaveBoard(str(self.source), native)
        self.config['authorization']['source_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.config['targeted']['offset_nm'] = [12_000_000, 0]
        result = place_candidate(self.source, self.netlist, self.scratch / 'decrease', self.config)
        self.assertEqual((result['before']['score'], result['after']['score'], result['status']), (100, 85, 'blocked'))
        self.assertEqual(list(self.scratch.iterdir()), [])
        fixture = native_fixtures.NativePlacementTest()
        fixture.root = self.root
        self.source = fixture.board('open_outline')
        self.config['authorization']['source_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = place_candidate(self.source, self.netlist, self.scratch / 'open', self.config)
        self.assertEqual((result['status'], result['outcome']), ('blocked', 'unknown'))
        self.assertIn('missing coverage', result['reasons'][0])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_actual_writer_runs_networkless_on_captured_copy(self):
        inputs, work = self.root / 'inputs', self.root / 'work'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(self.saved)
        (inputs / 'netlist.xml').write_bytes(self.netlist.read_bytes())
        config = copy.deepcopy(self.config)
        config['authorization']['writable_root'] = '/work'
        (inputs / 'request.json').write_text(json.dumps(config))
        execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
                            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request',
                            '/candidate/request.json', '--destination', '/work/placed', '--result', '/work/result.json'])
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        result = json.loads((work / 'result.json').read_text())
        self.assertEqual((result['status'], result['judgment']['score']), ('placed', 100))
        self.assertEqual((inputs / 'board.kicad_pcb').read_bytes(), self.saved)
        self.assertEqual((inputs / 'request.json').read_text(), json.dumps(config))

    def test_native_saved_judge_rechecks_score_gate(self):
        from agent_ee.placer_runner import _apply_plan, _saved_judgment
        import pcbnew as k
        expected = plan(extract(self.source), self.config['policy'], self.config['targeted'])
        output = self.scratch / 'saved.kicad_pcb'
        _apply_plan(k.LoadBoard(str(self.source)), expected, output)
        facts, judgment = _saved_judgment(output, expected)
        self.assertEqual(judgment['score'], 100)
        self.assertEqual(facts['board_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        # Exercise the saved-byte gate independently of the transformed preview.
        expected['before']['score'] = 100
        with self.assertRaisesRegex(ValueError, 'score did not strictly improve'):
            _saved_judgment(output, expected)

    def test_native_bga_pad_dialect_pitch_and_no_apply(self):
        import pcbnew as k
        native = k.LoadBoard(str(self.source))
        fp = next(f for f in native.GetFootprints() if f.GetReference() == 'U1')
        original = next(iter(fp.Pads()))
        for i, (x, y) in enumerate(((3, 3), (4, 3), (3, 4), (4, 4)), 1):
            pad = original if i == 1 else k.PAD(fp)
            if i != 1:
                fp.Add(pad)
            pad.SetNumber(str(i))
            pad.SetShape(k.PAD_SHAPE_CIRCLE)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetSize(k.VECTOR2I(300000, 300000))
            pad.SetPosition(k.VECTOR2I(x*1000000, y*1000000))
        k.SaveBoard(str(self.source), native)
        facts = extract(self.source)
        spec = {'mode': 'bga_fanout', 'bga_ref': 'U1', 'escape_offset_nm': 200000,
                'judge_policy': self.config['targeted']['judge_policy']}
        result = plan(facts, request(), spec)
        self.assertEqual((result['pitch_nm'], result['status'], result['plan_only']), (1000000, 'blocked', True))
        self.assertEqual(len(result['escape_targets']), 4)
        self.assertEqual(result, plan(facts, request(), spec))


if __name__ == '__main__':
    unittest.main()
