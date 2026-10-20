"""AGENTEE-181: owned, native, networkless pod-scale qualification."""
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.qualify_pod_placement import CASES, attempt, qualify
from agent_ee.storage import read_json
from tests.toolchain import require_toolchain


class PodScaleQualificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='agent-ee-pod-scale-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.out = cls.root / 'qualification'
        cls.record = qualify(cls.out)

    def test_all_envelopes_place_every_part_on_both_sides_and_pass_each_predicate(self):
        self.assertTrue(self.record['qualified'], self.record)
        self.assertEqual(len(self.record['cases']), len(CASES))
        for case, (width, height, count) in zip(self.record['cases'], CASES):
            self.assertEqual(case['outline_mm'], [width, height])
            self.assertEqual(case['footprint_count'], count)
            for run in case['runs']:
                self.assertGreater(run['runtime_seconds'], 0)
                self.assertEqual(run['placed_count'], count)
                self.assertEqual(run['sides'], {'F': count//2, 'B': count//2})
                self.assertEqual(run['verdicts'], {c: 'pass' for c in (
                    'courtyard-overlap/1', 'outline-containment/1', 'edge-clearance/1', 'footprint-keepout/1')})

    def test_repeat_is_identical_geometry_and_saved_bytes_with_immutable_inputs(self):
        for case in self.record['cases']:
            self.assertTrue(case['deterministic'])
            self.assertTrue(case['inputs_unchanged'])
            first, second = case['runs']
            self.assertEqual(first['placement'], second['placement'])
            self.assertEqual(first['candidate_sha256'], second['candidate_sha256'])
            width, height = case['outline_mm']
            root = self.out / f"{width}x{height}-{case['footprint_count']}"
            self.assertEqual((root / 'run-1/proposal/candidate.kicad_pcb').read_bytes(),
                             (root / 'run-2/proposal/candidate.kicad_pcb').read_bytes())

    def test_native_keepout_really_is_48_by_21_mm_on_both_copper_sides(self):
        for case in self.record['cases']:
            width, height = case['outline_mm']
            probe = read_json(self.out / f"{width}x{height}-{case['footprint_count']}" /
                              'run-1/proposal/placement-probe.json')
            area, = probe['rule_areas']
            self.assertTrue(area['forbid']['footprints'])
            self.assertEqual(set(area['layers']), {'F.Cu', 'B.Cu'})
            points, = area['outline']['polygons']
            self.assertEqual(max(p[0] for p in points) - min(p[0] for p in points), 48_000_000)
            self.assertEqual(max(p[1] for p in points) - min(p[1] for p in points), 21_000_000)

    def test_oversized_part_is_a_named_proof_without_a_crash_or_partial_board(self):
        run = self.record['oversized']
        self.assertEqual(run['result']['status'], 'packing_infeasible')
        self.assertEqual(run['result']['outcome'], 'defect')
        self.assertEqual(run['result']['proof']['ref'], 'C1')
        self.assertIn('C1', run['result']['reason'])
        self.assertEqual(run['execution']['exit_code'], 1)
        self.assertEqual(run['placed_count'], 0)
        self.assertFalse((self.out / 'oversized/run/proposal').exists())
        self.assertFalse(list((self.out / 'oversized/run').glob('proposal-failure-*')))

    def test_missing_xml_is_failed_execution_with_no_success_or_candidate(self):
        inputs = self.root / 'missing-xml-inputs'
        shutil.copytree(self.out / '80x50-90/inputs', inputs)
        (inputs / 'netlist.xml').unlink()
        work = self.root / 'missing-xml-run'
        run = attempt(inputs, work)
        self.assertNotEqual(run['execution']['execution'], 'ok')
        self.assertIsNone(run['result'])
        self.assertIsNone(run['verdicts'])
        self.assertEqual(run['placed_count'], 0)
        self.assertFalse((work / 'proposal').exists())
