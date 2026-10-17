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


class NativeRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def run_rules(self, document, **board_options):
        import json
        import tempfile
        import time
        from pathlib import Path
        from routing_fixture import make_board
        from agent_ee.checks import Ctx
        from agent_ee.routing_rules import check_routing_rules
        from agent_ee.storage import digest
        root = Path(tempfile.mkdtemp(prefix='agent-ee-routing-native-'))
        self.addCleanup(__import__('shutil').rmtree, root)
        inputs = root / 'inputs'; inputs.mkdir()
        make_board(inputs / 'test.kicad_pcb', **board_options)
        (inputs / 'rules.json').write_text(json.dumps(document))
        (inputs / 'requirements.txt').write_text('Owned test routing requirement.')
        files = {p.name: digest(p) for p in inputs.iterdir()}
        reqs = [{'id': rid, 'checks': ['routing'],
                 'source': {'path': 'requirements.txt', 'locator': 'Owned test'}}
                for rid in sorted({c['requirement'] for c in document['constraints']})]
        scope = {'board': 'test.kicad_pcb', 'baseline': {'requirements': reqs}}
        manifest = {'candidate_id': 'owned-test', 'scope': scope, 'files': files}
        ctx = Ctx(root, manifest, {'id': 'routing', 'kind': 'routing_rules',
                                 'params': {'routing_rules': 'rules.json'}}, time.monotonic()+900)
        check_routing_rules(ctx)
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
