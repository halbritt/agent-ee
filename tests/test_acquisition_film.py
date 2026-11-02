"""Preserve source, placement and model-custody guards for the film derivation."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee.acquisition import film
from agent_ee.acquisition import mechanics as acquisition_mechanics
from agent_ee import easyeda_footprint, land_pattern, typed_overlay
from tests.land_pattern_fixtures import manifest

ROOT = Path(os.environ.get('AGENT_EE_FILM_RUN', '/nonexistent/film-run'))
BASE = Path(os.environ.get('AGENT_EE_FILM_BASE', '/nonexistent/film-base'))
MODEL = Path(os.environ.get('AGENT_EE_FILM_MODEL', '/nonexistent/film-model'))


@unittest.skipUnless((ROOT / 'selected' / film.SUPPLIER).is_file(), 'exact captured supplier footprint required')
class FootprintTests(unittest.TestCase):
    def test_generated_copper_matches_complete_supplier_and_missing_pad_is_rejected(self):
        raw = (ROOT / 'selected' / film.SUPPLIER).read_bytes()
        supplier = easyeda_footprint.parse_package(raw, film.LCSC, 200)
        name = film.LIBRARY + '.pretty/' + film.FOOTPRINT + '.kicad_mod'
        generated = film.footprint_bytes(raw)
        item = dict(registration=dict(translation='declared_origin', rotation='0', mirror=False), pin_aliases=None)
        criteria = dict(threshold='0.98', pin_tolerance_nm='200', refinement_levels=[2, 4, 8, 16])
        for missing in (False, True):
            with self.subTest(missing_pad=missing):
                data = generated if not missing else b'\n'.join(
                    line for line in generated.splitlines() if not line.startswith(b'(pad "2"')) + b'\n'
                captures = {name: data}
                native = land_pattern.library_footprint(manifest(captures, library=film.LIBRARY), captures,
                                                        film.LIBRARY + ':' + film.FOOTPRINT)
                result = land_pattern.compare(native, supplier, item, criteria)
                self.assertEqual(result['outcome'], 'defect' if missing else 'pass', result)
        with self.assertRaisesRegex(ValueError, 'exact captured'):
            film.footprint_bytes(raw + b'\n')


@unittest.skipUnless((ROOT / 'model-r1/execution.json').is_file() and MODEL.is_file(), 'captured film execution required')
class FilmPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.temporary.name) / 'bundle'
        cls.report = film.prepare(BASE, ROOT / 'selected', ROOT / 'model-r1', MODEL, cls.bundle)
        cls.inputs = cls.bundle / 'inputs'

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_only_capacitor_selection_changes_with_all_native_obligations_retained(self):
        for path in ('spec.md', 'spec-record.json', 'obligations.json', 'electrical-locality.json', 'placement.json', 'groups.json'):
            self.assertEqual((BASE / path).read_bytes(), (self.inputs / path).read_bytes(), path)
        before = json.loads((BASE / 'circuit.json').read_bytes())
        after = json.loads((self.inputs / 'circuit.json').read_bytes())
        self.assertEqual(before['nets'], after['nets'])
        self.assertEqual(before['no_connects'], after['no_connects'])
        before_parts = {p['id']: p for p in json.loads((BASE / 'parts.json').read_bytes())['parts']}
        parts = {p['id']: p for p in json.loads((self.inputs / 'parts.json').read_bytes())['parts']}
        self.assertEqual(set(parts), set(before_parts))
        for ref, part in parts.items():
            if ref in ('C1', 'C2'):
                self.assertEqual((part['mpn'], part['lcsc']), (film.MPN, film.LCSC))
            else:
                self.assertEqual(part, before_parts[ref])
        groups = json.loads((self.inputs / 'acquisition-block-source.json').read_bytes())
        self.assertEqual(len(groups['templates']), 18)
        self.assertEqual(sum(len(b['references']) for b in groups['allocation']['blocks']), 17)
        self.assertFalse(self.report['accepted'])
        self.assertEqual(self.report['file_release'], 'not_assessed')

    def test_mechanical_model_and_native_check_limits_keep_source_coverage(self):
        mechanics = acquisition_mechanics.load(self.inputs / 'placement-mechanics.json')
        self.assertEqual(mechanics['model']['packages']['C1']['size_mm'], [3.5, 2.8, 2.0])
        self.assertEqual(mechanics['model']['packages']['C2']['size_mm'], [3.5, 2.8, 2.0])
        case = json.loads((film.DATA / 'mechanics-test-case.json').read_bytes())
        self.assertEqual(case['sources'], mechanics['declaration']['sources'])
        before = typed_overlay._check_pool(json.loads((BASE / 'requirement-map.json').read_bytes()))
        after = typed_overlay._check_pool(json.loads((self.inputs / 'requirement-map.json').read_bytes()))
        self.assertEqual(set(after), set(before) - {'adp-power-retained-model'} | {'film-power-retained-model'})
        for key in set(before) - {'adp-power-retained-model', 'assembly', 'cpl', 'engineering-geometry'}:
            self.assertEqual(before[key], after[key], key)
        expected_geometry = json.loads(json.dumps(before['engineering-geometry']))
        expected_geometry['params']['tool']['inputs'] += ['analysis/geometry_contact.py','analysis/ground_reference.py']
        expected_geometry['params']['limits'].update({
            'ground_saved_net_changes':dict(max=0,unit='count',condition='nominal25C-reviewed-acquisition'),
            'ground_transition_missing':dict(max=0,unit='count',condition='nominal25C-reviewed-acquisition'),
            'ground_corridor_missing':dict(max=0,unit='count',condition='nominal25C-reviewed-acquisition'),
            'ground_plane_components':dict(min=1,max=1,unit='count',condition='nominal25C-reviewed-acquisition'),
            'ground_return_path':dict(max=3,unit='mm',condition='nominal25C-reviewed-acquisition'),
            'plated_ring_minimum':dict(min=.18,unit='mm',condition='nominal25C-reviewed-acquisition')})
        self.assertEqual(after['engineering-geometry'], expected_geometry)
        self.assertEqual(after['assembly']['params']['max_age_hours'], before['assembly']['params']['max_age_hours'])

    def test_new_ground_witness_keeps_endpoints_width_and_all_other_geometry(self):
        old = json.loads((film.DATA / 'mechanics-test-case-r1.json').read_text())
        before = json.loads(old['model_raw'])
        after = json.loads((self.inputs / 'analysis/geometry-model.json').read_text())
        for key in set(before) - {'ground_strips', 'limits'}:
            self.assertEqual(before[key], after[key], key)
        for name, points in after['ground_strips'].items():
            self.assertEqual((points[0], points[-1]), (before['ground_strips'][name][0], before['ground_strips'][name][-1]))
        self.assertEqual(before['ground_strips']['J1-to-J2-pin1'], after['ground_strips']['J1-to-J2-pin1'])
        acquisition_mechanics._version(dict(schema=acquisition_mechanics.SCHEMA, sources=old['sources']))
        for revision in ('r2','r3'):
            previous=json.loads((film.DATA/f'mechanics-test-case-{revision}.json').read_text())
            acquisition_mechanics._version(dict(schema=acquisition_mechanics.SCHEMA,sources=previous['sources']))
        current = json.loads((self.inputs / 'placement-mechanics.json').read_text())
        for name in ('analysis/geometry_model.py', 'analysis/geometry_contact.py', 'film-reference/geometry-path-basis.json',
                     'analysis/ground_reference.py','analysis/geometry_adapter.py','film-reference/ground-return-basis.md'):
            changed = json.loads(json.dumps(current))
            changed['sources'][name] = '0' * 64
            with self.subTest(source=name), self.assertRaisesRegex(ValueError, 'source custody'):
                acquisition_mechanics._version(changed)

    def test_model_binds_generated_circuit_and_refuses_changed_pre_execution_identity(self):
        files, _ = film.sources(BASE, ROOT / 'selected')
        film._power(files, ROOT / 'model-r1', MODEL)
        application = json.loads(files[film.ANALYSIS + 'power-application.json'])
        self.assertEqual(application['parts_sha256'], sha256(files['parts.json']).hexdigest())
        files, _ = film.sources(BASE, ROOT / 'selected')
        files['parts.json'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'exact generated'):
            film._power(files, ROOT / 'model-r1', MODEL)

    def test_local_routing_uses_the_regulator_pins_in_the_generated_circuit(self):
        # The first film build found exact-feasible placement but the caller
        # defaulted to the original TPS pin5, which is ADP's input-connected EN.
        from agent_ee.acquisition import local_routing as acquisition_local_routing, local_return as acquisition_local_return
        recipe = acquisition_mechanics.load(self.inputs / 'placement-mechanics.json')
        reference = acquisition_local_routing.power_reference(recipe)
        self.assertEqual(reference, film.REFERENCE)
        circuit = json.loads((self.inputs / 'circuit.json').read_bytes())
        nets = {(t['component'], t['pin']['number']): n['name'] for n in circuit['nets'] for t in n['terminals']}
        for endpoints, net in acquisition_local_return.connections(reference).values():
            for endpoint in endpoints:
                self.assertEqual(nets[endpoint].lstrip('/'), net.lstrip('/'), endpoint)


if __name__ == '__main__':
    unittest.main()
