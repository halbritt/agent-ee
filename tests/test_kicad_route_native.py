"""Actual kicad-route (pinned a132899) and KRT on the original placed RC, read-only.

AGENT_EE_KICAD_ROUTE_CHECKOUT names a clean kicad-route checkout at the pin with
its installed KRT state; the standard image sets it to /opt/kicad-route. Unset,
the native tests skip, or fail under AGENT_EE_REQUIRE_TOOLCHAIN=1; a configured
checkout that does not qualify always fails. The placed RC is the hand-made RC
control with every track, via and zone removed; footprint placement, outline,
schematic, project and library tables are unchanged. This exercises the real
router; the policy fixture tests live in test_kicad_route_adapter.py.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee import stage_receipts
from agent_ee.executor import execute
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
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

# Original two-net fixture: two identical pad rows, 10 mm apart, on a 20 mm square.
PAIR_BOARD = r'''
import sys, pcbnew as k
b = k.BOARD()
point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
for start, end in [((40, 40), (60, 40)), ((60, 40), (60, 60)),
                   ((60, 60), (40, 60)), ((40, 60), (40, 40))]:
    edge = k.PCB_SHAPE(b)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetLayer(k.Edge_Cuts)
    edge.SetStart(point(*start))
    edge.SetEnd(point(*end))
    edge.SetWidth(k.FromMM(.05))
    b.Add(edge)
nets = []
for name in ('CAN+', 'CAN-'):
    net = k.NETINFO_ITEM(b, name)
    b.Add(net)
    nets.append(net)
for reference, x in (('J1', 45), ('J2', 55)):
    fp = k.FOOTPRINT(b)
    b.Add(fp)
    fp.SetReference(reference)
    fp.SetPosition(point(x, 50))
    for number, net in enumerate(nets, 1):
        pad = k.PAD(fp)
        fp.Add(pad)
        pad.SetNumber(str(number))
        pad.SetNet(net)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetSize(point(.25, .25))
        pad.SetPosition(point(x, 50 + (number - 1) * .6))
k.SaveBoard(sys.argv[1], b)
'''


class NativeKicadRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
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
        self.assertEqual(result['schema'], attempt.RESULT_SCHEMA)
        self.assertEqual(set(result), attempt.RESULT_KEYS)
        record = json.loads((work / 'routing/attempt/attempt.json').read_text())
        self.assertEqual(set(record), attempt.ATTEMPT_KEYS)
        self.assertTrue(attempt.validated_candidate(result['validation'], summary['candidate_sha256']))
        self.assertFalse(result['sandbox']['state']['writable'])
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

    def test_repeated_krt_runs_compare_geometry_and_name_a_moved_segment(self):
        from agent_ee.routing_equivalence import compare_boards
        placed = self.placed_rc()
        spec = self.spec()
        outputs = []
        for index in (1, 2):
            work = self.root / f'repeat-{index}'
            record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                             extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
            self.assertIn(record['exit_code'], (0, 2), record)
            summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
            self.assert_native_attempt(work, summary)
            output = self.root / f'routed-{index}.kicad_pcb'
            source = work / 'routing/candidate/project/rc_filter.kicad_pcb'
            self.assertTrue(source.is_file(), (source, summary))
            shutil.copyfile(source, output)
            outputs.append(output)
        report = compare_boards(*outputs)
        (self.root / 'repeated-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertTrue(report['geometry_equal'], report)
        self.assertIn('byte_equal', report)
        raw = outputs[1].read_text()
        segment = re.search(r'\t\(segment\b.*?\n\t\)', raw, re.S).group()
        duplicate = re.sub(r'\(uuid "[^"]+"\)', f'(uuid "{uuid.uuid4()}")', segment)
        duplicated = self.root / 'duplicated.kicad_pcb'
        duplicated.write_text(raw.rstrip()[:-1] + '\n' + duplicate + '\n)\n')
        duplicate_report = compare_boards(duplicated, outputs[0])
        (self.root / 'duplicate-comparison.json').write_text(json.dumps(duplicate_report, indent=2) + '\n')
        self.assertFalse(duplicate_report['geometry_equal'])
        self.assertFalse(duplicate_report['byte_equal'])
        self.assertEqual(len(duplicate_report['board_geometry']), len(report['board_geometry']) + 1)
        self.assertEqual(len(duplicate_report['only_board']), 1)
        element = duplicate_report['only_board'][0]
        self.assertEqual(element['kind'], 'PCB_TRACK')
        self.assertIn('endpoints_nm', element)
        self.assertEqual(duplicate_report['only_reference'], [])
        self.assertEqual(duplicate_report['board_duplicates'], [{'element': element, 'count': 2}])
        self.assertEqual(duplicate_report['reference_duplicates'], [])
        self.assertEqual(duplicate_report['count_differences'], [
            {'element': element, 'board_count': 2, 'reference_count': 1}])
        import pcbnew as k
        board = k.LoadBoard(str(outputs[1]))
        track = next(t for t in board.GetTracks() if t.GetClass() == 'PCB_TRACK')
        start = track.GetStart()
        track.SetStart(k.VECTOR2I(start.x + 100000, start.y))
        moved = self.root / 'moved.kicad_pcb'
        k.SaveBoard(str(moved), board)
        report = compare_boards(outputs[0], moved)
        (self.root / 'moved-comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertFalse(report['geometry_equal'])
        self.assertEqual(len(report['only_board']), 1)
        self.assertEqual(len(report['only_reference']), 1)
        self.assertEqual(report['only_reference'][0]['kind'], 'PCB_TRACK')
        self.assertIn('endpoints_nm', report['only_reference'][0])

    def test_invalid_board_is_a_versioned_refusal_without_a_proposal(self):
        placed = self.placed_rc()
        (placed / 'project/rc_filter.kicad_pcb').write_text('(invalid_board)\n')
        spec = self.spec()
        work = self.root / 'refused'
        record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        self.assertEqual(record['exit_code'], 4, (work / 'console.log').read_text()[-2000:])
        refusal = json.loads((work / 'routing/kicad-route.stdout').read_text())
        self.assertEqual(refusal['schema'], 'kicad-route/refusal/1')
        self.assertEqual(refusal['reason_class'], 'input')
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertEqual((summary['classification'], summary['proposal'], summary['coverage']),
                         ('refused', 'none', 'missing_routing'))
        self.assertFalse((work / 'routing/candidate').exists())

    def test_pair_opt_in_with_no_detected_pairs_uses_version_two(self):
        placed = self.placed_rc()
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                                    schematic='rc_filter.kicad_sch', name='rc_filter', label='pairs-empty',
                                    checkout=self.checkout, budget=120, diff_pairs='auto')
        before = self.state()
        receipt_dir = self.root / 'pairs-receipt'
        receipt = stage_receipts.run_step(receipt_dir, placed, spec, {'implementation': 1, 'receipt_index': 1})
        self.assertEqual(self.state(), before)
        self.assertEqual(stage_receipts.validate_receipt(receipt_dir), receipt)
        self.assertIn(receipt['exit_code'], (0, 2), receipt['violations'])
        self.assertEqual(receipt['violations'], [])
        work = receipt_dir / 'run'
        summary = routing_adapter.verify(work, receipt=receipt)
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'))
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        record = json.loads((work / 'routing/attempt/attempt.json').read_text())
        self.assertEqual(result['schema'], 'kicad-route/route/2')
        self.assertEqual(set(result), attempt.RESULT_KEYS | {'pairs'})
        self.assertEqual(record['schema'], 'kicad-route/attempt/2')
        self.assertEqual(set(record), attempt.ATTEMPT_KEYS | {'pairs', 'pair_routing'})
        self.assertEqual((result['pairs'], record['pairs'], record['pair_routing']), ([], [], {}))
        self.assertEqual(result['settings'], attempt.expected_settings(
            {'backend': 'krt', 'budget_s': 120, 'diff_pairs': 'auto'}, None, []))
        self.forge_validated_board(work)
        with self.assertRaisesRegex(ValueError, 'not the board the router validated'):
            routing_adapter.verify(work, receipt=receipt)

    def test_pair_opt_in_routes_an_original_two_net_board(self):
        placed = self.placed_rc()
        made = execute(placed, self.root / 'make-pair',
                       ['/usr/bin/python3', '-I', '-B', '-c', PAIR_BOARD, '/work/pair.kicad_pcb'], timeout=300)
        self.assertEqual(made['exit_code'], 0, (self.root / 'make-pair/console.log').read_text())
        shutil.copyfile(self.root / 'make-pair/pair.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
        pro = placed / 'project/rc_filter.kicad_pro'
        project = json.loads(pro.read_text())
        project['net_settings'] = {'meta': {'version': 3}, 'classes': [
            {'name': 'Default', 'clearance': 0.2, 'track_width': 0.25,
             'diff_pair_width': 0.25, 'diff_pair_gap': 0.35}], 'netclass_patterns': []}
        pro.write_text(json.dumps(project))
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                                    schematic='rc_filter.kicad_sch', name='rc_filter', label='pairs-native',
                                    checkout=self.checkout, budget=120, diff_pairs='auto')
        before = self.state()
        work = self.root / 'pairs-native'
        observed = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                           extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        self.assertEqual(self.state(), before)
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertEqual(observed['exit_code'], 0, summary)
        self.assertEqual(summary['classification'], 'complete')
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        self.assertEqual(result['schema'], 'kicad-route/route/2')
        pair, = result['pairs']
        self.assertEqual((pair['p'], pair['n']), ('CAN+', 'CAN-'))
        self.assertTrue(pair['routed'], pair)
        self.assertEqual(pair['backend_status'], 'COUPLED')
        self.assertEqual(pair['gap_violations'], 0)

    def test_a_pinned_checkout_with_a_broken_krt_runtime_fails_the_prerequisite(self):
        """Pinned code and present state files are not enough: KRT must import."""
        broken = self.root / 'broken-route'
        for top in ('bin', 'src'):
            shutil.copytree(self.checkout / top, broken / top, ignore=shutil.ignore_patterns('__pycache__'))
        (broken / '.git').mkdir()
        (broken / '.git/HEAD').write_text(attempt.PIN + '\n')
        subprocess.run(['/usr/bin/python3', '-m', 'venv', '--without-pip', str(broken / '.venv-krt')], check=True)
        (broken / 'vendor/krt/py_router').mkdir(parents=True)
        (broken / 'vendor/krt/py_router/route.py').write_text('')
        self.assertEqual(routing_adapter.preflight(broken)['problems'], [])
        with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(broken)}):
            with self.assertRaisesRegex(AssertionError, "(?s)KRT runtime import failed.*No module named 'grid_router'"):
                require_kicad_route()

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


class NativePrerequisiteTest(unittest.TestCase):
    """How the native class reports an absent, required or misconfigured kicad-route."""

    def run_native(self, checkout=None, strict=False):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        for name in ('AGENT_EE_KICAD_ROUTE_CHECKOUT', 'AGENT_EE_REQUIRE_TOOLCHAIN'):
            env.pop(name, None)
        if checkout is not None:
            env['AGENT_EE_KICAD_ROUTE_CHECKOUT'] = str(checkout)
        if strict:
            env['AGENT_EE_REQUIRE_TOOLCHAIN'] = '1'
        return subprocess.run([sys.executable, '-m', 'unittest', '-v',
                               'tests.test_kicad_route_native.NativeKicadRouteTest'],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)

    def test_unset_checkout_is_a_documented_skip(self):
        result = self.run_native()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OK (skipped=1)', result.stderr)
        self.assertIn('AGENT_EE_KICAD_ROUTE_CHECKOUT is unset', result.stderr)

    def test_unset_checkout_fails_in_strict_mode(self):
        result = self.run_native(strict=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('AGENT_EE_REQUIRE_TOOLCHAIN=1: toolchain prerequisite: AGENT_EE_KICAD_ROUTE_CHECKOUT is unset',
                      result.stderr)
        self.assertNotIn('skipped', result.stderr)

    def test_configured_checkout_that_does_not_qualify_fails_without_strict_mode(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-route-absent-') as tmp:
            result = self.run_native(checkout=Path(tmp) / 'kicad-route')
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('does not qualify', result.stderr)
        self.assertIn(f'is not the pinned {attempt.PIN}', result.stderr)
        self.assertIn('missing installed backend dependency: KICAD_ROUTE_STATE/.venv-krt/bin/python',
                      result.stderr)
        self.assertNotIn('skipped', result.stderr)


if __name__ == '__main__':
    unittest.main()
