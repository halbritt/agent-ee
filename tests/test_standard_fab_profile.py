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
from agent_ee.fab_profile import classify_preferred, compile_profile
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

    def test_options_are_explicit_v2_minimum_drill_metadata(self):
        self.assertEqual(self.compile()['constraints'][-1]['expected']['drill_options'],
                         {'values_mm': [.15, .2, .25], 'continuous_from_mm': .3})
        self.document['schema'] = 'agent-ee.fab-profile/1'
        with self.assertRaisesRegex(ValueError, 'unknown fabricator limit fields'):
            self.compile()

    def test_invalid_options_are_refused(self):
        original = copy.deepcopy(self.document)
        for options in (None, {}, {'values_mm': [], 'continuous_from_mm': .3},
                        {'values_mm': [.2, .2], 'continuous_from_mm': .3},
                        {'values_mm': [.3], 'continuous_from_mm': .3},
                        {'values_mm': [.1], 'continuous_from_mm': .3},
                        {'values_mm': [.2000001], 'continuous_from_mm': .3},
                        {'values_mm': [True], 'continuous_from_mm': .3},
                        {'values_mm': [10**400], 'continuous_from_mm': .3},
                        {'values_mm': [float('nan')], 'continuous_from_mm': .3},
                        {'values_mm': [.2], 'continuous_from_mm': 0}):
            self.document = copy.deepcopy(original)
            self.document['limits'][0]['drill_options'] = options
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.compile()
        for key, value in (('key', 'min_trace_mm'), ('tier', 'preferred')):
            self.document = copy.deepcopy(original)
            self.document['limits'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'requires minimum'):
                self.compile()

    def test_missing_inventory_is_unknown_and_empty_inventory_is_evaluated(self):
        record = self.compile()['constraints'][-1]
        record.update(execution='ok', status='satisfied')
        missing = classify_preferred([copy.deepcopy(record)], {'outline_xy_mm': [10, 10]})[0]
        self.assertEqual((missing['execution'], missing['status']), ('unsupported', 'not_evaluated'))
        self.assertIn('inventory missing', missing['reason'])
        empty = classify_preferred([record], {'outline_xy_mm': [10, 10], 'vias': []})[0]
        self.assertEqual((empty['execution'], empty['status']), ('ok', 'satisfied'))

    def test_off_table_refusal_is_typed_with_size_and_captured_table(self):
        record = next(c for c in self.compile()['constraints'] if c['id'] == 'via-drill')
        table = {'values_mm': [.15, .2, .25], 'continuous_from_mm': .3,
                 'source_path': 'jlcpcb-vias.txt', 'source_sha256': self.files['jlcpcb-vias.txt'],
                 'as_of': '2026-10-19'}
        for name, inventory, code, vias in (
                ('off-table', {'vias': [{'uuid': 'b', 'drill_nm': 180000},
                                        {'uuid': 'c', 'drill_nm': 250000}]},
                 'via_drill_off_table', [{'uuid': 'b', 'drill_nm': 180000}]),
                ('missing', {}, 'via_inventory_missing', None)):
            with self.subTest(case=name):
                trial = {**copy.deepcopy(record), 'execution': 'ok', 'status': 'satisfied'}
                result = classify_preferred([trial], {'outline_xy_mm': [10, 10], **inventory})[0]
                self.assertEqual((result['execution'], result['status'], result['code']),
                                 ('unsupported', 'not_evaluated', code))
                self.assertEqual(result['unsupported_vias'], vias)
                self.assertEqual(result['drill_table'], table)
        for drill in (150000, 200000, 250000, 300000, 2000000):
            with self.subTest(drill=drill):
                trial = {**copy.deepcopy(record), 'execution': 'ok', 'status': 'satisfied'}
                result = classify_preferred([trial], {'outline_xy_mm': [10, 10],
                                                      'vias': [{'uuid': 'a', 'drill_nm': drill}]})[0]
                self.assertEqual((result['execution'], result['status']), ('ok', 'satisfied'))
                self.assertFalse({'code', 'drill_table', 'unsupported_vias'} & set(result))

    def test_below_minimum_drill_needs_native_citation_and_unlisted_drill_takes_precedence(self):
        record = next(c for c in self.compile()['constraints'] if c['id'] == 'via-drill')
        low, unlisted = {'uuid': 'a', 'drill_nm': 149999}, {'uuid': 'b', 'drill_nm': 180000}
        cited = [{'items': [{'uuid': 'a'}]}]
        cases = (('cited', 'violation', cited, [low], ('ok', 'violation')),
                 ('satisfied', 'satisfied', [], [low], ('unsupported', 'not_evaluated')),
                 ('uncited', 'violation', [{'items': [{'uuid': 'z'}]}], [low],
                  ('unsupported', 'not_evaluated')),
                 ('mixed', 'violation', cited, [low, unlisted], ('unsupported', 'not_evaluated')))
        for name, status, findings, vias, expected in cases:
            with self.subTest(case=name):
                trial = {**copy.deepcopy(record), 'execution': 'ok', 'status': status,
                         'native_findings': findings}
                result = classify_preferred([trial], {'outline_xy_mm': [10, 10], 'vias': vias})[0]
                self.assertEqual((result['execution'], result['status']), expected)
                self.assertEqual(result['below_minimum_vias'], [low])
                if name == 'mixed':
                    self.assertEqual(result['unsupported_vias'], [unlisted])
                elif expected[0] == 'unsupported':
                    self.assertIn('not cited by native DRC', result['reason'])
        at_minimum = {**copy.deepcopy(record), 'execution': 'ok', 'status': 'satisfied'}
        result = classify_preferred([at_minimum], {'outline_xy_mm': [10, 10],
                                                   'vias': [{'uuid': 'c', 'drill_nm': 150000}]})[0]
        self.assertEqual((result['execution'], result['status']), ('ok', 'satisfied'))
        self.assertNotIn('below_minimum_vias', result)

    def test_options_cannot_default_when_their_source_or_native_run_is_unproved(self):
        original = copy.deepcopy(self.document)
        for field, value in (('quote', 'unrecorded options'), ('sha256', '0' * 64),
                             ('as_of', '2026-01-01')):
            self.document = copy.deepcopy(original)
            self.document['limits'][0]['source'][field] = value
            with self.subTest(field=field):
                record = self.compile()['constraints'][-1]
                self.assertEqual(record['execution'], 'unsupported')
                self.assertNotIn('rules', record)
        self.document = original
        record = self.compile()['constraints'][-1]
        record.update(execution='failed', status='not_evaluated', reason='native DRC failed')
        result = classify_preferred([record], {'outline_xy_mm': [10, 10], 'vias': []})[0]
        self.assertEqual((result['execution'], result['status'], result['reason']),
                         ('failed', 'not_evaluated', 'native DRC failed'))


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

    def test_drill_below_minimum_is_a_defect_and_cannot_be_a_surcharge(self):
        assignments = [('illegal', (.1, .35))] + [
            (f'control{i}', (.3, .6)) for i in range(5)]
        result, rules, saved = self.run_vias(assignments)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['violations'], ['via-drill'])
        self.assertEqual(result['details']['extra_charges'], [])
        gate = rules['via-drill']
        self.assertEqual((gate['execution'], gate['status']), ('ok', 'violation'))
        self.assertEqual(gate['below_minimum_vias'], [{'uuid': saved['illegal'], 'drill_nm': 100000}])
        self.assertNotIn('unsupported_vias', gate)
        for ident in ('small-drill', 'small-via'):
            self.assertNotEqual(rules[ident]['status'], 'extra_charge')
            self.assertEqual(rules[ident]['offender_ids'], [])
            self.assertEqual(rules[ident]['minimum_violation_ids'], [saved['illegal']])

    def test_minimum_boundary_is_exact_saved_nanometres(self):
        result, rules, saved = self.run_vias([
            ('just_below', (.149999, .6)), ('at_minimum', (.15, .6)),
            *[(f'control{i}', (.3, .6)) for i in range(4)]])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['violations'], ['via-drill'])
        self.assertEqual(rules['via-drill']['below_minimum_vias'],
                         [{'uuid': saved['just_below'], 'drill_nm': 149999}])
        self.assertEqual(rules['small-drill']['minimum_violation_ids'], [saved['just_below']])
        self.assertEqual(rules['small-drill']['offender_ids'], [saved['at_minimum']])
        self.assertEqual(result['details']['extra_charges'], [rules['small-drill']])

    def test_below_minimum_with_unlisted_drill_is_unknown_keeping_the_defect_visible(self):
        result, rules, saved = self.run_vias([
            ('illegal', (.1, .35)), ('unlisted', (.22, .4)),
            *[(f'control{i}', (.3, .6)) for i in range(4)]])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual(result['details']['extra_charges'], [])
        gate = rules['via-drill']
        self.assertEqual((gate['execution'], gate['status']), ('unsupported', 'not_evaluated'))
        self.assertEqual(gate['unsupported_vias'], [{'uuid': saved['unlisted'], 'drill_nm': 220000}])
        self.assertEqual(gate['below_minimum_vias'], [{'uuid': saved['illegal'], 'drill_nm': 100000}])
        self.assertIn(saved['illegal'], {i['uuid'] for f in gate['native_findings'] for i in f['items']})
        for ident in ('small-drill', 'small-via'):
            self.assertEqual((rules[ident]['execution'], rules[ident]['status']),
                             ('unsupported', 'not_evaluated'))

    def test_unlisted_018_022_and_028_drills_are_unknown_at_both_diameters(self):
        for drill in (.18, .22, .28):
            with self.subTest(drill=drill):
                result, rules, saved = self.run_vias([
                    ('small', (drill, .4)), ('large', (drill, .6)),
                    *[(f'control{i}', (.3, .6)) for i in range(4)]])
                self.assertEqual((result['execution'], result['outcome']),
                                 ('unsupported', 'unknown'), result)
                self.assertEqual(result['details']['extra_charges'], [])
                gate = rules['via-drill']
                self.assertEqual(gate['status'], 'not_evaluated')
                self.assertEqual(gate['unsupported_vias'], sorted(
                    [{'uuid': saved[n], 'drill_nm': round(drill * 1e6)} for n in ('small', 'large')],
                    key=lambda v: v['uuid']))
                for ident in ('small-drill', 'small-via'):
                    self.assertEqual((rules[ident]['execution'], rules[ident]['status']),
                                     ('unsupported', 'not_evaluated'))

    def test_off_table_drills_refuse_as_typed_unknown_naming_the_captured_table(self):
        result, rules, saved = self.run_vias([
            ('d018', (.18, .4)), ('d022', (.22, .6)), ('d028', (.28, .4)),
            ('charged', (.25, .4)), *[(f'control{i}', (.3, .6)) for i in range(2)]])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual(result['details']['extra_charges'], [])
        gate = rules['via-drill']
        self.assertEqual((gate['execution'], gate['status'], gate['code']),
                         ('unsupported', 'not_evaluated', 'via_drill_off_table'))
        self.assertEqual(gate['unsupported_vias'], sorted(
            [{'uuid': saved[n], 'drill_nm': d} for n, d in
             (('d018', 180000), ('d022', 220000), ('d028', 280000))], key=lambda v: v['uuid']))
        self.assertEqual(gate['drill_table'], {
            'values_mm': [.15, .2, .25], 'continuous_from_mm': .3, 'source_path': 'jlcpcb-vias.txt',
            'source_sha256': digest(PRESET / 'jlcpcb-vias.txt'), 'as_of': '2026-10-19'})
        # The on-table 0.25/0.4 mm via is never graded beside an off-table drill.
        for ident in ('small-drill', 'small-via'):
            self.assertNotEqual(rules[ident]['status'], 'extra_charge')

    def test_on_table_sizes_keep_their_grading_and_extra_charge_records(self):
        result, rules, saved = self.run_vias([
            ('d015', (.15, .6)), ('d020', (.2, .4)), ('d025', (.25, .4)),
            ('d025_large', (.25, .45)), ('d030', (.3, .4)), ('d040', (.4, .7))])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        gate = rules['via-drill']
        self.assertEqual((gate['execution'], gate['status']), ('ok', 'satisfied'))
        self.assertFalse({'code', 'drill_table', 'unsupported_vias', 'below_minimum_vias'} & set(gate))
        self.assertEqual((rules['small-drill']['status'], rules['small-drill']['offender_ids']),
                         ('extra_charge', [saved['d015']]))
        self.assertEqual((rules['small-via']['status'], rules['small-via']['offender_ids']),
                         ('extra_charge', sorted(saved[n] for n in ('d020', 'd025'))))
        self.assertEqual(result['details']['extra_charges'],
                         [rules['small-drill'], rules['small-via']])

    def test_tampered_source_hash_refuses_on_table_grading(self):
        def tampered(field):
            def profile(inputs):
                document = standard_profile(inputs)
                source = inputs / 'jlcpcb-vias.txt'
                if field == 'sha256':
                    document['limits'][0]['source']['sha256'] = '0' * 64
                else:
                    source.write_bytes(source.read_bytes() + b'0.22 mm\n')
                return document
            return profile
        for field in ('sha256', 'bytes'):
            with self.subTest(tampered=field):
                saved = {}
                result = base.NativeFabTest.run_profile(
                    self, None, builder=via_board(
                        [('charged', (.25, .4)), *[(f'control{i}', (.3, .6)) for i in range(5)]], saved),
                    profile=tampered(field))
                rules = {c['id']: c for c in result['details']['constraints']}
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertEqual(result['details']['extra_charges'], [])
                self.assertEqual(rules['via-drill']['execution'], 'unsupported')
                self.assertNotIn('rules', rules['via-drill'])
                self.assertNotIn('drill_table', rules['via-drill'])

    def test_options_match_exact_saved_nanometres_without_rounding_tolerance(self):
        result, rules, saved = self.run_vias([
            ('below', (.249999, .4)), ('above', (.250001, .4)),
            ('near_continuous', (.299999, .6)), ('exact', (.25, .4)),
            ('continuous', (.3, .6)), ('larger', (.35, .6))])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual([v['uuid'] for v in rules['via-drill']['unsupported_vias']],
                         sorted(saved[n] for n in ('below', 'above', 'near_continuous')))

    def test_supported_drill_with_illegal_diameter_keeps_its_defect(self):
        result, rules, saved = self.run_vias([('illegal_pad', (.2, .24)),
                                            *[(f'control{i}', (.3, .6)) for i in range(5)]])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['violations'], ['via-diameter'])
        self.assertEqual(result['details']['extra_charges'], [])
        self.assertEqual(rules['small-via']['minimum_violation_ids'], [saved['illegal_pad']])


if __name__ == '__main__':
    unittest.main()
