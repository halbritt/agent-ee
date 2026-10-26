"""Captured-geometry complexity contract, exact regions and bounded preparation.

The Rust engine owns the contour limits (AGENTEE-538): capture emits native
contours unchanged and the engine refuses an over-limit problem with the owning
footprint and pad. The limits below are read from job.rs, their only copy.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest

from agent_ee import placement_job as job

_ENGINE_SOURCE = (Path(__file__).resolve().parents[1] / 'crates/placement-engine/src/job.rs').read_text()
MAX_CONTOUR_VERTICES, MAX_ITEM_CONTOURS, MAX_PROBLEM_CONTOUR_VERTICES = (
    int(re.search(r'pub const ' + name + r': usize = (\d+);', _ENGINE_SOURCE)[1])
    for name in ('MAX_CONTOUR_VERTICES', 'MAX_ITEM_CONTOURS', 'MAX_PROBLEM_CONTOUR_VERTICES'))


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
    def test_retained_problem_prepares_and_evaluates_captured_poses(self):
        path = os.environ.get('AGENT_EE_515_E2E_PROBLEM')
        if not path:
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                self.fail('AGENT_EE_REQUIRE_TOOLCHAIN=1: retained problem required (AGENT_EE_515_E2E_PROBLEM)')
            self.skipTest('external retained problem not mounted')
        raw = Path(path).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '2ccd682b4bf36ea2aa2d2dcc8bca05f1770cd5927df6eb67142ba70bbf626f11')
        p = json.loads(raw)
        p['evaluate'] = [{}]
        started = time.monotonic()
        code, response = engine(p, timeout=120)
        wall_s = time.monotonic() - started
        self.assertEqual(code, 0, response)
        self.assertEqual(response['status'], 'evaluated')
        self.assertEqual(len(response['evaluations']), 1)
        print(json.dumps({'case': 'retained-problem', 'prepare_s': response['timing']['prepare_s'],
                          'process_wall_s': wall_s, 'timing': response['timing']}), flush=True)


if __name__ == '__main__':
    unittest.main()
