"""Wire-free KiCad 10 schematic emitter for resolved, captured connectivity.

    python3 -m agent_ee.schematic_emit RESOLVED.json --libraries LIBDIR \\
        --parts PARTS.json --project-template TEMPLATE.kicad_pro --name NAME --out OUTDIR

The input is a complete, passing ``agent-ee.resolved-connectivity/1`` record.
Every symbol is embedded from the captured library bytes named by the record
(sha256 re-verified), every pin receives a net label (``/X`` nets) or a
captured power symbol (bare names such as ``GND``) exactly at its connection
point, and no wires are drawn. Anything this cannot express exactly is refused;
nothing is guessed. The emitter does not run KiCad; native ERC and netlist
export of the result are the caller's checks.
"""
import argparse
import copy
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path, PurePosixPath

from .connectivity_ir import RESOLVED_SCHEMA, validate_resolved
from .symbol_library import LibraryError, children, nanometres, parse_library, parse_sexpr, resolve_symbol

RECEIPT_SCHEMA = 'agent-ee.rung1-schematic-emit-receipt/0-experimental'
QUALIFICATION = 'experimental-unqualified'
PARTS_SCHEMA = 'agent-ee.rung1-parts-manifest/0-experimental'
SCH_VERSION = '20260306'  # KiCad 10 schematic file version (fixtures/rc/control).
GRID = 2540000  # 2.54 mm in nanometres.
MANIFEST_FIELDS = ('Footprint', 'LCSC', 'MPN', 'Manufacturer', 'JLCPCB_Assembly')
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/halbritt/agent-ee/schematic-emit/1')
PAPERS = (('A4', 297, 210), ('A3', 420, 297), ('A2', 594, 420), ('A1', 841, 594), ('A0', 1189, 841))
LIB_NAME = re.compile(r'[A-Za-z0-9_.+\-]+')
PROJECT_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}')
FONT = 1270000  # Label/field character pitch estimate used only for spacing.


class EmitError(ValueError):
    """The inputs cannot be emitted exactly; nothing is written."""


class Quoted(str):
    """An atom that KiCad wrote (or must read) as a quoted string."""


# --- S-expression reading and KiCad-canonical writing --------------------------------------

def parse_quoted(data):
    """Parse like ``symbol_library.parse_sexpr`` but remember which atoms were quoted.

    The shared parser is run first and stays the authority: its limits apply and the
    two trees must be equal, so the quote marks are the only added information.
    """
    authority = parse_sexpr(data)
    text, stack, roots, pos = data.decode('utf-8'), [], [], 0
    while pos < len(text):
        char = text[pos]
        if char.isspace():
            pos += 1
        elif char == ';':
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end + 1
        elif char == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
            pos += 1
        elif char == ')':
            stack.pop()
            pos += 1
        elif char == '"':
            pos += 1
            chars = []
            while text[pos] != '"':
                if text[pos] == '\\':
                    pos += 1
                    chars.append({'n': '\n', 'r': '\r', 't': '\t'}.get(text[pos], text[pos]))
                else:
                    chars.append(text[pos])
                pos += 1
            pos += 1
            stack[-1].append(Quoted(''.join(chars)))
        else:
            end = pos
            while end < len(text) and not text[end].isspace() and text[end] not in '();"':
                end += 1
            stack[-1].append(text[pos:end])
            pos = end
    if len(roots) != 1 or roots[0] != authority:
        raise LibraryError('quote-preserving parse disagrees with the shared parser')
    return roots[0]


def _atom(value):
    if isinstance(value, Quoted):
        escaped = (value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                   .replace('\r', '\\r').replace('\t', '\\t'))
        return '"' + escaped + '"'
    if not value or any(c.isspace() or c in '()";' for c in value):
        raise EmitError('unquoted atom is not a KiCad token: ' + repr(value))
    return value


def _is_xy(node):
    return isinstance(node, list) and node and node[0] == 'xy' and not any(isinstance(x, list) for x in node)


def format_sexpr(node, depth=0, out=None):
    """KiCad 10 layout: tab indent, atom-only lists inline, consecutive xy wrapped at column 99.

    This reproduces every stock KiCad 10.0.7 symbol library and the fixture
    schematic byte for byte when applied to their quote-preserving parse.
    """
    lines = [] if out is None else out
    indent = '\t' * depth
    if not any(isinstance(child, list) for child in node):
        lines.append(indent + '(' + ' '.join(map(_atom, node)) + ')')
        return lines
    head = 0
    while head < len(node) and not isinstance(node[head], list):
        head += 1
    lines.append(indent + '(' + ' '.join(map(_atom, node[:head])))
    index = head
    while index < len(node):
        child = node[index]
        if _is_xy(child):
            line = ''
            while index < len(node) and _is_xy(node[index]):
                token = '(' + ' '.join(map(_atom, node[index])) + ')'
                if line and len(indent) + 1 + len(line) >= 99:
                    lines.append(indent + '\t' + line)
                    line = token
                else:
                    line = line + ' ' + token if line else token
                index += 1
            lines.append(indent + '\t' + line)
            continue
        if isinstance(child, list):
            format_sexpr(child, depth + 1, lines)
        else:
            lines.append(indent + '\t' + _atom(child))
        index += 1
    lines.append(indent + ')')
    return lines


def _mm(nm):
    sign = '-' if nm < 0 else ''
    whole, frac = divmod(abs(nm), 1000000)
    return sign + str(whole) + ('.' + f'{frac:06d}'.rstrip('0') if frac else '')


# --- Inputs ---------------------------------------------------------------------------------

def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EmitError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _json(data, label):
    try:
        return json.loads(data, object_pairs_hook=_unique_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(EmitError('non-finite JSON')))
    except (ValueError, UnicodeError) as exc:
        raise EmitError(f'{label} is not valid JSON: {exc}') from exc


_LIBRARY_CACHE = {}


def _library(data):
    """Return (plain symbols, quoted symbols) for captured bytes, cached by content hash."""
    digest = _sha(data)
    if digest not in _LIBRARY_CACHE:
        plain = parse_library(data)
        root = parse_quoted(data)
        quoted = {node[1]: node for node in children(root, 'symbol')}
        _LIBRARY_CACHE.clear()  # One emission's libraries at a time; bounds memory.
        _LIBRARY_CACHE[digest] = (plain, quoted)
    return _LIBRARY_CACHE[digest]


def _parts(manifest, references):
    if not isinstance(manifest, dict) or manifest.get('schema') != PARTS_SCHEMA:
        raise EmitError(f'parts manifest must be a {PARTS_SCHEMA} object')
    parts = manifest.get('parts')
    entries = {}
    if isinstance(parts, dict):
        items = list(parts.items())
    elif isinstance(parts, list):
        items = []
        for entry in parts:
            if not isinstance(entry, dict) or not isinstance(entry.get('reference'), str):
                raise EmitError('parts list entries need a string reference')
            items.append((entry['reference'], entry))
    else:
        raise EmitError('parts manifest requires parts as an object or list')
    ignored = []
    for reference, entry in items:
        if reference in entries:
            raise EmitError('parts manifest repeats reference ' + reference)
        if not isinstance(entry, dict):
            raise EmitError(f'parts manifest entry {reference} is not an object')
        source = entry.get('fields', entry)
        if not isinstance(source, dict):
            raise EmitError(f'parts manifest entry {reference} fields is not an object')
        fields = {}
        for key in MANIFEST_FIELDS:
            if key in source:
                value = source[key]
                if not isinstance(value, str) or not value or '\x00' in value or '\n' in value:
                    raise EmitError(f'{reference}.{key} must be a nonempty single-line string')
                fields[key] = value
        footprint = fields.get('Footprint')
        if footprint is None:
            raise EmitError(f'{reference}: parts manifest entry has no Footprint')
        library, sep, name = footprint.partition(':')
        if not sep or not LIB_NAME.fullmatch(library) or not name or ':' in name or name != name.strip():
            raise EmitError(f'{reference}: Footprint must be LIBRARY:NAME, got {footprint!r}')
        if fields.get('JLCPCB_Assembly', 'not_assembled') != 'not_assembled':
            raise EmitError(f'{reference}: JLCPCB_Assembly may only be "not_assembled"')
        ignored.extend(f'{reference}.{key}' for key in sorted(set(source) - set(MANIFEST_FIELDS)
                                                               - {'reference', 'fields'}))
        entries[reference] = fields
    unknown = sorted(set(entries) - set(references))
    if unknown:
        raise EmitError('parts manifest names references absent from the record: ' + ', '.join(unknown))
    missing = sorted(set(references) - set(entries))
    if missing:
        raise EmitError('populated components missing from the parts manifest: ' + ', '.join(missing))
    return entries, ignored


def _validated_record(record):
    if isinstance(record, dict) and record.get('schema') == RESOLVED_SCHEMA and record.get('findings'):
        detail = []
        for item in record['findings']:
            text = f"{item.get('code')} at {item.get('location')}: {item.get('message')}"
            if item.get('available_pins') is not None:
                text += '; available pins: ' + ', '.join(
                    f"{p['number']} ({p['name']})" if p.get('name') not in (None, '', '~') else p['number']
                    for p in item['available_pins'])
            if item.get('available_symbols') is not None:
                text += f"; {len(item['available_symbols'])} symbols in library"
            detail.append(text)
        raise EmitError('resolved connectivity is not complete and passing: ' + ' | '.join(detail))
    try:
        return validate_resolved(record)
    except (ValueError, TypeError, KeyError) as exc:
        raise EmitError(f'resolved connectivity rejected: {exc}') from exc


# --- Symbols --------------------------------------------------------------------------------

def _hidden(node):
    return ['hide', 'yes'] in node[1:] or any(['hide', 'yes'] in e[1:] or 'hide' in e[1:]
                                             for e in children(node, 'effects'))


def _flatten(quoted, name, trail=()):
    """Return a self-contained copy of library symbol ``name`` (``extends`` merged)."""
    node = quoted[name]
    parents = children(node, 'extends')
    if not parents:
        return copy.deepcopy(node)
    parent_name = parents[0][1]
    if name in trail or len(trail) > 64:
        raise EmitError('extends cycle at ' + name)
    parent = _flatten(quoted, parent_name, (*trail, name))
    own = [c for c in node[2:] if isinstance(c, list) and c[0] != 'extends']
    own_props = {c[1]: c for c in own if c[0] == 'property'}
    own_other = {c[0]: c for c in own if c[0] != 'property'}
    if 'symbol' in own_other:
        raise EmitError(f'derived symbol {name} replaces graphics/pins; unsupported')
    result = ['symbol', Quoted(name)]
    seen_props, seen_other = set(), set()
    tail = []
    for item in parent[2:]:
        if isinstance(item, list) and item[0] == 'property':
            result.append(copy.deepcopy(own_props.get(item[1], item)))
            seen_props.add(item[1])
        elif isinstance(item, list) and item[0] == 'symbol':
            if not item[1].startswith(parent_name + '_'):
                raise EmitError(f'unit {item[1]} does not belong to {parent_name}')
            tail.append([item[0], Quoted(name + item[1][len(parent_name):]), *item[2:]])
        elif isinstance(item, list) and item[0] == 'embedded_fonts':
            tail.append(copy.deepcopy(own_other.get('embedded_fonts', item)))
            seen_other.add('embedded_fonts')
        elif isinstance(item, list) and item[0] in own_other:
            result.append(copy.deepcopy(own_other[item[0]]))
            seen_other.add(item[0])
        else:
            result.append(item)
    position = max((i for i, x in enumerate(result) if isinstance(x, list) and x[0] == 'property'),
                   default=len(result) - 1) + 1
    extra = [copy.deepcopy(own_props[k]) for k in own_props if k not in seen_props]
    result[position:position] = extra
    others = [copy.deepcopy(v) for k, v in own_other.items() if k not in seen_other]
    first_prop = next((i for i, x in enumerate(result) if isinstance(x, list) and x[0] == 'property'),
                      len(result))
    result[first_prop:first_prop] = others
    return result + tail


def _property(node, key):
    found = [c for c in children(node, 'property') if len(c) > 2 and c[1] == key]
    return found[0] if found else None


def _at(node):
    at = children(node, 'at')
    if len(at) != 1 or len(at[0]) not in (3, 4):
        raise EmitError('expected one (at x y [angle])')
    x, y = nanometres(at[0][1]), nanometres(at[0][2])
    angle = at[0][3] if len(at[0]) == 4 else '0'
    return x, y, angle


def _effects(node, justify=None):
    effects = children(node, 'effects')
    effects = copy.deepcopy(effects[0]) if effects else ['effects', ['font', ['size', '1.27', '1.27']]]
    effects = [e for e in effects if not (isinstance(e, list) and e[0] == 'hide') and e != 'hide']
    if justify:
        effects = [e for e in effects if not (isinstance(e, list) and e[0] == 'justify')]
        effects.append(['justify', *justify])
    return effects


def _rotate(x, y, rotation):
    """Rotate schematic-space offset (y down) counter-clockwise on screen."""
    for _ in range(rotation // 90):
        x, y = y, -x
    return x, y


DIRECTION = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # Pin toward body, schematic axes.


# --- Emission -------------------------------------------------------------------------------

class _Emitter:
    def __init__(self, record, captures, parts, template, name):
        if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
            raise EmitError('name must match ' + PROJECT_NAME.pattern)
        self.record = _validated_record(record)
        self.name = name
        self.seed = self.record['canonical_sha256']
        self.root = self.uuid('sheet')
        self.libraries = {}
        for library in self.record['libraries']:
            if not LIB_NAME.fullmatch(library['name']):
                raise EmitError('library name is not a KiCad nickname: ' + library['name'])
            data = captures.get(library['path'])
            if data is None:
                raise EmitError('captured library missing: ' + library['path'])
            if _sha(data) != library['sha256']:
                raise EmitError(f"captured library sha256 mismatch for {library['path']}: record "
                                f"{library['sha256']}, file {_sha(data)}")
            try:
                plain, quoted = _library(data)
            except LibraryError as exc:
                raise EmitError(f"captured library {library['path']} unreadable: {exc}") from exc
            self.libraries[library['name']] = (library, plain, quoted)
        components = self.record['components']
        power = [c['reference'] for c in components if c['power']]
        if power:
            raise EmitError('power-symbol components are unsupported in v1; declare the power net '
                            'by name instead: ' + ', '.join(sorted(power)))
        self.parts, self.ignored = _parts(parts, [c['reference'] for c in components])
        self.template = template
        self.embedded = {}
        self.sources = {}
        self.power_refs = 0
        self.flag_refs = 0
        self.net_of = {}
        for net in self.record['nets']:
            for terminal in net['terminals']:
                self.net_of[(terminal['component'], terminal['pin'])] = net
        self.no_connect = {(t['component'], t['pin']) for t in self.record['no_connects']}
        self.power_nets = {}
        for net in self.record['nets']:
            self.power_nets[net['id']] = self._net_kind(net)

    def uuid(self, identity):
        return str(uuid.uuid5(UUID_NAMESPACE, self.seed + '\x00' + identity))

    def _net_kind(self, net):
        name = net['name']
        if name.startswith('/'):
            text = name[1:]
            if not text or '/' in text or text != text.strip() or any(c in text for c in '\n\r\t{}'):
                raise EmitError(f'net {net["id"]}: local net name {name!r} is not a root-sheet label')
            return None
        if not name:
            raise EmitError(f'net {net["id"]}: unnamed nets cannot be emitted without wires')
        found = []
        for lib_name, (_library_record, plain, quoted) in sorted(self.libraries.items()):
            node = plain.get(name)
            if node is None or not children(node, 'power'):
                continue
            value = _property(node, 'Value')
            if value is None or value[2] != name:
                continue
            found.append(lib_name)
        if len(found) != 1:
            why = 'is ambiguous between ' + ', '.join(found) if found else 'has no captured power symbol'
            raise EmitError(f'net {name!r} is not a "/"-prefixed local net and {why}; '
                            'v1 emits bare net names only as captured power symbols')
        lib_name = found[0]
        symbol = self.libraries[lib_name][1][name]
        if children(symbol, 'power') and children(symbol, 'power')[0][1:] not in ([], ['global']):
            raise EmitError(f'power symbol {lib_name}:{name} is not global')
        return lib_name + ':' + name

    def _embed(self, lib_id):
        if lib_id in self.embedded:
            return self.embedded[lib_id]
        lib_name, symbol = lib_id.split(':')
        library, plain, quoted = self.libraries[lib_name]
        if symbol not in quoted:
            raise EmitError(f'{lib_id} absent from captured {library["path"]}; '
                            f'{len(quoted)} symbols available')
        node = _flatten(quoted, symbol)
        units = {int(u[1].rsplit('_', 2)[1]) for u in children(node, 'symbol')} - {0}
        if len(units) > 1:
            raise EmitError(f'{lib_id} has {len(units)} units; multi-unit symbols are unsupported in v1')
        pins = [pin for unit in children(node, 'symbol') for pin in children(unit, 'pin')]
        for pin in pins if not children(node, 'power') else ():
            if pin[1] == 'power_in' and _hidden(pin):
                raise EmitError(f'{lib_id} has hidden power_in pin {children(pin, "number")[0][1]} '
                                '(implicit global connection); unsupported')
        try:
            _pins, sources, _power = resolve_symbol(plain, symbol, library['path'], library['sha256'])
        except LibraryError as exc:
            raise EmitError(f'{lib_id}: {exc}') from exc
        node[1] = Quoted(lib_id)
        self.embedded[lib_id] = node
        self.sources[lib_id] = sources
        return node

    def _check_pins(self, comp):
        lib_name, symbol = comp['lib_id'].split(':')
        if lib_name not in self.libraries:
            raise EmitError(f"{comp['reference']}: library {lib_name} not in record")
        library, plain, _quoted = self.libraries[lib_name]
        if symbol not in plain:
            raise EmitError(f"{comp['reference']}: unknown symbol {comp['lib_id']}")
        pins, sources, _power = resolve_symbol(plain, symbol, library['path'], library['sha256'])
        real = sorted((p for p in pins if p['unit'] == 0 or p['unit'] in comp['units']),
                      key=lambda p: (p['number'], p['unit']))
        if real != comp['pins'] or sources != comp['sources']:
            listing = ', '.join(f"{p['number']} ({p['name']})" if p['name'] not in ('', '~') else p['number'] for p in real)
            raise EmitError(f"{comp['reference']}: record pins/sources disagree with captured "
                            f"{comp['lib_id']}; real pins: {listing}")
        if comp['units'] != [1]:
            raise EmitError(f"{comp['reference']}: only unit 1 is supported in v1")

    # Layout ------------------------------------------------------------------------------

    def _blocks(self):
        """Each block: items placed relative to a local origin plus a bounding box (nm)."""
        blocks = []
        order = sorted(self.record['components'], key=lambda c: _natural(c['reference']))
        for comp in order:
            self._check_pins(comp)
            node = self._embed(comp['lib_id'])
            points, attachments = [], []
            for pin in comp['pins']:
                x, y = pin['x_nm'], -pin['y_nm']
                dx, dy = DIRECTION[pin['orientation']]
                length = _pin_length(node, pin['number'])
                points += [(x, y), (x + dx * length, y + dy * length)]
                key = (comp['id'], pin['number'])
                outward = (pin['orientation'] + 180) % 360
                if key in self.no_connect:
                    attachments.append(('nc', pin, x, y, outward, None))
                    continue
                net = self.net_of[key]
                power = self.power_nets[net['id']]
                text = net['name'][1:] if power is None else net['name']
                reach = len(text) * FONT + FONT if power is None else 4 * GRID
                ox, oy = DIRECTION[outward]
                points += [(x + ox * reach + oy * FONT, y + oy * reach + ox * FONT),
                           (x + ox * reach - oy * FONT, y + oy * reach - ox * FONT)]
                attachments.append(('power' if power else 'label', pin, x, y, outward, net))
            for key in ('Reference', 'Value'):
                prop = _property(node, key)
                if prop is not None and not _hidden(prop):
                    px, py, _angle = _at(prop)
                    text = comp['reference'] if key == 'Reference' else comp['value']
                    points += [(px, -py - FONT), (px + len(text) * FONT, -py + FONT)]
            blocks.append(dict(kind='component', comp=comp, attachments=attachments,
                               box=_box(points)))
        for net in self.record['nets']:
            lib_id = self.power_nets[net['id']]
            if lib_id is None:
                continue
            types = {p['electrical_type'] for c in self.record['components'] for p in c['pins']
                     if (c['id'], p['number']) in {(t['component'], t['pin']) for t in net['terminals']}}
            if 'power_out' in types:
                continue
            flag = self._flag_symbol(net)
            blocks.append(dict(kind='flag', net=net, power=lib_id, flag=flag,
                               box=_box([(-2 * GRID, -3 * GRID), (2 * GRID, 3 * GRID)])))
        return blocks

    def _flag_symbol(self, net):
        found = sorted(lib for lib, (_r, plain, _q) in self.libraries.items()
                       if 'PWR_FLAG' in plain and children(plain['PWR_FLAG'], 'power'))
        if len(found) != 1:
            raise EmitError(f"power net {net['name']} has no power_out pin and "
                            + ('PWR_FLAG is ambiguous' if found else 'no captured PWR_FLAG symbol')
                            + '; ERC cannot see a driver')
        return found[0] + ':PWR_FLAG'

    def _layout(self, blocks):
        margin = 10 * GRID  # Clear of the drawing-sheet frame and its zone labels.
        for paper, width_mm, height_mm in PAPERS:
            width, height = width_mm * 1000000, height_mm * 1000000
            placed, x, top, row_bottom = [], margin, margin * 2, margin * 2
            row = []
            fits = True
            for block in blocks:
                (minx, miny), (maxx, maxy) = block['box']
                if row and _snap(x - minx) + maxx > width - margin:
                    top, x, row = row_bottom + 2 * GRID, margin, []
                origin_x = _snap(x - minx)
                origin_y = _snap(top - miny)
                row.append(block)
                placed.append((block, origin_x, origin_y))
                x = origin_x + maxx + 2 * GRID
                row_bottom = max(row_bottom, origin_y + maxy)
                if origin_x + maxx > width - margin or row_bottom > height - margin:
                    fits = False
                    break
            if fits:
                return paper, placed
        raise EmitError('design does not fit on an A0 sheet')

    # Nodes -------------------------------------------------------------------------------

    def _instance(self, lib_id, reference, value, x, y, rotation, pins, extra, power):
        node = self._embed(lib_id)
        ident = 'symbol:' + reference
        out = ['symbol', ['lib_id', Quoted(lib_id)], ['at', _mm(x), _mm(y), str(rotation)],
               ['unit', '1'], ['body_style', '1']]
        flags = {key: children(node, key)[0][1] if children(node, key) else 'yes'
                 for key in ('exclude_from_sim', 'in_bom', 'on_board', 'in_pos_files')}
        if power:
            flags.update(in_bom='no', on_board='no')
        out += [[key, flags[key]] for key in ('exclude_from_sim', 'in_bom', 'on_board', 'in_pos_files')]
        out += [['dnp', 'no'], ['uuid', Quoted(self.uuid(ident))]]
        fields = [('Reference', reference), ('Value', value)]
        for key in ('Footprint', 'Datasheet', 'Description'):
            prop = _property(node, key)
            fields.append((key, extra.get(key, prop[2] if prop is not None else '')))
        fields += [(key, extra[key]) for key in MANIFEST_FIELDS[1:] if key in extra]
        for key, text in fields:
            prop = _property(node, key)
            if prop is not None and key in ('Reference', 'Value', 'Footprint', 'Datasheet', 'Description'):
                px, py, angle = _at(prop)
                hidden = _hidden(prop) or key not in ('Reference', 'Value')
                rx, ry = _rotate(px, -py, rotation)
                at = ['at', _mm(x + rx), _mm(y + ry), angle]
                effects = _effects(prop)
            else:
                at, hidden, effects = ['at', _mm(x), _mm(y), '0'], True, _effects([])
            item = ['property', Quoted(key), Quoted(text), at]
            if hidden:
                item.append(['hide', 'yes'])
            item += [['show_name', 'no'], ['do_not_autoplace', 'no'], effects]
            out.append(item)
        for number in pins:
            out.append(['pin', Quoted(number), ['uuid', Quoted(self.uuid(f'pin:{reference}:{number}'))]])
        out.append(['instances', ['project', Quoted(self.name),
                                  ['path', Quoted('/' + self.root), ['reference', Quoted(reference)],
                                   ['unit', '1']]]])
        return out

    def _power_instance(self, lib_id, x, y, outward):
        self.power_refs += 1
        node = self._embed(lib_id)
        pin = [p for u in children(node, 'symbol') for p in children(u, 'pin')]
        if len(pin) != 1:
            raise EmitError(f'power symbol {lib_id} must have exactly one pin')
        px, py, angle = _at(pin[0])
        if (px, py) != (0, 0):
            raise EmitError(f'power symbol {lib_id} pin is not at its origin')
        body = int(angle) % 360  # Pin orientation points from its connection end to the body.
        rotation = (outward - body) % 360
        value = _property(node, 'Value')[2]
        return self._instance(lib_id, f'#PWR{self.power_refs:02d}', value, x, y, rotation,
                              [children(pin[0], 'number')[0][1]], {}, True)

    def build(self):
        blocks = self._blocks()
        paper, placed = self._layout(blocks)
        labels, symbols, no_connects, placements, power_records = [], [], [], [], []
        owner = {}
        for block, ox, oy in placed:
            if block['kind'] == 'flag':
                net = block['net']
                self._claim(owner, ox, oy, 'net:' + net['id'])
                symbols.append(self._power_instance(block['power'], ox, oy, 270))
                self.flag_refs += 1
                reference = f'#FLG{self.flag_refs:02d}'
                node = self._embed(block['flag'])
                pin = [p for u in children(node, 'symbol') for p in children(u, 'pin')]
                if len(pin) != 1 or _at(pin[0])[:2] != (0, 0):
                    raise EmitError(f"{block['flag']} must have one pin at its origin")
                symbols.append(self._instance(block['flag'], reference, _property(node, 'Value')[2],
                                              ox, oy, 0, [children(pin[0], 'number')[0][1]], {}, True))
                power_records.append(dict(net=net['name'], erc_driver=block['flag'], reference=reference,
                                          reason='no intent terminal on this power net is power_out; '
                                                 'the net is asserted to be driven from outside the '
                                                 'sheet (for example through a connector)'))
                continue
            comp = block['comp']
            fields = dict(self.parts[comp['reference']])
            symbols.append(self._instance(comp['lib_id'], comp['reference'], comp['value'], ox, oy, 0,
                                          [p['number'] for p in comp['pins']], fields, False))
            placements.append(dict(reference=comp['reference'], lib_id=comp['lib_id'],
                                   at_mm=[_mm(ox), _mm(oy)], rotation=0,
                                   footprint=dict(intent=comp['footprint'], emitted=fields['Footprint'])))
            for kind, pin, x, y, outward, net in block['attachments']:
                ax, ay = ox + x, oy + y
                identity = f"{comp['reference']}:{pin['number']}"
                self._claim(owner, ax, ay, 'no_connect:' + identity if kind == 'nc' else 'net:' + net['id'])
                if kind == 'nc':
                    no_connects.append(['no_connect', ['at', _mm(ax), _mm(ay)],
                                        ['uuid', Quoted(self.uuid('no_connect:' + identity))]])
                elif kind == 'label':
                    justify = ['left', 'bottom'] if outward in (0, 90) else ['right', 'bottom']
                    labels.append(['label', Quoted(net['name'][1:]), ['at', _mm(ax), _mm(ay), str(outward)],
                                   _effects([], justify), ['uuid', Quoted(self.uuid('label:' + identity))]])
                else:
                    symbols.append(self._power_instance(self.power_nets[net['id']], ax, ay, outward))
        key = lambda item: children(item, 'uuid')[0][1]
        title = ['title_block', ['title', Quoted(self.name)],
                 ['comment', '1', Quoted('Emitted by agent_ee.schematic_emit from resolved connectivity '
                                         + self.seed[:16])]]
        sch = ['kicad_sch', ['version', SCH_VERSION], ['generator', Quoted('agent_ee.schematic_emit')],
               ['generator_version', Quoted('1')], ['uuid', Quoted(self.root)], ['paper', Quoted(paper)],
               title, ['lib_symbols', *[self.embedded[k] for k in sorted(self.embedded)]],
               *sorted(no_connects, key=key), *sorted(labels, key=key), *sorted(symbols, key=key),
               ['sheet_instances', ['path', Quoted('/'), ['page', Quoted('1')]]]]
        schematic = ('\n'.join(format_sexpr(sch)) + '\n').encode('utf-8')
        sym_libs = sorted({k.split(':')[0] for k in self.embedded})
        fp_libs = sorted({f['Footprint'].split(':')[0] for f in self.parts.values()})
        sym_table = _lib_table('sym_lib_table', sym_libs, '${KICAD10_SYMBOL_DIR}/{}.kicad_sym')
        fp_table = _lib_table('fp_lib_table', fp_libs, '${KICAD10_FOOTPRINT_DIR}/{}.pretty')
        outputs = {self.name + '.kicad_sch': schematic, self.name + '.kicad_pro': self._project(),
                   'sym-lib-table': sym_table, 'fp-lib-table': fp_table}
        details = dict(paper=paper, placements=placements, power_flags=power_records,
                       symbols=[dict(lib_id=k, sources=self.sources[k]) for k in sorted(self.embedded)],
                       ignored_manifest_keys=self.ignored)
        return outputs, details

    def _claim(self, owner, x, y, net):
        """Every connection point belongs to exactly one net (no accidental contact)."""
        if owner.setdefault((x, y), net) != net:
            raise EmitError(f'layout collision at ({_mm(x)}, {_mm(y)}) mm: {owner[(x, y)]} and {net}')

    def _project(self):
        project = _json(self.template, 'project template')
        if not isinstance(project, dict):
            raise EmitError('project template is not a JSON object')
        meta = project.setdefault('meta', {})
        if not isinstance(meta, dict):
            raise EmitError('project template meta is not an object')
        meta['filename'] = self.name + '.kicad_pro'
        sheets = project.get('sheets')
        if sheets is not None:
            if not isinstance(sheets, list) or len(sheets) != 1:
                raise EmitError('project template must describe exactly one root sheet')
            project['sheets'] = [[self.root, 'Root']]
        schematic = project.get('schematic')
        if isinstance(schematic, dict) and 'top_level_sheets' in schematic:
            if not isinstance(schematic['top_level_sheets'], list) or len(schematic['top_level_sheets']) != 1:
                raise EmitError('project template must have exactly one top-level sheet')
            schematic['top_level_sheets'] = [dict(filename=self.name + '.kicad_sch', name=self.name,
                                                  uuid=self.root)]
        return (json.dumps(project, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _pin_length(node, number):
    for unit in children(node, 'symbol'):
        for pin in children(unit, 'pin'):
            if children(pin, 'number')[0][1] == number:
                length = children(pin, 'length')
                return nanometres(length[0][1]) if length else 0
    raise EmitError('pin vanished from embedded symbol: ' + number)


def _box(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    pad = GRID
    return (min(xs) - pad, min(ys) - pad), (max(xs) + pad, max(ys) + pad)


def _snap(value):
    return -((-value) // GRID) * GRID


def _natural(reference):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', reference)]


def _lib_table(kind, names, uri):
    lines = [f'({kind}', '  (version 7)']
    for name in names:
        lines.append(f'  (lib (name "{name}")(type "KiCad")(uri "{uri.replace("{}", name)}")'
                     '(options "")(descr ""))')
    return ('\n'.join(lines) + '\n)\n').encode('utf-8')


def _tool_identity():
    here = Path(__file__).resolve().parent
    return dict(module='agent_ee.schematic_emit',
                source_sha256=_sha((here / 'schematic_emit.py').read_bytes()),
                dependencies={name: _sha((here / name).read_bytes())
                              for name in ('connectivity_ir.py', 'symbol_library.py')},
                python=sys.version.split()[0], kicad_cli=None,
                kicad_cli_note='not invoked; native ERC and netlist export are the caller\'s checks')


def emit(resolved_bytes, captures, parts_bytes, template_bytes, name):
    """Pure emission: returns {filename: bytes} including emit-receipt.json, or raises EmitError."""
    record = _json(resolved_bytes, 'resolved connectivity')
    parts = _json(parts_bytes, 'parts manifest')
    outputs, details = _Emitter(record, captures, parts, template_bytes, name).build()
    receipt = dict(
        schema=RECEIPT_SCHEMA, qualification=QUALIFICATION, status='ok', name=name,
        inputs=dict(resolved=dict(sha256=_sha(resolved_bytes), canonical_sha256=record['canonical_sha256'],
                                  intent_sha256=record['intent_sha256']),
                    parts=dict(sha256=_sha(parts_bytes)),
                    project_template=dict(sha256=_sha(template_bytes)),
                    libraries=[dict(name=lib['name'], path=lib['path'], sha256=lib['sha256'])
                               for lib in sorted(record['libraries'], key=lambda x: x['name'])]),
        outputs=[dict(path=path, sha256=_sha(data)) for path, data in sorted(outputs.items())],
        tool=_tool_identity(), **details)
    outputs['emit-receipt.json'] = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8')
    return outputs


def _read_captures(record_bytes, libdir):
    try:
        record = json.loads(record_bytes)
        libraries = record['libraries'] if isinstance(record, dict) else []
    except (ValueError, KeyError, TypeError):
        return {}
    root = libdir.resolve(strict=True)
    captures = {}
    for library in libraries if isinstance(libraries, list) else []:
        relative = PurePosixPath(library.get('path', '')) if isinstance(library, dict) else None
        if relative is None or relative.is_absolute() or '..' in relative.parts or not relative.parts:
            continue
        path = root.joinpath(*relative.parts)
        if not path.is_file() or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            continue
        if path.stat().st_size > 32 * 1024 * 1024:
            raise EmitError(f'captured library {relative} exceeds 32 MiB')
        captures[str(relative)] = path.read_bytes()
    return captures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('resolved', type=Path, help='agent-ee.resolved-connectivity/1 JSON')
    parser.add_argument('--libraries', required=True, type=Path, help='directory of captured .kicad_sym files')
    parser.add_argument('--parts', required=True, type=Path, help='agent-ee.parts-manifest/1 JSON')
    parser.add_argument('--project-template', required=True, type=Path, help='template .kicad_pro')
    parser.add_argument('--name', required=True, help='project name (NAME.kicad_sch, NAME.kicad_pro)')
    parser.add_argument('--out', required=True, type=Path, help='new or empty output directory')
    args = parser.parse_args(argv)
    try:
        if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
            raise EmitError(f'output directory {args.out} exists and is not empty')
        resolved = args.resolved.read_bytes()
        outputs = emit(resolved, _read_captures(resolved, args.libraries), args.parts.read_bytes(),
                       args.project_template.read_bytes(), args.name)
    except (EmitError, OSError) as exc:
        print(json.dumps(dict(schema=RECEIPT_SCHEMA, status='refused', reason=str(exc))), file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for path, data in outputs.items():
        (args.out / path).write_bytes(data)
    print(json.dumps(dict(status='ok', receipt=str(args.out / 'emit-receipt.json'))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
