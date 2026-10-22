"""Original saved RC boundaries: independent expectations and real native checks."""
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
FIXTURES = ROOT / 'fixtures/rc/stages'
SPEC = importlib.util.spec_from_file_location('rc_stage_generator', FIXTURES / 'generate.py')
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class FixtureDeclarations(unittest.TestCase):
    def test_hashes_and_requirement_oracles_are_committed_inputs(self):
        hashes = json.loads((FIXTURES / 'receipts/generation/fixture-hashes.json').read_text())
        actual = {str(p.relative_to(FIXTURES)): digest(p)
                  for name in generator.VARIANTS for p in (FIXTURES / name).rglob('*') if p.is_file()}
        self.assertEqual(actual, hashes)
        expectations = json.loads((FIXTURES / 'expectations.json').read_text())
        self.assertEqual(set(expectations), set(generator.VARIANTS))
        for name in generator.VARIANTS:
            scope = json.loads((FIXTURES / f'{name}.scope.json').read_text())
            self.assertEqual(scope['schema'], 'agent-ee.scope/2')
            self.assertEqual(set(expectations[name]), {c['id'] for c in scope['checks']})
            self.assertEqual({c for r in scope['baseline']['requirements'] for c in r['checks']},
                             set(expectations[name]))
            self.assertEqual(scope['mutable_inputs'], ['project/rc_stage.kicad_pcb'])
            for req in scope['baseline']['requirements']:
                text = (FIXTURES / req['source']['path']).read_text()
                self.assertIn(req['source']['locator'], text)
                cid, = req['checks']
                self.assertIn(f'Expected initial outcome: `{expectations[name][cid]}`', text)


class NativeStageFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rc-stages-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_sandbox_generation_reproduces_every_committed_fixture_byte(self):
        generated = generator.generate(self.root / 'generated')
        expected = json.loads((FIXTURES / 'receipts/generation/fixture-hashes.json').read_text())
        actual = {str(p.relative_to(generated)): digest(p) for p in generated.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        record = json.loads((generated.parent / 'generation/execution.json').read_text())
        self.assertEqual((record['execution'], record['exit_code']), ('ok', 0))
        self.assertIn('--unshare-net', record['sandbox_argv'])
        # A second invocation must not replace its inputs or an earlier receipt.
        with self.assertRaises(FileExistsError):
            generator.generate(generated.parent)

    def test_native_geometry_preserves_the_network_and_stage_boundaries(self):
        facts = {name: extract(FIXTURES / name / 'rc_stage.kicad_pcb') for name in generator.VARIANTS}
        reference = facts['placed-unrouted']
        self.assertEqual(reference['outline']['status'], 'proven')
        self.assertEqual({f['ref']: f['position_nm'] for f in reference['footprints']},
                         {'R1': [20000000, 15000000], 'C1': [30000000, 15000000], 'J1': [5000000, 10000000]})
        self.assertEqual({f['ref']: f['value'] for f in reference['footprints']},
                         {'R1': '10k', 'C1': '100n', 'J1': 'RC port'})
        self.assertEqual({f['ref']: f['position_nm'] for f in facts['seeded-placement']['footprints']},
                         {'R1': [20000000, 15000000], 'C1': [20000000, 16500000], 'J1': [-500000, 10000000]})
        identity = lambda f: (f['uuid'], f['value'], f['fpid'], f['side'], f['rotation_deg'],
                              [(p['number'], p['net'], p['uuid'], p['size_nm']) for p in f['pads']])
        import pcbnew as k
        for name, fact in facts.items():
            with self.subTest(variant=name):
                self.assertEqual(fact['outline'], reference['outline'])
                self.assertEqual([identity(f) for f in fact['footprints']],
                                 [identity(f) for f in reference['footprints']])
                board = k.LoadBoard(str(FIXTURES / name / 'rc_stage.kicad_pcb'))
                self.assertEqual(len(board.GetTracks()), 0)
                self.assertEqual(len(board.Zones()), 0 if name == 'unplaced' else 1)
                for zone in board.Zones():
                    self.assertEqual(zone.GetNetname(), '/GND')
                    self.assertEqual(zone.GetLayer(), k.B_Cu)
                    self.assertFalse(zone.IsFilled())
                if name == 'unplaced':
                    self.assertEqual([f['position_nm'] for f in fact['footprints']],
                                     [[60000000, 10000000], [70000000, 10000000], [80000000, 10000000]])
                self.assertEqual((FIXTURES / name / 'rc_stage.kicad_sch').read_bytes(),
                                 (FIXTURES / 'placed-unrouted/rc_stage.kicad_sch').read_bytes())

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
                    baseline.validate_witnesses(run, [{'requirement_id': 'R-courtyard', 'check_id': 'courtyard'}],
                                                [{'path': 'checks/courtyard.json'}])
                else:
                    self.assertEqual(report['violations'], [])
                # The existing native reader, rather than another XML parser.
                inventory = read_native_inventory(run / 'work/netlist/design.xml')
                self.assertEqual(set(inventory['components']), {'R1', 'C1', 'J1'})
                self.assertEqual({n['name']: sorted(n['terminals']) for n in inventory['nets'].values()}, {
                    '/IN': [('J1', '1'), ('R1', '1')],
                    '/OUT': [('C1', '1'), ('J1', '2'), ('R1', '2')],
                    '/GND': [('C1', '2'), ('J1', '3')]})


class NativeRoutingInput(unittest.TestCase):
    def test_placed_unrouted_is_a_real_krt_stage_receipt_input(self):
        require_toolchain(native=True)
        checkout = require_kicad_route()
        with tempfile.TemporaryDirectory(prefix='agent-ee-rc-stages-route-') as tmp:
            root = Path(tmp)
            (root / 'captured').mkdir()
            candidate.capture(FIXTURES / 'placed-unrouted.scope.json', root / 'captured')
            spec = routing_adapter.step(project='project', board='rc_stage.kicad_pcb',
                schematic='rc_stage.kicad_sch', name='rc_stage', label='ee103', checkout=checkout, budget=60)
            receipt_dir = root / 'receipt'
            receipt = stage_receipts.run_step(receipt_dir, root / 'captured/inputs', spec,
                                             {'implementation': 1, 'receipt_index': 1})
            self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
            summary = routing_adapter.verify(receipt_dir / 'run', receipt=receipt)
            result = json.loads((receipt_dir / 'run/routing/attempt/result.json').read_text())
            self.assertEqual(result['backend']['name'], 'builtin')
            self.assertIsInstance(summary['router_exit'], int)  # No success-exit assertion.
            self.assertIn('routing', result)
            self.assertIn('validation', result)


class PrerequisitePolicy(unittest.TestCase):
    def without_tools(self, strict=False):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rc-stages-absent-') as tmp:
            env = dict(os.environ, PATH=tmp, PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests')]))
            env.pop('AGENT_EE_REQUIRE_TOOLCHAIN', None)
            if strict:
                env['AGENT_EE_REQUIRE_TOOLCHAIN'] = '1'
            return subprocess.run([sys.executable, '-m', 'unittest', '-v',
                                   'test_rc_stages.NativeStageFixtures', 'test_rc_stages.NativeRoutingInput'],
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
