"""Portable invariants for the finite R6 source exception, without native processes."""
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import acquisition_power_repair as repair, acquisition_mechanics as mechanics
from agent_ee.storage import json_bytes


class PowerRepairTests(unittest.TestCase):
    def test_nominal_budget_counts_two_pullups_and_independent_output_bleed(self):
        output, total, idle = repair.currents()
        # Worked example: two pullups17/5500 A + R6 17/3100 A + loads13/10000 A.
        # Independent exact expected values, in A, from the declared operating box.
        self.assertEqual(output, Fraction(33673, 3410000))
        self.assertEqual(total, Fraction(78179, 7033125))
        self.assertEqual(idle, Fraction(1617, 313100))
        self.assertLess(total, Fraction(20, 1000))
        self.assertGreater(idle, Fraction(1, 1000))

    def test_adverse_bleed_revision_preserves_capacitors_esr_and_input_bleed(self):
        # Authored CPU source, not a native result.
        corner = (b'.subckt COUT_EFFECTIVE P N\nR_ESR P E 0.05\nC_EFFECTIVE E N 12.65u\n.ends COUT_EFFECTIVE\n'
                  b'.subckt RBLEED_INPUT P N\nR_EFFECTIVE P N 21780\n.ends RBLEED_INPUT\n'
                  b'.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N 2222\n.ends RBLEED_OUTPUT\n')
        revised = repair._corner(corner)
        self.assertIn(b'C_EFFECTIVE E N 12.65u', revised)
        self.assertIn(b'R_ESR P E 0.05', revised)
        self.assertIn(b'R_EFFECTIVE P N 21780', revised)
        self.assertIn(b'R_EFFECTIVE P N 626.2', revised)
        with self.assertRaises(ValueError):
            repair._corner(revised)

    def test_no_capture_or_caller_approval_text_cannot_activate_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'power repair missing'):
                repair.read(tmp)
            (Path(tmp) / 'activation.txt').write_text('qualified:true')
            with self.assertRaisesRegex(ValueError, 'power repair missing'):
                repair.read(tmp)

    def test_third_geometry_version_requires_exact_murata_and_r6_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'placement-mechanics.json'
            sources = dict.fromkeys(mechanics.SOURCES, '0'*64)
            from agent_ee import acquisition_source_supplement as supplement
            sources.update(supplement.geometry_sources())
            sources.update(repair.geometry_sources())
            sources['analysis/geometry-model.json'] = repair.MODEL
            sources['analysis/geometry_model.py'] = mechanics.CHECKER
            for missing in ('power-repair/request-04.body','source-supplement/murata/reference.pdf'):
                changed = dict(sources);changed.pop(missing)
                path.write_bytes(json_bytes(dict(schema=mechanics.SCHEMA,sources=changed)))
                with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'unsupported acquisition'):
                    mechanics.load(path)
