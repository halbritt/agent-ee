"""Scripted subscription boundary; no network, model or native KiCad process."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import design_generate as loop
from agent_ee.storage import write_json
from tests.test_design_compile import design_fixture


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.requirements = self.root / 'requirements.md'
        self.requirements.write_text('Build a compact divider. Measure it on hardware.')
        self.statement = design_fixture(self.root / 'fixture')
        self.responses = []
        self.prompts = []
        self.steps = []
        self.enterContext(patch.object(loop.harnesses, 'native_call', side_effect=self.seat))
        self.enterContext(patch.object(loop, 'run_step', side_effect=self.step))

    def response(self, statement=None):
        return dict(status='ready', reason='', statement_json=json.dumps(statement or self.statement),
            trace=[dict(field=f, quote='compact divider') for f in ('intent', 'functional[0].text')],
            agent_selected=['Agent-selected values and outline; no owner or datasheet ruling.'])

    def seat(self, run, label, definition, prompt, schema, timeout):
        self.prompts.append(prompt)
        value = self.responses.pop(0)
        if isinstance(value, tuple):
            return value
        return dict(execution='ok', billing_route='subscription'), value

    def step(self, attempt, label, argv, seconds):
        self.steps.append((attempt.name, label, seconds))
        (attempt / (label + '-execution')).mkdir()
        if label == 'capture':
            (attempt / 'capture').mkdir()
        elif label == 'compile':
            (attempt / 'bundle').mkdir()
        elif label == 'build':
            self.build_report(attempt)
        return dict(execution='ok', exit_code=0)

    def build_report(self, attempt, accepted=True, mode='typed', stage='package'):
        run = attempt / 'build'
        run.mkdir()
        write_json(run / 'build-report.json', dict(accepted=accepted, status='accepted' if accepted else 'failed',
            attempts=[dict(stage=stage)], candidate=None, reason='routing search exhausted' if not accepted else ''))
        write_json(run / 'qualification.json', dict(mode=mode,
            dimensions=dict(file_release=dict(status='pass' if accepted else 'failed'))))

    def generate(self, **kwargs):
        return loop.generate(self.requirements, self.root / 'run', **kwargs)

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
        self.assertEqual(report['claims'], dict(file_release=True, engineering_review='not_run',
                                               supplier_preview=False, physical_test=False))
        for n in (1, 2):
            root = self.root / f'run/attempt-{n:03}'
            for name in ('prompt.txt', 'response.json', 'statement.json', 'attempt.json'):
                self.assertTrue((root / name).is_file(), name)
            self.assertTrue((root / 'review-attempts/author/0/execution.json').is_file())
        self.assertEqual(json.loads((self.root / 'run/generate-report.json').read_text()), report)

    def test_additional_requirement_traces_accept_existing_fields_only(self):
        response = self.response()
        response['trace'] += [dict(field=f, quote='compact divider') for f in ('board', 'bench', 'parts.R1.value')]
        self.assertEqual(loop.trace_problems(response, self.statement, self.requirements.read_text()), [])
        response['trace'].append(dict(field='functional[9].text', quote='compact divider'))
        self.assertIn('existing design field', loop.trace_problems(response, self.statement,
            self.requirements.read_text())[0])
        response = self.response()
        response['trace'].pop(0)
        self.assertIn('intent:', loop.trace_problems(response, self.statement, self.requirements.read_text())[0])

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
        def seat(*args):
            clock[0] = 110.0
            return dict(execution='ok'), self.response({'invalid': 'statement'})
        with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(loop.harnesses, 'native_call', side_effect=seat):
            report = self.generate(max_seconds=2)
        self.assertEqual(report['status'], 'time_exhausted')
        self.assertEqual(len(report['attempts']), 1)
        self.assertFalse(self.steps)

    def test_subscription_failure_stops_without_retry(self):
        self.responses = [(dict(execution='failed', error='quota exhausted; no fallback'), None)]
        report = self.generate(max_attempts=4)
        self.assertEqual(report['status'], 'seat_failed')
        self.assertEqual(len(self.prompts), 1)
        self.assertFalse(self.steps)
        self.assertTrue((self.root / 'run/attempt-001/review-attempts/author/0/response.json').is_file())

    def test_no_capture_launch_after_author_spends_deadline(self):
        clock = [100.0]
        def seat(*args):
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
        report = json.loads((self.root / 'run/generate-report.json').read_text())
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['attempts'][0]['status'], 'error')

    def test_incomplete_review_stops_further_seats(self):
        self.responses = [self.response()]
        profile = self.root / 'profile.json'
        write_json(profile, dict(schema='agent-ee.profile/1'))
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
            report = self.generate(max_attempts=3, review_profile=profile)
        self.assertEqual(report['status'], 'review_incomplete')
        self.assertEqual(len(self.prompts), 1)

    def test_unsupported_is_explicit_and_retained(self):
        self.responses = [dict(status='unsupported', reason='board.copper_layers: four layers unsupported',
            statement_json='', trace=[], agent_selected=[])]
        report = self.generate()
        self.assertEqual(report['status'], 'unsupported')
        self.assertFalse(self.steps)

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

    def test_file_acceptance_requires_typed_qualification(self):
        self.responses = [self.response()]
        with patch.object(self, 'build_report', side_effect=lambda attempt: GenerateTests.build_report(self, attempt, mode='legacy')):
            report = self.generate(max_attempts=1)
        self.assertFalse(report['accepted'])
        self.assertEqual(report['attempts'][0]['failing_stage'], 'package')

    def test_search_retry_adds_seeds_without_another_model_call(self):
        self.responses = [self.response()]
        calls = [0]
        def build(attempt):
            calls[0] += 1
            self.build_report(attempt, accepted=calls[0] > 1, stage='placement-routing')
            if calls[0] == 1:
                path = attempt / 'build/stages/05-placement/placement'
                path.mkdir(parents=True)
                write_json(path / 'job-report.json', dict(status='route_failed', reason='one open', final=dict(opens=1)))
        with patch.object(self, 'build_report', wraps=self.build_report) as report_mock:
            # Preserve the normal report writer while scripting just the build boundary.
            original = self.step
            def step(attempt, label, argv, seconds):
                if label != 'build':
                    return original(attempt, label, argv, seconds)
                (attempt / 'build-execution').mkdir()
                build(attempt)
                return dict(execution='ok' if calls[0] > 1 else 'failed', exit_code=0 if calls[0] > 1 else 1)
            with patch.object(loop, 'run_step', side_effect=step):
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

    def test_review_blocker_goes_back_to_author_and_is_not_acceptance(self):
        self.responses = [self.response(), self.response()]
        profile = self.root / 'profile.json'
        write_json(profile, dict(schema='agent-ee.profile/1'))
        original = self.step
        def step(attempt, label, argv, seconds):
            if label != 'review':
                return original(attempt, label, argv, seconds)
            folder = attempt / 'review'
            folder.mkdir()
            write_json(folder / 'report.json', dict(outcome='defects_or_unresolved',
                qualification=dict(dimensions=dict(engineering_review='failed'))))
            (folder / 'review').mkdir()
            write_json(folder / 'review/result.json', dict(decisions=[dict(id='supply', reason='Wrong supply pin')]))
            return dict(execution='failed', exit_code=1)
        with patch.object(loop, 'run_step', side_effect=step):
            report = self.generate(max_attempts=2, review_profile=profile)
        self.assertFalse(report['accepted'])
        self.assertIn('Wrong supply pin', self.prompts[1])
        self.assertEqual(report['attempts'][0]['engineering_review'], 'failed')

    def test_utilisation_uses_final_positions_rotation_and_union(self):
        run = self.root / 'native'
        candidate = run / 'stages/package/candidate'
        candidate.mkdir(parents=True)
        write_json(run / 'build-report.json', dict(candidate='stages/package/candidate'))
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

    def test_runtime_refusal_is_not_a_search_retry(self):
        run = self.root / 'native'
        path = run / 'stages/placement/placement'
        path.mkdir(parents=True)
        write_json(path / 'job-report.json', dict(status='refused', reason='pin mismatch'))
        self.assertFalse(loop.search_failure(dict(attempts=[dict(stage='placement-routing')]), run))

    def test_benchmark_lists_unrun_designs_after_subscription_failure(self):
        directory = self.root / 'requirements'
        directory.mkdir()
        for name in ('a', 'b'):
            (directory / (name + '.md')).write_text(self.requirements.read_text())
        self.responses = [(dict(execution='failed', error='authentication failed'), None)]
        report = loop.benchmark(directory, self.root / 'benchmark')
        self.assertEqual(report['status'], 'stopped_seat_failed')
        self.assertEqual([r['status'] for r in report['designs']], ['seat_failed', 'not_run'])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(report['designs'][1]['duration_s'], 0)

    def test_benchmark_runs_sorted_and_reports_failures(self):
        directory = self.root / 'requirements'
        directory.mkdir()
        for name in ('b', 'a'):
            (directory / (name + '.md')).write_text(self.requirements.read_text())
        self.responses = [self.response(), dict(status='unsupported', reason='unsupported design',
            statement_json='', trace=[], agent_selected=[])]
        report = loop.benchmark(directory, self.root / 'benchmark', max_attempts=1)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([r['name'] for r in report['designs']], ['a', 'b'])
        self.assertEqual([r['attempts_to_accepted'] for r in report['designs']], [1, None])


if __name__ == '__main__':
    unittest.main()
