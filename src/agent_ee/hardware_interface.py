"""Captured owner declarations over direct native schematic conductors."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, read_json, write_json


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('expected nonempty text')
    return value


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError(f'expected fields: {keys}')


def strings(values):
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError('expected nonempty text list')
    if len(set(values)) != len(values):
        raise ValueError('duplicate text identity')


def validate_contract(contract):
    exact_keys(contract, 'schema components terminals allowed_edits invariants')
    if contract['schema'] != 'agent-ee.interface/1':
        raise ValueError('unsupported interface schema')
    components, terminals = contract['components'], contract['terminals']
    if not isinstance(components, dict) or not components or not isinstance(terminals, dict):
        raise ValueError('expected component and terminal mappings')
    locations = set()
    for cid, component in components.items():
        text(cid)
        exact_keys(component, 'locations symbols')
        strings(component['symbols'])
        if not isinstance(component['locations'], list) or not component['locations']:
            raise ValueError('component requires explicit native locations')
        for location in component['locations']:
            exact_keys(location, 'sheet reference')
            sheet, ref = text(location['sheet']), text(location['reference'])
            if not sheet.startswith('/') or not sheet.endswith('/') or (sheet, ref) in locations:
                raise ValueError('invalid or ambiguous native location')
            locations.add((sheet, ref))
    terminal_keys = set()
    for tid, terminal in terminals.items():
        text(tid)
        exact_keys(terminal, 'component pin')
        if terminal['component'] not in components:
            raise ValueError('unknown terminal component')
        text(terminal['pin'])
        key = (terminal['component'], terminal['pin'])
        if key in terminal_keys:
            raise ValueError('ambiguous declared terminal identity')
        terminal_keys.add(key)
    edits = contract['allowed_edits']
    if not isinstance(edits, list):
        raise ValueError('allowed_edits must be explicit list')
    edited = set()
    for edit in edits:
        exact_keys(edit, 'component property values')
        if edit['component'] not in components or edit['property'] != 'value' or edit['component'] in edited:
            raise ValueError('only unique declared component value edits supported')
        strings(edit['values'])
        edited.add(edit['component'])
    invariants = contract['invariants']
    if not isinstance(invariants, list) or not invariants:
        raise ValueError('expected nonempty invariants')
    ids, protected_values = set(), set()
    for invariant in invariants:
        kind = invariant.get('kind') if isinstance(invariant, dict) else None
        fields = {'component': 'component populated', 'value': 'component equals',
                  'same_net': 'terminals', 'different_net': 'terminals', 'named_net': 'terminal names'}
        if kind not in fields:
            raise ValueError('unknown invariant kind')
        exact_keys(invariant, 'id requirement source kind ' + fields[kind])
        iid = text(invariant['id'])
        if iid in ids:
            raise ValueError('duplicate invariant id')
        ids.add(iid)
        text(invariant['requirement'])
        exact_keys(invariant['source'], 'path locator')
        safe_relative(invariant['source']['path'])
        text(invariant['source']['locator'])
        if kind in ('component', 'value'):
            if invariant['component'] not in components:
                raise ValueError('unknown invariant component')
            if kind == 'component' and type(invariant['populated']) is not bool:
                raise ValueError('populated must be Boolean')
            if kind == 'value':
                text(invariant['equals'])
                protected_values.add(invariant['component'])
        elif kind == 'named_net':
            if invariant['terminal'] not in terminals:
                raise ValueError('unknown terminal')
            strings(invariant['names'])
        else:
            strings(invariant['terminals'])
            if len(invariant['terminals']) != 2 or any(t not in terminals for t in invariant['terminals']):
                raise ValueError('relation requires two declared terminals')
    if not edited <= protected_values:
        raise ValueError('allowed value edit requires a protected value predicate')
    return contract


def unique_object(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f'duplicate JSON field: {key}')
        fields[key] = value
    return fields


def load_contract(inputs, path):
    raw = (Path(inputs) / str(safe_relative(path))).read_text()
    return validate_contract(json.loads(raw, object_pairs_hook=unique_object))


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'contract netlist_check')
    rel = str(safe_relative(params['contract']))
    if rel not in files or inputs is None:
        raise ValueError('interface contract must be captured')
    contract = load_contract(inputs, rel)
    requirements = {r['id']: r for r in scope.get('baseline', {}).get('requirements', [])}
    declared = {i['requirement'] for i in contract['invariants']}
    mapped = {rid for rid, req in requirements.items() if check_id in req['checks']}
    if len(declared) != 1:
        raise ValueError('hardware_interface requires exactly one requirement per check')
    if mapped != declared:
        raise ValueError('interface requirement mappings must match declared invariant requirements')
    for invariant in contract['invariants']:
        req = requirements.get(invariant['requirement'])
        if (not req or invariant['source'] != req['source'] or
                check_id not in req['checks'] or invariant['source']['path'] not in files):
            raise ValueError('interface invariant requires exact captured requirement source and check mapping')
    return {'contract': contract, 'immutable_inputs': [rel],
            'predicates': ['interface:' + i['id'] for i in contract['invariants']]}


def read_inventory(path):
    """Reject lossy identity maps before judging any missing terminal."""
    root = ET.parse(path).getroot()
    sheets = [text(sheet.get('tstamps')) for sheet in root.findall('./design/sheet')]
    if (not sheets or len(set(sheets)) != len(sheets) or '/' not in sheets
            or any(not s.startswith('/') or not s.endswith('/')
                   or (s != '/' and any(not segment for segment in s[1:-1].split('/')))
                   for s in sheets)):
        raise ValueError('missing or ambiguous native hierarchy inventory')
    parts, components, refs, memberships, nets = {}, {}, {}, {}, {}
    pin_names = {}
    uuids = set()
    disconnected = set()
    for part in root.findall('./libparts/libpart'):
        key = (text(part.get('lib')), text(part.get('part')))
        pins = [text(p.get('num')) for p in part.findall('./pins/pin')]
        if key in parts or not pins or len(set(pins)) != len(pins):
            raise ValueError('missing or ambiguous symbol pin inventory')
        parts[key] = set(pins)
        pin_names[key] = {p.get('num'): p.get('name') for p in part.findall('./pins/pin')}
    for comp in root.findall('./components/comp'):
        ref, sheet, lib = text(comp.get('ref')), comp.find('sheetpath'), comp.find('libsource')
        if sheet is None or lib is None:
            raise ValueError('missing native sheet or symbol identity')
        sheet_id = text(sheet.get('tstamps'))
        uuid = text(comp.findtext('tstamps'))
        key, symbol = (sheet_id, ref), (text(lib.get('lib')), text(lib.get('part')))
        if (not sheet_id.startswith('/') or not sheet_id.endswith('/') or key in components
                or ref in refs or sheet_id not in sheets or (sheet_id, uuid) in uuids):
            raise ValueError('ambiguous native component identity')
        if symbol not in parts:
            raise ValueError('missing native symbol pin inventory')
        values = comp.findall('value')
        if len(values) != 1:
            raise ValueError('missing or ambiguous native component value')
        components[key] = {'sheet': sheet_id, 'reference': ref, 'uuid': uuid,
            'symbol': ':'.join(symbol), 'pins': sorted(parts[symbol]),
            'pin_names': pin_names[symbol],
            'populated': not any(p.get('name') == 'dnp' for p in comp.findall('property')),
            'value': values[0].text or ''}
        refs[ref] = key
        uuids.add((sheet_id, uuid))
    if not components:
        raise ValueError('empty native inventory')
    for net in root.findall('./nets/net'):
        code, name = text(net.get('code')), text(net.get('name'))
        if code in nets or name in nets.values():
            raise ValueError('duplicate native net code or name')
        nets[code] = name
        nodes = net.findall('node')
        for node in nodes:
            ref, pin = text(node.get('ref')), text(node.get('pin'))
            if ref not in refs or pin not in components[refs[ref]]['pins'] or (ref, pin) in memberships:
                raise ValueError('ambiguous or unknown native terminal membership')
            memberships[(ref, pin)] = code
        # Native generated singleton NC names have no sheet/name prefix; a
        # collision with another net appends _1, _2, ... A local label is
        # sheet-qualified; an identically spelled global label or power symbol
        # is judged only by qualify_global_names from source geometry.
        if len(nodes) == 1:
            pin_name = components[refs[ref]]['pin_names'][pin]
            prefix = f'{pin_name}-' if pin_name and pin_name not in (pin, '~') else ''
            if re.fullmatch(re.escape(f'unconnected-({ref}-{prefix}Pad{pin})') + '(_[1-9][0-9]*)?', name):
                disconnected.add((ref, pin))
    return {'components': components, 'memberships': memberships, 'nets': nets,
            'sheets': sheets, 'disconnected': disconnected, 'unresolved': {}, 'sources': {}}


# KiCad schematic internal unit is 100 nm. Native labels attach to wires within
# about one unit; anything within this much larger margin may be connected.
SCHEMATIC_UNIT = 100
TOUCH_MARGIN = 10000
INERT = frozenset('version generator generator_version uuid paper title_block lib_symbols text '
                  'text_box polyline rectangle circle arc bezier image table sheet_instances '
                  'symbol_instances embedded_fonts embedded_files group'.split())
CONNECTABLE = frozenset('wire bus bus_entry junction no_connect label global_label '
                        'hierarchical_label netclass_flag symbol sheet'.split())


def atoms(node):
    return [x for x in node[1:] if isinstance(x, str)]


def only(node, key):
    from .symbol_library import children
    found = children(node, key)
    if len(found) != 1:
        raise ValueError(f'expected one native {key}')
    return found[0]


def atom(node, key):
    values = atoms(only(node, key))
    if len(values) != 1:
        raise ValueError(f'expected one native {key} value')
    return values[0]


def coordinate(value):
    from .symbol_library import nanometres
    nm = nanometres(value)
    if nm % SCHEMATIC_UNIT:
        raise ValueError('coordinate is not a whole native schematic unit')
    return nm


def xy(node):
    values = atoms(node)
    if len(values) < 2:
        raise ValueError('expected native coordinates')
    return coordinate(values[0]), coordinate(values[1])


def placed_pins(symbol, library):
    """Native pin connection points: rotate counterclockwise, then mirror."""
    from .symbol_library import children
    definition = library.get(atom(symbol, 'lib_name' if children(symbol, 'lib_name') else 'lib_id'))
    if definition is None or children(definition, 'extends') or children(definition, 'pin'):
        raise ValueError('unsupported embedded symbol definition')
    styles = children(symbol, 'body_style') + children(symbol, 'convert')
    if len(styles) > 1:
        raise ValueError('ambiguous native body style')
    unit, style = atom(symbol, 'unit'), atoms(styles[0]) if styles else ['1']
    place = atoms(only(symbol, 'at'))
    mirrors = children(symbol, 'mirror')
    mirror = atoms(mirrors[0]) if mirrors else []
    if (len(style) != 1 or len(place) != 3 or len(mirrors) > 1
            or place[2] not in ('0', '90', '180', '270') or mirror not in ([], ['x'], ['y'])):
        raise ValueError('unsupported native symbol placement')
    x, y = xy(only(symbol, 'at'))
    pins = {}
    for sub in children(definition, 'symbol'):
        suffix = atoms(sub)[0].rsplit('_', 2) if atoms(sub) else []
        if len(suffix) != 3 or not suffix[1].isdigit() or not suffix[2].isdigit():
            raise ValueError('unsupported embedded symbol unit')
        if suffix[1] not in ('0', unit) or suffix[2] not in ('0', style[0]):
            continue
        for pin in children(sub, 'pin'):
            if not atoms(pin):
                raise ValueError('unsupported embedded pin')
            px, py = xy(only(pin, 'at'))
            for _ in range(int(place[2]) // 90):
                px, py = -py, px
            if mirror == ['x']:
                py = -py
            elif mirror == ['y']:
                px = -px
            hidden = 'hide' in atoms(pin) or any(atoms(h) == ['yes'] for h in children(pin, 'hide'))
            pins.setdefault(atom(pin, 'number'), []).append(
                {'point': (x + px, y - py), 'type': atoms(pin)[0], 'hidden': hidden})
    return pins


def within(point, a, b, margin):
    """Exact integer test: point lies within margin of segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    along = (point[0] - a[0]) * dx + (point[1] - a[1]) * dy
    if along <= 0 or along >= length:
        end = a if along <= 0 else b
        return (point[0] - end[0]) ** 2 + (point[1] - end[1]) ** 2 <= margin * margin
    cross = (point[0] - a[0]) * dy - (point[1] - a[1]) * dx
    return cross * cross <= margin * margin * length


def touches(first, second, margin):
    """Conservative proximity between point and segment shapes."""
    if len(first) == 1 or len(second) == 1:
        point, shape = (first, second) if len(first) == 1 else (second, first)
        return within(point[0], shape[0], shape[-1], margin)
    a, b = first
    c, d = second
    if any(within(p, *s, margin) for p, s in ((a, second), (b, second), (c, first), (d, first))):
        return True
    def turn(p, q, r):
        value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (value > 0) - (value < 0)
    return turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0


def item_shapes(kind, node, library):
    from .symbol_library import children
    if kind not in CONNECTABLE:
        raise ValueError(f'unsupported native schematic item {kind!r}')
    uuid = atom(node, 'uuid')
    if kind == 'symbol':
        return [{'kind': 'pin', 'shape': (pin['point'],), 'uuid': uuid, 'number': number,
                 'pin': pin, 'node': node}
                for number, pins in placed_pins(node, library).items() for pin in pins]
    if kind == 'sheet':
        return [{'kind': 'sheet_pin', 'shape': (xy(only(pin, 'at')),), 'uuid': atom(pin, 'uuid')}
                for pin in children(node, 'pin')]
    if kind in ('wire', 'bus'):
        points = only(node, 'pts')[1:]
        if (len(points) != 2 or any(not isinstance(p, list) or p[:1] != ['xy'] for p in points)
                or {n[0] for n in node[1:] if isinstance(n, list)} - {'pts', 'stroke', 'uuid'}):
            raise ValueError(f'unsupported native {kind} geometry')
        shape = tuple(xy(p) for p in points)
        if shape[0] == shape[1]:
            raise ValueError(f'degenerate native {kind}')
    elif kind == 'bus_entry':
        (x, y), (dx, dy) = xy(only(node, 'at')), xy(only(node, 'size'))
        shape = ((x, y), (x + dx, y + dy))
    else:
        shape = (xy(only(node, 'at')),)
    name = text(node[1]) if kind.endswith('label') else None
    return [{'kind': kind, 'shape': shape, 'uuid': uuid, 'name': name}]


def sheet_items(schematic):
    """Connectable geometry of one sheet file, plus every item it cannot model."""
    from .symbol_library import children
    library = {atoms(s)[0]: s for s in children(only(schematic, 'lib_symbols'), 'symbol') if atoms(s)}
    items, problems = [], []
    for node in schematic[1:]:
        kind = node[0] if isinstance(node, list) and node and isinstance(node[0], str) else None
        if kind not in INERT:
            try:
                items += item_shapes(kind, node, library)
            except ValueError as exc:
                problems.append(str(exc))
    return items, problems


def label_locator(rel, item):
    x, y = (f'{v / 1e6:g}' for v in item['shape'][0])
    return f'{rel}: global_label {item["name"]} at ({x}, {y}) mm, UUID {item["uuid"]}'


def label_connection(schematics, root, component, pin, name):
    """Classify one exact global label spelling against one native pin.

    'connected' requires exact native-equivalent geometry: a label on the pin,
    or on a wire reached through exact shared endpoints or junctions.
    Unmodelled items cannot break such a route. 'disconnected' requires every
    connectable item in the sheet file to be modelled and nothing name-bearing
    within TOUCH_MARGIN of the pin's conductor. Anything else is 'unknown';
    text presence is never evidence.
    """
    from .symbol_library import children
    try:
        owners = [(rel, symbol) for rel, schematic in schematics.items()
                  for symbol in children(schematic, 'symbol')
                  if atom(symbol, 'uuid') in component['uuid'].split()]
        if len(owners) != 1:
            return 'unknown', 'native component UUID does not resolve to one captured source symbol'
        rel, symbol = owners[0]
        instance = '/' + root + component['sheet'][:-1]
        refs = {atom(path, 'reference') for project in children(only(symbol, 'instances'), 'project')
                for path in children(project, 'path') if atoms(path) == [instance]}
        if refs != {component['reference']} or atom(symbol, 'lib_id') != component['symbol']:
            return 'unknown', 'captured source symbol instance does not match native component identity'
        items, problems = sheet_items(schematics[rel])
    except ValueError as exc:
        return 'unknown', f'source geometry unsupported: {exc}'
    targets = [i for i in items if i['kind'] == 'pin' and i['node'] is symbol and i['number'] == pin]
    if len(targets) != 1 or targets[0]['pin']['hidden'] or targets[0]['pin']['type'] == 'no_connect':
        return 'unknown', (f'{rel}: {component["reference"]} pin {pin} has no unique visible source point'
                           + ''.join(f'; {problem}' for problem in problems[:1]))
    target = targets[0]
    origin = target['shape'][0]
    wires = [i for i in items if i['kind'] == 'wire']
    junctions = [i['shape'][0] for i in items if i['kind'] == 'junction']
    # Only exact connections observed with native kicad-cli; no tolerance.
    reached = [w for w in wires if origin in w['shape']]
    frontier = list(reached)
    while frontier:
        wire = frontier.pop()
        for other in wires:
            if any(other is r for r in reached):
                continue
            if set(wire['shape']) & set(other['shape']) or any(
                    within(j, *wire['shape'], 0) and within(j, *other['shape'], 0) for j in junctions):
                reached.append(other)
                frontier.append(other)
    labels = [i for i in items if i['kind'] == 'global_label' and i['name'] == name]
    for label in labels:
        point = label['shape'][0]
        if point == origin or any(within(point, *w['shape'], 0) for w in reached):
            route = (', via wire UUIDs ' + ', '.join(sorted(w['uuid'] for w in reached))) if point != origin else ''
            return 'connected', [label_locator(rel, label) + f' joins {component["reference"]} pin {pin}{route}']
    if problems:
        return 'unknown', f'{rel}: source geometry unsupported: {problems[0]}'
    # Conservative closure: anything within the margin may be connected.
    cluster = [target]
    for item in cluster:
        cluster += [other for other in items if all(other is not c for c in cluster)
                    and touches(item['shape'], other['shape'], TOUCH_MARGIN)]
    blockers = sorted({i['kind'] for i in cluster[1:] if i['kind'] not in ('wire', 'junction', 'no_connect')})
    if blockers:
        return 'unknown', (f'{rel}: {", ".join(blockers)} may touch {component["reference"]} '
                           f'pin {pin}; global label connection unproven')
    return 'disconnected', [label_locator(rel, label) + f' does not reach {component["reference"]} pin {pin}'
                            for label in labels]


def unqualified_names(schematic):
    """Spellings that can name a native net without a sheet prefix."""
    from .symbol_library import children
    names = {text(label[1]) for label in children(schematic, 'global_label')}
    library = {atoms(s)[0]: s for lib in children(schematic, 'lib_symbols')
               for s in children(lib, 'symbol') if atoms(s)}
    for symbol in children(schematic, 'symbol'):
        keys = [atoms(n)[:1] for n in children(symbol, 'lib_name') or children(symbol, 'lib_id')]
        definition = library.get(keys[0][0]) if keys and keys[0] else None
        if definition is not None and children(definition, 'power'):
            # Power nets follow Value; keep every pin name too, conservatively.
            names |= {atoms(n)[1] for n in children(symbol, 'property') if atoms(n)[:1] == ['Value']
                      and len(atoms(n)) > 1}
            names |= {atoms(n)[0] for sub in children(definition, 'symbol') for pin in children(sub, 'pin')
                      for n in children(pin, 'name') if atoms(n)}
    return names


def qualify_global_names(inventory, schematics, root):
    """An unprefixed source name exempts a native NC spelling only with geometry proof."""
    spellings = set().union(*(unqualified_names(s) for s in schematics.values()))
    components = {c['reference']: c for c in inventory['components'].values()}
    for ref, pin in sorted(inventory['disconnected']):
        name = inventory['nets'][inventory['memberships'][(ref, pin)]]
        if name not in spellings:
            continue
        status, detail = label_connection(schematics, root, components[ref], pin, name)
        inventory['sources'][(ref, pin)] = {'status': status,
            **({'reason': detail} if status == 'unknown' else {'locators': detail})}
        if status == 'connected':
            inventory['disconnected'].discard((ref, pin))
        elif status == 'unknown':
            inventory['disconnected'].discard((ref, pin))
            inventory['unresolved'][(ref, pin)] = detail
    return inventory


def resolve_components(contract, inventory):
    resolved = {}
    for cid, declaration in contract['components'].items():
        matches = [inventory['components'][(loc['sheet'], loc['reference'])]
                   for loc in declaration['locations']
                   if (loc['sheet'], loc['reference']) in inventory['components']]
        if len(matches) > 1:
            resolved[cid] = {'error': 'multiple approved locations present'}
        elif matches and matches[0]['symbol'] not in declaration['symbols']:
            resolved[cid] = {'error': 'unsupported native symbol mapping', 'native': matches[0]}
        else:
            resolved[cid] = matches[0] if matches else None
    return resolved


def evaluate(contract, inventory):
    resolved = resolve_components(contract, inventory)
    records = []
    for invariant in contract['invariants']:
        record = {'id': invariant['id'], 'expected': invariant, 'status': 'not_evaluated',
                  'actual': {}, 'locators': []}
        records.append(record)
        kind = invariant['kind']
        tids = invariant.get('terminals', [invariant['terminal']] if kind == 'named_net' else [])
        cids = [contract['terminals'][t]['component'] for t in tids] if tids else [invariant['component']]
        natives = [resolved[c] for c in cids]
        record['actual']['components'] = dict(zip(cids, natives))
        record['locators'] = [f"components/comp[@ref='{n['reference']}'], sheet {n['sheet']}, UUID {n['uuid']}"
                              for n in natives if n and 'error' not in n]
        if any(n and 'error' in n for n in natives):
            record['actual']['reason'] = 'unsupported component identity'
            continue
        if any(n is None for n in natives):
            record['status'] = 'violation'
            record['actual']['reason'] = 'required component absent from complete native inventory'
            continue
        if kind == 'component':
            satisfied = natives[0]['populated'] == invariant['populated']
        elif kind == 'value':
            allowed = [invariant['equals']]
            allowed += [v for e in contract['allowed_edits'] if e['component'] == cids[0] for v in e['values']]
            satisfied = natives[0]['value'] in allowed
        else:
            pins = [contract['terminals'][t]['pin'] for t in tids]
            if any(pin not in n['pins'] for pin, n in zip(pins, natives)):
                record['actual']['reason'] = 'declared pin missing from native symbol metadata'
                continue
            codes = [inventory['memberships'].get((n['reference'], pin)) for n, pin in zip(natives, pins)]
            record['actual']['terminals'] = [{'terminal': t, 'pin': pin, 'net_code': code,
                'net_name': inventory['nets'].get(code)} for t, pin, code in zip(tids, pins, codes)]
            keys = [(n['reference'], pin) for n, pin in zip(natives, pins)]
            sources = [{'terminal': t, **inventory['sources'][k]}
                       for t, k in zip(tids, keys) if k in inventory['sources']]
            if sources:
                record['actual']['global_label_sources'] = sources
                record['locators'] += [l for s in sources if s['status'] == 'connected' for l in s['locators']]
            unresolved = [inventory['unresolved'][k] for k in keys if k in inventory['unresolved']]
            connected = all(code is not None and k not in inventory['disconnected']
                            for k, code in zip(keys, codes))
            if kind == 'named_net':
                satisfied = connected and inventory['nets'][codes[0]] in invariant['names']
            elif kind == 'same_net':
                satisfied = connected and codes[0] == codes[1]
            else:
                satisfied = connected and codes[0] != codes[1]
            if unresolved and satisfied:
                # A disconnected pin would fail; only proved geometry may pass.
                record['actual']['reason'] = '; '.join(unresolved)
                continue
        record['status'] = 'satisfied' if satisfied else 'violation'
    return records


def check_hardware_interface(ctx):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        xml = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'design.xml')
        specs = ctx.scope['checks']
        prior = next(s for s in specs if s['id'] == ctx.params['netlist_check'])
        if specs.index(prior) >= specs.index(ctx.spec):
            raise ValueError('native netlist prerequisite must precede interface check')
        try:
            from .symbol_library import parse_sexpr, children
            from .checks import verified_hierarchy
            files, _ = verified_hierarchy(ctx)
            schematics = {}
            for rel in files:
                schematics[rel] = parse_sexpr((ctx.inputs / rel).read_bytes())
                if schematics[rel][0] != 'kicad_sch':
                    raise ValueError('expected native schematic for label qualification')
                for label in children(schematics[rel], 'global_label'):
                    if len(label) < 2:
                        raise ValueError('missing native global label name')
                    text(label[1])
            inventory = read_inventory(xml)
            native_result = read_json(ctx.run_dir / 'checks' / f"{prior['id']}.json")
            if len(inventory['sheets']) != native_result['details']['sheet_instances']:
                raise ValueError('native XML hierarchy count differs from verified schematic hierarchy')
            # sheet_instances walks from the scope schematic, so files[0] is the root.
            qualify_global_names(inventory, schematics, atom(schematics[files[0]], 'uuid'))
            records = evaluate(descriptor['contract'], inventory)
        except (ValueError, KeyError, TypeError, ET.ParseError) as exc:
            records = [{'id': invariant['id'], 'expected': invariant,
                        'status': 'not_evaluated', 'actual': {'reason': str(exc)},
                        'locators': ['native XML inventory']}
                       for invariant in descriptor['contract']['invariants']]
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    complete = all(r['status'] != 'not_evaluated' for r in records)
    violations = [r['id'] for r in records if r['status'] == 'violation']
    report = {'schema': 'agent-ee.interface-result/1', 'candidate_id': ctx.manifest['candidate_id'],
              'contract_sha256': digest(ctx.inputs / ctx.params['contract']),
              'native_xml_sha256': digest(xml), 'invariants': records}
    write_json(ctx.work / 'interface.json', report)
    ctx.evidence('interface.json')
    ctx.result['details'].update(invariants=records, violations=violations)
    ctx.result['location'] = ', '.join(violations)
    ctx.result['limitations'].append('Only declared schematic identities and direct conductors are protected; '
        'undeclared topology, PCB routing, physical access and electrical transfer are outside coverage.')
    ctx.result.update(execution='ok' if complete else 'unsupported',
        outcome=('defect' if violations else 'pass') if complete else 'unknown',
        summary=f'{len(violations)} interface violation(s); complete={complete}')
