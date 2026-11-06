"""Per-module external input selection preserves distinct historical identities."""

import importlib.util
from functools import partial
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


if __name__ == '__main__':
    unittest.main()
