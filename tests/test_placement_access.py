"""AGENTEE-386 (plan M3a): native-qualified local access witnesses and unknowns.

Every native fixture is original: an authored pcbnew board (rotated and
mirrored footprints, slots, NPTH, a custom pad, same-number pads, keepouts, an
existing track and via, a narrow escape and an enclosed terminal) saved and
reloaded in KiCad, captured by ``placement_probe.routing_geometry``. Witnesses
are materialized on isolated copies with unchanged rules and checked by
kicad-cli DRC. The actual U23/C87 replay reads the pinned fome-mc store; no pod
design file is copied into this repository.
"""
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from toolchain import fome_mc_store, require_toolchain

from agent_ee import blocks, placement_access as access, placer
from agent_ee.placement_probe import routing_geometry

MM = 1_000_000
POLICY = {'track_width_nm': 200_000, 'routing_layers': ['F.Cu', 'B.Cu'],
          'vias': [{'diameter_nm': 600_000, 'drill_nm': 300_000}]}
R = 'roundrect'


def pad(number, net, at, size, shape='rect', attr='smd', drill=None, **extra):
    return {'n': number, 'net': net, 'at': at, 'size': size, 'shape': shape, 'attr': attr, 'drill': drill, **extra}


def ring(net, half, wall=0.4):
    """A closed square of four same-net wall pads whose inner edges are ``half`` mm from the centre."""
    centre, length = half + wall / 2, 2 * (half + wall)
    return [pad('2', net, [-centre, 0], [wall, length]), pad('3', net, [centre, 0], [wall, length]),
            pad('4', net, [0, -centre], [length, wall]), pad('5', net, [0, centre], [length, wall])]


SPEC = {
    'outline': [0, 0, 40, 30],
    'footprints': [
        {'ref': 'U1', 'at': [8, 8], 'rot': 30, 'pads': [
            *[pad(str(i + 1), f'S{i + 1}', [-0.75 + 0.5 * i, -1.2], [0.28, 0.85], R) for i in range(4)],
            *[pad(str(i + 5), ('S5', 'S6', 'S7', 'GND')[i], [-0.75 + 0.5 * i, 1.2], [0.28, 0.85], R) for i in range(4)],
            # Same-number exposed-pad pieces that touch: one terminal component, two UUIDs.
            pad('9', 'GND', [-0.4, 0], [0.8, 1.2]), pad('9', 'GND', [0.4, 0], [0.8, 1.2]),
            # Same-number pads that do not touch: two components.
            pad('10', 'GND', [-1.7, 0], [0.3, 0.3]), pad('10', 'GND', [1.7, 0], [0.3, 0.3])]},
        {'ref': 'R1', 'at': [14, 6], 'rot': 0, 'pads': [
            pad('1', 'S1', [-0.775, 0], [0.9, 0.95], R), pad('2', 'S2', [0.775, 0], [0.9, 0.95], R)]},
        {'ref': 'R2', 'at': [14, 11], 'rot': 90, 'pads': [
            pad('1', 'S3', [-0.775, 0], [0.9, 0.95], R), pad('2', 'S4', [0.775, 0], [0.9, 0.95], R)]},
        {'ref': 'R3', 'at': [4, 14], 'rot': 0, 'pads': [
            pad('1', 'S5', [-0.775, 0], [0.9, 0.95], R), pad('2', 'S6', [0.775, 0], [0.9, 0.95], R)]},
        {'ref': 'R4', 'at': [10, 14], 'rot': 0, 'pads': [
            pad('1', 'S7', [-0.775, 0], [0.9, 0.95], R), pad('2', 'GND', [0.775, 0], [0.9, 0.95], R)]},
        # Back side: rectangular and slotted plated holes and an asymmetric (trapezoid) SMD land.
        {'ref': 'J1', 'at': [24, 8], 'rot': 90, 'side': 'B', 'pads': [
            pad('1', 'S1', [0, 0], [1.7, 1.7], 'rect', 'pth', [1.0, 1.0]),
            pad('2', 'S2', [0, 2.54], [1.7, 2.4], 'oval', 'pth', [0.8, 1.6]),
            pad('3', 'S3', [2.2, 1.27], [1.2, 0.8], 'trapezoid', delta=[0.3, 0])]},
        {'ref': 'H1', 'at': [34, 5], 'rot': 0, 'pads': [pad('', '', [0, 0], [3.2, 3.2], 'circle', 'npth', [3.2, 3.2])]},
        {'ref': 'X1', 'at': [30, 20], 'rot': 0, 'pads': [
            pad('1', 'S4', [0, 0], [0.6, 0.6], 'custom', polygon=[[-0.3, -0.3], [1.0, -0.3], [1.0, 0.3], [-0.3, 0.3]]),
            pad('2', 'S5', [0, 1.5], [0.6, 0.6])]},
        # Narrow escape: walls left, right and below, a cap above; only bent exits and a via leave.
        {'ref': 'NE1', 'at': [8, 23], 'rot': 0, 'pads': [
            pad('1', 'NE', [0, 0], [0.4, 0.4]), pad('2', 'GND', [-0.75, 0], [0.4, 2.0]),
            pad('3', 'GND', [0.75, 0], [0.4, 2.0]), pad('4', 'GND', [0, 0.75], [1.9, 0.4]),
            pad('5', 'GND', [0, -2.0], [0.6, 0.4])]},
        {'ref': 'TP1', 'at': [16, 23], 'rot': 0, 'pads': [pad('1', 'NE', [0, 0], [1.0, 1.0], 'circle')]},
        # Enclosed: a closed different-net ring too tight for a via; a finite search cannot leave.
        {'ref': 'EN1', 'at': [24, 23], 'rot': 0, 'pads': [pad('1', 'EN', [0, 0], [0.4, 0.4]), *ring('GND', 0.45)]},
        {'ref': 'TP2', 'at': [30, 26], 'rot': 0, 'pads': [pad('1', 'EN', [0, 0], [1.0, 1.0], 'circle')]},
    ],
    # A via keepout around R3 and a track keepout right of R4 (both copper layers).
    'keepouts': [{'rect': [1.5, 12, 6.5, 16], 'tracks': False, 'vias': True},
                 {'rect': [11.4, 12.5, 13, 15.5], 'tracks': True, 'vias': False}],
    'tracks': [{'net': 'GND', 'layer': 'F.Cu', 'start': [2, 15.8], 'end': [6, 15.8], 'width': 0.25}],
    'vias': [{'net': 'GND', 'at': [14, 8.5], 'diameter': 0.6, 'drill': 0.3}],
}

BUILD = r'''
import json, sys
import pcbnew as k
path, spec = sys.argv[1], json.loads(sys.argv[2])
def nm(v):
    return int(round(v * 1000000))
def v2(p):
    return k.VECTOR2I(nm(p[0]), nm(p[1]))
board = k.NewBoard(path)
x0, y0, x1, y1 = spec['outline']
corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
for a, b in zip(corners, corners[1:] + corners[:1]):
    edge = k.PCB_SHAPE(board)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetStart(v2(a))
    edge.SetEnd(v2(b))
    edge.SetLayer(k.Edge_Cuts)
    edge.SetWidth(nm(0.1))
    board.Add(edge)
nets = {}
def net(name):
    if name not in nets:
        item = k.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = item
    return nets[name]
SHAPES = {'rect': k.PAD_SHAPE_RECT, 'roundrect': k.PAD_SHAPE_ROUNDRECT, 'circle': k.PAD_SHAPE_CIRCLE,
          'oval': k.PAD_SHAPE_OVAL, 'trapezoid': k.PAD_SHAPE_TRAPEZOID, 'custom': k.PAD_SHAPE_CUSTOM}
ALL = k.PADSTACK.ALL_LAYERS
for spec_fp in spec['footprints']:
    fp = k.FOOTPRINT(board)
    fp.SetReference(spec_fp['ref'])
    fp.SetFPIDAsString('Owned:' + spec_fp['ref'])
    board.Add(fp)
    for p in spec_fp['pads']:
        item = k.PAD(fp)
        item.SetNumber(p['n'])
        item.SetAttribute({'smd': k.PAD_ATTRIB_SMD, 'pth': k.PAD_ATTRIB_PTH, 'npth': k.PAD_ATTRIB_NPTH}[p['attr']])
        item.SetLayerSet({'smd': k.PAD.SMDMask(), 'pth': k.PAD.PTHMask(), 'npth': k.PAD.UnplatedHoleMask()}[p['attr']])
        item.SetShape(ALL, SHAPES[p['shape']])
        item.SetSize(ALL, v2(p['size']))
        if p['shape'] == 'roundrect':
            item.SetRoundRectRadiusRatio(ALL, 0.25)
        if p['shape'] == 'trapezoid':
            item.SetDelta(ALL, v2(p['delta']))
        if p['shape'] == 'custom':
            item.SetAnchorPadShape(ALL, k.PAD_SHAPE_RECT)
            points = k.VECTOR_VECTOR2I()
            for q in p['polygon']:
                points.append(v2(q))
            item.AddPrimitivePoly(ALL, points, 0, True)
        if p['drill']:
            item.SetDrillShape(k.PAD_DRILL_SHAPE_OBLONG if p['drill'][0] != p['drill'][1] else k.PAD_DRILL_SHAPE_CIRCLE)
            item.SetDrillSize(v2(p['drill']))
        item.SetPosition(v2(p['at']))
        if p['net']:
            item.SetNet(net(p['net']))
        fp.Add(item)
    fp.SetPosition(v2(spec_fp['at']))
    if spec_fp.get('side') == 'B':
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetOrientationDegrees(spec_fp['rot'])
for area in spec.get('keepouts', []):
    zone = k.ZONE(board)
    zone.SetIsRuleArea(True)
    layers = k.LSET()
    layers.AddLayer(k.F_Cu)
    layers.AddLayer(k.B_Cu)
    zone.SetLayerSet(layers)
    zone.SetDoNotAllowTracks(area['tracks'])
    zone.SetDoNotAllowVias(area['vias'])
    zone.SetDoNotAllowPads(False)
    zone.SetDoNotAllowZoneFills(False)
    zone.SetDoNotAllowFootprints(False)
    ax0, ay0, ax1, ay1 = area['rect']
    outline = zone.Outline()
    outline.NewOutline()
    for q in ((ax0, ay0), (ax1, ay0), (ax1, ay1), (ax0, ay1)):
        outline.Append(nm(q[0]), nm(q[1]))
    board.Add(zone)
for t in spec.get('tracks', []):
    track = k.PCB_TRACK(board)
    track.SetStart(v2(t['start']))
    track.SetEnd(v2(t['end']))
    track.SetWidth(nm(t['width']))
    track.SetLayer(board.GetLayerID(t['layer']))
    track.SetNet(net(t['net']))
    board.Add(track)
for v in spec.get('vias', []):
    via = k.PCB_VIA(board)
    via.SetViaType(k.VIATYPE_THROUGH)
    via.SetPosition(v2(v['at']))
    via.SetWidth(nm(v['diameter']))
    via.SetDrill(nm(v['drill']))
    via.SetNet(net(v['net']))
    board.Add(via)
k.SaveBoard(path, board)
'''

# Fresh interpreter: re-pose a footprint of a saved board and save it again.
REPOSE = r'''
import json, sys
import pcbnew as k
path, edit = sys.argv[1], json.loads(sys.argv[2])
board = k.LoadBoard(path)
for fp in board.GetFootprints():
    change = edit.get(fp.GetReference())
    if change is None:
        continue
    if change.get('flip'):
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
    if 'rotation_deg' in change:
        fp.SetOrientationDegrees(change['rotation_deg'])
    if 'position_nm' in change:
        fp.SetPosition(k.VECTOR2I(*change['position_nm']))
k.SaveBoard(path, board)
'''


def build(directory, spec=SPEC, name='access.kicad_pcb'):
    path = Path(directory) / name
    subprocess.run([sys.executable, '-c', BUILD, str(path), json.dumps(spec)], check=True, capture_output=True)
    return path


def repose(path, edit):
    subprocess.run([sys.executable, '-c', REPOSE, str(path), json.dumps(edit)], check=True, capture_output=True)


def deviation(left, right):
    """Largest distance from a vertex of either polygon to the other's boundary."""
    def one_way(a, b):
        return max(min(access._point_segment(p, q, r) for q, r in zip(b, b[1:] + b[:1])) for p in a)
    return max(one_way(left, right), one_way(right, left))


def terminal(report, ref, number, index=0):
    found = [t for t in report['terminals'] if any(p['ref'] == ref and p['number'] == number for p in t['pads'])]
    return found[index]


class NativeFixture:
    @classmethod
    def setUpClass(cls):
        require_toolchain()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-access-')
        cls.dir = Path(cls.tmp.name)
        cls.board = build(cls.dir)
        cls.geometry = routing_geometry(cls.board)
        cls.report = access.analyze_access(cls.geometry, None, POLICY)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class CaptureTests(NativeFixture, unittest.TestCase):
    def test_capture_is_raw_native_geometry_and_rules(self):
        g = self.geometry
        self.assertEqual(g['schema'], 'agent-ee.routing-geometry/1')
        self.assertEqual(g['board_sha256'], hashlib.sha256(self.board.read_bytes()).hexdigest())
        self.assertEqual(g['copper_layers'], ['F.Cu', 'B.Cu'])
        self.assertEqual(g['rules']['custom_rules'], {'status': 'absent'})
        self.assertEqual(g['rules']['hole_clearance_nm'], 250_000)
        self.assertEqual(g['rules']['copper_edge_clearance_nm'], 500_000)
        self.assertEqual(g['track_clearance_nm']['S1'], {'F.Cu': 200_000, 'B.Cu': 200_000})
        self.assertEqual(g['outline']['status'], 'proven')
        pads = {(p['ref'], p['number'], p['uuid']): p for p in g['pads']}
        # Raw copper, not the enlarged packing reservation: the outer bound exceeds the land by at most max error.
        r1 = next(p for k, p in pads.items() if k[:2] == ('R1', '1'))
        box = access._box([q for poly in r1['copper']['F.Cu']['outer'] for q in poly])
        self.assertLessEqual(abs((box[2] - box[0]) - 900_000), g['max_error_nm'])
        inner = access._box([q for poly in r1['copper']['F.Cu']['inner'] for q in poly])
        self.assertTrue(box[0] <= inner[0] and inner[2] <= box[2])
        # Plated and slotted drills, NPTH without a net, back-side copper of the mirrored connector.
        j1 = {p['number']: p for p in g['pads'] if p['ref'] == 'J1'}
        self.assertEqual(sorted(j1['1']['copper']), ['B.Cu', 'F.Cu'])
        self.assertTrue(j1['2']['hole']['plated'])
        self.assertEqual(sorted(j1['2']['hole']['drill_nm']), [800_000, 1_600_000])
        self.assertEqual(list(j1['3']['copper']), ['B.Cu'])
        h1 = next(p for p in g['pads'] if p['ref'] == 'H1')
        self.assertFalse(h1['hole']['plated'])
        self.assertEqual(h1['net'], '')
        x1 = next(p for p in g['pads'] if p['ref'] == 'X1' and p['number'] == '1')
        self.assertEqual(x1['status'], 'unsupported')
        self.assertIn('custom pad shape not qualified for access', x1['reasons'])
        self.assertEqual([t['kind'] for t in g['tracks']], ['segment'])
        self.assertEqual(g['vias'][0]['layers'], ['F.Cu', 'B.Cu'])
        forbid = sorted((tuple(a['forbid'][k] for k in ('tracks', 'vias'))) for a in g['rule_areas'])
        self.assertEqual(forbid, [(False, True), (True, False)])
        # Same-number pads keep distinct UUIDs.
        nines = [p['uuid'] for p in g['pads'] if p['ref'] == 'U1' and p['number'] == '9']
        self.assertEqual(len(set(nines)), 2)

    def test_capture_is_stable_across_save_and_reload(self):
        copy_path = self.dir / 'reloaded' / 'access.kicad_pcb'
        copy_path.parent.mkdir(exist_ok=True)
        copy_path.write_bytes(self.board.read_bytes())
        repose(copy_path, {})
        again = routing_geometry(copy_path)
        for key in ('pads', 'tracks', 'vias', 'rule_areas', 'outline', 'rules', 'track_clearance_nm'):
            self.assertEqual(again[key], self.geometry[key], key)

    def test_quarter_turn_poses_match_saved_pcbnew_geometry(self):
        """The index's pose transform (placer.rotate) against pcbnew re-posing, saving and reloading."""
        footprints = {fp['ref']: fp for fp in self.geometry['footprints']}
        edits = {'U1': 90, 'J1': -90, 'R2': 180, 'X1': 90}
        index = access.AccessIndex(self.geometry, POLICY)
        poses, edit = {}, {}
        for ref, turn in edits.items():
            fp = footprints[ref]
            position = [fp['position_nm'][0] + 1_250_000, fp['position_nm'][1] - 500_000]
            poses[ref] = {'position_nm': position, 'rotation_deg': fp['rotation_deg'] + turn}
            edit[ref] = {'position_nm': position, 'rotation_deg': (fp['rotation_deg'] + turn) % 360}
            index.push(ref, poses[ref])
        moved = self.dir / 'posed' / 'access.kicad_pcb'
        moved.parent.mkdir(exist_ok=True)
        moved.write_bytes(self.board.read_bytes())
        repose(moved, edit)
        native = {p['uuid']: p for p in routing_geometry(moved)['pads']}
        error = self.geometry['max_error_nm']
        checked = 0
        for ref in edits:
            for record in index.pads_of[ref]:
                target = native[record['uuid']]
                self.assertEqual(sorted(target['copper']), sorted(record['copper']))
                anchor = index.anchor(record['uuid'])
                self.assertLessEqual(math.dist(anchor, target['position_nm']), 1)
                for layer in record['copper']:
                    for bound in ('outer', 'inner'):
                        mine, theirs = index.pad_shape(record['uuid'], layer, bound), target['copper'][layer][bound]
                        self.assertEqual(len(mine), len(theirs))
                        for a, b in zip(mine, theirs):
                            self.assertLessEqual(deviation(a, [tuple(q) for q in b]), 2 * error, (ref, layer, bound))
                            checked += 1
                if record['hole']:
                    hole = index.items[f"hole:{record['uuid']}:0"]['shape'][1]
                    self.assertLessEqual(deviation(hole, [tuple(q) for q in target['hole']['outer'][0]]), 2 * error)
        self.assertGreater(checked, 20)

    def test_mirrored_side_matches_an_independent_reflection(self):
        """Flipping J1 in pcbnew reflects its raw copper and slots about the footprint's vertical axis."""
        flipped = self.dir / 'flipped' / 'access.kicad_pcb'
        flipped.parent.mkdir(exist_ok=True)
        flipped.write_bytes(self.board.read_bytes())
        repose(flipped, {'J1': {'flip': True}})
        native = {p['uuid']: p for p in routing_geometry(flipped)['pads']}
        centre = next(fp for fp in self.geometry['footprints'] if fp['ref'] == 'J1')['position_nm'][0]
        swap = {'F.Cu': 'B.Cu', 'B.Cu': 'F.Cu'}
        error = self.geometry['max_error_nm']
        for record in (p for p in self.geometry['pads'] if p['ref'] == 'J1'):
            target = native[record['uuid']]
            self.assertEqual(sorted(target['copper']), sorted(swap[l] for l in record['copper']))
            for layer, bounds in record['copper'].items():
                for a, b in zip(bounds['outer'], target['copper'][swap[layer]]['outer']):
                    reflected = [(2 * centre - x, y) for x, y in a]
                    self.assertLessEqual(deviation(reflected, [tuple(q) for q in b]), 2 * error)
            if record['hole']:
                reflected = [(2 * centre - x, y) for x, y in record['hole']['outer'][0]]
                self.assertLessEqual(deviation(reflected, [tuple(q) for q in target['hole']['outer'][0]]), 2 * error)


class AnalysisTests(NativeFixture, unittest.TestCase):
    def test_report_is_deterministic_and_never_claims_infeasibility(self):
        again = access.analyze_access(copy.deepcopy(self.geometry), None, POLICY)
        self.assertEqual(again, self.report)
        statuses = {t['status'] for t in self.report['terminals']}
        self.assertLessEqual(statuses, {'witness', 'unknown', 'not_required'})
        self.assertNotIn('proven_no_access', json.dumps(self.report))
        self.assertEqual(self.report['coverage']['status'], 'modelled')

    def test_terminal_components_and_same_number_pads(self):
        nine = terminal(self.report, 'U1', '9')
        self.assertEqual(sorted(p['number'] for p in nine['pads']), ['9', '9'])
        self.assertEqual(len({p['uuid'] for p in nine['pads']}), 2)
        tens = [t for t in self.report['terminals'] if any(p['ref'] == 'U1' and p['number'] == '10' for p in t['pads'])]
        self.assertEqual(len(tens), 2)
        self.assertNotEqual(tens[0]['id'], tens[1]['id'])
        # The NPTH hole has no copper and no net: a drill obstacle, never a terminal.
        self.assertFalse([t for t in self.report['terminals'] if any(p['ref'] == 'H1' for p in t['pads'])])

    def test_unsupported_custom_pad_is_unknown(self):
        x1 = terminal(self.report, 'X1', '1')
        self.assertEqual((x1['status'], x1['reason']), ('unknown', 'unsupported_geometry'))
        # Its native outer copper remains an obstacle to every other terminal.
        index = access.AccessIndex(self.geometry, POLICY)
        self.assertTrue(any(i['kind'] == 'pad' and i['ref'] == 'X1' and i['net'] == 'S4' for i in index.items.values()))

    def test_narrow_escape_needs_a_bend_or_via(self):
        ne = terminal(self.report, 'NE1', '1')
        self.assertEqual(ne['status'], 'witness')
        kinds = {(w['kind'], len(w['path_nm'])) for w in ne['witnesses']}
        self.assertNotIn(('exit', 2), kinds)  # no straight exit leaves the walls
        self.assertIn(('exit', 3), kinds)
        self.assertTrue(all(w['min_margin_nm'] >= 0 for w in ne['witnesses']))

    def test_enclosed_terminal_is_unknown_with_blockers(self):
        en = terminal(self.report, 'EN1', '1')
        self.assertEqual(en['status'], 'unknown')
        self.assertEqual(en['reason'], 'no_witness_in_finite_search')
        self.assertEqual(en['witnesses'], [])
        self.assertGreater(en['attempted'], 0)
        ring = {p['uuid'] for p in self.geometry['pads'] if p['ref'] == 'EN1' and p['number'] != '1'}
        self.assertTrue(en['blockers'])
        self.assertTrue(all(b['obstacle'].split(':')[1] in ring for b in en['blockers']))

    def test_budget_exhaustion_is_unknown(self):
        report = access.analyze_access(self.geometry, None, POLICY, {'max_candidates': 1}, refs=['EN1'])
        en = terminal(report, 'EN1', '1')
        self.assertEqual((en['status'], en['reason'], en['attempted']), ('unknown', 'search_budget_exhausted', 1))

    def test_witness_cap_reports_truncation(self):
        r1 = terminal(self.report, 'R1', '1')
        self.assertEqual(len(r1['witnesses']), access.LIMITS['max_witnesses'])
        self.assertTrue(r1['truncated'])
        # Tiny same-net direct links come before exits and vias.
        ten = terminal(self.report, 'U1', '10')
        self.assertEqual(ten['witnesses'][0]['kind'], 'direct')

    def test_via_keepout_and_track_keepout_are_respected(self):
        index = access.AccessIndex(self.geometry, POLICY)
        via_keepout = next(a for a in self.geometry['rule_areas'] if a['forbid']['vias'])
        polygon = [tuple(p) for p in via_keepout['outline']['polygons'][0]]
        vias = [w for t in self.report['terminals'] for w in t.get('witnesses', []) if w['kind'] == 'via']
        self.assertTrue(vias)
        for w in vias:
            self.assertGreater(access.shape_distance(tuple(w['via']['position_nm']), tuple(w['via']['position_nm']),
                                                     ('poly', polygon)), w['via']['diameter_nm'] / 2)
        r3 = [terminal(self.report, 'R3', n) for n in ('1', '2')]
        self.assertTrue(all(w['kind'] != 'via' for t in r3 for w in t['witnesses']))
        # A via inside the keepout is rejected by that keepout, outside it by nothing.
        inside = (4_000_000, 12_600_000)
        margin, limiting = index.check_via(inside, POLICY['vias'][0], 'S5', set())
        self.assertLess(margin, 0)
        self.assertTrue(limiting.startswith(f"keepout:{via_keepout['uuid']}"))
        track_keepout = next(a for a in self.geometry['rule_areas'] if a['forbid']['tracks'])
        r4 = terminal(self.report, 'R4', '2')
        self.assertNotIn('right', [w.get('direction') for w in r4['witnesses']])
        a = (10_775_000, 14_000_000)
        margin, limiting = index.check_track(a, (13_500_000, 14_000_000), 'F.Cu', 'GND', 100_000, set())
        self.assertTrue(limiting.startswith(f"keepout:{track_keepout['uuid']}"))

    def test_holes_existing_track_and_via_obstruct(self):
        index = access.AccessIndex(self.geometry, POLICY)
        via = self.geometry['vias'][0]
        margin, limiting = index.check_track((13_000_000, 8_500_000), (15_000_000, 8_500_000), 'B.Cu', 'S9', 100_000,
                                             set())
        self.assertLess(margin, 0)
        self.assertIn(via['uuid'], limiting)
        track = self.geometry['tracks'][0]
        margin, limiting = index.check_track((3_000_000, 15_000_000), (3_000_000, 17_000_000), 'F.Cu', 'S5', 100_000,
                                             set())
        self.assertEqual(limiting, f"track:{track['uuid']}")
        h1 = next(p for p in self.geometry['pads'] if p['ref'] == 'H1')
        # NPTH: a drill obstacle on a layer where it has no net.
        margin, limiting = index.check_track((31_000_000, 5_000_000), (32_500_000, 5_000_000), 'B.Cu', 'S1', 100_000,
                                             set())
        self.assertLess(margin, 0)
        self.assertIn(h1['uuid'], limiting)

    def test_finite_search_failure_never_rejects(self):
        """No access result has a rejecting meaning; unknowns keep their attempted candidates and blockers."""
        for t in self.report['terminals']:
            if t.get('reason') in ('no_witness_in_finite_search', 'search_budget_exhausted'):
                self.assertIn('blockers', t)
                self.assertIn('attempted', t)
        self.assertEqual(set(self.report['summary']), {'witness', 'unknown', 'not_required'})

    def test_custom_rules_make_every_terminal_unknown(self):
        geometry = copy.deepcopy(self.geometry)
        geometry['rules']['custom_rules'] = {'status': 'present', 'sha256': '0' * 64, 'rules': ['owned']}
        report = access.analyze_access(geometry, None, POLICY)
        self.assertEqual(report['coverage']['status'], 'unknown')
        self.assertEqual(report['summary']['witness'], 0)
        self.assertTrue(all(t['reason'] == 'coverage' for t in report['terminals'] if t['status'] == 'unknown'))

    def test_native_custom_rule_file_is_captured_as_unknown_coverage(self):
        directory = self.dir / 'dru'
        directory.mkdir(exist_ok=True)
        board = build(directory)
        board.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "owned_fixture" (constraint clearance (min 0.3mm))\n'
            '  (condition "A.NetName == \'S1\'"))\n')
        geometry = routing_geometry(board)
        self.assertEqual(geometry['rules']['custom_rules']['status'], 'present')
        self.assertEqual(geometry['rules']['custom_rules']['rules'], ['owned_fixture'])
        self.assertEqual(access.analyze_access(geometry, None, POLICY)['summary']['witness'], 0)

    def test_different_net_zone_leaves_its_layer_unknown(self):
        geometry = copy.deepcopy(self.geometry)
        geometry['zones'] = [{'uuid': 'zone-1', 'net': 'GND', 'filled': True, 'layers': ['B.Cu']}]
        report = access.analyze_access(geometry, None, POLICY, refs=['R1'])
        r1 = terminal(report, 'R1', '1')
        self.assertEqual(r1['zone_layers_unknown'], ['B.Cu'])
        self.assertTrue(all(w['layer'] == 'F.Cu' and w['kind'] != 'via' for w in r1['witnesses']))

    def test_policy_below_the_native_floor_refuses(self):
        with self.assertRaisesRegex(ValueError, 'minimum track width'):
            access.AccessIndex(self.geometry, {**POLICY, 'track_width_nm': 100_000})
        with self.assertRaisesRegex(ValueError, 'via diameter, drill or annular'):
            access.AccessIndex(self.geometry, {**POLICY, 'vias': [{'diameter_nm': 400_000, 'drill_nm': 300_000}]})
        with self.assertRaisesRegex(ValueError, 'captured copper layers'):
            access.AccessIndex(self.geometry, {**POLICY, 'routing_layers': ['In1.Cu']})
        with self.assertRaisesRegex(ValueError, 'quarter turns'):
            access.analyze_access(self.geometry, {'R1': {'position_nm': [0, 0], 'rotation_deg': 45}}, POLICY)


class IncrementalTests(NativeFixture, unittest.TestCase):
    def test_push_pop_restores_and_matches_full_recomputation(self):
        index = access.AccessIndex(self.geometry, POLICY)
        fresh = copy.deepcopy(index.state())
        fp = next(f for f in self.geometry['footprints'] if f['ref'] == 'R3')
        poses = {'R3': {'position_nm': [fp['position_nm'][0] + 2 * MM, fp['position_nm'][1] + 3 * MM],
                        'rotation_deg': fp['rotation_deg'] + 90}}
        index.push('R3', poses['R3'])
        incremental = index.analyze()
        full = access.analyze_access(self.geometry, poses, POLICY)
        self.assertEqual(incremental, full)
        self.assertNotEqual(incremental['terminals'], self.report['terminals'])
        index.pop()
        self.assertEqual(index.state(), fresh)
        self.assertEqual(index.analyze(), self.report)

    def test_orientation_dependent_access_is_native_qualified(self):
        """R3 turned a half turn swaps which net faces R4; both orientations' witnesses pass native DRC."""
        fp = next(f for f in self.geometry['footprints'] if f['ref'] == 'R3')
        pose = {'position_nm': fp['position_nm'], 'rotation_deg': fp['rotation_deg'] + 180}
        turned = access.analyze_access(self.geometry, {'R3': pose}, POLICY, refs=['R3'])
        before = terminal(self.report, 'R3', '2')
        after = terminal(turned, 'R3', '2')
        self.assertNotEqual(before['witnesses'], after['witnesses'])
        moved = self.dir / 'turned' / 'access.kicad_pcb'
        moved.parent.mkdir(exist_ok=True)
        moved.write_bytes(self.board.read_bytes())
        repose(moved, {'R3': {'rotation_deg': pose['rotation_deg'] % 360}})
        result = access.qualify(moved, [after['witnesses'], terminal(turned, 'R3', '1')['witnesses']],
                                self.dir / 'turned-qualification')
        self.assertEqual([g['status'] for g in result['groups']], ['qualified', 'qualified'], result)


class NativeQualificationTests(NativeFixture, unittest.TestCase):
    def test_every_witness_passes_unchanged_native_rules(self):
        groups = [t['witnesses'] for t in self.report['terminals'] if t['status'] == 'witness']
        kinds = {w['kind'] for group in groups for w in group}
        self.assertEqual(kinds, {'direct', 'exit', 'via'})
        result = access.qualify(self.board, groups, self.dir / 'qualification')
        refused = [g for g in result['groups'] if g['status'] != 'qualified']
        self.assertEqual(refused, [])
        self.assertEqual(len(result['groups']), len(groups))
        # The materialized copies kept the rules byte-identical; the source board is untouched.
        self.assertEqual(hashlib.sha256(self.board.read_bytes()).hexdigest(), self.geometry['board_sha256'])

    def test_a_forged_witness_is_refused_by_native_drc(self):
        r1 = terminal(self.report, 'R1', '1')
        forged = copy.deepcopy(r1['witnesses'][0])
        # Straight through the other-net pad R1.2.
        forged.update(kind='exit', path_nm=[forged['path_nm'][0], [14_775_000, 6_000_000]])
        result = access.qualify(self.board, [[forged]], self.dir / 'forged')
        self.assertEqual(result['groups'][0]['status'], 'refused')
        self.assertTrue(result['groups'][0]['new_findings'])


def synthetic_geometry(capture, candidate):
    """A routing geometry of the block fixture with members at the candidate's frame poses.

    Square raw copper per pad (0.4 mm) under the KiCad default rules; the
    anchor stays at its board pose. Pure data for the sidecar contract.
    """
    board = {fp['ref']: fp for fp in capture['footprints']}
    anchor = board[candidate['frame']['anchor']]
    footprints, pads = [], []
    turns = int(anchor['rotation_deg']) // 90
    for fp in capture['footprints']:
        row = next((r for r in candidate['members'] if r['reference'] == fp['ref']), None)
        if row is None:
            placed = fp
        else:
            x, y = placer.rotate(row['pose']['position_nm'], turns)
            placed = placer.transform(fp, [anchor['position_nm'][0] + x, anchor['position_nm'][1] + y],
                                      (row['pose']['rotation_deg'] + 90 * turns) % 360)
        footprints.append({'ref': fp['ref'], 'uuid': 'fp-' + fp['ref'], 'side': fp['side'],
                           'position_nm': placed['position_nm'], 'rotation_deg': placed['rotation_deg']})
        for p in placed['pads']:
            (cx, cy), half = p['position_nm'], 200_000
            square = [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]]
            pads.append({'uuid': p['uuid'], 'ref': fp['ref'], 'footprint_uuid': 'fp-' + fp['ref'],
                         'number': p['number'], 'net': p['net'], 'attribute': 'SMD', 'shape': 1,
                         'position_nm': p['position_nm'], 'rotation_deg': 0, 'status': 'supported', 'hole': None,
                         'copper': {'F.Cu': {'outer': [square], 'inner': [square]}},
                         'own_clearance_nm': {'F.Cu': 200_000}})
    nets = sorted({p['net'] for p in pads})
    outline = capture['outline']['polygons'][0]
    return {'schema': access.GEOMETRY, 'board_sha256': capture['board_sha256'], 'pcbnew_version': 'fixture',
            'max_error_nm': 5000, 'copper_layers': ['F.Cu', 'B.Cu'],
            'rules': {'min_clearance_nm': 0, 'hole_clearance_nm': 250_000, 'hole_to_hole_nm': 250_000,
                      'copper_edge_clearance_nm': 500_000, 'min_track_width_nm': 200_000,
                      'min_via_diameter_nm': 500_000, 'min_via_annular_nm': 100_000,
                      'min_through_drill_nm': 300_000, 'custom_rules': {'status': 'absent'}},
            'track_clearance_nm': {n: {'F.Cu': 200_000, 'B.Cu': 200_000} for n in nets},
            'outline': {'status': 'proven', 'polygons': [outline]}, 'rule_areas': [],
            'footprints': footprints, 'pads': pads, 'tracks': [], 'vias': [], 'zones': []}


class SidecarTests(unittest.TestCase):
    """Diagnostic block-access/1 binds candidate/2 and leaves legacy identity, order and admission unchanged."""

    def setUp(self):
        import test_blocks
        self.blocks_fixture = test_blocks
        fixture = test_blocks.PackingTests('test_rigid_candidate')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def pack(self):
        return self.fixture.pack()

    def test_feature_off_and_diagnostic_mode_keep_legacy_candidate_identity(self):
        legacy = self.pack()
        legacy_bytes = json.dumps(legacy, sort_keys=True).encode()
        candidate = legacy['candidate']
        geometry = synthetic_geometry(self.fixture.board, candidate)
        frozen = copy.deepcopy(candidate)
        sidecar = access.block_access(candidate, geometry, POLICY)
        self.assertEqual(candidate, frozen)  # read, never changed
        diagnostic = self.pack()
        self.assertEqual(json.dumps(diagnostic, sort_keys=True).encode(), legacy_bytes)
        self.assertEqual(blocks.digest(diagnostic['candidate']), blocks.digest(legacy['candidate']))
        self.assertEqual([m['reference'] for m in diagnostic['candidate']['members']],
                         [m['reference'] for m in legacy['candidate']['members']])
        self.assertEqual(sidecar['schema'], access.SIDECAR)
        self.assertEqual(sidecar['mode'], 'diagnostic')
        self.assertEqual(sidecar['candidate_sha256'], blocks.digest(candidate))
        self.assertEqual(set(sidecar['pad_geometry_sha256']), {'U1', 'C1', 'L1', 'C3'})
        roles = {r['net']: r['role'] for r in sidecar['net_roles']}
        self.assertEqual(roles['SW'], 'internal')
        self.assertEqual(roles['VIN'], 'boundary')
        self.assertTrue(all(set(t['members']) <= {'U1', 'C1', 'L1', 'C3'} for t in sidecar['terminals']))

    def test_missing_sidecar_keeps_admission_with_unknown_access(self):
        result = self.pack()
        candidate = json.loads(json.dumps(result['candidate']))
        inventory = self.fixture.inv
        derived = self.fixture.derive()
        policy = self.blocks_fixture.policy()
        admitted = blocks.admit(candidate, self.fixture.board, inventory, derived, policy)
        self.assertEqual(admitted, {'status': 'admitted', 'problems': []})
        self.assertEqual(access.access_status(candidate), {'status': 'unknown', 'reason': 'no block-access sidecar'})
        sidecar = access.block_access(candidate, synthetic_geometry(self.fixture.board, candidate), POLICY)
        self.assertIn(access.access_status(candidate, sidecar)['status'], ('witnessed', 'partial', 'unknown'))
        self.assertEqual(blocks.admit(candidate, self.fixture.board, inventory, derived, policy), admitted)
        other = copy.deepcopy(candidate)
        other['search']['steps_used'] += 1
        with self.assertRaisesRegex(ValueError, 'another candidate'):
            access.access_status(other, sidecar)

    def test_sidecar_refuses_a_board_that_does_not_hold_the_candidate_poses(self):
        candidate = self.pack()['candidate']
        geometry = synthetic_geometry(self.fixture.board, candidate)
        moved = next(f for f in geometry['footprints'] if f['ref'] == 'C1')
        moved['position_nm'] = [moved['position_nm'][0] + MM, moved['position_nm'][1]]
        with self.assertRaisesRegex(ValueError, 'C1: board pose differs'):
            access.block_access(candidate, geometry, POLICY)


# The captured pod (iteration-7 candidate board, as in tests/test_physical_intent.py): read from the
# pinned objects of a local fome-mc store into a temporary run; no pod design file is copied here.
POD_COMMIT = '19522e37de01103a93fe171a09fe624d8aba6bad'
POD_ITERATION = 'telemetry-pod/expansion/placement-candidate/iteration-7/'
POD_BOARD_SHA256 = '35f242c804038b7ec48740e6f894339e1c152fea95e6a15ddfe77b402030cba9'
POD_CANDIDATE_SHA256 = '9da9ba91731d27d37c17111ba5989bf7b97959774986cd8ed9616039453fe050'


def pod_blob(store, path):
    data = subprocess.run(['git', '-C', str(store), 'cat-file', 'blob', f'{POD_COMMIT}:{path}'],
                          capture_output=True, check=True).stdout
    return gzip.decompress(data) if path.endswith('.gz') else data


class PodReplayTests(unittest.TestCase):
    """Exact U23/C87 geometry of the reviewed pod candidate: a bent-access witness, native-qualified."""

    @classmethod
    def setUpClass(cls):
        require_toolchain()
        store = fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(Path.home() / 'git/fome-mc')),
                              POD_COMMIT)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-access-pod-')
        cls.dir = Path(cls.tmp.name)
        cls.board = cls.dir / 'candidate.kicad_pcb'
        cls.board.write_bytes(pod_blob(store, POD_ITERATION + 'candidate.kicad_pcb.gz'))
        assert hashlib.sha256(cls.board.read_bytes()).hexdigest() == POD_BOARD_SHA256, 'pinned pod board changed'
        cls.board.with_suffix('.kicad_pro').write_bytes(pod_blob(store, POD_ITERATION + 'candidate.kicad_pro'))
        cls.candidate_raw = pod_blob(store, POD_ITERATION + 'inputs/candidates/sensor-power-front.candidate.json')
        assert hashlib.sha256(cls.candidate_raw).hexdigest() == POD_CANDIDATE_SHA256, 'pinned pod candidate changed'
        started = time.perf_counter()
        cls.geometry = routing_geometry(cls.board)
        cls.report = access.analyze_access(cls.geometry, None, POLICY)
        cls.seconds = time.perf_counter() - started

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_u23_pin7_has_bent_access_and_a_straight_ray_hits_c87(self):
        u23 = terminal(self.report, 'U23', '7')
        self.assertEqual(u23['net'], '/SENSOR_POWER_FRONT_ILIM')
        self.assertEqual(u23['status'], 'witness')
        bends = [w for w in u23['witnesses'] if w['kind'] == 'exit' and len(w['path_nm']) == 3]
        self.assertTrue(bends)
        c87 = next(p for p in self.geometry['pads'] if p['ref'] == 'C87' and p['number'] == '2')
        self.assertEqual(c87['net'], '/GND')
        index = access.AccessIndex(self.geometry, POLICY)
        start = tuple(next(p for p in self.geometry['pads'] if p['uuid'] == u23['source_pad'])['position_nm'])
        # A 1 mm straight outward ray (y grows down, away from the package) is blocked by C87.2:
        # a straight-ray failure, which is not infeasibility.
        margin, limiting = index.check_track(start, (start[0], start[1] + MM), 'F.Cu', u23['net'], 100_000,
                                             {u23['source_pad']})
        self.assertLess(margin, 0)
        self.assertEqual(limiting, f"pad:{c87['uuid']}:F.Cu:0")
        result = access.qualify(self.board, [u23['witnesses']], self.dir / 'u23')
        self.assertEqual(result['groups'][0]['status'], 'qualified', result)

    def test_exposed_pad_pieces_are_one_component_by_uuid(self):
        ep = terminal(self.report, 'U23', '11')
        self.assertEqual(len(ep['pads']), 5)
        self.assertEqual(len({p['uuid'] for p in ep['pads']}), 5)

    def test_actual_candidate_sidecar_binds_and_witnesses_qualify(self):
        candidate = json.loads(self.candidate_raw)
        sidecar = access.block_access(candidate, self.geometry, POLICY)
        self.assertEqual(sidecar['candidate_sha256'], blocks.digest(candidate))
        self.assertIn('U23', sidecar['pad_geometry_sha256'])
        self.assertEqual(sidecar['coverage']['status'], 'modelled')
        groups = [t['witnesses'] for t in sidecar['terminals'] if t['status'] == 'witness']
        self.assertTrue(groups)
        result = access.qualify(self.board, groups, self.dir / 'block')
        self.assertEqual([g['status'] for g in result['groups'] if g['status'] != 'qualified'], [], result)
        print(f'\nAGENTEE-386 pod access capture+analysis: {self.seconds:.3f} s '
              f"({len(self.report['terminals'])} terminals; provisional ceiling 15.95 s)", file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
