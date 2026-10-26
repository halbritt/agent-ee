"""Diagnostic local copper access on a complete candidate board (AGENTEE-386, routability plan M3a).

Pure, bounded and geometry-based; not a router. Input is the opt-in
``agent-ee.routing-geometry/1`` capture (``placement_probe.routing_geometry``):
raw native pad copper and drills, native own clearances, tracks, vias, outline,
rule areas and the board rule floor. For each conductive terminal component
(same-net pads whose inner copper bounds touch) a finite, deterministic candidate
set is tested in the stated model:

1. direct straight or one-bend links to another same-net component;
2. straight and one-bend exits from the pad to a local shell around its footprint;
3. short stubs to an approved through via that reaches another routing layer.

A result is ``witness`` (clear alternatives with margins; never mutually
compatible reservations), ``unknown`` (no witness in the finite search, budget,
unsupported geometry or rules, incomplete coverage) or ``not_required``. A
finite-search failure is never infeasibility: there is no ``proven_no_access``
and nothing here can reject a placement. Different-net copper is enlarged by
half the track width plus max(board minimum, both native own clearances);
holes by the hole clearance (vias also hole-to-hole); the outline by the
copper-edge clearance; track/via keepouts block their item. Native custom rules
and copper zones are not evaluated: present custom rules make every terminal
unknown, and a different-net zone makes its layer unknown.

Native KiCad remains the authority: ``qualify`` materializes witnesses on an
isolated copy with unchanged rules, refills and runs kicad-cli DRC in the
existing sandbox. The ``agent-ee.block-access/1`` sidecar binds the strict
candidate/2 digest and, through the placement capture of the same board, every
member's land pattern, courtyard, pads and nets. Copper contours are bound
shape-specifically to the placement capture, or exactly to fresh native
captures of the saved board (``contour_authority``). Board rules, own
clearances and custom-rule presence are authoritative only from the saved
board: ``rule_authority`` hash-binds the board, project and custom rule bytes
of one snapshot that a fresh interpreter captured, and without them every
terminal is unknown. It is written only in
diagnostic mode and never changes a candidate, its order or its admission. Its
status is derived from terminal evidence re-derived from the captures it binds.
See docs/placement-access.md.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .catalog import strict_json
from .storage import identity as value_digest
from .placer import rotate

SCHEMA = 'agent-ee.placement-access/1'
SIDECAR = 'agent-ee.block-access/1'
GEOMETRY = 'agent-ee.routing-geometry/1'
SEARCH = 'bounded-local-access/1'
QUALIFICATION = 'agent-ee.access-qualification/1'
# Search budget, not physical limits (plan section 4.2: provisional eight witnesses).
LIMITS = {'max_witnesses': 8, 'max_candidates': 96, 'direct_max_nm': 5_000_000, 'grid_nm': 10_000,
          'shell_nm': None}
# KiCad board y grows downward. Existing deterministic order: normals, then diagonals.
DIRECTIONS = (('up', (0, -1)), ('down', (0, 1)), ('left', (-1, 0)), ('right', (1, 0)),
              ('up-left', (-1, -1)), ('up-right', (1, -1)), ('down-left', (-1, 1)), ('down-right', (1, 1)))
CELL_NM = 1_000_000
# A stub ends open by construction; only these native findings on materialized items are expected.
DANGLING = frozenset(('track_dangling', 'via_dangling'))


# ------------------------------------------------------------------ geometry

def _box(points, grow=0):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow)


def _point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx*dx + dy*dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / length2))
    return math.hypot(a[0] + t*dx - p[0], a[1] + t*dy - p[1])


def _orient(a, b, c):
    value = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    return (value > 0) - (value < 0)


def _crosses(a, b, c, d):
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True

    def on(p, q, r):
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    return ((o1 == 0 and on(a, b, c)) or (o2 == 0 and on(a, b, d)) or (o3 == 0 and on(c, d, a))
            or (o4 == 0 and on(c, d, b)))


def segment_distance(a, b, c, d):
    """Distance between segments ab and cd (zero when they meet)."""
    if _crosses(a, b, c, d):
        return 0.0
    return min(_point_segment(a, c, d), _point_segment(b, c, d), _point_segment(c, a, b), _point_segment(d, a, b))


def inside(point, polygon):
    """Even-odd containment; a point on the boundary may fall either way, callers add margins."""
    x, y, result = point[0], point[1], False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            result = not result
    return result


def _edges(polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def shape_distance(a, b, shape):
    """Distance from segment ab to an obstacle shape: ('poly', points) or ('capsule', c, d, radius)."""
    if shape[0] == 'capsule':
        return segment_distance(a, b, shape[1], shape[2]) - shape[3]
    polygon = shape[1]
    if inside(a, polygon) or inside(b, polygon):
        return 0.0
    return min(segment_distance(a, b, p, q) for p, q in _edges(polygon))


def _polygons_touch(left, right):
    if inside(left[0], right) or inside(right[0], left):
        return True
    return any(_crosses(a, b, c, d) for a, b in _edges(left) for c, d in _edges(right))


# ------------------------------------------------------------------- inputs

def validate_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get('schema') != GEOMETRY:
        raise ValueError(f'local access needs an {GEOMETRY} capture')
    for key in ('board_sha256', 'copper_layers', 'rules', 'track_clearance_nm', 'outline', 'rule_areas',
                'footprints', 'pads', 'tracks', 'vias', 'zones', 'max_error_nm'):
        if key not in geometry:
            raise ValueError(f'routing geometry lacks {key}')
    refs = Counter(fp['ref'] for fp in geometry['footprints'])
    if any(count > 1 for count in refs.values()):
        raise ValueError('routing geometry has duplicate footprint references: '
                         + ', '.join(sorted(r for r, n in refs.items() if n > 1)))
    uuids = Counter(p['uuid'] for p in geometry['pads'])
    if any(count > 1 for count in uuids.values()):
        raise ValueError('routing geometry has duplicate pad UUIDs')
    return geometry


def _nm(value, label, positive=True):
    if type(value) is not int or (positive and value <= 0):
        raise ValueError(f'{label} must be a positive integer nm')
    return value


def routing_policy(policy, geometry):
    """Validate the declared track, routing layers and approved through vias against the captured rule floor."""
    if not isinstance(policy, dict) or set(policy) - {'track_width_nm', 'routing_layers', 'vias'} or \
            {'track_width_nm', 'routing_layers'} - set(policy):
        raise ValueError('routing policy needs track_width_nm and routing_layers, optionally vias')
    rules, layers = geometry['rules'], geometry['copper_layers']
    width = _nm(policy['track_width_nm'], 'track_width_nm')
    if width < rules['min_track_width_nm']:
        raise ValueError('track_width_nm is below the captured minimum track width')
    routing = policy['routing_layers']
    if (not isinstance(routing, list) or not routing or len(set(routing)) != len(routing)
            or any(layer not in layers for layer in routing)):
        raise ValueError('routing_layers must list distinct captured copper layers')
    vias = []
    for via in policy.get('vias', []):
        if not isinstance(via, dict) or set(via) != {'diameter_nm', 'drill_nm'}:
            raise ValueError('an approved via needs exactly diameter_nm and drill_nm (through via)')
        diameter, drill = _nm(via['diameter_nm'], 'via diameter_nm'), _nm(via['drill_nm'], 'via drill_nm')
        if (diameter < rules['min_via_diameter_nm'] or drill < rules['min_through_drill_nm']
                or (diameter - drill) < 2 * rules['min_via_annular_nm']):
            raise ValueError('approved via is below the captured via diameter, drill or annular minimum')
        vias.append({'diameter_nm': diameter, 'drill_nm': drill})
    return {'track_width_nm': width, 'routing_layers': [l for l in layers if l in routing], 'vias': vias}


def search_limits(limits):
    out = dict(LIMITS)
    for key, value in (limits or {}).items():
        if key not in LIMITS:
            raise ValueError(f'unknown access limit {key}')
        if value is not None or key != 'shell_nm':
            _nm(value, key)
        out[key] = value
    return out


def _turns(delta):
    turns = delta / 90
    if not math.isfinite(turns) or turns != round(turns):
        raise ValueError('a local access pose may change rotation only by quarter turns')
    return int(round(turns)) % 4


def terminal_components(geometry, pad_shape):
    """Same-net pads whose copper soundly touches on a shared layer form one terminal component.

    A join needs contact of the native inner bounds (ERROR_INSIDE, a subset of
    the real copper), so it is physical contact. Touching outer bounds
    (ERROR_OUTSIDE, a superset) alone never join: two pads within the max-error
    rim may have a real gap, and merging them would remove a connection
    obligation that native KiCad still reports. Such pairs stay separate
    components and are listed under ``unresolved_contact``; the extra
    obligation is conservative. ``pad_shape(uuid, layer, bound)`` gives the
    (posed) polygons.
    """
    pads = sorted((p for p in geometry['pads'] if p['copper']), key=lambda p: (p['ref'], p['number'], p['uuid']))
    parent = {p['uuid']: p['uuid'] for p in pads}

    def find(u):
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    def touch(left, right, layer, bound):
        lp, rp = pad_shape(left['uuid'], layer, bound), pad_shape(right['uuid'], layer, bound)
        if not lp or not rp:
            return False
        lb, rb = _box([q for poly in lp for q in poly]), _box([q for poly in rp for q in poly])
        if lb[2] < rb[0] or rb[2] < lb[0] or lb[3] < rb[1] or rb[3] < lb[1]:
            return False
        return any(_polygons_touch(a, b) for a in lp for b in rp)
    by_net, rim = {}, []
    for pad in pads:
        by_net.setdefault(pad['net'], []).append(pad)
    for net, group in by_net.items():
        if not net:
            continue
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if find(left['uuid']) == find(right['uuid']):
                    continue
                layers = sorted(set(left['copper']) & set(right['copper']))
                if any(touch(left, right, layer, 'inner') for layer in layers):
                    parent[find(right['uuid'])] = find(left['uuid'])
                elif any(touch(left, right, layer, 'outer') for layer in layers):
                    rim.append((left['uuid'], right['uuid']))
    groups = {}
    for pad in pads:
        groups.setdefault(find(pad['uuid']), []).append(pad)
    ids = {}
    for members in groups.values():
        first = members[0]
        ids[find(first['uuid'])] = f"{first['ref']}.{first['number']}@{first['uuid']}"
    unresolved = {}
    for left, right in rim:
        a, b = ids[find(left)], ids[find(right)]
        if a != b:
            unresolved.setdefault(a, set()).add(b)
            unresolved.setdefault(b, set()).add(a)
    out = []
    for root, members in groups.items():
        record = {'id': ids[root], 'net': members[0]['net'],
                  'pads': [{'ref': p['ref'], 'number': p['number'], 'uuid': p['uuid']} for p in members]}
        if ids[root] in unresolved:
            record['unresolved_contact'] = sorted(unresolved[ids[root]])
        out.append(record)
    return sorted(out, key=lambda c: c['id'])


# -------------------------------------------------------------------- index

class AccessIndex:
    """Spatial index of the captured obstacles with exact push/pop of footprint poses.

    ``push(ref, pose)`` moves one footprint's pads, drills and footprint-owned
    rule areas by the placer's quarter-turn convention (``placer.rotate``);
    ``pop()`` restores the previous entries exactly. ``state()`` is the
    canonical content for equality tests against a fresh index.
    """

    def __init__(self, geometry, policy, limits=None):
        self.geometry = validate_geometry(geometry)
        self.policy = routing_policy(policy, geometry)
        self.limits = search_limits(limits)
        self.rules = geometry['rules']
        self.footprints = {fp['ref']: fp for fp in geometry['footprints']}
        self.owner = {fp['uuid']: fp['ref'] for fp in geometry['footprints']}
        self.pose = {ref: {'position_nm': list(fp['position_nm']), 'rotation_deg': fp['rotation_deg']}
                     for ref, fp in self.footprints.items()}
        self.pads = {p['uuid']: p for p in geometry['pads']}
        self.pads_of = {}
        for pad in geometry['pads']:
            self.pads_of.setdefault(pad['ref'], []).append(pad)
        self.shapes = {}
        self.coverage = self._coverage()
        self.outline = ([tuple(p) for p in geometry['outline']['polygons'][0]]
                        if geometry['outline'].get('status') == 'proven' else None)
        values = [self.rules['min_clearance_nm']] + [v for layers in geometry['track_clearance_nm'].values()
                                                     for v in layers.values()]
        self.max_clearance = max(values + [v for p in geometry['pads'] for v in p['own_clearance_nm'].values()])
        self.items, self.cells, self.by_ref, self.history = {}, {}, {}, []
        for item in self._items():
            self._insert(item)

    # Global model limits make every terminal unknown; they are reported, never guessed around.
    def _coverage(self):
        reasons = []
        custom = self.rules.get('custom_rules', {}).get('status')
        if custom not in ('absent', 'empty'):
            reasons.append('native custom rules present; pairwise evaluation not modelled')
        if self.geometry['outline'].get('status') != 'proven':
            reasons.append('board outline not proven')
        for area in self.geometry['rule_areas']:
            if area['outline'].get('status') != 'proven' and (area['forbid']['tracks'] or area['forbid']['vias']):
                reasons.append(f"rule area {area['uuid']} outline unsupported")
        for track in self.geometry['tracks']:
            if track['kind'] != 'segment':
                reasons.append(f"existing {track.get('native_class', 'track')} {track['uuid']} not modelled")
        for via in self.geometry['vias']:
            if via['layers'] != self.geometry['copper_layers']:
                reasons.append(f"existing blind or buried via {via['uuid']} not modelled")
        return sorted(set(reasons))

    def _items(self):
        geometry, items = self.geometry, []
        for pad in geometry['pads']:
            for layer, bounds in sorted(pad['copper'].items()):
                for i, polygon in enumerate(bounds['outer']):
                    items.append({'id': f"pad:{pad['uuid']}:{layer}:{i}", 'kind': 'pad', 'source': pad['uuid'],
                                  'ref': pad['ref'], 'net': pad['net'], 'layer': layer,
                                  'clearance_nm': pad['own_clearance_nm'][layer],
                                  'shape': ('poly', [tuple(p) for p in polygon])})
            if pad['hole']:
                for i, polygon in enumerate(pad['hole']['outer']):
                    items.append({'id': f"hole:{pad['uuid']}:{i}", 'kind': 'hole', 'source': pad['uuid'],
                                  'ref': pad['ref'], 'net': pad['net'], 'layer': '*', 'clearance_nm': 0,
                                  'shape': ('poly', [tuple(p) for p in polygon])})
        for track in geometry['tracks']:
            if track['kind'] == 'segment':
                items.append({'id': f"track:{track['uuid']}", 'kind': 'track', 'source': track['uuid'], 'ref': None,
                              'net': track['net'], 'layer': track['layer'],
                              'clearance_nm': geometry['track_clearance_nm'][track['net']][track['layer']],
                              'shape': ('capsule', tuple(track['start_nm']), tuple(track['end_nm']),
                                        track['width_nm'] / 2)})
        for via in geometry['vias']:
            centre = tuple(via['position_nm'])
            for layer in via['layers']:
                items.append({'id': f"via:{via['uuid']}:{layer}", 'kind': 'via', 'source': via['uuid'], 'ref': None,
                              'net': via['net'], 'layer': layer,
                              'clearance_nm': geometry['track_clearance_nm'][via['net']][layer],
                              'shape': ('capsule', centre, centre, via['diameter_nm'] / 2)})
            items.append({'id': f"hole:{via['uuid']}", 'kind': 'hole', 'source': via['uuid'], 'ref': None,
                          'net': via['net'], 'layer': '*', 'clearance_nm': 0,
                          'shape': ('capsule', centre, centre, via['drill_nm'] / 2)})
        if geometry['outline'].get('status') == 'proven':
            polygon = [tuple(p) for p in geometry['outline']['polygons'][0]]
            for i, (a, b) in enumerate(_edges(polygon)):
                items.append({'id': f'edge:{i}', 'kind': 'edge', 'source': 'outline', 'ref': None, 'net': None,
                              'layer': '*', 'clearance_nm': 0, 'shape': ('capsule', a, b, 0)})
        for area in geometry['rule_areas']:
            if area['outline'].get('status') != 'proven':
                continue
            forbid = sorted(k for k in ('tracks', 'vias') if area['forbid'][k])
            if not forbid:
                continue
            ref = self.owner.get(area['owner'])
            for layer in area['layers']:
                for i, polygon in enumerate(area['outline']['polygons']):
                    items.append({'id': f"keepout:{area['uuid']}:{layer}:{i}", 'kind': 'keepout',
                                  'source': area['uuid'], 'ref': ref, 'net': None, 'layer': layer,
                                  'forbid': forbid, 'clearance_nm': 0,
                                  'shape': ('poly', [tuple(p) for p in polygon])})
        return items

    def _cells_of(self, item):
        shape = item['shape']
        points = shape[1] if shape[0] == 'poly' else (shape[1], shape[2])
        grow = 0 if shape[0] == 'poly' else shape[3]
        x0, y0, x1, y1 = _box(points, grow)
        return [(item['layer'], ix, iy) for ix in range(int(x0 // CELL_NM), int(x1 // CELL_NM) + 1)
                for iy in range(int(y0 // CELL_NM), int(y1 // CELL_NM) + 1)]

    def _insert(self, item):
        self.items[item['id']] = item
        for cell in self._cells_of(item):
            self.cells.setdefault(cell, set()).add(item['id'])
        if item['ref'] is not None:
            self.by_ref.setdefault(item['ref'], []).append(item['id'])

    def _remove(self, item_id):
        item = self.items.pop(item_id)
        for cell in self._cells_of(item):
            self.cells[cell].discard(item_id)
            if not self.cells[cell]:
                del self.cells[cell]
        if item['ref'] is not None:
            self.by_ref[item['ref']].remove(item_id)
        return item

    def near(self, box, layers):
        x0, y0, x1, y1 = box
        found = set()
        for layer in layers:
            for ix in range(int(x0 // CELL_NM), int(x1 // CELL_NM) + 1):
                for iy in range(int(y0 // CELL_NM), int(y1 // CELL_NM) + 1):
                    found |= self.cells.get((layer, ix, iy), set())
        return [self.items[i] for i in sorted(found)]

    # ------------------------------------------------------------ poses

    def mover(self, ref, pose):
        old = self.pose[ref]
        turns = _turns(pose['rotation_deg'] - old['rotation_deg'])
        origin, target = old['position_nm'], pose['position_nm']
        if not isinstance(target, list) or len(target) != 2 or any(type(v) is not int for v in target):
            raise ValueError('a local access pose needs an integer position_nm')

        def move(point):
            x, y = rotate([point[0] - origin[0], point[1] - origin[1]], turns)
            return (x + target[0], y + target[1])
        return move

    def push(self, ref, pose):
        """Move ``ref`` to ``pose`` (quarter-turn change); undo with ``pop``."""
        if ref not in self.footprints:
            raise ValueError(f'unknown footprint {ref}')
        move = self.mover(ref, pose)
        before = [self._remove(i) for i in list(self.by_ref.get(ref, []))]
        for item in before:
            shape = item['shape']
            moved = (('poly', [move(p) for p in shape[1]]) if shape[0] == 'poly'
                     else ('capsule', move(shape[1]), move(shape[2]), shape[3]))
            self._insert({**item, 'shape': moved})
        self.history.append((ref, self.pose[ref], before))
        self.shapes = {}
        self.pose[ref] = {'position_nm': list(pose['position_nm']), 'rotation_deg': pose['rotation_deg']}

    def pop(self):
        ref, pose, before = self.history.pop()
        for item_id in list(self.by_ref.get(ref, [])):
            self._remove(item_id)
        for item in before:
            self._insert(item)
        self.pose[ref] = pose
        self.shapes = {}

    def state(self):
        return {'pose': self.pose, 'history': len(self.history),
                'items': {i: self.items[i] for i in sorted(self.items)},
                'cells': {f'{k[0]}|{k[1]}|{k[2]}': sorted(v) for k, v in sorted(self.cells.items())}}

    def pad_shape(self, uuid, layer, bound):
        """Current (posed) outer or inner copper polygons of a pad on a layer."""
        key = (uuid, layer, bound)
        if key not in self.shapes:
            pad = self.pads[uuid]
            move = self._from_capture(pad['ref'])
            self.shapes[key] = [[move(p) for p in polygon] for polygon in pad['copper'][layer][bound]]
        return self.shapes[key]

    def anchor(self, uuid):
        pad = self.pads[uuid]
        return self._from_capture(pad['ref'])(pad['position_nm'])

    def _from_capture(self, ref):
        fp, pose = self.footprints[ref], self.pose[ref]
        turns = _turns(pose['rotation_deg'] - fp['rotation_deg'])
        origin, target = fp['position_nm'], pose['position_nm']

        def move(point):
            x, y = rotate([point[0] - origin[0], point[1] - origin[1]], turns)
            return (x + target[0], y + target[1])
        return move

    # ---------------------------------------------------------- checks

    def required(self, net, layer, item):
        own = self.geometry['track_clearance_nm'].get(net, {}).get(layer)
        if own is None:
            raise ValueError(f'no captured native clearance for net {net!r} on {layer}')
        return max(self.rules['min_clearance_nm'], own, item['clearance_nm'])

    def check_track(self, a, b, layer, net, half, connected):
        """(margin, blocker) of a track segment; margin < 0 names the first violated obstacle."""
        rules = self.rules
        reach = half + max(rules['hole_clearance_nm'], rules['copper_edge_clearance_nm'],
                           self.max_clearance) + self.geometry['max_error_nm']
        box = _box((a, b), reach)
        best, limiting = math.inf, None
        for item in self.near(box, (layer, '*')):
            kind = item['kind']
            if kind == 'pad' and item['net'] == net:
                continue
            if kind in ('track', 'via') and item['net'] == net:
                continue
            if kind == 'hole' and item['source'] in connected:
                continue
            if kind == 'keepout':
                if 'tracks' not in item['forbid']:
                    continue
                need = half + 1
            elif kind == 'hole':
                need = half + rules['hole_clearance_nm']
            elif kind == 'edge':
                need = half + rules['copper_edge_clearance_nm']
            else:
                need = half + self.required(net, layer, item)
            margin = shape_distance(a, b, item['shape']) - need
            if margin < best:
                best, limiting = margin, item['id']
            if margin < 0:
                return margin, limiting
        if self.outline is None or not (inside(a, self.outline) and inside(b, self.outline)):
            return -1.0, 'outline'
        return best, limiting

    def check_via(self, centre, via, net, connected):
        rules, radius, drill = self.rules, via['diameter_nm'] / 2, via['drill_nm'] / 2
        layers = self.geometry['copper_layers']
        reach = radius + max(rules['hole_clearance_nm'], rules['hole_to_hole_nm'], rules['copper_edge_clearance_nm'],
                             self.max_clearance) + self.geometry['max_error_nm']
        box = _box((centre,), reach)
        best, limiting = math.inf, None
        for item in self.near(box, tuple(layers) + ('*',)):
            kind = item['kind']
            if kind in ('pad', 'track', 'via') and item['net'] == net:
                continue
            if kind == 'keepout':
                if 'vias' not in item['forbid']:
                    continue
                need = radius + 1
            elif kind == 'hole':
                # Drill to drill everywhere; via copper to a foreign drill by the hole clearance.
                need = drill + rules['hole_to_hole_nm']
                if item['source'] not in connected:
                    need = max(need, radius + rules['hole_clearance_nm'])
            elif kind == 'edge':
                need = radius + rules['copper_edge_clearance_nm']
            else:
                need = radius + self.required(net, item['layer'], item)
            margin = shape_distance(centre, centre, item['shape']) - need
            if margin < best:
                best, limiting = margin, item['id']
            if margin < 0:
                return margin, limiting
        if self.outline is None or not inside(centre, self.outline):
            return -1.0, 'outline'
        return best, limiting

    # --------------------------------------------------------- analysis

    def analyze(self, refs=None):
        """The ``agent-ee.placement-access/1`` report for every terminal (or those with a pad on ``refs``)."""
        terminals = []
        components = self.components()
        for component in components:
            if refs is not None and not {p['ref'] for p in component['pads']} & set(refs):
                continue
            terminals.append(self.terminal(component, components))
        summary = Counter(t['status'] for t in terminals)
        return {'schema': SCHEMA, 'search': SEARCH, 'board_sha256': self.geometry['board_sha256'],
                'geometry_sha256': value_digest(self.geometry), 'policy': self.policy, 'limits': self.limits,
                'poses': {ref: pose for ref, pose in sorted(self.pose.items())
                          if pose != {'position_nm': self.footprints[ref]['position_nm'],
                                      'rotation_deg': self.footprints[ref]['rotation_deg']}},
                'coverage': {'status': 'unknown' if self.coverage else 'modelled', 'reasons': self.coverage,
                             'model': ['pad copper (native outer bound)', 'drills and slots', 'tracks', 'vias',
                                       'board outline', 'track/via rule areas', 'native own clearances'],
                             'not_modelled': ['native custom rules', 'copper zones (refilled by native authority)',
                                              'simultaneous routability']},
                'summary': {k: summary.get(k, 0) for k in ('witness', 'unknown', 'not_required')},
                'terminals': terminals}

    def components(self):
        return terminal_components(self.geometry, self.pad_shape)

    def terminal(self, component, components):
        net = component['net']
        peers = [c for c in components if c['net'] == net and c is not component]
        record = {'id': component['id'], 'net': net, 'pads': component['pads']}
        if 'unresolved_contact' in component:
            record['unresolved_contact'] = component['unresolved_contact']
        # Other same-net copper (tracks, vias, zones) keeps the obligation; only a sole pad component is exempt.
        connected = any(item['net'] == net for item in
                        self.geometry['tracks'] + self.geometry['vias'] + self.geometry['zones'])
        if not net or net.startswith('unconnected-') or (not peers and not connected):
            return {**record, 'status': 'not_required', 'reason': 'no other terminal on the net'}
        if self.coverage:
            return {**record, 'status': 'unknown', 'reason': 'coverage', 'details': self.coverage}
        unsupported = [f"{self.pads[p['uuid']]['ref']}.{self.pads[p['uuid']]['number']}: "
                       + '; '.join(self.pads[p['uuid']]['reasons'])
                       for p in component['pads'] if self.pads[p['uuid']]['status'] != 'supported']
        if unsupported:
            return {**record, 'status': 'unknown', 'reason': 'unsupported_geometry', 'details': unsupported}
        source = self._source(component)
        if source is None:
            return {**record, 'status': 'unknown', 'reason': 'no pad anchor inside its copper on a routing layer'}
        uuid, layers = source
        zoned = self._zoned_layers(net)
        layers = [l for l in layers if l not in zoned]
        if not layers:
            return {**record, 'status': 'unknown', 'reason': 'different-net copper zone on every start layer',
                    'details': sorted(zoned)}
        search = _Search(self, component, uuid, layers, peers, zoned)
        result = search.run()
        result['source_pad'] = uuid
        if zoned:
            result['zone_layers_unknown'] = sorted(zoned)
        return {**record, **result}

    def _source(self, component):
        for pad in component['pads']:
            layers = [l for l in self.policy['routing_layers'] if l in self.pads[pad['uuid']]['copper']]
            anchor = self.anchor(pad['uuid'])
            layers = [l for l in layers
                      if any(inside(anchor, poly) for poly in self.pad_shape(pad['uuid'], l, 'inner'))]
            if layers:
                return pad['uuid'], layers
        return None

    def _zoned_layers(self, net):
        return {layer for zone in self.geometry['zones'] if zone['net'] != net for layer in zone['layers']
                if layer in self.geometry['copper_layers']}


class _Search:
    """Finite deterministic candidate set for one terminal component."""

    def __init__(self, index, component, uuid, layers, peers, zoned):
        self.index, self.component, self.uuid, self.layers = index, component, uuid, layers
        self.peers, self.zoned = peers, zoned
        self.net = component['net']
        self.limits, self.policy = index.limits, index.policy
        self.half = self.policy['track_width_nm'] / 2
        self.connected = {p['uuid'] for p in component['pads']}
        self.anchor = index.anchor(uuid)
        self.witnesses, self.blockers, self.attempted = [], Counter(), 0
        ref = index.pads[uuid]['ref']
        own = [q for pad in index.pads_of[ref] for layer in pad['copper']
               for poly in index.pad_shape(pad['uuid'], layer, 'outer') for q in poly]
        clear = max(index.rules['min_clearance_nm'],
                    *(index.geometry['track_clearance_nm'][self.net].values()))
        self.clear = clear
        shell = self.limits['shell_nm']
        if shell is None:
            shell = clear + self.policy['track_width_nm']
        self.shell = _box(own, shell)
        self.own_box = _box(own)
        self.pad_points = [q for layer in index.pads[uuid]['copper'] for poly in index.pad_shape(uuid, layer, 'outer')
                           for q in poly]

    def run(self):
        for candidate in self.candidates():
            if len(self.witnesses) >= self.limits['max_witnesses']:
                return self.result(truncated=True)
            if self.attempted >= self.limits['max_candidates']:
                return self.result(budget=True)
            self.attempted += 1
            self.test(candidate)
        return self.result()

    def result(self, truncated=False, budget=False):
        ranked = sorted(self.blockers.items(), key=lambda kv: (-kv[1], kv[0]))
        blockers = [{'obstacle': k, 'candidates': n} for k, n in ranked][:8]
        out = {'attempted': self.attempted, 'truncated': truncated}
        if self.witnesses:
            return {**out, 'status': 'witness', 'witnesses': self.witnesses, 'blockers': blockers}
        return {**out, 'status': 'unknown', 'witnesses': [],
                'reason': 'search_budget_exhausted' if budget else 'no_witness_in_finite_search',
                'blockers': blockers}

    def test(self, candidate):
        kind, layer, path = candidate['kind'], candidate['layer'], candidate['path']
        margins = []
        connected = self.connected | set(candidate.get('target_pads', ()))
        for a, b in zip(path, path[1:]):
            margin, limiting = self.index.check_track(a, b, layer, self.net, self.half, connected)
            if margin < 0:
                self.blockers[limiting] += 1
                return
            margins.append((margin, limiting))
        if 'via' in candidate:
            margin, limiting = self.index.check_via(path[-1], candidate['via'], self.net, connected)
            if margin < 0:
                self.blockers[limiting] += 1
                return
            margins.append((margin, limiting))
        margin, limiting = min(margins, key=lambda m: m[0])
        witness = {'kind': kind, 'source_pad': self.uuid, 'net': self.net, 'layer': layer,
                   'track_width_nm': self.policy['track_width_nm'], 'path_nm': [list(p) for p in path],
                   'min_margin_nm': math.floor(margin) if math.isfinite(margin) else None, 'limiting': limiting}
        if kind == 'direct':
            witness['target'] = candidate['target']
        if 'via' in candidate:
            witness['via'] = {**candidate['via'], 'position_nm': list(path[-1]),
                              'layers': self.index.geometry['copper_layers'],
                              'reaches': [l for l in self.policy['routing_layers'] if l != layer]}
        if 'direction' in candidate:
            witness['direction'] = candidate['direction']
        if witness not in self.witnesses:
            self.witnesses.append(witness)

    def _snap(self, value):
        grid = self.limits['grid_nm']
        return math.ceil(value / grid) * grid

    def _exit(self, origin, unit):
        """Smallest t with origin + t*unit strictly outside the local shell box."""
        x0, y0, x1, y1 = self.shell
        ts = []
        for axis, (lo, hi) in ((0, (x0, x1)), (1, (y0, y1))):
            if unit[axis] > 0:
                ts.append((hi - origin[axis]) / unit[axis])
            elif unit[axis] < 0:
                ts.append((lo - origin[axis]) / unit[axis])
        return self._snap(max(0, min(ts)) + 1)

    @staticmethod
    def _point(origin, unit, t):
        return (int(round(origin[0] + unit[0] * t)), int(round(origin[1] + unit[1] * t)))

    def _extent(self, points, unit):
        base = self.anchor[0] * unit[0] + self.anchor[1] * unit[1]
        return max(p[0] * unit[0] + p[1] * unit[1] for p in points) - base

    def candidates(self):
        index = self.index
        # 1. Direct links to nearby same-net components, nearest first.
        peers = []
        for peer in self.peers:
            for pad in peer['pads']:
                target = index.anchor(pad['uuid'])
                distance = math.hypot(target[0] - self.anchor[0], target[1] - self.anchor[1])
                shared = [l for l in self.layers if l in index.pads[pad['uuid']]['copper']
                          and any(inside(target, poly) for poly in index.pad_shape(pad['uuid'], l, 'inner'))]
                if distance <= self.limits['direct_max_nm'] and shared:
                    peers.append((distance, peer['id'], pad['uuid'], target, shared, peer))
                    break
        for distance, peer_id, pad_uuid, target, shared, peer in sorted(peers, key=lambda p: (p[0], p[1])):
            targets = [p['uuid'] for p in peer['pads']]
            bends = [(target[0], self.anchor[1]), (self.anchor[0], target[1])]
            for layer in shared:
                yield {'kind': 'direct', 'layer': layer, 'path': [self.anchor, target], 'target': peer_id,
                       'target_pads': targets}
                for bend in bends:
                    if bend != self.anchor and bend != target:
                        yield {'kind': 'direct', 'layer': layer, 'path': [self.anchor, bend, target],
                               'target': peer_id, 'target_pads': targets}
        # 2. Straight then one-bend exits to the local shell.
        for layer in self.layers:
            for name, direction in DIRECTIONS:
                unit = (direction[0] / math.hypot(*direction), direction[1] / math.hypot(*direction))
                end = self._point(self.anchor, unit, self._exit(self.anchor, unit))
                yield {'kind': 'exit', 'layer': layer, 'path': [self.anchor, end], 'direction': name}
            for name, direction in DIRECTIONS[:4]:
                for leg in self._first_legs(layer, direction):
                    corner = self._point(self.anchor, direction, leg)
                    for side_name, side in self._sides(direction):
                        for turn_name, turn in ((side_name, side), (f'{name}-{side_name}', (
                                (direction[0] + side[0]) / math.sqrt(2), (direction[1] + side[1]) / math.sqrt(2)))):
                            end = self._point(corner, turn, self._exit(corner, turn))
                            yield {'kind': 'exit', 'layer': layer, 'path': [self.anchor, corner, end],
                                   'direction': f'{name}+{turn_name}'}
        # 3. Short stubs to approved through vias that reach another routing layer.
        if len(self.policy['routing_layers']) > 1 and not (self.zoned & set(index.geometry['copper_layers'])):
            for via in self.policy['vias']:
                for layer in self.layers:
                    for name, direction in DIRECTIONS:
                        unit = (direction[0] / math.hypot(*direction), direction[1] / math.hypot(*direction))
                        t = self._snap(self._extent(self.pad_points, unit) + via['diameter_nm'] / 2 + self.clear)
                        yield {'kind': 'via', 'layer': layer, 'path': [self.anchor, self._point(self.anchor, unit, t)],
                               'via': via, 'direction': name}

    @staticmethod
    def _sides(direction):
        return (('left', (-1, 0)), ('right', (1, 0))) if direction[0] == 0 else (('up', (0, -1)), ('down', (0, 1)))

    def _first_legs(self, layer, direction):
        """First-leg lengths from the pad boundary, the footprint copper and the straight blocker, plus clearance."""
        clear = self.clear + self.half
        exit_t = self._exit(self.anchor, direction)
        legs = {self._snap(self._extent(self.pad_points, direction) + clear),
                self._snap(self._extent([(self.own_box[0], self.own_box[1]), (self.own_box[2], self.own_box[3])],
                                        direction) + clear)}
        # The longest clear straight stub before the first blocker (bisection on the grid).
        grid, lo, hi = self.limits['grid_nm'], 0, exit_t
        end = self._point(self.anchor, direction, hi)
        if self.index.check_track(self.anchor, end, layer, self.net, self.half, self.connected)[0] < 0:
            while hi - lo > grid:
                mid = self._snap((lo + hi) / 2 - grid + 1)
                mid = mid if lo < mid < hi else (lo + hi) // 2
                probe = self._point(self.anchor, direction, mid)
                if self.index.check_track(self.anchor, probe, layer, self.net, self.half, self.connected)[0] < 0:
                    hi = mid
                else:
                    lo = mid
            if lo > 0:
                legs.add(lo)
        return sorted(t for t in legs if 0 < t < exit_t)


def analyze_access(geometry, poses, routing_policy, limits=None, refs=None):
    """Full (non-incremental) local access report; ``poses`` maps ref to a quarter-turn pose change."""
    index = AccessIndex(geometry, routing_policy, limits)
    for ref in sorted(poses or {}):
        index.push(ref, poses[ref])
    return index.analyze(refs)


# ------------------------------------------------------------------ binding

# pcbnew PAD_SHAPE values. A circle, rectangle or oval is fully specified by the placement capture
# (size, copper centroid offset, orientation), so its contour is bound shape-specifically. A rounded
# or chamfered rectangle lies inside its size box but its radius or chamfer is not captured; a
# trapezoid or custom land has no captured box. Tests pin the values against pcbnew.
SPECIFIED_SHAPES = frozenset((0, 1, 2))
BOXED_SHAPES = frozenset((0, 1, 2, 4))
CHAMFERED_SHAPE = 5
CUSTOM_SHAPE = 6
# Integer rounding of captured vertices and of the float frame rotation, nm.
ROUNDING_NM = 2


def _land_frame(land):
    """Copper centre and pad orientation of a captured land: (centre, to_local, to_board)."""
    offset = [v[0] / v[1] for v in land['copper_centroid_offset_nm']]
    centre = (land['position_nm'][0] + offset[0], land['position_nm'][1] + offset[1])
    angle = math.radians(land['rotation_deg'])
    cos, sin = math.cos(angle), math.sin(angle)

    def to_local(q):
        x, y = q[0] - centre[0], q[1] - centre[1]
        return (x*cos - y*sin, x*sin + y*cos)

    def to_board(q):
        return (centre[0] + q[0]*cos + q[1]*sin, centre[1] - q[0]*sin + q[1]*cos)
    return centre, to_local, to_board


def _land_shape(land):
    """The captured land in its local frame: ('rect', hw, hh) or ('capsule', a, b, radius)."""
    w, h = land['size_nm']
    if land['shape'] == 1:
        return ('rect', w / 2, h / 2)
    if land['shape'] == 0 or w == h:
        return ('capsule', (0.0, 0.0), (0.0, 0.0), min(w, h) / 2)
    core = (abs(w - h) / 2, 0.0) if w > h else (0.0, abs(w - h) / 2)
    return ('capsule', (-core[0], -core[1]), core, min(w, h) / 2)


def _within(point, shape, grow):
    """Point inside the shape grown by ``grow`` (a rectangle grows to its box)."""
    if shape[0] == 'rect':
        return abs(point[0]) <= shape[1] + grow and abs(point[1]) <= shape[2] + grow
    return _point_segment(point, shape[1], shape[2]) <= shape[3] + grow


def _encloses(polygon, shape, shrink):
    """The shape shrunk by ``shrink`` lies inside ``polygon``: its centre is inside and no edge enters it.

    Every supported land is convex and connected, so a contour boundary that
    does not meet the shrunk land, around a point of it, encloses it.
    """
    if not inside((0.0, 0.0), polygon):
        return False
    if shape[0] == 'rect':
        hw, hh = shape[1] - shrink, shape[2] - shrink
        if hw <= 0 or hh <= 0:
            return True
        core = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        return all(shape_distance(a, b, ('poly', core)) > 0 for a, b in _edges(polygon))
    radius = shape[3] - shrink
    return radius <= 0 or all(segment_distance(a, b, shape[1], shape[2]) >= radius for a, b in _edges(polygon))


def _land_problems(pad, land, error, layers):
    """Contour problems of a fully specified land against its captured size, offset and orientation.

    On each layer the native inner bound (ERROR_INSIDE) must lie inside the
    captured land and contain it shrunk by the max error, and the outer bound
    (ERROR_OUTSIDE) must contain the land and lie inside it grown by the max
    error, each within the integer rounding. This proves the contour, not only
    its extents or area: a notch, a cut corner or a squared circle refuses.
    """
    _, to_local, _ = _land_frame(land)
    shape, t = _land_shape(land), ROUNDING_NM
    half = (land['size_nm'][0] / 2, land['size_nm'][1] / 2)
    problems = []
    for layer in layers:
        bounds = pad['copper'][layer]
        if len(bounds['outer']) != 1 or len(bounds['inner']) != 1:
            problems.append(f'{layer}: a boxed land has exactly one copper contour per bound')
            continue
        outer = [to_local(q) for q in bounds['outer'][0]]
        inner = [to_local(q) for q in bounds['inner'][0]]
        if len(outer) < 3 or len(inner) < 3:
            problems.append(f'{layer}: a copper contour needs at least three vertices')
            continue
        ox, oy = max(abs(q[0]) for q in outer), max(abs(q[1]) for q in outer)
        ix, iy = max(abs(q[0]) for q in inner), max(abs(q[1]) for q in inner)
        if not all(s - t <= o <= s + error + t for o, s in ((ox, half[0]), (oy, half[1]))):
            problems.append(f'{layer}: outer copper extent differs from the captured land size')
        if not all(s - error - t <= i <= s + t for i, s in ((ix, half[0]), (iy, half[1]))):
            problems.append(f'{layer}: inner copper extent differs from the captured land size')
        if not _encloses(outer, shape, t) or not all(_within(q, shape, error + t) for q in outer):
            problems.append(f'{layer}: outer copper contour differs from the captured land shape')
        if not all(_within(q, shape, t) for q in inner) or not _encloses(inner, shape, error + t):
            problems.append(f'{layer}: inner copper contour differs from the captured land shape')
    return problems


def _land_box(land, error):
    """A board-frame polygon that contains the land's copper, or None when the capture has no box for it.

    A rounded or chamfered rectangle lies inside its size box about the land
    (``ShapePos``) centre; when only the area centroid of a chamfered land is
    captured, the box is doubled about it. Grown by the max error and rounding.
    """
    if land['shape'] not in BOXED_SHAPES | {CHAMFERED_SHAPE}:
        return None
    w, h = land['size_nm']
    if 'copper_land_offset_nm' in land:
        offset = land['copper_land_offset_nm']
        land = {**land, 'copper_centroid_offset_nm': [[offset[0], 1], [offset[1], 1]]}
    elif 'copper_centroid_offset_nm' not in land:
        return None
    elif land['shape'] == CHAMFERED_SHAPE:
        w, h = 2 * w, 2 * h
    _, _, to_board = _land_frame(land)
    hw, hh = w / 2 + error + ROUNDING_NM, h / 2 + error + ROUNDING_NM
    return [[int(round(x)), int(round(y))] for x, y in
            (to_board(q) for q in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)))]


def _drill(land):
    """The captured drill as a land of its own (an oval slot or a circle about the pad position)."""
    return {'shape': 2, 'size_nm': land['drill_size_nm'], 'position_nm': land['position_nm'],
            'rotation_deg': land['rotation_deg'], 'copper_centroid_offset_nm': [[0, 1], [0, 1]]}


def _hole_problems(pad, land, error):
    hole = pad['hole']
    drilled = land['attribute'] in ('PTH', 'NPTH')
    if (hole is None) != (not drilled):
        return ['drill presence differs from the placement capture']
    if hole is None:
        return []
    drill = land['drill_size_nm']
    if hole['drill_nm'] != drill or hole['plated'] != (land['attribute'] == 'PTH'):
        return ['drill size or plating differs from the placement capture']
    if not hole['outer']:
        return []  # no native contour: obstructs as its conservative drill box (_bound_geometry)
    if len(hole['outer']) != 1:
        return ['a drill has exactly one contour']
    _, to_local, _ = _land_frame(_drill(land))
    contour, shape, t = [to_local(q) for q in hole['outer'][0]], _land_shape(_drill(land)), ROUNDING_NM
    x, y = max(abs(q[0]) for q in contour), max(abs(q[1]) for q in contour)
    if not all(d / 2 - t <= e <= d / 2 + error + t for e, d in ((x, drill[0]), (y, drill[1]))):
        return ['drill contour differs from the captured drill size']
    if not _encloses(contour, shape, t) or not all(_within(q, shape, error + t) for q in contour):
        return ['drill contour differs from the captured drill shape']
    return []


def _copper_layers(land, layers):
    """Copper layers the capture gives a land: its copper layers; an NPTH only when the land exceeds its drill."""
    copper = sorted(l for l in land['layers'] if l in layers)
    if land['attribute'] == 'NPTH' and not any(s > d for s, d in zip(land['size_nm'], land['drill_size_nm'])):
        return []
    return copper


def bind_capture(geometry, capture, *, native=False):
    """``{pad uuid: reason}`` of copper the placement capture cannot bind; raises on contradicting evidence."""
    return _bind(geometry, capture, native)[0]


def _bind(geometry, capture, native):
    try:
        return _bind_capture(geometry, capture, native)
    except (KeyError, TypeError, IndexError, AttributeError, ZeroDivisionError) as exc:
        raise ValueError(f'malformed routing geometry or placement capture: {type(exc).__name__}: {exc}') from None


def _bind_capture(geometry, capture, native):
    """Bind a routing geometry to the ``agent-ee.placement-probe/1`` capture of the same saved board.

    Raises ``ValueError`` when footprints, pads, their UUIDs, numbers, nets,
    poses, shapes, attributes, drills, the max error or the contours of fully
    specified lands (circle, rectangle, oval: ``_land_problems``) differ from
    the capture (missing, stale or changed evidence). Returns ``(unbound,
    geometry, coverage)``. ``unbound`` maps a pad UUID to the reason the capture cannot
    bind its copper: a land whose contour the capture does not specify
    (rounded, chamfered, trapezoid, custom), or a copper-layer set that differs
    from the captured land's. The returned geometry is the one to analyse: an
    unbound pad is unsupported (its terminal stays unknown), proves no contact
    (no inner copper), and obstructs on every layer with copper the capture
    cannot bind as its conservative size box; copper that has no such box is
    listed in ``coverage`` and makes every terminal unknown. A missing native
    drill contour obstructs as its drill box.

    With ``native`` the geometry and capture are the native captures of the
    hashed board (``block_access`` with ``board``): every native contour is
    authoritative, and only identity, pose, drill and custom-land support are
    checked here.
    """
    validate_geometry(geometry)
    if not isinstance(capture, dict) or capture.get('schema') != 'agent-ee.placement-probe/1':
        raise ValueError('block access needs the agent-ee.placement-probe/1 capture of the same board')
    if capture.get('board_sha256') != geometry['board_sha256']:
        raise ValueError('placement capture and routing geometry are of different boards')
    error = geometry['max_error_nm']
    if capture.get('max_error_nm') != error:
        raise ValueError('routing geometry max error differs from the placement capture')
    footprints = {(fp['ref'], fp['uuid']): fp for fp in capture['footprints']}
    pose = ('side', 'position_nm', 'rotation_deg')
    if (sorted(footprints) != sorted((fp['ref'], fp['uuid']) for fp in geometry['footprints'])
            or any({k: footprints[(fp['ref'], fp['uuid'])][k] for k in pose} != {k: fp[k] for k in pose}
                   for fp in geometry['footprints'])):
        raise ValueError('routing geometry footprints differ from the placement capture')
    lands = {p['uuid']: (fp, p) for fp in capture['footprints'] for p in fp['pads']}
    if (len(lands) != sum(len(fp['pads']) for fp in capture['footprints'])
            or set(lands) != {p['uuid'] for p in geometry['pads']}):
        raise ValueError('routing geometry pads differ from the placement capture pads')
    unbound, pads, coverage = {}, [], []
    for pad in geometry['pads']:
        fp, land = lands[pad['uuid']]
        label = f"{pad['ref']}.{pad['number']} ({pad['uuid']})"
        if pad['ref'] != fp['ref'] or pad['footprint_uuid'] != fp['uuid'] or any(
                pad[k] != land[k] for k in ('number', 'net', 'position_nm', 'rotation_deg', 'shape', 'attribute')):
            raise ValueError(f'{label}: pad identity, net or pose differs from the placement capture')
        if pad['shape'] == CUSTOM_SHAPE and pad['status'] == 'supported':
            raise ValueError(f'{label}: a custom land is never supported for access')
        problems = _hole_problems(pad, land, error)
        if not problems and pad['hole'] and not pad['hole']['outer']:
            pad = {**pad, 'hole': {**pad['hole'], 'outer': [_land_box({**_drill(land), 'shape': 1}, error)]}}
        if native:
            if problems:
                raise ValueError(f'{label}: ' + '; '.join(problems))
            pads.append(pad)
            continue
        expected, given = _copper_layers(land, geometry['copper_layers']), sorted(pad['copper'])
        verified = []
        if pad['shape'] in SPECIFIED_SHAPES and 'copper_centroid_offset_nm' in land:
            verified = sorted(set(expected) & set(given))
            problems += _land_problems(pad, land, error, verified)
        if problems:
            raise ValueError(f'{label}: ' + '; '.join(problems))
        if expected != given:
            unbound[pad['uuid']] = 'copper layers differ from the placement capture'
        elif given and verified != given:
            unbound[pad['uuid']] = 'land contour not bound by the placement capture'
        if pad['uuid'] not in unbound:
            pads.append(pad)
            continue
        box, copper, clearance = _land_box(land, error), {}, dict(pad['own_clearance_nm'])
        for layer in sorted(set(expected) | set(given)):
            if layer in verified:
                outer = pad['copper'][layer]['outer']
            elif box is not None:
                outer = [box]
            else:
                coverage.append(f'{label}: copper contour not bound by the placement capture')
                outer = pad['copper'].get(layer, {}).get('outer', [])
            copper[layer] = {'outer': outer, 'inner': []}
            clearance.setdefault(layer, max(clearance.values(), default=geometry['rules']['min_clearance_nm']))
        pads.append({**pad, 'copper': copper, 'own_clearance_nm': clearance, 'status': 'unsupported',
                     'reasons': sorted(set(pad.get('reasons', [])) | {unbound[pad['uuid']]})})
    return unbound, {**geometry, 'pads': pads}, sorted(set(coverage))


def _member_evidence(candidate, geometry, capture):
    """Each member's land pattern, courtyard, footprint and pin nets checked against the candidate."""
    from . import blocks
    board = {fp['ref']: fp for fp in capture['footprints']}
    evidence = []
    for row in candidate['members']:
        ref = row['reference']
        fp = board.get(ref)
        if fp is None:
            raise ValueError(f'{ref}: candidate member absent from the placement capture')
        if fp.get('fpid') != row['fpid'] or fp['side'] != row['side']:
            raise ValueError(f'{ref}: board footprint or side differs from the candidate member')
        for key, compute in (('pad_geometry_sha256', blocks.pad_geometry_sha256),
                             ('courtyard_sha256', blocks.courtyard_sha256)):
            if compute(fp) != row[key]:
                raise ValueError(f'{ref}: board {key} differs from the candidate member')
        nets = blocks._pad_nets(fp)
        if any(not p['number'] and p['net'] for p in fp['pads']):
            raise ValueError(f'{ref}: an unnumbered pad carries a net the candidate does not bind')
        if nets != {pin['pad']: pin.get('net', '') for pin in row['pins']}:
            raise ValueError(f'{ref}: board pad nets differ from the candidate member pins')
        copper = {p['uuid']: bool(p['copper']) for p in geometry['pads'] if p['ref'] == ref}
        evidence.append({'reference': ref, 'footprint_uuid': fp['uuid'],
                         'pad_geometry_sha256': row['pad_geometry_sha256'], 'courtyard_sha256': row['courtyard_sha256'],
                         'pads': [{'uuid': p['uuid'], 'number': p['number'], 'net': p['net'], 'copper': copper[p['uuid']]}
                                  for p in sorted(fp['pads'], key=lambda p: (p['number'], p['uuid']))]})
    return evidence


# ------------------------------------------------------------------ sidecar

RULE_SOURCES = ('.kicad_pcb', '.kicad_pro', '.kicad_dru')


def _native_capture(board):
    """Both native captures of one stable snapshot of the board and its rule files, in a fresh interpreter.

    pcbnew caches a loaded project within one process, so a capture in this
    process can carry the rules of earlier project bytes. The board, project
    and custom rule bytes are read once, written to a private directory and
    captured there by a clean child; the returned hashes are of exactly the
    bytes that child loaded.
    """
    board = Path(board)
    snapshot = {suffix: board.with_suffix(suffix).read_bytes() if board.with_suffix(suffix).is_file() else None
                for suffix in RULE_SOURCES}
    if snapshot['.kicad_pcb'] is None:
        raise ValueError('native board is not a saved board file')
    with tempfile.TemporaryDirectory(prefix='agent-ee-access-native-') as tmp:
        copy = Path(tmp) / board.name
        for suffix, raw in snapshot.items():
            if raw is not None:
                copy.with_suffix(suffix).write_bytes(raw)
        captures = {}
        for name, flags in (('geometry', ['--routing-geometry']), ('capture', [])):
            out = Path(tmp) / f'{name}.json'
            argv = [sys.executable, '-B', '-m', 'agent_ee.placement_probe', str(copy), '--out', str(out), *flags]
            # The child imports this same package source, whatever the caller's path.
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
                [str(Path(__file__).resolve().parents[1])] + [p for p in [os.environ.get('PYTHONPATH')] if p])}
            try:
                completed = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env, cwd=tmp)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f'native capture could not run: {exc}') from exc
            if completed.returncode != 0 or not out.is_file():
                raise ValueError(f'native {name} capture failed (exit {completed.returncode}): '
                                 + completed.stderr.strip()[-400:])
            captures[name] = strict_json(out.read_bytes())
        for suffix, raw in snapshot.items():
            if raw is not None and copy.with_suffix(suffix).read_bytes() != raw:
                raise ValueError('native rule sources changed during capture')
    hashes = {suffix: hashlib.sha256(raw).hexdigest() if raw is not None else None
              for suffix, raw in snapshot.items()}
    return captures['geometry'], captures['capture'], hashes


def _native_evidence(board, geometry, capture):
    """The routing geometry and placement capture must be the fresh native captures of one board snapshot.

    Returns the hashes of the snapshot's board, project and custom rule bytes:
    the rule sources of the supplied geometry, since it equals their capture.
    """
    fresh_geometry, fresh_capture, hashes = _native_capture(board)
    if hashes['.kicad_pcb'] != geometry['board_sha256']:
        raise ValueError('native board differs from the routing geometry board')
    if value_digest(fresh_geometry) != value_digest(geometry):
        raise ValueError('routing geometry differs from the native capture of the board')
    if value_digest(fresh_capture) != value_digest(capture):
        raise ValueError('placement capture differs from the native capture of the board')
    return hashes


# A placement capture states no rules; rules supplied beside it are editable and prove nothing.
RULES_UNBOUND = 'board rules, own clearances and custom-rule presence not bound to native rule authority'
RULE_AUTHORITY_KEYS = ('status', 'board_sha256', 'project_sha256', 'custom_rules_sha256')


def _rule_authority(hashes, geometry):
    """Hashes of the native rule sources the geometry was freshly captured from: board, project, custom rules."""
    if hashes['.kicad_dru'] != geometry['rules']['custom_rules'].get('sha256'):
        raise ValueError('routing geometry custom rules differ from the board custom rule file')
    return {'status': 'bound', 'board_sha256': hashes['.kicad_pcb'],
            'project_sha256': hashes['.kicad_pro'], 'custom_rules_sha256': hashes['.kicad_dru']}


def block_access(candidate, geometry, routing_policy, limits=None, *, capture, board=None):
    """Diagnostic ``agent-ee.block-access/1`` for a candidate/2 placed on a complete candidate board.

    The candidate is read, never changed: the sidecar binds its canonical
    digest. ``capture`` is the ``agent-ee.placement-probe/1`` capture of the
    same saved board; the routing geometry is bound to it (``bind_capture``)
    and every member's land pattern, courtyard, footprint and pin nets are
    recomputed from it and must equal the candidate's. With ``board`` (the
    saved board file), both captures must equal the native captures a fresh
    interpreter takes of one snapshot of it and its rule files (never this
    process's possibly cached project), so every native contour and rule is
    authoritative (``contour_authority``
    ``native board``); without it only contours the placement capture
    specifies are bound (``placement capture``). Rules, own clearances and
    custom-rule presence are bound only with ``board`` (``rule_authority``
    hashes that snapshot's board, project and custom rule bytes); without it they are
    unbound coverage and every terminal is unknown, never witnessed. Every
    member must sit on the board at the candidate's frame poses relative to
    its anchor. Witnesses that use space outside the block (edge, neighbours,
    vias) must be rechecked after arrangement.
    """
    from . import blocks
    blocks.validate_candidate(candidate)
    validate_geometry(geometry)
    if board is not None:
        rule_authority, rule_coverage = _rule_authority(_native_evidence(board, geometry, capture), geometry), []
    else:
        rule_authority, rule_coverage = {'status': 'unbound', 'reason': RULES_UNBOUND}, [RULES_UNBOUND]
    unbound, analysed, coverage = _bind(geometry, capture, board is not None)
    evidence = _member_evidence(candidate, analysed, capture)
    index = AccessIndex(analysed, routing_policy, limits)
    index.coverage = sorted(set(index.coverage) | set(coverage) | set(rule_coverage))
    members = [row['reference'] for row in candidate['members']]
    anchor = candidate['frame']['anchor']
    missing = [ref for ref in members if ref not in index.footprints]
    if missing:
        raise ValueError('candidate members absent from the board: ' + ', '.join(missing))
    origin = index.footprints[anchor]['position_nm']
    turns = _turns(index.footprints[anchor]['rotation_deg'])
    for row in candidate['members']:
        fp = index.footprints[row['reference']]
        position, rotation = blocks._frame_pose(fp, origin, turns)
        if (fp['side'] != row['side'] or position != row['pose']['position_nm']
                or rotation != row['pose']['rotation_deg'] % 360):
            raise ValueError(f"{row['reference']}: board pose differs from the candidate frame pose")
    report = index.analyze(members)
    member_set = set(members)
    # A direct witness into copper the capture cannot bind proves nothing about that target.
    unbound_ids = {c['id'] for c in index.components() if any(p['uuid'] in unbound for p in c['pads'])}
    roles = {}
    for terminal in report['terminals']:
        refs = {p['ref'] for p in terminal['pads']}
        net = terminal['net']
        outside = any(p['ref'] not in member_set for p in index.geometry['pads'] if p['net'] == net)
        if net and not net.startswith('unconnected-'):
            roles[net] = 'boundary' if outside else 'internal'
        terminal['members'] = sorted(refs & member_set)
        if terminal['status'] == 'witness':
            kept = [w for w in terminal['witnesses'] if w.get('target') not in unbound_ids]
            if not kept:
                terminal.update(status='unknown', witnesses=[], reason='witnesses target unbound copper')
            terminal['witnesses'] = kept
    summary = Counter(t['status'] for t in report['terminals'])
    return {'schema': SIDECAR, 'mode': 'diagnostic', 'block_id': candidate['block_id'],
            'candidate_sha256': value_digest(candidate), 'candidate_status': candidate['status'],
            'frame': candidate['frame'], 'board_sha256': report['board_sha256'],
            'geometry_sha256': value_digest(geometry), 'capture_sha256': value_digest(capture),
            'contour_authority': 'native board' if board is not None else 'placement capture',
            'rule_authority': rule_authority,
            'pad_geometry_sha256': {row['reference']: row['pad_geometry_sha256'] for row in candidate['members']},
            'courtyard_sha256': {row['reference']: row['courtyard_sha256'] for row in candidate['members']},
            'member_evidence': evidence,
            'unbound_pads': [{'uuid': uuid, 'reason': reason} for uuid, reason in sorted(unbound.items())],
            'net_roles': [{'net': net, 'role': roles[net]} for net in sorted(roles)],
            'policy': report['policy'], 'limits': report['limits'], 'search': SEARCH, 'coverage': report['coverage'],
            'recheck_after_arrangement': list(RECHECK),
            'summary': {k: summary.get(k, 0) for k in STATUSES}, 'terminals': report['terminals']}


RECHECK = ('board edge', 'neighbour copper', 'via sites', 'planes')
STATUSES = ('witness', 'unknown', 'not_required')
SIDECAR_KEYS = frozenset((
    'schema', 'mode', 'block_id', 'candidate_sha256', 'candidate_status', 'frame', 'board_sha256', 'geometry_sha256',
    'capture_sha256', 'contour_authority', 'rule_authority', 'pad_geometry_sha256', 'courtyard_sha256',
    'member_evidence', 'unbound_pads', 'net_roles', 'policy', 'limits', 'search', 'coverage', 'recheck_after_arrangement', 'summary', 'terminals'))


def _exact(value, keys, label, optional=()):
    if not isinstance(value, dict) or not set(keys) <= set(value) or set(value) - set(keys) - set(optional):
        raise ValueError(f'access sidecar {label} needs exactly ' + ', '.join(sorted(keys)))
    return value


def _text(value, label):
    if not isinstance(value, str):
        raise ValueError(f'access sidecar {label} must be text')
    return value


def _sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or set(value) - set('0123456789abcdef'):
        raise ValueError(f'access sidecar {label} must be a lowercase SHA-256')


def _sidecar_policy(policy):
    _exact(policy, ('track_width_nm', 'routing_layers', 'vias'), 'policy')
    _nm(policy['track_width_nm'], 'policy track_width_nm')
    layers = policy['routing_layers']
    if not isinstance(layers, list) or not layers or len(set(map(str, layers))) != len(layers) or \
            any(not isinstance(l, str) for l in layers):
        raise ValueError('access sidecar policy routing_layers must list distinct layers')
    if not isinstance(policy['vias'], list):
        raise ValueError('access sidecar policy vias must be a list')
    for via in policy['vias']:
        _exact(via, ('diameter_nm', 'drill_nm'), 'policy via')
        if _nm(via['diameter_nm'], 'policy via diameter_nm') <= _nm(via['drill_nm'], 'policy via drill_nm'):
            raise ValueError('access sidecar policy via diameter must exceed its drill')
    return policy


def validate_sidecar(candidate, sidecar, *, geometry, capture, board=None):
    """Strict ``agent-ee.block-access/1`` validation against its candidate and evidence; returns the summary.

    Structure, candidate binding, member land/pin evidence, policy, limits,
    coverage, terminal obligations and witness forms are checked first, and
    the summary is recounted from the terminal records. Those records are
    editable, so they cannot establish their own obligations: the sidecar is
    then re-derived from the routing geometry and placement capture it binds
    (and the saved board when its ``contour_authority`` is ``native board``)
    and must equal it exactly. Pad identity, copper participation, terminal
    partition, exemptions and witnesses therefore come from the bound
    evidence, never from the sidecar. Any omission or inconsistency raises
    ``ValueError``.
    """
    summary = _validate_structure(candidate, sidecar)
    if value_digest(geometry) != sidecar['geometry_sha256'] or value_digest(capture) != sidecar['capture_sha256']:
        raise ValueError('access sidecar is bound to another routing geometry or placement capture')
    native = sidecar['contour_authority'] == 'native board'
    if native and board is None:
        raise ValueError('access sidecar with native contour authority needs its saved board')
    derived = block_access(candidate, geometry, sidecar['policy'], sidecar['limits'], capture=capture,
                           board=board if native else None)
    if value_digest(derived) != value_digest(sidecar):
        raise ValueError('access sidecar differs from the evidence it binds')
    return summary


def _sidecar_rule_authority(sidecar):
    """Rules are bound only by a native board; a placement-capture sidecar states them as unknown coverage."""
    rules = sidecar['rule_authority']
    if sidecar['contour_authority'] == 'native board':
        _exact(rules, RULE_AUTHORITY_KEYS, 'rule authority')
        if rules['status'] != 'bound' or rules['board_sha256'] != sidecar['board_sha256']:
            raise ValueError('access sidecar native rule authority must be bound to its board')
        for key in RULE_AUTHORITY_KEYS[2:]:
            if rules[key] is not None:
                _sha(rules[key], f'rule authority {key}')
    elif sidecar['contour_authority'] == 'placement capture':
        reasons = sidecar['coverage'].get('reasons') if isinstance(sidecar['coverage'], dict) else None
        if rules != {'status': 'unbound', 'reason': RULES_UNBOUND} or \
                not isinstance(reasons, list) or RULES_UNBOUND not in reasons:
            raise ValueError('access sidecar without a native board must state its rules as unbound coverage')
    else:
        raise ValueError('access sidecar contour_authority must be native board or placement capture')
    return rules


def _validate_structure(candidate, sidecar):
    _exact(sidecar, SIDECAR_KEYS, 'record')
    if sidecar['schema'] != SIDECAR or sidecar['mode'] != 'diagnostic':
        raise ValueError(f'access sidecar must be a diagnostic {SIDECAR}')
    if sidecar['candidate_sha256'] != value_digest(candidate):
        raise ValueError('access sidecar is bound to another candidate')
    rows = candidate['members']
    if (sidecar['block_id'] != candidate['block_id'] or sidecar['candidate_status'] != candidate['status']
            or sidecar['frame'] != candidate['frame']):
        raise ValueError('access sidecar block, status or frame differs from its candidate')
    for key in ('board_sha256', 'geometry_sha256', 'capture_sha256'):
        _sha(sidecar[key], key)
    rules = _sidecar_rule_authority(sidecar)
    for key in ('pad_geometry_sha256', 'courtyard_sha256'):
        if sidecar[key] != {row['reference']: row[key] for row in rows}:
            raise ValueError(f'access sidecar {key} differs from its candidate members')
    evidence = sidecar['member_evidence']
    if not isinstance(evidence, list) or len(evidence) != len(rows):
        raise ValueError('access sidecar needs member evidence for every candidate member')
    copper, all_pads = {}, set()
    for row, record in zip(rows, evidence):
        _exact(record, ('reference', 'footprint_uuid', 'pad_geometry_sha256', 'courtyard_sha256', 'pads'),
               'member evidence')
        if any(record[k] != row[k] for k in ('pad_geometry_sha256', 'courtyard_sha256')) or \
                record['reference'] != row['reference']:
            raise ValueError(f"access sidecar member evidence differs from candidate member {row['reference']}")
        _text(record['footprint_uuid'], 'member footprint_uuid')
        if not isinstance(record['pads'], list):
            raise ValueError('access sidecar member pads must be a list')
        nets = {}
        for pad in record['pads']:
            _exact(pad, ('uuid', 'number', 'net', 'copper'), 'member pad')
            for key in ('uuid', 'number', 'net'):
                _text(pad[key], f'member pad {key}')
            if type(pad['copper']) is not bool or pad['uuid'] in all_pads:
                raise ValueError('access sidecar member pads need unique UUIDs and a copper flag')
            all_pads.add(pad['uuid'])
            if pad['number']:
                nets.setdefault(pad['number'], set()).add(pad['net'])
            elif pad['net']:
                raise ValueError('access sidecar member evidence has an unnumbered pad with a net')
            if pad['copper']:
                copper[pad['uuid']] = (row['reference'], pad['number'], pad['net'])
        if {n: s.pop() if len(s) == 1 else None for n, s in nets.items()} != \
                {pin['pad']: pin.get('net', '') for pin in row['pins']}:
            raise ValueError(f"access sidecar member pad nets differ from candidate pins of {row['reference']}")
    policy = _sidecar_policy(sidecar['policy'])
    if not isinstance(sidecar['limits'], dict) or set(sidecar['limits']) != set(LIMITS) or \
            search_limits(sidecar['limits']) != sidecar['limits']:
        raise ValueError('access sidecar limits must state every search limit')
    if sidecar['search'] != SEARCH or sidecar['recheck_after_arrangement'] != list(RECHECK):
        raise ValueError('access sidecar search or recheck list is not this model')
    coverage = _exact(sidecar['coverage'], ('status', 'reasons', 'model', 'not_modelled'), 'coverage')
    reasons = coverage['reasons']
    if (not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons)
            or coverage['status'] != ('unknown' if reasons else 'modelled')):
        raise ValueError('access sidecar coverage status must follow its reasons')
    unbound = sidecar['unbound_pads']
    if not isinstance(unbound, list):
        raise ValueError('access sidecar unbound_pads must be a list')
    unbound = {_text(_exact(u, ('uuid', 'reason'), 'unbound pad')['uuid'], 'unbound pad uuid') for u in unbound}
    roles = sidecar['net_roles']
    if not isinstance(roles, list) or any(_exact(r, ('net', 'role'), 'net role')['role'] not in ('internal', 'boundary')
                                          for r in roles):
        raise ValueError('access sidecar net roles must be internal or boundary')
    terminals, member_refs = sidecar['terminals'], {row['reference'] for row in rows}
    if not isinstance(terminals, list):
        raise ValueError('access sidecar terminals must be a list')
    ids, seen, counts = set(), {}, Counter()
    for terminal in terminals:
        _exact(terminal, ('id', 'net', 'pads', 'status', 'members'), 'terminal',
               optional=('witnesses', 'reason', 'details', 'attempted', 'truncated', 'blockers', 'source_pad',
                         'zone_layers_unknown', 'unresolved_contact'))
        _text(terminal['id'], 'terminal id')
        net, status, pads = _text(terminal['net'], 'terminal net'), terminal['status'], terminal['pads']
        if terminal['id'] in ids or status not in STATUSES or not isinstance(pads, list) or not pads:
            raise ValueError('access sidecar terminals need unique ids, a known status and pads')
        ids.add(terminal['id'])
        for pad in pads:
            _exact(pad, ('ref', 'number', 'uuid'), 'terminal pad')
            if pad['uuid'] in seen:
                raise ValueError('access sidecar pad appears in two terminals')
            seen[pad['uuid']] = (pad['ref'], pad['number'], net)
        if terminal['members'] != sorted({p['ref'] for p in pads} & member_refs) or not terminal['members']:
            raise ValueError('access sidecar terminal members differ from its pads')
        witnesses = terminal.get('witnesses', [])
        if not isinstance(witnesses, list):
            raise ValueError('access sidecar witnesses must be a list')
        if status == 'witness':
            if coverage['status'] != 'modelled' or rules['status'] != 'bound' or not witnesses or \
                    {p['uuid'] for p in pads} & unbound:
                raise ValueError('access sidecar witness terminal without modelled coverage, bound rules, witnesses '
                                 'or bound copper')
            for witness in witnesses:
                problem = _witness_form(witness)
                if problem is None and (witness['source_pad'] not in {p['uuid'] for p in pads}
                                        or witness['net'] != net or witness['layer'] not in policy['routing_layers']
                                        or witness['track_width_nm'] != policy['track_width_nm']):
                    problem = 'witness source, net, layer or width differs from its terminal and policy'
                if problem is None and witness['kind'] == 'via' and \
                        {k: witness['via'][k] for k in ('diameter_nm', 'drill_nm')} not in policy['vias']:
                    problem = 'witness via is not an approved policy via'
                if problem is None and witness['kind'] == 'direct' and witness['target'] == terminal['id']:
                    problem = 'direct witness targets its own terminal'
                if problem:
                    raise ValueError(f"access sidecar terminal {terminal['id']}: {problem}")
        elif witnesses or (status == 'unknown' and not isinstance(terminal.get('reason'), str)):
            raise ValueError('access sidecar unknown or not-required terminal carries witnesses or lacks a reason')
        counts[status] += 1
    members_seen = {u: v for u, v in seen.items() if v[0] in member_refs}
    if members_seen != copper:
        raise ValueError('access sidecar terminals do not cover exactly the member copper pads and nets')
    derived = {k: counts.get(k, 0) for k in STATUSES}
    if sidecar['summary'] != derived:
        raise ValueError('access sidecar summary is inconsistent with its terminal evidence')
    return derived


def access_status(candidate, sidecar=None, *, geometry=None, capture=None, board=None):
    """Access evidence for a candidate; a missing sidecar is unknown and admission is unchanged.

    The status is derived from the terminal evidence re-derived from the
    routing geometry, placement capture (and saved board) the sidecar binds
    (``validate_sidecar``), never from a supplied summary or terminal list. A
    sidecar without that evidence refuses.
    """
    if sidecar is None:
        return {'status': 'unknown', 'reason': 'no block-access sidecar'}
    if not isinstance(sidecar, dict) or sidecar.get('schema') != SIDECAR:
        raise ValueError(f'access sidecar must be {SIDECAR}')
    if sidecar.get('candidate_sha256') != value_digest(candidate):
        raise ValueError('access sidecar is bound to another candidate')
    if geometry is None or capture is None:
        raise ValueError('access status needs the routing geometry and placement capture the sidecar binds')
    summary = validate_sidecar(candidate, sidecar, geometry=geometry, capture=capture, board=board)
    if not summary['witness']:
        status = 'unknown'
    else:
        status = 'partial' if summary['unknown'] else 'witnessed'
    return {'status': status, 'summary': summary}


# ------------------------------------------------------------ native authority

# Keys a witness may carry, by kind: ``target`` only on direct, ``via`` only on via, ``direction`` never on direct.
WITNESS_KEYS = {'direct': ({'target'}, set()), 'exit': (set(), {'direction'}), 'via': ({'via'}, {'direction'})}
WITNESS_COMMON = ({'kind', 'source_pad', 'net', 'layer', 'track_width_nm', 'path_nm'}, {'min_margin_nm', 'limiting'})
VIA_KEYS = frozenset(('diameter_nm', 'drill_nm', 'position_nm', 'layers', 'reaches'))


def _point(value):
    return isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value)


def _witness_form(witness):
    """The first structural problem of a witness record (kind/key consistency, types), or None.

    Complete before anything is checked against a board or materialized: a
    record with a key of another kind, a missing key or a malformed via is
    refused, never written.
    """
    if not isinstance(witness, dict):
        return 'a witness must be an object'
    kind = witness.get('kind')
    # Type first: an unhashable kind (a list or object) must refuse, not raise from the lookup.
    if not isinstance(kind, str) or kind not in WITNESS_KEYS:
        return f'unknown witness kind {kind!r}'
    required = WITNESS_COMMON[0] | WITNESS_KEYS[kind][0]
    allowed = required | WITNESS_COMMON[1] | WITNESS_KEYS[kind][1]
    if required - set(witness) or set(witness) - allowed:
        return (f'{kind} witness needs exactly ' + ', '.join(sorted(required))
                + (', optionally ' + ', '.join(sorted(allowed - required)) if allowed - required else '')
                + '; got ' + ', '.join(sorted(map(str, witness))))
    for key in ('source_pad', 'net', 'layer') + (('target',) if kind == 'direct' else ()) + \
            (('direction',) if 'direction' in witness else ()):
        if not isinstance(witness[key], str):
            return f'witness {key} must be text'
    path = witness['path_nm']
    if not isinstance(path, list) or len(path) < 2 or not all(_point(q) for q in path):
        return 'path_nm must list at least two integer points'
    if type(witness['track_width_nm']) is not int or witness['track_width_nm'] <= 0:
        return 'track_width_nm must be a positive integer nm'
    if witness.get('min_margin_nm') is not None and type(witness['min_margin_nm']) is not int:
        return 'min_margin_nm must be an integer nm or null'
    if witness.get('limiting') is not None and not isinstance(witness['limiting'], str):
        return 'limiting must be text or null'
    if kind == 'via':
        via = witness['via']
        if not isinstance(via, dict) or set(via) != VIA_KEYS:
            return 'a via witness needs a via with exactly ' + ', '.join(sorted(VIA_KEYS))
        diameter, drill = via['diameter_nm'], via['drill_nm']
        if type(diameter) is not int or type(drill) is not int or diameter <= drill or drill <= 0:
            return 'via diameter_nm and drill_nm must be positive integers, diameter above drill'
        if not _point(via['position_nm']):
            return 'via position_nm must be an integer point'
        if any(not isinstance(via[k], list) or not all(isinstance(l, str) for l in via[k]) for k in ('layers', 'reaches')):
            return 'via layers and reaches must list layer names'
    return None


def _witness_problems(witness, geometry, components):
    """Source, target, kind and reach semantics of one witness against the native capture of the qualified board.

    Native DRC proves only copper legality and no extra opens; a shortened or
    retargeted witness is legal copper too. Each kind's claim is checked here
    on the raw capture before any copper is materialized:
    every witness starts inside the source pad's inner (ERROR_INSIDE) copper on
    its layer; ``direct`` ends inside the inner copper of a pad of its named
    same-net target component, which is not the source's; ``exit`` ends outside
    the owning footprint's copper box grown by clearance plus half the width;
    ``via`` ends at a through via of at least the rule minima that reaches
    another copper layer. Kind/key consistency and every type, including the
    whole via record, are checked first (``_witness_form``). Returns
    ``(problems, writable)``: a witness that is not writable (malformed, a key
    of another kind, or without source copper on its layer) is never
    materialized.
    """
    pads = {p['uuid']: p for p in geometry['pads']}
    rules, layers = geometry['rules'], geometry['copper_layers']

    def inner(uuid, layer, point):
        bounds = pads[uuid]['copper'].get(layer)
        return bool(bounds) and any(inside(point, [tuple(q) for q in poly]) for poly in bounds['inner'])
    form = _witness_form(witness)
    if form is not None:
        return [form], False
    kind, layer, path = witness['kind'], witness['layer'], witness['path_nm']
    source = pads.get(witness['source_pad'])
    if source is None:
        return ['source pad is not on the board'], False
    problems = []
    path = [tuple(q) for q in path]
    if source['net'] != witness.get('net') or not source['net']:
        problems.append('witness net is not the source pad net')
    if layer not in layers or layer not in source['copper']:
        return problems + [f'source pad has no copper on {layer!r}'], False
    width = witness['track_width_nm']
    if width < rules['min_track_width_nm']:
        return problems + ['track width is below the captured minimum'], False
    if not inner(source['uuid'], layer, path[0]):
        problems.append('path does not start inside the source pad copper')
    own = next(c for c in components if any(p['uuid'] == source['uuid'] for p in c['pads']))
    if kind == 'direct':
        target = next((c for c in components if c['id'] == witness['target']), None)
        if target is None or target is own or target['net'] != source['net']:
            problems.append('direct target is not another same-net terminal component')
        elif not any(inner(p['uuid'], layer, path[-1]) for p in target['pads']):
            problems.append('direct path does not end inside the target pad copper')
    elif kind == 'exit':
        own_clearance = geometry['track_clearance_nm'].get(source['net'], {})
        clear = max([rules['min_clearance_nm'], *own_clearance.values()])
        box = _box([tuple(q) for pad in geometry['pads'] if pad['ref'] == source['ref']
                    for bounds in pad['copper'].values() for poly in bounds['outer'] for q in poly],
                   clear + width / 2)
        end = path[-1]
        if box[0] <= end[0] <= box[2] and box[1] <= end[1] <= box[3]:
            problems.append('exit path ends inside the footprint copper clearance box')
    else:
        via = witness['via']
        diameter, drill, reaches = via['diameter_nm'], via['drill_nm'], via['reaches']
        if via['position_nm'] != list(path[-1]):
            problems.append('via is not at the path end')
        if via['layers'] != layers:
            problems.append('via is not a through via')
        if not reaches or layer in reaches or not set(reaches) <= set(layers):
            problems.append('via reaches no other copper layer')
        if (diameter < rules['min_via_diameter_nm'] or drill < rules['min_through_drill_nm']
                or diameter - drill < 2 * rules['min_via_annular_nm']):
            problems.append('via is below the captured via diameter, drill or annular minimum')
    return problems, True


def materialize(board_path, witnesses, out_path):
    """Write witnesses' copper (tracks and vias on the source net) into a new board file; rules untouched.

    Returns one record per witness: its added item UUIDs and the native
    (pcbnew connectivity) pads its first and last track directly touch.
    """
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    pads = {p.m_Uuid.AsString(): p for fp in board.GetFootprints() for p in fp.Pads()}
    records = []
    for witness in witnesses:
        net = pads[witness['source_pad']].GetNet()
        layer = board.GetLayerID(witness['layer'])
        tracks, added = [], []
        for a, b in zip(witness['path_nm'], witness['path_nm'][1:]):
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(pcbnew.VECTOR2I(*a))
            track.SetEnd(pcbnew.VECTOR2I(*b))
            track.SetWidth(witness['track_width_nm'])
            track.SetLayer(layer)
            track.SetNet(net)
            board.Add(track)
            tracks.append(track)
            added.append(track.m_Uuid.AsString())
        if witness['kind'] == 'via':
            via = pcbnew.PCB_VIA(board)
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetPosition(pcbnew.VECTOR2I(*witness['via']['position_nm']))
            via.SetWidth(witness['via']['diameter_nm'])
            via.SetDrill(witness['via']['drill_nm'])
            via.SetNet(net)
            board.Add(via)
            added.append(via.m_Uuid.AsString())
        records.append({'items': added, 'tracks': tracks})
    board.BuildConnectivity()
    connectivity = board.GetConnectivity()
    for record in records:
        tracks = record.pop('tracks')
        record['first_touches'], record['last_touches'] = (
            sorted(p.m_Uuid.AsString() for p in connectivity.GetConnectedPads(track))
            for track in (tracks[0], tracks[-1]))
    pcbnew.SaveBoard(str(out_path), board)
    return records


def _violations(report):
    return list(report.get('violations', []))


def _key(violation):
    return json.dumps({'type': violation.get('type'), 'description': violation.get('description'),
                       'items': sorted(json.dumps(i, sort_keys=True) for i in violation.get('items', []))},
                      sort_keys=True)


def qualify(board_path, groups, out):
    """Native qualification: each group of witnesses on its own isolated copy, unchanged rules, refilled DRC.

    Before any copper is materialized, every witness's source, target, kind
    and reach claim is checked against a fresh native capture of the board
    (``_witness_problems``); a semantic failure refuses the group, and a
    malformed witness or an empty group is never written and is refused:
    zero witnesses are zero evidence. Writable groups are still checked
    natively so their findings are reported. After
    materialization, native connectivity must show each witness's first track
    touching its source pad and a direct witness's last track touching a pad of
    its target. A group is then qualified only when the native DRC of its copy
    adds no rule finding beyond the unmodified copy, other than the
    dangling-end warnings an open access stub carries by construction, and does
    not add an unconnected item (a same-net stub re-pairs the ratsnest but
    cannot disconnect). pcbnew rewrites the project file on save; the copy's
    rule files are restored to the source bytes before DRC. Execution failure
    is refused, never a pass.
    """
    from .executor import execute
    from .placement_probe import routing_geometry
    board_path, out = Path(board_path), Path(out)
    if out.exists():
        raise ValueError('qualification output must be new')
    out.mkdir(parents=True)
    siblings = [board_path.with_suffix(s) for s in ('.kicad_pcb', '.kicad_pro', '.kicad_dru')]
    siblings = [p for p in siblings if p.is_file()]
    geometry = routing_geometry(board_path)
    pads = {p['uuid']: p for p in geometry['pads']}
    components = terminal_components(
        geometry, lambda uuid, layer, bound: [[tuple(q) for q in poly] for poly in pads[uuid]['copper'][layer][bound]])

    def copy(directory):
        directory.mkdir()
        for path in siblings:
            shutil.copyfile(path, directory / path.name)
        return directory

    def drc(directory, work):
        record = execute(directory, work, ['/usr/bin/kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                                           '--refill-zones', '-o', '/work/report.json',
                                           '/candidate/' + board_path.name], timeout=600)
        if record['execution'] != 'ok' or not (work / 'report.json').is_file():
            raise ValueError(f'native DRC execution failed: {record["execution"]}')
        return strict_json((work / 'report.json').read_bytes())

    base = copy(out / 'base')
    report = drc(base, out / 'base-drc')
    before, unconnected = Counter(_key(v) for v in _violations(report)), len(report.get('unconnected_items', []))
    results = []
    for number, group in enumerate(groups):
        semantic, writable = [], True
        if not isinstance(group, list) or not group:
            # Zero witnesses are zero evidence: an empty group is refused, never qualified.
            semantic.append({'witness': None, 'problem': 'a witness group must list at least one witness'})
            group, writable = [], False
        for i, witness in enumerate(group):
            problems, ok = _witness_problems(witness, geometry, components)
            semantic += [{'witness': i, 'problem': problem} for problem in problems]
            writable = writable and ok
        if not writable:
            results.append({'group': number, 'status': 'refused', 'added_items': [], 'semantic_findings': semantic,
                            'new_findings': [], 'unconnected_items': None})
            continue
        directory = copy(out / f'group-{number}')
        rules = {p.name: p.read_bytes() for p in siblings if p.suffix != '.kicad_pcb'}
        records = materialize(directory / board_path.name, group, directory / board_path.name)
        added = {uuid for record in records for uuid in record['items']}
        for i, (witness, record) in enumerate(zip(group, records)):
            if witness['source_pad'] not in record['first_touches']:
                semantic.append({'witness': i,
                                 'problem': 'native connectivity: first track does not touch the source pad'})
            if witness['kind'] == 'direct':
                target = next((c for c in components if c['id'] == witness.get('target')), {'pads': []})
                if not {p['uuid'] for p in target['pads']} & set(record['last_touches']):
                    semantic.append({'witness': i,
                                     'problem': 'native connectivity: last track does not touch the target pad'})
        for name, raw in rules.items():
            (directory / name).write_bytes(raw)
        report = drc(directory, out / f'group-{number}-drc')
        after = _violations(report)
        remaining, new = Counter(before), []
        for violation in after:
            key = _key(violation)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            items = {i.get('uuid') for i in violation.get('items', [])}
            if violation.get('type') in DANGLING and items <= added:
                continue
            new.append(violation)
        connections = len(report.get('unconnected_items', []))
        if connections > unconnected:
            new.append({'type': 'unconnected_items', 'description': f'{connections} unconnected items after, '
                                                                    f'{unconnected} before'})
        results.append({'group': number, 'status': 'refused' if new or semantic else 'qualified',
                        'added_items': sorted(added), 'semantic_findings': semantic, 'new_findings': new,
                        'unconnected_items': [unconnected, connections]})
    return {'schema': QUALIFICATION, 'board': board_path.name, 'groups': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('analyze', 'block'):
        command = sub.add_parser(name)
        command.add_argument('--geometry', required=True,
                             help=f'{GEOMETRY} capture (placement_probe --routing-geometry)')
        command.add_argument('--policy', required=True,
                             help='routing policy JSON: track_width_nm, routing_layers, vias')
        command.add_argument('--limits', help='optional search limits JSON')
        command.add_argument('--out', required=True)
        if name == 'block':
            command.add_argument('--candidate', required=True, help='agent-ee.block-candidate/2 document')
            command.add_argument('--capture', required=True,
                                 help='agent-ee.placement-probe/1 capture of the same board (placement_probe)')
            command.add_argument('--board', help='the saved board: both captures must equal its native captures '
                                                 '(contour and rule authority native board); without it the '
                                                 'rules are unbound and every terminal is unknown')
    args = parser.parse_args(argv)
    geometry = strict_json(Path(args.geometry).read_bytes())
    policy = strict_json(Path(args.policy).read_bytes())
    limits = strict_json(Path(args.limits).read_bytes()) if args.limits else None
    if args.command == 'analyze':
        report = analyze_access(geometry, None, policy, limits)
    else:
        report = block_access(strict_json(Path(args.candidate).read_bytes()), geometry, policy, limits,
                              capture=strict_json(Path(args.capture).read_bytes()), board=args.board)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report['summary'], sort_keys=True), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
