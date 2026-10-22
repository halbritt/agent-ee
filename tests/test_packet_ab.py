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
# The sweep's restarted call: one fresh interpreter makes one metered call against each listed ledger.
SWEEP_CALL = '''
import sys
from pathlib import Path
from agent_ee import packet_ab
launched = []
def provider(*args, **kwargs):
    launched.append(1)
    return {'execution': 'ok', 'duration_s': 0.0}, {}
for name in sys.stdin.read().split():
    ledger = Path(name)
    before = len(launched)
    try:
        record, _ = packet_ab.metered(packet_ab.CallLedger(ledger), 'live', 'plan', provider)(
            ledger.parent / ('after-' + sys.argv[1]), 'circuit', {'harness': 'fake', 'model': 'fake'}, 'p', {}, 1)
        outcome = record['execution']
    except packet_ab.LedgerRefused:
        outcome = 'refused'
    print(name, outcome, len(launched) - before)
'''
REPLACEMENTS = (0x00, 0x09, 0x0a, 0x0d, 0x20, 0x22, 0x2c, 0x30, 0x39, 0x3a, 0x5c, 0x61, 0x7b, 0x7d, 0x80, 0xff)


def child_env():
    return dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [
        str(Path(packet_ab.__file__).resolve().parents[1]), os.environ.get('PYTHONPATH')])))


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

    def test_torn_line_refuses_the_ledger_and_cap_cannot_be_raised(self):
        ledger = packet_ab.CallLedger(self.path)
        ledger.reserve('real', 'a', 'circuit', 'plan')
        with self.path.open('a') as handle:
            handle.write('{"event": "reserve", "kind": "re')
        damaged = self.path.read_bytes()
        with self.assertRaises(packet_ab.LedgerRefused):
            ledger.state()
        with self.assertRaises(ValueError):
            packet_ab.CallLedger(self.path, cap=5).state()
        with self.assertRaises(ValueError):
            packet_ab.CallLedger(self.path, cap=packet_ab.CAP + 1)
        self.assertEqual(self.path.read_bytes(), damaged)

    def restarted_call(self, cell):
        result = subprocess.run([sys.executable, '-c', RESTARTED_CALL, str(self.path), cell], env=child_env(),
                                capture_output=True, text=True, timeout=60, check=True)
        execution, launched = result.stdout.split()
        return execution, int(launched)

    def ledger_with_reservations(self, count=11, settled=10):
        for n in range(count):
            seq = packet_ab.CallLedger(self.path).reserve('real', f'cell-{n}', 'circuit', 'plan')
            if n < settled:
                packet_ab.CallLedger(self.path).settle(seq, {'execution': 'ok', 'duration_s': 1.5})

    def test_unterminated_complete_reservation_is_kept_and_cannot_absorb_the_next(self):
        """Ten settled real calls, then a crash before the eleventh reservation's newline."""
        self.ledger_with_reservations(10, 10)
        tail = json.dumps({'event': 'reserve', 'seq': 21, 'kind': 'real', 'cell': 'cell-10', 'label': 'circuit',
                           'plan_id': 'plan', 'at_utc': packet_ab._now(), 'real_calls_after': 11}, sort_keys=True)
        with self.path.open('a') as handle:
            handle.write(tail)
        damaged = self.path.read_text()
        outcomes = [self.restarted_call(f'after-{n}') for n in range(3)]
        # The kept tail is the eleventh call, so exactly one more launches; the thirteenth is refused.
        self.assertEqual(outcomes, [('ok', 1), ('not_run', 0), ('not_run', 0)])
        text = self.path.read_text()
        self.assertTrue(text.startswith(damaged + '\n'), 'the tail must be kept, not rewritten')
        self.assertTrue(text.endswith('\n'))
        records = [json.loads(line) for line in text[len(damaged) + 1:].splitlines()]
        self.assertEqual([r['event'] for r in records], ['tail_terminated', 'reserve', 'settle', 'refused', 'refused'])
        self.assertEqual((records[1]['cell'], records[1]['real_calls_after']), ('after-0', 12))
        self.assertEqual(records[2]['seq'], records[1]['seq'])
        state = packet_ab.CallLedger(self.path).state()
        self.assertEqual((state['real_calls'], state['refused'], state['tails_terminated']), (12, 2, 1))
        self.assertEqual(state['unsettled'], [21])
        self.assertEqual(self.path.read_text(), text, 'a terminated ledger is not repaired again')

    def assert_refused_everywhere_and_bytes_kept(self, damaged):
        """No in-process or restarted call may launch, count or write against a damaged ledger."""
        self.path.write_bytes(damaged)
        for _ in range(2):
            with self.assertRaises(packet_ab.LedgerRefused):
                packet_ab.CallLedger(self.path).state()
            with self.assertRaises(packet_ab.LedgerRefused):
                packet_ab.CallLedger(self.path).reserve('real', 'after', 'circuit', 'plan')
            with self.assertRaises(packet_ab.LedgerRefused):
                packet_ab.CallLedger(self.path).settle(21, {'execution': 'ok'})
        for n in range(2):
            result = subprocess.run([sys.executable, '-c', RESTARTED_CALL, str(self.path), f'r-{n}'], env=child_env(),
                                    capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('LedgerRefused', result.stderr)
            self.assertEqual(result.stdout, '', 'no provider may launch against a refused ledger')
        self.assertEqual(self.path.read_bytes(), damaged, 'ledger bytes are never rewritten or discarded')

    def test_partial_reservation_tail_refuses_the_ledger(self):
        """Ten settled real calls, then a crash part way through the eleventh reservation."""
        self.ledger_with_reservations(10, 10)
        clean = self.path.read_bytes()
        for terminated in (False, True):
            with self.subTest(terminated=terminated):
                tail = b'{"event": "reserve", "kind": "re' + (b'\n' if terminated else b'')
                self.assert_refused_everywhere_and_bytes_kept(clean + tail)

    def damaged_eleventh(self, old, new, terminated):
        self.ledger_with_reservations()
        prefix, last = self.path.read_bytes().rstrip(b'\n').rsplit(b'\n', 1)
        self.assertIn(old, last)
        return prefix + b'\n' + last.replace(old, new) + (b'\n' if terminated else b'')

    def test_invalid_utf8_in_complete_json_refuses_launch_in_every_field(self):
        for old, new in ((b'"kind": "real"', b'"kind": "rea\xff"'), (b'"event": "reserve"', b'"event": "reser\xff"'),
                         (b'"cell": "cell-10"', b'"cell": "cell-\xff0"'), (b'"plan_id": "plan"', b'"plan_id": "pl\xffn"')):
            for terminated in (True, False):
                with self.subTest(field=old, terminated=terminated):
                    self.tearDown()
                    self.setUp()
                    self.assert_refused_everywhere_and_bytes_kept(self.damaged_eleventh(old, new, terminated))

    def test_unknown_event_or_kind_in_valid_json_refuses_launch(self):
        """H1 of the ee12c review: one valid-UTF-8 bit flip in kind or event used to drop a reservation."""
        cases = ((b'"kind": "real"', b'"kind": "reaL"'), (b'"event": "reserve"', b'"event": "reserVe"'),
                 (b'"kind": ', b'"kinD": '), (b'"event": ', b'"evenT": '), (b'"seq": 21', b'"seq": 23'),
                 (b'"real_calls_after": 11', b'"real_calls_after": 10'), (b'"label": "circuit"', b'"label": 7'),
                 (b'"kind": "real"', b'"kind": "real", "note": "x"'), (b'"kind": "real", ', b''))
        for old, new in cases:
            for terminated in (True, False):
                with self.subTest(old=old, new=new, terminated=terminated):
                    self.tearDown()
                    self.setUp()
                    self.assert_refused_everywhere_and_bytes_kept(self.damaged_eleventh(old, new, terminated))

    def test_unknown_record_types_and_context_breaks_refuse(self):
        self.ledger_with_reservations(2, 2)
        base = self.path.read_bytes()
        now = packet_ab._now()
        records = ({'event': 'note', 'at_utc': now}, {'at_utc': now}, {'event': ['reserve']}, [1], 'x', None,
                   {'event': 'settle', 'seq': 1, 'at_utc': now, 'execution': 'ok'},
                   {'event': 'settle', 'seq': 9, 'at_utc': now, 'execution': 'ok'},
                   {'event': 'refused', 'kind': 'real', 'cell': 'c', 'label': 'l', 'plan_id': 'p', 'at_utc': now,
                    'real_calls': 2},
                   {'event': 'tail_terminated', 'line': 2, 'at_utc': now},
                   {'event': 'header', 'schema': packet_ab.LEDGER_SCHEMA, 'cap': 12, 'created_utc': now},
                   {'event': 'settle', 'seq': 1, 'at_utc': 'yesterday', 'execution': 'ok'})
        for record in records:
            with self.subTest(record=record):
                self.assert_refused_everywhere_and_bytes_kept(base + json.dumps(record, sort_keys=True).encode() + b'\n')
        for line in (b'', b' ', json.dumps({'event': 'note'}).encode() + b'\r', b'[' * 100000):
            with self.subTest(line=line):
                self.assert_refused_everywhere_and_bytes_kept(base + line + b'\n')
        noncanonical = base.rstrip(b'\n').rsplit(b'\n', 1)
        self.assert_refused_everywhere_and_bytes_kept(noncanonical[0] + b'\n' + noncanonical[1].replace(b', ', b',  ', 1)
                                                      + b'\n')

    def test_single_byte_damage_to_a_reservation_never_exceeds_the_cap_across_restarts(self):
        """Property sweep: every bit flip and a set of byte replacements at every byte position of a
        reservation line, as the unsettled tail (with and without its newline) and as an interior line
        followed by its settlement and later records; each damaged ledger then gets three calls, each in
        a fresh interpreter. Either the ledger is refused and kept byte for byte, or the damaged line is
        still a validated record charging the same call; at most twelve real calls ever exist."""
        root = Path(self.tmp.name)
        placements = {}
        self.ledger_with_reservations(11, 10)
        tail = self.path.read_bytes()
        prefix, last = tail.rstrip(b'\n').rsplit(b'\n', 1)
        placements['tail-terminated'] = (prefix + b'\n', last + b'\n', b'')
        placements['tail-unterminated'] = (prefix + b'\n', last, b'')
        self.path.unlink()
        self.ledger_with_reservations(11, 11)
        lines = self.path.read_bytes().split(b'\n')
        interior = next(i for i, line in enumerate(lines) if b'"cell": "cell-5"' in line)
        placements['interior'] = (b'\n'.join(lines[:interior]) + b'\n', lines[interior] + b'\n',
                                  b'\n'.join(lines[interior + 1:]))
        variants = []
        for name, (head, target, rest) in placements.items():
            self.assertEqual(json.loads(target)['event'], 'reserve')
            for position, byte in enumerate(target):
                values = {byte ^ (1 << bit) for bit in range(8)} | set(REPLACEMENTS)
                for value in sorted(values - {byte}):
                    damaged = head + target[:position] + bytes([value]) + target[position + 1:] + rest
                    path = root / 'sweep' / f'v{len(variants)}' / 'calls.jsonl'
                    path.parent.mkdir(parents=True)
                    path.write_bytes(damaged)
                    variants.append((name, position, value, path, damaged))
        self.assertGreater(len(variants), 3 * 200 * 8)
        listing = '\n'.join(str(v[3]) for v in variants)
        outcomes = {str(v[3]): [] for v in variants}
        for attempt in range(3):
            result = subprocess.run([sys.executable, '-c', SWEEP_CALL, str(attempt)], input=listing, env=child_env(),
                                    capture_output=True, text=True, timeout=900, check=True)
            for row in result.stdout.splitlines():
                path, outcome, launched = row.split()
                outcomes[path].append((outcome, int(launched)))
        refused = 0
        for name, position, value, path, damaged in variants:
            seen = outcomes[str(path)]
            where = f'{name} byte {position} -> {value:#04x}'
            self.assertEqual(len(seen), 3, where)
            self.assertLessEqual(11 + sum(n for _, n in seen), packet_ab.CAP, where)
            if seen == [('refused', 0)] * 3:
                refused += 1
                self.assertEqual(path.read_bytes(), damaged, where)
                with self.assertRaises(packet_ab.LedgerRefused, msg=where):
                    packet_ab.CallLedger(path).state()
                continue
            self.assertEqual(seen, [('ok', 1), ('not_run', 0), ('not_run', 0)], where)
            text = path.read_bytes()
            self.assertTrue(text.startswith(damaged), where)
            state = packet_ab.CallLedger(path).state()
            self.assertEqual((state['real_calls'], state['refused']), (12, 2), where)
            self.assertEqual(path.read_bytes(), text, where)
        # Most damage breaks the record; the survivors are string or timestamp content that charges the same.
        self.assertGreater(refused, len(variants) // 2)

    def test_single_bit_flip_anywhere_in_a_full_ledger_never_frees_budget(self):
        """Every bit of a ledger holding every record type: refused, or the same count and history."""
        packet_ab.CallLedger(self.path, cap=3).reserve('fake', 'f', 'circuit', 'plan')
        for n in range(3):
            seq = packet_ab.CallLedger(self.path, cap=3).reserve('real', f'cell-{n}', 'circuit', 'plan')
            packet_ab.CallLedger(self.path, cap=3).settle(seq, {'execution': 'raised', 'error': 'OSError'})
        with self.assertRaises(packet_ab.CallCapReached):
            packet_ab.CallLedger(self.path, cap=3).reserve('real', 'x', 'circuit', 'plan')
        with self.path.open('ab') as handle:
            handle.write(json.dumps({'event': 'settle', 'seq': 1, 'at_utc': packet_ab._now(), 'execution': None,
                                     'duration_s': None}, sort_keys=True).encode())
        expected = packet_ab.CallLedger(self.path, cap=3).state()
        self.assertEqual((expected['tails_terminated'], expected['refused'], expected['real_calls']), (1, 1, 3))
        clean = self.path.read_bytes()
        survived = 0
        for position, byte in enumerate(clean):
            for bit in range(8):
                damaged = clean[:position] + bytes([byte ^ (1 << bit)]) + clean[position + 1:]
                self.path.write_bytes(damaged)
                try:
                    state = packet_ab.CallLedger(self.path, cap=3).state()
                except ValueError:
                    self.assertEqual(self.path.read_bytes(), damaged)
                    continue
                survived += 1
                self.assertEqual(state, expected, f'byte {position} bit {bit}')
        self.assertLess(survived, len(clean) * 8 // 2)

    def test_retained_live_ledger_validates_without_writing(self):
        retained = Path(__file__).resolve().parents[1] / 'evidence/packet-index-ab/call-ledger.jsonl'
        copy = Path(self.tmp.name) / 'retained.jsonl'
        copy.write_bytes(retained.read_bytes())
        state = packet_ab.CallLedger(copy).state()
        self.assertEqual((state['real_calls'], state['fake_calls'], state['refused'], state['unsettled']), (9, 8, 0, []))
        self.assertEqual(copy.read_bytes(), retained.read_bytes())

    def test_torn_header_is_refused_without_writing(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"schema": "agent-ee.call-led')
        for _ in range(2):
            with self.assertRaises(ValueError):
                packet_ab.CallLedger(self.path).reserve('real', 'a', 'circuit', 'plan')
        self.assertEqual(self.path.read_text(), '{"schema": "agent-ee.call-led')

    def test_settle_requires_an_unsettled_reservation(self):
        ledger = packet_ab.CallLedger(self.path)
        seq = ledger.reserve('real', 'a', 'circuit', 'plan')
        ledger.settle(seq, {'execution': 'ok'})
        for bad in (seq, 99, 0, '1'):
            with self.assertRaises(ValueError):
                ledger.settle(bad, {'execution': 'ok'})
        seq = ledger.reserve('real', 'b', 'circuit', 'plan')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            ledger.settle(seq, {'execution': 'ok', 'tokens': 5})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(ledger.state()['unsettled'], [seq])

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
