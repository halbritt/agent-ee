"""AGENTEE-526 characterization; these fixtures confer no release qualification."""
import copy
from pathlib import Path
import tempfile
import unittest

import io
import json
import os
from unittest.mock import patch

from agent_ee.baseline import freeze_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.kicad_install import RECORD_ENV
from agent_ee.preflight import capture_expectation
from agent_ee.run_record import (KICAD_INSTALL_COPY, apply_decisions, capture_kicad_install, host_difference,
                                 policy_files, run_review, save_result, verify, verify_kicad_install_unchanged)
from agent_ee.storage import digest, read_json, write_json
try:
    from test_kicad_install import record as install_record
except ImportError:  # module-qualified invocation from the repository root
    from tests.test_kicad_install import record as install_record


class HistoricalReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'requirement.txt').write_text('Captured synthetic requirement.\n')
        scope_path = self.root / 'scope.json'
        write_json(scope_path, dict(schema='agent-ee.scope/1', checks=[],
            inputs=[dict(path=str(self.source), at='requirements')],
            limitations=['Synthetic integrity fixture; no native or engineering acceptance.']))
        self.run = self.root / 'run'
        self.run.mkdir()
        manifest = capture(scope_path, self.run)
        self.manifest = manifest
        self.policy = self.root / 'policy'
        self.policy.mkdir()
        for name in ('viewer.py', 'routing_rules.py'):
            (self.policy / name).write_text('# Synthetic policy observation.\n')
        self.binary = self.root / 'native-tool'
        self.binary.write_bytes(b'Synthetic runtime fingerprint; never executed.\n')
        self.host = self.observation()
        result = dict(run_id='historical-integrity-526', candidate_id=manifest['candidate_id'],
            state='interrupted', checks=[], reviews=[], review_executions=[], findings=[],
            adjudicated=False, limitations=manifest['scope']['limitations'], **copy.deepcopy(self.host))
        (self.run / 'work').mkdir()
        (self.run / 'work/evidence.txt').write_text('Retained synthetic observation.\n')
        freeze_evidence(self.run, [])
        save_result(self.run, result)

    def observation(self):
        return dict(policy_files={p.name: digest(p) for p in self.policy.glob('*.py')},
            runtime_files={'native-tool': dict(path=str(self.binary), sha256=digest(self.binary))})

    def test_unchanged_snapshot_verifies_without_promoting_incomplete_review(self):
        before = (self.run / 'result.json').read_bytes()
        result = verify(self.run, current_source=False, host=self.host)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(result['candidate_id'], verify_snapshot(self.run)['candidate_id'])
        self.assertEqual((self.run / 'result.json').read_bytes(), before)

    def test_changed_live_requirement_preserves_snapshot_but_refuses_current_source(self):
        (self.source / 'requirement.txt').write_text('A different current requirement.\n')
        historical = verify(self.run, current_source=False, host=self.host)
        self.assertEqual(historical['outcome'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'source inputs changed'):
            verify(self.run, host=self.host)

    def test_changed_implementation_is_reported_for_a_finished_run_and_refuses_continuing_work(self):
        # The AGENTEE-526 defect, resolved by the integrator decision of 2026-11-03: a finished run is
        # verified against its own records, including the implementation it recorded. Work that
        # continues on the run (current_host) still requires this checkout's implementation.
        for name in ('viewer.py', 'routing_rules.py'):
            with self.subTest(policy=name):
                path = self.policy / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'# changed observation\n')
                    verify_snapshot(self.run)
                    observations = []
                    result = verify(self.run, current_source=False, observations=observations, host=self.observation())
                    self.assertEqual(result['outcome'], 'incomplete')
                    self.assertEqual(observations, [{'name': 'agent-ee implementation', 'kind': 'policy_difference',
                                                     'files': [name]}])
                    with self.assertRaisesRegex(ValueError, 'review implementation changed'):
                        verify(self.run, current_source=False, host=self.observation(), current_host=True)
                finally:
                    path.write_bytes(original)

    def test_replaced_runtime_bytes_verify_from_the_record_and_are_reported(self):
        # Integrator decision 2026-11-03 ("versions should be fixed and recorded"): a finished run
        # used the tools it recorded. Another tool on the verifying host, such as a KiCad update,
        # is information; only a caller that claims this host's tools for the run refuses it.
        recorded = self.host['runtime_files']['native-tool']
        self.binary.write_bytes(b'Replacement runtime; never executed.\n')
        verify_snapshot(self.run)
        current = self.observation()
        observations = []
        result = verify(self.run, current_source=False, observations=observations, host=current)
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(observations, [{'name': 'native-tool', 'kind': 'host_difference', 'recorded': recorded,
                                         'current': current['runtime_files']['native-tool']}])
        with self.assertRaisesRegex(ValueError, 'tool runtime changed; rerun under the current tools'):
            verify(self.run, current_source=False, host=current, current_host=True)

    def test_tampered_runtime_record_refuses_on_any_host(self):
        # A run with a launch expectation records its runtime twice; a rewritten result disagrees.
        stored = read_json(self.run / 'result.json')
        stored['launch_expectation'] = capture_expectation(stored, self.manifest, [], inputs=self.run / 'inputs')
        save_result(self.run, stored)
        self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')
        forged = {'path': str(self.binary), 'sha256': '0' * 64}
        save_result(self.run, {**stored, 'runtime_files': {'native-tool': forged}})
        for host in (self.host, {**self.host, 'runtime_files': {'native-tool': forged}}):
            with self.subTest(host_matches_forgery=host is not self.host), \
                    self.assertRaisesRegex(ValueError, "tool runtime changed in the run's records"):
                verify(self.run, current_source=False, host=host)

    def test_retained_input_and_evidence_tamper_refuse_without_rewriting_history(self):
        before = (self.run / 'result.json').read_bytes()
        cases = (('inputs/requirements/requirement.txt', 'candidate snapshot changed'),
                 ('work/evidence.txt', 'execution/review evidence changed'))
        for relative, refusal in cases:
            with self.subTest(path=relative):
                path = self.run / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'tampered\n')
                    with self.assertRaisesRegex(ValueError, refusal):
                        verify(self.run, current_source=False, host=self.host)
                    self.assertEqual((self.run / 'result.json').read_bytes(), before)
                finally:
                    path.write_bytes(original)
        self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')


class RetainedKiCadInstallRecordTests(unittest.TestCase):
    """Every run retains the image's KiCad install record; verify checks that copy offline."""
    setUp = HistoricalReviewBoundaryTests.setUp
    observation = HistoricalReviewBoundaryTests.observation

    def write_record(self, name, version='10.0.8', **files):
        record = install_record(version)
        record['files'].update(files)
        path = self.root / name
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        return path, record

    def retain(self):
        path, record = self.write_record('image.install.json')
        stored = read_json(self.run / 'result.json')
        with patch.dict(os.environ, {RECORD_ENV: str(path)}):
            stored['kicad_install'] = capture_kicad_install(self.run, stored['runtime_files'])
        save_result(self.run, stored)
        return path, record, stored

    def test_capture_binds_the_record_to_the_installed_tools_observed(self):
        path, record = self.write_record('image.install.json')
        installed = {'kicad-cli': {'path': '/usr/bin/kicad-cli', 'sha256': record['files']['/usr/bin/kicad-cli']},
                     'pcbnew': {'path': '/usr/lib/python3/dist-packages/_pcbnew.so',
                                'sha256': record['files']['/usr/lib/python3/dist-packages/_pcbnew.so']}}
        with patch.dict(os.environ, {RECORD_ENV: str(path)}):
            entry = capture_kicad_install(self.run, installed)
            self.assertEqual(entry, {'path': str(path), 'sha256': digest(path), 'version': '10.0.8'})
            self.assertEqual((self.run / KICAD_INSTALL_COPY).read_bytes(), path.read_bytes())
            # A tool found elsewhere is not described by the record and is not bound.
            capture_kicad_install(self.run, {'kicad-cli': {'path': '/opt/other/kicad-cli', 'sha256': 'e' * 64}})
            for name in installed:
                other = {**installed, name: {**installed[name], 'sha256': 'e' * 64}}
                with self.subTest(differs=name), self.assertRaisesRegex(ValueError, 'does not describe the installed ' + name):
                    capture_kicad_install(self.run, other)
        path.write_text('{')
        with patch.dict(os.environ, {RECORD_ENV: str(path)}), self.assertRaisesRegex(ValueError, 'not JSON'):
            capture_kicad_install(self.run, installed)
        with patch.dict(os.environ, {RECORD_ENV: str(self.root / 'absent.json')}):
            self.assertIsNone(capture_kicad_install(self.run, installed))

    def test_retained_record_verifies_offline_on_a_host_with_another_record_or_none(self):
        _, _, stored = self.retain()
        self.assertIn(KICAD_INSTALL_COPY, stored['artifact_hashes'])
        other, _ = self.write_record('other.install.json', version='10.0.7')
        for host_record in (other, self.root / 'absent.json'):
            with self.subTest(host_record=host_record.name), patch.dict(os.environ, {RECORD_ENV: str(host_record)}):
                self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')

    def test_tampered_missing_or_unbound_retained_record_refuses(self):
        _, record, stored = self.retain()
        copy_path = self.run / KICAD_INSTALL_COPY
        original = copy_path.read_bytes()
        cases = {
            'changed bytes': (lambda: copy_path.write_bytes(original + b' '), 'retained KiCad install record changed'),
            'missing copy': (copy_path.unlink, 'retained KiCad install record is missing'),
            'other version': (lambda: save_result(self.run, {**stored, 'kicad_install': {**stored['kicad_install'],
                                                                                           'version': '10.0.7'}}),
                              'names another version'),
            'unbound tool': (lambda: save_result(self.run, {**stored, 'runtime_files': {
                                 **stored['runtime_files'], 'kicad-cli': {'path': '/usr/bin/kicad-cli', 'sha256': 'e' * 64}}}),
                             'does not describe the installed kicad-cli'),
        }
        with patch.dict(os.environ, {RECORD_ENV: str(self.root / 'absent.json')}):
            for name, (tamper, refusal) in cases.items():
                with self.subTest(tamper=name):
                    tamper()
                    with self.assertRaisesRegex(ValueError, refusal):
                        verify(self.run, current_source=False, host=self.host)
                    copy_path.write_bytes(original)
                    save_result(self.run, stored)
            self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')

    def test_a_record_changed_while_the_run_is_in_progress_refuses(self):
        path, _, stored = self.retain()
        with patch.dict(os.environ, {RECORD_ENV: str(path)}):
            verify_kicad_install_unchanged(stored)
            path.write_text(path.read_text().replace('10.0.8~ubuntu24.04.1', '10.0.8~ubuntu24.04.2'))
            with self.assertRaisesRegex(ValueError, 'KiCad install record changed during the run'):
                verify_kicad_install_unchanged(stored)
        with patch.dict(os.environ, {RECORD_ENV: str(self.root / 'absent.json')}):
            with self.assertRaisesRegex(ValueError, 'changed during the run'):
                verify_kicad_install_unchanged(stored)

    def test_continuing_work_requires_this_hosts_record_to_be_the_retained_one(self):
        path, _, stored = self.retain()
        decisions = self.root / 'decisions.json'
        write_json(decisions, {'schema': 'agent-ee.decisions/1', 'candidate_id': stored['candidate_id'],
                               'run_id': stored['run_id'], 'decisions': []})
        with patch.dict(os.environ, {RECORD_ENV: str(path)}):
            self.assertEqual(verify(self.run, current_source=False, host=self.host, current_host=True)['outcome'],
                             'incomplete')
            # A valid record with other package revisions; the retained copy and the binaries are unchanged.
            path.write_text(path.read_text().replace('10.0.8~ubuntu24.04.1', '10.0.8~ubuntu24.04.2'))
            with self.assertRaisesRegex(ValueError, 'differs from the one the run retained'):
                verify(self.run, current_source=False, host=self.host, current_host=True)
            with self.assertRaisesRegex(ValueError, 'differs from the one the run retained'):
                apply_decisions(self.run, decisions, host=self.host)
            self.assertEqual(list((self.run / 'events').glob('*-adjudication.json')), [])
            # Verifying the finished run still reads only its retained copy.
            self.assertEqual(verify(self.run, current_source=False, host=self.host)['outcome'], 'incomplete')


class DeclaredToolContinuationTests(unittest.TestCase):
    """DC tools a run declared at launch are compared with this host before work continues on it."""

    def test_a_changed_declared_dc_tool_is_reported_and_refuses_continuing_work(self):
        from agent_ee import dc_path
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # An extra tracked tool file, as pcbnew.py is: declared at launch, absent from runtime_files.
        tool = root / 'tracked-pcbnew.py'
        tool.write_bytes(b'wrapper at launch')
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/dc-path'
        scope = read_json(fixture / 'straight.scope.json')
        for entry in scope['inputs']:
            entry['path'] = str(fixture / entry['path'])
        self.enterContext(patch.object(dc_path, 'TOOL_PATHS', (*dc_path.TOOL_PATHS, str(tool))))
        self.enterContext(patch.dict(os.environ, {RECORD_ENV: str(root / 'absent.json'), 'AGENT_EE_SKIP_VISUALS': '1'}))
        write_json(root / 'qualification.json', dc_path.qualification())
        next(e for e in scope['inputs'] if e['at'] == 'qualification.json')['path'] = str(root / 'qualification.json')
        write_json(root / 'scope.json', scope)
        host = {'policy_files': policy_files(), 'runtime_files': {}}
        # A zero budget stops the check before any native execution; the launch expectation
        # still records the DC tools measured at launch.
        run_review(root / 'scope.json', root / 'run', 0, checks_only=True, host=host, output_stream=io.StringIO())
        stored = read_json(root / 'run/result.json')
        recorded = stored['launch_expectation']['declared_tools'][str(tool)]
        self.assertEqual(verify(root / 'run', host=host, current_host=True)['outcome'], 'incomplete')
        tool.write_bytes(b'wrapper replaced after the run')
        observations = []
        self.assertEqual(verify(root / 'run', host=host, observations=observations)['outcome'], 'incomplete')
        self.assertEqual(observations, [host_difference(str(tool), {'path': str(tool), 'sha256': recorded},
                                                        {'path': str(tool), 'sha256': digest(tool)})])
        with self.assertRaisesRegex(ValueError, 'declared tool changed since the run'):
            verify(root / 'run', host=host, current_host=True)
        decisions = root / 'decisions.json'
        write_json(decisions, {'schema': 'agent-ee.decisions/1', 'candidate_id': stored['candidate_id'],
                               'run_id': stored['run_id'], 'decisions': []})
        with self.assertRaisesRegex(ValueError, 'declared tool changed since the run'):
            apply_decisions(root / 'run', decisions, host=host)
        self.assertEqual(list((root / 'run/events').glob('*-adjudication.json')), [])


if __name__ == '__main__':
    unittest.main()
