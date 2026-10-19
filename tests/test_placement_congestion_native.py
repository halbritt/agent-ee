"""AGENTEE-57 congestion check on owned RC variants: real pcbnew saves, sandboxed probe, native DRC.

Each variant edits a copy of the captured RC fixture through pcbnew in a fresh
process, is captured as a scope/3 candidate and runs through
``checks.run_checks``. Native DRC is the independent courtyard authority the
suggested move is checked against.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from toolchain import require_toolchain

from agent_ee import candidate, checks
from agent_ee.placement_congestion import _turn
from agent_ee.placement_probe import extract

REPO = Path(__file__).resolve().parents[1]
BOARD = 'project/rc_filter.kicad_pcb'
MM = 1_000_000
STOCK = [{'path': '/usr/share/kicad/symbols', 'at': 'stock/symbols',
          'include': ['Device.kicad_sym', 'power.kicad_sym', 'Connector_Generic.kicad_sym']},
         {'path': '/usr/share/kicad/footprints', 'at': 'stock/footprints',
          'include': ['Resistor_SMD.pretty/*', 'Capacitor_SMD.pretty/*', 'Connector_PinHeader_2.54mm.pretty/*']},
         {'path': '/usr/share/kicad/template', 'at': 'stock/template', 'include': ['sym-lib-table', 'fp-lib-table']}]
PARAMS = {'clearance_mm': 0.2, 'track_width_mm': 0.25, 'cell_mm': 2.54, 'routing_layers': ['F.Cu', 'B.Cu']}
# Separate interpreter passes: pcbnew 10.0.7 misbehaves walking a board after Remove().
UNROUTE = """import sys
import pcbnew as k
board = k.LoadBoard(sys.argv[1])
for track in list(board.GetTracks()):
    board.Remove(track)
k.SaveBoard(sys.argv[1], board)
"""
EDIT = """import json, sys
import pcbnew as k
path, edit = sys.argv[1], json.loads(sys.argv[2])
board = k.LoadBoard(path)
for fp in board.GetFootprints():
    ref = fp.GetReference()
    if ref in edit.get('move', {}):
        x, y = edit['move'][ref]
        fp.SetPosition(k.VECTOR2I(int(round(x * 1000000)), int(round(y * 1000000))))
    if ref in edit.get('rotate', {}):
        fp.SetOrientationDegrees(float(edit['rotate'][ref]))
k.SaveBoard(path, board)
"""
# Fresh interpreter: the saved bytes, not the editing session, carry the pad layers.
LAYERS = """import json, sys
import pcbnew as k
board = k.LoadBoard(sys.argv[1])
print(json.dumps(sorted([fp.GetReference(), p.GetNumber(), sorted(k.LayerName(l) for l in p.GetLayerSet().Seq())]
                        for fp in board.GetFootprints() for p in fp.Pads())))
"""


class NativeCongestion(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-congestion-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0

    def python(self, script, *args):
        process = subprocess.run(['/usr/bin/python3', '-c', script, *args], capture_output=True, text=True,
                                 timeout=120)
        self.assertEqual(process.returncode, 0, process.stderr[-2000:])
        return process.stdout

    def source(self, **edit):
        self.count += 1
        source = self.root / f'source-{self.count}'
        (source / 'project').mkdir(parents=True)
        for name in ('rc_filter.kicad_pcb', 'rc_filter.kicad_sch', 'rc_filter.kicad_pro', 'fp-lib-table',
                     'sym-lib-table'):
            shutil.copy(REPO / 'fixtures/rc/control' / name, source / 'project' / name)
        self.python(UNROUTE, str(source / BOARD))
        self.python(EDIT, str(source / BOARD), json.dumps(edit))
        (source / 'requirements').mkdir()
        (source / 'requirements/placement.md').write_text('Owned AGENTEE-57 placement requirements.\n')
        return source

    def run_scope(self, source, params=None):
        specs = [{'id': 'drc', 'kind': 'drc'},
                 {'id': 'congestion', 'kind': 'placement_congestion', 'params': params or PARAMS}]
        requirements = [{'id': 'R-drc', 'intent': 'acceptance', 'statement': 'Native DRC',
                         'source': {'path': 'requirements/placement.md', 'locator': 'drc'}, 'checks': ['drc']},
                        {'id': 'I-congestion', 'intent': 'informational', 'statement': 'Pre-route report',
                         'source': {'path': 'requirements/placement.md', 'locator': 'congestion'},
                         'checks': ['congestion']}]
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'project/rc_filter.kicad_sch', 'board': BOARD,
                 'inputs': [{'path': str(source / 'project'), 'at': 'project'},
                            {'path': str(source / 'requirements'), 'at': 'requirements'}, *STOCK],
                 'requirements': ['requirements/placement.md'], 'checks': specs,
                 'baseline': {'requirements': requirements, 'targets': ['requirements/placement.md'],
                              'reference_architectures': ['requirements/placement.md']}}
        self.count += 1
        path = self.root / f'scope-{self.count}.json'
        path.write_text(json.dumps(scope))
        run = self.root / f'run-{self.count}'
        run.mkdir()
        candidate.capture(path, run)
        return run, {r['id']: r for r in checks.run_checks(run, time.monotonic() + 900)}

    def native_types(self, run):
        return {v['type'] for v in json.loads((run / 'work/drc/drc.json').read_text())['violations']}

    def test_overlapping_pads_defect_move_clears_native_courtyards_and_output_is_repeatable(self):
        # C1 at x = 106.5 mm: its pad 1 overlaps R1 pad 2 by 0.65 mm on F.Cu.
        source = self.source(move={'C1': [106.5, 100]})
        run, results = self.run_scope(source)
        result = results['congestion']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('courtyards_overlap', self.native_types(run))
        issue = result['details']['issues'][0]
        self.assertEqual((issue['rank'], issue['kind'], issue['refs']), (1, 'pad_overlap', ['C1', 'R1']))
        self.assertEqual(issue['clearance_mm'], -0.65)
        pair, = issue['pad_pairs']
        self.assertEqual((pair['pads'], pair['clearance_bounds_mm']), ([['C1', '1'], ['R1', '2']], [-0.65, -0.65]))
        move = issue['suggested_move']
        self.assertRegex(move['text'], r'^move (C1|R1) [0-9.]+ mm (up|down|left|right)$')
        finding = next(f for f in result['details']['gate_findings'] if f['kind'] == 'pad_overlap')
        self.assertEqual((finding['entity'], finding['suggested_fix']['op']), ('C1+R1', 'move'))
        self.assertEqual(finding['suggested_fix']['params']['reference'], move['reference'])
        self.assertIn('work/congestion/congestion.json', result['evidence'])
        self.assertIn('work/congestion/placement-probe.json', result['evidence'])
        self.assertEqual(result['details']['tool']['argv'][:4], ['/usr/bin/python3', '-B', '-m',
                                                                 'agent_ee.placement_probe'])
        # The same saved board gives byte-identical ordered output.
        again, repeat = self.run_scope(source)
        self.assertEqual((run / 'work/congestion/congestion.json').read_bytes(),
                         (again / 'work/congestion/congestion.json').read_bytes())
        self.assertEqual(repeat['congestion']['details']['issues'], result['details']['issues'])
        # Apply the suggestion with pcbnew in a fresh process; pad layers survive save and reload.
        layers = self.python(LAYERS, str(source / BOARD))
        moved = self.source(move={'C1': [106.5, 100], move['reference']: [move['x_mm'], move['y_mm']]})
        self.assertEqual(self.python(LAYERS, str(moved / BOARD)), layers)
        fixed, after = self.run_scope(moved)
        self.assertNotIn('courtyards_overlap', self.native_types(fixed))
        self.assertEqual(after['congestion']['outcome'], 'info')
        self.assertFalse([i for i in after['congestion']['details']['issues']
                          if i['kind'] in ('pad_overlap', 'pad_clearance', 'courtyard_overlap')])

    def test_clean_placement_is_info_never_pass(self):
        run, results = self.run_scope(self.source())
        result = results['congestion']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'), result)
        self.assertEqual(result['details']['issues'], [])
        self.assertEqual(result['details']['regions'], [])
        report = json.loads((run / 'work/congestion/congestion.json').read_text())
        self.assertEqual(report['congestion']['nets'], 3)

    def test_probe_without_outputs_is_unknown(self):
        source = self.source()
        (source / BOARD).write_text('(kicad_pcb (version 20240108) (generator owned) (unterminated\n')
        run, results = self.run_scope(source)
        result = results['congestion']
        self.assertNotEqual(result['execution'], 'ok')
        self.assertEqual(result['outcome'], 'unknown')
        self.assertFalse((run / 'work/congestion/congestion.json').exists())
        self.assertNotIn('issues', result['details'])

    def test_quarter_turns_match_pcbnew_and_rotation_suggestion_applies(self):
        control = extract(self.source() / BOARD)
        r1 = next(f for f in control['footprints'] if f['ref'] == 'R1')
        for turn in (90, 180, 270):
            with self.subTest(turn=turn):
                saved = extract(self.source(rotate={'R1': turn}) / BOARD)
                rotated = next(f for f in saved['footprints'] if f['ref'] == 'R1')
                about = r1['position_nm']
                predicted = []
                for pad in r1['pads']:
                    dx, dy = _turn((pad['position_nm'][0] - about[0], pad['position_nm'][1] - about[1]), turn)
                    predicted.append([pad['number'], [about[0] + int(dx), about[1] + int(dy)]])
                self.assertEqual(predicted, [[p['number'], p['position_nm']] for p in rotated['pads']])
        # R1 turned 180 degrees puts /IN away from J1; the report proposes turning it back.
        run, results = self.run_scope(self.source(rotate={'R1': 180}), {**PARAMS, 'min_rotation_gain_mm': 1})
        result = results['congestion']
        issue = next(i for i in result['details']['issues'] if i['kind'] == 'ratsnest_rotation')
        self.assertEqual(issue['suggested_move']['text'], 'rotate R1 180°')
        self.assertEqual(issue['suggested_move']['rotation_deg'], 0)
        self.assertEqual(result['outcome'], 'info')
        restored = extract(self.source(rotate={'R1': issue['suggested_move']['rotation_deg']}) / BOARD)
        self.assertEqual([[p['number'], p['position_nm']] for p in r1['pads']],
                         [[p['number'], p['position_nm']] for f in restored['footprints'] if f['ref'] == 'R1'
                          for p in f['pads']])


if __name__ == '__main__':
    unittest.main()
