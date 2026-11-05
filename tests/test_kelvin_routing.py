"""Tests for Kelvin sense connection routing verification."""

import math
import unittest
from pathlib import Path

from agent_ee.kelvin_routing import (
    describe_kelvin_routing,
    evaluate_board,
    evaluate_leg,
    SCHEMA,
    SHARED_COPPER_THRESHOLD_MM,
    _pad_bbox_distance,
)


class MockVector:
    def __init__(self, x_nm, y_nm):
        self.x = x_nm
        self.y = y_nm


class MockAngle:
    def __init__(self, degrees):
        self._deg = float(degrees)

    def AsDegrees(self):
        return self._deg

    def AsRadians(self):
        return math.radians(self._deg)


class MockShape:
    def __init__(self, center_nm, size_nm):
        self.cx = center_nm[0]
        self.cy = center_nm[1]
        self.sx = size_nm[0]
        self.sy = size_nm[1]

    def Collide(self, pt, margin=0):
        dx = max(0, abs(pt.x - self.cx) - self.sx // 2)
        dy = max(0, abs(pt.y - self.cy) - self.sy // 2)
        return math.hypot(dx, dy) <= margin


class MockPad:
    def __init__(self, ref, number, x_mm, y_mm, size_mm, netname, orientation=0.0, raw_orientation=False):
        self.ref = ref
        self.number = str(number)
        self.pos_nm = (int(round(x_mm * 1e6)), int(round(y_mm * 1e6)))
        self.size_nm = (int(round(size_mm[0] * 1e6)), int(round(size_mm[1] * 1e6)))
        self.netname = netname
        self.radius_nm = int(round(max(size_mm) * 0.5 * 1e6))
        if raw_orientation:
            self.orientation = orientation
        elif isinstance(orientation, (int, float)):
            self.orientation = MockAngle(orientation)
        else:
            self.orientation = orientation

    def GetNumber(self):
        return self.number

    def GetName(self):
        return self.number

    def GetPosition(self):
        return MockVector(*self.pos_nm)

    def GetSize(self):
        return MockVector(*self.size_nm)

    def GetNetname(self):
        return self.netname

    def GetOrientation(self):
        return self.orientation

    def GetLayerSet(self):
        class Layers:
            def CuStack(self):
                return [0, 31]
        return Layers()

    def GetEffectiveShape(self, layer=None):
        return MockShape(self.pos_nm, self.size_nm)


class MockTrack:
    def __init__(self, start_mm, end_mm, width_mm, netname, track_class="PCB_TRACK"):
        self.start_nm = (int(round(start_mm[0] * 1e6)), int(round(start_mm[1] * 1e6)))
        self.end_nm = (int(round(end_mm[0] * 1e6)), int(round(end_mm[1] * 1e6)))
        self.width_nm = int(round(width_mm * 1e6))
        self.netname = netname
        self.track_class = track_class

    def GetClass(self):
        return self.track_class

    def GetStart(self):
        return MockVector(*self.start_nm)

    def GetEnd(self):
        return MockVector(*self.end_nm)

    def GetNetname(self):
        return self.netname

    def GetWidth(self):
        return self.width_nm


class MockFootprint:
    def __init__(self, ref, pads):
        self.ref = ref
        self._pads = pads

    def GetReference(self):
        return self.ref

    def Pads(self):
        return self._pads


class MockBoard:
    def __init__(self, footprints, tracks):
        self._fps = footprints
        self._tracks = tracks

    def GetFootprints(self):
        return self._fps

    def GetTracks(self):
        return self._tracks


class KelvinRoutingTests(unittest.TestCase):
    def test_direct_kelvin_routes_pass(self):
        # Shunt R1: pad 1 at (10, 10), pad 2 at (10, 14)
        r1_pad1 = MockPad("R1", 1, 10.0, 10.0, (1.2, 2.0), "SOURCE_12V")
        r1_pad2 = MockPad("R1", 2, 10.0, 14.0, (1.2, 2.0), "LOAD_12V")
        fp_r1 = MockFootprint("R1", [r1_pad1, r1_pad2])

        # Monitor U1: pad 8 (IN+) at (25, 10), pad 7 (IN-) at (25, 14)
        u1_pad8 = MockPad("U1", 8, 25.0, 10.0, (0.6, 0.6), "SOURCE_12V")
        u1_pad7 = MockPad("U1", 7, 25.0, 14.0, (0.6, 0.6), "LOAD_12V")
        fp_u1 = MockFootprint("U1", [u1_pad8, u1_pad7])

        # Tracks on LOAD_12V:
        # 1. Sense track directly from U1.7 to R1.2
        t_sense = MockTrack((25.0, 14.0), (10.0, 14.0), 0.2, "LOAD_12V")
        # 2. Load current track from J2.1 to R1.2 (meeting at R1.2 pad center)
        t_load = MockTrack((0.0, 14.0), (10.0, 14.0), 0.5, "LOAD_12V")

        # Tracks on SOURCE_12V:
        t_src_sense = MockTrack((25.0, 10.0), (10.0, 10.0), 0.2, "SOURCE_12V")
        t_src_power = MockTrack((0.0, 10.0), (10.0, 10.0), 0.5, "SOURCE_12V")

        board = MockBoard([fp_r1, fp_u1], [t_sense, t_load, t_src_sense, t_src_power])

        declaration = {
            "schema": "agent-ee.kelvin-senses/1",
            "items": [{
                "name": "LOAD_CURRENT_SENSE",
                "shunt": "R1",
                "device": "U1",
                "positive": ["U1.8", "R1.1"],
                "negative": ["U1.7", "R1.2"]
            }]
        }

        report = evaluate_board(board, declaration)
        self.assertEqual(report["outcome"], "pass")
        self.assertEqual(len(report["findings"]), 0)
        self.assertEqual(report["items"][0]["status"], "pass")

    def test_shared_copper_branch_fails_with_defect(self):
        # Reproduces Defect #13:
        # Shunt R1 pad 2 at (121.31, 108.55)
        # T-junction at (121.3, 113.5), ~3.275 mm outside pad boundary
        r1_pad1 = MockPad("R1", 1, 121.31, 100.0, (1.2, 3.35), "SOURCE_12V")
        r1_pad2 = MockPad("R1", 2, 121.31, 108.55, (1.2, 3.35), "LOAD_12V")
        fp_r1 = MockFootprint("R1", [r1_pad1, r1_pad2])

        u1_pad8 = MockPad("U1", 8, 140.0, 100.0, (0.6, 0.6), "SOURCE_12V")
        u1_pad7 = MockPad("U1", 7, 140.0, 113.5, (0.6, 0.6), "LOAD_12V")
        fp_u1 = MockFootprint("U1", [u1_pad8, u1_pad7])

        # Positive side clean
        t_src_sense = MockTrack((140.0, 100.0), (121.31, 100.0), 0.2, "SOURCE_12V")

        # Negative side with external T-junction at (121.3, 113.5)
        # 1. Sense track from U1.7 to junction
        t_sense = MockTrack((140.0, 113.5), (121.3, 113.5), 0.2, "LOAD_12V")
        # 2. Track from load connector to junction
        t_load = MockTrack((100.0, 113.5), (121.3, 113.5), 0.5, "LOAD_12V")
        # 3. Shared track from junction to R1.2 pad
        t_shared = MockTrack((121.3, 113.5), (121.31, 108.55), 0.2, "LOAD_12V")

        board = MockBoard([fp_r1, fp_u1], [t_src_sense, t_sense, t_load, t_shared])

        declaration = {
            "schema": "agent-ee.kelvin-senses/1",
            "items": [{
                "name": "LOAD_CURRENT_SENSE",
                "shunt": "R1",
                "device": "U1",
                "positive": ["U1.8", "R1.1"],
                "negative": ["U1.7", "R1.2"]
            }]
        }

        report = evaluate_board(board, declaration)
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual(len(report["findings"]), 1)
        finding = report["findings"][0]
        self.assertEqual(finding["id"], "circuit-shared-sense-copper")
        self.assertEqual(finding["severity"], "defect")
        self.assertGreater(finding["shared_length_mm"], 2.0)
    def test_disconnected_sense_trace_fails_with_defect(self):
        r1_pad1 = MockPad("R1", 1, 10.0, 10.0, (1.2, 2.0), "SOURCE_12V")
        r1_pad2 = MockPad("R1", 2, 10.0, 14.0, (1.2, 2.0), "LOAD_12V")
        fp_r1 = MockFootprint("R1", [r1_pad1, r1_pad2])

        u1_pad8 = MockPad("U1", 8, 25.0, 10.0, (0.6, 0.6), "SOURCE_12V")
        u1_pad7 = MockPad("U1", 7, 25.0, 14.0, (0.6, 0.6), "LOAD_12V")
        fp_u1 = MockFootprint("U1", [u1_pad8, u1_pad7])

        # Positive sense is routed, but negative sense trace is disconnected
        t_src_sense = MockTrack((25.0, 10.0), (10.0, 10.0), 0.2, "SOURCE_12V")
        t_load = MockTrack((0.0, 14.0), (10.0, 14.0), 0.5, "LOAD_12V")

        board = MockBoard([fp_r1, fp_u1], [t_src_sense, t_load])

        declaration = {
            "schema": "agent-ee.kelvin-senses/1",
            "items": [{
                "name": "LOAD_CURRENT_SENSE",
                "shunt": "R1",
                "device": "U1",
                "positive": ["U1.8", "R1.1"],
                "negative": ["U1.7", "R1.2"]
            }]
        }

        report = evaluate_board(board, declaration)
        self.assertEqual(report["outcome"], "defect")
        neg_leg = report["items"][0]["legs"]["negative"]
        self.assertEqual(neg_leg["status"], "defect")
        self.assertEqual(neg_leg["reason"], "unrouted_sense_trace")

    def test_rectangular_pad_aspect_ratio_shared_copper_defect(self):
        # 2512 package shunt pad (1.2 mm x 3.35 mm). Half-width in X is 0.6 mm.
        shunt_pad = MockPad("R1", 1, 10.0, 10.0, (1.2, 3.35), "SOURCE_12V")
        dev_pad = MockPad("U1", 1, 25.0, 10.0, (0.5, 0.5), "SOURCE_12V")

        # Junction at (11.2, 10.0) is 0.6 mm outside copper on the narrow side
        t_shared = MockTrack((10.0, 10.0), (11.2, 10.0), 0.5, "SOURCE_12V")
        t_load = MockTrack((0.0, 10.0), (11.2, 10.0), 0.5, "SOURCE_12V")
        t_sense = MockTrack((25.0, 10.0), (11.2, 10.0), 0.2, "SOURCE_12V")

        board = MockBoard([], [t_shared, t_load, t_sense])
        res = evaluate_leg(board, dev_pad, shunt_pad, "SOURCE_12V")
        self.assertEqual(res["status"], "defect")
        self.assertEqual(res["reason"], "circuit-shared-sense-copper")
        self.assertAlmostEqual(res["shared_length_mm"], 0.6, places=2)

    def test_downstream_power_branch_passes(self):
        # Shunt R1 pad at (10, 10). Direct Kelvin connection to U1 at (20, 10)
        shunt_pad = MockPad("R1", 1, 10.0, 10.0, (1.0, 1.0), "SOURCE_12V")
        dev_pad = MockPad("U1", 1, 20.0, 10.0, (0.5, 0.5), "SOURCE_12V")
        t_sense = MockTrack((20.0, 10.0), (10.0, 10.0), 0.2, "SOURCE_12V")
        t_power = MockTrack((0.0, 10.0), (10.0, 10.0), 0.5, "SOURCE_12V")
        # Unrelated branch 10 mm away on power net (e.g. bulk cap or TVS diode)
        t_branch1 = MockTrack((0.0, 10.0), (0.0, 20.0), 0.5, "SOURCE_12V")
        t_branch2 = MockTrack((0.0, 10.0), (0.0, 0.0), 0.5, "SOURCE_12V")
        board = MockBoard([], [t_sense, t_power, t_branch1, t_branch2])
        res = evaluate_leg(board, dev_pad, shunt_pad, "SOURCE_12V")
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["reason"], "kelvin_sense_direct_land_connection_verified")
        self.assertEqual(res["shared_length_mm"], 0.0)

    def test_missing_footprint_pad_fails_with_defect(self):
        r1_pad1 = MockPad("R1", 1, 10.0, 10.0, (1.2, 2.0), "SOURCE_12V")
        r1_pad2 = MockPad("R1", 2, 10.0, 14.0, (1.2, 2.0), "LOAD_12V")
        fp_r1 = MockFootprint("R1", [r1_pad1, r1_pad2])

        # U1 only has pad 8; pad 7 is missing from footprint
        u1_pad8 = MockPad("U1", 8, 25.0, 10.0, (0.6, 0.6), "SOURCE_12V")
        fp_u1 = MockFootprint("U1", [u1_pad8])

        board = MockBoard([fp_r1, fp_u1], [])

        declaration = {
            "schema": "agent-ee.kelvin-senses/1",
            "items": [{
                "name": "LOAD_CURRENT_SENSE",
                "shunt": "R1",
                "device": "U1",
                "positive": ["U1.8", "R1.1"],
                "negative": ["U1.7", "R1.2"]
            }]
        }

        report = evaluate_board(board, declaration)
        self.assertEqual(report["outcome"], "defect")
        neg_leg = report["items"][0]["legs"]["negative"]
        self.assertEqual(neg_leg["status"], "defect")
        self.assertEqual(neg_leg["reason"], "missing_pad")
        finding = [f for f in report["findings"] if "U1.7" in f["location"]][0]
        self.assertNotIn("None mm", finding["condition"])

    def test_unrouted_net_formatting_no_none_mm(self):
        r1_pad1 = MockPad("R1", 1, 10.0, 10.0, (1.2, 2.0), "SOURCE_12V")
        r1_pad2 = MockPad("R1", 2, 10.0, 14.0, (1.2, 2.0), "LOAD_12V")
        fp_r1 = MockFootprint("R1", [r1_pad1, r1_pad2])

        u1_pad8 = MockPad("U1", 8, 25.0, 10.0, (0.6, 0.6), "SOURCE_12V")
        u1_pad7 = MockPad("U1", 7, 25.0, 14.0, (0.6, 0.6), "LOAD_12V")
        fp_u1 = MockFootprint("U1", [u1_pad8, u1_pad7])

        # No tracks at all
        board = MockBoard([fp_r1, fp_u1], [])

        declaration = {
            "schema": "agent-ee.kelvin-senses/1",
            "items": [{
                "name": "LOAD_CURRENT_SENSE",
                "shunt": "R1",
                "device": "U1",
                "positive": ["U1.8", "R1.1"],
                "negative": ["U1.7", "R1.2"]
            }]
        }

        report = evaluate_board(board, declaration)
        self.assertEqual(report["outcome"], "defect")
        for finding in report["findings"]:
            self.assertNotIn("None mm", finding["condition"])

    def test_describe_kelvin_routing(self):
        class MockCtx:
            def __init__(self, params):
                self.params = params
                self.scope = {"board": "project/board.kicad_pcb"}
                self.inputs = None
                self.result = {}
                self._captured = []

            def captured(self, item, immutable=False):
                self._captured.append((item, immutable))

            def apply(self, desc):
                self.result.update(desc)

        # Missing declaration raises ValueError
        with self.assertRaises(ValueError):
            describe_kelvin_routing(MockCtx({}))

        # Valid declaration registers predicates
        ctx = MockCtx({"declaration": "placement-kelvin.json"})
        describe_kelvin_routing(ctx)
        self.assertIn("kelvin-routing:circuit-shared-sense-copper", ctx.result["predicates"])
        self.assertIn("placement-kelvin.json", ctx.result["immutable_inputs"])

    def test_rotated_pad_bbox_distance_90_and_270(self):
        # 2512 package (1.2 x 3.35 mm) centered at (100, 100)
        # Rotated 90°: long axis is along world X (half-width 1.675), narrow axis along world Y (half-height 0.6)
        pad_90 = MockPad("R1", 1, 100.0, 100.0, (1.2, 3.35), "SOURCE_12V", orientation=90.0)
        # Point on rotated copper at (101.0, 100.0) -> offset 1.0 mm <= 1.675 mm
        self.assertLessEqual(_pad_bbox_distance(pad_90, (101.0, 100.0)), SHARED_COPPER_THRESHOLD_MM)
        # External point 0.4 mm outside rotated copper along Y at (100.0, 101.0) -> offset 1.0 mm > 0.6 mm
        dist_outside_90 = _pad_bbox_distance(pad_90, (100.0, 101.0))
        self.assertAlmostEqual(dist_outside_90, 0.4, places=2)
        self.assertGreaterEqual(dist_outside_90, 0.35)

        # Rotated 270°: same rotated geometry
        pad_270 = MockPad("R1", 1, 100.0, 100.0, (1.2, 3.35), "SOURCE_12V", orientation=270.0)
        self.assertLessEqual(_pad_bbox_distance(pad_270, (101.0, 100.0)), SHARED_COPPER_THRESHOLD_MM)
        dist_outside_270 = _pad_bbox_distance(pad_270, (100.0, 101.0))
        self.assertAlmostEqual(dist_outside_270, 0.4, places=2)
        self.assertGreaterEqual(dist_outside_270, 0.35)

    def test_rotated_shunt_pad_90_and_270_evaluate_leg(self):
        for angle in (90.0, 270.0):
            # 1. Clean direct connection to rotated shunt copper (no false defect)
            shunt_pad = MockPad("R1", 1, 100.0, 100.0, (1.2, 3.35), "SOURCE_12V", orientation=angle)
            dev_pad = MockPad("U1", 1, 120.0, 100.0, (0.5, 0.5), "SOURCE_12V")
            # Sense trace lands at (101.0, 100.0) on the pad copper
            t_sense = MockTrack((120.0, 100.0), (101.0, 100.0), 0.2, "SOURCE_12V")
            t_power = MockTrack((80.0, 100.0), (101.0, 100.0), 0.5, "SOURCE_12V")
            board_clean = MockBoard([], [t_sense, t_power])
            res_clean = evaluate_leg(board_clean, dev_pad, shunt_pad, "SOURCE_12V")
            self.assertEqual(res_clean["status"], "pass")
            self.assertEqual(res_clean["reason"], "kelvin_sense_direct_land_connection_verified")
            self.assertEqual(res_clean["shared_length_mm"], 0.0)

            # 2. External junction 0.4 mm outside rotated copper (no false pass)
            # Junction at (100.0, 101.0) is 0.4 mm outside copper along Y
            t_sense_bad = MockTrack((120.0, 100.0), (100.0, 101.0), 0.2, "SOURCE_12V")
            t_power_bad = MockTrack((80.0, 101.0), (100.0, 101.0), 0.5, "SOURCE_12V")
            t_shared = MockTrack((100.0, 101.0), (100.0, 100.0), 0.5, "SOURCE_12V")
            board_defect = MockBoard([], [t_sense_bad, t_power_bad, t_shared])
            res_defect = evaluate_leg(board_defect, dev_pad, shunt_pad, "SOURCE_12V")
            self.assertEqual(res_defect["status"], "defect")
            self.assertEqual(res_defect["reason"], "circuit-shared-sense-copper")
            self.assertAlmostEqual(res_defect["shared_length_mm"], 0.4, places=2)

    def test_pcb_arc_track_support(self):
        # Verify PCB_ARC class tracks are traversed as valid track segments
        shunt_pad = MockPad("R1", 1, 10.0, 10.0, (1.0, 1.0), "SOURCE_12V")
        dev_pad = MockPad("U1", 1, 20.0, 10.0, (0.5, 0.5), "SOURCE_12V")
        t_arc = MockTrack((20.0, 10.0), (10.0, 10.0), 0.2, "SOURCE_12V", track_class="PCB_ARC")
        t_power = MockTrack((0.0, 10.0), (10.0, 10.0), 0.5, "SOURCE_12V")
        board = MockBoard([], [t_arc, t_power])
        res = evaluate_leg(board, dev_pad, shunt_pad, "SOURCE_12V")
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["reason"], "kelvin_sense_direct_land_connection_verified")

    def test_rotated_pad_bbox_distance_non_cardinal_and_negative_angles(self):
        angles = [30.0, 45.0, 135.0, 225.0, 315.0, -45.0, -90.0]
        for angle in angles:
            for raw in (False, True):
                pad = MockPad("R1", 1, 100.0, 100.0, (1.2, 3.35), "SOURCE_12V", orientation=angle, raw_orientation=raw)
                rad = math.radians(angle)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)

                # Point strictly on copper: local (u, v) = (0.2, 0.8) mm (inside half-width 0.6 and half-height 1.675)
                # World displacement for +Y down screen coordinates: dx = u*cos + v*sin, dy = -u*sin + v*cos
                u_on, v_on = 0.2, 0.8
                pt_on = (100.0 + u_on * cos_a + v_on * sin_a, 100.0 - u_on * sin_a + v_on * cos_a)
                dist_on = _pad_bbox_distance(pad, pt_on)
                self.assertLessEqual(dist_on, SHARED_COPPER_THRESHOLD_MM, f"Failed on-copper distance for angle {angle}")

                # Point 0.4 mm outside copper along positive short axis (local X): local (1.0, 0.0) mm
                pt_out_pos = (100.0 + 1.0 * cos_a, 100.0 - 1.0 * sin_a)
                dist_out_pos = _pad_bbox_distance(pad, pt_out_pos)
                self.assertGreaterEqual(dist_out_pos, 0.35, f"Failed outside short axis distance for angle {angle}")
                self.assertAlmostEqual(dist_out_pos, 0.4, places=2)

                # Point 0.4 mm outside copper along negative short axis (local X): local (-1.0, 0.0) mm
                pt_out_neg = (100.0 - 1.0 * cos_a, 100.0 + 1.0 * sin_a)
                dist_out_neg = _pad_bbox_distance(pad, pt_out_neg)
                self.assertGreaterEqual(dist_out_neg, 0.35, f"Failed outside short axis distance for angle {angle}")
                self.assertAlmostEqual(dist_out_neg, 0.4, places=2)

    def test_rotated_shunt_pad_30_and_45_evaluate_leg(self):
        for angle in (30.0, 45.0):
            shunt_pad = MockPad("R1", 1, 100.0, 100.0, (1.2, 3.35), "SOURCE_12V", orientation=angle)
            dev_pad = MockPad("U1", 1, 120.0, 100.0, (0.5, 0.5), "SOURCE_12V")
            rad = math.radians(angle)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)

            # 1. Direct route to rotated shunt copper (status: pass, shared_length_mm: 0.0)
            u_on, v_on = 0.2, 0.8
            pt_land = (round(100.0 + u_on * cos_a + v_on * sin_a, 3), round(100.0 - u_on * sin_a + v_on * cos_a, 3))
            t_sense = MockTrack((120.0, 100.0), pt_land, 0.2, "SOURCE_12V")
            t_power = MockTrack((80.0, 100.0), pt_land, 0.5, "SOURCE_12V")
            board_clean = MockBoard([], [t_sense, t_power])
            res_clean = evaluate_leg(board_clean, dev_pad, shunt_pad, "SOURCE_12V")
            self.assertEqual(res_clean["status"], "pass")
            self.assertEqual(res_clean["reason"], "kelvin_sense_direct_land_connection_verified")
            self.assertEqual(res_clean["shared_length_mm"], 0.0)

            # 2. External T-junction 0.4 mm outside copper along short axis
            # (status: defect, circuit-shared-sense-copper, shared_length_mm approx 0.4 mm)
            pt_ext = (round(100.0 + 1.0 * cos_a, 3), round(100.0 - 1.0 * sin_a, 3))
            t_sense_bad = MockTrack((120.0, 100.0), pt_ext, 0.2, "SOURCE_12V")
            t_power_bad = MockTrack((80.0, 100.0), pt_ext, 0.5, "SOURCE_12V")
            t_shared = MockTrack(pt_ext, (100.0, 100.0), 0.5, "SOURCE_12V")
            board_defect = MockBoard([], [t_sense_bad, t_power_bad, t_shared])
            res_defect = evaluate_leg(board_defect, dev_pad, shunt_pad, "SOURCE_12V")
            self.assertEqual(res_defect["status"], "defect")
            self.assertEqual(res_defect["reason"], "circuit-shared-sense-copper")
            self.assertAlmostEqual(res_defect["shared_length_mm"], 0.4, places=2)
