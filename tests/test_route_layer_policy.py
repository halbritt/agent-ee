"""AGENTEE-231 declared layer policy on an original four-layer probe with GND planes on In1/In2.

Actual routing with the pinned kicad-route/KRT: an F.Cu/B.Cu allowlist keeps
tracks off the inner reference planes; a control that prices the outer layers
out of reach routes on the planes and the declared area-loss bound fails it; a
route over a plane void fails the declared RF reference continuity.
"""
import shutil
import unittest

from agent_ee import add_planes as planes, benchmark, stage_receipts
from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from agent_ee.storage import read_json
from tests.test_add_planes import PlaneFixture
from tests.toolchain import require_kicad_route

PREPARE = r'''
import sys
import pcbnew as k
y = float(sys.argv[1])
b = k.LoadBoard('/candidate/project/pod.kicad_pcb')
net = k.NETINFO_ITEM(b, 'SIGNAL'); b.Add(net)
for ref, x in [('TP1', 15), ('TP2', 45)]:
    fp = k.FOOTPRINT(b); fp.SetReference(ref); fp.SetValue('Signal probe'); b.Add(fp)
    fp.SetPosition(k.VECTOR2I(k.FromMM(x), k.FromMM(y)))
    for field in fp.GetFields(): field.SetVisible(False)
    p = k.PAD(fp); p.SetNumber('1'); p.SetNet(net); p.SetAttribute(k.PAD_ATTRIB_PTH)
    p.SetShape(k.PAD_SHAPE_CIRCLE); p.SetSize(k.VECTOR2I(k.FromMM(1.6), k.FromMM(1.6)))
    p.SetDrillSize(k.VECTOR2I(k.FromMM(.8), k.FromMM(.8)))
    layers = k.LSET(k.LSET.AllCuMask()); layers.AddLayer(k.F_Mask); layers.AddLayer(k.B_Mask); p.SetLayerSet(layers)
    p.SetPosition(fp.GetPosition()); fp.Add(p)
assert k.SaveBoard('/work/pod.kicad_pcb', b)
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

    def route(self, y, policy, label):
        rec = execute(self.source, self.root / f'prepare-{label}', ['/usr/bin/python3', '-B', '-c', PREPARE, str(y)],
                      timeout=120)
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


if __name__ == '__main__':
    unittest.main()
