"""Target-specific contracts use actual owned native reports, never empty-domain inference."""
import unittest
from agent_ee.kicad_happy import declarations

class AdapterDeclarationsTest(unittest.TestCase):
    def test_feedback_requires_closed_adapter_and_immutable_assumptions(self):
        rule = {'id': 'feedback', 'adapter': 'regulator-feedback/1', 'analyzer': 'schematic',
                'rule_id': 'PR-DET', 'components': ['U1', 'R1', 'R2'], 'min': 4.9, 'max': 5.1,
                'assumptions': {'vref_v': 1.21, 'input_v': 12, 'load_a': .1,
                    'input_net': '+12V', 'output_net': '+5V', 'feedback_net': 'FB',
                    'ground_net': 'GND', 'regulator': 'U1', 'top': 'R1', 'bottom': 'R2'},
                'assumptions_source': {'path': 'requirements.md', 'locator': 'ideal regulator'}}
        params = {'rules': [rule], 'counted_rules': ['feedback'], 'blocking_rules': ['feedback']}
        self.assertEqual(declarations(params)[0], [rule])
        for change in ({'adapter': 'arbitrary-pointer/1'}, {'assumptions': {}},
                       {'assumptions_source': {}}, {'field': '/anything'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                declarations({**params, 'rules': [{**rule, **change}]})

import copy
import json
import os
from pathlib import Path
import tempfile
import time
from agent_ee import candidate, checks
from agent_ee.kicad_happy import installation, normalize
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/kicad-happy/qualified'
INSTALLED = Path(os.environ.get('AGENT_EE_KICAD_HAPPY', str(Path.home() / 'git/kicad-happy')))

def feedback_rule():
    return {'id': 'feedback', 'adapter': 'regulator-feedback/1', 'analyzer': 'schematic',
            'rule_id': 'PR-DET', 'components': ['U1','R1','R2'], 'min': 4.9, 'max': 5.1,
            'assumptions': {'vref_v': 1.21, 'input_v': 12, 'load_a': .1,
                'input_net': '+12V', 'output_net': '+5V', 'feedback_net': 'FB',
                'ground_net': 'GND', 'regulator': 'U1', 'top': 'R1', 'bottom': 'R2'},
            'assumptions_source': {'path': 'requirements.md', 'locator': 'ideal fixture assumptions'}}

class NativeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        if not (INSTALLED / '.claude-plugin/plugin.json').is_file():
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('installed kicad-happy unavailable')
            raise unittest.SkipTest('installed kicad-happy unavailable')
        cls.pin = installation(INSTALLED)['sha256']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='happy-adapter-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline_ids = {}

    def run_check(self, rule, variant='control', ambient=None):
        params = {'installation': str(INSTALLED), 'installation_sha256': self.pin,
                  'rules': [rule], 'counted_rules': [rule['id']], 'blocking_rules': [rule['id']]}
        if ambient is not None:
            params['ambient_c'] = ambient
        scope = {'schema': 'agent-ee.scope/2', 'name': 'Original native qualification',
                 'scope': 'Bounded fixture adapter test, no hardware qualification',
                 'inputs': [{'path': str(FIXTURE / variant), 'at': 'project'},
                            {'path': str(FIXTURE / 'requirements.md'), 'at': 'requirements.md'}],
                 'schematic': 'project/qualification.kicad_sch',
                 'board': 'project/qualification.kicad_pcb', 'requirements': ['requirements.md'],
                 'checks': [{'id': 'happy', 'kind': 'kicad_happy', 'params': params}],
                 'baseline': {'requirements': [{'id': 'R-proxy', 'statement': 'Meet declared bounded fixture proxy',
                    'source': {'path': 'requirements.md', 'locator': 'ideal fixture assumptions'}, 'checks': ['happy']}],
                    'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        path = self.root / 'scope.json'; path.write_text(json.dumps(scope))
        run = self.root / str(time.monotonic_ns()); run.mkdir()
        candidate.capture(path, run)
        from agent_ee.storage import identity
        baseline_id=json.loads((run/'baseline.json').read_text())['baseline_id']
        params_id=identity(params)
        self.assertEqual(self.baseline_ids.setdefault(params_id,baseline_id),baseline_id,
                         'design-only seed must preserve immutable assumptions/criterion baseline')
        result = checks.run_checks(run, time.monotonic() + 600)[0]
        reports = {n: json.loads((run / f'work/happy/{n}.json').read_text())
                   for n in ('schematic','pcb','thermal','emc') if (run / f'work/happy/{n}.json').exists()}
        return run, result, reports

    def assert_sealed(self, run, result):
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        freeze_evidence(run, [result])
        validate_witnesses(run, [{'requirement_id': 'R-proxy', 'check_id': 'happy'}],
                           [{'path': 'checks/happy.json', 'locator': 'check:happy'}])
        evaluation=result['details']['normalized']['evaluations'][0]
        for source in evaluation['native_sources']:
            value=json.loads((run/'work/happy'/source['raw_output_path']).read_text())
            for token in source['raw_locator'].split('/')[1:]:
                token=token.replace('~1','/').replace('~0','~')
                value=value[int(token)] if isinstance(value,list) else value[token]
            self.assertIsNotNone(value)
        raw=run/'work/happy'/evaluation['raw_output_path']; original=raw.read_bytes()
        raw.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError,'changed|seal'):
            validate_witnesses(run,[{'requirement_id':'R-proxy','check_id':'happy'}],
                               [{'path':'checks/happy.json','locator':'check:happy'}])
        raw.write_bytes(original)
        assumption=run/'inputs/requirements.md'; original=assumption.read_bytes()
        assumption.write_bytes(original+b'changed operating assumption')
        with self.assertRaises(ValueError):
            candidate.verify_snapshot(run)
        assumption.write_bytes(original)
        baseline=run/'baseline.json'; original=baseline.read_bytes()
        value=json.loads(original); value['criteria'][0]['params']['rules'][0]['max']=999
        baseline.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'baseline'):
            validate_witnesses(run,[{'requirement_id':'R-proxy','check_id':'happy'}],
                               [{'path':'checks/happy.json','locator':'check:happy'}])
        baseline.write_bytes(original)


class NativeAdaptersTest(NativeCase):
    def test_native_feedback_control_seed_and_missing_provenance(self):
        rule = feedback_rule()
        _, result, reports = self.run_check(rule)
        self.assertEqual((result['execution'], result['outcome']), ('ok','pass'), result)
        boundary=copy.deepcopy(rule); boundary['min']=5; boundary['max']=5
        self.assertEqual(normalize(reports,[boundary])[1][0]['status'],'satisfied')
        run, result, _ = self.run_check(rule, 'feedback')
        self.assertEqual((result['execution'], result['outcome']), ('ok','defect'), result)
        # Independent ideal divider: 1.21 * (1 + 479/121) = 6 V.
        self.assertAlmostEqual(result['details']['normalized']['evaluations'][0]['value'], 6)
        self.assert_sealed(run, result)
        for mutate in ('unknown-vref','disconnected','duplicate','malformed-divider','wrong-regulator'):
            d = copy.deepcopy(reports)
            r = next(f for f in d['schematic']['findings'] if f['rule_id']=='PR-DET')
            if mutate=='wrong-regulator':
                next(c for c in d['schematic']['components'] if c['reference']=='U1')['value']='LM317'
            if mutate=='unknown-vref': r['vref_source']='heuristic'
            if mutate=='disconnected': r.pop('feedback_divider')
            if mutate=='duplicate': d['schematic']['findings'].append(r)
            if mutate=='malformed-divider': r['feedback_divider']['r_top']=None
            with self.subTest(mutate=mutate):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

    def test_native_protection_explicit_subcheck_and_missing_vbus(self):
        rule = {'id':'protection', 'adapter':'usb-vbus-presence/1', 'analyzer':'schematic',
                'rule_id':'UC-002', 'components':['J1'], 'min':1, 'max':1,
                'assumptions':{'connector':'J1','vbus_net':'+5V','subcheck':'vbus_esd_protection'},
                'assumptions_source':feedback_rule()['assumptions_source']}
        _, result, reports = self.run_check(rule)
        self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
        run, result, _ = self.run_check(rule,'protection')
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.assert_sealed(run,result)
        for mutation in ('missing-vbus','optional-info','duplicate','unknown-device','malformed-subchecks'):
            d=copy.deepcopy(reports)
            c=d['schematic']['usb_compliance']['connectors'][0]
            if mutation=='malformed-subchecks': d['schematic']['usb_compliance']=None
            if mutation=='missing-vbus': c['checks'].pop('vbus_esd_protection')
            if mutation=='optional-info': c['checks']['vbus_esd_protection']='info'
            if mutation=='duplicate': d['schematic']['usb_compliance']['connectors'].append(c)
            if mutation=='unknown-device':
                next(x for x in d['schematic']['components'] if x['reference']=='D1')['value']='unknown'
                next(x for x in d['schematic']['components'] if x['reference']=='D1')['lib_id']='Device:D'
            with self.subTest(mutation=mutation):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

    def test_native_power_inventory_uses_complete_pin_set(self):
        rule={'id':'power', 'adapter':'power-pin-inventory/1', 'analyzer':'schematic',
              'rule_id':'power-pin-inventory', 'components':['U1','U2','J2'], 'min':1,'max':1,
              'assumptions':{'ground_net':'GND','pins':[
                  {'component':'J2','pin':'1','net':'+12V','role':'power_out','current_a':.1},
                  {'component':'U1','pin':'2','net':'+12V','role':'power_in','current_a':.1},
                  {'component':'U1','pin':'4','net':'+5V','role':'power_out','current_a':.1},
                  {'component':'U2','pin':'1','net':'+5V','role':'power_in','current_a':.1}]},
              'assumptions_source':feedback_rule()['assumptions_source']}
        _, result, reports=self.run_check(rule)
        self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
        run,result,_=self.run_check(rule,'power')
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.assertEqual(result['details']['normalized']['evaluations'][0]['value'],0)
        self.assert_sealed(run,result)
        for mutation in ('extra-sink','missing-inventory','duplicate-source'):
            d=copy.deepcopy(reports)
            if mutation=='extra-sink':
                d['schematic']['nets']['+5V']['pins'].append({'component':'U9','pin_number':'1','pin_type':'power_in'})
            if mutation=='missing-inventory': d['schematic'].pop('nets')
            if mutation=='duplicate-source': d['schematic']['nets']['+5V']['pins'].append(d['schematic']['nets']['+5V']['pins'][0])
            with self.subTest(mutation=mutation):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

    def test_native_capacitor_derating_bounds_and_contradictory_rating(self):
        rule={'id':'derating','adapter':'capacitor-voltage/1','analyzer':'schematic',
              'rule_id':'capacitor-voltage','components':['C1'],'max':.5,
              'assumptions':{'operating_v':5,'rail_net':'+5V','ground_net':'GND',
                             'dielectric':'X7R','regulator':'U1'},
              'assumptions_source':feedback_rule()['assumptions_source']}
        _,result,reports=self.run_check(rule)
        self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
        self.assertEqual(result['details']['normalized']['evaluations'][0]['value'],.3125)
        run,result,_=self.run_check(rule,'derating')
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.assertAlmostEqual(result['details']['normalized']['evaluations'][0]['value'],5/6)
        self.assert_sealed(run,result)
        equal=copy.deepcopy(rule); equal['max']=.3125
        self.assertEqual(normalize(reports,[equal])[1][0]['status'],'satisfied')
        above=copy.deepcopy(rule); above['max']=.3124
        self.assertEqual(normalize(reports,[above])[1][0]['status'],'violation')
        for mutation in ('unknown-rating','contradictory-rating','missing-rail','unknown-dielectric'):
            d=copy.deepcopy(reports)
            c=next(c for c in d['schematic']['components'] if c['reference']=='C1')
            if mutation=='unknown-rating': c['value']='10u X7R'
            if mutation=='contradictory-rating': c['value']='10u/25V X7R'
            if mutation=='missing-rail': c['pin_nets'].pop('1')
            if mutation=='unknown-dielectric': c['value']='10u/16V'
            with self.subTest(mutation=mutation):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

    def test_native_thermal_heuristic_formula_and_unknown_package(self):
        rule={'id':'thermal','adapter':'thermal-ldo-estimate/1','analyzer':'thermal',
              'rule_id':'TH-DET','components':['U1'],'max':70,
              'assumptions':{'ambient_c':25,'input_v':12,'output_v':5,'load_a':.1,
                             'load_source':'output_cap_proxy','rtheta_source':'package_table',
                             'tj_max_source':'default_125'},
              'assumptions_source':feedback_rule()['assumptions_source']}
        _,result,reports=self.run_check(rule,ambient=25)
        self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
        self.assertEqual(result['details']['normalized']['evaluations'][0]['value'],67)
        run,result,seed_reports=self.run_check(rule,'thermal',ambient=25)
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.assertAlmostEqual(result['details']['normalized']['evaluations'][0]['value'],200)
        seed_boundary=copy.deepcopy(rule); seed_boundary['max']=200
        self.assertEqual(normalize(seed_reports,[seed_boundary])[1][0]['status'],'satisfied')
        self.assert_sealed(run,result)
        boundary=copy.deepcopy(rule); boundary['max']=67
        self.assertEqual(normalize(reports,[boundary])[1][0]['status'],'satisfied')
        for mutation in ('missing','unknown-package','default-rtheta','wrong-formula','wrong-ambient','unmarked-tjmax','malformed-provenance'):
            d=copy.deepcopy(reports); a=d['thermal']['assessments'][0]
            if mutation=='malformed-provenance':
                next(f for f in d['schematic']['findings'] if f['rule_id']=='PR-DET')['power_dissipation']['_iout_provenance']=None
            if mutation=='missing': d['thermal']['assessments']=[]
            if mutation=='unknown-package': a['package']='unknown'
            if mutation=='default-rtheta': a['rtheta_ja_source']='default'
            if mutation=='wrong-formula': a['tj_estimated_c']=66
            if mutation=='wrong-ambient': a['ambient_c']=40
            if mutation=='unmarked-tjmax': a.pop('tj_max_source')
            with self.subTest(mutation=mutation):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

    def test_native_emc_nearest_electrical_connector_and_skip_boundary(self):
        rule={'id':'emc','adapter':'esd-distance/1','analyzer':'emc','rule_id':'ES-001',
              'components':['RV1','J1'],'max':15,
              'assumptions':{'protection':'RV1','connector':'J1','protected_net':'+5V'},
              'assumptions_source':feedback_rule()['assumptions_source']}
        _,result,reports=self.run_check(rule)
        self.assertEqual((result['execution'],result['outcome']),('ok','pass'),result)
        self.assertEqual(result['details']['normalized']['evaluations'][0]['value'],2)
        run,result,_=self.run_check(rule,'emc')
        self.assertEqual((result['execution'],result['outcome']),('ok','defect'),result)
        self.assertEqual(result['details']['normalized']['evaluations'][0]['value'],20)
        self.assertTrue(any(f['rule_id']=='ES-001' and f['native_locator']['components']==['RV1','J1']
                            for f in result['details']['normalized']['findings']))
        self.assert_sealed(run,result)
        for mutation in ('far-skip','unrelated-nearest','missing-position','no-protection','unsupported-type','empty-vias','pcb-disconnected'):
            d=copy.deepcopy(reports)
            fp=next(f for f in d['pcb']['footprints'] if f['reference']=='RV1')
            if mutation=='pcb-disconnected': fp['pad_nets']={'1':{'net':'other'},'2':{'net':'GND'}}
            if mutation=='far-skip': fp['x']=300
            if mutation=='unrelated-nearest':
                other=copy.deepcopy(next(f for f in d['pcb']['footprints'] if f['reference']=='J2'))
                other['reference']='J9'; other['x']=52.5; other['y']=30
                d['pcb']['footprints'].append(other)
            if mutation=='missing-position': fp.pop('x')
            if mutation=='no-protection': d['schematic']['findings']=[f for f in d['schematic']['findings'] if f.get('ref')!='RV1']
            if mutation=='unsupported-type':
                next(f for f in d['schematic']['findings'] if f.get('ref')=='RV1')['type']='diode'
            if mutation=='empty-vias':
                d['pcb']['vias']={'vias':[]}
                # ES-001 evaluates independently of ES-002's zero-via blind spot.
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'satisfied')
                absent={**rule,'adapter':'esd-ground-vias/1','rule_id':'ES-002'}
                with self.assertRaises(ValueError): declarations({'rules':[absent],'counted_rules':['emc'],'blocking_rules':['emc']})
                continue
            with self.subTest(mutation=mutation):
                self.assertEqual(normalize(d,[rule])[1][0]['status'],'not_evaluated')

class CapabilityHelperTest(unittest.TestCase):
    def test_pure_descriptor_binds_assumptions_file_and_only_qualified_predicates(self):
        from agent_ee.kicad_happy import describe_capability
        params={'installation':'/installed','installation_sha256':'a'*64,
                'rules':[feedback_rule()], 'counted_rules':['feedback'],'blocking_rules':['feedback']}
        descriptor=describe_capability(params, {'requirements.md':'b'*64})
        self.assertEqual(descriptor['status'],'supported')
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['predicates'],['happy:feedback:regulator-feedback/1'])
        self.assertEqual(descriptor['immutable_inputs'],[{'path':'requirements.md','sha256':'b'*64}])
        self.assertEqual(describe_capability(params,{})['status'],'unsupported')
        unsupported=copy.deepcopy(params); unsupported['rules'][0]['adapter']='generic-domain-presence/1'
        self.assertFalse(describe_capability(unsupported,{'requirements.md':'b'*64})['can_evaluate_bound'])

class NativeFixtureChecksTest(NativeCase):
    def test_owned_fixtures_retain_actual_native_rule_outcomes(self):
        for variant in ('control','feedback','power','protection','derating','thermal','emc'):
            with self.subTest(variant=variant):
                self.run_check(feedback_rule(),variant)
                scope=json.loads((self.root/'scope.json').read_text())
                scope['checks']=[{'id':'erc','kind':'erc'},{'id':'drc','kind':'drc'}]
                scope['baseline']['requirements'][0]['checks']=['erc','drc']
                path=self.root/'native.scope.json'; path.write_text(json.dumps(scope))
                run=self.root/('native-'+variant); run.mkdir()
                candidate.capture(path,run)
                results=checks.run_checks(run,time.monotonic()+600)
                for result in results:
                    self.assertEqual(result['execution'],'ok',result)
                    # Simplified original layout is intentionally unrouted and
                    # lacks production library bindings; these are not DRC-clean
                    # hardware examples. Native violations must remain defects.
                    self.assertEqual(result['outcome'],'defect',result)
                    self.assertTrue(result['evidence'])
                    for rel in result['evidence']:
                        self.assertTrue((run/rel).is_file(),rel)
