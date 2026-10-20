"""Functional presentation for flat emitted schematics (AGENTEE-222).

Selected by ``"presentation": "functional"`` in an ``agent-ee.schematic-layout/1``
record. The arrangement changes only where things are drawn, never what is
connected:

- Parts are grouped: listed layout groups first, then one group per remaining
  anchor part (anything that is not a satellite or a marker).
- Satellites (two-pin R/C/L/D/FB/F/Y parts on two distinct nets) are placed
  beside the pin they serve and joined to it by a short wire. A shunt part (one
  pin on a rail) hangs perpendicular to the pin's wire, so several decoupling
  parts form a comb; a series part sits in line and its far pin can carry a
  further chain. Every placement is checked against every connection point,
  wire and visual reservation already in its cluster. Satellites that do not
  fit are retried while a sweep places any; a shunt part may then continue an
  anchor pin in line; one that does not fit within ``MAX_STEPS`` grid steps
  stays a labelled loose part.
- Every net keeps at least one label carrying its intent name: the target pin
  keeps its label, only a wired satellite's attach pin drops its own. Runs of
  adjacent labelled pins on an anchor's top or bottom edge fan out to staggered
  horizontal labels at the end of short wires (AGENTEE-241).
- Each group gets a dashed frame and a title, packed at its tightest aspect;
  groups flow left to right in layout order and wrap onto the smallest
  landscape paper that holds them.

Before any bytes are written, ``verify`` rebuilds connectivity from the drawn
pins, wires and labels and refuses unless it equals the intent partition
exactly. Native ERC, netlist export and parity on the output stay authoritative.
"""
import math
import re

from .schematic_emitter import (BORDER, CONNECTION_GRID, FONT, GAP, GRID, PAPERS, TITLE_RESERVE, EmissionError, Q,
                                _box, _geometry, _mm, _natural, _uuid)
from .symbol_library import children

RAIL_FANOUT = 6           # A net with this many terminals is drawn as a rail.
SATELLITE_PREFIXES = frozenset(('R', 'C', 'L', 'D', 'FB', 'F', 'Y'))
MAX_STEPS = 24            # Candidate satellite positions per target, one grid apart.
PAD = GRID                # Frame padding around group content.
TITLE_BAND = 2 * GRID     # Frame band above the content that holds the title.
TITLE_FONT = '2.54'
TEXT_ADVANCE = FONT * 9 // 10  # Generous mean glyph advance of KiCad's 1.27 mm stroke font.
TITLE_ADVANCE = 2540000       # Bold 2.54 mm title glyph advance, rounded up.
# Group width as quarters of sqrt(content area); tried in order on each paper.
ASPECTS = (4, 3, 5, 6, 2, 7, 8)
DIRS = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


def _overlap(a, b):
    """Strict interior overlap: boxes that only touch do not collide."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _line_hits(line, box):
    """True when an axis-aligned wire crosses the interior of ``box``."""
    x0, y0, x1, y1 = line
    if y0 == y1:
        return box[1] < y0 < box[3] and box[0] < x1 and x0 < box[2]
    return box[0] < x0 < box[2] and box[1] < y1 and y0 < box[3]


def _shift(box, dx, dy):
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def label_box(x, y, away, name, is_global):
    """Visual reservation of one label. Local text sits above (or left of) its line."""
    ux, uy = DIRS[away]
    length = (len(name) + 2) * FONT
    ex, ey = x + ux * length, y + uy * length
    if is_global:  # The flag outline is centred on the connection line.
        return [min(x, ex), y - FONT, max(x, ex), y + FONT] if ux else [x - FONT, min(y, ey), x + FONT, max(y, ey)]
    if ux:
        return [min(x, ex), y - 2 * FONT, max(x, ex), y - CONNECTION_GRID]
    return [x - 2 * FONT, min(y, ey), x - CONNECTION_GRID, max(y, ey)]


def _inside(point, a, b):
    """True when ``point`` lies strictly between the ends of axis-aligned segment a-b."""
    (x, y), (ax, ay), (bx, by) = point, a, b
    if ay == by == y:
        return min(ax, bx) < x < max(ax, bx)
    if ax == bx == x:
        return min(ay, by) < y < max(ay, by)
    return False


def _collinear_overlap(a, b, c, d):
    if a[1] == b[1] == c[1] == d[1]:
        return max(min(a[0], b[0]), min(c[0], d[0])) < min(max(a[0], b[0]), max(c[0], d[0]))
    if a[0] == b[0] == c[0] == d[0]:
        return max(min(a[1], b[1]), min(c[1], d[1])) < min(max(a[1], b[1]), max(c[1], d[1]))
    return False


def _prefix(reference):
    match = re.match(r'[A-Za-z]+', reference)
    return match.group(0).upper() if match else ''


def _text_box(cx, cy, text):
    half = len(text) * TEXT_ADVANCE // 2 + FONT // 2
    return [cx - half, cy - FONT * 3 // 4, cx + half, cy + FONT * 3 // 4]


def _autoplace(item, geo, horizontal, side=None):
    """Reference and Value beside a satellite body, horizontal and centred.

    A horizontal part (pins left and right, on a wire line) carries Reference
    above and Value below its body, or both stacked on one ``side`` ('above'
    or 'below') so a wire arriving from the other side stays clear of the
    text; a vertical part carries both to its right. Returns the
    anchor-relative ``fields`` and the matching ``shapes``.
    """
    x0, y0, x1, y1 = geo['shapes'][0]
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    texts = {'Reference': item['comp']['reference'], 'Value': item['comp']['value']}
    visible = [p[1] for p in item['properties'] if p[1] in texts and not children(p, 'hide')]
    fields, shapes = {}, [geo['shapes'][0]]
    # Reference reads above Value on either side.
    rows = [n for n in {'above': ('Value', 'Reference'), 'below': ('Reference', 'Value')}.get(side, ()) if n in visible]
    for name in visible:
        text = texts[name]
        if side:  # Stacked one text row (1.6 em) apart, nearest row against the body.
            step = FONT + rows.index(name) * FONT * 8 // 5
            at = (cx, y0 - step) if side == 'above' else (cx, y1 + step)
        elif horizontal:
            at = (cx, y0 - FONT) if name == 'Reference' else (cx, y1 + FONT)
        else:
            at = (x1 + FONT + len(text) * TEXT_ADVANCE // 2 + FONT // 2,
                  cy - FONT * 4 // 5 if name == 'Reference' else cy + FONT * 4 // 5)
        fields[name] = at
        shapes.append(_text_box(*at, text))
    return dict(fields=fields, shapes=shapes, body=_union(shapes))


class _Cluster:
    """One anchor unit and the satellites wired to it, in anchor-local coordinates."""

    def __init__(self, item, index, is_global):
        self.index, self.is_global = index, is_global
        self.parts = [(item, (0, 0))]
        self.boxes = list(item['shapes'])
        self.points, self.segments, self.wires, self.junctions = set(), [], [], []
        self.targets, self.label_boxes, self.moved = [], {}, {}
        for pin, net, x, y, away in item['labels']:
            self.points.add((x, y))
            if net is not None:
                box = label_box(x, y, away, net['name'], is_global(net))
                self.label_boxes.setdefault((x, y), []).append(box)
                self.boxes.append(box)
                self._target(item, pin['number'], net, (x, y), away, False)

    def _target(self, item, number, net, point, away, owned):
        if not self.is_global(net):  # Global flags straddle the line: never extend a wire through one.
            self.targets.append(dict(key=(item['comp']['id'], item['unit'], number), net=net, point=point,
                                     end=point, away=away, closed=False, satellite=owned))

    def free(self, boxes, wire, new_points):
        if any(_overlap(b, o) for b in boxes for o in self.boxes):
            return False
        start, end = wire
        wire_box = _box([start, end])
        if any(_line_hits(wire_box, o) for o in self.boxes):
            return False
        if any(p in self.points for p in new_points):
            return False
        if any(_inside(p, start, end) for p in self.points | set(new_points)):
            return False
        for a, b in self.segments:
            if any(_inside(p, a, b) for p in new_points) or _collinear_overlap(a, b, start, end):
                return False
        return True

    def attach(self, item, target, a, b, inline, last=False):
        """Place a satellite on ``target``; return its placed copy, or None when nothing fits.

        A shunt part hangs perpendicular to the target's wire (down or right
        first). A series part (``inline``) prefers to continue the wire, but
        hangs like a shunt part when that fits at a nearer step. ``last`` is
        the fallback for a part that fits nowhere else: it only continues the
        wire, which closes the target to further parts.
        """
        ux, uy = DIRS[target['away']]
        wanted = [(0, 1), (0, -1)] if ux else [(1, 0), (-1, 0)]
        if last:
            wanted = [(ux, uy)]
        elif inline:
            wanted.insert(0, (ux, uy))
        options = []
        for direction in wanted:
            for rotation in (0, 90, 180, 270):
                geo = _geometry(item, rotation, None)
                (ax, ay, _), (bx, by, b_away) = geo['ends'][a], geo['ends'][b]
                length = abs(bx - ax) + abs(by - ay)
                if length and (bx - ax, by - ay) == (direction[0] * length, direction[1] * length):
                    for side in (None, 'above', 'below') if bx != ax else (None,):
                        options.append((dict(geo, **_autoplace(item, geo, bx != ax, side)), (ax, ay), (bx, by),
                                        b_away, direction == (ux, uy)))
                    break
        ex0, ey0 = target['end']
        for step in range(1, MAX_STEPS + 1):
            end = (ex0 + ux * step * GRID, ey0 + uy * step * GRID)
            for geo, (ax, ay), (bx, by), b_away, along in options:
                anchor = (end[0] - ax, end[1] - ay)
                far = (anchor[0] + bx, anchor[1] + by)
                shapes = [_shift(s, *anchor) for s in geo['shapes']]
                far_label = label_box(far[0], far[1], b_away, item['nets'][b]['name'],
                                      self.is_global(item['nets'][b]))
                wire_box = _box([target['end'], end])
                if any(_line_hits(wire_box, s) or _overlap(far_label, s) for s in shapes[1:]):
                    continue
                if not self.free([*shapes, far_label], (target['end'], end), [end, far]):
                    continue
                placed = dict(item, **geo)
                self.parts.append((placed, anchor))
                self.boxes += [*shapes, far_label, wire_box]
                self.points |= {end, far}
                if target['end'] != target['point'] and not target.get('fanned'):
                    self.junctions.append((target['end'], (item['comp']['id'], item['unit'], a)))
                target.pop('fanned', None)
                self.segments.append((target['end'], end))
                self.wires.append((target['end'], end, (item['comp']['id'], item['unit'], a)))
                target['end'] = end
                target['closed'] = along
                self._target(placed, b, item['nets'][b], far, b_away, True)
                return placed
        return None

    def fan(self):
        """Fan each run of adjacent labelled pins on a top or bottom edge.

        Labels on vertical pins one grid apart read as one crowded column of
        text against the pin numbers. Each pin of a run instead gets a short
        wire straight out, staggered one grid per pin, and a horizontal label:
        the left half of the run points left, the right half right, the
        outermost pins nearest the body. A pin already wired to a satellite
        keeps its place in the run and is not moved. A run is fanned whole or
        not at all.
        """
        item = self.parts[0][0]
        wired = {t['point'] for t in self.targets if t['satellite'] or t['end'] != t['point']}
        edges, seen = {}, set()
        for pin, net, x, y, away in item['labels']:
            if net is None or away not in (90, 270) or (x, y) in seen:
                continue
            seen.add((x, y))  # Stacked pins share one label.
            edges.setdefault((away, y), []).append((x, pin['number'], net, (x, y) in wired))
        for (away, y), pins in sorted(edges.items()):
            pins.sort(key=lambda p: p[0])
            run = [pins[0]]
            for pin in pins[1:] + [None]:
                if pin and pin[0] - run[-1][0] <= GRID:
                    run.append(pin)
                    continue
                if len(run) > 1 and not all(p[3] for p in run):
                    self._fan_run(item, away, y, run)
                run = [pin]

    def _fan_run(self, item, away, y, run):
        saved = list(self.boxes), set(self.points), list(self.segments), list(self.wires)
        drop = {id(b) for x, _, _, fixed in run if not fixed for b in self.label_boxes[x, y]}
        self.boxes = [b for b in self.boxes if id(b) not in drop]
        uy = -1 if away == 90 else 1
        moved = {}
        for i, (x, number, net, fixed) in enumerate(run):
            if fixed:
                continue
            side, height = (180, i + 1) if i < len(run) // 2 else (0, len(run) - i)
            end = (x, y + uy * height * GRID)
            box = label_box(end[0], end[1], side, net['name'], self.is_global(net))
            if not self.free([box], ((x, y), end), [end]):
                self.boxes, self.points, self.segments, self.wires = saved
                return
            self.boxes += [box, _box([(x, y), end])]
            self.points.add(end)
            self.segments.append(((x, y), end))
            self.wires.append(((x, y), end, ('fan', item['comp']['id'], item['unit'], number)))
            moved[number] = (*end, side)
        for target in self.targets:
            if not target['satellite'] and target['key'][2] in moved:
                target['end'], target['fanned'] = moved[target['key'][2]][:2], True
        self.moved.update(moved)

    def box(self):
        return _union(self.boxes)


def _snap(value):
    return -(-value // GRID) * GRID


def _skyline(sizes, limit):
    """Bottom-left packing of (width, height) blocks in order; returns offsets and extent.

    Each block takes the lowest position (then the leftmost) along the current
    skyline where it fits within ``limit``; widths and gaps are grid multiples.
    """
    sky, offsets = [(0, limit, 0)], []  # (x, width, top) segments.
    for width, height in sizes:
        best = None
        for start, _, _ in sky:
            if start + width > limit and start:
                continue
            top = max((t for x, w, t in sky if x < start + width + GAP and start < x + w), default=0)
            if best is None or (top, start) < best:
                best = (top, start)
        top, start = best
        offsets.append((start, top))
        right, bottom = start + width + GAP, top + height + GAP
        cut = []
        for x, w, t in sky:
            if x + w <= start or x >= right:
                cut.append((x, w, t))
                continue
            if x < start:
                cut.append((x, start - x, t))
            if x + w > right:
                cut.append((right, x + w - right, t))
        cut.append((start, right - start, bottom))
        sky = sorted(cut)
    extent = (max((x + w for (x, _), (w, _) in zip(offsets, sizes)), default=0),
              max((y + h for (_, y), (_, h) in zip(offsets, sizes)), default=0))
    return offsets, extent


def _block(group, clusters, loose, entries, aspect):
    """One framed group: its clusters and loose parts packed at width ``aspect`` x sqrt(area)."""
    # One spare grid step absorbs snapping each origin up onto the grid.
    sizes = [(_snap(b[2] - b[0]) + GRID, _snap(b[3] - b[1]) + GRID) for _, b in entries]
    area = sum(w * h for w, h in sizes)
    limit = max(max(w for w, _ in sizes), _snap(math.isqrt(area) * aspect // 4))
    cells, (right, bottom) = _skyline(sizes, limit)
    offsets = {key: (_snap(cx - box[0]), _snap(cy - box[1])) for (key, box), (cx, cy) in zip(entries, cells)}
    parts, wires, junctions = [], [], []
    for cluster in clusters:
        ox, oy = offsets['cluster', cluster.index]
        parts += [(item, (ox + x, oy + y)) for item, (x, y) in cluster.parts]
        wires += [((a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), key) for a, b, key in cluster.wires]
        junctions += [((p[0] + ox, p[1] + oy), key) for p, key in cluster.junctions]
    for index, unit in enumerate(loose):
        ox, oy = offsets['loose', index]
        parts.append((unit, (ox, oy)))
        wires += [((a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), key)
                  for a, b, key in unit.get('label_leads', [])]
    title = _snap(len(group['title']) * TITLE_ADVANCE)
    return dict(group=group, parts=parts, wires=wires, junctions=junctions,
                size=(max(_snap(right), title) + 2 * PAD, _snap(bottom) + 2 * PAD + TITLE_BAND))


def _groups(units, resolved, global_nets, spec):
    """Deterministic group membership and the satellite/anchor/marker roles."""
    fanout = {n['id']: len(n['terminals']) for n in resolved['nets']}
    power = {c['id'] for c in resolved['components'] if c['power']}
    power_nets = {n['id'] for n in resolved['nets'] if any(t['component'] in power for t in n['terminals'])}

    def rail(net):
        return net['id'] in global_nets or net['id'] in power_nets or fanout[net['id']] >= RAIL_FANOUT

    def role(item):
        comp, nets = item['comp'], list(item['nets'].values())
        if comp['power'] or len(item['pins']) == 1:
            return 'marker'
        small = len(comp['units']) == 1 and len(nets) == 2 and _prefix(comp['reference']) in SATELLITE_PREFIXES
        if small and None not in nets and nets[0]['id'] != nets[1]['id'] and not item['fixed']:
            return 'satellite'
        return 'marker' if small else 'anchor'

    roles = {(u['comp']['id'], u['unit']): role(u) for u in units}
    order = sorted(units, key=lambda u: (_natural_ref(u['comp']['reference']), u['unit']))
    groups, member = [], {}
    for group in spec:
        groups.append(dict(key=('explicit', group['id']), title=group['title'],
                           components=group['components'], items=[]))
        for cid in group['components']:
            member[cid] = len(groups) - 1
    for unit in order:
        cid = unit['comp']['id']
        if cid not in member and roles[cid, unit['unit']] == 'anchor':
            member[cid] = len(groups)
            groups.append(dict(key=('component', cid), title=_title(unit['comp']), components=[], items=[]))
    # Breadth-first over non-rail nets: satellites and markers join the group
    # that reaches them first; satellites carry the search onward.
    by_net = {}
    for unit in order:
        for number, net in sorted(unit['nets'].items(), key=lambda kv: _natural(kv[0])):
            if net is not None:
                by_net.setdefault(net['id'], []).append(unit)
    queue = [u for u in order if u['comp']['id'] in member]
    queue.sort(key=lambda u: (member[u['comp']['id']], roles[u['comp']['id'], u['unit']] != 'anchor'))
    while queue:
        following = []
        for unit in queue:
            if roles[unit['comp']['id'], unit['unit']] == 'marker':
                continue
            for number, net in sorted(unit['nets'].items(), key=lambda kv: _natural(kv[0])):
                if net is None or rail(net):
                    continue
                for other in by_net[net['id']]:
                    key = other['comp']['id'], other['unit']
                    if other['comp']['id'] in member or roles[key] == 'anchor':
                        continue
                    member[other['comp']['id']] = member[unit['comp']['id']]
                    following.append(other)
        queue = following
    # Parts reachable only through rails (decoupling, bulk capacitors, flags,
    # test points) join the anchor with most pins on their lowest-fanout net.
    anchors_on = {}
    for unit in order:
        if roles[unit['comp']['id'], unit['unit']] == 'anchor':
            for net in unit['nets'].values():
                if net is not None:
                    anchors_on.setdefault(net['id'], []).append(unit['comp']['id'])
    for unit in order:
        if unit['comp']['id'] in member:
            continue
        for net in sorted((n for n in unit['nets'].values() if n is not None),
                          key=lambda n: (fanout[n['id']], n['name'])):
            hosts = anchors_on.get(net['id'], [])
            if hosts:
                best = min(set(hosts), key=lambda cid: (-hosts.count(cid), member[cid]))
                member[unit['comp']['id']] = member[best]
                break
    # Attachment order: hops from the group's own anchors over non-rail nets
    # inside the group, so a divider leg is tried after the series part whose
    # far pin it hangs from, even when the layout lists both.
    level = {}
    queue = [u for u in order if u['comp']['id'] in member and roles[u['comp']['id'], u['unit']] == 'anchor']
    for unit in queue:
        level[unit['comp']['id'], unit['unit']] = 0
    while queue:
        following = []
        for unit in queue:
            if roles[unit['comp']['id'], unit['unit']] == 'marker':
                continue
            for number, net in sorted(unit['nets'].items(), key=lambda kv: _natural(kv[0])):
                if net is None or rail(net):
                    continue
                for other in by_net[net['id']]:
                    key = other['comp']['id'], other['unit']
                    if key in level or roles[key] == 'anchor' or \
                            member.get(other['comp']['id']) != member[unit['comp']['id']]:
                        continue
                    level[key] = level[unit['comp']['id'], unit['unit']] + 1
                    following.append(other)
        queue = following
    for unit in order:  # Reached only through rails: decoupling and bulk parts.
        if unit['comp']['id'] in member and roles[unit['comp']['id'], unit['unit']] != 'anchor':
            level.setdefault((unit['comp']['id'], unit['unit']), 1)
    leftovers = [u for u in order if u['comp']['id'] not in member]
    if leftovers:
        groups.append(dict(key=('unassigned',), title='Unassigned parts', components=[], items=[]))
        for unit in leftovers:
            member[unit['comp']['id']] = len(groups) - 1
    for unit in order:
        groups[member[unit['comp']['id']]]['items'].append(unit)
    for group in groups:  # Listed order first, so a layout can set left-to-right flow.
        listed = {cid: i for i, cid in enumerate(group['components'])}
        group['items'].sort(key=lambda u: (listed.get(u['comp']['id'], len(listed)),
                                           _natural_ref(u['comp']['reference']), u['unit']))
    return [g for g in groups if g['items']], roles, level, rail, fanout


def _natural_ref(reference):
    match = re.fullmatch(r'(#?[A-Za-z_]+)([0-9]+)', reference)
    return (match.group(1), int(match.group(2))) if match else (reference, 0)


def _title(comp):
    title = (comp['reference'] + ' ' + comp['value']).strip()
    return title[:64]


def _loose_box(unit, is_global):
    """A labelled part on its own: its shapes plus the label at every connected pin."""
    boxes = list(unit['shapes'])
    for pin, net, x, y, away in unit['labels']:
        if net:
            x, y, away = unit.get('moved_labels', {}).get(pin['number'], (x, y, away))
            boxes.append(label_box(x, y, away, net['name'], is_global(net)))
    boxes += [_box([a, b]) for a, b, _ in unit.get('label_leads', [])]
    return _union(boxes)


def _readable_fields(item, is_global):
    """Place anchor and detached Reference/Value clear of their own pin labels.

    A one-pin marker uses a horizontal label, beside rather than along its
    pin-name column. Keep its connection point and UUID unchanged. The same
    reservations used by group packing enclose every new field position.
    """
    item = dict(item)
    moved = {}
    if len(item['pins']) == 1 and not item['comp']['power']:
        for pin, net, x, y, away in item['labels']:
            if net and away in (90, 270):
                # The pin name can run beyond a short pin's connection point.
                # Continue outward beyond that entire column before turning
                # the label horizontal; never draw text across the pin number.
                length = (len(pin['name']) + 3) * FONT
                ux, uy = DIRS[away]
                end = (x + ux * length, y + uy * length)
                moved[pin['number']] = (*end, 0)
                item['label_leads'] = [((x, y), end, ('marker', item['comp']['id'], item['unit'], pin['number']))]
        item['moved_labels'] = moved
    boxes = [label_box(*moved.get(pin['number'], (x, y, away)), net['name'], is_global(net))
             for pin, net, x, y, away in item['labels'] if net]
    # _geometry reserves explicit NC markers before the visible fields. Keep
    # those reservations when replacing the field boxes.
    markers = item['shapes'][1:1 + sum(net is None for _, net, *_ in item['labels'])]
    field_boxes = item['shapes'][1 + len(markers):]
    if any(_overlap(field, label) for field in field_boxes for label in boxes):
        # Keep the body and pins fixed. Search a bounded grid of horizontal,
        # centred field pairs; reserve both text rows before accepting either.
        geo = _autoplace(item, item, False)
        for step in range(MAX_STEPS + 1):
            for dx, dy in ((step * GRID, 0), (-step * GRID, 0), (0, step * GRID), (0, -step * GRID)):
                shapes = [_shift(b, dx, dy) for b in geo['shapes'][1:]]
                if any(_overlap(b, obstacle) for b in shapes for obstacle in [item['shapes'][0], *markers, *boxes]):
                    continue
                item.update(fields={name: (x + dx, y + dy) for name, (x, y) in geo['fields'].items()},
                            shapes=[item['shapes'][0], *markers, *shapes],
                            body=_union([item['shapes'][0], *markers, *shapes]))
                return item, moved
        raise EmissionError('presentation_conflict', 'no clear field placement for ' + item['comp']['reference'])
    return item, moved


def arrange(units, resolved, global_nets, spec, namespace):
    """Placements, relabelled pins and drawn nodes for one flat sheet.

    ``relabel`` maps a placed unit to its pins whose label is not drawn at the
    pin: ``None`` for a wired satellite's attach pin (no label), or the
    unit-local ``(x, y, angle)`` of a fanned label at the end of its wire.
    """
    groups, roles, level, rail, fanout = _groups(units, resolved, global_nets, spec)

    def is_global(net):
        return net['id'] in global_nets

    prepared = []
    relabel = {}
    for group in groups:
        anchors = [_readable_fields(u, is_global)[0] for u in group['items']
                   if roles[u['comp']['id'], u['unit']] == 'anchor']
        satellites = [u for u in group['items'] if roles[u['comp']['id'], u['unit']] == 'satellite']
        loose = [u for u in group['items'] if roles[u['comp']['id'], u['unit']] == 'marker']

        def inline(unit):
            return not any(rail(n) for n in unit['nets'].values())

        def preference(net, target, cluster):
            return (rail(net), target['satellite'], fanout[net['id']], cluster.index, _natural(target['key'][2]))

        def sweep(order):
            clusters = [_Cluster(u, i, is_global) for i, u in enumerate(anchors)]
            relabel_local = {}

            def hint(unit):
                """Lowest anchor pin first: downward combs then never cross a later pin's wire."""
                best = min(((preference(net, t, c), -t['point'][1], t['point'][0]) for number, net in unit['nets'].items()
                            for c in clusters for t in c.targets if t['net']['id'] == net['id']), default=None)
                return best[1:] if best else (0, 0)

            ordered = order if order is not None else sorted(
                satellites,
                key=lambda u: (level.get((u['comp']['id'], u['unit']), MAX_STEPS), inline(u), *hint(u),
                               _natural_ref(u['comp']['reference'])),
            )

            def place(sat, fallback):
                options = []
                for number, net in sat['nets'].items():
                    other = next(n for n in sat['nets'] if n != number)
                    for cluster in clusters:
                        for target in cluster.targets:
                            if target['net']['id'] == net['id'] and not target['closed']:
                                options.append((preference(net, target, cluster), cluster, target, number, other))
                options.sort(key=lambda o: o[0])
                # The fallback: a shunt part that cannot hang beside any target
                # continues an anchor pin in line instead.
                tries = [(o, False) for o in options]
                if fallback and not inline(sat):
                    tries += [(o, True) for o in options if not o[2]['satellite']]
                for (_, cluster, target, a, b), last in tries:
                    if cluster.attach(sat, target, a, b, inline(sat), last):
                        relabel_local.setdefault((sat['comp']['id'], sat['unit']), {})[a] = None
                        return True
                return False

            # Sweep until nothing more fits: a part whose target is the far pin of
            # a later part gets another try; the in-line fallback comes last,
            # after dense edge runs have fanned out to staggered columns.
            pending, fallback = list(ordered), False
            while pending:
                remaining = [sat for sat in pending if not place(sat, fallback)]
                if len(remaining) == len(pending):
                    if fallback:
                        break
                    for cluster in clusters:
                        cluster.fan()
                    fallback = True
                pending = remaining
            if not fallback:
                for cluster in clusters:
                    cluster.fan()
            return ordered, clusters, relabel_local, pending

        order, clusters, relabel_local, pending = sweep(None)
        while pending:
            unplaced = {(u['comp']['id'], u['unit']) for u in pending}
            nxt = [*pending, *(u for u in order if (u['comp']['id'], u['unit']) not in unplaced)]
            _, nxt_clusters, nxt_relabel, nxt_pending = sweep(nxt)
            if len(nxt_pending) >= len(pending):
                break
            order, clusters, relabel_local, pending = nxt, nxt_clusters, nxt_relabel, nxt_pending
        for key, mapping in relabel_local.items():
            relabel.setdefault(key, {}).update(mapping)
        loose += pending
        repaired = []
        for item in loose:
            item, moved = _readable_fields(item, is_global)
            repaired.append(item)
            if moved:
                relabel.setdefault((item['comp']['id'], item['unit']), {}).update(moved)
        loose = repaired
        for cluster in clusters:
            if cluster.moved:
                item = cluster.parts[0][0]
                relabel.setdefault((item['comp']['id'], item['unit']), {}).update(cluster.moved)
        entries = [(('cluster', c.index), c.box()) for c in clusters]
        entries += [(('loose', i), _loose_box(u, is_global)) for i, u in enumerate(loose)]
        prepared.append((group, clusters, loose, entries))
    # Every frame at the aspect that gives it the least area (least whitespace
    # inside it), then one shared aspect at a time; the first set that fits
    # the smallest paper wins.
    shared = [[_block(*parts, aspect) for parts in prepared] for aspect in ASPECTS]
    tightest = [min(column, key=lambda b: b['size'][0] * b['size'][1]) for column in zip(*shared)]
    for paper, width, height in PAPERS:
        usable_w, usable_h = width - 2 * BORDER, height - 2 * BORDER - TITLE_RESERVE
        for blocks in [tightest, *shared]:
            offsets, (right, bottom) = _skyline([b['size'] for b in blocks], usable_w)
            if right <= usable_w and bottom <= usable_h:
                break
        else:
            continue
        break
    else:
        raise EmissionError('paper_overflow', 'content exceeds the largest supported paper')
    placed, drawn, wires = [], [], []
    for index, block in enumerate(blocks):
        gx, gy = BORDER + offsets[index][0], BORDER + offsets[index][1]
        cx, cy = gx + PAD, gy + PAD + TITLE_BAND
        placed += [(item, (cx + x, cy + y)) for item, (x, y) in block['parts']]
        for a, b, key in block['wires']:
            a, b = (cx + a[0], cy + a[1]), (cx + b[0], cy + b[1])
            wires.append((a, b))
            drawn.append(['wire', ['pts', ['xy', _mm(a[0]), _mm(a[1])], ['xy', _mm(b[0]), _mm(b[1])]],
                          ['stroke', ['width', '0'], ['type', 'default']], ['uuid', Q(_uuid(namespace, 'wire', *key))]])
        for point, key in block['junctions']:
            drawn.append(['junction', ['at', _mm(cx + point[0]), _mm(cy + point[1])], ['diameter', '0'],
                          ['color', '0', '0', '0', '0'], ['uuid', Q(_uuid(namespace, 'junction', *key))]])
        group = block['group']
        drawn.append(['rectangle', ['start', _mm(gx), _mm(gy)],
                      ['end', _mm(gx + block['size'][0]), _mm(gy + block['size'][1])],
                      ['stroke', ['width', '0'], ['type', 'dash']], ['fill', ['type', 'none']],
                      ['uuid', Q(_uuid(namespace, 'group_frame', *group['key']))]])
        drawn.append(['text', Q(group['title']), ['exclude_from_sim', 'no'],
                      ['at', _mm(gx + PAD), _mm(gy + PAD + TITLE_BAND // 2), '0'],
                      ['effects', ['font', ['size', TITLE_FONT, TITLE_FONT], ['thickness', '0.508'], ['bold', 'yes']],
                       ['justify', 'left', 'bottom']],
                      ['uuid', Q(_uuid(namespace, 'group_title', *group['key']))]])
    verify(placed, relabel, wires, resolved)
    return paper, placed, relabel, drawn


def verify(placed, relabel, wires, resolved):
    """Rebuild connectivity from drawn pins, wires and labels; refuse unless it equals the intent."""
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a, b):
        parent[find(a)] = find(b)

    pins, points, labelled, ncs = {}, set(), [], []
    for item, anchor in placed:
        moved = relabel.get((item['comp']['id'], item['unit']), {})
        for pin, net, x, y, _ in item['labels']:
            point = (anchor[0] + x, anchor[1] + y)
            points.add(point)
            if net is None:
                ncs.append(point)
                continue
            pins[item['comp']['id'], pin['number']] = point
            if pin['number'] not in moved:
                labelled.append((point, net['name']))
            elif moved[pin['number']]:
                labelled.append(((anchor[0] + moved[pin['number']][0], anchor[1] + moved[pin['number']][1]),
                                 net['name']))
    ends = {p for wire in wires for p in wire}
    for a, b in wires:
        if any(_inside(p, a, b) for p in points | ends):
            raise EmissionError('presentation_conflict', 'a wire passes through a connection point')
        union(('point', a), ('point', b))
    for point, name in labelled:
        union(('point', point), ('label', name))
    for point in ncs:
        if point in ends or any(p == point for p, _ in labelled) or \
                sum(1 for p in pins.values() if p == point):
            raise EmissionError('presentation_conflict', 'a no-connect pin touches a drawn connection')
    seen = {}
    for net in resolved['nets']:
        roots = {find(('point', pins[t['component'], t['pin']])) for t in net['terminals']}
        if len(roots) != 1:
            raise EmissionError('presentation_conflict', 'drawn connectivity splits net ' + net['name'])
        root = roots.pop()
        if root in seen:
            raise EmissionError('presentation_conflict', 'drawn connectivity merges nets ' + net['name'] +
                                ' and ' + seen[root])
        seen[root] = net['name']
