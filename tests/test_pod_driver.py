"""Real scaffold/receipt/check execution; synthetic inputs never qualify pod hardware."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ScaffoldFixture
from toolchain import fome_mc_store, require_toolchain
from agent_ee import pod_driver as pod, kicad_route_attempt as attempt
from tests.kicad_route_fixture import make_checkout, FAKE_PIN
from agent_ee.storage import read_json, write_json, digest, identity
from agent_ee.stage_receipts import validate_pre_receipt
from agent_ee import spec_record
from agent_ee.storage import json_bytes

ROOT = Path(__file__).resolve().parents[1]
# The captured Rev-3 requirement accounting (spec, exact spec-record capture and compiled scope),
# read only from the owner's fome-mc object store at this pinned commit; never copied into the tree.
REV3_COMMIT = '1157b91899fd9f420089fbec4264b823310f0ad9'
REV3_DIR = 'telemetry-pod/expansion/requirements/compiled/'


def rev3_capture():
    """(spec bytes, spec-record bytes, compiled scope) of the actual captured Rev-3 baseline."""
    repository = fome_mc_store(Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(Path.home() / 'git/fome-mc'))),
                               REV3_COMMIT)
    def show(path):
        return subprocess.run(['git', '-C', str(repository), 'show', f'{REV3_COMMIT}:{REV3_DIR}{path}'],
                              capture_output=True, check=True).stdout
    return (show('requirements-map/spec.md'), show('requirements-map/spec-record.json'),
            json.loads(show('scope.json')))


def recapture(spec_raw, record_raw, drop=None, replace=None):
    """The ordinary capture of an edited spec: one acceptance line removed, or text replaced."""
    if drop is not None:
        lines = spec_raw.split(b'\n')
        hits = [i for i, line in enumerate(lines) if line.startswith(drop)]
        assert len(hits) == 1, drop
        del lines[hits[0]]
        spec_raw = b'\n'.join(lines)
    if replace is not None:
        assert spec_raw.count(replace[0]) == 1
        spec_raw = spec_raw.replace(*replace)
    ids, name = json.loads(record_raw)['ids'], json.loads(record_raw)['spec']['name']
    record = json_bytes(spec_record.capture(spec_raw, ids['prefix'], ids['codes'], name))
    spec_record.verify(record, spec_raw)
    return spec_raw, record


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
            spec='spec.md',spec_record='spec-record.json',requirement_source='telemetry-pod-rev2',writer_inputs={'intent':'intent.json','deviations':'deviations.json'},
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
        self.assertEqual(receipt['budget_derivation']['max_iterations']['source'],'default')
        self.assertEqual(receipt['budget_derivation']['max_seconds']['source'],'derived')
        self.assertEqual(receipt['validation']['routing_step']['adoptable_exits'],[0,2])
        self.assertEqual(receipt['validation']['routing_step']['step'],'kicad-route-builtin')
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

    def test_out_of_engine_range_budget_and_provider_fallback_refused(self):
        for changed in ({'max_iterations':0},{'max_iterations':101},{'max_iterations':-1},{'max_iterations':2.0},
                        {'max_iterations':'2'},{'max_seconds':100},{'max_iterations':True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                pod.validate_plan({**self.plan,**changed})
        self.plan['implementation']={**self.plan['implementation'],'harness':'openrouter'}
        with self.assertRaisesRegex(ValueError,'harness'):
            pod.validate_plan(self.plan)

    def test_explicit_iteration_budgets_refused_before_output(self):
        # AGENTEE-197 (agent-509 production decision): the engine's 1-100 range is the only bound.
        cases=[({'max_iterations':0},'between 1 and 100'),({'max_iterations':101},'between 1 and 100'),
               ({'max_iterations':2,'implementation':{**self.plan['implementation'],'harness':'openrouter'}},'harness'),
               ({'max_iterations':2,'review_profile':{**self.plan['review_profile'],'reviewers':[
                   {**r,'harness':'openrouter'} if i==0 else r
                   for i,r in enumerate(self.plan['review_profile']['reviewers'])]}},'harness')]
        original=copy.deepcopy(self.plan)
        for n,(changed,message) in enumerate(cases):
            with self.subTest(changed=sorted(changed)):
                self.plan={**original,**changed}
                self.save()
                with self.assertRaisesRegex(ValueError,message):
                    pod.build(self.plan_path,self.root/f'refused-{n}')
                self.assertFalse((self.root/f'refused-{n}').exists())
                stderr=io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(pod.main([str(self.plan_path),'--out',str(self.root/f'cli-{n}')]),2)
                self.assertRegex(stderr.getvalue(),'pod driver refused: .*'+message)
                self.assertFalse((self.root/f'cli-{n}').exists())

    def test_two_iteration_plan_produces_campaign_and_receipt(self):
        from agent_ee import campaign
        self.plan['max_iterations']=2
        self.save()
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        config=read_json(self.root/'run/campaign.json')
        self.assertEqual(config['max_iterations'],2)
        self.assertEqual(campaign.validate_iterations(config['max_iterations']),2)
        self.assertEqual(receipt['validation']['max_iterations'],2)
        stored=read_json(self.root/'run/driver-receipt.json')
        self.assertEqual(stored['status'],'prepared')
        self.assertEqual(stored['campaign_sha256'],digest(self.root/'run/campaign.json'))
        derivation=stored['budget_derivation']
        self.assertEqual(derivation['max_iterations'],
                         dict(value=2,source='plan',default=10,engine_range=[1,100]))
        # The derived wall clock covers two routing receipts and the review reserve and changes neither.
        self.assertEqual(derivation['routing'],dict(budget_s=300,timeout_s=900,step_margin_s=300,reserved_turns=2))
        self.assertEqual(derivation['review_budget_s'],3600)
        self.assertEqual(derivation['max_seconds']['value'],config['max_seconds'])
        self.assertEqual((derivation['max_seconds']['minimum_s'],derivation['max_seconds']['source']),(6000,'derived'))
        self.assertEqual(config['max_seconds'],2*(900+300)+3600)
        self.assertEqual(config['routing_adapter'],self.plan['routing_adapter'])
        self.assertEqual((receipt['validation']['routing_step']['timeout_s'],config['routing_adapter']['budget']),
                         (900+300,300))
        self.assertTrue(receipt['first_capture_binding']['bound'])
        # The engine itself refuses a campaign outside its range before reading scope or creating output.
        for bad in (0,101):
            with self.subTest(engine=bad):
                path=self.root/f'engine-{bad}.json'
                write_json(path,{**config,'max_iterations':bad})
                with self.assertRaisesRegex(ValueError,'between 1 and 100'):
                    campaign.run_campaign(path,self.root/f'engine-{bad}')
                self.assertFalse((self.root/f'engine-{bad}').exists())

    def test_budget_covers_two_full_receipt_envelopes_and_reviews(self):
        self.assertEqual(pod.validate_plan({**self.plan,'max_seconds':6000}),(10,6000))
        self.assertEqual(pod.validate_plan({**self.plan,'max_iterations':2}),(2,6000))
        self.assertEqual(pod.validate_plan({**self.plan,'max_iterations':1}),(1,6000))
        self.assertEqual(pod.validate_plan({**self.plan,'max_iterations':100}),(100,6000))
        explicit=pod.budget_derivation({**self.plan,'max_iterations':2,'max_seconds':7000})
        self.assertEqual((explicit['max_seconds']['source'],explicit['max_seconds']['minimum_s']),('plan',6000))
        self.assertEqual(explicit['routing']['timeout_s'],900)
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

    def use_rev3(self, spec_raw=None, record_raw=None, limitations=None):
        """Graft the actual captured Rev-3 spec, its exact capture and the compiled scope's
        criterion accounting into the synthetic driver fixture (still not pod hardware)."""
        real_spec, real_record, real_scope = rev3_capture()
        (self.source/'spec.md').write_bytes(real_spec if spec_raw is None else spec_raw)
        (self.source/'spec-record.json').write_bytes(real_record if record_raw is None else record_raw)
        self.scope['limitations'] = [s for s in self.scope['limitations'] if not s.startswith('POD-AC-')]
        self.scope['limitations'] += real_scope['limitations'] if limitations is None else limitations
        ac3 = copy.deepcopy(next(r for r in real_scope['baseline']['requirements'] if r['id'] == 'POD-AC-3'))
        # The real ERC/DRC requirement, its source rebased to this fixture's copy of the same spec bytes.
        ac3['source']['path'], ac3['checks'] = 'spec.md', ['erc', 'placement']
        self.scope['baseline']['requirements'].append(ac3)
        self.plan['requirement_source'] = 'telemetry-pod-rev3'
        self.save()
        return real_scope

    def refused_build(self, reason, requirement):
        with self.assertRaises(pod.RequirementSetError) as raised:
            pod.build(self.plan_path, self.root/'refused')
        self.assertEqual((raised.exception.reason, raised.exception.requirement), (reason, requirement))
        self.assertFalse((self.root/'refused').exists())
        return raised.exception

    def test_actual_rev3_baseline_accepted_with_limitations_visible(self):
        real_scope = self.use_rev3()
        with mock.patch('agent_ee.campaign.run_campaign', side_effect=AssertionError('inference forbidden')):
            receipt = self.build()
        found = receipt['validation']['requirement_set']
        self.assertEqual(found['revision'], 3)
        self.assertEqual(found['acceptance'], [f'POD-AC-{n}' for n in range(1, 12)])
        self.assertEqual(found['bench'], ['POD-AC-8', 'POD-AC-9', 'POD-AC-10', 'POD-AC-11'])
        self.assertEqual(found['spec_sha256'], 'e71dcca744f4b520fc4698402202d7d9efff2d316f8ad3c5eb6403cdf9d036c6')
        self.assertEqual((found['revision_locator']['byte_start'], found['revision_locator']['byte_end']), (30, 421))
        self.assertEqual((found['source'], found['record_sha256']), ('telemetry-pod-rev3',
                         '1d978fc1ddeddfe64a6900a719c437dabb31b2999feb258265b0fbad1ab4820b'))
        self.assertEqual((found['locators']['POD-AC-10'], found['locators']['POD-AC-11']), ([21943, 22224], [22229, 22495]))
        prepared = read_json(self.root/'run'/read_json(self.root/'run/campaign.json')['scope'])
        kept = prepared['limitations']
        for limitation in real_scope['limitations']:
            self.assertIn(limitation, kept)
        # Thermal, bench and owner DFM obligations remain visible; acceptance is not claimed.
        self.assertTrue(any(s.startswith('POD-TEMP-ENCLOSURE: NOT CHECKED') for s in kept))
        self.assertTrue(any(s.startswith('POD-AC-6: NOT CHECKED') and 'DFM' in s for s in kept))
        for n in (8, 9, 10, 11):
            self.assertTrue(any(s.startswith(f'POD-AC-{n}: BENCH REQUIRED') for s in kept))
        self.assertFalse(receipt['done'])

    def test_rev3_baseline_omitting_criterion_10_or_11_refused(self):
        # Review gap 6 regression: a supplied scope dropping POD-AC-10 was accepted.
        real_spec, real_record, real_scope = rev3_capture()
        for rid in ('POD-AC-10', 'POD-AC-11'):
            with self.subTest(scope_omits=rid):
                self.use_rev3(limitations=[s for s in real_scope['limitations'] if not s.startswith(rid + ':')])
                with self.assertRaises(pod.RequirementSetError) as raised:
                    pod.build(self.plan_path, self.root/'refused')
                self.assertEqual((raised.exception.reason, raised.exception.requirement),
                                 ('unaccounted-acceptance-criterion', rid))
                self.assertFalse((self.root/'refused').exists())
                self.scope['baseline']['requirements'].pop()
        for number in ('10', '11'):
            # A recapture without the item is not the pinned Rev-3 source, so it never reaches accounting.
            spec_raw, record_raw = recapture(real_spec, real_record, drop=number.encode() + b'. ')
            with self.subTest(spec_omits=number):
                self.use_rev3(spec_raw, record_raw)
                self.refused_build('unapproved-source', 'telemetry-pod-rev3')
                self.scope['baseline']['requirements'].pop()

    def downgraded_rev3(self):
        """Review H1: the real Rev-3 capture relabelled Revision 2, items 10/11 and their limitations removed."""
        real_spec, real_record, real_scope = rev3_capture()
        spec_raw, record_raw = recapture(real_spec, real_record, replace=(b'Revision 3,', b'Revision 2,'))
        for number in (b'10. ', b'11. '):
            spec_raw, record_raw = recapture(spec_raw, record_raw, drop=number)
        found = [r['id'] for r in json.loads(record_raw)['requirements'] if r['kind'] == 'acceptance']
        self.assertEqual(found, [f'POD-AC-{n}' for n in range(1, 10)])
        limitations = [s for s in real_scope['limitations'] if not s.startswith(('POD-AC-10:', 'POD-AC-11:'))]
        return spec_raw, record_raw, limitations

    def test_downgraded_rev3_capture_refused_in_full_build(self):
        spec_raw, record_raw, limitations = self.downgraded_rev3()
        self.use_rev3(spec_raw, record_raw, limitations)
        self.refused_build('unapproved-source', 'telemetry-pod-rev3')
        # Selecting the legacy source does not admit it either: it is not the pinned Rev-2 capture.
        self.plan['requirement_source'] = 'telemetry-pod-rev2'
        self.save()
        self.refused_build('unapproved-source', 'telemetry-pod-rev2')

    def test_source_substitution_refused_in_full_build(self):
        real_spec, real_record, real_scope = rev3_capture()
        self.use_rev3()
        # The genuine Rev-2 capture under the Rev-3 selection is a downgrade.
        for name in ('spec.md', 'spec-record.json'):
            shutil.copyfile(ROOT / 'fixtures/telemetry-pod' / name, self.source / name)
        self.save()
        self.refused_build('downgraded-source', 'telemetry-pod-rev3')
        # The genuine Rev-3 capture under the Rev-2 selection, and an edited recapture, are unapproved.
        self.scope['baseline']['requirements'].pop()
        self.use_rev3()
        self.plan['requirement_source'] = 'telemetry-pod-rev2'
        self.save()
        self.refused_build('unapproved-source', 'telemetry-pod-rev2')
        self.plan['requirement_source'] = 'telemetry-pod-rev3'
        spec_raw, record_raw = recapture(real_spec, real_record, replace=(b'Revision 3,', b'Revision 3 ,'))
        (self.source/'spec.md').write_bytes(spec_raw)
        (self.source/'spec-record.json').write_bytes(record_raw)
        self.save()
        self.refused_build('unapproved-source', 'telemetry-pod-rev3')
        # A source outside the pinned registry is refused while validating the plan.
        self.plan['requirement_source'] = 'telemetry-pod-rev4'
        self.save()
        self.refused_build('undeclared-source', 'telemetry-pod-rev4')
        self.plan['requirement_source'] = ['telemetry-pod-rev3']
        self.save()
        self.refused_build('undeclared-source', "['telemetry-pod-rev3']")
        del self.plan['requirement_source']
        self.save()
        with self.assertRaisesRegex(ValueError, 'expected agent-ee.pod-plan/1 fields'):
            pod.build(self.plan_path, self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def cli_recapture(self, name, edit=None):
        """Review H1: rename the supplied spec to ``name`` and recapture it with the ordinary capture CLI."""
        spec_raw = (self.source/self.plan['spec']).read_bytes()
        if edit is not None:
            assert spec_raw.count(edit[0]) == 1
            spec_raw = spec_raw.replace(*edit)
        ids = read_json(self.source/self.plan['spec_record'])['ids']
        (self.source/self.plan['spec']).unlink()
        (self.source/self.plan['spec_record']).unlink()
        (self.source/name).write_bytes(spec_raw)
        self.scope['baseline']['targets'] = [name if t == self.plan['spec'] else t for t in self.scope['baseline']['targets']]
        for requirement in self.scope['baseline']['requirements']:
            if requirement['source']['path'] == self.plan['spec']:
                requirement['source']['path'] = name
        self.plan['spec'] = name
        receipt = self.root/f'capture-{uuid.uuid4().hex}.receipt.json'
        subprocess.run([sys.executable, '-m', 'agent_ee.spec_record', 'capture', str(self.source/name),
                        '--out', str(self.source/self.plan['spec_record']), '--receipt', str(receipt),
                        '--prefix', ids['prefix'], *[f'--code={h}={c}' for h, c in ids['codes'].items()]],
                       check=True, capture_output=True, env={**os.environ, 'PYTHONPATH': str(ROOT/'src')})
        record_raw = (self.source/self.plan['spec_record']).read_bytes()
        record = spec_record.verify(record_raw, spec_raw)
        self.assertEqual(record['spec']['name'], name)
        self.save()
        return record_raw

    def renamed_recapture_accepted(self, source, name, count):
        pin = pod.SOURCES[source]
        record_raw = self.cli_recapture(name)
        self.assertNotEqual(spec_record.sha256(record_raw), pin['record_sha256'])
        with mock.patch('agent_ee.campaign.run_campaign', side_effect=AssertionError('inference forbidden')):
            receipt = self.build()
        found = receipt['validation']['requirement_set']
        self.assertEqual((found['source'], found['spec_sha256'], found['record_sha256'], found['capture_name'],
                          found['authoritative_record_sha256']),
                         (source, pin['spec_sha256'], spec_record.sha256(record_raw), name, pin['record_sha256']))
        self.assertEqual(found['acceptance'], [f'POD-AC-{n}' for n in range(1, count + 1)])
        self.assertFalse(receipt['done'])

    def test_cli_renamed_rev3_recapture_accepted_in_full_build(self):
        # Review H1: the filename is not source authority. The actual Rev-3 spec, byte-identical,
        # recaptured by the ordinary CLI under another filename is its pinned source.
        self.use_rev3()
        self.renamed_recapture_accepted('telemetry-pod-rev3', 'renamed.md', 11)
        # The same renamed recapture with one byte changed is another source.
        self.cli_recapture('renamed-edited.md', edit=(b'Revision 3,', b'Revision 3 ,'))
        self.refused_build('unapproved-source', 'telemetry-pod-rev3')

    def test_cli_renamed_rev2_recapture_accepted_in_full_build(self):
        self.renamed_recapture_accepted('telemetry-pod-rev2', 'pod-spec.md', 9)
        # Genuine Rev-2 bytes under a new name remain the earlier pin when Rev-3 is selected.
        self.plan['requirement_source'] = 'telemetry-pod-rev3'
        self.save()
        self.refused_build('downgraded-source', 'telemetry-pod-rev3')
        # A renamed one-byte change is unapproved under either selection.
        self.cli_recapture('renamed-edited.md', edit=(b'Revision 2', b'Revision 2 '))
        self.refused_build('unapproved-source', 'telemetry-pod-rev3')
        self.plan['requirement_source'] = 'telemetry-pod-rev2'
        self.save()
        self.refused_build('unapproved-source', 'telemetry-pod-rev2')

    def test_requirement_set_record_digest_must_be_the_verified_record(self):
        spec_raw = (ROOT/'fixtures/telemetry-pod/spec.md').read_bytes()
        record_raw = (ROOT/'fixtures/telemetry-pod/spec-record.json').read_bytes()
        record = spec_record.verify(record_raw, spec_raw)
        # A caller cannot present the pinned digest for a record whose content differs.
        with self.assertRaisesRegex(ValueError, 'record_sha256 is not the digest of the verified spec record'):
            pod.requirement_set('telemetry-pod-rev2', {**record, 'prose': []},
                                pod.SOURCES['telemetry-pod-rev2']['record_sha256'], self.scope)

    def test_pinned_sources_are_the_captured_files(self):
        real_spec, real_record, _ = rev3_capture()
        rev2 = [(ROOT/'fixtures/telemetry-pod'/name).read_bytes() for name in ('spec.md', 'spec-record.json')]
        for name, (spec_raw, record_raw), count in (('telemetry-pod-rev2', rev2, 9),
                                                     ('telemetry-pod-rev3', (real_spec, real_record), 11)):
            entry = pod.SOURCES[name]
            self.assertEqual((spec_record.sha256(spec_raw), spec_record.sha256(record_raw)),
                             (entry['spec_sha256'], entry['record_sha256']))
            record = spec_record.verify(record_raw, spec_raw)
            self.assertEqual(record['spec']['name'], entry['name'])
            self.assertEqual([r['id'] for r in record['requirements'] if r['kind'] == 'acceptance'],
                             [f'POD-AC-{n}' for n in range(1, count + 1)])

    def test_actual_rev3_requirement_set_and_refusals(self):
        real_spec, real_record, real_scope = rev3_capture()
        record = spec_record.verify(real_record, real_spec)
        found = pod.requirement_set('telemetry-pod-rev3', record, spec_record.sha256(real_record), real_scope)
        self.assertEqual((found['revision'], found['acceptance']), (3, [f'POD-AC-{n}' for n in range(1, 12)]))
        # Each criterion item cited in pod_driver.REVISIONS is the captured source's own text span.
        spans = {r['id']: (r['locator']['byte_start'], r['locator']['byte_end'])
                 for r in record['requirements'] if r['kind'] == 'acceptance'}
        self.assertEqual((spans['POD-AC-10'], spans['POD-AC-11']), ((21943, 22224), (22229, 22495)))
        self.assertTrue(real_spec[21943:22224].startswith(b'All eight analog channels'))
        def refused(scope):
            return refused_record(self, 'telemetry-pod-rev3', real_record, record, scope)
        for rid in ('POD-AC-10', 'POD-AC-11'):
            downgraded = copy.deepcopy(real_scope)
            downgraded['limitations'] = [s.replace(': BENCH REQUIRED', ': NOT CHECKED') if s.startswith(rid + ':')
                                         else s for s in real_scope['limitations']]
            self.assertEqual(refused(downgraded), ('missing-bench-obligation', rid))
        unmapped = copy.deepcopy(real_scope)
        unmapped['baseline']['requirements'] = []
        self.assertEqual(refused(unmapped), ('unaccounted-acceptance-criterion', 'POD-AC-3'))
        # A substring is not an accounting: 'POD-AC-10: ...' does not account for POD-AC-1.
        no_ac1 = copy.deepcopy(real_scope)
        no_ac1['limitations'] = [s for s in real_scope['limitations'] if not s.startswith('POD-AC-1: ')]
        self.assertEqual(refused(no_ac1), ('unaccounted-acceptance-criterion', 'POD-AC-1'))
        # Prose revision, criterion count and IDs never select the set: any other capture is unapproved.
        for edit in ((b'Revision 3,', b'Revision 4,'), (b'\n11. ', b'\n12. Extra criterion.\n11. ')):
            spec_raw, record_raw = recapture(real_spec, real_record, replace=edit)
            self.assertEqual(refused_record(self, 'telemetry-pod-rev3', record_raw, spec_record.verify(record_raw, spec_raw),
                                            real_scope), ('unapproved-source', 'telemetry-pod-rev3'))
        self.assertEqual(refused_record(self, 'telemetry-pod-rev9', real_record, record, real_scope),
                         ('undeclared-source', 'telemetry-pod-rev9'))
        # A pinned entry whose capture declares another revision is refused, not relabelled.
        spec_raw, record_raw = recapture(real_spec, real_record, replace=(b'Revision 3,', b'Revision 4,'))
        forged = dict(pod.SOURCES['telemetry-pod-rev3'], spec_sha256=spec_record.sha256(spec_raw),
                      record_sha256=spec_record.sha256(record_raw))
        with mock.patch.dict(pod.SOURCES, {'forged': forged}):
            self.assertEqual(refused_record(self, 'forged', record_raw, spec_record.verify(record_raw, spec_raw),
                                            real_scope), ('revision-mismatch', 'POD-DOC-P1'))

    def test_legacy_rev2_requirement_set_preserved(self):
        spec_raw = (ROOT/'fixtures/telemetry-pod/spec.md').read_bytes()
        record_raw = (ROOT/'fixtures/telemetry-pod/spec-record.json').read_bytes()
        record = spec_record.verify(record_raw, spec_raw)
        found = pod.requirement_set('telemetry-pod-rev2', record, spec_record.sha256(record_raw), self.scope)
        self.assertEqual((found['source'], found['revision'], found['acceptance'], found['bench']),
                         ('telemetry-pod-rev2', 2, [f'POD-AC-{n}' for n in range(1, 10)], ['POD-AC-8', 'POD-AC-9']))
        # Rev-2 is its pinned capture: a tenth criterion or a dropped ninth is another, unapproved source.
        for edit in (dict(replace=(b'\n9. ', b'\n10. Extra criterion.\n9. ')), dict(drop=b'9. ')):
            edited_spec, edited_record = recapture(spec_raw, record_raw, **edit)
            self.assertEqual(refused_record(self, 'telemetry-pod-rev2', edited_record,
                                            spec_record.verify(edited_record, edited_spec), self.scope),
                             ('unapproved-source', 'telemetry-pod-rev2'))
        # The legacy scope contract is unchanged: AC8/AC9 bench text only, as test_removed_bench_obligation_refused.
        with self.assertRaisesRegex(ValueError, 'POD-AC-9 must remain an explicit BENCH REQUIRED obligation'):
            pod.requirement_set('telemetry-pod-rev2', record, spec_record.sha256(record_raw),
                                {**self.scope, 'limitations': ['POD-AC-8: BENCH REQUIRED']})

    def test_genuine_rev2_source_accepted_in_full_build(self):
        with mock.patch('agent_ee.campaign.run_campaign', side_effect=AssertionError('inference forbidden')):
            receipt = self.build()
        found = receipt['validation']['requirement_set']
        self.assertEqual((found['source'], found['revision'], found['spec_sha256'], found['record_sha256']),
                         ('telemetry-pod-rev2', 2, pod.SOURCES['telemetry-pod-rev2']['spec_sha256'],
                          pod.SOURCES['telemetry-pod-rev2']['record_sha256']))
        self.assertEqual(found['acceptance'], [f'POD-AC-{n}' for n in range(1, 10)])
        self.assertFalse(receipt['done'])

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

    def test_caller_supplied_obstacle_and_group_sources_refused_before_output(self):
        # AGENTEE-365: the pod driver refuses caller sources exactly as the adapter binding does.
        self.use_placement_adapter()
        valid=read_json(self.source/'policy.json')
        source={'class':'board','enforcement':'hard','citation':'Original fixture: caller claim'}
        square=[[[0,0],[1000000,0],[1000000,1000000],[0,1000000]]]
        obstacle={'side':'F','polygons':square}
        group={'id':'g','references':['R1','C1'],'region':{'polygons':square}}
        cases=[({**valid,'constraint_provenance':{}},
                'constraint_provenance comes only from a captured envelope, never from the packing policy'),
               ({**valid,'obstacles':[{**obstacle,'provenance':source}]},
                'obstacle provenance is never accepted from the packing policy; '
                'a sourced keep-out comes from a captured envelope as a native rule area'),
               ({**valid,'groups':[{**group,'constraint_provenance':{'region':source}}]},
                'group constraint_provenance is never accepted from the packing policy; '
                'caller-declared regions and distances stay advisory')]
        for policy,reason in cases:
            with self.subTest(reason=reason):
                write_json(self.source/'policy.json',policy)
                with self.assertRaises(ValueError) as caught:
                    pod.build(self.plan_path,self.root/'refused')
                self.assertEqual(str(caught.exception),'invalid pod placement policy policy.json: '+reason)
                self.assertFalse((self.root/'refused').exists())
        # The same unsourced geometry is a valid pod policy.
        write_json(self.source/'policy.json',{**valid,'obstacles':[obstacle],'groups':[group]})
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        self.assertEqual(receipt['validation']['placement_adapter']['policy'],'policy.json')

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

    def use_rating_check(self, sealed=False):
        # binding-fill --ratings feeding an assembly_readiness check (AGENTEE-256); the step itself never runs here.
        from test_assembly_readiness import _write_sqlite_catalog
        (self.source/'assembly-catalog').mkdir()
        _write_sqlite_catalog(self.source/'assembly-catalog',[{'lcsc':'C1','stock':1}])
        params={'catalog':'assembly-catalog/catalog-manifest.json'}
        if sealed:
            (self.source/'old').mkdir()
            write_json(self.source/'old/binding-fill.receipt.json',{'test':'borrowed receipt'})
            write_json(self.source/'old/binding-fill.json',{'test':'borrowed report'})
            params['rating_provenance']=dict(receipt='old/binding-fill.receipt.json',report='old/binding-fill.json',
                receipt_sha256=digest(self.source/'old/binding-fill.receipt.json'))
        self.scope['checks'].insert(-1,dict(id='assembly',kind='assembly_readiness',stage='verification',params=params))
        self.scope['baseline']['requirements'][0]['checks'].insert(-1,'assembly')
        self.plan['implementation_steps'][0]['argv']=['/usr/bin/false','--ratings','ratings.json']
        self.save()

    def test_rating_step_with_assembly_check_opts_into_coordinator_provenance(self):
        from agent_ee import campaign
        self.use_rating_check()
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        config=read_json(self.root/'run/campaign.json')
        self.assertEqual(config['rating_provenance'],pod.RATING_PROVENANCE)
        self.assertEqual(receipt['validation']['rating_provenance'],pod.RATING_PROVENANCE)
        # The prepared scope stays sealed: the campaign derives the declaration per candidate.
        assembly=next(c for c in read_json(self.root/'run/scope.json')['checks'] if c['id']=='assembly')
        self.assertNotIn('rating_provenance',assembly['params'])
        self.start_campaign()
        # Dropping the opt-in is not silently accepted by the campaign either.
        del config['rating_provenance']
        config['implementation_steps'][0]['argv']=['/usr/bin/false']
        write_json(self.root/'run/campaign.json',config)
        self.start_campaign(name='without')
        config['rating_provenance']=pod.RATING_PROVENANCE
        write_json(self.root/'run/campaign.json',config)
        with self.assertRaisesRegex(ValueError,'--ratings'):
            campaign.run_campaign(self.root/'run/campaign.json',self.root/'refused-campaign')
        self.assertFalse((self.root/'refused-campaign').exists())

    def test_sealed_rating_provenance_is_refused_before_output(self):
        self.use_rating_check(sealed=True)
        with self.assertRaisesRegex(ValueError,'sealed scope cannot declare rating_provenance'):
            self.build('refused')
        self.assertFalse((self.root/'refused').exists())

    def test_plan_without_ratings_keeps_campaign_configuration(self):
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        self.assertNotIn('rating_provenance',read_json(self.root/'run/campaign.json'))
        self.assertNotIn('rating_provenance',receipt['validation'])


    def test_layer_policy_derivation_is_carried_into_the_campaign(self):
        # AGENTEE-260: the plan declares the method; the coordinator derives the bound per routing turn.
        derivation=dict(schema='agent-ee.layer-policy-derivation/1',directory='layer-policy',
                        policy={'signal_layers':['F.Cu','B.Cu']})
        self.plan['layer_policy_derivation']=derivation
        self.save()
        with mock.patch('agent_ee.campaign.run_campaign',side_effect=AssertionError('inference forbidden')):
            receipt=self.build()
        self.assertEqual(read_json(self.root/'run/campaign.json')['layer_policy_derivation'],derivation)
        self.assertEqual(receipt['validation']['layer_policy_derivation'],derivation)
        self.assertNotIn('layer_policy_provenance',read_json(self.root/'run/scope.json'))
        self.start_campaign()

    def test_layer_policy_derivation_refusals_before_output(self):
        derivation=dict(schema='agent-ee.layer-policy-derivation/1',directory='layer-policy',
                        policy={'signal_layers':['F.Cu','B.Cu']})
        for name,change,message in [
                ('literal',lambda: self.plan['routing_adapter'].update(layer_policy={'signal_layers':['F.Cu','B.Cu'],
                                                                                     'pour_area_loss_pct':0.5}),
                 'exclusive'),
                ('bound',lambda: derivation['policy'].update(pour_area_loss_pct=0.5),'cannot state pour_area_loss_pct'),
                ('sealed',lambda: self.scope.update(layer_policy_provenance={}),'sealed scope cannot declare')]:
            with self.subTest(name=name):
                original=copy.deepcopy((self.plan,self.scope,derivation))
                change()
                self.plan['layer_policy_derivation']=derivation
                self.save()
                with self.assertRaisesRegex(ValueError,message):
                    self.build('refused-'+name)
                self.assertFalse((self.root/('refused-'+name)).exists())
                self.plan,self.scope,derivation=original[0],original[1],original[2]


def refused_record(test, source, record_raw, record, scope):
    with test.assertRaises(pod.RequirementSetError) as raised:
        pod.requirement_set(source, record, spec_record.sha256(record_raw), scope)
    return raised.exception.reason, raised.exception.requirement


if __name__=='__main__':
    unittest.main()
