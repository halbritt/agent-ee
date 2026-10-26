"""Authored synthetic artwork authorization controls; no third-party board bytes."""
import unittest
import hashlib
import tempfile
from pathlib import Path
import copy
import json

from agent_ee import routed_artwork as art
from test_routed_artwork import FieldPoseFixture as F


class ArtworkPolicyTests(unittest.TestCase):
    def test_primitive_translation_does_not_authorize_anchored_outline(self):
        before = F.board()
        after = F.board(line='(start 1 2) (end 2 2)')
        with self.assertRaisesRegex(ValueError, 'anchored artwork'):
            art.write_scope(before, after)

    def test_source_bound_free_group_requires_common_translation(self):
        before = b'(kicad_pcb (gr_line (start 0 0) (end 1 0) (layer "F.SilkS") (uuid "a")) (gr_line (start 1 0) (end 1 1) (layer "F.SilkS") (uuid "b")))'
        after = before.replace(b'(start 0 0)', b'(start 2 0)').replace(b'(end 1 0)', b'(end 3 0)').replace(b'(start 1 0)', b'(start 3 0)').replace(b'(end 1 1)', b'(end 3 1)')
        policy = dict(schema='agent-ee.artwork-policy/1', source_sha256=hashlib.sha256(before).hexdigest(),
                      free_groups=[dict(owner_uuid=None, members=['a', 'b'])])
        self.assertEqual(art.write_scope(before, after, artwork_policy=policy)['moved_identities'], ['a', 'b'])
        with self.assertRaisesRegex(ValueError, 'rigid artwork group'):
            art.write_scope(before, after.replace(b'(end 3 1)', b'(end 1 1)').replace(b'(start 3 0)', b'(start 1 0)'), artwork_policy=policy)

    def test_intrinsic_comparison_keeps_warning_but_ignores_proved_position(self):
        before, after = F.board(), F.board(u31='1 2.4 90')
        finding = dict(type='text_height', severity='warning',
                       description='Text height out of range (board setup constraints silk text height min height 1.2000 mm; actual 1.0000 mm)',
                       items=[dict(uuid=F.uid(1), description='Reference field of U31', pos=dict(x=10, y=20))])
        old = dict(violations=[finding], ignored_checks=[], included_severities=['error', 'warning', 'exclusion'])
        import copy
        new = copy.deepcopy(old)
        new['violations'][0]['items'][0]['pos']['x'] = 11
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'source.kicad_pcb', Path(directory)/'after.kicad_pcb'
            source.write_bytes(before); output.write_bytes(after)
            pins = [art.artwork_drc_pin(source, old), art.artwork_drc_pin(output, new)]
            result = art.compare_artwork_drc(source, output, old, new, native_pins=pins)
            self.assertEqual(result['added_findings'], [])
            self.assertEqual(len(result['candidate_findings']), 1)
            self.assertEqual(result['intrinsic_text_baseline'], [F.uid(1)])

    def test_policy_binding_inventory_owner_and_raw_style_refuse(self):
        before = F.board()
        after = F.board(line='(start 1 2) (end 2 2)')
        good = dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(before).hexdigest(),
                    free_groups=[dict(owner_uuid=F.uid(10), members=[F.uid(11)])])
        self.assertEqual(art.write_scope(before, after, artwork_policy=good)['moved_identities'], [F.uid(11)])
        for mutation in ('source', 'owner', 'missing', 'duplicate', 'field', 'unknown', 'extra-key'):
            policy = copy.deepcopy(good)
            if mutation == 'source': policy['source_sha256'] = '0'*64
            elif mutation == 'owner': policy['free_groups'][0]['owner_uuid'] = F.uid(20)
            elif mutation == 'missing': policy['free_groups'][0]['members'] = [F.uid(999)]
            elif mutation == 'duplicate': policy['free_groups'].append(copy.deepcopy(policy['free_groups'][0]))
            elif mutation == 'field': policy['free_groups'][0]['members'] = [F.uid(1)]
            elif mutation == 'unknown': policy['free_groups'][0]['members'] = [F.uid(40)]
            else: policy['drop'] = [F.uid(11)]
            for output in (before, after):
                with self.subTest(mutation=mutation, changed=output!=before), self.assertRaises(ValueError):
                    art.write_scope(before, output, artwork_policy=policy)
        for changed in (after.replace(b'(end 2 2)', b'(end 3 2)'), after.replace(b'B.SilkS', b'B.Mask', 1)):
            with self.assertRaises(ValueError):
                art.write_scope(before, changed, artwork_policy=good)
        for member in ('(gr_poly (layer "F.SilkS") (uuid "g"))',
                       '(gr_circle (end 1 1) (layer "F.SilkS") (uuid "g"))',
                       '(gr_line (layer "F.SilkS") (uuid "g"))'):
            data = ('(kicad_pcb '+member+')').encode()
            policy = dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(data).hexdigest(),
                          free_groups=[dict(owner_uuid=None, members=['g'])])
            with self.assertRaises(ValueError):
                art.write_scope(data, data, artwork_policy=policy)
        hidden = b'(kicad_pcb (gr_text "hidden" (at 1 2) (layer "F.SilkS") hide (uuid "h")))'
        policy = dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(hidden).hexdigest(),
                      free_groups=[dict(owner_uuid=None, members=['h'])])
        with self.assertRaisesRegex(ValueError, 'hidden artwork'):
            art.write_scope(hidden, hidden, artwork_policy=policy)

    def test_policy_receipt_scope_is_canonical_and_exclusive(self):
        before = F.board()
        policy = dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(before).hexdigest(), free_groups=[])
        argv = ['python3', '-B', '-I', '/artwork.py', '/board', '--artwork-policy', art.canonical_artwork_policy(policy)]
        self.assertEqual(art.receipt_scope(argv), {'artwork_policy': policy})
        with self.assertRaises(ValueError):
            art.canonical_artwork_policy(None)
        for tail in ([json.dumps(policy)], ['null'], [art.canonical_artwork_policy(policy), '--target', 'U31', 'Reference']):
            with self.assertRaises(ValueError):
                art.receipt_scope(argv[:6]+tail)
        with self.assertRaises(ValueError):
            art.write_scope(before, before, artwork_policy=policy, targets=[('U31','Reference')])
        with self.assertRaises(ValueError):
            art.write_scope(before, before, artwork_policy=policy, field_poses=F.request(before))

    def test_visible_text_named_hide_is_not_a_visibility_attribute(self):
        before=b'(kicad_pcb (gr_text "hide" (at 1 2) (layer "F.SilkS") (uuid "visible")))'
        policy=dict(schema=art.ARTWORK_POLICY,source_sha256=hashlib.sha256(before).hexdigest(),
                    free_groups=[dict(owner_uuid=None,members=['visible'])])
        proof=art.write_scope(before,before.replace(b'(at 1 2)',b'(at 2 2)'),artwork_policy=policy)
        self.assertEqual(proof['moved_identities'],['visible'])

    def test_intrinsic_counterexamples_preserve_native_requirements(self):
        before, after = F.board(), F.board(u31='1 2.4 90', c128='1 1.85 0')
        def report(x):
            return dict(violations=[dict(type='text_height', severity='warning',
                description='Text height out of range (board setup constraints silk text height min height 1.2000 mm; actual 1.0000 mm)',
                items=[dict(uuid=uid, description='Reference', pos=dict(x=x,y=20))]) for uid in (F.uid(1), F.uid(4))],
                ignored_checks=[], included_severities=['error','warning','exclusion'])
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'source.kicad_pcb', Path(directory)/'after.kicad_pcb'
            source.write_bytes(before); output.write_bytes(after)
            for mutation in ('same','severity','threshold','actual','identity','count','provenance','rule','board-pin','rule-pin','report-pin','settings'):
                old,new=report(10),report(11)
                if mutation=='severity':new['violations'][0]['severity']='error'
                elif mutation=='threshold':new['violations'][0]['description']=new['violations'][0]['description'].replace('1.2000','1.3000')
                elif mutation=='actual':new['violations'][0]['description']=new['violations'][0]['description'].replace('1.0000','0.9000')
                elif mutation=='identity':new['violations'][0]['items'][0]['uuid']=F.uid(31)
                elif mutation=='count':new['violations'].append(copy.deepcopy(new['violations'][0]))
                elif mutation=='provenance':new['violations'][0]['constraint_provenance']={'enforcement':'hard'}
                elif mutation=='rule':new['violations'][0]['description']=new['violations'][0]['description'].replace('board setup constraints',"rule 'new'")
                elif mutation=='settings':new['included_severities']=['warning']
                pins=[art.artwork_drc_pin(source,old),art.artwork_drc_pin(output,new)]
                if mutation=='board-pin':pins[1]['board_sha256']='0'*64
                elif mutation=='report-pin':pins[1]['report_sha256']='0'*64
                elif mutation=='rule-pin':pins[1]['payloads']['.kicad_dru']='0'*64
                with self.subTest(mutation=mutation):
                    if mutation in ('provenance','board-pin','report-pin','rule-pin','settings'):
                        with self.assertRaises(ValueError):art.compare_artwork_drc(source,output,old,new,native_pins=pins)
                    else:
                        result=art.compare_artwork_drc(source,output,old,new,native_pins=pins)
                        if mutation=='same':
                            self.assertEqual(result['added_findings'],[])
                            self.assertEqual(result['intrinsic_text_baseline'],[F.uid(1),F.uid(4)])
                            self.assertEqual(len(result['advisory_findings']),2)
                        else:self.assertTrue(result['added_findings'])
            # A changed captured rule payload is refused even when separately pinned.
            source.with_suffix('.kicad_pro').write_text('{}')
            old,new=report(10),report(11)
            pins=[art.artwork_drc_pin(source,old),art.artwork_drc_pin(output,new)]
            with self.assertRaises(ValueError):art.compare_artwork_drc(source,output,old,new,native_pins=pins)

    def test_intrinsic_exception_requires_translation_even_with_explicit_field_rotation(self):
        before, after = F.board(), F.board(**F.MOVED)
        old = dict(violations=[dict(type='text_height', severity='warning',
            description='Text height out of range (board setup constraints silk text height min height 1.2000 mm; actual 1.0000 mm)',
            items=[dict(uuid=F.uid(1), description='Reference', pos=dict(x=10,y=20))])],
            ignored_checks=[],included_severities=['error','warning','exclusion'])
        new = copy.deepcopy(old);new['violations'][0]['items'][0]['pos']['x']=11
        with tempfile.TemporaryDirectory() as directory:
            source,output=Path(directory)/'source.kicad_pcb',Path(directory)/'after.kicad_pcb'
            source.write_bytes(before);output.write_bytes(after)
            pins=[art.artwork_drc_pin(source,old),art.artwork_drc_pin(output,new)]
            result=art.compare_artwork_drc(source,output,old,new,native_pins=pins,field_poses=F.request(before))
            self.assertTrue(result['added_findings'])
            self.assertEqual(result['intrinsic_text_baseline'],[])


class NativeArtworkPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def setUp(self):
        from test_routed_artwork import RoutedArtworkTest
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='art-policy-')))
        self.fixture = RoutedArtworkTest()
        self.fixture.root = self.root

    def test_native_rigid_group_and_receipt_reject_independent_or_missing_member(self):
        import pcbnew as k
        from agent_ee import stage_receipts
        from agent_ee.run_record import policy_files
        from agent_ee.symbol_library import parse_sexpr, children
        source = self.fixture.board(graphic=True)
        board = k.LoadBoard(str(source))
        first = next(item for item in board.GetDrawings() if item.GetLayer() == k.F_SilkS)
        second = k.PCB_SHAPE(board)
        second.SetShape(k.SHAPE_T_SEGMENT); second.SetLayer(k.F_SilkS); second.SetWidth(150000)
        second.SetStart(first.GetEnd())
        second.SetEnd(k.VECTOR2I(first.GetEnd().x, first.GetEnd().y + 500000))
        board.Add(second)
        k.SaveBoard(str(source), board, True)
        ids = sorted([first.m_Uuid.AsString(), second.m_Uuid.AsString()])
        policy = dict(schema=art.ARTWORK_POLICY, source_sha256=art.digest(source),
                      free_groups=[dict(owner_uuid=None, members=ids)])
        design = self.root/'design'; design.mkdir()
        (design/'board.kicad_pcb').write_bytes(source.read_bytes())
        spec = art.step(self.root/'tool', 'board.kicad_pcb', artwork_policy=policy)
        directory = self.root/'receipt'
        receipt = stage_receipts.run_step(directory, design, spec, {'implementation':1})
        self.assertEqual(receipt['status'], 'completed')
        proof = art.qualify_receipt(directory, receipt, 'board.kicad_pcb', policy_files())
        self.assertEqual(proof['moved_identities'], ids)
        saved = directory/'run'/art.BOARD
        data = saved.read_bytes()
        def emit(n):return '('+' '.join(emit(c) if isinstance(c,list) else c for c in n)+')'
        for mutation in ('independent','missing','changed-policy'):
            tree = parse_sexpr(data, raw=True)
            item = next(n for n in tree[1:] if isinstance(n,list) and children(n,'uuid')==[['uuid','"'+ids[0]+'"']])
            changed = copy.deepcopy(receipt)
            if mutation=='missing':tree.remove(item)
            elif mutation=='independent':
                for name in ('start','end'):children(item,name)[0][1]=str(float(children(item,name)[0][1])+1)
            else:
                altered = copy.deepcopy(policy); altered['free_groups'][0]['members']=ids[:1]
                changed['spec']['argv'][-1]=art.canonical_artwork_policy(altered)
            saved.write_bytes(emit(tree).encode())
            message={'independent':'rigid artwork group','missing':'artwork group member missing',
                     'changed-policy':'anchored artwork'}[mutation]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError,message):
                art.qualify_receipt(directory, changed, 'board.kicad_pcb', policy_files())
        saved.write_bytes(data)

    def test_two_intrinsic_warnings_retained_and_edge_residual_visible(self):
        import pcbnew as k
        from agent_ee.silk_artwork import SILK_CONFLICTS
        self.assertNotIn('silk_edge_clearance', SILK_CONFLICTS)
        source = self.fixture.board()
        board = k.LoadBoard(str(source))
        ids=[]
        for number, fp in enumerate(board.GetFootprints()):
            fp.Reference().SetVisible(False)
            text = k.PCB_TEXT(board); text.SetText('hide' if number==0 else 'free'+str(number)); text.SetLayer(k.F_SilkS)
            text.SetTextSize(k.VECTOR2I(508000,508000)); text.SetTextThickness(127000)
            text.SetPosition(next(iter(fp.Pads())).GetPosition()); board.Add(text)
            ids.append(text.m_Uuid.AsString())
        edge = k.PCB_SHAPE(board); edge.SetShape(k.SHAPE_T_SEGMENT); edge.SetLayer(k.F_SilkS)
        edge.SetWidth(150000); edge.SetStart(k.VECTOR2I(0,1000000)); edge.SetEnd(k.VECTOR2I(0,2000000)); board.Add(edge)
        k.SaveBoard(str(source),board,True)
        policy = dict(schema=art.ARTWORK_POLICY,source_sha256=art.digest(source),
                      free_groups=[dict(owner_uuid=None,members=[uid]) for uid in ids])
        result=art.produce(source,self.root/'run',artwork_policy=policy)
        self.assertEqual(result['native_drc_comparison']['intrinsic_text_baseline'],sorted(ids))
        self.assertEqual(sum(v['type']=='text_height' for v in result['native_drc_comparison']['candidate_findings']),2)
        self.assertTrue(any(d['native']['type']=='silk_edge_clearance' for d in result['open_defects']))
        self.assertEqual(result['verdict'],'defect')
