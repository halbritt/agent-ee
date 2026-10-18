"""JLCPCB CPL rotation correction over a sealed `fab_export` package (AGENTEE-72).

`cpl_rotation` never re-runs an export. It reads the earlier `fab_export`
check's verified staging (`cpl.csv` with native KiCad anchors and angles,
`bom.csv` for each designator's LCSC binding), the earlier sealed pcbnew
`geometry` probe of the same captured board, the captured board bytes, a
captured versioned rotation policy and captured supplier footprints. It writes
a JLC CPL and a per-designator evidence report:

* Mid X/Mid Y are the centre of the union of the footprint's native
  board-space pad boxes (integer nm, `BOX2I::GetCenter` rounding), never the
  anchor. A designator without pad geometry, a duplicate designator, or a
  native anchor that disagrees with the probe gets no row, and then no CPL is
  written at all.
* The policy (`schema_version` 1) applies per-designator overrides first,
  then the first footprint-name prefix rule. Top: `(angle + delta) mod 360`;
  bottom: `(180 - (angle - delta)) mod 360`. Offsets are added in CPL axes.
  Unknown keys, duplicate rule ids or designators and non-finite or
  sub-nanometre values refuse the policy, and no CPL is produced.
* Independently, pin 1 of the board footprint (top-view library frame
  recovered from the board bytes by `land_pattern`) and of the captured
  EasyEDA footprint (`easyeda_footprint`) are classified into one of eight
  positions. The cycle difference is the supplier-to-board rotation offset; it
  must equal the policy delta. A cycle mismatch is a reflection. For `D*` and
  `LED*` designators pin-1 anode/cathode polarity is cross-checked.

Every CPL row is listed in exactly one of `applied_corrections` or
`unmatched_footprints`. Designators the export manifest lists as excluded by
its declared assembly rule (AGENTEE-122; verified by `fab_consistency`) are
populated but deliberately absent from the CPL, so they are not counted as
unlisted geometry; one that still appears as a CPL row refuses the staging. Status stays `PREVIEW_REQUIRED`: nothing here
physically validates a JLCPCB placement model.
"""

import csv
import hashlib
import io
import json
import re
import shutil
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from .easyeda_footprint import SupplierError, parse_package, symbol_pins
from .fab_export import CPL_HEADER, NUMBER_RE, REF_RE, natural_key, verify_manifest
from .land_pattern import (PatternError, _decimal_text, _json, _path, board_footprint, board_footprints,
                           validate_provenance)
from .storage import digest, read_json, write_json

REPORT = 'agent-ee.cpl-rotation/1'
POLICY_SCHEMA_VERSION = 1
PREDICATES = ['cpl-rotation:pad-centroid-midpoint', 'cpl-rotation:policy', 'cpl-rotation:pin1-frame']
PARAMS = frozenset(('export_check', 'geometry_check', 'policy', 'supplier_footprints',
                    'source_tolerance_nm'))
POLICY_KEYS = frozenset(('schema_version', 'policy_id', 'provenance', 'footprint_rules',
                         'component_overrides'))
OFFSET_KEYS = frozenset(('offset_x_mm', 'offset_y_mm'))
RULE_KEYS = frozenset(('id', 'footprint_prefix', 'rotation_degrees'))
OVERRIDE_KEYS = frozenset(('id', 'designator', 'rotation_degrees'))
BOM_COLUMNS = ['Designator', 'Footprint', 'Quantity', 'Value', 'Manufacturer', 'Partnumber', 'LCSC Part #']
MICRO = Decimal('0.000001')
NM_PER_MM = 1000000
# Pin-1 classification compares exact pad centres; no real land pattern puts
# distinct pad rows within 10 um of each other.
CLASSIFY_TOLERANCE_NM = Fraction(10000)
# Two four-cycles of counter-clockwise quarter turns in the top view (+Y up).
CYCLES = (('leftside_top', 'bottomside_left', 'rightside_bottom', 'topside_right'),
          ('leftside_bottom', 'bottomside_right', 'rightside_top', 'topside_left'))
CORNERS = {frozenset(('leftside', 'bottomside')): 'leftside_bottom',
           frozenset(('bottomside', 'rightside')): 'bottomside_right',
           frozenset(('rightside', 'topside')): 'rightside_top',
           frozenset(('topside', 'leftside')): 'topside_left'}
POLARIZED = re.compile(r'(?:D|LED)[0-9]+')
ANODE = frozenset(('a', 'anode', '+', 'pos', 'positive'))
CATHODE = frozenset(('k', 'c', 'cathode', '-', 'neg', 'negative'))
SEVERITY = {'ok': 0, 'unsupported': 1, 'failed': 2, 'malformed': 3}
# mode -> per-designator execution; defect modes keep execution ok.
MODES = {
    'duplicate_designator': 'unsupported', 'geometry_missing': 'unsupported',
    'geometry_malformed': 'malformed', 'anchor_mismatch': 'malformed', 'side_mismatch': 'malformed',
    'footprint_mismatch': 'malformed', 'binding_missing': 'unsupported', 'supplier_missing': 'unsupported',
    'pin1_unclassified': 'unsupported', 'pin1_frame_mismatch': 'unsupported', 'pad_set_mismatch': 'unsupported',
    'polarity_unknown': 'unsupported',
    'reflection': 'ok', 'rotation_conflict': 'ok', 'polarity_conflict': 'ok',
}
DEFECTS = frozenset(('reflection', 'rotation_conflict', 'polarity_conflict'))


# ------------------------------------------------------------------ parameters

def validate_params(params):
    if not isinstance(params, dict) or set(params) != PARAMS:
        raise ValueError('cpl_rotation params must be exactly {export_check, geometry_check, policy, '
                         'supplier_footprints, source_tolerance_nm}')
    for key in ('export_check', 'geometry_check'):
        if not isinstance(params[key], str) or not params[key]:
            raise ValueError(f'{key} must name an earlier check')
    _path(params['policy'], 'policy')
    paths = params['supplier_footprints']
    if not isinstance(paths, list) or len(set(map(str, paths))) != len(paths):
        raise ValueError('supplier_footprints must be a list of distinct provenance paths')
    for path in paths:
        _path(path, 'supplier provenance')
    _decimal_text(params['source_tolerance_nm'], 'source_tolerance_nm', nonnegative=True)
    return params


def _number(value, label, *, places):
    """Finite JSON number as an exact Decimal with at most `places` decimals."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or not Decimal(value).is_finite():
        raise ValueError(f'{label} must be a finite number')
    value = Decimal(value)
    try:
        exact = value.adjusted() < 6 and value == value.quantize(Decimal(1).scaleb(-places))
    except InvalidOperation:
        exact = False
    if not exact:
        raise ValueError(f'{label} {value} has more than {places} decimals or is out of range')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def load_policy(data):
    """Strict `schema_version` 1 rotation policy; any defect raises ValueError."""
    if not isinstance(data, bytes) or len(data) > 1024 * 1024:
        raise ValueError('policy must be at most 1 MiB')

    def pairs(entries):
        record = {}
        for key, value in entries:
            if key in record:
                raise ValueError(f'duplicate JSON key {key!r}')
            record[key] = value
        return record

    def constant(token):
        raise ValueError(f'non-finite number {token}')
    try:
        doc = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_float=Decimal,
                         parse_int=Decimal, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f'policy is not strict UTF-8 JSON: {exc}') from exc
    if not isinstance(doc, dict) or set(doc) != POLICY_KEYS:
        raise ValueError(f'policy keys must be exactly {sorted(POLICY_KEYS)}')
    if doc['schema_version'] != POLICY_SCHEMA_VERSION or isinstance(doc['schema_version'], bool):
        raise ValueError(f'unsupported policy schema_version {doc["schema_version"]!r}')
    policy = dict(schema_version=POLICY_SCHEMA_VERSION, policy_id=_text(doc['policy_id'], 'policy_id'),
                  provenance=_text(doc['provenance'], 'provenance'), footprint_rules=[],
                  component_overrides=[])
    ids, designators = set(), set()
    for group, required, target in (('footprint_rules', RULE_KEYS, 'footprint_prefix'),
                                    ('component_overrides', OVERRIDE_KEYS, 'designator')):
        if not isinstance(doc[group], list):
            raise ValueError(f'{group} must be a list')
        for index, rule in enumerate(doc[group]):
            label = f'{group}[{index}]'
            if not isinstance(rule, dict) or not required <= set(rule) or set(rule) - required - OFFSET_KEYS:
                raise ValueError(f'{label} keys must be {sorted(required)} plus optional {sorted(OFFSET_KEYS)}')
            rid = _text(rule['id'], f'{label} id')
            if rid in ids:
                raise ValueError(f'duplicate rule id {rid!r}')
            ids.add(rid)
            key = _text(rule[target], f'{label} {target}')
            if target == 'designator':
                if not REF_RE.fullmatch(key) or key in designators:
                    raise ValueError(f'{label} designator {key!r} is invalid or repeated')
                designators.add(key)
            rotation = _number(rule['rotation_degrees'], f'{label} rotation_degrees', places=6)
            if abs(rotation) > 360:
                raise ValueError(f'{label} rotation_degrees must lie in [-360, 360]')
            offsets = [_number(rule.get(k, 0), f'{label} {k}', places=6) for k in sorted(OFFSET_KEYS)]
            if any(abs(v) > 100 for v in offsets):
                raise ValueError(f'{label} offsets above 100 mm are refused')
            policy[group].append({'id': rid, target: key, 'rotation_degrees': rotation,
                                  'offset_x_mm': offsets[0], 'offset_y_mm': offsets[1]})
    return policy


def match_policy(policy, designator, footprint):
    """Designator override, else first footprint-prefix rule, else None."""
    for rule in policy['component_overrides']:
        if rule['designator'] == designator:
            return 'component_override', rule
    if footprint is not None:
        name = footprint.split(':', 1)[1] if ':' in footprint else footprint
        for rule in policy['footprint_rules']:
            if name.startswith(rule['footprint_prefix']):
                return 'footprint_rule', rule
    return None, None


def turn(value):
    """Angle in [0, 360) (Decimal `%` keeps the dividend's sign)."""
    value %= 360
    return value + 360 if value < 0 else value


def corrected_rotation(angle, side, delta):
    value = turn((angle + delta) if side == 'Top' else (Decimal(180) - (angle - delta))).quantize(MICRO)
    return Decimal(0).quantize(MICRO) if value == 360 else value


def mm_text(value):
    value = Decimal(value).quantize(MICRO)
    return format(value.copy_abs() if value == 0 else value, 'f')


# -------------------------------------------------------------- pin-1 frames

def classify_pin1(pads, tolerance=CLASSIFY_TOLERANCE_NM):
    """(position, frame, reason) for pin 1 among [(number, x, y)] in a +Y-up top view.

    `frame` is `sided` (pin 1 on one dominant bounding-box side; reflections
    are detectable), `corner` (pin 1 on a corner shared by two equally
    populated sides) or `linear` (a single row or column). Corner and linear
    frames always land in one cycle, so they cannot reveal a reflection.
    """
    ones = [p for p in pads if p[0] == '1']
    if len(ones) != 1:
        return None, None, f'{len(ones)} pads are numbered 1'
    if len(pads) < 2:
        return None, None, 'fewer than two pads'
    xs, ys = [p[1] for p in pads], [p[2] for p in pads]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    _, px, py = ones[0]

    def near(a, b):
        return abs(a - b) <= tolerance
    if near(x0, x1) and near(y0, y1):
        return None, None, 'all pad centres coincide'
    if near(y0, y1) or near(x0, x1):
        low, high, value = (x0, x1, px) if near(y0, y1) else (y0, y1, py)
        ends = (('leftside_top', 'rightside_bottom') if near(y0, y1)
                else ('bottomside_left', 'topside_right'))
        if near(value, low):
            return ends[0], 'linear', None
        if near(value, high):
            return ends[1], 'linear', None
        return None, None, 'pin 1 is not at an end of the pad row'
    lines = {'leftside': (px, x0, xs), 'rightside': (px, x1, xs), 'topside': (py, y1, ys),
             'bottomside': (py, y0, ys)}
    on = {side: sum(near(v, edge) for v in values)
          for side, (value, edge, values) in lines.items() if near(value, edge)}
    if not on:
        return None, None, 'pin 1 is inside the pad-centre bounding box'
    best = max(on.values())
    chosen = [side for side, count in on.items() if count == best]
    if len(chosen) == 2:
        return CORNERS[frozenset(chosen)], 'corner', None
    side = chosen[0]
    if side in ('leftside', 'rightside'):
        centre, value, ends = (y0 + y1) / 2, py, ('bottom', 'top')
    else:
        centre, value, ends = (x0 + x1) / 2, px, ('left', 'right')
    if near(value, centre):
        return None, None, f'pin 1 is centred on its {side}'
    return f'{side}_{ends[value > centre]}', 'sided', None


def pin1_offset(supplier, board):
    """Counter-clockwise supplier-to-board quarter turn, or None for a reflection."""
    for cycle in CYCLES:
        if supplier in cycle and board in cycle:
            return (cycle.index(board) - cycle.index(supplier)) % 4 * 90
    return None


def polarity(name):
    key = re.sub(r'[^a-z0-9+-]', '', (name or '').lower())
    return 'anode' if key in ANODE else 'cathode' if key in CATHODE else None


def _top_view(pattern):
    """[(number, x, y)] in exact nm with +Y up from a +Y-down land pattern."""
    return [(p['number'], Fraction(p['primitive']['center'][0]), -Fraction(p['primitive']['center'][1]))
            for p in pattern['pads']]


def _board_pin1_function(found, reference):
    from .symbol_library import children
    names = set()
    for pad in children(found[reference][0], 'pad'):
        if len(pad) > 1 and pad[1] == '1':
            functions = children(pad, 'pinfunction')
            names.add(functions[0][1] if functions and len(functions[0]) == 2 else None)
    return names.pop() if len(names) == 1 else None


# ------------------------------------------------------------- evaluation

def _midpoint(fp):
    """Native pad-box union centre in integer nm, or (None, mode, message)."""
    pads = fp.get('pads')
    if not isinstance(pads, list) or not pads:
        return None, 'geometry_missing', 'footprint has no pads; the anchor is never used as a midpoint'
    boxes = []
    for pad in pads:
        box = pad.get('bbox_nm') if isinstance(pad, dict) else None
        if box is None:
            return None, 'geometry_missing', 'sealed geometry lacks native pad boxes; rerun the geometry probe'
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or box[2] < box[0] or box[3] < box[1]):
            return None, 'geometry_malformed', f'pad box {box!r} is not [left, top, right, bottom] nm'
        boxes.append(box)
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    # BOX2I::GetCenter adds the integer half-size (non-negative, so // truncates).
    return (left + (right - left) // 2, top + (bottom - top) // 2), None, len(boxes)


def _anchor(fp):
    anchor = fp.get('position_nm')
    if not isinstance(anchor, list) or len(anchor) != 2 or any(type(v) is not int for v in anchor):
        return None
    return anchor


def _row_numbers(row):
    _, x, y, _, rot = row
    return Decimal(x), Decimal(y), Decimal(rot)


def evaluate(rows, geometry, board_bytes, bindings, policy, suppliers, tolerance_nm):
    """Per-designator midpoint, policy and pin-1 evidence; pure over captured values.

    rows: native CPL rows [designator, x, y, Top|Bottom, rotation] (text).
    geometry: sealed pcbnew probe footprint records. bindings: {ref: LCSC}.
    suppliers: {LCSC: {'raw': bytes, 'provenance': path, 'sha256': hex}}.
    """
    populated = {}
    for fp in geometry:
        if not (fp.get('dnp') or fp.get('exclude_from_pos_files')):
            populated.setdefault(fp.get('ref'), []).append(fp)
    try:
        found = board_footprints(board_bytes)
    except PatternError as exc:
        found, board_error = {}, f'{exc.mode}: {exc}'
    else:
        board_error = None
    counts = {}
    for row in rows:
        counts[row[0]] = counts.get(row[0], 0) + 1
    records, applied, unmatched = [], [], []
    for row in rows:
        ref, side = row[0], row[3]
        x, y, angle = _row_numbers(row)
        notes = []

        def mode(name, message):
            notes.append({'mode': name, 'message': message})
        fps = populated.get(ref, [])
        nodes = found.get(ref, [])
        footprint = nodes[0][1] if len(nodes) == 1 and len(nodes[0]) > 1 and isinstance(nodes[0][1], str) else None
        record = {'designator': ref, 'layer': side, 'footprint': footprint, 'lcsc': bindings.get(ref),
                  'native': {'x_mm': row[1], 'y_mm': row[2], 'rotation': row[4]},
                  'geometry': None, 'policy': None, 'cpl': None, 'pin1': None, 'polarity': None}
        midpoint = None
        if counts[ref] > 1 or len(fps) > 1 or len(nodes) > 1:
            mode('duplicate_designator', f'{ref} is not unique among CPL rows ({counts[ref]}), populated '
                                         f'probe footprints ({len(fps)}) or board footprints ({len(nodes)})')
        elif not fps:
            mode('geometry_missing', f'{ref} has no populated footprint in the sealed geometry probe')
        else:
            fp = fps[0]
            anchor = _anchor(fp)
            point, problem, detail = _midpoint(fp)
            probe_side = {'F.Cu': 'Top', 'B.Cu': 'Bottom'}.get(fp.get('layer'))
            if point is None:
                mode(problem, detail)
            elif anchor is None:
                mode('geometry_missing', 'sealed geometry lacks the exact nm anchor; rerun the geometry probe')
            elif x * NM_PER_MM != anchor[0] or -y * NM_PER_MM != anchor[1]:
                mode('anchor_mismatch', f'native CPL anchor ({row[1]}, {row[2]}) is not the probe anchor '
                                        f'{anchor} nm with Y inverted')
            elif probe_side != side:
                mode('side_mismatch', f'native CPL layer {side} differs from probe layer {fp.get("layer")}')
            elif footprint is not None and fp.get('fpid') != footprint:
                mode('footprint_mismatch', f'probe lib id {fp.get("fpid")!r} differs from board {footprint!r}')
            else:
                midpoint = point
                record['geometry'] = {
                    'algorithm': 'native_board_pad_boxes_union_center', 'pads': detail,
                    'anchor_nm': anchor, 'midpoint_nm': list(point),
                    'midpoint_mm': [mm_text(Decimal(point[0]) / NM_PER_MM),
                                    mm_text(Decimal(point[1]) / NM_PER_MM)]}

        kind, rule = match_policy(policy, ref, footprint)
        delta = rule['rotation_degrees'] if rule else Decimal(0)
        after = corrected_rotation(angle, side, delta)
        if rule:
            record['policy'] = {'match_kind': kind, 'rule_id': rule['id'],
                                'rotation_degrees': str(rule['rotation_degrees']),
                                'offset_x_mm': str(rule['offset_x_mm']), 'offset_y_mm': str(rule['offset_y_mm'])}
        if midpoint is not None:
            off_x = rule['offset_x_mm'] if rule else Decimal(0)
            off_y = rule['offset_y_mm'] if rule else Decimal(0)
            record['cpl'] = [ref, mm_text(Decimal(midpoint[0]) / NM_PER_MM + off_x),
                             mm_text(-Decimal(midpoint[1]) / NM_PER_MM + off_y), side, mm_text(after)]
        entry = {'designator': ref, 'footprint': footprint, 'layer': side,
                 'rotation_before': row[4], 'rotation_after': mm_text(after)}
        if rule:
            applied.append(dict(entry, policy_id=policy['policy_id'], rule_id=rule['id'], match_kind=kind,
                                x_after=record['cpl'][1] if record['cpl'] else None,
                                y_after=record['cpl'][2] if record['cpl'] else None))
        else:
            unmatched.append(entry)

        _pin1(record, notes, found, board_error, ref, nodes, suppliers, tolerance_nm, delta)
        for note in notes:
            note['execution'] = MODES[note['mode']]
        failures = [n for n in notes if n['mode'] not in DEFECTS]
        if any(n['mode'] in DEFECTS for n in notes):
            record.update(execution='ok', outcome='defect')
        elif failures:
            record.update(execution=max((n['execution'] for n in failures), key=SEVERITY.get), outcome='unknown')
        else:
            record.update(execution='ok', outcome='pass')
        record['modes'] = notes
        records.append(record)
    return records, applied, unmatched


def _pin1(record, notes, found, board_error, ref, nodes, suppliers, tolerance_nm, delta):
    def mode(name, message):
        notes.append({'mode': name, 'message': message})
    lcsc = record['lcsc']
    if not lcsc:
        return mode('binding_missing', f'{ref} has no LCSC binding in the export BOM')
    supplier = suppliers.get(lcsc)
    if supplier is None:
        return mode('supplier_missing', f'no captured supplier footprint for {lcsc}')
    if board_error is not None or len(nodes) != 1:
        return mode('pin1_unclassified', board_error or f'{ref} is not exactly one board footprint')
    try:
        native = board_footprint(found, ref, None)
    except PatternError as exc:
        return mode('pin1_unclassified', f'board footprint {exc.mode}: {exc}')
    try:
        package = parse_package(supplier['raw'], lcsc, tolerance_nm)
    except SupplierError as exc:
        return mode('pin1_unclassified', f'supplier footprint {exc.mode}: {exc}')
    board_pads, supplier_pads = _top_view(native['pattern']), _top_view(package['pattern'])
    pin1 = {'supplier_provenance': supplier['provenance'], 'supplier_sha256': supplier['sha256'],
            'package': package['identity']['package']}
    record['pin1'] = pin1
    if sorted(p[0] for p in board_pads) != sorted(p[0] for p in supplier_pads):
        return mode('pad_set_mismatch', 'board and supplier pad numbers differ: '
                    f'{sorted(p[0] for p in board_pads)} vs {sorted(p[0] for p in supplier_pads)}')
    board, board_frame, why_board = classify_pin1(board_pads)
    found_supplier, supplier_frame, why_supplier = classify_pin1(supplier_pads)
    pin1.update(board=board, supplier=found_supplier, frame=board_frame)
    if board is None or found_supplier is None:
        return mode('pin1_unclassified', f'board: {why_board or board}; supplier: {why_supplier or found_supplier}')
    if board_frame != supplier_frame:
        return mode('pin1_frame_mismatch', f'board pin-1 frame is {board_frame}, supplier is {supplier_frame}')
    offset = pin1_offset(found_supplier, board)
    pin1.update(offset_degrees=offset, reflection_detectable=board_frame == 'sided')
    if offset is None:
        mode('reflection', f'board pin 1 {board} and supplier pin 1 {found_supplier} differ by a reflection')
    elif Decimal(offset) != turn(delta):
        mode('rotation_conflict', f'policy rotation {delta} differs from the supplier-to-board pin-1 '
                                  f'offset {offset}')
    if POLARIZED.fullmatch(ref):
        board_name = _board_pin1_function(found, ref)
        try:
            pins = [p for p in symbol_pins(supplier['raw'], lcsc)['pins'] if p['number'] == '1']
        except SupplierError as exc:
            pins = [{'name': None, 'displayed': f'unreadable: {exc}'}]
        # One pin 1 whose displayed number agrees; anything else stays unresolved.
        supplier_name = pins[0]['name'] if len(pins) == 1 and pins[0]['displayed'] == '1' else None
        sides = {'board': polarity(board_name), 'supplier': polarity(supplier_name)}
        record['polarity'] = dict(sides, board_pin1_function=board_name, supplier_pin1_name=supplier_name)
        if None in sides.values():
            mode('polarity_unknown', f'pin-1 polarity unresolved: board {board_name!r}, supplier pin-1 '
                                     f'records {pins}')
        elif sides['board'] != sides['supplier']:
            mode('polarity_conflict', f'board pin 1 is {sides["board"]}, supplier pin 1 is {sides["supplier"]}')


def aggregate(records):
    defects = [r for r in records if r['outcome'] == 'defect']
    failing = [r for r in records if r['execution'] != 'ok' or r['outcome'] == 'unknown']
    if defects:
        return 'ok', 'defect', 'incomplete' if failing else 'complete'
    if failing:
        return max((r['execution'] for r in failing), key=SEVERITY.get), 'unknown', 'incomplete'
    return 'ok', 'pass', 'complete'


def render_cpl(rows):
    out = io.StringIO()
    out.write(CPL_HEADER + '\n')
    csv.writer(out, lineterminator='\n').writerows(rows)
    return out.getvalue()


# --------------------------------------------------------- captured inputs

def read_native_cpl(text):
    reader = csv.reader(io.StringIO(text))
    if next(reader, None) != CPL_HEADER.split(','):
        raise ValueError('export cpl.csv header is not the JLC CPL header')
    rows = []
    for line in reader:
        if len(line) != 5 or not REF_RE.fullmatch(line[0]) or line[3] not in ('Top', 'Bottom'):
            raise ValueError(f'export cpl.csv row {line} is malformed')
        if not all(NUMBER_RE.fullmatch(v) for v in (line[1], line[2], line[4])):
            raise ValueError(f'export cpl.csv row {line} has a non-decimal coordinate or rotation')
        rows.append(line)
    if not rows:
        raise ValueError('export cpl.csv has no rows')
    return rows


def read_bom_bindings(text):
    reader = csv.reader(io.StringIO(text))
    if next(reader, None) != BOM_COLUMNS:
        raise ValueError('export bom.csv header is not the JLC BOM header')
    bindings = {}
    for line in reader:
        if len(line) != len(BOM_COLUMNS):
            raise ValueError(f'export bom.csv row {line} is malformed')
        for ref in line[0].split(','):
            if ref in bindings:
                raise ValueError(f'export bom.csv lists {ref} twice')
            bindings[ref] = line[6]
    return bindings


def load_suppliers(read, paths):
    """{LCSC: raw bytes and pins} from captured provenance records; ValueError on any defect."""
    suppliers = {}
    for path in paths:
        record = _json(read(path))
        lcsc = record.get('lcsc') if isinstance(record, dict) else None
        validate_provenance(record, lcsc)
        raw = read(record['path'])
        if len(raw) != record['size_bytes'] or digest_bytes(raw) != record['sha256']:
            raise ValueError(f'{record["path"]} differs from its provenance size/sha256')
        if lcsc in suppliers:
            raise ValueError(f'two supplier footprints are captured for {lcsc}')
        suppliers[lcsc] = {'raw': raw, 'provenance': path, 'sha256': record['sha256'], 'path': record['path']}
    return suppliers


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def capability(params, files, inputs):
    """Static scope/3 contract: exact params; policy, provenance and raw supplier bytes are immutable."""
    params = validate_params(params)
    paths = [params['policy'], *params['supplier_footprints']]
    for path in paths:
        if path not in files:
            raise ValueError(f'{path} must be captured')
    if inputs is not None:
        def read(path):
            if path not in files:
                raise ValueError(f'{path} must be captured')
            return (Path(inputs) / path).read_bytes()
        load_policy(read(params['policy']))
        paths += [s['path'] for s in load_suppliers(read, params['supplier_footprints']).values()]
    return {'immutable_inputs': sorted(set(paths)), 'predicates': list(PREDICATES)}


# ---------------------------------------------------------------- the check

def check_cpl_rotation(ctx):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    try:
        params = validate_params(ctx.params)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    details = ctx.result['details']
    board_rel = ctx.scope.get('board')
    board_sha = ctx.manifest['files'].get(board_rel) if isinstance(board_rel, str) else None
    if not board_sha:
        raise CheckError('unsupported', 'scope declares no captured board')
    board_bytes = (ctx.inputs / board_rel).read_bytes()
    if digest_bytes(board_bytes) != board_sha:
        raise CheckError('malformed', 'captured board bytes differ from the candidate manifest digest')

    def read(path):
        if path not in ctx.manifest['files']:
            raise ValueError(f'{path} is not a captured input')
        data = (ctx.inputs / path).read_bytes()
        if digest_bytes(data) != ctx.manifest['files'][path]:
            raise ValueError(f'{path} differs from its captured digest')
        return data
    try:
        policy_bytes = read(params['policy'])
        policy = load_policy(policy_bytes)
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'rotation policy refused; no CPL produced: {exc}') from exc
    details['policy'] = {'path': params['policy'], 'sha256': digest_bytes(policy_bytes),
                         'policy_id': policy['policy_id'], 'schema_version': policy['schema_version'],
                         'provenance': policy['provenance'],
                         'precedence': ['component_override', 'first footprint_rule prefix', 'none']}
    try:
        suppliers = load_suppliers(read, params['supplier_footprints'])
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'supplier footprint capture refused: {exc}') from exc

    eid, gid = params['export_check'], params['geometry_check']
    manifest_path = prerequisite(ctx, eid, 'fab_export', 'fab/manifest.json')
    stage = manifest_path.parent
    try:
        manifest = read_json(manifest_path)
        verify_manifest(stage, manifest)
        if manifest.get('board_sha256') != board_sha:
            raise ValueError('export manifest is bound to another board digest')
        cpl_text = (stage / 'cpl.csv').read_text(encoding='utf-8')
        rows = read_native_cpl(cpl_text)
        claimed = manifest.get('designators', {}).get('excluded')
        if not isinstance(claimed, list) or any(not isinstance(e, dict) or not isinstance(e.get('designator'), str)
                                                for e in claimed):
            raise ValueError('manifest designators.excluded is not a list of designator records')
        excluded = sorted({e['designator'] for e in claimed}, key=natural_key)
        if any(r[0] in excluded for r in rows):
            raise ValueError(f'CPL lists designators the manifest excludes from assembly: {excluded}')
        bindings = read_bom_bindings((stage / 'bom.csv').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise CheckError('malformed', f'fab_export {eid!r} staging is unusable: {exc}') from exc
    ctx.result['evidence'] += [f'work/{eid}/fab/cpl.csv', f'work/{eid}/fab/bom.csv']
    geometry_path = prerequisite(ctx, gid, 'geometry', 'geometry.json')
    if read_json(ctx.run_dir / 'checks' / f'{gid}.json').get('details', {}).get('board_sha256') != board_sha:
        raise CheckError('unsupported', f'geometry check {gid!r} is not bound to the captured board digest')
    try:
        geometry = read_json(geometry_path)['footprints']
        if not isinstance(geometry, list) or not all(isinstance(fp, dict) for fp in geometry):
            raise ValueError('footprints are not records')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'sealed geometry {gid!r} is unreadable: {exc}') from exc

    tolerance = _decimal_text(params['source_tolerance_nm'], 'source_tolerance_nm', nonnegative=True)
    records, applied, unmatched = evaluate(rows, geometry, board_bytes, bindings, policy, suppliers, tolerance)
    listed = sorted([e['designator'] for e in applied] + [e['designator'] for e in unmatched], key=natural_key)
    if listed != sorted((r[0] for r in rows), key=natural_key):
        raise CheckError('malformed', 'internal partition error: a CPL row is not listed exactly once')
    populated = sorted({fp.get('ref') for fp in geometry
                        if not (fp.get('dnp') or fp.get('exclude_from_pos_files'))}, key=natural_key)
    unlisted = [ref for ref in populated if ref not in {r[0] for r in rows} and ref not in excluded]
    execution, outcome, coverage = aggregate(records)
    if unlisted and outcome != 'defect':
        execution, outcome, coverage = 'malformed', 'unknown', 'incomplete'

    shutil.rmtree(ctx.work / 'cpl', ignore_errors=True)
    shutil.rmtree(ctx.work / 'cpl.incomplete', ignore_errors=True)
    cpl_rel = None
    if all(r['cpl'] is not None for r in records):
        folder = 'cpl' if outcome == 'pass' else 'cpl.incomplete'
        (ctx.work / folder).mkdir()
        (ctx.work / folder / 'cpl.csv').write_text(render_cpl([r['cpl'] for r in records]))
        cpl_rel = f'{folder}/cpl.csv'
        ctx.evidence(cpl_rel)
    report = {'schema': REPORT, 'candidate_id': ctx.manifest['candidate_id'], 'status': 'PREVIEW_REQUIRED',
              'physical_validation': False, 'execution': execution, 'outcome': outcome, 'coverage': coverage,
              'board_sha256': board_sha, 'policy': details['policy'],
              'export': {'check': eid, 'manifest_sha256': digest(manifest_path),
                         'cpl_sha256': manifest['files'].get('cpl.csv')},
              'geometry': {'check': gid, 'sha256': digest(geometry_path)},
              'cpl': {'path': cpl_rel, 'sha256': digest(ctx.work / cpl_rel)} if cpl_rel else None,
              'geometry_unlisted': unlisted, 'assembly_excluded': excluded, 'designators': records,
              'applied_corrections': applied, 'unmatched_footprints': unmatched}
    write_json(ctx.work / 'cpl-rotation.json', report)
    ctx.evidence('cpl-rotation.json')
    details.update(status='PREVIEW_REQUIRED', physical_validation=False, coverage=coverage, cpl=report['cpl'],
                   applied=len(applied), unmatched=len(unmatched), rows=len(records),
                   report_sha256=digest(ctx.work / 'cpl-rotation.json'),
                   modes={r['designator']: [m['mode'] for m in r['modes']] for r in records if r['modes']})
    ctx.result.update(execution=execution, outcome=outcome)
    ctx.result['location'] = ', '.join(r['designator'] + ':' + ','.join(m['mode'] for m in r['modes'])
                                       for r in records if r['outcome'] == 'defect')
    if not cpl_rel:
        summary = 'no JLC CPL written: a designator lacks native pad geometry'
    else:
        summary = f'JLC CPL {cpl_rel} from pad-box midpoints'
    ctx.result['summary'] = (f'{summary}; policy {policy["policy_id"]}: {len(applied)} corrected, '
                             f'{len(unmatched)} unmatched; pin-1 frames checked against captured supplier '
                             f'footprints; status PREVIEW_REQUIRED')
    ctx.result['limitations'] += [
        'PREVIEW_REQUIRED: rotation is not physically validated; inspect every part in the JLCPCB '
        'Component Placements preview before ordering',
        'bottom-side rotation follows the documented 180 - (angle - delta) transform, which has not been '
        'validated against a JLCPCB bottom-side preview',
        'pin-1 frames compare pad centres only; linear and corner frames cannot reveal a mirrored footprint',
        'diode polarity is checked only for D*/LED* designators from pad pinfunction and supplier symbol '
        'pin names',
    ]
