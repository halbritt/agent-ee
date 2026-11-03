"""Geometric compilation checks in the captured footprint's local frame."""

import math
import re
from decimal import Decimal

from .symbol_library import children


# The generated rectangular outline runs clockwise as seen from the front (design_compile envelope):
# each edge's start corner, end corner, direction, and unit vector in board coordinates (y down).
EDGE_RUNS = dict(
    top=("top-left", "top-right", "east", (1, 0)),
    right=("top-right", "bottom-right", "south", (0, 1)),
    bottom=("bottom-right", "bottom-left", "west", (-1, 0)),
    left=("bottom-left", "top-left", "north", (0, -1)),
)
# (cosine, sine) of each quarter turn of a footprint, in KiCad's board frame (y down).
QUARTER_TURNS = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
# Land shapes whose centre is the pad position when the pad has no offset or chamfer.
CENTRED_LANDS = ("rect", "roundrect", "circle", "oval")


def connector_edge_span(node, connector):
    """Where the rotated front courtyard starts and ends along the declared edge, as exact mm
    offsets from the connector's anchor (its position along the edge)."""
    ux, uy = EDGE_RUNS[connector["edge"]][3]
    along = [x * ux + y * uy for x, y in _rotated_courtyard(node, connector)]
    return min(along), max(along)


def connector_edge_gap(node, connector):
    """Inward gap from the rotated front courtyard to the declared edge, in mm."""
    ox, oy = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[
        connector["edge"]
    ]
    return float(
        Decimal(str(connector["inset_mm"]))
        - max(x * ox + y * oy for x, y in _rotated_courtyard(node, connector))
    )


def _rotated_courtyard(node, connector):
    """Front courtyard line and rectangle endpoints, turned to the connector's board rotation."""
    points = []
    for graphic in node:
        if not isinstance(graphic, list) or not graphic:
            continue
        if not any(layer[1:] == ["F.CrtYd"] for layer in children(graphic, "layer")):
            continue
        if graphic[0] not in ("fp_line", "fp_rect"):
            raise ValueError(
                "connector needs a linear front courtyard to establish edge proximity"
            )
        for key in ("start", "end"):
            points.extend(
                (Decimal(p[1]), Decimal(p[2])) for p in children(graphic, key)
            )
    if not points:
        raise ValueError(
            "connector footprint needs a front courtyard to establish edge proximity"
        )
    return [_turn(point, int(connector["rotation_deg"] % 360)) for point in points]


def _turn(point, rotation):
    cosine, sine = QUARTER_TURNS[rotation]
    x, y = point
    return (x * cosine + y * sine, -x * sine + y * cosine)


def courtyard_box(node):
    """Bounding box (x0, y0, x1, y1) of the straight front courtyard graphics, in mm.

    None when the courtyard is missing or has curves: the caller then makes no claim."""
    points = []
    for graphic in node:
        if not isinstance(graphic, list) or not graphic:
            continue
        if not any(layer[1:] == ["F.CrtYd"] for layer in children(graphic, "layer")):
            continue
        if graphic[0] in ("fp_line", "fp_rect"):
            points.extend((float(p[1]), float(p[2])) for key in ("start", "end") for p in children(graphic, key))
        elif graphic[0] == "fp_poly":
            points.extend((float(p[1]), float(p[2])) for pts in children(graphic, "pts") for p in children(pts, "xy"))
        else:
            return None
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def land_centre(node, number):
    """Footprint-local centre of the one land numbered ``number``, in mm.

    None when the number is repeated or the land centre can differ from the pad position
    (a drill offset, a chamfer or a shape other than CENTRED_LANDS)."""
    pads = [pad for pad in children(node, "pad") if len(pad) > 3 and pad[1] == number]
    if len(pads) != 1:
        return None
    pad = pads[0]
    at = children(pad, "at")
    offsets = [o for drill in children(pad, "drill") for o in children(drill, "offset")]
    if (pad[3] not in CENTRED_LANDS or not at or children(pad, "chamfer") or children(pad, "padstack")
            or any(float(v) for o in offsets for v in o[1:3])):
        return None
    return (float(at[0][1]), float(at[0][2]))


def _farthest(t, p, q):
    return max(math.dist(t, p), math.dist(t, q))


def _segment_reach(a, b, p, q):
    """Smallest max(|t - p|, |t - q|) over t on segment ab: both distances are convex along it, and the
    two are equal at one parameter at most, so the minimum is at a candidate below."""
    d = (b[0] - a[0], b[1] - a[1])
    length2 = d[0] ** 2 + d[1] ** 2
    candidates = [0.0, 1.0]
    if length2 > 0:
        for point in (p, q):
            candidates.append(((point[0] - a[0]) * d[0] + (point[1] - a[1]) * d[1]) / length2)
        along = d[0] * (q[0] - p[0]) + d[1] * (q[1] - p[1])
        if along:
            candidates.append((q[0] ** 2 + q[1] ** 2 - p[0] ** 2 - p[1] ** 2
                               - 2 * (a[0] * (q[0] - p[0]) + a[1] * (q[1] - p[1]))) / (2 * along))
    return min(_farthest((a[0] + u * d[0], a[1] + u * d[1]), p, q)
               for u in (min(1.0, max(0.0, c)) for c in candidates))


def decoupling_reach(device, capacitor, supply, ret):
    """The smallest limit, in mm, that lets both decoupling legs meet it with these two parts alone.

    ``supply`` and ``ret`` are (device pad, capacitor pad) numbers. The capacitor takes every quarter
    turn relative to the device (the same turn of both parts gives congruent distances) and every
    position whose courtyard box does not overlap the device's; touching boxes count as apart. The
    result is a lower bound for any placement: other parts, the outline, copper clearance and a
    coarser search grid can only raise it. None when either footprint lacks a straight front
    courtyard or a plain land centre for its pads, so that no limit is refused on a guess."""
    device_box, capacitor_box = courtyard_box(device), courtyard_box(capacitor)
    lands = [land_centre(device, supply[0]), land_centre(capacitor, supply[1]),
             land_centre(device, ret[0]), land_centre(capacitor, ret[1])]
    if device_box is None or capacitor_box is None or None in lands:
        return None
    d1, c1, d2, c2 = lands
    best = math.inf
    for rotation in QUARTER_TURNS:
        r1, r2 = _turn(c1, rotation), _turn(c2, rotation)
        corners = [_turn(c, rotation) for c in ((capacitor_box[0], capacitor_box[1]),
                                                 (capacitor_box[2], capacitor_box[3]))]
        cx0, cx1 = sorted(c[0] for c in corners)
        cy0, cy1 = sorted(c[1] for c in corners)
        # Capacitor offsets t with leg lengths |p - t| and |q - t|; its box overlaps the
        # device box exactly when t lies inside the open box F.
        p, q = (d1[0] - r1[0], d1[1] - r1[1]), (d2[0] - r2[0], d2[1] - r2[1])
        x0, x1 = device_box[0] - cx1, device_box[2] - cx0
        y0, y1 = device_box[1] - cy1, device_box[3] - cy0
        middle = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if not (x0 < middle[0] < x1 and y0 < middle[1] < y1):
            best = min(best, math.dist(p, q) / 2)
            continue
        # The free minimum is inside F, so the constrained one lies on F's boundary.
        ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        best = min(best, *(_segment_reach(ring[i], ring[(i + 1) % 4], p, q) for i in range(4)))
    return best


def passive_size_mismatch(part, package):
    """Compare only explicitly marked standard imperial chip sizes, without guessing IC codes."""
    native = re.search(
        r"(?:^|_)(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|$)",
        part["footprint"].split(":")[1],
    )
    supplier = re.fullmatch(
        r"(?:[RCL]_?)?(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:\([^)]*\))?",
        package.strip(),
        re.I,
    )
    return (
        (native[1], supplier[1])
        if native and supplier and native[1] != supplier[1]
        else None
    )
