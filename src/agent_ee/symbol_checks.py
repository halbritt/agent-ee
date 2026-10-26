"""Coordinator pin-reference check over explicit captured selected-symbol queries."""

from .check_registry import register as _register_check
from pathlib import Path

from .candidate import safe_relative
from .footprint_library import _object, load_document
from .symbol_context import validate_pin_reference


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
        validate_pin_reference(ref)
    if not isinstance(query['libraries'], list) or not query['libraries']:
        raise ValueError('explicit pinned libraries required')
    dependencies = []
    for lib in query['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        dep = str(safe_relative(lib['path']))
        if dep not in files:
            raise ValueError('symbol library is not captured')
        dependencies.append(dep)
    predicates = ['symbol:pin-existence/1']
    if any('expected' in ref for ref in query['references']):
        # Attribute expectations are verification authority, not repairable data.
        dependencies.append(path)
        predicates.append('symbol:pin-attributes/1')
    return dict(immutable_inputs=sorted(set(dependencies)), predicates=predicates)


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
                      summary='exact pin references and declared attributes on captured selected symbols')
    ctx.result['details']['report'] = report
    ctx.result['location'] = ', '.join(r['reference'] + ':' + r['lib_id']
        for r in report['references'] if r['status'] == 'defect')
    ctx.result['limitations'].append('Explicit selected-symbol references only; no automatic schematic '
        'instance binding, datasheet function verification or package choice. Bounding-box coverage is '
        'independent of pin-existence coverage.')


def describe_symbol_pins(ctx):
    descriptor = capability(ctx.params, ctx.files, ctx.inputs)
    ctx.captured(ctx.params['declaration'])
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('symbol_pins', describe_symbol_pins, check_symbol_pins)
