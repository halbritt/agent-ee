"""Courtyard geometry extraction, area calculation, and outline utilisation."""
import math
from pathlib import Path

from .storage import digest
from .symbol_library import children, field, parse_sexpr


def extract_courtyard_points(node, *, at=None):
    """Extract (x, y) coordinates from fp_line and fp_rect on F.CrtYd / B.CrtYd.

    If at=(x, y, angle_rad) is provided, coordinates are rotated and translated.
    """
    x, y, angle = (at[0], at[1], at[2]) if at is not None else (0.0, 0.0, 0.0)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    points = []
    for shape in children(node, 'fp_line') + children(node, 'fp_rect'):
        layers = field(shape, 'layer', 1)
        if not layers or layers[0] not in ('F.CrtYd', 'B.CrtYd'):
            continue
        for key in ('start', 'end'):
            coords = field(shape, key, 2)
            if not coords or len(coords) < 2:
                continue
            a, b = map(float, coords[:2])
            if at is not None:
                points.append((x + a * cos_a + b * sin_a,
                               y - a * sin_a + b * cos_a))
            else:
                points.append((a, b))
    return points


def footprint_courtyard(node):
    """Courtyard bounding box and dimensions for an unplaced footprint sexpr node."""
    points = extract_courtyard_points(node)
    if not points:
        return dict(status='none', bbox_mm=None, width_mm=None, height_mm=None, area_mm2=None)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return dict(status='linear_courtyard', bbox_mm=bbox,
                width_mm=width, height_mm=height, area_mm2=width * height)


def courtyard_union_area(boxes):
    """Compute 2D bounding-box union area via horizontal sweep-line."""
    if not boxes:
        return 0.0
    xs = sorted({p for box in boxes for p in (box[0], box[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        end, height = -math.inf, 0.0
        for low, high in intervals:
            height += max(0.0, high - max(end, low))
            end = max(end, high)
        area += (right - left) * height
    return area


def board_outline_utilisation(board_path, statement):
    """Final-coordinate courtyard bounding-box union / outline area; explicitly a proxy."""
    board_path = Path(board_path)
    raw = board_path.read_bytes()
    node = parse_sexpr(raw)
    boxes = []
    footprints = children(node, 'footprint')
    for fp in footprints:
        at_items = children(fp, 'at')
        if not at_items or len(at_items[0]) < 3:
            continue
        at = at_items[0][1:]
        x, y = map(float, at[:2])
        angle = math.radians(float(at[2]) if len(at) > 2 else 0)
        points = extract_courtyard_points(fp, at=(x, y, angle))
        if points:
            boxes.append((min(p[0] for p in points), min(p[1] for p in points),
                          max(p[0] for p in points), max(p[1] for p in points)))
    area = courtyard_union_area(boxes)
    outline = float(statement['board']['width_mm'] * statement['board']['height_mm'])
    fraction = area / outline if outline > 0 else 0.0
    return dict(
        status='measured_proxy' if len(boxes) == len(footprints) else 'partial_proxy',
        method='union of final-coordinate linear courtyard bounding boxes; sides projected together',
        courtyard_bbox_union_mm2=area, outline_mm2=outline, fraction=fraction,
        footprints=len(footprints), footprints_with_linear_courtyard=len(boxes),
        board_path=str(board_path), board_sha256=digest(board_path),
        limitations=['Not exact polygon area, routing density, thermal utilisation, or proof the outline can shrink.']
    )
