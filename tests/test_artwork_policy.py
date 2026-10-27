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

    def test_policy_file_receipt_scope_needs_exact_canonical_bytes(self):
        """AGENTEE-551: a writer-authored policy file is decoded from its bytes, never inferred."""
        before = F.board()
        policy = dict(schema=art.ARTWORK_POLICY, source_sha256=hashlib.sha256(before).hexdigest(),
                      free_groups=[dict(owner_uuid=None, members=['b', 'a'])])
        canonical = art.canonical_artwork_policy(policy).encode()
        argv = ['python3', '-B', '-I', '/artwork.py', '/board', '--artwork-policy-file', 'project/policy.json']
        self.assertEqual(art.receipt_policy_file(argv), 'project/policy.json')
        self.assertEqual(art.carried_files(argv), [art.SOURCE, art.POLICY_COPY])
        self.assertEqual(art.carried_files(argv[:5]), [art.SOURCE])
        self.assertEqual(art.receipt_scope(argv, canonical)['artwork_policy']['free_groups'],
                         [dict(owner_uuid=None, members=['a', 'b'])])
        for data, message in ((None, 'not supplied'), (json.dumps(policy).encode(), 'not canonical'),
                              (canonical + b'\n', 'not canonical'), (b'null', 'cannot be null'),
                              (b'\xff', 'not UTF-8')):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, message):
                art.receipt_scope(argv, data)
        for tail in (['--artwork-policy-file'], ['--artwork-policy-file', '/abs.json'],
                     ['--artwork-policy-file', '../up.json'],
                     ['--artwork-policy-file', 'project/policy.json', '--target', 'U31', 'Reference']):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                art.receipt_scope(argv[:5] + tail, canonical)
        # Bytes offered for a receipt that names no policy file are refused, not ignored.
        with self.assertRaisesRegex(ValueError, 'names no policy file'):
            art.receipt_scope(argv[:5], canonical)

    def test_policy_file_step_declares_its_input_and_stays_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = art.step(Path(tmp) / 'tool', 'board.kicad_pcb', artwork_policy_file='project/policy.json')
            self.assertEqual(spec['argv'][5:], ['--artwork-policy-file', 'project/policy.json'])
            self.assertEqual(spec['inputs'], ['project/policy.json'])
            self.assertNotIn('inputs', art.step(Path(tmp) / 'tool2', 'board.kicad_pcb'))
        policy = dict(schema=art.ARTWORK_POLICY, source_sha256='0' * 64, free_groups=[])
        for kwargs in (dict(artwork_policy=policy), dict(targets=[('U31', 'Reference')]),
                       dict(field_poses=F.request(F.board()))):
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaisesRegex(ValueError, 'exclusive'):
                art.step('/nonexistent', 'board.kicad_pcb', artwork_policy_file='project/policy.json', **kwargs)
        for path in ('board.kicad_pcb', '/abs.json', '../policy.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                art.step('/nonexistent', 'board.kicad_pcb', artwork_policy_file=path)

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

    def test_writer_policy_file_is_hashed_input_and_bound_to_exact_source(self):
        """AGENTEE-551: the receipt hashes the writer's policy file and replay decodes it from there."""
        import pcbnew as k
        from agent_ee import stage_receipts
        from agent_ee.run_record import policy_files
        source = self.fixture.board(graphic=True)
        board = k.LoadBoard(str(source))
        ids = [item.m_Uuid.AsString() for item in board.GetDrawings() if item.GetLayer() == k.F_SilkS]
        self.assertEqual(len(ids), 1)
        rel = 'artwork-policy.json'
        def run(name, policy):
            design = self.root / name / 'design'
            design.mkdir(parents=True)
            (design / 'board.kicad_pcb').write_bytes(source.read_bytes())
            (design / rel).write_text(art.canonical_artwork_policy(policy))
            spec = art.step(self.root / name / 'tool', 'board.kicad_pcb', artwork_policy_file=rel)
            directory = self.root / name / 'receipt'
            return directory, stage_receipts.run_step(directory, design, spec, {'implementation': 1})
        bound = dict(schema=art.ARTWORK_POLICY, source_sha256=art.digest(source),
                     free_groups=[dict(owner_uuid=None, members=ids)])
        directory, receipt = run('bound', bound)
        self.assertEqual((receipt['status'], receipt['violations']), ('completed', []))
        self.assertEqual(receipt['inputs'][rel], art.digest(directory / 'inputs' / rel))
        proof = art.qualify_receipt(directory, receipt, 'board.kicad_pcb', policy_files())
        self.assertEqual(proof['moved_identities'], ids)
        self.assertEqual(proof['artwork_policy'], bound)
        # The same output under the default anchored scope is refused: the file is the authority.
        with self.assertRaisesRegex(ValueError, 'anchored artwork'):
            art.write_scope((directory / 'inputs/board.kicad_pcb').read_bytes(),
                            (directory / 'run' / art.BOARD).read_bytes())
        policy_input, sealed = directory / 'inputs' / rel, directory / 'run' / art.POLICY_COPY
        original = policy_input.read_bytes()
        self.assertEqual(sealed.read_bytes(), original)
        anchored = art.canonical_artwork_policy({**bound, 'free_groups': []}).encode()
        for name, mutate, message in (
                ('input bytes', lambda r: policy_input.write_bytes(anchored), 'differs from receipt input'),
                ('input digest', lambda r: r['inputs'].update({rel: hashlib.sha256(anchored).hexdigest()}),
                 'differs from receipt input'),
                # The producer's sealed copy, which carry replay reads, must be the hashed input too.
                ('sealed bytes', lambda r: sealed.write_bytes(anchored), 'differs from receipt input'),
                ('sealed digest', lambda r: r['artifacts'].update({art.POLICY_COPY: hashlib.sha256(anchored).hexdigest()}),
                 'differs from receipt input'),
                ('undeclared', lambda r: r['spec'].pop('inputs'), 'not its declared input'),
                ('argv dropped', lambda r: r['spec'].__setitem__('argv', r['spec']['argv'][:5]),
                 'inputs without a policy file')):
            changed = copy.deepcopy(receipt)
            mutate(changed)
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                art.qualify_receipt(directory, changed, 'board.kicad_pcb', policy_files())
            policy_input.write_bytes(original)
            sealed.write_bytes(original)
        self.assertEqual(art.qualify_receipt(directory, receipt, 'board.kicad_pcb', policy_files()), proof)
        # A policy bound to any other board is refused by the producer, as for argv policies.
        _, refused = run('other', {**bound, 'source_sha256': '0' * 64})
        self.assertNotEqual(refused['status'], 'completed')
        stderr = (self.root / 'other/receipt/run/stderr.log').read_text()
        self.assertIn('artwork policy names a different source board', stderr)

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
