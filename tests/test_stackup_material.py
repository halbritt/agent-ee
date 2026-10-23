"""Authored material stackup regression, distinct from captured fabrication facts.

The existing JLCPCB layer thicknesses remain unchanged. Material values are
a clearly authored supplement, not a claim of vendor material qualification.
"""
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee.board_envelope import board_facts, round_trip, source_inventory, validate
from agent_ee.envelope_check import compare_saved, saved_structure
from agent_ee.scaffold import board
from toolchain import require_toolchain

POD = Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod'
MATERIALS = [('prepreg', '4.4'), ('core', '4.6'), ('prepreg', '4.4')]
AUTHORED = '''Authored stackup regression; not a fabricator specification.
Nominal board thickness: 1.6 mm; physical layer sum: 1.5862 mm.
Captured copper layers F.Cu/In1.Cu/In2.Cu/B.Cu: 0.035/0.0152/0.0152/0.035 mm.
Dielectric 1: prepreg, thickness 0.2104 mm, epsilon_r 4.4, loss tangent unknown.
Dielectric 2: core, thickness 1.065 mm, epsilon_r 4.6, loss tangent unknown.
Dielectric 3: prepreg, thickness 0.2104 mm, epsilon_r 4.4, loss tangent unknown.
Numeric-loss control: all three dielectrics loss tangent 0.018.
'''


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


class StackupMaterialTest(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('AGENT_EE_186_TEST_OUTPUT')
        if retained:
            self.root = Path(retained).resolve() / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-stackup-material-')
            self.addCleanup(self.tmp.cleanup)
            self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(POD, self.source)
        self.envelope = json.loads((self.source / 'envelope.json').read_text())
        self.profile = json.loads((self.source / 'fab-profile.json').read_text())
        self.envelope['obligations'] = []
        source_path = 'sources/authored-material-stackup.txt'
        source_raw = AUTHORED.encode()
        (self.source / source_path).write_bytes(source_raw)
        citation = {'path': source_path, 'sha256': hashlib.sha256(source_raw).hexdigest(),
                    'source': 'agent-ee authored material regression, not vendor authority',
                    'as_of': self.profile['as_of'], 'page': 1, 'quote': AUTHORED}
        stack = self.envelope['stackup']
        for index, layer in enumerate(stack['layers']):
            if layer['kind'] == 'dielectric':
                layer['source'] = copy.deepcopy(citation)
                kind, er = MATERIALS[index // 2]
                layer.update(dielectric_type=kind, epsilon_r=float(er), loss_tangent=None)
        self.profile_raw = encoded(self.profile)
        self.envelope['fab_profile']['sha256'] = hashlib.sha256(self.profile_raw).hexdigest()
        (self.source / 'fab-profile.json').write_bytes(self.profile_raw)
        (self.source / 'envelope.json').write_bytes(encoded(self.envelope))

    def record(self, envelope=None):
        return validate(encoded(self.envelope if envelope is None else envelope),
                        profile=self.profile_raw, captured=source_inventory(self.source))

    def native_save(self, payload, name):
        import pcbnew
        original = self.root / (name + '-input.kicad_pcb')
        saved = self.root / (name + '-saved.kicad_pcb')
        original.write_bytes(payload)
        loaded = pcbnew.LoadBoard(str(original))
        self.assertEqual(loaded.GetCopperLayerCount(), 4)
        self.assertEqual(loaded.GetDesignSettings().GetBoardThickness(), 1600000)
        pcbnew.SaveBoard(str(saved), loaded)
        reloaded = pcbnew.LoadBoard(str(saved))
        self.assertEqual(reloaded.GetCopperLayerCount(), 4)
        self.assertEqual(reloaded.GetDesignSettings().GetBoardThickness(), 1600000)
        self.assertEqual(original.read_bytes(), payload)
        return saved.read_bytes()

    def test_native_sourced_materials_save_reload_and_wrong_value_controls(self):
        require_toolchain(native=True)
        record = self.record()
        self.assertEqual(record['structural'], 'pass')
        self.assertEqual(record['outcome'], 'pass', record['unresolved'])
        declared = [l for l in record['stackup']['layers'] if l['kind'] == 'dielectric']
        self.assertEqual([(l['dielectric_type'], l['epsilon_r'], l['loss_tangent']) for l in declared],
                         [(kind, er, None) for kind, er in MATERIALS])
        generated = board(record, 'authored-stackup-material')
        # KiCad's observed serialization default is explicit, while the sourced
        # unknown stays null. The native number is not an impedance input.
        self.assertEqual(generated.count(b'(loss_tangent 0.02)'), 3)
        self.assertEqual([layer['loss_tangent'] for layer in declared], [None] * 3)
        self.assertEqual([layer['loss_tangent'] for layer in self.envelope['stackup']['layers']
                          if layer['kind'] == 'dielectric'], [None] * 3)
        saved = self.native_save(generated, 'correct')
        facts = board_facts(saved)
        layers = [l for l in facts['stackup'] if l['type'] in ('core', 'prepreg')]
        self.assertEqual([(l['type'], l['epsilon_r'], l['thickness_nm']) for l in layers],
                         [('prepreg', '4.4', 210400), ('core', '4.6', 1065000), ('prepreg', '4.4', 210400)])
        copper = [l for l in facts['stackup'] if l['type'] == 'copper']
        self.assertEqual(sum(l['thickness_nm'] for l in copper), 100400)
        self.assertEqual(sum(l['thickness_nm'] for l in copper + layers), 1586200)
        trip = round_trip(encoded(self.envelope), saved)
        fields = {row['field']: row for row in trip['fields']}
        for index in (1, 3, 5):
            for field in ('dielectric_type', 'epsilon_r'):
                self.assertEqual(fields[f'/stackup/layers/{index}/{field}']['status'], 'match')
            loss = fields[f'/stackup/layers/{index}/loss_tangent']
            self.assertEqual(loss['status'], 'not_compared')
            self.assertEqual({key: loss['detail'][key] for key in (
                'source_value', 'serialized_value', 'qualification', 'origin', 'used_for_impedance')}, {
                    'source_value': None, 'serialized_value': '0.02', 'qualification': 'unqualified',
                    'origin': 'KiCad serialization/tool default', 'used_for_impedance': False})
        (self.root / 'correct-round-trip.json').write_bytes(encoded(trip))
        judgment = compare_saved('stackup/1', record, saved_structure(saved))
        self.assertEqual((judgment['status'], judgment['defects']), ('unknown', []), judgment)
        self.assertIs(judgment['impedance_qualified'], False)
        # Native load/save controls use the same accepted source; only saved values change.
        for name, before, after, field in (
                ('wrong-type', b'(type "prepreg")', b'(type "core")', 'dielectric_type'),
                ('wrong-er', b'(epsilon_r 4.4)', b'(epsilon_r 4.1)', 'epsilon_r')):
            with self.subTest(control=name):
                self.assertIn(before, generated)
                wrong = self.native_save(generated.replace(before, after, 1), name)
                comparison = round_trip(encoded(self.envelope), wrong)
                (self.root / (name + '-round-trip.json')).write_bytes(encoded(comparison))
                self.assertEqual(next(r for r in comparison['fields']
                    if r['field'] == f'/stackup/layers/1/{field}')['status'], 'mismatch')
                judgment = compare_saved('stackup/1', record, saved_structure(wrong))
                self.assertEqual(judgment['status'], 'defect', judgment)
                self.assertTrue(any(field in defect for defect in judgment['defects']), judgment)
                self.assertTrue(any('loss tangent is unknown' in item for item in judgment['unknown']), judgment)
                self.assertEqual(len(judgment['material_evidence']), 3)
                self.assertTrue(all(item['source_value'] is None
                                    and item['qualification'] == 'unqualified'
                                    and item['used_for_impedance'] is False
                                    for item in judgment['material_evidence']))
                self.assertIs(judgment['impedance_qualified'], False)

        known = copy.deepcopy(self.envelope)
        for layer in known['stackup']['layers']:
            if layer['kind'] == 'dielectric':
                layer['loss_tangent'] = 0.018
        known_record = self.record(known)
        known_generated = board(known_record, 'authored-known-loss-control')
        known_saved = self.native_save(known_generated, 'known-loss')
        known_layers = [l for l in board_facts(known_saved)['stackup'] if l['type'] in ('core', 'prepreg')]
        self.assertEqual([l['loss_tangent'] for l in known_layers], ['0.018'] * 3)
        self.assertEqual(compare_saved('stackup/1', known_record, saved_structure(known_saved))['status'], 'pass')
        wrong_loss = self.native_save(known_generated.replace(b'(loss_tangent 0.018)',
                                     b'(loss_tangent 0.02)', 1), 'wrong-loss')
        loss_trip = round_trip(encoded(known), wrong_loss)
        self.assertEqual(next(row for row in loss_trip['fields']
            if row['field'] == '/stackup/layers/1/loss_tangent')['status'], 'mismatch')
        judgment = compare_saved('stackup/1', known_record, saved_structure(wrong_loss))
        self.assertEqual(judgment['status'], 'defect', judgment)
        self.assertTrue(any('loss_tangent' in defect for defect in judgment['defects']), judgment)
        (self.root / 'known-loss-round-trip.json').write_bytes(encoded(round_trip(encoded(known), known_saved)))
        (self.root / 'wrong-loss-round-trip.json').write_bytes(encoded(loss_trip))

    def test_material_declarations_require_dielectric_type(self):
        for declaration in ({'epsilon_r': 4.4}, {'loss_tangent': None},
                            {'loss_tangent': 0}, {'loss_tangent': 0.018},
                            {'epsilon_r': 4.4, 'loss_tangent': None}):
            with self.subTest(declaration=declaration):
                changed = copy.deepcopy(self.envelope)
                layer = changed['stackup']['layers'][1]
                for field in ('dielectric_type', 'epsilon_r', 'loss_tangent'):
                    layer.pop(field)
                layer.update(declaration)
                with self.assertRaisesRegex(ValueError, 'material declaration requires dielectric_type'):
                    self.record(changed)

    def test_native_zero_loss_is_known_and_compared(self):
        require_toolchain(native=True)
        declared = copy.deepcopy(self.envelope)
        for layer in declared['stackup']['layers']:
            if layer['kind'] == 'dielectric':
                layer['loss_tangent'] = 0
        record = self.record(declared)
        saved = self.native_save(board(record, 'explicit-zero-loss'), 'zero-loss')
        judgment = compare_saved('stackup/1', record, saved_structure(saved))
        self.assertEqual((judgment['status'], judgment['unknown'], judgment['material_evidence']),
                         ('pass', [], []), judgment)
        self.assertIs(judgment['impedance_qualified'], False)
        fields = {row['field']: row for row in round_trip(encoded(declared), saved)['fields']}
        for index in (1, 3, 5):
            loss = fields[f'/stackup/layers/{index}/loss_tangent']
            self.assertEqual(loss['status'], 'match')
            self.assertEqual(loss['detail'], {'declared': '0', 'board': '0'})

    def test_material_field_domains_and_copper_refusal(self):
        self.record()
        for key, values in {'dielectric_type': ['FR4', '', None],
                            'epsilon_r': [0, -1, True, '4.4', None],
                            'loss_tangent': [-1, True, '0.02']}.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    changed = copy.deepcopy(self.envelope)
                    changed['stackup']['layers'][1][key] = value
                    with self.assertRaises(ValueError):
                        self.record(changed)
        changed = copy.deepcopy(self.envelope)
        changed['stackup']['layers'][0]['epsilon_r'] = 4.4
        with self.assertRaises(ValueError):
            self.record(changed)
        legacy = json.loads((POD.parent / 'board-envelope/rc.envelope.json').read_text())
        legacy['stackup']['layers'][1]['dielectric_type'] = 'core'
        with self.assertRaises(ValueError):
            validate(encoded(legacy))
        for value in (0, 0.018):
            changed = copy.deepcopy(self.envelope)
            changed['stackup']['layers'][3]['loss_tangent'] = value
            self.assertEqual(Decimal(self.record(changed)['stackup']['layers'][3]['loss_tangent']), Decimal(str(value)))

    def test_material_source_hash_and_legacy_fields_remain_distinct(self):
        self.record()
        changed = copy.deepcopy(self.envelope)
        changed['stackup']['layers'][1]['source']['sha256'] = '0' * 64
        refused = self.record(changed)
        self.assertEqual(refused['outcome'], 'unknown')
        self.assertIn('stackup_layer_source_1', {row['predicate'] for row in refused['unresolved']})
        legacy_raw = (POD / 'envelope.json').read_bytes()
        legacy = validate(legacy_raw, profile=(POD / 'fab-profile.json').read_bytes(), captured=source_inventory(POD))
        self.assertEqual(legacy['stackup']['physical_thickness_mm'], '1.5862')
        self.assertTrue(all(not ({'dielectric_type', 'epsilon_r', 'loss_tangent'} & row.keys())
                            for row in legacy['stackup']['layers']))
        generated = board(legacy, 'legacy-stackup-unchanged')
        self.assertNotIn(b'epsilon_r', generated)
        self.assertNotIn(b'loss_tangent', generated)
        self.assertEqual([l['type'] for l in board_facts(generated)['stackup'] if l['type'] != 'copper'],
                         ['core', 'core', 'core'])
        judgment = compare_saved('stackup/1', legacy, saved_structure(generated))
        self.assertEqual((judgment['status'], judgment['defects'], judgment['unknown']), ('pass', [], []))
        self.assertNotIn('material_evidence', judgment)
        self.assertNotIn('impedance_qualified', judgment)


if __name__ == '__main__':
    unittest.main()
