"""Strict prepared-job contracts over authored data; no native execution claims."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import placement_job, placement_contract

MM = 1_000_000


def fixture():
    footprints = []
    for index, (ref, x) in enumerate((('U1', 2), ('C1', 4), ('TP1', 8))):
        footprints.append(dict(ref=ref, uuid=f'fp-{index}', position_nm=[x * MM, 2 * MM],
            rotation_deg=0, side='F', locked=False,
            courtyards={'F': {'status': 'proven', 'polygons': [[[x*MM-MM//2, MM],
                [x*MM+MM//2, MM], [x*MM+MM//2, 3*MM], [x*MM-MM//2, 3*MM]]]}},
            pads=[dict(number=str(i+1), uuid=f'pad-{index}-{i}', net=net,
                       position_nm=[x*MM, (2+i)*MM], copper_centroid_offset_nm=[[0, 1], [0, 1]])
                  for i, net in enumerate(('VCC', 'GND'))] if ref != 'TP1' else []))
    capture = dict(schema='agent-ee.placement-probe/1', board_sha256='a'*64, footprints=footprints)
    quote = 'Authored engineering maximum: 3 mm'
    citation = dict(kind='captured_source', path='limits.txt', locator='line 1', quote=quote,
                    sha256=hashlib.sha256(quote.encode()).hexdigest())
    def pair(pad, net):
        return {'from': dict(reference='U1', pad=pad, net=net),
                'to': dict(reference='C1', pad=pad, net=net), 'max_distance_nm': 3*MM,
                'citation': quote}
    supply, returned = pair('1', 'VCC'), pair('2', 'GND')
    for p in (supply, returned):
        p['provenance'] = {'class': 'engineering', 'basis': 'authored fixture', 'citation': citation}
    relationships = dict(schema='agent-ee.electrical-locality/1', source_records={'limits.txt': quote},
                         obligations=[dict(id='support', metric='pad-centre-to-centre',
                                           supply=supply, **{'return': returned})])
    contract = dict(schema='agent-ee.block-contract/1',
        allocation=dict(schema='agent-ee.block-allocation/2', board_sha256='a'*64,
            blocks=[dict(id='device', references=['U1', 'C1'], anchor={'member': 'U1'}, interfaces_to_fixed=None)],
            fixed=[], shared=[dict(reference='TP1', role='test point')]),
        eligibility=dict(schema='fome-mc.side-eligibility/1', sources={'board_sha256':'a'*64},
            default={'faces':['F']}, blocks={'device': {'eligible_faces':['F']}},
            shared={'TP1': {'eligible_faces':['F']}}),
        relationships=relationships, templates=[], thickness_nm=1600000)
    normalized = placement_job.normalize_intent({'electrical_locality': relationships},
        {'groups':[dict(name='device', anchor='U1', refs=['U1', 'C1'])]})
    records = [dict(uuid=fp['uuid'], native_ref=fp['ref'], role='movable') for fp in footprints]
    return capture, contract, normalized, records


class PreparedBlockContractTests(unittest.TestCase):
    def test_complete_allocation_is_bound_and_omitted_shared_member_refuses(self):
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        self.assertEqual(placement_contract.grade(capture, binding)['status'], 'pass')
        missing = deepcopy(contract)
        missing['allocation']['shared'] = []
        with self.assertRaisesRegex(placement_job.JobRefusal, 'unallocated native footprints: TP1'):
            placement_contract.bind(capture, missing, normalized, records)

    def test_stale_source_or_divergent_effective_intent_refuses_binding(self):
        capture, contract, normalized, records = fixture()
        for change, message in (('source', 'original board'), ('policy', 'relationships differ'),
                                ('group', 'bridge membership differs'), ('anchor', 'anchor differs')):
            altered, intent = deepcopy(contract), deepcopy(normalized)
            if change == 'source':
                altered['allocation']['board_sha256'] = 'b'*64
            elif change == 'policy':
                intent['intent']['electrical_locality']['obligations'][0]['return']['max_distance_nm'] += 1
            elif change == 'group':
                intent['groups']['groups'][0]['refs'].remove('C1')
            else:
                intent['groups']['groups'][0]['anchor'] = 'C1'
            with self.subTest(change=change), self.assertRaisesRegex(placement_job.JobRefusal, message):
                placement_contract.bind(capture, altered, intent, records)

    def test_saved_contract_result_controls_incumbent_without_claiming_native_acceptance(self):
        # Authored state records exercise promotion only; no DRC result is fabricated.
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        before = deepcopy((capture, contract, normalized, records))
        legal = placement_contract.grade(capture, binding)
        moved = deepcopy(capture)
        moved['board_sha256'] = 'b'*64
        support = moved['footprints'][1]
        support['position_nm'][0] += 10*MM
        for pad in support['pads']:
            pad['position_nm'][0] += 10*MM
        for polygon in support['courtyards']['F']['polygons']:
            for point in polygon:
                point[0] += 10*MM
        defect = placement_contract.grade(moved, binding)
        self.assertEqual(defect['board_sha256'], 'b'*64)
        self.assertEqual(defect['original_board_sha256'], 'a'*64)
        self.assertEqual([(r['role'], r['distance_squared_nm2']) for r in defect['relationships']],
                         [('supply', 144*MM**2), ('return', 144*MM**2)])
        self.assertEqual(defect['status'], 'defect')
        qualitative = deepcopy(contract)
        qualitative['relationships'] = {'schema': 'agent-ee.electrical-locality/1', 'obligations': []}
        uncovered = deepcopy(normalized)
        del uncovered['intent']['electrical_locality']
        pending = placement_contract.grade(capture, placement_contract.bind(capture, qualitative, uncovered, records))
        self.assertEqual(pending['status'], 'unknown')
        self.assertTrue(any(r['kind'] == 'block_pin_relationships_missing' for r in pending['unknown']))
        entry = dict(status='checked', integrity='preserved', placement_introduced_errors=0,
                     engine_feasible=True, engine_violations=[], edge_intent={'status':'pass'},
                     engine_cost='10', block_contract=legal)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(placement_job.promote(tmp, entry))
            pointer = Path(tmp, 'incumbent.json').read_bytes()
            for failed in (defect, pending):
                self.assertFalse(placement_job.promote(tmp, dict(entry, engine_cost='0', block_contract=failed)))
                self.assertEqual(Path(tmp, 'incumbent.json').read_bytes(), pointer)
        self.assertEqual((capture, contract, normalized, records), before)

    def test_changed_binding_or_request_refuses_before_a_native_read(self):
        capture, contract, normalized, records = fixture()
        binding = placement_contract.bind(capture, contract, normalized, records)
        binding['contract']['thickness_nm'] += 1
        with self.assertRaisesRegex(placement_job.JobRefusal, 'binding changed after capture'):
            placement_contract.grade(capture, binding)
        requested = dict(normalized, block_contract=contract)
        for binding_value in (None, binding):
            with self.subTest(bound=binding_value is not None):
                if binding_value is not None:
                    requested['block_contract_binding'] = binding_value
                with self.assertRaisesRegex(placement_job.JobRefusal, 'matching original capture binding'):
                    placement_job.check_block_contract('/not-a-board', requested)
        self.assertEqual(placement_job.check_block_contract('/not-a-board', normalized), {'status':'not_declared'})

    def test_parent_requires_the_returned_report_to_bind_this_request_and_saved_bytes(self):
        capture, contract, normalized, records = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'authored-board-bytes')
            path.write_bytes(b'CPU hash-binding fixture, not a native board')
            board_sha = placement_job.digest(path)
            capture['board_sha256'] = board_sha
            contract['allocation']['board_sha256'] = board_sha
            contract['eligibility']['sources']['board_sha256'] = board_sha
            binding = placement_contract.bind(capture, contract, normalized, records)
            requested = dict(normalized, block_contract=contract, block_contract_binding=binding)
            report = placement_contract.grade(capture, binding)
            # The child process is an external native boundary; only its parent contract is under test.
            with patch.object(placement_job, '_native_read', return_value=report):
                self.assertEqual(placement_job.check_block_contract(path, requested), report)
            for key, wrong in (('board_sha256', 'b'*64), ('binding_sha256', 'b'*64),
                               ('contract_sha256', 'b'*64), ('original_board_sha256', 'b'*64),
                               ('schema', 'unrecognized'), ('status', 'unrecognized'), ('unknown', None)):
                changed = dict(report, **{key: wrong})
                with self.subTest(key=key), patch.object(placement_job, '_native_read', return_value=changed):
                    with self.assertRaisesRegex(placement_job.JobRefusal, 'missing or invalid output'):
                        placement_job.check_block_contract(path, requested)

    def test_malformed_contract_uses_the_typed_refusal_boundary(self):
        capture, _, normalized, records = fixture()
        with self.assertRaisesRegex(placement_job.JobRefusal, 'block contract:'):
            placement_contract.bind(capture, [], normalized, records)

    def test_explicit_uuid_roles_preserve_repeated_marks_and_empty_graphic_labels(self):
        capture, contract, normalized, records = fixture()
        for i, (label, role) in enumerate((('FID', 'fixed_copper_mark'), ('FID', 'fixed_copper_mark'),
                                         ('', 'silk_only'))):
            fp = deepcopy(capture['footprints'][2])
            fp.update(uuid=f'extra-{i}', ref=label, position_nm=[(10+i)*MM, 2*MM])
            capture['footprints'].append(fp)
            records.append(dict(uuid=fp['uuid'], native_ref=label, role=role))
            ref = 'uuid:' + fp['uuid']
            contract['allocation']['fixed'].append({'reference':ref})
            contract['eligibility'].setdefault('fixed', {})[ref] = {'eligible_faces':['F']}
        before = deepcopy(capture)
        binding = placement_contract.bind(capture, contract, normalized, records)
        report = placement_contract.grade(capture, binding)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['reference_aliases'], {'extra-0':'uuid:extra-0', 'extra-1':'uuid:extra-1',
                                                     'extra-2':'uuid:extra-2'})
        self.assertEqual(capture, before)
        self.assertIn('uuid:extra-2', [r['reference'] for r in report['pose_constraints']])
        for index in (3, 5):  # An actual fixed mark and the immovable silk-only graphic.
            for kind in ('pose', 'reference'):
                changed = deepcopy(capture)
                if kind == 'pose':
                    changed['footprints'][index]['position_nm'][0] += MM
                else:
                    changed['footprints'][index]['ref'] = 'other-label'
                result = placement_contract.grade(changed, binding)
                self.assertEqual(result['status'], 'defect')
                self.assertTrue(any(d['kind'] == ('fixed_pose_changed' if kind == 'pose'
                                                else 'saved_native_reference_changed') for d in result['defects']))

    def test_duplicate_circuit_references_are_not_promoted_to_mark_aliases(self):
        capture, contract, normalized, records = fixture()
        capture['footprints'][2]['ref'] = 'U1'
        with self.assertRaisesRegex(placement_job.JobRefusal, 'ambiguous footprint identity'):
            placement_contract.bind(capture, contract, normalized, records)

    def test_build_admits_contract_but_package_without_retained_basis_refuses(self):
        from agent_ee import build_plan, build_stages
        plan = dict(schema=build_plan.SCHEMA, source='inputs', envelope='envelope.json',
            requirements=dict(map='map.json', record='record.json', spec='spec.md'),
            parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
            placement=dict(intent='intent.json', groups='groups.json', budget_s=10, search={}, router={},
                           block_contract='strict-contract.json'))
        self.assertEqual(build_plan.validate_plan(plan)['placement']['block_contract'], 'strict-contract.json')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'requires final artwork revalidation'):
                build_stages._package(plan, Path(tmp), Path(tmp), None, None)
            self.assertFalse(Path(tmp, 'accepted-package.json').exists())
        plan['placement']['block_contract'] = '../not-captured.json'
        with self.assertRaises(ValueError):
            build_plan.validate_plan(plan)


class FinalArtworkBlockProofTests(unittest.TestCase):
    """Sealed CPU custody + real evaluator; fresh native process is the only replaced boundary."""
    def setUp(self):
        import shutil
        from agent_ee import retained_run, native_run
        from agent_ee.storage import canonical, digest, identity, write_json
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.facts, contract, _, self.records = fixture()
        self.facts['pcbnew_version'] = 'CPU fixture, not native calibration'
        source = self.root / 'source'; source.mkdir()
        board = b'original authored CPU bytes, not KiCad'
        self.facts['board_sha256'] = hashlib.sha256(board).hexdigest()
        contract['allocation']['board_sha256'] = self.facts['board_sha256']
        contract['eligibility']['sources']['board_sha256'] = self.facts['board_sha256']
        for name, raw in {'board.kicad_pcb': board, 'board.kicad_pro': b'{}',
                          'contract.json': canonical(contract), 'intent.json': b'{}',
                          'groups.json': canonical({'groups':[dict(name='device',anchor='U1',refs=['U1','C1'])]}),
                          'locality.json':canonical(contract['relationships'])}.items():
            (source/name).write_bytes(raw)
        self.plan = {'placement':dict(block_contract='contract.json',intent='intent.json',groups='groups.json')}
        self.scope = dict(board='board.kicad_pcb',checks=[dict(kind='placement',params=dict(
            criterion='electrical-locality/1',locality='locality.json'))])
        self.report = retained_run.initialize(self.root,dict(plan_sha256='a'*64,
            source=retained_run._inventory(source),runtime={}))
        previous = source
        for stage in retained_run.STAGES[:5]:
            attempt = retained_run.begin_stage(self.root,stage)
            candidate = attempt.capture(previous)
            if stage == 'placement-routing':
                normalized = placement_job.normalize_intent({}, {'groups':[dict(name='device',anchor='U1',refs=['U1','C1'])]},
                    locality=contract['relationships'],board_sha256=self.facts['board_sha256'],
                    groups_sha256=digest(candidate/'groups.json'))
                binding = placement_contract.bind(self.facts,contract,normalized,self.records)
                write_json(attempt.directory/'placement-config.json',dict(inputs={key:dict(path=str(candidate/name),
                    sha256=digest(candidate/name)) for key,name in dict(board='board.kicad_pcb',
                    project='board.kicad_pro',intent='intent.json',groups='groups.json',
                    locality='locality.json',block_contract='contract.json').items()}))
                write_json(attempt.directory/'placement/capture.json',dict(block_contract=binding,
                    facts_sha256=placement_job.sha256_bytes(canonical(self.facts)),
                    board_sha256=self.facts['board_sha256'],pcbnew_version=self.facts['pcbnew_version']))
                # Actual build adoption overwrites this config path; original basis must use schematic.
                (candidate/'board.kicad_pcb').write_bytes(b'routed CPU bytes')
            retained_run.record_completion(self.root,self.report,attempt,candidate)
            retained_run.write_report(self.root,self.report);previous=candidate
        self.artwork = retained_run.begin_stage(self.root,'artwork')
        self.candidate = self.artwork.capture(previous)
        self.saved = deepcopy(self.facts)

    def reader(self, call):
        import json
        from agent_ee import native_run
        self.assertEqual(call.intent.argv[-2], '_check_blocks')
        path = Path(call.intent.argv[-1])
        facts = deepcopy(self.saved); facts['board_sha256'] = placement_job.digest(path)
        report = placement_contract.grade(facts,json.loads(call.intent.stdin)['binding'])
        return native_run.Outcome(stdout=json.dumps(report).encode())

    def seal(self):
        from agent_ee import block_package, retained_run, native_run
        with native_run.using(native_run.Recorded(self.reader)):
            block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)
        retained_run.record_completion(self.root,self.report,self.artwork,self.candidate)
        retained_run.write_report(self.root,self.report)

    def test_saved_artwork_proof_preserves_original_basis_and_binds_both_artwork_branches(self):
        from agent_ee import block_package
        from agent_ee.storage import digest
        # Finishing changes bytes only; both branch outputs must use their actual saved digest.
        for branch in ('repaired','no_movable_fields'):
            with self.subTest(branch=branch):
                (self.candidate/'board.kicad_pcb').write_bytes(branch.encode())
                from agent_ee import native_run
                with native_run.using(native_run.Recorded(self.reader)):
                    block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)
                from agent_ee.storage import read_json
                proof=read_json(self.artwork.directory/block_package.PROOF)
                self.assertEqual(proof['report']['board_sha256'],digest(self.candidate/'board.kicad_pcb'))
                self.assertEqual(proof['report']['original_board_sha256'],self.facts['board_sha256'])
                self.assertEqual(proof['report']['status'],'pass')
        self.seal()
        binding=block_package.verify(self.root,self.candidate,self.scope,self.plan)
        self.assertEqual(binding['board_sha256'],digest(self.candidate/'board.kicad_pcb'))
        self.assertEqual(block_package.verify(None,None,None,{'placement':{}}),None)
        self.assertIsNone(block_package.produce(None,None,None,None,{}))

    def test_final_coordinate_defect_and_required_unknown_refuse_with_retained_report(self):
        from agent_ee import block_package, native_run
        from agent_ee.storage import read_json
        for case in ('coordinate','membership','unknown'):
            self.saved=deepcopy(self.facts)
            if case=='coordinate':
                for pad in self.saved['footprints'][1]['pads']:
                    pad['position_nm'][0] += 10*MM
            elif case=='membership':
                self.saved['footprints'].pop()
            else:
                self.saved['footprints'][1]['pads'][1].pop('copper_centroid_offset_nm')
            with self.subTest(case=case), native_run.using(native_run.Recorded(self.reader)):
                with self.assertRaisesRegex(ValueError,'defects or required unknowns'):
                    block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)
            proof=read_json(self.artwork.directory/block_package.PROOF)
            self.assertNotEqual(proof['report']['status'],'pass')

    def test_missing_or_changed_proof_and_candidate_bytes_cannot_release(self):
        from agent_ee import block_package, retained_run
        from agent_ee.storage import read_json, write_json, identity
        self.seal()
        path=self.artwork.directory/block_package.PROOF
        original=path.read_bytes()
        for case in ('missing','board','report','binding','groups','policy','declaration'):
            with self.subTest(case=case):
                if case=='missing':path.unlink()
                elif case=='board':(self.candidate/'board.kicad_pcb').write_bytes(b'after proof edit')
                elif case=='declaration':
                    proof=read_json(path)
                    (self.candidate/'groups.json').write_bytes(b'changed authored groups')
                    proof['final_files']['groups.json']=placement_job.digest(self.candidate/'groups.json')
                    write_json(path,proof)
                else:
                    proof=read_json(path)
                    if case=='report':proof['report']['original_board_sha256']='b'*64
                    else:proof['basis'][case+'_sha256']='b'*64
                    proof['report_sha256']=identity(proof['report']);write_json(path,proof)
                # Resealing models forged custody: semantic validation must still refuse.
                self.report['completed'][-1]['tree']=retained_run.tree_identity(self.artwork.directory)
                retained_run.write_report(self.root,self.report)
                with self.assertRaisesRegex((ValueError,KeyError),'proof is missing|proof identity differs'):
                    block_package.verify(self.root,self.candidate,self.scope,self.plan)
                path.write_bytes(original)
                schematic=retained_run.completed_stage(self.root,'schematic')
                (self.candidate/'groups.json').write_bytes((schematic.candidate/'groups.json').read_bytes())
                (self.candidate/'board.kicad_pcb').write_bytes(b'routed CPU bytes')
                self.report['completed'][-1]['tree']=retained_run.tree_identity(self.artwork.directory)
                retained_run.write_report(self.root,self.report)

    def test_original_capture_policy_contract_groups_and_source_route_cannot_be_rebound(self):
        from agent_ee import block_package, retained_run
        from agent_ee.storage import read_json, write_json
        self.seal()
        placement=retained_run.completed_stage(self.root,'placement-routing')
        original=read_json(placement.directory/'placement/capture.json')
        for case in ('missing','policy','contract','groups','source_route'):
            changed=deepcopy(original)
            if case=='missing':del changed['block_contract']
            elif case=='source_route':self.plan['placement']['block_source']='never-produced.json'
            else:changed['block_contract'][case+'_sha256' if case!='contract' else 'contract']='changed'
            write_json(placement.directory/'placement/capture.json',changed)
            self.report['completed'][4]['tree']=retained_run.tree_identity(placement.directory)
            retained_run.write_report(self.root,self.report)
            with self.subTest(case=case),self.assertRaises((ValueError,KeyError,TypeError,placement_job.JobRefusal)):
                block_package.verify(self.root,self.candidate,self.scope,self.plan)
            self.plan['placement'].pop('block_source',None)


    def test_qualification_requires_matching_package_marker_and_reviewed_saved_board(self):
        from agent_ee import block_package
        self.seal()
        binding=block_package.verify(self.root,self.candidate,self.scope,self.plan)
        self.assertEqual(block_package.verify_package(self.root,self.candidate,self.scope,self.plan,
            {'block_contract':binding},binding['board_sha256']),binding)
        for marker,board in (({},binding['board_sha256']),({'block_contract':dict(binding,policy_sha256='b'*64)},
                             binding['board_sha256']),({'block_contract':binding},'b'*64)):
            with self.subTest(marker=marker,board=board),self.assertRaisesRegex(ValueError,'final review/marker'):
                block_package.verify_package(self.root,self.candidate,self.scope,self.plan,marker,board)

    def test_fresh_native_child_failure_never_seals_a_final_group_proof(self):
        from agent_ee import block_package, native_run
        with native_run.using(native_run.Recorded(lambda call:native_run.Outcome(exit_code=2,stderr=b'CPU failure control'))):
            with self.assertRaisesRegex(placement_job.JobRefusal,'native block contract check failed'):
                block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)
        self.assertFalse((self.artwork.directory/block_package.PROOF).exists())


    def test_timeout_and_build_cancel_refuse_without_final_group_publication(self):
        from agent_ee import block_package, native_run
        for case in ('timeout','cancel'):
            if case=='cancel':(self.root/'CANCEL').touch()
            outcome=native_run.Outcome(exit_code=None,execution='timeout')
            with self.subTest(case=case),native_run.using(native_run.Recorded(lambda call:outcome)):
                with self.assertRaisesRegex(ValueError,'native read stopped: '+('deadline' if case=='timeout' else 'cancelled')):
                    block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)
            self.assertFalse((self.artwork.directory/block_package.PROOF).exists())
