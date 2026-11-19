"""Actual kicad-route (current adapter pin) and its owned engine on the original placed RC, read-only.

AGENT_EE_KICAD_ROUTE_CHECKOUT names a clean kicad-route checkout at the pin with
its installed KRT state; the standard image sets it to /opt/kicad-route. Unset,
the native tests skip, or fail under AGENT_EE_REQUIRE_TOOLCHAIN=1; a configured
checkout that does not qualify always fails. The placed RC is the hand-made RC
control with every track, via and zone removed; footprint placement, outline,
schematic, project and library tables are unchanged. This exercises the real
router; the policy fixture tests live in test_kicad_route_adapter.py.
"""
import contextlib
import importlib.machinery
import json
import os
import pwd
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import traceback
import uuid
import zipfile
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee import stage_receipts
from agent_ee.executor import execute, sandbox_argv
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

# Original four-layer fixture: three nets, each from a front SMD pad to a back SMD pad,
# so every connection needs a through via.
VIA_BOARD = r'''
import sys, pcbnew as k
b = k.BOARD()
b.SetCopperLayerCount(4)
point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
for start, end in [((40, 40), (70, 40)), ((70, 40), (70, 60)),
                   ((70, 60), (40, 60)), ((40, 60), (40, 40))]:
    edge = k.PCB_SHAPE(b)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetLayer(k.Edge_Cuts)
    edge.SetStart(point(*start))
    edge.SetEnd(point(*end))
    edge.SetWidth(k.FromMM(.05))
    b.Add(edge)
nets = []
for name in ('A', 'B', 'C'):
    net = k.NETINFO_ITEM(b, name)
    b.Add(net)
    nets.append(net)
for reference, x, layer in (('J1', 47, k.F_Cu), ('J2', 63, k.B_Cu)):
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
        layers.AddLayer(layer)
        pad.SetLayerSet(layers)
        pad.SetSize(point(.6, .6))
        pad.SetPosition(point(x, 47 + (number - 1) * 3))
k.SaveBoard(sys.argv[1], b)
'''


@contextlib.contextmanager
def retain_failed_run(root, test_id):
    """Preserve the first failure's entire native workspace before unittest cleanup."""
    try:
        yield
    except Exception as exc:
        retained = Path(tempfile.mkdtemp(prefix='agent-ee-route-failure-', dir=root.parent))
        shutil.copytree(root, retained / 'workspace', symlinks=True)
        (retained / 'failure.json').write_text(json.dumps({
            'test': test_id, 'exception_type': type(exc).__name__,
            'exception': str(exc), 'traceback': traceback.format_exc(),
        }, indent=2) + '\n', encoding='utf-8')
        print(f'Native routing failure evidence retained at {retained}', file=sys.stderr, flush=True)
        raise


class NativeFailureRetentionTests(unittest.TestCase):
    def test_failure_preserves_console_evidence_and_original_exception_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            (root / 'repeat-2/routing/attempt').mkdir(parents=True)
            console = root / 'repeat-2/console.log'
            console.write_bytes(b'first failure exit 65\n')
            evidence = root / 'repeat-2/routing/attempt/result.json'
            evidence.write_bytes(b'{"routing":"failed"}\n')
            calls = 0
            with self.assertRaisesRegex(AssertionError, '65'):
                with retain_failed_run(root, self.id()):
                    calls += 1
                    raise AssertionError('adapter exit 65')
            retained, = Path(tmp).glob('agent-ee-route-failure-*')
            shutil.rmtree(root)
            self.assertEqual(calls, 1)
            self.assertEqual((retained / 'workspace/repeat-2/console.log').read_bytes(),
                             b'first failure exit 65\n')
            self.assertEqual((retained / 'workspace/repeat-2/routing/attempt/result.json').read_bytes(),
                             b'{"routing":"failed"}\n')
            self.assertEqual(json.loads((retained / 'failure.json').read_text())['test'], self.id())

    def test_success_leaves_no_failure_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            root.mkdir()
            with retain_failed_run(root, self.id()):
                pass
            self.assertEqual(list(Path(tmp).iterdir()), [root])


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
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            # Translate rc_filter.kicad_pcb into [-100, +100] mm while preserving rc_filter.kicad_sch parity.
            shift = (
                "import sys, pcbnew as k\n"
                "b = k.LoadBoard(sys.argv[1])\n"
                "d = k.VECTOR2I(k.FromMM(-50), k.FromMM(-50))\n"
                "for fp in b.GetFootprints():\n"
                "    fp.SetPosition(fp.GetPosition() + d)\n"
                "for dr in b.GetDrawings():\n"
                "    dr.Move(d)\n"
                "k.SaveBoard(sys.argv[2], b)\n"
            )
            shifted = execute(
                placed, self.root / 'shift-rc',
                ['/usr/bin/python3', '-I', '-B', '-c', shift,
                 '/candidate/project/rc_filter.kicad_pcb', '/work/shifted.kicad_pcb'],
                timeout=300,
            )
            self.assertEqual(shifted['exit_code'], 0, (self.root / 'shift-rc/console.log').read_text()[-2000:])
            shutil.copyfile(self.root / 'shift-rc/shifted.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
        return placed

    def state(self):
        generation, _ = attempt.state_generation(self.checkout)
        return {rel: attempt.tree(self.checkout / rel)
                for rel in ('bin', 'src', *attempt.state_components('builtin', generation))}

    def spec(self):
        return routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
                                    name='rc_filter', label='krt-native', checkout=self.checkout, budget=120)

    def assert_native_attempt(self, work, summary):
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            self.assertEqual(summary['proposal'], 'eligible')
            self.assertEqual(summary['backend']['value']['name'], 'kicad-route-engine')
            self.assertEqual(summary['tool_version']['status'], 'unknown')
            self.assertEqual(summary['reproducibility'], attempt.REPRODUCIBILITY['builtin'])
            op_result = json.loads((work / 'routing/attempt/operation-result.json').read_text())
            self.assertEqual(op_result['schema'], attempt.NATIVE_OPERATION_SCHEMA)
            self.assertEqual(op_result['status'], 'complete')
            self.assertEqual(op_result['exit_code'], 0)
            self.assertTrue(op_result['source']['sidecar_check']['passed'])
            self.assertTrue(op_result['publication']['published'])
            self.assertFalse(op_result['publication']['ambiguous_termination'])
            self.assertEqual(op_result['publication']['output_sha256'], summary['candidate_sha256'])
            req_file = work / 'routing/attempt/operation-request.json'
            self.assertEqual(attempt.digest(req_file), op_result['request']['request_sha256'])
            self.assertEqual(op_result['routing']['request_sha256'], op_result['request']['request_sha256'])
            return
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'))
        self.assertEqual(summary['backend']['value']['name'], 'builtin')
        self.assertEqual(summary['tool_version']['status'], 'unknown')
        self.assertEqual(summary['reproducibility'], attempt.REPRODUCIBILITY['builtin'])
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        self.assertEqual(summary['backend']['value'], result['backend'])
        self.assertEqual(result['validation']['status'], 'PASS')
        self.assertEqual(result['schema'], attempt.RESULT_SCHEMA)
        self.assertEqual(set(result), attempt.RESULT_KEYS | {'copper_ownership'})
        record = json.loads((work / 'routing/attempt/attempt.json').read_text())
        self.assertEqual(set(record), attempt.ATTEMPT_KEYS)
        self.assertTrue(attempt.validated_candidate(result['validation'], summary['candidate_sha256']))
        self.assertFalse(result['sandbox']['state']['writable'])
        self.assertEqual(result['routing']['stats']['removed_existing_tracks'], 0)
        snapshot = json.loads((work / 'routing/attempt/snapshot.json').read_text())
        self.assertLessEqual({'rc_filter.kicad_pcb', 'rc_filter.kicad_sch'}, set(snapshot['files']))

    def forge_validated_board(self, work):
        """The real retained bytes, not only the fixture's, carry what verify re-checks."""
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            path = work / 'routing/attempt/operation-result.json'
            op = json.loads(path.read_text())
            op['publication']['output_sha256'] = '0' * 64
            path.write_text(json.dumps(op))
            report = json.loads((work / 'routing/adapter-report.json').read_text())
            report['retained']['attempt/operation-result.json'] = attempt.digest(path)
            attempt.write_report(work / 'routing/adapter-report.json', report)
            return
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
        expected_err = 'classify as contradiction' if routing_adapter.is_rust_capable_checkout(self.checkout) else 'not the board the router validated'
        with self.assertRaisesRegex(ValueError, expected_err):
            routing_adapter.verify(work, inputs)

    def test_repeated_krt_runs_compare_geometry_and_name_a_moved_segment(self):
        with retain_failed_run(self.root, self.id()):
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
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            op = json.loads((work / 'routing/attempt/operation-result.json').read_text())
            self.assertEqual(op['schema'], attempt.NATIVE_OPERATION_SCHEMA)
            self.assertEqual(op['status'], 'invalid')
            self.assertEqual(op['exit_code'], 2)
        else:
            refusal = json.loads((work / 'routing/kicad-route.stdout').read_text())
            self.assertEqual(refusal['schema'], 'kicad-route/refusal/1')
            self.assertEqual(refusal['reason_class'], 'input')
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertEqual((summary['classification'], summary['proposal'], summary['coverage']),
                         ('refused', 'none', 'missing_routing'))
        self.assertFalse((work / 'routing/candidate').exists())

    def test_pair_opt_in_with_no_detected_pairs_uses_version_two(self):
        placed = self.placed_rc()
        if routing_adapter.is_rust_capable_checkout(self.checkout):
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
            self.assertEqual(summary['classification'], 'complete')
            self.assertEqual(summary['proposal'], 'eligible')
            op = json.loads((work / 'routing/attempt/operation-result.json').read_text())
            self.assertEqual(op['schema'], attempt.NATIVE_OPERATION_SCHEMA)
            self.assertEqual(op['status'], 'complete')
            report = json.loads((work / 'routing/adapter-report.json').read_text())
            self.assertEqual(report['process']['argv'][-2:], ['--diff-pairs', 'auto'])
            return
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
        self.assertEqual(set(result), attempt.RESULT_KEYS | {'pairs', 'copper_ownership'})
        self.assertEqual(record['schema'], 'kicad-route/attempt/2')
        self.assertEqual(set(record), attempt.ATTEMPT_KEYS | {'pairs', 'pair_routing'})
        self.assertEqual((result['pairs'], record['pairs'], record['pair_routing']), ([], [], {}))
        self.assertIn('grid_basis', result['settings'])
        self.assertTrue(attempt.settings_match(
            {'backend': 'builtin', 'budget_s': 120, 'diff_pairs': 'auto'},
            result['settings'], None, []))
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
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            op = json.loads((work / 'routing/attempt/operation-result.json').read_text())
            self.assertEqual(op['schema'], attempt.NATIVE_OPERATION_SCHEMA)
            self.assertEqual(op['status'], 'complete')
            report = json.loads((work / 'routing/adapter-report.json').read_text())
            self.assertEqual(report['process']['argv'][-2:], ['--diff-pairs', 'auto'])
            return
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        self.assertEqual(result['schema'], 'kicad-route/route/2')
        pair, = result['pairs']
        self.assertEqual((pair['p'], pair['n']), ('CAN+', 'CAN-'))
        self.assertTrue(pair['routed'], pair)
        self.assertEqual(pair['backend_status'], 'COUPLED')
        self.assertEqual(pair['gap_violations'], 0)

    def via_drc(self, candidate, label):
        """Native DRC violation types and the distinct (diameter, drill) via sizes, in mm."""
        import pcbnew as k
        drc = execute(candidate, self.root / (label + '-drc'), ['kicad-cli', 'pcb', 'drc', '--format', 'json',
                      '--severity-all', '--units', 'mm', '-o', '/work/drc.json',
                      '/candidate/project/rc_filter.kicad_pcb'], timeout=120)
        self.assertEqual(drc['execution'], 'ok', drc)
        report = json.loads((self.root / (label + '-drc/drc.json')).read_text())
        board = k.LoadBoard(str(candidate / 'project/rc_filter.kicad_pcb'))
        vias = sorted({(round(k.ToMM(v.GetWidth(k.F_Cu)), 4), round(k.ToMM(v.GetDrillValue()), 4))
                       for v in board.GetTracks() if v.GetClass() == 'PCB_VIA'})
        return vias, sorted(v['type'] for v in report['violations'])

    def test_scaffold_four_layer_default_via_routes_without_annular_errors(self):
        """AGENTEE-272: the produced pod Default via is 0.45/0.20 mm, not the 0.25/0.15 minimum."""
        import pcbnew as k
        from agent_ee.board_envelope import source_inventory
        from agent_ee.scaffold import produced_project, produced_rules
        files, inputs = source_inventory(ROOT / 'fixtures/telemetry-pod')
        _, _, rules, netclass, _ = produced_rules({'fab_profile': 'fab-profile.json'}, files, inputs)
        # The pod template carries fome-mc's severities: annular_width and hole_clearance are errors.
        project = produced_project(ROOT, 'fixtures/fome-mc/rules/pod.kicad_pro', rules, netclass)
        default = project['net_settings']['classes'][0]
        self.assertEqual((default['via_diameter'], default['via_drill']), (0.45, 0.2))
        # The minimum tier stays the DRC floor.
        design = project['board']['design_settings']['rules']
        self.assertEqual((design['min_via_diameter'], design['min_through_hole_diameter']), (0.25, 0.15))
        placed = self.placed_rc()
        made = execute(placed, self.root / 'make-vias',
                       ['/usr/bin/python3', '-I', '-B', '-c', VIA_BOARD, '/work/vias.kicad_pcb'], timeout=300)
        self.assertEqual(made['exit_code'], 0, (self.root / 'make-vias/console.log').read_text())
        shutil.copyfile(self.root / 'make-vias/vias.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
        (placed / 'project/rc_filter.kicad_pro').write_text(json.dumps(project, indent=2) + '\n')
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                                    schematic='rc_filter.kicad_sch', name='rc_filter', label='default-via',
                                    checkout=self.checkout, budget=120)
        work = self.root / 'default-via'
        observed = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                           extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertEqual(observed['exit_code'], 0, summary)
        self.assertEqual(summary['classification'], 'complete', summary)
        routed = self.root / 'routed'
        shutil.copytree(placed, routed)
        shutil.copyfile(work / 'routing/candidate/project/rc_filter.kicad_pcb', routed / 'project/rc_filter.kicad_pcb')
        vias, codes = self.via_drc(routed, 'routed')
        self.assertEqual(vias, [(0.45, 0.2)])
        self.assertNotIn('annular_width', codes)
        self.assertNotIn('hole_clearance', codes)
        # Control: the same routed vias at the former minimum-tier Default fail KiCad's
        # annular-ring rule, so the absence above is a graded result, not a blind DRC.
        board = k.LoadBoard(str(routed / 'project/rc_filter.kicad_pcb'))
        for via in board.GetTracks():
            if via.GetClass() == 'PCB_VIA':
                via.SetWidth(k.FromMM(0.25))
                via.SetDrill(k.FromMM(0.15))
        k.SaveBoard(str(routed / 'project/rc_filter.kicad_pcb'), board)
        vias, codes = self.via_drc(routed, 'minimum-via')
        self.assertEqual(vias, [(0.25, 0.15)])
        self.assertIn('annular_width', codes)

    def test_a_clean_checkout_with_a_broken_krt_runtime_fails_the_prerequisite(self):
        """Missing kicad-route-engine (Rust) or empty venv without manifest (legacy) refuses at preflight."""
        from tests.test_kicad_route_generations import copy_tool
        broken = copy_tool(self.checkout, self.root / 'broken-route')
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(broken)}):
                os.environ.pop('AGENT_EE_KICAD_ROUTE_ENGINE', None)
                os.environ.pop('KICAD_ROUTE_ENGINE_BIN', None)
                self.assertIsNone(attempt.resolve_kicad_route_engine(broken))
                problems = routing_adapter.preflight(broken)['problems']
                self.assertIn('missing executable kicad-route-engine binary', problems)
                with self.assertRaisesRegex(AssertionError, 'missing executable kicad-route-engine binary'):
                    require_kicad_route()
            # Separate explicit state manifest without manifest.json still refuses.
            sep_state = self.root / 'broken-state'
            (sep_state / '.venv-krt').mkdir(parents=True)
            (sep_state / 'manifest.json').write_text('{invalid')
            sep_problems = routing_adapter.preflight(self.checkout, sep_state)['problems']
            self.assertTrue(any('installed engine verification failed' in p for p in sep_problems), sep_problems)
            return
        subprocess.run(['/usr/bin/python3', '-m', 'venv', '--without-pip', str(broken / '.venv-krt')], check=True)
        (broken / 'vendor/krt/py_router').mkdir(parents=True)
        (broken / 'vendor/krt/py_router/route.py').write_text('')
        problems = routing_adapter.preflight(broken)['problems']
        self.assertTrue(any('installed engine verification failed' in problem and
                            'missing or unreadable state manifest' in problem
                            for problem in problems), problems)
        with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(broken)}):
            with self.assertRaisesRegex(AssertionError,
                                        '(?s)does not qualify:.*installed engine verification failed.*'
                                        'missing or unreadable state manifest'):
                require_kicad_route()

    def test_krt_attempt_as_a_coordinator_stage_receipt(self):
        placed = self.placed_rc()
        spec = self.spec()
        before = self.state()
        receipt_dir = self.root / 'receipts/1-kicad-route-builtin'
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
        expected_err = 'classify as contradiction' if routing_adapter.is_rust_capable_checkout(self.checkout) else 'not the board the router validated'
        with self.assertRaisesRegex(ValueError, expected_err):
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
        self.assertIn('cannot read the checkout HEAD', result.stderr)
        self.assertIn('missing installed backend dependency: KICAD_ROUTE_STATE/.venv-krt/bin/python',
                      result.stderr)
        self.assertNotIn('skipped', result.stderr)


class Feature10RouteNativeUnitTests(unittest.TestCase):
    """Focused behavioral coverage for resolver, route-native contract, exits 0/2..8, and RE-01."""

    def test_checkout_and_engine_resolver_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / 'explicit-route'
            env_co = root / 'env-route'
            explicit.mkdir()
            env_co.mkdir()
            with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(env_co)}):
                self.assertEqual(attempt.resolve_kicad_route_checkout(explicit), explicit)
                self.assertEqual(attempt.resolve_kicad_route_checkout(), env_co)
            # Engine precedence: AGENT_EE_KICAD_ROUTE_ENGINE -> KICAD_ROUTE_ENGINE_BIN -> target/{release,debug}
            rel_bin = explicit / 'rust-engine/target/release/kicad-route-engine'
            dbg_bin = explicit / 'rust-engine/target/debug/kicad-route-engine'
            dbg_bin.parent.mkdir(parents=True)
            dbg_bin.write_text('#!/bin/sh\nexit 0\n')
            dbg_bin.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('AGENT_EE_KICAD_ROUTE_ENGINE', None)
                os.environ.pop('KICAD_ROUTE_ENGINE_BIN', None)
                with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(env_co)}):
                    self.assertIsNone(attempt.resolve_kicad_route_engine())
                self.assertEqual(attempt.resolve_kicad_route_engine(explicit), dbg_bin)
                rel_bin.parent.mkdir(parents=True)
                rel_bin.write_text('#!/bin/sh\nexit 0\n')
                rel_bin.chmod(0o755)
                self.assertEqual(attempt.resolve_kicad_route_engine(explicit), rel_bin)
                override = root / 'override-engine'
                with unittest.mock.patch.dict(os.environ, {'KICAD_ROUTE_ENGINE_BIN': str(override)}):
                    self.assertEqual(attempt.resolve_kicad_route_engine(explicit), override)
                override2 = root / 'override-engine-primary'
                with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_ENGINE': str(override2),
                                                           'KICAD_ROUTE_ENGINE_BIN': str(override)}):
                    self.assertEqual(attempt.resolve_kicad_route_engine(explicit), override2)

    def test_worktree_git_file_and_symlink_refuse_preflight_with_sandbox_parity(self):
        git_env = {
            'PATH': '/usr/bin:/bin',
            'HOME': '/nonexistent',
            'LC_ALL': 'C',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_AUTHOR_NAME': 'agent-ee test',
            'GIT_AUTHOR_EMAIL': 'test@agent-ee.invalid',
            'GIT_AUTHOR_DATE': '2026-11-18T00:00:00Z',
            'GIT_COMMITTER_NAME': 'agent-ee test',
            'GIT_COMMITTER_EMAIL': 'test@agent-ee.invalid',
            'GIT_COMMITTER_DATE': '2026-11-18T00:00:00Z',
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_co = root / 'main-route'
            main_co.mkdir()
            for rel in ('bin/kicad-route', 'src/kicad_route/__init__.py',
                        'tools/rust_engine_kicad.py', 'rust-engine/Cargo.lock', 'LICENSE'):
                p = main_co / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('# stub\n')
            engine = main_co / 'rust-engine/target/release/kicad-route-engine'
            engine.parent.mkdir(parents=True)
            engine.write_text('#!/bin/sh\nexit 0\n')
            engine.chmod(0o755)
            (main_co / '.gitignore').write_text('/rust-engine/target/\n')
            for argv in (
                ['init', '-q', '-b', 'main'],
                ['add', '.gitignore', 'bin', 'src', 'tools', 'rust-engine/Cargo.lock', 'LICENSE'],
                ['commit', '-q', '-m', 'init'],
            ):
                subprocess.run(['/usr/bin/git', '-C', str(main_co), *argv],
                               check=True, capture_output=True, text=True, env=git_env)
            worktree_co = root / 'wt-route'
            subprocess.run(['/usr/bin/git', '-C', str(main_co), 'worktree', 'add', '-q', '-b', 'wt', str(worktree_co)],
                           check=True, capture_output=True, text=True, env=git_env)
            (worktree_co / 'rust-engine/target/release').mkdir(parents=True)
            shutil.copy2(engine, worktree_co / 'rust-engine/target/release/kicad-route-engine')
            symlink_co = root / 'symlink-route'
            shutil.copytree(main_co, symlink_co, ignore=shutil.ignore_patterns('.git'))
            (symlink_co / '.git').symlink_to(main_co / '.git')

            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('AGENT_EE_KICAD_ROUTE_ENGINE', None)
                os.environ.pop('KICAD_ROUTE_ENGINE_BIN', None)
                self.assertEqual(routing_adapter.preflight(main_co)['problems'], [])
                self.assertTrue((worktree_co / '.git').is_file())
                host_pf = routing_adapter.preflight(worktree_co)
                self.assertIsNone(host_pf['commit'])
                self.assertTrue(host_pf['problems'])
                self.assertTrue(any('cannot read the checkout HEAD' in p for p in host_pf['problems']),
                                host_pf['problems'])
                _, _, sandbox_problems = attempt.preflight(
                    worktree_co, worktree_co, 'builtin', mounted_at=attempt.SANDBOX['checkout'])
                self.assertTrue(any('cannot read the checkout HEAD' in p for p in sandbox_problems),
                                sandbox_problems)
                with self.assertRaisesRegex(ValueError, 'kicad-route does not qualify: .*cannot read the checkout HEAD'):
                    routing_adapter.step(
                        project='project', board='b.kicad_pcb', schematic='b.kicad_sch',
                        name='b', label='wt', checkout=worktree_co)
                sym_pf = routing_adapter.preflight(symlink_co)
                self.assertIsNone(sym_pf['commit'])
                self.assertTrue(any('cannot read the checkout HEAD' in p for p in sym_pf['problems']),
                                sym_pf['problems'])

    def test_native_semantics_exit_codes_and_integrity_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'src.kicad_pcb'
            out = root / 'out.kicad_pcb'
            req_file = root / 'operation-request.json'
            src.write_text('(kicad_pcb)\n')
            out.write_text('(kicad_pcb routed)\n')
            req_bytes = b'{"version":1}\n'
            req_file.write_bytes(req_bytes)
            req_sha = attempt.digest(req_file)
            src_sha = attempt.digest(src)
            out_sha = attempt.digest(out)
            base_op = {
                'schema': attempt.NATIVE_OPERATION_SCHEMA,
                'version': 1,
                'binary': {'name': 'kicad-route-engine', 'crate_version': '0.1.0', 'binary_sha256': 'a' * 64},
                'status': 'complete',
                'exit_code': 0,
                'reason': 'ok',
                'source': {'source_sha256': src_sha, 'sidecar_check': {'passed': True}},
                'request': {'request_sha256': req_sha, 'saved_request_path': str(req_file)},
                'routing': {'status': 'complete', 'request_sha256': req_sha},
                'publication': {'published': True, 'ambiguous_termination': False, 'output_sha256': out_sha},
            }
            self.assertEqual(attempt.native_semantics(0, base_op, req_file, src, out)[0], 'complete')
            cases = [
                (2, 'invalid', 'refused'),
                (2, 'unsupported', 'refused'),
                (2, 'native_validation_failed', 'validation_failed'),
                (3, 'unroutable', 'backend_failure'),
                (4, 'budget_exhausted', 'backend_failure'),
                (5, 'work_exhausted', 'backend_failure'),
                (6, 'operation_timeout', 'timeout'),
                (6, 'cancelled', 'cancelled'),
                (6, 'admission_exceeded', 'backend_failure'),
                (7, 'internal_error', 'backend_failure'),
                (8, 'usage_refused', 'refused'),
                (8, 'publication_refused', 'refused'),
                (8, 'destination_exists', 'refused'),
                (8, 'ambiguous_termination', 'contradiction'),
            ]
            for code, status, expected in cases:
                op = json.loads(json.dumps(base_op))
                op['exit_code'], op['status'] = code, status
                if code != 0:
                    op['publication']['published'] = False
                    op['publication']['output_sha256'] = None
                self.assertEqual(attempt.native_semantics(code, op, req_file, src, out)[0], expected, (code, status))
            # Pre-snapshot admission refusals (M5.2.1 P2): source_sha256 is None before source admission.
            for code, status, expected in (
                (8, 'destination_exists', 'refused'),
                (8, 'publication_refused', 'refused'),
                (8, 'usage_refused', 'refused'),
                (2, 'invalid', 'refused'),
                (2, 'unsupported', 'refused'),
                (8, 'ambiguous_termination', 'contradiction'),
                (3, 'unroutable', 'contradiction'),
                (2, 'native_validation_failed', 'contradiction'),
                (6, 'admission_exceeded', 'contradiction'),
                (0, 'complete', 'contradiction'),
            ):
                op = json.loads(json.dumps(base_op))
                op['exit_code'], op['status'] = code, status
                op['source'] = {'source_sha256': None, 'sidecar_check': {'passed': False}}
                if code != 0:
                    op['routing'] = None
                    op['publication']['published'] = False
                    op['publication']['output_sha256'] = None
                self.assertEqual(
                    attempt.native_semantics(code, op, req_file, src, out)[0],
                    expected,
                    (code, status),
                )
            # Pre-admission >16 MiB source/sidecar admission_exceeded has source_sha256=None
            # strictly before snapshot_stage/source_bytes/request_sha256 (and no saved request file).
            absent_req = root / 'absent-request.json'
            pre_adm = json.loads(json.dumps(base_op))
            pre_adm['exit_code'], pre_adm['status'] = 6, 'admission_exceeded'
            pre_adm['source'] = {'source_sha256': None, 'source_bytes': None, 'snapshot_sha256': None,
                                 'sidecar_check': {'passed': True}}
            pre_adm['request'] = {'request_sha256': None}
            pre_adm['routing'] = None
            pre_adm['publication']['published'] = False
            pre_adm['publication']['output_sha256'] = None
            pre_adm['phases'] = [{'phase': 'pre_snapshot_admission', 'status': 'failed'}]
            self.assertEqual(attempt.native_semantics(6, pre_adm, absent_req, src, out)[0], 'backend_failure')
            for mutate_adm in (
                lambda o: o['phases'].append({'phase': 'snapshot_stage', 'status': 'ok'}),
                lambda o: o['phases'][0].update(status='ok'),
                lambda o: o['source'].update(source_bytes=123),
                lambda o: o['source'].update(snapshot_sha256='0' * 64),
                lambda o: o['request'].update(request_sha256=req_sha),
                lambda o: o.update(routing={'status': 'admission_exceeded', 'request_sha256': None}),
            ):
                bad_adm = json.loads(json.dumps(pre_adm))
                mutate_adm(bad_adm)
                self.assertEqual(attempt.native_semantics(6, bad_adm, absent_req, src, out)[0], 'contradiction')
            # Non-None source_sha256 mismatch still fails closed to contradiction on exit 8.
            bad_src = json.loads(json.dumps(base_op))
            bad_src['exit_code'], bad_src['status'] = 8, 'destination_exists'
            bad_src['routing'] = None
            bad_src['publication']['published'] = False
            bad_src['publication']['output_sha256'] = None
            bad_src['source']['source_sha256'] = '0' * 64
            self.assertEqual(attempt.native_semantics(8, bad_src, req_file, src, out)[0], 'contradiction')
            # Tamper checks: every binding mismatch fails closed to contradiction.
            for mutate in (
                lambda o: o['request'].update(request_sha256='0' * 64),
                lambda o: o['routing'].update(request_sha256='0' * 64),
                lambda o: o['source']['sidecar_check'].update(passed=False),
                lambda o: o['publication'].update(published=False),
                lambda o: o['publication'].update(ambiguous_termination=True),
                lambda o: o['publication'].update(output_sha256='0' * 64),
                lambda o: o.update(status='budget_exhausted'),
            ):
                bad = json.loads(json.dumps(base_op))
                mutate(bad)
                self.assertEqual(attempt.native_semantics(0, bad, req_file, src, out)[0], 'contradiction')

    def test_re01_audit_hook_and_auxiliary_caller_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            co = Path(tmp) / 'kicad-route'
            (co / 'tools').mkdir(parents=True)
            (co / '.venv-krt').mkdir()
            (co / 'bin').mkdir()
            (co / 'src/kicad_route').mkdir(parents=True)
            (co / 'src/kicad_route/__init__.py').write_text('MARKER = "krt"\n')
            krt_bin = co / 'bin/kicad-route'
            krt_bin.write_text('#!/bin/sh\nexit 0\n')
            krt_bin.chmod(0o755)
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('import sys; sys.exit(0)\n')
            self.assertIsNone(routing_adapter.router_recipe(co))
            for fn in (
                lambda: routing_adapter.spokes_argv(co, '/run'),
                lambda: routing_adapter.preflight_stackup_argv(co, '/b.kicad_pcb', 'F.Cu'),
                lambda: routing_adapter.preflight_delay_argv(co, '/b.kicad_pcb', ['N']),
                lambda: routing_adapter.post_route_checker_argv(co, 'pair_impedance', '/b', '/req', '/res'),
                lambda: routing_adapter.board_recipe_argv(['kicad-route-engine', 'route-native'], '/out', 10, 'auto'),
                lambda: routing_adapter.board_retry_argv(['kicad-route-engine', 'route-native'], '/retry', 10, {'kind': 'thermal_corridors', 'pads': []}, [], '/b.kicad_pcb'),
            ):
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    fn()
            fake_engine = Path(tmp) / 'kicad-route-engine'
            fake_engine.symlink_to(krt_bin)
            outside_dir = Path(tmp) / 'outside'
            outside_dir.mkdir()
            plain_krt = outside_dir / 'kicad-route'
            plain_krt.write_text('#!/bin/sh\nexit 0\n')
            plain_krt.chmod(0o755)
            with routing_adapter.re01_enforced():
                for argv in (
                    [sys.executable, str(co / 'bin/kicad-route'), '--help'],
                    [sys.executable, str(co / 'src/kicad_route/cli.py')],
                    [sys.executable, '-m', 'kicad_route.cli'],
                    [sys.executable, '-mkicad_route.cli'],
                    [sys.executable, '-Bc', 'import kicad_route'],
                    [sys.executable, '-Ic', 'import kicad_route'],
                    [str(fake_engine), '--help'],
                    ['/bin/sh', '-c', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-ce', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-ce', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/bin/bash', '-cu', f'PYTHONPATH={co / "src"} {sys.executable} -c "import kicad_route"'],
                    ['/bin/sh', '-eu', '-c', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-c', '-e', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/bin/sh', '-c', '--', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/bin/bash', '-c', '-o', 'errexit', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/bin/sh', '-eo', 'errexit', '-c', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/bash', '-eo', 'pipefail', '-c', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '+eo', 'errexit', '-c', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-c', '-eo', 'errexit', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/bash', '-c', '-eo', 'errexit',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c "import kicad_route"'],
                    ['/bin/bash', '-c', '-eO', 'extglob',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c "import kicad_route"'],
                    ['/bin/sh', '-ceo', 'errexit', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-coe', 'errexit', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/bash', '-co', 'errexit', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-cO', 'extglob', f'PATH={co / "bin"}:$PATH kicad-route'],
                    ['/bin/sh', '-c', f'PATH={co / "bin"}:$PATH ki\\\ncad-route'],
                    ['/bin/sh', '-c', f'PATH={co / "bin"}:$PATH ki\\\r\ncad-route'],
                    ['/usr/bin/env', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-i', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-u', 'HOME', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-iu', 'HOME', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-iuHOME', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-iC', '/tmp', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-iC/tmp', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-uS', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-a', 'foo', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-ia', 'foo', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '--argv0', 'foo', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-f', '/dev/null', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-if', '/dev/null', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '--file', '/dev/null', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-u', 'HOME', '-a', 'foo', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-a', 'foo', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c "import kicad_route.__main__"'],
                    ['/usr/bin/env', '-f', '/dev/null', '-S',
                     f'PYTHONPATH={co / "src"} /bin/sh -c "{sys.executable} -m kicad_route"'],
                    ['/usr/bin/env', '--ignore-signal', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    [sys.executable, '-c', 'exec("import kicad_\\\nroute")'],
                    [sys.executable, '-cexec("import kicad_\\\nroute")'],
                    [sys.executable, '-c', 'exec("import kicad_\\\r\nroute")'],
                    ['/usr/bin/env', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c "exec(\'import kicad_\\\\\\nroute\')"'],
                    ['/usr/bin/env', f'-SPYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', f'--split-string=PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m', 'kicad_route'],
                    ['/usr/bin/env', f'PYTHONPATH={co / "src"}', sys.executable, '-m', 'kicad_route'],
                    ['/usr/bin/env', '-S', '', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', 'FOO=1', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', '-u HOME', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', '-a foo', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', '-i',
                     f'--split-string=PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-SFOO=1', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', '--', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', '-i --', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c "import kicad_route"'],
                    ['/usr/bin/env', '-S', 'FOO=1 /bin/sh', '-S',
                     f'-c "PATH={co / "bin"}:${{PATH}} kicad-route"'],
                    ['/usr/bin/env', '-S', 'FOO=1 /usr/bin/env', '-S',
                     f'-S "PYTHONPATH={co / "src"} {sys.executable} -m kicad_route"'],
                    ['/usr/bin/env', '--argv', 'foo', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', f'--split-s=PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                    ['/usr/bin/env', '-S', f"FOO='x\\' ' {sys.executable} -c 'import kicad_route\n#\\' '"],
                    ['/usr/bin/env', '-S',
                     f"/usr/bin/env -a 'x\\' ' {sys.executable} -c 'import kicad_route\n#\\' '"],
                    ['/usr/bin/env', '-S', f"'{co / 'bin/ki'}\\\ncad-route'"],
                    ['/usr/bin/env', '-S', f"'/bin/s\\\nh' -c 'PATH={co / 'bin'}:$PATH kicad-route'"],
                    ['/usr/bin/env', '-S',
                     f"FOO='a\\' ' /bin/sh -c 'PATH={co / 'bin'}:$PATH;ki'cad-route''"],
                    ['/usr/bin/env', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c\\"a\xa0\\";import\\tkicad_route'],
                    ['/usr/bin/env', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c\\"a\x1c\\";import\\tkicad_route'],
                    ['/usr/bin/env', '-S',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c\\"a\u2003\\";import\\tkicad_route'],
                    [sys.executable, '-c', 'exec("import kicad_\\\rroute.__main__")'],
                    [sys.executable, '-cexec("import kicad_\\\rroute.__main__")'],
                    [sys.executable, '-Ic', 'exec("import kicad_\\\rroute.__main__")'],
                    [sys.executable, '-W', 'ignore', '-c', 'exec("import kicad_\\\rroute.__main__")'],
                    [sys.executable, '-X', 'dev', '-c', 'exec("import kicad_\\\rroute")'],
                    ['/bin/sh', '-c',
                     f'PYTHONPATH={co / "src"} {sys.executable} -c \'exec("import kicad_\\\rroute.__main__")\''],
                    ['/usr/bin/env', '-S',
                     f"PYTHONPATH={co / 'src'} {sys.executable} -c 'exec(\"import kicad_\\\rroute.__main__\")'"],
                    ['/bin/sh', '-c',
                     f'FOO=a#b PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/bash', '-c', f'FOO=a#b PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'FOO=a\\\n#b PATH={co / "bin"}:$PATH ki""cad-route'],
                    [sys.executable, '-c', 'import runpy; runpy.run_path("bin/kicad-route")'],
                    [sys.executable, '-c', 'import pathlib; pathlib.Path(".venv-krt/bin/python")'],
                    ['/bin/sh', '-c', f'echo ok\r#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/sh', '-c', f'echo ok\x0b#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/sh', '-c', f'echo ok\x0c#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/bash', '-c', f'echo ok\r#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo ok\x0b#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo ok\x0c#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(echo hi)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(echo hi)#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/bash', '-c', f'echo $(echo hi)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $((1+1))#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $((1+1))#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/sh', '-c', f'echo $(( (1+2) ))#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $(( (1+2) ))#; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/bash', '-c', f'a=(1)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo @(a)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo <(echo hi)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(echo ${{x#(}})#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(case x in x) echo hi;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo `echo hi # c`; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo `echo hi # c`; PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/sh', '-c', f'echo "`echo hi # "`"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo `echo hi`#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f"cat <<'EOF'\n'\nEOF\necho ' #; ' ; PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/sh', '-c', f'cat <<EOF\n"\nEOF\nPYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/sh', '-c', f"cat <<-'EOF'\n\t'\n\tEOF\necho ' #; ' ; PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/sh', '-c', f'cat <<A <<B\n"\nA\n\'\nB\nPATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'cat <<EOF\n$(echo ok)\n$(PATH={co / "bin"}:$PATH ki""cad-route)\nEOF'],
                    ['/bin/sh', '-c', f"echo $(cat <<'EOF'\n)\nEOF\n)#; PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/bash', '-c', f'echo $(cat <<EOF)\n"\nEOF\nPATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f"echo $'a\\'' ' ; #'; PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/bash', '-c', f"echo $'\\'';PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/bash', '-c', f"PATH={co / 'bin'}:$PATH ki$'\\x63'ad-route"],
                    ['/bin/sh', '-c', f"echo $'a\\';PATH={co / 'bin'}:$PATH ki\"\"cad-route;#'"],
                    ['/bin/bash', '-c', f'PATH={co / "bin"}:$PATH ki$""cad-route'],
                    ['/bin/sh', '-c', f'echo "$(echo hi # "\n) #"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "$(echo \'"\') #"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "$(case x in \'"\') echo hi;; esac) #"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "$( # )\nPATH={co / "bin"}:$PATH ki""cad-route\n)"'],
                    ['/bin/sh', '-c', f'echo "${{x:-\"\'\"}}" ;PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "${{x:-\'}}";PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "${{x:-\" # \"}}" ; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(( 1 << 2 ))\nPATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $((cat <<EOF); PATH={co / "bin"}:$PATH ki""cad-route\na\nEOF\n)'],
                    ['/bin/bash', '-c', f'((echo hi # c\n); PATH={co / "bin"}:$PATH ki""cad-route)'],
                    ['/bin/bash', '-c', f'(( 1 + # )); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'for (( i=0; # ; i++ )); do :; done; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'echo @(a|#b); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'shopt -s extglob\necho @(#); PYTHONPATH={co / "src"} {sys.executable} -m ki""cad_route'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'echo @(a(b|#c)); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'echo @($(echo hi # c\n)|#d); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'[[ a == @(#) ]]; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'!(#); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'!(echo hi # c\nPATH={co / "bin"}:$PATH ki""cad-route)'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'x=@(#); a=(@(#)); PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(case case in case) echo ok;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(case in in esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(case x in x) if true; then :; fi esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $(time -p -- case x in x) :;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $(coproc MYP case x in x) :;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $(function f case x in x) :;; esac; f)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    [str(plain_krt)],
                    [sys.executable, '-c', 'import runpy; runpy.run_module("ki""cad_route")'],
                    [sys.executable, '-W', 'ignore', '-c', 'import runpy; runpy.run_module("ki""cad_route")'],
                    [sys.executable, '-c', f'import subprocess; subprocess.run(["{outside_dir}/ki""cad-route"])'],
                    ['/bin/bash', '-c', f'for(( i=0; # ; i++ )); do :; done; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo $(case x in(a) :;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo $(case x in(a) :;; esac)#; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f'echo "$(for x do case x in x) echo "hi # ";; esac; done)"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo "$(select x do case x in x) echo "hi # ";; esac; break; done)"; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'echo "$(!(echo ok # )\n)\n# )\'\nPATH={co / "bin"}:$PATH ki""cad-route\n# \'\n)"'],
                    ['/bin/bash', '-c', f'[[ x =~ (a|#b) ]]; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'[[ x =~ (a)# ]]; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'[[ ( x =~ (a|#b) ) ]]; PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/bash', '-c', f'a=( <(PATH={co / "bin"}:$PATH ki""cad-route) ); sleep 0.05'],
                    ['/bin/bash', '-c', f"echo $(cat <<'EOF'\n'\nEOF)\necho ' #; '; PATH={co / 'bin'}:$PATH ki\"\"cad-route"],
                    ['/bin/sh', '-c', f'/bin/sh >/dev/null -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'/bin/sh 2>/dev/null -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'/bin/sh -c >/dev/null "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/bash', '-c', f'/bin/sh {{fd}}>/dev/null -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'env >/dev/null -S "PATH={co / "bin"}:$PATH ki\'\'cad-route"'],
                    ['/bin/sh', '-c', f'PYTHONPATH={co / "src"} {sys.executable} -m >/dev/null ki""cad_route'],
                    ['/bin/sh', '-c', f'PYTHONPATH={co / "src"} {sys.executable} -c >/dev/null "import kicad_route"'],
                    ['/bin/sh', '-c', f'exec >/dev/null /bin/sh -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/bash', '-c', f'exec -a sh /bin/sh -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'true &>/dev/null /bin/sh -c "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/usr/bin/env', '-u', ';', '-S', f'PATH={co / "bin"} /bin/sh -c "ki\'\'cad-route"'],
                    ['/usr/bin/env', '-a', ';', '-S', f'PATH={co / "bin"} /bin/sh -c "ki\'\'cad-route"'],
                    ['/bin/bash', '--rcfile', ';', '-c', f'PATH={co / "bin"}:$PATH ki""cad-route'],
                    ['/bin/sh', '-c', f"env -u ';' -S 'PATH={co / 'bin'} /bin/sh -c \"ki\\\"\\\"cad-route\"'"],
                    ['/bin/sh', '-c', f"env -a '(' -S 'PATH={co / 'bin'} /bin/sh -c \"ki\\\"\\\"cad-route\"'"],
                    ['/bin/sh', '-c', f'env -u "\n" -S \'PATH={co / "bin"} /bin/sh -c "ki\\"\\"cad-route"\''],
                    ['/bin/sh', '-c', f'eval "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'FOO=1 eval "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f'command eval "PATH={co / "bin"}:$PATH ki\\"\\"cad-route"'],
                    ['/bin/sh', '-c', f"/bin/sh <<'EOF'\nPATH={co / 'bin'}:$PATH ki\"\"cad-route\nEOF"],
                    ['/bin/bash', '-c', f"/bin/bash <<< 'PATH={co / 'bin'}:$PATH ki\"\"cad-route'"],
                    ['/bin/bash', '-c', f"/bin/bash <(echo 'PATH={co / 'bin'}:$PATH ki\"\"cad-route')"],
                    ['/bin/sh', '-c', f"echo 'PATH={co / 'bin'}:$PATH ki\"\"cad-route' | /bin/sh"],
                    ['/bin/sh', '-c', f"echo 'PATH={co / 'bin'}:$PATH ki\"\"cad-route' | /bin/sh -sc 'echo ok'"],
                    ['/bin/sh', '-c', f"echo 'import kicad_route' | {sys.executable}"],
                    ['/bin/sh', '-c', f"echo 'import kicad_route' | {sys.executable} -W ignore"],
                    ['/bin/sh', '-c', f"echo 'import kicad_route' | {sys.executable} -"],
                    ['/bin/sh', '-c', f'{sys.executable} < {bridge}'],
                    ['/bin/sh', '-c', f'/bin/sh < {bridge}'],
                    ['/bin/sh', '-c', f'echo `echo hi # c`; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo `echo hi # ki""cad-route`; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'cat <<EOF >/dev/null\nunbalanced "\nEOF\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f"cat <<'EOF' >/dev/null\n$(echo ki\"\"cad-route)\nEOF\n{sys.executable} {bridge}"],
                    ['/bin/bash', '-c', f"echo $'a\\'' # ki\"\"cad-route\n{sys.executable} {bridge}"],
                    ['/bin/bash', '-c', f"echo $'hello\nworld'\n{sys.executable} {bridge}"],
                    ['/bin/bash', '-c', f'x=a@; echo ${{x#@(}}; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'x=a; echo ${{x#(}}; {sys.executable} {bridge}'],
                    ['/bin/bash', '-c', f'((1+1))# ki""cad-route\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo $(case x in x)# ki""cad-route\necho hi;; esac)\n{sys.executable} {bridge}'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'echo @($(echo hi # ki""cad-route\n))\n{sys.executable} {bridge}'],
                    ['/bin/bash', '-O', 'extglob', '-c', f'echo !(#); {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo $(( 1 << 2 )) >/dev/null; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo "${{x:-\"\'\"}}" >/dev/null; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'case x in esac; {sys.executable} {bridge}'],
                    ['/bin/bash', '-c', f'for ((i=0;i<1;i++)); do :; done; {sys.executable} {bridge}'],
                ):
                    with self.subTest(argv=argv):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            subprocess.run(argv, timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    routing_adapter.check_re01_command(
                        ['/usr/bin/env'] + ['-S', 'A=1'] * 8 + ['/bin/echo', 'ok'])
                for sh_name in ('dash', 'zsh', 'ksh', 'ash'):
                    with self.subTest(shell=sh_name):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            routing_adapter.check_re01_command([sh_name, '-ce', f'PATH={co / "bin"}:$PATH kicad-route'])
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    routing_adapter.check_re01_command(
                        ['busybox', 'sh', '-ce', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'])
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run(['innocent', '-ce', f'PATH={co / "bin"}:$PATH kicad-route'],
                                   executable='/bin/sh', timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run(['innocent', '-S', f'PYTHONPATH={co / "src"} {sys.executable} -m kicad_route'],
                                   executable='/usr/bin/env', timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run(f'PATH={co / "bin"}:$PATH kicad-route', shell=True, timeout=5)
                for ws in ('\r', '\x0b', '\x0c'):
                    with self.subTest(shell_true_ws=repr(ws)):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            subprocess.run(f'echo ok{ws}#; {fake_engine}', shell=True, timeout=5)
                for cmd in (
                    'bin/kicad-route',
                    b'kicad-route',
                    'echo `kicad-route`',
                    f'echo `{fake_engine}`',
                    f'echo "$({fake_engine})"',
                    f'true && /bin/sh -ce "{fake_engine}"',
                    f'echo ok\r#; {fake_engine}',
                    f'echo ok\x0b#; {fake_engine}',
                    f'echo ok\x0c#; {fake_engine}',
                    f'echo `echo hi # c`; {fake_engine}',
                    f'/bin/bash -O extglob -c \'echo @(a|#b); {fake_engine}\'',
                ):
                    with self.subTest(cmd=cmd):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            os.system(cmd)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run(f'echo `echo hi # c`; {fake_engine}', shell=True, timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run(['/bin/sh', '-c', '$( ' * 70 + 'echo hi' + ' )' * 70], timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    os.popen(f'/bin/bash -cu "PATH={co / "bin"}:$PATH kicad-route"')
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    os.posix_spawn('/bin/sh', [b'/bin/sh', b'-c', b'kicad-route'], os.environ)
                for blocked_argv in (
                    ['/bin/sh', '-ce', 'true'],
                    ['/bin/sh', '-eo', 'errexit', '-c', 'true'],
                    ['/bin/sh', '-ceo', 'errexit', 'true'],
                    ['/usr/bin/env', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '-iu', 'HOME', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '-iC/tmp', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '-a', 'foo', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '-f', '/dev/null', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '--ignore-signal', '-S', f'{sys.executable} {bridge}'],
                    [sys.executable, '-c', 'print("ok\\\nstill_ok")'],
                    ['/usr/bin/env', '-S', 'FOO=1', '/bin/echo', '-S', 'ok'],
                    ['/usr/bin/env', '-S', 'FOO=1', '-S', f'{sys.executable} {bridge}'],
                    ['/usr/bin/env', '-S', 'FOO=1', sys.executable, str(bridge), '-S', 'arg'],
                    ['/usr/bin/env', '--', '/bin/echo', '-S', 'ok'],
                    ['/usr/bin/env', '-S', f"FOO='x\\'y' {sys.executable} {bridge}"],
                    ['/usr/bin/env', '-S', f"FOO='a\\\\' {sys.executable} {bridge}"],
                    ['/usr/bin/env', '-S', f"FOO='a\\\nb' {sys.executable} {bridge}"],
                    ['/usr/bin/env', '-S', f"{sys.executable} -c 'print(\\'ok\\\\\\\\\\')'"],
                    ['/usr/bin/env', '-S', f"{sys.executable} -c 'print(\"a\xa0b\")'"],
                    [sys.executable, '-c', 'print("ok\\\r")'],
                    ['/bin/sh', '-c', f'FOO=a#b {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo ok # -c\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo ok # comment\\\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo ok\r#comment\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'(echo ok)#comment\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'(echo ok)# -c\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo $(echo hi)#lit {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'! (echo ok # ki""cad-route\n); {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo "$(echo hi # ki""cad-route\n)" >/dev/null; {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo a 2>&1 # ki""cad-route\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'/bin/sh >/dev/null -c "{sys.executable} {bridge}"'],
                    ['/bin/sh', '-c', f'/bin/sh 2>/dev/null -c "{sys.executable} {bridge}"'],
                    ['/bin/sh', '-c', f'/usr/bin/env >/dev/null -S "{sys.executable} {bridge}"'],
                    ['/bin/sh', '-c', f'FOO=1 >/dev/null {sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'exec >/dev/null {sys.executable} {bridge}'],
                    [sys.executable, '-c', 'print({})'],
                    ['/bin/sh', '-c', 'echo "{}" >/dev/null'],
                    ['/bin/sh', '-c', f'echo ok # `foo\n{sys.executable} {bridge}'],
                    ['/bin/sh', '-c', f'echo hi | /bin/sh -c "{sys.executable} {bridge}"'],
                    ['/bin/sh', '-c', f'echo hi | {sys.executable} {bridge}'],
                    [sys.executable],
                    [sys.executable, '-'],
                    [sys.executable, '--', '-'],
                    [sys.executable, '-W', 'ignore', str(bridge)],
                    [sys.executable, '-X', 'dev', str(bridge)],
                    [sys.executable, '-BW', 'ignore', str(bridge)],
                    [sys.executable, '--check-hash-based-pycs=always', str(bridge)],
                    [sys.executable, '/dev/stdin'],
                    [sys.executable, '/dev/fd/0'],
                    [sys.executable, '/proc/thread-self/fd/0'],
                    [sys.executable, str(outside_dir)],
                    ['xargs', 'echo', 'ok'],
                    ['find', str(outside_dir), '-exec', 'echo', '{}', ';'],
                    ['perl', '-e', 'print 1'],
                    ['ruby', '-e', 'puts 1'],
                    ['node', '-e', 'console.log(1)'],
                    ['awk', 'BEGIN{print 1}'],
                    ['sed', '-n', 'p'],
                    ['make', '--version'],
                    ['git', '-C', str(co), '-c', 'core.fsmonitor=false', '--version'],
                ):
                    with self.subTest(blocked_argv=blocked_argv):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            subprocess.run(blocked_argv, timeout=5)
                for ok_argv in (
                    [sys.executable, str(bridge)],
                    [sys.executable, '-B', '-I', str(bridge)],
                    [sys.executable, '-BI', str(bridge)],
                    [sys.executable, '-OO', '-P', '-q', '-s', '-S', '-u', '-v', '-E', str(bridge)],
                    [sys.executable, '--', str(bridge)],
                    [sys.executable, str(bridge), '-c', 'arg-after-script'],
                    ['bwrap', '--version'],
                ):
                    with self.subTest(ok_argv=ok_argv):
                        done = subprocess.run(ok_argv, capture_output=True, timeout=5)
                        self.assertEqual(done.returncode, 0, done.stderr)

    def test_re01_direct_exec_allowlist_shebang_env_cwd_and_bwrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            co = root / 'kicad-route'
            (co / 'bin').mkdir(parents=True)
            (co / 'src/kicad_route').mkdir(parents=True)
            (co / 'tools').mkdir(parents=True)
            krt_bin = co / 'bin/kicad-route'
            krt_bin.write_text(f'#!/bin/sh\necho HIT >> "{marker}"\n')
            krt_bin.chmod(0o755)
            (co / 'src/kicad_route/__init__.py').write_text('')
            (co / 'src/kicad_route/__main__.py').write_text(f'open({str(marker)!r}, "a").write("MOD\\n")\n')
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('#!/usr/bin/env python3\nimport sys; sys.exit(0)\n')
            bridge.chmod(0o755)
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'kicad-route').write_text(f'#!/bin/sh\necho PLAIN >> "{marker}"\n')
            (outside / 'kicad-route').chmod(0o755)
            extra_bin = root / 'extra_bin'
            extra_bin.mkdir()
            (extra_bin / 'mytool').symlink_to(krt_bin)
            (outside / 'mytool').symlink_to(krt_bin)
            site_dir = root / 'site'
            site_dir.mkdir()
            (site_dir / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "a").write("SITE\\n")\n')
            startup_py = root / 'startup.py'
            startup_py.write_text(f'open({str(marker)!r}, "a").write("START\\n")\n')
            not_py = root / 'plain_script'
            not_py.write_text('print(1)\n')
            dir_py = root / 'dir.py'
            dir_py.mkdir()
            (dir_py / '__main__.py').write_text(f'open({str(marker)!r}, "a").write("DIR\\n")\n')
            zip_py = root / 'fake_zip.py'
            zip_py.write_bytes(b'PK\x03\x04dummy')
            shebang_sh = outside / 'wrap_sh'
            shebang_sh.write_text(f'#!/bin/sh\necho SB >> "{marker}"\n')
            shebang_sh.chmod(0o755)
            shebang_env = outside / 'wrap_env'
            shebang_env.write_text(f'#!/usr/bin/env python3\nopen({str(marker)!r}, "a").write("ENV\\n")\n')
            shebang_env.chmod(0o755)
            shebang_krt = outside / 'wrap_krt'
            shebang_krt.write_text(f'#!{krt_bin}\n')
            shebang_krt.chmod(0o755)
            shebang_pyc = outside / 'wrap_pyc.py'
            shebang_pyc.write_text('#!/usr/bin/python3 -c\n')
            shebang_pyc.chmod(0o755)
            shebang_py_noext = outside / 'wrap_py_noext'
            shebang_py_noext.write_text(f'#!/usr/bin/python3\nopen({str(marker)!r}, "a").write("NOEXT\\n")\n')
            shebang_py_noext.chmod(0o755)
            with routing_adapter.re01_enforced():
                for fn in (
                    lambda: subprocess.run(['./kicad-route'], cwd=outside, timeout=5),
                    lambda: subprocess.run(['./mytool'], cwd=outside, timeout=5),
                    lambda: subprocess.run(['mytool'], env={'PATH': str(extra_bin)}, timeout=5),
                    lambda: subprocess.run([sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONSTARTUP': str(startup_py)}, timeout=5),
                    lambda: subprocess.run([sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(site_dir)}, timeout=5),
                    lambda: subprocess.run([sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(co / 'src')}, timeout=5),
                    lambda: subprocess.run([str(shebang_sh)], timeout=5),
                    lambda: subprocess.run([str(shebang_env)], timeout=5),
                    lambda: subprocess.run([str(shebang_krt)], timeout=5),
                    lambda: subprocess.run([str(shebang_pyc)], timeout=5),
                    lambda: subprocess.run([str(shebang_py_noext)], timeout=5),
                    lambda: subprocess.run([sys.executable, '--', str(not_py)], timeout=5),
                    lambda: subprocess.run([sys.executable, str(dir_py)], timeout=5),
                    lambda: subprocess.run([sys.executable, str(zip_py)], timeout=5),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--ro-bind', str(co), '/opt/kicad-route', '--', '/opt/kicad-route/bin/kicad-route']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--ro-bind', str(krt_bin), '/tmp/innocent', '--', '/tmp/innocent']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--ro-bind', '/usr', '/usr', '--', '/bin/sh', '-c', 'true']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--ro-bind', '/usr', '/usr', '--', sys.executable, '-c', 'import kicad_route']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--unknown-opt', '--', '/bin/true']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--argv0', 'sh', '--', '/bin/true']),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--setenv', 'PYTHONPATH', str(site_dir), '--', sys.executable, str(bridge)]),
                    lambda: routing_adapter.check_re01_command(['bwrap', '--ro-bind', str(co / 'bin'), '/opt/b', '--symlink', '/opt/b/kicad-route', '/opt/alias', '--', '/opt/alias']),
                ):
                    with self.assertRaises(routing_adapter.RE01OwnershipError):
                        fn()
                self.assertFalse(marker.exists())
                routing_adapter.check_re01_command([
                    'bwrap', '--ro-bind', str(co), '/opt/kicad-route', '--',
                    '/usr/bin/kicad-route-engine', 'route-native', '/work/in.kicad_pcb', '/work/out.kicad_pcb',
                    '--diff-pairs', 'auto',
                ])
                routing_adapter.check_re01_command([
                    'bwrap', '--ro-bind', str(co), '/opt/kicad-route', '--',
                    '/usr/bin/kicad-route-engine', 'route-native', '/work/in.kicad_pcb', '/work/out.kicad_pcb',
                    '--diff-pairs', 'CAN+,CAN-', '--diff-pairs', 'USB_P,USB_N',
                ])
                for bad_dp in (
                    ['--diff-pairs', '-bad'],
                    ['--diff-pairs', 'auto', '--diff-pairs', 'CAN+,CAN-'],
                    ['--diff-pairs', 'auto', '--diff-pairs', 'auto'],
                    ['--diff-pairs', 'CAN+'],
                    ['--diff-pairs', 'CAN+,'],
                    ['--diff-pairs', ',CAN-'],
                    ['--diff-pairs', 'A,A'],
                    ['--diff-pairs=A,A'],
                    ['--diff-pairs', ' , '],
                    ['--diff-pairs', 'A, '],
                    ['--diff-pairs', ' ,B'],
                    ['--diff-pairs', '\t,\n'],
                    ['--diff-pairs', 'A,-B'],
                    ['--diff-pairs', 'A,B', '--diff-pairs', 'A,B'],
                    ['--diff-pairs', 'A,B', '--diff-pairs', 'A,C'],
                    ['--diff-pairs', 'A,B', '--diff-pairs', 'B,A'],
                    ['--diff-pairs', 'A\x00,B'],
                    ['--diff-pairs', 'A,\udcff'],
                ):
                    with self.subTest(bad_dp=bad_dp):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            routing_adapter.check_re01_command([
                                'bwrap', '--ro-bind', str(co), '/opt/kicad-route', '--',
                                '/usr/bin/kicad-route-engine', 'route-native',
                                '/work/in.kicad_pcb', '/work/out.kicad_pcb', *bad_dp,
                            ])
                routing_adapter.check_re01_command([
                    'bwrap', '--ro-bind', str(bridge), '/opt/bridge.py', '--',
                    sys.executable, '-BI', '/opt/bridge.py', '--help',
                ])
                done = subprocess.run([sys.executable, str(bridge)], capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_re01_r10_and_r11_vfs_env_cwd_payload_and_wrapper_hardening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            co = root / 'kicad-route'
            (co / 'bin').mkdir(parents=True)
            (co / 'src/kicad_route').mkdir(parents=True)
            (co / 'tools').mkdir(parents=True)
            krt_bin = co / 'bin/kicad-route'
            krt_bin.write_text(f'#!/bin/sh\necho HIT >> "{marker}"\n')
            krt_bin.chmod(0o755)
            krt_main = co / 'src/kicad_route/__main__.py'
            (co / 'src/kicad_route/__init__.py').write_text('')
            krt_main.write_text(f'open({str(marker)!r}, "a").write("MOD\\n")\n')
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('#!/usr/bin/env python3\nimport sys; sys.exit(0)\n')
            bridge.chmod(0o755)

            # Aud-1 / R2-F2: bare relative .py script anchored to cwd (not process CWD) & bwrap --chdir
            proc_cwd = root / 'proc_cwd'
            outside = root / 'outside'
            sub_path = outside / 'sub'
            proc_cwd.mkdir()
            sub_path.mkdir(parents=True)
            (proc_cwd / 'rel.py').write_text('print("ok")\n')
            (outside / 'rel.py').symlink_to(krt_main)
            (outside / 'mytool').symlink_to(krt_bin)
            (sub_path / 'mytool').symlink_to(krt_bin)

            # Aud-2 / R1-F6 / R2-F1 / C1-V4 / N1: shebang zipapp .py & renamed .pyc / binary .py
            zipapp_py = root / 'shebang_zip.py'
            with zipfile.ZipFile(zipapp_py, 'w') as zf:
                zf.writestr('__main__.py', f'open({str(marker)!r}, "a").write("ZIPAPP\\n")\n')
            zipapp_py.write_bytes(b'#!/usr/bin/python3\n' + zipapp_py.read_bytes())
            pyc_src = root / 'evil_src.py'
            pyc_src.write_text(f'open({str(marker)!r}, "a").write("PYC\\n")\n')
            pyc_as_py = root / 'renamed_pyc.py'
            py_compile.compile(str(pyc_src), cfile=str(pyc_as_py), doraise=True)
            nul_py = root / 'nul_payload.py'
            nul_py.write_bytes(b'# fake\n\x00binary')

            # N2 / R1-F5 / C2-4 / N8: PYTHONPATH .zip, package dirs, native_delay/grid_router
            site_dir = root / 'site'
            site_pkg = root / 'site_pkg'
            user_pkg = root / 'user_pkg'
            delay_dir = root / 'delay_dir'
            (site_pkg / 'sitecustomize').mkdir(parents=True)
            (user_pkg / 'usercustomize').mkdir(parents=True)
            delay_dir.mkdir()
            site_dir.mkdir()
            (site_dir / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "a").write("SITE\\n")\n')
            (site_pkg / 'sitecustomize/__init__.py').write_text(f'open({str(marker)!r}, "a").write("PKG\\n")\n')
            (user_pkg / 'usercustomize/__init__.py').write_text(f'open({str(marker)!r}, "a").write("UPKG\\n")\n')
            (delay_dir / 'native_delay.py').write_text('x = 1\n')
            pypath_zip = root / 'pypath_hook.zip'
            with zipfile.ZipFile(pypath_zip, 'w') as zf:
                zf.writestr('sitecustomize.py', f'open({str(marker)!r}, "a").write("ZIPSITE\\n")\n')
            krt_zip = root / 'krt_pkg.zip'
            with zipfile.ZipFile(krt_zip, 'w') as zf:
                zf.writestr('kicad_route/__init__.py', '')

            # C2-5 / N5 / N6 / N7: bare shebang, host shadow, mount-to-mount symlinks, relative bwrap
            bare_sb = outside / 'wrap_bare_sb'
            bare_sb.write_text('#!mytool\n')
            bare_sb.chmod(0o755)
            dir_a = root / 'dir_a'
            dir_b = root / 'dir_b'
            dir_a.mkdir()
            dir_b.mkdir()
            (dir_a / 'hop1').symlink_to('/opt/b/hop2')
            (dir_b / 'hop2').symlink_to('/bin/sh')
            innocent_py = root / 'innocent.py'
            innocent_py.write_text('print("innocent")\n')
            evil_dir = root / 'evil_dir'
            evil_dir.mkdir()
            (evil_dir / 'krt_alias').symlink_to(krt_bin)

            old_cwd = os.getcwd()
            try:
                os.chdir(proc_cwd)
                with routing_adapter.re01_enforced():
                    for label, fn in (
                        ('aud1_bare_rel_cwd', lambda: subprocess.run([sys.executable, 'rel.py'], cwd=outside, timeout=5)),
                        ('aud1_bwrap_chdir_rel', lambda: subprocess.run(
                            ['bwrap', '--ro-bind', '/', '/', '--chdir', str(outside), '--', sys.executable, 'rel.py'],
                            timeout=5)),
                        ('aud2_shebang_zipapp', lambda: subprocess.run([sys.executable, str(zipapp_py)], timeout=5)),
                        ('n1_renamed_pyc', lambda: subprocess.run([sys.executable, str(pyc_as_py)], timeout=5)),
                        ('n1_nul_py', lambda: subprocess.run([sys.executable, str(nul_py)], timeout=5)),
                        ('aud3_bwrap_bind_sh', lambda: subprocess.run(
                            ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--ro-bind', '/bin/sh',
                             '/tmp/innocent', '--setenv', 'PATH', '/tmp', '--', 'innocent', '-c', 'true'], timeout=5)),
                        ('aud3_bwrap_bind_py', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', sys.executable, '/tmp/notpy', '--', '/tmp/notpy', '-c', '1'])),
                        ('c2_1_bwrap_bind_fd', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind-fd', '3', '/opt/x', '--', '/bin/true'])),
                        ('c2_1_bwrap_args', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--args', '3', '--', '/bin/true'])),
                        ('c2_1_bwrap_overlay', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--overlay-src', '/tmp', '--ro-overlay', '/opt', '--', '/bin/true'])),
                        ('r1_f3_bwrap_rel_symlink', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', str(krt_bin), '/opt/real', '--symlink', '../real',
                             '/opt/sub/link', '--', '/opt/sub/link'])),
                        ('c2_1_bwrap_symlink_cycle', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--symlink', '/a', '/b', '--symlink', '/b', '/a', '--', '/a'])),
                        ('r1_f3_bwrap_try_nonexistent_does_not_mask', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', str(krt_bin), '/opt/x', '--ro-bind-try',
                             str(root / 'missing'), '/opt/x', '--', '/opt/x'])),
                        ('r1_f1_bwrap_pypath_mount', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', str(site_dir), '/opt/s', '--setenv', 'PYTHONPATH',
                             '/opt/s', '--', sys.executable, str(bridge)])),
                        ('c1_child_mount_pypath', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', str(proc_cwd), '/opt/p', '--ro-bind',
                             str(site_dir / 'sitecustomize.py'), '/opt/p/sitecustomize.py',
                             '--setenv', 'PYTHONPATH', '/opt/p', '--', sys.executable, str(bridge)])),
                        ('n5_bwrap_host_file_shadow', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(zipapp_py), str(innocent_py),
                             '--', sys.executable, str(innocent_py)])),
                        ('n6_mount_to_mount_symlink', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', str(dir_a), '/opt/a', '--ro-bind', str(dir_b), '/opt/b',
                             '--', '/opt/a/hop1', '-c', 'true'])),
                        ('n6_nested_bwrap', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(evil_dir), '/opt/e',
                             '--', 'bwrap', '--ro-bind', '/opt/e/krt_alias', '/tmp/ok', '--', '/tmp/ok'])),
                        ('n7_bwrap_rel_ro_bind_src', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', 'rel.py', '/opt/rel.py', '--', sys.executable, '/opt/rel.py'],
                            cwd=outside)),
                        ('n7_bwrap_rel_chdir', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/', '--chdir', str(outside).lstrip('/'),
                             '--', sys.executable, 'rel.py'])),
                        ('r1_f4_rel_path_sub', lambda: subprocess.run(
                            ['mytool'], cwd=outside, env={'PATH': './sub'}, timeout=5)),
                        ('n4_empty_path_env', lambda: subprocess.run(
                            ['mytool'], cwd=outside, env={'PATH': ''}, timeout=5)),
                        ('c2_4_empty_pypath_seg', lambda: subprocess.run(
                            [sys.executable, str(bridge)], cwd=site_dir, env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': ':'},
                            timeout=5)),
                        ('r1_f5_site_pkg_dir', lambda: subprocess.run(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(site_pkg)},
                            timeout=5)),
                        ('r1_f5_user_pkg_dir', lambda: subprocess.run(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(user_pkg)},
                            timeout=5)),
                        ('n2_pypath_zip_site', lambda: subprocess.run(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(pypath_zip)},
                            timeout=5)),
                        ('n2_pypath_zip_krt', lambda: subprocess.run(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(krt_zip)},
                            timeout=5)),
                        ('n3_pythonuserbase', lambda: routing_adapter.check_re01_command(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONUSERBASE': str(root)})),
                        ('n3_pythoninspect', lambda: routing_adapter.check_re01_command(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'PYTHONINSPECT': '1'})),
                        ('n3_ld_preload', lambda: routing_adapter.check_re01_command(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'LD_PRELOAD': '/tmp/x.so'})),
                        ('n3_ld_audit', lambda: routing_adapter.check_re01_command(
                            [sys.executable, str(bridge)], env={'PATH': '/usr/bin:/bin', 'LD_AUDIT': '/tmp/x.so'})),
                        ('n8_native_delay_path', lambda: routing_adapter.check_re01_command(
                            [sys.executable, str(delay_dir / 'native_delay.py')])),
                        ('n8_grid_router_arg', lambda: routing_adapter.check_re01_command(
                            ['/bin/echo', str(root / 'grid_router/a.py')])),
                        ('n8_router_generations_arg', lambda: routing_adapter.check_re01_command(
                            ['/bin/echo', str(root / '.router-generations/abc/x')])),
                        ('c2_5_bare_shebang_cwd', lambda: subprocess.run(['./wrap_bare_sb'], cwd=outside, timeout=5)),
                        ('r2_f4_sh5', lambda: routing_adapter.check_re01_command(['sh5', '-c', 'true'])),
                        ('r2_f4_sh_dash', lambda: routing_adapter.check_re01_command(['sh-5.2', '-c', 'true'])),
                        ('r2_f4_su1', lambda: routing_adapter.check_re01_command(['su1'])),
                        ('r2_f4_su_rs', lambda: routing_adapter.check_re01_command(['su-rs'])),
                    ):
                        with self.subTest(case=label):
                            with self.assertRaises(routing_adapter.RE01OwnershipError):
                                fn()

                    # N9: newly blocked host wrappers/shells/runtimes
                    for wrapper in (
                        'static-sh', 'gnuenv', 'coreutils', 'script', 'flock', 'time', 'uv', 'uvx',
                        'pip', 'pip3', 'pytest', 'py.test', 'mise', 'rlwrap', 'socat', 'gdb', 'lldb',
                        'valgrind', 'systemd-run', 'runuser', 'run0', 'unshare', 'nsenter', 'capsh',
                        'pkexec', 'aa-exec', 'eatmydata', 'fakeroot',
                    ):
                        with self.subTest(wrapper=wrapper):
                            with self.assertRaises(routing_adapter.RE01OwnershipError):
                                routing_adapter.check_re01_command([wrapper, '--version'])

                    # Aud-4 & N7: inherited os.environ['PYTHONPATH'] inspected for env=None & bwrap --setenv
                    with unittest.mock.patch.dict(os.environ, {'PYTHONPATH': str(site_dir)}):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            subprocess.run([sys.executable, str(bridge)], timeout=5)
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            routing_adapter.check_re01_command(
                                ['bwrap', '--setenv', 'FOO', 'bar', '--', sys.executable, str(bridge)])
                        # Positive control: bwrap --unsetenv PYTHONPATH clears inherited poison
                        routing_adapter.check_re01_command(
                            ['bwrap', '--unsetenv', 'PYTHONPATH', '--', sys.executable, str(bridge)])

                    # C2-2: integer FD os.execve blocked in forked child
                    fd = os.open('/bin/true', os.O_RDONLY)
                    try:
                        pid = os.fork()
                        if pid == 0:
                            try:
                                os.execve(fd, [b'true'], {'PATH': '/usr/bin:/bin'})
                            except routing_adapter.RE01OwnershipError:
                                os._exit(42)
                            os._exit(0)
                        _, status = os.waitpid(pid, 0)
                        self.assertEqual(os.waitstatus_to_exitcode(status), 42)
                    finally:
                        os.close(fd)

                    self.assertFalse(marker.exists())

                    # Positive controls: PYTHONPATH=src:tests, LD_LIBRARY_PATH, bwrap --ro-bind / / + route-native
                    routing_adapter.check_re01_command(
                        [sys.executable, str(bridge)],
                        cwd=str(ROOT),
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': 'src:tests', 'LD_LIBRARY_PATH': '/usr/lib'},
                    )
                    done_bwrap = subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(bridge), '/opt/bridge.py',
                         '--', sys.executable, '-BI', '/opt/bridge.py'],
                        env={'PATH': '/usr/bin:/bin', 'LD_LIBRARY_PATH': '/usr/lib'},
                        capture_output=True, timeout=5,
                    )
                    self.assertEqual(done_bwrap.returncode, 0, done_bwrap.stderr)
            finally:
                os.chdir(old_cwd)

    def test_re01_r12_strict_allowlist_vfs_git_zip_venv_and_startup_hardening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            co = root / 'kicad-route'
            (co / 'bin').mkdir(parents=True)
            (co / 'tools').mkdir(parents=True)
            krt_bin = co / 'bin/kicad-route'
            krt_bin.write_text(f'#!/bin/sh\necho BIN_KRT >> "{marker}"\n')
            krt_bin.chmod(0o755)
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('#!/usr/bin/env python3\nimport sys; sys.exit(0)\n')
            bridge.chmod(0o755)

            dash_dir = root / 'dash_bin'
            dash_dir.mkdir()
            (dash_dir / '-mytool').symlink_to(krt_bin)
            evil_sbin = root / 'evil_sbin'
            evil_sbin.mkdir()
            (evil_sbin / 'mytool').symlink_to(krt_bin)
            host_link_base = root / 'host_link_base'
            (host_link_base / 'real/sub').mkdir(parents=True)
            (host_link_base / 'link').symlink_to('real')
            evil_sub = root / 'evil_sub'
            evil_sub.mkdir()
            (evil_sub / 'mytool').symlink_to(krt_bin)
            fake_true = root / 'true'
            shutil.copy2('/bin/sh', fake_true)
            fake_true.chmod(0o755)

            site_pkg = root / 'site_pkg'
            (site_pkg / 'sitecustomize').mkdir(parents=True)
            (site_pkg / 'sitecustomize/__init__.py').write_text(
                f'open({str(marker)!r}, "a").write("SITE_PKG\\n")\n')
            host_site_link = root / 'host_site_link'
            host_site_link.symlink_to('/var/tmp/empty')

            sub_zip = root / 'sub.zip'
            with zipfile.ZipFile(sub_zip, 'w') as zf:
                zf.writestr('sub/sitecustomize.py', f'open({str(marker)!r}, "a").write("ZIP_SUB\\n")\n')
            dat_zip = root / 'archive.dat'
            with zipfile.ZipFile(dat_zip, 'w') as zf:
                zf.writestr('a/b/encodings/__init__.py', f'open({str(marker)!r}, "a").write("DAT_ENC\\n")\n')
            dat_link = root / 'link.dat'
            dat_link.symlink_to(dat_zip)

            startup_dir = root / 'startup_hooks'
            for mod in ('types.py', 'apport_python_hook.py', '_distutils_hack.py', 'linecache.py'):
                startup_dir.mkdir(exist_ok=True)
                (startup_dir / mod).write_text(f'open({str(marker)!r}, "a").write("MOD\\n")\n')
            enc_dir = root / 'enc_override'
            (enc_dir / 'encodings').mkdir(parents=True)
            (enc_dir / 'encodings/__init__.py').write_text(f'open({str(marker)!r}, "a").write("ENC\\n")\n')

            sp0_dir = root / 'sp0_dir'
            (sp0_dir / 'kicad_route').mkdir(parents=True)
            (sp0_dir / 'kicad_route/__init__.py').write_text(f'open({str(marker)!r}, "a").write("SP0\\n")\n')
            (sp0_dir / 'json.py').write_text(f'open({str(marker)!r}, "a").write("SP0_JSON\\n")\n')
            sp0_script = sp0_dir / 'innocent.py'
            sp0_script.write_text('import sys; sys.exit(0)\n')

            myvenv = root / 'myvenv'
            (myvenv / 'bin').mkdir(parents=True)
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            (myvenv / f'lib/python{ver}/site-packages').mkdir(parents=True)
            (myvenv / 'pyvenv.cfg').write_text('home = /usr/bin\ninclude-system-site-packages = false\n')
            (myvenv / 'bin/python3').symlink_to(sys.executable)
            (myvenv / f'lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("PTH\\n")\n')
            ghost_venv = root / 'ghost_venv'
            (ghost_venv / 'bin').mkdir(parents=True)
            (ghost_venv / f'lib/python{ver}/site-packages').mkdir(parents=True)
            (ghost_venv / 'pyvenv.cfg').write_text('home = /usr/bin\ninclude-system-site-packages = false\n')
            (ghost_venv / f'lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("GHOST_PTH\\n")\n')
            fake_home = root / 'fake_home'
            (fake_home / f'.local/lib/python{ver}/site-packages').mkdir(parents=True)
            (fake_home / f'.local/lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("USER_PTH\\n")\n')

            git_repo = root / 'git_repo'
            git_repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(git_repo)], check=True)
            (git_repo / 'f.txt').write_text('hi\n')
            subprocess.run(['git', '-C', str(git_repo), 'add', 'f.txt'], check=True)
            subprocess.run(['git', '-C', str(git_repo), '-c', 'user.name=t', '-c', 'user.email=t@e',
                            'commit', '-qm', 'init'], check=True)
            (git_repo / '.git/config').write_text(
                (git_repo / '.git/config').read_text() + f'[core]\n\tfsmonitor = {krt_bin}\n')
            git_exec_dir = root / 'git_exec'
            git_exec_dir.mkdir()
            (git_exec_dir / 'git-mytool').symlink_to(krt_bin)
            tar_file = root / 'sample.tar'
            tar_file.write_bytes(b'\x00' * 1024)
            with routing_adapter.re01_enforced():
                for label, fn in (
                    ('ld_linux_sh', lambda: subprocess.run(
                        ['/lib64/ld-linux-x86-64.so.2', '/bin/sh', '-c', str(krt_bin)], timeout=5)),
                    ('ld_linux_py_c', lambda: subprocess.run(
                        ['/lib64/ld-linux-x86-64.so.2', sys.executable, '-c', 'import os'], timeout=5)),
                    ('ld_linux_bwrap', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', '/lib64/ld-linux-x86-64.so.2',
                         '/bin/sh', '-c', str(krt_bin)], timeout=5)),
                    ('ld_linux_alias', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/lib64/ld-linux-x86-64.so.2', '/opt/true', '--', '/opt/true'])),
                    ('qemu_x86_64', lambda: routing_adapter.check_re01_command(['qemu-x86_64', '/bin/true'])),
                    ('tar_to_cmd', lambda: subprocess.run(
                        ['tar', '-xf', str(tar_file), f'--to-command=/bin/sh -c "{krt_bin}"'], timeout=5)),
                    ('rsync_cmd', lambda: routing_adapter.check_re01_command(['rsync', '--version'])),
                    ('fake_true_copy', lambda: subprocess.run([str(fake_true), '-c', str(krt_bin)], timeout=5)),
                    ('unallowlisted_cat', lambda: routing_adapter.check_re01_command(['/usr/bin/cat', '/dev/null'])),
                    ('unallowlisted_s9', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/bin/true', '/opt/s9', '--', '/opt/s9'])),
                    ('engine_bad_subcmd', lambda: routing_adapter.check_re01_command(
                        ['kicad-route-engine', 'bogus'])),
                    ('popen_exec_mismatch', lambda: subprocess.Popen(
                        ['/usr/bin/true', str(bridge)], executable=sys.executable)),
                    ('dash_cmd_path', lambda: subprocess.run(
                        ['-mytool'], env={'PATH': f'{dash_dir}:/usr/bin:/bin'}, timeout=5)),
                    ('dash_cmd_bwrap', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(dash_dir), '/opt/d', '--setenv', 'PATH', '/opt/d:/usr/bin:/bin',
                         '--', '-mytool'], timeout=5)),
                    ('bwrap_host_sym_sbin', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_sbin), '/sbin', '--', '/usr/sbin/mytool'], timeout=5)),
                    ('bwrap_tmpfs_sbin_sym', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/sbin', '--ro-bind', str(evil_sbin), '/usr/sbin',
                         '--', '/sbin/mytool'], timeout=5)),
                    ('bwrap_host_link_sub', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/opt', '--ro-bind', str(host_link_base), '/opt/b',
                         '--ro-bind', str(evil_sub), '/opt/b/link/sub',
                         '--', '/opt/b/real/sub/mytool'], timeout=5)),
                    ('bwrap_sym_target_mount', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/opt', '--dir', '/opt/real', '--symlink', 'real', '/opt/l',
                         '--ro-bind', str(evil_sub), '/opt/l', '--', '/opt/real/mytool'], timeout=5)),
                    ('bwrap_tmpfs_sym_pypath', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/var/tmp/empty',
                         '--ro-bind', str(site_pkg / 'sitecustomize'), '/var/tmp/empty/sitecustomize',
                         '--symlink', '/var/tmp/empty', '/var/tmp/link',
                         '--setenv', 'PYTHONPATH', '/var/tmp/link',
                         '--', sys.executable, str(bridge)], timeout=5)),
                    ('bwrap_tmpfs_dotdot_pypath', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/var/tmp/empty',
                         '--ro-bind', str(site_pkg / 'sitecustomize'), '/var/tmp/empty/sitecustomize',
                         '--symlink', '/var/tmp/empty', '/var/tmp/link',
                         '--setenv', 'PYTHONPATH', '/var/tmp/link/../empty',
                         '--', sys.executable, str(bridge)], timeout=5)),
                    ('bwrap_host_sym_tmpfs_pypath', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/var/tmp/empty',
                         '--ro-bind', str(site_pkg / 'sitecustomize'), '/var/tmp/empty/sitecustomize',
                         '--setenv', 'PYTHONPATH', str(host_site_link),
                         '--', sys.executable, str(bridge)], timeout=5)),
                    ('bwrap_implicit_dir_sym_pypath', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', str(site_pkg / 'sitecustomize'), '/sbin/sitecustomize',
                         '--symlink', '/sbin', '/opt/link',
                         '--setenv', 'PYTHONPATH', '/opt/link',
                         '--', sys.executable, str(bridge)])),
                    ('git_exec_path', lambda: subprocess.run(
                        ['git', f'--exec-path={git_exec_dir}', 'mytool'], timeout=5)),
                    ('git_env_exec_path', lambda: subprocess.run(
                        ['git', 'mytool'], env={'PATH': '/usr/bin:/bin', 'GIT_EXEC_PATH': str(git_exec_dir)},
                        timeout=5)),
                    ('git_c_alias', lambda: subprocess.run(
                        ['git', '-c', f'alias.pwn=!{krt_bin}', 'pwn'], timeout=5)),
                    ('git_status_no_fsmonitor_false', lambda: subprocess.run(
                        ['git', '-C', str(git_repo), 'status', '--porcelain'], timeout=5)),
                    ('git_config_global_evil', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(git_repo), 'rev-parse', 'HEAD'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': str(root / 'evil.cfg')})),
                    ('git_editor_evil', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(git_repo), 'rev-parse', 'HEAD'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_EDITOR': '/bin/sh'})),
                    ('zip_subdir_direct', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': f'{sub_zip}/sub'}, timeout=5)),
                    ('zip_dat_subdir', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': f'{dat_zip}/a/b/'}, timeout=5)),
                    ('zip_sym_subdir', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': f'{dat_link}/a/b'}, timeout=5)),
                    ('zip_dotdot_subdir', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': f'{sub_zip}/x/../sub'}, timeout=5)),
                    ('zip_subdir_bwrap', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/opt', '--ro-bind', str(sub_zip), '/opt/z.zip',
                         '--setenv', 'PYTHONPATH', '/opt/z.zip/sub',
                         '--', sys.executable, str(bridge)], timeout=5)),
                    ('pypath_encodings', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(enc_dir)}, timeout=5)),
                    ('pypath_startup_hooks', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(startup_dir)}, timeout=5)),
                    ('env_pyvenv_launcher_BI', lambda: subprocess.run(
                        [sys.executable, '-BI', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', '__PYVENV_LAUNCHER__': str(myvenv / 'bin/python3')},
                        timeout=5)),
                    ('env_pythonexecutable_BI', lambda: subprocess.run(
                        [sys.executable, '-BI', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONEXECUTABLE': str(myvenv / 'bin/python3')},
                        timeout=5)),
                    ('env_pythonwarnings', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONWARNINGS': 'default::evil.W'})),
                    ('env_frozen_off', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHON_FROZEN_MODULES': 'off'})),
                    ('custom_venv_direct', lambda: subprocess.run(
                        [str(myvenv / 'bin/python3'), '-BI', str(bridge)], timeout=5)),
                    ('custom_venv_spoof_argv0', lambda: subprocess.Popen(
                        [str(myvenv / 'bin/python3'), str(bridge)], executable=sys.executable)),
                    ('custom_venv_nonexistent_argv0', lambda: subprocess.Popen(
                        [str(ghost_venv / 'bin/python3'), str(bridge)], executable=sys.executable)),
                    ('custom_venv_bwrap_cfg', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/',
                         '--ro-bind', str(myvenv / 'pyvenv.cfg'), '/usr/pyvenv.cfg',
                         '--ro-bind', str(bridge), '/opt/bridge.py',
                         '--', sys.executable, '-BI', '/opt/bridge.py'])),
                    ('custom_venv_bwrap_argv0', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--argv0', str(myvenv / 'bin/python3'),
                         '--', sys.executable, '-BI', str(bridge)])),
                    ('user_site_pth', lambda: subprocess.run(
                        [sys.executable, str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(fake_home)}, timeout=5)),
                    ('syspath0_no_I', lambda: subprocess.run(
                        [sys.executable, '-B', str(sp0_script)], timeout=5)),
                    ('git_rev_parse_blocked', lambda: subprocess.run(
                        ['git', '-C', str(git_repo), '-c', 'core.fsmonitor=false', 'rev-parse', 'HEAD'], timeout=5)),
                    ('git_status_porcelain_blocked', lambda: subprocess.run(
                        ['git', '-C', str(git_repo), '-c', 'safe.directory=*', '-c', 'core.fsmonitor=false',
                         '-c', 'core.hooksPath=/dev/null', '-c', 'core.untrackedCache=false',
                         'status', '--porcelain=v1', '-z'], timeout=5)),
                ):
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                for pos_argv, pos_env in (
                    ([sys.executable, str(bridge)], None),
                    ([sys.executable, '-BI', str(bridge)], None),
                    ([sys.executable, '-BI', str(sp0_script)], None),
                    ([sys.executable, '-BP', str(sp0_script)], None),
                    ([sys.executable, str(bridge)], {'PATH': '/usr/bin:/bin', 'HOME': str(fake_home), 'PYTHONNOUSERSITE': '1'}),
                    ([sys.executable, '-s', str(bridge)], {'PATH': '/usr/bin:/bin', 'HOME': str(fake_home)}),
                    (['bwrap', '--version'], None),
                    (['/bin/true'], None),
                    (['/usr/bin/echo', 'ok'], None),
                    (['bwrap', '--ro-bind', '/', '/', '--', '/bin/echo', 'bwrap-ok'], None),
                    (['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                      '--ro-bind', str(bridge), '/opt/bridge.py',
                      '--', sys.executable, '-BI', '/opt/bridge.py'],
                     {'PATH': '/usr/bin:/bin', 'LD_LIBRARY_PATH': '/usr/lib'}),
                ):
                    with self.subTest(pos_argv=pos_argv):
                        done = subprocess.run(pos_argv, env=pos_env, capture_output=True, timeout=5)
                        self.assertEqual(done.returncode, 0, done.stderr)
                routing_adapter.check_re01_command([
                    'bwrap', '--ro-bind', str(co), '/opt/kicad-route', '--',
                    '/usr/bin/kicad-route-engine', 'route-native', '/work/in.kicad_pcb', '/work/out.kicad_pcb',
                ])
                routing_adapter.check_re01_command(
                    [sys.executable, str(bridge)],
                    cwd=str(ROOT),
                    env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': 'src:tests', 'LD_LIBRARY_PATH': '/usr/lib'},
                )

    def test_re01_r13_engine_bwrap_usersite_argv0_and_git_hardening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            co = root / 'kicad-route'
            (co / 'src/kicad_route').mkdir(parents=True)
            (co / 'tools').mkdir(parents=True)
            (co / 'src/kicad_route/cli.py').write_text('x = 1\n')
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('import sys; sys.exit(0)\n')
            bridge.chmod(0o755)
            fake_engine_sh = root / 'kicad-route-engine'
            shutil.copy2('/bin/sh', fake_engine_sh)
            fake_engine_sh.chmod(0o755)
            rn_dir = root / 'rn_cwd'
            rn_dir.mkdir()
            (rn_dir / 'route-native').write_text(f'#!/bin/sh\necho SH_ENG >> "{marker}"\n')
            (rn_dir / 'route-native').chmod(0o755)
            fake_engine_py = root / 'py_eng/kicad-route-engine'
            fake_engine_py.parent.mkdir()
            shutil.copy2(os.path.realpath(sys.executable), fake_engine_py)
            fake_engine_py.chmod(0o755)

            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            myvenv = root / 'myvenv'
            (myvenv / 'bin').mkdir(parents=True)
            (myvenv / f'lib/python{ver}/site-packages').mkdir(parents=True)
            (myvenv / 'pyvenv.cfg').write_text('home = /usr/bin\ninclude-system-site-packages = false\n')
            (myvenv / 'bin/python3').symlink_to(sys.executable)
            (myvenv / 'bin/notpython').symlink_to(sys.executable)
            (myvenv / f'lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("VENV_PTH\\n")\n')

            poison_sp0 = root / 'poison_sp0'
            poison_sp0.mkdir()
            (poison_sp0 / 'json.py').write_text(f'open({str(marker)!r}, "a").write("SP0\\n")\n')
            clean_sp0 = root / 'clean_sp0'
            clean_sp0.mkdir()
            clean_bridge_json = clean_sp0 / 'bridge_json.py'
            clean_bridge_json.write_text('import json\n')
            evil_pycache = root / 'evil_pycache'
            evil_pycache.mkdir()
            (evil_pycache / 'json.cpython-314.pyc').write_bytes(b'x')

            evil_sc = root / 'evil_sitecust.py'
            evil_sc.write_text(f'open({str(marker)!r}, "a").write("SC\\n")\n')
            evil_site = root / 'evil_site'
            evil_site.mkdir()
            (evil_site / 'evil.pth').write_text(f'import builtins; open({str(marker)!r}, "a").write("DP\\n")\n')
            evil_enc = root / 'evil_enc'
            evil_enc.mkdir()
            (evil_enc / '__init__.py').write_text(f'open({str(marker)!r}, "a").write("ENC\\n")\n')
            evil_usr = root / 'evil_usr'
            (evil_usr / f'lib/python{ver}/encodings').mkdir(parents=True)
            (evil_usr / f'lib/python{ver}/encodings/__init__.py').write_text('')
            evil_so = root / 'evil.so'
            evil_so.write_text('x')
            fake_home = root / 'fake_home'
            (fake_home / f'.local/lib/python{ver}/site-packages').mkdir(parents=True)
            (fake_home / f'.local/lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("UP\\n")\n')
            pw_dir = pwd.getpwuid(os.getuid()).pw_dir
            fake_passwd = root / 'fake_passwd'
            fake_passwd.write_text(f'{pwd.getpwuid(os.getuid()).pw_name}:x:{os.getuid()}:{os.getgid()}::{fake_home}:/bin/sh\n')
            git_env = {
                'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_AUTHOR_DATE': '2026-11-18T00:00:00Z',
                'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e', 'GIT_COMMITTER_DATE': '2026-11-18T00:00:00Z',
            }
            repo = root / 'git_repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=git_env)
            (repo / 'f.txt').write_text('hello\n')
            (repo / '.gitattributes').write_text('*.txt filter=evil\n')
            subprocess.run(['git', '-C', str(repo), 'add', 'f.txt', '.gitattributes'], check=True, env=git_env)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'init'], check=True, env=git_env)
            (repo / 'f.txt').write_text('world\n')
            (repo / '.git/config').write_text(
                (repo / '.git/config').read_text()
                + f'[filter "evil"]\n\tclean = /bin/sh -c "echo F >> {marker}"\n'
                + f'\tsmudge = /bin/sh -c "echo S >> {marker}"\n')
            clean_repo = root / 'clean_repo'
            clean_repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(clean_repo)], check=True, env=git_env)
            (clean_repo / 'f.txt').write_text('hello\n')
            (clean_repo / '.gitattributes').write_text('*.txt filter=evil\n')
            subprocess.run(['git', '-C', str(clean_repo), 'add', 'f.txt', '.gitattributes'], check=True, env=git_env)
            subprocess.run(['git', '-C', str(clean_repo), 'commit', '-qm', 'init'], check=True, env=git_env)
            (clean_repo / 'f.txt').write_text('world\n')
            (clean_repo / '.git/modules/sub').mkdir(parents=True)
            sub_cfg = clean_repo / '.git/modules/sub/config'
            sub_cfg.write_text(f'[filter "sub"]\n\tclean = /bin/sh -c "echo SUB >> {marker}"\n')
            xdg_dir = root / 'xdg'
            (xdg_dir / 'git').mkdir(parents=True)
            (xdg_dir / 'git/config').write_text(f'[filter "evil"]\n\tclean = /bin/sh -c "echo XDG >> {marker}"\n')
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            with routing_adapter.re01_enforced():
                for label, fn in (
                    ('aud1_copy_sh_engine', lambda: subprocess.run([str(fake_engine_sh), 'route-native'], cwd=rn_dir, timeout=5)),
                    ('aud1_copy_py_engine', lambda: subprocess.run([str(fake_engine_py), 'route-native'], cwd=rn_dir, timeout=5)),
                    ('aud1_bwrap_usr_engine', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(fake_engine_sh), '/usr/bin/kicad-route-engine',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('aud1_bwrap_opt_engine', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(fake_engine_sh),
                         '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                         '--', '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                         'route-native', str(in_pcb), str(out_pcb)])),
                    ('r1_4_route_native_venv_py', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb),
                         '--native-bridge', str(bridge), '--python', str(myvenv / 'bin/python3')])),
                    ('r1_4_route_native_py_sh', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb), '--python=/bin/sh'])),
                    ('r1_4_route_native_rules_krt', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb),
                         f'--rules={co}/src/kicad_route/cli.py'])),
                    ('c1_1_sp0_file_bind', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(clean_bridge_json), str(poison_sp0 / 'bridge_json.py'),
                         '--', sys.executable, str(poison_sp0 / 'bridge_json.py')], timeout=5)),
                    ('c2_4_sp0_tmpfs_dir', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(poison_sp0), '/opt/evil',
                         '--ro-bind', str(clean_bridge_json), '/opt/evil/bridge_json.py',
                         '--', sys.executable, '/opt/evil/bridge_json.py'], timeout=5)),
                    ('c1_1_sp0_pycache_bind', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(clean_sp0), '/opt/d',
                         '--ro-bind', str(evil_pycache), '/opt/d/__pycache__',
                         '--', sys.executable, '/opt/d/bridge_json.py'])),
                    ('aud2_etc_sitecust_BI', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_sc), f'/etc/python{ver}/sitecustomize.py',
                         '--', sys.executable, '-BI', str(bridge)], timeout=5)),
                    ('aud2_dist_tmpfs_pth', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/usr/lib/python3/dist-packages',
                         '--ro-bind', str(evil_site / 'evil.pth'), '/usr/lib/python3/dist-packages/evil.pth',
                         '--', sys.executable, '-B', str(bridge)], timeout=5)),
                    ('r1_3_enc_init_BSI', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_enc / '__init__.py'), f'/usr/lib/python{ver}/encodings/__init__.py',
                         '--', sys.executable, '-BSI', str(bridge)], timeout=5)),
                    ('e3_fake_usr_ancestor', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(evil_usr), '/usr',
                         '--ro-bind', os.path.realpath(sys.executable), os.path.realpath(sys.executable),
                         '--ro-bind', str(bridge), '/opt/bridge.py',
                         '--', os.path.realpath(sys.executable), '-BI', '/opt/bridge.py'])),
                    ('e3_ld_preload', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(evil_so), '/etc/ld.so.preload', '--', '/bin/true'])),
                    ('e2_fake_etc_passwd', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(fake_passwd), '/etc/passwd', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                         '--', sys.executable, '-B', str(bridge)], timeout=5)),
                    ('r1_1_unset_home_bind_pwdir', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_site), f'{pw_dir}/.local/lib/python{ver}/site-packages',
                         '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                         '--', sys.executable, '-B', str(bridge)], timeout=5)),
                    ('e2_empty_home_slash_local', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib',
                         '--symlink', 'usr/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
                         '--ro-bind', '/etc', '/etc', '--ro-bind', str(bridge), '/opt/bridge.py',
                         '--ro-bind', str(evil_site), f'/.local/lib/python{ver}/site-packages',
                         '--setenv', 'HOME', '', '--', sys.executable, '-B', '/opt/bridge.py'])),
                    ('c1_4_nousersite_0', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(fake_home), 'PYTHONNOUSERSITE': '0'}, timeout=5)),
                    ('e2_nousersite_00', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(fake_home), 'PYTHONNOUSERSITE': '00'}, timeout=5)),
                    ('e3_safepath_0', lambda: subprocess.run(
                        [sys.executable, '-B', str(poison_sp0 / 'json.py')],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONSAFEPATH': '0'}, timeout=5)),
                    ('c1_2_argv0_bare_py3', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--argv0', 'python3', '--setenv', 'PATH', f'{myvenv / "bin"}:/usr/bin:/bin',
                         '--', sys.executable, '-BI', str(bridge)], timeout=5)),
                    ('c1_2_argv0_bare_notpy', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--argv0', 'notpython', '--setenv', 'PATH', f'{myvenv / "bin"}:/usr/bin:/bin',
                         '--', sys.executable, '-BI', str(bridge)], timeout=5)),
                    ('e2_argv0_slash_nonexistent_notpy', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--argv0', str(myvenv / 'bin/no_such_py'),
                         '--', sys.executable, '-BI', str(bridge)])),
                    ('r1_2_git_cat_file_filters', lambda: subprocess.run(
                        ['git', '-C', str(repo), 'cat-file', '--filters', 'HEAD:f.txt'], timeout=5)),
                    ('e3_git_cat_file_batch', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(repo), 'cat-file', '--batch'])),
                    ('e3_git_status_repo_filter', lambda: subprocess.run(
                        ['git', '-C', str(repo), '-c', 'core.fsmonitor=false', 'status', '--porcelain'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                        timeout=5)),
                    ('e3_git_status_submod_filter', lambda: subprocess.run(
                        ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'status', '--porcelain'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                        timeout=5)),
                    ('e3_git_status_xdg_filter', lambda: subprocess.run(
                        ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'status', '--porcelain'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'XDG_CONFIG_HOME': str(xdg_dir)},
                        timeout=5)),
                    ('e3_git_describe_dirty', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'describe', '--tags', '--dirty'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'XDG_CONFIG_HOME': str(xdg_dir)})),
                    ('e3_git_ls_files_m', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'ls-files', '-m'],
                        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'XDG_CONFIG_HOME': str(xdg_dir)})),
                    ('e3_git_status_help_man', lambda: routing_adapter.check_re01_command(
                        ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'status', '--help'])),
                ):
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                ex_in, ex_work = root / 'ex_in', root / 'ex_work'
                ex_in.mkdir()
                ex_work.mkdir()
                for sb in (
                    sandbox_argv(ex_in, ex_work, ['/bin/echo', 'bwrap-ok']),
                    sandbox_argv(ex_in, ex_work, ['/usr/bin/true'], network=True),
                ):
                    routing_adapter.check_re01_command(sb)
                    done = subprocess.run(sb, capture_output=True, timeout=5)
                    self.assertEqual(done.returncode, 0, done.stderr)
                done_clearenv = subprocess.run(
                    ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                     '--ro-bind', str(bridge), '/opt/bridge.py', '--clearenv',
                     '--setenv', 'PATH', '/usr/bin:/bin', '--', sys.executable, '-BI', '/opt/bridge.py'],
                    capture_output=True, timeout=5,
                )
                self.assertEqual(done_clearenv.returncode, 0, done_clearenv.stderr)
                for blocked_git in (
                    ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'describe', '--tags', '--always'],
                    ['git', '-C', str(clean_repo), '-c', 'core.fsmonitor=false', 'ls-files', '--error-unmatch', 'f.txt'],
                    ['git', '-C', str(clean_repo), 'cat-file', '-t', 'HEAD'],
                    ['bwrap', '--ro-bind', '/', '/', '--', 'git', '-C', str(clean_repo), 'rev-parse', 'HEAD'],
                    ['bwrap', '--argv0', 'git', '--', '/bin/true'],
                ):
                    with self.subTest(blocked_git=blocked_git):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            subprocess.run(blocked_git, capture_output=True, timeout=5)
                real_engine = routing_adapter.resolve_kicad_route_engine()
                if real_engine is not None and real_engine.is_file():
                    with self.assertRaises(routing_adapter.RE01OwnershipError):
                        subprocess.run(
                            [str(real_engine), 'route-native', str(in_pcb), str(out_pcb),
                             '--native-bridge', str(bridge), '--python', str(myvenv / 'bin/python3')],
                            timeout=5)
                    done_eng = subprocess.run([str(real_engine), 'route-native', '--help'],
                                              capture_output=True, timeout=5)
                    self.assertEqual(done_eng.returncode, 0, done_eng.stderr)

    def test_re01_r14_git_drop_pycache_bridge_sb_exe_and_kicad_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            tag = sys.implementation.cache_tag
            co = root / 'kicad-route'
            (co / 'tools').mkdir(parents=True)
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('import json, sys; sys.exit(0)\n')
            bridge.chmod(0o755)

            clean_d = root / 'clean_d'
            clean_d.mkdir()
            clean_helper_user = clean_d / 'use_helper.py'
            clean_helper_user.write_text('import helper_mod, sys; sys.exit(0)\n')
            clean_helper = clean_d / 'helper_mod.py'
            clean_helper.write_text('VAL = 1\n')
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("PYC_PWN\\n")\n')
            pc_dir = root / 'pc_nonempty'
            pc_dir.mkdir()
            py_compile.compile(str(evil_src), cfile=str(pc_dir / f'helper_mod.{tag}.pyc'), doraise=True)
            (pc_dir / 'json').mkdir()
            py_compile.compile(str(evil_src), cfile=str(pc_dir / f'json/__init__.{tag}.pyc'), doraise=True)
            empty_pc = root / 'empty_pc'
            empty_pc.mkdir()
            sym_pc = root / 'sym_pc'
            sym_pc.symlink_to(empty_pc)
            broken_pc = root / 'broken_pc'
            broken_pc.symlink_to(root / 'does_not_exist')
            file_pc = root / 'file_pc'
            file_pc.write_text('x')

            evil_pycache = root / 'evil_pycache'
            evil_pycache.mkdir()
            py_compile.compile(str(evil_src), cfile=str(evil_pycache / f'helper_mod.{tag}.pyc'), doraise=True)
            host_sym_sp0 = root / 'host_sym_sp0'
            host_sym_sp0.mkdir()
            (host_sym_sp0 / 'use_helper.py').write_text('import helper_mod\n')
            (host_sym_sp0 / 'helper_mod.py').write_text('VAL = 1\n')
            (host_sym_sp0 / '__pycache__').symlink_to(evil_pycache)
            host_sym_pyc_sp0 = root / 'host_sym_pyc_sp0'
            (host_sym_pyc_sp0 / '__pycache__').mkdir(parents=True)
            (host_sym_pyc_sp0 / 'use_helper.py').write_text('import helper_mod\n')
            (host_sym_pyc_sp0 / 'helper_mod.py').write_text('VAL = 1\n')
            (host_sym_pyc_sp0 / f'__pycache__/helper_mod.{tag}.pyc').symlink_to(
                evil_pycache / f'helper_mod.{tag}.pyc')

            ep = root / 'ep'
            (ep / 'bin').mkdir(parents=True)
            (ep / f'lib/python{ver}/encodings').mkdir(parents=True)
            (ep / f'lib/python{ver}/os.py').write_text('')
            (ep / f'lib/python{ver}/encodings/__init__.py').write_text(
                f'open({str(marker)!r}, "a").write("LANDMARK\\n")\n')
            poison_tools = root / 'poison_tools'
            poison_tools.mkdir()
            (poison_tools / 'rust_engine_kicad.py').write_text('import json\n')
            (poison_tools / 'json.py').write_text(f'open({str(marker)!r}, "a").write("BRIDGE_PWN\\n")\n')
            evil_kcli_dir = root / 'evil_kcli'
            evil_kcli_dir.mkdir()
            (evil_kcli_dir / 'kicad-cli').write_text(f'#!/bin/sh\necho KCLI >> "{marker}"\n')
            (evil_kcli_dir / 'kicad-cli').chmod(0o755)
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            real_engine = routing_adapter.resolve_kicad_route_engine()
            real_bridge = routing_adapter.resolve_native_bridge()
            py_real = os.path.realpath(sys.executable)
            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('git_direct_version', lambda: subprocess.run(['git', '--version'], timeout=5)),
                    ('git_bwrap_argv0', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--argv0', 'git', '--', '/bin/true'])),
                    ('c2_1_pycache_nonempty_direct', lambda: subprocess.run(
                        [sys.executable, '-B', str(clean_helper_user)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(pc_dir)}, timeout=5)),
                    ('c2_1_pycache_nonempty_rel_cwd', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(clean_helper_user)],
                        cwd=str(root), env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': 'pc_nonempty'})),
                    ('c2_1_pycache_symlink_host', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(sym_pc)})),
                    ('c2_1_pycache_broken_sym', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(broken_pc)})),
                    ('c2_1_pycache_reg_file', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(file_pc)})),
                    ('c2_1_pycache_root', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': '/'})),
                    ('c2_1_pycache_bwrap_mount', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--dir', '/opt/pc',
                         '--ro-bind', str(pc_dir), '/opt/pc',
                         '--setenv', 'PYTHONPYCACHEPREFIX', '/opt/pc',
                         '--', sys.executable, '-B', str(bridge)])),
                    ('c2_1_pycache_bwrap_submount', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--dir', '/opt/pc',
                         '--ro-bind', str(pc_dir), '/opt/pc/usr',
                         '--setenv', 'PYTHONPYCACHEPREFIX', '/opt/pc',
                         '--', sys.executable, '-B', str(bridge)])),
                    ('c2_1_pycache_bwrap_dev_null_bind', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(pc_dir), '/dev/null',
                         '--setenv', 'PYTHONPYCACHEPREFIX', '/dev/null',
                         '--', sys.executable, '-B', str(bridge)])),
                    ('c2_2_bwrap_sym_pycache_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--ro-bind', str(clean_helper_user), '/opt/d/use_helper.py',
                         '--ro-bind', str(clean_helper), '/opt/d/helper_mod.py',
                         '--symlink', str(evil_pycache), '/opt/d/__pycache__',
                         '--', sys.executable, '/opt/d/use_helper.py'], timeout=5)),
                    ('c2_2_bwrap_sym_pycache_pypath', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--ro-bind', str(clean_helper), '/opt/d/helper_mod.py',
                         '--symlink', str(evil_pycache), '/opt/d/__pycache__',
                         '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, str(clean_helper_user)], timeout=5)),
                    ('c2_2_bwrap_pkg_pycache', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--ro-bind', str(clean_helper_user), '/opt/d/use_helper.py',
                         '--symlink', str(evil_pycache), '/opt/d/pkg/__pycache__',
                         '--', sys.executable, '/opt/d/use_helper.py'])),
                    ('c2_2_bwrap_pyc_bind_sp0', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--ro-bind', str(clean_helper_user), '/opt/d/use_helper.py',
                         '--ro-bind', str(evil_pycache / f'helper_mod.{tag}.pyc'),
                         f'/opt/d/__pycache__/helper_mod.{tag}.pyc',
                         '--', sys.executable, '/opt/d/use_helper.py'])),
                    ('c2_2_host_sym_pycache', lambda: subprocess.run(
                        [sys.executable, str(host_sym_sp0 / 'use_helper.py')], timeout=5)),
                    ('c2_2_host_sym_pyc', lambda: subprocess.run(
                        [sys.executable, str(host_sym_pyc_sp0 / 'use_helper.py')], timeout=5)),
                    ('r2_1_bwrap_py_landmark', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', py_real, str(ep / 'bin/python3'),
                         '--', str(ep / 'bin/python3'), '-BI', str(bridge)], timeout=5)),
                    ('host_none_python_non_system', lambda: routing_adapter.check_re01_command(
                        ['/nonexistent/venv/bin/python3', str(bridge)])),
                    ('aud5_root_tools_bridge', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(poison_tools), '/tools',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('route_native_path_kicad_cli', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb),
                         '--native-bridge', str(bridge)],
                        env={'PATH': f'{evil_kcli_dir}:/usr/bin:/bin'})),
                ]
                if real_engine is not None and real_engine.is_file():
                    eng_str = str(real_engine)
                    blocked_cases.extend([
                        ('c2_1_engine_pycache', lambda: routing_adapter.check_re01_command(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb), '--native-bridge', str(bridge)],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(pc_dir)})),
                        ('c2_1_engine_implicit_pycache', lambda: routing_adapter.check_re01_command(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb)],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(pc_dir)})),
                        ('r2_1_engine_bwrap_py', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                             '--ro-bind', py_real, str(ep / 'bin/python3'),
                             '--', eng_str, 'route-native', str(in_pcb), str(out_pcb),
                             '--python', str(ep / 'bin/python3')])),
                        ('r14_engine_bwrap_untrusted_sb', lambda: routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                             '--ro-bind', eng_str, '/opt/evil/kicad-route-engine',
                             '--', '/opt/evil/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                        ('r14_engine_path_kicad_cli', lambda: routing_adapter.check_re01_command(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb)],
                            env={'PATH': f'{evil_kcli_dir}:/usr/bin:/bin'})),
                    ])
                    if real_bridge is not None and real_bridge.is_file():
                        blocked_cases.extend([
                            ('aud5_manifest_tools_bind', lambda: routing_adapter.check_re01_command(
                                ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt/kicad-route',
                                 '--dir', '/opt/kicad-route/rust-engine/target/release',
                                 '--ro-bind', eng_str, '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                                 '--ro-bind', str(poison_tools), str(real_bridge.parent),
                                 '--', '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                                 'route-native', str(in_pcb), str(out_pcb)])),
                            ('aud5_clean_shadow_bridge', lambda: routing_adapter.check_re01_command(
                                ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt/kicad-route',
                                 '--ro-bind', str(co / 'tools'), '/opt/kicad-route/tools',
                                 '--dir', '/opt/kicad-route/rust-engine/target/release',
                                 '--ro-bind', eng_str, '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                                 '--ro-bind', str(poison_tools), str(real_bridge.parent),
                                 '--', '/opt/kicad-route/rust-engine/target/release/kicad-route-engine',
                                 'route-native', str(in_pcb), str(out_pcb)])),
                        ])
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                t_layout = {'work': str(root / 'w'), 'candidate': str(root / 'c'),
                            'checkout': str(co), 'state': str(co), 'routing': str(root / 'r')}
                t_env = attempt.tool_env(t_layout)
                b_env = routing_adapter.board_route_env(co, root / 'home_br')
                for pos_env in (
                    t_env,
                    b_env,
                    {'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(root / 'nonexistent_pc')},
                    {'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(empty_pc)},
                    {'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': attempt.NO_PYCACHE},
                ):
                    routing_adapter.check_re01_command([sys.executable, '-B', str(bridge)], env=pos_env)
                    done_pc = subprocess.run([sys.executable, '-B', str(bridge)], env=pos_env,
                                             capture_output=True, timeout=5)
                    self.assertEqual(done_pc.returncode, 0, done_pc.stderr)
                routing_adapter.check_re01_command(
                    ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(bridge), '/opt/bridge.py',
                     '--setenv', 'PYTHONPYCACHEPREFIX', attempt.NO_PYCACHE,
                     '--', sys.executable, '-B', '/opt/bridge.py'])
                routing_adapter.check_re01_command(
                    ['bwrap', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib',
                     '--symlink', 'usr/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
                     '--ro-bind', '/etc', '/etc', '--dev', '/dev',
                     '--ro-bind', str(bridge), '/opt/bridge.py',
                     '--setenv', 'PYTHONPYCACHEPREFIX', attempt.NO_PYCACHE,
                     '--', sys.executable, '-B', '/opt/bridge.py'])
                if real_engine is not None and real_engine.is_file():
                    routing_adapter.check_re01_command(
                        [str(real_engine), 'route-native', str(in_pcb), str(out_pcb), '--native-bridge', str(bridge)],
                        env=t_env)
                    routing_adapter.check_re01_command(
                        [str(real_engine), 'route-native', str(in_pcb), str(out_pcb), '--native-bridge', str(bridge)],
                        env=b_env)

    def test_re01_r15_sym_cwd_backslash_paths_and_shadow_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            tag = sys.implementation.cache_tag
            co = root / 'kicad-route'
            (co / 'tools').mkdir(parents=True)
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('import json, sys; sys.exit(0)\n')
            bridge.chmod(0o755)
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("R15_PWN\\n")\n')

            # M51-R14-C1-1: host symlink cwd and bwrap --symlink --chdir
            evil_cwd_root = root / 'evil_cwd_root'
            (evil_cwd_root / 'sub').mkdir(parents=True)
            (evil_cwd_root / 'tools').mkdir(parents=True)
            (evil_cwd_root / 'tools/rust_engine_kicad.py').write_text('import json\n')
            (evil_cwd_root / 'tools/json.py').write_text(f'open({str(marker)!r}, "a").write("CWD_JSON\\n")\n')
            host_sym_cwd = root / 'host_sym_cwd'
            host_sym_cwd.symlink_to(evil_cwd_root / 'sub')

            # M51-R14-C2-1: literal backslash in Linux directory names
            bs_pc = root / 'bs\\pc'
            (bs_pc / 'json').mkdir(parents=True)
            py_compile.compile(str(evil_src), cfile=str(bs_pc / f'json/__init__.{tag}.pyc'), doraise=True)
            bs_pp = root / 'bs\\pp'
            bs_pp.mkdir()
            (bs_pp / 'json.py').write_text(f'open({str(marker)!r}, "a").write("BS_PP\\n")\n')
            bs_home = root / 'bs\\home'
            (bs_home / f'.local/lib/python{ver}/site-packages').mkdir(parents=True)
            (bs_home / f'.local/lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("BS_HOME\\n")\n')
            bs_path = root / 'bs\\path'
            bs_path.mkdir()
            (bs_path / 'kicad-cli').write_text(f'#!/bin/sh\necho BS_KCLI >> "{marker}"\n')
            (bs_path / 'kicad-cli').chmod(0o755)

            # M51-R14-C2-2: bwrap-mounted/overlaid dirs with forged non-hook .pyc
            clean_src_dir = root / 'clean_src'
            clean_src_dir.mkdir()
            clean_imp = clean_src_dir / 'bridge_helper.py'
            clean_imp.write_text('import helper_mod, sys; sys.exit(0)\n')
            clean_helper = clean_src_dir / 'helper_mod.py'
            clean_helper.write_text('VAL = 1\n')
            clean_pkg_imp = clean_src_dir / 'pkg_helper.py'
            clean_pkg_imp.write_text('import pkg.helper_mod, sys; sys.exit(0)\n')
            shadow_dir = root / 'shadow_dir'
            (shadow_dir / '__pycache__').mkdir(parents=True)
            (shadow_dir / 'pkg/__pycache__').mkdir(parents=True)
            (shadow_dir / 'pkg/__init__.py').write_text('')
            py_compile.compile(str(evil_src), cfile=str(shadow_dir / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            py_compile.compile(
                str(evil_src), cfile=str(shadow_dir / f'pkg/__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            evil_pkg = root / 'evil_pkg'
            (evil_pkg / '__pycache__').mkdir(parents=True)
            (evil_pkg / '__init__.py').write_text('')
            py_compile.compile(str(evil_src), cfile=str(evil_pkg / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            host_overlaid = root / 'host_overlaid'
            (host_overlaid / '__pycache__').mkdir(parents=True)
            (host_overlaid / 'bridge_helper.py').write_text('import helper_mod, sys; sys.exit(0)\n')
            (host_overlaid / 'helper_mod.py').write_text('VAL = 1\n')
            py_compile.compile(
                str(evil_src), cfile=str(host_overlaid / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)

            real_engine = routing_adapter.resolve_kicad_route_engine()
            real_bridge = routing_adapter.resolve_native_bridge()
            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('c2_1_bs_pycache', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': str(bs_pc)}, timeout=5)),
                    ('c2_1_bs_pypath', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(bs_pp)}, timeout=5)),
                    ('c2_1_bs_bwrap_robind_pypath', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(bs_pp), '/opt/d', '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, '-B', str(bridge)], timeout=5)),
                    ('c2_1_bs_home_usersite', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(bs_home)}, timeout=5)),
                    ('c2_1_bs_path_kicad_cli', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb),
                         '--native-bridge', str(bridge)],
                        env={'PATH': f'{bs_path}:/usr/bin:/bin'})),
                    ('c2_2_shadow_sp0_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(shadow_dir), '/opt/d',
                         '--ro-bind', str(clean_imp), '/opt/d/bridge_helper.py',
                         '--ro-bind', str(clean_helper), '/opt/d/helper_mod.py',
                         '--', sys.executable, '/opt/d/bridge_helper.py'], timeout=5)),
                    ('c2_2_shadow_pypath_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(shadow_dir), '/opt/d',
                         '--ro-bind', str(clean_helper), '/opt/d/helper_mod.py',
                         '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, str(clean_imp)], timeout=5)),
                    ('c2_2_shadow_usersite_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(shadow_dir), f'/opt/h/.local/lib/python{ver}/site-packages',
                         '--ro-bind', str(clean_helper), f'/opt/h/.local/lib/python{ver}/site-packages/helper_mod.py',
                         '--setenv', 'HOME', '/opt/h',
                         '--', sys.executable, str(clean_imp)], timeout=5)),
                    ('c2_2_host_child_overlay_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(clean_helper), str(host_overlaid / 'helper_mod.py'),
                         '--', sys.executable, str(host_overlaid / 'bridge_helper.py')], timeout=5)),
                    ('c2_2_shadow_subpkg_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(shadow_dir), '/opt/d',
                         '--ro-bind', str(clean_pkg_imp), '/opt/d/pkg_helper.py',
                         '--ro-bind', str(clean_helper), '/opt/d/pkg/helper_mod.py',
                         '--', sys.executable, '/opt/d/pkg_helper.py'], timeout=5)),
                    ('c2_2_mounted_subpkg_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(clean_src_dir), '/opt/d',
                         '--ro-bind', str(evil_pkg), '/opt/d/pkg',
                         '--ro-bind', str(clean_helper), '/opt/d/pkg/helper_mod.py',
                         '--', sys.executable, '/opt/d/pkg_helper.py'], timeout=5)),
                    ('c1_1_host_sym_cwd_bridge', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)],
                        cwd=str(host_sym_cwd))),
                    ('c1_1_bwrap_host_sym_chdir', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--chdir', str(host_sym_cwd),
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('c1_1_bwrap_sym_chdir', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--symlink', str(evil_cwd_root / 'sub'), '/opt/sym_cwd', '--chdir', '/opt/sym_cwd',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                ]
                if real_engine is not None and real_engine.is_file():
                    eng_str = str(real_engine)
                    blocked_cases.extend([
                        ('c2_1_real_engine_bs_path_kcli', lambda: routing_adapter.check_re01_command(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb)],
                            env={'PATH': f'{bs_path}:/usr/bin:/bin'})),
                        ('c1_1_real_engine_host_sym_cwd', lambda: subprocess.run(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb)],
                            cwd=str(host_sym_cwd), timeout=5)),
                    ])
                    if real_bridge is not None and real_bridge.is_file():
                        blocked_cases.extend([
                            ('c1_1_real_engine_bwrap_host_sym_chdir', lambda: subprocess.run(
                                ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                                 '--tmpfs', str(real_bridge.parent), '--chdir', str(host_sym_cwd),
                                 '--', eng_str, 'route-native', str(in_pcb), str(out_pcb)], timeout=5)),
                            ('c1_1_real_engine_bwrap_sym_chdir', lambda: subprocess.run(
                                ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                                 '--tmpfs', str(real_bridge.parent), '--tmpfs', '/opt',
                                 '--symlink', str(evil_cwd_root / 'sub'), '/opt/sym_cwd',
                                 '--chdir', '/opt/sym_cwd',
                                 '--', eng_str, 'route-native', str(in_pcb), str(out_pcb)], timeout=5)),
                        ])
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                # Positive controls: repo PYTHONPATH=src:tests, sandbox_argv, and repo tools under bwrap
                routing_adapter.check_re01_command(
                    [sys.executable, str(bridge)], cwd=str(ROOT),
                    env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': 'src:tests'})
                ex_in, ex_work = root / 'ex_in', root / 'ex_work'
                ex_in.mkdir()
                ex_work.mkdir()
                routing_adapter.check_re01_command(
                    sandbox_argv(ex_in, ex_work, [sys.executable, '-I', '-B', '/app/agent_ee/__init__.py']))
                if real_engine is not None and real_engine.is_file():
                    routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/',
                         '--', str(real_engine), 'route-native', '--help'])
                    if real_bridge is not None and real_bridge.is_file():
                        routing_adapter.check_re01_command(
                            ['bwrap', '--ro-bind', '/', '/',
                             '--', str(real_engine), 'route-native', str(in_pcb), str(out_pcb)])

    def test_re01_r16_sym_dotdot_deep_bwrap_and_cross_mount_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            tag = sys.implementation.cache_tag
            clean_d = root / 'clean'
            clean_d.mkdir()
            bridge = clean_d / 'bridge.py'
            bridge.write_text('import json, sys; sys.exit(0)\n')
            clean_helper = clean_d / 'helper_mod.py'
            clean_helper.write_text('VAL = 1\n')
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("R16_PWN\\n")\n')
            evil_pc = root / 'evil_pycache/__pycache__'
            evil_pc.mkdir(parents=True)
            py_compile.compile(str(evil_src), cfile=str(evil_pc / f'x.{tag}.pyc'), doraise=True)

            # Cluster A: <sym>/.. cwd, --chdir, --symlink, HOME, PYTHONPYCACHEPREFIX, sys.path[0]
            evil_cwd = root / 'evil_cwd_root'
            (evil_cwd / 'sub/deep').mkdir(parents=True)
            (evil_cwd / 'tools').mkdir(parents=True)
            (evil_cwd / 'tools/rust_engine_kicad.py').write_text('import json\n')
            (evil_cwd / 'tools/json.py').write_text(f'open({str(marker)!r}, "a").write("TOOLS_JSON\\n")\n')
            (evil_cwd / 'sub/json.py').write_text(f'open({str(marker)!r}, "a").write("SUB_JSON\\n")\n')
            (evil_cwd / 'sub/bridge_rel.py').write_text('import json\n')
            (evil_cwd / 'sub/rel_pc/json').mkdir(parents=True)
            py_compile.compile(str(evil_src), cfile=str(evil_cwd / f'sub/rel_pc/json/__init__.{tag}.pyc'), doraise=True)
            (evil_cwd / 'sub/kicad-cli').write_text(f'#!/bin/sh\necho KCLI >> "{marker}"\n')
            (evil_cwd / 'sub/kicad-cli').chmod(0o755)
            (evil_cwd / f'sub/.local/lib/python{ver}/site-packages').mkdir(parents=True)
            (evil_cwd / f'sub/.local/lib/python{ver}/site-packages/evil.pth').write_text(
                f'import builtins; open({str(marker)!r}, "a").write("HOME_PTH\\n")\n')
            sym_deep = root / 'host_sym_deep'
            sym_deep.symlink_to(evil_cwd / 'sub/deep')
            (evil_cwd / 'sub/sym_pc').symlink_to(root / 'does_not_exist')
            (evil_cwd / 'sub/caller_link.py').symlink_to(bridge)

            # Cluster B: depth-9 subpackage + cross-mount symlinks + host_sym_sp + cycle
            d9 = root / 'shadow_d9'
            d9.mkdir()
            (d9 / 'caller_d9.py').write_text('import p1.p2.p3.p4.p5.p6.p7.p8.p9.helper_mod\n')
            cur = d9
            for i in range(1, 10):
                cur = cur / f'p{i}'
                cur.mkdir()
                (cur / '__init__.py').write_text('')
            (cur / '__pycache__').mkdir()
            (cur / 'helper_mod.py').write_text('VAL = 1\n')
            py_compile.compile(str(evil_src), cfile=str(cur / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)

            evil_pkg = root / 'evil_pkg'
            (evil_pkg / '__pycache__').mkdir(parents=True)
            (evil_pkg / '__init__.py').write_text('')
            py_compile.compile(str(evil_src), cfile=str(evil_pkg / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            rel_sym_root = root / 'rel_sym_root'
            rel_sym_root.mkdir()
            (rel_sym_root / 'pkg_helper.py').write_text('import pkg.helper_mod\n')
            (rel_sym_root / 'pkg').symlink_to('../other_pkg')
            abs_sym_root = root / 'abs_sym_root'
            abs_sym_root.mkdir()
            (abs_sym_root / 'pkg_helper.py').write_text('import pkg.helper_mod\n')
            (abs_sym_root / 'pkg').symlink_to('/opt/evil_pkg')
            real_p1 = root / 'real_p1'
            (real_p1 / '__pycache__').mkdir(parents=True)
            py_compile.compile(str(evil_src), cfile=str(real_p1 / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            host_sym_sp = root / 'host_sym_sp'
            host_sym_sp.mkdir()
            (host_sym_sp / 'caller.py').write_text('import p1.helper_mod\n')
            (host_sym_sp / 'p1').symlink_to(real_p1)
            (host_sym_sp / 'a/b').mkdir(parents=True)
            (host_sym_sp / 'a/b/p1').symlink_to(real_p1)
            (host_sym_sp / 'caller_ab.py').write_text('import a.b.p1.helper_mod\n')
            cyc_a, cyc_b = root / 'cyc_a', root / 'cyc_b'
            cyc_a.mkdir()
            cyc_b.mkdir()
            (cyc_a / 'go.py').write_text('import sys; sys.exit(0)\n')
            (cyc_a / 'b').symlink_to('/opt/b')
            (cyc_b / 'a').symlink_to('/opt/a')
            real_engine = routing_adapter.resolve_kicad_route_engine()
            real_bridge = routing_adapter.resolve_native_bridge()
            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('aud1_bwrap_host_sym_dotdot', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--chdir', f'{sym_deep}/..',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('aud1_bwrap_sb_sym_dotdot', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--symlink', str(evil_cwd / 'sub/deep'), '/opt/sym_deep',
                         '--chdir', '/opt/sym_deep/..',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('aud1_cwd_sym_dotdot_pp_dot', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)], cwd=f'{sym_deep}/..',
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': '.'}, timeout=5)),
                    ('c2_1_cwd_sym_dotdot_rel_script', lambda: subprocess.run(
                        [sys.executable, '-B', 'bridge_rel.py'], cwd=f'{sym_deep}/..',
                        env={'PATH': '/usr/bin:/bin'}, timeout=5)),
                    ('c2_1_cwd_sym_dotdot_rel_pc', lambda: subprocess.run(
                        [sys.executable, str(bridge)], cwd=f'{sym_deep}/..',
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': 'rel_pc'}, timeout=5)),
                    ('c2_1_cwd_sym_dotdot_rel_kcli', lambda: routing_adapter.check_re01_command(
                        ['/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb),
                         '--native-bridge', str(bridge)],
                        cwd=f'{sym_deep}/..', env={'PATH': '.:/usr/bin:/bin'})),
                    ('gap_a1_home_sym_dotdot', lambda: subprocess.run(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': f'{sym_deep}/..'}, timeout=5)),
                    ('gap_pc_sym_dotdot_sym_pc', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': f'{sym_deep}/../sym_pc'})),
                    ('gap_a2_bwrap_chained_sym_dotdot', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--symlink', str(evil_cwd / 'sub/deep'), '/opt/s1',
                         '--symlink', '/opt/s1/..', '/opt/s2', '--chdir', '/opt/s2',
                         '--', '/usr/bin/kicad-route-engine', 'route-native', str(in_pcb), str(out_pcb)])),
                    ('gap_a3_script_sym_dotdot_host', lambda: subprocess.run(
                        [sys.executable, '-B', f'{sym_deep}/../bridge_rel.py'],
                        env={'PATH': '/usr/bin:/bin'}, timeout=5)),
                    ('gap_a3_script_sym_dotdot_bwrap_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--', sys.executable, '-B', f'{sym_deep}/../caller_link.py'],
                        env={'PATH': '/usr/bin:/bin'}, timeout=5)),
                    ('gap_bwrap_robind_sym_dotdot', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', f'{sym_deep}/..', '/opt/d', '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, '-B', str(bridge)], timeout=5)),
                    ('gap_rel_cwd_sym_dotdot', lambda: routing_adapter.check_re01_command(
                        [sys.executable, '-B', str(bridge)],
                        cwd=os.path.join(os.path.relpath(sym_deep, os.getcwd()), '..'),
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': '.'})),
                ]
                blocked_cases.extend([
                    ('r1_1_depth9_with_child_mount', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(d9), '/opt/d',
                         '--ro-bind', str(clean_helper), '/opt/d/p1/p2/p3/p4/p5/p6/p7/p8/p9/helper_mod.py',
                         '--', sys.executable, '/opt/d/caller_d9.py'], timeout=5)),
                    ('r1_1_depth9_no_child_mount', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(d9), '/opt/d', '--', sys.executable, '/opt/d/caller_d9.py'], timeout=5)),
                    ('r1_2_rel_cross_mount_sym', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(rel_sym_root), '/opt/d',
                         '--ro-bind', str(evil_pkg), '/opt/other_pkg',
                         '--ro-bind', str(clean_helper), '/opt/other_pkg/helper_mod.py',
                         '--', sys.executable, '/opt/d/pkg_helper.py'], timeout=5)),
                    ('r2_1_abs_cross_mount_sym_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(abs_sym_root), '/opt/d',
                         '--ro-bind', str(evil_pkg), '/opt/evil_pkg',
                         '--ro-bind', str(clean_helper), '/opt/evil_pkg/helper_mod.py',
                         '--', sys.executable, '/opt/d/pkg_helper.py'], timeout=5)),
                    ('r2_1_abs_cross_mount_sym_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(abs_sym_root), '/opt/d',
                         '--ro-bind', str(evil_pkg), '/opt/evil_pkg',
                         '--ro-bind', str(clean_helper), '/opt/evil_pkg/helper_mod.py',
                         '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, '/opt/d/pkg_helper.py'], timeout=5)),
                    ('r2_1_abs_cross_mount_sym_usersite', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root), '--tmpfs', '/opt',
                         '--ro-bind', str(abs_sym_root), f'/opt/h/.local/lib/python{ver}/site-packages',
                         '--ro-bind', str(evil_pkg), '/opt/evil_pkg',
                         '--ro-bind', str(clean_helper), '/opt/evil_pkg/helper_mod.py',
                         '--setenv', 'HOME', '/opt/h', '--', sys.executable, str(bridge)], timeout=5)),
                    ('r2_1_opaque_tmpfs_symlink_child', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--tmpfs', '/opt/other_pkg', '--ro-bind', str(evil_pkg), '/opt/other_pkg/sub',
                         '--symlink', '/opt/other_pkg', '/opt/d/pkg',
                         '--ro-bind', str(clean_helper), '/opt/d/use_helper.py',
                         '--', sys.executable, '/opt/d/use_helper.py'])),
                    ('c1_2_host_sym_sp_file_mount', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(clean_helper), str(real_p1 / 'helper_mod.py'),
                         '--', sys.executable, str(host_sym_sp / 'caller.py')], timeout=5)),
                    ('c1_2_host_sym_sp_dir_mount', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_pkg), str(real_p1),
                         '--', sys.executable, str(host_sym_sp / 'caller.py')], timeout=5)),
                    ('c1_2_host_sym_sp_multilevel_dir', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(evil_pkg), str(real_p1),
                         '--', sys.executable, str(host_sym_sp / 'caller_ab.py')], timeout=5)),
                    ('rev2_minor_mount_through_sym', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--ro-bind', str(clean_helper), str(host_sym_sp / 'p1/helper_mod.py'),
                         '--', sys.executable, str(host_sym_sp / 'caller.py')], timeout=5)),
                ])
                if real_engine is not None and real_engine.is_file() and real_bridge is not None and real_bridge.is_file():
                    eng_str = str(real_engine)
                    blocked_cases.extend([
                        ('aud1_real_engine_bwrap_host_sym_dotdot', lambda: subprocess.run(
                            ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                             '--tmpfs', str(real_bridge.parent), '--chdir', f'{sym_deep}/..',
                             '--', eng_str, 'route-native', str(in_pcb), str(out_pcb)], timeout=5)),
                        ('aud1_real_engine_bwrap_sb_sym_dotdot', lambda: subprocess.run(
                            ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                             '--tmpfs', str(real_bridge.parent), '--tmpfs', '/opt',
                             '--symlink', str(evil_cwd / 'sub/deep'), '/opt/sym_deep',
                             '--chdir', '/opt/sym_deep/..',
                             '--', eng_str, 'route-native', str(in_pcb), str(out_pcb)], timeout=5)),
                        ('c2_1_real_engine_cwd_sym_dotdot_kcli', lambda: subprocess.run(
                            [eng_str, 'route-native', str(in_pcb), str(out_pcb)],
                            cwd=f'{sym_deep}/..', env={'PATH': '.:/usr/bin:/bin'}, timeout=5)),
                    ])
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                # Positive controls: --bind root root with unrelated evil_pycache, symlink cycle, real bridge/co
                for pos_argv in (
                    ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                     '--', sys.executable, str(bridge)],
                    ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                     '--ro-bind', str(cyc_a), '/opt/a', '--ro-bind', str(cyc_b), '/opt/b',
                     '--', sys.executable, '/opt/a/go.py'],
                    *([['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                        '--ro-bind', str(real_bridge), '/opt/bridge.py',
                        '--', sys.executable, '-B', '/opt/bridge.py', '--help'],
                       ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                        '--ro-bind', str(real_bridge.parent.parent), '/opt/kicad-route',
                        '--', sys.executable, '-B', str(bridge)]]
                      if real_bridge is not None and real_bridge.is_file() else []),
                ):
                    done_pos = subprocess.run(pos_argv, capture_output=True, text=True, timeout=5)
                    self.assertEqual(done_pos.returncode, 0, done_pos.stderr)

    def test_re01_r16_c1_symlink_py_and_depth1_symlink_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            tag = sys.implementation.cache_tag
            other = root / 'other'
            other.mkdir()
            clean_helper = other / 'clean_helper.py'
            clean_helper.write_text('VAL = 1\n')
            evil_big = root / 'evil_big.py'
            evil_big.write_text('VAL = 99  # padded\n')
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("BYPASS_PYC\\n")\nVAL = 99\n')
            evil_pc = root / 'evil_pc_dir'
            evil_pc.mkdir()
            py_compile.compile(str(evil_src), cfile=str(evil_pc / f'helper_mod.{tag}.pyc'), doraise=True)
            os.utime(evil_big, (os.stat(evil_big).st_atime, os.stat(evil_src).st_mtime))
            real_evil_pc = root / 'real_evil_pc'
            real_evil_pc.mkdir()
            clean_caller = root / 'clean_caller.py'
            clean_caller.write_text('import helper_mod\n')

            # M51-R16-C1-1: symlinked .py -> bwrap-mounted .py + forged cur_dir/__pycache__
            sp0 = root / 'sp0'
            (sp0 / '__pycache__').mkdir(parents=True)
            (sp0 / 'caller.py').write_text('import helper_mod\n')
            (sp0 / 'helper_mod.py').symlink_to(clean_helper)
            py_compile.compile(str(evil_src), cfile=str(sp0 / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            sp1 = root / 'sp1'
            (sp1 / 'p1/__pycache__').mkdir(parents=True)
            (sp1 / 'caller.py').write_text('import p1.helper_mod\n')
            (sp1 / 'p1/__init__.py').write_text('')
            (sp1 / 'p1/helper_mod.py').symlink_to(clean_helper)
            py_compile.compile(str(evil_src), cfile=str(sp1 / f'p1/__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            sp1_opt = root / 'sp1_opt'
            (sp1_opt / 'p1/__pycache__').mkdir(parents=True)
            (sp1_opt / 'caller.py').write_text('import p1.helper_mod\n')
            (sp1_opt / 'p1/__init__.py').write_text('')
            (sp1_opt / 'p1/helper_mod.py').symlink_to('/opt/clean_helper.py')
            py_compile.compile(str(evil_src), cfile=str(sp1_opt / f'p1/__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            sp_pp = root / 'sp_pp'
            (sp_pp / '__pycache__').mkdir(parents=True)
            (sp_pp / 'helper_mod.py').symlink_to(clean_helper)
            py_compile.compile(str(evil_src), cfile=str(sp_pp / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            sp_bsym = root / 'sp_bsym'
            (sp_bsym / 'p1/__pycache__').mkdir(parents=True)
            (sp_bsym / 'caller.py').write_text('import p1.helper_mod\n')
            (sp_bsym / 'p1/__init__.py').write_text('')
            py_compile.compile(str(evil_src), cfile=str(sp_bsym / f'p1/__pycache__/helper_mod.{tag}.pyc'), doraise=True)

            # M51-R16-C1-2: depth >= 1 symlinked __pycache__ (sys.path[0], user_site, bwrap --symlink)
            c2_opt = root / 'c2_opt'
            (c2_opt / 'p1').mkdir(parents=True)
            (c2_opt / 'caller.py').write_text('import p1.helper_mod\n')
            (c2_opt / 'p1/__init__.py').write_text('')
            (c2_opt / 'p1/helper_mod.py').write_text(evil_big.read_text())
            (c2_opt / 'p1/__pycache__').symlink_to('/opt/evil_pc')
            c2_host = root / 'c2_host'
            (c2_host / 'p1').mkdir(parents=True)
            (c2_host / 'caller.py').write_text('import p1.helper_mod\n')
            (c2_host / 'p1/__init__.py').write_text('')
            (c2_host / 'p1/helper_mod.py').write_text(evil_big.read_text())
            (c2_host / 'p1/__pycache__').symlink_to(real_evil_pc)
            us_home = root / 'us_home'
            us_site = us_home / f'.local/lib/python{ver}/site-packages'
            (us_site / 'p1').mkdir(parents=True)
            (us_site / 'p1/__init__.py').write_text('')
            (us_site / 'p1/helper_mod.py').write_text(evil_big.read_text())
            (us_site / 'p1/__pycache__').symlink_to('/opt/evil_pc')
            us_caller = root / 'us_caller.py'
            us_caller.write_text('import p1.helper_mod\n')
            c2_bsym = root / 'c2_bsym'
            (c2_bsym / 'p1').mkdir(parents=True)
            (c2_bsym / 'caller.py').write_text('import p1.helper_mod\n')
            (c2_bsym / 'p1/__init__.py').write_text('')
            (c2_bsym / 'p1/helper_mod.py').write_text(evil_big.read_text())
            pos_sym = root / 'pos_sym'
            pos_sym.mkdir()
            (pos_sym / 'caller.py').write_text('import helper_mod, sys; assert helper_mod.VAL == 99; sys.exit(0)\n')
            (pos_sym / 'helper_mod.py').symlink_to('/opt/clean_helper.py')

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('c1_1_d0_sym_py_to_bound_file', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_big), str(clean_helper),
                         '--', sys.executable, str(sp0 / 'caller.py')], timeout=5)),
                    ('c1_1_d1_sym_py_to_bound_file', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_big), str(clean_helper),
                         '--', sys.executable, str(sp1 / 'caller.py')], timeout=5)),
                    ('c1_1_d1_sym_py_to_opt_bound_file', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_big), '/opt/clean_helper.py',
                         '--', sys.executable, str(sp1_opt / 'caller.py')], timeout=5)),
                    ('c1_1_pp_d0_sym_py_to_bound_file', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_big), str(clean_helper),
                         '--setenv', 'PYTHONPATH', str(sp_pp),
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('c1_1_bwrap_symlink_d1_py', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_big), '/opt/clean_helper.py',
                         '--symlink', '/opt/clean_helper.py', str(sp_bsym / 'p1/helper_mod.py'),
                         '--', sys.executable, str(sp_bsym / 'caller.py')], timeout=5)),
                    ('c1_2_d1_sym_pycache_opt', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_pc), '/opt/evil_pc',
                         '--', sys.executable, str(c2_opt / 'caller.py')], timeout=5)),
                    ('c1_2_d1_sym_pycache_host', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_pc), str(real_evil_pc),
                         '--', sys.executable, str(c2_host / 'caller.py')], timeout=5)),
                    ('c1_2_usersite_d1_sym_pycache_opt', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_pc), '/opt/evil_pc',
                         '--setenv', 'HOME', str(us_home),
                         '--', sys.executable, str(us_caller)], timeout=5)),
                    ('c1_2_bwrap_symlink_d1_pycache', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_pc), '/opt/evil_pc',
                         '--symlink', '/opt/evil_pc', str(c2_bsym / 'p1/__pycache__'),
                         '--', sys.executable, str(c2_bsym / 'caller.py')], timeout=5)),
                    ('c1_2_bwrap_symlink_opaque_d1_pycache', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', '/opt/d',
                         '--ro-bind', str(evil_pc), '/opt/evil_pc',
                         '--ro-bind', str(c2_bsym / 'caller.py'), '/opt/d/caller.py',
                         '--symlink', '/opt/evil_pc', '/opt/d/p1/__pycache__',
                         '--', sys.executable, '/opt/d/caller.py'])),
                ]
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                done_pos = subprocess.run(
                    ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                     '--ro-bind', str(evil_big), '/opt/clean_helper.py',
                     '--', sys.executable, str(pos_sym / 'caller.py')],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(done_pos.returncode, 0, done_pos.stderr)

    def test_re01_r17_sourceless_pyc_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("BYPASS_PYC\\n")\nVAL = 99\n')
            evil_pyc = root / 'evil.pyc'
            py_compile.compile(str(evil_src), cfile=str(evil_pyc), doraise=True)

            dir_pyc = root / 'dir_pyc'
            dir_pyc.mkdir()
            (dir_pyc / 'caller.py').write_text('import helper_mod\n')
            shutil.copy2(evil_pyc, dir_pyc / 'helper_mod.pyc')
            dir_pyc_sub = root / 'dir_pyc_sub'
            (dir_pyc_sub / 'p1').mkdir(parents=True)
            (dir_pyc_sub / 'p1/__init__.py').write_text('')
            shutil.copy2(evil_pyc, dir_pyc_sub / 'p1/helper_mod.pyc')

            sp0 = root / 'sp0'
            sp0.mkdir()
            (sp0 / 'caller.py').write_text('import helper_mod\n')
            (sp0 / 'helper_mod.pyc').write_text('placeholder\n')
            sp1 = root / 'sp1'
            (sp1 / 'p1').mkdir(parents=True)
            (sp1 / 'caller.py').write_text('import p1.helper_mod\n')
            (sp1 / 'p1/__init__.py').write_text('')
            (sp1 / 'p1/helper_mod.pyc').write_text('placeholder\n')

            pp = root / 'pp'
            pp.mkdir()
            (pp / 'helper_mod.pyc').write_text('placeholder\n')
            clean_caller = root / 'clean_caller.py'
            clean_caller.write_text('import helper_mod\n')
            clean_sub_caller = root / 'clean_sub_caller.py'
            clean_sub_caller.write_text('import sub.p1.helper_mod\n')

            us_home = root / 'us_home'
            us_site = us_home / f'.local/lib/python{ver}/site-packages'
            (us_site / 'p1').mkdir(parents=True)
            (us_site / 'p1/__init__.py').write_text('')
            (us_site / 'p1/helper_mod.pyc').write_text('placeholder\n')
            us_caller = root / 'us_caller.py'
            us_caller.write_text('import p1.helper_mod\n')

            hs0 = root / 'hs0'
            hs0.mkdir()
            (hs0 / 'caller.py').write_text('import helper_mod\n')
            (hs0 / 'helper_mod.pyc').symlink_to('/opt/evil.pyc')
            hs1 = root / 'hs1'
            (hs1 / 'p1').mkdir(parents=True)
            (hs1 / 'caller.py').write_text('import p1.helper_mod\n')
            (hs1 / 'p1/__init__.py').write_text('')
            (hs1 / 'p1/helper_mod.pyc').symlink_to('/opt/evil.pyc')

            host_sp0 = root / 'host_sp0'
            host_sp0.mkdir()
            (host_sp0 / 'caller.py').write_text('import helper_mod\n')
            shutil.copy2(evil_pyc, host_sp0 / 'helper_mod.pyc')

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('robind_dir_pyc_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(dir_pyc), '/opt/d',
                         '--', sys.executable, '/opt/d/caller.py'], timeout=5)),
                    ('robind_dir_pyc_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(dir_pyc), '/opt/d',
                         '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('robind_dir_pyc_opt_subdir', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(dir_pyc_sub), '/opt/sub',
                         '--setenv', 'PYTHONPATH', '/opt',
                         '--', sys.executable, str(clean_sub_caller)], timeout=5)),
                    ('robind_placeholder_pyc_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_pyc), str(sp0 / 'helper_mod.pyc'),
                         '--', sys.executable, str(sp0 / 'caller.py')], timeout=5)),
                    ('robind_placeholder_pyc_sp1', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_pyc), str(sp1 / 'p1/helper_mod.pyc'),
                         '--', sys.executable, str(sp1 / 'caller.py')], timeout=5)),
                    ('robind_placeholder_pyc_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_pyc), str(pp / 'helper_mod.pyc'),
                         '--setenv', 'PYTHONPATH', str(pp),
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('robind_placeholder_pyc_usersite_d1', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--ro-bind', str(evil_pyc), str(us_site / 'p1/helper_mod.pyc'),
                         '--setenv', 'HOME', str(us_home),
                         '--', sys.executable, str(us_caller)], timeout=5)),
                    ('tmpfs_robind_pyc_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--dir', '/opt/d',
                         '--ro-bind', str(clean_caller), '/opt/d/caller.py',
                         '--ro-bind', str(evil_pyc), '/opt/d/helper_mod.pyc',
                         '--', sys.executable, '/opt/d/caller.py'], timeout=5)),
                    ('tmpfs_robind_pyc_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--dir', '/opt/d',
                         '--ro-bind', str(evil_pyc), '/opt/d/helper_mod.pyc',
                         '--setenv', 'PYTHONPATH', '/opt/d',
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('tmpfs_symlink_pyc_sp0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--dir', '/opt/d',
                         '--ro-bind', str(clean_caller), '/opt/d/caller.py',
                         '--ro-bind', str(evil_pyc), '/opt/e.pyc',
                         '--symlink', '/opt/e.pyc', '/opt/d/helper_mod.pyc',
                         '--', sys.executable, '/opt/d/caller.py'], timeout=5)),
                    ('host_sym_pyc_d0', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_pyc), '/opt/evil.pyc',
                         '--', sys.executable, str(hs0 / 'caller.py')], timeout=5)),
                    ('host_sym_pyc_d1', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(marker), str(marker),
                         '--tmpfs', '/opt', '--ro-bind', str(evil_pyc), '/opt/evil.pyc',
                         '--', sys.executable, str(hs1 / 'caller.py')], timeout=5)),
                    ('host_sourceless_pyc_sp0', lambda: subprocess.run(
                        [sys.executable, str(host_sp0 / 'caller.py')], timeout=5)),
                    ('direct_pyc_arg', lambda: subprocess.run(
                        [sys.executable, str(evil_pyc)], timeout=5)),
                ]
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())

    def test_re01_r17_zip_bytecode_and_positive_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("ZIP_PYC_PWN\\n")\nVAL = 99\n')
            evil_pyc = root / 'evil.pyc'
            py_compile.compile(str(evil_src), cfile=str(evil_pyc), doraise=True)

            zip_top = root / 'evil_top.zip'
            with zipfile.ZipFile(zip_top, 'w') as zf:
                zf.write(evil_pyc, 'helper_mod.pyc')
            zip_nested = root / 'evil_nested.zip'
            with zipfile.ZipFile(zip_nested, 'w') as zf:
                zf.writestr('p1/__init__.py', '')
                zf.write(evil_pyc, 'p1/helper_mod.pyc')
            zip_pyo = root / 'evil_pyo.zip'
            with zipfile.ZipFile(zip_pyo, 'w') as zf:
                zf.write(evil_pyc, 'sub/helper_mod.pyo')
            zip_clean = root / 'clean.zip'
            with zipfile.ZipFile(zip_clean, 'w') as zf:
                zf.writestr('clean_zip_mod.py', 'VAL = 7\n')

            caller = root / 'caller.py'
            caller.write_text('import helper_mod\n')
            nested_caller = root / 'nested_caller.py'
            nested_caller.write_text('import p1.helper_mod\n')
            clean_dir = root / 'pos_clean_dir'
            clean_dir.mkdir()
            clean_py = clean_dir / 'clean.py'
            clean_py.write_text('import clean_zip_mod, sys; assert clean_zip_mod.VAL == 7; sys.exit(0)\n')
            unrelated_pc = root / 'unrelated_evil_pycache' / '__pycache__'
            unrelated_pc.mkdir(parents=True)
            shutil.copy2(evil_pyc, unrelated_pc / 'helper_mod.pyc')

            repo_root = Path(__file__).resolve().parents[1]
            co = routing_adapter.resolve_kicad_route_checkout()
            real_bridge = routing_adapter.resolve_native_bridge(co)

            with routing_adapter.re01_enforced():
                for label, fn in (
                    ('zip_top_pyc', lambda: subprocess.run(
                        [sys.executable, str(caller)],
                        env={**os.environ, 'PYTHONPATH': str(zip_top)}, timeout=5)),
                    ('zip_nested_pyc', lambda: subprocess.run(
                        [sys.executable, str(nested_caller)],
                        env={**os.environ, 'PYTHONPATH': str(zip_nested)}, timeout=5)),
                    ('zip_subprefix_pyo', lambda: subprocess.run(
                        [sys.executable, str(caller)],
                        env={**os.environ, 'PYTHONPATH': f'{zip_pyo}/sub'}, timeout=5)),
                    ('bwrap_zip_nested_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(zip_nested), '/opt/evil.zip',
                         '--setenv', 'PYTHONPATH', '/opt/evil.zip',
                         '--', sys.executable, str(nested_caller)], timeout=5)),
                ):
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                for pos_label, pos_argv, pos_env in (
                    ('pos_clean_zip', [sys.executable, str(clean_py)], {**os.environ, 'PYTHONPATH': str(zip_clean)}),
                    ('pos_pypath_src_tests', [sys.executable, '-B', str(clean_py)],
                     {**os.environ, 'PYTHONPATH': f"{repo_root / 'src'}{os.pathsep}{repo_root / 'tests'}{os.pathsep}{zip_clean}"}),
                    ('pos_bind_unrelated_evil_pc',
                     ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                      '--setenv', 'PYTHONPATH', str(zip_clean), '--', sys.executable, str(clean_py)], None),
                    *(([('pos_bwrap_real_bridge_help',
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(real_bridge), '/opt/bridge.py',
                         '--', sys.executable, '-B', '/opt/bridge.py', '--help'], None),
                       ('pos_bwrap_real_kr_co',
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--ro-bind', str(co), '/opt/kicad-route',
                         '--', sys.executable, '-B', str(real_bridge), '--help'], None)])
                      if real_bridge is not None and real_bridge.is_file() else []),
                ):
                    with self.subTest(case=pos_label):
                        done = subprocess.run(pos_argv, env=pos_env, capture_output=True, text=True, timeout=5)
                        self.assertEqual(done.returncode, 0, done.stderr)

    def test_re01_r18_depth1_forged_pycache_so_and_top_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            tag = sys.implementation.cache_tag
            ext_sfx = importlib.machinery.EXTENSION_SUFFIXES[0]
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("R18_PWN\\n")\nVAL = 99\n')
            evil_pyc = root / 'evil.pyc'
            py_compile.compile(str(evil_src), cfile=str(evil_pyc), doraise=True)
            evil_big = root / 'evil_big.py'
            evil_big.write_text('VAL = 99  # padded\n')
            os.utime(evil_big, (os.stat(evil_big).st_atime, os.stat(evil_src).st_mtime))
            clean_caller = root / 'clean_caller.py'
            clean_caller.write_text('import p1.helper_mod\n')

            sp1 = root / 'sp1'
            (sp1 / 'p1').mkdir(parents=True)
            (sp1 / 'caller.py').write_text('import p1.helper_mod\n')
            shutil.copy2(evil_pyc, sp1 / 'p1/helper_mod.pyc')
            sp2 = root / 'sp2'
            (sp2 / 'p1/p2').mkdir(parents=True)
            (sp2 / 'caller.py').write_text('import p1.p2.helper_mod\n')
            shutil.copy2(evil_pyc, sp2 / 'p1/p2/helper_mod.pyc')
            pp1 = root / 'pp1'
            (pp1 / 'p1').mkdir(parents=True)
            shutil.copy2(evil_pyc, pp1 / 'p1/helper_mod.pyc')
            us_home = root / 'us_home'
            us_site = us_home / f'.local/lib/python{ver}/site-packages'
            (us_site / 'p1').mkdir(parents=True)
            shutil.copy2(evil_pyc, us_site / 'p1/helper_mod.pyc')

            pc_d0 = root / 'pc_d0'
            (pc_d0 / '__pycache__').mkdir(parents=True)
            (pc_d0 / 'caller.py').write_text('import helper_mod\n')
            (pc_d0 / 'helper_mod.py').write_text(evil_big.read_text())
            py_compile.compile(str(evil_src), cfile=str(pc_d0 / f'__pycache__/helper_mod.{tag}.pyc'), doraise=True)
            pc_d1 = root / 'pc_d1'
            (pc_d1 / 'p1/__pycache__').mkdir(parents=True)
            (pc_d1 / 'caller.py').write_text('import p1.helper_mod\n')
            (pc_d1 / 'p1/helper_mod.py').write_text(evil_big.read_text())
            py_compile.compile(str(evil_src), cfile=str(pc_d1 / f'p1/__pycache__/helper_mod.{tag}.pyc'), doraise=True)

            sym_tgt = root / 'sym_tgt'
            sym_tgt.mkdir()
            shutil.copy2(evil_pyc, sym_tgt / 'helper_mod.pyc')
            sym_dir_sp = root / 'sym_dir_sp'
            sym_dir_sp.mkdir()
            (sym_dir_sp / 'caller.py').write_text('import p1.helper_mod\n')
            (sym_dir_sp / 'p1').symlink_to(sym_tgt)
            sym_pc = root / 'sym_pc'
            (sym_pc / 'p1').mkdir(parents=True)
            (sym_pc / 'caller.py').write_text('import p1.helper_mod\n')
            (sym_pc / 'p1/helper_mod.py').write_text(evil_big.read_text())
            (sym_pc / 'p1/__pycache__').symlink_to(pc_d1 / 'p1/__pycache__')
            sym_py = root / 'sym_py'
            (sym_py / 'p1/__pycache__').mkdir(parents=True)
            (sym_py / 'caller.py').write_text('import p1.helper_mod\n')
            (sym_py / 'p1/helper_mod.py').symlink_to(evil_big)
            py_compile.compile(str(evil_src), cfile=str(sym_py / f'p1/__pycache__/helper_mod.{tag}.pyc'), doraise=True)

            so_d0 = root / 'so_d0'
            so_d0.mkdir()
            (so_d0 / 'caller.py').write_text('import helper_mod\n')
            (so_d0 / f'helper_mod{ext_sfx}').write_bytes(b'\x7fELF')
            so_d1 = root / 'so_d1'
            (so_d1 / 'p1').mkdir(parents=True)
            (so_d1 / 'caller.py').write_text('import p1.helper_mod\n')
            (so_d1 / 'p1/helper_mod.abi3.so').write_bytes(b'\x7fELF')
            so_plain = root / 'so_plain'
            so_plain.mkdir()
            (so_plain / 'helper_mod.so').write_bytes(b'\x7fELF')
            clean_pkg = root / 'clean_pkg'
            (clean_pkg / 'sub').mkdir(parents=True)
            (clean_pkg / 'sub/__init__.py').write_text('VAL = 5\n')
            pos_clean_dir = root / 'pos_clean_dir'
            pos_clean_dir.mkdir()
            pos_opt_caller = pos_clean_dir / 'pos_opt_caller.py'
            pos_opt_caller.write_text('import sub, sys; assert sub.VAL == 5; sys.exit(0)\n')

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('host_sp0_d1_pyc', lambda: subprocess.run([sys.executable, str(sp1 / 'caller.py')], timeout=5)),
                    ('host_sp0_d2_pyc', lambda: subprocess.run([sys.executable, str(sp2 / 'caller.py')], timeout=5)),
                    ('host_pp_d1_pyc', lambda: subprocess.run(
                        [sys.executable, str(clean_caller)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(pp1)}, timeout=5)),
                    ('host_usersite_d1_pyc', lambda: subprocess.run(
                        [sys.executable, str(clean_caller)],
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(us_home)}, timeout=5)),
                    ('bwrap_roroot_sp0_d1_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(sp1 / 'caller.py')], timeout=5)),
                    ('bwrap_roroot_tmpfs_opt_sp0_d1_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                         '--', sys.executable, str(sp1 / 'caller.py')], timeout=5)),
                    ('bwrap_roroot_pp_d1_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--setenv', 'PYTHONPATH', str(pp1),
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('bwrap_roroot_usersite_d1_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--setenv', 'HOME', str(us_home),
                         '--', sys.executable, str(clean_caller)], timeout=5)),
                    ('host_d0_forged_pycache', lambda: subprocess.run(
                        [sys.executable, str(pc_d0 / 'caller.py')], timeout=5)),
                    ('host_d1_forged_pycache', lambda: subprocess.run(
                        [sys.executable, str(pc_d1 / 'caller.py')], timeout=5)),
                    ('bwrap_roroot_d1_forged_pycache', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(pc_d1 / 'caller.py')], timeout=5)),
                    ('host_sym_dir_d1_pyc', lambda: subprocess.run(
                        [sys.executable, str(sym_dir_sp / 'caller.py')], timeout=5)),
                    ('bwrap_roroot_sym_dir_d1_pyc', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(sym_dir_sp / 'caller.py')],
                        timeout=5)),
                    ('host_sym_pycache_d1', lambda: subprocess.run(
                        [sys.executable, str(sym_pc / 'caller.py')], timeout=5)),
                    ('bwrap_roroot_sym_pycache_d1', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(sym_pc / 'caller.py')], timeout=5)),
                    ('host_sym_py_forged_pc_d1', lambda: subprocess.run(
                        [sys.executable, str(sym_py / 'caller.py')], timeout=5)),
                    ('host_d0_so_sp0', lambda: subprocess.run([sys.executable, str(so_d0 / 'caller.py')], timeout=5)),
                    ('host_d1_so_sp0', lambda: subprocess.run([sys.executable, str(so_d1 / 'caller.py')], timeout=5)),
                    ('host_d0_so_pp', lambda: subprocess.run(
                        [sys.executable, str(clean_caller)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(so_plain)}, timeout=5)),
                    ('bwrap_tmpfs_opt_so', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--dir', '/opt/d',
                         '--ro-bind', str(so_d0 / 'caller.py'), '/opt/d/caller.py',
                         '--ro-bind', str(so_d0 / f'helper_mod{ext_sfx}'), f'/opt/d/helper_mod{ext_sfx}',
                         '--', sys.executable, '/opt/d/caller.py'], timeout=5)),
                    ('bwrap_roroot_so_d1', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(so_d1 / 'caller.py')], timeout=5)),
                    *[
                        (f'top_host_root_pp_{top_r}', lambda r=top_r: routing_adapter.check_re01_command(
                            [sys.executable, str(clean_caller)],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': r}))
                        for top_r in ('/', '/tmp', '/var/tmp', '/opt', '/var', '/home', '/usr', '/etc')
                    ],
                    ('top_host_root_bwrap_opt_pp', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                         '--setenv', 'PYTHONPATH', '/opt', '--', sys.executable, str(clean_caller)])),
                    ('top_host_root_bwrap_tmp_sp0', lambda: routing_adapter.check_re01_command(
                        ['bwrap', '--ro-bind', '/', '/', '--ro-bind', str(clean_caller), '/tmp/caller.py',
                         '--', sys.executable, '/tmp/caller.py'])),
                ]
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                done_opt = subprocess.run(
                    ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                     '--ro-bind', str(clean_pkg), '/opt/pkg',
                     '--setenv', 'PYTHONPATH', '/opt/pkg',
                     '--', sys.executable, str(pos_opt_caller)],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(done_opt.returncode, 0, done_opt.stderr)

    def test_re01_r18_pcbnew_dup_env_zip_and_host_usersite_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("R18_SIB_PWN\\n")\n')
            evil_pyc = root / 'evil.pyc'
            py_compile.compile(str(evil_src), cfile=str(evil_pyc), doraise=True)
            clean_dir = root / 'clean_dir'
            clean_dir.mkdir()
            clean_py = clean_dir / 'clean.py'
            clean_py.write_text('import sys; sys.exit(0)\n')
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')

            pp_pcb_pyc = root / 'pp_pcb_pyc'
            (pp_pcb_pyc / 'pcbnew').mkdir(parents=True)
            shutil.copy2(evil_pyc, pp_pcb_pyc / 'pcbnew/__init__.pyc')
            pp_pcb_py = root / 'pp_pcb_py'
            pp_pcb_py.mkdir()
            (pp_pcb_py / 'pcbnew.py').write_text(evil_src.read_text())
            pp_pcb_so = root / 'pp_pcb_so'
            pp_pcb_so.mkdir()
            (pp_pcb_so / 'pcbnew.so').write_bytes(b'\x7fELF')

            zip_nest_kr = root / 'nest_kr.zip'
            with zipfile.ZipFile(zip_nest_kr, 'w') as zf:
                zf.writestr('pkg/kicad_route/__init__.py', evil_src.read_text())
            zip_nest_pcb = root / 'nest_pcb.zip'
            with zipfile.ZipFile(zip_nest_pcb, 'w') as zf:
                zf.writestr('pkg/pcbnew.py', evil_src.read_text())

            co = routing_adapter.resolve_kicad_route_checkout()
            real_engine = routing_adapter.resolve_kicad_route_engine(co)
            real_bridge = routing_adapter.resolve_native_bridge(co)
            sym_kr_pp = root / 'sym_kr_pp'
            sym_kr_pp.mkdir()
            if (co / 'src/kicad_route').is_dir():
                (sym_kr_pp / 'sub_alias').symlink_to(co / 'src/kicad_route')

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('dup_env_bytes_str', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': str(pp_pcb_py).encode(),
                             'PYTHONPATH': str(clean_dir)}, timeout=5)),
                    ('dup_env_bwrap', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': str(pp_pcb_py).encode(),
                             'PYTHONPATH': str(clean_dir)}, timeout=5)),
                    ('dup_env_safepath', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONSAFEPATH': b'0', 'PYTHONSAFEPATH': '1'}, timeout=5)),
                    ('zip_nested_kicad_route', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(zip_nest_kr)}, timeout=5)),
                    ('zip_nested_pcbnew', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(zip_nest_pcb)}, timeout=5)),
                ]
                if (sym_kr_pp / 'sub_alias').is_symlink():
                    blocked_cases.extend([
                        ('sym_to_kicad_route_host', lambda: subprocess.run(
                            [sys.executable, str(clean_py)],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(sym_kr_pp)}, timeout=5)),
                        ('sym_to_kicad_route_bwrap', lambda: subprocess.run(
                            ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--ro-bind', str(sym_kr_pp), '/opt/pp',
                             '--setenv', 'PYTHONPATH', '/opt/pp', '--', sys.executable, str(clean_py)], timeout=5)),
                    ])
                if real_bridge is not None and real_bridge.is_file():
                    for lbl, pp_dir in (('pcb_pyc', pp_pcb_pyc), ('pcb_py', pp_pcb_py), ('pcb_so', pp_pcb_so)):
                        blocked_cases.append((f'bridge_{lbl}', lambda d=pp_dir: subprocess.run(
                            [sys.executable, '-B', str(real_bridge), '--help'],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(d)}, timeout=5)))
                if real_engine is not None and real_engine.is_file():
                    for lbl, pp_dir in (('pcb_pyc', pp_pcb_pyc), ('pcb_py', pp_pcb_py), ('pcb_so', pp_pcb_so)):
                        blocked_cases.append((f'route_native_{lbl}', lambda d=pp_dir: subprocess.run(
                            [str(real_engine), 'route-native', str(in_pcb), str(out_pcb)],
                            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(d)}, timeout=5)))
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                pw_dir = pwd.getpwuid(os.getuid()).pw_dir
                for pos_lbl, pos_argv, pos_env in (
                    ('pos_real_host_usersite', [sys.executable, str(clean_py)],
                     {'PATH': '/usr/bin:/bin', 'HOME': pw_dir}),
                    ('pos_bwrap_opaque_opt_pp',
                     ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                      '--ro-bind', str(clean_dir), '/opt/sub', '--setenv', 'PYTHONPATH', '/opt',
                      '--', sys.executable, str(clean_py)], None),
                ):
                    with self.subTest(case=pos_lbl):
                        done = subprocess.run(pos_argv, env=pos_env, capture_output=True, text=True, timeout=5)
                        self.assertEqual(done.returncode, 0, done.stderr)

    def test_re01_r19_nonutf8_bytes_surrogates_and_unrelated_env(self):
        class BytesPath:
            def __init__(self, raw):
                self._raw = raw

            def __fspath__(self):
                return self._raw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root_b = os.fsencode(root)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            clean_dir = root / 'clean_dir'
            clean_dir.mkdir()
            clean_py = clean_dir / 'clean.py'
            clean_py.write_text(
                'import os, sys\n'
                'val = os.environb.get(b"UNRELATED_NONUTF8")\n'
                'if val is not None:\n'
                '    assert val == b"val_\\xff", val\n'
                'sys.exit(0)\n'
            )
            caller = root / 'caller.py'
            caller.write_text('import p1.helper_mod\n')

            pp_b = root_b + b'/pp_\xff'
            os.makedirs(pp_b)
            with open(pp_b + b'/sitecustomize.py', 'w') as fh:
                fh.write(f'open({str(marker)!r}, "a").write("NU_SC\\n")\n')
            with open(pp_b + b'/pcbnew.py', 'w') as fh:
                fh.write(f'open({str(marker)!r}, "a").write("NU_PCB\\n")\n')
            home_b = root_b + b'/home_\xfe'
            us_b = home_b + f'/.local/lib/python{ver}/site-packages/p1'.encode()
            os.makedirs(us_b)
            py_compile.compile(str(clean_py), cfile=os.fsdecode(us_b + b'/helper_mod.pyc'), doraise=True)
            zip_b = root_b + b'/evil_\xff.zip'
            with zipfile.ZipFile(os.fsdecode(zip_b), 'w') as zf:
                zf.writestr('pcbnew.py', f'open({str(marker)!r}, "a").write("NU_ZIP\\n")\n')
            shebang_nonutf8 = root / 'shebang_nonutf8.sh'
            shebang_nonutf8.write_bytes(b'#!/tmp/dir_\xff/python3\n')
            shebang_nonutf8.chmod(0o755)
            shebang_nul = root / 'shebang_nul.sh'
            shebang_nul.write_bytes(b'#!/usr/bin/python3\x00/bin/sh\n')
            shebang_nul.chmod(0o755)
            in_pcb, out_pcb = root / 'in.kicad_pcb', root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            co = routing_adapter.resolve_kicad_route_checkout()
            real_engine = routing_adapter.resolve_kicad_route_engine(co)

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('pp_bytes', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': pp_b}, timeout=5)),
                    ('pp_pathlike_bytes', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': BytesPath(pp_b)}, timeout=5)),
                    ('pp_surrogate_str', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': os.fsdecode(pp_b)})),
                    ('pp_bytearray', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': bytearray(pp_b)})),
                    ('pp_memoryview', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py)],
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': memoryview(pp_b)})),
                    ('home_bytes', lambda: subprocess.run(
                        [sys.executable, str(caller)],
                        env={b'PATH': b'/usr/bin:/bin', b'HOME': home_b}, timeout=5)),
                    ('cwd_bytes', lambda: subprocess.run(
                        [sys.executable, str(clean_py)], cwd=pp_b,
                        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': '.'}, timeout=5)),
                    ('bwrap_robind_bytes', lambda: subprocess.run(
                        [b'bwrap', b'--ro-bind', b'/', b'/', b'--tmpfs', b'/opt',
                         b'--ro-bind', pp_b, b'/opt/pp', b'--setenv', b'PYTHONPATH', b'/opt/pp',
                         b'--', os.fsencode(sys.executable), os.fsencode(clean_py)], timeout=5)),
                    ('zipfile_bytes', lambda: subprocess.run(
                        [sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': zip_b}, timeout=5)),
                    ('posix_spawn_bytes', lambda: os.posix_spawn(
                        sys.executable, [sys.executable, str(clean_py)],
                        {b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': pp_b})),
                    ('argv_bytearray', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py), bytearray(b'arg_\xff')])),
                    ('argv_memoryview', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py), memoryview(b'arg_\xff')])),
                    ('argv_pathlike_bytes', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py), BytesPath(b'arg_\xff')])),
                    ('argv_surrogate_str', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py), 'arg_\udcff'])),
                    ('argv_nul', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py), 'arg\x00x'])),
                    ('env_key_surrogate', lambda: routing_adapter.check_re01_command(
                        [sys.executable, str(clean_py)],
                        env={b'PATH': b'/usr/bin:/bin', b'BAD_\xff': b'1'})),
                    *[
                        (f'sec_env_{sec_k}', lambda k=sec_k: routing_adapter.check_re01_command(
                            [sys.executable, str(clean_py)],
                            env={'PATH': '/usr/bin:/bin', k: 'val_\udcff'}))
                        for sec_k in (
                            'PATH', 'HOME', 'PYTHONPATH', 'TMPDIR', 'KICAD_CONFIG_HOME',
                            'AGENT_EE_TEST', 'PYTHONNOUSERSITE', 'LD_LIBRARY_PATH',
                            'GCONV_PATH', 'GLIBC_TUNABLES', '__PYVENV_LAUNCHER__',
                        )
                    ],
                    ('shebang_nonutf8', lambda: routing_adapter.check_re01_command(
                        [str(shebang_nonutf8)])),
                    ('shebang_nul', lambda: routing_adapter.check_re01_command(
                        [str(shebang_nul)])),
                ]
                if real_engine is not None and real_engine.is_file():
                    blocked_cases.append(('route_native_bytes_pp', lambda: subprocess.run(
                        [str(real_engine), 'route-native', '--python', sys.executable, str(in_pcb), str(out_pcb)],
                        env={b'PATH': b'/usr/bin:/bin', b'PYTHONPATH': pp_b}, timeout=5)))
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                with unittest.mock.patch.dict(os.environ, {'UNRELATED_NONUTF8': 'val_\udcff'}):
                    done = subprocess.run(
                        [sys.executable, str(clean_py)], capture_output=True, text=True, timeout=5)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    done_bw = subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--', sys.executable, str(clean_py)],
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(done_bw.returncode, 0, done_bw.stderr)

    def test_re01_r19_bwrap_opaque_host_bind_bypasses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'marker.txt'
            ver = f'{sys.version_info.major}.{sys.version_info.minor}'
            evil_src = root / 'evil_src.py'
            evil_src.write_text(f'open({str(marker)!r}, "a").write("R19_OPAQUE_PWN\\n")\n')
            evil_pyc = root / 'evil.pyc'
            py_compile.compile(str(evil_src), cfile=str(evil_pyc), doraise=True)

            evil_pp = root / 'evil_pp'
            evil_pp.mkdir()
            shutil.copy2(evil_pyc, evil_pp / 'helper_mod.pyc')
            evil_kr = root / 'evil_kr'
            (evil_kr / 'kicad_route').mkdir(parents=True)
            (evil_kr / 'kicad_route/__init__.py').write_text(evil_src.read_text())
            evil_us = root / 'evil_us'
            (evil_us / 'p1').mkdir(parents=True)
            shutil.copy2(evil_pyc, evil_us / 'p1/helper_mod.pyc')

            clean_dir = root / 'clean_dir'
            clean_dir.mkdir()
            clean_mount = root / 'clean_mount'
            clean_mount.mkdir()
            fake_home = root / 'fake_home'
            (fake_home / f'.local/lib/python{ver}/site-packages').mkdir(parents=True)
            clean_py = clean_dir / 'clean.py'
            clean_py.write_text('import sys; sys.exit(0)\n')
            caller_mod = clean_dir / 'caller_mod.py'
            caller_mod.write_text('import helper_mod\n')
            caller_kr = clean_dir / 'caller_kr.py'
            caller_kr.write_text('import kicad_route\n')
            caller_us = clean_dir / 'caller_us.py'
            caller_us.write_text('import p1.helper_mod\n')

            with routing_adapter.re01_enforced():
                blocked_cases = [
                    ('exact_tmpfs_evil_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(evil_pp),
                         '--ro-bind', str(evil_pp), str(clean_mount), '--setenv', 'PYTHONPATH', str(clean_mount),
                         '--', sys.executable, str(caller_mod)], timeout=5)),
                    ('parent_tmpfs_evil_pp', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(root),
                         '--ro-bind', str(evil_pp), str(clean_mount), '--setenv', 'PYTHONPATH', str(clean_mount),
                         '--', sys.executable, str(caller_mod)], timeout=5)),
                    ('exact_tmpfs_evil_kr', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(evil_kr),
                         '--ro-bind', str(evil_kr), str(clean_mount), '--setenv', 'PYTHONPATH', str(clean_mount),
                         '--', sys.executable, str(caller_kr)], timeout=5)),
                    ('parent_tmpfs_evil_kr', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(root),
                         '--ro-bind', str(evil_kr), str(clean_mount), '--setenv', 'PYTHONPATH', str(clean_mount),
                         '--', sys.executable, str(caller_kr)], timeout=5)),
                    ('exact_tmpfs_usersite', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(evil_us),
                         '--ro-bind', str(evil_us), str(fake_home / f'.local/lib/python{ver}/site-packages'),
                         '--setenv', 'HOME', str(fake_home), '--', sys.executable, str(caller_us)], timeout=5)),
                    ('parent_tmpfs_usersite', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', str(root),
                         '--ro-bind', str(evil_us), str(fake_home / f'.local/lib/python{ver}/site-packages'),
                         '--setenv', 'HOME', str(fake_home), '--', sys.executable, str(caller_us)], timeout=5)),
                    ('exact_tmpfs_opt_sub', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', str(evil_pp),
                         '--ro-bind', str(evil_pp), '/opt/sub', '--setenv', 'PYTHONPATH', '/opt/sub',
                         '--', sys.executable, str(caller_mod)], timeout=5)),
                    ('parent_tmpfs_opt_sub', lambda: subprocess.run(
                        ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt', '--tmpfs', str(root),
                         '--ro-bind', str(evil_pp), '/opt/sub', '--setenv', 'PYTHONPATH', '/opt/sub',
                         '--', sys.executable, str(caller_mod)], timeout=5)),
                ]
                for label, fn in blocked_cases:
                    with self.subTest(case=label):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            fn()
                self.assertFalse(marker.exists())
                done_pos = subprocess.run(
                    ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/opt',
                     '--ro-bind', str(clean_dir), '/opt/sub', '--setenv', 'PYTHONPATH', '/opt',
                     '--', sys.executable, str(clean_py)],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(done_pos.returncode, 0, done_pos.stderr)


class NativeAdapterHardeningTests(unittest.TestCase):
    def test_not_run_attempt_sets_native_backend_and_verifies(self):
        checkout = require_kicad_route()
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires Rust-capable kicad-route checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cand = root / 'candidate'
            work = root / 'work'
            (cand / 'project').mkdir(parents=True)
            work.mkdir()
            (cand / 'project/b.kicad_pcb').write_text('(kicad_pcb)\n')
            # Omit schematic so attempt() stops at not_run before launching the router.
            request = attempt.parse_request([
                '--project', 'project', '--board', 'b.kicad_pcb', '--schematic', 'b.kicad_sch',
                '--name', 'b', '--label', 'l', '--budget', '10', '--timeout', '30', '--backend', 'builtin',
                '--state', 'checkout',
            ])
            layout = {
                'work': str(work), 'candidate': str(cand), 'checkout': str(checkout),
                'state': str(checkout), 'staged': str(work / 'staged'), 'probe': [],
            }
            report = attempt.attempt(request, layout)
            self.assertEqual(report['semantics']['classification'], 'not_run')
            self.assertEqual(report['backend'], attempt.native_backend_of(None))
            summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(cand)), layout=layout)
            self.assertEqual(summary['classification'], 'not_run')

    def test_validate_campaign_step_host_false_with_external_engine_mount(self):
        checkout = require_kicad_route()
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires Rust-capable kicad-route checkout')
        with tempfile.TemporaryDirectory() as tmp:
            ext_engine = Path(tmp) / 'kicad-route-engine'
            ext_engine.write_text('#!/bin/sh\nexit 0\n')
            ext_engine.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_ENGINE': str(ext_engine)}):
                spec = routing_adapter.step(
                    project='project', board='b.kicad_pcb', schematic='b.kicad_sch',
                    name='b', label='l', checkout=checkout, budget=10,
                )
            self.assertTrue(any(m['at'] == attempt.SANDBOX['engine'] for m in spec['mounts']))
            # With host=False and an absent checkout, spec['backend_report'] still validates the engine mount.
            for m in spec['mounts']:
                if m['at'] == attempt.SANDBOX['checkout']:
                    m['source'] = str(Path(tmp) / 'absent-checkout-on-replay-host')
            validated = routing_adapter.validate_campaign_step(spec, host=False)
            self.assertEqual(validated['backend_report'], spec['backend_report'])

    def test_validate_campaign_step_host_true_validates_engine_mount(self):
        checkout = require_kicad_route()
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires Rust-capable kicad-route checkout')
        with tempfile.TemporaryDirectory() as tmp:
            ext_engine = Path(tmp) / 'kicad-route-engine'
            ext_engine.write_text('#!/bin/sh\nexit 0\n')
            ext_engine.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_ENGINE': str(ext_engine)}):
                spec = routing_adapter.step(
                    project='project', board='b.kicad_pcb', schematic='b.kicad_sch',
                    name='b', label='l', checkout=checkout, budget=10,
                )
                self.assertEqual(routing_adapter.validate_campaign_step(spec, host=True)['backend_report'],
                                 spec['backend_report'])
                tampered = json.loads(json.dumps(spec))
                for m in tampered['mounts']:
                    if m['at'] == attempt.SANDBOX['engine']:
                        m['source'] = '/usr/bin/true'
                with self.assertRaisesRegex(ValueError, 'routing mount sources must use resolved host paths'):
                    routing_adapter.validate_campaign_step(tampered, host=True)
                omitted = json.loads(json.dumps(spec))
                omitted['mounts'] = [m for m in omitted['mounts'] if m['at'] != attempt.SANDBOX['engine']]
                with self.assertRaisesRegex(ValueError, 'campaign routing step differs'):
                    routing_adapter.validate_campaign_step(omitted, host=True)
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('AGENT_EE_KICAD_ROUTE_ENGINE', None)
                os.environ.pop('KICAD_ROUTE_ENGINE_BIN', None)
                internal_spec = routing_adapter.step(
                    project='project', board='b.kicad_pcb', schematic='b.kicad_sch',
                    name='b', label='l', checkout=checkout, budget=10,
                )
                self.assertFalse(any(m['at'] == attempt.SANDBOX['engine'] for m in internal_spec['mounts']))
                self.assertEqual(routing_adapter.validate_campaign_step(internal_spec, host=True)['backend_report'],
                                 internal_spec['backend_report'])
                spurious = json.loads(json.dumps(internal_spec))
                spurious['mounts'].append({'source': '/usr/bin/true', 'at': attempt.SANDBOX['engine']})
                with self.assertRaisesRegex(ValueError, 'campaign routing step differs'):
                    routing_adapter.validate_campaign_step(spurious, host=True)

    def test_native_semantics_rejects_bool_float_inline_mismatch_and_strict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'src.kicad_pcb'
            src.write_text('(kicad_pcb)\n')
            req_file = root / 'operation-request.json'
            req_text = '{"schema":"kicad-route/native-request/1"}\n'
            req_file.write_text(req_text)
            req_sha = attempt.digest(req_file)
            base_op = {
                'schema': attempt.NATIVE_OPERATION_SCHEMA,
                'version': 1,
                'exit_code': 2,
                'status': 'invalid',
                'request': {'request_sha256': req_sha, 'request_json': req_text},
                'source': {'source_sha256': attempt.digest(src), 'sidecar_check': {'passed': True}},
                'publication': {'published': False, 'ambiguous_termination': False, 'output_sha256': None},
            }
            self.assertEqual(attempt.native_semantics(2, base_op, req_file, src)[0], 'refused')
            for mutate in (
                lambda o: o.update(version=True),
                lambda o: o.update(version=1.0),
                lambda o: o.update(exit_code=2.0),
                lambda o: o['request'].update(request_json='{"tampered":true}'),
            ):
                bad = json.loads(json.dumps(base_op))
                mutate(bad)
                self.assertEqual(attempt.native_semantics(2, bad, req_file, src)[0], 'contradiction')
            op_path = root / 'dup-op.json'
            op_path.write_text('{"schema":"kicad-route/native-operation/1","version":1,"version":1}')
            bound = routing_adapter.bind_board_attempt(
                root, ['kicad-route-engine', 'route-native', str(src), str(root / 'out.kicad_pcb'),
                       '--operation-result', str(op_path)], 2)
            self.assertEqual(bound['status'], 'refused_result')

    def test_verify_and_run_native_attempt_enforce_checks_on_self_reported_exit6(self):
        checkout = require_kicad_route()
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires Rust-capable kicad-route checkout')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cand = root / 'candidate'
            work = root / 'work'
            (cand / 'project').mkdir(parents=True)
            work.mkdir()
            src = cand / 'project/b.kicad_pcb'
            src.write_text('(kicad_pcb)\n')
            (cand / 'project/b.kicad_sch').write_text('(kicad_sch)\n')
            request = attempt.parse_request([
                '--project', 'project', '--board', 'b.kicad_pcb', '--schematic', 'b.kicad_sch',
                '--name', 'b', '--label', 'l', '--budget', '10', '--timeout', '30',
                '--backend', 'builtin', '--state', 'checkout',
            ])
            layout = {
                'work': str(work), 'candidate': str(cand), 'checkout': str(checkout),
                'state': str(checkout), 'staged': str(work / 'staged'), 'probe': [],
            }
            rec, generation, _ = attempt.preflight(checkout, checkout, 'builtin', mounted_at=checkout)
            routing = work / 'routing'
            attempt_dir = routing / 'attempt'
            attempt_dir.mkdir(parents=True)
            req_path = attempt_dir / attempt.NATIVE_REQUEST_FILE
            req_path.write_text('{"schema":"kicad-route/native-request/1"}\n')
            op_path = attempt_dir / attempt.NATIVE_OPERATION_FILE
            # Tampered op_result claims exit_code 0 while process exit_code was 6 (self-reported timeout).
            op_path.write_text(json.dumps({
                'schema': attempt.NATIVE_OPERATION_SCHEMA, 'version': 1, 'exit_code': 0,
                'status': 'operation_timeout',
                'request': {'request_sha256': attempt.digest(req_path)},
                'source': {'source_sha256': attempt.digest(src), 'sidecar_check': {'passed': True}},
                'publication': {'published': False, 'ambiguous_termination': False, 'output_sha256': None},
                'binary': {'name': 'kicad-route-engine'},
            }))
            inputs_tree = attempt.tree(cand)
            staged = attempt.stage_original(cand, inputs_tree, attempt.staged_root(layout))
            engine_bin = attempt.resolve_kicad_route_engine(checkout)
            argv = attempt.native_tool_argv(request, {**layout, 'engine': str(engine_bin)})
            report = {
                'schema': attempt.REPORT_SCHEMA,
                'adapter': attempt.adapter_claim(rec['commit']),
                'request': request,
                'reproducibility': attempt.REPRODUCIBILITY['builtin'],
                'tool_version': dict(attempt.TOOL_VERSION),
                'backend': attempt.native_backend_of({'binary': {'name': 'kicad-route-engine'}}),
                'tool': {**rec,
                         'kicad_route': attempt._executable(checkout / 'bin/kicad-route'),
                         'kicad_route_engine': attempt._executable(engine_bin),
                         'python': attempt._executable(attempt.PYTHON),
                         'kicad_cli': attempt._executable('/usr/bin/kicad-cli'),
                         'kicad_share_templates': {t: attempt._executable(f'/usr/share/kicad/template/{t}')['sha256']
                                                   for t in ('sym-lib-table', 'fp-lib-table')}},
                'inputs': {'files': attempt.files_of(inputs_tree),
                           'sha256': attempt.canonical_identity(inputs_tree),
                           'staged': attempt.staged_record(request, layout, staged),
                           'staged_after': {'status': 'observed', 'files': attempt.files_of(inputs_tree),
                                            'sha256': attempt.canonical_identity(inputs_tree)}},
                'process': {'argv': argv, 'env': attempt.tool_env(layout), 'cwd': str(routing),
                            'timeout_s': 30, 'exit_code': 6, 'timed_out': False, 'cancelled': False},
                'discovery': {'attempt_dir': None, 'problems': []}, 'missing_artifacts': [],
                'retained': {f'attempt/{attempt.NATIVE_OPERATION_FILE}': attempt.digest(op_path),
                             f'attempt/{attempt.NATIVE_REQUEST_FILE}': attempt.digest(req_path)},
                'omitted_design_files': {}, 'candidate': None, 'candidate_editor_state': {},
                'candidate_files': {}, 'rejected_candidate_sha256': None,
                'identity_before': attempt._identities(request, layout, generation),
                'identity_after': attempt._identities(request, layout, generation),
                'state_write_probe': [],
                'semantics': {'classification': 'timeout', 'reasons': ['forged'], 'exit_code': 6},
            }
            attempt._finish(report, routing)
            with self.assertRaisesRegex(ValueError, 'classify as contradiction'):
                routing_adapter.verify(work, attempt.files_of(inputs_tree), layout=layout)

    def test_re01_route_native_flag_parity_and_pycache_private_path(self):
        checkout = require_kicad_route()
        if not routing_adapter.is_rust_capable_checkout(checkout):
            self.skipTest('requires Rust-capable kicad-route checkout')
        real_engine = routing_adapter.resolve_kicad_route_engine(checkout)
        real_bridge = routing_adapter.resolve_native_bridge(checkout)
        self.assertIsNotNone(real_engine)
        self.assertIsNotNone(real_bridge)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_pcb = root / 'in.kicad_pcb'
            out_pcb = root / 'out.kicad_pcb'
            in_pcb.write_text('(kicad_pcb)\n')
            eng_str = str(real_engine)
            br_str = str(real_bridge)
            priv_pc = root / 'pc_ok'
            priv_env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': str(root),
                        'PYTHONPYCACHEPREFIX': str(priv_pc)}
            with routing_adapter.re01_enforced():
                # Valid route-native flags supported under RE-01 direct-exec allowlist
                for ok_cmd in (
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--protect', 'existing'],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--protect', 'human', '--nets', '/SIG_A', '!/GND'],
                    [eng_str, 'route-native', '--native-bridge', br_str,
                     '--nets', '/SIG_A', '/SIG_B', str(in_pcb), str(out_pcb)],
                    [eng_str, 'route-native', '--nets', 'SIG_A', str(in_pcb), str(out_pcb),
                     '--rules', str(root / 'rules.json'), '--native-bridge', br_str],
                    [eng_str, 'route-native', '--nets', 'SIG_A', str(in_pcb), str(out_pcb),
                     '--timeout-ms', '15000', '--native-bridge', br_str],
                    [eng_str, 'route-native', str(in_pcb), '--nets', 'SIG_A', str(out_pcb),
                     '--native-bridge', br_str],
                    [eng_str, 'route-native', '--pair-layers', 'F.Cu', 'B.Cu',
                     str(in_pcb), str(out_pcb), '--native-bridge', br_str],
                    [eng_str, 'route-native', '--pair-layers', 'F.Cu', 'B.Cu',
                     str(in_pcb), '--rules', str(root / 'rules.json'), str(out_pcb),
                     '--native-bridge', br_str],
                    [eng_str, 'route-native', '--length-match-group', 'N1', 'N2',
                     str(in_pcb), str(out_pcb), '--native-bridge', br_str],
                    [eng_str, 'route-native', '--protect', 'human', '--nets', 'SIG_A',
                     str(in_pcb), str(out_pcb), '--native-bridge', br_str],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--bus', '--bus-min-nets', '3',
                     '--bus-detection-radius', '20', '--bus-attraction-radius', '5',
                     '--bus-attraction-bonus', '100', '--grid-step', '0.1'],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--diff-pairs', 'auto',
                     '--pair-layers', 'F.Cu', 'B.Cu', '--no-short-pair-deferral',
                     '--diff-pair-intra-match', '--length-match-tolerance', '0.1',
                     '--meander-amplitude', '1.0', '--ac-couple-chain-json', '["C1","C2"]',
                     '--ac-match-tolerance', '0.1', '--ac-meander-amplitude', '2.0'],
                ):
                    with self.subTest(ok_cmd=ok_cmd):
                        routing_adapter.check_re01_command(ok_cmd, env=priv_env)

                # Invalid --protect mode, inline value on boolean flag, variadic missing positionals
                for bad_cmd in (
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--protect', 'bogus'],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--no-short-pair-deferral=1'],
                    [eng_str, 'route-native', '--native-bridge', br_str,
                     '--nets', '/SIG_A', str(in_pcb)],
                    [eng_str, 'route-native', '--nets', 'SIG_A', str(in_pcb),
                     '--rules', str(root / 'rules.json'), '--native-bridge', br_str],
                    [eng_str, 'route-native', '--pair-layers', str(in_pcb),
                     '--rules', str(root / 'rules.json'), str(out_pcb), '--native-bridge', br_str],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--pair-layers', 'F.Cu', '--pair-layers', 'B.Cu'],
                    [eng_str, 'route-native', str(in_pcb), str(out_pcb),
                     '--native-bridge', br_str, '--nets', '/SIG_A', str(root / 'kicad_route/evil')],
                ):
                    with self.subTest(bad_cmd=bad_cmd):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            routing_adapter.check_re01_command(bad_cmd, env=priv_env)

                # GAP-08: relative or non-private PYTHONPYCACHEPREFIX must fail closed
                for bad_pc_env in (
                    {'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': 'rel_cache'},
                    {'PATH': '/usr/bin:/bin', 'PYTHONPYCACHEPREFIX': '/opt/evil_pc'},
                    {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': str(root),
                     'PYTHONPYCACHEPREFIX': str(root)},
                ):
                    with self.subTest(bad_pc_env=bad_pc_env):
                        with self.assertRaises(routing_adapter.RE01OwnershipError):
                            routing_adapter.check_re01_command(
                                [sys.executable, '-I', '-B', br_str, '--help'], env=bad_pc_env)


if __name__ == '__main__':
    unittest.main()
