"""Native CAN pair routing and negative skew, with immutable captured constraints."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from agent_ee.storage import read_json, digest
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('can_pair_probe', ROOT / 'fixtures/telemetry-pod/can-pair/probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class NativeCanPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='agent-ee-can-pair-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_CAN_PAIR_EVIDENCE', cls.temp.name))
        cls.root.mkdir(exist_ok=True)
        cls.out = cls.root / 'coupled'
        cls.result = probe.run(cls.out, cls.checkout)

    def test_coupled_pair_native_skew_and_five_mm_negative(self):
        result = self.result
        self.assertEqual(result['routing']['classification'], 'complete')
        self.assertEqual(result['routing']['pair_coverage']['status'], 'reported_coupled')
        pair, = result['pair_result']
        self.assertEqual((pair['p'], pair['n'], pair['netclass']), ('CAN+', 'CAN-', 'CAN'))
        self.assertEqual((pair['width_mm'], pair['gap_mm']), (.25, .35))
        self.assertEqual(pair['backend_status'], 'COUPLED')
        self.assertTrue(pair['routed'])
        self.assertEqual(pair['gap_violations'], 0)
        self.assertEqual(result['positive'], {'execution': 'ok', 'outcome': 'pass'})
        self.assertEqual(result['negative'], {'execution': 'ok', 'outcome': 'defect', 'violations': ['can-skew']})
        before = self.out / 'positive/inputs/project/can.kicad_pcb'
        routed = self.out / 'route/run/routing/candidate/project/can.kicad_pcb'
        self.assertEqual(digest(before), digest(routed), 'coverage must grade the unchanged router output')
        findings = read_json(self.out / 'negative/checks/can-routing.json')['details']['constraints']
        skew = next(c for c in findings if c['id'] == 'can-skew')
        self.assertTrue(any('5.0000 mm' in f['description'] for f in skew['native_findings']))
        raw = read_json(self.out / 'positive/work/can-routing/native-rules/can-skew/drc.json')
        self.assertFalse(any(v['type'].startswith('diff_pair_gap') for v in raw['violations']))
        self.assertTrue(next(c for c in read_json(self.out / 'positive/checks/can-routing.json')
                             ['details']['constraints'] if c['id'] == 'can-skew')['tool_load_witness'])

    def test_single_ended_native_run_retains_explicit_limitation(self):
        out = self.root / 'single-ended'
        out.mkdir()
        candidate = out / 'candidate'
        probe.prepare(candidate)
        summary = probe.route(candidate, out / 'route', self.checkout, pairs=False)
        self.assertEqual(summary['proposal'], 'none')
        self.assertEqual(summary['classification'], 'validation_failed')
        self.assertEqual(summary['pair_coverage']['status'], 'unknown')
        self.assertIn('CAN pair routed single-ended', summary['pair_coverage']['limitations'])
        report = read_json(out / 'route/run/routing/adapter-report.json')
        self.assertEqual(report['semantics']['pair_coverage'], summary['pair_coverage'])
        self.assertNotIn('diff_pairs', report['request'])
        native = read_json(out / 'route/run/routing/attempt/result.json')
        self.assertEqual(native['routing']['status'], 'COMPLETE')
        self.assertTrue(any('diff_pair_gap' in reason for reason in report['semantics']['reasons']))
        self.assertFalse(summary['pair_coverage']['acceptance_ready'])


if __name__ == '__main__':
    unittest.main()
