"""Tests for strict schema generation and schema-directed unstrict normalization."""
import unittest

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


if __name__ == '__main__':
    unittest.main()
