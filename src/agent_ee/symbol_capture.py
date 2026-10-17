"""Bounded capture of selected symbols from a KiCad library too large to parse whole.

Run ``python -m agent_ee.symbol_capture --help`` for the standalone boundary.
A lexical scan validates the whole source library (UTF-8, one balanced
``kicad_symbol_lib`` root, quoting, escapes and comments, unique symbol
identities) without building its tree. The selected symbols and their
transitive ``extends`` ancestors are copied as exact source bytes, with the
library header, into a subset library under the original library name. That
subset then goes through the shared ``parse_library``/``resolve_symbol`` reader
with its usual limits; this module never interprets pins or graphics itself.

The receipt is informational capture provenance, not an electrical contract.
Captured upstream bytes keep their source licence and stay private until their
publication is authorized.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

from .symbol_library import (BYTE_LIMIT, DEPTH_LIMIT, LibraryError, children, field, parse_library,
                             parse_sexpr, resolve_symbol)

SCHEMA = 'agent-ee.symbol-capture/1'
BINDING_SCHEMA = 'agent-ee.symbol-capture-binding/1'
# Upper bounds; callers may lower them, never raise them.
LIMITS = dict(source_bytes=BYTE_LIMIT, scan_nodes=8000000, closure_symbols=64,
              closure_bytes=4 * 1024 * 1024, closure_nodes=1000000)
HEADER_KEYS = ('version', 'generator', 'generator_version')
LIBRARY_NAME = re.compile(r'[A-Za-z0-9_+\-.]{1,64}')
# Same lexical grammar as symbol_library.parse_sexpr: Unicode whitespace,
# ';' comments to end of line, and quoted strings where '\' escapes any
# character. The unrolled possessive string pattern is linear on failure.
TOKEN = re.compile(r'(\s+|;[^\n]*\n?)|(\()|(\))|"([^"\\]*+(?:\\.[^"\\]*+)*+)"|([^\s();"]+)|(")', re.S)
ESCAPE = re.compile(r'\\(.)', re.S)


class CaptureError(LibraryError):
    pass


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unescape(body):
    return ESCAPE.sub(lambda m: {'n': '\n', 'r': '\r', 't': '\t'}.get(m[1], m[1]), body)


def _limits(overrides):
    limits = dict(LIMITS)
    for key, value in (overrides or {}).items():
        if key not in LIMITS or type(value) is not int or not 0 < value <= LIMITS[key]:
            raise ValueError(f'capture limit {key} must be a positive integer at most {LIMITS.get(key)}')
        limits[key] = value
    return limits


def scan_library(data, *, limits=None):
    """Validate the whole library lexically; index top-level items by exact source span.

    Returns ``(text, header, symbols)``: the decoded text, header items and a
    name-to-item map, each item ``dict(head, name, start, end, nodes)`` with
    character offsets into ``text``. Node counts follow ``parse_sexpr``.
    """
    limits = _limits(limits)
    if not isinstance(data, bytes) or len(data) > limits['source_bytes']:
        raise CaptureError(f'source library must be bytes, at most {limits["source_bytes"]} bytes')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise CaptureError('source library is not UTF-8') from exc
    depth, nodes, roots, root_head = 0, 0, 0, None
    header, symbols, item = [], {}, None
    for match in TOKEN.finditer(text):
        kind = match.lastindex
        if kind == 1:
            continue
        if kind == 6:
            raise CaptureError('unterminated quote or escape at offset ' + str(match.start()))
        nodes += 1
        if nodes > limits['scan_nodes']:
            raise CaptureError(f'source scan node limit exceeded ({limits["scan_nodes"]} nodes)')
        if depth == 0 and (kind != 2 or roots):
            # Covers an unmatched ')', a stray atom and any second root.
            raise CaptureError('expected exactly one complete library root')
        if kind == 2 and depth == 1:
            if root_head is None:
                raise CaptureError('library root must start with kicad_symbol_lib')
            item = dict(head=None, name=None, start=match.start(), end=None, nodes=nodes, items=0)
        elif kind == 3:
            if depth == 2:
                item['end'], item['nodes'] = match.end(), nodes - item['nodes'] + 1
                if item['head'] == 'symbol':
                    if not isinstance(item['name'], str):
                        raise CaptureError('invalid symbol identity at offset ' + str(item['start']))
                    if item['name'] in symbols:
                        raise CaptureError('duplicate symbol identity: ' + item['name'])
                    symbols[item['name']] = item
                elif item['head'] in HEADER_KEYS and item['head'] not in [h['head'] for h in header]:
                    header.append(item)
                else:
                    # Unknown or repeated library-level nodes are refused, never dropped.
                    raise CaptureError('unsupported library-level node: ' + str(item['head']))
                item = None
            depth -= 1
            continue
        elif depth == 1 and kind != 2:
            atom = match[4] if kind == 4 else match[5]
            value = _unescape(atom) if kind == 4 else atom
            if root_head is not None:
                raise CaptureError('unsupported library-level atom')
            if value != 'kicad_symbol_lib':
                raise CaptureError('library root must start with kicad_symbol_lib')
            root_head = value
        elif depth == 2:
            item['items'] += 1
            if kind != 2 and item['items'] <= 2:
                value = _unescape(match[4]) if kind == 4 else match[5]
                item['head' if item['items'] == 1 else 'name'] = value
            elif kind == 2 and item['items'] == 2:
                item['name'] = []  # A list in name position is not a symbol identity.
        if kind == 2:
            depth += 1
            if depth > DEPTH_LIMIT:
                raise CaptureError(f'library nesting limit exceeded ({DEPTH_LIMIT} levels)')
            if depth == 1:
                roots += 1
    if depth or not roots:
        raise CaptureError('incomplete library: unbalanced parentheses')
    if root_head is None:
        raise CaptureError('library root must start with kicad_symbol_lib')
    if not symbols:
        raise CaptureError('empty library')
    for entry in (*header, *symbols.values()):
        del entry['items']
    return text, header, symbols


def _closure(text, symbols, selected, limits):
    """Selected symbols plus transitive ``extends`` ancestors, read by the shared reader."""
    closure, parents = {}, {}
    for name in selected:
        trail = []
        while name not in trail:
            if name not in symbols:
                raise CaptureError(('missing parent symbol: ' if trail else 'unknown symbol: ') + name)
            trail.append(name)
            if name not in closure:
                item = symbols[name]
                # UTF-8 decoding round-trips, so these are the exact source bytes.
                closure[name] = raw = text[item['start']:item['end']].encode('utf-8')
                if len(closure) > limits['closure_symbols']:
                    raise CaptureError(f'capture closure exceeds {limits["closure_symbols"]} symbols')
                if sum(map(len, closure.values())) > limits['closure_bytes']:
                    raise CaptureError(f'capture closure exceeds {limits["closure_bytes"]} bytes')
                if sum(symbols[x]['nodes'] for x in closure) > limits['closure_nodes']:
                    raise CaptureError(f'capture closure exceeds {limits["closure_nodes"]} nodes')
                node = parse_sexpr(raw)
                if len(children(node, 'extends')) > 1:
                    raise CaptureError('ambiguous extends declarations: ' + name)
                parents[name] = field(node, 'extends', 1)[0] if children(node, 'extends') else None
            if parents[name] is None:
                break
            name = parents[name]
        else:
            raise CaptureError('extends cycle: ' + ' -> '.join((*trail, name)))
    return closure, parents


def implementation():
    return dict(module=__name__, sha256=_sha(Path(__file__).read_bytes()))


def capture_symbols(data, selected, *, library, source_file=None, limits=None):
    """Pure capture: returns ``(library_bytes, receipt)`` or raises ``CaptureError``."""
    if not isinstance(library, str) or not LIBRARY_NAME.fullmatch(library):
        raise CaptureError('library name must be a safe KiCad library nickname')
    if source_file is not None and (not isinstance(source_file, str) or PurePosixPath(source_file).name != source_file
                                    or not source_file.endswith('.kicad_sym')):
        raise CaptureError('source file must be a bare .kicad_sym file name')
    if not isinstance(selected, (list, tuple)) or not selected or len(selected) > LIMITS['closure_symbols'] or \
            any(not isinstance(x, str) or not x for x in selected) or len(set(selected)) != len(selected):
        raise CaptureError('selected symbols must be unique nonempty names')
    limits = _limits(limits)
    text, header, symbols = scan_library(data, limits=limits)
    closure, parents = _closure(text, symbols, sorted(selected), limits)
    head = [(x, text[x['start']:x['end']].encode('utf-8')) for x in header]
    body = sorted(((symbols[name], raw) for name, raw in closure.items()), key=lambda x: x[0]['start'])
    # Source order is deterministic and keeps ancestors where the library put them.
    items = sorted(head + body, key=lambda x: x[0]['start'])
    output = b'(kicad_symbol_lib\n' + b''.join(b'\t' + raw + b'\n' for _, raw in items) + b')\n'
    path = library + '.kicad_sym'
    digest = _sha(output)
    # The subset must satisfy the shared reader and resolver unchanged.
    captured = parse_library(output)
    if set(captured) != set(closure):
        raise CaptureError('captured subset does not match the scanned closure')
    for name in sorted(selected):
        resolve_symbol(captured, name, path, digest)
    receipt = dict(
        schema=SCHEMA, implementation=implementation(), authority='informational-provenance',
        source=dict(library=library, file=source_file, sha256=_sha(data), bytes=len(data),
                    header=[dict(key=x['head'], sha256=_sha(raw)) for x, raw in head]),
        selected=sorted(selected),
        symbols=[dict(name=x['name'], role='selected' if x['name'] in selected else 'ancestor',
                      extends=parents[x['name']], sha256=_sha(raw), bytes=len(raw), nodes=x['nodes'])
                 for x, raw in body],
        output=dict(path=path, sha256=digest, bytes=len(output)), limits=limits)
    receipt['canonical_sha256'] = _sha(_json_bytes(receipt))
    return output, receipt


def verify_capture(receipt, captured, source):
    """Re-derive a capture from source bytes; any byte or receipt difference refuses."""
    if not isinstance(receipt, dict) or receipt.get('schema') != SCHEMA:
        raise CaptureError('not a symbol capture receipt')
    payload = {k: v for k, v in receipt.items() if k != 'canonical_sha256'}
    if receipt.get('canonical_sha256') != _sha(_json_bytes(payload)):
        raise CaptureError('capture receipt identity mismatch')
    output, fresh = capture_symbols(source, receipt['selected'], library=receipt['source']['library'],
                                    source_file=receipt['source']['file'], limits=receipt['limits'])
    if fresh != receipt or output != captured:
        raise CaptureError('capture does not re-derive from the supplied source bytes')
    return fresh


def bind_resolved(receipt, resolved):
    """Bind a passing resolved-connectivity record to a capture by captured library hash."""
    from .connectivity_ir import validate_resolved
    validate_resolved(resolved)
    payload = {k: v for k, v in receipt.items() if k != 'canonical_sha256'}
    if receipt.get('schema') != SCHEMA or receipt.get('canonical_sha256') != _sha(_json_bytes(payload)):
        raise CaptureError('capture receipt identity mismatch')
    output, library = receipt['output'], receipt['source']['library']
    bound = [lib for lib in resolved['libraries'] if lib['sha256'] == output['sha256']]
    if len(bound) != 1 or bound[0]['name'] != library or PurePosixPath(bound[0]['path']).name != output['path']:
        raise CaptureError('resolved record does not name the captured library exactly once')
    captured = {s['name'] for s in receipt['symbols']}
    components = []
    for comp in resolved.get('components', []):
        sources = comp['sources']
        if any(s['sha256'] == output['sha256'] for s in sources):
            if comp['lib_id'].split(':')[0] != library or {s['symbol'] for s in sources} - captured:
                raise CaptureError('component source is outside the capture: ' + comp['id'])
            components.append(comp['id'])
    if not components:
        raise CaptureError('resolved record uses no captured symbol')
    binding = dict(schema=BINDING_SCHEMA, capture_sha256=receipt['canonical_sha256'],
                   resolved_sha256=resolved['canonical_sha256'], library=bound[0],
                   source_sha256=receipt['source']['sha256'], components=sorted(components),
                   symbols=[dict(name=s['name'], sha256=s['sha256']) for s in receipt['symbols']])
    binding['canonical_sha256'] = _sha(_json_bytes(binding))
    return binding


def main(argv=None):
    from .schematic_emitter import publish
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, help='installed or captured .kicad_sym library (read only)')
    parser.add_argument('--symbol', action='append', required=True, help='exact symbol name; repeatable')
    parser.add_argument('--library', help='library nickname (default: source file stem)')
    parser.add_argument('--out', required=True, type=Path, help='new output directory')
    args = parser.parse_args(argv)
    try:
        with args.source.open('rb') as stream:
            data = stream.read(LIMITS['source_bytes'] + 1)
        output, receipt = capture_symbols(data, args.symbol, library=args.library or args.source.stem,
                                          source_file=args.source.name)
        publish({receipt['output']['path']: output, 'capture.json': _json_bytes(receipt) + b'\n'}, args.out)
    except (LibraryError, OSError) as exc:
        print('symbol capture refused: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(dict(output=receipt['output'], source_sha256=receipt['source']['sha256'],
                          symbols=[(s['name'], s['sha256']) for s in receipt['symbols']]), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
