"""AGENTEE-430 (G0): opt-in bounded placement trace and the shared pcb-trace-envelope/1.

Fixtures are the existing original placer fixtures (search, cluster, copper-extent,
block arrangement and native runner cases); none is a third-party design.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee import placement_trace, trace_envelope
from agent_ee.placer import pack
from test_placer import request
from test_placer_search import anchor_case, promotion_case

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = ROOT / 'schemas/pcb-trace-envelope-1.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def schema_problems(value, schema, root, path='$'):
    """The JSON Schema 2020-12 subset the published envelope schema uses, for parity tests."""
    if '$ref' in schema:
        node = root
        for part in schema['$ref'].removeprefix('#/').split('/'):
            node = node[part]
        return schema_problems(value, node, root, path)
    problems = []
    kinds = {'object': dict, 'array': list, 'string': str, 'null': type(None)}
    if 'type' in schema:
        names = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        ok = any((isinstance(value, int) and not isinstance(value, bool)) if n == 'integer'
                 else isinstance(value, kinds[n]) for n in names)
        if not ok:
            return [f'{path}: not {schema["type"]}']
    if 'const' in schema and value != schema['const']:
        problems.append(f'{path}: not const {schema["const"]!r}')
    if 'enum' in schema and value not in schema['enum']:
        problems.append(f'{path}: not in {schema["enum"]}')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            problems.append(f'{path}: too short')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            problems.append(f'{path}: pattern')
    if isinstance(value, int) and 'minimum' in schema and value < schema['minimum']:
        problems.append(f'{path}: below minimum')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            problems.append(f'{path}: item count')
        for i, item in enumerate(value):
            if 'items' in schema:
                problems += schema_problems(item, schema['items'], root, f'{path}[{i}]')
    if isinstance(value, dict):
        problems += [f'{path}: missing {key}' for key in schema.get('required', []) if key not in value]
        if len(value) < schema.get('minProperties', 0):
            problems.append(f'{path}: too few properties')
        for key, item in value.items():
            if key in schema.get('properties', {}):
                problems += schema_problems(item, schema['properties'][key], root, f'{path}.{key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                problems += schema_problems(item, schema['additionalProperties'], root, f'{path}.{key}')
    for sub in schema.get('allOf', []):
        problems += schema_problems(value, sub, root, path)
    if 'anyOf' in schema and all(schema_problems(value, sub, root, path) for sub in schema['anyOf']):
        problems.append(f'{path}: matches no anyOf branch')
    if 'if' in schema:
        branch = 'then' if not schema_problems(value, schema['if'], root, path) else 'else'
        if branch in schema:
            problems += schema_problems(value, schema[branch], root, path)
    return problems


def traced(source, policy, limits=None):
    return placement_trace.traced_pack(copy.deepcopy(source), policy, limits=limits)


class EnvelopeSchemaTest(unittest.TestCase):
    """The envelope is consumer-neutral: its published schema and the reader agree."""
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_FILE.read_text())
        source, policy = promotion_case()
        cls.plan, cls.document = traced(source, policy)

    def both(self, document):
        return trace_envelope.validate(document), schema_problems(document, self.schema, self.schema)

    def assert_rejected(self, document, label):
        reader, schema = self.both(document)
        self.assertTrue(reader, f'reader accepted {label}')
        self.assertTrue(schema, f'published schema accepted {label}')

    def test_published_schema_is_the_reader_contract(self):
        self.assertEqual(self.schema['$id'], trace_envelope.SCHEMA)
        self.assertEqual(self.schema['properties']['schema'], {'const': trace_envelope.SCHEMA})
        self.assertEqual(tuple(self.schema['required']), trace_envelope.REQUIRED)
        self.assertEqual(tuple(self.schema['properties']['identity']['required']), trace_envelope.IDENTITIES)
        self.assertEqual(tuple(self.schema['properties']['hashes']['required']), trace_envelope.HASH_GROUPS)
        self.assertEqual(tuple(self.schema['properties']['state']['properties']['status']['enum']),
                         trace_envelope.STATES)
        duration = self.schema['$defs']['duration']['properties']
        self.assertEqual(tuple(duration['boundary']['enum']), trace_envelope.BOUNDARIES)
        self.assertEqual(tuple(duration['overlap']['enum']), trace_envelope.OVERLAPS)
        self.assertEqual(tuple(self.schema['properties']['sampling']['properties']['coverage']['enum']),
                         trace_envelope.COVERAGE)
        self.assertEqual(self.both(self.document), ([], []))
        # Consumer-neutral: no placement vocabulary in the shared schema.
        self.assertNotRegex(SCHEMA_FILE.read_text().lower(), r'placer|placement|footprint|stage_a|wire')

    def test_every_required_field_is_required(self):
        for key in trace_envelope.REQUIRED:
            document = copy.deepcopy(self.document)
            del document[key]
            self.assert_rejected(document, f'missing {key}')
        for key in trace_envelope.IDENTITIES:
            document = copy.deepcopy(self.document)
            del document['identity'][key]
            self.assert_rejected(document, f'missing identity {key}')
        for key in ('unit', 'value', 'count', 'boundary', 'overlap', 'parent'):
            document = copy.deepcopy(self.document)
            del document['durations'][0][key]
            self.assert_rejected(document, f'duration without {key}')

    def test_unknown_needs_a_reason_and_qualified_needs_an_identity(self):
        document = copy.deepcopy(self.document)
        self.assertEqual(document['identity']['project'], {
            'status': 'unknown', 'reason': 'standalone placement capture carries no project file'})
        self.assertEqual(document['identity']['experiment']['status'], 'unknown')
        for change, label in (({'status': 'unknown'}, 'unknown without reason'),
                              ({'status': 'unknown', 'reason': ''}, 'empty reason'),
                              ({'status': 'qualified'}, 'qualified without sha256 or id'),
                              ({'status': 'qualified', 'sha256': 'A' * 64}, 'uppercase hash'),
                              ({'status': 'qualified', 'sha256': 'a' * 63}, 'short hash'),
                              ({'status': 'maybe', 'reason': 'x'}, 'status outside the enum')):
            document = copy.deepcopy(self.document)
            document['identity']['pcb'] = change
            self.assert_rejected(document, label)
        document = copy.deepcopy(self.document)
        document['hashes']['native_tools'] = [{'name': 'tool', 'status': 'unknown'}]
        self.assert_rejected(document, 'tool hash unknown without reason')
        document = copy.deepcopy(self.document)
        document['objective'] = {'status': 'unknown'}
        self.assert_rejected(document, 'objective unknown without reason')
        document['objective'] = {'status': 'unknown', 'reason': 'no objective declared'}
        self.assertEqual(self.both(document), ([], []))
        document = copy.deepcopy(self.document)
        document['state'] = {'status': 'failed'}
        self.assert_rejected(document, 'failed state without reason')

    def test_truncation_is_partial_and_never_qualifies_complete_parity(self):
        sampling = self.document['sampling']
        self.assertEqual((sampling['coverage'], sampling['dropped'], sampling['partial_reasons']),
                         ('complete', 0, []))
        self.assertTrue(trace_envelope.complete_parity_allowed(self.document))
        document = copy.deepcopy(self.document)
        document['sampling']['dropped'] = 1
        self.assert_rejected(document, 'complete coverage with drops')
        document['sampling']['coverage'] = 'partial'
        self.assert_rejected(document, 'partial coverage without a reason')
        document['sampling']['partial_reasons'] = ['sample limit reached']
        self.assertEqual(self.both(document), ([], []))
        self.assertFalse(trace_envelope.complete_parity_allowed(document))
        document = copy.deepcopy(self.document)
        document['sampling']['partial_reasons'] = ['reason without partial']
        self.assert_rejected(document, 'complete coverage with a partial reason')

    def test_additive_fields_are_ignored_and_other_versions_refused(self):
        document = copy.deepcopy(self.document)
        document['future_field'] = {'anything': [1, 2]}
        document['identity']['toolchain_lock'] = {'status': 'unknown', 'reason': 'not yet recorded'}
        document['durations'][0]['future'] = True
        document['sampling']['future'] = 'x'
        self.assertEqual(self.both(document), ([], []))
        document['identity']['toolchain_lock'] = {'status': 'unknown'}
        self.assert_rejected(document, 'additional identity without reason')
        for version in ('pcb-trace-envelope/2', 'pcb-trace-envelope/0', None):
            document = copy.deepcopy(self.document)
            document['schema'] = version
            self.assert_rejected(document, f'version {version}')
            self.assertIn('unsupported envelope version', trace_envelope.validate(document)[0])

    def test_cross_field_boundaries(self):
        names = [row['name'] for row in self.document['durations']]
        self.assertEqual(names, [row[0] for row in placement_trace.DURATIONS])
        document = copy.deepcopy(self.document)
        document['durations'][3]['parent'] = 'nowhere'
        self.assertIn('durations[3]: names unknown duration nowhere', trace_envelope.validate(document))
        document = copy.deepcopy(self.document)
        document['durations'].append(copy.deepcopy(document['durations'][0]))
        self.assertTrue(any('duplicate name' in p for p in trace_envelope.validate(document)))
        document = copy.deepcopy(self.document)
        nested = next(row for row in document['durations'] if row['overlap'] == 'nested')
        del nested['within']
        self.assert_rejected(document, 'nested overlap without within')
        document = copy.deepcopy(self.document)
        document['clock'] = {'source': 'wall', 'unit': 'ns'}
        self.assert_rejected(document, 'non-monotonic clock')
        document = copy.deepcopy(self.document)
        document['counters']['negative'] = -1
        self.assert_rejected(document, 'negative counter')

    def test_reader_and_published_schema_agree_on_every_present_field(self):
        """H5 (review ee430b): a malformed defined field is rejected by both, whatever its record's status."""
        bad_hash = {'status': 'unknown', 'reason': 'absent', 'sha256': 'invalid'}
        cases = [
            # (location, value, expected valid), the reviewer's reproduction first.
            (('identity', 'pcb'), bad_hash, False),
            (('identity', 'capture'), bad_hash, False),
            (('identity', 'pcb'), {'status': 'unknown', 'reason': 'absent', 'sha256': 'a' * 64}, True),
            (('identity', 'pcb'), {'status': 'qualified', 'id': ''}, False),
            (('identity', 'pcb'), {'status': 'qualified', 'id': 'x', 'sha256': 7}, False),
            (('hashes', 'native_tools'), [{'name': 'tool', **bad_hash}], False),
            (('hashes', 'native_tools'), [{'name': '', 'status': 'qualified', 'sha256': 'a' * 64}], False),
            (('objective',), {'status': 'unknown', 'reason': 'undeclared', 'sha256': 'invalid'}, True),
            (('objective',), {'status': 'unknown', 'reason': 'undeclared', 'id': 'x'}, False),
            (('objective',), {'status': 'unknown', 'reason': 'undeclared', 'id': 'x', 'version': '1'}, True),
            (('objective',), {'id': 'x', 'version': ''}, False),
            (('generation',), {'status': 'unknown', 'reason': ''}, False),
            (('generation',), {'status': 'other', 'id': 'g'}, True),
            (('generation',), {'id': ''}, False),
            (('state',), {'status': 'completed', 'reason': ''}, False),
            (('state',), {'status': 'completed', 'reason': 'note'}, True),
            (('state',), {'status': 'interrupted', 'reason': 3}, False),
            (('durations', 0, 'within'), 'pipeline', False),
            (('durations', 0, 'within'), ['Not A Name'], False),
            (('counters', 'Free Form'), 1, True),
            (('counters', 'free'), True, False),
            (('consumer', 'version'), '', False),
            (('stage', 'id'), 'Upper', False),
            (('clock', 'unit'), 'ms', False),
            (('sampling', 'limits'), {}, False),
            (('sampling', 'partial_reasons'), [''], False),
            (('payload', 'schema'), '', False),
        ]
        for location, value, valid in cases:
            document = copy.deepcopy(self.document)
            target = document
            for key in location[:-1]:
                target = target[key]
            target[location[-1]] = value
            reader, schema = self.both(document)
            self.assertEqual((not reader, not schema), (valid, valid), (location, value, reader, schema))

    def test_a_run_that_did_not_complete_never_qualifies_complete_parity(self):
        """H2 (review ee430b): the coverage label alone cannot make a failed or interrupted run complete."""
        for status in ('failed', 'interrupted'):
            document = copy.deepcopy(self.document)
            document['state'] = {'status': status, 'reason': 'stopped'}
            self.assertEqual(document['sampling']['coverage'], 'complete')
            self.assertEqual(self.both(document), ([], []))  # Retained as a valid record ...
            self.assertFalse(trace_envelope.complete_parity_allowed(document))  # ... never as complete evidence.
        self.assertTrue(trace_envelope.complete_parity_allowed(self.document))

    def test_payload_is_consumer_owned(self):
        document = copy.deepcopy(self.document)
        document['payload'] = {'schema': 'kicad-route.routing-trace/1', 'anything': [1]}
        self.assertEqual(self.both(document), ([], []))
        document['payload'] = {}
        self.assert_rejected(document, 'payload without its schema')


class PlacementTraceTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(placement_trace.active())
        placement_trace.phase('prep')  # A no-op while off.
        source, policy = promotion_case()
        plan = pack(copy.deepcopy(source), policy)
        self.assertEqual(plan['status'], 'planned')
        self.assertNotIn('trace', canonical(plan).lower().replace('"strategy"', ''))
        with placement_trace.recording() as recorder:
            self.assertIs(placement_trace.active(), recorder)
            with self.assertRaisesRegex(ValueError, 'already recording'):
                with placement_trace.recording():
                    pass
        self.assertIsNone(placement_trace.active())
        with self.assertRaisesRegex(ValueError, 'limits'):
            placement_trace.Recorder({'samples': -1})

    def assert_identical(self, source, policy, run=pack):
        off = run(copy.deepcopy(source), policy)
        with placement_trace.recording() as recorder:
            on = run(copy.deepcopy(source), policy)
        self.assertEqual(canonical(on), canonical(off))
        document = placement_trace.envelope(recorder, stage='pack', state={'status': 'completed'},
                                            identity=placement_trace.pack_identity(source, policy, on))
        self.assertEqual(document['payload']['reconciliation'], {'steps': True, 'candidates': True})
        report = placement_trace.replay(json.loads(json.dumps(document)))
        self.assertEqual(report['status'], 'exact', report)
        return off, recorder

    def test_outputs_are_identical_with_tracing_on_and_off(self):
        from test_placer_clusters import random_cluster_case, three_cap_case
        from test_placer_copper_extent import board, offset_part
        cases = [promotion_case(), anchor_case(), three_cap_case(),
                 (board([offset_part()]), request(fixed_refs=[]))]
        rng = random.Random(430)
        cases += [random_cluster_case(rng) for _ in range(30)]
        statuses, passes = set(), set()
        for source, policy in cases:
            plan, recorder = self.assert_identical(source, policy)
            statuses.add(plan['status'])
            passes.update((r['joint'], r['copper_axes']) for _, r in recorder.searches)
        # The differential covers success and typed failure, legacy, joint and copper-aligned passes.
        self.assertIn('planned', statuses)
        self.assertGreater(len(statuses), 1)
        self.assertEqual(passes, {(False, False), (True, False), (False, True)})

    def test_counters_reconcile_with_the_plan(self):
        for case in (promotion_case, anchor_case):
            source, policy = case()
            plan, document = traced(source, policy)
            counters = document['counters']
            self.assertEqual(counters['stage_a.steps'], plan['steps_used'])
            self.assertEqual(counters['stage_a.steps'],
                             counters['stage_a.doomed'] + counters['stage_a.admitted'] + counters['stage_a.rejected'])
            self.assertEqual(counters['stage_a.unresolved'],
                             counters['stage_a.candidates_generated'] - counters['stage_a.exact_resolved'])
            # Lazy ranking leaves most generated candidates unresolved; both are counted.
            self.assertGreater(counters['stage_a.unresolved'], 0)
            self.assertEqual(counters['stage_a.stale_samples'], 0)
            self.assertEqual(document['payload']['reconciliation'], {'steps': True, 'candidates': True})
            self.assertEqual(sum(r['generated'] for r in document['payload']['requests']),
                             counters['stage_a.candidates_generated'])
            self.assertEqual(len(document['payload']['samples']), counters['stage_a.exact_resolved'])
            self.assertEqual(sum(r['steps'] for r in document['generation']['searches']), plan['steps_used'])
            self.assertEqual(counters['stage_a.generations'], document['generation']['allocated'])
            durations = {row['name']: row for row in document['durations']}
            for name in ('pipeline', 'prep', 'stage_a.search', 'stage_a.geometry', 'stage_a.bounds',
                         'stage_a.exact', 'stage_a.rank', 'stage_a.predicates', 'stage_a.judge'):
                self.assertGreater(durations[name]['value'], 0, name)
            self.assertEqual(durations['stage_a.exact']['count'], counters['stage_a.exact_resolved'])
            self.assertEqual(durations['stage_a.predicates']['count'],
                             counters['stage_a.admitted'] + counters['stage_a.rejected'])
            children = sum(row['value'] for row in durations.values() if row['parent'] == 'stage_a.search')
            self.assertLessEqual(children, durations['stage_a.search']['value'])
            top = sum(row['value'] for row in durations.values()
                      if row['parent'] == 'pipeline' and row['overlap'] == 'disjoint' or
                      row['name'] in ('prep', 'stage_a.search', 'stage_a.judge'))
            self.assertLessEqual(top, durations['pipeline']['value'])
            self.assertEqual((durations['stage_b.arrange']['value'], durations['native_checks']['value'],
                              durations['export']['value']), (0, 0, 0))

    def test_identity_and_hashes(self):
        source, policy = promotion_case()
        plan, document = traced(source, policy)
        identity = document['identity']
        self.assertEqual(identity['pcb'], {'status': 'qualified', 'sha256': source['board_sha256']})
        self.assertEqual(identity['rules']['sha256'], trace_envelope.canonical_sha256(policy))
        self.assertEqual(identity['capture']['sha256'], trace_envelope.canonical_sha256(source))
        self.assertEqual(identity['candidate']['sha256'], trace_envelope.canonical_sha256(plan))
        names = {row['name']: row for row in document['hashes']['source']}
        self.assertEqual(set(names), {'agent_ee/' + n for n in placement_trace.SOURCES})
        placer_path = ROOT / 'src/agent_ee/placer.py'
        self.assertEqual(names['agent_ee/placer.py']['sha256'], hashlib.sha256(placer_path.read_bytes()).hexdigest())
        self.assertEqual(document['hashes']['binary'][0]['sha256'],
                         hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest())
        self.assertEqual(document['objective']['id'], 'agent-ee.placer.wire-cost')
        self.assertEqual((document['state'], document['stage'], document['consumer']['id']),
                         ({'status': 'completed', 'outcome': 'planned'}, {'id': 'pack'}, 'agent-ee.placement'))
        failed, document = traced(source, {**policy, 'step_budget': 1})
        self.assertEqual(failed['status'], 'packing_budget_exhausted')
        self.assertEqual(document['identity']['candidate'],
                         {'status': 'unknown', 'reason': 'no planned candidate: packing_budget_exhausted'})
        self.assertEqual(document['state']['outcome'], 'packing_budget_exhausted')

    def test_replay_reproduces_every_exact_cost_and_bound(self):
        source, policy = anchor_case()
        _, document = traced(source, policy)
        report = placement_trace.replay(document)
        self.assertEqual((report['status'], report['parity'], report['mismatch_count']), ('exact', 'complete', 0))
        self.assertEqual(report['samples_checked'], document['counters']['stage_a.exact_resolved'])
        inputs = placement_trace.stage_a_inputs(document)
        self.assertEqual(sum(len(i['queries']) for i in inputs), report['samples_checked'])
        self.assertTrue(all(set(i['pads']) == {'0', '90', '180', '270'} for i in inputs))
        # An independent recomputation of one sampled pose from the trace alone.
        entry = next(i for i in inputs if i['queries'])
        (x, y, rotation), cost = entry['queries'][0], entry['expected_cost_nm'][0]
        independent = sum(min(abs(ox + x - a) + abs(oy + y - b) for a, b in entry['peers'][net])
                          for net, ox, oy in entry['pads'][str(rotation)] if net in entry['peers'])
        self.assertEqual(independent, cost)

    def test_replay_rejects_wrong_costs_stale_generations_and_tampered_inputs(self):
        source, policy = anchor_case()
        _, document = traced(source, policy)
        wrong = copy.deepcopy(document)
        wrong['payload']['samples'][0][7] += 1
        report = placement_trace.replay(wrong)
        self.assertEqual((report['status'], report['parity'], report['mismatch_count']), ('mismatch', 'none', 1))
        stale = copy.deepcopy(document)
        stale['payload']['samples'][0][1] += 1
        report = placement_trace.replay(stale)
        self.assertEqual((report['status'], report['parity']), ('refused', 'none'))
        self.assertIn('stale', report['reason'])
        tampered = copy.deepcopy(document)
        request = next(r for r in tampered['payload']['requests'] if r['peers'])
        net = next(iter(request['peers']))
        request['peers'][net][0][0] += 1
        report = placement_trace.replay(tampered)
        self.assertEqual(report['status'], 'refused')
        self.assertIn('differ from their recorded hash', report['reason'])
        invalid = copy.deepcopy(document)
        invalid['schema'] = 'pcb-trace-envelope/2'
        self.assertEqual(placement_trace.replay(invalid)['status'], 'refused')

    def assert_refused(self, document, reason, label):
        report = placement_trace.replay(document)
        self.assertEqual((report['status'], report['parity']), ('refused', 'none'), (label, report))
        self.assertIn(reason, report['reason'], label)

    def test_replay_refuses_missing_duplicated_and_malformed_evidence(self):
        """H1 (review ee430b): sample evidence is reconciled with the counters, never taken from the label."""
        source, policy = anchor_case()
        _, document = traced(source, policy)
        self.assertEqual((len(document['payload']['requests']), len(document['payload']['samples'])), (4, 6))

        def changed(change):
            changed = copy.deepcopy(document)
            change(changed['payload'], changed)
            return changed

        def resize(payload, document):
            # Keep the sampling record consistent with the edited payload, so only reconciliation can refuse it.
            sampling = document['sampling']
            sampling['recorded'] = len(payload['requests']) + len(payload['samples'])
            sampling['bytes'] = len(canonical({'requests': payload['requests'], 'samples': payload['samples']}))

        def drop_samples(payload, document):
            payload['samples'] = []
            resize(payload, document)

        def drop_all(payload, document):
            payload['requests'], payload['samples'] = [], []
            resize(payload, document)

        def duplicate(payload, document):
            payload['samples'].append(list(payload['samples'][0]))
            resize(payload, document)
        unaccounted = 'does not account for'
        for change, reason, label in (
                (drop_samples, unaccounted, 'every sample deleted'),
                (drop_all, unaccounted, 'every request and sample deleted'),
                (duplicate, 'duplicates request', 'duplicated sample'),
                (lambda p, d: p['samples'].pop(), 'sampling.recorded', 'sample deleted, record unchanged'),
                (lambda p, d: p['samples'][0].pop(), 'must be integer', 'sample missing a field'),
                (lambda p, d: p['samples'][0].__setitem__(4, 45), 'rotation 45', 'invalid rotation'),
                (lambda p, d: p['samples'][0].__setitem__(2, True), 'must be integer', 'boolean coordinate'),
                (lambda p, d: p['samples'][0].__setitem__(5, '1.5'), 'must be integer', 'inexact local cost'),
                (lambda p, d: p['samples'][0].__setitem__(0, 9), 'unrecorded request 9', 'unknown request'),
                (lambda p, d: p['requests'][1].__setitem__('id', 0), 'recording position', 'duplicate request id'),
                (lambda p, d: p['requests'][0].__setitem__('search', 5), 'not a recorded search', 'unknown search'),
                (lambda p, d: p['requests'][0].__setitem__('generation', 99), 'never allocated', 'future generation'),
                (lambda p, d: p['requests'][0]['pads'].pop('90'), 'pads must give', 'missing rotation pads'),
                (lambda p, d: p['requests'][0].pop('peers'), 'peers must map', 'missing peers'),
                (lambda p, d: p['requests'][0].__setitem__('generated', 0), 'exceed its 0 generated',
                 'more samples than candidates'),
                (lambda p, d: d['sampling'].__setitem__('bytes', d['sampling']['bytes'] - 1), 'sampling.bytes',
                 'wrong byte count'),
                (lambda p, d: d['counters'].__setitem__('stage_a.exact_resolved', 7), 'stage_a.exact count',
                 'counter edited alone'),
                (lambda p, d: p.__setitem__('samples', {}), 'must be lists', 'samples not a list')):
            self.assert_refused(changed(change), reason, label)
        # A capped trace stays explicitly partial; removing one of its samples is refused too.
        _, capped = traced(source, policy, {'samples': 2})
        self.assertEqual(placement_trace.replay(capped)['parity'], 'partial')
        capped['payload']['samples'].pop()
        capped['sampling']['recorded'] -= 1
        capped['sampling']['bytes'] = len(canonical({'requests': capped['payload']['requests'],
                                                     'samples': capped['payload']['samples']}))
        self.assert_refused(capped, unaccounted, 'capped sample deleted')

    def test_replay_needs_the_declared_contract_and_qualified_identities(self):
        """H2 (review ee430b): unknown or other contracts are refused; unknown identity or an
        incomplete run leaves agreement partial. Neither ever reports complete parity."""
        source, policy = anchor_case()
        _, document = traced(source, policy)
        self.assertEqual(placement_trace.replay(document)['parity'], 'complete')

        def changed(key, value):
            changed = copy.deepcopy(document)
            changed[key] = value
            return changed
        for key, value, reason in (
                ('objective', {'status': 'unknown', 'reason': 'not declared'}, 'objective is unknown'),
                ('objective', {**placement_trace.OBJECTIVE, 'id': 'other.score'}, 'objective other.score/1'),
                ('objective', {**placement_trace.OBJECTIVE, 'version': '2'}, 'is not the supported'),
                ('generation', {'status': 'unknown', 'reason': 'not declared'}, 'generation is unknown'),
                ('generation', {**document['generation'], 'id': 'other/1'}, 'generation other/1'),
                ('consumer', {'id': 'kicad-route.routing', 'version': '1'}, 'consumer kicad-route.routing/1')):
            self.assert_refused(changed(key, value), reason, f'{key} {value}')
        unknown = trace_envelope.unknown('withheld')
        for key, value, reason in (
                ('state', {'status': 'failed', 'reason': 'stopped'}, 'traced run failed: stopped'),
                ('state', {'status': 'interrupted', 'reason': 'signal'}, 'traced run interrupted: signal'),
                ('identity', {**document['identity'], 'pcb': unknown}, 'identity.pcb unknown: withheld'),
                ('identity', {**document['identity'], 'rules': unknown}, 'identity.rules unknown: withheld'),
                ('identity', {key: unknown for key in document['identity']}, 'identity.pcb unknown')):
            report = placement_trace.replay(changed(key, value))
            self.assertEqual((report['status'], report['parity']), ('exact', 'partial'), (key, value, report))
            self.assertIn(reason, report['partial_reasons'][0] if key == 'state' else ' '.join(
                report['partial_reasons']))

    def test_byte_budget_bounds_what_is_retained_during_capture(self):
        """H4 (review ee430b): an oversized request is sized, not copied, and never retained."""
        class Points(list):
            yielded = 0

            def __iter__(self):
                for point in list.__iter__(self):
                    Points.yielded += 1
                    yield point
        recorder = placement_trace.Recorder({'bytes': 900})
        fp = {'ref': 'U1', 'uuid': None}
        peers = {'N': Points([(i, i) for i in range(100000)])}

        def pad_nets(fp, position, rotation, grounds):
            return [('N', 0, 0)]
        self.assertIsNone(recorder.scan_request(0, 0, fp, set(), peers, pad_nets))
        self.assertEqual((recorder.requests, recorder.byte_dropped_requests), ([], 1))
        self.assertLessEqual(recorder.stored, 900)
        self.assertEqual(Points.yielded, 0)  # Dropped from its point count: no point is read or copied.
        request = recorder.scan_request(0, 0, fp, set(), {'N': Points([(1, 2)])}, pad_nets)
        self.assertEqual(request['peers'], {'N': [[1, 2]]})
        for _ in range(100):
            recorder.sample(request, 0, 0, (0, 1, 3, 4, 0, ()), 2)
        self.assertLessEqual(recorder.stored, 900)
        self.assertGreater(recorder.byte_dropped_samples, 0)
        # Through the placer: at every byte limit the retained set is within it and the plan unchanged.
        source, policy = anchor_case()
        full_plan, full = traced(source, policy)
        sizes = set()
        # The account is an upper bound (it reserves each request's generated count and hash), so a
        # limit of exactly the full trace's size may still drop; twice that keeps everything.
        for limit in (placement_trace.FRAME_BYTES, placement_trace.FRAME_BYTES + 1, 300, 600, 900, 1500,
                      full['sampling']['bytes'], 2 * full['sampling']['bytes']):
            with placement_trace.recording({'bytes': limit}) as recorder:
                plan = pack(copy.deepcopy(source), policy)
            self.assertEqual(canonical(plan), canonical(full_plan))
            retained = len(canonical({'requests': recorder.requests, 'samples': recorder.samples}))
            self.assertLessEqual(recorder.stored, limit)
            # Recording's byte account bounds the exported size (generated count and hash are reserved).
            self.assertLessEqual(retained, recorder.stored)
            document = placement_trace.envelope(recorder, stage='pack', state={'status': 'completed'},
                                                identity=placement_trace.pack_identity(source, policy, plan))
            self.assertLessEqual(document['sampling']['bytes'], limit)
            report = placement_trace.replay(document)
            self.assertEqual(report['status'], 'exact', (limit, report))
            self.assertEqual(report['parity'], 'complete' if document['sampling']['coverage'] == 'complete'
                             else 'partial')
            sizes.add(document['sampling']['bytes'])
            if limit == 2 * full['sampling']['bytes']:
                self.assertEqual((report['parity'], document['payload']), ('complete', full['payload']))
        self.assertIn(placement_trace.FRAME_BYTES, sizes)  # The minimum budget is a valid empty capture.

    def test_impossible_limits_are_refused_before_placement(self):
        self.assertEqual(placement_trace.FRAME_BYTES, len(canonical({'requests': [], 'samples': []})))
        source, policy = anchor_case()
        for limits in ({'bytes': 0}, {'bytes': 1}, {'bytes': 25}, {'bytes': 26},
                       {'bytes': placement_trace.FRAME_BYTES - 1}, {'samples': True}, {'other': 1}):
            with self.assertRaises(ValueError, msg=limits):
                placement_trace.Recorder(limits)
            with self.assertRaises(ValueError, msg=limits):
                traced(source, policy, limits)
            self.assertIsNone(placement_trace.active())
        _, document = traced(source, policy, {'bytes': placement_trace.FRAME_BYTES})
        sampling = document['sampling']
        self.assertEqual((sampling['bytes'], sampling['recorded'], sampling['coverage']),
                         (placement_trace.FRAME_BYTES, 0, 'partial'))
        self.assertEqual(sampling['dropped'], document['counters']['stage_a.scans']
                         + document['counters']['stage_a.exact_resolved'])

    def test_capture_is_bounded_and_truncation_is_partial(self):
        source, policy = anchor_case()
        full_plan, full = traced(source, policy)
        for limits, reason in (({'samples': 2}, 'sample limit 2'), ({'requests': 1}, 'request limit 1'),
                               ({'bytes': 900}, 'byte limit 900'), ({'requests': 0, 'samples': 0}, 'request limit 0')):
            plan, document = traced(source, policy, limits)
            self.assertEqual(canonical(plan), canonical(full_plan))
            sampling = document['sampling']
            self.assertEqual(sampling['coverage'], 'partial')
            self.assertTrue(any(r.startswith(reason) for r in sampling['partial_reasons']), sampling)
            self.assertGreater(sampling['dropped'], 0)
            self.assertLessEqual(sampling['bytes'], sampling['limits']['bytes'])
            self.assertLessEqual(len(document['payload']['samples']), sampling['limits']['samples'])
            self.assertLessEqual(len(document['payload']['requests']), sampling['limits']['requests'])
            self.assertEqual(sampling['bytes'], len(canonical({'requests': document['payload']['requests'],
                                                              'samples': document['payload']['samples']})))
            # Counters stay complete when samples are capped.
            self.assertEqual(document['counters'], full['counters'])
            self.assertFalse(trace_envelope.complete_parity_allowed(document))
            report = placement_trace.replay(document)
            self.assertEqual((report['status'], report['parity']), ('exact', 'partial'))
        self.assertEqual(full['sampling']['recorded'],
                         len(full['payload']['requests']) + len(full['payload']['samples']))

    def test_block_arrangement_is_stage_b_with_nested_residual_stage_a(self):
        from test_block_arrangement import ArrangementFixture

        class Case(ArrangementFixture, unittest.TestCase):
            def runTest(self):
                pass
        case = Case()
        case.setUp()
        self.addCleanup(case.doCleanups)

        def normalized(plan):
            # elapsed_s is the arrangement's own wall time (AGENTEE-340): it differs between any two runs.
            plan = copy.deepcopy(plan)
            plan['block_arrangement']['search'].pop('elapsed_s')
            return plan
        off = case.arrange()
        with placement_trace.recording() as recorder:
            on = case.arrange()
        self.assertEqual(off['status'], 'planned')
        self.assertEqual(canonical(normalized(on)), canonical(normalized(off)))
        document = placement_trace.envelope(recorder, stage='arrange', state={'status': 'completed'},
                                            identity=placement_trace.pack_identity(case.board, case.policy, on))
        durations = {row['name']: row for row in document['durations']}
        self.assertEqual((durations['stage_b.arrange']['count'], durations['stage_b.search']['count']), (1, 1))
        self.assertLessEqual(durations['stage_b.search']['value'], durations['stage_b.arrange']['value'])
        self.assertGreater(durations['stage_a.search']['value'], 0)
        self.assertEqual(durations['stage_a.search']['within'], ['stage_b.arrange'])
        self.assertEqual(document['counters']['stage_b.arrangements'], 1)
        self.assertEqual(document['counters']['stage_b.steps'], on['block_arrangement']['search']['steps_used'])
        self.assertEqual(document['counters']['stage_b.steps'] + document['counters']['stage_a.steps'],
                         on['steps_used'])
        self.assertEqual(placement_trace.replay(document)['status'], 'exact')

    def test_overhead_measurement_is_matched_and_output_preserving(self):
        source, policy = promotion_case()
        result = placement_trace.overhead(source, policy, runs=2, warmups=0)
        self.assertTrue(result['outputs_identical'])
        self.assertEqual([len(result[k]) for k in ('off_ns', 'on_ns', 'recording_ns', 'export_ns')], [2, 2, 2, 2])
        # H6 (review ee430b): an enabled run is measured until its trace is exported.
        self.assertEqual(result['on_ns'], [r + e for r, e in zip(result['recording_ns'], result['export_ns'])])
        self.assertGreater(result['trace_bytes_max'], 0)
        self.assertEqual(result['gate']['kind'], 'experiment')
        self.assertIsInstance(result['median_overhead'], float)
        # Coordinator ruling on H6: a 2 ms unit fixture is reported, not gated, and never reported as met.
        self.assertLess(result['median_off_ns'], placement_trace.GATE_MIN_OFF_NS)
        self.assertEqual((result['gate']['applies'], result['gate']['met']), (False, None))

    def test_command_line(self):
        source, policy = promotion_case()
        with tempfile.TemporaryDirectory(prefix='agent-ee-trace-') as tmp:
            root = Path(tmp)
            (root / 'capture.json').write_text(json.dumps(source))
            (root / 'policy.json').write_text(json.dumps(policy))
            env = {'PYTHONPATH': str(ROOT / 'src'), 'PATH': '/usr/bin:/bin'}
            run = subprocess.run([sys.executable, '-m', 'agent_ee.placement_trace', 'pack',
                                  '--capture', str(root / 'capture.json'), '--policy', str(root / 'policy.json'),
                                  '--trace', str(root / 'trace.json'), '--plan', str(root / 'plan.json'),
                                  '--experiment', 'ee430-cli'], capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            document = json.loads((root / 'trace.json').read_text())
            self.assertEqual(trace_envelope.validate(document), [])
            self.assertEqual(document['identity']['experiment'], {'status': 'qualified', 'id': 'ee430-cli'})
            self.assertEqual(canonical(json.loads((root / 'plan.json').read_text())),
                             canonical(pack(copy.deepcopy(source), policy)))
            replay = subprocess.run([sys.executable, '-m', 'agent_ee.placement_trace', 'replay',
                                     str(root / 'trace.json')], capture_output=True, text=True, env=env)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)['status'], 'exact')
            document['payload']['samples'][0][7] += 1
            (root / 'wrong.json').write_text(json.dumps(document))
            replay = subprocess.run([sys.executable, '-m', 'agent_ee.placement_trace', 'replay',
                                     str(root / 'wrong.json')], capture_output=True, text=True, env=env)
            self.assertEqual(replay.returncode, 1)
            # Output paths are fresh: an existing trace is never overwritten.
            again = subprocess.run([sys.executable, '-m', 'agent_ee.placement_trace', 'pack',
                                    '--capture', str(root / 'capture.json'), '--policy', str(root / 'policy.json'),
                                    '--trace', str(root / 'trace.json')], capture_output=True, text=True, env=env)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn('FileExistsError', again.stderr)



class G0VerificationRegressionTest(unittest.TestCase):
    """Agent-510's G0 verification findings R1-R5 at 0b868441, kept as regressions (AGENTEE-430)."""
    @classmethod
    def setUpClass(cls):
        source, policy = anchor_case()
        cls.source, cls.policy = source, policy
        cls.plan, cls.document = traced(source, policy)

    def changed(self, change):
        document = copy.deepcopy(self.document)
        change(document)
        return json.loads(json.dumps(document))

    def assert_refused(self, document, reason, label):
        report = placement_trace.replay(document)
        self.assertEqual((report['status'], report['parity']), ('refused', 'none'), (label, report))
        self.assertIn(reason, report['reason'], label)

    def test_agent510_replay_reproductions(self):
        """R1/R2: the exact edits of 510's replay diagnostic; none may claim complete parity or raise."""
        report = placement_trace.replay(self.document)
        self.assertEqual((report['status'], report['parity'], report['requests'], report['samples_checked']),
                         ('exact', 'complete', 4, 6))
        self.assertEqual(self.document['counters']['stage_a.exact_resolved'], 6)
        # samples-removed: only payload.samples is cleared; the envelope still validates.
        removed = self.changed(lambda d: d['payload'].update(samples=[]))
        self.assertEqual(trace_envelope.validate(removed), [])
        self.assert_refused(removed, 'does not account for', 'samples removed')
        # failed-complete: only the state changes; the run is retained but never complete evidence.
        failed = self.changed(lambda d: d.update(state={'status': 'failed',
                                                        'reason': 'diagnostic interrupted producer'}))
        self.assertEqual(trace_envelope.validate(failed), [])
        report = placement_trace.replay(failed)
        self.assertEqual((report['status'], report['parity']), ('exact', 'partial'))
        self.assertIn('traced run failed: diagnostic interrupted producer', report['partial_reasons'])
        # unsupported-objective.
        unsupported = self.changed(lambda d: d.update(objective={'id': 'different-objective', 'version': '2'}))
        self.assert_refused(unsupported, 'objective different-objective/2 is not the supported', 'objective')
        # invalid-rotation: a typed refusal, not KeyError('45').
        self.assert_refused(self.changed(lambda d: d['payload']['samples'][0].__setitem__(4, 45)),
                            'rotation 45', 'invalid rotation')
        # The positive trace 510 retained predates the stage_a.aborted counter; it still reconciles.
        older = self.changed(lambda d: (d['counters'].pop('stage_a.aborted'),
                                        d['durations'].remove(next(r for r in d['durations']
                                                                   if r['name'] == 'stage_a.trace'))))
        self.assertEqual(placement_trace.replay(older)['parity'], 'complete')
        self.assert_refused(self.changed(lambda d: d['counters'].update({'stage_a.aborted': 1})),
                            'counters do not reconcile', 'aborted step that never ran')

    def test_zero_work_trace_is_distinguished_from_lost_samples(self):
        """R1: zero exact work whose counters agree is legitimate; zero samples against counted work is not."""
        source, policy = promotion_case()
        policy = {**policy, 'fixed_refs': [fp['ref'] for fp in source['footprints']]}
        plan, document = traced(source, policy)
        self.assertEqual(plan['status'], 'fixed_conflict')  # A typed failure before any Stage-A search.
        counters = document['counters']
        self.assertEqual((counters['stage_a.scans'], counters['stage_a.exact_resolved']), (0, 0))
        self.assertEqual((document['payload']['requests'], document['payload']['samples']), ([], []))
        report = placement_trace.replay(document)
        # Complete over the empty set of recorded work: nothing was evaluated, and nothing is missing.
        self.assertEqual((report['status'], report['parity'], report['samples_checked']), ('exact', 'complete', 0))
        # The same empty payload against counted work is refused, whatever its coverage label says.
        lost = self.changed(lambda d: (d['payload'].update(requests=[], samples=[]),
                                       d['sampling'].update(recorded=0, bytes=placement_trace.FRAME_BYTES)))
        self.assertEqual(lost['sampling']['coverage'], 'complete')
        self.assert_refused(lost, 'does not account for', 'lost samples')
        # Zero samples kept by a declared cap are partial, not complete and not refused.
        _, capped = traced(self.source, self.policy, {'samples': 0})
        self.assertEqual(capped['payload']['samples'], [])
        report = placement_trace.replay(capped)
        self.assertEqual((report['status'], report['parity']), ('exact', 'partial'))

    def test_malformed_payloads_are_typed_refusals(self):
        """R2: wrong arity and types, duplicate ids, missing rotation data and unsupported versions."""
        def request(i, key, value):
            return lambda d: d['payload']['requests'][i].__setitem__(key, value)

        def sample(i, j, value):
            return lambda d: d['payload']['samples'][i].__setitem__(j, value)
        for change, reason, label in (
                (sample(0, 4, '90'), 'must be integer', 'rotation as a string'),
                (sample(0, 4, None), 'must be integer', 'rotation null'),
                (sample(0, 4, 360), 'rotation 360', 'rotation out of range'),
                (lambda d: d['payload']['samples'][0].append(0), 'must be integer', 'sample of nine fields'),
                (lambda d: d['payload']['samples'].__setitem__(0, {'x_nm': 0}), 'must be integer', 'sample object'),
                (sample(0, 0, -1), 'unrecorded request -1', 'negative request'),
                (sample(0, 7, -5), 'non-negative', 'negative cost'),
                (sample(0, 5, '1/0x'), 'must be integer', 'malformed local cost'),
                (request(0, 'id', '0'), 'recording position', 'request id as a string'),
                (request(2, 'id', 1), 'recording position', 'duplicate request id'),
                (request(0, 'generation', -1), 'never allocated', 'negative generation'),
                (request(0, 'generated', None), 'generated must be', 'generated null'),
                (request(0, 'ref', ''), 'ref and uuid', 'empty ref'),
                (lambda d: d['payload']['requests'][0]['pads'].update({'45': []}), 'pads must give',
                 'extra rotation'),
                (lambda d: d['payload']['requests'][0]['pads'].__setitem__('0', [['N', 1]]), 'pads must give',
                 'pad row of two'),
                (lambda d: d['payload']['requests'][0].__setitem__('pads', []), 'pads must give', 'pads a list'),
                (lambda d: d['payload']['requests'][0]['peers'].update({'nowhere': [[0, 0]]}), 'peers must map',
                 'peer net without a pad'),
                (lambda d: d['payload']['requests'].__setitem__(0, None), 'not an object', 'request null'),
                (lambda d: d['payload'].update(schema='agent-ee.placement-trace/2'),
                 'not an agent-ee.placement-trace/1', 'payload version'),
                (lambda d: d['payload'].update(sample_fields=list(reversed(placement_trace.SAMPLE_FIELDS))),
                 'not an agent-ee.placement-trace/1', 'reordered sample fields'),
                (lambda d: d['consumer'].update(version='2'), 'consumer agent-ee.placement/2', 'consumer version'),
                (lambda d: d['generation'].update(id='agent-ee.placer.search-stack/2'), 'generation agent-ee.placer',
                 'generation version'),
                (lambda d: d['sampling']['limits'].pop('bytes'), 'sampling.limits must name',
                 'limits missing bytes'),
                (lambda d: d['counters'].pop('stage_a.scans'), 'counters missing stage_a.scans', 'counter missing'),
                (lambda d: d['generation']['searches'][0].pop('generations'), 'generation.searches',
                 'search without generations'),
                (lambda d: d.update(schema='pcb-trace-envelope/2'), 'unsupported envelope version', 'envelope /2')):
            self.assert_refused(self.changed(change), reason, label)
        # Random structural damage: every outcome is typed and complete parity needs an exact replay.
        rng = random.Random(510)
        junk = (None, -1, 0, 7, True, '', 'x', '1/2', [], [0], {}, {'a': 1}, 45)
        for _ in range(400):
            document = copy.deepcopy(self.document)
            node = document['payload']
            for _ in range(rng.randrange(1, 4)):
                key = rng.choice(list(node) if isinstance(node, dict) else range(len(node)))
                if not isinstance(node[key], (dict, list)) or not node[key] or rng.random() < 0.3:
                    break
                node = node[key]
            if isinstance(node, dict) and node:
                node[rng.choice(list(node))] = rng.choice(junk)
            elif isinstance(node, list) and node:
                node[rng.randrange(len(node))] = rng.choice(junk)
            report = placement_trace.replay(json.loads(json.dumps(document)))
            self.assertIn(report['status'], ('refused', 'exact', 'mismatch'))
            if report['parity'] == 'complete':
                self.assertEqual(report['status'], 'exact')

    def test_unexpected_reader_errors_are_not_refusals(self):
        """R2: only the typed refusal is a refusal; an implementation error is never reported as evidence."""
        with mock.patch.object(placement_trace, 'payload_problems', side_effect=KeyError('reader defect')):
            with self.assertRaises(KeyError):
                placement_trace.replay(self.document)
        with mock.patch('agent_ee.placer._wire_cost', side_effect=TypeError('scorer defect')):
            with self.assertRaises(TypeError):
                placement_trace.replay(self.document)

    def test_rank_time_excludes_trace_recording(self):
        """R3: 510's deterministic-clock probe: 1 ms injected per sample is recording time, not rank time."""
        class Clock:
            value = 0

            def __call__(self):
                self.value += 1
                return self.value
        clock, original = Clock(), placement_trace.Recorder.sample

        def slow_sample(recorder, *args):
            original(recorder, *args)
            clock.value += 1000000
        source, policy = promotion_case()
        with mock.patch.object(placement_trace, 'now', clock), \
                mock.patch.object(placement_trace.Recorder, 'sample', slow_sample):
            with placement_trace.recording() as recorder:
                plan = pack(copy.deepcopy(source), policy)
        self.assertEqual(plan['status'], 'planned')
        samples = len(recorder.samples)
        self.assertEqual(samples, 3)
        self.assertLess(recorder.durations['stage_a.rank'][0], 1000000)  # Was 3,000,007 at 0b868441.
        self.assertGreaterEqual(recorder.durations['stage_a.trace'][0], samples * 1000000)
        scans = recorder.counters['stage_a.scans']
        self.assertEqual(recorder.durations['stage_a.trace'][1], scans + recorder.durations['stage_a.exact'][1])

    def failed_trace(self, target, attribute):
        source, policy = promotion_case()
        with self.assertRaisesRegex(RuntimeError, 'placement sentinel'):
            with placement_trace.recording() as recorder:
                with mock.patch.object(target, attribute, side_effect=RuntimeError('placement sentinel')):
                    pack(copy.deepcopy(source), policy)
        document = placement_trace.envelope(recorder, stage='pack', state={'status': 'failed', 'reason': 'sentinel'},
                                            identity=placement_trace.pack_identity(source, policy, {}))
        return {row['name']: row for row in document['durations']}, document

    def assert_inclusive(self, durations):
        children = sum(row['value'] for row in durations.values() if row['parent'] == 'stage_a.search')
        self.assertGreater(children, 0)
        self.assertLessEqual(children, durations['stage_a.search']['value'])
        top = sum(durations[name]['value'] for name in ('prep', 'stage_a.search', 'stage_a.judge'))
        self.assertLessEqual(top, durations['pipeline']['value'])

    def test_a_raising_search_closes_its_spans(self):
        """R3: 510's predicate-exception probe, and a failure inside candidate geometry."""
        from agent_ee import placer
        durations, document = self.failed_trace(placer._Search, 'blockers')
        self.assertGreater(durations['prep']['value'], 0)  # Was 0 at 0b868441.
        self.assertGreater(durations['stage_a.search']['value'], 0)  # Was 0 at 0b868441.
        self.assertEqual(durations['stage_a.search']['count'], 1)
        self.assertEqual(durations['stage_a.predicates']['count'], 1)  # The raising predicate is timed.
        counters = document['counters']
        self.assertEqual((counters['stage_a.steps'], counters['stage_a.aborted']), (1, 1))
        self.assertEqual(document['payload']['reconciliation'], {'steps': True, 'candidates': True})
        self.assertGreater(durations['stage_a.predicates']['value'], 0)
        self.assert_inclusive(durations)
        report = placement_trace.replay(document)
        self.assertEqual((report['status'], report['parity']), ('exact', 'partial'))
        self.assertIn('traced run failed: sentinel', report['partial_reasons'])
        durations, document = self.failed_trace(placer, '_candidate_axes')
        self.assertEqual(durations['stage_a.geometry']['count'], 1)
        self.assertGreater(durations['stage_a.geometry']['value'], 0)
        self.assertEqual(durations['stage_a.bounds']['count'], 0)
        self.assert_inclusive(durations)
        self.assertEqual(placement_trace.replay(document)['parity'], 'partial')

    def test_a_raising_arrangement_closes_stage_b(self):
        from agent_ee import block_arrangement
        from test_block_arrangement import ArrangementFixture

        class Case(ArrangementFixture, unittest.TestCase):
            def runTest(self):
                pass
        case = Case()
        case.setUp()
        self.addCleanup(case.doCleanups)
        with self.assertRaisesRegex(RuntimeError, 'placement sentinel'):
            with placement_trace.recording() as recorder:
                with mock.patch.object(block_arrangement._Arrangement, 'candidates',
                                       side_effect=RuntimeError('placement sentinel')):
                    case.arrange()
        arrange, search = recorder.durations['stage_b.arrange'], recorder.durations['stage_b.search']
        self.assertEqual((arrange[1], search[1]), (1, 1))
        self.assertLessEqual(search[0], arrange[0])
        self.assertGreater(search[0], 0)

    def test_smallest_byte_cap_keeps_counters_and_reasons(self):
        """R5: limits below the empty sample set are refused before placement; at the smallest
        supported cap the counters are those of the uncapped run and the partial reason names the cap."""
        for limit in (0, 26, 27):
            with self.assertRaisesRegex(ValueError, 'byte limit must be at least 28'):
                traced(self.source, self.policy, {'bytes': limit})
        plan, document = traced(self.source, self.policy, {'bytes': placement_trace.FRAME_BYTES})
        self.assertEqual(canonical(plan), canonical(self.plan))
        self.assertEqual(document['counters'], self.document['counters'])
        sampling = document['sampling']
        self.assertEqual((sampling['coverage'], sampling['recorded'], sampling['bytes']),
                         ('partial', 0, placement_trace.FRAME_BYTES))
        self.assertTrue(any(r.startswith(f'byte limit {placement_trace.FRAME_BYTES} reached')
                            for r in sampling['partial_reasons']), sampling)
        report = placement_trace.replay(document)
        self.assertEqual((report['status'], report['parity']), ('exact', 'partial'))


class TracePersistenceTest(unittest.TestCase):
    """R4: a trace that cannot be written never replaces the placement outcome."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-ee-trace-persist-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        self.source = self.root / 'source.kicad_pcb'
        self.source.write_text('source board stand-in: only its hash is authorized\n')
        self.request = {'policy': {}, 'authorization': {
            'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
            'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
            'writable_root': str(self.scratch)}}
        self.trace = self.scratch / 'trace.json'

    @staticmethod
    def placed(source, netlist, destination, request):
        capture, policy = promotion_case()
        plan = pack(capture, policy)  # Real Stage-A work under the recorder.
        return {'status': 'placed', 'board_sha256': '0' * 64, 'plan': plan}

    def run_traced(self, placement, **patches):
        from agent_ee import placer_runner
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(placer_runner, 'place_candidate', side_effect=placement))
            stack.enter_context(mock.patch.object(placement_trace, 'native_tool_hashes', return_value=[]))
            for name, effect in patches.items():
                stack.enter_context(mock.patch.object(placement_trace, name, side_effect=effect))
            return placer_runner.traced_place_candidate(self.source, self.root / 'netlist.xml',
                                                        self.scratch / 'proposal', self.request, self.trace)

    def test_the_placement_error_survives_a_trace_write_failure(self):
        """510's probe, with a valid authorization: the placement RuntimeError, not the OSError."""
        with self.assertRaises(RuntimeError) as raised:
            self.run_traced(RuntimeError('placement sentinel'), export=OSError('trace disk full sentinel'))
        self.assertEqual(str(raised.exception), 'placement sentinel')
        secondary = raised.exception.placement_trace_error
        self.assertIsInstance(secondary, OSError)
        self.assertEqual(str(secondary), 'trace disk full sentinel')
        self.assertTrue(any('placement trace not written' in note and 'trace disk full sentinel' in note
                            for note in raised.exception.__notes__))
        self.assertFalse(self.trace.exists())
        # An assembly defect is exposed the same way; it never replaces the placement error.
        with self.assertRaises(RuntimeError) as raised:
            self.run_traced(RuntimeError('placement sentinel'), envelope=ValueError('assembly defect'))
        self.assertIsInstance(raised.exception.placement_trace_error, ValueError)
        # Without a trace failure the placement error carries none, and the failed trace is written.
        with self.assertRaises(RuntimeError) as raised:
            self.run_traced(RuntimeError('placement sentinel'))
        self.assertFalse(hasattr(raised.exception, 'placement_trace_error'))
        document = json.loads(self.trace.read_text())
        self.assertEqual(document['state'], {'status': 'failed', 'reason': 'RuntimeError: placement sentinel'})

    def test_a_placed_candidate_with_an_unwritten_trace_is_reported_as_such(self):
        from agent_ee.placer_runner import TracePersistenceError
        for effect in (OSError('trace disk full sentinel'), FileExistsError('raced')):
            with self.assertRaises(TracePersistenceError) as raised:
                self.run_traced(self.placed, export=effect)
            self.assertEqual(raised.exception.report['status'], 'placed')  # The candidate may exist.
            self.assertIs(raised.exception.__cause__, effect)
            self.assertIn('placement placed, but its trace was not written', str(raised.exception))
            self.assertFalse(self.trace.exists())
        report = self.run_traced(self.placed)
        self.assertEqual(report['status'], 'placed')
        self.assertEqual(placement_trace.replay(json.loads(self.trace.read_text()))['status'], 'exact')

    def test_command_line_reports_the_candidate_and_fails_on_an_unwritten_trace(self):
        from agent_ee import placer_runner
        (self.root / 'request.json').write_text(json.dumps(self.request))
        result = self.scratch / 'result.json'
        stderr = io.StringIO()
        with mock.patch.object(placer_runner, 'place_candidate', side_effect=self.placed), \
                mock.patch.object(placement_trace, 'native_tool_hashes', return_value=[]), \
                mock.patch.object(placement_trace, 'export', side_effect=OSError('trace disk full sentinel')), \
                contextlib.redirect_stderr(stderr):
            code = placer_runner.main([str(self.source), str(self.root / 'netlist.xml'),
                                       '--request', str(self.root / 'request.json'),
                                       '--destination', str(self.scratch / 'proposal'), '--result', str(result),
                                       '--trace', str(self.trace)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(result.read_text())['status'], 'placed')
        self.assertIn('trace disk full sentinel', stderr.getvalue())

    def test_a_failed_export_leaves_no_truncated_trace(self):
        _, document = traced(*promotion_case())
        original = Path.open

        class Full:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def write(self, data):
                self.stream.write(data[:len(data) // 2])
                self.stream.flush()
                raise OSError(28, 'No space left on device')

            def flush(self):
                self.stream.flush()

        def opened(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return Full(stream) if path == self.trace else stream
        with mock.patch.object(Path, 'open', opened):
            with self.assertRaisesRegex(OSError, 'No space left'):
                placement_trace.export(document, self.trace)
        self.assertFalse(self.trace.exists())
        self.assertGreater(placement_trace.export(document, self.trace), 0)
        self.assertEqual(json.loads(self.trace.read_text()), document)

class NativePlacementTraceTest(unittest.TestCase):
    """The traced disposable writer: identical candidate bytes, native-check and export stages."""
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        temporary = tempfile.TemporaryDirectory(prefix='agent-ee-trace-native-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()

    def files(self, directory):
        return {p.relative_to(directory).as_posix(): p.read_bytes()
                for p in sorted(directory.rglob('*')) if p.is_file()}

    def fixture(self):
        """The backjumping promotion case as an owned native board (AGENTEE-212 fixture)."""
        from test_placer_search import NativeSearchTest
        from test_placer_residuals import box, netlist
        source = NativeSearchTest.native_board(self, self.root / 'seed.kicad_pcb')
        xml = self.root / 'netlist.xml'
        xml.write_text(netlist(('F', 'P', 'X'), {'N': [('F', '1'), ('X', '1')]}))
        groups = [{'id': 'p-region', 'references': ['P'], 'region': {'polygons': [box(3.5, 0, 13.5, 8)]}}]
        config = {'policy': request(fixed_refs=['F'], priority_refs=['X'], groups=groups, step_budget=100000),
                  'skip_visuals': True,
                  'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
                                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                    'writable_root': str(self.scratch)}}
        return source, xml, config

    def test_traced_runner_writes_identical_candidates_and_a_bound_trace(self):
        from agent_ee.placer_runner import main, place_candidate
        source, xml, config = self.fixture()
        (self.root / 'request.json').write_text(json.dumps(config))
        plain = place_candidate(source, xml, self.scratch / 'off', config)
        self.assertEqual(plain['status'], 'placed', plain)
        code = main([str(source), str(xml), '--request', str(self.root / 'request.json'),
                     '--destination', str(self.scratch / 'on'), '--result', str(self.scratch / 'on.json'),
                     '--trace', str(self.scratch / 'trace.json'), '--trace-experiment', 'ee430-native'])
        self.assertEqual(code, 0)
        traced_report = json.loads((self.scratch / 'on.json').read_text())
        self.assertEqual(canonical(traced_report), canonical(json.loads(json.dumps(plain))))
        on, off = self.files(self.scratch / 'on'), self.files(self.scratch / 'off')
        self.assertEqual(set(on), set(off))
        for name in ('candidate.kicad_pcb', 'placement-plan.json', 'placement-probe.json', 'placement-judgment.json',
                     'placement-result.json'):
            self.assertEqual(on[name], off[name], name)
        # Native DRC records name their per-run staging directory and KiCad stamps each report's
        # date; those two run facts differ between any two runs. Every other byte is identical.
        run_facts = re.compile(rb'\.placement-staging-[A-Za-z0-9_]+|"date": "[0-9T:-]+"')
        self.assertEqual({k: run_facts.sub(b'RUN', v) for k, v in on.items()},
                         {k: run_facts.sub(b'RUN', v) for k, v in off.items()})
        self.assertEqual({k for k in on if on[k] != off[k]} - {
            'baseline/native-drc-execution.json', 'baseline/native-drc.json', 'baseline/native-drc.stdout.txt',
            'native-drc-execution.json', 'native-drc.json', 'native-drc.stdout.txt', 'pre-artwork-drc.json'}, set())
        document = json.loads((self.scratch / 'trace.json').read_text())
        self.assertEqual(trace_envelope.validate(document), [])
        self.assertEqual((document['stage'], document['state']),
                         ({'id': 'place-candidate'}, {'status': 'completed', 'outcome': 'placed'}))
        identity = document['identity']
        self.assertEqual(identity['pcb']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(identity['candidate']['sha256'], traced_report['board_sha256'])
        self.assertEqual(identity['candidate']['plan_sha256'], trace_envelope.canonical_sha256(traced_report['plan']))
        self.assertEqual(identity['netlist']['sha256'], hashlib.sha256(xml.read_bytes()).hexdigest())
        self.assertEqual(identity['experiment'], {'status': 'qualified', 'id': 'ee430-native'})
        tools = {row['name']: row for row in document['hashes']['native_tools']}
        self.assertEqual(tools['kicad-cli']['status'], 'qualified')
        self.assertEqual(tools['pcbnew']['status'], 'qualified')
        durations = {row['name']: row for row in document['durations']}
        for name in ('prep', 'stage_a.search', 'native_checks', 'export'):
            self.assertGreater(durations[name]['value'], 0, name)
        disjoint = sum(row['value'] for row in durations.values() if row['parent'] == 'pipeline')
        self.assertLessEqual(disjoint, durations['pipeline']['value'])
        self.assertEqual(document['counters']['stage_a.steps'], traced_report['plan']['steps_used'])
        self.assertEqual(placement_trace.replay(document)['status'], 'exact')
        # A trace path must be a fresh direct child of the writable root and not the result.
        for bad in (self.root / 'outside.json', self.scratch / 'on.json'):
            with self.assertRaisesRegex(ValueError, 'trace must be a fresh file'):
                main([str(source), str(xml), '--request', str(self.root / 'request.json'),
                      '--destination', str(self.scratch / 'again'), '--result', str(self.scratch / 'again.json'),
                      '--trace', str(bad)])

    def test_trace_writer_refuses_before_any_write(self):
        """H3 (review ee430b): authorization and the trace path are checked before recording or writing."""
        from agent_ee.placer_runner import main, traced_place_candidate
        source, xml, config = self.fixture()
        outside = self.root / 'outside.json'
        unauthorized = copy.deepcopy(config)
        unauthorized['authorization']['single_design_writer'] = False
        (self.scratch / 'existing.json').write_text('kept\n')
        (self.scratch / 'link.json').symlink_to(self.root / 'linked.json')
        (self.scratch / 'sub').mkdir()
        before = sorted(p.name for p in self.scratch.iterdir())
        for request, trace, limits, message, label in (
                # The reviewer's reproduction: refused authorization, trace beside the root.
                (unauthorized, outside, None, 'single-design-writer authorization', 'unauthorized, outside'),
                (unauthorized, self.scratch / 'trace.json', None, 'single-design-writer authorization',
                 'unauthorized, inside'),
                (config, outside, None, 'trace must be a fresh file', 'outside the root'),
                (config, self.scratch / 'link.json', None, 'trace must be a fresh file', 'symlink'),
                (config, self.scratch / 'existing.json', None, 'trace must be a fresh file', 'existing file'),
                (config, self.scratch / 'proposal', None, 'trace must be a fresh file', 'the destination'),
                (config, self.scratch / '.hidden.json', None, 'trace must be a fresh file', 'hidden name'),
                (config, self.scratch / 'sub' / 'trace.json', None, 'trace must be a fresh file', 'subdirectory'),
                (config, self.scratch / 'trace.json', {'bytes': 1}, 'byte limit', 'impossible byte limit')):
            with self.assertRaisesRegex(ValueError, message, msg=label):
                traced_place_candidate(source, xml, self.scratch / 'proposal', request, trace, limits=limits)
            self.assertFalse(outside.exists(), label)
            self.assertFalse((self.root / 'linked.json').exists(), label)
            self.assertEqual(sorted(p.name for p in self.scratch.iterdir()), before, label)
            self.assertEqual(list((self.scratch / 'sub').iterdir()), [], label)
            self.assertEqual((self.scratch / 'existing.json').read_text(), 'kept\n', label)
            self.assertIsNone(placement_trace.active())
        # The command line goes through the same boundary.
        (self.root / 'unauthorized.json').write_text(json.dumps(unauthorized))
        with self.assertRaisesRegex(ValueError, 'single-design-writer authorization'):
            main([str(source), str(xml), '--request', str(self.root / 'unauthorized.json'),
                  '--destination', str(self.scratch / 'proposal'), '--result', str(self.scratch / 'result.json'),
                  '--trace', str(self.scratch / 'trace.json')])
        self.assertEqual(sorted(p.name for p in self.scratch.iterdir()), before)

    def test_failed_placement_still_writes_a_failed_trace(self):
        from agent_ee.placer_runner import traced_place_candidate
        source, xml, config = self.fixture()
        xml = self.root / 'missing.xml'
        with self.assertRaises(OSError):
            traced_place_candidate(source, xml, self.scratch / 'proposal', config, self.scratch / 'trace.json')
        self.assertFalse((self.scratch / 'proposal').exists())
        document = json.loads((self.scratch / 'trace.json').read_text())
        self.assertEqual(trace_envelope.validate(document), [])
        self.assertEqual(document['state']['status'], 'failed')
        self.assertIn('FileNotFoundError', document['state']['reason'])
        self.assertEqual(document['sampling']['coverage'], 'partial')
        self.assertIn('traced run did not complete', document['sampling']['partial_reasons'])
        self.assertEqual(document['identity']['candidate']['status'], 'unknown')
        self.assertEqual(document['identity']['netlist'], {'status': 'unknown', 'reason': 'netlist unreadable'})


if __name__ == '__main__':
    unittest.main()
