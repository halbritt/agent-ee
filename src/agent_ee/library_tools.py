"""Small literal offline tool surface over captured symbol and footprint facts."""
from .footprint_library import (_object, _seal, _members, canonical, digest,
                                index_footprints, read_captures, load_document)
from .symbol_context import build_context


def dispatch_tool(name, request, captures):
    """Caller supplies all bytes; no process, provider or ambient library lookup."""
    if name in ('find-symbol', 'list-pins'):
        _object(request, ('libraries', 'selection'))
        result = build_context(request['libraries'], captures, request['selection'])
    elif name == 'find-footprint':
        _object(request, ('manifest', 'query'))
        if not isinstance(request['query'], str):
            raise ValueError('literal footprint query must be a string')
        index = index_footprints(request['manifest'], captures)
        result = _seal(dict(schema='agent-ee.footprint-query/1', query=request['query'],
            execution=index['execution'], outcome=index['outcome'], coverage=index['coverage'],
            index_sha256=index['canonical_sha256'], findings=index['findings'],
            matches=[fp for fp in index['footprints'] if fp['lib_id'] == request['query']]))
    else:
        raise ValueError('unknown offline library tool')
    return _seal(dict(schema='agent-ee.library-tool-result/1', tool=name,
                      request_sha256=digest(request), result=result))


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', choices=('find-symbol', 'list-pins', 'find-footprint'))
    parser.add_argument('request')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        request = load_document(args.request)
        if args.tool == 'find-footprint':
            paths = [member['path'] for _, member in _members(request['manifest'])]
        else:
            paths = [lib['path'] for lib in request['libraries']]
        result = dispatch_tool(args.tool, request, read_captures(args.captures, paths))
        with Path(args.out).open('xb') as stream:
            stream.write(canonical(result) + b'\n')
        return 0 if result['result']['execution'] == 'ok' else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
