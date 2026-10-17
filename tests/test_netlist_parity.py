import json
import unittest
from pathlib import Path

from agent_ee.netlist_parity import resolved_inventory

FIXTURE = Path(__file__).parent / 'fixtures/netlist-parity'


class ResolvedTests(unittest.TestCase):
    def test_identical_parts_remain_separate_instances(self):
        graph = resolved_inventory(json.loads((FIXTURE / 'resolved.json').read_text()))
        self.assertEqual(len(graph['components']), 3)
        self.assertEqual(len(graph['nets']), 3)
        self.assertEqual(graph['components']['series']['pins'], ('1', '2'))
        self.assertEqual(graph['components']['shunt']['pins'], ('1', '2'))


class NativeReaderTests(unittest.TestCase):
    def test_full_native_inventory_has_pin_metadata_and_provenance(self):
        from agent_ee.native_netlist import read_native_inventory
        graph = read_native_inventory(Path(__file__).parents[1] /
            'evidence/rc-final/control/work/netlist/design.xml')
        self.assertEqual(len(graph['components']), 3)
        self.assertEqual(graph['components']['J1']['pins'], ('1', '2', '3'))
        self.assertEqual(graph['components']['J1']['lib_id'], 'Connector_Generic:Conn_01x03')
        self.assertEqual(graph['components']['J1']['sheet'], '/')


class GraphTests(unittest.TestCase):
    def setUp(self):
        from agent_ee.native_netlist import read_native_inventory
        self.expected = resolved_inventory(json.loads((FIXTURE / 'resolved.json').read_text()))
        self.actual = read_native_inventory(FIXTURE / 'native.xml')

    def compare(self, **policy):
        from agent_ee.netlist_parity import compare
        return compare(self.expected, self.actual, {'predicate': 'topology',
                       'multiset': 'exactly', 'anchors': {}, **policy})

    def test_all_reference_and_net_names_can_change(self):
        report = self.compare()
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual(report['scores']['jaccard'], 1.0)
        self.assertEqual(report['component_matches']['series'], ['R80'])

    def change_membership(self, terminal, target):
        nets = self.actual['nets']
        for net in nets.values():
            net['terminals'] = tuple(t for t in net['terminals'] if t != terminal)
        if target is not None:
            nets[target]['terminals'] += (terminal,)
        from agent_ee.netlist_parity import terminal_inventory
        self.actual = terminal_inventory(self.actual['components'], nets)

    def test_ic_pin_swap_reports_both_affected_nets(self):
        self.change_membership(('X9', '1'), '82')
        self.change_membership(('X9', '2'), '81')
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(set(report['missing_nets']), {'input', 'output'})
        self.assertEqual(report['expected_net_names']['input'], 'VBUS')
        self.assertEqual(report['expected_net_names']['output'], 'VCC')

    def test_merge_and_off_pin_binding_are_not_hidden_by_name(self):
        self.actual['nets']['82']['terminals'] += self.actual['nets'].pop('81')['terminals']
        from agent_ee.netlist_parity import terminal_inventory
        self.actual.update(terminal_inventory(self.actual['components'], self.actual['nets']))
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertTrue(any(set(m['expected']) == {'input', 'output'} for m in report['merged_nets']))
        self.setUp()
        self.change_membership(('R80', '1'), None)
        self.actual['nets']['81']['name'] = 'VBUS'
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertTrue(any(b['component'] == 'series' and b['pin'] == '1' and
            b['kind'] == 'missing_binding' for b in report['bindings']))

    def test_identical_resistor_incidence_mutation_is_not_collapsed(self):
        self.change_membership(('R90', '1'), '81')
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(len(report['native_components']), 3)

    def test_exact_and_at_least_component_multiplicity(self):
        import copy
        self.actual['components']['R100'] = copy.deepcopy(self.actual['components']['R90'])
        report = self.compare(predicate='components')
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertEqual(report['extra_components'][0]['count'], 1)
        self.assertEqual(self.compare(predicate='components', multiset='at_least')['outcome'], 'pass')
        del self.actual['components']['R100']
        del self.actual['components']['R90']
        self.assertEqual(self.compare(predicate='components', multiset='at_least')['outcome'], 'defect')

    def test_unresolvable_keys_and_exhausted_search_never_pass(self):
        self.assertEqual(self.compare(max_search=1)['outcome'], 'unknown')
        self.actual['components']['R80']['pins'] = ('1', '2', '9')
        self.assertEqual(self.compare()['outcome'], 'unknown')

    def test_symmetric_instances_need_anchors_for_instance_predicate(self):
        # Two parallel resistors are indistinguishable under every graph predicate.
        from agent_ee.netlist_parity import terminal_inventory
        for graph, a, b in [(self.expected, 'series', 'shunt'), (self.actual, 'R80', 'R90')]:
            graph['nets'] = {str(i): {'name': str(i), 'locator': str(i),
                'terminals': ((a, str(i)), (b, str(i)), ('port' if graph is self.expected else 'X9', str(i)))}
                for i in (1, 2)}
            # Port pin3 is explicitly unconnected on both sides.
            graph.update(terminal_inventory(graph['components'], graph['nets']))
        self.assertEqual(self.compare()['outcome'], 'pass')
        report = self.compare(predicate='instance', instance_components=['series'])
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'), report)
        self.assertEqual(report['component_matches']['series'], ['R80', 'R90'])
        anchored = self.compare(predicate='instance', instance_components=['series'],
                                anchors={'series': {'sheet': '/', 'uuid': 'series-uuid'}})
        self.assertEqual(anchored['outcome'], 'pass', anchored)

    def test_no_connect_normalization_cannot_hide_a_required_identical_part_binding(self):
        from agent_ee.netlist_parity import terminal_inventory
        self.expected['nets']['output']['terminals'] = tuple(t for t in self.expected['nets']['output']['terminals']
            if t != ('shunt', '1'))
        self.expected.update(terminal_inventory(self.expected['components'], self.expected['nets']))
        self.expected['no_connects'] = (('shunt', '1'),)
        self.change_membership(('R80', '1'), None)
        self.actual['nets']['nc'] = {'name': 'unconnected-(R80-Pad1)', 'locator': 'native unconnected pin',
            'terminals': (('R80', '1'),), 'auto_unconnected_singleton': True, 'no_connect_marker': False}
        self.actual.update(terminal_inventory(self.actual['components'], self.actual['nets']))
        report = self.compare()
        self.assertEqual(report['outcome'], 'defect', report)
        self.assertIn('input', report['missing_nets'])

    def test_maximum_terminal_fanout_retains_exact_multiplicity(self):
        from agent_ee.netlist_parity import compare, terminal_inventory
        graphs = []
        for prefix in ['logical', 'native']:
            components = {f'{prefix}-{i}': {'lib_id': 'Owned:Many', 'value': 'same', 'populated': True,
                'pins': tuple(str(p) for p in range(64)), 'locator': str(i)} for i in range(128)}
            nets = {'one-conductor': {'name': prefix, 'locator': prefix,
                'terminals': tuple((cid, pin) for cid, comp in components.items() for pin in comp['pins'])}}
            graphs.append(terminal_inventory(components, nets))
        report = compare(*graphs, {'predicate': 'topology', 'multiset': 'exactly', 'anchors': {}})
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual(len(report['component_matches']), 128)
        self.assertLessEqual(report['search_steps'], 100000)
        self.assertEqual(report['scores']['jaccard'], 1.0)


class NativeParityTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-netlist-parity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', params=None, golden_fixture='native.xml'):
        import importlib.util
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import write_json
        spec = importlib.util.spec_from_file_location('parity_fixture_generator', FIXTURE / 'generate.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        self.counter += 1
        case = self.root / str(self.counter)
        case.mkdir()
        (case / 'parity.kicad_sch').write_text(generator.schematic(variant))
        (case / 'golden.xml').write_bytes((FIXTURE / golden_fixture).read_bytes())
        if policy_source := (params or {}).get('source'):
            if policy_source['kind'] in ('intent', 'resolved'):
                for name in [policy_source['path'], policy_source.get('intent'), 'Owned.kicad_sym']:
                    if name:
                        (case / name).write_bytes((FIXTURE / name).read_bytes())
        (case / 'requirements.md').write_text('Parity shall preserve the declared topology.\n')
        policy = {'source': {'kind': 'golden_xml', 'path': 'golden.xml'}, 'predicate': 'topology',
                  'multiset': 'exactly', 'netlist_check': 'netlist', **(params or {})}
        scope = {'schema': 'agent-ee.scope/3', 'name': 'parity', 'scope': 'Owned native fixture.',
            'limitations': [], 'schematic': 'parity.kicad_sch',
            'inputs': [{'path': n, 'at': n} for n in ['parity.kicad_sch', 'golden.xml', 'requirements.md']],
            'requirements': ['requirements.md'],
            'checks': [{'id': 'netlist', 'kind': 'netlist'},
                       {'id': 'parity', 'kind': 'netlist_parity', 'params': policy}],
            'baseline': {'requirements': [{'id': 'parity', 'statement': 'Preserve topology.',
                'source': {'path': 'requirements.md', 'locator': 'line 1'},
                'checks': ['netlist', 'parity'], 'intent': 'acceptance'}],
                'targets': ['requirements.md'], 'reference_architectures': ['golden.xml']}}
        if policy['source']['kind'] in ('intent', 'resolved'):
            scope['inputs'] += [{'path': n, 'at': n} for n in [policy['source']['path'], 'Owned.kicad_sym']]
        if 'intent' in policy['source']:
            scope['inputs'].append({'path': policy['source']['intent'], 'at': policy['source']['intent']})
        write_json(case / 'scope.json', scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 90)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return run, checks

    def test_actual_kicad_exports_match_precommitted_expectations(self):
        expected = json.loads((FIXTURE / 'native-expectations.json').read_text())
        for variant, outcome in expected.items():
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant)
                self.assertEqual(checks[1]['execution'], 'ok', checks)
                self.assertEqual(checks[1]['outcome'], outcome, checks)

    def test_native_report_and_xml_are_sealed_and_intent_immutable(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses
        from agent_ee.candidate import verify_snapshot
        run, checks = self.run_variant('ic-swap')
        self.assertEqual(checks[1]['outcome'], 'defect', checks)
        freeze_evidence(run, checks)
        witness = [{'requirement_id': 'parity', 'check_id': 'parity'}]
        citation = [{'path': 'checks/parity.json', 'locator': 'missing_nets'}]
        validate_witnesses(run, witness, citation)
        for rel in ['work/parity/parity.json', 'work/netlist/design.xml']:
            path = run / rel
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                verify_evidence(run, checks, required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witness, citation)
            path.write_bytes(original)
        (run / 'inputs/golden.xml').write_text('<export/>')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_runtime_missing_malformed_and_failed_producer_are_unknown(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json, write_json
        run, _ = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        source, xml = run / 'inputs/golden.xml', run / 'work/netlist/design.xml'
        original_source, original_xml = source.read_bytes(), xml.read_bytes()
        for path, content, execution in [(source, None, 'failed'), (source, b'<broken', 'malformed'),
                                          (xml, b'<broken', 'malformed')]:
            with self.subTest(path=path, content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
                with self.assertRaises(CheckError) as raised:
                    check_netlist_parity(ctx)
                self.assertEqual(raised.exception.execution, execution)
                self.assertEqual(ctx.result['outcome'], 'unknown')
                source.write_bytes(original_source)
                xml.write_bytes(original_xml)
        producer_path = run / 'checks/netlist.json'
        producer = read_json(producer_path)
        producer.update(execution='failed', outcome='unknown')
        write_json(producer_path, producer)
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_capability_baselines_and_whole_check_witness_scope(self):
        import copy
        from agent_ee.baseline import compile_baseline
        from agent_ee.storage import read_json
        run, _ = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        baseline = compile_baseline(scope, manifest['files'], run / 'inputs')
        self.assertIn('golden.xml', baseline['files'])
        descriptor = baseline['coverage']['checks']['parity']
        self.assertEqual(descriptor['predicates'], ['netlist-parity:topology'])
        self.assertEqual(descriptor['status'], 'supported')
        legacy = copy.deepcopy(scope)
        legacy['schema'] = 'agent-ee.scope/2'
        del legacy['baseline']['requirements'][0]['intent']
        self.assertIn('golden.xml', compile_baseline(legacy, manifest['files'], run / 'inputs')['files'])
        scope['baseline']['requirements'].append({**scope['baseline']['requirements'][0], 'id': 'unrelated'})
        with self.assertRaisesRegex(ValueError, 'one requirement'):
            compile_baseline(scope, manifest['files'], run / 'inputs')
        scope['baseline']['requirements'].pop()
        scope['checks'].reverse()
        with self.assertRaisesRegex(ValueError, 'forward'):
            compile_baseline(scope, manifest['files'], run / 'inputs')

    def test_actual39_intent_and_resolved_adapters_pass_native_exports(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence, load_baseline
        from agent_ee.storage import read_json
        for source in [{'kind': 'intent', 'path': 'intent.json'},
                       {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}]:
            with self.subTest(kind=source['kind']):
                run, checks = self.run_variant(params={'source': source})
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)
                self.assertEqual(checks[1]['details']['scores']['jaccard'], 1.0)
                resolved = read_json(run / 'work/parity/resolved-intent.json')
                from agent_ee.connectivity_ir import validate_resolved
                validate_resolved(resolved)
                self.assertEqual(checks[1]['details']['intent_sha256'], resolved['intent_sha256'])
                self.assertIn('Owned.kicad_sym', load_baseline(run)['files'])
                self.assertIn('intent.json', load_baseline(run)['files'])
                self.assertEqual(checks[1]['details']['source_provenance'],
                                 'resolved_from_captured_intent' if source['kind'] == 'intent'
                                 else 'captured_resolution_rederived_from_captured_intent')
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)

    def test_authored_resigned_resolution_cannot_supply_graph_authority(self):
        # Independent of the original provenance probe: a structurally valid,
        # self-hash-consistent record that the captured intent does not derive.
        import hashlib
        import time
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.connectivity_ir import canonical_resolved, validate_resolved
        from agent_ee.storage import read_json, write_json
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, checks = self.run_variant(params={'source': source})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        def resign(record):
            payload = {k: v for k, v in record.items() if k != 'canonical_sha256'}
            record['canonical_sha256'] = hashlib.sha256(canonical_resolved(payload)).hexdigest()
            return validate_resolved(record)
        def value(record):
            record['components'][0]['value'] = 'AUTHORED-WRONG-VALUE'
        def pin_type(record):
            record['components'][0]['pins'][0]['electrical_type'] = 'power_in'
        def net_order(record):
            record['nets'].reverse()
        def pin_source(record):
            record['components'][0]['pins'][0]['source']['unit_symbol'] = 'Forged_1_1'
        def intent_identity(record):
            record['intent_sha256'] = hashlib.sha256(b'another intent').hexdigest()
        for mutate in [value, pin_type, net_order, pin_source, intent_identity]:
            with self.subTest(mutation=mutate.__name__):
                record = read_json(FIXTURE / 'resolved.json')
                mutate(record)
                write_json(run.parent / 'resolved.json', resign(record))
                forged = run.parent / f'forged-{mutate.__name__}'
                forged.mkdir()
                capture(run.parent / 'scope.json', forged)
                checks = run_checks(forged, time.monotonic() + 90)
                self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('malformed', 'unknown'), checks)
                self.assertIn('rederivation', checks[1]['summary'])
                self.assertEqual(checks[1]['details'].get('evaluated_predicates', []), [])
                freeze_evidence(forged, checks)
                with self.assertRaises(ValueError):
                    validate_witnesses(forged, [{'requirement_id': 'parity', 'check_id': 'parity'}],
                                       [{'path': 'checks/parity.json', 'locator': 'missing_components'}])

    def test_resolved_source_without_captured_intent_is_not_supported(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.netlist_parity import validate_policy
        good = {'predicate': 'topology', 'netlist_check': 'netlist', 'multiset': 'exactly'}
        with self.assertRaisesRegex(ValueError, 'captured intent'):
            validate_policy({**good, 'source': {'kind': 'resolved', 'path': 'resolved.json'}})
        for kind in ('golden_xml', 'intent'):
            with self.assertRaises(ValueError):
                validate_policy({**good, 'source': {'kind': kind, 'path': 'x', 'intent': 'intent.json'}})
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, _ = self.run_variant(params={'source': source})
        from agent_ee.storage import read_json
        manifest = read_json(run / 'candidate.json')
        files = dict(manifest['files'])
        del files['intent.json']
        with self.assertRaisesRegex(ValueError, 'captured'):
            compile_baseline(manifest['scope'], files, run / 'inputs')

    def test_captured_intent_or_library_drift_is_rejected_by_snapshot_and_rederivation(self):
        import time
        from agent_ee.candidate import verify_snapshot
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json, write_json
        source = {'kind': 'resolved', 'path': 'resolved.json', 'intent': 'intent.json'}
        run, checks = self.run_variant(params={'source': source})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        intent = read_json(run / 'inputs/intent.json')
        intent['components'][0]['value'] = 'changed'
        write_json(run / 'inputs/intent.json', intent)
        with self.assertRaises(ValueError):
            verify_snapshot(run)
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
        self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_actual39_corrupt_library_is_failed_unknown_with_resolution_evidence(self):
        import time
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.netlist_parity import check_netlist_parity
        from agent_ee.storage import read_json
        run, checks = self.run_variant(params={'source': {'kind': 'intent', 'path': 'intent.json'}})
        self.assertEqual(checks[1]['outcome'], 'pass', checks)
        (run / 'inputs/Owned.kicad_sym').write_text('corrupt captured library')
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_netlist_parity(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        self.assertEqual(ctx.result['outcome'], 'unknown')
        self.assertIn('work/parity/resolved-intent.json', ctx.result['evidence'])
        self.assertEqual(read_json(run / 'work/parity/resolved-intent.json')['execution'], 'failed')

    def test_native_extra_part_allowed_only_by_at_least_multiset(self):
        _, checks = self.run_variant('extra', params={'predicate': 'components', 'multiset': 'at_least'})
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)
        self.assertEqual(checks[1]['details']['extra_components'][0]['count'], 1)

    def test_independent_initial_reviews_then_central_adjudication(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import runtime_files, run_review, verify
        from agent_ee.storage import digest, read_json, write_json
        from test_capability_pipeline import NativeCapabilityPipelineTests, PROFILE_CALL
        self.calls = []
        runtime = runtime_files()
        for key in ('codex', 'codex_tool_host'):
            runtime[key] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        for variant, params, expected in [('control', {}, 'criteria_satisfied'),
                ('ic-swap', {}, 'defects_or_unresolved'),
                ('control', {'anchors': {'R80': {'sheet': '/', 'uuid': 'unresolved'}}}, 'incomplete')]:
            with self.subTest(variant=variant, expected=expected):
                first, _ = self.run_variant(variant, params=params)
                scope_path = first.parent / 'scope.json'
                scope = read_json(scope_path)
                (first.parent / 'parity.kicad_pcb').write_bytes((Path(__file__).parents[1] / 'fixtures/hardware-interface/interface.kicad_pcb').read_bytes())
                scope['board'] = 'parity.kicad_pcb'
                scope['inputs'].append({'path': 'parity.kicad_pcb', 'at': 'parity.kicad_pcb'})
                scope['review_profile'] = {'schema': 'agent-ee.profile/1', 'name': 'scripted-parity',
                    'reviewers': [{**PROFILE_CALL, 'role': 'circuit'}, {**PROFILE_CALL, 'role': 'physical'}],
                    'adjudicator': PROFILE_CALL}
                write_json(scope_path, scope)
                def agent(*args, **kwargs):
                    return NativeCapabilityPipelineTests.agent(self, *args, **kwargs)
                out = first.parent / 'review'
                with patch('agent_ee.cli.runtime_files', return_value=runtime), \
                     patch('agent_ee.reviewers.dispatch', side_effect=agent), \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = run_review(scope_path, out, 120)
                self.assertNotIn('error', read_json(out / 'result.json'))
                with patch('agent_ee.cli.runtime_files', return_value=runtime):
                    result = verify(out)
                self.assertEqual(result['outcome'], expected, result)
                self.assertEqual(status, 0 if expected == 'criteria_satisfied' else 1, result)
                self.assertCountEqual([role for _, role in self.calls[-3:-1]], ['circuit', 'physical'])
                self.assertEqual(self.calls[-1][1], 'adjudicator')
                accepted = [f for f in result['findings'] if f.get('status') == 'accepted']
                self.assertEqual([f['local_id'] for f in accepted], ['parity'] if variant == 'ic-swap' else [])
                self.assertTrue(all(f['source'] == 'check' for f in accepted))

    def test_native_symmetric_instances_unknown_until_declared_uuid_anchor(self):
        from uuid import NAMESPACE_URL, uuid5
        params = {'predicate': 'instance', 'instance_components': ['R80']}
        _, checks = self.run_variant('symmetric', params=params, golden_fixture='symmetric.xml')
        report = checks[1]
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'), report)
        self.assertEqual(report['details']['component_matches']['R80'], ['R80', 'R90'])
        self.assertTrue(report['details']['topology_equivalent'])
        # This immutable UUID is declared before export from the original owned fixture identity.
        params['anchors'] = {'R80': {'sheet': '/',
            'uuid': str(uuid5(NAMESPACE_URL, 'agent-ee-owned-netlist-parity/R80'))}}
        _, checks = self.run_variant('symmetric', params=params, golden_fixture='symmetric.xml')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks)

    def test_declared_unconnected_pins_accept_only_compatible_auto_singletons(self):
        for variant in ('no-connects', 'unconnected'):
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant, params={
                    'source': {'kind': 'intent', 'path': 'nc-intent.json'}})
                report = checks[1]
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
                normalized = report['details']['normalized_no_connects']
                self.assertEqual(len(normalized), 2)
                self.assertTrue(all(n['native_no_connect_marker'] == (variant == 'no-connects') for n in normalized))
                self.assertEqual(report['details']['scores']['jaccard'], 1.0)


class FailureBoundaryTests(unittest.TestCase):
    def test_native_duplicate_id_pin_membership_and_entities_are_malformed(self):
        import copy
        import tempfile
        import xml.etree.ElementTree as ET
        from agent_ee.native_netlist import read_native_inventory
        def dup_ref(root):
            root.find('components').append(copy.deepcopy(root.find('./components/comp')))
        def dup_uuid(root):
            comps = root.findall('./components/comp')
            comps[1].find('tstamps').text = comps[0].findtext('tstamps')
        def dup_pin(root):
            pins = root.find('./libparts/libpart/pins')
            pins.append(copy.deepcopy(pins[0]))
        def dup_membership(root):
            root.findall('./nets/net')[1].append(copy.deepcopy(root.find('./nets/net/node')))
        def dup_net(root):
            root.find('nets').append(copy.deepcopy(root.find('./nets/net')))
        def undeclared_unit_pin(root):
            root.find('./components/comp/units/unit/pins/pin').set('num', '999')
        for mutate in [dup_ref, dup_uuid, dup_pin, dup_membership, dup_net, undeclared_unit_pin]:
            with self.subTest(mutation=mutate.__name__), tempfile.TemporaryDirectory() as tmp:
                tree = ET.parse(FIXTURE / 'native.xml')
                mutate(tree.getroot())
                path = Path(tmp) / 'native.xml'
                tree.write(path)
                with self.assertRaises(ValueError):
                    read_native_inventory(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.xml'
            path.write_text('<!DOCTYPE export [<!ENTITY e "expanded">]><export version="E">&e;</export>')
            with self.assertRaisesRegex(ValueError, 'entities'):
                read_native_inventory(path)

    def test_resolved_inventory_rejects_duplicate_and_unaccounted_pins(self):
        import copy
        record = json.loads((FIXTURE / 'resolved.json').read_text())
        for mutation in [lambda r: r['components'].append(copy.deepcopy(r['components'][0])),
                         lambda r: r['nets'][0]['terminals'].append(copy.deepcopy(r['nets'][0]['terminals'][0])),
                         lambda r: r['nets'][0]['terminals'].pop(),
                         lambda r: r['no_connects'].append({'component': 'series', 'pin': '1'})]:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(record)
                mutation(changed)
                import hashlib
                payload = {k: v for k, v in changed.items() if k != 'canonical_sha256'}
                changed['canonical_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'inventory|terminal|pin|component|net'):
                    resolved_inventory(changed)

    def test_input_bounds_fail_unknown_before_unbounded_reads_or_resolution(self):
        import tempfile
        from unittest.mock import patch
        from agent_ee.netlist_parity import _source_inventory, Unsupported, read_json_source
        from agent_ee.native_netlist import read_native_inventory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.loads((FIXTURE / 'intent.json').read_text())
            (root / 'intent.json').write_text(json.dumps(raw))
            (root / 'Owned.kicad_sym').write_bytes(b'x' * 100)
            with patch('agent_ee.netlist_parity.MAX_LIBRARY_BYTES', 64), self.assertRaisesRegex(Unsupported, 'library byte'):
                _source_inventory(root, {'source': {'kind': 'intent', 'path': 'intent.json'}})
            raw['components'] *= 129
            (root / 'intent.json').write_text(json.dumps(raw))
            with self.assertRaisesRegex(Unsupported, 'component inventory'):
                _source_inventory(root, {'source': {'kind': 'intent', 'path': 'intent.json'}})
            with patch('agent_ee.netlist_parity.MAX_BYTES', 8), self.assertRaisesRegex(Unsupported, 'byte limit'):
                read_json_source(root / 'intent.json')
            with patch('agent_ee.native_netlist.MAX_BYTES', 8), self.assertRaisesRegex(Unsupported, 'byte limit'):
                read_native_inventory(FIXTURE / 'native.xml')

    def test_policy_rejects_hidden_reference_colors_and_unbounded_search(self):
        from agent_ee.netlist_parity import validate_policy
        good = {'source': {'kind': 'golden_xml', 'path': 'golden.xml'}, 'predicate': 'topology',
                'netlist_check': 'netlist', 'multiset': 'exactly'}
        for patch in [{'anchors': {'series': {'reference': 'R80'}}}, {'max_search': 0},
                      {'max_search': True}, {'max_search': 100001}, {'multiset': 'at_least'},
                      {'source': {'kind': 'guessed', 'path': 'golden.xml'}}, {'predicate': 'instance'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_policy({**good, **patch})

    def test_json_duplicate_fields_fail_closed(self):
        import tempfile
        from agent_ee.netlist_parity import read_json_source
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'intent.json'
            path.write_text('{"schema":"one","schema":"two"}')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                read_json_source(path)
