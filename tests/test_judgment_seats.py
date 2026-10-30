"""Guarded owned artwork/seat controls. All provider and native facts are CPU mocks."""
import copy
import hashlib
import os
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
        self.calls=[];self.mode='pass';self.citation_path='spec.md'

    def dispatch(self, run, label, definition, prompt, schema, timeout, *, cancellation, executable):
        self.assertIs(cancellation,self.cancel)
        self.assertLessEqual(timeout,self.deadline-time.monotonic()+.1)
        self.assertNotIn('PRIVATE_EVALUATOR_SECRET',prompt)
        self.assertEqual((Path(run)/'work/build-stages/artwork/native-stdout.log').read_text(),'CPU_MOCK_ONLY retained native observation')
        data=read_json(Path(run)/'packet.json');self.calls.append(label)
        if label=='judgment-author':
            contract=schema if 'judgments' in schema['properties'] else read_json(Path(run)/'work/judgment-contract.json')
            props=contract['properties']['judgments']['items']['properties']['author']['properties']
            metadata={k:v['enum'][0] for k,v in props.items()}
            req=data['requirements'][0]
            doc=dict(schema='agent-ee.engineering-judgment/1',requirement=req['requirement'],span=req['span'],
                conclusion='CPU_MOCK_ONLY authored reasoning about the captured control.',assumptions=['Owned fixture only'],
                sources=[dict(kind='engineering',citation=dict(path=self.citation_path,sha256=data['files'][self.citation_path],
                    quote=(Path(run)/'inputs'/self.citation_path).read_bytes().decode('utf-8'),locator='whole owned fixture'))],
                inputs=[dict(path='spec.md',sha256=data['files']['spec.md'])],applicability=data['applicability'],candidate=data['binding'],author=metadata,
                unresolved_defects=['owned unresolved problem'] if self.mode in ('negative','negative-bad-witness') else [])
            if self.mode=='stale':doc['candidate']['board_sha256']='0'*64
            if self.mode=='missing-facts':doc['applicability']={}
            if self.mode=='extra-citation':doc['sources'][0]['citation']['unbound_claim']='not a source field'
            if self.mode=='duplicate':value={'judgments':[doc,doc]}
            else:value={'judgments':[doc]}
            if self.mode=='mutate':(Path(run)/'inputs/spec.md').write_text('changed')
            if self.mode=='file' or 'judgments' not in schema['properties']:
                target=Path(run)/'review-work/judgment-author/engineering-seat/judgments.json'
                write_json(target,value)
                value=dict(schema='agent-ee.judgment-delivery/1',path='engineering-seat/judgments.json',sha256=digest(target))
                if self.mode=='wrong-hash':value['sha256']='0'*64
                if self.mode=='missing-file':target.unlink()
                if self.mode in ('symlink','hardlink'):
                    other=target.with_name('other.json');target.rename(other)
                    if self.mode=='symlink':target.symlink_to(other)
                    else:os.link(other,target)
                if self.mode=='fifo':target.unlink();os.mkfifo(target)
                if self.mode=='duplicate-json-key':
                    target.write_text('{"judgments":[],"judgments":[]}')
                    value['sha256']=digest(target)
                if self.mode=='oversize':
                    with target.open('r+b') as stream:stream.truncate(4*1024*1024+1)
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
        if self.mode=='timeout':return {'execution':'timeout','error':'CPU_MOCK_ONLY expired provider'},None
        if self.mode=='no-final':value=None
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

    def test_file_delivery_requires_success_and_replays_the_archived_author_bytes(self):
        self.mode='file'
        result=self.produce()
        self.assertEqual((result['execution'],result['outcome']),('ok','criteria_satisfied'))
        response=self.out/'calls/review-attempts/judgment-author/0/response.json'
        self.assertLess(len(response.read_bytes()),250)
        self.assertEqual(read_json(response)['path'],'engineering-seat/judgments.json')
        live=self.out/'calls/review-work/judgment-author/engineering-seat/judgments.json'
        live.write_text('{}')
        seats.verify(self.out,result)
        archive=self.out/'calls/review-attempts/judgment-author/0/review-work/judgment-author/engineering-seat/judgments.json'
        archive.write_text('{}')
        with self.assertRaises(ValueError):seats.verify(self.out,result)

    def test_captured_crlf_source_keeps_its_exact_hash_and_quote(self):
        self.citation_path='manufacturer.txt'
        (self.candidate/self.citation_path).write_bytes(b'Captured manufacturer text\r\nExact second line\r\n')
        for row in self.control.report['completed']:
            row['tree']=tree_identity(self.root/row['directory'])
        self.control.save()
        result=self.produce()
        self.assertEqual(result['outcome'],'criteria_satisfied')
        seats.verify(self.out,result)

    def test_completed_scratch_without_success_or_final_response_is_never_recovered(self):
        for mode in ('timeout','no-final','auth'):
            self.mode=mode;self.out=self.root/mode;self.calls=[]
            result=self.produce()
            self.assertNotEqual(result['execution'],'ok')
            self.assertEqual(self.calls,['judgment-author'])
            self.assertTrue((self.out/'calls/review-work/judgment-author/engineering-seat/judgments.json').is_file())
            self.assertFalse((self.out/'judgments').exists())

    def test_oversized_sparse_delivery_is_rejected_before_reading_the_payload(self):
        self.mode='oversize'
        result=self.produce()
        self.assertNotEqual(result['execution'],'ok')
        record=read_json(self.out/'calls/review-executions/judgment-author.json')
        self.assertIn('exceeds byte limit',record['error'])
        self.assertEqual(self.calls,['judgment-author'])

    def test_file_delivery_refuses_invalid_file_custody_and_record_shapes(self):
        for mode in ('wrong-hash','missing-file','symlink','hardlink','fifo',
                     'duplicate-json-key','missing-facts','extra-citation'):
            self.mode=mode;self.out=self.root/mode;self.calls=[]
            result=self.produce()
            self.assertNotEqual(result['execution'],'ok',mode)
            self.assertEqual(self.calls,['judgment-author'],mode)
            self.assertFalse((self.out/'judgments').exists(),mode)

    def test_mutation_between_author_validation_and_archival_refuses_before_witness(self):
        import shutil
        copytree=shutil.copytree
        def altered(source,destination,*args,**kwargs):
            source=Path(source)
            if source.name=='judgment-author' and source.parent.name=='review-work':
                (source/'engineering-seat/judgments.json').write_text('{}')
            return copytree(source,destination,*args,**kwargs)
        with patch('agent_ee.review_run.shutil.copytree',side_effect=altered):
            result=self.produce()
        self.assertNotEqual(result['execution'],'ok')
        self.assertEqual(self.calls,['judgment-author'])
        self.assertFalse((self.out/'judgments').exists())

    def test_prompts_reference_complete_readonly_evidence_without_inline_inventory(self):
        self.produce()
        for role in ('judgment-author','judgment-verifier'):
            prompt=(self.out/'calls/review-attempts'/role/'0/prompt.txt').read_text()
            self.assertIn('/evidence/packet.json',prompt)
            self.assertNotIn('"files":',prompt)
            self.assertNotIn('"brief":',prompt)

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
