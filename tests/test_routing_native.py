"""Owned native contact fixtures: explicit entry chains, never inferred copper."""
import tempfile
import unittest
from pathlib import Path

from agent_ee.routing_native import route_coverage
from tests.routing_fixture import make_board
from tests.toolchain import require_toolchain


class NativePadContactTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        import pcbnew as k
        self.k = k
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = self.root / 'contact.kicad_pcb'
        make_board(path, lengths=(10,), layers=4, net_names=('SIGNAL',))
        self.board = k.LoadBoard(str(path))
        for t in list(self.board.GetTracks()):
            self.board.Remove(t)
        self.pads = sorted((p for fp in self.board.GetFootprints() for p in fp.Pads()),
                           key=lambda p: p.GetPosition().x)
        for p in self.pads:
            p.SetShape(k.PAD_SHAPE_RECT)
            p.SetSize(k.VECTOR2I(1000000, 1000000))

    def track(self, a, b, net='SIGNAL'):
        k = self.k
        t = k.PCB_TRACK(self.board)
        t.SetNet(self.board.FindNet(net)); t.SetLayer(k.F_Cu); t.SetWidth(100000)
        t.SetStart(k.VECTOR2I(*(round(v * 1e6) for v in a)))
        t.SetEnd(k.VECTOR2I(*(round(v * 1e6) for v in b)))
        self.board.Add(t)
        return t

    def contact(self, result, index=0):
        return next(c for c in result['pad_contacts']
                    if c['pad_id'] == self.pads[index].m_Uuid.AsString())

    def coverage(self):
        result = route_coverage(self.board, 'SIGNAL')
        self.assertEqual(result['net'], 'SIGNAL')
        return result

    def test_offcentre_native_pad_contact_has_uuid_and_offset_evidence(self):
        t = self.track((.3, 2.2), (9.7, 2.2))
        result = self.coverage()
        self.assertTrue(result['measurement_qualified'], result)
        contact = self.contact(result)
        self.assertEqual(contact['pad_id'], self.pads[0].m_Uuid.AsString())
        self.assertEqual(contact['vertices'][0]['offset_nm'], [300000, 200000])
        self.assertEqual(contact['vertices'][0]['copper_ids'], [t.m_Uuid.AsString()])
        self.assertAlmostEqual(contact['vertices'][0]['offset_distance_nm'], 360555.1275464)
        self.assertEqual(contact['fully_in_pad_track_length_nm'], 0)
        self.assertTrue(set(result['copper_ids']) <= set(result['native_connected_ids']))
        self.assertAlmostEqual(t.GetLength(), 9400000)

    def test_pad_entry_tail_includes_saved_copper_without_centre_extension(self):
        entry = self.track((.4, 2), (9.6, 2))
        tail = self.track((.1, 2), (.4, 2))
        far_tail = self.track((9.6, 2), (9.9, 2))
        result = self.coverage()
        self.assertTrue(result['measurement_qualified'], result)
        self.assertEqual(result['in_pad_length_convention'],
                         'include all saved track copper; no pad-centre extension')
        self.assertEqual(self.contact(result)['fully_in_pad_copper_ids'],
                         [tail.m_Uuid.AsString()])
        self.assertEqual(self.contact(result, 1)['fully_in_pad_copper_ids'],
                         [far_tail.m_Uuid.AsString()])
        self.assertAlmostEqual(sum(c['fully_in_pad_track_length_nm']
                                   for c in result['pad_contacts']), 600000)
        self.assertAlmostEqual(sum(t.GetLength() for t in (entry, tail, far_tail)), 9800000)
        self.assertEqual(self.contact(result)['terminal_nm'], [100000, 2000000])

    def test_multiple_collinear_entry_segments(self):
        for a, b in zip((.1, .2, .4), (.2, .4, 9.8)):
            self.track((a, 2), (b, 2))
        result = self.coverage()
        self.assertTrue(result['measurement_qualified'], result)
        self.assertEqual(len(self.contact(result)['vertices']), 3)
        self.assertAlmostEqual(self.contact(result)['fully_in_pad_track_length_nm'], 300000)

    def test_rotated_oval_uses_native_shape_not_bounding_box(self):
        k = self.k
        pad = self.pads[0]
        pad.SetShape(k.PAD_SHAPE_OVAL); pad.SetSize(k.VECTOR2I(800000, 400000))
        pad.SetOrientationDegrees(90)
        t = self.track((.1, 2), (9.8, 2))
        self.assertTrue(self.coverage()['complete'])
        t.SetStart(k.VECTOR2I(190000, 2390000))
        self.assertFalse(self.coverage()['complete'])

    def test_endcap_only_contact_still_refuses(self):
        self.track((.51, 2), (9.8, 2))
        self.assertFalse(self.coverage()['complete'])

    def test_disconnected_in_pad_endpoints_are_ambiguous(self):
        self.track((.3, 2), (9.8, 2))
        self.track((0, 2.1), (0, 2.2))
        self.assertFalse(self.coverage()['complete'])

    def test_second_in_pad_branch_refuses(self):
        self.track((.1, 2), (.4, 2)); self.track((.4, 2), (9.8, 2))
        self.track((.4, 2), (.4, 2.2))
        self.assertFalse(self.coverage()['complete'])

    def test_leaving_and_reentering_terminal_refuses(self):
        points = [(.1, 2), (.4, 2), (.8, 2.4), (.4, 2.4), (9.8, 2.4)]
        for a, b in zip(points, points[1:]):
            self.track(a, b)
        self.assertFalse(self.coverage()['complete'])

    def test_loop_still_refuses(self):
        self.track((.2, 2), (9.8, 2))
        points = [(4, 4), (5, 4), (5, 5), (4, 5), (4, 4)]
        for a, b in zip(points, points[1:]):
            self.track(a, b)
        self.assertFalse(self.coverage()['complete'])

    def test_interior_crossing_still_refuses(self):
        points = [(.2, 2), (8, 8), (2, 8), (9.8, 2)]
        for a, b in zip(points, points[1:]):
            self.track(a, b)
        self.assertFalse(self.coverage()['complete'])

    def test_track_crossing_own_pad_without_endpoint_refuses(self):
        points = [(.2, 2), (2, 3), (-2, 3), (2, 1), (9.8, 2)]
        for a, b in zip(points, points[1:]):
            self.track(a, b)
        self.assertFalse(self.coverage()['complete'])

    def test_other_net_contact_refuses(self):
        k = self.k
        net = k.NETINFO_ITEM(self.board, 'OTHER'); self.board.Add(net)
        self.track((.2, 2), (9.8, 2))
        self.track((5, 1), (5, 3), 'OTHER')
        self.assertFalse(self.coverage()['complete'])

    def test_pth_and_multi_terminal_stay_unsupported(self):
        self.track((.2, 2), (9.8, 2))
        self.pads[0].SetAttribute(self.k.PAD_ATTRIB_PTH)
        self.assertFalse(self.coverage()['complete'])
        self.pads[0].SetAttribute(self.k.PAD_ATTRIB_SMD)
        fp = next(iter(self.board.GetFootprints()))
        pad = self.k.PAD(fp); fp.Add(pad); pad.SetNet(self.board.FindNet('SIGNAL'))
        self.assertFalse(self.coverage()['complete'])

    def test_offcentre_through_via_native_shape_contact(self):
        k = self.k
        self.track((.2, 2), (2, 2))
        middle = self.track((2.1, 2.1), (8, 2))
        middle.SetLayer(k.In1_Cu)
        self.track((8, 2), (9.8, 2))
        for x in (2, 8):
            via = k.PCB_VIA(self.board); via.SetNet(self.board.FindNet('SIGNAL'))
            via.SetPosition(k.VECTOR2I(x * 1000000, 2000000))
            via.SetWidth(600000); via.SetDrill(300000)
            via.SetViaType(k.VIATYPE_THROUGH); via.SetLayerPair(k.F_Cu, k.B_Cu)
            self.board.Add(via)
        result = self.coverage()
        self.assertTrue(result['measurement_qualified'], result)
        contact = next(c for c in result['junction_contacts'] if c['offset_nm'] != [0, 0])
        self.assertEqual(contact['track_id'], middle.m_Uuid.AsString())
        self.assertEqual(contact['offset_nm'], [100000, 100000])
        self.assertAlmostEqual(contact['offset_distance_nm'], 141421.3562373)
        middle.SetStart(k.VECTOR2I(2400000, 2400000))
        self.assertFalse(self.coverage()['complete'])

    def test_two_vias_at_one_endpoint_are_ambiguous(self):
        k = self.k
        self.track((.2, 2), (5, 2))
        middle = self.track((5.1, 2), (9.8, 2)); middle.SetLayer(k.In1_Cu)
        for x in (5, 5.2):
            via = k.PCB_VIA(self.board); via.SetNet(self.board.FindNet('SIGNAL'))
            via.SetPosition(k.VECTOR2I(round(x * 1e6), 2000000))
            via.SetWidth(600000); via.SetDrill(300000)
            via.SetViaType(k.VIATYPE_THROUGH); via.SetLayerPair(k.F_Cu, k.B_Cu)
            self.board.Add(via)
        result = self.coverage()
        self.assertFalse(result['complete'], result)
        self.assertIn('ambiguous', result['reason'])

    def test_native_drc_counts_entry_tail_length(self):
        import json
        import subprocess
        k = self.k
        self.track((.4, 2), (9.6, 2))
        self.track((.1, 2), (.4, 2)); self.track((9.6, 2), (9.9, 2))
        self.assertTrue(self.coverage()['measurement_qualified'])
        path = self.root / 'length.kicad_pcb'
        self.assertTrue(k.SaveBoard(str(path), self.board))
        path.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "entry-length" (condition "A.NetName == \'SIGNAL\'") '
            '(constraint length (max 9.5mm)))\n')
        out = self.root / 'drc.json'
        proc = subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json',
                               '--severity-all', '--units', 'mm', '-o', str(out), str(path)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        violations = json.loads(out.read_text())['violations']
        findings = [v for v in violations if v['type'] == 'length_out_of_range']
        self.assertEqual(len(findings), 1, violations)
        self.assertIn('9.8000 mm', findings[0]['description'])
