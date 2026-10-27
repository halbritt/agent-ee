"""One whole-board placement-to-route job over one prepared problem (AGENTEE-515).

Order (chief 531, b4a5857a): native capture -> Rust prepared model with
incremental search -> shortlist native materialize/save/reload/constraint
check -> pinned router process -> final save/reload/native checks.

The controller is the only orchestration layer. Individual search moves stay
inside one Rust process (``agent-ee-placement``, schema
``agent-ee.placement-job-input/2``; the engine replays ``/1`` only under the
labelled legacy pad-centre contract); KiCad and the router are process adapters.
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
import shutil
import subprocess
import sys
import time

from . import native_run, placement_problem
from .placement_problem import (INPUT_SCHEMA, OUTPUT_SCHEMA, JobRefusal, escape_hints,  # noqa: F401
                                quarter_turns as _quarter_turns, rule_mm as _rule_mm)
from .storage import canonical, digest, read_json, write_json
from .run_record import policy_files

JOB_SCHEMA = 'agent-ee.placement-job/1'
CONFIG_SCHEMA = 'agent-ee.placement-job-config/1'
# INPUT_SCHEMA (agent-ee.placement-job-input/2) is built by placement_problem.
# Historical AGENTEE-515 prepared jobs. The engine still reads them under the visible
# `legacy_pad_centre/1` proximity contract (pad-centre Chebyshev, no group links); a fresh
# capture always emits INPUT_SCHEMA with pad half extents and the engine refuses a /2 record
# whose pad_edge rows lack them. See docs/intent-dialect.md "Capture contract".
LEGACY_INPUT_SCHEMA = 'agent-ee.placement-job-input/1'
CHECKPOINT_SCHEMA = 'agent-ee.placement-job-checkpoint/1'
PHASES = ('setup', 'import', 'prepare', 'search', 'judge', 'export', 'final_validation')
SILK = ('F.Silkscreen', 'B.Silkscreen')
FAB = {'F': 'F.Fab', 'B': 'B.Fab'}
SILK_OF = {'F': 'F.Silkscreen', 'B': 'B.Silkscreen'}
COPPER = ('F.Cu', 'B.Cu')
NM = 1_000_000


class JobStopped(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def _check_stop(clock, cancel_dir):
    if cancel_dir is not None and _cancelled(cancel_dir):
        raise JobStopped('cancelled')
    if clock.remaining() <= 0:
        raise JobStopped('deadline')


class _Discard:
    """A sink that keeps nothing: on_line consumes the stream and memory stays flat."""
    def write(self, chunk):
        return len(chunk)

    def flush(self):
        pass


def _run_child(argv, *, clock, cancel_dir=None, input_bytes=b'', on_line=None,
               stdout=None, stderr=None, env=None, cwd=None):
    """One host native run bounded by the job clock and the CANCEL file.

    native_run owns the process group (router/simulator descendants included), the nonblocking
    pipes and the kill. Its timeout is what the job clock has left, so it never waits past the
    deadline, and its per-poll checkpoint raises JobStopped on CANCEL or an expired clock; either
    stops and reaps the group before JobStopped('deadline'|'cancelled') propagates. stdout/stderr,
    when given, receive the streams live; stdout consumed by on_line is not kept. Returns what
    subprocess.run would: stdout is empty when it went to a file or to on_line, and stderr is
    always kept.
    """
    _check_stop(clock, cancel_dir)
    intent = native_run.Intent('placement_child', argv, timeout=clock.remaining(), isolation='host',
                               stdin=input_bytes or None, env=env, cwd=None if cwd is None else str(cwd))
    sink = stdout if stdout is not None else _Discard() if on_line is not None else None
    done = native_run.run(intent, work=None, on_tick=lambda: _check_stop(clock, cancel_dir), on_line=on_line,
                          sink=sink, stderr_sink=stderr)
    if done['execution'] == 'unavailable':
        native_run.completed(done)   # raises Popen's own launch error (errno, strerror, filename)
    # The job clock and CANCEL decide, not the child's own limit (which started counting later):
    # a run that ends after the deadline or a cancel is stopped, never a completion.
    _check_stop(clock, cancel_dir)
    if done['execution'] == 'timeout':
        raise JobStopped('deadline')
    output = b'' if sink is not None else done.stdout
    return subprocess.CompletedProcess(argv, done.returncode, output, done.stderr)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def verified_input(entry, label):
    """Read one external input by declared SHA-256; fixture bytes never enter the repository."""
    path = Path(entry['path'])
    data = path.read_bytes()
    if sha256_bytes(data) != entry['sha256']:
        raise JobRefusal(f'{label} {path} does not match its declared sha256')
    return path, data


# ---------------------------------------------------------------- capture

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


def _pad_half(pad, origin, q):
    """Axis-aligned half extents of the pad copper rect in the local frame (AGENTEE-479 pad_edge)."""
    import pcbnew
    if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH or not pad.IsOnCopperLayer():
        return [0, 0]
    if not any(pad.IsOnLayer(layer) for layer in pad.GetBoard().GetEnabledLayers().CuStack()):
        return [0, 0]
    if pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS) == pcbnew.PAD_SHAPE_CUSTOM:
        pos = pad.GetPosition()
        box = pad.GetEffectiveShape(pad.GetLayer()).BBox()
        hx = max(abs(int(box.GetLeft()) - int(pos.x)), abs(int(box.GetRight()) - int(pos.x)))
        hy = max(abs(int(box.GetTop()) - int(pos.y)), abs(int(box.GetBottom()) - int(pos.y)))
        return [hy, hx] if q % 2 else [hx, hy]
    size = pad.GetSize(pcbnew.PADSTACK.ALL_LAYERS)
    theta = math.radians(pad.GetOrientation().AsDegrees() - 90.0 * q)
    cos, sin = abs(math.cos(theta)), abs(math.sin(theta))
    hx = int(round((int(size.x) / 2) * cos + (int(size.y) / 2) * sin))
    hy = int(round((int(size.x) / 2) * sin + (int(size.y) / 2) * cos))
    return [hx, hy]


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


def check_edge_intent(path, normalized, *, clock=None, cancel_dir=None):
    """Grade the saved board in a bounded fresh process, including saved settings."""
    checked = _native_read(path, '_check_edges', {'intent': _bundle(normalized)['intent']},
                           clock=clock, cancel_dir=cancel_dir)
    if (checked.get('status') not in ('pass', 'failed')
            or checked.get('coverage') != ['edge_identity']
            or not isinstance(checked.get('connectors'), list)
            or not isinstance(checked.get('violations'), list)):
        raise JobRefusal('native edge check returned missing or invalid output')
    return checked


def check_locality(path, normalized, *, clock=None, cancel_dir=None):
    """Independently grade the saved native copper centroids, never solver costs."""
    intent = _bundle(normalized)['intent']
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


def _evaluate_board_native(path, *, fixed_poses=(), edges=(), groups=(), rails=None, proximity=None,
                           board=None, before=None):
    """Evaluate saved native geometry through the Rust placement engine (read-only).

    The document comes from the same builder as capture, so the role rule, the
    weights and the engine output check are the solver's.
    """
    from .placement_probe import extract
    if before is None:
        before = Path(path).read_bytes()
    if board is None:
        import pcbnew
        board = pcbnew.LoadBoard(str(path))
    payload = placement_problem.evaluate(_describe_board(board, extract(path, board=board, before=before)),
                                         fixed_poses=fixed_poses, edges=edges, groups=groups, rails=rails,
                                         proximity=proximity)
    done = _run_child([str(rust_executable())], clock=Clock(60), input_bytes=json.dumps(payload).encode())
    try:
        output = json.loads(done.stdout)
    except ValueError:
        output = None
    placement_problem.check_output(output, payload, code=done.returncode,
                                   detail=(done.stdout + done.stderr).decode(errors='replace')[-2000:])
    if Path(path).read_bytes() != before:
        raise JobRefusal('board changed during final edge-intent checks')
    evaluations = output.get('evaluations')
    if output.get('status') != 'evaluated' or not isinstance(evaluations, list) or len(evaluations) != 1:
        raise JobRefusal('placement engine returned no evaluation of the saved board')
    return evaluations[0]


def _check_edge_intent_native(path, intent):
    """Reload native geometry and grade declared edges independently of solver output."""
    if not intent.get('edge_connectors'):
        return {'status': 'pass', 'connectors': [], 'violations': [], 'coverage': ['edge_identity']}
    return _evaluate_board_native(path, fixed_poses=intent.get('fixed_poses', []),
                                  edges=intent['edge_connectors'])['edge_intent']


def _on_mask(pad):
    import pcbnew
    return pad.IsOnLayer(pcbnew.F_Mask) or pad.IsOnLayer(pcbnew.B_Mask)


def _mask_expansion(pad):
    import pcbnew
    layer = pcbnew.F_Mask if pad.IsOnLayer(pcbnew.F_Mask) else pcbnew.B_Mask
    return max(0, int(pad.GetSolderMaskExpansion(layer)))


def _mask_graphic_bbox(graphic):
    """Match native DRC: only PCB_SHAPE adds a resolved mask margin."""
    import pcbnew
    margin = (max(0, int(graphic.GetSolderMaskExpansion()))
              if graphic.Type() == pcbnew.PCB_SHAPE_T else 0)
    return _grow(_bbox(graphic), margin)


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def classify_footprint(fp, captured):
    """Native facts about one footprint that the role rule and its body occupancy need.

    Only demonstrably silk-only, padless, zone-free graphics are excluded from
    packing. A footprint whose pads are all unnetted and that draws no body
    (fab, silk or courtyard) is mark-eligible: if intent fixes it, it is a fixed
    copper mark, a copper obstacle and never a body. Every other footprint gets
    an occupancy with its provenance rung; a malformed courtyard or missing
    geometry is returned as ``refusal``, raised only if its role needs a body.
    """
    import pcbnew
    uuid = fp.m_Uuid.AsString()
    side = captured['side']
    if side not in ('F', 'B'):
        raise JobRefusal(f'{uuid}: unsupported footprint layer')
    graphics = [g for g in fp.GraphicalItems()]
    layers = Counter(pcbnew.LayerName(g.GetLayer()) for g in graphics)
    pads = list(fp.Pads())
    zones = list(fp.Zones())
    classified = {'User.Drawings': 'documentation drawing; not treated as a package outline'} \
        if layers.get('User.Drawings') else {}
    mask = [_grow(_bbox(p), _mask_expansion(p)) for p in pads if _on_mask(p)] + [
        _mask_graphic_bbox(g) for g in graphics if pcbnew.LayerName(g.GetLayer()) in ('F.Mask', 'B.Mask')]
    fab = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == FAB[side] and g.GetClass() == 'PCB_SHAPE']
    silk = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == SILK_OF[side] and g.GetClass() == 'PCB_SHAPE']
    facts = {'silk_only': False, 'mark_eligible': False, 'occupancy': None, 'refusal': None, 'diagnostics': [],
             'mask': mask, 'classified_layers': classified,
             'seats': {'body:fab': _union([_bbox(g) for g in fab]) if fab else None,
                       'body:silk': _union([_bbox(g) for g in silk]) if silk else None}}
    if not pads and not zones and graphics and set(layers) <= set(SILK):
        return dict(facts, silk_only=True, mask=[])
    if zones:
        raise JobRefusal(f'{uuid}: footprint rule areas/zones are not supported by the job model')
    body_layers = {FAB[side], SILK_OF[side], f'{side}.CrtYd'} & set(layers)
    facts['mark_eligible'] = bool(pads) and not body_layers and all(not p.GetNetname() for p in pads)
    courtyards = captured['courtyards']
    for court_side, record in courtyards.items():
        if record.get('status') != 'proven':
            return dict(facts, refusal=f'{uuid} ({fp.GetReference()!r}): malformed courtyard on {court_side}.CrtYd: '
                                       f'{record.get("status")} {record.get("reason", "")}'.strip())
    if side in courtyards:
        boxes = [[min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)]
                 for poly in courtyards[side]['polygons']]
        return dict(facts, occupancy={'status': 'courtyard', 'rung': 'courtyard', 'box': _union(boxes),
                                      'sources': ['courtyard']})
    pad_boxes = [_bbox(p) for p in pads]
    diagnostics = [f'{g.m_Uuid.AsString()}: empty drawing bounds' for g in fab + silk
                   if (b := _bbox(g))[0] > b[2] or b[1] > b[3]]
    if fab:
        rung, source = 'fab+pads', fab
    elif silk:
        rung, source = 'silk+pads', silk
    elif pad_boxes:
        rung, source = 'pads', []
    else:
        return dict(facts, refusal=f'{uuid}: no courtyard, fab, silk or pad geometry for derived occupancy')
    boxes = [_bbox(g) for g in source] + pad_boxes
    diagnostics.append(f'courtyard missing (metadata gap): derived_occupancy rung {rung}')
    return dict(facts, diagnostics=diagnostics,
                occupancy={'status': 'derived', 'label': 'derived_occupancy', 'rung': rung, 'box': _union(boxes),
                           'sources': sorted(g.m_Uuid.AsString() for g in source) + ['pads'],
                           'note': 'derived envelope; separation is not proof of package body separation'})


def _describe_board(board, facts):
    """Read a loaded native board into the description ``placement_problem`` builds from (no intent)."""
    import pcbnew
    by_uuid = {f['uuid']: f for f in facts['footprints']}
    if len(by_uuid) != len(facts['footprints']):
        raise JobRefusal('duplicate native footprint UUID')
    settings = board.GetDesignSettings()
    native_class = settings.m_NetSettings.GetDefaultNetclass()
    clearance = _clearance(settings)
    footprints = []
    for fp in board.GetFootprints():
        uuid = fp.m_Uuid.AsString()
        captured = by_uuid[uuid]
        q = _quarter_turns(captured['rotation_deg'], uuid)
        origin = captured['position_nm']
        native = classify_footprint(fp, captured)
        box = (native['occupancy'] or {}).get('box')
        footprints.append({
            'uuid': uuid, 'ref': captured['ref'], 'side': captured['side'], 'locked': captured['locked'],
            'position_nm': origin, 'rotation_deg': captured['rotation_deg'], 'quarter_turns': q,
            'silk_only': native['silk_only'], 'mark_eligible': native['mark_eligible'],
            'occupancy': native['occupancy'], 'occupancy_refusal': native['refusal'],
            'occupancy_diagnostics': native['diagnostics'],
            'occupancy_local_nm': _local_rect(box, origin, q) if box else None,
            'classified_layers': native['classified_layers'],
            'copper_local_nm': [c for p in fp.Pads() if (c := _pad_copper(p, origin, q)) is not None]
                + [{'rect_nm': _local_rect(_bbox(g), origin, q), 'clearance_nm': 0}
                   for g in fp.GraphicalItems() if pcbnew.LayerName(g.GetLayer()) in COPPER],
            'mask_local_nm': [_local_rect(b, origin, q) for b in native['mask']],
            'holes_local_nm': [h for p in fp.Pads()
                               if (h := _pad_hole(p, origin, q, int(settings.m_HoleClearance))) is not None],
            'pads': [{'uuid': p.m_Uuid.AsString(), 'number': p.GetNumber(), 'net': p.GetNetname(),
                      'local_nm': list(_unrotate(int(p.GetPosition().x) - origin[0],
                                                 int(p.GetPosition().y) - origin[1], q)),
                      'half_nm': _pad_half(p, origin, q)}
                     for p in fp.Pads()],
            'seats_local_nm': {basis: _local_rect(rect, origin, q) if rect else None
                               for basis, rect in native['seats'].items()}})
    return {'outline_rect_nm': _rectangle(facts['outline']),
            'rules': {'copper_clearance_nm': clearance,
                      'hole_to_hole_clearance_nm': int(settings.m_HoleToHoleMin),
                      'copper_edge_clearance_nm': int(settings.m_CopperEdgeClearance),
                      'mask_min_web_nm': int(settings.m_SolderMaskMinWidth)},
            'pair': {'width_nm': int(native_class.GetDiffPairWidth()) if native_class.HasDiffPairWidth() else None,
                     'gap_nm': int(native_class.GetDiffPairGap()) if native_class.HasDiffPairGap() else None,
                     'clearance_nm': clearance},
            'footprints': footprints}


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


NATIVE_READ_LABELS = {'_capture': 'capture', '_check_edges': 'edge check', '_check_locality': 'locality check',
                      '_nets': 'net membership read'}
BUNDLE_KEYS = frozenset(('intent', 'groups', 'rails', 'intent_contract', 'compatibility'))


def _native_read(board_path, command, request, *, clock=None, cancel_dir=None):
    """Read native files in a fresh process, bounded by the caller's job lifetime.

    KiCad caches projects by path, including fields omitted from a later saved
    project. Only a fresh native load gives all defaults the same meaning as
    independent native DRC. No native settings or project files are rewritten.
    """
    board_path = Path(board_path).absolute()
    sources = (board_path, board_path.with_suffix('.kicad_pro'), board_path.with_suffix('.kicad_dru'))
    before = [p.read_bytes() if p.exists() else None for p in sources]
    label = NATIVE_READ_LABELS[command]
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


def capture(board_path, normalized, *, clock=None, cancel_dir=None):
    """Capture native geometry and settings without inheriting cached defaults.

    ``normalized`` is the bundle from :func:`normalize_intent`; the child
    receives it whole, checks it is a bundle and never re-normalizes.
    """
    captured = _native_read(board_path, '_capture', {'normalized': _bundle(normalized)},
                            clock=clock, cancel_dir=cancel_dir)
    try:
        problem, record = captured['problem'], captured['record']
    except KeyError as exc:
        raise JobRefusal('native capture returned missing or invalid output') from exc
    if (not isinstance(problem, dict) or problem.get('schema') != INPUT_SCHEMA
            or not isinstance(record, dict) or record.get('schema') != 'agent-ee.placement-job-capture/1'):
        raise JobRefusal('native capture returned missing or invalid output')
    return problem, record


def _bundle(normalized):
    """The normalized intent bundle; a raw intent document is refused, never guessed at."""
    if not isinstance(normalized, dict) or not BUNDLE_KEYS <= set(normalized):
        raise JobRefusal('expected the normalized intent bundle from normalize_intent, not a raw intent document')
    return normalized


def _rail_membership(board_path, *, clock=None, cancel_dir=None):
    """Board net membership for binding a group-rails sidecar: ``nets`` and ``net_parts``.

    Read once at job entry in a fresh native process and passed to the single
    :func:`normalize_intent` call, which derives each net's groups from its parts.
    """
    read = _native_read(board_path, '_nets', {}, clock=clock, cancel_dir=cancel_dir)
    net_parts = read.get('net_parts')
    if not isinstance(net_parts, dict) or any(not isinstance(v, list) for v in net_parts.values()):
        raise JobRefusal('native net membership read returned missing or invalid output')
    return {'nets': set(net_parts), 'net_parts': {net: set(refs) for net, refs in net_parts.items()}}


def _rail_membership_native(board_path):
    import pcbnew
    net_parts = {}
    for fp in pcbnew.LoadBoard(str(board_path)).GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname():
                net_parts.setdefault(pad.GetNetname(), set()).add(fp.GetReference())
    return {'net_parts': {net: sorted(refs) for net, refs in sorted(net_parts.items())}}


DIALECT_SUPPORTED = frozenset(('intent.assembly.sides', 'intent.envelope', 'edge.side', 'edge.cable_entry',
                               'intent.proximity', 'groups.near'))


def _check_dialect_native(board, facts, record):
    """Verify the operative dialect checks against the native board (AGENTEE-479 capture hooks)."""
    import pcbnew
    checks = []
    by_ref = {f['ref']: f for f in facts['footprints']}
    for row in record['dispositions']:
        if row['disposition'] != 'operative_pending_capture':
            continue
        path = row['path']
        if path == 'intent.assembly.sides':
            wrong = sorted(f['ref'] for f in facts['footprints'] if f.get('side') and f['side'] != row['value']
                           and any(True for _ in (board.FindFootprintByReference(f['ref']) or pcbnew.FOOTPRINT(board)).Pads()))
            if wrong:
                raise JobRefusal(f'{path}: footprints not on declared side {row["value"]}: ' + ', '.join(wrong))
            checks.append({'path': path, 'verified': 'all pad-bearing footprints on side ' + row['value']})
        elif path == 'intent.envelope':
            box = board.GetBoardEdgesBoundingBox()
            native = [pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()), pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())]
            tolerance = row['tolerance_mm']
            if any(abs(a - b) > tolerance for a, b in zip(native, row['rect'])):
                raise JobRefusal(f'{path}: native Edge.Cuts bbox {native} differs from declared rect {row["rect"]} by more than {tolerance} mm')
            checks.append({'path': path, 'verified': 'native Edge.Cuts bbox within tolerance', 'native_rect_mm': native})
        elif path.endswith('.side'):
            ref = next(e['ref'] for e in record['native_intent']['edge_connectors']
                       if path.startswith(f"intent.edge_connectors[{record['native_intent']['edge_connectors'].index(e)}]"))
            fp = board.FindFootprintByReference(ref)
            if fp is None:
                raise JobRefusal(f'{path}: connector {ref} is not on the board')
            native_side = 'B' if fp.IsFlipped() else 'F'
            if native_side != row['value']:
                raise JobRefusal(f'{path}: {ref} is on native side {native_side}, declared {row["value"]}')
            checks.append({'path': path, 'verified': f'{ref} on native side {native_side}'})
        elif path.endswith('.context.cable_entry'):
            checks.append({'path': path, 'verified': 'cable entry consistency checked at adaptation; '
                                                     'seating verified by the edge rules', 'value': row['value']})
    return checks


def _capture_native(board_path, normalized):
    """Native capture once; returns (problem without search section, capture record)."""
    intent, dialect = (_bundle(normalized)[k] for k in ('intent', 'compatibility'))
    import pcbnew
    from .placement_probe import extract
    before = Path(board_path).read_bytes()
    project = _project_rules(board_path)
    _validate_project_rules(project)
    bindings = normalized['rails'].get('bindings')
    if bindings is not None and bindings['board_sha256'] != sha256_bytes(before):
        raise JobRefusal('group_rails.bindings.board_sha256: sidecar is bound to a different board')
    board = pcbnew.LoadBoard(str(board_path))
    facts = extract(board_path, board=board, before=before)
    dialect_checks = _check_dialect_native(board, facts, dialect) if dialect else []
    for zone in board.Zones():
        if (not zone.GetIsRuleArea() or zone.GetDoNotAllowFootprints() or zone.GetDoNotAllowPads()
                or zone.GetPlacementAreaEnabled()):
            raise JobRefusal('board-level zones with pours or placement constraints are unsupported; '
                             'routing-only rule areas must allow footprints and pads')
    description = _describe_board(board, facts)
    if Path(board_path).read_bytes() != before:
        raise JobRefusal('input board changed during capture')
    if 'electrical_locality' in intent:
        _locality_grade(facts, intent['electrical_locality'])
    problem, records = placement_problem.capture(
        description, normalized, dialect_checks=dialect_checks,
        locality_footprints=facts['footprints'] if 'electrical_locality' in intent else None)
    return problem, {'schema': 'agent-ee.placement-job-capture/1', 'board_sha256': sha256_bytes(before),
                     'pcbnew_version': facts['pcbnew_version'], 'footprints': records,
                     'native': {f['uuid']: {'ref': f['ref'], 'side': f['side'], 'locked': f['locked']}
                                for f in description['footprints']},
                     'facts_sha256': sha256_bytes(canonical(facts))}


def normalize_intent(intent, groups, *, rails=None, locality=None, board_sha256=None, groups_sha256=None,
                     nets=None, net_groups=None, net_parts=None, source=None):
    """Validate and normalize intent, groups, rails, and locality in one pass."""
    from . import placement_intent
    try:
        return placement_intent.admit_source(
            intent, groups, rails=rails, locality=locality,
            board_sha256=board_sha256, groups_sha256=groups_sha256,
            nets=nets, net_groups=net_groups, net_parts=net_parts,
            source=source, supported=DIALECT_SUPPORTED,
        )
    except ValueError as exc:
        raise JobRefusal(str(exc)) from exc


def validate_intent(intent, groups):
    return normalize_intent(intent, groups)['intent_contract']


# ---------------------------------------------------------------- Rust search

def rust_executable():
    from .placement_runtime import PlanningProcessError
    from .placement_rust_runtime import resolve_executable
    try:
        return resolve_executable()
    except PlanningProcessError:
        if 'AGENT_EE_RUST_PLANNER' in os.environ:
            raise
        root = Path(__file__).resolve().parents[2]
        candidates = [root / 'build/placement-engine/agent-ee-placement']
        git_dir = root / '.git'
        if git_dir.is_file():
            text = git_dir.read_text(errors='replace').strip()
            if text.startswith('gitdir:'):
                common = Path(text.split(':', 1)[1].strip()).resolve().parents[2]
                candidates.append(common / 'build/placement-engine/agent-ee-placement')
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve(strict=True)
        raise


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
    placement_problem.check_output(final, problem, code=code, detail=stderr[-2000:])
    return final, {'executable': str(executable), 'executable_sha256': digest(executable),
                   'input_sha256': sha256_bytes(encoded), 'exit_code': code}


# ---------------------------------------------------------------- native checks

def drc(board, out_dir, *, refill=False, label='drc', clock=None, cancel_dir=None):
    out_dir = Path(out_dir)
    report = out_dir / f'{label}.json'
    argv = native_run.drc_argv(board, report, refill=refill, output_flag='--output')
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


def materialize(source, poses, destination, project_bytes, *, normalized, rules_bytes=None):
    """Apply one pose vector to a disposable native board, save, reload and check.

    Returns the integrity record. Every footprint is addressed by full UUID; pads,
    nets, refs, sides, locks, graphics and all non-footprint board content must
    be unchanged, and every pad must sit where the engine's transform put it."""
    fixed_poses = _bundle(normalized)['intent'].get('fixed_poses', [])
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
        placement_problem.fixed_pose_owners(fixed_poses, captured))
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

def board_metrics(path, groups=None, edges=None, *, rails=None, proximity=None, fixed_poses=()):
    """Measured route length, vias and geometric via-in-pad on a saved board (read-only).

    With ``groups`` the AGENTEE-479 group-link report (signed pad-edge gaps) is
    included; with ``proximity`` rows of basis ``pad_edge`` their per-leg
    measurements are included. Both are measurements and diagnostics only.
    ``fixed_poses`` are the intent's declared fixed poses: the evaluated problem
    uses capture's role rule, so a footprint is fixed only if declared or locked.
    """
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
    declared_edges = ([{'ref': r, 'edge': e} for r, e in edges.items()] if isinstance(edges, dict)
                      else list(edges or ()))
    evaluated = (_evaluate_board_native(path, fixed_poses=fixed_poses, edges=declared_edges, groups=groups or (),
                                        rails=rails, proximity=proximity, board=board, before=data)
                 if (groups or edges or proximity) else None)
    if Path(path).read_bytes() != data:
        raise JobRefusal('board changed during metrics extraction')
    edge_rows = evaluated['edge_intent']['connectors'] if edges else []
    group_links = evaluated['group_links'] if groups else None
    pad_edge_legs = evaluated['pad_edge_legs'] if proximity else None
    return {'board_sha256': sha256_bytes(data), 'track_segments': segments, 'track_arcs': arcs,
            'route_length_mm': {k: round(v / NM, 4) for k, v in sorted(length.items())},
            'route_length_total_mm': round(sum(length.values()) / NM, 4), 'vias': len(vias),
            'via_in_pad': {'count': len(in_pad), 'sites': in_pad, 'method': 'via centre inside native pad copper (HitTest) on F.Cu/B.Cu'},
            'grouping': grouping, 'group_links': group_links, 'pad_edge_legs': pad_edge_legs, 'edges': edge_rows,
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
    from . import routing_adapter
    route_dir.mkdir(parents=True, exist_ok=True)
    with open(route_dir / 'kicad-route.stdout', 'wb') as out, open(route_dir / 'kicad-route.stderr', 'wb') as err:
        done = _run_child(argv, clock=clock, cancel_dir=cancel_dir, stdout=out, stderr=err, env=env, cwd=route_dir)
    return routing_adapter.bind_board_attempt(route_dir, argv, done.returncode)


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
        from . import routing_adapter
        argv = routing_adapter.spokes_argv(checkout, result_path.parent)
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
          bus=None, length_match=None, ac_match=None, layout=None):
    """Pinned kicad-route process on a fresh copy of one materialized candidate.

    The board has no schematic, which the sandboxed routing_adapter step refuses;
    the pinned CLI is invoked with the adapter's own argv shape and identity checks.
    layout (routing_adapter.pinned_layout) overrides the qualified pin."""
    from . import routing_adapter
    clock = clock or Clock(timeout_s)
    _check_stop(clock, cancel_dir)
    check = routing_adapter.preflight(checkout, layout=layout)
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
    argv = routing_adapter.board_route_argv(
        checkout, staged, route_dir / 'out', design_name=design_name, owned_copper=owned_copper,
        budget_s=remaining, grid_step_mm=grid_step_mm, pair_layers=pair_layers,
        pair_impedance=pair_impedance, intra_pair_match=intra_pair_match,
        bus=bus, length_match=length_match, ac_match=ac_match)
    env = routing_adapter.board_route_env(checkout, home)
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
            retry, pads = routing_adapter.board_retry_argv(
                argv, retry_dir, route_clock.remaining(), feedback, pads, record.get('board'))
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
        from . import routing_adapter
        try:
            router_options = routing_adapter.validate_router_options(config['router'])
        except ValueError as exc:
            raise JobRefusal(str(exc)) from exc
        ac_match = router_options['ac_match']
        length_match = router_options['length_match']
        bus = router_options['bus']
        pair_layers = router_options['pair_layers']
        impedance = router_options['pair_impedance']
        matching = router_options['intra_pair_match']
        unknown = set(config['inputs']) - {'board', 'project', 'rules', 'intent', 'groups', 'locality', 'planes', 'group_rails'}
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
        rails = None
        if 'group_rails' in config['inputs']:
            _, rails_bytes = verified_input(config['inputs']['group_rails'], 'group_rails')
            try:
                rails = strict_json(rails_bytes)
            except ValueError as exc:
                raise JobRefusal('inputs.group_rails: ' + str(exc)) from exc
        locality = None
        if 'locality' in config['inputs']:
            _, raw = verified_input(config['inputs']['locality'], 'locality')
            try:
                locality = strict_json(raw)
            except ValueError as exc:
                raise JobRefusal('inputs.locality JSON: ' + str(exc)) from exc
        # The one normalization of this job: every later step receives this bundle, never the raw documents.
        # Same CANCEL convention as capture: a pre-existing CANCEL still reaches the identity checkpoint.
        membership = (_rail_membership(board_path, clock=clock, cancel_dir=None if _cancelled(job_dir) else job_dir)
                      if rails is not None else {})
        normalized = normalize_intent(
            intent, groups, rails=rails, locality=locality,
            board_sha256=sha256_bytes(board_bytes), groups_sha256=sha256_bytes(groups_bytes), **membership,
        )
        report['intent_contract'] = normalized['intent_contract']
        plane_bytes = None
        plane_ids = ()
        if 'planes' in config['inputs']:
            from .add_planes import validate_plan, zone_ids
            _, plane_bytes = verified_input(config['inputs']['planes'], 'planes')
            try:
                plane_plan = validate_plan(strict_json(plane_bytes))
            except ValueError as exc:
                raise JobRefusal('invalid plane plan: ' + str(exc)) from exc
            if any(zone['island_removal'] != 'always' for zone in plane_plan['zones']):
                raise JobRefusal('placement job final planes require island_removal=always')
            plane_ids = zone_ids(plane_plan)
        report['preflight'] = {}
        report['preflight'] = preflight(config, executable, clock=clock, identities=report['preflight'])
        if impedance is not None:
            # Native load/save inserts dielectric defaults. Check the authored
            # bytes before placement can make an omitted value look declared.
            done = _run_child(routing_adapter.preflight_stackup_argv(
                config['router']['checkout'], board_path, pair_layers[0]),
                clock=clock, cancel_dir=None)
            if done.returncode:
                raise JobRefusal('original impedance stackup is unproven: '
                                 + done.stderr.decode(errors='replace')[-2000:])
            report['preflight']['impedance_stackup'] = {
                'status': 'PASS', 'board_sha256': sha256_bytes(board_bytes), 'layer': pair_layers[0]}
        if length_match is not None and 'tolerance_ps' in length_match:
            done = _run_child(routing_adapter.preflight_delay_argv(
                config['router']['checkout'], board_path,
                {n for g in length_match['groups'] for n in g}),
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
        problem, record = capture(board_path, normalized, clock=clock, cancel_dir=capture_cancel)
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
                                    normalized=normalized, rules_bytes=rules_bytes)
                _check_stop(clock, job_dir)
                report_, exec_ = drc(directory / 'board.kicad_pcb', directory, label='native-drc',
                                     clock=clock, cancel_dir=job_dir)
                split = classify_errors(report_, owners, base_keys)
                entry.update(status='checked', integrity=check['integrity'], integrity_problems=check['problems'],
                             locality=check_locality(directory / 'board.kicad_pcb', normalized,
                                                     clock=clock, cancel_dir=job_dir),
                             edge_intent=check_edge_intent(directory / 'board.kicad_pcb', normalized,
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
            selected_seed = next((row for row in report['engine']['seeds'] if row.get('seed') == selected['seed']), {})
            report['final'] = final_checks(routed, selected, owners, base_keys, job_dir / f'final-{suffix}', normalized,
                                           refinement=selected_seed.get('refinement'),
                                           clock=clock, cancel_dir=job_dir,
                                           owned_zone_uuids=plane_ids, planes=plane_bytes,
                                           pair_impedance=impedance, router_checkout=config['router']['checkout'],
                                           length_match=length_match, ac_match=ac_match,
                                           input_board=board_path, input_board_sha256=sha256_bytes(board_bytes))
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


GROUP_VIEWS_SCHEMA = 'agent-ee.group-views/1'


def _group_views(board, groups, out, *, input_board=None, input_board_sha256=None, clock, cancel_dir=None):
    """Supplemental annotated views of the saved final board, rendered in a bounded child.

    The helper runs through ``_run_child`` under the job clock and CANCEL file, so it can
    neither overrun the remaining deadline nor ignore cancellation. Its manifest must bind
    the exact saved final board, the validated groups identity and, separately, the raw
    input board; anything else is reported as missing evidence, never as a stale or partial
    view. A rendered view is supplemental evidence (``acceptance_assessed`` stays false), not
    a placement or group-quality pass.
    """
    from .storage import identity
    board, out = Path(board), Path(out)
    result = {'status': 'missing', 'schema': GROUP_VIEWS_SCHEMA, 'directory': str(out), 'manifest': None,
              'manifest_sha256': None, 'board_sha256': digest(board),
              'input_board_sha256': digest(input_board) if input_board is not None else None,
              'canonical_groups_sha256': identity(groups), 'views': {}, 'acceptance_assessed': False,
              'evidence_required': True, 'problems': []}
    if input_board_sha256 is not None and result['input_board_sha256'] != input_board_sha256:
        result['problems'].append('raw input board on disk does not match the captured input identity')
        return result
    # The validated groups document travels as its own file; the request record keeps the
    # identities this call was bound to, so a later reader can check what was asked for.
    groups_file = out.parent / (out.name + '-groups.json')
    groups_file.write_bytes(canonical(groups))
    write_json(out.parent / (out.name + '-request.json'),
               {'schema': 'agent-ee.group-views-request/1', 'board': str(board), 'board_sha256': result['board_sha256'],
                'input_board': str(input_board) if input_board is not None else None,
                'input_board_sha256': result['input_board_sha256'], 'groups_file': groups_file.name,
                'groups_sha256': digest(groups_file), 'canonical_groups_sha256': result['canonical_groups_sha256']})
    argv = [sys.executable, '-B', '-m', 'agent_ee.group_views', 'produce', '--board', str(board),
            '--groups', str(groups_file), '--out', str(out)]
    if input_board is not None:
        argv += ['--input-board', str(input_board)]
    _check_stop(clock, cancel_dir)
    done = _run_child(argv, clock=clock, cancel_dir=cancel_dir)
    if done.returncode != 0:
        noise = (': Debug: ', 'property.h(')
        tail = '\n'.join(line for line in done.stderr.decode(errors='replace').splitlines()
                         if not any(marker in line for marker in noise))
        result['problems'].append('group view helper exit %d: %s' % (done.returncode, tail[-2000:].strip()))
        return result
    manifest_path = out / 'manifest.json'
    if not manifest_path.is_file():
        result['problems'].append('group view helper produced no manifest')
        return result
    manifest = read_json(manifest_path)
    problems = []
    if manifest.get('schema') != GROUP_VIEWS_SCHEMA:
        problems.append('unexpected group view manifest schema')
    if manifest.get('board_sha256') != result['board_sha256'] or digest(board) != result['board_sha256']:
        problems.append('group view manifest does not bind the saved final board')
    if manifest.get('input_board_sha256') != result['input_board_sha256']:
        problems.append('group view manifest does not bind the raw input board identity')
    if manifest.get('canonical_groups_sha256') != result['canonical_groups_sha256']:
        problems.append('group view manifest does not bind the validated groups')
    if manifest.get('acceptance_assessed') is not False:
        problems.append('group view manifest claims an assessment')
    views = {}
    for side in ('top', 'bottom'):
        row = (manifest.get('views') or {}).get(side) or {}
        path = out / str(row.get('path') or '')
        if not row.get('path') or not path.is_file() or digest(path) != row.get('sha256'):
            problems.append(f'{side} view missing or does not match its manifest hash')
            continue
        views[side] = {'path': row['path'], 'sha256': row['sha256'], 'face': row.get('face')}
    result.update(manifest=manifest_path.name, manifest_sha256=digest(manifest_path), views=views,
                  members=len(manifest.get('members') or []), unresolved=len(manifest.get('unresolved') or []),
                  problems=problems, status='rendered' if not problems else 'missing')
    return result


def verify_group_views(final):
    """Problems with the retained view evidence of a final record: changed or missing artifacts are visible."""
    views = (final or {}).get('group_views')
    if not isinstance(views, dict):
        return ['final record carries no group view evidence']
    if views.get('status') != 'rendered':
        return ['group views were not rendered: ' + '; '.join(views.get('problems') or ['no reason recorded'])]
    out = Path(views['directory'])
    problems = []
    manifest_path = out / str(views.get('manifest') or 'manifest.json')
    if not manifest_path.is_file() or digest(manifest_path) != views.get('manifest_sha256'):
        problems.append('group view manifest missing or changed')
    else:
        manifest = read_json(manifest_path)
        if manifest.get('board_sha256') != final.get('board_sha256'):
            problems.append('group view manifest no longer binds the final board')
    for side in ('top', 'bottom'):
        row = views.get('views', {}).get(side)
        if row is None:
            problems.append(f'{side} view absent from the final record')
            continue
        path = out / row['path']
        if not path.is_file():
            problems.append(f'{side} view file missing: {row["path"]}')
        elif digest(path) != row['sha256']:
            problems.append(f'{side} view file changed: {row["path"]}')
    board = Path(final.get('board') or '')
    if board.is_file() and digest(board) != final.get('board_sha256'):
        problems.append('final board changed after its views were rendered')
    return problems


def final_checks(routed, incumbent, owners, base_keys, job_dir, normalized, *, refinement=None, clock=None, cancel_dir=None,
                 owned_zone_uuids=(), planes=None, pair_impedance=None, router_checkout=None, length_match=None, ac_match=None,
                 input_board=None, input_board_sha256=None):
    groups, intent, rails = (_bundle(normalized)[k] for k in ('groups', 'intent', 'rails'))
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
    from . import routing_adapter
    impedance_result = None
    if pair_impedance is not None:
        plan = routed.get('impedance_plan')
        impedance_result = {'status': 'FAIL', 'defects': ['router omitted or changed required impedance plan']}
        if isinstance(plan, dict) and plan.get('requirement') == pair_impedance and plan.get('status') == 'PASS':
            request, result = final_dir / 'impedance-plan.json', final_dir / 'impedance.json'
            write_json(request, plan)
            done = _run_child(routing_adapter.post_route_checker_argv(
                router_checkout, 'pair_impedance', board, request, result),
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
        done = _run_child(routing_adapter.post_route_checker_argv(
            router_checkout, 'length_groups', board, request, result),
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
        done = _run_child(routing_adapter.post_route_checker_argv(
            router_checkout, 'ac_matching', board, request, result),
            clock=clock, cancel_dir=cancel_dir)
        ac_result = read_json(result) if result.is_file() else {
            'status': 'FAIL', 'defects': [done.stderr.decode(errors='replace')[-2000:]]}
        if (done.returncode != 0 or ac_result.get('board_sha256') != digest(board)
                or ac_result.get('requirement') != ac_match):
            ac_result['status'] = 'FAIL'
            ac_result.setdefault('defects', []).append('final AC result does not bind the saved board and requirement')
    split = classify_errors(report_, owners, base_keys, placed_keys)
    edge_intent = check_edge_intent(board, normalized, clock=clock, cancel_dir=cancel_dir)
    locality = check_locality(board, normalized, clock=clock, cancel_dir=cancel_dir)
    metrics = board_metrics(board, [g for g in groups['groups']], rails=rails, proximity=intent.get('proximity'),
                            fixed_poses=intent.get('fixed_poses', []))
    metrics['edges'] = edge_intent['connectors']
    metrics['refinement'] = refinement
    # Supplemental annotated views of this same saved board (AGENTEE-479 part 2 evidence). Missing or
    # unbound views are visible as incomplete evidence; they never decide eligibility or acceptance.
    group_views = _group_views(board, groups, final_dir.parent / (final_dir.name + '-group-views'),
                               input_board=input_board, input_board_sha256=input_board_sha256,
                               clock=clock, cancel_dir=cancel_dir)
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
            'edge_intent': edge_intent, 'locality': locality, 'metrics': metrics,
            'group_views': group_views, 'evidence_complete': group_views['status'] == 'rendered'}


def preflight(config, executable=None, *, clock=None, cancel_dir=None, identities=None):
    """Runtime readiness before the budgeted work: imports, toolchain, engine and router."""
    import pcbnew
    from . import routing_adapter
    identities = {} if identities is None else identities
    identities.update(pcbnew_version=pcbnew.Version(), python=sys.version.split()[0])
    if 'planes' in config['inputs']:
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
        for command in NATIVE_READ_LABELS:
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
    if args.placement_command in NATIVE_READ_LABELS:
        request = json.load(sys.stdin)
        try:
            if args.placement_command == '_capture':
                problem, record = _capture_native(args.board, request['normalized'])
                result = {'problem': problem, 'record': record}
            elif args.placement_command == '_nets':
                result = _rail_membership_native(args.board)
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
    groups_document = read_json(args.groups) if args.groups else {'groups': []}
    normalized = normalize_intent(read_json(args.intent), groups_document) if args.intent else None
    metrics = board_metrics(args.board, (normalized['groups'] if normalized else groups_document)['groups'],
                            proximity=normalized['intent'].get('proximity') if normalized else None,
                            fixed_poses=normalized['intent'].get('fixed_poses', []) if normalized else ())
    if normalized:
        metrics['edge_intent'] = check_edge_intent(args.board, normalized)
        metrics['edges'] = metrics['edge_intent']['connectors']
    print(json.dumps(metrics, indent=1))
    return 1 if metrics.get('edge_intent', {}).get('status') == 'failed' else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.placement_job')
    configure_parser(parser, internal=True)
    return execute(parser.parse_args(argv), parser)


if __name__ == '__main__':
    raise SystemExit(main())
