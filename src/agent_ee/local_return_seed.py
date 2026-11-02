"""Data-driven local routing seed, ground ties, and reservation contracts.

Supports both design-declared models (accepting decoupling loop definitions, ground ties,
and B.Cu thermal bars in geometry-model.json) and legacy historical revisions.
"""
import hashlib
from pathlib import Path
import re
import shutil
import uuid

from . import source_mechanics as mechanics
from .add_planes import validate_plan
from .catalog import strict_json
from .placement_job import JobRefusal, JobStopped, _check_stop, drc
from .storage import canonical, digest, write_json
from .symbol_library import children, nanometres, parse_sexpr

SCHEMA = 'agent-ee.local-routing-seed/1'
LEGACY_SCHEMA = 'agent-ee.acquisition-local-routing/1'
FILES = ('board.kicad_pcb', 'board.kicad_pro', 'board.kicad_dru')
_NUMBER = re.compile(r'-?\d+(\.\d+)?')


class LocalSearchExhausted(JobRefusal):
    """The bounded copper search or its remaining length allowance is exhausted."""


def _sha(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _mm(nanometres_val):
    sign = '-' if nanometres_val < 0 else ''
    mag = abs(nanometres_val)
    return f'{sign}{mag // 1_000_000}.{mag % 1_000_000:06d}'


def capture(directory):
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


def identity(captured):
    return {name: None if data is None else _sha(data) for name, data in captured.items()}


_capture = capture
_identity = identity


def applies(recipe):
    """Whether local routing seeding applies to this recipe."""
    if not isinstance(recipe, dict):
        return False
    model = recipe.get('model', {})
    if any(k in model for k in ('connections', 'local_loops', 'decoupling_loops', 'ground_ties', 'thermal_bars', 'local_plane_returns', 'ground_strips')):
        return True
    sources = recipe.get('declaration', {}).get('sources', {})
    model_sha = sources.get('analysis/geometry-model.json')
    if model_sha and mechanics.known_revision(model_sha):
        return True
    return False


def power_reference(recipe):
    """A validated mechanical source revision selects the electrical recipe."""
    from . import acquisition_local_return as local
    model_sha = recipe.get('declaration', {}).get('sources', {}).get('analysis/geometry-model.json')
    try:
        from . import acquisition_film, acquisition_power_geometry, acquisition_reverse_blocking
        if model_sha in acquisition_film.MODELS:
            return acquisition_film.REFERENCE
        if model_sha == acquisition_power_geometry.MODEL:
            return acquisition_reverse_blocking.REFERENCE
    except (ImportError, AttributeError):
        pass
    return local.ORIGINAL_REFERENCE


def rectangles(strips, clearance_mm):
    """Reserve 0.1 mm square-joined strip plus plane clearance."""
    margin = 100_000 + nanometres(str(clearance_mm))
    result = set()
    if not isinstance(strips, dict) or not 0 < len(strips) <= 16:
        raise JobRefusal('local routing: unsupported ground-strip population')
    for points in strips.values():
        if not isinstance(points, list) or not 2 <= len(points) <= 32:
            raise JobRefusal('local routing: unsupported ground-strip polyline')
        native = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise JobRefusal('local routing: malformed ground-strip vertex')
            native.append(tuple(nanometres(str(v)) for v in point))
        for a, b in zip(native, native[1:]):
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                raise JobRefusal('local routing: nonorthogonal/empty ground-strip edge')
            result.add((min(a[0], b[0]) - margin, min(a[1], b[1]) - margin,
                        max(a[0], b[0]) + margin, max(a[1], b[1]) + margin))
    if not 0 < len(result) <= 32:
        raise JobRefusal('local routing: reservation population exceeds 32')
    return sorted(result)


def reservation_bytes(data, recipe, planes):
    mechanics._recipe(recipe)
    plan = validate_plan(strict_json(planes))
    zones = [z for z in plan['zones'] if z['net'] == 'GND' and z['layer'] == 'B.Cu']
    if len(zones) != 1:
        raise JobRefusal('local routing: one declared B.Cu ground plane required')
    strips = recipe['model'].get('ground_strips')
    model_sha = recipe['declaration']['sources']['analysis/geometry-model.json']
    if not strips:
        return data, dict(model_sha256=model_sha, planes_sha256=_sha(planes),
                           plane_clearance_mm=zones[0]['clearance_mm'], strip_half_width_nm=100000, areas=[])
    boxes = rectangles(strips, zones[0]['clearance_mm'])
    tree = parse_sexpr(data)
    nodes = []
    rows = []
    existing = {n[1] for node in tree[1:] if isinstance(node, list) for n in children(node, 'uuid')}
    for box in boxes:
        identity_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'agent-ee/ground-strip/' + model_sha + '/' + str(box)))
        if identity_uuid in existing:
            raise JobRefusal('local routing: reservation UUID already present')
        x, y, u, v = box
        polygon = [[x, y], [u, y], [u, v], [x, v]]
        rows.append(dict(uuid=identity_uuid, rectangle_nm=list(box)))
        points = ' '.join(f'(xy {_mm(a)} {_mm(b)})' for a, b in polygon)
        nodes.append(f'\n (zone (net "") (layer "B.Cu") (uuid "{identity_uuid}") '
            f'(name "acquisition ground strip") (hatch edge 0.5) (connect_pads (clearance 0)) '
            f'(min_thickness 0.25) (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) '
            f'(copperpour allowed) (footprints allowed)) (polygon (pts {points})))\n'.encode())
    end = data.rstrip().rfind(b')')
    output = data[:end] + b''.join(nodes) + data[end:]
    if parse_sexpr(output)[:-len(rows)] != tree:
        raise JobRefusal('local routing: reservation writer changed existing nodes')
    return output, dict(model_sha256=model_sha, planes_sha256=_sha(planes),
                        plane_clearance_mm=zones[0]['clearance_mm'], strip_half_width_nm=100000, areas=rows)


def verify_reservations(board_path, reservations):
    if not reservations.get('areas'):
        return []
    import pcbnew as k
    from .placement_probe import rule_area
    board = k.LoadBoard(str(board_path))
    if board is None:
        raise JobRefusal('local routing: native reservation board load failed')
    result = []

    def canonical_ring(points):
        candidates = []
        for ring in (points, list(reversed(points))):
            candidates.extend(ring[i:] + ring[:i] for i in range(len(ring)))
        return min(candidates)

    for row in reservations['areas']:
        found = [z for z in board.Zones() if z.m_Uuid.AsString() == row['uuid']]
        if len(found) != 1 or not found[0].GetIsRuleArea() or found[0].GetNetname():
            raise JobRefusal('local routing: native reservation identity/type changed')
        native = rule_area(found[0], 'board')
        x, y, u, v = row['rectangle_nm']
        expected = dict(uuid=row['uuid'], name='acquisition ground strip', owner='board', layers=['B.Cu'],
            forbid=dict(footprints=False, tracks=True, vias=True, pads=False, zone_fills=False),
            placement_area=False, outline=dict(status='proven', polygons=[[[x, y], [u, y], [u, v], [x, v]]]))
        outline = native.get('outline', {})
        if outline.get('status') != 'proven' or len(outline.get('polygons', [])) != 1:
            raise JobRefusal('local routing: unsupported native reservation outline')
        native['outline']['polygons'] = [canonical_ring(outline['polygons'][0])]
        expected['outline']['polygons'] = [canonical_ring(expected['outline']['polygons'][0])]
        if native != expected:
            raise JobRefusal('local routing: native reservation geometry/policy changed')
        result.append(native)
    return result


def _ground_tie_plan(board, policy):
    """Derive one outward connection per native pad, generalized across packages."""
    import pcbnew as k
    if board.GetCopperLayerCount() != 2:
        raise JobRefusal('ground ties: two copper layers required')
    footprints = list(board.GetFootprints())
    refs = [fp.GetReference() for fp in footprints]
    if len(refs) != len(set(refs)):
        raise JobRefusal('ground ties: duplicate footprint references')
    fps = dict(zip(refs, footprints))
    thickness = board.GetDesignSettings().GetBoardThickness()
    if thickness <= 0:
        raise JobRefusal('ground ties: positive board thickness required')
    rows = []
    for ref, number in policy['endpoints']:
        if ref not in fps:
            raise JobRefusal('ground ties: missing endpoint footprint ' + ref)
        fp = fps[ref]
        pins = list(fp.Pads())
        selected = [p for p in pins if p.GetNumber() == number]
        if len(selected) != 1:
            raise JobRefusal('ground ties: ambiguous endpoint ' + ref + '.' + number)
        pad = selected[0]
        if (fp.GetLayer() != k.F_Cu or fp.GetOrientationDegrees() % 90
                or pad.GetAttribute() != k.PAD_ATTRIB_SMD or pad.HasHole()
                or pad.GetNetname() != 'GND' or pad.GetNetCode() <= 0
                or not pad.IsOnLayer(k.F_Cu) or not pad.FlashLayer(k.F_Cu)
                or pad.IsOnLayer(k.B_Cu)
                or pad.GetShape() not in (k.PAD_SHAPE_RECT, k.PAD_SHAPE_ROUNDRECT)):
            raise JobRefusal('ground ties: endpoint needs an orthogonal front SMD ground pad')
        origin = fp.GetPosition()
        if len(pins) == 2:
            other = [p for p in pins if p.GetNumber() != number]
            if len(other) == 1 and other[0].GetNetname() != 'GND':
                origin = other[0].GetPosition()
        point = pad.GetPosition()
        dx, dy = point.x - origin.x, point.y - origin.y
        if (dx == 0) == (dy == 0):
            raise JobRefusal('ground ties: outward pad frame is not orthogonal')
        ux = 0 if dx == 0 else (1 if dx > 0 else -1)
        uy = 0 if dy == 0 else (1 if dy > 0 else -1)
        box = pad.GetBoundingBox()
        half = (max(abs(box.GetLeft() - point.x), abs(box.GetRight() - point.x)) if ux
                else max(abs(box.GetTop() - point.y), abs(box.GetBottom() - point.y)))
        distance = half + 500_000
        max_path = int(policy.get('maximum_path_mm', 3) * 1_000_000)
        if distance <= 500_000 or distance + thickness > max_path:
            raise JobRefusal(f'ground ties: direct connection exceeds path bound {max_path}')
        rows.append(dict(endpoint=[ref, number], pad_uuid=pad.m_Uuid.AsString(),
                         footprint_uuid=fp.m_Uuid.AsString(), pad_nm=[point.x, point.y],
                         via_nm=[point.x + ux * distance, point.y + uy * distance],
                         front_length_mm=distance / 1e6, barrel_length_mm=thickness / 1e6))
    return rows


def _exposed_pad_bars(board, policy):
    """Pair exposed-pad thermal holes along native rows for B.Cu thermal bars."""
    import pcbnew as k
    spec = policy.get('exposed_pad_bars')
    if spec is None:
        return []
    footprints = [fp for fp in board.GetFootprints() if fp.GetReference() == spec['reference']]
    if len(footprints) != 1:
        raise JobRefusal('ground ties: exposed-pad footprint is missing or ambiguous')
    holes = [pad for pad in footprints[0].Pads() if pad.GetNumber() == spec['pad'] and pad.HasHole()]
    if (len(holes) != 4 or any(pad.GetAttribute() != k.PAD_ATTRIB_PTH or pad.GetNetname() != 'GND'
                               or not pad.FlashLayer(k.B_Cu) for pad in holes)):
        raise JobRefusal('ground ties: exposed pad needs four plated ground holes on B.Cu')
    points = sorted(([pad.GetPosition().x, pad.GetPosition().y], pad.m_Uuid.AsString()) for pad in holes)
    pairs = [points[:2], points[2:]]
    if any(a[0] != b[0] for (a, _), (b, _) in pairs):
        points.sort(key=lambda row: (row[0][1], row[0][0]))
        pairs = [points[:2], points[2:]]
        if any(a[1] != b[1] for (a, _), (b, _) in pairs):
            raise JobRefusal('ground ties: exposed-pad holes are not an orthogonal pair of rows')
    lands = {(pad.GetDrillSize().x, pad.GetDrillSize().y, pad.GetSize(k.B_Cu).x, pad.GetSize(k.B_Cu).y) for pad in holes}
    if len(lands) != 1:
        raise JobRefusal('ground ties: exposed-pad holes differ in drill or land')
    width_mm = spec.get('width_mm', 0.81)
    return [dict(start_nm=a, end_nm=b, pad_uuids=[first, second], land_nm=list(next(iter(lands))), width_mm=width_mm)
            for (a, first), (b, second) in pairs]


def seed_ground_ties(board_path, data, policy):
    """Generate front tie segments, vias, and optional B.Cu thermal bars."""
    import pcbnew as k
    from . import acquisition_ground_ties as ground
    # Check if legacy policy matches exact historical constants
    if policy in (ground.POLICY, ground._GROUND_ONLY):
        return ground.seed(board_path, data, policy)
    path = Path(board_path)
    before = path.read_bytes()
    if data != before:
        raise JobRefusal('ground ties: native source and captured bytes differ')
    board = k.LoadBoard(str(path))
    if board is None:
        raise JobRefusal('ground ties: native board load failed')
    rows = _ground_tie_plan(board, policy)
    bars = _exposed_pad_bars(board, policy)
    parent = _sha(data)
    tree = parse_sexpr(data)
    if tree[0] != 'kicad_pcb' or children(tree, 'version') != [['version', '20260206']]:
        raise JobRefusal('ground ties: pinned KiCad 10 board required')
    existing = {n[1] for node in tree[1:] if isinstance(node, list)
                for n in children(node, 'uuid')}
    nodes = []
    width_str = f'{policy.get("track_width_mm", 0.25):.2f}'
    via_size = f'{policy.get("via_diameter_mm", 0.6):.1f}'
    via_drill = f'{policy.get("via_drill_mm", 0.3):.1f}'
    for row in rows:
        for kind in ('segment', 'via'):
            identity_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL,
                'agent-ee/ground-ties/' + parent + '/' + str(row['endpoint']) + '/' + kind))
            if identity_uuid in existing:
                raise JobRefusal('ground ties: generated UUID already present')
            row[kind + '_uuid'] = identity_uuid
        x, y = row['via_nm']
        a, b = row['pad_nm']
        nodes.append((f'\n(segment (start {_mm(a)} {_mm(b)}) '
                      f'(end {_mm(x)} {_mm(y)}) (width {width_str}) (layer "F.Cu") '
                      f'(net "GND") (uuid "{row["segment_uuid"]}"))\n'
                      f'(via (at {_mm(x)} {_mm(y)}) (size {via_size}) (drill {via_drill}) '
                      f'(layers "F.Cu" "B.Cu") (net "GND") (uuid "{row["via_uuid"]}"))\n').encode())
    for index, row in enumerate(bars):
        row['segment_uuid'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
            'agent-ee/ground-ties/' + parent + '/exposed-pad-bar/' + str(index)))
        if row['segment_uuid'] in existing:
            raise JobRefusal('ground ties: generated UUID already present')
        a, b = row['start_nm']
        x, y = row['end_nm']
        bar_w = f'{row.get("width_mm", 0.81):.2f}'
        nodes.append((f'\n(segment (start {_mm(a)} {_mm(b)}) (end {_mm(x)} {_mm(y)}) '
                      f'(width {bar_w}) (layer "B.Cu") (net "GND") (uuid "{row["segment_uuid"]}"))\n').encode())
    end = data.rstrip().rfind(b')')
    result = data[:end] + b''.join(nodes) + data[end:]
    after = parse_sexpr(result)
    added = 2 * len(rows) + len(bars)
    if after[:-added] != tree or [n[0] for n in after[-added:]] != ['segment', 'via'] * len(rows) + ['segment'] * len(bars):
        raise JobRefusal('ground ties: generation changed original board nodes')
    receipt = dict(schema=policy.get('schema', 'agent-ee.ground-ties/1'),
                   board_sha256=_sha(result), source_board_sha256=parent, policy=policy,
                   rows=rows, exposed_pad_bars=bars)
    return result, receipt


def produce(candidate_dir, destination, *, expected_inputs, mechanics_binding, planes, clock, cancel_dir=None):
    """Seed local decoupling loops, ground ties, and ground strip reservations."""
    from . import acquisition_ground_ties as ground, acquisition_local_return as local
    _check_stop(clock, cancel_dir)
    source = Path(candidate_dir).absolute()
    destination = Path(destination).absolute()
    if destination.resolve() != destination or destination.exists() or destination.is_relative_to(source):
        raise JobRefusal('local routing: output exists/aliases/overlaps source')
    if (mechanics_binding.get('schema') != mechanics.BINDING
            or mechanics_binding.get('binding_sha256') != mechanics.identity(mechanics_binding)):
        raise JobRefusal('local routing: mechanics binding changed')
    if _identity(_capture(source)) != expected_inputs:
        raise JobRefusal('local routing: original input identity changed')
    recipe = strict_json(canonical(mechanics_binding['recipe']))
    mechanics._recipe(recipe)
    if not isinstance(planes, bytes):
        raise JobRefusal('local routing: captured plane bytes required')
    validate_plan(strict_json(planes))
    helper_sha = digest(Path(__file__))
    destination.mkdir(parents=True)
    record = dict(schema=SCHEMA, status='running', inputs=expected_inputs, helper_sha256=helper_sha,
                mechanics_binding_sha256=mechanics_binding['binding_sha256'], recipe=recipe,
                planes_raw=planes.decode('utf-8'), record=str(destination / 'record.json'), acceptance_assessed=False)

    def custody():
        _check_stop(clock, cancel_dir)
        if _identity(_capture(source)) != expected_inputs or digest(Path(__file__)) != helper_sha:
            raise JobRefusal('local routing: original source/helper drift')

    try:
        write_json(destination / 'record.json', record)
        # Determine whether decoupling loops are declared or legacy
        model = recipe.get('model', {})
        has_loops = ('local_loops' in model or 'connections' in model
                     or mechanics.known_revision(recipe['declaration']['sources']['analysis/geometry-model.json']))
        current = source
        baseline_raw = local._report_bytes(current)
        baseline = strict_json(baseline_raw)
        loops = None
        if has_loops:
            try:
                loops = local.produce_loops(source, destination / 'loops', expected_inputs=expected_inputs,
                                            clock=clock, cancel_dir=cancel_dir, reference=power_reference(recipe))
            except local.LocalSearchExhausted as exc:
                raise LocalSearchExhausted(str(exc)) from exc
            record['loops'] = loops
            current = Path(loops['output'])
            local.verify_seed(current / FILES[0], loops, clock=clock, cancel_dir=cancel_dir)
            baseline_raw = local._report_bytes(current)
            baseline = strict_json(baseline_raw)
        captured = _capture(current)
        board = captured[FILES[0]]

        # Ground ties (if declared in model)
        tie_policy = model.get('ground_ties') or model.get('local_plane_returns')
        if tie_policy is not None:
            board, record['ground_ties'] = seed_ground_ties(current / FILES[0], board, tie_policy)

        # Ground strips (if declared)
        board, reservations = reservation_bytes(board, recipe, planes)
        record['reservations'] = reservations

        trial = destination / 'reservation'
        trial.mkdir()
        expected = {**captured, FILES[0]: board}
        for name, raw in expected.items():
            if raw is not None:
                (trial / name).write_bytes(raw)
        verify_reservations(trial / FILES[0], reservations)
        if 'ground_ties' in record:
            ground.verify(trial / FILES[0], record['ground_ties'])
        report, execution = drc(trial / FILES[0], trial, clock=clock, cancel_dir=cancel_dir)
        report_sha = local._raw_report(trial, report, execution)
        record['native'] = dict(execution=execution, report_sha256=report_sha)
        assessed = (ground.without_pending_plane_warnings(report, record['ground_ties'])
                    if 'ground_ties' in record else report)
        if not local._legal(assessed) or local._warnings(report) != local._warnings(baseline):
            raise JobRefusal('local routing: ground reservations fail native checks')
        custody()
        if loops is not None:
            local.verify_seed(trial / FILES[0], loops, clock=clock, cancel_dir=cancel_dir)
        if (_capture(trial) != expected or _capture(current) != captured
                or local._report_bytes(current) != baseline_raw):
            raise JobRefusal('local routing: reservation/source/report drift')
        pending = destination / '.accepted'
        shutil.copytree(trial, pending)
        custody()
        if (_capture(pending) != expected or _capture(trial) != expected
                or _sha(local._report_bytes(pending)) != report_sha
                or _sha(local._report_bytes(trial)) != report_sha):
            raise JobRefusal('local routing: reservation adoption changed')
        verify_reservations(pending / FILES[0], reservations)
        if loops is not None:
            local.verify_seed(pending / FILES[0], loops, clock=clock, cancel_dir=cancel_dir)
        if 'ground_ties' in record:
            ground.verify(pending / FILES[0], record['ground_ties'])
        custody()
        if (_capture(pending) != expected or _capture(trial) != expected
                or _sha(local._report_bytes(pending)) != report_sha
                or _sha(local._report_bytes(trial)) != report_sha):
            raise JobRefusal('local routing: final reservation adoption drift')
        pending.rename(destination / 'accepted')
        record.update(status='seeded', output=str(destination / 'accepted'), output_inputs=_identity(expected))
        write_json(destination / 'record.json', record)
        return record
    except (JobRefusal, JobStopped, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='stopped' if isinstance(exc, JobStopped) else 'refused', reason=str(exc))
        write_json(destination / 'record.json', record)
        raise


def verify(board_path, record, *, clock, cancel_dir=None):
    """Verify local routing, ground ties, and ground strip reservations against the final board."""
    from . import acquisition_ground_ties as ground, acquisition_local_return as local
    _check_stop(clock, cancel_dir)
    receipt = Path(record['record'])
    raw = receipt.read_bytes()
    source = Path(record['output'])
    if (record.get('schema') not in (SCHEMA, LEGACY_SCHEMA) or record.get('status') != 'seeded' or receipt.is_symlink()
            or canonical(strict_json(raw)) != canonical(record)
            or _identity(_capture(source)) != record['output_inputs']
            or _sha(local._report_bytes(source)) != record['native']['report_sha256']):
        raise JobRefusal('local routing: retained operation/seed/report changed')
    before = digest(Path(board_path))
    loops = None
    if 'loops' in record and record['loops'] is not None:
        if record['loops'].get('reference', local.ORIGINAL_REFERENCE) != power_reference(record['recipe']):
            raise JobRefusal('local routing: loop electrical reference differs from captured mechanics')
        loops = local.verify_seed(board_path, record['loops'], clock=clock, cancel_dir=cancel_dir)
    prior = _capture(Path(record['loops']['output'])) if 'loops' in record and record['loops'] else _capture(source)
    expected = prior[FILES[0]]
    ground_check = None
    model = record['recipe']['model']
    policy = model.get('ground_ties') or model.get('local_plane_returns')
    if (policy is not None) != ('ground_ties' in record):
        raise JobRefusal('local routing: ground-tie receipt coverage differs')
    if policy is not None:
        loops_out = Path(record['loops']['output']) if 'loops' in record and record['loops'] else source
        expected, replayed = seed_ground_ties(loops_out / FILES[0], expected, policy)
        if replayed != record['ground_ties']:
            raise JobRefusal('local routing: generated ground-tie provenance changed')
        ground_check = ground.verify(board_path, record['ground_ties'])
    expected, reservations = reservation_bytes(expected, record['recipe'], record['planes_raw'].encode('utf-8'))
    if (reservations != record['reservations']
            or _identity({**prior, FILES[0]: expected}) != record['output_inputs']):
        raise JobRefusal('local routing: generated reservation provenance changed')
    if 'loops' in record and record['loops'] and record['loops']['inputs'] != record['inputs']:
        raise JobRefusal('local routing: loop inputs changed')
    native = verify_reservations(board_path, reservations)
    _check_stop(clock, cancel_dir)
    if (digest(Path(board_path)) != before or (loops and loops['board_sha256'] != before) or receipt.read_bytes() != raw
            or _identity(_capture(source)) != record['output_inputs']
            or _sha(local._report_bytes(source)) != record['native']['report_sha256']):
        raise JobRefusal('local routing: verification custody changed')
    return dict(status='pass', board_sha256=before, record_sha256=_sha(raw),
                **({'loops': loops} if loops is not None else {}),
                reservations=native, owned_zone_uuids=[r['uuid'] for r in reservations['areas']],
                **({'ground_ties': ground_check} if ground_check is not None else {}), acceptance_assessed=False)
