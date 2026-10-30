"""Acquisition proposals remain untrusted until the original exact/native gates."""
import copy
import math
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_ee import acquisition_placement as repair
from agent_ee import placement_job as job
from owner_fixture import owner_bound
from toolchain import require_kicad_route


MM = 1_000_000


def fixture():
    """Original two overlapping movable parts, one fixed anchor, one unrelated part."""
    sources = {}
    footprints, facts, poses = [], [], []
    for number, (ref, role, x, y) in enumerate((
            ('J1', 'fixed_body', 3, 5), ('R1', 'movable', 6, 5),
            ('C1', 'movable', 6, 5), ('R9', 'movable', 10, 8)), 1):
        uuid = f'00000000-0000-4000-8000-{number:012d}'
        point = [x*MM, y*MM]
        footprints.append(dict(uuid=uuid, native_ref=ref, role=role,
            pose=dict(x_nm=point[0], y_nm=point[1], quarter_turns=0), rotations=[0, 1, 2, 3],
            occupancy=dict(local_rect_nm=[-MM, -MM//2, MM, MM//2]),
            source_body_local_nm=[-800000, -400000, 800000, 400000],
            access_local_nm=None, copper_local_nm=[dict(rect_nm=[-150000, -150000, 150000, 150000])]))
        facts.append(dict(ref=ref, pads=[dict(uuid=uuid+'-1', number='1', net='N',
            position_nm=point, copper_centroid_offset_nm=[[0, 1], [0, 1]])]))
        poses.append(dict(uuid=uuid, native_ref=ref, x_nm=point[0], y_nm=point[1],
                          quarter_turns=0, moved=False))
    obligations = []
    for a, b in [('J1', 'R1'), ('R1', 'C1')]:
        pair = {'from': dict(reference=a, pad='1', net='N'),
                'to': dict(reference=b, pad='1', net='N'), 'max_distance_nm': 3*MM,
                'citation': 'Original repair fixture: adjacent pads at most 3 mm.',
                'provenance': owner_bound('EE322', 3*MM, sources)}
        obligations.append(dict(id=a+'-'+b, metric='pad-centre-to-centre', leg=pair))
    problem = dict(schema='agent-ee.placement-job-input/2', problem_sha256='a'*64,
        outline=dict(kind='rectangle', rect_nm=[0, 0, 12*MM, 10*MM]),
        rules=dict(copper_clearance_nm=200000, copper_edge_clearance_nm=300000),
        footprints=footprints,
        locality=dict(document=dict(schema='agent-ee.electrical-locality/1',
            obligations=obligations, source_records=sources), footprints=facts))
    diagnostic = dict(seed=23, cost='100', terms=dict(hard_count=1, hard='2000000000000'),
        poses=poses, violations=[dict(kind='pair', ref='R1', with_uuid=footprints[2]['uuid'],
                                     **{'with': 'C1'})])
    return problem, diagnostic


class ProposalTests(unittest.TestCase):
    def test_incomplete_extra_malformed_and_changed_inactive_poses_refuse(self):
        problem, diagnostic = fixture()
        original = repair.pose_map(diagnostic)
        mutations = [lambda p: p.pop(next(iter(p))),
                     lambda p: p.update(unknown=[0, 0, 0]),
                     lambda p: p[next(iter(p))].__setitem__(0, True),
                     lambda p: p[next(iter(p))].__setitem__(0, 42),
                     lambda p: p[problem['footprints'][-1]['uuid']].__setitem__(1, 42)]
        for mutate in mutations:
            poses = copy.deepcopy(original)
            mutate(poses)
            with self.assertRaises(ValueError):
                repair.validate_poses(problem, diagnostic, poses)

    def test_solver_limit_without_incumbent_is_search_failure(self):
        problem, diagnostic = fixture()
        with patch.object(repair._Model, 'solve', return_value=SimpleNamespace(
                status=1, message='Time limit reached', x=None)):
            result = repair.propose(problem, diagnostic, max_seconds=1)
        self.assertEqual(result['status'], 'no_incumbent')
        self.assertNotIn('poses', result)

    def test_only_exact_regrade_can_supply_candidate_and_cost(self):
        problem, diagnostic = fixture()
        proposal = dict(schema=repair.SCHEMA, problem_sha256=problem['problem_sha256'],
            basis_sha256=repair.basis(diagnostic), status='proposed',
            poses=repair.pose_map(diagnostic))
        evaluation = dict(feasible=False, cost='456', poses=diagnostic['poses'],
                          terms=dict(hard_count=1, hard='10'), violations=['exact rejection'])
        self.assertIsNone(repair.candidate(problem, diagnostic, proposal, evaluation))
        evaluation.update(feasible=True, terms=dict(hard_count=0, hard='0'), violations=[])
        result = repair.candidate(problem, diagnostic, proposal, evaluation)
        self.assertEqual(result['cost'], '456')
        self.assertNotEqual(result['cost'], diagnostic['cost'])
        self.assertEqual(result['poses'], evaluation['poses'])
        for key, value in [('cost', None), ('poses', diagnostic['poses'][:-1])]:
            with self.assertRaises(ValueError):
                repair.candidate(problem, diagnostic, proposal, {**evaluation, key: value})
        with self.assertRaises(ValueError):
            repair.candidate(problem, diagnostic, {**proposal, 'basis_sha256': 'b'*64}, evaluation)

    def test_repair_preserves_unrelated_and_fixed_parts_and_satisfies_fixture(self):
        problem, diagnostic = fixture()
        original = copy.deepcopy((problem, diagnostic))
        checkout = require_kicad_route()
        runtime = job._acquisition_solver(checkout, {'operation': 'identity'}, clock=job.Clock(30))
        result = job._acquisition_solver(checkout, dict(operation='propose', problem=problem,
            diagnostic=diagnostic, max_seconds=10, runtime=runtime), clock=job.Clock(30))
        self.assertEqual(result['status'], 'proposed', result)
        self.assertEqual((problem, diagnostic), original)
        poses = result['poses']
        self.assertEqual(set(poses), {f['uuid'] for f in problem['footprints']})
        by_ref = {f['native_ref']: poses[f['uuid']] for f in problem['footprints']}
        self.assertEqual(by_ref['J1'], [3*MM, 5*MM, 0])
        self.assertEqual(by_ref['R9'], [10*MM, 8*MM, 0])
        for a, b in [('J1', 'R1'), ('R1', 'C1')]:
            self.assertLessEqual(math.dist(by_ref[a][:2], by_ref[b][:2]), 3*MM)
        rectangles = []
        for x, y, q in poses.values():
            self.assertIn(q, (0, 1, 2, 3))
            hx, hy = (MM, MM//2) if q % 2 == 0 else (MM//2, MM)
            rectangles.append((x-hx, y-hy, x+hx, y+hy))
            self.assertGreaterEqual(x-hx, 0)
            self.assertGreaterEqual(y-hy, 0)
            self.assertLessEqual(x+hx, 12*MM)
            self.assertLessEqual(y+hy, 10*MM)
        for i, a in enumerate(rectangles):
            for b in rectangles[i+1:]:
                self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.problem, self.diagnostic = fixture()
        self.problem['mechanics'] = {'schema': 'agent-ee.acquisition-mechanics/1'}
        self.runtime = {'identity': 'synthetic external-runtime fixture'}
        self.engine = self.directory/'engine'
        self.engine.write_text('synthetic unexecuted engine fixture')
        self.engine_sha256 = job.digest(self.engine)
        self.report = {}

    def run_repair(self, *, shortlist=None, clock=None):
        return job._repair_shortlist(self.problem, shortlist or [], [self.diagnostic],
            checkout=self.directory, runtime=self.runtime, clock=clock or job.Clock(10),
            job_dir=self.directory, attempt=1, report=self.report, executable=self.engine,
            engine_sha256=self.engine_sha256)

    def test_feasible_or_unqualified_job_never_invokes_solver(self):
        with patch.object(job, '_acquisition_solver', side_effect=AssertionError('must not run')):
            incumbent = [dict(feasible=True, seed=0, cost='1')]
            self.assertEqual(self.run_repair(shortlist=incumbent), incumbent)
            del self.problem['mechanics']
            self.assertEqual(self.run_repair(), [])
        self.assertEqual(list(self.directory.iterdir()), [self.engine])

    def test_incomplete_proposal_is_retained_and_never_reaches_exact_engine(self):
        proposal = dict(schema=repair.SCHEMA, problem_sha256=self.problem['problem_sha256'],
            basis_sha256=repair.basis(self.diagnostic), status='proposed', poses={}, runtime=self.runtime)
        with patch.object(job, '_acquisition_solver', return_value=proposal), patch.object(
                job, '_run_child', side_effect=AssertionError('partial poses must not reach engine')):
            self.assertEqual(self.run_repair(), [])
        self.assertEqual(self.report['placement_repair']['status'], 'failed')
        retained = self.directory/'acquisition-repair-a1'
        self.assertEqual(json.loads((retained/'proposal.json').read_text()), proposal)
        self.assertEqual(json.loads((retained/'basis.json').read_text()), self.diagnostic)

    def test_no_incumbent_is_retained_without_fabricated_candidate(self):
        with patch.object(job, '_acquisition_solver', return_value=dict(
                status='no_incumbent', runtime=self.runtime, solver_status=1)):
            self.assertEqual(self.run_repair(), [])
        self.assertEqual(self.report['placement_repair']['status'], 'no_incumbent')

    def test_changed_exact_evaluator_refuses_before_execution(self):
        proposal = dict(schema=repair.SCHEMA, problem_sha256=self.problem['problem_sha256'],
            basis_sha256=repair.basis(self.diagnostic), status='proposed',
            poses=repair.pose_map(self.diagnostic), runtime=self.runtime)
        self.engine.write_text('changed evaluator bytes at the same path')
        with patch.object(job, '_acquisition_solver', return_value=proposal), patch.object(
                job, '_run_child', side_effect=AssertionError('changed engine must not execute')):
            self.assertEqual(self.run_repair(), [])
        self.assertIn('evaluator changed', self.report['placement_repair']['error'])

    def test_stalled_numerical_child_is_bounded_and_leaves_routing_time(self):
        outer = job.Clock(4.2)
        argv = [sys.executable, '-I', '-B', '-c', 'import time; time.sleep(60)']
        with patch.object(repair, 'child_argv', return_value=argv):
            self.assertEqual(self.run_repair(clock=outer), [])
        self.assertEqual(self.report['placement_repair']['status'], 'deadline')
        self.assertGreater(outer.remaining(), 1)

    def test_cancel_during_numerical_child_propagates(self):
        argv = [sys.executable, '-I', '-B', '-c', 'import time; time.sleep(60)']
        timer = threading.Timer(.2, lambda: (self.directory/'CANCEL').touch())
        timer.start()
        self.addCleanup(timer.join)
        with patch.object(repair, 'child_argv', return_value=argv):
            with self.assertRaises(job.JobStopped) as stopped:
                self.run_repair()
        self.assertEqual(stopped.exception.status, 'cancelled')
        self.assertEqual(self.report['placement_repair']['status'], 'cancelled')

    def test_runtime_drift_refuses_before_numerical_work(self):
        for runtime in ({**self.runtime, 'helper_sha256': 'changed'},
                        {**self.runtime, 'files_sha256': 'changed'}):
            request = io.StringIO(json.dumps(dict(operation='propose', runtime=self.runtime)))
            with patch('sys.stdin', request), patch.object(repair, 'runtime_identity', return_value=runtime), \
                    patch.object(repair, 'propose', side_effect=AssertionError('must not run')):
                with self.assertRaisesRegex(ValueError, 'runtime changed'):
                    repair.main()

    def test_cancel_then_resume_keeps_completed_infeasible_seed_basis(self):
        state = dict(identity='fixture-identity', completed_seeds=[self.diagnostic['seed']],
            seed_results={str(self.diagnostic['seed']): dict(status='completed', feasible=False,
                diagnostic=self.diagnostic)}, attempts=1)
        job.checkpoint(self.directory, state)
        with patch.object(job, '_acquisition_solver', side_effect=job.JobStopped('cancelled')):
            with self.assertRaises(job.JobStopped):
                self.run_repair()
        restored = job.load_checkpoint(self.directory, state['identity'])
        # Engine has no new results: all seeds are already completed at resume.
        output = dict(shortlist=[], diagnostic_shortlist=[])
        diagnostics = job._search_diagnostics(output, restored)
        self.assertEqual(diagnostics, [self.diagnostic])
        with patch.object(job, '_acquisition_solver', return_value=dict(
                status='no_incumbent', runtime=self.runtime)) as numerical:
            job._repair_shortlist(self.problem, [], diagnostics, checkout=self.directory,
                runtime=self.runtime, clock=job.Clock(10), job_dir=self.directory,
                attempt=2, report={}, executable=self.engine, engine_sha256=self.engine_sha256)
        self.assertEqual(numerical.call_args.args[1]['diagnostic'], self.diagnostic)
        self.assertTrue((self.directory/'acquisition-repair-a1/result.json').is_file())
        self.assertTrue((self.directory/'acquisition-repair-a2/result.json').is_file())
