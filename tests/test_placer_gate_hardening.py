"""Native regressions for AGENTEE-325 publication boundaries."""
import json
import unittest

from agent_ee.placer_runner import _proposal_drc
import test_placer_through_holes as pth_fixture


class GateHardeningTest(unittest.TestCase):
    board = pth_fixture.ThroughHolePlacementTest.board
    setUp = pth_fixture.ThroughHolePlacementTest.setUp

    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def test_moved_existing_cross_footprint_pair_is_introduced(self):
        import pcbnew as k
        source = self.board()
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            fp.Move(k.VECTOR2I(1000000, 0))
        work = self.root / 'moved'
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
            _proposal_drc(source, output)
        delta = json.loads((work / 'native-drc-comparison.json').read_text())
        self.assertTrue(any(v['type'] == 'pth_inside_courtyard' for v in delta['blocking_findings']))

    def test_peer_netclass_clearance_reserves_pth_gap(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer import _qualified, _hole_conflict
        original = self.board()
        board = k.LoadBoard(str(original))
        for fp in board.GetFootprints():
            net = k.NETINFO_ITEM(board, fp.GetReference())
            board.Add(net)
            for pad in fp.Pads():
                pad.SetNet(net)
            if fp.GetReference() == 'C1':
                fp.Move(k.VECTOR2I(3000000, 0))
        source = self.root / 'peer-class.kicad_pcb'
        k.SaveBoard(str(original), board)
        source.write_bytes(original.read_bytes())
        source.with_suffix('.kicad_pro').write_text(json.dumps({
            'net_settings': {'meta': {'version': 5}, 'classes': [{'name': 'Default', 'clearance': 0.2},
                                         {'name': 'Wide', 'clearance': 4.0}],
                             'netclass_patterns': [{'netclass': 'Wide', 'pattern': 'C1'}]}}))
        facts = extract(source)
        _qualified(facts)
        a, b = facts['footprints']
        self.assertTrue(_hole_conflict(a, b), '4 mm peer clearance must reserve the otherwise legal gap')

    def test_every_drilled_pad_requires_its_own_drill_contour(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer import pack
        from test_placer import request
        source = self.board()
        board = k.LoadBoard(str(source))
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
        pad = k.PAD(next(iter(fp.Pads())))
        pad.SetNumber('2')
        pad.SetPosition(k.VECTOR2I(7000000, 3000000))
        fp.Add(pad)
        k.SaveBoard(str(source), board)
        facts = extract(source)
        front = next(f for f in facts['footprints'] if f['ref'] == 'U1')
        omitted = front['pads'][1]['uuid']
        holes = front['packing_pad_geometry']['holes']
        # Keep one pad's reservations: non-empty is not completeness.
        front['packing_pad_geometry']['holes'] = [h for h in holes if h.get('pad_uuid') != omitted]
        self.assertEqual(pack(facts, request())['status'], 'unsupported_geometry')

    def test_new_inter_footprint_mask_bridge_blocks_but_intra_does_not(self):
        from agent_ee.placer_runner import _drc_comparison
        report = {'ignored_checks': [], 'included_severities': ['error', 'warning', 'exclusion'],
                  'violations': []}
        finding = {'type': 'solder_mask_bridge', 'items': [{'uuid': 'a'}, {'uuid': 'b'}]}
        after = {**report, 'violations': [finding]}
        states = {'a': ('U1', 'pad geometry', (0, 0, 0, 0)),
                  'b': ('U2', 'pad geometry', (100, 0, 0, 0))}
        delta = _drc_comparison(report, after, states, states)
        self.assertEqual(delta['blocking_findings'], [finding])
        self.assertEqual(_drc_comparison(after, after, states, states)['blocking_findings'], [])
        intra = {**states, 'b': ('U1', 'pad geometry', (0, 0, 0, 0))}
        self.assertEqual(_drc_comparison(report, after, intra, intra)['blocking_findings'], [])
        self.assertEqual(_drc_comparison(report, after, intra, intra)['added_findings'], [finding])

    def test_larger_custom_rule_reaches_native_gate_as_refusal(self):
        import pcbnew as k
        original = self.board()
        board = k.LoadBoard(str(original))
        for fp in board.GetFootprints():
            net = k.NETINFO_ITEM(board, fp.GetReference())
            board.Add(net)
            for pad in fp.Pads():
                pad.SetNet(net)
            if fp.GetReference() == 'C1':
                fp.SetPosition(k.VECTOR2I(15000000, 3000000))
        k.SaveBoard(str(original), board)
        source = self.root / 'custom-rule.kicad_pcb'
        source.write_bytes(original.read_bytes())
        source.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "large owned gap" '
            '(condition "A.NetName == \'U1\' && B.NetName == \'C1\'") '
            '(constraint clearance (min 4mm)))\n')
        work = self.root / 'custom-rule'
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        board = k.LoadBoard(str(source))
        next(f for f in board.GetFootprints() if f.GetReference() == 'C1').SetPosition(k.VECTOR2I(9000000, 3000000))
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
            _proposal_drc(source, output)
        delta = json.loads((work / 'native-drc-comparison.json').read_text())
        clearances = [v for v in delta['blocking_findings'] if v['type'] == 'clearance']
        self.assertTrue(clearances, delta)
        self.assertTrue(all('4.0000 mm' in v['description'] for v in clearances), clearances)

    def test_native_mask_bridge_is_blocking_and_ignored_category_refuses(self):
        import pcbnew as k
        from agent_ee.placer_runner import _native_drc
        import test_placement_native as fixture
        maker = fixture.NativePlacementTest()
        maker.root = self.root
        original = maker.board()
        board = k.LoadBoard(str(original))
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                layers.AddLayer(k.F_Mask)
                pad.SetLayerSet(layers)
                pad.SetLocalSolderMaskMargin(150000)
        k.SaveBoard(str(original), board)
        source = self.root / 'mask-source.kicad_pcb'
        source.write_bytes(original.read_bytes())
        work = self.root / 'mask'
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        peer = next(f for f in board.GetFootprints() if f.GetReference() == 'C1')
        peer.SetPosition(k.VECTOR2I(3700000, 3000000))
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'introduced native DRC'):
            _proposal_drc(source, output)
        delta = json.loads((work / 'native-drc-comparison.json').read_text())
        self.assertTrue(any(v['type'] == 'solder_mask_bridge' for v in delta['blocking_findings']), delta)
        ignored = self.root / 'ignored-mask.kicad_pcb'
        ignored.write_bytes(source.read_bytes())
        ignored.with_suffix('.kicad_pro').write_text(json.dumps({'board': {'design_settings': {
            'rule_severities': {'solder_mask_bridge': 'ignore'}}}}))
        with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
            _native_drc(ignored, work)

    def test_rigid_intra_footprint_clearance_stays_baseline(self):
        import pcbnew as k
        from agent_ee.placer_runner import _drc_comparison, _drc_item_states, _native_drc
        for distinct in (False, True):
            with self.subTest(distinct_kiids=distinct):
                source = self.board()
                board = k.LoadBoard(str(source))
                fp = next(f for f in board.GetFootprints() if f.GetReference() == 'U1')
                first = next(iter(fp.Pads()))
                pad = k.PAD(fp) if distinct else k.PAD(first)
                if distinct:
                    pad.SetAttribute(first.GetAttribute())
                    pad.SetShape(first.GetShape())
                    pad.SetSize(first.GetSize())
                    pad.SetDrillSize(first.GetDrillSize())
                    pad.SetLayerSet(first.GetLayerSet())
                    pad.SetPosition(first.GetPosition())
                pad.SetNumber('2')
                fp.Add(pad)
                for i, p in enumerate(fp.Pads()):
                    net = k.NETINFO_ITEM(board, f'net-{i}')
                    board.Add(net)
                    p.SetNet(net)
                k.SaveBoard(str(source), board)
                work = self.root / f'rigid-{distinct}'
                work.mkdir()
                before = _native_drc(source, work)
                fp.Move(k.VECTOR2I(1000000, 0))
                output = work / 'candidate.kicad_pcb'
                k.SaveBoard(str(output), board)
                after = _native_drc(output, work)
                own = {p.m_Uuid.AsString() for p in fp.Pads()}
                self.assertEqual(len(own), 2 if distinct else 1)
                intra = lambda v: len(v['items']) > 1 and {i['uuid'] for i in v['items']} <= own
                self.assertTrue(any(v['type'] == 'shorting_items' and intra(v) for v in before['violations']))
                delta = _drc_comparison(before, after, _drc_item_states(source), _drc_item_states(output))
                self.assertFalse(any(intra(v) for v in delta['blocking_findings']))

    def test_peer_single_item_custom_rule_reserves_pth_gap(self):
        import pcbnew as k
        from agent_ee.placement_probe import extract
        from agent_ee.placer import _qualified, _hole_conflict
        original = self.board()
        board = k.LoadBoard(str(original))
        for fp in board.GetFootprints():
            net = k.NETINFO_ITEM(board, fp.GetReference())
            board.Add(net)
            for pad in fp.Pads():
                pad.SetNet(net)
            if fp.GetReference() == 'C1':
                fp.Move(k.VECTOR2I(3000000, 0))
        k.SaveBoard(str(original), board)
        source = self.root / 'peer-rule.kicad_pcb'
        source.write_bytes(original.read_bytes())
        source.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "peer owned gap" '
            '(condition "A.NetName == \'C1\'") (constraint clearance (min 4mm)))\n')
        native = k.LoadBoard(str(source))
        peer = next(f for f in native.GetFootprints() if f.GetReference() == 'C1')
        self.assertEqual(next(iter(peer.Pads())).GetOwnClearance(k.B_Cu), 4000000)
        facts = extract(source)
        _qualified(facts)
        self.assertTrue(_hole_conflict(*facts['footprints']))

    def _mask_source(self):
        import pcbnew as k
        import test_placement_native as fixture
        maker = fixture.NativePlacementTest()
        maker.root = self.root
        original = maker.board()
        board = k.LoadBoard(str(original))
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                layers = k.LSET()
                layers.AddLayer(k.F_Cu)
                layers.AddLayer(k.F_Mask)
                pad.SetLayerSet(layers)
                pad.SetLocalSolderMaskMargin(150000)
        k.SaveBoard(str(original), board)
        return original

    def _mask_candidate(self, source, name):
        import pcbnew as k
        work = self.root / name
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        board = k.LoadBoard(str(source))
        next(f for f in board.GetFootprints() if f.GetReference() == 'C1').SetPosition(k.VECTOR2I(3700000, 3000000))
        k.SaveBoard(str(output), board)
        return output

    def _share_pad_kiids(self, original, name, uppercase=False):
        import re
        text = original.read_text()
        first, second = re.findall(r'\(pad .*?\(uuid "([0-9a-f-]+)"\)', text, flags=re.S)[:2]
        source = self.root / f'{name}.kicad_pcb'
        source.write_text(text.replace(second, first.upper() if uppercase else first))
        return source

    def test_shared_kiid_across_footprints_refuses_mask_bridge(self):
        original = self._mask_source()
        for uppercase in (False, True):
            with self.subTest(uppercase=uppercase):
                name = f'shared-mask-{uppercase}'
                source = self._share_pad_kiids(original, name, uppercase)
                with self.assertRaisesRegex(ValueError, 'identities ambiguous'):
                    _proposal_drc(source, self._mask_candidate(source, name))

    def test_shared_kiid_across_moved_footprints_refuses(self):
        import pcbnew as k
        source = self._share_pad_kiids(self.board(), 'shared-collision')
        board = k.LoadBoard(str(source))
        for fp in board.GetFootprints():
            fp.Move(k.VECTOR2I(1000000, 0))
        work = self.root / 'shared-collision'
        work.mkdir()
        output = work / 'candidate.kicad_pcb'
        k.SaveBoard(str(output), board)
        with self.assertRaisesRegex(ValueError, 'identities ambiguous'):
            _proposal_drc(source, output)

    def test_rule_level_ignore_refuses(self):
        original = self._mask_source()
        for i, severity in enumerate(('(severity ignore)', '(severity\n    ignore)',
                                      '(SEVERITY IGNORE)', '(severity "ignore")')):
            with self.subTest(severity=severity):
                source = self.root / f'rule-ignore-{i}.kicad_pcb'
                source.write_bytes(original.read_bytes())
                source.with_suffix('.kicad_dru').write_text(
                    f'(version 1)\n(rule "allow"\n  {severity}\n  (constraint bridged_mask))\n')
                with self.assertRaisesRegex(ValueError, 'explicitly ignored'):
                    _proposal_drc(source, self._mask_candidate(source, f'rule-ignore-{i}'))
