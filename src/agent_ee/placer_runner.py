"""Single-writer disposable-board placement; intended for networkless sandbox use."""
import argparse
from collections import Counter
from decimal import Decimal
import functools
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from . import placement_groups, placement_trace
from .board_net_names import from_xml
from .locking import writer_lock
from .placement import judge
from .placement_probe import extract, custom_pad_geometry, primitive, rule_area
from .placer import _failure, pack, transform
from .silkscreen import reserve_imu_markings
from .silk_artwork import SILK_CONFLICTS, relocate
from .storage import digest, read_json, write_json
from .models3d import copy_project_models, verify_project_models
from .design_rule_provenance import (
    classify_finding, finding_sources, measured_actual, numeric_finding, require_evaluated_limits)


DRC_COLLISIONS = frozenset(('shorting_items', 'clearance', 'pth_inside_courtyard', 'holes_co_located'))
DRC_MASK = 'solder_mask_bridge'
DRC_KEEPOUT = 'items_not_allowed'
DRC_COURTYARDS = frozenset(('courtyards_overlap', 'malformed_courtyard', 'missing_courtyard'))


def _authorization(source, destination, authorization):
    if authorization.get('schema') != 'agent-ee.placement-write/1' or authorization.get('single_design_writer') is not True:
        raise ValueError('explicit single-design-writer authorization required')
    root = Path(authorization['writable_root'])
    if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
        raise ValueError('writable root must be an existing resolved directory')
    # A disposable writer has no authority over its read-only source tree.
    if source.is_relative_to(root) or destination.parent != root or destination.name.startswith('.'):
        raise ValueError('destination must be a fresh direct child of isolated writable root')
    if destination.exists() or destination.is_symlink():
        raise ValueError('refusing existing candidate destination')
    if digest(source) != authorization.get('source_sha256'):
        raise ValueError('source board differs from authorized hash')
    return root


def _netlist_binding(netlist, facts, board_only=frozenset()):
    """`board_only` names native board-only footprints, such as scaffold mounting holes.

    They have no schematic symbol, so they are absent from the export; one with a net,
    or one that the export also names, is refused."""
    root = ET.fromstring(netlist)
    if root.tag != 'export' or root.find('components') is None or root.find('nets') is None:
        raise ValueError('native XML export components/nets required')
    refs = [c.get('ref') for c in root.findall('components/comp')]
    if board_only & set(refs) or any(pad['net'] for f in facts['footprints'] if f['ref'] in board_only
                                     for pad in f['pads']):
        raise ValueError('board-only footprint has a net or a native netlist component')
    if len(set(refs)) != len(refs) or set(refs) != {f['ref'] for f in facts['footprints']} - board_only:
        raise ValueError('native netlist component inventory differs from saved board')
    raw = [net.get('name') for net in root.findall('nets/net')]
    if not all(raw) or len(set(raw)) != len(raw):
        raise ValueError('ambiguous native net identity')
    # Saved pads carry the board form of each XML name (AGENTEE-220).
    board_names = from_xml(root)
    assignments = {}
    for net in root.findall('nets/net'):
        name = board_names[net.get('name')]
        for node in net.findall('node'):
            identity = node.get('ref'), node.get('pin')
            if identity in assignments or identity[0] not in refs or not identity[1]:
                raise ValueError('ambiguous native terminal')
            assignments[identity] = name
    physical = {}
    for fp in facts['footprints']:
        for pad in fp['pads']:
            identity = fp['ref'], pad['number']
            if identity in physical and physical[identity] != pad['net']:
                raise ValueError('conflicting duplicate native pad number')
            physical[identity] = pad['net']
    if any(physical.get(pin) != net for pin, net in assignments.items()) or any(
            net and assignments.get(pin) != net for pin, net in physical.items()):
        raise ValueError('native netlist terminal nets differ from saved board')


def _unrouted_board(source):
    import pcbnew
    board = pcbnew.LoadBoard(str(source))
    # Board rule areas are inputs: the packer reads footprint keepouts as obstacles.
    if len(board.GetTracks()) or any(not zone.GetIsRuleArea() for zone in board.Zones()):
        raise ValueError('initial placer refuses existing tracks, vias or zones')
    for fp in board.GetFootprints():
        if any(not zone.GetIsRuleArea() for zone in fp.Zones()):
            raise ValueError('initial placer refuses footprint-owned copper zones')
        if any(s.GetLayer() == pcbnew.Edge_Cuts for s in list(fp.GraphicalItems()) + list(fp.GetFields())):
            raise ValueError('initial placer refuses footprint-owned board edges')
    return board


def _padstack_refusal(board):
    # The probe records one shape per pad; another padstack mode could hide an
    # unqualified shape on some copper layer.
    import pcbnew
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
                return f'{fp.GetReference()} pad {pad.GetNumber()!r}: per-layer padstack not qualified for packing'
    return None


def _pad_inventory(board):
    return {fp.m_Uuid.AsString(): _footprint_pad_inventory(fp) for fp in board.GetFootprints()}


def _footprint_pad_inventory(fp):
    """Footprint-local pad facts that a footprint move must leave unchanged.

    A quarter turn can report the unchanged relative angle as 360 degrees.
    """
    import pcbnew
    every = pcbnew.PADSTACK.ALL_LAYERS
    def xy(v):
        return [int(v.x), int(v.y)]
    return {
        'fpid': fp.GetFPIDAsString(), 'pads': sorted([
            [pad.m_Uuid.AsString(), pad.GetNumber(), pad.GetNetname(), int(pad.GetAttribute()),
             int(pad.Padstack().Mode()), int(pad.GetShape(every)), xy(pad.GetSize(every)),
             xy(pad.GetOffset(every)), xy(pad.GetDrillSize()), int(pad.GetDrillShape()),
             pad.GetRoundRectRadiusRatio(every), pad.GetChamferRectRatio(every),
             int(pad.GetChamferPositions(every)), pad.GetLayerSet().FmtHex(),
             xy(pad.GetFPRelativePosition()), round(pad.GetFPRelativeOrientation().AsDegrees(), 6) % 360,
             custom_pad_geometry(pad) if pad.GetShape(every) == pcbnew.PAD_SHAPE_CUSTOM else None]
            for pad in fp.Pads()])}


def _native_drc(board, scratch):
    report_path = scratch / 'native-drc.json'
    command = ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
               '--output', str(report_path), str(board)]
    execution = {'argv': command, 'execution': 'not_run', 'exit_code': None}
    write_json(scratch / 'native-drc-execution.json', execution)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        execution.update(execution='failed', error=str(exc))
        write_json(scratch / 'native-drc-execution.json', execution)
        if isinstance(exc, subprocess.TimeoutExpired):
            (scratch / 'native-drc.stdout.txt').write_bytes(exc.stdout or b'')
            (scratch / 'native-drc.stderr.txt').write_bytes(exc.stderr or b'')
        raise
    (scratch / 'native-drc.stdout.txt').write_text(completed.stdout)
    (scratch / 'native-drc.stderr.txt').write_text(completed.stderr)
    execution.update(execution='ok' if completed.returncode == 0 else 'failed', exit_code=completed.returncode)
    write_json(scratch / 'native-drc-execution.json', execution)
    if completed.returncode != 0:
        raise ValueError(f'native DRC failed (exit {completed.returncode})')
    report = read_json(report_path)
    if (not isinstance(report.get('violations'), list) or not isinstance(report.get('unconnected_items'), list)
            or not isinstance(report.get('ignored_checks'), list)):
        raise ValueError('native DRC report lacks counted evaluations')
    if (not isinstance(report.get('included_severities'), list) or
            any(not isinstance(s, str) for s in report['included_severities']) or
            set(report['included_severities']) != {'error', 'warning', 'exclusion'}):
        raise ValueError('native placement DRC did not evaluate all severities')
    if any(isinstance(check, dict) and check.get('key') == DRC_KEEPOUT
           for check in report['ignored_checks']):
        raise ValueError('native placement DRC explicitly ignored placement category items_not_allowed')
    if any(not isinstance(check, dict) or not isinstance(check.get('key'), str)
           or check['key'] in DRC_COLLISIONS | {DRC_MASK, DRC_KEEPOUT} | {'courtyards_overlap', 'courtyard_overlap'}
           or check['key'].startswith('silk_')
           for check in report['ignored_checks']):
        raise ValueError('native placement DRC evaluation unavailable or explicitly ignored')
    violations = report['violations']
    if any(not isinstance(v, dict) or not isinstance(v.get('type'), str) for v in violations):
        raise ValueError('native DRC contains malformed violations')
    require_evaluated_limits(report, finding_sources(board))
    from .outline_containment import run
    report['violations'].extend(run(board, scratch))
    # Excluded courtyard findings still prevent a placed result.
    return report


class _DrcItemStates(dict):
    """Existing collision states plus independent native keep-out witnesses."""


# Computational coverage guard, not a physical design limit (AGENTEE-427).
KEEPOUT_MAX_STEPS = 1_000_000


class KeepoutWorkLimit(ValueError):
    """Native keep-out coverage is unknown when deterministic work is exhausted."""

    outcome = 'unknown'
    reason = 'keepout_work_limit'

    def __init__(self, steps_used, max_steps):
        self.steps_used = steps_used
        self.max_steps = max_steps
        super().__init__(f'native keepout work limit exhausted: {steps_used}/{max_steps} steps; '
                         'items_not_allowed geometry unavailable')


def _keepout_item_states(path, states, *, max_steps=None, stats=None):
    """Bind copper to contacted areas using one native load and cached native indexes.

    Contact tests use absolute poses first. Only then normalize each contacted
    owner once, on the same disposable board, and cache its local area records.
    The budget counts inventory, pairs, layer tests, contour vertices and witness
    assembly; exhaustion raises typed unknown coverage, never partial witnesses.
    """
    import pcbnew as k
    limit = KEEPOUT_MAX_STEPS if max_steps is None else max_steps
    if type(limit) is not int or limit < 0:
        raise ValueError('keepout max_steps must be a nonnegative integer')
    work = {'steps_used': 0, 'max_steps': limit, 'board_loads': 0,
            'area_pairs': 0, 'layer_tests': 0, 'normalized_owners': 0}
    if stats is not None:
        stats.update(work)

    def charge(count=1):
        if work['steps_used'] + count > limit:
            raise KeepoutWorkLimit(work['steps_used'], limit)
        work['steps_used'] += count
        if stats is not None:
            stats.update(work)

    charge()
    board = k.LoadBoard(str(path))
    work['board_loads'] = 1
    areas, items, owners, local_areas = {}, [], {}, {}

    def add_area(owner, area):
        charge()
        # Keep every native area, including repeated UUIDs. Local lookup below
        # retains the original first-match semantics within the first owner.
        areas[len(areas)] = (owner, area, set(area.GetLayerSet().Seq()))

    for zone in board.Zones():
        charge()
        if zone.GetIsRuleArea():
            add_area(None, zone)
    for track in board.GetTracks():
        charge()
        items.append((None, track))
    for fp in board.GetFootprints():
        charge()
        owner = fp.m_Uuid.AsString()
        first_owner = owner not in owners
        owners.setdefault(owner, fp)
        for zone in fp.Zones():
            charge()
            if first_owner:
                local_areas.setdefault((owner, zone.m_Uuid.AsString()), zone)
            if zone.GetIsRuleArea():
                add_area(owner, zone)
        for pad in fp.Pads():
            charge()
            items.append((owner, pad))

    def record(area, owner):
        # rule_area traverses the saved polygon. Charge its native vertex count
        # before serialization, including unqualified holes/contours.
        charge(1 + area.Outline().TotalVertices())
        return rule_area(area, owner or 'board')

    contacts_by_item, local_ids = {}, set()
    for owner, item in items:
        charge()
        uid = item.m_Uuid.AsString()
        layers = set(item.GetLayerSet().Seq())
        shapes = {}
        contacts = []
        for area_id, (area_owner, area, area_layers) in areas.items():
            charge()
            work['area_pairs'] += 1
            hit = False
            for layer in sorted(layers & area_layers):
                charge(1 + area.Outline().TotalVertices())
                work['layer_tests'] += 1
                if layer not in shapes:
                    shapes[layer] = item.GetEffectiveShape(layer)
                if area.Outline().Collide(shapes[layer], 0):
                    hit = True
                    break
            if hit:
                local = owner is not None and owner == area_owner
                contacts.append((local, area_id))
                if local:
                    local_ids.add(area_id)
        contacts_by_item[uid] = contacts

    # Serialize absolute areas before any owner moves. Cache the complete
    # environment too: custom disallow rules need it for no-contact items.
    absolute_ids = {area_id for contacts in contacts_by_item.values()
                    for local, area_id in contacts if not local}
    needs_environment = any(not contacts for contacts in contacts_by_item.values())
    if needs_environment:
        absolute_ids.update(areas)
    absolute = {uid: record(areas[uid][1], areas[uid][0]) for uid in sorted(absolute_ids)}
    environment = sorted(absolute.values(), key=lambda a: a['uuid']) if needs_environment else []
    environment_proven = all(a['outline']['status'] == 'proven' for a in environment)
    local_records = {}
    local_witnesses = {}
    for uid in sorted(local_ids):
        owner, area, _ = areas[uid]
        if owner not in local_witnesses:
            charge()
            fp = owners[owner]
            # All absolute contacts and records are captured before normalization.
            # Unflip the owner and its areas together on this disposable board.
            if fp.IsFlipped():
                fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
            fp.SetOrientationDegrees(0)
            fp.SetPosition(k.VECTOR2I(0, 0))
            enabled = board.GetEnabledLayers()
            for pad in fp.Pads():
                layers = k.LSET()
                for layer in pad.GetLayerSet().Seq():
                    if enabled.Contains(layer):
                        layers.AddLayer(layer)
                pad.SetLayerSet(layers)
            # Inventory only this owner, once; do not rescan the board per contact.
            # Keep the existing per-owner normalization work unit: inventory
            # size is bounded by the earlier pad visits and native layer count.
            local_witnesses[owner] = (owner, json.dumps({
                'pads': _footprint_pad_inventory(fp), 'graphics': _local_graphics(fp)}, sort_keys=True))
            work['normalized_owners'] += 1
        local_records[uid] = record(local_areas[owner, area.m_Uuid.AsString()], owner)

    result = {}
    for uid, contacts in contacts_by_item.items():
        charge()
        witnesses = []
        for local, area_id in contacts:
            charge()
            area_record = (local_records if local else absolute)[area_id]
            if area_record['outline']['status'] != 'proven':
                witnesses = None
                break
            witnesses.append((local_witnesses[states[uid][0]] if local else states[uid], area_record))
        if witnesses == []:
            charge(len(environment))
            witnesses = [(states[uid], environment)] if environment_proven else None
        result[uid] = None if witnesses is None else tuple(sorted(map(_canonical, witnesses)))
    if stats is not None:
        stats.update(work)
    return result


def _local_graphics(fp):
    """Graphic primitives of a footprint already normalized to the origin at 0 degrees."""
    return sorted(({'layer': int(item.GetLayer()), 'width_nm': int(item.GetWidth()),
                    'shape': primitive(item)} for item in fp.GraphicalItems()
                   if item.GetClass() == 'PCB_SHAPE'), key=lambda item: item['shape']['uuid'])


def _drc_item_states(path):
    """Bind collision identities to native saved geometry and footprint transforms."""
    import pcbnew
    board = pcbnew.LoadBoard(str(path))
    inventory = _pad_inventory(board)
    states = _DrcItemStates()
    states.noncopper = set()
    for fp in board.GetFootprints():
        owner = fp.m_Uuid.AsString()
        if owner in states.noncopper:
            # Owner-local lookups select a footprint by KIID; a duplicate would be silently ambiguous.
            raise ValueError('native collision item identities ambiguous')
        states.noncopper.add(owner)
        pose = (int(fp.GetPosition().x), int(fp.GetPosition().y),
                fp.GetOrientationDegrees(), int(fp.GetLayer()))
        fp.SetOrientationDegrees(0)
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        geometry = json.dumps({'pads': inventory[owner], 'graphics': _local_graphics(fp)}, sort_keys=True)
        for item in [fp, *fp.Pads(), *fp.GraphicalItems(), *fp.GetFields()]:
            uuid = item.m_Uuid.AsString()
            if uuid in states and states[uuid][0] != owner:
                # A KIID shared across footprints would merge their states.
                raise ValueError('native collision item identities ambiguous')
            states[uuid] = (owner, geometry, pose)
    from .sublayout_probe import facts
    for item in facts(board)['items']:
        if not item['supported']:
            continue
        uid = item['uuid']
        if uid in states:
            raise ValueError('native collision item identities ambiguous')
        # Native facts ignore serialization whitespace and retain absolute
        # conductor/drawing geometry, layers and widths.
        states[uid] = (uid, json.dumps(item, sort_keys=True), ())
    states.keepouts = _keepout_item_states(path, states)
    return states


def _canonical(value):
    return json.dumps(value, sort_keys=True)


def _comparison_changes(recorded, repeated):
    """Per-key finding multiset differences between two DRC comparisons, ignoring report order."""
    changes = {}
    for name in sorted(recorded.keys() | repeated.keys()):
        old, new = recorded.get(name), repeated.get(name)
        if isinstance(old, list) and isinstance(new, list):
            old_set, new_set = Counter(map(_canonical, old)), Counter(map(_canonical, new))
            if old_set != new_set:
                changes[name] = {'added': [json.loads(v) for v in sorted((new_set - old_set).elements())],
                                 'removed': [json.loads(v) for v in sorted((old_set - new_set).elements())]}
        elif _canonical(old) != _canonical(new):
            changes[name] = {'recorded': old, 'repeated': new}
    return changes


class CollisionMeasurementRefusal(ValueError):
    """Stateless comparison cannot decide an unmeasured hard collision; native geometry required."""


def _drc_comparison(before, after, before_states=None, after_states=None, sources=None,
                    unchanged_copper=None):
    """Preserve rigid intra-footprint findings; moved cross-footprint collisions are new.

    Native geometry is supplied by the publication path, never by the caller's
    placement plan. Numeric findings bind to absolute saved geometry, including single-item findings.
    Without saved geometry an unmeasured hard collision on baseline items refuses,
    unless ``unchanged_copper`` is the caller's write-scope proof for the compared boards.
    """
    if unchanged_copper is not None and (
            unchanged_copper.get('status') != 'allowed' or
            any(not isinstance(unchanged_copper.get(f), str) for f in ('before_sha256', 'after_sha256'))):
        raise ValueError('unchanged-copper proof is not an allowed write scope')
    if (before['ignored_checks'] != after['ignored_checks'] or
            before.get('included_severities') != after.get('included_severities')):
        raise ValueError('native baseline/candidate DRC evaluation settings differ')

    if any(check.get('key') == DRC_KEEPOUT for report in (before, after)
           for check in report['ignored_checks']):
        raise ValueError('native items_not_allowed evaluation explicitly ignored')

    def key(finding, states):
        items = finding.get('items')
        if (not isinstance(items, list) or not items or
                any(not isinstance(i, dict) or not isinstance(i.get('uuid'), str) or not i['uuid'] for i in items)):
            raise ValueError('native DRC finding lacks item identities')
        identities = tuple(sorted(i['uuid'] for i in items))
        provenance = finding['constraint_provenance']
        # An existing advisory cannot hide a new sourced violation on the same
        # items; crossing the outline is new even below an already violated floor.
        identity = (finding['type'], identities, provenance['enforcement'],
                    finding['type'] == 'copper_edge_clearance' and
                    provenance['reason'] == 'Structural invariant; numeric provenance does not relax it')
        if finding['type'] == DRC_KEEPOUT:
            if states is not None and set(identities) <= getattr(states, 'noncopper', set()):
                return identity
            if states is None and unchanged_copper is not None:
                return identity
            contexts = getattr(states, 'keepouts', {})
            try:
                witnesses = tuple(contexts[uuid] for uuid in identities)
                if any(not witness for witness in witnesses):
                    raise KeyError('missing native contact')
            except KeyError as exc:
                raise ValueError('native items_not_allowed geometry unavailable') from exc
            return (*identity, finding.get('description'), witnesses)
        if states is None:
            if numeric_finding(finding) and finding['type'] not in DRC_COLLISIONS:
                return (*identity, finding.get('description'), json.dumps(items, sort_keys=True))
            if finding['type'] in DRC_COLLISIONS:
                # Without saved geometry the printed measurement is the only physical
                # witness: any changed value on the same items is new, never baseline.
                actual = measured_actual(finding)
                return (*identity, Decimal(actual[1]) if actual else None)
            return identity
        if finding['type'] not in DRC_COLLISIONS | {'solder_mask_bridge'} and not numeric_finding(finding):
            return identity
        try:
            geometry = [states[uuid] for uuid in identities]
        except KeyError as exc:
            raise ValueError('native collision item geometry unavailable') from exc
        # Local pad geometry proves a rigid intra-footprint violation unchanged.
        intra = (finding['type'] in DRC_COLLISIONS | {'solder_mask_bridge'} and
                 len({state[0] for state in geometry}) == 1)
        return (*identity, tuple(state[:2] if intra else state for state in geometry))

    for report in (before, after):
        for finding in report['violations']:
            classify_finding(finding, sources)
    if (before_states is None or after_states is None) and unchanged_copper is None:
        # A missing measurement leaves only the item pair: neither a worsened hard
        # collision nor a lost hard measurement on baseline items can be decided.
        hard = {}
        for finding in before['violations']:
            if finding['type'] in DRC_COLLISIONS:
                pair = key(finding, before_states)[:2]
                hard[pair] = hard.get(pair, False) or finding['constraint_provenance']['enforcement'] == 'hard'
        for finding in after['violations']:
            if finding['type'] not in DRC_COLLISIONS or measured_actual(finding):
                continue
            pair = key(finding, after_states)[:2]
            if pair in hard and (hard[pair] or finding['constraint_provenance']['enforcement'] == 'hard'):
                raise CollisionMeasurementRefusal(
                    'stateless collision comparison lacks a measured actual; native saved geometry required')
    old = Counter(key(v, before_states) for v in before['violations'])
    remaining = Counter(key(v, after_states) for v in after['violations']) - old
    # Native report order varies between runs of the same board (AGENTEE-383); choosing among
    # findings of one identity in canonical order makes the comparison a function of the findings.
    chosen = set()
    for index in sorted(range(len(after['violations'])), key=lambda i: _canonical(after['violations'][i])):
        identity = key(after['violations'][index], after_states)
        if remaining[identity]:
            chosen.add(index)
            remaining[identity] -= 1
    added = [v for i, v in enumerate(after['violations']) if i in chosen]
    return {'added_findings': added,
            'blocking_findings': [v for v in after['violations'] if v['type'] == 'copper_outside_outline'] + [v for v in added if v['type'] == DRC_KEEPOUT and
                not (after_states is not None and {i['uuid'] for i in v['items']} <=
                     getattr(after_states, 'noncopper', set()))] + [v for v in added if
                v['constraint_provenance']['enforcement'] == 'hard' and
                (v['type'] in DRC_COLLISIONS | {DRC_MASK} or numeric_finding(v)) and
                (v['type'] != DRC_MASK or after_states is None or
                 len({after_states[i['uuid']][0] for i in v['items']}) > 1)],
            'baseline_findings': before['violations'],
            'candidate_findings': after['violations'],
            'advisory_findings': [v for v in after['violations'] if
                                  v['constraint_provenance']['enforcement'] == 'advisory'],
            'silkscreen_findings': [v for v in added if v['type'].startswith('silk_')],
            'added_by_type': dict(Counter(v['type'] for v in added))}


def _proposal_drc(source, output):
    """Use the source project's unchanged rules on both saved boards."""
    scratch = output.parent
    baseline_dir = scratch / 'baseline'
    baseline_dir.mkdir()
    baseline = baseline_dir / output.name
    shutil.copyfile(source, baseline)
    project_payloads = {}
    for suffix in ('.kicad_pro', '.kicad_dru'):
        settings = source.with_suffix(suffix)
        project_payloads[suffix] = settings.read_bytes() if settings.is_file() else None
        payload = project_payloads[suffix]
        if payload is not None:
            # Rule-level ignores never reach the report's ignored_checks.
            if suffix == '.kicad_dru' and re.search(rb'\(\s*severity\s+"?ignore\b', payload, re.I):
                raise ValueError('native placement DRC evaluation explicitly ignored by a custom rule')
            baseline.with_suffix(suffix).write_bytes(payload)
            output.with_suffix(suffix).write_bytes(payload)
    before = _native_drc(baseline, baseline_dir)
    after = _native_drc(output, scratch)
    baseline_states = _drc_item_states(baseline)
    placed_states = _drc_item_states(output)
    sources = finding_sources(source, before['violations'] + after['violations'])
    comparison = _drc_comparison(before, after, baseline_states, placed_states, sources)
    artwork_regressions = []
    flipped_conflicts = []
    if any(v['type'] in SILK_CONFLICTS for v in comparison['silkscreen_findings']):
        import pcbnew
        # Retain the exact pre-relocation native observation as well as the gate.
        shutil.copyfile(scratch / 'native-drc.json', scratch / 'pre-artwork-drc.json')
        board = pcbnew.LoadBoard(str(output))
        source_board = pcbnew.LoadBoard(str(source))
        source_sides = {fp.m_Uuid.AsString(): fp.GetLayer() for fp in source_board.GetFootprints()}
        flipped = {fp.m_Uuid.AsString() for fp in board.GetFootprints()
                   if source_sides.get(fp.m_Uuid.AsString()) != fp.GetLayer()}
        artwork = relocate(board, comparison['silkscreen_findings'], flipped)
        if not pcbnew.SaveBoard(str(output), board):
            raise ValueError('native artwork save failed')
        # SaveBoard materialises project defaults; DRC must use original rule bytes.
        for suffix, payload in project_payloads.items():
            target = output.with_suffix(suffix)
            if payload is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(payload)
        pre_artwork = after
        after = _native_drc(output, scratch)
        artwork_states = _drc_item_states(output)
        sources = finding_sources(source, before['violations'] + pre_artwork['violations'] + after['violations'])
        artwork_delta = _drc_comparison(pre_artwork, after, placed_states, artwork_states, sources)
        artwork_regressions = [v for v in artwork_delta['added_findings']
                               if v['type'] not in SILK_CONFLICTS and
                               not (numeric_finding(v) and v['constraint_provenance']['enforcement'] == 'advisory')]
        final = _drc_comparison(before, after, baseline_states, artwork_states, sources)
        flipped_conflicts = [dict(type='placement_silkscreen_conflict', **d)
                             for d in artwork['search_diagnostics']
                             if d['reason'] == 'footprint_side_flipped']
        artwork.update(schema='agent-ee.placement-silkscreen/1',
                       added_before=comparison['added_by_type'], added_after=final['added_by_type'],
                       regressions=artwork_regressions,
                       findings=[dict(type='placement_silkscreen_conflict', native=v)
                                 for v in final['silkscreen_findings'] if v['type'] in SILK_CONFLICTS] +
                                flipped_conflicts)
        write_json(scratch / 'placement-silkscreen.json', artwork)
        comparison = final
    comparison['open_defects'] = [
        dict(type='placement_silkscreen_conflict', status='open', verdict='defect', native=v)
        for v in comparison['silkscreen_findings'] if v['type'] in SILK_CONFLICTS] + [
        dict(d, status='open', verdict='defect') for d in flipped_conflicts]
    write_json(scratch / 'native-drc-comparison.json', comparison)
    if comparison['blocking_findings']:
        raise ValueError(f"saved-board placement introduced native DRC collisions: {comparison['added_by_type']}")
    if artwork_regressions:
        raise ValueError('silkscreen relocation introduced other native DRC findings')
    if comparison['open_defects']:
        after = _stamp_lineage(source, output, project_payloads, comparison, before, baseline_states)
    return [v for v in after['violations'] if v['type'] in DRC_COURTYARDS], comparison


def _stamp_lineage(source, output, project_payloads, comparison, before, baseline_states):
    """Record this result's carry digest in the saved board's title block (AGENTEE-351).

    A published board with open defects names them in its own bytes, so a copy released
    outside the campaign must bring its placement evidence. Native DRC is repeated on the
    stamped bytes and must reproduce the same comparison, under the same captured rule
    provenance and independent of native report order (AGENTEE-383).
    """
    import copy
    import pcbnew
    from .placement_defects import STAMP, carry_digest, stamp, stamp_slot
    data = output.read_bytes()
    digests = stamp(data) or []
    # User text in comment 9 keeps its slot; the stamp takes the highest free one (AGENTEE-412).
    number = stamp_slot(data)
    board = pcbnew.LoadBoard(str(output))
    block = board.GetTitleBlock()
    slot = int(number) - 1
    if block.GetComment(slot) and not digests:
        raise ValueError(f'title block comment {number} is occupied; placement lineage cannot be recorded')
    value = carry_digest(comparison['open_defects'])
    recorded = [*digests, *([value] if value not in digests else [])]
    block.SetComment(slot, ' '.join([STAMP, *recorded]))
    board.SetTitleBlock(block)
    if not pcbnew.SaveBoard(str(output), board):
        raise ValueError('native lineage stamp save failed')
    if stamp(output.read_bytes()) != recorded:
        raise ValueError('native lineage stamp did not save as recorded')
    for suffix, payload in project_payloads.items():
        target = output.with_suffix(suffix)
        if payload is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(payload)
    after = _native_drc(output, output.parent)
    # Reclassify a copy: the recorded comparison's baseline findings are these same objects.
    before = copy.deepcopy(before)
    sources = finding_sources(source, before['violations'] + comparison['candidate_findings'] + after['violations'])
    # Both sides carry native saved states, so collision identities bind to saved geometry and
    # the stateless unmeasured-collision refusal (AGENTEE-369) never applies to this re-check.
    restamped = _drc_comparison(before, after, baseline_states, _drc_item_states(output), sources)
    changes = _comparison_changes({k: v for k, v in comparison.items() if k != 'open_defects'}, restamped)
    if changes:
        write_json(output.parent / 'native-drc-restamp.json',
                   {'schema': 'agent-ee.placement-restamp-drc/1', 'changes': changes})
        named = sorted({(f['type'], tuple(sorted(i['uuid'] for i in f.get('items', []))))
                        for change in changes.values()
                        for f in change.get('added', []) + change.get('removed', [])})
        summary = '; '.join(f'{name}: +{len(c["added"])} -{len(c["removed"])}' if 'added' in c else name
                            for name, c in changes.items())
        findings = ', '.join(f'{t} {"/".join(u)}' for t, u in named[:5]) + (' ...' if len(named) > 5 else '')
        raise ValueError(f'native DRC changed after recording the placement lineage stamp ({summary}'
                         + (f'; findings {findings}' if findings else '') + ')')
    return after


SIDE_CHANGES = 'agent-ee.side-changes/1'


def _side(fp):
    return 'B' if fp.IsFlipped() else 'F'


def _side_changes(source, authorization, plan):
    """Footprint side changes the request explicitly authorizes (AGENTEE-207 R5): {uuid: side}.

    The authorization, never the plan, grants a flip. It is bound to the original board bytes and
    names each exact reference and UUID once; the plan must declare the same side for exactly those
    movable footprints and no other side change. A footprint with a custom pad or a per-layer
    padstack has no qualified flipped geometry, so its flip is refused."""
    import pcbnew
    board = pcbnew.LoadBoard(str(source))
    by_uuid = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    changes = {}
    if authorization is not None:
        if (not isinstance(authorization, dict) or authorization.get('schema') != SIDE_CHANGES
                or set(authorization) != {'schema', 'source_sha256', 'changes'}
                or not isinstance(authorization['changes'], list) or not authorization['changes']):
            raise ValueError(f'side-change authorization must be one {SIDE_CHANGES} record')
        if authorization['source_sha256'] != digest(source):
            raise ValueError('side-change authorization is bound to another board')
        for change in authorization['changes']:
            fp = by_uuid.get(change.get('uuid')) if isinstance(change, dict) else None
            if (fp is None or set(change) != {'ref', 'uuid', 'side'} or fp.GetReference() != change['ref']
                    or change['side'] not in ('F', 'B') or change['uuid'] in changes):
                raise ValueError('side change must name one exact original reference and UUID')
            if change['side'] == _side(fp):
                raise ValueError(f"side change for {change['ref']} keeps its original side")
            if any(pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS) == pcbnew.PAD_SHAPE_CUSTOM
                   or pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL for pad in fp.Pads()):
                raise ValueError(f"side change for {change['ref']}: flipped custom or per-layer pad geometry is unqualified")
            changes[change['uuid']] = change['side']
    planned = set()
    for placement in plan['placements']:
        planned.add(placement['uuid'])
        declared = placement.get('side')
        if placement['uuid'] in changes:
            if placement['fixed'] or declared != changes[placement['uuid']]:
                raise ValueError(f"placement {placement['ref']} side differs from its authorized side change")
        elif declared is not None and declared != _side(by_uuid[placement['uuid']]):
            raise ValueError(f"placement {placement['ref']} side change is not authorized")
    if set(changes) - planned:
        raise ValueError('authorized side change has no placement')
    return changes


def _flipped_inventory(source, changes):
    """Expected footprint-local pad facts: the original footprints after the declared native flip."""
    import pcbnew
    board = pcbnew.LoadBoard(str(source))  # disposable original; the baseline is never flipped
    expected = {}
    for fp in board.GetFootprints():
        if fp.m_Uuid.AsString() in changes:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            expected[fp.m_Uuid.AsString()] = _footprint_pad_inventory(fp)
    return expected


def _apply_plan(board, plan, output, changes=None):
    import pcbnew
    by_uuid = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    for placement in plan['placements']:
        fp = by_uuid[placement['uuid']]
        if changes and placement['uuid'] in changes and _side(fp) != changes[placement['uuid']]:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        if not placement['fixed']:
            fp.SetOrientationDegrees(placement['rotation_deg'])
            fp.SetPosition(pcbnew.VECTOR2I(*placement['position_nm']))
    pcbnew.SaveBoard(str(output), board)


def _saved_judgment(output, plan):
    facts = extract(output)
    actual = {f['uuid']: f for f in facts['footprints']}
    for expected in plan['placements']:
        fp = actual[expected['uuid']]
        if fp['ref'] != expected['ref'] or fp['position_nm'] != expected['position_nm'] or (fp['rotation_deg']-expected['rotation_deg']) % 360:
            raise ValueError('native saved placement differs from planned transform')
    # Envelope-bound fixed-courtyard overhangs (AGENTEE-212) are judged as declared, nowhere else.
    policy = plan['policy']
    judgment = judge(facts, plan.get('judge_policy'), overhangs=policy.get('fixed_overhangs'))
    if 'judge_policy' in plan:
        if judgment['score'] <= plan['before']['score']:
            raise ValueError('saved-board placement score did not strictly improve')
    if 'groups' in policy:
        from .placement_groups import grade
        judgment['group_grading'] = grade(facts, policy['groups'])
    if 'electrical_locality' in policy:
        from .electrical_locality import grade
        judgment['locality_grading'] = grade(facts, policy['electrical_locality'])
    if 'block_arrangement' in plan:
        from .block_arrangement import saved_rules
        judgment['block_rules'] = saved_rules(facts, plan, policy)
    if 'advisory_regions' in policy:
        # Re-derived on the saved board: shadows follow their owners' native poses (AGENTEE-436).
        from .advisory_regions import grade
        from .placer import _fixed_refs
        fixed = {p['ref'] for p in plan['placements'] if p['fixed']} | _fixed_refs(facts, policy)
        judgment['advisory_regions'] = grade(facts, policy['advisory_regions'], fixed)
    return facts, judgment


def _saved_block_contract(output, contract, original_capture, plan, effective_policy, section):
    """Independently re-extract and judge the saved board before publication."""
    from .block_contract import judge as judge_contract
    saved = extract(output)
    if plan.get('policy') != effective_policy:
        raise ValueError('returned plan policy differs from actual effective request policy')
    record = judge_contract(saved, contract, original_capture=original_capture, plan=plan)
    path = output.parent / 'block-contract.json'
    write_json(path, record)
    if record['status'] not in ('pass', 'info'):
        raise ValueError('saved-board block contract has required missing evidence or a violation')
    if 'member_refinement' in plan['block_arrangement']:
        from .block_arrangement import validate_policy
        from .block_refinement import verify_saved
        expected = validate_policy(section['arrangement'], {b['id'] for b in contract['allocation']['blocks']},
                                   {f['ref'] for f in original_capture['footprints']})
        if plan['block_arrangement']['arrangement'] != expected:
            raise ValueError('refinement arrangement differs from actual request')
        verification_path = output.parent / 'block-refinement-verification.json'
        try:
            verification = verify_saved(saved, effective_policy, contract, plan['block_arrangement'], section['candidates'])
        except ValueError as exc:
            write_json(verification_path, {'schema': 'agent-ee.block-member-refinement-verification/1',
                'status': 'refused', 'board_sha256': saved['board_sha256'], 'reason': str(exc)})
            raise
        write_json(verification_path, verification)
        record['member_refinement_verification'] = {'path': verification_path.name,
            'sha256': digest(verification_path), 'status': verification['status']}
        write_json(path, record)
    return {'path': path.name, 'sha256': digest(path), 'status': record['status']}


def _board_only(board):
    return frozenset(fp.GetReference() for fp in board.GetFootprints() if fp.IsBoardOnly())


def _evaluate_proposal(board, output, plan, netlist_bytes, initial_facts, source, side_changes=None):
    import pcbnew
    changes = _side_changes(source, side_changes, plan)
    pads = {**_pad_inventory(board), **_flipped_inventory(source, changes)}
    from .geometry_probe import box_nm
    expected_custom = {}
    placements = {p['uuid']: p for p in plan['placements']}
    for fp in board.GetFootprints():
        custom = [{'uuid': p.m_Uuid.AsString(), 'position_nm': [p.GetPosition().x, p.GetPosition().y],
                   'rotation_deg': p.GetOrientationDegrees(),
                   'custom_geometry': {'status': 'proven', 'bbox_nm': box_nm(p.GetBoundingBox())}}
                  for p in fp.Pads() if p.GetShape() == pcbnew.PAD_SHAPE_CUSTOM]
        if custom:
            proposal = placements[fp.m_Uuid.AsString()]
            moved = transform({'position_nm': [fp.GetPosition().x, fp.GetPosition().y],
                               'rotation_deg': fp.GetOrientationDegrees(), 'courtyards': {}, 'pads': custom},
                              proposal['position_nm'], proposal['rotation_deg'])
            expected_custom.update({p['uuid']: p['custom_geometry']['bbox_nm'] for p in moved['pads']})
    placement_trace.phase('export')  # Trace phases (AGENTEE-430) are no-ops unless tracing is on.
    if changes:
        _apply_plan(board, plan, output, changes)
    else:
        _apply_plan(board, plan, output)  # unchanged call without side changes
    placement_trace.phase('native_checks')
    if _pad_inventory(pcbnew.LoadBoard(str(output))) != pads:
        raise ValueError('native saved board changed footprint pad geometry')
    original = {fp.m_Uuid.AsString(): _side(fp) for fp in pcbnew.LoadBoard(str(source)).GetFootprints()}
    if {fp.m_Uuid.AsString(): _side(fp) for fp in pcbnew.LoadBoard(str(output)).GetFootprints()} != {**original, **changes}:
        raise ValueError('native saved footprint side differs from the declared side changes')
    overlaps, comparison = _proposal_drc(source, output)
    if _pad_inventory(pcbnew.LoadBoard(str(output))) != pads:
        raise ValueError('native saved board changed footprint pad geometry')
    saved, judgment = _saved_judgment(output, plan)
    initial_custom = {p['uuid']: p['custom_geometry']['primitives'] for f in initial_facts['footprints']
                      for p in f['pads'] if p.get('custom_geometry', {}).get('status') == 'proven'}
    for fp in saved['footprints']:
        for pad in fp['pads']:
            if pad['uuid'] in expected_custom:
                geometry = pad.get('custom_geometry', {})
                if geometry.get('status') != 'proven' or (geometry.get('bbox_nm') != expected_custom[pad['uuid']]
                        or geometry.get('primitives') != initial_custom[pad['uuid']]):
                    raise ValueError('native saved custom-pad copper extent differs from planned transform')
    write_json(output.parent / 'placement-probe.json', saved)
    write_json(output.parent / 'placement-judgment.json', judgment)
    _netlist_binding(netlist_bytes, saved, _board_only(board))
    if judgment['verdict'] != 'pass' or overlaps:
        raise ValueError('saved-board placement failed polygon judge or native courtyard DRC')
    if 'group_grading' in judgment and not placement_groups.satisfied(judgment['group_grading']):
        raise ValueError('saved-board placement failed its declared placement groups')
    if judgment.get('locality_grading', {}).get('status', 'pass') not in ('pass', 'info'):
        raise ValueError('saved-board placement failed its declared electrical locality')
    if any(r['enforcement'] == 'hard' and r['status'] != 'pass' for r in judgment.get('block_rules', [])):
        raise ValueError('saved-board placement failed a hard block arrangement rule')
    from .reserved_area import saved_report
    reserve = saved_report(output, saved, plan)
    judgment['reserved_area'] = reserve
    write_json(output.parent / 'placement-judgment.json', judgment)
    from .silk_artwork import containment_witness
    return {'schema': 'agent-ee.placed-candidate/1', 'source_sha256': plan['board_sha256'],
            'containment': containment_witness(output),
            'board_sha256': digest(output), 'netlist_sha256': hashlib.sha256(netlist_bytes).hexdigest(),
            'status': 'placed', 'outcome': 'defect' if comparison['open_defects'] else 'info',
            'verdict': 'defect' if comparison['open_defects'] else 'info',
            'open_defects': comparison['open_defects'], 'min_courtyard_edge_mm': plan['min_courtyard_edge_mm'],
            'plan': plan, 'judgment': judgment, 'reserved_area': reserve,
            'native_courtyard_overlaps': overlaps, 'native_drc_comparison': comparison,
            'authority': 'disposable-unsealed-proposal'}


def _retain_failure(root, name, scratch, error):
    retained = Path(tempfile.mkdtemp(prefix=name + '-failure-', dir=root))
    names = ('candidate.kicad_pcb', 'placement-plan.json', 'placement-probe.json',
             'placement-judgment.json', 'placement-write-scope.json', 'native-drc.json', 'native-drc.stdout.txt',
             'native-drc.stderr.txt', 'native-drc-execution.json', 'native-drc-comparison.json',
             'candidate.kicad_pro', 'candidate.kicad_dru', 'pre-artwork-drc.json', 'placement-silkscreen.json',
             'native-drc-restamp.json', 'project-models.json', 'planning-execution.json', 'block-contract.json',
             'block-refinement-verification.json')
    for name in names:
        path = scratch / name
        if path.is_file():
            target = 'failed-' + name if name.startswith('candidate.kicad_') else name
            shutil.copyfile(path, retained / target)
    if (scratch / 'baseline').is_dir():
        shutil.copytree(scratch / 'baseline', retained / 'baseline')
    failure = {'schema': 'agent-ee.placement-failure/1', 'execution': 'failed',
               'outcome': 'unknown', 'status': 'not_placed', 'error': str(error)}
    if isinstance(error, KeepoutWorkLimit):
        failure.update(reason=error.reason, steps_used=error.steps_used, max_steps=error.max_steps)
    write_json(retained / 'failure.json', failure)
    return retained


def _effective_policy(request):
    policy = request['policy']
    margin = request.get('min_courtyard_edge_mm')
    if margin is None or not isinstance(policy, dict):
        return policy
    existing = policy.get('min_courtyard_edge_mm')
    if existing is not None and existing != margin:
        raise ValueError('conflicting min_courtyard_edge_mm between request and policy')
    return {**policy, 'min_courtyard_edge_mm': margin}


def _heartbeat(record):
    print(json.dumps({'event': 'placement_heartbeat', **record}, sort_keys=True), file=sys.stderr, flush=True)


def _plan(facts, request, policy=None, netlist=None, *, execution=None):
    from .placement_runtime import active
    from .placement_rust_runtime import Session
    runtime = active() or Session()
    plan = runtime.plan(facts, request, policy, netlist, heartbeat=_heartbeat)
    if execution is not None:
        execution.update(schema='agent-ee.planning-execution/1', implementation=runtime.implementation)
        if runtime.implementation == 'rust':
            execution.update(runtime.calls[-1])
    return plan


def _plan_local(facts, request, policy=None, netlist=None, *, heartbeat=None):
    heartbeat = _heartbeat if heartbeat is None else heartbeat
    policy = _effective_policy(request) if policy is None else policy
    if sum(key in request for key in ('targeted', 'targeted_plans', 'blocks')) > 1:
        raise ValueError('targeted, targeted_plans and blocks cannot be combined')
    if 'blocks' in request:
        # Candidates are packed and admitted against the captured request policy; the
        # effective policy adds only board obstacles, which arrangement enforces.
        from .block_arrangement import plan_from_request
        if netlist is None:
            raise ValueError('block arrangement needs the captured native netlist')
        return plan_from_request(facts, request['blocks'], _effective_policy(request), policy, netlist, heartbeat)
    if 'targeted' in request:
        from .placement_plans import plan
        return plan(facts, policy, request['targeted'])
    if 'targeted_plans' in request:
        from .placement_plans import select
        return select(facts, policy, request['targeted_plans'])
    return pack(facts, policy, heartbeat=heartbeat)


def place_candidate(source, netlist, destination, request):
    """Publish only a fully saved/judged proposal in a fresh authorized directory.

    Errors propagate and never publish a placed candidate. Typed packing failures
    return without creating a destination. Existing coordinator baseline, source
    allowlist and evidence-sealing checks remain the caller's responsibility.
    """
    from .candidate_visuals import produce, skip_requested
    placement_trace.phase('prep')
    skip_visuals = skip_requested(request.get('skip_visuals', False))
    source, netlist = Path(source).resolve(), Path(netlist).resolve()
    destination = Path(destination).absolute()
    root = _authorization(source, destination, request['authorization'])
    source_hash, netlist_bytes = digest(source), netlist.read_bytes()
    project_inputs = {source.with_suffix(suffix): (source.with_suffix(suffix).read_bytes()
                      if source.with_suffix(suffix).is_file() else None)
                      for suffix in ('.kicad_pro', '.kicad_dru')}
    targeted = 'targeted' in request or 'targeted_plans' in request
    if targeted:
        import pcbnew
        # Refuse before even creating a lease file. A successful plan is rebuilt
        # under the writer lease below; caller-provided plans are never trusted.
        policy = reserve_imu_markings(pcbnew.LoadBoard(str(source)), _effective_policy(request))
        facts = extract(source)
        placement_trace.phase(None)
        preview = _plan(facts, request, policy)
        placement_trace.phase('prep')
        if preview['status'] != 'planned':
            return preview
    with writer_lock(destination):
        _authorization(source, destination, request['authorization'])
        facts = extract(source)
        board = _unrouted_board(source)
        _netlist_binding(netlist_bytes, facts, _board_only(board))
        policy = reserve_imu_markings(board, _effective_policy(request))
        placement_trace.phase(None)  # Planning records its own prep, Stage-A and Stage-B time.
        from . import block_variants
        native_sets = (block_variants.verify_request(source, facts, request['blocks'])
                       if 'blocks' in request else [])
        contract = request.get('blocks', {}).get('contract')
        if contract is not None and (not isinstance(contract, dict)
                or contract.get('eligibility') != request['blocks'].get('side_eligibility')):
            raise ValueError('block contract and candidate sets must use the same side eligibility')
        planning_execution = {}
        plan = _plan(facts, request, policy, netlist, execution=planning_execution)
        placement_trace.phase('native_checks')
        # A per-layer padstack also leaves its copper extent unproven (AGENTEE-366):
        # the padstack refusal names the cause either way.
        refusal = plan['status'] in ('planned', 'unsupported_geometry') and _padstack_refusal(board)
        if refusal:
            return _failure(facts, policy, 'unsupported_geometry', 'unknown', plan['steps_used'], refusal)
        if plan['status'] != 'planned':
            return plan
        side_changes = request.get('side_changes')
        if native_sets:
            derived = block_variants.authorization(source, facts, native_sets, plan,
                                                    request['blocks']['side_eligibility'])
            if side_changes is not None:
                explicit = _side_changes(source, side_changes, plan)
                expected = {} if derived is None else {row['uuid']: row['side'] for row in derived['changes']}
                if explicit != expected:
                    raise ValueError('explicit side changes differ from eligibility-derived authorization')
            side_changes = derived
        with tempfile.TemporaryDirectory(prefix='.placement-staging-', dir=root) as temporary:
            scratch = Path(temporary)
            output = scratch / 'candidate.kicad_pcb'
            write_json(scratch / 'placement-plan.json', plan)
            planning_execution['plan_sha256'] = digest(scratch / 'placement-plan.json')
            write_json(scratch / 'planning-execution.json', planning_execution)
            try:
                report = _evaluate_proposal(board, output, plan, netlist_bytes, facts, source,
                                            side_changes)
                if contract is not None:
                    report['block_contract'] = _saved_block_contract(output, contract, facts, plan, policy,
                                                                    request['blocks'])
                report['planning_execution'] = {
                    'path': 'planning-execution.json', 'sha256': digest(scratch / 'planning-execution.json'),
                    'implementation': planning_execution['implementation']}
                from .placement_adapter import write_scope
                protected = ([p['uuid'] for p in plan['placements'] if p['fixed']]
                             if targeted else ())
                scope = write_scope(source.read_bytes(), output.read_bytes(), protected)
                write_json(scratch / 'placement-write-scope.json', scope)
                if scope['status'] != 'allowed':
                    raise ValueError(f"native saved placement exceeded write scope: {scope['violations']}")
                report['write_scope'] = scope
                placement_trace.phase('export')
                if 'block_arrangement' in plan:
                    # Review input naming every placed block's identity, class and enforcement,
                    # bound to the saved board and graded on it (AGENTEE-355).
                    from .block_arrangement import provenance_artifact
                    write_json(scratch / 'block-provenance.json', provenance_artifact(
                        plan, report['board_sha256'], report['judgment']['block_rules'],
                        plan_sha256=digest(scratch / 'placement-plan.json'),
                        reserved_area=report['reserved_area']))
                    report['block_provenance_sha256'] = digest(scratch / 'block-provenance.json')
                report['project_models'] = copy_project_models(source, scratch)
                write_json(scratch / 'project-models.json', report['project_models'])
                report['visuals'] = produce(output, scratch / 'visuals', skip=skip_visuals)
                verify_project_models(source, scratch, report['project_models'])
                for image in report['visuals']['images']:
                    image['path'] = 'visuals/' + image['path']
                if digest(source) != source_hash or netlist.read_bytes() != netlist_bytes:
                    raise ValueError('captured placement inputs changed during execution')
                if any((p.read_bytes() if p.is_file() else None) != payload for p, payload in project_inputs.items()):
                    raise ValueError('captured placement project rules changed during execution')
                write_json(scratch / 'placement-result.json', report)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                retained = _retain_failure(root, destination.name, scratch, exc)
                raise ValueError(f'placement failed; diagnostics retained at {retained}: {exc}') from exc
            # Destination is absent and held under this writer lease. Move the
            # complete directory in one operation; no partial board is placed.
            os.rename(scratch, destination)
    placement_trace.phase(None)
    return report


def _trace_identity(source, netlist, request, report, experiment):
    from .trace_envelope import canonical_sha256, qualified, unknown
    def file_identity(path, reason):
        return qualified(digest(path)) if path.is_file() else unknown(reason)
    placed = isinstance(report, dict) and report.get('status') == 'placed'
    return {'pcb': file_identity(source, 'source board unreadable'),
            'project': file_identity(source.with_suffix('.kicad_pro'), 'source has no .kicad_pro project file'),
            'rules': qualified(canonical_sha256(request.get('policy')), kind='agent-ee.packing-policy'),
            'design_rules': file_identity(source.with_suffix('.kicad_dru'), 'source has no .kicad_dru rules file'),
            'experiment': qualified(id=experiment) if experiment else unknown('no experiment declared'),
            'netlist': file_identity(netlist, 'netlist unreadable'),
            'candidate': (qualified(report['board_sha256'], kind='placed-board',
                                    plan_sha256=canonical_sha256(report['plan'])) if placed else
                          unknown('no placed candidate: ' + (report.get('status', 'no report')
                                                             if isinstance(report, dict) else 'placement raised')))}


def _trace_path(trace_path, root, destination):
    """A fresh trace file directly inside the authorized writable root, beside the candidate."""
    trace_path = Path(trace_path).absolute()
    if trace_path.parent != root or trace_path.name.startswith('.') or trace_path == destination \
            or trace_path.exists() or trace_path.is_symlink():
        raise ValueError('trace must be a fresh file directly inside authorized scratch root, '
                         'not the candidate destination')
    return trace_path


class TracePersistenceError(RuntimeError):
    """Placement returned its report, but its requested trace was not written (AGENTEE-430).

    The trace is an observer: this is not a placement failure, and a placed candidate may
    already be published at its destination. ``report`` is the placement report;
    ``__cause__`` is the trace error.
    """

    def __init__(self, report, trace_path):
        self.report, self.trace_path = report, trace_path
        status = report.get('status') if isinstance(report, dict) else None
        super().__init__(f'placement {status}, but its trace was not written to {trace_path}')


def _persist_trace(recorder, trace_path, *, state, source, netlist, request, report, experiment):
    """Assemble and export the trace; the exception that stopped it, or None once written."""
    try:
        document = placement_trace.envelope(
            recorder, stage='place-candidate', state=state,
            identity=_trace_identity(source, netlist, request, report, experiment),
            native_tools=placement_trace.native_tool_hashes(),
            outcome=report.get('status') if isinstance(report, dict) else None)
        placement_trace.export(document, trace_path)
    except Exception as exc:
        return exc
    return None


def traced_place_candidate(source, netlist, destination, request, trace_path, *, experiment=None, limits=None):
    """``place_candidate`` with the opt-in placement trace written to a fresh ``trace_path`` (AGENTEE-430).

    The single-design-writer authorization and the trace path (a fresh, non-hidden direct
    child of the authorized writable root, not the destination) are checked before
    anything is recorded or written: a refusal leaves no trace and no candidate. After
    that, the trace is written even when placement raises; it then records a failed
    state and the placement error is re-raised. The candidate directory and report are
    unchanged.

    A trace that cannot be written never replaces the placement outcome. When placement
    raised, its own exception is re-raised, carrying the trace error, or the interruption
    (KeyboardInterrupt or another BaseException) that stopped the write, as
    ``placement_trace_error`` and a note. When placement returned, ``TracePersistenceError``
    carries the report: the candidate may exist although its trace does not.
    """
    source, netlist = Path(source).resolve(), Path(netlist).resolve()
    destination = Path(destination).absolute()
    root = _authorization(source, destination, request['authorization'])
    trace_path = _trace_path(trace_path, root, destination)
    placement_trace.Recorder(limits)  # Refuse invalid limits before placement, not after it.
    recorder = report = None
    persist = functools.partial(_persist_trace, trace_path=trace_path, source=source, netlist=netlist,
                                request=request, experiment=experiment)
    try:
        from .placement_runtime import python_reference
        with python_reference(), placement_trace.recording(limits) as recorder:
            report = place_candidate(source, netlist, destination, request)
    except BaseException as exc:
        state = {'status': 'failed' if isinstance(exc, Exception) else 'interrupted',
                 'reason': f'{exc.__class__.__name__}: {exc}'}
        failure = None
        if recorder is not None:
            try:
                failure = persist(recorder, report=report, state=state)
            except BaseException as interruption:
                failure = interruption  # Even an interrupted write never displaces the primary exception.
        if failure is not None:
            exc.placement_trace_error = failure
            exc.add_note(f'placement trace not written to {trace_path}: {failure.__class__.__name__}: {failure}')
        raise
    failure = persist(recorder, report=report, state={'status': 'completed'})
    if failure is not None:
        raise TracePersistenceError(report, trace_path) from failure
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('netlist')
    parser.add_argument('--request', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--skip-visuals', action='store_true')
    parser.add_argument('--trace', help='Python-reference placement trace: fresh file directly inside the writable root')
    parser.add_argument('--trace-experiment', help='experiment identifier recorded in the trace')
    args = parser.parse_args(argv)
    request = read_json(args.request)
    if args.skip_visuals:
        request['skip_visuals'] = True
    root = Path(request['authorization']['writable_root'])
    result_path = Path(args.result).absolute()
    if result_path.parent != root or result_path.exists() or result_path.is_symlink():
        raise ValueError('result must be a fresh file directly inside authorized scratch root')
    if args.trace is None:
        if args.trace_experiment is not None:
            raise ValueError('--trace-experiment needs --trace')
        report = place_candidate(args.board, args.netlist, args.destination, request)
    else:
        trace_path = Path(args.trace).absolute()
        if trace_path == result_path:
            raise ValueError('trace must be a fresh file directly inside authorized scratch root, not the result')
        # traced_place_candidate checks authorization and the trace path before any write.
        try:
            report = traced_place_candidate(args.board, args.netlist, args.destination, request, trace_path,
                                            experiment=args.trace_experiment)
        except TracePersistenceError as exc:
            # The placement outcome is still reported; the missing trace fails the command (exit 3).
            _write_result(result_path, exc.report)
            print(f'{exc}: {exc.__cause__.__class__.__name__}: {exc.__cause__}', file=sys.stderr)
            return 3
    _write_result(result_path, report)
    return 0 if report['status'] == 'placed' else 1


def _write_result(path, report):
    with path.open('x') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
