"""Owned offline question/answer contracts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_design_dialog.

from agent_ee.baseline import compile_baseline
from agent_ee.design_dialog import capture_dialog


def encoded(value):
    return json.dumps(value).encode()


def inputs():
    idea = b'Owner idea: measure a sensor.\n'
    baseline = encoded({'schema': 'agent-ee.requirements/1', 'requirements': [
        {'id': 'R-original', 'text': 'Keep the owner wording.', 'source_question_ids': []}]})
    questions = encoded({'schema': 'agent-ee.questions/1', 'context': 'Supply decision', 'questions': [
        {'id': 'supply', 'question': 'Supply voltage?', 'options': ['3.3V', '5V'],
         'default': '3.3V', 'depends_on': [], 'requirement_id': 'R-supply'},
        {'id': 'sensor', 'question': 'Sensor interface?', 'options': ['analog', 'digital'],
         'default': 'analog', 'depends_on': ['supply'], 'requirement_id': 'R-sensor'}]})
    answers = encoded({'schema': 'agent-ee.answers/1', 'questions_sha256': hashlib.sha256(questions).hexdigest(),
        'idea_sha256': hashlib.sha256(idea).hexdigest(), 'requirements_sha256': hashlib.sha256(baseline).hexdigest(),
        'answers': {'sensor': {'value': 'digital'}, 'supply': {'use_default': True}}})
    return idea, questions, answers, baseline


class DesignDialogTests(unittest.TestCase):
    def test_capture_binds_answers_and_preserves_originals_and_ids(self):
        args = inputs()
        artifacts = capture_dialog(*args)
        report = json.loads(artifacts['dialog.json'])
        self.assertEqual(report['outcome'], 'pass')
        self.assertFalse(report['acceptance_ready'])
        derived = json.loads(artifacts['requirements.json'])['requirements']
        self.assertEqual(derived[0], json.loads(args[3])['requirements'][0])
        self.assertEqual([r['id'] for r in derived], ['R-original', 'R-supply', 'R-sensor'])
        self.assertEqual(derived[2]['source_question_ids'], ['supply', 'sensor'])
        self.assertEqual(report['answers']['supply'], {'value': '3.3V', 'defaulted': True})
        self.assertEqual(artifacts['original-requirements.json'], args[3])
        self.assertEqual(capture_dialog(*args), artifacts)

    def test_missing_malformed_and_partial_answers_never_default(self):
        idea, questions, answers, baseline = inputs()
        partial = json.loads(answers)
        del partial['answers']['supply']
        for raw in [None, b'{', b'null', b'\xff', encoded(partial)]:
            with self.subTest(raw=raw):
                artifacts = capture_dialog(idea, questions, raw, baseline)
                report = json.loads(artifacts['dialog.json'])
                self.assertEqual(report['outcome'], 'unknown')
                self.assertNotIn('requirements.json', artifacts)
                self.assertNotIn('requirements.md', artifacts)
        report = json.loads(capture_dialog(idea, questions, encoded(partial), baseline)['dialog.json'])
        self.assertEqual(report['ready_question_ids'], ['supply'])

    def test_invalid_default_duplicate_ids_cycles_and_collisions_are_rejected(self):
        idea, raw, answers, baseline = inputs()
        for change in ['default', 'duplicate', 'cycle', 'unknown', 'collision', 'duplicate_dependency', 'invalid_id']:
            questions = json.loads(raw)
            first, second = questions['questions']
            if change == 'default':
                first['default'] = '12V'
            elif change == 'duplicate':
                second['id'] = first['id']
            elif change == 'cycle':
                first['depends_on'] = ['sensor']
            elif change == 'unknown':
                first['depends_on'] = ['missing']
            elif change == 'collision':
                first['requirement_id'] = 'R-original'
            elif change == 'duplicate_dependency':
                second['depends_on'] = ['supply', 'supply']
            else:
                first['id'] = '../path'
            with self.subTest(change=change), self.assertRaises(ValueError):
                capture_dialog(idea, encoded(questions), answers, baseline)

    def test_deeply_nested_answers_are_recorded_unknown_without_derivation(self):
        idea, questions, answers, baseline = inputs()
        nested_array = b'[' * 100000 + b'0' + b']' * 100000
        nested_object = b'{"nested":' * 100000 + b'0' + b'}' * 100000
        for raw in [nested_array, nested_object,
                    answers.replace(b'"digital"', nested_array)]:
            with self.subTest(size=len(raw)):
                artifacts = capture_dialog(idea, questions, raw, baseline)
                report = json.loads(artifacts['dialog.json'])
                self.assertEqual(report['outcome'], 'unknown')
                self.assertFalse(report['acceptance_ready'])
                self.assertEqual(report['findings'], [{'code': 'answers_too_deep',
                    'reason': 'answers JSON nesting exceeds decoder recursion limit'}])
                self.assertEqual(report['answers'], {})
                self.assertEqual(report['ready_question_ids'], ['supply'])
                self.assertEqual(report['answers_sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifacts['answers.json'], raw)
                self.assertEqual(artifacts['original-requirements.json'], baseline)
                self.assertNotIn('requirements.json', artifacts)
                self.assertNotIn('requirements.md', artifacts)
                self.assertEqual(capture_dialog(idea, questions, raw, baseline), artifacts)

    def test_nested_answer_within_decoder_limit_is_invalid_without_flattening(self):
        idea, questions, answers, baseline = inputs()
        raw = answers.replace(b'"digital"', b'[' * 20 + b'"digital"' + b']' * 20)
        artifacts = capture_dialog(idea, questions, raw, baseline)
        report = json.loads(artifacts['dialog.json'])
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(report['findings'], [{'code': 'answer_invalid', 'question_id': 'sensor'}])
        self.assertNotIn('sensor', report['answers'])
        self.assertNotIn('requirements.json', artifacts)

    def test_stale_hash_extra_answer_and_implicit_defaults_block_capture(self):
        idea, questions, raw, baseline = inputs()
        for change in ['stale', 'extra', 'empty', 'false', 'wrong_value', 'null', 'duplicate_key']:
            answers = json.loads(raw)
            if change == 'stale':
                answers['idea_sha256'] = '0' * 64
            elif change == 'extra':
                answers['answers']['other'] = {'value': '5V'}
            elif change == 'empty':
                answers['answers']['supply'] = {}
            elif change == 'false':
                answers['answers']['supply'] = {'use_default': False}
            elif change == 'wrong_value':
                answers['answers']['supply'] = {'value': '12V'}
            elif change == 'null':
                answers['answers']['supply'] = None
            raw_answer = encoded(answers)
            if change == 'duplicate_key':
                raw_answer = raw_answer[:-1] + b',"answers":{}}'
            with self.subTest(change=change):
                artifacts = capture_dialog(idea, questions, raw_answer, baseline)
                self.assertEqual(json.loads(artifacts['dialog.json'])['outcome'], 'unknown')
                self.assertNotIn('requirements.json', artifacts)

    def test_independent_questions_are_batched_and_topological_ids_are_stable(self):
        idea, raw, answers_raw, baseline = inputs()
        questions = json.loads(raw)
        questions['questions'].append({'id': 'housing', 'question': 'Enclosure?', 'options': ['open'],
            'default': 'open', 'depends_on': [], 'requirement_id': 'R-housing'})
        answers = json.loads(answers_raw)
        answers['answers']['housing'] = {'value': 'open'}
        first_raw = encoded(questions)
        answers['questions_sha256'] = hashlib.sha256(first_raw).hexdigest()
        first = capture_dialog(idea, first_raw, encoded(answers), baseline)
        report = json.loads(first['dialog.json'])
        self.assertEqual(report['question_batches'], [['housing', 'supply'], ['sensor']])
        questions['questions'].reverse()
        second_raw = encoded(questions)
        answers['questions_sha256'] = hashlib.sha256(second_raw).hexdigest()
        second = capture_dialog(idea, second_raw, encoded(answers), baseline)
        self.assertEqual(first['requirements.json'], second['requirements.json'])
        self.assertEqual(first['requirements.md'], second['requirements.md'])

    def test_changed_original_requirement_bytes_invalidate_old_answers(self):
        idea, questions, answers, baseline = inputs()
        artifacts = capture_dialog(idea, questions, answers, baseline + b'\n')
        self.assertEqual(json.loads(artifacts['dialog.json'])['outcome'], 'unknown')
        self.assertEqual(artifacts['original-requirements.json'], baseline + b'\n')

    def test_cli_missing_answers_is_unknown_with_no_derived_output(self):
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            idea, questions, answers, baseline = inputs()
            for name, raw in [('idea', idea), ('questions', questions), ('baseline', baseline)]:
                (root / name).write_bytes(raw)
            argv = [sys.executable, '-m', 'agent_ee.design_dialog', '--idea', str(root / 'idea'),
                '--questions', str(root / 'questions'), '--requirements', str(root / 'baseline'),
                '--answers', str(root / 'missing'), '--out', str(root / 'output')]
            result = subprocess.run(argv, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse((root / 'output/requirements.json').exists())
            (root / 'missing').write_bytes(answers)
            result = subprocess.run(argv[:-1] + [str(root / 'ready')], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'ready/requirements.json').exists())

    def test_cli_deeply_nested_answers_writes_unknown_and_preserves_inputs(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            idea, questions, answers, baseline = inputs()
            raw = answers.replace(b'"digital"', b'[' * 100000 + b'0' + b']' * 100000)
            for name, content in [('idea', idea), ('questions', questions),
                                  ('baseline', baseline), ('answers', raw)]:
                (root / name).write_bytes(content)
            result = subprocess.run([sys.executable, '-m', 'agent_ee.design_dialog',
                '--idea', str(root / 'idea'), '--questions', str(root / 'questions'),
                '--requirements', str(root / 'baseline'), '--answers', str(root / 'answers'),
                '--out', str(root / 'output')], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, b'')
            output = root / 'output'
            report = json.loads((output / 'dialog.json').read_bytes())
            self.assertEqual(report['outcome'], 'unknown')
            self.assertEqual(report['findings'], [{'code': 'answers_too_deep',
                'reason': 'answers JSON nesting exceeds decoder recursion limit'}])
            self.assertEqual(report['answers'], {})
            self.assertFalse(report['acceptance_ready'])
            self.assertEqual(report['answers_sha256'], hashlib.sha256(raw).hexdigest())
            for name, content in [('idea.md', idea), ('questions.json', questions),
                                  ('original-requirements.json', baseline), ('answers.json', raw)]:
                self.assertEqual((output / name).read_bytes(), content)
            self.assertFalse((output / 'requirements.json').exists())
            self.assertFalse((output / 'requirements.md').exists())

    def test_ready_frontier_requires_all_ancestors_answered(self):
        idea, raw, ar, baseline = inputs()
        questions = json.loads(raw)
        questions['questions'].append({'id': 'gain', 'question': 'Gain?', 'options': ['one'],
            'default': 'one', 'depends_on': ['sensor'], 'requirement_id': 'R-gain'})
        qr = encoded(questions)
        answers = json.loads(ar)
        answers['questions_sha256'] = hashlib.sha256(qr).hexdigest()
        del answers['answers']['supply']
        report = json.loads(capture_dialog(idea, qr, encoded(answers), baseline)['dialog.json'])
        self.assertEqual(report['ready_question_ids'], ['supply'])
        self.assertEqual(report['outcome'], 'unknown')

    def test_imported_intent_original_text_and_requirement_id_survive_dialog(self):
        from agent_ee.design_intent import import_record
        from test_design_intent import record
        idea, qr, ar, _ = inputs()
        baseline = import_record(record())['requirements.json']
        answers = json.loads(ar)
        answers['requirements_sha256'] = hashlib.sha256(baseline).hexdigest()
        artifacts = capture_dialog(idea, qr, encoded(answers), baseline)
        original = json.loads(baseline)['requirements'][0]
        derived = json.loads(artifacts['requirements.json'])['requirements']
        self.assertEqual(derived[0], original)
        self.assertEqual(artifacts['original-requirements.json'], baseline)

    def test_numeric_answer_and_default_tokens_are_not_options(self):
        idea, qr, ar, baseline = inputs()
        answers = ar.replace(b'{"value": "digital"}', b'{"value": 1.0000000000000001}')
        self.assertNotEqual(answers, ar)
        report = json.loads(capture_dialog(idea, qr, answers, baseline)['dialog.json'])
        self.assertEqual(report['outcome'], 'unknown')
        self.assertIn({'code': 'answer_invalid', 'question_id': 'sensor'}, report['findings'])
        questions = qr.replace(b'"default": "3.3V"', b'"default": 3.3')
        self.assertNotEqual(questions, qr)
        with self.assertRaises(ValueError):
            capture_dialog(idea, questions, ar, baseline)

    def test_derived_requirements_feed_scope3_only_with_executable_coverage(self):
        artifacts = capture_dialog(*inputs())
        derived = json.loads(artifacts['requirements.json'])['requirements']
        files = {'design.sch': 'hash', 'design.pcb': 'hash', 'target': 'hash', 'ref': 'hash',
                 'requirements/requirements.json': hashlib.sha256(artifacts['requirements.json']).hexdigest()}
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'design.sch', 'board': 'design.pcb',
            'checks': [{'id': 'net', 'kind': 'netlist'},
                       {'id': 'width', 'kind': 'geometry', 'params': {'min_track_width_mm': 0.2}}],
            'baseline': {'requirements': [
                {'id': r['id'], 'intent': 'acceptance', 'statement': r['text'],
                 'source': {'path': 'requirements/requirements.json', 'locator': r['id']},
                 'checks': ['net', 'width']} for r in derived],
                'targets': ['target'], 'reference_architectures': ['ref']}}
        baseline = compile_baseline(copy.deepcopy(scope), files)
        self.assertEqual(set(baseline['coverage']['requirements']), {'R-original', 'R-supply', 'R-sensor'})
        self.assertEqual(baseline['coverage']['requirements']['R-sensor']['acceptance_checks'], ['width'])
        changed = dict(files, **{'requirements/requirements.json': '0' * 64})
        self.assertNotEqual(compile_baseline(copy.deepcopy(scope), changed)['baseline_id'], baseline['baseline_id'])
        # A derived acceptance requirement mapped only to an export check is refused.
        scope['baseline']['requirements'][2]['checks'] = ['net']
        with self.assertRaisesRegex(ValueError, 'R-sensor has no executable bound'):
            compile_baseline(scope, files)
