"""Native Stage-A face alternatives around unchanged block-candidate/2 documents.

A set binds the original capture and complete eligibility record. Each successful
native transform retains a complete recapture; the writer reproduces that native
transform before trusting it. A failed eligible face remains an explicit result.
"""
import copy
from pathlib import Path
import shutil
import tempfile

from . import blocks
from .footprint_library import canonical, digest
from .placement import validate_capture
from .storage import digest as file_digest

SCHEMA = 'agent-ee.block-candidate-set/1'
ELIGIBILITY = ('fome-mc.side-eligibility/1',
               'fome-mc.side-eligibility/1 (proposed consumer: agent-ee, AGENTEE-479)')
VARIANT_KEYS = {'status', 'capture', 'capture_sha256', 'board_sha256', 'derivation',
                'members', 'candidate', 'failure'}
MEMBER_KEYS = {'ref', 'uuid', 'source_side', 'side', 'flipped', 'pad_inventory_sha256'}


class NativeFlipRefused(ValueError):
    """The existing R5 boundary does not qualify this native side change."""


def _faces(value, label):
    if (not isinstance(value, list) or not value or len(value) != len(set(map(str, value)))
            or any(face not in ('F', 'B') for face in value)):
        raise ValueError(f'{label} must name unique F/B faces')
    return [face for face in ('F', 'B') if face in value]


def eligible_faces(record, block_id, ref=None):
    if not isinstance(record, dict) or record.get('schema') not in ELIGIBILITY:
        raise ValueError('expected a bound fome-mc.side-eligibility/1 record')
    if not isinstance(record.get('default'), dict):
        raise ValueError('side eligibility needs default faces')
    default = _faces(record['default'].get('faces'), 'default eligibility')
    entries = record.get('blocks')
    if not isinstance(entries, dict) or not isinstance(entries.get(block_id), dict):
        raise ValueError(f'no side eligibility for block {block_id}')
    entry = entries[block_id]
    faces = _faces(entry.get('eligible_faces'), f'block {block_id} eligibility')
    if not set(faces) <= set(default):
        raise ValueError(f'block {block_id}: eligible faces exceed the default permission')
    restrictions = entry.get('face_restrictions')
    if set(faces) != set(default) and (not isinstance(restrictions, list) or not restrictions):
        raise ValueError(f'block {block_id}: narrowed faces need explicit face restrictions')
    members = record.get('members', {})
    if not isinstance(members, dict):
        raise ValueError('eligibility members must be a reference mapping')
    if ref is not None and ref in members:
        if not isinstance(members[ref], dict):
            raise ValueError(f'{ref}: member eligibility must be an object')
        own = _faces(members[ref].get('eligible_faces'), f'{ref} eligibility')
        if not set(own) <= set(default):
            raise ValueError(f'{ref}: eligible faces exceed the default permission')
        return own
    return faces


def _eligibility_binding(record, capture):
    if not isinstance(record, dict) or not isinstance(record.get('sources'), dict) or record['sources'].get('board_sha256') != capture['board_sha256']:
        raise ValueError('side eligibility is not bound to this original board')


def _sources(capture, derivation, policy):
    return {'board_sha256': capture['board_sha256'], 'inventory_sha256': derivation['inventory_sha256'],
            'allocation_sha256': derivation['allocation_sha256'], 'derivation_sha256': digest(derivation),
            'policy_sha256': digest(policy)}


def _members(capture, derivation, block_id):
    found = [block for block in derivation['blocks'] if block['id'] == block_id]
    if len(found) != 1:
        raise ValueError(f'unknown or duplicate block {block_id}')
    board = {fp['ref']: fp for fp in capture['footprints']}
    return found[0], [board[ref] for ref in sorted(found[0]['references'], key=blocks._natural)]


def _capture_members(members):
    return [{'ref': fp['ref'], 'uuid': fp['uuid'], 'side': fp['side']} for fp in members]


def _requested_sides(record, block_id, face, members, overrides):
    if not isinstance(overrides, dict) or set(overrides) - {fp['ref'] for fp in members}:
        raise ValueError('member_sides must name only members of this block')
    sides = {fp['ref']: overrides.get(fp['ref'], face) for fp in members}
    for ref, side in sides.items():
        if side not in eligible_faces(record, block_id, ref):
            raise ValueError(f'{ref}: requested side {side!r} is not eligible')
    return sides


def _native_variant(source, destination, members, sides):
    """Return the actual saved full capture and native local pad identities."""
    import pcbnew
    from .placement_probe import extract
    from .placer_runner import _footprint_pad_inventory, _side
    board = pcbnew.LoadBoard(str(source))
    native = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    if len(native) != len(list(board.GetFootprints())):
        raise ValueError('native board contains duplicate footprint UUIDs')
    rows, changed = [], False
    for member in members:
        fp = native.get(member['uuid'])
        if fp is None or fp.GetReference() != member['ref'] or _side(fp) != member['side']:
            raise ValueError('captured member identity differs from the native source')
        side = sides[member['ref']]
        flipped = side != _side(fp)
        if flipped:
            if fp.IsLocked():
                raise NativeFlipRefused(f"{member['ref']}: locked footprint cannot change side")
            if any(pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS) == pcbnew.PAD_SHAPE_CUSTOM
                   or pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL for pad in fp.Pads()):
                raise NativeFlipRefused(
                    f"{member['ref']}: flipped custom or per-layer pad geometry is unqualified")
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            changed = True
        rows.append({'ref': member['ref'], 'uuid': member['uuid'], 'source_side': member['side'],
                     'side': side, 'flipped': flipped,
                     'pad_inventory_sha256': digest(_footprint_pad_inventory(fp))})
    destination.parent.mkdir(parents=True, exist_ok=False)
    for suffix in ('.kicad_pro', '.kicad_dru'):
        companion = source.with_suffix(suffix)
        if companion.is_file():
            shutil.copyfile(companion, destination.with_suffix(suffix))
    if changed:
        pcbnew.SaveBoard(str(destination), board)
    else:
        shutil.copyfile(source, destination)  # Preserve original-face candidate source identity.
    capture = extract(destination)
    saved = pcbnew.LoadBoard(str(destination))
    saved_by_uuid = {fp.m_Uuid.AsString(): fp for fp in saved.GetFootprints()}
    for row in rows:
        fp = saved_by_uuid[row['uuid']]
        if (_side(fp) != row['side']
                or digest(_footprint_pad_inventory(fp)) != row['pad_inventory_sha256']):
            raise ValueError(f"native save changed the declared transform for {row['ref']}")
    return capture, rows


def produce(source, capture, inventory, derivation, block_id, policy, eligibility, output_directory,
            *, heartbeat=None, compact=True, member_sides=None):
    """Pack every eligible anchor face on a fresh disposable native board copy.

    ``member_sides`` maps face keys to explicit per-member face overrides. The
    anchor must stay on the face key; other members require their own eligibility.
    All digests of JSON records are canonical ``footprint_library.digest`` hashes.
    """
    from .placement_probe import extract
    source, output = Path(source), Path(output_directory)
    validate_capture(capture)
    original_hash = file_digest(source)
    if original_hash != capture['board_sha256'] or digest(extract(source)) != digest(capture):
        raise ValueError('Stage-A capture is not the exact native source capture')
    if derivation['board_sha256'] != original_hash:
        raise ValueError('derivation belongs to another board capture')
    block, members = _members(capture, derivation, block_id)
    _eligibility_binding(eligibility, capture)
    faces = eligible_faces(eligibility, block_id)
    member_sides = {} if member_sides is None else member_sides
    if not isinstance(member_sides, dict) or set(member_sides) - set(faces):
        raise ValueError('member_sides face keys must be eligible anchor faces')
    anchor = blocks.anchor_of(block, {fp['ref']: fp for fp in capture['footprints']})
    requested = {face: _requested_sides(eligibility, block_id, face, members, member_sides.get(face, {}))
                 for face in faces}
    if any(sides[anchor] != face for face, sides in requested.items()):
        raise ValueError('variant face must be the actual anchor side')
    output.mkdir(parents=True, exist_ok=False)
    result = {'schema': SCHEMA, 'block_id': block_id, 'sources': _sources(capture, derivation, policy),
              'eligibility': {'record': copy.deepcopy(eligibility), 'record_sha256': digest(eligibility),
                              'eligible_faces': faces},
              'captured_members': _capture_members(members), 'variants': {}}
    for face in faces:
        variant = dict.fromkeys(VARIANT_KEYS)
        try:
            native_capture, rows = _native_variant(source, output / face / source.name, members, requested[face])
        except NativeFlipRefused as exc:
            variant.update(status='native_flip_refused', members=[{
                'ref': fp['ref'], 'uuid': fp['uuid'], 'source_side': fp['side'],
                'side': requested[face][fp['ref']], 'flipped': fp['side'] != requested[face][fp['ref']],
                'pad_inventory_sha256': None} for fp in members],
                failure={'status': 'native_flip_refused', 'outcome': 'unknown', 'reason': str(exc)})
        else:
            native_derivation = {**copy.deepcopy(derivation), 'board_sha256': native_capture['board_sha256']}
            packed = blocks._plain(blocks.pack_block(native_capture, inventory, native_derivation, block_id, policy,
                                                     heartbeat=heartbeat, compact=compact))
            variant.update(status=packed['status'], capture=native_capture, capture_sha256=digest(native_capture),
                           board_sha256=native_capture['board_sha256'], derivation=native_derivation, members=rows,
                           candidate=packed['candidate'] if packed['status'] == 'packed' else None,
                           failure=None if packed['status'] == 'packed' else packed)
            (output / face / 'capture.json').write_bytes(canonical(native_capture) + b'\n')
        result['variants'][face] = variant
        # Retain completed faces if a later native operation or search raises.
        (output / 'candidate-set.json').write_bytes(canonical(result) + b'\n')
    if file_digest(source) != original_hash:
        raise ValueError('native source changed during variant production')
    validate(result, capture, inventory, derivation, policy, eligibility)
    return result


def _structure(document, capture, eligibility=None):
    validate_capture(capture)
    blocks._keys(document, {'schema', 'block_id', 'sources', 'eligibility', 'captured_members', 'variants'},
                 label='block candidate set')
    if document['schema'] != SCHEMA:
        raise ValueError(f'expected {SCHEMA}')
    blocks._text(document['block_id'], 'candidate-set block id')
    bundle = document['eligibility']
    blocks._keys(bundle, {'record', 'record_sha256', 'eligible_faces'}, label='candidate-set eligibility')
    if digest(bundle['record']) != bundle['record_sha256']:
        raise ValueError('eligibility record canonical hash mismatch')
    if eligibility is not None and digest(eligibility) != bundle['record_sha256']:
        raise ValueError('candidate set belongs to another eligibility record')
    _eligibility_binding(bundle['record'], capture)
    faces = eligible_faces(bundle['record'], document['block_id'])
    if bundle['eligible_faces'] != faces or not isinstance(document['variants'], dict) or set(document['variants']) != set(faces):
        raise ValueError('every eligible face needs exactly one explicit result')
    board = {fp['ref']: fp for fp in capture['footprints']}
    rows = document['captured_members']
    if not isinstance(rows, list) or not rows:
        raise ValueError('candidate set needs captured members')
    seen_refs, seen_uuids = set(), set()
    for row in rows:
        blocks._keys(row, {'ref', 'uuid', 'side'}, label='captured member')
        blocks._text(row['ref'], 'captured member reference')
        blocks._text(row['uuid'], 'captured member UUID')
        if row['ref'] in seen_refs or row['uuid'] in seen_uuids:
            raise ValueError('candidate set needs unique captured members')
        seen_refs.add(row['ref'])
        seen_uuids.add(row['uuid'])
        fp = board.get(row['ref'])
        if fp is None or row != {'ref': fp['ref'], 'uuid': fp['uuid'], 'side': fp['side']}:
            raise ValueError('captured member identity differs from original capture')
    for face, variant in document['variants'].items():
        blocks._keys(variant, VARIANT_KEYS, label='face variant')
        members = variant['members']
        if not isinstance(members, list) or len(members) != len(rows):
            raise ValueError('variant members differ from captured membership')
        for member, original in zip(members, rows):
            blocks._keys(member, MEMBER_KEYS, label='variant member')
            if ({key: member[key] for key in ('ref', 'uuid')} != {key: original[key] for key in ('ref', 'uuid')}
                    or member['source_side'] != original['side']
                    or member['side'] not in eligible_faces(bundle['record'], document['block_id'], member['ref'])
                    or type(member['flipped']) is not bool or member['flipped'] != (member['side'] != original['side'])):
                raise ValueError('variant member identity, side or native flip declaration differs')
        if variant['status'] == 'native_flip_refused':
            if (any(variant[key] is not None for key in ('capture', 'capture_sha256', 'board_sha256', 'derivation', 'candidate'))
                    or any(member['pad_inventory_sha256'] is not None for member in members)
                    or not isinstance(variant['failure'], dict) or variant['failure'].get('status') != variant['status']):
                raise ValueError('native flip refusal carries inconsistent evidence')
            continue
        validate_capture(variant['capture'])
        if (digest(variant['capture']) != variant['capture_sha256']
                or variant['capture']['board_sha256'] != variant['board_sha256']):
            raise ValueError('variant capture hash mismatch')
        for member in members:
            blocks._hex(member['pad_inventory_sha256'], 'native member pad inventory')
        if variant['status'] == 'packed':
            if variant['failure'] is not None:
                raise ValueError('packed variant cannot carry a failure')
            candidate = variant['candidate']
            blocks.validate_candidate(candidate)
            if candidate['block_id'] != document['block_id']:
                raise ValueError('variant candidate belongs to another block')
            by_ref = {member['ref']: member for member in members}
            if ({row['reference'] for row in candidate['members']} != set(by_ref)
                    or any(row['side'] != by_ref[row['reference']]['side'] for row in candidate['members'])
                    or by_ref[candidate['frame']['anchor']]['side'] != face):
                raise ValueError('candidate face or membership differs from native variant')
        elif (variant['candidate'] is not None or not isinstance(variant['failure'], dict)
              or variant['failure'].get('status') != variant['status']):
            raise ValueError('failed eligible face needs its complete typed failure')
    return rows


def validate(document, capture, inventory, derivation, policy, eligibility=None):
    """Pure structural/source/admission validation; native reproduction is separate."""
    rows = _structure(document, capture, eligibility)
    _, members = _members(capture, derivation, document['block_id'])
    if rows != _capture_members(members) or document['sources'] != _sources(capture, derivation, policy):
        raise ValueError('candidate-set membership or original source binding mismatch')
    for variant in document['variants'].values():
        if variant['status'] == 'native_flip_refused':
            continue
        expected = {**copy.deepcopy(derivation), 'board_sha256': variant['board_sha256']}
        if variant['derivation'] != expected:
            raise ValueError('variant derivation differs beyond the native board binding')
        if variant['status'] == 'packed':
            admitted = blocks.admit(variant['candidate'], variant['capture'], inventory, expected, policy)
            if admitted['status'] != 'admitted':
                raise ValueError(f"variant candidate admission refused: {admitted['problems']}")


def verify_native(source, document, capture=None):
    """Recreate every declared transform from original board bytes before planning."""
    from .placement_probe import extract
    source = Path(source)
    actual = extract(source)
    capture = actual if capture is None else capture
    if digest(actual) != digest(capture) or document['sources']['board_sha256'] != actual['board_sha256']:
        raise ValueError('candidate-set original capture differs from native source')
    rows = _structure(document, capture)
    members = [{'ref': row['ref'], 'uuid': row['uuid'], 'side': row['side']} for row in rows]
    with tempfile.TemporaryDirectory(prefix='agent-ee-block-variants-') as temporary:
        for face, variant in document['variants'].items():
            sides = {member['ref']: member['side'] for member in variant['members']}
            try:
                fresh, native_rows = _native_variant(source, Path(temporary) / face / source.name, members, sides)
            except NativeFlipRefused as exc:
                if variant['status'] != 'native_flip_refused' or variant['failure']['reason'] != str(exc):
                    raise ValueError('declared native failure does not match the source') from exc
            else:
                if (variant['status'] == 'native_flip_refused' or digest(fresh) != variant['capture_sha256']
                        or canonical(fresh) != canonical(variant['capture']) or native_rows != variant['members']):
                    raise ValueError('supplied variant differs from the reproduced native capture')
    if file_digest(source) != actual['board_sha256']:
        raise ValueError('native source changed during variant verification')


def verify_request(source, capture, section):
    """Validate native evidence for every candidate set before the Rust call."""
    if not isinstance(section, dict) or not isinstance(section.get('candidates'), list):
        raise ValueError('blocks request needs a candidate list')
    documents = []
    seen = set()
    for entry in section['candidates']:
        if not isinstance(entry, dict) or not isinstance(entry.get('candidate'), dict):
            raise ValueError('block candidate entry must contain a document')
        document = entry['candidate']
        if document.get('schema') != SCHEMA:
            continue
        if set(entry) != {'candidate'}:
            raise ValueError('a native candidate set cannot carry a library remapping')
        if document['block_id'] in seen:
            raise ValueError('duplicate candidate set for block ' + document['block_id'])
        seen.add(document['block_id'])
        eligibility = section.get('side_eligibility')
        if eligibility is None:
            raise ValueError('native candidate sets require blocks.side_eligibility')
        _structure(document, capture, eligibility)
        verify_native(source, document, capture)
        documents.append(document)
    return documents


def authorization(source, capture, documents, plan, eligibility):
    """Derive exact R5 transitions from selected, independently verified variants.

    The caller first invokes ``verify_request`` before planning. A selected face,
    the selected inner candidate hash and the whole-set hash must all agree.
    """
    from .placer_runner import SIDE_CHANGES
    if file_digest(source) != capture['board_sha256']:
        raise ValueError('side authorization source board changed')
    _eligibility_binding(eligibility, capture)
    original = {fp['uuid']: fp for fp in capture['footprints']}
    placements = plan['placements']
    planned = {row['uuid']: row for row in placements}
    if len(planned) != len(placements) or set(planned) != set(original):
        raise ValueError('side authorization requires exact original placement UUID coverage')
    for uuid, fp in original.items():
        if planned[uuid]['ref'] != fp['ref']:
            raise ValueError('planned reference differs from original footprint UUID')
    arrangement = plan['block_arrangement']
    arranged = {row['block']: row for row in arrangement['arranged']}
    recorded = {row['block']: row for row in arrangement['blocks']}
    if len(arranged) != len(arrangement['arranged']) or len(recorded) != len(arrangement['blocks']):
        raise ValueError('duplicate block in selected arrangement')
    authorized = {}
    for document in documents:
        _structure(document, capture, eligibility)
        bid = document['block_id']
        if bid not in arranged or bid not in recorded:
            raise ValueError('candidate set has no selected arranged block')
        face = arranged[bid].get('selected_face')
        variant = document['variants'].get(face)
        if variant is None or variant['status'] != 'packed':
            raise ValueError('selected block face is not an eligible packed variant')
        if (recorded[bid].get('selected_face') != face
                or recorded[bid].get('candidate_set_sha256') != digest(document)
                or recorded[bid]['candidate_sha256'] != digest(variant['candidate'])):
            raise ValueError('selected block variant identity differs from native evidence')
        for member in variant['members']:
            uuid = member['uuid']
            if uuid in authorized:
                raise ValueError('a footprint belongs to more than one selected candidate set')
            if planned[uuid].get('side', original[uuid]['side']) != member['side']:
                raise ValueError('planned member side differs from the selected native variant')
            authorized[uuid] = member['side']
    changes = []
    for uuid, fp in original.items():
        side = planned[uuid].get('side', fp['side'])
        if side != fp['side']:
            if authorized.get(uuid) != side or planned[uuid]['fixed']:
                raise ValueError('side change lacks selected eligible movable-member authority')
            changes.append({'ref': fp['ref'], 'uuid': uuid, 'side': side})
    return ({'schema': SIDE_CHANGES, 'source_sha256': capture['board_sha256'],
             'changes': sorted(changes, key=lambda row: blocks._natural(row['ref']))} if changes else None)
