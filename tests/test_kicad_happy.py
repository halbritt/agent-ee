"""Installed analyzer normalization: absence is never a clean evaluation."""
import copy
import unittest

from agent_ee.kicad_happy import normalize
from agent_ee.checks import CheckError


class NormalizationTest(unittest.TestCase):
    def test_rc_calculation_is_evaluated_against_declared_bound(self):
        report = {'analyzer_type': 'schematic', 'schema_version': '1.4.0',
                  'findings': [{'rule_id': 'RC-DET', 'severity': 'info',
                    'components': ['C1', 'R1'], 'nets': [], 'pins': [],
                    'cutoff_hz': 15.92, 'type': 'low-pass'}],
                  'components': [{'reference': ref, 'value': 'native', 'lib_id': 'Device',
                                  'dnp': False, 'on_board': True} for ref in ('C1', 'R1')]}
        rules = [{'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                  'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150, 'max': 170}]
        findings, evaluations = normalize({'schematic': report}, rules)
        self.assertEqual(evaluations[0]['status'], 'violation')
        self.assertEqual(evaluations[0]['value'], 15.92)
        self.assertEqual(findings[0]['native_locator']['components'], ['C1', 'R1'])
        self.assertEqual(findings[0]['raw_output_path'], 'schematic.json')

    def test_rc_requires_each_explicit_eligible_native_target(self):
        report = {'analyzer_type': 'schematic', 'schema_version': '1.4.0',
                  'findings': [{'rule_id': 'RC-DET', 'severity': 'info',
                    'components': ['C1', 'R1'], 'type': 'low-pass', 'cutoff_hz': 159.15}],
                  'components': [{'reference': ref, 'value': value, 'lib_id': lib,
                                  'dnp': False, 'on_board': True, 'in_bom': True}
                                 for ref, value, lib in [('R1', '10k', 'Device:R'),
                                                        ('C1', '100n', 'Device:C')]]}
        rule = {'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150, 'max': 170}
        self.assertEqual(normalize({'schematic': report}, [rule])[1][0]['status'], 'satisfied')
        for targets in (['R1'], ['C1'], ['R1', 'C1']):
            for mutation in ('dnp', 'off-board', 'missing-dnp', 'missing-on-board',
                             'malformed-dnp', 'malformed-on-board', 'duplicate',
                             'missing', 'suppressed', 'malformed-identity', 'bom-only'):
                changed = copy.deepcopy(report)
                for ref in targets:
                    component = next(c for c in changed['components'] if c['reference'] == ref)
                    if mutation == 'dnp': component['dnp'] = True
                    if mutation == 'off-board': component['on_board'] = False
                    if mutation == 'missing-dnp': component.pop('dnp')
                    if mutation == 'missing-on-board': component.pop('on_board')
                    if mutation == 'malformed-dnp': component['dnp'] = 0
                    if mutation == 'malformed-on-board': component['on_board'] = 1
                    if mutation == 'duplicate': changed['components'].append(copy.deepcopy(component))
                    if mutation == 'missing': changed['components'].remove(component)
                    if mutation == 'suppressed': component['suppressed'] = True
                    if mutation == 'malformed-identity': component['lib_id'] = None
                    if mutation == 'bom-only': component['in_bom'] = False
                with self.subTest(targets=targets, mutation=mutation):
                    evaluation = normalize({'schematic': changed}, [rule])[1][0]
                    self.assertEqual(evaluation['status'],
                                     'satisfied' if mutation == 'bom-only' else 'not_evaluated')
        for inventory in (None, {}, [None], [report['components'][0], 1]):
            with self.subTest(inventory=inventory):
                changed = {**report, 'components': inventory}
                self.assertEqual(normalize({'schematic': changed}, [rule])[1][0]['status'], 'not_evaluated')
        changed = copy.deepcopy(report)
        changed.pop('components')
        self.assertEqual(normalize({'schematic': changed}, [rule])[1][0]['status'], 'not_evaluated')

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
                    'schema_version': '1.4.0', 'findings': entries,
                    'components': [{'reference': ref, 'value': 'native', 'lib_id': 'Device',
                                    'dnp': False, 'on_board': True} for ref in ('C1', 'R1')]}}, [rule])
                self.assertEqual(evaluations[0]['status'], 'not_evaluated')

    def test_nonadapter_declarations_reject_adapter_only_fields(self):
        from agent_ee.kicad_happy import declarations, describe_capability
        rule = {'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150}
        params = {'rules': [rule], 'counted_rules': ['cutoff'], 'blocking_rules': ['cutoff'],
                  'installation': '/installed', 'installation_sha256': 'a' * 64}
        self.assertEqual(declarations(params)[0], [rule])
        for fields in ({'assumptions': {}}, {'assumptions_source': {}},
                       {'assumptions': 'unused', 'assumptions_source': 123}):
            with self.subTest(fields=fields):
                changed = {**params, 'rules': [{**rule, **fields}]}
                with self.assertRaisesRegex(ValueError, 'adapter'):
                    declarations(changed)
                self.assertEqual(describe_capability(changed, {})['status'], 'unsupported')

    def test_unknown_output_format_is_unsupported(self):
        for version in ('99', '1.3.0', '1.5.0', None):
            with self.subTest(version=version), self.assertRaises(CheckError) as error:
                normalize({'schematic': {'analyzer_type': 'schematic', 'schema_version': version,
                                         'findings': []}}, [])
            self.assertEqual(error.exception.execution, 'unsupported')
        normalize({'schematic': {'analyzer_type': 'schematic', 'schema_version': '1.4.0', 'findings': []}}, [])


import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from agent_ee import candidate, checks
from agent_ee.kicad_happy import (INSTALL_RECORD_SCHEMA, installation, installation_record,
                                  require_release_line, verify_recorded, verify_unchanged)
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
INSTALLED = Path(os.environ.get('AGENT_EE_KICAD_HAPPY', str(Path.home() / 'git/kicad-happy')))
COMMIT_2_3_1 = '06840467ad0f5d76af45f64449b1f5ecb10f052f'
COMMIT_2_3_2 = '2fd9aa0fe2d620f36627d9221e8b1393987ae38e'


def specification(root):
    return {'id': 'happy', 'kind': 'kicad_happy', 'params': {
        'installation': str(root),
        'rules': [{'id': 'cutoff', 'analyzer': 'schematic', 'rule_id': 'RC-DET',
                  'components': ['R1', 'C1'], 'field': 'cutoff_hz', 'min': 150, 'max': 170}],
        'counted_rules': ['cutoff'], 'blocking_rules': ['cutoff']}}


class RecordedInstallationTest(unittest.TestCase):
    """The installed release is used and recorded; no fixed version or digest is compared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-happy-record-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def install(self, name, version, **record):
        """A minimal installed tree; with record fields, also the image's install record."""
        root = self.root / name / 'kicad-happy'
        write_json(root / '.claude-plugin/plugin.json', {'name': 'kicad-happy', 'version': version})
        (root / 'skills').mkdir()
        (root / 'skills/analyze.py').write_text('# installed analyzer bytes\n')
        if record:
            write_json(root.with_name('kicad-happy.install.json'), {
                'schema': INSTALL_RECORD_SCHEMA, 'version': version, 'output_format': '1.4.0',
                'installation_sha256': installation(root)['sha256'], **record})
        return root

    def recorded_run(self, name):
        """One finished check's saved evidence; its installation was the 2.3.1 image's."""
        root = self.install(name, '2.3.1', tag='v2.3.1', commit=COMMIT_2_3_1)
        record = installation_record(root)
        executed = {'skills/analyze.py': record['files']['skills/analyze.py']}
        run = self.root / name / 'run'
        write_json(run / 'work/happy/installation.json', record)
        write_json(run / 'work/happy/executed.json', executed)
        check = {'id': 'happy', 'kind': 'kicad_happy',
                 'evidence': ['work/happy/installation.json', 'work/happy/executed.json'],
                 'details': {'installation': record, 'executed_files': executed}}
        return root, run, check

    def test_release_other_than_the_previous_one_is_accepted_and_recorded(self):
        # 2.3.2 as the image installs it, with its install record; 2.9.0 as a plain checkout.
        for version, record, provenance in (
                ('2.3.2', {'tag': 'v2.3.2', 'commit': COMMIT_2_3_2}, ('v2.3.2', COMMIT_2_3_2)),
                ('2.9.0', {}, (None, None))):
            with self.subTest(version=version):
                root = self.install(version, version, **record)
                require_release_line(version)
                recorded = installation_record(root)
                self.assertEqual((recorded['version'], recorded['tag'], recorded['commit']),
                                 (version, *provenance))
                self.assertEqual(recorded['sha256'], installation(root)['sha256'])
                self.assertEqual(recorded['root'], str(root))

    def test_release_outside_the_contract_line_is_unsupported(self):
        for version in ('3.0.0', '1.3.2', '2.3', 'main'):
            with self.subTest(version=version), self.assertRaises(CheckError) as error:
                require_release_line(version)
            self.assertEqual(error.exception.execution, 'unsupported')

    def test_install_record_that_describes_other_files_is_refused(self):
        root = self.install('edited', '2.3.2', tag='v2.3.2', commit=COMMIT_2_3_2)
        (root / 'skills/analyze.py').write_text('# changed after the image build\n')
        with self.assertRaisesRegex(ValueError, 'does not describe the installed files'):
            installation_record(root)

    def test_old_run_verifies_from_its_saved_evidence_not_this_host(self):
        root, run, check = self.recorded_run('updated')
        # This host has since moved to a newer release, then removed the installation.
        write_json(root / '.claude-plugin/plugin.json', {'name': 'kicad-happy', 'version': '2.3.2'})
        verify_recorded(run, [check])
        shutil.rmtree(root)
        verify_recorded(run, [check])

    def test_saved_evidence_that_contradicts_the_record_is_refused(self):
        def edit_saved(run, check):
            saved = read_json(run / 'work/happy/installation.json')
            write_json(run / 'work/happy/installation.json', {**saved, 'commit': COMMIT_2_3_2})

        def forge_files(run, check):
            check['details']['installation']['files']['skills/analyze.py'] = '0' * 64
            write_json(run / 'work/happy/installation.json', check['details']['installation'])

        mutations = {
            'saved record edited': edit_saved,
            'saved record not cited': lambda run, check: check['evidence'].remove('work/happy/installation.json'),
            'saved record deleted': lambda run, check: (run / 'work/happy/installation.json').unlink(),
            'files without their identity': forge_files,
            'helper outside the installation': lambda run, check: (
                check['details']['executed_files'].update({'skills/other.py': '0' * 64}),
                write_json(run / 'work/happy/executed.json', check['details']['executed_files'])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                _, run, check = self.recorded_run(name.replace(' ', '-'))
                mutate(run, check)
                with self.assertRaisesRegex(ValueError, 'kicad-happy'):
                    verify_recorded(run, [check])

    def test_installation_change_during_the_run_is_refused(self):
        root, _, check = self.recorded_run('during')
        verify_unchanged([check])
        (root / 'skills/analyze.py').write_text('# changed during the run\n')
        with self.assertRaisesRegex(ValueError, 'bytes changed during the run'):
            verify_unchanged([check])


class InstalledAnalyzerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        if not (INSTALLED / '.claude-plugin/plugin.json').is_file():
            if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                raise AssertionError('installed kicad-happy unavailable')
            raise unittest.SkipTest('installed kicad-happy unavailable')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-happy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_check(self, spec, fixture='control', scope2=False, project=None):
        scope = json.loads((REPO / 'fixtures/rc' / f'{fixture}.scope.json').read_text())
        # Resolve fixture-relative input paths before writing the disposable scope.
        for item in scope['inputs']:
            item['path'] = str((REPO / 'fixtures/rc' / item['path']).resolve())
        if project is not None:
            scope['inputs'][0]['path'] = str(project)
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
        # The run records the installed version, and in the image the build's tag and commit.
        recorded = control['details']['installation']
        self.assertEqual(recorded, installation_record(INSTALLED))
        self.assertEqual(read_json(control_run / 'work/happy/installation.json'), recorded)
        verify_recorded(control_run, [control])

    def test_exact_native_rc_target_population_flags(self):
        from agent_ee.benchmark import parse, children, values, one, replace_spans, span
        for targets in (('R1',), ('C1',), ('R1', 'C1')):
            for field, flag in (('dnp', 'yes'), ('on_board', 'no'), ('in_bom', 'no')):
                with self.subTest(targets=targets, field=field):
                    project = self.root / ('-'.join(targets) + '-' + field)
                    shutil.copytree(REPO / 'fixtures/rc/control', project)
                    schematic = project / 'rc_filter.kicad_sch'
                    source = schematic.read_text()
                    edits = []
                    for symbol in children(parse(source), 'symbol'):
                        props = {values(p)[0]: values(p)[1] for p in children(symbol, 'property')}
                        if props.get('Reference') in targets:
                            edits.append(span(one(symbol, field), f'({field} {flag})'))
                    self.assertEqual(len(edits), len(targets))
                    changed = replace_spans(source, edits)
                    self.assertNotEqual(source, changed)
                    observed = {}
                    for symbol in children(parse(changed), 'symbol'):
                        props = {values(p)[0]: values(p)[1] for p in children(symbol, 'property')}
                        if props.get('Reference') in targets:
                            observed[props['Reference']] = values(one(symbol, field))[0]
                    self.assertEqual(observed, dict.fromkeys(targets, flag))
                    schematic.write_text(changed)
                    run, result = self.run_check(specification(INSTALLED), project=project)
                    expected = ('ok', 'pass') if field == 'in_bom' else ('unsupported', 'unknown')
                    self.assertEqual((result['execution'], result['outcome']), expected, result)
                    evaluation = result['details']['normalized']['evaluations'][0]
                    self.assertEqual(evaluation['status'],
                                     'satisfied' if field == 'in_bom' else 'not_evaluated')
                    report = json.loads((run / 'work/happy/schematic.json').read_text())
                    for ref in targets:
                        component = next(c for c in report['components'] if c['reference'] == ref)
                        self.assertIs(component[field], flag == 'yes')
                    if field == 'in_bom':
                        sources = evaluation['native_sources']
                        self.assertEqual(sources[0]['raw_locator'], evaluation['raw_locator'])
                        for ref in ('R1', 'C1'):
                            index = next(i for i,c in enumerate(report['components']) if c['reference'] == ref)
                            self.assertIn({'raw_output_path': 'schematic.json',
                                           'raw_locator': f'/components/{index}'}, sources)

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

    def test_old_run_verifies_after_the_installation_changes(self):
        copied = self.root / 'installation'
        shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        run, result = self.run_check(specification(copied))
        self.assertEqual(result['execution'], 'ok', result)
        from agent_ee.run_record import run_review, verify
        reviewed = self.root / 'reviewed'
        run_review(self.root / 'scope.json', reviewed, 600, checks_only=True)
        verify(reviewed)
        helper = copied / 'skills/kicad/scripts/sexp_parser.py'
        helper.write_bytes(helper.read_bytes() + b'\n# a later installation\n')
        # Finished runs keep their own record; only a run in progress refuses the change.
        verify(reviewed)
        verify_recorded(run, [result])
        with self.assertRaisesRegex(ValueError, 'bytes changed during the run'):
            verify_unchanged([result])
        with (reviewed / 'work/happy/installation.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'saved evidence|evidence changed'):
            verify(reviewed)

    def test_release_of_the_line_runs_and_is_recorded_and_another_major_is_unsupported(self):
        for version, expected in (('2.99.0', ('ok', 'pass')), ('3.0.0', ('unsupported', 'unknown'))):
            with self.subTest(version=version):
                copied = self.root / ('release-' + version)
                shutil.copytree(INSTALLED, copied, ignore=shutil.ignore_patterns('.git', '__pycache__'))
                metadata = read_json(copied / '.claude-plugin/plugin.json')
                write_json(copied / '.claude-plugin/plugin.json', {**metadata, 'version': version})
                _, result = self.run_check(specification(copied))
                self.assertEqual((result['execution'], result['outcome']), expected, result)
                self.assertEqual(result['details']['installation']['version'], version)

    def test_legacy_declared_digest_is_recorded_not_required(self):
        spec = specification(INSTALLED)
        spec['params']['installation_sha256'] = '0' * 64
        _, result = self.run_check(spec)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['declared_installation_sha256'], '0' * 64)
        self.assertTrue(any('retired declaration' in line for line in result['limitations']), result)

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
