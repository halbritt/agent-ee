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
    section = dict(contract=contract, side_eligibility=contract.get('eligibility'), candidates=[],
        allocation_text=json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
            dict(id=g['name'], references=g['refs'], anchor=g['anchor']) for g in groups]}))
    try:
        block_contract.validate(original, section, normalized['intent'])
        anchors = {g['name']: {'member': g['anchor']} for g in groups}
        for block in contract['allocation']['blocks']:
            if anchors.get(block['id']) != block['anchor']:
                raise ValueError('prepared block contract requires the declared group member anchor')
    except (ValueError, KeyError, TypeError) as exc:
        raise JobRefusal('block contract: ' + str(exc)) from exc
    fixed = [references.get(r['uuid'], r['native_ref']) for r in records
             if r['role'] in ('fixed_body', 'fixed_copper_mark')]
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
