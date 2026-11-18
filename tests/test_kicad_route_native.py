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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import traceback
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
            with self.assertRaisesRegex(ValueError, 'diff_pairs'):
                routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                                     schematic='rc_filter.kicad_sch', name='rc_filter', label='pairs-empty',
                                     checkout=self.checkout, budget=120, diff_pairs='auto')
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
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            with self.assertRaisesRegex(ValueError, 'diff_pairs'):
                routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
                                     schematic='rc_filter.kicad_sch', name='rc_filter', label='pairs-native',
                                     checkout=self.checkout, budget=120, diff_pairs='auto')
            return
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
                for ok_argv in (
                    [sys.executable, str(bridge)],
                    ['git', '-C', str(co), '-c', 'core.fsmonitor=false', '--version'],
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
                ):
                    with self.subTest(ok_argv=ok_argv):
                        done = subprocess.run(ok_argv, capture_output=True, timeout=5)
                        self.assertEqual(done.returncode, 0, done.stderr)


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


if __name__ == '__main__':
    unittest.main()
