"""Consume planned-plane obligations on the saved post-routing board."""
import argparse
import copy
import sys
import time
from pathlib import Path

from .add_planes import validate_plan, zone_ids, _planned_zone_errors
from .catalog import strict_json
from . import native_run
from .storage import digest, read_json, write_json


def validate(inputs, board, plan_path, work, *, drc=None, timeout=180):
    expires = time.monotonic() + timeout
    inputs, work = Path(inputs), Path(work)
    work.mkdir(parents=True, exist_ok=False)
    result = dict(schema='agent-ee.final-planes/1', status='FAIL', board_sha256=digest(inputs/board),
                  plan_sha256=digest(inputs/plan_path), errors=[])
    try:
        plan = validate_plan(strict_json((inputs/plan_path).read_bytes()))
        if any(z['island_removal'] != 'always' for z in plan['zones']):
            raise ValueError('final planned planes require island_removal=always')
        record = dict(native_run.run(native_run.Intent('pcbnew_script', ['/usr/bin/python3', '-B', '-m',
            'agent_ee.final_planes', '/candidate/'+board, '/candidate/'+plan_path,
            '/work/facts.json'], timeout=timeout), inputs=inputs, work=work/'native'))
        if record['execution'] != 'ok' or record['exit_code'] != 0:
            raise ValueError('native final plane inspection failed: ' +
                             (work/'native/console.log').read_text(errors='replace')[-2000:])
        facts = read_json(work/'native/facts.json')
        result['facts'] = facts
        if drc is None:
            remaining = expires - time.monotonic()
            if remaining <= 1:
                raise ValueError('deadline exhausted before final plane DRC')
            record = dict(native_run.run(native_run.drc('/candidate/'+board, '/work/report.json', timeout=remaining,
                                                        executable='/usr/bin/kicad-cli'), inputs=inputs, work=work/'drc'))
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
    direct = params.get('plane_plans', [])
    if not isinstance(direct, list):
        raise ValueError('plane_plans must be a list of captured plan bindings')
    for link in direct:
        if not isinstance(link, dict) or set(link) != {'path', 'sha256'}:
            raise ValueError('plane plan binding requires path and sha256')
        path = _relative(link['path'], 'plane plan binding')
        value = link['sha256']
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('plane plan binding requires a SHA-256 digest')
        paths.add(path)
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {'plan', 'producer', 'composition'}:
            raise ValueError('plane obligation requires plan, producer and composition bindings')
        for kind, link in declaration.items():
            late = kind == 'producer' and isinstance(link, dict) and set(link) == {'path', 'stage'}
            if late and link['stage'] != 'add-planes':
                raise ValueError('late plane producer must name add-planes')
            if not isinstance(link, dict) or (not late and set(link) != {'path', 'sha256'}):
                raise ValueError('plane binding requires path and sha256, or an add-planes producer stage')
            path = _relative(link['path'], 'plane binding')
            if late:
                continue
            paths.add(path)
            value = link['sha256']
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('plane binding requires a SHA-256 digest')
    return sorted(paths)


def complete_bindings(inputs, files, params, producer_receipts=None, *, board=None):
    """Resolve late report hashes only from validated producing-stage outputs."""
    from .stage_receipts import ADOPTABLE, validate_receipt
    binding_inputs(params)
    completed, evidence = copy.deepcopy(params), []
    for declaration in completed.get('plane_obligations', []):
        producer = declaration['producer']
        if 'stage' not in producer:
            continue
        path = producer['path']
        anchor = (producer_receipts or {}).get(path)
        if not isinstance(anchor, dict) or set(anchor) != {'path', 'sha256'}:
            raise ValueError('late plane producer has no authenticated stage receipt: ' + path)
        receipt_path = Path(anchor['path'])
        if receipt_path.is_symlink() or not receipt_path.is_file() or digest(receipt_path) != anchor['sha256']:
            raise ValueError('stale plane producer stage receipt: ' + path)
        receipt = validate_receipt(receipt_path.parent)
        if receipt['status'] not in ADOPTABLE or receipt['spec']['step'] != 'add-planes':
            raise ValueError('plane producer stage was not adopted successfully: ' + path)
        expected = receipt['outputs'].get(path)
        if (not expected or files.get(path) != expected or not (Path(inputs)/path).is_file()
                or digest(Path(inputs)/path) != expected):
            raise ValueError('missing or stale adopted plane producer report: ' + path)
        report = read_json(Path(inputs)/path)
        if not isinstance(report, dict):
            raise ValueError('adopted plane report is not an object: ' + path)
        native = board if board is not None else report.get('board_path')
        plan = declaration['plan']
        if (receipt['spec']['writes'].get(path) != 'out/planes.json' or
                receipt['spec']['writes'].get(native) != 'out/board.kicad_pcb' or
                report.get('schema') != 'agent-ee.add-planes/1' or report.get('execution') != 'ok' or
                report.get('board_path') != native or report.get('plan_path') != plan['path'] or
                report.get('board_before') != receipt['inputs'].get(native) or
                report.get('board_after') != receipt['outputs'].get(native) or
                report.get('plan_sha256') != plan['sha256'] or
                receipt['inputs'].get(plan['path']) != plan['sha256']):
            raise ValueError('adopted plane report differs from producing plan or board: ' + path)
        declaration['producer'] = {'path': path, 'sha256': expected}
        evidence.append({'plan': dict(plan), 'composition': dict(declaration['composition']),
                         'producer': dict(declaration['producer']), 'receipt': dict(anchor),
                         'board': native, 'board_after': report['board_after']})
    return completed, evidence


def producing_receipts(ctx):
    """Authenticate the add-planes predecessor through the anchored owned-router input."""
    from .rf_review import retained_routing
    from .stage_receipts import validate_receipt, after_tree
    routing = retained_routing(ctx.run_dir)
    board = ctx.scope['board']
    if routing.get('status') != 'verified' or routing.get('board', {}).get('sha256') != ctx.manifest['files'].get(board):
        raise ValueError('late plane binding requires verified routing evidence for this board')
    link = routing.get('receipt') or {}
    path = Path(link.get('path', ''))
    if path.is_symlink() or not path.is_file() or digest(path) != link.get('sha256'):
        raise ValueError('anchored routing receipt is missing or stale')
    routed = validate_receipt(path.parent)
    index = routed['binding'].get('receipt_index')
    if type(index) is not int or index <= 1 or routed['outputs'].get(board) != ctx.manifest['files'][board]:
        raise ValueError('routing receipt does not bind this saved board or a plane predecessor')
    prior = path.parent.parent / f'{index - 1}-add-planes'
    receipt = validate_receipt(prior, binding={**routed['binding'], 'receipt_index': index - 1})
    if receipt['spec']['step'] != 'add-planes' or after_tree(receipt) != routed['inputs']:
        raise ValueError('plane producer does not precede the routed input')
    return {name: {'path': str(prior/'receipt.json'), 'sha256': digest(prior/'receipt.json')}
            for name, output in receipt['spec']['writes'].items() if output == 'out/planes.json'}


def _reference_source_files(inputs, files, board, plans):
    """Identify declared source files of blocks separate from an explicitly planned board.

    Validation establishes payload custody, not source qualification or adoption.
    A block containing the current board or a selected plan remains an active subject.
    """
    if board is None or not plans:
        return set()
    from .reference_blocks import validate_block
    from .stage_receipts import _relative
    current = Path(_relative(board, 'current plane board'))
    if board not in files or digest(inputs/board) != files[board]:
        raise ValueError('current plane board is absent or stale')
    sources = set()
    for path in files:
        manifest = Path(path)
        if manifest.name != 'block.json':
            continue
        root = manifest.parent
        if current.is_relative_to(root) or any(Path(p).is_relative_to(root) for p in plans):
            continue
        record = read_json(inputs/path)
        if not isinstance(record, dict) or record.get('schema') != 'agent-ee.block/1':
            continue
        if digest(inputs/path) != files[path]:
            raise ValueError('stale captured reference block: ' + path)
        validate_block(inputs/root)
        payload = {(root/name).as_posix(): pin for name, pin in record['files'].items()}
        if any(files.get(name) != pin for name, pin in payload.items()):
            raise ValueError('reference block payload differs from captured inputs: ' + path)
        sources.update((root/name).as_posix() for name in record['source']['paths'])
    return sources


def bound_plans(inputs, files, params, *, producer_receipts=None, board=None):
    """Resolve explicit scope bindings against captured producer and composition facts.

    The producer report is provenance, not a substitute for saved-board checks.
    Discovered plan/report schemas require a declaration even after a rename.
    """
    from .add_planes import SCHEMA, RESULT
    from .stage_receipts import _relative
    inputs = Path(inputs)
    binding_inputs(params)
    params, _ = complete_bindings(inputs, files, params, producer_receipts, board=board)
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
        return path, strict_json((inputs/path).read_bytes())

    # A native check may bind a plan directly. Producer/composition custody is
    # still mandatory for any such reports actually captured alongside it.
    for link in params.get('plane_plans', []):
        path, plan = bound(link)
        validate_plan(plan)
        if path in plans:
            raise ValueError('duplicate plane obligation: ' + path)
        plans.add(path)

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

    reference_sources = _reference_source_files(inputs, files, board, plans)
    # Content, not basename, identifies otherwise undeclared plane obligations.
    # Only JSON objects are read; native KiCad files and binary evidence are skipped.
    for path in files:
        with (inputs/path).open('rb') as source:
            prefix = source.read(128).lstrip()
            while not prefix and source.tell() < (inputs/path).stat().st_size:
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
        if ((schema == SCHEMA and path not in plans and path not in reference_sources) or
                (schema == RESULT and path not in producers) or
                (str(schema).startswith('pod-capture.scope-composition/') and
                 record.get('planes') and path not in compositions)):
            raise ValueError('unbound captured plane obligation: ' + path)
    return sorted(plans)


def check_captured(ctx, drc):
    """Final DRC consumes every bound planned-plane obligation."""
    from .checks import CheckError
    try:
        binding_inputs(ctx.params)
        anchors = (producing_receipts(ctx) if any('stage' in d['producer']
                   for d in ctx.params.get('plane_obligations', [])) else None)
        params, bindings = complete_bindings(ctx.inputs, ctx.manifest['files'], ctx.params,
                                            anchors, board=ctx.scope['board'])
        paths = bound_plans(ctx.inputs, ctx.manifest['files'], params, board=ctx.scope['board'])
        if bindings:
            ctx.result['details']['plane_bindings'] = bindings
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'validate':
        parser = argparse.ArgumentParser(description='Verify saved planned planes and native connectivity')
        parser.add_argument('--inputs', required=True)
        parser.add_argument('--board', required=True)
        parser.add_argument('--plan', required=True)
        parser.add_argument('--out', required=True)
        parser.add_argument('--drc', type=Path)
        parser.add_argument('--timeout', type=float, default=180)
        args = parser.parse_args(argv[1:])
        result = validate(args.inputs, args.board, args.plan, args.out,
                          drc=read_json(args.drc) if args.drc else None, timeout=args.timeout)
        return 0 if result['status'] == 'PASS' else 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    import pcbnew as k
    from .add_planes_probe import inspect
    from .plane_reference import check_reference
    board = k.LoadBoard(str(args.board))
    facts = inspect(board, validate_plan(strict_json(Path(args.plan).read_bytes())))
    facts['gnss_reference'] = check_reference(board, Path('/candidate'), args.board)
    write_json(args.output, facts)


if __name__ == '__main__':
    raise SystemExit(main())
