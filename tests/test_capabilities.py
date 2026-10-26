"""Opt-in executable acceptance contracts; historical mappings stay historical."""
import copy
import unittest

from agent_ee.baseline import compile_baseline


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.files = dict.fromkeys(['design.sch', 'design.pcb', 'req', 'target', 'ref'], 'hash')
        self.scope = {'schema': 'agent-ee.scope/3', 'schematic': 'design.sch', 'board': 'design.pcb',
            'checks': [{'id': 'net', 'kind': 'netlist'}, {'id': 'width', 'kind': 'geometry',
                'params': {'min_track_width_mm': 0.2}}],
            'baseline': {'requirements': [
                {'id': 'I1', 'intent': 'informational', 'statement': 'Connectivity export',
                 'source': {'path': 'req', 'locator': 'I1'}, 'checks': ['net']},
                {'id': 'R1', 'intent': 'acceptance', 'statement': 'Routing width >= 0.2 mm',
                 'source': {'path': 'req', 'locator': 'R1'}, 'checks': ['width']}],
                'targets': ['target'], 'reference_architectures': ['ref']}}

    def test_mixed_intent_compiles_captured_coverage(self):
        baseline = compile_baseline(self.scope, self.files)
        self.assertEqual(baseline['schema'], 'agent-ee.baseline/2')
        coverage = baseline['coverage']
        self.assertFalse(coverage['checks']['net']['can_evaluate_bound'])
        self.assertEqual(coverage['requirements']['R1']['acceptance_checks'], ['width'])
        self.assertEqual(set(coverage['checks']), {'net', 'width'})

    def test_informational_checks_cannot_discharge_acceptance(self):
        for kind in ('netlist', 'geometry'):
            with self.subTest(kind=kind):
                scope = copy.deepcopy(self.scope)
                scope['checks'][1] = {'id': 'width', 'kind': kind}
                with self.assertRaisesRegex(ValueError, 'no executable bound'):
                    compile_baseline(scope, self.files)
        for req in self.scope['baseline']['requirements']:
            req['intent'] = 'informational'
        with self.assertRaisesRegex(ValueError, 'at least one acceptance'):
            compile_baseline(self.scope, self.files)

    def test_invalid_limits_unknown_contracts_and_no_waivers(self):
        for params in ({'min_track_width_mm': None}, {'min_track_width_mm': True},
                       {'min_track_width_mm': -1}, {'min_track_width_mm': float('nan')},
                       {'min_track_width_mm': float('inf')}, {'criterion': 'future'},
                       {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK'],
                        'min_track_width_mm': .2, 'exclude_nets': []}):
            with self.subTest(params=params):
                self.scope['checks'][1]['params'] = params
                with self.assertRaisesRegex(ValueError, 'unsupported capability'):
                    compile_baseline(self.scope, self.files)
        self.scope['checks'][1] = {'id': 'width', 'kind': 'future'}
        with self.assertRaisesRegex(ValueError, 'unknown capability kind'):
            compile_baseline(self.scope, self.files)
        self.scope['baseline']['requirements'][0]['waiver'] = True
        with self.assertRaisesRegex(ValueError, 'requirement needs'):
            compile_baseline(self.scope, self.files)

    def test_unknown_informational_kind_fails_closed(self):
        self.scope['checks'][0]['kind'] = 'future'
        with self.assertRaisesRegex(ValueError, 'unknown capability kind'):
            compile_baseline(self.scope, self.files)

    def rc(self):
        self.scope['checks'][1] = {'id': 'width', 'kind': 'rc_transient', 'params': {
            'netlist_check': 'net', 'input_net': 'IN', 'output_net': 'OUT', 'ground_net': 'GND',
            'measurements': [{'name': 'response', 'at_ms': 1, 'min': .62, 'max': .65}]}}
        return self.scope['checks'][1]['params']

    def test_rc_prerequisites_bounds_and_normalized_measurements(self):
        params = self.rc()
        baseline = compile_baseline(self.scope, self.files)
        self.assertEqual(baseline['coverage']['checks']['width']['prerequisites'],
                         [{'check_id': 'net', 'kind': 'netlist'}])
        for change in ({'netlist_check': 'missing'}, {'netlist_check': 'width'},
                       {'stop_ms': 0}, {'measurements': []},
                       {'measurements': [{'name': 'x', 'at_ms': 1, 'min': 2, 'max': 1}]},
                       {'measurements': [{'name': 'x', 'at_ms': 1, 'min': True}]},
                       {'measurements': [{'name': 'x', 'at_ms': 6, 'min': 0}]},
                       {'measurements': [{'name': 'X', 'at_ms': 1}, {'name': 'x', 'at_ms': 1}]}):
            with self.subTest(change=change):
                scope = copy.deepcopy(self.scope)
                scope['checks'][1]['params'].update(change)
                with self.assertRaises(ValueError):
                    compile_baseline(scope, self.files)
        self.scope['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            compile_baseline(self.scope, self.files)
        self.scope['checks'].reverse()
        self.scope['checks'][0]['kind'] = 'erc'
        with self.assertRaisesRegex(ValueError, 'wrong-kind'):
            compile_baseline(self.scope, self.files)

    def test_native_rule_configuration_is_owned_by_immutable_baseline(self):
        self.files.update({'design.kicad_pro': 'native settings', 'design.kicad_dru': 'custom rules'})
        self.scope['checks'].append({'id': 'rules', 'kind': 'erc'})
        self.scope['baseline']['requirements'][1]['checks'].append('rules')
        original = compile_baseline(self.scope, self.files)
        self.assertEqual(original['coverage']['checks']['rules']['immutable_inputs'],
                         ['design.kicad_dru', 'design.kicad_pro'])
        self.assertEqual(original['files']['design.kicad_pro'], 'native settings')
        self.files['design.kicad_dru'] = 'weakened rules'
        self.assertNotEqual(compile_baseline(self.scope, self.files)['baseline_id'], original['baseline_id'])
        from agent_ee.campaign import mutable_inputs
        self.scope['mutable_inputs'] = ['design.pcb', 'design.kicad_pro']
        with self.assertRaisesRegex(ValueError, 'outside the immutable baseline'):
            mutable_inputs({'scope': self.scope, 'files': self.files}, original)

    def test_registry_matches_execution_kinds(self):
        from agent_ee.capabilities import KINDS
        from agent_ee.checks import CHECK_FUNCS
        self.assertEqual(KINDS, CHECK_FUNCS.keys())

    def test_conversion_preserves_all_requirements_and_original(self):
        from agent_ee.capabilities import convert_scope
        legacy = copy.deepcopy(self.scope)
        legacy['schema'] = 'agent-ee.scope/2'
        for req in legacy['baseline']['requirements']:
            del req['intent']
        original = copy.deepcopy(legacy)
        converted = convert_scope(legacy)
        self.assertEqual(legacy, original)
        for before, after in zip(legacy['baseline']['requirements'], converted['baseline']['requirements']):
            self.assertEqual(after, {**before, 'intent': 'acceptance'})
        with self.assertRaisesRegex(ValueError, 'no executable bound'):
            compile_baseline(converted, self.files)
        with self.assertRaisesRegex(ValueError, 'requires scope/2'):
            convert_scope({'schema': 'agent-ee.scope/1'})

    def test_exact_legacy_baseline_and_unknown_direct_dispatch(self):
        from agent_ee.storage import identity
        self.scope['schema'] = 'agent-ee.scope/2'
        for req in self.scope['baseline']['requirements']:
            del req['intent']
        # Legacy permits even unbounded checks; no new fields/defaults enter identity.
        self.scope['checks'][1].pop('params')
        expected = {'schema': 'agent-ee.baseline/1', **self.scope['baseline'],
                    'files': {'req': 'hash', 'ref': 'hash', 'target': 'hash'},
                    'criteria': self.scope['checks']}
        self.assertEqual(compile_baseline(self.scope, self.files),
                         {**expected, 'baseline_id': identity(expected)})
        self.scope['schema'] = 'agent-ee.scope/1'
        self.assertIsNone(compile_baseline(self.scope, self.files))
        for version in ('agent-ee.scope/0', 'agent-ee.scope/4', None):
            self.scope['schema'] = version
            with self.assertRaisesRegex(ValueError, 'unknown scope'):
                compile_baseline(self.scope, self.files)

    def measurement(self):
        self.files.update(dict.fromkeys(('adapter.py', 'solver.py', 'model'), 'hash'))
        self.scope['checks'][1] = {'id': 'width', 'kind': 'measurements', 'params': {
            'adapter': 'adapter.py', 'calibration_inputs': ['model'],
            'tool': {'argv': ['/usr/bin/python3', '/candidate/solver.py'],
                     'sha256': 'a' * 64, 'domain': 'electrical', 'inputs': ['solver.py']},
            'limits': {'output': {'unit': 'V', 'condition': 'load', 'min': 0, 'max': 10}}}}
        return self.scope['checks'][1]['params']

    def test_measurement_dependency_ownership_and_bounds(self):
        params = self.measurement()
        baseline = compile_baseline(self.scope, self.files)
        self.assertEqual(set(baseline['files']), {'req', 'ref', 'target', 'adapter.py', 'solver.py', 'model'})
        for change in ({'limits': {}}, {'limits': {'output': {'unit': 'V', 'condition': 'load'}}},
                       {'limits': {'output': {'unit': 'V', 'condition': 'load', 'min': 2, 'max': 1}}},
                       {'limits': {'output': {'unit': 'V', 'condition': 'load', 'max': float('inf')}}},
                       {'adapter': 'missing'}, {'tool': {**params['tool'], 'inputs': []}},
                       {'tool': {**params['tool'], 'domain': 'magic'}},
                       {'tool': {**params['tool'], 'sha256': 'unpinned'}}):
            with self.subTest(change=change):
                scope = copy.deepcopy(self.scope)
                scope['checks'][1]['params'].update(change)
                with self.assertRaisesRegex(ValueError, 'unsupported capability'):
                    compile_baseline(scope, self.files)
        params['tool']['inputs'].append('design.pcb')
        with self.assertRaisesRegex(ValueError, 'native design'):
            compile_baseline(self.scope, self.files)

    def test_coupling_requires_resolvable_metrics_distinct_domains_and_conditions(self):
        params = self.measurement()
        source = copy.deepcopy(self.scope['checks'][1])
        source['id'] = 'thermal'
        source['params']['tool']['domain'] = 'thermal'
        self.scope['checks'].append(source)
        self.scope['checks'].append({'id': 'coupled', 'kind': 'coupled_margins', 'params': {
            'members': [{'check': 'width', 'metric': 'output', 'name': 'e'},
                        {'check': 'thermal', 'metric': 'output', 'name': 't'}],
            'condition': 'load', 'limits': {'e': params['limits']['output'], 't': params['limits']['output']}}})
        self.scope['baseline']['requirements'][1]['checks'].extend(['thermal', 'coupled'])
        self.assertTrue(compile_baseline(self.scope, self.files)['coverage']['checks']['coupled']['can_evaluate_bound'])
        for mutation in ('metric', 'domain', 'condition', 'limit'):
            with self.subTest(mutation=mutation):
                scope = copy.deepcopy(self.scope)
                if mutation == 'metric':
                    scope['checks'][3]['params']['members'][0]['metric'] = 'missing'
                elif mutation == 'domain':
                    scope['checks'][2]['params']['tool']['domain'] = 'electrical'
                elif mutation == 'condition':
                    scope['checks'][3]['params']['condition'] = 'other'
                else:
                    del scope['checks'][3]['params']['limits']['t']
                with self.assertRaisesRegex(ValueError, 'unsupported capability coupled'):
                    compile_baseline(scope, self.files)


class CapturedCapabilityTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        setup = CapabilityTests()
        setup.setUp()
        self.scope = setup.scope
        self.scope.update(name='coverage', scope='coverage contract',
                          inputs=[{'path': str(source), 'at': 'design'}], packet_max_bytes=1024)
        for name in setup.files:
            (source / name).write_text(name)
        self.scope['board'] = 'design/design.pcb'
        self.scope['schematic'] = 'design/design.sch'
        for req in self.scope['baseline']['requirements']:
            req['source']['path'] = 'design/req'
        self.scope['baseline']['targets'] = ['design/target']
        self.scope['baseline']['reference_architectures'] = ['design/ref']
        self.path = self.root / 'scope.json'
        write_json(self.path, self.scope)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.manifest = capture(self.path, self.run)

    def sealed(self, *, outcome='defect', execution='ok', artifacts=True):
        from agent_ee.baseline import freeze_evidence
        from agent_ee.storage import write_json
        self.checks = []
        for cid, kind in (('net', 'netlist'), ('width', 'geometry')):
            check = {'id': cid, 'kind': kind, 'candidate_id': self.manifest['candidate_id'],
                     'execution': execution if cid == 'width' else 'ok',
                     'outcome': outcome if cid == 'width' else 'info', 'summary': 'test evidence',
                     'evidence': [f'work/{cid}/raw.json'] if artifacts else []}
            write_json(self.run / f'work/{cid}/raw.json', {'measurement': .1})
            write_json(self.run / f'checks/{cid}.json', check)
            self.checks.append(check)
        freeze_evidence(self.run, self.checks)
        self.witness = [{'requirement_id': 'R1', 'check_id': 'width'}]
        self.evidence = [{'path': 'checks/width.json', 'locator': 'outcome'}]

    def test_retained_coverage_tampering_and_unknown_snapshot_schema_refused(self):
        from agent_ee.candidate import verify_snapshot
        from agent_ee.storage import read_json, write_json
        original = read_json(self.run / 'baseline.json')
        for mutation in ('boolean', 'predicate', 'intent', 'mapping', 'hash', 'missing'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(original)
                if mutation == 'boolean':
                    changed['coverage']['checks']['net']['can_evaluate_bound'] = True
                elif mutation == 'predicate':
                    changed['coverage']['checks']['width']['predicates'] = ['made-up']
                elif mutation == 'intent':
                    changed['coverage']['requirements']['I1']['intent'] = 'acceptance'
                elif mutation == 'mapping':
                    changed['coverage']['requirements']['R1']['acceptance_checks'] = ['net']
                elif mutation == 'hash':
                    changed['files']['design/req'] = 'changed'
                else:
                    del changed['coverage']
                write_json(self.run / 'baseline.json', changed)
                with self.assertRaisesRegex(ValueError, 'baseline changed'):
                    verify_snapshot(self.run)
        write_json(self.run / 'baseline.json', original)
        manifest = copy.deepcopy(self.manifest)
        manifest['scope']['schema'] = 'agent-ee.scope/99'
        write_json(self.run / 'candidate.json', manifest)
        from agent_ee.baseline import load_baseline
        with self.assertRaisesRegex(ValueError, 'unknown scope'):
            load_baseline(self.run)

    def test_design_changes_preserve_coverage_and_baseline_but_criteria_do_not(self):
        from agent_ee.baseline import load_baseline
        original = load_baseline(self.run)
        files = copy.deepcopy(self.manifest['files'])
        files['design/design.pcb'] = 'new candidate design hash'
        self.assertEqual(compile_baseline(self.scope, files), original)
        for change in ('limit', 'intent', 'source'):
            scope = copy.deepcopy(self.scope)
            changed_files = copy.deepcopy(files)
            if change == 'limit':
                scope['checks'][1]['params']['min_track_width_mm'] = .3
            elif change == 'intent':
                scope['baseline']['requirements'][0]['intent'] = 'acceptance'
                scope['baseline']['requirements'][0]['checks'].append('width')
            else:
                changed_files['design/req'] = 'new normative source'
            self.assertNotEqual(compile_baseline(scope, changed_files)['baseline_id'], original['baseline_id'])

    def test_sealed_witnesses_require_acceptance_and_executed_failed_capability(self):
        from agent_ee.baseline import validate_witnesses
        from agent_ee.storage import read_json, write_json
        self.sealed()
        validate_witnesses(self.run, self.witness, self.evidence)
        for witnesses, evidence, message in (
            ([{'requirement_id': 'I1', 'check_id': 'net'}],
             [{'path': 'checks/net.json', 'locator': 'outcome'}], 'audited acceptance'),
            ([{'requirement_id': 'R1', 'check_id': 'net'}], self.evidence, 'trace'),
            ([], self.evidence, 'requires requirement'),
            (self.witness, [{'path': 'inputs/design/design.pcb', 'locator': 'text'}], 'cited check')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_witnesses(self.run, witnesses, evidence)
        for execution, outcome in (('ok', 'pass'), ('ok', 'info'), ('unsupported', 'unknown'),
                                   ('malformed', 'unknown'), ('failed', 'unknown')):
            self.sealed(execution=execution, outcome=outcome)
            with self.subTest(execution=execution, outcome=outcome), self.assertRaisesRegex(ValueError, 'failed criterion'):
                validate_witnesses(self.run, self.witness, self.evidence)
        self.sealed(artifacts=False)
        with self.assertRaisesRegex(ValueError, 'no reproducible'):
            validate_witnesses(self.run, self.witness, self.evidence)
        self.sealed()
        write_json(self.run / 'work/width/raw.json', {'measurement': .3})
        with self.assertRaisesRegex(ValueError, 'tool evidence changed'):
            validate_witnesses(self.run, self.witness, self.evidence)
        self.sealed()
        check = read_json(self.run / 'checks/width.json')
        check['summary'] = 'modified'
        write_json(self.run / 'checks/width.json', check)
        with self.assertRaisesRegex(ValueError, 'unchanged cited check'):
            validate_witnesses(self.run, self.witness, self.evidence)
        self.sealed()
        seal_path = self.run / 'work/verification/evidence.json'
        seal = read_json(seal_path)
        seal['candidate_id'] = 'wrong candidate'
        write_json(seal_path, seal)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            validate_witnesses(self.run, self.witness, self.evidence)

    def test_manual_adjudication_mapping_excludes_informational_requirement(self):
        from agent_ee.findings import adjudicate, collect_findings
        from agent_ee.storage import write_json
        self.scope['baseline']['requirements'][0]['checks'].append('width')
        # Capture a fresh contract rather than editing historical baseline bytes.
        from agent_ee.candidate import capture
        write_json(self.path, self.scope)
        self.run = self.root / 'mixed-run'
        self.run.mkdir()
        self.manifest = capture(self.path, self.run)
        self.sealed()
        findings = collect_findings([], self.checks)
        decisions = {'schema': 'agent-ee.decisions/1', 'candidate_id': self.manifest['candidate_id'],
            'run_id': 'coverage-test', 'decisions': [{'finding_id': 'check/width', 'disposition': 'accepted',
                           'reason': 'routing width failed', 'evidence': self.evidence}]}
        self.assertEqual(adjudicate(findings, decisions, self.run)[0]['status'], 'accepted')
        fabricated = {**findings[0], 'id': 'circuit/thermal', 'source': 'review', 'witnesses': self.witness}
        decisions['decisions'][0]['finding_id'] = fabricated['id']
        with self.assertRaisesRegex(ValueError, 'only executable check'):
            adjudicate([fabricated], decisions, self.run)

    def test_packet_keeps_full_coverage_at_tiny_text_budget(self):
        from agent_ee.reviewers import build_packet
        from agent_ee.baseline import load_baseline
        self.sealed()
        packet = build_packet(self.run, self.checks, 'coverage-test')
        self.assertEqual(packet['baseline'], load_baseline(self.run))
        self.assertIn('coverage_id', packet['baseline']['coverage'])

    def test_refused_capture_and_review_never_execute_tools_or_inference(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.run_record import run_review
        from agent_ee.storage import read_json, write_json
        variants = []
        for kind in ('netlist', 'geometry', 'future'):
            scope = copy.deepcopy(self.scope)
            scope['checks'][1] = {'id': 'width', 'kind': kind}
            variants.append(scope)
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/4'
        variants.append(scope)
        for index, scope in enumerate(variants):
            write_json(self.path, scope)
            with patch('agent_ee.checks.run_checks') as checks, \
                 patch('agent_ee.reviewers.reviewer_call') as inference, \
                 contextlib.redirect_stdout(io.StringIO()):
                run = self.root / f'refused-{index}'
                self.assertEqual(run_review(self.path, run, 60, host={'runtime_files': {}}), 1)
                checks.assert_not_called()
                inference.assert_not_called()
                self.assertEqual(read_json(run / 'result.json')['outcome'], 'incomplete')
                self.assertFalse((run / 'candidate.json').exists())

    def test_sealing_refuses_check_from_another_candidate(self):
        from agent_ee.baseline import freeze_evidence
        from agent_ee.storage import write_json
        self.sealed()
        self.checks[1]['candidate_id'] = 'another-candidate'
        write_json(self.run / 'checks/width.json', self.checks[1])
        with self.assertRaisesRegex(ValueError, 'different candidate'):
            freeze_evidence(self.run, self.checks)

    def test_immutable_dependencies_cannot_become_campaign_repair_targets(self):
        from agent_ee.campaign import mutable_inputs
        from agent_ee.baseline import load_baseline
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        self.scope['checks'][1]['params']['calibration_inputs'] = ['design/ref']
        self.scope['mutable_inputs'] = ['design/design.pcb', 'design/ref']
        write_json(self.path, self.scope)
        run = self.root / 'dependency-run'
        run.mkdir()
        manifest = capture(self.path, run)
        with self.assertRaisesRegex(ValueError, 'outside the immutable baseline'):
            mutable_inputs(manifest, load_baseline(run))
