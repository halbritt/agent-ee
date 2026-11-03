"""Original synthetic labels and geometry; no supplier designs or native host runs."""
import tempfile
import unittest
from pathlib import Path

from agent_ee import board_text
from agent_ee.design import DesignError, validate
from agent_ee.design_compile import board_markings, compile_design
from agent_ee.executor import execute
from agent_ee.storage import read_json
from tests.test_design_compile import design_fixture
from toolchain import require_toolchain


def label(**changes):
    return dict(id='pin-one', text='+', layer='F.SilkS',
                anchor=dict(reference='R1', pin='1', offset_mm=[3, 0]), **changes)


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.design = design_fixture(self.root / 'capture')

    def test_free_part_and_pin_labels_validate_and_compile(self):
        rows = [label(), dict(id='name', text='Owned divider', layer='B.SilkS', anchor=dict(at_mm=[15, 4])),
                dict(id='part', text='INPUT', layer='F.SilkS', anchor=dict(reference='R2', offset_mm=[0, 4]))]
        self.design['markings'] = rows
        validate(self.design, self.root / 'capture')
        plan_path = compile_design(self.design, self.root / 'capture', self.root / 'bundle')
        plan = read_json(plan_path)
        self.assertEqual(plan['board_text'], 'board-text.json')
        emitted = read_json(self.root / 'bundle/inputs/board-text.json')
        self.assertEqual(emitted['items'], rows)
        self.assertEqual(emitted['origin_mm'], [100, 100])
        from agent_ee.requirement_map import input_inventory
        limits, sources = board_text.profile_limits(self.root / 'bundle/inputs', emitted,
                                                   input_inventory(self.root / 'bundle/inputs'))
        self.assertEqual(limits['min_silk_text_height_mm'], 1)
        self.assertIn('fab-sources/board-text-facts.txt', sources)
        mapped = read_json(self.root / 'bundle/inputs/requirement-map.json')
        self.assertTrue(any(c['kind'] == 'board_text' for r in mapped['requirements'] for c in r.get('checks', [])))

    def test_bad_markings_are_field_named_refusals(self):
        mutations = [
            lambda r: r.update(text=' '), lambda r: r.update(text='bad\nline'),
            lambda r: r.update(text='${REFERENCE}'), lambda r: r.update(layer='F.Fab'),
            lambda r: r.update(height_mm=.8), lambda r: r.update(stroke_mm=.12),
            lambda r: r.update(rotation_deg=45), lambda r: r['anchor'].update(reference='MISSING'),
            lambda r: r['anchor'].update(pin='404'), lambda r: r['anchor'].update(offset_mm=[True, 0]),
            lambda r: r['anchor'].update(offset_mm=[float('nan'), 0]),
            lambda r: r['anchor'].update(offset_mm=[.00000001, 0]),
            lambda r: r.update(anchor=dict(at_mm=[31, 4])), lambda r: r.update(unknown=1),
        ]
        for mutate in mutations:
            row = label()
            mutate(row)
            self.design['markings'] = [row]
            with self.subTest(row=row), self.assertRaisesRegex(DesignError, 'markings'):
                validate(self.design, self.root / 'capture')
        self.design['markings'] = [label(), label()]
        with self.assertRaisesRegex(DesignError, 'unique'):
            validate(self.design)

    def test_profile_source_tampering_refuses(self):
        self.design['markings'] = [label()]
        (self.root / 'capture/fab-sources').mkdir()
        board_markings(self.design, self.root / 'capture')
        from agent_ee.requirement_map import input_inventory
        doc = board_text.load_document(self.root / 'capture/board-text.json')
        source = self.root / 'capture/fab-sources/board-text-facts.txt'
        source.write_text('Unverified limits')
        with self.assertRaisesRegex(ValueError, 'sourced'):
            board_text.profile_limits(self.root / 'capture', doc, input_inventory(self.root / 'capture'))

    def test_pin_label_refuses_repeated_physical_lands(self):
        from tests.test_design_compile import seal_capture
        path = self.root / 'capture/cad/Owned.pretty/R2.kicad_mod'
        from agent_ee.symbol_library import parse_sexpr, children
        from agent_ee.scaffold import sexpr
        node = parse_sexpr(path.read_bytes())
        import copy
        node.append(copy.deepcopy(children(node, 'pad')[0]))
        path.write_bytes(sexpr(node))
        seal_capture(self.root / 'capture')
        self.design['markings'] = [label()]
        with self.assertRaisesRegex(DesignError, 'exactly one physical pad'):
            validate(self.design, self.root / 'capture')


SETUP = '''
import pcbnew as k
from agent_ee.board_text import emit, evaluate, default_limits
b=k.BOARD()
for a,z in [((0,0),(40,0)),((40,0),(40,30)),((40,30),(0,30)),((0,30),(0,0))]:
    s=k.PCB_SHAPE(b,k.SHAPE_T_SEGMENT);s.SetLayer(k.Edge_Cuts)
    s.SetStart(k.VECTOR2I(*(k.FromMM(v) for v in a)));s.SetEnd(k.VECTOR2I(*(k.FromMM(v) for v in z)))
    s.SetWidth(50000);b.Add(s)
fp=k.FOOTPRINT(b);fp.SetReference('J1');fp.SetPosition(k.VECTOR2I(10000000,15000000))
fp.Reference().SetVisible(False);fp.Value().SetVisible(False);b.Add(fp)
p=k.PAD(fp);p.SetNumber('1');p.SetAttribute(k.PAD_ATTRIB_SMD);p.SetShape(k.PAD_SHAPE_RECT)
p.SetSize(k.VECTOR2I(1000000,1000000));p.SetPosition(k.VECTOR2I(12000000,16000000))
ls=k.LSET();ls.AddLayer(k.F_Cu);ls.AddLayer(k.F_Mask);p.SetLayerSet(ls);fp.Add(p)
doc={'schema':'agent-ee.board-text/1','namespace':'synthetic','origin_mm':[0,0],
     'fab_profile':'unused-in-this-native-unit', 'items':[
     {'id':'pin','text':'+','layer':'F.SilkS','anchor':{'reference':'J1','pin':'1','offset_mm':[3,0]}},
     {'id':'name','text':'TEST','layer':'B.SilkS','anchor':{'at_mm':[25,5]}}]}
limits=default_limits()
emit(b,doc,limits)
'''


class NativeTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()

    def native(self, script):
        work = self.root / 'work'
        result = execute(self.inputs, work, ['/usr/bin/python3', '-B', '-c', SETUP + script])
        self.assertEqual((result['execution'], result['exit_code']), ('ok', 0),
                         (work / 'console.log').read_text())
        return work

    def test_pin_label_survives_placement_rotation_flip_and_native_save(self):
        self.native('''
assert evaluate(b,doc,limits)['outcome']=='pass', evaluate(b,doc,limits)
assert k.SaveBoard('/work/before.kicad_pcb',b)
fp.SetPosition(k.VECTOR2I(18000000,18000000));fp.SetOrientationDegrees(90)
assert k.SaveBoard('/work/placed.kicad_pcb',b)
from agent_ee.placement_adapter import write_scope
from pathlib import Path
proof=write_scope(Path('/work/before.kicad_pcb').read_bytes(),Path('/work/placed.kicad_pcb').read_bytes())
assert not proof['violations'], proof
fp.Flip(fp.GetPosition(),k.FLIP_DIRECTION_TOP_BOTTOM)
assert k.SaveBoard('/work/final.kicad_pcb',b)
b=k.LoadBoard('/work/final.kicad_pcb')
report=evaluate(b,doc,limits)
assert report['outcome']=='pass', report
pin=report['records'][0]
assert pin['layer']=='B.Silkscreen', pin
assert pin['position_nm'] != [15000000,16000000], pin
assert len(list(b.GetDrawings()))==5
''')

    def test_final_saved_board_check_catches_text_over_a_pad(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
p.SetPosition(text.GetPosition())
assert k.SaveBoard('/work/over-pad.kicad_pcb',b)
report=evaluate(k.LoadBoard('/work/over-pad.kicad_pcb'),doc,limits)
assert report['outcome']=='defect', report
assert 'silk_overlap_or_pad_clearance' in report['records'][0]['violations'], report
''')

    def test_native_shapes_allow_hollow_silk_frames_and_apply_pad_floor_only_to_pads(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
at=text.GetPosition()
frame=k.PCB_SHAPE(b,k.SHAPE_T_RECT);frame.SetLayer(k.F_SilkS)
frame.SetStart(k.VECTOR2I(at.x-2000000,at.y-2000000));frame.SetEnd(k.VECTOR2I(at.x+2000000,at.y+2000000))
frame.SetWidth(150000);b.Add(frame)
report=evaluate(b,doc,limits)
assert report['outcome']=='pass', report
q=k.PAD(fp);q.SetNumber('2');q.SetAttribute(k.PAD_ATTRIB_SMD);q.SetShape(k.PAD_SHAPE_RECT)
q.SetSize(k.VECTOR2I(1000000,1000000));q.SetLayerSet(ls)
q.SetPosition(k.VECTOR2I(at.x+2000000,at.y));fp.Add(q)
assert evaluate(b,doc,limits)['outcome']=='pass'
larger=dict(limits,min_pad_to_silk_mm=2)
report=evaluate(b,doc,larger)
assert 'silk_overlap_or_pad_clearance' in report['records'][0]['violations'], report
''')

    def test_compiled_text_is_emitted_by_the_receipted_build_step(self):
        import shutil
        design = design_fixture(self.root / 'capture')
        design['markings'] = [label(), dict(id='name', text='OWNED', layer='B.SilkS', anchor=dict(at_mm=[20, 5]))]
        compile_design(design, self.root / 'capture', self.root / 'bundle')
        shutil.copytree(self.root / 'bundle/inputs', self.inputs, dirs_exist_ok=True)
        work = self.native('''
for item in list(b.GetDrawings()):
    if item.GetClass()=='PCB_TEXT': b.Remove(item)
for item in list(fp.GraphicalItems()):
    if item.GetClass()=='PCB_TEXT': fp.Remove(item)
fp.SetReference('R1')
shift=k.VECTOR2I(100000000,100000000)
for edge in b.GetDrawings(): edge.Move(shift)
fp.Move(shift)
assert k.SaveBoard('/work/source.kicad_pcb',b)
''')
        shutil.copyfile(work / 'source.kicad_pcb', self.inputs / 'source.kicad_pcb')
        from agent_ee.build_stages import _step
        declaration = board_text.step(Path(__file__).resolve().parents[1], 'source.kicad_pcb', 'board-text.json')
        receipt_path, receipt = _step(self.root / 'stage', self.inputs, declaration, 0)
        self.assertEqual(receipt['status'], 'completed')
        saved = (self.inputs / 'source.kicad_pcb').read_bytes()
        from agent_ee.symbol_library import parse_sexpr, children
        board = parse_sexpr(saved)
        free = children(board, 'gr_text')
        self.assertEqual([(n[1], children(n, 'layer')[0][1]) for n in free], [('OWNED', 'B.SilkS')])
        owned = children(children(board, 'footprint')[0], 'fp_text')
        self.assertEqual([(n[2], children(n, 'layer')[0][1]) for n in owned], [('+', 'F.SilkS')])
        checked = self.root / 'checked'
        result = execute(self.inputs, checked, ['/usr/bin/python3', '-B', '-m', 'agent_ee.board_text', 'check',
            '--board', '/candidate/source.kicad_pcb', '--declaration', '/candidate/board-text.json',
            '--inputs', '/candidate', '--out', '/work/board-text.json'])
        self.assertEqual((result['execution'], result['exit_code']), ('ok', 0), (checked / 'console.log').read_text())
        self.assertEqual(read_json(checked / 'board-text.json')['outcome'], 'pass')

    def test_final_check_rejects_overlap_outside_edge_small_text_and_changed_pose(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
other=k.PCB_TEXT(b);other.SetText('+');other.SetLayer(k.F_SilkS)
other.SetPosition(text.GetPosition());other.SetTextSize(k.VECTOR2I(1000000,1000000));b.Add(other)
assert 'silk_overlap_or_pad_clearance' in evaluate(b,doc,limits)['records'][0]['violations']
b.Remove(other)
text.SetTextSize(k.VECTOR2I(500000,500000));text.SetTextThickness(50000)
assert 'text_below_fabrication_minimum' in evaluate(b,doc,limits)['records'][0]['violations']
text.SetPosition(k.VECTOR2I(0,0))
violations=evaluate(b,doc,limits)['records'][0]['violations']
assert 'text_outside_board' in violations and 'declared_pose_changed' in violations, violations
''')
