"""AGENTEE-389 S2: the semantic architect producer with scripted transports, never a live model call.

``agent_ee.reviewers.dispatch`` (the provider launch itself) is replaced by a scripted
transport. The real preflight guard, its networkless sandbox probe, structured resubmission,
launch-record verification and the unchanged S1 compiler all run on sealed fixture runs.
"""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import physical_architect, physical_intent
from agent_ee.executor import Cancellation
from agent_ee.physical_architect import compare, produce, propose, verify_stage
from agent_ee.physical_intent import IntentRefused, compile_plan
from agent_ee.review_run import input_identity
from agent_ee.storage import digest, read_json
from tests import test_physical_intent as pi
from toolchain import require_toolchain

DEFINITION = {'harness': 'claude', 'model': 'scripted-architect', 'effort': 'high', 'timeout_s': 60}
RUNTIME = {name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
           for name in ('claude', 'codex', 'codex_tool_host', 'agy', 'bwrap', '/usr/bin/python3', 'kicad-cli',
                        'pcbnew', 'ngspice', '/usr/bin/ngspice')}
CORRECT = [pi.terminal('u1-canl', '6', 'CANL', 'canl'), pi.terminal('u1-canh', '7', 'CANH', 'canh')]
SWAPPED = [pi.terminal('u1-canl', '7', 'CANL', 'canl', pad_uuid=pi.PAD['7'], net='unconnected-(U1-CANL-Pad7)'),
           pi.terminal('u1-canh', '6', 'CANH', 'canh', pad_uuid=pi.PAD['6'], net='unconnected-(U1-CANH-Pad6)')]
KELVIN = {'id': 'can-kelvin', 'kind': 'kelvin', 'required': True, 'requirement': 'pins',
          'terminals': ['u1-canl', 'u1-canh'], 'statement': 'fixture-only force/sense obligation'}


class Transport:
    """A scripted provider: each call returns the next (record, value); no harness, network or model runs."""

    def __init__(self, *replies, sleep_until=None):
        self.replies = list(replies)
        self.calls = []
        self.sleep_until = sleep_until

    def __call__(self, run_dir, label, definition, prompt, schema, timeout, **kwargs):
        self.calls.append({'label': label, 'definition': definition, 'prompt': prompt, 'timeout': timeout})
        if self.sleep_until is not None:
            time.sleep(max(0, self.sleep_until - time.monotonic()) + .05)
        reply = self.replies.pop(0)
        if isinstance(reply, tuple):
            return copy.deepcopy(reply)
        return {'backend': definition['harness'], 'execution': 'ok', 'billing_route': 'subscription',
                'model_requested': definition['model']}, copy.deepcopy(reply)


class PhysicalArchitectTests(unittest.TestCase):
    checked = classmethod(pi.PhysicalIntentTests.checked.__func__)
    board_run = classmethod(pi.PhysicalIntentTests.board_run.__func__)
    sources = pi.PhysicalIntentTests.sources
    intent = pi.PhysicalIntentTests.intent
    pair = pi.PhysicalIntentTests.pair
    locality = pi.PhysicalIntentTests.locality

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True, tools=('pdftotext',))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.control = cls.fixture_run('control')
        board = (pi.FIXTURE / 'control/pins.kicad_pcb').read_text()
        local = (board.replace('(net "unconnected-(U1-VCC-Pad3)")', '(net "VCC")')
                 .replace('(net "unconnected-(U1-GND-Pad2)")', '(net "GND")')
                 .replace('\t(gr_rect', pi.C1, 1))
        cls.local = cls.board_run('local', local, {})

    @classmethod
    def fixture_run(cls, name):
        return cls.checked(pi.FIXTURE / f'{name}.scope.json', name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        runtime = patch('agent_ee.cli.runtime_files', return_value=RUNTIME)
        runtime.start()
        self.addCleanup(runtime.stop)
        self.slot = self.root / self.id().rsplit('.', 1)[-1]
        self.slot.mkdir()

    def architect(self, transport, run=None, *, deadline=60, **kwargs):
        run = run or self.control
        before = input_identity(run)
        transport.deadline = time.monotonic() + deadline
        name = f'{len(list(self.slot.glob("stage-*")))}'
        stage, out = self.slot / f'stage-{name}', self.slot / f'package-{name}'
        with patch('agent_ee.reviewers.dispatch', side_effect=transport):
            receipt = propose(run, out, definition=dict(DEFINITION), deadline=transport.deadline, stage=stage,
                              **kwargs)
        # The run is evidence: no net, pin, board or schematic byte changes in any outcome.
        self.assertEqual(input_identity(run), before)
        self.assertEqual(read_json(stage / 'architect-receipt.json'), receipt)
        self.assertIsNone(receipt['fallback'])
        self.assertTrue(all(c['label'] == 'architect' and c['definition'] == {**DEFINITION, 'tool_mode': 'fixed_packet'}
                            for c in transport.calls), 'only the configured route is ever dispatched')
        if receipt['status'] == 'accepted':
            self.assertTrue(out.is_dir())
            verify_stage(stage, run, out)
        else:
            self.assertFalse(out.exists(), 'a stage that is not accepted publishes nothing')
            self.assertFalse((stage / 'proposed-intent.json').exists() and receipt['status'] != 'unresolved')
            verify_stage(stage, run)
        return receipt, stage, out

    # ------------------------------------------------------------ positive

    def test_one_proposal_compiles_and_every_assertion_is_bound(self):
        transport = Transport({'terminals': CORRECT, 'obligations': [KELVIN]})
        receipt, stage, out = self.architect(transport, reference=self.intent(obligations=[KELVIN]), deadline=30)
        self.assertEqual((receipt['status'], receipt['attempts'], receipt['corrections_used']), ('accepted', 1, 0))
        self.assertEqual(len(transport.calls), 1)
        # The call timeout is the remaining caller deadline (30 s), below the profile's 60 s.
        self.assertTrue(20 < transport.calls[0]['timeout'] < 30)
        call = transport.calls[0]
        # Captured evidence is supplied; the source binding is the coordinator's, never the model's.
        for text in ('"canl"', 'unconnected-(U1-CANL-Pad6)', pi.PAD['6'], '"pins"'):
            self.assertIn(text, call['prompt'])
        intent = read_json(stage / 'proposed-intent.json')
        self.assertEqual(intent['sources'], self.sources(self.control))
        self.assertEqual(receipt['bindings'], {
            'can-kelvin': ['unsupported'],
            'u1-canh': ['/contracts/topology-rules.json#/rules/1'],
            'u1-canl': ['/contracts/topology-rules.json#/rules/0']})
        self.assertEqual(receipt['comparison']['unresolved'], [])
        # The required Kelvin obligation stays visible: no physical compliance is claimed.
        self.assertEqual((receipt['semantic_completion'], receipt['required_unsupported']),
                         ('unsupported', ['can-kelvin']))
        package = physical_intent.verify_receipt(out, self.control, intent)
        self.assertEqual(package['package_id'], receipt['package_id'])
        self.assertEqual(package, compile_plan(self.control, self.intent(obligations=[KELVIN]))['receipt.json'])

    # ------------------------------------------------------ invalid outputs

    def test_compiler_refusal_gets_exactly_one_bounded_correction(self):
        transport = Transport({'terminals': SWAPPED}, {'terminals': CORRECT})
        receipt, stage, out = self.architect(transport)
        self.assertEqual((receipt['status'], receipt['attempts'], receipt['corrections_used']), ('accepted', 2, 1))
        self.assertNotIn('OUTPUT VALIDATION ERROR:\n', transport.calls[0]['prompt'])
        self.assertIn('OUTPUT VALIDATION ERROR:\n', transport.calls[1]['prompt'])
        self.assertIn('verified CANL fact binds U1 pin 6, not proposed pin 7', transport.calls[1]['prompt'])
        first = read_json(stage / 'review-attempts/architect/0/execution.json')
        self.assertEqual((first['execution'], first['refusal_kind']), ('malformed', 'structured_output'))
        self.assertEqual(len(receipt['proposals']), 2)

    def test_invalid_outputs_stop_after_one_correction_and_publish_nothing(self):
        forged = {'terminals': CORRECT, 'sources': {'candidate_id': '0' * 64}}
        cases = {
            'forged sources then wrong pin': (forged, {'terminals': SWAPPED}, 'verified CANL fact binds'),
            'no object then uncaptured record': (None, {'terminals': CORRECT, 'records': ['ghost.md']},
                                                 'not captured inputs'),
            'wrong shape twice': ({'terminals': 'U1'}, {'terminals': []}, 'terminals'),
            'invented requirement': ({'terminals': [{**CORRECT[0], 'requirement': 'made-up'}]},
                                     {'terminals': [{**CORRECT[0], 'requirement': 'made-up'}]}, 'made-up'),
        }
        for name, (first, second, pattern) in cases.items():
            with self.subTest(name):
                first_reply = ({'backend': 'claude', 'execution': 'ok'}, None) if first is None else first
                transport = Transport(first_reply, second)
                receipt, stage, _ = self.architect(transport)
                self.assertEqual((receipt['status'], receipt['execution'], receipt['attempts']),
                                 ('refused', 'malformed', 2))
                self.assertEqual(len(transport.calls), 2, 'never a third call')
                self.assertRegex(receipt['error'], pattern)

    # ----------------------------------------------- auth, quota and stops

    def test_auth_or_quota_failure_stops_without_any_fallback(self):
        failures = {
            'auth': ({'backend': 'claude', 'execution': 'unavailable',
                      'error': 'Claude subscription credential file unavailable; no fallback attempted'}, None),
            'quota': ({'backend': 'claude', 'execution': 'failed',
                       'error': 'provider/harness did not report a successful complete review'}, None),
        }
        for name, failure in failures.items():
            with self.subTest(name):
                transport = Transport(failure, {'terminals': CORRECT})
                receipt, stage, _ = self.architect(transport)
                self.assertEqual(len(transport.calls), 1, 'an execution failure is never retried or rerouted')
                self.assertEqual((receipt['status'], receipt['execution'], receipt['attempts']),
                                 ('stopped', failure[0]['execution'], 1))
                self.assertEqual(receipt['error'], failure[0]['error'])
        source = Path(physical_architect.__file__).read_text().lower()
        self.assertNotIn('openrouter', source)

    def test_cancellation_and_exhausted_deadline_stop_before_launch(self):
        cancelled = Cancellation()
        cancelled.cancel()
        transport = Transport({'terminals': CORRECT})
        receipt, stage, _ = self.architect(transport, cancellation=cancelled)
        self.assertEqual((receipt['status'], receipt['execution'], transport.calls), ('stopped', 'not_run', []))
        self.assertIn('cancelled', receipt['error'])
        transport = Transport({'terminals': CORRECT})
        receipt, _, _ = self.architect(transport, deadline=-1)
        self.assertEqual((receipt['status'], receipt['execution'], transport.calls), ('stopped', 'not_run', []))
        self.assertIn('deadline_exhausted', receipt['error'])

    def test_correction_shares_the_original_deadline(self):
        transport = Transport({'terminals': SWAPPED}, {'terminals': CORRECT})
        transport.sleep_until = time.monotonic() + 10
        receipt, _, _ = self.architect(transport, deadline=10)
        self.assertEqual(len(transport.calls), 1, 'no correction launches after the deadline')
        self.assertEqual((receipt['status'], receipt['execution']), ('stopped', 'not_run'))
        self.assertIn('deadline_exhausted', receipt['error'])

    # ----------------------------------------------- misbound and weakening

    def test_seeded_misbound_return_is_exposed_without_changing_nets(self):
        reference = self.intent(self.local, locality=self.locality(self.pair()))
        right = reference['locality'][0]
        misbound = [{**right, 'supply': right['return'], 'return': right['supply']}]
        # The S1 compiler alone accepts the swapped supply/return: both pairs are same-net native pads.
        compile_plan(self.local, self.intent(self.local, locality=copy.deepcopy(misbound)))
        sources = self.sources(self.local)
        transport = Transport({'terminals': CORRECT, 'locality': misbound})
        receipt, stage, _ = self.architect(transport, self.local, reference=reference)
        self.assertEqual(receipt['status'], 'unresolved')
        comparison = receipt['comparison']
        self.assertEqual((comparison['matched'], comparison['unresolved']),
                         (['u1-canh', 'u1-canl'], ['u1-decoupling']))
        self.assertEqual(comparison['differs'][0]['reference'], right)
        self.assertEqual(comparison['differs'][0]['proposed']['return']['from']['net'], 'VCC')
        # Net membership and the reference obligation are untouched.
        self.assertEqual(self.sources(self.local), sources)
        self.assertEqual(read_json(stage / 'proposed-intent.json')['sources'], sources)
        missing = compare(self.intent(self.local), reference)
        self.assertEqual((missing['missing'], missing['unresolved']), (['u1-decoupling'], ['u1-decoupling']))
        extra = compare(reference, self.intent(self.local))
        self.assertEqual(extra['extra'], ['u1-decoupling'])

    def test_correction_cannot_weaken_obligations(self):
        stale = self.pair()
        stale['to']['uuid'] = pi.C1_GND
        broken = {'terminals': CORRECT, 'locality': self.locality(stale), 'obligations': [KELVIN]}
        loose = self.locality(self.pair(limit=999_000_000))
        cases = {
            'drops locality': ({'terminals': CORRECT, 'obligations': [KELVIN]}, 'drops locality obligation u1-decoupling'),
            'loosens bound': ({'terminals': CORRECT, 'locality': loose, 'obligations': [KELVIN]},
                              'loosens u1-decoupling supply max_distance_nm 3000000 -> 999000000'),
            'drops required kelvin': ({'terminals': CORRECT, 'locality': self.locality(self.pair())},
                                      'drops required obligation can-kelvin'),
            'unrequires kelvin': ({'terminals': CORRECT, 'locality': self.locality(self.pair()),
                                   'obligations': [{**KELVIN, 'required': False}]},
                                  'drops required obligation can-kelvin'),
        }
        for name, (correction, pattern) in cases.items():
            with self.subTest(name):
                transport = Transport(broken, correction)
                receipt, _, _ = self.architect(transport, self.local)
                self.assertEqual((receipt['status'], len(transport.calls)), ('refused', 2))
                self.assertIn(pattern, receipt['error'])
        transport = Transport(broken, {'terminals': CORRECT, 'locality': self.locality(self.pair()),
                                       'obligations': [KELVIN]})
        receipt, _, _ = self.architect(transport, self.local)
        self.assertEqual((receipt['status'], receipt['semantic_completion']), ('accepted', 'unsupported'))

    # ------------------------------------------------------- mode and scope

    def test_manual_and_disabled_modes_remain_usable(self):
        out = self.slot / 'manual'
        self.assertIsNone(produce('disabled', self.control, self.slot / 'disabled'))
        self.assertFalse((self.slot / 'disabled').exists())
        with patch('agent_ee.reviewers.dispatch') as dispatch:
            self.assertEqual(produce('manual', self.control, out, intent=self.intent()), out)
            physical_intent.verify_receipt(out, self.control, self.intent())
            with self.assertRaisesRegex(IntentRefused, 'explicitly selected intent'):
                produce('manual', self.control, self.slot / 'm2')
            with self.assertRaisesRegex(IntentRefused, 'starts no architect call'):
                produce('manual', self.control, self.slot / 'm3', intent=self.intent(), definition=DEFINITION)
            with self.assertRaisesRegex(IntentRefused, 'never its substitute'):
                produce('architect', self.control, self.slot / 'm4', intent=self.intent())
            with self.assertRaisesRegex(IntentRefused, 'mode must be one of'):
                produce('auto', self.control, self.slot / 'm5')
            dispatch.assert_not_called()

    def test_architect_is_not_wired_into_any_runtime_path(self):
        users = [p.name for p in (pi.ROOT / 'src/agent_ee').glob('*.py')
                 if p.name != 'physical_architect.py' and 'physical_architect' in p.read_text()]
        self.assertEqual(users, [], 'S2 must not be wired into a default, runtime, campaign or routing path')


if __name__ == '__main__':
    unittest.main()
