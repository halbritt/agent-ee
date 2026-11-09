"""Tests for data-driven local routing seed, ground ties, and reservation contracts."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee import local_return_seed
from agent_ee.placement_job import JobRefusal


class LocalReturnSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_schema_constants(self):
        self.assertEqual(local_return_seed.SCHEMA, 'agent-ee.local-routing-seed/1')
        self.assertEqual(local_return_seed.LEGACY_SCHEMA, 'agent-ee.acquisition-local-routing/1')

    def test_applies_contract(self):
        # Empty/unsupported model
        self.assertFalse(local_return_seed.applies({}))
        self.assertFalse(local_return_seed.applies({'model': {}}))

        # Design-declared model without declared loops
        self.assertFalse(local_return_seed.applies({
            'custody': 'design-declared',
            'model': {'custody': 'design-declared'}
        }))

        # Design-declared model with declared ground ties
        self.assertTrue(local_return_seed.applies({
            'custody': 'design-declared',
            'model': {
                'custody': 'design-declared',
                'ground_ties': [{'point_mm': [10.0, 10.0]}]
            }
        }))

    def test_design_declared_loop_fields_refuse_by_name(self):
        for field in ('connections', 'local_loops', 'decoupling_loops'):
            for carrier in ('recipe', 'model'):
                with self.subTest(field=field, carrier=carrier):
                    recipe = {'model': {field: []}}
                    target = recipe['model'] if carrier == 'model' else recipe
                    target['custody'] = 'design-declared'
                    with self.assertRaisesRegex(JobRefusal, 'mechanics field ' + field):
                        local_return_seed.applies(recipe)
                    with self.assertRaisesRegex(JobRefusal, 'mechanics field ' + field):
                        local_return_seed.produce(self.tmp, self.tmp / 'output', expected_inputs={},
                            mechanics_binding={'recipe': recipe}, planes=b'', clock=None)
                    self.assertFalse((self.tmp / 'output').exists())

    def test_known_acquisition_revision_keeps_legacy_loop_selection(self):
        from agent_ee import source_mechanics
        recipe = {'model': {'connections': []}, 'declaration': {'sources': {
            'analysis/geometry-model.json': source_mechanics.MODEL}}}
        self.assertTrue(local_return_seed.applies(recipe))

    def test_capture_and_identity(self):
        # Missing required files raises JobRefusal
        with self.assertRaises(JobRefusal):
            local_return_seed.capture(self.tmp)

        # Create required files
        (self.tmp / 'board.kicad_pcb').write_text('(kicad_pcb)')
        (self.tmp / 'board.kicad_pro').write_text('{"board": {}}')

        captured = local_return_seed.capture(self.tmp)
        self.assertIn('board.kicad_pcb', captured)
        self.assertIn('board.kicad_pro', captured)
        self.assertIsNone(captured.get('board.kicad_dru'))

        ident = local_return_seed.identity(captured)
        self.assertEqual(ident['board.kicad_pcb'], hashlib.sha256(b'(kicad_pcb)').hexdigest())
        self.assertEqual(ident['board.kicad_pro'], hashlib.sha256(b'{"board": {}}').hexdigest())
        self.assertIsNone(ident['board.kicad_dru'])

        # Private alias compatibility
        self.assertEqual(local_return_seed._identity(captured), ident)

    def test_rectangles_from_strips(self):
        strips = {'strip1': [[0, 0], [10, 0]]}
        boxes = local_return_seed.rectangles(strips, clearance_mm=0.2)
        self.assertEqual(len(boxes), 1)
        # Margin is 100_000 + 200_000 = 300_000 nm.
        self.assertEqual(boxes[0], (-300_000, -300_000, 10_300_000, 300_000))

    def test_reservation_verification_empty(self):
        self.assertEqual(local_return_seed.verify_reservations('/nonexistent', {}), [])
        self.assertEqual(local_return_seed.verify_reservations('/nonexistent', {'areas': []}), [])

    def test_produce_and_verify_known_revision_without_source_drc_json(self):
        import json
        from unittest.mock import patch
        from agent_ee import placement_job as job, source_mechanics
        from agent_ee.storage import canonical

        source = self.tmp / 'candidate'
        source.mkdir()
        board_bytes = b'(kicad_pcb (version 20260206) (generator "test"))'
        pro_bytes = b'{"board": {"design_settings": {"rules": {"min_clearance": 0.2, "min_track_width": 0.2}}}}'
        (source / 'board.kicad_pcb').write_bytes(board_bytes)
        (source / 'board.kicad_pro').write_bytes(pro_bytes)
        (source / 'native-drc.json').write_bytes(b'{"violations": [], "unconnected_items": []}')

        raw_model = (Path(__file__).resolve().parents[1]
                     / 'fixtures/acquisition-qualification/geometry-model.json').read_text()
        sources = {name: '0' * 64 for name in source_mechanics.SOURCES}
        sources.update({'analysis/geometry-model.json': source_mechanics.MODEL,
                        'analysis/geometry_model.py': source_mechanics.CHECKER})
        recipe = {'declaration': {'schema': source_mechanics.SCHEMA, 'sources': sources},
                  'model': json.loads(raw_model), 'model_raw': raw_model}
        binding = {'schema': source_mechanics.BINDING, 'recipe': recipe}
        binding['binding_sha256'] = source_mechanics.identity(binding)
        planes = json.dumps({
            'schema': 'agent-ee.plane-plan/1',
            'zones': [{'net': 'GND', 'layer': 'B.Cu', 'outline': 'board',
                       'clearance_mm': 0.2, 'min_width_mm': 0.2,
                       'thermal': {'connection': 'thermal', 'gap_mm': 0.25, 'spoke_width_mm': 0.25},
                       'priority': 0, 'island_removal': 'always'}],
        }).encode()
        expected_inputs = local_return_seed.identity(local_return_seed.capture(source))
        empty_drc = {
            '$schema': 'https://schemas.kicad.org/drc.v1.json',
            'kicad_version': '10.0.8',
            'coordinate_units': 'mm',
            'included_severities': ['error', 'warning', 'exclusion'],
            'ignored_checks': [],
            'violations': [],
            'unconnected_items': [],
            'schematic_parity': [],
        }
        empty_drc_bytes = canonical(empty_drc)

        def fake_produce_loops(src, dst, *, expected_inputs, clock, cancel_dir=None, reference='acquisition-control'):
            accepted = Path(dst) / 'accepted'
            accepted.mkdir(parents=True)
            (accepted / 'board.kicad_pcb').write_bytes(board_bytes)
            (accepted / 'board.kicad_pro').write_bytes(pro_bytes)
            (accepted / 'drc.json').write_bytes(empty_drc_bytes)
            out_inputs = local_return_seed.identity(local_return_seed.capture(accepted))
            rec = {'schema': 'agent-ee.acquisition-local-loops/1', 'status': 'seeded',
                   'inputs': expected_inputs, 'output': str(accepted), 'output_inputs': out_inputs,
                   'reference': reference, 'record': str(Path(dst) / 'record.json')}
            (Path(dst) / 'record.json').write_text(json.dumps(rec))
            return rec

        def fake_verify_seed(bpath, rec, *, clock, cancel_dir=None):
            return {'status': 'pass', 'board_sha256': hashlib.sha256(Path(bpath).read_bytes()).hexdigest()}

        def fake_drc(bpath, out_dir, **kwargs):
            (Path(out_dir) / 'drc.json').write_bytes(empty_drc_bytes)
            return empty_drc, {'exit_code': 0, 'report_sha256': hashlib.sha256(empty_drc_bytes).hexdigest()}

        with (patch('agent_ee.acquisition.local_return.produce_loops', side_effect=fake_produce_loops),
              patch('agent_ee.acquisition.local_return.verify_seed', side_effect=fake_verify_seed),
              patch.object(local_return_seed, 'drc', side_effect=fake_drc),
              patch.object(local_return_seed, 'verify_reservations', return_value=[{'uuid': 'u1'}])):
            record = local_return_seed.produce(
                source, self.tmp / 'local-routing', expected_inputs=expected_inputs,
                mechanics_binding=binding, planes=planes, clock=job.Clock(30))
            self.assertEqual(record['schema'], local_return_seed.SCHEMA)
            self.assertEqual(record['status'], 'seeded')
            routed_board = Path(record['output']) / 'board.kicad_pcb'
            verified = local_return_seed.verify(routed_board, record, clock=job.Clock(30))
            self.assertEqual(verified['status'], 'pass')
            self.assertGreater(len(verified['owned_zone_uuids']), 0)

    def test_produce_and_verify_design_declared_strips_stages_baseline_drc(self):
        import json
        from unittest.mock import patch
        from agent_ee import placement_job as job, source_mechanics
        from agent_ee.storage import canonical

        source = self.tmp / 'declared-candidate'
        source.mkdir()
        board_bytes = b'(kicad_pcb (version 20260206) (generator "test"))'
        pro_bytes = b'{"board": {"design_settings": {"rules": {"min_clearance": 0.2, "min_track_width": 0.2}}}}'
        (source / 'board.kicad_pcb').write_bytes(board_bytes)
        (source / 'board.kicad_pro').write_bytes(pro_bytes)

        raw_model = json.dumps({
            'custody': 'design-declared',
            'access_owners': ['J1'],
            'packages': {'J1': {'size_mm': [5.9, 4.5, 6.0], 'offset_mm': [0.0, 0.0]}},
            'ground_strips': {'s1': [[0, 0], [10, 0]]},
        }, sort_keys=True)
        model_sha = hashlib.sha256(raw_model.encode()).hexdigest()
        recipe = {'custody': 'design-declared',
                  'declaration': {'schema': source_mechanics.SCHEMA,
                                  'sources': {'analysis/geometry-model.json': model_sha}},
                  'model': json.loads(raw_model), 'model_raw': raw_model}
        binding = {'schema': source_mechanics.BINDING, 'recipe': recipe}
        binding['binding_sha256'] = source_mechanics.identity(binding)
        planes = json.dumps({
            'schema': 'agent-ee.plane-plan/1',
            'zones': [{'net': 'GND', 'layer': 'B.Cu', 'outline': 'board',
                       'clearance_mm': 0.2, 'min_width_mm': 0.2,
                       'thermal': {'connection': 'thermal', 'gap_mm': 0.25, 'spoke_width_mm': 0.25},
                       'priority': 0, 'island_removal': 'always'}],
        }).encode()
        expected_inputs = local_return_seed.identity(local_return_seed.capture(source))
        empty_drc = {
            '$schema': 'https://schemas.kicad.org/drc.v1.json',
            'kicad_version': '10.0.8',
            'coordinate_units': 'mm',
            'included_severities': ['error', 'warning', 'exclusion'],
            'ignored_checks': [],
            'violations': [],
            'unconnected_items': [],
            'schematic_parity': [],
        }
        empty_drc_bytes = canonical(empty_drc)

        def fake_drc(bpath, out_dir, **kwargs):
            (Path(out_dir) / 'drc.json').write_bytes(empty_drc_bytes)
            return empty_drc, {'exit_code': 0, 'report_sha256': hashlib.sha256(empty_drc_bytes).hexdigest()}

        with (patch.object(local_return_seed, 'drc', side_effect=fake_drc),
              patch.object(local_return_seed, 'verify_reservations', return_value=[{'uuid': 'u1'}])):
            record = local_return_seed.produce(
                source, self.tmp / 'declared-routing', expected_inputs=expected_inputs,
                mechanics_binding=binding, planes=planes, clock=job.Clock(30))
            self.assertEqual(record['schema'], local_return_seed.SCHEMA)
            self.assertTrue((self.tmp / 'declared-routing/baseline/drc.json').is_file())
            routed_board = Path(record['output']) / 'board.kicad_pcb'
            verified = local_return_seed.verify(routed_board, record, clock=job.Clock(30))
            self.assertEqual(verified['status'], 'pass')
            self.assertGreater(len(verified['owned_zone_uuids']), 0)


if __name__ == '__main__':
    unittest.main()
