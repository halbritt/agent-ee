"""Deterministic, wire-free KiCad 10 schematic emission from captured intent.

Run ``python -m agent_ee.schematic_emitter --help`` for the standalone boundary.
The emitter resolves raw intent against captured library bytes in the same
invocation (AGENTEE-39) and never accepts an authored resolved record as pin
geometry. Every pin endpoint receives a net label or an explicit no-connect
marker; no wires are drawn. Hierarchical (connectivity-intent/2) designs get
one file per sheet occurrence: a declared port becomes one sheet pin on the
child's sheet symbol and hierarchical labels on its child segment, and every
other segment uses local labels. Its receipt is diagnostic: native ERC,
netlist export and parity checks on the captured output decide correctness.
"""
import argparse
import hashlib
import json
import re
import uuid
from decimal import Decimal
from pathlib import Path, PurePosixPath

from .connectivity_ir import _json_bytes, _read_json, canonical_resolved, resolve_intent, validate_resolved
from .symbol_context import build_context
from .symbol_library import LibraryError, children, parse_sexpr

LAYOUT_SCHEMA = 'agent-ee.schematic-layout/1'
RESULT_SCHEMA = 'agent-ee.schematic-emission/1'
HIERARCHY_SCHEMA = 'agent-ee.resolved-connectivity/2'  # AGENTEE-39 hierarchical record.
# KiCad 10.0 native schematic format, as written by eeschema/kicad-cli 10.0.7.
SCH_VERSION = '20260306'
GENERATOR = 'agent-ee-schematic-emitter'
# The importable module name, also under ``python -m`` (where __name__ is __main__).
MODULE = __spec__.name if __name__ == '__main__' and __spec__ else __name__
GENERATOR_VERSION = '10.0'
GRID = 2540000          # Symbol anchors (nm).
CONNECTION_GRID = 100  # Exact native schematic coordinate lattice (nm).
FONT = 1270000
# Landscape paper, smallest first: (name, width_nm, height_nm).
PAPERS = (('A4', 297000000, 210000000), ('A3', 420000000, 297000000),
          ('A2', 594000000, 420000000), ('A1', 841000000, 594000000),
          ('A0', 1189000000, 841000000))
BORDER = 15240000       # Frame clearance on every side.
TITLE_RESERVE = 40640000  # Bottom band kept free for the title block.
GAP = 5080000           # Clearance between reserved cells.
NAME = re.compile(r'[A-Za-z0-9_+\-.]{1,64}')
# AGENTEE-39's deterministic label for an unnamed hierarchical segment.
GENERATED = re.compile(r'__agent_ee_[0-9a-f]{64}')
SHEET_FILE = re.compile(r'[A-Za-z0-9_\-]{1,64}\.kicad_sch')
REFERENCE = re.compile(r'#?[A-Za-z_]{1,16}[0-9]{1,6}')
FOOTPRINT = re.compile(r'[A-Za-z0-9_+\-.]{1,128}:[A-Za-z0-9_+\-.,]{1,256}')
TEXT = re.compile(r'[\x20-\x7e\u00a0-\uffff]{0,256}')
BARE = re.compile(r'[^\s()";]+')
XY_WRAP = 99  # Native point-list wrap column (tabs count one), fitted to KiCad 10.0.7 output.
INSTANCE_FLAGS = (('exclude_from_sim', 'no'), ('in_bom', 'yes'), ('on_board', 'yes'),
                  ('in_pos_files', 'yes'))
# Top-level symbol nodes copied verbatim. Anything else is refused, not dropped.
SYMBOL_KEYS = frozenset(('power', 'pin_numbers', 'pin_names', 'exclude_from_sim', 'in_bom', 'on_board',
                         'in_pos_files', 'duplicate_pin_numbers_are_jumpers', 'jumper_pin_groups',
                         'property', 'symbol', 'embedded_fonts', 'body_styles'))


class EmissionError(ValueError):
    def __init__(self, code, message, location='/'):
        super().__init__(message)
        self.code, self.location = code, location


class Q(str):
    """A quoted S-expression atom; plain ``str`` atoms are written bare."""


def _scan(data):
    """Quote-aware tree for re-serialization, cross-checked against the shared reader.

    The shared ``parse_sexpr`` remains the structural authority and enforces the
    byte/depth/node limits first. This scan only records which atoms were quoted;
    any disagreement with the shared tree refuses the capture.
    """
    shared = parse_sexpr(data)
    text, pos, stack, roots = data.decode('utf-8'), 0, [], []
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
            pos, chars = pos + 1, []
            while text[pos] != '"':
                if text[pos] == '\\':
                    pos += 1
                    chars.append({'n': '\n', 'r': '\r', 't': '\t'}.get(text[pos], text[pos]))
                else:
                    chars.append(text[pos])
                pos += 1
            pos += 1
            (stack[-1] if stack else roots).append(Q(''.join(chars)))
        else:
            end = pos
            while end < len(text) and not text[end].isspace() and text[end] not in '();"':
                end += 1
            (stack[-1] if stack else roots).append(text[pos:end])
            pos = end
    if len(roots) != 1 or roots[0] != shared:
        raise LibraryError('quote-aware scan disagrees with shared reader')
    return roots[0]


def _quote(value):
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise EmissionError('malformed_text', 'control character in emitted string')
    return '"' + (value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                  .replace('\r', '\\r').replace('\t', '\\t')) + '"'


def _dump(node, depth=0, out=None):
    out = [] if out is None else out
    def atom(value):
        if isinstance(value, Q):
            return _quote(value)
        if not isinstance(value, str) or not BARE.fullmatch(value):
            raise EmissionError('malformed_text', 'invalid bare S-expression token')
        return value
    pad = '\t' * depth
    if node and node[0] == 'pts' and len(node) > 1 and all(isinstance(x, list) and x[:1] == ['xy'] for x in node[1:]):
        # KiCad packs point lists, wrapping before an item once column 99 is reached.
        out.append(pad + '(pts')
        line = '\t' * (depth + 1)
        for index, point in enumerate(node[1:]):
            text = '(' + ' '.join(atom(x) for x in point) + ')'
            if index and len(line) >= XY_WRAP:
                out.append(line)
                line = '\t' * (depth + 1)
            elif index:
                line += ' '
            line += text
        out += [line, pad + ')']
        return out
    if not any(isinstance(x, list) for x in node):
        out.append(pad + '(' + ' '.join(atom(x) for x in node) + ')')
        return out
    head = [atom(x) for x in node if not isinstance(x, list)]
    # Atoms only precede nested lists in KiCad's grammar; refuse anything else.
    if any(not isinstance(x, list) for x in node[len(head):]):
        raise EmissionError('malformed_text', 'atom after nested list')
    out.append(pad + '(' + ' '.join(head))
    for child in node[len(head):]:
        _dump(child, depth + 1, out)
    out.append(pad + ')')
    return out


def _mm(nm):
    value = (Decimal(nm) / Decimal(1000000)).normalize()
    text = format(value, 'f')
    return '0' if text in ('-0', '0') else text


def _nm(value):
    from .symbol_library import nanometres
    return nanometres(value)


# KiCad transform matrices map library (x, y-up) to sheet (x, y-down) offsets.
# Each entry is ((a, b), (c, d)): X = a*x + b*y, Y = c*x + d*y.
def _matrix(rotation, mirror):
    base = {0: ((1, 0), (0, -1)), 90: ((0, -1), (-1, 0)),
            180: ((-1, 0), (0, 1)), 270: ((0, 1), (1, 0))}[rotation]
    (a, b), (c, d) = base
    if mirror == 'x':   # Flip vertically on the sheet after rotation.
        c, d = -c, -d
    elif mirror == 'y':  # Flip horizontally on the sheet after rotation.
        a, b = -a, -b
    return (a, b), (c, d)


# KiCad's SCH_SYMBOL::GetOrientation search order; mirror x + 180 is absent
# there, so that transform is written as mirror y + 0.
ORIENTATIONS = ((0, None), (90, None), (180, None), (270, None), (0, 'x'), (90, 'x'), (270, 'x'),
                (0, 'y'), (90, 'y'), (180, 'y'), (270, 'y'))


def _canonical_orientation(matrix):
    """KiCad's own (angle, mirror) spelling of one of the eight transforms."""
    for rotation, mirror in ORIENTATIONS:
        if _matrix(rotation, mirror) == matrix:
            return rotation, mirror
    raise EmissionError('unsupported_transform', 'non-orthogonal transform')


def _apply(matrix, x, y):
    (a, b), (c, d) = matrix
    return a * x + b * y, c * x + d * y


def transformed_pin(pin, anchor, rotation=0, mirror=None):
    """Sheet endpoint and outward label angle for one resolved pin."""
    matrix = _matrix(rotation, mirror)
    dx, dy = _apply(matrix, pin['x_nm'], pin['y_nm'])
    # Library pin orientation points from the connection point into the body.
    ux, uy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[pin['orientation']]
    bx, by = _apply(matrix, ux, uy)
    # Labels point away from the body; sheet angles are counter-clockwise, y down.
    away = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[(-bx, -by)]
    return anchor[0] + dx, anchor[1] + dy, away


def _uuid(namespace, *parts):
    return str(uuid.uuid5(namespace, json.dumps(parts, separators=(',', ':'), ensure_ascii=False)))


def _validate_layout(layout, components):
    if not isinstance(layout, dict) or set(layout) - {'schema', 'namespace', 'project', 'transforms'} or \
            {'schema', 'namespace', 'project'} - set(layout):
        raise EmissionError('layout_schema', 'layout requires schema, namespace, project and optional transforms')
    if layout['schema'] != LAYOUT_SCHEMA:
        raise EmissionError('layout_schema', 'unknown layout schema')
    try:
        namespace = uuid.UUID(layout['namespace'])
        if str(namespace) != layout['namespace']:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise EmissionError('layout_schema', 'namespace must be a canonical lowercase UUID') from None
    if not isinstance(layout['project'], str) or not re.fullmatch(r'[A-Za-z0-9_\-]{1,64}', layout['project']):
        raise EmissionError('layout_schema', 'project name must be a safe file stem')
    transforms = layout.get('transforms', {})
    if not isinstance(transforms, dict):
        raise EmissionError('layout_schema', 'transforms must map logical component IDs')
    for cid, value in transforms.items():
        if cid not in components:
            raise EmissionError('layout_schema', 'transform for unknown component', '/transforms/' + cid)
        if not isinstance(value, dict) or set(value) != {'rotation', 'mirror'} or \
                type(value['rotation']) is not int or value['rotation'] not in (0, 90, 180, 270) or \
                value['mirror'] not in (None, 'x', 'y'):
            raise EmissionError('layout_schema', 'transform requires rotation 0/90/180/270 and mirror null/x/y',
                                '/transforms/' + cid)
    return namespace, layout['project'], transforms


def _library_symbol(tree, symbol, lib_id, location):
    nodes = [n for n in children(tree, 'symbol') if len(n) > 1 and n[1] == symbol]
    if len(nodes) != 1:
        raise EmissionError('unknown_symbol', 'captured symbol absent or ambiguous', location)
    node = nodes[0]
    if children(node, 'extends'):
        # KiCad flattens derived symbols into lib_symbols; that flattening is not
        # yet natively qualified here, so it is refused rather than approximated.
        raise EmissionError('derived_symbol_unsupported', 'derived (extends) symbol embedding not qualified',
                            location)
    for child in node[2:]:
        if not isinstance(child, list) or not child or child[0] not in SYMBOL_KEYS:
            raise EmissionError('unsupported_symbol_node', 'unsupported top-level symbol node', location)
    embedded = [node[0], Q(lib_id), *node[2:]]
    flags = {}
    for key, default in INSTANCE_FLAGS:
        found = children(node, key)
        if len(found) > 1 or (found and (len(found[0]) != 2 or found[0][1] not in ('yes', 'no'))):
            raise EmissionError('unsupported_symbol_node', 'ambiguous symbol flag ' + key, location)
        flags[key] = found[0][1] if found else default
    properties = []
    for prop in children(node, 'property'):
        if len(prop) < 3 or not isinstance(prop[1], str) or not isinstance(prop[2], str):
            raise EmissionError('unsupported_symbol_node', 'malformed library property', location)
        properties.append(prop)
    names = [p[1] for p in properties]
    if len(names) != len(set(names)) or not {'Reference', 'Value'} <= set(names):
        raise EmissionError('unsupported_symbol_node', 'library properties must be unique and complete', location)
    return embedded, flags, properties


def _field_text(value, code, location):
    if not isinstance(value, str) or not TEXT.fullmatch(value) or '${' in value or '\\' in value:
        raise EmissionError(code, 'unsupported characters, escaping or text variable', location)
    return value


def _property(prop, value, anchor, matrix, rotation):
    """Instance property from a library property, at transformed sheet position."""
    at = children(prop, 'at')
    fx, fy, fangle = (0, 0, 0)
    if at:
        if len(at[0]) not in (3, 4):
            raise EmissionError('unsupported_symbol_node', 'invalid property position')
        fx, fy = _nm(at[0][1]), _nm(at[0][2])
        fangle = _nm(at[0][3]) // 1000000 if len(at[0]) == 4 else 0
    dx, dy = _apply(matrix, fx, fy)
    angle = (fangle + rotation) % 180
    node = ['property', Q(prop[1]), Q(value), ['at', _mm(anchor[0] + dx), _mm(anchor[1] + dy), str(angle)]]
    hide = children(prop, 'hide')
    if hide:
        node.append(['hide', 'yes' if hide[0][1:] in ([], ['yes']) else 'no'])
    node += [['show_name', 'no'], ['do_not_autoplace', 'no']]
    effects = children(prop, 'effects')
    node.append(effects[0] if effects else ['effects', ['font', ['size', '1.27', '1.27']]])
    return node


def _natural(number):
    return (0, int(number), '') if number.isdigit() else (1, 0, number)


def _box(points):
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _plan(resolved, captured, intent_libraries, transforms):
    """Validate every component, unit, pin and net before any output is built."""
    trees, units, net_of, nc = {}, [], {}, set()
    for net in resolved['nets']:
        location = '/nets/' + net['id']
        if not isinstance(net['name'], str) or not (NAME.fullmatch(net['name']) or GENERATED.fullmatch(net['name'])):
            raise EmissionError('malformed_net_name', 'net label requires a nonempty safe name', location)
        if net['name'] in {n['name'] for n in resolved['nets'] if n['id'] != net['id']}:
            raise EmissionError('duplicate_net_name', 'distinct nets would merge by label name', location)
        for terminal in net['terminals']:
            net_of[terminal['component'], terminal['pin']] = net
    nc = {(t['component'], t['pin']) for t in resolved['no_connects']}
    libraries = {lib['name']: lib for lib in resolved['libraries']}
    symbols, global_nets = {}, set()
    for comp in resolved['components']:
        location = '/components/' + comp['id']
        library, symbol = comp['lib_id'].split(':')
        lib = libraries[library]
        if lib['path'] not in trees:
            trees[lib['path']] = _scan(captured[lib['path']])
        symbols[comp['id']] = _library_symbol(trees[lib['path']], symbol, comp['lib_id'], location)
        if not REFERENCE.fullmatch(comp['reference']) or comp['reference'].startswith('#') != comp['power']:
            raise EmissionError('malformed_reference',
                                'reference must be annotated; # prefix exactly for power symbols', location)
        _field_text(comp['value'], 'malformed_value', location)
        if comp['footprint'] and not FOOTPRINT.fullmatch(comp['footprint']):
            raise EmissionError('malformed_footprint', 'footprint must be empty or exact library:name', location)
        if comp['power']:
            if len(comp['pins']) != 1 or (comp['id'], comp['pins'][0]['number']) in nc:
                raise EmissionError('unsupported_power_symbol', 'power symbol requires one connected pin', location)
            pin = comp['pins'][0]
            net = net_of[comp['id'], pin['number']]
            if pin['electrical_type'] != 'power_in':
                continue  # A flag (power_out) names no net.
            # A power_in power symbol is a net driver named by its Value.
            if net['name'] != comp['value']:
                raise EmissionError('power_net_mismatch',
                                    'power symbol value must equal its intent net name', location)
            scope = children(symbols[comp['id']][0], 'power')
            if len(scope) != 1 or scope[0][1:] not in ([], ['global'], ['local']):
                raise EmissionError('unsupported_power_symbol', 'unqualified power symbol scope', location)
            if scope[0][1:] != ['local']:
                # Local labels beside a global power net draw a native ERC warning;
                # every terminal of this net carries a global label instead.
                global_nets.add(net['id'])
    for comp in resolved['components']:
        location = '/components/' + comp['id']
        embedded, flags, properties = symbols[comp['id']]
        context = build_context(intent_libraries, captured, comp['lib_id'])
        if context['execution'] != 'ok':
            raise EmissionError('symbol_context', context.get('message', 'symbol context unavailable'), location)
        context_pins = {(p['number'], p['unit'], p['x_nm'], p['y_nm'], p['orientation'])
                        for p in context['pins'] if p['body_style'] in (0, 1)}
        transform = transforms.get(comp['id'], {'rotation': 0, 'mirror': None})
        rotation, mirror = _canonical_orientation(_matrix(transform['rotation'], transform['mirror']))
        matrix = _matrix(rotation, mirror)
        for unit in sorted(comp['units']):
            unit_location = location + '/units/' + str(unit)
            pins = [p for p in comp['pins'] if p['unit'] in (0, unit)]
            if not pins:
                raise EmissionError('unlocatable_pin', 'selected unit has no locatable pins', unit_location)
            for pin in pins:
                if (pin['number'], pin['unit'], pin['x_nm'], pin['y_nm'], pin['orientation']) not in context_pins:
                    raise EmissionError('unlocatable_pin', 'pin geometry disagrees with captured context',
                                        unit_location)
                if pin['x_nm'] % CONNECTION_GRID or pin['y_nm'] % CONNECTION_GRID:
                    raise EmissionError('unlocatable_pin', 'pin endpoint is not representable on the native 100 nm lattice',
                                        unit_location + '/pins/' + pin['number'])
            bbox = context['bbox_by_unit'].get(str(unit))
            if not bbox or bbox['coverage'] != 'complete':
                raise EmissionError('unsupported_graphic', (bbox or {}).get('reason', 'unit bounds unavailable'),
                                    unit_location)
            x0, y0, x1, y1 = bbox['bounds_nm']
            points = [_apply(matrix, x, y) for x in (x0, x1) for y in (y0, y1)]
            labels = []
            for pin in sorted(pins, key=lambda p: p['number']):
                key = (comp['id'], pin['number'])
                x, y, away = transformed_pin(pin, (0, 0), rotation, mirror)
                if key in nc:
                    labels.append((pin, None, x, y, away))
                    points += [(x - FONT, y - FONT), (x + FONT, y + FONT)]
                    continue
                net = net_of[key]
                labels.append((pin, net, x, y, away))
                # Conservative reservation: label text plus its flag outline.
                length = (len(net['name']) + 3) * FONT
                ux, uy = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[away]
                points += [(x + ux * length - FONT * 2, y + uy * length - FONT * 2),
                           (x + ux * length + FONT * 2, y + uy * length + FONT * 2)]
            for prop in properties:
                if prop[1] in ('Reference', 'Value') and not children(prop, 'hide'):
                    at = children(prop, 'at')
                    if at:
                        px, py = _apply(matrix, _nm(at[0][1]), _nm(at[0][2]))
                        text = comp['reference'] if prop[1] == 'Reference' else comp['value']
                        width = (len(text) + 1) * FONT
                        points += [(px - width, py - 2 * FONT), (px + width, py + 2 * FONT)]
            units.append(dict(comp=comp, unit=unit, matrix=matrix, rotation=rotation, mirror=mirror,
                              # Every unit instance lists all symbol pins, as KiCad saves it.
                              pin_numbers=sorted({p[0] for p in context_pins}, key=_natural),
                              embedded=embedded, flags=flags, properties=properties,
                              box=_box(points), labels=labels))
    return units, global_nets


def _pack(units):
    """Rows left to right with wrap; smallest landscape paper that holds them."""
    for paper, width, height in PAPERS:
        usable_w, usable_h = width - 2 * BORDER, height - 2 * BORDER - TITLE_RESERVE
        x = y = row_h = 0
        placed, fits = [], True
        for item in units:
            x0, y0, x1, y1 = item['box']
            for attempt in (0, 1):
                # Snap the symbol anchor up to the 2.54 mm grid inside its cell.
                ax = -(-(BORDER + x - x0) // GRID) * GRID
                ay = -(-(BORDER + y - y0) // GRID) * GRID
                right, bottom = ax + x1 - BORDER, ay + y1 - BORDER
                if right <= usable_w or not x:
                    break
                x, y, row_h = 0, y + row_h + GAP, 0
            if right > usable_w or bottom > usable_h:
                fits = False
                break
            placed.append((item, (ax, ay)))
            x, row_h = right + GAP, max(row_h, bottom - y)
        if fits:
            return paper, placed
    raise EmissionError('paper_overflow', 'content exceeds the largest supported paper')


def _item_uuid(node):
    return children(node, 'uuid')[0][1]


FONT_EFFECTS = ['font', ['size', '1.27', '1.27']]


def _label(net, point, away, uid):
    """Local, global or hierarchical label for one net at one connection point."""
    side = 'left' if away in (0, 90) else 'right'
    at = ['at', _mm(point[0]), _mm(point[1]), str(away)]
    if net.get('port'):
        return ['hierarchical_label', Q(net['name']), ['shape', net['port']['direction']], at,
                ['effects', FONT_EFFECTS, ['justify', side]], ['uuid', Q(uid('hierarchical_label'))]]
    if net.get('global'):
        return ['global_label', Q(net['name']), ['shape', 'passive'], at, ['fields_autoplaced', 'yes'],
                ['effects', FONT_EFFECTS, ['justify', side]], ['uuid', Q(uid('global_label'))],
                ['property', Q('Intersheetrefs'), Q('${INTERSHEET_REFS}'), at, ['hide', 'yes'],
                 ['show_name', 'no'], ['do_not_autoplace', 'no'], ['effects', FONT_EFFECTS, ['justify', side]]]]
    return ['label', Q(net['name']), at, ['effects', FONT_EFFECTS, ['justify', side, 'bottom']],
            ['uuid', Q(uid('label'))]]


def _symbol(item, anchor, namespace, key, project, path, endpoints, global_nets):
    """One placed unit: its instance node, plus a label or marker on every pin endpoint.

    ``key`` is the unit's identity prefix for every derived UUID, and ``path``
    is its native sheet-occurrence path.
    """
    comp, unit = item['comp'], item['unit']
    instance = ['symbol', ['lib_id', Q(comp['lib_id'])], ['at', _mm(anchor[0]), _mm(anchor[1]), str(item['rotation'])]]
    if item['mirror']:
        instance.append(['mirror', item['mirror']])
    instance += [['unit', str(unit)], ['body_style', '1']]
    instance += [[k, item['flags'][k]] for k, _ in INSTANCE_FLAGS]
    instance += [['dnp', 'no'], ['uuid', Q(_uuid(namespace, 'symbol', *key, unit))]]
    values = {'Reference': comp['reference'], 'Value': comp['value'], 'Footprint': comp['footprint']}
    for prop in item['properties']:
        if prop[1].startswith('ki_'):
            continue
        instance.append(_property(prop, values.get(prop[1], prop[2]), anchor, item['matrix'], item['rotation']))
    instance += [['pin', Q(n), ['uuid', Q(_uuid(namespace, 'pin', *key, unit, n))]] for n in item['pin_numbers']]
    instance.append(['instances', ['project', Q(project), ['path', Q(path), ['reference', Q(comp['reference'])],
                                                           ['unit', str(unit)]]]])
    labels, markers = [], []
    for pin, net, x, y, away in item['labels']:
        point = (anchor[0] + x, anchor[1] + y)
        owner = (comp['id'], pin['number'])
        # Distinct physical pins may never share a connection point.
        if point in endpoints and endpoints[point] != owner:
            raise EmissionError('impossible_pin_endpoint', 'two pins share one connection point',
                                '/components/' + comp['id'] + '/pins/' + pin['number'])
        endpoints[point] = owner
        ident = (*key, unit, pin['number'])
        if net is None:
            markers.append(['no_connect', ['at', _mm(point[0]), _mm(point[1])],
                            ['uuid', Q(_uuid(namespace, 'no_connect', *ident))]])
        else:
            kind = dict(net, **{'global': net['id'] in global_nets})
            labels.append(_label(kind, point, away, lambda tag: _uuid(namespace, tag, *ident)))
    return instance, labels, markers


def _document(uid, paper, lib_symbols, markers, labels, symbols, sheets=(), root=False):
    # Native order: item type, then UUID (KiCad's SCH_ITEM::cmp_items).
    nodes = ['kicad_sch', ['version', SCH_VERSION], ['generator', Q(GENERATOR)],
             ['generator_version', Q(GENERATOR_VERSION)], ['uuid', Q(uid)], ['paper', Q(paper)],
             ['lib_symbols', *[lib_symbols[k] for k in sorted(lib_symbols)]],
             *sorted(markers, key=_item_uuid)]
    for kind in ('label', 'global_label', 'hierarchical_label'):
        nodes += sorted([x for x in labels if x[0] == kind], key=_item_uuid)
    nodes += sorted(symbols, key=_item_uuid) + sorted(sheets, key=_item_uuid)
    if root:
        nodes.append(['sheet_instances', ['path', Q('/'), ['page', Q('1')]]])
    return ('\n'.join(_dump(nodes)) + '\n').encode('utf-8')


def _project_file(project, root, sheets):
    project_file = dict(board=dict(design_settings=dict(defaults={}, diff_pair_dimensions=[], drc_exclusions=[],
                                                        rules={}, track_widths=[], via_dimensions=[])),
                        boards=[], libraries=dict(pinned_footprint_libs=[], pinned_symbol_libs=[]),
                        meta=dict(filename=project + '.kicad_pro', version=1),
                        net_settings=dict(classes=[], meta=dict(version=0)),
                        pcbnew=dict(page_layout_descr_file=''), sheets=sheets, text_variables={},
                        schematic=dict(top_level_sheets=[dict(filename=project + '.kicad_sch', name=project,
                                                              uuid=root)]))
    return (json.dumps(project_file, indent=2, sort_keys=True) + '\n').encode('utf-8')


def _emit(resolved, units, global_nets, namespace, project):
    root = _uuid(namespace, 'sheet', '/')
    paper, placed = _pack(units)
    endpoints, lib_symbols, symbols, labels, markers = {}, {}, [], [], []
    for item, anchor in placed:
        lib_symbols[item['comp']['lib_id']] = item['embedded']
        symbol, unit_labels, unit_markers = _symbol(item, anchor, namespace, (item['comp']['id'],), project,
                                                    '/' + root, endpoints, global_nets)
        symbols.append(symbol)
        labels += unit_labels
        markers += unit_markers
    schematic = _document(root, paper, lib_symbols, markers, labels, symbols, root=True)
    pro = _project_file(project, root, [[root, 'Root']])
    return {project + '.kicad_pro': pro, project + '.kicad_sch': schematic}, root, paper


def _sheet_symbol(child, pins, parent_file):
    """Packing item for one child sheet symbol: ports become left-edge pins in name order."""
    width = max(4 * GRID, -(-(max((len(p['name']) for p, _ in pins), default=0) + 2) * FONT // GRID) * GRID)
    height = max(2, len(pins) + 1) * GRID
    points = [(0, -2 * FONT), (max(width, (len(child['local_id']) + 1) * FONT), 0),
              (max(width, (len(child['file']) + 1) * FONT), height + 2 * FONT)]
    placed_pins = []
    for index, (port, net) in enumerate(pins):
        y = (index + 1) * GRID
        length = (len(net['name']) + 3) * FONT  # The parent's label runs left from the pin.
        points += [(-length - FONT * 2, y - FONT * 2), (FONT * 2, y + FONT * 2)]
        placed_pins.append((port, net, y))
    return dict(child=child, parent_file=parent_file, size=(width, height), pins=placed_pins, box=_box(points))


def _sheet(item, anchor, namespace, project, path, page, endpoints):
    """Native sheet symbol node, plus the parent segment's label on each sheet pin."""
    child, (width, height) = item['child'], item['size']
    x, y = anchor
    key = (item['parent_file'], child['local_id'])
    node = ['sheet', ['at', _mm(x), _mm(y)], ['size', _mm(width), _mm(height)],
            ['exclude_from_sim', 'no'], ['in_bom', 'yes'], ['on_board', 'yes'], ['dnp', 'no'],
            ['fields_autoplaced', 'yes'], ['stroke', ['width', '0.1524'], ['type', 'solid']],
            ['fill', ['color', '0', '0', '0', '0']], ['uuid', Q(_uuid(namespace, 'sheet', *key))],
            ['property', Q('Sheetname'), Q(child['local_id']), ['at', _mm(x), _mm(y - 711200), '0'],
             ['show_name', 'no'], ['do_not_autoplace', 'no'], ['effects', FONT_EFFECTS, ['justify', 'left', 'bottom']]],
            ['property', Q('Sheetfile'), Q(child['file']), ['at', _mm(x), _mm(y + height + 584200), '0'],
             ['show_name', 'no'], ['do_not_autoplace', 'no'], ['effects', FONT_EFFECTS, ['justify', 'left', 'top']]]]
    labels = []
    for port, net, offset in item['pins']:
        point = (x, y + offset)
        owner = ('sheet_pin', child['id'], port['id'])
        if point in endpoints and endpoints[point] != owner:
            raise EmissionError('impossible_pin_endpoint', 'a sheet pin shares a connection point',
                                '/ports/' + child['id'] + '/' + port['id'])
        endpoints[point] = owner
        node.append(['pin', Q(port['name']), port['direction'], ['at', _mm(point[0]), _mm(point[1]), '180'],
                     ['uuid', Q(_uuid(namespace, 'sheet_pin', *key, port['id']))],
                     ['effects', FONT_EFFECTS, ['justify', 'left']]])
        labels.append(_label(net, point, 180, lambda tag: _uuid(namespace, tag, 'sheet_pin', *key, port['id'])))
    node.append(['instances', ['project', Q(project), ['path', Q(path), ['page', Q(str(page))]]]])
    return node, labels


def _hierarchy(resolved, captured, intent_libraries, transforms, project):
    """Per-sheet plans for a resolved-connectivity/2 record, validated before any output.

    Segments come from ``local_nets`` with their AGENTEE-39 labels; the only
    connections between sheets are declared ports. A port's name is also its
    child segment's hierarchical label, so it must equal that segment's label.
    """
    sheets = {s['id']: s for s in resolved['hierarchy']}
    root = next(s for s in sheets.values() if s['parent'] is None)
    if root['file'] != project + '.kicad_sch':
        raise EmissionError('layout_schema', 'the root sheet file must be the project schematic',
                            '/hierarchy/' + root['id'])
    for sheet in sheets.values():
        location = '/hierarchy/' + sheet['id']
        if not SHEET_FILE.fullmatch(sheet['file']):
            raise EmissionError('hierarchy_file_unsupported', 'sheet files must be flat safe project files', location)
        if sheet['parent'] is not None and not NAME.fullmatch(sheet['local_id']):
            raise EmissionError('malformed_sheet_name', 'sheet name requires a safe local ID', location)
    up = {(p['child'], p['child_net']): p for p in resolved['ports']}
    views = {sid: dict(nets=[], no_connects=[], components=[], libraries=resolved['libraries'])
             for sid in sheets}
    nets = {}
    for segment in resolved['local_nets']:
        location = '/local_nets/' + segment['sheet'] + '/' + segment['id']
        port = up.get((segment['sheet'], segment['id']))
        if port is not None:
            if not NAME.fullmatch(port['name']):
                raise EmissionError('malformed_net_name', 'port name requires a safe label', location)
            if port['name'] != segment['label']:
                raise EmissionError('port_label_alias', 'a port must carry its child segment label; '
                                    'two names for one segment are not emitted', location)
        net = dict(id=segment['id'], name=segment['label'], terminals=segment['terminals'], port=port)
        views[segment['sheet']]['nets'].append(net)
        nets[segment['sheet'], segment['id']] = net
    sheet_of = {}
    for comp in resolved['components']:
        views[comp['sheet']]['components'].append(comp)
        sheet_of[comp['id']] = comp['sheet']
    for nc in resolved['no_connects']:
        views[sheet_of[nc['component']]]['no_connects'].append(nc)
    plans, kids = {}, {}
    for sid, view in views.items():
        plans[sid] = _plan(view, captured, intent_libraries, transforms)
        kids[sid] = sorted((s for s in sheets.values() if s['parent'] == sid), key=lambda s: s['local_id'])
    below = {(sheets[p['child']]['parent'], p['parent_net']) for p in resolved['ports']}
    for sid, view in views.items():
        for net in view['nets']:
            if not net['terminals'] and (sid, net['id']) not in below:
                raise EmissionError('unanchored_segment', 'segment has no pin or sheet pin to carry its label',
                                    '/local_nets/' + sid + '/' + net['id'])
    items = {}
    for sid in sheets:
        items[sid] = [_sheet_symbol(child, sorted(((p, nets[sid, p['parent_net']]) for p in resolved['ports']
                                                   if p['child'] == child['id']), key=lambda pn: pn[0]['name']),
                                    sheets[sid]['file'])
                      for child in kids[sid]]
    return root, sheets, kids, plans, items


def _emit_hierarchy(root, sheets, kids, plans, items, namespace, project):
    """One native file per sheet; occurrence paths chain ancestor sheet-symbol UUIDs."""
    root_uuid = _uuid(namespace, 'document', root['file'])
    files, order, paths = {}, [], {root['id']: '/' + root_uuid}
    pending = [root]
    while pending:  # Pages in depth-first order, children by local ID.
        sheet = pending.pop()
        order.append(sheet)
        for child in kids[sheet['id']]:
            paths[child['id']] = paths[sheet['id']] + '/' + _uuid(namespace, 'sheet', sheet['file'], child['local_id'])
        pending.extend(reversed(kids[sheet['id']]))
    pages = {sheet['id']: index + 1 for index, sheet in enumerate(order)}
    papers = {}
    for sheet in order:
        units, global_nets = plans[sheet['id']]
        paper, placed = _pack(units + items[sheet['id']])
        papers[sheet['file']] = paper
        endpoints, lib_symbols, symbols, labels, markers, nodes = {}, {}, [], [], [], []
        for item, anchor in placed:
            if 'child' in item:
                node, sheet_labels = _sheet(item, anchor, namespace, project, paths[sheet['id']],
                                            pages[item['child']['id']], endpoints)
                nodes.append(node)
                labels += sheet_labels
                continue
            lib_symbols[item['comp']['lib_id']] = item['embedded']
            key = (sheet['file'], item['comp']['local_id'])
            symbol, unit_labels, unit_markers = _symbol(item, anchor, namespace, key, project, paths[sheet['id']],
                                                        endpoints, global_nets)
            symbols.append(symbol)
            labels += unit_labels
            markers += unit_markers
        uid = root_uuid if sheet is root else _uuid(namespace, 'document', sheet['file'])
        files[sheet['file']] = _document(uid, paper, lib_symbols, markers, labels, symbols, nodes,
                                         root=sheet is root)
    listed = [[root_uuid, 'Root']] + [[paths[s['id']].rsplit('/', 1)[1], s['local_id']] for s in order[1:]]
    files[project + '.kicad_pro'] = _project_file(project, root_uuid, listed)
    return files, root_uuid, papers[root['file']]


def _table(kind, uris):
    if not isinstance(uris, dict) or not uris:
        raise EmissionError('layout_schema', kind + ' table requires a nonempty name-to-URI map')
    rows = []
    for name in sorted(uris):
        uri = uris[name]
        if not re.fullmatch(r'[A-Za-z0-9_\-.]{1,64}', name) or not isinstance(uri, str) or \
                not re.fullmatch(r'\$\{KIPRJMOD\}(/[A-Za-z0-9_\-.]{1,128}){1,8}', uri) or '/../' in uri + '/':
            raise EmissionError('layout_schema', 'library table rows need safe names and ${KIPRJMOD} URIs')
        rows.append(f'  (lib (name {_quote(name)})(type "KiCad")(uri {_quote(uri)})(options "")(descr ""))')
    head = 'sym_lib_table' if kind == 'symbol' else 'fp_lib_table'
    return ('(' + head + '\n  (version 7)\n' + '\n'.join(rows) + '\n)\n').encode('utf-8')


def emit_schematic(intent, captured_libraries, layout, *, expected_resolved=None,
                   symbol_table=None, footprint_table=None):
    """Pure emission: returns a result record and, only on success, path→bytes.

    ``expected_resolved`` (optional) must equal the fresh resolution exactly; it
    is never a source of geometry. Any refusal returns no files at all.
    """
    result = dict(schema=RESULT_SCHEMA, execution='failed', outcome='unknown', findings=[],
                  implementation=dict(module=MODULE, sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
                  intent_sha256=None, resolved_sha256=None, layout_sha256=None, libraries=[],
                  root_uuid=None, paper=None, files=[])
    def refuse(code, message, location='/', execution='failed'):
        result.update(execution=execution, outcome='unknown')
        result['findings'].append(dict(code=code, location=location, message=message))
        result['canonical_sha256'] = hashlib.sha256(_json_bytes(result)).hexdigest()
        return result, None
    try:
        layout = _read_json(layout)
        result['layout_sha256'] = hashlib.sha256(_json_bytes(layout)).hexdigest()
        raw = _read_json(intent)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        return refuse('input_schema', str(exc), execution='malformed')
    resolved = resolve_intent(raw, captured_libraries)
    result.update(intent_sha256=resolved['intent_sha256'], resolved_sha256=resolved['canonical_sha256'],
                  libraries=resolved['libraries'])
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        result['findings'].extend(dict(f, code='resolution_' + f['code']) for f in resolved['findings'])
        return refuse('resolution_rejected', 'intent did not resolve completely and cleanly',
                      execution='unsupported' if resolved['execution'] == 'unsupported' else 'failed')
    try:
        validate_resolved(resolved)
        if expected_resolved is not None and canonical_resolved(_read_json(expected_resolved)) != \
                canonical_resolved(resolved):
            raise EmissionError('resolved_mismatch', 'supplied resolved record differs from fresh resolution')
        components = {c['id']: c for c in resolved['components']}
        namespace, project, transforms = _validate_layout(layout, components)
        if resolved['schema'] == HIERARCHY_SCHEMA:
            files, root, paper = _emit_hierarchy(*_hierarchy(resolved, captured_libraries, raw['libraries'],
                                                             transforms, project), namespace, project)
        else:
            units, global_nets = _plan(resolved, captured_libraries, raw['libraries'], transforms)
            files, root, paper = _emit(resolved, units, global_nets, namespace, project)
        if symbol_table is not None:
            files['sym-lib-table'] = _table('symbol', symbol_table)
        if footprint_table is not None:
            files['fp-lib-table'] = _table('footprint', footprint_table)
    except EmissionError as exc:
        return refuse(exc.code, str(exc), exc.location)
    except (LibraryError, ValueError, TypeError, KeyError) as exc:
        return refuse('emission_input', str(exc))
    result.update(execution='ok', outcome='info', root_uuid=root, paper=paper,
                  files=[dict(path=p, sha256=hashlib.sha256(files[p]).hexdigest()) for p in sorted(files)])
    result['canonical_sha256'] = hashlib.sha256(_json_bytes(result)).hexdigest()
    return result, files


def publish(files, out):
    """Write a complete emission into a new directory; never into an existing one.

    Files are staged beside the target and renamed into place only when all are
    written, so a refusal or crash leaves no partially published project.
    """
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(str(out))
    staging = out.with_name('.' + out.name + '.partial')
    staging.mkdir(parents=False, exist_ok=False)
    try:
        for name, data in sorted(files.items()):
            if PurePosixPath(name).name != name or name.startswith('.'):
                raise EmissionError('output_path', 'emitted files are flat project files')
            with (staging / name).open('xb') as stream:
                stream.write(data)
        if out.exists() or out.is_symlink():
            raise FileExistsError(str(out))
        staging.rename(out)
    except BaseException:
        for child in staging.iterdir():
            child.unlink()
        staging.rmdir()
        raise


def main(argv=None):
    from .footprint_library import read_captures
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('intent', type=Path)
    parser.add_argument('--layout', required=True, type=Path)
    parser.add_argument('--captures', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path, help='new project directory')
    parser.add_argument('--receipt', required=True, type=Path, help='new diagnostic receipt JSON file')
    args = parser.parse_args(argv)
    intent = args.intent.read_bytes()
    try:
        paths = [lib['path'] for lib in _read_json(intent)['libraries']]
        captures = read_captures(args.captures, paths)
    except (ValueError, TypeError, KeyError, OSError):
        captures = {}  # The resolver records the missing capture as failed coverage.
    result, files = emit_schematic(intent, captures, args.layout.read_bytes())
    if files is not None:
        publish(files, args.out)
    with args.receipt.open('xb') as stream:
        stream.write(_json_bytes(result) + b'\n')
    return 0 if files is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
