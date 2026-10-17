import tempfile
import unittest
from pathlib import Path

from agent_ee.diagnostics import diagnose
from agent_ee.storage import digest


def inventory(root):
    return {str(p.relative_to(root)): ('directory' if p.is_dir() else digest(p))
            for p in root.rglob('*')}


class DiagnosisTests(unittest.TestCase):
    def test_missing_records_and_lock_do_not_create_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            before = inventory(Path(tmp))
            report = diagnose(root)
            self.assertEqual(report['coherence']['status'], 'unverified')
            self.assertEqual(report['verification']['status'], 'unavailable')
            self.assertTrue(report['unavailable_facts'])
            self.assertEqual(inventory(Path(tmp)), before)

    def test_busy_existing_lease_and_partial_tmp_are_explicit(self):
        from agent_ee.locking import writer_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            (root / 'result.json.tmp').write_text('{')
            with writer_lock(root):
                before = inventory(Path(tmp))
                report = diagnose(root)
                self.assertEqual(report['coherence']['status'], 'busy')
                self.assertEqual(len(report['ambiguous_transitions'][0]['candidates']), 3)
                self.assertEqual(report['verified_facts'], [])
                self.assertEqual(inventory(Path(tmp)), before)

    def test_interrupted_boundaries_preserve_all_bytes(self):
        from agent_ee.locking import writer_lock
        from agent_ee.storage import write_json
        boundaries = [('candidate.json', 'capture'), ('checks/native.json', 'checks'),
                      ('reviews/circuit.json', 'review'),
                      ('adjudicator-execution.json', 'adjudication'),
                      ('events/bench-1.json', 'empirical')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            with writer_lock(root):
                pass
            for name, boundary in boundaries:
                with self.subTest(boundary=boundary):
                    write_json(root / name, {'execution': 'ok'})
                    (root / 'result.json.tmp').write_text('{')
                    before = inventory(Path(tmp))
                    report = diagnose(root)
                    self.assertEqual(report['coherence']['status'], 'coherent')
                    self.assertIn(boundary, [event['boundary'] for event in report['timeline']])
                    self.assertNotEqual(report['verification']['status'], 'passed')
                    self.assertTrue(report['ambiguous_transitions'])
                    self.assertEqual(inventory(Path(tmp)), before)

    def test_campaign_verification_differential_and_missing_lock(self):
        from agent_ee.campaign import verify_campaign
        from agent_ee.storage import write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'campaign'
            root.mkdir()
            (root / '.campaign-root').touch()
            write_json(root / 'config.json', {'review_profile': {}})
            write_json(root / 'profile.json', {})
            write_json(root / 'state.json', {'state': 'running', 'history': [],
                                            'config_sha256': digest(root / 'config.json')})
            before = inventory(Path(tmp))
            self.assertEqual(diagnose(root)['verification']['status'], 'passed')
            self.assertEqual(inventory(Path(tmp)), before)
            verify_campaign(root)
            for tampered in (False, True):
                if tampered:
                    write_json(root / 'profile.json', {'changed': True})
                before = inventory(Path(tmp))
                report = diagnose(root)
                if tampered:
                    with self.assertRaises(ValueError) as error:
                        verify_campaign(root)
                    self.assertEqual(report['verification']['error']['message'], str(error.exception))
                else:
                    self.assertEqual(report['verification']['status'], 'passed')
                    self.assertTrue(report['verified_facts'])
                self.assertEqual(inventory(Path(tmp)), before)

    def test_run_verification_differential_intact_and_tampered(self):
        from agent_ee.candidate import capture
        from agent_ee.cli import policy_files, runtime_files, save_result, verify
        from agent_ee.locking import writer_lock
        from agent_ee.storage import write_json
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            source = parent / 'design'
            source.write_text('original')
            scope = parent / 'scope.json'
            write_json(scope, {'schema': 'agent-ee.scope/1', 'checks': [],
                               'inputs': [{'path': str(source), 'at': 'design'}]})
            root = parent / 'run'
            root.mkdir()
            manifest = capture(scope, root)
            result = {'run_id': 'run-1', 'candidate_id': manifest['candidate_id'],
                      'state': 'running', 'policy_files': policy_files(),
                      'runtime_files': runtime_files(), 'checks': [], 'reviews': [],
                      'findings': [], 'limitations': []}
            save_result(root, result)
            with writer_lock(root):
                pass
            original = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            mutations = [None, ('inputs/design', 'changed'), ('candidate.json', '{}'),
                         ('packet.json', '{}'), ('reviews/physical.json', '{}'),
                         ('result.json', '{')]
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    for path in list(root.rglob('*')):
                        if path.is_file() and path not in original:
                            path.unlink()
                    for path, data in original.items():
                        path.write_bytes(data)
                    if mutation:
                        target = root / mutation[0]
                        target.parent.mkdir(exist_ok=True)
                        target.write_text(mutation[1])
                    before = inventory(parent)
                    try:
                        verify(root)
                    except (ValueError, OSError, KeyError, TypeError) as exc:
                        expected = (type(exc).__name__, str(exc))
                    else:
                        expected = None
                    report = diagnose(root)
                    if expected:
                        self.assertEqual((report['verification']['error']['type'],
                                          report['verification']['error']['message']), expected)
                    else:
                        self.assertEqual(report['verification']['status'], 'passed')
                    self.assertEqual(inventory(parent), before)

    def test_cli_machine_and_readable_reports_do_not_write(self):
        import contextlib
        import io
        import json
        from agent_ee.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            (root / 'result.json').write_text('[]')
            before = inventory(Path(tmp))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['diagnose', str(root), '--json']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['schema'], 'agent-ee.diagnosis/1')
            self.assertTrue(report['unavailable_facts'])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['diagnose', str(root)]), 0)
            self.assertIn('Snapshot: unverified', output.getvalue())
            self.assertIn('Unavailable', output.getvalue())
            self.assertEqual(inventory(Path(tmp)), before)

    def test_concurrent_writer_process_is_busy_without_mutation(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            command = [sys.executable, '-c',
                'from agent_ee.locking import writer_lock; import sys; '
                '\nwith writer_lock(sys.argv[1]):\n print("ready", flush=True); sys.stdin.read()', str(root)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                before = inventory(Path(tmp))
                report = diagnose(root)
                self.assertEqual(report['coherence']['status'], 'busy')
                self.assertFalse(report['verification']['coherent'])
                self.assertEqual(inventory(Path(tmp)), before)
            finally:
                process.communicate('', timeout=10)
            self.assertEqual(process.returncode, 0)


class InterruptedNativeDiagnosisTests(unittest.TestCase):
    # Share the existing synthetic solver/scripted subscription fixture. These
    # exercise real Bubblewrap execution, not live model or physical evidence.
    from test_campaign import CampaignIntegrationTests as _Fixture
    setUp = _Fixture.setUp
    agent = _Fixture.agent

    def test_abrupt_capture_check_review_adjudication_boundaries(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee import cli, checks, reviewers
        targets = [('capture', cli, 'capture'), ('checks', checks, 'run_checks'),
                   ('review', reviewers, 'initial_reviews'),
                   ('adjudication', cli, 'write_json')]
        for boundary, module, name in targets:
            with self.subTest(boundary=boundary):
                original = getattr(module, name)

                def interrupt(*args, **kwargs):
                    value = original(*args, **kwargs)
                    if boundary != 'adjudication' or Path(args[0]).name == 'adjudicator-execution.json':
                        raise SystemExit('injected process stop')
                    return value

                run = self.root / boundary
                with patch.object(module, name, side_effect=interrupt):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaisesRegex(SystemExit, 'injected process stop'):
                            cli.run_review(self.root / 'scope.json', run, 60)
                before = inventory(self.root)
                report = diagnose(run)
                self.assertEqual(report['coherence']['status'], 'coherent')
                self.assertEqual(report['recorded_state'], 'running')
                self.assertTrue(report['ambiguous_transitions'])
                self.assertIn(boundary, [item['boundary'] for item in report['timeline']])
                self.assertEqual(inventory(self.root), before)
                try:
                    cli.verify(run)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    self.assertEqual(report['verification']['error']['message'], str(exc))
                else:
                    self.assertEqual(report['verification']['status'], 'passed')

    def test_abrupt_empirical_registration_preserves_diagnostic_uncertainty(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import run_review
        from agent_ee import empirical
        from agent_ee.storage import read_json, write_json
        write_json(self.root / 'source/design.json', {'value': 5})
        run = self.root / 'initial'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(self.root / 'scope.json', run, 60), 0)
        result = read_json(run / 'result.json')
        sample = {'check_id': 'response', 'metric': 'output', 'value': 12,
                  'uncertainty': 1, 'unit': 'V', 'condition': 'load'}
        write_json(self.root / 'instrument.json', {'samples': [sample]})
        write_json(self.root / 'bench.json', {'schema': 'agent-ee.bench/1',
            'candidate_id': result['candidate_id'], 'prototype': 'test-fixture',
            'instrument': 'protocol-fixture', 'instrument_calibration': 'test-only',
            'procedure': 'synthetic integration test', 'observed_utc': '2026-10-17T00:00:00Z',
            'artifacts': [{'path': 'instrument.json', 'sha256': digest(self.root / 'instrument.json')}],
            'observations': [{**sample, 'artifact': 'instrument.json', 'locator': '0'}]})
        original = empirical.write_json

        def interrupt(path, value):
            original(path, value)
            if Path(path).name.startswith('bench-'):
                raise SystemExit('injected empirical stop')

        with patch.object(empirical, 'write_json', side_effect=interrupt):
            with self.assertRaisesRegex(SystemExit, 'injected empirical stop'):
                empirical.bench_review(run, self.root / 'bench.json', self.root / 'feedback', 60)
        before = inventory(self.root)
        report = diagnose(run)
        self.assertEqual(report['verification']['status'], 'failed')
        self.assertIn('empirical', [item['boundary'] for item in report['timeline']])
        self.assertEqual(inventory(self.root), before)
