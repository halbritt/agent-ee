"""Captured catalog adapters; preparation never authenticates upstream claims."""
import argparse
import re
from pathlib import Path

from .candidate import safe_relative
from .storage import write_json


LIMIT = ('Snapshot claims only: no authenticated upstream data, live stock, datasheet, '
         'pin/footprint suitability, procurement or circuit acceptance.')


def capability(kind, params, scope, files, inputs, cid):
    expected = {'snapshot', 'manifest_sha256'}
    if kind == 'part_selection':
        expected |= {'requirements', 'requirement_id'}
    if not isinstance(params, dict) or set(params) != expected:
        raise ValueError('catalog check requires exact snapshot/pin and selection mapping fields')
    if not isinstance(params['manifest_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', params['manifest_sha256']):
        raise ValueError('manifest_sha256 requires SHA-256')
    snapshot = str(safe_relative(params['snapshot']))
    paths = [snapshot + '/manifest.json', snapshot + '/catalog.sqlite3']
    if kind == 'part_selection':
        requirement_path = str(safe_relative(params['requirements']))
        paths.append(requirement_path)
        if inputs is None:
            raise ValueError('captured selection requirements required')
        from .catalog import strict_json
        from .part_selection import validate_requirements
        validate_requirements(strict_json((Path(inputs) / requirement_path).read_text(encoding='utf-8')))
        requirements = scope.get('baseline', {}).get('requirements', [])
        mapped = [r for r in requirements if cid in r['checks']]
        if (len(mapped) != 1 or mapped[0]['id'] != params['requirement_id'] or
                mapped[0]['source']['path'] != requirement_path):
            raise ValueError('selection must map exactly one baseline requirement sourced from its requirements file')
    if any(p not in files for p in paths):
        raise ValueError('catalog snapshot and selection requirements must be captured')
    return dict(immutable_inputs=sorted(paths),
                predicates=['catalog:declared-part-constraints/1'] if kind == 'part_selection' else [])


def check_catalog(ctx):
    from .checks import CheckError
    try:
        capability(ctx.spec['kind'], ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('failed', str(exc)) from exc
    argv = ['/usr/bin/python3', '-m', 'agent_ee.catalog_checks', ctx.spec['kind'],
            '/candidate/' + ctx.params['snapshot'], ctx.params['manifest_sha256'],
            '--out', '/work/catalog.json']
    if ctx.spec['kind'] == 'part_selection':
        argv += ['--requirements', '/candidate/' + ctx.params['requirements']]
    try:
        ctx.execute(argv, 120)
    finally:
        ctx.evidence('catalog.json')
    report = ctx.load_json('catalog.json', 'catalog')
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='Offline captured catalog consistency and declared selection constraints')
    ctx.result['details']['report'] = report
    ctx.result['limitations'].append(LIMIT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['catalog', 'part_selection'])
    parser.add_argument('snapshot')
    parser.add_argument('manifest_sha256')
    parser.add_argument('--requirements')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.kind == 'catalog':
        from .catalog import catalog_check
        report = catalog_check(args.snapshot, args.manifest_sha256)
    else:
        from .part_selection import selection_check
        requirements = Path(args.requirements) if args.requirements is not None else None
        report = selection_check(args.snapshot, args.manifest_sha256, requirements)
    write_json(Path(args.out), report)


if __name__ == '__main__':
    main()
