"""One whole-board placement-to-route job over one prepared problem (AGENTEE-515).

Order (chief 531, b4a5857a): native capture -> Rust prepared model with
incremental search -> shortlist native materialize/save/reload/constraint
check -> router process (recorded checkout) -> final save/reload/native checks.

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
transform preserved and native DRC shows no placement-introduced error. A
routed board is eligible when native DRC with refilled zones reports no open
connection and no error beyond those of the input board, its protected
content is unchanged and every declared check passes (AGENTEE-640). An error
the unchanged input already reports is counted as pre-existing and rejects
nothing; the report never calls a board "clean".
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import native_run, placement_problem
from .placement_problem import (INPUT_SCHEMA, OUTPUT_SCHEMA, JobRefusal, escape_hints,  # noqa: F401
                                rule_mm as _rule_mm)
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
# The placement search may use this part of the time left when it starts; the rest is kept for
# the native checks of its candidates, routing and the final checks (AGENTEE-637). The engine
# sizes its seeds and steps to that time. `search.time_share` in the job configuration overrides
# it. A scheduling choice of the tool, not an engineering rule of any board.
SEARCH_TIME_SHARE = 0.4
# How long the controller waits for the engine's answer after the engine's own deadline.
SEARCH_GRACE_S = 15.0
SILK = ('F.Silkscreen', 'B.Silkscreen')
FAB = {'F': 'F.Fab', 'B': 'B.Fab'}
SILK_OF = {'F': 'F.Silkscreen', 'B': 'B.Silkscreen'}
COPPER = ('F.Cu', 'B.Cu')
NM = 1_000_000


class EnvironmentFailure(Exception):
    """A runtime/tool failure whose recovery does not belong to the design author."""

    def __init__(self, stage, reason):
        self.stage = stage
        super().__init__(reason)


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
    always kept. A command that runs Python from the router checkout (routing_adapter.is_router_command)
    runs with routing_adapter.router_env, whoever builds it.
    """
    from . import routing_adapter
    if routing_adapter.is_router_command(argv):
        env = routing_adapter.router_env(env)
    _check_stop(clock, cancel_dir)
    intent = native_run.Intent('placement_child', argv, timeout=clock.remaining(), isolation='host',
                               stdin=input_bytes or None, env=env, cwd=None if cwd is None else str(cwd))
    sink = stdout if stdout is not None else _Discard() if on_line is not None else None
    try:
        done = native_run.run(intent, work=None, on_tick=lambda: _check_stop(clock, cancel_dir), on_line=on_line,
                              sink=sink, stderr_sink=stderr)
        if done['execution'] == 'unavailable':
            native_run.completed(done)   # raises Popen's own launch error (errno, strerror, filename)
    except OSError as exc:
        raise EnvironmentFailure('native-tool', f'{argv[0]}: {exc}') from exc
    # The job clock and CANCEL decide, not the child's own limit (which started counting later):
    # a run that ends after the deadline or a cancel is stopped, never a completion.
    _check_stop(clock, cancel_dir)
    if done['execution'] == 'timeout':
        raise JobStopped('deadline')
    if done.returncode < 0:
        raise EnvironmentFailure('native-tool', f'{argv[0]} crashed (exit {done.returncode}): '
                                 + done.stderr.decode(errors='replace')[-2000:])
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


def _land_choices(facts, unknown):
    """Name the lands of a repeated pad number, because a declared uuid must select one."""
    lands = {}
    for row in unknown:
        for endpoint in (row.get('from'), row.get('to')):
            if isinstance(endpoint, dict) and 'uuid' not in endpoint:
                pads = [pad for fp in facts['footprints'] if fp['ref'] == endpoint.get('reference')
                        for pad in fp['pads'] if pad['number'] == endpoint.get('pad')]
                if len(pads) > 1:
                    lands[f"{endpoint['reference']}.{endpoint['pad']}"] = [
                        dict(uuid=pad.get('uuid'), size_nm=pad.get('size_nm')) for pad in pads]
    return '; set uuid on the endpoint to choose a land: ' + canonical(lands).decode() if lands else ''


def _locality_grade(facts, document):
    from .electrical_locality import grade
    result = grade(facts, document)
    if result['unknown']:
        raise JobRefusal('electrical locality cannot be evaluated: ' + canonical(result['unknown']).decode()
                         + _land_choices(facts, result['unknown']))
    return result


def check_block_contract(path, normalized, *, clock=None, cancel_dir=None, retain_capture=False):
    """Require strict block evidence only when the caller declared that contract."""
    from .storage import identity
    normalized = _bundle(normalized)
    if 'block_contract' not in normalized:
        return {'status': 'not_declared'}
    binding = normalized.get('block_contract_binding')
    if (binding is None or binding['groups_sha256'] != identity(normalized['groups'])
            or identity(binding['contract']) != identity(normalized['block_contract'])):
        raise JobRefusal('block contract has no matching original capture binding')
    request = {'binding': binding}
    if retain_capture:
        request['retain_capture'] = True
    report = _native_read(path, '_check_blocks', request, clock=clock, cancel_dir=cancel_dir)
    if (report.get('schema') != 'agent-ee.block-contract-report/1'
            or report.get('status') not in ('pass', 'info', 'defect', 'unknown')
            or report.get('binding_sha256') != binding['binding_sha256']
            or report.get('contract_sha256') != identity(binding['contract'])
            or report.get('original_board_sha256') != binding['original_capture']['board_sha256']
            or report.get('board_sha256') != digest(path)
            or any(not isinstance(report.get(k), list) for k in ('defects', 'unknown', 'advisories'))):
        raise JobRefusal('native block contract check returned missing or invalid output')
    if retain_capture:
        from .placement_contract import verify_saved_report
        verify_saved_report(report, binding)
    return report



def local_routing_applies(recipe):
    from .local_return_seed import applies
    return applies(recipe)


def __getattr__(name):
    if name == 'acquis' 'ition_local_routing_applies':
        return local_routing_applies
    if name == '_acquis' 'ition_solver':
        return _pose_solver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Scheduling policy for the builtin router, not a design rule. No one grid is better on every
# board: its 0.1 mm default left opens and dangling vias on two sub-millimetre boards that
# 0.025 mm routed, and routes other fine-pitch boards clean in a fifth of the time. So a job
# starts on the router's default, which is cheap, and moves to the finer grid after a failure.
FINE_ROUTER_GRID_MM = 0.025


def router_grid(config, *, after_failed_route):
    """The declared search grid; otherwise the router's own, and the finer one after a failed trial."""
    declared = config['router'].get('grid_step_mm')
    if declared is not None:
        return declared, 'declared'
    if after_failed_route:
        return FINE_ROUTER_GRID_MM, 'after-failed-route'
    return None, 'router-default'


# A second routing trial of a placement, on the router's other grid, must end inside the job's
# time: the board of the first trial is already known, and a job cut off at its deadline would
# lose it. Its search is therefore stopped early enough for the router's stop grace
# (routing_adapter.search_budget_s) and for this allowance, the router's grading of its own
# result plus the job's final checks. Measured on six boards of 21 to 94 footprints (the matched
# benchmark of 2026-11-11): 21 to 25 s of grading and 3 to 10 s of final checks.
ROUTE_CHECK_ALLOWANCE_S = 60


def second_trial_useful(status, routed, final):
    """Whether another routing search of the same placement can change a failed trial's outcome.

    It can when the router ran out of search time, when its own grading rejected its board, or
    when the final checks reject only what the routing made (``final['made_by']``): open
    connections, errors and dangling tracks it introduced, and the checks that measure routed
    copper. It cannot when the placement itself is rejected, because that stays the same."""
    if status == 'route_failed':
        return routed.get('status') == 'no_candidate'
    if status != 'verification_failed':
        return False
    made_by = final.get('made_by') or {}
    return not made_by.get('placement') and (routed.get('router_accepted') is False or bool(made_by.get('routing')))


def other_grid(grid_step, grid_basis):
    """The grid of a placement's second routing trial, or None when the plan declares the grid.

    No one grid is better for every board: on the matched benchmark's fine-pitch boards the
    0.025 mm grid left fewer connections open on three and added a pair of vias too close
    together on a fourth, which the router's default grid routed clean in a fifth of the time."""
    if grid_basis == 'declared':
        return None
    return (FINE_ROUTER_GRID_MM if grid_step is None else None), 'other-grid-after-failed-route'


def trial_rank(final):
    """Order of two failed routing trials of one placement; the smaller is the better board.

    A checked board comes before none, then fewer open connections, then fewer errors the
    routing introduced."""
    if not isinstance(final, dict) or not isinstance(final.get('opens'), int):
        return (1, 0, 0)
    introduced = ((final.get('errors') or {}).get('routing_introduced') or {}).get('count', 0)
    return (0, final['opens'], introduced)


def check_mechanics(path, normalized, *, clock=None, cancel_dir=None):
    if 'mechanics' not in normalized:
        return {'status': 'not_declared'}
    binding = normalized.get('mechanics_binding')
    if binding is None or binding['recipe'] != normalized['mechanics']:
        raise JobRefusal('acquisition mechanics has no original matching binding')
    result = _native_read(path, '_check_mechanics', {'binding': binding}, clock=clock, cancel_dir=cancel_dir)
    if (result.get('status') not in ('pass', 'defect') or not isinstance(result.get('defects'), list)
            or result.get('binding_sha256') != binding['binding_sha256'] or result.get('board_sha256') != digest(path)):
        raise JobRefusal('native acquisition mechanics check is missing or stale')
    if (result['status'] == 'pass') != (not result['defects']):
        raise JobRefusal('inconsistent acquisition mechanics verdict')
    return result


def _evaluate_board_native(path, *, fixed_poses=(), edges=(), groups=(), rails=None, proximity=None,
                           board=None, before=None, mechanics_binding=None):
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
    facts = extract(path, board=board, before=before)
    # The same choice of model as capture made for this board and intent.
    description = _describe_board(board, facts, fixed_poses=fixed_poses, geometry=(
        bool(edges) or mechanics_binding is not None or not routing_only(facts['footprints'], list(fixed_poses))))
    if mechanics_binding is not None:
        from .source_mechanics import attach
        attach(description, mechanics_binding, facts)
    payload = placement_problem.evaluate(description,
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


def classify_footprint(fp, captured, *, courtyard_is_body=True):
    """Native facts about one footprint that the role rule and its body occupancy need.

    Only demonstrably silk-only, padless, zone-free graphics are excluded from
    packing. A footprint whose pads are all unnetted and that draws no body
    (fab, silk or courtyard) is mark-eligible: if intent fixes it, it is a fixed
    copper mark, a copper obstacle and never a body. Every other footprint gets
    an occupancy with its provenance rung; a malformed courtyard or missing
    geometry is returned as ``refusal``, raised only if its role needs a body.

    ``courtyard_is_body`` is False on a board whose own rules ignore courtyard
    overlap (:func:`courtyard_overlap_ignored`). Its courtyards may overlap, so
    the body is the next rung, the package outline with the pads; a footprint
    that draws nothing else keeps its courtyard.
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
    # The mask layers each aperture opens, in the same order: apertures on different layers never meet.
    mask_layers = [[pcbnew.LayerName(l) for l in (pcbnew.F_Mask, pcbnew.B_Mask) if p.IsOnLayer(l)]
                   for p in pads if _on_mask(p)] + [
        [pcbnew.LayerName(g.GetLayer())] for g in graphics if pcbnew.LayerName(g.GetLayer()) in ('F.Mask', 'B.Mask')]
    fab = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == FAB[side] and g.GetClass() == 'PCB_SHAPE']
    silk = [g for g in graphics if pcbnew.LayerName(g.GetLayer()) == SILK_OF[side] and g.GetClass() == 'PCB_SHAPE']
    facts = {'silk_only': False, 'mark_eligible': False, 'bodiless': False, 'occupancy': None, 'refusal': None,
             'diagnostics': [],
             'mask': mask, 'mask_layers': mask_layers, 'classified_layers': classified,
             'seats': {'body:fab': _union([_bbox(g) for g in fab]) if fab else None,
                       'body:silk': _union([_bbox(g) for g in silk]) if silk else None}}
    if not pads and not zones and graphics and set(layers) <= set(SILK):
        return dict(facts, silk_only=True, mask=[], mask_layers=[])
    body_layers = {FAB[side], SILK_OF[side], f'{side}.CrtYd'} & set(layers)
    facts['mark_eligible'] = bool(pads) and not body_layers and all(not p.GetNetname() for p in pads)
    courtyards = captured['courtyards']
    for court_side, record in courtyards.items():
        if record.get('status') != 'proven':
            return dict(facts, refusal=f'{uuid} ({fp.GetReference()!r}): malformed courtyard on {court_side}.CrtYd: '
                                       f'{record.get("status")} {record.get("reason", "")}'.strip())
    if side in courtyards and (courtyard_is_body or not (fab or silk or pads)):
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
        # Nothing to pack: a graphic on another layer (a mask logo, say). Fixed, it is no body;
        # movable, there is no shape to place, so the role rule refuses it.
        return dict(facts, bodiless=True,
                    refusal=f'{uuid}: no courtyard, fab, silk or pad geometry for derived occupancy')
    boxes = [_bbox(g) for g in source] + pad_boxes
    diagnostics.append(f"courtyard overlap is ignored by the board's rules: derived_occupancy rung {rung}"
                       if side in courtyards else f'courtyard missing (metadata gap): derived_occupancy rung {rung}')
    return dict(facts, diagnostics=diagnostics,
                occupancy={'status': 'derived', 'label': 'derived_occupancy', 'rung': rung, 'box': _union(boxes),
                           'sources': sorted(g.m_Uuid.AsString() for g in source) + ['pads'],
                           'note': 'derived envelope; separation is not proof of package body separation'})


def courtyard_overlap_ignored(board):
    """Whether the board's own rules ignore overlapping courtyards (``courtyards_overlap: ignore``).

    KiCad's default is an error, and a warning still tells the designer, so only an
    explicit ``ignore`` in the board's project file says its courtyards may overlap."""
    from . import silk_labels
    return silk_labels.rule_severities(board).get('courtyards_overlap') == 'ignore'


ROUTING_ONLY = {'status': 'none', 'reason': 'routing-only job: every footprint is fixed, no placement model is built'}


def routing_only(footprints, fixed_poses):
    """Whether nothing can move: every footprint is natively locked or has a declared fixed pose."""
    owners = placement_problem.fixed_pose_owners(fixed_poses, footprints)
    return bool(footprints) and all(f['locked'] or f['uuid'] in owners for f in footprints)


def keepout_obstacles(board):
    """Board rule areas that forbid footprints or pads, as fixed bodies for the engine.

    The body is the bounding box of the rule area on every side, which is
    larger than an area that is not a rectangle or that forbids only pads.
    Native DRC on the saved board remains the judge of the real rule.
    """
    rows = []
    for zone in board.Zones():
        if not zone.GetIsRuleArea() or not (zone.GetDoNotAllowFootprints() or zone.GetDoNotAllowPads()):
            continue
        uid, box = zone.m_Uuid.AsString(), _bbox(zone)
        rows.append(dict(uuid=uid, native_ref='rule-area:' + uid, role='fixed_body',
                         pose=dict(x_nm=box[0], y_nm=box[1], quarter_turns=0),
                         occupancy=dict(status='derived', label='derived_occupancy', rung='courtyard',
                                        local_rect_nm=[0, 0, box[2] - box[0], box[3] - box[1]]),
                         copper_local_nm=[], mask_local_nm=[], holes_local_nm=[], pads=[], rotations=[0]))
    return sorted(rows, key=lambda r: r['uuid'])


def _board_obstacles(board, fixed_poses=()):
    """Fixed obstacles that are not footprints, and the text boxes attached to footprints.

    Where the board's own severities make a silkscreen overlap an error, the silkscreen text
    that stays where it is (board-level text, labels of fixed footprints) is an obstacle too."""
    from . import silk_labels
    from .board_text import placement_regions
    text_obstacles, attached_text = placement_regions(board)
    silk_text = silk_labels.board_obstacles(board, fixed_poses, known={row['uuid'] for row in text_obstacles})
    return sorted([*text_obstacles, *keepout_obstacles(board), *silk_text], key=lambda r: r['uuid']), attached_text


def pour_zones(board):
    """Copper pours: zones that are neither rule areas nor teardrops."""
    return [z for z in board.Zones() if not z.GetIsRuleArea() and not z.IsTeardropArea()]


def refill_pours(board):
    """Refill every zone of a loaded board after its footprints or copper changed."""
    import pcbnew
    board.BuildConnectivity()
    if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
        raise JobRefusal('native zone fill failed')


def _describe_board(board, facts, *, geometry=True, fixed_poses=()):
    """Read a loaded native board into the description ``placement_problem`` builds from.

    ``geometry=False`` is the routing-only description: nothing can move, so no
    occupancy, copper, hole or mask model is read and no footprint or outline
    is refused for what that model cannot represent. Pads keep their nets and
    positions, which the net, group and proximity measurements use.
    ``fixed_poses`` are the intent's declared fixed poses; they only tell which
    footprints' labels are fixed silkscreen text (``_board_obstacles``).
    """
    import pcbnew
    from . import silk_labels
    by_uuid = {f['uuid']: f for f in facts['footprints']}
    if len(by_uuid) != len(facts['footprints']):
        raise JobRefusal('duplicate native footprint UUID')
    settings = board.GetDesignSettings()
    native_class = settings.m_NetSettings.GetDefaultNetclass()
    clearance = _clearance(settings)
    text_obstacles, attached_text = _board_obstacles(board, fixed_poses)
    # None unless a footprint can move and this board's own rules make silk overlaps errors.
    silk_grow = silk_labels.reserve(board) if geometry and not routing_only(facts['footprints'], list(fixed_poses)) else None
    courtyard_is_body = not courtyard_overlap_ignored(board)
    footprints = []
    for fp in board.GetFootprints():
        uuid = fp.m_Uuid.AsString()
        captured = by_uuid[uuid]
        q, residual = placement_problem.split_turns(captured['rotation_deg'])
        origin = captured['position_nm']
        if not geometry:
            footprints.append({
                'uuid': uuid, 'ref': captured['ref'], 'side': captured['side'], 'locked': captured['locked'],
                'position_nm': origin, 'rotation_deg': captured['rotation_deg'], 'quarter_turns': q,
                'residual_deg': residual, 'silk_only': False, 'mark_eligible': False,
                'occupancy': dict(ROUTING_ONLY), 'occupancy_refusal': None, 'occupancy_diagnostics': [],
                'occupancy_local_nm': None, 'classified_layers': {}, 'copper_local_nm': [], 'mask_local_nm': [],
                'holes_local_nm': [],
                'pads': [{'uuid': p.m_Uuid.AsString(), 'number': p.GetNumber(), 'net': p.GetNetname(),
                          'local_nm': list(_unrotate(int(p.GetPosition().x) - origin[0],
                                                     int(p.GetPosition().y) - origin[1], q)),
                          'half_nm': _pad_half(p, origin, q)}
                         for p in fp.Pads()],
                'seats_local_nm': {'body:fab': None, 'body:silk': None, 'body:occupancy': None}})
            continue
        native = classify_footprint(fp, captured, courtyard_is_body=courtyard_is_body)
        seat_occupancy = (native['occupancy'] or {}).get('box')
        # Declared text and the footprint's own zones (keep-outs, antenna areas) move with the part.
        # So do its silk drawings and texts that are not fields, which no later step can move
        # (AGENTEE-644): with its pad openings they join the occupancy where silk overlaps are errors.
        attached = [(attached_text.get(uuid, []), 'declared-board-text'),
                    ([_bbox(z) for z in fp.Zones()], 'footprint-zones'),
                    (silk_labels.fixed_content(fp, silk_grow, native['mask'], seat_occupancy), 'silk-content')]
        for boxes, source in attached:
            if boxes and native['occupancy'] is not None:
                native['occupancy'] = dict(native['occupancy'],
                    status='derived', label='derived_occupancy',
                    box=_union([native['occupancy']['box'], *boxes]),
                    sources=[*native['occupancy'].get('sources', []), source])
        box = (native['occupancy'] or {}).get('box')
        footprints.append({
            'uuid': uuid, 'ref': captured['ref'], 'side': captured['side'], 'locked': captured['locked'],
            'position_nm': origin, 'rotation_deg': captured['rotation_deg'], 'quarter_turns': q,
            'residual_deg': residual,
            'silk_only': native['silk_only'], 'mark_eligible': native['mark_eligible'],
            'bodiless': native['bodiless'],
            'occupancy': native['occupancy'], 'occupancy_refusal': native['refusal'],
            'occupancy_diagnostics': native['diagnostics'],
            'occupancy_local_nm': _local_rect(box, origin, q) if box else None,
            'classified_layers': native['classified_layers'],
            'copper_local_nm': [c for p in fp.Pads() if (c := _pad_copper(p, origin, q)) is not None]
                + [{'rect_nm': _local_rect(_bbox(g), origin, q), 'clearance_nm': 0}
                   for g in fp.GraphicalItems() if pcbnew.LayerName(g.GetLayer()) in COPPER],
            'mask_local_nm': [{'rect_nm': _local_rect(b, origin, q), 'layers': layers}
                              for b, layers in zip(native['mask'], native['mask_layers'])],
            'holes_local_nm': [h for p in fp.Pads()
                               if (h := _pad_hole(p, origin, q, int(settings.m_HoleClearance))) is not None],
            'pads': [{'uuid': p.m_Uuid.AsString(), 'number': p.GetNumber(), 'net': p.GetNetname(),
                      'local_nm': list(_unrotate(int(p.GetPosition().x) - origin[0],
                                                 int(p.GetPosition().y) - origin[1], q)),
                      'half_nm': _pad_half(p, origin, q)}
                     for p in fp.Pads()],
            'seats_local_nm': {basis: _local_rect(rect, origin, q) if rect else None
                               for basis, rect in {**native['seats'], 'body:occupancy': seat_occupancy}.items()}})
    outline = board_outline(board, facts, required=geometry)
    return {'outline_rect_nm': outline['rect_nm'],
            **({'outline': outline} if outline['kind'] != 'rectangle' else {}),
            'rules': {'copper_clearance_nm': clearance,
                      'hole_to_hole_clearance_nm': int(settings.m_HoleToHoleMin),
                      'copper_edge_clearance_nm': int(settings.m_CopperEdgeClearance),
                      'mask_min_web_nm': int(settings.m_SolderMaskMinWidth)},
            'pair': {'width_nm': int(native_class.GetDiffPairWidth()) if native_class.HasDiffPairWidth() else None,
                     'gap_nm': int(native_class.GetDiffPairGap()) if native_class.HasDiffPairGap() else None,
                     'clearance_nm': clearance},
            'footprints': footprints, 'text_obstacles': text_obstacles}


def _rectangle(points):
    """[x0, y0, x1, y1] when the contour is an axis-aligned rectangle, else None."""
    xs, ys = sorted({p[0] for p in points}), sorted({p[1] for p in points})
    if len(xs) != 2 or len(ys) != 2 or len(points) != 4:
        return None
    return [xs[0], ys[0], xs[1], ys[1]]


def board_outline(board, facts, *, required=True):
    """The engine's outline record: a rectangle, or any closed contour with its cut-outs.

    A proven axis-aligned rectangle is passed as before. Every other board uses
    KiCad's own outline builder, which joins lines and arcs into closed contours;
    it turns arcs into segments within the board's maximum error, recorded as
    ``arc_max_error_nm``. A board with no closed outline, or with several
    separate boards, is refused when a placement needs the outline. A
    routing-only job (``required=False``) then uses the bounding box, which
    nothing is checked against because nothing moves.
    """
    import pcbnew
    proven = facts['outline']
    if proven.get('status') == 'proven' and len(proven['polygons']) == 1:
        rect = _rectangle(proven['polygons'][0])
        if rect is not None:
            return {'kind': 'rectangle', 'rect_nm': rect}
    polygons = pcbnew.SHAPE_POLY_SET()
    closed = board.GetBoardPolygonOutlines(polygons, False)

    def contour(chain):
        points = []
        for index in range(chain.PointCount()):
            point = [int(chain.CPoint(index).x), int(chain.CPoint(index).y)]
            if not points or points[-1] != point:
                points.append(point)
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        return points

    if closed and polygons.OutlineCount() == 1:
        outer = contour(polygons.Outline(0))
        holes = [contour(polygons.Hole(0, index)) for index in range(polygons.HoleCount(0))]
        if len(outer) >= 3 and all(len(hole) >= 3 for hole in holes):
            rect = _rectangle(outer)
            if rect is not None and not holes:
                return {'kind': 'rectangle', 'rect_nm': rect}
            return {'kind': 'polygon',
                    'rect_nm': [min(p[0] for p in outer), min(p[1] for p in outer),
                                max(p[0] for p in outer), max(p[1] for p in outer)],
                    'polygon_nm': outer, 'holes_nm': holes,
                    'source': 'native board outline (GetBoardPolygonOutlines)',
                    'arc_max_error_nm': int(board.GetDesignSettings().m_MaxError)}
    if required:
        raise JobRefusal('board outline is not one closed contour (native outlines: '
                         f'{polygons.OutlineCount() if closed else "not closed"})')
    box = board.GetBoardEdgesBoundingBox()
    if box.GetWidth() <= 0 or box.GetHeight() <= 0:
        box = board.ComputeBoundingBox(False)
    if box.GetWidth() <= 0 or box.GetHeight() <= 0:
        raise JobRefusal('board has no extent')
    return {'kind': 'rectangle', 'rect_nm': [int(box.GetLeft()), int(box.GetTop()), int(box.GetRight()), int(box.GetBottom())]}


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
                      '_nets': 'net membership read', '_check_blocks': 'block contract check',
                      '_check_mechanics': 'acquisition mechanics check'}
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
        if done.returncode == 2:
            try:
                refusal = json.loads(done.stdout)
            except (ValueError, TypeError):
                pass
            else:
                if isinstance(refusal, dict) and isinstance(refusal.get('error'), str):
                    raise JobRefusal(refusal['error'])
        raise EnvironmentFailure('native-tool', f'native {label} failed (exit {done.returncode}): '
                                 + (done.stdout + done.stderr).decode(errors='replace')[-2000:])
    try:
        captured = json.loads(done.stdout)
    except (ValueError, TypeError) as exc:
        raise EnvironmentFailure('native-tool', f'native {label} returned missing or invalid output') from exc
    if not isinstance(captured, dict):
        raise EnvironmentFailure('native-tool', f'native {label} returned missing or invalid output')
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
        raise EnvironmentFailure('native-tool', 'native capture returned missing or invalid output') from exc
    if (not isinstance(problem, dict) or problem.get('schema') != INPUT_SCHEMA
            or not isinstance(record, dict) or record.get('schema') != 'agent-ee.placement-job-capture/1'):
        raise EnvironmentFailure('native-tool', 'native capture returned missing or invalid output')
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
    # Copper pours stay on the board: they are refilled after a move and after routing, and native
    # DRC judges the saved result. Rule areas that forbid footprints or pads are fixed obstacles.
    fixed = routing_only(facts['footprints'], intent.get('fixed_poses', []))
    # Declared edge, locality, mechanics and block checks read the placement model; a plain
    # routing-only job does not, so it is not held to what that model can represent.
    geometry = (not fixed or bool(intent.get('edge_connectors')) or 'electrical_locality' in intent
                or 'mechanics' in normalized or 'block_contract' in normalized)
    description = _describe_board(board, facts, geometry=geometry, fixed_poses=intent.get('fixed_poses', []))
    if Path(board_path).read_bytes() != before:
        raise JobRefusal('input board changed during capture')
    if 'electrical_locality' in intent:
        _locality_grade(facts, intent['electrical_locality'])
    mechanics = {}
    if 'mechanics' in normalized:
        from .source_mechanics import bind, attach
        binding = bind(normalized['mechanics'], facts)
        attach(description, binding, facts)
        mechanics['mechanics'] = binding
    problem, records = placement_problem.capture(
        description, normalized, dialect_checks=dialect_checks,
        locality_footprints=facts['footprints'] if 'electrical_locality' in intent else None)
    strict = {}
    if 'block_contract' in normalized:
        from .placement_contract import bind
        strict['block_contract'] = bind(facts, normalized['block_contract'], normalized, records)
    return problem, {'schema': 'agent-ee.placement-job-capture/1', 'board_sha256': sha256_bytes(before), **strict, **mechanics,
                     'routing_only': fixed, 'placement_model': 'built' if geometry else 'not built (routing-only job)',
                     'outline': {k: v for k, v in problem['outline'].items() if k not in ('polygon_nm', 'holes_nm')},
                     'pour_zones': len(pour_zones(board)),
                     'keepout_rule_areas': len(keepout_obstacles(board)),
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

def _checkout_engine(root):
    """The development binary for this checkout, refused when built from other engine sources.

    A stale binary would silently answer for old engine code, and a run would report its result.
    """
    from .placement_runtime import PlanningProcessError
    candidates = [root / 'build/placement-engine/agent-ee-placement']
    git_dir = root / '.git'
    if git_dir.is_file():
        text = git_dir.read_text(errors='replace').strip()
        if text.startswith('gitdir:'):
            common = Path(text.split(':', 1)[1].strip()).resolve().parents[2]
            candidates.append(common / 'build/placement-engine/agent-ee-placement')
    binary = next((c for c in candidates if c.is_file() and os.access(c, os.X_OK)), None)
    if binary is None:
        raise PlanningProcessError('Rust placement planner is not installed; build with '
                                   'scripts/build_placement_engine.py (see docs/installation.md)')
    binary = binary.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('build_placement_engine', root / 'scripts/build_placement_engine.py')
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    try:
        script.verify(root, binary)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PlanningProcessError(f'{binary} does not match this checkout ({exc}); rebuild it with '
                                   'scripts/build_placement_engine.py build or set AGENT_EE_RUST_PLANNER') from exc
    return binary


def rust_executable():
    from .placement_runtime import PlanningProcessError
    from .placement_rust_runtime import resolve_executable
    try:
        return resolve_executable()
    except PlanningProcessError:
        if 'AGENT_EE_RUST_PLANNER' in os.environ:
            raise
        return _checkout_engine(Path(__file__).resolve().parents[2])


def solve(problem, search, job_dir, *, deadline_s, executable=None, on_seed=None, clock=None):
    """One Rust process for the whole seed portfolio; heartbeats carry finished seeds.

    deadline_s is the engine's own deadline: it shares that time between its seeds and answers
    `completed` with what it found. clock bounds the process; when it expires first the
    answer is an interrupted record holding only the seeds the heartbeats reported."""
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


def _search_diagnostics(output, state):
    """Completed infeasible seeds survive interruption before repair/promotion."""
    retained = {int(seed): event['diagnostic'] for seed, event in state['seed_results'].items()
                if event.get('diagnostic') is not None}
    retained.update({c['seed']: c for c in output.get('diagnostic_shortlist', [])})
    return sorted(retained.values(), key=lambda c: (
        int(c['terms']['hard_count']), int(c['terms']['hard']), int(c['cost']), c['seed']))


def _pose_solver(checkout, request, *, clock, cancel_dir=None, evidence=None):
    from . import pose_milp
    done = _run_child(pose_milp.child_argv(checkout), clock=clock, cancel_dir=cancel_dir,
                      input_bytes=canonical(request))
    if evidence is not None:
        (evidence / 'solver.stdout').write_bytes(done.stdout)
        (evidence / 'solver.stderr').write_bytes(done.stderr)
    if done.returncode:
        if request.get('operation') == 'identity':
            raise EnvironmentFailure('placement-planner', 'planner identity failed: '
                                     + done.stderr.decode(errors='replace')[-2000:])
        raise JobRefusal('proposal process failed: ' + done.stderr.decode(errors='replace')[-2000:])
    return json.loads(done.stdout)


def _repair_shortlist(problem, shortlist, diagnostics, *, checkout, runtime, clock, job_dir,
                      attempt, report, engine_sha256, executable=None, routing_exhausted=False):
    """One retained-diagnostic proposal after placement or routing search exhausts."""
    from . import pose_milp as repair
    from .source_mechanics import is_design_declared
    unchanged = [] if routing_exhausted else shortlist
    if routing_exhausted:
        trials = report.get('routing_trials', [])
        mechanics = problem.get('mechanics', {})
        if (not shortlist or not any(c.get('feasible') for c in shortlist)
                or 'placement_repair' in report or not trials):
            return unchanged
        if is_design_declared(mechanics):
            if any(t.get('status') not in ('local_search_exhausted', 'route_failed', 'verification_failed') for t in trials):
                return unchanged
        else:
            if any(t.get('status') != 'local_search_exhausted' for t in trials):
                return unchanged
    elif shortlist:
        return unchanged
    mechanics_schema = problem.get('mechanics', {}).get('schema')
    if (mechanics_schema not in ('agent-ee.acquisition-mechanics/1', 'agent-ee.source-mechanics/1')
            or not diagnostics):
        return unchanged
    _check_stop(clock, job_dir)
    # Preserve at least half of the remaining job for native checks and routing.
    seconds = min(150.0, clock.remaining()/2)
    record = dict(strategy=repair.SCHEMA, status='insufficient_budget', budget_s=seconds,
                  trigger='feasible_routing_exhausted' if routing_exhausted else 'no_feasible_incumbent')
    report['placement_repair'] = record
    if seconds < 2:
        return unchanged
    bounded = Clock(seconds)
    diagnostic = min(diagnostics, key=lambda c: (
        int(c['terms']['hard_count']), int(c['terms']['hard']), int(c['cost']), c['seed']))
    prefix = 'acquisition-repair' if problem.get('mechanics', {}).get('schema') == 'agent-ee.acquisition-mechanics/1' else 'pose-repair'
    directory = job_dir / f'{prefix}-a{attempt}'
    directory.mkdir()
    write_json(directory / 'basis.json', diagnostic)
    record.update(status='running', directory=str(directory), seed=diagnostic['seed'],
                  basis_sha256=repair.basis(diagnostic), runtime=runtime)
    try:
        solver_fn = getattr(sys.modules[__name__], '_acquis' 'ition_solver', _pose_solver)
        proposal = solver_fn(checkout, dict(operation='propose', problem=problem,
            diagnostic=diagnostic, runtime=runtime, max_seconds=min(120.0, seconds*.8)),
            clock=bounded, cancel_dir=job_dir, evidence=directory)
        write_json(directory / 'proposal.json', proposal)
        if proposal.get('runtime') != runtime:
            raise EnvironmentFailure('placement-planner', 'proposal omitted its bound numerical runtime')
        if proposal.get('status') == 'no_incumbent':
            record['status'] = 'no_incumbent'
            return unchanged
        repair.validate_proposal(problem, diagnostic, proposal)
        payload = dict(problem, evaluate=[proposal['poses']])
        engine = Path(executable or rust_executable())
        if digest(engine) != engine_sha256:
            raise EnvironmentFailure('placement-planner', 'exact repair evaluator changed after job identity capture')
        done = _run_child([str(engine)], clock=bounded, cancel_dir=job_dir,
                          input_bytes=canonical(payload))
        (directory / 'evaluation.stdout').write_bytes(done.stdout)
        (directory / 'evaluation.stderr').write_bytes(done.stderr)
        if digest(engine) != engine_sha256:
            raise EnvironmentFailure('placement-planner', 'exact repair evaluator changed during regrade')
        output = json.loads(done.stdout)
        placement_problem.check_output(output, payload, code=done.returncode,
                                       detail=done.stderr.decode(errors='replace')[-2000:])
        if output.get('status') != 'evaluated' or len(output.get('evaluations', [])) != 1:
            raise JobRefusal('proposal lacks an exact engine evaluation')
        candidate = repair.candidate(problem, diagnostic, proposal, output['evaluations'][0])
        record['status'] = 'exact_feasible' if candidate is not None else 'exact_rejected'
        return [candidate] if candidate is not None else unchanged
    except EnvironmentFailure as exc:
        record.update(status='environment_failure', error=str(exc))
        raise
    except JobStopped as exc:
        record['status'] = exc.status
        if exc.status != 'deadline' or clock.remaining() <= 0:
            raise
        return unchanged
    except (JobRefusal, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        return unchanged
    finally:
        record['elapsed_s'] = round(time.monotonic()-bounded.start, 3)
        write_json(directory / 'result.json', record)


# ---------------------------------------------------------------- native checks

def drc(board, out_dir, *, refill=False, label='drc', clock=None, cancel_dir=None):
    out_dir = Path(out_dir)
    report = out_dir / f'{label}.json'
    argv = native_run.drc_argv(board, report, refill=refill, output_flag='--output')
    done = _run_child(argv, clock=clock or Clock(300), cancel_dir=cancel_dir)
    (out_dir / f'{label}.log').write_bytes(done.stdout + done.stderr)
    # kicad-cli exits 0 when it writes a report; a missing or malformed report is never a pass.
    if done.returncode != 0 or not report.is_file():
        raise EnvironmentFailure('native-tool', f'native DRC did not complete (exit {done.returncode})')
    try:
        data = read_json(report)
    except ValueError as exc:
        raise EnvironmentFailure('native-tool', f'native DRC returned invalid JSON: {exc}') from exc
    if (not isinstance(data, dict) or not isinstance(data.get('violations'), list)
            or not isinstance(data.get('unconnected_items'), list)):
        raise EnvironmentFailure('native-tool', 'native DRC report lacks counted evaluations')
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


def _rigid(board):
    """UUIDs of the items that keep their place inside their footprint whatever this job does.

    These are a footprint's pads and drawings. Its fields are left out, because the label step
    moves them; its zones are left out, because their fill depends on the copper around them."""
    rigid = set()
    for fp in board.GetFootprints():
        fields = {field.m_Uuid.AsString() for field in fp.GetFields()}
        rigid.update(uuid for uuid in (item.m_Uuid.AsString() for item in list(fp.Pads()) + list(fp.GraphicalItems()))
                     if uuid not in fields)
    return rigid


def _key(v):
    return (v['type'], tuple(sorted(i.get('uuid', '') for i in v.get('items', []))))


def error_keys(report):
    """The errors of a native DRC report as a multiset of (type, items) keys."""
    return Counter(_key(v) for v in report['violations'] if v.get('severity') == 'error')


def classify_errors(report, owners, baseline_keys, placed_keys=None, moved=None, rigid=None):
    """Split native errors into pre-existing / placement-introduced / routing-introduced.

    Pre-existing, by the input: the unchanged input reports the same (type, items), and
    either every item belongs to one footprint or, when ``moved`` (the UUIDs of the
    footprints this placement moved) is known, no item belongs to a moved footprint.

    Pre-existing, by geometry: every item is a rigid member of one footprint (``rigid``,
    see :func:`_rigid`), and the placed board has the finding too when ``placed_keys`` is
    given. A placement moves a footprint as a whole, so such a finding is the same
    wherever the footprint is, and the input need not report it. It often does not:
    KiCad stops reporting a type at a limit (499 clearance findings and 199 of several
    other types on KiCad 10), and the unplaced input of a placement job, a pile of
    footprints, has more. Without ``rigid`` only the first rule applies.

    Placement-introduced: any other error present on the placed board.
    Routing-introduced: new relative to the placed board.

    The key sets are multisets (:func:`error_keys`; a plain set counts each key
    once). One finding of the input accounts for one finding here, and likewise
    for the placed board: a board that reports the same (type, items) more often
    than its input has the extra findings as introduced, never as pre-existing."""
    split = {'pre_existing': [], 'placement_introduced': [], 'routing_introduced': []}
    in_input = Counter(baseline_keys)
    on_placed = None if placed_keys is None else Counter(placed_keys)
    for v in report['violations']:
        if v.get('severity') != 'error':
            continue
        key = _key(v)
        items = v.get('items', [])
        footprints = {owners.get(i.get('uuid')) for i in items}
        one_footprint = len(footprints) == 1 and None not in footprints
        before_routing = on_placed is None or on_placed[key] > 0
        if (one_footprint and rigid is not None and before_routing
                and all(i.get('uuid') in rigid for i in items)):
            in_input[key] -= in_input[key] > 0
            cause = 'pre_existing'
        elif in_input[key] > 0 and (one_footprint or (moved is not None and not footprints & set(moved))):
            in_input[key] -= 1
            cause = 'pre_existing'
        elif before_routing:
            cause = 'placement_introduced'
        else:
            cause = 'routing_introduced'
        if cause != 'routing_introduced' and on_placed is not None and on_placed[key] > 0:
            on_placed[key] -= 1         # the placed board's finding is this one
        split[cause].append(v)
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


def _is_teardrop_zone(node):
    """Whether a `zone` node is a teardrop: `(attr (teardrop (type ...)))`."""
    return any(isinstance(child, list) and child[:1] == ['attr']
               and any(isinstance(part, list) and part[:1] == ['teardrop'] for part in child[1:])
               for child in node[1:])


def _non_footprint_nodes(data, *, include_footprints=False, routing_owned=False, owned_zones=()):
    """Top-level board nodes other than footprints, for rule/geometry identity.

    Two normalizations. Native SaveBoard writes each standard layer's user
    name as a fourth `layers` element; entries are compared on ordinal, canonical
    name and type, and any other user name difference is still reported. A zone
    is compared without its fill result (`filled_polygon`, `fill_segments` and
    the filled flag): the fill is recomputed from the zone's outline and
    settings, which are compared, whenever footprints or copper change.

    With ``routing_owned`` the copper that routing produces is left out: tracks,
    arcs, vias, the zones the controller names as owned, and teardrop zones. A
    teardrop zone is not a zone a designer defined. KiCad builds one where a
    track meets a pad or via whose teardrop setting is on: ``kicad-cli pcb drc
    --refill-zones --save-board``, which a router runs on its result, rebuilds
    them all and writes them without a UUID. The pad settings they come from
    are footprint content, which is compared; native DRC judges their copper."""
    import pcbnew
    from .symbol_library import parse_sexpr
    tree = parse_sexpr(data)
    nodes = []
    for node in tree[1:]:
        if not isinstance(node, list) or not node or (node[0] == 'footprint' and not include_footprints):
            continue
        if routing_owned and node[0] in ('segment', 'arc', 'via'):
            continue
        if routing_owned and node[0] == 'zone' and (_is_teardrop_zone(node) or any(
                isinstance(child, list) and child[:1] == ['uuid'] and child[1] in owned_zones
                for child in node[1:])):
            continue
        if node[0] == 'zone':
            node = [(['fill'] + [part for part in child[1:] if part not in ('yes', 'no')]
                     if isinstance(child, list) and child[:1] == ['fill'] else child)
                    for child in node
                    if not (isinstance(child, list) and child[:1] in (['filled_polygon'], ['fill_segments']))]
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
    obstacles, _ = _board_obstacles(board, fixed_poses)
    reserved = {row['uuid']: row for row in obstacles}
    obstacle_poses = [row for row in poses if row['uuid'] in reserved]
    if len(obstacle_poses) != len(reserved) or len({r['uuid'] for r in obstacle_poses}) != len(reserved):
        raise JobRefusal('candidate pose vector omits or repeats a board text or rule-area obstacle')
    for row in obstacle_poses:
        expected = reserved[row['uuid']]['pose']
        if row['moved'] or any(row[key] != expected[key] for key in ('x_nm', 'y_nm', 'quarter_turns')):
            raise JobRefusal('candidate moved a fixed board text or rule-area obstacle')
    poses = [row for row in poses if row['uuid'] not in reserved]
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
                old['x_nm'], old['y_nm'], placement_problem.split_turns(old['rotation_deg'])[0], old['side']):
            raise JobRefusal(f'{uuid}: protected footprint pose or moved flag differs from frozen input')
    destination.mkdir(parents=True)
    for fp in board.GetFootprints():
        row = target[fp.m_Uuid.AsString()]
        if not row['moved']:
            continue
        fp.SetPosition(pcbnew.VECTOR2I(int(row['x_nm']), int(row['y_nm'])))
        # A part at another angle than a quarter turn keeps that offset (placement_problem.split_turns).
        fp.SetOrientationDegrees(90.0 * row['quarter_turns']
                                 + placement_problem.split_turns(before[fp.m_Uuid.AsString()]['rotation_deg'])[1])
    if pour_zones(board):
        # The saved fill belongs to the input placement and copper; the router and DRC get a current one.
        refill_pours(board)
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
        residual = placement_problem.split_turns(old['rotation_deg'])[1]
        turns, saved_residual = placement_problem.split_turns(new['rotation_deg'])
        if turns != row['quarter_turns'] or abs(saved_residual - residual) > 1e-6:
            problems.append(f'{uuid}: saved orientation differs from the engine pose')
        # Quarter turns are exact. At any other angle KiCad rounds each pad after a
        # floating-point rotation, so a moved pad may differ from the engine's exact turn by rounding.
        slack = 2 if residual and row['moved'] else 0
        for pad in row['pads']:
            q = new['pads'].get(pad['uuid'])
            if q is None or abs(q['x_nm'] - pad['x_nm']) > slack or abs(q['y_nm'] - pad['y_nm']) > slack:
                problems.append(f'{uuid}: pad {pad["uuid"]} native position differs from the engine transform')
        if not row['moved'] and (new['x_nm'], new['y_nm'], new['rotation_deg']) != (
                old['x_nm'], old['y_nm'], old['rotation_deg']):
            problems.append(f'{uuid}: fixed footprint moved')
    if _non_footprint_nodes(Path(source).read_bytes()) != _non_footprint_nodes(out.read_bytes()):
        problems.append('non-footprint board content (setup, rules, nets, outline, drawings) changed')
    return {'board': str(out), 'board_sha256': digest(out), 'problems': problems,
            'integrity': 'preserved' if not problems else 'violated', 'inventory': after,
            'moved': sorted(row['uuid'] for row in poses if row['moved'])}


def place_labels(check, report, execution, project_bytes, rules_bytes, *, clock, cancel_dir=None):
    """The label step of a placed candidate (AGENTEE-644, ``silk_labels.settle``).

    Labels of the moved footprints that the candidate's native DRC ``report`` names in a
    silkscreen finding are moved to free places beside their parts, and the same native DRC
    judges the saved board again. Returns the report and execution that now describe the
    candidate board, and the record of the step. When a label moved, ``native-drc.json`` is
    the report after the step, the report before it is kept as ``native-drc-before-labels``,
    and ``check`` names the board as it is now.
    """
    from . import silk_labels
    board = Path(check['board'])
    directory = board.parent

    def restore():
        # SaveBoard also writes sibling project files; the job restores the frozen bytes.
        board.with_suffix('.kicad_pro').write_bytes(project_bytes)
        if rules_bytes is not None:
            board.with_suffix('.kicad_dru').write_bytes(rules_bytes)
        board.with_suffix('.kicad_prl').unlink(missing_ok=True)

    def again():
        for suffix in ('.json', '.log'):
            kept = directory / ('native-drc-before-labels' + suffix)
            if not kept.exists():
                os.replace(directory / ('native-drc' + suffix), kept)
        return drc(board, directory, refill=True, label='native-drc', clock=clock, cancel_dir=cancel_dir)

    report, after, record = silk_labels.settle(board, report, check.get('moved') or (), drc=again, restore=restore,
                                               tick=lambda: _check_stop(clock, cancel_dir))
    if after is not None:
        check['board_sha256'] = digest(board)
    return report, after or execution, record


# ---------------------------------------------------------------- incumbent

def _rank(entry):
    return (
        entry.get('placement_introduced_errors', 0),
        len(entry.get('engine_violations', [])),
        entry.get('decoupling_loop_excess_nm', 0),
        entry.get('decoupling_loop_nm', 0),
        int(entry.get('engine_cost', 0)),
    )


def _placement_eligible(entry):
    return (entry.get('integrity') == 'preserved' and entry.get('placement_introduced_errors') == 0
            and entry.get('status') == 'checked' and entry.get('engine_feasible') is True
            and entry.get('edge_intent', {}).get('status') == 'pass'
            and entry.get('locality', {}).get('status', 'not_declared') in ('pass', 'info', 'not_declared')
            and entry.get('mechanics', {}).get('status', 'not_declared') in ('pass', 'not_declared')
            and entry.get('block_contract', {}).get('status', 'not_declared') in ('pass', 'info', 'not_declared')
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


def chosen_placement(job_dir, *, deadline_limited):
    """The placement this job offers, also when it stopped early (AGENTEE-637).

    The checked incumbent, else the best diagnostic candidate, which the engine or native DRC
    rejected and which is never valid. None when no candidate was checked. deadline_limited
    says the search was cut short by the budget or the job ended at its deadline."""
    for kind, name in (('incumbent', 'incumbent.json'), ('best_diagnostic', 'best-diagnostic.json')):
        pointer = Path(job_dir) / name
        if pointer.exists():
            entry = read_json(pointer)
            return {'kind': kind, 'valid': kind == 'incumbent', 'board': entry.get('board'),
                    'board_sha256': entry.get('board_sha256'), 'seed': entry.get('seed'),
                    'engine_cost': entry.get('engine_cost'), 'deadline_limited': bool(deadline_limited)}
    return None


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
    settings = board.GetDesignSettings()
    board_cap = bool(getattr(settings, 'm_CapVias', False))
    board_fill = bool(getattr(settings, 'm_FillVias', False))
    in_pad = []
    unprotected_vias = []
    seen_sites = set()
    for v in vias:
        pos = v.GetPosition()
        drill_nm = v.GetDrillValue()
        barrel_radius = drill_nm // 2
        cap_mode = v.GetCappingMode()
        fill_mode = v.GetFillingMode()
        capped = (True if cap_mode == pcbnew.CAPPING_MODE_CAPPED
                  else False if cap_mode == pcbnew.CAPPING_MODE_NOT_CAPPED
                  else board_cap)
        filled = (True if fill_mode == pcbnew.FILLING_MODE_FILLED
                  else False if fill_mode == pcbnew.FILLING_MODE_NOT_FILLED
                  else board_fill)
        is_protected = (capped and filled)

        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if any(pad.IsOnLayer(layer) and pad.HitTest(pos, barrel_radius, layer)
                       for layer in (pcbnew.F_Cu, pcbnew.B_Cu, pcbnew.F_Paste, pcbnew.B_Paste)):
                    site_key = (v.m_Uuid.AsString(), fp.GetReference(), str(pad.GetNumber()))
                    if site_key not in seen_sites:
                        seen_sites.add(site_key)
                        site_entry = {
                            'via': v.m_Uuid.AsString(),
                            'ref': fp.GetReference(),
                            'pad': pad.GetNumber(),
                            'drill_mm': round(drill_nm / NM, 4),
                            'position_mm': [round(pos.x / NM, 4), round(pos.y / NM, 4)],
                            'capping': capped,
                            'filling': filled,
                            'protected': is_protected,
                            'effective_protection': 'IPC-4761 Type VII' if is_protected else 'unprotected',
                        }
                        in_pad.append(site_entry)
                        if not is_protected:
                            unprotected_vias.append(site_entry)
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
            'via_in_pad': {'count': len(in_pad), 'sites': in_pad, 'unprotected': unprotected_vias,
                           'method': 'via barrel inside native pad copper/paste (HitTest) on F.Cu/B.Cu/F.Paste/B.Paste'},
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
    settings = result.get('settings', {})
    if record.get('argv') is not None and '--diff-pairs' not in record['argv']:
        # No pair handling was requested, and the router reports none.
        pair_free = 'pairs' not in settings and 'diff_pairs' not in settings
    else:
        # The router's own pair plan must be empty.
        pair_free = settings.get('pairs') == []
    if kinds == {'track_dangling'} and all(v == 'PASS' for v in preservation.values()) and pair_free:
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


# The router identity a job records: routing_adapter.preflight's checkout record (the kicad-route
# commit, clean-tree state and code digest), the executable and adapter digests, and its problems.
ROUTER_IDENTITY = ('commit', 'clean', 'code_sha256', 'code_files', 'executable_sha256', 'adapter_sha256', 'problems')


def route(candidate_dir, route_dir, checkout, *, budget_s, timeout_s, home, clock=None, cancel_dir=None,
          design_name='board', grid_step_mm=None, intra_pair_match=None, planes=None, pair_layers=None, pair_impedance=None,
          bus=None, length_match=None, ac_match=None, local_decoupling=None,
          defer_local_fallback=False, local_fallback_reason=None, diff_pairs=None, checks_s=None):
    """kicad-route process on a fresh copy of one materialized candidate.

    The board has no schematic, which the sandboxed routing_adapter step refuses;
    the CLI is invoked with the adapter's own argv shape and identity checks, on a
    clean checkout whose commit the adapter preflight records.

    ``budget_s`` is this trial's share of the job's time. Without ``checks_s`` the router
    may search for all of it, and a search that does so is cut off with the job. With
    ``checks_s`` the search gets only what leaves room for the router's stop grace and
    that many seconds of checks afterwards (``routing_adapter.search_budget_s``), so the
    trial ends inside its share."""
    from . import routing_adapter
    clock = clock or Clock(timeout_s)
    _check_stop(clock, cancel_dir)
    if local_decoupling is not None:
        from . import local_decoupling as local
        try:
            local.validate_topology(local_decoupling)
        except ValueError as exc:
            raise JobRefusal('local decoupling contract: ' + str(exc)) from exc
    check = routing_adapter.preflight(checkout)
    if check['problems']:
        raise EnvironmentFailure('routing-preflight', 'router preflight: ' + '; '.join(check['problems']))
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
    import pcbnew
    owned_copper = planes is not None and not list(pcbnew.LoadBoard(str(staged / 'board.kicad_pcb')).GetTracks())
    local_record = None
    if local_decoupling is not None:
        seed_clock = Clock(min(route_clock.remaining() / 4, clock.remaining()))
        local_dir = route_dir / 'local-decoupling'
        local_dir.mkdir()
        (local_dir / 'before').mkdir()
        (local_dir / 'after').mkdir()
        if isinstance(local_fallback_reason, dict):
            # A deferred candidate owns this already verified generated subset.
            local_record = local_fallback_reason
            native_ids = {t.m_Uuid.AsString() for t in pcbnew.LoadBoard(str(staged / 'board.kicad_pcb')).GetTracks()}
            owned_copper = bool(planes is not None and local_record.get('input_track_free')
                                and native_ids == {r['uuid'] for r in local_record['protected']})
            if local_record['protected']:
                local.verify(staged / 'board.kicad_pcb', local_record)
        else:
            local_record = local.fallback_receipt(staged / 'board.kicad_pcb', local_decoupling, '')
            local_record['fallbacks'] = []
            local_record['input_track_free'] = owned_copper
            local_record['generation_order'] = dict(selected='per-bank', attempts=[])
            for bank in local.banks(local_decoupling):
                bank_dir = local_dir / bank['id']
                bank_dir.mkdir()
                (bank_dir / 'before').mkdir()
                (bank_dir / 'after').mkdir()
                originals = {path.name: path.read_bytes() for path in staged.iterdir() if path.is_file()}
                members = [r for r in local_decoupling['relations'] if r['id'] in bank['relations']]
                document = dict(local_decoupling, relations=members, tie_other_pads=False)
                document.pop('banks', None)  # Derive the subset's bank inventory for the strict producer.
                refusal = local_fallback_reason
                bank_record = None
                try:
                    if refusal is not None:
                        raise JobRefusal(refusal)
                    baseline, _ = drc(staged / 'board.kicad_pcb', bank_dir / 'before',
                                      clock=seed_clock, cancel_dir=cancel_dir)
                    bank_record = local.seed(staged / 'board.kicad_pcb', document,
                                             clock=seed_clock, cancel_dir=cancel_dir)
                    write_json(bank_dir / 'seed.json', bank_record)
                    seeded, _ = drc(staged / 'board.kicad_pcb', bank_dir / 'after', refill=True,
                                    clock=seed_clock, cancel_dir=cancel_dir)
                    existing = {_key(v) for v in baseline['violations'] if v.get('severity') == 'error'}
                    errors = [v for v in seeded['violations'] if v.get('severity') == 'error' and _key(v) not in existing]
                    if errors:
                        error_items = {item['uuid'] for error in errors for item in error.get('items', []) if 'uuid' in item}
                        names = ', '.join(row['id'] for row in bank_record['relations'] if error_items.intersection(row['copper']))
                        attribution = 'local decoupling ' + names + ': ' if names else ''
                        raise JobRefusal(attribution + 'native DRC rejects generated copper: ' +
                                         ', '.join(v['type'] for v in errors))
                    combined = dict(local_record, relations=local_record['relations'] + bank_record['relations'],
                                    protected=local_record['protected'] + bank_record['protected'])
                    local.verify(staged / 'board.kicad_pcb', combined)
                except JobRefusal as exc:
                    refusal = str(exc)
                except JobStopped as exc:
                    if exc.status != 'deadline' or clock.remaining() <= 0:
                        raise
                    refusal = 'bounded generation time share exhausted'
                if refusal is not None:
                    write_json(bank_dir / 'failure.json', {'reason': refusal})
                    rejected = bank_dir / 'rejected'
                    rejected.mkdir()
                    for path in staged.iterdir():
                        if path.is_file():
                            shutil.copyfile(path, rejected / path.name)
                    # Restore the failed bank only, retaining earlier banks' copper.
                    for path in staged.iterdir():
                        if path.is_file() and path.name not in originals:
                            path.unlink()
                    for name, raw in originals.items():
                        (staged / name).write_bytes(raw)
                    local_record['fallbacks'] += local.bank_fallbacks(bank, members, refusal)
                else:
                    local_record = combined
                local_record['generation_order']['attempts'].append(dict(bank=bank['id'],
                    status='fell_back' if refusal is not None else 'generated',
                    order=bank_record.get('generation_order') if bank_record else None))
            local_record['board_sha256'] = digest(staged / 'board.kicad_pcb')
            if local_record['protected']:
                local.verify(staged / 'board.kicad_pcb', local_record)
            if local_decoupling['tie_other_pads']:
                mentioned = {local.endpoint_key(p) for r in local_decoupling['relations']
                             for leg in ('supply', 'return') for p in r[leg]}
                native = pcbnew.LoadBoard(str(staged / 'board.kicad_pcb'))
                local_record['other_pads'] = [dict(pad=p.m_Uuid.AsString(), status='left_to_router',
                    reason='optional plane pads left to router by bank-local generation')
                    for fp in native.GetFootprints() for p in fp.Pads()
                    if any(p.GetNetname() == plane['net'] for plane in local_decoupling['planes'])
                    and (fp.GetReference(), p.GetNumber(), p.GetNetname()) not in mentioned]
        write_json(local_dir / 'seed.json', local_record)
        if local_record.get('fallbacks') and defer_local_fallback and local_fallback_reason is None:
            return dict(status='local_copper_deferred', preflight=check,
                        reason='; '.join(r['reason'] for r in local_record['fallbacks']),
                        prepared_directory=str(staged), local_decoupling=local_record,
                        generated_relation_count=len(local_record['relations']),
                        elapsed_s=time.monotonic() - started)
    _check_stop(clock, cancel_dir)
    # The CLI accepts integral seconds. Refuse sub-second remainder rather than
    # inventing a minimum extension; the outer clock also bounds all descendants.
    remaining = min(budget_s - (time.monotonic() - started), clock.remaining())
    if checks_s is not None:
        remaining = routing_adapter.search_budget_s(remaining, checks_s)
        if remaining < 1:
            # A trial that keeps time for its checks ends in order when none is left for a search.
            return {'status': 'no_time_for_search', 'preflight': check, 'elapsed_s': time.monotonic() - started}
    if remaining < 1:
        if planes is None:
            raise JobStopped('deadline')
        return {'status': 'plane_deadline', 'preflight': check,
                'elapsed_s': time.monotonic() - started}
    # These jobs normally start without copper. Prove that on the actual native
    # input before allowing the router to revisit its own generated tracks.
    argv = routing_adapter.board_route_argv(
        checkout, staged, route_dir / 'out', design_name=design_name, owned_copper=owned_copper,
        budget_s=remaining, grid_step_mm=grid_step_mm, pair_layers=pair_layers,
        pair_impedance=pair_impedance, intra_pair_match=intra_pair_match,
        bus=bus, length_match=length_match, ac_match=ac_match, diff_pairs=diff_pairs)
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
            repair_s = (route_clock.remaining() if checks_s is None
                        else routing_adapter.search_budget_s(route_clock.remaining(), checks_s))
            if repair_s < 1:
                break
            retry, pads = routing_adapter.board_retry_argv(
                argv, retry_dir, repair_s, feedback, pads, record.get('board'))
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
                  copper_policy=('locked_local_copper_from_track_free_input' if owned_copper and local_record is not None and local_record['protected']
                                 else 'generated_from_track_free_input' if owned_copper else 'preserve_existing'))
    if plane_record is not None:
        record['planes'] = plane_record
    if local_record is not None:
        record['local_decoupling'] = local_record
        record['generated_relation_count'] = len(local_record['relations'])
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
    except (JobRefusal, EnvironmentFailure) as exc:
        if not ownership['established'] and not isinstance(exc, EnvironmentFailure):
            raise
        report.update(status='refused', reason=str(exc), phases=clock.phases,
                      elapsed_s=round(time.monotonic() - clock.start, 3))
        if isinstance(exc, EnvironmentFailure):
            report.update(failure_kind='environment', failing_stage=exc.stage)
        report.setdefault('attempt', None)
        if not ownership['established']:
            return report  # A foreign resume directory must remain byte-identical.
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
        # A stopped job still names the best placement it checked; it never ends with nothing
        # when a candidate exists.
        report['placement'] = chosen_placement(job_dir, deadline_limited=True)
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
        diff_pairs = router_options['diff_pairs']
        unknown = set(config['inputs']) - {'board', 'project', 'rules', 'intent', 'groups', 'locality', 'planes', 'group_rails', 'block_contract', 'mechanics', 'local_decoupling'}
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
        if 'block_contract' in config['inputs']:
            _, raw = verified_input(config['inputs']['block_contract'], 'block_contract')
            try:
                normalized['block_contract'] = strict_json(raw)
            except ValueError as exc:
                raise JobRefusal('inputs.block_contract JSON: ' + str(exc)) from exc
            if not isinstance(normalized['block_contract'], dict):
                raise JobRefusal('inputs.block_contract: expected object')
        if 'mechanics' in config['inputs']:
            from .source_mechanics import load
            path, _ = verified_input(config['inputs']['mechanics'], 'mechanics')
            normalized['mechanics'] = load(path)
        report['intent_contract'] = normalized['intent_contract']
        # Which groups pull their members toward an anchor: only declared ones (AGENTEE-637).
        from .placement_intent import group_pull
        report['groups'] = group_pull(normalized['groups'])
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
        local_contract = None
        if 'local_decoupling' in config['inputs']:
            from .local_decoupling import validate as validate_local
            _, raw = verified_input(config['inputs']['local_decoupling'], 'local_decoupling')
            try:
                local_contract = validate_local(strict_json(raw))
            except ValueError as exc:
                raise JobRefusal('invalid local decoupling contract: ' + str(exc)) from exc
            if local_contract['planes'] and (plane_bytes is None or any(
                    not any(z['net'] == p['net'] and z['layer'] == p['layer'] for z in plane_plan['zones'])
                    for p in local_contract['planes'])):
                raise JobRefusal('local decoupling planes differ from captured plane plan')
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
        if 'mechanics' in normalized:
            if 'mechanics' not in record:
                raise JobRefusal('native capture omitted mechanics binding')
            if (record['mechanics']['recipe'] != normalized['mechanics']
                    or record['mechanics']['original_board_sha256'] != sha256_bytes(board_bytes)):
                raise JobRefusal('mechanics differs from the original source capture')
            normalized['mechanics_binding'] = record['mechanics']
        if 'block_contract' in normalized:
            if 'block_contract' not in record:
                raise JobRefusal('native capture omitted the requested block contract binding')
            normalized['block_contract_binding'] = record['block_contract']
        for name in ('board', 'project', *(['rules'] if rules_bytes is not None else [])):
            verified_input(config['inputs'][name], name)
    if 'mechanics' in normalized:
        report['preflight']['pose_solver'] = _pose_solver(
            config['router']['checkout'], {'operation': 'identity'},
            clock=Clock(min(30, clock.remaining())), cancel_dir=capture_cancel)
    identity = sha256_bytes(canonical({'problem': problem['problem_sha256'], 'config': report['config_sha256'],
                                       'engine': report['preflight']['engine_sha256'],
                                       'controller': digest(Path(__file__)),
                                       **({'local_decoupling': local_contract,
                                           'local_decoupling_modules': {name: digest(Path(__file__).parent / name) for name in
                                               ('local_decoupling.py', 'decoupling_loops.py', 'local_loop_geometry.py', 'acquisition/local_return.py')}}
                                          if local_contract is not None else {}),
                                       **({'mechanics_controller': digest(Path(__file__).with_name('source_mechanics.py'))}
                                          if 'mechanics' in normalized else {}),
                                       **({name: digest(Path(__file__).with_name(name+'.py')) for name in
                                           ('source_mechanics', 'local_geometry', 'local_return_seed', 'pose_milp')}
                                          if 'mechanics' in normalized else {}),
                                       **({'pose_solver': report['preflight']['pose_solver']}
                                          if 'mechanics' in normalized else {}),
                                       **({'block_contract': record['block_contract']['binding_sha256']}
                                          if 'block_contract' in normalized else {}),
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
    share = search.pop('time_share', SEARCH_TIME_SHARE)
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share < 1:
        raise JobRefusal('search.time_share: expected a number above 0 and below 1')

    def on_seed(event):
        state['completed_seeds'] = sorted(set(state['completed_seeds']) | (
            {event['seed']} if event['status'] == 'completed' else set()))
        state['seed_results'][str(event['seed'])] = event
        checkpoint(job_dir, state)

    with clock.phase('search'):
        # The engine gets a share of the time left and sizes its seeds and steps to it; the
        # controller waits a little longer for its answer, never past the job deadline.
        search_s = max(0.0, clock.remaining()) * share
        output, engine = solve(problem, search, job_dir, deadline_s=search_s,
                               executable=executable, on_seed=on_seed,
                               clock=Clock(min(clock.remaining(), search_s + SEARCH_GRACE_S)))
        write_json(job_dir / 'engine-output.json', output)
        write_json(job_dir / f'engine-output-a{attempt}.json', output)
    report['engine'] = {**engine, 'status': output['status'], 'counters': output['counters'],
                        'seeds': output['seeds'], 'prng': output['prng'], 'budget': output.get('budget')}
    # 'deadline' here is the search's own time, not the job's: the candidates found so far go
    # on to the checks and the router, marked as limited. Only a cancel stops the job.
    search_limited = output['status'] == 'deadline' or bool((output.get('budget') or {}).get('limited'))
    report['search'] = {'time_share': share, 'budget_s': round(search_s, 3), 'status': output['status'],
                        'limited_by_budget': search_limited}
    if output['status'] == 'cancelled':
        raise JobStopped('cancelled')
    _check_stop(clock, job_dir)
    if output['timing'].get('prepare_s') is not None:
        clock.phases['prepare'] = round(output['timing']['prepare_s'], 6)
    # Earlier seeds of a resumed job compete with the new ones on equal terms.
    shortlist = list(output['shortlist'])
    for seed, event in state['seed_results'].items():
        if event.get('feasible') and int(seed) not in {c['seed'] for c in shortlist} and event.get('poses'):
            shortlist.append({'seed': int(seed), 'cost': event['cost'], 'poses': event['poses'], 'resumed': True})
    shortlist.sort(key=lambda c: (int(c['cost']), c['seed']))
    shortlist = [dict(c, feasible=True) for c in shortlist[:int(config['search'].get('shortlist', 3))]]
    diagnostics = list(output.get('diagnostic_shortlist', []))
    if not diagnostics and not shortlist:
        # An interrupted engine sent no final record; the heartbeats of its finished seeds did.
        diagnostics = _search_diagnostics(output, state)[:int(config['search'].get('shortlist', 3))]
    if output['status'] == 'deadline' and not shortlist and not diagnostics:
        raise JobStopped('deadline')   # the search time ended before any state could be kept
    if 'mechanics' in normalized:
        diagnostics = _search_diagnostics(output, state)[:int(config['search'].get('shortlist', 3))]
        with clock.phase('placement_repair'):
            shortlist = _repair_shortlist(problem, shortlist, diagnostics,
                checkout=config['router']['checkout'], runtime=report['preflight']['pose_solver'],
                clock=clock, job_dir=job_dir, attempt=attempt, report=report,
                executable=report['preflight']['engine'], engine_sha256=report['preflight']['engine_sha256'])
    if not shortlist:
        # No hard-feasible state: least-infeasible states, with their exact violations,
        # are checked natively as DIAGNOSTIC candidates and can never be promoted.
        shortlist = diagnostics
    report['engine_feasible'] = any(c.get('feasible') for c in shortlist)
    import pcbnew
    native_input = pcbnew.LoadBoard(str(board_path))
    owners, rigid = _owners(native_input), _rigid(native_input)
    with clock.phase('judge'):
        _check_stop(clock, job_dir)
        (job_dir / f'input-drc-a{attempt}').mkdir()
        # Every DRC of this job refills zones, so a board with pours is judged on a current fill.
        base_report, base_exec = drc(board_path, job_dir / f'input-drc-a{attempt}', refill=True, label='native-drc',
                                     clock=clock, cancel_dir=job_dir)
        base_keys = error_keys(base_report)
        def judge_candidate(cand, index):
            _check_stop(clock, job_dir)
            decoupling_loop_nm = int(cand.get('terms', {}).get('decoupling_loop', 0))
            decoupling_excess_nm = sum(
                max(0, int(v.get('loop_nm', 0)) - int(v.get('max_loop_nm', 0)))
                for v in cand.get('violations', []) if v.get('kind') == 'decoupling_loop_length'
            )
            entry = {'index': index, 'seed': cand['seed'], 'engine_cost': cand['cost'], 'status': 'failed',
                     'engine_feasible': bool(cand.get('feasible')), 'engine_violations': cand.get('violations', []),
                     'decoupling_loop_nm': decoupling_loop_nm,
                     'decoupling_loop_excess_nm': decoupling_excess_nm}
            directory = job_dir / 'candidates' / f'a{attempt}-{index:02d}-seed{cand["seed"]}'
            try:
                check = materialize(board_path, cand['poses'], directory, project_bytes,
                                    normalized=normalized, rules_bytes=rules_bytes)
                _check_stop(clock, job_dir)
                report_, exec_ = drc(directory / 'board.kicad_pcb', directory, refill=True, label='native-drc',
                                     clock=clock, cancel_dir=job_dir)
                report_, exec_, labels = place_labels(check, report_, exec_, project_bytes, rules_bytes,
                                                      clock=clock, cancel_dir=job_dir)
                split = classify_errors(report_, owners, base_keys, moved=check.get('moved'), rigid=rigid)
                entry.update(status='checked', integrity=check['integrity'], integrity_problems=check['problems'],
                             moved_footprints=check.get('moved'), labels=labels,
                             mechanics=check_mechanics(directory / 'board.kicad_pcb', normalized, clock=clock, cancel_dir=job_dir),
                             block_contract=check_block_contract(directory / 'board.kicad_pcb', normalized,
                                                                  clock=clock, cancel_dir=job_dir),
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
            return entry
        trials = [judge_candidate(cand, index) for index, cand in enumerate(shortlist)]
        report['shortlist'] = trials
        report['input_drc'] = {**base_exec, 'errors': sum(base_keys.values())}
    incumbent = read_json(job_dir / 'incumbent.json') if (job_dir / 'incumbent.json').exists() else None
    diagnostic = read_json(job_dir / 'best-diagnostic.json') if (job_dir / 'best-diagnostic.json').exists() else None
    keys = ('index', 'seed', 'board_sha256', 'errors', 'engine_violations')
    report['incumbent'] = incumbent and {k: incumbent.get(k) for k in keys}
    report['best_diagnostic'] = diagnostic and {k: diagnostic.get(k) for k in keys}
    # Exploratory routing of the best diagnostic candidate is allowed (rule 7); it is never valid.
    selected, report['routed_candidate'] = ((incumbent, 'incumbent') if incumbent is not None
                                            else (diagnostic, 'best_diagnostic_exploratory'))
    report['placement'] = chosen_placement(job_dir, deadline_limited=search_limited)
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
    from . import routing_adapter
    second_trials, asked, best_failed = [], set(), None
    # A resumed job has already seen its first trials fail on the router's default grid.
    resumed_after_failure = bool(state.get('routing_trials'))
    def routing_candidates():
        index, repairs_tried = 0, False
        while True:
            # Discover mechanics alternatives before trying a reduced-generation
            # candidate, so the same generation-count preference includes repairs.
            if not repairs_tried and (index >= len(queue) or queue[index].get('local_fallback_reason') is not None):
                repairs_tried = True
                if incumbent is not None and 'mechanics' in normalized:
                    with clock.phase('placement_repair'):
                        alternatives = _repair_shortlist(problem, shortlist, diagnostics,
                            checkout=config['router']['checkout'], runtime=report['preflight']['pose_solver'],
                            clock=clock, job_dir=job_dir, attempt=attempt, report=report,
                            executable=report['preflight']['engine'], engine_sha256=report['preflight']['engine_sha256'],
                            routing_exhausted=True)
                    for cand in alternatives:
                        with clock.phase('judge'):
                            entry = judge_candidate(cand, len(trials))
                            trials.append(entry)
                        if _placement_eligible(entry) and entry['board_sha256'] not in seen:
                            entry['routing_repair'] = True
                            queue.append(entry)
                            seen.add(entry['board_sha256'])
                    maximum = len(local_contract['relations']) if local_contract else 0
                    queue[index:] = sorted(queue[index:], key=lambda row: -row.get('generated_relation_count', maximum))
            if index >= len(queue) and second_trials:
                # Second trials use only the time the first trials left, in equal shares, and one
                # starts only when its share lets the search run as long as the first trial took.
                waiting = sorted(second_trials, key=lambda row: row['second_trial']['first_elapsed_s'])
                second_trials.clear()
                while waiting and routing_adapter.search_budget_s(clock.remaining() / len(waiting), ROUTE_CHECK_ALLOWANCE_S) < max(
                        1., waiting[-1]['second_trial']['first_elapsed_s']):
                    late = waiting.pop()
                    report.setdefault('second_trials_not_started', []).append({
                        'seed': late['seed'], 'board_sha256': late['board_sha256'],
                        'first_trial': late['second_trial']['first_trial'],
                        'grid_step_mm': late['second_trial']['grid_step_mm'],
                        'reason': 'the job has less time left than the first trial took'})
                queue.extend(waiting)
            if index >= len(queue):
                return
            yield index, queue[index]
            index += 1

    def retain_routing_trial(trial, suffix):
        receipt = job_dir / f'routing-trial-{suffix}.json'
        write_json(receipt, trial)
        state.setdefault('routing_trials', []).append({'path': str(receipt), 'sha256': digest(receipt)})
        checkpoint(job_dir, state)
        report['route_selection'] = {k: trial[k] for k in ('seed', 'board_sha256', 'directory', 'status')}
        # Keep failed candidates visible even if a later proposal is interrupted.
        write_json(job_dir / f'job-report-a{attempt}.json', report)
        write_json(job_dir / 'job-report.json', report)
        return receipt

    for number, selected in routing_candidates():
        report['routed_candidate'] = ('deferred_incumbent' if selected.get('local_fallback_reason') is not None
            and incumbent is not None and selected['board_sha256'] == incumbent['board_sha256'] else
            'incumbent' if incumbent is not None and selected['board_sha256'] == incumbent['board_sha256'] else
            'placement_repair' if selected.get('routing_repair') else
            'best_diagnostic_exploratory' if incumbent is None else 'shortlist_alternative')
        suffix = f'a{attempt}' + (f'-r{number + 1}' if number else '')
        # Equal shares of the remaining job time prevent one failed routing
        # search from taking the entire portfolio budget. This is scheduling
        # policy, not an engineering relaxation; the outer deadline still wins.
        budget = clock.remaining() / (len(queue) - number)
        # Every candidate's first trial is the cheap one. The finer grid comes with the second
        # trials, or first in a job resumed with failed trials retained (an interrupted first
        # trial leaves none).
        second = selected.get('second_trial')
        grid_step, grid_basis = ((second['grid_step_mm'], second['grid_basis']) if second else router_grid(
            config, after_failed_route=resumed_after_failure))
        trial = {'seed': selected['seed'], 'board_sha256': selected['board_sha256'],
                 'directory': selected['directory'], 'status': 'running', 'router_budget_s': budget,
                 'budget_basis': 'agent-default/equal-remaining-share',
                 'grid_step_mm': grid_step, 'grid_basis': grid_basis}
        if second:
            trial.update(second_trial_of=second['first_trial'], checks_allowance_s=ROUTE_CHECK_ALLOWANCE_S)
        if 'original_router_budget_s' in selected:
            trial['original_router_budget_s'] = selected['original_router_budget_s']
            trial['deferral_budget_reduced'] = budget < selected['original_router_budget_s']
            trial['deferral_budget_loss_s'] = max(0., selected['original_router_budget_s'] - budget)
        report['routing_trials'].append(trial)
        with clock.phase('export'):
            _check_stop(clock, job_dir)
            route_input = selected.get('local_prepared_directory', selected['directory'])
            local_seed = selected.get('local_routing_seed')
            if local_seed is not None:
                from . import local_return_seed
                trial['local_routing'] = local_return_seed.verify(
                    Path(route_input) / 'board.kicad_pcb', local_seed, clock=clock, cancel_dir=job_dir)
            elif 'mechanics' in normalized and local_routing_applies(normalized['mechanics']):
                # Seed only the mechanics recipe's named local loops, and never an
                # exploratory or invalid pose. Other designs route without this seed.
                if (not _placement_eligible(selected)
                        or selected.get('mechanics', {}).get('status') != 'pass'
                        or selected['mechanics'].get('binding_sha256') != normalized['mechanics_binding']['binding_sha256']
                        or selected['mechanics'].get('board_sha256') != selected['board_sha256']):
                    raise JobRefusal('local routing requires an eligible placement')
                from . import local_return_seed
                from .local_return_seed import LocalSearchExhausted
                seed_clock = Clock(min(budget, clock.remaining()))
                local_directory=job_dir / f'local-routing-{suffix}'
                try:
                    local_seed = local_return_seed.produce(route_input, local_directory,
                        expected_inputs={'board.kicad_pcb': selected['board_sha256'],
                                         'board.kicad_pro': sha256_bytes(project_bytes),
                                         'board.kicad_dru': sha256_bytes(rules_bytes) if rules_bytes is not None else None},
                        mechanics_binding=normalized['mechanics_binding'], planes=plane_bytes,
                        clock=seed_clock, cancel_dir=job_dir)
                except LocalSearchExhausted as exc:
                    _check_stop(clock, job_dir)
                    status='local_search_exhausted'
                    trial.update(status=status, error=str(exc), local_routing={
                        'record':str(local_directory/'record.json'),
                        'record_sha256':digest(local_directory/'record.json')})
                    retain_routing_trial(trial, suffix)
                    continue
                route_input = local_seed['output']
                trial['local_routing'] = local_return_seed.verify(
                    Path(route_input) / 'board.kicad_pcb', local_seed, clock=seed_clock, cancel_dir=job_dir)
                budget = min(seed_clock.remaining(), clock.remaining())
                _check_stop(seed_clock, job_dir)
            routed = route(route_input, job_dir / f'route-{suffix}', config['router']['checkout'],
                           budget_s=budget, timeout_s=clock.remaining(), home=job_dir / f'route-home-{suffix}',
                           clock=clock, cancel_dir=job_dir, design_name=report['design_name'],
                           grid_step_mm=grid_step,
                           intra_pair_match=matching, planes=plane_bytes, pair_layers=pair_layers, pair_impedance=impedance,
                           bus=bus, length_match=length_match, ac_match=ac_match, local_decoupling=local_contract,
                           defer_local_fallback=len(queue) > 1,
                           local_fallback_reason=selected.get('local_fallback_reason'), diff_pairs=diff_pairs,
                           checks_s=ROUTE_CHECK_ALLOWANCE_S if second else None)
            report['route'] = {k: v for k, v in routed.items() if k != 'preflight'}
            if routed.get('failure_kind') == 'environment':
                trial.update(status=routed['status'], route=report['route'])
                retain_routing_trial(trial, suffix)
                raise EnvironmentFailure(routed['failing_stage'], routed['reason'])
            trial['generated_relation_count'] = routed.get('generated_relation_count', 0)
            report['router_identity'] = {k: routed['preflight'][k] for k in ROUTER_IDENTITY}
            if routed['status'] == 'router_refused':
                # Input/ledger refusals concern the project's nets and rules, which are
                # the same for every placement. Return them to the author without
                # another candidate or seed; runtime/tool refusals were handled above.
                refusal = routed['refusal']
                status = 'router_refused'
                trial.update(status=status, route=report['route'], router_refusal=refusal)
                report.update(router_refusal=refusal,
                              reason=f"kicad-route refused ({refusal['reason_class']}): {refusal['message']}")
                retain_routing_trial(trial, suffix)
                break
            if routed['status'] == 'local_copper_deferred':
                # Unknown candidates get generation first; retained subsets rank by count.
                queue.append({**selected, 'local_fallback_reason': routed.get('local_decoupling', routed['reason']),
                              'local_prepared_directory': routed.get('prepared_directory', selected['directory']),
                              'generated_relation_count': routed.get('generated_relation_count', 0),
                              'original_router_budget_s': budget, 'local_routing_seed': local_seed})
                total_relations = len(local_contract['relations']) if local_contract else 0
                queue[number + 1:] = sorted(queue[number + 1:],
                    key=lambda row: -row.get('generated_relation_count', total_relations))
                status = 'local_copper_deferred'
                trial.update(status=status, reason=routed['reason'], route=report['route'])
                report['reason'] = routed['reason']
                retain_routing_trial(trial, suffix)
                continue
            report.pop('reason', None)
        with clock.phase('final_validation'):
            _check_stop(clock, job_dir)
            selected_seed = next((row for row in report['engine']['seeds'] if row.get('seed') == selected['seed']), {})
            report['final'] = final_checks(routed, selected, owners, base_keys, job_dir / f'final-{suffix}', normalized,
                                           refinement=selected_seed.get('refinement'),
                                           clock=clock, cancel_dir=job_dir,
                                           owned_zone_uuids=plane_ids, planes=plane_bytes,
                                           pair_impedance=impedance, router_checkout=config['router']['checkout'],
                                           length_match=length_match, ac_match=ac_match,
                                           local_routing=local_seed,
                                           input_board=board_path, input_board_sha256=sha256_bytes(board_bytes),
                                           reject_silk_graphics=config.get('reject_silk_graphic_conflicts') is True,
                                           reject_track_dangling=config.get('reject_track_dangling') is True,
                                           rigid=rigid)
            if local_seed is not None and report['final'].get('board'):
                seed_check = report['final']['local_routing']
                if (routed.get('copper_policy') != 'preserve_existing'
                        or seed_check['board_sha256'] != report['final'].get('board_sha256')):
                    raise JobRefusal('acquisition route did not preserve the checked local routing')
        _check_stop(clock, job_dir)
        status = _completion_status(routed, selected if incumbent is not None else None, report['final'],
                                    engine_feasible=report['engine_feasible'])
        if status != 'completed':
            # The job says why, in words; the next trial or a completed one replaces it.
            report['reason'] = trial['reason'] = _rejection_reason(status, routed, report['final'])
        trial.update(status=status, route=report['route'], final=report['final'])
        receipt = retain_routing_trial(trial, suffix)
        if status == 'completed':
            report.pop('reason', None)
            tmp = job_dir / '.routed-incumbent.json.tmp'
            write_json(tmp, {'placement': selected, 'trial': str(receipt), 'trial_sha256': digest(receipt)})
            os.replace(tmp, job_dir / 'routed-incumbent.json')
            break
        if (not second and second_trial_useful(status, routed, report['final'])
                and selected['board_sha256'] not in asked and (other := other_grid(grid_step, grid_basis)) is not None):
            asked.add(selected['board_sha256'])
            second_trials.append({**selected, 'second_trial': {
                'first_trial': number + 1, 'first_elapsed_s': float(routed.get('elapsed_s') or 0.),
                'grid_step_mm': other[0], 'grid_basis': other[1]}})
        # No trial has completed so far. The job reports the best board among the failed ones,
        # also when it is stopped at its deadline during a later trial; an equal later board
        # does not replace an earlier one.
        reported = ('reason', 'final', 'route', 'route_selection', 'routed_candidate')
        rank = trial_rank(report['final'])
        if best_failed is None or rank < best_failed[0]:
            best_failed = (rank, {'status': status, **{key: report[key] for key in reported}})
        else:
            status = best_failed[1]['status']
            report.update({key: best_failed[1][key] for key in reported})
    _check_stop(clock, job_dir)
    report['placement'] = chosen_placement(job_dir, deadline_limited=search_limited)
    report['phases'] = clock.phases
    report['elapsed_s'] = round(time.monotonic() - clock.start, 3)
    report['status'] = status
    report['validity'] = ('integrity-valid placement; physical checks ' + report.get('final', {}).get('physical_checks', 'unavailable')
                          if incumbent is not None else
                          'NO valid incumbent: exploratory route of the best diagnostic candidate (engine violations: '
                          + ', '.join(sorted({v['kind'] for v in selected.get('engine_violations', [])})) + ')')
    write_json(job_dir / f'job-report-a{attempt}.json', report)
    write_json(job_dir / 'job-report.json', report)
    return report


def _unrepairable_track_warnings(report, *, reject_track_dangling=False):
    """Build artwork moves fields; it cannot remove a package-fatal copper stub."""
    if not reject_track_dangling:
        return []
    return [v for v in report.get('violations', []) if v.get('type') == 'track_dangling']


def _silk_graphic_conflicts(report):
    """Silk findings that name no field, so moving fields cannot fix them.

    KiCad 10 describes a field as ``Reference field of J2``, and gives a text item's content in
    ``PCB Text 'field of view' on F.Silkscreen`` or ``Footprint text of U1 (field of view)``.
    """
    from .silk_artwork import ARTWORK_CONFLICTS
    def names_a_field(description):
        return (re.match(r'[^(]* field of ', description) is not None
                and not description.lower().startswith(('pcb text', 'footprint text')))
    return [violation for violation in report['violations'] if violation['type'] in ARTWORK_CONFLICTS
            and not any(names_a_field(item['description']) for item in violation['items'])]


def _completion_status(routed, incumbent, final, *, engine_feasible=True):
    """Native rejection or absent checks cannot be overridden by router success.

    With no incumbent and no hard-feasible engine state, the routed board is only the
    exploratory route of a diagnostic candidate. Its router and native outcomes cannot
    make the job pass, so the job reports why: ``placement_infeasible``.
    """
    if incumbent is None and not engine_feasible:
        return 'placement_infeasible'
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


def _rejection_reason(status, routed, final):
    """Why a routed trial did not complete, in words: every check that rejected it."""
    if status == 'route_failed':
        return 'the router delivered no board: ' + str(routed.get('reason') or routed.get('status'))
    reasons = []
    if status == 'placement_infeasible':
        reasons.append('the search found no placement without a hard violation; '
                       'the routed board is an exploratory route of a diagnostic candidate')
    if routed.get('router_accepted') is False:
        graded = (routed.get('classification') or {}).get('reasons') or ['no reason recorded']
        reasons.append("the router's own grading rejected its result: " + '; '.join(str(r) for r in graded))
    reasons += final.get('rejections') or []
    if status == 'placement_failed':
        reasons.append('no placement passed its checks; the routed board is an exploratory route')
    return '; '.join(reasons) or 'final board is ' + str(final.get('eligibility', 'not checked'))


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
    try:
        done = _run_child(argv, clock=clock, cancel_dir=cancel_dir)
    except EnvironmentFailure as exc:
        # A helper that cannot start or crashes is a note like one that refuses: the picture
        # supplements the record. A deadline or a cancel still stops the job.
        result['problems'].append(f'group view helper did not finish: {exc}')
        return result
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
    if not board.is_file():
        problems.append('final board missing after its views were rendered')
    elif digest(board) != final.get('board_sha256'):
        problems.append('final board changed after its views were rendered')
    return problems


def _verify_local_routing(board, incumbent, normalized, planes, record, *, clock, cancel_dir):
    """Grant reservation ownership only from the original placement's native proof."""
    from . import local_return_seed
    expected = local_return_seed.identity(local_return_seed.capture(Path(incumbent['directory'])))
    binding = normalized.get('mechanics_binding', {}).get('binding_sha256')
    if (record.get('inputs') != expected or expected['board.kicad_pcb'] != incumbent['board_sha256']
            or digest(Path(incumbent['board'])) != incumbent['board_sha256']
            or not binding or record.get('mechanics_binding_sha256') != binding
            or not isinstance(planes, bytes) or record.get('planes_raw') != planes.decode('utf-8')):
        raise JobRefusal('local routing: final proof differs from captured placement/mechanics/planes')
    result = local_return_seed.verify(board, record, clock=clock, cancel_dir=cancel_dir)
    if result.get('status') != 'pass' or result.get('board_sha256') != digest(Path(board)):
        raise JobRefusal('local routing: native ownership proof is not bound to the final board')
    return result


def _cleanup_local_router_stubs(board, report, receipt, directory, *, owned_zone_uuids=(), clock, cancel_dir):
    """Trial a redundant router-only removal; every existing final gate still runs."""
    warnings = {item['uuid'] for v in report['violations'] if v.get('type') == 'track_dangling'
                for item in v.get('items', []) if 'uuid' in item}
    if not warnings or report['unconnected_items'] or any(v.get('severity') == 'error' for v in report['violations']):
        return dict(status='not_applicable')
    directory.mkdir()
    before = digest(board)
    shutil.copyfile(board, directory / 'before.kicad_pcb')
    write_json(directory / 'before-drc.json', report)
    request, result, trial = directory/'request.json', directory/'result.json', directory/'board.kicad_pcb'
    write_json(request, dict(board=str(board), board_sha256=before, output=str(trial), result=str(result),
                             protected=receipt['protected'], warnings=sorted(warnings)))
    done = _run_child([sys.executable,'-B','-m','agent_ee.local_decoupling',str(request)], clock=clock, cancel_dir=cancel_dir)
    (directory/'prepare.log').write_bytes(done.stdout + done.stderr)
    record = read_json(result) if result.is_file() else dict(status='rejected', reason='native stub preparation failed')
    if done.returncode or record.get('status') != 'prepared':
        write_json(directory/'cleanup.json', record)
        return record
    after, execution = drc(trial, directory, refill=True, label='trial-drc', clock=clock, cancel_dir=cancel_dir)
    original = _non_footprint_nodes(board.read_bytes(), include_footprints=True, routing_owned=True, owned_zones=owned_zone_uuids)
    candidate = _non_footprint_nodes(trial.read_bytes(), include_footprints=True, routing_owned=True, owned_zones=owned_zone_uuids)
    safe = (digest(board) == before and record.get('source_sha256') == before
            and record.get('board_sha256') == digest(trial)
            and sorted(original,key=canonical) == sorted(candidate,key=canonical)
            and not after['unconnected_items']
            and not any(v.get('severity') == 'error' or v.get('type') == 'track_dangling' for v in after['violations']))
    record.update(status='accepted' if safe else 'rejected', native_drc=execution)
    if safe:
        from .local_decoupling import verify
        try:
            record['protected'] = verify(trial, receipt)
        except JobRefusal as exc:
            record.update(status='rejected', reason=str(exc))
        else:
            shutil.copyfile(trial, board)
    write_json(directory/'cleanup.json', record)
    return record


def final_checks(routed, incumbent, owners, base_keys, job_dir, normalized, *, refinement=None, clock=None, cancel_dir=None,
                 owned_zone_uuids=(), planes=None, pair_impedance=None, router_checkout=None, length_match=None, ac_match=None,
                 input_board=None, input_board_sha256=None, local_routing=None, reject_silk_graphics=False,
                 reject_track_dangling=False, rigid=None):
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
    native_board = pcbnew.LoadBoard(str(board))
    if planes is None and pour_zones(native_board):
        # The input's own pours: refill them around the routed copper and save, so the
        # board that is judged and handed on holds a current fill. The authored rule
        # files are put back because SaveBoard rewrites the project beside the board.
        try:
            refill_pours(native_board)
            if not pcbnew.SaveBoard(str(board), native_board):
                raise JobRefusal('native SaveBoard failed after zone refill')
        except JobRefusal as exc:
            problems.append(str(exc))
        for suffix in ('.kicad_pro', '.kicad_dru'):
            authored = Path(incumbent['directory']) / ('board' + suffix)
            if authored.exists():
                board.with_suffix(suffix).write_bytes(authored.read_bytes())
        board.with_suffix('.kicad_prl').unlink(missing_ok=True)
    del native_board
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
    local_check=None
    if local_routing is not None:
        local_check=_verify_local_routing(board,incumbent,normalized,planes,local_routing,
                                         clock=clock,cancel_dir=cancel_dir)
        owned_zone_uuids=(*owned_zone_uuids,*local_check['owned_zone_uuids'])
    placed_nodes = _non_footprint_nodes(Path(incumbent['board']).read_bytes(), include_footprints=True,
                                         routing_owned=True, owned_zones=owned_zone_uuids)
    final_nodes = _non_footprint_nodes(board.read_bytes(), include_footprints=True, routing_owned=True,
                                      owned_zones=owned_zone_uuids)
    placed_count, final_count = (Counter(canonical(node) for node in nodes) for nodes in (placed_nodes, final_nodes))
    if placed_count != final_count:
        def kinds(difference):   # what differs, by kind of node, so the report names it
            return ', '.join(f'{kind} {count}' for kind, count in sorted(
                Counter(json.loads(node)[0] for node in difference.elements()).items()))
        lost, new = kinds(placed_count - final_count), kinds(final_count - placed_count)
        problems.append('router changed protected native content (footprints, pads, graphics, setup, outline or drawings): '
                        + '; '.join(part for part in (f'only on the placed board: {lost}' if lost else '',
                                                      f'only on the routed board: {new}' if new else '') if part))
    placed_report = read_json(Path(incumbent['directory']) / 'native-drc.json')
    placed_keys = error_keys(placed_report)
    _check_stop(clock, cancel_dir)
    report_, exec_ = drc(board, final_dir, refill=True, label='native-drc', clock=clock, cancel_dir=cancel_dir)
    drc_report_path = final_dir / 'native-drc.json'
    local_stub_cleanup = None
    if (routed.get('local_decoupling') or {}).get('protected'):
        local_stub_cleanup = _cleanup_local_router_stubs(board, report_, routed['local_decoupling'],
            final_dir / 'local-stub-cleanup', owned_zone_uuids=owned_zone_uuids, clock=clock, cancel_dir=cancel_dir)
        if local_stub_cleanup.get('status') == 'accepted':
            report_, exec_ = drc(board, final_dir, refill=True, label='native-drc-after-stub-prune', clock=clock, cancel_dir=cancel_dir)
            drc_report_path = final_dir / 'native-drc-after-stub-prune.json'
    plane_result = None
    if planes is not None:
        out = final_dir.parent / (final_dir.name + '-plane-verification')
        done = _run_child([sys.executable, '-B', '-m', 'agent_ee.final_planes', 'validate',
            '--inputs', str(final_dir), '--board', board.name, '--plan', 'plane-plan.json',
            '--out', str(out), '--drc', str(drc_report_path),
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
    split = classify_errors(report_, owners, base_keys, placed_keys, moved=incumbent.get('moved_footprints'), rigid=rigid)
    edge_intent = check_edge_intent(board, normalized, clock=clock, cancel_dir=cancel_dir)
    locality = check_locality(board, normalized, clock=clock, cancel_dir=cancel_dir)
    block_result = check_block_contract(board, normalized, clock=clock, cancel_dir=cancel_dir)
    mechanics_result = check_mechanics(board, normalized, clock=clock, cancel_dir=cancel_dir)
    metrics = board_metrics(board, [g for g in groups['groups']], rails=rails, proximity=intent.get('proximity'),
                            fixed_poses=intent.get('fixed_poses', []))
    metrics['edges'] = edge_intent['connectors']
    metrics['refinement'] = refinement
    # Supplemental annotated views of this same saved board (AGENTEE-479 part 2 evidence). Missing or
    # unbound views are visible as incomplete evidence; they never decide eligibility or acceptance.
    group_views = _group_views(board, groups, final_dir.parent / (final_dir.name + '-group-views'),
                               input_board=input_board, input_board_sha256=input_board_sha256,
                               clock=clock, cancel_dir=cancel_dir)
    if local_routing is not None:
        # Native refill/save and the other final consumers ran after the first
        # proof. Recheck the board that will actually be handed downstream.
        local_check=_verify_local_routing(board,incumbent,normalized,planes,local_routing,
                                         clock=clock,cancel_dir=cancel_dir)
    final_board_sha256 = digest(board)
    general_local_check = None
    general_loops = None
    if routed.get('local_decoupling') is not None:
        from .local_decoupling import verify as verify_local
        from .decoupling_loops import measure as measure_loops
        try:
            general_local_check = (verify_local(board, routed['local_decoupling'])
                                   if routed['local_decoupling']['protected'] else dict(status='not_generated'))
        except JobRefusal as exc:
            general_local_check = dict(status='fail', reason=str(exc))
            problems.append(str(exc))
        try:
            general_loops = measure_loops(board, routed['local_decoupling']['contract'],
                                          generation=routed['local_decoupling'])
        except (JobRefusal, ValueError) as exc:
            general_loops = dict(outcome='unknown', reasons=[str(exc)], warnings=[])
            problems.append(str(exc))
        write_json(final_dir / 'decoupling-loops.json', general_loops)
    if block_result['status'] != 'not_declared' and block_result['board_sha256'] != final_board_sha256:
        problems.append('final board changed after block contract check')
    if mechanics_result.get('status') != 'not_declared' and mechanics_result.get('board_sha256') != final_board_sha256:
        problems.append('final board changed after acquisition mechanics check')
    for s in metrics.get('via_in_pad', {}).get('sites', []):
        if not s.get('protected', True):
            problems.append(f"unprotected via-in-pad: {s['ref']} pad {s['pad']} at {s['position_mm']} mm lacks Type VII fill/cap")
    silk_conflicts = _silk_graphic_conflicts(report_)
    track_conflicts = _unrepairable_track_warnings(report_, reject_track_dangling=reject_track_dangling)
    # Every reason this board is not eligible, in words; none means eligible. An error the
    # input board already reports is counted under `errors.pre_existing` and is no reason:
    # neither the placement nor the routing made it.
    errors = _error_summary(split)
    rejections = list(problems)
    if report_['unconnected_items']:
        rejections.append(f"{len(report_['unconnected_items'])} open connection(s)")
    for cause in ('placement_introduced', 'routing_introduced'):
        if split[cause]:
            rejections.append(f"{errors[cause]['count']} {cause.replace('_', '-')} DRC error(s): "
                              + ', '.join(f'{kind} {count}' for kind, count in sorted(errors[cause]['by_type'].items())))
    if reject_silk_graphics and silk_conflicts:
        rejections.append(f'{len(silk_conflicts)} silkscreen conflict(s) that moving a field cannot fix')
    if track_conflicts:
        rejections.append(f'{len(track_conflicts)} dangling track warning(s)')
    declared = [('edge intent', edge_intent['status'], ('pass',)),
                ('electrical locality', locality['status'], ('pass', 'info', 'not_declared')),
                ('block contract', block_result['status'], ('pass', 'info', 'not_declared')),
                ('mechanics', mechanics_result['status'], ('pass', 'not_declared'))]
    declared += [(name, result.get(key), passing) for name, result, key, passing in (
        ('decoupling loops', general_loops, 'outcome', ('pass', 'info')),
        ('pair impedance', impedance_result, 'status', ('PASS',)),
        ('length matching', length_result, 'status', ('PASS',)),
        ('AC matching', ac_result, 'status', ('PASS',))) if result is not None]
    rejections += [f'{name} check: {status}' for name, status, passing in declared if status not in passing]
    failed = bool(rejections)
    # Which rejections the placement made and which the routing made: another routing search of the
    # same placement can change only the second kind (second_trial_useful). `problems` are what the
    # router did to the placed board (a changed footprint, a via in a pad), so they are the routing's.
    made_by = {'placement': bool(split['placement_introduced'] or (reject_silk_graphics and silk_conflicts)
                                 or any(status not in passing for _, status, passing in declared[:4])),
               'routing': bool(problems or report_['unconnected_items'] or split['routing_introduced'] or track_conflicts
                               or any(status not in passing for _, status, passing in declared[4:]))}
    return {'status': 'checked', 'board': str(board), 'board_sha256': final_board_sha256,
            **({'local_stub_cleanup': local_stub_cleanup} if local_stub_cleanup is not None else {}),
            **({'local_decoupling': general_local_check} if general_local_check is not None else {}),
            **({'decoupling_loops': general_loops} if general_loops is not None else {}),
            **({'local_routing':local_check} if local_check is not None else {}),
            **({'planes': plane_result, 'plane_fill': plane_fill} if planes is not None else {}),
            **({'impedance': impedance_result} if impedance_result is not None else {}),
            **({'length_matching': length_result} if length_result is not None else {}),
            **({'ac_matching': ac_result} if ac_result is not None else {}),
            'integrity': 'violated' if problems else 'preserved',
            'eligibility': 'INELIGIBLE' if failed else 'eligible', 'rejections': rejections, 'made_by': made_by,
            'identity_problems': problems, 'rule_files': rule_files,
            'drc': exec_, 'opens': len(report_['unconnected_items']), 'silk_graphic_conflicts': silk_conflicts,
            **({'track_dangling_conflicts': track_conflicts} if reject_track_dangling else {}),
            'errors': errors, 'warnings': _warnings(report_),
            'physical_checks': ('FAILED' if failed else 'no native errors' if not split['pre_existing'] else
                                f"no introduced native errors ({len(split['pre_existing'])} already in the input)"),
            'edge_intent': edge_intent, 'locality': locality, 'block_contract': block_result, 'mechanics': mechanics_result, 'metrics': metrics,
            'group_views': group_views, 'evidence_complete': group_views['status'] == 'rendered'}


def preflight(config, executable=None, *, clock=None, cancel_dir=None, identities=None):
    """Runtime readiness before the budgeted work: imports, toolchain, engine and router."""
    from .placement_runtime import PlanningProcessError
    try:
        return _preflight(config, executable, clock=clock, cancel_dir=cancel_dir, identities=identities)
    except EnvironmentFailure as exc:
        if exc.stage == 'runtime-preflight':
            raise
        raise EnvironmentFailure('runtime-preflight', f'runtime preflight failed: {exc}') from exc
    except (ImportError, OSError, PlanningProcessError) as exc:
        raise EnvironmentFailure('runtime-preflight', f'runtime preflight failed: {exc}') from exc


def _preflight(config, executable=None, *, clock=None, cancel_dir=None, identities=None):
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
    identities['router'] = {k: router.get(k) for k in ('checkout', *ROUTER_IDENTITY[:-1])}
    problems = list(router['problems'])
    if cli.returncode != 0:
        problems.append('kicad-cli unavailable')
    if problems:
        raise EnvironmentFailure('runtime-preflight', 'runtime preflight failed: ' + '; '.join(problems))
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
            elif args.placement_command == '_check_mechanics':
                result = _evaluate_board_native(args.board, mechanics_binding=request['binding'])['mechanics']
                result.update(board_sha256=digest(args.board), binding_sha256=request['binding']['binding_sha256'])
            elif args.placement_command == '_check_blocks':
                from .placement_probe import extract
                from .placement_contract import grade
                saved = extract(args.board)
                result = grade(saved, request['binding'])
                if request.get('retain_capture') is True:
                    result['saved_capture'] = saved
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
