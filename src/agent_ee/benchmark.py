"""Exact, fail-closed preparation of scratch-only synthetic benchmark candidates.

This module does not judge designs. Scored execution uses the ordinary review
and verification paths; structural preparation never calls those paths.
"""
import functools
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Node:
    items: list
    start: int
    end: int


def parse(source):
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+', source))
    stack, roots = [], []
    end = 0
    for token in tokens:
        if source[end:token.start()].strip():
            raise ValueError('unparsed S-expression bytes')
        end = token.end()
        text = token.group()
        if text == '(':
            node = Node([], token.start(), 0)
            (stack[-1].items if stack else roots).append(node)
            stack.append(node)
        elif text == ')':
            if not stack:
                raise ValueError('unbalanced S-expression')
            stack.pop().end = token.end()
        else:
            if not stack:
                raise ValueError('atom outside root')
            stack[-1].items.append(Node(text, token.start(), token.end()))
    if source[end:].strip() or stack or len(roots) != 1:
        raise ValueError('incomplete S-expression')
    return roots[0]


def atom(node):
    value = node.items
    if not isinstance(value, str):
        raise ValueError('expected atom')
    if value.startswith('"'):
        # KiCad strings use backslash escapes; reject unknown forms, do not
        # silently lose a slash or normalize untouched text.
        return json.loads(value)
    return value


def tag(node):
    return atom(node.items[0]) if isinstance(node.items, list) and node.items else None


def children(node, name):
    return [n for n in node.items if isinstance(n.items, list) and tag(n) == name]


def one(node, name):
    matches = children(node, name)
    if len(matches) != 1:
        raise ValueError(f'expected one {name}, got {len(matches)}')
    return matches[0]


def values(node):
    return [atom(n) for n in node.items[1:] if isinstance(n.items, str)]


def value(node, name):
    return values(one(node, name))[0]


def uid(node):
    fields = children(node, 'uuid') or children(node, 'tstamp')
    if len(fields) != 1:
        raise ValueError('missing or ambiguous UUID')
    return values(fields[0])[0]


def select(root, name, identity):
    found = [n for n in children(root, name) if uid(n) == identity]
    if len(found) != 1:
        raise ValueError(f'expected unique {name} UUID {identity}, got {len(found)}')
    return found[0]


def replace_spans(source, edits):
    edits = sorted(edits)
    previous = 0
    for start, end, replacement in edits:
        if not 0 <= start <= end <= len(source) or start < previous:
            raise ValueError('overlapping or invalid edit spans')
        previous = end
    for start, end, replacement in reversed(edits):
        source = source[:start] + replacement + source[end:]
    parse(source)
    return source


def span(node, replacement):
    return node.start, node.end, replacement


def property_edit(node, name, before, after):
    matches = [n for n in children(node, 'property') if values(n)[0] == name]
    # KiCad 8 footprint values may be fp_text rather than properties.
    if not matches and name in ('Reference', 'Value'):
        matches = [n for n in children(node, 'fp_text') if values(n)[0] == name.lower()]
    if len(matches) != 1 or atom(matches[0].items[2]) != before or before == after:
        raise ValueError(f'stale or ambiguous {name} property')
    return span(matches[0].items[2], json.dumps(after, ensure_ascii=False))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    relative = Path(relative)
    root = Path(root)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('unsafe relative path')
    if any(p.is_symlink() for p in [root,*root.parents]):
        raise ValueError('symlink in input/output root')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink in input/output path')
    return path


def checked_read(root, relative, expected):
    data = safe_path(root, relative).read_bytes()
    if sha(data) != expected:
        raise ValueError(f'source hash mismatch: {relative}')
    return data.decode('utf-8')


def xy(node):
    coordinates = tuple(float(v) for v in values(node)[:2])
    if len(coordinates) != 2 or not all(math.isfinite(v) for v in coordinates):
        raise ValueError('invalid coordinates')
    return tuple(round(v, 6) for v in coordinates)


def wire_points(wire):
    points = children(one(wire, 'pts'), 'xy')
    if len(points) != 2 or xy(points[0]) == xy(points[1]):
        raise ValueError('unsupported wire geometry')
    return tuple(xy(p) for p in points)


def on_segment(point, a, b):
    cross = (point[0]-a[0])*(b[1]-a[1]) - (point[1]-a[1])*(b[0]-a[0])
    return abs(cross) < 1e-7 and all(min(x,y)-1e-7 <= p <= max(x,y)+1e-7
                                                   for p,x,y in zip(point,a,b))


def properties(node):
    return {values(n)[0]: values(n)[1] for n in children(node, 'property')}


def reference_of(symbol, instance=None):
    """Flat symbols use their field; hierarchical ones the native instance path."""
    if instance is None:
        return properties(symbol)['Reference']
    found = {values(one(path, 'reference'))[0] for block in children(symbol, 'instances')
             for project in children(block, 'project') for path in children(project, 'path')
             if values(path)[0] == instance}
    if len(found) != 1:
        raise ValueError('symbol has no unique reference at its sheet instance')
    return found.pop()


def terminals(root, instance=None):
    """Single-sheet embedded symbols, including unit and mirror transforms."""
    if children(root, 'sheet') or children(root, 'bus') or children(root, 'bus_entry'):
        raise ValueError('hierarchy/buses not structurally supported')
    library = {values(n)[0]: n for n in children(one(root, 'lib_symbols'), 'symbol')}
    result, powers = {}, {}
    for symbol in children(root, 'symbol'):
        props = properties(symbol)
        ref = reference_of(symbol, instance)
        lib = library[value(symbol, 'lib_id')]
        if children(lib, 'extends'):
            raise ValueError('inherited symbols not structurally supported')
        unit = int(value(symbol, 'unit'))
        at = values(one(symbol, 'at'))
        angle = math.radians(float(at[2]))
        mirror = values(one(symbol, 'mirror'))[0] if children(symbol, 'mirror') else None
        for sub in children(lib, 'symbol'):
            subunit = int(values(sub)[0].rsplit('_', 2)[1])
            if subunit not in (0, unit):
                continue
            for pin in children(sub, 'pin'):
                x, y = xy(one(pin, 'at'))
                if mirror == 'x':
                    y = -y
                elif mirror == 'y':
                    x = -x
                elif mirror is not None:
                    raise ValueError('unsupported symbol mirror')
                point = (round(float(at[0])+x*math.cos(angle)-y*math.sin(angle), 6),
                         round(float(at[1])-x*math.sin(angle)-y*math.cos(angle), 6))
                key = (ref, value(pin, 'number'))
                if key in result and result[key] != point:
                    raise ValueError('ambiguous multi-unit pin')
                result[key] = point
                if children(lib, 'power'):
                    powers[key] = props['Value']
    return result, powers


def conductor_groups(root):
    pins, powers = terminals(root)
    edges = [wire_points(n) for n in children(root, 'wire')]
    labels = [(values(n)[0], xy(one(n, 'at'))) for t in ('label','global_label')
              for n in children(root, t)]
    labels += [(name, pins[key]) for key, name in powers.items()]
    points = set(pins.values()) | {p for edge in edges for p in edge}
    points |= {xy(one(n, 'at')) for n in children(root, 'junction')}
    points |= {point for _, point in labels}
    parent = {p:p for p in points}
    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    def join(a,b):
        parent[find(a)] = find(b)
    for a,b in edges:
        for p in points:
            if on_segment(p,a,b):
                join(a,p)
    names = {}
    for name, point in labels:
        if name in names:
            join(point,names[name])
        names[name] = point
    groups = {}
    for key, point in pins.items():
        groups.setdefault(find(point),set()).add(key)
    return {key:frozenset(groups[find(point)]) for key,point in pins.items()}


def verify_wiring(before, after, mutation):
    """Compare actual conductor partitions, never infer success from deletion."""
    return check_partitions(conductor_groups(before), conductor_groups(after), mutation,
                            'independent embedded-pin/direct-conductor partitions')


UNCHANGED_CONNECTIVITY = frozenset(['replace_exact_value_properties', 'replace_exact_field',
    'set_assembly_state', 'project_rule_edit', 'replace_embedded_physical_footprint',
    'pcb_track_width', 'pcb_move_footprint', 'pcb_delete_zone'])


DECLARED_DELTA_OPERATIONS = frozenset(['rename_exact_label', 'move_wire_endpoints'])


def declared_partition(old, steps):
    """Apply explicit isolate/merge/move steps to the original conductor partition."""
    parts = set(old.values())
    def owner(terminal):
        key = tuple(terminal)
        found = [g for g in parts if key in g]
        if len(found) != 1:
            raise ValueError('declared terminal absent from conductor partition')
        return key, found[0]
    if not steps:
        raise ValueError('empty declared conductor delta')
    for step in steps:
        kind = step['kind']
        if kind not in ('isolate', 'move', 'merge'):
            raise ValueError('unknown declared conductor step')
        if kind in ('isolate', 'move'):
            key, group = owner(step['terminal'])
            if len(group) <= 1:
                raise ValueError('declared isolation of an already isolated terminal')
            parts = (parts - {group}) | {group - {key}, frozenset([key])}
        if kind in ('move', 'merge'):
            (_, a), (_, b) = (owner(t) for t in (step['terminals'] if kind == 'merge' else [step['terminal'], step['to']]))
            if a == b:
                raise ValueError('declared merge of one conductor group')
            parts = (parts - {a, b}) | {a | b}
    return {key: group for group in parts for key in group}


def check_partitions(old, new, mutation, method):
    """Require exactly the operation's declared schematic conductor delta."""
    op = mutation['operation']
    if op in UNCHANGED_CONNECTIVITY:
        if new != old:
            changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
            raise ValueError(f'{op} changed schematic connectivity at {changed[:3]}')
        return {'method':method,'terminals_before':len(old),'terminals_after':len(new),'passed':True}
    if op in DECLARED_DELTA_OPERATIONS:
        if new != declared_partition(old, mutation['expected_delta']):
            changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
            raise ValueError(f'{op} conductor delta differs from its declaration at {changed[:3]}')
        return {'method':method,'terminals_before':len(old),'terminals_after':len(new),'passed':True}
    ref = mutation['reference'] if 'reference' in mutation else mutation['terminal']['reference']
    if op == 'delete_exact_schematic_wire':
        key = (ref, mutation['terminal']['pin'])
        if len(old[key]) <= 1 or new[key] != frozenset([key]):
            raise ValueError('removed wire ineffective or target still connected')
        for other in old.keys() - {key}:
            if new[other] != old[other] - {key}:
                raise ValueError('wire removal changed another conductor partition')
    elif mutation['operation'] == 'swap_wire_pin_endpoints':
        keys = [(ref,p) for p in mutation['pins']]
        a,b = keys
        if old[a] == old[b] or len(old[a]) <= 1 or len(old[b]) <= 1:
            raise ValueError('swap does not select distinct connected nets')
        rename = {a:b,b:a}
        for other in old:
            expected = frozenset(rename.get(k,k) for k in old[rename.get(other,other)])
            if new[other] != expected:
                raise ValueError(f'swap has unintended conductor delta at {other}')
    elif op in ('remove_fuse_and_bridge', 'remove_shunt_esd', 'remove_component'):
        removed = {k for k in old if k[0] == ref}
        if not removed or any(k in new for k in removed):
            raise ValueError('protection component not removed')
        merged = set()
        if mutation['operation'] == 'remove_fuse_and_bridge':
            merged = set(old[(ref,'1')] | old[(ref,'2')])
        for key in new:
            expected = (merged if key in merged else old[key]) - removed
            if new[key] != expected:
                raise ValueError(f'protection removal changed normal path at {key}')
    else:
        raise ValueError(f'no declared conductor delta for {op}')
    return {'method':method,'terminals_before':len(old),'terminals_after':len(new),'passed':True}


def wire_edits(source, root, mutation):
    op = mutation['operation']
    if op == 'delete_exact_schematic_wire':
        recipes = [{'wire_uuid':mutation['wire_uuid'], 'before_xy':mutation['before_xy']}]
    elif op in ('swap_wire_pin_endpoints', 'move_wire_endpoints'):
        recipes = mutation['wire_replacements']
    else:
        recipes = [{'wire_uuid':r['uuid'], 'before_xy':[p[1:] for p in r['pts']]}
                   for r in mutation.get('schematic_remove_dangling_shunt_wires', [])]
    edits = []
    for recipe in recipes:
        wire = select(root, 'wire', recipe['wire_uuid'])
        if wire_points(wire) != tuple(tuple(float(v) for v in p) for p in recipe['before_xy']):
            raise ValueError('stale wire coordinates')
        if 'after_xy' not in recipe:
            edits.append(span(wire,''))
        else:
            for point, coords in zip(children(one(wire,'pts'),'xy'),recipe['after_xy']):
                edits.append(span(point, f'(xy {coords[0]:.6f} {coords[1]:.6f})'))
    return edits


def pad_map(footprint):
    pads = {}
    for p in children(footprint, 'pad'):
        number = values(p)[0]
        if number:
            if number in pads:
                raise ValueError('duplicate electrical pad numbers unsupported')
            pads[number] = p
    return pads


def footprint_edit(source, footprint, alternate, mutation):
    old, new = pad_map(footprint), pad_map(parse(alternate))
    if set(old) != set(new) or '1' not in old:
        raise ValueError('alternate footprint electrical pad set mismatch')
    anchor = xy(one(old['1'],'at'))
    origin = xy(one(new['1'],'at'))
    changed = False
    replacements = []
    for number, oldpad in old.items():
        newpad = new[number]
        coords = xy(one(newpad,'at'))
        coords = tuple(round(c-o+a,6) for c,o,a in zip(coords,origin,anchor))
        changed |= coords != xy(one(oldpad,'at'))
        # Retain source UUID/net identities; substitute actual physical pad
        # geometry from the hash-pinned library, anchored at old pad 1.
        fields = []
        for field in newpad.items[1:]:
            if isinstance(field.items,list) and tag(field) in ('net','uuid','tstamp','at'):
                continue
            fields.append(alternate[field.start:field.end])
        placement = values(one(footprint,'at')) if children(footprint,'at') else ['0','0']
        rotation = float(placement[2]) if len(placement)>2 else 0
        pad_at = values(one(newpad,'at'))
        rotation += float(pad_at[2]) if len(pad_at)>2 else 0
        fields.append(f'(at {coords[0]:.6f} {coords[1]:.6f} {rotation:.6f})')
        for name in ('net','uuid','tstamp'):
            for field in children(oldpad,name):
                fields.append(source[field.start:field.end])
        replacements.append(span(oldpad,'(pad '+' '.join(fields)+')'))
    if not changed:
        raise ValueError('alternate footprint has no physical pitch delta')
    replacements.append(span(footprint.items[1],json.dumps(mutation['after_name'])))
    return replacements


def absolute_pad(footprint, pad):
    at = values(one(footprint,'at'))
    angle = math.radians(float(at[2]) if len(at)>2 else 0)
    x,y = xy(one(pad,'at'))
    return (float(at[0])+x*math.cos(angle)+y*math.sin(angle),
            float(at[1])-x*math.sin(angle)+y*math.cos(angle))


def descendants(node):
    for child in node.items:
        if isinstance(child.items,list):
            yield child
            yield from descendants(child)


WIRING_OPERATIONS = ('swap_wire_pin_endpoints', 'delete_exact_schematic_wire',
                     'remove_fuse_and_bridge', 'remove_shunt_esd', 'rename_exact_label', 'move_wire_endpoints')
# AGENTEE-108 operations. Each one records its own schematic and saved-copper
# connectivity receipt against an explicit expected delta.
FIELD_OPERATIONS = ('replace_exact_field', 'set_assembly_state', 'remove_component',
                    'pcb_track_width', 'pcb_move_footprint', 'pcb_delete_zone')
FILE_OPERATIONS = ('project_rule_edit', 'fab_file_edit')
IDENTITY_FIELDS = ('Reference', 'Value', 'Footprint')
ASSEMBLY_ATTRIBUTES = {'dnp': ('dnp', False), 'exclude_from_bom': ('in_bom', True)}
# Native footprint attribute order; any other token is refused, not dropped.
FOOTPRINT_ATTRIBUTES = ('smd', 'through_hole', 'board_only', 'exclude_from_pos_files',
                        'exclude_from_bom', 'allow_missing_courtyard', 'dnp', 'allow_soldermask_bridges')
RULE_SECTIONS = (('board', 'design_settings', 'rules'), ('net_settings',))
FLAT_METHOD = 'independent embedded-pin/direct-conductor partitions'
NATIVE_METHOD = 'sandboxed kicad-cli kicadxml netlist terminal partitions'
PCB_METHOD = 'pcbnew BuildConnectivity/GetConnectedItems; saved copper, no refill/DRC'


def sheet_instances(root):
    """Native instance paths of each child sheet file of a one-level hierarchy."""
    result = {}
    for sheet in children(root, 'sheet'):
        result.setdefault(properties(sheet)['Sheetfile'], []).append(f'/{uid(root)}/{uid(sheet)}')
    return result


def schematic_target(texts, board, selector):
    """Return the selected symbol's sheet file, parsed sheet and instance path.

    Flat sources keep the AGENTEE-21 rule. A hierarchical source must pin every
    sheet file; a sheet file shared by several instances is refused because one
    edit would change every instance.
    """
    root_rel = board['native_files']['schematic']
    root = parse(texts[root_rel])
    spec = selector['schematic']
    if not children(root, 'sheet'):
        if spec.get('file', root_rel) != root_rel or 'instance_path' in spec:
            raise ValueError('flat source has no sheet instances')
        return root_rel, root, None
    if board.get('hierarchy') != 'single-level sheet instances':
        raise ValueError('hierarchical mutation unsupported')
    instances = {**sheet_instances(root), root_rel: [f'/{uid(root)}']}
    if set(instances) - set(texts):
        raise ValueError('hierarchical case must pin every sheet file')
    sheets = {rel: root if rel == root_rel else parse(texts[rel]) for rel in instances}
    if any(children(sheet, 'sheet') for rel, sheet in sheets.items() if rel != root_rel):
        raise ValueError('nested hierarchy unsupported')
    paths = instances.get(spec['file'], [])
    if len(paths) != 1 or spec['instance_path'] != paths[0]:
        raise ValueError('edit requires the unique instance path of a single-instance sheet file')
    return spec['file'], sheets[spec['file']], paths[0]


def native_partitions(texts, root_rel):
    """(reference, pin) conductor groups from a sandboxed native netlist export."""
    return _native_partitions(tuple(sorted((rel, text) for rel, text in texts.items()
                                           if rel.endswith(('.kicad_sch', '.kicad_pro')))), root_rel)


@functools.lru_cache(maxsize=4)
def _native_partitions(items, root_rel):
    # Every case of a board shares its control; the export is a pure function
    # of these exact bytes, so one process reuses it.
    texts = dict(items)
    import tempfile
    from .executor import execute
    from .hardware_interface import read_inventory
    with tempfile.TemporaryDirectory(prefix='agent-ee-benchmark-netlist-') as directory:
        source, work = Path(directory)/'candidate', Path(directory)/'work'
        for rel, text in texts.items():
            if rel.endswith(('.kicad_sch', '.kicad_pro')):
                target = safe_path(source, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode('utf-8'))
        record = execute(source, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                        '-o', '/work/design.xml', '/candidate/'+root_rel], timeout=600)
        if record['execution'] != 'ok' or record['exit_code'] != 0 or record.get('displaced_records'):
            raise ValueError('native netlist export failed during structural preparation')
        inventory = read_inventory(work/'design.xml')
    groups = {}
    for key, code in inventory['memberships'].items():
        groups.setdefault(code, set()).add(key)
    return {key: frozenset(groups[code]) for key, code in inventory['memberships'].items()}


def schematic_connectivity(texts, changed, board, mutation, rel, instance):
    if instance is None and rel is not None:
        return verify_wiring(parse(texts[rel]), parse(changed[rel]), mutation)
    root = board['native_files']['schematic']
    return check_partitions(native_partitions(texts, root), native_partitions({**texts, **changed}, root),
                            mutation, NATIVE_METHOD)


def net_name(board, node):
    """Net of a board item; KiCad 10 names it, older boards number it."""
    net = one(node, 'net')
    fields = net.items[1:]
    if len(fields) == 2 or (len(fields) == 1 and fields[0].items.startswith('"')):
        return atom(fields[-1])
    table = {values(n)[0]: values(n)[1] for n in children(board, 'net')}
    if len(fields) != 1 or values(net)[0] not in table:
        raise ValueError('unresolved board net identity')
    return table[values(net)[0]]


def number(text):
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('non-finite board coordinate')
    return result


def millimetres(value):
    return f'{round(value, 6):.6f}'.rstrip('0').rstrip('.')


def assembly_edits(symbol, footprint, mutation):
    before, after = mutation['before'], mutation['after']
    if (not before or set(before) != set(after) or not set(before) <= set(ASSEMBLY_ATTRIBUTES)
            or before == after or any(type(v) is not bool for v in [*before.values(), *after.values()])):
        raise ValueError('assembly state edits only Boolean dnp/exclude_from_bom')
    se = []
    for name in sorted(before):
        field, inverted = ASSEMBLY_ATTRIBUTES[name]
        node = one(symbol, field)
        if values(node) not in (['yes'], ['no']) or ((values(node)[0] == 'yes') != inverted) != before[name]:
            raise ValueError(f'stale schematic {name} state')
        se.append(span(node, f'({field} {"yes" if after[name] != inverted else "no"})'))
    attr = one(footprint, 'attr')
    tokens = values(attr)
    if any(t not in FOOTPRINT_ATTRIBUTES for t in tokens) or len(set(tokens)) != len(tokens):
        raise ValueError('unsupported footprint attribute')
    if any((name in tokens) != state for name, state in before.items()):
        raise ValueError('stale footprint assembly state')
    state = {t for t in tokens if t not in before} | {n for n, v in after.items() if v}
    pe = [span(attr, '(attr ' + ' '.join(t for t in FOOTPRINT_ATTRIBUTES if t in state) + ')')]
    return se, pe


def project_rule_text(text, mutation):
    """Change one scalar design rule by exact bytes, then prove nothing else moved."""
    from .hardware_interface import unique_object
    pointer, before, after = mutation['pointer'], mutation['before'], mutation['after']
    if (not any(tuple(pointer[:len(p)]) == p and len(pointer) > len(p) for p in RULE_SECTIONS)
            or not isinstance(pointer[-1], str) or before == after
            or any(isinstance(v, (dict, list)) for v in (before, after))):
        raise ValueError('project edit must change one scalar design rule')
    data = json.loads(text, object_pairs_hook=unique_object)
    parent = data
    try:
        for key in pointer[:-1]:
            parent = parent[key]
        current = parent[pointer[-1]]
    except (KeyError, IndexError, TypeError):
        raise ValueError('project rule pointer absent') from None
    if current != before or type(current) is not type(before):
        raise ValueError('stale project rule value')
    key = json.dumps(pointer[-1])
    found = list(re.finditer(re.escape(key) + r'(\s*:\s*)' + re.escape(json.dumps(before)) + r'(?=\s*[,}\]])', text))
    if len(found) != 1:
        raise ValueError('project rule is not textually unique')
    match = found[0]
    changed = text[:match.start()] + key + match.group(1) + json.dumps(after) + text[match.end():]
    parent[pointer[-1]] = after
    if json.loads(changed) != data:
        raise ValueError('project edit changed more than the declared rule')
    return changed


def csv_fields(line):
    """Exact field spans of one CSV line; quoted fields keep their quotes."""
    body = line.rstrip('\r\n')
    spans, position = [], 0
    while True:
        quoted = re.compile(r'"(?:[^"]|"")*"').match(body, position) if body[position:position+1] == '"' else None
        end = quoted.end() if quoted else re.compile(r'[^,"]*').match(body, position).end()
        spans.append((position, end))
        if end == len(body):
            return spans
        if body[end] != ',':
            raise ValueError('irregular CSV field')
        position = end + 1


def fab_row_text(text, mutation):
    """Change one non-key cell of one uniquely keyed fab-file row."""
    import csv
    import io
    lines = text.splitlines(keepends=True)
    rows = [next(csv.reader([line])) for line in lines]
    if not lines or any(not line.strip() for line in lines) or list(csv.reader(io.StringIO(text))) != rows:
        raise ValueError('multi-line or irregular CSV unsupported')
    header, column, key = rows[0], mutation['column'], mutation['key_column']
    if column == key or header.count(column) != 1 or header.count(key) != 1:
        raise ValueError('fab edit must change one non-key column')
    ci, ki = header.index(column), header.index(key)
    matches = [i for i, row in enumerate(rows[1:], 1) if len(row) == len(header) and row[ki] == mutation['key']]
    if len(matches) != 1 or any(len(row) != len(header) for row in rows):
        raise ValueError('fab row key absent, ambiguous or irregular')
    index = matches[0]
    before, after = mutation['before'], mutation['after']
    if rows[index][ci] != before or before == after or re.search(r'[",\r\n]', after):
        raise ValueError('stale fab cell or unsupported replacement text')
    start, end = csv_fields(lines[index])[ci]
    field = lines[index][start:end]
    replacement = f'"{after}"' if field.startswith('"') else after
    lines[index] = lines[index][:start] + replacement + lines[index][end:]
    changed = ''.join(lines)
    expected = [list(row) for row in rows]
    expected[index][ci] = after
    if list(csv.reader(io.StringIO(changed))) != expected:
        raise ValueError('fab edit changed more than one cell')
    return changed, {'rows': len(rows)-1, 'cells_changed': 1, 'key': mutation['key'], 'column': column}


def file_mutation(case, board, texts):
    """Project-rule and fab-file seeds edit no schematic or board bytes."""
    mutation = case['mutation']
    op, rel = mutation['operation'], mutation['file']
    if rel not in texts:
        raise ValueError('edited file is not pinned by the case')
    extra = {}
    if op == 'project_rule_edit':
        if rel != board['native_files'].get('project'):
            raise ValueError('project rule edit must target the native project file')
        changed = {rel: project_rule_text(texts[rel], mutation)}
    else:
        if rel in board['native_files'].values() or not rel.endswith('.csv'):
            raise ValueError('fab file edit must not touch native design files')
        text, extra = fab_row_text(texts[rel], mutation)
        changed = {rel: text}
    receipt = {'operation':op,'source_sha256':{p:sha(t.encode()) for p,t in texts.items()},
               'generated_sha256':{p:sha(t.encode()) for p,t in changed.items()},
               'changed_files':sorted(p for p in texts if changed.get(p,texts[p])!=texts[p]),
               'structural_only':True}
    if receipt['changed_files'] != [rel]:
        raise ValueError('file mutation changed other files or had no effect')
    if op == 'project_rule_edit':
        receipt['schematic_connectivity'] = schematic_connectivity(texts, changed, board, mutation, None, None)
    else:
        receipt['fab_row'] = extra
    return changed, receipt


def mutate(case, board, source_root, alternate_root):
    """Return changed UTF-8 files and structural receipt, without writing source."""
    texts = {rel:checked_read(source_root,rel,digest)
             for rel,digest in case['source_file_sha256'].items()}
    mutation = case['mutation']
    op = mutation['operation']
    if op in FILE_OPERATIONS:
        return file_mutation(case, board, texts)
    pcbrel = board['native_files']['pcb']
    selector = case['source_selectors'][0]
    schrel, sch, instance = schematic_target(texts, board, selector)
    pcb = parse(texts[pcbrel])
    symbol = select(sch,'symbol',selector['schematic']['symbol_uuid'])
    footprint = select(pcb,'footprint',selector['pcb']['footprint_uuid'])
    if value(symbol,'lib_id') != selector['schematic']['lib_id'] or values(footprint)[0] != selector['pcb']['name']:
        raise ValueError('source identity mismatch')
    reference = selector['reference']
    props = properties(symbol)
    if reference_of(symbol, instance) != reference or value(footprint,'path') != selector['pcb']['footprint_path']:
        raise ValueError('source reference mismatch')
    if instance is not None and op in ('remove_fuse_and_bridge', 'remove_shunt_esd'):
        raise ValueError('protection recipes are flat-only; use remove_component')
    se,pe = [],[]
    if op == 'replace_exact_value_properties':
        se.append(property_edit(symbol,'Value',mutation['before'],mutation['after']))
        pe.append(property_edit(footprint,'Value',mutation['before'],mutation['after']))
    elif op in ('swap_wire_pin_endpoints','delete_exact_schematic_wire','move_wire_endpoints'):
        se.extend(wire_edits(texts[schrel],sch,mutation))
    elif op == 'rename_exact_label':
        if mutation['label_kind'] not in ('label', 'global_label', 'hierarchical_label'):
            raise ValueError('unsupported label kind')
        label = select(sch, mutation['label_kind'], mutation['label_uuid'])
        if values(label)[0] != mutation['before'] or mutation['before'] == mutation['after'] or not mutation['after']:
            raise ValueError('stale or empty label rename')
        se.append(span(label.items[1], json.dumps(mutation['after'], ensure_ascii=False)))
    elif op == 'replace_embedded_physical_footprint':
        alternate = checked_read(alternate_root,Path(mutation['alternate_source']).name,mutation['alternate_sha256'])
        pe.extend(footprint_edit(texts[pcbrel],footprint,alternate,mutation))
        if mutation['before_name'] != mutation['after_name']:
            se.append(property_edit(symbol,'Footprint',mutation['before_name'],mutation['after_name']))
        elif props.get('Footprint') != mutation['before_name']:
            # An earlier revision of the same library footprint keeps its name.
            raise ValueError('stale or ambiguous Footprint property')
    elif op in ('remove_fuse_and_bridge','remove_shunt_esd','remove_component'):
        if props['Value'] != mutation['before_value']:
            raise ValueError('stale protection value')
        se.append(span(symbol,''));pe.append(span(footprint,''))
        if op != 'remove_component':
            se.extend(wire_edits(texts[schrel],sch,mutation))
        if op == 'remove_fuse_and_bridge':
            a,b = mutation['schematic_bridge_xy']
            identity = hashlib.md5(case['id'].encode(),usedforsecurity=False).hexdigest()
            import uuid
            bridge_id = str(uuid.UUID(identity))
            addition = f'\n(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) (uuid "{bridge_id}"))\n'
            se.append((sch.end-1,sch.end-1,addition))
            pads = pad_map(footprint)
            start,end = absolute_pad(footprint,pads['1']),absolute_pad(footprint,pads['2'])
            net1,net2 = one(pads['1'],'net'),one(pads['2'],'net')
            if len(values(net1)) != 2 or len(values(net2)) != 2:
                raise ValueError('fuse bridge requires numeric PCB net identities')
            oldnet,newnet = values(net1)[0],values(net2)[0]
            # Every net-bearing board item must follow the merge, including
            # zone fills and vias. Definition nodes may remain unused.
            for node in descendants(pcb):
                if tag(node)=='net' and node not in children(pcb,'net') and values(node)[0]==oldnet:
                    if footprint.start <= node.start < footprint.end:
                        continue
                    pe.append(span(node,texts[pcbrel][net2.start:net2.end] if len(values(node))==2 else f'(net {newnet})'))
            addition = f'\n(segment (start {start[0]:.6f} {start[1]:.6f}) (end {end[0]:.6f} {end[1]:.6f}) (width {mutation["pcb_bridge"]["width_mm"]}) (layer "F.Cu") (net {newnet}) (uuid "{bridge_id}"))\n'
            pe.append((pcb.end-1,pcb.end-1,addition))
    elif op == 'replace_exact_field':
        library = {values(n)[0]: n for n in children(one(sch, 'lib_symbols'), 'symbol')}
        if mutation['field'] in IDENTITY_FIELDS or children(library[value(symbol, 'lib_id')], 'power'):
            raise ValueError('field edit would change component identity or a power net')
        representations = mutation['representations']
        if not representations or not set(representations) <= {'schematic', 'pcb'}:
            raise ValueError('field edit requires schematic and/or pcb representation')
        if 'schematic' in representations:
            se.append(property_edit(symbol, mutation['field'], mutation['before'], mutation['after']))
        if 'pcb' in representations:
            pe.append(property_edit(footprint, mutation['field'], mutation['before'], mutation['after']))
    elif op == 'set_assembly_state':
        se, pe = assembly_edits(symbol, footprint, mutation)
    elif op == 'pcb_track_width':
        before, after = mutation['before_width_mm'], mutation['after_width_mm']
        if not (math.isfinite(after) and after > 0) or after == before:
            raise ValueError('track width edit requires a different positive width')
        # Every segment/arc of the net at the declared width, and only those.
        segments = [n for kind in ('segment', 'arc') for n in children(pcb, kind)
                    if net_name(pcb, n) == mutation['net'] and number(values(one(n, 'width'))[0]) == before]
        if not segments or sorted(uid(n) for n in segments) != sorted(mutation['segment_uuids']):
            raise ValueError('net copper inventory differs from preregistration')
        for segment in segments:
            pe.append(span(one(segment, 'width'), f'(width {millimetres(after)})'))
    elif op == 'pcb_move_footprint':
        at = one(footprint, 'at')
        coordinates = values(at)
        dx, dy = mutation['offset_mm']
        if [number(v) for v in coordinates[:2]] != mutation['before_xy'] or not (dx or dy):
            raise ValueError('stale or empty footprint move')
        moved = [millimetres(number(coordinates[0])+dx), millimetres(number(coordinates[1])+dy), *coordinates[2:]]
        pe.append(span(at, '(at ' + ' '.join(moved) + ')'))
    elif op == 'pcb_delete_zone':
        # Native zones may omit a UUID; such a zone can never be the addressed one.
        zones = [z for z in children(pcb, 'zone') if children(z, 'uuid') or children(z, 'tstamp')]
        found = [z for z in zones if uid(z) == mutation['zone_uuid']]
        if len(found) != 1:
            raise ValueError(f'expected unique zone UUID {mutation["zone_uuid"]}, got {len(found)}')
        zone = found[0]
        if net_name(pcb, zone) != mutation['net'] or values(one(zone, 'layer')) != [mutation['layer']]:
            raise ValueError('stale zone net or layer')
        pe.append(span(zone, ''))
    else:
        raise ValueError(f'unsupported mutation operation: {op}')
    changed = {schrel:replace_spans(texts[schrel],se),pcbrel:replace_spans(texts[pcbrel],pe)}
    receipt = {'operation':op,'source_sha256':{p:sha(t.encode()) for p,t in texts.items()},
               'generated_sha256':{p:sha(t.encode()) for p,t in changed.items()},
               'changed_files':sorted(p for p in texts if changed.get(p,texts[p])!=texts[p]),
               'structural_only':True}
    if op in WIRING_OPERATIONS:
        receipt['wiring'] = schematic_connectivity(texts, changed, board, mutation, schrel, instance)
    elif op in FIELD_OPERATIONS or instance is not None:
        # A removal is judged against the selected component, never a free-text field.
        receipt['schematic_connectivity'] = schematic_connectivity(texts, changed, board, {**mutation, 'reference': reference},
                                                                   schrel, instance)
    if op=='replace_embedded_physical_footprint':
        receipt['pad_geometry'] = verify_pad_geometry(footprint,select(parse(changed[pcbrel]),'footprint',selector['pcb']['footprint_uuid']),mutation)
    if op in ('remove_fuse_and_bridge','remove_shunt_esd'):
        receipt['pcb_wiring'] = verify_pcb_protection(texts[pcbrel],changed[pcbrel],reference,op)
    if op in FIELD_OPERATIONS:
        receipt['pcb_connectivity'] = verify_pcb_connectivity(texts[pcbrel], changed[pcbrel], mutation,
                                                              reference, selector['pcb']['footprint_uuid'])
    if not receipt['changed_files']:
        raise ValueError('mutation had no effect')
    return changed,receipt


@functools.lru_cache(maxsize=2)
def pcb_groups(text):
    """Pad UUID -> saved-copper connected pad UUIDs, and pad -> owner footprint."""
    import tempfile
    import pcbnew
    with tempfile.TemporaryDirectory(prefix='agent-ee-benchmark-connectivity-') as directory:
        path = Path(directory)/'board.kicad_pcb'
        path.write_bytes(text.encode('utf-8'))
        board = pcbnew.LoadBoard(str(path))
    board.BuildConnectivity()
    connectivity = board.GetConnectivity()
    pads = [p for f in board.GetFootprints() for p in f.Pads() if p.GetNumber()]
    result, owners = {}, {}
    for pad in pads:
        key = pad.m_Uuid.AsString()
        if key in result:
            raise ValueError('ambiguous native pad UUID')
        related = {p.m_Uuid.AsString() for p in connectivity.GetConnectedItems(pad) if isinstance(p,pcbnew.PAD)}
        related.add(key)
        result[key] = frozenset(related)
        footprint = pad.GetParentFootprint()
        owners[key] = (footprint.GetReference(), footprint.m_Uuid.AsString())
    return result, owners, pcbnew.Version()


@functools.lru_cache(maxsize=4)
def copper_islands(text):
    """Copper item UUID -> (net, island identity) from native saved-copper clusters.

    An island is one native connectivity cluster: the item plus every item
    GetConnectedItems returns for it. Its identity is the hash of the sorted
    member UUIDs. An item whose cluster is not a consistent partition (for
    example a zone whose fill spans several clusters) maps to None.
    """
    import tempfile
    import pcbnew
    from .storage import identity
    with tempfile.TemporaryDirectory(prefix='agent-ee-benchmark-islands-') as directory:
        path = Path(directory)/'board.kicad_pcb'
        path.write_bytes(text.encode('utf-8'))
        board = pcbnew.LoadBoard(str(path))
    board.BuildConnectivity()
    connectivity = board.GetConnectivity()
    items = [*board.GetTracks(), *board.Zones(), *(p for f in board.GetFootprints() for p in f.Pads())]
    members, nets = {}, {}
    for item in items:
        key = item.m_Uuid.AsString()
        if key in members:
            raise ValueError('ambiguous native copper UUID')
        members[key] = frozenset({key, *(i.m_Uuid.AsString() for i in connectivity.GetConnectedItems(item))})
        nets[key] = item.GetNetname()
    result = {}
    for key, group in members.items():
        consistent = all(members.get(other) == group and nets[other] == nets[key] for other in group)
        result[key] = (nets[key], identity(sorted(group))) if consistent else None
    return result


def verify_pcb_connectivity(before, after, mutation, reference, footprint_uuid):
    """Saved copper must keep exactly the operation's declared pad partition."""
    op = mutation['operation']
    if op == 'remove_component':
        return verify_pcb_protection(before, after, reference, op)
    old, owners, version = pcb_groups(before)
    new, _, _ = pcb_groups(after)
    if set(old) != set(new):
        raise ValueError('PCB pad inventory changed')
    receipt = {'method':PCB_METHOD,'native_version':version,'pads_before':len(old),'pads_after':len(new),'passed':True}
    if op == 'pcb_move_footprint':
        moved = {pad for pad, owner in owners.items() if owner[1] == footprint_uuid}
        for key, actual in new.items():
            # The moved pads may lose copper; nothing may gain a connection,
            # and no other pad may lose anything but the moved pads.
            if not actual <= old[key] or (key not in moved and not old[key] - moved <= actual):
                raise ValueError('footprint move changed copper connectivity beyond its own pads')
        receipt['moved_pads_detached'] = sum(new[k] != old[k] for k in moved)
    elif new != old:
        raise ValueError(f'{op} changed saved copper connectivity')
    return receipt


def verify_pcb_protection(before, after, reference, operation):
    """Native connectivity construction only, never ERC/DRC or scored review."""
    old, owners, version = pcb_groups(before)
    new, _, _ = pcb_groups(after)
    removed = {key for key, owner in owners.items() if owner[0]==reference}
    if not removed or set(old)-removed != set(new):
        raise ValueError('PCB protection inventory delta is incorrect')
    merged = set()
    if operation=='remove_fuse_and_bridge':
        for key in removed:
            merged.update(old[key])
    for key,actual in new.items():
        expected = (merged if key in merged else old[key])-removed
        if actual!=expected:
            raise ValueError('PCB protection removal severed or altered normal copper continuity')
    return {'method':PCB_METHOD,'native_version':version,'pads_before':len(old),'pads_after':len(new),'passed':True}


def verify_pad_geometry(before, after, mutation):
    old,new = pad_map(before),pad_map(after)
    if set(old)!=set(new) or xy(one(old['1'],'at'))!=xy(one(new['1'],'at')):
        raise ValueError('footprint pad identity/anchor changed')
    for key in old:
        if values(one(old[key],'net'))!=values(one(new[key],'net')) or uid(old[key])!=uid(new[key]):
            raise ValueError('footprint pad net/UUID changed')
    def minimum(pads):
        coords = [xy(one(p,'at')) for p in pads.values()]
        return min(round(math.dist(a,b),6) for i,a in enumerate(coords) for b in coords[i+1:])
    original,replacement = minimum(old),minimum(new)
    required = mutation['source_geometry_verification']
    if original!=required['original_minimum_pin_spacing_mm'] or replacement!=required['replacement_minimum_pin_spacing_mm']:
        raise ValueError('generated footprint pitch disagrees with independent numerical expectation')
    return {'pad1_anchor_preserved':True,'electrical_pad_map_preserved':True,
        'original_minimum_pin_spacing_mm':original,'replacement_minimum_pin_spacing_mm':replacement}
