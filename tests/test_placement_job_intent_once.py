"""AGENTEE-541: the placement job normalizes intent once and hands every later step the bundle.

The original dialect shares top-level keys with the native form, so a consumer
that read the raw document could look right while missing the projection (an
edge connector's ``side`` and most of its ``context`` are removed; locality from
``inputs.locality`` is added). Each consumer here takes the bundle and refuses a
raw document; the job-level test checks that one bundle object reaches them all.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee import placement_intent, placement_job as job
from test_intent_dialect import minimal_dialect, minimal_groups

MM = 1_000_000
QUOTE = 'Engineering maximum distance: 3 mm'


def locality(near='C1'):
    return dict(schema='agent-ee.electrical-locality/1', source_records={'limit.txt': QUOTE}, obligations=[dict(
        id='R1-' + near, kind='single-leg', metric='pad-centre-to-centre', leg={
            'from': dict(reference='R1', pad='1', net='A'), 'to': dict(reference=near, pad='1', net='A'),
            'max_distance_nm': 3 * MM, 'citation': QUOTE,
            'provenance': {'class': 'engineering', 'basis': 'qualification constraint',
                           'citation': dict(kind='captured_source', path='limit.txt', locator='line 1', quote=QUOTE,
                                            sha256=hashlib.sha256(QUOTE.encode()).hexdigest())}})])


def dialect_bundle():
    return job.normalize_intent(minimal_dialect(), minimal_groups(), locality=locality())


class Reads:
    """Records each native child request instead of starting a process."""

    def __init__(self, result):
        self.result, self.requests = result, []

    def __call__(self, board_path, command, request, **_kwargs):
        self.requests.append((command, json.loads(json.dumps(request))))
        return self.result


CAPTURED = {'problem': {'schema': job.INPUT_SCHEMA}, 'record': {'schema': 'agent-ee.placement-job-capture/1'}}
EDGES = {'status': 'pass', 'coverage': ['edge_identity'], 'connectors': [], 'violations': []}
LOCALITY = {'criterion': 'electrical-locality/1', 'status': 'pass', 'measured': [], 'defects': [], 'unknown': [],
            'advisories': []}


class ProjectionTests(unittest.TestCase):
    def test_bundle_carries_the_projection_not_the_dialect_fields(self):
        bundle = dialect_bundle()
        edge = bundle['intent']['edge_connectors'][0]
        self.assertNotIn('side', edge)
        self.assertEqual(edge['context'], {'mount_mode': 'edge_mount'})
        self.assertEqual(bundle['intent']['electrical_locality'], locality())
        # The removed fields stay operative through the compatibility record that capture checks natively.
        paths = {d['path']: d for d in bundle['compatibility']['dispositions']}
        self.assertEqual(paths['intent.edge_connectors[0].side']['value'], 'F')
        self.assertEqual(paths['intent.edge_connectors[0].context.cable_entry']['value'], 'in_plane')
        self.assertEqual(paths['intent.assembly.sides']['value'], 'F')

    def test_raw_dialect_carrying_locality_inline_is_refused_like_any_unknown_field(self):
        with self.assertRaisesRegex(job.JobRefusal, 'intent.electrical_locality: unsupported original-dialect field'):
            job.normalize_intent(dict(minimal_dialect(), electrical_locality=locality()), minimal_groups())


class ConsumerTests(unittest.TestCase):
    """Every consumer reads the projection from the bundle and refuses the raw dialect document."""

    def assert_refuses_raw(self, call):
        for raw in (minimal_dialect(), {}, None):
            with self.subTest(raw=raw), patch.object(job, '_native_read') as native:
                with self.assertRaisesRegex(job.JobRefusal, 'normalized intent bundle'):
                    call(raw)
                native.assert_not_called()

    def test_capture_child_receives_the_bundle_and_never_renormalizes(self):
        bundle = dialect_bundle()
        reads = Reads(CAPTURED)
        with patch.object(job, '_native_read', reads), patch.object(placement_intent, 'normalize_intent') as again:
            job.capture('board.kicad_pcb', bundle)
        again.assert_not_called()
        [(command, request)] = reads.requests
        self.assertEqual(command, '_capture')
        self.assertEqual(request, {'normalized': {k: json.loads(json.dumps(bundle[k]))
                                                  for k in ('intent', 'groups', 'rails', 'compatibility')}})
        self.assertNotIn('side', request['normalized']['intent']['edge_connectors'][0])
        self.assertIn('electrical_locality', request['normalized']['intent'])
        self.assert_refuses_raw(lambda raw: job.capture('board.kicad_pcb', raw))

    def test_edge_check_reads_the_projected_edge_rows(self):
        bundle = dialect_bundle()
        reads = Reads(EDGES)
        with patch.object(job, '_native_read', reads):
            job.check_edge_intent('board.kicad_pcb', bundle)
        [(command, request)] = reads.requests
        self.assertEqual((command, request), ('_check_edges', {'intent': json.loads(json.dumps(bundle['intent']))}))
        self.assertEqual(request['intent']['edge_connectors'][0]['context'], {'mount_mode': 'edge_mount'})
        self.assert_refuses_raw(lambda raw: job.check_edge_intent('board.kicad_pcb', raw))

    def test_locality_check_honours_the_locality_input_for_a_dialect_intent(self):
        reads = Reads(LOCALITY)
        with patch.object(job, '_native_read', reads):
            self.assertEqual(job.check_locality('board.kicad_pcb', dialect_bundle())['status'], 'pass')
            undeclared = job.normalize_intent(minimal_dialect(), minimal_groups())
            self.assertEqual(job.check_locality('board.kicad_pcb', undeclared), {'status': 'not_declared'})
        [(command, request)] = reads.requests
        self.assertEqual(command, '_check_locality')
        self.assertEqual(request['intent']['electrical_locality'], locality())
        self.assert_refuses_raw(lambda raw: job.check_locality('board.kicad_pcb', raw))

    def test_materialize_refuses_a_raw_document_before_any_native_load(self):
        self.assert_refuses_raw(lambda raw: job.materialize('board.kicad_pcb', [], 'out', b'{}\n', normalized=raw))

    def test_final_checks_refuse_a_raw_document_before_any_work(self):
        self.assert_refuses_raw(lambda raw: job.final_checks(
            {'status': 'routed', 'board': 'routed.kicad_pcb'}, {}, {}, set(), 'final', raw))


class RailsMembershipTests(unittest.TestCase):
    def rails(self, groups):
        return {'schema': placement_intent.RAILS_SCHEMA, 'bindings': {'board_sha256': 'a' * 64, 'groups_sha256': 'b' * 64},
                'shared': {'GND': {'evidence': 'return', 'groups': list(groups)}}, 'scoped': {}}

    def test_net_groups_follow_from_the_supplied_parts(self):
        groups = {'groups': [{'name': 'g', 'anchor': 'R1', 'refs': ['R1', 'C1']},
                             {'name': 'h', 'anchor': 'U1', 'refs': ['U1']}]}
        membership = {'nets': {'GND', 'A'}, 'net_parts': {'GND': {'R1', 'U1'}, 'A': {'C1'}}}
        bound = dict(board_sha256='a' * 64, groups_sha256='b' * 64)
        bundle = job.normalize_intent({}, groups, rails=self.rails(['g', 'h']), **bound, **membership)
        self.assertEqual(bundle['rails']['shared'], {'GND': 'return'})
        with self.assertRaisesRegex(job.JobRefusal, r"group_rails.shared.GND.groups: 'g' has no pad on this net"):
            job.normalize_intent({}, groups, rails=self.rails(['g']), **bound,
                                 nets={'GND'}, net_parts={'GND': {'U1'}})

    def test_capture_child_refuses_rails_bound_to_another_board(self):
        bundle = job.normalize_intent({}, {'groups': []})
        bundle['rails'] = {'shared': {}, 'scoped': {}, 'bindings': {'board_sha256': '0' * 64, 'groups_sha256': '0' * 64}}
        with patch.object(job, '_project_rules', return_value={}), \
                patch.object(Path, 'read_bytes', return_value=b'board'):
            with self.assertRaisesRegex(job.JobRefusal, 'sidecar is bound to a different board'):
                job._capture_native('board.kicad_pcb', bundle)


class JobEntryTests(unittest.TestCase):
    """One normalization at entry; capture, materialize, both checks and final checks get that one object."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import shutil
        import tempfile
        from test_placement_job import build_board
        self.dir = Path(tempfile.mkdtemp(prefix='ee541-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.board = build_board(self.dir / 'board.kicad_pcb')
        self.project = self.dir / 'board.kicad_pro'
        self.project.write_text('{}\n')

    def run_job(self, declared, grouped, *, rails=None, located=None):
        inputs = ('board', 'project', 'intent', 'groups') + (('group_rails',) if rails else ()) + (
            ('locality',) if located else ())
        values = {'board': (self.board, self.board.read_bytes()), 'project': (self.project, b'{}'),
                  'intent': (self.board, json.dumps(declared).encode()),
                  'groups': (self.board, json.dumps(grouped).encode())}
        if rails:
            values['group_rails'] = (self.board, json.dumps(rails).encode())
        if located:
            values['locality'] = (self.board, json.dumps(located).encode())
        config = {'budget_s': 60, 'inputs': {k: {} for k in inputs}, 'search': {'shortlist': 1},
                  'router': {'checkout': 'unused'}}
        output = {'timing': {'prepare_s': 0}, 'status': 'completed', 'counters': {}, 'seeds': [], 'prng': 'probe',
                  'shortlist': [{'seed': 0, 'cost': '1', 'poses': [0]}]}
        problem = {'problem_sha256': 'p', 'rails': {}}
        seen = {}

        def remember(name, result):
            def call(*args, **kwargs):
                bundle = kwargs.get('normalized', next((a for a in args if isinstance(a, dict) and 'intent_contract' in a),
                                                       None))
                seen.setdefault(name, []).append(bundle)
                return result(*args, **kwargs) if callable(result) else result
            return call

        def materialized(*_args, **_kwargs):
            entry = {'index': 0, 'seed': 0, 'board_sha256': 'fixture', 'errors': {}, 'engine_violations': [],
                     'directory': str(self.dir), 'board': str(self.board)}
            (self.dir / 'job' / 'incumbent.json').write_text(json.dumps(entry))
            return {'board': str(self.board), 'board_sha256': 'fixture', 'integrity': 'preserved', 'problems': []}

        routed = {'status': 'routed', 'board': str(self.board), 'preflight': {
            k: None for k in ('commit', 'pin', 'executable_sha256', 'adapter_sha256', 'problems')}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label: values[label]))
            stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe'}))
            normalize = stack.enter_context(patch.object(placement_intent, 'normalize_intent',
                                                         wraps=placement_intent.normalize_intent))
            membership = stack.enter_context(patch.object(job, '_rail_membership', wraps=job._rail_membership))
            stack.enter_context(patch.object(job, 'capture', side_effect=remember('capture', (problem, {}))))
            stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
            stack.enter_context(patch.object(job, 'materialize', side_effect=remember('materialize', materialized)))
            stack.enter_context(patch.object(job, 'check_locality', side_effect=remember('locality', {'status': 'pass'})))
            stack.enter_context(patch.object(job, 'check_edge_intent', side_effect=remember('edges', {'status': 'pass'})))
            stack.enter_context(patch.object(job, 'promote', return_value=True))
            stack.enter_context(patch.object(job, 'retain_diagnostic', return_value=False))
            stack.enter_context(patch.object(job, 'drc', return_value=({'violations': [], 'unconnected_items': []}, {})))
            stack.enter_context(patch.object(job, 'route', return_value=routed))
            stack.enter_context(patch.object(job, 'final_checks', side_effect=remember('final', {
                'integrity': 'preserved', 'edge_intent': {'status': 'pass'}, 'eligibility': 'eligible',
                'physical_checks': 'PASS'})))
            report = job.run_job(config, self.dir / 'job')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(normalize.call_count, 1)
        bundles = [b for name in ('capture', 'materialize', 'locality', 'edges', 'final') for b in seen[name]]
        self.assertTrue(all(b is bundles[0] for b in bundles), seen)
        return bundles[0], normalize.call_args, membership

    def test_native_intent_with_rails_and_locality(self):
        from test_placement_job import intent, groups
        grouped = groups()
        rails = {'schema': placement_intent.RAILS_SCHEMA,
                 'bindings': {'board_sha256': job.sha256_bytes(self.board.read_bytes()),
                              'groups_sha256': job.sha256_bytes(json.dumps(grouped).encode())},
                 'shared': {'GND': {'evidence': 'return', 'groups': ['g'], 'parts': ['R1', 'U1']}}, 'scoped': {}}
        bundle, call, membership = self.run_job(intent(), grouped, rails=rails, located=locality())
        self.assertEqual(membership.call_count, 1)
        self.assertEqual(call.kwargs['nets'], {'A', 'B', 'GND'})
        self.assertEqual(call.kwargs['net_parts']['GND'], {'R1', 'U1'})
        self.assertIsNone(call.kwargs['net_groups'])
        self.assertEqual(bundle['rails']['shared'], {'GND': 'return'})
        self.assertEqual(bundle['intent']['electrical_locality'], locality())

    def test_dialect_intent_with_locality_reaches_every_step_projected(self):
        bundle, call, membership = self.run_job(minimal_dialect(), minimal_groups(), located=locality())
        membership.assert_not_called()
        self.assertIsNone(call.kwargs['nets'])
        self.assertNotIn('side', bundle['intent']['edge_connectors'][0])
        self.assertEqual(bundle['intent']['electrical_locality'], locality())
        self.assertEqual(bundle['compatibility']['dialect']['kind'], 'floorplan-intent')


if __name__ == '__main__':
    unittest.main()
