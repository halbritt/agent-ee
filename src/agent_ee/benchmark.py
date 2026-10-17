"""Exact, fail-closed preparation of scratch-only synthetic benchmark candidates.

This module does not judge designs. Scored execution uses the ordinary review
and verification paths; structural preparation never calls those paths.
"""
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


def terminals(root):
    """Single-sheet embedded symbols, including unit and mirror transforms."""
    if children(root, 'sheet') or children(root, 'bus') or children(root, 'bus_entry'):
        raise ValueError('hierarchy/buses not structurally supported')
    library = {values(n)[0]: n for n in children(one(root, 'lib_symbols'), 'symbol')}
    result, powers = {}, {}
    for symbol in children(root, 'symbol'):
        props = {values(n)[0]: values(n)[1] for n in children(symbol, 'property')}
        ref = props['Reference']
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
    old, new = conductor_groups(before), conductor_groups(after)
    ref = mutation['reference'] if 'reference' in mutation else mutation['terminal']['reference']
    if mutation['operation'] == 'delete_exact_schematic_wire':
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
    else:
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
    return {'method':'independent embedded-pin/direct-conductor partitions',
            'terminals_before':len(old),'terminals_after':len(new),'passed':True}


def wire_edits(source, root, mutation):
    op = mutation['operation']
    if op == 'delete_exact_schematic_wire':
        recipes = [{'wire_uuid':mutation['wire_uuid'], 'before_xy':mutation['before_xy']}]
    elif op == 'swap_wire_pin_endpoints':
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


def mutate(case, board, source_root, alternate_root):
    """Return changed UTF-8 files and structural receipt, without writing source."""
    texts = {rel:checked_read(source_root,rel,digest)
             for rel,digest in case['source_file_sha256'].items()}
    schrel, pcbrel = board['native_files']['schematic'],board['native_files']['pcb']
    sch,pcb = parse(texts[schrel]),parse(texts[pcbrel])
    if children(sch,'sheet'):
        raise ValueError('hierarchical mutation unsupported')
    selector = case['source_selectors'][0]
    symbol = select(sch,'symbol',selector['schematic']['symbol_uuid'])
    footprint = select(pcb,'footprint',selector['pcb']['footprint_uuid'])
    if value(symbol,'lib_id') != selector['schematic']['lib_id'] or values(footprint)[0] != selector['pcb']['name']:
        raise ValueError('source identity mismatch')
    reference = selector['reference']
    props = {values(n)[0]:values(n)[1] for n in children(symbol,'property')}
    if props['Reference'] != reference or value(footprint,'path') != selector['pcb']['footprint_path']:
        raise ValueError('source reference mismatch')
    mutation = case['mutation']
    op = mutation['operation']
    se,pe = [],[]
    if op == 'replace_exact_value_properties':
        se.append(property_edit(symbol,'Value',mutation['before'],mutation['after']))
        pe.append(property_edit(footprint,'Value',mutation['before'],mutation['after']))
    elif op in ('swap_wire_pin_endpoints','delete_exact_schematic_wire'):
        se.extend(wire_edits(texts[schrel],sch,mutation))
    elif op == 'replace_embedded_physical_footprint':
        alternate = checked_read(alternate_root,Path(mutation['alternate_source']).name,mutation['alternate_sha256'])
        pe.extend(footprint_edit(texts[pcbrel],footprint,alternate,mutation))
        se.append(property_edit(symbol,'Footprint',mutation['before_name'],mutation['after_name']))
    elif op in ('remove_fuse_and_bridge','remove_shunt_esd'):
        if props['Value'] != mutation['before_value']:
            raise ValueError('stale protection value')
        se.append(span(symbol,''));pe.append(span(footprint,''))
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
    else:
        raise ValueError(f'unsupported mutation operation: {op}')
    changed = {schrel:replace_spans(texts[schrel],se),pcbrel:replace_spans(texts[pcbrel],pe)}
    receipt = {'operation':op,'source_sha256':{p:sha(t.encode()) for p,t in texts.items()},
               'generated_sha256':{p:sha(t.encode()) for p,t in changed.items()},
               'changed_files':sorted(p for p in texts if changed[p]!=texts[p]),
               'structural_only':True}
    if op in ('swap_wire_pin_endpoints','delete_exact_schematic_wire','remove_fuse_and_bridge','remove_shunt_esd'):
        receipt['wiring'] = verify_wiring(sch,parse(changed[schrel]),mutation)
    if op=='replace_embedded_physical_footprint':
        receipt['pad_geometry'] = verify_pad_geometry(footprint,select(parse(changed[pcbrel]),'footprint',selector['pcb']['footprint_uuid']),mutation)
    if op in ('remove_fuse_and_bridge','remove_shunt_esd'):
        receipt['pcb_wiring'] = verify_pcb_protection(texts[pcbrel],changed[pcbrel],reference,op)
    if not receipt['changed_files']:
        raise ValueError('mutation had no effect')
    return changed,receipt


def verify_pcb_protection(before, after, reference, operation):
    """Native connectivity construction only, never ERC/DRC or scored review."""
    import tempfile
    import pcbnew
    def groups(board):
        board.BuildConnectivity()
        connectivity = board.GetConnectivity()
        pads = [p for f in board.GetFootprints() for p in f.Pads() if p.GetNumber()]
        result = {}
        for pad in pads:
            related = {p.m_Uuid.AsString() for p in connectivity.GetConnectedItems(pad) if isinstance(p,pcbnew.PAD)}
            related.add(pad.m_Uuid.AsString())
            result[pad.m_Uuid.AsString()] = frozenset(related)
        return result,pads
    with tempfile.TemporaryDirectory(prefix='agent-ee-21-connectivity-') as directory:
        paths = [Path(directory)/'before.kicad_pcb',Path(directory)/'after.kicad_pcb']
        paths[0].write_text(before);paths[1].write_text(after)
        old_board,new_board = [pcbnew.LoadBoard(str(p)) for p in paths]
        old,pads = groups(old_board);new,_ = groups(new_board)
        removed = {p.m_Uuid.AsString() for p in pads if p.GetParentFootprint().GetReference()==reference}
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
        return {'method':'pcbnew BuildConnectivity/GetConnectedItems; saved copper, no refill/DRC',
                'native_version':pcbnew.Version(),'pads_before':len(old),'pads_after':len(new),'passed':True}


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
