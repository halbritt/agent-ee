"""Native post-routing plane obligations; synthetic fixture only."""
import os
from pathlib import Path
import shutil
import unittest
from types import SimpleNamespace

from agent_ee import final_planes
from agent_ee.executor import execute
from agent_ee.storage import read_json, write_json, digest
from tests.test_add_planes import PlaneFixture


class FinalPlanesTests(PlaneFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plan['zones'] = [dict(self.plan['zones'][0],
            outline={'polygon_mm': [[35,11],[49,11],[49,30],[35,30]]})]
        self.save()
        self.pre = self.run_plan('pre', phase='pre-routing')
        self.assert_ok(self.pre)
        self.assertTrue(self.pre['deferred_connectivity'])
        shutil.copyfile(self.root/'pre/board.kicad_pcb', self.source/self.board)

    def mutate(self, operation):
        script = r'''
import pcbnew as k
from pathlib import Path
b=k.LoadBoard('/candidate/project/pod.kicad_pcb')
z=next(z for z in b.Zones() if not z.GetIsRuleArea())
op=OPERATION
if op == 'connect':
    n=b.FindNet('GND')
    t=k.PCB_TRACK(b);t.SetNet(n);t.SetLayer(k.F_Cu);t.SetWidth(k.FromMM(.5))
    t.SetStart(k.VECTOR2I(k.FromMM(20),k.FromMM(20)))
    t.SetEnd(k.VECTOR2I(k.FromMM(40),k.FromMM(20)));b.Add(t)
    v=k.PCB_VIA(b);v.SetNet(n);v.SetPosition(t.GetEnd());v.SetWidth(k.FromMM(.8))
    v.SetDrill(k.FromMM(.4));v.SetLayerPair(k.F_Cu,k.B_Cu);b.Add(v)
    b.BuildConnectivity();assert k.ZONE_FILLER(b).Fill(b.Zones())
elif op == 'empty':
    z.UnFill()
elif op == 'wrong-net':
    z.SetNet(b.FindNet('+3V3'))
elif op == 'missing':
    b.Remove(z)
elif op == 'never':
    z.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_NEVER)
assert k.SaveBoard('/work/changed.kicad_pcb',b)
'''.replace('OPERATION', repr(operation))
        rec = execute(self.source, self.root/operation, ['/usr/bin/python3','-B','-c',script], timeout=120)
        self.assertEqual(rec['exit_code'],0,(self.root/operation/'console.log').read_text())
        shutil.copyfile(self.root/operation/'changed.kicad_pcb',self.source/self.board)

    def grade(self, name):
        result = final_planes.validate(self.source,self.board,self.plan_path,self.root/name)
        evidence = os.environ.get('FINAL_PLAN_EVIDENCE')
        if evidence:
            target=Path(evidence)/self._testMethodName
            shutil.copytree(self.root,target,dirs_exist_ok=True)
        return result

    def test_deferred_then_connected_passes_production_consumer(self):
        self.assertEqual(self.grade('before')['status'],'FAIL')
        self.mutate('connect')
        result=self.grade('after')
        self.assertEqual(result['status'],'PASS',result)
        self.assertGreater(result['facts']['zones'][0]['fill_polygons'],0)
        # The native consumer must also run for a bound non-default filename.
        renamed = 'ground-plan.data'
        (self.source/self.plan_path).rename(self.source/renamed)
        self.plan_path = renamed
        bindings = bind_fixture(self.source, self.plan_path, self.pre)
        files = {p.relative_to(self.source).as_posix(): digest(p) for p in self.source.rglob('*') if p.is_file()}
        context=SimpleNamespace(manifest={'files':files}, inputs=self.source, params=bindings,
            scope={'board':self.board}, work=self.root/'final-drc', result={'details':{}},
            remaining=lambda:180, evidence=lambda *paths:None)
        context.work.mkdir()
        final_planes.check_captured(context,read_json(self.root/'after/drc.json'))
        self.assertEqual(context.result['details']['final_planes'][0]['status'],'PASS')
        if os.environ.get('FINAL_PLAN_EVIDENCE'):
            shutil.copytree(self.root,Path(os.environ['FINAL_PLAN_EVIDENCE'])/self._testMethodName,dirs_exist_ok=True)

    def test_unconnected_fails(self):
        result=self.grade('final')
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('isolated copper',str(result))

    def test_empty_saved_fill_fails(self):
        self.mutate('empty')
        result=self.grade('final')
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('no native filled copper',str(result))

    def test_wrong_net_fails(self):
        self.mutate('wrong-net')
        result=self.grade('final')
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('unplanned copper zone',str(result))

    def test_missing_zone_fails(self):
        self.mutate('missing')
        result=self.grade('final')
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('missing after save',str(result))

    def test_policy_change_fails(self):
        self.mutate('never')
        result=self.grade('final')
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('settings or outline differ',str(result))

    def test_native_gnss_coverage_and_stale_pose(self):
        self.mutate('connect')
        script = r'''
import json
from pathlib import Path
import pcbnew as k
from agent_ee.plane_reference import BOUND_NETS, check_reference, _polygons
from agent_ee.storage import digest
b=k.LoadBoard('/candidate/project/pod.kicad_pcb')
ground=b.FindNet('GND');ground.SetNetname('/GND')
for fp in b.GetFootprints():fp.SetReference('FIXTURE_'+fp.GetReference())
for name in ['/POD_GNSS_RF','/ANT_BIAS']:
    b.Add(k.NETINFO_ITEM(b,name))
fps={};bindings=[];rf=[]
for i,((ref,num),net) in enumerate(BOUND_NETS.items()):
    if ref not in fps:
        fp=k.FOOTPRINT(b);fp.SetReference(ref);fp.SetFPIDAsString('Owned:'+ref)
        fp.SetPosition(k.VECTOR2I(k.FromMM(40),k.FromMM(15)));b.Add(fp);fps[ref]=fp
    fp=fps[ref];p=k.PAD(fp);p.SetNumber(num);p.SetNet(ground if net=='/GND' else b.FindNet(net))
    p.SetAttribute(k.PAD_ATTRIB_SMD);p.SetShape(k.PAD_SHAPE_CIRCLE)
    p.SetSize(k.VECTOR2I(k.FromMM(.3),k.FromMM(.3)));layers=k.LSET();layers.AddLayer(k.F_Cu);p.SetLayerSet(layers)
    p.SetPosition(k.VECTOR2I(k.FromMM(36+i*.5),k.FromMM(15)));fp.Add(p)
    if net=='/POD_GNSS_RF':rf.append(p)
    xy=lambda v:[v.x/1e6,v.y/1e6]
    bindings.append(dict(reference=ref,number=num,fp_uuid=fp.m_Uuid.AsString(),lib_id=fp.GetFPID().GetUniStringLibId(),
      pose_mm_deg=[*xy(fp.GetPosition()),fp.GetOrientationDegrees()],pad=dict(uuid=p.m_Uuid.AsString(),net=net,
      at_mm=xy(p.GetPosition()),size_mm=xy(p.GetSize()),shape=int(p.GetShape()),orientation_deg=p.GetOrientationDegrees(),
      drill_mm=xy(p.GetDrillSize()),attribute=int(p.GetAttribute()),copper_polygons={b.GetLayerName(l):_polygons(p.GetEffectivePolygon(l)) for l in [k.F_Cu,k.In1_Cu,k.In2_Cu,k.B_Cu] if p.IsOnLayer(l)},layers=[b.GetLayerName(l) for l in p.GetLayerSet().Seq()])))
for p,q in zip(rf,rf[1:]):
    t=k.PCB_TRACK(b);t.SetNet(b.FindNet('/POD_GNSS_RF'));t.SetLayer(k.F_Cu);t.SetWidth(k.FromMM(.3))
    t.SetStart(p.GetPosition());t.SetEnd(q.GetPosition());b.Add(t)
board_path=Path('/work/rf.kicad_pcb')
assert k.SaveBoard(str(board_path),b)
board_path.with_suffix('.kicad_pro').write_bytes(Path('/candidate/project/pod.kicad_pro').read_bytes())
b=k.LoadBoard(str(board_path))
fps={f.GetReference():f for f in b.GetFootprints()}
edges=[dict(start_mm=xy(e.GetStart()),end_mm=xy(e.GetEnd()),shape=int(e.GetShape())) for e in b.GetDrawings() if e.GetLayer()==k.Edge_Cuts]
d=dict(schema='agentee-480.gnss-reference/1',ground_net='/GND',ground_reference_layer='In1.Cu',
 binding=dict(project_sha256=digest(board_path.with_suffix('.kicad_pro')),edges=edges,copper_layers=4,thickness_mm=b.GetDesignSettings().GetBoardThickness()/1e6,pads=bindings),
 reference_exclusion=dict(type='Polygon',coordinates=[[[35.5,14],[42,14],[42,16],[35.5,16],[35.5,14]]]))
p=Path('/work/gnss-corridor.json');p.write_text(json.dumps(d))
assert check_reference(b,Path('/work'),board_path)['status']=='PASS'
# A corridor extending outside saved fill must fail, even with correct RF tracks.
d['reference_exclusion']['coordinates'][0][0]=[34,14]
d['reference_exclusion']['coordinates'][0][-1]=[34,14]
p.write_text(json.dumps(d))
try:check_reference(b,Path('/work'),board_path)
except ValueError as e:assert 'no single saved' in str(e),str(e)
else:raise AssertionError('uncovered corridor accepted')
# Retaining a mask after moving its bound component is not evidence.
fps['U3'].SetPosition(k.VECTOR2I(k.FromMM(41),k.FromMM(15)))
try:check_reference(b,Path('/work'),board_path)
except ValueError as e:assert 'stale GNSS' in str(e),str(e)
else:raise AssertionError('stale pose accepted')
print('native GNSS: covered PASS; uncovered FAIL; stale pose FAIL')
'''
        rec=execute(self.source,self.root/'gnss',['/usr/bin/python3','-B','-c',script],timeout=120)
        self.assertEqual(rec['exit_code'],0,(self.root/'gnss/console.log').read_text())
        if os.environ.get('FINAL_PLAN_EVIDENCE'):
            shutil.copytree(self.root,Path(os.environ['FINAL_PLAN_EVIDENCE'])/self._testMethodName,dirs_exist_ok=True)


def bind_fixture(root, plan_path, producer):
    plan_link = {'path': plan_path, 'sha256': digest(root/plan_path)}
    write_json(root/'producer.json', producer)
    write_json(root/'composition.json', {'schema': 'pod-capture.scope-composition/4', 'planes': [plan_link]})
    return {'plane_obligations': [dict(plan=plan_link,
        producer={'path': 'producer.json', 'sha256': digest(root/'producer.json')},
        composition={'path': 'composition.json', 'sha256': digest(root/'composition.json')})]}
