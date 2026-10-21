"""Declared pad-centre locality: grading and a distinct partial-placement constraint.

Limits and citations are supplied by the engineer, never inferred from a net name.
Distances use native copper-land centroids, not anchors, routed length or loop inductance.
"""
from fractions import Fraction
import math
from uuid import UUID

SCHEMA = 'agent-ee.electrical-locality/1'
CRITERION = 'electrical-locality/1'


def validate(document):
    if (not isinstance(document, dict) or set(document) != {'schema', 'obligations'}
            or document['schema'] != SCHEMA or not isinstance(document['obligations'], list)
            or not document['obligations']):
        raise ValueError(f'expected {SCHEMA} with nonempty obligations')
    ids = set()
    for obligation in document['obligations']:
        if (not isinstance(obligation, dict) or set(obligation) - {
                'id', 'metric', 'supply', 'return', 'switch', 'kind', 'ic', 'inductor', 'return_net'}
                or not {'id', 'metric', 'supply', 'return'} <= obligation.keys()):
            raise ValueError('locality obligation needs id, metric, supply and return; optional switch')
        oid = obligation['id']
        if not isinstance(oid, str) or not oid.strip() or oid in ids:
            raise ValueError('locality obligation ids must be unique nonempty text')
        ids.add(oid)
        if not isinstance(obligation['metric'], str):
            raise ValueError('locality metric must be text')
        for role in roles(obligation):
            pair = obligation[role]
            if (not isinstance(pair, dict) or set(pair) - {'from', 'to', 'max_distance_nm', 'citation'}
                    or not {'from', 'to'} <= pair.keys()):
                raise ValueError('locality pair needs from/to, optionally max_distance_nm and citation')
            for endpoint in (pair['from'], pair['to']):
                if (not isinstance(endpoint, dict) or set(endpoint) - {'reference', 'pad', 'net', 'uuid'}
                        or not {'reference', 'pad', 'net'} <= endpoint.keys()
                        or any(not isinstance(v, str) or not v.strip() for v in endpoint.values())):
                    raise ValueError('locality endpoint needs explicit reference, pad and net; optional captured uuid')
                if 'uuid' in endpoint and str(UUID(endpoint['uuid'])) != endpoint['uuid']:
                    raise ValueError('locality uuid must use canonical captured spelling')
            if pair['from']['reference'] == pair['to']['reference']:
                raise ValueError('locality endpoints must name distinct footprints')
            if 'max_distance_nm' in pair and (type(pair['max_distance_nm']) is not int or pair['max_distance_nm'] < 0):
                raise ValueError('locality limit must be nonnegative integer nanometres')
            if 'citation' in pair and (not isinstance(pair['citation'], str) or not pair['citation'].strip()):
                raise ValueError('locality citation must be nonempty text')
        supply, ground = obligation['supply'], obligation['return']
        if supply['to']['reference'] != ground['to']['reference']:
            raise ValueError('supply and return must name the same capacitor reference')
        if any(supply[k]['reference'] == ground[k]['reference']
               and supply[k]['pad'] == ground[k]['pad'] for k in ('from', 'to')):
            raise ValueError('supply and return must select distinct physical pads')
        loop_fields = {'kind', 'ic', 'inductor', 'return_net'}
        if loop_fields & obligation.keys():
            if (not loop_fields <= obligation.keys() or obligation['kind'] != 'buck-output-loop'
                    or any(not isinstance(obligation[k], str) or not obligation[k].strip()
                           for k in ('ic', 'inductor', 'return_net'))):
                raise ValueError('buck-output-loop needs explicit kind, ic, inductor and return_net')
            if obligation['ic'] == obligation['inductor']:
                raise ValueError('loop IC and inductor must be distinct footprints')
            if supply['from']['reference'] != obligation['inductor']:
                raise ValueError('loop supply must start at the declared inductor')
            if ground['from']['reference'] != obligation['ic']:
                raise ValueError('loop return must start at the declared IC')
            if any(ground[k]['net'] != obligation['return_net'] for k in ('from', 'to')):
                raise ValueError('loop return must use the declared return_net')
            if any(supply[k]['net'] == obligation['return_net'] for k in ('from', 'to')):
                raise ValueError('loop supply and return nets must differ')
            if ('switch' in obligation
                    and obligation['switch']['to']['reference'] != obligation['inductor']):
                raise ValueError('loop switch must end at the declared inductor')
        if 'switch' in obligation and obligation['switch']['from']['reference'] != ground['from']['reference']:
            raise ValueError('switch pair must start at the declared IC')
    return document


def roles(obligation):
    return [r for r in ('supply', 'return', 'switch') if r in obligation]


def resolve(footprints, endpoint):
    fps = [f for f in footprints if f['ref'] == endpoint['reference']]
    if len(fps) != 1:
        raise ValueError('reference missing or duplicated')
    if 'uuid' in endpoint:
        pads = [p for f in footprints for p in f['pads'] if p.get('uuid') == endpoint['uuid']]
        if len(pads) != 1 or not any(p is pads[0] for p in fps[0]['pads']):
            raise ValueError('captured pad UUID missing, duplicated or belongs to another reference')
        if pads[0]['number'] != endpoint['pad']:
            raise ValueError('captured pad UUID number differs from declaration')
    else:
        pads = [p for p in fps[0]['pads'] if p['number'] == endpoint['pad']]
        if len(pads) != 1:
            raise ValueError('pad missing or duplicated')
    pad = pads[0]
    if pad['net'] != endpoint['net']:
        raise ValueError('native pad net differs from declaration')
    position = pad.get('position_nm')
    if not isinstance(position, list) or len(position) != 2 or any(type(v) is not int for v in position):
        raise ValueError('native pad centre unavailable')
    offset = pad.get('copper_centroid_offset_nm')
    if (not isinstance(offset, list) or len(offset) != 2 or any(
            not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v)
            or v[1] <= 0 for v in offset)):
        raise ValueError('native copper centroid unavailable: ' + pad.get('copper_centroid_reason', 'missing evidence'))
    return [p + Fraction(*v) for p, v in zip(position, offset)]


def grade(probe, document):
    """CHECK: grade every pair, preserving determinate defects alongside unknowns."""
    validate(document)
    measured, defects, unknown = [], [], []
    for obligation in document['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            record = {'obligation': obligation['id'], 'role': role, 'metric': obligation['metric'],
                      'refs': sorted({pair['from']['reference'], pair['to']['reference']}), **pair}
            try:
                if obligation['metric'] != 'pad-centre-to-centre':
                    raise ValueError('unsupported locality metric')
                if pair['from']['net'] != pair['to']['net']:
                    raise ValueError('locality endpoints must declare the same net')
                a, b = (resolve(probe['footprints'], pair[k]) for k in ('from', 'to'))
                squared = sum((x-y)**2 for x, y in zip(a, b))
                record.update(distance_squared_nm=(int(squared) if squared.denominator == 1 else
                              {'numerator': squared.numerator, 'denominator': squared.denominator}),
                              distance_mm=math.sqrt(squared)/10**6,
                              geometry='native copper-land centroid (before drill subtraction)')
                measured.append(record)
                if 'max_distance_nm' not in pair or 'citation' not in pair:
                    raise ValueError('declared numeric limit and citation required')
                if squared > pair['max_distance_nm']**2:
                    defects.append({'kind': 'electrical_locality_distance', **record})
            except ValueError as exc:
                unknown.append({'kind': 'electrical_locality_unavailable', **record, 'reason': str(exc)})
    return {'criterion': CRITERION, 'status': 'defect' if defects else 'unknown' if unknown else 'pass',
            'measured': measured, 'defects': defects, 'unknown': unknown, 'declaration': document}


def constraint(footprints, proposed, document):
    """PLACEMENT CONSTRAINT: reject known violating pairs; rank all known roles together.

    Unplaced peers are deferred, not accepted; the complete plan and saved board
    must independently pass grade(). Returns blocking references and exact cost.
    """
    selected = [f for f in footprints if f['ref'] != proposed['ref']] + [proposed]
    refs = {f['ref'] for f in selected}
    blockers, cost = set(), Fraction(0)
    for obligation in document['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            involved = {pair[k]['reference'] for k in ('from', 'to')}
            if proposed['ref'] not in involved or not involved <= refs:
                continue
            a, b = (resolve(selected, pair[k]) for k in ('from', 'to'))
            squared = sum((x-y)**2 for x, y in zip(a, b))
            limit2 = pair['max_distance_nm']**2
            cost += Fraction(squared, max(1, limit2))
            if squared > limit2:
                blockers |= involved
    return blockers, cost


def targets(footprints, proposed, document):
    """Candidate origins aligning each selected pad, and declared radius extrema."""
    refs = {f['ref'] for f in footprints}
    points = []
    for obligation in document['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            for own, peer in ((pair['from'], pair['to']), (pair['to'], pair['from'])):
                if own['reference'] != proposed['ref'] or peer['reference'] not in refs:
                    continue
                offset = resolve([proposed], own)
                target = resolve(footprints, peer)
                x, y = [round(target[i]-offset[i]) for i in (0, 1)]
                d = pair['max_distance_nm']
                points += [[x, y], [x-d, y], [x+d, y], [x, y-d], [x, y+d]]
    return points
