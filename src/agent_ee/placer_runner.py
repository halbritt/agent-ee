"""Single-writer disposable-board placement; intended for networkless sandbox use."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from .board_net_names import from_xml
from .locking import writer_lock
from .placement import judge
from .placement_probe import extract
from .placer import _failure, pack
from .silkscreen import reserve_imu_markings
from .storage import digest, read_json, write_json


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
             xy(pad.GetFPRelativePosition()), round(pad.GetFPRelativeOrientation().AsDegrees(), 6) % 360]
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
    if any(not isinstance(check, dict) or check.get('key') in ('courtyards_overlap', 'courtyard_overlap')
           for check in report['ignored_checks']):
        raise ValueError('native courtyard evaluation unavailable or explicitly ignored')
    violations = report['violations']
    if any(not isinstance(v, dict) or not isinstance(v.get('type'), str) for v in violations):
        raise ValueError('native DRC contains malformed violations')
    # Excluded courtyard findings still prevent a placed result.
    return [v for v in violations if v.get('type') in ('courtyards_overlap', 'malformed_courtyard', 'missing_courtyard')]


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
    return facts, judgment


def _board_only(board):
    return frozenset(fp.GetReference() for fp in board.GetFootprints() if fp.IsBoardOnly())


def _evaluate_proposal(board, output, plan, netlist_bytes):
    import pcbnew
    pads = _pad_inventory(board)
    _apply_plan(board, plan, output)
    if _pad_inventory(pcbnew.LoadBoard(str(output))) != pads:
        raise ValueError('native saved board changed footprint pad geometry')
    saved, judgment = _saved_judgment(output, plan)
    write_json(output.parent / 'placement-probe.json', saved)
    write_json(output.parent / 'placement-judgment.json', judgment)
    _netlist_binding(netlist_bytes, saved, _board_only(board))
    overlaps = _native_drc(output, output.parent)
    if judgment['verdict'] != 'pass' or overlaps:
        raise ValueError('saved-board placement failed polygon judge or native courtyard DRC')
    if judgment.get('group_grading', {'status': 'pass'})['status'] != 'pass':
        raise ValueError('saved-board placement failed its declared placement groups')
    return {'schema': 'agent-ee.placed-candidate/1', 'source_sha256': plan['board_sha256'],
            'board_sha256': digest(output), 'netlist_sha256': hashlib.sha256(netlist_bytes).hexdigest(),
            'status': 'placed', 'outcome': 'info', 'min_courtyard_edge_mm': plan['min_courtyard_edge_mm'],
            'plan': plan, 'judgment': judgment,
            'native_courtyard_overlaps': overlaps, 'authority': 'disposable-unsealed-proposal'}


def _retain_failure(root, name, scratch, error):
    retained = Path(tempfile.mkdtemp(prefix=name + '-failure-', dir=root))
    names = ('candidate.kicad_pcb', 'placement-plan.json', 'placement-probe.json',
             'placement-judgment.json', 'placement-write-scope.json', 'native-drc.json', 'native-drc.stdout.txt',
             'native-drc.stderr.txt', 'native-drc-execution.json')
    for name in names:
        path = scratch / name
        if path.is_file():
            target = 'failed-candidate.kicad_pcb' if name == 'candidate.kicad_pcb' else name
            shutil.copyfile(path, retained / target)
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
    if 'targeted' in request:
        from .placement_plans import plan as targeted_plan
        # Refuse before even creating a lease file. A successful plan is rebuilt
        # under the writer lease below; caller-provided plans are never trusted.
        preview = targeted_plan(extract(source), _effective_policy(request), request['targeted'])
        if preview['status'] != 'planned':
            return preview
    with writer_lock(destination):
        _authorization(source, destination, request['authorization'])
        facts = extract(source)
        board = _unrouted_board(source)
        _netlist_binding(netlist_bytes, facts, _board_only(board))
        policy = reserve_imu_markings(board, _effective_policy(request))
        if 'targeted' in request:
            plan = targeted_plan(facts, policy, request['targeted'])
        else:
            plan = pack(facts, policy)
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
                report = _evaluate_proposal(board, output, plan, netlist_bytes)
                from .placement_adapter import write_scope
                protected = ([p['uuid'] for p in plan['placements'] if p['fixed']]
                             if 'targeted' in request else ())
                scope = write_scope(source.read_bytes(), output.read_bytes(), protected)
                write_json(scratch / 'placement-write-scope.json', scope)
                if scope['status'] != 'allowed':
                    raise ValueError(f"native saved placement exceeded write scope: {scope['violations']}")
                report['write_scope'] = scope
                if digest(source) != source_hash or netlist.read_bytes() != netlist_bytes:
                    raise ValueError('captured placement inputs changed during execution')
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
