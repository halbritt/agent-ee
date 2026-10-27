"""Compare saved routed copper primitives, independently of serialization order."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .routing_rules import captured_file
from .storage import canonical, write_json
from .symbol_library import parse_sexpr


def capability(params, scope, files, inputs, cid=None):
    if not isinstance(params, dict) or set(params) != {'reference_board'}:
        raise ValueError('routing_equivalence requires only reference_board')
    board = captured_file(inputs, files, scope['board'])
    reference = captured_file(inputs, files, params['reference_board'])
    if board.resolve() == reference.resolve():
        raise ValueError('reference_board must differ from the candidate board')
    return {'immutable_inputs': [params['reference_board']],
            'predicates': ['routing:copper-geometry-equal']}


def canonical_ring(points):
    """Ignore the arbitrary starting vertex and winding of a closed contour."""
    points = tuple(tuple(p) for p in points)
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise ValueError('zone contour needs at least three points')
    # A polygon normally has one lexicographically least vertex. Keep ties so
    # repeated vertices cannot make the result depend on traversal direction.
    minimum = min(points)
    return min(ring[i:] + ring[:i] for ring in (points, points[::-1])
               for i, point in enumerate(ring) if point == minimum)


def canonical_set(elements):
    """Return a sorted canonical multiset, retaining exact duplicate primitives."""
    return sorted(canonical(e) for e in elements)


def compare_boards(board, reference):
    from .geometry_probe import routing_facts
    blobs = [Path(p).read_bytes() for p in (board, reference)]
    for blob in blobs:
        if parse_sexpr(blob)[0] != 'kicad_pcb':
            raise ValueError('expected a complete kicad_pcb')
    copper = [canonical_set(routing_facts(str(p))) for p in (board, reference)]
    if not copper[0] or not copper[1]:
        raise ValueError('empty routed copper inventory cannot establish determinism')
    left, right = map(Counter, copper)
    return {'schema': 'agent-ee.routing-equivalence/1', 'units': 'nm',
            'geometry_equal': left == right, 'byte_equal': blobs[0] == blobs[1],
            'board_sha256': hashlib.sha256(blobs[0]).hexdigest(),
            'reference_sha256': hashlib.sha256(blobs[1]).hexdigest(),
            'board_geometry': [json.loads(e) for e in copper[0]],
            'reference_geometry': [json.loads(e) for e in copper[1]],
            'only_board': [json.loads(e) for e in sorted((left - right).elements())],
            'only_reference': [json.loads(e) for e in sorted((right - left).elements())],
            'board_duplicates': [{'element': json.loads(e), 'count': left[e]}
                                 for e in sorted(left) if left[e] > 1],
            'reference_duplicates': [{'element': json.loads(e), 'count': right[e]}
                                     for e in sorted(right) if right[e] > 1],
            'count_differences': [{'element': json.loads(e), 'board_count': left[e],
                                   'reference_count': right[e]}
                                  for e in sorted(left.keys() | right.keys()) if left[e] != right[e]]}


def check_routing_equivalence(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.routing_equivalence',
                 '/candidate/' + ctx.scope['board'], '/candidate/' + ctx.params['reference_board'],
                 '--out', '/work/comparison.json'], timeout=300)
    report = ctx.load_json('comparison.json', 'routed copper comparison')
    ctx.evidence('comparison.json')
    if (not isinstance(report, dict) or report.get('schema') != 'agent-ee.routing-equivalence/1' or
            type(report.get('geometry_equal')) is not bool or type(report.get('byte_equal')) is not bool):
        raise CheckError('malformed', 'invalid routing equivalence report')
    ctx.result['details'].update(report)
    ctx.result.update(execution='ok', outcome='pass' if report['geometry_equal'] else 'defect',
                      summary=f"Copper geometry equal={report['geometry_equal']}; "
                              f"board bytes equal={report['byte_equal']} (informational)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('reference')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    # Exceptions intentionally fail the native call; no partial inventory is a pass.
    report = compare_boards(args.board, args.reference)
    import pcbnew
    report['pcbnew_version'] = pcbnew.Version()
    write_json(args.out, report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def describe_routing_equivalence(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.captured(ctx.scope.get('board'))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('routing_equivalence', describe_routing_equivalence, check_routing_equivalence,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
