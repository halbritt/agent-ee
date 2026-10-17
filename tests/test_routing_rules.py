"""Owned rule compiler examples; native fixtures are generated, never imported."""
import unittest
from agent_ee.routing_rules import compile_routing


def contract(kind='skew', **limits):
    return {'schema': 'agent-ee.routing-rules/1', 'constraints': [
        {'id': 'usb', 'requirement': 'usb-skew', 'kind': kind,
         'nets': ['D+', 'D-'], **limits}]}


class RoutingCompilerTest(unittest.TestCase):
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

    def run_rules(self, document, *, project=None, custom=None, **board_options):
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
            (inputs / 'test.kicad_pro').write_text(project)
        if custom is not None:
            (inputs / 'test.kicad_dru').write_text(custom)
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

    def test_requirement_check_mapping_must_be_exact(self):
        from agent_ee.routing_rules import validate_mappings
        compiled = compile_routing(contract(max_mm=0.15))
        with self.assertRaisesRegex(ValueError, 'mappings'):
            validate_mappings(compiled['constraints'], {'baseline': {'requirements': []}}, {}, 'routing')

    def test_descriptor_freezes_captured_native_sidecar_criteria(self):
        import json
        import tempfile
        from pathlib import Path
        from agent_ee.routing_rules import capability
        from agent_ee.storage import digest
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            for name, content in {'board.kicad_pcb': 'owned board', 'board.kicad_pro': '{}',
                                  'board.kicad_dru': '(version 1)', 'requirements.txt': 'owned',
                                  'rules.json': json.dumps(contract(max_mm=0.15))}.items():
                (inputs / name).write_text(content)
            files = {p.name: digest(p) for p in inputs.iterdir()}
            scope = {'board': 'board.kicad_pcb', 'baseline': {'requirements': [
                {'id': 'usb-skew', 'checks': ['routing'],
                 'source': {'path': 'requirements.txt', 'locator': 'Owned'}}]}}
            descriptor = capability({'routing_rules': 'rules.json'}, scope, files, inputs, 'routing')
            self.assertEqual(descriptor['immutable_inputs'], ['board.kicad_dru', 'board.kicad_pro', 'rules.json'])
            (inputs / 'board.kicad_pro').write_text('{"changed":true}')
            with self.assertRaises(ValueError):
                capability({'routing_rules': 'rules.json'}, scope, files, inputs, 'routing')

    def test_one_requirement_per_check_preserves_witness_authority(self):
        from agent_ee.routing_rules import validate_mappings
        doc = contract(max_mm=0.15)
        doc['constraints'].append({'id': 'clock', 'requirement': 'clock', 'kind': 'via_count',
                                   'nets': ['CLK'], 'max_count': 2})
        compiled = compile_routing(doc)
        scope = {'baseline': {'requirements': [
            {'id': rid, 'checks': ['routing'],
             'source': {'path': 'requirements.txt', 'locator': 'Owned'}}
            for rid in ('usb-skew', 'clock')]}}
        with self.assertRaisesRegex(ValueError, 'one requirement'):
            validate_mappings(compiled['constraints'], scope, {'requirements.txt': 'captured'}, 'routing')
