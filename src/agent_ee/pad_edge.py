"""Exact upstream pad-edge proximity metric (kicad-route@c4af095) with per-subject-leg emission.

``pad_rect`` is the axis-aligned board-space copper rectangle of a pad and
``rect_gap`` the signed axis-aligned gap between two rectangles: negative
overlap depth is never clamped. ``claim_legs`` reproduces the upstream
subject/partner selection: every declared pad name must resolve or the claim
is unresolved (blind), partners are narrowed to the subject pad's net when
possible, and one leg is emitted per declared subject pad. Measuring a leg
says nothing about authority: a ``derived_default`` limit remains advisory.
"""
import math

METRIC_ID = 'kicad_route_c4af095_pad_rect_signed_gap'


def pad_rect(x, y, size_x, size_y, rotation_deg):
    """Axis-aligned copper rect of a standard pad: half extents from the rotated size."""
    theta = math.radians(rotation_deg)
    cos, sin = abs(math.cos(theta)), abs(math.sin(theta))
    hx = (size_x / 2) * cos + (size_y / 2) * sin
    hy = (size_x / 2) * sin + (size_y / 2) * cos
    return (x - hx, y - hy, x + hx, y + hy)


def pad_rect_from_bbox(x, y, left, top, right, bottom):
    """Axis-aligned rect of a custom pad from its effective copper bounding box."""
    hx = max(abs(left - x), abs(right - x))
    hy = max(abs(top - y), abs(bottom - y))
    return (x - hx, y - hy, x + hx, y + hy)


def rect_gap(a, b):
    """Signed axis-aligned gap: negative overlap depth, hypotenuse when diagonal, else the larger axis gap."""
    dx = max(a[0] - b[2], b[0] - a[2])
    dy = max(a[1] - b[3], b[1] - a[3])
    if dx < 0 and dy < 0:
        return max(dx, dy)
    if dx > 0 and dy > 0:
        return math.hypot(dx, dy)
    return max(dx, dy)


def _pads_named(pads, names):
    """All pads whose number is in names; duplicate numbers are all selected."""
    wanted = set(names)
    return [pad for pad in pads if pad['number'] in wanted]


def _reach(pad, partners, *, net_match):
    same = [q for q in partners if net_match and q.get('net_id', 0) == pad.get('net_id', 0) and pad.get('net_id', 0) > 0]
    use, how = (same, 'net') if same else (partners, 'any')
    best = min(use, key=lambda q: rect_gap(pad['rect'], q['rect']))
    return rect_gap(pad['rect'], best['rect']), best, how


def claim_legs(claim, pads_by_ref):
    """Measure one proximity claim against pads keyed by footprint reference.

    ``pads_by_ref[ref]`` is a list of ``{'number', 'rect', 'net_id', 'net'?}``
    for the non-aperture pads of that footprint. Returns ``{'status',
    'legs', 'missing'}``; status is ``unresolved`` when a declared pad name
    resolves to no pad, and no partial measurement is emitted in that case.
    """
    ref, near = claim['ref'], claim['near']
    declared = claim.get('pads') or {}
    missing = []
    for footprint in (ref, near):
        for name in declared.get(footprint, []):
            if not _pads_named(pads_by_ref.get(footprint, []), [name]):
                missing.append({'ref': footprint, 'pad': name})
    if missing or ref not in pads_by_ref or near not in pads_by_ref:
        if ref not in pads_by_ref:
            missing.append({'ref': ref, 'pad': None})
        if near not in pads_by_ref:
            missing.append({'ref': near, 'pad': None})
        return {'status': 'unresolved', 'legs': [], 'missing': missing, 'metric': METRIC_ID}
    subject_declared = bool(declared.get(ref))
    subject = _pads_named(pads_by_ref[ref], declared[ref]) if subject_declared else list(pads_by_ref[ref])
    partners = _pads_named(pads_by_ref[near], declared[near]) if declared.get(near) else list(pads_by_ref[near])
    if not subject or not partners:
        return {'status': 'unresolved', 'legs': [], 'missing': [{'ref': ref if not subject else near, 'pad': None}],
                'metric': METRIC_ID}
    legs = []
    if subject_declared:
        for pad in subject:
            gap, partner, how = _reach(pad, partners, net_match=True)
            legs.append({'subject_pad': pad['number'], 'partner_pad': partner['number'], 'paired_by': how,
                         'net': pad.get('net'), 'gap_mm': gap,
                         'within_max_mm': gap <= claim['max_mm'] if 'max_mm' in claim else None})
    else:
        best = None
        for pad in subject:
            gap, partner, how = _reach(pad, partners, net_match=False)
            if best is None or gap < best[0]:
                best = (gap, pad, partner, how)
        gap, pad, partner, how = best
        legs.append({'subject_pad': pad['number'], 'partner_pad': partner['number'], 'paired_by': how,
                     'net': pad.get('net'), 'gap_mm': gap,
                     'within_max_mm': gap <= claim['max_mm'] if 'max_mm' in claim else None})
    return {'status': 'measured', 'legs': legs, 'missing': [], 'metric': METRIC_ID,
            'pads_basis': 'declared' if subject_declared else 'all_non_aperture'}


def pcbnew_pads(board, reference):
    """Pad rects of one footprint read through pcbnew, with the upstream custom-pad rule."""
    import pcbnew
    footprint = board.FindFootprintByReference(reference)
    if footprint is None:
        return None
    rows = []
    for pad in footprint.Pads():
        if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            continue
        if not any(pad.IsOnLayer(layer) for layer in (pcbnew.F_Cu, pcbnew.B_Cu)):
            continue  # paste/mask-only apertures carry no copper
        position = pad.GetPosition()
        x, y = pcbnew.ToMM(position.x), pcbnew.ToMM(position.y)
        if pad.GetShape() == pcbnew.PAD_SHAPE_CUSTOM:
            box = pad.GetEffectiveShape(pad.GetLayer()).BBox()
            rect = pad_rect_from_bbox(x, y, pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()),
                                      pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom()))
        else:
            size = pad.GetSize()
            rect = pad_rect(x, y, pcbnew.ToMM(size.x), pcbnew.ToMM(size.y), pad.GetOrientation().AsDegrees())
        rows.append({'number': pad.GetNumber(), 'rect': rect, 'net_id': pad.GetNetCode(),
                     'net': pad.GetNetname()})
    return rows
