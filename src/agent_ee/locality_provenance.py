"""Locality authority and necessary manufacturing floors, never inferred maxima.

Source references locate reviewable evidence; they do not authenticate its author.
The pad-only floor is a necessary bound, not whole-component packing feasibility.
"""
from fractions import Fraction
from importlib.resources import files
import hashlib
import json
import math
import re

from . import source_authority

CLASSES = frozenset(('datasheet', 'manufacturer', 'owner', 'engineering', 'default'))
RECORD_SHA256 = '92561871360fe5652778275ab31009e80e70b2e15af3d905a8d16eaa67aa063d'


def manufacturing_record():
    """The builtin numeric record is pinned by the fingerprinted policy code."""
    try:
        raw = files('agent_ee').joinpath('data/jlcpcb-locality.json').read_bytes()
    except OSError as exc:
        raise ValueError('captured JLCPCB numeric record unavailable') from exc
    if hashlib.sha256(raw).hexdigest() != RECORD_SHA256:
        raise ValueError('captured JLCPCB numeric record hash mismatch')
    return json.loads(raw)


# One citation model with the design-rule import (AGENTEE-348).
reference, verified_reference = source_authority.reference, source_authority.verified_reference


def classify(pair, records=None):
    """A label or prose alone never grants numeric enforcement authority."""
    records = records or {}
    provenance = pair.get('provenance', {})
    result = {'class': provenance.get('class', 'default'), 'enforcement': 'advisory',
              'reason': 'unsourced numeric locality bound'}
    if not isinstance(result['class'], str) or result['class'] not in CLASSES:
        return {**result, 'class': 'default'}
    if not isinstance(provenance.get('basis'), str) or not provenance['basis'].strip():
        return result
    # The same citation model and value binding as the design-rule import (AGENTEE-348):
    # the declared maximum, in exact millimetres, must be the governed value.
    bound = pair.get('max_distance_nm')
    values = [Fraction(bound, 10 ** 6)] if type(bound) is int and bound >= 0 else []
    if result['class'] == 'engineering':
        # A captured engineering requirement is enforceable without presenting
        # the engineer's chosen limit as an owner or datasheet requirement.
        return {**result, **source_authority.cite('engineering', provenance.get('citation'), records, values)}
    if result['class'] == 'manufacturer':
        # A captured manufacturing floor never qualifies an unsourced maximum: only a
        # resolving manufacturer citation of this bound makes it a feasibility limit.
        resolved = source_authority.cite('manufacturer', provenance.get('citation'), records, values)
        if resolved['enforcement'] == 'integrity':
            return {**result, **resolved}
        if resolved['enforcement'] != 'hard':
            return {**result, 'reason': resolved['reason']}
        return {**result, 'enforcement': 'feasibility',
                'reason': 'Captured manufacturing minimum cannot establish an electrical maximum'}
    if result['class'] == 'owner':
        # Hard only at a pinned owner commit whose decision states this bound; a verified but
        # uncommitted, stale, unresolvable or value-mismatched decision is advisory with its reason.
        decision = provenance.get('decision_reference')
        verification = source_authority.cite('owner', {**decision, 'kind': 'owner_decision'}
                                             if isinstance(decision, dict) else None, records, values)
        if verification['enforcement'] == 'integrity':
            return {**result, **verification}
        if verification['enforcement'] == 'hard':
            return {**result, 'enforcement': 'hard', 'reason': 'explicit owner decision reference'}
        if source_authority.reference(decision):
            return {**result, 'reason': verification['reason']}
    if result['class'] == 'datasheet':
        derivation = provenance.get('derivation', {})
        if not isinstance(derivation, dict):
            return result
        source = derivation.get('source')
        verification = verified_reference(source, records)
        if verification['enforcement'] == 'integrity':
            return {**result, **verification}
        if verification['enforcement'] != 'verified':
            return result
        # The derivation may only restrict: its declared value must equal the bound.
        if derivation.get('kind') == 'numeric':
            value = derivation.get('value_nm')
            valid = type(value) is int and value >= 0
        elif derivation.get('kind') == 'layout':
            distance, scale = (derivation.get(k) for k in ('distance_units', 'scale_nm_per_unit'))
            valid = (type(distance) is int and distance >= 0 and type(scale) is int and scale > 0
                     and isinstance(derivation.get('scale_reference'), str)
                     and bool(derivation['scale_reference'].strip()))
            value = distance * scale if valid else None
        else:
            valid, value = False, None
        if not valid or value != bound:
            return result
        # Round-2 H3: authority comes only from the common exact-unit qualification. The
        # captured source must state the derived distance itself, so an authored layout
        # scale or a prose diagram never establishes the limit.
        resolved = source_authority.cite('datasheet', {**source, 'kind': 'captured_source'}, records, values)
        if resolved['enforcement'] == 'hard':
            return {**result, 'enforcement': 'hard', 'reason': 'numeric derivation equals declared bound'}
        return {**result, 'reason': ('layout derivation scale is not validated by the captured source'
                                     if derivation['kind'] == 'layout' else resolved['reason'])}
    return result


def package(footprint):
    """Only explicitly named captured package families; never infer from ref/net."""
    name = footprint.get('fpid', '').split(':')[-1]
    chip = re.match(r'[CRL]_(0201|0402|0603|0805|1206)(?:_|$)', name)
    if chip:
        return 'chip ' + chip[1]
    for pattern, family in ((r'(?:H?SOIC|SOP)-', 'SOIC'),
                            (r'(?:QFN|VQFN|WQFN)-', 'QFN'),
                            (r'(?:LQFP|TQFP|QFP)-', 'QFP'),
                            (r'SOT-(?:23|363)(?:_|-|$)', 'SOT'), (r'BGA-', 'BGA')):
        if re.match(pattern, name):
            return family
    raise ValueError('package family absent from captured JLCPCB spacing table')


def pad_radius(pad):
    if pad.get('attribute') not in ('SMD', 'smd') or pad.get('shape') not in (0, 1, 2, 4):
        raise ValueError('pad geometry unsupported for inscribed copper disk')
    size = pad.get('size_nm')
    if (not isinstance(size, list) or len(size) != 2 or
            any(type(n) is not int or n <= 0 for n in size)):
        raise ValueError('native pad size unavailable')
    return Fraction(min(size), 2)


def footprint_disks(footprints, fp, selected):
    """Qualify native disk subsets once per footprint in one grading operation."""
    from .electrical_locality import resolve
    disks = []
    for pad in fp['pads']:
        if not pad.get('net'):
            continue
        try:
            radius = pad_radius(pad)
            endpoint = {'reference': fp['ref'], 'pad': pad['number'], 'net': pad['net']}
            if pad.get('uuid'):
                endpoint['uuid'] = pad['uuid']
            position = selected[1] if pad is selected[0] else resolve([fp], endpoint)
        except ValueError:
            continue  # A subset can prove a lower bound, never completeness.
        disks.append((pad, position, radius))
    return disks


def ceil_distance(position, center):
    squared = sum((a-b)**2 for a, b in zip(position, center))
    root = math.isqrt(squared.numerator // squared.denominator)
    return root + (root*root*squared.denominator < squared.numerator)


def copper_floor(disks, centers, clearance):
    """A rotation-independent necessary bound from different-net disk pairs.

    Offset lengths upper-bound the cross-pad centre offset at every relative
    rotation (triangle inequality). Their rounded-up sum can only weaken the
    lower bound. The maximum escape distance over these disks remains necessary;
    it does not solve the exclusion union or establish complete feasibility.
    """
    bound = Fraction(0)
    for a, position_a, radius_a in disks[0]:
        for b, position_b, radius_b in disks[1]:
            common = set(a['layers']) & set(b['layers'])
            if a['net'] == b['net'] or not any(layer.endswith('.Cu') for layer in common):
                continue
            offset = ceil_distance(position_a, centers[0]) + ceil_distance(position_b, centers[1])
            bound = max(bound, radius_a + radius_b + clearance - offset)
    return bound.numerator // bound.denominator


def floor(footprints, pair, centers=None, disk_cache=None):
    """PCB copper proves a necessary floor; assembly pad envelopes are advisory.

    Component body/lead extents in the assembly diagram are not native solder
    lands. Do not promote that estimate into an infeasibility proof.
    """
    from .electrical_locality import resolve
    try:
        selected, radii, selected_pads = [], [], []
        if centers is None:
            centers = [resolve(footprints, pair[k]) for k in ('from', 'to')]
        if disk_cache is None:
            disk_cache = {}
        by_ref = disk_cache.get(('by_ref',))
        if by_ref is None:
            by_ref = disk_cache[('by_ref',)] = {}
            for f in footprints:
                by_ref.setdefault(f['ref'], []).append(f)
        for endpoint in (pair['from'], pair['to']):
            fps = by_ref.get(endpoint['reference'], [])
            if len(fps) != 1:
                raise ValueError('floor needs unique native footprints')
            fp = fps[0]
            pads = [p for p in fp['pads'] if p['number'] == endpoint['pad'] and
                    ('uuid' not in endpoint or p['uuid'] == endpoint['uuid'])]
            if len(pads) != 1:
                raise ValueError('floor needs unique native pads')
            selected_pads.append(pads[0])
            radii.append(pad_radius(pads[0]))
            selected.append(fp)
        if selected[0]['side'] != selected[1]['side']:
            raise ValueError('opposite-side assembly spacing not qualified')
        record = manufacturing_record()
        if len(selected[0]['pads']) * len(selected[1]['pads']) > 4096:
            raise ValueError('manufacturing pad-pair work budget exceeded')
        disks = []
        for fp, pad, center in zip(selected, selected_pads, centers):
            if fp['ref'] not in disk_cache:
                disk_cache[fp['ref']] = footprint_disks(footprints, fp, (pad, center))
            disks.append(disk_cache[fp['ref']])
        bound = copper_floor(disks, centers, record['different_net_clearance_nm'])
        assembly = {'status': 'unknown', 'reason': 'native solder lands do not establish physical body/lead extents'}
        try:
            families = sorted(package(fp) for fp in selected)
            spacing = next(r for r in record['component_spacing'] if r['packages'] == families)
            estimate = sum(radii) + spacing['spacing_nm']
            assembly.update(packages=families, pad_envelope_estimate_nm=estimate.numerator // estimate.denominator,
                            component_spacing_nm=spacing['spacing_nm'], source=record['sources']['spacing'])
        except (ValueError, StopIteration):
            assembly['reason'] = 'package pair absent from captured JLCPCB spacing table'
        return {'status': 'known', 'floor_nm': bound, 'kind': 'necessary_copper_floor',
                'source': record['sources']['pcb'], 'assembly': assembly,
                'different_net_clearance_nm': record['different_net_clearance_nm'],
                'limitation': 'Native different-net inscribed disks only; exact whole-footprint minimum unknown'}
    except ValueError as exc:
        return {'status': 'unknown', 'reason': str(exc)}
