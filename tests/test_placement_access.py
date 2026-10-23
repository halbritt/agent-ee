"""AGENTEE-386 (plan M3a): native-qualified local access witnesses and unknowns.

Every native fixture is original: an authored pcbnew board (rotated and
mirrored footprints, slots, NPTH, a custom pad, same-number pads, keepouts, an
existing track and via, a narrow escape and an enclosed terminal) saved and
reloaded in KiCad, captured by ``placement_probe.routing_geometry``. Witnesses
are materialized on isolated copies with unchanged rules and checked by
kicad-cli DRC. The actual U23/C87 replay reads the pinned fome-mc store; no pod
design file is copied into this repository.
"""
from collections import Counter
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from toolchain import fome_mc_store, require_toolchain

from agent_ee import blocks, placement_access as access, placer
from agent_ee.placement_probe import extract, routing_geometry

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


# Terminal contact and witness semantics (agent-510 HOLD R1/R2): original 1 mm circular same-net lands.
CONTACT = {
    'outline': [0, 0, 20, 20],
    'footprints': [
        # 8.63 um physical gap: inside the ERROR_OUTSIDE rims, so only the outer bounds touch.
        {'ref': 'J1', 'at': [5, 5], 'rot': 0, 'pads': [pad('1', 'A', [0, 0], [1, 1], 'circle')]},
        {'ref': 'J2', 'at': [6, 4.868348], 'rot': 0, 'pads': [pad('1', 'A', [0, 0], [1, 1], 'circle')]},
        {'ref': 'J3', 'at': [5, 10], 'rot': 0, 'pads': [pad('1', 'B', [0, 0], [1, 1], 'circle')]},
        {'ref': 'J4', 'at': [10, 10], 'rot': 0, 'pads': [pad('1', 'B', [0, 0], [1, 1], 'circle')]},
        # Truly overlapping lands of different footprints: one component, natively connected.
        {'ref': 'T1', 'at': [5, 15], 'rot': 0, 'pads': [pad('1', 'C', [0, 0], [1, 1], 'circle')]},
        {'ref': 'T2', 'at': [5.8, 15], 'rot': 0, 'pads': [pad('1', 'C', [0, 0], [1, 1], 'circle')]},
        # A four-piece exposed pad, one number, overlapping pieces.
        {'ref': 'EP1', 'at': [14, 5], 'rot': 90, 'pads': [
            pad('9', 'D', [x, y], [1.0, 1.0]) for x in (-0.45, 0.45) for y in (-0.45, 0.45)]},
    ],
    'keepouts': [], 'tracks': [], 'vias': [],
}


def native_unconnected(board, out):
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '-o', str(out), str(board)],
                   check=True, capture_output=True)
    pairs = set()
    for item in json.loads(Path(out).read_text())['unconnected_items']:
        pairs.add(tuple(sorted(i['description'].split(' of ')[1].split(' ')[0] for i in item['items'])))
    return pairs


class ContactAndSemanticsTests(unittest.TestCase):
    """An outer-bound overlap never removes an obligation; a witness qualifies only for what it claims."""

    @classmethod
    def setUpClass(cls):
        require_toolchain()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-access-contact-')
        cls.dir = Path(cls.tmp.name)
        cls.board = build(cls.dir, CONTACT)
        cls.geometry = routing_geometry(cls.board)
        cls.report = access.analyze_access(cls.geometry, None, POLICY)
        cls.native = native_unconnected(cls.board, cls.dir / 'drc.json')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def pads(self, ref):
        return [p for p in self.geometry['pads'] if p['ref'] == ref]

    def test_near_gap_outer_overlap_keeps_the_native_obligation(self):
        j1, j2 = self.pads('J1')[0], self.pads('J2')[0]
        outer = [[tuple(q) for q in poly] for poly in j1['copper']['F.Cu']['outer']]
        other = [[tuple(q) for q in poly] for poly in j2['copper']['F.Cu']['outer']]
        self.assertTrue(any(access._polygons_touch(a, b) for a in outer for b in other))  # the rim overlaps
        self.assertIn(('J1', 'J2'), self.native)  # native KiCad: a missing connection
        first, second = terminal(self.report, 'J1', '1'), terminal(self.report, 'J2', '1')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['status'], 'not_required')
        self.assertNotEqual(second['status'], 'not_required')
        self.assertEqual(first['unresolved_contact'], [second['id']])
        self.assertTrue(any(w['kind'] == 'direct' and w['target'] == second['id'] for w in first['witnesses']))

    def test_truly_touching_lands_are_one_component(self):
        joined = terminal(self.report, 'T1', '1')
        self.assertEqual(sorted(p['ref'] for p in joined['pads']), ['T1', 'T2'])
        self.assertNotIn('unresolved_contact', joined)
        self.assertEqual(joined['status'], 'not_required')
        self.assertFalse([pair for pair in self.native if 'T1' in pair or 'T2' in pair])

    def test_exposed_pad_pieces_are_one_component(self):
        ep = terminal(self.report, 'EP1', '9')
        self.assertEqual(len({p['uuid'] for p in ep['pads']}), 4)
        self.assertEqual(ep['status'], 'not_required')
        self.assertFalse([pair for pair in self.native if 'EP1' in pair])

    def test_witness_semantics_are_checked_before_native_copper_legality(self):
        j3, j4 = terminal(self.report, 'J3', '1'), terminal(self.report, 'J4', '1')
        full = next(w for w in j3['witnesses'] if w['kind'] == 'direct' and len(w['path_nm']) == 2)
        self.assertEqual(full['target'], j4['id'])
        shortened = copy.deepcopy(full)
        shortened['path_nm'][-1] = [6 * MM, 10 * MM]  # legal copper, but it never reaches J4
        retargeted = copy.deepcopy(full)
        retargeted['target'] = j3['id']
        exit_ = copy.deepcopy(next(w for w in j3['witnesses'] if w['kind'] == 'exit'))
        exit_['path_nm'] = [exit_['path_nm'][0], [5 * MM + 200_000, 10 * MM]]  # still on its own land
        foreign = copy.deepcopy(full)
        foreign['source_pad'] = self.pads('J1')[0]['uuid']
        malformed = copy.deepcopy(full)
        malformed['path_nm'] = [full['path_nm'][0]]
        result = access.qualify(self.board, [[full], [shortened], [retargeted], [exit_], [foreign], [malformed]],
                                self.dir / 'semantics')
        groups = result['groups']
        self.assertEqual(groups[0]['status'], 'qualified', groups[0])
        before, after = groups[0]['unconnected_items']
        self.assertEqual(after, before - 1)  # the full direct link removes the J3-J4 opening natively
        self.assertEqual([g['status'] for g in groups[1:]], ['refused'] * 5)
        problems = [[f['problem'] for f in g['semantic_findings']] for g in groups[1:]]
        self.assertIn('direct path does not end inside the target pad copper', problems[0])
        self.assertIn('native connectivity: last track does not touch the target pad', problems[0])
        # Native DRC alone passes the shortened link: legal copper, no extra opening, no reach.
        self.assertEqual(groups[1]['new_findings'], [])
        self.assertEqual(groups[1]['unconnected_items'], [before, before])
        self.assertIn('direct target is not another same-net terminal component', problems[1])
        self.assertIn('exit path ends inside the footprint copper clearance box', problems[2])
        self.assertIn('witness net is not the source pad net', problems[3])
        self.assertEqual(problems[4], ['path_nm must list at least two integer points'])
        self.assertEqual(groups[5]['added_items'], [])  # a malformed witness is never written

    def test_witness_keys_follow_their_kind_and_empty_groups_refuse(self):
        """agent-510 R3/R4 and review-ee386b/ee386c H3: kind/key consistency and kind type before
        materialization; no empty evidence."""
        j3 = terminal(self.report, 'J3', '1')
        full = next(w for w in j3['witnesses'] if w['kind'] == 'direct' and len(w['path_nm']) == 2)
        exit_ = next(w for w in j3['witnesses'] if w['kind'] == 'exit')
        wide = access.analyze_access(self.geometry, None, POLICY, {'max_witnesses': 96})
        via = next(w for w in terminal(wide, 'J3', '1')['witnesses'] if w['kind'] == 'via')
        direct_via = {**copy.deepcopy(full), 'via': {}}  # agent-510: KeyError 'position_nm' before this fix
        direct_full_via = {**copy.deepcopy(full), 'via': copy.deepcopy(via['via'])}
        exit_target = {**copy.deepcopy(exit_), 'target': full['target']}
        direct_direction = {**copy.deepcopy(full), 'direction': 'up'}
        via_short = copy.deepcopy(via)
        del via_short['via']['position_nm']
        via_extra = copy.deepcopy(via)
        via_extra['via']['blind'] = True
        via_text = copy.deepcopy(via)
        via_text['via']['position_nm'] = ['0', '0']
        stray = {**copy.deepcopy(exit_), 'note': 'x'}
        groups = [[full], [exit_], [via], [direct_via], [direct_full_via], [exit_target], [direct_direction],
                  [via_short], [via_extra], [via_text], [stray], [], [full, direct_via], 'not a group', [None],
                  # agent-510 R4 / review-ee386c H3: an unhashable kind raised TypeError from the key lookup.
                  [{**copy.deepcopy(full), 'kind': []}], [{**copy.deepcopy(full), 'kind': {}}],
                  [{**copy.deepcopy(exit_), 'kind': ['exit']}]]
        result = access.qualify(self.board, groups, self.dir / 'kinds')
        statuses = [g['status'] for g in result['groups']]
        self.assertEqual(statuses[:3], ['qualified'] * 3, result['groups'][:3])
        self.assertEqual(statuses[3:], ['refused'] * (len(groups) - 3))
        for group in result['groups'][3:]:
            self.assertEqual(group['added_items'], [], group)  # never materialized
            self.assertIsNone(group['unconnected_items'], group)  # nor natively checked as if writable
            self.assertTrue(group['semantic_findings'], group)
        problems = [g['semantic_findings'][0]['problem'] if g['semantic_findings'] else None for g in result['groups']]
        self.assertEqual(problems[:3], [None] * 3)
        for i in (3, 4, 6):
            self.assertTrue(problems[i].startswith('direct witness needs exactly'), problems[i])
        self.assertTrue(problems[5].startswith('exit witness needs exactly'), problems[5])
        self.assertEqual(problems[7], problems[8])
        self.assertTrue(problems[7].startswith('a via witness needs a via with exactly'), problems[7])
        self.assertEqual(problems[9], 'via position_nm must be an integer point')
        self.assertTrue(problems[10].startswith('exit witness needs exactly'), problems[10])
        for i in (11, 13):
            self.assertEqual(result['groups'][i]['semantic_findings'],
                             [{'witness': None, 'problem': 'a witness group must list at least one witness'}])
        self.assertEqual(problems[14], 'a witness must be an object')
        self.assertEqual(result['groups'][12]['semantic_findings'][0]['witness'], 1)
        self.assertEqual(problems[15:], ['unknown witness kind []', 'unknown witness kind {}',
                                         "unknown witness kind ['exit']"])

    def test_routing_geometry_binds_to_the_native_placement_capture(self):
        import pcbnew
        self.assertEqual(access.BOXED_SHAPES, {pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_OVAL,
                                               pcbnew.PAD_SHAPE_ROUNDRECT})
        self.assertEqual(access.SPECIFIED_SHAPES, {pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_RECT,
                                                   pcbnew.PAD_SHAPE_OVAL})
        self.assertEqual(access.CHAMFERED_SHAPE, pcbnew.PAD_SHAPE_CHAMFERED_RECT)
        self.assertEqual(access.CUSTOM_SHAPE, pcbnew.PAD_SHAPE_CUSTOM)
        capture = extract(self.board)
        self.assertEqual(access.bind_capture(self.geometry, capture), {})
        self.assertEqual(access.bind_capture(self.geometry, capture, native=True), {})
        # A copper extent changed by more than the max-error rim refuses; within it, the bound still holds.
        forged = copy.deepcopy(self.geometry)
        pad = next(p for p in forged['pads'] if p['ref'] == 'J3')
        pad['copper']['F.Cu']['outer'] = [[[x + (20_000 if x > pad['position_nm'][0] else 0), y]
                                           for x, y in pad['copper']['F.Cu']['outer'][0]]]
        with self.assertRaisesRegex(ValueError, 'J3.1 .*outer copper extent differs'):
            access.bind_capture(forged, capture)

    def test_squared_circular_inner_copper_refuses(self):
        """review-ee386c H1: J1/J2 inner circles replaced by their 1 mm bounding squares (same extent)."""
        capture = extract(self.board)
        forged = copy.deepcopy(self.geometry)
        for ref in ('J1', 'J2'):
            pad = next(p for p in forged['pads'] if p['ref'] == ref)
            (cx, cy), h = pad['position_nm'], 500_000
            pad['copper']['F.Cu']['inner'] = [[[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]]
        # The forged contours would join J1 and J2 and erase the obligation native KiCad reports.
        merged = terminal(access.analyze_access(forged, None, POLICY), 'J1', '1')
        self.assertEqual(sorted(p['ref'] for p in merged['pads']), ['J1', 'J2'])
        self.assertEqual(merged['status'], 'not_required')
        self.assertIn(('J1', 'J2'), self.native)
        with self.assertRaisesRegex(ValueError, 'J1.1 .*inner copper contour differs from the captured land shape'):
            access.bind_capture(forged, capture)
        # Squaring the outer bound too (a superset) still refuses: the contour, not only the extent, is bound.
        for ref in ('J1', 'J2'):
            pad = next(p for p in forged['pads'] if p['ref'] == ref)
            pad['copper']['F.Cu']['outer'] = copy.deepcopy(pad['copper']['F.Cu']['inner'])
        with self.assertRaisesRegex(ValueError, 'J1.1 .*contour differs from the captured land shape'):
            access.bind_capture(forged, capture)


class NativeCaptureBindingTests(NativeFixture, unittest.TestCase):
    def test_rotated_mirrored_and_drilled_lands_bind_and_others_stay_unbound(self):
        capture = extract(self.board)
        unbound = access.bind_capture(self.geometry, capture)
        pads = {p['uuid']: p for p in self.geometry['pads']}
        # Circles, rectangles and ovals (30 deg rotated, back-side mirrored, PTH, slotted) bind by contour.
        # Rounded rectangles (radius not captured), the trapezoid and the custom land stay unbound.
        rounded = [(f['ref'], str(i)) for f in SPEC['footprints'] for i, p in enumerate(f['pads'], 1)
                   if p['shape'] == R]
        self.assertEqual(len(rounded), 16)
        self.assertEqual(sorted((pads[u]['ref'], pads[u]['number'], r) for u, r in unbound.items()),
                         sorted([(ref, n, 'land contour not bound by the placement capture') for ref, n in rounded]
                                + [('J1', '3', 'land contour not bound by the placement capture'),
                                   ('X1', '1', 'land contour not bound by the placement capture')]))
        bound = {(pads[u]['ref'], pads[u]['number']) for u in pads if u not in unbound and pads[u]['copper']}
        self.assertTrue({('U1', '9'), ('J1', '1'), ('J1', '2'), ('TP1', '1')} <= bound, bound)
        self.assertEqual(access.bind_capture(self.geometry, capture, native=True), {})
        # Without the native board an unbound land proves no contact and obstructs as its size box;
        # the trapezoid and custom land have no captured box, so coverage is unknown.
        _, analysed, coverage = access._bind(self.geometry, capture, False)
        r1 = next(p for p in analysed['pads'] if p['ref'] == 'R1' and p['number'] == '1')
        native_r1 = next(p for p in self.geometry['pads'] if p['uuid'] == r1['uuid'])
        self.assertEqual(r1['copper']['F.Cu']['inner'], [])
        box = r1['copper']['F.Cu']['outer'][0]
        self.assertTrue(all(access.inside(tuple(q), [tuple(b) for b in box])
                            for q in native_r1['copper']['F.Cu']['outer'][0]))
        self.assertEqual(sorted(c.split(' ')[0] for c in coverage), ['J1.3', 'X1.1'])
        forged = copy.deepcopy(self.geometry)
        hole = next(p for p in forged['pads'] if p['ref'] == 'J1' and p['number'] == '2')['hole']
        hole['drill_nm'] = [hole['drill_nm'][0], hole['drill_nm'][0]]
        with self.assertRaisesRegex(ValueError, 'drill size or plating differs'):
            access.bind_capture(forged, capture)
        forged = copy.deepcopy(self.geometry)
        next(p for p in forged['pads'] if p['ref'] == 'X1' and p['number'] == '1')['status'] = 'supported'
        with self.assertRaisesRegex(ValueError, 'custom land is never supported'):
            access.bind_capture(forged, capture)


def synthetic_geometry(capture, candidate):
    """A routing geometry of the block fixture with members at the candidate's frame poses, and its capture.

    Square raw copper per pad (the fixture's 0.4 mm rectangular lands) under
    the KiCad default rules; the anchor stays at its board pose. The returned
    placement capture is the fixture capture with the same poses. Pure data
    for the sidecar contract.
    """
    board = {fp['ref']: fp for fp in capture['footprints']}
    anchor = board[candidate['frame']['anchor']]
    footprints, pads, placed_capture = [], [], []
    turns = int(anchor['rotation_deg']) // 90
    for fp in capture['footprints']:
        row = next((r for r in candidate['members'] if r['reference'] == fp['ref']), None)
        if row is None:
            placed = fp
        else:
            x, y = placer.rotate(row['pose']['position_nm'], turns)
            placed = placer.transform(fp, [anchor['position_nm'][0] + x, anchor['position_nm'][1] + y],
                                      (row['pose']['rotation_deg'] + 90 * turns) % 360)
        placed = {k: v for k, v in placed.items() if not k.startswith('_')}
        placed_capture.append(placed)
        footprints.append({'ref': fp['ref'], 'uuid': fp['uuid'], 'side': fp['side'],
                           'position_nm': placed['position_nm'], 'rotation_deg': placed['rotation_deg']})
        for p in placed['pads']:
            (cx, cy), half = p['position_nm'], 200_000
            square = [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]]
            pads.append({'uuid': p['uuid'], 'ref': fp['ref'], 'footprint_uuid': fp['uuid'],
                         'number': p['number'], 'net': p['net'], 'attribute': 'SMD', 'shape': 1,
                         'position_nm': p['position_nm'], 'rotation_deg': p['rotation_deg'], 'status': 'supported',
                         'hole': None,
                         'copper': {'F.Cu': {'outer': [square], 'inner': [square]}},
                         'own_clearance_nm': {'F.Cu': 200_000}})
    nets = sorted({p['net'] for p in pads})
    outline = capture['outline']['polygons'][0]
    geometry = {'schema': access.GEOMETRY, 'board_sha256': capture['board_sha256'], 'pcbnew_version': 'fixture',
            'max_error_nm': 5000, 'copper_layers': ['F.Cu', 'B.Cu'],
            'rules': {'min_clearance_nm': 0, 'hole_clearance_nm': 250_000, 'hole_to_hole_nm': 250_000,
                      'copper_edge_clearance_nm': 500_000, 'min_track_width_nm': 200_000,
                      'min_via_diameter_nm': 500_000, 'min_via_annular_nm': 100_000,
                      'min_through_drill_nm': 300_000, 'custom_rules': {'status': 'absent'}},
            'track_clearance_nm': {n: {'F.Cu': 200_000, 'B.Cu': 200_000} for n in nets},
            'outline': {'status': 'proven', 'polygons': [outline]}, 'rule_areas': [],
            'footprints': footprints, 'pads': pads, 'tracks': [], 'vias': [], 'zones': []}
    return geometry, {**capture, 'footprints': placed_capture}


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
        geometry, capture = synthetic_geometry(self.fixture.board, candidate)
        frozen = copy.deepcopy(candidate)
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
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
        geometry, capture = synthetic_geometry(self.fixture.board, candidate)
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        self.assertIn(access.access_status(candidate, sidecar, geometry=geometry, capture=capture)['status'],
                      ('witnessed', 'partial', 'unknown'))
        self.assertEqual(blocks.admit(candidate, self.fixture.board, inventory, derived, policy), admitted)
        other = copy.deepcopy(candidate)
        other['search']['steps_used'] += 1
        with self.assertRaisesRegex(ValueError, 'another candidate'):
            access.access_status(other, sidecar, geometry=geometry, capture=capture)
        # A sidecar without the evidence it binds establishes nothing.
        with self.assertRaisesRegex(ValueError, 'needs the routing geometry and placement capture'):
            access.access_status(candidate, sidecar)

    def test_sidecar_refuses_a_board_that_does_not_hold_the_candidate_poses(self):
        candidate = self.pack()['candidate']
        # A consistent board (both captures) on which C1 sits 1 mm off its candidate frame pose.
        moved = copy.deepcopy(candidate)
        pose = next(r for r in moved['members'] if r['reference'] == 'C1')['pose']
        pose['position_nm'] = [pose['position_nm'][0] + MM, pose['position_nm'][1]]
        geometry, capture = synthetic_geometry(self.fixture.board, moved)
        with self.assertRaisesRegex(ValueError, 'C1: board pose differs'):
            access.block_access(candidate, geometry, POLICY, capture=capture)

    def bound(self):
        candidate = self.pack()['candidate']
        geometry, capture = synthetic_geometry(self.fixture.board, candidate)
        return candidate, geometry, capture

    def test_member_evidence_is_bound_to_the_candidate(self):
        """review-ee386b H1: missing, changed or foreign member pad evidence refuses."""
        candidate, geometry, capture = self.bound()
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        evidence = {e['reference']: e for e in sidecar['member_evidence']}
        self.assertEqual(set(evidence), {'U1', 'C1', 'L1', 'C3'})
        self.assertEqual(evidence['U1']['pad_geometry_sha256'], blocks.pad_geometry_sha256(
            next(f for f in capture['footprints'] if f['ref'] == 'U1')))
        self.assertEqual(sidecar['capture_sha256'], blocks.digest(capture))
        self.assertEqual(sidecar['unbound_pads'], [])

        def refuses(pattern, edit):
            g, c = copy.deepcopy(geometry), copy.deepcopy(capture)
            edit(g, c)
            with self.assertRaisesRegex(ValueError, pattern):
                access.block_access(candidate, g, POLICY, capture=c)

        def u1(record):
            return next(f for f in record['footprints'] if f['ref'] == 'U1')

        def drop_pads(g, c):
            g['pads'] = [p for p in g['pads'] if p['ref'] != 'U1']
        refuses('pads differ from the placement capture', drop_pads)

        def drop_everywhere(g, c):  # a consistent board whose U1 has lost its lands
            drop_pads(g, c)
            u1(c)['pads'] = []
        refuses('U1: board pad_geometry_sha256 differs', drop_everywhere)

        def renet(g, c):
            next(p for p in g['pads'] if p['ref'] == 'U1')['net'] = 'FORGED'
        refuses('identity, net or pose differs', renet)

        def renet_everywhere(g, c):
            uuid = next(p for p in g['pads'] if p['ref'] == 'U1')['uuid']
            for pad in g['pads'] + u1(c)['pads']:
                if pad['uuid'] == uuid:
                    pad['net'] = 'FORGED'
        refuses('U1: board pad nets differ from the candidate member pins', renet_everywhere)

        def reuuid(g, c):
            next(p for p in g['pads'] if p['ref'] == 'U1')['uuid'] = 'replaced'
        refuses('pads differ from the placement capture', reuuid)

        def shrink(g, c):  # changed copper at unchanged poses
            pad = next(p for p in g['pads'] if p['ref'] == 'U1')
            (cx, cy), half = pad['position_nm'], 100_000
            small = [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]]
            pad['copper']['F.Cu'] = {'outer': [small], 'inner': [small]}
        refuses('copper extent differs from the captured land size', shrink)

        def notch(g, c):  # same extent, carved copper
            pad = next(p for p in g['pads'] if p['ref'] == 'U1')
            (cx, cy), h = pad['position_nm'], 200_000
            star = [[cx - h, cy], [cx, cy - h], [cx + h, cy], [cx, cy + h]]
            pad['copper']['F.Cu'] = {'outer': [star], 'inner': [star]}
        refuses('copper contour differs from the captured land shape', notch)

        def small_notch(g, c):  # agent-510 R5: 80 um deep, 160 um tall notch, 8 % of the area, same extents
            pad = next(p for p in g['pads'] if p['uuid'] == 'U1-1')
            (cx, cy), h = pad['position_nm'], 200_000
            notched = [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy - 80_000], [cx + h - 80_000, cy - 80_000],
                       [cx + h - 80_000, cy + 80_000], [cx + h, cy + 80_000], [cx + h, cy + h], [cx - h, cy + h]]
            area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(notched, notched[1:] + notched[:1]))) / 2
            self.assertAlmostEqual(1 - area / (4 * h * h), 0.08)
            pad['copper']['F.Cu'] = {'outer': [notched], 'inner': [notched]}
        refuses('U1.1 .*outer copper contour differs from the captured land shape', small_notch)

        def outer_notch(g, c):  # the obstacle alone shrinks; the inner bound is unchanged
            small_notch(g, c)
            pad = next(p for p in g['pads'] if p['uuid'] == 'U1-1')
            (cx, cy), h = pad['position_nm'], 200_000
            pad['copper']['F.Cu']['inner'] = [[[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]]
        refuses('U1.1 .*outer copper contour differs from the captured land shape', outer_notch)

        def cut_corners(g, c):  # review-ee386c H1: 100 um corner triangles cut from a 400 um rectangle
            for pad in g['pads']:
                (cx, cy), h, k = pad['position_nm'], 200_000, 100_000
                cut = [[cx - h + k, cy - h], [cx + h - k, cy - h], [cx + h, cy - h + k], [cx + h, cy + h - k],
                       [cx + h - k, cy + h], [cx - h + k, cy + h], [cx - h, cy + h - k], [cx - h, cy - h + k]]
                pad['copper']['F.Cu'] = {'outer': [cut], 'inner': [cut]}
        refuses('outer copper contour differs from the captured land shape', cut_corners)

        def other_board(g, c):
            c['board_sha256'] = 'b' * 64
        refuses('different boards', other_board)

        def no_capture(g, c):
            c.clear()
        refuses('placement-probe/1 capture', no_capture)

        def moved_fp(g, c):
            fp = next(f for f in g['footprints'] if f['ref'] == 'U1')
            fp['position_nm'] = [fp['position_nm'][0] + 1, fp['position_nm'][1]]
        refuses('footprints differ', moved_fp)

        def max_error(g, c):  # a widened max-error rim would loosen every contour tolerance
            g['max_error_nm'] = 50_000
        refuses('max error differs', max_error)
        with self.assertRaises(TypeError):
            access.block_access(candidate, geometry, POLICY)  # the capture is required

    def test_copper_the_capture_cannot_bind_stays_unknown(self):
        candidate, geometry, capture = self.bound()
        pad = next(p for p in geometry['pads'] if p['ref'] == 'U1')
        square = pad['copper']['F.Cu']['outer'][0]
        pad['copper']['B.Cu'] = {'outer': [square], 'inner': [square]}  # a layer the capture does not record
        pad['own_clearance_nm']['B.Cu'] = 200_000
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        self.assertEqual(sidecar['unbound_pads'], [{'uuid': pad['uuid'],
                                                    'reason': 'copper layers differ from the placement capture'}])
        record = next(t for t in sidecar['terminals'] if any(p['uuid'] == pad['uuid'] for p in t['pads']))
        self.assertEqual(record['status'], 'unknown')
        self.assertEqual(record['reason'], 'coverage')  # rules without a native board are unbound
        access.validate_sidecar(candidate, sidecar, geometry=geometry, capture=capture)
        # The bound copper alone is unsupported, whatever the rule coverage.
        analysed = access._bind(geometry, capture, False)[1]
        alone = next(t for t in access.analyze_access(analysed, None, POLICY)['terminals']
                     if any(p['uuid'] == pad['uuid'] for p in t['pads']))
        self.assertEqual((alone['status'], alone['reason']), ('unknown', 'unsupported_geometry'))

    def test_stripped_copper_keeps_its_obligation_unknown(self):
        """review-ee386c H2: copper removed from the routing geometry cannot drop a member obligation."""
        candidate, geometry, capture = self.bound()
        pad = next(p for p in geometry['pads'] if p['uuid'] == 'U1-1')
        pad['copper'], pad['own_clearance_nm'] = {}, {}
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        self.assertEqual(sidecar['unbound_pads'], [{'uuid': 'U1-1',
                                                    'reason': 'copper layers differ from the placement capture'}])
        record = next(t for t in sidecar['terminals'] if any(p['uuid'] == 'U1-1' for p in t['pads']))
        self.assertEqual(record['status'], 'unknown')
        evidence = next(p for e in sidecar['member_evidence'] for p in e['pads'] if p['uuid'] == 'U1-1')
        self.assertTrue(evidence['copper'])

    def test_status_is_derived_from_validated_evidence(self):
        """review-ee386b H2: summaries are recounted; invented, partial or inconsistent sidecars refuse.

        Without the saved board the rules are unbound (review-ee386d H1) and every terminal is unknown;
        the witness-bearing forgeries run on the native pod sidecar (PodReplayTests).
        """
        candidate, geometry, capture = self.bound()
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        evidence = {'geometry': geometry, 'capture': capture}
        status = access.access_status(candidate, sidecar, **evidence)
        self.assertEqual(sidecar['contour_authority'], 'placement capture')
        self.assertEqual(sidecar['rule_authority'], {'status': 'unbound', 'reason': access.RULES_UNBOUND})
        # A sidecar written and read back as JSON (the CLI form) still re-derives exactly.
        self.assertEqual(access.access_status(candidate, json.loads(json.dumps(sidecar)), **evidence), status)
        counts = Counter(t['status'] for t in sidecar['terminals'])
        self.assertEqual(status['summary'], {k: counts.get(k, 0) for k in ('witness', 'unknown', 'not_required')})
        self.assertEqual(status, {'status': 'unknown', 'summary': {'witness': 0, 'unknown': 10, 'not_required': 0}})
        first = sidecar['terminals'][0]

        def refuses(pattern, edit):
            forged = copy.deepcopy(sidecar)
            edit(forged)
            with self.assertRaisesRegex(ValueError, pattern):
                access.access_status(candidate, forged, **evidence)
        refuses('needs exactly', lambda f: [f.pop(k) for k in list(f) if k not in
                                            ('schema', 'candidate_sha256', 'summary')])
        refuses('needs exactly', lambda f: f.pop('terminals'))
        refuses('needs exactly', lambda f: f.pop('policy'))
        refuses('needs exactly', lambda f: f.pop('member_evidence'))
        refuses('needs exactly', lambda f: f.pop('rule_authority'))
        refuses('summary is inconsistent', lambda f: f['summary'].update(witness=-1))
        refuses('summary is inconsistent', lambda f: f['summary'].update(unknown=f['summary']['unknown'] + 1))
        refuses('coverage status must follow', lambda f: f['coverage'].update(status='modelled'))

        def drop_terminal(f):
            f['terminals'].pop(0)
            f['summary']['unknown'] -= 1
        refuses('do not cover exactly the member copper pads', drop_terminal)

        def promote(f):
            target = f['terminals'][0]
            target['witnesses'] = [{'kind': 'exit', 'source_pad': target['pads'][0]['uuid'], 'net': target['net'],
                                    'layer': 'F.Cu', 'track_width_nm': POLICY['track_width_nm'],
                                    'path_nm': [[0, 0], [MM, 0]], 'direction': 'right'}]
            f['summary']['unknown'] -= 1
            target['status'] = 'witness'
            f['summary']['witness'] += 1
        refuses('witness terminal without modelled coverage, bound rules', promote)

        # review-ee386d H1: rules the placement capture does not bind stay unbound coverage.
        def bind_rules(f):
            f['rule_authority'] = {'status': 'bound', 'board_sha256': f['board_sha256'],
                                   'project_sha256': None, 'custom_rules_sha256': None}
        refuses('must state its rules as unbound coverage', bind_rules)

        def drop_unbound_reason(f):
            f['coverage']['reasons'].remove(access.RULES_UNBOUND)
            f['coverage']['status'] = 'unknown' if f['coverage']['reasons'] else 'modelled'
        refuses('must state its rules as unbound coverage', drop_unbound_reason)
        refuses('rule authority needs exactly', lambda f: f.update(contour_authority='native board'))

        def native_relabel(f):
            f['contour_authority'] = 'native board'
            bind_rules(f)
        refuses('needs its saved board', native_relabel)
        refuses('contour_authority must be', lambda f: f.update(contour_authority='owner'))

        def other_pins(f):
            f['member_evidence'][0]['pads'][0]['net'] = 'FORGED'
        refuses('member pad nets differ from candidate pins', other_pins)
        refuses('limits must state', lambda f: f['limits'].pop('grid_nm'))
        refuses('another candidate', lambda f: f.update(candidate_sha256='0' * 64))

        # review-ee386c H2: records that stay self-consistent cannot change what the evidence obliges.
        def recount(f):
            counts = Counter(t['status'] for t in f['terminals'])
            f['summary'] = {k: counts.get(k, 0) for k in access.STATUSES}

        def suppress_copper(f):  # nine member copper flags cleared, their terminals deleted, one terminal kept
            keep = first['pads'][0]['uuid']
            gone = {p['uuid'] for e in f['member_evidence'] for p in e['pads'] if p['copper'] and p['uuid'] != keep}
            self.assertGreaterEqual(len(gone), 9)
            for record in f['member_evidence']:
                for pad in record['pads']:
                    pad['copper'] = pad['copper'] and pad['uuid'] not in gone
            f['terminals'] = [t for t in f['terminals'] if not {p['uuid'] for p in t['pads']} & gone]
            recount(f)
            self.assertEqual(access._validate_structure(candidate, f)['unknown'], 1)  # the structure accepts it
        refuses('differs from the evidence it binds', suppress_copper)

        def invented_exemption(f):  # an unknown terminal turned into an unproven not_required exemption
            record = next(t for t in f['terminals'] if t['id'] == first['id'])
            for key in ('witnesses', 'attempted', 'truncated', 'blockers', 'source_pad', 'details'):
                record.pop(key, None)
            record.update(status='not_required', reason='no other terminal on the net')
            recount(f)
            access._validate_structure(candidate, f)
        refuses('differs from the evidence it binds', invented_exemption)

        def forged_geometry(f):  # evidence other than the one the sidecar binds
            evidence['geometry'] = {**geometry, 'pcbnew_version': 'other'}
        try:
            refuses('bound to another routing geometry', forged_geometry)
        finally:
            evidence['geometry'] = geometry

    def test_unbound_rules_never_witness(self):
        """review-ee386d H1 / agent-510: rules beside a placement capture are unknown coverage, never witnessed.

        Capture and candidate are fixed. A 10 mm rule floor leaves every terminal unknown; lowering it to
        0, or erasing a present custom rule, used to turn the same ten unknowns into ten witnesses.
        """
        candidate, geometry, capture = self.bound()
        high = copy.deepcopy(geometry)
        high['rules']['min_clearance_nm'] = 10_000_000
        custom = copy.deepcopy(geometry)
        custom['rules']['custom_rules'] = {'status': 'present', 'rules': ['unsupported-native-rule']}
        erased = copy.deepcopy(geometry)
        del erased['rules']['custom_rules']
        unknown = {'status': 'unknown', 'summary': {'witness': 0, 'unknown': 10, 'not_required': 0}}
        for label, rules in (('high floor', high), ('lowered floor', geometry), ('custom present', custom),
                             ('custom erased', geometry), ('custom key erased', erased)):
            with self.subTest(label):
                sidecar = access.block_access(candidate, rules, POLICY, capture=capture)
                self.assertEqual(sidecar['rule_authority'], {'status': 'unbound', 'reason': access.RULES_UNBOUND})
                self.assertEqual(sidecar['coverage']['status'], 'unknown')
                self.assertIn(access.RULES_UNBOUND, sidecar['coverage']['reasons'])
                self.assertEqual(sidecar['summary'], unknown['summary'])
                self.assertTrue(all(t['status'] == 'unknown' and t['reason'] == 'coverage'
                                    for t in sidecar['terminals']))
                self.assertEqual(access.access_status(candidate, sidecar, geometry=rules, capture=capture), unknown)
        # The pure diagnostic helper is unchanged and non-authoritative: it still reports its model's search.
        self.assertEqual(access.analyze_access(geometry, None, POLICY)['coverage']['status'], 'modelled')
        self.assertGreater(access.analyze_access(geometry, None, POLICY)['summary']['witness'], 0)
        self.assertEqual(access.analyze_access(high, None, POLICY)['summary']['witness'], 0)

    def test_deleting_an_unknown_obligation_refuses(self):
        """review-ee386c H2: a real layer-mismatch unknown cannot be deleted from the obligations."""
        candidate, geometry, capture = self.bound()
        pad = next(p for p in geometry['pads'] if p['uuid'] == 'U1-1')
        square = pad['copper']['F.Cu']['outer'][0]
        pad['copper']['B.Cu'] = {'outer': [square], 'inner': [square]}
        pad['own_clearance_nm']['B.Cu'] = 200_000
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture)
        status = access.access_status(candidate, sidecar, geometry=geometry, capture=capture)
        self.assertEqual(status['status'], 'unknown')
        forged = copy.deepcopy(sidecar)
        for record in forged['member_evidence']:
            for land in record['pads']:
                if land['uuid'] == 'U1-1':
                    land['copper'] = False
        forged['terminals'] = [t for t in forged['terminals'] if not any(p['uuid'] == 'U1-1' for p in t['pads'])]
        counts = Counter(t['status'] for t in forged['terminals'])
        forged['summary'] = {k: counts.get(k, 0) for k in access.STATUSES}
        access._validate_structure(candidate, forged)  # the structure alone accepts it
        with self.assertRaisesRegex(ValueError, 'differs from the evidence it binds'):
            access.access_status(candidate, forged, geometry=geometry, capture=capture)


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

    def native_sidecar(self, board=None):
        board = board or self.board
        candidate = json.loads(self.candidate_raw)
        geometry = self.geometry if board == self.board else routing_geometry(board)
        capture = extract(board)
        sidecar = access.block_access(candidate, geometry, POLICY, capture=capture, board=board)
        return candidate, sidecar, {'geometry': geometry, 'capture': capture, 'board': board}

    def copy_board(self, name):
        directory = self.dir / name
        directory.mkdir()
        for suffix in ('.kicad_pcb', '.kicad_pro'):
            shutil.copyfile(self.board.with_suffix(suffix), (directory / 'candidate').with_suffix(suffix))
        return directory / 'candidate.kicad_pcb'

    def test_native_rule_authority_is_hash_bound(self):
        """review-ee386d H1: native rules are bound by the board, project and custom rule file hashes."""
        candidate, sidecar, evidence = self.native_sidecar()
        self.assertEqual(sidecar['rule_authority'], {
            'status': 'bound', 'board_sha256': POD_BOARD_SHA256, 'custom_rules_sha256': None,
            'project_sha256': hashlib.sha256(self.board.with_suffix('.kicad_pro').read_bytes()).hexdigest()})
        self.assertNotIn(access.RULES_UNBOUND, sidecar['coverage']['reasons'])
        status = access.access_status(candidate, sidecar, **evidence)
        self.assertIn(status['status'], ('witnessed', 'partial'))
        witnessed = next(t for t in sidecar['terminals'] if t['status'] == 'witness')

        def refuses(pattern, edit):
            forged = copy.deepcopy(sidecar)
            edit(forged)
            with self.assertRaisesRegex(ValueError, pattern):
                access.access_status(candidate, forged, **evidence)

        def unknown_coverage(f):
            f['coverage'].update(status='unknown', reasons=['native custom rules present'])
        refuses('witness terminal without modelled coverage', unknown_coverage)
        refuses('rule authority needs exactly', lambda f: f.update(rule_authority={'status': 'unbound',
                                                                                     'reason': access.RULES_UNBOUND}))
        refuses('native rule authority must be bound to its board',
                lambda f: f['rule_authority'].update(board_sha256='0' * 64))
        refuses('lowercase SHA-256', lambda f: f['rule_authority'].update(project_sha256='forged'))
        # A well-formed rule authority of other rule files: the re-derived evidence differs.
        refuses('differs from the evidence it binds', lambda f: f['rule_authority'].update(project_sha256='0' * 64))
        refuses('differs from the evidence it binds',
                lambda f: f['rule_authority'].update(custom_rules_sha256='0' * 64))

        def no_witnesses(f):
            next(t for t in f['terminals'] if t['id'] == witnessed['id'])['witnesses'] = []
        refuses('witness terminal without', no_witnesses)

        def foreign_source(f):
            record = next(t for t in f['terminals'] if t['id'] == witnessed['id'])
            record['witnesses'][0]['source_pad'] = 'elsewhere'
        refuses('witness source, net, layer or width differs', foreign_source)

        def via_on_direct(f):
            record = next(t for t in f['terminals'] if t['id'] == witnessed['id'])
            record['witnesses'][0]['via'] = {}
            if record['witnesses'][0]['kind'] == 'via':
                record['witnesses'][0]['kind'] = 'exit'
        refuses('witness needs exactly', via_on_direct)

        def invented_exemption(f):  # a witnessed terminal turned into an unproven not_required exemption
            record = next(t for t in f['terminals'] if t['id'] == witnessed['id'])
            for key in ('witnesses', 'attempted', 'truncated', 'blockers', 'source_pad'):
                record.pop(key, None)
            record.update(status='not_required', reason='no other terminal on the net')
            counts = Counter(t['status'] for t in f['terminals'])
            f['summary'] = {k: counts.get(k, 0) for k in access.STATUSES}
            access._validate_structure(candidate, f)
        refuses('differs from the evidence it binds', invented_exemption)

    def test_substituted_native_rules_are_unknown_or_refused(self):
        """review-ee386d H1 on the native board: lowered, erased or replaced rules never witness."""
        candidate, sidecar, evidence = self.native_sidecar()
        capture, board = evidence['capture'], evidence['board']
        lowered = copy.deepcopy(self.geometry)
        floor = next(k for k in ('min_clearance_nm', 'hole_clearance_nm', 'copper_edge_clearance_nm')
                     if lowered['rules'][k] > 0)
        lowered['rules'][floor] = 0
        erased = copy.deepcopy(self.geometry)
        self.assertEqual(erased['rules']['custom_rules'], {'status': 'absent'})
        erased['rules']['custom_rules'] = {'status': 'empty', 'sha256': '0' * 64, 'rules': []}
        for label, geometry in (('lowered floor', lowered), ('replaced custom rules', erased)):
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, 'routing geometry differs from the native capture'):
                    access.block_access(candidate, geometry, POLICY, capture=capture, board=board)
                with self.assertRaisesRegex(ValueError, 'bound to another routing geometry'):
                    access.access_status(candidate, sidecar, **{**evidence, 'geometry': geometry})
                plain = access.block_access(candidate, geometry, POLICY, capture=capture)
                self.assertEqual(plain['summary']['witness'], 0)
        # A native custom rule file: every terminal unknown; erasing it afterwards refuses.
        ruled = self.copy_board('custom-rules')
        ruled.with_suffix('.kicad_dru').write_text(
            '(version 1)\n(rule "owned_fixture" (constraint clearance (min 0.3mm))\n'
            '  (condition "A.NetName == \'/GND\'"))\n')
        candidate, present, evidence = self.native_sidecar(ruled)
        self.assertEqual(present['rule_authority']['custom_rules_sha256'],
                         hashlib.sha256(ruled.with_suffix('.kicad_dru').read_bytes()).hexdigest())
        self.assertEqual(present['coverage']['status'], 'unknown')
        self.assertEqual(present['summary']['witness'], 0)
        self.assertEqual(access.access_status(candidate, present, **evidence)['status'], 'unknown')
        erased = copy.deepcopy(evidence['geometry'])
        erased['rules']['custom_rules'] = {'status': 'absent'}
        with self.assertRaisesRegex(ValueError, 'routing geometry differs from the native capture'):
            access.block_access(candidate, erased, POLICY, capture=evidence['capture'], board=ruled)
        ruled.with_suffix('.kicad_dru').unlink()
        with self.assertRaisesRegex(ValueError, 'differs from the native capture|differs from the evidence it binds'
                                                '|custom rules differ from the board custom rule file'):
            access.access_status(candidate, present, **evidence)
        # A project replaced after the sidecar: its hash no longer binds (pcbnew may cache the old settings).
        project = self.copy_board('project')
        candidate, bound, evidence = self.native_sidecar(project)
        self.assertIn(access.access_status(candidate, bound, **evidence)['status'], ('witnessed', 'partial'))
        settings = json.loads(project.with_suffix('.kicad_pro').read_text())
        rules = settings['board']['design_settings']['rules']
        rules['min_clearance'] = rules['min_clearance'] + 0.05
        project.with_suffix('.kicad_pro').write_text(json.dumps(settings, indent=2))
        with self.assertRaisesRegex(ValueError, 'differs from the native capture|differs from the evidence it binds'):
            access.access_status(candidate, bound, **evidence)

    def test_project_edited_after_native_load_never_binds_cached_rules(self):
        """review-ee386e H1: load, edit the project, then issue: this process's cached rules never bind."""
        board = self.copy_board('cache-order')
        candidate, before, evidence = self.native_sidecar(board)
        self.assertIn(access.access_status(candidate, before, **evidence)['status'], ('witnessed', 'partial'))
        project = board.with_suffix('.kicad_pro')
        settings = json.loads(project.read_text())
        settings['board']['design_settings']['rules']['min_clearance'] = 10.0
        project.write_text(json.dumps(settings))
        # pcbnew may reuse the project it loaded first: whatever this process reads is never authority.
        stale = routing_geometry(board)
        stale_capture = extract(board)
        print('\nAGENTEE-386 same-process floor after project edit:', stale['rules']['min_clearance_nm'], 'nm',
              file=sys.stderr)
        if stale['rules']['min_clearance_nm'] != 10 * MM:
            with self.assertRaisesRegex(ValueError, 'routing geometry differs from the native capture'):
                access.block_access(candidate, stale, POLICY, capture=stale_capture, board=board)
        else:  # an uncached load read the edited project: it binds exactly like a fresh capture
            self.assertEqual(access.block_access(candidate, stale, POLICY, capture=stale_capture,
                                                 board=board)['summary']['witness'], 0)
        with self.assertRaisesRegex(ValueError, 'differs from the native capture|differs from the evidence it binds'):
            access.access_status(candidate, before, **evidence)
        # The captures a fresh interpreter takes of the edited snapshot bind its bytes and its 10 mm floor.
        fresh, fresh_capture, hashes = access._native_capture(board)
        self.assertEqual(fresh['rules']['min_clearance_nm'], 10 * MM)
        self.assertEqual(hashes['.kicad_pro'], hashlib.sha256(project.read_bytes()).hexdigest())
        after = access.block_access(candidate, fresh, POLICY, capture=fresh_capture, board=board)
        self.assertEqual(after['rule_authority']['project_sha256'], hashes['.kicad_pro'])
        self.assertEqual(after['summary']['witness'], 0)
        self.assertEqual(access.access_status(candidate, after, geometry=fresh, capture=fresh_capture,
                                              board=board)['status'], 'unknown')

    def test_actual_candidate_sidecar_binds_and_witnesses_qualify(self):
        candidate = json.loads(self.candidate_raw)
        capture = extract(self.board)
        sidecar = access.block_access(candidate, self.geometry, POLICY, capture=capture, board=self.board)
        self.assertEqual(sidecar['candidate_sha256'], blocks.digest(candidate))
        self.assertEqual(sidecar['contour_authority'], 'native board')
        self.assertEqual(sidecar['unbound_pads'], [])
        self.assertIn('U23', sidecar['pad_geometry_sha256'])
        self.assertEqual(sidecar['coverage']['status'], 'modelled')
        evidence = {'geometry': self.geometry, 'capture': capture, 'board': self.board}
        self.assertIn(access.access_status(candidate, sidecar, **evidence)['status'], ('witnessed', 'partial'))
        # The native board is the contour authority: a capture the board does not reproduce refuses.
        notched = copy.deepcopy(self.geometry)
        u23 = next(p for p in notched['pads'] if p['ref'] == 'U23' and p['number'] == '7')
        layer = sorted(u23['copper'])[0]
        u23['copper'][layer]['outer'][0] = u23['copper'][layer]['outer'][0][:-1]
        with self.assertRaisesRegex(ValueError, 'routing geometry differs from the native capture'):
            access.block_access(candidate, notched, POLICY, capture=capture, board=self.board)
        with self.assertRaisesRegex(ValueError, 'needs its saved board'):
            access.access_status(candidate, sidecar, geometry=self.geometry, capture=capture)
        # Without the board, contours the placement capture does not specify are never witnessed.
        shapes = {p['uuid']: p['shape'] for p in self.geometry['pads']}
        plain = access.block_access(candidate, self.geometry, POLICY, capture=capture)
        self.assertEqual(plain['contour_authority'], 'placement capture')
        # ... and without the board its rules are unbound: nothing is witnessed (review-ee386d H1).
        self.assertEqual(plain['rule_authority']['status'], 'unbound')
        self.assertEqual(plain['summary']['witness'], 0)
        self.assertEqual(access.access_status(candidate, plain, geometry=self.geometry, capture=capture)['status'],
                         'unknown')
        unbound = {u['uuid'] for u in plain['unbound_pads']}
        self.assertTrue(unbound)
        self.assertTrue(all(shapes[u] not in access.SPECIFIED_SHAPES for u in unbound))
        for record in plain['terminals']:
            if record['status'] == 'witness':
                self.assertFalse({p['uuid'] for p in record['pads']} & unbound, record['id'])
                self.assertFalse({w.get('target') for w in record['witnesses']}
                                 & {t['id'] for t in plain['terminals'] if {p['uuid'] for p in t['pads']} & unbound})
        # review-ee386b H1 on the actual pod: removed or renetted U23 lands refuse, never witness.
        stripped = {**self.geometry, 'pads': [p for p in self.geometry['pads'] if p['ref'] != 'U23']}
        with self.assertRaisesRegex(ValueError, 'pads differ from the placement capture'):
            access.block_access(candidate, stripped, POLICY, capture=capture)
        for number, pattern in (('7', 'U23: board pad nets differ from the candidate member pins'),
                                ('', 'U23: an unnumbered pad carries a net')):  # '' : copper-less paste pieces
            capture_forged = copy.deepcopy(capture)
            land = next(p for f in capture_forged['footprints'] if f['ref'] == 'U23' for p in f['pads']
                        if p['number'] == number)
            land['net'] = '/FORGED'
            forged = {**self.geometry, 'pads': [{**p, 'net': '/FORGED'} if p['uuid'] == land['uuid'] else p
                                                for p in self.geometry['pads']]}
            with self.assertRaisesRegex(ValueError, pattern):
                access.block_access(candidate, forged, POLICY, capture=capture_forged)
        groups = [t['witnesses'] for t in sidecar['terminals'] if t['status'] == 'witness']
        self.assertTrue(groups)
        result = access.qualify(self.board, groups, self.dir / 'block')
        self.assertEqual([g['status'] for g in result['groups'] if g['status'] != 'qualified'], [], result)
        print(f'\nAGENTEE-386 pod access capture+analysis: {self.seconds:.3f} s '
              f"({len(self.report['terminals'])} terminals; provisional ceiling 15.95 s)", file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
