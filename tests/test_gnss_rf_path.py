"""Synthetic RF controls; dimensions are test choices, never calculator evidence."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.net_constraints import emit
from agent_ee.storage import digest, read_json, write_json
from tests.routing_fixture import make_scope
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class NativeRfMechanismTests(unittest.TestCase):
    """Exercise the existing checks without inventing a JLCPCB calculation."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rf-mechanism-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_probe(self, *, width=0.3, sma_x=10, routed=True, project_width=None):
        import pcbnew as k

        source = self.root / 'source'
        source.mkdir()
        # Deliberately synthetic: these bounds cannot qualify a pod RF path.
        rules = {'schema': 'agent-ee.routing-rules/1', 'constraints': [
            {'id': 'rf-length', 'requirement': 'R-rf-length', 'kind': 'length',
             'nets': ['RF_IN'], 'max_mm': 15}]}
        scope = make_scope(source, rules, lengths=(sma_x,), net_names=('RF_IN',),
                           width=width, layers=4, outline=(80, 50))
        board_path = source / 'test.kicad_pcb'
        board = k.LoadBoard(str(board_path))
        for fp in board.GetFootprints():
            fp.SetReference('U_GNSS' if fp.GetReference() == 'J00' else 'J_SMA')
        if not routed:
            for track in list(board.GetTracks()):
                board.Remove(track)
        k.SaveBoard(str(board_path), board)
        envelope = (ROOT / 'fixtures/telemetry-pod/envelope.json').read_bytes()
        intent = {'schema': 'agent-ee.net-constraints/1', 'classes': [
            {'name': 'RF_50', 'requirement': 'R-rf-width', 'nets': ['RF_IN'],
             'track_width_mm': 0.3, 'clearance_mm': 0.2, 'impedance_ohm': 50}]}
        artifacts = emit(json.dumps(intent).encode(),
                         (ROOT / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
                         envelope_raw=envelope)
        self.assertEqual(json.loads(artifacts['record'])['outcome'], 'pass')
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
        scope['baseline']['requirements'].append({
            'id': 'R-rf-width', 'intent': 'acceptance', 'statement': 'Synthetic RF width minimum.',
            'source': {'path': 'requirements.txt', 'locator': 'R-rf-width'}, 'checks': ['width']})
        scope['limitations'] = ['Synthetic width/gap/length test choices; no captured calculator '
                                'result, ground plane, actual devices or impedance qualification.']
        write_json(source / 'scope.json', scope)
        before = digest(board_path)
        run = self.root / 'run'
        run.mkdir()
        candidate.capture(source / 'scope.json', run)
        results = checks.run_checks(run, time.monotonic() + 300)
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        self.assertEqual(before, digest(board_path))
        return {r['id']: r for r in results}, json.loads(artifacts['record'])

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


if __name__ == '__main__':
    unittest.main()
