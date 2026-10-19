"""Exact reviewed J1 candidate exclusion; no generic clearance or acceptance policy."""
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from agent_ee.symbol_library import children, field, parse_sexpr


# This recipe accepts only the reviewed fixed decision. A new policy/source needs
# a separately reviewed input and producer change, not caller-supplied authority.
DECISION_SHA256 = '9ae3a4affba96e99b680997f163ad99740f65f83e2c64a09383dba536690df2a'


def derive(inputs, decision_bytes):
    """Return derived CAD and informational custody record without writing files."""
    if hashlib.sha256(decision_bytes).hexdigest() != DECISION_SHA256:
        raise ValueError('J1 requires the exact reviewed decision')
    decision = json.loads(decision_bytes)
    sources = {}
    for label, blob in decision['sources'].items():
        if blob['path'] not in inputs:
            raise ValueError('J1 missing source: ' + blob['path'])
        raw = inputs[blob['path']]
        if hashlib.sha256(raw).hexdigest() != blob['sha256']:
            raise ValueError('J1 source hash mismatch: ' + blob['path'])
        sources[label] = raw
    geometry = json.loads(sources['geometry'])
    if (geometry['geometry']['local_polygon_nm'] != decision['local_polygon_nm'] or
            geometry['geometry']['qualified_courtyard'] is not False or
            geometry['source']['sha256'] != decision['sources']['drawing']['sha256']):
        raise ValueError('J1 nominal source geometry differs')
    original = sources['footprint']
    tree = parse_sexpr(original)
    if tree[:2] != ['footprint', decision['fpid'].split(':')[1]]:
        raise ValueError('J1 source footprint identity differs')
    pads = list(children(tree, 'pad'))
    if (len(pads) != 27 or sorted(p[1] for p in pads if p[1]) != sorted(map(str, range(1,24))) or
            len([p for p in pads if p[1:3] == ['', 'np_thru_hole']]) != 4):
        raise ValueError('J1 source pad inventory differs')
    centre = next(p for p in pads if p[1] == '12')
    if field(centre, 'at', 2) != ['0', '0']:
        raise ValueError('J1 source origin is not contact12')
    if any(layer[1:] in (['F.CrtYd'], ['B.CrtYd'])
           for node in tree if isinstance(node, list) for layer in children(node, 'layer')):
        raise ValueError('J1 source already has a courtyard')
    uid = str(uuid5(NAMESPACE_URL, 'agent-ee:pod-j1-exclusion:' + DECISION_SHA256))
    addition = ('\t(fp_rect (start -30.4 -16.05) (end 30.4 16.05)\n'
                '\t\t(stroke (width 0.05) (type default)) (fill none)\n'
                '\t\t(layer "F.CrtYd") (uuid "' + uid + '"))\n').encode()
    end = len(original.rstrip()) - 1
    if original[end:end+1] != b')':
        raise ValueError('J1 source has no final root delimiter')
    derived = original[:end] + addition + original[end:]
    if parse_sexpr(derived) != tree + [parse_sexpr(addition)]:
        raise ValueError('J1 derivation changed existing primitives')
    return derived, dict(schema='agent-ee.pod-j1-courtyard-derivation/1', done=False,
        authority='Informational candidate CAD custody; not a physical qualification or accepted check.',
        decision=dict(path='j1-placement-exclusion.json', sha256=DECISION_SHA256),
        sources=decision['sources'], fpid=decision['fpid'], reference=decision['reference'],
        derived=dict(path=decision['derived_path'], sha256=hashlib.sha256(derived).hexdigest()),
        delta=dict(kind='append-one-front-rectangle', uuid=uid, layer=decision['layer'],
                   local_polygon_nm=decision['local_polygon_nm'], added_margin_mm=decision['added_margin_mm'],
                   preserved_pad_count=len(pads), original_bytes_preserved=True),
        qualification=decision['qualification'], limitations=decision['limitations'])
