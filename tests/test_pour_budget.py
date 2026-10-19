"""Pour budget (AGENTEE-257) on an original four-layer probe; not a telemetry-pod design."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agent_ee import add_planes as planes, pour_budget as budget, routing_adapter, stage_receipts
from agent_ee.executor import execute
from agent_ee.kicad_route_attempt import validate_layer_policy
from agent_ee.storage import read_json, write_json
from tests.test_add_planes import PlaneFixture
from tests.test_route_layer_policy import PREPARE
from tests.toolchain import require_toolchain, require_kicad_route

# J1..J3 are GND, A1/A2 one Default net, B1/B2 one net of a class with a larger via.
BUILD = r'''
import json, sys
import pcbnew as k
from pathlib import Path
out=Path('/work/project');out.mkdir()
b=k.BOARD();b.SetCopperLayerCount(4)
e=k.PCB_SHAPE(b);e.SetShape(k.SHAPE_T_RECT);e.SetLayer(k.Edge_Cuts)
e.SetStart(k.VECTOR2I(k.FromMM(10),k.FromMM(10)));e.SetEnd(k.VECTOR2I(k.FromMM(50),k.FromMM(40)))
e.SetWidth(k.FromMM(.1));b.Add(e)
nets={}
for ref,name,x,y in json.loads(sys.argv[1]):
    if name not in nets:
        nets[name]=k.NETINFO_ITEM(b,name);b.Add(nets[name])
    fp=k.FOOTPRINT(b);fp.SetReference(ref);fp.SetValue(name);fp.SetFPIDAsString('Owned:Probe')
    fp.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));b.Add(fp)
    for field in fp.GetFields():field.SetVisible(False)
    pad=k.PAD(fp);pad.SetNumber('1');pad.SetNet(nets[name]);pad.SetAttribute(k.PAD_ATTRIB_PTH)
    pad.SetShape(k.PAD_SHAPE_CIRCLE);pad.SetSize(k.VECTOR2I(k.FromMM(1.6),k.FromMM(1.6)))
    pad.SetDrillSize(k.VECTOR2I(k.FromMM(.8),k.FromMM(.8)))
    layers=k.LSET(k.LSET.AllCuMask());layers.AddLayer(k.F_Mask);layers.AddLayer(k.B_Mask);pad.SetLayerSet(layers)
    pad.SetPosition(fp.GetPosition());fp.Add(pad)
assert k.SaveBoard(str(out/'probe.kicad_pcb'),b)
'''
PADS = [('J1', 'GND', 14, 14), ('J2', 'GND', 46, 36), ('J3', 'GND', 14, 36),
        ('A1', 'SIG_A', 20, 20), ('A2', 'SIG_A', 40, 20), ('B1', 'SIG_B', 20, 30), ('B2', 'SIG_B', 40, 30)]
CLASS = dict(bus_width=12, clearance=0.2, diff_pair_gap=0.25, diff_pair_via_gap=0.25, diff_pair_width=0.2,
             line_style=0, microvia_diameter=0.3, microvia_drill=0.1, pcb_color='rgba(0, 0, 0, 0.000)',
             priority=2147483647, schematic_color='rgba(0, 0, 0, 0.000)', track_width=0.2, wire_width=6)
PROJECT = {'board': {'design_settings': {'rules': {'min_clearance': 0.09}}},
           'net_settings': {'classes': [dict(CLASS, name='Default', via_diameter=0.25, via_drill=0.15),
                                        dict(CLASS, name='WIDE', priority=0, via_diameter=0.6, via_drill=0.3)],
                            'meta': {'version': 4}, 'net_colors': None,
                            'netclass_assignments': None,
                            'netclass_patterns': [{'netclass': 'WIDE', 'pattern': 'SIG_B'}]},
           'meta': {'filename': 'probe.kicad_pro', 'version': 3}}
PLANE = dict(net='GND', outline='board', clearance_mm=.2, min_width_mm=.2,
             thermal=dict(connection='tht_thermal', gap_mm=.3, spoke_width_mm=.4), priority=0, island_removal='always')


def build(root, pads, name):
    empty = root/f'{name}-empty'
    empty.mkdir()
    rec = execute(empty, root/f'{name}-fixture', ['/usr/bin/python3', '-B', '-c', BUILD, json.dumps(pads)], timeout=120)
    if rec['exit_code'] != 0:
        raise AssertionError((root/f'{name}-fixture/console.log').read_text()[-2000:])
    source = root/f'{name}-candidate'
    shutil.copytree(root/f'{name}-fixture/project', source/'project')
    write_json(source/'project/probe.kicad_pro', PROJECT)
    write_json(source/'planes.json', dict(schema=planes.SCHEMA, zones=[dict(PLANE, layer='In1.Cu'),
                                                                       dict(PLANE, layer='In2.Cu')]))
    report = planes.generate(source, 'project/probe.kicad_pcb', 'planes.json', root/f'{name}-planes')
    if report['execution'] != 'ok':
        raise AssertionError(report)
    shutil.copyfile(root/f'{name}-planes/board.kicad_pcb', source/'project/probe.kicad_pcb')
    return source


class PourBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-pour-budget-test-')
        cls.root = Path(cls.tmp.name)
        cls.source = build(cls.root, PADS, 'placed')
        cls.report = budget.measure(cls.source, 'project/probe.kicad_pcb', cls.root/'measure')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_measures_both_planes_and_every_routed_class(self):
        r = self.report
        self.assertEqual(r['execution'], 'ok', r.get('reason'))
        self.assertEqual(r['outside_outline'], [])
        self.assertEqual(sorted(r['pour_area_mm2']), ['GND@In1.Cu', 'GND@In2.Cu'])
        self.assertEqual([(c['netclass'], c['net'], c['via_diameter_mm'], c['via_drill_mm']) for c in r['net_classes']],
                         [('Default', 'SIG_A', 0.25, 0.15), ('WIDE', 'SIG_B', 0.6, 0.3)])
        self.assertEqual(len(r['probes']), 4)
        for probe in r['probes']:
            self.assertEqual(probe['status'], 'measured', probe)
            self.assertEqual(probe['flashed'], ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
            # One whole circular antipad: via radius plus the 0.2 mm plane clearance (and
            # KiCad's 0.0005 mm extra fill clearance), to within the arc approximation.
            radius = probe['via_diameter_mm'] / 2 + 0.2 + 0.0005
            self.assertAlmostEqual(probe['loss_mm2'][probe['key']], 3.141592653589793 * radius ** 2, delta=0.01)
            other = next(k for k in r['pour_area_mm2'] if k != probe['key'])
            self.assertGreaterEqual(probe['loss_mm2'][other], 0)
        # A GND via joins the GND pours: it removes no fill.
        self.assertEqual([(p['key'], p['net'], p['status']) for p in r['own_net_probes']],
                         [('GND@In1.Cu', 'GND', 'measured'), ('GND@In2.Cu', 'GND', 'measured')])
        for probe in r['own_net_probes']:
            self.assertLessEqual(abs(probe['loss_mm2'][probe['key']]), budget.OWN_NET_TOLERANCE_MM2)

    def test_denominator_is_kicad_routes_own_pour_area(self):
        checkout = require_kicad_route()
        script = Path(checkout)/'src/kicad_route/kicad/pour.py'
        out = self.root/'krt-pour.json'
        rec = subprocess.run(['/usr/bin/python3', '-B', str(script), str(self.root/'measure/native/base/probe.kicad_pcb'),
                              str(out)], capture_output=True, text=True, timeout=600, cwd=self.root)
        self.assertEqual(rec.returncode, 0, rec.stderr[-2000:])
        krt = read_json(out)
        self.assertEqual({k: v['area_mm2'] for k, v in krt['layers'].items()}, self.report['pour_area_mm2'])

    def test_derives_the_bound_from_the_worst_class_and_pour(self):
        d = budget.derive(self.report, 40)
        worst = max(p['loss_mm2'][p['key']] / self.report['pour_area_mm2'][p['key']] for p in self.report['probes'])
        self.assertGreaterEqual(d['pour_area_loss_pct'], 100 * 40 * worst)
        self.assertLess(d['pour_area_loss_pct'] - 100 * 40 * worst, 0.001)
        self.assertEqual({p['worst_netclass'] for p in d['pours'].values()}, {'WIDE'})
        self.assertEqual(d['board_sha256'], self.report['board_sha256'])
        self.assertIn('not impedance', d['scope'])
        validate_layer_policy({'signal_layers': ['F.Cu', 'B.Cu'], 'pour_area_loss_pct': d['pour_area_loss_pct']})
        self.assertGreater(budget.derive(self.report, 41)['pour_area_loss_pct'], d['pour_area_loss_pct'])

    def test_refuses_without_a_declared_budget_or_full_evidence(self):
        for bad in (0, -1, True, 2.5, budget.MAX_VIA_BUDGET + 1):
            with self.assertRaisesRegex(ValueError, 'via budget'):
                budget.derive(self.report, bad)
        unknown = copy.deepcopy(self.report)
        unknown['probes'][1].update(status='unknown', reason='no pour interior wide enough for a whole antipad')
        with self.assertRaisesRegex(ValueError, 'per-via loss unknown'):
            budget.derive(unknown, 10)
        missing = copy.deepcopy(self.report)
        missing['probes'].pop()
        with self.assertRaisesRegex(ValueError, 'probes missing'):
            budget.derive(missing, 10)
        nothing = copy.deepcopy(self.report)
        nothing['probes'][0]['loss_mm2'][nothing['probes'][0]['key']] = 0.0
        with self.assertRaisesRegex(ValueError, 'removed no fill'):
            budget.derive(nothing, 10)
        own = copy.deepcopy(self.report)
        own['own_net_probes'][0]['loss_mm2'][own['own_net_probes'][0]['key']] = 0.3
        with self.assertRaisesRegex(ValueError, "own net removes fill"):
            budget.derive(own, 10)
        own['own_net_probes'][0].update(status='unknown', reason='x')
        with self.assertRaisesRegex(ValueError, "own net is unknown"):
            budget.derive(own, 10)
        failed = dict(self.report, execution='failed')
        with self.assertRaisesRegex(ValueError, 'successful'):
            budget.derive(failed, 10)
        with self.assertRaisesRegex(ValueError, 'more than the whole pour'):
            budget.derive(self.report, budget.MAX_VIA_BUDGET)

    def test_cli_derive(self):
        env = dict(os.environ, PYTHONPATH=str(Path(budget.__file__).parents[1]))
        run = subprocess.run(['python3', '-m', 'agent_ee.pour_budget', 'derive', '--report',
                              str(self.root/'measure/pour-budget.json'), '--via-budget', '40'],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), budget.derive(self.report, 40))

    def test_unplaced_board_is_not_a_baseline(self):
        source = build(self.root, PADS + [('C1', 'SIG_A', 70, 20)], 'staged')
        report = budget.measure(source, 'project/probe.kicad_pcb', self.root/'staged-measure')
        self.assertEqual(report['execution'], 'ok', report.get('reason'))
        self.assertEqual(report['outside_outline'], ['C1'])
        with self.assertRaisesRegex(ValueError, 'not a placed board: .*C1'):
            budget.derive(report, 10)

    def test_measurement_refuses_reused_output_and_custom_via_rules(self):
        with self.assertRaisesRegex(ValueError, 'must be new'):
            budget.measure(self.source, 'project/probe.kicad_pcb', self.root/'measure')
        ruled = self.root/'ruled'
        shutil.copytree(self.source, ruled)
        (ruled/'project/probe.kicad_dru').write_text(
            '(version 1)\n(rule big (condition "A.NetName == \'SIG_A\'") (constraint via_diameter (min 0.8mm)))\n')
        report = budget.measure(ruled, 'project/probe.kicad_pcb', self.root/'ruled-measure')
        self.assertEqual(report['execution'], 'failed')
        self.assertIn('custom via size rules', report['reason'])


class PourBudgetRoutingTest(PlaneFixture, unittest.TestCase):
    """The derived bound against the pinned router's own area_loss on a routed board.

    SIGNAL runs from a surface pad on F.Cu to one on B.Cu (test_route_layer_policy's
    layer_change probe), so an F.Cu/B.Cu route needs one signal through-via and
    nothing else touches the GND planes on In1/In2."""

    def setUp(self):
        super().setUp()
        self.checkout = require_kicad_route()
        common = dict(PLANE, clearance_mm=.25, thermal=dict(connection='thermal', gap_mm=.3, spoke_width_mm=.3))
        self.plan = dict(schema=planes.SCHEMA, zones=[dict(common, layer='In1.Cu'), dict(common, layer='In2.Cu')])
        self.save()
        rec = execute(self.source, self.root/'prepare', ['/usr/bin/python3', '-B', '-c', PREPARE, '35', 'layer_change'],
                      timeout=120)
        self.assertEqual(rec['exit_code'], 0, (self.root/'prepare/console.log').read_text()[-2000:])
        shutil.copyfile(self.root/'prepare/pod.kicad_pcb', self.source/self.board)
        (self.source/'project/pod.kicad_sch').write_text(
            '(kicad_sch (version 20250114) (generator "eeschema") '
            '(uuid "d59616d7-b33b-41fe-bc77-f86de4d10183") (paper "A4") (lib_symbols))')
        spec = planes.step(Path(planes.__file__).resolve().parents[1], board=self.board, plan=self.plan_path)
        plane = stage_receipts.run_step(self.root/'plane-receipt', self.source, spec, {'test': 'pour-budget'})
        self.assertEqual(plane['status'], 'completed', plane)
        stage_receipts.adopt(self.root/'plane-receipt', plane, self.source)
        # The routing input as it stands: placed, planed, unrouted.
        self.measured = budget.measure(self.source, self.board, self.root/'budget')
        self.assertEqual(self.measured['execution'], 'ok', self.measured.get('reason'))

    def route(self, bound, label):
        policy = {'signal_layers': ['F.Cu', 'B.Cu'], 'pour_area_loss_pct': bound}
        spec = routing_adapter.step(project='project', board='pod.kicad_pcb', schematic='pod.kicad_sch',
                                    name='pour-budget', label=label, checkout=self.checkout, budget=120,
                                    layer_policy=policy)
        receipt = stage_receipts.run_step(self.root/f'route-{label}', self.source, spec, {'test': label})
        work = self.root/f'route-{label}/run'
        summary = routing_adapter.verify(work, receipt=receipt)
        delta = read_json(work/'routing/attempt/validate/pour-integrity.json')
        facts = read_json(work/'routing/attempt/validate/candidate-facts.json')
        return receipt, summary, delta, facts

    def test_one_via_budget_admits_the_routed_antipad_and_a_zero_bound_does_not(self):
        derived = budget.derive(self.measured, 1)
        receipt, summary, delta, facts = self.route(derived['pour_area_loss_pct'], 'one-via')
        self.assertEqual(receipt['status'], 'completed', summary)
        self.assertEqual([v['net'] for v in facts['vias']], ['SIGNAL'])
        self.assertEqual(summary['layer_policy']['status'], 'pass', summary['layer_policy'])
        rows = {r['net_layer']: r for r in delta['layers']}
        self.assertEqual(set(rows), set(self.measured['pour_area_mm2']))
        for key, row in rows.items():
            # The router's denominator is the measured baseline, and its loss is one antipad.
            self.assertEqual(row['area_mm2'][0], self.measured['pour_area_mm2'][key])
            loss = row['area_mm2'][0] - row['area_mm2'][1]
            self.assertGreater(loss, 0)
            self.assertLessEqual(-row['area_delta_pct'], derived['pour_area_loss_pct'])
            self.assertAlmostEqual(loss, derived['pours'][key]['via_loss_mm2']['Default'], delta=0.02)
        receipt, summary, delta, facts = self.route(0, 'zero')
        self.assertEqual([v['net'] for v in facts['vias']], ['SIGNAL'])
        self.assertNotEqual(receipt['status'], 'completed')
        self.assertEqual(summary['layer_policy']['items']['area_loss']['status'], 'defect')


if __name__ == '__main__':
    unittest.main()
