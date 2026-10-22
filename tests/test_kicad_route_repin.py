"""The kicad-route b577449 re-pin (AGENTEE-450).

Policy cases use the coordinator-owned fixture router from kicad_route_fixture.py,
which mirrors the pin's result contract. Native cases run the real pinned
kicad-route through the adapter and the networkless sandbox, read-only, on
original boards authored here:

- the adapter accepts the new producer and refuses evidence from another pin
  with a typed StaleRoutingEvidence, without rewriting it;
- the pin's copper-custody pointer is bound to the retained bytes and never graded;
- native terminal preservation (KICADROUTE-98) gates eligibility: a partially
  joined net keeps its joined pads, a capped raw DRC report never yields a board;
- an invalid complete candidate never delivers a board while a valid incomplete one does;
- agent-ee emits only the canonical 'builtin' backend name.
"""
import contextlib
import inspect
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import generate
from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from tests.kicad_route_fixture import FAKE_PIN
from tests.test_kicad_route_adapter import REQUEST, Case
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
OLD_PIN = '5f1c072b86e0caee82c7656226c4d0e3ec580a58'
FIXTURE_RECEIPT = ROOT / 'fixtures/rc/stages/receipts'  # retained at 08179cd, never rewritten


def rewrite_report(case, change):
    """Edit the retained adapter report and refresh its identity, as a forger would."""
    path = case.work / 'routing/adapter-report.json'
    report = json.loads(path.read_text())
    change(report)
    attempt.write_report(path, report)


def relabel_result(case, change):
    """Edit the retained result.json and refresh every hash the report keeps for it."""
    path = case.work / 'routing/attempt/result.json'
    result = json.loads(path.read_text())
    change(result)
    path.write_text(json.dumps(result))
    rewrite_report(case, lambda r: r['retained'].update({'attempt/result.json': attempt.digest(path)}))


class RepinPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-repin-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def run_case(self, scenario, argv=REQUEST):
        self.count += 1
        with contextlib.redirect_stdout(io.StringIO()):
            return Case(Path(self.tmp.name) / str(self.count), scenario).run(argv)

    def test_new_pin_identity(self):
        self.assertEqual(attempt.PIN, 'b5774496d8466b67ffa0362b6665fee2108155a3')
        self.assertNotEqual(attempt.PIN, OLD_PIN)
        self.assertEqual(attempt.default_layout(attempt.parse_request(REQUEST))['pin'], attempt.PIN)
        # The pin's owned engine and the modules added since 5f1c072 are byte-pinned.
        for rel in ('src/kicad_route/engine.py', 'src/kicad_route/copper_ownership.py',
                    'src/kicad_route/native_components.py', 'src/kicad_route/backend_names.py',
                    'src/kicad_route/router/rust_router/Cargo.lock'):
            self.assertIn(rel, attempt.PINNED_CODE)

    def test_emitted_backend_name_is_canonical(self):
        self.assertEqual(attempt.parse_request(REQUEST)['backend'], 'builtin')
        layout = {'work': '/w', 'candidate': '/c', 'checkout': '/k'}
        for name in ('builtin', 'krt'):  # 'krt' is still read, never passed on
            argv = attempt.tool_argv(attempt.parse_request(REQUEST + ['--backend', name]), layout)
            self.assertEqual(argv[argv.index('--backend') + 1], 'builtin')
        declaration = {'schema': 'agent-ee.routing-adapter/1', 'project': 'p', 'board': 'b.kicad_pcb',
                       'schematic': 'b.kicad_sch', 'name': 'n', 'label': 'l', 'checkout': '/opt/kicad-route'}
        self.assertEqual(routing_adapter.validate_declaration(declaration)['backend'], 'builtin')
        self.assertEqual(inspect.signature(routing_adapter.step).parameters['backend'].default, 'builtin')
        self.assertEqual(inspect.signature(routing_adapter.preflight).parameters['backend'].default, 'builtin')
        source = inspect.getsource(generate.stage_routing)
        self.assertIn("'--backend', 'builtin'", source)
        self.assertNotIn("'--backend', 'krt'", source)

    def test_new_producer_is_accepted(self):
        case = self.run_case('complete')
        self.assertEqual(case.exit, 0, case.report['semantics'])
        summary = case.verify()
        self.assertEqual((summary['classification'], summary['proposal']), ('complete', 'eligible'))
        result = json.loads((case.work / 'routing/attempt/result.json').read_text())
        self.assertEqual(result['backend']['name'], 'builtin')
        self.assertEqual(result['copper_ownership']['status'], 'bound')
        self.assertIn('attempt/validate/copper-ownership.json', case.report['retained'])

    def test_copper_custody_pointer_is_bound_and_never_graded(self):
        unavailable = self.run_case('custody_unavailable')
        self.assertEqual(unavailable.exit, 0, unavailable.report['semantics'])
        self.assertEqual(unavailable.verify()['proposal'], 'eligible')
        for scenario, reason in (('custody_missing', 'result.json shape differs from the pinned tool'),
                                 ('custody_authority', 'copper_ownership differs from the pinned tool'),
                                 ('custody_forged', 'copper ownership record differs from its result pointer')):
            with self.subTest(scenario):
                case = self.run_case(scenario)
                self.assertEqual(case.exit, attempt.EXIT_CONTRADICTION)
                self.assertEqual(case.report['semantics']['classification'], 'contradiction')
                self.assertIn(reason, case.report['semantics']['reasons'])
                self.assertIsNone(case.report['candidate'])
        # Only a builtin result whose process passed carries the custody pointer.
        case = self.run_case('complete')
        result = json.loads((case.work / 'routing/attempt/result.json').read_text())
        freerouting = {**case.report['request'], 'backend': 'freerouting'}
        self.assertEqual(attempt.semantics(0, {**result, 'backend': {**result['backend'], 'name': 'freerouting'}},
                                           None, False, True, freerouting),
                         ('contradiction', ['result.json shape differs from the pinned tool']))
        failed = {**result, 'process': {'status': 'FAIL', 'reasons': ['x']}, 'exit_code': 1}
        self.assertEqual(attempt.semantics(1, failed, None, False, True, case.report['request']),
                         ('contradiction', ['result.json shape differs from the pinned tool']))

    def test_custody_record_changed_after_retention_is_refused(self):
        case = self.run_case('complete')
        record = case.work / 'routing/attempt/validate/copper-ownership.json'
        record.write_text('{"schema": "kicad-route/copper-ownership/1", "forged": true}\n')
        rewrite_report(case, lambda r: r['retained'].update(
            {'attempt/validate/copper-ownership.json': attempt.digest(record)}))
        with self.assertRaisesRegex(ValueError, 'copper ownership record differs from the retained routing evidence'):
            case.verify()

    def test_native_terminal_preservation_gates_eligibility(self):
        for scenario, reason in (
                ('terminal_split', 'native terminal preservation (net_completion) is missing or did not pass'),
                ('terminal_missing', 'native terminal preservation (net_completion) is missing or did not pass'),
                ('terminal_complete_split', 'routing COMPLETE while native terminals remain split')):
            with self.subTest(scenario):
                case = self.run_case(scenario)
                self.assertEqual(case.exit, attempt.EXIT_CONTRADICTION, case.report['semantics'])
                self.assertIn(reason, case.report['semantics']['reasons'])
                self.assertIsNone(case.report['candidate'])
                self.assertFalse((case.work / 'routing/candidate').exists())
        # The retained bytes of an honest eligible attempt, edited to show a split, are refused.
        case = self.run_case('incomplete')
        self.assertEqual(case.verify()['proposal'], 'eligible_partial')
        relabel_result(case, lambda r: r['validation']['net_completion']['split_components'].append(
            {'net': '/IN', 'pads': ['u1', 'u2']}))
        with self.assertRaisesRegex(ValueError, 'native terminal preservation'):
            case.verify()

    def test_invalid_complete_candidate_never_beats_a_valid_incomplete_one(self):
        invalid = self.run_case('validation_failed')
        valid = self.run_case('incomplete')
        invalid_result = json.loads((invalid.work / 'routing/attempt/result.json').read_text())
        valid_result = json.loads((valid.work / 'routing/attempt/result.json').read_text())
        # The invalid candidate routed everything; the valid one left nets open.
        self.assertEqual((invalid_result['routing']['status'], invalid_result['validation']['status']),
                         ('COMPLETE', 'FAIL'))
        self.assertEqual((valid_result['routing']['status'], valid_result['validation']['status']),
                         ('INCOMPLETE', 'PASS'))
        invalid_summary, valid_summary = invalid.verify(), valid.verify()
        self.assertEqual((invalid_summary['classification'], invalid_summary['proposal'],
                          invalid_summary['coverage'], invalid_summary['candidate_sha256']),
                         ('validation_failed', 'none', 'missing_routing', None))
        self.assertFalse((invalid.work / 'routing/candidate').exists())
        self.assertEqual((valid_summary['classification'], valid_summary['proposal']),
                         ('incomplete', 'eligible_partial'))
        self.assertTrue((valid.work / 'routing/candidate/project/rc.kicad_pcb').is_file())

    def test_old_pin_evidence_is_a_typed_stale_refusal(self):
        # A receipt retained at an older pin keeps its bytes and is refused as stale.
        before = {p: p.read_bytes() for p in FIXTURE_RECEIPT.rglob('*') if p.is_file()}
        with self.assertRaises(routing_adapter.StaleRoutingEvidence) as caught:
            routing_adapter.verify(FIXTURE_RECEIPT)
        self.assertEqual(caught.exception.pin, '08179cdc97d08864da127f14d53ac46cf5a27851')
        self.assertEqual(caught.exception.expected, attempt.PIN)
        self.assertEqual({p: p.read_bytes() for p in FIXTURE_RECEIPT.rglob('*') if p.is_file()}, before)
        # An honest attempt relabelled as produced by 5f1c072: stale before anything else.
        case = self.run_case('complete')
        self.assertEqual(case.verify()['proposal'], 'eligible')
        rewrite_report(case, lambda r: (r['adapter'].update(compatibility=f'kicad-route@{OLD_PIN}'),
                                        r['tool'].update(pin=OLD_PIN, commit=OLD_PIN)))
        with self.assertRaises(routing_adapter.StaleRoutingEvidence) as caught:
            case.verify()
        self.assertEqual((caught.exception.pin, caught.exception.expected), (OLD_PIN, FAKE_PIN))
        self.assertIn('stale routing evidence', str(caught.exception))
        # A frozen campaign binding captured on the old pin is stale too.
        binding = {'spec': {}, 'tool': {}, 'pin': OLD_PIN, 'adapter_sha256': '0' * 64}
        with self.assertRaises(routing_adapter.StaleRoutingEvidence):
            routing_adapter.validate_binding(binding, None, None, None, None)

    def test_mismatched_evidence_is_refused_but_not_called_stale(self):
        for label, change, message in (
                ('malformed compatibility', lambda r: r['adapter'].update(compatibility='kicad-route@5f1c072'),
                 'different kicad-route pin or location'),
                ('current pin, other commit ran', lambda r: r['tool'].update(commit=OLD_PIN),
                 'not the pinned code')):
            with self.subTest(label):
                case = self.run_case('complete')
                rewrite_report(case, change)
                with self.assertRaisesRegex(ValueError, message) as caught:
                    case.verify()
                self.assertNotIsInstance(caught.exception, routing_adapter.StaleRoutingEvidence)


class RepinNativeTest(unittest.TestCase):
    """The real b577449 checkout through the adapter, read-only, on original boards."""

    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-repin-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_installed_build_identity(self):
        check = routing_adapter.preflight(self.checkout)
        self.assertEqual(check['problems'], [])
        self.assertEqual(check['commit'], attempt.PIN)
        manifest = json.loads((self.checkout / 'manifest.json').read_text())
        self.assertEqual(manifest['state_dir'], attempt.SANDBOX['checkout'])
        self.assertEqual((manifest['kicad_route']['commit'], manifest['krt']['commit']), (attempt.PIN, attempt.PIN))
        self.assertIsNone(manifest['krt']['generation'])
        module = manifest['krt']['extension_module']
        self.assertEqual(attempt.digest(self.checkout / module['path']), module['sha256'])
        self.assertEqual(check['executable_sha256'], attempt.PINNED_CODE['bin/kicad-route'])

    def candidate(self, script, name):
        """An original board in the RC project's place; schematic and project unchanged."""
        placed = self.root / name
        shutil.copytree(ROOT / 'fixtures/rc/control', placed / 'project')
        shutil.copytree(ROOT / 'fixtures/rc/requirements', placed / 'requirements')
        made = execute(placed, self.root / f'make-{name}',
                       ['/usr/bin/python3', '-I', '-B', '-c', script, '/work/board.kicad_pcb'], timeout=300)
        self.assertEqual(made['exit_code'], 0, (self.root / f'make-{name}/console.log').read_text()[-2000:])
        shutil.copyfile(self.root / f'make-{name}/board.kicad_pcb', placed / 'project/rc_filter.kicad_pcb')
        return placed

    def route(self, placed, label, budget):
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
                                    name='rc_filter', label=label, checkout=self.checkout, budget=budget)
        self.assertEqual(spec['step'], 'kicad-route-builtin')
        work = self.root / label
        record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        return record, report, summary, result, work

    def test_partial_net_keeps_its_joined_pads_on_the_pin(self):
        placed = self.candidate(PARTIAL_BOARD, 'partial')
        record, report, summary, result, work = self.route(placed, 'partial-native', 120)
        self.assertIn(record['exit_code'], (0, 2), report['semantics'])
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'))
        self.assertEqual(result['backend']['name'], 'builtin')
        self.assertEqual(result['backend']['commit'], attempt.PIN)
        completion = result['validation']['net_completion']
        self.assertEqual((completion['status'], completion['split_components'], completion['nets_lost']),
                         ('PASS', [], []))
        native = result['validation']['native_terminal_evidence']
        # N joins J1.1-J2.1 with J3.1 open, M is open: two terminal nets, deficit 2, both incomplete.
        self.assertEqual(native['metrics']['reference'],
                         {'terminal_nets': 2, 'incomplete_nets': 2, 'component_deficit': 2})
        for name in ('reference-terminal-components.json', 'candidate-terminal-components.json'):
            self.assertIn(f'attempt/validate/{name}', report['retained'])
        self.assertEqual(result['routing']['stats']['removed_existing_tracks'], 0)
        self.assertEqual(result['copper_ownership']['status'], 'bound')
        self.assertEqual(report['retained']['attempt/validate/copper-ownership.json'],
                         result['copper_ownership']['sha256'])
        # The real retained bytes, edited to report a split joined group, no longer verify.
        path = work / 'routing/attempt/result.json'
        result['validation']['net_completion']['split_components'] = [{'net': 'N', 'pads': ['x']}]
        path.write_text(json.dumps(result))
        retained = json.loads((work / 'routing/adapter-report.json').read_text())
        retained['retained']['attempt/result.json'] = attempt.digest(path)
        attempt.write_report(work / 'routing/adapter-report.json', retained)
        with self.assertRaisesRegex(ValueError, 'native terminal preservation'):
            routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))

    def test_capped_raw_report_never_yields_a_board(self):
        """More open connections than the raw DRC report lists: routing is unknown, never eligible."""
        placed = self.candidate(CAPPED_BOARD, 'capped')
        record, report, summary, result, work = self.route(placed, 'capped-native', 30)
        native = result['validation'].get('native_terminal_evidence') or {}
        metrics = native.get('metrics') or {}
        self.assertEqual(metrics.get('reference', {}).get('terminal_nets'), CAPPED_NETS, result['validation'])
        self.assertEqual(metrics['reference']['component_deficit'], CAPPED_NETS)
        self.assertLess(result['routing'].get('baseline_unconnected_total', CAPPED_NETS), CAPPED_NETS)
        self.assertEqual(result['routing']['status'], 'NOT_RUN', result['routing'])
        self.assertEqual(summary['proposal'], 'none', summary)
        self.assertIsNone(summary['candidate_sha256'])
        self.assertFalse((work / 'routing/candidate').exists())


# Original three-footprint board: net N on J1.1, J2.1 and J3.1 with J1.1-J2.1 already
# joined by one track; net M on J1.2 and J2.2, open.
PARTIAL_BOARD = r'''
import sys, pcbnew as k
b = k.BOARD()
point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
for start, end in [((40, 40), (60, 40)), ((60, 40), (60, 60)), ((60, 60), (40, 60)), ((40, 60), (40, 40))]:
    edge = k.PCB_SHAPE(b)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetLayer(k.Edge_Cuts)
    edge.SetStart(point(*start))
    edge.SetEnd(point(*end))
    edge.SetWidth(k.FromMM(.05))
    b.Add(edge)
nets = {}
for name in ('N', 'M'):
    nets[name] = k.NETINFO_ITEM(b, name)
    b.Add(nets[name])
for reference, x, y, pads in (('J1', 45, 45, ('N', 'M')), ('J2', 55, 45, ('N', 'M')), ('J3', 50, 55, ('N',))):
    fp = k.FOOTPRINT(b)
    b.Add(fp)
    fp.SetReference(reference)
    fp.SetPosition(point(x, y))
    for number, net in enumerate(pads, 1):
        pad = k.PAD(fp)
        fp.Add(pad)
        pad.SetNumber(str(number))
        pad.SetNet(nets[net])
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetSize(point(.6, .6))
        pad.SetPosition(point(x, y + (number - 1) * 3))
track = k.PCB_TRACK(b)
track.SetStart(point(45, 45))
track.SetEnd(point(55, 45))
track.SetWidth(k.FromMM(.25))
track.SetLayer(k.F_Cu)
track.SetNet(nets['N'])
b.Add(track)
k.SaveBoard(sys.argv[1], b)
'''

# Original board with more two-pad nets than KiCad's DRC lists unconnected items; a
# full-height keepout between the pad columns forbids every route.
CAPPED_NETS = 511
CAPPED_BOARD = r'''
import sys, pcbnew as k
NETS = 511
b = k.BOARD()
point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
height = NETS * 0.5 + 4
for start, end in [((0, 0), (30, 0)), ((30, 0), (30, height)), ((30, height), (0, height)), ((0, height), (0, 0))]:
    edge = k.PCB_SHAPE(b)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetLayer(k.Edge_Cuts)
    edge.SetStart(point(*start))
    edge.SetEnd(point(*end))
    edge.SetWidth(k.FromMM(.05))
    b.Add(edge)
for column, x in (('L', 5), ('R', 25)):
    fp = k.FOOTPRINT(b)
    b.Add(fp)
    fp.SetReference('J' + column)
    fp.SetPosition(point(x, 2))
    for index in range(NETS):
        if column == 'L':
            net = k.NETINFO_ITEM(b, f'S{index}')
            b.Add(net)
        else:
            net = b.FindNet(f'S{index}')
        pad = k.PAD(fp)
        fp.Add(pad)
        pad.SetNumber(str(index + 1))
        pad.SetNet(net)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetSize(point(.25, .25))
        pad.SetPosition(point(x, 2 + index * 0.5))
zone = k.ZONE(b)
zone.SetIsRuleArea(True)
zone.SetDoNotAllowTracks(True)
zone.SetDoNotAllowVias(True)
zone.SetDoNotAllowZoneFills(True)
zone.SetDoNotAllowPads(False)
zone.SetDoNotAllowFootprints(False)
copper = k.LSET()
copper.AddLayer(k.F_Cu)
copper.AddLayer(k.B_Cu)
zone.SetLayerSet(copper)
outline = zone.Outline()
outline.NewOutline()
for x, y in ((12, -1), (18, -1), (18, height + 1), (12, height + 1)):
    outline.Append(k.FromMM(x), k.FromMM(y))
b.Add(zone)
k.SaveBoard(sys.argv[1], b)
'''


if __name__ == '__main__':
    unittest.main()
