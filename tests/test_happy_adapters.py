"""Target-specific contracts use actual owned native reports, never empty-domain inference."""
import unittest
from agent_ee.kicad_happy import declarations

class AdapterDeclarationsTest(unittest.TestCase):
    def test_feedback_requires_closed_adapter_and_immutable_assumptions(self):
        rule = {'id': 'feedback', 'adapter': 'regulator-feedback/1', 'analyzer': 'schematic',
                'rule_id': 'PR-DET', 'components': ['U1', 'R1', 'R2'], 'min': 4.9, 'max': 5.1,
                'assumptions': {'vref_v': 1.25, 'input_v': 12, 'load_a': .1,
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
            'assumptions': {'vref_v': 1.25, 'input_v': 12, 'load_a': .1,
                'input_net': '+12V', 'output_net': '+5V', 'feedback_net': 'FB',
                'ground_net': 'GND', 'regulator': 'U1', 'top': 'R1', 'bottom': 'R2'},
            'assumptions_source': {'path': 'requirements.md', 'locator': 'ideal fixture assumptions'}}

class NativeAdaptersTest(unittest.TestCase):
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
        result = checks.run_checks(run, time.monotonic() + 600)[0]
        reports = {n: json.loads((run / f'work/happy/{n}.json').read_text())
                   for n in ('schematic','pcb','thermal','emc') if (run / f'work/happy/{n}.json').exists()}
        return run, result, reports

    def assert_sealed(self, run, result):
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        freeze_evidence(run, [result])
        validate_witnesses(run, [{'requirement_id': 'R-proxy', 'check_id': 'happy'}],
                           [{'path': 'checks/happy.json', 'locator': 'check:happy'}])

    def test_native_feedback_control_seed_and_missing_provenance(self):
        rule = feedback_rule()
        _, result, reports = self.run_check(rule)
        self.assertEqual((result['execution'], result['outcome']), ('ok','pass'), result)
        run, result, _ = self.run_check(rule, 'feedback')
        self.assertEqual((result['execution'], result['outcome']), ('ok','defect'), result)
        # Independent ideal divider: 1.25 * (1 + 1000/240) = 6.458333333 V.
        self.assertAlmostEqual(result['details']['normalized']['evaluations'][0]['value'], 6.458333333)
        self.assert_sealed(run, result)
        for mutate in ('unknown-vref','disconnected','duplicate'):
            d = copy.deepcopy(reports)
            r = next(f for f in d['schematic']['findings'] if f['rule_id']=='PR-DET')
            if mutate=='unknown-vref': r['vref_source']='heuristic'
            if mutate=='disconnected': r.pop('feedback_divider')
            if mutate=='duplicate': d['schematic']['findings'].append(r)
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
        for mutation in ('missing-vbus','optional-info','duplicate','unknown-device'):
            d=copy.deepcopy(reports)
            c=d['schematic']['usb_compliance']['connectors'][0]
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
                  {'component':'U1','pin':'1','net':'+12V','role':'power_in','current_a':.1},
                  {'component':'U1','pin':'2','net':'+5V','role':'power_out','current_a':.1},
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
