"""Owned snippets exercise exact edits without publishing upstream designs."""
import unittest
from agent_ee.benchmark import parse, children, select, replace_spans, property_edit


class ExactMutationTests(unittest.TestCase):
    def test_value_edit_preserves_other_bytes_and_escaped_strings(self):
        source = '(root\n(symbol (uuid "a") (property "Value" "1k") (property "Note" "a\\\"b"))\n(symbol (uuid "b") (property "Value" "1k")))'
        node = select(parse(source), 'symbol', 'a')
        changed = replace_spans(source, [property_edit(node, 'Value', '1k', '2k')])
        self.assertEqual(changed, source.replace('"Value" "1k"', '"Value" "2k"', 1))
        self.assertEqual(len(children(parse(changed), 'symbol')), 2)

    def test_refuses_ambiguous_or_stale_value_and_malformed_source(self):
        for source in ('(root (symbol (uuid a))(symbol (uuid a)))', '(root (symbol (uuid b)))'):
            with self.assertRaisesRegex(ValueError, 'unique'):
                select(parse(source), 'symbol', 'a')
        node = select(parse('(root (symbol (uuid a)(property "Value" "1k")))'),'symbol','a')
        with self.assertRaisesRegex(ValueError, 'stale'):
            property_edit(node,'Value','2k','3k')
        for source in ('(root', '(root))', '(root "unterminated)', '(root) junk', '(root)(root)'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse(source)

    def test_real_pad_geometry_changes_and_net_uuid_preservation(self):
        from agent_ee.benchmark import footprint_edit
        source = '(root (footprint "Old" (uuid f) (pad "1" thru_hole circle (at 0 0)(size 2 2)(drill 1)(layers "*.Cu")(net 1 "A")(uuid p1))(pad "2" thru_hole circle (at 0 2.54)(size 2 2)(drill 1)(layers "*.Cu")(net 2 "B")(uuid p2))))'
        alternate = '(footprint "New" (pad "1" thru_hole rect (at 0 0)(size 1 1)(drill 0.5)(layers "*.Cu"))(pad "2" thru_hole circle (at 0 1.27)(size 1 1)(drill 0.5)(layers "*.Cu")))'
        fp = select(parse(source),'footprint','f')
        changed = replace_spans(source,footprint_edit(source,fp,alternate,{'after_name':'New'}))
        self.assertIn('(at 0.000000 1.270000 0.000000)',changed)
        self.assertIn('(net 2 "B")',changed)
        self.assertIn('(uuid p2)',changed)
        self.assertIn('(drill 0.5)',changed)
        with self.assertRaisesRegex(ValueError,'pad set'):
            footprint_edit(source,fp,alternate.replace('"2"','"3"'),{'after_name':'New'})
        with self.assertRaisesRegex(ValueError,'physical pitch'):
            footprint_edit(source,fp,alternate.replace('1.27','2.54'),{'after_name':'New'})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.case = {'id':'seed','source_selectors':[{'pcb':{'footprint_uuid':'target'}}]}
        self.expected = {'kind':'hardware_interface','predicate':{'kind':'value'}}

    def record(self, status='violation', execution='ok'):
        return {'check':{'execution':execution,'outcome':'defect','details':{'invariants':[
            {'id':'unrelated-0','status':'violation'},{'id':'seed-0','status':status}]}},'native':None}

    def test_unrelated_defect_not_hit_and_control_flaw_not_false_alarm(self):
        from agent_ee.benchmark_run import classify
        self.assertEqual(classify(self.expected,self.record('satisfied'),self.case),'miss')
        self.assertEqual(classify(self.expected,self.record(),self.case,self.record('satisfied')),'detection')
        self.assertEqual(classify(self.expected,self.record(),self.case,self.record()),'control_unresolved')
        self.assertEqual(classify(self.expected,self.record(execution='unsupported'),self.case),'unsupported')
        self.assertEqual(classify(self.expected,self.record(execution='malformed'),self.case),'execution_error')
        self.assertEqual(classify(self.expected,None,self.case),'not_evaluated')
        self.assertEqual(classify(self.expected,self.record('not_evaluated'),self.case),'not_evaluated')

    def test_drc_requires_new_native_violation_at_exact_target(self):
        from agent_ee.benchmark_run import classify
        expected = {'kind':'drc','predicate':{'kind':'schematic_parity','pin':'1'}}
        def rec(uuid):
            return {'check':{'execution':'ok','outcome':'defect'},'native':{
                'schematic_parity':[{'type':'net_conflict','items':[{'uuid':uuid}]}]},'pcb_pad_uuids':{'target':{'1':['pad1']}}}
        self.assertEqual(classify(expected,rec('other'),self.case,rec('other')),'miss')
        self.assertEqual(classify(expected,rec('target'),self.case,rec('other')),'miss')
        self.assertEqual(classify(expected,rec('pad1'),self.case,rec('other')),'detection')
        self.assertEqual(classify(expected,rec('pad1'),self.case,rec('pad1')),'control_unresolved')

    def test_canonicalization_keeps_numerical_and_locator_changes(self):
        from agent_ee.benchmark_run import canonical_check
        record = {'id':'x','duration_s':1,'details':{'tool':{'duration_s':2},'voltage':1.2},'location':'R1'}
        other = {**record,'duration_s':9}
        self.assertEqual(canonical_check(record),canonical_check(other))
        other = {**record,'details':{'tool':{'duration_s':2},'voltage':1.3}}
        self.assertNotEqual(canonical_check(record),canonical_check(other))
        self.assertNotEqual(canonical_check(record),canonical_check({**record,'location':'R2'}))


class WiringTests(unittest.TestCase):
    def schematic(self, wires):
        # Four independent owned single-pin connectors on two simple nets.
        libraries = '(lib_symbols (symbol "Owned:Pin" (symbol "Pin_1_1" (pin passive line (at 0 0) (name "P")(number "1")))))'
        symbols = ''.join(f'(symbol (lib_id "Owned:Pin")(unit 1)(at {x} {y} 0)(uuid {ref})(property "Reference" "{ref}")(property "Value" "Pin"))'
            for ref,x,y in [('J1',0,0),('J2',10,0),('J3',0,10),('J4',10,10)])
        return '(kicad_sch '+libraries+symbols+wires+')'

    def test_swap_and_disconnect_actual_partitions_and_redundant_refusal(self):
        from agent_ee.benchmark import wire_edits, verify_wiring
        source = self.schematic('(wire (pts(xy 0 0)(xy 10 0))(uuid w1))(wire(pts(xy 0 10)(xy 10 10))(uuid w2))')
        mutation = {'operation':'delete_exact_schematic_wire','wire_uuid':'w1','before_xy':[[0,0],[10,0]],'terminal':{'reference':'J1','pin':'1'}}
        changed = replace_spans(source,wire_edits(source,parse(source),mutation))
        self.assertTrue(verify_wiring(parse(source),parse(changed),mutation)['passed'])
        redundant = source[:-1]+'(wire (pts(xy 0 0)(xy 10 0))(uuid redundant)))'
        changed = replace_spans(redundant,wire_edits(redundant,parse(redundant),mutation))
        with self.assertRaisesRegex(ValueError,'ineffective'):
            verify_wiring(parse(redundant),parse(changed),mutation)
        swap = {'operation':'swap_wire_pin_endpoints','reference':'J1','pins':['1','2']}
        source = '''(kicad_sch
          (lib_symbols
            (symbol "Owned:Double" (symbol "Double_1_1"
              (pin passive line(at 0 0)(name "P")(number "1"))
              (pin passive line(at 0 -10)(name "Q")(number "2"))))
            (symbol "Owned:Single" (symbol "Single_1_1"
              (pin passive line(at 0 0)(name "P")(number "1")))))
          (symbol(lib_id "Owned:Double")(unit 1)(at 0 0 0)(uuid J1)(property "Reference" "J1")(property "Value" "Double"))
          (symbol(lib_id "Owned:Single")(unit 1)(at 10 0 0)(uuid J2)(property "Reference" "J2")(property "Value" "Single"))
          (symbol(lib_id "Owned:Single")(unit 1)(at 10 10 0)(uuid J4)(property "Reference" "J4")(property "Value" "Single"))
          (wire(pts(xy 0 0)(xy 10 0))(uuid w1))
          (wire(pts(xy 0 10)(xy 10 10))(uuid w2)))'''
        swap['wire_replacements'] = [
            {'wire_uuid':'w1','before_xy':[[0,0],[10,0]],'after_xy':[[0,10],[10,0]]},
            {'wire_uuid':'w2','before_xy':[[0,10],[10,10]],'after_xy':[[0,0],[10,10]]}]
        changed = replace_spans(source,wire_edits(source,parse(source),swap))
        self.assertTrue(verify_wiring(parse(source),parse(changed),swap)['passed'])
        with self.assertRaisesRegex(ValueError,'stale wire'):
            wire_edits(source,parse(source),{**mutation,'before_xy':[[0,0],[9,0]]})


class StorageGuardTests(unittest.TestCase):
    def test_path_symlink_clobber_and_archive_traversal_refused(self):
        import io
        import tarfile
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark import safe_path, checked_read
        from agent_ee.benchmark_run import fresh, extract_archive
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileExistsError):
                fresh(root)
            with self.assertRaises(ValueError):
                safe_path(root,'../escape')
            (root/'link').symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):
                safe_path(root,'link/file')
            (root/'source').write_text('real')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                checked_read(root,'source','0'*64)
            for name,kind in [('../escape',tarfile.REGTYPE),('tree/link',tarfile.SYMTYPE)]:
                archive = root/'source.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    item = tarfile.TarInfo(name);item.type=kind
                    if kind==tarfile.SYMTYPE:
                        item.linkname='/tmp'
                    tar.addfile(item,io.BytesIO())
                dest = root/'extract';dest.mkdir(exist_ok=True)
                with self.assertRaises(ValueError):
                    extract_archive(archive,dest)
                self.assertFalse((root/'escape').exists())

    def test_expectation_commit_required_and_changed_bytes_refused(self):
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import committed_manifest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git','init','-q',str(root)],check=True)
            path=root/'expectations.json'
            manifest=Path(__file__).resolve().parents[1]/'benchmarks/seeded/expectations.json'
            path.write_bytes(manifest.read_bytes())
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','expectations'],check=True)
            commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
            self.assertEqual(len(committed_manifest(path,commit,root)['cases']),30)
            with self.assertRaisesRegex(ValueError,'full commit'):
                committed_manifest(path,'',root)
            path.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'bytes differ'):
                committed_manifest(path,commit,root)

    def test_no_internal_execution_without_strict_committed_request(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import execute_batch
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                execute_batch(Path(tmp)/'prepared',Path(tmp)/'runs')
            self.assertFalse((Path(tmp)/'runs').exists())


class ProtectionStructureTests(unittest.TestCase):
    def test_native_copper_bridge_retains_paths_and_open_is_rejected(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import pcbnew
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark import verify_pcb_protection
        board = pcbnew.BOARD()
        nets=[]
        for name in ('IN','OUT'):
            net=pcbnew.NETINFO_ITEM(board,name);board.Add(net);nets.append(net)
        for ref,positions in [('J1',[0]),('F1',[4,6]),('J2',[10])]:
            fp=pcbnew.FOOTPRINT(board);fp.SetReference(ref);board.Add(fp)
            for i,x in enumerate(positions,1):
                pad=pcbnew.PAD(fp);pad.SetNumber(str(i));pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
                layers=pcbnew.LSET();layers.AddLayer(pcbnew.F_Cu);pad.SetLayerSet(layers)
                pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1),pcbnew.FromMM(1)))
                pad.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x),0))
                pad.SetNet(nets[0] if x<=4 else nets[1]);fp.Add(pad)
        for a,b in [(0,4),(6,10)]:
            track=pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a),0));track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b),0))
            track.SetLayer(pcbnew.F_Cu);track.SetWidth(pcbnew.FromMM(.5))
            track.SetNet(nets[0] if a==0 else nets[1]);board.Add(track)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'owned.kicad_pcb';pcbnew.SaveBoard(str(path),board);before=path.read_text()
        from agent_ee.benchmark import values, span, descendants, tag
        root=parse(before)
        fuse=next(f for f in children(root,'footprint') if any(values(p)[:2]==['Reference','F1'] for p in children(f,'property')))
        edits=[span(fuse,'')]
        for node in descendants(root):
            if tag(node)=='net' and node not in children(root,'net') and values(node)[0]=='IN' and not fuse.start<=node.start<fuse.end:
                edits.append(span(node,'(net "OUT")'))
        opened=replace_spans(before,edits)
        bridged=opened.rstrip()[:-1]+'''(segment(start 4 0)(end 6 0)(width 0.5)(layer "F.Cu")(net "OUT")
            (uuid "12345678-1234-1234-1234-123456789abc")))'''
        self.assertTrue(verify_pcb_protection(before,bridged,'F1','remove_fuse_and_bridge')['passed'])
        with self.assertRaisesRegex(ValueError,'normal copper'):
            verify_pcb_protection(before,opened,'F1','remove_fuse_and_bridge')


class PreregistrationTests(unittest.TestCase):
    def test_concrete_corpus_and_contracts_are_complete_before_execution(self):
        from pathlib import Path
        from collections import Counter
        from agent_ee.benchmark_run import load_manifest, contract_for
        from agent_ee.storage import identity
        path=Path(__file__).resolve().parents[1]/'benchmarks/seeded/expectations.json'
        manifest=load_manifest(path)
        self.assertEqual(len(manifest['boards']),5)
        self.assertEqual(Counter(c['category'] for c in manifest['cases']),{
            'changed_value':9,'swapped_connections':5,'removed_connection':9,'wrong_footprint':5,'removed_protection':2})
        for board in manifest['boards']:
            cases=[c for c in manifest['cases'] if c['board_id']==board['id']]
            self.assertEqual(len(cases),6)
            self.assertEqual(identity(contract_for(board,cases)),board['contract_sha256'])
            for case in cases:
                self.assertTrue(case['structural_receipt']['changed_files'])
                for check in case['expected_checks']:
                    self.assertIn(check['check_id'],['benchmark-hardware-interface','benchmark-drc'])

    def test_duplicate_identity_and_unsafe_native_path_are_rejected(self):
        import copy,json,tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import load_manifest
        source=Path(__file__).resolve().parents[1]/'benchmarks/seeded/expectations.json'
        original=json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'manifest.json'
            data=copy.deepcopy(original);data['cases'].append(data['cases'][0]);path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'duplicate'):
                load_manifest(path)
            data=copy.deepcopy(original);data['boards'][0]['native_file_sha256']['../escape']='0'*64;path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'unsafe'):
                load_manifest(path)


class NativeReviewTests(unittest.TestCase):
    def test_checks_only_exit_one_is_not_a_native_execution_failure(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import review_case
        source=Path(__file__).resolve().parents[1]/'fixtures/hardware-interface/control.scope.json'
        with tempfile.TemporaryDirectory() as tmp:
            result=review_case(source,Path(tmp)/'run')
            self.assertEqual(result['state'],'incomplete')
            self.assertEqual(result['outcome'],'incomplete')
            self.assertTrue(all(c['execution']=='ok' for c in result['checks']),result['checks'])
            self.assertEqual(result['reviews'],[])
            self.assertFalse(result['adjudicated'])


class NativeNormalizationTests(unittest.TestCase):
    def test_native_order_is_incidental_but_witness_and_measurements_are_retained(self):
        from agent_ee.benchmark_run import canonical_native
        first={'violations':[{'items':[{'uuid':'b'},{'uuid':'a'}],'actual':1.2},{'items':[{'uuid':'c'}]}]}
        second={'violations':[{'items':[{'uuid':'c'}]},{'items':[{'uuid':'a'},{'uuid':'b'}],'actual':1.2}]}
        self.assertEqual(canonical_native(first),canonical_native(second))
        second['violations'][1]['actual']=1.3
        self.assertNotEqual(canonical_native(first),canonical_native(second))
        second['violations'][1]['actual']=1.2
        second['violations'][1]['items'][0]['uuid']='different'
        self.assertNotEqual(canonical_native(first),canonical_native(second))


class ControlWitnessTests(unittest.TestCase):
    def test_preexisting_target_violation_with_another_witness_is_not_a_seed_hit(self):
        from agent_ee.benchmark_run import classify
        case={'id':'owned-seed','source_selectors':[{'pcb':{'footprint_uuid':'f'}}]}
        expected={'kind':'drc','predicate':{'kind':'schematic_parity','pin':'1'}}
        def record(witness):
            return {'check':{'execution':'ok','outcome':'defect'},'pcb_pad_uuids':{'f':{'1':['pad']}},
                'native':{'schematic_parity':[{'type':'net_conflict','description':witness,'items':[{'uuid':'pad'}]}]}}
        self.assertEqual(classify(expected,record('new wording'),case,record('existing flaw')),'control_unresolved')


class ConnectionSemanticsTests(unittest.TestCase):
    """KiCad may name another item pair, or another spanning tree, for the same split net."""
    islands = {'pad':('N','P'),'a1':('N','A'),'a2':('N','A'),'b1':('N','B'),'m':('M','X'),'zone':None}

    @staticmethod
    def native(*pairs):
        return {'unconnected_items':[{'type':'unconnected_items','items':[{'uuid':a},{'uuid':b}]} for a,b in pairs]}

    def test_other_witness_items_or_tree_over_the_same_islands_match(self):
        from agent_ee.benchmark_run import connection_semantics
        first = connection_semantics(self.native(('pad','a1'),('a2','b1')),self.islands)
        self.assertEqual(first,{'N':['A','B','P']})
        self.assertEqual(connection_semantics(self.native(('pad','b1'),('b1','a2')),self.islands),first)
        self.assertEqual(connection_semantics(self.native(),self.islands),{})

    def test_unresolved_witnesses_are_never_equal(self):
        from agent_ee.benchmark_run import connection_semantics
        for pairs in ([('pad','zone')],[('pad','unknown')],[('a1','a2')],[('pad','m')],
                      [('pad','a1')],[('pad','a1'),('a2','pad')]):
            with self.subTest(pairs=pairs):
                semantics = connection_semantics(self.native(*pairs),self.islands)
                self.assertTrue(semantics is None or semantics!={'N':['A','B','P']})
        self.assertIsNone(connection_semantics(None,self.islands))
        self.assertIsNone(connection_semantics(self.native(('pad','a1'),('a2','b1'),('pad','b1')),self.islands))


def owned_split_board(directory):
    """One net in three saved-copper islands: a lone pad and two separate tracks."""
    import pcbnew
    from pathlib import Path
    board = pcbnew.BOARD()
    net = pcbnew.NETINFO_ITEM(board,'N');board.Add(net)
    footprint = pcbnew.FOOTPRINT(board);footprint.SetReference('J1');board.Add(footprint)
    pad = pcbnew.PAD(footprint);pad.SetNumber('1');pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    layers = pcbnew.LSET();layers.AddLayer(pcbnew.F_Cu);pad.SetLayerSet(layers)
    pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1),pcbnew.FromMM(1)));pad.SetNet(net);footprint.Add(pad)
    tracks = []
    for y in (5,10):
        for x in ((0,4),(4,8)):
            track = pcbnew.PCB_TRACK(board);track.SetLayer(pcbnew.F_Cu);track.SetWidth(pcbnew.FromMM(.25))
            track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x[0]),pcbnew.FromMM(y)))
            track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x[1]),pcbnew.FromMM(y)));track.SetNet(net);board.Add(track)
            tracks.append(track)
    # An outline keeps DRC from reporting the board itself, whose item UUID
    # KiCad regenerates on every load.
    outline = pcbnew.PCB_SHAPE(board);outline.SetShape(pcbnew.SHAPE_T_RECT);outline.SetLayer(pcbnew.Edge_Cuts)
    outline.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(-5),pcbnew.FromMM(-5)))
    outline.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(15),pcbnew.FromMM(15)));board.Add(outline)
    path = Path(directory)/'owned.kicad_pcb';pcbnew.SaveBoard(str(path),board)
    text = path.read_text()
    from agent_ee.benchmark import parse, children, one, uid, values
    root = parse(text)
    pads = [uid(p) for f in children(root,'footprint') for p in children(f,'pad')]
    # Saved order is not creation order: name each segment by its start point.
    segments = {tuple(values(one(s,'start'))):uid(s) for s in children(root,'segment')}
    return text, pads[0], [segments[k] for k in (('0','5'),('4','5'),('0','10'),('4','10'))]


class NativeConnectionWitnessTests(unittest.TestCase):
    def test_native_islands_equate_witness_choice_but_keep_other_differences(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import tempfile
        from agent_ee.benchmark import copper_islands
        from agent_ee.benchmark_run import compare_records
        with tempfile.TemporaryDirectory() as tmp:
            text, pad, (a1,a2,b1,b2) = owned_split_board(tmp)
        islands = copper_islands(text)
        self.assertEqual(islands[a1],islands[a2])
        self.assertNotEqual(islands[a1],islands[b1])
        self.assertEqual(len({islands[pad],islands[a1],islands[b1]}),3)
        def record(*pairs, violations=()):
            native = ConnectionSemanticsTests.native(*pairs)
            native['violations'] = list(violations);native['schematic_parity'] = []
            return {'d':{'check':{'kind':'drc','execution':'ok','details':{'unconnected_items':len(pairs)}},'native':native}}
        first = record((pad,a1),(a2,b1))
        compared = compare_records(first,record((pad,b2),(b1,a1)),text)
        self.assertFalse(compared['exact'])
        self.assertTrue(compared['semantic'],compared)
        self.assertEqual(compared['differences'][0]['native_sections'],['unconnected_items'])
        self.assertTrue(compared['differences'][0]['first_only']['unconnected_items'])
        self.assertTrue(compare_records(first,record((pad,a1),(a2,b1)),text)['exact'])
        for other,board in ((record((pad,a1),(a2,b1),violations=[{'type':'clearance','items':[]}]),text),
                            (record((pad,b2),(b1,a1)),None),(record((pad,a1)),text)):
            with self.subTest(other=other,board=board is None):
                self.assertFalse(compare_records(first,other,board)['semantic'])


def owned_native_run(root):
    """One owned checks-only review with native ERC and DRC, in a benchmark run layout.

    The board is the owned split net, so DRC retains unconnected-item witnesses.
    """
    import json
    import shutil
    from pathlib import Path
    from agent_ee.benchmark_run import review_case
    from agent_ee.storage import digest
    fixture = Path(__file__).resolve().parents[1]/'fixtures/hardware-interface'
    source = root/'source';source.mkdir()
    for name in ('interface.kicad_sch','requirements.md'):
        shutil.copyfile(fixture/name,source/name)
    text = owned_split_board(source)[0]
    (source/'interface.kicad_pcb').write_text(text);(source/'owned.kicad_pcb').unlink()
    checks = [{'id':'owned-netlist','kind':'netlist'},{'id':'owned-erc','kind':'erc'},{'id':'owned-drc','kind':'drc'}]
    scope = {'schema':'agent-ee.scope/2','name':'owned','scope':'Owned native rerun fixture.',
        'schematic':'interface.kicad_sch','board':'interface.kicad_pcb',
        'inputs':[{'path':p,'at':p} for p in ('interface.kicad_sch','interface.kicad_pcb','requirements.md')],
        'requirements':['requirements.md'],'checks':checks,
        'baseline':{'requirements':[{'id':'owned','statement':'Owned native checks.','source':{'path':'requirements.md','locator':'owned'},
            'checks':[c['id'] for c in checks]}],'targets':['requirements.md'],'reference_architectures':['requirements.md']}}
    (source/'scope.json').write_text(json.dumps(scope))
    first = root/'first';(first/'runs').mkdir(parents=True)
    run = first/'runs'/'owned-control'
    review_case(source/'scope.json',run)
    (first/'request.json').write_text(json.dumps({'schema':'agent-ee.benchmark-request/1'}))
    (first/'runs/index.json').write_text(json.dumps({'records':[{'id':'owned-control','run':str(run),
        'result_sha256':digest(run/'result.json')}],'model_pass':False}))
    return first, run


class NativeRerunTests(unittest.TestCase):
    def test_reruns_from_captured_inputs_reproduce_retained_native_records(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import native_rerun
        with tempfile.TemporaryDirectory() as tmp:
            first, run = owned_native_run(Path(tmp))
            retained = json.loads((run/'result.json').read_text())
            self.assertEqual({c['id']:c['execution'] for c in retained['checks']},
                             {'owned-netlist':'ok','owned-erc':'ok','owned-drc':'ok'})
            native = json.loads((run/'work/owned-drc/drc.json').read_text())
            self.assertEqual(len(native['unconnected_items']),2)
            summary = native_rerun(first,Path(tmp)/'rerun',repeats=2)
            self.assertEqual((summary['native_semantic_cases'],summary['semantic_cases'],summary['all_cases']),(1,1,1),summary)
            for repeat in summary['cases'][0]['repeats']:
                # A different witness choice is reported, and only for unconnected items.
                for difference in repeat['differences']:
                    self.assertEqual(difference['native_sections'],['unconnected_items'],difference)
                    self.assertTrue(difference['semantic_equal'])
            self.assertTrue(summary['same_native_runtime'])
            self.assertEqual([r['repeat'] for r in summary['cases'][0]['repeats']],[1,2])
            self.assertTrue((Path(tmp)/'rerun/repeat-2/owned-control/work/owned-drc/drc.json').is_file())
            self.assertEqual(json.loads((Path(tmp)/'rerun/native-rerun.json').read_text()),summary)
            with self.assertRaises(FileExistsError):
                native_rerun(first,Path(tmp)/'rerun')
            board = run/'inputs/interface.kicad_pcb'
            board.write_text(board.read_text()+'\n')
            # The retained artifact inventory refuses a changed captured input first.
            with self.assertRaisesRegex(ValueError,'retained benchmark artifacts changed'):
                native_rerun(first,Path(tmp)/'tampered-input')
            (run/'result.json').write_text(json.dumps({**retained,'outcome':'changed'}))
            with self.assertRaisesRegex(ValueError,'retained result changed'):
                native_rerun(first,Path(tmp)/'tampered-result')


class ControlAdjudicationTests(unittest.TestCase):
    def control(self, root, *, project_table=None, hardware=None):
        import json
        from pathlib import Path
        run = Path(root)/'runs'/'owned-control'
        (run/'work/erc/kicad-config/10.0').mkdir(parents=True);(run/'work/drc').mkdir(parents=True)
        (run/'inputs/hw').mkdir(parents=True)
        (run/'work/erc/kicad-config/10.0/sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "Global")(type "KiCad")(uri "x")(options "")(descr "")))')
        if project_table:
            (run/'inputs/hw/sym-lib-table').write_text(project_table)
        def item(uuid):
            return {'description':'item','pos':{'x':1.0,'y':2.0},'uuid':uuid}
        missing = "The current configuration does not include the symbol library '{}'"
        erc = {'coordinate_units':'mm','kicad_version':'10.0.7','sheets':[{'path':'/','uuid_path':'/root','violations':[
            {'type':'lib_symbol_issues','severity':'warning','description':missing.format('Custom'),'items':[item('00000000-0000-0000-0000-000000000001')]},
            {'type':'lib_symbol_issues','severity':'warning','description':missing.format('Project'),'items':[item('00000000-0000-0000-0000-000000000002')]},
            {'type':'lib_symbol_issues','severity':'warning','description':missing.format('Global'),'items':[item('00000000-0000-0000-0000-000000000003')]},
            {'type':'lib_symbol_issues','severity':'warning','description':missing.format('Custom')+' (changed wording)','items':[]},
            {'type':'footprint_link_issues','severity':'warning','description':missing.format('Custom'),'items':[]},
            {'type':'power_pin_not_driven','severity':'error','description':'Input Power pin not driven by any Output Power pins','items':[]}]}]}
        drc = {'coordinate_units':'mm','kicad_version':'10.0.7','violations':[
            {'type':'lib_footprint_issues','severity':'warning','description':"The current configuration does not include the footprint library 'Stock'",'items':[]},
            {'type':'clearance','severity':'error','description':'Clearance violation','items':[]}],
            'unconnected_items':[],'schematic_parity':[]}
        (run/'work/erc/erc.json').write_text(json.dumps(erc));(run/'work/drc/drc.json').write_text(json.dumps(drc))
        checks = [{'id':'erc','kind':'erc','execution':'ok'},{'id':'drc','kind':'drc','execution':'ok'},
            {'id':'interface','kind':'hardware_interface','execution':'ok','details':{'invariants':hardware or [
                {'id':'a','status':'satisfied'},{'id':'b','status':'satisfied'},{'id':'c','status':'violation'},
                {'id':'d','status':'not_evaluated'}]}},
            {'id':'skipped','kind':'hardware_interface','execution':'unsupported','details':{'invariants':[{'id':'e','status':'not_evaluated'}]}}]
        (run/'result.json').write_text(json.dumps({'checks':checks}))
        (run/'candidate.json').write_text(json.dumps({'scope':{'schematic':'hw/owned.kicad_sch','board':'hw/owned.kicad_pcb'}}))
        return {'boards':[{'id':'owned'}]}

    def test_only_fixed_rules_decide_and_open_records_bound_the_share(self):
        import tempfile
        from agent_ee.benchmark_run import adjudicate_controls
        with tempfile.TemporaryDirectory() as tmp:
            table = '(sym_lib_table (version 7) (lib (name "Project")(type "KiCad")(uri "${KIPRJMOD}/p.kicad_sym")(options "")(descr "")))'
            result = adjudicate_controls(self.control(tmp,project_table=table),tmp)
        erc, drc, hardware = (result['per_kind'][k] for k in ('erc','drc','hardware_interface'))
        # Only the absent nickname with the exact native wording and matching type is decided.
        self.assertEqual((erc['records'],erc['false_alarm'],erc['unadjudicated']),(6,1,5))
        self.assertIsNone(erc['rate'])
        self.assertEqual(erc['share_bounds'],[1/6,1.0])
        self.assertEqual((drc['false_alarm'],drc['unadjudicated'],drc['share_bounds']),(1,1,[0.5,1.0]))
        self.assertEqual((hardware['true_negative'],hardware['false_alarm'],hardware['not_evaluated'],
                          hardware['checks_not_ok'],hardware['negative_opportunities']),(2,1,2,1,3))
        self.assertAlmostEqual(hardware['rate'],1/3)
        rows = result['controls'][0]['records']
        decided = [r for r in rows if r['verdict']=='false_alarm' and r['kind']=='erc']
        self.assertEqual([(r['library'],r['raw_locator']) for r in decided],[('Custom','/sheets/0/violations/0')])
        self.assertRegex(decided[0]['location_hash'],'^[a-f0-9]{64}$')
        self.assertTrue(all(r['rule']=='requires-judgement' for r in rows if r['verdict']=='unadjudicated'))

    def test_without_open_records_native_rate_is_exact_and_all_satisfied_is_zero(self):
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import adjudicate_controls
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.control(tmp,hardware=[{'id':'a','status':'satisfied'}])
            path = Path(tmp)/'runs/owned-control/work/erc/erc.json'
            erc = json.loads(path.read_text());erc['sheets'][0]['violations'] = erc['sheets'][0]['violations'][:1]
            path.write_text(json.dumps(erc))
            result = adjudicate_controls(manifest,tmp)
        self.assertEqual(result['per_kind']['erc']['rate'],1.0)
        self.assertEqual(result['per_kind']['hardware_interface']['rate'],0.0)


class DependencyClosureTests(unittest.TestCase):
    SCHEMATIC = ('(kicad_sch (lib_symbols (symbol "Device:R") (symbol "Custom:Part") (symbol "Local"))'
                 '(symbol (lib_id "Device:R") (uuid "s1")) (symbol (lib_id "Custom:Part") (uuid "s2"))'
                 '(symbol (lib_id "Other:Part") (lib_name "Local") (uuid "s3")))')
    PCB = ('(kicad_pcb (footprint "Resistor_SMD:R_0402" (model "${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R.step"))'
           '(footprint "owned:J" (model "${KIPRJMOD}/models/J.step")) (footprint "Logo"))')
    TABLE = '(fp_lib_table (version 7) (lib (name "owned")(type "KiCad")(uri "${KIPRJMOD}/owned.pretty")(options "")(descr "")))'

    def board(self, root, schematic=SCHEMATIC):
        from agent_ee.benchmark import sha
        files = {'hw/b.kicad_sch':schematic,'hw/b.kicad_pcb':self.PCB,'hw/fp-lib-table':self.TABLE}
        for rel,text in files.items():
            (root/rel).parent.mkdir(parents=True,exist_ok=True);(root/rel).write_text(text)
        return {'native_files':{'schematic':'hw/b.kicad_sch','pcb':'hw/b.kicad_pcb'},
            'native_file_sha256':{r:sha(files[r].encode()) for r in ('hw/b.kicad_sch','hw/b.kicad_pcb')},
            'captured_dependency_sha256':{'hw/fp-lib-table':sha(self.TABLE.encode())}}

    def test_inventory_names_every_library_model_root_and_table(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import design_dependencies, library_origin
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            found = design_dependencies(self.board(root),root)
            stock = root/'stock';(stock/'symbols').mkdir(parents=True);(stock/'footprints/Resistor_SMD.pretty').mkdir(parents=True)
            (stock/'symbols/Device.kicad_sym').write_text('(kicad_symbol_lib)')
            origins = [library_origin('symbol',n,found['project_tables'],stock) for n in found['symbol_libraries']]
            footprints = [library_origin('footprint',n,found['project_tables'],stock) for n in found['footprint_libraries']]
        self.assertEqual(found['symbol_libraries'],['Custom','Device','Other'])
        self.assertEqual(origins,['third-party-custom','kicad-stock','third-party-custom'])
        self.assertEqual(found['footprint_libraries'],['','Resistor_SMD','owned'])
        self.assertEqual(footprints,['none','kicad-stock','project'])
        self.assertEqual(found['model_roots'],['${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes','${KIPRJMOD}/models'])
        self.assertEqual(found['project_tables'],{'hw/fp-lib-table':{'owned':'${KIPRJMOD}/owned.pretty'}})

    def test_missing_definition_hierarchy_and_changed_bytes_are_refused(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import design_dependencies
        cases = [(self.SCHEMATIC.replace('(symbol "Custom:Part") ',''),'no embedded definition'),
                 (self.SCHEMATIC[:-1]+'(sheet (uuid "h")))','flat boards only')]
        for schematic,message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,message):
                    design_dependencies(self.board(Path(tmp),schematic),Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            board = self.board(Path(tmp));(Path(tmp)/'hw/fp-lib-table').write_text(self.TABLE+' ')
            with self.assertRaisesRegex(ValueError,'captured library table changed'):
                design_dependencies(board,Path(tmp))

    def test_committed_closure_pins_manifest_runtime_and_licence_classes(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import LIBRARY_LICENCES, NATIVE_RUNTIME, load_manifest, tool_licences, verify_closure
        from agent_ee.storage import digest
        seeded = Path(__file__).resolve().parents[1]/'benchmarks/seeded'
        closure = json.loads((seeded/'dependency-closure.json').read_text())
        manifest = load_manifest(seeded/'expectations.json')
        self.assertEqual(closure['manifest_sha256'],digest(seeded/'expectations.json'))
        self.assertEqual(closure['image_id'],manifest['image_id'])
        self.assertEqual([b['board_id'] for b in closure['boards']],[b['id'] for b in manifest['boards']])
        for board in closure['boards']:
            for row in board['symbol_libraries']+board['footprint_libraries']:
                self.assertEqual(row['licence_class'],LIBRARY_LICENCES[row['origin']])
        tools = {t['name']:t for t in closure['tools']}
        self.assertTrue(set(NATIVE_RUNTIME)<=set(tools))
        # Only the container runtime that launches the image is outer; it is absent inside the image.
        self.assertEqual({n for n,t in tools.items() if t['licence_evidence']['location']=='outer-host'},{'docker'})
        docker = tools['docker']
        outer = Path(docker['path']).exists() or Path(docker['licence_evidence']['path']).exists()
        expected = {n:'verified' for n in tools}
        if not outer:
            expected['docker'] = 'outer-host-absent'
        self.assertEqual(tool_licences(closure['tools']),expected)
        for tool in tools.values():
            self.assertTrue(tool['licence_class'])
            if tool['licence_evidence']['location']=='runtime' or outer:
                self.assertEqual(digest(tool['licence_evidence']['path']),tool['licence_evidence']['sha256'],tool['name'])
        self.assertEqual(closure['alternate_library']['record_sha256'],digest(seeded/'alternate-library.json'))
        self.assertFalse(closure['redistribution']['benchmark_publishes'])
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp)/'closure.json'
            changed.write_text(json.dumps({**closure,'manifest_sha256':'0'*64}))
            with self.assertRaisesRegex(ValueError,'different manifest'):
                verify_closure(seeded/'expectations.json',Path(tmp)/'cache',changed)


class ToolLicenceTests(unittest.TestCase):
    def tool(self, root, name, location, text='licence\n', binary=True):
        from pathlib import Path
        from agent_ee.benchmark import sha
        evidence = Path(root)/(name.strip('/').replace('/','-')+'.copyright');evidence.write_text(text)
        path = Path(root)/(name.strip('/').replace('/','-')+'.bin')
        if binary:
            path.write_text('binary')
        return {'name':name,'path':str(path),'sha256':'1'*64,'licence_class':'GPL (copyleft)',
                'licence_evidence':{'location':location,'path':str(evidence),'sha256':sha(b'licence\n')}}

    def test_runtime_and_present_outer_evidence_verify_and_absent_outer_is_explicit(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import tool_licences
        with tempfile.TemporaryDirectory() as tmp:
            native = self.tool(tmp,'kicad-cli','runtime');docker = self.tool(tmp,'docker','outer-host')
            self.assertEqual(tool_licences([native,docker]),{'kicad-cli':'verified','docker':'verified'})
            Path(docker['path']).unlink();Path(docker['licence_evidence']['path']).unlink()
            self.assertEqual(tool_licences([native,docker]),{'kicad-cli':'verified','docker':'outer-host-absent'})

    def test_tampered_absent_unscoped_or_rescoped_evidence_is_refused(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import tool_licences
        with tempfile.TemporaryDirectory() as tmp:
            tampered = self.tool(tmp,'bwrap','runtime',text='changed\n')
            absent = self.tool(tmp,'/usr/bin/python3','runtime');Path(absent['licence_evidence']['path']).unlink()
            native_outer = self.tool(tmp,'pcbnew','outer-host',binary=False)
            Path(native_outer['licence_evidence']['path']).unlink()
            outer_tampered = self.tool(tmp,'docker','outer-host',text='changed\n',binary=False)
            outer_licence_only = self.tool(tmp,'podman','outer-host',binary=False)
            Path(outer_licence_only['licence_evidence']['path']).write_text('changed\n')
            outer_binary_only = self.tool(tmp,'nerdctl','outer-host')
            Path(outer_binary_only['licence_evidence']['path']).unlink()
            unscoped = self.tool(tmp,'git','runtime');del unscoped['licence_evidence']['location']
            unknown = self.tool(tmp,'git','host')
            relative = self.tool(tmp,'git','runtime');relative['licence_evidence']['path'] = 'copyright'
            unclassed = self.tool(tmp,'git','runtime');unclassed['licence_class'] = ''
            cases = [(tampered,'missing or changed: bwrap'),(absent,'missing or changed: /usr/bin/python3'),
                     (native_outer,'must be verified in the runtime: pcbnew'),
                     (outer_tampered,'missing or changed: docker'),(outer_licence_only,'missing or changed: podman'),
                     (outer_binary_only,'missing or changed: nerdctl'),
                     (unscoped,'evidence missing: git'),(unknown,'evidence missing: git'),
                     (relative,'evidence missing: git'),(unclassed,'evidence missing: git')]
            for tool,message in cases:
                with self.subTest(message=message), self.assertRaisesRegex(ValueError,message):
                    tool_licences([tool])


class ClosureEvidenceTests(unittest.TestCase):
    """Code-owned tool locations, positive image context and digested font licences."""

    def closure(self, root):
        import json
        from pathlib import Path
        from agent_ee.benchmark import sha
        from agent_ee.benchmark_run import IMAGE_CONTEXT_RECORD, TOOL_LOCATIONS
        root = Path(root);tools = [];packages = []
        for name,location in TOOL_LOCATIONS.items():
            stem = name.strip('/').replace('/','-')
            (root/(stem+'.bin')).write_text('binary '+name);(root/(stem+'.copyright')).write_text('licence\n')
            tools.append({'name':name,'path':str(root/(stem+'.bin')),'sha256':sha(('binary '+name).encode()),
                'licence_class':'GPL (copyleft)','licence_evidence':{'location':location,
                'path':str(root/(stem+'.copyright')),'sha256':sha(b'licence\n')}})
        for name in ('fonts-dejavu-core','fontconfig'):
            (root/(name+'.copyright')).write_text('font licence\n')
            packages.append({'package':name,'version':'1','licence_class':'permissive','licence_evidence':
                {'location':'image','path':str(root/(name+'.copyright')),'sha256':sha(b'font licence\n')}})
        (root/'dockerenv').write_text('');(root/'image-context.json').write_text(json.dumps(IMAGE_CONTEXT_RECORD))
        paths = {'container':str(root/'dockerenv'),'record':str(root/'image-context.json')}
        return {'tools':tools,'fonts':{'packages':packages}}, paths

    def without(self, path):
        from pathlib import Path
        Path(path).unlink()

    def test_outer_host_absent_needs_positive_image_context_otherwise_unknown(self):
        import copy
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import closure_evidence
        with tempfile.TemporaryDirectory() as tmp:
            closure,paths = self.closure(tmp);tools = {t['name']:t for t in closure['tools']}
            result = closure_evidence(closure,**paths)
            self.assertEqual(set(result['licence_evidence'].values()),{'verified'})
            self.assertEqual(result['image_context'],{'established':True,'problems':[]})
            self.assertEqual(result['unverified'],[])
            self.without(tools['docker']['path']);self.without(tools['docker']['licence_evidence']['path'])
            result = closure_evidence(closure,**paths)
            self.assertEqual(result['licence_evidence']['docker'],'outer-host-absent')
            self.assertEqual(result['unverified'],['docker'])
            # A host without docker is not the image: each missing piece of evidence leaves docker unknown.
            Path(tmp,'other.bin').write_text('other')
            cases = {'container marker':dict(paths,container=str(Path(tmp)/'absent')),
                     'record absent':dict(paths,record=str(Path(tmp)/'absent'))}
            for label,changed in cases.items():
                with self.subTest(label=label):
                    result = closure_evidence(closure,**changed)
                    self.assertEqual(result['licence_evidence']['docker'],'unknown')
                    self.assertFalse(result['image_context']['established'])
                    self.assertEqual(result['unverified'],['docker'])
            Path(paths['record']).write_text('{"schema": "agent-ee.image-context/1", "outer_host_tools": []}')
            self.assertEqual(closure_evidence(closure,**paths)['licence_evidence']['docker'],'unknown')
            Path(paths['record']).write_text('not json')
            self.assertEqual(closure_evidence(closure,**paths)['licence_evidence']['docker'],'unknown')
        for runtime in ('kicad-cli','pcbnew','bwrap','/usr/bin/python3','git'):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as tmp:
                closure,paths = self.closure(tmp);tools = {t['name']:t for t in closure['tools']}
                self.without(tools['docker']['path']);self.without(tools['docker']['licence_evidence']['path'])
                changed = copy.deepcopy(closure)
                next(t for t in changed['tools'] if t['name']==runtime)['sha256'] = '0'*64
                result = closure_evidence(changed,**paths)
                self.assertEqual(result['licence_evidence']['docker'],'unknown')
                self.assertEqual(result['image_context']['problems'],['runtime binary differs from closure: '+runtime])

    def test_relabelled_omitted_or_extra_tools_are_refused_by_policy(self):
        import copy
        import tempfile
        from agent_ee.benchmark_run import closure_evidence, tool_licences
        with tempfile.TemporaryDirectory() as tmp:
            closure,paths = self.closure(tmp)
            def relabel(name,location):
                changed = copy.deepcopy(closure)
                next(t for t in changed['tools'] if t['name']==name)['licence_evidence']['location'] = location
                return changed
            relabelled = [(relabel('git','outer-host'),'must be verified in the runtime: git'),
                          (relabel('docker','runtime'),'must be verified on the outer host: docker'),
                          (relabel('kicad-cli','outer-host'),'must be verified in the runtime: kicad-cli')]
            for changed,message in relabelled:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError,message):
                        tool_licences(changed['tools'])
                    with self.assertRaisesRegex(ValueError,message):
                        closure_evidence(changed,**paths)
            extra = dict(closure['tools'][0],name='podman')
            sets = {'omit git':[t for t in closure['tools'] if t['name']!='git'],
                    'omit docker':[t for t in closure['tools'] if t['name']!='docker'],
                    'extra tool':closure['tools']+[extra],
                    'duplicate':closure['tools']+[closure['tools'][0]]}
            for label,tools in sets.items():
                with self.subTest(label=label), self.assertRaisesRegex(ValueError,'tool set differs from policy'):
                    closure_evidence(dict(closure,tools=tools),**paths)

    def test_font_licences_are_digested_where_present_and_required_in_the_image(self):
        import copy
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import closure_evidence
        with tempfile.TemporaryDirectory() as tmp:
            closure,paths = self.closure(tmp);font = closure['fonts']['packages'][0]
            host = dict(paths,container=str(Path(tmp)/'absent'))
            self.assertEqual(closure_evidence(closure,**host)['font_licence_evidence'],
                             {'fonts-dejavu-core':'verified','fontconfig':'verified'})
            Path(font['licence_evidence']['path']).write_text('changed\n')
            for label,context in (('host',host),('image',paths)):
                with self.subTest(label=label), self.assertRaisesRegex(ValueError,'font licence evidence missing or changed: fonts-dejavu-core'):
                    closure_evidence(closure,**context)
            self.without(font['licence_evidence']['path'])
            result = closure_evidence(closure,**host)
            self.assertEqual(result['font_licence_evidence']['fonts-dejavu-core'],'unknown')
            self.assertEqual(result['unverified'],['fonts-dejavu-core'])
            with self.assertRaisesRegex(ValueError,'font licence evidence missing or changed: fonts-dejavu-core'):
                closure_evidence(closure,**paths)
            for key,value in (('location','runtime'),('sha256','0'),('path','copyright')):
                changed = copy.deepcopy(closure);changed['fonts']['packages'][1]['licence_evidence'][key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError,'font licence evidence missing: fontconfig'):
                    closure_evidence(changed,**host)
            changed = copy.deepcopy(closure);changed['fonts']['packages'].append(changed['fonts']['packages'][1])
            with self.assertRaisesRegex(ValueError,'font package listed twice: fontconfig'):
                closure_evidence(changed,**host)

    def test_committed_closure_evidence_where_the_suite_runs(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import json
        from pathlib import Path
        from agent_ee.benchmark_run import CONTAINER_MARKER, IMAGE_CONTEXT, closure_evidence
        from agent_ee.storage import digest
        closure = json.loads((Path(__file__).resolve().parents[1]/'benchmarks/seeded/dependency-closure.json').read_text())
        result = closure_evidence(closure)
        tools = {t['name']:t for t in closure['tools']}
        docker = tools['docker']
        outer = Path(docker['path']).exists() or Path(docker['licence_evidence']['path']).exists()
        image = (Path(CONTAINER_MARKER).is_file() and Path(IMAGE_CONTEXT).is_file() and
                 all(digest(t['path'])==t['sha256'] for n,t in tools.items() if n!='docker'))
        self.assertEqual(result['image_context']['established'],image)
        expected = {n:'verified' for n in tools}
        if not outer:
            expected['docker'] = 'outer-host-absent' if image else 'unknown'
        self.assertEqual(result['licence_evidence'],expected)
        fonts = {p['package']:'verified' if Path(p['licence_evidence']['path']).is_file() else 'unknown'
                 for p in closure['fonts']['packages']}
        self.assertEqual(result['font_licence_evidence'],fonts)
        if image:
            self.assertEqual(set(fonts.values()),{'verified'})


class FontSubstitutionTests(unittest.TestCase):
    def test_console_substitutions_are_read_exactly(self):
        import tempfile
        from pathlib import Path
        from agent_ee.benchmark_run import font_substitutions
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'work/drc').mkdir(parents=True)
            (Path(tmp)/'work/drc/console.log').write_text(
                "Found 6 violations\n18:06:03: Font 'Arial' not found; substituting 'DejaVu Sans Book'.\n"
                "18:06:03: Font 'Futura' not found; substituting 'DejaVu Sans Bold'.\nFont Arial missing\n")
            self.assertEqual(font_substitutions(tmp,'drc'),{'Arial':'DejaVu Sans Book','Futura':'DejaVu Sans Bold'})
            self.assertEqual(font_substitutions(tmp,'erc'),{})


class ContainerCommandTests(unittest.TestCase):
    def test_image_launch_keeps_network_init_and_read_only_mounts(self):
        from pathlib import Path
        from agent_ee.benchmark_run import container_command
        image = 'sha256:'+'a'*64
        command = container_command('agent-ee-21-x',image,Path('/src'),[(Path('/in'),True),(Path('/out'),False)],['_execute','/in','/out/runs'])
        self.assertEqual(command[:6],['docker','run','--rm','--init','--name','agent-ee-21-x'])
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertIn('apparmor=agent-ee-container',command)
        volumes = [command[i+1] for i,a in enumerate(command) if a=='-v']
        self.assertEqual(volumes,['/src:/opt/agent-ee:ro','/in:/in:ro','/out:/out'])
        self.assertEqual(command[command.index(image)+1:],['/opt/agent-ee/scripts/seeded_benchmark.py','_execute','/in','/out/runs'])
