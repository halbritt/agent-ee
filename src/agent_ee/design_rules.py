"""Design-rule cards: versioned writer guidance bound to qualified check adapters.

A rule pack is captured owner data. Each card carries guidance text for the
implementation writer and at most one enforcement: a closed connectivity
adapter below, or a delegation to one earlier check whose single predicate
grades it. A card without qualified enforcement is advisory: its text may reach
the writer prompt, but it has no predicate, so it can never satisfy an
acceptance mapping. Grading reads only the strict native inventory of an
earlier ``netlist`` check, owner role bindings to exported ``{reference, pin}``
terminals and verified datasheet facts. Card text is never matched against the
design.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import re
from decimal import Decimal
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, strings, text
from .netclass_completeness import load_json
from .netlist_extra import class_rule
from .storage import digest, read_json, write_json

PACK_SCHEMA = 'agent-ee.design-rule-pack/1'
BINDINGS_SCHEMA = 'agent-ee.design-rule-bindings/1'
CARD_ID = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
PRIORITIES = frozenset(('critical', 'important', 'recommended'))
CARD_FIELDS = 'id version description applies_to rationale examples violations_cause priority enforcement parameters'
ROLE_GROUPS = frozenset(('i2c_buses', 'crystals', 'resets', 'power_pins', 'usb_lines'))
# Written values only: 4.7k, 4k7, 470R, 10 kΩ. A lower-case m is never read as mega.
VALUE = re.compile(r'(\d+(?:\.\d+)?)\s*([kKM]?)\s*(?:Ω|ohms?|R)?')
RKM = re.compile(r'(\d+)([RkKM])(\d+)')
SCALE = {'': 1, 'R': 1, 'k': 1000, 'K': 1000, 'M': 1000000}
ADDRESS = re.compile(r'0[xX]([0-9A-Fa-f]{1,2})')


def resistance(value):
    """Ohms as Decimal, or None when the native Value is not a plain resistance."""
    value = value.strip() if isinstance(value, str) else ''
    match = RKM.fullmatch(value)
    if match:
        return Decimal(f'{match[1]}.{match[3]}') * SCALE[match[2]]
    match = VALUE.fullmatch(value)
    return Decimal(match[1]) * SCALE[match[2]] if match else None


def address(value):
    match = ADDRESS.fullmatch(value.strip()) if isinstance(value, str) else None
    number = int(match[1], 16) if match else None
    return number if number is not None and number <= 0x7f else None


def ohm_window(parameters):
    exact_keys(parameters, 'min_ohm max_ohm')
    low, high = parameters['min_ohm'], parameters['max_ohm']
    for value in (low, high):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1e9:
            raise ValueError('ohm window bounds must be finite positive numbers')
    if low >= high:
        raise ValueError('ohm window requires min_ohm < max_ohm')


def no_parameters(parameters):
    if parameters != {}:
        raise ValueError('adapter takes no parameters')


def terminal(value):
    exact_keys(value, 'reference pin')
    text(value['reference'])
    text(value['pin'])
    return value


def applicability(value):
    exact_keys(value, 'fact requires')
    text(value['fact'])
    if type(value['requires']) is not bool:
        raise ValueError('applicability requires must be Boolean')


def optional(value, required, extra=()):
    keys = set(required.split())
    if not isinstance(value, dict) or not keys <= value.keys() <= keys | set(extra):
        raise ValueError(f'expected fields: {required}' + (f' (optional {" ".join(extra)})' if extra else ''))


def unique_ids(entries, label):
    ids = [text(entry['id']) for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError(f'duplicate {label} id')


def bus_binding(bus):
    exact_keys(bus, 'id sda scl rail devices')
    for key in ('sda', 'scl', 'rail'):
        terminal(bus[key])
    if not isinstance(bus['devices'], list) or not bus['devices']:
        raise ValueError('I2C bus requires every attached device')
    refs = set()
    for device in bus['devices']:
        optional(device, 'reference part_number', ('address',))
        if text(device['reference']) in refs:
            raise ValueError('duplicate I2C device')
        refs.add(device['reference'])
        text(device['part_number'])
        if 'address' not in device:
            continue
        resolved = device['address']
        if isinstance(resolved, dict) and set(resolved) == {'fact'}:
            text(resolved['fact'])
            continue
        exact_keys(resolved, 'strap')
        exact_keys(resolved['strap'], 'pin options')
        text(resolved['strap']['pin'])
        options = resolved['strap']['options']
        if not isinstance(options, list) or not options:
            raise ValueError('strap requires options')
        for option in options:
            exact_keys(option, 'terminal fact')
            terminal(option['terminal'])
            text(option['fact'])


def crystal_binding(crystal):
    exact_keys(crystal, 'reference pins ground')
    text(crystal['reference'])
    strings(crystal['pins'])
    terminal(crystal['ground'])


def reset_binding(target):
    optional(target, 'id terminal part_number rail ground', ('applicability',))
    text(target['part_number'])
    for key in ('terminal', 'rail', 'ground'):
        terminal(target[key])
    if 'applicability' in target:
        applicability(target['applicability'])


def power_binding(pin):
    exact_keys(pin, 'terminal ground')
    terminal(pin['terminal'])
    terminal(pin['ground'])


def usb_binding(line):
    optional(line, 'id device connector part_number', ('applicability',))
    text(line['part_number'])
    terminal(line['device'])
    terminal(line['connector'])
    if 'applicability' in line:
        applicability(line['applicability'])


ROLE_VALIDATORS = {'i2c_buses': bus_binding, 'crystals': crystal_binding, 'resets': reset_binding,
                   'power_pins': power_binding, 'usb_lines': usb_binding}


def validate_bindings(bindings):
    exact_keys(bindings, 'schema classes roles')
    if bindings['schema'] != BINDINGS_SCHEMA:
        raise ValueError('unsupported design-rule bindings')
    if not isinstance(bindings['classes'], dict):
        raise ValueError('expected exact library:symbol class map')
    for symbol, rule in bindings['classes'].items():
        if ':' not in text(symbol):
            raise ValueError('class requires exact library:symbol ID')
        class_rule(rule)
    roles = bindings['roles']
    if not isinstance(roles, dict) or not roles.keys() <= ROLE_GROUPS:
        raise ValueError('unknown design-rule role group')
    for group, entries in roles.items():
        # An absent group is an unbound role (unknown); an empty one would be a vacuous pass.
        if not isinstance(entries, list) or not entries:
            raise ValueError(f'{group} must be a nonempty list when declared')
        for entry in entries:
            ROLE_VALIDATORS[group](entry)
        if group in ('i2c_buses', 'resets', 'usb_lines'):
            unique_ids(entries, group)
    return bindings


def validate_pack(pack):
    exact_keys(pack, 'schema id version provenance cards')
    if pack['schema'] != PACK_SCHEMA:
        raise ValueError('unsupported design-rule pack')
    text(pack['id'])
    text(pack['version'])
    exact_keys(pack['provenance'], 'authored_by date license derived_from')
    for key in ('authored_by', 'date', 'license'):
        text(pack['provenance'][key])
    derived = pack['provenance']['derived_from']
    if not isinstance(derived, list) or any(not isinstance(v, str) or not v.strip() for v in derived):
        raise ValueError('derived_from must list provenance text')
    cards = pack['cards']
    if not isinstance(cards, list) or not cards:
        raise ValueError('rule pack requires cards')
    ids = set()
    for card in cards:
        exact_keys(card, CARD_FIELDS)
        if not isinstance(card['id'], str) or not CARD_ID.fullmatch(card['id']) or card['id'] in ids:
            raise ValueError(f"invalid or duplicate card id: {card.get('id')!r}")
        ids.add(card['id'])
        if type(card['version']) is not int or card['version'] < 1:
            raise ValueError('card version must be a positive integer')
        for key in ('description', 'rationale', 'violations_cause'):
            text(card[key])
        strings(card['applies_to'])
        strings(card['examples'])
        if card['priority'] not in PRIORITIES:
            raise ValueError('card priority must be critical, important or recommended')
        enforcement = card['enforcement']
        if enforcement is not None:
            if not isinstance(enforcement, dict) or len(enforcement) != 1 or not (
                    enforcement.keys() <= {'adapter', 'check_kind'}):
                raise ValueError('enforcement must be null, {adapter} or {check_kind}')
            text(next(iter(enforcement.values())))
        if not isinstance(card['parameters'], dict):
            raise ValueError('card parameters must be an object')
        mode, name = enforcement_mode(card)
        if mode == 'adapter':
            ADAPTERS[name][2](card['parameters'])
        elif card['parameters']:
            raise ValueError('only a qualified adapter card takes parameters')
    return pack


def enforcement_mode(card):
    """``(adapter, name)``, ``(delegated, kind)`` or ``(advisory, reason)``."""
    from .capabilities import KINDS
    enforcement = card['enforcement']
    if enforcement is None:
        return 'advisory', 'no executable mapping'
    if 'adapter' in enforcement:
        name = enforcement['adapter']
        return ('adapter', name) if name in ADAPTERS else ('advisory', f'adapter {name!r} is not qualified')
    kind = enforcement['check_kind']
    if kind in KINDS and kind != 'design_rules':
        return 'delegated', kind
    return 'advisory', f'check kind {kind!r} is not a registered capability'


def captured_json(params, key, files, inputs):
    rel = str(safe_relative(params[key]))
    if rel != params[key] or rel not in files or inputs is None:
        raise ValueError(f'{key} must be captured')
    return load_json(Path(inputs) / rel)


def capability(params, scope, files, inputs, check_id):
    optional(params, 'pack bindings netlist_check cards', ('facts_check', 'delegated'))
    if params['pack'] == params['bindings']:
        raise ValueError('expected distinct captured pack and bindings')
    pack = validate_pack(captured_json(params, 'pack', files, inputs))
    bindings = validate_bindings(captured_json(params, 'bindings', files, inputs))
    if 'facts_check' in params:
        text(params['facts_check'])
    selected = params['cards']
    strings(selected)
    cards = {card['id']: card for card in pack['cards']}
    delegated = params.get('delegated', {})
    if not isinstance(delegated, dict):
        raise ValueError('delegated must map card ids to checks')
    specs = scope['checks']
    prior = specs[:next(i for i, s in enumerate(specs) if s['id'] == check_id)]
    predicates = []
    for cid in selected:
        if cid not in cards:
            raise ValueError(f'card {cid} is not in rule pack {pack["id"]}')
        mode, name = enforcement_mode(cards[cid])
        if mode == 'advisory':
            # Scope/3 refusal: an unverifiable card can never be graded or mapped.
            raise ValueError(f'card {cid} is advisory ({name}); it cannot be enforced or mapped')
        if mode == 'adapter':
            if cid in delegated:
                raise ValueError(f'adapter card {cid} cannot be delegated')
            predicates.append(f'design-rule:{cid}:{name}')
            continue
        delegation = delegated.get(cid)
        exact_keys(delegation, 'check predicate')
        source = next((s for s in prior if s['id'] == delegation['check']), None)
        if source is None or source.get('kind') != name:
            raise ValueError(f'card {cid} needs an earlier {name} check')
        from .capabilities import describe_check
        descriptor = describe_check(source, check_specs=specs, files=files, scope=scope, inputs=inputs)
        # One predicate, so the delegated check-level outcome is exactly this card's verdict.
        if descriptor['status'] != 'supported' or descriptor['predicates'] != [delegation['predicate']]:
            raise ValueError(f'card {cid} delegation must name the single supported predicate of {source["id"]}')
        predicates.append(f'design-rule:{cid}:delegated:{source["id"]}')
    if set(delegated) - set(selected):
        raise ValueError('delegation names a card that is not enforced')
    return {'pack':pack, 'bindings':bindings, 'immutable_inputs':[params['pack'], params['bindings']],
            'predicates':predicates}


class Design:
    """Read-only view over the strict native inventory with owner passive classes."""

    def __init__(self, graph, classes):
        self.components, self.memberships, self.nets = (graph[k] for k in ('components', 'memberships', 'nets'))
        self.classes = classes

    def net(self, anchor):
        component = self.components.get(anchor['reference'])
        if component is None or anchor['pin'] not in component['pins']:
            return None
        return self.memberships.get((anchor['reference'], anchor['pin']))

    def describe(self, code):
        return ({'net_id':code, 'net_name':self.nets[code]['name'], 'locator':self.nets[code]['locator']}
                if code is not None else {'net_id':None})

    def codes(self, ref):
        return {self.memberships.get((ref, pin)) for pin in self.components[ref]['pins']}

    def part(self, ref):
        comp = self.components[ref]
        return {'reference':ref, 'value':comp['value'], 'symbol':comp['lib_id'], 'locator':comp['locator']}

    def bridges(self, a, b, cls):
        """Declared two-terminal parts of ``cls`` with one terminal on each net: (populated, DNP)."""
        fitted, unfitted = [], []
        for ref, comp in sorted(self.components.items()):
            rule = self.classes.get(comp['lib_id'])
            if (rule and rule['class'] == cls and set(comp['pins']) == set(rule['pins'])
                    and a != b and self.codes(ref) == {a, b}):
                (fitted if comp['populated'] else unfitted).append(self.part(ref))
        return fitted, unfitted

    def unclassified(self, a, b, declared):
        """Undeclared two-pin parts across the nets, which could be the missing element."""
        found = []
        for ref, comp in sorted(self.components.items()):
            rule = self.classes.get(comp['lib_id'])
            if (ref not in declared and len(comp['pins']) == 2 and comp['populated']
                    and not (rule and set(comp['pins']) == set(rule['pins'])) and self.codes(ref) == {a, b}):
                found.append(self.part(ref))
        return found


def gate(target, kind, facts):
    """Applicability from a verified fact; absent or inapplicable facts are unknown."""
    declared = target.get('applicability')
    if declared is None:
        return 'unknown', 'applicability_fact_missing', None
    fact = facts.get(declared['fact'])
    if fact is None:
        return 'unknown', 'applicability_fact_unverified', None
    if fact['kind'] != kind or fact['part_number'] != target['part_number']:
        return 'unknown', 'applicability_fact_inapplicable', None
    cited = {'fact_id':fact['id'], 'value':fact['value'], 'requires':declared['requires']}
    return ('required', None, cited) if declared['requires'] else ('not_applicable', 'fact_establishes_not_required', cited)


def absent(item, design, a, b, declared, reason):
    others = design.unclassified(a, b, declared)
    if others:
        item.update(status='unknown', reason='unclassified_two_pin_part', unclassified=others)
    else:
        item.update(status='violation', reason=reason)
    return item


def i2c_pullups(card, roles, design, facts):
    window = card['parameters']
    items = []
    for bus in roles['i2c_buses']:
        rail = design.net(bus['rail'])
        declared = {bus[k]['reference'] for k in ('sda', 'scl', 'rail')} | {d['reference'] for d in bus['devices']}
        for line in ('sda', 'scl'):
            code = design.net(bus[line])
            item = {'bus':bus['id'], 'line':line, 'terminal':bus[line], **design.describe(code),
                    'rail':design.describe(rail), 'window_ohm':[window['min_ohm'], window['max_ohm']]}
            items.append(item)
            if code is None or rail is None:
                item.update(status='unknown', reason='terminal_unresolved')
                continue
            if code == rail:
                item.update(status='violation', reason='line_on_rail')
                continue
            pulls, unfitted = design.bridges(code, rail, 'resistor')
            item.update(pullups=pulls, unpopulated=unfitted)
            if not pulls:
                absent(item, design, code, rail, declared, 'pullup_absent')
                continue
            values = [resistance(p['value']) for p in pulls]
            if None in values or any(v <= 0 for v in values):
                item.update(status='unknown', reason='value_unparsed')
                continue
            equivalent = 1 / sum(1 / v for v in values)
            item['equivalent_ohm'] = float(round(equivalent, 3))
            inside = Decimal(str(window['min_ohm'])) <= equivalent <= Decimal(str(window['max_ohm']))
            item.update(status='satisfied' if inside else 'violation',
                        reason='equivalent_inside_window' if inside else 'equivalent_outside_window')
    return items


def resolve_address(device, design, facts):
    declared = device.get('address')
    if declared is None:
        return None, 'address_fact_missing', {}
    detail = {}
    fid = declared.get('fact')
    if fid is None:
        strap = declared['strap']
        code = design.net({'reference':device['reference'], 'pin':strap['pin']})
        matches = [o for o in strap['options'] if code is not None and design.net(o['terminal']) == code]
        detail['strap'] = {'pin':strap['pin'], **design.describe(code)}
        if len(matches) != 1:
            return None, 'strap_unresolved', detail
        fid = matches[0]['fact']
    fact = facts.get(fid)
    if fact is None or fact['kind'] != 'i2c_address' or fact['part_number'] != device['part_number']:
        return None, 'address_fact_unverified_or_inapplicable', {**detail, 'fact_id':fid}
    value = address(fact['value'])
    if value is None:
        return None, 'address_fact_unparsed', {**detail, 'fact_id':fid}
    return value, None, {**detail, 'fact_id':fid}


def i2c_addresses(card, roles, design, facts):
    items = []
    for bus in roles['i2c_buses']:
        lines = design.net(bus['sda']), design.net(bus['scl'])
        resolved = []
        for device in bus['devices']:
            item = {'bus':bus['id'], 'reference':device['reference'], 'part_number':device['part_number']}
            items.append(item)
            if None in lines or device['reference'] not in design.components or not set(lines) <= design.codes(device['reference']):
                item.update(status='unknown', reason='device_not_on_bus')
                continue
            value, reason, detail = resolve_address(device, design, facts)
            item.update(detail)
            if value is None:
                item.update(status='unknown', reason=reason)
                continue
            item['address'] = f'0x{value:02x}'
            resolved.append(item)
        for item in resolved:
            peers = [p['reference'] for p in resolved if p['address'] == item['address'] and p is not item]
            if peers:
                item.update(status='violation', reason='duplicate_address', duplicates=peers)
            else:
                item.update(status='satisfied', reason='address_unique_among_resolved')
    return items


def crystal_caps(card, roles, design, facts):
    items = []
    for crystal in roles['crystals']:
        ground = design.net(crystal['ground'])
        declared = {crystal['reference'], crystal['ground']['reference']}
        for pin in crystal['pins']:
            code = design.net({'reference':crystal['reference'], 'pin':pin})
            item = {'reference':crystal['reference'], 'pin':pin, **design.describe(code), 'ground':design.describe(ground)}
            items.append(item)
            if code is None or ground is None:
                item.update(status='unknown', reason='terminal_unresolved')
                continue
            if code == ground:
                item.update(status='violation', reason='pin_on_ground')
                continue
            caps, unfitted = design.bridges(code, ground, 'capacitor')
            item.update(load_caps=caps, unpopulated=unfitted)
            if caps:
                item.update(status='satisfied', reason='load_cap_present')
            else:
                absent(item, design, code, ground, declared, 'load_cap_absent')
    return items


def reset_rc(card, roles, design, facts):
    items = []
    for target in roles['resets']:
        item = {'id':target['id'], 'terminal':target['terminal'], 'part_number':target['part_number']}
        items.append(item)
        state, reason, cited = gate(target, 'reset_external_rc', facts)
        if cited:
            item['applicability'] = cited
        if state != 'required':
            item.update(status=state, reason=reason)
            continue
        en, rail, ground = (design.net(target[k]) for k in ('terminal', 'rail', 'ground'))
        item.update(design.describe(en), rail=design.describe(rail), ground=design.describe(ground))
        if None in (en, rail, ground):
            item.update(status='unknown', reason='terminal_unresolved')
            continue
        pulls, _ = design.bridges(en, rail, 'resistor')
        caps, _ = design.bridges(en, ground, 'capacitor')
        item.update(pullups=pulls, capacitors=caps)
        missing = [name for name, found in (('pullup', pulls), ('capacitor', caps)) if not found]
        if not missing:
            item.update(status='satisfied', reason='external_rc_present')
            continue
        declared = {target[k]['reference'] for k in ('terminal', 'rail', 'ground')}
        others = [p for name, far in (('pullup', rail), ('capacitor', ground)) if name in missing
                  for p in design.unclassified(en, far, declared)]
        item['missing'] = missing
        if others:
            item.update(status='unknown', reason='unclassified_two_pin_part', unclassified=others)
        else:
            item.update(status='violation', reason='external_rc_incomplete')
    return items


def decoupling(card, roles, design, facts):
    items = []
    for pin in roles['power_pins']:
        code, ground = design.net(pin['terminal']), design.net(pin['ground'])
        item = {'terminal':pin['terminal'], **design.describe(code), 'ground':design.describe(ground)}
        items.append(item)
        if code is None or ground is None:
            item.update(status='unknown', reason='terminal_unresolved')
            continue
        if code == ground:
            item.update(status='violation', reason='pin_on_ground')
            continue
        caps, unfitted = design.bridges(code, ground, 'capacitor')
        item.update(capacitors=caps, unpopulated=unfitted)
        if caps:
            item.update(status='satisfied', reason='capacitor_to_ground_present')
        else:
            absent(item, design, code, ground, {pin['terminal']['reference'], pin['ground']['reference']},
                   'decoupling_capacitor_absent')
    return items


def usb_series(card, roles, design, facts):
    items = []
    for line in roles['usb_lines']:
        item = {'id':line['id'], 'device':line['device'], 'connector':line['connector'],
                'part_number':line['part_number']}
        items.append(item)
        state, reason, cited = gate(line, 'usb_external_series_resistor', facts)
        if cited:
            item['applicability'] = cited
        if state != 'required':
            item.update(status=state, reason=reason)
            continue
        device, connector = design.net(line['device']), design.net(line['connector'])
        item.update(device_net=design.describe(device), connector_net=design.describe(connector))
        if device is None or connector is None:
            item.update(status='unknown', reason='terminal_unresolved')
            continue
        if device == connector:
            item.update(status='violation', reason='series_resistor_absent')
            continue
        series, _ = design.bridges(device, connector, 'resistor')
        item['series'] = series
        if series:
            item.update(status='satisfied', reason='series_resistor_present')
        else:
            absent(item, design, device, connector, {line['device']['reference'], line['connector']['reference']},
                   'series_resistor_absent')
    return items


# name: (role group, evaluator, parameter validator). Each is a pure connectivity rule.
ADAPTERS = {
    'i2c-pullup-window/1': ('i2c_buses', i2c_pullups, ohm_window),
    'i2c-address-unique/1': ('i2c_buses', i2c_addresses, no_parameters),
    'crystal-load-caps/1': ('crystals', crystal_caps, no_parameters),
    'reset-rc/1': ('resets', reset_rc, no_parameters),
    'decoupling-to-ground/1': ('power_pins', decoupling, no_parameters),
    'usb-series-resistor/1': ('usb_lines', usb_series, no_parameters),
}


def card_status(items):
    statuses = {item['status'] for item in items}
    if 'violation' in statuses:
        return 'defect'
    if 'unknown' in statuses:
        return 'unknown'
    return 'not_applicable' if statuses == {'not_applicable'} else 'pass'


def evaluate(pack, bindings, selected, graph, facts, delegated=None):
    """Per-card verdicts. ``delegated`` maps card id to an earlier check's result record."""
    design = Design(graph, bindings['classes'])
    roles = bindings['roles']
    cards = []
    for card in pack['cards']:
        mode, name = enforcement_mode(card)
        record = {'id':card['id'], 'version':card['version'], 'priority':card['priority'],
                  'description':card['description'], 'enforcement':mode,
                  'enforced':card['id'] in selected}
        cards.append(record)
        if mode == 'advisory':
            record.update(status='advisory', reason=name)
            continue
        if card['id'] not in selected:
            record.update(status='not_enforced', reason='card not selected by this check')
            continue
        if mode == 'delegated':
            result = (delegated or {}).get(card['id'])
            record.update(check_kind=name, delegated_check=result and result['id'])
            ok = result is not None and result['execution'] == 'ok' and result['outcome'] in ('pass', 'defect')
            record.update(status=result['outcome'] if ok else 'unknown',
                          reason='delegated check outcome' if ok else 'delegated check did not evaluate')
            continue
        group, evaluator, _ = ADAPTERS[name]
        record.update(adapter=name, parameters=card['parameters'])
        if group not in roles:
            record.update(status='unknown', reason='role_binding_missing', items=[])
            continue
        record['items'] = evaluator(card, roles, design, facts)
        record['status'] = card_status(record['items'])
    enforced = [c for c in cards if c['enforced']]
    statuses = {c['status'] for c in enforced}
    complete = 'unknown' not in statuses
    outcome = 'defect' if 'defect' in statuses else ('pass' if complete else 'unknown')
    return {'pack':{'id':pack['id'], 'version':pack['version']}, 'cards':cards, 'complete':complete,
            'execution':'ok' if outcome != 'unknown' else 'unsupported', 'outcome':outcome}


def witnesses(report):
    """Native locators of every violated item, for the defect location."""
    found = []
    for card in report['cards']:
        for item in card.get('items', []):
            if item['status'] == 'violation':
                locator = item.get('locator') or item.get('device_net', {}).get('locator')
                where = item.get('net_name') or item.get('reference') or item.get('id')
                found.append(f"{card['id']}: {item['reason']} at {where}" + (f' ({locator})' if locator else ''))
        if card['status'] == 'defect' and card['enforcement'] == 'delegated':
            found.append(f"{card['id']}: delegated check {card['delegated_check']} defect")
    return found


def verified_facts(ctx):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    if 'facts_check' not in ctx.params:
        return {}
    path = prerequisite(ctx, ctx.params['facts_check'], 'datasheet_facts', 'facts.json')
    try:
        return {r['id']: r['fact'] for r in read_json(path)['facts'] if r['status'] == 'verified'}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('failed', f'datasheet facts prerequisite unreadable: {exc}') from exc


def delegated_results(ctx):
    from .checks import CheckError
    results = {}
    for card, delegation in ctx.params.get('delegated', {}).items():
        path = ctx.run_dir / 'checks' / f"{delegation['check']}.json"
        if not path.is_file():
            continue
        try:
            result = read_json(path)
        except (OSError, ValueError) as exc:
            raise CheckError('failed', f'delegated result unreadable: {exc}') from exc
        if result.get('candidate_id') == ctx.manifest['candidate_id']:
            results[card] = {**result, 'sha256':digest(path)}
    return results


def check_design_rules(ctx):
    from .checks import CheckError
    from .netlist_extra import native_graph
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (ValueError, TypeError, KeyError, OSError, StopIteration) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    prior, xml, connectivity, graph = native_graph(ctx)
    facts = verified_facts(ctx)
    delegated = delegated_results(ctx)
    report = evaluate(descriptor['pack'], descriptor['bindings'], ctx.params['cards'], graph, facts, delegated)
    report.update(schema='agent-ee.design-rule-result/1', candidate_id=ctx.manifest['candidate_id'],
        netlist_check=prior['id'], facts_check=ctx.params.get('facts_check'), verified_facts=sorted(facts),
        native_xml_sha256=digest(xml), connectivity_sha256=digest(connectivity),
        pack_sha256=digest(ctx.inputs / ctx.params['pack']),
        bindings_sha256=digest(ctx.inputs / ctx.params['bindings']),
        delegated_results={card: r['sha256'] for card, r in delegated.items()})
    write_json(ctx.work / 'design-rules.json', report)
    ctx.evidence('design-rules.json')
    ctx.result['details'].update(report)
    found = witnesses(report)
    if found:
        ctx.result['location'] = '; '.join(found)
    counts = {s: sum(c['status'] == s for c in report['cards'] if c['enforced'])
              for s in ('pass', 'defect', 'unknown', 'not_applicable')}
    advisory = sum(c['status'] == 'advisory' for c in report['cards'])
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
        summary=f"Design-rule cards: {report['outcome']}; {counts['defect']} defect, {counts['unknown']} unknown, "
                f"{counts['pass']} pass, {counts['not_applicable']} not applicable; "
                f"{advisory} advisory card(s) not graded")
    ctx.result['limitations'].append('Exported-net connectivity only: a part counts when its two declared '
        'terminals sit on the two bound nets. No proximity, routing, bus capacitance, rise time or value '
        'tolerance. Undeclared parts with more than two pins (resistor arrays, ICs) are not credited. '
        'Applicability and addresses come only from verified datasheet facts; advisory cards are not graded.')


def writer_context(manifest, inputs):
    """Card guidance for the writer prompt; context only, never evidence of a pass."""
    packs = []
    for spec in manifest['scope'].get('checks', []):
        if spec.get('kind') != 'design_rules':
            continue
        params = spec['params']
        rel = str(safe_relative(params['pack']))
        pack = validate_pack(load_json(Path(inputs) / rel))
        cards = []
        for card in pack['cards']:
            mode, name = enforcement_mode(card)
            graded = mode != 'advisory' and card['id'] in params['cards']
            cards.append({key: card[key] for key in ('id', 'version', 'priority', 'description', 'applies_to',
                                                     'rationale', 'examples', 'violations_cause')} |
                         {'graded_by': spec['id'] if graded else None,
                          'advisory': mode == 'advisory'})
        packs.append({'check_id':spec['id'], 'pack':pack['id'], 'version':pack['version'],
                      'pack_sha256':digest(Path(inputs) / rel), 'cards':cards})
    return packs


def describe_design_rules(ctx):
    from .capabilities import KINDS
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    if 'facts_check' in ctx.params:
        ctx.prerequisite(ctx.params['facts_check'], ('datasheet_facts',))
    for delegation in ctx.params.get('delegated', {}).values():
        ctx.prerequisite(delegation['check'], KINDS)
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('design_rules', describe_design_rules, check_design_rules,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
