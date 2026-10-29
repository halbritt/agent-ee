"""Derive the captured acquisition grouping declaration, without native identity."""
from decimal import Decimal
from hashlib import sha256

from . import connectivity_ir, electrical_locality, parts_manifest, placement_contract
from .catalog import strict_json

PATH = 'acquisition-block-source.json'
GROUPS = {
    'power': ('J1', {'U1', 'J1', 'D1', 'C1', 'C2', 'R5', 'R6'}),
    'analog': ('J3', {'U2', 'J3', 'C3', 'C4', 'C5', 'R1', 'R2'}),
    'host': ('J2', {'J2', 'R3', 'R4'}),
}
# These are the existing captured physical legs, not newly selected constraints.
LEGS = {
    ('C1', 'supply'): ('U1', '1', 'C1', '1', '/VIN_PROTECTED'),
    ('C1', 'return'): ('U1', '2', 'C1', '2', 'GND'),
    ('C2', 'supply'): ('U1', '5', 'C2', '1', '/V3V3'),
    ('C2', 'return'): ('U1', '2', 'C2', '2', 'GND'),
    ('C3', 'supply'): ('U2', '8', 'C3', '1', '/V3V3'),
    ('C3', 'return'): ('U2', '3', 'C3', '2', 'GND'),
    ('C4', 'leg'): ('U2', '4', 'C4', '1', '/ANALOG0'),
    ('C5', 'leg'): ('U2', '5', 'C5', '1', '/ANALOG1'),
}


def derive(root, plan):
    """Read operative admitted inputs; refuse drift instead of inferring new roles."""
    names = ('spec.md', 'groups.json', 'electrical-locality.json', plan['envelope'],
             plan['parts']['manifest'], plan['parts']['circuit'],
             'analysis/geometry-model.json', 'analysis/geometry_model.py')
    raw = {name: parts_manifest.read_captured(root, name) for name in names}
    groups = strict_json(raw['groups.json'])['groups']
    actual = {}
    refs = []
    for group in groups:
        name = group['name']
        if name in actual or len(group['refs']) != len(set(group['refs'])):
            raise ValueError('acquisition final groups contain duplicate groups or members')
        actual[name] = (group['anchor'], set(group['refs']))
        refs.extend(group['refs'])
    if actual != GROUPS or len(refs) != 17 or len(set(refs)) != 17:
        raise ValueError('acquisition final groups differ from captured 17-part allocation/anchors')
    manifest = parts_manifest.validate_manifest(strict_json(raw[plan['parts']['manifest']]))
    instances = [i for part in manifest['parts'] for i in part['instances']]
    if (len(instances) != 17 or any(i['sheet'] != '/' for i in instances)
            or sorted(i['reference'] for i in instances) != sorted(refs)):
        raise ValueError('acquisition final groups differ from selected physical parts')
    circuit = strict_json(raw[plan['parts']['circuit']])
    libraries = {lib['path']: parts_manifest.read_captured(root, lib['path']) for lib in circuit['libraries']}
    resolved = connectivity_ir.resolve_intent(circuit, libraries)
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('acquisition final groups need complete captured pin resolution: ' + str(resolved['findings']))
    physical = [c for c in resolved['components'] if not c['power']]
    if sorted(c['reference'] for c in physical) != sorted(refs):
        raise ValueError('acquisition final groups differ from circuit physical references')
    references = {c['id']: c['reference'] for c in physical}
    memberships = {(references[t['component']], t['pin']): net['name']
                   for net in resolved['nets'] for t in net['terminals'] if t['component'] in references}
    locality = strict_json(raw['electrical-locality.json'])
    electrical_locality.validate(locality)
    if locality.get('source_records', {}).get('spec.md') != raw['spec.md'].decode('utf-8'):
        raise ValueError('acquisition final-group locality does not bind the operative spec')
    pairs, templates = {}, []
    for obligation in locality['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            a, b = pair['from'], pair['to']
            key = obligation['id'], role
            signature = (a['reference'], a['pad'], b['reference'], b['pad'], a['net'])
            if (key in pairs or LEGS.get(key) != signature or a['net'] != b['net']
                    or pair['max_distance_nm'] != 3000000):
                raise ValueError('acquisition final-group physical leg or 3mm bound differs')
            for endpoint in (a, b):
                # The finite flat recipe uses local /names and global GND.
                if memberships.get((endpoint['reference'], endpoint['pad'])) != endpoint['net'].removeprefix('/'):
                    raise ValueError('acquisition final-group pad/net differs from captured circuit')
            citation = pair.get('provenance', {}).get('citation', {})
            if (citation.get('path') != 'spec.md' or citation.get('sha256') != sha256(raw['spec.md']).hexdigest()
                    or pair.get('provenance', {}).get('class') != 'engineering'):
                raise ValueError('acquisition final-group locality source differs')
            pairs[key] = pair
            templates.append(dict(relationship=key[0], role=role, template='same-face-only'))
    if pairs.keys() != LEGS.keys():
        raise ValueError('acquisition final groups require all eight captured physical legs')
    stackup = strict_json(raw[plan['envelope']])['stackup']
    thickness = Decimal(str(stackup.get('nominal_thickness_mm', stackup['thickness_mm']))) * 1000000
    if thickness != 1200000:
        raise ValueError('acquisition final groups require the captured nominal 1.2mm stackup')
    source = dict(schema=placement_contract.SOURCE_SCHEMA,
        allocation=dict(schema='agent-ee.block-allocation/2', blocks=[
            dict(id=g['name'], references=g['refs'], anchor={'member':g['anchor']}, interfaces_to_fixed=None)
            for g in groups], fixed=[], shared=[]),
        eligibility=dict(schema='fome-mc.side-eligibility/1',
            sources={name: sha256(raw[name]).hexdigest() for name in names
                     if name not in (plan['parts']['manifest'], plan['parts']['circuit'])}, default={'faces':['F']},
            blocks={name:{'eligible_faces':['F']} for name in actual}, fixed={}, shared={}),
        relationships=locality, templates=templates, thickness_nm=int(thickness))
    placement_contract.validate_source(source)
    for name, captured in {**raw, **libraries}.items():
        if parts_manifest.read_captured(root, name) != captured:
            raise ValueError('acquisition final-group source changed during derivation: ' + name)
    return source
