"""Offline, source-bound fabricator limits using the staged native rule compiler.

Captured source text verifies citation occurrence, not fabricator authenticity or
engineering applicability. No built-in fabricator values or pricing are supplied.
"""
import copy
import datetime
import json
from collections import defaultdict

from .routing_rules import (ID, captured_file, number, rule_text, sha_text,
                            validate_mappings, run_compiled, native_rule_inputs)
from .storage import digest
from .hardware_interface import unique_object

NATIVE = {
    'min_trace_mm': ('track_width', 'track_width', "A.Type == 'Track' || A.Type == 'Arc'"),
    'min_clearance_mm': ('clearance', 'clearance', None),
    'min_via_drill_mm': ('hole_size', 'drill_out_of_range', "A.Type == 'Via'"),
    'min_via_pad_mm': ('via_diameter', 'via_diameter', "A.Type == 'Via'"),
    'min_edge_clearance_mm': ('edge_clearance', 'copper_edge_clearance', None),
    'min_hole_to_hole_mm': ('hole_to_hole', 'hole_to_hole', None),
}
GEOMETRY = {'max_layers', 'max_board_mm', 'supports_smt'}
PROJECT_RULES = {'min_trace_mm': 'min_track_width', 'min_clearance_mm': 'min_clearance',
                 'min_via_drill_mm': 'min_through_hole_diameter', 'min_via_pad_mm': 'min_via_diameter',
                 'min_edge_clearance_mm': 'min_copper_edge_clearance',
                 'min_hole_to_hole_mm': 'min_hole_to_hole'}


def date(text):
    if not isinstance(text, str) or datetime.date.fromisoformat(text).isoformat() != text:
        raise ValueError('as_of must be an ISO calendar date')
    return text


def verify_source(source, inputs, files, as_of):
    if not isinstance(source, dict) or set(source) != {'path', 'sha256', 'source', 'as_of', 'page', 'quote'}:
        raise ValueError('limit needs captured source path/hash/source/as_of/page/quote')
    if date(source['as_of']) != as_of:
        raise ValueError('fact as_of must equal pinned profile date')
    if not isinstance(source['source'], str) or not source['source'].strip():
        raise ValueError('source identity required')
    path = captured_file(inputs, files, source['path'])
    if source['sha256'] != digest(path):
        raise ValueError('source capture hash mismatch')
    page = source['page']
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError('source page must be positive integer')
    # The bounded v1 dialect is UTF-8 text with form-feed page boundaries.
    # PDFs need a separately qualified extraction path; never guess their text.
    if path.suffix.lower() == '.pdf' or path.read_bytes().startswith(b'%PDF-'):
        raise ValueError('raw PDF requires a separately qualified text extraction adapter')
    text = path.read_text(encoding='utf-8')
    pages = text.split('\f')
    quote = source['quote']
    if not isinstance(quote, str) or not quote.strip() or page > len(pages) or quote not in pages[page-1]:
        raise ValueError('exact source quote absent on captured page')
    return source['path']


def validate_limit(limit):
    if (not isinstance(limit, dict) or not isinstance(limit.get('id'), str) or
            not ID.fullmatch(limit['id']) or not isinstance(limit.get('requirement'), str) or
            not limit['requirement'].strip()):
        raise ValueError('limit needs safe id and mapped requirement')
    if set(limit) - {'id', 'requirement', 'key', 'value', 'unit', 'tier', 'source'}:
        raise ValueError('unknown fabricator limit fields')
    key = limit.get('key')
    if not isinstance(key, str) or key not in NATIVE.keys() | GEOMETRY:
        raise ValueError('unsupported fabricator limit key')
    if limit.get('tier') not in ('minimum', 'preferred'):
        raise ValueError('limit tier must be minimum or preferred')
    unit = 'count' if key == 'max_layers' else 'boolean' if key == 'supports_smt' else 'mm'
    if limit.get('unit') != unit:
        raise ValueError('explicit physical unit mismatch')
    value = limit.get('value')
    if key == 'supports_smt':
        if not isinstance(value, bool):
            raise ValueError('supports_smt must be boolean')
    elif key == 'max_board_mm':
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError('board size must be explicit [x,y] mm')
        for v in value:
            if number(v, key) <= 0:
                raise ValueError('board bounds must be positive')
    elif number(value, key, integer=key == 'max_layers') <= 0:
        raise ValueError('fabricator bound must be positive')


def compile_profile(profile, files, inputs):
    if (not isinstance(profile, dict) or set(profile) != {'schema', 'name', 'as_of', 'limits'} or
            profile['schema'] != 'agent-ee.fab-profile/1' or not isinstance(profile['name'], str)
            or not ID.fullmatch(profile['name']) or not isinstance(profile['limits'], list)
            or not profile['limits']):
        raise ValueError('named, dated nonempty agent-ee.fab-profile/1 required; no defaults')
    as_of = date(profile['as_of'])
    records, dependencies, ids, keys = [], set(), set(), set()
    for limit in profile['limits']:
        validate_limit(limit)
        lid, key, tier = limit['id'], limit['key'], limit['tier']
        if lid in ids or (key, tier) in keys:
            raise ValueError('duplicate fabricator id or tier/key')
        ids.add(lid); keys.add((key, tier))
        record = {'id': lid, 'requirement': limit['requirement'], 'expected': copy.deepcopy(limit),
                  'kind': 'fab', 'marker': 'agent_ee_' + lid, 'execution': 'unsupported'}
        try:
            source = limit.get('source')
            if isinstance(source, dict) and isinstance(source.get('path'), str):
                captured_file(inputs, files, source['path'])
                dependencies.add(source['path'])
            verify_source(source, inputs, files, as_of)
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            records.append({**record, 'reason': str(exc)})
            continue
        record['execution'] = 'ready'
        if key in NATIVE:
            constraint, violation, condition = NATIVE[key]
            rules = rule_text(record['marker'], condition, f'{constraint} (min {limit["value"]:.12g}mm)')
            record.update(rules=rules, rules_sha256=sha_text(rules), native_types=[violation],
                          project_rules={PROJECT_RULES[key]: limit['value']})
        else:
            record['geometry'] = key
        records.append(record)
    return {'schema': 'agent-ee.compiled-fab/1', 'name': profile['name'], 'as_of': as_of,
            'constraints': sorted(records, key=lambda c: c['id']),
            'immutable_inputs': sorted(dependencies)}


def capability(params, scope, files, inputs, cid):
    if not isinstance(params, dict) or set(params) != {'fab_profile'}:
        raise ValueError('fab_profile requires only its captured JSON path')
    path = captured_file(inputs, files, params['fab_profile'])
    compiled = compile_profile(json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object), files, inputs)
    validate_mappings(compiled['constraints'], scope, files, cid)
    return {'compiled': compiled,
            'immutable_inputs': sorted({params['fab_profile'], *compiled['immutable_inputs'],
                                        *native_rule_inputs(scope, files, inputs)}),
            'predicates': ['fab:' + c['id'] for c in compiled['constraints']
                           if c['execution'] == 'ready' and c['expected']['tier'] == 'minimum']}


def native_inventory(board_path):
    import pcbnew as k
    board = k.LoadBoard(str(board_path))
    edges = [e for e in board.GetDrawings() if e.GetLayer() == k.Edge_Cuts]
    outline, adjacency = None, defaultdict(list)
    if edges and all(e.GetClass() == 'PCB_SHAPE' and e.GetShape() == k.SHAPE_T_SEGMENT for e in edges):
        for edge in edges:
            start, end = edge.GetStart(), edge.GetEnd()
            a, b = (int(start.x), int(start.y)), (int(end.x), int(end.y))
            adjacency[a].append(b); adjacency[b].append(a)
        seen, pending = set(), [next(iter(adjacency))]
        while pending:
            vertex = pending.pop()
            if vertex not in seen:
                seen.add(vertex); pending.extend(adjacency[vertex])
        if len(seen) == len(adjacency) and all(len(n) == 2 for n in adjacency.values()):
            xs, ys = zip(*adjacency)
            outline = [(max(xs)-min(xs))/1e6, (max(ys)-min(ys))/1e6]
    return {'board_uuid': board.m_Uuid.AsString(), 'copper_layers': board.GetCopperLayerCount(),
            'copper_layer_names': [board.GetLayerName(l) for l in board.GetEnabledLayers().Seq()
                                   if board.GetLayerName(l).endswith('.Cu')],
            'outline_xy_mm': outline, 'outline_ids': sorted(e.m_Uuid.AsString() for e in edges),
            'smt_ids': sorted(p.m_Uuid.AsString() for fp in board.GetFootprints() for p in fp.Pads()
                              if p.GetAttribute() == k.PAD_ATTRIB_SMD)}


def evaluate_geometry(constraint, inventory):
    limit = constraint['expected']
    record = {k: constraint[k] for k in ('id', 'requirement', 'expected')}
    key, value = limit['key'], limit['value']
    ids = [inventory['board_uuid']]
    if key == 'max_layers':
        actual = inventory['copper_layers']; legal = actual <= value
    elif key == 'max_board_mm':
        actual = inventory['outline_xy_mm']; ids = inventory['outline_ids']
        if actual is None:
            return {**record, 'status': 'not_evaluated', 'execution': 'unsupported',
                    'reason': 'closed straight-segment outline required'}
        legal = all(a <= b for a, b in zip(actual, value))
    else:
        actual = bool(inventory['smt_ids']); legal = value or not actual
        ids = inventory['smt_ids']
    return {**record, 'status': 'satisfied' if legal else 'violation', 'execution': 'ok',
            'actual': actual, 'offender_ids': [] if legal else ids}


def offender_ids(record):
    return set(record.get('offender_ids', [])) | {
        i['uuid'] for f in record.get('native_findings', []) for i in f['items']}


def classify_preferred(records):
    """Only proved legal features can become extra charges, never defects."""
    minima = {r['expected']['key']: r for r in records if r['expected']['tier'] == 'minimum'}
    for record in records:
        if record['expected']['tier'] != 'preferred':
            continue
        minimum = minima.get(record['expected']['key'])
        if minimum is None or minimum['status'] not in ('satisfied', 'violation'):
            record.update(status='not_evaluated', execution='unsupported',
                          reason='extra-charge classification requires evaluated minimum for same key')
        elif record['status'] == 'violation':
            offenders = sorted(offender_ids(record) - offender_ids(minimum))
            record.update(status='extra_charge' if offenders else 'satisfied', offender_ids=offenders)
    return records


def check_fab_profile(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        source = {'path': ctx.params['fab_profile'], 'sha256': digest(ctx.inputs / ctx.params['fab_profile'])}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    records = run_compiled(ctx, descriptor['compiled'], source)
    ctx.result['details']['extra_charges'] = [r for r in records if r['status'] == 'extra_charge']
