"""A placement hint must recognize terminal geometry without becoming a rule."""
import copy
import unittest

from agent_ee import placement_escape
from agent_ee import placement_job as job
from test_placement_job import Fixture, intent, groups


def terminal():
    return {'uuid': 'owner', 'native_ref': 'J7',
            'occupancy': {'local_rect_nm': [-2000000, -2000000, 2000000, 2000000]},
            'pads': [{'uuid': 'p', 'net': '/io/DP', 'local_nm': [1800000, -325000]},
                     {'uuid': 'n', 'net': '/io/DN', 'local_nm': [1800000, 325000]}],
            'copper_local_nm': [
                {'rect_nm': [1400000, -475000, 2200000, -175000], 'geometry': {'pad_uuid': 'p'}},
                {'rect_nm': [1400000, 175000, 2200000, 475000], 'geometry': {'pad_uuid': 'n'}}]}


class EscapeHints(unittest.TestCase):
    def hints(self, row):
        return placement_escape.hints(row, width=200000, gap=250000, clearance=150000)

    def test_outward_terminal_space_is_a_sourced_heuristic(self):
        row = terminal()
        before = copy.deepcopy(row)
        hints = self.hints(row)
        self.assertEqual(row, before)
        self.assertEqual(len(hints), 1)
        hint = hints[0]
        self.assertEqual(hint['authority'], 'agent-heuristic/1')
        self.assertEqual(hint['pads'], ['p', 'n'])
        x0, y0, x1, y1 = hint['rect_nm']
        self.assertEqual(x0, 2200000)
        self.assertGreater(x1, 3000000)
        self.assertLess(y0, -475000)
        self.assertGreater(y1, 475000)

    def test_pair_names_and_reference_are_not_board_specific(self):
        for p, n in (('/a_P', '/a_N'), ('D+', 'D-'), ('/bus/CLKP', '/bus/CLKN')):
            row = terminal()
            row['native_ref'] = 'arbitrary'
            row['pads'][0]['net'], row['pads'][1]['net'] = p, n
            self.assertEqual(self.hints(row)[0]['nets'], [p, n])

    def test_ambiguous_or_incomplete_terminals_do_not_invent_a_direction(self):
        row = terminal()
        row['pads'][1]['net'] = '/another/sheet/DN'
        self.assertEqual(self.hints(row), [])
        row = terminal()
        row['pads'].append(dict(row['pads'][0], uuid='p2'))
        self.assertEqual(self.hints(row), [])
        row = terminal()
        for pad in row['pads']:
            pad['local_nm'][0] = 0
        self.assertEqual(self.hints(row), [])

    def test_hint_rotates_with_native_local_pad_geometry(self):
        row = terminal()
        original = self.hints(row)[0]['rect_nm']
        for _ in range(4):
            for pad in row['pads']:
                x, y = pad['local_nm']
                pad['local_nm'] = [y, -x]
            for item in row['copper_local_nm']:
                x0, y0, x1, y1 = item['rect_nm']
                item['rect_nm'] = [y0, -x1, y1, -x0]
            x0, y0, x1, y1 = original
            original = [y0, -x1, y1, -x0]
            self.assertEqual(self.hints(row)[0]['rect_nm'], original)


class NativeEscapeHints(Fixture):
    def test_capture_binds_hint_to_saved_native_pads_through_rotation(self):
        import pcbnew
        board = pcbnew.LoadBoard(str(self.board))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
        for number, name in (('1', '/link_P'), ('2', '/link_N')):
            net = pcbnew.NETINFO_ITEM(board, name)
            board.Add(net)
            next(p for p in fp.Pads() if p.GetNumber() == number).SetNet(net)
        expected = None
        for turn in range(4):
            fp.SetOrientationDegrees(90 * turn)
            pcbnew.SaveBoard(str(self.board), board)
            before = self.board.read_bytes(), self.project.read_bytes()
            problem, capture = job.capture(self.board, intent(), groups())
            row = next(f for f in problem['footprints'] if f['native_ref'] == 'U1')
            hints = row['escape_local_nm']
            self.assertEqual(len(hints), 1)
            if expected is None:
                expected = hints
            self.assertEqual(hints, expected)
            self.assertEqual(hints[0]['nets'], ['/link_P', '/link_N'])
            note = next(f for f in capture['footprints'] if f['native_ref'] == 'U1')
            self.assertEqual(note['routing_preferences'], hints)
            self.assertEqual(before, (self.board.read_bytes(), self.project.read_bytes()))
