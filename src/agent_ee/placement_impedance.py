"""Measure the saved native pair and reference geometry after placement refills it.

Polygon and launch checks adapted from kicad-route's pair_impedance.py. Nominal
impedance equations follow its Rust diff_pair.rs.

MIT License

Copyright (c) 2026 halbritt

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import argparse
import json
import math
from pathlib import Path

from .storage import digest, read_json, write_json
from .symbol_library import children, field, nanometres, parse_sexpr


def stackup(board_bytes, layer):
    """Read authored properties directly, before KiCad can insert dielectric defaults."""
    board = parse_sexpr(board_bytes)
    setups = children(board, 'setup')
    stacks = children(setups[0], 'stackup') if len(setups) == 1 else []
    if len(stacks) != 1:
        raise ValueError('pair impedance requires one explicit native stackup')
    rows = children(stacks[0], 'layer')
    kinds = [field(row, 'type', 1)[0] for row in rows]
    copper = [i for i, kind in enumerate(kinds) if kind == 'copper']
    indices = [i for i in copper if rows[i][1] == layer]
    if len(indices) != 1:
        raise ValueError(f'pair impedance requires one explicit copper layer {layer}')
    index = indices[0]
    directions = [1] if index == copper[0] else [-1] if index == copper[-1] else [-1, 1]
    heights, permittivities, references = [], [], []
    thickness = nanometres(field(rows[index], 'thickness', 1)[0])
    for direction in directions:
        di, ref = index + direction, index + 2 * direction
        if not 0 <= ref < len(rows) or kinds[di] not in ('core', 'prepreg') or ref not in copper:
            raise ValueError('each reference needs an adjacent single dielectric and copper layer')
        dielectric = rows[di]
        heights.append(nanometres(field(dielectric, 'thickness', 1)[0]))
        permittivities.append(float(field(dielectric, 'epsilon_r', 1)[0]))
        references.append(rows[ref][1])
    if thickness <= 0 or min(heights) <= 0 or not all(math.isfinite(e) and e > 0 for e in permittivities):
        raise ValueError('pair impedance requires positive explicit thickness and epsilon_r')
    if max(heights) - min(heights) > 1 or max(permittivities) - min(permittivities) > 1e-9:
        raise ValueError('stripline needs symmetric dielectric heights and equal epsilon_r')
    return dict(signal_layer=layer, reference_layers=references, copper_nm=thickness,
                height_nm=sum(heights) // len(heights), epsilon_r=permittivities[0])


def nominal_impedance(model, width, gap):
    """Nominal uncoated microstrip or symmetric homogeneous stripline at measured mm dimensions."""
    h, t, er = model['height_nm'] / 1e6, model['copper_nm'] / 1e6, model['epsilon_r']
    if len(model['reference_layers']) == 2:
        b = 2 * h + t
        m = 6 * h / (3 * h + t)
        effective_width = width + t / math.pi * (1 - .5 * math.log((t / b) ** 2 + (t / (m * math.pi * width + 1.1 * t)) ** 2))
        correction = 1 / (1 + (effective_width / b / .35) ** 2.5)
        z0 = 60 / math.sqrt(er) * math.log(1.9 * b / (.8 * effective_width + t) * (1 + .4 * correction))
        return 2 * z0 * (1 - .347 * math.exp(-2.9 * gap / (2 * h)))

    def air(ratio):
        if ratio <= 1:
            return 60 * math.log(8 / ratio + .25 * ratio)
        return 120 * math.pi / (ratio + 1.393 + .667 * math.log(ratio + 1.444))

    du = t / math.pi * math.log1p(4 * math.e * h / t * math.tanh(math.sqrt(6.517 * width / h)) ** 2)
    u = (width + du) / h
    ur = (width + .5 * (1 + 1 / math.cosh(math.sqrt(max(0, er - 1)))) * du) / h
    effective_er = (.5 * (er + 1) + .5 * (er - 1) / math.sqrt(1 + 12 / ur)) * (air(u) / air(ur)) ** 2
    return 2 * air(ur) / math.sqrt(effective_er) * (1 - .48 * math.exp(-.96 * gap / h))


def native_plan(operation, requirement, model):
    request = json.loads(operation['request']['request_json'])
    opts = request['routing_options']
    layer = model['signal_layer']
    if (opts.get('pair_layers') != [layer] or opts.get('pair_impedance_ohm') != requirement['target_ohm']
            or opts.get('impedance_tolerance_pct') != requirement['tolerance_percent']
            or opts.get('impedance_reference') != requirement['reference_net']
            or opts.get('impedance_uncoupled_nm') != round(requirement['max_uncoupled_mm'] * 1e6)):
        raise ValueError('native impedance request differs from the placement requirement')
    net_names = {n['id']: n['name'] for n in request['nets']}
    pairs = []
    seen = set()
    for row in operation['routing'].get('diff_pairs', []):
        imp = row.get('impedance') or {}
        names = [net_names[row['pos_net']], net_names[row['neg_net']]]
        width, gap = row['width_nm'], row['gap_nm']
        if (row.get('disposition') not in ('coupled', 'existing') or names != [row['pos_name'], row['neg_name']]
                or len(set(names)) != 2 or seen.intersection(names) or imp.get('signal_layer') != layer
                or imp.get('reference_layers') != model['reference_layers']
                or net_names.get(imp.get('reference_net')) != requirement['reference_net']
                or imp.get('target_ohm') != requirement['target_ohm'] or imp.get('tolerance_pct') != requirement['tolerance_percent']
                or row['class_width_nm'] <= 0 or width < row['class_width_nm'] or gap <= 0 or imp.get('solved_width_nm') != width):
            raise ValueError('resolved pair impedance or reference differs from the declared requirement')
        expected = nominal_impedance(model, width / 1e6, gap / 1e6)
        # The continuous solver rounds its physical width to the nearest native nm.
        ends = [nominal_impedance(model, (width + shift) / 1e6, gap / 1e6) for shift in (-.5, .5)]
        reported = imp['solved_ohm']
        if (not min(ends) - 1e-9 <= reported <= max(ends) + 1e-9
                or abs(expected / requirement['target_ohm'] - 1) * 100 > requirement['tolerance_percent']):
            raise ValueError('nominal impedance at the resolved pair width and gap misses its target')
        seen.update(names)
        pairs.append(dict(p=names[0], n=names[1], netclass=row['class_name'], width_mm=width / 1e6,
                          native_width_mm=row['class_width_nm'] / 1e6, gap_mm=gap / 1e6, nominal_impedance_ohm=expected))
    if not pairs:
        raise ValueError('native operation has no resolved pair impedance measurements')
    return dict(layer=layer, reference_layers=model['reference_layers'], requirement=requirement, pairs=pairs)


def _interval_length(intervals):
    end, total = 0.0, 0.0
    for lo, hi in sorted(intervals):
        total += max(0.0, hi - max(lo, end))
        end = max(end, hi)
    return total


def check(board_path, planned, model):
    import pcbnew as k
    if not planned.get('pairs'):
        raise ValueError('impedance check has no required pairs')
    layer_name = planned['layer']
    references = model['reference_layers']
    if references != planned.get('reference_layers', [planned.get('reference_layer')]):
        raise ValueError('native impedance reference layer changed')
    spec = planned['requirement']
    board = k.LoadBoard(str(board_path))
    layer = board.GetLayerID(layer_name)
    defects, results = [], []
    planes = {}
    for reference in references:
        ref_layer = board.GetLayerID(reference)
        ground = k.SHAPE_POLY_SET()
        zones = []
        for zone in board.Zones():
            if zone.GetIsRuleArea():
                continue
            if zone.IsOnLayer(layer):
                defects.append('coplanar pours require a separate impedance model')
            if zone.IsOnLayer(ref_layer) and str(zone.GetNetname()) == spec['reference_net']:
                if not zone.IsFilled() or not zone.HasFilledPolysForLayer(ref_layer):
                    defects.append(reference + ': reference plane has no saved native fill')
                else:
                    ground.Append(zone.GetFilledPolysList(ref_layer))
                    zones.append(zone)
        ground.Simplify()
        if not ground.OutlineCount():
            defects.append(reference + ': declared filled reference is missing')
        planes[reference] = (ground, zones)
    for pair in planned['pairs']:
        members = {name: [t for t in board.GetTracks() if str(t.GetNetname()) == name]
                   for name in (pair['p'], pair['n'])}
        for name, tracks in members.items():
            other = members[pair['n'] if name == pair['p'] else pair['p']]
            if not tracks:
                defects.append(name + ': no saved copper')
            pads = [pad for fp in board.GetFootprints() for pad in fp.Pads()
                    if str(pad.GetNetname()) == name and pad.IsOnLayer(layer)]
            antipads = {}
            for reference, (ground, zones) in planes.items():
                ref_layer = board.GetLayerID(reference)
                openings = k.SHAPE_POLY_SET()
                if len(references) == 2 and zones:
                    # Only the native zone's explicit clearance around this net's
                    # own PTH pads may interrupt an inner reference. Use the
                    # smallest declared clearance; larger rule-driven openings
                    # fail closed rather than licensing an arbitrary plane slot.
                    clearance = min(int(z.GetLocalClearance() or 0) for z in zones)
                    # Native fill and this pad envelope use different polygon
                    # tessellations. Bound that numerical difference explicitly.
                    clearance += int(board.GetDesignSettings().m_MaxError) + 1000
                    for pad in pads:
                        if pad.GetAttribute() == k.PAD_ATTRIB_PTH and pad.IsOnLayer(ref_layer):
                            pad.TransformShapeToPolygon(openings, ref_layer, clearance, 1000, k.ERROR_OUTSIDE)
                    openings.Simplify()
                antipads[reference] = openings
            antipad_area = {reference: 0.0 for reference in references}
            total = covered = 0.0
            zs, gaps, escapes = [], [], []
            for track in tracks:
                if track.GetClass() != 'PCB_TRACK' or track.GetLayer() != layer:
                    defects.append(name + ': via, arc or different-layer geometry needs a qualified model')
                    continue
                if track.GetWidth() < round(pair['native_width_mm'] * 1e6) - 1:
                    defects.append(name + ': saved width is below the native pair floor')
                for reference in references:
                    shape = k.SHAPE_POLY_SET()
                    track.TransformShapeToPolygon(shape, layer, 0, 1000, k.ERROR_OUTSIDE)
                    # Measure the actual exception, not the whole pad envelope.
                    shape.BooleanSubtract(planes[reference][0])
                    shape.Simplify()
                    antipad_area[reference] += shape.Area() / 1e12
                    shape.BooleanSubtract(antipads[reference])
                    shape.Simplify()
                    if shape.OutlineCount():
                        defects.append(name + ': saved trace copper is not fully over the declared filled reference ' + reference)
                a, b = track.GetStart(), track.GetEnd()
                dx, dy = b.x - a.x, b.y - a.y
                length = math.hypot(dx, dy)
                if length == 0:
                    defects.append(name + ': zero-length track')
                    continue
                total += length / 1e6
                ux, uy = dx / length, dy / length
                intervals = []
                for partner in other:
                    if partner.GetClass() != 'PCB_TRACK' or partner.GetLayer() != layer:
                        continue
                    c, d = partner.GetStart(), partner.GetEnd()
                    px, py = d.x - c.x, d.y - c.y
                    if abs(ux * py - uy * px) > 1:  # at most one native nm off parallel
                        continue
                    ends = [ux * (v.x - a.x) + uy * (v.y - a.y) for v in (c, d)]
                    lo, hi = max(0, min(ends)), min(length, max(ends))
                    if hi <= lo:
                        continue
                    gap = (abs(ux * (c.y - a.y) - uy * (c.x - a.x))
                           - (track.GetWidth() + partner.GetWidth()) / 2) / 1e6
                    # Only a coupled trunk at the declared gap discharges coverage;
                    # terminal escapes remain visible under the explicit length limit.
                    if abs(gap - pair['gap_mm']) > .001 or abs(partner.GetWidth() - track.GetWidth()) > 1:
                        continue
                    value = nominal_impedance(model, track.GetWidth() / 1e6, gap)
                    if value is None or not math.isfinite(value) or abs(value / spec['target_ohm'] - 1) * 100 > spec['tolerance_percent']:
                        defects.append(name + ': saved coupled geometry misses impedance tolerance')
                    else:
                        intervals.append((lo, hi))
                        zs.append(value)
                        gaps.append(gap)
                        if len(references) == 2:
                            # The stripline model also needs reference copper
                            # between the members, not just under each trace.
                            offset = ux * (c.y - a.y) - uy * (c.x - a.x)
                            low = min(-track.GetWidth() / 2, offset - partner.GetWidth() / 2)
                            high = max(track.GetWidth() / 2, offset + partner.GetWidth() / 2)
                            for reference, (ground, _) in planes.items():
                                corridor = k.SHAPE_POLY_SET()
                                corridor.NewOutline()
                                for along, across in ((lo, low), (hi, low), (hi, high), (lo, high)):
                                    corridor.Append(round(a.x + ux * along - uy * across),
                                                    round(a.y + uy * along + ux * across))
                                corridor.BooleanSubtract(ground)
                                corridor.Simplify()
                                if corridor.OutlineCount():
                                    defects.append(name + ': coupled corridor lacks filled reference ' + reference)
                covered += _interval_length(intervals) / 1e6
                # Unmatched intervals are allowed only as pad-connected launches,
                # never as an ungraded discontinuity in the middle of a trunk.
                cursor = 0.0
                for lo, hi in [*sorted(intervals), (length, length)]:
                    if lo > cursor:
                        escapes.append(((round(a.x + ux * cursor), round(a.y + uy * cursor)),
                                        (round(a.x + ux * lo), round(a.y + uy * lo))))
                    cursor = max(cursor, hi)
            graph = {}
            for start, end in escapes:
                graph.setdefault(start, set()).add(end)
                graph.setdefault(end, set()).add(start)
            pending = [point for point in graph if any(pad.HitTest(k.VECTOR2I(*point)) for pad in pads)]
            reached = set(pending)
            while pending:
                for neighbor in graph[pending.pop()] - reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
            if set(graph) - reached:
                defects.append(name + ': ungraded geometry is not a pad-connected terminal escape')
            uncoupled = total - covered
            if uncoupled > spec['max_uncoupled_mm'] + 1e-6 or not zs:
                defects.append(name + ': coupled geometry coverage misses declared uncoupled-length limit')
            results.append({'net': name, 'length_mm': total, 'coupled_mm': covered, 'uncoupled_mm': uncoupled,
                            'reference_opening_area_mm2': antipad_area,
                            'antipad_polygon_margin_mm': (board.GetDesignSettings().m_MaxError + 1000) / 1e6 if len(references) == 2 else 0,
                            'widths_mm': sorted({t.GetWidth() / 1e6 for t in tracks if t.GetClass() == 'PCB_TRACK'}),
                            'impedance_ohm_range': [min(zs), max(zs)] if zs else None,
                            'gap_mm_range': [min(gaps), max(gaps)] if gaps else None})
    return {'status': 'FAIL' if defects else 'PASS', 'board_sha256': digest(board_path),
            'defects': sorted(set(defects)), 'nets': results, 'plan': planned,
            'limits': 'Nominal microstrip or symmetric homogeneous stripline trunks; each adjacent native filled reference checked with 1um polygon bound. Inner PTH terminal antipads limited to own pad plus the smallest explicit zone clearance and the reported native polygon error bound. Terminal escapes separately limited. No field solver, supplier tolerance, launch-impedance or physical measurement.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verify saved differential pair impedance and reference fill')
    parser.add_argument('board', type=Path)
    parser.add_argument('request', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    info = read_json(args.request)
    try:
        operation = read_json(info['operation'])
        source = Path(info['original_board'])
        if digest(source) != info['original_sha256']:
            raise ValueError('authored board changed since placement preflight')
        layer, = info['layers']
        authored = stackup(source.read_bytes(), layer)
        if stackup(args.board.read_bytes(), layer) != authored:
            raise ValueError('final native impedance stackup differs from the authored board')
        plan = native_plan(operation, info['requirement'], authored)
        result = check(args.board, plan, authored)
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as exc:
        result = dict(status='FAIL', board_sha256=digest(args.board), defects=[str(exc)])
    write_json(args.output, result)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
