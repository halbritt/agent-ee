"""Actual kicad-route (pinned 5f1c072) and KRT on the original placed RC, read-only.

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

    def state(self):
        return {rel: attempt.tree(self.checkout / rel) for rel in ('bin', 'src', '.venv-krt')}

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
