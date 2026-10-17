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
        expected = {'kind':'drc','predicate':{'kind':'schematic_parity'}}
        def rec(uuid):
            return {'check':{'execution':'ok','outcome':'defect'},'native':{
                'schematic_parity':[{'type':'net_conflict','items':[{'uuid':uuid}]}]}}
        self.assertEqual(classify(expected,rec('other'),self.case,rec('other')),'miss')
        self.assertEqual(classify(expected,rec('target'),self.case,rec('other')),'detection')
        self.assertEqual(classify(expected,rec('target'),self.case,rec('target')),'miss')

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
