"""Single-writer disposable-board placement; intended for networkless sandbox use."""
import argparse
from collections import Counter
from decimal import Decimal
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

from .board_net_names import from_xml
from .locking import writer_lock
from .placement import judge
from .placement_probe import extract, custom_pad_geometry, primitive
from .placer import _failure, pack, transform
from .silkscreen import reserve_imu_markings
from .silk_artwork import SILK_CONFLICTS, relocate
from .storage import digest, read_json, write_json
from .design_rule_provenance import (
    classify_finding, finding_sources, measured_actual, numeric_finding, require_evaluated_limits)


DRC_COLLISIONS = frozenset(('shorting_items', 'clearance', 'pth_inside_courtyard', 'holes_co_located'))
DRC_MASK = 'solder_mask_bridge'
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
    """Footprint-local pad facts that a footprint move must leave unchanged.

    A quarter turn can report the unchanged relative angle as 360 degrees.
    """
    import pcbnew
    every = pcbnew.PADSTACK.ALL_LAYERS
    def xy(v):
        return [int(v.x), int(v.y)]
    return {fp.m_Uuid.AsString(): {
        'fpid': fp.GetFPIDAsString(), 'pads': sorted([
            [pad.m_Uuid.AsString(), pad.GetNumber(), pad.GetNetname(), int(pad.GetAttribute()),
             int(pad.Padstack().Mode()), int(pad.GetShape(every)), xy(pad.GetSize(every)),
             xy(pad.GetOffset(every)), xy(pad.GetDrillSize()), int(pad.GetDrillShape()),
             pad.GetRoundRectRadiusRatio(every), pad.GetChamferRectRatio(every),
             int(pad.GetChamferPositions(every)), pad.GetLayerSet().FmtHex(),
             xy(pad.GetFPRelativePosition()), round(pad.GetFPRelativeOrientation().AsDegrees(), 6) % 360,
             custom_pad_geometry(pad) if pad.GetShape(every) == pcbnew.PAD_SHAPE_CUSTOM else None]
            for pad in fp.Pads()])} for fp in board.GetFootprints()}


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
    if any(not isinstance(check, dict) or not isinstance(check.get('key'), str)
           or check['key'] in DRC_COLLISIONS | {DRC_MASK} | {'courtyards_overlap', 'courtyard_overlap'}
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


def _drc_item_states(path):
    """Bind collision identities to native saved geometry and footprint transforms."""
    import pcbnew
    board = pcbnew.LoadBoard(str(path))
    inventory = _pad_inventory(board)
    states = {}
    for fp in board.GetFootprints():
        owner = fp.m_Uuid.AsString()
        pose = (int(fp.GetPosition().x), int(fp.GetPosition().y),
                fp.GetOrientationDegrees(), int(fp.GetLayer()))
        fp.SetOrientationDegrees(0)
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        graphics = sorted(({'layer': int(item.GetLayer()), 'width_nm': int(item.GetWidth()),
                            'shape': primitive(item)} for item in fp.GraphicalItems()
                           if item.GetClass() == 'PCB_SHAPE'), key=lambda item: item['shape']['uuid'])
        geometry = json.dumps({'pads': inventory[owner], 'graphics': graphics}, sort_keys=True)
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
    return states


def _drc_comparison(before, after, before_states=None, after_states=None, sources=None):
    """Preserve rigid intra-footprint findings; moved cross-footprint collisions are new.

    Native geometry is supplied by the publication path, never by the caller's
    placement plan. Numeric findings bind to absolute saved geometry, including single-item findings.
    """
    if (before['ignored_checks'] != after['ignored_checks'] or
            before.get('included_severities') != after.get('included_severities')):
        raise ValueError('native baseline/candidate DRC evaluation settings differ')

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
    old = Counter(key(v, before_states) for v in before['violations'])
    remaining = Counter(key(v, after_states) for v in after['violations']) - old
    added = []
    for finding in after['violations']:
        identity = key(finding, after_states)
        if remaining[identity]:
            added.append(finding)
            remaining[identity] -= 1
    return {'added_findings': added,
            'blocking_findings': [v for v in after['violations'] if v['type'] == 'copper_outside_outline'] + [v for v in added if
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
    return [v for v in after['violations'] if v['type'] in DRC_COURTYARDS], comparison


def _apply_plan(board, plan, output):
    import pcbnew
    by_uuid = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    for placement in plan['placements']:
        fp = by_uuid[placement['uuid']]
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
    return facts, judgment


def _board_only(board):
    return frozenset(fp.GetReference() for fp in board.GetFootprints() if fp.IsBoardOnly())


def _evaluate_proposal(board, output, plan, netlist_bytes, initial_facts, source):
    import pcbnew
    pads = _pad_inventory(board)
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
    _apply_plan(board, plan, output)
    if _pad_inventory(pcbnew.LoadBoard(str(output))) != pads:
        raise ValueError('native saved board changed footprint pad geometry')
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
    if judgment.get('group_grading', {'status': 'pass'})['status'] != 'pass':
        raise ValueError('saved-board placement failed its declared placement groups')
    if judgment.get('locality_grading', {}).get('status', 'pass') not in ('pass', 'info'):
        raise ValueError('saved-board placement failed its declared electrical locality')
    if any(r['enforcement'] == 'hard' and r['status'] != 'pass' for r in judgment.get('block_rules', [])):
        raise ValueError('saved-board placement failed a hard block arrangement rule')
    from .silk_artwork import containment_witness
    return {'schema': 'agent-ee.placed-candidate/1', 'source_sha256': plan['board_sha256'],
            'containment': containment_witness(output),
            'board_sha256': digest(output), 'netlist_sha256': hashlib.sha256(netlist_bytes).hexdigest(),
            'status': 'placed', 'outcome': 'defect' if comparison['open_defects'] else 'info',
            'verdict': 'defect' if comparison['open_defects'] else 'info',
            'open_defects': comparison['open_defects'], 'min_courtyard_edge_mm': plan['min_courtyard_edge_mm'],
            'plan': plan, 'judgment': judgment,
            'native_courtyard_overlaps': overlaps, 'native_drc_comparison': comparison,
            'authority': 'disposable-unsealed-proposal'}


def _retain_failure(root, name, scratch, error):
    retained = Path(tempfile.mkdtemp(prefix=name + '-failure-', dir=root))
    names = ('candidate.kicad_pcb', 'placement-plan.json', 'placement-probe.json',
             'placement-judgment.json', 'placement-write-scope.json', 'native-drc.json', 'native-drc.stdout.txt',
             'native-drc.stderr.txt', 'native-drc-execution.json', 'native-drc-comparison.json',
             'candidate.kicad_pro', 'candidate.kicad_dru', 'pre-artwork-drc.json', 'placement-silkscreen.json')
    for name in names:
        path = scratch / name
        if path.is_file():
            target = 'failed-' + name if name.startswith('candidate.kicad_') else name
            shutil.copyfile(path, retained / target)
    if (scratch / 'baseline').is_dir():
        shutil.copytree(scratch / 'baseline', retained / 'baseline')
    write_json(retained / 'failure.json', {'schema': 'agent-ee.placement-failure/1',
               'execution': 'failed', 'outcome': 'unknown', 'status': 'not_placed', 'error': str(error)})
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


def _plan(facts, request, policy=None, netlist=None):
    policy = _effective_policy(request) if policy is None else policy
    if sum(key in request for key in ('targeted', 'targeted_plans', 'blocks')) > 1:
        raise ValueError('targeted, targeted_plans and blocks cannot be combined')
    if 'blocks' in request:
        # Candidates are packed and admitted against the captured request policy; the
        # effective policy adds only board obstacles, which arrangement enforces.
        from .block_arrangement import plan_from_request
        if netlist is None:
            raise ValueError('block arrangement needs the captured native netlist')
        return plan_from_request(facts, request['blocks'], _effective_policy(request), policy, netlist, _heartbeat)
    if 'targeted' in request:
        from .placement_plans import plan
        return plan(facts, policy, request['targeted'])
    if 'targeted_plans' in request:
        from .placement_plans import select
        return select(facts, policy, request['targeted_plans'])
    return pack(facts, policy, heartbeat=_heartbeat)


def place_candidate(source, netlist, destination, request):
    """Publish only a fully saved/judged proposal in a fresh authorized directory.

    Errors propagate and never publish a placed candidate. Typed packing failures
    return without creating a destination. Existing coordinator baseline, source
    allowlist and evidence-sealing checks remain the caller's responsibility.
    """
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
        preview = _plan(extract(source), request, policy)
        if preview['status'] != 'planned':
            return preview
    with writer_lock(destination):
        _authorization(source, destination, request['authorization'])
        facts = extract(source)
        board = _unrouted_board(source)
        _netlist_binding(netlist_bytes, facts, _board_only(board))
        policy = reserve_imu_markings(board, _effective_policy(request))
        plan = _plan(facts, request, policy, netlist)
        refusal = plan['status'] == 'planned' and _padstack_refusal(board)
        if refusal:
            return _failure(facts, policy, 'unsupported_geometry', 'unknown', plan['steps_used'], refusal)
        if plan['status'] != 'planned':
            return plan
        with tempfile.TemporaryDirectory(prefix='.placement-staging-', dir=root) as temporary:
            scratch = Path(temporary)
            output = scratch / 'candidate.kicad_pcb'
            write_json(scratch / 'placement-plan.json', plan)
            try:
                report = _evaluate_proposal(board, output, plan, netlist_bytes, facts, source)
                from .placement_adapter import write_scope
                protected = ([p['uuid'] for p in plan['placements'] if p['fixed']]
                             if targeted else ())
                scope = write_scope(source.read_bytes(), output.read_bytes(), protected)
                write_json(scratch / 'placement-write-scope.json', scope)
                if scope['status'] != 'allowed':
                    raise ValueError(f"native saved placement exceeded write scope: {scope['violations']}")
                report['write_scope'] = scope
                if 'block_arrangement' in plan:
                    # Review input naming every placed block's identity, class and enforcement,
                    # bound to the saved board and graded on it (AGENTEE-355).
                    from .block_arrangement import provenance_artifact
                    write_json(scratch / 'block-provenance.json', provenance_artifact(
                        plan, report['board_sha256'], report['judgment']['block_rules'],
                        plan_sha256=digest(scratch / 'placement-plan.json')))
                    report['block_provenance_sha256'] = digest(scratch / 'block-provenance.json')
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
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('netlist')
    parser.add_argument('--request', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--result', required=True)
    args = parser.parse_args(argv)
    request = read_json(args.request)
    root = Path(request['authorization']['writable_root'])
    result_path = Path(args.result).absolute()
    if result_path.parent != root or result_path.exists() or result_path.is_symlink():
        raise ValueError('result must be a fresh file directly inside authorized scratch root')
    report = place_candidate(args.board, args.netlist, args.destination, request)
    with result_path.open('x') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0 if report['status'] == 'placed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
