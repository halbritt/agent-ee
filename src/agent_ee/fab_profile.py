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


def cross(o, a, b):
    return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])


def touches(p, q, r, s):
    """Exact integer test: closed segments pq and rs share at least one point."""
    def within(a, b, c):
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
    d1, d2, d3, d4 = cross(r, s, p), cross(r, s, q), cross(p, q, r), cross(p, q, s)
    if ((d1 > 0) != (d2 > 0) and d1 and d2) and ((d3 > 0) != (d4 > 0) and d3 and d4):
        return True
    return ((not d1 and within(r, s, p)) or (not d2 and within(r, s, q)) or
            (not d3 and within(p, q, r)) or (not d4 and within(p, q, s)))


def simple_polygon(segments):
    """Qualify one simple, nonzero-area polygon from exact saved segment endpoints.

    Returns (extents_mm, None) or (None, (reason, locator_uuids)). Anything other
    than one closed, non-self-touching ring stays unqualified; nothing is repaired.
    """
    def refuse(reason, *indices):
        return None, (reason, sorted({segments[i][0] for i in indices}))
    seen = {}
    for index, (_, a, b) in enumerate(segments):
        if a == b:
            return refuse('zero-length Edge.Cuts segment', index)
        pair = frozenset((a, b))
        if pair in seen:
            return refuse('coincident or reversed duplicate Edge.Cuts segments', seen[pair], index)
        seen[pair] = index
    incident = defaultdict(list)
    for index, (_, a, b) in enumerate(segments):
        incident[a].append(index); incident[b].append(index)
    for vertex in sorted(incident):
        if len(incident[vertex]) != 2:
            return refuse('open, branched or vertex-touching Edge.Cuts outline', *incident[vertex])
    order, ring, vertex, index = [], [], segments[0][1], 0
    while not order or index != 0:
        order.append(index); ring.append(vertex)
        _, a, b = segments[index]
        vertex = b if vertex == a else a
        index = next(i for i in incident[vertex] if i != index)
    if len(order) != len(segments):
        return refuse('multiple closed Edge.Cuts outlines (cutouts or separate boards) unsupported',
                      *set(range(len(segments))) - set(order))
    for i, first in enumerate(order):
        p, q = segments[first][1:]
        for second in order[i+1:]:
            r, s = segments[second][1:]
            shared = {p, q} & {r, s}
            if shared:
                # Adjacent edges may meet only at the shared vertex, never fold back.
                v = shared.pop()
                u, w = (p if q == v else q), (r if s == v else s)
                folded = not cross(v, u, w) and (u[0]-v[0])*(w[0]-v[0]) + (u[1]-v[1])*(w[1]-v[1]) > 0
            else:
                folded = touches(p, q, r, s)
            if folded:
                return refuse('self-intersecting or overlapping Edge.Cuts segments', first, second)
    if not sum(cross((0, 0), a, b) for a, b in zip(ring, ring[1:] + ring[:1])):
        return refuse('zero-area Edge.Cuts outline', *order)
    xs, ys = zip(*ring)
    return [(max(xs)-min(xs))/1e6, (max(ys)-min(ys))/1e6], None


def native_inventory(board_path):
    import pcbnew as k
    board = k.LoadBoard(str(board_path))
    edges = [e for e in board.GetDrawings() if e.GetLayer() == k.Edge_Cuts]
    # Footprint shapes on Edge.Cuts join KiCad's board outline (cutouts, tabs).
    footprint_edges = [i for fp in board.GetFootprints() for i in (*fp.GraphicalItems(), *fp.GetFields())
                       if i.GetLayer() == k.Edge_Cuts]
    unsupported = [e for e in edges if e.GetClass() != 'PCB_SHAPE' or e.GetShape() != k.SHAPE_T_SEGMENT]
    outline, problem = None, None
    if not edges:
        problem = ('no Edge.Cuts outline', [])
    elif footprint_edges:
        problem = ('footprint Edge.Cuts items unsupported',
                   sorted(i.m_Uuid.AsString() for i in footprint_edges))
    elif unsupported:
        problem = ('only straight Edge.Cuts segments qualified',
                   sorted(e.m_Uuid.AsString() for e in unsupported))
    else:
        outline, problem = simple_polygon(sorted(
            (e.m_Uuid.AsString(), (int(e.GetStart().x), int(e.GetStart().y)),
             (int(e.GetEnd().x), int(e.GetEnd().y))) for e in edges))
    # KiCad does not save a board UUID (each load invents one); cite saved names/UUIDs only.
    inventory = {'copper_layers': board.GetCopperLayerCount(),
                 # Canonical saved layer names; user display names may omit '.Cu'.
                 'copper_layer_names': [board.GetStandardLayerName(l) for l in board.GetEnabledLayers().Seq()
                                        if k.IsCopperLayer(l)],
                 'outline_xy_mm': outline, 'outline_ids': sorted(e.m_Uuid.AsString() for e in edges),
                 'smt_ids': sorted(p.m_Uuid.AsString() for fp in board.GetFootprints() for p in fp.Pads()
                                   if p.GetAttribute() == k.PAD_ATTRIB_SMD)}
    if problem:
        inventory['outline_problem'] = {'reason': problem[0], 'locator_ids': problem[1]}
    return inventory


def evaluate_geometry(constraint, inventory):
    limit = constraint['expected']
    record = {k: constraint[k] for k in ('id', 'requirement', 'expected')}
    key, value = limit['key'], limit['value']
    if key == 'max_layers':
        actual = inventory['copper_layers']; legal = actual <= value
        ids = inventory['copper_layer_names']
    elif key == 'max_board_mm':
        actual = inventory['outline_xy_mm']; ids = inventory['outline_ids']
        if actual is None:
            problem = inventory.get('outline_problem', {})
            return {**record, 'status': 'not_evaluated', 'execution': 'unsupported',
                    'reason': 'one simple closed straight-segment outline required: '
                              + problem.get('reason', 'unqualified outline'),
                    'locator_ids': problem.get('locator_ids', [])}
        # Saved-axis extents; a fabricator's x/y size limit is not orientation-bound.
        legal = all(a <= b for a, b in zip(sorted(actual), sorted(value)))
    else:
        actual = bool(inventory['smt_ids']); legal = value or not actual
        ids = inventory['smt_ids']
    return {**record, 'status': 'satisfied' if legal else 'violation', 'execution': 'ok',
            'actual': actual, 'offender_ids': [] if legal else ids}


def offender_ids(record):
    return set(record.get('offender_ids', [])) | {
        i['uuid'] for f in record.get('native_findings', []) for i in f['items']}


def classify_preferred(records, inventory=None):
    """Only proved legal features can become extra charges, never defects.

    With the native inventory, an edge clearance is satisfied only against one
    qualified outline: absent or malformed Edge.Cuts cannot prove clearance.
    """
    if inventory is not None and inventory['outline_xy_mm'] is None:
        for record in records:
            if record['expected']['key'] == 'min_edge_clearance_mm' and record['status'] == 'satisfied':
                problem = inventory.get('outline_problem', {})
                record.update(status='not_evaluated', execution='unsupported',
                              reason='edge clearance requires one simple closed straight-segment outline: '
                                     + problem.get('reason', 'unqualified outline'),
                              locator_ids=problem.get('locator_ids', []))
    minima = {r['expected']['key']: r for r in records if r['expected']['tier'] == 'minimum'}
    for record in records:
        if record['expected']['tier'] != 'preferred':
            continue
        minimum = minima.get(record['expected']['key'])
        if minimum is None or minimum['status'] not in ('satisfied', 'violation'):
            record.update(status='not_evaluated', execution='unsupported',
                          reason='extra-charge classification requires evaluated minimum for same key')
        elif record['status'] == 'violation':
            found = offender_ids(record)
            offenders = sorted(found - offender_ids(minimum))
            record.update(status='extra_charge' if offenders else 'satisfied', offender_ids=offenders,
                          minimum_violation_ids=sorted(found & offender_ids(minimum)))
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
