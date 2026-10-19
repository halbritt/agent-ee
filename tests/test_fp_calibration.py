"""AGENTEE-70 reviewer false-positive calibration: proven variants, paired calls, retained report."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import fp_calibration as fp
from agent_ee.design_diff import compare
from agent_ee.schematic_emit import format_sexpr, parse_quoted
from agent_ee.storage import digest, read_json, write_json
from agent_ee.symbol_library import children
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / 'fixtures/rc/control'
SCH = 'rc_filter.kicad_sch'
EQUIVALENT = set(fp.EQUIVALENT_KINDS)


def profile():
    return {'schema': 'agent-ee.profile/1', 'name': 'calibration-test', 'tool_mode': 'investigate',
            'reviewers': [
                {'role': 'claude', 'harness': 'claude', 'model': 'scripted-claude', 'effort': 'high', 'timeout_s': 30},
                {'role': 'codex', 'harness': 'codex', 'model': 'scripted-codex', 'effort': 'high', 'timeout_s': 30},
                {'role': 'agy', 'harness': 'agy', 'model': 'scripted-agy-high', 'effort': 'high', 'timeout_s': 30}],
            # Profiles always declare one; calibration never launches it.
            'adjudicator': {'harness': 'claude', 'model': 'scripted-claude', 'effort': 'high', 'timeout_s': 30}}


def runtime():
    return {name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('claude', 'codex', 'codex_tool_host', 'agy', 'bwrap', '/usr/bin/python3', 'kicad-cli')}


def presented(run_dir):
    """Which variant/order a call shows, read from the retained path (the reviewer never sees it)."""
    parts = Path(run_dir).parts
    return parts[-3], parts[-2], int(parts[-1])


def verdict(prompt, value, differences=None):
    call_id = prompt.rsplit('call_id "', 1)[1].split('"', 1)[0]
    if differences is None:
        differences = [{'title': 'changed', 'location': 'B', 'reasoning': 'scripted'}] if value == 'different' else []
    return {'schema': fp.VERDICT_VERSION, 'call_id': call_id, 'verdict': value,
            'differences': differences, 'limitations': []}


class Transport:
    """Recorded-style fake harness: deterministic answers per reviewer; no inference."""

    def __init__(self, plan):
        self.expected = {v['id']: v['expected'] for v in plan['variants']}
        self.calls = []

    def __call__(self, run_dir, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        variant, order, repeat = presented(run_dir)
        inputs = Path(run_dir) / 'inputs'
        self.calls.append((label, variant, order, repeat, prompt,
                           sorted(p.relative_to(inputs).as_posix() for p in inputs.rglob('*') if p.is_file())))
        ok = {'execution': 'ok', 'backend': definition['harness']}
        truth = self.expected[variant]
        if label == 'claude':
            # A false positive only when the renumbered copy is shown second: order-dependent.
            if variant == 'reference_renumber' and order == 'forward':
                return ok, verdict(prompt, 'different')
            return ok, verdict(prompt, truth)
        if label == 'codex':
            if len([c for c in self.calls if c[0] == 'codex']) == 3:
                return {'execution': 'failed', 'error': 'scripted provider failure'}, None
            return ok, verdict(prompt, truth)
        # agy: misses the value change every time; one internally inconsistent record.
        if variant == 'net_rename' and order == 'reverse' and repeat == 0:
            return ok, verdict(prompt, 'different', differences=[])
        if variant == 'value_change':
            return ok, verdict(prompt, 'equivalent')
        return ok, verdict(prompt, truth)


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fp-calibration-')
        cls.root = Path(cls.tmp.name)
        cls.plan = fp.prepare(DESIGN, SCH, 7, cls.root / 'cal')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        runtime_patch = patch('agent_ee.cli.runtime_files', return_value=runtime())
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def fresh(self, name):
        target = self.root / name
        shutil.copytree(self.root / 'cal', target)
        return target

    def test_each_variant_is_proven_by_native_netlist_identity(self):
        variants = {v['id']: v for v in self.plan['variants']}
        self.assertEqual(set(variants), set(fp.TRANSFORMS))
        original = (self.root / 'cal/design/original' / SCH).read_bytes()
        for kind, entry in variants.items():
            self.assertEqual(entry['status'], 'included', entry)
            self.assertEqual(entry['expected'], 'equivalent' if kind in EQUIVALENT else 'different')
            self.assertTrue(entry['proof']['established'])
            self.assertEqual(entry['proof']['netlist_identity'], kind in EQUIVALENT)
            self.assertNotEqual((self.root / 'cal/variants' / kind / SCH).read_bytes(), original)
        # The renaming is what makes renumbered and renamed copies identical; the raw exports differ.
        raw = (self.root / 'cal/proof/original/netlist.xml').read_bytes()
        for kind in ('reference_renumber', 'net_rename'):
            self.assertFalse(compare(raw, (self.root / 'cal/proof' / kind / 'netlist.xml').read_bytes(),
                                     'netlist')['equivalent'])
        self.assertEqual(variants['symbol_relocation']['change']['added_wires'], 3)
        self.assertEqual(variants['value_change']['change']['from'], '100n')

    def test_preparation_is_seeded_and_never_overwrites(self):
        again = fp.prepare(DESIGN, SCH, 7, self.root / 'again')
        self.assertEqual(again['identity'], self.plan['identity'])
        other = fp.prepare(DESIGN, SCH, 8, self.root / 'other')
        self.assertNotEqual(other['identity'], self.plan['identity'])
        with self.assertRaisesRegex(fp.CalibrationError, 'never overwritten'):
            fp.prepare(DESIGN, SCH, 7, self.root / 'again')

    def test_unproven_variants_are_excluded(self):
        original = (self.root / 'cal/proof/original/netlist.xml').read_bytes()
        # A symbol moved without connecting wires is not equivalent and must not be presented.
        tree = parse_quoted((DESIGN / SCH).read_bytes())
        symbol = next(s for s in children(tree, 'symbol') if fp._reference(s) == 'R1')
        fp._shift(children(symbol, 'at')[0], (5080000, 0))
        project = self.root / 'moved'
        project.mkdir()
        (project / SCH).write_text('\n'.join(format_sexpr(tree)) + '\n')
        moved, record = fp.export_netlist(project, SCH, self.root / 'moved-proof')
        self.assertEqual(record['execution'], 'ok')
        proof = fp.prove(original, moved, 'symbol_relocation', {})
        self.assertFalse(proof['established'])
        self.assertIn('identity not established', proof['reason'])
        # A wrong declared renaming is not repaired.
        renamed = (self.root / 'cal/proof/reference_renumber/netlist.xml').read_bytes()
        self.assertFalse(fp.prove(original, renamed, 'reference_renumber', {'references': {'C1': 'C11'}})['established'])
        # A "different" seed that left the netlist unchanged is excluded, not counted as different.
        same = fp.prove(original, original, 'wire_removal', {})
        self.assertEqual((same['established'], same['reason']), (False, 'change left the netlist identical'))
        # A failed or unparseable export never proves anything.
        broken = self.root / 'broken'
        broken.mkdir()
        (broken / SCH).write_text('(kicad_sch (version 20260306)')
        data, record = fp.export_netlist(broken, SCH, self.root / 'broken-proof')
        self.assertIsNone(data)
        self.assertFalse(fp.prove(original, b'<export', 'net_rename', {'net_labels': {}})['established'])

    def test_not_applicable_and_hierarchical_designs(self):
        tree = parse_quoted((DESIGN / SCH).read_bytes())
        tree[:] = [n for n in tree if not (isinstance(n, list) and n and n[0] == 'label')]
        project = self.root / 'unlabelled'
        shutil.copytree(DESIGN, project)
        (project / SCH).write_text('\n'.join(format_sexpr(tree)) + '\n')
        plan = fp.prepare(project, SCH, 7, self.root / 'unlabelled-cal')
        status = {v['id']: (v['status'], v.get('reason')) for v in plan['variants']}
        for kind in ('net_rename', 'label_reassign'):
            self.assertEqual(status[kind], ('excluded', 'transform not applicable to this design'))
        self.assertEqual(status['reference_renumber'][0], 'included')
        tree.insert(-1, ['sheet'])
        (project / SCH).write_text('\n'.join(format_sexpr(tree)) + '\n')
        with self.assertRaisesRegex(fp.CalibrationError, 'hierarchical'):
            fp.prepare(project, SCH, 7, self.root / 'hierarchical-cal')
        (project / 'second.kicad_sch').write_text('(kicad_sch)')
        with self.assertRaisesRegex(fp.CalibrationError, 'flat single-schematic'):
            fp.prepare(project, SCH, 7, self.root / 'two-sheet-cal')

    def run_calibration(self, cal, transport, **kwargs):
        with patch('agent_ee.reviewers.dispatch', side_effect=transport):
            return fp.run(cal, profile(), kwargs.get('repeats', 2), kwargs.get('max_seconds', 600))

    def test_both_orders_repeats_failures_and_rates(self):
        cal = self.fresh('run')
        transport = Transport(self.plan)
        ledger = self.run_calibration(cal, transport)
        variants = len(self.plan['variants'])
        self.assertEqual(len(ledger['calls']), 3 * variants * 2 * 2)
        # Every launched call went through the preflight seam and saw only A and B.
        for label, variant, order, repeat, prompt, files in transport.calls:
            self.assertEqual(files, ['A/rc_filter.kicad_pro', 'A/rc_filter.kicad_sch',
                                     'B/rc_filter.kicad_pro', 'B/rc_filter.kicad_sch'])
            for word in (*fp.TRANSFORMS, 'forward', 'reverse', 'original', 'variant'):
                self.assertNotIn(word, prompt)
            path = cal / 'calls' / label / variant / order / str(repeat)
            self.assertEqual(read_json(path / 'review-work' / label / 'preflight/receipt.json')['result'], 'ok')
            first, second = ('A', 'B') if order == 'forward' else ('B', 'A')
            self.assertEqual((path / 'inputs' / first / SCH).read_bytes(),
                             (cal / 'design/original' / SCH).read_bytes())
            self.assertEqual((path / 'inputs' / second / SCH).read_bytes(),
                             (cal / 'variants' / variant / SCH).read_bytes())
        result = fp.report(cal)
        reviewers = {r['role']: r for r in result['reviewers']}

        claude = reviewers['claude']
        self.assertEqual((claude['calls'], claude['valid_calls'], claude['missing_calls']), (28, 28, 0))
        self.assertEqual((claude['false_positives'], claude['equivalent_calls']), (2, 16))
        self.assertEqual(claude['false_positive_rate'], 2 / 16)
        self.assertEqual(claude['false_negative_rate'], 0)
        self.assertEqual(claude['false_positives_by_kind']['reference_renumber'], 2)
        self.assertEqual(claude['order_dependent_pairs'], ['reference_renumber'])
        self.assertEqual(claude['order_sensitivity'], 1 / variants)
        pair = next(p for p in claude['pairs'] if p['variant'] == 'reference_renumber')
        self.assertEqual(pair['orders']['forward']['majority'], 'different')
        self.assertEqual(pair['orders']['reverse']['majority'], 'equivalent')
        self.assertEqual(pair['order_gap'], 1)
        self.assertEqual(pair['pooled']['majority'], 'split')
        self.assertIsNone(pair['pooled_error'])

        codex = reviewers['codex']
        # The third call failed: retained as missing, and no further codex call was launched.
        self.assertEqual(len([c for c in transport.calls if c[0] == 'codex']), 3)
        self.assertEqual((codex['valid_calls'], codex['missing_calls']), (2, 26))
        self.assertEqual(codex['missing_by_execution'], {'failed': 1, 'not_run': 25})
        self.assertEqual(codex['false_positives'], 0)
        # Its two valid calls were both orders of the first pair; every other pair is unknown, not consistent.
        self.assertEqual(codex['order_evaluable_pairs'], 1)
        self.assertEqual(codex['order_sensitivity'], 0)
        self.assertEqual({p['variant']: p['order_dependent'] for p in codex['pairs']},
                         {v['id']: (False if v['id'] == 'reference_renumber' else None) for v in self.plan['variants']})
        failed = next(e for e in ledger['calls'] if e['role'] == 'codex' and e['execution'] == 'failed')
        self.assertIsNone(failed['verdict'])
        self.assertTrue((cal / failed['path'] / 'execution.json').is_file())
        self.assertIsNone(read_json(cal / failed['path'] / 'response.json'))
        self.assertTrue(all(e['missing'] == 'harness stopped after call ' + failed['call_id']
                            for e in ledger['calls'] if e['role'] == 'codex' and e['execution'] == 'not_run'))

        agy = reviewers['agy']
        self.assertEqual(agy['missing_by_execution'], {'malformed': 1})
        self.assertEqual((agy['false_negatives'], agy['different_calls']), (4, 12))
        self.assertEqual(agy['false_negative_rate'], 4 / 12)
        self.assertEqual(agy['false_positives'], 0)
        self.assertEqual(agy['agreement_rate'], (27 - 4) / 27)
        rename = next(p for p in agy['pairs'] if p['variant'] == 'net_rename')
        self.assertEqual(rename['orders']['reverse'],
                         {'different': 0, 'equivalent': 1, 'missing': 1, 'different_fraction': 0.0,
                          'majority': 'equivalent'})
        self.assertEqual(agy['order_dependent_pairs'], [])

    def test_report_is_reproducible_without_model_calls_and_detects_tampering(self):
        cal = self.fresh('reproduce')
        self.run_calibration(cal, Transport(self.plan))
        refuse = AssertionError('report must not call a reviewer')
        with patch('agent_ee.reviewers.dispatch', side_effect=refuse), \
                patch('agent_ee.reviewers.reviewer_call', side_effect=refuse):
            first, second = fp.report(cal), fp.report(cal)
        self.assertEqual(first, second)
        self.assertEqual(fp.main(['report', str(cal), '--out', str(self.root / 'report.json')]), 0)
        self.assertEqual(read_json(self.root / 'report.json'), first)
        ledger = read_json(cal / 'calls.json')
        entry = next(e for e in ledger['calls'] if e['role'] == 'claude' and e['verdict'] == 'different')

        response = cal / entry['path'] / 'response.json'
        saved = response.read_bytes()
        value = json.loads(saved)
        write_json(response, {**value, 'verdict': 'equivalent', 'differences': []})
        with self.assertRaisesRegex(fp.CalibrationError, 'retained call record changed'):
            fp.report(cal)
        response.write_bytes(saved)

        forged = json.loads(json.dumps(ledger))
        target = next(e for e in forged['calls'] if e['call_id'] == entry['call_id'])
        target['verdict'] = 'equivalent'
        write_json(cal / 'calls.json', forged)
        with self.assertRaisesRegex(fp.CalibrationError, 'differs from the retained response'):
            fp.report(cal)
        target.update(verdict=None, execution='not_run')
        missing = next(e for e in forged['calls'] if e['execution'] == 'not_run' and e is not target)
        missing['verdict'] = 'equivalent'
        write_json(cal / 'calls.json', forged)
        with self.assertRaisesRegex(fp.CalibrationError, 'carries a result|differs'):
            fp.report(cal)
        write_json(cal / 'calls.json', {**ledger, 'calls': [e for e in ledger['calls'] if e is not entry]})
        with self.assertRaisesRegex(fp.CalibrationError, 'complete schedule'):
            fp.report(cal)
        write_json(cal / 'calls.json', ledger)

        swapped = cal / entry['path'] / 'inputs'
        (swapped / 'A').rename(swapped / 'C')
        (swapped / 'B').rename(swapped / 'A')
        (swapped / 'C').rename(swapped / 'B')
        with self.assertRaisesRegex(fp.CalibrationError, 'does not match its order'):
            fp.report(cal)

    def test_calls_are_never_replayed(self):
        cal = self.fresh('replay')
        self.run_calibration(cal, Transport(self.plan), repeats=1)
        transport = Transport(self.plan)
        with self.assertRaisesRegex(fp.CalibrationError, 'never replayed'):
            self.run_calibration(cal, transport, repeats=1)
        self.assertEqual(transport.calls, [])

    def test_interruption_and_deadline_leave_missing_calls(self):
        cal = self.fresh('interrupt')
        seen = []

        def interrupted(run_dir, label, definition, prompt, schema, timeout, **kwargs):
            seen.append(run_dir)
            if len(seen) == 2:
                raise KeyboardInterrupt
            return {'execution': 'ok'}, verdict(prompt, 'equivalent')
        with self.assertRaises(KeyboardInterrupt):
            self.run_calibration(cal, interrupted, repeats=1)
        ledger = read_json(cal / 'calls.json')
        self.assertEqual(len(ledger['calls']), 3 * len(self.plan['variants']) * 2)
        self.assertEqual([e['execution'] for e in ledger['calls'][:3]], ['ok', 'interrupted', 'not_run'])
        self.assertEqual(fp.report(cal)['reviewers'][0]['valid_calls'], 1)

        cal = self.fresh('deadline')
        transport = Transport(self.plan)
        self.run_calibration(cal, transport, repeats=1, max_seconds=0)
        self.assertEqual(transport.calls, [])
        result = fp.report(cal)
        for reviewer in result['reviewers']:
            self.assertEqual(reviewer['valid_calls'], 0)
            # Unknown is never pass: no call means no rate, not a zero false-positive rate.
            for key in ('false_positive_rate', 'false_negative_rate', 'agreement_rate', 'order_sensitivity'):
                self.assertIsNone(reviewer[key])

    def test_verdict_validation(self):
        good = verdict('call_id "abc"', 'different')
        self.assertEqual(fp.validate_verdict(good, 'abc'), 'different')
        for bad in ({**good, 'call_id': 'other'}, {**good, 'differences': []},
                    {**verdict('call_id "abc"', 'equivalent'), 'differences': good['differences']},
                    {**good, 'verdict': 'unsure'}, {**good, 'extra': 1},
                    {**good, 'differences': [{'title': ' ', 'location': 'x', 'reasoning': 'y'}]}):
            with self.assertRaises(ValueError):
                fp.validate_verdict(bad, 'abc')
        self.assertEqual(fp.VERDICT_SCHEMA['required'], ['schema', 'call_id', 'verdict', 'differences', 'limitations'])


if __name__ == '__main__':
    unittest.main()
