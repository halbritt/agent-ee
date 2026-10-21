"""Two-level placement, stage A (AGENTEE-339): blocks from the sealed hierarchy, packed alone.

A block is a top-level child sheet of the captured native netlist, or the explicit
grouping of a hashed engineer allocation file. Each footprint is in exactly one
block or the residual set; ambiguity refuses. A block is packed by the unchanged
placer on a synthetic plane around a fixed anchor, and the result is a rigid
``agent-ee.block-candidate/1``: relative quarter-turn poses, an independent pad
obligation grade, a courtyard witness, per-side outlines and source hashes.
Stage B (AGENTEE-340) arranges candidates; the block library (AGENTEE-342)
imports cited ones. A search-derived candidate is advisory, never a reference
layout. See docs/blocks.md.
"""
import argparse
import copy
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

from . import electrical_locality, placement_groups, placer
from .catalog import strict_json
from .footprint_library import canonical, digest
from .native_netlist import read_native_inventory, read_sheet_names
from .placement import courtyard_bounds, edges, intersection_area, qualified_polygons, validate_capture
from .placement_predicates import segment_distance2
from .topology_rules import quantity

ALLOCATION = 'agent-ee.block-allocation/1'
DERIVATION = 'agent-ee.block-derivation/1'
CANDIDATE = 'agent-ee.block-candidate/1'
FRAME = 'anchor-origin-quarter-turn/1'
# Layout authority classes, aligned with the AGENTEE-331 class/citation/enforcement record.
# Only a cited manufacturer or qualified owner layout may be a hard reference.
PROVENANCE = {'manufacturer-reference-layout': 'hard', 'manufacturer-evm': 'hard',
              'qualified-owner-board': 'hard', 'search-derived': 'advisory'}
SEARCH_CITATION = ('agent-ee.blocks intra-block search over the captured board; '
                   'no manufacturer or owner layout authority')
# Pad facts that identify an instance or its board connection, not its land geometry.
INSTANCE_PAD_KEYS = frozenset(('uuid', 'net', 'copper_centroid_reason'))


def _natural(text):
    head = text.rstrip('0123456789')
    tail = text[len(head):]
    return (head, int(tail) if tail else -1, text)


def _number(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_number(v) for v in value]
    if isinstance(value, dict):
        return {k: _number(v) for k, v in value.items()}
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def _texts(value, label):
    if not isinstance(value, list) or len(value) != len(set(map(str, value))):
        raise ValueError(f'{label} must be a list of unique text')
    return [_text(v, label) for v in value]


def _top_sheet(path):
    parts = path.strip('/').split('/')
    return '/' if not parts[0] else '/' + parts[0] + '/'


def _netted(fp):
    return any(p.get('net') for p in fp['pads'])


def _allocation(raw):
    document = strict_json(raw)
    if not isinstance(document, dict) or document.get('schema') != ALLOCATION or set(document) - {
            'schema', 'blocks', 'residual'} or not isinstance(document.get('blocks'), list) or not document['blocks']:
        raise ValueError(f'expected {ALLOCATION} with nonempty blocks and optional residual')
    for block in document['blocks']:
        if not isinstance(block, dict) or 'id' not in block or set(block) - {'id', 'sheets', 'references', 'anchor'}:
            raise ValueError('allocated block needs id and sheets and/or references, optional anchor')
        _text(block['id'], 'block id')
        if not _texts(block.get('sheets', []), 'block sheets') + _texts(block.get('references', []), 'block references'):
            raise ValueError(f"block {block['id']}: needs sheets or references")
        if 'anchor' in block:
            _text(block['anchor'], 'block anchor')
    _texts(document.get('residual', []), 'residual references')
    return document


def derive(inventory, sheet_names, capture, *, inventory_sha256, allocation_raw=None):
    """Blocks and residual set; every board footprint is in exactly one. Raises ValueError."""
    validate_capture(capture)
    board = {fp['ref']: fp for fp in capture['footprints']}
    parts = {ref: c for ref, c in inventory['components'].items() if c['on_board']}
    for ref, component in sorted(parts.items()):
        if ref not in board:
            raise ValueError(f'{ref}: on-board component has no board footprint')
        if component['footprint'] != board[ref].get('fpid'):
            raise ValueError(f'{ref}: board footprint differs from the native netlist')
    residual = {}
    for ref, fp in board.items():
        if ref not in parts:
            if ref in inventory['components'] or _netted(fp):
                raise ValueError(f'{ref}: board footprint is not an on-board native component')
            residual[ref] = 'board-only'
    top = {ref: _top_sheet(c['sheet']) for ref, c in parts.items()}
    claims = {}
    if allocation_raw is None:
        blocks = []
        for sheet in sorted({s for s in top.values() if s != '/'},
                            key=lambda s: _natural(sheet_names.get(s, s).strip('/'))):
            name = sheet_names.get(sheet, sheet).strip('/')
            blocks.append({'id': name, 'origin': 'sheet', 'sheets': [sheet],
                           'references': sorted((r for r, s in top.items() if s == sheet), key=_natural)})
    else:
        document = _allocation(allocation_raw)
        by_name = {name: path for path, name in sheet_names.items()}
        blocks = []
        def claim(ref, owner):
            if ref in claims:
                raise ValueError(f'{ref}: allocated to both {claims[ref]} and {owner}')
            claims[ref] = owner
        for entry in document['blocks']:
            bid = entry['id']
            if any(b['id'] == bid for b in blocks):
                raise ValueError(f'duplicate block id {bid}')
            members, sheets = set(), []
            for sheet in entry.get('sheets', []):
                path = by_name.get(sheet, sheet)
                if path == '/' or path not in sheet_names:
                    raise ValueError(f'block {bid}: unknown child sheet {sheet}')
                inside = {r for r, c in parts.items() if c['sheet'].startswith(path)}
                if not inside:
                    raise ValueError(f'block {bid}: sheet {sheet} has no on-board footprints')
                members |= inside
                sheets.append(path)
            for ref in entry.get('references', []):
                if ref not in parts:
                    raise ValueError(f'block {bid}: {ref} is not an on-board native component')
                if ref in members:
                    raise ValueError(f'block {bid}: {ref} is allocated twice')
                members.add(ref)
            for ref in members:
                claim(ref, 'block ' + bid)
            if 'anchor' in entry and entry['anchor'] not in members:
                raise ValueError(f'block {bid}: anchor must be a member')
            blocks.append({'id': bid, 'origin': 'allocation', 'sheets': sorted(sheets),
                           'references': sorted(members, key=_natural),
                           **({'anchor': entry['anchor']} if 'anchor' in entry else {})})
        for ref in document.get('residual', []):
            if ref not in board:
                raise ValueError(f'residual {ref} is not a board footprint')
            claim(ref, 'residual')
            residual.setdefault(ref, 'allocated-residual')
        loose = sorted((r for r, s in top.items() if s != '/' and r not in claims), key=_natural)
        if loose:
            raise ValueError('child-sheet components neither allocated to a block nor declared residual: '
                             + ', '.join(loose))
    for ref, sheet in top.items():
        if sheet == '/' and ref not in claims:
            residual[ref] = 'root-sheet'
    locked = sorted((r for b in blocks for r in b['references'] if board[r].get('locked')), key=_natural)
    if locked:
        raise ValueError('locked footprints cannot be rigid-block members; allocate them to the residual set: '
                         + ', '.join(locked))
    return {'schema': DERIVATION, 'board_sha256': capture['board_sha256'], 'inventory_sha256': inventory_sha256,
            'allocation_sha256': None if allocation_raw is None else hashlib.sha256(allocation_raw).hexdigest(),
            'blocks': blocks,
            'residual': [{'reference': r, 'reason': residual[r]} for r in sorted(residual, key=_natural)]}


def _local(fp):
    """Footprint-local geometry at the origin, rotation 0, on its captured side."""
    local = placer.transform(fp, [0, 0], 0)
    pads = sorted((canonical(_number({k: v for k, v in p.items() if k not in INSTANCE_PAD_KEYS}))
                   for p in local['pads']))
    courtyards = {side: {k: c[k] for k in ('status', 'polygons', 'inner_polygons', 'approximation') if k in c}
                  for side, c in sorted(local['courtyards'].items())}
    return {'side': fp['side'], 'pads': [json.loads(p) for p in pads]}, {'side': fp['side'], 'courtyards': courtyards}


def pad_geometry_sha256(fp):
    """Hash of the land pattern: every pad's local geometry, without UUIDs or nets."""
    return digest(_local(fp)[0])


def courtyard_sha256(fp):
    return digest(_local(fp)[1])


def _pad_nets(fp):
    nets = {}
    for pad in fp['pads']:
        if pad['number']:
            nets.setdefault(pad['number'], set()).add(pad.get('net', ''))
    ambiguous = sorted(n for n, s in nets.items() if len(s) != 1)
    if ambiguous:
        raise ValueError(f"{fp['ref']}: pads {', '.join(ambiguous)} carry several nets")
    return {n: s.pop() for n, s in nets.items()}


def net_roles(capture, inventory, refs):
    """Per member pin: its function and a local net role; net names are not identities.

    A role is shared by exactly the member pins on one net. ``boundary`` marks a net
    that also reaches a footprint outside ``refs``. Unconnected pins have no role.
    """
    refs = list(refs)
    board = {fp['ref']: fp for fp in capture['footprints']}
    nets = {ref: _pad_nets(board[ref]) for ref in refs}
    outside = {p.get('net') for fp in capture['footprints'] if fp['ref'] not in refs for p in fp['pads']}
    labels, roles = {}, {}
    for ref in refs:
        for number in sorted(nets[ref], key=_natural):
            net = nets[ref][number]
            if net and net not in labels:
                labels[net] = {'role': f'n{len(labels) + 1}', 'boundary': net in outside}
    for ref in refs:
        metadata = inventory['components'][ref]['pin_metadata']
        roles[ref] = [{'pad': number, 'function': metadata.get(number),
                       **({'role': labels[nets[ref][number]]['role'],
                           'boundary': labels[nets[ref][number]]['boundary'],
                           'net': nets[ref][number]} if nets[ref][number] else {'role': None})}
                      for number in sorted(nets[ref], key=_natural)]
    return roles


def _filter_policy(policy, block):
    """The block's own obligations; board-placed and boundary constraints are deferred to stage B."""
    members = set(block['references'])
    fixed = sorted(members & set(policy.get('fixed_refs', [])) |
                   {o.get('reference') for o in policy.get('fixed_overhangs', [])} & members, key=_natural)
    if fixed:
        raise ValueError(f"block {block['id']}: fixed references cannot be rigid-block members: {', '.join(fixed)}")
    sub = {'schema': 'agent-ee.packing-policy/1', 'step_budget': policy['step_budget'], 'gap_nm': policy['gap_nm'],
           'priority_refs': [r for r in policy['priority_refs'] if r in members],
           'ground_nets': list(policy['ground_nets']), 'obstacles': []}
    if 'search_timeout_s' in policy:
        sub['search_timeout_s'] = policy['search_timeout_s']
    deferred = {'groups': [], 'obligations': [], 'obstacles': len(policy['obstacles']),
                'board_rule_areas': 0}
    groups = []
    for group in policy.get('groups', []):
        refs = set(group['references']) | {group.get('anchor', {}).get('reference')} - {None}
        if not refs & members:
            continue
        if refs <= members and 'reference' in group.get('anchor', {}) and 'region' not in group:
            groups.append(copy.deepcopy(group))
        else:
            deferred['groups'].append(group['id'])
    if groups:
        sub['groups'] = groups
    document = policy.get('electrical_locality')
    if document:
        internal = []
        for obligation in document['obligations']:
            refs = {obligation[r][k]['reference'] for r in electrical_locality.roles(obligation) for k in ('from', 'to')}
            if refs <= members:
                internal.append(copy.deepcopy(obligation))
            elif refs & members:
                deferred['obligations'].append(obligation['id'])
        if internal:
            sub['electrical_locality'] = {'schema': electrical_locality.SCHEMA, 'obligations': internal}
    return sub, deferred


def _extent(fp):
    """Largest coordinate offset of courtyards and pad reservations from the footprint origin."""
    ox, oy = fp['position_nm']
    polygons = []
    for courtyard in fp['courtyards'].values():
        try:
            polygons += courtyard_bounds(courtyard)[0]
        except ValueError:
            continue  # The placer refuses an unqualified courtyard by name, as typed unknown.
    geometry = fp.get('packing_pad_geometry') or {}
    if isinstance(geometry, dict):
        polygons += [p for kind in ('copper', 'holes') for e in geometry.get(kind, [])
                     if isinstance(e, dict) for p in e.get('polygons') or []]
    return max((max(abs(x - ox), abs(y - oy)) for poly in polygons for x, y in poly), default=0)


def _separation2(left, right):
    if any(intersection_area(p, q) > 0 for p in left for q in right):
        return Fraction(0)
    return min(segment_distance2(a, b, c, d) for p in left for q in right for a, b in edges(p) for c, d in edges(q))


def _witness(placed, gap_nm):
    """Exact same-side courtyard separations and drilled-pad reservations of the framed members."""
    pairs, holes = [], []
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            for side in sorted(set(a['courtyards']) & set(b['courtyards'])):
                left = qualified_polygons(a['courtyards'][side])
                right = qualified_polygons(b['courtyards'][side])
                pairs.append({'refs': [a['ref'], b['ref']], 'side': side, 'squared': _separation2(left, right)})
            if ('_packing_targets' in a and '_packing_targets' in b) and placer._hole_conflict(a, b):
                holes.append([a['ref'], b['ref']])
    short = [p for p in pairs if p['squared'] < gap_nm ** 2]
    minimum = {}
    for p in pairs:
        if p['side'] not in minimum or p['squared'] < minimum[p['side']]['squared']:
            minimum[p['side']] = p
    def encode(p):
        s = p['squared']
        return {'refs': p['refs'], 'distance_squared_nm2': [s.numerator, s.denominator]}
    return {'method': 'exact-courtyard-separation/1', 'gap_nm': gap_nm, 'pairs_checked': len(pairs),
            'status': 'pass' if not short and not holes else 'defect',
            'minimum': {side: encode(p) for side, p in sorted(minimum.items())},
            'below_gap': [dict(encode(p), side=p['side']) for p in short], 'drilled_pad_conflicts': holes}


def connected_order(board, members, anchor, declared, ground_nets):
    """Declared priorities, then breadth-first over shared non-ground nets from the anchor.

    The placer's wire cost ignores ground nets and unplaced peers, so a member queued
    before every same-net neighbour would rank only by position. Unreachable members
    keep the placer's own order after the reachable ones.
    """
    nets = {r: set(_pad_nets(board[r]).values()) - {''} - set(ground_nets) for r in members}
    order, seen, frontier = list(declared), set(declared) | {anchor}, [anchor]
    while frontier:
        reached = sorted((r for r in members if r not in seen and any(nets[r] & nets[f] for f in frontier)),
                         key=lambda r: (-sum(1 for p in board[r]['pads'] if p['number']), _natural(r)))
        order += reached
        seen |= set(reached)
        frontier = reached
    return order


def anchor_of(block, board):
    """Declared anchor, else the member with the most numbered pads (ties: natural order)."""
    if 'anchor' in block:
        return block['anchor']
    return sorted(block['references'],
                  key=lambda r: (-sum(1 for p in board[r]['pads'] if p['number']), _natural(r)))[0]


def pack_block(capture, inventory, derivation, block_id, policy, *, heartbeat=None):
    """Pack one block in isolation. Returns {'status': 'packed', 'candidate'} or the typed failure."""
    blocks = [b for b in derivation['blocks'] if b['id'] == block_id]
    if len(blocks) != 1:
        raise ValueError(f'unknown block {block_id}')
    block = blocks[0]
    if derivation['board_sha256'] != capture['board_sha256']:
        raise ValueError('derivation belongs to another board capture')
    placer._validate_policy(policy, {fp['ref'] for fp in capture['footprints']})
    board = {fp['ref']: fp for fp in capture['footprints']}
    members = sorted(block['references'], key=_natural)
    anchor = anchor_of(block, board)
    sub, deferred = _filter_policy(policy, block)
    sub['fixed_refs'] = [anchor]
    sub['priority_refs'] = connected_order(board, members, anchor, sub['priority_refs'], sub['ground_nets'])
    uuids = {board[r]['uuid'] for r in members}
    areas = [a for a in capture.get('rule_areas', []) if isinstance(a, dict) and a.get('owner') in uuids]
    deferred['board_rule_areas'] = sum(1 for a in capture.get('rule_areas', [])
                                       if isinstance(a, dict) and a.get('owner') == 'board')
    # A synthetic square that any chain of the members fits inside, centred on the
    # fixed anchor: the plane is unbounded for the search unless a member reaches it.
    reach = _extent(board[anchor]) + sum(2 * _extent(board[r]) + sub['gap_nm'] for r in members if r != anchor)
    reach += sub['gap_nm'] + 1
    ax, ay = board[anchor]['position_nm']
    square = [[ax - reach, ay - reach], [ax + reach, ay - reach], [ax + reach, ay + reach], [ax - reach, ay + reach]]
    isolated = {'schema': capture['schema'], 'board_sha256': capture['board_sha256'],
                'pcbnew_version': capture.get('pcbnew_version'),
                'outline': {'status': 'proven', 'polygons': [square]},
                'footprints': [copy.deepcopy(board[r]) for r in members], 'rule_areas': copy.deepcopy(areas)}
    plan = placer.pack(isolated, sub, heartbeat=heartbeat)
    base = {'schema': CANDIDATE, 'block_id': block_id, 'block_policy': sub, 'deferred': deferred}
    if plan['status'] != 'planned':
        return {**base, 'status': plan['status'], 'outcome': plan['outcome'], 'reason': plan.get('reason'),
                'plan': plan}
    poses = {p['ref']: p for p in plan['placements']}
    touching = sorted((r for r in members for box in placer.boxes(
        placer.transform(board[r], poses[r]['position_nm'], poses[r]['rotation_deg'])).values()
        if box[0] <= ax - reach or box[1] <= ay - reach or box[2] >= ax + reach or box[3] >= ay + reach),
        key=_natural)
    if touching:
        return {**base, 'status': 'synthetic_plane_reached', 'outcome': 'unknown',
                'reason': 'members reached the synthetic plane edge: ' + ', '.join(touching), 'plan': plan}
    result = candidate(capture, inventory, derivation, block, anchor, poses, policy, sub, deferred, plan)
    return {'status': result['status'], 'outcome': 'info' if result['status'] == 'packed' else 'unknown',
            'candidate': result}


def _frame_pose(pose, origin, turns):
    x, y = placer.rotate([pose['position_nm'][0] - origin[0], pose['position_nm'][1] - origin[1]], -turns)
    return [x, y], int(pose['rotation_deg'] - turns * 90) % 360


def candidate(capture, inventory, derivation, block, anchor, poses, policy, sub, deferred, plan):
    board = {fp['ref']: fp for fp in capture['footprints']}
    members = sorted(block['references'], key=_natural)
    origin, turns = poses[anchor]['position_nm'], int(poses[anchor]['rotation_deg']) // 90
    framed, rows = [], []
    roles = net_roles(capture, inventory, [anchor] + [r for r in members if r != anchor])
    for ref in members:
        position, rotation = _frame_pose(poses[ref], origin, turns)
        fp = placer.transform(board[ref], position, rotation)
        framed.append(fp)
        component = inventory['components'][ref]
        rows.append({'reference': ref, 'fpid': board[ref]['fpid'], 'side': board[ref]['side'],
                     'value': {'text': component['value'], 'rule': 'exact'},
                     'pose': {'position_nm': position, 'rotation_deg': rotation},
                     'pad_geometry_sha256': pad_geometry_sha256(board[ref]),
                     'courtyard_sha256': courtyard_sha256(board[ref]), 'pins': roles[ref]})
    outline = {}
    for fp in framed:
        for side, courtyard in fp['courtyards'].items():
            outline.setdefault(side, []).extend(courtyard_bounds(courtyard)[0])
    outline = {side: {'box_nm': list(placer.boxes({'courtyards': {side: {'polygons': polys}}})[side]),
                      'courtyards': [[list(p) for p in poly] for poly in polys]}
               for side, polys in sorted(outline.items())}
    framed_capture = {**capture, 'footprints': framed}
    grade = {'electrical_locality': (electrical_locality.grade(framed_capture, sub['electrical_locality'])
                                     if 'electrical_locality' in sub else None),
             'groups': placement_groups.grade(framed_capture, sub['groups']) if 'groups' in sub else None}
    witness = _witness(framed, sub['gap_nm'])
    status = ('refused' if witness['status'] != 'pass' or any(g and g['status'] != 'pass' for g in grade.values())
              else 'packed')
    return {'schema': CANDIDATE, 'block_id': block['id'], 'status': status,
            'provenance': {'class': 'search-derived', 'enforcement': PROVENANCE['search-derived'],
                           'citation': SEARCH_CITATION, 'method': plan['search']['strategy']},
            'frame': {'convention': FRAME, 'anchor': anchor},
            'members': rows, 'outline': outline, 'grade': grade, 'witness': witness,
            'placer_judgment': {'verdict': plan['judgment']['verdict'], 'basis': plan['judgment']['basis']},
            'deferred': deferred,
            'search': {k: plan['search'][k] for k in ('strategy', 'backjumps') if k in plan['search']}
                      | {'steps_used': plan['steps_used'], 'step_budget': plan['step_budget']},
            'block_policy': sub,
            'sources': {'board_sha256': capture['board_sha256'], 'inventory_sha256': derivation['inventory_sha256'],
                        'allocation_sha256': derivation['allocation_sha256'], 'derivation_sha256': digest(derivation),
                        'policy_sha256': digest(policy)}}


def _hex(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'{label} must be a lowercase SHA-256')


def validate_candidate(document):
    """Structural contract for stage B and the block library; raises ValueError."""
    if not isinstance(document, dict) or document.get('schema') != CANDIDATE:
        raise ValueError(f'expected {CANDIDATE}')
    required = {'schema', 'block_id', 'status', 'provenance', 'frame', 'members', 'outline', 'sources'}
    if not required <= document.keys():
        raise ValueError('block candidate needs ' + ', '.join(sorted(required - document.keys())))
    _text(document['block_id'], 'block id')
    if document['status'] != 'packed':
        raise ValueError('only a packed block candidate is usable')
    provenance = document['provenance']
    if (not isinstance(provenance, dict) or provenance.get('class') not in PROVENANCE
            or provenance.get('enforcement') != PROVENANCE[provenance['class']]):
        raise ValueError('block provenance needs a known class and its enforcement')
    _text(provenance.get('citation'), 'block provenance citation')
    if provenance['class'] != 'search-derived':
        source = provenance.get('source')
        if not isinstance(source, dict) or set(source) != {'locator', 'sha256'}:
            raise ValueError('a reference layout needs its captured source locator and sha256')
        _text(source['locator'], 'source locator')
        _hex(source['sha256'], 'source sha256')
    if document['frame'] != {'convention': FRAME, 'anchor': document['frame'].get('anchor')}:
        raise ValueError(f'block frame must be {FRAME} with an anchor')
    rows = document['members']
    refs = [r.get('reference') for r in rows] if isinstance(rows, list) else []
    if not refs or len(refs) != len(set(refs)) or document['frame']['anchor'] not in refs:
        raise ValueError('block members need unique references including the anchor')
    for row in rows:
        _text(row['reference'], 'member reference')
        _text(row.get('fpid'), 'member fpid')
        if row.get('side') not in ('F', 'B'):
            raise ValueError('member side must be F or B')
        for key in ('pad_geometry_sha256', 'courtyard_sha256'):
            _hex(row.get(key), key)
        pose = row.get('pose')
        if (not isinstance(pose, dict) or set(pose) != {'position_nm', 'rotation_deg'}
                or not isinstance(pose['position_nm'], list) or len(pose['position_nm']) != 2
                or any(type(v) is not int for v in pose['position_nm'])
                or type(pose['rotation_deg']) is not int or pose['rotation_deg'] not in (0, 90, 180, 270)):
            raise ValueError('member pose needs integer nanometres and a quarter-turn rotation')
        if row['reference'] == document['frame']['anchor'] and pose != {'position_nm': [0, 0], 'rotation_deg': 0}:
            raise ValueError('the anchor defines the block frame at the origin, rotation 0')
        _value_rule(row.get('value'))
        if not isinstance(row.get('pins'), list):
            raise ValueError('member pins must list functions and net roles')
    if not isinstance(document['outline'], dict) or not document['outline'] or set(document['outline']) - {'F', 'B'}:
        raise ValueError('block outline must be given per side')
    for side in document['outline'].values():
        qualified_polygons({'status': 'proven', 'polygons': side['courtyards']})
    sources = document['sources']
    _hex(sources.get('board_sha256'), 'board sha256')
    return document


def _value_rule(value):
    if not isinstance(value, dict) or value.get('rule') not in ('exact', 'tolerance'):
        raise ValueError('member value needs rule exact or tolerance')
    if not isinstance(value.get('text'), str):
        raise ValueError('member value text required')
    if value['rule'] == 'exact':
        if set(value) != {'text', 'rule'}:
            raise ValueError('exact value takes only text')
        return
    tolerance = value.get('relative_tolerance')
    if (set(value) != {'text', 'rule', 'relative_tolerance', 'citation'} or quantity(value['text']) is None
            or not isinstance(tolerance, list) or len(tolerance) != 2 or any(type(v) is not int for v in tolerance)
            or tolerance[1] <= 0 or tolerance[0] < 0):
        raise ValueError('tolerance value needs a written quantity, [numerator, denominator] and a citation')
    _text(value['citation'], 'value tolerance citation')


def _value_matches(rule, text):
    if rule['rule'] == 'exact':
        return text == rule['text']
    nominal, actual = quantity(rule['text']), quantity(text)
    if actual is None or nominal[1] != actual[1]:
        return False
    tolerance = Fraction(*rule['relative_tolerance'])
    return abs(Fraction(actual[0]) - Fraction(nominal[0])) <= tolerance * abs(Fraction(nominal[0]))


def match(document, capture, inventory, mapping=None):
    """Whether a target instance reproduces a candidate: footprint, lands, pin roles, value.

    ``mapping`` maps each candidate reference to one target reference (identity by
    default). Every mismatch is listed; any mismatch refuses reuse.
    """
    validate_candidate(document)
    rows = {r['reference']: r for r in document['members']}
    mapping = {r: r for r in rows} if mapping is None else mapping
    if (set(mapping) != set(rows) or len(set(mapping.values())) != len(mapping)):
        raise ValueError('mapping must pair every candidate member with a distinct target reference')
    board = {fp['ref']: fp for fp in capture['footprints']}
    problems = []
    for ref, target in sorted(mapping.items()):
        if target not in board or target not in inventory['components']:
            problems.append({'reference': ref, 'target': target, 'kind': 'target_missing'})
    if problems:
        return {'status': 'refused', 'mismatches': problems}
    anchor = document['frame']['anchor']
    order = [anchor] + [r for r in sorted(rows, key=_natural) if r != anchor]
    found = net_roles(capture, inventory, [mapping[r] for r in order])
    pairs = {}
    for ref in order:
        row, target = rows[ref], mapping[ref]
        fp, component = board[target], inventory['components'][target]
        def mismatch(kind, expected, actual):
            problems.append({'reference': ref, 'target': target, 'kind': kind, 'expected': expected, 'actual': actual})
        if fp.get('fpid') != row['fpid']:
            mismatch('fpid', row['fpid'], fp.get('fpid'))
        if fp['side'] != row['side']:
            mismatch('side', row['side'], fp['side'])
        for key, compute in (('pad_geometry_sha256', pad_geometry_sha256), ('courtyard_sha256', courtyard_sha256)):
            if compute(fp) != row[key]:
                mismatch(key.removesuffix('_sha256'), row[key], compute(fp))
        if not _value_matches(row['value'], component['value']):
            mismatch('value', row['value'], component['value'])
        have = {p['pad']: p for p in found[target]}
        want = {p['pad']: p for p in row['pins']}
        if set(have) != set(want):
            mismatch('pins', sorted(want, key=_natural), sorted(have, key=_natural))
        for pad in sorted(set(have) & set(want), key=_natural):
            if have[pad]['function'] != want[pad]['function']:
                mismatch('pin_function', {pad: want[pad]['function']}, {pad: have[pad]['function']})
            role = (want[pad]['role'], want[pad].get('boundary')), (have[pad]['role'], have[pad].get('boundary'))
            pairs.setdefault(role[0], set()).add(role[1])
            if (role[0][0] is None) != (role[1][0] is None) or role[0][1] != role[1][1]:
                mismatch('net_role', {pad: role[0]}, {pad: role[1]})
    # The candidate's net partition must map one-to-one onto the target's.
    images = {}
    for expected, actual in pairs.items():
        if expected[0] is None:
            continue
        if len(actual) != 1:
            problems.append({'kind': 'net_role_split', 'role': expected[0], 'actual': sorted(map(str, actual))})
        for a in actual:
            images.setdefault(a, set()).add(expected)
    for actual, expected in sorted(images.items(), key=str):
        if len(expected) != 1 and actual[0] is not None:
            problems.append({'kind': 'net_role_merged', 'roles': sorted(e[0] for e in expected), 'actual': actual[0]})
    return {'status': 'refused' if problems else 'match', 'mismatches': problems}


def _read(path):
    data = Path(path).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _load(args):
    raw, sha = _read(args.netlist)
    capture = strict_json(Path(args.probe).read_bytes())
    allocation = Path(args.allocation).read_bytes() if args.allocation else None
    inventory = read_native_inventory(args.netlist)
    derivation = derive(inventory, read_sheet_names(args.netlist), capture,
                        inventory_sha256=sha, allocation_raw=allocation)
    return capture, inventory, derivation


def _write(path, value):
    path = Path(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=1, sort_keys=True, default=lambda v: [v.numerator, v.denominator])
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('derive', 'pack', 'match'):
        command = sub.add_parser(name)
        command.add_argument('--probe', required=True, type=Path, help='native placement-probe.json')
        command.add_argument('--netlist', required=True, type=Path, help='captured native XML export')
        command.add_argument('--allocation', type=Path, help=f'{ALLOCATION} JSON')
        command.add_argument('--out', required=True, type=Path, help='new output JSON file')
        if name == 'pack':
            command.add_argument('--policy', required=True, type=Path, help='agent-ee.packing-policy/1')
            command.add_argument('--block', required=True)
        if name == 'match':
            command.add_argument('--candidate', required=True, type=Path)
            command.add_argument('--mapping', type=Path, help='JSON object: candidate reference -> target reference')
    args = parser.parse_args(argv)
    try:
        capture, inventory, derivation = _load(args)
        if args.command == 'derive':
            result, ok = derivation, True
        elif args.command == 'pack':
            policy = strict_json(args.policy.read_bytes())
            result = pack_block(capture, inventory, derivation, args.block, policy,
                                heartbeat=lambda event: print(json.dumps(event), file=sys.stderr, flush=True))
            result = result.get('candidate', result)
            ok = result['status'] == 'packed'
        else:
            mapping = strict_json(args.mapping.read_bytes()) if args.mapping else None
            result = match(strict_json(args.candidate.read_bytes()), capture, inventory, mapping)
            ok = result['status'] == 'match'
        _write(args.out, result)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        return 1
    print(json.dumps({'status': result.get('status', 'derived'), 'out': str(args.out)}))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
