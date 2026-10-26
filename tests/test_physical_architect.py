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
from agent_ee.harnesses import strict_schema
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

# Review ee389-s2 H1: a required obligation's full semantic restriction, not only its ID.
KELVIN_FULL = {**KELVIN, 'joins': [pi.PAD['6']], 'forbidden_shared': [['u1-canl', 'u1-canh']]}


def conforms(value, schema, path='$'):
    """The JSON Schema subset the proposal schemas use, as a provider enforcing it would apply it."""
    if 'anyOf' in schema:
        errors = [conforms(value, branch, path) for branch in schema['anyOf']]
        return [] if any(not e for e in errors) else [e for group in errors for e in group]
    kinds = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}
    kind = schema.get('type')
    if kind == 'integer':
        if type(value) is not int:
            return [f'{path}: not an integer']
    elif kind and not isinstance(value, kinds[kind]):
        return [f'{path}: not {kind}']
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: {value!r} not in enum']
    errors = []
    if kind == 'object':
        properties = schema.get('properties', {})
        errors += [f'{path}: missing {k}' for k in schema.get('required', []) if k not in value]
        if schema.get('additionalProperties') is False:
            errors += [f'{path}: unexpected {k}' for k in value if k not in properties]
        for key, item in value.items():
            if key in properties:
                errors += conforms(item, properties[key], f'{path}.{key}')
    if kind == 'array':
        for i, item in enumerate(value):
            errors += conforms(item, schema.get('items', {}), f'{path}[{i}]')
    return errors


def wire(value, schema):
    """The Codex wire form: every optional property the strict schema closes is present as null."""
    if 'anyOf' in schema:
        branch = next(b for b in schema['anyOf'] if b.get('type') != 'null')
        return None if value is None else wire(value, branch)
    if schema.get('type') == 'object':
        return {key: wire(value.get(key), item) for key, item in schema.get('properties', {}).items()}
    if schema.get('type') == 'array':
        return [wire(item, schema.get('items', {})) for item in value]
    return value


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
        if getattr(self, 'on_call', None):
            self.on_call()
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
        self.host = {'runtime_files': RUNTIME}
        self.slot = self.root / self.id().rsplit('.', 1)[-1]
        self.slot.mkdir()

    def architect(self, transport, run=None, *, deadline=60, definition=DEFINITION, **kwargs):
        run = run or self.control
        before = input_identity(run)
        transport.deadline = time.monotonic() + deadline
        name = f'{len(list(self.slot.glob("stage-*")))}'
        stage, out = self.slot / f'stage-{name}', self.slot / f'package-{name}'
        with patch('agent_ee.reviewers.dispatch', side_effect=transport):
            receipt = propose(run, out, definition=dict(definition), deadline=transport.deadline, stage=stage,
                              **kwargs, host=self.host)
        # The run is evidence: no net, pin, board or schematic byte changes in any outcome.
        self.assertEqual(input_identity(run), before)
        self.assertEqual(read_json(stage / 'architect-receipt.json'), receipt)
        self.assertIsNone(receipt['fallback'])
        self.assertTrue(all(c['label'] == 'architect' and c['definition'] == {**definition, 'tool_mode': 'fixed_packet'}
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
        # The refused first response returns inside the 5 s validation reserve (RESERVE_S) of the 10 s
        # deadline: it is still validated, but the correction may no longer launch.
        transport.sleep_until = time.monotonic() + 7
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
        # Review ee389-s2 gap: a dropped reference diagnostic is unresolved too.
        access = {'id': 'u1-access', 'kind': 'access', 'status': 'unknown', 'statement': 'fixture access'}
        dropped = compare(self.intent(self.local), self.intent(self.local, diagnostics=[access]))
        self.assertEqual((dropped['missing'], dropped['unresolved']), (['u1-access'], ['u1-access']))

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

    # ------------------------------------------------ review ee389-s2 H1-H3

    def test_correction_preserves_required_obligation_content(self):
        """H1: a correction may fix the refused pins, not replace a required obligation's restrictions."""
        first = {'terminals': SWAPPED, 'obligations': [KELVIN_FULL]}
        mutations = {
            'kelvin to current_loop, one terminal, no forbidden_shared': {
                **KELVIN, 'kind': 'current_loop', 'terminals': ['u1-canl']},
            'drops forbidden_shared': {k: v for k, v in KELVIN_FULL.items() if k != 'forbidden_shared'},
            'drops joins': {k: v for k, v in KELVIN_FULL.items() if k != 'joins'},
            'drops a terminal': {**KELVIN_FULL, 'terminals': ['u1-canl'], 'forbidden_shared': []},
        }
        for name, replaced in mutations.items():
            with self.subTest(name):
                transport = Transport(first, {'terminals': CORRECT, 'obligations': [replaced]})
                receipt, _, out = self.architect(transport)
                self.assertEqual((receipt['status'], len(transport.calls)), ('refused', 2))
                self.assertIn('changes required obligation can-kelvin', receipt['error'])
                self.assertFalse(out.exists())
        transport = Transport(first, {'terminals': CORRECT, 'obligations': [KELVIN_FULL]})
        receipt, _, _ = self.architect(transport)
        self.assertEqual((receipt['status'], receipt['required_unsupported']), ('accepted', ['can-kelvin']))

    def test_correction_preserves_locality_relationship_identity(self):
        """H1: the supply/return relationship keeps its endpoints, not only its numeric bound."""
        stale = self.pair()
        stale['to']['uuid'] = pi.C1_GND
        broken = {'terminals': CORRECT, 'locality': self.locality(stale)}
        right = self.locality(self.pair())[0]
        swapped = [{**right, 'supply': right['return'], 'return': right['supply']}]
        transport = Transport(broken, {'terminals': CORRECT, 'locality': swapped})
        receipt, _, out = self.architect(transport, self.local)
        self.assertEqual((receipt['status'], len(transport.calls)), ('refused', 2))
        self.assertIn('changes locality relationship u1-decoupling', receipt['error'])
        self.assertFalse(out.exists())

    def test_conflicting_duplicate_required_ids_refuse_in_both_orders(self):
        """H1 delta: a duplicate required ID never lets a weaker entry hide the stronger one."""
        weak = {**KELVIN, 'kind': 'current_loop', 'terminals': ['u1-canl']}
        orders = {'strong then weak': [KELVIN_FULL, weak], 'weak then strong': [weak, KELVIN_FULL]}
        for order, obligations in orders.items():
            # Each correction keeps one entry; the last one is what a last-wins map retained.
            for kept in ('last', 'first'):
                with self.subTest(order=order, kept=kept):
                    correction = obligations[-1] if kept == 'last' else obligations[0]
                    transport = Transport({'terminals': CORRECT, 'obligations': copy.deepcopy(obligations)},
                                          {'terminals': CORRECT, 'obligations': [correction]})
                    receipt, _, out = self.architect(transport)
                    self.assertEqual((receipt['status'], len(transport.calls)), ('refused', 2))
                    self.assertIn('first proposal has conflicting duplicate required obligation can-kelvin',
                                  receipt['error'])
                    self.assertFalse(out.exists())
        # An exact duplicate hides nothing: the correction that keeps its one restriction is accepted.
        transport = Transport({'terminals': CORRECT, 'obligations': [KELVIN_FULL, copy.deepcopy(KELVIN_FULL)]},
                              {'terminals': CORRECT, 'obligations': [KELVIN_FULL]})
        receipt, _, _ = self.architect(transport)
        self.assertEqual((receipt['status'], receipt['required_unsupported']), ('accepted', ['can-kelvin']))

    def test_conflicting_duplicate_locality_ids_refuse_in_both_orders(self):
        """H1 delta: the same for locality: a looser duplicate never stands in for the tighter bound."""
        strong = self.locality(self.pair())[0]
        weak = self.locality(self.pair(limit=999_000_000))[0]
        for order, locality in {'strong then weak': [strong, weak], 'weak then strong': [weak, strong]}.items():
            with self.subTest(order=order):
                transport = Transport({'terminals': CORRECT, 'locality': copy.deepcopy(locality)},
                                      {'terminals': CORRECT, 'locality': [locality[-1]]})
                receipt, _, out = self.architect(transport, self.local)
                self.assertEqual((receipt['status'], len(transport.calls)), ('refused', 2))
                self.assertIn('first proposal has conflicting duplicate locality obligation u1-decoupling',
                              receipt['error'])
                self.assertFalse(out.exists())

    def test_on_time_response_with_late_validation_publishes_nothing(self):
        """H2: the deadline bounds compilation, comparison, verification and publication too."""
        transport = Transport({'terminals': CORRECT})
        transport.sleep_until = time.monotonic() + 10 - .15  # returns about 0.1 s before the deadline
        receipt, stage, out = self.architect(transport, deadline=10)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual((receipt['status'], receipt['execution']), ('stopped', 'ok'))
        self.assertIn('deadline_exhausted', receipt['error'])
        self.assertFalse(out.exists())
        self.assertEqual([p.name for p in out.parent.iterdir() if p.name.startswith(f'.{out.name}.')], [])
        self.assertFalse((stage / 'proposed-intent.json').exists())

    def test_late_response_or_cancellation_publishes_nothing(self):
        """H2: a response after the deadline, or a stop during the call, never publishes."""
        transport = Transport({'terminals': CORRECT})
        transport.sleep_until = time.monotonic() + 10 + .2
        receipt, _, out = self.architect(transport, deadline=10)
        self.assertEqual((receipt['status'], len(transport.calls)), ('stopped', 1))
        self.assertIn('deadline_exhausted', receipt['error'])
        self.assertFalse(out.exists())
        cancellation = Cancellation()
        transport = Transport({'terminals': CORRECT})
        transport.on_call = cancellation.cancel
        receipt, _, out = self.architect(transport, cancellation=cancellation)
        self.assertEqual((receipt['status'], len(transport.calls)), ('stopped', 1))
        self.assertIn('cancelled', receipt['error'])
        self.assertFalse(out.exists())

    def test_launch_reserves_validation_time(self):
        """H2: each call's timeout leaves the validation reserve inside the caller deadline."""
        transport = Transport({'terminals': CORRECT})
        receipt, _, _ = self.architect(transport, deadline=30, reserve_s=12)
        self.assertEqual(receipt['status'], 'accepted')
        self.assertTrue(10 < transport.calls[0]['timeout'] <= 18)

    def test_codex_strict_schema_expresses_compiler_valid_proposals(self):
        """H3: the strict (Codex) form of the wire schema admits every compiler-valid assertion shape."""
        strict = strict_schema(physical_architect.PROPOSAL_SCHEMA)

        def objects(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    yield node
                for value in node.values():
                    yield from objects(value)
            elif isinstance(node, list):
                for value in node:
                    yield from objects(value)
        for node in objects(strict):
            self.assertTrue(node.get('properties'), f'bare object closed by strict conversion: {node}')
        datasheet = {'class': 'datasheet', 'basis': 'L-2', 'derivation': {'kind': 'numeric', 'value_nm': 3_000_000,
                     'source': {'path': 'layout-notes.md', 'sha256': '0' * 64, 'locator': 'L-2', 'quote': 'within 3 mm'}}}
        owner = {'class': 'owner', 'basis': 'D-1', 'decision_reference': {
            'path': 'owner-decisions.md', 'sha256': '0' * 64, 'locator': 'D-1', 'quote': 'within 3 mm', 'owner': 'o'}}
        right = self.locality(self.pair(datasheet))[0]
        loop = {**right, 'return': {**right['return'], 'provenance': owner}, 'kind': 'buck-output-loop', 'ic': 'U1',
                'inductor': 'L1', 'return_net': 'GND', 'switch': self.pair()}
        full = {'terminals': CORRECT, 'blocks': [{'id': 'can', 'references': ['U1', 'C1'], 'anchor': 'U1'}],
                'residual': ['H1'], 'moves': [{'reference': 'C1', 'allow': ['rotate', 'translate']}],
                'locality': [right, loop], 'obligations': [KELVIN_FULL | {'proxy': 'u1-decoupling'}],
                'diagnostics': [{'id': 'u1-access', 'kind': 'access', 'status': 'unknown', 'statement': 's'}],
                'records': ['layout-notes.md']}
        self.assertEqual(conforms(full, physical_architect.PROPOSAL_SCHEMA), [])
        self.assertEqual(conforms(wire(full, strict), strict), [])
        extra = wire({'terminals': [CORRECT[0]]}, strict)
        extra['terminals'][0]['extra'] = 1
        self.assertTrue(conforms(extra, strict))
        self.assertTrue(conforms({'terminals': [{**CORRECT[0], 'extra': 1}]}, physical_architect.PROPOSAL_SCHEMA))
        # The Codex wire form, nulls included, compiles end to end through the scripted transport.
        codex = {'harness': 'codex', 'model': 'scripted-architect', 'effort': 'high', 'timeout_s': 60}
        proposal = {'terminals': CORRECT, 'locality': self.locality(self.pair()), 'obligations': [KELVIN],
                    'diagnostics': [{'id': 'u1-access', 'kind': 'access', 'status': 'unknown', 'statement': 's'}]}
        sent = wire(proposal, strict)
        self.assertIsNone(sent['records'])
        self.assertIsNone(sent['obligations'][0]['joins'])
        self.assertEqual(conforms(sent, strict), [])
        transport = Transport(sent)
        receipt, stage, out = self.architect(transport, self.local, definition=codex)
        self.assertEqual(receipt['status'], 'accepted')
        self.assertEqual(read_json(stage / 'proposed-intent.json')['obligations'], [KELVIN])


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
