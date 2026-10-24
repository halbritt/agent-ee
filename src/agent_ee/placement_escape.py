"""Soft space preferences for aligned differential terminals, never design rules.

The normal router recognizes KiCad P/N suffixes. Here that naming convention
only proposes space outside an unambiguous two-pad terminal. Actual routing,
coupling and native checks still decide whether a placement works. No footprint
reference, board name or final coordinate is special-cased.
"""
from collections import defaultdict


def hints(row, *, width, gap, clearance):
    """Infer local outward rectangles from captured pads and native pair sizes.

    One P and one N pad must share a row/column off the occupancy centre. The
    preference extends two pair pitches past the pad edge and allows a track
    half-width plus native clearance to either side. These dimensions are an
    agent-selected search heuristic, not an impedance or routability proof.
    Ambiguous, diagonal and multi-pad terminals get no inferred preference.
    """
    if width is None or gap is None or width <= 0 or gap <= 0:
        return []
    body = row['occupancy'].get('local_rect_nm')
    if body is None:
        return []
    nets = defaultdict(list)
    for pad in row['pads']:
        nets[pad['net']].append(pad)
    boxes = {c.get('geometry', {}).get('pad_uuid'): c['rect_nm']
             for c in row['copper_local_nm']}
    result = []
    for name in sorted(nets):
        suffix = next((s for s in ('_P', '+', 'P') if name.endswith(s)), None)
        if suffix is None:
            continue
        mate = name[:-len(suffix)] + {'_P': '_N', '+': '-', 'P': 'N'}[suffix]
        if len(nets[name]) != 1 or len(nets.get(mate, [])) != 1:
            continue
        p, n = nets[name][0], nets[mate][0]
        a, b = p['local_nm'], n['local_nm']
        normal = 0 if a[0] == b[0] and a[1] != b[1] else (
            1 if a[1] == b[1] and a[0] != b[0] else None)
        if normal is None or a[normal] * 2 == body[normal] + body[normal + 2]:
            continue
        if p['uuid'] not in boxes or n['uuid'] not in boxes:
            continue
        pa, pb = boxes[p['uuid']], boxes[n['uuid']]
        rect = [min(pa[0], pb[0]), min(pa[1], pb[1]), max(pa[2], pb[2]), max(pa[3], pb[3])]
        tangent = 1 - normal
        margin = clearance + (width + 1) // 2
        rect[tangent] -= margin
        rect[tangent + 2] += margin
        depth = 2 * (width + gap)
        if a[normal] * 2 > body[normal] + body[normal + 2]:
            rect[normal] = rect[normal + 2]
            rect[normal + 2] += depth
        else:
            rect[normal + 2] = rect[normal]
            rect[normal] -= depth
        result.append({'authority': 'agent-heuristic/1', 'kind': 'differential-terminal-space',
                       'nets': [name, mate], 'pads': [p['uuid'], n['uuid']], 'rect_nm': rect,
                       'width_nm': width, 'gap_nm': gap, 'clearance_nm': clearance})
    return result
