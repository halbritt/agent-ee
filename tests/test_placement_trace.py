"""AGENTEE-430 (G0): opt-in bounded placement trace and the shared pcb-trace-envelope/1.

Fixtures are the existing original placer fixtures (search, cluster, copper-extent,
block arrangement and native runner cases); none is a third-party design.
"""
import copy
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

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
        self.assertEqual((len(result['off_ns']), len(result['on_ns']), len(result['envelope_ns'])), (2, 2, 2))
        self.assertEqual(result['gate']['kind'], 'experiment')
        self.assertIsInstance(result['median_overhead'], float)

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
