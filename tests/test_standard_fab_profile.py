"""Dated JLCPCB via options through the existing source-bound native check."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import tests.test_fab_profile as base
from tests.test_fab_profile_conjunctive import via_board
from agent_ee.fab_profile import compile_profile
from agent_ee.storage import digest

PRESET = Path(__file__).resolve().parents[1] / 'fixtures/fab-profiles/jlcpcb-standard-2026-10-19'


def standard_profile(inputs):
    document = json.loads((PRESET / 'profile.json').read_text())
    shutil.copyfile(PRESET / 'jlcpcb-vias.txt', inputs / 'jlcpcb-vias.txt')
    return document


class StandardProfileContractTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inputs = Path(tmp.name)
        self.document = standard_profile(self.inputs)
        self.files = {p.name: digest(p) for p in self.inputs.iterdir()}

    def compile(self):
        return compile_profile(self.document, self.files, self.inputs)

    def test_dated_preset_compiles_captured_minima_and_both_surcharges(self):
        compiled = self.compile()
        self.assertEqual(compiled['name'], 'jlcpcb_standard_2026-10-19')
        self.assertEqual({c['execution'] for c in compiled['constraints']}, {'ready'})
        self.assertEqual(len(compiled['constraints']), 4)
        rule = next(c for c in compiled['constraints'] if c['id'] == 'small-via')
        self.assertEqual([(t['expected']['key'], t['expected']['value']) for t in rule['terms']],
                         [('min_via_drill_mm', .3), ('min_via_pad_mm', .45)])
        self.assertEqual(compiled['immutable_inputs'], ['jlcpcb-vias.txt'])
        capture = json.loads((PRESET / 'source-capture.json').read_text())
        self.assertFalse(capture['raw_bytes_committed'])
        for recorded in capture['recorded_sources'].values():
            self.assertEqual(digest(PRESET / recorded['path']), recorded['sha256'])
            self.assertEqual((PRESET / recorded['path']).stat().st_size, recorded['bytes'])

    def test_missing_changed_or_misquoted_capture_is_unsupported(self):
        original = copy.deepcopy(self.document)
        for mutation in ('missing', 'changed', 'misquoted', 'unsourced'):
            with self.subTest(mutation=mutation):
                self.document = copy.deepcopy(original)
                standard_profile(self.inputs)
                source = self.inputs / 'jlcpcb-vias.txt'
                if mutation == 'missing':
                    source.unlink()
                elif mutation == 'changed':
                    source.write_bytes(source.read_bytes() + b'\n')
                elif mutation == 'misquoted':
                    self.document['limits'][-1]['source']['quote'] = 'unrecorded policy'
                else:
                    del self.document['limits'][-1]['source']
                rule = next(c for c in self.compile()['constraints'] if c['id'] == 'small-via')
                self.assertEqual(rule['execution'], 'unsupported', rule)
                self.assertNotIn('terms', rule)


class NativeStandardProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def run_vias(self, assignments):
        saved = {}
        build = via_board(assignments, saved)

        def checked_board(path, **options):
            build(path, **options)
            probe = subprocess.run(['/usr/bin/python3', '-B', '-c',
                'import json, pcbnew, sys; b=pcbnew.LoadBoard(sys.argv[1]); '
                'print(json.dumps(sorted((f.GetReference(), p.GetNumber(), '
                '[b.GetLayerName(layer) for layer in p.GetLayerSet().Seq()]) '
                'for f in b.GetFootprints() for p in f.Pads())))',
                str(path)], capture_output=True, text=True, check=True, timeout=30)
            self.assertEqual(json.loads(probe.stdout),
                             [['J00', '1', ['F.Cu']], ['J01', '1', ['B.Cu']],
                              ['J10', '1', ['F.Cu']], ['J11', '1', ['B.Cu']]])

        result = base.NativeFabTest.run_profile(
            self, None, builder=checked_board, profile=standard_profile)
        rules = {c['id']: c for c in result['details']['constraints']}
        return result, rules, saved

    def test_025_drill_reports_only_vias_below_joint_diameter_bound(self):
        assignments = [('charged1', (.25, .4)), ('charged2', (.25, .4)),
                       ('charged020', (.2, .4)), ('diameter_at_bound', (.25, .45)),
                       ('drill_at_bound', (.3, .4)), ('large_diameter', (.25, .6))]
        result, rules, saved = self.run_vias(assignments)
        rule = rules['small-via']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(rule['status'], 'extra_charge', rule)
        self.assertEqual(rule['offender_ids'], sorted(saved[n] for n in
                         ('charged1', 'charged2', 'charged020')), rule)
        self.assertEqual(rule['minimum_violation_ids'], [])
        self.assertEqual(result['details']['extra_charges'], [rule])
        self.assertEqual(rules['small-drill']['status'], 'satisfied')

    def test_legal_vias_outside_joint_band_have_no_extra_charge(self):
        assignments = [('diameter_at_bound', (.25, .45)), ('diameter_above', (.25, .6)),
                       ('drill_at_bound', (.3, .4)), ('drill_above', (.35, .45)),
                       ('control1', (.3, .6)), ('control2', (.4, .7))]
        result, rules, _ = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual((rules['small-via']['status'], rules['small-via']['offender_ids']),
                         ('satisfied', []))
        self.assertEqual(result['details']['extra_charges'], [])

    def test_015_drill_at_any_diameter_keeps_its_separate_charge(self):
        assignments = [('small_drill', (.15, .6))] + [
            (f'control{i}', (.3, .6)) for i in range(5)]
        result, rules, saved = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        rule = rules['small-drill']
        self.assertEqual((rule['status'], rule['offender_ids']),
                         ('extra_charge', [saved['small_drill']]))
        self.assertEqual(result['details']['extra_charges'], [rule])
        self.assertEqual(rules['small-via']['status'], 'satisfied')

    def test_illegal_drill_stays_a_defect_and_cannot_be_a_surcharge(self):
        assignments = [('illegal', (.1, .35))] + [
            (f'control{i}', (.3, .6)) for i in range(5)]
        result, rules, saved = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['violations'], ['via-drill'])
        self.assertEqual(result['details']['extra_charges'], [])
        for ident in ('small-drill', 'small-via'):
            self.assertEqual(rules[ident]['minimum_violation_ids'], [saved['illegal']])


if __name__ == '__main__':
    unittest.main()
