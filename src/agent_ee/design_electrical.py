"""Bind authored electrical roles to the existing native-connectivity checks."""

from .design_rules import BINDINGS_SCHEMA, PACK_SCHEMA, validate_bindings, validate_pack
from .storage import read_json, write_json

RULES = (
    ('differential_buses', 'BUS_FAILSAFE', 'diff-bus-failsafe-bias/1',
     'Declared RS-485/RS-422 buses shall be checked for termination and failsafe bias. '
     'Missing bias or verified true-failsafe evidence is a warning, not electrical acceptance.'),
    ('offboard_controls', 'CONTROL_DEFAULT', 'offboard-control-default-state/1',
     'Declared off-board control inputs shall be checked for their agent-selected default bias. '
     'This checks connectivity, not buffering or transient tolerance.'),
)


def selected(design):
    roles = design.get('electrical_roles', {})
    return [rule for rule in RULES if roles.get(rule[0])]


def validate(design):
    if 'electrical_roles' not in design:
        return  # Existing design/1 files remain supported.
    roles = design['electrical_roles']
    if not isinstance(roles, dict) or set(roles) != {r[0] for r in RULES}:
        raise ValueError('declare differential_buses and offboard_controls; use [] only when none apply')
    if any(not isinstance(entries, list) for entries in roles.values()):
        raise ValueError('each electrical role group must be a list')
    bound = {group: entries for group, entries in roles.items() if entries}
    validate_bindings(dict(schema=BINDINGS_SCHEMA, classes={}, roles=bound))
    nets = {term: net for net, terms in design['nets'].items() for term in terms}
    connectors = {c['reference'] for c in design['connectors']}
    for group, entries in bound.items():
        for entry in entries:
            for anchor in entry.values():
                if isinstance(anchor, dict) and set(anchor) == {'reference', 'pin'}:
                    term = f"{anchor['reference']}.{anchor['pin']}"
                    if anchor['reference'] not in design['parts'] or term not in nets:
                        raise ValueError(f"{group}/{entry['id']}: terminal {term} has no declared net")
            if group == 'differential_buses':
                transceiver = entry['transceiver']
                part = design['parts'].get(transceiver['reference'])
                if part is None or part['value'] != transceiver['part_number']:
                    raise ValueError(f"{group}/{entry['id']}: transceiver must match the declared part")
                def net(key):
                    anchor = entry[key]
                    return nets[f"{anchor['reference']}.{anchor['pin']}"]
                if net('positive') == net('negative') or net('rail') == net('ground'):
                    raise ValueError(f"{group}/{entry['id']}: bus ends and supply rails must be distinct")
            else:
                if entry['connector']['reference'] not in connectors:
                    raise ValueError(f"{group}/{entry['id']}: connector must be declared in connectors")
                ic = entry['ic']
                if entry['net'] != nets[f"{ic['reference']}.{ic['pin']}"]:
                    raise ValueError(f"{group}/{entry['id']}: net must name the IC terminal's declared net")
                rail = 'rail' if entry['expected_bias'] == 'pullup' else 'ground'
                if rail not in entry:
                    raise ValueError(f"{group}/{entry['id']}: supply an explicit {rail} terminal")


def emit(design, root):
    rules = selected(design)
    if not rules:
        return
    # Known two-terminal resistor symbols plus explicit custom classifications.
    from .design import device_class
    classes = {part['lib_id']: {'class': 'resistor', 'pins': ['1', '2']}
               for part in design['parts'].values() if device_class(part.get('lib_id')) == 'resistor'}
    for symbol, rule in read_json(root / 'topology-rules.json')['classes'].items():
        classes[symbol] = {'class': rule['class'], 'pins': list(rule['terminals'].values())}
    bindings = dict(schema=BINDINGS_SCHEMA, classes=classes,
                    roles={group: design['electrical_roles'][group] for group, *_ in rules})
    pack = dict(schema=PACK_SCHEMA, id='generated-electrical-roles', version='1',
        provenance=dict(authored_by='agent-ee compiler; role bindings are design-author choices',
                        date='2026-11-15', license='MIT', derived_from=['agent_ee.design_rules']),
        cards=[dict(id=card, version=1, description=description,
                    applies_to=[group], rationale='Assess declared roles beyond pin-to-net parity.',
                    examples=['Terminated bus without bias.' if group == 'differential_buses'
                              else 'Off-board enable without a default pull resistor.'],
                    violations_cause='Review the reported missing bias or unresolved terminal.',
                    priority='important', enforcement={'adapter': adapter}, parameters={})
               for group, card, adapter, description in rules])
    validate_bindings(bindings)
    validate_pack(pack)
    write_json(root / 'electrical-role-bindings.json', bindings)
    write_json(root / 'electrical-role-rules.json', pack)


def limitations(design):
    if 'electrical_roles' not in design:
        return 'Electrical role checks were not selected: this design has no electrical_roles declaration.'
    absent = [group for group, *_ in RULES if not design['electrical_roles'][group]]
    return ('Electrical roles are design-author interpretations, not owner or verified datasheet bindings. '
            'Only declared roles are checked; default-bias warnings do not establish electrical correctness.'
            + (f" Author declared no roles in: {', '.join(absent)}; those rules were not run." if absent else ''))
