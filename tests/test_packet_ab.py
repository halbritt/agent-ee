import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_ee import packet_ab
from agent_ee.storage import identity


DEFINITION = {'harness': 'claude', 'model': 'claude-fable-5'}
# One fresh interpreter per call: reserve, launch an inert fake provider, settle. Prints the outcome.
RESTARTED_CALL = '''
import sys
from pathlib import Path
from agent_ee import packet_ab
launched = []
def provider(*args, **kwargs):
    launched.append(1)
    return {'execution': 'ok', 'duration_s': 0.0}, {}
ledger, cell = Path(sys.argv[1]), sys.argv[2]
record, _ = packet_ab.metered(packet_ab.CallLedger(ledger), 'live', 'plan', provider)(
    ledger.parent / cell, 'circuit', {'harness': 'fake', 'model': 'fake'}, 'p', {}, 1)
print(record['execution'], len(launched))
'''


class CallLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'ledger' / 'calls.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def test_thirteenth_real_call_is_refused_and_recorded_across_restarts(self):
        for n in range(7):
            packet_ab.CallLedger(self.path).reserve('real', f'cell-{n}', 'circuit', 'plan')
        restarted = packet_ab.CallLedger(self.path)
        for n in range(7, 12):
            restarted.reserve('real', f'cell-{n}', 'circuit', 'plan')
        with self.assertRaises(packet_ab.CallCapReached):
            packet_ab.CallLedger(self.path).reserve('real', 'cell-12', 'circuit', 'plan')
        state = packet_ab.CallLedger(self.path).state()
        self.assertEqual((state['real_calls'], state['refused'], len(state['unsettled'])), (12, 1, 12))
        self.assertEqual(packet_ab.CallLedger(self.path).remaining(), 0)

    def test_fake_calls_never_spend_budget(self):
        ledger = packet_ab.CallLedger(self.path)
        for n in range(20):
            ledger.reserve('fake', f'cell-{n}', 'circuit', 'plan')
        self.assertEqual(ledger.state()['real_calls'], 0)
        self.assertEqual(ledger.remaining(), packet_ab.CAP)

    def test_torn_line_counts_as_a_call_and_cap_cannot_be_raised(self):
        ledger = packet_ab.CallLedger(self.path)
        ledger.reserve('real', 'a', 'circuit', 'plan')
        with self.path.open('a') as handle:
            handle.write('{"event": "reserve", "kind": "re')
        self.assertEqual(ledger.state()['real_calls'], 2)
        with self.assertRaises(ValueError):
            packet_ab.CallLedger(self.path, cap=5).state()
        with self.assertRaises(ValueError):
            packet_ab.CallLedger(self.path, cap=packet_ab.CAP + 1)

    def restarted_call(self, cell):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [
            str(Path(packet_ab.__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')])))
        result = subprocess.run([sys.executable, '-c', RESTARTED_CALL, str(self.path), cell], env=env,
                                capture_output=True, text=True, timeout=60, check=True)
        execution, launched = result.stdout.split()
        return execution, int(launched)

    def assert_damaged_tail_keeps_its_charge(self, tail):
        """Ten settled real calls, then a crash while writing the eleventh reservation."""
        for n in range(10):
            seq = packet_ab.CallLedger(self.path).reserve('real', f'cell-{n}', 'circuit', 'plan')
            packet_ab.CallLedger(self.path).settle(seq, {'execution': 'ok'})
        with self.path.open('a') as handle:
            handle.write(tail)
        damaged = self.path.read_text()
        outcomes = [self.restarted_call(f'after-{n}') for n in range(3)]
        # The damaged tail is the eleventh call, so exactly one more launches; the thirteenth is refused.
        self.assertEqual(outcomes, [('ok', 1), ('not_run', 0), ('not_run', 0)])
        text = self.path.read_text()
        self.assertTrue(text.startswith(damaged + '\n'), 'damaged tail must be kept, not rewritten')
        self.assertTrue(text.endswith('\n'))
        records = [json.loads(line) for line in text[len(damaged) + 1:].splitlines()]
        self.assertEqual([r['event'] for r in records], ['tail_terminated', 'reserve', 'settle', 'refused', 'refused'])
        self.assertEqual((records[1]['cell'], records[1]['real_calls_after']), ('after-0', 12))
        self.assertEqual(records[2]['seq'], records[1]['seq'])
        state = packet_ab.CallLedger(self.path).state()
        self.assertEqual((state['real_calls'], state['refused'], state['tails_terminated']), (12, 2, 1))
        self.assertEqual(self.path.read_text(), text, 'a terminated ledger is not repaired again')
        return state

    def test_unterminated_complete_reservation_is_kept_and_cannot_absorb_the_next(self):
        tail = json.dumps({'event': 'reserve', 'seq': 21, 'kind': 'real', 'cell': 'cell-10', 'label': 'circuit',
                           'plan_id': 'plan', 'at_utc': 'x', 'real_calls_after': 11}, sort_keys=True)
        state = self.assert_damaged_tail_keeps_its_charge(tail)
        self.assertEqual(state['malformed_lines'], 0)
        self.assertEqual(state['unsettled'], [21])

    def test_partial_reservation_is_charged_and_cannot_absorb_the_next(self):
        state = self.assert_damaged_tail_keeps_its_charge('{"event": "reserve", "kind": "re')
        self.assertEqual((state['malformed_lines'], state['unsettled']), (1, []))

    def assert_invalid_utf8_refuses_launch_and_keeps_bytes(self, damage, terminated):
        for n in range(11):
            seq = packet_ab.CallLedger(self.path).reserve('real', f'cell-{n}', 'circuit', 'plan')
            if n < 10:
                packet_ab.CallLedger(self.path).settle(seq, {'execution': 'ok'})
        raw = self.path.read_bytes()
        prefix, last = raw.rstrip(b'\n').rsplit(b'\n', 1)
        old, new = damage
        self.assertIn(old, last)
        damaged = prefix + b'\n' + last.replace(old, new) + (b'\n' if terminated else b'')
        self.path.write_bytes(damaged)
        for _ in range(2):
            with self.assertRaises(ValueError):
                packet_ab.CallLedger(self.path).state()
            with self.assertRaises(ValueError):
                packet_ab.CallLedger(self.path).reserve('real', 'after', 'circuit', 'plan')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [
            str(Path(packet_ab.__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')])))
        for n in range(2):
            result = subprocess.run([sys.executable, '-c', RESTARTED_CALL, str(self.path), f'r-{n}'], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '', 'no provider may launch against an undecodable ledger')
        self.assertEqual(self.path.read_bytes(), damaged, 'ledger bytes are never rewritten or discarded')

    def test_invalid_utf8_in_complete_json_refuses_launch_in_every_field(self):
        for old, new in ((b'"kind": "real"', b'"kind": "rea\xff"'), (b'"event": "reserve"', b'"event": "reser\xff"'),
                         (b'"cell": "cell-10"', b'"cell": "cell-\xff0"'), (b'"plan_id": "plan"', b'"plan_id": "pl\xffn"')):
            for terminated in (True, False):
                with self.subTest(field=old, terminated=terminated):
                    self.tearDown()
                    self.setUp()
                    self.assert_invalid_utf8_refuses_launch_and_keeps_bytes((old, new), terminated)

    def test_torn_header_is_refused_without_writing(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"schema": "agent-ee.call-led')
        for _ in range(2):
            with self.assertRaises(ValueError):
                packet_ab.CallLedger(self.path).reserve('real', 'a', 'circuit', 'plan')
        self.assertEqual(self.path.read_text(), '{"schema": "agent-ee.call-led')

    def test_reservation_is_durable_before_launch_and_each_retry_is_a_call(self):
        ledger = packet_ab.CallLedger(self.path)
        seen = []

        def provider(run_dir, label, definition, prompt, schema, timeout, **_):
            seen.append(packet_ab.CallLedger(self.path).state()['real_calls'])
            return {'execution': 'malformed'}, None

        run_dir = Path(self.tmp.name) / 'cell-x'
        dispatch = packet_ab.metered(ledger, 'live', 'plan', provider)
        for _ in range(2):
            dispatch(run_dir, 'circuit', DEFINITION, 'p', {}, 1)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(ledger.state()['real_calls'], 2)
        self.assertEqual(len(list((run_dir / 'ab-attempts').iterdir())), 2)

    def test_refused_reservation_never_launches_the_provider(self):
        ledger = packet_ab.CallLedger(self.path, cap=1)
        launched = []

        def provider(*args, **kwargs):
            launched.append(args)
            return {'execution': 'ok'}, {}

        dispatch = packet_ab.metered(ledger, 'live', 'plan', provider)
        dispatch(Path(self.tmp.name) / 'cell-a', 'circuit', DEFINITION, 'p', {}, 1)
        record, value = dispatch(Path(self.tmp.name) / 'cell-b', 'circuit', DEFINITION, 'p', {}, 1)
        self.assertEqual(len(launched), 1)
        self.assertEqual((record['execution'], value), ('not_run', None))
        self.assertIn('cap 1 reached', record['error'])
        self.assertEqual(ledger.state()['refused'], 1)

    def test_live_run_refuses_a_volatile_ledger(self):
        with self.assertRaises(ValueError):
            packet_ab.run(self.tmp.name, '/tmp/ledger.jsonl', 'live')


class PacketAndScoringTests(unittest.TestCase):
    def test_historical_packet_drops_only_the_navigation_extension(self):
        packet = {'schema': 'agent-ee.packet/1', 'candidate_id': 'c', 'run_id': 'r', 'checks': [],
                  'navigation': {'entries': []},
                  'evidence_access': {'candidate': '/candidate/<path>', 'navigation': 'n', 'binding': 'b'},
                  'citation_files': [{'path': 'checks/x.json'}, {'path': 'work/.navigation/index.json'}]}
        packet['packet_sha256'] = identity(packet)
        old = packet_ab.historical_packet(packet)
        self.assertNotIn('.navigation', json.dumps(old))
        self.assertEqual(old['evidence_access'], {'candidate': '/candidate/<path>'})
        self.assertEqual(old['citation_files'], [{'path': 'checks/x.json'}])
        self.assertEqual(old['packet_sha256'], identity({k: v for k, v in old.items() if k != 'packet_sha256'}))
        self.assertEqual({k: v for k, v in old.items() if k not in ('evidence_access', 'citation_files', 'packet_sha256')},
                         {k: v for k, v in packet.items() if k not in ('navigation', 'evidence_access',
                                                                       'citation_files', 'packet_sha256')})

    @staticmethod
    def finding(text, assessment='defect', path='checks/rc-step.json'):
        return {'id': 'f', 'title': text, 'location': '', 'condition': '', 'mechanism': '', 'reasoning': '',
                'assessment': assessment, 'evidence': [{'path': path}]}

    def test_seed_scoring_requires_entity_and_fact(self):
        hit = packet_ab.score('seeded', {'findings': [self.finding('R1 is 100k, not 10k')]})
        self.assertEqual((hit['seed_detected'], hit['seed_detected_as_defect']), (True, True))
        self.assertTrue(packet_ab.score('seeded', {'findings': [self.finding('R1 time constant 10 ms', 'concern')]})
                        ['seed_detected'])
        for text in ('rc-step fails at 0.095 V', 'R10 is 100k', 'R1 footprint is 0603'):
            self.assertTrue(packet_ab.score('seeded', {'findings': [self.finding(text)]})['seed_missed'], text)

    def test_failed_review_is_not_a_miss_and_control_counts_false_alarms(self):
        self.assertEqual(packet_ab.score('seeded', None)['scored'], False)
        control = packet_ab.score('control', {'findings': [
            self.finding('x', path='work/.navigation/index.json'), self.finding('y', 'concern')]})
        self.assertEqual((control['false_alarm_defects'], control['navigation_citations']),
                         (1, ['work/.navigation/index.json']))

    def test_plan_is_bounded_and_arm_order_alternates(self):
        plan = packet_ab.plan()
        self.assertLessEqual(len(plan['cells']) * (1 + plan['max_schema_resubmissions']), 2 * packet_ab.CAP)
        self.assertLessEqual(len(plan['cells']), packet_ab.CAP)
        firsts = [plan['cells'][i]['arm'] for i in range(0, len(plan['cells']), 2)]
        self.assertEqual(firsts, ['baseline', 'indexed', 'indexed', 'baseline'])
        self.assertEqual(identity(plan), identity(packet_ab.plan()))


class NativeDryRunTests(unittest.TestCase):
    def test_full_plan_dry_run_spends_no_real_call(self):
        import os
        from unittest.mock import patch
        from agent_ee.storage import read_json
        from toolchain import require_toolchain
        require_toolchain(native=True)
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # An inert stand-in so preflight pins a harness identity; the fake provider never executes it.
            (root / 'claude').write_text('not a harness\n')
            with patch.dict(os.environ, {'AGENT_EE_CLAUDE_BINARY': str(root / 'claude')}):
                prepared = packet_ab.prepare(root / 'ab', repository=repo)
                outcome = packet_ab.run(root / 'ab', root / 'ledger.jsonl', 'dry-run')
            self.assertEqual(outcome['ledger']['real_calls'], 0)
            self.assertEqual(outcome['ledger']['fake_calls'], len(packet_ab.plan()['cells']))
            self.assertEqual([c['execution'] for c in outcome['cells']], ['ok'] * len(outcome['cells']))
            for cell in outcome['cells']:
                directory = root / 'ab' / 'cells' / cell['id']
                indexed = cell['arm'] == 'indexed'
                self.assertEqual((directory / 'work/.navigation').is_dir(), indexed)
                self.assertEqual(cell['packet_has_navigation'], indexed)
                self.assertEqual(cell['packet_sha256'], prepared[cell['fixture']]['arms'][cell['arm']]['packet_sha256'])
                self.assertEqual(read_json(directory / 'review-executions/circuit.json')['backend'], 'fake-dry-run')
                if cell['fixture'] == 'seeded':
                    self.assertTrue(cell['score']['seed_detected'])
            for fixture in packet_ab.FIXTURES:
                arms = root / 'ab' / 'arms' / fixture
                self.assertEqual(packet_ab.historical_packet(read_json(arms / 'indexed/packet.json')),
                                 read_json(arms / 'baseline/packet.json'))
            summary = packet_ab.report(outcome)
            self.assertTrue(summary['claim'].startswith('none'))
            self.assertEqual(summary['arms']['indexed']['seeded_detected'], 2)


if __name__ == '__main__':
    unittest.main()
