"""Sub-layout reuse: replicate a proven module layout into new instances (AGENTEE-56).

A request names, per group, a captured sub-board ``.kicad_pcb``, the complete
mapping of its footprint references to the top board's references, the
anchor's target position and a declared quarter-turn rotation. The anchor is
the sub-board footprint with the most pads (ties: natural reference order).
Every member footprint is placed at the anchor target plus its rotated
sub-board offset from the anchor; tracks, arcs, vias, zones and non-outline
graphics are copied with new deterministic UUIDs.

Sub-board nets bind to top-board nets by a vote over paired pads. Top pads
without a net abstain; a sub net maps only when every voting pad agrees.
Copper on an unmapped or contested net is withheld and reported, never
created on net 0. A group is pulled only when every member is new: unlocked
and wholly outside the top board's outline (the off-board staging row of
board building and forward annotation). A missing or mismatched sub-board
leaves its group unknown; the sub-board sha256 is always recorded.

pcbnew runs only in the sandboxed ``agent_ee.sublayout_probe`` child. The
saved result is re-read by a fresh child and must show exactly the planned
change. Native DRC before and after, through the same sandbox, must show no
new violation inside any group's bounding box. Group-local only: integration
routing remains kicad-route's job.
"""
import argparse
import json
import re
import shutil
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath

from .placer import rotate
from .storage import canonical, digest, identity

REQUEST_SCHEMA = 'agent-ee.sublayout-request/1'
PLAN_SCHEMA = 'agent-ee.sublayout-plan/1'
RESULT_SCHEMA = 'agent-ee.sublayout-result/1'
# Fixed namespace for the UUIDs of reused items; keyed by group, sub-board bytes and source UUID.
NAMESPACE = uuid.UUID('5c0d7e2a-56a1-5b3e-9f4d-1e6b8a2c7d90')
ROTATIONS = (0, 90, 180, 270)
# Sub-board layers that describe the sub-board itself, not the module.
BOARD_LAYERS = ('Edge.Cuts', 'Margin')
GROUP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
SHA256 = re.compile(r'[0-9a-f]{64}')
DRC_TIMEOUT = 300


class SublayoutError(ValueError):
    def __init__(self, code, message, location='/'):
        super().__init__(message)
        self.code, self.location = code, location


def _seal(record):
    record.pop('canonical_sha256', None)
    record['canonical_sha256'] = identity(record)
    return record


def _natural(text):
    head = text.rstrip('0123456789')
    tail = text[len(head):]
    return (head, int(tail) if tail else -1, text)


def _point(value, label):
    if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int for v in value):
        raise SublayoutError('request', f'{label} must be [x, y] integer nanometres', label)
    return value


def read_request(value):
    """Validate an ``agent-ee.sublayout-request/1`` document; refuse anything else."""
    if not isinstance(value, dict) or set(value) != {'schema', 'groups'} or value['schema'] != REQUEST_SCHEMA:
        raise SublayoutError('request', f'request must be exactly {{schema: {REQUEST_SCHEMA}, groups}}')
    groups = value['groups']
    if not isinstance(groups, list) or not groups:
        raise SublayoutError('request', 'groups must be a non-empty list', '/groups')
    ids, tops = set(), set()
    for index, group in enumerate(groups):
        where = f'/groups/{index}'
        required = {'id', 'sub_board', 'members', 'anchor_at_nm'}
        optional = {'rotation_deg', 'sub_board_sha256'}
        if not isinstance(group, dict) or not required <= set(group) or set(group) - required - optional:
            raise SublayoutError('request', f'group needs {sorted(required)}, optional {sorted(optional)}', where)
        if not isinstance(group['id'], str) or not GROUP_ID.fullmatch(group['id']) or group['id'] in ids:
            raise SublayoutError('request', 'group id must be a unique short identifier', where + '/id')
        ids.add(group['id'])
        path = group['sub_board']
        parts = PurePosixPath(path).parts if isinstance(path, str) else ()
        if not parts or PurePosixPath(path).is_absolute() or '..' in parts or '\\' in path \
                or not path.endswith('.kicad_pcb') or str(PurePosixPath(path)) != path:
            raise SublayoutError('request', 'sub_board must be a normal relative .kicad_pcb path', where + '/sub_board')
        members = group['members']
        if not isinstance(members, dict) or not members or \
                any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in members.items()):
            raise SublayoutError('request', 'members must map sub-board references to top-board references',
                                 where + '/members')
        if len(set(members.values())) != len(members) or tops & set(members.values()):
            raise SublayoutError('request', 'a top-board reference may belong to only one group member',
                                 where + '/members')
        tops |= set(members.values())
        _point(group['anchor_at_nm'], where + '/anchor_at_nm')
        if group.get('rotation_deg', 0) not in ROTATIONS or type(group.get('rotation_deg', 0)) is not int:
            raise SublayoutError('request', f'rotation_deg must be one of {list(ROTATIONS)}', where + '/rotation_deg')
        declared = group.get('sub_board_sha256')
        if declared is not None and (not isinstance(declared, str) or not SHA256.fullmatch(declared)):
            raise SublayoutError('request', 'sub_board_sha256 must be lowercase hex sha256',
                                 where + '/sub_board_sha256')
    return value


def _transform(point, origin, target, rotation):
    x, y = rotate([point[0] - origin[0], point[1] - origin[1]], rotation // 90)
    return [x + target[0], y + target[1]]


def _local(pad, layer):
    # A flipped footprint stores its pads' local y mirrored.
    x, y = pad['local_nm']
    return (x, -y) if layer == 'B.Cu' else (x, y)


def _pairs(sub_fp, top_fp):
    """Pads paired by number and local geometry, or None when the land patterns differ."""
    def keyed(fp):
        return sorted(((p['number'], _local(p, fp['layer'])), p) for p in fp['pads'])
    sub, top = keyed(sub_fp), keyed(top_fp)
    if [k for k, _ in sub] != [k for k, _ in top]:
        return None
    return [(s, t) for (_, s), (_, t) in zip(sub, top)]


def _outside(box, edge):
    return box[2] < edge[0] or box[0] > edge[2] or box[3] < edge[1] or box[1] > edge[3]


def _refused(group, code, message, **fields):
    return dict(status='refused', findings=[dict(code=code, location=f"/groups/{group['id']}", message=message)],
                **fields)


def _vote(sub_net, pairs):
    votes = Counter(t['net'] for s, t in pairs if s['net'] == sub_net and t['net'])
    abstentions = sum(1 for s, t in pairs if s['net'] == sub_net and not t['net'])
    entry = dict(sub_net=sub_net, votes=dict(sorted(votes.items())), abstentions=abstentions, top_net=None)
    if not votes:
        entry['status'] = 'unmapped'
    elif len(votes) > 1:
        entry['status'] = 'contested'
    else:
        entry.update(status='mapped', top_net=next(iter(votes)))
    return entry


def plan_group(group, top, sub, sub_sha256):
    """Plan one group from board facts; every refusal leaves the top board untouched."""
    rotation = group.get('rotation_deg', 0)
    members = group['members']
    subs = {}
    for fp in sub['footprints']:
        if fp['reference'] in subs:
            return _refused(group, 'sub_board_reference', f"sub-board reference {fp['reference']} is not unique")
        subs[fp['reference']] = fp
    if set(subs) != set(members):
        return _refused(group, 'member_coverage', 'members must map every sub-board footprint exactly: '
                        f'unmapped {sorted(set(subs) - set(members))}, absent {sorted(set(members) - set(subs))}')
    tops = {}
    for fp in top['footprints']:
        tops.setdefault(fp['reference'], []).append(fp)
    if not top['edge_items']:
        return _refused(group, 'no_outline', 'top board has no Edge.Cuts outline; new footprints are undefined')
    used = {fp['layer'] for fp in sub['footprints']} | {f['layer'] for fp in sub['footprints'] for f in fp['fields']}
    used |= {layer for i in sub['items'] if not set(i.get('layers', ())) & set(BOARD_LAYERS)
             for layer in i.get('layers', ())}
    absent = sorted(used - set(top['enabled_layers']))
    if absent:
        return _refused(group, 'layer_stack', f'sub-board layers absent from the top board: {absent}')
    pairs, footprints = {}, []
    for ref in sorted(members, key=_natural):
        found = tops.get(members[ref], [])
        if len(found) != 1:
            return _refused(group, 'top_reference', f'top-board reference {members[ref]} matches {len(found)} footprints')
        sub_fp, top_fp = subs[ref], found[0]
        if sub_fp['fpid'] != top_fp['fpid']:
            return _refused(group, 'footprint_mismatch', f"{ref} is {sub_fp['fpid']} but {members[ref]} is {top_fp['fpid']}")
        if sub_fp['orientation_deg'] % 90:
            return _refused(group, 'orientation_unqualified',
                            f"{ref} orientation {sub_fp['orientation_deg']} is not a quarter turn")
        if top_fp['locked'] or not _outside(top_fp['bbox_nm'], top['edge_bbox_nm']):
            return _refused(group, 'not_new', f'{members[ref]} is locked or already on the board; '
                            'a layout is pulled only when every footprint in the group is new')
        pairs[ref] = _pairs(sub_fp, top_fp)
        if pairs[ref] is None:
            return _refused(group, 'land_pattern_mismatch', f'{ref} and {members[ref]} pads differ')
    unsupported = sorted(f"{i['kind']} {i['uuid']}" for i in sub['items'] if not i['supported'])
    if unsupported:
        return _refused(group, 'unsupported_item', f'sub-board items cannot be described: {unsupported}')
    anchor = sorted(subs, key=lambda r: (-len(subs[r]['pads']), _natural(r)))[0]
    origin, target = subs[anchor]['position_nm'], group['anchor_at_nm']
    every = [pair for ref in sorted(pairs, key=_natural) for pair in pairs[ref]]
    copper = {i['net'] for i in sub['items'] if i['net']}
    nets = {}
    for net in sorted({s['net'] for s, _ in every if s['net']} | copper):
        nets[net] = _vote(net, every)
    def uid(*parts):
        return str(uuid.uuid5(NAMESPACE, json.dumps([group['id'], sub_sha256, *parts], separators=(',', ':'))))
    copy, withheld = [], []
    for item in sub['items']:
        if set(item['layers']) & set(BOARD_LAYERS):
            continue
        entry = nets.get(item['net'])
        if item['net'] and entry['status'] != 'mapped':
            withheld.append(dict(source_uuid=item['uuid'], kind=item['kind'], sub_net=item['net'],
                                 reason=entry['status']))
            continue
        moved = dict(item, uuid=uid('item', item['uuid']), net=entry['top_net'] if item['net'] else '',
                     points_nm=[_transform(p, origin, target, rotation) for p in item['points_nm']])
        for key in ('bbox_nm', 'filled'):
            moved.pop(key, None)
        if 'angle_deg' in moved:
            moved['angle_deg'] = (moved['angle_deg'] + rotation) % 360
        copy.append(dict(source_uuid=item['uuid'], uuid=moved['uuid'], net=moved['net'], expected=moved))
    for ref in sorted(members, key=_natural):
        sub_fp, top_fp = subs[ref], tops[members[ref]][0]
        top_fields = {f['name'] for f in top_fp['fields']}
        fields = [dict(name=f['name'], layer=f['layer'], visible=f['visible'],
                       angle_deg=(f['angle_deg'] + rotation) % 360,
                       position_nm=_transform(f['position_nm'], origin, target, rotation))
                  for f in sub_fp['fields'] if f['name'] in ('Reference', 'Value') and f['name'] in top_fields]
        footprints.append(dict(
            sub_ref=ref, top_ref=members[ref], uuid=top_fp['uuid'], layer=sub_fp['layer'],
            orientation_deg=(sub_fp['orientation_deg'] + rotation) % 360,
            position_nm=_transform(sub_fp['position_nm'], origin, target, rotation), fields=fields,
            pads={t['uuid']: _transform(s['position_nm'], origin, target, rotation) for s, t in pairs[ref]},
            sub_offset_nm=[sub_fp['position_nm'][0] - origin[0], sub_fp['position_nm'][1] - origin[1]]))
    taken = {fp['uuid'] for fp in top['footprints']} | {p['uuid'] for fp in top['footprints'] for p in fp['pads']}
    taken |= {i['uuid'] for i in top['items']} | {g['uuid'] for g in top['groups']}
    fresh = [c['uuid'] for c in copy] + [uid('group')]
    if taken & set(fresh):
        return _refused(group, 'uuid_collision', 'the top board already holds items of this reuse')
    return dict(status='apply', findings=[], name=f"sublayout:{group['id']}", uuid=uid('group'),
                sub_anchor=dict(sub_ref=anchor, top_ref=members[anchor]), sub_anchor_nm=origin,
                anchor_at_nm=target, rotation_deg=rotation, footprints=footprints, copy=copy,
                withheld=withheld, net_map=list(nets.values()))


# --------------------------------------------------------------------------
# Sandboxed native steps


def _child(inputs, work, argv, timeout=300):
    from . import native_run
    record = dict(native_run.run(native_run.Intent('pcbnew_script', ['/usr/bin/python3', '-B', '-m', 'agent_ee.sublayout_probe', *argv], timeout=timeout), inputs=inputs, work=work))
    if record['execution'] != 'ok':
        console = (Path(work) / 'console.log').read_text(errors='replace')[-2000:]
        raise SublayoutError('native_child', f'pcbnew child {record["execution"]} '
                             f'(exit {record["exit_code"]}): {console}')
    return record


def board_facts(board, work, sha256=None):
    """Facts from a private read-only copy of the board, through the sandboxed child.

    ``sha256``, when given, binds the copy to bytes already recorded.
    """
    work = Path(work)
    inputs = work / 'facts-input'
    inputs.mkdir(parents=True)
    shutil.copyfile(board, inputs / 'board.kicad_pcb')
    if digest(inputs / 'board.kicad_pcb') != (sha256 or digest(board)):
        raise SublayoutError('stale_input', 'board changed while it was copied')
    _child(inputs, work / 'facts', ['facts', '/candidate/board.kicad_pcb', '-o', '/work/facts.json'])
    return json.loads((work / 'facts' / 'facts.json').read_text())


def zone_fills(board, work, zones, sha256=None):
    """Saved and refilled fill of each named zone, from a private copy of the board refilled in the
    sandboxed child; the board itself is never filled or written. ``sha256`` binds the copy as in
    ``board_facts``."""
    work = Path(work)
    inputs = work / 'fills-input'
    inputs.mkdir(parents=True)
    shutil.copyfile(board, inputs / 'board.kicad_pcb')
    if digest(inputs / 'board.kicad_pcb') != (sha256 or digest(board)):
        raise SublayoutError('stale_input', 'board changed while it was copied')
    _child(inputs, work / 'fills', ['fills', '/candidate/board.kicad_pcb', *(a for z in zones for a in ('--zone', z)),
                                    '-o', '/work/fills.json'])
    return json.loads((work / 'fills' / 'fills.json').read_text())


def _mm(value):
    return round(value * 1000000)


def native_drc(board, work, *, project_board=None):
    """Native DRC of a board copy inside the networkless sandbox; failure is missing coverage."""
    from . import native_run
    project_board = Path(project_board if project_board is not None else board)
    work = Path(work)
    inputs = work / 'drc-input'
    inputs.mkdir(parents=True)
    staged = inputs / project_board.name
    shutil.copyfile(board, staged)
    for suffix in ('.kicad_pro', '.kicad_prl', '.kicad_dru'):
        source = project_board.with_suffix(suffix)
        if source.is_file():
            shutil.copyfile(source, staged.with_suffix(suffix))
    config = work / 'drc' / 'kicad-config' / '10.0'
    config.mkdir(parents=True)
    # Empty global tables: the saved board embeds every footprint it uses.
    (config / 'fp-lib-table').write_text('(fp_lib_table\n\t(version 7)\n)\n')
    (config / 'sym-lib-table').write_text('(sym_lib_table\n\t(version 7)\n)\n')
    record = dict(native_run.run(native_run.drc('/candidate/' + staged.name, '/work/drc.json', units='mm',
                                                timeout=DRC_TIMEOUT), inputs=inputs, work=work / 'drc'))
    path = work / 'drc' / 'drc.json'
    if record['execution'] != 'ok' or record['exit_code'] != 0 or not path.is_file():
        raise SublayoutError('drc_unavailable', f"native DRC {record['execution']} (exit {record['exit_code']})")
    report = json.loads(path.read_text())
    if report.get('coordinate_units') != 'mm' or any(not isinstance(report.get(k), list)
                                                     for k in ('violations', 'unconnected_items')):
        raise SublayoutError('drc_unavailable', 'native DRC report lacks counted mm evaluations')
    for violation in report['violations']:
        items = violation.get('items')
        if not isinstance(violation.get('type'), str) or not isinstance(items, list) or not items or any(
                not isinstance(i, dict) or not isinstance(i.get('uuid'), str) or not isinstance(i.get('pos'), dict)
                for i in items):
            raise SublayoutError('drc_unavailable', 'native DRC report has a malformed violation')
    from .design_rule_provenance import classify_finding, finding_sources, require_evaluated_limits
    from .outline_containment import sandboxed
    try:
        containment = sandboxed(inputs, staged.name, work / 'containment')
        sources = finding_sources(staged, report['violations'])
        require_evaluated_limits(report, sources)
        classified = [classify_finding(dict(v), sources) for v in report['violations'] + containment]
    except (OSError, ValueError) as exc:
        raise SublayoutError('drc_unavailable', str(exc)) from exc
    from .storage import write_json
    write_json(work / 'drc' / 'constraints.json', {'sources': sources, 'violations': classified})
    raw_count = len(report['violations'])
    report = dict(report, violations=classified)
    return dict(report=report, report_sha256=digest(path), kicad_version=report.get('kicad_version'),
                violations=raw_count, unconnected_items=len(report['unconnected_items']))


def _key(violation):
    from .design_rule_provenance import numeric_finding
    key = (violation['type'], tuple(sorted(i['uuid'] for i in violation['items'])))
    if numeric_finding(violation):
        return (*key, violation.get('description'), canonical(violation['items']))
    return key


def drc_delta(before, after, bbox, owned):
    """Violations of ``after`` inside ``bbox`` or naming an ``owned`` item that ``before`` lacks."""
    known = {_key(v) for v in before['violations']}
    new = []
    for violation in after['violations']:
        inside = any(i['uuid'] in owned or (
            bbox[0] <= _mm(i['pos']['x']) <= bbox[2] and bbox[1] <= _mm(i['pos']['y']) <= bbox[3])
            for i in violation['items'])
        if inside and _key(violation) not in known:
            new.append(dict(**({'constraint_provenance': violation['constraint_provenance']}
                               if 'constraint_provenance' in violation else {}),
                            type=violation['type'], severity=violation.get('severity'),
                            description=violation.get('description'),
                            items=[dict(uuid=i['uuid'], description=i.get('description'),
                                        pos_nm=[_mm(i['pos']['x']), _mm(i['pos']['y'])])
                                   for i in violation['items']]))
    return new


# --------------------------------------------------------------------------
# Verification of the saved board


def _box(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def footprint_error(want, fp, old):
    """Why a saved group footprint differs from its planned transform, or None when it is exact."""
    if (fp['position_nm'], fp['orientation_deg'], fp['layer']) != \
            (want['position_nm'], want['orientation_deg'], want['layer']):
        return f"{want['top_ref']} pose differs from the sub-board transform"
    pads = {p['uuid']: p for p in fp['pads']}
    if {p['uuid']: p['net'] for p in fp['pads']} != {p['uuid']: p['net'] for p in old['pads']}:
        return f"{want['top_ref']} pad nets changed"
    if any(pads[u]['position_nm'] != xy for u, xy in want['pads'].items() if u in pads) or set(pads) != set(want['pads']):
        return f"{want['top_ref']} pad positions differ from the sub-board transform"
    fields = {f['name']: f for f in fp['fields']}
    for field in want['fields']:
        got = fields.get(field['name'])
        if got is None or (got['position_nm'], got['angle_deg'], got['layer'], got['visible']) != \
                (field['position_nm'], field['angle_deg'], field['layer'], field['visible']):
            return f"{want['top_ref']} {field['name']} differs from the sub-board"
    return None


def module_region(plan, sub):
    """The planned group's region: the sub-board module's extent (footprints and every copied
    item, not the sub-board outline) moved and rotated exactly like its copies."""
    boxes = [fp['bbox_nm'] for fp in sub['footprints']]
    boxes += [i['bbox_nm'] for i in sub['items'] if not set(i.get('layers', ())) & set(BOARD_LAYERS)]
    corners = []
    for box in boxes:
        a, b = (_transform(p, plan['sub_anchor_nm'], plan['anchor_at_nm'], plan['rotation_deg'])
                for p in ((box[0], box[1]), (box[2], box[3])))
        corners.append([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])])
    return _box(corners)


def verify(plan, before, after):
    """The saved board must show exactly the planned change; returns each group's owned items and box."""
    old = {fp['uuid']: fp for fp in before['footprints']}
    new = {fp['uuid']: fp for fp in after['footprints']}
    if set(old) != set(new):
        raise SublayoutError('verification', 'footprint set changed')
    planned = {fp['uuid']: fp for g in plan['groups'] for fp in g['footprints']}
    for key, fp in new.items():
        if key not in planned:
            if fp != old[key]:
                raise SublayoutError('verification', f"footprint {fp['reference']} outside every group changed")
            continue
        error = footprint_error(planned[key], fp, old[key])
        if error:
            raise SublayoutError('verification', error)
    old_items = {i['uuid']: i for i in before['items']}
    new_items = {i['uuid']: i for i in after['items']}
    copies = {c['uuid']: c for g in plan['groups'] for c in g['copy']}
    for key, item in old_items.items():
        if new_items.get(key) != item:
            raise SublayoutError('verification', f"existing {item['kind']} {key} changed")
    if set(new_items) - set(old_items) != set(copies):
        raise SublayoutError('verification', 'copied items differ from the plan')
    for key, copy in copies.items():
        got = {k: v for k, v in new_items[key].items() if k not in ('bbox_nm', 'filled')}
        if got != copy['expected']:
            raise SublayoutError('verification', f"copied {got['kind']} {key} differs from the sub-board transform "
                                 'or the voted net')
    groups = {g['uuid']: g for g in after['groups']}
    if {g['uuid']: g for g in before['groups']} != {k: v for k, v in groups.items() if k in
                                                   {g['uuid'] for g in before['groups']}}:
        raise SublayoutError('verification', 'existing groups changed')
    owned = {}
    for group in plan['groups']:
        members = {fp['uuid'] for fp in group['footprints']} | {c['uuid'] for c in group['copy']}
        saved = groups.get(group['uuid'])
        if saved is None or saved['name'] != group['name'] or set(saved['members']) != members:
            raise SublayoutError('verification', f"group {group['name']} membership differs from the plan")
        boxes = [new[u]['bbox_nm'] for u in members if u in new] + [new_items[u]['bbox_nm'] for u in members
                                                                   if u in new_items]
        owned[group['id']] = dict(bbox_nm=_box(boxes), uuids=members | {group['uuid']} |
                                  {p['uuid'] for u in members if u in new for p in new[u]['pads']})
    return owned


# --------------------------------------------------------------------------
# Driver


def _sub_board(root, group):
    """Captured sub-board bytes and identity; a missing or changed file is unknown, never guessed."""
    root = Path(root)
    path = root / group['sub_board']
    record = dict(path=group['sub_board'], sha256=None, declared_sha256=group.get('sub_board_sha256'))
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            return record, SublayoutError('sub_board_missing', 'sub-board path crosses a symbolic link')
    if not path.is_file():
        return record, SublayoutError('sub_board_missing', f"sub-board {group['sub_board']} is not a captured input")
    record['sha256'] = digest(path)
    if record['declared_sha256'] and record['declared_sha256'] != record['sha256']:
        return record, SublayoutError('sub_board_changed', 'sub-board bytes differ from the declared sha256')
    return record, None


def _group_result(group, sub_board, plan=None, error=None):
    result = dict(id=group['id'], sub_board=sub_board, rotation_deg=group.get('rotation_deg', 0),
                  members=dict(sorted(group['members'].items())), anchor_at_nm=group['anchor_at_nm'])
    if error is not None:
        return dict(result, status='unknown', outcome='unknown',
                    findings=[dict(code=error.code, location=f"/groups/{group['id']}", message=str(error))])
    if plan['status'] != 'apply':
        return dict(result, status='refused', outcome='unknown', findings=plan['findings'])
    kinds = Counter(c['expected']['kind'] for c in plan['copy'])
    return dict(result, status='partial' if plan['withheld'] else 'populated', outcome=None, findings=[],
                anchor=plan['sub_anchor'], net_map=plan['net_map'], copied=dict(sorted(kinds.items())),
                withheld=plan['withheld'],
                # Copper the sub-board itself left without a net is copied unnetted and listed.
                unnetted_copper=sorted(c['source_uuid'] for c in plan['copy'] if c['expected']['copper']
                                       and not c['net'] and not c['expected'].get('rule_area')),
                sub_offsets_nm={fp['top_ref']: fp['sub_offset_nm'] for fp in plan['footprints']})


def reuse(top_board, request, sub_root, out, work):
    """Plan, apply in the sandbox, verify, run the DRC delta, and publish ``out`` when anything was pulled."""
    work = Path(work)
    work.mkdir(mode=0o700, parents=False, exist_ok=False)
    out = Path(out)
    result = dict(schema=RESULT_SCHEMA, execution='failed', outcome='unknown', findings=[], groups=[],
                  output=None, top_board=dict(sha256=digest(top_board)), request_sha256=identity(request))
    try:
        request = read_request(request)
        if out.exists() or out.is_symlink():
            raise SublayoutError('unsafe_path', 'output already exists', str(out))
        top = board_facts(top_board, work / 'top', result['top_board']['sha256'])
        plans, results = [], []
        for index, group in enumerate(request['groups']):
            sub_board, error = _sub_board(sub_root, group)
            plan = None
            if error is None:
                try:
                    sub = board_facts(Path(sub_root) / group['sub_board'], work / f'sub-{index}',
                                      sub_board['sha256'])
                except SublayoutError as exc:
                    error = SublayoutError('sub_board_unreadable', str(exc))
            if error is None:
                plan = plan_group(group, top, sub, sub_board['sha256'])
                if plan['status'] == 'apply':
                    plans.append(dict(plan, id=group['id'], sub_board=sub_board))
            results.append(_group_result(group, sub_board, plan, error))
        result['groups'] = results
        if plans:
            plan_record = dict(schema=PLAN_SCHEMA, top_board=result['top_board'], groups=plans)
            inputs = work / 'apply-input'
            (inputs / 'sub').mkdir(parents=True)
            shutil.copyfile(top_board, inputs / 'board.kicad_pcb')
            for suffix in ('.kicad_pro', '.kicad_prl', '.kicad_dru'):
                source = Path(top_board).with_suffix(suffix)
                if source.is_file():
                    shutil.copyfile(source, (inputs / 'board.kicad_pcb').with_suffix(suffix))
            for plan in plans:
                target = inputs / 'sub' / plan['sub_board']['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(sub_root) / plan['sub_board']['path'], target)
            (inputs / 'plan.json').write_bytes(canonical(plan_record))
            _child(inputs, work / 'apply', ['apply', '/candidate/board.kicad_pcb', '--plan', '/candidate/plan.json',
                                            '--sub-boards', '/candidate/sub', '-o', '/work/board.kicad_pcb'])
            saved = work / 'apply' / 'board.kicad_pcb'
            for suffix in ('.kicad_pro', '.kicad_prl', '.kicad_dru'):
                source = (inputs / 'board.kicad_pcb').with_suffix(suffix)
                if source.is_file():
                    shutil.copyfile(source, saved.with_suffix(suffix))
                else:
                    saved.with_suffix(suffix).unlink(missing_ok=True)
            # A fresh child reads the saved output; the writer's process is not trusted for it.
            owned = verify(plan_record, top, board_facts(saved, work / 'verify'))
            data = saved.read_bytes()
            if digest(work / 'verify' / 'facts-input' / 'board.kicad_pcb') != digest(saved):
                raise SublayoutError('verification', 'saved board changed after verification')
            _drc_groups(result, owned, top_board, saved, work)
            with out.open('xb') as stream:
                stream.write(data)
            result['output'] = dict(path=str(out), sha256=digest(out))
        if digest(top_board) != result['top_board']['sha256']:
            raise SublayoutError('stale_input', 'top board changed during reuse')
        outcomes = {g['outcome'] for g in result['groups']}
        result.update(execution='ok', outcome='defect' if 'defect' in outcomes else
                      'unknown' if 'unknown' in outcomes else 'pass')
    except SublayoutError as exc:
        result['findings'].append(dict(code=exc.code, location=exc.location, message=str(exc)))
    return _seal(result)


def _drc_groups(result, owned, before_board, after_board, work):
    try:
        before = native_drc(before_board, work / 'drc-before')
        after = native_drc(after_board, work / 'drc-after', project_board=before_board)
    except SublayoutError as exc:
        result['findings'].append(dict(code=exc.code, location='/drc', message=str(exc)))
        before = after = None
    if before is not None:
        result['drc'] = {k: {key: v[key] for key in ('report_sha256', 'kicad_version', 'violations',
                                                     'unconnected_items')} for k, v in (('before', before),
                                                                                        ('after', after))}
    for group in result['groups']:
        if group['id'] not in owned:
            continue
        group['bbox_nm'] = owned[group['id']]['bbox_nm']
        if before is None:
            group.update(drc='not_evaluated', outcome='unknown')
            continue
        new = drc_delta(before['report'], after['report'], group['bbox_nm'], owned[group['id']]['uuids'])
        from .design_rule_provenance import advisory_numeric
        advisory = [v for v in new if advisory_numeric(v)]
        hard = [v for v in new if not advisory_numeric(v)]
        grading = dict(new_violations=new)
        if advisory:
            grading['advisory_findings'] = advisory
        group.update(drc=grading, outcome='defect' if hard else
                     'unknown' if group['status'] == 'partial' else 'pass')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', help='top board .kicad_pcb')
    parser.add_argument('--request', required=True, help=f'{REQUEST_SCHEMA} JSON')
    parser.add_argument('--sub-boards', required=True, help='root of the captured sub-board files')
    parser.add_argument('--work', required=True, help='new private scratch directory')
    parser.add_argument('--out', required=True, help='new output .kicad_pcb')
    parser.add_argument('--record', required=True, help='new result JSON file')
    args = parser.parse_args(argv)
    try:
        request = json.loads(Path(args.request).read_text())
    except (OSError, ValueError) as exc:
        request = {'unreadable': str(exc)}
    record = reuse(args.board, request, args.sub_boards, args.out, args.work)
    with Path(args.record).open('xb') as stream:
        stream.write(canonical(record) + b'\n')
    return 0 if record['execution'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
