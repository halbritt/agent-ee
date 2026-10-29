"""Pinned wideband captures and fresh native adaptation, without source-checkout access."""
import copy
import importlib.util
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence
from agent_ee.block_inventory import (_projection, adapt_connectivity, compare_connectivity,
                                      validate_adaptation_fixture)
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.hardware_interface import read_inventory
from agent_ee.native_netlist import read_components
from agent_ee.reference_blocks import validate_block
from agent_ee.storage import digest, read_json
from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/fome-mc/wbo1'
COMMIT = 'df304f3321296f7e517e0d2e2aa52e1380d52993'
spec = importlib.util.spec_from_file_location('wideband_fixture', FIXTURE / 'adapt.py')
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


def native_projection(xml, instance):
    native = read_inventory(xml)
    components = read_components(xml)
    members = {ref: {'uuid_path': instance} for ref, c in components.items() if c['sheet'] == instance}
    return _projection(members, native, components, dict(commit=COMMIT, tool='native test export'))


class CaptureTests(unittest.TestCase):
    def test_hashes_identity_parts_ports_and_unverified_status(self):
        for channel in ('wbo1', 'wbo2'):
            with self.subTest(channel=channel):
                root = FIXTURE / 'blocks' / channel
                record = read_json(root / 'block.json')
                result = validate_block(root)
                self.assertEqual(result['status'], 'unverified')
                if channel == 'wbo1':
                    receipt = read_json(REPO / 'evidence/fome-block-review-runs/receipts/wbo1/result.json')
                    self.assertEqual(result['runs'], [dict(run_id=receipt['run_id'], status='unavailable')])
                    self.assertEqual(record['runs'][0]['candidate_id'], receipt['candidate_id'])
                else:
                    self.assertEqual(result['runs'], [])
                self.assertEqual(record['source']['commit'], COMMIT)
                self.assertEqual(set(record['source']['paths']), {record['sheet']})
                self.assertFalse((root / 'sources').exists())
                self.assertEqual(len(result['external_sources']), 4)
                for citation in result['external_sources']:
                    source = citation['source']
                    self.assertEqual(source['kind'], 'pinned-external')
                    self.assertEqual(source['commit'], COMMIT)
                    self.assertIn(source['path'], ('docs/DESIGN.md', 'docs/BLOCK_LIBRARY.md'))
                self.assertEqual(len(record['instances'][0]['references']), 58)
                self.assertEqual(len(read_json(root / 'parts.json')['parts']), 58)
                self.assertEqual(len(record['boundary_ports']), 10)
                self.assertTrue(all(p['current_a'] is None and p['voltage_domain'] is None
                                    for p in record['boundary_ports']))
                self.assertFalse(any(l['origin'] == 'unresolved' for l in record['libraries']))
                self.assertIn('fixtures/fome-mc/LICENSE-NOTICE.md', record['source']['license'])
                for token in ('T9', 'T6', 'ca2adce', 'MIT'):
                    self.assertIn(token, record['source']['upstream_origin'])
                for token in ('MIT', 'Matthew Kennedy', 'CC-BY-SA-4.0'):
                    self.assertIn(token, record['source']['upstream_license'])
                self.assertEqual(read_json(root / 'block-contract.json')['source'], record['source'])
                for path, expected in record['files'].items():
                    self.assertEqual(digest(root / path), expected)
        parts = {p['reference']: p for p in read_json(FIXTURE / 'blocks/wbo1/parts.json')['parts']}
        for ref, lcsc in {'U302': 'C69216', 'U301': 'C50282', 'U305': 'C29309',
                          'U303': 'C36658', 'U309': 'C5446', 'Q301': 'C2649436',
                          'U307': 'C38695', 'R310': 'C23092'}.items():
            self.assertEqual(parts[ref]['lcsc'], lcsc)
        self.assertEqual(parts['R310']['value'], '61.9')

    def test_undeclared_and_wrong_adaptations_remain_visible(self):
        fixture = read_json(FIXTURE / 'wbo1-to-wbo2.json')
        validate_adaptation_fixture(fixture)
        source = read_json(FIXTURE / 'blocks/wbo1/connectivity.json')
        target = read_json(FIXTURE / 'blocks/wbo2/connectivity.json')
        params = fixture['parameters']
        without = dict(params, straps=[])
        self.assertEqual(compare_connectivity(adapt_connectivity(source, without), target),
                         fixture['expected_without_straps'])
        self.assertEqual(compare_connectivity(adapt_connectivity(source, params), target), [])
        for wrong in (dict(params, reference_offset=200, straps=[]), dict(params, renames=[]),
                      dict(params, straps=[dict(reference='U402', pin='18', net='GND')])):
            self.assertTrue(compare_connectivity(adapt_connectivity(source, wrong), target))

    def test_changed_payload_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            block = Path(tmp) / 'block'
            shutil.copytree(FIXTURE / 'blocks/wbo1', block)
            sheet = block / 'sheets/wbo1.kicad_sch'
            sheet.write_bytes(sheet.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'inventory/hash differs'):
                validate_block(block)

    def test_fixture_writer_refuses_wrong_straps_and_changed_source_geometry(self):
        raw = (FIXTURE / 'blocks/wbo1/sheets/wbo1.kicad_sch').read_bytes()
        params = copy.deepcopy(read_json(FIXTURE / 'wbo1-to-wbo2.json')['parameters'])
        params['straps'][1]['net'] = 'GND'
        with self.assertRaisesRegex(ValueError, 'declared wbo2 strap pair'):
            writer.adapt_sheet(raw, params)
        params = read_json(FIXTURE / 'wbo1-to-wbo2.json')['parameters']
        from agent_ee.schematic_emit import parse_quoted
        from agent_ee.symbol_library import children
        tree = parse_quoted(raw)
        # Removing the pinned PB1 wire must refuse, rather than guess a strap.
        wire = next(w for w in children(tree, 'wire')
                    if any(
                        p[1:] == ['199.39', '93.345'] for p in children(children(w, 'pts')[0], 'xy')))
        tree.remove(wire)
        with self.assertRaisesRegex(ValueError, 'source strap geometry differs'):
            writer.adapt_sheet(writer.dump(tree), params)


class LegacyAdmissionTests(unittest.TestCase):
    def test_pending_capture_refuses_legacy_admission_without_publishing_instance(self):
        for straps in (False,True):
            with self.subTest(straps=straps),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'candidate'
                with self.assertRaisesRegex(ValueError,'pending cannot be dropped'):writer.prepare(root,straps=straps)
                self.assertFalse((root/'instance.json').exists())
                self.assertFalse((root/'scope.json').exists())
                self.assertEqual(read_json(root/'blocks/wbo1/block.json'),read_json(FIXTURE/'blocks/wbo1/block.json'))

    def test_probe_preserves_both_pending_rows_and_exact_topology_scope(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.requirement_map import input_inventory
        original=read_json(FIXTURE/'blocks/wbo1/block.json')
        for straps in (False,True):
            with self.subTest(straps=straps),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'probe';scope=writer.prepare_native_probe(root,straps=straps)
                coverage=read_json(root/'probe-coverage.json')
                self.assertEqual(coverage['pending_requirements'],original['pending_requirements'])
                self.assertEqual(coverage['evaluation'],'not_evaluated')
                self.assertEqual(read_json(root/'blocks/wbo1/block.json'),original)
                self.assertEqual({c['id'] for c in scope['checks']},{'erc','netlist','adapted-interface'})
                self.assertFalse((root/'instance.json').exists())
                self.assertEqual((root/'coverage-legacy.md').read_bytes(),(FIXTURE/'COVERAGE.md').read_bytes())
                self.assertTrue(compile_baseline(scope,input_inventory(root),root))


class NativeWidebandTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-wbo1-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def checks(self, scope, run):
        run.mkdir()
        manifest = capture(scope, run)
        checks = run_checks(run, time.monotonic() + 180)
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)
        return manifest, {c['id']: c for c in checks}

    def test_captured_native_boundary_straps_and_channel_isolation(self):
        run = self.root / 'run'
        _, checks = self.checks(FIXTURE / 'reference.scope.json', run)
        self.assertEqual((checks['netlist']['execution'], checks['netlist']['outcome']), ('ok', 'info'), checks['netlist']['summary'])
        self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']), ('ok', 'pass'), checks['interface']['summary'])
        graph = native_projection(run / 'work/netlist/design.xml', '/a1000000-0000-4000-8000-000000000004/')
        expected = read_json(FIXTURE / 'blocks/wbo1/connectivity.json')
        self.assertEqual(compare_connectivity(adapt_connectivity(graph, dict(reference_offset=0, renames=[], straps=[])), expected), [])
        self.assertIsNone(graph['components']['U302']['pins']['18'])
        self.assertEqual(graph['components']['U302']['pins']['15'], 'GND')
        self.assertEqual(load_baseline(run), read_json(FIXTURE / 'expected-baseline.json'))

    def test_native_regenerated_wbo2_topology_with_pending_block_unadmitted(self):
        for straps in (False, True):
            with self.subTest(straps=straps):
                candidate = self.root / ('strapped' if straps else 'without-straps')
                writer.prepare_native_probe(candidate, straps=straps)
                _, checks = self.checks(candidate / 'scope.json', candidate / 'run')
                self.assertEqual((checks['netlist']['execution'], checks['netlist']['outcome']), ('ok', 'info'), checks['netlist']['summary'])
                self.assertNotIn('provenance',checks)  # Pending block is never admitted by this probe.
                self.assertEqual((checks['adapted-interface']['execution'], checks['adapted-interface']['outcome']), ('ok', 'pass'), checks['adapted-interface']['summary'])
                self.assertNotEqual((candidate/'project/wbo2.kicad_sch').read_bytes(),(FIXTURE/'blocks/wbo1/sheets/wbo1.kicad_sch').read_bytes())
                graph = native_projection(candidate / 'run/work/netlist/design.xml', '/' + writer.INSTANCE_UUID + '/')
                target = read_json(FIXTURE / 'blocks/wbo2/connectivity.json')
                differences = compare_connectivity(adapt_connectivity(graph, dict(reference_offset=0, renames=[], straps=[])), target)
                expected = [] if straps else read_json(FIXTURE / 'wbo1-to-wbo2.json')['expected_without_straps']
                self.assertEqual(differences, expected)
                self.assertEqual(validate_block(candidate / 'blocks/wbo1')['status'], 'unverified')
                from agent_ee.reference_blocks import hierarchy_ports
                self.assertEqual(len(hierarchy_ports((candidate/'project/wbo2.kicad_sch').read_bytes())),10)


if __name__ == '__main__':
    unittest.main()
