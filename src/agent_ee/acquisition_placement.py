"""Bounded acquisition placement proposals, independently graded by the existing engine.

This model is deliberately conservative. Failure to find an incumbent is only
search failure, never a physical infeasibility proof. Native geometry, source
mechanics, locality and DRC remain the acceptance authorities.
"""
import hashlib
import math
from pathlib import Path

from .electrical_locality import resolve, roles
from .storage import canonical

MM = 1_000_000
SCHEMA = 'agent-ee.acquisition-placement-proposal/1'
# The acquisition-mechanics/1 reader admits exactly this captured body gap.
BODY_GAP_NM = 200_000
ROUNDING_MARGIN_NM = 2_000  # Proposal allowance; does not change a design rule.


def basis(diagnostic):
    return hashlib.sha256(canonical(diagnostic)).hexdigest()


def pose_map(diagnostic):
    rows = diagnostic['poses']
    result = {r['uuid']: [r['x_nm'], r['y_nm'], r['quarter_turns']] for r in rows}
    if len(result) != len(rows):
        raise ValueError('repair basis repeats a footprint UUID')
    return result


def active_component(problem, diagnostic):
    """Movable locality-connected participants in the original exact violations."""
    footprints = problem['footprints']
    by_ref = {f['native_ref']: f for f in footprints}
    if len(by_ref) != len(footprints):
        raise ValueError('acquisition repair requires unique physical references')
    adjacency = {ref: set() for ref in by_ref}
    for obligation in problem['locality']['document']['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            a, b = (pair[key]['reference'] for key in ('from', 'to'))
            adjacency[a].add(b)
            adjacency[b].add(a)
    participants = set()

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('ref', 'with') and isinstance(child, str):
                    if child not in by_ref:
                        raise ValueError('repair violation names an unknown reference')
                    participants.add(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(diagnostic['violations'])
    queue = sorted(participants)
    for ref in queue:
        for peer in sorted(adjacency[ref]):
            if peer not in participants:
                participants.add(peer)
                queue.append(peer)
    return sorted(by_ref[ref]['uuid'] for ref in participants if by_ref[ref]['role'] == 'movable')


def validate_poses(problem, diagnostic, poses):
    """Check custody before calling an evaluator that permits partial pose maps."""
    original = pose_map(diagnostic)
    known = {f['uuid']: f for f in problem['footprints']}
    active = set(active_component(problem, diagnostic))
    if set(original) != set(known) or not isinstance(poses, dict) or set(poses) != set(known):
        raise ValueError('repair must cover the exact complete footprint UUID set')
    for uuid, pose in poses.items():
        if (not isinstance(pose, list) or len(pose) != 3
                or any(type(v) is not int for v in pose)
                or pose[2] not in known[uuid]['rotations']):
            raise ValueError('repair returned an invalid footprint pose')
        if uuid not in active and pose != original[uuid]:
            raise ValueError('repair changed an inactive or fixed footprint')
    return sorted(active)


def validate_proposal(problem, diagnostic, proposal):
    if (proposal.get('schema') != SCHEMA or proposal.get('status') != 'proposed'
            or proposal.get('problem_sha256') != problem['problem_sha256']
            or proposal.get('basis_sha256') != basis(diagnostic)):
        raise ValueError('repair proposal identity differs from its original problem/basis')
    validate_poses(problem, diagnostic, proposal['poses'])


def candidate(problem, diagnostic, proposal, evaluation):
    """An exact-feasible proposal may enter the unchanged native shortlist gates."""
    validate_proposal(problem, diagnostic, proposal)
    if pose_map(evaluation) != proposal['poses']:
        raise ValueError('exact evaluation differs from the complete proposed poses')
    cost = evaluation.get('cost')
    if not isinstance(cost, str) or not cost.isdecimal():
        raise ValueError('exact evaluation omitted its native cost')
    if (evaluation.get('feasible') is not True or evaluation.get('violations') != []
            or evaluation['terms']['hard_count'] != 0 or int(evaluation['terms']['hard']) != 0):
        return None
    return dict(evaluation, seed=diagnostic['seed'], strategy=SCHEMA)


def runtime_identity():
    """Observe the existing owned KRT wheels; never select or install a fallback."""
    import importlib.metadata
    import sys
    prefix = Path(sys.prefix).resolve()
    versions, files = {}, {}
    for name, version in [('numpy', '2.5.4'), ('scipy', '1.18.1')]:
        dist = importlib.metadata.distribution(name)
        if dist.version != version or not dist.files:
            raise ValueError('acquisition placement requires the pinned KRT wheel: ' + name)
        versions[name] = dist.version
        for entry in dist.files:
            if entry.suffix == '.pyc':
                continue
            path = Path(dist.locate_file(entry)).resolve(strict=True)
            if not path.is_relative_to(prefix):
                raise ValueError('acquisition solver dependency escaped its explicit runtime')
            files[path.relative_to(prefix).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(versions=versions, files_sha256=hashlib.sha256(canonical(files)).hexdigest(),
                python_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                python_version=sys.version, helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def child_argv(checkout):
    """Use only the already qualified checkout's numerical interpreter."""
    interpreter = Path(checkout) / '.venv-krt/bin/python'
    return [str(interpreter), '-I', '-B', '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); '
            'from agent_ee.acquisition_placement import main; main()',
            str(Path(__file__).resolve().parent.parent)]


def main():
    import json
    import sys
    request = json.load(sys.stdin)
    runtime = runtime_identity()
    if request['operation'] == 'identity':
        result = runtime
    elif request['operation'] == 'propose':
        if runtime != request['runtime']:
            raise ValueError('acquisition solver runtime changed after job identity capture')
        result = propose(request['problem'], request['diagnostic'], max_seconds=request['max_seconds'])
        if runtime_identity() != runtime:
            raise ValueError('acquisition solver runtime changed during the proposal')
        result['runtime'] = runtime
    else:
        raise ValueError('unsupported acquisition solver operation')
    print(json.dumps(result, allow_nan=False))


def _rotate(point, q):
    x, y = point
    return ((x, y), (y, -x), (-x, -y), (-y, x))[q % 4]


def _rect(rectangle, q):
    corners = [_rotate((x, y), q) for x in (rectangle[0], rectangle[2])
               for y in (rectangle[1], rectangle[3])]
    return [min(x for x, _ in corners)/MM, min(y for _, y in corners)/MM,
            max(x for x, _ in corners)/MM, max(y for _, y in corners)/MM]


def _sum(*terms):
    result = {}
    for scale, expression in terms:
        for key, value in expression.items():
            result[key] = result.get(key, 0.0) + scale*value
    return result


class _Model:
    """Sparse affine constraints; key -1 is a constant, other keys are variables."""
    def __init__(self):
        self.low, self.high, self.integer = [], [], []
        self.rows, self.lower, self.upper = [], [], []

    def variable(self, low, high, *, binary=False):
        index = len(self.low)
        self.low.append(low)
        self.high.append(high)
        self.integer.append(int(binary))
        return {index: 1.0}

    def constrain(self, expression, low=-math.inf, high=math.inf):
        constant = expression.get(-1, 0.0)
        self.rows.append({k: v for k, v in expression.items() if k != -1 and v})
        self.lower.append(low-constant)
        self.upper.append(high-constant)

    def solve(self, seconds):
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_matrix
        indices = [(r, c, v) for r, row in enumerate(self.rows) for c, v in row.items()]
        rows, columns, values = zip(*indices)
        matrix = coo_matrix((values, (rows, columns)), shape=(len(self.rows), len(self.low))).tocsc()
        return milp(np.zeros(len(self.low)), integrality=np.array(self.integer),
                    bounds=Bounds(self.low, self.high),
                    constraints=LinearConstraint(matrix, self.lower, self.upper),
                    options={'time_limit': seconds, 'mip_rel_gap': 0})


def propose(problem, diagnostic, *, max_seconds):
    """One MILP proposal, preserving inactive poses; caller owns the process deadline."""
    if (type(max_seconds) not in (int, float) or not math.isfinite(max_seconds)
            or not 0 < max_seconds <= 120):
        raise ValueError('repair needs a finite positive budget at most 120 seconds')
    footprints = problem['footprints']
    if not 1 <= len(footprints) <= 32:
        raise ValueError('acquisition repair exceeds its footprint work bound')
    original = pose_map(diagnostic)
    active = set(validate_poses(problem, diagnostic, original))
    result = dict(schema=SCHEMA, problem_sha256=problem['problem_sha256'],
                  basis_sha256=basis(diagnostic), active_uuids=sorted(active),
                  status='no_incumbent', authority='unvalidated_proposal')
    if not active:
        return {**result, 'reason': 'no movable violation component'}
    by_ref = {f['native_ref']: f for f in footprints}
    outline = problem['outline']
    if outline['kind'] != 'rectangle':
        raise ValueError('acquisition repair requires the captured rectangular outline')
    x0, y0, x1, y1 = [v/MM for v in outline['rect_nm']]
    origin, size = (x0, y0), (x1-x0, y1-y0)
    clearance = max([problem['rules']['copper_clearance_nm'], *[
        item.get('clearance_nm', 0) for f in footprints for item in f['copper_local_nm']]])
    margin = ROUNDING_MARGIN_NM/MM
    copper_growth, body_growth = (clearance/2+ROUNDING_MARGIN_NM/2)/MM, (BODY_GAP_NM/2+ROUNDING_MARGIN_NM/2)/MM
    edge = max(0, problem['rules']['copper_edge_clearance_nm']/MM-copper_growth)
    model = _Model()
    positions, turns, local_boxes = {}, {}, {}
    for f in footprints:
        uuid = f['uuid']
        if uuid in active:
            positions[uuid] = [model.variable(0, extent) for extent in size]
            turns[uuid] = {q: model.variable(0, 1, binary=True) for q in f['rotations']}
            model.constrain(_sum(*[(1, v) for v in turns[uuid].values()]), 1, 1)
        else:
            positions[uuid] = [{-1: original[uuid][axis]/MM-origin[axis]} for axis in range(2)]
            turns[uuid] = {original[uuid][2]: {-1: 1.0}}
        for q in turns[uuid]:
            shapes = [_rect(f['occupancy']['local_rect_nm'], q)]
            for rectangle, grow in [(f['source_body_local_nm'], body_growth), *[
                    (item['rect_nm'], copper_growth) for item in f['copper_local_nm']]]:
                box = _rect(rectangle, q)
                shapes.append([v+(-grow if axis < 2 else grow) for axis, v in enumerate(box)])
            local_boxes[uuid, q] = [min(s[0] for s in shapes), min(s[1] for s in shapes),
                                    max(s[2] for s in shapes), max(s[3] for s in shapes)]

    def offset(uuid, values):
        return _sum(*[(values[q], variable) for q, variable in turns[uuid].items()])

    def box(f, kind='packing'):
        uuid = f['uuid']
        rectangles = {q: local_boxes[uuid, q] if kind == 'packing' else _rect(
            f['source_body_local_nm'] if kind == 'body' else f['access_local_nm'], q) for q in turns[uuid]}
        return [_sum((1, positions[uuid][axis % 2]),
                     (1, offset(uuid, {q: r[axis] for q, r in rectangles.items()}))) for axis in range(4)]

    boxes = {f['uuid']: box(f) for f in footprints}
    # Bound every possible coordinate difference using the board, fixed positions
    # and captured local extents. An inactive inequality must impose no restriction.
    local_extent = max(abs(v) for rectangle in local_boxes.values() for v in rectangle)
    local_extent = max(local_extent, max((abs(v)/MM for f in footprints
        for v in (f['access_local_nm'] or [])), default=0))
    fixed_extent = max(abs(original[u][axis]/MM-origin[axis]) for u in original for axis in range(2))
    big_m = 2*(max(*size, fixed_extent)+2*local_extent+margin)

    def separate(a, b):
        choices = (_sum((1, a[0]), (-1, b[2])), _sum((1, b[0]), (-1, a[2])),
                   _sum((1, a[1]), (-1, b[3])), _sum((1, b[1]), (-1, a[3])))
        flags = []
        for gap in choices:
            flag = model.variable(0, 1, binary=True)
            flags.append(flag)
            model.constrain(_sum((1, gap), (-big_m, flag)), margin-big_m)
        model.constrain(_sum(*[(1, flag) for flag in flags]), 1)

    for i, f in enumerate(footprints):
        uuid = f['uuid']
        if uuid in active:
            for axis in range(2):
                model.constrain(boxes[uuid][axis], edge)
                model.constrain(boxes[uuid][axis+2], high=size[axis]-edge)
        for peer in footprints[i+1:]:
            if uuid in active or peer['uuid'] in active:
                separate(boxes[uuid], boxes[peer['uuid']])
        if f['access_local_nm'] is not None:
            for peer in footprints:
                if peer['uuid'] != uuid and (uuid in active or peer['uuid'] in active):
                    separate(box(f, 'access'), box(peer, 'body'))

    def pad(endpoint):
        f = by_ref[endpoint['reference']]
        uuid, initial = f['uuid'], f['pose']
        centre = resolve(problem['locality']['footprints'], endpoint)
        local = _rotate((float(centre[0])-initial['x_nm'], float(centre[1])-initial['y_nm']),
                        -initial['quarter_turns'])
        offsets = {q: _rotate(local, q) for q in turns[uuid]}
        return uuid, [_sum((1, positions[uuid][axis]),
                          (1, offset(uuid, {q: v[axis]/MM for q, v in offsets.items()}))) for axis in range(2)]

    for obligation in problem['locality']['document']['obligations']:
        for role in roles(obligation):
            pair = obligation[role]
            a, pa = pad(pair['from'])
            b, pb = pad(pair['to'])
            if a not in active and b not in active:
                continue
            delta = [_sum((1, pa[axis]), (-1, pb[axis])) for axis in range(2)]
            limit = (pair['max_distance_nm']/MM-margin)*math.cos(math.pi/32)
            for k in range(32):
                angle = 2*math.pi*k/32
                model.constrain(_sum((math.cos(angle), delta[0]), (math.sin(angle), delta[1])), high=limit)
    solved = model.solve(max_seconds)
    result.update(solver_status=int(solved.status), solver_message=str(solved.message),
                  variables=len(model.low), constraints=len(model.rows))
    if solved.status not in (0, 1) or solved.x is None:
        return result

    def value(expression):
        return sum(v*(1 if key == -1 else solved.x[key]) for key, v in expression.items())

    poses = {uuid: list(pose) for uuid, pose in original.items()}
    for uuid in active:
        xy = [int(round((value(positions[uuid][axis])+origin[axis])*MM)) for axis in range(2)]
        q = max(turns[uuid], key=lambda q: value(turns[uuid][q]))
        poses[uuid] = [*xy, q]
    validate_poses(problem, diagnostic, poses)
    return {**result, 'status': 'proposed', 'poses': poses}
