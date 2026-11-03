"""Deterministic producer policy; native DRC still grades every saved marking."""
import re

# Exact standard metric chip identities, not reference prefixes or size guesses.
DENSE_PASSIVE = re.compile(r'(?:Resistor_SMD:R|Capacitor_SMD:C|Inductor_SMD:L)_(?:0402_1005|0603_1608)Metric\Z')


def prepare_footprint(fp, *, overhang=False):
    """Keep dense reference labels and overhanging graphics on fabrication layers."""
    import pcbnew as k
    if DENSE_PASSIVE.fullmatch(fp.GetFPIDAsString()):
        reference = fp.Reference()
        if reference.GetLayer() in (k.F_SilkS, k.B_SilkS):
            reference.SetLayer(k.B_Fab if reference.GetLayer() == k.B_SilkS else k.F_Fab)
    if overhang:
        for item in [*fp.GraphicalItems(), *fp.GetFields()]:
            if item.GetLayer() in (k.F_SilkS, k.B_SilkS):
                item.SetLayer(k.B_Fab if item.GetLayer() == k.B_SilkS else k.F_Fab)


def overhang_orientation_mark(board, fp, record, rotation):
    """Place a reference and native-angle callout wholly inside the substrate.

    The numeric angle uses the declared KiCad footprint convention, rather than
    guessing pin one from a footprint graphic. Full original graphics stay on Fab.
    Positions are searched towards the outline's bounds centre; an unplaceable
    mark refuses generation. Native DRC still grades the complete saved board.
    """
    import pcbnew as k
    from decimal import Decimal
    from .board_envelope import distance2, point, segment_inside

    vertices = [point([Decimal(v) for v in e['start_mm']], 'outline')
                for e in record['outline']['edges']]
    centre = tuple((min(p[i] for p in vertices) + max(p[i] for p in vertices)) // 2 for i in (0, 1))
    text = k.PCB_TEXT(board)
    text.SetText(f'ASM {fp.GetReference()} {rotation}deg')
    text.SetLayer(k.B_SilkS if fp.IsFlipped() else k.F_SilkS)
    text.SetMirrored(fp.IsFlipped())
    text.SetTextSize(k.VECTOR2I(800000, 800000))
    text.SetTextThickness(120000)
    obstacles = []
    copper = k.B_Cu if fp.IsFlipped() else k.F_Cu
    mask = k.B_Mask if fp.IsFlipped() else k.F_Mask
    courtyard = k.B_CrtYd if fp.IsFlipped() else k.F_CrtYd
    for part in board.GetFootprints():
        if part.IsFlipped() == fp.IsFlipped():
            obstacles.append(part.GetBoundingBox(False, False))
        boxes = [i.GetBoundingBox() for i in part.GraphicalItems() if i.GetLayer() == courtyard]
        if boxes:
            x0, y0 = min(b.GetLeft() for b in boxes), min(b.GetTop() for b in boxes)
            x1, y1 = max(b.GetRight() for b in boxes), max(b.GetBottom() for b in boxes)
            obstacles.append(k.BOX2I(k.VECTOR2I(x0, y0), k.VECTOR2I(x1 - x0, y1 - y0)))
        obstacles.extend(p.GetBoundingBox() for p in part.Pads()
                         if p.IsOnLayer(copper) or p.IsOnLayer(mask))
        obstacles.extend(i.GetBoundingBox() for i in [*part.GraphicalItems(), *part.GetFields()]
                         if i.GetLayer() == text.GetLayer() and
                         (i.GetClass() != 'PCB_FIELD' or i.IsVisible()))
    obstacles.extend(i.GetBoundingBox() for i in board.GetDrawings() if i.GetLayer() == text.GetLayer())
    anchor = fp.GetPosition()
    margin = 500000
    for step in range(201):
        text.SetPosition(k.VECTOR2I(anchor.x + (centre[0] - anchor.x) * step // 200,
                                   anchor.y + (centre[1] - anchor.y) * step // 200))
        box = text.GetBoundingBox()
        corners = [(box.GetLeft(), box.GetTop()), (box.GetRight(), box.GetTop()),
                   (box.GetRight(), box.GetBottom()), (box.GetLeft(), box.GetBottom())]
        edges = list(zip(corners, corners[1:] + corners[:1]))
        if not all(segment_inside(a, b, vertices) for a, b in edges):
            continue
        # Both directions matter: a concave outline vertex may approach a long text edge.
        if any(distance2(p, a, b) < margin * margin
               for p in vertices for a, b in edges) or any(
                distance2(p, a, b) < margin * margin for p in corners
                for a, b in zip(vertices, vertices[1:] + vertices[:1])):
            continue
        # The placer adds its ordinary gap to the 0.2 mm marking reservation.
        # Keep conservative courtyard bounds clear for the common 0.5/1 mm gaps.
        box.Inflate(1500000)
        if not any(box.Intersects(obstacle) for obstacle in obstacles):
            return text
    raise ValueError(f'no in-board assembly orientation mark fits for {fp.GetReference()}')


def reserve_imu_markings(board, policy):
    """Reserve visible IMU and assembly callout boxes plus 0.2 mm.

    The effective packing policy retains the UUID and exact reservation geometry.
    The board itself and the captured policy are not modified.
    """
    import pcbnew as k
    # Leave malformed policies to pack's existing fail-closed validation.
    if not isinstance(policy, dict) or not isinstance(policy.get('obstacles'), list):
        return policy
    obstacles = list(policy['obstacles'])
    for item in sorted(board.GetDrawings(), key=lambda x: x.m_Uuid.AsString()):
        if (item.GetClass() != 'PCB_TEXT' or not item.IsVisible() or
                item.GetText().split()[:1] not in (['IMU_AXES'], ['ASM']) or
                item.GetLayer() not in (k.F_SilkS, k.B_SilkS)):
            continue
        box = item.GetBoundingBox()
        margin = 200000
        x0, y0 = box.GetLeft() - margin, box.GetTop() - margin
        x1, y1 = box.GetRight() + margin, box.GetBottom() + margin
        obstacles.append(dict(side='F' if item.GetLayer() == k.F_SilkS else 'B',
            polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
            source=item.GetText().split()[0] + '/' + item.m_Uuid.AsString()))
    return {**policy, 'obstacles': obstacles}
