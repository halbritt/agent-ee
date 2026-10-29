"""Guarded owned artwork/seat controls. All provider and native facts are CPU mocks."""
import copy
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_ee import judgment_seats as seats, native_run, qualification, retained_run
from agent_ee.run_record import policy_files
from agent_ee.stage_receipts import tree_identity
from agent_ee.storage import digest, identity, read_json, write_json
from tests import test_qualification as fixture


class JudgmentSeatsTests(unittest.TestCase):
    def setUp(self):
        self.control=fixture.QualificationTests('test_checkpoint_never_implies_checks_review_or_file_release')
        self.control.setUp();self.addCleanup(self.control.doCleanups);self.control.typed_checkpoint()
        self.root=self.control.root
        scaffold=self.root/self.control.report['completed'][2]['directory']
        (scaffold/'product').mkdir()
        scope=dict(schema='agent-ee.scope/1',name='Owned CPU_MOCK_ONLY',scope='No physical acceptance',board='board.kicad_pcb',checks=[])
        write_json(scaffold/'product/scope.json',scope)
        spec=(self.root/'source/spec.md').read_bytes()
        entry=dict(id='span1',parent_id='R1',mode='judgment',locator=dict(byte_start=0,byte_end=len(spec)),
                   obligation_text_sha256=hashlib.sha256(spec).hexdigest(),mapping=dict(record='decisions/span1.json'))
        write_json(scaffold/'requirements.json',dict(schema='agent-ee.requirement-map-receipt/2',requirements=[entry]))
        self.candidate=self.root/self.control.report['candidate']
        (self.candidate/'spec.md').write_bytes(spec)
        (self.candidate/'board.kicad_pcb').write_text('CPU_MOCK_ONLY native bytes, not a valid board')
        write_json(self.candidate/'envelope.json',dict(stackup=dict(thickness_mm=1.2),layer_count=2,
            fab_profile=dict(sha256='a'*64),max_component_height_mm=8))
        stage=self.root/self.control.report['completed'][-1]['directory']
        (stage/'native-stdout.log').write_text('CPU_MOCK_ONLY retained native observation')
        for row in self.control.report['completed']:
            row['tree']=tree_identity(self.root/row['directory'])
        self.control.save()
        self.out=self.root/'seat-production'
        binary=self.root/'mock-ELF';binary.write_bytes(b'\x7fELFCPU_MOCK_ONLY')
        runtime={key:dict(path=str(binary),sha256=digest(binary)) for key in
                 ['codex','codex_tool_host','claude','bwrap','/usr/bin/python3']}
        self.environment=dict(run_id='owned-seat-case',policy_files=policy_files(),runtime_files=runtime)
        self.host=dict(runtime_files=runtime)
        self.author=dict(harness='codex',model='owned-author',effort='high',timeout_s=20)
        self.verifier=dict(harness='claude',model='owned-verifier',effort='high',timeout_s=20)
        self.cancel=native_run.Cancellation();self.deadline=time.monotonic()+40
        self.calls=[];self.mode='pass'

    def dispatch(self, run, label, definition, prompt, schema, timeout, *, cancellation, executable):
        self.assertIs(cancellation,self.cancel)
        self.assertLessEqual(timeout,self.deadline-time.monotonic()+.1)
        self.assertNotIn('PRIVATE_EVALUATOR_SECRET',prompt)
        self.assertEqual((Path(run)/'work/build-stages/artwork/native-stdout.log').read_text(),'CPU_MOCK_ONLY retained native observation')
        data=read_json(Path(run)/'packet.json');self.calls.append(label)
        if label=='judgment-author':
            props=schema['properties']['judgments']['items']['properties']['author']['properties']
            metadata={k:v['enum'][0] for k,v in props.items()}
            req=data['requirements'][0]
            doc=dict(schema='agent-ee.engineering-judgment/1',requirement=req['requirement'],span=req['span'],
                conclusion='CPU_MOCK_ONLY authored reasoning about the captured control.',assumptions=['Owned fixture only'],
                sources=[dict(kind='engineering',citation=dict(path='spec.md',sha256=data['files']['spec.md'],quote=(Path(run)/'inputs/spec.md').read_text(),locator='whole owned fixture'))],
                inputs=[dict(path='spec.md',sha256=data['files']['spec.md'])],applicability=data['applicability'],candidate=data['binding'],author=metadata,
                unresolved_defects=['owned unresolved problem'] if self.mode in ('negative','negative-bad-witness') else [])
            if self.mode=='stale':doc['candidate']['board_sha256']='0'*64
            if self.mode=='duplicate':value={'judgments':[doc,doc]}
            else:value={'judgments':[doc]}
            if self.mode=='mutate':(Path(run)/'inputs/spec.md').write_text('changed')
        else:
            props=schema['properties']['verifications']['items']['properties']['verifier']['properties']
            metadata={k:v['enum'][0] for k,v in props.items()}
            docs=list((Path(run)/'work/authored-records').rglob('*.json'))
            value={'verifications':[dict(schema='agent-ee.judgment-verification/1',judgment_sha256=digest(p),verifier=metadata,
                checked=list(qualification.WITNESS_CHECKS),result='dissent' if self.mode=='dissent' else 'concur',defects=[]) for p in docs]}
            if self.mode=='negative-bad-witness':value['verifications'][0]['checked']=[]
            if self.mode=='wrong-witness':value['verifications'][0]['judgment_sha256']='0'*64
        if self.mode=='late':self.deadline=time.monotonic()-1
        if self.mode=='wrong-model':return dict(execution='ok',backend=definition['harness'],model_requested='wrong-model',billing_route='subscription'),value
        if self.mode=='auth':return {'execution':'failed','error':'CPU_MOCK_ONLY subscription authentication failure'},None
        return dict(execution='ok',backend=definition['harness'],model_requested=definition['model'],billing_route='subscription',harness_session='CPU_MOCK_ONLY-'+label),value

    def produce(self):
        # Real preflight and receipt replay, only sandbox probe/native provider boundaries substituted.
        probe=native_run.Recorded()
        with native_run.using(probe,deadline=self.deadline,cancel=self.cancel), patch('agent_ee.reviewers.dispatch',side_effect=self.dispatch):
            result=seats.produce(self.root,self.out,author=self.author,verifier=self.verifier,
                private_evaluator_pair=('agy','private-evaluator'),deadline=self.deadline,cancellation=self.cancel,
                launch_environment=self.environment,host=self.host)
        self.assertTrue(all(c.intent.tool=='probe' and c.intent.argv==('/usr/bin/true',) for c in probe.calls))
        return result

    def test_two_actual_boundary_calls_produce_bound_records_and_replay(self):
        (self.root/'PRIVATE_EVALUATOR_SECRET').write_text('not inside artwork')
        before=tree_identity(self.candidate)
        result=self.produce()
        self.assertEqual((result['execution'],result['outcome']),('ok','criteria_satisfied'))
        self.assertEqual(self.calls,['judgment-author','judgment-verifier'])
        self.assertEqual(tree_identity(self.candidate),before)
        record=seats.verify(self.out,result)
        self.assertEqual(len(record['calls']),2)
        self.assertEqual(set(result['judgment_inventory']),{'decisions/span1.json','verification/'+identity(read_json(self.out/'judgments/decisions/span1.json')['span'])+'.json'})
        self.assertFalse((self.out/'calls/inputs/PRIVATE_EVALUATOR_SECRET').exists())
        target=self.out/'judgments/decisions/span1.json';target.write_text('{}')
        with self.assertRaises(ValueError):seats.verify(self.out,result)

    def test_honest_negative_and_dissent_never_become_success(self):
        for mode in ('negative','dissent','negative-bad-witness'):
            self.mode=mode;self.out=self.root/mode;self.calls=[]
            result=self.produce()
            self.assertNotEqual(result['outcome'],'criteria_satisfied')
            self.assertEqual(self.calls,['judgment-author','judgment-verifier'])
            if mode!='negative-bad-witness':
                self.assertEqual(result['outcome'],'criteria_failed');seats.verify(self.out,result)
            else:self.assertEqual(result['execution'],'malformed')

    def test_missing_duplicate_wrong_bindings_and_auth_stop_without_retry(self):
        for mode in ('stale','duplicate','wrong-witness','auth','wrong-model'):
            self.mode=mode;self.out=self.root/mode;self.calls=[]
            result=self.produce()
            self.assertNotEqual(result['outcome'],'criteria_satisfied')
            self.assertEqual(len(self.calls),2 if mode=='wrong-witness' else 1)
            self.assertTrue((self.out/'calls/review-attempts/judgment-author/0/response.json').exists())

    def test_policy_excludes_private_evaluator_and_same_pair_before_calls(self):
        for other,private in [(self.author,('agy','private')), (self.verifier,(self.author['harness'],self.author['model']))]:
            with self.assertRaises(ValueError):seats.produce(self.root,self.out,author=self.author,verifier=other,
                private_evaluator_pair=private,deadline=self.deadline,cancellation=self.cancel,launch_environment=self.environment,host=self.host)
        self.assertFalse(self.out.exists())

    def test_cancel_and_expired_clock_do_not_launch_or_publish(self):
        self.cancel.cancelled.set()
        with self.assertRaises(native_run.DeadlineExhausted):self.produce()
        self.assertEqual(self.calls,[]);self.assertFalse(self.out.exists())

    def test_source_mutation_refuses_and_preserves_attempt(self):
        self.mode='mutate'
        result=self.produce()
        self.assertNotEqual(result['outcome'],'criteria_satisfied')
        self.assertEqual(self.calls,['judgment-author'])

    def test_expired_and_late_response_do_not_publish(self):
        deadline=self.deadline
        self.deadline=time.monotonic()-1
        with self.assertRaises(native_run.DeadlineExhausted):self.produce()
        self.assertEqual(self.calls,[])
        self.deadline=deadline
        def late(*args,**kwargs):
            result=self.dispatch(*args,**kwargs)
            self.cancel.cancelled.set()
            return result
        with native_run.using(native_run.Recorded(),deadline=self.deadline,cancel=self.cancel), \
                patch('agent_ee.reviewers.dispatch',side_effect=late), self.assertRaises(native_run.DeadlineExhausted):
            seats.produce(self.root,self.out,author=self.author,verifier=self.verifier,
                private_evaluator_pair=('agy','private'),deadline=self.deadline,cancellation=self.cancel,
                launch_environment=self.environment,host=self.host)
        self.assertFalse((self.out/'judgments').exists())
        self.assertTrue((self.out/'producer-receipt.json').is_file())
