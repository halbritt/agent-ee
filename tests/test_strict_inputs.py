"""Per-module external input selection preserves distinct historical identities."""

import importlib.util
from functools import partial
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    'strict_inputs', Path(__file__).resolve().parents[1] / 'scripts/strict_inputs.py')
strict_inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(strict_inputs)


class ModuleInputEnvironmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-module-inputs-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.input_root = self.root / 'inputs'
        self.input_root.mkdir()
        self.artifact_root = self.root / 'artifacts'
        self.artifact_root.mkdir()
        self.historical, self.current = self.input_root / 'historical', self.input_root / 'current'
        self.historical.mkdir()
        self.current.mkdir()
        self.manifest = self.root / 'module-env.json'
        self.base = {'AGENT_EE_REQUIRE_TOOLCHAIN': '1', 'AGENT_EE_MODULE_ENV': str(self.manifest)}
        self.known = ['test_historical', 'test_current', 'test_other']
        self.select = partial(strict_inputs.for_module, base_env=self.base, known_modules=self.known,
                              input_root=self.input_root, artifact_root=self.artifact_root)
        self.validate = partial(strict_inputs.validate, self.base, self.known,
                                input_root=self.input_root, artifact_root=self.artifact_root)
        self.mapping = {'test_historical': {'inputs': {
            'AGENT_EE_ACQUISITION_INPUTS': str(self.historical)}, 'outputs': {}},
                        'test_current': {'inputs': {
                            'AGENT_EE_ACQUISITION_INPUTS': str(self.current)}, 'outputs': {}}}

    def write(self):
        self.manifest.write_text(json.dumps({'schema': 'agent-ee.module-env/1', 'modules': self.mapping}))

    def test_each_child_receives_only_its_selected_input_without_mutating_parent_environment(self):
        self.write()
        selection = self.validate()
        historical = self.select('test_historical', selection=selection)
        current = self.select('test_current', selection=selection)
        other = self.select('test_other', selection=selection)
        self.assertEqual(historical['AGENT_EE_ACQUISITION_INPUTS'], str(self.historical))
        self.assertEqual(current['AGENT_EE_ACQUISITION_INPUTS'], str(self.current))
        self.assertNotIn('AGENT_EE_ACQUISITION_INPUTS', other)
        self.assertNotIn('AGENT_EE_ACQUISITION_INPUTS', self.base)
        self.assertEqual(historical['AGENT_EE_REQUIRE_TOOLCHAIN'], '1')

    def test_missing_selected_input_and_missing_manifest_refuse_child_setup(self):
        self.write()
        self.historical.rmdir()
        selection = self.validate()
        self.assertEqual(set(selection.unavailable), {'test_historical'})
        with self.assertRaisesRegex(strict_inputs.ModuleInputUnavailable, 'AGENT_EE_ACQUISITION_INPUTS: input is missing'):
            self.select('test_historical', selection=selection)
        self.assertEqual(self.select('test_current', selection=selection)['AGENT_EE_ACQUISITION_INPUTS'],
                         str(self.current))
        self.manifest.unlink()
        with self.assertRaises(OSError):
            self.validate()

    def test_malformed_unknown_module_and_reserved_variable_are_refused(self):
        cases = [
            {'schema': 'other/1', 'modules': {}},
            {'schema': 'agent-ee.module-env/1', 'modules': {'test_typo': {'inputs': {}, 'outputs': {}}}},
            {'schema': 'agent-ee.module-env/1', 'modules': {'test_current': {
                'inputs': {'AGENT_EE_REQUIRE_TOOLCHAIN': '0'}, 'outputs': {}}}},
            {'schema': 'agent-ee.module-env/1', 'modules': {'test_current': {
                'inputs': {'AGENT_EE_ACQUISITION_INPUTS': 'relative'}, 'outputs': {}}}},
            {'schema': 'agent-ee.module-env/1', 'modules': {'test_current': {
                'inputs': [], 'outputs': {}}}},
        ]
        for document in cases:
            with self.subTest(document=document):
                self.manifest.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    self.validate()
        self.manifest.write_text('{"schema":"agent-ee.module-env/1","modules":{},"modules":{}}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.validate()

    def test_output_leaves_are_absent_and_completed_siblings_do_not_invalidate_selection(self):
        output = self.artifact_root / 'modules'
        output.mkdir()
        self.base['AGENT_EE_EVIDENCE_DIR'] = str(output)
        for module in ('test_historical', 'test_current'):
            parent = output / module
            parent.mkdir()
            self.mapping[module]['outputs'] = {'AGENT_EE_POWER_PART_ARTIFACTS': str(parent / 'parts')}
        self.write()
        selection = self.validate()
        first = self.select('test_historical', selection=selection)
        leaf = Path(first['AGENT_EE_POWER_PART_ARTIFACTS'])
        self.assertFalse(leaf.exists())
        leaf.mkdir()
        current = self.select('test_current', selection=selection)
        self.assertFalse(Path(current['AGENT_EE_POWER_PART_ARTIFACTS']).exists())
        with self.assertRaisesRegex(ValueError, 'must start absent'):
            self.select('test_historical', selection=selection)
        leaf.rmdir()
        self.mapping['test_current']['outputs']['AGENT_EE_POWER_PART_ARTIFACTS'] = str(self.root / 'outside')
        self.write()
        with self.assertRaisesRegex(ValueError, 'must stay under'):
            self.validate()

    def test_parent_traversal_and_symlink_escape_are_refused_before_any_child(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.input_root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        for value in (str(link), str(self.input_root / '..' / 'inputs' / 'historical')):
            with self.subTest(value=value):
                self.mapping['test_historical']['inputs']['AGENT_EE_ACQUISITION_INPUTS'] = value
                self.write()
                with self.assertRaisesRegex(ValueError, 'must stay under'):
                    self.validate()
        self.mapping['test_historical']['inputs']['AGENT_EE_ACQUISITION_INPUTS'] = str(self.historical)
        parent = self.artifact_root / 'modules'
        parent.mkdir()
        (parent / 'test_historical').symlink_to(outside, target_is_directory=True)
        self.mapping['test_historical']['outputs'] = {
            'AGENT_EE_POWER_PART_ARTIFACTS': str(parent / 'test_historical' / 'parts')}
        self.write()
        with self.assertRaisesRegex(ValueError, 'must stay under'):
            self.validate()

    def test_declared_source_unavailability_fails_only_its_module_even_if_target_exists(self):
        self.write()
        document = json.loads(self.manifest.read_text())
        document['unavailable'] = {'test_historical': {
            'AGENT_EE_ACQUISITION_INPUTS': 'retained historical input is absent'}}
        self.manifest.write_text(json.dumps(document))
        selection = self.validate()
        with self.assertRaisesRegex(strict_inputs.ModuleInputUnavailable, 'retained historical input is absent'):
            self.select('test_historical', selection=selection)
        self.assertEqual(self.select('test_current', selection=selection)['AGENT_EE_ACQUISITION_INPUTS'],
                         str(self.current))
        self.assertEqual(set(selection.unavailable), {'test_historical'})

    def test_historical_path_requires_a_fresh_receipt_bound_to_policy_selection_and_exact_files(self):
        # Execute the real consumer with a fixture-owned source policy, never an allow-prefix override.
        import shutil
        consumer_path = self.root / 'strict_inputs.py'
        shutil.copy2(Path(strict_inputs.__file__), consumer_path)
        policy = json.loads(Path(strict_inputs.__file__).with_name('native-input-policy.json').read_text())
        alias = self.root / 'original-film'
        probe = alias / 'model-r1/probe'
        probe.mkdir(parents=True)
        (probe/'model.sub').write_bytes(b'original model\n')
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        pins = alias / 'model-r1/input-pins.json'
        pins.write_text(json.dumps({'generated_files': {str(probe/'model.sub'): sha(probe/'model.sub')}}))
        execution = alias / 'model-r1/execution.json'
        (probe/'result.raw').write_bytes(b'original output\n')
        execution.write_text(json.dumps({'input_pins_sha256': sha(pins), 'outputs': {
            'result.raw': {'bytes': 16, 'sha256': sha(probe/'result.raw')}}}))
        policy['layouts'] = {'film-model-r1': {'at': str(alias), 'env': 'AGENT_EE_FILM_RUN',
            'modules': ['test_historical'], 'pins_sha256': sha(pins), 'execution_sha256': sha(execution)}}
        policy_file = consumer_path.with_name('native-input-policy.json')
        policy_file.write_text(json.dumps(policy))
        spec = importlib.util.spec_from_file_location('layout_consumer', consumer_path)
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        self.mapping['test_historical']['inputs'] = {'AGENT_EE_FILM_RUN': str(alias)}
        self.write()
        canonical = self.input_root/'film-run'
        shutil.copytree(alias, canonical)
        files = {p.relative_to(alias).as_posix(): sha(p) for p in alias.rglob('*') if p.is_file()}
        receipt = self.input_root/'receipt.json'
        record = {'schema': 'agent-ee.native-inputs/1', 'status': 'prepared',
            'policy_sha256': sha(policy_file), 'module_env_sha256': sha(self.manifest), 'inputs': [{
                'name': 'film-run', 'env': 'AGENT_EE_FILM_RUN', 'modules': ['test_historical'],
                'layout': 'film-model-r1', 'mount_path': '/opt/agent-ee-inputs/film-run',
                'container_path': str(alias), 'read_only': True, 'files': files}]}
        receipt.write_text(json.dumps(record))
        self.base['AGENT_EE_RUN_INPUT_RECEIPT'] = str(receipt)
        select = partial(consumer.for_module, base_env=self.base, known_modules=self.known,
                         input_root=self.input_root, artifact_root=self.artifact_root)
        self.assertEqual(select('test_historical')['AGENT_EE_FILM_RUN'], str(alias))
        self.assertNotIn('AGENT_EE_FILM_RUN', select('test_current'))
        before = pins.read_bytes()
        for fault in ('selection', 'policy', 'readonly', 'scope', 'inventory', 'receipt-path', 'alias-link'):
            with self.subTest(fault=fault):
                changed = json.loads(json.dumps(record))
                if fault == 'selection': changed['module_env_sha256'] = '0'*64
                if fault == 'policy': changed['policy_sha256'] = '0'*64
                if fault == 'readonly': changed['inputs'][0]['read_only'] = False
                if fault == 'scope': changed['inputs'][0]['modules'] = ['test_current']
                if fault == 'inventory': (probe/'model.sub').write_bytes(b'damaged\n')
                if fault == 'receipt-path': self.base['AGENT_EE_RUN_INPUT_RECEIPT'] = str(self.root/'other.json')
                if fault == 'alias-link':
                    shutil.rmtree(alias)
                    alias.symlink_to(canonical, target_is_directory=True)
                receipt.write_text(json.dumps(changed))
                with self.assertRaises(ValueError): select('test_historical')
                if fault == 'inventory': (probe/'model.sub').write_bytes(b'original model\n')
                if fault == 'receipt-path': self.base['AGENT_EE_RUN_INPUT_RECEIPT'] = str(receipt)
                if fault == 'alias-link':
                    alias.unlink()
                    shutil.copytree(canonical, alias)
        receipt.write_text(json.dumps(record))
        selection = consumer.validate(self.base, self.known, input_root=self.input_root,
                                      artifact_root=self.artifact_root)
        receipt.write_text(json.dumps({**record, 'image': 'changed after dispatch'}))
        with self.assertRaises(ValueError): select('test_historical', selection=selection)
        self.assertEqual(pins.read_bytes(), before)
        receipt.write_text(json.dumps(record))
        shutil.rmtree(alias)
        selection = consumer.validate(self.base, self.known, input_root=self.input_root,
                                      artifact_root=self.artifact_root)
        self.assertEqual(set(selection.unavailable), {'test_historical'})
        self.assertEqual(select('test_current', selection=selection)['AGENT_EE_ACQUISITION_INPUTS'],
                         str(self.current))
        shutil.copytree(canonical, alias)
        (alias/'model-r1/probe/model.sub').unlink()
        (canonical/'model-r1/probe/model.sub').unlink()
        del record['inputs'][0]['files']['model-r1/probe/model.sub']
        receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'generated file'):
            select('test_historical')


if __name__ == '__main__':
    unittest.main()
