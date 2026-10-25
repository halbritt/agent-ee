"""One whole-board placement-to-route job over one prepared problem (AGENTEE-515).

Order (chief 531, b4a5857a): native capture -> Rust prepared model with
incremental search -> shortlist native materialize/save/reload/constraint
check -> pinned router process -> final save/reload/native checks.

The controller is the only orchestration layer. Individual search moves stay
inside one Rust process (``agent-ee-placement``, schema
``agent-ee.placement-job-input/1``); KiCad and the router are process adapters.
Identity is the full native footprint UUID; ``native_ref`` is carried unchanged.
A missing courtyard yields an explicitly labelled ``derived_occupancy`` with its
provenance rung; a present but unproven courtyard is a hard refusal. Separation
of derived envelopes is not body separation, and the controller never says so.

Acceptance is not decided here. A candidate is promoted to the job incumbent
only after its saved board reloads with every identity, lock, rule and
transform preserved and native DRC shows no placement-introduced error. Errors
inside one footprint that the unchanged input already has (esp_prog's U2
artwork) stay FAILED physical checks: such an incumbent is never "clean".
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time

from .storage import canonical, digest, read_json, write_json

JOB_SCHEMA = 'agent-ee.placement-job/1'
CONFIG_SCHEMA = 'agent-ee.placement-job-config/1'
INPUT_SCHEMA = 'agent-ee.placement-job-input/1'
OUTPUT_SCHEMA = 'agent-ee.placement-job-output/1'
CHECKPOINT_SCHEMA = 'agent-ee.placement-job-checkpoint/1'
PHASES = ('setup', 'import', 'prepare', 'search', 'judge', 'export', 'final_validation')
SILK = ('F.Silkscreen', 'B.Silkscreen')
FAB = {'F': 'F.Fab', 'B': 'B.Fab'}
SILK_OF = {'F': 'F.Silkscreen', 'B': 'B.Silkscreen'}
COPPER = ('F.Cu', 'B.Cu')
NM = 1_000_000
# Mirrored from job.rs; the boundary tests pin both sides.
MAX_CONTOUR_VERTICES = 2048
MAX_ITEM_CONTOURS = 16
MAX_PROBLEM_CONTOUR_VERTICES = 16384


class JobRefusal(ValueError):
    """The job input cannot be prepared honestly (fail closed)."""


class JobStopped(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def _geometry_complexity(row, total=0):
    """Refuse raw native contours before they leave capture, without changing geometry."""
    for key in ('copper_local_nm', 'holes_local_nm'):
        for item in row.get(key, []):
            if 'geometry' not in item:
                continue
            geometry = item['geometry']
            contours = geometry['polygons']
            owner = f'{row["native_ref"]} ({row["uuid"]}) pad {geometry.get("pad_uuid")}'
            if len(contours) > MAX_ITEM_CONTOURS:
                raise JobRefusal(f'{owner}: item contour count {len(contours)} exceeds {MAX_ITEM_CONTOURS}')
            for index, contour in enumerate(contours):
                count = len(contour)
                if count > MAX_CONTOUR_VERTICES:
                    raise JobRefusal(f'{owner}: contour {index} vertex count {count} exceeds {MAX_CONTOUR_VERTICES}')
                total += count
                if total > MAX_PROBLEM_CONTOUR_VERTICES:
                    raise JobRefusal(f'{owner}: contour {index} prepared contour vertex total {total} '
                                     f'exceeds {MAX_PROBLEM_CONTOUR_VERTICES} (contour count {count})')
    return total


def _check_stop(clock, cancel_dir):
    if cancel_dir is not None and _cancelled(cancel_dir):
        raise JobStopped('cancelled')
    if clock.remaining() <= 0:
        raise JobStopped('deadline')


def _run_child(argv, *, clock, cancel_dir=None, input_bytes=b'', on_line=None,
               stdout=None, stderr=None, **kwargs):
    """Bound pipe reads, writes and child lifetime by the same job clock.

    Each child owns a process group, including router/simulator descendants.
    Nonblocking pipes also cover a child which never reads stdin or closes stdout.
    """
    _check_stop(clock, cancel_dir)
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True, **kwargs)
    output, errors, pending = bytearray(), bytearray(), bytearray()
    sent = 0
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in (proc.stdout, proc.stderr, proc.stdin):
                os.set_blocking(pipe.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            selector.register(proc.stderr, selectors.EVENT_READ)
            if input_bytes:
                selector.register(proc.stdin, selectors.EVENT_WRITE)
            else:
                proc.stdin.close()
            while selector.get_map() or proc.poll() is None:
                _check_stop(clock, cancel_dir)
                for key, _ in selector.select(timeout=max(0.0, min(0.05, clock.remaining()))):
                    pipe = key.fileobj
                    if pipe is proc.stdin:
                        try:
                            sent += os.write(pipe.fileno(), input_bytes[sent:sent + 65536])
                        except BrokenPipeError:
                            sent = len(input_bytes)
                        if sent == len(input_bytes):
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    chunk = os.read(pipe.fileno(), 65536)
                    if not chunk:
                        selector.unregister(pipe)
                        continue
                    if pipe is proc.stderr:
                        errors.extend(chunk)
                        if stderr is not None:
                            stderr.write(chunk)
                    else:
                        if stdout is not None:
                            stdout.write(chunk)
                        if on_line is None:
                            if stdout is None:
                                output.extend(chunk)
                        else:
                            pending.extend(chunk)
                            while b'\n' in pending:
                                line, _, rest = pending.partition(b'\n')
                                pending[:] = rest
                                on_line(line)
            if pending and on_line is not None:
                on_line(bytes(pending))
            return subprocess.CompletedProcess(argv, proc.wait(), bytes(output), bytes(errors))
    finally:
        # Reap the owned group even if its leader exited but a descendant holds a pipe.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            pipe.close()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _mm(value):
    return int(round(float(value) * NM))


def _rule_mm(value, what):
    from .routing_rules import number
    try:
        return _mm(number(value, what))
    except (ValueError, OverflowError) as exc:
        raise JobRefusal(f'{what}: {exc}') from exc


def verified_input(entry, label):
    """Read one external input by declared SHA-256; fixture bytes never enter the repository."""
    path = Path(entry['path'])
    data = path.read_bytes()
    if sha256_bytes(data) != entry['sha256']:
        raise JobRefusal(f'{label} {path} does not match its declared sha256')
    return path, data


# ---------------------------------------------------------------- capture

def _quarter_turns(degrees, what):
    q = degrees / 90.0
    if abs(q - round(q)) > 1e-9:
        raise JobRefusal(f'{what}: orientation {degrees} is not a quarter turn')
    return int(round(q)) % 4


def _unrotate(x, y, q):
    # Inverse of job.rs rotate(): native (x, y) at q turns -> local frame.
    for _ in range((4 - q) % 4):
        x, y = y, -x
    return x, y


def _local_rect(box, origin, q):
    x0, y0, x1, y1 = box
    a = _unrotate(x0 - origin[0], y0 - origin[1], q)
    b = _unrotate(x1 - origin[0], y1 - origin[1], q)
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]


def _bbox(item):
    box = item.GetBoundingBox()
    return [int(box.GetLeft()), int(box.GetTop()), int(box.GetRight()), int(box.GetBottom())]


def _grow(box, by):
    return [box[0] - by, box[1] - by, box[2] + by, box[3] + by]


def _pad_clearance(pad):
    import pcbnew
    layers = [l for l in (pcbnew.F_Cu, pcbnew.B_Cu) if pad.IsOnLayer(l)]
    return max([int(pad.GetOwnClearance(l)) for l in layers] or [0])


def _pad_copper(pad, origin, q):
    """Conservative native copper contours, in the footprint's local frame.

    KiCad's ERROR_OUTSIDE contains the curved copper; dropped contour holes
    also over-reserve copper. This is a labelled search bound, not a DRC pass.
    """
    import pcbnew
    from .placement_probe import _outer_contours
    if pad.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL:
        raise JobRefusal(f'{pad.m_Uuid.AsString()}: per-layer padstack unsupported')
    kinds = {pcbnew.PAD_SHAPE_CIRCLE: 'circle', pcbnew.PAD_SHAPE_RECT: 'rect',
             pcbnew.PAD_SHAPE_OVAL: 'oval', pcbnew.PAD_SHAPE_ROUNDRECT: 'roundrect',
             pcbnew.PAD_SHAPE_CUSTOM: 'custom'}
    kind = kinds.get(pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS))
    if kind is None:
        raise JobRefusal(f'{pad.m_Uuid.AsString()}: unsupported native pad shape')
    contours, layers, clearances = [], [], []
    for layer in pad.GetBoard().GetEnabledLayers().CuStack():
        if not pad.IsOnLayer(layer) or not pad.IsOnCopperLayer():
            continue
        poly = pcbnew.SHAPE_POLY_SET()
        pad.TransformShapeToPolygon(poly, layer, 0, 1000, pcbnew.ERROR_OUTSIDE)
        native = _outer_contours(poly)
        if not native:
            raise JobRefusal(f'{pad.m_Uuid.AsString()}: native copper contour missing')
        contours.extend([[list(_unrotate(x - origin[0], y - origin[1], q)) for x, y in p]
                         for p in native])
        layers.append(pcbnew.LayerName(layer))
        clearances.append(int(pad.GetOwnClearance(layer)))
    if not contours:
        return None
    points = [p for contour in contours for p in contour]
    return {'rect_nm': [min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)],
            'clearance_nm': max(clearances),
            'geometry': {'status': 'conservative_native_polygon', 'kind': kind,
                         'tolerance_nm': 1000, 'polygons': contours,
                         'layers': layers, 'pad_uuid': pad.m_Uuid.AsString(),
                         'note': 'native ERROR_OUTSIDE; holes filled; layers projected together'}}


def _pad_hole(pad, origin, q, clearance):
    """Keep drills independently of copper, including bodyless NPTH marks."""
    import pcbnew
    from .placement_probe import _outer_contours
    size = pad.GetDrillSize()
    if size.x == 0 and size.y == 0:
        if pad.HasHole() or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            raise JobRefusal(f'{pad.m_Uuid.AsString()}: unsupported drill geometry')
        return None
    if size.x <= 0 or size.y <= 0 or pad.GetDrillShape() not in (
            pcbnew.PAD_DRILL_SHAPE_CIRCLE, pcbnew.PAD_DRILL_SHAPE_OBLONG):
        raise JobRefusal(f'{pad.m_Uuid.AsString()}: unsupported drill geometry')
    for get_size in (pad.GetSecondaryDrillSize, pad.GetTertiaryDrillSize):
        extra = get_size()
        if extra.x or extra.y:
            raise JobRefusal(f'{pad.m_Uuid.AsString()}: secondary/tertiary drill unsupported')
    poly = pcbnew.SHAPE_POLY_SET()
    if not pad.TransformHoleToPolygon(poly, 0, 1000, pcbnew.ERROR_OUTSIDE):
        raise JobRefusal(f'{pad.m_Uuid.AsString()}: native hole contour missing')
    contours = [[list(_unrotate(x - origin[0], y - origin[1], q)) for x, y in p]
                for p in _outer_contours(poly)]
    if not contours:
        raise JobRefusal(f'{pad.m_Uuid.AsString()}: native hole contour missing')
    points = [p for contour in contours for p in contour]
    return {'rect_nm': [min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)],
            'clearance_nm': clearance,
            'geometry': {'status': 'conservative_native_polygon', 'kind': 'hole',
                         'tolerance_nm': 1000, 'polygons': contours,
                         'pad_uuid': pad.m_Uuid.AsString(),
                         'note': 'native drill ERROR_OUTSIDE; independent hole-rule reservation'}}


def _edge_rules(intent, footprints):
    edges = {}
    for row in intent.get('edge_connectors', []):
        ref = row['ref']
        if ref in edges or sum(f['ref'] == ref for f in footprints) != 1:
            raise JobRefusal(f'{ref}: edge connector must identify one native footprint')
        if row.get('edge') not in ('west', 'east', 'north', 'south'):
            raise JobRefusal(f'{ref}: unsupported declared edge')
        rule = {'edge': row['edge'], 'max_setback_nm': None}
        if row.get('max_setback_mm') is not None:
            rule['max_setback_nm'] = _rule_mm(row['max_setback_mm'], f'{ref}: malformed edge setback')
        if 'overhang_mm' in row:
            interval = row['overhang_mm']
            if not isinstance(interval, dict) or not interval or set(interval) - {'min', 'max'}:
                raise JobRefusal(f'{ref}: malformed overhang interval')
            lo = _rule_mm(interval.get('min', 0), f'{ref}: malformed overhang interval')
            hi = _rule_mm(interval['max'], f'{ref}: malformed overhang interval') if 'max' in interval else None
            if hi is not None and (hi < lo or interval['max'] < interval.get('min', 0)):
                raise JobRefusal(f'{ref}: malformed overhang interval')
            if hi is None and lo > 0:
                raise JobRefusal(f'{ref}: positive overhang minimum needs a finite maximum')
            rule['overhang_nm'] = {'min': lo, 'max': hi}
        edges[ref] = rule
    return edges


def _edge_seat(fp, declaration, occupancy):
    """Native seat geometry, distinct from the packing reservation around it."""
    import pcbnew
    if declaration.get('class') == 'edge_receptacle' or declaration.get('context', {}).get('mount_mode') == 'edge_mount':
        side = 'B' if fp.IsFlipped() else 'F'
        for layer, basis in ((FAB[side], 'body:fab'), (SILK_OF[side], 'body:silk')):
            shapes = [g for g in fp.GraphicalItems()
                      if g.GetClass() == 'PCB_SHAPE' and pcbnew.LayerName(g.GetLayer()) == layer]
            if shapes:
                return _union([_bbox(g) for g in shapes]), basis
    if 'box' not in occupancy:
        raise JobRefusal(f'{fp.GetReference()}: declared edge has no seat geometry')
    return occupancy['box'], occupancy.get('rung', occupancy['status'])


def check_edge_intent(path, intent, *, clock=None, cancel_dir=None):
    """Grade the saved board in a bounded fresh process, including saved settings."""
    checked = _native_read(path, '_check_edges', {'intent': intent},
                           clock=clock, cancel_dir=cancel_dir)
    if (checked.get('status') not in ('pass', 'failed')
            or checked.get('coverage') != ['edge_identity']
            or not isinstance(checked.get('connectors'), list)
            or not isinstance(checked.get('violations'), list)):
        raise JobRefusal('native edge check returned missing or invalid output')
    return checked


def check_locality(path, intent, *, clock=None, cancel_dir=None):
    """Independently grade the saved native copper centroids, never solver costs."""
    if 'electrical_locality' not in intent:
        return {'status': 'not_declared'}
    checked = _native_read(path, '_check_locality', {'intent': intent}, clock=clock, cancel_dir=cancel_dir)
    if (checked.get('criterion') != 'electrical-locality/1'
            or checked.get('status') not in ('pass', 'info', 'defect')
            or any(not isinstance(checked.get(key), list) for key in ('measured', 'defects', 'unknown', 'advisories'))):
        raise JobRefusal('native locality check returned missing or invalid output')
    return checked


def _locality_grade(facts, document):
    from .electrical_locality import grade
    result = grade(facts, document)
    if result['unknown']:
        raise JobRefusal('electrical locality cannot be evaluated: ' + canonical(result['unknown']).decode())
    return result


def _check_edge_intent_native(path, intent):
    """Reload native geometry and grade declared edges independently of solver output."""
    import pcbnew
    from .placement_edges import nearest_edge, setbacks
    from .placement_probe import extract
    if not intent.get('edge_connectors'):
        return {'status': 'pass', 'connectors': [], 'violations': [], 'coverage': ['edge_identity']}
    before = Path(path).read_bytes()
    facts = extract(path)
    rules = _edge_rules(intent, facts['footprints'])
    board = pcbnew.LoadBoard(str(path))
    outline = _rectangle(facts['outline'])
    by_uuid = {f['uuid']: f for f in facts['footprints']}
    by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
    rows, violations = [], []
    for declaration in intent['edge_connectors']:
        ref = declaration['ref']
        fp = by_ref[ref]
        occupancy = classify_footprint(fp, None, by_uuid[fp.m_Uuid.AsString()])['occupancy']
        seat, basis = _edge_seat(fp, declaration, occupancy)
        actual = nearest_edge(seat, outline, int(board.GetDesignSettings().m_CopperEdgeClearance))
        row = {'ref': ref, 'uuid': fp.m_Uuid.AsString(), 'basis': basis,
               'declared': rules[ref]['edge'], 'nearest': actual, 'seat_rect_nm': seat,
               'setback_mm': {edge: distance / NM for edge, distance in setbacks(seat, outline).items()}}
        rows.append(row)
        if actual != rules[ref]['edge']:
            violations.append({'kind': 'edge_identity', 'ref': ref, 'expected': rules[ref]['edge'],
                               'actual': actual, 'basis': basis})
    if Path(path).read_bytes() != before:
        raise JobRefusal('board changed during final edge-intent checks')
    return {'status': 'failed' if violations else 'pass', 'connectors': rows,
            'violations': violations, 'coverage': ['edge_identity']}


def _on_mask(pad):
    import pcbnew
    return pad.IsOnLayer(pcbnew.F_Mask) or pad.IsOnLayer(pcbnew.B_Mask)


def _mask_expansion(pad):
    import pcbnew
    layer = pcbnew.F_Mask if pad.IsOnLayer(pcbnew.F_Mask) else pcbnew.B_Mask
    return max(0, int(pad.GetSolderMaskExpansion(layer)))


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def classify_footprint(fp, fixed_pose, captured):
    """Role, occupancy (with provenance rung) and copper for one native footprint.

    Only demonstrably silk-only, padless, zone-free graphics are excluded from
    packing. Fixed footprints whose pads are all unnetted and that draw no body
    (fab, silk or courtyard) are fixed copper marks: copper obstacles, never bodies."""
    import pcbnew
    uuid = fp.m_Uuid.AsString()
    side = captured['side']
    if side not in ('F', 'B'):
        raise JobRefusal(f'{uuid}: unsupported footprint layer')
    graphics = [g for g in fp.GraphicalItems()]
    layers = Counter(pcbnew.LayerName(g.GetLayer()) for g in graphics)
    pads = list(fp.Pads())
    zones = list(fp.Zones())
    diagnostics = []
    classified = {'User.Drawings': 'documentation drawing; not treated as a package outline'} \
        if layers.get('User.Drawings') else {}
    copper_graphics = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) in COPPER]
    copper = [(_bbox(p), _pad_clearance(p)) for p in pads] + [(_bbox(g), 0) for g in copper_graphics]
    mask = [_grow(_bbox(p), _mask_expansion(p)) for p in pads if _on_mask(p)] + [
        _bbox(g) for g in graphics if pcbnew.LayerName(g.GetLayer()) in ('F.Mask', 'B.Mask')]
    courtyards = captured['courtyards']
    if not pads and not zones and graphics and set(layers) <= set(SILK):
        return {'role': 'silk_only', 'occupancy': {'status': 'none', 'reason': 'silk-only non-electrical graphic'},
                'copper': [], 'mask': [], 'classified_layers': classified, 'diagnostics': diagnostics}
    if zones:
        raise JobRefusal(f'{uuid}: footprint rule areas/zones are not supported by the job model')
    if fixed_pose is not None:
        body_layers = {FAB[side], SILK_OF[side], f'{side}.CrtYd'} & set(layers)
        if pads and not body_layers and all(not p.GetNetname() for p in pads):
            return {'role': 'fixed_copper_mark', 'copper': copper, 'mask': mask, 'classified_layers': classified,
                    'occupancy': {'status': 'none', 'reason': 'fixed copper mark: copper obstacle, not a body'},
                    'diagnostics': diagnostics}
    for court_side, record in courtyards.items():
        if record.get('status') != 'proven':
            raise JobRefusal(f'{uuid} ({fp.GetReference()!r}): malformed courtyard on {court_side}.CrtYd: '
                             f'{record.get("status")} {record.get("reason", "")}'.strip())
    if side in courtyards:
        boxes = [[min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)]
                 for poly in courtyards[side]['polygons']]
        occupancy = {'status': 'courtyard', 'rung': 'courtyard', 'box': _union(boxes), 'sources': ['courtyard']}
    else:
        pad_boxes = [_bbox(p) for p in pads]
        fab = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == FAB[side]
               and g.GetClass() == 'PCB_SHAPE']
        silk = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == SILK_OF[side]
                and g.GetClass() == 'PCB_SHAPE']
        for g in fab + silk:
            b = _bbox(g)
            if b[0] > b[2] or b[1] > b[3]:
                diagnostics.append(f'{g.m_Uuid.AsString()}: empty drawing bounds')
        if fab:
            rung, source = 'fab+pads', fab
        elif silk:
            rung, source = 'silk+pads', silk
        elif pad_boxes:
            rung, source = 'pads', []
        else:
            raise JobRefusal(f'{uuid}: no courtyard, fab, silk or pad geometry for derived occupancy')
        boxes = [_bbox(g) for g in source] + pad_boxes
        occupancy = {'status': 'derived', 'label': 'derived_occupancy', 'rung': rung, 'box': _union(boxes),
                     'sources': sorted(g.m_Uuid.AsString() for g in source) + ['pads'],
                     'note': 'derived envelope; separation is not proof of package body separation'}
        diagnostics.append(f'courtyard missing (metadata gap): derived_occupancy rung {rung}')
    return {'role': 'fixed_body' if fixed_pose is not None else 'movable', 'occupancy': occupancy,
            'copper': copper, 'mask': mask, 'classified_layers': classified, 'diagnostics': diagnostics}


def _fixed_pose_owner(fixed_poses, captured):
    """Resolve intent fixed poses to native UUIDs by reference base and exact pose."""
    owners = {}
    for index, row in enumerate(fixed_poses):
        base = row['ref'].split('~')[0]
        target = (_mm(row['x']), _mm(row['y']))
        matches = [f for f in captured if f['ref'] == base and tuple(f['position_nm']) == target]
        if 'side' in row and matches and not any(f['side'] == row['side'] for f in matches):
            raise JobRefusal(f'intent.fixed_poses[{index}].side: differs from the fixed native side; '
                             'side changes are unsupported')
        matches = [f for f in matches if f['side'] == row.get('side', f['side'])]
        if len(matches) != 1:
            raise JobRefusal(f'intent.fixed_poses[{index}]: fixed pose {row["ref"]} '
                             f'matches {len(matches)} native footprints')
        if 'rot' in row and _quarter_turns(row['rot'], row['ref']) != _quarter_turns(
                matches[0]['rotation_deg'], row['ref']):
            raise JobRefusal(f'intent.fixed_poses[{index}].rot: differs from the fixed native rotation')
        if matches[0]['uuid'] in owners:
            raise JobRefusal(f'intent.fixed_poses[{index}].ref: duplicates a fixed native footprint')
        owners[matches[0]['uuid']] = row
    return owners


def _rectangle(outline):
    if outline.get('status') != 'proven' or len(outline['polygons']) != 1:
        raise JobRefusal('board outline is not one proven contour')
    points = outline['polygons'][0]
    xs, ys = sorted({p[0] for p in points}), sorted({p[1] for p in points})
    if len(xs) != 2 or len(ys) != 2 or len(points) != 4:
        raise JobRefusal('only a rectangular board outline is supported by this job model')
    return [xs[0], ys[0], xs[1], ys[1]]


def _clearance(settings):
    """Conservative placement bound across native classes and the board minimum."""
    net = settings.m_NetSettings
    classes = [net.GetDefaultNetclass()] + [c for _, c in net.GetNetclasses().items()]
    values = [int(settings.m_MinClearance)] + [int(c.GetClearance()) for c in classes if c.HasClearance()]
    return max(values)


def _project_rules(board_path):
    """Read authored classes; native DRC evaluates their assignments/custom rules."""
    from .catalog import strict_json
    project = Path(board_path).with_suffix('.kicad_pro')
    if not project.exists():
        return {}
    project = strict_json(project.read_bytes())
    if not isinstance(project, dict) or not isinstance(project.get('net_settings', {}), dict):
        raise JobRefusal('native project/net_settings must be objects')
    settings = project.get('net_settings', {})
    classes = settings.get('classes', [])
    if not isinstance(classes, list) or any(not isinstance(c, dict) or not isinstance(c.get('name'), str)
                                           for c in classes):
        raise JobRefusal('native netclasses require named objects')
    return project


def _validate_project_rules(project):
    """Reject malformed declared bounds; omitted bounds belong to native KiCad."""
    design = project.get('board', {}).get('design_settings', {})
    rules = design.get('rules', {})
    for key in ('min_clearance', 'min_hole_clearance', 'min_hole_to_hole',
                'min_copper_edge_clearance'):
        if key in rules:
            _rule_mm(rules[key], key)
    if 'solder_mask_min_width' in design:
        _rule_mm(design['solder_mask_min_width'], 'solder_mask_min_width')
    for cls in project.get('net_settings', {}).get('classes', []):
        if 'clearance' in cls:
            _rule_mm(cls['clearance'], cls['name'] + ' clearance')


def _native_read(board_path, command, request, *, clock=None, cancel_dir=None):
    """Read native files in a fresh process, bounded by the caller's job lifetime.

    KiCad caches projects by path, including fields omitted from a later saved
    project. Only a fresh native load gives all defaults the same meaning as
    independent native DRC. No native settings or project files are rewritten.
    """
    board_path = Path(board_path).absolute()
    sources = (board_path, board_path.with_suffix('.kicad_pro'), board_path.with_suffix('.kicad_dru'))
    before = [p.read_bytes() if p.exists() else None for p in sources]
    label = 'capture' if command == '_capture' else 'edge check'
    done = _run_child([sys.executable, '-m', 'agent_ee.placement_job', command, str(board_path)],
                      clock=clock or Clock(300), cancel_dir=cancel_dir,
                      input_bytes=json.dumps(request).encode())
    if done.returncode:
        raise JobRefusal(f'native {label} failed (exit {done.returncode}): '
                         + (done.stdout + done.stderr).decode(errors='replace')[-2000:])
    try:
        captured = json.loads(done.stdout)
    except (ValueError, TypeError) as exc:
        raise JobRefusal(f'native {label} returned missing or invalid output') from exc
    if not isinstance(captured, dict):
        raise JobRefusal(f'native {label} returned missing or invalid output')
    if [p.read_bytes() if p.exists() else None for p in sources] != before:
        raise JobRefusal(f'input board/project/rules changed during {label}')
    return captured


def capture(board_path, intent, groups, *, clock=None, cancel_dir=None):
    """Capture native geometry and settings without inheriting cached defaults."""
    validate_intent(intent, groups)
    captured = _native_read(board_path, '_capture', {'intent': intent, 'groups': groups},
                            clock=clock, cancel_dir=cancel_dir)
    try:
        problem, record = captured['problem'], captured['record']
    except KeyError as exc:
        raise JobRefusal('native capture returned missing or invalid output') from exc
    if (not isinstance(problem, dict) or problem.get('schema') != INPUT_SCHEMA
            or not isinstance(record, dict) or record.get('schema') != 'agent-ee.placement-job-capture/1'):
        raise JobRefusal('native capture returned missing or invalid output')
    return problem, record


def _capture_native(board_path, intent, groups):
    """Native capture once; returns (problem without search section, capture record)."""
    validate_intent(intent, groups)
    import pcbnew
    from .placement_probe import extract
    from .placement_escape import hints as escape_hints
    before = Path(board_path).read_bytes()
    project = _project_rules(board_path)
    _validate_project_rules(project)
    facts = extract(board_path)
    board = pcbnew.LoadBoard(str(board_path))
    for zone in board.Zones():
        if (not zone.GetIsRuleArea() or zone.GetDoNotAllowFootprints() or zone.GetDoNotAllowPads()
                or zone.GetPlacementAreaEnabled()):
            raise JobRefusal('board-level zones with pours or placement constraints are unsupported; '
                             'routing-only rule areas must allow footprints and pads')
    by_uuid = {f['uuid']: f for f in facts['footprints']}
    if len(by_uuid) != len(facts['footprints']):
        raise JobRefusal('duplicate native footprint UUID')
    owners = _fixed_pose_owner(intent.get('fixed_poses', []), facts['footprints'])
    edges = _edge_rules(intent, facts['footprints'])
    settings = board.GetDesignSettings()
    native_class = settings.m_NetSettings.GetDefaultNetclass()
    pair_width = int(native_class.GetDiffPairWidth()) if native_class.HasDiffPairWidth() else None
    pair_gap = int(native_class.GetDiffPairGap()) if native_class.HasDiffPairGap() else None
    rows, record = [], []
    contour_vertices = 0
    native = {}
    for fp in board.GetFootprints():
        uuid = fp.m_Uuid.AsString()
        captured = by_uuid[uuid]
        q = _quarter_turns(captured['rotation_deg'], uuid)
        origin = captured['position_nm']
        info = classify_footprint(fp, owners.get(uuid), captured)
        ref = captured['ref']
        if info['role'] == 'movable' and captured['locked']:
            info['role'] = 'fixed_body'
        row = {'uuid': uuid, 'native_ref': ref, 'role': info['role'],
               'pose': {'x_nm': origin[0], 'y_nm': origin[1], 'quarter_turns': q},
               'occupancy': ({k: v for k, v in info['occupancy'].items() if k in ('status', 'label', 'rung')}
                             | ({'local_rect_nm': _local_rect(info['occupancy']['box'], origin, q)}
                                if 'box' in info['occupancy'] else {})),
               'copper_local_nm': [c for p in fp.Pads() if (c := _pad_copper(p, origin, q)) is not None]
                   + [{'rect_nm': _local_rect(_bbox(g), origin, q), 'clearance_nm': 0}
                      for g in fp.GraphicalItems() if pcbnew.LayerName(g.GetLayer()) in COPPER],
               'mask_local_nm': [_local_rect(b, origin, q) for b in info['mask']],
               'holes_local_nm': [h for p in fp.Pads()
                                  if (h := _pad_hole(p, origin, q, int(settings.m_HoleClearance))) is not None],
               'pads': [{'uuid': p.m_Uuid.AsString(), 'number': p.GetNumber(), 'net': p.GetNetname(),
                         'local_nm': list(_unrotate(int(p.GetPosition().x) - origin[0],
                                                    int(p.GetPosition().y) - origin[1], q))}
                        for p in fp.Pads()]}
        if ref in edges:
            declaration = next(e for e in intent['edge_connectors'] if e['ref'] == ref)
            seat, basis = _edge_seat(fp, declaration, info['occupancy'])
            row['edge'] = dict(edges[ref], seat_local_nm=_local_rect(seat, origin, q), seat_basis=basis)
            # Mating-face direction is not captured, so an edge connector keeps its native orientation.
            row['rotations'] = [q]
        elif info['role'] == 'movable':
            row['rotations'] = [0, 1, 2, 3]
        else:
            row['rotations'] = [q]
        row['escape_local_nm'] = escape_hints(row, width=pair_width, gap=pair_gap,
                                             clearance=_clearance(settings))
        contour_vertices = _geometry_complexity(row, contour_vertices)
        rows.append(row)
        native[uuid] = {'ref': ref, 'side': captured['side'], 'locked': captured['locked']}
        record.append({'uuid': uuid, 'native_ref': ref, 'role': info['role'],
                       'occupancy': info['occupancy'], 'classified_layers': info['classified_layers'],
                       'diagnostics': info['diagnostics'], 'fixed_pose': owners.get(uuid),
                       'routing_preferences': row['escape_local_nm']})
    if Path(board_path).read_bytes() != before:
        raise JobRefusal('input board changed during capture')
    settings = board.GetDesignSettings()
    clearance = _clearance(settings)
    by_ref = {r['native_ref']: r for r in rows if r['role'] == 'movable'}
    proximity = []
    for index, row in enumerate(intent.get('proximity', [])):
        pads = row['pads']
        for key in ('ref', 'near'):
            ref = row[key]
            if sum(r['native_ref'] == ref and r['role'] == 'movable' for r in rows) != 1:
                raise JobRefusal(f'intent.proximity[{index}].{key}: must identify one movable footprint')
            if set(pads[ref]) - {p['number'] for p in by_ref[ref]['pads']}:
                raise JobRefusal(f'intent.proximity[{index}].pads.{ref}: selected pad is missing')
        a, b = by_ref[row['ref']], by_ref[row['near']]
        pick = lambda fp_row, numbers: [p['uuid'] for p in fp_row['pads'] if p['number'] in numbers]
        proximity.append({'ref': row['ref'], 'near': row['near'], 'max_nm': _mm(row['max_mm']),
                          'ref_pads': pick(a, pads[row['ref']]), 'near_pads': pick(b, pads[row['near']]),
                          'basis': row.get('basis'), 'limit': row.get('context', {}).get('limit')})
    problem = {'schema': INPUT_SCHEMA,
               'outline': {'kind': 'rectangle', 'rect_nm': _rectangle(facts['outline'])},
               'rules': {'copper_clearance_nm': clearance,
                         'hole_to_hole_clearance_nm': int(settings.m_HoleToHoleMin),
                         'copper_edge_clearance_nm': int(settings.m_CopperEdgeClearance),
                         'mask_min_web_nm': int(settings.m_SolderMaskMinWidth)},
               'footprints': sorted(rows, key=lambda r: r['uuid']),
               'groups': [{'name': g['name'], 'anchor': g['anchor'], 'refs': g['refs']} for g in groups['groups']],
               'proximity': proximity,
               'weights': {'wire': 1, 'group': 1, 'proximity': 4, 'edge': 4, 'hard': 50, 'escape': 20}}
    if 'electrical_locality' in intent:
        document = intent['electrical_locality']
        _locality_grade(facts, document)
        problem['locality'] = {'document': document, 'footprints': facts['footprints']}
    problem['problem_sha256'] = sha256_bytes(canonical(problem))
    return problem, {'schema': 'agent-ee.placement-job-capture/1', 'board_sha256': sha256_bytes(before),
                     'pcbnew_version': facts['pcbnew_version'], 'footprints': record, 'native': native,
                     'facts_sha256': sha256_bytes(canonical(facts))}


def validate_intent(intent, groups):
    from . import placement_intent
    try:
        placement_intent.validate(intent, groups)
    except ValueError as exc:
        raise JobRefusal(str(exc)) from exc
    return {'schema': placement_intent.SCHEMA,
            'input_form': 'versioned' if 'schema' in intent else 'legacy_unversioned',
            'validator_sha256': digest(Path(placement_intent.__file__)),
            'constraints': ['fixed_native_pose', 'edge_identity', 'edge_setback', 'edge_overhang',
                            'sourced_electrical_locality'],
            'preferences': ['group_anchor_distance', 'minimum_chebyshev_pad_centre_proximity'],
            'unsupported': ['keepouts', 'assembly.sides', 'blocks', 'side_changes', 'group_regions']}


# ---------------------------------------------------------------- Rust search

def rust_executable():
    from .placement_rust_runtime import resolve_executable
    return resolve_executable()


def solve(problem, search, job_dir, *, deadline_s, executable=None, on_seed=None, clock=None):
    """One Rust process for the whole seed portfolio; heartbeats carry finished seeds."""
    executable = Path(executable or rust_executable())
    payload = dict(problem, search=dict(search, deadline_ms=max(0, int(deadline_s * 1000)),
                                        cancel_file=str(Path(job_dir) / 'CANCEL')))
    encoded = json.dumps(payload).encode()
    clock = clock or Clock(deadline_s)
    final = None
    events = []

    def consume(line):
        nonlocal final
        record = json.loads(line)
        if record.get('kind') == 'heartbeat':
            if record['record'].get('event') == 'seed':
                events.append(record['record'])
                if on_seed is not None:
                    on_seed(record['record'])
        else:
            final = record

    try:
        done = _run_child([str(executable), '--events'], clock=clock, cancel_dir=job_dir,
                          input_bytes=encoded, on_line=consume)
        code, stderr = done.returncode, done.stderr.decode(errors='replace')
    except JobStopped as exc:
        # This is an interrupted execution record, never fabricated solver evidence.
        return {'schema': OUTPUT_SCHEMA, 'problem_sha256': problem['problem_sha256'],
                'status': exc.status, 'timing': {'prepare_s': None}, 'counters': {},
                'seeds': events, 'shortlist': [], 'prng': None}, {
                    'executable': str(executable), 'executable_sha256': digest(executable),
                    'input_sha256': sha256_bytes(encoded), 'exit_code': None, 'interruption': exc.status}
    if code != 0 or final is None or final.get('schema') != OUTPUT_SCHEMA:
        raise JobRefusal(f'placement engine failed (exit {code}): {(final or {}).get("error") or stderr[-2000:]}')
    if final['problem_sha256'] != problem['problem_sha256']:
        raise JobRefusal('placement engine answered a different problem')
    return final, {'executable': str(executable), 'executable_sha256': digest(executable),
                   'input_sha256': sha256_bytes(encoded), 'exit_code': code}


# ---------------------------------------------------------------- native checks

def drc(board, out_dir, *, refill=False, label='drc', clock=None, cancel_dir=None):
    out_dir = Path(out_dir)
    report = out_dir / f'{label}.json'
    argv = ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
            *(['--refill-zones'] if refill else []), '--output', str(report), str(board)]
    done = _run_child(argv, clock=clock or Clock(300), cancel_dir=cancel_dir)
    (out_dir / f'{label}.log').write_bytes(done.stdout + done.stderr)
    # kicad-cli exits 0 when it writes a report; a missing or malformed report is never a pass.
    if done.returncode != 0 or not report.is_file():
        raise JobRefusal(f'native DRC did not complete (exit {done.returncode})')
    data = read_json(report)
    if not isinstance(data.get('violations'), list) or not isinstance(data.get('unconnected_items'), list):
        raise JobRefusal('native DRC report lacks counted evaluations')
    return data, {'argv': argv, 'exit_code': done.returncode, 'report_sha256': digest(report)}


def verify_rule_loading(source, directory, *, clock, cancel_dir):
    """Prove native rule loading on a disposable copy, including track-free inputs.

    KiCad may silently discard malformed rules while returning a valid empty
    DRC report. Reuse the native routing check's final disallow-track witness;
    only this diagnostic copy gains a probe track and witness severity override.
    """
    import pcbnew as k
    from .routing_native import LOAD_WITNESS, stage_board
    _check_stop(clock, cancel_dir)
    directory = Path(directory)
    try:
        target = stage_board(Path(source), directory, dict(marker='_agent_ee_placement_rule_probe',
            rules='(version 1)\n', native_types=[]))
    except ValueError as exc:
        raise JobRefusal('cannot stage native rule loading proof: ' + str(exc)) from exc
    project = target.with_suffix('.kicad_pro').read_bytes()
    native = k.LoadBoard(str(target))
    track = k.PCB_TRACK(native)
    track.SetLayer(k.F_Cu)
    track.SetStart(k.VECTOR2I(0, 0))
    track.SetEnd(k.VECTOR2I(NM, 0))
    track.SetWidth(250000)
    native.Add(track)
    if not k.SaveBoard(str(target), native):
        raise JobRefusal('native rule loading probe SaveBoard failed')
    target.with_suffix('.kicad_pro').write_bytes(project)
    data, execution = drc(target, directory, clock=clock, cancel_dir=cancel_dir)
    witness = [v for v in data['violations'] if v['type'] == 'items_not_allowed'
               and v['severity'] == 'error' and f"rule '{LOAD_WITNESS}'" in v['description']]
    valid = bool(witness) and not any(v['type'] == 'rule_compile_error' for v in data['violations'])
    record = dict(status='pass' if valid else 'failed', rules_sha256=digest(Path(source).with_suffix('.kicad_dru')),
                  probe_board_sha256=digest(target), execution=execution, witness=witness,
                  purpose='rule loading only; probe geometry and findings do not grade the design')
    write_json(directory / 'rule-loading.json', record)
    if not valid:
        raise JobRefusal('native custom rules failed loading witness; see ' + str(directory / 'rule-loading.json'))
    return record


def _owners(board):
    """Native item UUID -> owning footprint UUID, for intra-footprint classification."""
    owner = {}
    for fp in board.GetFootprints():
        u = fp.m_Uuid.AsString()
        owner[u] = u
        for item in list(fp.Pads()) + list(fp.GraphicalItems()) + list(fp.GetFields()) + list(fp.Zones()):
            owner[item.m_Uuid.AsString()] = u
    return owner


def _key(v):
    return (v['type'], tuple(sorted(i.get('uuid', '') for i in v.get('items', []))))


def classify_errors(report, owners, baseline_keys, placed_keys=None):
    """Split native errors into pre-existing / placement-introduced / routing-introduced.

    Pre-existing: every item belongs to one footprint and the unchanged input
    reports the same (type, items). Placement-introduced: any other error present
    on the placed board. Routing-introduced: new relative to the placed board."""
    split = {'pre_existing': [], 'placement_introduced': [], 'routing_introduced': []}
    for v in report['violations']:
        if v.get('severity') != 'error':
            continue
        key = _key(v)
        footprints = {owners.get(i.get('uuid')) for i in v.get('items', [])}
        if len(footprints) == 1 and None not in footprints and key in baseline_keys:
            split['pre_existing'].append(v)
        elif placed_keys is None or key in placed_keys:
            split['placement_introduced'].append(v)
        else:
            split['routing_introduced'].append(v)
    return split


def _error_summary(split):
    return {k: {'count': len(v), 'by_type': dict(Counter(x['type'] for x in v))} for k, v in split.items()}


def _warnings(report):
    return dict(Counter(v['type'] for v in report['violations'] if v.get('severity') == 'warning'))


def native_inventory(board):
    """UUID-indexed footprint/pad identity of a loaded board."""
    import pcbnew
    rows = {}
    for fp in board.GetFootprints():
        rows[fp.m_Uuid.AsString()] = {
            'ref': fp.GetReference(), 'side': {pcbnew.F_Cu: 'F', pcbnew.B_Cu: 'B'}.get(fp.GetLayer(), '?'),
            'locked': bool(fp.IsLocked()), 'fpid': fp.GetFPIDAsString(),
            'x_nm': int(fp.GetPosition().x), 'y_nm': int(fp.GetPosition().y),
            'rotation_deg': fp.GetOrientationDegrees(),
            'pads': {p.m_Uuid.AsString(): {'number': p.GetNumber(), 'net': p.GetNetname(),
                                          'x_nm': int(p.GetPosition().x), 'y_nm': int(p.GetPosition().y)}
                     for p in fp.Pads()},
            'graphics': sorted(g.m_Uuid.AsString() for g in fp.GraphicalItems())}
    return rows


def _non_footprint_nodes(data, *, include_footprints=False, routing_owned=False, owned_zones=()):
    """Top-level board nodes other than footprints, for rule/geometry identity.

    The only normalization: native SaveBoard writes each standard layer's user
    name as a fourth `layers` element; entries are compared on ordinal, canonical
    name and type, and any other user name difference is still reported."""
    import pcbnew
    from .symbol_library import parse_sexpr
    tree = parse_sexpr(data)
    nodes = []
    for node in tree[1:]:
        if not isinstance(node, list) or not node or (node[0] == 'footprint' and not include_footprints):
            continue
        if routing_owned and node[0] in ('segment', 'arc', 'via'):
            continue
        if routing_owned and node[0] == 'zone' and any(
                isinstance(child, list) and child[:1] == ['uuid'] and child[1] in owned_zones
                for child in node[1:]):
            continue
        if node[0] == 'layers':
            # The file ordinal is the native layer id; its standard name is pcbnew.LayerName(id).
            node = ['layers'] + [entry[:3] if len(entry) == 4 and entry[0].isdigit()
                                 and entry[3] == pcbnew.LayerName(int(entry[0])) else entry for entry in node[1:]]
        nodes.append(node)
    return nodes


def materialize(source, poses, destination, project_bytes, *, intent, rules_bytes=None):
    """Apply one pose vector to a disposable native board, save, reload and check.

    Returns the integrity record. Every footprint is addressed by full UUID; pads,
    nets, refs, sides, locks, graphics and all non-footprint board content must
    be unchanged, and every pad must sit where the engine's transform put it."""
    import pcbnew
    destination = Path(destination)
    source_rules = Path(source).with_suffix('.kicad_dru')
    if (source_rules.read_bytes() if source_rules.exists() else None) != rules_bytes:
        raise JobRefusal('native custom rules differ from captured rules')
    board = pcbnew.LoadBoard(str(source))
    before = native_inventory(board)
    captured = [dict(uuid=u, ref=r['ref'], position_nm=[r['x_nm'], r['y_nm']], side=r['side'],
                     rotation_deg=r['rotation_deg'])
                for u, r in before.items()]
    protected = {u for u, row in before.items() if row['locked']} | set(
        _fixed_pose_owner(intent.get('fixed_poses', []), captured))
    target = {row['uuid']: row for row in poses}
    if set(target) != set(before) or len(target) != len(poses):
        raise JobRefusal('candidate pose vector does not cover the native footprint UUIDs exactly')
    for uuid in protected:
        row, old = target[uuid], before[uuid]
        if row['moved'] or (row['x_nm'], row['y_nm'], row['quarter_turns'], row.get('side', old['side'])) != (
                old['x_nm'], old['y_nm'], _quarter_turns(old['rotation_deg'], uuid), old['side']):
            raise JobRefusal(f'{uuid}: protected footprint pose or moved flag differs from frozen input')
    destination.mkdir(parents=True)
    for fp in board.GetFootprints():
        row = target[fp.m_Uuid.AsString()]
        if not row['moved']:
            continue
        fp.SetPosition(pcbnew.VECTOR2I(int(row['x_nm']), int(row['y_nm'])))
        fp.SetOrientationDegrees(90.0 * row['quarter_turns'])
    out = destination / 'board.kicad_pcb'
    if not pcbnew.SaveBoard(str(out), board):
        raise JobRefusal('native SaveBoard failed')
    # SaveBoard also writes sibling project files; the job restores the frozen project bytes.
    (destination / 'board.kicad_pro').write_bytes(project_bytes)
    if rules_bytes is not None:
        (destination / 'board.kicad_dru').write_bytes(rules_bytes)
    prl = destination / 'board.kicad_prl'
    if prl.exists():
        prl.unlink()
    reloaded = pcbnew.LoadBoard(str(out))
    after = native_inventory(reloaded)
    problems = []
    if set(after) != set(before):
        problems.append('footprint UUID set changed')
    for uuid, old in before.items():
        new = after.get(uuid)
        if new is None:
            continue
        for key in ('ref', 'side', 'locked', 'fpid', 'graphics'):
            if new[key] != old[key]:
                problems.append(f'{uuid}: {key} changed')
        if set(new['pads']) != set(old['pads']):
            problems.append(f'{uuid}: pad UUIDs changed')
        for pad, p in old['pads'].items():
            q = new['pads'].get(pad)
            if q is not None and (q['number'], q['net']) != (p['number'], p['net']):
                problems.append(f'{uuid}: pad {pad} number/net changed')
        row = target[uuid]
        if (new['x_nm'], new['y_nm']) != (row['x_nm'], row['y_nm']):
            problems.append(f'{uuid}: saved position differs from the engine pose')
        if _quarter_turns(new['rotation_deg'], uuid) != row['quarter_turns']:
            problems.append(f'{uuid}: saved orientation differs from the engine pose')
        for pad in row['pads']:
            q = new['pads'].get(pad['uuid'])
            if q is None or (q['x_nm'], q['y_nm']) != (pad['x_nm'], pad['y_nm']):
                problems.append(f'{uuid}: pad {pad["uuid"]} native position differs from the engine transform')
        if not row['moved'] and (new['x_nm'], new['y_nm'], new['rotation_deg']) != (
                old['x_nm'], old['y_nm'], old['rotation_deg']):
            problems.append(f'{uuid}: fixed footprint moved')
    if _non_footprint_nodes(Path(source).read_bytes()) != _non_footprint_nodes(out.read_bytes()):
        problems.append('non-footprint board content (setup, rules, nets, outline, drawings) changed')
    return {'board': str(out), 'board_sha256': digest(out), 'problems': problems,
            'integrity': 'preserved' if not problems else 'violated', 'inventory': after}


# ---------------------------------------------------------------- incumbent

def _rank(entry):
    return (entry['placement_introduced_errors'], len(entry.get('engine_violations', [])), int(entry['engine_cost']))


def _placement_eligible(entry):
    return (entry.get('integrity') == 'preserved' and entry.get('placement_introduced_errors') == 0
            and entry.get('status') == 'checked' and entry.get('engine_feasible') is True
            and entry.get('edge_intent', {}).get('status') == 'pass'
            and entry.get('locality', {}).get('status', 'not_declared') in ('pass', 'info', 'not_declared')
            and not entry.get('engine_violations'))


def promote(job_dir, entry):
    """Atomically replace the incumbent pointer only with a strictly better, fully checked candidate.

    A failed trial (integrity violated, placement-introduced errors, crash) never
    replaces or edits the incumbent; candidate directories are never overwritten."""
    job_dir = Path(job_dir)
    pointer = job_dir / 'incumbent.json'
    if not _placement_eligible(entry):
        return False
    current = read_json(pointer) if pointer.exists() else None
    if current is not None and _rank(current) <= _rank(entry):
        return False
    tmp = job_dir / '.incumbent.json.tmp'
    write_json(tmp, entry)
    os.replace(tmp, pointer)
    return True


def retain_diagnostic(job_dir, entry):
    """Best diagnostic candidate, kept separately when no incumbent qualifies."""
    pointer = Path(job_dir) / 'best-diagnostic.json'
    current = read_json(pointer) if pointer.exists() else None
    if entry.get('status') != 'checked' or entry.get('integrity') != 'preserved':
        return False
    if current is not None and _rank(current) <= _rank(entry):
        return False
    tmp = Path(job_dir) / '.best-diagnostic.json.tmp'
    write_json(tmp, entry)
    os.replace(tmp, pointer)
    return True


def checkpoint(job_dir, state):
    tmp = Path(job_dir) / '.checkpoint.json.tmp'
    write_json(tmp, dict(state, schema=CHECKPOINT_SCHEMA))
    os.replace(tmp, Path(job_dir) / 'checkpoint.json')


def load_checkpoint(job_dir, identity):
    path = Path(job_dir) / 'checkpoint.json'
    if not path.exists():
        return None
    state = read_json(path)
    if state.get('schema') != CHECKPOINT_SCHEMA or state.get('identity') != identity:
        raise JobRefusal('checkpoint belongs to a different job identity; resume refused')
    return state


# ---------------------------------------------------------------- metrics

def board_metrics(path, groups=None, edges=None):
    """Measured route length, vias and geometric via-in-pad on a saved board (read-only)."""
    import pcbnew
    data = Path(path).read_bytes()
    board = pcbnew.LoadBoard(str(path))
    length, segments, vias, arcs = Counter(), 0, [], 0
    for t in board.GetTracks():
        if t.GetClass() == 'PCB_VIA':
            vias.append(t)
        elif t.GetClass() == 'PCB_ARC':
            arcs += 1
            length[pcbnew.LayerName(t.GetLayer())] += t.GetLength()
        else:
            segments += 1
            length[pcbnew.LayerName(t.GetLayer())] += t.GetLength()
    in_pad = []
    for v in vias:
        pos = v.GetPosition()
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if any(pad.IsOnLayer(layer) and pad.HitTest(pos, 0, layer) for layer in (pcbnew.F_Cu, pcbnew.B_Cu)):
                    in_pad.append({'via': v.m_Uuid.AsString(), 'ref': fp.GetReference(), 'pad': pad.GetNumber()})
    centres = {fp.GetReference(): (fp.GetPosition().x, fp.GetPosition().y) for fp in board.GetFootprints()}
    grouping = []
    for g in (groups or []):
        anchor = centres[g['anchor']]
        spread = max(math.dist(anchor, centres[r]) for r in g['refs']) / NM
        grouping.append({'group': g['name'], 'anchor': g['anchor'], 'max_member_anchor_mm': round(spread, 3)})
    edge_rows = []
    if edges:
        outline = board.GetBoardEdgesBoundingBox()
        box = (outline.GetLeft(), outline.GetTop(), outline.GetRight(), outline.GetBottom())
        for ref, edge in edges.items():
            fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
            fab = [g for g in fp.GraphicalItems() if pcbnew.LayerName(g.GetLayer()) in ('F.Fab', 'B.Fab')]
            items = fab or list(fp.Pads())
            b = _union([_bbox(i) for i in items])
            gaps = {'west': b[0] - box[0], 'north': b[1] - box[1], 'east': box[2] - b[2], 'south': box[3] - b[3]}
            edge_rows.append({'ref': ref, 'declared': edge, 'basis': 'fab' if fab else 'pads',
                              'nearest': min(gaps, key=gaps.get),
                              'setback_mm': {k: round(v / NM, 4) for k, v in gaps.items()}})
    return {'board_sha256': sha256_bytes(data), 'track_segments': segments, 'track_arcs': arcs,
            'route_length_mm': {k: round(v / NM, 4) for k, v in sorted(length.items())},
            'route_length_total_mm': round(sum(length.values()) / NM, 4), 'vias': len(vias),
            'via_in_pad': {'count': len(in_pad), 'sites': in_pad, 'method': 'via centre inside native pad copper (HitTest) on F.Cu/B.Cu'},
            'grouping': grouping, 'edges': edge_rows,
            'extraction': 'pcbnew GetTracks lengths (segments and arcs), all copper layers'}


# ---------------------------------------------------------------- router

def _produce_planes(candidate, raw, output, *, phase=None, clock, cancel_dir):
    """Run the existing native producer inside the job's owned process budget."""
    candidate, output = Path(candidate), Path(output)
    plan = candidate / 'plane-plan.json'
    plan.write_bytes(raw)
    argv = [sys.executable, '-B', '-m', 'agent_ee.add_planes', '--inputs', str(candidate),
            '--board', 'board.kicad_pcb', '--plan', plan.name, '--out', str(output)]
    if phase:
        argv += ['--phase', phase]
    done = _run_child(argv, clock=clock, cancel_dir=cancel_dir)
    report = read_json(output / 'planes.json') if (output / 'planes.json').is_file() else {
        'execution': 'failed', 'findings': [{'message': done.stderr.decode(errors='replace')[-2000:]}]}
    if (done.returncode != 0 or report.get('execution') != 'ok'
            or report.get('plan_sha256') != sha256_bytes(raw)
            or report.get('board_before') != digest(candidate / 'board.kicad_pcb')
            or not (output / 'board.kicad_pcb').is_file()
            or report.get('board_after') != digest(output / 'board.kicad_pcb')):
        raise JobRefusal('native plane production failed: ' + str(report.get('findings', report)))
    if plan.read_bytes() != raw:
        raise JobRefusal('plane plan changed during production')
    shutil.copyfile(output / 'board.kicad_pcb', candidate / 'board.kicad_pcb')
    return {'report': str(output / 'planes.json'), 'sha256': digest(output / 'planes.json'),
            'plan_sha256': sha256_bytes(raw), 'board_sha256': report['board_after'],
            'deferred_connectivity': report['deferred_connectivity']}


def _route_attempt(route_dir, *, argv, env, clock, cancel_dir):
    """Retain one public router invocation and bind its saved candidate."""
    route_dir.mkdir(parents=True, exist_ok=True)
    with open(route_dir / 'kicad-route.stdout', 'wb') as out, open(route_dir / 'kicad-route.stderr', 'wb') as err:
        done = _run_child(argv, clock=clock, cancel_dir=cancel_dir, stdout=out, stderr=err, env=env, cwd=route_dir)
    record = {'argv': argv, 'exit_code': done.returncode}
    results = sorted((route_dir / 'out').rglob('result.json'))
    if len(results) != 1:
        return {**record, 'status': 'missing_output'}
    result = read_json(results[0])
    record.update(result_path=str(results[0]), result_sha256=digest(results[0]),
                  router_exit=result.get('exit_code'), router_status=result.get('status') or result.get('outcome'))
    candidate = result.get('candidate')
    if not candidate:
        return {**record, 'status': 'no_candidate'}
    board = route_dir / candidate['board']
    sha = digest(board)
    if sha != result.get('validation', {}).get('candidate_board_sha256'):
        return {**record, 'status': 'stale_candidate'}
    accepted = (done.returncode == result.get('exit_code') == 0
                and result.get('process', {}).get('status') == 'PASS'
                and result.get('routing', {}).get('status') == 'COMPLETE'
                and result.get('routing', {}).get('unconnected_total') == 0
                and result.get('validation', {}).get('status') == 'PASS')
    return {**record, 'status': 'routed', 'board': str(board), 'board_sha256': sha,
            'router_accepted': accepted,
            **({'impedance_plan': result['settings']['pair_impedance']}
               if 'pair_impedance' in result.get('settings', {}) else {})}


def _route_feedback(record, checkout, *, env, clock, cancel_dir):
    """Select one bounded repair from native findings; never relax a rule."""
    if record.get('status') != 'routed' or record['router_accepted']:
        return None
    result_path = Path(record['result_path'])
    result = read_json(result_path)
    validation = result['validation']
    if (result.get('process', {}).get('status') != 'PASS'
            or result.get('routing', {}).get('unconnected_total') != 0
            or validation.get('drc', {}).get('new_parity', {}).get('count') != 0):
        return None
    preservation = validation.get('preservation', {})
    if not preservation or any(v != 'PASS' for k, v in preservation.items() if k != 'pour_integrity'):
        return None
    findings = read_json(result_path.parent / 'validate' / 'new-findings.json')['violations']
    kinds = {v['type'] for v in findings}
    if 'starved_thermal' in kinds and kinds <= {'starved_thermal', 'track_dangling'}:
        from . import kicad_route_attempt as attempt
        argv = [attempt.PYTHON, '-B', str(Path(checkout) / 'bin/kicad-route'), 'spokes', str(result_path.parent)]
        done = _run_child(argv, clock=clock, cancel_dir=cancel_dir, env=env, cwd=result_path.parent)
        path = result_path.parent / 'spokes.json'
        if done.returncode != 3 or not path.is_file():
            return None
        report = read_json(path)
        pads = report['starved_pads']
        if not pads or not all(p.get('new') is True and p.get('attributed') is True
                               and p.get('uuid') and p.get('layer') for p in pads):
            return None
        return {'kind': 'thermal_corridors', 'evidence': str(path), 'sha256': digest(path),
                'pads': sorted({p['uuid'] + '@' + p['layer'] for p in pads})}
    if (kinds == {'track_dangling'} and all(v == 'PASS' for v in preservation.values())
            and result.get('settings', {}).get('pairs') == []):
        # The public force-reroute option cannot be combined with pair routing.
        # Only a positively established pair-free attempt may use this repair.
        import pcbnew
        board = pcbnew.LoadBoard(record['board'])
        tracks = {t.m_Uuid.AsString(): t for t in board.GetTracks()}
        items = [i['uuid'] for v in findings for i in v.get('items', [])]
        if not items or any(i not in tracks or not tracks[i].GetNetname() for i in items):
            return None
        return {'kind': 'dangling_net', 'nets': sorted({tracks[i].GetNetname() for i in items}),
                'evidence': str(result_path.parent / 'validate/new-findings.json'),
                'sha256': digest(result_path.parent / 'validate/new-findings.json')}
    return None


def route(candidate_dir, route_dir, checkout, *, budget_s, timeout_s, home, clock=None, cancel_dir=None,
          design_name='board', grid_step_mm=None, intra_pair_match=None, planes=None, pair_layers=None, pair_impedance=None,
          bus=None, length_match=None, ac_match=None):
    """Pinned kicad-route process on a fresh copy of one materialized candidate.

    The board has no schematic, which the sandboxed routing_adapter step refuses;
    the pinned CLI is invoked with the adapter's own argv shape and identity checks."""
    from . import kicad_route_attempt as attempt, routing_adapter
    clock = clock or Clock(timeout_s)
    _check_stop(clock, cancel_dir)
    check = routing_adapter.preflight(checkout)
    if check['problems']:
        raise JobRefusal('router preflight: ' + '; '.join(check['problems']))
    route_dir = Path(route_dir)
    if not isinstance(design_name, str) or not design_name or Path(design_name).name != design_name or design_name in ('.', '..'):
        raise JobRefusal('design name must be one nonempty path component')
    staged = route_dir / 'candidate' / design_name
    staged.mkdir(parents=True)
    for name in ('board.kicad_pcb', 'board.kicad_pro', 'board.kicad_dru'):
        if name.endswith('.kicad_dru') and not (Path(candidate_dir) / name).exists():
            continue
        shutil.copyfile(Path(candidate_dir) / name, staged / name)
    started = time.monotonic()
    route_clock = Clock(min(budget_s, clock.remaining()))
    plane_record = None
    if planes is not None:
        try:
            plane_record = _produce_planes(staged, planes, route_dir / 'planes', phase='pre-routing',
                clock=Clock(min(budget_s, clock.remaining())), cancel_dir=cancel_dir)
        except JobStopped as exc:
            if exc.status != 'deadline' or clock.remaining() <= 0:
                raise
            return {'status': 'plane_deadline', 'preflight': check,
                    'elapsed_s': time.monotonic() - started}
        except JobRefusal as exc:
            return {'status': 'plane_failed', 'preflight': check, 'reason': str(exc),
                    'elapsed_s': time.monotonic() - started}
    _check_stop(clock, cancel_dir)
    # The CLI accepts integral seconds. Refuse sub-second remainder rather than
    # inventing a minimum extension; the outer clock also bounds all descendants.
    remaining = min(budget_s - (time.monotonic() - started), clock.remaining())
    if remaining < 1:
        if planes is None:
            raise JobStopped('deadline')
        return {'status': 'plane_deadline', 'preflight': check,
                'elapsed_s': time.monotonic() - started}
    # These jobs normally start without copper. Prove that on the actual native
    # input before allowing the router to revisit its own generated tracks.
    import pcbnew
    owned_copper = planes is not None and not list(pcbnew.LoadBoard(str(staged / 'board.kicad_pcb')).GetTracks())
    argv = [attempt.PYTHON, '-B', str(Path(checkout) / 'bin' / 'kicad-route'), 'run', '--out', str(route_dir / 'out'),
            '--json', '--backend', attempt.PIN_BACKEND_NAMES['builtin'], '--dir', str(staged),
            '--board', 'board.kicad_pcb', '--schematic', 'board.kicad_sch', '--name', design_name,
            '--label', 'placement-job', '--protect', 'locked' if owned_copper else 'existing',
            '--budget', str(int(remaining)), '--diff-pairs', 'auto']
    if grid_step_mm is not None:
        argv += ['--grid-step', str(grid_step_mm)]
    if pair_layers is not None:
        argv += ['--pair-layers', *pair_layers]
    if pair_impedance is not None:
        argv += ['--pair-impedance', str(pair_impedance['target_ohm']),
                 '--impedance-tolerance', str(pair_impedance['tolerance_percent']),
                 '--impedance-reference', pair_impedance['reference_net'],
                 '--impedance-uncoupled', str(pair_impedance['max_uncoupled_mm'])]
    if intra_pair_match is not None:
        argv += ['--diff-pair-intra-match', '--length-match-tolerance', str(intra_pair_match['tolerance_mm']),
                 '--meander-amplitude', str(intra_pair_match['amplitude_mm'])]
    if bus is not None:
        argv += ['--bus']
        for key, value in bus.items():
            argv += ['--bus-' + key.removesuffix('_mm').replace('_', '-'), str(value)]
    if length_match is not None:
        for group in length_match['groups']:
            argv += ['--length-match-group-json', json.dumps(group)]
        unit = 'ps' if 'tolerance_ps' in length_match else 'mm'
        flag = '--group-delay-tolerance' if unit == 'ps' else '--group-match-tolerance'
        argv += [flag, str(length_match['tolerance_' + unit]),
                 '--group-meander-amplitude', str(length_match['amplitude_mm']),
                 '--group-meander-spacing', str(length_match['spacing_widths'])]
    if ac_match is not None:
        for refs in ac_match['chains']:
            argv += ['--ac-couple-chain-json', json.dumps(refs)]
        argv += ['--ac-match-tolerance', str(ac_match['tolerance_mm']),
                 '--ac-meander-amplitude', str(ac_match['amplitude_mm'])]
    Path(home).mkdir(parents=True, exist_ok=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'TMPDIR': str(home), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
           'KICAD_ROUTE_STATE': str(checkout), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_OPTIONAL_LOCKS': '0'}
    record = _route_attempt(route_dir, argv=argv, env=env, clock=clock, cancel_dir=cancel_dir)
    attempts = [dict(record)]
    used, pads = set(), []
    # At most one retry for each demonstrated defect class, within the original
    # candidate's remaining allocation. Failed trials and source bytes survive.
    while owned_copper and route_clock.remaining() >= 1:
        try:
            feedback = _route_feedback(record, checkout, env=env, clock=route_clock, cancel_dir=cancel_dir)
            if feedback is None or feedback['kind'] in used:
                break
            if (intra_pair_match is not None or length_match is not None or ac_match is not None) and feedback['kind'] != 'thermal_corridors':
                attempts[-1]['feedback'] = {**feedback, 'recovery': 'not_applied',
                    'reason': 'single-net repair cannot preserve required matching groups'}
                break
            used.add(feedback['kind'])
            attempts[-1]['feedback'] = feedback
            if route_clock.remaining() < 1:
                break
            retry_dir = route_dir / ('repair-' + feedback['kind'])
            retry = list(argv)
            retry[retry.index('--out') + 1] = str(retry_dir / 'out')
            retry[retry.index('--budget') + 1] = str(int(route_clock.remaining()))
            if feedback['kind'] == 'thermal_corridors':
                pads = feedback['pads']
            else:
                retry[retry.index('--dir') + 1] = str(Path(record['board']).parent)
                del retry[retry.index('--diff-pairs'):retry.index('--diff-pairs') + 2]
                retry += ['--nets', *feedback['nets'], '--backend-arg=--force-reroute']
            for pad in pads:
                retry += ['--reserve-spoke-pad', pad]
            attempts.append({'argv': retry, 'status': 'running'})
            record = _route_attempt(retry_dir, argv=retry, env=env,
                                    clock=route_clock, cancel_dir=cancel_dir)
            attempts[-1] = dict(record)
        except JobStopped as exc:
            if exc.status != 'deadline' or clock.remaining() <= 0:
                raise
            attempts[-1]['feedback_deadline'] = True
            if attempts[-1].get('status') == 'running':
                attempts[-1]['status'] = 'deadline'
            break
    record.update(attempts=attempts, elapsed_s=time.monotonic() - started, preflight=check,
                  copper_policy='generated_from_track_free_input' if owned_copper else 'preserve_existing')
    if plane_record is not None:
        record['planes'] = plane_record
    return record


# ---------------------------------------------------------------- job

class Clock:
    def __init__(self, budget_s):
        self.start = time.monotonic()
        self.budget = budget_s
        self.phases = {}

    def remaining(self):
        return self.budget - (time.monotonic() - self.start)

    def phase(self, name):
        clock = self

        class _P:
            def __enter__(self):
                self.t = time.monotonic()

            def __exit__(self, *exc):
                clock.phases[name] = round(clock.phases.get(name, 0.0) + time.monotonic() - self.t, 3)
        return _P()


def _cancelled(job_dir):
    return (Path(job_dir) / 'CANCEL').exists()


def run_job(config, job_dir, *, resume=False, executable=None):
    """The supported entry point: prepare -> search -> shortlist checks -> route -> final checks."""
    clock = Clock(float(config['budget_s']))
    report = {'schema': JOB_SCHEMA, 'status': 'running', 'config_sha256': sha256_bytes(canonical(config)),
              'claims': {'accepted': False, 'clean': False, 'superiority': None}}
    ownership = {'established': False}
    try:
        return _execute_job(config, job_dir, resume=resume, executable=executable, clock=clock,
                            report=report, ownership=ownership)
    except JobRefusal as exc:
        if not ownership['established']:
            raise
        report.update(status='refused', reason=str(exc), phases=clock.phases,
                      elapsed_s=round(time.monotonic() - clock.start, 3))
        report.setdefault('attempt', None)
        if report['attempt'] is not None:
            write_json(Path(job_dir) / f'job-report-a{report["attempt"]}.json', report)
        write_json(Path(job_dir) / 'job-report.json', report)
        return report
    except JobStopped as exc:
        report['status'] = exc.status
        report.setdefault('engine', {'status': exc.status, 'not_started': True})
        report['phases'] = clock.phases
        report['elapsed_s'] = round(time.monotonic() - clock.start, 3)
        if not ownership['established']:
            return report
        # An interrupted trial never overwrites the incumbent or its board.
        pointer = Path(job_dir) / 'incumbent.json'
        report['incumbent'] = read_json(pointer) if pointer.exists() else None
        if report.get('attempt') is not None:
            write_json(Path(job_dir) / f'job-report-a{report["attempt"]}.json', report)
        write_json(Path(job_dir) / 'job-report.json', report)
        return report


def _execute_job(config, job_dir, *, resume, executable, clock, report, ownership):
    job_dir = Path(job_dir).absolute()
    if job_dir.exists() and not resume:
        if any(p.name != 'CANCEL' for p in job_dir.iterdir()):
            raise JobRefusal(f'job directory {job_dir} is not empty; use resume')
    ownership['established'] = not resume
    if not resume:
        job_dir.mkdir(parents=True, exist_ok=True)
    with clock.phase('setup'):
        ac_match = config['router'].get('ac_match')
        if 'ac_match' in config['router']:
            if not isinstance(ac_match, dict) or set(ac_match) != {'chains', 'tolerance_mm', 'amplitude_mm'}:
                raise JobRefusal('router.ac_match requires chains, tolerance_mm and amplitude_mm')
            chains = ac_match['chains']
            if (not isinstance(chains, list) or not chains
                    or any(not isinstance(refs, list) or len(refs) < 2
                           or not all(isinstance(ref, str) and ref for ref in refs) for refs in chains)):
                raise JobRefusal('router.ac_match needs explicit series-part reference chains')
            refs = [ref for chain in chains for ref in chain]
            if len(set(refs)) != len(refs):
                raise JobRefusal('router.ac_match series-part references must not repeat')
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < .000001
                   for key, value in ac_match.items() if key != 'chains'):
                raise JobRefusal('router.ac_match controls must be finite and positive')
        length_match = config['router'].get('length_match')
        if 'length_match' in config['router']:
            unit = 'ps' if isinstance(length_match, dict) and 'tolerance_ps' in length_match else 'mm'
            required = {'groups', 'tolerance_' + unit, 'amplitude_mm'}
            if (not isinstance(length_match, dict) or not required <= set(length_match)
                    or set(length_match) - required - {'spacing_widths'}):
                raise JobRefusal('router.length_match requires groups, exactly one of tolerance_mm or tolerance_ps, and amplitude_mm; spacing_widths is optional')
            length_match = {'spacing_widths': 2, **length_match}
            members = length_match['groups']
            if (not isinstance(members, list) or not members
                    or any(not isinstance(group, list) or len(group) < 2
                           or not all(isinstance(name, str) and name for name in group)
                           or len(set(group)) != len(group) for group in members)):
                raise JobRefusal('router.length_match groups need at least two distinct exact net names')
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < .000001
                   for key, value in length_match.items() if key != 'groups'):
                raise JobRefusal('router.length_match controls must be finite and positive')
            if length_match['spacing_widths'] < 1:
                raise JobRefusal('router.length_match spacing must be at least one track width')
        bus = config['router'].get('bus')
        if 'bus' in config['router']:
            fields = {'detection_radius_mm', 'attraction_radius_mm', 'attraction_bonus', 'min_nets'}
            if not isinstance(bus, dict) or set(bus) - fields:
                raise JobRefusal('router.bus accepts detection_radius_mm, attraction_radius_mm, attraction_bonus and min_nets only')
            for key, value in bus.items():
                if key.endswith('_mm'):
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0.000001:
                        raise JobRefusal('bus radii must be finite and at least 0.000001 mm')
                elif type(value) is not int or value < (2 if key == 'min_nets' else 0):
                    raise JobRefusal('bus minimum nets must be an integer at least two and attraction bonus a nonnegative integer')
        pair_layers = config['router'].get('pair_layers')
        if 'pair_layers' in config['router']:
            if (not isinstance(pair_layers, list) or not pair_layers
                    or any(not isinstance(layer, str) or not layer for layer in pair_layers)
                    or len(set(pair_layers)) != len(pair_layers)):
                raise JobRefusal('router.pair_layers requires a nonempty list of distinct copper layer names')
        impedance = config['router'].get('pair_impedance')
        if 'pair_impedance' in config['router']:
            fields = {'target_ohm', 'tolerance_percent', 'reference_net', 'max_uncoupled_mm'}
            if not isinstance(impedance, dict) or set(impedance) != fields:
                raise JobRefusal('router.pair_impedance requires target_ohm, tolerance_percent, reference_net and max_uncoupled_mm')
            if (not isinstance(impedance['reference_net'], str) or not impedance['reference_net'].strip()
                    or any(type(impedance[key]) not in (int, float) or not math.isfinite(impedance[key])
                           or impedance[key] <= 0 for key in fields - {'reference_net'})
                    or impedance['tolerance_percent'] >= 100):
                raise JobRefusal('pair impedance needs positive finite numbers, tolerance below 100 percent and a reference net')
            if not pair_layers or len(pair_layers) != 1:
                raise JobRefusal('qualified pair impedance requires exactly one explicit pair layer')
        matching = config['router'].get('intra_pair_match')
        if 'intra_pair_match' in config['router']:
            if not isinstance(matching, dict) or set(matching) != {'tolerance_mm', 'amplitude_mm'}:
                raise JobRefusal('router.intra_pair_match requires tolerance_mm and amplitude_mm only')
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0.000001
                   for value in matching.values()):
                raise JobRefusal('intra-pair matching values must be finite and at least 0.000001 mm')
        unknown = set(config['inputs']) - {'board', 'project', 'rules', 'intent', 'groups', 'locality', 'planes'}
        if unknown:
            raise JobRefusal('unsupported placement input(s): ' + ', '.join(sorted(unknown)))
        board_path, board_bytes = verified_input(config['inputs']['board'], 'board')
        report['design_name'] = board_path.stem
        project_path, project_bytes = verified_input(config['inputs']['project'], 'project')
        if project_path.resolve() != board_path.with_suffix('.kicad_pro').resolve():
            raise JobRefusal('project input must be the native board sibling .kicad_pro')
        rules_path = board_path.with_suffix('.kicad_dru')
        rules_bytes = None
        if 'rules' in config['inputs']:
            declared, rules_bytes = verified_input(config['inputs']['rules'], 'rules')
            if declared.resolve() != rules_path.resolve():
                raise JobRefusal('rules input must be the native board sibling .kicad_dru')
        elif rules_path.exists():
            raise JobRefusal('native custom rules require a captured inputs.rules path and sha256')
        _, intent_bytes = verified_input(config['inputs']['intent'], 'intent')
        _, groups_bytes = verified_input(config['inputs']['groups'], 'groups')
        from .catalog import strict_json
        try:
            intent = strict_json(intent_bytes)
            groups = strict_json(groups_bytes)
        except ValueError as exc:
            raise JobRefusal('placement intent/groups JSON: ' + str(exc)) from exc
        report['intent_contract'] = validate_intent(intent, groups)
        plane_bytes = None
        plane_ids = ()
        if 'planes' in config['inputs']:
            from .add_planes import validate_plan, zone_ids
            from .catalog import strict_json
            _, plane_bytes = verified_input(config['inputs']['planes'], 'planes')
            try:
                plane_plan = validate_plan(strict_json(plane_bytes))
            except ValueError as exc:
                raise JobRefusal('invalid plane plan: ' + str(exc)) from exc
            if any(zone['island_removal'] != 'always' for zone in plane_plan['zones']):
                raise JobRefusal('placement job final planes require island_removal=always')
            plane_ids = zone_ids(plane_plan)
        if 'locality' in config['inputs']:
            _, raw = verified_input(config['inputs']['locality'], 'locality')
            try:
                document = strict_json(raw)
            except ValueError as exc:
                raise JobRefusal('inputs.locality JSON: ' + str(exc)) from exc
            if 'electrical_locality' in intent and intent['electrical_locality'] != document:
                raise JobRefusal('inline locality differs from the captured locality input')
            intent['electrical_locality'] = document
            report['intent_contract'] = validate_intent(intent, groups)
        report['preflight'] = {}
        report['preflight'] = preflight(config, executable, clock=clock, identities=report['preflight'])
        if impedance is not None:
            # Native load/save inserts dielectric defaults. Check the authored
            # bytes before placement can make an omitted value look declared.
            done = _run_child([sys.executable, '-B', '-c',
                'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                'from kicad_route.pair_impedance import model; model(Path(sys.argv[2]), sys.argv[3])',
                str(Path(config['router']['checkout']) / 'src'), str(board_path), pair_layers[0]],
                clock=clock, cancel_dir=None)
            if done.returncode:
                raise JobRefusal('original impedance stackup is unproven: '
                                 + done.stderr.decode(errors='replace')[-2000:])
            report['preflight']['impedance_stackup'] = {
                'status': 'PASS', 'board_sha256': sha256_bytes(board_bytes), 'layer': pair_layers[0]}
        if length_match is not None and 'tolerance_ps' in length_match:
            done = _run_child([sys.executable, '-B', '-c',
                'import json,sys; sys.path.insert(0, sys.argv[1]); '
                'from native_delay import profiles; '
                'profiles(sys.argv[2], json.loads(sys.argv[3]), required=True)',
                str(Path(config['router']['checkout']) / 'src/kicad_route/router/py_router'),
                str(board_path), json.dumps(sorted({n for g in length_match['groups'] for n in g}))],
                clock=clock, cancel_dir=None)
            if done.returncode:
                raise JobRefusal('original delay model is unproven: '
                                 + done.stderr.decode(errors='replace')[-2000:])
            report['preflight']['delay_model'] = {
                'status': 'PASS', 'board_sha256': sha256_bytes(board_bytes),
                'project_sha256': sha256_bytes(project_bytes)}
    with clock.phase('import'):
        _check_stop(clock, None)
        # A pre-existing CANCEL still captures/checkpoints the initial identity,
        # as before, so that removing it can resume the same job. New cancellation
        # during capture is watched by the bounded child runner.
        capture_cancel = None if _cancelled(job_dir) else job_dir
        problem, record = capture(board_path, intent, groups, clock=clock, cancel_dir=capture_cancel)
        for name in ('board', 'project', *(['rules'] if rules_bytes is not None else [])):
            verified_input(config['inputs'][name], name)
    identity = sha256_bytes(canonical({'problem': problem['problem_sha256'], 'config': report['config_sha256'],
                                       'engine': report['preflight']['engine_sha256'],
                                       'controller': digest(Path(__file__)),
                                       'intent_contract': report['intent_contract'],
                                       'router': report['preflight'].get('router'),
                                       'plane_tools': report['preflight'].get('plane_tools'),
                                       'kicad_cli': report['preflight'].get('kicad_cli'),
                                       'pcbnew': record.get('pcbnew_version')}))
    report['identity'] = identity
    state = load_checkpoint(job_dir, identity) if resume else None
    if resume and state is None:
        raise JobRefusal('resume requires an identity-bound checkpoint')
    ownership['established'] = True
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / 'capture.json', record)
    write_json(job_dir / 'problem.json', problem)
    if plane_bytes is not None:
        (job_dir / 'plane-plan.json').write_bytes(plane_bytes)
    state = state or {'identity': identity, 'completed_seeds': [], 'seed_results': {}, 'attempts': 0}
    # Each attempt writes its own candidate/route/final directories; nothing earlier is overwritten.
    state['attempts'] = attempt = state.get('attempts', 0) + 1
    report['attempt'] = attempt
    checkpoint(job_dir, state)
    if rules_bytes is not None:
        with clock.phase('rule_validation'):
            report['native_rules'] = verify_rule_loading(board_path, job_dir / f'native-rules-a{attempt}',
                                                        clock=clock, cancel_dir=job_dir)
    search = dict(config['search'], completed_seeds=state['completed_seeds'])

    def on_seed(event):
        state['completed_seeds'] = sorted(set(state['completed_seeds']) | (
            {event['seed']} if event['status'] == 'completed' else set()))
        state['seed_results'][str(event['seed'])] = event
        checkpoint(job_dir, state)

    with clock.phase('search'):
        output, engine = solve(problem, search, job_dir, deadline_s=clock.remaining(),
                               executable=executable, on_seed=on_seed, clock=clock)
        write_json(job_dir / 'engine-output.json', output)
    report['engine'] = {**engine, 'status': output['status'], 'counters': output['counters'],
                        'seeds': output['seeds'], 'prng': output['prng']}
    if output['status'] in ('cancelled', 'deadline'):
        raise JobStopped(output['status'])
    clock.phases['prepare'] = round(output['timing']['prepare_s'], 6)
    # Earlier seeds of a resumed job compete with the new ones on equal terms.
    shortlist = list(output['shortlist'])
    for seed, event in state['seed_results'].items():
        if event.get('feasible') and int(seed) not in {c['seed'] for c in shortlist} and event.get('poses'):
            shortlist.append({'seed': int(seed), 'cost': event['cost'], 'poses': event['poses'], 'resumed': True})
    shortlist.sort(key=lambda c: (int(c['cost']), c['seed']))
    shortlist = [dict(c, feasible=True) for c in shortlist[:int(config['search'].get('shortlist', 3))]]
    if not shortlist:
        # No hard-feasible state: least-infeasible states, with their exact violations,
        # are checked natively as DIAGNOSTIC candidates and can never be promoted.
        shortlist = list(output.get('diagnostic_shortlist', []))
    report['engine_feasible'] = any(c.get('feasible') for c in shortlist)
    import pcbnew
    owners = _owners(pcbnew.LoadBoard(str(board_path)))
    with clock.phase('judge'):
        _check_stop(clock, job_dir)
        (job_dir / f'input-drc-a{attempt}').mkdir()
        base_report, base_exec = drc(board_path, job_dir / f'input-drc-a{attempt}', label='native-drc',
                                     clock=clock, cancel_dir=job_dir)
        base_keys = {_key(v) for v in base_report['violations'] if v.get('severity') == 'error'}
        trials = []
        for index, cand in enumerate(shortlist):
            _check_stop(clock, job_dir)
            entry = {'index': index, 'seed': cand['seed'], 'engine_cost': cand['cost'], 'status': 'failed',
                     'engine_feasible': bool(cand.get('feasible')), 'engine_violations': cand.get('violations', [])}
            directory = job_dir / 'candidates' / f'a{attempt}-{index:02d}-seed{cand["seed"]}'
            try:
                check = materialize(board_path, cand['poses'], directory, project_bytes,
                                    intent=intent, rules_bytes=rules_bytes)
                _check_stop(clock, job_dir)
                report_, exec_ = drc(directory / 'board.kicad_pcb', directory, label='native-drc',
                                     clock=clock, cancel_dir=job_dir)
                split = classify_errors(report_, owners, base_keys)
                entry.update(status='checked', integrity=check['integrity'], integrity_problems=check['problems'],
                             locality=check_locality(directory / 'board.kicad_pcb', intent,
                                                     clock=clock, cancel_dir=job_dir),
                             edge_intent=check_edge_intent(directory / 'board.kicad_pcb', intent,
                                                           clock=clock, cancel_dir=job_dir),
                             board=check['board'], board_sha256=check['board_sha256'], drc=exec_,
                             errors=_error_summary(split), warnings=_warnings(report_),
                             placement_introduced_errors=len(split['placement_introduced']),
                             pre_existing_errors=len(split['pre_existing']), directory=str(directory))
            except (JobRefusal, OSError, ValueError, subprocess.SubprocessError) as exc:
                entry['error'] = f'{type(exc).__name__}: {exc}'
            entry['promoted'] = promote(job_dir, entry)
            entry['diagnostic_retained'] = retain_diagnostic(job_dir, entry)
            trials.append(entry)
        report['shortlist'] = trials
        report['input_drc'] = {**base_exec, 'errors': len(base_keys)}
    incumbent = read_json(job_dir / 'incumbent.json') if (job_dir / 'incumbent.json').exists() else None
    diagnostic = read_json(job_dir / 'best-diagnostic.json') if (job_dir / 'best-diagnostic.json').exists() else None
    keys = ('index', 'seed', 'board_sha256', 'errors', 'engine_violations')
    report['incumbent'] = incumbent and {k: incumbent.get(k) for k in keys}
    report['best_diagnostic'] = diagnostic and {k: diagnostic.get(k) for k in keys}
    # Exploratory routing of the best diagnostic candidate is allowed (rule 7); it is never valid.
    selected, report['routed_candidate'] = ((incumbent, 'incumbent') if incumbent is not None
                                            else (diagnostic, 'best_diagnostic_exploratory'))
    if selected is None:
        _check_stop(clock, job_dir)
        report['status'] = 'cancelled' if _cancelled(job_dir) else 'no_checked_candidate'
        report['phases'] = clock.phases
        write_json(job_dir / f'job-report-a{attempt}.json', report)
        write_json(job_dir / 'job-report.json', report)
        return report
    # Placement score orders attempts, but only fresh routing and saved-board
    # verification can select a routed result. Keep the placement incumbent
    # separately: a lower-ranked placement may be the first routable one.
    queue = [selected]
    seen = {selected['board_sha256']}
    if incumbent is not None:
        for entry in sorted((e for e in trials if _placement_eligible(e)), key=_rank):
            if entry['board_sha256'] not in seen:
                queue.append(entry)
                seen.add(entry['board_sha256'])
    report['routing_trials'] = []
    for number, selected in enumerate(queue):
        if number:
            report['routed_candidate'] = 'shortlist_alternative'
        suffix = f'a{attempt}' + (f'-r{number + 1}' if number else '')
        # Equal shares of the remaining job time prevent one failed routing
        # search from taking the entire portfolio budget. This is scheduling
        # policy, not an engineering relaxation; the outer deadline still wins.
        budget = clock.remaining() / (len(queue) - number)
        trial = {'seed': selected['seed'], 'board_sha256': selected['board_sha256'],
                 'directory': selected['directory'], 'status': 'running', 'router_budget_s': budget,
                 'budget_basis': 'agent-default/equal-remaining-share'}
        report['routing_trials'].append(trial)
        with clock.phase('export'):
            _check_stop(clock, job_dir)
            routed = route(selected['directory'], job_dir / f'route-{suffix}', config['router']['checkout'],
                           budget_s=budget, timeout_s=clock.remaining(), home=job_dir / f'route-home-{suffix}',
                           clock=clock, cancel_dir=job_dir, design_name=report['design_name'],
                           grid_step_mm=config['router'].get('grid_step_mm'),
                           intra_pair_match=matching, planes=plane_bytes, pair_layers=pair_layers, pair_impedance=impedance,
                           bus=bus, length_match=length_match, ac_match=ac_match)
            report['route'] = {k: v for k, v in routed.items() if k != 'preflight'}
            report['router_identity'] = {k: routed['preflight'][k] for k in ('commit', 'pin', 'executable_sha256',
                                                                           'adapter_sha256', 'problems')}
        with clock.phase('final_validation'):
            _check_stop(clock, job_dir)
            report['final'] = final_checks(routed, selected, owners, base_keys, job_dir / f'final-{suffix}', groups, intent,
                                           clock=clock, cancel_dir=job_dir,
                                           owned_zone_uuids=plane_ids, planes=plane_bytes,
                                           pair_impedance=impedance, router_checkout=config['router']['checkout'],
                                           length_match=length_match, ac_match=ac_match)
        _check_stop(clock, job_dir)
        status = _completion_status(routed, selected if incumbent is not None else None, report['final'])
        trial.update(status=status, route=report['route'], final=report['final'])
        receipt = job_dir / f'routing-trial-{suffix}.json'
        write_json(receipt, trial)
        state.setdefault('routing_trials', []).append({'path': str(receipt), 'sha256': digest(receipt)})
        checkpoint(job_dir, state)
        report['route_selection'] = {k: trial[k] for k in ('seed', 'board_sha256', 'directory', 'status')}
        if status == 'completed':
            tmp = job_dir / '.routed-incumbent.json.tmp'
            write_json(tmp, {'placement': selected, 'trial': str(receipt), 'trial_sha256': digest(receipt)})
            os.replace(tmp, job_dir / 'routed-incumbent.json')
            break
    _check_stop(clock, job_dir)
    report['phases'] = clock.phases
    report['elapsed_s'] = round(time.monotonic() - clock.start, 3)
    report['status'] = status
    report['validity'] = ('integrity-valid placement; physical checks ' + report['final'].get('physical_checks', 'unavailable')
                          if incumbent is not None else
                          'NO valid incumbent: exploratory route of the best diagnostic candidate (engine violations: '
                          + ', '.join(sorted({v['kind'] for v in selected.get('engine_violations', [])})) + ')')
    write_json(job_dir / f'job-report-a{attempt}.json', report)
    write_json(job_dir / 'job-report.json', report)
    return report


def _completion_status(routed, incumbent, final):
    """Native rejection or absent checks cannot be overridden by router success."""
    if routed.get('status') != 'routed':
        return 'route_failed'
    if routed.get('router_accepted') is False:
        return 'verification_failed'
    if final.get('integrity') != 'preserved':
        return 'integrity_failed'
    if final.get('edge_intent', {}).get('status') != 'pass':
        return 'intent_failed'
    if final.get('eligibility') != 'eligible':
        return 'verification_failed'
    if incumbent is None:
        return 'placement_failed'
    return 'completed'


def final_checks(routed, incumbent, owners, base_keys, job_dir, groups, intent, *, clock=None, cancel_dir=None,
                 owned_zone_uuids=(), planes=None, pair_impedance=None, router_checkout=None, length_match=None, ac_match=None):
    import pcbnew
    clock = clock or Clock(300)
    _check_stop(clock, cancel_dir)
    if routed.get('status') != 'routed':
        return {'status': 'unavailable', 'reason': routed.get('status')}
    final_dir = Path(job_dir)
    final_dir.mkdir()
    shutil.copyfile(routed['board'], final_dir / 'board.kicad_pcb')
    board = final_dir / 'board.kicad_pcb'
    problems = []
    rule_files = {}
    for suffix in ('.kicad_pro', '.kicad_dru'):
        authored = Path(incumbent['directory']) / ('board' + suffix)
        emitted = Path(routed['board']).with_suffix(suffix)
        expected = authored.read_bytes() if authored.exists() else None
        actual = emitted.read_bytes() if emitted.exists() else None
        if actual != expected:
            problems.append(f'router changed or lost native {suffix} file')
        # Always grade against the authored rules, including on a failed route.
        if expected is not None:
            board.with_suffix(suffix).write_bytes(expected)
        rule_files[suffix] = {'authored_sha256': sha256_bytes(expected) if expected is not None else None,
                              'routed_sha256': sha256_bytes(actual) if actual is not None else None}
    plane_fill = None
    if planes is not None:
        # Refill and save through the existing zone-only producer. Its strict
        # post-routing mode cannot defer disconnected planned copper.
        try:
            plane_fill = _produce_planes(final_dir, planes, final_dir.parent / (final_dir.name + '-planes'),
                                         clock=clock, cancel_dir=cancel_dir)
        except JobRefusal as exc:
            problems.append(str(exc))
    placed = native_inventory(pcbnew.LoadBoard(incumbent['board']))
    final = native_inventory(pcbnew.LoadBoard(str(board)))
    if set(final) != set(placed):
        problems.append('router changed the footprint UUID set')
    for uuid, row in placed.items():
        new = final.get(uuid, {})
        for key in ('ref', 'side', 'locked', 'fpid', 'x_nm', 'y_nm', 'rotation_deg', 'pads', 'graphics'):
            if new.get(key) != row[key]:
                problems.append(f'{uuid}: router changed {key}')
    # Compare complete saved native nodes, not a lossy API projection. This
    # includes all padstack geometry/layers, fields, graphics, rules and drawings.
    # Zone ownership is supplied by the controller, never by router output.
    placed_nodes = _non_footprint_nodes(Path(incumbent['board']).read_bytes(), include_footprints=True,
                                         routing_owned=True, owned_zones=owned_zone_uuids)
    final_nodes = _non_footprint_nodes(board.read_bytes(), include_footprints=True, routing_owned=True,
                                      owned_zones=owned_zone_uuids)
    if sorted(placed_nodes, key=canonical) != sorted(final_nodes, key=canonical):
        problems.append('router changed protected native content (footprints, pads, graphics, setup, outline or drawings)')
    placed_report = read_json(Path(incumbent['directory']) / 'native-drc.json')
    placed_keys = {_key(v) for v in placed_report['violations'] if v.get('severity') == 'error'}
    _check_stop(clock, cancel_dir)
    report_, exec_ = drc(board, final_dir, refill=True, label='native-drc', clock=clock, cancel_dir=cancel_dir)
    plane_result = None
    if planes is not None:
        out = final_dir.parent / (final_dir.name + '-plane-verification')
        done = _run_child([sys.executable, '-B', '-m', 'agent_ee.final_planes', 'validate',
            '--inputs', str(final_dir), '--board', board.name, '--plan', 'plane-plan.json',
            '--out', str(out), '--drc', str(final_dir / 'native-drc.json'),
            '--timeout', str(max(0.001, min(180, clock.remaining())))], clock=clock, cancel_dir=cancel_dir)
        plane_result = read_json(out / 'result.json') if (out / 'result.json').is_file() else {
            'status': 'FAIL', 'errors': [done.stderr.decode(errors='replace')[-2000:]]}
        if (done.returncode != 0 or plane_result.get('status') != 'PASS'
                or plane_result.get('board_sha256') != digest(board)
                or plane_result.get('plan_sha256') != sha256_bytes(planes)):
            problems.append('final saved plane verification failed')
    impedance_result = None
    if pair_impedance is not None:
        plan = routed.get('impedance_plan')
        impedance_result = {'status': 'FAIL', 'defects': ['router omitted or changed required impedance plan']}
        if isinstance(plan, dict) and plan.get('requirement') == pair_impedance and plan.get('status') == 'PASS':
            request, result = final_dir / 'impedance-plan.json', final_dir / 'impedance.json'
            write_json(request, plan)
            checker = Path(router_checkout) / 'src/kicad_route/pair_impedance.py'
            done = _run_child([sys.executable, '-B', str(checker), str(board), str(request), str(result), '--check'],
                              clock=clock, cancel_dir=cancel_dir)
            impedance_result = read_json(result) if result.is_file() else {
                'status': 'FAIL', 'defects': [done.stderr.decode(errors='replace')[-2000:]]}
            if done.returncode != 0 or impedance_result.get('board_sha256') != digest(board):
                impedance_result['status'] = 'FAIL'
                impedance_result.setdefault('defects', []).append('final impedance result does not bind the saved board')
    length_result = None
    if length_match is not None:
        out = final_dir.parent / (final_dir.name + '-length-verification')
        out.mkdir()
        request, result = out / 'request.json', out / 'result.json'
        write_json(request, length_match)
        checker = Path(router_checkout) / 'src/kicad_route/length_groups.py'
        done = _run_child([sys.executable, '-B', str(checker), str(board), str(request), str(result)],
                          clock=clock, cancel_dir=cancel_dir)
        length_result = read_json(result) if result.is_file() else {
            'status': 'FAIL', 'defects': [done.stderr.decode(errors='replace')[-2000:]]}
        if (done.returncode != 0 or length_result.get('board_sha256') != digest(board)
                or length_result.get('requirement') != length_match):
            length_result['status'] = 'FAIL'
            length_result.setdefault('defects', []).append('final length result does not bind the saved board and requirement')
    ac_result = None
    if ac_match is not None:
        out = final_dir.parent / (final_dir.name + '-ac-verification')
        out.mkdir()
        request, result = out / 'request.json', out / 'result.json'
        write_json(request, {'requirement': ac_match})
        checker = Path(router_checkout) / 'src/kicad_route/ac_matching.py'
        done = _run_child([sys.executable, '-B', str(checker), str(board), str(request), str(result)],
                          clock=clock, cancel_dir=cancel_dir)
        ac_result = read_json(result) if result.is_file() else {
            'status': 'FAIL', 'defects': [done.stderr.decode(errors='replace')[-2000:]]}
        if (done.returncode != 0 or ac_result.get('board_sha256') != digest(board)
                or ac_result.get('requirement') != ac_match):
            ac_result['status'] = 'FAIL'
            ac_result.setdefault('defects', []).append('final AC result does not bind the saved board and requirement')
    split = classify_errors(report_, owners, base_keys, placed_keys)
    edge_intent = check_edge_intent(board, intent, clock=clock, cancel_dir=cancel_dir)
    locality = check_locality(board, intent, clock=clock, cancel_dir=cancel_dir)
    metrics = board_metrics(board, groups['groups'])
    metrics['edges'] = edge_intent['connectors']
    failed = (problems or any(split.values()) or report_['unconnected_items'] or edge_intent['status'] != 'pass'
              or locality['status'] not in ('pass', 'info', 'not_declared')
              or impedance_result is not None and impedance_result.get('status') != 'PASS'
              or length_result is not None and length_result.get('status') != 'PASS'
              or ac_result is not None and ac_result.get('status') != 'PASS')
    return {'status': 'checked', 'board': str(board), 'board_sha256': digest(board),
            **({'planes': plane_result, 'plane_fill': plane_fill} if planes is not None else {}),
            **({'impedance': impedance_result} if impedance_result is not None else {}),
            **({'length_matching': length_result} if length_result is not None else {}),
            **({'ac_matching': ac_result} if ac_result is not None else {}),
            'integrity': 'violated' if problems else 'preserved',
            'eligibility': 'INELIGIBLE' if failed else 'eligible',
            'identity_problems': problems, 'rule_files': rule_files,
            'drc': exec_, 'opens': len(report_['unconnected_items']),
            'errors': _error_summary(split), 'warnings': _warnings(report_),
            'physical_checks': 'FAILED' if failed else 'no native errors',
            'edge_intent': edge_intent, 'locality': locality, 'metrics': metrics}


def preflight(config, executable=None, *, clock=None, cancel_dir=None, identities=None):
    """Runtime readiness before the budgeted work: imports, toolchain, engine and router."""
    import pcbnew
    from . import routing_adapter
    identities = {} if identities is None else identities
    identities.update(pcbnew_version=pcbnew.Version(), python=sys.version.split()[0])
    if 'planes' in config['inputs']:
        from .cli import policy_files
        identities['plane_tools'] = sha256_bytes(canonical(policy_files()))
    engine = Path(executable or rust_executable())
    identities.update(engine=str(engine), engine_sha256=digest(engine))
    cli = _run_child(['kicad-cli', 'version'], clock=clock or Clock(60), cancel_dir=cancel_dir)
    identities['kicad_cli'] = cli.stdout.decode().strip()
    router = routing_adapter.preflight(config['router']['checkout'])
    identities['router'] = {k: router.get(k) for k in (
        'checkout', 'commit', 'pin', 'executable_sha256', 'adapter_sha256')}
    problems = list(router['problems'])
    if cli.returncode != 0:
        problems.append('kicad-cli unavailable')
    if problems:
        raise JobRefusal('runtime preflight failed: ' + '; '.join(problems))
    return identities


def configure_parser(parser, *, internal=False):
    sub = parser.add_subparsers(dest='placement_command', required=True)
    if internal:
        for command in ('_capture', '_check_edges', '_check_locality'):
            native = sub.add_parser(command, help=argparse.SUPPRESS)
            native.add_argument('board')
    run = sub.add_parser('run', help='run one whole placement-to-route job')
    run.add_argument('config')
    run.add_argument('--out', required=True)
    run.add_argument('--resume', action='store_true')
    metrics = sub.add_parser('metrics', help='measure a saved board read-only with the job extractor')
    metrics.add_argument('board')
    metrics.add_argument('--groups')
    metrics.add_argument('--intent')


def execute(args, parser):
    if args.placement_command in ('_capture', '_check_edges', '_check_locality'):
        request = json.load(sys.stdin)
        try:
            if args.placement_command == '_capture':
                problem, record = _capture_native(args.board, request['intent'], request['groups'])
                result = {'problem': problem, 'record': record}
            elif args.placement_command == '_check_locality':
                from .placement_probe import extract
                result = _locality_grade(extract(args.board), request['intent']['electrical_locality'])
            else:
                result = _check_edge_intent_native(args.board, request['intent'])
        except JobRefusal as exc:
            print(json.dumps({'error': str(exc)}))
            return 2
        print(json.dumps(result))
        return 0
    if args.placement_command == 'run':
        config = read_json(args.config)
        if config.get('schema') != CONFIG_SCHEMA:
            parser.error('unsupported job config schema')
        report = run_job(config, args.out, resume=args.resume)
        print(json.dumps({k: report.get(k) for k in ('status', 'phases', 'elapsed_s', 'incumbent')}, indent=1))
        return 0 if report['status'] == 'completed' else 1
    groups = read_json(args.groups)['groups'] if args.groups else None
    metrics = board_metrics(args.board, groups)
    if args.intent:
        metrics['edge_intent'] = check_edge_intent(args.board, read_json(args.intent))
        metrics['edges'] = metrics['edge_intent']['connectors']
    print(json.dumps(metrics, indent=1))
    return 1 if metrics.get('edge_intent', {}).get('status') == 'failed' else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.placement_job')
    configure_parser(parser, internal=True)
    return execute(parser.parse_args(argv), parser)


if __name__ == '__main__':
    raise SystemExit(main())
