"""Explicit external-fixture gate, invoked separately from ordinary discovery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest

from agent_ee import placement_job as job
from tests.toolchain import require_toolchain
from tests.test_placement_job_clearance import evaluate, pair_items, pair_feasible


class EspProgClearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        for key in ('AGENT_EE_ESP_PROG_PREPARED', 'AGENT_EE_USB1_SCAN',
                    'AGENT_EE_USB1_SCAN_PROBLEM', 'AGENT_EE_CLEARANCE_EVIDENCE'):
            if not os.environ.get(key):
                if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
                    raise AssertionError(f'{key} required for strict native agreement')
                raise unittest.SkipTest(f'{key} not configured')
        cls.source = Path(os.environ['AGENT_EE_ESP_PROG_PREPARED'])
        cls.out = Path(os.environ['AGENT_EE_CLEARANCE_EVIDENCE'])
        cls.out.mkdir(parents=True, exist_ok=True)
        problem, _ = job.capture(cls.source / 'board.kicad_pcb',
                                 json.loads((cls.source / 'intent.json').read_text()),
                                 json.loads((cls.source / 'groups.json').read_text()))
        cls.usb = next(r for r in problem['footprints'] if r['native_ref'] == 'USB1')
        cls.fid = next(r for r in problem['footprints']
                       if r['uuid'] == '00000000-0000-0000-0000-00005d8c55f6')
        cls.problem = dict(problem, footprints=[cls.usb, cls.fid], groups=[], proximity=[])
        cls.source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in cls.source.iterdir() if p.is_file()}

    def native_seat(self, name, x, y):
        import pcbnew as k
        target = self.out / name
        target.mkdir()
        board = k.LoadBoard(str(self.source / 'board.kicad_pcb'))
        usb = next(f for f in board.GetFootprints() if f.m_Uuid.AsString() == self.usb['uuid'])
        fid = next(f for f in board.GetFootprints() if f.m_Uuid.AsString() == self.fid['uuid'])
        usb.SetOrientationDegrees(180)
        usb.SetPosition(k.VECTOR2I(x, y))
        k.SaveBoard(str(target / 'board.kicad_pcb'), board)
        shutil.copyfile(self.source / 'board.kicad_pro', target / 'board.kicad_pro')
        report, execution = job.drc(target / 'board.kicad_pcb', target)
        items = pair_items(report, {p.m_Uuid.AsString() for p in usb.Pads()},
                           {p.m_Uuid.AsString() for p in fid.Pads()})
        return {'native_clearance': bool(items), 'items': items, 'execution': execution}

    def test_00_as_built_agrees(self):
        result = evaluate(self.problem, [{self.usb['uuid']: [117_500_000, 100_000_000, 2]}])[0]
        native = self.native_seat('as-built', 117_500_000, 100_000_000)
        receipt = dict(native, engine=result)
        (self.out / 'as-built-agreement.json').write_text(json.dumps(receipt, indent=2) + '\n')
        self.assertEqual(pair_feasible(result), not native['native_clearance'], receipt)
        self.assertEqual(result['feasible'], not native['native_clearance'],
                         'full as-built feasibility gate failed; stop before further qualification: '
                         + json.dumps(receipt))

    def test_01_deterministic_50_plus_10_soundness(self):
        # The sealed scan stores the count/grid but no per-seat coordinates.
        # Reconstruct its exact enumeration from its sealed producer's input.
        scan_path = Path(os.environ['AGENT_EE_USB1_SCAN'])
        scan = json.loads(scan_path.read_text())
        old = json.loads(Path(os.environ['AGENT_EE_USB1_SCAN_PROBLEM']).read_text())
        old_usb = next(r for r in old['footprints'] if r['uuid'] == scan['usb1_uuid'])
        self.assertEqual(old_usb['occupancy'], self.usb['occupancy'])
        x0, y0, _, y1 = old['outline']['rect_nm']
        _, ly0, lx1, ly1 = old_usb['occupancy']['local_rect_nm']
        grid = scan['grid_nm']
        seats = [(x0 + s + lx1, y) for s in range(0, 750_001, grid)
                 for y in range(y0 + ly1, y1 + ly0 + 1, grid)]
        self.assertEqual(len(seats), scan['seats'])
        indices = [i * (len(seats) - 1) // 49 for i in range(50)]
        selected = [('scan', i, *seats[i]) for i in indices]
        pad = next(p for p in self.usb['pads'] if p['number'] == '1')
        dx, dy = pad['local_nm']
        fx, fy = self.fid['pose']['x_nm'], self.fid['pose']['y_nm']
        selected += [('deliberate', i, fx + dx + (i - 5) * 20_000, fy + dy) for i in range(10)]
        engines = evaluate(self.problem, [{self.usb['uuid']: [x, y, 2]} for _, _, x, y in selected])
        matrix = {'engine_feasible_native_clear': 0, 'engine_feasible_native_violation': 0,
                  'engine_infeasible_native_clear': 0, 'engine_infeasible_native_violation': 0}
        rows = []
        for n, ((kind, index, x, y), engine) in enumerate(zip(selected, engines)):
            native = self.native_seat(f'seat-{n:02}', x, y)
            key = 'engine_' + ('feasible' if pair_feasible(engine) else 'infeasible') + '_native_' + (
                'violation' if native['native_clearance'] else 'clear')
            matrix[key] += 1
            rows.append(dict(kind=kind, index=index, x_nm=x, y_nm=y, engine=engine, **native))
        receipt = {'matrix': matrix, 'rows': rows,
                   'scan_sha256': hashlib.sha256(scan_path.read_bytes()).hexdigest(),
                   'selection': '50 evenly spaced enumeration indices, including endpoints; 10 pad-centre intrusions',
                   'scope': 'USB1 and specified fiducial pair; feasibility excludes unary constraints, which are retained in each engine diagnostic',
                   'source_hashes': self.source_hashes}
        (self.out / 'soundness.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print('CONFUSION_MATRIX ' + json.dumps(matrix), flush=True)
        self.assertTrue(all(r['native_clearance'] for r in rows if r['kind'] == 'deliberate'))
        self.assertEqual(matrix['engine_feasible_native_violation'], 0, receipt)
        self.assertEqual({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in self.source.iterdir() if p.is_file()}, self.source_hashes)
