"""Capture-time coverage must not promote unverified citations to design checks."""
import copy
from pathlib import Path
import tempfile
import time
import unittest

from agent_ee.baseline import compile_baseline
from agent_ee.storage import digest, read_json, write_json
from agent_ee.candidate import capture
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.checks import run_checks
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class DatasheetCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inputs = Path(self.tmp.name)
        self.scope = read_json(ROOT / 'fixtures/datasheet-pins/control.scope.json')
        self.scope['schema'] = 'agent-ee.scope/3'
        self.scope['checks'] = self.scope['checks'][2:]
        req = self.scope['baseline']['requirements'][0]
        req.update(intent='acceptance', checks=['netlist', 'facts', 'pin-map'])
        self.facts = read_json(ROOT / 'fixtures/datasheet-pins/facts.json')
        write_json(self.inputs / 'facts.json', self.facts)
        self.files = {'facts.json': digest(self.inputs / 'facts.json'),
                      'TJA1051.pdf': 'a' * 64, 'requirements.md': 'b' * 64,
                      'project/pins.kicad_sch': 'c' * 64, 'project/pins.kicad_pcb': 'd' * 64}

    def compile(self):
        return compile_baseline(self.scope, self.files, self.inputs)

    def test_citation_is_prerequisite_not_defect_capability(self):
        baseline = self.compile()
        coverage = baseline['coverage']
        self.assertFalse(coverage['checks']['facts']['can_evaluate_bound'])
        pin = coverage['checks']['pin-map']
        self.assertEqual(pin['predicates'], ['pin-map:U1:6', 'pin-map:U1:7'])
        self.assertEqual(pin['prerequisites'], [
            {'check_id': 'facts', 'kind': 'datasheet_facts'},
            {'check_id': 'netlist', 'kind': 'netlist'}])
        self.assertEqual(coverage['requirements']['pins']['acceptance_checks'], ['pin-map'])
        self.assertEqual(pin['immutable_inputs'], ['TJA1051.pdf', 'facts.json'])
        self.assertIn('TJA1051.pdf', baseline['files'])
        self.scope['checks'].pop()
        self.scope['baseline']['requirements'][0]['checks'].remove('pin-map')
        with self.assertRaisesRegex(ValueError, 'no executable bound coverage'):
            self.compile()

    def test_inapplicable_missing_duplicate_and_forward_contracts_refuse(self):
        original = copy.deepcopy(self.scope)
        mutations = [
            lambda p: p.update(facts_check='absent'),
            lambda p: p.update(netlist_check='facts'),
            lambda p: p.update(symbols=[]),
            lambda p: p['symbols'][0].update(part_number='another part'),
            lambda p: p['symbols'][0].update(facts=['absent']),
            lambda p: p['symbols'][0].update(facts=['canl', 'canl']),
            lambda p: p['symbols'].append(copy.deepcopy(p['symbols'][0])),
        ]
        for mutation in mutations:
            self.scope = copy.deepcopy(original)
            mutation(self.scope['checks'][-1]['params'])
            with self.subTest(scope=self.scope), self.assertRaisesRegex(ValueError, 'unsupported capability'):
                self.compile()
        self.scope = copy.deepcopy(original)
        self.scope['checks'][1:] = reversed(self.scope['checks'][1:])
        with self.assertRaisesRegex(ValueError, 'forward'):
            self.compile()

    def test_missing_dependency_and_invalid_extractor_refuse_without_tools(self):
        del self.files['TJA1051.pdf']
        with self.assertRaisesRegex(ValueError, 'not captured'):
            self.compile()
        self.files['TJA1051.pdf'] = 'a' * 64
        self.scope['checks'][1]['params']['extractor']['sha256'] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'extractor requires'):
            self.compile()

    def test_fact_dependencies_recompile_and_cannot_be_native_design(self):
        baseline = self.compile()
        self.facts['facts'][0]['source']['path'] = 'project/pins.kicad_sch'
        write_json(self.inputs / 'facts.json', self.facts)
        self.files['facts.json'] = digest(self.inputs / 'facts.json')
        with self.assertRaisesRegex(ValueError, 'native design cannot'):
            self.compile()
        self.assertEqual(baseline['schema'], 'agent-ee.baseline/2')


class NativeDatasheetCoverageTests(unittest.TestCase):
    def test_scope3_control_and_seed_retain_executable_pin_coverage(self):
        require_toolchain(native=True, tools=('pdftotext',))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = ROOT / 'fixtures/datasheet-pins'
            for name, outcome in [('control', 'pass'), ('swapped', 'defect')]:
                with self.subTest(name=name):
                    scope = read_json(fixture / f'{name}.scope.json')
                    scope['schema'] = 'agent-ee.scope/3'
                    scope['baseline']['requirements'][0]['intent'] = 'acceptance'
                    for item in scope['inputs']:
                        item['path'] = str((fixture / item['path']).resolve())
                    write_json(root / 'scope.json', scope)
                    run = root / name
                    run.mkdir()
                    capture(root / 'scope.json', run)
                    checks = run_checks(run, time.monotonic() + 90)
                    by_id = {c['id']: c for c in checks}
                    self.assertEqual(by_id['pin-map']['outcome'], outcome, by_id)
                    self.assertTrue(all(c['execution'] == 'ok' for c in checks), checks)
                    for cid in ('erc', 'drc', 'facts'):
                        self.assertEqual(by_id[cid]['outcome'], 'pass', by_id[cid])
                    freeze_evidence(run, checks)
                    verify_evidence(run, checks, required=True)
