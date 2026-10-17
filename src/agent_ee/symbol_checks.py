"""Coordinator pin-reference check over explicit captured selected-symbol queries."""
from pathlib import Path

from .candidate import safe_relative
from .footprint_library import _object, _text, load_document


def capability(params, files, inputs):
    _object(params, ('declaration',))
    path = str(safe_relative(params['declaration']))
    if path not in files or inputs is None:
        raise ValueError('symbol query must be captured')
    query = load_document(Path(inputs) / path)
    _object(query, ('schema', 'libraries', 'selection', 'references'))
    if query['schema'] != 'agent-ee.symbol-query/1' or query['selection'] is not None:
        raise ValueError('pin-reference check requires a reference-list query')
    if not isinstance(query['references'], list) or not query['references']:
        raise ValueError('nonempty explicit pin references required')
    for ref in query['references']:
        _object(ref, ('reference', 'lib_id', 'pin'))
        _text(ref['reference'])
        _text(ref['lib_id'])
        if not isinstance(ref['pin'], dict) or set(ref['pin']) not in ({'name'}, {'number'}):
            raise ValueError('exact name/number selector required')
        _text(next(iter(ref['pin'].values())))
    if not isinstance(query['libraries'], list) or not query['libraries']:
        raise ValueError('explicit pinned libraries required')
    dependencies = []
    for lib in query['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        dep = str(safe_relative(lib['path']))
        if dep not in files:
            raise ValueError('symbol library is not captured')
        dependencies.append(dep)
    return dict(immutable_inputs=sorted(set(dependencies)), predicates=['symbol:pin-existence/1'])


def check_symbol_pins(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.manifest['files'], ctx.inputs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.symbol_context',
                     '/candidate/' + ctx.params['declaration'], '--captures', '/candidate',
                     '--out', '/work/pins.json'], 60)
    finally:
        ctx.evidence('pins.json')
    report = ctx.load_json('pins.json', 'captured symbol pins')
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='exact pin-reference existence on captured selected symbols')
    ctx.result['details']['report'] = report
    ctx.result['location'] = ', '.join(r['reference'] + ':' + r['lib_id']
        for r in report['references'] if r['status'] == 'defect')
    ctx.result['limitations'].append('Explicit selected-symbol references only; no automatic schematic '
        'instance binding, datasheet function verification or package choice. Bounding-box coverage is '
        'independent of pin-existence coverage.')
