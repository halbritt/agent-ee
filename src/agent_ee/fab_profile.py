"""Offline, source-bound fabricator limits using the staged native rule compiler.

Captured source text verifies citation occurrence, not fabricator authenticity or
engineering applicability; only silk minima also bind a recorded supplier material
scope. No built-in fabricator values or pricing are supplied.
"""
import copy
import datetime
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

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
# fab-profile/2 silkscreen (legend) minima. Each key keeps its own measured meaning
# and is kept out of NATIVE, whose finding kinds guard scaffold templates. KiCad 10's
# silk_clearance against a pad measures pad copper, not the mask aperture; a tented
# pad reports silk_overlap instead and stays unknown. Text keys grade silk text only.
SILK = {
    'min_pad_to_silk_mm': ('silk_clearance', ['silk_over_copper', 'silk_overlap'], "B.Type == 'Pad'"),
    'min_silk_text_height_mm': ('text_height', ['text_height'], "A.Layer == '?.Silkscreen'"),
    'min_silk_line_width_mm': ('text_thickness', ['text_thickness'], "A.Layer == '?.Silkscreen'"),
}
# Sourced, but with no qualified measurement: the limit stays unknown, never pass.
UNMEASURED = {
    'min_silk_to_mask_mm': ('silk_to_mask_unmeasured',
                            'no qualified silkscreen-to-solder-mask measurement: KiCad 10 silk_clearance '
                            'measures pad copper, not mask apertures'),
}
ADVISORY_ROLES = ('recommended', 'guide_minimum')
# A silk quote is one whole recorded line '<subject> / <role>: <value> mm' whose subject
# states the key's quantity and whose role fits its use: a limit cites a minimum, so a
# recommendation never stands as a hard limit, whatever its value.
FACT = re.compile(r'(?P<subject>.+) / (?P<role>[a-z ]+): (?P<value>[0-9]+(?:\.[0-9]+)?) mm')
SILK_SUBJECTS = {
    'min_pad_to_silk_mm': ('legend / pad to silkscreen',),
    'min_silk_text_height_mm': ('legend / text height', 'legend / character height'),
    'min_silk_line_width_mm': ('legend / line width (characters)', 'legend / line width / text thickness'),
    'min_silk_to_mask_mm': ('legend / silkscreen to solder mask clearance',),
}
MINIMUM_ROLES = ('minimum', 'absolute minimum')
FACT_ROLES = {'limit': MINIMUM_ROLES, 'guide_minimum': MINIMUM_ROLES, 'recommended': ('recommended',)}
# Supplier base-material scope of one source page, recorded as a whole names-only line.
SCOPE = re.compile(r'scope / (?P<subject>.+) / (?:base material|supported base materials): (?P<names>.+)')
# Saved KiCad stackup dielectric material names qualified to a supplier base-material
# name. Any other saved name, or none, leaves silk applicability unverified.
BASE_MATERIALS = {'FR4': 'FR-4'}


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


def recorded_line(source, inputs, files):
    """True when a verified quote is one whole line of its captured page."""
    text = captured_file(inputs, files, source['path']).read_text(encoding='utf-8')
    return source['quote'] in text.split('\f')[source['page'] - 1].splitlines()


def silk_fact(source, key, value, roles, inputs, files):
    """Bind a verified silk quote's subject, role and value to its use; (code, reason) or None."""
    match = FACT.fullmatch(source['quote'])
    if not match or not recorded_line(source, inputs, files):
        return 'source_fact_unparsed', 'silk quote must be one whole recorded "<subject> / <role>: <value> mm" line'
    if match['subject'] not in SILK_SUBJECTS[key]:
        return 'source_subject_mismatch', f'cited fact {match["subject"]!r} does not state {key}'
    if match['role'] not in roles:
        return 'source_role_mismatch', (f'cited fact role {match["role"]!r} cannot stand as '
                                        + ' or '.join(repr(r) for r in roles))
    if Fraction(match['value']) != Fraction(str(value)):
        return 'source_value_mismatch', f'cited fact value {match["value"]} mm differs from {value} mm'
    return None


def compile_applicability(entries, files, inputs, as_of, dependencies):
    """Supplier base-material scope per cited source page; never a rule, grade or charge."""
    if not isinstance(entries, list) or not entries:
        raise ValueError('applicability must be a nonempty list')
    records, ids, pages = [], set(), set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'base_materials', 'source'} or
                not isinstance(entry['id'], str) or not ID.fullmatch(entry['id'])):
            raise ValueError('applicability needs exactly id/base_materials/source')
        names, source = entry['base_materials'], entry['source']
        if (not isinstance(names, list) or not names or len(set(map(repr, names))) != len(names) or
                not all(isinstance(n, str) and n.strip() == n and n for n in names)):
            raise ValueError('base_materials must be unique nonempty names')
        page = (repr(source.get('path')), repr(source.get('page'))) if isinstance(source, dict) else None
        if entry['id'] in ids or page in pages:
            raise ValueError('duplicate applicability id or source page')
        ids.add(entry['id']); pages.add(page)
        record = {'id': entry['id'], 'expected': copy.deepcopy(entry), 'execution': 'unsupported'}
        try:
            if isinstance(source, dict) and isinstance(source.get('path'), str):
                captured_file(inputs, files, source['path'])
                dependencies.add(source['path'])
            verify_source(source, inputs, files, as_of)
            match = SCOPE.fullmatch(source['quote'])
            if not match or not recorded_line(source, inputs, files) or match['names'].split(', ') != names:
                raise ValueError('applicability quote must be one whole recorded scope line naming '
                                 'exactly base_materials')
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            record['reason'] = str(exc)
        else:
            record['execution'] = 'ready'
        records.append(record)
    return sorted(records, key=lambda r: r['id'])


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
    silk = SILK.keys() | UNMEASURED.keys() if schema == SCHEMAS[1] else set()
    if not isinstance(key, str) or key not in NATIVE.keys() | GEOMETRY | silk:
        raise ValueError('unsupported fabricator limit key')
    if key in silk and limit.get('tier') != 'minimum':
        # Recommendations are sourced advisories; they never grade or charge.
        raise ValueError('silkscreen limits are minimum-tier only; record recommendations as advisories')
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
    if key in SILK:
        # Silk minima stage no board-wide project minimum: only the marked rule grades.
        constraint, types, condition = SILK[key]
        project = {}
    else:
        constraint, violation, condition = NATIVE[key]
        types, project = [violation], {PROJECT_RULES[key]: value}
    rules = rule_text(record['marker'], condition, f'{constraint} (min {value:.12g}mm)')
    record.update(rules=rules, rules_sha256=sha_text(rules), native_types=list(types),
                  project_rules=project)
    return record


def compile_advisories(advisories, files, inputs, as_of, dependencies):
    """Sourced recommendations and guide values; never rules, predicates or charges."""
    if not isinstance(advisories, list) or not advisories:
        raise ValueError('advisories must be a nonempty list')
    records, ids, roles = [], set(), set()
    for advisory in advisories:
        if (not isinstance(advisory, dict) or
                set(advisory) != {'id', 'key', 'value', 'unit', 'role', 'source'} or
                not isinstance(advisory['id'], str) or not ID.fullmatch(advisory['id'])):
            raise ValueError('advisory needs exactly id/key/value/unit/role/source')
        key, role = advisory['key'], advisory['role']
        if not isinstance(key, str) or key not in NATIVE.keys() | SILK.keys() | UNMEASURED.keys():
            raise ValueError('unsupported advisory key')
        if role not in ADVISORY_ROLES:
            raise ValueError('advisory role must be ' + ' or '.join(ADVISORY_ROLES))
        if advisory['unit'] != 'mm':
            raise ValueError('explicit physical unit mismatch')
        if number(advisory['value'], key) <= 0:
            raise ValueError('advisory value must be positive')
        if advisory['id'] in ids or (key, role) in roles:
            raise ValueError('duplicate advisory id or key/role')
        ids.add(advisory['id']); roles.add((key, role))
        record = {'id': advisory['id'], 'expected': copy.deepcopy(advisory), 'execution': 'unsupported'}
        try:
            source = advisory['source']
            if isinstance(source, dict) and isinstance(source.get('path'), str):
                captured_file(inputs, files, source['path'])
                dependencies.add(source['path'])
            verify_source(source, inputs, files, as_of)
            problem = silk_fact(source, key, advisory['value'], FACT_ROLES[role], inputs, files) \
                if key in SILK_SUBJECTS else None
            if problem:
                raise ValueError(problem[1])
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            record['reason'] = str(exc)
        else:
            record['execution'] = 'ready'
        records.append(record)
    return sorted(records, key=lambda r: r['id'])


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
            set(profile) - required - ({'finished_thickness_tolerance', 'stackup', 'advisories', 'applicability'}
                                      if profile.get('schema') == SCHEMAS[1] else set()) or
            profile['schema'] not in SCHEMAS or not isinstance(profile['name'], str)
            or not ID.fullmatch(profile['name']) or not isinstance(profile['limits'], list)
            or not profile['limits']):
        raise ValueError('named, dated nonempty agent-ee.fab-profile/1 or /2 required; no defaults')
    as_of = date(profile['as_of'])
    records, dependencies, ids, keys = [], set(), set(), set()
    metadata = {}
    if 'applicability' in profile:
        metadata['applicability'] = compile_applicability(profile['applicability'], files, inputs, as_of,
                                                          dependencies)
    scopes = {(a['expected']['source']['path'], a['expected']['source']['page']): a
              for a in metadata.get('applicability', []) if a['execution'] == 'ready'}
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
        problem = silk_fact(source, key, limit['value'], FACT_ROLES['limit'], inputs, files) \
            if key in SILK_SUBJECTS else None
        if problem:
            records.append({**record, 'code': problem[0], 'reason': problem[1]})
            continue
        record['execution'] = 'ready'
        if key is None:
            # Each term is its own isolated native run under a marker outside the ID alphabet.
            record['terms'] = [native_rule({'id': f'{lid}+{term["key"]}', 'requirement': limit['requirement'],
                                            'expected': copy.deepcopy(term),
                                            'marker': f'{record["marker"]}+{term["key"]}',
                                            'execution': 'ready'}, term['key'], term['value'])
                               for term in limit['all']]
        elif key in NATIVE or key in SILK:
            native_rule(record, key, limit['value'])
            scope = scopes.get((source['path'], source['page']))
            if key in SILK and scope is not None:
                # Graded only where the saved board material is in this page's scope.
                record['applicability'] = {'id': scope['id'], 'base_materials': scope['expected']['base_materials'],
                                           'source_path': source['path'], 'source_page': source['page']}
        elif key in UNMEASURED:
            code, reason = UNMEASURED[key]
            record.update(execution='unsupported', code=code, reason=reason)
        else:
            record['geometry'] = key
        records.append(record)
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
    if 'advisories' in profile:
        metadata['advisories'] = compile_advisories(profile['advisories'], files, inputs, as_of, dependencies)
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
    unverified = [a for a in compiled.get('advisories', []) if a['execution'] != 'ready']
    if unverified:
        raise ValueError('advisory source not verified: ' + unverified[0]['reason'])
    unverified = [a for a in compiled.get('applicability', []) if a['execution'] != 'ready']
    if unverified:
        raise ValueError('applicability source not verified: ' + unverified[0]['reason'])
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
    classes = {k.PAD_ATTRIB_PTH: 'PTH', k.PAD_ATTRIB_NPTH: 'NPTH'}
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
                                key=lambda v: v['uuid']),
                 # Every drilled pad, by plating class; a captured table may cover vias only.
                 'holes': sorted(({'uuid': p.m_Uuid.AsString(), 'hole_class': classes.get(p.GetAttribute(), 'other'),
                                   'drill_x_nm': int(p.GetDrillSize().x), 'drill_y_nm': int(p.GetDrillSize().y)}
                                  for fp in board.GetFootprints() for p in fp.Pads()
                                  if p.GetDrillSize().x > 0 or p.GetDrillSize().y > 0),
                                 key=lambda h: h['uuid'])}
    if problem:
        inventory['outline_problem'] = {'reason': problem[0], 'locator_ids': problem[1]}
    inventory['silk'] = silk_inventory(board)
    inventory['dielectrics'] = saved_dielectrics(board_path)
    return inventory


def saved_dielectrics(board_path):
    """Saved stackup core/prepreg layers with their material names; None without one stackup."""
    from .board_envelope import atoms
    from .symbol_library import LibraryError, children, parse_sexpr
    try:
        root = parse_sexpr(Path(board_path).read_bytes())
    except LibraryError:
        return None
    setups = children(root, 'setup')
    stackups = children(setups[0], 'stackup') if len(setups) == 1 else []
    if len(stackups) != 1:
        return None
    return [{'layer': layer[1] if len(layer) > 1 and isinstance(layer[1], str) else None,
             # Sublayers repeat material within the same saved layer.
             'materials': [atoms(m)[0] for m in children(layer, 'material') if atoms(m)]}
            for layer in children(stackups[0], 'layer')
            if any(atoms(t)[:1] in (['core'], ['prepreg']) for t in children(layer, 'type'))]


def silk_inventory(board):
    """Every saved silkscreen item: text font/visibility, exact graphic stroke widths.

    Text and stroked shapes are the qualified classes; anything else printed on a
    silk layer (filled shapes, dimensions, tables, zones, pads) is listed as other.
    """
    import pcbnew as k
    layers = {k.F_SilkS: 'F.SilkS', k.B_SilkS: 'B.SilkS'}
    footprints = list(board.GetFootprints())
    items = [*board.GetDrawings(), *board.Zones(),
             *(i for fp in footprints for i in (*fp.GraphicalItems(), *fp.GetFields(), *fp.Zones(), *fp.Pads()))]
    texts, graphics, other = [], [], []
    for item in items:
        on = [name for layer, name in layers.items() if item.IsOnLayer(layer)]
        if not on:
            continue
        cls, uid = item.GetClass(), item.m_Uuid.AsString()
        base = {'uuid': uid, 'class': cls, 'layers': on}
        if cls in ('PCB_TEXT', 'PCB_FIELD', 'PCB_TEXTBOX'):
            # KiCad's own stroke font has no font object; outline fonts name a face.
            texts.append({**base, 'visible': bool(item.IsVisible()), 'stroke_font': item.GetFont() is None,
                          'font_name': item.GetFontName(),
                          'knockout': cls != 'PCB_TEXTBOX' and bool(item.IsKnockout()),
                          'height_nm': int(item.GetTextHeight()), 'thickness_nm': int(item.GetTextThickness()),
                          **({'border_width_nm': int(item.GetBorderWidth())}
                             if cls == 'PCB_TEXTBOX' and item.IsBorderEnabled() else {})})
        elif cls == 'PCB_SHAPE':
            graphics.append({**base, 'shape': item.ShowShape(), 'width_nm': int(item.GetWidth()),
                             'filled': bool(item.IsAnyFill())})
        else:
            other.append(base)
    return {key: sorted(value, key=lambda i: i['uuid'])
            for key, value in (('texts', texts), ('graphics', graphics), ('other', other))}


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


def qualify_silk(records, inventory):
    """Grade silk minima only where the saved silk is what the native rule measures.

    Unqualified saved silk keeps the record unknown; that takes precedence over a
    proved violation, which stays visible as native_status and in the findings.
    """
    for record in records:
        key = record['expected'].get('key')
        if key not in SILK or record['execution'] != 'ok':
            continue
        silk = inventory.get('silk')
        if silk is None:
            record.update(native_status=record['status'], status='not_evaluated', execution='unsupported',
                          code='silk_inventory_missing', reason='silkscreen inventory missing')
            continue
        texts = {t['uuid']: t for t in silk['texts']}
        cited = {i['uuid'] for f in record['native_findings'] for i in f['items']}
        refusals = []
        # Supplier applicability comes first: a measurement outside its scope proves nothing.
        scope, dielectrics = record.get('applicability'), inventory.get('dielectrics')
        if scope is None:
            refusals.append(('silk_applicability_unsourced', 'no verified supplier base-material scope '
                             'on the cited source page', []))
        elif not dielectrics or any(not d['materials'] or set(d['materials']) - BASE_MATERIALS.keys()
                                    for d in dielectrics):
            refusals.append(('silk_material_unverified', 'saved board stackup names no dielectric material '
                             'qualified to a supplier base-material name', dielectrics or []))
        else:
            outside = [d for d in dielectrics if {BASE_MATERIALS[m] for m in d['materials']}
                       - set(scope['base_materials'])]
            if outside:
                refusals.append(('silk_applicability_mismatch', 'saved dielectric material outside the '
                                 'cited supplier base-material scope', outside))
        if silk['other']:
            refusals.append(('silk_item_unqualified', 'silkscreen items outside the qualified text and '
                             'stroked-shape classes', silk['other']))
        hidden = [texts[u] for u in sorted(cited & texts.keys()) if not texts[u]['visible']]
        if hidden:
            refusals.append(('hidden_silk_text', 'native rule graded hidden silkscreen text', hidden))
        if key == 'min_pad_to_silk_mm':
            tented = [f for f in record['native_findings'] if f['type'] == 'silk_overlap']
            if tented:
                refusals.append(('pad_to_silk_tented_pad', 'silk near a pad without a mask opening '
                                 'on that side', tented))
        else:
            fonts = [t for t in silk['texts'] if t['visible'] and (not t['stroke_font'] or t['knockout'])]
            if fonts:
                refusals.append(('silk_text_unqualified', 'outline-font or knockout silkscreen text', fonts))
        violations = []
        if key == 'min_silk_line_width_mm':
            # Native text_thickness grades characters; saved stroke widths grade the rest.
            limit = Fraction(str(record['expected']['value'])) * 1_000_000
            strokes = [*silk['graphics'], *({'uuid': t['uuid'], 'class': t['class'], 'layers': t['layers'],
                                             'width_nm': t['border_width_nm'], 'filled': False}
                                            for t in silk['texts'] if 'border_width_nm' in t)]
            unmeasured = [g for g in strokes if g['filled'] or g['width_nm'] <= 0]
            if unmeasured:
                refusals.append(('silk_graphic_unmeasured', 'filled or zero-width silkscreen shapes '
                                 'have no measured line width', unmeasured))
            violations = [g for g in strokes if not g['filled'] and 0 < g['width_nm'] < limit]
            if violations:
                record.update(status='violation', silk_graphic_violations=violations,
                              offender_ids=sorted(g['uuid'] for g in violations))
        if refusals:
            code, reason, offenders = refusals[0]
            record.update(native_status=record['status'], status='not_evaluated', execution='unsupported',
                          code=code, reason=reason, unqualified_silk=offenders,
                          silk_refusals=[{'code': c, 'reason': r, 'items': i} for c, r, i in refusals])
    return records


def classify_preferred(records, inventory):
    """Only proved legal features can become extra charges, never defects.

    The native inventory is required: an edge clearance is satisfied only against
    one qualified outline, because absent or malformed Edge.Cuts prove nothing.
    """
    qualify_silk(records, inventory)
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
        # A refusal names the exact captured table (and its source hash) it checked.
        source = record['expected']['source']
        table = {**options, 'source_path': source['path'], 'source_sha256': source['sha256'],
                 'as_of': source['as_of']}
        if unknown is None or unknown:
            record.update(status='not_evaluated', execution='unsupported',
                          code='via_drill_off_table' if unknown else 'via_inventory_missing',
                          reason='via drill outside sourced options' if unknown else 'via drill inventory missing',
                          unsupported_vias=unknown, drill_table=table)
        elif below and (record['status'] != 'violation' or
                        not {v['uuid'] for v in below} <= offender_ids(record)):
            record.update(status='not_evaluated', execution='unsupported',
                          code='below_minimum_via_uncited',
                          reason='below-minimum via drill not cited by native DRC', unsupported_vias=[],
                          drill_table=table)
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
    uncovered = hole_classes_without_table(inventory)
    for record in records:
        # A captured drill table covers via holes only. A board that also drills pads
        # grades its vias as before, but the uncovered hole classes keep the table's
        # record, and so the check, unknown. Earlier refusal codes stay first.
        if 'drill_table' not in record and (record['expected'].get('drill_options') is None
                                            or record['execution'] != 'ok'):
            continue
        if uncovered:
            record['unsupported_hole_classes'] = uncovered
            if record['execution'] == 'ok':
                source = record['expected']['source']
                record.update(via_status=record['status'], status='not_evaluated', execution='unsupported',
                              code='hole_class_without_drill_table',
                              reason='drilled ' + ', '.join(c['hole_class'] for c in uncovered)
                                     + ' holes have no captured drill table',
                              drill_table={**record['expected']['drill_options'], 'source_path': source['path'],
                                           'source_sha256': source['sha256'], 'as_of': source['as_of']})
    return records


def hole_classes_without_table(inventory):
    """Observed non-via hole classes with their saved pad UUIDs and requested drill sizes.

    Captured drill tables (drill_options) list via drills only, so every drilled pad
    class is uncovered. Callers without a hole inventory (unit records) observe none.
    """
    classes = defaultdict(list)
    for hole in inventory.get('holes', []):
        classes[hole['hole_class']].append(hole)
    return [{'hole_class': name, 'holes': holes} for name, holes in sorted(classes.items())]


def check_fab_profile(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        source = {'path': ctx.params['fab_profile'], 'sha256': digest(ctx.inputs / ctx.params['fab_profile'])}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    records = run_compiled(ctx, descriptor['compiled'], source)
    ctx.result['details']['extra_charges'] = [r for r in records if r['status'] == 'extra_charge']
