"""Bounded silk relocation with native saved-board DRC.

The default placement pass writes only field positions. The routed artwork
producer opts into graphic translations and proves its separate write scope.
Text content, side, angle, font, size, thickness and visibility stay unchanged.
"""
import json

DECLARATION = 'agent_ee_movable_silk_fields'
SILK_CONFLICTS = frozenset(('silk_overlap', 'silk_over_copper'))


def movable_names(declaration=None):
    """Reference/Value plus explicitly named custom fields; never footprint graphics."""
    names = ['Reference', 'Value']
    if declaration is not None:
        extra = json.loads(declaration)
        if (not isinstance(extra, list) or len(extra) > 64 or
                any(not isinstance(n, str) or not n or n == DECLARATION for n in extra) or
                len(set(extra)) != len(extra)):
            raise ValueError('invalid movable silkscreen field declaration')
        names.extend(extra)
    return frozenset(names)


def _obstacle_boxes(field, items, identities=None):
    """Native same-side bounds, optionally restricted to cited native identities."""
    import pcbnew as k
    copper, mask = ((k.F_Cu, k.F_Mask) if field.GetLayer() == k.F_SilkS else (k.B_Cu, k.B_Mask))
    for item in items:
        uid = item.m_Uuid.AsString()
        if uid == field.m_Uuid.AsString() or (identities is not None and uid not in identities):
            continue
        if item.GetClass() in ('PCB_FIELD', 'PCB_TEXT') and not item.IsVisible():
            continue
        if item.GetClass() == 'PAD':
            if not (item.IsOnLayer(copper) or item.IsOnLayer(mask)):
                continue
            box = item.GetBoundingBox()
            box.Inflate(max(0, item.GetSolderMaskExpansion(mask)))
        elif item.GetLayer() in (field.GetLayer(), mask):
            box = item.GetBoundingBox()
        else:
            continue
        yield box


def _edge_primitives(shape):
    """Placement primitives, with a native rounded rectangle as exact segments and arcs.

    Each corner arc is cited by an exact rational point on its circle, so the
    shared curve bounds apply. Rotated or over-rounded rectangles stay unsupported.
    """
    import pcbnew as k
    from fractions import Fraction
    from .placement_probe import primitive, vector_nm
    record = primitive(shape)
    r = record.get('corner_radius_nm')
    if r is None or record.get('native_shape') != int(k.SHAPE_T_RECT):
        return [record]
    corners = sorted({tuple(vector_nm(p)) for p in shape.GetRectCorners()})
    if len(corners) != 4:
        return [record]
    (x0, y0), _, _, (x1, y1) = corners
    if corners != [(x0, y0), (x0, y1), (x1, y0), (x1, y1)] or not 0 < 2 * r < min(x1 - x0, y1 - y0):
        return [record]
    uid = record['uuid']
    parts = [dict(uuid=uid, native_class='PCB_SHAPE', kind='segment', start=list(a), end=list(b))
             for a, b in (((x0+r, y0), (x1-r, y0)), ((x1, y0+r), (x1, y1-r)),
                          ((x1-r, y1), (x0+r, y1)), ((x0, y1-r), (x0, y0+r)))]
    for cx, cy, sx, sy in ((x0+r, y0+r, -1, -1), (x1-r, y0+r, 1, -1),
                           (x1-r, y1-r, 1, 1), (x0+r, y1-r, -1, 1)):
        parts.append(dict(uuid=uid, native_class='PCB_SHAPE', kind='arc', start=[cx + sx*r, cy],
                          mid=[cx + sx*Fraction(3*r, 5), cy + sy*Fraction(4*r, 5)], end=[cx, cy + sy*r]))
    return parts


def _outline_with_holes(board):
    """One outer ring and disjoint strictly internal cutout rings, as straight bounds.

    Circles and arcs (also rounded rectangles) are bounded exactly as courtyards
    are (AGENTEE-212): the outer ring uses its inner (subset) polygon and every
    hole its outer (superset) polygon, so the qualified region is a subset of the
    true board. Curves without an exact bound leave the outline unqualified.
    """
    import pcbnew as k
    from .placement_probe import prove_contours
    from .board_envelope import strictly_inside, intersects
    from .placement import edges
    shapes = [s for s in board.GetDrawings() if s.GetLayer() == k.Edge_Cuts]
    for fp in board.GetFootprints():
        shapes.extend(s for s in fp.GraphicalItems() if s.GetLayer() == k.Edge_Cuts)
    record = prove_contours([p for s in shapes for p in _edge_primitives(s)], 'outline_missing',
                            'outline_unproven', curves_allowed=True, separate_contours=True)
    if record['status'] != 'proven':
        return None
    supersets = record['polygons']
    subsets = record.get('inner_polygons', supersets)

    def disjoint(rings):
        return not any(intersects(a, b, c, d) for i, left in enumerate(rings)
                       for right in rings[i+1:] for a, b in edges(left) for c, d in edges(right))

    # Boundary contact, crossing, multiple islands and nested holes are ambiguous.
    if not disjoint(supersets):
        return None
    outers = [i for i, p in enumerate(supersets)
              if all(q is p or strictly_inside(q[0], p) for q in supersets)]
    # Exactly one ring may enclose all others. Two or more is impossible for
    # disjoint rings, and none means separate islands: either way, no outline.
    if len(outers) != 1:
        return None
    outer = subsets[outers[0]]
    holes = [p for i, p in enumerate(supersets) if i != outers[0]]
    # The subset outer ring must still strictly enclose every superset hole.
    if not disjoint([outer] + holes) or not all(strictly_inside(h[0], outer) for h in holes):
        return None
    if any(strictly_inside(p[0], q) or strictly_inside(q[0], p)
           for i, p in enumerate(holes) for q in holes[i+1:]):
        return None
    return outer, holes


def _box_contained(corners, outline):
    from .board_envelope import inside, intersects, segment_inside
    from .placement import edges
    outer, holes = outline
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]

    def apart(hole):
        # Exact separation of closed bounding boxes: no contact, crossing or enclosure.
        return (max(p[0] for p in hole) < min(xs) or min(p[0] for p in hole) > max(xs) or
                max(p[1] for p in hole) < min(ys) or min(p[1] for p in hole) > max(ys))
    if any(min(a[0], b[0]) <= max(xs) and max(a[0], b[0]) >= min(xs) and
           min(a[1], b[1]) <= max(ys) and max(a[1], b[1]) >= min(ys) for a, b in edges(outer)):
        within = all(segment_inside(a, b, outer) for a, b in edges(corners))
    else:
        # No outer edge reaches the closed box, so one corner decides the whole box.
        within = inside(corners[0], outer)
    return (within and
            not any(not apart(hole) and (
                inside(corners[0], hole) or inside(hole[0], corners) or
                any(intersects(a, b, c, d) for a, b in edges(corners) for c, d in edges(hole)))
                    for hole in holes))


def relocate(board, findings, flipped_footprints=frozenset(), *, graphics=False):
    """Translate owned fields and opted-in graphics within 10 mm, deterministically.

    Native bounding boxes include actual text metrics (as with AGENTEE-301,
    never character-count widths). Boxes conservatively search; they do not
    establish clearance. Unqualified outlines or exhausted searches are retained
    diagnostics, never permission to hide or reshape a marking.
    """
    import pcbnew as k

    targets = {i['uuid'] for v in findings if v['type'] in SILK_CONFLICTS for i in v['items']}
    fields, obstacles = {}, []
    for fp in board.GetFootprints():
        props = {f.GetName(): f.GetText() for f in fp.GetFields()}
        names = movable_names(props.get(DECLARATION))
        for f in fp.GetFields():
            uid = f.m_Uuid.AsString()
            if f.GetName() in names and f.IsVisible() and f.GetLayer() in (k.F_SilkS, k.B_SilkS):
                fields[uid] = (fp.GetReference(), f)
        obstacles.extend(fp.Pads())
        obstacles.extend(fp.GraphicalItems())
        obstacles.extend(fp.GetFields())
    obstacles.extend(board.GetDrawings())
    if graphics:
        # Only native silk shapes/text with a saved, supported translation proof.
        for item in obstacles:
            if (item.GetClass() in ('PCB_SHAPE', 'PCB_TEXT', 'FP_SHAPE', 'FP_TEXT') and
                    item.GetLayer() in (k.F_SilkS, k.B_SilkS)):
                fp = item.GetParentFootprint()
                fields[item.m_Uuid.AsString()] = (fp.GetReference() if fp else '', item)
    outline = _outline_with_holes(board)
    settings = board.GetDesignSettings()
    margin = max(200000, settings.m_SilkClearance)
    # KiCad 10 can omit custom-field UUIDs from silk reports, citing only the
    # other item. Native bounds nominate declared fields near those cited items;
    # the saved-board DRC remains the authority on whether a conflict cleared.
    reported = frozenset(targets)
    for uid, (_, field) in fields.items():
        if field.GetClass() == 'PCB_FIELD' and field.GetName() not in ('Reference', 'Value'):
            box = field.GetBoundingBox()
            box.Inflate(margin)
            if any(box.Intersects(other) for other in _obstacle_boxes(field, obstacles, reported)):
                targets.add(uid)
    moved, moved_graphics, unresolved = [], [], []
    for uid in sorted(targets & fields.keys(), key=lambda u: (fields[u][0], u)):
        ref, field = fields[uid]
        start = field.GetPosition()
        reason = None
        is_field = field.GetClass() == 'PCB_FIELD'
        parent = field.GetParentFootprint()
        if parent and parent.m_Uuid.AsString() in flipped_footprints:
            reason = 'footprint_side_flipped'
        elif outline is None:
            reason = 'outline_unqualified'
        elif is_field and (min(field.GetTextSize().x, field.GetTextSize().y) < settings.m_MinSilkTextHeight or
                           field.GetEffectiveTextPenWidth() < settings.m_MinSilkTextThickness):
            reason = 'text_below_declared_minimum'
        elif is_field and field.IsMirrored() != (field.GetLayer() == k.B_SilkS):
            reason = 'text_side_readability'
        if reason:
            unresolved.append(dict(uuid=uid, reference=ref, reason=reason))
            continue
        boxes = list(_obstacle_boxes(field, obstacles))
        # Reserve other components' same-side courtyard interiors for assembled bodies.
        side = k.F_CrtYd if field.GetLayer() == k.F_SilkS else k.B_CrtYd
        owner = parent.m_Uuid.AsString() if parent else None
        for fp in board.GetFootprints():
            courtyard = fp.GetCourtyard(side)
            if fp.m_Uuid.AsString() != owner and courtyard.OutlineCount():
                boxes.append(courtyard.BBox())
        # Smallest displacement first, lexicographic tie break. Keep the native
        # angle and justification, so board-side reading rules cannot change.
        offsets = [(x * 250000, y * 250000) for x in range(-40, 41) for y in range(-40, 41)
                   if x*x + y*y <= 1600]
        offsets.sort(key=lambda p: (p[0]*p[0] + p[1]*p[1], p))
        for dx, dy in offsets:
            field.SetPosition(k.VECTOR2I(start.x + dx, start.y + dy))
            box = field.GetBoundingBox()
            box.Inflate(margin)
            corners = [(box.GetLeft(), box.GetTop()), (box.GetRight(), box.GetTop()),
                       (box.GetRight(), box.GetBottom()), (box.GetLeft(), box.GetBottom())]
            if (_box_contained(corners, outline) and
                    not any(box.Intersects(other) for other in boxes)):
                if dx or dy:
                    (moved if is_field else moved_graphics).append(dict(
                        uuid=uid, reference=ref, field=field.GetName() if is_field else None,
                        from_nm=[start.x, start.y], to_nm=[start.x + dx, start.y + dy]))
                break
        else:
            field.SetPosition(start)
            unresolved.append(dict(uuid=uid, reference=ref, reason='bounded_search_exhausted'))
    report = dict(grid_nm=250000, max_displacement_nm=10000000,
                  moved_fields=moved, search_diagnostics=unresolved)
    if graphics:
        report['moved_graphics'] = moved_graphics
    return report


def contained_markings(board):
    """Visible silk whose full native bounds fit a proven straight outline with holes.

    Checking all box edges also rejects boxes crossing a concavity even when
    their corners are inside. Ambiguous or unsupported contours establish no closure.
    Run in the native tool process, never import pcbnew into the coordinator.
    """
    import pcbnew as k
    outline = _outline_with_holes(board)
    if outline is None:
        return []
    items = list(board.GetDrawings())
    for fp in board.GetFootprints():
        items.extend(fp.GraphicalItems())
        items.extend(fp.GetFields())
    contained = []
    for item in items:
        if item.GetLayer() not in (k.F_SilkS, k.B_SilkS):
            continue
        if item.GetClass() in ('PCB_FIELD', 'PCB_TEXT', 'FP_TEXT') and not item.IsVisible():
            continue
        # Unsupported native artwork cannot qualify by a possibly empty bbox.
        if item.GetClass() not in ('PCB_FIELD', 'PCB_TEXT', 'FP_TEXT', 'PCB_SHAPE', 'FP_SHAPE'):
            continue
        box = item.GetBoundingBox()
        corners = [(box.GetLeft(), box.GetTop()), (box.GetRight(), box.GetTop()),
                   (box.GetRight(), box.GetBottom()), (box.GetLeft(), box.GetBottom())]
        if _box_contained(corners, outline):
            contained.append(item.m_Uuid.AsString())
    return sorted(contained)


def containment_witness(path):
    """Bind native containment of the final saved board to its receipt report."""
    import pcbnew
    from .storage import digest
    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise ValueError('native containment could not load saved board')
    return {'board_sha256': digest(path), 'markings': contained_markings(board)}
