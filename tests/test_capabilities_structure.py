"""Registry completeness and capability projection behavior."""

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.capabilities import KINDS, describe_check
from agent_ee.check_registry import Registry, registry
from agent_ee.checks import CHECK_FUNCS

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'agent_ee' / 'capabilities.py'


class DescribeCheckStructureTest(unittest.TestCase):
    def test_every_kind_has_one_capability_and_check_in_its_module(self):
        self.assertEqual(KINDS, CHECK_FUNCS.keys())
        self.assertTrue(KINDS)
        for kind, record in registry.items():
            with self.subTest(kind=kind):
                self.assertEqual(record.kind, kind)
                self.assertTrue(callable(record.capability))
                self.assertTrue(callable(record.check))
                self.assertEqual(record.capability.__module__, record.check.__module__)

    def test_duplicate_registration_is_rejected_without_replacing_the_first(self):
        isolated = Registry()
        capability, check = lambda ctx: None, lambda ctx: None
        first = isolated.register('example', capability, check)
        with self.assertRaisesRegex(ValueError, 'already registered: example'):
            isolated.register('example', lambda ctx: None, lambda ctx: None)
        self.assertIs(isolated['example'], first)
        for cap, handler in ((None, check), (capability, None)):
            with self.assertRaises(TypeError):
                isolated.register('invalid', cap, handler)

    def test_describer_has_no_kind_specific_branches(self):
        tree = ast.parse(SOURCE.read_text())
        describe = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'describe_check')
        # A kind lookup/membership check is generic; comparisons to particular
        # string literals or tuples of kinds would recreate the old dispatch.
        for node in ast.walk(describe):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == 'kind':
                self.assertFalse(any(isinstance(other, (ast.Constant, ast.Tuple, ast.List))
                                     for other in node.comparators), ast.unparse(node))

    def test_check_function_patches_restore_without_losing_capabilities(self):
        original = registry['geometry']
        replacement = lambda ctx: None
        with patch.dict(CHECK_FUNCS, {'geometry': replacement}):
            self.assertIs(CHECK_FUNCS['geometry'], replacement)
            self.assertIs(registry['geometry'].check, replacement)
            self.assertIs(registry['geometry'].capability, original.capability)
            spec = {'id': 'width', 'kind': 'geometry', 'params': {'min_track_width_mm': .2}}
            record = describe_check(spec, check_specs=[spec], scope={'board': 'board.kicad_pcb'},
                                    files={'board.kicad_pcb': 'hash'})
            self.assertEqual(record['predicates'], ['geometry:straight-width/1'])
        self.assertEqual(registry['geometry'], original)
        self.assertEqual(KINDS, CHECK_FUNCS.keys())

    def test_new_kind_appears_in_both_views_and_the_generic_describer(self):
        script = """
from agent_ee.capabilities import KINDS, describe_check
from agent_ee.check_registry import register
from agent_ee.checks import CHECK_FUNCS

def capability(ctx):
    ctx.apply({'immutable_inputs': ['policy.json'], 'predicates': ['example:bound/1']})

def check(ctx):
    ctx.result.update(execution='ok', outcome='pass')

register('example', capability, check)
assert 'example' in KINDS
assert CHECK_FUNCS['example'] is check
spec = {'id': 'new', 'kind': 'example'}
record = describe_check(spec, check_specs=[spec], scope={}, files={'policy.json': 'hash'})
assert record['status'] == 'supported'
assert record['can_evaluate_bound']
assert record['predicates'] == ['example:bound/1']
assert record['immutable_inputs'] == ['policy.json']
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registry_loads_for_each_existing_import_entry_point(self):
        for entry in ('capabilities', 'candidate', 'measurements', 'checks', 'datasheet_facts'):
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, '-c',
                    f'import agent_ee.{entry}; '
                    'from agent_ee.capabilities import KINDS; '
                    'from agent_ee.checks import CHECK_FUNCS; '
                    'assert KINDS == CHECK_FUNCS.keys(); assert KINDS'],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
