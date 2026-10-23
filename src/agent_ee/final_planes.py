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



def binding_inputs(params):
    """Inputs that the existing baseline/capability machinery must keep immutable."""
    from .stage_receipts import _relative
    declarations = params.get('plane_obligations', [])
    if not isinstance(declarations, list):
        raise ValueError('plane_obligations must be a list')
    paths = set()
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {'plan', 'producer', 'composition'}:
            raise ValueError('plane obligation requires plan, producer and composition bindings')
        for link in declaration.values():
            if not isinstance(link, dict) or set(link) != {'path', 'sha256'}:
                raise ValueError('plane binding requires path and sha256')
            paths.add(_relative(link['path'], 'plane binding'))
            value = link['sha256']
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('plane binding requires a SHA-256 digest')
    return sorted(paths)


def bound_plans(inputs, files, params):
    """Resolve explicit scope bindings against captured producer and composition facts.

    The producer report is provenance, not a substitute for saved-board checks.
    Discovered plan/report schemas require a declaration even after a rename.
    """
    from .add_planes import SCHEMA, RESULT
    from .stage_receipts import _relative
    inputs = Path(inputs)
    binding_inputs(params)
    declarations = params.get('plane_obligations', [])
    plans, producers, compositions = set(), set(), set()
    composition_plans = {}

    def bound(link):
        if not isinstance(link, dict) or set(link) != {'path', 'sha256'}:
            raise ValueError('plane binding requires path and sha256')
        path = _relative(link['path'], 'plane binding')
        if path not in files or not (inputs/path).is_file():
            raise ValueError('required plane binding is absent: ' + path)
        if files[path] != link['sha256'] or digest(inputs/path) != link['sha256']:
            raise ValueError('stale plane binding: ' + path)
        return path, read_json(inputs/path)

    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {'plan', 'producer', 'composition'}:
            raise ValueError('plane obligation requires plan, producer and composition bindings')
        path, plan = bound(declaration['plan'])
        producer_path, producer = bound(declaration['producer'])
        composition_path, composition = bound(declaration['composition'])
        validate_plan(plan)
        if path in plans:
            raise ValueError('duplicate plane obligation: ' + path)
        if (not isinstance(producer, dict) or producer.get('schema') != RESULT or
                producer.get('execution') != 'ok' or
                producer.get('plan_sha256') != declaration['plan']['sha256'] or
                producer.get('planned_zone_ids') != zone_ids(plan)):
            raise ValueError('plane producer does not bind the declared plan: ' + producer_path)
        if (not isinstance(composition, dict) or
                not str(composition.get('schema', '')).startswith('pod-capture.scope-composition/') or
                declaration['plan'] not in composition.get('planes', [])):
            raise ValueError('composition does not bind the declared plane plan: ' + composition_path)
        plans.add(path)
        producers.add(producer_path)
        compositions.add(composition_path)
        composition_plans[composition_path] = composition['planes']

    declared_links = [d['plan'] for d in declarations]
    for path, links in composition_plans.items():
        if not isinstance(links, list) or not links or any(link not in declared_links for link in links):
            raise ValueError('composition has an undeclared plane obligation: ' + path)

    # Content, not basename, identifies otherwise undeclared plane obligations.
    # Only JSON objects are read; native KiCad files and binary evidence are skipped.
    for path in files:
        with (inputs/path).open('rb') as source:
            prefix = source.read(128).lstrip()
        if not prefix.startswith(b'{'):
            continue
        try:
            record = read_json(inputs/path)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        schema = record.get('schema')
        if ((schema == SCHEMA and path not in plans) or
                (schema == RESULT and path not in producers) or
                (str(schema).startswith('pod-capture.scope-composition/') and
                 record.get('planes') and path not in compositions)):
            raise ValueError('unbound captured plane obligation: ' + path)
    return sorted(plans)


def check_captured(ctx, drc):
    """Final DRC consumes every bound planned-plane obligation."""
    from .checks import CheckError
    try:
        paths = bound_plans(ctx.inputs, ctx.manifest['files'], ctx.params)
    except (ValueError, OSError, TypeError) as exc:
        raise CheckError('malformed', 'final planned-plane binding failed: ' + str(exc)) from exc
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
