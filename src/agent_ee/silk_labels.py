"""Silkscreen after an automatic placement (AGENTEE-644).

A placement moves and turns footprints, and everything a footprint prints on the
silkscreen goes with it. The body packing does not see two things:

* a label (a visible field on a silkscreen layer) keeps its offset from the part and
  lands on a neighbour's outline, pad opening or label;
* silk content that nothing can move afterwards (a part's silk drawings and its texts
  that are not fields, such as a diode's "K"; a board-level silkscreen text; the labels
  of fixed footprints) ends up on another part's silk or pad openings.

``settle`` handles the first on every board. After the job has placed the footprints it
moves the labels of the moved footprints that native DRC names in a silkscreen finding
to a free place beside their part. Native DRC of the saved board is the judge before and
after; the native shapes used to search are only a way to propose places. A label is
never hidden, resized, moved to another layer or re-justified: only its position and its
angle (0 or 90 degrees, both readable) change. A label that conflicts with nothing stays
where it is, and so does every label of a footprint the job did not move. A label
without a free place is reported by name.

``reserve``, ``fixed_content`` and ``board_obstacles`` handle the second at capture, and
only where the board's own rule severities make a silkscreen overlap an error: the
content joins its part's occupancy or the fixed obstacles, so the search keeps other
parts off it. Where those findings are warnings or ignored, capture is unchanged.
"""
import json
from pathlib import Path

SCHEMA = 'agent-ee.silk-labels/1'
# Native findings a silkscreen label can take part in (KiCad 10 type names).
FINDINGS = ('silk_overlap', 'silk_over_copper', 'silk_edge_clearance')
# The findings whose error severity makes capture reserve silk content.
RESERVING = ('silk_overlap', 'silk_over_copper')
# How often a label that native DRC still reports is searched again.
ROUNDS = 3
TEXTS = ('PCB_TEXT', 'PCB_TEXTBOX', 'PCB_FIELD')
INSIDE = 10 ** 12          # ranks every place inside the part's own outline after every place beside it
CELL = 2_000_000           # spatial index pitch; only a speed setting


def _nm(box):
    return [int(box.GetLeft()), int(box.GetTop()), int(box.GetRight()), int(box.GetBottom())]


def _grown(box, by):
    return [box[0] - by, box[1] - by, box[2] + by, box[3] + by]


def _ink(item, layer):
    """The bounds of what the item prints: native stroke shapes, not the larger text frame."""
    return _nm(item.GetEffectiveShape(layer).BBox())


def _labels(fp, silk):
    """The labels of one footprint: its visible fields on a silkscreen layer that print something."""
    return [f for f in fp.GetFields()
            if f.IsVisible() and f.GetLayer() in silk and f.GetShownText(False).strip()]


# ---------------------------------------------------------------- capture

def rule_severities(board):
    """The severities the board's own project file gives native DRC finding types.

    Empty when the board has no project file beside it or the file names none; a type the
    file leaves out has KiCad's default severity, which this does not supply."""
    name = board.GetFileName()
    project = Path(name).with_suffix('.kicad_pro') if name else None
    if project is None or not project.is_file():
        return {}
    try:
        severities = json.loads(project.read_bytes())['board']['design_settings']['rule_severities']
    except (ValueError, KeyError, TypeError):
        return {}
    return severities if isinstance(severities, dict) else {}


def reserve(board):
    """How far capture grows reserved silk content and pad openings (nm), or None.

    Capture reserves silk content only where the board's own project file makes
    ``silk_overlap`` or ``silk_over_copper`` an error. KiCad's default for both is a
    warning, so a project that does not name them reserves nothing (None). Otherwise
    the answer is ``(silk, openings)``: pad openings grow by half the board's
    silkscreen clearance and silk content by that plus the board's curve approximation
    error, by which the openings native DRC tests can exceed the exact pad shapes. Two
    reservations that touch are then at least a whole clearance apart.
    """
    if not any(rule_severities(board).get(kind) == 'error' for kind in RESERVING):
        return None
    settings = board.GetDesignSettings()
    half = (max(0, int(settings.m_SilkClearance)) + 1) // 2
    return half + max(0, int(settings.m_MaxError)), half


def fixed_content(fp, grow, mask=(), within=None):
    """Boxes of what a footprint prints that no later step can move, and of its pad openings.

    The silk drawings and the visible texts that are not fields, on either silkscreen
    layer, and ``mask``, the footprint's pad and mask openings, which another part's
    silk must stay off just the same. ``grow`` is the answer of ``reserve``. Returns
    nothing when it is None (the board reserves nothing) or when every box already
    lies in ``within``.
    """
    if grow is None:
        return []
    import pcbnew as k
    silk = (k.F_SilkS, k.B_SilkS)
    boxes = [_grown(box, grow[1]) for box in mask]
    for item in fp.GraphicalItems():
        if item.GetLayer() not in silk:
            continue
        if item.GetClass() in TEXTS:
            if item.IsVisible() and item.GetShownText(False).strip():
                boxes.append(_grown(_ink(item, item.GetLayer()), grow[0]))
        else:
            boxes.append(_grown(_nm(item.GetBoundingBox()), grow[0]))
    boxes = [box for box in boxes if box[0] < box[2] and box[1] < box[3]]
    if within is not None and all(within[0] <= box[0] and within[1] <= box[1]
                                  and box[2] <= within[2] and box[3] <= within[3] for box in boxes):
        return []
    return boxes


def fixed_footprints(board, fixed_poses=()):
    """UUIDs of the footprints a job cannot move: natively locked, or with a declared fixed pose."""
    from . import placement_problem
    rows = [{'uuid': fp.m_Uuid.AsString(), 'ref': fp.GetReference(), 'locked': bool(fp.IsLocked()),
             'position_nm': [int(fp.GetPosition().x), int(fp.GetPosition().y)],
             'side': 'B' if fp.IsFlipped() else 'F', 'rotation_deg': fp.GetOrientationDegrees()}
            for fp in board.GetFootprints()]
    return ({row['uuid'] for row in rows if row['locked']}
            | set(placement_problem.fixed_pose_owners(list(fixed_poses), rows)))


def board_obstacles(board, fixed_poses=(), known=()):
    """Silkscreen text that stays where it is, as fixed bodies for the placement engine.

    Every visible board-level silkscreen text and every label of a fixed footprint: the
    designer placed them, no step moves them, and a part placed on one is a silkscreen
    finding nothing can repair. ``known`` holds the UUIDs of texts that are obstacles
    already (declared board text). Empty where the board reserves nothing (``reserve``)
    and where no footprint can move.
    """
    grow = reserve(board)
    if grow is None:
        return []
    import pcbnew as k
    silk = (k.F_SilkS, k.B_SilkS)
    fixed = fixed_footprints(board, fixed_poses)
    footprints = list(board.GetFootprints())
    if all(fp.m_Uuid.AsString() in fixed for fp in footprints):
        return []
    texts = [item for item in board.GetDrawings()
             if item.GetClass() in TEXTS and item.GetLayer() in silk and item.IsVisible()
             and item.GetShownText(False).strip()]
    for fp in footprints:
        if fp.m_Uuid.AsString() in fixed:
            texts.extend(_labels(fp, silk))
    rows = []
    for item in texts:
        uid = item.m_Uuid.AsString()
        box = _ink(item, item.GetLayer())
        if uid in known or box[0] > box[2] or box[1] > box[3]:
            continue
        box, at = _grown(box, max(grow[0], 1)), item.GetPosition()
        rows.append(dict(
            uuid=uid, native_ref='board-text:' + uid, role='fixed_body',
            pose=dict(x_nm=int(at.x), y_nm=int(at.y), quarter_turns=0),
            occupancy=dict(status='derived', label='derived_occupancy', rung='silk+pads',
                           local_rect_nm=[box[0] - int(at.x), box[1] - int(at.y),
                                          box[2] - int(at.x), box[3] - int(at.y)]),
            copper_local_nm=[], mask_local_nm=[], holes_local_nm=[], pads=[], rotations=[0]))
    return sorted(rows, key=lambda row: row['uuid'])


# ---------------------------------------------------------------- the scene a label is placed in

class _Scene:
    """Everything a label can collide with, per silkscreen layer, as native shapes.

    The same items native DRC tests a silkscreen item against: silk drawings and texts
    of every footprint and of the board, the other labels, the solder mask openings of
    the same side (pads with their mask expansion, mask drawings, untented vias) and the
    board edge. Drill holes are added because ink over a hole prints nothing.
    """

    def __init__(self, board):
        import pcbnew as k
        self.k = k
        settings = board.GetDesignSettings()
        self.clearance = max(0, int(settings.m_SilkClearance))
        self.tolerance = max(0, int(settings.m_MaxError))
        self.rows, self.cells = {}, {}
        outline = k.SHAPE_POLY_SET()
        closed = board.GetBoardPolygonOutlines(outline, False)
        self.outline = outline if closed and outline.OutlineCount() else None
        self.sides = ((k.F_SilkS, k.F_Mask), (k.B_SilkS, k.B_Mask))
        silk = (k.F_SilkS, k.B_SilkS)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                uid = pad.m_Uuid.AsString()
                for layer, mask in self.sides:
                    if pad.IsOnLayer(mask):
                        self.add(('mask', uid, layer), layer, pad.GetEffectiveShape(mask),
                                 extra=max(0, int(pad.GetSolderMaskExpansion(mask))))
                    if pad.HasHole():
                        self.add(('hole', uid, layer), layer, pad.GetEffectiveHoleShape())
            for item in fp.GraphicalItems():
                self.graphic(item)
            for field in _labels(fp, silk):
                self.add(('label', field.m_Uuid.AsString()), field.GetLayer(),
                         field.GetEffectiveShape(field.GetLayer()))
            for layer, courtyard in ((k.F_SilkS, k.F_CrtYd), (k.B_SilkS, k.B_CrtYd)):
                body = fp.GetCourtyard(courtyard)
                if body.OutlineCount():
                    self.add(('body', fp.m_Uuid.AsString(), layer), layer, body)
        for item in board.GetDrawings():
            self.graphic(item)
        for track in board.GetTracks():
            if track.GetClass() == 'PCB_VIA':
                for layer, mask in self.sides:
                    if track.IsOnLayer(mask):
                        self.add(('mask', track.m_Uuid.AsString(), layer), layer, track.GetEffectiveShape(mask),
                                 extra=max(0, int(track.GetSolderMaskExpansion())))

    def graphic(self, item):
        k, layer, uid = self.k, item.GetLayer(), item.m_Uuid.AsString()
        if item.GetClass() in TEXTS and not (item.IsVisible() and item.GetShownText(False).strip()):
            return
        for silk, mask in self.sides:
            if layer == silk:
                self.add(('silk', uid), silk, item.GetEffectiveShape(layer))
            elif layer in (mask, k.Edge_Cuts):
                self.add(('mask' if layer == mask else 'edge', uid, silk), silk, item.GetEffectiveShape(layer))

    def add(self, key, layer, shape, extra=0):
        box = _grown(_nm(shape.BBox()), extra)
        if box[0] > box[2] or box[1] > box[3]:
            return
        self.remove(key)
        self.rows[key] = (layer, box, shape, extra)
        for cell in self._cells(layer, box):
            self.cells.setdefault(cell, set()).add(key)

    def remove(self, key):
        row = self.rows.pop(key, None)
        if row is not None:
            for cell in self._cells(row[0], row[1]):
                self.cells[cell].discard(key)

    @staticmethod
    def _cells(layer, box):
        return [(layer, x, y) for x in range(box[0] // CELL, box[2] // CELL + 1)
                for y in range(box[1] // CELL, box[3] // CELL + 1)]

    def near(self, layer, box, skip):
        """Keys of the rows on this layer whose bounds overlap ``box``, less the label's own rows."""
        found = set()
        for cell in self._cells(layer, box):
            for key in self.cells.get(cell, ()):
                if key not in skip and key not in found:
                    other = self.rows[key][1]
                    if other[0] < box[2] and box[0] < other[2] and other[1] < box[3] and box[1] < other[3]:
                        found.add(key)
        return found

    def clear(self, label, x, y, angle, margin, spacing=0):
        """Whether the label at this pose is inside the board and keeps its distance from everything.

        ``margin`` from every row (beyond what the row itself needs, such as a pad's mask
        expansion) and ``spacing`` more from other labels. Bounds select the rows near
        the label; the native shapes decide.
        """
        k = self.k
        left, top, right, bottom = label.frame(angle)
        box = [x + left - margin, y + top - margin, x + right + margin, y + bottom + margin]
        near = self.near(label.layer, _grown(box, spacing), (label.key, ('body', label.owner, label.layer)))
        if near:
            label.pose(x, y, angle)
            shape = label.shape()
            for key in near:
                row = self.rows[key]
                if shape.Collide(row[2], margin + row[3] + (spacing if key[0] == 'label' else 0)):
                    return False
        return self.outline is None or all(
            self.outline.Contains(k.VECTOR2I(px, py))
            for px, py in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])))


class _Label:
    """One label and the poses it can take."""

    def __init__(self, k, fp, field):
        self.k, self.fp, self.field = k, fp, field
        self.uuid = field.m_Uuid.AsString()
        self.owner = fp.m_Uuid.AsString()
        self.key = ('label', self.uuid)
        self.layer = field.GetLayer()
        at = field.GetPosition()
        self.start = (int(at.x), int(at.y), field.GetTextAngle().AsDegrees())
        self.frames = {}
        self.pen = max(1, int(field.GetEffectiveTextPenWidth()))
        left, top, right, bottom = self.frame(0)
        self.unit = max(1, min(right - left, bottom - top))          # the height of the ink
        self.pose(*self.start)

    def pose(self, x, y, angle):
        if self.field.GetTextAngle().AsDegrees() != angle:
            self.field.SetTextAngle(self.k.EDA_ANGLE(float(angle), self.k.DEGREES_T))
        self.field.SetPosition(self.k.VECTOR2I(int(x), int(y)))

    def shape(self):
        return self.field.GetEffectiveShape(self.layer)

    def frame(self, angle):
        """The ink bounds at this angle, relative to the text position."""
        if angle not in self.frames:
            x, y, _ = self.start
            self.pose(x, y, angle)
            box = _nm(self.shape().BBox())
            self.frames[angle] = (box[0] - x, box[1] - y, box[2] - x, box[3] - y)
        return self.frames[angle]

    def at(self, cx, cy, angle):
        """The text position that puts the centre of the ink at (cx, cy)."""
        left, top, right, bottom = self.frame(angle)
        return int(cx) - (left + right) // 2, int(cy) - (top + bottom) // 2

    def body(self):
        """The part's own extent on the label's side: courtyard, fabrication and silk drawings, pads."""
        k = self.k
        front = self.layer == k.F_SilkS
        layers = (k.F_CrtYd, k.F_Fab, k.F_SilkS) if front else (k.B_CrtYd, k.B_Fab, k.B_SilkS)
        boxes = [_nm(item.GetBoundingBox()) for item in self.fp.GraphicalItems()
                 if item.GetClass() not in TEXTS and item.GetLayer() in layers]
        boxes += [_nm(pad.GetBoundingBox()) for pad in self.fp.Pads()]
        if not boxes:
            at = self.fp.GetPosition()
            return [int(at.x), int(at.y), int(at.x), int(at.y)]
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _spread(low, high, step):
    """Positions from the middle of [low, high] outwards, ``step`` apart."""
    middle = (low + high) // 2
    out, offset = [middle], step
    while middle - offset >= low or middle + offset <= high:
        out.extend(v for v in (middle - offset, middle + offset) if low <= v <= high)
        offset += step
    return out


def places(label):
    """Candidate poses of a label, best first: (cost, where, x, y, angle).

    Beside the part comes first: against each of its four sides, the centre of the
    label within that side, at up to one label height from the part, reading left to
    right (0 degrees) or bottom to top (90). Nearer to the part and nearer to the middle
    of the side is better; a label along the side it sits on is preferred to one across
    it, a side the label fits on to one it overhangs, and 0 to 90 degrees. Places inside
    the part's own outline (as on a DIP or a large capacitor) come after all of these.
    The order is a preference only: `_Scene.clear` and then native DRC decide.
    """
    x0, y0, x1, y1 = label.body()
    middle = ((x0 + x1) // 2, (y0 + y1) // 2)
    unit = label.unit
    step = max(unit // 4, 1)
    rows = []
    for angle in (0, 90):
        left, top, right, bottom = label.frame(angle)
        w, h = right - left, bottom - top
        turned = unit // 8 if angle else 0
        for gap in range(0, unit + 1, step):
            # where, the fixed centre coordinate, the length of that side, the label's length along it
            sides = (('above', y0 - gap - h // 2, x1 - x0, w), ('below', y1 + gap + h // 2, x1 - x0, w),
                     ('left', x0 - gap - w // 2, y1 - y0, h), ('right', x1 + gap + w // 2, y1 - y0, h))
            for where, fixed, side, extent in sides:
                horizontal = where in ('above', 'below')
                across = unit // 2 if horizontal != (angle == 0) else 0
                base = 2 * gap + turned + across + max(0, extent - side) // 2
                for value in _spread(x0, x1, step) if horizontal else _spread(y0, y1, step):
                    centre = (value, fixed) if horizontal else (fixed, value)
                    slide = abs(value - middle[0 if horizontal else 1])
                    rows.append((base + slide, where, *label.at(*centre, angle), angle))
        if w <= x1 - x0 and h <= y1 - y0:
            pitch = max(step, min(x1 - x0, y1 - y0) // 24)
            for cx in _spread(x0 + w // 2, x1 - (w - w // 2), pitch):
                for cy in _spread(y0 + h // 2, y1 - (h - h // 2), pitch):
                    cost = INSIDE + turned + abs(cx - middle[0]) + abs(cy - middle[1])
                    rows.append((cost, 'inside', *label.at(cx, cy, angle), angle))
    return sorted(rows)


def _find(scene, label, tiers, avoid=()):
    """The best clear place under the first (margin, spacing) tier that has one, else None."""
    rows = [row for row in places(label) if row[2:] not in avoid]
    for margin, spacing in tiers:
        for row in rows:
            if scene.clear(label, row[2], row[3], row[4], margin, spacing):
                return row
    return None


# ---------------------------------------------------------------- the step

def _named(report, labels):
    """The native silkscreen findings that name each label: UUID -> list of findings.

    A finding names an item by UUID. ``silk_artwork.relocate`` records that KiCad 10 can
    leave the UUID of a custom field out of a silkscreen finding, so an item described as
    ``<Name> field of <reference>`` also names a label when exactly one label matches.
    """
    by_name = {}
    for uuid, (fp, field) in labels.items():
        by_name.setdefault(f'{field.GetName()} field of {fp.GetReference()}', []).append(uuid)
    named = {}
    for finding in report.get('violations', []):
        if finding.get('type') not in FINDINGS:
            continue
        for item in finding.get('items', []):
            uuid = item.get('uuid')
            if uuid not in labels:
                matches = by_name.get(item.get('description', ''), [])
                uuid = matches[0] if len(matches) == 1 else None
            if uuid is not None and not any(finding is other for other in named.setdefault(uuid, [])):
                named[uuid].append(finding)
    return named


def _by_type(named):
    """Findings that name at least one label, counted once each, by type."""
    seen, counts = [], {}
    for findings in named.values():
        for finding in findings:
            if not any(finding is other for other in seen):
                seen.append(finding)
                counts[finding['type']] = counts.get(finding['type'], 0) + 1
    return dict(sorted(counts.items()))


def settle(board_path, report, moved, *, drc, restore, tick=None):
    """Move the labels of moved footprints that native DRC reports to free places beside their parts.

    ``board_path`` is the placed board the job judges and ``report`` its native DRC
    report; ``moved`` holds the UUIDs of the footprints the placement moved. After a
    round that moved a label the board is saved, ``restore()`` puts the job's frozen
    project and rule files back beside it (a native save rewrites them) and ``drc()``
    runs the job's own native DRC again and returns ``(report, execution)``. A label
    that DRC still names is searched again, away from the place DRC rejected and with a
    wider margin, at most ``ROUNDS`` times. ``tick()`` is called before each label so the
    caller can stop at its deadline.

    A place is first sought with room to spare: the label's own stroke width from
    everything, half its height from other labels, so that two labels do not read as
    one. Without such a place the board's silkscreen clearance alone decides (plus the
    board's curve approximation error). Other parts' courtyards are never used: a
    label there is covered by that part.

    Returns ``(report, execution, record)``: the report and execution of the last DRC
    run (``execution`` is None when no label moved and the given report stands), and the
    record of the step, which names every moved label and every label left in a finding.
    """
    moved = set(moved)
    record = {'schema': SCHEMA, 'status': 'not_needed', 'rounds': 0, 'named': 0, 'moved': [], 'unplaced': [],
              'findings': {'before': {}, 'after': {}}}
    execution = None
    if not moved or not any(finding.get('type') in FINDINGS for finding in report.get('violations', [])):
        return report, execution, record
    import pcbnew as k
    silk = (k.F_SilkS, k.B_SilkS)
    rejected, reasons, placed = {}, {}, {}
    for number in range(ROUNDS + 1):
        board = k.LoadBoard(str(board_path))
        labels = {field.m_Uuid.AsString(): (fp, field) for fp in board.GetFootprints()
                  if fp.m_Uuid.AsString() in moved for field in _labels(fp, silk)}
        named = _named(report, labels)
        if number == 0:
            record['named'] = len(named)
            record['findings']['before'] = _by_type(named)
        pending = [uuid for uuid in named if uuid not in reasons]
        if not pending or number == ROUNDS:
            break
        scene = _Scene(board)
        base = scene.clearance + scene.tolerance
        changed = False
        # A label in more findings sits in a worse place: moving it first can free its neighbours.
        for uuid in sorted(pending, key=lambda u: (-len(named[u]), labels[u][0].GetReference(),
                                                   labels[u][1].GetName(), u)):
            if tick is not None:
                tick()
            fp, field = labels[uuid]
            label = _Label(k, fp, field)
            if number == 0 and scene.clear(label, *label.start, base):
                continue                    # what it overlapped has moved away: the label stays
            wider = number * label.pen
            roomy = max(base, label.pen) + wider
            row = _find(scene, label, ((roomy, label.unit // 2), (roomy, 0), (base + wider, 0)),
                        rejected.get(uuid, ()))
            if row is None:
                label.pose(*label.start)
                reasons[uuid] = 'no free place beside or inside its part'
                continue
            _, where, x, y, angle = row
            label.pose(x, y, angle)
            scene.add(label.key, label.layer, label.shape())
            rejected.setdefault(uuid, []).append((x, y, angle))
            start = placed.get(uuid, {}).get('from', {'x_nm': label.start[0], 'y_nm': label.start[1],
                                                      'angle_deg': label.start[2]})
            placed[uuid] = {'reference': fp.GetReference(), 'field': field.GetName(), 'uuid': uuid, 'place': where,
                            'from': start, 'to': {'x_nm': x, 'y_nm': y, 'angle_deg': float(angle)}}
            changed = True
        if not changed:
            break
        if not k.SaveBoard(str(board_path), board):
            raise ValueError('native SaveBoard failed after label placement')
        restore()
        report, execution = drc()
        record['rounds'] = number + 1
    record['moved'] = sorted(placed.values(), key=lambda row: (row['reference'], row['field'], row['uuid']))
    record['unplaced'] = sorted(
        ({'reference': labels[uuid][0].GetReference(), 'field': labels[uuid][1].GetName(), 'uuid': uuid,
          'findings': len(findings),
          'reason': reasons.get(uuid, f'native DRC still reports it after {record["rounds"]} round(s)')}
         for uuid, findings in named.items()), key=lambda row: (row['reference'], row['field'], row['uuid']))
    record['findings']['after'] = _by_type(named)
    record['status'] = 'incomplete' if record['unplaced'] else 'placed' if record['moved'] else 'not_needed'
    return report, execution, record
