"""Captured owner declarations over direct native schematic conductors."""
import json
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


def read_inventory(path, *, global_labels=()):
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
        # Native generated singleton NC names have no sheet/name prefix. A
        # local label is sheet-qualified; global labels require captured source.
        if len(nodes) == 1:
            pin_name = components[refs[ref]]['pin_names'][pin]
            prefix = f'{pin_name}-' if pin_name and pin_name not in (pin, '~') else ''
            if name == f'unconnected-({ref}-{prefix}Pad{pin})' and name not in global_labels:
                disconnected.add((ref, pin))
    return {'components': components, 'memberships': memberships, 'nets': nets,
            'sheets': sheets, 'disconnected': disconnected}


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
            connected = all(code is not None and (n['reference'], pin) not in inventory['disconnected']
                            for n, pin, code in zip(natives, pins, codes))
            if kind == 'named_net':
                satisfied = connected and inventory['nets'][codes[0]] in invariant['names']
            elif kind == 'same_net':
                satisfied = connected and codes[0] == codes[1]
            else:
                satisfied = connected and codes[0] != codes[1]
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
            global_labels = {}
            for rel in files:
                schematic = parse_sexpr((ctx.inputs / rel).read_bytes())
                if schematic[0] != 'kicad_sch':
                    raise ValueError('expected native schematic for label qualification')
                for label in children(schematic, 'global_label'):
                    if len(label) < 2:
                        raise ValueError('missing native global label name')
                    name = text(label[1])
                    global_labels.setdefault(name, []).append(f'{rel}: global_label {name}')
            inventory = read_inventory(xml, global_labels=global_labels)
            native_result = read_json(ctx.run_dir / 'checks' / f"{prior['id']}.json")
            if len(inventory['sheets']) != native_result['details']['sheet_instances']:
                raise ValueError('native XML hierarchy count differs from verified schematic hierarchy')
            records = evaluate(descriptor['contract'], inventory)
            for record in records:
                for terminal in record['actual'].get('terminals', []):
                    record['locators'].extend(global_labels.get(terminal['net_name'], []))
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
