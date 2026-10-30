"""Bounded acquisition loop copper, inside accepted placement only.

No placement, routing controller, or acceptance decision lives here. The caller
owns the placement clock/CANCEL and invokes route() on the returned directory.
"""
from collections import Counter
import hashlib
import heapq
import json
import math
import os
import re
from pathlib import Path
import shutil
import stat
import uuid

from .placement_job import JobRefusal, JobStopped, _check_stop, drc, verify_rule_loading
from .dc_rail import NONCONDUCTIVE_IGNORES
from .net_constraints import rule_forms, template_rules
from .storage import canonical, write_json
from .symbol_library import children, nanometres, parse_sexpr
from . import acquisition_local_geometry as geometry

FILES = ('board.kicad_pcb', 'board.kicad_pro', 'board.kicad_dru')
WIDTH_NM = 250_000
CLEARANCE_MM = 0.2
DEFERRED_ARTWORK = frozenset(('silk_overlap', 'silk_over_copper'))
# Route the individual signal/supply legs before the shared GND returns. A
# crossbar joining GND pads first can close access to an adjacent signal pad.
CONNECTIONS = {
    'filter0-signal': ((('C4', '1'), ('U2', '4')), '/ANALOG0'),
    'adc-supply': ((('C3', '1'), ('U2', '8')), '/V3V3'),
    'output-supply': ((('C2', '1'), ('U1', '5')), '/V3V3'),
    'filter1-signal': ((('C5', '1'), ('U2', '5')), '/ANALOG1'),
    'adc-return': ((('C3', '2'), ('U2', '3')), 'GND'),
    'output-return': ((('C2', '2'), ('U1', '2')), 'GND'),
    'filter0-return': ((('C4', '2'), ('U2', '3')), 'GND'),
    'filter1-return': ((('C5', '2'), ('U2', '3')), 'GND'),
}
LOOP_LIMITS = {'adc':10., 'output':10., 'filter0':12., 'filter1':12.}


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


def _endpoints(path, *, seed=None, connection='adc-return'):
    """One read-only pcbnew load; identities and coordinates are never imported."""
    import pcbnew as k
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('local return: native board load failed')
    if connection not in CONNECTIONS:
        raise JobRefusal('local return: unknown acquisition connection')
    terminals, net = CONNECTIONS[connection]
    result = []
    for ref, number in terminals:
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
        if pad.GetNetname() != net or pad.GetNetCode() <= 0:
            raise JobRefusal(f'local return: {ref}.{number} must have actual {net} net')
        pos = pad.GetPosition()
        result.append(dict(ref=ref, number=number, footprint_uuid=fp.m_Uuid.AsString(),
                           pad_uuid=pad.m_Uuid.AsString(), net=net, net_code=pad.GetNetCode(),
                           position_nm=[int(pos.x), int(pos.y)], layer='F.Cu'))
    identities = [p[key] for p in result for key in ('footprint_uuid', 'pad_uuid')]
    if len(set(identities)) != 4 or any(not value for value in identities):
        raise JobRefusal('local return: missing/ambiguous endpoint native identities')
    if result[0]['net_code'] != result[1]['net_code']:
        raise JobRefusal('local return: endpoint net/identity mismatch')
    if result[0]['position_nm'] == result[1]['position_nm']:
        raise JobRefusal('local return: coincident endpoints unsupported')
    if seed is not None:
        if isinstance(seed,dict):
            points,identities=seed['path_nm'],seed['segment_uuids']
            layers=seed['layers'];vias=seed.get('vias',[])
        else:
            points,identities=seed;layers=['F.Cu']*len(identities);vias=[]
        if len(points)!=len(identities)+1 or len(layers)!=len(identities):
            raise JobRefusal('local return: seed segment coverage changed')
        tracks = list(board.GetTracks())
        for identity, a, b, layer in zip(identities, points, points[1:],layers):
            found = [t for t in tracks if t.m_Uuid.AsString() == identity]
            if len(found) != 1:
                raise JobRefusal('local return: saved native seed identity missing/ambiguous')
            track = found[0]
            if (track.GetClass() != 'PCB_TRACK' or track.GetLayer() != getattr(k,layer.replace('.','_'))
                    or track.GetNetname() != net or track.GetNetCode() != result[0]['net_code']
                    or int(track.GetWidth()) != WIDTH_NM
                    or sorted(((int(track.GetStart().x), int(track.GetStart().y)),
                               (int(track.GetEnd().x), int(track.GetEnd().y))))
                       != sorted((tuple(a),tuple(b)))):
                raise JobRefusal('local return: saved native seed geometry/net changed')
        for row in vias:
            found=[t for t in tracks if t.m_Uuid.AsString()==row['uuid']]
            if len(found)!=1:raise JobRefusal('local return: native seed via identity missing/ambiguous')
            via=found[0]
            if (via.GetClass()!='PCB_VIA' or via.GetNetname()!=net
                    or via.GetNetCode()!=result[0]['net_code']
                    or via.GetViaType()!=k.VIATYPE_THROUGH
                    or via.GetWidth(k.PADSTACK.ALL_LAYERS)!=600_000 or via.GetDrillValue()!=300_000
                    or via.TopLayer()!=k.F_Cu or via.BottomLayer()!=k.B_Cu
                    or [int(via.GetPosition().x),int(via.GetPosition().y)]!=list(row['at_nm'])):
                raise JobRefusal('local return: native seed via geometry/net changed')
        if vias and int(board.GetDesignSettings().GetBoardThickness())!=seed['board_thickness_nm']:
            raise JobRefusal('local return: seed via vertical length changed')
    return result


def _paths(a, b):
    """Five paths derived only from the endpoints."""
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


def _obstacle_path(a, b, boxes, *, clock, cancel_dir):
    """Shortest visibility path around expanded copper rectangles, at most 12 mm.

    This proposes one front-copper seed. Only the subsequent full native DRC
    can establish clearance; the rectangles are a conservative search model.
    """
    a, b = tuple(a), tuple(b)
    if len(boxes)>1024:
        raise JobRefusal('local return: local obstacle population exceeds 1024')
    limit = 12_000_000
    def inside(p, box):
        x, y, u, v = box
        return x < p[0] < u and y < p[1] < v
    def crosses(p, q, box):
        low, high = 0.0, 1.0
        for origin, end, left, right in zip(p, q, box[:2], box[2:]):
            delta = end - origin
            if not delta:
                if not left < origin < right:
                    return False
            else:
                start, stop = sorted(((left-origin)/delta, (right-origin)/delta))
                low, high = max(low, start), min(high, stop)
        return low < high
    _check_stop(clock, cancel_dir)
    if any(inside(p, box) for p in (a, b) for box in boxes):
        return None
    nodes = {a, b}
    for x, y, u, v in boxes:
        _check_stop(clock, cancel_dir)
        for point in ((x,y), (u,y), (u,v), (x,v)):
            _check_stop(clock,cancel_dir)
            if math.dist(a,point)+math.dist(point,b) <= limit and not any(inside(point,r) for r in boxes):
                nodes.add(point)
            if len(nodes)>512:
                raise JobRefusal('local return: local obstacle search exceeds 512 vertices')
    nodes = sorted(nodes)
    queue, best, previous = [(0.0,a)], {a:0.0}, {}
    while queue:
        _check_stop(clock,cancel_dir)
        length, point = heapq.heappop(queue)
        if length != best[point]:
            continue
        if point == b:
            path = [b]
            while path[-1] != a:
                path.append(previous[path[-1]])
            return list(reversed(path))
        for other in nodes:
            _check_stop(clock,cancel_dir)
            distance = length + math.dist(point,other)
            if (other == point or distance >= best.get(other,math.inf)
                    or distance+math.dist(other,b) > limit
                    or any(crosses(point,other,box) for box in boxes)):
                continue
            best[other], previous[other] = distance, point
            heapq.heappush(queue,(distance,other))
    return None


def _candidate_paths(source, endpoints, *, obstacle_search, clock, cancel_dir):
    a, b = [p['position_nm'] for p in endpoints]
    paths = _paths(a,b)
    if not obstacle_search:
        return paths
    import pcbnew as k
    board = k.LoadBoard(str(source))
    if board is None:
        raise JobRefusal('local return: obstacle board load failed')
    net = endpoints[0]['net']
    items = [p for fp in board.GetFootprints() for p in fp.Pads()]
    items += list(board.GetTracks()) + list(board.GetDrawings())
    if len(items)>4096:
        raise JobRefusal('local return: native obstacle population exceeds 4096')
    if any(z.GetLayer()==k.F_Cu and z.GetNetname()!=net for z in board.Zones()):
        raise JobRefusal('local return: foreign front-copper zones unsupported in local search')
    margin = WIDTH_NM//2 + round(CLEARANCE_MM*1e6) + 25_000
    boxes = []
    for item in items:
        _check_stop(clock,cancel_dir)
        if not item.IsOnLayer(k.F_Cu) or (hasattr(item,'GetNetname') and item.GetNetname()==net):
            continue
        box = item.GetBoundingBox()
        boxes.append((int(box.GetLeft())-margin,int(box.GetTop())-margin,
                      int(box.GetRight())+margin,int(box.GetBottom())+margin))
    path = _obstacle_path(a,b,boxes,clock=clock,cancel_dir=cancel_dir)
    if path is not None and path not in paths:
        paths.append(path)
    # A merely legal L-shaped leg can close a neighbouring signal corridor.
    # Try the shortest proposals first; every proposal still needs native DRC.
    return sorted(paths,key=lambda p:sum(math.dist(x,y) for x,y in zip(p,p[1:])))


def _seed(data, path, *, net='GND', layers=None):
    if net not in {value[1] for value in CONNECTIONS.values()}:
        raise JobRefusal('local return: unsupported seed net')
    tree = parse_sexpr(data)
    if tree[0] != 'kicad_pcb' or children(tree, 'version') != [['version', '20260206']]:
        raise JobRefusal('local return: requires pinned KiCad 10 saved-board version 20260206')
    # Root closing token is found only after the complete shared parser accepted
    # the document. Leave every original byte, including comments, untouched.
    end = data.rstrip().rfind(b')')
    ids, segments = [], []
    layers=layers if layers is not None else ['F.Cu']*(len(path)-1)
    if len(layers)!=len(path)-1 or any(layer not in ('F.Cu','B.Cu') for layer in layers):
        raise JobRefusal('local return: unsupported seed layer coverage')
    existing = {}
    for node in children(tree, 'segment'):
        if (children(node,'net') != [['net',net]]
                or nanometres(children(node,'width')[0][1]) != WIDTH_NM):
            continue
        points = tuple(sorted(tuple(nanometres(v) for v in children(node,key)[0][1:3])
                              for key in ('start','end')))
        existing.setdefault((children(node,'layer')[0][1],points),children(node,'uuid')[0][1])
    for a, b, layer in zip(path, path[1:],layers):
        key = (layer,tuple(sorted((tuple(a),tuple(b)))))
        if key in existing:
            ids.append(existing[key])
            continue
        identity = str(uuid.uuid4())
        ids.append(identity)
        existing[key] = identity
        segments.append(f'\n (segment (start {_mm(a[0])} {_mm(a[1])}) '
                        f'(end {_mm(b[0])} {_mm(b[1])}) (width 0.25) '
                        f'(layer "{layer}") (net "{net}") (uuid "{identity}"))\n'.encode())
    output = data[:end] + b''.join(segments) + data[end:]
    after = parse_sexpr(output)
    count = len(segments)
    added = after[-count:] if count else []
    if (after[:-count] if count else after) != tree:
        raise JobRefusal('local return: write scope changed original board nodes')
    if any(n[0] != 'segment' for n in added):
        raise JobRefusal('local return: write scope is not segments only')
    return output, ids


def _seed_vias(data,centres,net):
    if len(centres)!=2 or net!='/ANALOG0':
        raise JobRefusal('local loops: unsupported via population/net')
    tree=parse_sexpr(data);nodes=[];rows=[]
    for point in centres:
        identity=str(uuid.uuid4());rows.append(dict(uuid=identity,at_nm=list(point)))
        nodes.append(f'\n (via (at {_mm(point[0])} {_mm(point[1])}) (size 0.6) (drill 0.3) '
                     f'(layers "F.Cu" "B.Cu") (net "{net}") (uuid "{identity}"))\n'.encode())
    end=data.rstrip().rfind(b')');output=data[:end]+b''.join(nodes)+data[end:]
    after=parse_sexpr(output)
    if after[:-2]!=tree or any(n[0]!='via' for n in after[-2:]):
        raise JobRefusal('local loops: via write scope changed original nodes')
    return output,rows


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
    # tuning profiles. Native seed verification binds plain segments and the
    # two declared through-vias, including exact shared centre coordinates.
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
    return (all(v['type'] in DEFERRED_ARTWORK | {'lib_footprint_issues'} and v['severity'] == 'warning'
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
            position_nm=[int(point.x), int(point.y)], library=str(fp.GetFPID().GetLibNickname()))
    return result


def _bound_library_warnings(report, context):
    for warning in report['violations']:
        if warning['type'] in DEFERRED_ARTWORK:
            continue
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


def _report_bytes(directory):
    path = directory / 'drc.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise JobRefusal('local return: native DRC report must be an unaliased regular file')
            raw = stream.read()
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            stamp = tuple(getattr(before, key) for key in fields)
            if any(tuple(getattr(s, key) for key in fields) != stamp for s in
                   (os.fstat(stream.fileno()), path.stat(follow_symlinks=False))):
                raise JobRefusal('local return: native DRC report changed during read')
            return raw
    except OSError as exc:
        raise JobRefusal('local return: native DRC report missing or aliased') from exc


def _raw_report(directory, report, execution):
    raw = _report_bytes(directory)
    sha = _sha(raw)
    if json.loads(raw) != report or execution.get('report_sha256') != sha or execution.get('exit_code') != 0:
        raise JobRefusal('local return: native DRC report/receipt identity mismatch')
    return sha


def _warnings(report):
    return Counter(canonical(v) for v in report['violations'] if v['type'] == 'lib_footprint_issues')


def _artwork(report):
    return [v for v in report['violations'] if v['type'] in DEFERRED_ARTWORK]


def produce(candidate_dir, destination, *, expected_inputs, clock, cancel_dir=None):
    """Original single ADC return operation; retained callers keep five proposals."""
    return _produce(candidate_dir, destination, expected_inputs=expected_inputs,
                    clock=clock, cancel_dir=cancel_dir, connection='adc-return', obstacle_search=False)


def _produce(candidate_dir, destination, *, expected_inputs, clock, cancel_dir, connection, obstacle_search,
             max_length_mm=None, two_layer=False):
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
    geometry_hash = _sha(Path(geometry.__file__).read_bytes())
    destination.mkdir(parents=True)
    record = dict(schema='agent-ee.acquisition-local-return/1', status='running',
                  inputs=identities, helper_sha256=helper_hash, trials=[],
                  connection=connection,
                  geometry_helper_sha256=geometry_hash, max_length_mm=max_length_mm,
                  width_nm=WIDTH_NM, clearance_mm=CLEARANCE_MM,
                  acceptance_assessed=False, final_geometry_required=('fresh supply + return <= 12 mm'
                      if connection.startswith('filter') else 'fresh supply + return <= 10 mm'),
                  router_copper_policy='preserve_existing', owned_copper_retries=False)

    def custody():
        _check_stop(clock, cancel_dir)
        if (_identity(_capture(source)) != identities or _sha(helper.read_bytes()) != helper_hash
                or _sha(Path(geometry.__file__).read_bytes()) != geometry_hash):
            raise JobRefusal('local return: source/input drift')

    try:
        if max_length_mm is not None and (not math.isfinite(max_length_mm) or max_length_mm<=0):
            raise JobRefusal('local loops: remaining loop length is exhausted')
        _no_suppression(captured)
        project = json.loads(captured['board.kicad_pro'])
        rules = project['board']['design_settings']['rules']
        if rules['min_clearance'] != CLEARANCE_MM or not 0 < rules['min_track_width'] <= WIDTH_NM / 1e6:
            raise JobRefusal('local return: requires 0.2 mm native minimum clearance and <=0.25 mm minimum width')
        endpoints = _endpoints(source / FILES[0], connection=connection)
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
            report_sha256=baseline_sha, library_warning_count=sum(warnings.values()),
            deferred_artwork=_artwork(baseline))
        custody()
        if _capture(baseline_dir) != captured:
            raise JobRefusal('local return: baseline/input drift during native checks')
        if two_layer:
            if (connection!='filter0-signal' or not 0<rules['min_via_diameter']<=.6
                    or not 0<rules['min_through_hole_diameter']<=.3):
                raise JobRefusal('local loops: two-layer via rules are unsupported')
            plans=[geometry.two_layer_plan(source/FILES[0],endpoints,clock=clock,cancel_dir=cancel_dir)]
        else:
            paths = _candidate_paths(source / FILES[0], endpoints, obstacle_search=obstacle_search,
                                     clock=clock, cancel_dir=cancel_dir)
            if obstacle_search:
                path=geometry.polygon_path(source/FILES[0],endpoints,max_length_mm or 12.,
                                           clock=clock,cancel_dir=cancel_dir)
                if path is not None and path not in paths:paths.append(path)
                paths.sort(key=lambda p:sum(math.dist(a,b) for a,b in zip(p,p[1:])))
            plans=[dict(path_nm=path,layers=['F.Cu']*(len(path)-1),vertical_length_mm=0.) for path in paths]
        for index, plan in enumerate(plans):
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha(_report_bytes(baseline_dir)) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift')
            path=plan['path_nm']
            trace_length=sum(math.dist(a,b) for a,b in zip(path,path[1:]))/1e6
            trial=dict(plan,trace_length_mm=trace_length,total_length_mm=trace_length+plan['vertical_length_mm'],status='checking')
            record['trials'].append(trial)
            if max_length_mm is not None and trial['total_length_mm']>max_length_mm:
                trial['status']='length_refused';write_json(destination/'record.json',record);continue
            trial_dir = destination / f'trial-{index + 1}';trial_dir.mkdir()
            board, ids = _seed(captured[FILES[0]], path, net=endpoints[0]['net'],layers=plan['layers'])
            vias=[]
            if two_layer:board,vias=_seed_vias(board,plan['via_centres_nm'],endpoints[0]['net'])
            for name, data in captured.items():
                if data is not None:
                    (trial_dir / name).write_bytes(board if name == FILES[0] else data)
            trial.update(segment_uuids=ids,vias=vias,board_sha256=_sha(board))
            write_json(destination / 'record.json', record)
            if _endpoints(trial_dir / FILES[0], seed=trial, connection=connection) != endpoints:
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
            trial['deferred_artwork'] = _artwork(report)
            trial['status'] = 'clearance_pass' if legal else 'rejected'
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha(_report_bytes(baseline_dir)) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift')
            expected_trial = {**captured, FILES[0]: board}
            if _capture(trial_dir) != expected_trial:
                raise JobRefusal('local return: trial/input drift during native checks')
            if not legal:
                write_json(destination / 'record.json', record)
                continue
            pending = destination / '.accepted'
            if _sha(_report_bytes(trial_dir)) != trial['report_sha256']:
                raise JobRefusal('local return: selected trial report changed before adoption')
            shutil.copytree(trial_dir, pending)
            custody()
            if (_capture(pending) != expected_trial
                    or _sha(_report_bytes(pending)) != trial['report_sha256']
                    or _sha(_report_bytes(trial_dir)) != trial['report_sha256']):
                raise JobRefusal('local return: adoption/input drift')
            record.update(status='seeded', selected_trial=index + 1, output=str(destination / 'accepted'),
                          output_inputs=_identity(expected_trial))
            write_json(destination / 'record.json', record)
            custody()
            if (_capture(baseline_dir) != captured
                    or _sha(_report_bytes(baseline_dir)) != baseline_sha):
                raise JobRefusal('local return: baseline/input or report drift before adoption')
            if (_sha(_report_bytes(pending)) != trial['report_sha256']
                    or _sha(_report_bytes(trial_dir)) != trial['report_sha256']):
                raise JobRefusal('local return: selected report drift before adoption')
            pending.rename(destination / 'accepted')
            return record
        raise JobRefusal('local return: all bounded proposals failed length or native clearance; see record.json')
    except (JobRefusal, JobStopped, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='stopped' if isinstance(exc, JobStopped) else 'refused', reason=str(exc))
        write_json(destination / 'record.json', record)
        raise


def verify_seed(board, record, *, clock, cancel_dir=None):
    """Reload the routed result and require the exact original seed to survive."""
    if record.get('schema') == 'agent-ee.acquisition-local-loops/1':
        return _verify_loops(board, record, clock=clock, cancel_dir=cancel_dir)
    _check_stop(clock, cancel_dir)
    source = Path(record['output'])
    receipt = source.parent / 'record.json'
    receipt_bytes = receipt.read_bytes()
    if (receipt.is_symlink() or record.get('status') != 'seeded'
            or canonical(json.loads(receipt_bytes)) != canonical(record)
            or _identity(_capture(source)) != record['output_inputs']):
        raise JobRefusal('local return: retained seed or operation record changed')
    trial = record['trials'][record['selected_trial'] - 1]
    if _sha(_report_bytes(source)) != trial['report_sha256']:
        raise JobRefusal('local return: retained seed report changed')
    board = Path(board)
    before = _sha(board.read_bytes())
    endpoints = _endpoints(board, seed=trial if 'layers' in trial else (trial['path_nm'],trial['segment_uuids']),
                           connection=record.get('connection', 'adc-return'))
    if trial.get('vias'):
        plan=geometry.two_layer_plan(board,endpoints,clock=clock,cancel_dir=cancel_dir)
        if canonical(plan)!=canonical({key:trial[key] for key in plan}):
            raise JobRefusal('local loops: retained via pad-frame plan changed')
    _check_stop(clock, cancel_dir)
    if (endpoints != record['endpoints'] or _sha(board.read_bytes()) != before
            or receipt.read_bytes() != receipt_bytes
            or _identity(_capture(source)) != record['output_inputs']
            or _sha(_report_bytes(source)) != trial['report_sha256']):
        raise JobRefusal('local return: final endpoint/board identity changed')
    return dict(status='pass', board_sha256=before, record_sha256=_sha(receipt_bytes),
                seed_board_sha256=record['output_inputs'][FILES[0]],
                segment_uuids=trial['segment_uuids'], acceptance_assessed=False)


def produce_loops(candidate_dir, destination, *, expected_inputs, clock, cancel_dir=None):
    """Seed the fixed acquisition legs under one caller-owned clock and custody chain."""
    _check_stop(clock, cancel_dir)
    source, destination = Path(candidate_dir).absolute(), Path(destination).absolute()
    if destination.resolve()!=destination or destination.exists() or destination.is_relative_to(source):
        raise JobRefusal('local loops: output exists/aliases/overlaps source')
    if _identity(_capture(source)) != expected_inputs:
        raise JobRefusal('local loops: source/input identity mismatch')
    helper_hash = _sha(Path(__file__).read_bytes())
    geometry_hash = _sha(Path(geometry.__file__).read_bytes())
    destination.mkdir(parents=True)
    record = dict(schema='agent-ee.acquisition-local-loops/1', status='running', inputs=expected_inputs,
                  helper_sha256=helper_hash, connections=[], record=str(destination/'record.json'),
                  geometry_helper_sha256=geometry_hash,loop_limits_mm=dict(LOOP_LIMITS),loop_lengths_mm={},
                  acceptance_assessed=False, router_copper_policy='preserve_existing')
    current, pins = source, expected_inputs
    lengths={name:0. for name in LOOP_LIMITS}
    def custody():
        _check_stop(clock, cancel_dir)
        if (_identity(_capture(source)) != expected_inputs or _sha(Path(__file__).read_bytes()) != helper_hash
                or _sha(Path(geometry.__file__).read_bytes()) != geometry_hash):
            raise JobRefusal('local loops: original source/helper drift')
    try:
        for connection in CONNECTIONS:
            custody()
            record['active_connection'] = connection
            write_json(destination/'record.json', record)
            loop=connection.split('-')[0]
            seed = _produce(current, destination/connection, expected_inputs=pins, clock=clock,
                            cancel_dir=cancel_dir, connection=connection, obstacle_search=True,
                            max_length_mm=LOOP_LIMITS[loop]-lengths[loop],two_layer=connection=='filter0-signal')
            record['connections'].append(seed)
            lengths[loop]+=_leg_length(seed)
            record['loop_lengths_mm']=dict(lengths)
            current, pins = Path(seed['output']), seed['output_inputs']
        custody()
        # Every earlier seed/report must still exist and every original seed
        # segment must survive in the final board before publishing the bundle.
        for seed in record['connections']:
            verify_seed(current/FILES[0], seed, clock=clock, cancel_dir=cancel_dir)
        custody()
        del record['active_connection']
        record.update(status='seeded', output=str(current), output_inputs=pins)
        write_json(destination/'record.json', record)
        return record
    except (JobRefusal, JobStopped, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='stopped' if isinstance(exc,JobStopped) else 'refused', reason=str(exc))
        write_json(destination/'record.json', record)
        raise


def _verify_loops(board, record, *, clock, cancel_dir=None):
    _check_stop(clock, cancel_dir)
    receipt = Path(record['record'])
    raw = receipt.read_bytes()
    if (receipt.is_symlink() or record.get('status')!='seeded'
            or canonical(json.loads(raw))!=canonical(record)
            or [s.get('connection') for s in record['connections']]!=list(CONNECTIONS)):
        raise JobRefusal('local loops: retained operation record changed')
    pins = record['inputs']
    results = []
    lengths={name:0. for name in LOOP_LIMITS}
    for seed in record['connections']:
        loop=seed['connection'].split('-')[0]
        if (seed['inputs']!=pins or seed['helper_sha256']!=record['helper_sha256']
                or seed['geometry_helper_sha256']!=record['geometry_helper_sha256']
                or seed['max_length_mm']!=LOOP_LIMITS[loop]-lengths[loop]):
            raise JobRefusal('local loops: seed custody chain changed')
        results.append(verify_seed(board, seed, clock=clock, cancel_dir=cancel_dir))
        lengths[loop]+=_leg_length(seed)
        if lengths[loop]>LOOP_LIMITS[loop]:raise JobRefusal('local loops: declared loop length exceeded')
        pins = seed['output_inputs']
    if (pins!=record['output_inputs'] or record['output']!=record['connections'][-1]['output']
            or receipt.read_bytes()!=raw or len({r['board_sha256'] for r in results})!=1
            or record['loop_lengths_mm']!=lengths or record['loop_limits_mm']!=LOOP_LIMITS):
        raise JobRefusal('local loops: output/record identity changed')
    return dict(status='pass', board_sha256=results[0]['board_sha256'], record_sha256=_sha(raw),
                seed_board_sha256=pins[FILES[0]], connections=results, acceptance_assessed=False)


def _leg_length(seed):
    trial=seed['trials'][seed['selected_trial']-1]
    trace=sum(math.dist(a,b) for a,b in zip(trial['path_nm'],trial['path_nm'][1:]))/1e6
    vertical=len(trial.get('vias',[]))*trial.get('board_thickness_nm',0)/1e6
    total=trace+vertical
    if (trace!=trial['trace_length_mm'] or vertical!=trial['vertical_length_mm']
            or total!=trial['total_length_mm'] or not math.isfinite(total) or total<=0):
        raise JobRefusal('local loops: seed length/vertical contribution changed')
    return total
