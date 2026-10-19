"""Recorded pod fabrication facts; unresolved mechanics must not become an envelope."""
import copy
import hashlib
import subprocess
import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from agent_ee.board_envelope import baseline_envelope, derive_requirements, load, obligation_coverage, round_trip, source_inventory, validate, validate_stackup
from agent_ee.datasheet_facts import load_facts
from agent_ee.fab_profile import capability, compile_profile, evaluate_geometry
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
        self.assertEqual(compiled['finished_thickness_tolerance']['execution'], 'ready')
        self.assertEqual(compiled['finished_thickness_tolerance']['expected']['percent'], 10)
        self.assertEqual(set(compiled['immutable_inputs']),
                         {s['path'] for s in manifest['recorded_sources'].values()})

    def test_profile_stackup_authority_is_sourced_and_closed(self):
        compiled = compile_profile(self.profile, self.files, self.inputs)
        authority = compiled['stackup']
        self.assertEqual(authority['execution'], 'ready')
        self.assertEqual(authority['expected']['name'], 'jlc04161h-7628')
        self.assertEqual(authority['expected']['layer_thickness_tolerance_mm'], '0')
        self.assertEqual([l['copper_weight_oz'] for l in authority['expected']['layers']
                          if l['kind'] == 'copper'], ['1', '0.5', '0.5', '1'])
        for mutation in ('schema', 'missing_tolerance', 'negative_tolerance', 'sub_nm',
                         'unknown', 'layers', 'weight', 'sum'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.profile)
                stack = changed['stackup']
                if mutation == 'schema':
                    changed['schema'] = 'agent-ee.fab-profile/1'
                elif mutation == 'missing_tolerance':
                    del stack['layer_thickness_tolerance_mm']
                elif mutation == 'negative_tolerance':
                    stack['layer_thickness_tolerance_mm'] = -0.001
                elif mutation == 'sub_nm':
                    stack['layer_thickness_tolerance_mm'] = 0.0000001
                elif mutation == 'unknown':
                    stack['accept'] = True
                elif mutation == 'layers':
                    stack['layer_count'] = 2
                elif mutation == 'weight':
                    stack['layers'][0]['copper_weight_oz'] = True
                else:
                    stack['thickness_mm'] = 1.6
                with self.assertRaises(ValueError):
                    compile_profile(changed, self.files, self.inputs)

    def test_pod_profile_source_hash(self):
        for source in ({c['source']['path'] for c in self.profile['limits']} |
                       {self.profile['finished_thickness_tolerance']['source']['path']}):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'pod'
                shutil.copytree(POD, root)
                inventory, _ = source_inventory(root)
                path = root / source
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                for files in (inventory, source_inventory(root)[0]):
                    compiled = compile_profile(self.profile, files, root)
                    tolerance = compiled['finished_thickness_tolerance']
                    affected = tolerance['expected']['source']['path'] == source
                    self.assertEqual(tolerance['execution'], 'unsupported' if affected else 'ready')
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

    def test_tolerance_descriptor_refuses_invalid_numbers_and_version_one(self):
        for key, value in (('percent', True), ('percent', 0), ('percent', 100),
                           ('percent', float('inf')), ('percent', '10'),
                           ('min_nominal_thickness_mm', 0), ('min_nominal_thickness_mm', False)):
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.profile)
                changed['finished_thickness_tolerance'][key] = value
                with self.assertRaises(ValueError):
                    compile_profile(changed, self.files, self.inputs)
        changed = copy.deepcopy(self.profile)
        changed['schema'] = 'agent-ee.fab-profile/1'
        with self.assertRaises(ValueError):
            compile_profile(changed, self.files, self.inputs)

    def test_native_descriptor_requires_tolerance_source_without_claiming_thickness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'pod'
            shutil.copytree(POD, root)
            shutil.copyfile(POD.parents[1] / 'fixtures/fab-export/fab4.kicad_pcb', root / 'board.kicad_pcb')
            scope = {'board': 'board.kicad_pcb', 'baseline': {'requirements': [{
                'id': 'R-envelope-telemetry-pod-fab-profile', 'checks': ['fab'],
                'source': {'path': 'fab-profile.json', 'locator': '/limits'}}]}}
            inventory, _ = source_inventory(root)
            good = capability({'fab_profile': 'fab-profile.json'}, scope, inventory, root, 'fab')
            self.assertEqual(len(good['predicates']), 8)
            self.assertIn('sources/jlcpcb-thickness.txt', good['immutable_inputs'])
            path = root / 'sources/jlcpcb-thickness.txt'
            original = path.read_bytes()
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            for files in (inventory, source_inventory(root)[0]):
                with self.assertRaisesRegex(ValueError, 'tolerance source not verified'):
                    capability({'fab_profile': 'fab-profile.json'}, scope, files, root, 'fab')


class PodEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.envelope = json.loads((POD / 'envelope.json').read_text())
        self.profile = (POD / 'fab-profile.json').read_bytes()
        self.capture = source_inventory(POD)

    def validate(self, envelope=None, profile=None):
        return validate(json.dumps(envelope or self.envelope).encode(),
                        profile=profile or self.profile, captured=self.capture)

    def test_pod_envelope_accepts_honest_stackup_with_unbound_mechanics(self):
        record = self.validate()
        self.assertEqual(record['structural'], 'pass')
        self.assertEqual(record['outcome'], 'unknown')
        self.assertFalse(record['acceptance_ready'])
        self.assertEqual(record['stackup']['thickness_mm'], '1.6')
        self.assertEqual(record['stackup']['physical_thickness_mm'], '1.5862')
        self.assertEqual(record['stackup']['finished_thickness_tolerance']['percent'], 10)
        self.assertEqual({x['predicate'] for x in record['unresolved']},
                         {'mechanical_' + i['id'] for i in self.envelope['obligations']})
        self.assertEqual(record['mounting_holes'], [])
        self.assertEqual(record['keepouts'], [])
        self.assertEqual(record['fixed_footprints'], [])
        spec = json.loads((POD / 'spec-record.json').read_text())
        ids = {r['id'] for r in spec['requirements']}
        for item in record['obligations']:
            self.assertTrue(set(item['requirement_ids']) <= ids)
            self.assertEqual(item['owner_items'], ['AGENTEE-177', 'AGENTEE-179'])
        requirements = derive_requirements(record, 'envelope.json')
        self.assertFalse(requirements['complete'])
        obligations = [r for r in requirements['requirements'] if '/obligations/' in r['source']['locator']]
        self.assertEqual(len(obligations), 5)
        scope = {'schema': 'agent-ee.scope/3', 'checks': [{'id': 'mechanics', 'kind': 'board_envelope'}],
                 'baseline': {'requirements': [{**r, 'checks': ['mechanics']} for r in obligations]}}
        coverage = obligation_coverage(scope, requirements, record, {})
        self.assertEqual({r['status'] for r in coverage if '/obligations/' in r['locator']}, {'unknown'})

    def test_pod_outside_hole_and_keepout_are_refused(self):
        for field, item, message in (
                ('mounting_holes', {'id': 'outside', 'at': [81, 25], 'drill_mm': 3.2, 'plated': False},
                 'not inside the outline'),
                ('keepouts', {'id': 'outside', 'polygon': [[79, 1], [81, 1], [81, 7], [79, 7]],
                              'layers': ['F.Cu'], 'forbid': ['tracks']}, 'not contained in the outline')):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.envelope)
                changed[field] = [item]
                with self.assertRaisesRegex(ValueError, message):
                    self.validate(changed)

    def test_physical_sum_remains_exact_and_old_schema_is_not_reinterpreted(self):
        changed = copy.deepcopy(self.envelope)
        changed['stackup']['physical_thickness_mm'] = 1.6
        with self.assertRaisesRegex(ValueError, 'sum to 1.5862'):
            self.validate(changed)
        changed = copy.deepcopy(self.envelope)
        changed['schema'] = 'agent-ee.envelope/1'
        with self.assertRaises(ValueError):
            self.validate(changed)

    def test_tolerance_edges_and_applicability(self):
        # Nominal is a request; hold every published physical layer unchanged.
        for nominal, accepted in ((1.442, True), (1.441999, False),
                                  (1.762444, True), (1.762445, False), (.9, False)):
            with self.subTest(nominal=nominal):
                changed = copy.deepcopy(self.envelope)
                changed['stackup']['nominal_thickness_mm'] = nominal
                if accepted:
                    self.assertEqual(self.validate(changed)['structural'], 'pass')
                else:
                    with self.assertRaisesRegex(ValueError, 'captured.*tolerance|tolerance applicability'):
                        self.validate(changed)

    def test_missing_tolerance_and_layer_citations_never_pass(self):
        profile = json.loads(self.profile)
        for mutate in ('absent', 'source', 'quote', 'page', 'as_of', 'sha256'):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(profile)
                if mutate == 'absent':
                    del changed['finished_thickness_tolerance']
                elif mutate == 'source':
                    changed['finished_thickness_tolerance']['source'] = None
                else:
                    changed['finished_thickness_tolerance']['source'][mutate] = {
                        'quote': 'missing', 'page': 2, 'as_of': '2026-10-17', 'sha256': '0'*64}[mutate]
                raw = json.dumps(changed).encode()
                envelope = copy.deepcopy(self.envelope)
                envelope['fab_profile']['sha256'] = hashlib.sha256(raw).hexdigest()
                record = self.validate(envelope, raw)
                self.assertIn('finished_thickness_tolerance', {u['predicate'] for u in record['unresolved']})
                self.assertEqual(record['outcome'], 'unknown')
        for index in range(7):
            changed = copy.deepcopy(self.envelope)
            del changed['stackup']['layers'][index]['source']
            with self.assertRaises(ValueError):
                self.validate(changed)
            changed = copy.deepcopy(self.envelope)
            changed['stackup']['layers'][index]['source']['sha256'] = '0'*64
            record = self.validate(changed)
            self.assertIn(f'stackup_layer_source_{index}', {u['predicate'] for u in record['unresolved']})

    def test_scaffold_keeps_nominal_and_physical_layers(self):
        from agent_ee.scaffold import board
        from agent_ee.envelope_check import saved_structure, compare_saved
        record = self.validate()
        native = board(record, 'pod-envelope-test')
        facts = saved_structure(native)
        self.assertEqual(facts['stackup']['thickness_mm'], '1.6')
        self.assertEqual(sum(Decimal(l['thickness_mm']) for l in facts['stackup']['layers']), Decimal('1.5862'))
        comparison = compare_saved('stackup/1', record, facts)
        self.assertEqual(comparison['status'], 'pass')
        self.assertEqual(comparison['unknown'], [])
        self.assertEqual(comparison['defects'], [])
        trip = round_trip((POD / 'envelope.json').read_bytes(), native)
        fields = {f['field']: f['status'] for f in trip['fields']}
        self.assertEqual(fields['/stackup/nominal_thickness_mm'], 'match')
        self.assertEqual(fields['/obligations/0'], 'not_compared')
        self.assertEqual(trip['outcome'], 'unknown')

    def test_obligation_shape_cannot_claim_a_pass(self):
        for key, value in (('status', 'pass'), ('requirement_ids', []),
                           ('owner_items', ['AGENTEE-177', 'AGENTEE-177']), ('statement', '')):
            changed = copy.deepcopy(self.envelope)
            changed['obligations'][0][key] = value
            with self.assertRaises(ValueError):
                self.validate(changed)

    def test_version_two_consumption_requires_baseline_declaration(self):
        scope = {'schema': 'agent-ee.scope/3', 'board': 'board.kicad_pcb',
                 'requirements': ['envelope.json'], 'checks': [], 'baseline': {'requirements': []}}
        with self.assertRaisesRegex(ValueError, 'without a scope envelope declaration'):
            baseline_envelope(scope, *self.capture)

    def test_pod_scaffold_native_roundtrip_preserves_both_thicknesses(self):
        from agent_ee.scaffold import board
        from agent_ee.envelope_check import saved_structure
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pod.kicad_pcb'
            path.write_bytes(board(self.validate(), 'pod-native-test'))
            run = subprocess.run(['/usr/bin/python3', '-c',
                                  'import pcbnew,sys; b=pcbnew.LoadBoard(sys.argv[1]); '
                                  'print(b.GetDesignSettings().GetBoardThickness()); '
                                  'pcbnew.SaveBoard(sys.argv[1],b)', str(path)],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), '1600000')
            facts = saved_structure(path.read_bytes())['stackup']
            self.assertEqual(facts['thickness_mm'], '1.6')
            self.assertEqual(sum(Decimal(l['thickness_mm']) for l in facts['layers']), Decimal('1.5862'))


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
        self.assertEqual(self.record['status'], 'superseded-by-envelope-2')
        self.assertIsNone(self.record['known']['antenna_module_frame']['board_transform'])
        self.assertIn('AGENTEE-177/179', self.record['unresolved']['mechanics'])
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
