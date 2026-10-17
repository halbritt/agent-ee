"""Exit policy, path discovery, retention and verification of the kicad-route adapter.

The router in these tests is the coordinator-owned fixture in
kicad_route_fixture.py. They qualify the adapter's policy only. What the real
kicad-route and KRT do is exercised by test_kicad_route_native.py.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from toolchain import require_toolchain

REQUEST = ['--project', 'project', '--board', 'rc.kicad_pcb', '--schematic', 'rc.kicad_sch',
           '--name', 'rc', '--label', 'step-1', '--budget', '5', '--timeout', '30']
CONTRADICTIONS = ('exit0_incomplete', 'exit2_not_run', 'result_exit_mismatch', 'escaping_candidate',
                  'candidate_symlink', 'two_attempts', 'latest_elsewhere', 'unknown_key', 'malformed_json',
                  'missing_candidate', 'board_hash_mismatch', 'backend_env', 'extra_candidate_file',
                  'reference_differs', 'touch_input', 'exit_signal')


def files_under(root, skip=()):
    for path in Path(root).rglob('*'):
        if path.is_file() and not path.is_symlink() and not any(p in path.parts for p in skip):
            yield path


class Case:
    def __init__(self, root, scenario, *, probe=(), state=None):
        self.root = Path(root)
        self.checkout, self.code = make_checkout(self.root / 'checkout', scenario)
        self.candidate = make_candidate(self.root / 'candidate')
        self.work = self.root / 'work'
        self.work.mkdir()
        self.layout = {'work': self.work, 'candidate': self.candidate, 'checkout': self.checkout,
                       'state': state or self.checkout, 'pin': FAKE_PIN, 'code': self.code,
                       'probe': None if probe is None else list(probe)}

    def run(self, argv=REQUEST):
        self.inputs = attempt.files_of(attempt.tree(self.candidate))
        self.exit = attempt.main(list(argv), self.layout)
        path = self.work / 'routing/adapter-report.json'
        self.report = json.loads(path.read_text()) if path.is_file() else None
        return self


class AdapterPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def case(self, scenario, **kwargs):
        self.count += 1
        return Case(Path(self.tmp.name) / str(self.count), scenario, **kwargs)

    def assert_no_proposal(self, case, classification, exit_code):
        self.assertEqual(case.exit, exit_code, case.report['semantics'])
        self.assertEqual(case.report['semantics']['classification'], classification)
        self.assertEqual(case.report['semantics']['proposal'], 'none')
        self.assertIsNone(case.report['candidate'])
        self.assertFalse(os.path.lexists(case.work / 'routing/candidate'))
        summary = routing_adapter.verify(case.work, case.inputs)
        self.assertEqual(summary['proposal'], 'none')
        return summary

    def test_exit_zero_delivers_only_the_validated_contained_candidate(self):
        case = self.case('complete').run()
        self.assertEqual(case.exit, 0)
        sem = case.report['semantics']
        self.assertEqual((sem['classification'], sem['proposal'], sem['coverage'], sem['exit_code']),
                         ('complete', 'eligible', 'routing_complete', 0))
        delivered = case.work / 'routing/candidate/project/rc.kicad_pcb'
        self.assertIn(b'routed by fixture', delivered.read_bytes())
        result = json.loads((case.work / 'routing/attempt/result.json').read_text())
        self.assertEqual(case.report['candidate']['sha256'], result['validation']['delivered_board_sha256'])
        for name in ('attempt/result.json', 'attempt/attempt.json', 'attempt/RESULT.md',
                     'attempt/validate/preservation.json', 'attempt/work/krt.log', 'kicad-route.stdout'):
            self.assertIn(name, case.report['retained'])
        # The router's raw output board is a design file: hashed, never retained.
        self.assertIn('work/rc.kicad_pcb', case.report['omitted_design_files'])
        self.assertFalse((case.work / 'routing/attempt/work/rc.kicad_pcb').exists())
        self.assertEqual(case.report['backend'], {'status': 'reported', 'reason': None, 'value': result['backend']})
        self.assertEqual(case.report['tool_version']['status'], 'unknown')
        self.assertIsNone(case.report['tool_version']['value'])
        self.assertEqual(case.report['missing_artifacts'], [])
        # The router's editor-state file is hashed and reported, never delivered.
        self.assertEqual(list(case.report['candidate_editor_state']), ['rc.kicad_prl'])
        self.assertEqual([p.name for p in (case.work / 'routing/candidate/project').iterdir()], ['rc.kicad_pcb'])
        summary = routing_adapter.verify(case.work, case.inputs)
        self.assertEqual((summary['proposal'], summary['candidate_sha256']),
                         ('eligible', case.report['candidate']['sha256']))

    def test_exit_two_with_incomplete_routing_is_a_partial_proposal(self):
        case = self.case('incomplete').run()
        self.assertEqual(case.exit, 2)
        sem = case.report['semantics']
        self.assertEqual((sem['classification'], sem['proposal'], sem['coverage']),
                         ('incomplete', 'eligible_partial', 'routing_incomplete'))
        self.assertTrue((case.work / 'routing/candidate/project/rc.kicad_pcb').is_file())
        self.assertEqual(routing_adapter.verify(case.work, case.inputs)['coverage'], 'routing_incomplete')

    def test_exit_one_with_a_result_is_missing_routing_coverage(self):
        case = self.case('backend_failure').run()
        summary = self.assert_no_proposal(case, 'backend_failure', 1)
        self.assertEqual(summary['coverage'], 'missing_routing')
        self.assertEqual(summary['backend']['status'], 'reported')

    def test_early_exit_one_retains_the_partial_attempt_without_inventing_a_result(self):
        case = self.case('early_exit').run()
        self.assert_no_proposal(case, 'backend_failure', 1)
        self.assertEqual(case.report['missing_artifacts'], ['result.json', 'attempt.json', 'RESULT.md', 'validate'])
        self.assertIn('attempt/snapshot.json', case.report['retained'])
        self.assertEqual(case.report['backend'], {'status': 'unknown', 'value': None, 'reason': 'no result.json'})
        self.assertIn(b'could not read the input board',
                      (case.work / 'routing/kicad-route.stderr').read_bytes())

    def test_exit_three_board_never_leaves_the_attempt(self):
        case = self.case('validation_failed').run()
        self.assert_no_proposal(case, 'validation_failed', 3)
        self.assertIsNotNone(case.report['rejected_candidate_sha256'])
        # Only the raw router OUT still holds the sentinel board.
        for path in files_under(case.work / 'routing', skip=('out',)):
            self.assertNotIn(b'EXIT3-SENTINEL', path.read_bytes(), path)

    def test_exit_four_refusal_has_no_run_directory_and_no_proposal(self):
        case = self.case('refused').run()
        self.assert_no_proposal(case, 'refused', 4)
        self.assertIsNone(case.report['discovery']['attempt_dir'])
        self.assertEqual(case.report['missing_artifacts'], ['result.json', 'attempt.json', 'RESULT.md', 'validate'])
        self.assertIn(b'[route] refused:', (case.work / 'routing/kicad-route.stdout').read_bytes())

    def test_usage_exit_two_without_a_result_is_never_partial(self):
        case = self.case('argparse').run()
        self.assert_no_proposal(case, 'contradiction', attempt.EXIT_CONTRADICTION)
        self.assertEqual(case.report['semantics']['exit_code'], 2)

    def test_contradictory_or_escaping_outputs_fail_closed(self):
        scenarios = [*CONTRADICTIONS, {'name': 'complete', 'exit': 5}]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                case = self.case(scenario).run()
                self.assert_no_proposal(case, 'contradiction', attempt.EXIT_CONTRADICTION)
                self.assertEqual(case.report['semantics']['coverage'], 'unknown')

    def test_state_change_during_an_attempt_is_a_contradiction(self):
        case = self.case('mutate_state').run()
        self.assert_no_proposal(case, 'contradiction', attempt.EXIT_CONTRADICTION)
        self.assertIn('tool code or state changed during the attempt', case.report['semantics']['reasons'])
        self.assertNotEqual(case.report['identity_before'], case.report['identity_after'])

    def test_writable_state_is_refused_before_the_router_runs(self):
        case = self.case('complete', probe=None).run()
        self.assert_no_proposal(case, 'not_run', attempt.EXIT_REQUEST)
        self.assertIsNone(case.report['process'])
        self.assertTrue(case.report['state_write_probe'])
        self.assertFalse(any(p['refused'] for p in case.report['state_write_probe']))
        self.assertEqual(list(case.checkout.rglob('.agent-ee-write-probe-*')), [])
        self.assertFalse((case.work / 'routing/out').exists())

    def test_unpinned_code_wrong_commit_or_missing_dependency_is_not_run(self):
        mutations = {
            'tool file differs from pin: bin/kicad-route':
                lambda c: (c.checkout / 'bin/kicad-route').write_text('#!/usr/bin/python3\nraise SystemExit(0)\n'),
            'unpinned file in tool code: src/kicad_route/sitecustomize.py':
                lambda c: (c.checkout / 'src/kicad_route/sitecustomize.py').write_text(''),
            f'checkout HEAD {"0" * 40} is not the pinned {FAKE_PIN}':
                lambda c: (c.checkout / '.git/refs/heads/main').write_text('0' * 40 + '\n'),
            'missing installed backend dependency: KICAD_ROUTE_STATE/.venv-krt/bin/python':
                lambda c: (c.checkout / '.venv-krt/bin/python').unlink(),
            'candidate lacks project/rc.kicad_sch':
                lambda c: (c.candidate / 'project/rc.kicad_sch').unlink(),
            'candidate inputs contain links or special files':
                lambda c: (c.candidate / 'link').symlink_to('/etc/hostname'),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                case = self.case('complete')
                mutate(case)
                case.run()
                self.assertEqual(case.exit, attempt.EXIT_REQUEST)
                self.assertIn(reason, case.report['semantics']['reasons'])
                self.assertIsNone(case.report['process'])
                routing_adapter.verify(case.work)

    def test_request_must_match_the_fixed_shape(self):
        bad = {
            'writer-chosen backend environment': REQUEST + ['--backend-env', 'KICAD_X=1'],
            'unknown backend': REQUEST + ['--backend', 'other'],
            'zero budget': [*REQUEST[:-4], '--budget', '0', '--timeout', '30'],
            'timeout within the budget': [*REQUEST[:-4], '--budget', '30', '--timeout', '30'],
            'escaping project': ['--project', '../x', *REQUEST[2:]],
            'non-board file': ['--project', 'project', '--board', 'rc.txt', *REQUEST[4:]],
            'repeated option': REQUEST + ['--label', 'other'],
            'odd argument count': REQUEST + ['--state'],
            'missing option': REQUEST[2:],
        }
        for label, argv in bad.items():
            with self.subTest(label):
                case = self.case('complete').run(argv)
                self.assertEqual(case.exit, attempt.EXIT_REQUEST)
                self.assertFalse((case.work / 'routing').exists())

    def test_an_existing_routing_directory_is_never_reused(self):
        case = self.case('complete')
        (case.work / 'routing').mkdir()
        (case.work / 'routing/marker').write_text('earlier attempt')
        case.run()
        self.assertEqual(case.exit, attempt.EXIT_REQUEST)
        self.assertEqual([p.name for p in (case.work / 'routing').iterdir()], ['marker'])

    def test_router_gets_fixed_argv_and_environment_only(self):
        case = self.case('env')
        with patch.dict(os.environ, {'PYTHONPATH': '/evil', 'KICAD_ROUTE_LEDGER': 'off',
                                     'KICAD_ROUTE_OUT': '/evil', 'OPENROUTER_API_KEY': 'x'}):
            case.run()
        self.assertEqual(case.exit, 0, case.report['semantics'])
        seen = json.loads((case.work / 'routing/attempt/validate/env.json').read_text())
        routing = case.work / 'routing'
        self.assertEqual(seen['environ'], attempt.tool_env(case.layout))
        self.assertEqual(seen['environ']['KICAD_ROUTE_STATE'], str(case.checkout))
        self.assertEqual((seen['cwd'], seen['dont_write_bytecode'], seen['pycache_prefix']),
                         (str(routing), 1, str(routing / 'pycache')))
        argv = case.report['process']['argv']
        self.assertLess(argv.index('--out'), argv.index('run'))
        self.assertEqual(argv[argv.index('--out') + 1], str(routing / 'out'))
        self.assertEqual(argv[argv.index('--protect') + 1], 'existing')
        for flag in ('--backend-arg', '--backend-env', '--force', '--canary', '--rev', '--version'):
            self.assertNotIn(flag, argv)

    def _gone(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = Path(f'/proc/{pid}/stat').read_text().split(') ')[1][0]
            except (FileNotFoundError, IndexError):
                return True
            if state == 'Z':
                return True
            time.sleep(0.05)
        return False

    def test_timeout_kills_the_router_group_and_retains_what_exists(self):
        case = self.case('sleep').run([*REQUEST[:-4], '--budget', '1', '--timeout', '2'])
        self.assert_no_proposal(case, 'timeout', attempt.EXIT_TIMEOUT)
        self.assertTrue(case.report['process']['timed_out'])
        self.assertIn('attempt/attempt.json', case.report['retained'])
        self.assertIn('result.json', case.report['missing_artifacts'])
        child = int((case.work / 'routing/out/child.pid').read_text())
        self.assertTrue(self._gone(child), 'router descendant survived the timeout')

    def test_cancellation_is_retained_and_kills_the_router_group(self):
        case = self.case('sleep')
        driver = case.root / 'driver.py'
        driver.write_text(
            'import json, sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0, {str(Path(attempt.__file__).parent)!r})\n'
            'import kicad_route_attempt as a\n'
            'layout = {k: (Path(v) if isinstance(v, str) and k not in ("pin",) else v)\n'
            '          for k, v in json.loads(sys.argv[1]).items()}\n'
            'sys.exit(a.main(sys.argv[2:], layout))\n')
        layout = {k: (str(v) if isinstance(v, Path) else v) for k, v in case.layout.items()}
        proc = subprocess.Popen([sys.executable, '-I', '-B', str(driver), json.dumps(layout), *REQUEST])
        pid_file = case.work / 'routing/out/child.pid'
        deadline = time.monotonic() + 20
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(pid_file.is_file())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), attempt.EXIT_CANCELLED)
        case.report = json.loads((case.work / 'routing/adapter-report.json').read_text())
        case.exit, case.inputs = attempt.EXIT_CANCELLED, attempt.files_of(attempt.tree(case.candidate))
        self.assert_no_proposal(case, 'cancelled', attempt.EXIT_CANCELLED)
        self.assertTrue(self._gone(int(pid_file.read_text())), 'router descendant survived cancellation')

    def test_verify_rejects_tampered_or_forged_records(self):
        def complete():
            return self.case('complete').run()

        def rewrite(case, change):
            path = case.work / 'routing/adapter-report.json'
            report = json.loads(path.read_text())
            change(report)
            attempt.write_report(path, report)

        tampers = {
            'retained result edited': lambda c: (c.work / 'routing/attempt/result.json').write_text('{}'),
            'delivered board edited': lambda c: (c.work / 'routing/candidate/project/rc.kicad_pcb').write_text('x'),
            'extra delivered file': lambda c: (c.work / 'routing/candidate/project/extra').write_text('x'),
            'report edited without identity': lambda c: (c.work / 'routing/adapter-report.json').write_text(
                (c.work / 'routing/adapter-report.json').read_text().replace('"complete"', '"incomplete"')),
            'reclassified with fresh identity': lambda c: rewrite(c, lambda r: r['semantics'].update(
                classification='incomplete', proposal='eligible_partial', coverage='routing_incomplete',
                adapter_exit=2)),
            'request widened': lambda c: rewrite(c, lambda r: r['request'].update(backend_env='X=1')),
        }
        for label, tamper in tampers.items():
            with self.subTest(label):
                case = complete()
                tamper(case)
                with self.assertRaises(ValueError):
                    routing_adapter.verify(case.work, case.inputs)
        case = complete()
        with self.assertRaises(ValueError):
            routing_adapter.verify(case.work, {**case.inputs, 'project/rc.kicad_pcb': '0' * 64})
        # A forged eligible report over an exit-3 attempt cannot pass, nor can a planted board.
        case = self.case('validation_failed').run()
        rewrite(case, lambda r: r['semantics'].update(classification='complete', proposal='eligible',
                                                      coverage='routing_complete', adapter_exit=0))
        with self.assertRaises(ValueError):
            routing_adapter.verify(case.work, case.inputs)
        case = self.case('validation_failed').run()
        (case.work / 'routing/candidate/project').mkdir(parents=True)
        (case.work / 'routing/candidate/project/rc.kicad_pcb').write_text('planted')
        with self.assertRaises(ValueError):
            routing_adapter.verify(case.work, case.inputs)
        # A missing report (for example after the coordinator killed the sandbox) is unknown, not success.
        with self.assertRaises(ValueError):
            routing_adapter.verify(self.case('complete').work)

    def test_an_adapter_fault_has_its_own_exit_and_no_report(self):
        case = self.case('complete')
        with patch.object(attempt, 'retain', side_effect=OSError('disk full')), \
                patch('sys.stderr', new_callable=lambda: open(os.devnull, 'w')):
            case.run()
        self.assertEqual(case.exit, attempt.EXIT_ADAPTER_ERROR)
        self.assertIsNone(case.report)
        self.assertFalse(os.path.lexists(case.work / 'routing/candidate'))
        with self.assertRaises(ValueError):
            routing_adapter.verify(case.work)

    def test_write_probe_reports_and_removes_a_successful_probe(self):
        target = Path(self.tmp.name) / 'writable'
        target.mkdir()
        (record,) = attempt.write_probe([target])
        self.assertEqual((record['refused'], record['errno']), (False, None))
        self.assertEqual(list(target.iterdir()), [])
        (record,) = attempt.write_probe([target / 'absent'])
        self.assertEqual((record['refused'], record['errno']), (True, 'ENOENT'))

    def test_step_declares_a_networkless_coordinator_attempt(self):
        case = self.case('complete')
        with self.assertRaisesRegex(ValueError, 'not the pinned'):
            routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                 name='rc', label='step-1', checkout=case.checkout)
        with patch.object(attempt, 'PIN', FAKE_PIN), patch.object(attempt, 'PINNED_CODE', case.code):
            spec = routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                        name='rc', label='step-1', checkout=case.checkout, budget=60)
            with self.assertRaises(ValueError):
                routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                     name='rc', label='bad label', checkout=case.checkout)
        self.assertEqual(spec['argv'][:4], ['/usr/bin/python3', '-I', '-B', routing_adapter.ADAPTER_AT])
        self.assertEqual(attempt.parse_request(spec['argv'][4:])['state'], 'checkout')
        self.assertEqual(spec['mounts'], [
            {'source': str(routing_adapter.ADAPTER_SOURCE), 'at': routing_adapter.ADAPTER_AT},
            {'source': str(case.checkout.resolve()), 'at': '/opt/kicad-route'}])
        self.assertEqual(spec['writes'], {'project/rc.kicad_pcb': 'routing/candidate/project/rc.kicad_pcb'})
        self.assertEqual(spec['adoptable_exits'], [0, 2])
        self.assertNotIn('version_argv', spec)
        self.assertEqual(spec['timeout_s'], 2 * 60 + 300 + routing_adapter.STEP_MARGIN_S)
        self.assertIn(spec['backend_report']['artifact'], spec['artifacts'])


class SandboxedAdapterTest(unittest.TestCase):
    """The same adapter under the production Bubblewrap executor and read-only mounts."""

    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-sandbox-')
        self.addCleanup(self.tmp.cleanup)

    def sandboxed(self, scenario):
        root = Path(self.tmp.name) / (scenario if isinstance(scenario, str) else scenario['name'])
        root.mkdir()
        checkout, code = make_checkout(root / 'checkout', scenario)
        candidate = make_candidate(root / 'candidate')
        driver = root / 'driver'
        driver.mkdir()
        (driver / 'layout.json').write_text(json.dumps({'pin': FAKE_PIN, 'code': code}))
        (driver / 'run.py').write_text(
            'import json, sys\nsys.path.insert(0, "/opt/agent-ee-routing")\n'
            'import kicad_route_attempt as a\n'
            'cfg = json.load(open("/opt/driver/layout.json"))\n'
            'layout = a.default_layout(a.parse_request(sys.argv[1:]))\n'
            'layout.update(pin=cfg["pin"], code=cfg["code"])\n'
            'sys.exit(a.main(sys.argv[1:], layout))\n')
        before = attempt.tree(checkout)
        work = root / 'work'
        record = execute(candidate, work, ['/usr/bin/python3', '-I', '-B', '/opt/driver/run.py', *REQUEST],
                         timeout=60, extra_ro=[(routing_adapter.ADAPTER_SOURCE, routing_adapter.ADAPTER_AT),
                                               (checkout, '/opt/kicad-route'), (driver, '/opt/driver')])
        self.assertEqual(attempt.tree(checkout), before, 'host checkout/state changed')
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        return record, report, work, candidate

    def test_read_only_state_refuses_writes_and_a_complete_attempt_is_delivered(self):
        record, report, work, candidate = self.sandboxed('write_state_attempt')
        self.assertEqual(record['exit_code'], 0, report['semantics'])
        probes = report['state_write_probe']
        self.assertEqual([p['directory'] for p in probes],
                         ['/opt/kicad-route', '/opt/kicad-route/.venv-krt', '/opt/kicad-route/vendor/krt'])
        self.assertTrue(all(p['refused'] and p['errno'] == 'EROFS' for p in probes), probes)
        # The router's own write attempt into its state is refused by the OS too.
        write = json.loads((work / 'routing/attempt/validate/state-write.json').read_text())
        self.assertEqual(write['outcome'], 'OSError:30')
        inputs = attempt.files_of(attempt.tree(candidate))
        self.assertEqual(routing_adapter.verify(work, inputs)['proposal'], 'eligible')

    def test_exit_three_in_the_sandbox_delivers_no_board(self):
        record, report, work, candidate = self.sandboxed('validation_failed')
        self.assertEqual(record['exit_code'], 3)
        self.assertFalse(os.path.lexists(work / 'routing/candidate'))
        self.assertEqual(routing_adapter.verify(work)['classification'], 'validation_failed')


if __name__ == '__main__':
    unittest.main()
