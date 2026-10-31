"""Guarded owned artwork/seat controls. All provider and native facts are CPU mocks."""
import copy
import hashlib
import json
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
        self.enterContext(patch('agent_ee.run_record.verify',side_effect=lambda run,**kw:read_json(Path(run)/'result.json')))

    def native_checks(self):
        # Only the native execution/replay boundary is substituted. The actual
        # selection, input/scope binding and copied evidence custody run below.
        from agent_ee.requirement_map import input_inventory
        selected=retained_run.review_source(self.root)
        target=self.out.with_name(self.out.name+'-native-checks')
        target.mkdir()
        write_json(target/'source.json',selected.selection)
        write_json(target/'report.json',dict(schema='agent-ee.build-review/1',accepted=False,
            source=selected.selection,candidate_id='CPU_MOCK_ONLY'))
        write_json(target/'review/candidate.json',dict(files={**input_inventory(self.candidate),
            'build-review/source.json':digest(target/'source.json')},scope=selected.scope))
        write_json(target/'review/result.json',dict(candidate_id='CPU_MOCK_ONLY',reviews=[],review_executions=[],adjudicated=False))
        (target/'review/work').mkdir()
        (target/'review/work/native-stdout.log').write_text('CPU_MOCK_ONLY current startup/removal outputs')
        (target/'review/.lock').touch()
        return target

    def dispatch(self, run, label, definition, prompt, schema, timeout, *, cancellation, executable):
        self.assertIs(cancellation,self.cancel)
        self.assertLessEqual(timeout,self.deadline-time.monotonic()+.1)
        self.assertNotIn('PRIVATE_EVALUATOR_SECRET',prompt)
        self.assertEqual((Path(run)/'work/build-stages/artwork/native-stdout.log').read_text(),'CPU_MOCK_ONLY retained native observation')
        self.assertEqual((Path(run)/'work/native-checks/review/work/native-stdout.log').read_text(),
                         'CPU_MOCK_ONLY current startup/removal outputs')
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
        probe=native_run.Recorded();checks=self.native_checks()
        with native_run.using(probe,deadline=self.deadline,cancel=self.cancel), patch('agent_ee.reviewers.dispatch',side_effect=self.dispatch):
            result=seats.produce(self.root,self.out,author=self.author,verifier=self.verifier,
                private_evaluator_pair=('agy','private-evaluator'),deadline=self.deadline,cancellation=self.cancel,
                launch_environment=self.environment,host=self.host,native_checks=checks)
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

    def test_stream_idle_verifier_recovery_keeps_failed_evidence_and_original_deadline(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        now=[time.monotonic()];self.deadline=now[0]+40
        timeouts=[];original=self.dispatch
        def dispatch(run,label,definition,prompt,schema,timeout,**kwargs):
            if label=='judgment-verifier':
                timeouts.append(timeout)
                if len(timeouts)==1:
                    now[0]+=4
                    return self.stream_idle_response(run,label,definition)
                self.assertNotIn('FAILED_REVIEW_NOT_REUSABLE',prompt)
                self.assertFalse((Path(run)/'review-work'/label/'experiments/partial').exists())
            return original(run,label,definition,prompt,schema,timeout,**kwargs)
        self.dispatch=dispatch
        with patch('time.monotonic',side_effect=lambda:now[0]):result=self.produce()
        self.assertEqual((result['execution'],result['outcome']),('ok','criteria_satisfied'))
        record=read_json(self.out/'calls/review-executions/judgment-verifier.json')
        self.assertEqual([a['execution'] for a in record['attempts']],['failed','ok'])
        self.assertEqual((record['max_schema_resubmissions'],record['resubmissions_used']),(0,0))
        self.assertEqual((record['max_transport_retries'],record['transport_retries_used']),(1,1))
        self.assertLessEqual(timeouts[1],timeouts[0]-4)
        failed=self.out/'calls'/record['attempts'][0]['path']
        self.assertIsNone(read_json(failed/'response.json'))
        self.assertIn('stream idle timeout',(failed/'review-work/judgment-verifier/stdout.log').read_text())
        seats.verify(self.out,result)

    def stream_idle_response(self,run,label,definition):
        work=Path(run)/'review-work'/label;work.mkdir(parents=True,exist_ok=True)
        error='API error (attempt 1): request failed: stream idle timeout (5m0s): context deadline exceeded'
        events=[dict(event='init',init=dict(model=definition['model']),conversation_id='failed-verifier'),
            dict(event='result',result=dict(status='ERROR',conversation_id='failed-verifier',error=error,
                structured_output={'unadmitted':'FAILED_REVIEW_NOT_REUSABLE'}))]
        (work/'stdout.log').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        (work/'experiments').mkdir();(work/'experiments/partial').write_text('FAILED_REVIEW_NOT_REUSABLE')
        return dict(execution='failed',backend='agy',model_requested=definition['model'],
                    failure_kind='stream_idle_timeout',error=error),None

    def verify_transport_custody(self):
        from agent_ee.review_run import verify_role_call
        capsule=self.out/'calls';record=read_json(capsule/'review-executions/judgment-verifier.json')
        verify_role_call(capsule,record,read_json(capsule/'launch-expectation.json'),
                         'judgment-verifier','initial',limit=0,transport_limit=1)
        return record

    def test_second_stream_idle_failure_is_terminal_and_both_attempts_remain_verifiable(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        original=self.dispatch
        def dispatch(run,label,definition,*args,**kwargs):
            if label=='judgment-verifier':return self.stream_idle_response(run,label,definition)
            return original(run,label,definition,*args,**kwargs)
        self.dispatch=dispatch
        result=self.produce()
        self.assertEqual((result['execution'],result['outcome']),('failed','incomplete'))
        record=self.verify_transport_custody()
        self.assertEqual([a['execution'] for a in record['attempts']],['failed','failed'])
        self.assertEqual((record['transport_retries_used'],record['resubmissions_used']),(1,0))
        self.assertFalse((self.out/'judgments').exists())

    def test_runtime_drift_before_retry_retains_the_refused_preflight(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        original=self.dispatch
        def dispatch(run,label,definition,*args,**kwargs):
            if label=='judgment-verifier':
                record=self.stream_idle_response(run,label,definition)
                self.host['runtime_files']={**self.host['runtime_files'],'agy':{'path':'changed','sha256':'0'*64}}
                return record
            return original(run,label,definition,*args,**kwargs)
        self.dispatch=dispatch
        result=self.produce()
        self.assertEqual((result['execution'],result['outcome']),('not_run','incomplete'))
        record=self.verify_transport_custody()
        self.assertEqual([a['execution'] for a in record['attempts']],['failed','not_run'])
        self.assertIn('runtime_drift',record['error'])
        self.assertFalse((self.out/'judgments').exists())

    def test_cancellation_or_role_expiry_retains_the_settled_failed_verifier(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        original=self.dispatch
        for stop in ('cancel','role-deadline'):
            with self.subTest(stop=stop):
                self.out=self.root/stop;self.cancel=native_run.Cancellation()
                now=[time.monotonic()];self.deadline=now[0]+40
                def dispatch(run,label,definition,*args,**kwargs):
                    if label=='judgment-verifier':
                        record=self.stream_idle_response(run,label,definition)
                        if stop=='cancel':self.cancel.cancelled.set()
                        else:now[0]+=21
                        return record
                    return original(run,label,definition,*args,**kwargs)
                self.dispatch=dispatch
                with patch('time.monotonic',side_effect=lambda:now[0]),self.assertRaises(native_run.DeadlineExhausted):
                    self.produce()
                record=self.verify_transport_custody()
                self.assertEqual([a['execution'] for a in record['attempts']],['failed'])
                self.assertIn('stop_reason',record)
                self.assertEqual(record['transport_retries_used'],0)
                self.assertFalse((self.out/'judgments').exists())

    def test_cancellation_after_scratch_cleanup_replays_the_archived_failed_attempt(self):
        import shutil
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        original=self.dispatch;remove=shutil.rmtree
        def dispatch(run,label,definition,*args,**kwargs):
            if label=='judgment-verifier':return self.stream_idle_response(run,label,definition)
            return original(run,label,definition,*args,**kwargs)
        def cleanup_then_cancel(path,*args,**kwargs):
            result=remove(path,*args,**kwargs)
            if Path(path)==self.out/'calls/review-work/judgment-verifier':self.cancel.cancelled.set()
            return result
        self.dispatch=dispatch
        with patch('agent_ee.review_run.shutil.rmtree',side_effect=cleanup_then_cancel), \
                self.assertRaises(native_run.DeadlineExhausted):self.produce()
        record=self.verify_transport_custody()
        self.assertEqual([a['execution'] for a in record['attempts']],['failed'])
        self.assertIn('transport_retry_blocked',record)
        self.assertFalse((self.out/'calls/review-work/judgment-verifier').exists())
        self.assertTrue((self.out/'calls'/record['attempts'][0]['path']/'review-work/judgment-verifier/preflight/receipt.json').is_file())

    def test_claimed_transport_failure_without_matching_trace_does_not_retry(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        original=self.dispatch
        def dispatch(run,label,definition,*args,**kwargs):
            if label=='judgment-verifier':
                record=self.stream_idle_response(run,label,definition)
                stream=Path(run)/'review-work'/label/'stdout.log'
                rows=[json.loads(line) for line in stream.read_text().splitlines()]
                rows[-1]['result']['error']='401 Unauthorized: subscription authentication failed'
                stream.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
                return record
            return original(run,label,definition,*args,**kwargs)
        self.dispatch=dispatch
        result=self.produce();record=self.verify_transport_custody()
        self.assertEqual((result['execution'],result['outcome']),('failed','incomplete'))
        self.assertEqual([a['execution'] for a in record['attempts']],['failed'])
        self.assertEqual(record['transport_retries_used'],0)
        self.assertFalse((self.out/'judgments').exists())

    def test_legacy_receipt_replays_one_attempt_without_acquiring_transport_permission(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        result=self.produce();execution=self.out/'calls/review-executions/judgment-verifier.json'
        record=read_json(execution);record.pop('max_transport_retries');record.pop('transport_retries_used')
        receipt_path=self.out/'producer-receipt.json';receipt=read_json(receipt_path)
        # Owned CPU fixture shaped like the existing version-3 single-attempt receipts.
        receipt['schema']='agent-ee.judgment-seat-production/3'
        for invented_permission in (False,True):
            with self.subTest(invented_permission=invented_permission):
                if invented_permission:record.update(max_transport_retries=1,transport_retries_used=0)
                write_json(execution,record);receipt['calls'][1]['sha256']=digest(execution)
                write_json(receipt_path,receipt);result['producer_receipt_sha256']=digest(receipt_path)
                if invented_permission:
                    with self.assertRaisesRegex(ValueError,'transport retry declaration changed'):seats.verify(self.out,result)
                else:seats.verify(self.out,result)

    def test_receipt_replay_cannot_extend_the_verifier_role_deadline(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        now=[time.monotonic()];self.deadline=now[0]+40
        original=Path.read_text;delayed=[]
        receipt=self.out/'calls/review-attempts/judgment-verifier/0/execution.json'
        def delayed_read(path,*args,**kwargs):
            data=original(path,*args,**kwargs)
            if path==receipt and not delayed:
                delayed.append(path);now[0]+=21
            return data
        with patch('time.monotonic',side_effect=lambda:now[0]),patch.object(Path,'read_text',delayed_read), \
                self.assertRaises(native_run.DeadlineExhausted):self.produce()
        self.assertTrue(receipt.is_file())
        self.assertEqual(read_json(receipt)['execution'],'ok')
        self.assertFalse((self.out/'judgments').exists())
        self.assertEqual(read_json(self.out/'producer-receipt.json')['outcome'],'incomplete')

    def test_transport_counter_types_cannot_change_the_replayed_policy(self):
        self.verifier.update(harness='agy',model='gemini-4-argon-high')
        self.host['runtime_files']['agy']=self.host['runtime_files']['claude']
        result=self.produce();execution=self.out/'calls/review-executions/judgment-verifier.json'
        original=read_json(execution);receipt_path=self.out/'producer-receipt.json';base=read_json(receipt_path)
        cases=[('boolean-limit',{'max_transport_retries':True},seats.SCHEMA),
               ('float-limit',{'max_transport_retries':1.0},seats.SCHEMA),
               ('boolean-used',{'transport_retries_used':False},seats.SCHEMA),
               ('float-used',{'transport_retries_used':0.0},seats.SCHEMA),
               ('legacy-counter',{'transport_retries_used':0},seats.LEGACY_SCHEMA)]
        for name,change,version in cases:
            with self.subTest(name=name):
                record=copy.deepcopy(original);receipt=copy.deepcopy(base)
                if version==seats.LEGACY_SCHEMA:
                    record.pop('max_transport_retries');record.pop('transport_retries_used')
                record.update(change);write_json(execution,record)
                receipt['schema']=version;receipt['calls'][1]['sha256']=digest(execution)
                write_json(receipt_path,receipt);result['producer_receipt_sha256']=digest(receipt_path)
                with self.assertRaisesRegex(ValueError,'transport retry'):seats.verify(self.out,result)

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

    def test_brief_identifies_nontext_citations_without_hiding_native_inputs(self):
        # The actual author cited valid ASCII comments in non-UTF8 SPICE files.
        # Those files remain available, but cannot satisfy exact UTF8 citations.
        opaque = b'* Manufacturer model\r\n* current in \xb5A\r\n'
        text = b'Captured source\r\nExact quote\r\n'
        (self.candidate/'model.lib').write_bytes(opaque)
        (self.candidate/'manufacturer.txt').write_bytes(text)
        for row in self.control.report['completed']:
            row['tree']=tree_identity(self.root/row['directory'])
        self.control.save()
        capsule, manifest, data = seats.brief(self.root,self.out,self.deadline,self.cancel)
        self.assertEqual((capsule/'inputs/model.lib').read_bytes(),opaque)
        self.assertEqual(manifest['files']['model.lib'],hashlib.sha256(opaque).hexdigest())
        self.assertEqual(data['citation_policy']['encoding'],'UTF-8')
        self.assertEqual(data['citation_policy']['normalization'],'none')
        self.assertIn('model.lib',data['citation_policy']['excluded_files'])
        self.assertNotIn('manufacturer.txt',data['citation_policy']['excluded_files'])
        self.assertEqual(seats.source_text(capsule,data['files'])['manufacturer.txt'],text.decode('utf-8'))

    def test_brief_supplies_declared_scope_and_complete_requirement_source(self):
        capsule, manifest, data = seats.brief(self.root,self.out,self.deadline,self.cancel)
        self.assertEqual(data['review_scope']['statement'],manifest['scope']['scope'])
        self.assertEqual(data['review_scope']['qualification_claim'],qualification.CLAIM)
        self.assertEqual(data['review_scope']['separate_obligations'],['supplier','physical'])
        source=data['requirement_sources']['spec.md']
        self.assertEqual(source['sha256'],data['files']['spec.md'])
        self.assertEqual(source['text'].encode('utf-8'),(capsule/'inputs/spec.md').read_bytes())
        row=data['requirements'][0]
        self.assertEqual(source['text'].encode('utf-8')[row['span']['byte_start']:row['span']['byte_end']],row['text'].encode('utf-8'))

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

    def test_current_native_checks_refuse_foreign_selection_scope_and_files_before_seats(self):
        for part in ('selection','scope','files','prior-testimony'):
            with self.subTest(part=part):
                self.out=self.root/part
                checks=self.native_checks()
                if part=='selection':
                    write_json(checks/'source.json',{'foreign':'CPU_MOCK_ONLY'})
                elif part in ('scope','files'):
                    path=checks/'review/candidate.json';data=read_json(path)
                    data[part]['foreign']='CPU_MOCK_ONLY';write_json(path,data)
                else:
                    path=checks/'review/result.json';data=read_json(path)
                    data['reviews']=[{'prior':'CPU_MOCK_ONLY'}];write_json(path,data)
                with self.assertRaises(ValueError):
                    seats.produce(self.root,self.out,author=self.author,verifier=self.verifier,
                        private_evaluator_pair=('agy','private'),deadline=self.deadline,cancellation=self.cancel,
                        launch_environment=self.environment,host=self.host,native_checks=checks)
                self.assertEqual(self.calls,[])

    def test_copied_native_observation_mutation_refuses_replay(self):
        result=self.produce()
        (self.out/'calls/work/native-checks/review/work/native-stdout.log').write_text('foreign observation')
        with self.assertRaisesRegex(ValueError,'native evidence changed'):
            seats.verify(self.out,result)

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
                launch_environment=self.environment,host=self.host,native_checks=self.out.with_name(self.out.name+'-native-checks'))
        self.assertFalse((self.out/'judgments').exists())
        self.assertTrue((self.out/'producer-receipt.json').is_file())
