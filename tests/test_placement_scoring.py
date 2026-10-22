"""Rust placement scoring: exact costs/ranks, lazy plans and explicit fallback."""
import _ctypes
import contextlib
import copy
from fractions import Fraction
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import random
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import weakref

from agent_ee import placement_scoring as scoring, placement_trace, placer
from test_placer import request
from test_placer_search import anchor_case, promotion_case
from test_placer_clusters import random_cluster_case, three_cap_case
from test_placer_copper_extent import board, offset_part


def corpus():
    rng = random.Random(430)
    return [promotion_case(), anchor_case(), three_cap_case(),
            (board([offset_part()]), request(fixed_refs=[]))] + [random_cluster_case(rng) for _ in range(30)]


class CompiledScoringFixture:
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('AGENT_EE_PLACEMENT_SCORE_LIBRARY')
        if not configured and shutil.which('rustc') is None:
            reason = 'toolchain prerequisite: rustc not found on PATH and no prebuilt placement scorer configured'
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
            raise unittest.SkipTest(reason)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.library = (Path(configured) if configured
                       else scoring.build(Path(cls.tmp.name) / 'scoring.so'))

    def session(self, *, check=False):
        session = scoring.Session(self.library, check=check)
        self.assertIsNotNone(session.native, session.events)
        return session

    def snapshot(self, generation=0):
        # Asymmetric pads, nonzero initial rotation, duplicate physical terms,
        # shared net, no-peer, ground and unconnected pads use the existing transform.
        fp = {'position_nm': [20, -40], 'rotation_deg': 90,
              'pads': [{'net': net, 'position_nm': point} for net, point in
                       [('N', [23, -35]), ('N', [23, -35]), ('N', [17, -42]),
                        ('MISSING', [20, -40]), ('GND', [99, 100]), ('', [100, 200])]]}
        pads = {str(r): placer._pad_nets(fp, (0, 0), r, ['GND']) for r in scoring.ROTATIONS}
        return scoring.Snapshot.capture(generation, pads, {'N': [[-30, 11], [48, -19], [0, 0]]})


class ScoringTest(CompiledScoringFixture, unittest.TestCase):
    def test_all_rotations_duplicates_shared_nets_and_complete_rank_are_exact(self):
        snapshot = self.snapshot()
        queries = [(x, y, r) for x in range(-20, 21, 5) for y in range(-20, 21, 5)
                   for r in scoring.ROTATIONS]
        expected = scoring.reference(snapshot, queries)
        for check in (False, True):
            with self.subTest(check=check):
                scorer = self.session(check=check).scorer(snapshot)
                costs = scorer.score_batch(queries, generation=0)
                self.assertEqual(costs, expected)
                self.assertEqual([scorer.score(*q, generation=0) for q in queries], expected)
                # Preserve fractional locality and every tie-break component.
                bounds = [(Fraction(i % 3, 7), 0, *q, bool(i % 2)) for i, q in enumerate(queries)]
                lookup = dict(zip(queries, costs))
                exact = lambda k: (k[0], lookup[k[2:5]], *k[2:])
                self.assertEqual(list(placer._ranked(bounds, exact)), sorted(map(exact, bounds)))
                self.assertEqual(scorer.session.events, [])

    def test_native_fast_path_does_not_recompute_python_oracle(self):
        snapshot = self.snapshot()
        queries = [(0, 0, 0), (1, -2, 270), (22, 9, 180)]
        expected = scoring.reference(snapshot, queries)
        scorer = self.session().scorer(snapshot)
        oracle_calls = []
        oracle_codes = {scoring.reference.__code__, placer._wire_cost.__code__}

        def profile(frame, event, arg):
            if event == 'call' and frame.f_code in oracle_codes:
                oracle_calls.append(frame.f_code.co_name)

        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            batch = scorer.score_batch(queries, generation=0)
            scalar = [scorer.score(*q, generation=0) for q in queries]
        finally:
            sys.setprofile(previous)
        self.assertEqual((batch, scalar), (expected, expected))
        self.assertEqual(oracle_calls, [])
        self.assertEqual(scorer.session.events, [])

    def test_snapshot_is_immutable_and_stale_generation_is_refused(self):
        pads = {str(r): [['N', 1, 2]] for r in scoring.ROTATIONS}
        peers = {'N': [[3, 4]]}
        snapshot = scoring.Snapshot.capture((1, 0), pads, peers)
        scorer = self.session().scorer(snapshot)
        pads['0'][0][1] = 999
        peers['N'][0][0] = 999
        self.assertEqual(scorer.score_batch([(0, 0, 0)], generation=(1, 0)), [4])
        self.assertEqual(scorer.score(0, 0, 0, generation=(1, 0)), 4)
        for epoch in ((2, 1), (2, 0)):
            with self.assertRaisesRegex(ValueError, 'stale'):
                scorer.score_batch([(0, 0, 0)], generation=epoch)
            with self.assertRaisesRegex(ValueError, 'stale'):
                scorer.score(0, 0, 0, generation=epoch)

    def test_checked_mismatch_discards_entire_result_and_fallback_sticks(self):
        snapshot = self.snapshot()
        queries = [(0, 0, 0), (1, -2, 270)]
        expected = scoring.reference(snapshot, queries)
        for scalar in (False, True):
            with self.subTest(scalar=scalar):
                session = self.session(check=True)
                scorer = session.scorer(snapshot)
                method = 'one' if scalar else 'score'
                wrong = expected[0] + 1 if scalar else [expected[0], expected[1] + 1]
                with mock.patch.object(session.native, method, return_value=wrong) as fault:
                    if scalar:
                        self.assertEqual(scorer.score(*queries[0], generation=0), expected[0])
                    else:
                        self.assertEqual(scorer.score_batch(queries, generation=0), expected)
                    # Both APIs and subsequently prepared scorers must use the fallback.
                    self.assertEqual(scorer.score_batch(queries, generation=0), expected)
                    self.assertEqual(session.scorer(snapshot).score(*queries[1], generation=0), expected[1])
                    self.assertEqual(fault.call_count, 1)
                self.assertIsNone(session.native)
                self.assertEqual(len(session.events), 1)
                self.assertIn('mismatch', session.events[0]['reason'])

    def test_missing_or_incompatible_library_reports_reference_operation(self):
        snapshot = self.snapshot()
        queries = [(0, 0, 0), (3, -8, 90)]
        for path in (Path(self.tmp.name) / 'absent.so', Path(_ctypes.__file__)):
            with self.subTest(path=path):
                session = scoring.Session(path)
                self.assertIsNone(session.native)
                scorer = session.scorer(snapshot)
                expected = scoring.reference(snapshot, queries)
                self.assertEqual(scorer.score_batch(queries, generation=0), expected)
                self.assertEqual(scorer.score(*queries[0], generation=0), expected[0])
                self.assertIn('unavailable', session.events[0]['reason'])

    def test_prepared_allocation_lives_with_its_reader_and_is_released_once(self):
        snapshot = self.snapshot()
        queries = [(0, 0, 0), (3, -8, 90)]
        expected = scoring.reference(snapshot, queries)
        native = self.session().native
        with mock.patch.object(native, 'free', wraps=native.free) as released:
            packed = native.prepare(snapshot)
            retained = packed
            observer = weakref.ref(packed)
            del packed
            gc.collect()
            self.assertIsNotNone(observer())
            self.assertEqual(native.score(retained, queries), expected)
            self.assertEqual(released.call_count, 0)
            del retained
            gc.collect()
            self.assertIsNone(observer())
            self.assertEqual(released.call_count, 1)
            gc.collect()
            self.assertEqual(released.call_count, 1)

    def test_malformed_peer_coordinates_are_refused_before_native_packing(self):
        pads = {str(r): [('N', 0, 0)] for r in scoring.ROTATIONS}
        for point in ((1,), (1, 2, 3)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                scoring.Snapshot.capture(0, pads, {'N': [point]})

    def test_low_level_pose_validation_prevents_short_buffers_and_rotation_wrapping(self):
        native = self.session().native
        packed = native.prepare(self.snapshot())
        with mock.patch.object(native, 'prepare_function', side_effect=AssertionError('short rotations reached FFI')):
            for count in (3, 5):
                snapshot = scoring.Snapshot(0, ((),) * count, ())
                with self.subTest(rotations=count), self.assertRaises(ValueError):
                    native.prepare(snapshot)
        with mock.patch.object(native, 'batch_function', side_effect=AssertionError('invalid buffer reached FFI')):
            for pose in ((0, 0), (0, 0, 0, 0)):
                with self.subTest(pose=pose), self.assertRaises(ValueError):
                    native.score(packed, [pose])
        with mock.patch.object(native, 'one_function', side_effect=AssertionError('invalid rotation reached FFI')):
            for rotation in (False, 45, 2**64):
                with self.subTest(rotation=rotation), self.assertRaises(ValueError):
                    native.one(packed, 0, 0, rotation)

    def test_native_exception_falls_back_in_fast_and_checked_modes(self):
        snapshot = self.snapshot()
        queries = [(0, 0, 0), (3, -8, 90)]
        expected = scoring.reference(snapshot, queries)
        for check in (False, True):
            for scalar in (False, True):
                with self.subTest(check=check, scalar=scalar):
                    session = self.session(check=check)
                    scorer = session.scorer(snapshot)
                    method = 'one' if scalar else 'score'
                    with mock.patch.object(session.native, method, side_effect=OSError('native failure')):
                        if scalar:
                            self.assertEqual(scorer.score(*queries[0], generation=0), expected[0])
                        else:
                            self.assertEqual(scorer.score_batch(queries, generation=0), expected)
                    self.assertEqual(scorer.score_batch(queries, generation=0), expected)
                    self.assertIsNone(session.native)
                    self.assertIn('native failure', session.events[0]['reason'])

    def test_int64_boundaries_preserve_python_results_without_partial_output(self):
        limit = scoring.LIMIT
        for offsets, peer, pose, overflow in (
                ((0, 0), (limit, 0), (0, 0, 0), False),
                ((0, 0), (-limit-1, 0), (limit, 0, 0), True),
                # The first row succeeds; the Rust batch overflows on the second.
                ((0, 0), (0, 0), (limit, 1, 0), True),
                ((limit+1, 0), (0, 0), (0, 0, 0), True),
                ((0, 0), (0, 0), (limit+1, 0, 0), True),
                # A transformed coordinate may exceed int64 while the cost fits.
                ((limit, 0), (limit, 0), (limit, 0, 0), None)):
            snapshot = scoring.Snapshot.capture(0, {str(r): [('N', *offsets)] for r in scoring.ROTATIONS},
                                                {'N': [peer]})
            for scalar in (False, True):
                with self.subTest(offsets=offsets, peer=peer, pose=pose, scalar=scalar):
                    session = self.session()
                    scorer = session.scorer(snapshot)
                    poses = [(0, 0, 0), pose]
                    expected = scoring.reference(snapshot, poses)
                    actual = ([scorer.score(*q, generation=0) for q in poses] if scalar
                              else scorer.score_batch(poses, generation=0))
                    self.assertEqual(actual, expected)
                    if overflow:
                        self.assertIsNone(session.native)
                        self.assertTrue(session.events)
                    elif overflow is False:
                        self.assertEqual(session.events, [])
        # Each physical pad term fits; their sum does not.
        snapshot = scoring.Snapshot.capture(0, {str(r): [('N', 0, 0)] * 2 for r in scoring.ROTATIONS},
                                            {'N': [(limit, 0)]})
        session = self.session()
        self.assertEqual(session.scorer(snapshot).score_batch([(0, 0, 0)], generation=0), [2*limit])
        self.assertIsNone(session.native)

    def test_seeded_random_multinet_nearest_costs_and_extreme_integers_match(self):
        rng = random.Random(524)
        for scale in (1, 10**9, 2**58, scoring.LIMIT):
            for case in range(20):
                # Varied net cardinality reaches both short and long nearest-peer scans.
                peers = {net: [(rng.randint(-scale, scale), rng.randint(-scale, scale))
                               for _ in range(count)]
                         for net, count in (('N', 1), ('M', 7), ('WIDE', 80))}
                pads = {str(r): [(rng.choice(('N', 'M', 'WIDE', 'MISSING')),
                                 rng.randint(-scale, scale), rng.randint(-scale, scale))
                                for _ in range(rng.randint(0, 9))] for r in scoring.ROTATIONS}
                snapshot = scoring.Snapshot.capture(case, pads, peers)
                poses = [(rng.randint(-scale, scale), rng.randint(-scale, scale),
                          rng.choice(scoring.ROTATIONS)) for _ in range(12)]
                expected = scoring.reference(snapshot, poses)
                with self.subTest(scale=scale, case=case):
                    scorer = self.session().scorer(snapshot)
                    self.assertEqual(scorer.score_batch(poses, generation=case), expected)
                    scalar = self.session().scorer(snapshot)
                    self.assertEqual([scalar.score(*q, generation=case) for q in poses], expected)
                    if scale <= 10**9:
                        self.assertEqual(scorer.session.events, [])
                        self.assertEqual(scalar.session.events, [])

    def test_empty_signal_sets_and_invalid_poses(self):
        snapshot = scoring.Snapshot.capture(0, {str(r): [] for r in scoring.ROTATIONS}, {})
        scorer = self.session().scorer(snapshot)
        self.assertEqual(scorer.score_batch([(0, 0, r) for r in scoring.ROTATIONS], generation=0), [0]*4)
        self.assertEqual(scorer.score_batch([], generation=0), [])
        self.assertEqual(scorer.score(0, 0, 0, generation=0), 0)
        for pose in ((0, 0, 45), (True, 0, 0), (1.5, 0, 0), (0, 0, False)):
            with self.assertRaises(ValueError, msg=pose):
                scorer.score_batch([pose], generation=0)
            with self.assertRaises(ValueError, msg=pose):
                scorer.score(*pose, generation=0)

    def test_corpus_preserves_scores_lazy_evaluations_and_full_plans(self):
        for source, policy in corpus():
            with self.subTest(refs=len(source['footprints'])):
                reference_plan, reference_trace = placement_trace.traced_pack(source, policy)
                with scoring.experimental(self.library) as session:
                    actual_plan, actual_trace = placement_trace.traced_pack(source, policy)
                self.assertEqual(actual_plan, reference_plan)
                self.assertEqual(actual_trace['payload'], reference_trace['payload'])
                self.assertEqual(actual_trace['counters'], reference_trace['counters'])
                self.assertEqual(session.events, [])
                for row in placement_trace.stage_a_inputs(reference_trace):
                    snapshot = scoring.Snapshot.capture(row['generation'], row['pads'], row['peers'])
                    scorer = self.session().scorer(snapshot)
                    self.assertEqual(scorer.score_batch(row['queries'], generation=row['generation']),
                                     row['expected_cost_nm'])

    def test_complete_rank_sequence_for_every_generated_fixture_candidate(self):
        # This instrumentation exhausts the ranker, including candidates that the
        # production lazy search never resolves. Both arms get the same probe.
        ranker = placer._ranked
        for source, policy in corpus():
            sequences = []

            def complete(bounds, exact):
                sequence = list(ranker(bounds, exact))
                sequences.append(sequence)
                yield from sequence

            with mock.patch.object(placer, '_ranked', complete):
                expected = placer.pack(source, policy)
            reference_sequences = sequences
            sequences = []
            with scoring.experimental(self.library) as session:
                with mock.patch.object(placer, '_ranked', complete):
                    actual = placer.pack(source, policy)
            self.assertEqual(actual, expected)
            self.assertEqual(sequences, reference_sequences)
            self.assertEqual(session.events, [])

    def test_retained_research_dataset_has_exact_scores_and_complete_ranks(self):
        dataset = Path(__file__).resolve().parents[1] / 'docs/research/2026-10-21-gpu-pcb/experiments/placement-input.json'
        data = json.loads(dataset.read_text())
        ref = data['queries'][0]['ref']
        fp = next(f for f in data['footprints'] if f['ref'] == ref)
        peers = {}
        for other in data['footprints']:
            if other['ref'] != ref:
                for pad in other['pads']:
                    if pad['net'] and pad['net'] not in data['ground_nets']:
                        peers.setdefault(pad['net'], []).append(pad['position_nm'])
        pads = {str(r): placer._pad_nets(fp, (0, 0), r, data['ground_nets']) for r in scoring.ROTATIONS}
        snapshot = scoring.Snapshot.capture(0, pads, peers)
        queries = [(*q['position_nm'], q['rotation_deg']) for q in data['queries']]
        session = self.session()
        scores = session.scorer(snapshot).score_batch(queries, generation=0)
        self.assertEqual(scores, data['expected_score_nm'])
        expected = [placer._wire_cost(placer._pad_nets(fp, (x, y), r, data['ground_nets']), peers)
                    for x, y, r in queries]
        self.assertEqual(scores, expected)
        self.assertEqual(sorted(zip(scores, queries)), sorted(zip(expected, queries)))
        self.assertEqual(session.events, [])

    def test_moves_and_backjumps_preserve_plan_and_trace_generations(self):
        source, policy = promotion_case()
        expected, reference_trace = placement_trace.traced_pack(source, policy)
        with scoring.experimental(self.library) as session:
            actual, compiled_trace = placement_trace.traced_pack(source, policy)
        self.assertEqual(actual['search']['backjumps'], 1)
        self.assertEqual(actual, expected)
        self.assertEqual(compiled_trace['payload'], reference_trace['payload'])
        generations = [row['generation'] for row in placement_trace.stage_a_inputs(compiled_trace)]
        self.assertGreater(len(set(generations)), 1)
        self.assertEqual(session.events, [])
        self.assertIsNone(scoring.active())

    def test_stage_b_objective_is_unchanged_with_rust_residual_stage_a(self):
        from test_block_arrangement import ArrangementFixture

        class Case(ArrangementFixture, unittest.TestCase):
            def runTest(self):
                pass

        case = Case()
        case.setUp()
        self.addCleanup(case.doCleanups)
        expected = copy.deepcopy(case.arrange())
        with scoring.experimental(self.library) as session:
            actual = copy.deepcopy(case.arrange())
        for plan in (expected, actual):
            plan['block_arrangement']['search'].pop('elapsed_s')
        self.assertEqual(expected['status'], 'planned')
        self.assertEqual(actual, expected)
        self.assertEqual(session.events, [])

    def test_checked_placement_mismatch_preserves_plan_and_nested_context(self):
        source, policy = anchor_case()
        expected = placer.pack(source, policy)
        with scoring.experimental(self.library) as outer:
            with scoring.experimental(self.library, check=True) as session:
                with mock.patch.object(session.native, 'one', return_value=-1):
                    self.assertEqual(placer.pack(source, policy), expected)
                self.assertIn('mismatch', session.events[0]['reason'])
            self.assertIs(scoring.active(), outer)
            self.assertEqual(placer.pack(source, policy), expected)
        self.assertIsNone(scoring.active())
        # In ordinary operation no shared library should be loaded at all.
        with mock.patch.object(scoring.ctypes, 'CDLL', side_effect=AssertionError('native opt-in required')):
            self.assertEqual(placer.pack(source, policy), expected)


class ScoringBenchmarkTest(CompiledScoringFixture, unittest.TestCase):
    def test_benchmark_preserves_raw_alternating_timings_and_parity_for_every_arm(self):
        from scripts.placement_scoring_benchmark import benchmark, BOUNDARIES
        source, policy = promotion_case()
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            report = benchmark('promotion', source, policy, self.library, out, runs=3)
            self.assertTrue(report['all_outputs_exact'])
            self.assertEqual(set(report['raw_ns']), set(BOUNDARIES))
            self.assertEqual({len(samples) for samples in report['raw_ns'].values()}, {3})
            self.assertTrue(all(duration > 0 for samples in report['raw_ns'].values() for duration in samples))
            order = report['arm_order']
            self.assertEqual(order, [order[0], list(reversed(order[0])), order[0]])
            self.assertEqual(set(order[0]), set(BOUNDARIES))
            self.assertFalse(report['gate']['met'])
            self.assertFalse(report['gate']['promotion_authorized'])
            for kind in ('trace', 'capture', 'policy'):
                raw = (out / f'promotion.{kind}.json').read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), report['input_sha256'][kind])

    def test_benchmark_rejects_wrong_scores_and_exact_fallback_and_retains_failure(self):
        from scripts.placement_scoring_benchmark import benchmark
        source, policy = promotion_case()
        for method, fault, message, arm in (
                ('score', {'return_value': [-1]}, 'parity failure', 'rust_batch_score'),
                ('one', {'side_effect': OSError('injected native failure')},
                 'unexpected scorer rollback', 'rust_scalar_score')):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as root:
                out = Path(root)
                with mock.patch.object(scoring.Native, method, **fault):
                    with self.assertRaisesRegex(ValueError, message):
                        benchmark('bad', source, policy, self.library, out, runs=3)
                failure = json.loads((out / 'bad.failure.json').read_text())
                self.assertEqual((failure['phase'], failure['arm']), ('warmup', arm))
                self.assertIn(message, failure['failure']['message'])
                self.assertEqual({len(samples) for samples in failure['raw_ns'].values()}, {0})

    def test_benchmark_cli_refuses_unbounded_runs_and_unpaired_supplied_inputs(self):
        from scripts.placement_scoring_benchmark import main
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / 'report'
            for invalid in (['--runs', '2'], ['--runs', '101'], ['--capture', 'capture.json'],
                            ['--policy', 'policy.json']):
                with self.subTest(invalid=invalid), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(['--out', str(out), *invalid])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertFalse(out.exists())

    def test_supplied_capture_and_unit_only_reports_never_claim_complete_g0(self):
        from scripts.placement_scoring_benchmark import main
        source, policy = promotion_case()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            capture_path, policy_path = root / 'capture.json', root / 'policy.json'
            capture_path.write_text(json.dumps(source))
            policy_path.write_text(json.dumps(policy))
            for supplied in (False, True):
                with self.subTest(supplied=supplied):
                    out = root / ('supplied' if supplied else 'units')
                    args = (['--capture', str(capture_path), '--policy', str(policy_path)] if supplied
                            else ['--no-native'])
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = main(['--library', str(self.library), '--out', str(out), '--runs', '3', *args])
                    self.assertEqual(result, 0)
                    report = json.loads((out / 'report.json').read_text())
                    self.assertEqual(report['status'], 'passed-parity')
                    self.assertFalse(report['gate']['g0_workloads_complete'])
                    self.assertFalse(report['gate']['met'])
                    self.assertFalse(report['gate']['promotion_authorized'])
                    kind = 'supplied-capture' if supplied else 'fixture'
                    self.assertEqual({case['workload_kind'] for case in report['cases']}, {kind})
                    if supplied:
                        for kind, path in (('capture', capture_path), ('policy', policy_path)):
                            self.assertEqual(report['supplied_inputs'][kind],
                                             {'path': str(path.resolve()),
                                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


class NativeScoringTest(CompiledScoringFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        super().setUpClass()

    def test_pod_complete_generated_ranks_beyond_the_trace_cap(self):
        from scripts.placement_trace_overhead import pod_cases
        ranker = placer._ranked
        with tempfile.TemporaryDirectory() as scratch:
            for name, source, policy in pod_cases(Path(scratch)):
                with tempfile.TemporaryFile() as witness:
                    counts = [0, 0]

                    def reference_ranks(bounds, exact):
                        sequence = list(ranker(bounds, exact))
                        pickle.dump(sequence, witness)
                        counts[0] += len(sequence)
                        yield from sequence

                    with mock.patch.object(placer, '_ranked', reference_ranks):
                        expected = placer.pack(source, policy)
                    witness.seek(0)

                    def compiled_ranks(bounds, exact):
                        sequence = list(ranker(bounds, exact))
                        self.assertEqual(sequence, pickle.load(witness), name)
                        counts[1] += len(sequence)
                        yield from sequence

                    with scoring.experimental(self.library) as session:
                        with mock.patch.object(placer, '_ranked', compiled_ranks):
                            actual = placer.pack(source, policy)
                    self.assertEqual(actual, expected, name)
                    self.assertEqual(session.events, [], name)
                    self.assertGreater(counts[0], 100000, name)
                    self.assertEqual(counts[0], counts[1], name)
                    self.assertEqual(witness.read(), b'', name)

    def test_native_writer_preserves_board_drc_receipts_and_checked_rollback(self):
        import re
        from agent_ee.placer_runner import place_candidate
        from test_placement_trace import NativePlacementTraceTest
        fixture = NativePlacementTraceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, xml, config = fixture.fixture()
        before = source.read_bytes()
        plain = place_candidate(source, xml, fixture.scratch / 'reference', config)
        self.assertEqual(plain['status'], 'placed', plain)
        with scoring.experimental(self.library) as session:
            actual = place_candidate(source, xml, fixture.scratch / 'compiled', config)
        self.assertEqual(actual, plain)
        self.assertEqual(session.events, [])
        with scoring.experimental(self.library, check=True) as rollback:
            with mock.patch.object(rollback.native, 'one', return_value=-1):
                recovered = place_candidate(source, xml, fixture.scratch / 'rollback', config)
        self.assertEqual(recovered, plain)
        self.assertIn('mismatch', rollback.events[0]['reason'])
        run_facts = re.compile(rb'\.placement-staging-[A-Za-z0-9_]+|"date": "[0-9T:-]+"')

        def normalized(directory):
            return {k: run_facts.sub(b'RUN', v) for k, v in fixture.files(directory).items()}

        baseline = normalized(fixture.scratch / 'reference')
        self.assertEqual(normalized(fixture.scratch / 'compiled'), baseline)
        self.assertEqual(normalized(fixture.scratch / 'rollback'), baseline)
        self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
