"""Versioned verdict classifiers of the AGENTEE-442 native K91 harness (review ee442-native-positive-b, H2).

The frozen harness (scripts/k91_native_positive.py, 553e4151) and its one native
record (evidence/k91-native-positive) stay byte-identical; CLASSIFIER_V1 restates
its verdict and must reproduce the recorded booleans. CLASSIFIER_V2
(scripts/k91_classify.py, used by scripts/k91_native_positive_v2.py) counts the
actual consumer's coherent not_run return of the mismatched-runtime control as a
refusal, keeps the candidate-board and sidecar tamper controls apart, and leaves
an absent candidate-board control incomplete. No router runs here: the controls
are exercised through step/run_step/verify/adopt on the synthetic fixture router.
"""
import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import stage_receipts
from tests import test_routing_experiment_boundary as boundary
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/k91-native-positive'
# The frozen harness as it ran (evidence-manifest.json harness_sha256).
V1_HARNESS_SHA256 = '97d6055d148ef40ae110a09ab6c906f28d0ceea65afc4949f44ca531242e600f'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classify = load('k91_classify')


def historical():
    return json.loads((EVIDENCE / 'evidence-manifest.json').read_text())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HistoricalRecordTest(unittest.TestCase):
    def test_the_frozen_harness_and_its_record_are_unchanged(self):
        manifest = historical()
        self.assertEqual(sha256(ROOT / 'scripts/k91_native_positive.py'), V1_HARNESS_SHA256)
        self.assertEqual(manifest['harness_sha256'], V1_HARNESS_SHA256)
        for line in (EVIDENCE / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ', 1)
            with self.subTest(name):
                self.assertEqual(sha256(EVIDENCE / name), digest)

    def test_v1_reproduces_the_recorded_verdict(self):
        manifest = historical()
        self.assertEqual(classify.v1_verdict(manifest), manifest['verdict'])
        self.assertEqual(manifest['verdict'], {'positive_retained_and_adopted': False, 'controls_refused': False})

    def test_the_reviewers_mismatch_return_is_a_refusal_under_v2_only(self):
        # The actual consumer's return for the mismatched-runtime control: the reviewer's replay.py
        # re-ran verify() on the retained receipt and asserted it equals this recorded value.
        record = historical()['controls']['a_mismatched_runtime']
        value = record['verify']['value']
        self.assertTrue(record['verify']['accepted'])
        self.assertEqual((value['classification'], value['proposal'], value['router_exit'], value['candidate_sha256']),
                         ('not_run', 'none', None, None))
        self.assertFalse(record['adopt']['accepted'])
        # v1 counts the non-raising verify as accepted, so its controls stay false.
        self.assertFalse(classify.v1_verdict(historical())['controls_refused'])
        # v2: the coherent verified not_run, the observed bare adoption refusal, the host refusal
        # and the runtime reasons make a refusal.
        mapped = classify.v1_controls_as_v2(historical())['controls']['a_mismatched_runtime']
        self.assertTrue(classify.not_run_refusal(mapped['verify']['value']))
        self.assertEqual(mapped['direct_adopt'], {'attempted': True, **record['adopt']})
        self.assertEqual(mapped['adopt']['attempted'], False)
        self.assertEqual(classify.runtime_state(mapped), classify.REFUSED)
        self.assertIn('owned engine source digest differs', mapped['host_step']['error'])
        self.assertTrue(any('owned engine source digest differs' in r for r in record['sandbox']['reasons']))

    def test_v2_on_the_historical_records(self):
        verdict = classify.v2_verdict(classify.v1_controls_as_v2(historical()))
        # Revision 3: no production consumer was observed in the historical run, so no required
        # control is refused at the boundary; the round-2 low-level states are kept and reported.
        self.assertEqual(verdict['controls'], dict.fromkeys(classify.CONTROLS_V2, classify.INCOMPLETE))
        self.assertEqual(verdict['low_level'], {'a_mismatched_runtime': classify.REFUSED,
                                                'b_tampered_candidate': classify.INCOMPLETE,
                                                'b_tampered_sidecar': classify.REFUSED,
                                                'c_stale_input': classify.REFUSED})
        self.assertEqual(verdict['required'], ['a_mismatched_runtime', 'b_tampered_candidate', 'c_stale_input'])
        self.assertFalse(verdict['controls_refused'])
        self.assertFalse(verdict['positive_accepted'])

    def test_the_mapping_does_not_edit_the_historical_record(self):
        manifest = historical()
        before = json.dumps(manifest, sort_keys=True)
        classify.v2_verdict(classify.v1_controls_as_v2(manifest))
        self.assertEqual(json.dumps(manifest, sort_keys=True), before)


def observed(value=None):
    return {'attempted': True, 'accepted': True, 'value': value}


def raised(error='ValueError: refused'):
    return {'attempted': True, 'accepted': False, 'error': error}


NOT_MADE = {'attempted': False, 'reason': 'consumer adoption follows only a verify() that did not refuse'}


class ClassifierRulesTest(unittest.TestCase):
    def setUp(self):
        self.runtime = classify.v1_controls_as_v2(historical())['controls']['a_mismatched_runtime']
        self.assertEqual(classify.runtime_state(self.runtime), classify.REFUSED)

    def test_any_departure_from_a_coherent_not_run_is_not_a_refusal(self):
        cases = {'classification': 'complete', 'proposal': 'eligible', 'router_exit': 0, 'candidate_sha256': 'a' * 64}
        for key, value in cases.items():
            with self.subTest(key):
                record = copy.deepcopy(self.runtime)
                record['verify']['value'][key] = value
                self.assertFalse(classify.not_run_refusal(record['verify']['value']))
                self.assertEqual(classify.runtime_state(record), classify.NOT_REFUSED)

    def test_a_missing_not_run_key_is_never_read_as_null(self):
        # H2 (round 2): an absent router_exit or candidate_sha256 is not evidence of no process or no candidate.
        for key in classify.NOT_RUN_KEYS:
            with self.subTest(key):
                record = copy.deepcopy(self.runtime)
                del record['verify']['value'][key]
                self.assertFalse(classify.not_run_refusal(record['verify']['value']))
                self.assertNotEqual(classify.runtime_state(record), classify.REFUSED)
        record = copy.deepcopy(self.runtime)
        del record['sandbox']['candidate']
        self.assertEqual(classify.runtime_state(record), classify.INCOMPLETE)

    def test_a_missing_or_malformed_outcome_is_incomplete(self):
        # H2 (round 2): missing direct_adopt, verify, adopt or host_step, and outcomes without an explicit
        # boolean or with neither value nor error, never satisfy a refusal.
        edits = {'missing direct_adopt': lambda r: r.pop('direct_adopt'),
                 'missing adopt': lambda r: r.pop('adopt'),
                 'missing verify': lambda r: r.pop('verify'),
                 'missing host_step': lambda r: r.pop('host_step'),
                 'direct_adopt without attempted': lambda r: r['direct_adopt'].pop('attempted'),
                 'direct_adopt accepted not boolean': lambda r: r['direct_adopt'].update(accepted=0),
                 'direct_adopt without error': lambda r: r['direct_adopt'].pop('error'),
                 'direct_adopt empty error': lambda r: r['direct_adopt'].update(error=''),
                 'verify without value': lambda r: r['verify'].pop('value'),
                 'verify accepted as string': lambda r: r['verify'].update(accepted='true'),
                 'host error missing': lambda r: r['host_step'].pop('error'),
                 'adopt skip without reason': lambda r: r.update(adopt={'attempted': False}),
                 'extra outcome key': lambda r: r['direct_adopt'].update(value=None)}
        for label, edit in edits.items():
            with self.subTest(label):
                record = copy.deepcopy(self.runtime)
                edit(record)
                self.assertEqual(classify.runtime_state(record), classify.INCOMPLETE)
                verdict = classify.v2_verdict({'positive': {}, 'controls': {
                    'a_mismatched_runtime': record, 'b_tampered_candidate': self.refusal(),
                    'c_stale_input': self.refusal()}})
                self.assertFalse(verdict['controls_refused'])

    def refusal(self):
        return {'verify': raised(), 'adopt': dict(NOT_MADE), 'direct_adopt': raised('ValueError: output changed')}

    def test_a_skipped_or_successful_adoption_is_not_an_adoption_refusal(self):
        # H1 (round 2): verify() refuses, and the adoption was skipped (no call observed) or the bare
        # adoption SUCCEEDED. Neither is an observed adoption refusal: incomplete, never refused.
        cases = {'skipped, bare not made': {'verify': raised(), 'adopt': dict(NOT_MADE),
                                            'direct_adopt': {'attempted': False, 'reason': 'not made'}},
                 'skipped, bare succeeds': {'verify': raised(), 'adopt': dict(NOT_MADE), 'direct_adopt': observed()},
                 'consumer succeeds': {'verify': raised(), 'adopt': observed(), 'direct_adopt': raised()},
                 'not_run, bare succeeds': {'verify': copy.deepcopy(self.runtime['verify']), 'adopt': dict(NOT_MADE),
                                            'direct_adopt': observed()}}
        for label, record in cases.items():
            with self.subTest(label):
                self.assertEqual(classify.control_state(record), classify.INCOMPLETE)
        self.assertEqual(classify.control_state(self.refusal()), classify.REFUSED)
        runtime = copy.deepcopy(self.runtime)
        runtime['direct_adopt'] = observed()
        self.assertEqual(classify.runtime_state(runtime), classify.INCOMPLETE)

    def test_a_verify_that_accepts_is_not_refused(self):
        self.assertEqual(classify.control_state({'verify': observed({'classification': 'complete'}),
                                                 'adopt': raised(), 'direct_adopt': raised()}), classify.NOT_REFUSED)

    def test_a_refusal_needs_host_refusal_and_sandbox_reasons(self):
        edits = {'host accepted': lambda r: r.update(host_step=observed()),
                 'sandbox ran': lambda r: r['sandbox'].update(classification='complete'),
                 'no reasons': lambda r: r['sandbox'].update(reasons=[]),
                 'sandbox candidate': lambda r: r['sandbox'].update(candidate={'sha256': 'a' * 64})}
        for label, edit in edits.items():
            with self.subTest(label):
                record = copy.deepcopy(self.runtime)
                edit(record)
                self.assertEqual(classify.runtime_state(record), classify.NOT_REFUSED)

    def test_absent_controls_are_incomplete(self):
        self.assertEqual(classify.control_state({'skipped': 'absent'}), classify.INCOMPLETE)
        self.assertEqual(classify.control_state(None), classify.INCOMPLETE)
        self.assertEqual(classify.runtime_state(None), classify.INCOMPLETE)
        self.assertFalse(classify.v2_verdict({'positive': {}, 'controls': {}})['controls_refused'])



DIGEST_A, DIGEST_B = 'a' * 64, 'b' * 64


def consumer_refusal(**changes):
    """A schema-valid production-consumer refusal record (the shape k91_consumer.consume writes)."""
    record = {'consumer': raised('ValueError: refused at the boundary'), 'run_step': 'replayed',
              'consumer_run_step': observed('receipt-id'),
              'consumer_verify': raised('ValueError: retained routing artifacts differ'),
              'consumer_adopt': {'attempted': False, 'reason': 'not called by campaign.run_implementation_steps'},
              'consumer_design': '/w/design', 'direct_design': '/w/direct-design',
              'design_before': DIGEST_A, 'design_after': DIGEST_A, 'direct_adopt': raised('ValueError: changed')}
    record.update(changes)
    return record


class ConsumerRulesTest(unittest.TestCase):
    """Revision 3 (ruling a01d6f91): refusal is observed at the production consumer; H2 schema rules hold."""

    def test_an_observed_consumer_refusal_with_unchanged_design_is_a_refusal(self):
        self.assertEqual(classify.consumer_state(consumer_refusal()), classify.REFUSED)
        # The bare diagnostic stays visible whatever it returned; it neither makes nor unmakes the refusal.
        for direct in (observed(), {'attempted': False, 'reason': 'no receipt was retained'}):
            with self.subTest(direct=direct):
                record = consumer_refusal(direct_adopt=direct)
                self.assertEqual(classify.consumer_state(record), classify.REFUSED)
                self.assertEqual(record['direct_adopt'], direct)

    def test_a_skipped_consumer_adoption_is_not_an_adoption_refusal(self):
        # The skipped layer stays recorded as not made; only the observed consumer refusal decides.
        record = consumer_refusal()
        self.assertFalse(record['consumer_adopt']['attempted'])
        self.assertIsNone(classify.observed(record['consumer_adopt']))
        self.assertTrue(classify.skipped(record['consumer_adopt']))
        # Without an observed consumer refusal, the skipped adoption and a refusing bare adoption prove nothing.
        for consumer in ({'attempted': False, 'reason': 'not run'}, None):
            with self.subTest(consumer=consumer):
                self.assertEqual(classify.consumer_state(consumer_refusal(consumer=consumer)), classify.INCOMPLETE)

    def test_a_consumer_that_accepted_adopted_or_changed_the_design_is_not_refused(self):
        cases = {'consumer returned': {'consumer': observed()},
                 'consumer adopted': {'consumer_adopt': observed()},
                 'design changed': {'design_after': DIGEST_B}}
        for label, changes in cases.items():
            with self.subTest(label):
                self.assertEqual(classify.consumer_state(consumer_refusal(**changes)), classify.NOT_REFUSED)

    def test_missing_or_malformed_consumer_observations_are_incomplete(self):
        # H2: explicit keys, explicit booleans, explicit digests; nothing missing is read as a refusal.
        edits = {'missing consumer': lambda r: r.pop('consumer'),
                 'missing consumer_run_step': lambda r: r.pop('consumer_run_step'),
                 'missing consumer_verify': lambda r: r.pop('consumer_verify'),
                 'missing consumer_adopt': lambda r: r.pop('consumer_adopt'),
                 'missing direct_adopt': lambda r: r.pop('direct_adopt'),
                 'missing design_before': lambda r: r.pop('design_before'),
                 'missing design_after': lambda r: r.pop('design_after'),
                 'missing direct_design': lambda r: r.pop('direct_design'),
                 'same design for both': lambda r: r.update(direct_design=r['consumer_design']),
                 'design digest not hex': lambda r: r.update(design_before='Z' * 64, design_after='Z' * 64),
                 'design digest null': lambda r: r.update(design_before=None, design_after=None),
                 'consumer accepted not boolean': lambda r: r['consumer'].update(accepted=0),
                 'consumer without error': lambda r: r['consumer'].pop('error'),
                 'consumer empty error': lambda r: r['consumer'].update(error=''),
                 'consumer without attempted': lambda r: r['consumer'].pop('attempted'),
                 'two consumer adoptions': lambda r: r.update(consumer_adopt={'malformed': [raised(), raised()]}),
                 'skip without reason': lambda r: r.update(consumer_adopt={'attempted': False}),
                 'extra key': lambda r: r['consumer_verify'].update(value=None)}
        for label, edit in edits.items():
            with self.subTest(label):
                record = consumer_refusal()
                edit(record)
                self.assertEqual(classify.consumer_state(record), classify.INCOMPLETE)
                verdict = classify.v2_verdict({'positive': {}, 'controls': {
                    'a_mismatched_runtime': {**record, 'host_binding': raised()},
                    'b_tampered_candidate': record, 'c_stale_input': record}})
                self.assertFalse(verdict['controls_refused'])

    def test_the_runtime_control_needs_the_observed_binding_refusal(self):
        self.assertEqual(classify.runtime_consumer_state(consumer_refusal(host_binding=raised())), classify.REFUSED)
        self.assertEqual(classify.runtime_consumer_state(consumer_refusal()), classify.INCOMPLETE)
        self.assertEqual(classify.runtime_consumer_state(consumer_refusal(
            host_binding={'attempted': True, 'accepted': False})), classify.INCOMPLETE)
        self.assertEqual(classify.runtime_consumer_state(consumer_refusal(host_binding=observed())),
                         classify.NOT_REFUSED)

    def test_required_controls_decide_and_the_sidecar_is_reported(self):
        controls = {'a_mismatched_runtime': consumer_refusal(host_binding=raised()),
                    'b_tampered_candidate': consumer_refusal(), 'c_stale_input': consumer_refusal(),
                    'b_tampered_sidecar': consumer_refusal(direct_adopt=observed())}
        verdict = classify.v2_verdict({'positive': {}, 'controls': controls})
        self.assertTrue(verdict['controls_refused'])
        self.assertEqual(verdict['controls']['b_tampered_sidecar'], classify.REFUSED)
        self.assertTrue(controls['b_tampered_sidecar']['direct_adopt']['accepted'])
        for name in classify.REQUIRED_V2:
            with self.subTest(name):
                self.assertFalse(classify.v2_verdict({'positive': {}, 'controls': {
                    **controls, name: {'skipped': 'absent'}}})['controls_refused'])

    def test_the_positive_needs_the_consumer_to_verify_and_adopt_the_delivered_board(self):
        value = {'classification': 'complete', 'proposal': 'eligible', 'candidate_sha256': DIGEST_B,
                 'experiment': {'status': 'retained', 'qualified': False}}
        good = {'consumer': observed(), 'consumer_verify': observed(value), 'consumer_adopt': observed(),
                'design_before': DIGEST_A, 'design_after': DIGEST_B, 'delivered_board_sha256': DIGEST_B}
        self.assertTrue(classify.positive_accepted(good))
        edits = {'consumer raised': lambda p: p.update(consumer=raised()),
                 'not adopted': lambda p: p.update(consumer_adopt=raised()),
                 'adoption not made': lambda p: p.update(consumer_adopt={'attempted': False, 'reason': 'x'}),
                 'adoption missing': lambda p: p.pop('consumer_adopt'),
                 'adoption unobserved': lambda p: p.update(consumer_adopt={'accepted': True, 'value': None}),
                 'verify missing': lambda p: p.pop('consumer_verify'),
                 'verify raised': lambda p: p.update(consumer_verify=raised()),
                 'nothing changed': lambda p: p.update(design_after=DIGEST_A),
                 'other board delivered': lambda p: p.update(delivered_board_sha256='c' * 64),
                 'delivered missing': lambda p: p.pop('delivered_board_sha256'),
                 'no candidate': lambda p: p['consumer_verify']['value'].update(candidate_sha256=None),
                 'proposal none': lambda p: p['consumer_verify']['value'].update(proposal='none'),
                 'refused record': lambda p: p['consumer_verify']['value']['experiment'].update(status='refused'),
                 'qualified': lambda p: p['consumer_verify']['value']['experiment'].update(qualified=True)}
        for label, edit in edits.items():
            with self.subTest(label):
                positive = copy.deepcopy(good)
                edit(positive)
                self.assertFalse(classify.positive_accepted(positive))
        self.assertFalse(classify.positive_accepted({}))
        self.assertFalse(classify.positive_accepted(None))


class ProductionConsumerTest(unittest.TestCase):
    """The positive and every control through campaign.run_implementation_steps (the production routing
    consumer) on the synthetic fixture router: run_step executes (positive, runtime control) or replays a
    retained disposable receipt copy at its own path (board, sidecar, stale); verification, adoption and
    their order are the consumer's own."""

    @classmethod
    def setUpClass(cls):
        require_toolchain()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-consumer-')
        cls.root = root = Path(cls.tmp.name)
        cls.consumer = load('k91_consumer')
        checkout, code = make_checkout(root / 'checkout', {'name': 'complete'})
        mismatch, _ = make_checkout(root / 'mismatch', {'name': 'complete'})
        with (mismatch / 'src/kicad_route/__init__.py').open('a') as handle:
            handle.write('# another runtime\n')
        cls.code = code
        with cls.consumer.pinned(root, FAKE_PIN, code) as adapter:
            cls.adapter = adapter
            cls.decl = cls.consumer.declaration(checkout, project='project', board='rc.kicad_pcb',
                                                schematic='rc.kicad_sch', name='rc', budget=5, timeout=30,
                                                policy=boundary.CURRENT)
            cls.bind = cls.consumer.binding(cls.decl)
            cls.work = root / 'positive' / '1'
            cls.work.mkdir(parents=True)
            cls.positive = cls.consumer.consume(cls.work, make_candidate(root / 'positive/design'), cls.bind)
            receipt = json.loads((cls.work / cls.consumer.STEP_DIR / 'receipt.json').read_text())
            cls.positive['delivered_board_sha256'] = receipt['outputs']['project/rc.kicad_pcb']
            cls.kept = cls.consumer.tree_sha256(cls.work)
            cls.controls = cls.consumer.run_controls(root / 'controls', cls.work, cls.decl, cls.bind, mismatch,
                                                     make_candidate, 'project/rc.kicad_pcb')
        cls.verdict = classify.v2_verdict({'positive': cls.positive, 'controls': cls.controls})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_positive_is_verified_and_adopted_by_the_consumer(self):
        positive = self.positive
        self.assertTrue(positive['consumer']['accepted'], positive)
        self.assertEqual(positive['run_step'], 'executed')
        self.assertTrue(positive['consumer_run_step']['accepted'])
        self.assertTrue(positive['consumer_verify']['accepted'])
        self.assertEqual(positive['consumer_verify']['value']['experiment']['status'], 'retained')
        self.assertTrue(positive['consumer_adopt']['accepted'])
        self.assertNotEqual(positive['design_before'], positive['design_after'])
        self.assertTrue(self.verdict['positive_accepted'])

    def test_every_required_control_is_refused_by_the_consumer_with_unchanged_design(self):
        self.assertEqual(self.verdict['controls'], dict.fromkeys(classify.CONTROLS_V2, classify.REFUSED))
        self.assertTrue(self.verdict['controls_refused'])
        for name, record in self.controls.items():
            with self.subTest(name):
                self.assertFalse(record['consumer']['accepted'])
                self.assertEqual(record['design_before'], record['design_after'])
                self.assertNotEqual(record['consumer_design'], record['direct_design'])

    def test_each_control_is_refused_at_its_own_layer(self):
        runtime = self.controls['a_mismatched_runtime']
        self.assertIn('kicad-route does not qualify', runtime['host_binding']['error'])
        # The production consumer re-checks the live binding and refuses before run_step.
        self.assertIn('kicad-route does not qualify', runtime['consumer']['error'])
        self.assertEqual([runtime[k]['attempted'] for k in ('consumer_run_step', 'consumer_verify', 'consumer_adopt')],
                         [False, False, False])
        self.assertEqual(runtime['direct_adopt'], {'attempted': False, 'reason': 'no receipt was retained'})
        # The low-level observation, kept apart: run_step executed directly, sandbox not_run (no router),
        # verify returns the coherent not_run, the bare adoption on another copy refuses.
        low = runtime['low_level']
        self.assertTrue(low['run_step']['accepted'])
        self.assertEqual((low['sandbox']['classification'], low['sandbox']['candidate']), ('not_run', None))
        self.assertTrue(classify.not_run_refusal(low['verify']['value']))
        self.assertIn('no output adopted', low['direct_adopt']['error'])
        self.assertEqual(self.verdict['low_level']['a_mismatched_runtime'], classify.REFUSED)
        for name in ('b_tampered_candidate', 'b_tampered_sidecar'):
            with self.subTest(name):
                record = self.controls[name]
                self.assertEqual(record['run_step'], 'replayed')
                self.assertTrue(record['consumer_run_step']['accepted'])
                self.assertFalse(record['consumer_verify']['accepted'], record['consumer_verify'])
                self.assertEqual(record['consumer_adopt']['attempted'], False)
                self.assertFalse(record['low_level']['verify']['accepted'])
        # The bare stage adoption: refuses the tampered board, ACCEPTS the tampered sidecar (kept visible).
        self.assertFalse(self.controls['b_tampered_candidate']['direct_adopt']['accepted'])
        self.assertTrue(self.controls['b_tampered_sidecar']['direct_adopt']['accepted'])
        self.assertEqual(self.verdict['low_level']['b_tampered_sidecar'], classify.INCOMPLETE)
        stale = self.controls['c_stale_input']
        self.assertIn('routing receipt chain input differs', stale['consumer']['error'])
        self.assertTrue(stale['consumer_run_step']['accepted'])
        self.assertEqual((stale['consumer_verify']['attempted'], stale['consumer_adopt']['attempted']), (False, False))
        self.assertIn('inputs differ', stale['low_level']['verify']['error'])
        self.assertIn('design changed', stale['direct_adopt']['error'])

    def test_an_untampered_replay_is_accepted_and_adopted_by_the_same_consumer(self):
        # Counterexample at the real boundary: the replay plumbing refuses nothing itself.
        with self.consumer.pinned(self.root, FAKE_PIN, self.code) as adapter, \
                self.consumer.disposable_receipt(self.work) as work:
            record = self.consumer.consume(work, make_candidate(self.root / 'counterexample/design'), self.bind,
                                           replay=True, direct_design=make_candidate(self.root / 'counterexample/direct'))
        self.assertEqual(adapter, self.adapter)  # the binding's own adapter path and bytes
        self.assertTrue(record['consumer']['accepted'], record['consumer'])
        self.assertTrue(record['consumer_verify']['accepted'])
        self.assertTrue(record['consumer_adopt']['accepted'])
        self.assertNotEqual(record['design_before'], record['design_after'])
        self.assertEqual(classify.consumer_state(record), classify.NOT_REFUSED)
        self.assertEqual(self.consumer.tree_sha256(self.work), self.kept)

    def test_the_positive_receipt_is_restored_after_the_replays(self):
        self.assertEqual(self.consumer.tree_sha256(self.work), self.kept)
        self.assertFalse(self.work.with_name('1.kept').exists())

    def test_observers_pass_values_and_exceptions_through_unchanged(self):
        with patch.object(self.consumer.routing_adapter, 'verify_campaign_receipt', lambda *a, **k: 'summary'), \
                patch.object(self.consumer.stage_receipts, 'adopt', lambda *a, **k: (_ for _ in ()).throw(KeyError('x'))):
            with self.consumer.observed_layers() as calls:
                self.assertEqual(self.consumer.routing_adapter.verify_campaign_receipt(1), 'summary')
                with self.assertRaises(KeyError):
                    self.consumer.stage_receipts.adopt(1)
        self.assertEqual(calls['verify'], [{'attempted': True, 'accepted': True, 'value': 'summary'}])
        self.assertEqual(calls['adopt'], [{'attempted': True, 'accepted': False, 'error': "KeyError: 'x'"}])

    def test_the_replay_only_returns_the_receipt_the_consumer_asked_for(self):
        replay = self.consumer.replayed_run_step(self.work)
        with self.assertRaises(ValueError):
            replay(self.root / 'elsewhere', None, self.bind['spec'], {})
        receipt_dir = self.work / self.consumer.STEP_DIR
        expected = {'implementation': 1, 'source_candidate_id': self.consumer.CANDIDATE_ID, 'receipt_index': 1}
        self.assertEqual(replay(receipt_dir, None, self.bind['spec'], expected)['receipt_id'],
                         json.loads((receipt_dir / 'receipt.json').read_text())['receipt_id'])
        with self.assertRaises(ValueError):
            replay(receipt_dir, None, self.bind['spec'], {**expected, 'implementation': 2})

    def test_an_absent_candidate_board_is_incomplete(self):
        self.assertEqual(classify.consumer_state({'skipped': 'routing/candidate absent', 'file': 'x'}),
                         classify.INCOMPLETE)


class SuccessorCommandLineTest(unittest.TestCase):
    def setUp(self):
        self.v2 = load('k91_native_positive_v2')
        self.argv = ['--checkout', 'L', '--frozen-source', 'F', '--mismatch', 'M', '--out', 'O', '--commit', 'c',
                     '--source-sha256', 's', '--extension-sha256', 'e', '--packages-sha256', 'p',
                     '--python-version', '3.12.3', '--input-tree-sha256', 't', '--board-sha256', 'b',
                     '--rule-areas-sha256', 'r']

    def test_limits_default_to_the_authorized_bounds_and_are_ordered(self):
        args = self.v2.parse(self.argv)
        self.assertEqual((args.router_limit_s, args.total_limit_s, args.budget), (900, 2400, 300))
        for extra in (['--budget', '900'], ['--router-limit-s', '2500'], ['--budget', '0']):
            with self.subTest(extra), self.assertRaises(SystemExit):
                self.v2.parse(self.argv + extra)
        for required in ('--input-tree-sha256', '--board-sha256', '--packages-sha256', '--python-version'):
            with self.subTest(required), self.assertRaises(SystemExit):
                i = self.argv.index(required)
                self.v2.parse(self.argv[:i] + self.argv[i + 2:])

    def test_runtime_and_input_identities_are_exact(self):
        args = self.v2.parse(self.argv)
        found = {'packages_sha256': 'p', 'python_version': '3.12.3'}
        self.assertEqual(self.v2.runtime_mismatch(args, found, {}), {})
        self.assertIn('python_version', self.v2.runtime_mismatch(args, {**found, 'python_version': '3.12.4'}, {}))
        record = {'sha256': 't', 'files': {'project/rc_filter.kicad_pcb': 'b'}}
        self.assertEqual(self.v2.input_mismatch(args, record, 'b'), {})
        self.assertEqual(set(self.v2.input_mismatch(args, {**record, 'sha256': 'u'}, 'b')), {'input_tree_sha256'})
        self.assertEqual(set(self.v2.input_mismatch(args, record, 'x')), {'board_sha256'})


if __name__ == '__main__':
    unittest.main()
