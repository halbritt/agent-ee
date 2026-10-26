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

from .check_registry import register as _register_check

import hashlib
import json
import re
import shutil
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from .easyeda_footprint import SupplierError, parse_package, symbol_pins
from .fab_export import (REF_RE, load_verified_staging, natural_key, read_bom_records,
                         read_native_cpl as read_native_cpl, render_cpl)
from .land_pattern import (PatternError, _decimal_text, _json, _path, board_footprint, board_footprints,
                           validate_provenance)
from .storage import digest, read_json, write_json

REPORT = 'agent-ee.cpl-rotation/1'
POLICY_SCHEMA_VERSION = 1
PREDICATES = ['cpl-rotation:pad-centroid-midpoint', 'cpl-rotation:policy', 'cpl-rotation:pin1-frame']
PARAMS = frozenset(('export_check', 'geometry_check', 'policy', 'supplier_footprints',
                    'source_tolerance_nm'))
# Optional captured agent-ee.component-polarity/1 records (AGENTEE-524 D1): supplemental,
# source-bound supplier polarity for captures whose symbol names no polarity.
OPTIONAL_PARAMS = frozenset(('polarity_evidence',))
BOARD_BASES = ('board_pad_pinfunction', 'sealed_netlist_libpart')
SUPPLIER_BASES = ('supplier_symbol_pin_name', 'supplemental_component_polarity_evidence')
POLICY_KEYS = frozenset(('schema_version', 'policy_id', 'provenance', 'footprint_rules',
                         'component_overrides'))
OFFSET_KEYS = frozenset(('offset_x_mm', 'offset_y_mm'))
RULE_KEYS = frozenset(('id', 'footprint_prefix', 'rotation_degrees'))
OVERRIDE_KEYS = frozenset(('id', 'designator', 'rotation_degrees'))
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
    if not isinstance(params, dict) or not PARAMS <= set(params) <= PARAMS | OPTIONAL_PARAMS:
        raise ValueError('cpl_rotation params must be exactly {export_check, geometry_check, policy, '
                         'supplier_footprints, source_tolerance_nm} with optional polarity_evidence')
    evidence = params.get('polarity_evidence', [])
    if not isinstance(evidence, list) or not evidence or len(set(map(str, evidence))) != len(evidence):
        if 'polarity_evidence' in params:
            raise ValueError('polarity_evidence must be a nonempty list of distinct captured record paths')
    for path in evidence:
        _path(path, 'polarity evidence')
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


def read_bom_partnumbers(text):
    """{ref: Partnumber} from the export BOM, the manufacturer part each row was bound to."""
    return read_bom_records(text)[1]


def netlist_pin_facts(xml_path):
    """{ref: {lcsc, mpn, pins: {num: libpart pin name}, nets: {num: net}}} from a sealed native XML netlist.

    Only the canonical libpart pin names are read (for example `K`/`A`); node `pinfunction`
    text is cross-checked against `<name>_<num>` and never parsed on its own.
    """
    import xml.etree.ElementTree as ET
    root = ET.parse(xml_path).getroot()
    libparts = {}
    for part in root.iter('libpart'):
        pins = {}
        for pin in part.iter('pin'):
            if pin.get('num') in pins:
                raise ValueError(f'libpart {part.get("lib")}:{part.get("part")} repeats pin {pin.get("num")}')
            pins[pin.get('num')] = pin.get('name')
        libparts[(part.get('lib'), part.get('part'))] = pins
    facts = {}
    for comp in root.iter('comp'):
        ref = comp.get('ref')
        source = comp.find('libsource')
        key = (source.get('lib'), source.get('part')) if source is not None else None
        fields = {f.get('name'): (f.text or '').strip() for f in comp.findall('./fields/field')}
        if ref in facts:
            raise ValueError(f'sealed netlist repeats reference {ref}')
        facts[ref] = {'lcsc': fields.get('LCSC'), 'mpn': fields.get('MPN') or fields.get('Partnumber'),
                      'pins': dict(libparts.get(key, {})), 'nets': {}}
    for net in root.iter('net'):
        for node in net.iter('node'):
            fact = facts.get(node.get('ref'))
            if fact is None:
                continue
            num = node.get('pin')
            if num in fact['nets']:
                raise ValueError(f'sealed netlist lists {node.get("ref")} pin {num} twice')
            fact['nets'][num] = net.get('name')
            function = node.get('pinfunction')
            name = fact['pins'].get(num)
            if function is not None and name is not None and function != f'{name}_{num}':
                raise ValueError(f'sealed netlist {node.get("ref")} pin {num} function {function!r} '
                                 f'disagrees with libpart name {name!r}')
    return facts


def evaluate(rows, geometry, board_bytes, bindings, policy, suppliers, tolerance_nm, *,
             netlist=None, pad_nets=None, partnumbers=None, evidence=None):
    """Per-designator midpoint, policy and pin-1 evidence; pure over captured values.

    rows: native CPL rows [designator, x, y, Top|Bottom, rotation] (text).
    geometry: sealed pcbnew probe footprint records. bindings: {ref: LCSC}.
    suppliers: {LCSC: {'raw': bytes, 'provenance': path, 'sha256': hex}}.
    netlist: `netlist_pin_facts` of the sealed native netlist (board-side polarity fallback);
    pad_nets: {ref: {pad: net}} from the sealed probe; partnumbers: {ref: BOM Partnumber};
    evidence: `polarity_evidence.load_evidence` records by LCSC (supplier-side supplement).
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

        _pin1(record, notes, found, board_error, ref, nodes, suppliers, tolerance_nm, delta,
              netlist=(netlist or {}).get(ref), pad_nets=(pad_nets or {}).get(ref, {}),
              partnumber=(partnumbers or {}).get(ref), evidence=(evidence or {}).get(record.get('lcsc')))
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


def _pin1(record, notes, found, board_error, ref, nodes, suppliers, tolerance_nm, delta, *,
          netlist=None, pad_nets=None, partnumber=None, evidence=None):
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
        board_basis, board_note = BOARD_BASES[0], None
        if netlist is not None:
            netlist_name = netlist['pins'].get('1')
            if board_name is None:
                # Sealed-netlist fallback: only with the same part binding and the same pin-1 net.
                if not netlist.get('lcsc'):
                    board_note = f'sealed netlist records no LCSC binding for {ref}; fallback needs the BOM binding {lcsc}'
                elif netlist['lcsc'] != lcsc:
                    board_note = f'sealed netlist binds {ref} to {netlist["lcsc"]}, the BOM to {lcsc}'
                elif '1' not in netlist['nets'] or not pad_nets.get('1'):
                    board_note = 'sealed netlist or probe lacks the pin-1 net'
                elif netlist['nets']['1'] != pad_nets['1']:
                    mode('polarity_conflict', f'board pad 1 net {pad_nets["1"]!r} differs from sealed netlist '
                                              f'pin 1 net {netlist["nets"]["1"]!r}')
                elif polarity(netlist_name) is None:
                    board_note = f'sealed netlist libpart pin 1 name {netlist_name!r} names no polarity'
                else:
                    board_name, board_basis = netlist_name, BOARD_BASES[1]
            elif polarity(board_name) and polarity(netlist_name) and polarity(board_name) != polarity(netlist_name):
                mode('polarity_conflict', f'board pad 1 pinfunction {board_name!r} contradicts sealed netlist '
                                          f'libpart pin 1 {netlist_name!r}')
        try:
            pins = [p for p in symbol_pins(supplier['raw'], lcsc)['pins'] if p['number'] == '1']
        except SupplierError as exc:
            pins = [{'name': None, 'displayed': f'unreadable: {exc}'}]
        # One pin 1 whose displayed number agrees; anything else stays unresolved.
        supplier_name = pins[0]['name'] if len(pins) == 1 and pins[0]['displayed'] == '1' else None
        supplier_basis, supplier_note, used = SUPPLIER_BASES[0], None, None
        if evidence is not None:
            declared = evidence['pin_polarity'].get('1')
            if evidence['supplier_capture_sha256'] != supplier['sha256']:
                supplier_note = 'polarity evidence is bound to another supplier capture'
            elif not partnumber:
                supplier_note = f'export BOM records no Partnumber for {ref}; evidence MPN {evidence["mpn"]!r} cannot bind'
            elif evidence['mpn'] != partnumber:
                supplier_note = f'polarity evidence MPN {evidence["mpn"]!r} differs from BOM part {partnumber!r}'
            elif netlist is not None and netlist.get('mpn') and evidence['mpn'] != netlist['mpn']:
                supplier_note = f'polarity evidence MPN {evidence["mpn"]!r} differs from netlist part {netlist["mpn"]!r}'
            elif polarity(supplier_name) is not None and polarity(supplier_name) != declared:
                mode('polarity_conflict', f'supplier symbol pin 1 {supplier_name!r} contradicts polarity evidence '
                                          f'{declared}')
            elif polarity(supplier_name) is None:
                supplier_name, supplier_basis = declared, SUPPLIER_BASES[1]
                used = {'path': evidence['path'], 'manufacturer_source': evidence['manufacturer_source'],
                        'package_feature_ids': evidence['package_feature_ids'],
                        'symbol_feature_ids': evidence['symbol_feature_ids'], 'limitation': evidence['limitation']}
        sides = {'board': polarity(board_name), 'supplier': polarity(supplier_name)}
        record['polarity'] = dict(sides, board_pin1_function=board_name, supplier_pin1_name=supplier_name,
                                  board_basis=board_basis, supplier_basis=supplier_basis, evidence=used)
        if any(n['mode'] == 'polarity_conflict' for n in notes):
            pass
        elif None in sides.values():
            mode('polarity_unknown', f'pin-1 polarity unresolved: board {board_name!r}'
                                     + (f' ({board_note})' if board_note else '')
                                     + f', supplier pin-1 records {pins}'
                                     + (f' ({supplier_note})' if supplier_note else ''))
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


# --------------------------------------------------------- captured inputs

def read_bom_bindings(text):
    return read_bom_records(text)[0]


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


SCHEMA = 'agent-ee.component-polarity/1'
POLARITY_KEYS = frozenset({'schema', 'lcsc', 'mpn', 'manufacturer', 'supplier_capture_sha256', 'pin_polarity',
                           'manufacturer_source', 'package_feature_ids', 'symbol_feature_ids', 'rationale', 'limitation'})
POLARITY_SOURCE_KEYS = frozenset({'path', 'sha256', 'page', 'locator', 'statement'})
POLARITIES = ('anode', 'cathode')
MIN_RATIONALE = 80
SHA256 = re.compile(r'[0-9a-f]{64}')
FEATURE_ID = re.compile(r'[A-Za-z][A-Za-z0-9_]*[0-9]')


def _polarity_text(value, label, minimum=1):
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise ValueError(f'polarity evidence {label} must be text of at least {minimum} characters')
    return value


def feature_ids(shapes):
    """Every native EasyEDA shape id (`gge…`) named by a list of shape records."""
    found = set()
    for shape in shapes:
        if isinstance(shape, str):
            found.update(field for part in shape.split('^^') for field in part.split('~')
                         if FEATURE_ID.fullmatch(field))
    return found


def validate_record(document, suppliers, read):
    """Verify a captured component-polarity record against captured suppliers and inputs."""
    if not isinstance(document, dict) or set(document) != POLARITY_KEYS:
        raise ValueError('polarity evidence fields must be exactly ' + ', '.join(sorted(POLARITY_KEYS)))
    if document['schema'] != SCHEMA:
        raise ValueError(f'polarity evidence schema must be {SCHEMA}')
    lcsc = _polarity_text(document['lcsc'], 'lcsc')
    _polarity_text(document['mpn'], 'mpn')
    _polarity_text(document['manufacturer'], 'manufacturer')
    supplier = suppliers.get(lcsc)
    if supplier is None:
        raise ValueError(f'polarity evidence names {lcsc}, which has no declared supplier capture')
    sha = document['supplier_capture_sha256']
    if not isinstance(sha, str) or not SHA256.fullmatch(sha) or sha != supplier['sha256']:
        raise ValueError(f'polarity evidence for {lcsc} is bound to another supplier capture digest')
    try:
        pins = {p['number'] for p in symbol_pins(supplier['raw'], lcsc)['pins']}
        raw = _json(supplier['raw'])
    except (SupplierError, ValueError) as exc:
        raise ValueError(f'polarity evidence for {lcsc}: supplier capture unreadable: {exc}') from exc
    mapping = document['pin_polarity']
    if (not isinstance(mapping, dict) or '1' not in mapping or not mapping
            or any(not isinstance(k, str) or v not in POLARITIES for k, v in mapping.items())):
        raise ValueError(f'polarity evidence for {lcsc} pin_polarity must map pin "1" (and others) to anode|cathode')
    unknown = sorted(set(mapping) - pins)
    if unknown:
        raise ValueError(f'polarity evidence for {lcsc} names symbol pins the capture lacks: {unknown}')
    if len(set(mapping.values())) < 2 and len(mapping) > 1:
        raise ValueError(f'polarity evidence for {lcsc} gives every pin the same polarity')
    source = document['manufacturer_source']
    if not isinstance(source, dict) or set(source) != POLARITY_SOURCE_KEYS:
        raise ValueError('polarity evidence manufacturer_source must be exactly {path, sha256, page, locator, statement}')
    _path(source['path'], 'manufacturer source path')
    if not isinstance(source['sha256'], str) or not SHA256.fullmatch(source['sha256']):
        raise ValueError('polarity evidence manufacturer_source.sha256 must be a lowercase sha256')
    if type(source['page']) is not int or source['page'] < 1:
        raise ValueError('polarity evidence manufacturer_source.page must be a positive integer')
    _polarity_text(source['locator'], 'manufacturer_source.locator', 8)
    _polarity_text(source['statement'], 'manufacturer_source.statement', 4)
    if digest_bytes(read(source['path'])) != source['sha256']:
        raise ValueError(f'polarity evidence manufacturer source {source["path"]} differs from its declared sha256')
    shapes = raw.get('dataStr', {}).get('shape', []) if isinstance(raw, dict) else []
    package = raw.get('packageDetail', {}).get('dataStr', {}).get('shape', []) if isinstance(raw, dict) else []
    for key, available in (('package_feature_ids', feature_ids(package)), ('symbol_feature_ids', feature_ids(shapes))):
        ids = document[key]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not FEATURE_ID.fullmatch(i) for i in ids):
            raise ValueError(f'polarity evidence {key} must list native shape ids')
        missing = sorted(set(ids) - available)
        if missing:
            raise ValueError(f'polarity evidence {key} names shapes absent from the {lcsc} capture: {missing}')
    _polarity_text(document['rationale'], 'rationale', MIN_RATIONALE)
    _polarity_text(document['limitation'], 'limitation', 8)
    return document


def load_evidence(read, paths, suppliers):
    """{LCSC: validated record with its captured path} for every declared evidence path."""
    evidence = {}
    for path in paths:
        record = validate_record(_json(read(path)), suppliers, read)
        if record['lcsc'] in evidence:
            raise ValueError(f'two polarity evidence records are declared for {record["lcsc"]}')
        evidence[record['lcsc']] = {**record, 'path': path}
    return evidence


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def capability(params, files, inputs):
    """Static scope/3 contract: exact params; policy, provenance and raw supplier bytes are immutable."""
    params = validate_params(params)
    paths = [params['policy'], *params['supplier_footprints'], *params.get('polarity_evidence', [])]
    for path in paths:
        if path not in files:
            raise ValueError(f'{path} must be captured')
    if inputs is not None:
        def read(path):
            if path not in files:
                raise ValueError(f'{path} must be captured')
            return (Path(inputs) / path).read_bytes()
        load_policy(read(params['policy']))
        suppliers = load_suppliers(read, params['supplier_footprints'])
        paths += [s['path'] for s in suppliers.values()]
        if params.get('polarity_evidence'):
            evidence = load_evidence(read, params['polarity_evidence'], suppliers)
            paths += [e['manufacturer_source']['path'] for e in evidence.values()]
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
        evidence = {}
        if params.get('polarity_evidence'):
            evidence = load_evidence(read, params['polarity_evidence'], suppliers)
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'supplier footprint capture refused: {exc}') from exc
    details['polarity_evidence'] = {lcsc: {'path': e['path'], 'mpn': e['mpn'],
                                           'manufacturer_source': e['manufacturer_source']}
                                    for lcsc, e in sorted(evidence.items())}
    export_spec = next((c for c in ctx.scope['checks'] if c.get('id') == params['export_check']), {})
    # The export's own sealed native netlist supplies canonical libpart pin names (K/A) when a
    # polarized board pad carries no pinfunction (AGENTEE-524 D1); the same binding every row uses.
    nid = (export_spec.get('params') or {}).get('netlist_check')
    try:
        netlist_facts = netlist_pin_facts(prerequisite(ctx, nid, 'netlist', 'design.xml'))
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'sealed native netlist unusable for polarity: {exc}') from exc

    eid, gid = params['export_check'], params['geometry_check']
    manifest_path = prerequisite(ctx, eid, 'fab_export', 'fab/manifest.json')
    stage = manifest_path.parent
    try:
        staged = load_verified_staging(stage, board_sha256=board_sha)
        manifest, rows = staged['manifest'], staged['cpl_rows']
        excluded = staged['excluded']
        bindings, partnumbers = staged['bindings'], staged['partnumbers']
    except (OSError, ValueError, UnicodeError) as exc:
        raise CheckError('malformed', f'fab_export {eid!r} staging is unusable: {exc}') from exc
    ctx.result['evidence'] += [f'work/{eid}/fab/cpl.csv', f'work/{eid}/fab/bom.csv']
    geometry_path = prerequisite(ctx, gid, 'geometry', 'geometry.json')
    if read_json(ctx.run_dir / 'checks' / f'{gid}.json').get('details', {}).get('board_sha256') != board_sha:
        raise CheckError('unsupported', f'geometry check {gid!r} is not bound to the captured board digest')
    try:
        probe = read_json(geometry_path)
        geometry = probe['footprints']
        if not isinstance(geometry, list) or not all(isinstance(fp, dict) for fp in geometry):
            raise ValueError('footprints are not records')
        pad_nets = {}
        for pad in probe.get('pads') or []:
            pad_nets.setdefault(pad['ref'], {})[str(pad['pad'])] = pad.get('net')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'sealed geometry {gid!r} is unreadable: {exc}') from exc

    tolerance = _decimal_text(params['source_tolerance_nm'], 'source_tolerance_nm', nonnegative=True)
    records, applied, unmatched = evaluate(rows, geometry, board_bytes, bindings, policy, suppliers, tolerance,
                                           netlist=netlist_facts, pad_nets=pad_nets, partnumbers=partnumbers,
                                           evidence=evidence)
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
        'diode polarity is checked only for D*/LED* designators from pad pinfunction (or the sealed netlist libpart name when the pad has none), captured polarity evidence and supplier symbol '
        'pin names',
    ]
    if (execution, outcome) == ('ok', 'pass'):
        from .fab_stage import grade_cpl
        grade_cpl(ctx)


def describe_cpl_rotation(ctx):
    descriptor = capability(ctx.params, ctx.files, ctx.inputs)
    ctx.prerequisite(ctx.params['export_check'], ('fab_export',))
    ctx.prerequisite(ctx.params['geometry_check'], ('geometry',))
    ctx.captured(ctx.scope.get('board'))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('cpl_rotation', describe_cpl_rotation, check_cpl_rotation)
