"""Native qualification of AGENTEE-276's producer policy on original geometry."""
from collections import Counter
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_ee.executor import execute
from agent_ee.storage import read_json
from toolchain import require_toolchain


class SilkscreenTests(unittest.TestCase):
    def test_assembly_orientation_front_back_clearance_reservation_and_refusal(self):
        import shutil
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'fixtures/board-envelope/rc.envelope.json',
                        self.inputs / 'envelope.json')
        self.native('''
import pcbnew as k
from agent_ee.board_envelope import validate
from agent_ee.silkscreen import overhang_orientation_mark, reserve_imu_markings
from pathlib import Path
record=validate(Path('/candidate/envelope.json').read_bytes())
b=k.BOARD()
fp=k.FOOTPRINT(b);fp.SetReference('J1');fp.SetPosition(k.VECTOR2I(96000000,100000000));b.Add(fp)
mark=overhang_orientation_mark(b,fp,record,'90');b.Add(mark)
assert mark.GetText()=='ASM J1 90deg' and mark.GetLayer()==k.F_SilkS
box=mark.GetBoundingBox()
assert box.GetLeft()>=96500000 and box.GetTop()>=96500000
assert box.GetRight()<=115500000 and box.GetBottom()<=107500000
fp.Flip(fp.GetPosition(),k.FLIP_DIRECTION_TOP_BOTTOM)
back=overhang_orientation_mark(b,fp,record,'270');b.Add(back)
assert back.GetLayer()==k.B_SilkS and back.IsMirrored()
policy={'obstacles':[]}
obstacles=reserve_imu_markings(b,policy)['obstacles']
assert policy=={'obstacles':[]} and {o['side'] for o in obstacles}=={'F','B'}
assert all(o['source'].startswith('ASM/') for o in obstacles)
assert k.SaveBoard('/work/assembly.kicad_pcb',b)
saved=k.LoadBoard('/work/assembly.kicad_pcb')
assert {t.GetText() for t in saved.GetDrawings()}=={'ASM J1 90deg','ASM J1 270deg'}
# A pad covering the complete board must refuse rather than publish a covered mark.
pad=k.PAD(fp);pad.SetSize(k.VECTOR2I(100000000,100000000));pad.SetPosition(k.VECTOR2I(100000000,100000000))
ls=k.LSET();ls.AddLayer(k.B_Cu);pad.SetLayerSet(ls);fp.Add(pad)
try:
    overhang_orientation_mark(b,fp,record,'270')
except ValueError as exc:
    assert 'no in-board assembly orientation mark fits for J1' in str(exc)
else:
    raise AssertionError('covered mark accepted')
''')

    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee276-')))
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()

    def native(self, script):
        work = self.root / 'native'
        result = execute(self.inputs, work, ['/usr/bin/python3', '-B', '-c', script])
        self.assertEqual((result['execution'], result['exit_code']), ('ok', 0),
                         (work / 'console.log').read_text())
        return work

    def test_dense_passive_exact_identity_and_overhang_layers(self):
        self.native('''
import pcbnew as k
from agent_ee.silkscreen import prepare_footprint
b=k.BOARD()
for lib, name in [('Resistor_SMD','R_0402_1005Metric'), ('Capacitor_SMD','C_0603_1608Metric'),
                  ('Inductor_SMD','L_0603_1608Metric')]:
    for silk, fab in [(k.F_SilkS,k.F_Fab),(k.B_SilkS,k.B_Fab)]:
        fp=k.FOOTPRINT(b); fp.SetFPID(k.LIB_ID(lib,name)); fp.SetReference('R1')
        ref=fp.Reference(); ref.SetLayer(silk); ref.SetPosition(k.VECTOR2I(0,-1160000))
        uid=ref.m_Uuid.AsString(); at=ref.GetPosition()
        prepare_footprint(fp)
        assert ref.GetLayer()==fab and ref.GetPosition()==at and ref.m_Uuid.AsString()==uid
        prepare_footprint(fp)
        assert ref.GetLayer()==fab
for name in ['R_0805_2012Metric','R_0402_1005Metric_Pad1.00x0.70mm_HandSolder','Other']:
    fp=k.FOOTPRINT(b); fp.SetFPID(k.LIB_ID('Resistor_SMD',name)); fp.Reference().SetLayer(k.F_SilkS)
    prepare_footprint(fp)
    assert fp.Reference().GetLayer()==k.F_SilkS
for lib,name in [('Resistor_SMD','R_1206_3216Metric'),('Capacitor_SMD','C_1210_3225Metric'),
                 ('Package_TO_SOT_SMD','SOT-323_SC-70'),('TestPoint','TestPoint_Pad_D1.0mm'),
                 ('Package_SO','SOIC-8_3.9x4.9mm_P1.27mm')]:
    fp=k.FOOTPRINT(b);fp.SetFPID(k.LIB_ID(lib,name));fp.Reference().SetLayer(k.F_SilkS)
    prepare_footprint(fp)
    assert fp.Reference().GetLayer()==k.F_SilkS
fp=k.FOOTPRINT(b); fp.Reference().SetLayer(k.B_SilkS)
shape=k.PCB_SHAPE(fp,k.SHAPE_T_SEGMENT);shape.SetLayer(k.F_SilkS);fp.Add(shape)
pad=k.PAD(fp);pad.SetSize(k.VECTOR2I(1000000,1000000));fp.Add(pad)
before=pad.GetSize()
prepare_footprint(fp,overhang=True)
assert shape.GetLayer()==k.F_Fab and fp.Reference().GetLayer()==k.B_Fab
assert pad.GetSize()==before
''')

    def test_imu_reservation_blocks_same_side_and_keeps_policy(self):
        work = self.native('''
import pcbnew as k
from agent_ee.silkscreen import reserve_imu_markings
from agent_ee.storage import write_json
b=k.BOARD()
for i, (label,layer) in enumerate([('IMU_AXES X forward Y left Z up',k.F_SilkS),
                                  ('IMU_AXES back',k.B_SilkS),('unrelated',k.F_SilkS)]):
    t=k.PCB_TEXT(b);t.SetText(label);t.SetLayer(layer);t.SetPosition(k.VECTOR2I(10000000,10000000))
    t.SetTextSize(k.VECTOR2I(800000,800000));t.SetTextThickness(120000);b.Add(t)
for invalid in (None, {}, {'obstacles':None}):
    assert reserve_imu_markings(b,invalid) is invalid
policy={'obstacles':[]}
a=reserve_imu_markings(b,policy)
assert policy=={'obstacles':[]}
assert a==reserve_imu_markings(b,policy) and len(a['obstacles'])==2
assert {o['side'] for o in a['obstacles']}=={'F','B'}
write_json('/work/reserved.json',a)
''')
        from test_placement import capture, part
        from test_placer import request
        from agent_ee.placer import pack
        obstacles = read_json(work / 'reserved.json')['obstacles']
        front = next(o for o in obstacles if o['side'] == 'F')
        points = front['polygons'][0]
        self.assertLess(points[0][0], 10_000_000)
        self.assertGreater(points[2][0], 10_000_000)
        board = capture([part('C48', 10, 10)])
        self.assertEqual(pack(board, request(fixed_refs=['C48'], obstacles=[front]))['status'], 'fixed_conflict')
        self.assertEqual(pack(board, request(fixed_refs=['C48'], obstacles=[{**front, 'side': 'B'}]))['status'], 'planned')
        planned = pack(board, request(fixed_refs=[], obstacles=[front]))
        self.assertEqual(planned['status'], 'planned', planned)
        self.assertNotEqual(planned['placements'][0]['position_nm'], [10_000_000, 10_000_000])

    def test_dense_reference_and_edge_silk_native_drc_before_after(self):
        work = self.native('''
import pcbnew as k
from agent_ee.silkscreen import prepare_footprint
b=k.BOARD()
for a,z in [((0,0),(20,0)),((20,0),(20,20)),((20,20),(0,20)),((0,20),(0,0))]:
    s=k.PCB_SHAPE(b,k.SHAPE_T_SEGMENT);s.SetLayer(k.Edge_Cuts)
    s.SetStart(k.VECTOR2I(*(k.FromMM(v) for v in a)));s.SetEnd(k.VECTOR2I(*(k.FromMM(v) for v in z)))
    s.SetWidth(50000);b.Add(s)
fp=k.FOOTPRINT(b);fp.SetFPID(k.LIB_ID('Resistor_SMD','R_0402_1005Metric'));fp.SetReference('R1')
fp.Reference().SetLayer(k.F_SilkS);fp.Reference().SetPosition(k.VECTOR2I(10000000,10000000))
p=k.PAD(fp);p.SetAttribute(k.PAD_ATTRIB_SMD);p.SetSize(k.VECTOR2I(2000000,2000000));p.SetPosition(k.VECTOR2I(10000000,10000000))
ls=k.LSET();ls.AddLayer(k.F_Cu);ls.AddLayer(k.F_Mask);p.SetLayerSet(ls);fp.Add(p);b.Add(fp)
edge=k.FOOTPRINT(b);edge.SetReference('J1');edge.Reference().SetLayer(k.F_SilkS)
edge.Reference().SetPosition(k.VECTOR2I(20000000,10000000));b.Add(edge)
assert k.SaveBoard('/work/before.kicad_pcb',b)
prepare_footprint(fp);prepare_footprint(edge,overhang=True)
assert k.SaveBoard('/work/after.kicad_pcb',b)
''')
        reports = {}
        for stage in ('before', 'after'):
            path = work / (stage + '.kicad_pcb')
            report = work / (stage + '.json')
            result = subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                                     '-o', str(report), str(path)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            reports[stage] = Counter(v['type'] for v in read_json(report)['violations'])
        silk = {'silk_over_copper', 'silk_edge_clearance'}
        self.assertTrue(silk <= set(reports['before']), reports)
        self.assertFalse(silk & set(reports['after']), reports)
        self.assertEqual({k: v for k, v in reports['before'].items() if k not in silk}, reports['after'])

    def test_non_dense_references_remain_ordinary_native_drc_findings(self):
        work = self.native('''
import pcbnew as k
from agent_ee.silkscreen import prepare_footprint
b=k.BOARD()
for index,(lib,name) in enumerate([('Resistor_SMD','R_1206_3216Metric'),
        ('Capacitor_SMD','C_1210_3225Metric'),('Package_TO_SOT_SMD','SOT-323_SC-70'),
        ('TestPoint','TestPoint_Pad_D1.0mm'),('Package_SO','SOIC-8_3.9x4.9mm_P1.27mm')]):
    fp=k.FOOTPRINT(b);fp.SetFPID(k.LIB_ID(lib,name));fp.SetReference('X'+str(index+1))
    pos=k.VECTOR2I(10000000+index*10000000,10000000)
    fp.Reference().SetLayer(k.F_SilkS);fp.Reference().SetPosition(pos)
    pad=k.PAD(fp);pad.SetAttribute(k.PAD_ATTRIB_SMD);pad.SetSize(k.VECTOR2I(4000000,4000000))
    layers=k.LSET();layers.AddLayer(k.F_Cu);layers.AddLayer(k.F_Mask);pad.SetLayerSet(layers)
    pad.SetPosition(pos);fp.Add(pad);b.Add(fp)
assert k.SaveBoard('/work/before.kicad_pcb',b)
for fp in b.GetFootprints():
    prepare_footprint(fp)
assert k.SaveBoard('/work/after.kicad_pcb',b)
''')
        findings = []
        for stage in ('before', 'after'):
            report = work / (stage + '.json')
            subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                            '-o', str(report), str(work / (stage + '.kicad_pcb'))],
                           check=True, capture_output=True, timeout=120)
            findings.append(Counter(v['type'] for v in read_json(report)['violations']))
        self.assertGreaterEqual(findings[0]['silk_over_copper'], 5)
        self.assertEqual(findings[0], findings[1])
