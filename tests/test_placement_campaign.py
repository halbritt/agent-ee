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


if __name__ == '__main__':
    unittest.main()
