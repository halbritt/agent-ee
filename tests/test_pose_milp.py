"""Tests for pose MILP solver contracts and candidate generation."""
import hashlib
import unittest

from agent_ee import pose_milp
from agent_ee.storage import canonical


class PoseMilpTests(unittest.TestCase):
    def setUp(self):
        self.problem = {
            'problem_sha256': '11' * 32,
            'footprints': [
                {'native_ref': 'U1', 'uuid': 'u1-uuid', 'role': 'movable', 'rotations': [0, 1, 2, 3]},
                {'native_ref': 'C1', 'uuid': 'c1-uuid', 'role': 'movable', 'rotations': [0, 1, 2, 3]},
                {'native_ref': 'J1', 'uuid': 'j1-uuid', 'role': 'fixed_body', 'rotations': [0, 1, 2, 3]}
            ]
        }
        self.diagnostic = {
            'seed': 42,
            'cost': '100',
            'terms': {'hard_count': '0', 'hard': '0'},
            'poses': [
                {'uuid': 'u1-uuid', 'native_ref': 'U1', 'x_nm': 10_000_000, 'y_nm': 20_000_000, 'quarter_turns': 0},
                {'uuid': 'c1-uuid', 'native_ref': 'C1', 'x_nm': 15_000_000, 'y_nm': 20_000_000, 'quarter_turns': 1},
                {'uuid': 'j1-uuid', 'native_ref': 'J1', 'x_nm': 0, 'y_nm': 0, 'quarter_turns': 0}
            ],
            'violations': [
                {'kind': 'body_clearance', 'ref': 'U1', 'blockers': [{'ref': 'C1'}]}
            ]
        }

    def test_schema_constants(self):
        self.assertEqual(pose_milp.SCHEMA, 'agent-ee.pose-milp/1')
        self.assertEqual(pose_milp.LEGACY_SCHEMA, 'agent-ee.acquisition-placement-proposal/1')

    def test_basis_hash(self):
        expected = hashlib.sha256(canonical(self.diagnostic)).hexdigest()
        self.assertEqual(pose_milp.basis(self.diagnostic), expected)

    def test_pose_map(self):
        poses = pose_milp.pose_map(self.diagnostic)
        self.assertEqual(poses['u1-uuid'], [10_000_000, 20_000_000, 0])
        self.assertEqual(poses['c1-uuid'], [15_000_000, 20_000_000, 1])
        self.assertEqual(poses['j1-uuid'], [0, 0, 0])

        # Duplicate UUID raises ValueError
        duplicate = dict(self.diagnostic, poses=self.diagnostic['poses'] + [self.diagnostic['poses'][0]])
        with self.assertRaisesRegex(ValueError, 'repeats a footprint UUID'):
            pose_milp.pose_map(duplicate)

    def test_active_component(self):
        active = pose_milp.active_component(self.problem, self.diagnostic)
        self.assertIn('u1-uuid', active)
        self.assertIn('c1-uuid', active)
        self.assertNotIn('j1-uuid', active)

    def test_validate_proposal(self):
        proposal = {
            'schema': pose_milp.SCHEMA,
            'problem_sha256': self.problem['problem_sha256'],
            'basis_sha256': pose_milp.basis(self.diagnostic),
            'status': 'proposed',
            'poses': pose_milp.pose_map(self.diagnostic)
        }
        # Valid proposal passes
        pose_milp.validate_proposal(self.problem, self.diagnostic, proposal)

        # Legacy schema also accepted
        legacy_proposal = dict(proposal, schema=pose_milp.LEGACY_SCHEMA)
        pose_milp.validate_proposal(self.problem, self.diagnostic, legacy_proposal)

        # Wrong schema raises ValueError
        with self.assertRaisesRegex(ValueError, 'repair proposal identity'):
            pose_milp.validate_proposal(self.problem, self.diagnostic, dict(proposal, schema='wrong-schema'))

        # Stale problem_sha256 raises ValueError
        with self.assertRaisesRegex(ValueError, 'repair proposal identity'):
            pose_milp.validate_proposal(self.problem, self.diagnostic, dict(proposal, problem_sha256='00' * 32))

        # Stale basis_sha256 raises ValueError
        with self.assertRaisesRegex(ValueError, 'repair proposal identity'):
            pose_milp.validate_proposal(self.problem, self.diagnostic, dict(proposal, basis_sha256='00' * 32))

        # Missing pose for a footprint raises ValueError
        incomplete_poses = dict(proposal['poses'])
        del incomplete_poses['u1-uuid']
        with self.assertRaisesRegex(ValueError, 'repair must cover'):
            pose_milp.validate_proposal(self.problem, self.diagnostic, dict(proposal, poses=incomplete_poses))

    def test_candidate_generation(self):
        proposal = {
            'schema': pose_milp.SCHEMA,
            'problem_sha256': self.problem['problem_sha256'],
            'basis_sha256': pose_milp.basis(self.diagnostic),
            'status': 'proposed',
            'poses': pose_milp.pose_map(self.diagnostic)
        }
        # Feasible engine evaluation generates candidate
        evaluation = {
            'feasible': True,
            'cost': '50',
            'violations': [],
            'counters': {'overlap': 0},
            'terms': {'hard_count': 0, 'hard': '0'},
            'poses': self.diagnostic['poses']
        }
        candidate = pose_milp.candidate(self.problem, self.diagnostic, proposal, evaluation)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['seed'], self.diagnostic['seed'])
        self.assertEqual(candidate['cost'], '50')
        self.assertTrue(candidate['feasible'])
        self.assertEqual(candidate['strategy'], pose_milp.SCHEMA)

        # Infeasible evaluation returns None
        infeasible_eval = {'feasible': False, 'cost': '150', 'violations': ['overlap'],
                           'terms': {'hard_count': 0, 'hard': '0'}, 'poses': self.diagnostic['poses']}
        self.assertIsNone(pose_milp.candidate(self.problem, self.diagnostic, proposal, infeasible_eval))


if __name__ == '__main__':
    unittest.main()
