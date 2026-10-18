"""Recorded pod fabrication facts; unresolved mechanics must not become an envelope."""
import copy
import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent_ee.board_envelope import load, source_inventory, validate, validate_stackup
from agent_ee.datasheet_facts import load_facts
from agent_ee.fab_profile import compile_profile, evaluate_geometry
from agent_ee.scaffold import produced_rules
from agent_ee.storage import digest

POD = Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod'


class PodFabProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((POD / 'fab-profile.json').read_text())
        self.files, self.inputs = source_inventory(POD)

    def test_pod_profile_sources_verified(self):
        compiled = compile_profile(self.profile, self.files, self.inputs)
        self.assertEqual(self.profile['schema'], 'agent-ee.fab-profile/2')
        self.assertEqual(compiled['name'], 'jlc04161h-7628')
        self.assertEqual(len(compiled['constraints']), 8)
        self.assertEqual({c['execution'] for c in compiled['constraints']}, {'ready'})
        expected = {'min_trace_mm': .09, 'min_clearance_mm': .09,
                    'min_via_drill_mm': .15, 'min_via_pad_mm': .25,
                    'min_edge_clearance_mm': .2, 'min_hole_to_hole_mm': .45,
                    'max_layers': 4, 'max_board_mm': [663, 593]}
        self.assertEqual({c['expected']['key']: c['expected']['value']
                          for c in compiled['constraints']}, expected)
        manifest = json.loads((POD / 'source-capture.json').read_text())
        for source in manifest['recorded_sources'].values():
            self.assertEqual(digest(POD / source['path']), source['sha256'])
            self.assertEqual((POD / source['path']).stat().st_size, source['bytes'])
        self.assertEqual(set(compiled['immutable_inputs']),
                         {s['path'] for s in manifest['recorded_sources'].values()})

    def test_pod_profile_source_hash(self):
        for source in {c['source']['path'] for c in self.profile['limits']}:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'pod'
                shutil.copytree(POD, root)
                inventory, _ = source_inventory(root)
                path = root / source
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                for files in (inventory, source_inventory(root)[0]):
                    compiled = compile_profile(self.profile, files, root)
                    for record in compiled['constraints']:
                        affected = record['expected']['source']['path'] == source
                        self.assertEqual(record['execution'], 'unsupported' if affected else 'ready')
                        if affected:
                            self.assertIn('hash', record['reason'])

    def test_pod_profile_missing_or_uncited_limits_never_become_ready(self):
        for limit in self.profile['limits']:
            for field in ('source', 'quote', 'page', 'as_of'):
                with self.subTest(limit=limit['id'], field=field):
                    changed = copy.deepcopy(self.profile)
                    selected = next(c for c in changed['limits'] if c['id'] == limit['id'])
                    if field == 'source':
                        del selected['source']
                    elif field == 'page':
                        selected['source'][field] = 2
                    elif field == 'as_of':
                        selected['source'][field] = '2026-10-17'
                    else:
                        selected['source'][field] = 'uncaptured quotation'
                    compiled = compile_profile(changed, self.files, self.inputs)
                    refused = next(c for c in compiled['constraints'] if c['id'] == limit['id'])
                    self.assertEqual(refused['execution'], 'unsupported')

    def test_pod_profile_produces_all_six_scaffold_minima(self):
        compiled, requirement, rules, netclass = produced_rules(
            {'fab_profile': 'fab-profile.json'}, self.files, self.inputs)
        self.assertEqual(requirement, 'R-envelope-telemetry-pod-fab-profile')
        self.assertEqual(len(rules), 6)
        self.assertEqual(len(compiled['constraints']), 8)
        self.assertEqual({n['setting'].rsplit('.', 1)[1]: n['value'] for n in netclass},
                         {'clearance': .09, 'track_width': .09,
                          'via_diameter': .25, 'via_drill': .15})

    def test_four_layer_specialization_refuses_six_layers(self):
        compiled = compile_profile(self.profile, self.files, self.inputs)
        layers = next(c for c in compiled['constraints'] if c.get('geometry') == 'max_layers')
        for count, expected in ((4, 'satisfied'), (6, 'violation')):
            result = evaluate_geometry(layers, {'copper_layers': count,
                                               'copper_layer_names': [f'L{i}' for i in range(count)]})
            self.assertEqual(result['status'], expected)


class PodEnvelopeInputTests(unittest.TestCase):
    def setUp(self):
        self.record = load((POD / 'envelope-inputs.json').read_bytes())

    def test_pod_inputs_bind_revision_two_requirement_ids(self):
        source = self.record['spec_record']
        self.assertEqual(digest(POD / source['path']), source['sha256'])
        self.assertEqual(digest(POD / 'spec.md'), source['spec_sha256'])
        spec = json.loads((POD / source['path']).read_text())
        raw = (POD / 'spec.md').read_bytes()
        requirements = {r['id']: r for r in spec['requirements']}
        for rid, binding in self.record['requirements'].items():
            requirement = requirements[rid]
            self.assertEqual(binding['text_sha256'], requirement['text_sha256'])
            self.assertEqual(binding['locator'], requirement['locator'])
            locator = binding['locator']
            self.assertEqual(raw[locator['byte_start']:locator['byte_end']].decode(), requirement['text'])
        known = self.record['known']
        self.assertEqual(known['max_board_mm'], [80, 50])
        self.assertEqual(known['max_component_height_mm'], 12)
        self.assertEqual(known['mounting_hole_count'], 4)
        self.assertEqual(known['layer_count'], 4)
        self.assertEqual(known['fab_profile']['sha256'], digest(POD / 'fab-profile.json'))

    def test_captured_stackup_is_refused_without_inventing_dielectric_padding(self):
        known = self.record['known']
        layers = known['stackup_layers']
        total = sum(Decimal(str(layer['thickness_mm'])) for layer in layers)
        self.assertEqual(total, Decimal('1.5862'))
        with self.assertRaisesRegex(ValueError, 'sum to 1.5862 mm, not thickness_mm 1.6'):
            validate_stackup({'thickness_mm': known['nominal_thickness_mm'], 'layers': layers}, 4)

    def test_partial_inputs_cannot_masquerade_as_an_envelope(self):
        self.assertEqual(self.record['status'], 'blocked')
        self.assertIsNone(self.record['known']['antenna_module_frame']['board_transform'])
        self.assertIn('fixed_footprints', self.record['unresolved'])
        self.assertIn('mounting_holes', self.record['unresolved'])
        with self.assertRaises(ValueError):
            validate((POD / 'envelope-inputs.json').read_bytes())

    def test_antenna_facts_reference_real_pdf_page_and_module_frame(self):
        facts = load_facts(POD, 'antenna-facts.json')
        self.assertEqual({f['id']: f['value'] for f in facts},
                         {'antenna-width': '18', 'antenna-depth': '6'})
        source = next(r for r in json.loads((POD / 'source-capture.json').read_text())['raw_captures']
                      if r['file'] == 'esp32-module.pdf')
        for fact in facts:
            self.assertEqual(fact['source']['sha256'], source['sha256'])
            self.assertEqual(fact['source']['page'], 45)
            self.assertIn('Figure 11-1', fact['source']['locator'])
        frame = self.record['known']['antenna_module_frame']
        self.assertEqual(set(frame['fact_ids']), {f['id'] for f in facts})
        self.assertEqual(frame['polygon_mm'], [[0, 0], [18, 0], [18, 6], [0, 6]])


if __name__ == '__main__':
    unittest.main()
