"""Declared pad-centre locality: grading and a distinct partial-placement constraint.

Limits and citations are supplied by the engineer, never inferred from a net name.
Distances use native copper-land centroids, not anchors, routed length or loop inductance.
"""
from copy import deepcopy
from fractions import Fraction
import json
import math
from types import MappingProxyType
from uuid import UUID

from . import locality_provenance

SCHEMA = 'agent-ee.electrical-locality/1'
CRITERION = 'electrical-locality/1'


MAX_DOCUMENT_DEPTH = 64
DEPTH_ERROR = f'locality document nesting exceeds {MAX_DOCUMENT_DEPTH} levels'
MAX_DOCUMENT_NODES = 200_000
NODE_ERROR = f'locality document exceeds {MAX_DOCUMENT_NODES} nodes'


def _bounded_depth(value):
    """Refuse deep or cyclic containers before stdlib copying can recurse without bound.

    Subclass storage is read through the base type, so no override runs here.
    """
    ancestors = set()
    nodes = [0]

    def visit(value, depth):
        kind = type(value)  # Not isinstance: a __class__ property cannot pose as a container.
        nodes[0] += 1  # Shared sub-objects are revisited, so a DAG is counted as the tree it unfolds to.
        if nodes[0] > MAX_DOCUMENT_NODES:
            raise ValueError(NODE_ERROR)
        if not issubclass(kind, (dict, list)):
            return
        if depth > MAX_DOCUMENT_DEPTH:
            raise ValueError(DEPTH_ERROR)
        identity = id(value)
        if identity in ancestors:
            raise ValueError('locality document must contain only JSON values')
        ancestors.add(identity)
        try:
            for item in (dict.values(value) if issubclass(kind, dict) else list.copy(value)):
                visit(item, depth + 1)
        finally:
            ancestors.remove(identity)

    visit(value, 0)


def load_json(text):
    """Decode captured locality JSON; decoder recursion on deep nesting is a typed refusal."""
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise ValueError(DEPTH_ERROR) from exc


def _plain_json(value):
    """Copy exact JSON types into fresh containers unreachable by copy hooks."""
    ancestors = set()
    nodes = [0]

    def copy_value(value, depth):
        kind = type(value)
        nodes[0] += 1  # Shared sub-objects are copied per reference, so budget the unfolded tree.
        if nodes[0] > MAX_DOCUMENT_NODES:
            raise ValueError(NODE_ERROR)
        if kind is dict or kind is list:
            if depth > MAX_DOCUMENT_DEPTH:
                raise ValueError(DEPTH_ERROR)
            identity = id(value)
            if identity in ancestors:
                raise ValueError('locality document must contain only JSON values')
            ancestors.add(identity)
            try:
                if kind is list:
                    return [copy_value(item, depth + 1) for item in value]
                copied = {}
                for key, item in value.items():
                    if type(key) is not str:
                        raise ValueError('locality document must contain only JSON values')
                    copied[key] = copy_value(item, depth + 1)
                return copied
            finally:
                ancestors.remove(identity)
        if value is not None and not any(kind is scalar for scalar in (str, int, float, bool)):
            raise ValueError('locality document must contain only JSON values')
        return value

    return copy_value(value, 0)


def validate(document):
    """Validate a fresh exact-type snapshot; returns the caller's object unchanged."""
    _validated(document)
    return document


def _validated(document):
    """Validate and return a fresh exact-type snapshot; the caller's object is not reread."""
    document = _plain_json(document)
    if (not isinstance(document, dict) or set(document) - {'schema', 'obligations', 'source_records'}
            or not {'schema', 'obligations'} <= document.keys()
            or document['schema'] != SCHEMA or not isinstance(document['obligations'], list)
            or not document['obligations']):
        raise ValueError(f'expected {SCHEMA} with nonempty obligations')
    records = document.get('source_records', {})
    if not isinstance(records, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                             for k, v in records.items()):
        raise ValueError('locality source_records must map source paths to captured UTF-8 text')
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
            if (not isinstance(pair, dict) or set(pair) - {'from', 'to', 'max_distance_nm', 'citation', 'provenance'}
                    or not {'from', 'to'} <= pair.keys()):
                raise ValueError('locality pair needs from/to, optionally max_distance_nm and citation')
            if 'provenance' in pair and not isinstance(pair['provenance'], dict):
                raise ValueError('locality provenance must be an object')
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
        if (supply['from']['reference'] != ground['from']['reference']
                and {supply[k]['net'] for k in ('from', 'to')} & {ground[k]['net'] for k in ('from', 'to')}):
            raise ValueError('distinct-source supply and return nets must differ')
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
            if ('switch' in obligation and {obligation['switch'][k]['net'] for k in ('from', 'to')}
                    & ({supply[k]['net'] for k in ('from', 'to')} | {obligation['return_net']})):
                raise ValueError('loop switch nets must differ from supply and return')
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
    """CHECK: grade every pair, preserving determinate defects alongside unknowns.

    Grades and reports only the validated snapshot, never the caller's document.
    """
    document = _validated(document)
    measured, defects, unknown, advisories = [], [], [], []
    disk_cache = {}
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
                record['constraint_provenance'] = locality_provenance.classify(pair, document.get('source_records'))
                record['manufacturing_floor'] = locality_provenance.floor(probe['footprints'], pair, (a, b), disk_cache)
                measured.append(record)
                authority = record['constraint_provenance']
                floor = record['manufacturing_floor']
                if 'max_distance_nm' not in pair or ('citation' not in pair and authority['enforcement'] == 'hard'):
                    raise ValueError('declared numeric limit and citation required')
                if (authority['enforcement'] == 'feasibility' and floor['status'] != 'known'
                        and floor['reason'].startswith('captured JLCPCB numeric record')):
                    raise ValueError('manufacturer floor unavailable: ' + floor['reason'])
                below = floor['status'] == 'known' and pair['max_distance_nm'] < floor['floor_nm']
                if authority['enforcement'] in ('hard', 'feasibility') and below:
                    defects.append({'kind': 'locality_infeasible', **record})
                elif authority['enforcement'] == 'hard':
                    if squared > pair['max_distance_nm']**2:
                        defects.append({'kind': 'electrical_locality_distance', **record})
                else:
                    advisories.append({'kind': 'unsourced', 'severity': 'defect', **record,
                                       'exceeded': squared > pair['max_distance_nm']**2, 'below_floor': below})
            except ValueError as exc:
                unknown.append({'kind': 'electrical_locality_unavailable', **record, 'reason': str(exc)})
    return {'criterion': CRITERION, 'status': 'defect' if defects else 'unknown' if unknown else 'info' if advisories else 'pass',
            'measured': measured, 'defects': defects, 'unknown': unknown, 'advisories': advisories,
            'declaration': document}


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
            if squared > limit2 and locality_provenance.classify(pair, document.get('source_records'))['enforcement'] == 'hard':
                blockers |= involved
    return blockers, cost


def targets(footprints, proposed, document):
    """Candidate origins aligning each selected pad, and declared radius extrema."""
    refs = {f['ref'] for f in footprints}
    points = []
    for obligation in document['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            if locality_provenance.classify(pair, document.get('source_records'))['enforcement'] != 'hard':
                continue
            for own, peer in ((pair['from'], pair['to']), (pair['to'], pair['from'])):
                if own['reference'] != proposed['ref'] or peer['reference'] not in refs:
                    continue
                offset = resolve([proposed], own)
                target = resolve(footprints, peer)
                x, y = [round(target[i]-offset[i]) for i in (0, 1)]
                d = pair['max_distance_nm']
                points += [[x, y], [x-d, y], [x+d, y], [x, y-d], [x, y+d]]
    return points


MAX_PLACEMENT_TARGETS = 256


class TargetBudgetExhausted(Exception):
    """Too many distinct locality origins; no candidate subset is silently used."""


class PlacementIndex:
    """Incremental locality state for a capture already qualified by grade().

    The independent grader remains the identity/geometry authority. Only selected
    pads are transformed/resolved on push; pop removes every cached position for
    that footprint. Candidate evaluators contain only incident, placed pairs.
    """
    def __init__(self, document):
        _bounded_depth(document)
        try:
            copied = deepcopy(document)
        except RecursionError as exc:
            # The pre-walk cannot see a concurrent insertion or a deep non-JSON container.
            raise ValueError(DEPTH_ERROR) from exc
        document = _plain_json(copied)
        validate(document)
        pairs = {}
        endpoints = {}
        self.positions = {}
        for obligation in document['obligations']:
            for role in roles(obligation):
                pair = obligation[role]
                a, b = (MappingProxyType(dict(pair[end])) for end in ('from', 'to'))
                for own, peer in ((a, b), (b, a)):
                    ref = own['reference']
                    pairs.setdefault(ref, []).append((own, peer, pair['max_distance_nm'],
                        locality_provenance.classify(pair, document.get('source_records'))['enforcement'] == 'hard'))
                    endpoints.setdefault(ref, {})[self.key(own)] = own
        self._pairs = MappingProxyType({ref: tuple(rows) for ref, rows in pairs.items()})
        self._endpoints = MappingProxyType({ref: MappingProxyType(rows)
                                            for ref, rows in endpoints.items()})

    @property
    def pairs(self):
        return self._pairs

    @property
    def endpoints(self):
        return self._endpoints

    @staticmethod
    def key(endpoint):
        return (endpoint['reference'], endpoint['pad'], endpoint.get('uuid'), endpoint['net'])

    def push(self, footprint):
        ref = footprint['ref']
        if ref in self.endpoints:
            self.positions[ref] = {key: resolve([footprint], endpoint)
                                   for key, endpoint in self.endpoints[ref].items()}

    def pop(self, ref):
        self.positions.pop(ref, None)

    def prepare(self, at_origin):
        """Resolve the candidate's selected pads once per rotation, before scanning."""
        ref = at_origin['ref']
        own_positions = {}
        pairs = []
        for own, peer, limit, hard in self.pairs.get(ref, ()):
            if peer['reference'] not in self.positions:
                continue
            key = self.key(own)
            if key not in own_positions:
                own_positions[key] = resolve([at_origin], own)
            offset = own_positions[key]
            target = self.positions[peer['reference']][self.key(peer)]
            pairs.append((offset, target, limit, {ref, peer['reference']}, hard))
        return CandidateLocality(pairs)


class CandidateLocality:
    """Exact translated centroid cost; no board scan or geometry copy per point."""
    def __init__(self, pairs):
        self.pairs = [tuple(pair) if len(pair) == 5 else (*pair, True) for pair in pairs]

    def constraint(self, position):
        blockers, cost = set(), Fraction(0)
        for offset, target, limit, involved, hard in self.pairs:
            squared = sum((offset[i] + position[i] - target[i])**2 for i in (0, 1))
            limit2 = limit**2
            cost += Fraction(squared, max(1, limit2))
            if squared > limit2 and hard:
                blockers |= involved
        return blockers, cost

    def targets(self):
        # At most five distinct origins per incident role with a placed peer;
        # duplicates never add axes. No truncation of the original candidate set.
        points = set()
        for offset, target, limit, _, hard in self.pairs:
            if not hard:
                continue
            x, y = [round(target[i]-offset[i]) for i in (0, 1)]
            points.update(((x, y), (x-limit, y), (x+limit, y), (x, y-limit), (x, y+limit)))
            if len(points) > MAX_PLACEMENT_TARGETS:
                raise TargetBudgetExhausted
        return sorted(points)
