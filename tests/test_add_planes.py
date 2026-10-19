"""Native zone generation on an original probe; not a telemetry-pod design."""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import add_planes as planes, benchmark, stage_receipts
from agent_ee import routing_adapter
from agent_ee.executor import execute
from agent_ee.storage import digest, read_json, write_json
from tests.toolchain import require_toolchain, require_kicad_route

BUILD = r'''
import pcbnew as k
from pathlib import Path
out=Path('/work/project');out.mkdir()
b=k.BOARD();b.SetCopperLayerCount(4)
e=k.PCB_SHAPE(b);e.SetShape(k.SHAPE_T_RECT);e.SetLayer(k.Edge_Cuts)
e.SetStart(k.VECTOR2I(k.FromMM(10),k.FromMM(10)));e.SetEnd(k.VECTOR2I(k.FromMM(50),k.FromMM(50)))
e.SetWidth(k.FromMM(.1));b.Add(e)
for i,(name,x,y) in enumerate([('GND',20,20),('+3V3',20,40),('+5V',40,40)]):
    net=k.NETINFO_ITEM(b,name);b.Add(net)
    fp=k.FOOTPRINT(b);fp.SetReference('J'+str(i+1));fp.SetValue(name);fp.SetFPIDAsString('Owned:Probe')
    fp.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));b.Add(fp)
    for field in fp.GetFields():field.SetVisible(False)
    pad=k.PAD(fp);pad.SetNumber('1');pad.SetNet(net);pad.SetAttribute(k.PAD_ATTRIB_PTH)
    pad.SetShape(k.PAD_SHAPE_CIRCLE);pad.SetSize(k.VECTOR2I(k.FromMM(2),k.FromMM(2)))
    pad.SetDrillSize(k.VECTOR2I(k.FromMM(.8),k.FromMM(.8)))
    layers=k.LSET(k.LSET.AllCuMask());layers.AddLayer(k.F_Mask);layers.AddLayer(k.B_Mask);pad.SetLayerSet(layers)
    pad.SetPosition(fp.GetPosition());fp.Add(pad)
owner=k.FOOTPRINT(b);owner.SetReference('ANT1');owner.SetValue('Owned keepout probe — Ω');b.Add(owner)
owner.SetPosition(k.VECTOR2I(k.FromMM(30),k.FromMM(25)))
for field in owner.GetFields():field.SetVisible(False)
z=k.ZONE(owner);z.SetIsRuleArea(True);z.SetDoNotAllowZoneFills(True)
layers=k.LSET();layers.AddLayer(k.In1_Cu);layers.AddLayer(k.In2_Cu);z.SetLayerSet(layers)
z.Outline().NewOutline()
for x,y in [(28,20),(32,20),(32,30),(28,30)]:z.Outline().Append(k.FromMM(x),k.FromMM(y))
owner.Add(z)
assert k.SaveBoard(str(out/'pod.kicad_pcb'),b)
(out/'pod.kicad_pro').write_text('{}\n');(out/'pod.kicad_prl').write_text('{"owned":"sibling sentinel"}\n')
'''


class PlaneFixture:
    def setUp(self):
        require_toolchain(native=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='agent-ee-planes-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        empty=self.root/'empty';empty.mkdir()
        rec=execute(empty,self.root/'fixture',['/usr/bin/python3','-B','-c',BUILD],timeout=120)
        self.assertEqual(rec['exit_code'],0,(self.root/'fixture/console.log').read_text()[-2000:])
        self.source=self.root/'candidate';shutil.copytree(self.root/'fixture/project',self.source/'project')
        common=dict(clearance_mm=.25,min_width_mm=.2,thermal=dict(connection='thermal',gap_mm=.3,spoke_width_mm=.3),priority=0,island_removal='always')
        self.plan=dict(schema=planes.SCHEMA,zones=[
            dict(common,net='GND',layer='In1.Cu',outline='board'),
            dict(common,net='+3V3',layer='In2.Cu',outline={'polygon_mm':[[10,10],[30,10],[30,50],[10,50]]}),
            dict(common,net='+5V',layer='In2.Cu',outline={'polygon_mm':[[30.5,10],[50,10],[50,50],[30.5,50]]})])
        self.board='project/pod.kicad_pcb'
        self.plan_path='planes.json'
        self.save()

    def save(self):
        write_json(self.source/self.plan_path,self.plan)

    def run_plan(self,name='run'):
        return planes.generate(self.source,self.board,self.plan_path,self.root/name)

    def assert_ok(self,report):
        self.assertEqual(report['execution'],'ok',report)

    def bad_fill(self, text):
        zone = next(z for z in benchmark.children(benchmark.parse(text), 'zone')
                    if benchmark.uid(z) == planes.zone_ids(self.plan)[0])
        fill = benchmark.children(zone, 'filled_polygon')[0]
        bad = '(filled_polygon (layer "In1.Cu") (pts (xy 11 11) (xy 49 11) (xy 49 49) (xy 11 49)))'
        return benchmark.replace_spans(text, [(fill.start, fill.end, bad)])


class AddPlanesTests(PlaneFixture, unittest.TestCase):
    def test_plan_applied(self):
        before=stage_receipts.file_tree(self.source)
        report=self.run_plan();self.assert_ok(report)
        self.assertEqual(stage_receipts.file_tree(self.source),before)
        output=(self.root/'run/board.kicad_pcb').read_text()
        node=benchmark.parse(output);ids=planes.zone_ids(self.plan)
        stripped=benchmark.replace_spans(output,[(z.start,z.end,'') for z in benchmark.children(node,'zone') if benchmark.uid(z) in ids])
        self.assertEqual(stripped.encode(),(self.source/self.board).read_bytes())
        self.assertEqual({(z['net'],z['layer']) for z in report['zones']},{('GND','In1.Cu'),('+3V3','In2.Cu'),('+5V','In2.Cu')})
        self.assertTrue(all(z['filled_area_mm2']>0 for z in report['zones']))
        self.assertEqual(report['drc']['planned_zone_errors'],[])
        for suffix in planes.SIBLINGS:
            original=(self.source/self.board).with_suffix(suffix)
            restored=self.root/'run/native/native-project'/Path(self.board).with_suffix(suffix).name
            self.assertEqual(restored.read_bytes() if restored.exists() else None,original.read_bytes() if original.exists() else None)

    def test_same_plan_reapplies_without_unplanned_content_change(self):
        first=self.run_plan();self.assert_ok(first)
        shutil.copyfile(self.root/'run/board.kicad_pcb',self.source/self.board)
        second=self.run_plan('again');self.assert_ok(second)
        self.assertEqual(first['zones'],second['zones'])
        self.assertEqual(first['board_after'],second['board_after'])

    def test_plan_net_missing_refused(self):
        self.plan['zones'][0]['net']='MISSING';self.save()
        report=self.run_plan()
        self.assertEqual(report['outcome'],'unknown')
        self.assertIn('net is missing',str(report['findings']))
        self.assertFalse((self.root/'run/board.kicad_pcb').exists())

    def test_unplanned_zone_refused(self):
        self.assert_ok(self.run_plan())
        shutil.copyfile(self.root/'run/board.kicad_pcb',self.source/self.board)
        self.plan['zones'][0]['priority']=1;self.save()
        report=self.run_plan('unplanned')
        self.assertEqual(report['execution'],'failed')
        self.assertIn('unplanned copper zone',str(report['findings']))
        self.assertFalse((self.root/'unplanned/board.kicad_pcb').exists())

    def test_keepout_removes_actual_fill_and_empty_plane_refuses(self):
        self.plan['zones']=[dict(self.plan['zones'][0],outline={'polygon_mm':[[28.5,21],[31.5,21],[31.5,29],[28.5,29]]},island_removal='never')]
        self.save();report=self.run_plan()
        self.assertEqual(report['execution'],'failed')
        self.assertIn('no native filled copper',str(report['findings']))
        self.assertFalse((self.root/'run/board.kicad_pcb').exists())

    def test_wholly_unconnected_zone_refuses_always_but_preserves_never(self):
        before = stage_receipts.file_tree(self.source)
        for policy in ('always', 'never'):
            with self.subTest(policy=policy):
                self.plan['zones'] = [dict(self.plan['zones'][0], island_removal=policy,
                    outline={'polygon_mm': [[35,11],[49,11],[49,30],[35,30]]})]
                self.save()
                name = 'isolated-' + policy
                report = self.run_plan(name)
                uid = planes.zone_ids(self.plan)[0]
                # The producer's inspect child reloads the saved fill in a fresh process.
                facts = read_json(self.root/name/'verify/facts.json')
                self.assertEqual(facts['zones'][0]['uuid'], uid)
                self.assertGreater(facts['zones'][0]['filled_area_mm2'], 0)
                native = read_json(self.root/name/'drc/report.json')
                isolated = [v for v in native['violations'] if v['type']=='isolated_copper'
                            and any(i['uuid']==uid for i in v['items'])]
                self.assertTrue(isolated, native)
                self.assertTrue(all(v['severity']=='warning' for v in isolated))
                if policy == 'always':
                    self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'), report)
                    self.assertEqual(report['drc']['planned_zone_errors'], isolated)
                    self.assertIn(uid, str(report['findings']))
                    self.assertFalse((self.root/name/'board.kicad_pcb').exists())
                else:
                    self.assert_ok(report)
                    self.assertEqual(report['drc']['planned_zone_errors'], [])
                    self.assertTrue((self.root/name/'board.kicad_pcb').is_file())
        # Only the explicitly rewritten plan changes in the captured inputs.
        after = stage_receipts.file_tree(self.source)
        self.assertEqual({p for p in before[0] if before[0][p]!=after[0][p]}, {self.plan_path})

    def test_split_zone_removes_secondary_island_only_under_always(self):
        prepare = r"""
import pcbnew as k
b=k.LoadBoard('/candidate/project/pod.kicad_pcb')
z=k.ZONE(b);z.SetIsRuleArea(True);z.SetDoNotAllowZoneFills(True);z.SetLayer(k.In1_Cu)
z.Outline().NewOutline()
for x,y in [(29,9),(31,9),(31,51),(29,51)]:z.Outline().Append(k.FromMM(x),k.FromMM(y))
b.Add(z)
assert k.SaveBoard('/work/pod.kicad_pcb',b)
"""
        rec = execute(self.source, self.root/'split', ['/usr/bin/python3','-B','-c',prepare], timeout=120)
        self.assertEqual(rec['exit_code'], 0, (self.root/'split/console.log').read_text()[-2000:])
        shutil.copyfile(self.root/'split/pod.kicad_pcb', self.source/self.board)
        fills = {}
        for policy, polygons in [('always', 1), ('never', 2)]:
            with self.subTest(policy=policy):
                self.plan['zones'] = [dict(self.plan['zones'][0], island_removal=policy)]
                self.save()
                before = stage_receipts.file_tree(self.source)
                report = self.run_plan('split-' + policy)
                self.assert_ok(report)
                self.assertEqual(stage_receipts.file_tree(self.source), before)
                self.assertEqual(report['drc']['planned_zone_errors'], [])
                fills[policy] = report['zones'][0]
                self.assertEqual(fills[policy]['fill_polygons'], polygons)
        self.assertGreater(fills['never']['filled_area_mm2'], fills['always']['filled_area_mm2'])

    def test_drc_execution_failure_never_publishes_board(self):
        original=planes.execute
        def broken(inputs,work,argv,**kw):
            if argv[0]=='/usr/bin/kicad-cli':argv=['/usr/bin/false']
            return original(inputs,work,argv,**kw)
        with patch.object(planes,'execute',side_effect=broken):
            report=self.run_plan()
        self.assertEqual(report['execution'],'failed')
        self.assertIn('DRC execution failed',str(report['findings']))
        self.assertFalse((self.root/'run/board.kicad_pcb').exists())

    def test_native_drc_rejects_copper_in_keepout_and_foreign_pads(self):
        original = planes.splice_zones
        with patch.object(planes, 'splice_zones', side_effect=lambda *args:self.bad_fill(original(*args))):
            report = self.run_plan()
        self.assertEqual(report['execution'], 'failed', report)
        self.assertIn('native DRC found an error', str(report['findings']))
        self.assertTrue(report['drc']['planned_zone_errors'])
        self.assertFalse((self.root/'run/board.kicad_pcb').exists())
        native = read_json(self.root/'run/drc/report.json')
        for field, bad in [('severity', None), ('severity', []), ('severity', 'unknown'),
                           ('type', ''), ('type', []), ('type', None), ('items', None), ('items', []),
                           ('items', [None]), ('items', [{}]), ('items', [{'uuid':None}]),
                           ('items', [{'uuid':'not-a-uuid'}])]:
            with self.subTest(field=field, bad=bad):
                malformed = copy.deepcopy(native)
                error = next(v for v in malformed['violations'] if v['severity']=='error')
                error[field] = bad
                with self.assertRaises(ValueError):
                    planes._planned_zone_errors(malformed, planes.zone_ids(self.plan))
        for severities in (None, ['warning','exclusion'], ['error', [], 'exclusion']):
            malformed = dict(native, included_severities=severities)
            with self.assertRaises(ValueError):
                planes._planned_zone_errors(malformed, planes.zone_ids(self.plan))

    def test_missing_native_error_metadata_never_publishes_board(self):
        splice, execute_native = planes.splice_zones, planes.execute
        for missing in ('severity', 'uuid'):
            with self.subTest(missing=missing):
                native_errors = []
                def corrupt_report(inputs, work, argv, **kwargs):
                    result = execute_native(inputs, work, argv, **kwargs)
                    if argv[0] == '/usr/bin/kicad-cli':
                        path = work/'report.json'; data = read_json(path)
                        for violation in data['violations']:
                            if violation['severity']=='error' and any(i['uuid'] in planes.zone_ids(self.plan) for i in violation['items']):
                                native_errors.append(copy.deepcopy(violation))
                                if missing=='severity':
                                    del violation['severity']
                                else:
                                    for item in violation['items']:
                                        del item['uuid']
                        write_json(path, data)
                    return result
                with patch.object(planes, 'splice_zones', side_effect=lambda *args:self.bad_fill(splice(*args))), \
                     patch.object(planes, 'execute', side_effect=corrupt_report):
                    report = self.run_plan('missing-'+missing)
                self.assertTrue(native_errors, 'the probe must first produce actual native planned-zone errors')
                self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'), report)
                self.assertIn('malformed', str(report['findings']))
                self.assertFalse((self.root/('missing-'+missing)/'board.kicad_pcb').exists())

    def test_declared_step_receipt_adopts_only_board(self):
        spec=planes.step(Path(planes.__file__).resolve().parents[1],board=self.board,plan=self.plan_path)
        before=stage_receipts.file_tree(self.source)[0]
        receipt=stage_receipts.run_step(self.root/'receipt',self.source,spec,{'test':'planes'})
        self.assertEqual(receipt['status'],'completed',(self.root/'receipt/run/console.log').read_text()[-3000:])
        stage_receipts.adopt(self.root/'receipt',receipt,self.source)
        after=stage_receipts.file_tree(self.source)[0]
        self.assertEqual({p for p in before if before[p]!=after[p]},{self.board})
        stage_receipts.validate_receipt(self.root/'receipt')
        report=read_json(self.root/'receipt/run/out/planes.json')
        self.assertEqual(report['plan_sha256'],digest(self.source/self.plan_path))

    def test_malformed_plan_refuses_before_output(self):
        for mutate in (lambda p:p['zones'][0].update(clearance_mm=float('nan')),
                       lambda p:p['zones'][0].update(priority=True),
                       lambda p:p['zones'][0].update(layer='F.SilkS'),
                       lambda p:p['zones'][0].update(layer=[]),
                       lambda p:p['zones'][0].update(island_removal='guess')):
            plan=copy.deepcopy(self.plan);mutate(plan)
            with self.assertRaises(ValueError):planes.validate_plan(plan)


class PlannedZoneClassificationTests(unittest.TestCase):
    def test_isolated_copper_is_policy_and_zone_specific_at_every_severity(self):
        always = '10000000-0000-0000-0000-000000000001'
        never = '10000000-0000-0000-0000-000000000002'
        unrelated = '10000000-0000-0000-0000-000000000003'
        for severity in ('warning', 'exclusion', 'error'):
            for uid in (always, never, unrelated):
                for kind in ('isolated_copper', 'unconnected_items'):
                    with self.subTest(severity=severity, uid=uid, kind=kind):
                        violation = dict(type=kind, severity=severity, items=[dict(uuid=uid)])
                        native = dict(included_severities=['error','warning','exclusion'], violations=[violation])
                        expected = ([violation] if (severity=='error' and uid in (always,never)) or
                                    (kind=='isolated_copper' and uid==always) else [])
                        self.assertEqual(planes._planned_zone_errors(native, [always,never], {always}), expected)

    def test_isolated_copper_unknown_attribution_refuses_with_always_policy(self):
        uid = '10000000-0000-0000-0000-000000000001'
        for items in ([], [{}], [dict(uuid='invalid')]):
            with self.subTest(items=items):
                native = dict(included_severities=['error','warning','exclusion'],
                    violations=[dict(type='isolated_copper', severity='warning', items=items)])
                with self.assertRaises(ValueError):
                    planes._planned_zone_errors(native, [uid], {uid})


class AddPlanesRoutingTests(PlaneFixture, unittest.TestCase):
    """One actual routing attempt; no assertion of schematic or pod acceptance."""
    def test_route_preserves_three_native_planes(self):
        checkout = require_kicad_route()
        prepare = r'''
import pcbnew as k
from pathlib import Path
b=k.LoadBoard('/candidate/project/pod.kicad_pcb')
net=k.NETINFO_ITEM(b,'SIGNAL');b.Add(net)
for ref,x in [('TP1',15),('TP2',45)]:
    fp=k.FOOTPRINT(b);fp.SetReference(ref);fp.SetValue('Signal probe');b.Add(fp)
    fp.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(35)))
    for field in fp.GetFields():field.SetVisible(False)
    p=k.PAD(fp);p.SetNumber('1');p.SetNet(net);p.SetAttribute(k.PAD_ATTRIB_SMD)
    p.SetShape(k.PAD_SHAPE_CIRCLE);p.SetSize(k.VECTOR2I(k.FromMM(2),k.FromMM(2)))
    layers=k.LSET();layers.AddLayer(k.F_Cu);p.SetLayerSet(layers)
    p.SetPosition(fp.GetPosition());fp.Add(p)
assert k.SaveBoard('/work/pod.kicad_pcb',b)
'''
        rec = execute(self.source, self.root/'prepare', ['/usr/bin/python3', '-B', '-c', prepare], timeout=120)
        self.assertEqual(rec['exit_code'], 0, (self.root/'prepare/console.log').read_text()[-2000:])
        shutil.copyfile(self.root/'prepare/pod.kicad_pcb', self.source/self.board)
        # The explicit empty schematic keeps native parity evaluation enabled. Its
        # existing findings are baseline findings; this probe claims only routing
        # and plane preservation, never schematic parity or electrical acceptance.
        (self.source/'project/pod.kicad_sch').write_text(
            '(kicad_sch (version 20250114) (generator "eeschema") '
            '(uuid "d59616d7-b33b-41fe-bc77-f86de4d10183") (paper "A4") (lib_symbols))')
        plane_spec = planes.step(Path(planes.__file__).resolve().parents[1], board=self.board, plan=self.plan_path)
        plane_receipt = stage_receipts.run_step(self.root/'plane-receipt', self.source, plane_spec, {'test':'planes-route'})
        self.assertEqual(plane_receipt['status'], 'completed', plane_receipt)
        stage_receipts.adopt(self.root/'plane-receipt', plane_receipt, self.source)
        route_spec = routing_adapter.step(project='project', board='pod.kicad_pcb', schematic='pod.kicad_sch',
                                         name='plane-probe', label='planes-native', checkout=checkout, budget=120)
        route_receipt = stage_receipts.run_step(self.root/'route-receipt', self.source, route_spec, {'test':'planes-route'})
        self.assert_routed(plane_receipt, route_receipt, self.root/'route-receipt/run')

    def assert_routed(self, plane_receipt, route_receipt, work):
        self.assertEqual(route_receipt['inputs'], stage_receipts.after_tree(plane_receipt))
        self.assertEqual(route_receipt['status'], 'completed', route_receipt)
        summary = routing_adapter.verify(work, receipt=route_receipt)
        self.assertEqual(summary['proposal'], 'eligible')
        attempt = work/'routing/attempt'
        record = read_json(attempt/'attempt.json')
        self.assertEqual(record['settings']['protect'], 'existing')
        result = read_json(attempt/'result.json')
        self.assertEqual(result['validation']['status'], 'PASS')
        self.assertEqual(result['routing']['status'], 'COMPLETE')
        board = (work/'routing/candidate/project/pod.kicad_pcb').read_text()
        self.assertGreater(len(benchmark.children(benchmark.parse(board), 'segment')), 0)
        checks = read_json(attempt/'validate/preservation.json')
        pour = next(c for c in checks if c['check']=='pour_integrity')
        self.assertEqual(pour['status'], 'PASS', pour)


if __name__=='__main__':
    unittest.main()
