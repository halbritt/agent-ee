import tempfile
import unittest
from pathlib import Path

from agent_ee.candidate import capture
from agent_ee.cli import save_result, policy_files, runtime_files
from agent_ee.storage import digest, write_json, read_json
from agent_ee.findings import collect_findings
from agent_ee.evidence_requests import export_request, verify_operation


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'board').write_text('owned candidate')
        scope = {'schema': 'agent-ee.scope/1', 'name': 'test', 'scope': 'test',
                 'board': 'design/board', 'schematic': 'design/board',
                 'inputs': [{'path': 'source', 'at': 'design'}],
                 'checks': [{'id': 'erc', 'kind': 'erc'}]}
        write_json(self.root / 'scope.json', scope)
        self.run = self.root / 'run'
        self.run.mkdir()
        manifest = capture(self.root / 'scope.json', self.run)
        review = {'limitations': [], 'coverage': ['owned board text'], 'role': 'circuit', 'candidate_id': manifest['candidate_id'], 'run_id': 'run1',
                  'findings': [{'id': 'question', 'assessment': 'concern', 'severity': 'major',
                  'evidence': [], 'title': 'Measure board', 'reasoning': 'Need measurement',
                  'condition': 'room temperature', 'mechanism': 'Unmeasured', 'location': 'board'}]}
        self.concern = collect_findings([review], [])[0]
        save_result(self.run, {'schema': 'agent-ee.result/1', 'run_id': 'run1',
            'candidate_id': manifest['candidate_id'], 'state': 'incomplete',
            'checks': [], 'reviews': [review], 'review_executions': [], 'findings': [self.concern],
            'adjudicated': False, 'limitations': [], 'policy_files': policy_files(),
            'runtime_files': runtime_files()})
        self.script = self.root / 'script.py'
        self.script.write_text('print("{\\"assertions\\": []}")\n')
        self.spec = {'mode': 'experiment', 'requirement_id': None, 'check_id': None,
                     'condition': 'room temperature', 'observable': 'board bytes', 'unit': 'bytes',
                     'rationale': 'Reproduce concern', 'interpreter': '/usr/bin/python3',
                     'resources': {'wall_seconds': 2, 'cpu_seconds': 1,
                                   'memory_bytes': 134217728, 'log_bytes': 4096}}

    def request(self, name='request'):
        return export_request(self.run, 'circuit/question', self.spec, self.root / name,
                              script=self.script)

    def test_request_is_standalone_content_bound_and_source_is_unchanged(self):
        before = {str(p): digest(p) for p in self.run.rglob('*') if p.is_file()}
        request = self.request()
        self.assertEqual(request['acceptance_authority'], 'none')
        self.assertEqual(request['concern']['id'], 'circuit/question')
        self.assertEqual(request['baseline_id'], None)
        self.assertEqual(before, {str(p): digest(p) for p in self.run.rglob('*') if p.is_file()})
        verify_operation(self.root / 'request')
        (self.root / 'request/script.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_operation(self.root / 'request')

    def test_replay_success_cannot_resolve_concern(self):
        from agent_ee.replay import replay
        from toolchain import require_toolchain
        require_toolchain()
        self.script.write_text('import json\nfrom pathlib import Path\n'
            'assert Path("/candidate/design/board").read_text() == "owned candidate"\n'
            'print(json.dumps({"assertions": [{"name": "read", "passed": True}]}))\n')
        self.request()
        before = digest(self.run / 'result.json')
        receipt = replay(self.root / 'request', self.root / 'replay')
        self.assertEqual(receipt['execution'], 'ok')
        self.assertEqual(receipt['parsing'], 'ok')
        self.assertEqual(receipt['assertions'], [{'name': 'read', 'passed': True}])
        self.assertEqual(receipt['acceptance_authority'], 'none')
        self.assertEqual(receipt['concern_status'], 'unresolved')
        self.assertEqual(before, digest(self.run / 'result.json'))
        verify_operation(self.root / 'replay')

    def test_unsupported_resources_and_interpreter_retain_receipts(self):
        from agent_ee.replay import replay
        self.spec['resources']['aggregate_memory_bytes'] = 1000000
        self.request()
        receipt = replay(self.root / 'request', self.root / 'unsupported')
        self.assertEqual(receipt['execution'], 'unsupported')
        self.assertIsNone(receipt['enforced_limits'])
        verify_operation(self.root / 'unsupported')
        del self.spec['resources']['aggregate_memory_bytes']
        self.spec['interpreter'] = '/missing/python'
        self.request('missing-request')
        receipt = replay(self.root / 'missing-request', self.root / 'missing')
        self.assertEqual(receipt['execution'], 'unsupported')
        self.assertEqual(receipt['acceptance_authority'], 'none')

    def test_tampered_candidate_and_script_fail_with_receipts(self):
        from agent_ee.replay import replay
        self.request()
        (self.run / 'inputs/design/board').write_text('changed candidate')
        receipt = replay(self.root / 'request', self.root / 'changed')
        self.assertEqual(receipt['execution'], 'failed')
        self.assertIn('snapshot changed', receipt['error'])
        verify_operation(self.root / 'changed')
        (self.run / 'inputs/design/board').write_text('owned candidate')
        (self.root / 'request/script.py').write_text('print("tampered")')
        receipt = replay(self.root / 'request', self.root / 'tampered')
        self.assertEqual(receipt['execution'], 'failed')
        self.assertIn('operation changed', receipt['error'])

    def test_path_role_mapping_and_running_run_refusals(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            export_request(self.run, 'circuit/question', self.spec, self.run / 'new', script=self.script)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            export_request(self.run, 'check/question', self.spec, self.root / 'wrong', script=self.script)
        self.spec['check_id'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'not declared'):
            self.request()
        self.spec['check_id'] = None
        result = read_json(self.run / 'result.json')
        result['state'] = 'running'
        save_result(self.run, result)
        with self.assertRaisesRegex(ValueError, 'settled'):
            self.request()

    def test_graduation_is_inert_and_cross_request_receipts_are_refused(self):
        from agent_ee.evidence_requests import export_graduation
        from agent_ee.replay import replay
        self.request()
        proposal = {k: 'explicit proposed value' for k in ('adapter_contract', 'supported_scope',
            'unsupported_scope', 'requirement_mapping', 'numerical_criteria', 'expected_control',
            'expected_seed', 'limitations')}
        before = {str(p): digest(p) for p in self.run.rglob('*') if p.is_file()}
        exported = export_graduation(self.root / 'request', proposal, self.root / 'graduation')
        self.assertEqual(exported['status'], 'proposed_unaccepted')
        self.assertEqual(exported['acceptance_authority'], 'none')
        self.assertEqual(before, {str(p): digest(p) for p in self.run.rglob('*') if p.is_file()})
        verify_operation(self.root / 'graduation')
        self.spec['interpreter'] = '/missing/python'
        self.request('other')
        replay(self.root / 'other', self.root / 'other-replay')
        with self.assertRaisesRegex(ValueError, 'belong'):
            export_graduation(self.root / 'request', proposal, self.root / 'wrong-graduation',
                              replay_dir=self.root / 'other-replay')

    def test_execution_assertions_and_failures_remain_separate(self):
        from agent_ee.replay import replay
        from toolchain import require_toolchain
        require_toolchain()
        cases = [
            ('exit-one', 'from pathlib import Path\nPath("/candidate/design/board").read_bytes()\nraise SystemExit(1)', 'failed', 'malformed'),
            ('false-assertion', 'print(\'{"assertions": [{"name": "unproven", "passed": false}]}\')', 'ok', 'ok'),
            ('duplicate-json', 'print(\'{"assertions": [], "assertions": []}\')', 'ok', 'malformed'),
            ('wrong-assertion', 'print(\'{"assertions": [{"name": "invalid", "passed": 1}]}\')', 'ok', 'malformed'),
            ('malformed', 'print("not JSON")', 'ok', 'malformed'),
            ('network', 'import socket\nsocket.socket().connect(("1.1.1.1", 80))', 'failed', 'malformed'),
            ('write-candidate', 'open("/candidate/design/board", "w").write("escape")', 'failed', 'malformed'),
            ('write-scratch', 'open("/work/output", "w").write("escape")', 'failed', 'malformed'),
            ('write-tmp', 'open("/tmp/output", "w").write("escape")', 'failed', 'malformed'),
            ('write-root', 'open("/escape", "w").write("escape")', 'failed', 'malformed'),
            ('symlink', 'import os\nos.symlink("/candidate/design/board", "/work/link")', 'failed', 'malformed'),
            ('memory', 'bytearray(512 * 1024 * 1024)', 'failed', 'malformed'),
            ('raise-limits', 'import resource\nresource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)', 'failed', 'malformed'),
            ('x32-abi', 'import ctypes\nctypes.CDLL(None).syscall(0x40000000 | 56, 0, 0)', 'failed', 'malformed'),
            ('fork', 'import os\nos.fork()', 'failed', 'malformed'),
            ('exec', 'import os\nos.execv("/bin/true", ["/bin/true"])', 'failed', 'malformed'),
            ('logs', 'print("X" * 100000)', 'output_limit', 'malformed'),
            ('timeout', 'import time\ntime.sleep(10)', 'timeout', 'malformed'),
        ]
        before = digest(self.run / 'result.json')
        for name, script, execution, parsing in cases:
            with self.subTest(case=name):
                self.script.write_text(script)
                self.request(name + '-request')
                receipt = replay(self.root / (name + '-request'), self.root / name)
                self.assertEqual(receipt['execution'], execution, receipt)
                self.assertEqual(receipt['parsing'], parsing)
                self.assertEqual(receipt['concern_status'], 'unresolved')
                self.assertEqual(receipt['acceptance_authority'], 'none')
                self.assertLessEqual(sum((self.root / name / (stream + '.log')).stat().st_size
                                         for stream in ('stdout', 'stderr')), 4096)
                self.assertEqual(before, digest(self.run / 'result.json'))
                verify_operation(self.root / name)

    def test_operation_cancellation_does_not_poison_next_replay(self):
        from agent_ee.executor import Cancellation
        from agent_ee.replay import replay
        from toolchain import require_toolchain
        require_toolchain()
        self.request()
        cancelled = Cancellation()
        cancelled.cancel()
        receipt = replay(self.root / 'request', self.root / 'cancelled', cancellation=cancelled)
        self.assertEqual(receipt['execution'], 'interrupted')
        receipt = replay(self.root / 'request', self.root / 'next')
        self.assertEqual(receipt['execution'], 'ok', receipt)

    def test_malformed_request_and_linked_script_are_retained_failures(self):
        from agent_ee.replay import replay
        self.request()
        (self.root / 'request/script.py').unlink()
        (self.root / 'request/script.py').symlink_to(self.script)
        receipt = replay(self.root / 'request', self.root / 'linked')
        self.assertEqual(receipt['execution'], 'failed')
        self.assertIn('nonregular', receipt['error'])
        (self.root / 'request/request.json').write_text('invalid JSON')
        receipt = replay(self.root / 'request', self.root / 'malformed-request')
        self.assertEqual(receipt['execution'], 'failed')
        verify_operation(self.root / 'malformed-request')

    def test_live_sigint_reaps_sandbox_and_later_replay_succeeds(self):
        import os
        import signal
        import subprocess
        import time
        from agent_ee.cli import main
        from toolchain import require_toolchain
        require_toolchain()
        self.script.write_text('import time\ntime.sleep(30)')
        self.spec['resources']['wall_seconds'] = 40
        self.request()
        output = self.root / 'interrupted'
        proc = subprocess.Popen(['/usr/bin/python3', '-m', 'agent_ee.cli', 'evidence-replay',
            str(self.root / 'request'), '--out', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        child = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and proc.poll() is None:
            children = Path(f'/proc/{proc.pid}/task/{proc.pid}/children').read_text().split()
            for pid in children:
                try:
                    command = Path(f'/proc/{pid}/cmdline').read_bytes()
                except FileNotFoundError:
                    continue
                if b'bwrap' in command and b'/bundle/launcher.py' in command:
                    child = int(pid)
                    break
            if child is not None:
                break
            time.sleep(.02)
        self.assertIsNotNone(child, 'replay never launched its sandbox')
        os.kill(proc.pid, signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 130, (stdout, stderr))
        receipt = read_json(output / 'receipt.json')
        self.assertEqual(receipt['execution'], 'interrupted')
        verify_operation(output)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        self.script.write_text('print(\'{"assertions": []}\')')
        self.request('after-interrupt')
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['evidence-replay', str(self.root / 'after-interrupt'),
                                   '--out', str(self.root / 'after-interrupt-replay')]), 0)

    def test_cpu_deadline_and_cli_exports(self):
        import contextlib
        import io
        from agent_ee.cli import main
        from agent_ee.replay import replay
        from toolchain import require_toolchain
        require_toolchain()
        self.script.write_text('while True: pass')
        self.spec['resources']['wall_seconds'] = 5
        write_json(self.root / 'spec.json', self.spec)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['evidence-request', str(self.run), '--finding', 'circuit/question',
                '--spec', str(self.root / 'spec.json'), '--script', str(self.script),
                '--out', str(self.root / 'request')]), 0)
        receipt = replay(self.root / 'request', self.root / 'cpu')
        self.assertEqual(receipt['execution'], 'failed', receipt)
        self.assertNotEqual(receipt['exit_code'], 0)
        self.assertTrue(receipt['isolation_established'])
        self.assertLess(receipt['duration_s'], 5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['verify-evidence-operation', str(self.root / 'cpu')]), 0)
        (self.root / 'cpu/stdout.log').write_bytes(b'tampered')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['verify-evidence-operation', str(self.root / 'cpu')]), 2)


    def test_malformed_request_nesting_and_operation_shape_retain_failure(self):
        from agent_ee.replay import replay
        self.request()
        original = (self.root / 'request/request.json').read_bytes()
        (self.root / 'request/request.json').write_text('[' * 2000 + '0' + ']' * 2000)
        receipt = replay(self.root / 'request', self.root / 'nested-request')
        self.assertEqual(receipt['execution'], 'failed')
        verify_operation(self.root / 'nested-request')
        (self.root / 'request/request.json').write_bytes(original)
        write_json(self.root / 'request/operation.json', [])
        receipt = replay(self.root / 'request', self.root / 'malformed-operation')
        self.assertEqual(receipt['execution'], 'failed')
        verify_operation(self.root / 'malformed-operation')



class NativeWitnessTests(unittest.TestCase):
    def test_real_checks_seal_and_witness_gate_are_not_replaced_by_replay(self):
        import contextlib
        import io
        from agent_ee.baseline import validate_witnesses
        from agent_ee.capabilities import convert_scope
        from agent_ee.cli import run_review, verify
        from agent_ee.evidence_requests import export_graduation
        from agent_ee.findings import adjudicate, validate_review
        from agent_ee.harnesses import evidence_mounts
        from agent_ee.replay import replay
        from agent_ee.reviewers import build_packet
        from toolchain import require_toolchain
        require_toolchain(native=True)
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for fixture, expected in (('control', 'pass'), ('seeded', 'defect')):
                with self.subTest(fixture=fixture):
                    base = root / fixture
                    base.mkdir()
                    scope = convert_scope(read_json(repo / 'fixtures/closed-loop/control.scope.json'))
                    for item in scope['inputs']:
                        path = Path(item['path'])
                        if not path.is_absolute():
                            item['path'] = str((repo / 'fixtures/closed-loop' / path).resolve())
                    scope['inputs'][0]['path'] = str(repo / 'fixtures/rc' / fixture)
                    write_json(base / 'scope.json', scope)
                    run = base / 'run'
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(run_review(base / 'scope.json', run, 90, checks_only=True), 1)
                    result = verify(run)
                    self.assertTrue(all(c['execution'] == 'ok' for c in result['checks']), result)
                    rc = next(c for c in result['checks'] if c['id'] == 'rc-step')
                    self.assertEqual(rc['outcome'], expected)
                    packet = build_packet(run, result['checks'], result['run_id'])
                    raw_review = {'schema': 'agent-ee.review/1', 'role': 'circuit',
                        'candidate_id': result['candidate_id'], 'run_id': result['run_id'],
                        'packet_sha256': packet['packet_sha256'], 'coverage': ['response'], 'limitations': [],
                        'findings': [{'id': 'response', 'title': 'Request response evidence',
                            'location': 'R1', 'condition': '1 V step', 'mechanism': 'Unverified response',
                            'severity': 'major', 'assessment': 'concern', 'reasoning': 'Compare declared response',
                            'evidence': [{'path': 'checks/rc-step.json', 'locator': 'outcome'}]}]}
                    review = validate_review(raw_review, result['candidate_id'], result['run_id'],
                                             packet['packet_sha256'], run)
                    result['reviews'] = [review]
                    result['findings'] = collect_findings([review], result['checks'])
                    write_json(run / 'reviews/circuit.json', review)
                    save_result(run, result)
                    spec = {'mode': 'experiment', 'requirement_id': 'R-response', 'check_id': 'rc-step',
                        'condition': '1 V step', 'observable': 'response', 'unit': 'V',
                        'rationale': 'Request missing verification', 'interpreter': '/usr/bin/python3',
                        'resources': {'wall_seconds': 2, 'cpu_seconds': 1,
                                      'memory_bytes': 134217728, 'log_bytes': 4096}}
                    script = base / 'experiment.py'
                    script.write_text('from pathlib import Path\n'
                        'Path("/candidate/project/rc_filter.kicad_sch").read_bytes()\nraise SystemExit(1)')
                    before = {str(p): digest(p) for p in run.rglob('*') if p.is_file()}
                    mounts = {role: evidence_mounts(run, role) for role in ('circuit', 'physical')}
                    export_request(run, 'circuit/response', spec, base / 'request', script=script)
                    receipt = replay(base / 'request', base / 'replay')
                    self.assertEqual(receipt['execution'], 'failed', receipt)
                    self.assertEqual(receipt['acceptance_authority'], 'none')
                    proposal = {k: 'proposed only' for k in ('adapter_contract', 'supported_scope',
                        'unsupported_scope', 'requirement_mapping', 'numerical_criteria',
                        'expected_control', 'expected_seed', 'limitations')}
                    export_graduation(base / 'request', proposal, base / 'graduation', replay_dir=base / 'replay')
                    self.assertEqual(before, {str(p): digest(p) for p in run.rglob('*') if p.is_file()})
                    for role in mounts:
                        self.assertEqual(mounts[role], evidence_mounts(run, role))
                        self.assertTrue(all('/request' not in str(p) and '/replay' not in str(p)
                                            for p, _ in mounts[role]))
                    witness = [{'requirement_id': 'R-response', 'check_id': 'rc-step'}]
                    citation = [{'path': 'checks/rc-step.json', 'locator': 'outcome'}]
                    with self.assertRaises(ValueError):
                        validate_witnesses(run, witness, [{'path': '../replay/receipt.json', 'locator': 'exit_code'}])
                    decisions = {'schema': 'agent-ee.decisions/1', 'candidate_id': result['candidate_id'],
                                 'run_id': result['run_id'], 'decisions': [{
                                     'finding_id': 'circuit/response', 'disposition': 'accepted',
                                     'reason': 'Script failure is not a defect', 'evidence': citation}]}
                    with self.assertRaisesRegex(ValueError, 'only executable check'):
                        adjudicate(result['findings'], decisions, run)
                    if expected == 'defect':
                        validate_witnesses(run, witness, citation)
                        decisions['decisions'] = [
                            {'finding_id': 'check/rc-step', 'disposition': 'accepted',
                             'reason': 'Fresh native netlist and ngspice establish declared bound failure',
                             'evidence': citation},
                            {'finding_id': 'circuit/response', 'disposition': 'duplicate',
                             'duplicate_of': 'check/rc-step', 'reason': 'Same declared response concern',
                             'evidence': citation}]
                        findings = adjudicate(result['findings'], decisions, run)
                        self.assertEqual(next(f for f in findings if f['id'] == 'check/rc-step')['status'], 'accepted')
                        self.assertEqual(next(f for f in findings if f['id'] == 'circuit/response')['status'], 'duplicate')
                    else:
                        with self.assertRaisesRegex(ValueError, 'failed criterion'):
                            validate_witnesses(run, witness, citation)
                    verify(run)
