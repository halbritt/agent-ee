"""Static analyzer coverage cannot outpromise the runtime assumption contract."""
import copy
import unittest

from agent_ee.capabilities import describe_check
from test_happy_adapters import feedback_rule
import test_kicad_happy as native
from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.storage import read_json, write_json
import time


class HappyCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.params = {'installation': '/installed', 'installation_sha256': 'a' * 64,
                       'rules': [feedback_rule()], 'counted_rules': ['feedback'],
                       'blocking_rules': ['feedback']}
        self.scope = {'schematic': 'design.kicad_sch', 'board': 'design.kicad_pcb',
                      'requirements': ['requirements.md']}
        self.files = {
            'design.kicad_sch': 'b' * 64, 'design.kicad_pcb': 'c' * 64, 'requirements.md': 'd' * 64}

    def describe(self):
        spec = {'id': 'happy', 'kind': 'kicad_happy', 'params': self.params}
        return describe_check(spec, check_specs=[spec], files=self.files, scope=self.scope)

    def test_bounded_adapter_freezes_source_and_has_explicit_predicate(self):
        descriptor = self.describe()
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(descriptor['immutable_inputs'], ['requirements.md'])
        self.assertEqual(descriptor['predicates'], ['happy:feedback:regulator-feedback/1'])
        self.assertTrue(descriptor['can_evaluate_bound'])
        self.params['blocking_rules'] = []
        self.assertFalse(self.describe()['can_evaluate_bound'])

    def test_missing_or_unowned_source_and_native_input_refuse(self):
        for path in list(self.files):
            with self.subTest(path=path):
                saved = self.files.pop(path)
                self.assertEqual(self.describe()['status'], 'unsupported')
                self.files[path] = saved
        self.scope['requirements'] = []
        self.assertEqual(self.describe()['status'], 'unsupported')

    def test_unknown_rule_pin_and_ambient_refuse(self):
        original = copy.deepcopy(self.params)
        for mutate in (lambda p: p.update(installation_sha256='bad'),
                       lambda p: p.update(ambient_c=float('nan')),
                       lambda p: p['rules'][0].update(adapter='unknown/1')):
            self.params = copy.deepcopy(original)
            mutate(self.params)
            self.assertEqual(self.describe()['status'], 'unsupported')


class NativeHappyCoverageTests(unittest.TestCase):
    def test_scope3_rc_control_seed_and_seal(self):
        # Reuse fixture setup without inheriting and rerunning its full test class.
        native.InstalledAnalyzerTest.setUpClass()
        helper = native.InstalledAnalyzerTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        for name, outcome in [('control', 'pass'), ('seeded', 'defect')]:
            with self.subTest(name=name):
                helper.run_check(native.specification(native.INSTALLED), name, scope2=True)
                scope = read_json(helper.root / 'scope.json')
                scope['schema'] = 'agent-ee.scope/3'
                scope['baseline']['requirements'][0]['intent'] = 'acceptance'
                write_json(helper.root / 'scope.json', scope)
                run = helper.root / ('scope3-' + name)
                run.mkdir()
                candidate.capture(helper.root / 'scope.json', run)
                results = checks.run_checks(run, time.monotonic() + 90)
                self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', outcome), results)
                descriptor = read_json(run / 'baseline.json')['coverage']['checks']['happy']
                self.assertEqual(descriptor['predicates'], ['happy:cutoff:low-pass-rc/1'])
                freeze_evidence(run, results)
                verify_evidence(run, results, required=True)
