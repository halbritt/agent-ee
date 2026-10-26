"""Requirement topology oracle: declarative structural rules over the native netlist.

A captured owner rule file (``agent-ee.topology-rules/1``) maps exact library
symbols to closed component classes and declares rules, each tagged with the
baseline requirement it covers. Rules are evaluated over the strict native
inventory of an earlier ``netlist`` check, cross-checked against its retained
``connectivity.json``. The check never parses prose and never judges electrical
adequacy, values beyond declared equality, proximity or geometry.

Every rule is ``satisfied``, ``violation`` or ``unknown``. An absent or
ambiguous net or reference, a missing or unreadable value, an unclassified
populated part that could carry the required structure, or an exhausted
matcher visit bound is ``unknown``, never a pass. The expected-components
matcher is a clean-room bounded backtracking search: roles are assigned to
references most-constrained first, net roles stay a bijection with native
nets, and only two-terminal passives may be matched with swapped terminals.
"""

from .check_registry import register as _register_check
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, strings, text
from .netclass_completeness import load_json
from .storage import digest, write_json

SCHEMA = 'agent-ee.topology-rules/1'
RESULT_SCHEMA = 'agent-ee.topology-rules-result/1'
RULE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
# Terminal swaps are allowed only on these two-terminal passives.
SWAPPABLE = frozenset(('resistor', 'capacitor', 'inductor', 'ferrite', 'fuse'))
POLARIZED = frozenset(('diode', 'led', 'polarized_capacitor'))
TWO_TERMINAL = SWAPPABLE | POLARIZED
CLASSES = TWO_TERMINAL | frozenset(('connector', 'ic', 'transistor', 'switch', 'crystal'))
RULE_TYPES = frozenset(('pin_net', 'pin_no_connect', 'count_between', 'series_element',
                        'forbidden_between', 'element_between', 'value_equal', 'expected_components'))
DEFAULT_VISITS = 20_000
MAX_VISITS = 1_000_000
PREFIX = {'p': Decimal('1e-12'), 'n': Decimal('1e-9'), 'u': Decimal('1e-6'), 'µ': Decimal('1e-6'),
          'μ': Decimal('1e-6'), 'm': Decimal('1e-3'), 'k': Decimal(1000), 'K': Decimal(1000),
          'M': Decimal(1000000), 'meg': Decimal(1000000), 'Meg': Decimal(1000000),
          'MEG': Decimal(1000000), 'G': Decimal(1000000000), 'R': Decimal(1), '': Decimal(1)}
UNITS = {'Ω': 'ohm', 'ohm': 'ohm', 'ohms': 'ohm', 'Ohm': 'ohm', 'R': 'ohm', 'F': 'F', 'H': 'H', '': None}
_PREFIXES = 'meg|Meg|MEG|[pnuµμmkKMG]'
# Written values only: 4.7k, 100nF, 120R, 10 kΩ, 4k7, 4R7. Case is significant: m is milli, M is mega.
PLAIN = re.compile(rf'(\d+(?:\.\d+)?)\s*({_PREFIXES})?\s*(Ω|ohms?|Ohm|F|H|R)?')
RKM = re.compile(rf'(\d+)({_PREFIXES}|R)(\d+)\s*(Ω|F|H)?')


def quantity(value):
    """``(Decimal magnitude, unit or None)`` for a plain written value, else None."""
    value = value.strip() if isinstance(value, str) else ''
    match = RKM.fullmatch(value)
    if match:
        number, prefix, unit = f'{match[1]}.{match[3]}', match[2], match[4] or ''
    else:
        match = PLAIN.fullmatch(value)
        if not match:
            return None
        number, prefix, unit = match[1], match[2] or '', match[3] or ''
        if prefix == '' and unit == 'R':
            prefix = 'R'
    try:
        return Decimal(number) * PREFIX[prefix], UNITS[unit]
    except (InvalidOperation, KeyError):
        return None


def same_quantity(first, second):
    return first[0] == second[0] and (first[1] is None or second[1] is None or first[1] == second[1])


def fields(value, required, optional=''):
    keys = set(value) if isinstance(value, dict) else None
    if keys is None or not set(required.split()) <= keys or not keys <= set((required + ' ' + optional).split()):
        raise ValueError(f'expected fields: {required}' + (f' [{optional}]' if optional else ''))


def terminal(value):
    exact_keys(value, 'reference pin')
    text(value['reference'])
    text(value['pin'])
    return value


def net_pair(value):
    strings(value)
    if len(value) != 2:
        raise ValueError('expected two distinct net names')
    return value


def count(value, label, low=0, high=10_000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} must be an integer in [{low}, {high}]')
    return value


def validate_class(symbol, declaration):
    if ':' not in text(symbol):
        raise ValueError('class requires exact library:symbol ID')
    exact_keys(declaration, 'class terminals')
    if declaration['class'] not in CLASSES:
        raise ValueError(f'unsupported component class: {declaration["class"]!r}')
    terminals = declaration['terminals']
    if not isinstance(terminals, dict) or not terminals:
        raise ValueError('class needs terminal name to physical pin map')
    pins = [text(p) for p in terminals.values()]
    for name in terminals:
        text(name)
    if len(set(pins)) != len(pins):
        raise ValueError('class terminals must name distinct physical pins')
    if declaration['class'] in TWO_TERMINAL and len(pins) != 2:
        raise ValueError(f'{declaration["class"]} requires exactly two terminals')


def validate_rule(rule, classes):
    fields(rule, 'id requirement type', 'terminal net class nets min max value references match '
           'components fixed_nets max_visits')
    if not isinstance(rule['id'], str) or not RULE_ID.fullmatch(rule['id']):
        raise ValueError('rule id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
    text(rule['requirement'])
    kind = rule['type']
    if kind not in RULE_TYPES:
        raise ValueError(f'unsupported rule type: {kind!r}')
    declared = {d['class'] for d in classes.values()}
    base = 'id requirement type '

    def known_class(name, two_terminal):
        if name not in (TWO_TERMINAL if two_terminal else CLASSES):
            raise ValueError(f'{kind} needs a {"two-terminal " if two_terminal else ""}class')
        if name not in declared:
            raise ValueError(f'class {name!r} has no declared symbol')

    if kind == 'pin_net':
        exact_keys(rule, base + 'terminal net')
        terminal(rule['terminal'])
        text(rule['net'])
    elif kind == 'pin_no_connect':
        exact_keys(rule, base + 'terminal')
        terminal(rule['terminal'])
    elif kind == 'count_between':
        fields(rule, base + 'class nets min', 'max')
        known_class(rule['class'], True)
        net_pair(rule['nets'])
        count(rule['min'], 'min', 1)
        if 'max' in rule and count(rule['max'], 'max', 1) < rule['min']:
            raise ValueError('max must not be below min')
    elif kind == 'series_element':
        exact_keys(rule, base + 'terminal net class')
        terminal(rule['terminal'])
        text(rule['net'])
        known_class(rule['class'], True)
    elif kind == 'forbidden_between':
        exact_keys(rule, base + 'class nets')
        known_class(rule['class'], False)
        net_pair(rule['nets'])
    elif kind == 'element_between':
        fields(rule, base + 'class nets', 'value')
        known_class(rule['class'], True)
        net_pair(rule['nets'])
        if 'value' in rule and quantity(rule['value']) is None:
            raise ValueError('element value must be a plain written quantity')
    elif kind == 'value_equal':
        exact_keys(rule, base + 'references')
        strings(rule['references'])
        if len(rule['references']) < 2:
            raise ValueError('value equality needs at least two references')
    else:
        validate_expected(rule, classes, base, known_class)


def validate_expected(rule, classes, base, known_class):
    fields(rule, base + 'match components', 'fixed_nets max_visits')
    if rule['match'] not in ('exact', 'embedded'):
        raise ValueError('match must be exact or embedded')
    if not isinstance(rule['components'], list) or not 0 < len(rule['components']) <= 500:
        raise ValueError('expected components must be a list of 1 to 500 roles')
    roles = set()
    for component in rule['components']:
        fields(component, 'role class terminals', 'value symbol')
        role = text(component['role'])
        if role in roles:
            raise ValueError(f'duplicate role: {role}')
        roles.add(role)
        known_class(component['class'], False)
        terminals = component['terminals']
        if not isinstance(terminals, dict) or not terminals:
            raise ValueError(f'role {role} needs terminal to net-role map')
        for name, net_role in terminals.items():
            text(name)
            text(net_role)
        shapes = [d for symbol, d in classes.items() if d['class'] == component['class'] and
                  component.get('symbol', symbol) == symbol and set(terminals) <= set(d['terminals'])]
        if not shapes:
            raise ValueError(f'role {role}: no declared {component["class"]} symbol has terminals '
                             f'{sorted(terminals)}')
        if 'value' in component and quantity(component['value']) is None:
            raise ValueError(f'role {role} value must be a plain written quantity')
    net_roles = {n for c in rule['components'] for n in c['terminals'].values()}
    fixed = rule.get('fixed_nets', {})
    if not isinstance(fixed, dict) or any(r not in net_roles for r in fixed):
        raise ValueError('fixed_nets must map used net roles to native net names')
    for name in fixed.values():
        text(name)
    if len(set(fixed.values())) != len(fixed):
        raise ValueError('fixed_nets must keep net roles distinct')
    count(rule.get('max_visits', DEFAULT_VISITS), 'max_visits', 1, MAX_VISITS)


def validate_rules(document):
    exact_keys(document, 'schema classes rules')
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported topology rule schema')
    classes = document['classes']
    if not isinstance(classes, dict) or not classes:
        raise ValueError('expected nonempty exact library:symbol class map')
    for symbol, declaration in classes.items():
        validate_class(symbol, declaration)
    if not isinstance(document['rules'], list) or not document['rules']:
        raise ValueError('rules must be a nonempty list')
    ids = set()
    for rule in document['rules']:
        validate_rule(rule, classes)
        if rule['id'] in ids:
            raise ValueError(f'duplicate rule id: {rule["id"]}')
        ids.add(rule['id'])
    return document


def load_rules(path):
    return validate_rules(load_json(path))


def captured_rules(params, files, inputs):
    exact_keys(params, 'rules netlist_check')
    rel = str(safe_relative(params['rules']))
    if rel != params['rules'] or rel not in files or inputs is None:
        raise ValueError('topology rule file must be captured')
    text(params['netlist_check'])
    return Path(inputs) / rel


def bind_requirements(document, scope, files, check_id):
    """Every tagged requirement maps this check; every mapping requirement has a rule."""
    mapped = {r['id']: r for r in scope.get('baseline', {}).get('requirements', [])
              if check_id in r['checks']}
    tagged = {rule['requirement'] for rule in document['rules']}
    if tagged != set(mapped):
        raise ValueError(f'rule requirement tags {sorted(tagged)} differ from requirements mapping '
                         f'this check {sorted(mapped)}')
    for requirement in mapped.values():
        if requirement.get('source', {}).get('path') not in files:
            raise ValueError(f'requirement {requirement["id"]} source must be captured')


def capability(params, scope, files, inputs, check_id):
    document = load_rules(captured_rules(params, files, inputs))
    bind_requirements(document, scope, files, check_id)
    return {'rules': document, 'immutable_inputs': [params['rules']],
            'predicates': ['topology-rule:' + rule['id'] for rule in document['rules']]}


# ----------------------------------------------------------------- evaluation

class Graph:
    """Classified view of the strict native inventory."""

    def __init__(self, classes, graph):
        self.components, self.memberships, self.nets = (graph[k] for k in ('components', 'memberships', 'nets'))
        self.classified, self.unresolved, self.dnp = {}, {}, []
        for ref, comp in sorted(self.components.items()):
            if not comp['populated']:
                self.dnp.append(ref)
                continue
            known = {self.memberships.get((ref, pin)) for pin in comp['pins']}
            declaration = classes.get(comp['lib_id'])
            reason = None
            if declaration is None:
                reason = 'symbol has no declared class'
            elif not set(declaration['terminals'].values()) <= set(comp['pins']) or (
                    declaration['class'] in TWO_TERMINAL and set(declaration['terminals'].values()) != set(comp['pins'])):
                reason = 'declared terminals differ from the native physical pin inventory'
            elif None in known:
                reason = 'physical pin has no exported net membership'
            if reason:
                self.unresolved[ref] = {'reason': reason, 'nets': known - {None}, 'open': None in known,
                                        'symbol': comp['lib_id']}
                continue
            self.classified[ref] = {'class': declaration['class'], 'symbol': comp['lib_id'],
                'value': comp['value'], 'nets': known, 'terminals': {name: self.memberships[ref, pin]
                                                      for name, pin in declaration['terminals'].items()}}
        by_base = {}
        for nid, net in self.nets.items():
            by_base.setdefault(net['name'].rsplit('/', 1)[-1], []).append(nid)
        self.by_name = {net['name']: nid for nid, net in self.nets.items()}
        self.by_base = by_base

    def net(self, name):
        """``(net id, None)`` by exact native name, else by unique basename; else ``(None, reason)``."""
        if name in self.by_name:
            return self.by_name[name], None
        found = self.by_base.get(name.rsplit('/', 1)[-1], []) if '/' not in name else []
        if len(found) == 1:
            return found[0], None
        return None, (f'net {name!r} is ambiguous among {sorted(self.nets[n]["name"] for n in found)}'
                      if found else f'net {name!r} is absent from the native netlist')

    def name(self, nid):
        return self.nets[nid]['name']

    def pin(self, anchor):
        comp = self.components.get(anchor['reference'])
        if comp is None:
            return None, f'reference {anchor["reference"]} is absent from the native netlist'
        if anchor['pin'] not in comp['pins']:
            return None, f'pin {anchor["reference"]}.{anchor["pin"]} is absent from the native pin inventory'
        nid = self.memberships.get((anchor['reference'], anchor['pin']))
        if nid is None:
            return None, f'pin {anchor["reference"]}.{anchor["pin"]} has no exported net membership'
        return nid, None

    def between(self, cls, first, second, two_terminal=True):
        """Classified parts of a class touching both nets, and unresolved parts that might.

        Two-terminal rules need exactly one terminal on each net; otherwise any
        native pin of the part counts, declared as a terminal or not.
        """
        found = [ref for ref, c in self.classified.items() if c['class'] == cls and (
            sorted(c['terminals'].values()) == sorted((first, second)) if two_terminal
            else {first, second} <= c['nets'])]
        possible = [ref for ref, u in self.unresolved.items()
                    if u['open'] or {first, second} <= u['nets']]
        return found, possible


def nets_of(rule, graph, record):
    resolved, reasons = [], []
    for name in rule['nets']:
        nid, reason = graph.net(name)
        resolved.append(nid)
        if reason:
            reasons.append(reason)
    record['nets'] = [{'name': name, 'net_id': nid, 'native_name': graph.name(nid) if nid else None}
                      for name, nid in zip(rule['nets'], resolved)]
    if not reasons and resolved[0] == resolved[1]:
        reasons.append(f'nets {rule["nets"]} resolve to one native net')
    return (None, reasons) if reasons else (resolved, [])


def unknown(record, *reasons):
    record.update(status='unknown', reasons=list(reasons))
    return record


def eval_pin(rule, graph, record):
    nid, reason = graph.pin(rule['terminal'])
    if reason:
        return unknown(record, reason)
    record['actual_net'] = {'net_id': nid, 'name': graph.name(nid), 'locator': graph.nets[nid]['locator']}
    if rule['type'] == 'pin_no_connect':
        others = [list(t) for t in graph.nets[nid]['terminals']
                  if t != (rule['terminal']['reference'], rule['terminal']['pin'])]
        record['no_connect_marker'] = graph.nets[nid]['no_connect_marker']
        record['status'] = 'violation' if others else 'satisfied'
        if others:
            record['offending'] = others
        return record
    target, reason = graph.net(rule['net'])
    if reason:
        return unknown(record, reason)
    record['expected_net'] = {'net_id': target, 'name': graph.name(target)}
    record['status'] = 'satisfied' if nid == target else 'violation'
    return record


def eval_between(rule, graph, record):
    nets, reasons = nets_of(rule, graph, record)
    if reasons:
        return unknown(record, *reasons)
    kind = rule['type']
    found, possible = graph.between(rule['class'], *nets, two_terminal=kind != 'forbidden_between')
    record['possible_unclassified'] = possible
    if kind == 'forbidden_between':
        record['offending'] = found
        if found:
            record['status'] = 'violation'
        elif possible:
            unknown(record, f'unclassified populated parts may bridge the nets: {possible}')
        else:
            record['status'] = 'satisfied'
        return record
    if kind == 'count_between':
        low, high = rule['min'], rule.get('max')
        record.update(matched=found, count=len(found), min=low, max=high)
        if high is not None and len(found) > high:
            record['status'] = 'violation'
        elif len(found) >= low and (high is None or not possible):
            record['status'] = 'satisfied'
        elif possible:
            unknown(record, f'unclassified populated parts may change the count: {possible}')
        else:
            record['status'] = 'violation'
        return record
    # element_between
    wanted = quantity(rule['value']) if 'value' in rule else None
    good, unread = [], []
    for ref in found:
        got = quantity(graph.classified[ref]['value'])
        if wanted is None or (got is not None and same_quantity(got, wanted)):
            good.append(ref)
        elif got is None:
            unread.append(ref)
    record.update(matched=good, unreadable_values=unread)
    if good:
        record['status'] = 'satisfied'
    elif unread or possible:
        unknown(record, *(['element values are missing or unreadable: ' + ', '.join(unread)] if unread else []),
                *([f'unclassified populated parts may bridge the nets: {possible}'] if possible else []))
    else:
        record.update(status='violation', offending=found)
    return record


def eval_series(rule, graph, record):
    source, reason = graph.pin(rule['terminal'])
    target, reason2 = (None, None) if reason else graph.net(rule['net'])
    if reason or reason2:
        return unknown(record, reason or reason2)
    record.update(pin_net={'net_id': source, 'name': graph.name(source)},
                  target_net={'net_id': target, 'name': graph.name(target)})
    if source == target:
        record.update(status='violation', reasons=['pin ties directly to the target net'])
        return record
    found, possible = graph.between(rule['class'], source, target)
    record.update(matched=found, possible_unclassified=possible)
    if found:
        record['status'] = 'satisfied'
    elif possible:
        unknown(record, f'unclassified populated parts may form the series element: {possible}')
    else:
        record['status'] = 'violation'
    return record


def eval_values(rule, graph, record):
    values, reasons = {}, []
    for ref in rule['references']:
        comp = graph.components.get(ref)
        if comp is None:
            reasons.append(f'reference {ref} is absent from the native netlist')
            continue
        got = quantity(comp['value'])
        values[ref] = {'value': comp['value'], 'quantity': None if got is None else [format(got[0].normalize(), 'f'), got[1]]}
        if got is None:
            reasons.append(f'{ref} value {comp["value"]!r} is missing or not a plain quantity')
    record['values'] = values
    if reasons:
        return unknown(record, *reasons)
    first = quantity(graph.components[rule['references'][0]]['value'])
    unequal = [ref for ref in rule['references'] if not same_quantity(quantity(graph.components[ref]['value']), first)]
    record['status'] = 'violation' if unequal else 'satisfied'
    if unequal:
        record['offending'] = unequal
    return record


class Exhausted(Exception):
    pass


def search(roles, candidates, fixed, bound, visits):
    """First consistent injective role assignment, most-constrained role first.

    ``candidates`` maps each role to ``[(reference, {terminal: net id}, flags)]``
    orientations. Net roles stay a bijection with native net ids. Raises
    Exhausted when the shared visit counter passes ``bound``.
    """
    order = sorted(roles, key=lambda r: (len(candidates[r['role']]), -len(r['terminals']), r['role']))
    assignment, used = {}, set()
    net_of, role_of = dict(fixed), {nid: role for role, nid in fixed.items()}
    deepest = {}

    def extend(index):
        if index == len(order):
            return True
        role = order[index]
        for ref, terminals, flags in candidates[role['role']]:
            if ref in used:
                continue
            visits[0] += 1
            if visits[0] > bound:
                raise Exhausted
            added, ok = [], True
            for name, net_role in role['terminals'].items():
                nid = terminals[name]
                if net_of.get(net_role, nid) != nid or role_of.get(nid, net_role) != net_role:
                    ok = False
                    break
                if net_role not in net_of:
                    net_of[net_role], role_of[nid] = nid, net_role
                    added.append((net_role, nid))
            if ok:
                assignment[role['role']] = (ref, flags)
                used.add(ref)
                if len(assignment) > len(deepest):
                    deepest.clear()
                    deepest.update({k: v[0] for k, v in assignment.items()})
                if extend(index + 1):
                    return True
                del assignment[role['role']]
                used.discard(ref)
            for net_role, nid in added:
                del net_of[net_role], role_of[nid]
        return False

    found = extend(0)
    return (dict(assignment), dict(net_of)) if found else None, deepest


def eval_expected(rule, graph, record):
    roles, bound = rule['components'], rule.get('max_visits', DEFAULT_VISITS)
    exact = rule['match'] == 'exact'
    expected = Counter(r['class'] for r in roles)
    actual = Counter(c['class'] for c in graph.classified.values())
    kinds = {cls: {'expected': expected[cls], 'actual': actual[cls]}
             for cls in sorted(set(expected) | (set(actual) if exact else set()))}
    unresolved = sorted(graph.unresolved)
    record.update(match=rule['match'], kind_counts=kinds, max_visits=bound, unclassified=unresolved)
    over = [c for c, n in kinds.items() if exact and n['actual'] > n['expected']]
    under = [c for c, n in kinds.items() if n['actual'] < n['expected']]
    if over or (under and not unresolved):
        record.update(status='violation', reasons=[f'component kind counts differ: ' + ', '.join(
            f'{c} expected {kinds[c]["expected"]} actual {kinds[c]["actual"]}' for c in sorted(over + under))])
        return record
    if under:
        return unknown(record, f'kind counts short for {under}; unclassified populated parts {unresolved} may supply them')
    fixed, reasons = {}, []
    for net_role, name in sorted(rule.get('fixed_nets', {}).items()):
        nid, reason = graph.net(name)
        if reason:
            reasons.append(reason)
        else:
            fixed[net_role] = nid
    if reasons:
        return unknown(record, *reasons)
    if len(set(fixed.values())) != len(fixed):
        return unknown(record, 'fixed_nets resolve two net roles to one native net')

    def candidates(optimistic):
        table, unread = {}, set()
        for role in roles:
            wanted = quantity(role['value']) if 'value' in role else None
            options = []
            for ref, comp in sorted(graph.classified.items()):
                if comp['class'] != role['class'] or role.get('symbol', comp['symbol']) != comp['symbol']:
                    continue
                if not set(role['terminals']) <= set(comp['terminals']):
                    continue
                flags = {}
                if wanted is not None:
                    got = quantity(comp['value'])
                    if got is None:
                        if not optimistic:
                            continue
                        flags['value_unknown'] = True
                        unread.add(ref)
                    elif not same_quantity(got, wanted):
                        continue
                names = list(comp['terminals'])
                options.append((ref, comp['terminals'], dict(flags, swapped=False)))
                if role['class'] in SWAPPABLE and len(names) == 2:
                    swapped = {names[0]: comp['terminals'][names[1]], names[1]: comp['terminals'][names[0]]}
                    options.append((ref, swapped, dict(flags, swapped=True)))
            table[role['role']] = options
        return table, unread

    visits = [0]
    try:
        strict, deepest = search(roles, candidates(False)[0], fixed, bound, visits)
        loose, unread = (None, set()) if strict else candidates(True)
        optimistic = None
        if strict is None and unread:
            optimistic, deepest = search(roles, loose, fixed, bound, visits)
    except Exhausted:
        record['visits'] = visits[0]
        return unknown(record, f'matcher visit bound {bound} exhausted before a decision')
    record['visits'] = visits[0]
    found = strict or optimistic
    if found:
        assignment, net_map = found
        record['mapping'] = {'roles': {role: ref for role, (ref, _) in sorted(assignment.items())},
                             'swapped': sorted(role for role, (_, f) in assignment.items() if f['swapped']),
                             'nets': {net_role: graph.name(nid) for net_role, nid in sorted(net_map.items())}}
        if strict is not None and not (exact and unresolved):
            record['status'] = 'satisfied'
        elif strict is not None:
            unknown(record, f'unclassified populated parts {unresolved} prevent an exact match')
        else:
            pending = sorted(ref for ref, f in assignment.values() if f.get('value_unknown'))
            unknown(record, 'match depends on missing or unreadable values: ' + ', '.join(pending))
        return record
    record['deepest_partial'] = dict(sorted(deepest.items()))
    if unresolved and not exact:
        return unknown(record, f'no consistent assignment among classified parts; unclassified {unresolved} may complete it')
    record.update(status='violation', reasons=['no role assignment satisfies the expected connectivity'])
    return record


EVALUATORS = {'pin_net': eval_pin, 'pin_no_connect': eval_pin, 'count_between': eval_between,
              'forbidden_between': eval_between, 'element_between': eval_between,
              'series_element': eval_series, 'value_equal': eval_values, 'expected_components': eval_expected}


def evaluate(document, native):
    graph = Graph(document['classes'], native)
    records = []
    for rule in document['rules']:
        record = {'id': rule['id'], 'requirement': rule['requirement'], 'type': rule['type'],
                  'predicate': 'topology-rule:' + rule['id'], 'status': 'unknown'}
        records.append(EVALUATORS[rule['type']](rule, graph, record))
    violations = [r['id'] for r in records if r['status'] == 'violation']
    unknowns = [r['id'] for r in records if r['status'] == 'unknown']
    outcome = 'defect' if violations else 'unknown' if unknowns else 'pass'
    return {'records': records, 'violations': violations, 'unknown': unknowns,
            'unclassified': {ref: {k: v for k, v in u.items() if k != 'nets'} | {'nets': sorted(graph.name(n) for n in u['nets'])}
                             for ref, u in graph.unresolved.items()},
            'excluded_dnp': graph.dnp,
            'execution': 'unsupported' if outcome == 'unknown' else 'ok', 'outcome': outcome}


def check_topology_rules(ctx):
    from .checks import CheckError
    from .netlist_extra import native_graph
    try:
        path = captured_rules(ctx.params, ctx.manifest['files'], ctx.inputs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        document = load_rules(path)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('malformed', f'topology rule file: {exc}') from exc
    try:
        bind_requirements(document, ctx.scope, ctx.manifest['files'], ctx.cid)
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    prior, xml, connectivity, graph = native_graph(ctx)
    report = evaluate(document, graph)
    report.update(schema=RESULT_SCHEMA, candidate_id=ctx.manifest['candidate_id'], netlist_check=prior['id'],
                  native_xml_sha256=digest(xml), connectivity_sha256=digest(connectivity),
                  rules_sha256=digest(path))
    write_json(ctx.work / 'topology-rules.json', report)
    ctx.evidence('topology-rules.json')
    ctx.result['details'].update(report)
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
        summary=f"Topology rules: {report['outcome']}; {len(report['records'])} rule(s), "
                f"{len(report['violations'])} violation(s) {report['violations']}, "
                f"{len(report['unknown'])} unknown {report['unknown']}")
    ctx.result['limitations'].append('Exported-net structure only, against owner-declared classes and rules. '
        'DNP parts are excluded from population-based rules. No prose parsing, electrical adequacy, '
        'tolerance, proximity, routing or geometry; values are compared only for declared equality.')


def describe_topology_rules(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('topology_rules', describe_topology_rules, check_topology_rules)
