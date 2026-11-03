"""Scripted subscription boundary; no network, model or native KiCad process.

StopTests start small Python helper processes (never KiCad) to show that a stopped or
finished phase leaves no process behind and that SIGTERM finalizes every report."""

import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

from agent_ee import design_generate as loop
from agent_ee.storage import write_json
from tests.test_design_compile import design_fixture

ROOT = Path(__file__).resolve().parents[1]
REAL_RUN_STEP = loop.run_step
REQUIREMENTS = 'Build a compact divider. Measure it on hardware.'
QUOTE = 'Build a compact divider'
AUTHORITY = 'Agent-authored choices and requirement quotations; not owner or datasheet rulings.'
# A phase that starts a child in its own process group (not holding the phase's output),
# records both pids, waits argv[2] seconds and exits with status argv[3], leaving the child running.
PHASE = textwrap.dedent('''
    import os, subprocess, sys, time
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(sys.argv[1] + '.tmp', 'w') as stream:
        stream.write(f'{os.getpid()} {child.pid}')
    os.replace(sys.argv[1] + '.tmp', sys.argv[1])
    time.sleep(float(sys.argv[2]))
    os._exit(int(sys.argv[3]))
''')
# A benchmark of three designs whose second design's build is a real PHASE under run_step.
DRIVER = textwrap.dedent('''
    import argparse, json, sys
    from pathlib import Path
    from unittest.mock import patch
    from agent_ee import design_generate as loop
    from agent_ee.storage import write_json
    from tests.test_design_compile import design_fixture

    root, marker = Path(sys.argv[1]), sys.argv[2]
    root.mkdir()
    statement = design_fixture(root / 'fixture')
    directory = root / 'requirements'
    directory.mkdir()
    for name in ('a', 'b', 'c'):
        (directory / (name + '.md')).write_text('Build a compact divider. Measure it on hardware.')
    real_step = loop.run_step

    def seat(run, label, definition, prompt, schema, timeout, **options):
        quote = 'Build a compact divider'
        return dict(execution='ok', billing_route='subscription'), dict(status='ready', reason='',
            statement_json=json.dumps(statement), trace=[dict(field='intent', quote=quote),
            dict(field='functional[0].text', quote=quote), dict(field='bench', quote='Measure it on hardware')],
            untraced=[], agent_selected=['Agent-selected values.'])

    def step(attempt, label, argv, seconds):
        if label == 'build' and attempt.parent.name == 'b':
            return real_step(attempt, label, [sys.executable, '-c', @PHASE@, marker, '300', '0'], seconds)
        (attempt / (label + '-execution')).mkdir()
        if label == 'capture':
            (attempt / 'capture').mkdir()
        elif label == 'compile':
            (attempt / 'bundle').mkdir()
        else:
            run = attempt / 'build'
            run.mkdir()
            write_json(run / 'build-report.json', dict(accepted=True, status='accepted',
                attempts=[dict(stage='package')], candidate=None, reason=''))
            write_json(run / 'qualification.json', dict(mode='typed',
                dimensions=dict(file_release=dict(status='pass'))))
        return dict(execution='ok', exit_code=0)

    args = argparse.Namespace(design_command='benchmark', requirements=directory, out=root / 'benchmark',
                              **dict(loop.DEFAULTS, max_attempts=1))
    with patch.object(loop.harnesses, 'native_call', side_effect=seat), \\
         patch.object(loop, 'run_step', side_effect=step):
        sys.exit(loop.execute(args))
''').replace('@PHASE@', repr(PHASE))


def alive(pid):
    try:
        data = Path(f'/proc/{pid}/stat').read_bytes()
    except OSError:
        return False
    return data[data.rindex(b')') + 2:].split()[0] != b'Z'


def wait_for(predicate, seconds=30):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def recorded_pids(marker):
    try:
        values = Path(marker).read_text().split()
    except OSError:
        return None
    return [int(value) for value in values] if len(values) == 2 else None


def kill_all(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class ScriptedLoop(unittest.TestCase):
    """Helpers only: a scripted author seat and scripted phases."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.requirements = self.root / 'requirements.md'
        self.requirements.write_text(REQUIREMENTS)
        self.statement = design_fixture(self.root / 'fixture')
        self.responses = []
        self.prompts = []
        self.seat_options = []
        self.steps = []
        self.argv = {}
        self.enterContext(patch.object(loop.harnesses, 'native_call', side_effect=self.seat))
        self.enterContext(patch.object(loop, 'run_step', side_effect=self.step))

    def response(self, statement=None):
        return dict(status='ready', reason='', statement_json=json.dumps(statement or self.statement),
            trace=[dict(field='intent', quote=QUOTE), dict(field='functional[0].text', quote=QUOTE),
                   dict(field='bench', quote='Measure it on hardware')],
            untraced=[], agent_selected=['Agent-selected values and outline; no owner or datasheet ruling.'])

    def unsupported(self, reason='unsupported design'):
        return dict(status='unsupported', reason=reason, statement_json='', trace=[], untraced=[], agent_selected=[])

    def seat(self, run, label, definition, prompt, schema, timeout, **options):
        self.prompts.append(prompt)
        self.seat_options.append(dict(options, timeout=timeout))
        value = self.responses.pop(0)
        if isinstance(value, tuple):
            return value
        return dict(execution='ok', billing_route='subscription'), value

    def step(self, attempt, label, argv, seconds):
        self.steps.append((attempt.name, label, seconds))
        self.argv[label] = argv
        (attempt / (label + '-execution')).mkdir()
        if label == 'capture':
            (attempt / 'capture').mkdir()
        elif label == 'compile':
            (attempt / 'bundle').mkdir()
        elif label == 'build':
            self.build_report(attempt)
        return dict(execution='ok', exit_code=0)

    def build_report(self, attempt, accepted=True, mode='typed', stage='package', release=None, reason=None):
        run = attempt / 'build'
        run.mkdir()
        write_json(run / 'build-report.json', dict(accepted=accepted, status='accepted' if accepted else 'failed',
            attempts=[dict(stage=stage)], candidate=None,
            reason=reason if reason is not None else ('' if accepted else 'routing search exhausted')))
        write_json(run / 'qualification.json', dict(mode=mode,
            dimensions=dict(file_release=dict(status=release or ('pass' if accepted else 'failed')))))

    def failing_search_build(self, attempt, seeds=None):
        """A build that fails its placement search, recording the statement's seed count."""
        if seeds is not None:
            seeds.append(json.loads((attempt / 'statement.json').read_text()).get('placement', {}).get('seeds'))
        self.build_report(attempt, accepted=False, stage='placement-routing')
        path = attempt / 'build/stages/05-placement/placement'
        path.mkdir(parents=True)
        write_json(path / 'job-report.json', dict(status='route_failed', reason='one open', final=dict(opens=1)))

    def with_build(self, build):
        """Scripted phases, except that build(attempt) writes the build and returns its record."""
        original = self.step

        def step(attempt, label, argv, seconds):
            if label != 'build':
                return original(attempt, label, argv, seconds)
            self.steps.append((attempt.name, label, seconds))
            (attempt / 'build-execution').mkdir()
            return build(attempt)
        return patch.object(loop, 'run_step', side_effect=step)

    def generate(self, **kwargs):
        return loop.generate(self.requirements, self.root / 'run', **kwargs)

    def saved(self, relative='run/generate-report.json'):
        return json.loads((self.root / relative).read_text())

    def feedback(self, index):
        """The feedback section of one author prompt."""
        section = self.prompts[index].split('EXACT PREVIOUS REFUSAL OR SHORT FAILURE FEEDBACK:\n', 1)[1]
        return section.split('\nReturn the final envelope within', 1)[0]

    def make_dir(self, names):
        directory = self.root / 'requirements'
        directory.mkdir()
        for name, text in names:
            (directory / (name + '.md')).write_text(text)
        return directory

    def profile(self):
        profile = self.root / 'profile.json'
        write_json(profile, dict(schema='agent-ee.profile/1'))
        return profile


class GenerateTests(ScriptedLoop):
    def test_refusal_feedback_retained_attempts_and_typed_report(self):
        bad = copy.deepcopy(self.statement)
        bad['board']['copper_layers'] = 4
        self.responses = [self.response(bad), self.response()]
        report = self.generate(max_attempts=2)
        self.assertEqual(report['status'], 'accepted')
        self.assertEqual([r['status'] for r in report['attempts']], ['refused', 'accepted'])
        self.assertIn('board.copper_layers', report['attempts'][0]['refusal_classes'])
        self.assertIn('only two copper layers', self.prompts[1])
        self.assertIn('"copper_layers": 4', self.prompts[1])
        self.assertEqual((self.root / 'run/attempt-001/refusal.txt').read_text(), report['attempts'][0]['reason'])
        self.assertEqual(report['claims'], dict(file_release=True, engineering_review='not_run',
                                               supplier_preview=False, physical_test=False))
        self.assertIn('No supplier preview, purchase, fabrication order, or physical test.', report['limitations'])
        for n in (1, 2):
            root = self.root / f'run/attempt-{n:03}'
            for name in ('prompt.txt', 'response.json', 'statement.json', 'attempt.json'):
                self.assertTrue((root / name).is_file(), name)
            self.assertTrue((root / 'review-attempts/author/0/execution.json').is_file())
        provenance = json.loads((self.root / 'run/attempt-002/provenance.json').read_text())
        self.assertEqual(provenance['authority'], AUTHORITY)
        self.assertEqual(json.loads((self.root / 'run/generate-report.json').read_text()), report)

    def test_retained_requirements_and_hash_preserve_original_bytes(self):
        raw = b'Build a compact divider.\r\nMeasure it on hardware.\r\n'
        self.requirements.write_bytes(raw)
        self.responses = [self.response()]
        report = self.generate()
        self.assertTrue(report['accepted'])
        self.assertEqual((self.root / 'run/requirements.md').read_bytes(), raw)
        self.assertEqual(report['requirements_sha256'], hashlib.sha256(raw).hexdigest())

    def test_invalid_json_is_retained_and_returned_to_author(self):
        bad = self.response()
        bad['statement_json'] = '{broken: 1}'
        self.responses = [bad, self.response()]
        report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertIn('{broken: 1}', self.prompts[1])
        self.assertIn('statement_json', report['attempts'][0]['refusal_classes'])
        self.assertEqual((self.root / 'run/attempt-001/statement.json').read_text(), '{broken: 1}')

    def test_attempt_limit_and_bad_trace_never_launch_capture(self):
        response = self.response()
        response['trace'][0]['quote'] = 'not in the requirements'
        self.responses = [response, response]
        report = self.generate(max_attempts=2)
        self.assertEqual(report['status'], 'attempts_exhausted')
        self.assertFalse(report['accepted'])
        self.assertEqual(len(self.prompts), 2)
        self.assertFalse(self.steps)
        self.assertIn('trace.quote', self.prompts[1])

    def test_time_limit_shared_across_attempts(self):
        clock = [100.0]
        def seat(*args, **options):
            clock[0] = 110.0
            return dict(execution='ok'), self.response({'invalid': 'statement'})
        with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = self.generate(max_seconds=2)
        self.assertEqual(report['status'], 'time_exhausted')
        self.assertEqual(len(report['attempts']), 1)
        self.assertFalse(self.steps)

    def test_seat_and_phases_get_only_the_remaining_time(self):
        clock = [1000.0]
        original_seat, original_step = self.seat, self.step
        def seat(*args, **options):
            clock[0] += 30
            return original_seat(*args, **options)
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original_step(attempt, label, argv, seconds)
            self.steps.append((attempt.name, label, seconds))
            self.argv[label] = argv
            (attempt / 'review').mkdir()
            write_json(attempt / 'review/report.json', dict(qualification=dict(dimensions=dict(engineering_review='pass'))))
            return dict(execution='ok', exit_code=0)
        self.responses = [self.response()]
        with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(loop.harnesses, 'native_call', side_effect=seat), \
             patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_seconds=100, author_seconds=600, review_profile=self.profile())
        self.assertTrue(report['accepted'])
        self.assertEqual(self.seat_options[0]['timeout'], 100)
        self.assertIn('Return the final envelope within 100 seconds.', self.prompts[0])
        self.assertEqual([(label, seconds) for _, label, seconds in self.steps],
                         [('capture', 70), ('compile', 70), ('build', 70), ('review', 70)])
        review = self.argv['review']
        self.assertEqual(review[review.index('--max-seconds') + 1], '70')
        self.assertEqual(report['attempts'][0]['duration_s'], 30)
        self.assertEqual(report['claims']['engineering_review'], 'pass')

    def test_bounds_are_validated_before_any_output(self):
        for kwargs in (dict(max_attempts=0), dict(max_attempts=101), dict(max_attempts=True),
                       dict(max_seconds=0), dict(max_seconds=86401), dict(author_seconds=0),
                       dict(author_seconds=1.5), dict(search_retries=-1), dict(search_retries=9),
                       dict(search_retries=False), dict(author='openrouter:x:high')):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                self.generate(**kwargs)
        self.assertFalse((self.root / 'run').exists())
        self.assertFalse(self.prompts)

    def test_malformed_envelope_is_a_bounded_refusal_not_a_subscription_failure(self):
        bad = self.response()
        del bad['agent_selected']
        self.responses = [bad, self.response()]
        report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual(report['attempts'][0]['status'], 'refused')
        self.assertIn('author_response', report['attempts'][0]['refusal_classes'])
        self.assertIn('supply all documented envelope fields', self.prompts[1])
        self.assertEqual(json.loads((self.root / 'run/attempt-001/review-attempts/author/0/response.json').read_text()), bad)

    def test_author_reuses_own_prior_evidence_read_only_without_copying_links(self):
        bad = self.response()
        bad['statement_json'] = '{}'
        self.responses = [bad, self.response()]
        original = self.seat
        def seat(run, *args, **options):
            if run.name == 'attempt-001':
                experiments = run / 'review-work/author/experiments'
                experiments.mkdir(parents=True)
                (experiments / 'parts.json').write_text('{"retained":true}')
                (experiments / 'skip-link').symlink_to('/never-follow-this')
            else:
                self.assertEqual((run / 'inputs/prior-author-experiments/parts.json').read_text(), '{"retained":true}')
                self.assertFalse((run / 'inputs/prior-author-experiments/skip-link').is_symlink())
            return original(run, *args, **options)
        with patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertIn('Return the final envelope within', self.prompts[0])

    def test_next_author_turn_gets_the_most_recent_author_experiments(self):
        self.responses = [self.response(), self.response()]
        offered = []
        original = self.seat
        def seat(run, *args, **options):
            prior = run / 'inputs/prior-author-experiments/parts.json'
            offered.append((run.name, prior.read_text() if prior.is_file() else None))
            experiments = run / 'review-work/author/experiments'
            experiments.mkdir(parents=True)
            (experiments / 'parts.json').write_text(run.name)
            return original(run, *args, **options)
        builds = [0]
        def build(attempt):
            builds[0] += 1
            if builds[0] == 1:
                self.failing_search_build(attempt)
            else:
                self.build_report(attempt, accepted=False)
            return dict(execution='failed', exit_code=1)
        with patch.object(loop.harnesses, 'native_call', side_effect=seat), self.with_build(build):
            report = self.generate(max_attempts=3)
        self.assertEqual([r['kind'] for r in report['attempts']], ['author', 'search_retry', 'author'])
        self.assertEqual(offered, [('attempt-001', None), ('attempt-003', 'attempt-001')])
        self.assertEqual(report['attempts'][2]['prior_experiments'], 'attempt-001')

    def test_subscription_failure_stops_without_retry(self):
        self.responses = [(dict(execution='failed', error='quota exhausted; no fallback'), None)]
        report = self.generate(max_attempts=4)
        self.assertEqual(report['status'], 'seat_failed')
        self.assertEqual(len(self.prompts), 1)
        self.assertFalse(self.steps)
        self.assertTrue((self.root / 'run/attempt-001/review-attempts/author/0/response.json').is_file())

    def test_author_timeout_with_attempts_left_starts_another_author_turn(self):
        original = self.seat
        def seat(run, *args, **options):
            if run.name == 'attempt-001':
                experiments = run / 'review-work/author/experiments'
                experiments.mkdir(parents=True)
                (experiments / 'envelope.json').write_text('{"nearly": "done"}')
            else:
                self.assertEqual((run / 'inputs/prior-author-experiments/envelope.json').read_text(), '{"nearly": "done"}')
            return original(run, *args, **options)
        self.responses = [(dict(execution='timeout', error='time limit'), None), self.response()]
        with patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual([r['status'] for r in report['attempts']], ['author_timeout', 'accepted'])
        self.assertIn('limit before it returned the envelope', self.feedback(1))

    def test_author_timeout_ends_the_design_only_when_attempts_or_time_run_out(self):
        self.responses = [(dict(execution='timeout'), None), (dict(execution='timeout'), None)]
        report = self.generate(max_attempts=2)
        self.assertEqual(report['status'], 'author_timeout')
        self.assertEqual(len(self.prompts), 2)
        clock = [100.0]
        def seat(*args, **options):
            clock[0] += 50
            return dict(execution='timeout'), None
        with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = loop.generate(self.requirements, self.root / 'spent', max_attempts=5, max_seconds=50)
        self.assertEqual(report['status'], 'author_timeout')
        self.assertEqual(len(report['attempts']), 1)

    def test_author_console_overflow_is_an_attempt_failure_not_a_seat_failure(self):
        self.responses = [(dict(execution='output_limit', error='harness execution failed'), None), self.response()]
        report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual(report['attempts'][0]['failing_stage'], 'author')
        self.assertIn('byte limit', self.feedback(1))
        self.assertEqual(self.seat_options[0]['log_bytes'], 32 * 1024 * 1024)

    def test_no_capture_launch_after_author_spends_deadline(self):
        clock = [100.0]
        def seat(*args, **options):
            clock[0] = 110.0
            return dict(execution='ok'), self.response()
        with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = self.generate(max_seconds=2)
        self.assertEqual(report['status'], 'time_exhausted')
        self.assertEqual(report['attempts'][0]['status'], 'time_exhausted')
        self.assertFalse(self.steps)

    def test_unexpected_error_is_retained_and_propagated(self):
        self.responses = [self.response()]
        with patch.object(loop, 'run_step', side_effect=TypeError('unexpected defect')):
            with self.assertRaisesRegex(TypeError, 'unexpected defect'):
                self.generate()
        report = self.saved()
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['attempts'][0]['status'], 'error')

    def test_incomplete_review_stops_further_seats(self):
        self.responses = [self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            folder = attempt / 'review'
            folder.mkdir()
            write_json(folder / 'report.json', dict(qualification=dict(
                dimensions=dict(engineering_review='not_assessed'))))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=3, review_profile=self.profile())
        self.assertEqual(report['status'], 'review_incomplete')
        self.assertEqual(len(self.prompts), 1)

    def test_unsupported_is_explicit_and_retained(self):
        self.responses = [self.unsupported('board.copper_layers: four layers unsupported')]
        report = self.generate()
        self.assertEqual(report['status'], 'unsupported')
        self.assertFalse(self.steps)

    def test_unsupported_needs_a_reason(self):
        with self.assertRaisesRegex(ValueError, 'explain the unsupported requirement'):
            loop.validate_response(self.unsupported(reason='  '))

    def test_compile_refusal_is_exact_and_missing_field_is_a_gap(self):
        self.responses = [self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label == 'compile':
                folder = attempt / 'compile-execution'
                folder.mkdir()
                (folder / 'stderr.log').write_text('unlocated preflight failure')
                return dict(execution='failed', exit_code=2)
            return original(attempt, label, argv, seconds)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2)
        self.assertIn('unlocated preflight failure', self.prompts[1])
        self.assertEqual(report['attempts'][0]['tool_gap'], 'Refusal does not name a design field')
        self.assertEqual((self.root / 'run/attempt-001/refusal.txt').read_text(), 'unlocated preflight failure')

    def test_phase_logs_reach_reasons_only_as_bounded_tails(self):
        self.responses = [self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'compile' or attempt.name != 'attempt-001':
                return original(attempt, label, argv, seconds)
            folder = attempt / 'compile-execution'
            folder.mkdir()
            (folder / 'stderr.log').write_text('E' * 50000 + '\n- parts.R1.lcsc: out of stock')
            (folder / 'stdout.log').write_text('O' * 50000)
            return dict(execution='failed', exit_code=2)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2)
        reason = report['attempts'][0]['reason']
        self.assertIn('earlier bytes of stderr.log omitted', reason)
        self.assertIn('earlier bytes of stdout.log omitted', reason)
        self.assertLess(len(reason), 2 * loop.LOG_TAIL_BYTES + 200)
        self.assertEqual(report['attempts'][0]['refusal_classes'], ['parts.R1.lcsc'])

    def test_file_acceptance_requires_typed_qualification(self):
        self.responses = [self.response()]
        with patch.object(self, 'build_report', side_effect=lambda attempt: ScriptedLoop.build_report(self, attempt, mode='legacy')):
            report = self.generate(max_attempts=1)
        self.assertFalse(report['accepted'])
        self.assertEqual(report['attempts'][0]['failing_stage'], 'package')

    def test_file_release_needs_both_a_passing_dimension_and_an_accepted_build(self):
        # Each guard on its own: an accepted build whose release dimension is pending, and a
        # passing release dimension on a build that did not accept.
        for name, accepted, release in (('pending', True, 'pending'), ('unaccepted', False, 'pass')):
            self.responses = [self.response()]
            def build(attempt):
                self.build_report(attempt, accepted=accepted, release=release)
                return dict(execution='ok', exit_code=0)
            with self.with_build(build):
                report = loop.generate(self.requirements, self.root / name, max_attempts=1)
            self.assertFalse(report['accepted'], name)
            self.assertIs(report['attempts'][0]['file_release'], False, name)
            self.assertEqual(report['claims']['file_release'], False, name)

    def test_failed_checks_keep_every_outcome_except_pass_and_info(self):
        run = self.root / 'native'
        (run / 'stages/04-check/review').mkdir(parents=True)
        write_json(run / 'stages/04-check/review/result.json', dict(checks=[
            *(dict(id=outcome, execution='ok', outcome=outcome) for outcome in ('pass', 'info', 'warn', 'fail', 'defect')),
            dict(id='crashed', execution='failed', outcome='pass')]))
        self.assertEqual([c['id'] for c in loop.failed_checks(run)], ['warn', 'fail', 'defect', 'crashed'])

    def test_build_failure_feedback_carries_check_records_and_placement_findings(self):
        self.responses = [self.response(), self.response()]
        def build(attempt):
            self.failing_search_build(attempt)
            (attempt / 'build/stages/04-check/review').mkdir(parents=True)
            write_json(attempt / 'build/stages/04-check/review/result.json', dict(checks=[dict(
                id='drc', execution='ok', outcome='fail', findings=['Clearance 0.1 mm below 0.2 mm'])]))
            return dict(execution='failed', exit_code=1)
        with self.with_build(build):
            report = self.generate(max_attempts=2, search_retries=0)
        self.assertEqual([r['kind'] for r in report['attempts']], ['author', 'author'])
        feedback = self.feedback(1)
        self.assertIn('Clearance 0.1 mm below 0.2 mm', feedback)
        self.assertIn('"opens": 1', feedback)

    def test_search_retry_adds_seeds_without_another_model_call(self):
        self.responses = [self.response()]
        calls = [0]
        def build(attempt):
            calls[0] += 1
            if calls[0] == 1:
                self.failing_search_build(attempt)
                return dict(execution='failed', exit_code=1)
            self.build_report(attempt, stage='placement-routing')
            return dict(execution='ok', exit_code=0)
        with self.with_build(build):
            report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(report['attempts'][1]['kind'], 'search_retry')
        self.assertEqual(report['attempts'][0]['placement_findings'][0]['final']['opens'], 1)
        first = json.loads((self.root / 'run/attempt-001/statement.json').read_text())
        second = json.loads((self.root / 'run/attempt-002/statement.json').read_text())
        self.assertEqual(second.pop('placement')['seeds'], 8)
        self.assertEqual(first, second)
        self.assertEqual([s[1] for s in self.steps].count('capture'), 1)
        # No unsent prompt and no answer that looks like a seat's; a record says what changed.
        retry = self.root / 'run/attempt-002'
        self.assertFalse((retry / 'prompt.txt').exists())
        self.assertFalse((retry / 'response.json').exists())
        record = json.loads((retry / 'automatic-retry.json').read_text())
        self.assertEqual((record['kind'], record['model_call'], record['source_attempt']),
                         ('search_retry', False, 'attempt-001'))
        self.assertEqual(record['changed'], {'placement.seeds': {'before': 4, 'after': 8}})

    def test_search_retry_seeds_stop_at_the_compiler_limit(self):
        statement = copy.deepcopy(self.statement)
        statement['placement'] = dict(seeds=40)
        self.responses = [self.response(statement)]
        seeds = []
        def build(attempt):
            if not seeds:
                self.failing_search_build(attempt, seeds)
                return dict(execution='failed', exit_code=1)
            seeds.append(json.loads((attempt / 'statement.json').read_text())['placement']['seeds'])
            self.build_report(attempt)
            return dict(execution='ok', exit_code=0)
        with self.with_build(build):
            report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual(seeds, [40, 64])

    def test_no_search_retry_when_seeds_cannot_increase(self):
        statement = copy.deepcopy(self.statement)
        statement['placement'] = dict(seeds=64)
        self.responses = [self.response(statement), self.response(statement)]
        def build(attempt):
            self.failing_search_build(attempt)
            return dict(execution='failed', exit_code=1)
        with self.with_build(build):
            report = self.generate(max_attempts=2)
        self.assertEqual([r['kind'] for r in report['attempts']], ['author', 'author'])
        self.assertIn('placement.seeds is already 64', report['attempts'][0]['search_retry_skipped'])
        self.assertIn('No automatic search retry', self.feedback(1))

    def test_runtime_pin_refusal_is_not_scored_as_a_design_failure(self):
        self.responses = [self.response()]
        def build(attempt):
            self.build_report(attempt, accepted=False, stage='placement-routing',
                              reason='kicad-route checkout is not the pinned commit')
            return dict(execution='failed', exit_code=1)
        with self.with_build(build):
            report = self.generate(max_attempts=3)
        self.assertEqual(report['status'], 'runtime_refused')
        self.assertEqual(len(self.prompts), 1)

    def test_review_blocker_goes_back_to_author_and_is_not_acceptance(self):
        self.responses = [self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            folder = attempt / 'review'
            folder.mkdir()
            write_json(folder / 'report.json', dict(outcome='defects_or_unresolved',
                qualification=dict(dimensions=dict(engineering_review='failed'))))
            (folder / 'review').mkdir()
            write_json(folder / 'review/result.json', dict(findings=[dict(id='supply', title='Wrong supply pin', disposition='accepted')]))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2, review_profile=self.profile())
        self.assertFalse(report['accepted'])
        self.assertIn('Wrong supply pin', self.prompts[1])
        self.assertEqual(report['attempts'][0]['engineering_review'], 'failed')

    def review_report(self):
        """A build review report of realistic size: its source section is about 25 KB."""
        runtime = {f'stage-{n:03}': dict(sha256=hashlib.sha256(str(n).encode()).hexdigest(),
                       argv=['agent-ee', 'build', 'run', f'--stage-{n:03}'], note='SOURCE-SECTION-MARKER ' * 2)
                   for n in range(120)}
        return dict(schema='agent-ee.build-review/1', accepted=False, outcome='defects_or_unresolved',
            checks=[dict(id=f'check-{n}', execution='ok', outcome='pass') for n in range(60)]
                + [dict(id='erc', execution='ok', outcome='fail')],
            limitations=['REPORT-LIMITATION-MARKER: no physical validation.'] * 20,
            qualification=dict(mode='typed', dimensions=dict(checks='pass', engineering_review='failed',
                file_release='pass')),
            source=dict(schema='agent-ee.build-source/1', build_runtime=runtime))

    def test_review_feedback_leads_with_blocking_findings_not_the_report(self):
        reviewed = self.review_report()
        self.assertGreater(len(json.dumps(reviewed['source'])), 24000)
        findings = [dict(id='area', title='Large board', disposition='noted', disposition_reason='x' * 5000),
                    dict(id='supply', title='Wrong supply pin', disposition='accepted',
                         condition='U1 VDD is tied to the output rail', recommendation='Tie U1 VDD to +3V3'),
                    dict(id='bulk', title='Missing bulk capacitor', disposition='deferred',
                         recommendation='Add 10 uF at the input')]
        self.responses = [self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            (attempt / 'review-execution').mkdir()
            # The review command prints its whole report; that console stays retained only.
            (attempt / 'review-execution/stdout.log').write_text(json.dumps(reviewed))
            (attempt / 'review/review').mkdir(parents=True)
            write_json(attempt / 'review/report.json', reviewed)
            write_json(attempt / 'review/review/result.json', dict(findings=findings))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2, review_profile=self.profile())
        feedback = self.feedback(1)
        self.assertTrue(feedback.startswith('Failing stage: review\nReason: Blocking engineering review findings'))
        self.assertIn('1. supply: Wrong supply pin [accepted]', feedback)
        self.assertIn('2. bulk: Missing bulk capacitor [deferred]', feedback)
        self.assertIn('Tie U1 VDD to +3V3', feedback)
        self.assertNotIn('Large board', feedback)
        self.assertLess(feedback.index('Add 10 uF at the input'), feedback.index('review/report.json'))
        self.assertIn('"erc"', feedback)
        for marker in ('SOURCE-SECTION-MARKER', 'REPORT-LIMITATION-MARKER'):
            self.assertNotIn(marker, self.prompts[1])
        self.assertLessEqual(len(feedback), loop.FEEDBACK_CHARS)
        self.assertEqual([f['id'] for f in report['attempts'][0]['review_findings']], ['supply', 'bulk'])
        self.assertEqual(self.saved('run/attempt-001/review/report.json'), reviewed)

    def test_feedback_excerpt_is_capped_and_marked(self):
        self.responses = [self.response(), self.response()]
        def build(attempt):
            self.build_report(attempt, accepted=False, reason='R' * 30000)
            return dict(execution='failed', exit_code=1)
        with self.with_build(build):
            self.generate(max_attempts=2)
        feedback = self.feedback(1)
        self.assertTrue(feedback.endswith('\n[Feedback excerpt ends; full refusal retained in preceding attempt.]\n'))
        self.assertLessEqual(len(feedback), 14000 + 100)
        self.assertEqual((self.root / 'run/attempt-001/refusal.txt').read_text(), 'R' * 30000)

    def test_requirements_over_the_size_limit_are_refused_naming_it(self):
        self.requirements.write_bytes(b'Build a compact divider. ' + b'x' * 40000)
        with self.assertRaisesRegex(ValueError, '32768-byte limit'):
            self.generate()
        self.assertFalse((self.root / 'run').exists())
        self.assertFalse(self.prompts)

    def test_invalid_or_oversized_statement_is_echoed_as_a_marked_excerpt(self):
        big = self.response()
        big['statement_json'] = '{"x": "' + 'A' * 100000 + '"'  # invalid: the object is not closed
        self.responses = [big, self.response()]
        report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertLess(len(self.prompts[1]), 40000)
        self.assertIn(f'[Statement excerpt cut at 20000 of {len(big["statement_json"])} characters', self.prompts[1])
        self.assertEqual((self.root / 'run/attempt-002/inputs/current-statement.txt').read_text(), big['statement_json'])

    def test_kicad_root_outside_usr_is_bound_read_only_for_the_author(self):
        libraries = self.root / 'kicad'
        libraries.mkdir()
        self.responses = [self.response()]
        self.generate(kicad_root=libraries)
        self.assertEqual(tuple(self.seat_options[0]['extra_mounts']), ((libraries.resolve(), '/kicad'),))
        self.assertIn('--kicad-root /kicad\n', self.prompts[0])
        self.assertNotIn(str(libraries), self.prompts[0])
        self.assertEqual(self.argv['capture'][-2:], ['--kicad-root', str(libraries)])
        with self.assertRaisesRegex(ValueError, 'kicad_root'):
            loop.generate(self.requirements, self.root / 'other', kicad_root=self.root / 'missing')

    def test_kicad_root_under_usr_needs_no_mount(self):
        self.responses = [self.response()]
        self.generate(kicad_root=Path('/usr/share'))
        self.assertEqual(tuple(self.seat_options[0]['extra_mounts']), ())
        self.assertIn('--kicad-root /usr/share\n', self.prompts[0])

    def test_claims_describe_the_final_outcome_not_an_earlier_attempt(self):
        self.responses = [self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if label == 'compile' and attempt.name == 'attempt-002':
                (attempt / 'compile-execution').mkdir()
                (attempt / 'compile-execution/stderr.log').write_text('Design refused:\n- parts.R1.lcsc: out of stock')
                return dict(execution='failed', exit_code=2)
            if label != 'review':
                return original(attempt, label, argv, seconds)
            (attempt / 'review/review').mkdir(parents=True)
            write_json(attempt / 'review/report.json', dict(qualification=dict(dimensions=dict(engineering_review='failed'))))
            write_json(attempt / 'review/review/result.json', dict(findings=[dict(id='x', title='Blocker', disposition='accepted')]))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2, review_profile=self.profile())
        self.assertEqual((report['status'], report['accepted']), ('attempts_exhausted', False))
        self.assertIs(report['attempts'][0]['file_release'], True)
        self.assertEqual(report['claims'], dict(file_release=False, engineering_review='not_claimed',
                                               supplier_preview=False, physical_test=False))

    def test_utilisation_uses_final_positions_rotation_and_union(self):
        run = self.root / 'native'
        candidate = run / 'stages/package/candidate'
        candidate.mkdir(parents=True)
        write_json(run / 'build-report.json', dict(candidate='stages/package/candidate', completed=[dict(stage='placement-routing')]))
        (candidate / 'project').mkdir()
        (candidate / 'project/owned.kicad_pcb').write_text('''(kicad_pcb
          (footprint "one" (at 10 20 90)
            (fp_rect (start 0 0) (end 2 4) (layer "F.CrtYd")))
          (footprint "two" (at 12 20)
            (fp_rect (start 0 -2) (end 4 0) (layer "F.CrtYd"))))''')
        result = loop.outline_utilisation(run, self.statement)
        # First rotated box: x=10..14, y=18..20. Second: x=12..16,
        # y=18..20. Union is 12, not their summed area of 16.
        self.assertEqual(result['courtyard_bbox_union_mm2'], 12)
        self.assertEqual(result['fraction'], 12 / 600)
        self.assertEqual(len(result['board_sha256']), 64)

    def test_utilisation_does_not_treat_schematic_positions_as_final_placement(self):
        run = self.root / 'native'
        run.mkdir()
        write_json(run / 'build-report.json', dict(candidate='stages/schematic/candidate',
            completed=[dict(stage='schematic')]))
        result = loop.outline_utilisation(run, self.statement)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('not final placement', result['reason'])

    def test_runtime_refusal_is_not_a_search_retry(self):
        run = self.root / 'native'
        path = run / 'stages/placement/placement'
        path.mkdir(parents=True)
        write_json(path / 'job-report.json', dict(status='refused', reason='pin mismatch'))
        self.assertFalse(loop.search_failure(dict(attempts=[dict(stage='placement-routing')]), run))

    def test_review_feedback_prioritises_blockers_over_long_closed_advisories(self):
        result = dict(findings=[
            dict(id='area', title='Large board', disposition='noted', disposition_reason='x' * 20000),
            dict(id='supply', title='Wrong supply pin', disposition='accepted',
                 condition='Supply connected to output', disposition_reason='y' * 20000)])
        feedback = loop.review_feedback(result)
        self.assertEqual([f['id'] for f in feedback], ['supply'])
        self.assertEqual(len(feedback[0]['disposition_reason']), 1200)


class TraceTests(ScriptedLoop):
    def problems(self, response, requirements=REQUIREMENTS):
        return loop.trace_problems(response, self.statement, requirements)

    def test_requirement_items_are_sentences_of_paragraphs_and_list_items(self):
        text = ('# Board title\n\nMake a 3.3 V supply at up to\n1 A. Keep it small, e.g. under 30 mm.\n'
                '- Provide a USB-C input\n- Label the output. Add a test point\n\nBench: check ripple!')
        self.assertEqual(loop.requirement_items(text), [
            ('Board title', False), ('Make a 3.3 V supply at up to 1 A.', True),
            ('Keep it small, e.g. under 30 mm.', True), ('Provide a USB-C input', True),
            ('Label the output.', True), ('Add a test point', True), ('Bench: check ripple!', True)])

    def test_a_quote_is_a_whole_word_span_inside_one_requirement_item(self):
        for quote, expected in (('a', 'at least 3 whole words'), ('compact divider', 'at least 3 whole words'),
                                ('uild a compact divide', 'word boundaries'),
                                ('divider. Measure it', 'inside one requirement item'),
                                ('not in the text', 'exact nonempty span'), ('  ', 'exact nonempty span')):
            response = self.response()
            response['trace'][0]['quote'] = quote
            problems = self.problems(response)
            self.assertTrue(problems[0].startswith('intent: trace.quote must '), problems)
            self.assertIn(expected, problems[0], quote)
        # Line breaks and spacing may differ; a shorter item may be quoted whole.
        response = self.response()
        response['trace'].append(dict(field='board', quote='Label it'))
        self.assertEqual(self.problems(response, 'Build a compact\n   divider. Label it. Measure it on hardware.'), [])

    def test_a_trace_field_is_a_structured_path_never_a_text_position(self):
        response = self.response()
        response['trace'] += [dict(field=field, quote=QUOTE) for field in
                              ('board', 'bench', 'bench[0]', 'parts.R1.value', 'board.width_mm')]
        self.assertEqual(self.problems(response), [])
        for field in ('intent[0]', 'parts.R1.value[1]', 'functional[9].text', 'board.width_mm.x', 'parts[0]', 'board.'):
            response = self.response()
            response['trace'].append(dict(field=field, quote=QUOTE))
            self.assertEqual(self.problems(response), [f'trace.{field}: cite an existing design field, never a '
                                                       'position inside a text value'], field)

    def test_every_requirement_item_is_quoted_or_explicitly_untraced(self):
        response = self.response()
        response['trace'].pop()
        self.assertEqual(self.problems(response), [
            'requirements: item 2 ("Measure it on hardware.") has no trace quote and no untraced entry'])
        response['untraced'] = [dict(quote='Measure it on hardware', status='not_applicable',
                                     reason='A bench activity after assembly.')]
        self.assertEqual(self.problems(response), [])
        response['untraced'][0]['quote'] = 'it on'
        problems = self.problems(response)
        self.assertIn('untraced[0]: untraced.quote must hold at least 3 whole words', problems[0])
        self.assertIn('requirements: item 2', problems[1])
        response = self.response()
        response['trace'].pop(0)
        self.assertIn('intent: supply an exact requirement quote in trace', self.problems(response))

    def test_a_shared_quote_covers_no_item_unless_it_names_one(self):
        text = 'Provide a labelled connector for input power. Provide a labelled connector for output power.'
        shared = self.response()
        shared['trace'] = [dict(field=field, quote='Provide a labelled connector')
                           for field in ('intent', 'functional[0].text', 'bench')]
        problems = self.problems(shared, text)
        self.assertIn('intent: trace.quote occurs in requirement items 1, 2; quote words that occur in one item '
                      'only, or name the item in item', problems)
        self.assertIn('requirements: item 1 ("Provide a labelled connector for input power.") has no trace quote '
                      'and no untraced entry', problems)
        self.assertIn('requirements: item 2 ("Provide a labelled connector for output power.") has no trace quote '
                      'and no untraced entry', problems)
        named = copy.deepcopy(shared)
        named['trace'][1]['item'], named['trace'][2]['item'] = 1, 2
        self.assertEqual(self.problems(named, text), ['intent: trace.quote occurs in requirement items 1, 2; quote '
            'words that occur in one item only, or name the item in item', 'intent: supply an exact requirement quote in trace'])
        named['trace'][0] = dict(field='intent', quote='connector for output power')
        self.assertEqual(self.problems(named, text), [])
        named['trace'][1]['item'] = 3
        self.assertIn('functional[0].text: trace.quote must lie inside requirement item 3, the item it names',
                      self.problems(named, text))
        # The scripted loop refuses the shared quote instead of accepting it.
        self.requirements.write_text(text)
        self.responses = [shared]
        report = self.generate(max_attempts=1)
        self.assertEqual((report['status'], report['attempts'][0]['status']), ('attempts_exhausted', 'refused'))
        self.assertIn('requirements', report['attempts'][0]['refusal_classes'])

    def test_table_rows_are_items_and_a_header_covers_none_of_them(self):
        text = '| Required electrical behavior | Value |\n|---|---|\n| Input | 12 V |\n| Output | 3.3 V |\n'
        self.assertEqual(loop.requirement_items(text), [('Required electrical behavior | Value', False),
                                                        ('Input | 12 V', True), ('Output | 3.3 V', True)])
        response = self.response()
        response['trace'] = [dict(field=field, quote='Required electrical behavior')
                             for field in ('intent', 'functional[0].text', 'bench')]
        self.assertEqual(self.problems(response, text), [
            'requirements: item 1 ("Input | 12 V") has no trace quote and no untraced entry',
            'requirements: item 2 ("Output | 3.3 V") has no trace quote and no untraced entry'])
        response['trace'] += [dict(field='board', quote='Input | 12 V'), dict(field='board', quote='Output | 3.3 V')]
        self.assertEqual(self.problems(response, text), [])

    def test_tables_without_outer_pipes_are_tables_too(self):
        # The review's case: the spec parser's delimiter rule (outer pipes optional) decides tables.
        rows = [('Required electrical behavior | Value', False), ('Input | 12 V', True), ('Output | 3.3 V', True)]
        for name, text in (('no outer pipes', 'Required electrical behavior | Value\n--- | ---\nInput | 12 V\nOutput | 3.3 V\n'),
                           ('mixed', '| Required electrical behavior | Value |\n--- | ---\nInput | 12 V\nOutput | 3.3 V\n'),
                           ('aligned', 'Required electrical behavior | Value\n:--- | ---:\nInput | 12 V\nOutput | 3.3 V\n')):
            with self.subTest(table=name):
                self.assertEqual(loop.requirement_items(text), rows)
                header_only = self.response()
                header_only['trace'] = [dict(field=field, quote='Required electrical behavior')
                                        for field in ('intent', 'functional[0].text', 'bench')]
                self.assertEqual(self.problems(header_only, text), [
                    'requirements: item 1 ("Input | 12 V") has no trace quote and no untraced entry',
                    'requirements: item 2 ("Output | 3.3 V") has no trace quote and no untraced entry'])
                self.requirements.write_text(text)
                self.responses = [header_only]
                report = loop.generate(self.requirements, self.root / name.replace(' ', '-'), max_attempts=1)
                self.assertEqual(report['status'], 'attempts_exhausted')
        # A line that only looks like a delimiter (no pipe) stays prose, as in the spec parser.
        self.assertEqual(loop.requirement_items('Use a 5 V input rail.\n---\n'), [('Use a 5 V input rail.', True)])
        # Rows that start with a pipe but have no delimiter row are still one item each, never one paragraph.
        self.assertEqual(loop.requirement_items('Rails:\n\n| Input | 12 V |\n| Output | 3.3 V |\n'),
                         [('Rails:', True), ('Input | 12 V', True), ('Output | 3.3 V', True)])

    def test_a_null_item_is_valid_for_the_validator_and_both_seat_schemas(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema is not installed')
        from agent_ee.harnesses import strict_schema
        envelope = self.response()
        envelope['trace'][0]['item'] = None
        envelope['trace'][1]['item'] = 1
        for row in envelope['trace'][2:]:
            row['item'] = None
        envelope['untraced'] = [dict(quote='Measure it on hardware', item=None, status='not_applicable', reason='Bench.')]
        schemas = {'codex': strict_schema(loop.AUTHOR_SCHEMA), 'agy': loop.AUTHOR_SCHEMA}
        self.assertEqual(loop.validate_response(copy.deepcopy(envelope)), envelope)
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                jsonschema.validate(envelope, schema)
                wrong = copy.deepcopy(envelope)
                wrong['trace'][0]['item'] = '1'
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(wrong, schema)

    def test_an_item_number_is_null_or_a_positive_integer(self):
        for item in (0, -1, 1.5, '1', True):
            response = self.response()
            response['trace'][0]['item'] = item
            with self.assertRaises(ValueError, msg=repr(item)):
                loop.validate_response(response)
        response = self.response()
        response['trace'][0]['item'] = None
        response['untraced'] = [dict(quote='Measure it on hardware', item=2, status='not_applicable', reason='Bench.')]
        self.assertIs(loop.validate_response(response), response)

    def test_untraced_entries_need_a_known_status_and_a_reason(self):
        for entry in (dict(quote='Measure it on hardware', status='maybe', reason='x'),
                      dict(quote='Measure it on hardware', status='unsupported', reason='  '),
                      dict(quote='Measure it on hardware', status='unsupported')):
            response = self.response()
            response['untraced'] = [entry]
            with self.assertRaises(ValueError, msg=str(entry)):
                loop.validate_response(response)

    def test_agent_selected_choices_must_be_labelled(self):
        for selected in ([], ['  '], ['Outline is agent-selected.', '']):
            response = self.response()
            response['agent_selected'] = selected
            self.assertEqual(self.problems(response), ['agent_selected: label the choices you made, separately from '
                                                       'requirement and datasheet facts'], selected)

    def test_dropped_requirement_is_refused_then_recorded_as_untraced(self):
        dropped = self.response()
        dropped['trace'].pop()
        kept = copy.deepcopy(dropped)
        kept['untraced'] = [dict(quote='Measure it on hardware', status='not_applicable',
                                 reason='A bench activity after assembly.')]
        self.responses = [dropped, kept]
        report = self.generate(max_attempts=2)
        self.assertTrue(report['accepted'])
        self.assertEqual(report['attempts'][0]['refusal_classes'], ['requirements'])
        self.assertIn('item 2 ("Measure it on hardware.")', self.feedback(1))
        self.assertEqual(report['attempts'][1]['untraced'], kept['untraced'])
        provenance = self.saved('run/attempt-002/provenance.json')
        self.assertEqual(provenance['untraced'], kept['untraced'])
        self.assertEqual(provenance['authority'], AUTHORITY)

    def test_author_is_told_the_item_rule_in_prompt_and_schema(self):
        self.responses = [self.response()]
        self.generate()
        self.assertIn('REQUIREMENT ITEMS (each needs a trace quote or an untraced entry):\n'
                      '1. Build a compact divider.\n2. Measure it on hardware.\n', self.prompts[0])
        schema = self.saved('run/context/author-schema.json')
        self.assertIn('untraced', schema['required'])
        self.assertIn('one sentence', schema['properties']['trace']['description'])
        self.assertIn('never a character position', schema['properties']['trace']['items']['properties']['field']['description'])
        self.assertIn('At least 3 whole words', schema['properties']['untraced']['items']['properties']['quote']['description'])


class BenchmarkTests(ScriptedLoop):
    def test_author_timeout_does_not_skip_unrelated_benchmark_designs(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [(dict(execution='timeout', error='time limit'), None), self.response()]
        report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([r['status'] for r in report['designs']], ['author_timeout', 'accepted'])
        self.assertEqual(len(self.prompts), 2)

    def test_benchmark_lists_unrun_designs_after_subscription_failure(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [(dict(execution='failed', error='authentication failed'), None)]
        report = loop.benchmark(directory, self.root / 'benchmark')
        self.assertEqual(report['status'], 'stopped_seat_failed')
        self.assertEqual([r['status'] for r in report['designs']], ['seat_failed', 'not_run'])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(report['designs'][1]['duration_s'], 0)

    def test_benchmark_runs_sorted_and_reports_failures(self):
        directory = self.make_dir([('b', REQUIREMENTS), ('a', REQUIREMENTS)])
        self.responses = [self.response(), self.unsupported()]
        report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([r['name'] for r in report['designs']], ['a', 'b'])
        self.assertEqual([r['attempts_to_accepted'] for r in report['designs']], [1, None])
        self.assertEqual(report['designs'][1]['reason'], 'unsupported design')

    def test_runtime_pin_refusal_stops_the_benchmark(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [self.response()]
        def build(attempt):
            self.build_report(attempt, accepted=False, stage='placement-routing',
                              reason='kicad-route checkout is not the pinned commit')
            return dict(execution='failed', exit_code=1)
        with self.with_build(build):
            report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=3)
        self.assertEqual(report['status'], 'stopped_runtime_refused')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']], [('a', 'runtime_refused'), ('b', 'not_run')])

    def test_a_stop_before_a_designs_report_exists_lists_it_interrupted(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS), ('c', REQUIREMENTS)])
        self.responses = [self.response()]
        original = loop.prepare_context
        def prepare_context(root, out):
            if out.parent.name == 'b':
                raise KeyboardInterrupt  # design b has started; its report does not exist yet
            return original(root, out)
        with patch.object(loop, 'prepare_context', side_effect=prepare_context):
            with self.assertRaises(KeyboardInterrupt):
                loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        report = self.saved('benchmark/benchmark-report.json')
        self.assertEqual([(r['name'], r['status'], r['report']) for r in report['designs']],
                         [('a', 'accepted', str((self.root / 'benchmark').resolve() / 'a/generate-report.json')),
                          ('b', 'interrupted', None), ('c', 'not_run', None)])
        self.assertEqual(report['designs'][1]['reason'], 'Coordinator interrupted during this design; partial artifacts retained')

    def test_a_stop_just_after_a_design_finished_keeps_its_final_state(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [self.response()]
        original, calls = loop.design_row, []
        def design_row(path, run, *args):
            calls.append(path.stem)
            if len(calls) == 1:
                raise KeyboardInterrupt  # after design a's report is final, before its row
            return original(path, run, *args)
        with patch.object(loop, 'design_row', side_effect=design_row):
            with self.assertRaises(KeyboardInterrupt):
                loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        report = self.saved('benchmark/benchmark-report.json')
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']], [('a', 'accepted'), ('b', 'not_run')])

    def test_a_review_seat_failure_stops_the_benchmark(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [self.response(), self.response()]
        reviews = []
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            reviews.append(attempt.parent.name)
            (attempt / 'review/review').mkdir(parents=True)
            # A failed reviewer leaves the engineering dimension unassessed (qualification.py).
            write_json(attempt / 'review/report.json', dict(outcome='execution_incomplete', qualification=dict(
                dimensions=dict(engineering_review='not_assessed'))))
            write_json(attempt / 'review/review/result.json', dict(findings=[], review_executions=[dict(
                role='circuit', execution='failed', error='subscription quota exhausted; no fallback attempted')]))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1, review_profile=self.profile())
        self.assertEqual(report['status'], 'stopped_seat_failed')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']], [('a', 'seat_failed'), ('b', 'not_run')])
        self.assertEqual((len(self.prompts), reviews), (1, ['a']))
        self.assertIn('Engineering review seat failed (circuit: failed)', report['designs'][0]['reason'])

    def test_an_adjudicator_seat_failure_stops_the_benchmark(self):
        # The review's case: both reviewers finished, the adjudicator ran out of quota.
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS)])
        self.responses = [self.response(), self.response()]
        reviews = []
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            reviews.append(attempt.parent.name)
            (attempt / 'review/review').mkdir(parents=True)
            write_json(attempt / 'review/report.json', dict(outcome='incomplete', qualification=dict(
                dimensions=dict(engineering_review='not_assessed'))))
            write_json(attempt / 'review/review/result.json', dict(findings=[], adjudicated=False, reviews=[{}, {}],
                review_executions=[dict(role='circuit', execution='ok'), dict(role='physical', execution='ok')]))
            write_json(attempt / 'review/review/adjudicator-execution.json', dict(execution='failed',
                error='subscription quota exhausted; no fallback attempted'))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1, review_profile=self.profile())
        self.assertEqual(report['status'], 'stopped_seat_failed')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']], [('a', 'seat_failed'), ('b', 'not_run')])
        self.assertEqual((len(self.prompts), reviews), (1, ['a']))
        self.assertIn('Engineering review seat failed (adjudicator: failed)', report['designs'][0]['reason'])

    def test_review_seat_failures_exclude_timeouts_unreached_seats_and_format_refusals(self):
        rows = [dict(role='ok', execution='ok'), dict(role='slow', execution='timeout'),
                dict(role='unreached', execution='not_run'), dict(role='stopped', execution='interrupted'),
                dict(role='format', execution='malformed', refusal_kind='structured_output'),
                dict(role='quota', execution='failed', error='quota'), dict(role='stream', execution='malformed'),
                dict(role='login', execution='unavailable')]
        self.assertEqual([r['role'] for r in loop.review_seat_failures(dict(review_executions=rows))],
                         ['quota', 'stream', 'login'])
        self.assertEqual(loop.review_seat_failures(None), [])
        # The adjudicator's own execution is judged by the same rule.
        for execution, failed in (('timeout', False), ('not_run', False), ('failed', True), ('unavailable', True)):
            self.assertEqual(loop.review_seat_failures(None, dict(execution=execution)) != [], failed, execution)
        self.assertEqual(loop.review_seat_failures(None, dict(execution='malformed', refusal_kind='structured_output')), [])

    def test_one_designs_error_is_a_row_and_the_rest_still_run(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', '   \n'), ('c', ''), ('d', REQUIREMENTS), ('e', REQUIREMENTS)])
        (directory / 'c.md').write_bytes(b'\xff\xfe not UTF-8')
        self.responses = [self.response(), self.response(), self.response()]
        original = self.step
        def step(attempt, label, argv, seconds):
            if attempt.parent.name == 'd' and label == 'build':
                raise KeyError('defect after the report exists')
            return original(attempt, label, argv, seconds)
        with patch.object(loop, 'run_step', side_effect=step):
            report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        self.assertEqual(report['status'], 'complete')
        rows = {r['name']: r for r in report['designs']}
        self.assertEqual([(r['name'], r['status']) for r in report['designs']],
                         [('a', 'accepted'), ('b', 'error'), ('c', 'error'), ('d', 'error'), ('e', 'accepted')])
        self.assertEqual(rows['b']['reason'], 'ValueError: requirements: supply nonempty text')
        self.assertIn('UTF-8', rows['c']['reason'])
        self.assertEqual(rows['d']['reason'], "KeyError: 'defect after the report exists'")
        self.assertEqual((rows['b']['attempts'], rows['b']['report'], rows['d']['attempts']), (0, None, 1))
        self.assertEqual(self.saved('benchmark/d/generate-report.json')['status'], 'error')

    def test_invalid_options_refuse_the_whole_benchmark_before_any_output(self):
        directory = self.make_dir([('a', REQUIREMENTS)])
        with self.assertRaisesRegex(ValueError, 'max_attempts'):
            loop.benchmark(directory, self.root / 'benchmark', max_attempts=0)
        self.assertFalse((self.root / 'benchmark').exists())

    def test_rows_share_keys_and_the_summary_records_run_identity(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS), ('c', REQUIREMENTS)])
        self.responses = [self.response(), (dict(execution='failed', error='quota exhausted'), None)]
        report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=2, author_seconds=300)
        self.assertEqual([r['status'] for r in report['designs']], ['accepted', 'seat_failed', 'not_run'])
        keys = {'name', 'requirements_sha256', 'status', 'reason', 'attempts', 'attempts_to_accepted', 'duration_s',
                'report', 'refusal_classes', 'failed_checks', 'placement_findings', 'failing_stages',
                'untraced_requirements', 'outline_utilisation'}
        for row in report['designs']:
            self.assertEqual(set(row), keys, row['name'])
        self.assertEqual([r['reason'] for r in report['designs']], [None, 'quota exhausted', 'stopped_seat_failed'])
        self.assertEqual([r['untraced_requirements'] for r in report['designs']], [[], None, None])
        run = report['run']
        self.assertEqual(datetime.datetime.fromisoformat(run['started_at']).utcoffset(), datetime.timedelta(0))
        self.assertEqual(run['author'], 'codex:gpt-6.1-sol:high')
        self.assertEqual(run['bounds'], dict(max_attempts=2, max_seconds=2400, author_seconds=300, search_retries=1))
        self.assertRegex(run['agent_ee']['tree_sha256'], '^[0-9a-f]{64}$')
        pin = json.loads((ROOT / 'src/agent_ee/data/kicad-route-pin.json').read_text())
        self.assertEqual(run['router_pin']['pin'], pin['pin'])
        self.assertEqual(self.saved('benchmark/a/generate-report.json')['run'], run)

    def test_interrupt_lists_the_design_in_flight_and_every_design_not_started(self):
        directory = self.make_dir([('a', REQUIREMENTS), ('b', REQUIREMENTS), ('c', REQUIREMENTS)])
        self.responses = [self.response()]
        original = self.seat
        def seat(run, *args, **options):
            if run.parent.name == 'b':
                raise KeyboardInterrupt
            return original(run, *args, **options)
        with patch.object(loop.harnesses, 'native_call', side_effect=seat):
            with self.assertRaises(KeyboardInterrupt):
                loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        report = self.saved('benchmark/benchmark-report.json')
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']],
                         [('a', 'accepted'), ('b', 'interrupted'), ('c', 'not_run')])
        self.assertEqual(report['designs'][2]['reason'], 'interrupted')
        generated = self.saved('benchmark/b/generate-report.json')
        self.assertEqual(generated['status'], 'interrupted')
        self.assertEqual([r['status'] for r in generated['attempts']], ['interrupted'])
        self.assertEqual(self.saved('benchmark/b/attempt-001/attempt.json')['status'], 'interrupted')


class StopTests(ScriptedLoop):
    def test_sigterm_takes_the_interrupt_path_and_finalizes_the_report(self):
        def untranslated(signum, frame):
            raise AssertionError('SIGTERM reached the caller untranslated')
        previous = signal.signal(signal.SIGTERM, untranslated)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        def seat(*args, **options):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(10)
            raise AssertionError('SIGTERM did not stop the author turn')
        with patch.object(loop.harnesses, 'native_call', side_effect=seat):
            with self.assertRaises(KeyboardInterrupt):
                self.generate()
        self.assertIs(signal.getsignal(signal.SIGTERM), untranslated)
        report = self.saved()
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual([r['status'] for r in report['attempts']], ['interrupted'])
        self.assertEqual(self.saved('run/attempt-001/attempt.json')['status'], 'interrupted')

    def outsider(self):
        """An unrelated process, in a session of its own, that no stop may signal."""
        outsider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(outsider.wait)
        self.addCleanup(outsider.kill)
        return outsider

    def test_a_stop_kills_the_phase_group_and_nothing_else(self):
        marker, attempt, outsider = self.root / 'pids', self.root / 'attempt', self.outsider()
        attempt.mkdir()
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        def interrupt():
            if wait_for(lambda: recorded_pids(marker), 30):
                signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        helper = threading.Thread(target=interrupt)
        helper.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                REAL_RUN_STEP(attempt, 'build', [sys.executable, '-c', PHASE, str(marker), '300', '0'], 30)
        finally:
            helper.join()
            self.addCleanup(kill_all, recorded_pids(marker) or [])
        pids = recorded_pids(marker)
        self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids), 10), pids)
        self.assertEqual(json.loads((attempt / 'build-execution/execution.json').read_text())['execution'], 'interrupted')
        self.assertIsNone(outsider.poll())

    def test_a_finished_phase_keeps_its_exit_status_and_its_group_is_killed(self):
        marker, attempt, outsider = self.root / 'pids', self.root / 'attempt', self.outsider()
        attempt.mkdir()
        record = REAL_RUN_STEP(attempt, 'build', [sys.executable, '-c', PHASE, str(marker), '0', '3'], 60)
        pids = recorded_pids(marker)
        self.addCleanup(kill_all, pids or [])
        self.assertEqual((record['execution'], record['exit_code']), ('failed', 3))
        self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids), 10), pids)
        self.assertIsNone(outsider.poll())

    def test_sigterm_during_a_launch_leaves_no_phase_group_process(self):
        # SIGTERM arrives after the phase has started but before Popen returns its handle,
        # whatever SIGINT's handler is.
        def untranslated(signum, frame):
            raise AssertionError('SIGTERM reached the caller untranslated')
        previous_term = signal.signal(signal.SIGTERM, untranslated)
        self.addCleanup(signal.signal, signal.SIGTERM, previous_term)
        real_popen = subprocess.Popen
        for name, handler in (('custom', lambda signum, frame: None), ('ignored', signal.SIG_IGN),
                              ('default', signal.default_int_handler)):
            with self.subTest(sigint=name):
                previous_int = signal.signal(signal.SIGINT, handler)
                self.addCleanup(signal.signal, signal.SIGINT, previous_int)
                marker, outsider = self.root / f'pids-{name}', self.outsider()
                def launch(argv, **options):
                    process = real_popen(argv, **options)
                    if PHASE in argv:
                        self.assertTrue(wait_for(lambda: recorded_pids(marker), 30))
                        os.kill(os.getpid(), signal.SIGTERM)
                    return process
                original = self.step
                def step(attempt, label, argv, seconds):
                    if label != 'capture':
                        return original(attempt, label, argv, seconds)
                    return REAL_RUN_STEP(attempt, label, [sys.executable, '-c', PHASE, str(marker), '300', '0'], 30)
                self.responses = [self.response()]
                with patch.object(loop, 'run_step', side_effect=step), \
                     patch.object(loop.native_run.subprocess, 'Popen', side_effect=launch):
                    with self.assertRaises(KeyboardInterrupt):
                        loop.generate(self.requirements, self.root / f'run-{name}', max_attempts=1)
                pids = recorded_pids(marker)
                self.addCleanup(kill_all, pids or [])
                self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids), 10), pids)
                self.assertEqual(self.saved(f'run-{name}/generate-report.json')['status'], 'interrupted')
                self.assertIsNone(outsider.poll())

    def test_sigterm_to_a_benchmark_finalizes_every_report_and_stops_the_phase_tree(self):
        marker, work, driver = self.root / 'pids', self.root / 'work', self.root / 'driver.py'
        driver.write_text(DRIVER)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT)]))
        process = subprocess.Popen([sys.executable, '-B', str(driver), str(work), str(marker)], cwd=ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if not wait_for(lambda: recorded_pids(marker) or process.poll() is not None, 60) or process.poll() is not None:
                process.kill()
                self.fail('driver never reached the phase: ' + process.communicate()[1].decode()[-3000:])
            process.send_signal(signal.SIGTERM)
            _, err = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            self.addCleanup(kill_all, recorded_pids(marker) or [])
        self.assertEqual(process.returncode, 130, err.decode()[-3000:])
        self.assertIn('interrupted; its reports under', err.decode())
        report = json.loads((work / 'benchmark/benchmark-report.json').read_text())
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual([(r['name'], r['status']) for r in report['designs']],
                         [('a', 'accepted'), ('b', 'interrupted'), ('c', 'not_run')])
        generated = json.loads((work / 'benchmark/b/generate-report.json').read_text())
        self.assertEqual((generated['status'], generated['attempts'][0]['status']), ('interrupted', 'interrupted'))
        execution = json.loads((work / 'benchmark/b/attempt-001/build-execution/execution.json').read_text())
        self.assertEqual(execution['execution'], 'interrupted')
        pids = recorded_pids(marker)
        self.assertTrue(wait_for(lambda: not any(alive(pid) for pid in pids), 10), pids)


class SignalTests(ScriptedLoop):
    """SIGTERM interrupts whatever SIGINT's handler is; a guard fails the test if it is not translated."""

    def setUp(self):
        super().setUp()
        def untranslated(signum, frame):
            raise AssertionError('SIGTERM reached the caller untranslated')
        self.guard = untranslated
        previous = signal.signal(signal.SIGTERM, untranslated)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)

    def sigint(self, handler):
        previous = signal.signal(signal.SIGINT, handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)

    def test_sigterm_interrupts_even_with_a_custom_sigint_handler(self):
        seen = []
        def custom(signum, frame):
            seen.append(signum)
        self.sigint(custom)
        def seat(*args, **options):
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(10)
            raise AssertionError('SIGTERM did not stop the author turn')
        with patch.object(loop.harnesses, 'native_call', side_effect=seat):
            with self.assertRaises(KeyboardInterrupt):
                self.generate(max_attempts=1)
        self.assertEqual(seen, [])
        self.assertEqual(self.saved()['status'], 'interrupted')
        self.assertIs(signal.getsignal(signal.SIGINT), custom)
        self.assertIs(signal.getsignal(signal.SIGTERM), self.guard)

    def test_sigterm_interrupts_while_an_ignored_sigint_stays_ignored(self):
        self.sigint(signal.SIG_IGN)
        with loop.stop_signals():
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
            with self.assertRaises(KeyboardInterrupt):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(10)
        self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        self.assertIs(signal.getsignal(signal.SIGTERM), self.guard)

    def test_every_sigterm_interrupts_while_the_scope_lasts(self):
        with loop.stop_signals():
            for _ in range(3):
                with self.assertRaises(KeyboardInterrupt):
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(10)

    def test_a_stop_during_a_final_write_arrives_right_after_it(self):
        written = []
        with loop.stop_signals():
            with self.assertRaises(KeyboardInterrupt):
                with loop.held_stops():
                    signal.pthread_kill(threading.main_thread().ident, signal.SIGTERM)
                    time.sleep(0.05)
                    written.append('report')
                time.sleep(10)
        self.assertEqual(written, ['report'])


class DocsTests(unittest.TestCase):
    def test_doc_names_every_option_with_its_default_and_every_final_state(self):
        text = (ROOT / 'docs/design-generate.md').read_text()
        parser = argparse.ArgumentParser()
        loop.configure_parser(parser.add_subparsers(dest='design_command'))
        options = {option for command in ('generate', 'benchmark')
                   for action in parser._subparsers._group_actions[0].choices[command]._actions
                   for option in action.option_strings if option.startswith('--') and option != '--help'}
        for option in options:
            self.assertIn(f'`{option}', text, option)
        for option, default in (('--author', 'codex:gpt-6.1-sol:high'), ('--max-attempts', '4'),
                                ('--max-seconds', '2400'), ('--author-seconds', '600'), ('--search-retries', '1'),
                                ('--kicad-root', '/usr/share/kicad')):
            self.assertRegex(text, rf'\| `{option}[^`]*` \| `{default}` \|', option)
        for state in ('accepted', 'unsupported', 'seat_failed', 'author_timeout', 'review_incomplete',
                      'runtime_refused', 'time_exhausted', 'attempts_exhausted', 'interrupted', 'error',
                      'complete', 'stopped_seat_failed', 'stopped_runtime_refused', 'not_run'):
            self.assertIn(f'`{state}`', text, state)
        for code in ('0', '1', '2', '130'):
            self.assertRegex(text, rf'\n\| `{code}` \| ', code)
        # Internal errors of these types leave through the CLI's handler with exit 2.
        self.assertIn('internal `ValueError`, `OSError`, `KeyError` or `TypeError`', text)


if __name__ == '__main__':
    unittest.main()
