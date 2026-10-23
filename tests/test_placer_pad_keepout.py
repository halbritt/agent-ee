"""Independent saved-native pad keep-out publication gate (AGENTEE-424)."""
import copy
import json
import unittest
from unittest.mock import patch

from agent_ee.placer_runner import (
    KeepoutWorkLimit, _drc_comparison, _drc_item_states, _native_drc, _proposal_drc,
    _keepout_item_states)
import test_placer_packing_scope as fixture


def large_board(path, owners=64, pads=16):
    import pcbnew as k
    board = k.BOARD()
    for n in range(owners):
        fp = k.FOOTPRINT(board)
        fp.SetReference('U' + str(n + 1))
        board.Add(fp)
        for p in range(pads):
            pad = k.PAD(fp)
            pad.SetNumber(str(p + 1))
            pad.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
            pad.SetSize(k.F_Cu, k.VECTOR2I(1000000, 1000000))
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetLayerSet(k.PAD.SMDMask())
            fp.Add(pad)
        zone = k.ZONE(fp)
        zone.SetIsRuleArea(True)
        zone.SetLayer(k.F_Cu)
        zone.SetDoNotAllowPads(True)
        fp.Add(zone)
        zone.Outline().NewOutline()
        for x, y in ((-2, -2), (2, -2), (2, 2), (-2, 2)):
            zone.Outline().Append(x * 1000000, y * 1000000)
        fp.SetPosition(k.VECTOR2I((n % 8) * 10000000, (n // 8) * 10000000))
    k.SaveBoard(str(path), board)


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

    def owned_many_pads(self):
        import pcbnew as k
        source, _ = self.drc(['F.Cu'], ('pads',), owned=True)
        board = k.LoadBoard(str(source))
        fp = next(iter(board.GetFootprints()))
        for number in range(2, 33):
            pad = k.PAD(fp)
            pad.SetNumber(str(number))
            pad.SetShape(k.F_Cu, k.PAD_SHAPE_RECT)
            pad.SetSize(k.F_Cu, k.VECTOR2I(1000000, 1000000))
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetLayerSet(k.PAD.SMDMask())
            pad.SetPosition(k.VECTOR2I(10000000, 10000000))
            fp.Add(pad)
        k.SaveBoard(str(source), board)
        return source

    def test_keepout_inventory_loads_once_for_many_owned_contacts(self):
        import pcbnew as k
        source = self.owned_many_pads()
        states = _drc_item_states(source)
        with patch.object(k, 'LoadBoard', wraps=k.LoadBoard) as load:
            repeated = _keepout_item_states(source, states)
        self.assertEqual(repeated, states.keepouts)
        self.assertEqual(len(repeated), 32)
        self.assertEqual(load.call_count, 1)

    def test_flipped_many_pad_owner_normalizes_and_inventories_once(self):
        import pcbnew as k
        from agent_ee import placer_runner as runner
        source = self.owned_many_pads()
        before = _drc_item_states(source)
        board, _ = self.flipped(source, 270, k.FLIP_DIRECTION_TOP_BOTTOM)
        candidate = self.root / 'many-flipped.kicad_pcb'
        self.assertTrue(k.SaveBoard(str(candidate), board))
        after = _drc_item_states(candidate)
        stats = {}
        with patch.object(k, 'LoadBoard', wraps=k.LoadBoard) as load, patch.object(
                runner, '_footprint_pad_inventory', wraps=runner._footprint_pad_inventory) as inventory:
            repeated = _keepout_item_states(candidate, after, stats=stats)
        self.assertEqual(repeated, before.keepouts)
        self.assertEqual(repeated, after.keepouts)
        self.assertEqual(len(repeated), 32)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(inventory.call_count, 1)
        self.assertEqual(stats['normalized_owners'], 1)

    def test_large_owned_board_work_and_default_exhaustion(self):
        from agent_ee import placer_runner as runner
        for owners in (64, 128):
            with self.subTest(owners=owners):
                source = self.root / f'large-{owners}.kicad_pcb'
                large_board(source, owners=owners)
                # Build the independent collision inventory without doing the
                # keep-out scan under test twice. No native geometry is mocked.
                with patch.object(runner, '_keepout_item_states', return_value={}):
                    states = runner._drc_item_states(source)
                stats = {}
                if owners == 64:
                    witnesses = _keepout_item_states(source, states, stats=stats)
                    self.assertEqual(len(witnesses), 1024)
                    self.assertTrue(all(witnesses.values()))
                    self.assertEqual(stats['area_pairs'], 65536)
                    self.assertEqual(stats['normalized_owners'], 64)
                    self.assertEqual(stats['board_loads'], 1)
                    self.assertLess(stats['steps_used'], stats['max_steps'])
                else:
                    with self.assertRaises(KeepoutWorkLimit) as caught:
                        _keepout_item_states(source, states, stats=stats)
                    self.assertEqual(caught.exception.max_steps, runner.KEEPOUT_MAX_STEPS)
                    self.assertLessEqual(stats['steps_used'], stats['max_steps'])

    def test_keepout_exact_step_boundary_and_typed_unknown(self):
        source = self.owned_many_pads()
        states = _drc_item_states(source)
        stats = {}
        expected = _keepout_item_states(source, states, stats=stats)
        self.assertEqual(stats['board_loads'], 1)
        self.assertEqual(stats['normalized_owners'], 1)
        self.assertEqual(stats['area_pairs'], 32)
        required = stats['steps_used']
        self.assertEqual(_keepout_item_states(source, states, max_steps=required), expected)
        for limit in (0, required - 1):
            with self.subTest(limit=limit), self.assertRaises(KeepoutWorkLimit) as caught:
                _keepout_item_states(source, states, max_steps=limit)
            error = caught.exception
            self.assertEqual((error.outcome, error.reason), ('unknown', 'keepout_work_limit'))
            self.assertEqual(error.max_steps, limit)
            self.assertLessEqual(error.steps_used, limit)
        for invalid in (-1, True, 1.5, '100'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _keepout_item_states(source, states, max_steps=invalid)

    def test_no_contact_environment_is_serialized_once(self):
        import pcbnew as k
        from agent_ee import placer_runner as runner
        source = self.owned_many_pads()
        board = k.LoadBoard(str(source))
        area = next(iter(next(iter(board.GetFootprints())).Zones()))
        area.Move(k.VECTOR2I(20000000, 0))
        k.SaveBoard(str(source), board)
        states = _drc_item_states(source)
        with patch.object(runner, 'rule_area', wraps=runner.rule_area) as serialize:
            witnesses = _keepout_item_states(source, states)
        self.assertEqual(serialize.call_count, 1)
        self.assertEqual(witnesses, states.keepouts)
        self.assertTrue(all(witnesses.values()))

    def test_work_limit_refuses_publication_and_retains_unknown(self):
        maker = fixture.NativePadKeepoutPublicationTest()
        maker.setUp()
        self.addCleanup(maker.doCleanups)
        source = maker.source(['F.Cu'])
        with patch('agent_ee.placer_runner.KEEPOUT_MAX_STEPS', 0):
            with self.assertRaisesRegex(ValueError, 'keepout work limit exhausted'):
                maker.place(source, 'exhausted')
        self.assertFalse((maker.scratch / 'exhausted').exists())
        retained, = maker.scratch.glob('exhausted-failure-*')
        failure = json.loads((retained / 'failure.json').read_text())
        self.assertEqual((failure['outcome'], failure['status'], failure['reason']),
                         ('unknown', 'not_placed', 'keepout_work_limit'))
        self.assertEqual((failure['steps_used'], failure['max_steps']), (0, 0))
        self.assertTrue((retained / 'native-drc.json').is_file())

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

    def flipped(self, source, rotation, direction):
        import pcbnew as k
        board = k.LoadBoard(str(source))
        fp = next(iter(board.GetFootprints()))
        fp.Flip(fp.GetPosition(), direction)
        fp.SetPosition(k.VECTOR2I(11000000, 12000000))
        fp.SetOrientationDegrees(rotation)
        return board, fp

    def test_owner_baseline_through_side_flip(self):
        """AGENTEE-426: a legitimately flipped footprint keeps its own pad keep-out baseline."""
        import pcbnew as k
        directions = {'lr': k.FLIP_DIRECTION_LEFT_RIGHT, 'tb': k.FLIP_DIRECTION_TOP_BOTTOM}
        cases = [(layers, through, back, rotation, flip)
                 for layers, through in ((['F.Cu'], False), (['F.Cu', 'B.Cu'], False), (['F.Cu'], True),
                                         ('*.Cu', True))
                 for back in (False, True) for rotation in (0, 90, 180, 270) for flip in ('lr', 'tb')
                 if (flip == 'lr' or (rotation in (0, 90) and not through))]
        for layers, through, back, rotation, flip in cases:
            if back and layers == ['F.Cu']:
                layers = ['B.Cu']
            name = f"flip-{'-'.join(layers) if layers != '*.Cu' else 'all'}-{through}-{back}-{rotation}-{flip}"
            with self.subTest(layers=layers, through=through, back=back, rotation=rotation, flip=flip):
                source, found = self.drc(['F.Cu'] if layers == '*.Cu' else layers, ('pads',), through=through,
                                         back=back, owned=True, name=f'src-{name}')
                self.assertTrue(found)
                if layers == '*.Cu':
                    # Every copper layer, including the 28 this 4-layer board does not enable.
                    board = k.LoadBoard(str(source))
                    next(iter(next(iter(board.GetFootprints())).Zones())).SetLayerSet(k.LSET.AllCuMask())
                    k.SaveBoard(str(source), board)
                board, fp = self.flipped(source, rotation, directions[flip])
                self.assertNotEqual(fp.IsFlipped(), back)
                args = self.reports(source, board, name)
                self.assertTrue(any(v['type'] == 'items_not_allowed' for v in args[1]['violations']))
                delta = _drc_comparison(*args)
                self.assertEqual(delta['added_by_type'].get('items_not_allowed', 0), 0, delta)
                self.assertEqual(delta['blocking_findings'], [])
                # Exercise the complete publication comparison at the flipped side.
                _, published = _proposal_drc(source, self.root / name / 'candidate.kicad_pcb')
                self.assertEqual(published['blocking_findings'], [])
                self.assertEqual(published['added_by_type'].get('items_not_allowed', 0), 0)

    def test_flipped_owner_with_reshaped_area_or_new_peer_contact_is_new(self):
        import pcbnew as k
        for change in ('reshaped', 'board-area', 'peer-area'):
            for rotation in (0, 90, 180, 270):
                with self.subTest(change=change, rotation=rotation):
                    name = f'flip-new-{change}-{rotation}'
                    source, found = self.drc(['F.Cu', 'B.Cu'], ('pads',), owned=True, name=f'src-{name}')
                    self.assertTrue(found)
                    board, fp = self.flipped(source, rotation, k.FLIP_DIRECTION_LEFT_RIGHT)
                    if change == 'reshaped':
                        # Same area UUID, but the area no longer has the owner's saved shape.
                        next(iter(fp.Zones())).Move(k.VECTOR2I(100000, 0))
                    else:
                        parent = board if change == 'board-area' else k.FOOTPRINT(board)
                        if change == 'peer-area':
                            parent.SetReference('U2')
                            board.Add(parent)
                        area = k.ZONE(parent)
                        area.SetIsRuleArea(True)
                        area.SetLayer(k.B_Cu)
                        area.SetDoNotAllowPads(True)
                        area.SetDoNotAllowFootprints(False)
                        outline = area.Outline()
                        outline.NewOutline()
                        for x, y in ((10.5, 11.5), (11.5, 11.5), (11.5, 12.5), (10.5, 12.5)):
                            outline.Append(int(x * fixture.MM), int(y * fixture.MM))
                        parent.Add(area)
                    args = self.reports(source, board, name)
                    delta = _drc_comparison(*args)
                    self.assertTrue(any(v['type'] == 'items_not_allowed' for v in delta['blocking_findings']),
                                    delta)

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
        for engine in ('rust', 'python-reference'):
            with self.subTest(engine=engine):
                failed = maker.refused(source, 'regression-' + engine, engine, 'items_not_allowed')
                self.assertTrue(any(x0 < 3 * fixture.MM for x0, _, _, _ in maker.saved_lands(failed)))
                self.assertEqual(maker.not_allowed(failed.parent), ['Pad 1 [SIGNAL] of C1 on F.Cu'])


if __name__ == '__main__':
    unittest.main()
