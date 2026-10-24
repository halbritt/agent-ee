"""Independent whole-net resistance bounds for ordinary multilayer boards."""
import copy
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from agent_ee import dc_rail
from agent_ee.storage import digest, read_json, write_json

REPO = Path(__file__).resolve().parents[1]

# Native mutation uses a fresh process, just like production extraction. The
# combined test returned an unwrapped board after repeated in-process Load/Save;
# the same isolated four-layer mutation passes independently.
MUTATE = '''import pcbnew as k, sys
path, mode = sys.argv[1:]
b = k.LoadBoard(path)
if mode == 'four-layer-arc':
    b.SetCopperLayerCount(4)
for t in list(b.GetTracks()):
    if t.GetNetname() == '/IN' and t.GetClass() == 'PCB_TRACK' and t.GetLayer() == k.B_Cu:
        if mode == 'four-layer-arc':
            arc = k.PCB_ARC(b)
            arc.SetStart(t.GetStart()); arc.SetEnd(t.GetEnd())
            arc.SetMid(k.VECTOR2I(102000000, 99500000))
            arc.SetWidth(t.GetWidth()); arc.SetLayer(k.B_Cu); arc.SetNet(t.GetNet())
            b.Add(arc)
        elif mode != 'disconnected':
            raise ValueError(mode)
        b.Remove(t)
k.SaveBoard(path, b)
'''


def declaration():
    return {
        'schema': 'agent-ee.dc-path/2',
        'model': 'whole-net-ideal-copper-bound/1',
        'drc_check': 'drc',
        'material': {'rho_upper_ohm_m': 1.724e-8, 'temperature_c': 20},
        'layers': {'F.Cu': {'thickness_min_m': .000035},
                   'B.Cu': {'thickness_min_m': .000035}},
        'width_factor_min': 1,
        'barrels': {'height_max_m': .0016, 'plating_min_m': .00002,
                    'hole_factor_min': 1},
        'paths': [{'id': 'rail', 'net': 'RAIL',
                   'source': {'ref': 'SOURCE', 'pad': '1'},
                   'sink': {'ref': 'SINK', 'pad': '1'},
                   'max_injected_current_a': 1,
                   'max_drop_v': .03}],
        'contact_model': 'ideal-pad-and-junction/1',
    }


def geometry():
    return {
        'schema': 'agent-ee.dc-rail-geometry/1', 'pcbnew_version': '10.0.7',
        'enabled_copper_layers': ['F.Cu', 'B.Cu'],
        'tracks': [
            {'uuid': 'trace', 'native_class': 'PCB_TRACK', 'net': 'RAIL',
             'layer': 'F.Cu', 'width_nm': 250000, 'length_nm': 10000000},
            {'uuid': 'via', 'native_class': 'PCB_VIA', 'net': 'RAIL',
             'via_type': 'THROUGH', 'drill_nm': 300000,
             'copper_layers': ['F.Cu', 'B.Cu']},
            # An unrelated net is accounted for but contributes no rail drop.
            {'uuid': 'other', 'native_class': 'PCB_TRACK', 'net': 'SIGNAL',
             'layer': 'F.Cu', 'width_nm': 250000, 'length_nm': 100000000},
        ],
        'pads': [
            {'uuid': 'p1', 'ref': 'SOURCE', 'pad': '1', 'net': 'RAIL',
             'attribute': 'SMD', 'copper_layers': ['F.Cu'], 'drill_nm': [0, 0]},
            {'uuid': 'p2', 'ref': 'SINK', 'pad': '1', 'net': 'RAIL',
             'attribute': 'SMD', 'copper_layers': ['B.Cu'], 'drill_nm': [0, 0]},
        ],
        'zones': [], 'copper_graphics': [],
    }


class RailBoundTests(unittest.TestCase):
    def test_shipped_qualification_matches_implementation(self):
        self.assertEqual(read_json(REPO / 'fixtures/dc-rail/qualification.json'), dc_rail.qualification())

    def test_compilation_requires_an_earlier_full_native_drc(self):
        from agent_ee.capabilities import describe_check
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'contract.json', declaration())
            write_json(root / 'qualification.json', dc_rail.qualification())
            files = {name: digest(root / name) for name in ('contract.json', 'qualification.json')}
            files['board.kicad_pcb'] = '0' * 64
            spec = {'id': 'dc', 'kind': 'dc_path', 'params': {
                'contract': 'contract.json', 'qualification': 'qualification.json'}}
            drc = {'id': 'drc', 'kind': 'drc'}
            def descriptor(specs):
                return describe_check(spec, check_specs=specs, files=files,
                                      scope={'board': 'board.kicad_pcb'}, inputs=root)
            good = descriptor([drc, spec])
            self.assertEqual(good['status'], 'supported', good)
            self.assertEqual(good['prerequisites'], [{'check_id': 'drc', 'kind': 'drc'}])
            for specs in ([spec], [spec, drc], [{**drc, 'kind': 'netlist'}, spec],
                          [{**drc, 'params': {'projection': 'placement-stage/1'}}, spec]):
                with self.subTest(specs=specs):
                    self.assertEqual(descriptor(specs)['status'], 'unsupported')

    def test_trace_and_barrel_bound_uses_explicit_dimensions(self):
        report = dc_rail.bound(geometry(), declaration())
        # 10 mm x .25 mm x35um copper: .019702857142857 ohm.
        # 1.6mm barrel, conservative outer300um/inner260um diameters:
        # .001567903553660 ohm. Expected values calculated independently.
        path = report['paths']['rail']
        self.assertAlmostEqual(path['trace_resistance_ohm'], .019702857142857143, places=13)
        self.assertAlmostEqual(path['barrel_resistance_ohm'], .001567903553659586, places=13)
        self.assertAlmostEqual(path['drop_bound_v'], .02127076069651673, places=13)
        self.assertEqual(path['modeled_uuids'], ['trace', 'via'])
        self.assertEqual(report['excluded_other_net_uuids'], ['other'])

    def test_every_branch_and_plated_pad_counts(self):
        g = geometry()
        g['tracks'].append({**g['tracks'][0], 'uuid': 'branch'})
        g['pads'][1].update(attribute='PTH', drill_nm=[1000000, 1000000],
                            copper_layers=['F.Cu', 'B.Cu'])
        report = dc_rail.bound(g, declaration())['paths']['rail']
        self.assertAlmostEqual(report['trace_resistance_ohm'], .039405714285714286, places=13)
        self.assertAlmostEqual(report['barrel_resistance_ohm'], .002015875997562325, places=13)
        self.assertEqual(report['modeled_uuids'], ['branch', 'p2', 'trace', 'via'])
        self.assertEqual(report['ideal_contact_uuids'], ['p1'])

    def test_arcs_use_arc_length_and_different_layer_thickness(self):
        g, c = geometry(), declaration()
        g['tracks'][0].update(native_class='PCB_ARC', layer='B.Cu', length_nm=15707963.267948966)
        c['layers']['B.Cu']['thickness_min_m'] = .0000175
        c['width_factor_min'] = .8
        r = dc_rail.bound(g, c)['paths']['rail']['trace_resistance_ohm']
        self.assertAlmostEqual(r, .0773729390684115, places=13)

    def test_missing_physics_and_invalid_operating_conditions_refuse(self):
        for key in declaration():
            c = declaration()
            del c[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                dc_rail.validate_contract(c)
        for value in (True, 0, -1, float('nan'), float('inf')):
            c = declaration()
            c['material']['rho_upper_ohm_m'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                dc_rail.validate_contract(c)
        for key, value in (('width_factor_min', 1.1), ('contact_model', 'physical'),
                            ('drc_check', '../../other')):
            c = {**declaration(), key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                dc_rail.validate_contract(c)

    def test_ambiguous_or_wrong_net_terminals_refuse(self):
        for mutation in ('duplicate', 'wrong_net', 'missing', 'same'):
            g, c = geometry(), declaration()
            if mutation == 'duplicate':
                g['pads'].append({**g['pads'][0], 'uuid': 'duplicate'})
            elif mutation == 'wrong_net':
                g['pads'][0]['net'] = 'OTHER'
            elif mutation == 'missing':
                g['pads'].pop(0)
            else:
                c['paths'][0]['sink'] = c['paths'][0]['source']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                dc_rail.bound(g, c)

    def test_unmodeled_and_impossible_conductors_refuse(self):
        mutations = [
            ('tracks', 0, 'native_class', 'UNKNOWN'),
            ('tracks', 0, 'width_nm', 0),
            ('tracks', 0, 'length_nm', float('inf')),
            ('tracks', 1, 'drill_nm', 40000),
            ('tracks', 1, 'via_type', 'MICROVIA'),
            ('tracks', 1, 'copper_layers', ['F.Cu']),
            ('pads', 0, 'attribute', 'NPTH'),
            ('pads', 0, 'copper_layers', []),
        ]
        for key, i, field, value in mutations:
            g = geometry()
            g[key][i][field] = value
            with self.subTest(mutation=(key, field, value)), self.assertRaises(ValueError):
                dc_rail.bound(g, declaration())
        for key in ('zones', 'copper_graphics'):
            g = geometry()
            g[key] = ['native-copper']
            with self.subTest(key=key), self.assertRaises(ValueError):
                dc_rail.bound(g, declaration())

    def test_layer_and_inventory_omissions_refuse(self):
        g = geometry()
        g['enabled_copper_layers'].append('In1.Cu')
        with self.assertRaises(ValueError):
            dc_rail.bound(g, declaration())
        g = geometry()
        g['tracks'].append(copy.deepcopy(g['tracks'][0]))
        with self.assertRaises(ValueError):
            dc_rail.bound(g, declaration())
        g = geometry()
        del g['zones']
        with self.assertRaises(ValueError):
            dc_rail.bound(g, declaration())


class NativeRailTests(unittest.TestCase):
    def setUp(self):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='dc-rail-')))

    def prepare(self, label):
        root = self.root / label
        root.mkdir()
        shutil.copytree(REPO / 'fixtures/rc/control', root / 'project')
        fixture = REPO / 'fixtures/dc-rail'
        shutil.copyfile(fixture / 'rc_filter.kicad_pcb', root / 'project/rc_filter.kicad_pcb')
        for name in ('contract.json', 'qualification.json', 'requirements.md'):
            shutil.copyfile(fixture / name, root / name)
        s = read_json(fixture / 'control.scope.json')
        s['inputs'] = [{'path': str(root / 'project'), 'at': 'project'}] + [
            {'path': str(root / name), 'at': name} for name in ('contract.json', 'qualification.json', 'requirements.md')
        ] + [i for i in s['inputs'] if i['at'].startswith('stock/')]
        write_json(root / 'scope.json', s)
        return root

    def review(self, root):
        from agent_ee.cli import run_review, verify
        with contextlib.redirect_stdout(io.StringIO()):
            # Checks-only is deliberately incomplete overall, even with passing checks.
            code = run_review(root / 'scope.json', root / 'run', 180, checks_only=True)
        self.assertEqual(code, 1)
        sealed = verify(root / 'run')
        self.assertEqual(sealed['outcome'], 'incomplete', sealed)
        return read_json(root / 'run/checks/dc.json')

    def test_native_multilayer_oracle_limits_and_seal(self):
        from agent_ee.cli import verify
        root = self.prepare('control')
        result = self.review(root)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        native = read_json(root / 'run/work/dc/geometry.json')
        path = result['details']['analysis']['network']['paths']['rail']
        self.assertAlmostEqual(path['drop_bound_v'], .011637322408364768, delta=1e-12)
        self.assertEqual(len(path['modeled_uuids']), 6)  # Three tracks, two vias, one PTH pad.
        self.assertEqual({t['layer'] for t in native['tracks'] if t['net'] == '/IN' and t['native_class'] == 'PCB_TRACK'},
                         {'F.Cu', 'B.Cu'})
        for rel in ('work/dc/geometry.json', 'work/dc/analysis.json', 'work/drc/drc.json', 'inputs/contract.json'):
            p = root / 'run' / rel
            before = p.read_bytes()
            p.write_bytes(before + b'\n')
            with self.subTest(tamper=rel), self.assertRaises(ValueError):
                verify(root / 'run')
            p.write_bytes(before)
        bad = self.prepare('drop-defect')
        c = read_json(bad / 'contract.json')
        c['paths'][0]['max_drop_v'] = .010
        write_json(bad / 'contract.json', c)
        result = self.review(bad)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertLess(result['details']['margins']['rail_drop']['max'], 0)

    def test_native_disconnection_cannot_produce_a_drop_pass(self):
        root = self.prepare('disconnected')
        p = root / 'project/rc_filter.kicad_pcb'
        subprocess.run(['/usr/bin/python3', '-c', MUTATE, str(p), 'disconnected'],
                       check=True, capture_output=True, text=True, timeout=30)
        result = self.review(root)
        self.assertEqual(result['outcome'], 'unknown', result)
        self.assertNotEqual(result['execution'], 'ok', result)
        self.assertIn('passing full native DRC', result['summary'])
        self.assertGreater(read_json(root / 'run/checks/drc.json')['details']['unconnected_items'], 0)

    def test_native_four_layers_and_arc_keep_full_barrel_and_arc_length(self):
        root = self.prepare('four-layer-arc')
        p = root / 'project/rc_filter.kicad_pcb'
        subprocess.run(['/usr/bin/python3', '-c', MUTATE, str(p), 'four-layer-arc'],
                       check=True, capture_output=True, text=True, timeout=30)
        c = read_json(root / 'contract.json')
        c['layers'].update({'In1.Cu': {'thickness_min_m': .000035},
                            'In2.Cu': {'thickness_min_m': .000035}})
        c['paths'][0]['max_drop_v'] = .02
        write_json(root / 'contract.json', c)
        result = self.review(root)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        path = result['details']['analysis']['network']['paths']['rail']
        self.assertAlmostEqual(path['drop_bound_v'], .012761954256815513, delta=2e-11)
        native = read_json(root / 'run/work/dc/geometry.json')
        self.assertEqual(set(native['enabled_copper_layers']), {'F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'})
        vias = [t for t in native['tracks'] if t['native_class'] == 'PCB_VIA']
        self.assertTrue(all(len(v['copper_layers']) == 4 for v in vias))

    def test_missing_stale_projected_and_ignored_native_evidence_refuses(self):
        from agent_ee import checks, dc_path
        root = self.prepare('prerequisite')
        result = self.review(root)
        self.assertEqual(result['outcome'], 'pass', result)
        run = root / 'run'
        manifest = read_json(run / 'candidate.json')
        spec = manifest['scope']['checks'][1]
        prior = read_json(run / 'checks/drc.json')
        raw = read_json(run / 'work/drc/drc.json')
        for mutation in ('missing', 'candidate', 'board', 'projection', 'ignored'):
            bad = copy.deepcopy(prior)
            if mutation == 'missing':
                (run / 'checks/drc.json').unlink()
            else:
                if mutation == 'candidate':
                    bad['candidate_id'] = 'not-this-candidate'
                elif mutation == 'board':
                    bad['details']['board_sha256'] = '0' * 64
                elif mutation == 'projection':
                    bad['details']['projection'] = 'placement-stage/1'
                else:
                    write_json(run / 'work/drc/drc.json', {**raw, 'ignored_checks': [{'key': 'unconnected_items'}]})
                write_json(run / 'checks/drc.json', bad)
            ctx = checks.Ctx(run, manifest, spec, time.monotonic() + 60)
            with self.subTest(mutation=mutation), self.assertRaises(checks.CheckError):
                dc_path.check_dc_path(ctx)
            self.assertFalse(read_json(ctx.work / 'analysis.json')['complete'])
            write_json(run / 'checks/drc.json', prior)
            write_json(run / 'work/drc/drc.json', raw)
