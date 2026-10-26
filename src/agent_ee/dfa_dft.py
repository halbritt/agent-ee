"""AGENTEE-64 assembly and test access from declared intent; one criterion per check.

`connector-orientation/1`, `testpoint-access/1` and `testpoint-coverage/1` are
pure predicates over the saved-board placement probe (integer nanometres, exact
rationals) and the declarations in the check's own scope parameters. Nothing is
inferred from reference prefixes, symbol classes or footprint names. The
coverage criterion also derives `bringup_plan.json`, whose bench-bound
measurements carry the exact `agent-ee.bench/1` sample identity of an executed
prediction of the same candidate. Values the scope does not declare stay
unknown; there are no default current limits or tolerances.
"""

from .check_registry import register as _register_check
from fractions import Fraction
import math
import re

from .placement import edges, on_segment, qualified_polygons, validate_capture
from .placement_predicates import (AXES, _point_nm, board_axis, decimal_text, mm, native_rotation,
                                   point_segment2, proven_outline, verdict)

CRITERIA = ('connector-orientation/1', 'testpoint-access/1', 'testpoint-coverage/1')
# Footprint-local direction the mating part arrives from (KiCad y grows downward).
LOCAL_INSERTION = {'from_left': 'x-', 'from_right': 'x+', 'from_up': 'y-', 'from_down': 'y+'}
VERTICAL_INSERTION = {'from_top': 'F', 'from_bottom': 'B'}
INSERTIONS = tuple(LOCAL_INSERTION) + tuple(VERTICAL_INSERTION)
EXPOSED = {'F': ('F.Cu', 'F.Mask'), 'B': ('B.Cu', 'B.Mask')}
FIELDS = {'connector-orientation/1': ({'connectors'}, set()),
          'testpoint-access/1': ({'test_points'}, set()),
          'testpoint-coverage/1': ({'rails', 'first_power'}, {'test_points', 'probe_pads', 'measurements'})}
RAIL_ID = re.compile(r'[a-z0-9][a-z0-9_.-]{0,31}\Z')
# Bench ingestion (empirical.ingest) admits observations of these executed kinds only.
BENCH_KINDS = ('measurements', 'rc_transient')
PLAN_SCHEMA = 'agent-ee.bringup-plan/1'


# ------------------------------------------------------------- declarations

def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def _exact(value, keys, optional, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    missing, unknown = set(keys) - set(value), set(value) - set(keys) - set(optional)
    if missing or unknown:
        raise ValueError(f'{label}: unknown {sorted(unknown)}, missing {sorted(missing)}')
    return value


def _positive(value, label, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be finite numeric')
    if value < 0 or (value == 0 and not zero):
        raise ValueError(f'{label} must be positive')
    return value


def _references(values, label, *, nonempty):
    if not isinstance(values, list) or (nonempty and not values):
        raise ValueError(f'{label} must be a {"nonempty " if nonempty else ""}list of references')
    for value in values:
        _text(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f'duplicate {label}')
    return values


def parameters(params):
    """Validate the inline declaration; raises ValueError. Returns the criterion."""
    if not isinstance(params, dict):
        raise ValueError('dfa_dft params must be an object')
    criterion = params.get('criterion')
    if criterion not in CRITERIA:
        raise ValueError(f'dfa_dft requires one criterion of {list(CRITERIA)}')
    required, optional = FIELDS[criterion]
    _exact(params, required | {'criterion'}, optional, f'dfa_dft {criterion} params')
    if criterion == 'connector-orientation/1':
        connectors = params['connectors']
        if not isinstance(connectors, list) or not connectors:
            raise ValueError('connectors must be a nonempty list')
        _references([_exact(c, {'reference'}, {'insertion', 'cable_insertion_mm'}, 'connector')['reference']
                     for c in connectors], 'connector reference', nonempty=True)
        for connector in connectors:
            if 'insertion' in connector and 'cable_insertion_mm' in connector:
                raise ValueError('declare insertion or cable_insertion_mm, not both')
            if 'insertion' in connector and connector['insertion'] not in INSERTIONS:
                raise ValueError(f'connector insertion must be one of {list(INSERTIONS)}')
            if 'cable_insertion_mm' in connector:
                _point_nm(connector['cable_insertion_mm'], 'cable_insertion_mm')
        return criterion
    _references(params.get('test_points', []), 'test point', nonempty=criterion == 'testpoint-access/1')
    if criterion == 'testpoint-access/1':
        return criterion
    pads = params.get('probe_pads', [])
    if not isinstance(pads, list):
        raise ValueError('probe_pads must be a list')
    keys = [(_text(_exact(p, {'reference', 'pad'}, set(), 'probe pad')['reference'], 'probe pad reference'),
             _text(p['pad'], 'probe pad number')) for p in pads]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate probe pad')
    rails = params['rails']
    if not isinstance(rails, list) or not rails:
        raise ValueError('rails must be a nonempty list')
    ids = []
    for rail in rails:
        _exact(rail, {'id', 'net', 'return_net'}, {'nominal_v', 'tolerance_v', 'measurement'}, 'rail')
        if not isinstance(rail['id'], str) or not RAIL_ID.fullmatch(rail['id']):
            raise ValueError('rail id must match [a-z0-9][a-z0-9_.-]{0,31}')
        ids.append(rail['id'])
        if _text(rail['net'], 'rail net') == _text(rail['return_net'], 'rail return_net'):
            raise ValueError('rail net and return_net must differ')
        if 'nominal_v' in rail:
            _positive(rail['nominal_v'], 'nominal_v')
        if 'tolerance_v' in rail:
            _positive(rail['tolerance_v'], 'tolerance_v', zero=True)
        if 'measurement' in rail:
            _exact(rail['measurement'], {'check', 'metric'}, set(), 'rail measurement')
            _text(rail['measurement']['check'], 'rail measurement check')
            _text(rail['measurement']['metric'], 'rail measurement metric')
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate rail id')
    first = _exact(params['first_power'], {'rail'}, {'current_limit_a'}, 'first_power')
    if first['rail'] not in ids:
        raise ValueError('first_power names an undeclared rail')
    if 'current_limit_a' in first:
        _positive(first['current_limit_a'], 'current_limit_a')
    measurements = params.get('measurements', [])
    if not isinstance(measurements, list):
        raise ValueError('measurements must be a list')
    seen = set()
    for item in measurements:
        _exact(item, {'check', 'metric', 'net', 'return_net'}, set(), 'measurement')
        key = (_text(item['check'], 'measurement check'), _text(item['metric'], 'measurement metric'))
        if key in seen:
            raise ValueError('duplicate measurement declaration')
        seen.add(key)
        if _text(item['net'], 'measurement net') == _text(item['return_net'], 'measurement return_net'):
            raise ValueError('measurement net and return_net must differ')
    return criterion


def acceptance_measurements(scope, spec, params):
    """Bounded metrics of earlier acceptance-mapped checks and the nets a bench probes for them.

    Native RC checks name their nets; adapter metrics need an explicit
    `measurements` declaration. A missing one is returned as a gap, never guessed.
    """
    specs = scope.get('checks', [])
    earlier = specs[:specs.index(spec)] if spec in specs else []
    requirements = scope.get('baseline', {}).get('requirements', [])
    accepted = {cid for r in requirements if r.get('intent', 'acceptance') != 'informational'
                for cid in r.get('checks', [])}
    declared = {(m['check'], m['metric']): m for m in params.get('measurements', [])}
    items, gaps = [], []
    for source in earlier:
        cid, kind, sp = source.get('id'), source.get('kind'), source.get('params', {})
        if cid not in accepted or kind not in BENCH_KINDS:
            continue
        if kind == 'rc_transient':
            for m in sp.get('measurements', []):
                bounds = {k: m[k] for k in ('min', 'max') if m.get(k) is not None}
                if bounds:
                    items.append({'check': cid, 'metric': str(m['name']).lower(), 'kind': kind,
                                  'net': sp['output_net'], 'return_net': sp['ground_net'],
                                  'stimulus_net': sp['input_net'], 'expected': {'unit': 'V', **bounds},
                                  'source': f'/checks/{specs.index(source)}/params/measurements'})
            continue
        for metric, limit in sorted(sp.get('limits', {}).items()):
            entry = declared.pop((cid, metric), None)
            if entry is None:
                gaps.append({'kind': 'measured_net_undeclared', 'check': cid, 'metric': metric,
                             'reason': 'acceptance-measured adapter metric has no declared probe net'})
                continue
            items.append({'check': cid, 'metric': metric, 'kind': kind, 'net': entry['net'],
                          'return_net': entry['return_net'], 'stimulus_net': None,
                          'expected': {k: limit[k] for k in ('unit', 'min', 'max') if k in limit},
                          'source': f'/checks/{specs.index(source)}/params/limits/{metric}'})
    for check, metric in sorted(declared):
        gaps.append({'kind': 'measurement_not_acceptance_bounded', 'check': check, 'metric': metric,
                     'reason': 'declared measurement names no earlier acceptance-bounded adapter metric'})
    return items, gaps


def capability(params, scope, spec, prerequisite):
    criterion = parameters(params)
    if criterion != 'testpoint-coverage/1':
        return [f'dfa-dft:{criterion}']
    for rail in params['rails']:
        if 'measurement' in rail:
            source = prerequisite(rail['measurement']['check'], ('measurements',))
            if rail['measurement']['metric'] not in source.get('params', {}).get('limits', {}):
                raise ValueError('rail measurement names an undeclared adapter metric')
    items, gaps = acceptance_measurements(scope, spec, params)
    if gaps:
        raise ValueError('; '.join(f"{g['check']}:{g['metric']}: {g['reason']}" for g in gaps))
    for check in sorted({item['check'] for item in items}):
        prerequisite(check, BENCH_KINDS)
    return [f'dfa-dft:{criterion}']


# ---------------------------------------------------------------- geometry

def locate(point, polygon):
    """Exact point location in a simple polygon: inside, boundary or outside."""
    x, y = point
    inside = False
    for a, b in edges(polygon):
        if on_segment(a, b, point):
            return 'boundary'
        if (a[1] > y) != (b[1] > y):
            crossing = a[0] + Fraction((y - a[1]) * (b[0] - a[0]), b[1] - a[1])
            if x < crossing:
                inside = not inside
    return 'inside' if inside else 'outside'


def outward_edges(polygon):
    """Outline edges with their outward axis (None if not axis-aligned); polygon is CCW."""
    out = []
    for a, b in edges(polygon):
        dx, dy = b[0] - a[0], b[1] - a[1]
        normal = (int(math.copysign(1, dy)) if dy else 0, -int(math.copysign(1, dx)) if dx else 0)
        axis = next((n for n, v in AXES.items() if v == normal), None) if (dx == 0) != (dy == 0) else None
        out.append({'start': a, 'end': b, 'outward': axis})
    return out


def nearest_edge(point, polygon):
    """The outward axis of the nearest outline edge, or (None, reason)."""
    records = [(point_segment2(point, e['start'], e['end']), e) for e in outward_edges(polygon)]
    best = min(d for d, _ in records)
    nearest = [e for d, e in records if d == best]
    axes = {e['outward'] for e in nearest}
    measured = {'distance_mm': round(math.sqrt(best) / 10**6, 6),
                'edges': [{'start_mm': [mm(v) for v in e['start']], 'end_mm': [mm(v) for v in e['end']],
                           'outward': e['outward']} for e in nearest]}
    if None in axes:
        return None, measured, 'nearest outline edge is not axis-aligned'
    if len(axes) != 1:
        return None, measured, 'equidistant from outline edges facing different directions'
    return axes.pop(), measured, None


def local_insertion(declaration):
    """Footprint-local arrival axis from the declaration or a declared cable-insertion point."""
    if 'insertion' in declaration:
        return declaration['insertion'], LOCAL_INSERTION.get(declaration['insertion']), None
    if 'cable_insertion_mm' not in declaration:
        return None, None, 'no declared or derivable insertion direction'
    x, y = _point_nm(declaration['cable_insertion_mm'], 'cable_insertion_mm')
    if abs(x) == abs(y):
        return None, None, 'cable insertion point does not select one footprint axis'
    axis = ('x+' if x > 0 else 'x-') if abs(x) > abs(y) else ('y+' if y > 0 else 'y-')
    return next(k for k, v in LOCAL_INSERTION.items() if v == axis), axis, None


def connector_orientation(facts, connectors):
    """Each declared connector faces its nearest board edge unless mated from above or below."""
    try:
        outline, reason = proven_outline(facts), None
    except ValueError as exc:
        outline, reason = None, f'saved outline not proven: {exc}'
    results = []
    for declaration in connectors:
        ref = declaration['reference']
        record = {'reference': ref, 'uuid': None, 'declared': declaration, 'measured': {},
                  'violations': [], 'reasons': []}
        results.append(record)
        matches = [f for f in facts['footprints'] if f['ref'] == ref]
        if len(matches) != 1:
            record['reasons'].append('declared reference ' + ('missing' if not matches else 'duplicated'))
            record['status'] = 'unknown'
            continue
        fp = matches[0]
        record['uuid'] = fp['uuid']
        insertion, local, why = local_insertion(declaration)
        record['measured']['insertion'] = insertion
        if why:
            record['reasons'].append(why)
        elif insertion in VERTICAL_INSERTION:
            if fp['side'] != VERTICAL_INSERTION[insertion]:
                record['violations'].append({'kind': 'insertion_side', 'insertion': insertion, 'actual': fp['side'],
                                             'recommended_side': VERTICAL_INSERTION[insertion]})
        elif fp['side'] not in ('F', 'B'):
            record['reasons'].append('unsupported footprint side')
        else:
            rotation = native_rotation(fp['rotation_deg'])
            faces = board_axis(local, rotation, fp['side'])
            record['measured'].update(rotation_deg=decimal_text(rotation), local_axis=local, faces=faces,
                                      origin_mm=[mm(v) for v in fp['position_nm']])
            if faces is None:
                record['reasons'].append('facing undeterminable at a non-quarter-turn rotation')
            elif outline is None:
                record['reasons'].append(reason)
            else:
                required, nearest, why = nearest_edge(tuple(fp['position_nm']), outline)
                record['measured']['nearest_edge'] = nearest
                if why:
                    record['reasons'].append(why)
                elif faces != required:
                    record['violations'].append({'kind': 'facing', 'faces': faces, 'required': required,
                                                 'recommended': required})
        record['status'] = verdict(record['violations'], record['reasons'])
    defects = [r for r in results if r['status'] == 'defect']
    unknown = [r for r in results if r['status'] == 'unknown']
    return {'criterion': 'connector-orientation/1', 'status': verdict(defects, unknown),
            'defects': defects, 'unknown': unknown, 'info': [], 'connectors': results}


def _obstacles(footprints, side, own_uuid):
    """Qualified same-side courtyards of every other footprint, info and unknown records."""
    polygons, info, unknown = [], [], []
    for other in footprints:
        if other['uuid'] == own_uuid:
            continue
        records = other.get('courtyards')
        if not isinstance(records, dict):
            unknown.append({'kind': 'courtyard_inventory_missing', 'ref': other['ref']})
        elif not records:
            info.append({'kind': 'missing_courtyard', 'ref': other['ref'], 'uuid': other['uuid'],
                         'reason': 'footprint has no courtyard; it is not tested as a probe obstruction'})
        elif side in records:
            try:
                polygons.extend((other['ref'], p) for p in qualified_polygons(records[side]))
            except ValueError as exc:
                unknown.append({'kind': 'courtyard_unproven', 'ref': other['ref'], 'side': side, 'reason': str(exc)})
    return polygons, info, unknown


def pad_access(fp, pad, footprints):
    """Probe access of one pad from each outer side where its copper is exposed through mask."""
    layers = pad.get('layers')
    position = pad.get('position_nm')
    base = {'reference': fp['ref'], 'pad': pad['number'], 'pad_uuid': pad['uuid'], 'net': pad.get('net')}
    if (not isinstance(layers, list) or not isinstance(position, list) or len(position) != 2
            or any(type(v) is not int for v in position)):
        return {**base, 'status': 'unknown', 'sides': [], 'info': [],
                'reasons': ['pad record lacks layers or integer position']}
    sides, info, reasons = [], [], []
    for side, needed in EXPOSED.items():
        if not set(needed) <= set(layers):
            continue
        polygons, side_info, side_unknown = _obstacles(footprints, side, fp['uuid'])
        info.extend(side_info)
        blockers, boundary = [], []
        for ref, polygon in polygons:
            where = locate(tuple(position), polygon)
            if where == 'inside':
                blockers.append(ref)
            elif where == 'boundary':
                boundary.append(ref)
        unknown = side_unknown + [{'kind': 'courtyard_boundary', 'ref': r} for r in sorted(set(boundary))]
        status = 'defect' if blockers else 'unknown' if unknown else 'pass'
        sides.append({'side': side, 'status': status, 'under_courtyards': sorted(set(blockers)), 'unknown': unknown})
    if not sides:
        reasons.append('pad copper is not exposed through solder mask on an outer layer')
    statuses = {s['status'] for s in sides}
    status = 'pass' if 'pass' in statuses else 'unknown' if 'unknown' in statuses else 'defect'
    return {**base, 'position_mm': [mm(v) for v in position], 'status': status, 'sides': sides,
            'info': info, 'reasons': reasons}


def _unique_info(records):
    seen, out = set(), []
    for record in records:
        if record['uuid'] not in seen:
            seen.add(record['uuid'])
            out.append(record)
    return sorted(out, key=lambda r: (r['ref'], r['uuid']))


def testpoint_records(facts, references):
    """Declared test points: every pad's probe access, with the footprint's overall status."""
    results, info = [], []
    for ref in references:
        matches = [f for f in facts['footprints'] if f['ref'] == ref]
        record = {'reference': ref, 'uuid': None, 'pads': [], 'reasons': []}
        results.append(record)
        if len(matches) != 1:
            record['reasons'].append('declared reference ' + ('missing' if not matches else 'duplicated'))
            record['status'] = 'unknown'
            continue
        fp = matches[0]
        record.update(uuid=fp['uuid'], side=fp.get('side'), fpid=fp.get('fpid'))
        record['pads'] = [pad_access(fp, pad, facts['footprints']) for pad in fp['pads']]
        for pad in record['pads']:
            info.extend(pad['info'])
        statuses = {p['status'] for p in record['pads']}
        if not record['pads']:
            record['reasons'].append('test point footprint has no pads')
        record['status'] = ('pass' if 'pass' in statuses else 'unknown' if 'unknown' in statuses or not statuses
                            else 'defect')
    return results, _unique_info(info)


def testpoint_access(facts, references):
    results, info = testpoint_records(facts, references)
    defects = [r for r in results if r['status'] == 'defect']
    unknown = [r for r in results if r['status'] == 'unknown']
    return {'criterion': 'testpoint-access/1', 'status': verdict(defects, unknown), 'defects': defects,
            'unknown': unknown, 'info': info, 'test_points': results}


# --------------------------------------------------------------- coverage

def probe_inventory(facts, params):
    """Accessible declared probe pads by net, plus declaration mismatches and info."""
    points, info = testpoint_records(facts, params.get('test_points', []))
    notes = []
    by_net = {}
    for tp in points:
        if tp['status'] == 'unknown' and not tp['pads']:
            notes.append({'kind': 'declared_test_point_unavailable', 'reference': tp['reference'],
                          'reasons': tp['reasons']})
        for pad in tp['pads']:
            by_net.setdefault(pad['net'], []).append({**pad, 'kind': 'test_point'})
    for declared in params.get('probe_pads', []):
        fps = [f for f in facts['footprints'] if f['ref'] == declared['reference']]
        pads = [p for f in fps for p in f['pads'] if p['number'] == declared['pad']]
        if len(fps) != 1 or not pads:
            notes.append({'kind': 'declared_probe_pad_unavailable', **declared})
            continue
        for pad in pads:
            access = pad_access(fps[0], pad, facts['footprints'])
            info.extend(access['info'])
            by_net.setdefault(pad['net'], []).append({**access, 'kind': 'probe_pad'})
    return by_net, _unique_info(info), notes


def undeclared_candidates(facts, net, declared):
    """Diagnostics only: exposed pads on the net that the owner did not declare as probe points."""
    out = []
    for fp in facts['footprints']:
        for pad in fp['pads']:
            layers = pad.get('layers') or []
            if pad.get('net') == net and (fp['ref'], pad['number']) not in declared and any(
                    set(needed) <= set(layers) for needed in EXPOSED.values()):
                out.append({'reference': fp['ref'], 'pad': pad['number'], 'fpid': fp.get('fpid')})
    return sorted(out, key=lambda c: (c['reference'], c['pad']))


def required_nets(params, items):
    nets = {}
    for rail in params['rails']:
        nets.setdefault(rail['net'], []).append(f"rail:{rail['id']}")
        nets.setdefault(rail['return_net'], []).append(f"rail-return:{rail['id']}")
    for item in items:
        name = f"{item['check']}:{item['metric']}"
        nets.setdefault(item['net'], []).append(f'measure:{name}')
        nets.setdefault(item['return_net'], []).append(f'measure-return:{name}')
        if item['stimulus_net']:
            nets.setdefault(item['stimulus_net'], []).append(f'stimulus:{name}')
    return {net: sorted(set(roles)) for net, roles in nets.items()}


def testpoint_coverage(facts, params, items, gaps):
    by_net, info, notes = probe_inventory(facts, params)
    declared = {(tp, p['number']) for tp in params.get('test_points', [])
                for f in facts['footprints'] if f['ref'] == tp for p in f['pads']}
    declared |= {(p['reference'], p['pad']) for p in params.get('probe_pads', [])}
    probes, defects, unknown = [], [], list(gaps)
    for net, roles in sorted(required_nets(params, items).items()):
        pads = sorted(by_net.get(net, []), key=lambda p: (p['kind'] != 'test_point', p['reference'], p['pad']))
        accessible = [p for p in pads if p['status'] == 'pass']
        status = 'pass' if accessible else 'unknown' if any(p['status'] == 'unknown' for p in pads) else 'defect'
        record = {'id': f'probe:{net}', 'net': net, 'roles': roles, 'status': status,
                  'accessible': [{k: p[k] for k in ('kind', 'reference', 'pad', 'position_mm')} for p in accessible],
                  'declared_pads': [{k: p.get(k) for k in ('kind', 'reference', 'pad', 'status', 'reasons')} for p in pads]}
        if status != 'pass':
            record['undeclared_exposed_pads'] = undeclared_candidates(facts, net, declared)
        probes.append(record)
        if status == 'defect':
            defects.append({'kind': 'net_without_probe_access', 'net': net, 'roles': roles,
                            'reason': f'net {net} has no accessible declared test point or exposed probe pad'})
        elif status == 'unknown':
            unknown.append({'kind': 'probe_access_unproven', 'net': net, 'roles': roles})
    return probes, defects, unknown, info, notes


# ------------------------------------------------------------ bring-up plan

def _executed_metric(prior, check, metric, candidate_id):
    """The executed source metric's bench identity, or (None, reason)."""
    record = prior.get(check)
    if record is None:
        return None, 'source check has no result'
    if record.get('candidate_id') != candidate_id or record.get('execution') != 'ok':
        return None, f"source check was {record.get('execution')} for this candidate"
    value = (record.get('details') or {}).get('metrics', {}).get(metric)
    if not isinstance(value, dict) or not isinstance(value.get('unit'), str) or not isinstance(value.get('condition'), str):
        return None, 'source check retains no executed metric of that name'
    return {'check_id': check, 'metric': metric, 'unit': value['unit'], 'condition': value['condition']}, None


def bringup_plan(facts, params, items, probes, prior, candidate_id, check_id):
    """One probe per rail and interface net, current-limited first power, scope-only values."""
    unknown, probe_ids = [], {p['net']: p['id'] for p in probes}
    rails = {r['id']: r for r in params['rails']}
    first = params['first_power']
    rail = rails[first['rail']]
    missing = [k for k, present in (('current_limit_a', 'current_limit_a' in first),
                                    ('nominal_v', 'nominal_v' in rail)) if not present]
    for key in missing:
        unknown.append({'kind': 'first_power_value_undeclared', 'field': key,
                        'reason': f'scope declares no {key} for first power; no default is assumed'})
    first_power = {'rail': rail['id'], 'net': rail['net'], 'return_net': rail['return_net'],
                   'supply_v': rail.get('nominal_v'), 'current_limit_a': first.get('current_limit_a'),
                   'probes': [probe_ids[rail['net']], probe_ids[rail['return_net']]],
                   'status': 'unknown' if missing else 'declared', 'missing': missing,
                   'source': {'current_limit_a': '/params/first_power/current_limit_a',
                              'supply_v': f"/params/rails/{params['rails'].index(rail)}/nominal_v"}}
    measurements = []
    for index, r in enumerate(params['rails']):
        lacking = [k for k in ('nominal_v', 'tolerance_v') if k not in r]
        entry = {'id': f"rail:{r['id']}", 'kind': 'rail', 'net': r['net'], 'return_net': r['return_net'],
                 'probes': [probe_ids[r['net']], probe_ids[r['return_net']]],
                 'expected': {'unit': 'V', 'nominal': r.get('nominal_v'), 'tolerance': r.get('tolerance_v'),
                              'source': f'/params/rails/{index}'},
                 'missing': lacking, 'bench': None}
        if 'measurement' in r:
            entry['bench'], why = _executed_metric(prior, r['measurement']['check'], r['measurement']['metric'],
                                                   candidate_id)
            if why:
                lacking.append('bench_binding')
                entry['bench_reason'] = why
        else:
            entry['bench_reason'] = 'no executed prediction is declared for this rail; procedural observation only'
        for key in lacking:
            unknown.append({'kind': 'rail_value_undeclared' if key != 'bench_binding' else 'bench_binding_unavailable',
                            'rail': r['id'], 'field': key})
        entry['status'] = 'unknown' if lacking else 'ready'
        measurements.append(entry)
    for item in items:
        bench, why = _executed_metric(prior, item['check'], item['metric'], candidate_id)
        entry = {'id': f"{item['check']}:{item['metric']}", 'kind': 'acceptance', 'net': item['net'],
                 'return_net': item['return_net'], 'stimulus_net': item['stimulus_net'],
                 'probes': [probe_ids[n] for n in (item['stimulus_net'], item['net'], item['return_net']) if n],
                 'expected': {**item['expected'], 'nominal': None, 'tolerance': None, 'source': item['source']},
                 'missing': [], 'bench': bench, 'status': 'ready' if bench else 'unknown'}
        if why:
            entry['missing'].append('bench_binding')
            entry['bench_reason'] = why
            unknown.append({'kind': 'bench_binding_unavailable', 'measurement': entry['id'], 'reason': why})
        measurements.append(entry)
    steps = [{'order': 1, 'action': 'first_power', 'rail': rail['id'],
              'instruction': 'apply the declared supply through a current-limited source set to the declared '
                             'limit; stop on limit before any measurement',
              'requires': ['supply_v', 'current_limit_a']}]
    steps += [{'order': i + 2, 'action': 'measure', 'measurement': m['id'], 'probes': m['probes']}
              for i, m in enumerate(measurements)]
    plan = {'schema': PLAN_SCHEMA, 'candidate_id': candidate_id, 'board_sha256': facts['board_sha256'],
            'check_id': check_id, 'bench_report_schema': 'agent-ee.bench/1', 'first_power': first_power,
            'probes': probes, 'measurements': measurements, 'steps': steps,
            'status': 'unknown' if unknown else 'complete', 'unknown': unknown,
            'limitations': ['no instrument is driven; cable bend radius and enclosure are not modelled',
                            'expected values come only from the scope; absent values are unknown']}
    return plan, unknown


def bench_sample(plan, measurement_id, value, uncertainty):
    """The `agent-ee.bench/1` raw sample for one bench-bound plan measurement."""
    entry = next((m for m in plan['measurements'] if m['id'] == measurement_id), None)
    if entry is None or entry.get('bench') is None:
        raise ValueError(f'plan measurement {measurement_id!r} has no executed bench binding')
    return {**entry['bench'], 'value': value, 'uncertainty': uncertainty}


def plan_measurement(plan, candidate_id, sample):
    """Inverse of bench_sample: the plan measurement ID a bench sample answers."""
    if plan.get('candidate_id') != candidate_id:
        raise ValueError('bench sample belongs to a different candidate than the plan')
    key = tuple(sample.get(k) for k in ('check_id', 'metric', 'unit', 'condition'))
    matches = [m['id'] for m in plan['measurements'] if m.get('bench') and tuple(
        m['bench'][k] for k in ('check_id', 'metric', 'unit', 'condition')) == key]
    if len(matches) != 1:
        raise ValueError('bench sample matches no unique plan measurement')
    return matches[0]


# ------------------------------------------------------------------ check

def _prior_results(ctx, checks):
    """Earlier retained results of this run; a missing one leaves its binding unknown."""
    from .storage import read_json
    return {cid: read_json(ctx.run_dir / 'checks' / f'{cid}.json') for cid in checks
            if (ctx.run_dir / 'checks' / f'{cid}.json').is_file()}


def check_dfa_dft(ctx):
    from .checks import CheckError
    from .storage import digest, write_json
    try:
        criterion = parameters(ctx.params)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    board = ctx.scope.get('board')
    if not isinstance(board, str) or not (ctx.inputs / board).is_file():
        raise CheckError('failed', 'no captured board to probe')
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe', ctx.candidate_path(board),
                 '--out', '/work/placement-probe.json'], timeout=600)
    if not (ctx.work / 'placement-probe.json').is_file():
        raise CheckError('failed', 'saved-board probe produced no data')
    ctx.evidence('placement-probe.json')
    facts = ctx.load_json('placement-probe.json', 'placement probe')
    if facts.get('board_sha256') != ctx.manifest['files'].get(board):
        raise CheckError('malformed', 'placement probe does not bind the captured board')
    try:
        validate_capture(facts)
    except ValueError as exc:
        raise CheckError('failed', f'saved-board probe data unusable: {exc}') from exc
    plan = None
    try:
        if criterion == 'connector-orientation/1':
            result = connector_orientation(facts, ctx.params['connectors'])
        elif criterion == 'testpoint-access/1':
            result = testpoint_access(facts, ctx.params['test_points'])
        else:
            items, gaps = acceptance_measurements(ctx.scope, ctx.spec, ctx.params)
            sources = sorted({i['check'] for i in items} | {r['measurement']['check'] for r in ctx.params['rails']
                                                            if 'measurement' in r})
            prior = _prior_results(ctx, sources)
            probes, defects, unknown, info, notes = testpoint_coverage(facts, ctx.params, items, gaps)
            plan, plan_unknown = bringup_plan(facts, ctx.params, items, probes, prior,
                                              ctx.manifest['candidate_id'], ctx.cid)
            plan['sources'] = {cid: digest(ctx.run_dir / 'checks' / f'{cid}.json') for cid in sorted(prior)}
            unknown = unknown + plan_unknown
            result = {'criterion': criterion, 'status': verdict(defects, unknown), 'defects': defects,
                      'unknown': unknown, 'info': info, 'notes': notes, 'probes': probes}
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'dfa_dft input unusable: {exc}') from exc
    record = {'schema': 'agent-ee.dfa-dft/1', 'candidate_id': ctx.manifest['candidate_id'],
              'board_sha256': facts['board_sha256'], 'pcbnew_version': facts.get('pcbnew_version'), **result}
    if plan is not None:
        write_json(ctx.work / 'bringup_plan.json', plan)
        ctx.evidence('bringup_plan.json')
        record['bringup_plan'] = {'path': f'work/{ctx.cid}/bringup_plan.json',
                                  'sha256': digest(ctx.work / 'bringup_plan.json'), 'status': plan['status']}
    write_json(ctx.work / 'dfa-dft.json', record)
    ctx.evidence('dfa-dft.json')
    ctx.result['details'].update({k: v for k, v in record.items() if k != 'schema'})
    ctx.result['limitations'].append(
        'declared assembly/test access over saved courtyards, pads and outline only; no cable bend radius, '
        'enclosure, probe-tip geometry or instrument control')
    if result['info']:
        ctx.result['limitations'].append(
            f"{len(result['info'])} footprint(s) without a courtyard were not tested as probe obstructions")
    status = result['status']
    if status == 'unknown':
        raise CheckError('unsupported', f'{criterion} coverage incomplete: {len(result["unknown"])} unknown item(s)')
    ctx.result.update(execution='ok', outcome='defect' if status == 'defect' else 'pass',
                      summary=f"{criterion}: {len(result['defects'])} proved violation(s)")


def describe_dfa_dft(ctx):
    from .dfa_dft import capability as dfa_dft_capability
    ctx.captured(ctx.scope.get('board'))
    ctx.result['predicates'] = dfa_dft_capability(ctx.params, ctx.scope, ctx.spec, ctx.prerequisite)


if __name__ != '__main__':
    _register_check('dfa_dft', describe_dfa_dft, check_dfa_dft, accepts_criterion=True)
