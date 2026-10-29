"""Pure trusted-boundary controls; public authored refusal, no private cases/native calls."""
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agent_ee import acquisition_completion as completion, generation_benchmark as benchmark, native_run, qualification
from agent_ee.storage import digest, json_bytes, read_json, write_json
from tests import test_generation_benchmark as benchmark_tests
from tests import test_qualification as qualification_tests


class AcquisitionBoundaryTests(benchmark_tests.RefusalTests):
    def setUp(self):
        super().setUp()
        self.public = self.root / 'public-context'; self.public.mkdir()
        (self.public / 'inputs').mkdir()
        write_json(self.public / 'plan.json', {'source': 'inputs'})
        write_json(self.public / 'profile.json', {})
        write_json(self.public / 'context.json', dict(schema=completion.CONTEXT,
            plan='plan.json', added_sources={}, review_profile='profile.json'))
        self.context_pin = self.repin_context()
        self.generator = self.root / 'generator.py'; self.generator.write_text('# public request producer\n')
        self.adapter = native_run.Recorded(self.child)
        self.cancel = native_run.Cancellation()
        self.generation_files = None
        self.calls = []

    def tearDown(self):
        import os
        import shutil
        evidence=os.environ.get('AGENT_EE_COMPLETION_EVIDENCE')
        if evidence and self._testMethodName in AcquisitionBoundaryTests.__dict__:
            target=Path(evidence)/self._testMethodName
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(self.root,target,symlinks=True)

    def repin_context(self):
        write_json(self.public / 'pins.json', dict(schema=completion.PINS,
            files={k:v for k,v in benchmark.tree_files(self.public).items() if k!='pins.json'}))
        return digest(self.public / 'pins.json')

    def child(self, call):
        self.calls.append(call)
        if call.intent.tool == 'generation':
            self.assertEqual(set(benchmark.tree_files(call.inputs)), {'generator.py','prompt.md'})
            self.assertEqual(call.mounts, ())
            self.assertFalse(call.intent.network)
            self.assertEqual(call.intent.isolation, 'sandbox')
            request=dict(schema=completion.REQUEST,brief_sha256=digest(self.task/'prompt.md'),
                         action='prepare' if len([c for c in self.calls if c.intent.tool=='generation'])==1 else 'repair')
            return native_run.Outcome(files=self.generation_files if self.generation_files is not None
                else {'output/request.json':json_bytes(request)})
        if call.intent.tool != 'acquisition_completion':
            raise AssertionError('unexpected tool call: '+call.intent.tool)
        self.assertEqual(call.intent.isolation, 'host')
        self.assertEqual(call.intent.argv[1:4], ('-B','-m','agent_ee.acquisition_completion'))
        values={name:call.intent.argv[call.intent.argv.index('--'+name)+1] for name in
                ('context','expect-context','brief','request','production','attempt','deadline')}
        self.assertNotIn(str(self.task), call.intent.argv)
        self.assertFalse(Path(values['production']).is_relative_to(Path(values['request']).parent))
        try:
            completion.produce(values['context'],values['expect-context'],values['brief'],values['request'],
                values['production'],attempt=int(values['attempt']),deadline=float(values['deadline']))
        except ValueError as exc:
            return native_run.Outcome(exit_code=23,stderr=str(exc).encode())
        return native_run.Outcome()

    def run_acquisition(self, **kwargs):
        with native_run.using(self.adapter):
            return benchmark.run_task(self.task,self.generator,self.root/'trial',self.root/'store',
                attempts=kwargs.pop('attempts',1),timeout=100,case_timeout=100,grading_reserve=20,
                completion_context=self.public,expect_context=self.context_pin,cancel=self.cancel,**kwargs)

    def test_trusted_real_preparer_refusal_grades_freezes_and_preserves_private_isolation(self):
        path,=self.run_acquisition()
        report=read_json(path)
        self.assertTrue(report['result']['overall_pass'])
        self.assertEqual(report['kind'],'source_refusal')
        receipt=read_json(self.root/'trial/completion-1/receipt.json')
        self.assertEqual(read_json(Path(receipt['outputs'])/'refusal.json'),self.response)
        self.assertIsNone(receipt['package'])
        ledger=read_json(self.root/'trial/trial.json')
        self.assertEqual((ledger['work_started'],ledger['attempts_started'],ledger['status']),(3,1,'completed'))
        manifest=benchmark.freeze_series([path],{'public-refusal':digest(self.task/'pins.json')})
        self.assertIn('production-1/',manifest['evaluations'][0]['case_files'])
        self.assertIn('completion-1/receipt.json',manifest['evaluations'][0]['case_files'])
        self.assertTrue(benchmark.recorded_report(path)['result']['overall_pass'])
        (self.root/'trial/production-1/refusal/refusal.json').write_text('{}')
        with self.assertRaises(ValueError):benchmark.recorded_report(path)

    def test_generator_cannot_publish_a_refusal_or_native_deliverable(self):
        self.generation_files={'output/refusal.json':self.payload,'output/board.kicad_pcb':b'forged'}
        path,=self.run_acquisition()
        self.assertFalse(read_json(path)['result']['overall_pass'])
        self.assertEqual(read_json(path)['generation']['execution'],'failed')
        self.assertIn('only bounded request.json',(self.root/'trial/completion-1/stderr.log').read_text())
        self.assertTrue((self.root/'trial/attempt-1/output/refusal.json').exists())

    def test_unavailable_completion_is_graded_and_replayed_as_a_counted_failure(self):
        def unavailable(call):
            if call.intent.tool == 'acquisition_completion':
                return native_run.Outcome(execution='unavailable')
            return self.child(call)
        self.adapter.responder = unavailable
        path, = self.run_acquisition()
        self.assertFalse(read_json(path)['result']['overall_pass'])
        receipt = read_json(self.root/'trial/completion-1/receipt.json')
        self.assertEqual(receipt['execution'], 'failed')
        self.assertEqual(read_json(self.root/'trial/completion-1/execution.json')['execution'], 'unavailable')
        ledger = read_json(self.root/'trial/trial.json')
        self.assertEqual((ledger['work_started'], ledger['attempts_started'], ledger['status']), (3, 1, 'attempts_exhausted'))
        self.assertFalse(benchmark.recorded_report(path)['result']['overall_pass'])
        manifest = self.root/'frozen.json'
        write_json(manifest, benchmark.freeze_series([path], {'public-refusal': digest(self.task/'pins.json')}))
        rows = benchmark.aggregate([path], manifest=manifest, expect_pins=digest(manifest))['rows']
        self.assertTrue(rows)
        self.assertTrue(all(row['failed'] == 1 for row in rows))

    def test_context_repinning_or_private_task_alias_refuses_before_generation(self):
        (self.public/'profile.json').write_text('changed');self.repin_context()
        with self.assertRaisesRegex(ValueError,'external public context pins'):self.run_acquisition()
        self.assertFalse(self.calls)
        with self.assertRaisesRegex(ValueError,'separate from the private task'):
            benchmark.run_task(self.task,self.generator,self.root/'other',self.root/'store',attempts=1,
                completion_context=self.task,expect_context=digest(self.task/'pins.json'))

    def test_context_source_cannot_escape_even_with_new_local_pins(self):
        write_json(self.public/'plan.json', {'source':'../task/evaluator'})
        self.context_pin=self.repin_context()
        with self.assertRaisesRegex(ValueError,'escapes context'):self.run_acquisition()
        self.assertFalse(self.calls)

    def test_completion_cancellation_retains_actual_child_and_no_grading(self):
        prior=self.adapter.responder
        def cancel(call):
            if call.intent.tool=='acquisition_completion':
                self.cancel.cancel();return native_run.Outcome()
            return prior(call)
        self.adapter.responder=cancel
        with self.assertRaises(KeyboardInterrupt):self.run_acquisition()
        ledger=read_json(self.root/'trial/trial.json')
        self.assertEqual(ledger['work_started'],2)
        self.assertEqual(ledger['attempt_records'][0]['qualification_execution'],'interrupted')
        self.assertEqual(ledger['evaluations'],[])
        self.assertTrue((self.root/'trial/completion-1/execution.json').is_file())

    def test_completion_cutoff_does_not_reset_clock_or_spend_grading_reserve(self):
        clock=[1000.0];seen=[]
        def child(call):
            seen.append(call.timeout)
            if call.intent.tool=='generation':
                result=self.child(call);clock[0]+=25;return result
            clock[0]+=call.timeout
            return native_run.Outcome(execution='timeout',exit_code=None,stderr=b'owned completion timeout')
        def identity():
            clock[0]+=9;return {'public':'frozen CPU identity'}
        self.adapter.responder=child
        with patch('time.monotonic',lambda:clock[0]),patch.object(benchmark,'_refusal_identity',identity):
            paths=self.run_acquisition(attempts=2)
        self.assertEqual(seen,[80,55])
        ledger=read_json(self.root/'trial/trial.json')
        self.assertEqual(ledger['elapsed_s'],98)
        self.assertEqual(ledger['status'],'budget_exhausted')
        self.assertEqual(ledger['work_started'],3)
        self.assertFalse(read_json(paths[0])['result']['overall_pass'])
        self.assertFalse((self.root/'trial/attempt-2').exists())

    def test_missing_terminal_and_deleted_production_never_replay_as_success(self):
        path,=self.run_acquisition()
        (self.root/'trial/production-1/completion.json').unlink()
        with self.assertRaises((ValueError,OSError)):benchmark.recorded_report(path)

    def test_two_failed_completions_keep_all_credits_records_and_series_denominator(self):
        self.generation_files={'output/refusal.json':self.payload}
        paths=self.run_acquisition(attempts=2)
        ledger=read_json(self.root/'trial/trial.json')
        self.assertEqual((ledger['work_started'],ledger['attempts_started']),(6,2))
        self.assertTrue(all(read_json(p)['result']['overall_pass'] is False for p in paths))
        manifest=self.root/'frozen.json'
        write_json(manifest,benchmark.freeze_series([paths[-1]],{'public-refusal':digest(self.task/'pins.json')}))
        anchor=digest(manifest)
        self.assertTrue(all(row['failed']==1 for row in benchmark.aggregate([paths[-1]],manifest=manifest,expect_pins=anchor)['rows']))
        (self.root/'trial/completion-1/stderr.log').write_text('changed earlier failed child')
        with self.assertRaises(ValueError):benchmark.aggregate([paths[-1]],manifest=manifest,expect_pins=anchor)

    def test_actual_zero_exit_with_cleanup_error_cannot_supply_refusal_success(self):
        responder=self.child
        class CleanupError(native_run.Recorded):
            def run(self,intent,**kwargs):
                result=super().run(intent,**kwargs)
                if intent.tool=='acquisition_completion':
                    record=read_json(kwargs['work']/'execution.json')
                    record['cleanup_error']='owned descendant absence unknown'
                    write_json(kwargs['work']/'execution.json',record)
                return result
        self.adapter=CleanupError(responder)
        path,=self.run_acquisition()
        self.assertFalse(read_json(path)['result']['overall_pass'])
        self.assertEqual(read_json(self.root/'trial/completion-1/execution.json')['exit_code'],0)
        self.assertIn('cleanup incomplete',read_json(self.root/'trial/completion-1/receipt.json')['error'])

    def test_supported_preparation_is_intermediate_and_never_an_accepted_package(self):
        from agent_ee import acquisition_prepare
        output=self.root/'request';output.mkdir()
        write_json(output/'request.json',dict(schema=completion.REQUEST,
            brief_sha256=digest(self.task/'prompt.md'),action='prepare'))
        with patch.object(acquisition_prepare,'prepare',return_value={'status':'prepared'}):
            completion.produce(self.public,self.context_pin,self.task/'prompt.md',output,
                self.root/'prepared-only',attempt=1,deadline=time.monotonic()+30)
        self.assertEqual(read_json(self.root/'prepared-only/completion.json'), {'kind':'prepared'})
        with self.assertRaises(FileNotFoundError):
            completion.terminal(self.root/'prepared-only',time.monotonic()+30)

    def test_actual_zero_exit_after_productive_cutoff_is_retained_failure(self):
        clock=[1000.0]
        def late(call):
            result=self.child(call)
            if call.intent.tool=='acquisition_completion':clock[0]=1081.0
            return result
        self.adapter.responder=late
        with patch('time.monotonic',lambda:clock[0]):path,=self.run_acquisition()
        self.assertFalse(read_json(path)['result']['overall_pass'])
        receipt=read_json(self.root/'trial/completion-1/receipt.json')
        self.assertEqual(receipt['finished'],1081.0)
        self.assertIn('cutoff exhausted',receipt['error'])
        self.assertEqual(read_json(self.root/'trial/completion-1/execution.json')['exit_code'],0)

    def test_unsafe_writer_tree_is_not_copied_into_a_repair(self):
        count=[0]
        original=self.child
        def unsafe(call):
            if call.intent.tool=='generation':
                count[0]+=1
                if count[0]==1:
                    (call.work/'output').rmdir()
                    (call.work/'output').symlink_to(self.task/'evaluator',target_is_directory=True)
                    return native_run.Outcome()
                self.assertEqual(list((call.work/'previous').iterdir()),[])
            return original(call)
        self.adapter.responder=unsafe
        paths=self.run_acquisition(attempts=2)
        self.assertEqual(len(paths),2)
        self.assertTrue(all(read_json(p)['result']['overall_pass'] is False for p in paths))
        self.assertEqual(list((self.root/'trial/attempt-2/previous').iterdir()),[])

    def test_attempt_and_context_binding_cannot_be_swapped(self):
        path,=self.run_acquisition()
        receipt=self.root/'trial/evaluation-1/completion/receipt.json'
        data=read_json(receipt);data['brief_sha256']='b'*64;write_json(receipt,data)
        with self.assertRaises(ValueError):benchmark.recorded_report(path)


class ProductionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture=qualification_tests.QualificationTests('test_checkpoint_never_implies_checks_review_or_file_release')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_readonly_production_assessment_does_not_rewrite_existing_failure(self):
        root=self.fixture.root
        qualification.assess(root)
        original=(root/'qualification.json').read_bytes()
        result=qualification.assess(root,persist=False)
        self.assertFalse(result['accepted'])
        self.assertEqual((root/'qualification.json').read_bytes(),original)

    def test_missing_fixed_judgment_module_refuses_before_build_or_seat_launch(self):
        with patch('importlib.util.find_spec',return_value=None), self.assertRaises(completion.CompletionUnavailable):
            completion.complete_prepared(self.fixture.root,self.fixture.root/'build',self.fixture.root/'seats',
                author={},verifier={},private_evaluator_pair=('private','evaluator'),
                deadline=time.monotonic()+30,cancellation=native_run.Cancellation(),launch_environment={})
        self.assertFalse((self.fixture.root/'build').exists())

    def test_forged_complete_stage_and_accepted_flag_cannot_bypass_production_verifier(self):
        self.fixture.complete_control(typed=True)
        root=self.fixture.root
        plan=read_json(root/'build-plan.json');plan['review_profile']='profile.json'
        write_json(root/'source/profile.json',{});write_json(root/'build-plan.json',plan)
        from agent_ee import build_workflow
        from agent_ee.storage import identity
        self.fixture.report['inputs'].update(source=build_workflow._inventory(root/'source'),
            plan_sha256=digest(root/'build-plan.json'))
        self.fixture.report['identity']=identity(self.fixture.report['inputs'])
        self.fixture.report.update(accepted=True,status='accepted');self.fixture.save()
        # The real verifier must refuse; no authored success predicate is substituted.
        with self.assertRaises(ValueError):
            completion.package_binding(self.fixture.root,time.monotonic()+30,
                environment={'policy_files':benchmark.policy_files(),'runtime_files':{}})


class ParentCompletionTests(unittest.TestCase):
    """CPU_MOCK_ONLY orchestration; production verifier/seat tests are separate."""
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.public=self.root/'public';self.public.mkdir()
        self.work=self.root/'trial/generation-1';self.work.mkdir(parents=True)
        self.prod=self.root/'trial/production-1'
        self.cancel=native_run.Cancellation();self.deadline=time.monotonic()+20
        self.policy=self.root/'policy.json'
        self.data=dict(author=dict(harness='codex',model='owned-author',effort='high',timeout_s=10),
            verifier=dict(harness='claude',model='owned-verifier',effort='high',timeout_s=10),
            private_evaluator_pair=['agy','private'],launch_environment=dict(run_id='owned-parent',policy_files={},runtime_files={}))
        write_json(self.policy,self.data)
        self.binding=dict(root='CPU_MOCK_ONLY',candidate='candidate',verifier_environment={})
        self.child_mode='prepared';self.calls=[]

    def child(self, call):
        self.calls.append(call)
        self.assertIs(native_run._cancel.get(),self.cancel)
        self.assertLessEqual(call.timeout,self.deadline-time.monotonic()+.1)
        self.assertNotIn(str(self.policy),call.intent.argv)
        self.prod.mkdir()
        if self.child_mode!='missing':write_json(self.prod/'completion.json',dict(kind=self.child_mode))
        return native_run.Outcome()

    def finish(self, *args, **kw):
        self.assertEqual(args,(self.prod/'prepared',self.prod/'build',self.prod/'seats'))
        self.assertIs(kw['cancellation'],self.cancel)
        self.assertEqual(kw['deadline'],self.deadline)
        return self.binding

    def run_parent(self, finish=None):
        with native_run.using(native_run.Recorded(self.child)), patch.object(completion,'context',return_value=({},{})), \
                patch.object(completion,'complete_prepared',side_effect=finish or self.finish) as parent:
            raw=completion.complete(self.public,self.work,self.prod,self.public,'unused',attempt=1,
                deadline=self.deadline,cancel=self.cancel,policy=self.policy,expect_policy=digest(self.policy))
        return raw,parent

    def test_parent_consumes_prepared_on_same_clock_and_cancel_after_real_child_record(self):
        raw,called=self.run_parent()
        self.assertEqual(raw['execution'],'ok');called.assert_called_once()
        final,parent=completion.terminal(self.prod,self.deadline)
        self.assertEqual(final,dict(kind='package'));self.assertEqual(parent['package'],self.binding)
        self.assertEqual(read_json(self.root/'trial/completion-1/parent-completion.json'),parent)
        self.assertEqual(read_json(self.prod/'completion.json'),dict(kind='prepared'))
        parent['deadline']+=1;write_json(self.prod/'parent-completion.json',parent)
        with self.assertRaises(ValueError):completion.terminal(self.prod,self.deadline)

    def test_parent_failure_keeps_raw_child_exit_and_failure_never_supplies_package(self):
        def fail(*a,**kw):
            self.finish(*a,**kw)
            raise RuntimeError('CPU_MOCK_ONLY qualification failure')
        raw,_=self.run_parent(fail)
        self.assertEqual(raw['execution'],'ok')
        parent=read_json(self.prod/'parent-completion.json')
        self.assertEqual(parent['execution'],'failed');self.assertIn('qualification failure',parent['error'])
        self.assertIsNone(parent['package'])
        with self.assertRaises(ValueError):completion.terminal(self.prod,self.deadline)

    def test_missing_terminal_and_direct_child_package_do_not_start_seats(self):
        self.child_mode='missing'
        _,called=self.run_parent();called.assert_not_called()
        self.assertEqual(read_json(self.prod/'parent-completion.json')['execution'],'failed')
        write_json(self.prod/'completion.json',dict(kind='package'))
        with self.assertRaises(ValueError):completion.terminal(self.prod,self.deadline)

    def test_parent_cancellation_after_child_retains_separate_failure(self):
        def finish(*args,**kw):
            binding=self.finish(*args,**kw);self.cancel.cancelled.set();return binding
        _,parent=self.run_parent(finish)
        parent.assert_called_once()
        self.assertEqual(read_json(self.prod/'parent-completion.json')['execution'],'failed')

    def test_complete_prepared_adopts_only_verified_fresh_records_before_package(self):
        from agent_ee import judgment_seats
        from agent_ee.requirement_map import input_inventory
        prepared=self.root/'prepared';prepared.mkdir();(prepared/'source').mkdir()
        write_json(prepared/'build-plan.json',dict(source='source',judgments='judgments'))
        build=self.root/'build';out=self.root/'seats';events=[]
        def workflow(plan,run,**kw):
            self.assertIs(native_run._cancel.get(),self.cancel)
            self.assertEqual(native_run._deadline.get(),self.deadline)
            events.append(kw)
            if kw.get('resume'):
                self.assertTrue((prepared/'source/judgments/record.json').is_file())
                self.assertEqual(events[-2],'verified')
                return dict(status='accepted')
            return dict(completed=[dict(stage=k) for k in completion.retained_run.STAGES[:-1]])
        def produce(run,target,**kw):
            self.assertEqual(kw['deadline'],self.deadline);self.assertIs(kw['cancellation'],self.cancel)
            records=target/'judgments';records.mkdir(parents=True)
            write_json(records/'record.json',dict(classification='CPU_MOCK_ONLY'))
            write_json(target/'producer-receipt.json',dict(classification='CPU_MOCK_ONLY'))
            return dict(execution='ok',outcome='criteria_satisfied',judgment_directory=str(records),
                judgment_inventory=input_inventory(records),producer_receipt=str(target/'producer-receipt.json'),
                producer_receipt_sha256=digest(target/'producer-receipt.json'))
        with patch('agent_ee.build_workflow.run',side_effect=workflow), \
                patch.object(judgment_seats,'produce',side_effect=produce), \
                patch.object(judgment_seats,'verify',side_effect=lambda *a:events.append('verified')), \
                patch.object(completion,'package_binding',return_value=self.binding) as binding:
            actual=completion.complete_prepared(prepared,build,out,**self.data,deadline=self.deadline,cancellation=self.cancel)
        self.assertEqual(actual,self.binding)
        self.assertEqual(events,[dict(through='artwork'),'verified',dict(resume=True)])
        binding.assert_called_once_with(build,self.deadline,self.cancel)
        self.assertIsNone(native_run._cancel.get())
        self.assertIsNone(native_run._deadline.get())

    def test_existing_prepare_options_are_contained_pinned_and_defaults_unchanged(self):
        self.assertEqual(completion.preparation_options(self.root,{}),{})
        for name in ('roles','alternative'): (self.root/name).mkdir()
        for name in ('roles.json','alternative.json','intent.json','parts.json'):
            write_json(self.root/name,dict(classification='CPU_MOCK_ONLY'))
        options=dict(role_context='roles.json',role_context_sha256=digest(self.root/'roles.json'),role_source='roles',
            alternative_context='alternative.json',alternative_context_sha256=digest(self.root/'alternative.json'),alternative_source='alternative',
            final_groups=True,addr_tie='GND',verification_timeout_s=12)
        self.assertEqual(completion.preparation_options(self.root,dict(prepare_options=options))['role_source'],self.root/'roles')
        for change in ({'role_context':'../escape'}, {'role_context_sha256':'0'*64}, {'unknown':True}, {'final_groups':1}):
            with self.assertRaises(ValueError):completion.preparation_options(self.root,dict(prepare_options=options|change))
        (self.root/'link').symlink_to('/etc')
        with self.assertRaises(ValueError):completion.preparation_options(self.root,dict(prepare_options=options|{'role_source':'link'}))
