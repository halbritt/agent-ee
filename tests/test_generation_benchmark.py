"""Frozen generation contracts, native grading, and sandbox isolation."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee.generation_benchmark import (aggregate, average_scores, evaluate, pinned_task, publish_report,
                                           run_task, score, split_tasks, tree_files)
from agent_ee.storage import digest, read_json, write_json
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
        report = read_json(self.evaluate())
        self.assertIsNone(report['scoring']['overall_pass'], report)
        self.assertIsNone(report['scoring']['score'])

    def test_generator_is_offline_and_cannot_read_evaluator_rules(self):
        generator = self.root / 'generator.py'
        generator.write_text('''import json, socket, sys
from pathlib import Path
assert Path(sys.argv[1]).is_file()
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
''')
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
