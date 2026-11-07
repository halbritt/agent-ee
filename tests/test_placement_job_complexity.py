"""Captured-geometry complexity contract, exact regions and bounded preparation.

The Rust engine owns the contour limits (AGENTEE-538): capture emits native
contours unchanged and the engine refuses an over-limit problem with the owning
footprint and pad. The boundary cases below exercise the supported limits through
the executable, independently of its source spelling.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

import portable_placement as portable

from agent_ee import placement_job as job

# Accepted geometry boundaries: exactly at the limit succeeds; one beyond it refuses.
MAX_CONTOUR_VERTICES = 2048
MAX_ITEM_CONTOURS = 16
MAX_PROBLEM_CONTOUR_VERTICES = 16384


def contour(n):
    """Original simple concave star with integer coordinates and n distinct vertices."""
    return [[round((1_000_000 if i % 2 else 900_000) * math.cos(2 * math.pi * i / n)),
             round((1_000_000 if i % 2 else 900_000) * math.sin(2 * math.pi * i / n))]
            for i in range(n)]


def item(polygons):
    return {'rect_nm': [-1_000_000, -1_000_000, 1_000_000, 1_000_000], 'clearance_nm': 0,
            'geometry': {'status': 'conservative_native_polygon', 'kind': 'custom',
                         'pad_uuid': 'pad-1', 'tolerance_nm': 1000, 'polygons': polygons}}


def problem(polygons):
    return {'schema': job.INPUT_SCHEMA, 'problem_sha256': 'a' * 64,
            'outline': {'kind': 'rectangle', 'rect_nm': [0, 0, 20_000_000, 20_000_000]},
            'rules': {'copper_clearance_nm': 0, 'copper_edge_clearance_nm': 0},
            'footprints': [{'uuid': '00000000-0000-0000-0000-000000000001', 'native_ref': 'R1',
                            'role': 'fixed_copper_mark', 'occupancy': {'status': 'none'},
                            'pose': {'x_nm': 5_000_000, 'y_nm': 5_000_000, 'quarter_turns': 0},
                            'rotations': [0], 'pads': [], 'copper_local_nm': [item(polygons)]}],
            'groups': [], 'evaluate': [{}]}


def engine(payload, timeout=60):
    done = subprocess.run([str(job.rust_executable())], input=json.dumps(payload), text=True,
                          capture_output=True, timeout=timeout)
    return done.returncode, json.loads(done.stdout)


class ComplexityTests(unittest.TestCase):
    def check_engine(self, p, message=None):
        code, response = engine(p)
        self.assertEqual(code, int(message is not None), response)
        if message:
            self.assertIn(message, response['error'])
            # The engine names the owning footprint and pad; capture adds nothing to its check.
            self.assertIn('"R1" ("00000000-0000-0000-0000-000000000001") pad "pad-1"', response['error'])
        else:
            self.assertEqual(response['status'], 'evaluated')
        return response

    def test_1118_and_per_contour_boundary_preserve_capture_bytes(self):
        for n in (1118, MAX_CONTOUR_VERTICES):
            with self.subTest(vertices=n):
                p = problem([contour(n)])
                before = copy.deepcopy(p)
                self.check_engine(p)
                self.assertEqual(p, before)
        n = MAX_CONTOUR_VERTICES + 1
        self.check_engine(problem([contour(n)]), f'contour 0 vertex count {n} exceeds {n - 1}')

    def test_per_item_contour_boundary(self):
        triangle = [[0, 0], [100, 0], [0, 100]]
        self.check_engine(problem([triangle] * MAX_ITEM_CONTOURS))
        self.check_engine(problem([triangle] * (MAX_ITEM_CONTOURS + 1)),
                        f'item contour count {MAX_ITEM_CONTOURS + 1} exceeds {MAX_ITEM_CONTOURS}')

    def test_total_vertices_boundary(self):
        p = problem([contour(MAX_CONTOUR_VERTICES)] *
                    (MAX_PROBLEM_CONTOUR_VERTICES // MAX_CONTOUR_VERTICES))
        self.check_engine(p)
        # Keep each contour within its own bound while crossing only the problem bound.
        p['footprints'][0]['copper_local_nm'][0]['geometry']['polygons'][-1] = contour(MAX_CONTOUR_VERTICES - 2)
        p['footprints'][0]['copper_local_nm'].append(item([[[0, 0], [100, 0], [0, 100]]]))
        self.check_engine(p, f'prepared contour vertex total {MAX_PROBLEM_CONTOUR_VERTICES + 1} '
                        f'exceeds {MAX_PROBLEM_CONTOUR_VERTICES}')

    def test_limit_scale_prepare_is_killed_by_controller_deadline(self):
        p = problem([contour(MAX_CONTOUR_VERTICES)] *
                    (MAX_PROBLEM_CONTOUR_VERTICES // MAX_CONTOUR_VERTICES))
        p.pop('evaluate')
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            out, execution = job.solve(p, {'seeds': [], 'steps_per_seed': 1}, tmp, deadline_s=0.002)
        self.assertEqual(out['status'], 'deadline')
        self.assertIsNone(out['timing']['prepare_s'])
        self.assertEqual(execution['interruption'], 'deadline')
        self.assertLess(time.monotonic() - started, 1)


class RetainedProblemTests(unittest.TestCase):
    """Historical ID retained; evaluate poses captured from the authored native board."""
    def test_retained_problem_prepares_and_evaluates_captured_poses(self):
        with portable.portable_fixture(self, native=True) as fixture:
            board, board_raw = portable.verified(fixture, 'board')
            _, intent_raw = portable.verified(fixture, 'intent')
            _, groups_raw = portable.verified(fixture, 'groups')
            captured, record = job.capture(board, job.normalize_intent(json.loads(intent_raw), json.loads(groups_raw)))
            portable.save(fixture, 'capture-record.json', record)
            portable.save(fixture, 'problem.json', captured)
            portable.save(fixture, 'captured-problem-original.json', captured)
            fixture['inputs']['problem'] = portable.bind(fixture['root'] / 'problem.json')
            _, raw = portable.verified(fixture, 'problem')
            portable.save(fixture, 'problem-input-identity.json', fixture['inputs']['problem'])
            payload = json.loads(raw)
            self.assertEqual(payload['problem_sha256'], hashlib.sha256(job.canonical(
                {key: value for key, value in payload.items() if key != 'problem_sha256'})).hexdigest())
            payload['evaluate'] = [{}]
            portable.save(fixture, 'evaluation-request.json', payload)
            started = time.monotonic()
            done = portable.command(fixture, [str(job.rust_executable())], 'captured-evaluation',
                                    timeout=120, input_text=json.dumps(payload))
            wall_s = time.monotonic() - started
            self.assertEqual(done.returncode, 0, done.stderr)
            response = json.loads(done.stdout)
            self.assertEqual(response['status'], 'evaluated')
            self.assertEqual(len(response['evaluations']), 1)
            self.assertGreaterEqual(response['timing']['prepare_s'], 0)
            portable.save(fixture, 'timing.json', {'process_wall_s': wall_s, 'timing': response['timing']})
            self.assertEqual(board.read_bytes(), board_raw)
            path = fixture['root'] / 'changed-problem.json'
            path.write_bytes(raw + b'\n')
            fixture['inputs']['changed_problem'] = dict(fixture['inputs']['problem'], path=str(path))
            with self.assertRaisesRegex(job.JobRefusal, 'declared sha256'):
                portable.verified(fixture, 'changed_problem')
            fixture['inputs']['missing_problem'] = dict(fixture['inputs']['problem'], path=str(fixture['root'] / 'missing-problem.json'))
            with self.assertRaises(FileNotFoundError):
                portable.verified(fixture, 'missing_problem')
            portable.save(fixture, 'negative-problem-selections.json', fixture['inputs'])
            self.assertEqual((fixture['root'] / 'captured-problem-original.json').read_bytes(), raw)



if __name__ == '__main__':
    unittest.main()
