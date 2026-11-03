"""Tests for strict schema generation, schema-directed unstrict normalization and native_call options."""
import json
import os
import unittest
from unittest.mock import patch

from agent_ee.harnesses import strict_schema, unstrict_value
from agent_ee.reviewers import DECISIONS_SCHEMA
from agent_ee.datasheet_footprint import EXTRACTION_SCHEMA


class StrictSchemaTests(unittest.TestCase):
    def test_basic_object_properties_made_strict(self):
        schema = {
            'type': 'object',
            'properties': {
                'req': {'type': 'string'},
                'opt': {'type': 'integer'}
            },
            'required': ['req']
        }
        strict = strict_schema(schema)
        self.assertFalse(strict['additionalProperties'])
        self.assertEqual(set(strict['required']), {'req', 'opt'})
        self.assertEqual(strict['properties']['req'], {'type': 'string'})
        self.assertEqual(strict['properties']['opt'], {
            'anyOf': [{'type': 'integer'}, {'type': 'null'}]
        })
        # Original schema unchanged
        self.assertNotIn('additionalProperties', schema)
        self.assertEqual(schema['required'], ['req'])

    def test_defs_and_definitions_descended_recursively(self):
        schema = {
            'type': 'object',
            '$defs': {
                'sub_type': {
                    'type': 'object',
                    'properties': {
                        'x': {'type': 'number'},
                        'y': {'type': 'number'}
                    },
                    'required': ['x']
                }
            },
            'definitions': {
                'legacy_sub': {
                    'type': 'object',
                    'properties': {
                        'z': {'type': 'string'}
                    }
                }
            },
            'properties': {
                'item': {'$ref': '#/$defs/sub_type'}
            },
            'required': ['item']
        }
        strict = strict_schema(schema)
        sub = strict['$defs']['sub_type']
        self.assertFalse(sub['additionalProperties'])
        self.assertEqual(set(sub['required']), {'x', 'y'})
        self.assertEqual(sub['properties']['x'], {'type': 'number'})
        self.assertEqual(sub['properties']['y'], {'anyOf': [{'type': 'number'}, {'type': 'null'}]})

        legacy = strict['definitions']['legacy_sub']
        self.assertFalse(legacy['additionalProperties'])
        self.assertEqual(legacy['required'], ['z'])
        self.assertEqual(legacy['properties']['z'], {'anyOf': [{'type': 'string'}, {'type': 'null'}]})

    def test_unions_any_of_one_of_all_of_descended(self):
        schema = {
            'type': 'object',
            'properties': {
                'variant': {
                    'anyOf': [
                        {
                            'type': 'object',
                            'properties': {'kind': {'const': 'a'}, 'a_val': {'type': 'string'}},
                            'required': ['kind']
                        },
                        {
                            'type': 'object',
                            'properties': {'kind': {'const': 'b'}, 'b_val': {'type': 'integer'}},
                            'required': ['kind']
                        }
                    ]
                },
                'choice': {
                    'oneOf': [
                        {
                            'type': 'object',
                            'properties': {'c': {'type': 'boolean'}}
                        }
                    ]
                },
                'combined': {
                    'allOf': [
                        {
                            'type': 'object',
                            'properties': {'base': {'type': 'string'}},
                            'required': ['base']
                        }
                    ]
                }
            },
            'required': ['variant', 'choice', 'combined']
        }
        strict = strict_schema(schema)
        branch_a = strict['properties']['variant']['anyOf'][0]
        self.assertFalse(branch_a['additionalProperties'])
        self.assertEqual(set(branch_a['required']), {'kind', 'a_val'})
        self.assertEqual(branch_a['properties']['a_val'], {'anyOf': [{'type': 'string'}, {'type': 'null'}]})

        choice_0 = strict['properties']['choice']['oneOf'][0]
        self.assertFalse(choice_0['additionalProperties'])
        self.assertEqual(choice_0['required'], ['c'])

        comb_0 = strict['properties']['combined']['allOf'][0]
        self.assertFalse(comb_0['additionalProperties'])
        self.assertEqual(comb_0['required'], ['base'])

    def test_items_and_prefix_items_descended(self):
        schema = {
            'type': 'object',
            'properties': {
                'items_dict': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {'title': {'type': 'string'}}
                    }
                },
                'items_list': {
                    'type': 'array',
                    'items': [
                        {
                            'type': 'object',
                            'properties': {'first': {'type': 'string'}}
                        }
                    ]
                },
                'prefix_items': {
                    'type': 'array',
                    'prefixItems': [
                        {
                            'type': 'object',
                            'properties': {'p': {'type': 'number'}}
                        }
                    ]
                }
            },
            'required': ['items_dict', 'items_list', 'prefix_items']
        }
        strict = strict_schema(schema)
        self.assertFalse(strict['properties']['items_dict']['items']['additionalProperties'])
        self.assertEqual(strict['properties']['items_dict']['items']['required'], ['title'])
        self.assertFalse(strict['properties']['items_list']['items'][0]['additionalProperties'])
        self.assertFalse(strict['properties']['prefix_items']['prefixItems'][0]['additionalProperties'])


class UnstrictValueTests(unittest.TestCase):
    def test_strips_synthetic_none_on_optional_non_nullable(self):
        schema = {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'tag': {'type': 'string'},
                'count': {'type': 'integer'}
            },
            'required': ['name']
        }
        value = {'name': 'test', 'tag': None, 'count': None}
        unstrict = unstrict_value(value, schema)
        self.assertEqual(unstrict, {'name': 'test'})

    def test_preserves_none_on_required_field(self):
        schema = {
            'type': 'object',
            'properties': {
                'id': {'type': 'string'},
                'status': {'type': 'string'}
            },
            'required': ['id', 'status']
        }
        value = {'id': '123', 'status': None}
        unstrict = unstrict_value(value, schema)
        self.assertEqual(unstrict, {'id': '123', 'status': None})

    def test_preserves_explicit_null_unions(self):
        schema = {
            'type': 'object',
            'properties': {
                'explicit_null_type': {'type': 'null'},
                'union_type_list': {'type': ['string', 'null']},
                'enum_with_none': {'enum': ['a', 'b', None]},
                'const_none': {'const': None},
                'any_of_null': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
                'one_of_null': {'oneOf': [{'type': 'integer'}, {'type': 'null'}]}
            }
        }
        value = {
            'explicit_null_type': None,
            'union_type_list': None,
            'enum_with_none': None,
            'const_none': None,
            'any_of_null': None,
            'one_of_null': None
        }
        unstrict = unstrict_value(value, schema)
        self.assertEqual(unstrict, value)

    def test_resolves_ref_in_defs_for_nullability(self):
        schema = {
            'type': 'object',
            '$defs': {
                'NullableText': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
                'StrictText': {'type': 'string'}
            },
            'properties': {
                'nullable': {'$ref': '#/$defs/NullableText'},
                'strict': {'$ref': '#/$defs/StrictText'}
            }
        }
        value = {'nullable': None, 'strict': None}
        unstrict = unstrict_value(value, schema)
        self.assertEqual(unstrict, {'nullable': None})

    def test_nested_objects_and_arrays(self):
        schema = {
            'type': 'object',
            'properties': {
                'items': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'string'},
                            'desc': {'type': 'string'},
                            'note': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
                        },
                        'required': ['id']
                    }
                }
            },
            'required': ['items']
        }
        value = {
            'items': [
                {'id': '1', 'desc': None, 'note': None},
                {'id': '2', 'desc': 'present', 'note': 'noted'}
            ]
        }
        unstrict = unstrict_value(value, schema)
        self.assertEqual(unstrict, {
            'items': [
                {'id': '1', 'note': None},
                {'id': '2', 'desc': 'present', 'note': 'noted'}
            ]
        })

    def test_decisions_schema_strips_duplicate_of_none(self):
        value = {
            'schema': 'agent-ee.decisions/2',
            'run_id': 'run-1',
            'candidate_id': 'cand-1',
            'decisions': [
                {
                    'finding_id': 'f-1',
                    'disposition': 'accepted',
                    'rationale': 'valid finding',
                    'duplicate_of': None
                },
                {
                    'finding_id': 'f-2',
                    'disposition': 'duplicate',
                    'rationale': 'dup of f-1',
                    'duplicate_of': 'f-1'
                }
            ]
        }
        unstrict = unstrict_value(value, DECISIONS_SCHEMA)
        self.assertNotIn('duplicate_of', unstrict['decisions'][0])
        self.assertEqual(unstrict['decisions'][1]['duplicate_of'], 'f-1')

    def test_extraction_schema_preserves_nullable_hole_and_circuit(self):
        value = {
            'schema': 'agent-ee.footprint-extraction/1',
            'part_number': 'TEST-PART',
            'pdf_sha256': 'a' * 64,
            'pads': [
                {
                    'number': '1',
                    'name': 'GND',
                    'kind': 'smd',
                    'shape': 'rect',
                    'x': '0.00',
                    'y': '0.00',
                    'w': '1.00',
                    'h': '1.00',
                    'hole': None,
                    'cite': {k: {'fact': 'f', 'scale': '1'} for k in ('x', 'y', 'w', 'h', 'hole_w', 'hole_h')}
                }
            ],
            'circuit': None,
            'facts': [],
            'limitations': []
        }
        unstrict = unstrict_value(value, EXTRACTION_SCHEMA)
        self.assertIn('hole', unstrict['pads'][0])
        self.assertIsNone(unstrict['pads'][0]['hole'])
        self.assertIn('circuit', unstrict)
        self.assertIsNone(unstrict['circuit'])


class ProviderContractTests(unittest.TestCase):
    def test_output_refusal_requires_explicit_error_evidence(self):
        from agent_ee.harnesses import provider_output_refusal
        message = 'Incomplete response returned, reason: max_output_tokens'
        for event in (
            {'type': 'error', 'message': message},
            {'event': 'result', 'result': {'status': 'ERROR', 'error': message}},
            {'type': 'result', 'is_error': True, 'errors': [message]},
        ):
            with self.subTest(event=event):
                self.assertEqual(provider_output_refusal(json.dumps(event)), message)
                self.assertEqual(provider_output_refusal(json.dumps(event, indent=2)), message)
        for event in (
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': message}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'output': message}},
            {'type': 'result', 'subtype': 'success', 'result': message},
            {'event': 'result', 'result': {'status': 'SUCCESS', 'response': message}},
            {'type': 'error', 'message': 'Invalid schema in the design file'},
            {'type': 'result', 'is_error': True, 'errors': None},
        ):
            with self.subTest(event=event):
                self.assertIsNone(provider_output_refusal(json.dumps(event)))
        self.assertIsNone(provider_output_refusal('truncated non-JSON console'))
        mixed = [{'type': 'error', 'message': message},
                 {'type': 'turn.failed', 'error': {'message': 'Quota exhausted'}}]
        self.assertIsNone(provider_output_refusal('\n'.join(map(json.dumps, mixed))))

    def test_all_provider_adapters_refuse_oversized_patterns_before_launch(self):
        import tempfile
        from pathlib import Path
        from agent_ee import harnesses, reviewers
        schema = {'$defs': {'nested': {'anyOf': [{'type': 'string', 'pattern': 'x' * 513}]}}}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'HOME': directory, 'AGENT_EE_CODEX_AUTH_HOME': directory}), \
             patch.object(harnesses, 'seat_credential') as credentials, \
             patch.object(harnesses.native_run, 'run') as run:
            from agent_ee.review_run import structured_call
            root = Path(directory)
            for backend in ('codex', 'agy', 'claude'):
                with self.subTest(backend=backend):
                    def call(prompt):
                        if backend == 'claude':
                            return reviewers.claude_call(root, backend, 'test', prompt, schema, 10)
                        return harnesses.native_call(root, backend,
                            {'harness': backend, 'model': 'test', 'effort': 'high'}, prompt, schema, 10)
                    record, value = structured_call(root, backend, 'prompt', schema, call, lambda v: v, limit=1)
                    self.assertEqual((record['execution'], record['failure_kind']), ('failed', 'tool_defect'))
                    self.assertIsNone(value)
                    self.assertEqual(len(record['attempts']), 1)
                    self.assertEqual(record['resubmissions_used'], 0)
            credentials.assert_not_called()
            run.assert_not_called()
        harnesses.validate_provider_schema({'pattern': 'x' * 512})


class ClaudeCallTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        auth = self.root / 'subscription'
        (auth / '.claude').mkdir(parents=True)
        (auth / '.claude/.credentials.json').write_text('{"synthetic":true}')
        self.enterContext(patch.dict(os.environ, {'HOME': str(auth)}))

    def call(self, stdout, exit_code, *, tools=False):
        from agent_ee import native_run, reviewers
        transport = native_run.Recorded(lambda call: native_run.Outcome(stdout=stdout, exit_code=exit_code))
        with native_run.using(transport):
            record, value = reviewers.claude_call(self.root, 'probe', 'test', 'prompt', {}, 10,
                                                  tools=tools, executable='/usr/bin/true')
        self.assertEqual(len(transport.calls), 1)
        return record, value

    def test_provider_contract_refusals_are_classified_at_both_exit_statuses(self):
        from agent_ee.design_generate import review_seat_failures
        message = 'Incomplete response returned, reason: max_output_tokens'
        result = dict(type='result', is_error=True, subtype='error_during_execution', errors=[message])
        for tools, exit_code in ((tools, code) for tools in (False, True) for code in (0, 1)):
            stdout = ('\n'.join(map(json.dumps, [dict(type='system', subtype='init'), result]))
                      if tools else json.dumps(result, indent=2)).encode()
            with self.subTest(tools=tools, exit_code=exit_code):
                record, value = self.call(stdout, exit_code, tools=tools)
                self.assertEqual((record['execution'], record['refusal_kind']), ('malformed', 'structured_output'))
                self.assertEqual(record['error'], message)
                self.assertIsNone(value)
                self.assertEqual(review_seat_failures(None, record), [])

    def test_unknown_errors_remain_failed_at_both_exit_statuses(self):
        for message in ('OAuth token has expired', 'You exceeded your current quota', 'Connection reset by peer'):
            for exit_code in (0, 1):
                with self.subTest(message=message, exit_code=exit_code):
                    result = dict(type='result', is_error=True, subtype='error_during_execution', errors=[message])
                    record, value = self.call(json.dumps(result).encode(), exit_code)
                    self.assertEqual(record['execution'], 'failed')
                    self.assertNotIn('refusal_kind', record)
                    self.assertIsNone(value)

    def test_failed_non_utf8_console_remains_a_failure(self):
        record, value = self.call(b'\xff\xfe failed console\n', 1)
        self.assertEqual(record['execution'], 'failed')
        self.assertNotIn('refusal_kind', record)
        self.assertIsNone(value)


class NativeCallTests(unittest.TestCase):
    """A scripted transport and a fake login: no model, network or real credential."""

    session = '01234567-89ab-4cde-8fab-0123456789ab'

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        auth = self.root / 'subscription'
        auth.mkdir()
        self.login = json.dumps({'tokens': {'access_token': 'fake-access-' + 'a1' * 24,
                                            'refresh_token': 'fake-refresh-' + 'b2' * 24}}).encode()
        (auth / 'auth.json').write_bytes(self.login)
        self.enterContext(patch.dict(os.environ, {'AGENT_EE_CODEX_AUTH_HOME': str(auth)}))
        self.enterContext(patch.dict(os.environ, {'HOME': str(auth)}))

    def call(self, files=None, stream=None, exit_code=0, stdout=None, **options):
        from agent_ee import native_run
        from agent_ee.harnesses import native_call
        stream = stream if stream is not None else [{'type': 'thread.started', 'thread_id': self.session},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"answer": 1}'}},
                  {'type': 'turn.completed', 'usage': {}}]
        transport = native_run.Recorded(lambda call: native_run.Outcome(
            stdout=stdout if stdout is not None else '\n'.join(map(json.dumps, stream)).encode(),
            files=files or {}, exit_code=exit_code))
        with native_run.using(transport):
            record, value = native_call(self.root, 'probe', {'harness': 'codex', 'model': 'gpt-6.1-sol',
                'effort': 'high', 'tool_mode': 'read-only'}, 'scripted prompt', {}, 10,
                executable='/usr/bin/true', **options)
        return transport.calls[0], record, value

    def test_provider_schema_and_output_limits_are_not_seat_failures(self):
        from agent_ee.design_generate import review_seat_failures
        errors = [
            {'message': 'Incomplete response returned, reason: max_output_tokens'},
            {'code': 'invalid_json_schema', 'message': 'Unsupported grammar'},
            {'message': "Invalid schema for response_format 'review': pattern is too large"},
        ]
        for error, exit_code in ((error, code) for error in errors for code in (0, 1)):
            with self.subTest(error=error, exit_code=exit_code):
                stream = [{'type': 'thread.started', 'thread_id': self.session},
                          {'type': 'error', **error},
                          {'type': 'turn.failed', 'error': error}]
                _, record, value = self.call(stream=stream, exit_code=exit_code)
                self.assertEqual(record['execution'], 'malformed')
                self.assertEqual(record['refusal_kind'], 'structured_output')
                self.assertIn(error['message'], record['error'])
                self.assertEqual(record['exit_code'], exit_code)
                self.assertIsNone(value)
                self.assertEqual(review_seat_failures(None, record), [])
                self.assertEqual(review_seat_failures({'review_executions': [record]}), [])

    def test_unknown_provider_errors_remain_seat_failures(self):
        from agent_ee.design_generate import review_seat_failures
        errors = [dict(code='invalid_api_key', message='Incorrect API key provided'),
                  dict(code='insufficient_quota', message='You exceeded your current quota'),
                  dict(code='server_error', message='An error occurred while processing your request')]
        for error, exit_code in ((error, code) for error in errors for code in (0, 1)):
            with self.subTest(error=error, exit_code=exit_code):
                _, record, value = self.call(stream=[{'type': 'turn.failed', 'error': error}], exit_code=exit_code)
                self.assertEqual(record['execution'], 'failed')
                self.assertIsNone(value)
                self.assertTrue(review_seat_failures(None, record))

    def test_failed_non_utf8_console_remains_a_failure(self):
        _, record, value = self.call(stdout=b'\xff\xfe failed console\n', exit_code=1)
        self.assertEqual(record['execution'], 'failed')
        self.assertNotIn('refusal_kind', record)
        self.assertIsNone(value)

    def test_default_call_keeps_an_unbounded_console_and_no_extra_mounts(self):
        call, record, value = self.call()
        self.assertEqual((record['execution'], value), ('ok', {'answer': 1}))
        self.assertIsNone(call.intent.log_bytes)
        self.assertEqual([dest for _, dest in call.mounts],
                         ['/work/home/.codex/auth.json', '/reviewer'])

    def test_a_caller_can_bound_the_console_and_add_read_only_mounts(self):
        libraries = self.root / 'libraries'
        libraries.mkdir()
        call, record, _ = self.call(log_bytes=4096, extra_mounts=[(libraries, '/kicad')])
        self.assertEqual(call.intent.log_bytes, 4096)
        self.assertIn((libraries, '/kicad'), call.mounts)

    def test_experiments_are_retained_unchanged(self):
        # Evidence is never deleted on a heuristic, even a file that holds the (fake) login.
        files = {'experiments/login-copy.json': self.login, 'experiments/parts.json': b'{"lcsc": "C1"}'}
        _, record, value = self.call(files=files)
        self.assertEqual(value, {'answer': 1})
        for name, data in files.items():
            self.assertEqual((self.root / 'review-work/probe' / name).read_bytes(), data)
        self.assertNotIn('credential_copies_refused', record)


if __name__ == '__main__':
    unittest.main()
