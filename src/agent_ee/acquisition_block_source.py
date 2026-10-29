"""Derive the captured acquisition grouping declaration, without native identity."""
from decimal import Decimal
from hashlib import sha256

from . import connectivity_ir, electrical_locality, locality_provenance, parts_manifest, placement_contract
from .catalog import strict_json
from .storage import json_bytes

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
ENGINEERING_PATH = 'preparation/final-group-engineering.txt'
# Root706 adopted these exact finite engineering choices on 2026-10-29.
ADDED_LEGS = {
    'group-input-connector': ('J1', '1', 'D1', '2', '/VIN', 5000000),
    'group-protected-input': ('D1', '1', 'U1', '1', '/VIN_PROTECTED', 5000000),
    'group-input-bleeder': ('R5', '1', 'C1', '1', '/VIN_PROTECTED', 3000000),
    'group-output-bleeder': ('R6', '1', 'C2', '1', '/V3V3', 3000000),
    'group-analog0-input': ('J3', '1', 'R1', '1', '/ANALOG0_IN', 5000000),
    'group-analog1-input': ('J3', '3', 'R2', '1', '/ANALOG1_IN', 5000000),
    'group-analog0-filter': ('R1', '2', 'U2', '4', '/ANALOG0', 3000000),
    'group-analog1-filter': ('R2', '2', 'U2', '5', '/ANALOG1', 3000000),
    'group-host-sda': ('R3', '2', 'J2', '2', '/SDA', 5000000),
    'group-host-scl': ('R4', '2', 'J2', '3', '/SCL', 5000000),
}


def engineering_text():
    lines = ['Coordinator root706 engineering placement choices adopted 2026-10-29.',
             'These are not owner or datasheet mandates. Native feasibility remains unverified.']
    lines += [f'{a}.{ap} to {b}.{bp} pad distance shall be at most {limit // 1000000} mm.'
              for a, ap, b, bp, net, limit in ADDED_LEGS.values()]
    return ('\n'.join(lines) + '\n').encode('utf-8')


def apply(root, plan):
    """Append the adopted choices to both operative copies before sealing."""
    locality = strict_json(parts_manifest.read_captured(root, 'electrical-locality.json'))
    placement = strict_json(parts_manifest.read_captured(root, plan['placement']['intent']))
    keys = [(o['id'], r) for o in locality['obligations'] for r in electrical_locality.roles(o)]
    if set(keys) != LEGS.keys() or len(keys) != 8 or placement.get('electrical_locality') != locality:
        raise ValueError('final-group additions require the original eight operative locality legs')
    target = root / ENGINEERING_PATH
    if target.exists() or ENGINEERING_PATH in locality.get('source_records', {}):
        raise ValueError('final-group engineering source already exists')
    text = engineering_text()
    for index, (oid, (a, ap, b, bp, net, limit)) in enumerate(ADDED_LEGS.items()):
        quote = text.decode().splitlines()[index + 2]
        pair = dict(**{'from':dict(reference=a, pad=ap, net=net)},
            to=dict(reference=b, pad=bp, net=net), max_distance_nm=limit, citation=quote,
            provenance=dict(**{'class':'engineering'},
                basis='Coordinator root706 engineering choice, 2026-10-29; not owner/datasheet authority',
                citation=dict(kind='captured_source', path=ENGINEERING_PATH,
                    sha256=sha256(text).hexdigest(), locator=oid, quote=quote)))
        locality['obligations'].append(dict(id=oid, kind='single-leg', metric='pad-centre-to-centre', leg=pair))
    locality['source_records'][ENGINEERING_PATH] = text.decode()
    placement['electrical_locality'] = locality
    target.write_bytes(text)
    (root / 'electrical-locality.json').write_bytes(json_bytes(locality))
    (root / plan['placement']['intent']).write_bytes(json_bytes(placement))


def derive(root, plan):
    """Read operative admitted inputs; refuse drift instead of inferring new roles."""
    names = ('spec.md', 'groups.json', 'electrical-locality.json', plan['envelope'],
             plan['parts']['manifest'], plan['parts']['circuit'],
             'analysis/geometry-model.json', 'analysis/geometry_model.py',
             ENGINEERING_PATH, plan['placement']['intent'])
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
    if (raw[ENGINEERING_PATH] != engineering_text()
            or locality['source_records'].get(ENGINEERING_PATH) != raw[ENGINEERING_PATH].decode()
            or strict_json(raw[plan['placement']['intent']]).get('electrical_locality') != locality):
        raise ValueError('final-group engineering source or operative locality copies differ')
    expected = {key:(signature, 3000000) for key,signature in LEGS.items()}
    expected.update({(key, 'leg'):(row[:5], row[5]) for key,row in ADDED_LEGS.items()})
    pairs, templates = {}, []
    for obligation in locality['obligations']:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            a, b = pair['from'], pair['to']
            key = obligation['id'], role
            signature = (a['reference'], a['pad'], b['reference'], b['pad'], a['net'])
            if (key in pairs or expected.get(key) != (signature, pair['max_distance_nm']) or a['net'] != b['net']):
                raise ValueError('acquisition final-group physical leg or adopted bound differs')
            for endpoint in (a, b):
                # The finite flat recipe uses local /names and global GND.
                if memberships.get((endpoint['reference'], endpoint['pad'])) != endpoint['net'].removeprefix('/'):
                    raise ValueError('acquisition final-group pad/net differs from captured circuit')
            citation = pair.get('provenance', {}).get('citation', {})
            path = 'spec.md' if key in LEGS else ENGINEERING_PATH
            if (citation.get('path') != path or citation.get('sha256') != sha256(raw[path]).hexdigest()
                    or pair.get('provenance', {}).get('class') != 'engineering'
                    or not pair.get('citation') or pair['citation'] != citation.get('quote')
                    or locality_provenance.classify(pair, locality['source_records'])['enforcement'] != 'hard'):
                raise ValueError('acquisition final-group locality source differs')
            pairs[key] = pair
            templates.append(dict(relationship=key[0], role=role, template='same-face-only'))
    if pairs.keys() != expected.keys():
        raise ValueError('acquisition final groups require all eighteen adopted physical legs')
    covered = {pair[end]['reference'] for pair in pairs.values() for end in ('from','to')}
    if covered != set(refs):
        raise ValueError('acquisition final groups lack complete 17-member electrical coverage')
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
