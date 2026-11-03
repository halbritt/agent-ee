"""Amendment A2: K91 control A under recorded router versions (AGENTEE-442; integrator agent-724, 2026-11-03).

The production routing consumer runs on the synthetic fixture router through scripts/k91_consumer.py.
Control A presents a different clean commit M to a campaign whose routing binding recorded L. A2
accepts it only as the fresh binding of M accepted and recorded, plus the continuation guard's
literal refusal of M; a dirty runtime, other identities or any other reason never refuses control A.
scripts/k91_classify.py and scripts/k91_classify_a1.py are records and stay byte-identical.
"""
import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import k91_classify as v2  # noqa: E402
import k91_classify_a1 as a1  # noqa: E402
import k91_classify_a2 as a2  # noqa: E402
import k91_consumer as k  # noqa: E402
from agent_ee import routing_adapter  # noqa: E402
from tests.kicad_route_fixture import commit_all, make_candidate, make_checkout  # noqa: E402
from tests.test_routing_experiment_boundary import CURRENT  # noqa: E402
from tests.toolchain import require_toolchain  # noqa: E402

A = a2.CONTROL
# The records A2 amends, byte-identical (test_k91_classify_a1 also checks the v2 bytes against evidence).
RECORD_SHA256 = {'k91_classify.py': '55563ea17c7e4ba725219717904a95beb0a5814676558f5690c0500f102dd706',
                 'k91_classify_a1.py': 'b78046bcfb70426e827a0929eaaba4ab000aaab0184645aa88dc482abc45ce01'}


def harness(root, *, dirty):
    """One consumer run: the positive, then every control with M either another clean commit or a dirty
    tree of L. Returns (binding, M's HEAD, controls)."""
    checkout, _ = make_checkout(root / 'checkout', {'name': 'complete'})
    mismatch, _ = make_checkout(root / 'mismatch', {'name': 'complete'})
    with (mismatch / 'src/kicad_route/__init__.py').open('a') as handle:
        handle.write('# another runtime\n')
    current = k.attempt.checkout_commit(mismatch) if dirty else commit_all(mismatch, 'another runtime')
    with k.production():
        decl = k.declaration(checkout, project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                             name='rc', budget=5, timeout=30, policy=CURRENT)
        bind = k.binding(decl)
        work = root / 'positive' / '1'
        work.mkdir(parents=True)
        positive = k.consume(work, make_candidate(root / 'positive/design'), bind)
        assert positive['consumer']['accepted'], positive
        controls = k.run_controls(root / 'controls', work, decl, bind, mismatch, make_candidate, 'project/rc.kicad_pcb')
    return bind, current, controls


class AmendmentA2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-k91-a2-')
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        cls.bind, cls.current, cls.controls = harness(root / 'clean', dirty=False)
        cls.ids = a2.identities_of(cls.bind, cls.current)
        _, cls.dirty_head, cls.dirty = harness(root / 'dirty', dirty=True)

    def record(self):
        return copy.deepcopy(self.controls[A])

    def test_the_records_it_amends_are_unchanged(self):
        for name, digest in RECORD_SHA256.items():
            with self.subTest(name):
                self.assertEqual(hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest(), digest)

    def test_the_accepted_message_is_the_production_continuation_refusal(self):
        recorded, current = self.ids['binding_commit'], self.ids['runtime_commit']
        self.assertEqual(a2.continuation_message(recorded, current),
                         str(routing_adapter.StaleRoutingEvidence(recorded, current)))
        self.assertEqual(self.controls[A]['consumer']['error'],
                         'StaleRoutingEvidence: ' + a2.continuation_message(recorded, current))

    def test_the_live_control_is_refused_under_a2_only(self):
        self.assertNotEqual(self.ids['binding_commit'], self.ids['runtime_commit'])
        self.assertEqual(a2.contract_match(A, self.controls[A], self.ids), [])
        self.assertEqual(a2.consumer_state(self.controls[A], A, self.ids), v2.REFUSED)
        # The frozen classifier and A1 (which needs the retained native identities) cannot refuse it.
        self.assertEqual(v2.consumer_state(self.controls[A], A), v2.INCOMPLETE)
        self.assertEqual(a1.consumer_state(self.controls[A], A, None), v2.INCOMPLETE)
        verdict = a2.v2a2_verdict({'controls': self.controls}, self.ids)
        self.assertTrue(verdict['controls_refused'])
        self.assertEqual(verdict['low_level'][A], a2.FRESH_RUN)
        # Without identities the A2 path never applies.
        self.assertEqual(a2.consumer_state(self.controls[A], A, None), v2.INCOMPLETE)

    def test_a_dirty_runtime_never_refuses_the_different_commit_control(self):
        dirty = self.dirty[A]
        # The dirty tree is refused for that reason (host binding and consumer), never as continuation.
        self.assertFalse(dirty['host_binding']['accepted'])
        self.assertIn('uncommitted or untracked changes', dirty['consumer']['error'])
        for identities in (self.ids, a2.identities_of(self.bind, self.dirty_head),
                           {'binding_commit': self.ids['binding_commit'], 'runtime_commit': self.dirty_head}):
            with self.subTest(identities=identities):
                self.assertEqual(a2.consumer_state(dirty, A, identities), v2.INCOMPLETE)
        self.assertFalse(a2.v2a2_verdict({'controls': self.dirty}, self.ids)['controls_refused'])

    def test_the_identities_are_the_bound_and_the_presented_commit(self):
        recorded, current = self.ids['binding_commit'], self.ids['runtime_commit']
        self.assertIsNone(a2.identities_of(self.bind, recorded))
        self.assertIsNone(a2.identities_of({'commit': 'main'}, current))
        self.assertIsNone(a2.identities_of(self.bind, current.upper()))
        for label, identities in (('swapped', {'binding_commit': current, 'runtime_commit': recorded}),
                                  ('equal', {'binding_commit': recorded, 'runtime_commit': recorded}),
                                  ('other', {'binding_commit': recorded, 'runtime_commit': '0' * 40}),
                                  ('extra', {**self.ids, 'note': 'x'})):
            with self.subTest(label):
                self.assertEqual(a2.consumer_state(self.controls[A], A, identities), v2.INCOMPLETE)

    def test_the_fresh_binding_must_accept_and_record_m(self):
        for label, change in (
                ('refused', lambda r: r.update(host_binding=copy.deepcopy(self.dirty[A]['host_binding']))),
                ('records L', lambda r: r['host_binding'].update(value={'commit': self.ids['binding_commit']})),
                ('no record', lambda r: r['host_binding'].update(value=None)),
                ('absent', lambda r: r.pop('host_binding'))):
            with self.subTest(label):
                record = self.record()
                change(record)
                self.assertEqual(a2.consumer_state(record, A, self.ids), v2.INCOMPLETE)

    def test_the_refusal_must_be_the_single_continuation_link(self):
        link = self.controls[A]['consumer']['exception'][0]
        for label, change in (('type', {'type': 'ValueError'}),
                              ('message', {'message': link['message'] + ' '}),
                              ('origin', {'origin': {**link['origin'], 'function': 'validate_campaign_step'}}),
                              ('path', {'frames': [f for f in link['frames'] if 'run_implementation_steps' not in f]})):
            with self.subTest(label):
                record = self.record()
                record['consumer']['exception'][0].update(change)
                record['consumer']['error'] = f"{record['consumer']['exception'][0]['type']}: " \
                                              f"{record['consumer']['exception'][0]['message']}"
                self.assertEqual(a2.consumer_state(record, A, self.ids), v2.INCOMPLETE)

    def test_layers_and_preconditions_must_be_shown(self):
        for label, change, state in (
                ('run_step called', lambda r: r.update(consumer_run_step={'attempted': True, 'accepted': True,
                                                                          'value': None}), v2.INCOMPLETE),
                ('replayed', lambda r: r.update(run_step='replayed'), v2.INCOMPLETE),
                ('design changed', lambda r: r.update(design_after='0' * 64), v2.NOT_REFUSED)):
            with self.subTest(label):
                record = self.record()
                change(record)
                self.assertEqual(a2.consumer_state(record, A, self.ids), state)

    def test_other_controls_are_judged_by_verdict_2r4_unchanged(self):
        for name in v2.CONTROLS_V2:
            if name == A:
                continue
            with self.subTest(name):
                self.assertEqual(a2.consumer_state(self.controls[name], name, self.ids),
                                 v2.consumer_state(self.controls[name], name))
                self.assertEqual(a2.contract_match(name, self.controls[name], self.ids),
                                 v2.contract_match(name, self.controls[name]))

    def test_the_low_level_fresh_run_must_record_m(self):
        for label, change in (('other commit', {'commit': self.ids['binding_commit']}), ('not clean', {'clean': None}),
                              ('not run', {'classification': 'not_run'})):
            with self.subTest(label):
                record = self.record()
                record['low_level']['sandbox'].update(change)
                self.assertEqual(a2.fresh_run_state(record['low_level'], self.ids), v2.INCOMPLETE)


class LegacyContractTest(unittest.TestCase):
    """verdict-2r4's control-A contract never refuses control A under A2 (review of fcfb4385, finding 2)."""

    def test_the_retired_table_refusal_does_not_refuse_control_a(self):
        # The reviewer's a2_legacy_bypass probe: a schema-valid refusal for the retired table's reason,
        # with the fresh binding refused, which the frozen v2 contract accepts.
        message = 'kicad-route does not qualify: owned engine source digest differs'
        link = dict(type='ValueError', message=message, frames=list(v2.CONTRACTS[A]['path']), link=None,
                    origin=dict(file='routing_adapter.py', function='validate_campaign_step', line=1))
        refused = dict(attempted=True, accepted=False, error='ValueError: ' + message, exception=[link])
        skipped = dict(attempted=False, reason='not reached')
        record = dict(consumer_design='consumer', direct_design='direct', consumer=copy.deepcopy(refused),
                      host_binding=copy.deepcopy(refused), consumer_run_step=skipped, consumer_verify=skipped,
                      consumer_adopt=skipped, direct_adopt=skipped, design_before='a' * 64, design_after='a' * 64,
                      run_step='executed')
        self.assertEqual(v2.schema_problems(record), [])
        self.assertEqual(v2.consumer_state(record, A), v2.REFUSED)
        for identities in (None, {'binding_commit': '1' * 40, 'runtime_commit': '2' * 40}):
            with self.subTest(identities=identities):
                self.assertEqual(a2.consumer_state(record, A, identities), v2.INCOMPLETE)
                self.assertTrue(a2.contract_match(A, record, identities))
                self.assertFalse(a2.v2a2_verdict({'controls': {A: record}}, identities)['controls_refused'])


if __name__ == '__main__':
    unittest.main()
