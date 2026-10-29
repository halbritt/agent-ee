"""Portable invariants for the finite R6 source exception, without native processes."""
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
import copy
from decimal import Decimal

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


class Margin470Tests(unittest.TestCase):
    def test_selected470_current_budget_and_copper_operand_are_consistent(self):
        output, total, idle = repair.currents('470')
        self.assertEqual(output, Fraction(60101, 5170000))
        self.assertEqual(total, Fraction(548767, 42652500))
        self.assertEqual(idle, Fraction(1617, 237350))
        self.assertLess(total, Fraction(20, 1000))
        root = Path(__file__).resolve().parents[1]
        before = json.loads((root/'fixtures/acquisition-qualification/geometry-model-murata.json').read_bytes())
        after = json.loads((root/'fixtures/acquisition-qualification/geometry-model-r6-470.json').read_bytes())
        self.assertEqual(after['dc_estimate']['input_current_a'], float(total))
        after['dc_estimate']['input_current_a'] = before['dc_estimate']['input_current_a']
        self.assertIn('RC0603FR-07470RL', after['packages']['R6']['basis'])
        after['packages']['R6']['basis'] = before['packages']['R6']['basis']
        self.assertEqual(after, before)  # No dimensional, tolerance, copper or other package changes.

    def test470_corner_changes_only_bleed_and_refuses_an_already_revised_source(self):
        source = (b'.subckt COUT_EFFECTIVE P N\nR_ESR P E 0.05\nC_EFFECTIVE E N 12.65u\n.ends COUT_EFFECTIVE\n'
                  b'.subckt RBLEED_OUTPUT P N\nR_EFFECTIVE P N 2222\n.ends RBLEED_OUTPUT\n')
        revised = repair._corner(source, '470')
        self.assertEqual(revised.replace(b'474.7', b'2222'), source)
        with self.assertRaisesRegex(ValueError, 'corner differs'):
            repair._corner(revised, '470')

    def test470_interval_and_power_recomputation_preserve_limits_and_other_operands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'analysis').mkdir()
            original=dict(parameters=dict(output_bleed_r=dict(min='2178 ohm',max='2222 ohm'),
                diode_drop=dict(min='.45 V',max='.45 V'),bus_c=dict(max='150e-12 F')),
                assertions=[dict(id='input-current-budget',lhs='3*rail/pullup_r+adc_load+external_load+iq_allowance+vin/input_bleed_r',op='<=',rhs='20e-3 A')])
            (root/'analysis/electrical-intervals.json').write_bytes(json_bytes(original))
            (root/'engineering-calculations.json').write_bytes(json_bytes(dict(values=dict(thermal_sensitivity_rise_all_power_in_u1=dict(value='42')),sources={})))
            repair._calculations(root,'470')
            interval=json.loads((root/'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(interval['parameters']['output_bleed_r'],dict(min='465.3 ohm',max='474.7 ohm'))
            self.assertEqual(interval['parameters']['bus_c'],original['parameters']['bus_c'])
            self.assertEqual(interval['assertions'][0]['rhs'],'20e-3 A')
            calc=json.loads((root/'engineering-calculations.json').read_bytes())['values']
            power=calc['output_bleed_dissipation']['exact_fraction']
            self.assertEqual(Fraction(power['numerator'],power['denominator']),Fraction(28611,1175000))
            self.assertGreater(Decimal(calc['source_current_margin']['value']),Decimal('.007'))
            self.assertNotIn('exact620',calc['output_bleed_dissipation']['basis'])

    def test470_mechanics_requires_its_own_complete_source_pins(self):
        from agent_ee import acquisition_source_supplement as supplement
        sources=dict.fromkeys(mechanics.SOURCES,'0'*64)
        sources.update(supplement.geometry_sources());sources.update(repair.geometry_sources('470'))
        sources['analysis/geometry-model.json']=repair.MODEL470
        sources['analysis/geometry_model.py']=mechanics.CHECKER
        document=dict(schema=mechanics.SCHEMA,sources=sources)
        mechanics._version(document)
        for key in repair.geometry_sources('470'):
            for mutation in ('missing','wrong'):
                changed=copy.deepcopy(document)
                if mutation=='missing':changed['sources'].pop(key)
                else:changed['sources'][key]='0'*64
                with self.subTest(key=key,mutation=mutation),self.assertRaisesRegex(ValueError,'unsupported acquisition'):
                    mechanics._version(changed)
        # Complete620 proof cannot authorize a470 model.
        changed=copy.deepcopy(document)
        changed['sources']={k:v for k,v in sources.items() if not k.startswith('power-repair/')}
        changed['sources'].update(repair.geometry_sources())
        with self.assertRaisesRegex(ValueError,'unsupported acquisition'):
            mechanics._version(changed)


    def test_minimum_uvlo_is_an_additional_removal_control_with_unchanged_stress_and_limit(self):
        directory='analysis/power-removal-corrected/'
        params=dict(model_files=[directory+'power.lib',directory+'corners-lowcin.lib'],
            active_parts={ref:dict(model_file=directory+name,subcircuit=sub) for ref,name,sub in
                [('U1','tps7a20-adj_trans-removal.lib','TPS7A20_ADJ_TRANS_REMOVAL'),
                 ('R6','corners-lowcin.lib','RBLEED_OUTPUT'),('C2','corners-lowcin.lib','COUT_EFFECTIVE')]},
            analyses=[dict(id='removal',step_s=20e-6,stop_s=.2,measurements=[dict(name='peak',max=.3,from_s=.01,to_s=.2)])],
            sources=[dict(id='supply',dc_v=4.75),dict(id='iq_allowance',current_limit=dict(sink_a=.0002))])
        original=dict(id='power-removal-stress-lowcin',kind='active_spice',params=params)
        before=copy.deepcopy(original);added=repair._minimum_uvlo_check(original)
        self.assertEqual(original,before)
        self.assertNotEqual(added['id'],original['id'])
        self.assertEqual(added['params']['sources'],params['sources'])
        self.assertEqual(added['params']['analyses'][0]['measurements'],params['analyses'][0]['measurements'])
        self.assertEqual(added['params']['analyses'][0]['stop_s'],.2)
        self.assertEqual(added['params']['analyses'][0]['step_s'],2e-6)
        self.assertEqual(added['params']['active_parts']['U1']['subcircuit'],'TPS7A20_ADJ_TRANS_REMOVAL_UVLO111')
        self.assertTrue(all('power-removal-uvlo111/' in row['model_file'] for row in added['params']['active_parts'].values()))
        self.assertEqual(added['params']['active_parts']['C2']['subcircuit'],'COUT_EFFECTIVE')
