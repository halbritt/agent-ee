"""Bounded C3.2 -> U2.3 front-copper seed, inside accepted placement only.

No placement, routing controller, or acceptance decision lives here. The caller
owns the placement clock/CANCEL and invokes route() on the returned directory.
"""
from collections import Counter
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import uuid

from .placement_job import JobRefusal, JobStopped, _check_stop, drc, verify_rule_loading
from .dc_rail import NONCONDUCTIVE_IGNORES
from .net_constraints import rule_forms, template_rules
from .storage import canonical, write_json
from .symbol_library import children, nanometres, parse_sexpr

FILES = ('board.kicad_pcb', 'board.kicad_pro', 'board.kicad_dru')
WIDTH_NM = 250_000
CLEARANCE_MM = 0.2


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _capture(directory):
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise JobRefusal('local return: source directory aliases are unsupported')
    captured = {}
    for name in FILES:
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise JobRefusal(f'local return: source alias/non-file {name}')
        captured[name] = path.read_bytes() if path.exists() else None
    if any(captured[name] is None for name in FILES[:2]):
        raise JobRefusal('local return: board and project are required')
    return captured


def _identity(captured):
    return {name: None if data is None else _sha(data) for name, data in captured.items()}


def _endpoints(path, *, seed=None):
    """One read-only pcbnew load; identities and coordinates are never imported."""
    import pcbnew as k
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('local return: native board load failed')
    result = []
    for ref, number in (('C3', '2'), ('U2', '3')):
        fps = [fp for fp in board.GetFootprints() if fp.GetReference() == ref]
        if len(fps) != 1:
            raise JobRefusal(f'local return: missing/ambiguous footprint {ref}')
        fp = fps[0]
        pads = [p for p in fp.Pads() if p.GetNumber() == number]
        if len(pads) != 1:
            raise JobRefusal(f'local return: missing/ambiguous pad {ref}.{number}')
        pad = pads[0]
        copper = [l for l in board.GetEnabledLayers().CuStack() if pad.IsOnLayer(l) and pad.FlashLayer(l)]
        if fp.GetLayer() != k.F_Cu or copper != [k.F_Cu] or pad.GetAttribute() != k.PAD_ATTRIB_SMD:
            raise JobRefusal(f'local return: {ref}.{number} must be a front-only SMD pad')
        if pad.GetNetname() != 'GND' or pad.GetNetCode() <= 0:
            raise JobRefusal(f'local return: {ref}.{number} must have actual GND net')
        pos = pad.GetPosition()
        result.append(dict(ref=ref, number=number, footprint_uuid=fp.m_Uuid.AsString(),
                           pad_uuid=pad.m_Uuid.AsString(), net='GND', net_code=pad.GetNetCode(),
                           position_nm=[int(pos.x), int(pos.y)], layer='F.Cu'))
    identities = [p[key] for p in result for key in ('footprint_uuid', 'pad_uuid')]
    if len(set(identities)) != 4 or any(not value for value in identities):
        raise JobRefusal('local return: missing/ambiguous endpoint native identities')
    if result[0]['net_code'] != result[1]['net_code']:
        raise JobRefusal('local return: endpoint net/identity mismatch')
    if result[0]['position_nm'] == result[1]['position_nm']:
        raise JobRefusal('local return: coincident endpoints unsupported')
    if seed is not None:
        points, identities = seed
        tracks = list(board.GetTracks())
        for identity, a, b in zip(identities, points, points[1:]):
            found = [t for t in tracks if t.m_Uuid.AsString() == identity]
            if len(found) != 1:
                raise JobRefusal('local return: saved native seed identity missing/ambiguous')
            track = found[0]
            if (track.GetClass() != 'PCB_TRACK' or track.GetLayer() != k.F_Cu
                    or track.GetNetname() != 'GND' or track.GetNetCode() != result[0]['net_code']
                    or int(track.GetWidth()) != WIDTH_NM
                    or [int(track.GetStart().x), int(track.GetStart().y)] != list(a)
                    or [int(track.GetEnd().x), int(track.GetEnd().y)] != list(b)):
                raise JobRefusal('local return: saved native seed geometry/net changed')
    return result


def _paths(a, b):
    """At most five endpoint-derived paths. No search, obstacles, or old poses."""
    x, y = a
    u, v = b
    dx, dy = u - x, v - y
    d = min(abs(dx), abs(dy))
    sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
    proposals = ((a, b), (a, (u, y), b), (a, (x, v), b),
                 (a, (x + sx*d, y + sy*d), b), (a, (u - sx*d, v - sy*d), b))
    result = []
    for proposal in proposals:
        path = []
        for p in proposal:
            p = tuple(p)
            if not path or path[-1] != p:
                path.append(p)
        if path not in result:
            result.append(path)
    return result


def _mm(nm):
    sign = '-' if nm < 0 else ''
    whole, part = divmod(abs(nm), 1_000_000)
    return f'{sign}{whole}.{part:06d}'


def _seed(data, path):
    tree = parse_sexpr(data)
    if tree[0] != 'kicad_pcb' or children(tree, 'version') != [['version', '20260206']]:
        raise JobRefusal('local return: requires pinned KiCad 10 saved-board version 20260206')
    # Root closing token is found only after the complete shared parser accepted
    # the document. Leave every original byte, including comments, untouched.
    end = data.rstrip().rfind(b')')
    ids, segments = [], []
    for a, b in zip(path, path[1:]):
        identity = str(uuid.uuid4())
        ids.append(identity)
        segments.append(f'\n (segment (start {_mm(a[0])} {_mm(a[1])}) '
                        f'(end {_mm(b[0])} {_mm(b[1])}) (width 0.25) '
                        f'(layer "F.Cu") (net "GND") (uuid "{identity}"))\n'.encode())
    output = data[:end] + b''.join(segments) + data[end:]
    after = parse_sexpr(output)
    added = after[-len(ids):]
    if after[:-len(ids)] != tree:
        raise JobRefusal('local return: write scope changed original board nodes')
    if any(n[0] != 'segment' for n in added):
        raise JobRefusal('local return: write scope is not segments only')
    return output, ids


def _no_suppression(captured):
    settings = json.loads(captured['board.kicad_pro'])['board']['design_settings']
    if (not isinstance(settings.get('rule_severities', {}), dict)
            or settings.get('rule_severities', {})
            or not isinstance(settings.get('drc_exclusions', []), list)
            or settings.get('drc_exclusions', [])):
        raise JobRefusal('local return: authored DRC severity overrides/exclusions unsupported')
    raw = captured['board.kicad_dru']
    if raw is not None:
        template_rules(raw, allow_generated=True)
        if any(children(node, 'severity') for node in rule_forms(raw, native=True)[1:]):
            raise JobRefusal('local return: authored custom-rule severity overrides unsupported')


def _legal(report):
    if (not isinstance(report, dict)
            or report.get('$schema') != 'https://schemas.kicad.org/drc.v1.json'
            or report.get('kicad_version') != '10.0.7'
            or report.get('coordinate_units') != 'mm'):
        raise JobRefusal('local return: unknown native DRC schema/version/units')
    severities = report.get('included_severities')
    if (not isinstance(severities, list) or any(not isinstance(s, str) for s in severities)
            or len(severities) != 3 or set(severities) != {'error', 'warning', 'exclusion'}):
        raise JobRefusal('local return: incomplete native DRC severity coverage')
    ignored = report.get('ignored_checks')
    if (not isinstance(ignored, list) or any(not isinstance(c, dict)
            or set(c) != {'key', 'description'} or not isinstance(c['key'], str)
            or not isinstance(c['description'], str) or not c['description'].strip()
            for c in ignored)):
        raise JobRefusal('local return: malformed/missing native DRC ignored-check metadata')
    keys = [c['key'] for c in ignored]
    if len(keys) != len(set(keys)) or not set(keys) <= NONCONDUCTIVE_IGNORES:
        raise JobRefusal('local return: required/unknown native DRC check ignored')
    # These defaults concern unchanged footprint metadata, via centering and
    # tuning profiles; this operation adds only plain pad-centre trace segments.
    # They cannot suppress clearance/width/edge/short checks. See applicability
    # in docs/acquisition-local-return.md; no full placement/parity pass implied.
    for category in ('violations', 'unconnected_items', 'schematic_parity'):
        records = report.get(category)
        if not isinstance(records, list):
            raise JobRefusal('local return: unknown native DRC evaluations')
        for finding in records:
            if (not isinstance(finding, dict) or not isinstance(finding.get('type'), str)
                    or not finding['type'].strip() or not isinstance(finding.get('description'), str)
                    or finding.get('severity') not in ('error', 'warning', 'exclusion')
                    or not isinstance(finding.get('items'), list)
                    or any(not isinstance(item, dict) or not isinstance(item.get('uuid'), str)
                           or not item['uuid'].strip() for item in finding['items'])):
                raise JobRefusal('local return: malformed native DRC finding')
            if category == 'unconnected_items' and finding['type'] != 'unconnected_items':
                raise JobRefusal('local return: non-open finding in native pre-route open inventory')
            if finding['severity'] == 'exclusion':
                raise JobRefusal('local return: excluded native DRC finding')
    # The fresh unseeded baseline admits only library metadata warnings. Trials
    # must also match that baseline's complete record multiset in produce().
    return (all(v['type'] == 'lib_footprint_issues' and v['severity'] == 'warning'
                for v in report['violations']) and not report['schematic_parity'])


def _library_context(path):
    import pcbnew as k
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('local return: native library context load failed')
    result = {}
    for fp in board.GetFootprints():
        identity = fp.m_Uuid.AsString()
        if identity in result:
            raise JobRefusal('local return: ambiguous original footprint identity')
        point = fp.GetPosition()
        result[identity] = dict(description='Footprint ' + fp.GetReference(),
            position_nm=[int(point.x), int(point.y)], library=fp.GetFPID().GetLibNickname())
    return result


def _bound_library_warnings(report, context):
    for warning in report['violations']:
        match = re.fullmatch(r"The current configuration does not include the footprint library '([^'\r\n]+)'",
                             warning['description'])
        items = warning['items']
        if match is None or len(items) != 1:
            raise JobRefusal('local return: unrecognized baseline library diagnostic')
        item = items[0]
        original = context.get(item['uuid'])
        position = item.get('pos')
        if (original is None or item.get('description') != original['description']
                or match[1] != original['library'] or not isinstance(position, dict)
                or set(position) != {'x', 'y'}
                or [nanometres(str(position[k])) for k in ('x', 'y')] != original['position_nm']):
            raise JobRefusal('local return: baseline library warning is not bound to original footprint')


def _raw_report(directory, report, execution):
    path = directory / 'drc.json'
    if not path.is_file():
        raise JobRefusal('local return: native DRC raw report missing')
    raw = path.read_bytes()
    sha = _sha(raw)
    if json.loads(raw) != report or execution.get('report_sha256') != sha or execution.get('exit_code') != 0:
        raise JobRefusal('local return: native DRC report/receipt identity mismatch')
    return sha


def _warnings(report):
    return Counter(canonical(v) for v in report['violations'])


def produce(candidate_dir, destination, *, expected_inputs, clock, cancel_dir=None):
    """Return a complete checked seed directory; refuse without adopting a trial.

    expected_inputs must bind all FILES to SHA256 (None for absent rules), from
    the accepted placement input custody. destination must be new and outside
    candidate_dir. Every attempted board/report remains there, including failure.
    The existing placement clock and CANCEL propagate without an extra grant.
    """
    _check_stop(clock, cancel_dir)
    source = Path(candidate_dir).absolute()
    destination = Path(destination).absolute()
    if destination.resolve() != destination or destination.exists() or destination.is_relative_to(source):
        raise JobRefusal('local return: output exists/aliases/overlaps source')
    captured = _capture(source)
    identities = _identity(captured)
    if identities != expected_inputs:
        raise JobRefusal('local return: source/input identity mismatch')
    helper = Path(__file__)
    helper_hash = _sha(helper.read_bytes())
    destination.mkdir(parents=True)
    record = dict(schema='agent-ee.acquisition-local-return/1', status='running',
                  inputs=identities, helper_sha256=helper_hash, trials=[],
                  width_nm=WIDTH_NM, clearance_mm=CLEARANCE_MM,
                  acceptance_assessed=False, final_geometry_required='fresh supply + return <= 10 mm',
                  router_copper_policy='preserve_existing', owned_copper_retries=False)

    def custody():
        _check_stop(clock, cancel_dir)
        if _identity(_capture(source)) != identities or _sha(helper.read_bytes()) != helper_hash:
            raise JobRefusal('local return: source/input drift')

    try:
        _no_suppression(captured)
        project = json.loads(captured['board.kicad_pro'])
        rules = project['board']['design_settings']['rules']
        if rules['min_clearance'] != CLEARANCE_MM or not 0 < rules['min_track_width'] <= WIDTH_NM / 1e6:
            raise JobRefusal('local return: requires 0.2 mm native minimum clearance and <=0.25 mm minimum width')
        endpoints = _endpoints(source / FILES[0])
        record['endpoints'] = endpoints
        custody()
        if captured['board.kicad_dru'] is not None:
            proof = destination / 'rule-loading'
            record['rule_loading'] = verify_rule_loading(source / FILES[0], proof, clock=clock, cancel_dir=cancel_dir)
            custody()
        baseline_dir = destination / 'baseline'
        baseline_dir.mkdir()
        for name, data in captured.items():
            if data is not None:
                (baseline_dir / name).write_bytes(data)
        record['baseline'] = dict(status='checking', inputs=identities, board_sha256=identities[FILES[0]])
        write_json(destination / 'record.json', record)
        baseline, baseline_execution = drc(baseline_dir / FILES[0], baseline_dir,
                                           clock=clock, cancel_dir=cancel_dir)
        baseline_sha = _raw_report(baseline_dir, baseline, baseline_execution)
        if not _legal(baseline):
            raise JobRefusal('local return: fresh baseline has forbidden native DRC findings')
        context = _library_context(source / FILES[0])
        _bound_library_warnings(baseline, context)
        warnings = _warnings(baseline)
        record['baseline'].update(status='checked', execution=baseline_execution,
            report_sha256=baseline_sha, library_warning_count=sum(warnings.values()))
        custody()
        if _capture(baseline_dir) != captured:
            raise JobRefusal('local return: baseline/input drift during native checks')
        for index, path in enumerate(_paths(*(p['position_nm'] for p in endpoints))):
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha((baseline_dir / 'drc.json').read_bytes()) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift')
            trial_dir = destination / f'trial-{index + 1}'
            trial_dir.mkdir()
            board, ids = _seed(captured[FILES[0]], path)
            for name, data in captured.items():
                if data is not None:
                    (trial_dir / name).write_bytes(board if name == FILES[0] else data)
            trial = dict(path_nm=path, segment_uuids=ids, board_sha256=_sha(board), status='checking',
                         trace_length_mm=sum(math.dist(a, b) for a, b in zip(path, path[1:])) / 1e6)
            record['trials'].append(trial)
            write_json(destination / 'record.json', record)
            if _endpoints(trial_dir / FILES[0], seed=(path, ids)) != endpoints:
                raise JobRefusal('local return: saved native endpoint identity/geometry changed')
            report, execution = drc(trial_dir / FILES[0], trial_dir, clock=clock, cancel_dir=cancel_dir)
            trial['execution'] = execution
            trial['report_sha256'] = _raw_report(trial_dir, report, execution)
            legal = _legal(report)
            current_warnings = _warnings(report)
            if current_warnings != warnings:
                legal = False
                trial['warning_difference'] = dict(
                    missing=[json.loads(v) for v in (warnings - current_warnings).elements()],
                    added=[json.loads(v) for v in (current_warnings - warnings).elements()])
            trial['coverage'] = {k: report[k] for k in
                ('$schema', 'kicad_version', 'included_severities', 'ignored_checks')}
            trial['unconnected_count'] = len(report['unconnected_items'])
            trial['status'] = 'clearance_pass' if legal else 'rejected'
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha((baseline_dir / 'drc.json').read_bytes()) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift')
            expected_trial = {**captured, FILES[0]: board}
            if _capture(trial_dir) != expected_trial:
                raise JobRefusal('local return: trial/input drift during native checks')
            if not legal:
                write_json(destination / 'record.json', record)
                continue
            pending = destination / '.accepted'
            shutil.copytree(trial_dir, pending)
            custody()
            if _capture(pending) != expected_trial:
                raise JobRefusal('local return: adoption/input drift')
            record.update(status='seeded', selected_trial=index + 1, output=str(destination / 'accepted'),
                          output_inputs=_identity(expected_trial))
            write_json(destination / 'record.json', record)
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha((baseline_dir / 'drc.json').read_bytes()) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift before adoption')
            pending.rename(destination / 'accepted')
            return record
        raise JobRefusal('local return: all endpoint-derived proposals failed native clearance; see record.json')
    except (JobRefusal, JobStopped, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='stopped' if isinstance(exc, JobStopped) else 'refused', reason=str(exc))
        write_json(destination / 'record.json', record)
        raise
