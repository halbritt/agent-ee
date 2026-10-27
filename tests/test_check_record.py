"""Durable check records and isolated composition, without native tools."""

import copy
import ast
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.check_record import CheckRecord
from agent_ee.checks import CHECK_FUNCS, CheckError, Ctx


class CheckRecordTests(unittest.TestCase):
    def test_no_other_production_literal_defines_the_check_record_shape(self):
        import agent_ee
        keys = {'id', 'kind', 'candidate_id', 'execution', 'outcome', 'summary',
                'evidence', 'details', 'limitations'}
        for path in Path(agent_ee.__file__).parent.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Dict):
                    literal = {key.value for key in node.keys if isinstance(key, ast.Constant)}
                    self.assertFalse(keys <= literal, f'{path.name}:{node.lineno}')

    def test_constructor_keeps_the_durable_shape_and_fresh_mutable_fields(self):
        record = CheckRecord('fab', 'fab_package', 'candidate')
        expected = {'id': 'fab', 'kind': 'fab_package', 'candidate_id': 'candidate',
                    'execution': 'not_run', 'outcome': 'unknown', 'summary': '',
                    'evidence': [], 'details': {}, 'limitations': []}
        self.assertEqual(json.loads(json.dumps(record)), expected)
        record['details']['partial'] = True
        record['evidence'].append('diagnostic')
        self.assertEqual(CheckRecord('fab', 'fab_package', 'candidate'), expected)

    def test_limitation_scope_is_structural_and_never_changes_record_json(self):
        record = CheckRecord('fab', 'fab_export', 'candidate')
        # Equal text with different scope must not be removed by text matching.
        text = 'pass means the exports exist; a retained unrelated observation'
        record['limitations'].append(text)
        record.add_limitation(text, scope='export_only')
        record['limitations'].append('other coverage boundary')
        self.assertEqual(record.limitations_except('export_only'),
                         [text, 'other coverage boundary'])
        self.assertEqual(json.loads(json.dumps(record))['limitations'],
                         [text, text, 'other coverage boundary'])


class SubcheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.spec = {'id': 'fab', 'kind': 'fab_package',
                     'params': {'netlist_check': 'net', 'nested': {'outer': True}}}
        manifest = {'candidate_id': 'candidate', 'scope': {'checks': [self.spec]}}
        self.ctx = Ctx(Path(tmp.name), manifest, self.spec, time.monotonic() + 30,
                       context={'fab_context': {'receipt': 'bound'}, 'unneeded': 'hidden'})
        self.ctx.result['details']['outer'] = {'retained': True}

    def test_registered_handler_returns_an_isolated_record_with_declared_context(self):
        params, record = copy.deepcopy(self.ctx.params), copy.deepcopy(self.ctx.result)

        def child(ctx):
            self.assertIsNot(ctx, self.ctx)
            self.assertEqual(ctx.work, self.ctx.work)
            self.assertIs(ctx.spec, self.spec)
            self.assertEqual(ctx.deadline, self.ctx.deadline)
            self.assertEqual(ctx.context, {'fab_context': {'receipt': 'bound'}})
            ctx.params['nested']['outer'] = False
            ctx.result['details']['child'] = True
            ctx.result.update(execution='ok', outcome='pass', summary='returned')

        with patch.dict(CHECK_FUNCS, {'fab_export': child}):
            result = self.ctx.invoke('fab_export', self.ctx.params)
        self.assertEqual(result['summary'], 'returned')
        self.assertEqual(result['details'], {'child': True})
        self.assertEqual(self.ctx.params, params)
        self.assertEqual(self.ctx.result, record)

    def test_refusal_returns_partial_evidence_without_mutating_parent(self):
        before = copy.deepcopy(self.ctx.result)

        def child(ctx):
            ctx.result['evidence'].append('work/fab/partial.json')
            ctx.result['details']['reason'] = 'partial export'
            raise CheckError('malformed', 'sealed export refused')

        with patch.dict(CHECK_FUNCS, {'fab_export': child}):
            result = self.ctx.invoke('fab_export', {})
        self.assertEqual((result['execution'], result['outcome'], result['summary']),
                         ('malformed', 'unknown', 'sealed export refused'))
        self.assertEqual(result['evidence'], ['work/fab/partial.json'])
        self.assertEqual(result['details']['reason'], 'partial export')
        self.assertEqual(self.ctx.result, before)

    def test_unexpected_errors_and_cancellation_propagate(self):
        for error in (RuntimeError('bug'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch.dict(CHECK_FUNCS, {'fab_export': lambda ctx: (_ for _ in ()).throw(error)}):
                    with self.assertRaises(type(error)):
                        self.ctx.invoke('fab_export', {})

    def test_consistency_gate_returns_partial_result_without_swapping_parent(self):
        from agent_ee import fab_consistency
        before = copy.deepcopy(self.ctx.result)
        original = self.ctx.result

        def gate(child, *args):
            self.assertIs(self.ctx.result, original)
            self.assertIsNot(child.result, original)
            child.result['details']['gate'] = 'partial'
            child.result['evidence'].append('work/fab/fab-consistency.json')
            raise CheckError('unsupported', 'model coverage absent')

        with patch.object(fab_consistency, '_gate', gate):
            result = fab_consistency.gate(self.ctx, 'fab', self.spec, {}, self.ctx.work / 'fab')
        self.assertEqual(result['execution'], 'unsupported')
        self.assertEqual(result['summary'], 'model coverage absent')
        self.assertEqual(result['details'], {'gate': 'partial'})
        self.assertEqual(result['location'], '')
        self.assertEqual(self.ctx.result, before)


if __name__ == '__main__':
    unittest.main()
