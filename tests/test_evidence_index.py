"""Synthetic sealed-record contracts; these are not telemetry-pod qualification."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from agent_ee.baseline import freeze_evidence
from agent_ee.candidate import capture
from agent_ee.cli import policy_files, runtime_files, save_result, verify
from agent_ee.evidence_index import build_index, main, render_index
from agent_ee.preflight import capture_expectation
from agent_ee.storage import digest, read_json, write_json


def publish(run, checks, *, state='complete'):
    manifest = read_json(run / 'candidate.json')
    result = {'run_id': 'synthetic-index-fixture', 'candidate_id': manifest['candidate_id'],
              'state': state, 'policy_files': policy_files(), 'runtime_files': runtime_files(),
              'checks': checks, 'reviews': [], 'review_executions': [], 'findings': [],
              'adjudicated': False, 'limitations': ['Synthetic fixture; no live review or board qualification.']}
    result['launch_expectation'] = capture_expectation(result, manifest, [], inputs=run / 'inputs')
    freeze_evidence(run, checks)
    save_result(run, result)
    verify(run, current_source=False)


class EvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        source = self.root / 'source'
        source.mkdir()
        mapping = {str(i): [f'R{i}'] for i in range(1, 10)}
        write_json(source / 'pod-criteria.json', mapping)
        for name in ('spec.md', 'catalog.json', 'dfm.json'):
            (source / name).write_text('synthetic source: ' + name)
        self.scope = {'schema': 'agent-ee.scope/2', 'name': 'synthetic pod evidence',
            'scope': 'Index reader contracts only', 'inputs': [{'path': str(source), 'at': 'requirements'}],
            'checks': [{'id': f'check{i}', 'kind': 'synthetic',
                        'params': {'source': 'requirements/catalog.json' if i == 5 else 'requirements/dfm.json'}}
                       for i in range(1, 10)],
            'baseline': {'requirements': [{'id': f'R{i}', 'statement': f'Synthetic criterion {i}',
                'source': {'path': 'requirements/spec.md', 'locator': str(i)}, 'checks': [f'check{i}']}
                for i in range(1, 10)], 'targets': ['requirements/pod-criteria.json'],
                'reference_architectures': ['requirements/spec.md']}}
        self.scope_path = self.root / 'scope.json'
        self.run = self.root / 'run'
        self.capture()

    def capture(self):
        write_json(self.scope_path, self.scope)
        self.run.mkdir()
        manifest = capture(self.scope_path, self.run)
        self.checks = []
        for spec in self.scope['checks']:
            cid = spec['id']
            evidence = f'work/{cid}/observation.json'
            write_json(self.run / evidence, {'synthetic': True, 'criterion': cid})
            check = {'id': cid, 'kind': spec['kind'], 'execution': 'ok', 'outcome': 'pass',
                     'summary': 'Synthetic recorded observation', 'candidate_id': manifest['candidate_id'],
                     'evidence': [evidence], 'limitations': ['Synthetic result, not a pod measurement.']}
            write_json(self.run / 'checks' / f'{cid}.json', check)
            self.checks.append(check)
        publish(self.run, self.checks)

    def index(self):
        return build_index(self.run, 'requirements/pod-criteria.json')

    def test_maps_all_criteria(self):
        report = self.index()
        self.assertEqual([r['criterion'] for r in report['criteria']], list(map(str, range(1, 10))))
        self.assertEqual([r['status'] for r in report['criteria']], ['met'] * 7 + ['not_met'] * 2)
        self.assertFalse(report['acceptance'])
        for row in report['criteria']:
            check = row['checks'][0]
            self.assertEqual(check['result']['sha256'], digest(self.run / check['result']['path']))
            self.assertEqual(check['evidence'][0]['sha256'], digest(self.run / check['evidence'][0]['path']))
        self.assertIn('requirements/catalog.json', str(report['criteria'][4]['captured_inputs']))
        self.assertIn('requirements/dfm.json', str(report['criteria'][5]['captured_inputs']))
        rendered = render_index(report)
        self.assertIn('CAN/ECU bench', rendered)
        self.assertIn('RaceChrono Pro', rendered)
        self.assertIn('Synthetic result, not a pod measurement.', rendered)

    def test_unknown_shown_not_met(self):
        self.checks[0].update(execution='failed', outcome='unknown')
        write_json(self.run / 'checks/check1.json', self.checks[0])
        publish(self.run, self.checks)
        row = self.index()['criteria'][0]
        self.assertEqual(row['status'], 'not_met')
        self.assertIn('check1: failed/unknown', row['reasons'])

    def test_reads_only_sealed(self):
        unsealed = self.run / 'writer-says-everything-passes.json'
        unsealed.write_text('{ this is deliberately not JSON; every criterion passes!')
        report = self.index()
        self.assertIn(unsealed.name, report['ignored_unsealed'])
        self.assertNotIn('every criterion passes!', str(report))
        self.assertEqual(report['criteria'][8]['status'], 'not_met')
        (self.run / 'work/check1/observation.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.index()

    def test_incomplete_process_cannot_mark_a_criterion_met(self):
        publish(self.run, self.checks, state='running')
        self.assertTrue(all(r['status'] == 'not_met' for r in self.index()['criteria']))

    def test_mapping_must_be_captured_immutable_and_cover_all_requirements(self):
        with self.assertRaisesRegex(ValueError, 'immutable captured'):
            build_index(self.run, 'writer-mapping.json')
        for mapping in ({'1': ['R1']}, {str(i): ['R1'] for i in range(1, 10)},
                        {str(i): ['missing'] for i in range(1, 10)}):
            with self.subTest(mapping=mapping):
                source = self.root / 'source/pod-criteria.json'
                write_json(source, mapping)
                self.run = self.root / ('run-' + digest(source)[:10])
                self.capture()
                with self.assertRaises(ValueError):
                    self.index()

    def test_cli_outputs_outside_evidence_and_preserves_existing_files(self):
        before = {p: p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
        output = self.root / 'index'
        args = ['--run', str(self.run), '--criteria', 'requirements/pod-criteria.json', '--out', str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.assertEqual(read_json(output / 'index.json'), self.index())
        self.assertIn('# Telemetry pod evidence index', (output / 'index.md').read_text())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 2)
            self.assertEqual(main([*args[:-1], str(self.run / 'index')]), 2)
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.rglob('*') if p.is_file()})

    def test_absent_retained_seal_is_refused(self):
        (self.run / 'work/verification/evidence.json').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.index()


class NativePreviewIndexTests(unittest.TestCase):
    def test_preview_obligations_come_from_real_sealed_fab_outputs(self):
        from tests.test_fab_package import NativeRun, rotation_scope, cpl_obligations

        fixture = NativeRun()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        scope = rotation_scope()
        mapping = {str(i): [] for i in range(1, 10)}
        mapping['6'] = [r['id'] for r in scope['baseline']['requirements']]
        path = fixture.root / 'pod-criteria.json'
        write_json(path, mapping)
        scope['inputs'].append({'path': str(path), 'at': 'requirements/pod-criteria.json'})
        scope['baseline']['targets'].append('requirements/pod-criteria.json')
        fab = fixture.run_case(scope=scope)
        self.assertEqual((fab['execution'], fab['outcome']), ('ok', 'pass'), fab)
        publish(fixture.run_dir, fixture.results)
        report = build_index(fixture.run_dir, 'requirements/pod-criteria.json')
        obligation = report['owner_obligations'][0]
        self.assertEqual(obligation['status'], 'PREVIEW_REQUIRED')
        self.assertEqual(obligation['designators'], cpl_obligations(fixture.run_dir / 'work/cpl/cpl/cpl.csv'))
        self.assertTrue(obligation['designators'])
        self.assertEqual(report['criteria'][5]['owner_obligations'], [obligation])
        self.assertIn('PREVIEW_REQUIRED', render_index(report))
        self.assertFalse(report['acceptance'])


class CampaignReceiptIndexTests(unittest.TestCase):
    def test_receipts_follow_verified_campaign_history_not_writer_claims(self):
        from tests.test_stage_receipts import CampaignStageReceiptTests
        from tests.toolchain import require_toolchain

        require_toolchain()
        fixture = CampaignStageReceiptTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        scope_path = fixture.root / 'scope.json'
        scope = read_json(scope_path)
        scope['baseline']['targets'].append('design/pod-criteria.json')
        write_json(scope_path, scope)
        mapping = {str(i): ['R1'] if i == 1 else [] for i in range(1, 10)}
        write_json(fixture.root / 'source/pod-criteria.json', mapping)
        fixture.pre_receipt()
        status, state = fixture.campaign([fixture.step()])
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state)
        first = build_index(fixture.root / 'campaign/runs/1', 'design/pod-criteria.json')
        final = build_index(Path(state['history'][-1]['run']), 'design/pod-criteria.json')
        self.assertEqual(len(first['receipts']), 1)
        self.assertEqual(len(final['receipts']), 2)
        self.assertEqual(first['criteria'][0]['status'], 'not_met')
        self.assertEqual(final['criteria'][0]['status'], 'met')
        self.assertTrue(all('review-work' not in item['path'] for item in final['receipts']))
        receipt = Path(final['receipts'][-1]['path'])
        self.assertEqual(final['receipts'][-1]['sha256'], digest(receipt))
        receipt.write_bytes(receipt.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            build_index(Path(state['history'][-1]['run']), 'design/pod-criteria.json')


if __name__ == '__main__':
    unittest.main()
