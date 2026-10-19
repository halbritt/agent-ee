"""The committed pod composition record stays bound to the repository spec and never claims acceptance."""
from pathlib import Path
import json
import tempfile
import unittest

from agent_ee import evidence_index, pod_driver
from agent_ee.storage import digest

ROOT = Path(__file__).resolve().parents[1]
POD = ROOT / 'fixtures/telemetry-pod'


class PodCompositionRecordTests(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((POD / 'composition-recipe.json').read_bytes())
        self.spec = json.loads((POD / 'spec-record.json').read_bytes())

    def test_record_is_incomplete_and_bound_to_the_repository_spec(self):
        self.assertEqual(self.recipe['schema'], 'agent-ee.pod-composition-recipe/1')
        self.assertEqual((self.recipe['status'], self.recipe['done']), ('incomplete', False))
        self.assertEqual(self.recipe['dry_run']['status'], 'not-run')
        self.assertTrue(self.recipe['refusal'].startswith('ValueError: pod stages must be'))
        files = self.recipe['sources']['inputs']['key_files']
        self.assertEqual(files['spec.md'], digest(POD / 'spec.md'))
        self.assertEqual(files['spec-record.json'], digest(POD / 'spec-record.json'))
        self.assertEqual(self.recipe['gates']['stage_order']['required'], pod_driver.ORDER)

    def test_every_spec_requirement_is_accounted_and_obligations_stay_open(self):
        rows = {r['id']: r for r in self.recipe['requirements']}
        self.assertEqual(set(rows), {r['id'] for r in self.spec['requirements']} |
                         {p['id'] for p in self.spec['prose']})
        checked = {i for i, r in rows.items() if r['disposition'] == 'check'}
        self.assertEqual(checked, {'POD-AC-3'})
        for label, ids in self.recipe['open_obligations'].items():
            for rid in ids:
                with self.subTest(label=label, rid=rid):
                    self.assertIn(rows[rid]['disposition'], ('limitation', 'bench'))
                    self.assertTrue(rows[rid]['scope_limitation_retained'])
        self.assertEqual(self.recipe['open_obligations']['temperature (AGENTEE-209)'], ['POD-ME-3'])
        for rid in ('POD-AC-8', 'POD-AC-9'):
            self.assertEqual(rows[rid]['disposition'], 'bench')

    def test_criterion_map_passes_the_existing_reader_and_leaves_unmapped_criteria_empty(self):
        mapping = self.recipe['criteria']['evidence_index_map']
        baseline = {'requirements': [{'id': i} for i in self.recipe['criteria']['baseline_requirements']]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inputs/requirements/pod-criteria.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(mapping))
            baseline['files'] = {'requirements/pod-criteria.json': digest(path)}
            self.assertEqual(evidence_index.criteria_map(Path(tmp), 'requirements/pod-criteria.json',
                                                         baseline)[0], mapping)
        self.assertEqual({k for k, v in mapping.items() if v}, {'3'})


if __name__ == '__main__':
    unittest.main()
