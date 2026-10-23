"""Consume planned-plane obligations on the saved post-routing board."""
import argparse
import time
from pathlib import Path

from .add_planes import validate_plan, zone_ids, _planned_zone_errors
from .executor import execute
from .storage import digest, read_json, write_json


def validate(inputs, board, plan_path, work, *, drc=None, timeout=180):
    expires = time.monotonic() + timeout
    inputs, work = Path(inputs), Path(work)
    work.mkdir(parents=True, exist_ok=False)
    result = dict(schema='agent-ee.final-planes/1', status='FAIL', board_sha256=digest(inputs/board),
                  plan_sha256=digest(inputs/plan_path), errors=[])
    try:
        plan = validate_plan(read_json(inputs/plan_path))
        if any(z['island_removal'] != 'always' for z in plan['zones']):
            raise ValueError('final planned planes require island_removal=always')
        record = execute(inputs, work/'native', ['/usr/bin/python3', '-B', '-m',
            'agent_ee.final_planes', '/candidate/'+board, '/candidate/'+plan_path,
            '/work/facts.json'], timeout=timeout)
        if record['execution'] != 'ok' or record['exit_code'] != 0:
            raise ValueError('native final plane inspection failed: ' +
                             (work/'native/console.log').read_text(errors='replace')[-2000:])
        facts = read_json(work/'native/facts.json')
        result['facts'] = facts
        if drc is None:
            remaining = expires - time.monotonic()
            if remaining <= 1:
                raise ValueError('deadline exhausted before final plane DRC')
            record = execute(inputs, work/'drc', ['/usr/bin/kicad-cli', 'pcb', 'drc', '--format',
                'json', '--severity-all', '-o', '/work/report.json', '/candidate/'+board], timeout=remaining)
            if record['execution'] != 'ok' or record['exit_code'] != 0:
                raise ValueError('native final plane DRC failed')
            drc = read_json(work/'drc/report.json')
        write_json(work/'drc.json', drc)
        if any(c.get('key') == 'isolated_copper' for c in drc.get('ignored_checks', [])):
            raise ValueError('native isolated_copper check is disabled')
        ids = zone_ids(plan)
        errors = _planned_zone_errors(drc, ids, set(ids))
        if errors:
            result['findings'] = errors
            raise ValueError('planned zone has native DRC errors or isolated copper')
        if digest(inputs/board) != result['board_sha256'] or digest(inputs/plan_path) != result['plan_sha256']:
            raise ValueError('final plane inputs changed during validation')
        result['status'] = 'PASS'
    except (ValueError, OSError) as exc:
        result['errors'].append(str(exc))
    write_json(work/'result.json', result)
    return result



def check_captured(ctx, drc):
    """Final DRC re-evaluates captured planes.json, never trusts a pre-route receipt."""
    from .checks import CheckError
    paths = sorted(p for p in ctx.manifest['files'] if Path(p).name == 'planes.json')
    for index, path in enumerate(paths):
        name = f'final-planes-{index}'
        remaining = ctx.remaining()
        if remaining <= 1:
            raise CheckError('interrupted', 'deadline exhausted before final plane validation')
        result = validate(ctx.inputs, ctx.scope['board'], path, ctx.work/name,
                          drc=drc, timeout=min(180, remaining))
        ctx.evidence(name+'/result.json', name+'/native/facts.json', name+'/native/execution.json',
                     name+'/native/console.log', name+'/drc.json')
        ctx.result['details'].setdefault('final_planes', []).append(result)
        if result['status'] != 'PASS':
            raise CheckError('malformed', 'final planned-plane obligation failed: ' + '; '.join(result['errors']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    import pcbnew as k
    from .add_planes_probe import inspect
    from .plane_reference import check_reference
    board = k.LoadBoard(str(args.board))
    facts = inspect(board, validate_plan(read_json(args.plan)))
    facts['gnss_reference'] = check_reference(board, Path('/candidate'), args.board)
    write_json(args.output, facts)


if __name__ == '__main__':
    main()
