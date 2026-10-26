"""UUID-insensitive canonical design diff and regeneration determinism.

Two captured KiCad boards, schematics, netlists or JSON probe outputs are read
into one ID-free canonical tree: UUIDs, timestamps, net codes and tool/date
headers are dropped, geometric numbers are rounded to a declared number of
decimals, and unordered collections are keyed by reference, name or content
instead of file position. The diff lists per-path before/after entries; raw
byte differences are recorded and never graded (docs/decisions.md, "Routing
determinism is geometric equivalence"). Nothing here judges whether a
difference is electrically acceptable.
"""

from .check_registry import register as _register_check
import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys
from .storage import digest, write_json
from .symbol_library import BYTE_LIMIT, DEPTH_LIMIT, NODE_LIMIT, LibraryError, children, parse_sexpr

SCHEMA = 'agent-ee.design-diff/1'
FORMATS = {'board': 'kicad_pcb', 'schematic': 'kicad_sch', 'netlist': 'export', 'json': None}
MAX_DECIMALS = 6
UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
ID_ATOM = re.compile(rf'/?{UUID}(?:/{UUID})*/?')
NUMBER = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)')
# Identifiers and timestamps carry no design meaning.
DROPPED = frozenset(('uuid', 'tstamp', 'tstamps'))
# Tool and date headers are recorded per side but never compared.
HEADERS = {'board': ('generator', 'generator_version'), 'schematic': ('generator', 'generator_version'),
           'netlist': ('date', 'tool', 'source'), 'json': ()}
# Only coordinates, sizes and widths are rounded; text atoms such as values stay exact.
GEOMETRIC = frozenset(('at', 'xy', 'start', 'end', 'mid', 'center', 'size', 'width', 'thickness',
                       'offset', 'drill', 'rect_delta', 'radius', 'length'))
# Unordered drawing and copper primitives are keyed by their own canonical content.
COLLECTION = frozenset((
    'segment', 'arc', 'via', 'zone', 'filled_polygon', 'polygon', 'group', 'dimension', 'image',
    'gr_line', 'gr_arc', 'gr_circle', 'gr_rect', 'gr_poly', 'gr_curve', 'gr_text', 'gr_text_box',
    'fp_line', 'fp_arc', 'fp_circle', 'fp_rect', 'fp_poly', 'fp_curve', 'fp_text', 'fp_text_box',
    'wire', 'bus', 'bus_entry', 'junction', 'no_connect', 'label', 'global_label', 'hierarchical_label',
    'netclass_flag', 'polyline', 'rectangle', 'circle', 'bezier', 'text', 'text_box', 'model'))
PLACEMENT = {'board': ('footprint', ('at', 'layer'), ()),
             'schematic': ('symbol', ('at', 'mirror'), ('at',)),
             'netlist': None, 'json': None}
COUNTED = {'board': ('footprint', 'pad', 'segment', 'arc', 'via', 'zone', 'group'),
           'schematic': ('symbol', 'sheet', 'wire', 'bus', 'junction', 'no_connect', 'label',
                         'global_label', 'hierarchical_label'),
           'netlist': ('comp', 'libpart', 'net', 'node'), 'json': ()}
BOARD_FLAGS = frozenset(('exclude_from_pos_files', 'exclude_from_bom', 'dnp', 'board_only'))
SYMBOL_FLAGS = ('in_bom', 'on_board', 'in_pos_files', 'dnp', 'exclude_from_sim')
RENDER_LIMIT = 400


class Malformed(ValueError):
    """A side cannot be read as the declared format."""


class Node:
    """One canonical node: head, own atoms, children by stable key, Merkle digest."""
    __slots__ = ('head', 'atoms', 'children', 'digest')

    def __init__(self, head, atoms, kids):
        self.head, self.atoms, self.children = head, tuple(atoms), dict(sorted(kids.items()))
        payload = [head, list(self.atoms), [[k, c.digest] for k, c in self.children.items()]]
        self.digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def without(self, keys):
        return Node(self.head, self.atoms, {k: c for k, c in self.children.items() if k not in keys})


def atoms_of(node):
    return [x for x in node[1:] if isinstance(x, str)]


def lists_of(node):
    return [x for x in node[1:] if isinstance(x, list) and x and isinstance(x[0], str)]


def child_atom(node, key):
    found = children(node, key)
    return atoms_of(found[0])[0] if found and atoms_of(found[0]) else None


def property_value(node, name):
    for prop in children(node, 'property'):
        values = atoms_of(prop)
        if len(values) >= 2 and values[0] == name:
            return values[1]
    for text in children(node, 'fp_text'):
        values = atoms_of(text)
        if len(values) >= 2 and values[0] == name.lower():
            return values[1]
    return None


def label(node):
    """Stable identity of a node among its siblings, or None for positional/content keying."""
    head, values = node[0], atoms_of(node)
    if head == 'footprint' or (head == 'symbol' and children(node, 'lib_id')):
        ref = property_value(node, 'Reference')
        unit = child_atom(node, 'unit')
        return None if ref is None else ref if unit in (None, '1') else f'{ref}.{unit}'
    if head == 'sheet':
        return property_value(node, 'Sheetname')
    if head == 'comp':
        return child_atom(node, 'ref')
    if head == 'libpart':
        lib, part = child_atom(node, 'lib'), child_atom(node, 'part')
        return None if lib is None or part is None else f'{lib}:{part}'
    if head == 'node':
        ref, pin = child_atom(node, 'ref'), child_atom(node, 'pin')
        return None if ref is None or pin is None else f'{ref}.{pin}'
    if head == 'net':
        # Declarations are named; references inside pads, tracks and zones stay positional.
        if child_atom(node, 'name') is not None:
            return child_atom(node, 'name')
        return values[1] if len(values) == 2 and values[0].isdigit() else None
    if head == 'pin':
        number = child_atom(node, 'num') or child_atom(node, 'number')
        return number if number is not None else (values[0] if values else None)
    if head in ('field', 'property') and child_atom(node, 'name') is not None:
        return child_atom(node, 'name')
    if head == 'library' and child_atom(node, 'logical') is not None:
        return child_atom(node, 'logical')
    if head in ('symbol', 'property', 'pad', 'field'):
        return values[0] if values else None
    return None


def rounded(value, decimals):
    if decimals is None or not NUMBER.fullmatch(value):
        return value
    try:
        number = Decimal(value).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        return value
    out = format(number, 'f')
    if '.' in out:
        out = out.rstrip('0').rstrip('.')
    return '0' if out in ('-0', '+0') else out


def atom(value):
    return '<id>' if ID_ATOM.fullmatch(value) else value


def canonical_sexpr(raw, decimals, net_codes):
    """ID-free canonical Node for one parsed S-expression list."""
    head = raw[0]
    values = atoms_of(raw)
    if head == 'net' and net_codes:
        # Pre-KiCad-10 boards: (net CODE "NAME") declarations and (net CODE) references.
        if len(values) == 2 and values[0].isdigit():
            values = values[1:]
        elif len(values) == 1 and values[0] in net_codes:
            values = [net_codes[values[0]]]
    if head in GEOMETRIC:
        values = [rounded(v, decimals) for v in values]
    values = [atom(v) for v in values]
    grouped, order = {}, []
    for kid in lists_of(raw):
        if kid[0] in DROPPED:
            continue
        node = canonical_sexpr(kid, decimals, net_codes)
        name = label(kid)
        if name is not None:
            base, ordered = f'{kid[0]}[{atom(name)}]', False
        elif kid[0] in COLLECTION:
            base, ordered = f'{kid[0]}@{node.digest[:12]}', False
        else:
            base, ordered = kid[0], True
        if base not in grouped:
            grouped[base] = (ordered, [])
            order.append(base)
        grouped[base][1].append(node)
    keyed = {}
    for base in order:
        ordered, nodes = grouped[base]
        # Duplicate labels or contents are order-free; repeated plain heads keep file order.
        for index, node in enumerate(nodes if ordered else sorted(nodes, key=lambda n: n.digest)):
            keyed[base if index == 0 else f'{base}#{index + 1}'] = node
    return Node(head, values, keyed)


def xml_tree(element, count, depth=0):
    """Element as an S-expression-shaped list: attributes become (name value) children."""
    count[0] += 1
    if count[0] > NODE_LIMIT or depth > DEPTH_LIMIT:
        raise Malformed('XML node or nesting limit exceeded')
    out = [element.tag] + [[k, v] for k, v in sorted(element.attrib.items())]
    out.extend(xml_tree(child, count, depth + 1) for child in element)
    if element.text and element.text.strip():
        out.append(element.text.strip())
    return out


def json_node(value, decimals, depth=0):
    if depth > DEPTH_LIMIT:
        raise Malformed('JSON nesting limit exceeded')
    if isinstance(value, dict):
        return Node('{}', (), {str(k): json_node(v, decimals, depth + 1) for k, v in value.items()})
    if isinstance(value, list):
        return Node('[]', (), {f'[{i}]': json_node(v, decimals, depth + 1) for i, v in enumerate(value)})
    if isinstance(value, float) and decimals is not None:
        return Node('number', (rounded(repr(value), decimals),), {})
    if isinstance(value, str):
        return Node('string', (atom(value),), {})
    return Node(type(value).__name__, (json.dumps(value),), {})


def read_side(data, fmt, decimals):
    """Parse captured bytes into (canonical Node, headers); raises Malformed."""
    if not isinstance(data, bytes) or len(data) > BYTE_LIMIT:
        raise Malformed('input must be bytes, at most 32 MiB')
    if fmt == 'json':
        try:
            return json_node(json.loads(data.decode('utf-8')), decimals), {}
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise Malformed(f'not valid JSON: {exc}') from exc
    if fmt == 'netlist' and data.lstrip().startswith(b'<'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError as exc:
            raise Malformed('XML is not UTF-8') from exc
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise Malformed('XML declarations/entities are not supported')
        try:
            raw = xml_tree(ET.fromstring(text), [0])
        except ET.ParseError as exc:
            raise Malformed(f'not valid XML: {exc}') from exc
    else:
        try:
            raw = parse_sexpr(data)
        except LibraryError as exc:
            raise Malformed(f'not a complete S-expression: {exc}') from exc
    if raw[0] != FORMATS[fmt]:
        raise Malformed(f'expected a {FORMATS[fmt]} root, found {raw[0]!r}')
    header_root = children(raw, 'design')[0] if fmt == 'netlist' and children(raw, 'design') else raw
    headers = {key: ' '.join(atoms_of(found[0])) for key in HEADERS[fmt]
               for found in [children(header_root, key)] if found}
    stripped = [n for n in header_root[1:] if not (isinstance(n, list) and n and n[0] in HEADERS[fmt])]
    if header_root is raw:
        raw = [raw[0], *stripped]
    else:
        raw = [raw[0], *[[header_root[0], *stripped] if n is header_root else n for n in raw[1:]]]
    net_codes = {}
    if fmt == 'board':
        for net in children(raw, 'net'):
            values = atoms_of(net)
            if len(values) == 2 and values[0].isdigit():
                net_codes[values[0]] = values[1]
    if fmt == 'netlist':
        # Net codes are export-local numbering.
        raw = drop_net_codes(raw)
    try:
        return canonical_sexpr(raw, decimals, net_codes), headers
    except RecursionError as exc:
        raise Malformed('nesting too deep') from exc


def drop_net_codes(raw):
    out = [raw[0]]
    for item in raw[1:]:
        if isinstance(item, list) and item:
            if raw[0] == 'net' and item[0] == 'code':
                continue
            item = drop_net_codes(item)
        out.append(item)
    return out


def render(node):
    parts = [node.head, *(json.dumps(a, ensure_ascii=False) if (not a or ' ' in a) else a for a in node.atoms)]
    parts.extend(render(c) for c in node.children.values())
    return '(' + ' '.join(parts) + ')'


def capped(text):
    if len(text) <= RENDER_LIMIT:
        return text
    return text[:RENDER_LIMIT] + f'... (+{len(text) - RENDER_LIMIT} chars, sha256 ' \
        f'{hashlib.sha256(text.encode()).hexdigest()[:16]})'


def walk(path, left, right, entries):
    if left.digest == right.digest:
        return
    if left.head != right.head or left.atoms != right.atoms:
        entries.append({'path': path or '/', 'before': ' '.join(left.atoms), 'after': ' '.join(right.atoms)})
    for key in sorted(set(left.children) | set(right.children)):
        sub = f'{path}/{key}' if path else key
        a, b = left.children.get(key), right.children.get(key)
        if a is None:
            entries.append({'path': sub, 'before': None, 'after': capped(render(b))})
        elif b is None:
            entries.append({'path': sub, 'before': capped(render(a)), 'after': None})
        else:
            walk(sub, a, b, entries)


def split_placement(node, fmt):
    """(identity node without placement fields, placement digest input) for one format."""
    spec = PLACEMENT[fmt]
    if spec is None:
        return node, None
    head, fields, property_fields = spec
    kept, placement = {}, {}
    for key, child in node.children.items():
        if child.head != head or not key.startswith(f'{head}['):
            kept[key] = child
            continue
        moved = {f: child.children[f].digest for f in fields if f in child.children}
        trimmed = child.without(fields)
        if property_fields:
            props = {}
            for pkey, prop in trimmed.children.items():
                if prop.head == 'property':
                    moved.update({f'{pkey}/{f}': prop.children[f].digest for f in property_fields
                                  if f in prop.children})
                    prop = prop.without(property_fields)
                props[pkey] = prop
            trimmed = Node(trimmed.head, trimmed.atoms, props)
        placement[key] = moved
        kept[key] = trimmed
    return Node(node.head, node.atoms, kept), placement


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def board_facts(node):
    footprints, nets, transforms, flags = [], set(), {}, {}

    def collect(n):
        if n.head == 'net' and n.atoms and n.atoms[0]:
            nets.add(n.atoms[0])
        for c in n.children.values():
            collect(c)
    collect(node)
    for key, fp in node.children.items():
        if fp.head != 'footprint':
            continue
        pads = sorted((c.atoms[0] if c.atoms else '', next((n.atoms[0] for n in c.children.values()
                       if n.head == 'net' and n.atoms), '')) for c in fp.children.values() if c.head == 'pad')
        value = fp.children.get('property[Value]')
        footprints.append([key, list(fp.atoms[:1]), list(value.atoms[1:2]) if value else [], pads])
        transforms[key] = {f: ' '.join(fp.children[f].atoms) for f in ('at', 'layer') if f in fp.children}
        attr = fp.children.get('attr')
        flags[key] = sorted(BOARD_FLAGS & set(attr.atoms)) if attr else []
    layers = node.children.get('layers')
    structural = {'footprints': sorted(footprints), 'nets': sorted(nets),
                  'layers': render(layers) if layers else None}
    return structural, sorted(nets), transforms, flags


def schematic_facts(node):
    symbols, nets, transforms, flags = [], set(), {}, {}
    for key, child in node.children.items():
        if child.head in ('label', 'global_label', 'hierarchical_label') and child.atoms:
            nets.add(child.atoms[0])
        if child.head != 'symbol' or not key.startswith('symbol['):
            continue
        props = {k[len('property['):-1]: c.atoms[1] for k, c in child.children.items()
                 if c.head == 'property' and len(c.atoms) >= 2}
        lib = ' '.join(child.children['lib_id'].atoms) if 'lib_id' in child.children else ''
        if lib.startswith('power:') and props.get('Value'):
            nets.add(props['Value'])
        symbols.append([key, lib, props.get('Value', ''), props.get('Footprint', '')])
        transforms[key] = {f: ' '.join(child.children[f].atoms) for f in ('at', 'mirror') if f in child.children}
        flags[key] = {f: ' '.join(child.children[f].atoms) for f in SYMBOL_FLAGS if f in child.children}
    sheets = sorted(k for k, c in node.children.items() if c.head == 'sheet')
    structural = {'symbols': sorted(symbols), 'nets': sorted(nets), 'sheets': sheets}
    return structural, sorted(nets), transforms, flags


def netlist_facts(node):
    groups = {c.head: c for c in node.children.values()}
    comps = groups.get('components')
    nets_node = groups.get('nets')
    components = sorted([k, [' '.join(c.children[f].atoms) for f in ('value', 'footprint') if f in c.children]]
                        for k, c in (comps.children.items() if comps else ()) if c.head == 'comp')
    nets = sorted([k, sorted(n for n in c.children if n.startswith('node['))]
                  for k, c in (nets_node.children.items() if nets_node else ()) if c.head == 'net')
    names = sorted(k[len('net['):-1] for k, _ in nets if k.startswith('net['))
    return {'components': components, 'nets': nets}, names, {}, {}


FACTS = {'board': board_facts, 'schematic': schematic_facts, 'netlist': netlist_facts,
         'json': lambda node: (None, [], {}, {})}


def counts(node, kinds):
    found = Counter()

    def visit(n):
        if n.head in kinds:
            found[n.head] += 1
        for c in n.children.values():
            visit(c)
    visit(node)
    return {k: found[k] for k in kinds}


def summarize(node, fmt):
    identity_node, placement = split_placement(node, fmt)
    structural, nets, transforms, flags = FACTS[fmt](node)
    hashes = {'identity': identity_node.digest,
              'structural': sha(structural) if structural is not None else None,
              'placement': sha(placement) if placement is not None else None}
    return {'hashes': hashes, 'nets': nets, 'transforms': transforms, 'flags': flags,
            'counts': counts(node, COUNTED[fmt])}


def changed(before, after):
    return {k: {'before': before.get(k), 'after': after.get(k)}
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}


def raw_difference(left, right):
    a, b = Counter(left.splitlines()), Counter(right.splitlines())
    return {'identical': left == right, 'reference_bytes': len(left), 'subject_bytes': len(right),
            'reference_sha256': hashlib.sha256(left).hexdigest(),
            'subject_sha256': hashlib.sha256(right).hexdigest(),
            'lines_only_in_reference': sum((a - b).values()), 'lines_only_in_subject': sum((b - a).values())}


def compare(reference, subject, fmt, decimals=None):
    """Canonical diff of two captured byte strings; raises Malformed naming the side."""
    if fmt not in FORMATS:
        raise ValueError(f'unsupported format {fmt!r}')
    sides = {}
    for name, data in (('reference', reference), ('subject', subject)):
        try:
            sides[name] = read_side(data, fmt, decimals)
        except Malformed as exc:
            raise Malformed(f'{name}: {exc}') from exc
    (left, left_headers), (right, right_headers) = sides['reference'], sides['subject']
    entries = []
    walk('', left, right, entries)
    a, b = summarize(left, fmt), summarize(right, fmt)
    removed, added = sorted(set(a['nets']) - set(b['nets'])), sorted(set(b['nets']) - set(a['nets']))
    return {
        'schema': SCHEMA, 'format': fmt, 'decimals': decimals,
        'equivalent': not entries, 'entries': entries,
        'hashes': {'reference': a['hashes'], 'subject': b['hashes'],
                   'changed': sorted(k for k in a['hashes'] if a['hashes'][k] != b['hashes'][k])},
        'typed': {
            'counts': {k: {'before': a['counts'][k], 'after': b['counts'][k]}
                       for k in a['counts'] if a['counts'][k] != b['counts'][k]},
            'net_names': {'removed': removed, 'added': added},
            'transforms': changed(a['transforms'], b['transforms']),
            'assembly_exclusions': changed(a['flags'], b['flags']),
        },
        'headers': {'reference': left_headers, 'subject': right_headers},
        'raw': raw_difference(reference, subject),
    }


# ------------------------------------------------------------------ check kind

def side_spec(value, label_):
    if not isinstance(value, dict) or set(value) not in ({'path'}, {'check', 'artifact'}):
        raise ValueError(f'{label_} must be {{"path"}} or {{"check", "artifact"}}')
    for key, item in value.items():
        if not isinstance(item, str) or not item:
            raise ValueError(f'{label_}.{key} must be nonempty text')
    if 'path' in value and str(safe_relative(value['path'])) != value['path']:
        raise ValueError(f'{label_}.path must be a normalized relative path')
    if 'artifact' in value and (str(safe_relative(value['artifact'])) != value['artifact']):
        raise ValueError(f'{label_}.artifact must be a normalized relative path')
    return value


def parameters(params):
    if not isinstance(params, dict) or not {'format', 'reference', 'subject'} <= set(params) or \
            set(params) - {'format', 'reference', 'subject', 'decimals'}:
        raise ValueError('expected fields: format reference subject, and decimals for geometric formats')
    fmt = params['format']
    if fmt not in FORMATS:
        raise ValueError(f'format must be one of {sorted(FORMATS)}')
    decimals = params.get('decimals')
    if fmt in ('board', 'schematic') and 'decimals' not in params:
        raise ValueError(f'{fmt} comparison needs a declared decimals precision')
    if fmt == 'netlist' and 'decimals' in params:
        raise ValueError('netlist identity is exact; decimals does not apply')
    if 'decimals' in params and (isinstance(decimals, bool) or not isinstance(decimals, int)
                                 or not 0 <= decimals <= MAX_DECIMALS):
        raise ValueError(f'decimals must be an integer from 0 to {MAX_DECIMALS}')
    reference, subject = side_spec(params['reference'], 'reference'), side_spec(params['subject'], 'subject')
    if reference == subject:
        raise ValueError('reference and subject must be different inputs')
    return fmt, decimals, reference, subject


def capability(params, scope, files):
    """Immutable reference path, mutable subject path and earlier-check producers."""
    fmt, _, reference, subject = parameters(params)
    if reference.get('path') in (scope.get('board'), scope.get('schematic')):
        raise ValueError('the reference is acceptance input; it cannot be the writer-mutable design file')
    immutable, mutable, producers = [], [], []
    for side, bucket in ((reference, immutable), (subject, mutable)):
        if 'path' in side:
            if side['path'] not in files:
                raise ValueError(f"input not captured: {side['path']}")
            bucket.append(side['path'])
        else:
            producers.append(side['check'])
    return {'immutable_inputs': immutable, 'mutable_inputs': mutable, 'producers': producers,
            'predicates': [f'design-diff:{fmt}:equivalent']}


def side_bytes(ctx, side):
    """Captured bytes for one side; CheckError when a producer or its output is absent."""
    from .checks import CheckError, SAFE_ID
    if 'path' in side:
        path = ctx.inputs / side['path']
        if not path.is_file():
            raise CheckError('malformed', f"captured input is missing {side['path']!r}")
        return path.read_bytes(), side['path']
    cid = side['check']
    specs = ctx.scope['checks']
    if not SAFE_ID.fullmatch(cid) or not any(s.get('id') == cid for s in specs[:specs.index(ctx.spec)]):
        raise CheckError('unsupported', f'producer check {cid!r} must precede this check')
    record = ctx.run_dir / 'checks' / f'{cid}.json'
    if not record.is_file():
        raise CheckError('not_run', f'producer check {cid!r} has no result')
    result = json.loads(record.read_text())
    rel = f"work/{cid}/{side['artifact']}"
    # A failed producer or missing output is unknown coverage, never "no change".
    if result.get('candidate_id') != ctx.manifest['candidate_id'] or result.get('execution') != 'ok':
        raise CheckError('not_run', f'producer check {cid!r} did not run cleanly; output unverified')
    if rel not in result.get('evidence', []) or not (ctx.run_dir / rel).is_file():
        raise CheckError('not_run', f"producer check {cid!r} left no {side['artifact']}")
    if rel not in ctx.result['evidence']:
        ctx.result['evidence'].append(rel)
    return (ctx.run_dir / rel).read_bytes(), rel


def check_regeneration_determinism(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.scope, ctx.manifest['files'])
        fmt, decimals, reference, subject = parameters(ctx.params)
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    left, left_name = side_bytes(ctx, reference)
    right, right_name = side_bytes(ctx, subject)
    try:
        report = compare(left, right, fmt, decimals)
    except Malformed as exc:
        raise CheckError('malformed', str(exc)) from exc
    report.update(candidate_id=ctx.manifest['candidate_id'], reference=left_name, subject=right_name)
    write_json(ctx.work / 'design_diff.json', report)
    ctx.evidence('design_diff.json')
    details = {k: report[k] for k in ('format', 'decimals', 'equivalent', 'hashes', 'typed', 'headers', 'raw',
                                     'reference', 'subject')}
    details.update(entry_count=len(report['entries']), entries=report['entries'][:50])
    ctx.result['details'].update(details)
    bytes_note = 'identical bytes' if report['raw']['identical'] else 'byte differences recorded, not graded'
    ctx.result.update(execution='ok', outcome='pass' if report['equivalent'] else 'defect',
        summary=f"Regeneration determinism ({fmt}): "
                f"{'equivalent' if report['equivalent'] else str(len(report['entries'])) + ' canonical difference(s)'}"
                f"; {bytes_note}")
    ctx.result['limitations'].append(
        'UUIDs, timestamps, net codes and tool/date headers are ignored; geometric numbers are compared '
        'after rounding to the declared decimals. Differences are not judged for electrical acceptability.')


# ------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.design_diff',
                                     description='ID-free canonical diff of two captured design files.')
    parser.add_argument('reference', type=Path)
    parser.add_argument('subject', type=Path)
    parser.add_argument('--format', required=True, choices=sorted(FORMATS))
    parser.add_argument('--decimals', type=int)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    params = {'format': args.format, 'reference': {'path': 'reference'}, 'subject': {'path': 'subject'}}
    if args.decimals is not None:
        params['decimals'] = args.decimals
    try:
        parameters(params)
        report = compare(args.reference.read_bytes(), args.subject.read_bytes(), args.format, args.decimals)
    except (ValueError, OSError) as exc:
        print(f'design_diff: {exc}', file=sys.stderr)
        return 2
    report.update(reference=str(args.reference), subject=str(args.subject),
                  reference_sha256=digest(args.reference), subject_sha256=digest(args.subject))
    if args.out:
        write_json(args.out, report)
    else:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
    return 0 if report['equivalent'] else 1


if __name__ == '__main__':
    sys.exit(main())


def describe_regeneration_determinism(ctx):
    from .capabilities import KINDS
    descriptor = capability(ctx.params, ctx.scope, ctx.files)
    for path in descriptor['mutable_inputs']:
        ctx.captured(path)
    for path in descriptor['immutable_inputs']:
        ctx.captured(path, immutable=True)
    for cid in descriptor['producers']:
        ctx.prerequisite(cid, KINDS)
    ctx.result['predicates'] = descriptor['predicates']


if __name__ != '__main__':
    _register_check('regeneration_determinism', describe_regeneration_determinism, check_regeneration_determinism)
