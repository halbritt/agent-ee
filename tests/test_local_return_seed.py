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


if __name__ == '__main__':
    unittest.main()
