"""Real strict-suite children with the runtime helper and filesystem policy roots."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ModuleEnvironmentConsumerTests(unittest.TestCase):
    def setUp(self):
        evidence = os.environ.get('AGENT_EE_EVIDENCE_DIR')
        if evidence:
            parent = Path(evidence) / 'ci-consumer-fixtures'
            parent.mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=parent))
        else:
            temporary = tempfile.TemporaryDirectory(prefix='agent-ee-module-env-')
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
        for name in ('scripts', 'tests', 'src', 'inputs', 'artifacts'):
            (self.root / name).mkdir()
        (self.root / 'tests/__init__.py').write_text('')
        for name in ('strict_suite.py', 'strict_selection.py', 'strict_inputs.py', 'native-input-policy.json'):
            shutil.copyfile(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        self.document = {'schema': 'agent-ee.module-env/1', 'modules': {}}
        for module, value in (('test_left', 'alpha'), ('test_right', 'beta')):
            source = self.root / 'inputs' / module
            source.write_text(value)
            parent = self.root / 'artifacts/modules' / module
            parent.mkdir(parents=True)
            self.document['modules'][module] = {
                'inputs': {'AGENT_EE_ACQUISITION_INPUTS': str(source)},
                'outputs': {'AGENT_EE_POWER_PART_ARTIFACTS': str(parent / 'captured.json')},
            }
            (self.root / 'tests' / (module + '.py')).write_text(f'''
import json, os, unittest
from pathlib import Path
class SelectedInput(unittest.TestCase):
    def test_selected_input(self):
        self.assertEqual(Path(os.environ['AGENT_EE_ACQUISITION_INPUTS']).read_text(), {value!r})
        self.assertEqual(os.environ['AGENT_EE_REQUIRE_TOOLCHAIN'], '1')
        output = Path(os.environ['AGENT_EE_POWER_PART_ARTIFACTS'])
        self.assertFalse(output.exists())
        output.write_text(json.dumps({{'value': {value!r}, 'strict': os.environ['AGENT_EE_REQUIRE_TOOLCHAIN']}}))
''')
        # The helper's public policy-root parameters keep these pure filesystem
        # checks independent of host /opt and /artifacts, with its bytes unchanged.
        (self.root / 'run.py').write_text('''
from functools import partial
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
import strict_suite, strict_inputs
validate = strict_inputs.validate
def startup(env, known):
    selected = validate(env, known, input_root=Path('inputs').resolve(), artifact_root=Path('artifacts').resolve())
    if env.get('CONSUMER_REMOVE_AFTER_VALIDATE'):
        Path(env['CONSUMER_REMOVE_AFTER_VALIDATE']).unlink()
    return selected
strict_inputs.validate = startup
strict_inputs.for_module = partial(strict_inputs.for_module,
    input_root=Path('inputs').resolve(), artifact_root=Path('artifacts').resolve())
raise SystemExit(strict_suite.main(sys.argv[1:]))
''')

    def execute(self, document=None, modules=(), **extra_env):
        sequence = len(list(self.root.glob('run-*.log'))) + 1
        manifest = self.root / f'module-env-{sequence}.json'
        manifest.write_text(json.dumps(document if document is not None else self.document))
        env = dict(os.environ, AGENT_EE_MODULE_ENV=str(manifest), AGENT_EE_REQUIRE_TOOLCHAIN='1',
                   PYTHONPATH=str(self.root), **extra_env)
        env.pop('AGENT_EE_ACQUISITION_INPUTS', None)
        self.summary = self.root / f'summary-{sequence}.md'
        result = subprocess.run([sys.executable, str(self.root / 'run.py'), '--jobs', '2',
                                 '--summary', str(self.summary), *modules],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        (self.root / f'run-{sequence}.log').write_text(result.stdout + result.stderr)
        return result

    def output(self, module):
        return self.root / 'artifacts/modules' / module / 'captured.json'

    def test_conflicting_values_are_scoped_to_their_actual_children(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('2 modules, 2 tests, no skips', result.stderr)
        self.assertEqual(json.loads(self.output('test_left').read_text()), {'value': 'alpha', 'strict': '1'})
        self.assertEqual(json.loads(self.output('test_right').read_text()), {'value': 'beta', 'strict': '1'})

    def test_global_policy_fault_in_unselected_module_refuses_before_children(self):
        for fault in ('schema', 'unknown-module', 'reserved', 'pod-input', 'visual-skip', 'traversal'):
            document = copy.deepcopy(self.document)
            if fault == 'schema':
                document['schema'] = 'unknown/1'
            elif fault == 'unknown-module':
                document['modules']['test_unknown'] = {'inputs': {}, 'outputs': {}}
            elif fault == 'reserved':
                document['modules']['test_right']['inputs']['AGENT_EE_REQUIRE_TOOLCHAIN'] = str(self.root / 'inputs/test_right')
            elif fault in ('pod-input', 'visual-skip'):
                variable = 'AGENT_EE_POD_INPUTS' if fault == 'pod-input' else 'AGENT_EE_SKIP_VISUALS'
                document['modules']['test_right']['inputs'][variable] = str(self.root / 'inputs/test_right')
            else:
                document['modules']['test_right']['inputs']['AGENT_EE_ACQUISITION_INPUTS'] = str(self.root / 'inputs/../inputs/test_right')
            with self.subTest(fault=fault):
                for mode in (('test_left',), ('--cheap-checks',)):
                    result = self.execute(document, modules=mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('module input manifest refused', result.stderr)
                    self.assertNotIn('===== ', result.stdout)
                self.assertFalse(self.output('test_left').exists())
                self.assertFalse(self.output('test_right').exists())

    def test_known_unselected_bindings_do_not_narrow_the_module_inventory(self):
        result = self.execute(modules=('test_left',))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('1 modules, 1 tests, no skips', result.stderr)
        self.assertTrue(self.output('test_left').exists())
        self.assertFalse(self.output('test_right').exists())

    def test_missing_manifest_refuses_before_any_child(self):
        env = dict(os.environ, AGENT_EE_MODULE_ENV=str(self.root / 'absent.json'),
                   AGENT_EE_REQUIRE_TOOLCHAIN='1', PYTHONPATH=str(self.root))
        result = subprocess.run([sys.executable, str(self.root / 'run.py'), '--jobs', '2'],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        (self.root / 'missing-manifest.log').write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1)
        self.assertIn('module input manifest refused', result.stderr)
        self.assertFalse(self.output('test_left').exists())
        self.assertFalse(self.output('test_right').exists())

    def test_missing_input_refuses_only_its_module_and_is_counted(self):
        (self.root / 'inputs/test_left').unlink()
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('2 modules, 1 tests, 1 problem(s)', result.stderr)
        self.assertIn('NOT EXECUTED, required input AGENT_EE_ACQUISITION_INPUTS missing: test_left', result.stderr)
        self.assertIn('AGENT_EE_ACQUISITION_INPUTS', self.summary.read_text())
        self.assertIn('1 required module setup(s) refused, unexecuted test count unknown', result.stderr)
        self.assertTrue(self.output('test_right').exists())
        self.assertFalse(self.output('test_left').exists())

    def test_declared_unavailability_refuses_even_an_existing_source_without_fallback(self):
        self.document['unavailable'] = {'test_left': {'AGENT_EE_ACQUISITION_INPUTS': 'retained inventory is incomplete'}}
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('NOT EXECUTED, required input AGENT_EE_ACQUISITION_INPUTS missing: test_left', result.stderr)
        self.assertIn('2 modules, 1 tests, 1 problem(s)', result.stderr)
        self.assertFalse(self.output('test_left').exists())
        self.assertTrue(self.output('test_right').exists())

    def test_existing_output_is_preserved_and_other_module_still_executes(self):
        self.output('test_left').write_bytes(b'preserve prior output\n')
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('required input AGENT_EE_POWER_PART_ARTIFACTS missing: test_left', result.stderr)
        self.assertEqual(self.output('test_left').read_bytes(), b'preserve prior output\n')
        self.assertTrue(self.output('test_right').exists())

    def test_input_loss_after_startup_is_reported_without_losing_other_results(self):
        result = self.execute(CONSUMER_REMOVE_AFTER_VALIDATE=str(self.root / 'inputs/test_left'))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('NOT EXECUTED, required input AGENT_EE_ACQUISITION_INPUTS missing: test_left', result.stderr)
        self.assertIn('2 modules, 1 tests, 1 problem(s)', result.stderr)
        self.assertTrue(self.output('test_right').exists())


if __name__ == '__main__':
    unittest.main()
