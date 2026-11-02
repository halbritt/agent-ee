"""A design can declare its own mechanics without any code or engine change."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee.acquisition import local_routing as routing, mechanics
from agent_ee import placement_job as job, placement_problem as problem_builder
from agent_ee.storage import digest
from tests.test_placement_problem import MM, board, footprint

PACKAGES = dict(X9=dict(size_mm=[5.9, 4.5, 6], offset_mm=[0, 0]), R1=dict(size_mm=[1.7, .9, .55], offset_mm=[0, 0]))


class DeclaredMechanicsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'analysis').mkdir()
        (self.root / 'analysis/body-source.txt').write_text('body dimensions from a datasheet\n')
        self.write_model(custody='design-declared', packages=PACKAGES, access_owners=['X9'],
                         source_files=['analysis/body-source.txt'])

    def write_model(self, **model):
        (self.root / 'analysis/geometry-model.json').write_text(json.dumps(model))

    def recipe(self):
        path = self.root / 'placement-mechanics.json'
        path.write_text(json.dumps(mechanics.declaration(self.root)))
        return mechanics.load(path)

    def test_declared_model_loads_with_its_own_source_files_and_owners(self):
        recipe = self.recipe()
        self.assertEqual(recipe['declaration']['sources'], {
            'analysis/geometry-model.json': digest(self.root / 'analysis/geometry-model.json'),
            'analysis/body-source.txt': digest(self.root / 'analysis/body-source.txt')})
        self.assertEqual(mechanics.access_owners(recipe['model']), ('X9',))

    def test_changed_declared_source_refuses(self):
        self.recipe()
        (self.root / 'analysis/body-source.txt').write_text('different dimensions\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            mechanics.load(self.root / 'placement-mechanics.json')

    def test_invalid_design_declarations_refuse_before_native_work(self):
        design = dict(custody='design-declared', packages=PACKAGES, access_owners=['X9'])
        cases = {
            'unmarked model': (dict(packages=PACKAGES, access_owners=['X9']), 'unsupported acquisition model'),
            'no access owners': (dict(custody='design-declared', packages=PACKAGES), 'access_owners'),
            'owners not a list': (dict(design, access_owners='X9'), 'access owners'),
            'escaping source': (dict(design, source_files=['../outside.txt']), 'unsafe'),
            'no packages': (dict(design, packages={}), 'list its packages'),
            'short body size': (dict(design, packages=dict(X9=dict(size_mm=[1, 2], offset_mm=[0, 0]))), 'size_mm'),
            'text offset': (dict(design, packages=dict(X9=dict(size_mm=[1, 2, 3], offset_mm=['0', 0]))), 'offset_mm'),
        }
        for name, (model, message) in cases.items():
            with self.subTest(name):
                self.write_model(**model)
                with self.assertRaisesRegex(ValueError, message):
                    self.recipe()

    def test_declaration_that_omits_a_model_source_file_refuses(self):
        path = self.root / 'placement-mechanics.json'
        declared = mechanics.declaration(self.root)
        del declared['sources']['analysis/body-source.txt']
        path.write_text(json.dumps(declared))
        with self.assertRaisesRegex(ValueError, 'source_files'):
            mechanics.load(path)

    def test_declaration_outside_its_source_root_refuses(self):
        path = self.root / 'placement-mechanics.json'
        declared = mechanics.declaration(self.root)
        declared['sources']['../outside.txt'] = '0' * 64
        path.write_text(json.dumps(declared))
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            mechanics.load(path)

    def test_design_model_skips_recipe_local_loop_seeding(self):
        self.assertFalse(routing.applies(self.recipe()))

    def test_known_recipe_revision_keeps_loop_seeding_and_exact_source_custody(self):
        known = dict(schema=mechanics.SCHEMA, sources={name: '0' * 64 for name in mechanics.SOURCES})
        known['sources'].update({'analysis/geometry-model.json': mechanics.MODEL,
                                 'analysis/geometry_model.py': mechanics.CHECKER})
        self.assertTrue(routing.applies(dict(declaration=known)))
        mechanics._version(known)
        known['sources']['analysis/extra'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source custody'):
            mechanics._version(known)

    def test_acquisition_recipe_refuses_a_design_declared_model(self):
        from agent_ee.acquisition import block_source as recipe
        plan = dict(envelope='envelope.json', parts=dict(manifest='parts.json', circuit='circuit.json'),
                    placement=dict(intent='intent.json'))
        for name in ('spec.md', 'analysis/geometry_model.py', 'groups.json', 'electrical-locality.json',
                     'envelope.json', 'parts.json', 'circuit.json', 'intent.json', recipe.ENGINEERING_PATH):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'known mechanics model revision'):
            recipe.derive(self.root, plan)

    def test_repair_of_a_design_without_electrical_locality_finds_its_active_parts(self):
        from agent_ee.acquisition import placement as repair
        problem = dict(footprints=[dict(native_ref='X9', uuid='fixed', role='fixed_body'),
                                   dict(native_ref='R1', uuid='moved', role='movable')])
        diagnostic = dict(violations=[dict(kind='connector_access', ref='X9', blockers=[dict(ref='R1')])])
        self.assertEqual(repair.active_component(problem, diagnostic), ['moved'])

    def evaluate_with_r1_at(self, x, y):
        """Run the actual Python binding, problem builder and compiled engine on a design model."""
        recipe = self.recipe()
        rows = [footprint(1, 'X9', 10 * MM, 10 * MM, pads=[(0, 'GND')], locked=True),
                footprint(2, 'R1', x, y, pads=[(0, 'GND')])]
        facts = dict(board_sha256='a' * 64, footprints=[
            dict(uuid=row['uuid'], ref=row['ref'], value=row['ref'], fpid='Design:' + row['ref'], side='F',
                 locked=row['locked'], position_nm=row['position_nm'], rotation_deg=0,
                 pads=[dict(uuid=row['pads'][0]['uuid'], number='1', net='GND', position_nm=row['position_nm'],
                            rotation_deg=0, shape=0, size_nm=[100000, 100000])]) for row in rows])
        description = dict(copy.deepcopy(board()), footprints=rows)
        binding = mechanics.bind(recipe, facts)
        document = problem_builder.evaluate(mechanics.attach(description, binding, facts))
        done = subprocess.run([str(job.rust_executable())], input=json.dumps(document).encode(),
                              capture_output=True, timeout=60)
        output = json.loads(done.stdout)
        self.assertEqual((done.returncode, output['status']), (0, 'evaluated'), output)
        return output['evaluations'][0]

    def test_compiled_engine_grades_the_declared_body_and_access_column(self):
        # X9's body ends at x=12.95 mm and its access column at 13.45 mm. R1 is 1.7 mm wide.
        self.assertTrue(self.evaluate_with_r1_at(int(14.4 * MM), 10 * MM)['feasible'])
        self.assertFalse(self.evaluate_with_r1_at(int(14.05 * MM), 10 * MM)['feasible'])
        self.assertFalse(self.evaluate_with_r1_at(int(13.9 * MM), 10 * MM)['feasible'])

    def test_source_mechanics_identity_and_design_declared(self):
        from agent_ee import source_mechanics
        recipe = self.recipe()
        rows = [footprint(1, 'X9', 10 * MM, 10 * MM, pads=[(0, 'GND')], locked=True),
                footprint(2, 'R1', 14 * MM, 10 * MM, pads=[(0, 'GND')])]
        facts = dict(board_sha256='a' * 64, footprints=[
            dict(uuid=row['uuid'], ref=row['ref'], value=row['ref'], fpid='Design:' + row['ref'], side='F',
                 locked=row['locked'], position_nm=row['position_nm'], rotation_deg=0,
                 pads=[dict(uuid=row['pads'][0]['uuid'], number='1', net='GND', position_nm=row['position_nm'],
                            rotation_deg=0, shape=0, size_nm=[100000, 100000])]) for row in rows])
        binding = source_mechanics.bind(recipe, facts)
        ident = source_mechanics.identity(binding)
        self.assertIsInstance(ident, str)
        self.assertEqual(len(ident), 64)
        self.assertEqual(source_mechanics._identity(binding), ident)
        self.assertTrue(source_mechanics.is_design_declared(recipe))
        self.assertTrue(source_mechanics.is_design_declared(recipe['model']))
        self.assertFalse(source_mechanics.is_design_declared({}))


if __name__ == '__main__':
    unittest.main()
