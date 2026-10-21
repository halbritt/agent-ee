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


def relocate(board, findings, flipped_footprints=frozenset(), *, graphics=False):
    """Translate owned fields and opted-in graphics within 10 mm, deterministically.

    Native bounding boxes include actual text metrics (as with AGENTEE-301,
    never character-count widths). Boxes conservatively search; they do not
    establish clearance. Unqualified outlines or exhausted searches are retained
    diagnostics, never permission to hide or reshape a marking.
    """
    import pcbnew as k
    from .board_envelope import segment_inside
    from .placement_probe import primitive, prove_contours

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
    outline = prove_contours([primitive(s) for s in board.GetDrawings()
                              if s.GetLayer() == k.Edge_Cuts], 'outline_missing', 'outline_unproven')
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
        elif outline['status'] != 'proven' or len(outline['polygons']) != 1:
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
            if (all(segment_inside(a, b, outline['polygons'][0])
                    for a, b in zip(corners, corners[1:] + corners[:1])) and
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
    """Visible silk whose full native bounds fit one proven straight outline.

    Checking all box edges also rejects boxes crossing a concavity even when
    their corners are inside. Unsupported/multiple contours establish no closure.
    Run in the native tool process, never import pcbnew into the coordinator.
    """
    import pcbnew as k
    from .board_envelope import segment_inside
    from .placement_probe import primitive, prove_contours
    outline = prove_contours([primitive(s) for s in board.GetDrawings()
                              if s.GetLayer() == k.Edge_Cuts], 'outline_missing', 'outline_unproven')
    if outline['status'] != 'proven' or len(outline['polygons']) != 1:
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
        if all(segment_inside(a, b, outline['polygons'][0])
               for a, b in zip(corners, corners[1:] + corners[:1])):
            contained.append(item.m_Uuid.AsString())
    return sorted(contained)
