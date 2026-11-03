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
    row = dict(id='pin-one', text='+', layer='F.SilkS',
               anchor=dict(reference='R1', pin='1', offset_mm=[3, 0]))
    row.update(changes)
    return row


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
        with self.assertRaisesRegex(ValueError, 'source capture hash mismatch'):
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

    def test_variables_expressions_markup_and_escapes_are_not_literal_labels(self):
        for text in ('${REFERENCE}', '@{2+3}', 'V@{1+1}', '~{RESET}', 'V_{IN}', 'V^{2}',
                     '\\@{x}', '\\${x}', '\\~{RESET}', 'a\\b'):
            self.design['markings'] = [label(text=text)]
            with self.subTest(text=text), self.assertRaisesRegex(DesignError, 'literal'):
                validate(self.design)

    def test_kicad_brace_escape_tokens_are_refused_before_capture(self):
        for token in ('dblquote', 'quote', 'lt', 'gt', 'backslash', 'slash', 'bar', 'comma',
                      'colon', 'space', 'dollar', 'tab', 'return', 'brace'):
            self.design['markings'] = [label(text='A{' + token + '}B')]
            with self.subTest(token=token), self.assertRaisesRegex(DesignError, 'literal'):
                validate(self.design)
        for text in ('{{slash}}', '{dollar}{brace}REFERENCE}'):
            self.design['markings'] = [label(text=text)]
            with self.subTest(text=text), self.assertRaisesRegex(DesignError, 'literal'):
                validate(self.design)
        for text in ('V{unknown}', '{Slash}'):
            self.design['markings'] = [label(text=text)]
            validate(self.design)

    def test_compiler_uses_all_four_generator_rules_without_a_fab_grade_gate(self):
        self.design['markings'] = [label()]
        compile_design(self.design, self.root / 'capture', self.root / 'bundle')
        root = self.root / 'bundle/inputs'
        profile = read_json(root / 'board-text-profile.json')
        self.assertEqual({r['key'] for r in profile['limits']}, {
            'min_silk_text_height_mm', 'min_silk_line_width_mm', 'min_pad_to_silk_mm', 'min_silk_to_mask_mm'})
        mapped = read_json(root / 'requirement-map.json')
        delivery = next(r for r in mapped['requirements'] if r['id'] == 'TEST-DELIV-2')
        self.assertEqual([c['kind'] for c in delivery['checks']], ['board_text'])
        self.assertNotIn('board-text-fab', {c['id'] for r in mapped['requirements'] for c in r.get('checks', [])})
        from agent_ee.requirement_map import input_inventory
        rules = board_text.design_rules(root, read_json(root / 'board-text.json'), input_inventory(root))
        self.assertEqual(rules['limits']['min_silk_to_mask_mm'], .15)
        self.assertEqual(rules['basis']['kind'], 'generator_design_rule')
        self.assertEqual(rules['basis']['scope'], 'declared_labels_only')
        self.assertFalse(rules['basis']['qualified_fabrication_profile_grade'])
        self.assertEqual(len(rules['basis']['limits']), 4)
        advisories = {r['code']: r for r in rules['advisories']}
        self.assertIn('supplier_applicability_not_qualified', advisories)
        self.assertIn('fabrication_order_not_qualified', advisories)
        self.assertEqual(advisories['fabrication_order_not_qualified']['recorded'], [])
        self.assertTrue(advisories['supplier_applicability_not_qualified']['recorded'])

    def test_mask_rule_source_and_value_must_be_verified(self):
        from agent_ee.requirement_map import input_inventory
        from agent_ee.storage import write_json
        self.design['markings'] = [label()]
        compile_design(self.design, self.root / 'capture', self.root / 'bundle')
        root = self.root / 'bundle/inputs'
        profile = read_json(root / 'board-text-profile.json')
        next(r for r in profile['limits'] if r['key'] == 'min_silk_to_mask_mm')['value'] = .01
        write_json(root / 'board-text-profile.json', profile)
        with self.assertRaisesRegex(ValueError, 'sourced|differs'):
            board_text.design_rules(root, read_json(root / 'board-text.json'), input_inventory(root))


GEOMETRY = '''
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
'''

SETUP = GEOMETRY + '''
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

    def native(self, script, setup=SETUP, engine=False):
        work = self.root / 'work'
        mounts = ()
        if engine:
            from agent_ee.placement_job import rust_executable
            mounts = ((str(rust_executable()), '/engine'),
                      (str(Path(__file__).resolve().parents[1] / 'fixtures'), '/fixtures'))
        result = execute(self.inputs, work, ['/usr/bin/python3', '-B', '-c', setup + script], extra_ro=mounts)
        self.assertEqual((result['execution'], result['exit_code']), ('ok', 0),
                         (work / 'console.log').read_text())
        return work

    def mechanics_statement_places(self, include_free):
        import shutil
        design = design_fixture(self.root / 'capture')
        design['board'].update(width_mm=40, height_mm=30)
        design['mechanics'] = dict(access_owners=[])
        design['markings'] = [label(anchor=dict(reference='R1', pin='1', offset_mm=[0, 3]))]
        if include_free:
            design['markings'].append(dict(id='name', text='TEXT', layer='F.SilkS',
                anchor=dict(at_mm=[20, 15]), height_mm=1.000001, stroke_mm=.150001, rotation_deg=90))
        compile_design(design, self.root / 'capture', self.root / 'bundle')
        shutil.copytree(self.root / 'bundle/inputs', self.inputs, dirs_exist_ok=True)
        self.native('''
import copy
from pathlib import Path
from agent_ee import placement_job as job, placement_problem, source_mechanics
from agent_ee.placement_probe import extract
from agent_ee.board_text import load_document, design_rules
from agent_ee.requirement_map import input_inventory
b.Remove(fp)
for edge in b.GetDrawings(): edge.Move(k.VECTOR2I(100000000,100000000))
for ref,x,y in [('R1',108,110),('R2',130,120)]:
    part=k.FootprintLoad('/candidate/Owned.pretty','R2');assert part is not None
    part.SetReference(ref);part.SetValue('10k');part.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));b.Add(part)
doc=load_document('/candidate/board-text.json')
rules=design_rules(Path('/candidate'),doc,input_inventory('/candidate'));limits=rules['limits']
emit(b,doc,limits)
path=Path('/work/source.kicad_pcb');assert k.SaveBoard(str(path),b)
facts=extract(path,board=b,before=path.read_bytes());description=job._describe_board(b,facts)
recipe=source_mechanics.load('/candidate/placement-mechanics.json')
binding=source_mechanics.bind(recipe,facts);source_mechanics.attach(description,binding,facts)
original=copy.deepcopy(description)
normal=job.normalize_intent({'fixed_poses':[]},{'groups':[]})
problem,_=placement_problem.capture(description,normal)
assert description==original, 'builder mutated captured mechanics or text'
assert Path('/candidate/analysis/geometry-model.json').read_text()==recipe['model_raw']
search={'seeds':[{'seed':0,'start':'scattered'}],'steps_per_seed':20000,'shortlist':1}
out,_=job.solve(problem,search,Path('/work'),deadline_s=10,executable='/engine')
assert out['shortlist'] and out['shortlist'][0]['terms']['hard_count']==0,out
for row in problem['footprints']:
    if row['native_ref'].startswith('board-text:'):
        assert row['source_body_local_nm']==row['occupancy']['local_rect_nm'],row
placed=job.materialize(path,out['shortlist'][0]['poses'],Path('/work/placed'),b'{}\\n',normalized=normal)
assert placed['integrity']=='preserved',placed
saved=k.LoadBoard(placed['board']);assert len(list(saved.GetFootprints()))==2
assert evaluate(saved,doc,limits)['outcome']=='pass',evaluate(saved,doc,limits)
''', setup=GEOMETRY, engine=True)

    def test_statement_with_mechanics_free_and_pin_labels_places(self):
        self.mechanics_statement_places(include_free=True)

    def test_statement_with_mechanics_and_only_a_pin_label_places(self):
        self.mechanics_statement_places(include_free=False)

    def test_build_problem_search_reserves_free_and_attached_text_and_materializes_only_parts(self):
        self.native('''
from pathlib import Path
from agent_ee import placement_job as job, placement_problem
from agent_ee.placement_probe import extract
free=next(i for i in b.GetDrawings() if i.GetClass()=='PCB_TEXT')
free.SetText('X');free.SetLayer(k.F_SilkS);free.SetMirrored(False)
free.SetPosition(k.VECTOR2I(10500000,16000000))
doc['items'][1].update(text='X',layer='F.SilkS',anchor={'at_mm':[10.5,16]})
for layer in (k.F_CrtYd,k.F_SilkS):
    frame=k.PCB_SHAPE(fp,k.SHAPE_T_RECT);frame.SetLayer(layer);frame.SetWidth(50000)
    frame.SetStart(k.VECTOR2I(9000000,14000000));frame.SetEnd(k.VECTOR2I(13500000,18000000));fp.Add(frame)
assert 'label_covered_by_part' in evaluate(b,doc,limits)['records'][1]['violations']
path=Path('/work/source.kicad_pcb');assert k.SaveBoard(str(path),b)
facts=extract(path,board=b,before=path.read_bytes());description=job._describe_board(b,facts)
normal=job.normalize_intent({'fixed_poses':[],'edge_connectors':[]},{'groups':[]})
problem,_=placement_problem.capture(description,normal)
real=next(r for r in problem['footprints'] if r['native_ref']=='J1')
reservation=next(r for r in problem['footprints'] if r['native_ref'].startswith('board-text:'))
assert reservation['role']=='fixed_body' and len(problem['footprints'])==2
assert real['occupancy']['local_rect_nm'][2]>=5000000, real
search={'seeds':[{'seed':0,'start':'scattered'}],'steps_per_seed':20000,'shortlist':1}
out,_=job.solve(problem,search,Path('/work'),deadline_s=10,executable='/engine')
assert out['shortlist'], out
candidate=out['shortlist'][0]
assert candidate['terms']['hard_count']==0, candidate
placed=job.materialize(path,candidate['poses'],Path('/work/placed'),b'{}\\n',normalized=normal)
assert placed['integrity']=='preserved', placed
saved=k.LoadBoard(placed['board'])
assert len(list(saved.GetFootprints()))==1
assert evaluate(saved,doc,limits)['outcome']=='pass', evaluate(saved,doc,limits)
bad=[dict(r) for r in candidate['poses']]
next(r for r in bad if r['uuid']==reservation['uuid'])['x_nm']=1
try: job.materialize(path,bad,Path('/work/bad'),b'{}\\n',normalized=normal)
except job.JobRefusal: pass
else: raise AssertionError('moved text obstacle admitted')
''', engine=True)

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

    def test_left_right_flip_retains_every_declared_quarter_turn_on_both_layers(self):
        self.native('''
import copy
from agent_ee.board_text import _pose
for theta in (0,90,180,270):
    for rot in (0,90,180,270):
        for target in (0,90,180,270):
            for layer in ('F.SilkS','B.SilkS'):
                fp.SetOrientationDegrees(theta)
                # Start from a front part each time, as the placer does.
                if fp.IsFlipped(): fp.Flip(fp.GetPosition(),k.FLIP_DIRECTION_LEFT_RIGHT)
                fp.SetOrientationDegrees(theta)
                row=copy.deepcopy(doc['items'][0]);row['rotation_deg']=rot;row['layer']=layer
                owner,at,angle,side,mirror=_pose(b,doc,row)
                t=k.PCB_TEXT(fp);t.SetText('+');t.SetKeepUpright(False)
                t.SetPosition(at);t.SetTextAngleDegrees(angle);t.SetLayer(side);t.SetMirrored(mirror);fp.Add(t)
                assert t.GetTextAngleDegrees()%360==(theta+rot)%360
                fp.Flip(fp.GetPosition(),k.FLIP_DIRECTION_LEFT_RIGHT)
                fp.SetOrientationDegrees(target)
                _,expected,angle,side,mirror=_pose(b,doc,row)
                assert t.GetPosition()==expected and t.GetTextAngleDegrees()%360==angle, (theta,rot,target,layer)
                assert t.GetLayer()==side and t.IsMirrored()==mirror and not t.IsKeepUpright()
                if theta==0 and rot in (180,270) and target==90 and layer=='F.SilkS':
                    assert k.SaveBoard('/work/left-right.kicad_pcb',b)
                    saved=k.LoadBoard('/work/left-right.kicad_pcb')
                    again=next(i for f in saved.GetFootprints() for i in f.GraphicalItems()
                               if i.m_Uuid.AsString()==t.m_Uuid.AsString())
                    assert again.GetTextAngleDegrees()%360==angle and not again.IsKeepUpright()
                fp.Remove(t)
''')

    def test_back_label_on_front_part_and_shown_expression_text(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
doc['items'][0]['layer']='B.SilkS';text.SetLayer(k.B_SilkS);text.SetMirrored(True)
assert not fp.IsFlipped() and evaluate(b,doc,limits)['outcome']=='pass'
text.SetText('@{2+3}');doc['items'][0]['text']='@{2+3}'
assert text.GetText()=='@{2+3}' and text.GetShownText(False)=='5'
assert 'content_or_visibility_changed' in evaluate(b,doc,limits)['records'][0]['violations']
''')

    def test_declared_centre_can_overhang_the_edge_and_is_refused(self):
        self.native('''
doc['items'][1]['anchor']['at_mm']=[0,0]
text=next(i for i in b.GetDrawings() if i.GetClass()=='PCB_TEXT')
text.SetPosition(k.VECTOR2I(0,0))
row=evaluate(b,doc,limits)['records'][1]
assert 'text_outside_board' in row['violations'] and 'declared_pose_changed' not in row['violations'], row
''')

    def test_real_pad_clearance_boundary(self):
        self.native('''
from agent_ee.board_text import _conflicts
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
right=text.GetEffectiveShape().BBox().GetRight()
for gap,conflict in ((150001,False),(150000,False),(149999,True)):
    q=k.PAD(fp);q.SetNumber('2');q.SetAttribute(k.PAD_ATTRIB_SMD);q.SetShape(k.PAD_SHAPE_RECT)
    q.SetSize(k.VECTOR2I(1000000,1000000));q.SetLayerSet(ls)
    q.SetPosition(k.VECTOR2I(right+gap+500000,text.GetPosition().y));fp.Add(q)
    assert bool(_conflicts(text,[q],150000))==conflict, gap
    fp.Remove(q)
''')

    def test_each_text_size_rule_at_and_below_the_boundary(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
for key,bound in (('height_mm',1000000),('stroke_mm',150000)):
    for value,defect in ((bound,False),(bound-1,True)):
        doc['items'][0][key]=value/1000000
        if key=='height_mm': text.SetTextSize(k.VECTOR2I(value,value))
        else: text.SetTextThickness(value)
        row=evaluate(b,doc,limits)['records'][0]
        assert ('text_below_fabrication_minimum' in row['violations'])==defect, row
    doc['items'][0].pop(key)
    text.SetTextSize(k.VECTOR2I(1000000,1000000));text.SetTextThickness(150000)
''')

    def test_mask_clearance_uses_expanded_and_contracted_apertures_on_both_sides(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
for side,mask in ((k.F_Cu,k.F_Mask),(k.B_Cu,k.B_Mask)):
    text.SetLayer(k.F_SilkS if side==k.F_Cu else k.B_SilkS)
    doc['items'][0]['layer']='F.SilkS' if side==k.F_Cu else 'B.SilkS'
    text.SetMirrored(side==k.B_Cu)
    right=text.GetEffectiveShape().BBox().GetRight()
    # A mask-only pad isolates this rule from the pad-copper floor. Normal
    # copper pads additionally exercise the native resolved expansion.
    for margin,copper in ((0,False),(100000,True),(-100000,True)):
        q=k.PAD(fp);q.SetNumber('2');q.SetAttribute(k.PAD_ATTRIB_SMD);q.SetShape(k.PAD_SHAPE_RECT)
        q.SetSize(k.VECTOR2I(1000000,1000000));q.SetLocalSolderMaskMargin(margin)
        layers=k.LSET();layers.AddLayer(mask)
        if copper: layers.AddLayer(side)
        q.SetLayerSet(layers);fp.Add(q)
        for gap,defect in ((150001,False),(150000,False),(149999,True)):
            q.SetPosition(k.VECTOR2I(right+margin+gap+500000,text.GetPosition().y))
            row=evaluate(b,doc,limits)['records'][0]
            assert q.GetSolderMaskExpansion(mask)==margin
            assert ('silk_to_mask_clearance' in row['violations'])==defect, (side,margin,gap,row)
        fp.Remove(q)
''')

    def test_mask_graphics_and_exposed_vias_are_obstacles_but_tented_vias_are_not(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
right=text.GetEffectiveShape().BBox().GetRight()
opening=k.PCB_SHAPE(b,k.SHAPE_T_RECT);opening.SetLayer(k.F_Mask);opening.SetFilled(True)
opening.SetStart(k.VECTOR2I(right+149999,text.GetPosition().y-500000))
opening.SetEnd(k.VECTOR2I(right+1149999,text.GetPosition().y+500000));opening.SetWidth(0);b.Add(opening)
assert 'silk_to_mask_clearance' in evaluate(b,doc,limits)['records'][0]['violations']
b.Remove(opening)
via=k.PCB_VIA(b);via.SetWidth(1000000);via.SetDrill(500000)
via.SetPosition(text.GetPosition());via.SetLayerPair(k.F_Cu,k.B_Cu);via.SetFrontTentingMode(k.TENTING_MODE_NOT_TENTED);b.Add(via)
assert 'silk_to_mask_clearance' in evaluate(b,doc,limits)['records'][0]['violations']
via.SetFrontTentingMode(k.TENTING_MODE_TENTED)
assert 'silk_to_mask_clearance' not in evaluate(b,doc,limits)['records'][0]['violations']
''')

    def test_mask_opening_remains_an_obstacle_when_through_hole_copper_is_not_flashed(self):
        self.native('''
text=next(i for i in fp.GraphicalItems() if i.GetClass()=='PCB_TEXT')
right=text.GetEffectiveShape().BBox().GetRight()
q=k.PAD(fp);q.SetNumber('2');q.SetAttribute(k.PAD_ATTRIB_PTH);q.SetShape(k.PAD_SHAPE_RECT)
q.SetSize(k.VECTOR2I(1000000,1000000));q.SetDrillSize(k.VECTOR2I(200000,200000))
layers=k.LSET();layers.AddLayer(k.F_Mask);q.SetLayerSet(layers)
q.SetPosition(k.VECTOR2I(right+149999+500000,text.GetPosition().y));fp.Add(q)
assert not q.FlashLayer(k.F_Cu) and q.FlashLayer(k.F_Mask)
assert q.GetEffectiveShape(k.F_Cu).BBox().GetWidth() < q.GetEffectiveShape(k.F_Mask).BBox().GetWidth()
assert 'silk_to_mask_clearance' in evaluate(b,doc,limits)['records'][0]['violations']
''')

    def test_existing_stock_artwork_is_not_graded_by_generated_text_rules(self):
        self.native('''
stock=k.PCB_SHAPE(b,k.SHAPE_T_SEGMENT);stock.SetLayer(k.F_SilkS)
stock.SetStart(k.VECTOR2I(30000000,20000000));stock.SetEnd(k.VECTOR2I(32000000,20000000))
stock.SetWidth(50000);b.Add(stock)
q=k.PAD(fp);q.SetNumber('2');q.SetAttribute(k.PAD_ATTRIB_SMD);q.SetShape(k.PAD_SHAPE_RECT)
q.SetSize(k.VECTOR2I(1000000,1000000));q.SetLayerSet(ls)
q.SetPosition(k.VECTOR2I(31000000,20640000));fp.Add(q)
assert evaluate(b,doc,limits)['outcome']=='pass'
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
''', setup=GEOMETRY)
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
        report = read_json(checked / 'board-text.json')
        self.assertEqual(report['outcome'], 'pass')
        self.assertEqual(report['basis']['kind'], 'generator_design_rule')
        self.assertFalse(report['basis']['qualified_fabrication_profile_grade'])
        self.assertEqual(len(report['basis']['limits']), 4)
        self.assertIn('fabrication_order_not_qualified', {a['code'] for a in report['advisories']})

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
