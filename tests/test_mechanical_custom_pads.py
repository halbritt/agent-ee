"""AGENTEE-489 U1: actual JP1-JP5 captures, native transforms and stale controls.

The source recipe is TIP_ONLY with unknown height/access. Finite-height variants
below are test-only frame witnesses, never production clearance declarations.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import mechanical_reservations as mr, placer
from agent_ee.placement_probe import extract
from toolchain import require_toolchain

FIXTURE = Path(__file__).parent / 'fixtures/mechanical-jumpers'


def inputs():
    return (json.loads((FIXTURE / 'capture.json').read_text()),
            json.loads((FIXTURE / 'reservations.json').read_text()))


def frame_witness(document):
    document = copy.deepcopy(document)
    for row in document['reservations']:
        row['height_nm'] = [0, 1]
        row['coverage'].update(height='covered', access='covered',
                               basis='TEST ONLY: 1 nm interval isolates attachment transforms.')
    return document


class CapturedJumperBindingTests(unittest.TestCase):
    def test_all_ten_actual_rows_bind_without_promoting_unknown_access(self):
        capture, document = inputs()
        self.assertEqual(capture['board_sha256'],
                         'fa7c2ca67e7ba3b832c0b89268052c6701695454bb17756af01854ef3ecaf46e')
        expected = {f'JP{i}/iron-pad{p}@actual-face' for i in range(1, 6) for p in (1, 2)}
        self.assertEqual({r['id'] for r in document['reservations']}, expected)
        binding = mr.bind(document, capture)
        self.assertEqual(binding['document'], document)
        self.assertEqual(len(binding['owners']), 5)
        result = mr.materialize(binding, capture)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual({r['reservation'] for r in result['unknown']}, expected)
        self.assertTrue(all(r['reason'] == 'height coverage is unresolved; access coverage is unresolved'
                            for r in result['unknown']), result)
        projected = mr.materialize(mr.bind(frame_witness(document), capture), capture)
        self.assertEqual(projected['status'], 'pass', projected)
        self.assertEqual({r['id'] for r in projected['reservations']}, expected)

    def test_stale_board_and_wrong_declared_owner_or_pad_refuse_binding(self):
        capture, document = inputs()
        for mutation in ('board', 'ref', 'uuid', 'pad-number', 'pad-uuid'):
            changed = copy.deepcopy(document)
            owner = changed['reservations'][0]['owner']
            if mutation == 'board':
                changed['original_board_sha256'] = '0' * 64
            elif mutation in ('ref', 'uuid'):
                owner[mutation] = 'wrong'
            else:
                owner['pad'][mutation.split('-')[1]] = 'wrong'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                mr.bind(changed, capture)

    def test_stale_custom_geometry_and_nonrigid_poses_cannot_materialize(self):
        capture, document = inputs()
        binding = mr.bind(frame_witness(document), capture)
        for mutation in ('owner', 'pad', 'number', 'move', 'rotate', 'flip',
                         'anchor', 'primitive', 'merged-copper', 'bbox', 'unqualified'):
            changed = copy.deepcopy(capture)
            fp = changed['footprints'][0]
            pad = fp['pads'][0]
            if mutation == 'owner':
                fp['uuid'] = 'wrong'
            elif mutation in ('pad', 'number'):
                pad['uuid' if mutation == 'pad' else 'number'] = 'wrong'
            elif mutation == 'move':
                fp['position_nm'][0] += 1
            elif mutation == 'rotate':
                fp['rotation_deg'] += 90
            elif mutation == 'flip':
                fp['side'] = 'B'
            elif mutation == 'anchor':
                pad['custom_geometry']['anchor'] = 0
            elif mutation == 'primitive':
                pad['custom_geometry']['primitives'][0][1][2] = '0.26'
            elif mutation == 'merged-copper':
                pad['custom_geometry']['merged_polygons_nm'][0][0][0] += 1
            elif mutation == 'bbox':
                pad['custom_geometry']['bbox_nm'][0] += 1
            else:
                pad['custom_geometry']['status'] = 'unsupported'
            with self.subTest(mutation=mutation):
                result = mr.materialize(binding, changed)
                self.assertEqual(result['status'], 'unknown', result)
                self.assertEqual({r['reservation'] for r in result['unknown']},
                                 {r['id'] for r in document['reservations'] if r['owner']['ref'] == fp['ref']})

    def test_planner_moves_keep_custom_geometry_bound(self):
        capture, document = inputs()
        binding = mr.bind(frame_witness(document), capture)
        for rotation in (0, 90, 180, 270):
            changed = copy.deepcopy(capture)
            changed['footprints'] = [placer.transform(fp, [fp['position_nm'][0] + 10000000,
                                                          fp['position_nm'][1] + 20000000], rotation)
                                     for fp in capture['footprints']]
            with self.subTest(rotation=rotation):
                result = mr.materialize(binding, changed)
                self.assertEqual(result['status'], 'pass', result)
                self.assertEqual(len(result['reservations']), 10)


class NativeJumperBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def test_all_ten_rows_follow_native_move_rotate_and_both_flip_directions(self):
        import pcbnew as k
        source = FIXTURE / 'jumpers.kicad_pcb'
        original, document = inputs()
        capture = extract(source)
        by_ref = {f['ref']: f for f in capture['footprints']}
        for fp in original['footprints']:
            self.assertEqual(by_ref[fp['ref']]['pads'], fp['pads'])
        # This reduced board preserves every original JP pad but has its own byte hash.
        document['original_board_sha256'] = capture['board_sha256']
        binding = mr.bind(frame_witness(document), capture)
        with tempfile.TemporaryDirectory(prefix='mechanical-custom-native-') as temporary:
            target = Path(temporary) / 'moved.kicad_pcb'
            for direction in (None, k.FLIP_DIRECTION_LEFT_RIGHT, k.FLIP_DIRECTION_TOP_BOTTOM):
                for rotation in (0, 90, 180, 270):
                    board = k.LoadBoard(str(source))
                    for fp in board.GetFootprints():
                        if direction is not None:
                            fp.Flip(fp.GetPosition(), direction)
                        fp.SetOrientationDegrees(rotation)
                        fp.SetPosition(fp.GetPosition() + k.VECTOR2I(10000000, 20000000))
                    k.SaveBoard(str(target), board)
                    moved = extract(target)
                    result = mr.materialize(binding, moved)
                    with self.subTest(direction=direction, rotation=rotation):
                        self.assertEqual(result['status'], 'pass', result)
                        self.assertEqual(len(result['reservations']), 10)
                        pads = {(f['ref'], p['number']): p for f in moved['footprints'] for p in f['pads']}
                        for row in result['reservations']:
                            number = row['id'].split('iron-pad')[1][0]
                            pad = pads[row['owner_ref'], number]
                            x0, y0, x1, y1 = pad['custom_geometry']['bbox_nm']
                            dx, dy = ((650000, 200000) if rotation % 180 == 0 else (200000, 650000))
                            expected = {(x0-dx, y0-dy), (x1+dx, y0-dy), (x1+dx, y1+dy), (x0-dx, y1+dy)}
                            self.assertEqual(set(map(tuple, row['polygons_nm'][0])), expected)
                            self.assertEqual(row['faces'], ['F' if direction is None else 'B'])

    def test_native_primitive_change_refuses_old_binding(self):
        source = FIXTURE / 'jumpers.kicad_pcb'
        capture = extract(source)
        _, document = inputs()
        document['original_board_sha256'] = capture['board_sha256']
        binding = mr.bind(frame_witness(document), capture)
        with tempfile.TemporaryDirectory(prefix='mechanical-custom-change-') as temporary:
            target = Path(temporary) / 'changed.kicad_pcb'
            raw = source.read_bytes()
            self.assertIn(b'(center 0 0.25)', raw)
            target.write_bytes(raw.replace(b'(center 0 0.25)', b'(center 0 0.26)', 1))
            changed = extract(target)
            result = mr.materialize(binding, changed)
            self.assertEqual(result['status'], 'unknown', result)
            self.assertTrue(any('custom-pad geometry changed' in row['reason'] for row in result['unknown']), result)


if __name__ == '__main__':
    unittest.main()
