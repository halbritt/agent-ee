"""Installed analyzer normalization: absence is never a clean evaluation."""
import unittest

from agent_ee.kicad_happy import normalize
from agent_ee.checks import CheckError


class NormalizationTest(unittest.TestCase):
    def test_rc_calculation_is_evaluated_against_declared_bound(self):
        report = {'analyzer_type': 'schematic', 'schema_version': '1.4.0',
                  'findings': [{'rule_id': 'RC-DET', 'severity': 'info',
                    'components': ['C1', 'R1'], 'nets': [], 'pins': [],
                    'cutoff_hz': 15.92, 'type': 'low-pass'}]}
        rules = [{'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                  'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150, 'max': 170}]
        findings, evaluations = normalize({'schematic': report}, rules)
        self.assertEqual(evaluations[0]['status'], 'violation')
        self.assertEqual(evaluations[0]['value'], 15.92)
        self.assertEqual(findings[0]['native_locator']['components'], ['C1', 'R1'])
        self.assertEqual(findings[0]['raw_output_path'], 'schematic.json')

    def test_absent_blocking_evaluation_is_not_evaluated(self):
        report = {'analyzer_type': 'schematic', 'schema_version': '1.4.0', 'findings': []}
        _, evaluations = normalize({'schematic': report}, [{'id': 'absent',
            'analyzer': 'schematic', 'rule_id': 'VD-001', 'components': ['C1'],
            'field': 'voltage', 'max': 5}])
        self.assertEqual(evaluations, [{'id': 'absent', 'status': 'not_evaluated'}])

    def test_ambiguous_suppressed_and_nonfinite_calculations_are_not_evaluated(self):
        rule = {'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                'components': ['C1', 'R1'], 'field': 'cutoff_hz', 'min': 150}
        base = {'rule_id': 'RC-DET', 'severity': 'info', 'components': ['C1', 'R1'],
                'type': 'low-pass', 'cutoff_hz': 159.15}
        for entries in ([base, base], [{**base, 'suppressed': True}],
                        [{**base, 'cutoff_hz': float('nan')}], [{**base, 'type': 'high-pass'}]):
            with self.subTest(entries=entries):
                _, evaluations = normalize({'schematic': {'analyzer_type': 'schematic',
                    'schema_version': '1.4.0', 'findings': entries}}, [rule])
                self.assertEqual(evaluations[0]['status'], 'not_evaluated')

    def test_unknown_schema_is_unsupported(self):
        with self.assertRaises(CheckError) as error:
            normalize({'schematic': {'schema_version': '99', 'findings': []}}, [])
        self.assertEqual(error.exception.execution, 'unsupported')


import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from agent_ee import candidate, checks
from agent_ee.kicad_happy import installation, verify_installations
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
INSTALLED = Path(os.environ.get('AGENT_EE_KICAD_HAPPY', str(Path.home() / 'git/kicad-happy')))


def specification(root):
    return {'id': 'happy', 'kind': 'kicad_happy', 'params': {
        'installation': str(root), 'installation_sha256': installation(root)['sha256'],
        'rules': [{'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                  'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150, 'max': 170}],
        'counted_rules': ['cutoff'], 'blocking_rules': ['cutoff']}}


class InstalledAnalyzerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        if not (INSTALLED / '.claude-plugin/plugin.json').is_file():
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('installed kicad-happy 2.3.1 unavailable')
            raise unittest.SkipTest('installed kicad-happy 2.3.1 unavailable')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-happy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_check(self, spec, fixture='control', scope2=False):
        scope = json.loads((REPO / 'fixtures/rc' / f'{fixture}.scope.json').read_text())
        # Resolve fixture-relative input paths before writing the disposable scope.
        for item in scope['inputs']:
            item['path'] = str((REPO / 'fixtures/rc' / item['path']).resolve())
        scope['checks'] = [spec]
        if scope2:
            scope['schema'] = 'agent-ee.scope/2'
            scope['baseline'] = {'requirements': [{'id': 'R-cutoff',
                'statement': 'Low-pass cutoff is in [150,170] Hz',
                'source': {'path': 'requirements/fixture-requirements.md', 'locator': 'RC'},
                'checks': ['happy']}], 'targets': ['requirements/fixture-requirements.md'],
                'reference_architectures': ['requirements/fixture-requirements.md']}
        path = self.root / 'scope.json'
        path.write_text(json.dumps(scope))
        run = self.root / ('run-' + str(time.monotonic_ns()))
        run.mkdir()
        candidate.capture(path, run)
        result = checks.run_checks(run, time.monotonic() + 600)[0]
        return run, result

    def test_native_control_and_independent_seed_with_sealed_witness(self):
        spec = specification(INSTALLED)
        control_run, control = self.run_check(spec)
        self.assertEqual((control['execution'], control['outcome']), ('ok', 'pass'), control)
        run, seed = self.run_check(spec, 'seeded', scope2=True)
        self.assertEqual((seed['execution'], seed['outcome']), ('ok', 'defect'), seed)
        # Independent oracle: 1/(2*pi*100000*100e-9) = 15.915494 Hz.
        self.assertAlmostEqual(seed['details']['normalized']['evaluations'][0]['value'], 15.915494, delta=.01)
        self.assertEqual(seed['location'], 'C1,R1')
        self.assertIn('skills/kicad/scripts/analyze_schematic.py', seed['details']['executed_files'])
        self.assertTrue(any('sexp_parser' in f for f in seed['details']['executed_files']))
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        freeze_evidence(run, [seed])
        evidence = [{'path': 'checks/happy.json', 'locator': 'check:happy'}]
        validate_witnesses(run, [{'requirement_id': 'R-cutoff', 'check_id': 'happy'}], evidence)
        with (run / 'work/happy/schematic.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'changed|seal'):
            validate_witnesses(run, [{'requirement_id': 'R-cutoff', 'check_id': 'happy'}], evidence)
        for name in ('schematic', 'pcb', 'thermal', 'emc'):
            report = json.loads((control_run / f'work/happy/{name}.json').read_text())
            self.assertEqual(report['schema_version'], '1.4.0')
        self.assertEqual(json.loads((control_run / 'work/happy/thermal.json').read_text())['summary']['components_assessed'], 0)
        verify_installations([control])

    def test_missing_installation_is_unavailable(self):
        spec = specification(INSTALLED)
        spec['params']['installation'] = str(self.root / 'absent')
        _, result = self.run_check(spec)
        self.assertEqual((result['execution'], result['outcome']), ('unavailable', 'unknown'))

    def test_declared_absent_blocking_rule_is_unsupported(self):
        spec = specification(INSTALLED)
        spec['params']['rules'][0]['rule_id'] = 'VD-001'
        _, result = self.run_check(spec)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_changed_helper_bytes_are_rejected_by_verify(self):
        copied = self.root / 'installation'
        shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        run, result = self.run_check(specification(copied))
        self.assertEqual(result['execution'], 'ok', result)
        verify_installations([result])
        from agent_ee.cli import run_review, verify
        reviewed = self.root / 'reviewed'
        run_review(self.root / 'scope.json', reviewed, 600, checks_only=True)
        verify(reviewed)
        helper = copied / 'skills/kicad/scripts/sexp_parser.py'
        helper.write_bytes(helper.read_bytes() + b'\n# changed bytes\n')
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            verify_installations([result])
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            verify(reviewed)

    def test_crash_invalid_json_and_unrecognized_schema_have_distinct_status(self):
        # Boundary injectors live only in disposable test installations.
        for source, expected in [('raise RuntimeError("injected crash")', 'failed'),
                ('from pathlib import Path; Path("/work/schematic.json").write_text("{")', 'malformed'),
                ('import json; json.dump({"schema_version":"99"}, open("/work/schematic.json","w"))', 'unsupported')]:
            with self.subTest(expected=expected):
                copied = self.root / expected
                shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
                (copied / 'skills/kicad/scripts/analyze_schematic.py').write_text(source)
                _, result = self.run_check(specification(copied))
                self.assertEqual((result['execution'], result['outcome']), (expected, 'unknown'), result)
