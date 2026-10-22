"""Independent saved-native pad keep-out publication gate (AGENTEE-424)."""
import copy
import json
import unittest
from unittest.mock import patch

from agent_ee.placer_runner import _drc_comparison, _drc_item_states, _native_drc, _proposal_drc
import test_placer_packing_scope as fixture


class NativeKeepoutComparisonTest(unittest.TestCase):
    setUpClass = classmethod(fixture.NativeRuleAreaSemanticsTest.setUpClass.__func__)
    setUp = fixture.NativeRuleAreaSemanticsTest.setUp
    drc = fixture.NativeRuleAreaSemanticsTest.drc

    def reports(self, source, board, name):
        import pcbnew as k
        work = self.root / name
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        k.SaveBoard(str(output), board)
        before = _native_drc(source, work)
        after = _native_drc(output, work)
        return before, after, _drc_item_states(source), _drc_item_states(output)

    def test_owner_baseline_across_poses(self):
        import pcbnew as k
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source, found = self.drc(['F.Cu'], ('pads',), owned=True, name=f'owner-{rotation}')
                self.assertTrue(found)
                board = k.LoadBoard(str(source))
                fp = next(iter(board.GetFootprints()))
                fp.SetPosition(k.VECTOR2I(11000000, 12000000))
                fp.SetOrientationDegrees(rotation)
                args = self.reports(source, board, f'pose-{rotation}')
                delta = _drc_comparison(*args)
                self.assertTrue(any(v['type'] == 'items_not_allowed' for v in args[1]['violations']))
                self.assertEqual(delta['added_by_type'].get('items_not_allowed', 0), 0)
                self.assertEqual(delta['blocking_findings'], [])
                # Exercise the complete publication comparison, with retained raw reports.
                output = self.root / f'pose-{rotation}' / 'candidate.kicad_pcb'
                _, published = _proposal_drc(source, output)
                self.assertEqual(published['blocking_findings'], [])
                self.assertEqual(published['added_by_type'].get('items_not_allowed', 0), 0)

    def test_moved_same_pad_in_board_area_is_new_but_unchanged_is_baseline(self):
        import pcbnew as k
        source, found = self.drc(['F.Cu'], ('pads',))
        self.assertTrue(found)
        board = k.LoadBoard(str(source))
        unchanged = self.reports(source, board, 'unchanged')
        self.assertEqual(_drc_comparison(*unchanged)['blocking_findings'], [])
        next(iter(board.GetFootprints())).Move(k.VECTOR2I(200000, 200000))
        args = self.reports(source, board, 'moved')
        delta = _drc_comparison(*args)
        self.assertEqual(delta['added_by_type']['items_not_allowed'], 1)
        self.assertTrue(any(v['type'] == 'items_not_allowed' for v in delta['blocking_findings']))

    def test_new_peer_area_cannot_hide_behind_owner_baseline(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',), owned=True)
        board = k.LoadBoard(str(source))
        peer = k.FOOTPRINT(board)
        peer.SetReference('U2')
        board.Add(peer)
        area = k.ZONE(peer)
        area.SetIsRuleArea(True)
        area.SetLayer(k.F_Cu)
        area.SetDoNotAllowPads(True)
        area.SetDoNotAllowFootprints(False)
        outline = area.Outline()
        outline.NewOutline()
        for x, y in ((9, 9), (11, 9), (11, 11), (9, 11)):
            outline.Append(x * fixture.MM, y * fixture.MM)
        peer.Add(area)
        # Establish a source where only the owner's own area contacts its pad.
        peer.Move(k.VECTOR2I(5000000, 0))
        k.SaveBoard(str(source), board)
        for mover in ('peer', 'pad'):
            with self.subTest(mover=mover):
                candidate = k.LoadBoard(str(source))
                fp = next(f for f in candidate.GetFootprints()
                          if f.GetReference() == ('U2' if mover == 'peer' else 'C1'))
                fp.Move(k.VECTOR2I(-5000000 if mover == 'peer' else 5000000, 0))
                args = self.reports(source, candidate, mover)
                delta = _drc_comparison(*args)
                self.assertTrue(any(v['type'] == 'items_not_allowed' for v in delta['blocking_findings']), delta)

    def test_moved_board_area_with_same_pad_identity_is_new(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',))
        board = k.LoadBoard(str(source))
        next(iter(board.Zones())).Move(k.VECTOR2I(100000, 0))
        args = self.reports(source, board, 'area-moved')
        delta = _drc_comparison(*args)
        self.assertEqual(delta['added_by_type']['items_not_allowed'], 1)
        self.assertTrue(any(v['type'] == 'items_not_allowed' for v in delta['blocking_findings']))

    def test_new_track_copper_in_pad_area_with_tracks_forbidden_is_blocked(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',))
        board = k.LoadBoard(str(source))
        zone = next(iter(board.Zones()))
        zone.SetDoNotAllowTracks(True)
        track = k.PCB_TRACK(board)
        track.SetLayer(k.F_Cu)
        track.SetStart(k.VECTOR2I(15000000, 11000000))
        track.SetEnd(k.VECTOR2I(16000000, 11000000))
        track.SetWidth(200000)
        board.Add(track)
        k.SaveBoard(str(source), board)
        track.Move(k.VECTOR2I(-4000000, 0))
        args = self.reports(source, board, 'track-added')
        delta = _drc_comparison(*args)
        self.assertTrue(any(v['type'] == 'items_not_allowed' and
                            any(i['uuid'] == track.m_Uuid.AsString() for i in v['items'])
                            for v in delta['blocking_findings']), delta)

    def test_contact_with_unsupported_area_geometry_refuses(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',))
        board = k.LoadBoard(str(source))
        outline = next(iter(board.Zones())).Outline()
        outline.NewHole(0)
        for x, y in ((12, 12), (12.5, 12), (12.5, 12.5), (12, 12.5)):
            outline.Append(int(x * fixture.MM), int(y * fixture.MM), 0, 0)
        k.SaveBoard(str(source), board)
        args = self.reports(source, board, 'unsupported')
        self.assertTrue(any(v['type'] == 'items_not_allowed' for v in args[1]['violations']))
        with self.assertRaisesRegex(ValueError, 'items_not_allowed geometry unavailable'):
            _drc_comparison(*args)

    def test_missing_native_geometry_and_ignored_coverage_refuse(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',))
        args = self.reports(source, k.LoadBoard(str(source)), 'missing')
        before, after, old_states, new_states = args
        for old, new in ((None, None), ({}, new_states), (old_states, {})):
            with self.subTest(old=old is None, new=new is None):
                with self.assertRaisesRegex(ValueError, 'items_not_allowed geometry unavailable'):
                    _drc_comparison(copy.deepcopy(before), copy.deepcopy(after), old, new)
        ignored = self.root / 'ignored'
        ignored.mkdir()
        source.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'items_not_allowed': 'ignore'}}}}))
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            _native_drc(source, ignored)
        for field in ('ignored_checks', 'included_severities', 'violations'):
            report = copy.deepcopy(before)
            report.pop(field)
            with self.subTest(missing=field), patch('agent_ee.placer_runner.read_json', return_value=report):
                with self.assertRaises(ValueError):
                    _native_drc(source, ignored)

    def test_independent_publication_regression(self):
        maker = fixture.NativePadKeepoutPublicationTest()
        maker.setUp()
        self.addCleanup(maker.doCleanups)
        source = maker.source(['F.Cu'])
        patches = fixture.courtyard_only()
        for mutant in patches:
            mutant.start()
        try:
            with self.assertRaisesRegex(ValueError, 'introduced native DRC.*items_not_allowed'):
                maker.place(source, 'regression')
        finally:
            for mutant in patches:
                mutant.stop()
        self.assertFalse((maker.scratch / 'regression').exists())


if __name__ == '__main__':
    unittest.main()
