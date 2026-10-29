"""Pure source/custody replay; authored facts and intercepted native read, no native acceptance."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import (acquisition_mechanics as mechanics, block_package, placement_contract,
                      placement_job, placement_problem, retained_run, native_run, routed_artwork)
from agent_ee.storage import canonical, digest, identity, read_json, write_json
from tests.test_prepared_block_contract import fixture, FinalArtworkBlockProofTests


class MechanicsPackageTests(unittest.TestCase):
    reader = FinalArtworkBlockProofTests.reader

    def setUp(self):
        import os
        evidence=os.environ.get('AGENT_EE_BLOCK_PACKAGE_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            self.root=Path(tempfile.mkdtemp(prefix=self._testMethodName+'-',dir=evidence))
        else:
            self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source=self.root/'source';source.mkdir();(source/'analysis').mkdir()
        fixture_root=Path(__file__).resolve().parents[1]/'fixtures/acquisition-qualification'
        for name in ('geometry-model.json','geometry_model.py'):
            (source/'analysis'/name).write_bytes((fixture_root/name).read_bytes())
        packages=read_json(source/'analysis/geometry-model.json')['packages']
        base,contract,_,_=fixture()
        self.facts=dict(base,pcbnew_version='CPU authored facts; not a native result',footprints=[])
        for i,ref in enumerate(packages):
            fp=deepcopy(base['footprints'][0]);fp.update(ref=ref,uuid=f'fp-{i}',locked=ref.startswith('J'))
            fp['pads']=[dict(p,uuid=f'pad-{i}-{j}',rotation_deg=0) for j,p in enumerate(fp['pads'])]
            self.facts['footprints'].append(fp)
        members=['U1','C1']
        contract['allocation']['blocks'][0].update(references=members,anchor={'member':'U1'})
        shared=[r for r in packages if r not in members and not r.startswith('J')]
        contract['allocation']['shared']=[dict(reference=r,role='authored CPU shared part') for r in shared]
        contract['allocation']['fixed']=[{'reference':r} for r in ('J1','J2','J3')]
        contract['eligibility']['shared']={r:{'eligible_faces':['F']} for r in shared}
        contract['eligibility']['fixed']={r:{'eligible_faces':['F']} for r in ('J1','J2','J3')}
        # Lexical fixture exercises the REAL silk scope. No native geometry pass is claimed.
        board=b'(kicad_pcb '+b''.join((f'(footprint "CPU" (at 2 2) (layer "F.Cu") '
            f'(property "Reference" "{r}" (at 1 2) (layer "F.SilkS") (uuid "fp-{i}-ref")) '
            f'(uuid "fp-{i}"))').encode() for i,r in enumerate(packages))+b')'
        (source/'board.kicad_pcb').write_bytes(board);(source/'board.kicad_pro').write_bytes(b'{}')
        self.facts['board_sha256']=digest(source/'board.kicad_pcb')
        contract['allocation']['board_sha256']=self.facts['board_sha256']
        contract['eligibility']['sources']['board_sha256']=self.facts['board_sha256']
        (source/'spec.md').write_text('Authored CPU mechanics source; no native acceptance.')
        limits={k:dict(**{bound:value},unit=unit,condition='nominal25C-reviewed-acquisition')
                for k,bound,value,unit in [('body_gap','min',.2,'mm'),('body_overlap','max',0,'mm2'),
                    ('body_outside','max',0,'mm2'),('access_obstruction','max',1e-8,'mm2')]}
        write_json(source/'requirement-map.json',{'prose':[],'requirements':[{'checks':[
            {'id':'engineering-geometry','params':{'limits':limits}}]}]})
        write_json(source/'placement-mechanics.json',mechanics.declaration(source))
        self.recipe=mechanics.load(source/'placement-mechanics.json')
        self.groups={'groups':[dict(name='device',anchor='U1',refs=members)]}
        for name,value in [('contract.json',contract),('intent.json',{}),('groups.json',self.groups),
                           ('locality.json',contract['relationships'])]:write_json(source/name,value)
        self.scope=dict(board='board.kicad_pcb',checks=[dict(kind='placement',params=dict(
            criterion='electrical-locality/1',locality='locality.json'))])
        self.plan={'placement':dict(block_contract='contract.json',intent='intent.json',groups='groups.json',
                                    mechanics='placement-mechanics.json')}
        self.records=[dict(uuid=f['uuid'],native_ref=f['ref'],role='fixed_body' if f['locked'] else 'movable')
                      for f in self.facts['footprints']]
        self.report=retained_run.initialize(self.root,dict(plan_sha256='a'*64,source=retained_run._inventory(source),runtime={}))
        previous=source
        for name in retained_run.STAGES[:5]:
            stage=retained_run.begin_stage(self.root,name);candidate=stage.capture(previous)
            if name=='schematic':self.original=candidate
            if name=='placement-routing':
                self.placement=stage
                self.placement_candidate=candidate
                normalized=placement_job.normalize_intent({},self.groups,locality=contract['relationships'],
                    board_sha256=self.facts['board_sha256'],groups_sha256=digest(candidate/'groups.json'))
                binding=placement_contract.bind(self.facts,contract,normalized,self.records)
                self.bound=mechanics.bind(self.recipe,self.facts)
                write_json(stage.directory/'placement-config.json',dict(inputs={k:dict(path=str(candidate/v),sha256=digest(candidate/v))
                    for k,v in dict(board='board.kicad_pcb',project='board.kicad_pro',intent='intent.json',groups='groups.json',
                        locality='locality.json',block_contract='contract.json',mechanics='placement-mechanics.json').items()}))
                self.capture=dict(block_contract=binding,mechanics=self.bound,board_sha256=self.facts['board_sha256'],
                    pcbnew_version=self.facts['pcbnew_version'],facts_sha256=identity(self.facts))
                write_json(stage.directory/'placement/capture.json',self.capture)
                # Only data needed for original owner/model identity replay, not another geometry evaluator.
                desc={'footprints':[dict(f) for f in self.facts['footprints']]}
                mechanics.attach(desc,self.bound,self.facts)
                problem={'schema':placement_problem.INPUT_SCHEMA,'mechanics':desc['mechanics'],'footprints':[dict(uuid=f['uuid'],native_ref=f['ref'],
                    role='fixed_body' if f['locked'] else 'movable',pose=dict(x_nm=f['position_nm'][0],
                    y_nm=f['position_nm'][1],quarter_turns=0),pads=[dict(uuid=p['uuid'],number=p['number'],net=p['net'],
                    local_nm=[p['position_nm'][0]-f['position_nm'][0],p['position_nm'][1]-f['position_nm'][1]]) for p in f['pads']],source_body_local_nm=f['source_body_local_nm'],
                    access_local_nm=f['access_local_nm']) for f in desc['footprints']]}
                problem['problem_sha256']=placement_problem.identity(problem)
                write_json(stage.directory/'placement/problem.json',problem)
                final=stage.directory/'placement/final-a1/board.kicad_pcb';final.parent.mkdir();final.write_bytes(board)
                write_json(stage.directory/'placement/job-report.json',dict(status='completed',
                    config_sha256=identity(read_json(stage.directory/'placement-config.json')),final=dict(board=str(final),
                    board_sha256=digest(final),eligibility='eligible',mechanics=dict(status='pass',defects=[],
                    board_sha256=digest(final),binding_sha256=self.bound['binding_sha256']))))
            retained_run.record_completion(self.root,self.report,stage,candidate)
            retained_run.write_report(self.root,self.report);previous=candidate
        self.artwork=retained_run.begin_stage(self.root,'artwork');self.candidate=self.artwork.capture(previous)
        self.saved=deepcopy(self.facts)
        write_json(self.artwork.directory/'artwork.json',dict(board=self.scope['board'],status='no_movable_fields',
            source_sha256=digest(self.candidate/self.scope['board'])))

    def reseal(self):
        for row in self.report['completed']:
            row['tree']=retained_run.tree_identity(self.root/row['directory'])
        retained_run.write_report(self.root,self.report)

    def produce(self):
        with native_run.using(native_run.Recorded(self.reader)):
            block_package.produce(self.root,self.candidate,self.artwork.directory,self.scope,self.plan)

    def test_original_and_no_movable_transition_proof_pins_all_sources(self):
        self.produce()
        proof=read_json(self.artwork.directory/block_package.PROOF)
        self.assertEqual(proof['basis']['mechanics']['binding_sha256'],self.bound['binding_sha256'])
        for name,pin in self.recipe['declaration']['sources'].items():
            self.assertEqual(proof['basis']['declarations'][name],pin)
        retained_run.record_completion(self.root,self.report,self.artwork,self.candidate);retained_run.write_report(self.root,self.report)
        self.assertEqual(block_package.verify(self.root,self.candidate,self.scope,self.plan)['board_sha256'],digest(self.candidate/self.scope['board']))

    def test_resealed_missing_or_rehashed_original_evidence_refuses(self):
        job=self.placement.directory/'placement';files={name:(job/name).read_bytes() for name in ('capture.json','problem.json')}
        for case in ('missing_capture','binding','pad','problem_source','problem_pose','undeclared'):
            capture=read_json(job/'capture.json');problem=read_json(job/'problem.json')
            if case=='missing_capture':capture.pop('mechanics')
            elif case=='binding':
                capture['mechanics']['original_board_sha256']='b'*64
                capture['mechanics']['binding_sha256']=mechanics._identity(capture['mechanics'])
            elif case=='pad':capture['mechanics']['owners'][0]['pads'][0]['number']='other'
            elif case=='problem_source':problem['mechanics']['binding_sha256']='b'*64
            elif case=='problem_pose':problem['footprints'][0]['pose']['x_nm']+=1
            else:self.plan['placement'].pop('mechanics')
            problem['problem_sha256']=placement_problem.identity(problem)
            write_json(job/'capture.json',capture);write_json(job/'problem.json',problem);self.reseal()
            with self.subTest(case=case),self.assertRaises(ValueError):self.produce()
            for name,raw in files.items():(job/name).write_bytes(raw)
            self.plan['placement']['mechanics']='placement-mechanics.json';self.reseal()

    def test_resealed_final_mechanics_failure_or_changed_board_refuses(self):
        path=self.placement.directory/'placement/job-report.json';raw=path.read_bytes()
        for case in ('missing','not_declared','defect','binding','board','final_board'):
            value=json.loads(raw);result=value['final']['mechanics']
            if case=='missing':value['final'].pop('mechanics')
            elif case in ('not_declared','defect'):result['status']=case
            elif case=='binding':result['binding_sha256']='b'*64
            elif case=='board':result['board_sha256']='b'*64
            else:Path(value['final']['board']).write_bytes(b'late changed final board')
            write_json(path,value);self.reseal()
            with self.subTest(case=case),self.assertRaises(ValueError):self.produce()
            Path(value['final']['board']).write_bytes((self.placement_candidate/self.scope['board']).read_bytes())
            path.write_bytes(raw);self.reseal()

    def test_no_movable_does_not_authorize_changed_bytes_or_declarations(self):
        path=self.candidate/self.scope['board'];raw=path.read_bytes();path.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError,'changed board bytes'):self.produce()
        path.write_bytes(raw)
        (self.candidate/'analysis/geometry_model.py').write_bytes(b'changed checker')
        with self.assertRaisesRegex(ValueError,'proof identity differs'):self.produce()

    def repaired(self, tamper=None):
        from agent_ee import stage_receipts
        import shutil
        source=self.placement_candidate;board=source/self.scope['board'];before=board.read_bytes()
        after=before.replace(b'"Reference" "U1" (at 1 2)',b'"Reference" "U1" (at 2 2)')
        self.assertNotEqual(before,after)
        if tamper=='pose':after=after.replace(b'(footprint "CPU" (at 2 2)',b'(footprint "CPU" (at 3 2)',1)
        tool=self.artwork.directory/'tool'
        spec=routed_artwork.step(tool,self.scope['board'],targets=[('U1','Reference')])
        spec['version_argv']=['/usr/bin/python3','--version'] # CPU boundary double only.
        receipt_dir=self.artwork.directory/'receipts/0'
        # For the negative, the producer falsely asserts a field-only proof; qualifier must replay actual bytes.
        expected_after=before.replace(b'"Reference" "U1" (at 1 2)',b'"Reference" "U1" (at 2 2)')
        scope=routed_artwork.write_scope(before,expected_after,targets=[('U1','Reference')])
        def child(call):
            if call.intent.argv==('/usr/bin/python3','--version'):return native_run.Outcome(stdout=b'CPU version')
            return native_run.Outcome(files={routed_artwork.BOARD:after,routed_artwork.SOURCE:before,
                routed_artwork.REPORT:canonical(dict(producer=routed_artwork.STEP,write_scope=scope,
                    source_sha256=identity_bytes(before),board_sha256=identity_bytes(after)))})
        with native_run.using(native_run.Recorded(child)),patch('fcntl.flock'):
            receipt=stage_receipts.run_step(receipt_dir,source,spec,dict(implementation=1,receipt_index=0,
                source_candidate_id=stage_receipts.tree_identity(source)['sha256']))
        self.assertEqual(receipt['status'],'completed')
        stage_receipts.adopt(receipt_dir,receipt,self.candidate)
        write_json(self.artwork.directory/'artwork.json',dict(board=self.scope['board'],status='repaired',
            source_sha256=identity_bytes(before),board_sha256=digest(self.candidate/self.scope['board']),
            receipt='receipts/0',receipt_sha256=digest(receipt_dir/'receipt.json'),write_scope=scope,open_defects=[]))

    def test_real_receipt_scope_replay_accepts_only_field_change(self):
        self.repaired()
        self.produce()
        proof=read_json(self.artwork.directory/block_package.PROOF)
        self.assertEqual(proof['mechanics_artwork']['status'],'repaired')
        retained_run.record_completion(self.root,self.report,self.artwork,self.candidate);retained_run.write_report(self.root,self.report)
        self.assertEqual(block_package.verify(self.root,self.candidate,self.scope,self.plan)['board_sha256'],digest(self.candidate/self.scope['board']))

    def test_resealed_receipt_with_pose_change_is_not_field_authority(self):
        self.repaired(tamper='pose')
        with self.assertRaises(ValueError):self.produce()

    def test_original_sources_and_missing_config_or_problem_refuse_after_reseal(self):
        for rel in mechanics.SOURCES:
            path=self.original/rel;raw=path.read_bytes();path.write_bytes(raw+b' ');self.reseal()
            with self.subTest(source=rel),self.assertRaises(ValueError):self.produce()
            path.write_bytes(raw);self.reseal()
        config=self.placement.directory/'placement-config.json';raw=config.read_bytes()
        value=read_json(config);value['inputs'].pop('mechanics');write_json(config,value);self.reseal()
        with self.assertRaises(ValueError):self.produce()
        config.write_bytes(raw);self.reseal()
        problem=self.placement.directory/'placement/problem.json';raw=problem.read_bytes();problem.unlink();self.reseal()
        with self.assertRaises(ValueError):self.produce()
        problem.write_bytes(raw);self.reseal()

    def test_block_source_proof_is_still_required_before_mechanics_reconstruction(self):
        from agent_ee import block_source_stage
        contract_name=block_source_stage.DIRECTORY+'/contract.json'
        for candidate in (self.original,self.placement_candidate,self.candidate):
            path=candidate/contract_name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((candidate/'contract.json').read_bytes())
            (candidate/'source.json').write_bytes(b'CPU producer boundary, not an accepted native source')
        self.plan['placement'].pop('block_contract');self.plan['placement']['block_source']='source.json'
        path=self.placement.directory/'placement-config.json';config=read_json(path)
        config['inputs']['block_contract']=dict(path=str(self.placement_candidate/contract_name),sha256=digest(self.original/contract_name))
        write_json(path,config)
        report_path=self.placement.directory/'placement/job-report.json';report=read_json(report_path)
        report['config_sha256']=identity(config);write_json(report_path,report);self.reseal()
        with patch.object(block_source_stage,'verify',return_value=self.capture['block_contract']) as verify:
            self.produce();verify.assert_called()
        with patch.object(block_source_stage,'verify',side_effect=ValueError('source producer changed')):
            with self.assertRaisesRegex(ValueError,'source producer changed'):self.produce()


def identity_bytes(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()
