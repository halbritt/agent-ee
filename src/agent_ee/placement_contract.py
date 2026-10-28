"""Bind strict block intent to prepared jobs without changing native identities."""
from copy import deepcopy
import json

from . import block_contract
from .placement_problem import JobRefusal
from .run_record import policy_files
from .storage import identity

SCHEMA = 'agent-ee.prepared-block-contract/1'


def _project(capture, references):
    projected = deepcopy(capture)
    for fp in projected['footprints']:
        if fp['uuid'] in references:
            fp['native_ref'] = fp['ref']
            fp['ref'] = references[fp['uuid']]
    return projected


def bind(capture, contract, normalized, records):
    """Validate complete allocation and bind it to the captured effective intent.

    Fixed copper marks and silk-only graphics use explicit ``uuid:UUID`` roles
    in allocation/2. This projection leaves the native file and circuit reference
    semantics untouched, including repeated fixed-mark labels.
    """
    references = {r['uuid']: 'uuid:' + r['uuid'] for r in records
                  if r['role'] in ('fixed_copper_mark', 'silk_only')}
    original = _project(capture, references)
    groups = normalized['groups']['groups']
    try:
        section = dict(contract=contract, side_eligibility=contract.get('eligibility'), candidates=[],
            allocation_text=json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
                dict(id=g['name'], references=g['refs'], anchor=g['anchor']) for g in groups]}))
        block_contract.validate(original, section, normalized['intent'])
        anchors = {g['name']: {'member': g['anchor']} for g in groups}
        for block in contract['allocation']['blocks']:
            if anchors.get(block['id']) != block['anchor']:
                raise ValueError('prepared block contract requires the declared group member anchor')
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise JobRefusal('block contract: ' + str(exc)) from exc
    fixed = [references.get(r['uuid'], r['native_ref']) for r in records
             if r['role'] in ('fixed_body', 'fixed_copper_mark', 'silk_only')]
    binding = dict(schema=SCHEMA, contract=deepcopy(contract), original_capture=original,
                   references=references, fixed_refs=fixed, groups_sha256=identity(normalized['groups']),
                   policy_sha256=identity(policy_files()))
    return dict(binding, binding_sha256=identity(binding))


def grade(saved_capture, binding):
    """Judge reloaded saved facts; missing coverage remains unknown, never pass."""
    body = {k: v for k, v in binding.items() if k != 'binding_sha256'}
    if binding.get('schema') != SCHEMA or binding.get('binding_sha256') != identity(body):
        raise JobRefusal('block contract binding changed after capture')
    if binding['policy_sha256'] != identity(policy_files()):
        raise JobRefusal('block contract checking policy changed after capture')
    report = block_contract.judge(_project(saved_capture, binding['references']), binding['contract'],
        original_capture=binding['original_capture'], plan={'policy': {'fixed_refs': binding['fixed_refs']}})
    native_refs = {fp['uuid']: fp['native_ref'] for fp in binding['original_capture']['footprints']
                   if fp['uuid'] in binding['references']}
    for fp in saved_capture['footprints']:
        if fp['uuid'] in native_refs and fp['ref'] != native_refs[fp['uuid']]:
            report['defects'].append({'kind': 'saved_native_reference_changed', 'uuid': fp['uuid'],
                                     'original': native_refs[fp['uuid']], 'saved': fp['ref']})
            report['status'] = 'defect'
    return dict(report, binding_sha256=binding['binding_sha256'],
                groups_sha256=binding['groups_sha256'], reference_aliases=binding['references'])


SOURCE_SCHEMA = 'agent-ee.block-contract-source/1'


def validate_source(source, normalized=None):
    """Admit boardless authority without inventing a native capture or identity."""
    from . import electrical_locality, locality_provenance, source_authority
    required = {'schema', 'allocation', 'eligibility', 'relationships', 'templates', 'thickness_nm'}
    try:
        if (not isinstance(source, dict) or source.get('schema') != SOURCE_SCHEMA
                or not required <= source.keys() or source.keys() - required - {'routing_obligations'}):
            raise ValueError('expected ' + SOURCE_SCHEMA)
        allocation, eligibility = source['allocation'], source['eligibility']
        if not isinstance(allocation, dict) or allocation.get('schema') not in block_contract.ALLOCATION:
            raise ValueError('source needs allocation/2 fragments')
        if 'board_sha256' in allocation:
            raise ValueError('block source allocation must omit board_sha256')
        if (not isinstance(eligibility, dict) or eligibility.get('schema') not in block_contract.ELIGIBILITY
                or not isinstance(eligibility.get('sources'), dict)):
            raise ValueError('source needs eligibility with explicit sources')
        if 'board_sha256' in eligibility['sources']:
            raise ValueError('block source eligibility must omit board_sha256')
        roles, blocks = {}, {}
        def add(ref, role):
            block_contract._text(ref, 'reference')
            if ref in roles:
                raise ValueError('source gives multiple roles to ' + ref)
            roles[ref] = role
        for b in block_contract._list(allocation.get('blocks'), 'blocks'):
            if not isinstance(b, dict):
                raise ValueError('source block must be an object')
            bid = block_contract._text(b.get('id'), 'block id')
            if bid in blocks:
                raise ValueError('duplicate source block ' + bid)
            refs = block_contract._list(b.get('references'), 'block references')
            anchor = b.get('anchor')
            if not refs or not isinstance(anchor, dict) or set(anchor) != {'member'} or anchor['member'] not in refs:
                raise ValueError('block source requires a declared member anchor')
            if 'interfaces_to_fixed' not in b:
                raise ValueError('source block needs interfaces_to_fixed')
            blocks[bid] = b
            for ref in refs:
                add(ref, {'role': 'block', 'block_id': bid})
        for role in ('fixed', 'shared'):
            for row in block_contract._list(allocation.get(role), role):
                if not isinstance(row, dict):
                    raise ValueError('source role must be an object')
                if role == 'shared':
                    block_contract._text(row.get('role'), 'shared role')
                add(row.get('reference'), {'role': role})
        for b in blocks.values():
            ref = b['interfaces_to_fixed']
            if ref is not None and roles.get(ref, {}).get('role') != 'fixed':
                raise ValueError('source interface is not an explicit fixed reference')
        block_contract._faces(eligibility.get('default', {}).get('faces'), 'default eligibility')
        if not isinstance(eligibility.get('blocks'), dict) or set(eligibility['blocks']) != set(blocks):
            raise ValueError('source eligibility block ids differ from allocation')
        for role in ('members', 'fixed', 'shared'):
            if not isinstance(eligibility.get(role, {}), dict) or set(eligibility.get(role, {})) - roles.keys():
                raise ValueError('source eligibility names unknown roles')
            if role != 'members' and any(roles[ref]['role'] != role for ref in eligibility.get(role, {})):
                raise ValueError('source eligibility role differs from allocation')
        for ref, role in roles.items():
            if block_contract._eligible(eligibility, ref, role) is None:
                raise ValueError('source side eligibility missing: ' + ref)
        thickness = source['thickness_nm']
        if thickness is not None and (type(thickness) is not int or thickness <= 0):
            raise ValueError('thickness_nm must be positive integer or null')
        doc = source['relationships']
        if doc != {'schema': electrical_locality.SCHEMA, 'obligations': []}:
            electrical_locality.validate(doc)
        pairs = {}
        for obligation in doc['obligations']:
            for role in electrical_locality.roles(obligation):
                pair = obligation[role]
                if any(pair[k]['reference'] not in roles for k in ('from', 'to')):
                    raise ValueError('source relationship names unallocated reference')
                if pair['from']['net'] != pair['to']['net']:
                    raise ValueError('source relationship endpoints name different nets')
                if locality_provenance.classify(pair, doc.get('source_records'))['enforcement'] == 'integrity':
                    raise ValueError('source relationship authority does not verify')
                pairs[obligation['id'], role] = pair
        seen = set()
        for row in block_contract._list(source['templates'], 'templates'):
            if (not isinstance(row, dict) or set(row) != {'relationship', 'role', 'template'}
                    or row['template'] not in ('same-face-only', 'decoupler-under-device')
                    or (row['relationship'], row['role']) not in pairs
                    or (row['relationship'], row['role']) in seen):
                raise ValueError('source template is unsupported, duplicate or names unknown relationship')
            seen.add((row['relationship'], row['role']))
        ids = set()
        for row in block_contract._list(source.get('routing_obligations', []), 'routing_obligations'):
            if (not isinstance(row, dict) or set(row) != {'id', 'stage', 'kind', 'pairs', 'requirement', 'source'}
                    or row['stage'] != 'routing' or row['kind'] not in block_contract.ROUTING_EVIDENCE):
                raise ValueError('unsupported source routing obligation')
            rid = block_contract._text(row['id'], 'routing obligation id')
            if rid in ids:
                raise ValueError('duplicate routing obligation')
            ids.add(rid)
            block_contract._text(row['requirement'], 'routing requirement')
            if not isinstance(row['pairs'], list) or not row['pairs']:
                raise ValueError('routing pairs must be explicit and nonempty')
            for pair in row['pairs']:
                electrical_locality.validate({'schema': electrical_locality.SCHEMA, 'obligations': [
                    {'id': rid, 'kind': 'single-leg', 'metric': 'pad-centre-to-centre', 'leg': pair}]})
                if any(pair[k]['reference'] not in roles for k in ('from', 'to')):
                    raise ValueError('routing endpoint is unallocated')
                if pair['from']['net'] != pair['to']['net']:
                    raise ValueError('routing endpoints name different nets')
            if source_authority.verified_reference(row['source'], doc.get('source_records'))['enforcement'] != 'verified':
                raise ValueError('routing obligation source is not verified')
        if normalized is not None:
            groups = normalized['groups']['groups']
            expected = {g['name']: (set(g['refs']), {'member': g['anchor']}) for g in groups}
            actual = {bid: (set(b['references']), b['anchor']) for bid, b in blocks.items()}
            if actual != expected:
                raise ValueError('block source membership/member anchors differ from prepared groups')
            empty = {'schema': electrical_locality.SCHEMA, 'obligations': []}
            if doc != normalized['intent'].get('electrical_locality', empty):
                raise ValueError('block source relationships differ from effective electrical locality')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('malformed block source: ' + str(exc)) from exc
    return source


def materialize_source(source, board_sha256):
    """Insert observed native identity into a new value; old native contracts refuse."""
    import re
    validate_source(source)
    if not isinstance(board_sha256, str) or re.fullmatch('[0-9a-f]{64}', board_sha256) is None:
        raise ValueError('observed board sha256 must be a content digest')
    contract = deepcopy(source)
    contract['schema'] = block_contract.SCHEMA
    contract['allocation']['board_sha256'] = board_sha256
    contract['eligibility']['sources']['board_sha256'] = board_sha256
    stripped = deepcopy(contract)
    stripped['schema'] = SOURCE_SCHEMA
    del stripped['allocation']['board_sha256']
    del stripped['eligibility']['sources']['board_sha256']
    if stripped != source:
        raise ValueError('block source derivation changed authored fields')
    return contract
