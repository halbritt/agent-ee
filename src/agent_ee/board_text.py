"""Declared board labels, native emission and final saved-board verification.

Coordinates are explicit design choices. Footprint-owned graphics follow the
native rigid transform; the artwork mover has no authority to change them.
"""
import argparse
import json
import math
import re
import uuid
from pathlib import Path

from .storage import digest, read_json, write_json
from .symbol_library import nanometres as _nanometres

SCHEMA = 'agent-ee.board-text/1'
DATA = Path(__file__).parent / 'data'
KEYS = ('min_silk_text_height_mm', 'min_silk_line_width_mm', 'min_pad_to_silk_mm', 'min_silk_to_mask_mm')
PLACEMENT_FIELD = 'agent_ee_board_text_placement'
LITERAL_CONSTRUCTS = ('${', '@{', '~{', '_{', '^{')


def nanometres(value):
    return _nanometres(str(value))


def default_limits():
    return {r['key']: r['value'] for r in read_json(DATA / 'board-text-fab-profile.json')['limits']}


def validate_items(items, references=None, pins=None):
    """Validate the statement field without native tools or captured vendor data."""
    if not isinstance(items, list) or len(items) > 1024:
        raise ValueError('markings: supply a list of at most 1024 labels')
    seen = set()
    for i, row in enumerate(items):
        path = f'markings[{i}]'
        def fail(message):
            raise ValueError(path + ': ' + message)
        if (not isinstance(row, dict) or not {'id', 'text', 'layer', 'anchor'} <= row.keys()
                or row.keys() - {'id', 'text', 'layer', 'anchor', 'rotation_deg', 'height_mm', 'stroke_mm'}):
            fail('use id, text, layer, anchor and optional rotation_deg, height_mm, stroke_mm')
        ident = row['id']
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', ident) or ident in seen:
            fail('id must be a unique short identifier')
        seen.add(ident)
        text = row['text']
        if (not isinstance(text, str) or not text.strip() or len(text) > 256
                or any(not c.isprintable() for c in text) or '\\' in text
                or any(token in text for token in LITERAL_CONSTRUCTS)):
            fail('text must be 1–256 printable literal characters without KiCad variables, expressions or markup')
        if row['layer'] not in ('F.SilkS', 'B.SilkS'):
            fail('layer must be F.SilkS or B.SilkS')
        anchor = row['anchor']
        if not isinstance(anchor, dict):
            fail('anchor must be an object')
        if set(anchor) == {'at_mm'}:
            coords = anchor['at_mm']
        elif {'reference', 'offset_mm'} <= anchor.keys() and not anchor.keys() - {'reference', 'pin', 'offset_mm'}:
            ref = anchor['reference']
            if not isinstance(ref, str) or not ref or (references is not None and ref not in references):
                fail('anchor.reference must name a declared part')
            if 'pin' in anchor:
                pin = anchor['pin']
                if not isinstance(pin, str) or not pin or (pins is not None and pin not in pins.get(ref, {})):
                    fail('anchor.pin must name a captured physical pin')
            coords = anchor['offset_mm']
        else:
            fail('anchor needs at_mm, or reference and offset_mm with optional pin')
        if not isinstance(coords, list) or len(coords) != 2:
            fail('anchor coordinates must be [x, y] in mm')
        for value in [*coords, *(row[k] for k in ('rotation_deg', 'height_mm', 'stroke_mm') if k in row)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                fail('coordinates, angle and sizes must be finite numbers')
        try:
            for value in coords:
                if abs(nanometres(value)) > 2_000_000_000:
                    fail('coordinate exceeds the native range')
            for key in ('height_mm', 'stroke_mm'):
                if key in row and not 0 < nanometres(row[key]) <= 2_000_000_000:
                    fail(key + ' must be positive and within the native range')
        except (ValueError, TypeError) as exc:
            fail(str(exc))
        if row.get('rotation_deg', 0) % 90:
            fail('rotation_deg must be a multiple of 90')
    return items


def label_uuid(namespace, ident):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'agent-ee-board-text/' + namespace + '/' + ident))


def load_document(path):
    doc = read_json(path)
    if (not isinstance(doc, dict) or set(doc) != {'schema', 'namespace', 'origin_mm', 'items', 'fab_profile'}
            or doc['schema'] != SCHEMA or not isinstance(doc['namespace'], str) or not doc['namespace']
            or not isinstance(doc['origin_mm'], list) or len(doc['origin_mm']) != 2):
        raise ValueError('expected agent-ee.board-text/1')
    for value in doc['origin_mm']:
        nanometres(value)
    validate_items(doc['items'])
    return doc


def design_rules(root, doc, files):
    """Adopt verified published minima for generated text, without a fab grade."""
    from .fab_profile import compile_profile, verify_source, silk_fact, FACT_ROLES
    from .routing_rules import captured_file
    path = captured_file(root, files, doc['fab_profile'])
    compiled = compile_profile(read_json(path), files, root)
    records = [c['expected'] for c in compiled['constraints']]
    if {r.get('key') for r in records} != set(KEYS) or len(records) != len(KEYS):
        raise ValueError('board text needs all four sourced legend minima')
    for record in records:
        verify_source(record['source'], root, files, compiled['as_of'])
        problem = silk_fact(record['source'], record['key'], record['value'], FACT_ROLES['limit'], root, files)
        if record['tier'] != 'minimum' or problem:
            raise ValueError('board text needs sourced minima: ' + (problem[1] if problem else 'minimum tier required'))
    return dict(limits={r['key']: r['value'] for r in records},
                immutable_inputs=sorted({doc['fab_profile'], *compiled['immutable_inputs']}),
                basis=dict(kind='generator_design_rule', scope='declared_labels_only',
                    description='Published JLCPCB legend minima adopted as generated-text design rules.',
                    qualified_fabrication_profile_grade=False, name=compiled['name'],
                    as_of=compiled['as_of'], limits=records),
                advisories=[
                    dict(code='supplier_applicability_not_qualified',
                         description='Supplier scope is recorded; this check does not qualify applicability.',
                         recorded=compiled.get('applicability', [])),
                    dict(code='fabrication_order_not_qualified',
                         description='This check does not establish a verified fabrication order.',
                         recorded=compiled.get('order', [])),
                    dict(code='published_supplier_advisories',
                         description='Published supplier recommendations retained as advisories.',
                         recorded=compiled.get('advisories', []))])


def profile_limits(root, doc, files):
    rules = design_rules(root, doc, files)
    return rules['limits'], rules['immutable_inputs']


def _rotate(x, y, angle):
    theta = math.radians(angle)
    return round(x * math.cos(theta) + y * math.sin(theta)), round(-x * math.sin(theta) + y * math.cos(theta))


def _pose(board, doc, row):
    import pcbnew as k
    anchor = row['anchor']
    flipped = False
    angle = row.get('rotation_deg', 0)
    if 'at_mm' in anchor:
        owner = None
        at = [nanometres(a) + nanometres(b) for a, b in zip(doc['origin_mm'], anchor['at_mm'])]
    else:
        matches = [fp for fp in board.GetFootprints() if fp.GetReference() == anchor['reference']]
        if len(matches) != 1:
            raise ValueError('board text owner absent or ambiguous: ' + anchor['reference'])
        owner = matches[0]
        flipped = owner.IsFlipped()
        base = owner.GetPosition()
        if 'pin' in anchor:
            pads = [p for p in owner.Pads() if p.GetNumber() == anchor['pin']]
            if len(pads) != 1:
                raise ValueError('board text pin must select exactly one physical pad: ' + anchor['reference'] + '.' + anchor['pin'])
            base = pads[0].GetPosition()
        x, y = [nanometres(v) for v in anchor['offset_mm']]
        dx, dy = _rotate(x, -y if flipped else y, owner.GetOrientationDegrees())
        at = [base.x + dx, base.y + dy]
        # KiCad's footprint flip mirrors local y and gives mirrored text a
        # half-turn, unlike the pad/shape angle convention.
        angle = owner.GetOrientationDegrees() + (180 - angle if flipped else angle)
    back = (row['layer'] == 'B.SilkS') != flipped
    return owner, k.VECTOR2I(*at), angle % 360, k.B_SilkS if back else k.F_SilkS, back


def emit(board, doc, limits):
    """Add real visible silk text. Native footprint ownership carries attached labels."""
    import pcbnew as k
    labels = {}
    owners = sorted(board.GetFootprints(), key=lambda fp: fp.m_Uuid.AsString())
    for row in doc['items']:
        owner, at, angle, layer, mirror = _pose(board, doc, row)
        text = k.PCB_TEXT(owner or board)
        text.SetKeepUpright(False)
        text.SetHorizJustify(k.GR_TEXT_H_ALIGN_CENTER)
        text.SetVertJustify(k.GR_TEXT_V_ALIGN_CENTER)
        text.SetText(row['text'])
        text.SetLayer(layer)
        text.SetPosition(at)
        text.SetTextAngle(k.EDA_ANGLE(angle, k.DEGREES_T))
        text.SetMirrored(mirror)
        height = nanometres(row.get('height_mm', limits[KEYS[0]]))
        text.SetTextSize(k.VECTOR2I(height, height))
        text.SetTextThickness(nanometres(row.get('stroke_mm', limits[KEYS[1]])))
        text.SetUuidDirect(k.KIID(label_uuid(doc['namespace'], row['id'])))
        (owner or board).Add(text)
        holder = owner or (owners[0] if owners else None)
        if holder is None:
            raise ValueError('board text placement metadata needs a physical part')
        entry = labels.setdefault(holder.m_Uuid.AsString(), (holder, dict(free=[], attached=[])))
        entry[1]['attached' if owner else 'free'].append(text.m_Uuid.AsString())
    for holder, record in labels.values():
        if holder.HasField(PLACEMENT_FIELD):
            raise ValueError('board text placement metadata already exists')
        holder.SetField(PLACEMENT_FIELD, json.dumps(record, sort_keys=True))
        field = next(f for f in holder.GetFields() if f.GetName() == PLACEMENT_FIELD)
        field.SetVisible(False)
        field.SetLayer(k.F_Fab)
        field.SetUuidDirect(k.KIID(str(uuid.uuid5(uuid.UUID(holder.m_Uuid.AsString()), PLACEMENT_FIELD))))


def placement_regions(board):
    """Native label bounds cited by immutable footprint properties.

    Free text becomes fixed-body input to the existing engine. Attached text
    extends its owner's occupied region. Neither adds a fictitious PCB part.
    """
    import pcbnew as k
    drawings = {i.m_Uuid.AsString(): i for i in board.GetDrawings()}
    free, attached, seen = [], {}, set()
    for fp in board.GetFootprints():
        fields = [f for f in fp.GetFields() if f.GetName() == PLACEMENT_FIELD]
        if not fields:
            continue
        if len(fields) != 1:
            raise ValueError('ambiguous board text placement metadata')
        record = json.loads(fields[0].GetText())
        if (not isinstance(record, dict) or set(record) != {'free', 'attached'} or
                any(not isinstance(record[key], list) for key in record)):
            raise ValueError('malformed board text placement metadata')
        owned = {i.m_Uuid.AsString(): i for i in fp.GraphicalItems()}
        for kind in ('free', 'attached'):
            for uid in record[kind]:
                item = (drawings if kind == 'free' else owned).get(uid)
                if (uid in seen or item is None or item.GetClass() != 'PCB_TEXT' or not item.IsVisible()
                        or item.GetLayer() not in (k.F_SilkS, k.B_SilkS)):
                    raise ValueError('missing, hidden or ambiguous declared board text: ' + str(uid))
                seen.add(uid)
                box = item.GetBoundingBox()
                bounds = [box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()]
                if kind == 'attached':
                    attached.setdefault(fp.m_Uuid.AsString(), []).append(bounds)
                else:
                    at = item.GetPosition()
                    free.append(dict(uuid=uid, native_ref='board-text:' + uid,
                        role='fixed_body', pose=dict(x_nm=at.x, y_nm=at.y, quarter_turns=0),
                        occupancy=dict(status='derived', label='derived_occupancy', rung='silk+pads',
                            local_rect_nm=[bounds[0]-at.x, bounds[1]-at.y, bounds[2]-at.x, bounds[3]-at.y]),
                        copper_local_nm=[], mask_local_nm=[], holes_local_nm=[], pads=[], rotations=[0]))
    return sorted(free, key=lambda r: r['uuid']), attached


def _conflicts(text, items, pad_clearance):
    """Native stroke shapes decide collisions; boxes only nominate candidates.

    A pad-to-legend minimum does not impose a silk-to-silk spacing rule. Mask
    expansion additionally forbids printing inside the pad's actual opening.
    """
    import pcbnew as k
    from .silk_artwork import _obstacle_boxes
    shape = text.GetEffectiveShape()
    box = text.GetBoundingBox()
    box.Inflate(pad_clearance)
    copper, mask = (k.F_Cu, k.F_Mask) if text.GetLayer() == k.F_SilkS else (k.B_Cu, k.B_Mask)
    conflicts = []
    for item in items:
        if not any(box.Intersects(b) for b in _obstacle_boxes(text, [item])):
            continue
        if item.GetClass() == 'PAD':
            overlaps = ((item.IsOnLayer(copper) and shape.Collide(item.GetEffectiveShape(copper), pad_clearance)) or
                        (item.IsOnLayer(mask) and shape.Collide(item.GetEffectiveShape(copper),
                                                               max(0, item.GetSolderMaskExpansion(mask)))))
        else:
            overlaps = shape.Collide(item.GetEffectiveShape(), 0)
        if overlaps:
            conflicts.append(dict(uuid=item.m_Uuid.AsString(), native_class=item.GetClass()))
    return conflicts


def _mask_contours(board, layer):
    """Non-pad apertures, conservatively bounded to one micrometre.

    Track conversion resolves exposed tracks; vias need their margin explicitly.
    Native layer membership excludes tented vias. These are generator geometry
    rules, not a qualification of the supplier's mask process.
    """
    import pcbnew as k
    poly = k.SHAPE_POLY_SET()
    for track in board.GetTracks():
        if track.IsOnLayer(layer):
            margin = track.GetSolderMaskExpansion() if track.GetClass() == 'PCB_VIA' else 0
            track.TransformShapeToPolygon(poly, layer, margin, 1000, k.ERROR_OUTSIDE)
    graphics = list(board.GetDrawings())
    zones = list(board.Zones())
    for fp in board.GetFootprints():
        graphics.extend([*fp.GraphicalItems(), *fp.GetFields()])
        zones.extend(fp.Zones())
    for item in graphics:
        if not item.IsOnLayer(layer):
            continue
        kind = item.GetClass()
        if kind in ('PCB_TEXT', 'PCB_FIELD'):
            if item.IsVisible():
                item.TransformTextToPolySet(poly, 0, 1000, k.ERROR_OUTSIDE)
        elif kind == 'PCB_TEXTBOX':
            if item.IsBorderEnabled():
                item.TransformShapeToPolygon(poly, layer, 0, 1000, k.ERROR_OUTSIDE)
            item.TransformTextToPolySet(poly, 0, 1000, k.ERROR_OUTSIDE)
        elif kind == 'PCB_SHAPE':
            margin = item.GetSolderMaskExpansion() if item.GetLayer() != layer else 0
            item.TransformShapeToPolySet(poly, layer, margin, 1000, k.ERROR_OUTSIDE)
        else:
            raise ValueError('unsupported mask graphic for generated text: ' + kind)
    for zone in zones:
        if zone.IsOnLayer(layer) and not zone.GetIsRuleArea():
            zone.TransformSolidAreasShapesToPolygon(layer, poly)
    poly.Simplify()
    return poly


def _pad_mask_conflicts(text, board, layer, clearance):
    """Use native pad shapes so polygon collision rounding cannot hide a gap.

    Positive margins expand the clearance around the original shape. Negative
    margins follow KiCad's TransformPadsToPolySet size reduction for ordinary
    pads. Custom pad erosion needs a separate geometric qualification.
    """
    import pcbnew as k
    copper = k.F_Cu if layer == k.F_Mask else k.B_Cu
    ink = text.GetEffectiveShape()
    conflicts = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if not pad.FlashLayer(layer):
                continue
            margin = pad.GetSolderMaskExpansion(layer)
            if margin < 0:
                if pad.GetShape(layer) == k.PAD_SHAPE_CUSTOM:
                    raise ValueError('generated text cannot qualify a custom pad with negative mask expansion')
                size = pad.GetSize(layer) + k.VECTOR2I(2 * margin, 2 * margin)
                if size.x <= 0 or size.y <= 0:
                    continue
                aperture = k.PAD(pad)
                aperture.SetSize(layer, size)
                shape, distance = aperture.GetEffectiveShape(copper), clearance
            else:
                shape, distance = pad.GetEffectiveShape(copper), clearance + margin
            if ink.Collide(shape, distance):
                conflicts.append(pad.m_Uuid.AsString())
    return conflicts


def evaluate(board, doc, limits):
    """Labels must fit the board, retain their pose and clear native silk/pad shapes.

    Full text bounds establish outline containment. Native stroke geometry
    distinguishes a hollow silk frame from filled copper or intersecting ink.
    """
    import pcbnew as k
    from .silk_artwork import contained_markings
    items = list(board.GetDrawings())
    for fp in board.GetFootprints():
        items.extend([*fp.GraphicalItems(), *fp.GetFields(), *fp.Pads()])
    by_id = {}
    for item in items:
        by_id.setdefault(item.m_Uuid.AsString(), []).append(item)
    contained = set(contained_markings(board))
    records, masks = [], {}
    for row in doc['items']:
        uid = label_uuid(doc['namespace'], row['id'])
        defects = []
        matches = by_id.get(uid, [])
        record = dict(id=row['id'], uuid=uid, violations=defects)
        records.append(record)
        if len(matches) != 1 or matches[0].GetClass() != 'PCB_TEXT':
            defects.append('text_missing_or_ambiguous')
            continue
        text = matches[0]
        owner, at, angle, layer, mirror = _pose(board, doc, row)
        parent = text.GetParentFootprint()
        if (parent.m_Uuid.AsString() if parent else None) != (owner.m_Uuid.AsString() if owner else None):
            defects.append('attachment_changed')
        if text.GetShownText(False) != row['text'] or not text.IsVisible():
            defects.append('content_or_visibility_changed')
        if (text.GetLayer() != layer or text.IsMirrored() != mirror or
                text.GetTextAngle().AsDegrees() % 360 != angle or text.GetPosition() != at or text.IsKeepUpright()):
            defects.append('declared_pose_changed')
        if (text.GetTextHeight() < nanometres(limits[KEYS[0]]) or
                text.GetTextThickness() < nanometres(limits[KEYS[1]])):
            defects.append('text_below_fabrication_minimum')
        height = nanometres(row.get('height_mm', limits[KEYS[0]]))
        stroke = nanometres(row.get('stroke_mm', limits[KEYS[1]]))
        if text.GetTextSize() != k.VECTOR2I(height, height) or text.GetTextThickness() != stroke or text.GetFont() is not None:
            defects.append('declared_font_or_size_changed')
        if uid not in contained:
            defects.append('text_outside_board')
        conflicts = _conflicts(text, items, nanometres(limits[KEYS[2]]))
        if conflicts:
            defects.append('silk_overlap_or_pad_clearance')
        record['conflicts'] = conflicts
        mask = k.F_Mask if layer == k.F_SilkS else k.B_Mask
        if mask not in masks:
            masks[mask] = _mask_contours(board, mask)
        mask_minimum = nanometres(limits[KEYS[3]])
        pad_mask_conflicts = _pad_mask_conflicts(text, board, mask, mask_minimum)
        contour_conflict = bool(text.GetEffectiveShape().Collide(masks[mask], mask_minimum))
        mask_conflict = bool(pad_mask_conflicts) or contour_conflict
        if mask_conflict:
            defects.append('silk_to_mask_clearance')
        record['mask_clearance'] = dict(layer=board.GetLayerName(mask),
            minimum_nm=mask_minimum, outcome='defect' if mask_conflict else 'pass',
            pad_conflicts=pad_mask_conflicts, non_pad_contour_conflict=contour_conflict,
            non_pad_contour_error_nm=1000, non_pad_contour_bound='outside')
        covered = []
        for fp in board.GetFootprints():
            if ((owner is not None and fp.m_Uuid.AsString() == owner.m_Uuid.AsString())
                    or fp.IsFlipped() != (layer == k.B_SilkS)):
                continue
            court = fp.GetCourtyard(k.B_CrtYd if fp.IsFlipped() else k.F_CrtYd)
            body = court.BBox() if court.OutlineCount() else fp.GetBoundingBox(False, False)
            if text.GetBoundingBox().Intersects(body):
                covered.append(fp.GetReference())
        if covered:
            defects.append('label_covered_by_part')
        record['covering_parts'] = covered
        record.update(text=text.GetText(), layer=board.GetLayerName(text.GetLayer()),
                      position_nm=[text.GetPosition().x, text.GetPosition().y],
                      expected_position_nm=[at.x, at.y], rotation_deg=text.GetTextAngleDegrees(),
                      expected_rotation_deg=angle, height_nm=text.GetTextHeight(),
                      stroke_nm=text.GetTextThickness())
    return dict(schema='agent-ee.board-text-result/1', records=records,
                outcome='defect' if any(r['violations'] for r in records) else 'pass')


def step(source_root, board, markings):
    from .tool_source import snapshot
    return dict(schema='agent-ee.stage-step/1', stage='schematic', step='board-text',
                argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-m',
                      'agent_ee.board_text', 'emit', '--board', '/candidate/' + board,
                      '--declaration', '/candidate/' + markings, '--inputs', '/candidate',
                      '--out', '/work/board.kicad_pcb'],
                mounts=[dict(source=str(snapshot(Path(source_root) / 'src')), at='/tool/agent-ee')],
                source='/tool/agent-ee', inputs=[markings], writes={board: 'board.kicad_pcb'}, timeout_s=300)


def describe(ctx):
    from .routing_rules import captured_file
    if set(ctx.params) != {'markings'}:
        raise ValueError('board_text requires only markings')
    path = captured_file(ctx.inputs, ctx.files, ctx.params['markings'])
    doc = load_document(path)
    _, dependencies = profile_limits(ctx.inputs, doc, ctx.files)
    ctx.apply(dict(immutable_inputs=[ctx.params['markings'], *dependencies], predicates=['board-text:' + r['id'] for r in doc['items']]))


def check(ctx):
    from .checks import CheckError
    doc = load_document(ctx.inputs / ctx.params['markings'])
    profile_limits(ctx.inputs, doc, ctx.manifest['files'])
    rec = ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.board_text', 'check',
                       '--board', ctx.candidate_path(ctx.scope['board']),
                       '--declaration', ctx.candidate_path(ctx.params['markings']),
                       '--inputs', '/candidate', '--out', '/work/board-text.json'], 300)
    if rec['exit_code'] != 0:
        raise CheckError('failed', 'native board text check failed')
    report = ctx.load_json('board-text.json', 'board text report')
    ctx.evidence('board-text.json')
    ctx.result.update(execution='ok', outcome=report['outcome'], summary='Declared board text: ' + report['outcome'])
    ctx.result['details'].update(report)
    ctx.result['limitations'].append('Native stroke geometry and conservative outline bounds. No physical print, assembled readability or supplier confirmation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('emit', 'check'))
    for name in ('board', 'declaration', 'inputs', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    import pcbnew as k
    from .requirement_map import input_inventory
    path = Path(args.declaration)
    doc = load_document(path)
    root = Path(args.inputs)
    rules = design_rules(root, doc, input_inventory(root))
    limits = rules['limits']
    board = k.LoadBoard(args.board)
    if board is None:
        raise ValueError('native board text input failed to load')
    if args.command == 'emit':
        emit(board, doc, limits)
        if not k.SaveBoard(args.out, board):
            raise ValueError('native board text save failed')
    else:
        report = evaluate(board, doc, limits)
        report.update(board_sha256=digest(args.board), declaration_sha256=digest(path),
                      basis=rules['basis'], advisories=rules['advisories'])
        write_json(args.out, report)


if __name__ == '__main__':
    main()
else:
    from .check_registry import register
    register('board_text', describe, check)
