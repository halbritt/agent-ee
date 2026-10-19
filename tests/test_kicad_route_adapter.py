"""Exit policy, path discovery, retention and verification of the kicad-route adapter.

The router in these tests is the coordinator-owned fixture in
kicad_route_fixture.py. They qualify the adapter's policy only. What the real
kicad-route and KRT do is exercised by test_kicad_route_native.py.
"""
import contextlib
import io
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
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from tests.toolchain import require_toolchain

REQUEST = ['--project', 'project', '--board', 'rc.kicad_pcb', '--schematic', 'rc.kicad_sch',
           '--name', 'rc', '--label', 'step-1', '--budget', '5', '--timeout', '30']
CONTRADICTIONS = ('exit0_incomplete', 'exit2_not_run', 'result_exit_mismatch', 'escaping_candidate',
                  'candidate_symlink', 'two_attempts', 'latest_elsewhere', 'unknown_key', 'malformed_json',
                  'missing_candidate', 'board_hash_mismatch', 'backend_env', 'extra_candidate_file',
                  'reference_differs', 'touch_input', 'exit_signal', 'missing_result_version',
                  'unknown_attempt_version', 'missing_attempt_schema', 'missing_attempt_rules',
                  'unknown_refusal_version', 'human_refusal')


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

    def verify(self, inputs=None, **kwargs):
        """Production verify, told the fixture router's pin and host layout."""
        return routing_adapter.verify(self.work, self.inputs if inputs is None else inputs,
                                      layout=self.layout, **kwargs)


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
        summary = case.verify()
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
        summary = case.verify()
        self.assertEqual((summary['proposal'], summary['candidate_sha256']),
                         ('eligible', case.report['candidate']['sha256']))
        self.assertEqual(summary['backend'], {'status': 'reported', 'reason': None, 'value': result['backend']})
        self.assertEqual(summary['reproducibility'], attempt.REPRODUCIBILITY['krt'])
        self.assertEqual(case.report['candidate_files'], {
            **json.loads((case.work / 'routing/attempt/snapshot.json').read_text())['files'],
            **case.report['candidate_editor_state'], 'rc.kicad_pcb': case.report['candidate']['sha256']})

    def test_exit_two_with_incomplete_routing_is_a_partial_proposal(self):
        case = self.case('incomplete').run()
        self.assertEqual(case.exit, 2)
        sem = case.report['semantics']
        self.assertEqual((sem['classification'], sem['proposal'], sem['coverage']),
                         ('incomplete', 'eligible_partial', 'routing_incomplete'))
        self.assertTrue((case.work / 'routing/candidate/project/rc.kicad_pcb').is_file())
        self.assertEqual(case.verify()['coverage'], 'routing_incomplete')

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
        self.assertTrue(attempt.refused_output((case.work / 'routing/kicad-route.stdout').read_text()))

    def test_unknown_result_version_refused(self):
        case = self.case('unknown_result_version').run()
        self.assert_no_proposal(case, 'contradiction', attempt.EXIT_CONTRADICTION)
        self.assertIn('unknown result schema version', case.report['semantics']['reasons'])

    def test_refill_modified_candidate_binds_to_the_board_drc_graded(self):
        case = self.case('refill_modified').run()
        self.assertEqual(case.exit, 0, case.report['semantics'])
        summary = case.verify()
        self.assertEqual(summary['proposal'], 'eligible')
        result = json.loads((case.work / 'routing/attempt/result.json').read_text())
        self.assertNotEqual(result['validation']['delivered_board_sha256'], summary['candidate_sha256'])
        self.assertEqual(result['validation']['candidate_board_sha256'], summary['candidate_sha256'])

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
                self.assertEqual(routing_adapter.verify(case.work, layout=case.layout)['classification'],
                                 'not_run')

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
        self.assertGreater(argv.index('--out'), argv.index('run'))
        self.assertIn('--json', argv)
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
                    case.verify()
        case = complete()
        with self.assertRaises(ValueError):
            case.verify({**case.inputs, 'project/rc.kicad_pcb': '0' * 64})
        # A forged eligible report over an exit-3 attempt cannot pass, nor can a planted board.
        case = self.case('validation_failed').run()
        rewrite(case, lambda r: r['semantics'].update(classification='complete', proposal='eligible',
                                                      coverage='routing_complete', adapter_exit=0))
        with self.assertRaises(ValueError):
            case.verify()
        case = self.case('validation_failed').run()
        (case.work / 'routing/candidate/project').mkdir(parents=True)
        (case.work / 'routing/candidate/project/rc.kicad_pcb').write_text('planted')
        with self.assertRaises(ValueError):
            case.verify()
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


def forge(case, *, files=None, report=None):
    """Rewrite retained JSON files and the report, then refresh every hash and the identity.

    The result is self-consistent bytes, as a forger with write access to the
    run would leave them; only re-derivation can refuse it."""
    routing = case.work / 'routing'
    stored = json.loads((routing / 'adapter-report.json').read_text())
    for rel, change in (files or {}).items():
        path = routing / 'attempt' / rel
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))
        stored['retained'][f'attempt/{rel}'] = attempt.digest(path)
    if report is not None:
        report(stored)
    attempt.write_report(routing / 'adapter-report.json', stored)


def set_validation(**fields):
    return {'result.json': lambda v: v['validation'].update(fields)}


class RetainedVerificationTest(unittest.TestCase):
    """verify() against the retained bytes, pins and constants, never the report's word.

    Each mutation starts from an honest fixture attempt, changes one claim and
    refreshes the hashes and identity, so it would pass a self-hash check."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-verify-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def case(self, scenario, argv=REQUEST, **kwargs):
        self.count += 1
        with contextlib.redirect_stdout(io.StringIO()):
            return Case(Path(self.tmp.name) / str(self.count), scenario, **kwargs).run(argv)

    def test_honest_complete_and_incomplete_attempts_verify(self):
        for scenario, proposal, coverage, code in (('complete', 'eligible', 'routing_complete', 0),
                                                   ('incomplete', 'eligible_partial', 'routing_incomplete', 2)):
            with self.subTest(scenario):
                case = self.case(scenario)
                result = json.loads((case.work / 'routing/attempt/result.json').read_text())
                summary = case.verify()
                pairs = summary.pop('pair_coverage')
                self.assertEqual(pairs['status'], 'unknown')
                self.assertFalse(pairs['acceptance_ready'])
                self.assertEqual(summary, {
                    'classification': scenario, 'proposal': proposal, 'coverage': coverage, 'router_exit': code,
                    'candidate_sha256': result['validation']['validated_board_sha256'],
                    'backend': {'status': 'reported', 'reason': None, 'value': result['backend']},
                    'tool_version': attempt.TOOL_VERSION, 'reproducibility': attempt.REPRODUCIBILITY['krt']})

    def test_pair_opt_in_binds_empty_and_nonempty_plans(self):
        plan = {'p': 'CAN+', 'n': 'CAN-', 'netclass': 'CAN', 'width_mm': 0.25, 'gap_mm': 0.2}
        for plans in ([], [plan]):
            with self.subTest(plans=plans):
                case = self.case({'name': 'complete', 'pairs': plans}, REQUEST + ['--diff-pairs', 'auto'])
                self.assertEqual(case.exit, 0, case.report['semantics'])
                self.assertEqual(case.verify()['proposal'], 'eligible')
                self.assertEqual(case.report['request']['diff_pairs'], 'auto')
                self.assertEqual(case.report['process']['argv'][-2:], ['--diff-pairs', 'auto'])

    def test_pair_schemas_and_keys_require_the_coordinator_opt_in_on_replay(self):
        for file, mutate in (
                ('result.json', lambda v: v.update(schema='kicad-route/route/2', pairs=[])),
                ('result.json', lambda v: v.update(pairs=[])),
                ('result.json', lambda v: v['settings'].update(diff_pairs='auto', pairs=[])),
                ('attempt.json', lambda v: v.update(schema='kicad-route/attempt/2')),
                ('attempt.json', lambda v: v.update(pairs=[], pair_routing={}))):
            with self.subTest(file=file, mutate=mutate):
                case = self.case('complete')
                forge(case, files={file: mutate})
                with self.assertRaisesRegex(ValueError, 'classify as contradiction'):
                    case.verify()

    def test_declared_pairs_refuse_downgrades_missing_keys_and_mismatched_plans(self):
        plan = {'p': 'CAN+', 'n': 'CAN-', 'netclass': 'CAN', 'width_mm': 0.25, 'gap_mm': 0.2}
        mutations = (
            ('result.json', lambda v: v.update(schema='kicad-route/route/1')),
            ('result.json', lambda v: v.update(schema='kicad-route/route-validate/2')),
            ('attempt.json', lambda v: v.update(schema='kicad-route/attempt/1')),
            ('attempt.json', lambda v: v.pop('schema')),
            ('attempt.json', lambda v: v.pop('pair_routing')),
            ('result.json', lambda v: v.pop('pairs')),
            ('result.json', lambda v: v['settings'].pop('diff_pairs')),
            ('result.json', lambda v: v['settings'].pop('pairs')),
            ('result.json', lambda v: v['settings'].update(pairs=None)),
            ('result.json', lambda v: v.update(pairs=[])),
            ('attempt.json', lambda v: v['pairs'][0].update(n='OTHER-')),
            ('attempt.json', lambda v: v['pairs'][0].update(initially_connected='unknown')),
            ('attempt.json', lambda v: v['pair_routing'].update(OTHER={})),
            ('result.json', lambda v: v['pairs'][0].update(width_mm=0.5)),
            ('attempt.json', lambda v: v.update(unexpected=True)),
        )
        for file, mutate in mutations:
            with self.subTest(file=file, mutate=mutate):
                case = self.case({'name': 'complete', 'pairs': [plan]}, REQUEST + ['--diff-pairs', 'auto'])
                forge(case, files={file: mutate})
                with self.assertRaisesRegex(ValueError, 'classify as contradiction'):
                    case.verify()

    def test_pair_request_rejects_unsupported_modes_and_backend(self):
        for args in (['--diff-pairs', 'manual'], ['--diff-pairs', ''],
                     ['--diff-pairs', 'auto', '--backend', 'freerouting']):
            with self.subTest(args=args), self.assertRaisesRegex(attempt.RequestError, 'diff-pairs'):
                attempt.parse_request(REQUEST + args)

    def test_a_fixture_attempt_is_not_a_pinned_kicad_route_attempt(self):
        case = self.case('complete')
        with self.assertRaisesRegex(ValueError, 'different kicad-route pin'):
            routing_adapter.verify(case.work, case.inputs)

    def test_eligible_mutations_of_retained_evidence_are_refused(self):
        def board(case):
            return case.report['candidate']['sha256']

        other = 'f' * 64
        mutations = {
            # The five retained-verifier mutations of the AGENTEE-93 review, then neighbours.
            'validated board hash': (dict(files=set_validation(validated_board_sha256=other)),
                                     'not the board the router validated'),
            'restored project files': (dict(files=set_validation(restored_project_files=['rc.kicad_pro'])),
                                       'restored project files'),
            'empty snapshot': (dict(files={'snapshot.json': lambda v: v.update(files={})}),
                               'lacks the board or schematic'),
            'reported exit': (dict(report=lambda r: r['semantics'].update(exit_code=3)),
                              'differs from the observed process exit'),
            'tool pin and commit': (dict(report=lambda r: r['tool'].update(pin='0' * 40, commit='0' * 40)),
                                    'different kicad-route pin'),
            'unknown result version': (dict(files={'result.json': lambda v: v.update(
                schema='kicad-route/route/999')}), 'classify as contradiction'),
            'candidate board hash': (dict(files=set_validation(candidate_board_sha256=other)),
                                     'not the board the router validated'),
            'refill flag': (dict(files=set_validation(refill_modified_board=True)),
                            'not the board the router validated'),
            'delivered board hash': (dict(files=set_validation(delivered_board_sha256=other)),
                                     'not the board the router validated'),
            'restored files absent': (dict(files={'result.json': lambda v: v['validation'].pop(
                'restored_project_files')}), 'restored project files'),
            'snapshot without schematic': (dict(files={'snapshot.json': lambda v: v['files'].pop('rc.kicad_sch')}),
                                           'lacks the board or schematic'),
            'snapshot not the input': (dict(files={'snapshot.json': lambda v: v['files'].update(
                {'rc.kicad_sch': other})}), 'differs from the attempt inputs'),
            'fingerprint': (dict(files={'result.json': lambda v: v['input'].update(fingerprint=other),
                                        'attempt.json': lambda v: v.update(input_fingerprint=other)}),
                            'not the retained snapshot fingerprint'),
            'process and reported exit': (dict(report=lambda r: (r['process'].update(exit_code=3),
                                                                 r['semantics'].update(exit_code=3))),
                                          'classify as contradiction'),
            'commit only': (dict(report=lambda r: r['tool'].update(commit='0' * 40)), 'not the pinned code'),
            'executable bytes': (dict(report=lambda r: r['tool']['kicad_route'].update(sha256=other)),
                                 'not the pinned code'),
            'executable location': (dict(report=lambda r: r['tool']['kicad_route'].update(path='/tmp/kicad-route')),
                                    'different kicad-route pin'),
            'adapter compatibility': (dict(report=lambda r: r['adapter'].update(compatibility='kicad-route@main')),
                                      'different kicad-route pin'),
            'write probe accepted': (dict(report=lambda r: r.update(state_write_probe=[
                {'directory': str(r['tool']['kicad_route']['path']), 'refused': False, 'errno': None}])),
                                     'OS-refused write probe'),
            'router argv': (dict(report=lambda r: r['process']['argv'].append('--force')), 'fixed adapter values'),
            'router environment': (dict(report=lambda r: r['process']['env'].update(OPENROUTER_API_KEY='x')),
                                   'fixed adapter values'),
            'backend identity': (dict(report=lambda r: r['backend']['value'].update(version='9.9')),
                                 'backend identity differs'),
            'backend from nowhere': (dict(files={'result.json': lambda v: v['backend'].update(build='forged')}),
                                     'backend identity differs'),
            'reproducibility': (dict(report=lambda r: r.update(reproducibility='reproducible measurement')),
                                'reproducibility label'),
            'tool version': (dict(report=lambda r: r.update(tool_version={'status': 'reported', 'value': '1.0',
                                                                           'reason': None})),
                             'tool version'),
            'candidate inventory extra file': (dict(report=lambda r: r['candidate_files'].update(
                {'extra.kicad_dru': other})), 'only the board routed'),
            'candidate changed a project file': (dict(report=lambda r: r['candidate_files'].update(
                {'rc.kicad_pro': other})), 'only the board routed'),
            'candidate inventory missing': (dict(report=lambda r: r.update(candidate_files={})),
                                            'only the board routed'),
            'editor state renamed': (dict(report=lambda r: r.update(candidate_editor_state={'other.kicad_prl': other})),
                                     'editor-state file'),
            'tool state changed': (dict(report=lambda r: r['identity_after']['code'].update(bin=None)),
                                   'is a contradiction'),
            'discovery problem': (dict(report=lambda r: r['discovery'].update(problems=['two attempts'])),
                                  'is a contradiction'),
            'attempt directory elsewhere': (dict(report=lambda r: r['discovery'].update(
                attempt_dir='out/runs/rc/step-1/../x')), 'fresh OUT stamp directory'),
            'result run_dir elsewhere': (dict(files={'result.json': lambda v: v.update(run_dir='/etc')}),
                                         'not inside the discovered attempt'),
            'missing artifacts': (dict(report=lambda r: r.update(missing_artifacts=['RESULT.md'])),
                                  'missing routing artifacts'),
            'rejected candidate': (dict(report=lambda r: r.update(rejected_candidate_sha256=other)),
                                   'rejected candidate'),
            'unknown report field': (dict(report=lambda r: r.update(accepted=True)), 'unknown or missing fields'),
            'dropped report field': (dict(report=lambda r: r.pop('candidate_files')), 'unknown or missing fields'),
            'malformed tool record': (dict(report=lambda r: r.update(tool=None)), 'malformed routing adapter report'),
        }
        for label, (change, message) in mutations.items():
            with self.subTest(label):
                case = self.case('complete')
                case.verify()
                forge(case, **change)
                with self.assertRaisesRegex(ValueError, message):
                    case.verify()
        # The partial proposal gets the same bindings.
        case = self.case('incomplete')
        forge(case, files=set_validation(validated_board_sha256=other))
        with self.assertRaisesRegex(ValueError, 'not the board the router validated'):
            case.verify()

    def test_non_eligible_mutations_of_retained_evidence_are_refused(self):
        mutations = {
            ('backend_failure', 'reported exit'): (dict(report=lambda r: r['semantics'].update(exit_code=0)),
                                                   'observed process exit'),
            ('backend_failure', 'malformed result'): (dict(report=lambda r: None),
                                                      'is a contradiction'),
            ('validation_failed', 'commit'): (dict(report=lambda r: r['tool'].update(commit='0' * 40)),
                                              'not the pinned code'),
            ('validation_failed', 'bound candidate'): (dict(report=lambda r: r.update(
                candidate_files={'rc.kicad_pcb': 'f' * 64})), 'records a bound candidate'),
            ('refused', 'reported exit'): (dict(report=lambda r: r['semantics'].update(exit_code=None)),
                                           'observed process exit'),
            ('refused', 'backend invented'): (dict(report=lambda r: r.update(backend={
                'status': 'reported', 'value': {'name': 'krt'}, 'reason': None})), 'backend identity differs'),
            ('refused', 'timed out'): (dict(report=lambda r: r['process'].update(timed_out=True)),
                                       'timed out or cancelled'),
            ('sleep', 'not timed out'): (dict(report=lambda r: r['process'].update(timed_out=False)),
                                         'without that observation'),
        }
        for (scenario, label), (change, message) in mutations.items():
            with self.subTest(scenario=scenario, mutation=label):
                argv = [*REQUEST[:-4], '--budget', '1', '--timeout', '2'] if scenario == 'sleep' else REQUEST
                case = self.case(scenario, argv)
                case.verify()
                if label == 'malformed result':
                    path = case.work / 'routing/attempt/result.json'
                    path.write_text('not json {')
                    change = dict(report=lambda r: r['retained'].update({'attempt/result.json': attempt.digest(path)}))
                forge(case, **change)
                with self.assertRaisesRegex(ValueError, message):
                    case.verify()

    def test_a_not_run_attempt_cannot_claim_router_output_or_another_pin(self):
        case = self.case('complete', probe=None)
        self.assertEqual(case.verify()['classification'], 'not_run')
        for label, change, message in (
                ('discovery', lambda r: r.update(discovery={'attempt_dir': None, 'problems': []}), 'router output'),
                ('reasons', lambda r: r['semantics'].update(reasons=[]), 'without its reasons'),
                ('pin', lambda r: r['tool'].update(pin='0' * 40), 'different kicad-route pin')):
            with self.subTest(label):
                path = case.work / 'routing/adapter-report.json'
                original = path.read_bytes()
                forge(case, report=change)
                with self.assertRaisesRegex(ValueError, message):
                    case.verify()
                path.write_bytes(original)


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
        self.pin = {'pin': FAKE_PIN, 'code': code}
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
        self.assertEqual(routing_adapter.verify(work, inputs, layout=self.pin)['proposal'], 'eligible')
        # The fixture router is not the pinned kicad-route; production verify says so.
        with self.assertRaisesRegex(ValueError, 'different kicad-route pin'):
            routing_adapter.verify(work, inputs)

    def test_exit_three_in_the_sandbox_delivers_no_board(self):
        record, report, work, candidate = self.sandboxed('validation_failed')
        self.assertEqual(record['exit_code'], 3)
        self.assertFalse(os.path.lexists(work / 'routing/candidate'))
        self.assertEqual(routing_adapter.verify(work, layout=self.pin)['classification'], 'validation_failed')


if __name__ == '__main__':
    unittest.main()
