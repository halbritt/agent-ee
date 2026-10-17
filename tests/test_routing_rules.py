"""Owned rule compiler examples; native fixtures are generated, never imported."""
import unittest
from agent_ee.routing_rules import compile_routing


def contract(kind='skew', **limits):
    return {'schema': 'agent-ee.routing-rules/1', 'constraints': [
        {'id': 'usb', 'requirement': 'usb-skew', 'kind': kind,
         'nets': ['D+', 'D-'], **limits}]}


class RoutingCompilerTest(unittest.TestCase):
    def test_compiled_expectations_and_native_types_own_their_state(self):
        doc = contract(max_mm=0.15)
        compiled = compile_routing(doc)
        doc['constraints'][0]['max_mm'] = 20
        self.assertEqual(compiled['constraints'][0]['expected']['max_mm'], 0.15)
        compiled['constraints'][0]['native_types'].append('unexpected')
        self.assertEqual(compile_routing(contract(max_mm=0.15))['constraints'][0]['native_types'],
                         ['skew_out_of_range'])

    def test_skew_is_deterministic_and_unit_pinned(self):
        compiled = compile_routing(contract(max_mm=0.15))
        self.assertEqual(compiled, compile_routing(contract(max_mm=0.15)))
        self.assertIn('(constraint skew (max 0.15mm))', compiled['constraints'][0]['rules'])
        self.assertEqual(compiled['constraints'][0]['requirement'], 'usb-skew')
        self.assertEqual(len(compiled['constraints'][0]['rules_sha256']), 64)

    def test_target_length_needs_tolerance_and_rejects_unknown_units(self):
        with self.assertRaises(ValueError):
            compile_routing(contract('length', target_mm=10))
        with self.assertRaises(ValueError):
            compile_routing(contract(max_mil=6))
        compiled = compile_routing(contract('length', target_mm=10, tolerance_mm=0.5))
        self.assertIn('(max 10.5mm) (min 9.5mm)', compiled['constraints'][0]['rules'])

    def test_impedance_is_unverified_even_if_solver_is_named(self):
        compiled = compile_routing(contract('impedance', target_ohm=90, stackup='owned.json', solver='named'))
        self.assertEqual(compiled['constraints'][0]['execution'], 'unsupported')
        self.assertNotIn('rules', compiled['constraints'][0])

    def test_rejects_rule_injection_nan_and_fractional_via_limit(self):
        for bad in (float('nan'), float('inf'), True, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compile_routing(contract(max_mm=bad))
        doc = contract(max_mm=0.15)
        doc['constraints'][0]['nets'] = ["D' || true"]
        with self.assertRaises(ValueError):
            compile_routing(doc)
        with self.assertRaises(ValueError):
            compile_routing(contract('via_count', max_count=2.5))

    def test_compilation_owns_its_expected_declaration(self):
        doc = contract(max_mm=0.15)
        compiled = compile_routing(doc)
        doc['constraints'][0]['max_mm'] = 10
        self.assertEqual(compiled['constraints'][0]['expected']['max_mm'], 0.15)


class NativeRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def run_rules(self, document, *, custom_rules=None, project=None, **board_options):
        import json
        import tempfile
        import time
        from pathlib import Path
        from routing_fixture import make_board, manifest_for_rules, assert_sealed_native
        from agent_ee.checks import Ctx
        from agent_ee.routing_rules import check_routing_rules
        from agent_ee.storage import digest
        root = Path(tempfile.mkdtemp(prefix='agent-ee-routing-native-'))
        self.addCleanup(__import__('shutil').rmtree, root)
        inputs = root / 'inputs'; inputs.mkdir()
        make_board(inputs / 'test.kicad_pcb', **board_options)
        if project is not None:
            (inputs / 'test.kicad_pro').write_text(json.dumps(project))
        if custom_rules is not None:
            (inputs / 'test.kicad_dru').write_text(custom_rules)
        (inputs / 'rules.json').write_text(json.dumps(document))
        (inputs / 'requirements.txt').write_text('Owned test routing requirement.')
        files = {p.name: digest(p) for p in inputs.iterdir()}
        reqs = [{'id': rid, 'checks': ['routing'],
                 'source': {'path': 'requirements.txt', 'locator': 'Owned test'}}
                for rid in sorted({c['requirement'] for c in document['constraints']})]
        spec = {'id': 'routing', 'kind': 'routing_rules', 'params': {'routing_rules': 'rules.json'}}
        manifest = manifest_for_rules(inputs, files, spec, reqs)
        ctx = Ctx(root, manifest, spec, time.monotonic()+900)
        check_routing_rules(ctx)
        assert_sealed_native(self, ctx)
        self.assertEqual(files, {p.name: digest(p) for p in inputs.iterdir()})
        for path in ctx.result['evidence']:
            self.assertTrue((root / path).is_file())
        return ctx.result

    def test_interior_crossing_cannot_prove_complete_route(self):
        import pcbnew as k
        import tempfile
        from pathlib import Path
        from routing_fixture import make_board
        from agent_ee.routing_native import route_coverage
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'board.kicad_pcb'
            make_board(path, lengths=(10, 10))
            board = k.LoadBoard(str(path))
            for track in list(board.GetTracks()):
                if track.GetNetname() == 'D+':
                    board.Remove(track)
            points = [(0, 2), (8, 8), (2, 8), (10, 2)]
            for start, end in zip(points, points[1:]):
                track = k.PCB_TRACK(board)
                track.SetStart(k.VECTOR2I(*(round(v*1e6) for v in start)))
                track.SetEnd(k.VECTOR2I(*(round(v*1e6) for v in end)))
                track.SetLayer(k.F_Cu); track.SetWidth(200000)
                track.SetNet(board.FindNet('D+')); board.Add(track)
            self.assertFalse(route_coverage(board, 'D+')['complete'],
                             'bowtie interior crossing creates a physical branch/loop')

    def test_native_ignored_required_checks_are_enabled_in_staging(self):
        project = {'board': {'design_settings': {'rule_severities': {
            'skew_out_of_range': 'ignore', 'too_many_vias': 'ignore',
            'items_not_allowed': 'ignore', 'disallowed_items': 'ignore'}}}}
        for doc, options in ((contract(max_mm=0.15), {}),
                             (contract('via_count', max_count=2), {'vias': 3})):
            with self.subTest(document=doc):
                result = self.run_rules(doc, project=project, **options)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)

    def test_tool_loading_witness_cannot_alias_a_design_constraint(self):
        for cid in ('tool_load_witness', 'error-length'):
            with self.subTest(constraint=cid):
                doc = contract('length', max_mm=20)
                doc['constraints'][0]['id'] = cid
                result = self.run_rules(doc)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)

    def test_native_unicode_net_conditions_select_actual_routes(self):
        doc = contract(max_mm=0.15)
        doc['constraints'][0]['nets'] = ['測+', '測-']
        result = self.run_rules(doc, net_names=('測+', '測-'))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)

    def test_native_malformed_custom_rules_are_unknown(self):
        result = self.run_rules(contract(max_mm=0.15), custom_rules='(version 1)\n(rule broken')
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_native_skew_defect_and_pass(self):
        for length, expected in ((10.3, 'defect'), (10.1, 'pass')):
            with self.subTest(length=length):
                result = self.run_rules(contract(max_mm=0.15), lengths=(10, length))
                self.assertEqual((result['execution'], result['outcome']), ('ok', expected), result)
                self.assertEqual(len(result['details']['constraints'][0]['rules_sha256']), 64)

    def test_native_three_vias_exceeds_two(self):
        result = self.run_rules(contract('via_count', max_count=2), vias=3)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertTrue(result['details']['constraints'][0]['native_findings'])

    def test_native_missing_route_and_impedance_are_unknown(self):
        for document, options in ((contract(max_mm=0.15), {'unrouted': True}),
                                  (contract('impedance', target_ohm=90), {})):
            result = self.run_rules(document, **options)
            self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_native_length_bounds_and_clearance(self):
        for document, expected in ((contract('length', max_mm=10.2), 'defect'),
                                   (contract('length', min_mm=9, max_mm=11), 'pass'),
                                   (contract('clearance', min_mm=4), 'defect'),
                                   (contract('clearance', min_mm=0.1), 'pass')):
            with self.subTest(document=document):
                result = self.run_rules(document)
                self.assertEqual((result['execution'], result['outcome']), ('ok', expected), result)

    def test_native_overlapping_bounds_are_all_run(self):
        doc = contract('length', max_mm=9)
        doc['constraints'].append({**doc['constraints'][0], 'id': 'permissive', 'max_mm': 20})
        result = self.run_rules(doc)
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertEqual({r['id']: r['status'] for r in result['details']['constraints']},
                         {'usb': 'violation', 'permissive': 'satisfied'})

    def test_native_disabled_via_check_is_enabled_in_staging(self):
        import json
        project = json.dumps({'board': {'design_settings': {'rule_severities': {'too_many_vias': 'ignore'}}}})
        result = self.run_rules(contract('via_count', max_count=2), vias=3, project=project)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)

    def test_native_bad_project_does_not_report_pass(self):
        from agent_ee.checks import CheckError
        with self.assertRaises(CheckError) as failed:
            self.run_rules(contract(max_mm=0.15), project='{invalid-json')
        self.assertEqual(failed.exception.execution, 'failed')

    def test_native_bad_custom_rules_are_missing_coverage(self):
        from agent_ee.checks import CheckError
        try:
            result = self.run_rules(contract(max_mm=0.15), custom='(version 1) (rule "broken" (constraint nonsense))')
        except CheckError as failed:
            self.assertIn(failed.execution, ('failed', 'malformed'))
        else:
            self.assertEqual(result['outcome'], 'unknown', result)
            self.assertNotEqual(result['execution'], 'ok', result)

    def test_native_unicode_net_names_select_the_saved_routes(self):
        document = contract(max_mm=0.15)
        document['constraints'][0]['nets'] = ['測+', '測-']
        result = self.run_rules(document, net_names=('測+', '測-'))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)


class RoutingBoundaryTest(unittest.TestCase):
    def test_malformed_documents_and_nontext_nets_do_not_compile(self):
        for doc in ({}, {'schema': 'agent-ee.routing-rules/1', 'constraints': []},
                    {'schema': 'agent-ee.routing-rules/1', 'constraints': [None]}):
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                compile_routing(doc)
        doc = contract(max_mm=0.15)
        doc['constraints'][0]['nets'] = [{}]
        with self.assertRaises(ValueError):
            compile_routing(doc)

    def test_captured_source_changed_and_path_escape_refused(self):
        import tempfile
        from pathlib import Path
        from agent_ee.routing_rules import captured_file
        from agent_ee.storage import digest
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'rules.json'; p.write_text('{}')
            files = {'rules.json': digest(p)}
            p.write_text('{"changed":true}')
            for relative in ('rules.json', '../rules.json', '/rules.json'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    captured_file(tmp, files, relative)

    def test_unrelated_satisfied_requirement_cannot_share_defect_check(self):
        from agent_ee.routing_rules import validate_mappings
        doc = contract('length', max_mm=9)
        doc['constraints'].append({**doc['constraints'][0], 'id': 'permissive',
                                   'requirement': 'other-satisfied', 'max_mm': 20})
        scope = {'baseline': {'requirements': [
            {'id': rid, 'checks': ['routing'],
             'source': {'path': 'requirements.txt', 'locator': rid}}
            for rid in ('usb-skew', 'other-satisfied')]}}
        with self.assertRaisesRegex(ValueError, 'one requirement'):
            validate_mappings(compile_routing(doc)['constraints'], scope,
                              {'requirements.txt': 'captured'}, 'routing')

    def test_requirement_check_mapping_must_be_exact(self):
        from agent_ee.routing_rules import validate_mappings
        compiled = compile_routing(contract(max_mm=0.15))
        with self.assertRaisesRegex(ValueError, 'mappings'):
            validate_mappings(compiled['constraints'], {'baseline': {'requirements': []}}, {}, 'routing')


class RoutingIntegrationTest(unittest.TestCase):
    def test_registered_execution_and_capability(self):
        from agent_ee.checks import CHECK_FUNCS
        from agent_ee.capabilities import KINDS
        self.assertIn('routing_rules', CHECK_FUNCS)
        self.assertIn('routing_rules', KINDS)


class NativeRoutingPipelineTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-routing-pipeline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare(self, name, document=None, **options):
        from routing_fixture import make_scope
        root = self.root / name
        root.mkdir()
        make_scope(root, document or contract(max_mm=0.15), **options)
        return root

    def test_public_native_review_verify_and_sealed_witnesses(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import run_review, verify, runtime_files
        from agent_ee.baseline import load_baseline, validate_witnesses
        from agent_ee.storage import digest, read_json, write_json
        from test_capability_pipeline import NativeCapabilityPipelineTests, PROFILE_CALL
        runtime = runtime_files()
        for name in ('codex', 'codex_tool_host'):
            runtime[name] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        self.calls = []
        with patch('agent_ee.cli.runtime_files', return_value=runtime):
            for name, lengths, unrouted, document, outcome in (
                ('pass', (10, 10.1), False, contract(max_mm=0.15), 'criteria_satisfied'),
                ('defect', (10, 10.3), False, contract(max_mm=0.15), 'defects_or_unresolved'),
                ('missing', (10, 10.3), True, contract(max_mm=0.15), 'incomplete'),
                ('partial', (10, 10.3), False, {'schema': 'agent-ee.routing-rules/1',
                    'constraints': [contract(max_mm=0.15)['constraints'][0],
                        {**contract('impedance', target_ohm=90)['constraints'][0], 'id': 'impedance'}]},
                 'incomplete')):
                with self.subTest(case=name):
                    root = self.prepare(name, document, lengths=lengths, unrouted=unrouted)
                    scope = read_json(root / 'scope.json')
                    if name == 'defect':
                        other = contract('length', max_mm=20)
                        other['constraints'][0]['requirement'] = 'other-satisfied'
                        write_json(root / 'other.json', other)
                        scope['inputs'].append({'path': 'other.json', 'at': 'other.json'})
                        scope['checks'].append({'id': 'other', 'kind': 'routing_rules',
                                                'params': {'routing_rules': 'other.json'}})
                        scope['baseline']['requirements'].append({
                            **scope['baseline']['requirements'][0], 'id': 'other-satisfied', 'checks': ['other']})
                    scope['review_profile'] = {'schema': 'agent-ee.profile/1', 'name': 'scripted-routing',
                        'reviewers': [{**PROFILE_CALL, 'role': 'circuit'}, {**PROFILE_CALL, 'role': 'physical'}],
                        'adjudicator': PROFILE_CALL}
                    write_json(root / 'scope.json', scope)
                    def agent(*args, **kwargs):
                        return NativeCapabilityPipelineTests.agent(self, *args, **kwargs)
                    with patch('agent_ee.reviewers.dispatch', side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
                        status = run_review(root / 'scope.json', root / 'run', 120)
                    result = verify(root / 'run')
                    self.assertNotIn('error', result, result)
                    self.assertEqual(status, 0 if name == 'pass' else 1, result)
                    self.assertEqual(result['outcome'], outcome, result)
                    self.assertTrue(result['adjudicated'], result)
                    self.assertCountEqual([role for _, role in self.calls[-3:-1]], ['circuit', 'physical'])
                    self.assertEqual(self.calls[-1][1], 'adjudicator')
                    baseline = load_baseline(root / 'run')
                    self.assertIn('rules.json', baseline['files'])
                    self.assertEqual(baseline['coverage']['checks']['routing']['predicates'], ['routing:usb'])
                    self.assertIn('kicad-cli', result['launch_expectation']['required_runtime'])
                    self.assertIn('pcbnew', result['launch_expectation']['required_runtime'])
                    accepted = [f for f in result['findings'] if f.get('status') == 'accepted']
                    self.assertEqual([f['local_id'] for f in accepted], ['routing'] if name == 'defect' else [])
                    witness = [{'requirement_id': 'usb-skew', 'check_id': 'routing'}]
                    evidence = [{'path': 'checks/routing.json'}]
                    if name == 'defect':
                        validate_witnesses(root / 'run', witness, evidence)
                        with self.assertRaisesRegex(ValueError, 'trace'):
                            validate_witnesses(root / 'run', [{'requirement_id': 'other-satisfied',
                                'check_id': 'routing'}], evidence)
                        with self.assertRaisesRegex(ValueError, 'failed criterion'):
                            validate_witnesses(root / 'run', [{'requirement_id': 'other-satisfied',
                                'check_id': 'other'}], [{'path': 'checks/other.json'}])
                        artifact = root / 'run/work/routing/native-rules/usb/board.kicad_dru'
                        artifact.write_text(artifact.read_text() + '\n; tampered\n')
                        with self.assertRaisesRegex(ValueError, 'changed|seal'):
                            verify(root / 'run')
                        with self.assertRaisesRegex(ValueError, 'changed'):
                            validate_witnesses(root / 'run', witness, evidence)
                    else:
                        with self.assertRaisesRegex(ValueError, 'failed criterion'):
                            validate_witnesses(root / 'run', witness, evidence)
                    if name == 'partial':
                        records = result['checks'][0]['details']['constraints']
                        self.assertEqual({r['id']: r['status'] for r in records},
                                         {'impedance': 'not_evaluated', 'usb': 'violation'})

    def test_cli_checks_only_preserves_incomplete_status(self):
        import subprocess
        import sys
        from agent_ee.cli import verify
        from agent_ee.storage import read_json, digest
        root = self.prepare('cli', lengths=(10, 10.1))
        proc = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'review', str(root / 'scope.json'),
                               '--out', str(root / 'run'), '--checks-only'],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        result = verify(root / 'run')
        self.assertNotIn('error', result, result)
        self.assertEqual(result['checks'][0]['outcome'], 'pass', result)
        self.assertEqual(result['outcome'], 'incomplete', result)
        self.assertFalse(result['adjudicated'])
        self.assertTrue((root / 'run/work/verification/evidence.json').is_file())
        self.assertEqual(read_json(root / 'run/candidate.json')['files'],
                         {p.relative_to(root / 'run/inputs').as_posix(): digest(p)
                          for p in (root / 'run/inputs').iterdir()})

    def test_native_staging_failure_is_retained_as_unknown(self):
        import contextlib
        import io
        from agent_ee.cli import run_review, verify
        from agent_ee.storage import read_json, write_json
        for suffix, content in (('pro', '{'), ('dru', '(version 1)\n(rule "agent_ee_usb" (constraint length (max 1mm)))')):
            with self.subTest(sidecar=suffix):
                root = self.prepare('invalid-' + suffix)
                sidecar = 'test.kicad_' + suffix
                (root / sidecar).write_text(content)
                scope = read_json(root / 'scope.json')
                scope['inputs'].append({'path': sidecar, 'at': sidecar})
                write_json(root / 'scope.json', scope)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(run_review(root / 'scope.json', root / 'run', 120, checks_only=True), 1)
                result = verify(root / 'run')
                self.assertNotIn('error', result, result)
                check = result['checks'][0]
                self.assertEqual((check['execution'], check['outcome']), ('failed', 'unknown'), check)
                self.assertTrue((root / 'run/work/routing/stderr.log').read_bytes())
                self.assertTrue((root / 'run/work/verification/evidence.json').is_file())

    def test_scope2_and_scope3_refuse_shared_requirement_and_freeze_rule_dependencies(self):
        from agent_ee.candidate import capture
        from agent_ee.baseline import compile_baseline
        from agent_ee.storage import read_json, write_json, digest
        for version in (2, 3):
            root = self.prepare(f'v{version}', version=version)
            scope = read_json(root / 'scope.json')
            for suffix, contents in (('pro', '{}'), ('dru', '(version 1)\n')):
                name = 'test.kicad_' + suffix
                (root / name).write_text(contents)
                scope['inputs'].append({'path': name, 'at': name})
            write_json(root / 'scope.json', scope)
            run = root / 'run'; run.mkdir()
            manifest = capture(root / 'scope.json', run)
            baseline = read_json(run / 'baseline.json')
            self.assertTrue({'rules.json', 'test.kicad_pro', 'test.kicad_dru'} <= baseline['files'].keys())
            scope['baseline']['requirements'].append({**scope['baseline']['requirements'][0],
                                                       'id': 'other-satisfied'})
            document = read_json(run / 'inputs/rules.json')
            document['constraints'].append({**document['constraints'][0], 'id': 'permissive',
                                             'requirement': 'other-satisfied', 'max_mm': 20})
            write_json(run / 'inputs/rules.json', document)
            manifest['files']['rules.json'] = digest(run / 'inputs/rules.json')
            with self.assertRaisesRegex(ValueError, 'one requirement'):
                compile_baseline(scope, manifest['files'], run / 'inputs')


class RoutingContractIntegrationTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from agent_ee.storage import digest
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'board.kicad_pcb').write_text('Captured board bytes; pure validation never runs tools.')
        (self.root / 'rules.json').write_text(__import__('json').dumps(contract(max_mm=0.15)))
        (self.root / 'requirements.txt').write_text('Declared routing bound.')
        self.files = {p.name: digest(p) for p in self.root.iterdir()}
        self.scope = {'board': 'board.kicad_pcb', 'baseline': {'requirements': [
            {'id': 'usb-skew', 'checks': ['routing'],
             'source': {'path': 'requirements.txt', 'locator': 'routing'}}]}}
        self.spec = {'id': 'routing', 'kind': 'routing_rules', 'params': {'routing_rules': 'rules.json'}}

    def describe(self):
        from agent_ee.capabilities import describe_check
        return describe_check(self.spec, check_specs=[self.spec], scope=self.scope,
                              files=self.files, inputs=self.root)

    def test_supported_and_missing_malformed_contracts(self):
        from agent_ee.storage import digest
        self.assertTrue(self.describe()['can_evaluate_bound'])
        for contents in ('{}', '{', '{"schema":"ignored","schema":"agent-ee.routing-rules/1",'
                         '"constraints":[]}', '[]'):
            with self.subTest(contents=contents):
                (self.root / 'rules.json').write_text(contents)
                self.files['rules.json'] = digest(self.root / 'rules.json')
                descriptor = self.describe()
                self.assertEqual(descriptor['status'], 'unsupported', descriptor)
                self.assertFalse(descriptor['can_evaluate_bound'], descriptor)
        del self.files['rules.json']
        self.assertEqual(self.describe()['status'], 'unsupported')

    def test_duplicate_json_keys_cannot_change_captured_meaning(self):
        import json
        from agent_ee.storage import digest
        text = json.dumps(contract(max_mm=0.15)).replace('"max_mm": 0.15', '"max_mm": 0.01, "max_mm": 0.15')
        (self.root / 'rules.json').write_text(text)
        self.files['rules.json'] = digest(self.root / 'rules.json')
        self.assertEqual(self.describe()['status'], 'unsupported')

    def test_unknown_params_unsafe_paths_and_changed_hash_are_unsupported(self):
        import copy
        original = copy.deepcopy(self.spec['params'])
        for params in ({}, {**original, 'allow_partial': True}, {'routing_rules': '../rules.json'},
                       {'routing_rules': './rules.json'}, {'routing_rules': '/rules.json'}):
            with self.subTest(params=params):
                self.spec['params'] = params
                self.assertEqual(self.describe()['status'], 'unsupported')
        self.spec['params'] = original
        (self.root / 'rules.json').write_text('{}')
        self.assertEqual(self.describe()['status'], 'unsupported')

    def test_policy_identity_covers_rule_compiler_and_native_runner(self):
        from agent_ee.cli import policy_files
        self.assertTrue({'routing_rules.py', 'routing_native.py'} <= policy_files().keys())


class NativePhysicalRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def test_physical_contacts_and_simple_bends(self):
        import pcbnew as k
        import tempfile
        from pathlib import Path
        from routing_fixture import make_board
        from agent_ee.routing_native import route_coverage
        paths = {
            'simple-bend': [(0, 2), (5, 3), (10, 2)],
            'sharp-reversal': [(0, 2), (12, 3), (10, 2)],
            'overlap': [(0, 2), (8, 2), (4, 2), (10, 2)],
            'finite-width-contact': [(0, 2), (4, 2), (4, 2.1), (8, 2.1), (10, 2)],
            'finite-width-tangent': [(0, 2), (4, 2), (4, 2.2), (8, 2.2), (10, 2)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            for case in (*paths, 'pad-contact', 'pad-tangent', 'via-contact', 'via-tangent', 'arc', 'copper-graphic'):
                with self.subTest(case=case):
                    path = Path(tmp) / 'board.kicad_pcb'
                    make_board(path, lengths=(10, 10))
                    board = k.LoadBoard(str(path))
                    if case in paths or case == 'arc':
                        for track in list(board.GetTracks()):
                            if track.GetNetname() == 'D+':
                                board.Remove(track)
                        points = paths.get(case, [(0, 2), (10, 2)])
                        for start, end in zip(points, points[1:]):
                            track = k.PCB_ARC(board) if case == 'arc' else k.PCB_TRACK(board)
                            track.SetStart(k.VECTOR2I(*(round(v*1e6) for v in start)))
                            track.SetEnd(k.VECTOR2I(*(round(v*1e6) for v in end)))
                            if case == 'arc':
                                track.SetMid(k.VECTOR2I(5000000, 3000000))
                            track.SetLayer(k.F_Cu); track.SetWidth(200000)
                            track.SetNet(board.FindNet('D+')); board.Add(track)
                    elif case in ('pad-contact', 'pad-tangent'):
                        pad = next(p for fp in board.GetFootprints() for p in fp.Pads()
                                   if p.GetNetname() == 'D-')
                        pad.SetPosition(k.VECTOR2I(5000000, 2300000 if case == 'pad-tangent' else 2150000))
                    elif case in ('via-contact', 'via-tangent'):
                        via = k.PCB_VIA(board); via.SetPosition(k.VECTOR2I(5000000, 2400000 if case == 'via-tangent' else 2350000))
                        via.SetWidth(600000); via.SetDrill(300000)
                        via.SetViaType(k.VIATYPE_THROUGH); via.SetLayerPair(k.F_Cu, k.B_Cu)
                        via.SetNet(board.FindNet('D-')); board.Add(via)
                    else:
                        shape = k.PCB_SHAPE(board); shape.SetShape(k.SHAPE_T_SEGMENT)
                        shape.SetStart(k.VECTOR2I(5000000, 0)); shape.SetEnd(k.VECTOR2I(5000000, 4000000))
                        shape.SetLayer(k.F_Cu); shape.SetWidth(200000); board.Add(shape)
                    coverage = route_coverage(board, 'D+')
                    self.assertEqual(coverage['complete'], case == 'simple-bend', coverage)
