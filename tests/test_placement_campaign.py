"""AGENTEE-91 placement iterations inside the existing campaign, with real native checks.

The campaign, capture, sandboxed checks (native DRC, AGENTEE-92 placement checks,
stage gates), stage receipts and the pinned AGENTEE-54 placer all run for real.
Only the agent transport is scripted: reviewers report nothing, the adjudicator
accepts every check defect, and the implementation writer applies a scripted pcbnew
edit. These tests establish orchestration and evidence contracts, not live-model
repair quality.

The starting boards are owned variants of the RC control fixture: tracks and zones
removed, every pad made rectangular, with project-local footprint libraries rebuilt
from the stock KiCad footprints at test time so that native DRC compares against the
same rectangular pads. They are not AGENTEE-52 forward-annotated scaffolds. The
unchanged AGENTEE-54 packer supports only rectangular pads; the unmodified RC pads
are used below to show that limitation as an incomplete iteration.
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import placement_adapter as pa
from agent_ee.campaign import run_campaign, verify_campaign
from agent_ee.cli import verify
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
BOARD = 'project/rc_filter.kicad_pcb'
SCHEMATIC = 'project/rc_filter.kicad_sch'
POLICY = 'project/placement-policy.json'
ENVELOPE = 'project/rc.envelope.json'
STOCK = [{'path': '/usr/share/kicad/symbols', 'at': 'stock/symbols',
          'include': ['Device.kicad_sym', 'power.kicad_sym', 'Connector_Generic.kicad_sym']},
         {'path': '/usr/share/kicad/template', 'at': 'stock/template', 'include': ['sym-lib-table', 'fp-lib-table']}]
LIBRARIES = [('Resistor_SMD', 'R_0805_2012Metric'), ('Capacitor_SMD', 'C_0805_2012Metric'),
             ('Connector_PinHeader_2.54mm', 'PinHeader_1x03_P2.54mm_Vertical')]
# Separate interpreter passes: pcbnew 10.0.7 crashes walking a board after Remove().
STRIP = """import sys
import pcbnew as k
board = k.LoadBoard(sys.argv[1])
for item in list(board.GetTracks()) + list(board.Zones()):
    board.Remove(item)
k.SaveBoard(sys.argv[1], board, True)
"""
LIBRARY = """import json, sys
from pathlib import Path
import pcbnew as k
project, items = Path(sys.argv[1]), json.loads(sys.argv[2])
table = ['(fp_lib_table', '  (version 7)']
for lib, name in items:
    fp = k.FootprintLoad(f'/usr/share/kicad/footprints/{lib}.pretty', name)
    for pad in fp.Pads():
        pad.SetShape(k.PAD_SHAPE_RECT)
    out = project / 'lib' / f'{lib}.pretty'
    out.mkdir(parents=True, exist_ok=True)
    k.PCB_IO_MGR.FindPlugin(k.PCB_IO_MGR.KICAD_SEXP).FootprintSave(str(out), fp)
    table.append(f'  (lib (name "{lib}")(type "KiCad")(uri "${{KIPRJMOD}}/lib/{lib}.pretty")(options "")(descr ""))')
(project / 'fp-lib-table').write_text('\\n'.join(table + [')', '']))
"""
# The scripted writer's and fixture's pcbnew edits; saved without touching project settings.
EDIT = """import json, sys
import pcbnew as k
path, edit = sys.argv[1], json.loads(sys.argv[2])
MM = 1000000
board = k.LoadBoard(path)
fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
if edit.get('rect_pads'):
    for fp in fps.values():
        for pad in fp.Pads():
            pad.SetShape(k.PAD_SHAPE_RECT)
for ref, (x, y) in edit.get('move', {}).items():
    fps[ref].SetPosition(k.VECTOR2I(int(round(x * MM)), int(round(y * MM))))
for ref, angle in edit.get('rotate', {}).items():
    fps[ref].SetOrientationDegrees(angle)
for ref in edit.get('flip', []):
    fps[ref].Flip(fps[ref].GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
for ref in edit.get('lock', []):
    fps[ref].SetLocked(True)
for ref in edit.get('unlock', []):
    fps[ref].SetLocked(False)
for ref, value in edit.get('value', {}).items():
    fps[ref].SetValue(value)
if edit.get('outline'):
    edge = next(d for d in board.GetDrawings() if d.GetLayer() == k.Edge_Cuts)
    edge.Move(k.VECTOR2I(MM, 0))
if edit.get('track'):
    track = k.PCB_TRACK(board)
    track.SetStart(k.VECTOR2I(98 * MM, 106 * MM))
    track.SetEnd(k.VECTOR2I(102 * MM, 106 * MM))
    track.SetLayer(k.F_Cu)
    board.Add(track)
k.SaveBoard(path, board, True)
"""


def pcbnew(script, *args):
    process = subprocess.run(['/usr/bin/python3', '-c', script, *map(str, args)],
                             capture_output=True, text=True, timeout=180)
    if process.returncode:
        raise AssertionError(process.stderr[-3000:])


def edit_board(path, **edit):
    pcbnew(EDIT, path, json.dumps(edit))


def references(board):
    return {pa._reference(node): key for key, node in pa.footprints(pa._board(Path(board).read_bytes())).items()}


def placement_check(cid, criterion, **params):
    return {'id': cid, 'kind': 'placement', 'stage': 'placement',
            'params': {'criterion': criterion, 'drc_check': 'drc-placement', **params}}


CHECKS = [{'id': 'drc-placement', 'kind': 'drc', 'stage': 'placement', 'params': {'projection': 'placement-stage/1'}},
          placement_check('courtyard', 'courtyard-overlap/1'),
          placement_check('outline', 'outline-containment/1'),
          placement_check('connector', 'connector-edge/1', envelope=ENVELOPE),
          {'id': 'drc-final', 'kind': 'drc', 'stage': 'routing'}]


class PlacementCampaignFixture(unittest.TestCase):
    """One owned base project per class; each test copies it and runs a real campaign."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-campaign-')
        cls.base = Path(cls.tmp.name) / 'base'
        project = cls.base / 'project'
        project.mkdir(parents=True)
        for name in ('rc_filter.kicad_pcb', 'rc_filter.kicad_sch', 'rc_filter.kicad_pro', 'sym-lib-table'):
            shutil.copy(REPO / 'fixtures/rc/control' / name, project / name)
        pcbnew(STRIP, project / 'rc_filter.kicad_pcb')
        pcbnew(LIBRARY, project, json.dumps(LIBRARIES))
        shutil.copy(REPO / 'fixtures/board-envelope/rc.envelope.json', cls.base / ENVELOPE)
        write_json(cls.base / POLICY, {'schema': 'agent-ee.packing-policy/1', 'step_budget': 100000,
                                       'gap_nm': 500000, 'fixed_refs': [], 'priority_refs': [],
                                       'ground_nets': ['GND'], 'obstacles': []})
        (cls.base / 'requirements').mkdir()
        (cls.base / 'requirements/placement.md').write_text('Owned AGENTEE-91 placement campaign requirements.\n')
        cls.original_pads = cls.base.parent / 'original-pads.kicad_pcb'
        shutil.copy(project / 'rc_filter.kicad_pcb', cls.original_pads)
        edit_board(project / 'rc_filter.kicad_pcb', rect_pads=True)
        for generated in project.glob('*.kicad_prl'):
            generated.unlink()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='agent-ee-placement-campaign-run-')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.source = self.root / 'source'
        shutil.copytree(self.base, self.source)
        self.fixed = references(self.source / BOARD)
        self.writes = {}
        self.prompts = {}
        self.calls = []
        self.writer_failure = False
        self.addCleanup(patch.stopall)
        patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        # Avoid platform fingerprint probes for the scripted agent launch; sandbox tool execution is real.
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3', 'kicad-cli')}).start()

    def declaration(self, helper=True, fixed=None, timeout_s=300):
        return {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1' if helper else None,
                'policy': POLICY if helper else None,
                'fixed_footprints': [self.fixed['J1']] if fixed is None else fixed, 'timeout_s': timeout_s}

    def scope(self, checks=CHECKS):
        specs = [dict(c) for c in checks]
        requirements = [{'id': f"R-{c['id']}", 'statement': f"Owned requirement for {c['id']}",
                         'source': {'path': 'requirements/placement.md', 'locator': c['id']},
                         'checks': [c['id']], 'intent': 'acceptance'} for c in specs]
        return {'schema': 'agent-ee.scope/3', 'name': 'placement-campaign', 'scope': 'owned RC placement campaign',
                'board': BOARD, 'schematic': SCHEMATIC,
                'inputs': [{'path': str(self.source / 'project'), 'at': 'project'},
                           {'path': str(self.source / 'requirements'), 'at': 'requirements'}, *STOCK],
                'requirements': ['requirements/placement.md'], 'mutable_inputs': [BOARD, SCHEMATIC],
                'checks': specs,
                'stages': {'schema': 'agent-ee.stages/1', 'order': ['placement', 'routing'],
                           'subjects': {'placement': {'path': BOARD, 'kind': 'footprints',
                                                      'references': ['C1', 'J1', 'R1']}},
                           'finding_retry_limit': 4},
                'baseline': {'requirements': requirements, 'targets': ['requirements/placement.md'],
                             'reference_architectures': ['requirements/placement.md']}}

    def campaign(self, declaration=None, iterations=3, steps=None, scope=None, name='campaign'):
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 600}
        profile = {'schema': 'agent-ee.profile/1', 'name': 'placement-campaign-test',
                   'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
                   'adjudicator': definition}
        write_json(self.root / 'scope.json', scope or self.scope())
        config = {'schema': 'agent-ee.campaign/1', 'scope': 'scope.json', 'review_profile': profile,
                  'implementation': definition, 'max_iterations': iterations, 'max_seconds': 3600}
        if declaration is not None:
            config['placement_adapter'] = declaration
        if steps is not None:
            config['implementation_steps'] = steps
        write_json(self.root / 'campaign.json', config)
        self.out = self.root / name
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', self.out)
        return status, read_json(self.out / 'state.json')

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        self.calls.append(label)
        if label == 'implementation':
            number = int(run.name)
            self.prompts[number] = prompt
            if self.writer_failure:
                return {'execution': 'failed', 'error': 'scripted writer failure'}, None
            edit = self.writes.get(number)
            if edit:
                edit_board(run / 'review-work/implementation/design' / BOARD, **edit)
            return {'execution': 'ok'}, {'summary': 'Scripted placement edit; not evidence'}
        packet = read_json(run / 'packet.json')
        identity = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            checks = [read_json(p) for p in (run / 'checks').glob('*.json')]
            decisions = [{'finding_id': 'check/' + c['id'], 'disposition': 'accepted', 'reason': 'check defect',
                          'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome'}]}
                         for c in checks if c['outcome'] == 'defect']
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/1', **identity, 'decisions': decisions}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/1', **identity,
            'packet_sha256': packet['packet_sha256'], 'role': label,
            'coverage': ['placement protocol'], 'limitations': ['scripted test response'], 'findings': []}

    def outcomes(self, number):
        result = verify(self.out / 'runs' / str(number))
        return {c['id']: c['outcome'] for c in result['checks']}

    def assertNoNextCandidate(self, state, error):
        self.assertEqual(state['state'], 'incomplete', state)
        self.assertIn(error, state['error'])
        self.assertEqual(len(state['history']), 1)
        self.assertFalse((self.out / 'runs/2').exists())
        self.assertFalse((self.out / 'implementations/1/proposal.scope.json').exists())
        self.assertEqual(verify_campaign(self.out)['state'], 'incomplete')


class HelperCampaignTest(PlacementCampaignFixture):
    def test_pinned_helper_repairs_placement_then_routing_turn_runs_no_helper(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        status, state = self.campaign(self.declaration())
        self.assertEqual(status, 1, state)
        # Placement passes after the helper; the unrouted board then fails only the routing stage.
        self.assertEqual((state['state'], state['reason']), ('escalated', 'repeated_design'), state)
        first, second = state['history'][:2]
        self.assertEqual(first['earliest_failing_stage'], 'placement')
        self.assertEqual(first['stage_failures']['placement'], ['courtyard', 'drc-placement'])
        self.assertEqual(first['blocked_checks'], ['drc-final'])
        self.assertEqual(second['earliest_failing_stage'], 'routing')
        self.assertEqual(second['stage_failures'], {'placement': [], 'routing': ['drc-final']})
        self.assertEqual(self.outcomes(2), {'drc-placement': 'pass', 'courtyard': 'pass', 'outline': 'pass',
                                            'connector': 'pass', 'drc-final': 'defect'})
        work = self.out / 'implementations/1'
        record = read_json(work / 'placement-adapter.json')
        self.assertEqual((record['status'], record['helper']['status']), ('proposed', 'completed'))
        self.assertEqual(record['writer_scope']['transforms'], [])
        self.assertEqual(record['protected_refs'], ['J1'])
        self.assertEqual(sorted(t['reference'] for t in record['proposal_scope']['transforms']), ['C1', 'R1'])
        self.assertEqual(state['placement_records'], {'1': digest(work / 'placement-adapter.json')})
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual([(r['path'], r['applicability']) for r in index['receipts']],
                         [('receipts/1-agent-ee-placer/receipt.json', 'applicable')])
        # The fresh captured candidate is exactly the adopted helper output, graded by fresh checks.
        proposal = read_json(self.out / 'runs/2/candidate.json')
        receipt = read_json(work / 'receipts/1-agent-ee-placer/receipt.json')
        self.assertEqual(proposal['files'][BOARD], receipt['outputs'][BOARD])
        judgment = read_json(self.out / 'runs/2/work/courtyard/placement-judgment.json')
        self.assertEqual((judgment['candidate_id'], judgment['board_sha256']),
                         (proposal['candidate_id'], proposal['files'][BOARD]))
        drc = read_json(self.out / 'runs/2/work/drc-placement/drc.json')
        self.assertEqual((drc['violations'], drc['schematic_parity']), ([], []))
        self.assertTrue(drc['unconnected_items'])
        # Only the placement turn ran the helper or saw placement context.
        self.assertIn('This is a placement iteration', self.prompts[1])
        self.assertIn('"routing_feedback": {"status": "unavailable"', self.prompts[1])
        self.assertNotIn('placement iteration', self.prompts[2])
        self.assertFalse((self.out / 'implementations/2/placement-adapter.json').exists())
        self.assertFalse((self.out / 'implementations/2/receipts').exists())
        self.assertEqual(read_json(self.out / 'implementations/2/stage-receipts.json')['receipts'], [])
        self.assertEqual(verify_campaign(self.out)['state'], 'escalated')
        self.assertEqual((self.source / BOARD).read_bytes(),
                         (self.out / 'runs/1/inputs' / BOARD).read_bytes())


class WriterOnlyCampaignTest(PlacementCampaignFixture):
    def test_two_writer_repairs_each_graded_by_fresh_native_checks(self):
        edit_board(self.source / BOARD, move={'C1': (115.5, 100), 'R1': (114.5, 100)})
        self.writes = {1: {'move': {'C1': (112, 100), 'R1': (111, 100)}},
                       2: {'move': {'C1': (110, 100), 'R1': (105, 100)}, 'rotate': {'R1': 90}}}
        status, state = self.campaign(self.declaration(helper=False))
        self.assertEqual(status, 1, state)
        self.assertEqual((state['state'], state['reason']), ('escalated', 'iteration_limit'), state)
        self.assertEqual([h['earliest_failing_stage'] for h in state['history']], ['placement', 'placement', 'routing'])
        self.assertEqual([h['stage_failures']['placement'] for h in state['history']],
                         [['courtyard', 'drc-placement', 'outline'], ['courtyard', 'drc-placement'], []])
        self.assertEqual(self.outcomes(2)['outline'], 'pass')
        self.assertEqual(self.outcomes(2)['courtyard'], 'defect')
        self.assertEqual(self.outcomes(3)['courtyard'], 'pass')
        # The second writer turn received the fresh second-run findings, not the first run's.
        context = json.loads(self.prompts[2].rsplit('\n', 1)[1])
        self.assertEqual(sorted(f['local_id'] for f in context['placement']['findings']), ['courtyard', 'drc-placement'])
        candidate = read_json(self.out / 'runs/2/candidate.json')['candidate_id']
        self.assertTrue(all(c['details'].get('candidate_id', candidate) == candidate
                            for c in context['placement']['checks'] if c['kind'] == 'placement'))
        for number in (1, 2):
            record = read_json(self.out / f'implementations/{number}/placement-adapter.json')
            self.assertEqual((record['status'], record['helper']), ('proposed', None))
            self.assertEqual(record['writer_scope'], record['proposal_scope'])
        rotated = read_json(self.out / 'implementations/2/placement-adapter.json')['proposal_scope']['transforms']
        self.assertIn(('R1', 90), [(t['reference'], t['rotation_deg']) for t in rotated])
        self.assertEqual(verify_campaign(self.out)['state'], 'escalated')


class SideFlipCampaignTest(PlacementCampaignFixture):
    def test_writer_side_flip_is_captured_and_graded_by_fresh_checks(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        self.writes = {1: {'flip': ['R1'], 'move': {'R1': (105, 100)}}}
        status, state = self.campaign(self.declaration(helper=False), iterations=2)
        self.assertEqual(status, 1, state)
        self.assertEqual([h['earliest_failing_stage'] for h in state['history']], ['placement', 'routing'])
        self.assertEqual(self.outcomes(2)['courtyard'], 'pass')
        transform, = read_json(self.out / 'implementations/1/placement-adapter.json')['proposal_scope']['transforms']
        self.assertEqual((transform['reference'], transform['flipped'], transform['side'], transform['rotation_deg']),
                         ('R1', True, 'back', 180))
        facts = read_json(self.out / 'runs/2/work/courtyard/placement-probe.json')
        self.assertEqual(facts['board_sha256'], read_json(self.out / 'runs/2/candidate.json')['files'][BOARD])
        self.assertEqual(verify_campaign(self.out)['state'], 'escalated')


class RefusalCampaignTest(PlacementCampaignFixture):
    """Out-of-scope writer proposals refuse the iteration before any helper runs."""

    def refused(self, edit, fragment, declaration=None, before=None, schematic=False):
        edit_board(self.source / BOARD, move={'R1': (109, 100)}, **(before or {}))
        self.writes = {1: edit}
        if schematic:
            original = self.agent
            def writer(run, label, *args, **kwargs):
                reply = original(run, label, *args, **kwargs)
                if label == 'implementation':
                    path = Path(run) / 'review-work/implementation/design' / SCHEMATIC
                    path.write_bytes(path.read_bytes() + b'\n')
                return reply
            patch('agent_ee.reviewers.dispatch', side_effect=writer).start()
        status, state = self.campaign(declaration or self.declaration())
        self.assertEqual(status, 1, state)
        self.assertNoNextCandidate(state, fragment)
        work = self.out / 'implementations/1'
        record = read_json(work / 'placement-adapter.json')
        self.assertEqual((record['status'], record['helper'], record['proposal_scope']), ('refused', None, None))
        self.assertEqual(record['writer_scope']['status'], 'refused')
        self.assertFalse((work / 'receipts').exists())
        self.assertEqual(read_json(work / 'stage-receipts.json')['receipts'], [])
        self.assertEqual(state['placement_records'], {'1': digest(work / 'placement-adapter.json')})
        self.assertEqual((self.source / BOARD).read_bytes(), (self.out / 'runs/1/inputs' / BOARD).read_bytes())
        return record

    def test_outline_move(self):
        self.refused({'outline': True}, 'board: removed or changed gr_line')

    def test_copper_added(self):
        self.refused({'track': True}, 'board: added or changed segment')

    def test_field_value_changed(self):
        self.refused({'value': {'R1': '22k'}}, 'changed beyond the footprint transform')

    def test_envelope_fixed_connector_with_an_incomplete_declared_list(self):
        # No UUID is declared: J1 is protected only because the captured envelope fixes it.
        record = self.refused({'move': {'J1': (101, 100)}}, 'protected footprint transform changed',
                              declaration=self.declaration(fixed=[]))
        self.assertEqual(record['protected'], [self.fixed['J1']])

    def test_lock_removed_before_move(self):
        self.refused({'unlock': ['C1'], 'move': {'C1': (112, 103)}}, 'protected footprint transform changed',
                     before={'lock': ['C1']})

    def test_schematic_change_despite_file_level_mutability(self):
        self.refused({}, 'non-board file changed: ' + SCHEMATIC, schematic=True)

    def test_tiny_angle(self):
        self.refused({'rotate': {'R1': 1e-29}}, 'is not a quarter turn')

    def test_side_flip_with_a_non_quarter_turn(self):
        self.refused({'flip': ['R1'], 'rotate': {'R1': 135}}, 'after the side flip is not a quarter turn')


class BindingAdmissionTest(PlacementCampaignFixture):
    def test_envelope_reference_missing_from_the_board_refuses_binding_before_any_writer(self):
        envelope = read_json(self.source / ENVELOPE)
        envelope['fixed_footprints'][0]['reference'] = 'J9'
        write_json(self.source / ENVELOPE, envelope)
        status, state = self.campaign(self.declaration())
        self.assertEqual(status, 1)
        self.assertIn("fixed reference 'J9' does not name exactly one source footprint", state['error'])
        self.assertNotIn('implementation', self.calls)
        self.assertFalse((self.out / 'implementations').exists())

    def test_envelope_or_policy_declared_mutable_is_refused(self):
        # The placement check's envelope is already a frozen baseline dependency.
        for name, mutable, error in (
                ('envelope', ENVELOPE, 'mutable_inputs must name captured design files outside the immutable baseline'),
                ('policy', POLICY, 'placement policy must be a captured immutable input')):
            with self.subTest(name):
                self.calls.clear()
                scope = self.scope()
                scope['mutable_inputs'] = [BOARD, mutable]
                status, state = self.campaign(self.declaration(), scope=scope, name=f'campaign-{name}')
                self.assertEqual(status, 1)
                self.assertIn(error, state['error'])
                self.assertNotIn('implementation', self.calls)


class FailureCampaignTest(PlacementCampaignFixture):
    """Helper and writer failures leave the iteration incomplete and publicly verifiable."""

    def failed(self, fragment, declaration=None, receipt_status=None):
        status, state = self.campaign(declaration or self.declaration())
        self.assertEqual(status, 1, state)
        self.assertNoNextCandidate(state, fragment)
        work = self.out / 'implementations/1'
        if not self.writes:
            # Nothing was adopted into the scratch design and no default placement was substituted.
            self.assertEqual((work / 'review-work/implementation/design' / BOARD).read_bytes(),
                             (self.out / 'runs/1/inputs' / BOARD).read_bytes())
        if receipt_status is None:
            return state, None
        record = read_json(work / 'placement-adapter.json')
        self.assertIn(record['status'], ('incomplete', 'cancelled'))
        self.assertEqual(record['helper']['status'], receipt_status)
        receipt = read_json(work / record['helper']['receipt'] / 'receipt.json')
        self.assertEqual(receipt['status'], receipt_status)
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual(state['stage_receipts']['1'], digest(work / 'stage-receipts.json'))
        self.assertEqual([r['applicability'] for r in index['receipts']], ['not_applicable'])
        self.assertEqual(state['placement_records']['1'], digest(work / 'placement-adapter.json'))
        return state, record

    def test_helper_typed_failure(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        write_json(self.source / POLICY, {**read_json(self.source / POLICY), 'step_budget': 1})
        _, record = self.failed('placement helper failed', receipt_status='failed')
        result = self.out / 'implementations/1' / record['helper']['receipt'] / 'run/placement/placement-result.json'
        self.assertEqual(read_json(result)['status'], 'packing_budget_exhausted')

    def test_helper_crash(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        write_json(self.source / POLICY, {**read_json(self.source / POLICY), 'gap_nm': 'wide'})
        _, record = self.failed('placement helper failed', receipt_status='failed')
        run = self.out / 'implementations/1' / record['helper']['receipt'] / 'run'
        self.assertEqual(read_json(run / 'placement/driver.json')['status'], 'error')
        self.assertFalse((run / 'placement/placement-result.json').exists())

    def test_unsupported_rc_pads_are_not_packed_and_nothing_is_substituted(self):
        # The actual RC roundrect and circle pads: the unchanged AGENTEE-54 packer refuses them.
        shutil.copy(self.original_pads, self.source / BOARD)
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        _, record = self.failed('placement helper failed', receipt_status='failed')
        result = self.out / 'implementations/1' / record['helper']['receipt'] / 'run/placement/placement-result.json'
        self.assertEqual(read_json(result)['status'], 'unsupported_geometry')

    def test_helper_timeout(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        self.failed('placement helper timeout', self.declaration(timeout_s=1), receipt_status='timeout')

    def test_helper_cancellation(self):
        import _thread
        import threading
        from agent_ee import stage_receipts
        real = stage_receipts.execute
        def interrupted(inputs, work, argv, **kwargs):
            # A real interrupt of the coordinator while the sandboxed helper process itself runs.
            if argv[:4] != ['/usr/bin/python3', '-B', '-I', f'{pa.TOOL_AT}/launch.py']:
                return real(inputs, work, argv, **kwargs)
            timer = threading.Timer(0.3, _thread.interrupt_main)
            timer.start()
            try:
                return real(inputs, work, argv, **kwargs)
            finally:
                timer.cancel()
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        patch('agent_ee.stage_receipts.execute', side_effect=interrupted).start()
        state, record = self.failed('KeyboardInterrupt', receipt_status='cancelled')
        self.assertEqual((record['status'], record['error']), ('cancelled', 'KeyboardInterrupt'))

    def test_writer_failure_runs_no_placement_step(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        self.writer_failure = True
        state, _ = self.failed('implementation execution failed')
        self.assertFalse((self.out / 'implementations/1/placement-adapter.json').exists())
        self.assertNotIn('placement_records', state)

    def test_record_write_failure_keeps_the_original_refusal(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        self.writes = {1: {'move': {'J1': (101, 100)}}}
        real = pa.write_json
        def failing(path, value):
            if Path(path).name == 'placement-adapter.json':
                raise OSError('scripted record write failure')
            return real(path, value)
        patch('agent_ee.placement_adapter.write_json', side_effect=failing).start()
        state, _ = self.failed('protected footprint transform changed')
        self.assertFalse((self.out / 'implementations/1/placement-adapter.json').exists())
        self.assertEqual(state['placement_records'], {'1': None})


class TamperCampaignTest(PlacementCampaignFixture):
    """Public verify_campaign refuses edited placement provenance after a real helper iteration."""

    def test_tampering_is_refused_by_public_verification(self):
        edit_board(self.source / BOARD, move={'R1': (109, 100)})
        status, state = self.campaign(self.declaration())
        self.assertEqual(state['state'], 'escalated', state)
        verify_campaign(self.out)
        work = self.out / 'implementations/1'
        record_path, state_path = work / 'placement-adapter.json', self.out / 'state.json'
        record, saved_state = read_json(record_path), read_json(state_path)

        def reanchored(changes):
            write_json(record_path, {**record, **changes})
            write_json(state_path, {**saved_state, 'placement_records': {'1': digest(record_path)}})

        refused = pa.proposal_scope(self.out / 'runs/1/inputs', self.out / 'runs/1/inputs', BOARD, [])
        refused.update(status='refused', violations=['forged'])
        receipt = work / 'receipts/1-agent-ee-placer'
        request = work / 'placement/1/request/request.json'
        output = receipt / 'run' / pa.OUTPUT
        config = self.out / 'config.json'
        cases = {
            'record removed': (lambda: record_path.unlink(), 'differs from its campaign anchor'),
            'record rewritten': (lambda: write_json(record_path, {**record, 'error': None, 'protected': []}),
                                 'differs from its campaign anchor'),
            'forged proposed with refused scope': (lambda: reanchored({'proposal_scope': refused}),
                                                   'differs from retained receipts or captured proposal'),
            'other source candidate': (lambda: reanchored({'source_candidate_id': '0' * 64}),
                                       'differs from retained receipts or captured proposal'),
            'request rewritten': (lambda: write_json(request, {**read_json(request), 'policy': {
                **read_json(request)['policy'], 'fixed_refs': []}}), 'request differs from the coordinator derivation'),
            'helper output rewritten': (lambda: output.write_bytes(output.read_bytes() + b'\n'),
                                        'stage receipt outputs changed'),
            'extra helper receipt': (lambda: shutil.copytree(receipt, work / 'receipts/2-agent-ee-placer'),
                                     'stage receipt'),
            'helper receipt removed': (lambda: shutil.rmtree(receipt), 'placement helper receipt missing'),
            'configuration changed': (lambda: write_json(config, {**read_json(config), 'max_iterations': 4}),
                                      'campaign configuration changed'),
            'binding drops the envelope': (lambda: write_json(state_path, {**saved_state, 'placement_binding': {
                **saved_state['placement_binding'], 'envelopes': []}}), 'placement binding differs'),
            'record on the routing turn': (lambda: shutil.copy(record_path, self.out / 'implementations/2/placement-adapter.json'),
                                           'placement record on an implementation without a placement step'),
            'anchor on the routing turn': (lambda: write_json(state_path, {**saved_state, 'placement_records': {
                **saved_state['placement_records'], '2': None}}), 'without a placement step'),
            'history stage relabelled': (lambda: write_json(state_path, {**saved_state, 'history': [
                {**saved_state['history'][0], 'earliest_failing_stage': 'routing'}, *saved_state['history'][1:]]}),
                'campaign history projection differs from evidence'),
            'legacy plan claimed': (lambda: write_json(state_path, {k: v for k, v in saved_state.items()
                                                                    if k != 'step_plan'}),
                                    'placement adapter requires a campaign step plan'),
        }
        snapshot = self.root / 'snapshot'
        shutil.copytree(work, snapshot / 'work', symlinks=True)
        shutil.copytree(self.out / 'implementations/2', snapshot / 'two', symlinks=True)
        config_bytes = config.read_bytes()
        for name, (tamper, fragment) in cases.items():
            with self.subTest(name):
                tamper()
                with self.assertRaisesRegex(ValueError, fragment):
                    verify_campaign(self.out)
            shutil.rmtree(work)
            shutil.copytree(snapshot / 'work', work, symlinks=True)
            shutil.rmtree(self.out / 'implementations/2')
            shutil.copytree(snapshot / 'two', self.out / 'implementations/2', symlinks=True)
            config.write_bytes(config_bytes)
            write_json(state_path, saved_state)
            self.assertEqual(verify_campaign(self.out)['state'], 'escalated')


class AdmissionTest(unittest.TestCase):
    """Configuration admission happens before the campaign directory or any provider call exists."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10}
        self.config = {'schema': 'agent-ee.campaign/1', 'scope': 'scope.json', 'max_iterations': 2, 'max_seconds': 60,
                       'review_profile': {'schema': 'agent-ee.profile/1', 'name': 'x',
                                          'reviewers': [{**definition, 'role': 'circuit'},
                                                        {**definition, 'role': 'physical'}],
                                          'adjudicator': definition},
                       'implementation': definition,
                       'placement_adapter': {'schema': pa.ADAPTER_SCHEMA, 'helper': None, 'policy': None,
                                             'fixed_footprints': [], 'timeout_s': 60}}
        self.stages = {'schema': 'agent-ee.stages/1', 'order': ['placement', 'routing'], 'subjects': {},
                       'finding_retry_limit': 2}

    def refused(self, fragment, scope, **changes):
        write_json(self.root / 'scope.json', scope)
        write_json(self.root / 'campaign.json', {**self.config, **changes})
        with self.assertRaisesRegex(ValueError, fragment):
            run_campaign(self.root / 'campaign.json', self.root / 'campaign')
        self.assertFalse((self.root / 'campaign').exists())

    def test_adapter_needs_a_staged_placement_scope_and_a_valid_declaration(self):
        self.refused('requires a staged scope with a placement stage', {'schema': 'agent-ee.scope/2'})
        self.refused('requires a staged scope with a placement stage',
                     {'schema': 'agent-ee.scope/2', 'stages': {**self.stages, 'order': ['routing']}})
        self.refused('expected agent-ee.placement-adapter/1', {'schema': 'agent-ee.scope/2', 'stages': self.stages},
                     placement_adapter={**self.config['placement_adapter'], 'default_placement': 'grid'})

    def test_adapter_and_placement_steps_are_never_combined(self):
        step = {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': 'other-placer',
                'argv': ['/usr/bin/true'], 'writes': {'board': 'design/board'}}
        self.refused('cannot be combined with placement implementation_steps',
                     {'schema': 'agent-ee.scope/2', 'stages': self.stages}, implementation_steps=[step])


class StagedStepPlanTest(unittest.TestCase):
    """Generic steps in a staged campaign run only on their own stage's turns."""

    def test_only_earliest_stage_steps_run_and_the_plan_is_reverified(self):
        import copy
        import test_campaign
        fixture = test_campaign.CampaignIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        require_toolchain(native=True)
        fixture.scope['schematic'] = 'design/design.kicad_sch'
        fixture.scope['checks'][0]['stage'] = 'schematic'
        route = copy.deepcopy(fixture.scope['checks'][0])
        route.update(id='route', stage='routing')
        fixture.scope['checks'].append(route)
        fixture.scope['baseline']['requirements'][0]['checks'].append('route')
        fixture.scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['schematic', 'routing'],
            'subjects': {'schematic': {'path': 'design/design.kicad_sch', 'kind': 'symbols', 'references': ['R1']}},
            'finding_retry_limit': 3}
        (fixture.root / 'source/design.kicad_sch').write_bytes((REPO / 'fixtures/stages/schematic.kicad_sch').read_bytes())
        write_json(fixture.root / 'scope.json', fixture.scope)
        tool = fixture.root / 'tool'
        tool.mkdir()
        (tool / 'step.py').write_text('import json, sys\nfrom pathlib import Path\n'
                                      'Path("/work/design/design/design.json").write_text(json.dumps({"value": 5}))\n'
                                      'raise SystemExit(int(sys.argv[1]))\n')
        def step(stage, code):
            return {'schema': 'agent-ee.stage-step/1', 'stage': stage, 'step': f'{stage}-step',
                    'argv': ['/usr/bin/python3', '/tool/step.py', str(code)],
                    'mounts': [{'source': str(tool), 'at': '/tool'}], 'source': '/tool',
                    'writes': {'design/design.json': 'design/design/design.json'}, 'timeout_s': 20}
        config = read_json(fixture.root / 'campaign.json')
        # The routing step would fail if it ran during the schematic turn.
        write_json(fixture.root / 'campaign.json', {**config, 'implementation_steps': [step('routing', 3),
                                                                                       step('schematic', 0)]})
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(fixture.root / 'campaign.json', fixture.root / 'campaign')
        root = fixture.root / 'campaign'
        state = verify_campaign(root)
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state)
        index = read_json(root / 'implementations/1/stage-receipts.json')
        self.assertEqual([r['path'] for r in index['receipts']], ['receipts/1-schematic-step/receipt.json'])
        # Read as a legacy campaign, the same records would need every declared step.
        stored = read_json(root / 'state.json')
        write_json(root / 'state.json', {k: v for k, v in stored.items() if k != 'step_plan'})
        with self.assertRaisesRegex(ValueError, 'stage receipt step differs from the campaign declaration'):
            verify_campaign(root)


if __name__ == '__main__':
    unittest.main()
