"""Historical C benchmark adapter: exact arithmetic, safe ABI and planner parity."""
import _ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from agent_ee import placement_scoring as scoring, placer
from test_placer_search import anchor_case, promotion_case

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import placement_scoring_c as c


class HistoricalCScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('AGENT_EE_C_SCORE_LIBRARY')
        if not configured and shutil.which('cc') is None:
            reason = 'toolchain prerequisite: cc missing and no AGENT_EE_C_SCORE_LIBRARY'
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.library = Path(configured) if configured else c.build(Path(cls.tmp.name) / 'historical-c.so')

    def snapshot(self, offsets=(0, 0), peer=(0, 0), count=1):
        return scoring.Snapshot.capture(0, {str(r): [('N', *offsets)] * count for r in c.ROTATIONS},
                                        {'N': [peer]})

    def session(self, check=False):
        session = c.Session(self.library, check=check)
        self.assertIsNotNone(session.native, session.events)
        return session

    def test_historical_kernel_bytes_and_provenance_match_the_original_pin(self):
        provenance = json.loads(c.SOURCE.with_suffix('.json').read_bytes())
        self.assertEqual(hashlib.sha256(c.SOURCE.read_bytes()).hexdigest(),
                         '1828e54cc2b44e4187a23f48ecf19a0b4bd3b61b1ad31969535ff86dec04248c')
        self.assertEqual(provenance['source_sha256'], c.SOURCE_SHA256)
        self.assertEqual(provenance['source_commit'], c.ORIGINAL_COMMIT)
        self.assertEqual(provenance['source_modifications'], [])

    def test_multinet_duplicates_rotations_and_extreme_coordinates_match_python(self):
        rng = random.Random(524)
        native = self.session().native
        for scale in (10, 10**9, 2**58, scoring.LIMIT):
            for case in range(8):
                peers = {net: [(rng.randint(-scale, scale), rng.randint(-scale, scale))
                               for _ in range(count)] for net, count in (('N', 1), ('M', 7), ('WIDE', 80))}
                pads = {str(r): [(rng.choice(('N', 'M', 'WIDE', 'MISSING')),
                                 rng.randint(-scale, scale), rng.randint(-scale, scale))
                                for _ in range(rng.randint(0, 7))] for r in c.ROTATIONS}
                for rows in pads.values():
                    rows.extend(rows[:1])  # Duplicate physical pads remain separate cost terms.
                snapshot = scoring.Snapshot.capture(case, pads, peers)
                packed = native.prepare(snapshot)
                poses = [(rng.randint(-scale, scale), rng.randint(-scale, scale), r)
                         for r in c.ROTATIONS for _ in range(3)]
                expected = scoring.reference(snapshot, poses)
                with self.subTest(scale=scale, case=case):
                    if max(expected) > scoring.LIMIT:
                        with self.assertRaises(OverflowError):
                            native.score(packed, poses)
                    else:
                        self.assertEqual(native.score(packed, poses), expected)
                    for pose, value in zip(poses, expected):
                        if value > scoring.LIMIT:
                            with self.assertRaises(OverflowError):
                                native.one(packed, *pose)
                        else:
                            self.assertEqual(native.one(packed, *pose), value)
        # Intermediate translated coordinates exceed int64, but the final cost fits.
        snapshot = self.snapshot((scoring.LIMIT, 0), (scoring.LIMIT, 0))
        self.assertEqual(native.one(native.prepare(snapshot), scoring.LIMIT, 0, 0), scoring.LIMIT)
        self.assertEqual(native.score(native.prepare(snapshot), []), [])

    def test_fast_scalar_and_batch_do_not_recompute_the_python_oracle(self):
        snapshot = self.snapshot((3, -4), (20, 30), 2)
        poses = [(0, 0, 0), (5, -10, 270), (-9, 20, 180)]
        expected = scoring.reference(snapshot, poses)
        scorer = self.session().scorer(snapshot)
        called = []
        oracle_codes = {scoring.reference.__code__, placer._wire_cost.__code__}

        def profile(frame, event, arg):
            if event == 'call' and frame.f_code in oracle_codes:
                called.append(frame.f_code.co_name)

        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            batch = scorer.score_batch(poses, generation=0)
            scalar = [scorer.score(*pose, generation=0) for pose in poses]
        finally:
            sys.setprofile(previous)
        self.assertEqual((batch, scalar), (expected, expected))
        self.assertEqual(called, [])
        self.assertEqual(scorer.session.events, [])

    def test_overflow_discards_the_whole_batch_and_records_sticky_fallback(self):
        cases = [(self.snapshot(), [(0, 0, 0), (scoring.LIMIT, 1, 0)]),
                 (self.snapshot(count=2), [(scoring.LIMIT, 0, 0)]),
                 (self.snapshot(peer=(-scoring.LIMIT-1, 0)), [(scoring.LIMIT, 0, 0)]),
                 (self.snapshot((scoring.LIMIT+1, 0)), [(0, 0, 0)]),
                 (self.snapshot(), [(scoring.LIMIT+1, 0, 0)])]
        for snapshot, poses in cases:
            for check in (False, True):
                with self.subTest(snapshot=snapshot, poses=poses, check=check):
                    session = self.session(check)
                    scorer = session.scorer(snapshot)
                    expected = scoring.reference(snapshot, poses)
                    self.assertEqual(scorer.score_batch(poses, generation=0), expected)
                    self.assertIsNone(session.native)
                    self.assertEqual(len(session.events), 1)
                    self.assertEqual([scorer.score(*p, generation=0) for p in poses], expected)
                    self.assertEqual(len(session.events), 1)

    def test_malformed_inputs_are_rejected_before_crossing_the_c_abi(self):
        native = self.session().native
        packed = native.prepare(self.snapshot())
        with mock.patch.object(native, 'function', side_effect=AssertionError('malformed C ABI call')):
            for pose in ((0, 0), (0, 0, 0, 0), (0, 0, False), (0, 0, 45),
                         (0, 0, 2**64), (True, 0, 0), (0, 1.0, 0), (0, '1', 0),
                         (scoring.LIMIT+1, 0, 0), (-scoring.LIMIT-2, 0, 0)):
                with self.subTest(pose=pose), self.assertRaises(ValueError):
                    native.score(packed, [pose])
            for count in (3, 5):
                with self.subTest(rotations=count), self.assertRaises(ValueError):
                    native.prepare(scoring.Snapshot(0, ((),)*count, ()))
            for points in ((), ((1,),), ((1, 2, 3),), ((True, 0),), ((scoring.LIMIT+1, 0),)):
                snapshot = scoring.Snapshot(0, ((('N', 0, 0),),)*4, (('N', points),))
                with self.subTest(points=points), self.assertRaises(ValueError):
                    native.prepare(snapshot)

    def test_checked_c_corruption_discards_results_and_keeps_reference_selected(self):
        session = self.session(check=True)
        snapshot = self.snapshot((5, 8), (12, 15))
        scorer = session.scorer(snapshot)
        poses = [(0, 0, 0), (4, 3, 0)]
        expected = scoring.reference(snapshot, poses)
        function = session.native.function

        def corrupt(*args):
            status = function(*args)
            args[-1][0] += 1
            return status

        with mock.patch.object(session.native, 'function', side_effect=corrupt):
            self.assertEqual(scorer.score_batch(poses, generation=0), expected)
        self.assertIsNone(session.native)
        self.assertEqual(len(session.events), 1)
        self.assertIn('mismatch', session.events[0]['reason'])
        self.assertEqual(scorer.score(*poses[0], generation=0), expected[0])

    def test_unavailable_library_falls_back_visibly_and_stale_snapshots_are_rejected(self):
        snapshot = self.snapshot((1, 2), (4, 8))
        for path in (Path(self.tmp.name)/'missing.so', Path(_ctypes.__file__)):
            with self.subTest(path=path):
                session = c.Session(path)
                self.assertIsNone(session.native)
                self.assertIn('C library unavailable', session.events[0]['reason'])
                self.assertEqual(session.scorer(snapshot).score(0, 0, 0, generation=0), 9)
        scorer = self.session().scorer(snapshot)
        with self.assertRaisesRegex(ValueError, 'stale'):
            scorer.score(0, 0, 0, generation=1)

    def test_opt_in_plans_match_default_and_context_is_restored_after_exception(self):
        before = scoring.active()
        for case in (promotion_case, anchor_case):
            source, policy = case()
            expected = placer.pack(source, policy)
            with c.experimental(self.library) as session:
                with mock.patch.object(session.native, 'function', wraps=session.native.function) as function:
                    actual = placer.pack(source, policy)
                self.assertGreater(function.call_count, 0)
                self.assertEqual(session.events, [])
            self.assertIs(scoring.active(), before)
            self.assertEqual(actual, expected)
        with self.assertRaisesRegex(RuntimeError, 'context failure'):
            with c.experimental(self.library):
                raise RuntimeError('context failure')
        self.assertIs(scoring.active(), before)


if __name__ == '__main__':
    unittest.main()
