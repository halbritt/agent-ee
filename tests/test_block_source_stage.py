"""Slice A CPU contracts; authored board bytes/facts never claim native feasibility."""
from copy import deepcopy
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import block_source_stage as stage, placement_contract as contract, placement_job as job
from agent_ee import build_plan, build_stages, build_workflow, native_run, retained_run, stage_receipts
from agent_ee.storage import canonical, digest, identity, read_json, write_json
from test_prepared_block_contract import fixture
from test_build_plan import declaration
from test_strict_build_preflight import source_plan


def source_fixture():
    facts, native, normalized, records = fixture()
    source = deepcopy(native)
    source['schema'] = contract.SOURCE_SCHEMA
    del source['allocation']['board_sha256']
    del source['eligibility']['sources']['board_sha256']
    return source, facts, records


class BlockSourceTests(unittest.TestCase):
    def test_source_derivation_changes_only_native_identity_leaves_and_preserves_authority(self):
        source, _, _ = source_fixture()
        before = deepcopy(source)
        derived = contract.materialize_source(source, 'c'*64)
        self.assertEqual(source, before)
        self.assertEqual(derived['allocation'].pop('board_sha256'), 'c'*64)
        self.assertEqual(derived['eligibility']['sources'].pop('board_sha256'), 'c'*64)
        derived['schema'] = source['schema']
        self.assertEqual(derived, source)
        with self.assertRaisesRegex(ValueError, 'expected agent-ee.block-contract-source'):
            contract.materialize_source(fixture()[1], 'c'*64)

    def test_malformed_source_cannot_gain_identity_or_roles(self):
        source, _, _ = source_fixture()
        cases = []
        for key in ('allocation', 'eligibility'):
            s = deepcopy(source)
            target = s[key] if key == 'allocation' else s[key]['sources']
            target['board_sha256'] = None
            cases.append((s, 'must omit board_sha256'))
        s=deepcopy(source);s['allocation']['blocks'][0]['anchor']={'none':'centroid of members'}
        cases.append((s,'member anchor'))
        s=deepcopy(source);s['allocation']['shared'].append({'reference':'U1','role':'other'})
        cases.append((s,'multiple roles'))
        s=deepcopy(source);del s['relationships']['obligations'][0]['return']
        cases.append((s,'return'))
        s=deepcopy(source);s['relationships']['source_records']['limits.txt']='altered authority'
        cases.append((s,'authority'))
        s=deepcopy(source);s['eligibility']['members']={'FOREIGN': {'eligible_faces':['F']}}
        cases.append((s,'unknown roles'))
        for value, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic), self.assertRaisesRegex(ValueError, diagnostic):
                contract.validate_source(value)

    def test_shared_omission_refuses_native_binding_with_identical_locality(self):
        source, facts, records = source_fixture()
        normalized = fixture()[2]
        complete = contract.materialize_source(source, facts['board_sha256'])
        self.assertEqual(contract.grade(facts, contract.bind(facts, complete, normalized, records))['status'],'pass')
        source['allocation']['shared']=[];source['eligibility']['shared']={}
        missing=contract.materialize_source(source,facts['board_sha256'])
        self.assertEqual(missing['relationships'],complete['relationships'])
        with self.assertRaisesRegex(job.JobRefusal,'unallocated native footprints: TP1'):
            contract.bind(facts,missing,normalized,records)
        complete['allocation']['board_sha256']='b'*64
        with self.assertRaisesRegex(job.JobRefusal,'original board'):
            contract.bind(facts,complete,normalized,records)

    def test_plan_mutual_exclusion_and_reserved_input_namespace(self):
        for option in ('both','namespace','judgments'):
            plan=declaration();plan['placement']['block_source']='source.json'
            if option=='both':plan['placement']['block_contract']='native.json'
            elif option=='namespace':plan['placement']['intent']=stage.DIRECTORY+'/intent.json'
            else:
                plan['schema']=build_plan.TYPED_SCHEMA;plan['requirements']['obligations']='obligations.json'
                plan['judgments']='judgments';plan['placement']['block_source']='judgments/source.json'
            with self.subTest(option=option),self.assertRaises(ValueError):build_plan.validate_plan(plan)

    def _plan_files(self, root):
        path=source_plan(root,strict=False)
        plan=read_json(path);plan['placement']['block_source']='source.json';write_json(path,plan)
        source=root/'inputs';write_json(source/'source.json',source_fixture()[0])
        write_json(source/'groups.json',{'groups':[dict(name='device',anchor='U1',refs=['U1','C1'])]})
        return path,source

    def test_strict_source_package_admission_reaches_runtime_but_standalone_package_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,source=self._plan_files(root);out=root/'run'
            for resume in (False,True):
                if resume:out.mkdir();(out/'sentinel').write_bytes(b'unchanged')
                with patch.object(build_workflow,'_runtime',side_effect=RuntimeError('native runtime boundary')):
                    with self.assertRaisesRegex(RuntimeError,'native runtime boundary'):
                        build_workflow.run(path,out,resume=resume)
                self.assertEqual(out.exists(),resume)
                if resume:self.assertEqual((out/'sentinel').read_bytes(),b'unchanged')
            with self.assertRaisesRegex(ValueError,'requires final artwork revalidation'):
                build_stages._package(read_json(path),source,root,None,None)
            self.assertFalse((root/'accepted-package.json').exists())

    def test_source_preflight_refuses_malformed_or_namespace_before_runtime(self):
        for kind in ('malformed','namespace','group'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path,source=self._plan_files(root)
                if kind=='malformed':write_json(source/'source.json',{'schema':'wrong'})
                elif kind=='namespace':(source/stage.DIRECTORY).mkdir(parents=True)
                else:write_json(source/'groups.json',{'groups':[]})
                with patch.object(build_workflow,'_runtime',side_effect=AssertionError('runtime probed')):
                    with self.assertRaises(ValueError):build_workflow.run(path,root/'run',through='artwork')
                self.assertFalse((root/'run').exists())

    def test_ordinary_scope_and_locality_are_not_rewritten_for_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,source=self._plan_files(root)
            before=(source/'map.json').read_bytes()
            with patch.object(build_workflow,'_runtime',side_effect=RuntimeError('runtime boundary')):
                with self.assertRaisesRegex(RuntimeError,'runtime boundary'):
                    build_workflow.run(path,root/'run',through='artwork')
            self.assertEqual((source/'map.json').read_bytes(),before)
        scope={'checks':[{'kind':'placement','params':{'criterion':'electrical-locality/1','locality':'locality.json'}}]}
        self.assertEqual(stage.locality_path(scope),'locality.json')
        scope['checks'].append({'kind':'placement','params':{'criterion':'electrical-locality/1','locality':'other.json'}})
        with self.assertRaisesRegex(ValueError,'one shared'):stage.locality_path(scope)


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.candidate=self.root/'candidate';self.candidate.mkdir()
        self.source,self.facts,self.records=source_fixture()
        self.facts['pcbnew_version']='CPU fixture; no native calibration'
        self.names=dict(board='board.kicad_pcb',project='board.kicad_pro',rules=None,
                        source='source.json',intent='intent.json',groups='groups.json',locality='locality.json')
        for name,raw in {'board.kicad_pcb':b'CPU bytes; NOT a native board', 'board.kicad_pro':b'{}',
                         'source.json':canonical(self.source),'intent.json':canonical({}),
                         'groups.json':canonical({'groups':[dict(name='device',anchor='U1',refs=['U1','C1'])]}),
                         'locality.json':canonical(self.source['relationships'])}.items():
            (self.candidate/name).write_bytes(raw)
        self.facts['board_sha256']=digest(self.candidate/'board.kicad_pcb')
        self.plan=declaration();self.plan['placement'].update(block_source='source.json')
        self.scope={'board':'board.kicad_pcb','checks':[dict(kind='placement',params=dict(
            criterion='electrical-locality/1',locality='locality.json'))]}

    def responder(self,call):
        # Only the fresh native process is replaced, not its parent read/bind/protocol checks.
        request=json.loads(call.intent.stdin)
        normalized=request['normalized']
        binding=contract.bind(self.facts,normalized['block_contract'],normalized,self.records)
        record=dict(schema='agent-ee.placement-job-capture/1',block_contract=binding,
                    board_sha256=self.facts['board_sha256'],pcbnew_version=self.facts['pcbnew_version'],
                    facts_sha256=job.sha256_bytes(canonical(self.facts)))
        return native_run.Outcome(stdout=json.dumps(dict(problem={'schema':job.INPUT_SCHEMA},record=record)).encode())

    def test_real_capture_protocol_publishes_exact_originals_and_normalization(self):
        with native_run.using(native_run.Recorded(self.responder)):
            stage.produce(self.candidate,self.root/'out',self.names,clock=job.Clock(10))
        produced=read_json(self.root/'out/production.json')
        raw,source,normalized=stage._inputs(self.candidate,self.names)
        normalized['block_contract']=contract.materialize_source(source,digest(self.candidate/'board.kicad_pcb'))
        self.assertEqual(produced['normalized_sha256'],identity(normalized))
        self.assertEqual((self.root/'out/original/board.kicad_pcb').read_bytes(),raw['board'])
        self.assertIsNone(produced['inputs']['rules'])
        self.assertNotIn('accepted',produced)
        self.assertEqual(read_json(self.root/'out/binding.json')['policy_sha256'],identity(stage.policy_files()))

    def test_child_failure_drift_and_deadline_publish_nothing(self):
        for failure in ('exit','board','rules','policy','deadline'):
            with self.subTest(failure=failure):
                out=self.root/failure
                original=(self.candidate/'board.kicad_pcb').read_bytes()
                def respond(call):
                    if failure=='exit':return native_run.Outcome(exit_code=2,stderr=b'native capture refused')
                    result=self.responder(call)
                    if failure=='board':(self.candidate/'board.kicad_pcb').write_bytes(b'drift')
                    if failure=='rules':(self.candidate/'board.kicad_dru').write_bytes(b'late')
                    return result
                policies=[{'old':'policy'},{'changed':'policy'}]
                with native_run.using(native_run.Recorded(respond)):
                    if failure=='policy':
                        with patch.object(stage,'policy_files',side_effect=policies),self.assertRaisesRegex(ValueError,'policy changed'):
                            stage.produce(self.candidate,out,self.names,clock=job.Clock(10))
                    else:
                        with self.assertRaises((job.JobRefusal,job.JobStopped,ValueError)):
                            stage.produce(self.candidate,out,self.names,clock=job.Clock(0 if failure=='deadline' else 10))
                self.assertFalse(out.exists())
                (self.candidate/'board.kicad_pcb').write_bytes(original)
                (self.candidate/'board.kicad_dru').unlink(missing_ok=True)

    def test_regular_file_receipt_adoption_and_proof_drift(self):
        # Real spec/run_step/receipt/adoption; only external execution and its kernel custody observation mocked.
        tool=self.root/'tool';tool.mkdir();(tool/'code.py').write_text('CPU tool source snapshot')
        spec=stage._spec(self.names,tool)
        self.assertEqual(set(spec['writes']),{stage.DIRECTORY+'/'+p for p in (
            'contract.json','binding.json','production.json','original/board.kicad_pcb','original/board.kicad_pro')})
        def respond(call):
            if call.intent.isolation=='host':return self.responder(call)
            output=self.root/'external-output'
            stage.produce(call.inputs,output,self.names,clock=job.Clock(10))
            return native_run.Outcome(files={'out/'+p.relative_to(output).as_posix():p.read_bytes()
                                            for p in output.rglob('*') if p.is_file()})
        with native_run.using(native_run.Recorded(respond)),patch('fcntl.flock'):
            path,receipt=build_stages._step(self.root/'stage',self.candidate,spec,0)
            self.assertEqual(stage_receipts.validate_receipt(path),receipt)
        self.assertEqual(receipt['status'],'completed')
        for target,produced in spec['writes'].items():
            self.assertTrue((self.candidate/target).is_file())
            self.assertEqual(digest(self.candidate/target),receipt['outputs'][target])
        (self.candidate/stage.DIRECTORY/'binding.json').write_bytes(b'drift')
        with self.assertRaisesRegex(ValueError,'design changed'):
            stage_receipts.adopt(path,receipt,self.candidate)

    def test_missing_completed_schematic_cannot_reuse_partial_proof(self):
        root=self.root/'run';root.mkdir()
        pin=dict(plan_sha256='a'*64,source={},runtime={})
        (root/'source').mkdir();retained_run.initialize(root,pin)
        with self.assertRaisesRegex(ValueError,'completed schematic'):
            stage.verify(root,self.candidate,self.scope,self.plan)

    def test_fixed_entrypoint_refuses_unconfined_host_before_native_read(self):
        from agent_ee.forward_annotation import AnnotationError
        with patch('pathlib.Path.read_text', side_effect=PermissionError('CPU denied mountinfo')):
            with self.assertRaisesRegex(AnnotationError, 'stage mountinfo'):
                stage.main(['--board','board.kicad_pcb','--project','board.kicad_pro',
                            '--source','source.json','--intent','intent.json','--groups','groups.json'])

    def test_completed_producer_chain_revalidates_and_rejects_foreign_source_policy_or_proof(self):
        root=self.root/'retained';root.mkdir()
        shutil.copytree(self.candidate,root/'source')
        source_files=stage.file_tree(root/'source')[0]
        report=retained_run.initialize(root,dict(plan_sha256='a'*64,source=source_files,runtime={}))
        previous=root/'source'
        for name in ('requirements','parts','scaffold'):
            attempt=retained_run.begin_stage(root,name)
            candidate=attempt.capture(previous)
            retained_run.record_completion(root,report,attempt,candidate)
            retained_run.write_report(root,report);previous=candidate
        attempt=retained_run.begin_stage(root,'schematic');candidate=attempt.capture(previous)
        tool=self.root/'frozen-tool'
        shutil.copytree(stage.SOURCE_ROOT,tool,ignore=shutil.ignore_patterns('__pycache__'))
        with patch.object(stage,'snapshot',return_value=tool):
            spec=stage.configuration(stage.SOURCE_ROOT,candidate,self.scope,self.plan)
        initial=stage.file_tree(candidate)[0]
        def respond(call):
            if call.intent.isolation=='host':return self.responder(call)
            if call.intent.argv[-1]=='noop':return native_run.Outcome(files={'out/report.json':b'{}'})
            output=self.root/'external-chain-output'
            stage.produce(call.inputs,output,self.names,clock=job.Clock(10))
            return native_run.Outcome(files={'out/'+p.relative_to(output).as_posix():p.read_bytes()
                                            for p in output.rglob('*') if p.is_file()})
        with native_run.using(native_run.Recorded(respond)),patch('fcntl.flock'):
            for index in range(3):
                noop=dict(spec,argv=['/usr/bin/python3','noop'],writes={},artifacts=['out/report.json'])
                build_stages._step(attempt.directory,candidate,noop,index)
            path,receipt=build_stages._step(attempt.directory,candidate,spec,3)
        self.assertEqual(receipt['inputs'],initial)
        retained_run.record_completion(root,report,attempt,candidate);retained_run.write_report(root,report)
        expected=read_json(candidate/stage.DIRECTORY/'binding.json')
        self.assertEqual(stage.verify(root,candidate,self.scope,self.plan),expected)
        consumer=self.root/'placement-inputs';shutil.copytree(candidate,consumer)
        self.assertEqual(stage.verify(root,consumer,self.scope,self.plan),expected)
        with patch.object(stage,'policy_files',return_value={'changed':'policy'}):
            with self.assertRaisesRegex(ValueError,'production no longer matches'):
                stage.verify(root,consumer,self.scope,self.plan)
        for target in ('source.json',stage.DIRECTORY+'/production.json',
                       stage.DIRECTORY+'/original/board.kicad_pcb'):
            path=consumer/target;original=path.read_bytes()
            path.write_bytes(original+b' ')
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'changed|no longer matches|differs'):
                stage.verify(root,consumer,self.scope,self.plan)
            path.write_bytes(original)
        # Continue the actual sealed producer/adoption chain into original-basis replay and artwork proof.
        from agent_ee import block_package
        placement=retained_run.begin_stage(root,'placement-routing')
        placed=placement.capture(candidate)
        config=dict(inputs={key:dict(path=str(placed/name),sha256=digest(placed/name))
            for key,name in dict(board='board.kicad_pcb',project='board.kicad_pro',intent='intent.json',
                groups='groups.json',locality='locality.json',block_contract=stage.DIRECTORY+'/contract.json').items()})
        write_json(placement.directory/'placement-config.json',config)
        write_json(placement.directory/'placement/capture.json',dict(block_contract=expected,
            board_sha256=self.facts['board_sha256'],pcbnew_version=self.facts['pcbnew_version'],
            facts_sha256=job.sha256_bytes(canonical(self.facts))))
        (placed/'board.kicad_pcb').write_bytes(b'CPU routed bytes, original config path overwritten')
        retained_run.record_completion(root,report,placement,placed);retained_run.write_report(root,report)
        artwork=retained_run.begin_stage(root,'artwork');final=artwork.capture(placed)
        def final_read(call):
            facts=deepcopy(self.facts);facts['board_sha256']=digest(final/'board.kicad_pcb')
            result=contract.grade(facts,json.loads(call.intent.stdin)['binding'])
            return native_run.Outcome(stdout=json.dumps(result).encode())
        with native_run.using(native_run.Recorded(final_read)):
            block_package.produce(root,final,artwork.directory,self.scope,self.plan)
        retained_run.record_completion(root,report,artwork,final);retained_run.write_report(root,report)
        bound=block_package.verify(root,final,self.scope,self.plan)
        self.assertEqual(bound['board_sha256'],digest(final/'board.kicad_pcb'))
        self.assertEqual(read_json(artwork.directory/block_package.PROOF)['basis']['source_kind'],'block_source')
        # The final board differs: source verification must keep using the sealed schematic.
        with self.assertRaises(ValueError):stage.verify(root,final,self.scope,self.plan)
        receipt_file=attempt.directory/'receipts/3/receipt.json'
        broken=read_json(receipt_file);broken['status']='failed';write_json(receipt_file,broken)
        with self.assertRaisesRegex(retained_run.QualificationError,'completed stage identity changed'):
            stage.verify(root,consumer,self.scope,self.plan)

    def test_present_rules_are_individual_original_outputs(self):
        names=dict(self.names,rules='board.kicad_dru')
        (self.candidate/names['rules']).write_bytes(b'CPU authored rules bytes')
        with native_run.using(native_run.Recorded(self.responder)) as runs:
            stage.produce(self.candidate,self.root/'with-rules',names,clock=job.Clock(10))
        self.assertEqual((self.root/'with-rules/original/board.kicad_dru').read_bytes(),b'CPU authored rules bytes')
        self.assertEqual(runs.calls[0].intent.isolation,'host')
        self.assertLessEqual(runs.calls[0].timeout,10)
        self.assertIn(stage.DIRECTORY+'/original/board.kicad_dru',stage._outputs(names))

    def test_failed_stage_retains_failure_without_adopting_new_files(self):
        tool=self.root/'failed-tool';tool.mkdir();(tool/'code.py').write_text('CPU frozen tool')
        spec=stage._spec(self.names,tool);before=stage.file_tree(self.candidate)
        with native_run.using(native_run.Recorded(lambda call: native_run.Outcome(exit_code=2,stderr=b'capture refused'))),patch('fcntl.flock'):
            with self.assertRaisesRegex(ValueError,'failed'):
                build_stages._step(self.root/'failed-stage',self.candidate,spec,0)
        self.assertEqual(stage.file_tree(self.candidate),before)
        self.assertEqual(read_json(self.root/'failed-stage/receipts/0/receipt.json')['status'],'failed')
