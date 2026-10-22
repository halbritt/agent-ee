"""Control-derived RC stage boundaries: pcbnew derivation, independent expectations, native checks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agent_ee import baseline, candidate, checks, routing_adapter, stage_receipts
from agent_ee.native_netlist import read_native_inventory
from agent_ee.placement_probe import extract
from agent_ee.storage import digest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / 'fixtures/rc/control'
FIXTURES = ROOT / 'fixtures/rc/derived'
RECEIPT = FIXTURES / 'receipts/generation'
SPEC = importlib.util.spec_from_file_location('rc_derived_generator', FIXTURES / 'generate.py')
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)

CONTROL_MM = {'J1': (100, 100), 'R1': (105, 100), 'C1': (110, 100)}


def positions_mm(variant):
    return {**CONTROL_MM, **(generator.STAGING if variant == 'unplaced' else
                             generator.SEEDS if variant == 'seeded-placement' else {})}


class FixtureDeclarations(unittest.TestCase):
    def test_hashes_sources_and_requirement_oracles_are_committed_inputs(self):
        hashes = json.loads((RECEIPT / 'fixture-hashes.json').read_text())
        actual = {str(p.relative_to(FIXTURES)): digest(p)
                  for name in generator.VARIANTS for p in (FIXTURES / name).rglob('*') if p.is_file()}
        self.assertEqual(actual, hashes)
        # The receipt names the committed generator and the control it read.
        sources = json.loads((RECEIPT / 'sources.json').read_text())['sources']
        self.assertEqual(sources, {'generate.py': digest(FIXTURES / 'generate.py'),
                                   **{f'control/{p.name}': digest(p) for p in CONTROL.iterdir()}})
        record = json.loads((RECEIPT / 'execution.json').read_text())
        self.assertEqual((record['execution'], record['exit_code']), ('ok', 0))
        self.assertIn('--unshare-net', record['sandbox_argv'])
        for name in generator.VARIANTS:
            for copied in generator.COPIED:
                self.assertEqual((FIXTURES / name / copied).read_bytes(), (CONTROL / copied).read_bytes())
        expectations = json.loads((FIXTURES / 'expectations.json').read_text())
        self.assertEqual(set(expectations), set(generator.VARIANTS))
        for name in generator.VARIANTS:
            scope = json.loads((FIXTURES / f'{name}.scope.json').read_text())
            self.assertEqual(scope['schema'], 'agent-ee.scope/2')
            self.assertEqual(scope['inputs'][0], {'path': name, 'at': 'project'})
            self.assertEqual(set(expectations[name]), {c['id'] for c in scope['checks']})
            self.assertEqual({c for r in scope['baseline']['requirements'] for c in r['checks']},
                             set(expectations[name]))
            self.assertEqual(scope['mutable_inputs'], ['project/rc_filter.kicad_pcb'])
            for req in scope['baseline']['requirements']:
                text = (FIXTURES / req['source']['path']).read_text()
                self.assertIn(req['source']['locator'], text)
                cid, = req['checks']
                self.assertIn(f'Expected initial outcome: `{expectations[name][cid]}`', text)


class NativeDerivedFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rc-derived-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_sandbox_derivation_reproduces_every_committed_fixture_byte(self):
        generated = generator.generate(self.root / 'generated')
        expected = json.loads((RECEIPT / 'fixture-hashes.json').read_text())
        actual = {str(p.relative_to(generated)): digest(p) for p in generated.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        work = generated.parent / 'generation'
        self.assertEqual(json.loads((work / 'edits.json').read_text()),
                         json.loads((RECEIPT / 'edits.json').read_text()))
        self.assertEqual(json.loads((work / 'sources.json').read_text()),
                         json.loads((RECEIPT / 'sources.json').read_text()))
        record = json.loads((work / 'execution.json').read_text())
        self.assertEqual((record['execution'], record['exit_code']), ('ok', 0))
        self.assertIn('--unshare-net', record['sandbox_argv'])
        with self.assertRaises(FileExistsError):
            generator.generate(generated.parent)

    def test_derivatives_keep_control_identity_and_change_only_the_stage_boundary(self):
        import pcbnew as k
        control = extract(CONTROL / 'rc_filter.kicad_pcb')
        identity = lambda f: (f['ref'], f['uuid'], f['value'], f['fpid'], f['side'], f['rotation_deg'],
                              [(p['number'], p['net'], p['uuid'], p['size_nm']) for p in f['pads']])
        self.assertEqual({f['ref']: f['position_nm'] for f in control['footprints']},
                         {ref: [x * 1_000_000, y * 1_000_000] for ref, (x, y) in CONTROL_MM.items()})
        source = k.LoadBoard(str(CONTROL / 'rc_filter.kicad_pcb'))
        source_zone, = source.Zones()
        self.assertTrue(source_zone.IsFilled())
        self.assertEqual(len(source.GetTracks()), 6)
        for name in generator.VARIANTS:
            with self.subTest(variant=name):
                path = FIXTURES / name / 'rc_filter.kicad_pcb'
                fact = extract(path)
                self.assertEqual(fact['outline'], control['outline'])
                self.assertEqual(sorted(map(identity, fact['footprints'])),
                                 sorted(map(identity, control['footprints'])))
                self.assertEqual({f['ref']: f['position_nm'] for f in fact['footprints']},
                                 {ref: [round(x * 1e6), round(y * 1e6)]
                                  for ref, (x, y) in positions_mm(name).items()})
                board = k.LoadBoard(str(path))
                self.assertEqual(len(board.GetTracks()), 0)
                zones = list(board.Zones())
                if name == 'unplaced':
                    self.assertEqual(zones, [])
                else:
                    zone, = zones
                    self.assertEqual(zone.m_Uuid.AsString(), source_zone.m_Uuid.AsString())
                    self.assertEqual((zone.GetNetname(), zone.GetLayer()), ('GND', k.B_Cu))
                    self.assertFalse(zone.IsFilled())
                    self.assertEqual([tuple(zone.Outline().CVertex(i)) for i in range(zone.GetNumCorners())],
                                     [tuple(source_zone.Outline().CVertex(i))
                                      for i in range(source_zone.GetNumCorners())])

    def test_checks_only_boundaries_have_real_native_defects_and_sealed_evidence(self):
        expected = json.loads((FIXTURES / 'expectations.json').read_text())
        for name in generator.VARIANTS:
            with self.subTest(variant=name):
                run = self.root / name
                run.mkdir()
                candidate.capture(FIXTURES / f'{name}.scope.json', run)
                results = checks.run_checks(run, time.monotonic() + 300)
                self.assertEqual({r['id']: (r['execution'], r['outcome']) for r in results},
                                 {cid: ('ok', outcome) for cid, outcome in expected[name].items()})
                baseline.freeze_evidence(run, results)
                baseline.verify_evidence(run, results, required=True)
                candidate.verify_snapshot(run)
                report = json.loads((run / 'work/drc-final/drc.json').read_text())
                self.assertGreater(len(report['unconnected_items']), 0)
                self.assertEqual(report['schematic_parity'], [])
                overlap = [v for v in report['violations'] if v['type'] == 'courtyards_overlap']
                if name == 'seeded-placement':
                    self.assertEqual(len(overlap), 1)
                    descriptions = ' '.join(i['description'] for i in overlap[0]['items'])
                    self.assertIn('R1', descriptions)
                    self.assertIn('C1', descriptions)
                    edge = [v for v in report['violations'] if v['type'] == 'copper_edge_clearance']
                    self.assertTrue(edge)
                    self.assertTrue(all('of J1' in ' '.join(i['description'] for i in v['items'])
                                        for v in edge))
                    baseline.validate_witnesses(run, [{'requirement_id': 'R-courtyard', 'check_id': 'courtyard'}],
                                                [{'path': 'checks/courtyard.json'}])
                else:
                    self.assertEqual(report['violations'], [])
                inventory = read_native_inventory(run / 'work/netlist/design.xml')
                self.assertEqual(set(inventory['components']), {'R1', 'C1', 'J1'})
                self.assertEqual({n['name']: sorted(n['terminals']) for n in inventory['nets'].values()}, {
                    '/IN': [('J1', '1'), ('R1', '1')],
                    '/OUT': [('C1', '1'), ('J1', '2'), ('R1', '2')],
                    'GND': [('C1', '2'), ('J1', '3')]})


class NativeRoutingInput(unittest.TestCase):
    def test_placed_unrouted_is_a_runnable_routing_input(self):
        # The ticket's 'krt' backend is now the alias of canonical 'builtin' (AGENTEE-343): the
        # default request and the legacy spelling both run the builtin engine and report it.
        require_toolchain(native=True)
        checkout = require_kicad_route()
        for declared in (None, 'krt'):
            with self.subTest(backend=declared), \
                    tempfile.TemporaryDirectory(prefix='agent-ee-rc-derived-route-') as tmp:
                root = Path(tmp)
                (root / 'captured').mkdir()
                candidate.capture(FIXTURES / 'placed-unrouted.scope.json', root / 'captured')
                spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                    schematic='rc_filter.kicad_sch', name='rc_filter', label='ee103', checkout=checkout,
                    budget=60, **({'backend': declared} if declared else {}))
                self.assertEqual(spec['argv'][spec['argv'].index('--backend') + 1], declared or 'builtin')
                receipt_dir = root / 'receipt'
                receipt = stage_receipts.run_step(receipt_dir, root / 'captured/inputs', spec,
                                                 {'implementation': 1, 'receipt_index': 1})
                self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
                summary = routing_adapter.verify(receipt_dir / 'run', receipt=receipt)
                result = json.loads((receipt_dir / 'run/routing/attempt/result.json').read_text())
                self.assertEqual(result['backend']['name'], 'builtin')
                self.assertIsInstance(summary['router_exit'], int)  # Recorded; exit 0 is not asserted.
                self.assertIn('routing', result)
                self.assertIn('validation', result)


class PrerequisitePolicy(unittest.TestCase):
    def without_tools(self, strict=False):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rc-derived-absent-') as tmp:
            env = dict(os.environ, PATH=tmp, PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests')]))
            env.pop('AGENT_EE_REQUIRE_TOOLCHAIN', None)
            if strict:
                env['AGENT_EE_REQUIRE_TOOLCHAIN'] = '1'
            return subprocess.run([sys.executable, '-m', 'unittest', '-v',
                                   'test_rc_derived.NativeDerivedFixtures', 'test_rc_derived.NativeRoutingInput'],
                                  cwd=ROOT / 'tests', env=env, capture_output=True, text=True, timeout=30)

    def test_missing_toolchain_is_a_standard_skip(self):
        result = self.without_tools()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OK (skipped=2)', result.stderr)
        self.assertIn('toolchain prerequisite: bwrap not found on PATH', result.stderr)

    def test_missing_required_toolchain_fails(self):
        result = self.without_tools(strict=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('AGENT_EE_REQUIRE_TOOLCHAIN=1: toolchain prerequisite: bwrap not found on PATH', result.stderr)
        self.assertNotIn('skipped=', result.stderr)


if __name__ == '__main__':
    unittest.main()
