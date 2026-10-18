"""Real scaffold/receipt/check execution; synthetic inputs never qualify pod hardware."""
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ScaffoldFixture
from toolchain import require_toolchain
from agent_ee import pod_driver as pod, kicad_route_attempt as attempt
from tests.kicad_route_fixture import make_checkout, FAKE_PIN
from agent_ee.storage import read_json, write_json, digest
from agent_ee.stage_receipts import validate_pre_receipt

ROOT = Path(__file__).resolve().parents[1]


class PodDriverTests(ScaffoldFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Existing synthetic parts exercise the config boundary only, not a stand-in production pod.
        self.scope['schema'] = 'agent-ee.scope/3'
        self.scope['name'] = 'Synthetic pod-driver contract test (not pod design)'
        self.scope['limitations'] += ['POD-AC-8: BENCH REQUIRED', 'POD-AC-9: BENCH REQUIRED',
                                     'All other pod hardware requirements remain unqualified in this test.']
        for name in ('spec.md', 'spec-record.json'):
            shutil.copyfile(ROOT / 'fixtures/telemetry-pod' / name, self.source / name)
        write_json(self.source / 'intent.json', {'test': 'writer-owned, not a production connectivity intent'})
        write_json(self.source / 'deviations.json', {'test': 'writer-owned, not approved deviations'})
        write_json(self.source / 'bindings.json', {'test': 'coordinator-owned binding result'})
        self.scope['checks'] = [dict(id='erc',kind='erc',stage='schematic'),
            dict(id='netlist',kind='netlist',stage='schematic'),
            dict(id='placement',kind='drc',stage='placement'),
            dict(id='routing',kind='geometry',stage='routing',params=dict(criterion='copper-width/1', covered_primitives=['PCB_ARC','PCB_TRACK'], min_track_width_mm=.2,exclude_nets=[])),
            dict(id='fab',kind='fab_package',stage='verification',params={'netlist_check':'netlist'})]
        self.scope['generation'] = dict(schema='agent-ee.generation/1',done_check='fab')
        self.scope['baseline']['requirements'] = [dict(id='native',intent='acceptance',
            statement='Synthetic fixture native checks and package, not full pod coverage.',
            source=dict(path='requirements.md',locator='line 1'),checks=[c['id'] for c in self.scope['checks']])]
        self.scope['baseline']['targets'] += ['spec.md','spec-record.json']
        self.scope['stages'] = dict(schema='agent-ee.stages/1', order=list(pod.ORDER),
            subjects={'schematic':dict(path=self.scope['schematic'],kind='symbols',references=['R1','C1','J1']),
                      'placement':dict(path=self.scope['board'],kind='footprints',references=['R1','C1','J1']),
                      'routing':dict(path=self.scope['board'],kind='tracks',minimum=1)},finding_retry_limit=3)
        steps=[]
        for name,stage in pod.STEPS:
            output = ('bindings.json' if name == 'binding-fill' else self.scope['schematic']
                      if name == 'emit-schematic' else self.scope['board'])
            step=dict(schema='agent-ee.stage-step/1',step=name,stage=stage,
                argv=['/usr/bin/false'],writes={output:'design/'+output},adoptable_exits=[0],timeout_s=600)
            steps.append(step)
        checkout, code = make_checkout(self.root/'router','complete')
        self.enterContext(mock.patch.object(attempt,'PIN',FAKE_PIN))
        self.enterContext(mock.patch.object(attempt,'PINNED_CODE',code))
        route=dict(schema='agent-ee.routing-adapter/1',project='project',board='design.kicad_pcb',
                   schematic='design.kicad_sch',name='pod',label='campaign',checkout=str(checkout),budget=300,timeout=900)
        profile=read_json(ROOT/'profiles/claude-baseline.json')
        self.plan=dict(schema=pod.SCHEMA,scope='planning.scope.json',envelope='envelope.json',parts='parts.json',
            spec='spec.md',spec_record='spec-record.json',writer_inputs={'intent':'intent.json','deviations':'deviations.json'},
            implementation_steps=steps,review_profile=profile,implementation=profile['adjudicator'],
            routing_budget_s=300,review_budget_s=3600,routing_adapter=route)
        self.plan_path=self.root/'pod-plan.json'
        self.save()

    def save(self):
        self.scope['inputs']=[dict(path=str(p),at=p.relative_to(self.source).as_posix())
                              for p in sorted(self.source.rglob('*')) if p.is_file()]
        write_json(self.scope_path,self.scope)
        write_json(self.plan_path,self.plan)

    def build(self, name='run', **kw):
        require_toolchain()
        return pod.build(self.plan_path,self.root/name,**kw)

    def test_config_validates(self):
        before={str(p):digest(p) for p in self.source.rglob('*') if p.is_file()}
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        config=read_json(self.root/'run/campaign.json')
        self.assertEqual(config['writer_editable'],['deviations.json','intent.json'])
        self.assertEqual(config['max_iterations'],10)
        self.assertEqual(config['max_schema_resubmissions'],1)
        self.assertEqual(config['max_seconds'],6000)
        self.assertEqual(receipt['validation']['routing_step']['adoptable_exits'],[0,2])
        self.assertEqual(receipt['validation']['routing_step']['step'],'kicad-route-krt')
        self.assertFalse(receipt['done'])
        self.assertTrue(receipt['first_capture_binding']['bound'])
        self.assertEqual(before,{str(p):digest(p) for p in self.source.rglob('*') if p.is_file()})
        link=config['pre_receipts'][0]
        retained=validate_pre_receipt(self.root/'run',link)
        self.assertEqual(retained['spec']['stage'],'scaffold')
        self.assertEqual(receipt['validation']['writer_inputs'],['deviations.json','intent.json'])
        (self.root/'run/scaffold-receipt/receipt.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):
            validate_pre_receipt(self.root/'run',link)

    def test_retry_limit_below_two_refused(self):
        self.scope['stages']['finding_retry_limit']=1
        self.save()
        with self.assertRaisesRegex(ValueError,'finding_retry_limit'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_fab_in_order_refused_until_146(self):
        self.scope['stages']['order'].append('fab')
        self.scope['checks'][-1]['stage']='fab'
        self.scope['checks'].insert(-1,dict(id='verify',kind='drc',stage='verification'))
        self.scope['baseline']['requirements'][0]['checks'].insert(-1,'verify')
        self.plan['implementation_steps'].append(dict(schema='agent-ee.stage-step/1',stage='fab',step='export-fab',
            argv=['/usr/bin/false'],writes={self.scope['board']:'design/'+self.scope['board']},adoptable_exits=[0]))
        self.save()
        with self.assertRaisesRegex(ValueError,'AGENTEE-146'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_partial_predecessor_and_extra_write_authority_refused(self):
        self.plan['implementation_steps'][-1]['adoptable_exits']=[0,2]
        self.save()
        with self.assertRaisesRegex(ValueError,'exit 0 only'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())
        self.plan['implementation_steps'][-1]['adoptable_exits']=[0]
        self.plan['implementation_steps'][0]['writes']['intent.json']='design/intent.json'
        self.save()
        with self.assertRaisesRegex(ValueError,'outside writer inputs'):
            pod.build(self.plan_path,self.root/'refused')

    def test_small_iteration_budget_and_provider_fallback_refused(self):
        for changed in ({'max_iterations':7},{'max_seconds':100},{'max_iterations':True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                pod.validate_plan({**self.plan,**changed})
        self.plan['implementation']={**self.plan['implementation'],'harness':'openrouter'}
        with self.assertRaisesRegex(ValueError,'harness'):
            pod.validate_plan(self.plan)

    def test_budget_covers_two_full_receipt_envelopes_and_reviews(self):
        self.assertEqual(pod.validate_plan({**self.plan,'max_seconds':6000}),(10,6000))
        with self.assertRaisesRegex(ValueError,'max_seconds'):
            pod.validate_plan({**self.plan,'max_seconds':5999})

    def test_dry_run_reports_nonabsence_defect_and_tool_failure(self):
        # Substitute only the checks transport to exercise reporting of engine outcomes.
        out=self.root/'dry-report'
        out.mkdir()
        write_json(out/'scope.json',self.scope)
        for execution,outcome,expected in [('ok','defect','evaluated'),('timeout','unknown','unknown')]:
            checks=[dict(id=c['id'],execution=execution if c['stage']=='schematic' else 'not_run',
                         outcome=outcome if c['stage']=='schematic' else 'unknown',
                         details={} if c['stage']=='schematic' else {'reason':'stage_blocked'})
                    for c in self.scope['checks']]
            write_json(out/'dry-run/result.json',{'checks':checks})
            with mock.patch('agent_ee.cli.run_review',return_value=1):
                record=pod.run_dry(out)
            self.assertEqual(record['earliest_failing_stage'],'schematic')
            self.assertEqual(record['stages'][0]['status'],expected)
            self.assertEqual([s['status'] for s in record['stages'][1:]],['stage_blocked']*3)
            self.assertFalse(record['done'])

    def test_malformed_plan_refused_before_output(self):
        for bad in ({}, [], {**self.plan, 'scope': 4}, {**self.plan, 'writer_inputs': []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pod.validate_plan(bad)
        self.plan_path.write_text('{"schema":"one","schema":"two"}')
        with self.assertRaises(ValueError):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_malformed_step_declarations_refused_before_scaffold_or_output(self):
        for steps in (None, {}, [None], [{'schema':'agent-ee.stage-step/1'}],
                      [{**self.plan['implementation_steps'][0],'writes':[]}],
                      [{**self.plan['implementation_steps'][0],'writes':{'../escape':'out'}}]):
            with self.subTest(steps=steps):
                write_json(self.plan_path,{**self.plan,'implementation_steps':steps})
                with mock.patch.object(pod.scaffold,'scaffold',side_effect=AssertionError('preflight must refuse first')):
                    with self.assertRaises(ValueError):
                        pod.build(self.plan_path,self.root/'refused')
                self.assertFalse((self.root/'refused').exists())

    def test_failed_scaffold_retains_failure_without_campaign(self):
        require_toolchain()
        make_step=pod._scaffold_step
        def failed(*args):
            step=make_step(*args)
            step['argv']=['/usr/bin/false']
            return step
        with mock.patch.object(pod,'_scaffold_step',side_effect=failed):
            with self.assertRaises(ValueError):
                self.build('failed')
        root=self.root/'failed'
        self.assertFalse((root/'campaign.json').exists())
        record=read_json(root/'driver-receipt.json')
        self.assertEqual(record['status'],'preparing')
        self.assertFalse(record['done'])
        self.assertNotIn('campaign_sha256',record)
        receipt=read_json(root/'scaffold-receipt/receipt.json')
        self.assertNotEqual(receipt['status'],'adoptable')
        self.assertEqual(receipt['exit_code'],1)

    def test_removed_bench_obligation_refused(self):
        self.scope['limitations']=[s for s in self.scope['limitations'] if 'POD-AC-8' not in s]
        self.save()
        with self.assertRaisesRegex(ValueError,'POD-AC-8'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_run_cli_propagates_campaign_incomplete(self):
        with mock.patch.object(pod,'build'), mock.patch('agent_ee.campaign.run_campaign',return_value=1):
            self.assertEqual(pod.main([str(self.plan_path),'--out',str(self.root/'run'),'--run']),1)

    def test_checks_only_dry_run_reaches_schematic_without_later_steps(self):
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            self.build(dry_run=True)
        record=read_json(self.root/'run/dry-run-record.json')
        self.assertFalse(record['done'])
        self.assertFalse(record['inference'])
        self.assertEqual(record['earliest_failing_stage'],'schematic')
        self.assertEqual([s['status'] for s in record['stages']],
                         ['stage_not_reached','stage_not_reached','stage_not_reached','stage_blocked'])
        self.assertTrue(any(c.get('details',{}).get('reason')=='stage_not_reached'
                            for c in record['stages'][0]['checks']))
        self.assertFalse((self.root/'run/campaign').exists())


if __name__=='__main__':
    unittest.main()
