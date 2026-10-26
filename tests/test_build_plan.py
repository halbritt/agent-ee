"""Build source admission refusals belong to the plan owner."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_ee import build_plan
from agent_ee.storage import write_json


def declaration():
    return dict(schema=build_plan.SCHEMA, source='inputs', envelope='envelope.json',
        requirements=dict(map='map.json', record='record.json', spec='spec.md'),
        parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
        placement=dict(intent='intent.json', groups='groups.json', budget_s=10,
                       search={}, router={}))


class BuildPlanTests(unittest.TestCase):
    def test_output_inside_source_bundle_is_refused_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'inputs').mkdir()
            write_json(root / 'plan.json', declaration())
            for out in (root / 'inputs', root / 'inputs/run'):
                with self.subTest(out=out), self.assertRaises(ValueError) as error:
                    build_plan.admit_source(root / 'plan.json', out)
                self.assertEqual(str(error.exception), 'build output must be outside the source bundle')
            self.assertFalse((root / 'inputs/run').exists())

    def test_judgments_must_not_overlap_source_or_generated_namespaces(self):
        plan = declaration()
        plan['schema'] = build_plan.TYPED_SCHEMA
        plan['requirements']['obligations'] = 'obligations.json'
        for directory in ('.', 'build-generated', 'build-generated/subdir', 'requirements-map',
                          'requirements-map/subdir'):
            value = copy.deepcopy(plan)
            value['judgments'] = directory
            with self.subTest(directory=directory), self.assertRaises(ValueError) as error:
                build_plan.validate_plan(value)
            self.assertEqual(str(error.exception),
                             'judgments directory overlaps the source root or generated namespace')

    def test_inventory_prunes_late_judgments_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'inputs').mkdir()
            (root / 'inputs/input.txt').write_text('captured')
            (root / 'inputs/judgments').mkdir()
            (root / 'inputs/judgments/late.txt').write_text('late')
            plan = declaration()
            plan.update(schema=build_plan.TYPED_SCHEMA, judgments='judgments')
            plan['requirements']['obligations'] = 'obligations.json'
            write_json(root / 'plan.json', plan)
            admitted = build_plan.admit_source(root / 'plan.json', root / 'run')
            self.assertEqual(set(admitted[-1]), {'input.txt'})
            (root / 'inputs/link').symlink_to('input.txt')
            with self.assertRaisesRegex(ValueError, 'build inputs contain links or special files'):
                build_plan.admit_source(root / 'plan.json', root / 'run')
