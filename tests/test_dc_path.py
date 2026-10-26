"""DC model oracles are independent of extraction and the conductance solver."""
import copy
import unittest

from agent_ee import dc_path


def contract():
    return {'schema': 'agent-ee.dc-path/1', 'net': 'PATH', 'layer': 'F.Cu',
            'stackup': {'layer': 'F.Cu', 'thickness_m': .000035},
            'material': {'rho_ref_ohm_m': 1.724e-8, 'reference_c': 20., 'alpha_per_c': .00393,
                         'valid_c': [20., 80.], 'model': 'linear-external-temperature/1'},
            'temperature_c': 20., 'dimensions': {'width_factors': [1., 1.],
                'thickness_factors': [1., 1.], 'length_factors': [1., 1.]},
            'terminals': [{'ref': 'SOURCE', 'pad': '1', 'contact': 'ideal-centered-round-smd/1'},
                          {'ref': 'SINK', 'pad': '1', 'contact': 'ideal-centered-round-smd/1'}],
            'loads': [{'id': 'one_amp', 'current_a': 1., 'max_drop_v': .03, 'max_loss_w': .03}]}


class DCPathModelTests(unittest.TestCase):
    def test_shipped_qualification_matches_reviewed_producer(self):
        self.assertEqual(read_json(REPO / 'fixtures/dc-path/qualification.json'), dc_path.qualification())

    def test_straight_resistance_and_all_solver_residuals(self):
        solution = dc_path.solve([{'start': (0, 0), 'end': (10000000, 0),
                                   'resistance_ohm': .019702857142857143}],
                                 [(0, 0), (10000000, 0)], 1.)
        self.assertAlmostEqual(solution['resistance_ohm'], .019702857142857143, places=14)
        self.assertLess(solution['kcl_residual_a'], 1e-12)
        self.assertLess(solution['energy_residual_w'], 1e-12)

    def test_series_parallel_account_for_every_branch(self):
        edges = [{'start': (0, 0), 'end': (5, 0), 'resistance_ohm': .01},
                 {'start': (5, 0), 'end': (10, 0), 'resistance_ohm': .01},
                 {'start': (0, 0), 'end': (5, 5), 'resistance_ohm': .02},
                 {'start': (5, 5), 'end': (10, 0), 'resistance_ohm': .02}]
        solution = dc_path.solve(edges, [(0, 0), (10, 0)], 2.)
        self.assertAlmostEqual(solution['resistance_ohm'], 1 / 75, places=14)
        self.assertAlmostEqual(solution['loss_w'], 4 / 75, places=14)
        self.assertEqual(len(solution['edge_currents_a']), 4)
        for extra in ({'start': (5, 0), 'end': (7, 9), 'resistance_ohm': .01},
                      {'start': (50, 0), 'end': (70, 0), 'resistance_ohm': .01},
                      {'start': (5, 0), 'end': (5, 5), 'resistance_ohm': .01}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                dc_path.solve(edges + [extra], [(0, 0), (10, 0)], 1.)

    def test_no_default_physics_or_unqualified_inputs(self):
        dc_path.validate_contract(contract())
        for key in contract():
            broken = contract()
            del broken[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                dc_path.validate_contract(broken)
        for section in ('stackup', 'material', 'dimensions'):
            for key in contract()[section]:
                broken = contract()
                del broken[section][key]
                with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                    dc_path.validate_contract(broken)
        for key, value in (('temperature_c', 81), ('temperature_c', float('nan')),
                           ('temperature_c', True), ('layer', 'B.Cu')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                dc_path.validate_contract({**contract(), key: value})


import contextlib
import io
import math
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from agent_ee import checks
from agent_ee.baseline import compile_baseline, validate_witnesses
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.run_record import run_review, verify
from agent_ee.storage import digest, read_json, write_json
import test_capability_pipeline as pipeline

REPO = Path(__file__).resolve().parents[1]


class NativeDCPathTests(unittest.TestCase):
    agent = pipeline.NativeCapabilityPipelineTests.agent

    def setUp(self):
        pipeline.NativeCapabilityPipelineTests.setUp(self)
        self.expected = read_json(REPO / 'tests/fixtures/dc-path.json')

    def prepare(self, label, board='straight', declared=None):
        root = self.root / label
        root.mkdir()
        project = root / 'project'
        project.mkdir()
        shutil.copyfile(REPO / 'fixtures/dc-path' / (board + '.kicad_pcb'), project / 'dc_path.kicad_pcb')
        shutil.copyfile(REPO / 'fixtures/dc-path/dc_path.kicad_sch', project / 'dc_path.kicad_sch')
        shutil.copyfile(REPO / 'fixtures/dc-path/EXPECTATIONS.md', root / 'requirements.md')
        write_json(root / 'contract.json', contract() if declared is None else declared)
        write_json(root / 'qualification.json', dc_path.qualification())
        scope = read_json(REPO / 'fixtures/dc-path/straight.scope.json')
        scope['inputs'] = [{'path': str(project), 'at': 'project'}] + [
            {'path': str(root / name), 'at': name} for name in
            ('contract.json', 'qualification.json', 'requirements.md')]
        scope['review_profile'] = {'schema': 'agent-ee.profile/1', 'name': 'scripted-dc-corpus',
            'reviewers': [{**pipeline.PROFILE_CALL, 'role': 'circuit'},
                          {**pipeline.PROFILE_CALL, 'role': 'physical'}],
            'adjudicator': pipeline.PROFILE_CALL}
        write_json(root / 'scope.json', scope)
        return root

    def ctx(self, root):
        (root / 'run').mkdir()
        manifest = capture(root / 'scope.json', root / 'run')
        return checks.Ctx(root / 'run', manifest, manifest['scope']['checks'][0], time.monotonic() + 120)

    def test_actual_native_oracles_and_dimensional_cases(self):
        for board in ('straight', 'series', 'parallel', 'arc', 'narrow', 'long'):
            with self.subTest(board=board):
                root = self.prepare(board, board)
                ctx = self.ctx(root)
                dc_path.check_dc_path(ctx)
                result = ctx.result
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['outcome'], 'defect' if board in ('arc', 'narrow', 'long') else 'pass', result)
                report = result['details']['analysis']
                self.assertTrue(report['complete'])
                self.assertEqual(report['network']['coverage']['native_tracks'],
                                 report['network']['coverage']['modeled_edges'])
                for analysis in report['analyses']:
                    solution = analysis['solutions']['one_amp']
                    self.assertAlmostEqual(solution['resistance_ohm'], self.expected[board], delta=1e-10)
                    self.assertLess(solution['kcl_residual_a'], 1e-12)
                    self.assertLess(solution['energy_residual_w'], 1e-12)
                if board == 'arc':
                    arc = report['network']['edges'][0]
                    self.assertEqual(arc['mid_nm'], [35000000, 35000000])
                    self.assertAlmostEqual(arc['length_m'], .005 * math.pi, delta=1e-12)
                    self.assertNotAlmostEqual(arc['length_m'], .01, places=5)
        declared = contract()
        declared['dimensions'].update(width_factors=[.9, 1.1], thickness_factors=[.9, 1.1])
        declared['temperature_c'] = 80.
        declared['loads'].append({'id': 'two_amp', 'current_a': 2., 'max_drop_v': .1, 'max_loss_w': .2})
        ctx = self.ctx(self.prepare('envelope', declared=declared))
        dc_path.check_dc_path(ctx)
        metrics = ctx.result['details']['metrics']
        nominal = self.expected['straight'] * 1.2358
        self.assertAlmostEqual(metrics['one_amp_drop']['value'], nominal, places=13)
        self.assertAlmostEqual(metrics['one_amp_drop']['lower'], nominal / 1.21, delta=1e-9)
        self.assertAlmostEqual(metrics['one_amp_drop']['upper'], nominal / .81, delta=1e-9)
        self.assertAlmostEqual(metrics['two_amp_loss']['value'], nominal * 4, places=13)

    def test_public_seal_witness_and_input_output_tampering(self):
        for board in ('straight', 'narrow'):
            root = self.prepare('sealed-' + board, board)
            with patch('agent_ee.reviewers.dispatch', side_effect=self.agent), contextlib.redirect_stdout(io.StringIO()):
                status = run_review(root / 'scope.json', root / 'run', 180, host=self.host)
            result = verify(root / 'run', host=self.host)
            self.assertEqual(result['launch_expectation']['declared_tools'], dc_path.TOOLS)
            self.assertIn('pcbnew', result['launch_expectation']['required_runtime'])
            self.assertEqual(status, int(board == 'narrow'), result)
            self.assertEqual(result['outcome'], 'defects_or_unresolved' if board == 'narrow' else 'criteria_satisfied', result)
            if board != 'narrow':
                continue
            self.assertTrue(any(f.get('status') == 'accepted' and f['source'] == 'check' for f in result['findings']))
            witnesses = [{'requirement_id': 'R-dc', 'check_id': 'dc'}]
            evidence = [{'path': 'checks/dc.json', 'locator': 'outcome'}]
            validate_witnesses(root / 'run', witnesses, evidence)
            for rel in ('work/dc/network.json', 'work/dc/analysis.json', 'work/dc/geometry.json',
                        'inputs/contract.json', 'inputs/qualification.json'):
                path = root / 'run' / rel
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.subTest(tamper=rel):
                    with self.assertRaises(ValueError):
                        verify(root / 'run', host=self.host)
                    with self.assertRaises(ValueError):
                        validate_witnesses(root / 'run', witnesses, evidence)
                path.write_bytes(original)

    def test_native_unmodeled_conductors_and_contacts_refuse(self):
        mutation = '''import pcbnew as p, sys
path, kind = sys.argv[1:]
b = p.LoadBoard(path)
t = next(iter(b.GetTracks()))
def v(x,y): return p.VECTOR2I(x,y)
if kind == 'zone':
 z=p.ZONE(b); z.SetLayer(p.F_Cu); z.SetNetCode(t.GetNetCode())
 outline=z.Outline(); outline.NewOutline()
 for x,y in ((32000000,29000000),(38000000,29000000),(38000000,31000000),(32000000,31000000)): outline.Append(x,y)
 b.Add(z)
elif kind == 'via':
 z=p.PCB_VIA(b); z.SetPosition(v(35000000,30000000)); z.SetNetCode(t.GetNetCode()); b.Add(z)
elif kind in ('crossing','overlap','interior','island','dangling'):
 z=p.PCB_TRACK(b); z.SetLayer(p.F_Cu); z.SetNetCode(t.GetNetCode()); z.SetWidth(250000)
 a,e={'crossing':((35000000,25000000),(35000000,35000000)),
 'overlap':((33000000,30050000),(37000000,30050000)),
 'interior':((35000000,30000000),(35000000,35000000)),
 'island':((50000000,30000000),(55000000,30000000)),
 'dangling':((40000000,30000000),(45000000,30000000))}[kind]
 z.SetStart(v(*a)); z.SetEnd(v(*e)); b.Add(z)
elif kind == 'disconnected': t.SetEnd(v(39000000,30000000))
elif kind == 'pad': next(iter(next(iter(b.GetFootprints())).Pads())).SetShape(p.PAD_SHAPE_RECT)
elif kind == 'duplicate':
 for fp in b.GetFootprints(): fp.SetReference('SOURCE')
p.SaveBoard(path,b)
'''
        for kind in ('zone', 'via', 'crossing', 'overlap', 'interior', 'island', 'dangling', 'disconnected', 'pad', 'duplicate'):
            with self.subTest(kind=kind):
                root = self.prepare('refuse-' + kind)
                completed = subprocess.run(['/usr/bin/python3', '-c', mutation,
                    str(root / 'project/dc_path.kicad_pcb'), kind], capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                ctx = self.ctx(root)
                with self.assertRaises(checks.CheckError):
                    dc_path.check_dc_path(ctx)
                self.assertEqual(ctx.result['outcome'], 'unknown')
                self.assertNotIn('metrics', ctx.result['details'])
                self.assertFalse(read_json(ctx.work / 'analysis.json')['complete'])

    def test_native_substrate_and_field_inventory(self):
        mutation = """import pcbnew as p, sys
path, kind = sys.argv[1:]
b = p.LoadBoard(path)
def v(x,y): return p.VECTOR2I(round(x*1e6),round(y*1e6))
if kind in ('missing', 'open'):
 for g in list(b.GetDrawings())[:(1 if kind == 'open' else 4)]: b.Remove(g)
elif kind.startswith('field'):
 fp=next(iter(b.GetFootprints()))
 field = fp.Reference() if kind == 'field-reference' else fp.Value()
 if kind == 'field-user':
  field=p.PCB_FIELD(fp, p.FIELD_T_USER, 'Custom'); fp.Add(field)
 field.SetText('COPPER'); field.SetVisible(True); field.SetLayer(p.F_Cu)
elif kind == 'outside':
 for t in b.GetTracks(): t.Move(v(40,0))
 for fp in b.GetFootprints(): fp.Move(v(40,0))
else:
 owner=next(iter(b.GetFootprints())) if kind == 'footprint-cutout' else b
 coords=((34,29),(36,29),(36,31),(34,31))
 if kind == 'clear-cutout': coords=((22,22),(24,22),(24,24),(22,24))
 if kind == 'envelope': coords=((34,30.13),(36,30.13),(36,31),(34,31))
 if kind == 'touching': coords=((20,29),(36,29),(36,31),(20,31))
 if kind == 'diagonal': coords=((22,22),(24,23),(24,24),(22,24))
 if kind == 'nested': coords=((22,22),(24,22),(24,24),(22,24))
 for a,z in zip(coords,coords[1:]+coords[:1]):
  g=p.PCB_SHAPE(owner); g.SetLayer(p.Edge_Cuts); g.SetShape(p.SHAPE_T_SEGMENT)
  g.SetStart(v(*a)); g.SetEnd(v(*z)); g.SetWidth(50000); owner.Add(g)
 if kind in ('nested', 'overlap'):
  coords=((22.5,22.5),(23.5,22.5),(23.5,23.5),(22.5,23.5)) if kind == 'nested' else ((35,29),(37,29),(37,31),(35,31))
  for a,z in zip(coords,coords[1:]+coords[:1]):
   g=p.PCB_SHAPE(b); g.SetLayer(p.Edge_Cuts); g.SetShape(p.SHAPE_T_SEGMENT)
   g.SetStart(v(*a)); g.SetEnd(v(*z)); g.SetWidth(50000); b.Add(g)
 if kind == 'curve':
  g=p.PCB_SHAPE(b); g.SetLayer(p.Edge_Cuts); g.SetShape(p.SHAPE_T_CIRCLE)
  g.SetStart(v(25,25)); g.SetEnd(v(26,25)); b.Add(g)
p.SaveBoard(path,b)
"""
        for kind in ('cutout', 'footprint-cutout', 'clear-cutout', 'missing', 'open',
                     'outside', 'curve', 'envelope', 'nested', 'overlap', 'touching', 'diagonal', 'field-reference', 'field-value', 'field-user'):
            with self.subTest(kind=kind):
                declared = contract()
                declared['dimensions']['width_factors'] = [.9, 1.1]
                root = self.prepare('inventory-' + kind, declared=declared)
                completed = subprocess.run(['/usr/bin/python3', '-c', mutation,
                    str(root / 'project/dc_path.kicad_pcb'), kind], capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                ctx = self.ctx(root)
                if kind == 'clear-cutout':
                    dc_path.check_dc_path(ctx)
                    self.assertTrue(ctx.result['details']['analysis']['complete'])
                else:
                    with self.assertRaises(checks.CheckError):
                        dc_path.check_dc_path(ctx)
                    self.assertNotIn('metrics', ctx.result['details'])
                    self.assertFalse(read_json(ctx.work / 'analysis.json')['complete'])
                geo = read_json(ctx.work / 'geometry.json')
                self.assertIn('substrate', geo)
                if kind.startswith('field'):
                    self.assertIn('PCB_FIELD', [g['native_class'] for g in geo['copper_graphics']])
                if kind in ('cutout', 'footprint-cutout'):
                    self.assertEqual(len(geo['substrate']['boundaries']), 8)

    def test_qualification_scope3_and_baseline_own_inputs(self):
        root = self.prepare('descriptor')
        ctx = self.ctx(root)
        m = ctx.manifest
        descriptor = describe_check(ctx.spec, check_specs=[ctx.spec], files=m['files'], scope=ctx.scope, inputs=ctx.inputs)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['immutable_inputs'], ['contract.json', 'qualification.json'])
        scope2 = copy.deepcopy(ctx.scope)
        scope2['schema'] = 'agent-ee.scope/2'
        scope2['baseline']['requirements'][0].pop('intent')
        self.assertIn('contract.json', compile_baseline(scope2, m['files'], ctx.inputs)['files'])
        for key in ('temperature_c', 'stackup', 'terminals'):
            broken = contract()
            del broken[key]
            write_json(root / 'run/inputs/contract.json', broken)
            files = {**m['files'], 'contract.json': digest(root / 'run/inputs/contract.json')}
            descriptor = describe_check(ctx.spec, check_specs=[ctx.spec], files=files, scope=ctx.scope, inputs=ctx.inputs)
            self.assertEqual(descriptor['status'], 'unsupported', descriptor)
        write_json(ctx.inputs / 'contract.json', contract())
        with patch.dict(dc_path.TOOLS, {'/usr/bin/python3': '0' * 64}):
            with self.assertRaisesRegex(checks.CheckError, 'requalification'):
                dc_path.check_dc_path(ctx)

    def test_native_failure_and_partial_solver_never_supply_metrics(self):
        root = self.prepare('principal-failures')
        original = self.ctx(root)
        for label in ('native', 'solver', 'partial', 'empirical'):
            manifest = copy.deepcopy(original.manifest)
            spec = manifest['scope']['checks'][0]
            spec['id'] = label
            if label == 'empirical':
                manifest['scope']['empirical'] = 'unqualified.json'
            ctx = checks.Ctx(root / 'run', manifest, spec, time.monotonic() + 120)
            if label == 'native':
                injection = patch.object(ctx, 'execute', side_effect=checks.CheckError('failed', 'injected native failure'))
            elif label == 'partial':
                real_solve = dc_path.solve
                calls = []
                def fail_later(*args):
                    calls.append(1)
                    if len(calls) == 2:
                        raise ValueError('injected failed second dimensional case')
                    return real_solve(*args)
                injection = patch.object(dc_path, 'solve', side_effect=fail_later)
            else:
                injection = patch.object(dc_path, 'solve', side_effect=ValueError('injected failed solver residual'))
            with injection, self.assertRaises(checks.CheckError):
                dc_path.check_dc_path(ctx)
            self.assertNotIn('metrics', ctx.result['details'])
            receipt = read_json(ctx.work / 'analysis.json')
            self.assertFalse(receipt['complete'])
            if label == 'partial':
                self.assertEqual(len(receipt['analyses']), 1)
                self.assertEqual(receipt['analyses'][0]['id'], 'nominal')
            self.assertEqual(ctx.result['outcome'], 'unknown')


class DCNumericalBoundaryTests(unittest.TestCase):
    def test_invalid_residual_and_nonfinite_solution_refuse(self):
        edges = [{'start': (0, 0), 'end': (1, 0), 'resistance_ohm': .02}]
        solution = dc_path.solve(edges, [(0, 0), (1, 0)], 1.)
        for field, invalid in (('kcl_residual_a', .01), ('energy_residual_w', .01),
                               ('loss_w', .04), ('drop_v', float('nan')),
                               ('resistance_ohm', float('inf'))):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'residual'):
                dc_path.validate_solution({**solution, field: invalid}, 1.)
        for resistance in (0, -1, float('nan'), float('inf'), 1e-320):
            with self.subTest(resistance=resistance), self.assertRaises(ValueError):
                dc_path.solve([{**edges[0], 'resistance_ohm': resistance}], [(0, 0), (1, 0)], 1.)
        with self.assertRaises(ValueError):
            dc_path.solve([], [(0, 0), (1, 0)], 1.)

    def test_explicit_invalid_contract_boundaries(self):
        for section, field, value in (('dimensions', 'length_factors', [.9, 1.1]),
            ('dimensions', 'width_factors', [0, 1]), ('dimensions', 'thickness_factors', [1.1, 1.2]),
            ('stackup', 'thickness_m', 0), ('material', 'rho_ref_ohm_m', float('inf')),
            ('material', 'alpha_per_c', -.02), ('material', 'valid_c', [20, 90])):
            c = contract()
            c[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                dc_path.validate_contract(c)
