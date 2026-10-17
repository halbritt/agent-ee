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

    def test_krt_attempt_on_the_placed_rc_with_read_only_code_and_state(self):
        placed = self.placed_rc()
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
                                    name='rc_filter', label='krt-native', checkout=self.checkout, budget=120)
        before = self.state()
        work = self.root / 'work'
        record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        self.assertEqual(self.state(), before, 'kicad-route code or state changed on the host')
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        self.assertTrue(all(p['refused'] and p['errno'] == 'EROFS' for p in report['state_write_probe']),
                        report['state_write_probe'])
        self.assertIn(record['exit_code'], (0, 2), report['semantics'])
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'))
        self.assertEqual(summary['backend']['value']['name'], 'krt')
        self.assertEqual(summary['tool_version']['status'], 'unknown')
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        self.assertEqual(result['validation']['status'], 'PASS')
        self.assertEqual(result['routing']['stats']['removed_existing_tracks'], 0)


if __name__ == '__main__':
    unittest.main()
