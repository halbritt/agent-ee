"""Real scaffold/receipt/check execution; synthetic inputs never qualify pod hardware."""
import copy
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ScaffoldFixture
from toolchain import require_toolchain
from agent_ee import pod_driver as pod, kicad_route_attempt as attempt
from tests.kicad_route_fixture import make_checkout, FAKE_PIN
from agent_ee.storage import read_json, write_json, digest, identity
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
        profile=read_json(ROOT/'profiles/pod-review.json')
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
        # Plans without envelope content keep exactly the one scaffold pre_receipt.
        self.assertEqual([l['binding'] for l in config['pre_receipts']],[{'pre_receipt':1}])
        self.assertNotIn('envelope_content',receipt)
        self.assertFalse((self.root/'run/content-receipt').exists())
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

    def use_placement_adapter(self, policy='policy.json'):
        write_json(self.source/'policy.json',{'schema':'agent-ee.packing-policy/1','fixed_refs':[],
            'step_budget':10000,'gap_nm':500000,'priority_refs':[],'ground_nets':[],'obstacles':[]})
        self.plan['implementation_steps']=[s for s in self.plan['implementation_steps'] if s['step']!='place-board']
        self.plan['placement_adapter']=dict(schema='agent-ee.placement-adapter/1',helper='agent-ee.placer/1',
            policy=policy,fixed_footprints=[],timeout_s=900)
        self.save()

    def test_owned_placement_adapter_replaces_generic_placement_step(self):
        self.use_placement_adapter()
        policy=read_json(self.source/'policy.json')
        policy.update(fixed_refs=['J1'],priority_refs=['R1'],min_courtyard_edge_mm=0.25)
        write_json(self.source/'policy.json',policy)
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        config=read_json(self.root/'run/campaign.json')
        self.assertEqual(config['placement_adapter'],self.plan['placement_adapter'])
        self.assertNotIn('placement',[s['stage'] for s in config['implementation_steps']])
        self.assertEqual(receipt['validation']['placement_adapter']['board'],self.scope['board'])
        # The engine's own start validation accepts the prepared configuration; no iteration is launched.
        from agent_ee import campaign
        with mock.patch.object(campaign,'continue_campaign',return_value=0) as launched:
            self.assertEqual(campaign.run_campaign(self.root/'run/campaign.json',self.root/'campaign'),0)
        launched.assert_called_once()
        self.assertEqual(read_json(self.root/'campaign/config.json')['placement_adapter'],config['placement_adapter'])

    def test_placement_adapter_with_generic_step_or_uncaptured_policy_refused(self):
        steps=list(self.plan['implementation_steps'])
        self.use_placement_adapter()
        self.plan['implementation_steps']=steps
        self.save()
        with self.assertRaisesRegex(ValueError,'placement uses the declared placement_adapter'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())
        self.use_placement_adapter(policy='absent-policy.json')
        with self.assertRaisesRegex(ValueError,'placement policy must be a captured immutable input'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())
        self.plan['placement_adapter']['helper']='invented-placer'
        with self.assertRaisesRegex(ValueError,'placement helper'):
            pod.validate_plan(self.plan)

    def test_null_placement_helper_refused_before_output(self):
        self.use_placement_adapter()
        for helper, policy in ((None, None), (None, 'policy.json'), ('agent-ee.placer/1', None)):
            with self.subTest(helper=helper, policy=policy):
                self.plan['placement_adapter'].update(helper=helper, policy=policy)
                self.save()
                with self.assertRaisesRegex(ValueError, 'requires.*(helper|packing policy)'):
                    pod.validate_plan(self.plan)
                with self.assertRaisesRegex(ValueError, 'requires.*(helper|packing policy)'):
                    pod.build(self.plan_path,self.root/'refused')
                self.assertFalse((self.root/'refused').exists())

    def test_invalid_packing_policy_refused_before_output(self):
        self.use_placement_adapter()
        valid=read_json(self.source/'policy.json')
        cases=[([], 'expected an agent-ee.packing-policy/1 object'),
               ({**valid,'schema':'wrong'},'unsupported packing policy'),
               ({**valid,'step_budget':True},'step budget must be 1..1000000'),
               ({**valid,'gap_nm':'wide'},'gap must be nonnegative integer nanometres'),
               ({**valid,'min_courtyard_edge_mm':-1},'min_courtyard_edge_mm must be nonnegative'),
               ({**valid,'fixed_refs':['R1','R1']},'expected unique explicit fixed_refs'),
               ({**valid,'priority_refs':['absent']},'unknown declared reference'),
               ({**valid,'obstacles':[{'side':'sideways'}]},'obstacle side required'),
               ({**valid,'groups':[]},'placement groups must be a nonempty list'),
               ({**valid,'fixed_overhangs':[]},'fixed_overhangs come only from a captured envelope, never from the packing policy')]
        for policy,reason in cases:
            with self.subTest(policy=policy):
                write_json(self.source/'policy.json',policy)
                with self.assertRaises(ValueError) as caught:
                    pod.build(self.plan_path,self.root/'refused')
                self.assertEqual(str(caught.exception),'invalid pod placement policy policy.json: '+reason)
                self.assertFalse((self.root/'refused').exists())
        (self.source/'policy.json').write_text('{broken')
        with self.assertRaisesRegex(ValueError,'invalid pod placement policy policy.json:'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def use_envelope_content(self, fixed=False, **content):
        # Original synthetic mechanics: board-only holes and a rule area, no part placement.
        envelope=copy.deepcopy(self.envelope)
        if not fixed:
            envelope['fixed_footprints']=[]
        envelope['mounting_holes']=[dict(id=f'H{i}',at=at,drill_mm=3.2,plated=False,tolerance_mm=0)
                                    for i,at in enumerate(([100,100],[112,100]),1)]
        envelope['keepouts']=[dict(id='content-clearance',polygon=[[104,104],[106,104],[106,106],[104,106]],
                                   layers=['F.Cu','B.Cu'],forbid=['tracks','vias'])]
        write_json(self.source/'envelope.json',envelope)
        self.plan['envelope_content']=content
        self.save()

    def start_campaign(self, run='run', name='campaign'):
        from agent_ee import campaign
        with mock.patch.object(campaign,'continue_campaign',return_value=0) as launched:
            self.assertEqual(campaign.run_campaign(self.root/run/'campaign.json',self.root/name),0)
        launched.assert_called_once()
        return self.root/name

    def verify_first_capture(self, root):
        # Real run-1 capture of the prepared candidate, then the campaign's own pre_receipt verifier.
        from agent_ee.candidate import capture
        from agent_ee.stage_receipts import verify_pre_receipts
        (root/'runs/1').mkdir(parents=True)
        capture(self.root/'run/scope.json',root/'runs/1')
        state={**read_json(root/'state.json'),'history':[{'run':str(root/'runs/1')}]}
        verify_pre_receipts(root,state,read_json(root/'config.json')['pre_receipts'])

    def test_envelope_content_joins_source_bound_pre_receipt_chain(self):
        from agent_ee.stage_receipts import after_tree, validate_pre_receipt_chain
        from agent_ee.symbol_library import children, parse_sexpr
        self.use_envelope_content(intent='intent.json')
        before={str(p):digest(p) for p in self.source.rglob('*') if p.is_file()}
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            record=self.build()
        run=self.root/'run'
        config=read_json(run/'campaign.json')
        self.assertEqual([l['path'] for l in config['pre_receipts']],
                         ['scaffold-receipt/receipt.json','content-receipt/receipt.json'])
        self.assertEqual([l['binding'] for l in config['pre_receipts']],[{'pre_receipt':1},{'pre_receipt':2}])
        self.assertEqual(record['envelope_content'],config['pre_receipts'][1])
        self.assertTrue(record['first_capture_binding']['bound'])
        scaffolded,content=validate_pre_receipt_chain(run,config['pre_receipts'])
        self.assertEqual(content['spec']['step'],'envelope-content')
        self.assertEqual(content['inputs'],after_tree(scaffolded))
        board=self.scope['board']
        self.assertEqual(content['changed'],[board])
        argv=content['spec']['argv']
        for flag,value in (('--envelope','envelope.json'),('--parts','parts.json'),('--board',board),
                           ('--intent','intent.json')):
            self.assertEqual(argv[argv.index(flag)+1],value)
        for rel in ('envelope.json','parts.json','intent.json'):
            self.assertEqual(content['inputs'][rel],digest(self.source/rel))
        self.assertEqual(digest(run/'inputs'/board),content['outputs'][board])
        holes=[fp for fp in children(parse_sexpr((run/'inputs'/board).read_bytes()),'footprint')
               if children(fp,'attr') and 'board_only' in children(fp,'attr')[0]]
        self.assertEqual(len(holes),2)
        self.assertEqual(before,{str(p):digest(p) for p in self.source.rglob('*') if p.is_file()})
        root=self.start_campaign()
        self.assertEqual([l['path'] for l in read_json(root/'config.json')['pre_receipts']],
                         ['pre-receipts/1/receipt.json','pre-receipts/2/receipt.json'])
        self.verify_first_capture(root)

    def _setup_valid_emitter_inputs(self):
        """Prepare valid connectivity-intent/1 and schematic-layout/1 matching self.parts (R1, C1, J1)."""
        assignments = read_json(self.source / 'assignments.json')
        library = self.source / 'Owned.kicad_sym'
        raw_lib = library.read_bytes()
        if b'(property "Footprint"' not in raw_lib:
            library.write_bytes(raw_lib.replace(
                b'(property "Value"',
                b'(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)))) (property "Value"'))
        assignments['libraries'][0]['sha256'] = digest(library)
        write_json(self.source / 'assignments.json', assignments)
        cad = read_json(self.source / 'cad.json')
        for name in ('manifest', 'assignments'):
            blob = self.parts['cad'][name]
            blob['sha256'] = digest(self.source / blob['path'])
        members = [m for lib in cad['libraries'] for m in lib['members']]
        self.parts['cad']['members_sha256'] = identity(sorted(
            [*members, dict(path='Owned.kicad_sym', sha256=digest(library))],
            key=lambda m: m['path']))
        write_json(self.source / 'parts.json', self.parts)
        intent = dict(
            schema='agent-ee.connectivity-intent/1',
            libraries=assignments['libraries'],
            components=[
                dict(id=p['id'], reference=p['instances'][0]['reference'], lib_id=p['lib_id'],
                     footprint=p['footprint'], value=p['mpn'], units=[1])
                for p in self.parts['parts']
            ],
            nets=[],
            no_connects=[
                dict(component=p['id'], pin=dict(number=n))
                for p in self.parts['parts']
                for n in ('123' if p['id'] == 'header' else '12')
            ],
        )
        write_json(self.source / 'intent.json', intent)
        layout = dict(
            schema='agent-ee.schematic-layout/1',
            namespace=str(uuid.uuid5(uuid.NAMESPACE_URL, 'probe-ee224-fixed')),
            project='design',
            transforms={},
        )
        write_json(self.source / 'layout.json', layout)
        return intent, layout

    def test_fixed_footprints_and_imu_in_content_custody_chain(self):
        """Probe 1: pod_driver.build with non-empty fixed_footprints (J1), intent, layout, and imu_reference."""
        from agent_ee import board_envelope, candidate, stage_receipts
        from agent_ee.symbol_library import children, parse_sexpr
        self._setup_valid_emitter_inputs()
        # Keep fixed_footprints=[J1] from ScaffoldFixture and add holes + keepout + imu_reference='J1'
        self.use_envelope_content(
            fixed=True,
            intent='intent.json',
            layout='layout.json',
            imu_reference='J1',
        )
        with mock.patch('agent_ee.campaign.run_campaign', side_effect=AssertionError('inference forbidden')):
            record = self.build('run-fixed')
        run = self.root / 'run-fixed'
        config = read_json(run / 'campaign.json')
        self.assertEqual(len(config['pre_receipts']), 2)
        scaffolded, content = stage_receipts.validate_pre_receipt_chain(run, config['pre_receipts'])
        self.assertEqual(content['status'], 'completed')
        self.assertEqual(content['spec']['step'], 'envelope-content')
        self.assertIn('--layout', content['spec']['argv'])
        self.assertIn('--imu-reference', content['spec']['argv'])
        board_path = run / 'inputs' / self.scope['board']
        # Verify J1 fixed footprint round-trip against envelope.json
        rt = board_envelope.round_trip((self.source / 'envelope.json').read_bytes(), board_path.read_bytes())
        compared = [f for f in rt['fields'] if f['status'] not in ('not_in_board', 'not_compared')]
        self.assertTrue(all(f['status'] == 'match' for f in compared), rt)
        fixed_fields = [f for f in rt['fields'] if f['field'].startswith('/fixed_footprints/')]
        self.assertEqual(len(fixed_fields), 1)
        self.assertEqual(fixed_fields[0]['status'], 'match')
        # Verify IMU_AXES silkscreen text was written by content_step
        sexpr = parse_sexpr(board_path.read_bytes())
        texts = [t for t in children(sexpr, 'gr_text') if 'IMU_AXES' in str(t)]
        self.assertEqual(len(texts), 1)
        # Verify campaign start and run-1 capture binding
        camp = self.start_campaign(run='run-fixed', name='campaign-fixed')
        (camp / 'runs/1').mkdir(parents=True)
        candidate.capture(run / 'scope.json', camp / 'runs/1')
        state = {**read_json(camp / 'state.json'), 'history': [{'run': str(camp / 'runs/1')}]}
        stage_receipts.verify_pre_receipts(camp, state, read_json(camp / 'config.json')['pre_receipts'])

    def test_envelope_content_custody_refusals(self):
        from agent_ee import campaign
        self.use_envelope_content()
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            self.build()
        path=self.root/'run/campaign.json'
        config=read_json(path)
        links=config['pre_receipts']
        for label,changed,message in (
                ('out of order',links[::-1],'must form one ordered chain'),
                ('hash mismatch',[links[0],{**links[1],'sha256':'0'*64}],'changed, unsafe or missing'),
                ('rebound',[links[0],{**links[1],'binding':{'pre_receipt':1}}],'foreign stage receipt binding')):
            with self.subTest(label):
                write_json(path,{**config,'pre_receipts':changed})
                with self.assertRaisesRegex(ValueError,message):
                    campaign.run_campaign(path,self.root/'campaign')
                self.assertFalse((self.root/'campaign').exists())
        # Dropping the content receipt leaves a chain that no longer reaches the candidate.
        write_json(path,{**config,'pre_receipts':links[:1]})
        root=self.start_campaign(name='scaffold-only')
        with self.assertRaisesRegex(ValueError,'differs from first capture: '+self.scope['board']):
            self.verify_first_capture(root)
        # An unreceipted board edit after preparation is not bound by the chain.
        write_json(path,config)
        root=self.start_campaign(name='edited')
        board=self.root/'run/inputs'/self.scope['board']
        board.write_bytes(board.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'differs from first capture: '+self.scope['board']):
            self.verify_first_capture(root)

    def test_envelope_content_inputs_refused_before_output(self):
        for content,message in (({'intent':'absent.json'},'uncaptured pod envelope_content intent'),
                                ({'layout':self.scope['board']},'uncaptured pod envelope_content layout'),
                                ({'intent':self.scope['schematic']},'uncaptured|collides')):
            with self.subTest(content=content):
                self.use_envelope_content(**content)
                with self.assertRaisesRegex(ValueError,message):
                    pod.build(self.plan_path,self.root/'refused')
                self.assertFalse((self.root/'refused').exists())
        for content in ({'envelope':'envelope.json'},{'net_constraints':'intent.json'},[],
                        {'intent':'../intent.json'},{'imu_reference':''}):
            with self.subTest(content=content),self.assertRaises(ValueError):
                pod.validate_plan({**self.plan,'envelope_content':content})
        # The envelope declares fixed J1, which needs the emitter's intent and layout.
        self.use_envelope_content(fixed=True,intent='intent.json')
        with self.assertRaisesRegex(ValueError,'require declared envelope_content intent and layout'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_antenna_without_facts_refused_before_output(self):
        self.use_envelope_content()
        envelope=read_json(self.source/'envelope.json')
        envelope['keepouts']=[dict(id='antenna',antenna=dict(reference='U2',facts_check='facts'),
            layers=['F.Cu','B.Cu'],forbid=['tracks','vias','pads','copper_pours','footprints'])]
        write_json(self.source/'envelope.json',envelope)
        self.save()
        with self.assertRaisesRegex(ValueError,'antenna keepout requires declared envelope_content antenna_facts'):
            pod.build(self.plan_path,self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_failed_envelope_content_retains_receipt_without_campaign(self):
        # The real producer refuses an IMU reference that selects no fixed footprint.
        self.use_envelope_content(imu_reference='U99')
        with self.assertRaisesRegex(ValueError,'envelope-content failed'):
            self.build('failed')
        root=self.root/'failed'
        self.assertFalse((root/'campaign.json').exists())
        self.assertEqual(read_json(root/'driver-receipt.json')['status'],'preparing')
        receipt=read_json(root/'content-receipt/receipt.json')
        self.assertEqual(receipt['status'],'failed')
        self.assertIn('declared fixed footprint',(root/'content-receipt/run/console.log').read_text())
        board=self.scope['board']
        self.assertEqual(digest(root/'inputs'/board),receipt['inputs'][board])

    def use_net_constraints(self):
        # Qualified owned template; source constraints use the actual pod native name.
        shutil.copyfile(ROOT/'fixtures/netclass-completeness/native.kicad_pro',
                        self.source/'project/design.kicad_pro')
        write_json(self.source/'constraints.json',dict(schema='agent-ee.net-constraints/1',classes=[
            dict(name='RF_50',requirement='native',nets=['/POD_GNSS_RF'],track_width_mm=.335,
                 clearance_mm=.2032,impedance_ohm=50)]))
        self.plan['net_constraints']='constraints.json'
        self.save()

    def test_net_constraints_receipt_final_project_and_campaign_binding(self):
        self.use_net_constraints()
        before={str(p):digest(p) for p in self.source.rglob('*') if p.is_file()}
        record=self.build()
        run=self.root/'run'
        config=read_json(run/'campaign.json')
        self.assertEqual([l['path'] for l in config['pre_receipts']],
                         ['scaffold-receipt/receipt.json','constraints-receipt/receipt.json'])
        self.assertEqual(record['net_constraints'],config['pre_receipts'][1])
        receipt=read_json(run/'constraints-receipt/receipt.json')
        self.assertEqual(receipt['status'],'completed')
        self.assertEqual(receipt['execution']['execution'],'ok')
        self.assertEqual(receipt['violations'],[])
        self.assertEqual(receipt['inputs']['constraints.json'],digest(self.source/'constraints.json'))
        project='project/design.kicad_pro'
        self.assertNotEqual(receipt['inputs'][project],receipt['outputs'][project])
        emitted=read_json(run/'inputs/project/design.net-constraints.record.json')
        self.assertEqual(emitted['project_sha256'],digest(run/'inputs'/project))
        self.assertEqual(emitted['template_project_sha256'],receipt['inputs'][project])
        self.assertEqual(emitted['rules_sha256'],digest(run/'inputs/project/design.kicad_dru'))
        self.assertEqual(emitted['board_binding']['status'],'unbound')
        self.assertEqual(emitted['classes'][0]['impedance']['status'],'recorded_not_enforced')
        prepared=read_json(run/'scope.json')
        native=next(c for c in prepared['checks'] if c['kind']=='net_class_assignment')
        self.assertEqual(native['stage'],'placement')
        self.assertIn(native['id'],prepared['baseline']['requirements'][0]['checks'])
        self.assertEqual(before,{str(p):digest(p) for p in self.source.rglob('*') if p.is_file()})
        root=self.start_campaign()
        self.verify_first_capture(root)
        # No declared net exists in the empty scaffold yet: native absence remains a defect.
        from agent_ee.checks import Ctx
        from agent_ee.net_class_assignment import check_net_class_assignment
        from agent_ee.candidate import capture
        import time
        captured=self.root/'assignment'
        captured.mkdir()
        manifest=capture(run/'scope.json',captured)
        ctx=Ctx(captured,manifest,native,time.monotonic()+120)
        check_net_class_assignment(ctx)
        self.assertEqual(ctx.result['outcome'],'defect')
        self.assertIn('net_absent',[d['reason'] for d in ctx.result['details']['defects']])

    def test_constraints_and_content_form_three_receipt_chain(self):
        self.use_net_constraints()
        self.use_envelope_content()
        record=self.build()
        links=read_json(self.root/'run/campaign.json')['pre_receipts']
        self.assertEqual([l['binding'] for l in links],[{'pre_receipt':i} for i in (1,2,3)])
        self.assertEqual(record['envelope_content'],links[2])
        self.verify_first_capture(self.start_campaign())

    def test_failed_constraint_application_retains_unadoptable_receipt(self):
        self.use_net_constraints()
        make_step=pod._constraint_step
        def failed(*args):
            return {**make_step(*args),'argv':['/usr/bin/false']}
        with mock.patch.object(pod,'_constraint_step',side_effect=failed):
            with self.assertRaises(ValueError):
                self.build('failed')
        root=self.root/'failed'
        self.assertFalse((root/'campaign.json').exists())
        self.assertEqual(read_json(root/'driver-receipt.json')['status'],'preparing')
        receipt=read_json(root/'constraints-receipt/receipt.json')
        self.assertEqual(receipt['status'],'failed')
        self.assertNotIn('project/design.net-constraints.record.json',receipt['inputs'])
        self.assertFalse((root/'inputs/project/design.net-constraints.record.json').exists())

    def test_net_constraint_failures_refuse_before_output(self):
        self.use_net_constraints()
        constraints=self.source/'constraints.json'
        original=read_json(constraints)
        for doc in ({**original,'classes':[]},
                    {**original,'classes':[{**original['classes'][0],'requirement':'unmapped'}]}):
            write_json(constraints,doc)
            with self.assertRaises(ValueError):
                self.build('refused')
            self.assertFalse((self.root/'refused').exists())
        self.plan['net_constraints']='absent.json'
        self.save()
        with self.assertRaisesRegex(ValueError,'uncaptured pod net_constraints'):
            self.build('refused')

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
