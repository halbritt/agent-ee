"""AGENTEE-231 declared layer policy on an original four-layer probe with GND planes on In1/In2.

Actual routing with the pinned kicad-route/KRT: an F.Cu/B.Cu allowlist keeps
tracks off the inner reference planes; a control that prices the outer layers
out of reach routes on the planes and the declared area-loss bound fails it; a
route over a plane void fails the declared RF reference continuity; an RF route
that changes side through a via cuts both planes; copper the input already had on
an inner layer is exempt only by its geometry; a declared plane with no fill is
never a pass. The reference measurement itself runs on original boards whose
plane voids sit just beyond the clearance KiCad cut for an RF pad.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import add_planes as planes, benchmark, stage_receipts
from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from agent_ee.storage import read_json
from tests.test_add_planes import PlaneFixture
from tests.toolchain import require_kicad_route, require_toolchain

PREPARE = r'''
import sys
import pcbnew as k
y = float(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else 'pth'
b = k.LoadBoard('/candidate/project/pod.kicad_pcb')
net = k.NETINFO_ITEM(b, 'SIGNAL'); b.Add(net)


def probe(ref, x, y, net, side):
    fp = k.FOOTPRINT(b); fp.SetReference(ref); fp.SetValue('Signal probe'); b.Add(fp)
    fp.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(y)))
    for field in fp.GetFields(): field.SetVisible(False)
    p = k.PAD(fp); p.SetNumber('1'); p.SetNet(net)
    if side is None:
        p.SetAttribute(k.PAD_ATTRIB_PTH); p.SetShape(k.PAD_SHAPE_CIRCLE)
        p.SetSize(k.VECTOR2I(k.FromMM(1.6), k.FromMM(1.6))); p.SetDrillSize(k.VECTOR2I(k.FromMM(.8), k.FromMM(.8)))
        layers = k.LSET(k.LSET.AllCuMask()); layers.AddLayer(k.F_Mask); layers.AddLayer(k.B_Mask)
    else:
        # A surface pad on one side only: the route must change side through a via.
        p.SetAttribute(k.PAD_ATTRIB_SMD); p.SetShape(k.PAD_SHAPE_RECT)
        p.SetSize(k.VECTOR2I(k.FromMM(1.2), k.FromMM(1.2)))
        layers = k.LSET(); layers.AddLayer(side); layers.AddLayer(k.F_Mask if side == k.F_Cu else k.B_Mask)
    p.SetLayerSet(layers)
    p.SetPosition(fp.GetPosition()); fp.Add(p)


for ref, x, side in [('TP1', 15, k.F_Cu), ('TP2', 45, k.B_Cu)]:
    probe(ref, x, y, net, side if mode == 'layer_change' else None)
if mode == 'inner_track':
    # The input's own copper on In1.Cu: a finished AUX connection the router must keep.
    aux = k.NETINFO_ITEM(b, 'AUX'); b.Add(aux)
    probe('TP3', 20, 46, aux, None); probe('TP4', 40, 46, aux, None)
    t = k.PCB_TRACK(b); t.SetLayer(k.In1_Cu); t.SetWidth(k.FromMM(.25)); t.SetNet(aux)
    t.SetStart(k.VECTOR2I(k.FromMM(20), k.FromMM(46))); t.SetEnd(k.VECTOR2I(k.FromMM(40), k.FromMM(46))); b.Add(t)
assert k.SaveBoard('/work/pod.kicad_pcb', b)
'''
# Original measurement boards: GND planes on In1/In2 with 0.25 mm clearance, an RF
# net SIGNAL between two through-hole pads on F.Cu, and one change per scenario.
MEASURE_BOARDS = r'''
import json, subprocess, sys
import pcbnew as k
mode, measure, spec = sys.argv[1], open('/candidate/measure.py').read(), sys.argv[2]
b = k.CreateEmptyBoard(); b.SetCopperLayerCount(4)
for x0, y0, x1, y1 in [(0, 0, 60, 0), (60, 0, 60, 50), (60, 50, 0, 50), (0, 50, 0, 0)]:
    e = k.PCB_SHAPE(b); e.SetShape(k.SHAPE_T_SEGMENT); e.SetLayer(k.Edge_Cuts); e.SetWidth(k.FromMM(.1))
    e.SetStart(k.VECTOR2I(k.FromMM(x0), k.FromMM(y0))); e.SetEnd(k.VECTOR2I(k.FromMM(x1), k.FromMM(y1))); b.Add(e)
nets = {}
for name in ('GND', 'SIGNAL', 'OTHER'):
    nets[name] = k.NETINFO_ITEM(b, name); b.Add(nets[name])
for ref, x, y, net in [('G1', 5, 5, 'GND'), ('O1', 30, 20, 'OTHER'), ('TP1', 15, 35, 'SIGNAL'),
                       ('TP2', 45, 35, 'SIGNAL')]:
    fp = k.FOOTPRINT(b); fp.SetReference(ref); b.Add(fp); fp.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(y)))
    p = k.PAD(fp); p.SetNumber('1'); p.SetNet(nets[net]); p.SetAttribute(k.PAD_ATTRIB_PTH)
    p.SetShape(k.PAD_SHAPE_CIRCLE); p.SetSize(k.VECTOR2I(k.FromMM(1.6), k.FromMM(1.6)))
    p.SetDrillSize(k.VECTOR2I(k.FromMM(.8), k.FromMM(.8)))
    layers = k.LSET(k.LSET.AllCuMask()); layers.AddLayer(k.F_Mask); layers.AddLayer(k.B_Mask); p.SetLayerSet(layers)
    p.SetPosition(fp.GetPosition()); fp.Add(p)
for layer in (k.In1_Cu, k.In2_Cu):
    z = k.ZONE(b); z.SetNet(nets['GND']); z.SetLayer(layer); z.SetLocalClearance(k.FromMM(.25))
    z.SetMinThickness(k.FromMM(.2)); z.SetPadConnection(k.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(k.FromMM(.3)); z.SetThermalReliefSpokeWidth(k.FromMM(.3))
    z.Outline().NewOutline()
    for x, y in [(0, 0), (60, 0), (60, 50), (0, 50)]: z.Outline().Append(k.FromMM(x), k.FromMM(y))
    b.Add(z)


def track(x0, x1, y, layer, net):
    t = k.PCB_TRACK(b); t.SetLayer(layer); t.SetWidth(k.FromMM(.25)); t.SetNet(nets[net])
    t.SetStart(k.VECTOR2I(k.FromMM(x0), k.FromMM(y))); t.SetEnd(k.VECTOR2I(k.FromMM(x1), k.FromMM(y))); b.Add(t)


def via(x, y, net):
    v = k.PCB_VIA(b); v.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(y))); v.SetViaType(k.VIATYPE_THROUGH)
    v.SetLayerPair(k.F_Cu, k.B_Cu); v.SetWidth(k.FromMM(.6)); v.SetDrill(k.FromMM(.3)); v.SetNet(nets[net]); b.Add(v)


def void(x1):
    # A fill keepout on both planes from TP1's centre to x1, under the RF launch.
    z = k.ZONE(b); z.SetIsRuleArea(True); z.SetDoNotAllowZoneFills(True)
    layers = k.LSET(); layers.AddLayer(k.In1_Cu); layers.AddLayer(k.In2_Cu); z.SetLayerSet(layers)
    z.Outline().NewOutline()
    for x, y in [(15, 34.8), (x1, 34.8), (x1, 35.2), (15, 35.2)]: z.Outline().Append(k.FromMM(x), k.FromMM(y))
    b.Add(z)


if mode == 'rf_via':
    track(15, 30, 35, k.F_Cu, 'SIGNAL'); via(30, 35, 'SIGNAL'); track(30, 45, 35, k.B_Cu, 'SIGNAL')
else:
    track(15, 45, 35, k.F_Cu, 'SIGNAL')
if mode == 'void_in_old_ring':
    # TP1's plane clearance ends at 0.8 + 0.25 = 1.05 mm; the old padded ring excused 1.10 mm.
    void(16.095)
elif mode == 'void_beyond_old_ring':
    void(16.5)
elif mode == 'other_via':
    # Clear of the RF track on F.Cu; its antipad in the planes is not.
    via(30, 35.5, 'OTHER')
assert k.SaveBoard('/work/board.kicad_pcb', b)
subprocess.run(['kicad-cli', 'pcb', 'drc', '--refill-zones', '--save-board', '-o', '/work/drc.rpt',
                '/work/board.kicad_pcb'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', measure, '/work/board.kicad_pcb', spec, '/work/measured.json'],
               check=True)
'''
OUTER = {'signal_layers': ['F.Cu', 'B.Cu'], 'pour_area_loss_pct': 0.5,
         'reference': {'net': 'GND', 'layers': ['In1.Cu', 'In2.Cu'], 'rf_nets': ['SIGNAL']}}
# Control: inner layers allowed and the outer layers priced at KRT's maximum.
INNER = {'signal_layers': ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'], 'layer_costs': [1000, 1, 1, 1000],
         'pour_area_loss_pct': 0.01}


class RouteLayerPolicyNativeTest(PlaneFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.checkout = require_kicad_route()
        common = dict(clearance_mm=.25, min_width_mm=.2, thermal=dict(connection='thermal', gap_mm=.3,
                      spoke_width_mm=.3), priority=0, island_removal='always', net='GND', outline='board')
        self.plan = dict(schema=planes.SCHEMA, zones=[dict(common, layer='In1.Cu'), dict(common, layer='In2.Cu')])
        self.save()

    def route(self, y, policy, label, mode='pth'):
        rec = execute(self.source, self.root / f'prepare-{label}',
                      ['/usr/bin/python3', '-B', '-c', PREPARE, str(y), mode], timeout=120)
        self.assertEqual(rec['exit_code'], 0, (self.root / f'prepare-{label}/console.log').read_text()[-2000:])
        shutil.copyfile(self.root / f'prepare-{label}/pod.kicad_pcb', self.source / self.board)
        # Empty schematic: native parity stays enabled; its findings are baseline findings.
        (self.source / 'project/pod.kicad_sch').write_text(
            '(kicad_sch (version 20250114) (generator "eeschema") '
            '(uuid "d59616d7-b33b-41fe-bc77-f86de4d10183") (paper "A4") (lib_symbols))')
        spec = planes.step(planes.Path(planes.__file__).resolve().parents[1], board=self.board, plan=self.plan_path)
        plane = stage_receipts.run_step(self.root / 'plane-receipt', self.source, spec, {'test': label})
        self.assertEqual(plane['status'], 'completed', plane)
        stage_receipts.adopt(self.root / 'plane-receipt', plane, self.source)
        spec = routing_adapter.step(project='project', board='pod.kicad_pcb', schematic='pod.kicad_sch',
                                    name='layer-policy', label=label, checkout=self.checkout, budget=120,
                                    layer_policy=policy)
        receipt = stage_receipts.run_step(self.root / 'route-receipt', self.source, spec, {'test': label})
        work = self.root / 'route-receipt/run'
        summary = routing_adapter.verify(work, receipt=receipt)
        report = read_json(work / 'routing/adapter-report.json')
        return receipt, summary, report, work

    def facts_tracks(self, work):
        return read_json(work / 'routing/attempt/validate/candidate-facts.json')['tracks']

    def test_outer_allowlist_keeps_tracks_off_the_planes_and_grades_pass(self):
        receipt, summary, report, work = self.route(35, OUTER, 'outer')
        self.assertEqual(receipt['status'], 'completed', report['semantics'])
        self.assertEqual(summary['proposal'], 'eligible', report['semantics'])
        argv = report['process']['argv']
        self.assertIn('--backend-arg=--layers', argv)
        grade = summary['layer_policy']
        self.assertEqual(grade['status'], 'pass', grade)
        board = (work / 'routing/candidate/project/pod.kicad_pcb').read_text()
        root = benchmark.parse(board)
        layers = {benchmark.value(t, 'layer') for kind in ('segment', 'arc') for t in benchmark.children(root, kind)}
        self.assertTrue(layers, 'the router delivered no signal copper')
        self.assertLessEqual(layers, {'F.Cu', 'B.Cu'})
        self.assertTrue(self.facts_tracks(work))
        self.assertFalse([t for t in self.facts_tracks(work) if t['layer'] in ('In1.Cu', 'In2.Cu')])
        continuity = grade['items']['reference_continuity']
        self.assertEqual(continuity['tracks_measured'], 2 * len(self.facts_tracks(work)))
        area = grade['items']['area_loss']
        self.assertEqual({r['net_layer'] for r in area['layers']}, {'GND@In1.Cu', 'GND@In2.Cu'})
        measured = read_json(work / 'routing/attempt' / attempt.POLICY_FILE)
        self.assertEqual(measured['board_sha256'], summary['candidate_sha256'])

    def test_inner_allowance_cuts_the_plane_and_the_area_bound_gives_defect(self):
        receipt, summary, report, work = self.route(35, INNER, 'inner')
        self.assertEqual(summary['classification'], 'validation_failed', report['semantics'])
        self.assertEqual(summary['proposal'], 'none')
        self.assertNotEqual(receipt['status'], 'completed')
        inner = [t for t in self.facts_tracks(work) if t['layer'] in ('In1.Cu', 'In2.Cu')]
        self.assertTrue(inner, 'the control did not route on the inner planes')
        grade = summary['layer_policy']
        self.assertEqual(grade['status'], 'defect', grade)
        self.assertEqual(grade['items']['area_loss']['status'], 'defect')
        self.assertTrue({r['net_layer'] for r in grade['items']['area_loss']['exceeded']} &
                        {'GND@In1.Cu', 'GND@In2.Cu'})
        self.assertEqual(grade['items']['pour_integrity']['status'], 'defect')

    def test_route_over_a_plane_void_fails_reference_continuity(self):
        # y = 25 crosses the owned In1/In2 fill keepout at x 28..32.
        receipt, summary, report, work = self.route(25, OUTER, 'void')
        self.assertEqual(summary['classification'], 'policy_failed', report['semantics'])
        self.assertEqual(receipt['exit_code'], attempt.EXIT_POLICY)
        self.assertFalse((work / 'routing/candidate').exists())
        grade = summary['layer_policy']
        self.assertEqual(grade['items']['disallowed_tracks']['status'], 'pass', grade)
        continuity = grade['items']['reference_continuity']
        self.assertEqual(continuity['status'], 'defect', continuity)
        self.assertEqual({t['reference_layer'] for t in continuity['interrupted']}, {'In1.Cu', 'In2.Cu'})
        ids = {t['uuid'] for t in self.facts_tracks(work)}
        self.assertLessEqual({t['uuid'] for t in continuity['interrupted']}, ids)

    def test_rf_route_changing_side_through_a_via_cuts_the_reference(self):
        # SIGNAL runs from a pad on F.Cu to a pad on B.Cu: its via passes through both planes.
        receipt, summary, report, work = self.route(35, OUTER, 'layer-change', 'layer_change')
        self.assertEqual(summary['classification'], 'policy_failed', report['semantics'])
        self.assertEqual(receipt['exit_code'], attempt.EXIT_POLICY)
        self.assertFalse((work / 'routing/candidate').exists())
        facts = read_json(work / 'routing/attempt/validate/candidate-facts.json')
        rf_vias = {v['uuid'] for v in facts['vias'] if v['net'] == 'SIGNAL'}
        self.assertTrue(rf_vias, 'the router did not change side through a via')
        grade = summary['layer_policy']
        self.assertEqual(grade['items']['disallowed_tracks']['status'], 'pass', grade)
        continuity = grade['items']['reference_continuity']
        self.assertEqual(continuity['status'], 'defect', continuity)
        self.assertEqual({(v['uuid'], v['reference_layer']) for v in continuity['vias']},
                         {(u, layer) for u in rf_vias for layer in ('In1.Cu', 'In2.Cu')})
        self.assertEqual(continuity['scope'], attempt.CONTINUITY_SCOPE)

    def test_inner_copper_of_the_input_is_exempt_only_by_its_geometry(self):
        receipt, summary, report, work = self.route(35, OUTER, 'inner-track', 'inner_track')
        self.assertEqual(receipt['status'], 'completed', report['semantics'])
        self.assertEqual(summary['proposal'], 'eligible', report['semantics'])
        grade = summary['layer_policy']
        self.assertEqual(grade['status'], 'pass', grade)
        item = grade['items']['disallowed_tracks']
        self.assertEqual((item['tracks'], [(t['layer'], t['net']) for t in item['preexisting']]),
                         ([], [('In1.Cu', 'AUX')]))
        facts = read_json(work / 'routing/attempt/facts/reference-facts.json')
        snapshot = read_json(work / 'routing/attempt/snapshot.json')
        self.assertEqual(facts['sha256'], snapshot['files']['pod.kicad_pcb'])
        # The same input copper, recorded one micrometre longer: no longer the input's, a defect.
        facts['tracks'] = [dict(t, end=[t['end'][0] + 1000, t['end'][1]]) if t['layer'] == 'In1.Cu' else t
                           for t in facts['tracks']]
        path = work / 'routing/attempt/facts/reference-facts.json'
        path.write_text(json.dumps(facts))
        result = read_json(work / 'routing/attempt/result.json')
        regraded = attempt.layer_policy_grade(OUTER, work / 'routing/attempt', result, 'complete', 'pod.kicad_pcb')
        self.assertEqual(regraded['items']['disallowed_tracks']['status'], 'defect')
        self.assertEqual([t['net'] for t in regraded['items']['disallowed_tracks']['tracks']], ['AUX'])

    def test_declared_reference_plane_without_fill_is_never_a_pass(self):
        # Only In1.Cu is planned; the policy declares In1.Cu and In2.Cu as the reference.
        self.plan['zones'] = self.plan['zones'][:1]
        self.save()
        receipt, summary, report, work = self.route(35, OUTER, 'one-plane')
        self.assertEqual(summary['classification'], 'policy_failed', report['semantics'])
        self.assertFalse((work / 'routing/candidate').exists())
        items = summary['layer_policy']['items']
        self.assertEqual(items['area_loss']['status'], 'unknown', items['area_loss'])
        self.assertEqual(items['area_loss']['absent'], ['GND@In2.Cu'])
        continuity = items['reference_continuity']
        self.assertIn('no reference zone on In2.Cu', continuity['problems'])
        self.assertNotEqual(continuity['status'], 'pass')


class ReferenceMeasurementNativeTest(unittest.TestCase):
    """MEASURE_REFERENCE under KiCad on original boards: only the clearance the zone filler
    cut for the RF net's own pads is excused, and an RF via through the planes interrupts them."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-reference-measure-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'candidate').mkdir()
        (self.root / 'candidate/measure.py').write_text(attempt.MEASURE_REFERENCE)
        self.spec = attempt.canonical_json(attempt.reference_spec(OUTER))

    def measure(self, mode):
        rec = execute(self.root / 'candidate', self.root / mode,
                      ['/usr/bin/python3', '-B', '-c', MEASURE_BOARDS, mode, self.spec], timeout=300)
        self.assertEqual(rec['exit_code'], 0, (self.root / mode / 'console.log').read_text()[-2000:])
        measured = read_json(self.root / mode / 'measured.json')
        self.assertEqual(measured['problems'], [])
        self.assertEqual(measured['scope'], attempt.CONTINUITY_SCOPE)
        return measured

    def test_clean_launch_is_continuous_and_voids_near_the_pad_are_not_excused(self):
        clean = self.measure('clean')
        self.assertEqual([(t['reference_layer'], t['status']) for t in clean['tracks']],
                         [('In1.Cu', 'continuous'), ('In2.Cu', 'continuous')])
        self.assertTrue(all(t['uncovered_mm2'] <= attempt.CONTINUITY_TOLERANCE_MM2 for t in clean['tracks']))
        self.assertEqual(clean['vias'], [])
        # 45 um of extra void under the RF launch, inside the old padded ring: about 0.011 mm2.
        for mode in ('void_in_old_ring', 'void_beyond_old_ring', 'other_via'):
            with self.subTest(mode):
                measured = self.measure(mode)
                self.assertEqual({t['status'] for t in measured['tracks']}, {'interrupted'}, measured['tracks'])
                self.assertTrue(all(t['uncovered_mm2'] > 0.005 for t in measured['tracks']), measured['tracks'])
                grade = attempt.reference_grade(OUTER, measured, measured['board_sha256'], 'complete')
                self.assertEqual(grade['status'], 'defect')

    def test_rf_via_through_the_planes_interrupts_them(self):
        measured = self.measure('rf_via')
        self.assertEqual(sorted((v['reference_layer'], v['layers'][0], v['layers'][1]) for v in measured['vias']),
                         [('In1.Cu', 'F.Cu', 'B.Cu'), ('In2.Cu', 'F.Cu', 'B.Cu')])
        grade = attempt.reference_grade(OUTER, measured, measured['board_sha256'], 'complete')
        self.assertEqual((grade['status'], len(grade['vias'])), ('defect', 2), grade)


if __name__ == '__main__':
    unittest.main()
