"""Pure generated-child protocol controls; no electrical/native qualification claimed."""
import contextlib
import copy
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_ee import acquisition_completion as completion, campaign, generation_benchmark as benchmark
from agent_ee import native_run, retained_run, run_record
from agent_ee.check_record import CheckRecord
from agent_ee.storage import digest, read_json, write_json
from tests.test_generation_benchmark import RefusalTests


class PublicCaseContextTests(unittest.TestCase):
    def setUp(self):
        RefusalTests.setUp(self)
        self.document = dict(schema='agent-ee.case-context/1', catalog_policy=None)
        task = read_json(self.task/'task.json'); task['public_context'] = 'public-case.json'
        write_json(self.task/'task.json', task)
        self.repin()

    def repin(self):
        write_json(self.task/'public-case.json', self.document)
        write_json(self.task/'pins.json', dict(schema='agent-ee.generation-pins/1',
            files={k:v for k,v in benchmark.tree_files(self.task).items() if k != 'pins.json'}))

    def test_public_context_transport_external_series_and_replay(self):
        generator = self.root/'generator.py'; generator.write_text('# protocol fixture')
        def generate(call):
            self.assertEqual(set(benchmark.tree_files(call.inputs)), {'generator.py','prompt.md','public-case.json'})
            self.assertEqual(read_json(call.inputs/'public-case.json'), self.document)
            self.assertNotIn('evaluator', repr(call.mounts))
            return native_run.Outcome(files={'output/refusal.json': self.payload})
        with native_run.using(native_run.Recorded(generate)):
            report, = benchmark.run_task(self.task, generator, self.root/'trial', self.root/'store',
                attempts=1, timeout=30, case_timeout=100, grading_reserve=20)
        record = read_json(report)
        pin = digest(self.task/'public-case.json')
        self.assertEqual(record['binding']['case']['public_context_sha256'], pin)
        self.assertEqual(read_json(self.root/'trial/trial.json')['public_context_sha256'], pin)
        frozen = benchmark.freeze_series([report], {record['task']: digest(self.task/'pins.json')})
        self.assertIn('case_files', frozen['evaluations'][0])
        self.assertTrue(benchmark.recorded_report(report)['result']['overall_pass'])
        (self.root/'trial/public/public-case.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'public context changed'):
            benchmark.recorded_report(report)

    def test_exact_wire_shape_and_private_path_rejected_before_generation(self):
        for change in [dict(extra=True), dict(schema='unknown'),
                dict(catalog_policy=dict(kind='stockout', label='x', exclude=[])),
                dict(catalog_policy=dict(kind='evaluation_exclusion',
                    label='evaluation scenario; not an observed supplier stockout', exclude=[]))]:
            with self.subTest(change=change):
                original = self.document
                self.document = dict(original, **change); self.repin()
                with self.assertRaises(ValueError): benchmark.pinned_task(self.task)
                self.document = original
        self.repin()
        task = read_json(self.task/'task.json'); task['public_context']='evaluator/context.json'
        write_json(self.task/'task.json',task)
        (self.task/'evaluator').mkdir(exist_ok=True)
        write_json(self.task/'evaluator/context.json',self.document)
        write_json(self.task/'pins.json', dict(schema='agent-ee.generation-pins/1',
            files={k:v for k,v in benchmark.tree_files(self.task).items() if k != 'pins.json'}))
        with self.assertRaisesRegex(ValueError,'public case-context path'):benchmark.pinned_task(self.task)


class GeneratedAddrTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        source=self.root/'source'; source.mkdir()
        for name in ('board','schematic','requirements','targets','reference','adapter.py','solver.py'):
            (source/name).write_text('protocol fixture '+name)
        self.definition=dict(harness='codex',model='scripted',effort='high',timeout_s=10)
        self.profile=dict(schema='agent-ee.profile/1',name='protocol',tool_mode='fixed_packet',
            reviewers=[dict(self.definition,role=r) for r in ('circuit','physical')],adjudicator=self.definition)
        self.scope=dict(schema='agent-ee.scope/2',name='public-address-protocol',scope='Pure boundary fixture',
            board='design/board',schematic='design/schematic',inputs=[dict(path=str(source),at='design')],
            review_profile=self.profile,checks=[dict(id='adc-ties',kind='measurements',params=dict(
                adapter='design/adapter.py',tool=dict(argv=['/usr/bin/python3','/candidate/design/solver.py'],
                    sha256=digest('/usr/bin/python3'),domain='electrical',inputs=['design/solver.py']),
                limits={'address':dict(unit='V',condition='protocol',max=0)}))],
            baseline=dict(requirements=[dict(id='ACQ-ELEC-6-TIES',statement='Fixed public address requirement',
                source=dict(path='design/requirements',locator='whole'),checks=['adc-ties'])],
                targets=['design/targets'],reference_architectures=['design/reference']))
        write_json(self.root/'scope.json',self.scope)
        runtime={name:dict(path='/usr/bin/true',sha256=digest('/usr/bin/true')) for name in
            ('codex','codex_tool_host','bwrap','/usr/bin/python3')}
        self.host=dict(policy_files=run_record.policy_files(),runtime_files=runtime)
        self.environment=dict(run_id='original-case',**self.host)
        self.cancel=native_run.Cancellation();self.deadline=time.monotonic()+100
        self.calls=[];self.address='GND';self.fail=True
        def git(argv, **kwargs):
            self.assertEqual(argv[0], 'git')
            return dict(execution='ok',exit_code=0,returncode=0), b'cpu-protocol-source\n', b''
        self.enterContext(patch.object(native_run,'supervise',side_effect=git))
        self.enterContext(patch('agent_ee.reviewers.dispatch',side_effect=self.answer))
        self.enterContext(patch('agent_ee.candidate_visuals.capture_visuals',side_effect=lambda run:read_json(run/'candidate.json')))
        self.enterContext(patch('agent_ee.checks.run_checks',side_effect=self.checks))
        self.enterContext(native_run.using(native_run.Recorded(lambda call:native_run.Outcome()),
                                           deadline=self.deadline,cancel=self.cancel))
        self.first=self.root/'first'
        run_record.run_review(self.root/'scope.json',self.first,90,host=self.host,max_schema_resubmissions=0)

    def tearDown(self):
        evidence=os.environ.get('AGENT_EE_COMPLETION_EVIDENCE')
        if evidence:
            shutil.copytree(self.root,Path(evidence)/self._testMethodName,symlinks=True)

    def checks(self,run,deadline,**kwargs):
        self.assertLessEqual(deadline,self.deadline)
        manifest=read_json(run/'candidate.json')
        raw=run/'work/adc-ties/raw.json';write_json(raw,dict(protocol='synthetic check outcome',defect=self.fail))
        result=CheckRecord('adc-ties','measurements',manifest['candidate_id'],execution='ok',
            outcome='defect' if self.fail else 'pass',summary='Synthetic protocol check',
            evidence=['work/adc-ties/raw.json'])
        write_json(run/'checks/adc-ties.json',result)
        return [result]

    def answer(self,run,role,definition,prompt,schema,timeout,*,cancellation=None,executable=None):
        self.assertIs(native_run._cancel.get(),self.cancel)
        # The explicit inherited object reaches both initial peers and the implementation call.
        if role!='adjudicator':self.assertIs(cancellation,self.cancel)
        self.calls.append(role)
        if role=='implementation':
            return dict(execution='ok'),dict(summary='Correct actual public ADDR finding',addr_tie=self.address,
                                          finding_ids=['check/adc-ties'])
        packet=read_json(run/'packet.json');ids={k:packet[k] for k in ('candidate_id','run_id')}
        if role=='adjudicator':
            return dict(execution='ok'),dict(schema='agent-ee.decisions/2',**ids,
                decisions=[dict(finding_id='check/adc-ties',disposition='accepted',reason='Actual sealed defect',
                    evidence=[dict(path='checks/adc-ties.json',locator='outcome',pointer='/outcome')])] if self.fail else [])
        return dict(execution='ok'),dict(schema='agent-ee.review/2',**ids,packet_sha256=packet['packet_sha256'],
            role=role,coverage=['protocol'],limitations=['Scripted CPU responses; no electrical claim'],findings=[])

    def select(self):
        return campaign.select_generated_addr(self.root/'repair',self.first,definition=self.definition,
            launch_environment=self.environment,deadline=self.deadline,cancellation=self.cancel,host=self.host)

    def test_actual_sealed_finding_one_writer_full_baseline_and_convergence(self):
        self.assertEqual(run_record.verify(self.first,host=self.host)['outcome'],'defects_or_unresolved')
        self.assertEqual(self.select()['addr_tie'],'GND')
        self.assertEqual(self.calls.count('implementation'),1)
        self.assertEqual(campaign.verify_generated_addr(self.root/'repair',host=self.host)['addr_tie'],'GND')
        self.fail=False
        # A new generated native subject has changed coordinates/bytes; immutable requirements stay exact.
        source=self.root/'new-source';shutil.copytree(self.scope['inputs'][0]['path'],source)
        (source/'board').write_text('new generated native subject')
        scope=copy.deepcopy(self.scope);scope['inputs'][0]['path']=str(source)
        write_json(self.root/'successor-scope.json',scope)
        successor=self.root/'successor'
        run_record.run_review(self.root/'successor-scope.json',successor,90,host=self.host,max_schema_resubmissions=0)
        verified=campaign.verify_generated_addr(self.root/'repair',successor=successor,host=self.host)
        self.assertEqual(verified['state'],'criteria_satisfied');self.assertEqual(len(verified['history']),2)
        (self.root/'repair/review-attempts/implementation/0/response.json').write_text('{}')
        with self.assertRaises(ValueError):campaign.verify_generated_addr(self.root/'repair',host=self.host)

    def test_noop_writer_retained_once_without_schema_retry(self):
        self.address='V3V3'
        with self.assertRaisesRegex(ValueError,'implementation did not complete'):self.select()
        self.assertEqual(self.calls.count('implementation'),1)
        record=read_json(self.root/'repair/implementation-execution.json')
        self.assertEqual(record['max_schema_resubmissions'],0);self.assertEqual(len(record['attempts']),1)
        self.assertEqual(record['execution'],'malformed')
        self.assertTrue((self.root/'repair/review-attempts/implementation/0/response.json').exists())

    def test_changed_immutable_baseline_fails_even_with_new_valid_review(self):
        self.select();self.fail=False
        source=self.root/'new-source';shutil.copytree(self.scope['inputs'][0]['path'],source)
        (source/'requirements').write_text('weakened requirement')
        scope=copy.deepcopy(self.scope);scope['inputs'][0]['path']=str(source)
        write_json(self.root/'successor-scope.json',scope)
        successor=self.root/'successor'
        run_record.run_review(self.root/'successor-scope.json',successor,90,host=self.host,max_schema_resubmissions=0)
        with self.assertRaises(ValueError):campaign.verify_generated_addr(self.root/'repair',successor=successor,host=self.host)

    def test_missing_or_changed_actual_check_artifacts_refuses_before_writer(self):
        (self.first/'work/adc-ties/raw.json').write_text('changed')
        with self.assertRaises(ValueError):self.select()
        self.assertNotIn('implementation',self.calls)

    def test_real_public_prefix_review_is_diagnostic_with_original_clock_and_cancel(self):
        from agent_ee.storage import identity
        from agent_ee.stage_receipts import tree_identity
        production=self.root/'production-1';build=production/'build';(build/'source').mkdir(parents=True)
        (build/'source/seed').write_text('Synthetic retained build custody, no native acceptance')
        (build/'.lock').touch();rows=[]
        for index, stage in enumerate(retained_run.STAGES[:4],1):
            directory=build/'stages'/f'{index:02d}-{stage}'
            candidate=directory/'candidate';(candidate/'design').mkdir(parents=True)
            for name in Path(self.scope['inputs'][0]['path']).iterdir():
                shutil.copyfile(name,candidate/'design'/name.name)
            if stage=='scaffold':write_json(directory/'product/scope.json',self.scope)
            rows.append(dict(stage=stage,directory=str(directory.relative_to(build)),
                candidate=str(candidate.relative_to(build)),tree=tree_identity(directory)))
        inputs=dict(plan_sha256='a'*64,source={'seed':digest(build/'source/seed')},runtime=self.host)
        write_json(build/'build-report.json',dict(schema=retained_run.REPORT_SCHEMA,identity=identity(inputs),
            inputs=inputs,completed=rows,candidate=rows[-1]['candidate'],status='failed',accepted=False))
        common=self.root/'common';common.mkdir();(common/'inputs').mkdir()
        write_json(common/'profile.json',self.profile);write_json(common/'plan.json',{'source':'inputs'})
        write_json(common/'context.json',dict(schema=completion.CONTEXT,plan='plan.json',added_sources={},review_profile='profile.json'))
        write_json(common/'pins.json',dict(schema=completion.PINS,files={k:v for k,v in benchmark.tree_files(common).items() if k!='pins.json'}))
        before=tree_identity(build)
        # Host observations are a system boundary; every review/check/campaign verifier below is real.
        with patch.object(run_record,'runtime_files',return_value=self.host['runtime_files']), \
                patch('agent_ee.preflight.runtime_files',return_value=self.host['runtime_files']):
            diagnostic=completion.failed_public_review(production,common,digest(common/'pins.json'),self.deadline,self.cancel)
        self.assertEqual(tree_identity(build),before)
        report=read_json(production/'failed-public-review/report.json')
        self.assertFalse(report['accepted'])
        self.assertEqual(report['qualification']['dimensions']['file_release'],'not_assessed')
        self.assertEqual(diagnostic['source']['selected']['stage'],'schematic')
        self.assertEqual(campaign.addr_feedback(production/'failed-public-review/review',host=self.host)[0]['max_schema_resubmissions'],0)

    def test_fixed_child_uses_verified_writer_selector_and_original_preparation(self):
        production=self.root/'case/production-2';production.mkdir(parents=True)
        campaign.select_generated_addr(production/'repair',self.first,definition=self.definition,
            launch_environment=self.environment,deadline=self.deadline,cancellation=self.cancel,host=self.host)
        common=self.root/'common';common.mkdir();(common/'original-inputs').mkdir()
        write_json(common/'plan.json',{'source':'original-inputs'});write_json(common/'profile.json',self.profile)
        write_json(common/'context.json',dict(schema=completion.CONTEXT,plan='plan.json',added_sources={},review_profile='profile.json',
            prepare_options={'addr_tie':'V3V3'}))
        write_json(common/'pins.json',dict(schema=completion.PINS,files={k:v for k,v in benchmark.tree_files(common).items() if k!='pins.json'}))
        brief=self.root/'prompt.md';brief.write_text('actual public brief')
        request=self.root/'request';request.mkdir()
        write_json(request/'request.json',dict(schema=completion.REQUEST,brief_sha256=digest(brief),action='repair'))
        observed=[]
        def prepare(actual_brief,plan,out,**kwargs):
            self.assertEqual(actual_brief,brief);self.assertEqual(plan,common/'plan.json')
            self.assertFalse(out.exists());self.assertEqual(kwargs['addr_tie'],'GND')
            self.assertIs(native_run._cancel.get(),self.cancel);self.assertEqual(native_run._deadline.get(),self.deadline)
            observed.append(out);out.mkdir();write_json(out/'preparation.json',{'synthetic':'fresh original source preparation boundary'})
        with patch('agent_ee.acquisition_prepare.prepare',side_effect=prepare), \
                patch.object(run_record,'runtime_files',return_value=self.host['runtime_files']):
            selection=production/'repair/selection.json'
            completion.produce(common,digest(common/'pins.json'),brief,request,production,attempt=2,deadline=self.deadline,
                repair_selection=selection,expect_repair_selection=digest(selection))
        self.assertEqual(observed,[production/'prepared'])
        self.assertEqual(read_json(production/'completion.json'),{'kind':'prepared'})
        self.assertFalse((production/'build').exists()) # Prepared cannot substitute for all seven stages.

    def test_expired_case_cannot_start_selector(self):
        with self.assertRaises(native_run.DeadlineExhausted):
            campaign.select_generated_addr(self.root/'repair',self.first,definition=self.definition,
                launch_environment=self.environment,deadline=time.monotonic()-1,cancellation=self.cancel,host=self.host)
        self.assertNotIn('implementation',self.calls)


class ReviewBudgetInheritanceTests(unittest.TestCase):
    def test_retained_review_default_and_opt_in_zero_are_distinct(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(retained_run,'write_scope'), patch.object(retained_run,'verify_stage_review',return_value={}), \
                patch.object(retained_run,'run_review') as review:
            retained_run.review_candidate({},root,12)
            self.assertNotIn('max_schema_resubmissions',review.call_args.kwargs)
            with retained_run.review_resubmissions(0):retained_run.review_candidate({},root,12)
            self.assertEqual(review.call_args.kwargs['max_schema_resubmissions'],0)
            retained_run.review_candidate({},root,12)
            self.assertNotIn('max_schema_resubmissions',review.call_args.kwargs)

    def test_setup_and_imaging_cannot_extend_inherited_deadline(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        scope=root/'scope.json';write_json(scope,{})
        clock=[100.0]; seen=[]
        manifest=dict(candidate_id='cpu',files={'board':'x','schematic':'y'},scope=dict(
            name='cpu',scope='deadline protocol',board='board',schematic='schematic',checks=[],limitations=[]))
        def capture(*args,**kwargs):clock[0]+=7;return manifest
        def imaging(*args):clock[0]+=8;return manifest
        def checks(run,deadline,**kwargs):
            seen.append((clock[0],deadline,native_run._cancel.get()))
            raise ValueError('stop after observing the actual check deadline')
        cancel=native_run.Cancellation()
        with patch('time.monotonic',lambda:clock[0]),native_run.using(deadline=120,cancel=cancel), \
                patch.object(run_record,'capture',side_effect=capture),patch.object(run_record,'validate_scope'), \
                patch('agent_ee.board_envelope.final_qualification',return_value=None), \
                patch('agent_ee.candidate_visuals.capture_visuals',side_effect=imaging), \
                patch.object(run_record,'save_result',side_effect=lambda run,result:result.update(outcome='incomplete')), \
                patch.object(run_record,'recover_records'), \
                patch('agent_ee.rf_review.capture_routing'),patch('agent_ee.rf_review.decide_run',return_value={'required':False,'basis':[]}), \
                patch('agent_ee.preflight.capture_expectation',return_value={}), \
                patch('agent_ee.checks.run_checks',side_effect=checks):
            run_record.run_review(scope,root/'review',100,checks_only=True,
                host=dict(policy_files=run_record.LOADED_POLICY_FILES,runtime_files={}))
        self.assertEqual(seen,[(115.0,120,cancel)])


class PolicyRequestBindingTests(unittest.TestCase):
    def setUp(self):
        RefusalTests.setUp(self)
        self.public=self.root/'public-context';self.public.mkdir();(self.public/'inputs').mkdir()
        write_json(self.public/'plan.json',{'source':'inputs'});write_json(self.public/'profile.json',{})
        write_json(self.public/'context.json',dict(schema=completion.CONTEXT,plan='plan.json',added_sources={},review_profile='profile.json'))
        self.context_pin=self.repin_context()
        self.generator=self.root/'generator.py';self.generator.write_text('# public request producer')
        self.adapter=native_run.Recorded(self.child);self.cancel=native_run.Cancellation()
        self.generation_files=None;self.calls=[]

    # Reuse only the fixture methods, not its unrelated test methods.
    from tests.test_acquisition_completion import AcquisitionBoundaryTests as _Fixture
    repin_context=_Fixture.repin_context
    child=_Fixture.child
    run_acquisition=_Fixture.run_acquisition

    def test_requested_policy_pin_survives_ledger_binding_series_and_replay(self):
        policy=self.root/'policy.json'
        write_json(policy,dict(author=dict(harness='codex',model='author',effort='high',timeout_s=10),
            verifier=dict(harness='claude',model='verifier',effort='high',timeout_s=10),
            private_evaluator_pair=['agy','evaluator'],launch_environment=dict(run_id='case',policy_files={},runtime_files={})))
        pin=digest(policy)
        report,=self.run_acquisition(completion_policy=policy,expect_completion_policy=pin)
        result=read_json(report)
        self.assertTrue(result['result']['overall_pass'])
        self.assertEqual(result['binding']['case']['completion_policy_sha256'],pin)
        self.assertEqual(read_json(self.root/'trial/trial.json')['completion_policy_sha256'],pin)
        self.assertEqual(read_json(self.root/'trial/completion-1/receipt.json')['completion_policy_sha256'],pin)
        frozen=benchmark.freeze_series([report],{'public-refusal':digest(self.task/'pins.json')})
        self.assertEqual(frozen['evaluations'][0]['case_files']['completion-policy.json'],pin)
        (self.root/'trial/completion-policy.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'completion policy'):benchmark.recorded_report(report)


class PublicCatalogAdmissionTests(unittest.TestCase):
    """Use the actual producer policy reader, before any generation admission."""
    def setUp(self):
        from agent_ee import acquisition_resistor
        self.producer=acquisition_resistor
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.common=self.root/'common';self.common.mkdir();(self.common/'inputs').mkdir()
        self.brief=self.root/'prompt.md'
        self.brief.write_text('Use acquisition reference acquisition-control unchanged.\n'
            'Total I2C line capacitance shall be at most 150 pF per line, including board, host and cable.\n')
        write_json(self.common/'plan.json',{'source':'inputs'})
        write_json(self.common/'profile.json',{})
        write_json(self.common/'context.json',dict(schema=completion.CONTEXT,plan='plan.json',added_sources={},review_profile='profile.json'))
        write_json(self.common/'inputs/requirement-map.json',{'check':dict(kind='assembly_readiness',params=dict(catalog='catalog.json'))})
        write_json(self.common/'inputs/catalog.json',dict(parts={'C22548':dict(mpn='RC0603FR-071KL')}))
        write_json(self.common/'pins.json',dict(schema=completion.PINS,
            files={k:v for k,v in benchmark.tree_files(self.common).items() if k!='pins.json'}))
        self.pin=digest(self.common/'pins.json');self.public=self.root/'public.json'
        self.document=dict(schema='agent-ee.case-context/1',catalog_policy=dict(kind='evaluation_exclusion',
            label=self.producer.LABEL,exclude=[dict(lcsc='C22548',mpn='RC0603FR-071KL')]))
        self.cancel=native_run.Cancellation();self.deadline=time.monotonic()+30

    def admit(self):
        write_json(self.public,self.document)
        return completion.admit_public_context(self.common,self.pin,self.brief,self.public,digest(self.public),
            deadline=self.deadline,cancel=self.cancel)

    def test_actual_catalog_pair_identity_and_null_use_production_parser(self):
        self.assertEqual(self.admit(),self.document['catalog_policy'])
        for identity in [dict(lcsc='C22548',mpn='foreign'),dict(lcsc='C999',mpn='RC0603FR-071KL')]:
            self.document['catalog_policy']['exclude']=[identity]
            with self.subTest(identity=identity),self.assertRaisesRegex(ValueError,'identity absent'):self.admit()
        self.document['catalog_policy']=None
        self.assertIsNone(self.admit())

    def test_invalid_catalog_policy_refuses_before_any_attempt(self):
        from tests.test_generation_benchmark import FIXTURE, repin
        task=self.root/'task';shutil.copytree(FIXTURE,task)
        (task/'prompt.md').write_bytes(self.brief.read_bytes())
        document=read_json(task/'task.json');document['public_context']='public-case.json'
        write_json(task/'task.json',document)
        self.document['catalog_policy']['exclude']=[dict(lcsc='C999',mpn='foreign')]
        write_json(task/'public-case.json',self.document);repin(task)
        generator=self.root/'generator.py';generator.write_text('# no launch permitted')
        def forbidden(call):raise AssertionError('invalid policy reached an attempted generation')
        with native_run.using(native_run.Recorded(forbidden)), self.assertRaisesRegex(ValueError,'identity absent'):
            benchmark.run_task(task,generator,self.root/'trial',self.root/'store',attempts=2,timeout=30,
                case_timeout=100,grading_reserve=20,completion_context=self.common,expect_context=self.pin)
        ledger=read_json(self.root/'trial/trial.json')
        self.assertEqual((ledger['attempts_started'],ledger['work_started'],ledger['status']),(0,0,'error'))
        self.assertFalse((self.root/'trial/attempt-1').exists())

    def test_resistor_options_require_complete_triple_adc_and_no_explicit_roles(self):
        base=dict(schema=completion.CONTEXT,plan='plan.json',added_sources={},review_profile='profile.json')
        for options in [dict(resistor_context='pool.json'),
                dict(resistor_context='pool.json',resistor_context_sha256='a'*64,resistor_source='pool')]:
            with self.subTest(options=options),self.assertRaises(ValueError):
                completion.preparation_options(self.common,dict(base,prepare_options=options))
