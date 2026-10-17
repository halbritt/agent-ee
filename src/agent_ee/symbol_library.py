"""Bounded, independent reader for captured KiCad symbol libraries."""
from decimal import Decimal, DecimalException
import re


ELECTRICAL_TYPES = frozenset(('input', 'output', 'bidirectional', 'tri_state', 'passive',
                              'free', 'unspecified', 'power_in', 'power_out',
                              'open_collector', 'open_emitter', 'no_connect'))


class LibraryError(ValueError):
    pass


def parse_library(data):
    if not isinstance(data, bytes) or len(data) > 32 * 1024 * 1024:
        raise LibraryError('library must be bytes, at most 32 MiB')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise LibraryError('library is not UTF-8') from exc
    if '\x00' in text:
        raise LibraryError('NUL byte in library')
    stack, roots, nodes = [], [], 0
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char.isspace():
            pos += 1
            continue
        if char == ';':
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end + 1
            continue
        nodes += 1
        if nodes > 2000000:
            raise LibraryError('library node limit exceeded')
        if char == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
            if len(stack) > 128:
                raise LibraryError('library nesting limit exceeded')
            pos += 1
        elif char == ')':
            if not stack:
                raise LibraryError('unmatched closing parenthesis')
            stack.pop()
            pos += 1
        else:
            if char == '"':
                pos += 1
                chars = []
                while pos < len(text) and text[pos] != '"':
                    if text[pos] == '\\':
                        pos += 1
                        if pos >= len(text):
                            raise LibraryError('unterminated escape')
                        chars.append({'n': '\n', 'r': '\r', 't': '\t'}.get(text[pos], text[pos]))
                    else:
                        chars.append(text[pos])
                    pos += 1
                if pos >= len(text):
                    raise LibraryError('unterminated quote')
                pos += 1
                atom = ''.join(chars)
            else:
                end = pos
                while end < len(text) and not text[end].isspace() and text[end] not in '();"':
                    end += 1
                if end == pos:
                    raise LibraryError('invalid token')
                atom, pos = text[pos:end], end
            (stack[-1] if stack else roots).append(atom)
    if stack or len(roots) != 1 or not isinstance(roots[0], list) or not roots[0] or roots[0][0] != 'kicad_symbol_lib':
        raise LibraryError('expected one complete kicad_symbol_lib')
    symbols = {}
    for symbol in children(roots[0], 'symbol'):
        if len(symbol) < 2 or not isinstance(symbol[1], str) or symbol[1] in symbols:
            raise LibraryError('invalid/duplicate symbol')
        symbols[symbol[1]] = symbol
    if not symbols:
        raise LibraryError('empty library')
    return symbols


def children(node, key):
    return [x for x in node[1:] if isinstance(x, list) and x and x[0] == key]


def field(node, key, count):
    found = children(node, key)
    if len(found) != 1 or len([x for x in found[0][1:] if isinstance(x, str)]) != count:
        raise LibraryError(f'expected one {key} with {count} atoms')
    return [x for x in found[0][1:] if isinstance(x, str)]


def nanometres(value):
    # Decimal multiplication observes a mutable precision context and can round.
    # Work directly from the exact coefficient/exponent instead.
    if len(value) > 128:
        raise LibraryError('coordinate token limit exceeded')
    try:
        number = Decimal(value)
    except DecimalException as exc:
        raise LibraryError('invalid coordinate') from exc
    if not number.is_finite():
        raise LibraryError('coordinate must be finite exact nanometres')
    sign, digits, exponent = number.as_tuple()
    coefficient = int(''.join(map(str, digits)))
    if coefficient == 0:
        return 0
    exponent += 6
    if exponent < -128 or exponent > 15:
        raise LibraryError('coordinate outside exact nanometre range')
    if exponent < 0:
        coefficient, remainder = divmod(coefficient, 10 ** -exponent)
        if remainder:
            raise LibraryError('coordinate must be finite exact nanometres')
    else:
        coefficient *= 10 ** exponent
    if coefficient > 10**15:
        raise LibraryError('coordinate outside exact nanometre range')
    return -coefficient if sign else coefficient


def resolve_symbol(symbols, name, path, digest, trail=()):
    if len(trail) > 64:
        raise LibraryError('extends depth limit exceeded')
    if name in trail:
        raise LibraryError('extends cycle: ' + ' -> '.join((*trail, name)))
    if name not in symbols:
        raise LibraryError('missing parent symbol: ' + name)
    node = symbols[name]
    source = dict(path=path, sha256=digest, symbol=name)
    parents = children(node, 'extends')
    if parents:
        parent = field(node, 'extends', 1)[0]
        pins, sources, power = resolve_symbol(symbols, parent, path, digest, (*trail, name))
        if children(node, 'symbol'):
            raise LibraryError('derived symbol with replacement graphics/pins unsupported')
        return pins, sources + [source], power or bool(children(node, 'power'))
    pins = []
    for unit_node in children(node, 'symbol'):
        match = re.fullmatch(r'.*_(\d{1,4})_(\d{1,4})', unit_node[1]) if len(unit_node) > 1 and isinstance(unit_node[1], str) else None
        if not match:
            raise LibraryError('invalid symbol unit identifier')
        unit, style = map(int, match.groups())
        if style not in (0, 1):
            # Alternative graphical representations cannot silently replace pin geometry.
            if children(unit_node, 'pin'):
                raise LibraryError('alternate pin representation unsupported')
            continue
        for pin in children(unit_node, 'pin'):
            if len(pin) < 3 or not isinstance(pin[1], str) or pin[1] not in ELECTRICAL_TYPES:
                raise LibraryError('invalid pin type')
            number = field(pin, 'number', 1)[0]
            label = field(pin, 'name', 1)[0]
            x, y, angle = field(pin, 'at', 3)
            orientation = nanometres(angle)
            if orientation % 90000000:
                raise LibraryError('non-orthogonal pin orientation unsupported')
            if not number:
                raise LibraryError('empty physical pin number')
            pins.append(dict(number=number, name=label, electrical_type=pin[1], unit=unit,
                             x_nm=nanometres(x), y_nm=nanometres(y),
                             orientation=(orientation // 1000000) % 360,
                             source=dict(source, unit_symbol=unit_node[1], pin=number)))
    return pins, [source], bool(children(node, 'power'))
