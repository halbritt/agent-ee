"""RF width controls from a captured calculation; length bounds remain test-only."""
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee import candidate, checks, routing_adapter, stage_receipts
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.net_constraints import emit
from agent_ee.storage import digest, read_json, write_json
from tests.routing_fixture import make_scope
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class NativeRfMechanismTests(unittest.TestCase):
    """Exercise the existing checks without inventing a JLCPCB calculation."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rf-mechanism-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_probe(self, *, width=0.3, sma_x=10, routed=True, project_width=None,
                  captured=False, missing_native_net=False):
        import pcbnew as k

        source = self.root / 'source'
        source.mkdir()
        net = '/POD_GNSS_RF' if captured else 'RF_IN'
        # Deliberately synthetic: these bounds cannot qualify a pod RF path.
        rules = {'schema': 'agent-ee.routing-rules/1', 'constraints': [
            {'id': 'rf-length', 'requirement': 'R-rf-length', 'kind': 'length',
             'nets': [net], 'max_mm': 15}]}
        scope = make_scope(source, rules, lengths=(sma_x,), net_names=(net,),
                           width=width, layers=4, outline=(80, 50))
        board_path = source / 'test.kicad_pcb'
        board = k.LoadBoard(str(board_path))
        for fp in board.GetFootprints():
            fp.SetReference('U_GNSS' if fp.GetReference() == 'J00' else 'J_SMA')
        if not routed or captured:
            for track in list(board.GetTracks()):
                board.Remove(track)
        k.SaveBoard(str(board_path), board)
        envelope = (ROOT / 'fixtures/telemetry-pod/envelope.json').read_bytes()
        intent = {'schema': 'agent-ee.net-constraints/1', 'classes': [
            {'name': 'RF_50', 'requirement': 'R-rf-width', 'nets': ['RF_IN'],
             'track_width_mm': 0.3, 'clearance_mm': 0.2, 'impedance_ohm': 50}]}
        raw = ((ROOT / 'fixtures/telemetry-pod/rf-net-constraints.json').read_bytes()
               if captured else json.dumps(intent).encode())
        intent = json.loads(raw)
        artifacts = emit(raw,
                         (ROOT / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
                         envelope_raw=envelope, board_raw=board_path.read_bytes())
        self.assertEqual(json.loads(artifacts['record'])['outcome'], 'pass')
        if captured:
            for name in ('rf-net-constraints.json', 'rf-source-capture.json', 'envelope.json'):
                shutil.copyfile(ROOT / 'fixtures/telemetry-pod' / name, source / name)
                scope['inputs'].append({'path': name, 'at': name})
        for name, key in (('test.kicad_pro', 'project'), ('test.kicad_dru', 'rules'),
                          ('net-constraints.record.json', 'record')):
            (source / name).write_bytes(artifacts[key])
            scope['inputs'].append({'path': name, 'at': name})
        if project_width is not None:
            project = read_json(source / 'test.kicad_pro')
            project['net_settings']['classes'][1]['track_width'] = project_width
            write_json(source / 'test.kicad_pro', project)
        scope['checks'].append({'id': 'width', 'kind': 'geometry', 'params': {
            'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
            'min_track_width_mm': 0.09, 'exclude_nets': [],
            'net_constraints': 'net-constraints.record.json'}})
        requirement = intent['classes'][0]['requirement']
        scope['baseline']['requirements'].append({
            'id': requirement, 'intent': 'acceptance', 'statement': 'Meet the declared RF width minimum.',
            'source': {'path': 'requirements.txt', 'locator': requirement}, 'checks': ['width']})
        scope['limitations'] = ['Synthetic width/gap/length test choices; no captured calculator '
                                'result, ground plane, actual devices or impedance qualification.']
        if captured:
            scope['limitations'] = [
                'Captured W1 lower width rounded upward by the emitter; W2 upper taper is metadata only.',
                'The 15 mm length bound is test-only; actual pod RF maximum length remains unbound.',
                'No actual devices, ground plane, exact coplanar gap, mask or impedance qualification.']
            if missing_native_net:
                board_path.write_bytes(board_path.read_bytes().replace(
                    b'"/POD_GNSS_RF"', b'"/RENAMED_RF"'))
            router_input = self.root / 'router-input'
            shutil.copytree(source, router_input / 'project')
            step = routing_adapter.step(project='project', board='test.kicad_pcb',
                schematic='test.kicad_sch', name='rf', label='source-width-probe',
                checkout=require_kicad_route(), budget=120)
            receipt_dir = self.root / 'route'
            receipt = stage_receipts.run_step(receipt_dir, router_input, step,
                {'implementation': 1, 'receipt_index': 1})
            stage_receipts.validate_receipt(receipt_dir)
            summary = routing_adapter.verify(receipt_dir / 'run', receipt=receipt)
            if missing_native_net:
                self.assertEqual(summary['classification'], 'not_run', summary)
                self.assertEqual(summary['proposal'], 'none')
                report = read_json(receipt_dir / 'run/routing/adapter-report.json')
                self.assertIsNone(report['process'], 'routing tool must not run with an unmatched constraint')
                self.assertIn('constraint nets absent from board: /POD_GNSS_RF',
                              str(report['semantics']['reasons']))
                return summary, json.loads(artifacts['record'])
            self.assertEqual(summary['classification'], 'complete', summary)
            self.assertEqual(summary['proposal'], 'eligible', summary)
            shutil.copyfile(receipt_dir / 'run/routing/candidate/project/test.kicad_pcb', board_path)
        write_json(source / 'scope.json', scope)
        self.source = source
        return self.evaluate(source, 'run'), json.loads(artifacts['record'])

    def evaluate(self, source, name):
        board_path = source / 'test.kicad_pcb'
        before = digest(board_path)
        run = self.root / name
        run.mkdir()
        candidate.capture(source / 'scope.json', run)
        results = checks.run_checks(run, time.monotonic() + 300)
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        self.assertEqual(before, digest(board_path))
        return {r['id']: r for r in results}

    def test_absent_pod_net_is_refused_before_native_router_invocation(self):
        self.run_probe(captured=True, missing_native_net=True)

    def test_captured_width_routes_then_narrower_segment_is_a_defect(self):
        import pcbnew as k

        # Retain real router/check receipts when the host validation requests them.
        evidence = os.environ.get('AGENT_EE_RF_EVIDENCE')
        if evidence:
            self.root = Path(evidence)
            self.root.mkdir()
        results, record = self.run_probe(captured=True)
        for result in results.values():
            self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        cls, = record['classes']
        self.assertEqual(cls['min_track_width_mm'], 0.293)
        self.assertEqual(cls['impedance']['status'], 'recorded_not_enforced')
        self.assertEqual(cls['impedance']['target_ohm'], 50)
        board_path = self.source / 'test.kicad_pcb'
        routed = self.root / 'route/run/routing/candidate/project/test.kicad_pcb'
        self.assertEqual(digest(board_path), digest(routed))
        before = {p.name: digest(p) for p in self.source.iterdir() if p.is_file()}
        board = k.LoadBoard(str(board_path))
        tracks = list(board.GetTracks())
        self.assertTrue(tracks)
        self.assertTrue(all(t.GetWidth() == 293000 and t.GetNetname() == '/POD_GNSS_RF' for t in tracks))
        tracks[0].SetWidth(292999)
        k.SaveBoard(str(board_path), board)
        negative = self.evaluate(self.source, 'narrower')
        self.assertEqual((negative['width']['execution'], negative['width']['outcome']), ('ok', 'defect'))
        offender, = negative['width']['details']['offending_primitives']
        self.assertEqual((offender['net'], offender['limit_mm']), ('/POD_GNSS_RF', 0.293))
        self.assertEqual(negative['routing']['outcome'], 'pass')
        after = {p.name: digest(p) for p in self.source.iterdir() if p.is_file()}
        self.assertEqual({n for n in before if before[n] != after[n]}, {'test.kicad_pcb'})

    def test_declared_width_and_short_path_pass_without_verifying_impedance(self):
        results, record = self.run_probe()
        for result in results.values():
            self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(record['classes'][0]['impedance']['status'], 'recorded_not_enforced')
        self.assertEqual(record['classes'][0]['impedance']['target_ohm'], 50)
        self.assertTrue(any('Impedance' in text for text in record['limitations']))

    def test_narrower_rf_segment_is_a_width_defect(self):
        results, _ = self.run_probe(width=0.299999)
        width = results['width']
        self.assertEqual((width['execution'], width['outcome']), ('ok', 'defect'), width)
        offender, = width['details']['offending_primitives']
        self.assertEqual((offender['net'], offender['limit_mm']), ('RF_IN', 0.3))
        self.assertEqual(results['routing']['outcome'], 'pass')

    def test_sma_moved_far_away_with_connected_copper_fails_length(self):
        results, _ = self.run_probe(sma_x=40)
        routing = results['routing']
        self.assertEqual((routing['execution'], routing['outcome']), ('ok', 'defect'), routing)
        constraint, = routing['details']['constraints']
        self.assertEqual((constraint['id'], constraint['status']), ('rf-length', 'violation'))
        self.assertTrue(constraint['tool_load_witness'])
        self.assertTrue(constraint['native_findings'])
        self.assertEqual(results['width']['outcome'], 'pass')

    def test_missing_copper_cannot_pass_length(self):
        results, _ = self.run_probe(routed=False)
        routing = results['routing']
        self.assertEqual((routing['execution'], routing['outcome']), ('unsupported', 'unknown'))

    def test_lowered_project_width_cannot_override_recorded_rf_minimum(self):
        with self.assertRaisesRegex(ValueError, 'RF_50 track_width is below the recorded minimum'):
            self.run_probe(project_width=0.1)


class CapturedRfDeclarationTests(unittest.TestCase):
    def test_native_net_is_backed_by_pin_allocation_and_explicit_adaptation(self):
        capture = read_json(ROOT / 'fixtures/telemetry-pod/rf-source-capture.json')
        mapping = capture['w2_continuation']['dimension_mapping']
        adaptation = mapping['net_adaptation']
        allocation_path = ROOT / 'fixtures/telemetry-pod' / adaptation['source']['path']
        self.assertEqual(digest(allocation_path), adaptation['source']['sha256'])
        gnss, = [c for c in read_json(allocation_path)['components'] if c['component'] == 'gnss']
        pin, = [p for p in gnss['pins'] if p['number'] == '11']
        self.assertEqual((gnss['reference'], pin['name'], pin['net']), ('U3', 'RF_IN', 'POD_GNSS_RF'))
        change, = adaptation['changes']
        self.assertEqual((change['source'], change['native']), ('RF_IN', '/' + pin['net']))
        self.assertEqual(mapping['net'], change['native'])
        self.assertIn('U3.11', change['terminals'])

    def test_source_widths_and_gap_feed_existing_emitter_without_impedance_acceptance(self):
        capture_path = ROOT / 'fixtures/telemetry-pod/rf-source-capture.json'
        capture = read_json(capture_path)['w2_continuation']
        mapping = capture['dimension_mapping']
        path = capture_path.parent / mapping['constraint_path']
        self.assertEqual(digest(path), mapping['constraint_sha256'])
        calculation = capture['calculation']
        self.assertEqual((calculation['solver_status'], calculation['result_valid']), (0, 1))
        cls, = read_json(path)['classes']
        self.assertEqual((cls['name'], cls['nets']), ('RF_50', ['/POD_GNSS_RF']))
        # AGENTEE-186 (agent-529 4cf9d43d v2): widths come from the ADOPTED inverse result, bound by path and sha256;
        # the forward calculation above stays the historical capture.
        adopted = mapping['adopted_inverse']
        inverse_path = capture_path.parent / adopted['result_path']
        self.assertEqual(digest(inverse_path), adopted['result_sha256'])
        inverse = read_json(inverse_path)['impedance_calc_result']
        self.assertEqual((inverse['dResultValid'], inverse['dErEff'], inverse['dImpedance']),
                         (1, adopted['er_eff'], adopted['impedance_ohm']))
        for label, result_field, constraint_field in (
                ('lower_width', 'W1', 'track_width_mm'), ('gap', 'D1', 'clearance_mm'),
                ('upper_width', 'W2', None)):
            with self.subTest(dimension=label):
                dimension = mapping[label]
                mil = Decimal(str(inverse['jBackCalc'][result_field]))
                self.assertEqual(Decimal(str(dimension['mm'])), mil * Decimal('0.0254'))
                self.assertEqual(dimension['constraint_field'], constraint_field)
                if constraint_field:
                    self.assertEqual(cls[constraint_field], dimension['mm'])
        self.assertEqual(mapping['lower_width']['mil'] - mapping['upper_width']['mil'],
                         calculation['input_parameters']['W2LinkW1Incr'])
        self.assertEqual(calculation['geometry_mil']['D1'], calculation['input_parameters']['D1'])
        produced = emit(path.read_bytes(),
            (ROOT / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
            envelope_raw=(ROOT / 'fixtures/telemetry-pod/envelope.json').read_bytes())
        record = json.loads(produced['record'])
        self.assertEqual(record['outcome'], 'pass')
        self.assertFalse(record['acceptance_ready'])
        emitted, = record['classes']
        self.assertEqual(emitted['min_track_width_mm'], mapping['emitted_min_track_width_mm'])
        self.assertEqual(emitted['impedance']['status'], 'recorded_not_enforced')
        self.assertEqual(emitted['impedance']['target_ohm'], 50)
        self.assertEqual(mapping['rf_maximum_length']['status'], 'unbound')


if __name__ == '__main__':
    unittest.main()
