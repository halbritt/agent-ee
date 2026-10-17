"""Actual kicad-route (pinned 08179cd) and KRT on the original placed RC, read-only.

Opt-in: set AGENT_EE_KICAD_ROUTE_CHECKOUT to a clean kicad-route checkout at
the pin with its installed KRT state. The placed RC is the hand-made RC
control with every track, via and zone removed; footprint placement, outline,
schematic, project and library tables are unchanged. This exercises the real
router; the policy fixture tests live in test_kicad_route_adapter.py.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from toolchain import require_toolchain

try:
    from agent_ee import stage_receipts
except ImportError:  # AGENTEE-95 lands separately; root integrates both.
    stage_receipts = None

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = os.environ.get('AGENT_EE_KICAD_ROUTE_CHECKOUT')
STRIP = r'''
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
before = sorted((f.GetReference(), f.m_Uuid.AsString(), f.GetPosition().x, f.GetPosition().y,
                 f.GetOrientationDegrees(), f.GetLayer()) for f in board.GetFootprints())
for item in list(board.GetTracks()) + list(board.Zones()):
    board.Delete(item)  # Remove() leaves a dangling SWIG owner and can crash
pcbnew.SaveBoard(sys.argv[2], board)
check = pcbnew.LoadBoard(sys.argv[2])
after = sorted((f.GetReference(), f.m_Uuid.AsString(), f.GetPosition().x, f.GetPosition().y,
                f.GetOrientationDegrees(), f.GetLayer()) for f in check.GetFootprints())
assert not len(check.GetTracks()) and not len(check.Zones()) and after == before
print("placed")
'''


@unittest.skipUnless(CHECKOUT, 'set AGENT_EE_KICAD_ROUTE_CHECKOUT to run actual kicad-route')
class NativeKicadRouteTest(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.checkout = Path(CHECKOUT).resolve()
        check = routing_adapter.preflight(self.checkout)
        self.assertEqual(check['problems'], [])

    def placed_rc(self):
        placed = self.root / 'placed-rc'
        shutil.copytree(ROOT / 'fixtures/rc/control', placed / 'project')
        shutil.copytree(ROOT / 'fixtures/rc/requirements', placed / 'requirements')
        record = execute(ROOT / 'fixtures/rc', self.root / 'strip', ['/usr/bin/python3', '-I', '-B', '-c', STRIP,
                         '/candidate/control/rc_filter.kicad_pcb', '/work/placed.kicad_pcb'], timeout=300)
        self.assertEqual(record['exit_code'], 0, (self.root / 'strip/console.log').read_text()[-2000:])
        shutil.copyfile(self.root / 'strip/placed.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
        return placed

    def state(self):
        return {rel: attempt.tree(self.checkout / rel) for rel in ('bin', 'src', '.venv-krt', 'vendor/krt')}

    def spec(self):
        return routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
                                    name='rc_filter', label='krt-native', checkout=self.checkout, budget=120)

    def assert_native_attempt(self, work, summary):
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'))
        self.assertEqual(summary['backend']['value']['name'], 'krt')
        self.assertEqual(summary['tool_version']['status'], 'unknown')
        self.assertEqual(summary['reproducibility'], attempt.REPRODUCIBILITY['krt'])
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        self.assertEqual(summary['backend']['value'], result['backend'])
        self.assertEqual(result['validation']['status'], 'PASS')
        self.assertEqual(result['validation']['validated_board_sha256'], summary['candidate_sha256'])
        self.assertEqual(result['routing']['stats']['removed_existing_tracks'], 0)
        snapshot = json.loads((work / 'routing/attempt/snapshot.json').read_text())
        self.assertLessEqual({'rc_filter.kicad_pcb', 'rc_filter.kicad_sch'}, set(snapshot['files']))

    def forge_validated_board(self, work):
        """The real retained bytes, not only the fixture's, carry what verify re-checks."""
        path = work / 'routing/attempt/result.json'
        result = json.loads(path.read_text())
        result['validation']['validated_board_sha256'] = '0' * 64
        path.write_text(json.dumps(result))
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        report['retained']['attempt/result.json'] = attempt.digest(path)
        attempt.write_report(work / 'routing/adapter-report.json', report)

    def test_krt_attempt_on_the_placed_rc_with_read_only_code_and_state(self):
        placed = self.placed_rc()
        spec = self.spec()
        before = self.state()
        work = self.root / 'work'
        record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        self.assertEqual(self.state(), before, 'kicad-route code or state changed on the host')
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        self.assertTrue(all(p['refused'] and p['errno'] == 'EROFS' for p in report['state_write_probe']),
                        report['state_write_probe'])
        self.assertIn(record['exit_code'], (0, 2), report['semantics'])
        inputs = attempt.files_of(attempt.tree(placed))
        summary = routing_adapter.verify(work, inputs)
        self.assertEqual(summary['router_exit'], record['exit_code'])
        self.assert_native_attempt(work, summary)
        self.forge_validated_board(work)
        with self.assertRaisesRegex(ValueError, 'not the board the router validated'):
            routing_adapter.verify(work, inputs)

    @unittest.skipIf(stage_receipts is None, 'AGENTEE-95 agent_ee.stage_receipts is not integrated on this branch')
    def test_krt_attempt_as_a_coordinator_stage_receipt(self):
        placed = self.placed_rc()
        spec = self.spec()
        before = self.state()
        receipt_dir = self.root / 'receipts/1-kicad-route-krt'
        receipt = stage_receipts.run_step(receipt_dir, placed, spec, {'implementation': 1, 'receipt_index': 1})
        self.assertEqual(self.state(), before, 'kicad-route code or state changed on the host')
        self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
        self.assertIn((receipt['exit_code'], receipt['status']), ((0, 'completed'), (2, 'partial')),
                      receipt['violations'])
        self.assertEqual(receipt['violations'], [])
        summary = routing_adapter.verify(receipt_dir / 'run', receipt=receipt)
        self.assertEqual(summary['candidate_sha256'], receipt['outputs']['project/rc_filter.kicad_pcb'])
        self.assertEqual(summary['backend'], receipt['tool']['backend'])
        self.assert_native_attempt(receipt_dir / 'run', summary)
        self.forge_validated_board(receipt_dir / 'run')
        with self.assertRaisesRegex(ValueError, 'not the board the router validated'):
            routing_adapter.verify(receipt_dir / 'run', receipt=receipt)


if __name__ == '__main__':
    unittest.main()
