"""Severity policy, immutable dispositions and real sandbox boundary evidence."""
import copy
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.capabilities import KINDS, describe_check
from agent_ee.happy_severity_gate import KIND, PREDICATES, SCHEMA, dispositions, evaluate
from agent_ee.kicad_happy import SEVERITY_ANALYZERS, installation, verify_installations
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/kicad-happy/severity-gate'
INSTALLED = Path(os.environ.get('AGENT_EE_KICAD_HAPPY', str(Path.home() / 'git/kicad-happy')))


def spec(root=INSTALLED):
    return {'id': 'severity', 'kind': KIND, 'params': {'installation': str(root),
        'installation_sha256': installation(root)['sha256'],
        'dispositions': 'requirements/dispositions.json'}}


def reports():
    return {name: {'analyzer_type': name, 'schema_version': '1.4.0', 'findings': [],
                   'summary': {'total_findings': 0, 'by_severity': {'error': 0, 'warning': 0, 'info': 0}}}
            for name in SEVERITY_ANALYZERS}


def finding(analyzer, severity):
    return {'finding_id': f'{analyzer}:TEST-001:j1', 'rule_id': 'TEST-001',
            'severity': severity, 'components': ['J1']}


def entry(fid):
    return {'finding_id': fid, 'disposition': 'accepted_risk',
            'rationale': 'Synthetic policy test only', 'recorded_by': 'test author'}


class SeverityPolicyTests(unittest.TestCase):
    def test_every_analyzer_error_is_blocking_and_warning_needs_exact_id(self):
        for analyzer in SEVERITY_ANALYZERS:
            for severity in ('error', 'warning', 'info'):
                with self.subTest(analyzer=analyzer, severity=severity):
                    data = reports()
                    f = finding(analyzer, severity)
                    data[analyzer]['findings'] = [f]
                    data[analyzer]['summary']['total_findings'] = 1
                    data[analyzer]['summary']['by_severity'][severity] = 1
                    fid = f['finding_id']
                    empty = evaluate(data, {})
                    recorded = dispositions({'schema': SCHEMA, 'dispositions': [entry(fid)]})
                    present = evaluate(data, recorded)
                    self.assertEqual(empty['error_finding_ids'], [fid] if severity == 'error' else [])
                    self.assertEqual(present['error_finding_ids'], empty['error_finding_ids'])
                    self.assertEqual(empty['undispositioned_warning_ids'], [fid] if severity == 'warning' else [])
                    self.assertEqual(present['undispositioned_warning_ids'], [])
                    wrong = evaluate(data, dispositions({'schema': SCHEMA, 'dispositions': [entry(fid + '-other')]}))
                    self.assertEqual(wrong['undispositioned_warning_ids'], empty['undispositioned_warning_ids'])
        data = reports()
        data['schematic']['summary'] = {'total_findings': 1, 'by_severity': {'error': 0, 'warning': 0, 'info': 1}}
        data['schematic']['findings'] = [{'finding_id': 'schematic:detector-j1',
            'rule_id': 'DET', 'severity': 'info'}]
        evaluate(data, {})  # Both native two-part and three-part identities are valid.

    def test_assumptions_at_every_severity_require_exact_dispositions(self):
        for analyzer in SEVERITY_ANALYZERS:
            for severity in ('error', 'warning', 'info'):
                for metadata in ({'assumptions': ['unverified threshold']},
                                 {'source_path': 'assumed'}):
                    with self.subTest(analyzer=analyzer, severity=severity, metadata=metadata):
                        data = reports()
                        f = {**finding(analyzer, severity), **metadata}
                        fid = f['finding_id']
                        data[analyzer]['findings'] = [f]
                        data[analyzer]['summary']['total_findings'] = 1
                        data[analyzer]['summary']['by_severity'][severity] = 1
                        for ids in ([], [fid], [fid + '-other']):
                            recorded = dispositions({'schema': SCHEMA,
                                'dispositions': [entry(i) for i in ids]})
                            result = evaluate(data, recorded)
                            self.assertEqual(result['undispositioned_warning_ids'],
                                             [] if fid in ids else [fid])
                            self.assertEqual(result['error_finding_ids'],
                                             [fid] if severity == 'error' else [])
                            self.assertEqual(result['unused_disposition_ids'],
                                             [i for i in ids if i != fid])
                            self.assertEqual('disposition' in result['findings'][0], fid in ids)

    def test_plain_info_remains_unrestricted_and_its_disposition_unused(self):
        data = reports()
        f = {**finding('schematic', 'info'), 'assumptions': [], 'source_path': 'topology'}
        fid = f['finding_id']
        data['schematic']['findings'] = [f]
        data['schematic']['summary']['total_findings'] = 1
        data['schematic']['summary']['by_severity']['info'] = 1
        for ids in ([], [fid]):
            result = evaluate(data, dispositions({'schema': SCHEMA,
                'dispositions': [entry(i) for i in ids]}))
            self.assertEqual(result['error_finding_ids'], [])
            self.assertEqual(result['undispositioned_warning_ids'], [])
            self.assertEqual(result['unused_disposition_ids'], ids)
            self.assertNotIn('disposition', result['findings'][0])

    def test_unknown_output_dominates_known_errors(self):
        base = reports()
        base['schematic']['findings'] = [finding('schematic', 'error')]
        base['schematic']['summary'] = {'total_findings': 1, 'by_severity': {'error': 1, 'warning': 0, 'info': 0}}
        variants = []
        changed = copy.deepcopy(base); changed.pop('emc'); variants.append(changed)
        for mutate in (lambda r: r.update(schema_version='99'),
                       lambda r: r.update(analyzer_type='thermal'),
                       lambda r: r.update(status='unknown'),
                       lambda r: r.pop('summary'),
                       lambda r: r['summary'].update(total_findings=1),
                       lambda r: r['summary']['by_severity'].update(error=1),
                       lambda r: r['summary'].update(suppressed=1),
                       lambda r: r.update(stage_filter={}),
                       lambda r: r.update(findings=None),
                       lambda r: r.update(findings=[{'rule_id': 'X', 'severity': 'fatal'}]),
                       lambda r: r.update(findings=[{'rule_id': 'X', 'severity': 'warning'}]),
                       lambda r: r.update(findings=[finding('emc', 'warning')] * 2),
                       lambda r: r.update(findings=[{**finding('emc', 'warning'), 'suppressed': True}])):
            changed = copy.deepcopy(base); mutate(changed['emc']); variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(checks.CheckError) as error:
                evaluate(changed, {})
            self.assertEqual(error.exception.execution, 'unsupported')

    def test_dispositions_fail_closed_on_duplicates_missing_rationale_and_unknown_schema(self):
        good = {'schema': SCHEMA, 'dispositions': [entry('schematic:DS-002:j1')]}
        for mutate in (lambda r: r.update(schema='unknown'),
                       lambda r: r['dispositions'].append(copy.deepcopy(r['dispositions'][0])),
                       lambda r: r['dispositions'][0].update(rationale=' '),
                       lambda r: r['dispositions'][0].update(recorded_by=''),
                       lambda r: r['dispositions'][0].update(disposition='fixed'),
                       lambda r: r.update(dispositions={})):
            changed = copy.deepcopy(good); mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                dispositions(changed)

    def test_registration_and_static_coverage_require_captured_immutable_dispositions(self):
        check = {'id': 'severity', 'kind': KIND, 'params': {'installation': '/installed',
            'installation_sha256': 'a' * 64, 'dispositions': 'dispositions.json'}}
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'gate.kicad_sch', 'board': 'gate.kicad_pcb',
                 'requirements': ['dispositions.json']}
        files = dict.fromkeys(['gate.kicad_sch', 'gate.kicad_pcb', 'dispositions.json'], 'b' * 64)
        def describe():
            return describe_check(check, check_specs=[check], files=files, scope=scope)
        self.assertIn(KIND, KINDS)
        self.assertIn(KIND, checks.CHECK_FUNCS)
        result = describe()
        self.assertEqual(result['status'], 'supported', result)
        self.assertEqual(result['predicates'], PREDICATES)
        self.assertEqual(result['immutable_inputs'], ['dispositions.json'])
        for path in list(files):
            saved = files.pop(path)
            self.assertEqual(describe()['status'], 'unsupported')
            files[path] = saved
        scope['mutable_inputs'] = ['dispositions.json']
        self.assertEqual(describe()['status'], 'unsupported')
        scope.pop('mutable_inputs')
        scope['requirements'] = []
        self.assertEqual(describe()['status'], 'unsupported')


class NativeSeverityGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        if not (INSTALLED / '.claude-plugin/plugin.json').is_file():
            raise AssertionError('pinned kicad-happy installation unavailable')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-severity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_check(self, variant='warning', record=None, installed=INSTALLED, schema='agent-ee.scope/3'):
        if record is None:
            record = read_json(FIXTURE / 'dispositions.json')
        source = self.root / f'dispositions-{time.monotonic_ns()}.json'
        write_json(source, record)
        scope = {'schema': schema, 'name': 'Original severity fixture', 'scope': 'Severity policy only',
            'limitations': ['No electrical or EMC certification.'],
            'inputs': [{'path': str(FIXTURE / variant), 'at': 'project'},
                {'path': str(FIXTURE / 'EXPECTATIONS.md'), 'at': 'requirements/expectations.md'},
                {'path': str(source), 'at': 'requirements/dispositions.json'}],
            'schematic': 'project/gate.kicad_sch', 'board': 'project/gate.kicad_pcb',
            'requirements': ['requirements/expectations.md', 'requirements/dispositions.json'],
            'mutable_inputs': ['project/gate.kicad_sch', 'project/gate.kicad_pcb'],
            'checks': [spec(installed)], 'baseline': {
                'requirements': [{'id': 'R-severity', 'statement': 'No errors and every warning dispositioned',
                    'source': {'path': 'requirements/expectations.md', 'locator': 'Severity policy fixtures'},
                    'checks': ['severity']}],
                'targets': ['requirements/expectations.md'],
                'reference_architectures': ['requirements/expectations.md']}}
        if schema == 'agent-ee.scope/3':
            scope['baseline']['requirements'][0]['intent'] = 'acceptance'
        path = self.root / 'scope.json'; write_json(path, scope)
        run = self.root / f'run-{time.monotonic_ns()}'; run.mkdir()
        candidate.capture(path, run)
        result = checks.run_checks(run, time.monotonic() + 600)[0]
        return run, result

    def test_native_warning_pass_error_defect_and_missing_warning_witness(self):
        run, result = self.run_check()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        normalized = result['details']['normalized']
        self.assertEqual(normalized['error_finding_ids'], [])
        self.assertEqual(normalized['undispositioned_warning_ids'], [])
        self.assertEqual({f['finding_id'] for f in normalized['findings'] if f['severity'] == 'warning'},
                         {'schematic:DS-002:j1', 'emc:VS-001:85855106fcd4'})
        for name, script in SEVERITY_ANALYZERS.items():
            self.assertIn(script, result['details']['executed_files'])
            self.assertTrue((run / f'work/severity/{name}.json').is_file())
            argv = read_json(run / f'work/severity/{name}-argv.json')
            self.assertNotIn('--spice-enhanced', argv)
            self.assertNotIn('--lifecycle', argv)
        self.assertFalse((run / 'work/severity/thermal.json').exists())
        self.assertEqual((run / 'work/severity/dispositions.json').read_bytes(),
                         (run / 'inputs/requirements/dispositions.json').read_bytes())
        for f in normalized['findings']:
            if 'disposition' in f:
                self.assertEqual(f['disposition']['raw_output_path'], 'dispositions.json')
                self.assertTrue(f['disposition']['raw_locator'].startswith('/dispositions/'))
        freeze_evidence(run, [result]); verify_evidence(run, [result], required=True)
        pin = read_json(run / 'baseline.json')
        self.assertIn('requirements/dispositions.json', pin['files'])
        self.assertEqual(pin['coverage']['checks']['severity']['predicates'], PREDICATES)
        verify_installations([result])
        scope = read_json(self.root / 'scope.json')
        scope['mutable_inputs'].append('requirements/dispositions.json')
        write_json(self.root / 'mutable.scope.json', scope)
        rejected = self.root / 'rejected'; rejected.mkdir()
        with self.assertRaisesRegex(ValueError, 'immutable|mutable'):
            candidate.capture(self.root / 'mutable.scope.json', rejected)
        for record in ({'schema': SCHEMA, 'dispositions': []},
                       {'schema': SCHEMA, 'dispositions': read_json(FIXTURE / 'dispositions.json')['dispositions'][:1]}):
            failed_run, failed = self.run_check(record=record, schema='agent-ee.scope/2')
            self.assertEqual((failed['execution'], failed['outcome']), ('ok', 'defect'), failed)
            self.assertTrue(failed['details']['normalized']['undispositioned_warning_ids'])
            self.assertNotEqual(read_json(failed_run / 'baseline.json')['baseline_id'], pin['baseline_id'])
            freeze_evidence(failed_run, [failed])
            validate_witnesses(failed_run, [{'requirement_id': 'R-severity', 'check_id': 'severity'}],
                               [{'path': 'checks/severity.json', 'locator': 'check:severity'}])
        # Even an entry naming an error never waives it.
        record = read_json(FIXTURE / 'dispositions.json')
        record['dispositions'].append(entry('emc:GP-002:6ad3eb0974a3'))
        _, failed = self.run_check('error', record=record)
        self.assertEqual((failed['execution'], failed['outcome']), ('ok', 'defect'), failed)
        self.assertIn('emc:GP-002:6ad3eb0974a3', failed['details']['normalized']['error_finding_ids'])
        self.assertEqual(failed['details']['normalized']['undispositioned_warning_ids'], [])
        with (run / 'work/severity/dispositions.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'seal|contradicts'):
            verify_evidence(run, [result], required=True)

    def test_native_crash_missing_invalid_json_schema_and_missing_id_stay_unknown(self):
        for body, status in [('raise RuntimeError("test boundary crash")', 'failed'),
                ('pass', 'malformed'),
                ('from pathlib import Path; Path("/work/cross_analysis.json").write_text("{")', 'malformed'),
                ('from pathlib import Path; Path("/work/cross_analysis.json").write_text(\'{"schema_version":"1.4.0","analyzer_type":"cross_analysis","findings":[{"rule_id":"TEST-001","severity":"error","severity":"info","finding_id":"cross_analysis:TEST-001:j1"}]}\')', 'malformed'),
                ('import json; json.dump({"schema_version":"99"}, open("/work/cross_analysis.json","w"))', 'unsupported'),
                ('import json; json.dump({"schema_version":"1.4.0", "analyzer_type":"cross_analysis", '
                 '"findings":[{"rule_id":"TEST-001","severity":"warning"}]}, '
                 'open("/work/cross_analysis.json","w"))', 'unsupported')]:
            with self.subTest(status=status, body=body):
                copied = self.root / f'installed-{time.monotonic_ns()}'
                shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
                (copied / SEVERITY_ANALYZERS['cross_analysis']).write_text(body)
                _, result = self.run_check(installed=copied)
                self.assertEqual((result['execution'], result['outcome']), (status, 'unknown'), result)

    def test_public_verification_detects_installation_and_disposition_drift(self):
        from agent_ee.cli import run_review, verify
        copied = self.root / 'installed'
        shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        _, result = self.run_check(installed=copied)
        reviewed = self.root / 'reviewed'
        run_review(self.root / 'scope.json', reviewed, 600, checks_only=True)
        verify(reviewed)
        helper = copied / 'skills/kicad/scripts/sexp_parser.py'
        original = helper.read_bytes()
        helper.write_bytes(original + b'\n# test-only installation drift\n')
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            verify_installations([result])
        scope = read_json(self.root / 'scope.json')
        scope['mutable_inputs'].append('requirements/dispositions.json')
        write_json(self.root / 'mutable.scope.json', scope)
        rejected = self.root / 'rejected'; rejected.mkdir()
        with self.assertRaisesRegex(ValueError, 'immutable|mutable'):
            candidate.capture(self.root / 'mutable.scope.json', rejected)
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            verify(reviewed)
        helper.write_bytes(original)
        with (reviewed / 'work/severity/dispositions.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'changed|seal|stale'):
            verify(reviewed)
