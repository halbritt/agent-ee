"""Default Rust/Python planning parity on the actual ten JP attachment rows.

Finite-height records here are TEST ONLY frame witnesses. The captured TIP_ONLY
recipe retains unknown height/access; these tests do not qualify iron clearance.
"""
import copy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tempfile
import unittest

from agent_ee import mechanical_reservations as mr
from agent_ee.placement_probe import extract
from test_mechanical_custom_pads import FIXTURE, frame_witness, inputs
from test_placement_engine_parity import EngineFixture
from test_placer import request
from toolchain import require_toolchain


class NativeCustomPadParityTests(EngineFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        super().setUpClass()

    def bound_capture(self, source, *, witness=True):
        capture = extract(source)
        _, document = inputs()
        document['original_board_sha256'] = capture['board_sha256']
        return capture, mr.bind(frame_witness(document) if witness else document, capture)

    def plan(self, capture, binding, *, free=False):
        refs = sorted(fp['ref'] for fp in capture['footprints'])
        policy = request(fixed_refs=refs[:-1] if free else refs, step_budget=2000,
                         mechanical_reservations=binding)
        return self.compare(capture, {'policy': policy})

    def test_ten_rows_native_move_rotate_flip_and_default_planner_parity(self):
        import pcbnew as k
        source = FIXTURE / 'jumpers.kicad_pcb'
        capture, binding = self.bound_capture(source)
        original, document = inputs()
        actual = {f['ref']: f for f in capture['footprints']}
        for fp in original['footprints']:
            self.assertEqual(actual[fp['ref']]['pads'], fp['pads'])
        expected = {row['id'] for row in document['reservations']}
        self.assertEqual(len(expected), 10)
        baseline = self.plan(capture, binding)
        # The actual source parks these footprints outside its 90 x 65 mm
        # outline. Both engines must retain that physical refusal.
        self.assertEqual(baseline['status'], 'fixed_conflict', baseline['status'])
        with tempfile.TemporaryDirectory(prefix='mechanical-parity-') as temporary:
            target = Path(temporary) / 'moved.kicad_pcb'
            for direction in (None, k.FLIP_DIRECTION_LEFT_RIGHT, k.FLIP_DIRECTION_TOP_BOTTOM):
                for rotation in (0, 90, 180, 270):
                    board = k.LoadBoard(str(source))
                    for fp in board.GetFootprints():
                        if direction is not None:
                            fp.Flip(fp.GetPosition(), direction)
                        fp.SetOrientationDegrees(rotation)
                        # Native owner relocation only: preserve every pad and
                        # put the parked jumpers inside the existing outline.
                        fp.SetPosition(k.VECTOR2I((10 + 15 * (int(fp.GetReference()[2:]) - 1)) * 1000000,
                                                 20000000))
                    k.SaveBoard(str(target), board)
                    moved = extract(target)
                    with self.subTest(direction=direction, rotation=rotation):
                        plan = self.plan(moved, binding)
                        self.assertEqual(plan['status'], 'planned', plan['status'])
                        if direction is None and rotation == 0:
                            free = self.plan(moved, binding, free=True)
                            self.assertEqual(free['status'], 'planned', free['status'])
                        grade = plan['judgment']['mechanical_reservations']
                        self.assertEqual(grade['status'], 'pass', grade)
                        self.assertEqual({r['id'] for r in grade['reservations']}, expected)
                        # Independent expected swept-tip bounds, not one engine's output.
                        pads = {(f['ref'], p['number']): p for f in moved['footprints'] for p in f['pads']}
                        for row in grade['reservations']:
                            pad = pads[row['owner_ref'], row['id'].split('iron-pad')[1][0]]
                            x0, y0, x1, y1 = pad['custom_geometry']['bbox_nm']
                            dx, dy = (650000, 200000) if rotation % 180 == 0 else (200000, 650000)
                            self.assertEqual(set(map(tuple, row['polygons_nm'][0])),
                                             {(x0-dx, y0-dy), (x1+dx, y0-dy), (x1+dx, y1+dy), (x0-dx, y1+dy)})
                            self.assertEqual(row['faces'], ['F' if direction is None else 'B'])

    def test_native_changed_primitive_and_stale_identity_refused_by_both(self):
        source = FIXTURE / 'jumpers.kicad_pcb'
        capture, binding = self.bound_capture(source)
        stale = copy.deepcopy(binding)
        stale['source_board_sha256'] = '0' * 64
        self.assertEqual(self.plan(capture, stale), {'error': 'mechanical binding document identity changed'})
        with tempfile.TemporaryDirectory(prefix='mechanical-parity-stale-') as temporary:
            target = Path(temporary) / 'changed.kicad_pcb'
            raw = source.read_bytes()
            self.assertIn(b'(center 0 0.25)', raw)
            target.write_bytes(raw.replace(b'(center 0 0.25)', b'(center 0 0.26)', 1))
            changed = extract(target)
            plan = self.plan(changed, binding)
            self.assertEqual(plan['status'], 'mechanical_geometry_unavailable')
            self.assertTrue(any('custom-pad geometry changed' in r['reason']
                                for r in plan['mechanical_reservations']['unknown']))
        for mutation in ('owner', 'pad', 'number', 'move', 'rotate', 'flip', 'anchor', 'merged', 'bbox'):
            changed = copy.deepcopy(capture)
            fp = changed['footprints'][0]
            pad = fp['pads'][0]
            if mutation == 'owner':
                fp['uuid'] = 'wrong-owner'
            elif mutation == 'pad':
                pad['uuid'] = 'wrong-pad'
            elif mutation == 'number':
                pad['number'] = 'wrong-number'
            elif mutation == 'move':
                fp['position_nm'][0] += 1
            elif mutation == 'rotate':
                fp['rotation_deg'] += 90
            elif mutation == 'flip':
                fp['side'] = 'B'
            elif mutation == 'anchor':
                pad['custom_geometry']['anchor'] = 0
            elif mutation == 'merged':
                pad['custom_geometry']['merged_polygons_nm'][0][0][0] += 1
            else:
                pad['custom_geometry']['bbox_nm'][0] += 1
            with self.subTest(mutation=mutation):
                plan = self.plan(changed, binding)
                self.assertEqual(plan['status'], 'mechanical_geometry_unavailable')
                self.assertEqual({r['reservation'] for r in plan['mechanical_reservations']['unknown']},
                                 {r['id'] for r in binding['document']['reservations'] if r['owner']['ref'] == fp['ref']})

    def test_native_ten_and_twenty_mm_coordinates_survive_flip(self):
        import pcbnew as k
        raw = (FIXTURE / 'jumpers.kicad_pcb').read_bytes()
        # Explicit fault-injection fixture: enlarge a primitive's circle to put
        # exact 10/20 mm coordinates through native serialization and face flips.
        # Keep unknown access, since this altered copper is not the pod design.
        self.assertIn(b'(center 0 0.25) (end 0.5 0.25)', raw)
        with tempfile.TemporaryDirectory(prefix='mechanical-parity-exponent-') as temporary:
            source = Path(temporary) / 'large.kicad_pcb'
            source.write_bytes(raw.replace(b'(center 0 0.25) (end 0.5 0.25)',
                                           b'(center 0 10) (end 0 20)', 1))
            capture, binding = self.bound_capture(source, witness=False)
            before = self.plan(capture, binding)
            for direction in (k.FLIP_DIRECTION_LEFT_RIGHT, k.FLIP_DIRECTION_TOP_BOTTOM):
                board = k.LoadBoard(str(source))
                for fp in board.GetFootprints():
                    fp.Flip(fp.GetPosition(), direction)
                    fp.SetOrientationDegrees(90)
                    fp.SetPosition(fp.GetPosition() + k.VECTOR2I(100000, 200000))
                target = Path(temporary) / 'flipped.kicad_pcb'
                k.SaveBoard(str(target), board)
                with self.subTest(direction=direction):
                    moved = extract(target)
                    after = self.plan(moved, binding)
                    # Explicit representation control: the native probe currently
                    # normalizes decimals, so also compare equivalent fixed-point
                    # tokens. This is not claimed as naturally observed output.
                    def fixed_tokens(node):
                        if isinstance(node, list):
                            return [fixed_tokens(child) for child in node]
                        try:
                            return format(Decimal(node), 'f')
                        except InvalidOperation:
                            return node
                    for fp in moved['footprints']:
                        for pad in fp['pads']:
                            geometry = pad['custom_geometry']
                            geometry['primitives'] = fixed_tokens(geometry['primitives'])
                    spelled = self.plan(moved, binding)
                    self.assertEqual(spelled['mechanical_reservations'], after['mechanical_reservations'])
                    self.assertEqual(after['status'], 'mechanical_geometry_unavailable')
                    self.assertEqual(len(after['mechanical_reservations']['unknown']), 10)
                    self.assertEqual(after['mechanical_reservations']['unknown'],
                                     before['mechanical_reservations']['unknown'])
                    self.assertTrue(all(r['reason'] == 'height coverage is unresolved; access coverage is unresolved'
                                        for r in after['mechanical_reservations']['unknown']))


if __name__ == '__main__':
    unittest.main()
