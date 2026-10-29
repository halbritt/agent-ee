"""Frozen generation contracts, native grading, and sandbox isolation."""
import re
import contextlib
import io
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agent_ee.generation_benchmark import (aggregate, average_scores, evaluate, pinned_task, publish_report,
                                           main, run_task, score, split_tasks, tree_files)
from agent_ee.storage import digest, read_json, write_json
from agent_ee import generation_benchmark as benchmark
from agent_ee.baseline import freeze_evidence
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/generation-benchmark/original-series'


def repin(root):
    write_json(root / 'pins.json', {'schema': 'agent-ee.generation-pins/1',
                                  'files': {k: v for k, v in tree_files(root).items() if k != 'pins.json'}})


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / 'task'
        shutil.copytree(FIXTURE, self.task)

    def edit(self, edit):
        doc = read_json(self.task / 'task.json')
        edit(doc)
        write_json(self.task / 'task.json', doc)
        repin(self.task)

    def test_frozen_original_task_validates(self):
        task = pinned_task(self.task)
        self.assertEqual(task['id'], 'original-series')
        self.assertEqual(len(task['fingerprint']), 64)

    def test_committed_original_series_maps_every_acceptance_criterion(self):
        # AGENTEE-69 criterion 2 as amended: the committed original series substitutes the private prompts.
        tasks = sorted(p.parent for p in FIXTURE.parent.glob('*/task.json'))
        self.assertIn(FIXTURE, tasks)
        for root in tasks:
            with self.subTest(task=root.name):
                pinned_task(root)
                doc = read_json(root / 'task.json')
                self.assertIn(doc['scope']['schema'], ('agent-ee.scope/2', 'agent-ee.scope/3'))
                checks = {c['id'] for c in doc['scope']['checks']}
                acceptance = [r for r in doc['scope']['baseline']['requirements'] if r['intent'] == 'acceptance']
                self.assertTrue(acceptance)
                for req in acceptance:
                    self.assertTrue(req['checks'] and set(req['checks']) <= checks, req['id'])
                    self.assertTrue(any(req['id'] in rule['source_criteria'] for rule in doc['rules']), req['id'])

    def test_external_pins_digest_refuses_coordinated_task_repin(self):
        anchor = digest(self.task / 'pins.json')
        self.assertEqual(pinned_task(self.task, expect_pins=anchor)['id'], 'original-series')
        (self.task / 'prompt.md').write_text('Changed task')
        repin(self.task)
        with self.assertRaisesRegex(ValueError, 'external pins digest'):
            pinned_task(self.task, expect_pins=anchor)
        with self.assertRaisesRegex(ValueError, 'external pins digest'):
            run_task(self.task, self.root / 'absent.py', self.root / 'trial', self.root / 'store',
                     expect_pins=anchor)
        with self.assertRaisesRegex(ValueError, 'external pins digest'):
            main(['run', '--task', str(self.task), '--generator', str(self.root / 'absent.py'),
                  '--scratch', str(self.root / 'trial'), '--store', str(self.root / 'store'),
                  '--expect-pins', anchor])
        self.assertFalse((self.root / 'trial').exists())

    def test_only_prompt_md_can_be_public(self):
        shutil.copyfile(self.task / 'evaluator/rules.json', self.task / 'notes.md')
        self.edit(lambda d: d.update(prompt='notes.md'))
        with self.assertRaisesRegex(ValueError, 'prompt.md'):
            pinned_task(self.task)

    def test_prompt_copy_of_private_inputs_is_refused_before_execution(self):
        nested = self.task / 'evaluator/nested/private.txt'
        nested.parent.mkdir()
        nested.write_bytes(b'Private evaluator input\r\n')
        private = sorted(p for p in (self.task / 'evaluator').rglob('*') if p.is_file())
        private.append(self.task / 'reference.kicad_sch')
        for index, source in enumerate(private):
            (self.task / 'prompt.md').write_bytes(source.read_bytes())
            repin(self.task)
            scratch = self.root / f'private-copy-{index}'
            with self.subTest(source=source.relative_to(self.task)):
                with self.assertRaisesRegex(ValueError, 'prompt duplicates private input'):
                    pinned_task(self.task)
                with self.assertRaisesRegex(ValueError, 'prompt duplicates private input'):
                    run_task(self.task, self.root / 'absent.py', scratch, self.root / 'store')
                with self.assertRaisesRegex(ValueError, 'prompt duplicates private input'):
                    evaluate(self.task, self.root / 'absent-output', scratch, self.root / 'store')
                self.assertFalse(scratch.exists())
                self.assertFalse((self.root / 'store').exists())

    def test_prompt_content_comparison_is_byte_exact(self):
        private = (self.task / 'evaluator/rules.json').read_bytes()
        (self.task / 'prompt.md').write_bytes(private + b'\n')
        repin(self.task)
        self.assertEqual(pinned_task(self.task)['prompt'], 'prompt.md')

    def test_board_only_task_fingerprint_and_split(self):
        # Original in-repository board fixture; no third-party design import.
        board = FIXTURE.parents[1] / 'stages/placed.kicad_pcb'
        shutil.copyfile(board, self.task / 'reference.kicad_pcb')
        def board_task(doc):
            doc['reference'] = 'reference.kicad_pcb'
            doc['scope'].pop('schematic')
            doc['scope']['board'] = 'circuit.kicad_pcb'
            doc['scope'].pop('stages')
            doc['scope']['checks'] = [{'id': 'width', 'kind': 'geometry',
                                       'params': {'min_track_width_mm': 0.2}}]
            doc['scope']['baseline']['requirements'] = [
                {**doc['scope']['baseline']['requirements'][0], 'checks': ['width']}]
            doc['deliverables'] = ['circuit.kicad_pcb']
            doc['rules'] = [{**doc['rules'][0], 'check': 'width', 'deliverables': doc['deliverables']}]
        self.edit(board_task)
        task = pinned_task(self.task)
        self.assertEqual(len(task['fingerprint']), 64)
        result = split_tasks([self.task], {'schema': 'agent-ee.generation-exclusions/1',
                                          'version': '1', 'tasks': {}})
        self.assertEqual(len(result['train']) + len(result['test']), 1)
        alias = self.root / 'board-alias'
        shutil.copytree(self.task, alias)
        doc = read_json(alias / 'task.json')
        doc['id'] = 'board-alias'
        write_json(alias / 'task.json', doc)
        repin(alias)
        result = split_tasks([self.task, alias], {'schema': 'agent-ee.generation-exclusions/1',
                                                 'version': '1', 'tasks': {}})
        self.assertEqual(sorted([len(result['train']), len(result['test'])]), [0, 2])
        (self.task / 'reference.kicad_pcb').write_text('broken')
        repin(self.task)
        with self.assertRaises(ValueError):
            pinned_task(self.task)

    def test_every_task_file_is_pinned_and_extra_files_are_refused(self):
        for rel in read_json(self.task / 'pins.json')['files']:
            path = self.task / rel
            before = path.read_bytes()
            path.write_bytes(before + b' ')
            with self.subTest(path=rel), self.assertRaisesRegex(ValueError, 'task-file hash mismatch'):
                pinned_task(self.task)
            path.write_bytes(before)
        (self.task / 'extra').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'task-file hash mismatch'):
            pinned_task(self.task)

    def test_scope3_refuses_unmapped_acceptance_and_scoring_gaps(self):
        for mutate in (lambda d: d['scope']['baseline']['requirements'][0].update(checks=[]),
                       lambda d: d['rules'].pop(0),
                       lambda d: d['rules'][0].update(source_criteria=['NOT-DECLARED']),
                       lambda d: d['rules'][0].update(check='not-declared'),
                       lambda d: d['rules'][0].update(weight=True),
                       lambda d: d['scope']['inputs'].append({'path': '/etc', 'at': 'stolen'}),
                       lambda d: d['deliverables'].append('rules.json')):
            before = (self.task / 'task.json').read_bytes()
            self.edit(mutate)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                pinned_task(self.task)
            (self.task / 'task.json').write_bytes(before)
            repin(self.task)

    def test_private_prompt_spellings_are_refused_before_generator_execution(self):
        generator = self.root / 'leak.py'
        generator.write_text('import shutil, sys\nfrom pathlib import Path\n'
                             'shutil.copyfile(sys.argv[1], Path(sys.argv[2], "leak.txt"))\n')
        for index, prompt in enumerate(('evaluator/rules.json', './evaluator/rules.json',
                                        'task.json', 'pins.json', 'reference.kicad_sch',
                                        './reference.kicad_sch')):
            self.edit(lambda d: d.update(prompt=prompt))
            scratch = self.root / f'leak-trial-{index}'
            with self.subTest(prompt=prompt):
                with self.assertRaises(ValueError):
                    pinned_task(self.task)
                with self.assertRaises(ValueError):
                    run_task(self.task, generator, scratch, self.root / 'store', attempts=1)
                self.assertFalse(scratch.exists(), 'generator must never receive private prompt bytes')

    def test_reference_path_must_be_canonical(self):
        self.edit(lambda d: d.update(reference='./reference.kicad_sch'))
        with self.assertRaisesRegex(ValueError, 'non-canonical task reference'):
            pinned_task(self.task)

    def test_links_and_special_output_entries_are_refused(self):
        (self.task / 'link').symlink_to(self.task / 'prompt.md')
        with self.assertRaisesRegex(ValueError, 'non-ordinary'):
            pinned_task(self.task)
        (self.task / 'link').unlink()
        (self.task / 'prompt.md').unlink()
        (self.task / 'prompt.md').symlink_to('/etc/hostname')
        with self.assertRaisesRegex(ValueError, 'non-ordinary'):
            pinned_task(self.task)

    def test_structural_fingerprint_groups_aliases_and_exclusions(self):
        alias = self.root / 'alias'
        shutil.copytree(self.task, alias)
        doc = read_json(alias / 'task.json')
        doc['id'] = 'alias'
        write_json(alias / 'task.json', doc)
        # A UUID-only and position change must not send the alias to another split.
        ref = alias / 'reference.kicad_sch'
        source = ref.read_text()
        original_uuid = re.search(r'\(uuid "([^"]+)"', source).group(1)
        changed = source.replace('(at 30.48 40.64 0)', '(at 35.56 40.64 0)').replace(
            original_uuid, '11111111-1111-1111-1111-111111111111')
        self.assertNotEqual(source, changed)
        ref.write_text(changed)
        repin(alias)
        self.assertEqual(pinned_task(alias)['fingerprint'], pinned_task(self.task)['fingerprint'])
        exclusions = {'schema': 'agent-ee.generation-exclusions/1', 'version': '1', 'tasks': {}}
        for seed in ('0', 'another', '17'):
            result = split_tasks([self.task, alias], exclusions, seed=seed)
            self.assertEqual(sorted([len(result['train']), len(result['test'])]), [0, 2])
            self.assertEqual(result, split_tasks([alias, self.task], exclusions, seed=seed))
        exclusions['tasks'] = {'alias': 'Degenerate original synthetic example'}
        result = split_tasks([self.task, alias], exclusions)
        self.assertEqual(len(result['excluded']), 2)
        self.assertEqual({r['reason'] for r in result['excluded']}, {'Degenerate original synthetic example'})
        exclusions['tasks']['original-series'] = 'Another exclusion reason'
        self.assertEqual(split_tasks([self.task, alias], exclusions),
                         split_tasks([alias, self.task], exclusions))
        exclusions['tasks'] = {'absent': 'reason'}
        with self.assertRaisesRegex(ValueError, 'unknown task'):
            split_tasks([self.task], exclusions)


class ScoringTests(unittest.TestCase):
    def test_unknown_score_null_bounds_exact_and_known_critical_fail_wins(self):
        rules = pinned_task(FIXTURE)['rules']
        checks = [{'id': 'components', 'execution': 'ok', 'outcome': 'defect'},
                  {'id': 'erc', 'execution': 'failed', 'outcome': 'pass'}]
        result = score(rules, checks)
        self.assertIsNone(result['score'])
        self.assertFalse(result['overall_pass'])
        self.assertEqual(result['lower'], {'numerator': 0, 'denominator': 1})
        self.assertEqual(result['upper'], {'numerator': 1, 'denominator': 2})
        self.assertIsNone(result['categories']['native']['score'])
        checks[0]['outcome'] = 'pass'
        result = score(rules, checks)
        self.assertIsNone(result['overall_pass'])
        self.assertEqual(result['lower'], {'numerator': 1, 'denominator': 2})
        checks[1].update(execution='ok', outcome='info')
        self.assertIsNone(score(rules, checks)['overall_pass'])
        checks[1]['outcome'] = 'pass'
        self.assertTrue(score(rules, checks)['overall_pass'])

    def test_missing_deliverable_fails_even_if_check_claims_pass(self):
        rules = pinned_task(FIXTURE)['rules']
        checks = [{'id': r['check'], 'execution': 'ok', 'outcome': 'pass'} for r in rules]
        result = score(rules, checks, ['circuit.kicad_sch'])
        self.assertEqual([r['status'] for r in result['rules']], ['fail', 'fail'])
        self.assertFalse(result['overall_pass'])

    def test_aggregate_keeps_unknown_scores_null_and_averages_exact_bounds(self):
        rules = pinned_task(FIXTURE)['rules']
        known = score(rules, [{'id': r['check'], 'execution': 'ok', 'outcome': 'pass'} for r in rules])
        unknown = score(rules, [])
        result = average_scores([known, unknown])
        self.assertIsNone(result['score'])
        self.assertEqual(result['tasks'], 2)
        self.assertEqual(result['lower'], {'numerator': 1, 'denominator': 2})
        self.assertEqual(result['upper'], {'numerator': 1, 'denominator': 1})

    def test_aggregate_refuses_handwritten_and_unrecorded_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = {'schema': 'agent-ee.generation-evaluation/1', 'binding': {'inputs': 'fake'},
                      'task': 'fake', 'domain': 'x', 'difficulty': 'y',
                      'generation': {'execution': 'ok'},
                      'scoring': score(pinned_task(FIXTURE)['rules'], [])}
            forged = Path(tmp) / 'forged.json'
            write_json(forged, report)
            with self.assertRaisesRegex(ValueError, 'identity-bound'):
                aggregate([forged])
            stored = publish_report(tmp, report)
            with self.assertRaisesRegex(ValueError, 'recorded evaluation trial'):
                aggregate([stored])

    def test_saved_record_conflict_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = {'binding': {'inputs': 'same'}, 'scoring': {'score': None}}
            path = publish_report(tmp, report)
            original = path.read_bytes()
            self.assertEqual(publish_report(tmp, report), path)
            report['scoring']['score'] = 1
            with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
                publish_report(tmp, report)
            self.assertEqual(path.read_bytes(), original)


class CaseBudgetTests(unittest.TestCase):
    def setUp(self):
        from agent_ee import native_run
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.generator = self.root / 'generator.py'
        self.generator.write_text('raise SystemExit(23)\n')
        self.clock = 1000.0
        self.generation_seconds = 1
        self.grade_seconds = 0
        self.grade_starts = []
        self.execution = 'failed'
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('time.monotonic', lambda: self.clock))
        self.stack.enter_context(patch.object(benchmark, 'evaluator_identity', self.environment))
        self.adapter = native_run.Recorded(self.generate)
        self.stack.enter_context(native_run.using(self.adapter))

    def environment(self):
        self.grade_starts.append(self.clock)
        self.clock += self.grade_seconds / 2
        return {'authored': 'CPU evaluator identity'}

    def generate(self, call):
        from agent_ee import native_run
        self.clock += min(self.generation_seconds, call.timeout)
        return native_run.Outcome(execution='timeout' if self.generation_seconds >= call.timeout else self.execution,
                                  exit_code=23, stderr=b'authored failed generator')

    def run_case(self, **kwargs):
        return run_task(FIXTURE, self.generator, self.root / 'trial', self.root / 'store',
                        attempts=2, timeout=100, case_timeout=100, grading_reserve=20, **kwargs)

    def ledger(self):
        return read_json(self.root / 'trial/trial.json')

    def test_generation_and_grading_share_one_clock_and_work_counter_across_retries(self):
        self.generation_seconds = 25
        self.grade_seconds = 10
        reports = self.run_case()
        self.assertEqual(len(reports), 2)
        self.assertEqual([c.timeout for c in self.adapter.calls], [80, 45])
        ledger = self.ledger()
        self.assertEqual((ledger['attempts_started'], ledger['work_started']), (2, 4))
        self.assertEqual(ledger['elapsed_s'], 70)
        self.assertEqual(ledger['status'], 'attempts_exhausted')
        self.assertTrue(all(read_json(p)['result']['overall_pass'] is False for p in reports))

    def test_generation_stops_at_productive_cutoff_and_reserved_grading_still_runs(self):
        self.generation_seconds = 1000
        self.grade_seconds = 18
        reports = self.run_case()
        self.assertEqual(len(reports), 1)
        self.assertEqual(self.adapter.calls[0].timeout, 80)
        self.assertEqual(self.grade_starts[0], 1080)
        ledger = self.ledger()
        self.assertEqual(ledger['elapsed_s'], 98)
        self.assertEqual(ledger['status'], 'budget_exhausted')
        self.assertEqual(ledger['attempt_records'][0]['generation_execution'], 'timeout')
        self.assertFalse((self.root / 'trial/attempt-2').exists())
        self.assertFalse(read_json(reports[0])['result']['overall_pass'])

    def test_grading_exhaustion_keeps_started_attempt_and_partial_evaluation(self):
        self.generation_seconds = 75
        self.grade_seconds = 30
        self.assertEqual(self.run_case(), [])
        ledger = self.ledger()
        self.assertEqual((ledger['attempts_started'], ledger['work_started']), (1, 2))
        self.assertEqual(ledger['status'], 'budget_exhausted')
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertTrue((self.root / 'trial/attempt-1/execution.json').is_file())
        self.assertTrue((self.root / 'trial/evaluation-1').is_dir())

    def test_work_exhaustion_never_refunds_failed_generation_or_missing_output(self):
        reports = self.run_case(max_work=2)
        self.assertEqual(len(reports), 1)
        self.assertEqual(self.ledger()['work_started'], 2)
        self.assertEqual(self.ledger()['attempts_started'], 1)
        self.assertEqual(self.ledger()['status'], 'budget_exhausted')
        self.assertEqual(read_json(reports[0])['generation']['missing_deliverables'], ['circuit.kicad_sch'])
        self.assertFalse((self.root / 'trial/attempt-2').exists())

    def test_unexpected_generation_error_retains_admission_before_propagating(self):
        self.adapter.responder = lambda call: OSError('authored launch failure')
        with self.assertRaisesRegex(OSError, 'authored launch failure'):
            self.run_case()
        ledger = self.ledger()
        self.assertEqual((ledger['attempts_started'], ledger['work_started']), (1, 1))
        self.assertEqual(ledger['status'], 'error')
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertIn('authored launch failure', ledger['attempt_records'][0]['error'])

    def test_pre_cancelled_case_admits_no_attempt_or_public_preparation(self):
        from agent_ee import native_run
        cancel = native_run.Cancellation()
        cancel.cancel()
        self.assertEqual(self.run_case(cancel=cancel), [])
        ledger = self.ledger()
        self.assertEqual((ledger['status'], ledger['attempts_started'], ledger['work_started']),
                         ('cancelled', 0, 0))
        self.assertFalse((self.root/'trial/public').exists())
        self.assertFalse(self.adapter.calls)

    def test_cancellation_reaches_generation_and_retains_interrupted_admission(self):
        from agent_ee import native_run
        cancel = native_run.Cancellation()
        def cancelled(call):
            cancel.cancel()
            return native_run.Outcome()
        self.adapter.responder = cancelled
        with self.assertRaises(KeyboardInterrupt):
            self.run_case(cancel=cancel)
        ledger = self.ledger()
        self.assertEqual(ledger['attempts_started'], 1)
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertEqual(read_json(self.root/'trial/attempt-1/execution.json')['execution'], 'interrupted')
        self.assertEqual(len(self.adapter.calls), 1)

    def test_cancel_during_grading_retains_case_and_cannot_start_retry(self):
        from agent_ee import native_run
        cancel = native_run.Cancellation()
        def cancelled_identity():
            cancel.cancel()
            return {'authored': 'cancelled runtime probe'}
        with patch.object(benchmark, 'evaluator_identity', cancelled_identity):
            self.assertEqual(self.run_case(cancel=cancel), [])
        ledger = self.ledger()
        self.assertEqual((ledger['status'], ledger['attempts_started'], ledger['work_started']),
                         ('cancelled', 1, 2))
        self.assertEqual(ledger['attempt_records'][0]['generation_execution'], 'failed')
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertEqual(len(self.adapter.calls), 1)

    def test_series_authenticates_all_started_attempts_including_failed_ones(self):
        reports = self.run_case()
        pins = {'original-series': digest(FIXTURE / 'pins.json')}
        manifest = self.root / 'series.json'
        write_json(manifest, benchmark.freeze_series([reports[-1]], pins))
        anchor = digest(manifest)
        entry = read_json(manifest)['evaluations'][0]
        self.assertIn('attempt-1/execution.json', entry['case_files'])
        self.assertIn('attempt-2/execution.json', entry['case_files'])
        self.assertEqual([r['failed'] for r in aggregate([reports[-1]], manifest=manifest, expect_pins=anchor)['rows']], [1, 1])
        (self.root / 'trial/attempt-1/stderr.log').write_text('changed earlier failed attempt')
        with self.assertRaises(ValueError):
            aggregate([reports[-1]], manifest=manifest, expect_pins=anchor)

    def test_case_exhaustion_cannot_be_selected_as_success(self):
        reports = self.run_case(max_work=2)
        report = read_json(reports[0])
        report['result']['overall_pass'] = True
        with self.assertRaisesRegex(ValueError, 'case cannot supply success'):
            benchmark.case_files(report, reports[0])

    def test_invalid_case_limits_refuse_before_scratch_and_cli_threads_limits(self):
        for kwargs in ({'case_timeout': 0}, {'case_timeout': True}, {'grading_reserve': 0},
                       {'case_timeout': 100, 'grading_reserve': 100}, {'max_work': 1}, {'max_work': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_task(FIXTURE, self.generator, self.root/'unused', self.root/'store', **kwargs)
        self.assertFalse((self.root/'unused').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            main(['run', '--task', str(FIXTURE), '--generator', str(self.generator),
                  '--scratch', str(self.root/'cli'), '--store', str(self.root/'store'),
                  '--attempts', '2', '--timeout', '10', '--case-timeout', '30',
                  '--grading-reserve', '5', '--max-work', '2'])
        ledger = read_json(self.root/'cli/trial.json')
        self.assertEqual(ledger['limits'], {'attempts': 2, 'timeout_s': 10,
            'case_timeout_s': 30, 'grading_reserve_s': 5, 'max_work': 2})


class BudgetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = 1000.0
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('time.monotonic', lambda: self.clock))

    def test_nested_native_deadline_cannot_be_renewed(self):
        from agent_ee import native_run
        boundary = native_run.Recorded()
        owner = self
        class Outer:
            def run(self, intent, **kwargs):
                owner.clock += 20
                return native_run.run(native_run.Intent('nested', ['authored'], timeout=1000, isolation='host'),
                    work=owner.root/'nested', deadline=5000, adapter=boundary)
        native_run.run(native_run.Intent('outer', ['authored'], timeout=1000),
            work=self.root/'outer', deadline=1050, adapter=Outer())
        self.assertEqual(boundary.calls[0].timeout, 30)
        self.assertIsNone(native_run._deadline.get())

    def test_limit_scope_preserves_routing_and_refuses_replacement_cancel(self):
        from agent_ee import native_run
        cancel = native_run.Cancellation()
        adapter = native_run.Recorded()
        with native_run.using(adapter), native_run.using(deadline=1050, cancel=cancel):
            native_run.run(native_run.Intent('probe', ['authored'], timeout=1000, isolation='host'),
                work=self.root/'probe', deadline=2000)
            self.assertEqual(adapter.calls[0].timeout, 50)
            with self.assertRaisesRegex(ValueError, 'enclosing cancellation'):
                native_run.run(native_run.Intent('probe', ['authored'], isolation='host'),
                    work=self.root/'replacement', cancel=native_run.Cancellation())
            self.clock = 1050
            with self.assertRaises(native_run.DeadlineExhausted):
                native_run.run(native_run.Intent('probe', ['authored'], isolation='host'),
                    work=self.root/'late', deadline=2000)
        self.assertEqual(len(adapter.calls), 1)
        self.assertIsNone(native_run._deadline.get())
        self.assertIsNone(native_run._cancel.get())

    def test_sandbox_setup_consumes_original_deadline_and_refuses_late_spawn(self):
        from agent_ee import native_run, executor
        candidate = self.root/'candidate';candidate.mkdir()
        def prepare(*a, **kw):
            self.clock = 1050
            return ['authored sandbox']
        @contextlib.contextmanager
        def no_signal():
            yield []
        with patch.object(executor, 'sandbox_argv', prepare), patch.object(executor, 'held_interrupt', no_signal), \
             patch('subprocess.Popen', side_effect=AssertionError('late real child')) as launch:
            record = native_run.run(native_run.Intent('generation', ['authored'], timeout=1000),
                inputs=candidate, work=self.root/'generation', deadline=1050)
        launch.assert_not_called()
        self.assertEqual(record['execution'], 'not_run')
        self.assertTrue((self.root/'generation/execution.json').is_file())

    def test_host_setup_consumes_original_deadline_and_refuses_late_spawn(self):
        from agent_ee import native_run
        @contextlib.contextmanager
        def slow_setup():
            self.clock = 1050
            yield []
        with patch.object(native_run, 'held_interrupt', slow_setup), \
             patch('subprocess.Popen', side_effect=AssertionError('late real child')) as launch:
            record = native_run.run(native_run.Intent('generation', ['authored'], timeout=1000, isolation='host'),
                work=self.root/'generation', deadline=1050)
        launch.assert_not_called()
        self.assertEqual(record['execution'], 'timeout')

    def test_check_cancellation_reaches_delegates_and_prevents_native_launch(self):
        from agent_ee import native_run, checks
        cancel = native_run.Cancellation()
        manifest = {'candidate_id': 'authored', 'scope': {'checks': [{'id': 'erc', 'kind': 'erc'}]}}
        write_json(self.root/'candidate.json', manifest)
        observed = []
        def boundary(intent, **kwargs):
            observed.append(kwargs['cancel'])
            cancel.cancel()
            return {'execution': 'interrupted', 'exit_code': None}
        ctx = checks.Ctx(self.root, manifest, manifest['scope']['checks'][0], 1100, cancel=cancel)
        with patch.object(native_run, 'run', boundary):
            child = ctx.evaluate(lambda c: c.run_native(native_run.Intent('probe', ['authored'])))
        self.assertIs(observed[0], cancel)
        with patch.object(native_run, 'run', side_effect=AssertionError('cancelled child')):
            result = checks.run_checks(self.root, 1100, cancel=cancel)
        self.assertEqual(result[0]['execution'], 'interrupted')
        self.assertEqual(result[0]['outcome'], 'unknown')

    def test_process_group_control_refuses_unsafe_ids_before_signal(self):
        from agent_ee import native_run
        from types import SimpleNamespace
        with patch('os.killpg', side_effect=AssertionError('unsafe signal')) as signal:
            for pid in (-1, 0, 1, True):
                for control in (native_run._kill_group, lambda p: native_run._stop_group(p, 1)):
                    with self.subTest(pid=pid), self.assertRaisesRegex(ValueError, 'unsafe owned process group'):
                        control(SimpleNamespace(pid=pid))
            signal.assert_not_called()


class CaptureIdentityBudgetTests(unittest.TestCase):
    """Actual capture/runtime identity seams; only external process outcomes are authored."""
    def setUp(self):
        from agent_ee import native_run
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.generator = self.root/'generator.py'
        self.generator.write_text('authored complete-deliverable process boundary\n')
        self.extension = self.root/'authored-pcbnew.so'
        self.extension.write_bytes(b'CPU identity fixture, not a loaded extension')
        self.clock = 1000.0
        self.cancel = native_run.Cancellation()
        self.calls = []
        self.mode = None
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('time.monotonic', lambda: self.clock))
        self.stack.enter_context(patch.object(native_run, 'supervise', self.process))
        generation = native_run.Recorded(lambda call: native_run.Outcome(files={
            'output/circuit.kicad_sch': (FIXTURE/'reference.kicad_sch').read_bytes()}))
        owner = self
        class Boundary:
            def run(self, intent, **kwargs):
                if intent.tool == 'generation':
                    return generation.run(intent, **kwargs)
                if intent.isolation != 'host':
                    if owner.mode == 'identity-final-cutoff':
                        owner.calls.append({'kind':'check'})
                        return native_run.Recorded(lambda call: native_run.Outcome(
                            execution='unavailable', exit_code=None, stderr=b'authored unavailable native check')).run(intent, **kwargs)
                    raise AssertionError('checks must not start after the failed capture/identity seam')
                return native_run.Host().run(intent, **kwargs)
        self.stack.enter_context(native_run.using(Boundary()))

    def process(self, argv, *, timeout, cancel=None, **kwargs):
        from agent_ee import native_run
        kind = 'git' if argv[0] == 'git' else 'identity'
        self.calls.append({'kind': kind, 'timeout': timeout, 'deadline': native_run._deadline.get(),
                           'same_cancel': cancel is self.cancel, 'argv': list(argv)})
        if kind == 'git':
            self.assertEqual(tuple(argv[-2:]), ('rev-parse', 'HEAD'))
            if self.mode == 'git-cutoff':
                self.assertIs(cancel, self.cancel)
                self.assertEqual(timeout, native_run._deadline.get()-self.clock)
                self.clock = native_run._deadline.get()
                return {'execution':'timeout','exit_code':None,'returncode':-9}, b'', b'authored case cutoff'
            if self.mode == 'git-cancel':
                self.assertIs(cancel, self.cancel)
                cancel.cancel()
                return {'execution':'interrupted','exit_code':None,'returncode':-9}, b'', b'authored cancellation'
            code = 128 if self.mode == 'git-not-repo' else 0
            return {'execution':'failed' if code else 'ok','exit_code':code,'returncode':code}, b'  authored-revision  \n', b''
        self.assertIn('import _pcbnew', argv[-1])
        if self.mode == 'identity-cutoff' or (self.mode == 'identity-final-cutoff' and
                sum(c['kind'] == 'identity' for c in self.calls) == 2):
            self.assertIs(cancel, self.cancel)
            self.assertLess(timeout, 15)
            self.clock = native_run._deadline.get()
            return {'execution':'timeout','exit_code':None,'returncode':-9}, b'', b'authored enclosing cutoff'
        if self.mode == 'identity-local-timeout':
            self.assertEqual(timeout, 15)
            self.clock += 15
            return {'execution':'timeout','exit_code':None,'returncode':-9}, b'', b'authored local probe timeout'
        if self.mode == 'identity-unavailable':
            return {'execution':'unavailable','exit_code':None,'launch_error':FileNotFoundError('authored missing interpreter')}, b'', b''
        return {'execution':'ok','exit_code':0,'returncode':0}, (str(self.extension)+'\n').encode(), b''

    def run_case(self, case_timeout=10):
        return run_task(FIXTURE, self.generator, self.root/'trial', self.root/'store',
                        attempts=2, timeout=100, case_timeout=case_timeout,
                        grading_reserve=3, cancel=self.cancel)

    def assert_retained(self, status):
        ledger = read_json(self.root/'trial/trial.json')
        self.assertEqual((ledger['status'],ledger['attempts_started'],ledger['work_started']), (status,1,2))
        self.assertEqual(ledger['attempt_records'][0]['generation_execution'], 'ok')
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertEqual((self.root/'trial/attempt-1/output/circuit.kicad_sch').read_bytes(),
                         (FIXTURE/'reference.kicad_sch').read_bytes())
        self.assertFalse((self.root/'trial/attempt-2').exists())

    def test_complete_deliverable_capture_git_cutoff_keeps_attempt_and_stops_following_probes(self):
        self.mode = 'git-cutoff'
        self.assertEqual(self.run_case(), [])
        self.assert_retained('budget_exhausted')
        self.assertTrue((self.root/'trial/evaluation-1/scope.json').is_file())
        self.assertTrue((self.root/'trial/evaluation-1/evaluation-inputs/circuit.kicad_sch').is_file())
        self.assertEqual([c['kind'] for c in self.calls], ['identity','git'])
        self.assertTrue(all(c['same_cancel'] for c in self.calls))
        self.assertFalse((self.root/'trial/evaluation-1/run/candidate.json').exists())

    def test_complete_deliverable_capture_git_cancel_keeps_attempt_and_no_next_probe(self):
        self.mode = 'git-cancel'
        self.assertEqual(self.run_case(), [])
        self.assert_retained('cancelled')
        self.assertEqual([c['kind'] for c in self.calls], ['identity','git'])
        self.assertTrue(all(c['same_cancel'] for c in self.calls))

    def test_real_identity_probe_timeout_at_case_cutoff_is_budget_exhaustion(self):
        self.mode = 'identity-cutoff'
        self.assertEqual(self.run_case(), [])
        self.assert_retained('budget_exhausted')
        self.assertEqual([c['kind'] for c in self.calls], ['identity'])

    def test_final_identity_observation_cutoff_keeps_captured_unknown_checks_and_attempt(self):
        self.mode = 'identity-final-cutoff'
        self.assertEqual(self.run_case(), [])
        self.assert_retained('budget_exhausted')
        run = self.root/'trial/evaluation-1/run'
        self.assertTrue((run/'candidate.json').is_file())
        self.assertTrue((run/'work/verification/evidence.json').is_file())
        self.assertTrue(list((run/'checks').glob('*.json')))
        self.assertIn('check', [c['kind'] for c in self.calls])
        self.assertEqual(self.calls[-1]['kind'], 'identity')
        self.assertEqual(sum(c['kind'] == 'identity' for c in self.calls), 2)

    def test_identity_cutoff_uses_a_shorter_inherited_scope(self):
        from agent_ee import native_run
        self.mode = 'identity-cutoff'
        with native_run.using(deadline=1005, cancel=self.cancel):
            self.assertEqual(self.run_case(case_timeout=100), [])
        self.assert_retained('budget_exhausted')
        self.assertEqual(self.clock, 1005)
        self.assertEqual(self.calls[0]['timeout'], 5)

    def test_shorter_identity_probe_timeout_stays_a_genuine_error(self):
        import subprocess
        self.mode = 'identity-local-timeout'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_case(case_timeout=100)
        self.assert_retained('error')
        self.assertLess(self.clock, 1100)
        self.assertEqual([c['kind'] for c in self.calls], ['identity'])

    def test_identity_environment_failure_is_not_relabelled_exhaustion(self):
        self.mode = 'identity-unavailable'
        with self.assertRaisesRegex(FileNotFoundError, 'authored missing interpreter'):
            self.run_case(case_timeout=100)
        self.assert_retained('error')
        self.assertEqual(self.clock, 1000)

    def test_standalone_source_revision_and_non_repository_none_are_preserved(self):
        from agent_ee.candidate import source_manifest
        payload = self.root/'payload.txt'
        payload.write_text('authored source')
        scope = {'inputs':[{'path':'payload.txt','at':'payload.txt'}]}
        for mode, expected in (('git-success','authored-revision'), ('git-not-repo',None)):
            with self.subTest(mode=mode):
                self.mode = mode
                files, sources, revisions = source_manifest(scope, self.root)
                self.assertEqual(revisions, {'payload.txt':expected})
                self.assertEqual(files, {'payload.txt':digest(payload)})
                self.assertEqual(sources, {'payload.txt':str(payload)})
                self.assertIsNone(self.calls[-1]['timeout'])
                self.assertIsNone(self.calls[-1]['deadline'])


class RefusalTests(unittest.TestCase):
    """Public authored cases: oracle frozen independently of producer parsing."""
    def setUp(self):
        from agent_ee import acquisition_prepare
        from agent_ee.storage import json_bytes
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / 'task'; self.task.mkdir()
        (self.task / 'evaluator').mkdir()
        shutil.copyfile(FIXTURE / 'reference.kicad_sch', self.task / 'reference.kicad_sch')
        self.clause = 'Add a café status display.'
        self.raw = ('Use acquisition reference acquisition-control unchanged.\n'
            'Total I2C line capacitance shall be at most 150 pF per line, including board, host and cable.\n'
            '  ' + self.clause + '\r\n').encode()
        (self.task / 'prompt.md').write_bytes(self.raw)
        (self.task / 'evaluator/capability.py').write_bytes(Path(acquisition_prepare.__file__).read_bytes())
        start = self.raw.index(self.clause.encode())
        self.contract = {'outcome': 'unresolved', 'code': 'unsupported_clause',
            'brief_sha256': benchmark.hashlib.sha256(self.raw).hexdigest(),
            'byte_start': start, 'byte_end': start + len(self.clause.encode()),
            'clause_sha256': benchmark.hashlib.sha256(self.clause.encode()).hexdigest(),
            'context_files': {'evaluator/capability.py': digest(self.task / 'evaluator/capability.py')}}
        self.contract_path = self.task / 'evaluator/refusal-contract.json'
        write_json(self.contract_path, self.contract)
        write_json(self.task / 'task.json', {'schema': 'agent-ee.generation-task/1',
            'id': 'public-refusal', 'domain': 'public-source', 'difficulty': 'public',
            'prompt': 'prompt.md', 'reference': 'reference.kicad_sch', 'deliverables': ['refusal.json'],
            'expected_refusal': {'contract': 'evaluator/refusal-contract.json'}})
        repin(self.task)
        # The positive response comes from the actual producer, not the oracle.
        with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
            acquisition_prepare.prepare(self.task / 'prompt.md', self.root / 'absent-plan',
                self.root / 'never-prepared', added_sources={})
        self.response = caught.exception.report
        self.assertFalse((self.root / 'never-prepared').exists())
        self.payload = json_bytes(self.response)
        self.number = 0
        self.guard = patch.object(benchmark, 'evaluator_identity', side_effect=AssertionError('native probe'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def evaluated(self, raw=None, *, status='ok', extra=None, directories=(), store=None):
        from agent_ee import native_run
        self.number += 1
        work = self.root / f'writer-{self.number}'
        files = {} if raw is False else {'output/refusal.json': self.payload if raw is None else raw}
        files.update({'output/' + k: v for k, v in (extra or {}).items()})
        native_run.run(native_run.Intent('generation', ['authored-public-response'], isolation='host'),
            work=work, adapter=native_run.Recorded(lambda call: native_run.Outcome(
                execution=status, exit_code=0 if status == 'ok' else 23, files=files)))
        for directory in directories:
            (work / 'output' / directory).mkdir(parents=True)
        trial = self.root / f'eval-{self.number}'
        path = evaluate(self.task, work / 'output', trial,
            self.root / 'store' if store is None else store, execution_work=work)
        return path, trial

    def anchor(self, path):
        target = self.root / f'series-{self.number}.json'
        write_json(target, benchmark.freeze_series([path], {'public-refusal': digest(self.task / 'pins.json')}))
        return target, digest(target)

    def test_real_public_producer_refusal_replays_freezes_and_is_never_a_design_pass(self):
        from agent_ee import acquisition_prepare
        with patch.object(acquisition_prepare, 'parse_brief', side_effect=AssertionError('producer oracle forbidden')):
            path, trial = self.evaluated()
            report = read_json(path)
            self.assertTrue(report['result']['overall_pass'])
            self.assertEqual(report['kind'], 'source_refusal')
            self.assertEqual(report['evaluation']['checks'], [
                {'id': 'refusal', 'kind': 'source_refusal', 'execution': 'ok', 'outcome': 'pass'}])
            self.assertFalse((trial / 'run').exists())
            self.assertEqual(read_json(trial / 'outputs/refusal.json'), self.response)
            manifest, anchor = self.anchor(path)
            self.assertIsNone(read_json(manifest)['evaluations'][0]['evidence_sha256'])
            rows = aggregate([path], manifest=manifest, expect_pins=anchor)['rows']
            self.assertEqual([r['passed'] for r in rows], [1, 1])
            self.assertEqual({r['kind'] for r in rows}, {'source_refusal'})
            self.assertEqual({r['tasks'] for r in rows}, {1})

    def test_second_public_utf8_clause_with_unicode_line_boundary_uses_frozen_bytes(self):
        from agent_ee import acquisition_prepare
        from agent_ee.storage import json_bytes
        clause = 'Require a π-shaped custom display with a different byte length.'
        raw = ('Use acquisition reference acquisition-control unchanged.\u2028\t' + clause + '\n').encode()
        (self.task / 'prompt.md').write_bytes(raw)
        start = raw.index(clause.encode())
        self.contract.update(brief_sha256=benchmark.hashlib.sha256(raw).hexdigest(), byte_start=start,
            byte_end=start+len(clause.encode()), clause_sha256=benchmark.hashlib.sha256(clause.encode()).hexdigest())
        write_json(self.contract_path, self.contract); repin(self.task)
        with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
            acquisition_prepare.prepare(self.task / 'prompt.md', self.root / 'absent-plan',
                self.root / 'no-bundle', added_sources={})
        response = caught.exception.report
        with patch.object(acquisition_prepare, 'parse_brief', side_effect=AssertionError('no producer oracle')):
            path, _ = self.evaluated(json_bytes(response))
            self.assertTrue(read_json(path)['result']['overall_pass'])
            manifest, anchor = self.anchor(path)
            self.assertEqual([r['passed'] for r in aggregate([path], manifest=manifest, expect_pins=anchor)['rows']], [1,1])
        self.assertFalse((self.root / 'no-bundle').exists())

    def test_wrong_generic_extra_and_malformed_responses_fail_independent_oracle(self):
        from agent_ee.storage import json_bytes
        import copy
        mutations = [lambda r: r.update(outcome='refused'), lambda r: r.update(outcome='prepared'),
            lambda r: r.update(outcome='unavailable'), lambda r: r.update(brief_sha256='0'*64),
            lambda r: r['findings'][0].update(code='unavailable'),
            lambda r: r['findings'][0].update(message='cannot comply'),
            lambda r: r['findings'][0].update(message='unsupported or ambiguous brief clause: invented'),
            lambda r: r['findings'][0].update(byte_start=self.contract['byte_start']+1),
            lambda r: r['findings'][0].update(byte_end=float(self.contract['byte_end'])),
            lambda r: r['findings'][0].update(byte_start=True),
            lambda r: r['findings'].append(dict(r['findings'][0])),
            lambda r: r.update(findings=[]), lambda r: r.update(prepared=True),
            lambda r: r.update(release={}), lambda r: r.update(context_files=self.contract['context_files'])]
        for mutate in mutations:
            response = copy.deepcopy(self.response); mutate(response)
            with self.subTest(mutate=mutate):
                path, trial = self.evaluated(json_bytes(response))
                self.assertFalse(read_json(path)['result']['overall_pass'])
                self.assertEqual([r['failed'] for r in aggregate([path])['rows']], [1, 1])
        for raw in (b'{}', b'null', b'not JSON', b'\xff', b'{"outcome":1,"outcome":2}'):
            with self.subTest(raw=raw):
                path, _ = self.evaluated(raw)
                self.assertFalse(read_json(path)['result']['overall_pass'])

    def test_refusal_with_any_file_or_empty_directory_is_retained_and_fails(self):
        for extra, directories in (({'board.kicad_pcb': b'board'}, ()),
                ({'package/release.zip': b'package'}, ()), ({'extra.txt': b'extra'}, ()),
                ({}, ('release',)), ({}, ('prepared',)), ({}, ('other/empty',))):
            with self.subTest(extra=extra, directories=directories):
                path, trial = self.evaluated(extra=extra, directories=directories)
                report = read_json(path)
                self.assertFalse(report['result']['overall_pass'])
                self.assertEqual(tree_files(trial / 'outputs'), report['binding']['output_tree']['files'])
                for directory in directories:
                    self.assertTrue((trial / 'outputs' / directory).is_dir())
                self.assertEqual([r['failed'] for r in aggregate([path])['rows']], [1, 1])

    def test_missing_nonzero_and_timeout_keep_denominator_despite_valid_response(self):
        for raw, status in ((False, 'ok'), (None, 'failed'), (None, 'timeout')):
            with self.subTest(status=status, missing=raw is False):
                path, _ = self.evaluated(raw, status=status)
                report = read_json(path)
                self.assertFalse(report['result']['overall_pass'])
                if raw is False:
                    self.assertEqual(report['generation']['missing_deliverables'], ['refusal.json'])
                    self.assertFalse(report['scoring']['overall_pass'])
                else:
                    self.assertTrue(report['scoring']['overall_pass'])
                    self.assertEqual(report['generation']['execution'], 'failed')
                self.assertEqual([r['tasks'] for r in aggregate([path])['rows']], [1, 1])

    def test_unsafe_links_and_special_entries_cannot_supply_refusal_evidence(self):
        import os
        for kind in ('symlink', 'hardlink', 'fifo'):
            work = self.root / kind; work.mkdir(); output = work / 'output'; output.mkdir()
            if kind == 'symlink':
                (output / 'refusal.json').symlink_to(self.task / 'prompt.md')
            elif kind == 'hardlink':
                (output / 'ordinary').write_bytes(self.payload)
                os.link(output / 'ordinary', output / 'refusal.json')
            else:
                os.mkfifo(output / 'refusal.json')
            with self.subTest(kind=kind):
                path = evaluate(self.task, output, self.root / ('eval-' + kind), self.root / 'store')
                report = read_json(path)
                self.assertFalse(report['result']['overall_pass'])
                self.assertEqual(report['generation']['output_error'], 'non_ordinary_output')
                self.assertIsNone(report['binding']['output_tree'])
                aggregate([path])

    def test_private_contract_pins_spans_and_capability_context_are_required(self):
        import copy
        before = self.contract_path.read_bytes(); anchor = digest(self.task / 'pins.json')
        cases = [dict(context_files={}), dict(context_files={'evaluator/capability.py':'0'*64}),
            dict(context_files={'prompt.md':digest(self.task / 'prompt.md')}),
            dict(code='invented'), dict(outcome='refused'), dict(brief_sha256='0'*64),
            dict(clause_sha256='0'*64), dict(byte_start=True), dict(byte_end=len(self.raw)+1),
            dict(byte_start=self.raw.index('é'.encode())+1)]
        for changes in cases:
            changed = copy.deepcopy(self.contract); changed.update(changes)
            write_json(self.contract_path, changed); repin(self.task)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pinned_task(self.task)
        self.contract_path.write_bytes(before); repin(self.task)
        self.assertEqual(digest(self.task / 'pins.json'), anchor)
        context = self.task / 'evaluator/capability.py'; original = context.read_bytes()
        context.write_bytes(b'')
        empty_context = dict(self.contract, context_files={'evaluator/capability.py':digest(context)})
        write_json(self.contract_path, empty_context); repin(self.task)
        with self.assertRaisesRegex(ValueError, 'refusal context'):
            pinned_task(self.task)
        context.write_bytes(original); self.contract_path.write_bytes(before); repin(self.task)
        context.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError, 'task-file hash'):
            pinned_task(self.task)
        self.contract['context_files']['evaluator/capability.py'] = digest(context)
        write_json(self.contract_path, self.contract); repin(self.task)
        with self.assertRaisesRegex(ValueError, 'external pins'):
            pinned_task(self.task, expect_pins=anchor)

    def test_refusal_variant_refuses_native_scoring_or_another_output_contract(self):
        original = (self.task / 'task.json').read_bytes()
        for changes in ({'scope':{}}, {'rules':[]}, {'deliverables':['refusal.json','board.kicad_pcb']},
                {'expected_refusal':{'contract':'prompt.md'}},
                {'expected_refusal':{'contract':'evaluator/../prompt.md'}}):
            task = benchmark.strict_json(original); task.update(changes)
            write_json(self.task / 'task.json', task); repin(self.task)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pinned_task(self.task)
        (self.task / 'task.json').write_bytes(original); repin(self.task)

    def test_complete_snapshot_census_and_raw_bytes_are_rechecked(self):
        for mutate in ('bytes', 'add-directory', 'remove-empty-directory', 'remove-extra-file'):
            path, trial = self.evaluated(extra={'extra': b'x'} if mutate == 'remove-extra-file' else None,
                directories=('release',) if mutate == 'remove-empty-directory' else (),
                store=self.root / ('census-store-' + mutate))
            report, witness = benchmark.recorded_evaluation(path)
            self.assertEqual(witness, trial)
            self.assertEqual(len(list((path.parent / 'trials').glob('*.json'))), 1)
            manifest, anchor = self.anchor(path)
            rows = aggregate([path], manifest=manifest, expect_pins=anchor)['rows']
            self.assertEqual([r['passed'] for r in rows],
                             [int(report['result']['overall_pass'] is True)] * 2)
            snapshot = trial / 'outputs'
            if mutate == 'bytes': (snapshot / 'refusal.json').write_bytes(b'{}')
            elif mutate == 'add-directory': (snapshot / 'release').mkdir()
            elif mutate == 'remove-empty-directory': (snapshot / 'release').rmdir()
            else: (snapshot / 'extra').unlink()
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, 'retained refusal output'):
                benchmark.replay_report(trial, report)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, 'retained refusal output'):
                aggregate([path], manifest=manifest, expect_pins=anchor)

    def test_replay_does_not_trust_coordinated_cached_pass(self):
        path, trial = self.evaluated(b'{}')
        report = read_json(path)
        report['evaluation']['checks'][0]['outcome'] = 'pass'
        report['scoring'] = score(pinned_task(self.task)['rules'], report['evaluation']['checks'])
        report['result'] = benchmark.combined_result('ok', report['scoring'])
        path.unlink(); forged = publish_report(self.root / 'store', report)
        receipt = {'schema': 'agent-ee.generation-evaluation-record/1',
                   'report': str(forged.resolve()), 'report_sha256': digest(forged)}
        write_json(trial / 'evaluation.json', receipt)
        records = forged.parent / 'trials'; records.mkdir(exist_ok=True)
        write_json(records / 'forged.json', {'trial': str(trial.resolve()), **receipt})
        with self.assertRaisesRegex(ValueError, 'retained evidence'):
            aggregate([forged])

    def test_run_task_retries_missing_output_without_leaking_private_contract(self):
        from agent_ee import native_run
        calls = []
        def writer(call):
            calls.append(call)
            self.assertEqual(sorted(p.name for p in call.inputs.iterdir()), ['generator.py', 'prompt.md'])
            self.assertEqual((call.inputs / 'prompt.md').read_bytes(), self.raw)
            if len(calls) == 1:
                return native_run.Outcome()
            feedback = read_json(call.work / 'feedback.json')
            self.assertEqual(set(feedback), {'checks', 'missing_deliverables'})
            self.assertEqual(feedback['missing_deliverables'], ['refusal.json'])
            self.assertNotIn('context_files', repr(feedback))
            return native_run.Outcome(files={'output/refusal.json': self.payload})
        generator = self.root / 'generator.py'; generator.write_text('public authored boundary only')
        with native_run.using(native_run.Recorded(writer)):
            paths = run_task(self.task, generator, self.root / 'case', self.root / 'store',
                attempts=2, timeout=30, case_timeout=100, grading_reserve=20)
        self.assertEqual(len(paths), 2)
        self.assertFalse(read_json(paths[0])['result']['overall_pass'])
        self.assertTrue(read_json(paths[1])['result']['overall_pass'])
        ledger = read_json(self.root / 'case/trial.json')
        self.assertEqual((ledger['attempts_started'], ledger['work_started'], ledger['status']), (2,4,'completed'))
        manifest, anchor = self.anchor(paths[-1])
        self.assertEqual([r['passed'] for r in aggregate([paths[-1]], manifest=manifest, expect_pins=anchor)['rows']], [1,1])
        earlier = self.root / 'case/evaluation-1/outputs'
        (earlier / 'release').mkdir()
        with self.assertRaises(ValueError):
            aggregate([paths[-1]], manifest=manifest, expect_pins=anchor)
        (earlier / 'release').rmdir()
        (self.root / 'case/attempt-1/stderr.log').write_bytes(b'changed earlier failure')
        with self.assertRaises(ValueError):
            aggregate([paths[-1]], manifest=manifest, expect_pins=anchor)

    def test_refusal_cannot_bypass_case_work_exhaustion_or_cancelled_grading(self):
        from agent_ee import native_run
        generator = self.root / 'generator.py'; generator.write_text('public boundary')
        adapter = native_run.Recorded(lambda call: native_run.Outcome(execution='failed', exit_code=23,
            files={'output/refusal.json':self.payload}))
        with native_run.using(adapter):
            paths = run_task(self.task, generator, self.root / 'exhausted', self.root / 'store',
                attempts=2, timeout=30, case_timeout=100, grading_reserve=20, max_work=2)
        ledger = read_json(self.root / 'exhausted/trial.json')
        self.assertEqual((ledger['status'], ledger['attempts_started'], ledger['work_started']),
                         ('budget_exhausted',1,2))
        self.assertTrue(read_json(paths[0])['scoring']['overall_pass'])
        self.assertFalse(read_json(paths[0])['result']['overall_pass'])
        cancel = native_run.Cancellation(); cancel.cancel()
        with self.assertRaises(benchmark.native_run.DeadlineExhausted):
            evaluate(self.task, self.root / 'exhausted/attempt-1/output', self.root / 'cancelled-eval',
                self.root / 'store', cancel=cancel)
        self.assertFalse((self.root / 'cancelled-eval').exists())
        path, trial = self.evaluated()
        with self.assertRaisesRegex(ValueError, 'external task pins'):
            benchmark.freeze_series([path], {'public-refusal':'0'*64})
        diagnostic = evaluate(self.task, trial / 'outputs', self.root / 'diagnostic', self.root / 'store')
        with self.assertRaisesRegex(ValueError, 'retained executor'):
            benchmark.freeze_series([diagnostic], {'public-refusal':digest(self.task / 'pins.json')})

    def test_reserved_refusal_grading_cannot_renew_original_case_deadline(self):
        from agent_ee import native_run
        clock = [1000.0]; observations = []
        generator = self.root / 'generator.py'; generator.write_text('public boundary')
        def writer(call):
            self.assertEqual(call.timeout, 80)
            clock[0] += call.timeout
            return native_run.Outcome(execution='timeout', exit_code=None,
                files={'output/refusal.json': self.payload})
        def identity():
            observations.append((clock[0], native_run._deadline.get()))
            clock[0] += 12
            return {'authored':'source identity boundary'}
        with patch('time.monotonic', lambda: clock[0]), \
                patch.object(benchmark, '_refusal_identity', identity), native_run.using(native_run.Recorded(writer)):
            paths = run_task(self.task, generator, self.root / 'late-case', self.root / 'store',
                attempts=2, timeout=100, case_timeout=100, grading_reserve=20)
        self.assertEqual(paths, [])
        self.assertEqual(observations, [(1080,1100),(1092,1100)])
        ledger = read_json(self.root / 'late-case/trial.json')
        self.assertEqual((ledger['status'],ledger['attempts_started'],ledger['work_started']), ('budget_exhausted',1,2))
        self.assertEqual(ledger['attempt_records'][0]['generation_execution'], 'timeout')
        self.assertIsNone(ledger['attempt_records'][0]['evaluation_report'])
        self.assertTrue((self.root / 'late-case/evaluation-1/outputs/refusal.json').is_file())
        self.assertFalse((self.root / 'late-case/attempt-2').exists())

    def test_retained_private_context_mutation_and_repin_cannot_cross_external_custody(self):
        path, trial = self.evaluated()
        manifest, anchor = self.anchor(path)
        context = trial / 'task/evaluator/capability.py'
        context.write_bytes(context.read_bytes()+b'changed source context')
        with self.assertRaises(ValueError):
            aggregate([path], manifest=manifest, expect_pins=anchor)
        contract = read_json(trial / 'task/evaluator/refusal-contract.json')
        contract['context_files']['evaluator/capability.py'] = digest(context)
        write_json(trial / 'task/evaluator/refusal-contract.json', contract); repin(trial / 'task')
        with self.assertRaises(ValueError):
            aggregate([path], manifest=manifest, expect_pins=anchor)

    def test_refusal_and_native_rows_remain_separate_with_legacy_native_shape(self):
        from agent_ee import native_run
        refusal, _ = self.evaluated()
        work = self.root / 'native-writer'
        native_run.run(native_run.Intent('generation', ['authored-native-missing'], isolation='host'),
            work=work, adapter=native_run.Recorded())
        with patch.object(benchmark, 'evaluator_identity', return_value={'authored':'native identity'}):
            native = evaluate(FIXTURE, work / 'output', self.root / 'native-eval',
                self.root / 'store', execution_work=work)
        self.assertNotIn('kind', read_json(native))
        self.assertEqual(len(aggregate([native])['rows']), 2)
        self.assertTrue(all('kind' not in r for r in aggregate([native])['rows']))
        manifest = self.root / 'mixed-series.json'
        write_json(manifest, benchmark.freeze_series([native, refusal], {
            'original-series':digest(FIXTURE / 'pins.json'), 'public-refusal':digest(self.task / 'pins.json')}))
        rows = aggregate([native, refusal], manifest=manifest, expect_pins=digest(manifest))['rows']
        self.assertEqual({r['kind'] for r in rows}, {'native_design', 'source_refusal'})
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row['tasks'], 1)
            self.assertEqual(row['passed'], int(row['kind'] == 'source_refusal'))


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.store = self.root / 'store'

    def evaluate(self, suffix='1', **kwargs):
        return evaluate(FIXTURE, self.output, self.root / ('eval-' + suffix), self.store, **kwargs)

    def native_output(self, data=None):
        (self.output / 'circuit.kicad_sch').write_bytes(data or (FIXTURE / 'reference.kicad_sch').read_bytes())

    def generated(self, *, exit_code=0, defective=False, source=None):
        generator = self.root / 'generator.py'
        source = source if source is not None else (FIXTURE / 'reference.kicad_sch').read_text()
        if defective:
            source = source.replace('"1k"', '"2k"')
        generator.write_text('import sys\nfrom pathlib import Path\n'
                             'Path(sys.argv[2], "circuit.kicad_sch").write_text(' + repr(source) + ')\n'
                             'raise SystemExit(' + str(exit_code) + ')\n')
        path = run_task(FIXTURE, generator, self.root / 'trial', self.store, attempts=1)[0]
        return path, self.root / 'trial/evaluation-1'

    def anchor(self, path):
        report = read_json(path)
        trial = self.root / 'trial/evaluation-1'
        manifest = {'schema': 'agent-ee.generation-series/1', 'evaluations': [{
            'binding_id': path.parent.name, 'report_sha256': digest(path),
            'task_pins': digest(FIXTURE / 'pins.json'),
            'evidence_sha256': digest(trial / 'run/work/verification/evidence.json'),
            'generation_files': report['binding'].get('generation_records', {})}]}
        if 'case' in report['binding']:
            root = self.root / 'trial'
            names = ['trial.json', 'evaluation-1/evaluation.json'] + [
                'attempt-1/' + name for name in benchmark.RECORDS]
            manifest['evaluations'][0]['case_files'] = {name: digest(root/name) for name in names}
        self.assertEqual(manifest, benchmark.freeze_series(
            [path], {'original-series': digest(FIXTURE / 'pins.json')}))
        target = self.root / 'external-series.json'
        write_json(target, manifest)
        return target, digest(target)

    def republish(self, path, trial, report):
        path.unlink()
        forged = publish_report(self.store, report)
        receipt = {'schema': 'agent-ee.generation-evaluation-record/1',
                   'report': str(forged.resolve()), 'report_sha256': digest(forged)}
        write_json(trial / 'evaluation.json', receipt)
        records = forged.parent / 'trials'
        records.mkdir(exist_ok=True)
        write_json(records / 'forged.json', {'trial': str(trial.resolve()), **receipt})
        return forged

    def test_p1_resealed_defective_checks_refused_by_external_anchor(self):
        path, trial = self.generated(defective=True)
        report = read_json(path)
        self.assertFalse(report['result']['overall_pass'])
        manifest, anchor = self.anchor(path)
        checks = []
        for configured in pinned_task(FIXTURE)['scope']['checks']:
            check_path = trial / 'run/checks' / (configured['id'] + '.json')
            check = read_json(check_path)
            check.update(execution='ok', outcome='pass')
            write_json(check_path, check)
            checks.append(check)
        freeze_evidence(trial / 'run', checks)
        report['evaluation']['checks'] = [{k: c[k] for k in ('id', 'kind', 'execution', 'outcome')}
                                          for c in checks]
        report['scoring'] = score(pinned_task(FIXTURE)['rules'], checks)
        report['result'] = benchmark.combined_result('ok', report['scoring'])
        forged = self.republish(path, trial, report)
        with self.assertRaisesRegex(ValueError, 'case cannot supply success'):
            aggregate([forged])
        # The attacker also owns the local case ledger; the external anchor does not.
        ledger_path = trial.parent / 'trial.json'
        ledger = read_json(ledger_path)
        self.assertEqual(ledger['status'], 'attempts_exhausted')
        ledger['status'] = 'completed'
        write_json(ledger_path, ledger)
        self.assertEqual([r['passed'] for r in aggregate([forged])['rows']], [1, 1])
        with self.assertRaisesRegex(ValueError, 'external series'):
            aggregate([forged], manifest=manifest, expect_pins=anchor)

    def test_p2_caller_claim_cannot_override_retained_executor(self):
        path, trial = self.generated(exit_code=23)
        report = read_json(path)
        self.assertFalse(report['result']['overall_pass'])
        self.assertTrue(report['scoring']['overall_pass'])
        report['binding']['generation_execution'] = 'ok'
        report['generation']['execution'] = 'ok'
        report['result'] = benchmark.combined_result('ok', report['scoring'])
        forged = self.republish(path, trial, report)
        with self.assertRaisesRegex(ValueError, 'executor'):
            aggregate([forged])

    def test_p3d_repin_and_restratify_refused_by_external_anchor(self):
        path, trial = self.generated()
        manifest, anchor = self.anchor(path)
        task = read_json(trial / 'task/task.json')
        task.update(domain='forged-domain', difficulty='forged-difficulty')
        write_json(trial / 'task/task.json', task)
        repin(trial / 'task')
        report = read_json(path)
        report.update(domain=task['domain'], difficulty=task['difficulty'])
        report['binding']['task_files'] = tree_files(trial / 'task')
        forged = self.republish(path, trial, report)
        with self.assertRaisesRegex(ValueError, 'case attempt ledger'):
            aggregate([forged])
        # Complete the local rebind rather than mistaking an early refusal for anchor coverage.
        ledger_path = trial.parent / 'trial.json'
        ledger = read_json(ledger_path)
        index = report['binding']['case']['attempt'] - 1
        ledger['task_pins'] = report['binding']['task_files']['pins.json']
        ledger['evaluations'][index] = str(forged.resolve())
        ledger['attempt_records'][index]['evaluation_report'] = str(forged.resolve())
        write_json(ledger_path, ledger)
        self.assertEqual({r['value'] for r in aggregate([forged])['rows']},
                         {'forged-domain', 'forged-difficulty'})
        with self.assertRaisesRegex(ValueError, 'external series'):
            aggregate([forged], manifest=manifest, expect_pins=anchor)

    def test_authenticated_freeze_cli_and_aggregate_require_exact_anchor_and_selection(self):
        path, trial = self.generated()
        manifest, anchor = self.anchor(path)
        self.assertEqual([r['passed'] for r in aggregate([path], manifest=manifest,
                                                        expect_pins=anchor)['rows']], [1, 1])
        self.assertEqual(aggregate([path], manifest=manifest, expect_pins=anchor)['authentication'],
                         {'series_sha256': anchor})
        self.assertEqual(aggregate([path])['authentication'], 'diagnostic-unauthenticated')
        records = read_json(path)['binding']['generation_records']
        captured = read_json(trial / 'run/candidate.json')['files']
        self.assertEqual({k: captured['.generation/' + k] for k in records}, records)
        pins = self.root / 'external-task-pins.json'
        write_json(pins, {'original-series': digest(FIXTURE / 'pins.json')})
        cli_manifest = self.root / 'cli-series.json'
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            main(['freeze', '--manifest', str(cli_manifest), '--expect-pins', str(pins), str(path)])
        self.assertEqual(read_json(cli_manifest), read_json(manifest))
        self.assertEqual(benchmark.strict_json(stdout.getvalue())['sha256'], digest(cli_manifest))
        with self.assertRaises(FileExistsError), contextlib.redirect_stdout(io.StringIO()):
            main(['freeze', '--manifest', str(cli_manifest), '--expect-pins', str(pins), str(path)])
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            main(['aggregate', '--manifest', str(cli_manifest), '--expect-pins', anchor, str(path)])
        self.assertEqual([r['passed'] for r in benchmark.strict_json(stdout.getvalue())['rows']], [1, 1])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(['freeze', '--manifest', str(self.root / 'unused'), str(path)])
        self.assertFalse((self.root / 'unused').exists())
        for supplied in ('', '0' * 64, anchor.upper()):
            with self.subTest(anchor=supplied), self.assertRaisesRegex(ValueError, 'external series'):
                aggregate([path], manifest=manifest, expect_pins=supplied)
        for kwargs in ({'manifest': manifest}, {'expect_pins': anchor}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'both'):
                aggregate([path], **kwargs)
        for paths in ([], [path, path]):
            with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, 'selection'):
                aggregate(paths, manifest=manifest, expect_pins=anchor)
        original = manifest.read_bytes()
        manifest.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest digest'):
            aggregate([path], manifest=manifest, expect_pins=anchor)
        manifest.write_bytes(original)
        seal = trial / 'run/work/verification/evidence.json'
        seal.write_bytes(seal.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'external series evidence'):
            aggregate([path], manifest=manifest, expect_pins=anchor)

    def test_freeze_requires_external_task_pins_and_retained_executor(self):
        path, trial = self.generated()
        for pins in ({}, [], {'original-series': '0' * 64},
                     {'original-series': digest(FIXTURE / 'pins.json'), 'extra': '0' * 64}):
            with self.subTest(pins=pins), self.assertRaisesRegex(ValueError, 'external task pins'):
                benchmark.freeze_series([path], pins)
        pins = {'original-series': digest(FIXTURE / 'pins.json')}
        with self.assertRaisesRegex(ValueError, 'one selected attempt'):
            benchmark.freeze_series([path, path], pins)
        with self.assertRaisesRegex(ValueError, 'exactly'):
            benchmark.freeze_series([], {})
        self.native_output()
        legacy = self.evaluate()
        with self.assertRaisesRegex(ValueError, 'retained executor'):
            benchmark.freeze_series([legacy], pins)
        (trial / 'generation/execution.json').unlink()
        with self.assertRaisesRegex(ValueError, 'executor'):
            benchmark.freeze_series([path], pins)

    def test_anchored_unknown_never_passes_and_executor_tamper_is_refused(self):
        path, trial = self.generated(source='broken native schematic')
        pins = {'original-series': digest(FIXTURE / 'pins.json')}
        manifest = self.root / 'unknown-series.json'
        write_json(manifest, benchmark.freeze_series([path], pins))
        summary = aggregate([path], manifest=manifest, expect_pins=digest(manifest))
        self.assertEqual([r['unknown'] for r in summary['rows']], [1, 1])
        self.assertEqual([r['passed'] for r in summary['rows']], [0, 0])
        self.assertTrue(all(r['score'] is None for r in summary['rows']))
        (trial / 'generation/stdout.log').write_text('forged executor stream')
        with self.assertRaisesRegex(ValueError, 'executor'):
            aggregate([path])

    def test_native_success_is_deterministic_and_aggregates(self):
        self.native_output()
        first = self.evaluate()
        content = first.read_bytes()
        second = self.evaluate('2')
        self.assertEqual(first, second)
        self.assertEqual(content, second.read_bytes())
        report = read_json(first)
        self.assertTrue(report['scoring']['overall_pass'], report)
        self.assertEqual(report['generation']['execution'], 'ok')
        summary = aggregate([first])
        self.assertEqual([r['passed'] for r in summary['rows']], [1, 1])
        self.assertEqual([r['score'] for r in summary['rows']], [{'numerator': 1, 'denominator': 1}] * 2)
        with self.assertRaisesRegex(ValueError, 'one selected attempt'):
            aggregate([first, second])

    def test_failed_generation_with_passing_outputs_has_failed_overall_result(self):
        self.native_output()
        path = self.evaluate(generation_execution='failed')
        report = read_json(path)
        self.assertTrue(report['scoring']['overall_pass'])
        self.assertEqual(report['result'], {'generation_execution': 'failed', 'overall_pass': False})
        self.assertEqual([r['passed'] for r in aggregate([path])['rows']], [0, 0])

    def rewrite_report_receipts(self, path, report):
        write_json(path, report)
        receipt = read_json(self.root / 'eval-1/evaluation.json')
        receipt['report_sha256'] = digest(path)
        write_json(self.root / 'eval-1/evaluation.json', receipt)
        for index in (path.parent / 'trials').glob('*.json'):
            write_json(index, {'trial': str((self.root / 'eval-1').resolve()), **receipt})

    def test_aggregate_recomputes_scoring_after_coordinated_receipt_rewrite(self):
        self.native_output((FIXTURE / 'reference.kicad_sch').read_bytes().replace(b'"1k"', b'"2k"'))
        path = self.evaluate()
        report = read_json(path)
        self.assertFalse(report['scoring']['overall_pass'])
        report['scoring'] = score(pinned_task(FIXTURE)['rules'], [
            {'id': r['check'], 'execution': 'ok', 'outcome': 'pass'}
            for r in pinned_task(FIXTURE)['rules']])
        report['evaluation']['checks'] = []
        self.rewrite_report_receipts(path, report)
        with self.assertRaisesRegex(ValueError, 'retained evidence'):
            aggregate([path])

    def test_aggregate_refuses_damaged_sealed_evidence_and_task_snapshot(self):
        self.native_output()
        path = self.evaluate()
        check = self.root / 'eval-1/run/checks/components.json'
        original = check.read_bytes()
        doc = read_json(check)
        doc['outcome'] = 'unknown'
        write_json(check, doc)
        with self.assertRaises(ValueError):
            aggregate([path])
        check.write_bytes(original)
        self.assertEqual([r['passed'] for r in aggregate([path])['rows']], [1, 1])
        seal = self.root / 'eval-1/run/work/verification/evidence.json'
        original_seal = seal.read_bytes()
        seal.unlink()
        with self.assertRaisesRegex(ValueError, 'missing retained evidence seal'):
            aggregate([path])
        seal.write_bytes(original_seal)
        (self.root / 'eval-1/task/prompt.md').write_text('tampered')
        with self.assertRaises(ValueError):
            aggregate([path])

    def test_aggregate_refuses_changed_or_missing_trial_binding(self):
        path = self.evaluate()
        receipt = self.root / 'eval-1/evaluation.json'
        original = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, 'recorded evaluation trial'):
            aggregate([path])
        receipt.write_bytes(original)
        self.assertEqual(len(aggregate([path])['rows']), 2)
        report = read_json(path)
        report['scoring']['overall_pass'] = True
        write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'recorded evaluation trial'):
            aggregate([path])

    def test_unsafe_generator_outputs_are_recorded_and_not_copied_into_repairs(self):
        bodies = {
            'symlink': 'Path(sys.argv[2], "circuit.kicad_sch").symlink_to("/etc/hostname")',
            'hardlink': 'Path(sys.argv[2], "a").write_text("x"); '
                        'os.link(Path(sys.argv[2], "a"), Path(sys.argv[2], "circuit.kicad_sch"))',
            'fifo': 'os.mkfifo(Path(sys.argv[2], "circuit.kicad_sch"))',
        }
        for name, body in bodies.items():
            with self.subTest(kind=name):
                generator = self.root / (name + '.py')
                generator.write_text('import os, sys\nfrom pathlib import Path\n'
                                     'assert not Path("/work/previous/circuit.kicad_sch").exists()\n' +
                                     body + '\n')
                scratch = self.root / ('trial-' + name)
                paths = run_task(FIXTURE, generator, scratch, self.store, attempts=2)
                self.assertEqual(len(paths), 2)
                self.assertEqual(len(read_json(scratch / 'trial.json')['evaluations']), 2)
                for index, path in enumerate(paths, 1):
                    report = read_json(path)
                    self.assertEqual(report['generation']['execution'], 'failed')
                    self.assertEqual(report['generation']['output_error'], 'non_ordinary_output')
                    self.assertEqual(report['evaluation']['checks'], [])
                    self.assertIsNone(report['scoring']['overall_pass'])
                    self.assertIsNone(report['scoring']['score'])
                    self.assertTrue((scratch / f'evaluation-{index}/evaluation.json').is_file())
                    execution = read_json(scratch / f'attempt-{index}/execution.json')
                    self.assertEqual(execution['execution'], 'ok', execution)
                self.assertEqual([r['generation_failed'] for r in aggregate([paths[-1]])['rows']], [1, 1])

    def test_empty_erc_clean_schematic_fails_component_conformance(self):
        # Preserve the owned embedded library; remove placed symbols and labels with the shared parser.
        from agent_ee.symbol_library import parse_sexpr
        from agent_ee.benchmark import parse, replace_spans, children
        source = (FIXTURE / 'reference.kicad_sch').read_text()
        tree = parse(source)
        spans = [(node.start, node.end, '') for tag in ('symbol', 'label') for node in children(tree, tag)]
        empty = replace_spans(source, spans)
        self.assertEqual(parse_sexpr(empty.encode())[0], 'kicad_sch')
        self.native_output(empty.encode())
        report = read_json(self.evaluate())
        checks = {c['id']: c for c in report['evaluation']['checks']}
        self.assertEqual((checks['erc']['execution'], checks['erc']['outcome']), ('ok', 'pass'), report)
        self.assertEqual((checks['components']['execution'], checks['components']['outcome']), ('ok', 'defect'), report)
        self.assertFalse(report['scoring']['overall_pass'])

    def test_execution_failure_is_separate_from_check_defect(self):
        self.native_output((FIXTURE / 'reference.kicad_sch').read_bytes().replace(b'"1k"', b'"2k"'))
        report = read_json(self.evaluate(generation_execution='failed'))
        self.assertEqual(report['generation']['execution'], 'failed')
        self.assertEqual({c['id']: c['outcome'] for c in report['evaluation']['checks']}['components'], 'defect')

    def test_missing_deliverable_fails_and_result_json_claim_is_ignored(self):
        write_json(self.output / 'result.json', {'overall_pass': True})
        report = read_json(self.evaluate())
        self.assertEqual(report['generation']['missing_deliverables'], ['circuit.kicad_sch'])
        self.assertEqual([r['status'] for r in report['scoring']['rules']], ['fail', 'fail'])
        self.assertFalse(report['scoring']['overall_pass'])

    def test_removed_output_directory_still_fails_rules_and_keeps_repairs(self):
        self.output.rmdir()
        report = read_json(self.evaluate())
        self.assertFalse(report['scoring']['overall_pass'])
        self.assertEqual(report['generation']['missing_deliverables'], ['circuit.kicad_sch'])
        generator = self.root / 'generator.py'
        generator.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[2]).rmdir()\n')
        reports = run_task(FIXTURE, generator, self.root / 'trial', self.store)
        self.assertEqual(len(reports), 3)
        self.assertTrue(all(read_json(p)['scoring']['overall_pass'] is False for p in reports))

    def test_malformed_native_output_is_unknown(self):
        self.native_output(b'broken native schematic')
        path = self.evaluate()
        report = read_json(path)
        self.assertIsNone(report['scoring']['overall_pass'], report)
        self.assertIsNone(report['scoring']['score'])
        self.assertEqual([r['unknown'] for r in aggregate([path])['rows']], [1, 1])
        report['scoring'] = score(pinned_task(FIXTURE)['rules'], [
            {'id': r['check'], 'execution': 'ok', 'outcome': 'pass'}
            for r in pinned_task(FIXTURE)['rules']])
        self.rewrite_report_receipts(path, report)
        with self.assertRaisesRegex(ValueError, 'retained evidence'):
            aggregate([path])

    def test_generator_is_offline_and_cannot_read_evaluator_rules(self):
        generator = self.root / 'generator.py'
        generator.write_text('''import json, socket, sys
from pathlib import Path
assert Path(sys.argv[1]).read_text() == PUBLIC_PROMPT
assert sorted(p.name for p in Path('/candidate').iterdir()) == ['generator.py', 'prompt.md']
assert not Path('/candidate/evaluator').exists()
assert not Path('/evidence/baseline.json').exists()
assert not Path('/work/feedback.json').exists() or 'rules' not in json.loads(Path('/work/feedback.json').read_text())
try:
    socket.create_connection(('192.0.2.1', 443), timeout=.1)
except OSError:
    pass
else:
    raise AssertionError('sandbox network was available')
Path(sys.argv[2], 'result.json').write_text('{"overall_pass": true}')
'''.replace('PUBLIC_PROMPT', repr((FIXTURE / 'prompt.md').read_text())))
        reports = run_task(FIXTURE, generator, self.root / 'trial', self.store)
        self.assertEqual(len(reports), 3)
        for i, path in enumerate(reports, 1):
            self.assertEqual(read_json(path)['generation']['execution'], 'ok')
            self.assertFalse(read_json(path)['scoring']['overall_pass'])
            work = self.root / f'trial/attempt-{i}'
            self.assertTrue((work / 'output/result.json').is_file())
            self.assertEqual(read_json(work / 'execution.json')['execution'], 'ok')
            if i > 1:
                self.assertEqual(set(read_json(work / 'feedback.json')), {'checks', 'missing_deliverables'})

    def test_successful_generator_stops_after_initial_attempt(self):
        generator = self.root / 'generator.py'
        source = (FIXTURE / 'reference.kicad_sch').read_text()
        generator.write_text('import sys\nfrom pathlib import Path\n'
                             'Path(sys.argv[2], "circuit.kicad_sch").write_text(' + repr(source) + ')\n')
        paths = run_task(FIXTURE, generator, self.root / 'trial', self.store)
        self.assertEqual(len(paths), 1)
        self.assertTrue(read_json(paths[0])['scoring']['overall_pass'])
        trial = read_json(self.root / 'trial/trial.json')
        self.assertEqual(trial['generator_sha256'], digest(generator))
        self.assertFalse((self.root / 'trial/attempt-2').exists())

    def test_failed_generator_is_recorded_and_attempt_budget_is_bounded(self):
        generator = self.root / 'generator.py'
        generator.write_text('raise SystemExit(23)\n')
        report = read_json(run_task(FIXTURE, generator, self.root / 'trial', self.store, attempts=1)[0])
        self.assertEqual(report['generation']['execution'], 'failed')
        with self.assertRaisesRegex(ValueError, '1..3'):
            run_task(FIXTURE, generator, self.root / 'unused', self.store, attempts=4)


if __name__ == '__main__':
    unittest.main()
