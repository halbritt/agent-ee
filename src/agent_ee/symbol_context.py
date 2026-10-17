"""Deterministic context and exact pin lookups over the shared captured resolver.

Inheritance and electrical inventory are owned by symbol_library. This module
consumes its resolved source chain to present properties and graphic bounds.
"""
from .footprint_library import (_symbol_inventory, _object, _seal, _text, canonical,
                                digest, FootprintError, read_captures)


BBOX_DEFINITION = ('Local body graphic centreline bounds plus complete pin segments, per unit/body 1; '
                   'common unit/body graphics included; text, stroke thickness and rendered labels excluded.')


def _metadata(sources, captured_files):
    from .symbol_library import parse_library, children, LibraryError
    properties, units = {}, []
    for source in sources:
        # The shared resolver already established this exact, ordered inheritance
        # chain and refused unsupported replacements. No extends traversal here.
        node = parse_library(captured_files[source['path']])[source['symbol']]
        keys = set()
        for prop in children(node, 'property'):
            if len(prop) < 3 or any(not isinstance(x, str) for x in prop[1:3]) or prop[1] in keys:
                raise LibraryError('ambiguous symbol properties')
            keys.add(prop[1])
            properties[prop[1]] = prop[2]
        units.extend(children(node, 'symbol'))
    return properties, units


def _bounds(units, pins, unit):
    from .symbol_library import children, field, nanometres, LibraryError
    points = []
    try:
        for node in units:
            _, block_unit, body = node[1].rsplit('_', 2)
            if int(block_unit) not in (0, unit) or int(body) not in (0, 1):
                continue
            for graphic in node[2:]:
                if not isinstance(graphic, list) or not graphic:
                    raise LibraryError('invalid graphic node')
                kind = graphic[0]
                if kind == 'pin':
                    # Inventory authority remains the shared resolver. Only length
                    # is read here to complete the graphic segment's bounds.
                    number = field(graphic, 'number', 1)[0]
                    x, y, angle = field(graphic, 'at', 3)
                    x, y, angle = nanometres(x), nanometres(y), nanometres(angle)
                    if not any(p['number'] == number and p['unit'] == int(block_unit)
                               and p['x_nm'] == x and p['y_nm'] == y for p in pins):
                        raise LibraryError('graphic pin does not match shared inventory')
                    length = nanometres(field(graphic, 'length', 1)[0])
                    if length < 0 or angle % 90000000:
                        raise LibraryError('unqualified pin segment')
                    dx, dy = {0: (length, 0), 90: (0, length),
                              180: (-length, 0), 270: (0, -length)}[(angle // 1000000) % 360]
                    points.extend(((x, y), (x + dx, y + dy)))
                elif kind == 'rectangle':
                    points.extend(tuple(map(nanometres, field(graphic, key, 2))) for key in ('start', 'end'))
                elif kind == 'polyline':
                    pts = children(graphic, 'pts')
                    if len(pts) != 1 or len(pts[0]) < 3:
                        raise LibraryError('polyline requires complete points')
                    for xy in pts[0][1:]:
                        if not isinstance(xy, list) or len(xy) != 3 or xy[0] != 'xy':
                            raise LibraryError('invalid polyline point')
                        points.append(tuple(map(nanometres, xy[1:])))
                elif kind == 'circle':
                    x, y = map(nanometres, field(graphic, 'center', 2))
                    radius = nanometres(field(graphic, 'radius', 1)[0])
                    if radius <= 0:
                        raise LibraryError('invalid circle radius')
                    points.extend(((x - radius, y - radius), (x + radius, y + radius)))
                elif kind in ('text', 'text_box'):
                    continue
                else:
                    raise LibraryError('unsupported bounded graphic: ' + str(kind))
        if not points:
            raise LibraryError('empty bounded graphics')
        return dict(coverage='complete', bounds_nm=[min(p[0] for p in points), min(p[1] for p in points),
                                                   max(p[0] for p in points), max(p[1] for p in points)])
    except (LibraryError, ValueError, TypeError) as exc:
        return dict(coverage='unsupported', bounds_nm=None, reason=str(exc))


def build_context(libraries, captured_files, selection):
    """Exact lib:symbol lookup; all pins survive names shared by several numbers."""
    from .symbol_library import LibraryError
    result = dict(schema='agent-ee.symbol-context/1', execution='ok', outcome='info',
                  selection=selection, query_sha256=digest(selection), sources=[], pins=[],
                  properties={}, description='', selected_body_style=1, bbox_definition=BBOX_DEFINITION, bbox_by_unit={})
    try:
        pins, sources, power = _symbol_inventory(selection, libraries, captured_files)
        presented_pins = [dict(p, body_style=int(p['source']['unit_symbol'].rsplit('_', 1)[1])) for p in pins]
        result.update(pins=sorted(presented_pins, key=lambda p: (p['unit'], p['number'], canonical(p))),
                      sources=sources, power=power)
        properties, units = _metadata(sources, captured_files)
        result['properties'] = properties
        result['description'] = properties.get('Description', properties.get('ki_description', ''))
        unit_numbers = sorted({p['unit'] for p in pins if p['unit']}) or [1]
        for unit in unit_numbers:
            result['bbox_by_unit'][str(unit)] = _bounds(units, pins, unit)
    except (FootprintError, LibraryError) as exc:
        result.update(execution='failed', outcome='unknown', message=str(exc))
    return _seal(result)


def check_pin_references(libraries, captured_files, references):
    """Declared selectors, independent of connectivity's full net-accounting rules."""
    result = dict(schema='agent-ee.pin-reference-result/1', execution='ok', outcome='pass',
                  references_sha256=digest(references), references=[], contexts=[])
    contexts = {}
    try:
        if not isinstance(references, list) or not references:
            raise FootprintError('nonempty pin-reference list required')
        for ref in references:
            _object(ref, ('reference', 'lib_id', 'pin'))
            _text(ref['reference'])
            _text(ref['lib_id'])
            selector = ref['pin']
            if not isinstance(selector, dict) or set(selector) not in ({'number'}, {'name'}):
                raise FootprintError('pin requires exactly one name or number')
            _text(next(iter(selector.values())))
        identities = {(r['reference'], r['lib_id']) for r in references}
        if len({r for r, _ in identities}) != len(identities):
            raise FootprintError('reference has ambiguous selected symbols')
    except (ValueError, TypeError) as exc:
        result.update(execution='malformed', outcome='unknown', message=str(exc))
        return _seal(result)
    for ref in references:
        selection = ref['lib_id']
        if selection not in contexts:
            contexts[selection] = build_context(libraries, captured_files, selection)
        context = contexts[selection]
        record = dict(reference=ref['reference'], lib_id=selection, pin=dict(ref['pin']),
                      status='unknown', context_sha256=context['canonical_sha256'])
        if context['execution'] != 'ok':
            result.update(execution='failed', outcome='unknown')
            record['message'] = context.get('message', 'unavailable selected symbol')
        else:
            key, value = next(iter(ref['pin'].items()))
            matches = [p for p in context['pins'] if p[key] == value]
            record['available_pins'] = [dict(number=p['number'], name=p['name']) for p in context['pins']]
            record['numbers'] = sorted({p['number'] for p in matches})
            record['status'] = 'pass' if matches else 'defect'
        result['references'].append(record)
    result['contexts'] = [contexts[key] for key in sorted(contexts)]
    if result['execution'] == 'ok' and any(r['status'] == 'defect' for r in result['references']):
        result['outcome'] = 'defect'
    return _seal(result)


def main(argv=None):
    import argparse
    from pathlib import Path
    from .footprint_library import load_document
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('declaration', help='captured JSON schema agent-ee.symbol-query/1')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        declaration = load_document(args.declaration)
        _object(declaration, ('schema', 'libraries', 'selection', 'references'))
        if declaration['schema'] != 'agent-ee.symbol-query/1':
            raise FootprintError('unsupported symbol query schema')
        captured = read_captures(args.captures, [lib['path'] for lib in declaration['libraries']])
        if declaration['references'] is None:
            result = build_context(declaration['libraries'], captured, declaration['selection'])
        else:
            if declaration['selection'] is not None:
                raise FootprintError('pin references require null selection')
            result = check_pin_references(declaration['libraries'], captured, declaration['references'])
        with Path(args.out).open('xb') as stream:
            stream.write(canonical(result) + b'\n')
        return 0 if result['execution'] == 'ok' else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
