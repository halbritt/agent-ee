"""Launch refusal tests count dispatches below the production guard."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import native_run
from agent_ee.reviewers import reviewer_call
from agent_ee.storage import read_json


class PreflightTests(unittest.TestCase):
    def test_missing_context_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temp, patch('agent_ee.harnesses.native_call') as provider:
            record, value = reviewer_call(Path(temp), 'circuit', {'harness': 'codex'}, '', {}, 10)
            self.assertEqual(record['execution'], 'not_run')
            self.assertIn('missing_context', record['error'])
            self.assertIsNone(value)
            provider.assert_not_called()
            self.assertEqual(read_json(Path(temp) / 'review-work/circuit/preflight/receipt.json')['result'], 'refused')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        (self.run / 'inputs').mkdir()
        from agent_ee.run_record import policy_files
        from agent_ee.preflight import capture_expectation
        from agent_ee.storage import digest
        self.binary = self.run / 'binary'
        self.binary.write_bytes(b'\x7fELFtest-only identity; dispatch is substituted')
        self.runtime = {name: {'path': str(self.binary), 'sha256': digest(self.binary)}
                        for name in ('codex', 'codex_tool_host', 'claude', 'bwrap', '/usr/bin/python3')}
        self.definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10,
                           'tool_mode': 'investigate'}
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': policy_files(),
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {'schema': 'agent-ee.scope/1'}},
            [self.definition])
        self.context = {'expected': self.expected, 'boundary': 'initial', 'deadline': time.monotonic() + 20}

    def call(self, role='circuit', context=None, *, policy=None):
        # Each launch is given the runtime (and optionally policy) fingerprints this test holds.
        context = dict(self.context if context is None else context)
        host = {**({'runtime_files': self.runtime} if self.runtime is not None else {}),
                **({'policy_files': policy} if policy is not None else {})}
        if host:
            context['host'] = host
        return reviewer_call(self.run, role, self.definition, '', {}, 10, launch_context=context)

    def assert_refused(self, reason):
        with patch('agent_ee.reviewers.dispatch') as provider:
            record, value = self.call()
        provider.assert_not_called()
        self.assertEqual(record['execution'], 'not_run')
        self.assertIn(reason, record['error'])
        self.assertIsNone(value)
        return record

    def test_runtime_and_policy_drift_refuse_before_probe_or_dispatch(self):
        with native_run.using(native_run.Recorded()) as probe:
            self.runtime = {**self.runtime, 'changed': {}}
            self.assert_refused('runtime_drift')
            self.assertEqual(probe.calls, [])
        with patch('agent_ee.reviewers.dispatch') as provider:
            record, _ = self.call('physical', policy={'changed': 'hash'})
            self.assertIn('policy_drift', record['error'])
            provider.assert_not_called()

    def test_supplied_policy_never_stands_for_the_loaded_code(self):
        # A caller (or a replay) that supplies a foreign policy matching the expectation still
        # meets this process's own loaded-code fingerprint, which nothing can supply (AGENTEE-545).
        from agent_ee.preflight import capture_expectation
        foreign = {'foreign.py': '0' * 64}
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': foreign,
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {'schema': 'agent-ee.scope/1'}},
            [self.definition])
        self.context['expected'] = self.expected
        with patch('agent_ee.reviewers.dispatch') as provider, native_run.using(native_run.Recorded()) as probe:
            record, value = self.call(policy=foreign)
        self.assertIn('policy_drift', record['error'])
        self.assertIsNone(value)
        self.assertEqual(probe.calls, [])
        provider.assert_not_called()

    def test_selected_missing_binary_is_specific_refusal(self):
        self.runtime = {**self.runtime, 'codex': {'path': None, 'sha256': None}}
        self.assert_refused('missing_binary')

    def test_malformed_expectation_and_definition_change_refuse(self):
        self.expected['identity'] = 'bad'
        self.assert_refused('malformed_expectation')

    def test_changed_declared_producer_refuses(self):
        from agent_ee.storage import identity
        from agent_ee.storage import digest
        producer = self.run / 'producer'
        producer.write_bytes(b'original declared producer')
        self.expected['declared_tools'] = {str(producer): digest(producer)}
        self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
        producer.write_bytes(b'replaced declared producer')
        self.assert_refused('declared_tool_drift')

    def optional_expectation(self, specs):
        from agent_ee.preflight import capture_expectation
        from agent_ee.run_record import policy_files
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': policy_files(),
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {
                'schema': 'agent-ee.scope/1', 'checks': specs}}, [self.definition])
        self.context['expected'] = self.expected

    def test_pdf_extractor_drift_blocks_dispatch(self):
        from agent_ee.storage import digest
        tool = self.run / 'extractor'
        tool.write_bytes(b'extractor v1')
        self.optional_expectation([{'kind': 'datasheet_facts', 'params': {'extractor': {
            'path': str(tool), 'sha256': digest(tool)}}}])
        tool.write_bytes(b'extractor v2')
        self.assert_refused('declared_tool_drift')

    def test_analyzer_helper_drift_blocks_dispatch(self):
        from agent_ee.kicad_happy import installation
        from agent_ee.storage import write_json
        root = self.run / 'analyzer'
        write_json(root / '.claude-plugin/plugin.json', {'name': 'kicad-happy', 'version': '2.3.1'})
        source = root / 'skills/probe.py'
        source.parent.mkdir()
        source.write_text('# original installed bytes')
        self.optional_expectation([{'kind': 'kicad_happy', 'params': {
            'installation': str(root), 'installation_sha256': installation(root)['sha256']}}])
        source.write_text('# changed after capture')
        self.assert_refused('declared_installation_drift')

    def test_severity_analyzer_pin_and_required_runtime_are_gated(self):
        from agent_ee.kicad_happy import installation
        from agent_ee.storage import write_json
        root = self.run / 'analyzer'
        write_json(root / '.claude-plugin/plugin.json', {'name': 'kicad-happy', 'version': '2.3.1'})
        source = root / 'skills/probe.py'
        source.parent.mkdir()
        source.write_text('# installed source')
        self.optional_expectation([{'kind': 'kicad_happy_severity_gate', 'params': {
            'installation': str(root), 'installation_sha256': installation(root)['sha256']}}])
        self.assertIn('/usr/bin/python3', self.expected['required_runtime'])
        self.assertEqual(self.expected['declared_installations'], {str(root): installation(root)['sha256']})
        source.write_text('# changed after capture')
        self.assert_refused('declared_installation_drift')

    def test_path_shadow_cannot_replace_absolute_active_solver_identity(self):
        # Probe identities independently of the test host's installed toolchain.
        def which(name):
            return '/shadow/ngspice' if name == 'ngspice' else name if name == '/usr/bin/ngspice' else None
        from agent_ee.run_record import runtime_files
        with patch('agent_ee.run_record.shutil.which', side_effect=which), \
                patch('agent_ee.harnesses.executable_for', return_value=None), \
                patch('agent_ee.run_record.pcbnew_extension', return_value=(None, '')), \
                patch('agent_ee.run_record.runtime_digest', side_effect=lambda path, **_: 'hash:' + path):
            actual = runtime_files()
        self.assertEqual(actual['ngspice']['path'], '/shadow/ngspice')
        self.assertEqual(actual['/usr/bin/ngspice'], {
            'path': '/usr/bin/ngspice', 'sha256': 'hash:/usr/bin/ngspice'})
        self.runtime['/usr/bin/ngspice'] = actual['/usr/bin/ngspice']
        self.optional_expectation([{'kind': 'active_spice', 'params': {}}])
        self.assertIn('/usr/bin/ngspice', self.expected['required_runtime'])
        self.runtime = {**self.runtime, '/usr/bin/ngspice': {'path': '/usr/bin/ngspice', 'sha256': 'changed'}}
        self.assert_refused('runtime_drift')

    def test_routing_equivalence_requires_pcbnew_before_dispatch(self):
        self.runtime['pcbnew'] = {'path': None, 'sha256': None}
        self.optional_expectation([{'kind': 'routing_equivalence', 'params': {}}])
        self.assertIn('pcbnew', self.expected['required_runtime'])
        self.assert_refused('missing_binary:pcbnew')

    def test_routing_requires_both_native_interfaces_before_dispatch(self):
        self.runtime['kicad-cli'] = self.runtime['/usr/bin/python3']
        self.runtime['pcbnew'] = {'path': None, 'sha256': None}
        self.optional_expectation([{'kind': 'routing_rules', 'params': {}}])
        self.assertIn('kicad-cli', self.expected['required_runtime'])
        self.assertIn('pcbnew', self.expected['required_runtime'])
        self.assert_refused('missing_binary:pcbnew')

    def test_fab_profile_requires_both_native_interfaces_before_dispatch(self):
        self.runtime['kicad-cli'] = self.runtime['/usr/bin/python3']
        self.runtime['pcbnew'] = {'path': None, 'sha256': None}
        self.optional_expectation([{'kind': 'fab_profile', 'params': {}}])
        self.assertIn('kicad-cli', self.expected['required_runtime'])
        self.assertIn('pcbnew', self.expected['required_runtime'])
        self.assert_refused('missing_binary:pcbnew')

    def test_active_spice_requires_solver_before_dispatch(self):
        self.runtime['/usr/bin/ngspice'] = {'path': None, 'sha256': None}
        self.optional_expectation([{'kind': 'active_spice', 'params': {}}])
        self.assert_refused('missing_binary:/usr/bin/ngspice')

    def test_fab_export_requires_native_cli_before_dispatch(self):
        self.runtime['kicad-cli'] = {'path': None, 'sha256': None}
        self.optional_expectation([{'kind': 'fab_export', 'params': {'netlist_check': 'netlist'}}])
        self.assertIn('kicad-cli', self.expected['required_runtime'])
        self.assert_refused('missing_binary:kicad-cli')

    def test_broken_and_malformed_probe_never_dispatch(self):
        class Malformed:
            def run(self, intent, **_):
                return {'execution': 'ok'}

        with native_run.using(Malformed()):
            self.assert_refused('malformed_probe_receipt')

    def test_runtime_probe_timeout_retains_valid_refusal_without_dispatch(self):
        import subprocess
        from agent_ee.preflight import verify_receipt
        refusals = []
        self.context['refuse'] = refusals.append
        self.runtime = None  # nothing supplied: the launch observes the host, and that probe times out
        with patch('agent_ee.run_record._runtime_files', side_effect=subprocess.TimeoutExpired(['probe'], 1)):
            record = self.assert_refused('runtime_probe_timeout')
        self.assertEqual(refusals, ['runtime_probe_timeout'])
        verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_native_networkless_probe_and_tamper_detection(self):
        from tests.toolchain import require_toolchain
        from agent_ee.preflight import verify_receipt
        from agent_ee.storage import write_json
        require_toolchain()
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})) as provider:
            record, _ = self.call()
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs['executable'], str(self.binary))
        self.assertLess(provider.call_args.args[5], 10)
        verify_receipt(self.run, record, self.expected, 'circuit', 'initial')
        path = self.run / record['preflight']['path']
        self.assertIn('--unshare-net', read_json(path)['probe']['sandbox_argv'])
        write_json(path, {'schema': 'invalid'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_duplicate_receipt_cannot_authorize_another_call(self):
        from agent_ee.storage import write_json
        write_json(self.run / 'review-work/circuit/preflight/receipt.json', {'schema': 'forged'})
        self.assert_refused('duplicate_receipt')
        self.assertEqual(read_json(self.run / 'review-work/circuit/preflight/receipt.json'), {'schema': 'forged'})

    def test_adjudicator_and_implementation_drift_each_block_dispatch(self):
        self.runtime = {**self.runtime, 'changed': {}}
        for boundary in ('adjudicator', 'implementation'):
            with self.subTest(boundary=boundary), patch('agent_ee.reviewers.dispatch') as provider:
                record, _ = self.call(boundary, {**self.context, 'boundary': boundary})
                self.assertIn('runtime_drift', record['error'])
                provider.assert_not_called()

    def test_deadline_and_owner_cancellation_refuse(self):
        from agent_ee.executor import Cancellation
        with patch('agent_ee.reviewers.dispatch') as provider:
            record, _ = self.call(context={**self.context, 'deadline': time.monotonic() - 1})
            self.assertIn('deadline_exhausted', record['error'])
            cancel = Cancellation()
            cancel.cancel()
            record, _ = reviewer_call(self.run, 'other', self.definition, '', {}, 10,
                                      launch_context={**self.context, 'host': {'runtime_files': self.runtime}},
                                      cancellation=cancel)
            self.assertIn('cancelled', record['error'])
            provider.assert_not_called()

    def test_actual_invalid_bwrap_option_refuses(self):
        from agent_ee.executor import sandbox_argv
        def invalid(*args, **kwargs):
            return ['bwrap', '--agent-ee-invalid-option', *sandbox_argv(*args, **kwargs)[1:]]
        with patch('agent_ee.executor.sandbox_argv', side_effect=invalid):
            self.assert_refused('sandbox_invalid_option')

    def test_claude_override_checked_path_is_the_mounted_path(self):
        import json
        import os
        from tests.toolchain import require_toolchain
        from agent_ee.storage import identity
        from agent_ee.harnesses import executable_for
        require_toolchain()
        self.definition = {**self.definition, 'harness': 'claude'}
        self.expected['definitions'] = [self.definition]
        self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
        home = self.run / 'credential-source'
        (home / '.claude').mkdir(parents=True)
        (home / '.claude/.credentials.json').write_text('scripted credential; no network')
        from agent_ee import native_run
        def provider(call):
            self.assertEqual(call.mounts[0], (str(self.binary), '/reviewer'))
            return native_run.Outcome(stdout=json.dumps({'subtype': 'success', 'structured_output': {}}).encode())
        calls = native_run.Recorded(provider)
        with patch.dict(os.environ, {'AGENT_EE_CLAUDE_BINARY': str(self.binary)}), \
             patch('agent_ee.reviewers.Path.home', return_value=home), \
             native_run.using(calls):
            self.assertEqual(executable_for('claude'), str(self.binary))
            # No tools makes the scripted terminal JSON sufficient for this adapter test.
            self.definition['tool_mode'] = 'none'
            self.expected['identity'] = identity({k: v for k, v in self.expected.items() if k != 'identity'})
            record, _ = self.call()
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(len(calls.calls), 1)

    def test_foreign_baseline_receipt_is_rejected_even_with_updated_link_hash(self):
        from tests.toolchain import require_toolchain
        from agent_ee.preflight import verify_receipt
        from agent_ee.storage import digest, write_json
        require_toolchain()
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})):
            record, _ = self.call()
        path = self.run / record['preflight']['path']
        receipt = read_json(path)
        receipt['baseline_id'] = 'foreign-baseline'
        write_json(path, receipt)
        record['preflight']['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'foreign'):
            verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_replaced_captured_binary_has_zero_provider_invocations(self):
        from agent_ee.storage import digest
        self.binary.write_bytes(b'\x7fELFreplaced after capture')
        # The host now reports the replaced binary's bytes.
        self.runtime = {name: {**entry, 'sha256': digest(entry['path'])} for name, entry in self.runtime.items()}
        self.assert_refused('runtime_drift')

    def test_required_native_check_binary_missing_at_capture_refuses_inference(self):
        from agent_ee.preflight import capture_expectation
        from agent_ee.run_record import policy_files
        self.runtime = {**self.runtime, 'pcbnew': {'path': None, 'sha256': None}}
        self.expected = capture_expectation({'run_id': 'run', 'policy_files': policy_files(),
            'runtime_files': self.runtime}, {'candidate_id': 'candidate', 'scope': {
                'schema': 'agent-ee.scope/1', 'checks': [{'id': 'width', 'kind': 'geometry'}]}}, [self.definition])
        self.context['expected'] = self.expected
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})) as provider:
            record, _ = self.call()
        provider.assert_not_called()
        self.assertIn('missing_binary:pcbnew', record['error'])

    def test_simulated_namespace_denial_is_specific_and_no_provider_runs(self):
        denied = native_run.Recorded(lambda call: native_run.Outcome(
            exit_code=1, stderr=b'bwrap: Creating new namespace failed: Operation not permitted'))
        with native_run.using(denied):
            self.assert_refused('sandbox_namespace_denied')

    def test_missing_receipt_link_is_not_admission_evidence(self):
        from agent_ee.preflight import verify_receipt
        with self.assertRaisesRegex(ValueError, 'missing or foreign'):
            verify_receipt(self.run, {'execution': 'ok'}, self.expected, 'circuit', 'initial')

    def test_unused_harness_change_is_recorded_and_launch_proceeds(self):
        from tests.toolchain import require_toolchain
        from agent_ee.preflight import verify_receipt
        from agent_ee.storage import digest, write_json
        require_toolchain()
        agy = self.run / 'agy'
        agy.write_bytes(b'\x7fELFunused harness v1')
        self.runtime['agy'] = {'path': str(agy), 'sha256': digest(agy)}
        self.optional_expectation([])
        changed = {'path': str(agy), 'sha256': 'replaced-by-host-update'}
        # The codex profile never invokes agy or claude.
        self.runtime = {**self.runtime, 'agy': changed, 'claude': {'path': None, 'sha256': None}}
        with patch('agent_ee.reviewers.dispatch', return_value=({'execution': 'ok'}, {})) as provider:
            record, _ = self.call()
        provider.assert_called_once()
        verify_receipt(self.run, record, self.expected, 'circuit', 'initial')
        path = self.run / record['preflight']['path']
        receipt = read_json(path)
        self.assertEqual(receipt['result'], 'ok')
        self.assertEqual(receipt['runtime_classification'], {
            'agy': 'unused', 'claude': 'unused', 'codex': 'used', 'codex_tool_host': 'used'})
        self.assertEqual([c['name'] for c in receipt['unused_runtime_changes']], ['agy', 'claude'])
        self.assertEqual(receipt['unused_runtime_changes'][0]['observed'], changed)
        # The recorded observation is bound to the observed runtime; it cannot be dropped.
        receipt['unused_runtime_changes'] = []
        write_json(path, receipt)
        record['preflight']['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'observation mismatch'):
            verify_receipt(self.run, record, self.expected, 'circuit', 'initial')

    def test_used_harness_and_tool_host_changes_still_refuse(self):
        for name in ('codex', 'codex_tool_host'):
            with self.subTest(name=name):
                self.runtime = {**self.runtime, name: {**self.runtime[name], 'sha256': 'replaced'}}
                record = self.assert_refused('runtime_drift')
                receipt = read_json(self.run / record['preflight']['path'])
                self.assertEqual(receipt['runtime_drift'], [name])
                self.runtime = {**self.runtime, name: self.expected['runtime_files'][name]}
                (self.run / record['preflight']['path']).unlink()

    def test_adjudicator_harness_counts_as_used_for_reviewer_launch(self):
        from agent_ee.preflight import runtime_drift
        definitions = [{'harness': 'codex'}, {'harness': 'claude'}]
        drift = runtime_drift({'claude': {'sha256': 'a'}}, {'claude': {'sha256': 'b'}}, definitions)
        self.assertEqual(drift['gated'], ['claude'])
        self.assertEqual(drift['unused_changes'], [])

    def test_unknown_definitions_and_native_tools_fail_closed(self):
        from agent_ee.preflight import runtime_drift
        before, after = {'agy': {'sha256': 'a'}, 'kicad-cli': {'sha256': 'a'}}, {'agy': {'sha256': 'b'}, 'kicad-cli': {'sha256': 'b'}}
        self.assertEqual(runtime_drift(before, after, [{'harness': 'claude'}])['gated'], ['kicad-cli'])
        for definitions in (None, [{'harness': 'other'}], ['malformed']):
            with self.subTest(definitions=definitions):
                drift = runtime_drift(before, after, definitions)
                self.assertEqual(drift['gated'], ['agy', 'kicad-cli'])
                self.assertEqual(drift['unused_changes'], [])
        self.assertEqual(runtime_drift(before, {**before, 'new': {}}, [{'harness': 'claude'}])['gated'], ['new'])


class RuntimeProbeCleanupTests(unittest.TestCase):
    @staticmethod
    def _proc_state(pid):
        # /proc/<pid>/stat can vanish before open() (FileNotFoundError / ENOENT)
        # or between open() and read() when the kernel reaps the exited task under
        # load (ProcessLookupError / ESRCH); both mean the descendant is gone.
        try:
            return Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return None

    def test_cancelled_probe_cleans_descendant_holding_output_pipe(self):
        import os
        import signal
        import subprocess
        from agent_ee.run_record import pcbnew_extension
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'child.pid'
            script = ('import subprocess,sys,pathlib; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                      f'pathlib.Path({str(marker)!r}).write_text(str(p.pid))')
            original = subprocess.Popen
            def launch(argv, **kwargs):
                return original(['/usr/bin/python3', '-c', script], **kwargs)
            end = time.monotonic() + 5
            def checkpoint():
                if marker.exists() or time.monotonic() >= end:
                    raise ValueError('operation cancelled')
            try:
                with patch('agent_ee.run_record.subprocess.Popen', side_effect=launch), \
                        self.assertRaisesRegex(ValueError, 'operation cancelled'):
                    pcbnew_extension(checkpoint=checkpoint)
                self.assertTrue(marker.exists(), 'test probe did not start child')
                pid = int(marker.read_text())
                deadline = time.monotonic() + 1
                state = self._proc_state(pid)
                while state not in (None, 'Z') and time.monotonic() < deadline:
                    time.sleep(.01)
                    state = self._proc_state(pid)
                self.assertIn(state, (None, 'Z'), f'runtime probe left descendant alive (state={state!r})')
            finally:
                if marker.exists():
                    try:
                        os.kill(int(marker.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        self.assertNotIn(self._proc_state(os.getpid()), (None, 'Z'))
        for exc in (FileNotFoundError(2, 'No such file or directory'), ProcessLookupError(3, 'No such process')):
            with patch.object(Path, 'read_text', side_effect=exc):
                self.assertIsNone(self._proc_state(os.getpid()))
