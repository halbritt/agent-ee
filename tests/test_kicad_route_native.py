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
        return placed

    def placed_native(self):
        placed = self.placed_rc()
        if routing_adapter.is_rust_capable_checkout(self.checkout):
            made = execute(placed, self.root / 'make-native',
                           ['/usr/bin/python3', '-I', '-B', '-c', PAIR_BOARD, '/work/native.kicad_pcb'], timeout=300)
            self.assertEqual(made['exit_code'], 0, (self.root / 'make-native/console.log').read_text()[-2000:])
            shutil.copyfile(self.root / 'make-native/native.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
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
        placed = self.placed_native()
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
            placed = self.placed_native()
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
            missing_engine = self.root / 'no-such-engine'
            with unittest.mock.patch.dict(os.environ, {'AGENT_EE_KICAD_ROUTE_ENGINE': str(missing_engine),
                                                       'AGENT_EE_KICAD_ROUTE_CHECKOUT': str(broken)}):
                problems = routing_adapter.preflight(broken)['problems']
                self.assertIn('missing executable kicad-route-engine binary', problems)
                with self.assertRaisesRegex(AssertionError, 'missing executable kicad-route-engine binary'):
                    require_kicad_route()
            # Separate explicit state manifest without manifest.json still refuses.
            sep_state = self.root / 'broken-state'
            (sep_state / '.venv-krt').mkdir(parents=True)
            (sep_state / 'manifest.json').write_text('{invalid')
            sep_problems = routing_adapter.preflight(broken, sep_state)['problems']
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
        placed = self.placed_native()
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
            bridge = co / 'tools/rust_engine_kicad.py'
            bridge.write_text('import sys; sys.exit(0)\n')
            self.assertIsNone(routing_adapter.router_recipe(co))
            for fn in (
                lambda: routing_adapter.spokes_argv(co, '/run'),
                lambda: routing_adapter.preflight_stackup_argv(co, '/b.kicad_pcb', 'F.Cu'),
                lambda: routing_adapter.preflight_delay_argv(co, '/b.kicad_pcb', ['N']),
                lambda: routing_adapter.post_route_checker_argv(co, 'pair_impedance', '/b', '/req', '/res'),
            ):
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    fn()
            with routing_adapter.re01_enforced():
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run([sys.executable, str(co / 'bin/kicad-route'), '--help'], timeout=5)
                with self.assertRaises(routing_adapter.RE01OwnershipError):
                    subprocess.run([sys.executable, str(co / 'src/kicad_route/cli.py')], timeout=5)
                done = subprocess.run([sys.executable, str(bridge)], capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 0)


if __name__ == '__main__':
    unittest.main()
