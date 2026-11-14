"""Native copper-bound agreement; external esp_prog seats never enter git."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agent_ee import placement_job as job
from tests.toolchain import require_toolchain
from tests.test_placement_job import MM, _box


def evaluate(problem, moves):
    request = dict(problem, evaluate=moves)
    done = subprocess.run([str(job.rust_executable())], input=json.dumps(request).encode(),
                          capture_output=True, timeout=60)
    if done.returncode:
        raise AssertionError((done.returncode, done.stdout.decode(), done.stderr.decode()))
    return json.loads(done.stdout)['evaluations']


def pair_items(report, a, b):
    return [v for v in report['violations'] if v['type'] in ('clearance', 'shorting_items')
            and {i['uuid'] for i in v.get('items', [])} & a
            and {i['uuid'] for i in v.get('items', [])} & b]


def pair_feasible(evaluation):
    # This two-footprint problem retains unary constraints as diagnostics. The
    # agreement obligation concerns pair clearance, not whole-placement legality.
    return not any(v['kind'] == 'pair' for v in evaluation['violations'])


class PadClearanceTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.directory = Path(tempfile.mkdtemp(prefix='ee515q-shapes-'))
        self.addCleanup(shutil.rmtree, self.directory)

    def board(self, kind, clearance=1_016_000):
        import pcbnew as k
        board = k.BOARD()
        for edge in _box(k, board, k.Edge_Cuts, 0, 0, 30 * MM, 30 * MM):
            board.Add(edge)
        pads = []
        for ref, x, shape in [('P', 10, kind), ('FID', 14, 'circle')]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            board.Add(fp)
            pad = k.PAD(fp)
            fp.Add(pad)
            pad.SetNumber('1')
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.PADSTACK.ALL_LAYERS, getattr(k, 'PAD_SHAPE_' + shape.upper()))
            pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(2 * MM, MM))
            if shape == 'circle':
                pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(MM, MM))
            if shape == 'roundrect':
                pad.SetRoundRectRadiusRatio(k.PADSTACK.ALL_LAYERS, 0.25)
            layer_set = k.LSET()
            layer_set.AddLayer(k.F_Cu)
            pad.SetLayerSet(layer_set)
            fp.SetPosition(k.VECTOR2I(x * MM, 10 * MM))
            if ref == 'FID':
                pad.SetLocalClearance(clearance)
            pads.append(pad)
        path = self.directory / 'board.kicad_pcb'
        k.SaveBoard(str(path), board)
        path.with_suffix('.kicad_pro').write_text(json.dumps({
            'board': {'design_settings': {'rules': {'min_clearance': 0.15}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': 0.15}]}}))
        fixed = {'fixed_poses': [{'ref': 'FID', 'x': 14, 'y': 10, 'rot': 0, 'side': 'F'}]}
        return path, fixed, pads

    def shape_agreement(self, kind):
        import pcbnew as k
        path, fixed, pads = self.board(kind)
        problem, _ = job.capture(path, job.normalize_intent(fixed, {'groups': []}))
        row = next(r for r in problem['footprints'] if r['native_ref'] == 'P')
        geometry = row['copper_local_nm'][0]['geometry']
        self.assertEqual(geometry['kind'], kind)
        self.assertEqual(geometry['status'], 'conservative_native_polygon')
        fid = next(r for r in problem['footprints'] if r['native_ref'] == 'FID')
        self.assertEqual(fid['copper_local_nm'][0]['clearance_nm'], 1_016_000)
        # Far, inside only the local override, and overlapping native copper.
        override_only = 12_100_000 if kind == 'circle' else 11_600_000
        for index, x in enumerate((10 * MM, override_only, 14 * MM)):
            result = evaluate(problem, [{row['uuid']: [x, 10 * MM, 0]}])[0]
            board = k.LoadBoard(str(path))
            native = next(f for f in board.GetFootprints() if f.GetReference() == 'P')
            native.SetPosition(k.VECTOR2I(x, 10 * MM))
            target = self.directory / f'seat{index}'
            target.mkdir()
            k.SaveBoard(str(target / 'board.kicad_pcb'), board)
            shutil.copyfile(path.with_suffix('.kicad_pro'), target / 'board.kicad_pro')
            report, _ = job.drc(target / 'board.kicad_pcb', target)
            native_bad = bool(pair_items(report, {pads[0].m_Uuid.AsString()}, {pads[1].m_Uuid.AsString()}))
            self.assertEqual(result['feasible'], not native_bad, (kind, x, result, report))
            self.assertEqual(native_bad, index > 0)

    def test_circle_native_agreement_and_override(self):
        self.shape_agreement('circle')

    def test_rect_native_agreement_and_override(self):
        self.shape_agreement('rect')

    def test_oval_native_agreement_and_override(self):
        self.shape_agreement('oval')

    def test_roundrect_native_agreement_and_override(self):
        self.shape_agreement('roundrect')

    def test_unknown_shape_and_bad_polygon_refuse(self):
        import pcbnew as k
        path, fixed, _ = self.board('rect')
        board = k.LoadBoard(str(path))
        pad = next(iter(next(f for f in board.GetFootprints() if f.GetReference() == 'P').Pads()))
        pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_TRAPEZOID)
        with self.assertRaisesRegex(job.JobRefusal, 'unsupported native pad shape'):
            job._pad_copper(pad, (0, 0), 0)
        problem, _ = job.capture(path, job.normalize_intent(fixed, {'groups': []}))
        row = next(r for r in problem['footprints'] if r['native_ref'] == 'P')
        row['copper_local_nm'][0]['geometry']['polygons'] = []
        with self.assertRaisesRegex(AssertionError, 'empty native copper polygons'):
            evaluate(problem, [{}])

    def test_per_layer_padstack_refuses(self):
        import pcbnew as k
        path, _, _ = self.board('rect')
        board = k.LoadBoard(str(path))
        pad = next(iter(next(f for f in board.GetFootprints() if f.GetReference() == 'P').Pads()))
        pad.Padstack().SetMode(k.PADSTACK.MODE_FRONT_INNER_BACK)
        with self.assertRaisesRegex(job.JobRefusal, 'per-layer padstack unsupported'):
            job._pad_copper(pad, (0, 0), 0)

    def test_custom_native_polygon_uses_primitive_not_anchor_box(self):
        import pcbnew as k
        from tests.test_custom_pad_index import custom, PRIMITIVES
        footprint = self.directory / 'OwnedCustom.kicad_mod'
        footprint.write_text(custom(PRIMITIVES['poly']))
        fp = k.FootprintLoad(str(self.directory), 'OwnedCustom')
        self.assertIsNotNone(fp)
        board = k.BOARD()
        board.Add(fp)
        pad = next(p for p in fp.Pads() if p.GetNumber() == '1')
        copper = job._pad_copper(pad, (0, 0), 0)
        self.assertEqual(copper['geometry']['kind'], 'custom')
        # KiCad's custom-pad ERROR_OUTSIDE also adds the requested 1 µm
        # tolerance at straight primitive edges. Preserve that native bound.
        self.assertEqual(copper['rect_nm'], [-MM - 1000, -MM - 1000, 2 * MM + 1000, MM + 1000])

    def test_non_orthogonal_rotated_footprints_exact_obb_avoids_false_conflicts(self):
        """Two 45-degree footprints whose upright AABBs overlap report zero pair conflicts."""
        import math, pcbnew as k
        path = self.directory / 'rotated.kicad_pcb'
        board = k.BOARD()
        # Create board outline
        outline = k.PCB_SHAPE(board)
        outline.SetShape(k.SHAPE_T_RECT)
        outline.SetStart(k.VECTOR2I(0, 0))
        outline.SetEnd(k.VECTOR2I(50 * MM, 50 * MM))
        outline.SetLayer(k.Edge_Cuts)
        board.Add(outline)
        settings = board.GetDesignSettings()
        settings.m_SolderMaskMinWidth = 50000

        # Part body: 0.8 mm x 1.6 mm. At 45 deg, upright AABB is ~1.7 x 1.7 mm.
        # Placed with 1.0 mm separation along normal (-0.707, 0.707): actual physical gap is 0.2 mm.
        # Upright AABBs overlap by ~0.7 mm.
        for ref, x, y in [('C1', 20 * MM, 20 * MM),
                          ('C2', 20 * MM - 707106, 20 * MM + 707106)]:
            fp = k.FOOTPRINT(board)
            fp.SetReference(ref)
            fab = k.PCB_SHAPE(fp)
            fab.SetShape(k.SHAPE_T_RECT)
            fab.SetStart(k.VECTOR2I(-400000, -800000))
            fab.SetEnd(k.VECTOR2I(400000, 800000))
            fab.SetLayer(k.F_Fab)
            fp.Add(fab)
            pad = k.PAD(fp)
            pad.SetNumber('1')
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_RECT)
            pad.SetSize(k.PADSTACK.ALL_LAYERS, k.VECTOR2I(500000, 600000))
            pad.SetPosition(k.VECTOR2I(0, 0))
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            layers.AddLayer(k.F_Mask)
            pad.SetLayerSet(layers)
            fp.Add(pad)
            fp.SetPosition(k.VECTOR2I(x, y))
            fp.SetOrientationDegrees(45)
            board.Add(fp)
        k.SaveBoard(str(path), board)
        path.with_suffix('.kicad_pro').write_text('{"board": {"design_settings": {"rule_severities": {"courtyards_overlap": "ignore"}}}}\n')

        fixed = {'schema': 'agent-ee.placement-intent/1',
                 'fixed_poses': [{'ref': 'C1', 'x': 20.0, 'y': 20.0, 'side': 'F'}]}
        problem, _ = job.capture(path, job.normalize_intent(fixed, {'groups': []}))

        # Evaluate at native poses: exact OBB and mask aperture geometry avoids false conflicts
        evals = evaluate(problem, [{}])
        violations = [v for v in evals[0]['violations'] if v['kind'] == 'pair']
        self.assertEqual(violations, [])

        # If geometry is stripped to upright boxes, false body conflict is detected
        for fp_row in problem['footprints']:
            if 'geometry' in fp_row.get('occupancy', {}):
                del fp_row['occupancy']['geometry']
        evals_stripped = evaluate(problem, [{}])
        violations_stripped = [v for v in evals_stripped[0]['violations'] if v['kind'] == 'pair']
        self.assertTrue(any('body' in v.get('rules', {}) for v in violations_stripped))
