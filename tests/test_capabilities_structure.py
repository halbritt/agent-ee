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
    def test_runner_contains_no_specific_kinds_or_gate_names(self):
        from agent_ee import checks
        tree = ast.parse(Path(checks.__file__).read_text())
        runner = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'run_checks')
        forbidden = {'placement_defects', 'placement_defect_gate', 'before_check',
                     'before_fab_check', 'validate_gate_findings', 'fab_context', 'stock_context'}
        names = {node.id for node in ast.walk(runner) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(runner) if isinstance(node, ast.Attribute)}
        literals = {node.value for node in ast.walk(runner)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertFalse(names & forbidden)
        self.assertFalse(literals & (set(KINDS) | forbidden))

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

    def test_invalid_check_function_replacements_preserve_both_views(self):
        original = registry['geometry']
        for invalid in (None, False, 1, 'not callable'):
            with self.subTest(invalid=invalid):
                try:
                    with self.assertRaisesRegex(TypeError, 'callable'):
                        CHECK_FUNCS['geometry'] = invalid
                    self.assertEqual(registry['geometry'], original)
                    self.assertEqual(KINDS, CHECK_FUNCS.keys())
                finally:
                    CHECK_FUNCS['geometry'] = original.check

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
        for entry in ('capabilities', 'candidate', 'measurements', 'checks', 'datasheet_facts',
                      'fab_stage', 'run_record'):
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, '-c',
                    f'import agent_ee.{entry}; '
                    'from agent_ee.capabilities import KINDS; '
                    'from agent_ee.checks import CHECK_FUNCS; '
                    'assert KINDS == CHECK_FUNCS.keys(); assert KINDS'],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_kind_declares_its_prerequisite_and_only_the_context_it_needs(self):
        script = """
import tempfile
import time
from pathlib import Path
from agent_ee.check_registry import register
from agent_ee.checks import run_checks
from agent_ee.storage import write_json

calls = []
context = {'receipt': 'sealed-example'}

def prerequisite(ctx, prior):
    assert prior == []
    assert ctx.context == {'example_context': context}
    calls.append('prerequisite')
    ctx.result.update(execution='not_run', outcome='unknown', summary='example prerequisite pending')
    return False

def check(ctx):
    raise AssertionError('a refused prerequisite must prevent the handler')

register('example-prerequisite', lambda ctx: None, check,
         prerequisites=(prerequisite,), context_keys=('example_context',))
with tempfile.TemporaryDirectory() as tmp:
    run = Path(tmp)
    write_json(run / 'candidate.json', {'candidate_id': 'example-candidate', 'files': {},
        'scope': {'checks': [{'id': 'example', 'kind': 'example-prerequisite'}]}})
    results = run_checks(run, time.monotonic() + 60,
                         example_context=context, fab_context={'unused': True})
    assert calls == ['prerequisite']
    assert len(results) == 1
    assert results[0]['execution'] == 'not_run'
    assert results[0]['outcome'] == 'unknown'
    assert results[0]['summary'] == 'example prerequisite pending'
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_registration_api_keeps_stage_based_placement_admission(self):
        script = """
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from agent_ee import placement_defects
from agent_ee.check_registry import register
from agent_ee.checks import run_checks
from agent_ee.storage import write_json

def check(ctx):
    raise AssertionError('open placement carry must withhold a verification handler')

register('legacy-extension', lambda ctx: None, check)
defect = {'uuid': 'fixture-id', 'reference': 'R1', 'field': 'Reference',
          'source_receipt': {'receipt': 'fixture-receipt'}}
with tempfile.TemporaryDirectory() as tmp, patch.object(
        placement_defects, 'open_defects', return_value=[defect]):
    run = Path(tmp)
    write_json(run / 'candidate.json', {'candidate_id': 'example-candidate', 'files': {},
        'scope': {'checks': [{'id': 'example', 'kind': 'legacy-extension', 'stage': 'verification'}]}})
    record = run_checks(run, time.monotonic() + 60)[0]
    assert (record['execution'], record['outcome']) == ('ok', 'defect')
    assert record['details']['reason'] == 'open_placement_defects'
    assert record['summary'] == 'gate refused: 1 carried open placement silkscreen defect(s)'
    assert 'work/example/placement-defects.json' in record['evidence']
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
