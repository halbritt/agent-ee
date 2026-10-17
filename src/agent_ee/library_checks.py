"""Coordinator adapter for captured library checks; offline helpers stay pure."""
from pathlib import Path

from .candidate import safe_relative
from .footprint_library import _members, _object, canonical, validate_declaration, load_document


def capability(params, files, inputs):
    _object(params, ('manifest', 'declaration', 'assembly_intent'))
    if inputs is None:
        raise ValueError('captured inputs required for library capability')
    manifest_path = str(safe_relative(params['manifest']))
    declaration_path = str(safe_relative(params['declaration']))
    if manifest_path not in files or declaration_path not in files:
        raise ValueError('library manifest and declaration must be captured')
    manifest = load_document(Path(inputs) / manifest_path)
    declaration = load_document(Path(inputs) / declaration_path)
    members = _members(manifest)
    validate_declaration(declaration)
    if any(c['assembly'] == 'mixed' for c in declaration['components']):
        raise ValueError('mixed assembly intent is not qualified')
    policy = params['assembly_intent']
    if not isinstance(policy, list) or not policy:
        raise ValueError('explicit immutable assembly intent required')
    refs = set()
    for item in policy:
        _object(item, ('reference', 'assembly', 'connector'))
        if not isinstance(item['reference'], str) or not item['reference'] or item['reference'] in refs:
            raise ValueError('unique assembly-intent references required')
        refs.add(item['reference'])
    actual = [dict(reference=c['reference'], assembly=c['assembly'], connector=c['connector'])
              for c in declaration['components']]
    if sorted(actual, key=canonical) != sorted(policy, key=canonical):
        raise ValueError('candidate assembly declarations differ from immutable intent')
    paths = [manifest_path] + [m['path'] for _, m in members]
    for lib in declaration['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        paths.append(str(safe_relative(lib['path'])))
    if any(p not in files for p in paths):
        raise ValueError('complete library closure must be captured')
    return dict(immutable_inputs=sorted(set(paths)), predicates=['footprint:assignment-parity/1'])


def check_footprint_assignment(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.manifest['files'], ctx.inputs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.footprint_library',
                 '/candidate/' + ctx.params['manifest'], '--captures', '/candidate',
                 '--assignments', '/candidate/' + ctx.params['declaration'],
                     '--out', '/work/assignments.json'], 60)
    finally:
        ctx.evidence('assignments.json')
    report = ctx.load_json('assignments.json', 'footprint assignment')
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='captured symbol-to-footprint assignment and numbered pad parity')
    ctx.result['details'].update(report=report)
    ctx.result['location'] = ', '.join(a['reference'] + ':' + a['footprint']
        for a in report['assignments'] if a['status'] == 'defect')
    ctx.result['limitations'].append('Declared component list only; no schematic binding, package selection, '
        'land-pattern geometry qualification, datasheet conformance or manufacturing approval.')
