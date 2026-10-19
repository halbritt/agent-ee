"""Selected CAN composition: native preservation, source custody and refusals."""
import copy
import importlib.util
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee.baseline import freeze_evidence, verify_evidence, load_baseline, validate_witnesses
from agent_ee.block_adaptation import _source, _instance, instantiate
from agent_ee.block_composition import validate
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.native_netlist import read_native_inventory
from agent_ee.reference_blocks import hierarchy_ports, validate_block
from agent_ee.schematic_emit import parse_quoted, format_sexpr
from agent_ee.storage import read_json, write_json, digest
from agent_ee.symbol_library import children
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/fome-mc/pod-can'
spec = importlib.util.spec_from_file_location('pod_can_compose', FIXTURE / 'compose.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


class CompositionContractTests(unittest.TestCase):
    def test_explicit_maps_and_refusal_boundaries(self):
        block = read_json(FIXTURE / 'block/block.json')
        plan = read_json(FIXTURE / 'composition.json')
        self.assertEqual(len(validate(plan, block)), 8)
        mutations = []
        missing = copy.deepcopy(plan); del missing['sources'][0]['references']['U307']; mutations.append(missing)
        duplicate = copy.deepcopy(plan); duplicate['sources'][1]['references']['D4'] = 'U307'; mutations.append(duplicate)
        identity = copy.deepcopy(plan); identity['sources'][1]['sha256'] = '0' * 64; mutations.append(identity)
        source = copy.deepcopy(plan); source['sources'][1] = copy.deepcopy(source['sources'][0]); mutations.append(source)
        vio = copy.deepcopy(plan); vio['sources'][0]['nets']['+3V3_WBO1'] = '+5V'; mutations.append(vio)
        alias = copy.deepcopy(plan); alias['ports'][4]['alias_of'] = None; mutations.append(alias)
        for bad in mutations:
            with self.subTest(plan=bad), self.assertRaises(ValueError):
                validate(bad, block)
        with self.assertRaisesRegex(ValueError, 'multiple source instances'):
            _source(FIXTURE, 'block')

    def test_bad_selected_reference_plan_leaves_destination_unchanged(self):
        from tests.test_block_adaptation import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope, request = fixture(root)
            shutil.copytree(FIXTURE / 'block', root / 'blocks/can')
            plan = read_json(FIXTURE / 'composition.json')
            del plan['sources'][0]['references']['U307']
            write_json(root / 'composition.json', plan)
            request.update(block='blocks/can', composition='composition.json')
            original = (root / request['sheet']).read_bytes()
            with self.assertRaisesRegex(ValueError, 'selected source references exactly'):
                instantiate(root, scope, request)
            self.assertEqual((root / request['sheet']).read_bytes(), original)
            self.assertFalse((root / request['record']).exists())


class NativeCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='agent-ee-selected-can-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.prepared = cls.root / 'prepared'
        compose.prepare(cls.prepared)

    def setUp(self):
        self.work = self.root / self._testMethodName
        self.work.mkdir()
        self.candidate = self.work / 'candidate'
        shutil.copytree(self.prepared, self.candidate)

    def checks(self, name='run'):
        run = self.work / name
        run.mkdir()
        capture(self.candidate / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 180)
        freeze_evidence(run, results)
        verify_evidence(run, results, required=True)
        by_id = {r['id']: r for r in results}
        self.assertEqual(by_id['netlist']['execution'], 'ok', by_id['netlist'])
        return run, by_id, read_native_inventory(run / 'work/netlist/design.xml')

    def test_four_parts_eight_ports_both_native_sources_and_interface(self):
        run, results, graph = self.checks()
        self.assertEqual(set(graph['components']), {'U307', 'C308', 'D4', 'R9'})
        self.assertEqual(len(graph['nets']), 7)  # Eight ports; S is explicitly grounded.
        self.assertEqual(set(hierarchy_ports((self.candidate / 'project/can.kicad_sch').read_bytes())), set(compose.BINDINGS))
        for cid in ('interface', 'provenance'):
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'pass'), results[cid])
        provenance = results['provenance']['details']
        self.assertEqual(len(provenance['composition']['sources']), 2)
        self.assertEqual(provenance['composition']['source_commit'], 'df304f3321296f7e517e0d2e2aa52e1380d52993')
        coverage = load_baseline(run)['coverage']['checks']['provenance']['predicates']
        self.assertTrue({'block-composition:components', 'block-composition:pin-nets'} <= set(coverage))
        self.assertEqual(provenance['component_changes'], [])
        self.assertEqual(provenance['net_changes'], [])
        self.assertEqual({p['port']: p['observed_net'] for p in provenance['boundary']}, compose.BINDINGS)
        for index in (0, 1):
            self.assertTrue((run / f'work/provenance/source-{index}/design.xml').is_file())
        self.assertEqual(graph['components']['R9']['properties']['LCSC'], 'C22787')
        self.assertEqual(graph['nets'][graph['memberships'][('U307', '8')]]['name'], 'GND')
        self.assertEqual(graph['nets'][graph['memberships'][('U307', '5')]]['name'], '+3V3')
        self.assertEqual(validate_block(self.candidate / 'block')['status'], 'unverified')

    def test_deterministic_emission_receipt_and_provenance_mapping(self):
        other = self.work / 'other'
        compose.prepare(other)
        for path in ('project/can.kicad_sch', 'instance.json', 'composition.json'):
            self.assertEqual((other / path).read_bytes(), (self.candidate / path).read_bytes())
        record = read_json(other / 'instance.json')
        self.assertEqual(record['composition']['emitted_sha256'], digest(other / 'project/can.kicad_sch'))
        plan = read_json(other / 'composition.json')
        self.assertEqual([s['references'] for s in plan['sources']], [{'C308': 'C308', 'U307': 'U307'}, {'D4': 'D4', 'R9': 'R9'}])

    def test_both_source_tampers_refused_at_capture(self):
        for sheet in ('wbo1', 'psu'):
            with self.subTest(sheet=sheet):
                path = self.candidate / f'block/sheets/{sheet}.kicad_sch'
                before = path.read_bytes()
                path.write_bytes(before + b'\n')
                try:
                    with self.assertRaisesRegex(ValueError, 'inventory/hash differs'):
                        _instance(self.candidate, 'instance.json')
                    run = self.work / ('tamper-' + sheet)
                    run.mkdir()
                    with self.assertRaises(ValueError):
                        capture(self.candidate / 'scope.json', run)
                finally:
                    path.write_bytes(before)

    def test_vio_5v_native_defect_and_provenance_defect(self):
        path = self.candidate / 'project/can.kicad_sch'
        tree = parse_quoted(path.read_bytes())
        # One pin label carries VIO; the port's label is a separate object.
        labels = [n for n in children(tree, 'label') if n[1] == '+3V3']
        self.assertEqual(len(labels), 2)
        labels[0][1] = type(labels[0][1])('+5V')
        path.write_text('\n'.join(format_sexpr(tree)) + '\n')
        _, results, graph = self.checks()
        self.assertEqual(graph['nets'][graph['memberships'][('U307', '5')]]['name'], '+5V')
        for cid in ('interface', 'provenance'):
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'defect'), results[cid])
        self.assertIn('U307-5-net', results['interface']['details']['violations'])
        self.assertTrue(any(n['terminal'] == ['U307', '5'] for n in results['provenance']['details']['net_changes']))

    def test_component_property_change_is_provenance_defect(self):
        path = self.candidate / 'project/can.kicad_sch'
        tree = parse_quoted(path.read_bytes())
        resistor = next(n for n in children(tree, 'symbol') if any(p[1:3] == ['Reference', 'R9'] for p in children(n, 'property')))
        value = next(p for p in children(resistor, 'property') if p[1] == 'LCSC')
        value[2] = type(value[2])('C999999')
        path.write_text('\n'.join(format_sexpr(tree)) + '\n')
        run, results, _ = self.checks()
        self.assertEqual(results['provenance']['outcome'], 'defect', results['provenance'])
        req = next(r for r in load_baseline(run)['requirements'] if r['id'] == 'can-boundary')
        self.assertIn('selected source component identities', req['statement'])
        validate_witnesses(run, [{'requirement_id': 'can-boundary', 'check_id': 'provenance'}],
                          [{'path': 'checks/provenance.json'}])
        self.assertEqual(results['provenance']['details']['component_changes'][0]['reference'], 'R9')

    def test_plan_tamper_and_independent_s_binding_refused(self):
        path = self.candidate / 'composition.json'
        before = path.read_bytes()
        path.write_bytes(before + b'\n')
        with self.assertRaisesRegex(ValueError, 'composition plan hash differs'):
            _instance(self.candidate, 'instance.json')
        path.write_bytes(before)
        record = read_json(self.candidate / 'instance.json')
        record['bindings']['S'] = '/INDEPENDENT_S'
        write_json(self.candidate / 'instance.json', record)
        with self.assertRaisesRegex(ValueError, 'alias binding differs'):
            _instance(self.candidate, 'instance.json')


if __name__ == '__main__':
    unittest.main()
