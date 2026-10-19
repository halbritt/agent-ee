"""Offline, source-bound fabricator limits using the staged native rule compiler.

Captured source text verifies citation occurrence, not fabricator authenticity or
engineering applicability. No built-in fabricator values or pricing are supplied.
"""
import copy
import datetime
from collections import defaultdict
from fractions import Fraction

from .routing_rules import (ID, captured_file, load_document, native_rule_inputs, number,
                            rule_text, run_compiled, sha_text, validate_mappings)
from .storage import digest

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
# fab-profile/2 conjunctive surcharge terms: each native finding cites only the via,
# so the joint condition is the intersection of per-term via offender UUIDs.
CONJUNCTIVE = {'min_via_drill_mm', 'min_via_pad_mm'}
SCHEMAS = ('agent-ee.fab-profile/1', 'agent-ee.fab-profile/2')


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


def validate_conjunctive(limit):
    """One sourced rule whose surcharge applies only when every term falls under its bound."""
    if set(limit) - {'id', 'requirement', 'all', 'tier', 'source'}:
        raise ValueError('unknown conjunctive rule fields')
    if limit.get('tier') != 'preferred':
        raise ValueError('conjunctive rules classify extra charges only; tier must be preferred')
    terms = limit['all']
    if not isinstance(terms, list) or len(terms) < 2:
        raise ValueError('conjunctive rule needs at least two terms')
    keys = set()
    for term in terms:
        if not isinstance(term, dict) or set(term) != {'key', 'value', 'unit'}:
            raise ValueError('conjunctive term needs exactly key/value/unit')
        if not isinstance(term['key'], str) or term['key'] not in CONJUNCTIVE:
            raise ValueError('conjunctive terms support only ' + ', '.join(sorted(CONJUNCTIVE)))
        if term['key'] in keys:
            raise ValueError('duplicate conjunctive term key')
        keys.add(term['key'])
        if term['unit'] != 'mm':
            raise ValueError('explicit physical unit mismatch')
        if number(term['value'], term['key']) <= 0:
            raise ValueError('fabricator bound must be positive')


def validate_limit(limit, schema='agent-ee.fab-profile/1'):
    if (not isinstance(limit, dict) or not isinstance(limit.get('id'), str) or
            not ID.fullmatch(limit['id']) or not isinstance(limit.get('requirement'), str) or
            not limit['requirement'].strip()):
        raise ValueError('limit needs safe id and mapped requirement')
    if schema == 'agent-ee.fab-profile/2' and 'all' in limit:
        return validate_conjunctive(limit)
    if set(limit) - {'id', 'requirement', 'key', 'value', 'unit', 'tier', 'source'} - (
            {'drill_options'} if schema == SCHEMAS[1] else set()):
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
    if 'drill_options' in limit:
        if key != 'min_via_drill_mm' or limit['tier'] != 'minimum':
            raise ValueError('drill_options requires minimum min_via_drill_mm')
        options = limit['drill_options']
        if (not isinstance(options, dict) or set(options) != {'values_mm', 'continuous_from_mm'} or
                not isinstance(options['values_mm'], list) or not options['values_mm']):
            raise ValueError('drill_options needs nonempty values_mm and continuous_from_mm')
        sizes = [*options['values_mm'], options['continuous_from_mm']]
        for size in sizes:
            if (number(size, 'drill option') <= 0 or
                    (Fraction(str(size)) * 1_000_000).denominator != 1):
                raise ValueError('drill options must be positive whole nanometres')
        if len(set(sizes)) != len(sizes) or any(v >= sizes[-1] for v in sizes[:-1]):
            raise ValueError('drill options must be unique and below continuous_from_mm')
        if any(v < value for v in sizes):
            raise ValueError('drill options must not be below the minimum drill')


def native_rule(record, key, value):
    constraint, violation, condition = NATIVE[key]
    rules = rule_text(record['marker'], condition, f'{constraint} (min {value:.12g}mm)')
    record.update(rules=rules, rules_sha256=sha_text(rules), native_types=[violation],
                  project_rules={PROJECT_RULES[key]: value})
    return record


def compile_stackup(value, files, inputs, as_of):
    """Source-bound nominal copper weights; thickness matching never converts ounces."""
    from .board_envelope import fields, length, load, mm, name, validate_stackup
    from .storage import canonical
    # Reuse the envelope's exact numeric and alternating-layer contract.
    declaration = load(canonical(value))
    fields(declaration, 'profile stackup', ['name', 'layer_count', 'nominal_thickness_mm',
           'thickness_mm', 'layers', 'layer_thickness_tolerance_mm', 'source'])
    name(declaration['name'], 'profile stackup name', ID)
    expected = validate_stackup({k: declaration[k] for k in ('thickness_mm', 'layers')},
                                declaration['layer_count'])
    expected.update(name=declaration['name'], layer_count=declaration['layer_count'],
                    nominal_thickness_mm=mm(length(declaration['nominal_thickness_mm'],
                                                  'nominal_thickness_mm', positive=True)),
                    layer_thickness_tolerance_mm=mm(length(declaration['layer_thickness_tolerance_mm'],
                                                           'layer_thickness_tolerance_mm')),
                    source=copy.deepcopy(value['source']))
    record = {'expected': expected, 'execution': 'unsupported'}
    try:
        verify_source(value['source'], inputs, files, as_of)
    except (ValueError, KeyError, OSError, UnicodeError) as exc:
        record['reason'] = str(exc)
    else:
        record['execution'] = 'ready'
    return record


def compile_profile(profile, files, inputs):
    required = {'schema', 'name', 'as_of', 'limits'}
    if (not isinstance(profile, dict) or not required <= profile.keys() or
            set(profile) - required - ({'finished_thickness_tolerance', 'stackup'}
                                      if profile.get('schema') == SCHEMAS[1] else set()) or
            profile['schema'] not in SCHEMAS or not isinstance(profile['name'], str)
            or not ID.fullmatch(profile['name']) or not isinstance(profile['limits'], list)
            or not profile['limits']):
        raise ValueError('named, dated nonempty agent-ee.fab-profile/1 or /2 required; no defaults')
    as_of = date(profile['as_of'])
    records, dependencies, ids, keys = [], set(), set(), set()
    for limit in profile['limits']:
        validate_limit(limit, profile['schema'])
        lid, key, tier = limit['id'], limit.get('key'), limit['tier']
        if lid in ids or (key is not None and (key, tier) in keys):
            raise ValueError('duplicate fabricator id or tier/key')
        ids.add(lid)
        if key is not None:
            keys.add((key, tier))
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
        if key is None:
            # Each term is its own isolated native run under a marker outside the ID alphabet.
            record['terms'] = [native_rule({'id': f'{lid}+{term["key"]}', 'requirement': limit['requirement'],
                                            'expected': copy.deepcopy(term),
                                            'marker': f'{record["marker"]}+{term["key"]}',
                                            'execution': 'ready'}, term['key'], term['value'])
                               for term in limit['all']]
        elif key in NATIVE:
            native_rule(record, key, limit['value'])
        else:
            record['geometry'] = key
        records.append(record)
    metadata = {}
    if 'finished_thickness_tolerance' in profile:
        tolerance = profile['finished_thickness_tolerance']
        if (not isinstance(tolerance, dict) or set(tolerance) != {'percent', 'min_nominal_thickness_mm', 'source'} or
                not 0 < number(tolerance['percent'], 'finished thickness tolerance') < 100 or
                number(tolerance['min_nominal_thickness_mm'], 'minimum nominal thickness') <= 0):
            raise ValueError('finished_thickness_tolerance needs percent in (0, 100) and source')
        record = {'expected': copy.deepcopy(tolerance), 'execution': 'unsupported'}
        try:
            source = tolerance['source']
            if isinstance(source, dict) and isinstance(source.get('path'), str):
                captured_file(inputs, files, source['path'])
                dependencies.add(source['path'])
            verify_source(source, inputs, files, as_of)
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            record['reason'] = str(exc)
        else:
            record['execution'] = 'ready'
        # This is an envelope bound, not an isolated native DRC rule or predicate.
        metadata['finished_thickness_tolerance'] = record
    if 'stackup' in profile:
        metadata['stackup'] = compile_stackup(profile['stackup'], files, inputs, as_of)
        source = profile['stackup']['source']
        if isinstance(source, dict) and isinstance(source.get('path'), str):
            try:
                captured_file(inputs, files, source['path'])
            except ValueError:
                pass  # The unsupported metadata retains the missing-source reason.
            else:
                dependencies.add(source['path'])
    return {'schema': 'agent-ee.compiled-fab/1', 'name': profile['name'], 'as_of': as_of, **metadata,
            'constraints': sorted(records, key=lambda c: c['id']),
            'immutable_inputs': sorted(dependencies)}


def capability(params, scope, files, inputs, cid):
    if not isinstance(params, dict) or set(params) != {'fab_profile'}:
        raise ValueError('fab_profile requires only its captured JSON path')
    path = captured_file(inputs, files, params['fab_profile'])
    compiled = compile_profile(load_document(path), files, inputs)
    tolerance = compiled.get('finished_thickness_tolerance')
    if tolerance is not None and tolerance['execution'] != 'ready':
        raise ValueError('finished-thickness tolerance source not verified: ' + tolerance['reason'])
    stackup = compiled.get('stackup')
    if stackup is not None and stackup['execution'] != 'ready':
        raise ValueError('stackup source not verified: ' + stackup['reason'])
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
                                   if p.GetAttribute() == k.PAD_ATTRIB_SMD),
                 'vias': sorted(({'uuid': t.m_Uuid.AsString(), 'drill_nm': int(t.GetDrillValue())}
                                 for t in board.GetTracks() if t.GetClass() == 'PCB_VIA'),
                                key=lambda v: v['uuid'])}
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
        # Fixed saved axes under fab-profile/1: x to x, y to y. No rotation is
        # granted; orientation permission would need a new explicit contract.
        legal = all(a <= b for a, b in zip(actual, value))
    else:
        actual = bool(inventory['smt_ids']); legal = value or not actual
        ids = inventory['smt_ids']
    return {**record, 'status': 'satisfied' if legal else 'violation', 'execution': 'ok',
            'actual': actual, 'offender_ids': [] if legal else ids}


def evaluate_conjunctive(record, terms):
    """Joint offenders are vias every term's native finding cites; any unproved term is unknown."""
    record['terms'] = terms
    for term in terms:
        if term['execution'] != 'ok' or term['status'] not in ('satisfied', 'violation'):
            return {**record, 'execution': term['execution'] if term['execution'] != 'ok' else 'unsupported',
                    'reason': f"conjunctive term {term['expected']['key']} not evaluated: "
                              + term.get('reason', term['status'])}
    joint = sorted(set.intersection(*(offender_ids(term) for term in terms)))
    return {**record, 'execution': 'ok', 'status': 'violation' if joint else 'satisfied',
            'offender_ids': joint}


def offender_ids(record):
    return set(record.get('offender_ids', [])) | {
        i['uuid'] for f in record.get('native_findings', []) for i in f['items']}


def classify_preferred(records, inventory):
    """Only proved legal features can become extra charges, never defects.

    The native inventory is required: an edge clearance is satisfied only against
    one qualified outline, because absent or malformed Edge.Cuts prove nothing.
    """
    for record in records:
        options = record['expected'].get('drill_options')
        if options is None or record['execution'] != 'ok':
            continue
        vias = inventory.get('vias')
        allowed = {Fraction(str(v)) * 1_000_000 for v in options['values_mm']}
        continuous = Fraction(str(options['continuous_from_mm'])) * 1_000_000
        minimum = Fraction(str(record['expected']['value'])) * 1_000_000
        # Below the sourced minimum is a defect, provided native DRC cites the same
        # vias; an unlisted drill at or above it is unknown and takes precedence.
        below = None if vias is None else [v for v in vias if v['drill_nm'] < minimum]
        unknown = None if vias is None else [v for v in vias if v['drill_nm'] >= minimum and
                                            v['drill_nm'] not in allowed and v['drill_nm'] < continuous]
        if below:
            record['below_minimum_vias'] = below
        if unknown is None or unknown:
            record.update(status='not_evaluated', execution='unsupported',
                          reason='via drill outside sourced options' if unknown else 'via drill inventory missing',
                          unsupported_vias=unknown)
        elif below and (record['status'] != 'violation' or
                        not {v['uuid'] for v in below} <= offender_ids(record)):
            record.update(status='not_evaluated', execution='unsupported',
                          reason='below-minimum via drill not cited by native DRC', unsupported_vias=[])
    if inventory['outline_xy_mm'] is None:
        for record in records:
            if record['expected'].get('key') == 'min_edge_clearance_mm' and record['status'] == 'satisfied':
                problem = inventory.get('outline_problem', {})
                record.update(status='not_evaluated', execution='unsupported',
                              reason='edge clearance requires one simple closed straight-segment outline: '
                                     + problem.get('reason', 'unqualified outline'),
                              locator_ids=problem.get('locator_ids', []))
    minima = {r['expected']['key']: r for r in records if r['expected']['tier'] == 'minimum'}
    for record in records:
        if record['expected']['tier'] != 'preferred':
            continue
        # A conjunctive rule needs every term key's minimum proved, and a via illegal
        # under any of those minima is a defect, never part of the surcharge.
        keys = ([t['key'] for t in record['expected']['all']] if 'all' in record['expected']
                else [record['expected']['key']])
        required = [minima.get(key) for key in keys]
        if any(m is None or m['status'] not in ('satisfied', 'violation') for m in required):
            record.update(status='not_evaluated', execution='unsupported',
                          reason='extra-charge classification requires evaluated minimum for '
                                 + ('every term key' if len(keys) > 1 else 'same key'))
        elif record['status'] == 'violation':
            found = offender_ids(record)
            illegal = set().union(*(offender_ids(m) for m in required))
            offenders = sorted(found - illegal)
            record.update(status='extra_charge' if offenders else 'satisfied', offender_ids=offenders,
                          minimum_violation_ids=sorted(found & illegal))
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
