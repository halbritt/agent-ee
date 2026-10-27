"""AGENTEE-541: the placement job normalizes intent once and hands every later step the bundle.

The original dialect shares top-level keys with the native form, so a consumer
that read the raw document could look right while missing the projection (an
edge connector's ``side`` and most of its ``context`` are removed; locality from
``inputs.locality`` is added). Each consumer here takes the bundle and refuses a
raw document; the job-level test follows the projection through real native
placement, routing and saved-board checks.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee import native_run, placement_intent, placement_job as job
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
        self.assertEqual(request, {'normalized': json.loads(json.dumps(bundle))})
        self.assertNotIn('side', request['normalized']['intent']['edge_connectors'][0])
        self.assertIn('electrical_locality', request['normalized']['intent'])
        self.assert_refuses_raw(lambda raw: job.capture('board.kicad_pcb', raw))
        # The child checks the bundle itself rather than failing on a missing key.
        self.assert_refuses_raw(lambda raw: job._capture_native('board.kicad_pcb', raw))

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
    """One normalization at entry, observed through native requests and the final saved board."""

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
        from toolchain import require_kicad_route
        import os
        require_kicad_route()
        files = {'board': self.board, 'project': self.project}
        documents = {'intent': declared, 'groups': grouped}
        if rails is not None:
            documents['group_rails'] = rails
        if located is not None:
            documents['locality'] = located
        for label, document in documents.items():
            files[label] = self.dir / (label + '.json')
            files[label].write_text(json.dumps(document))
        config = {'schema': job.CONFIG_SCHEMA, 'budget_s': 300,
                  'inputs': {label: {'path': str(path), 'sha256': job.digest(path)}
                             for label, path in files.items()},
                  'search': {'seeds': [{'seed': 0, 'start': 'scattered'}, {'seed': 1, 'start': 'clustered'}],
                             'steps_per_seed': 30000, 'shortlist': 2},
                  'router': {'checkout': os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'], 'budget_s': 60}}
        calls = []

        class ObservedHost(native_run.Host):
            def run(self, intent, **options):
                calls.append(intent)
                return super().run(intent, **options)

        # Only spy on the once-per-job contract. All consumers, native loads,
        # placement, routing and saved-board checks run through their real paths.
        with native_run.using(ObservedHost()), patch.object(
                placement_intent, 'normalize_intent', wraps=placement_intent.normalize_intent) as normalize:
            report = job.run_job(config, self.dir / 'job')
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(normalize.call_count, 1)
        requests = [(call.argv[3], json.loads(call.stdin)) for call in calls
                    if len(call.argv) > 3 and call.argv[1:3] == ('-m', 'agent_ee.placement_job')]
        captures = [request['normalized'] for command, request in requests if command == '_capture']
        self.assertEqual(len(captures), 1)
        bundle = captures[0]
        for command in ('_check_locality', '_check_edges'):
            checked = [request['intent'] for name, request in requests if name == command]
            self.assertGreaterEqual(len(checked), 2, 'candidate and final board must both be checked')
            self.assertTrue(all(intent == bundle['intent'] for intent in checked), checked)
        self.assertEqual(report['final']['integrity'], 'preserved')
        self.assertEqual(report['final']['edge_intent']['status'], 'pass')
        self.assertEqual(report['final']['locality']['status'], 'pass')
        membership = [request for command, request in requests if command == '_nets']
        return bundle, normalize.call_args, membership

    def test_native_intent_with_rails_and_locality(self):
        from test_placement_job import intent, groups
        grouped = groups()
        rails = {'schema': placement_intent.RAILS_SCHEMA,
                 'bindings': {'board_sha256': job.sha256_bytes(self.board.read_bytes()),
                              'groups_sha256': job.sha256_bytes(json.dumps(grouped).encode())},
                 'shared': {'GND': {'evidence': 'return', 'groups': ['g'], 'parts': ['R1', 'U1']}}, 'scoped': {}}
        bundle, call, membership = self.run_job(intent(), grouped, rails=rails, located=locality())
        self.assertEqual(len(membership), 1)
        self.assertEqual(call.kwargs['nets'], {'A', 'B', 'GND'})
        self.assertEqual(call.kwargs['net_parts']['GND'], {'R1', 'U1'})
        self.assertIsNone(call.kwargs['net_groups'])
        self.assertEqual(bundle['rails']['shared'], {'GND': 'return'})
        self.assertEqual(bundle['intent']['electrical_locality'], locality())

    def test_cancel_during_the_membership_read_stops_the_job_and_reaps_the_child(self):
        import os
        import threading
        import time
        from test_placement_job import intent, groups
        grouped = groups()
        rails = {'schema': placement_intent.RAILS_SCHEMA,
                 'bindings': {'board_sha256': job.sha256_bytes(self.board.read_bytes()),
                              'groups_sha256': job.sha256_bytes(json.dumps(grouped).encode())},
                 'shared': {'GND': {'evidence': 'return', 'groups': ['g']}}, 'scoped': {}}
        values = {'board': (self.board, self.board.read_bytes()), 'project': (self.project, b'{}'),
                  'intent': (self.board, json.dumps(intent()).encode()),
                  'groups': (self.board, json.dumps(grouped).encode()),
                  'group_rails': (self.board, json.dumps(rails).encode())}
        config = {'budget_s': 60, 'inputs': {k: {} for k in values}, 'search': {'shortlist': 1},
                  'router': {'checkout': 'unused'}}
        out, started = self.dir / 'job', self.dir / 'membership-started'
        shim = self.dir / 'membership-python'
        shim.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n'
                        f'Path({str(started)!r}).write_text(str(os.getpid()))\n'
                        'time.sleep(20)\n')
        shim.chmod(0o755)
        done = threading.Event()

        def cancel():
            while not done.wait(0.01):
                if started.exists():
                    (out / 'CANCEL').touch()
                    return
        thread = threading.Thread(target=cancel)
        thread.start()
        try:
            with patch.object(job, 'verified_input', side_effect=lambda _entry, label: values[label]), \
                    patch.object(job.sys, 'executable', str(shim)), \
                    patch.object(job, 'capture') as capture:
                begun = time.monotonic()
                report = job.run_job(config, out)
        finally:
            done.set()
            thread.join(timeout=3)
        self.assertEqual(report['status'], 'cancelled', report)
        self.assertLess(time.monotonic() - begun, 15)
        capture.assert_not_called()
        self.assertFalse(Path(f'/proc/{started.read_text()}').exists(), 'membership child was reaped')
        self.assertTrue(os.path.exists(out / 'CANCEL'))

    def test_dialect_intent_with_locality_reaches_every_step_projected(self):
        # Keep the original-dialect projection fields, bound to this original board.
        declared = json.loads(json.dumps(minimal_dialect()).replace('J1', 'R1'))
        declared['fixed_poses'] = [{'ref': 'FID', 'x': 3.0, 'y': 3.0, 'rot': 0.0, 'side': 'F',
                                    'basis': 'mechanical', 'why': 'fixed fiducial'}]
        declared['envelope']['rect'] = [0.0, 0.0, 20.0, 12.0]
        declared['edge_connectors'][0]['max_setback_mm'] = 12.0
        declared['proximity'] = []
        from test_placement_job import groups
        bundle, call, membership = self.run_job(declared, groups(), located=locality())
        self.assertEqual(membership, [])
        self.assertIsNone(call.kwargs['nets'])
        self.assertNotIn('side', bundle['intent']['edge_connectors'][0])
        self.assertEqual(bundle['intent']['electrical_locality'], locality())
        self.assertEqual(bundle['compatibility']['dialect']['kind'], 'floorplan-intent')


if __name__ == '__main__':
    unittest.main()
