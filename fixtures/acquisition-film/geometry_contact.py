"""Conservative centerline joins through proved native pad/track copper.

The caller supplies a saved board's native objects. A positive polygon contact
is required; bounding boxes, proximity and electrical net names are not proof.
"""
import math


def pad_track_contact(pad, track, layer):
    import pcbnew as k
    layer_names = {k.F_Cu: 'F.Cu', k.B_Cu: 'B.Cu'}
    if layer not in layer_names:
        raise ValueError('acquisition contact requires an outer copper layer')
    if (track.GetClass() != 'PCB_TRACK' or track.GetLayer() != layer
            or not pad.IsOnLayer(layer) or not pad.FlashLayer(layer)
            or pad.GetNetCode() <= 0 or pad.GetNetCode() != track.GetNetCode()):
        return None
    copper = []
    for item in (pad, track):
        polygon = k.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(polygon, layer, 0, 1, k.ERROR_INSIDE)
        copper.append(polygon)
    contact = k.SHAPE_POLY_SET(copper[0])
    contact.BooleanIntersection(copper[1])
    contact.Simplify()
    if contact.IsEmpty() or contact.Area() <= 0:
        return None
    # A straight leg from the pad center to a contact point stays inside these
    # convex SMD pads. Do not infer that for holes or arbitrary custom copper.
    if (pad.GetAttribute() != k.PAD_ATTRIB_SMD or pad.HasHole()
            or pad.GetShape() not in (k.PAD_SHAPE_RECT, k.PAD_SHAPE_ROUNDRECT,
                                     k.PAD_SHAPE_CIRCLE, k.PAD_SHAPE_OVAL)
            or contact.OutlineCount() != 1 or contact.HoleCount(0) != 0):
        raise ValueError('positive pad/track contact needs a convex SMD path witness')
    def xy(point):
        return (point.x / 1e6, point.y / 1e6)
    center, a, b = map(xy, (pad.GetPosition(), track.GetStart(), track.GetEnd()))
    dx, dy = b[0] - a[0], b[1] - a[1]
    squared = dx * dx + dy * dy
    if squared == 0:
        raise ValueError('pad/track contact requires a nonzero straight track')
    outline = contact.COutline(0)
    vertices = [outline.CPoint(index) for index in range(outline.PointCount())]
    interior = k.VECTOR2I(round(sum(p.x for p in vertices) / len(vertices)),
                         round(sum(p.y for p in vertices) / len(vertices)))
    # The vertex mean lies in a convex intersection. Recheck the rounded native
    # point so a sub-nanometer sliver cannot manufacture a conductive join.
    if not pad.HitTest(interior) or not track.HitTest(interior):
        raise ValueError('pad/track contact has no verified interior witness')
    point = xy(interior)
    fraction = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
    projected = (a[0] + fraction * dx, a[1] + fraction * dy)
    distance = math.dist(center, point) + math.dist(point, projected)
    return dict(contact_mm=list(point), track_point_mm=list(projected),
                path_length_mm=distance, contact_area_mm2=contact.Area() / 1e12,
                pad_uuid=pad.m_Uuid.AsString(), track_uuid=track.m_Uuid.AsString(),
                net=pad.GetNetname(), layer=layer_names[layer])
