"""Native copper contact must be proved without inventing a shorter path."""
import importlib.util
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import pcbnew as k

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/acquisition-film/geometry_contact.py'
spec = importlib.util.spec_from_file_location('film_geometry_contact', SOURCE)
contact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contact)
NATIVE = Path(os.environ.get('AGENT_EE_FILM_NATIVE', '/nonexistent/film-native'))


def ground_reference():
    spec = importlib.util.spec_from_file_location('film_ground_reference', SOURCE.parent / 'ground_reference.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BottomCopperTests(unittest.TestCase):
    def test_via_without_a_bottom_land_cannot_join_separate_plane_regions(self):
        # Independent review 2026-11-01: every GND via was projected onto B.Cu.
        board = k.BOARD()
        board.SetCopperLayerCount(4)
        net = k.NETINFO_ITEM(board, 'GND', 1)
        board.Add(net)
        for x in (0, 3_000_000):
            zone = k.ZONE(board)
            zone.SetLayer(k.B_Cu)
            zone.SetNet(net)
            region = k.SHAPE_POLY_SET()
            region.NewOutline()
            for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                region.Append(x + u * 1_000_000, v * 1_000_000)
            zone.SetFilledPolysList(k.B_Cu, region)
            board.Add(zone)
        bridge = k.PCB_VIA(board)
        bridge.SetPosition(k.VECTOR2I(1_500_000, 0))
        bridge.SetWidth(2_400_000)
        bridge.SetDrill(300_000)
        bridge.SetNet(net)
        board.Add(bridge)
        reference = ground_reference()
        bridge.SetViaType(k.VIATYPE_BLIND)
        bridge.SetLayerPair(k.F_Cu, k.In1_Cu)
        self.assertEqual(reference.bottom_copper(board).OutlineCount(), 2)
        bridge.SetViaType(k.VIATYPE_THROUGH)
        bridge.SetLayerPair(k.F_Cu, k.B_Cu)
        self.assertEqual(reference.bottom_copper(board).OutlineCount(), 1)


class PadTrackContactTests(unittest.TestCase):
    def setUp(self):
        self.board = k.BOARD()
        self.board.SetCopperLayerCount(2)
        self.net = k.NETINFO_ITEM(self.board, '/VIN', 1)
        self.other_net = k.NETINFO_ITEM(self.board, '/OTHER', 2)
        self.board.Add(self.net)
        self.board.Add(self.other_net)
        fp = k.FOOTPRINT(self.board)
        fp.SetReference('C1')
        self.board.Add(fp)
        self.pad = k.PAD(fp)
        self.pad.SetNumber('1')
        self.pad.SetAttribute(k.PAD_ATTRIB_SMD)
        self.pad.SetShape(k.PAD_SHAPE_RECT)
        self.pad.SetSize(k.VECTOR2I(k.FromMM(1.4), k.FromMM(2.7)))
        self.pad.SetPosition(k.VECTOR2I(k.FromMM(10), k.FromMM(10)))
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        self.pad.SetLayerSet(layers)
        self.pad.SetNet(self.net)
        fp.Add(self.pad)
        self.track = k.PCB_TRACK(self.board)
        self.track.SetLayer(k.F_Cu)
        self.track.SetWidth(k.FromMM(.25))
        self.track.SetNet(self.net)
        self.board.Add(self.track)
        self.move_track(9.2)

    def move_track(self, x):
        self.track.SetStart(k.VECTOR2I(k.FromMM(x), k.FromMM(8.5)))
        self.track.SetEnd(k.VECTOR2I(k.FromMM(x), k.FromMM(11.5)))

    def test_edge_contact_uses_a_native_copper_witness_and_conservative_length(self):
        self.assertFalse(self.pad.HitTest(k.VECTOR2I(k.FromMM(9.2), k.FromMM(10))))
        result = contact.pad_track_contact(self.pad, self.track, k.F_Cu)
        self.assertGreater(result['contact_area_mm2'], 0)
        self.assertGreaterEqual(result['path_length_mm'], .8)
        point = k.VECTOR2I(*(round(x * 1e6) for x in result['contact_mm']))
        self.assertTrue(self.pad.HitTest(point))
        self.assertTrue(self.track.HitTest(point))
        self.assertAlmostEqual(result['path_length_mm'],
            math.dist((10, 10), result['contact_mm']) +
            math.dist(result['contact_mm'], result['track_point_mm']))

    def test_touch_gap_foreign_net_and_wrong_layer_do_not_create_connections(self):
        for x in (9.175, 9.174):
            with self.subTest(x=x):
                self.move_track(x)
                self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.move_track(9.2)
        self.track.SetNet(self.other_net)
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.track.SetNet(self.net)
        self.track.SetLayer(k.B_Cu)
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.F_Cu))
        self.assertIsNone(contact.pad_track_contact(self.pad, self.track, k.B_Cu))

    def test_through_hole_contact_is_not_treated_as_a_convex_smd_path(self):
        self.pad.SetAttribute(k.PAD_ATTRIB_PTH)
        self.pad.SetDrillSize(k.VECTOR2I(k.FromMM(.3), k.FromMM(.3)))
        with self.assertRaisesRegex(ValueError, 'convex SMD'):
            contact.pad_track_contact(self.pad, self.track, k.F_Cu)


@unittest.skipUnless((NATIVE / 'project/acquisition.kicad_pcb').is_file(), 'captured failing film board required')
class SavedBoardGeometryTests(unittest.TestCase):
    def test_corrected_witnesses_meet_original_limits_without_changing_the_board(self):
        from agent_ee import typed_overlay
        original = (NATIVE / 'project/acquisition.kicad_pcb').read_bytes()
        self.assertEqual(sha256(original).hexdigest(),
            '2040f20bd2eda776d37eb39a296bbc2c7429f61ff268aa0cbfe9b7df0cac38b1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(NATIVE / 'project', root / 'project')
            shutil.copyfile(NATIVE / 'groups.json', root / 'groups.json')
            (root / 'analysis').mkdir()
            for name in ('geometry_model.py', 'geometry_contact.py', 'geometry-model.json','ground_reference.py'):
                shutil.copyfile(SOURCE.parent / name, root / 'analysis' / name)
            output = root / 'geometry.json'
            result = subprocess.run([sys.executable, str(root / 'analysis/geometry_model.py'),
                str(root), str(output)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(output.read_text())
            self.assertEqual(observed['metrics']['ground_transition_missing'],3)
            limits = typed_overlay._check_pool(json.loads((NATIVE / 'requirement-map.json').read_text()))[
                'engineering-geometry']['params']['limits']
            for name, limit in limits.items():
                with self.subTest(metric=name):
                    if 'min' in limit:
                        self.assertGreaterEqual(observed['metrics'][name], limit['min'])
                    if 'max' in limit:
                        self.assertLessEqual(observed['metrics'][name], limit['max'])
            self.assertEqual((root / 'project/acquisition.kicad_pcb').read_bytes(), original)
            self.assertEqual(len(observed['parts']), 17)
            self.assertTrue(any(w['reference'] == 'C1' and w['pad'] == '1'
                                for w in observed['pad_track_contacts']))
        self.assertEqual((NATIVE / 'project/acquisition.kicad_pcb').read_bytes(), original)


@unittest.skipUnless((NATIVE / 'project/acquisition.kicad_pcb').is_file(), 'captured film board required')
class SeededGroundReferenceTests(unittest.TestCase):
    """Run the ground checker on a real routed board with its native J3 lands.

    Regression for 2026-11-01: 74 passing tests missed full-precision via
    coordinates that matched no graph node, and a round-land assumption that
    refused J3's oval pads. Both failures appeared only on a real board.
    """

    def setUp(self):
        from agent_ee.acquisition import ground_ties as ties
        self.ties = ties
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        shutil.copytree(NATIVE / 'project', self.root / 'project')
        shutil.copyfile(NATIVE / 'groups.json', self.root / 'groups.json')
        (self.root / 'analysis').mkdir()
        for name in ('geometry_model.py', 'geometry_contact.py', 'geometry-model.json', 'ground_reference.py'):
            shutil.copyfile(SOURCE.parent / name, self.root / 'analysis' / name)
        self.path = self.root / 'project/acquisition.kicad_pcb'
        data, self.receipt = ties.seed(self.path, self.path.read_bytes(), ties.POLICY)
        self.path.write_bytes(data)
        board = k.LoadBoard(str(self.path))
        k.ZONE_FILLER(board).Fill(board.Zones())
        k.SaveBoard(str(self.path), board)

    def measure(self):
        output = self.root / 'geometry.json'
        result = subprocess.run([sys.executable, str(self.root / 'analysis/geometry_model.py'),
            str(self.root), str(output)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(output.read_text())

    def test_local_ties_reach_both_connector_grounds_through_saved_copper(self):
        self.ties.verify(self.path, self.receipt)
        observed = self.measure()
        metrics = observed['metrics']
        self.assertEqual((metrics['ground_saved_net_changes'], metrics['ground_transition_missing'],
                          metrics['ground_corridor_missing'], metrics['ground_plane_components']), (0, 0, 0, 1))
        self.assertLessEqual(metrics['ground_return_path'], 3)
        rows = observed['ground_reference']['connections']
        self.assertEqual([row['endpoint'] for row in rows], self.ties.POLICY['endpoints'])
        for row in rows:
            self.assertEqual([c['connector'] for c in row['plane_corridors']], [['J3', '2'], ['J3', '4']])
            for corridor in row['plane_corridors']:
                self.assertEqual(corridor['status'], 'witnessed')
                self.assertGreater(corridor['conditional_path_ohm'], row['conditional_front_ohm'])
                self.assert_reported_path_is_saved_copper(corridor, row['endpoint'])
        self.assertGreaterEqual(metrics['plated_ring_minimum'], .18)
        rings = {(r['reference'], r['pad'], r['layer']): r for r in observed['ground_reference']['plated_rings']}
        self.assertGreaterEqual(rings[('U1', '9', 'B.Cu')]['ring_mm'], .25)
        self.assertTrue(rings[('U1', '9', 'F.Cu')]['at_ceiling'])

    def test_another_drill_through_the_copper_breaks_the_measured_ring(self):
        # Independent review 2026-11-01: other drills were not subtracted from the ring.
        reference = ground_reference()
        board = k.LoadBoard(str(self.path))
        regulator = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'U1')
        target = next(pad for pad in regulator.Pads() if pad.GetNumber() == '9' and pad.HasHole())
        ring = lambda: min(r['ring_mm'] for r in reference.plated_rings(board)
                           if r['reference'] == 'U1' and r['layer'] == 'B.Cu')
        self.assertGreaterEqual(ring(), .25)
        hole = k.PAD(regulator)
        hole.SetNumber('')
        hole.SetAttribute(k.PAD_ATTRIB_NPTH)
        hole.SetShape(k.PAD_SHAPE_CIRCLE)
        hole.SetSize(k.VECTOR2I(200_000, 200_000))
        hole.SetDrillSize(k.VECTOR2I(200_000, 200_000))
        hole.SetPosition(target.GetPosition() + k.VECTOR2I(400_000, 0))
        regulator.Add(hole)
        self.assertLess(ring(), .15)
        target.SetSecondaryDrillSize(k.VECTOR2I(700_000, 700_000))
        with self.assertRaisesRegex(ValueError, 'centred round drill'):
            reference.plated_rings(board)

    def test_supplier_land_alone_falls_below_the_plated_ring_minimum(self):
        # Full review 2026-11-01: 0.1524 mm is below the captured 0.18 mm PTH minimum.
        board = k.LoadBoard(str(self.path))
        for bar in self.receipt['bars']:
            board.Remove(next(t for t in board.GetTracks() if t.m_Uuid.AsString() == bar['segment_uuid']))
        k.SaveBoard(str(self.path), board)
        ring = self.measure()['metrics']['plated_ring_minimum']
        self.assertLess(ring, .18)
        self.assertGreater(ring, .15)

    def assert_reported_path_is_saved_copper(self, corridor, endpoint):
        """Re-prove the reported points, independently of the search that found them."""
        board = k.LoadBoard(str(self.path))
        strip = k.SHAPE_POLY_SET()
        points = [k.VECTOR2I(*point) for point in corridor['points_nm']]
        for a, b in zip(points, points[1:]):
            track = k.PCB_TRACK(board)
            track.SetStart(a)
            track.SetEnd(b)
            track.SetWidth(round(corridor['width_mm'] * 1e6))
            track.SetLayer(k.B_Cu)
            track.TransformShapeToPolygon(strip, k.B_Cu, 0, 1, k.ERROR_OUTSIDE)
        strip.Simplify()
        strip.BooleanSubtract(ground_reference().bottom_copper(board))
        strip.Simplify()
        self.assertTrue(strip.IsEmpty() or strip.Area() == 0, endpoint)
        fp = next(f for f in board.GetFootprints() if f.GetReference() == 'J3')
        pad = next(p for p in fp.Pads() if p.GetNumber() == corridor['connector'][1])
        self.assertTrue(pad.HitTest(points[-1]))

    def test_removed_tie_measures_the_longer_real_path_and_exceeds_the_limit(self):
        board = k.LoadBoard(str(self.path))
        identity = self.receipt['connections'][0]['via_uuid']
        board.Remove(next(t for t in board.GetTracks() if t.m_Uuid.AsString() == identity))
        k.SaveBoard(str(self.path), board)
        observed = self.measure()
        row = observed['ground_reference']['connections'][0]
        self.assertEqual(row['endpoint'], ['U2', '3'])
        self.assertNotEqual(row['via_uuid'], identity)
        self.assertGreater(observed['metrics']['ground_return_path'], 3)

    def test_backdrilled_tie_does_not_count_as_a_plane_transition(self):
        # Independent review 2026-11-01: drilling out the barrel can disconnect the layers.
        identity = self.receipt['connections'][0]['via_uuid']
        marker = b'(uuid "' + identity.encode() + b'")'
        raw = self.path.read_bytes()
        self.assertEqual(raw.count(marker), 1)
        self.path.write_bytes(raw.replace(marker, b'(backdrill (size 0.8) (layers "B.Cu" "F.Cu"))\n' + marker))
        observed = self.measure()
        self.assertNotEqual(observed['ground_reference']['connections'][0].get('via_uuid'), identity)
        self.assertGreater(observed['metrics']['ground_return_path'], 3)

    def test_missing_plane_leaves_no_corridor_witness(self):
        board = k.LoadBoard(str(self.path))
        planes = [z for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == 'GND']
        self.assertTrue(planes)
        for zone in planes:
            board.Remove(zone)
        k.SaveBoard(str(self.path), board)
        metrics = self.measure()['metrics']
        self.assertEqual(metrics['ground_corridor_missing'], 6)
        self.assertGreater(metrics['ground_plane_components'], 1)

    def test_persisted_foreign_via_net_is_counted_even_after_native_inference(self):
        identity = self.receipt['connections'][0]['via_uuid'].encode()
        raw = self.path.read_bytes()
        end = raw.index(identity)
        start = raw.rindex(b'(via', 0, end)
        self.assertEqual(raw[start:end].count(b'(net "GND")'), 1)
        self.path.write_bytes(raw[:start] + raw[start:end].replace(b'(net "GND")', b'(net "/V3V3")') + raw[end:])
        self.assertEqual(self.measure()['metrics']['ground_saved_net_changes'], 1)

    def test_connector_ports_lie_on_the_pad_and_other_lands_refuse(self):
        # Independent review 2026-11-01: a front-only SMD pad at J3 still passed.
        reference = ground_reference()
        board = k.LoadBoard(str(self.path))
        connector = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'J3')
        pads = {pad.GetNumber(): pad for pad in connector.Pads()}
        for number in ('2', '4'):
            pad = pads[number]
            center, drill = pad.GetPosition(), pad.GetDrillSize().x
            for x, y in reference.annulus_ports(pad):
                self.assertTrue(pad.HitTest(k.VECTOR2I(x, y)))
                self.assertGreater(math.dist((x, y), (center.x, center.y)), drill / 2)

        def oblong_drill(pad):
            pad.SetDrillSize(k.VECTOR2I(750_000, 900_000))

        def rectangular_land(pad):
            pad.SetShape(k.PAD_SHAPE_RECT)

        def foreign_net(pad):
            pad.SetNet(board.FindNet('/V3V3'))

        def front_only_smd(pad):
            layers = k.LSET()
            layers.AddLayer(k.F_Cu)
            pad.SetAttribute(k.PAD_ATTRIB_SMD)
            pad.SetLayerSet(layers)
            pad.SetDrillSize(k.VECTOR2I(0, 0))

        for change in (oblong_drill, rectangular_land, foreign_net, front_only_smd):
            with self.subTest(change=change.__name__):
                pad = k.PAD(pads['2'])
                change(pad)
                with self.assertRaisesRegex(ValueError, 'plated GND land'):
                    reference.annulus_ports(pad)


if __name__ == '__main__':
    unittest.main()
