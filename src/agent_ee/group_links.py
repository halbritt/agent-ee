"""Measured functional-group relations (AGENTEE-479 S1, S2, R1, R2, D1, D2) on native pad geometry.

Every number here is a measurement with the exact upstream pad-edge metric
(``pad_edge``); none is a release gate. Shared rails are an explicit,
evidence-bound input (captured net/part provenance supplied by the caller),
never a fixed string list: a net spanning more than two groups that is not a
declared rail is surfaced in ``unknown_multi_group_nets``. D1 and D2 are
diagnostics only and carry their limitation text.
"""
import math

from .pad_edge import METRIC_ID, rect_gap

REPORT_SCHEMA = 'agent-ee.group-links/1'
D_LIMITATION = ('Diagnostic/objective only; axis-aligned bbox and centroid-to-anchor inequalities can flag '
                'legal L-shaped/interleaved or large-IC layouts and are rejected as unconditional hard release gates.')


def _best(pads_a, pads_b):
    return min(((rect_gap(a['rect'], b['rect']), a, b) for a in pads_a for b in pads_b),
               key=lambda t: (t[0], t[1]['number'], t[2]['number']))


def _centre(rect):
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def partition(groups, references, *, fixed=()):
    """S1: every circuit footprint belongs to exactly one declared group, or is fixed/shared."""
    owner, duplicates = {}, []
    for group in groups['groups']:
        for ref in group['refs']:
            if ref in owner:
                duplicates.append(ref)
            owner[ref] = group['name']
    references = set(references)
    missing = sorted(ref for ref in references if ref not in owner and ref not in set(fixed))
    extra = sorted(ref for ref in owner if ref not in references)
    return {'owner': owner, 'duplicate_refs': sorted(duplicates), 'ungrouped_refs': missing,
            'undeclared_refs': extra, 'complete': not (duplicates or missing or extra)}


def measure(groups, footprints, rails, *, fixed=()):
    """Measure group relations.

    ``footprints[ref] = {'x', 'y', 'side', 'pads': [{'number', 'rect', 'net_id', 'net'}]}``
    with non-aperture pads only. ``rails = {'shared_rails': {net: evidence text},
    'scoped': {net: {'intra_group': name, 'near_pairs': [[a, b], ...]}}}``.
    """
    shared = set(rails.get('shared_rails', {}))
    scoped = rails.get('scoped', {})
    s1 = partition(groups, footprints, fixed=fixed)
    owner = s1['owner']
    anchor_of = {g['name']: g['anchor'] for g in groups['groups']}
    net_groups = {}
    for ref, name in owner.items():
        for pad in footprints.get(ref, {}).get('pads', []):
            if pad.get('net_id', 0) > 0 and pad.get('net'):
                net_groups.setdefault(pad['net'], set()).add(name)
    unknown = sorted(net for net, names in net_groups.items() if len(names) > 2 and net not in shared)
    report = {}
    for group in groups['groups']:
        name, refs, anchor = group['name'], group['refs'], group['anchor']
        present = [r for r in refs if r in footprints]
        signal = {}
        for ref in present:
            for pad in footprints[ref]['pads']:
                net = pad.get('net')
                if pad.get('net_id', 0) > 0 and net and (net not in shared or
                                                          scoped.get(net, {}).get('intra_group') == name):
                    signal.setdefault(net, []).append((ref, pad))
        intra = []
        for net, endpoints in sorted(signal.items()):
            distinct = sorted({ref for ref, _ in endpoints})
            for i in range(len(distinct)):
                for j in range(i + 1, len(distinct)):
                    gap, a, b = _best([p for r, p in endpoints if r == distinct[i]],
                                      [p for r, p in endpoints if r == distinct[j]])
                    ca, cb = _centre(a['rect']), _centre(b['rect'])
                    intra.append({'net': net, 'ref_a': distinct[i], 'pad_a': a['number'], 'ref_b': distinct[j],
                                  'pad_b': b['number'], 'gap_mm': round(gap, 4),
                                  'euclidean_pad_centre_mm': round(math.hypot(ca[0] - cb[0], ca[1] - cb[1]), 4)})
        anchor_links = []
        if anchor in footprints:
            for ref in present:
                if ref == anchor or not footprints[ref]['pads'] or not footprints[anchor]['pads']:
                    continue
                gap, a, b = _best(footprints[ref]['pads'], footprints[anchor]['pads'])
                shared_net = a.get('net') if a.get('net_id', 0) > 0 and a.get('net_id') == b.get('net_id') else None
                anchor_links.append({'member': ref, 'anchor': anchor, 'member_pad': a['number'],
                                     'anchor_pad': b['number'], 'shared_net': shared_net, 'gap_mm': round(gap, 4),
                                     'origin_distance_mm': round(math.hypot(footprints[ref]['x'] - footprints[anchor]['x'],
                                                                            footprints[ref]['y'] - footprints[anchor]['y']), 4)})
        near_links = []
        for target in group.get('near', []):
            target_refs = [r for r in next((g['refs'] for g in groups['groups'] if g['name'] == target), [])
                           if r in footprints]
            best = None
            for ref in present:
                for pad in footprints[ref]['pads']:
                    net = pad.get('net')
                    if pad.get('net_id', 0) <= 0 or not net:
                        continue
                    if net in shared and [name, target] not in scoped.get(net, {}).get('near_pairs', []):
                        continue
                    for other in target_refs:
                        for partner in footprints[other]['pads']:
                            if partner.get('net_id') != pad.get('net_id'):
                                continue
                            gap = rect_gap(pad['rect'], partner['rect'])
                            key = (gap, ref, pad['number'], other, partner['number'])
                            if best is None or key < best[0]:
                                best = (key, ref, pad, other, partner, net)
            if best is None:
                near_links.append({'from_group': name, 'to_group': target, 'paired_by': None, 'gap_mm': None,
                                   'reason': 'no shared non-rail signal net between the groups'})
            else:
                _, ref, pad, other, partner, net = best
                near_links.append({'from_group': name, 'to_group': target, 'paired_by': 'signal_net',
                                   'from_ref': ref, 'from_pad': pad['number'], 'to_ref': other,
                                   'to_pad': partner['number'], 'net': net, 'gap_mm': round(best[0][0], 4),
                                   'authority_role': 'soft_objective_and_measured_interface'})
        xs = [footprints[r]['x'] for r in present]
        ys = [footprints[r]['y'] for r in present]
        bbox = [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)] if present else None
        intruders = sorted(f'{owner[r]}:{r}' for r in owner if r in footprints and owner[r] != name and bbox
                           and bbox[0] <= footprints[r]['x'] <= bbox[2] and bbox[1] <= footprints[r]['y'] <= bbox[3])
        inversions = []
        if anchor in footprints:
            for ref in present:
                if ref == anchor:
                    continue
                d_own = math.hypot(footprints[ref]['x'] - footprints[anchor]['x'], footprints[ref]['y'] - footprints[anchor]['y'])
                for other, other_anchor in anchor_of.items():
                    if other == name or other_anchor not in footprints:
                        continue
                    d_foreign = math.hypot(footprints[ref]['x'] - footprints[other_anchor]['x'],
                                           footprints[ref]['y'] - footprints[other_anchor]['y'])
                    if d_foreign < d_own:
                        inversions.append({'member': ref, 'own_anchor_mm': round(d_own, 4), 'foreign_group': other,
                                           'foreign_anchor_mm': round(d_foreign, 4)})
        report[name] = {'anchor': anchor, 'refs': list(refs), 'members_present': present,
                        'R2_intra_group_signal_links': intra, 'R2_member_to_anchor_links': anchor_links,
                        'R1_declared_near_interfaces': near_links,
                        'diagnostic_only_not_hard_gate': {'D1_bbox_mm': bbox, 'D1_bbox_foreign_origin_intruders': intruders,
                                                          'D2_anchor_separation_inversions': inversions,
                                                          'limitation': D_LIMITATION}}
    return {'schema': REPORT_SCHEMA, 'metric': METRIC_ID, 'S1_complete_partition': s1,
            'S2_shared_rails': {'shared_rails': dict(rails.get('shared_rails', {})), 'scoped': scoped,
                                'unknown_multi_group_nets': unknown},
            'groups': report, 'authority': 'measurements and diagnostics only; no automatic group pass'}


def objective(report):
    """The soft objective the solver minimises: sum of R2 and R1 gaps clamped at zero."""
    total = 0.0
    for group in report['groups'].values():
        total += sum(max(0.0, link['gap_mm']) for link in group['R2_intra_group_signal_links'])
        total += sum(max(0.0, link['gap_mm']) for link in group['R2_member_to_anchor_links'])
        total += sum(max(0.0, link['gap_mm']) for link in group['R1_declared_near_interfaces']
                     if link.get('gap_mm') is not None)
    return round(total, 4)


def pcbnew_footprints(board, references):
    """Footprint centres, sides and non-aperture pad rects read through pcbnew."""
    from .pad_edge import pcbnew_pads
    import pcbnew
    rows = {}
    for ref in references:
        footprint = board.FindFootprintByReference(ref)
        if footprint is None:
            continue
        position = footprint.GetPosition()
        rows[ref] = {'x': pcbnew.ToMM(position.x), 'y': pcbnew.ToMM(position.y),
                     'side': 'B' if footprint.IsFlipped() else 'F', 'pads': pcbnew_pads(board, ref)}
    return rows
