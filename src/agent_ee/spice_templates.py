"""Role-keyed SPICE measurement templates expanded into explicit active-spice/1 requests.

A captured, versioned template library describes the stimulus, loads, analyses
and measurements for a circuit role. The role, operating conditions and terminal
bindings come from a captured reference-architecture record, terminals resolve
through hardware_interface named-net bindings and every bound comes from a
captured targets record. Nothing is defaulted: a value string that does not
parse exactly makes the measurements that use it unsupported.
"""
import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .candidate import safe_relative
from .storage import digest

LIBRARY_SCHEMA = 'agent-ee.spice-templates/1'
ARCHITECTURE_SCHEMA = 'agent-ee.spice-architecture/1'
TARGETS_SCHEMA = 'agent-ee.spice-targets/1'
REPORT_SCHEMA = 'agent-ee.spice-template-expansion/1'
NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
UNITS = ('V', 'A', 's', 'Hz', 'V/V')
PREFIXES = {'': 0, 'p': -12, 'n': -9, 'u': -6, 'm': -3, 'k': 3, 'M': 6, 'G': 9}
QUANTITY = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?) ?([pnumkMG]?)(V/V|V|A|s|Hz|dB)')
NET_FIELDS = ('positive_net', 'negative_net', 'output_positive_net', 'output_negative_net',
              'input_positive_net', 'input_negative_net')


class Unsupported(ValueError):
    """A declared value or binding that cannot be interpreted exactly."""


def parse_quantity(value, unit):
    """Exact SI parse of one unit-bearing string; V/V also accepts dB (20 log10)."""
    if not isinstance(value, str):
        raise Unsupported(f'{value!r} is not a unit-bearing value string')
    match = QUANTITY.fullmatch(value)
    if not match:
        raise Unsupported(f'cannot interpret {value!r} exactly as {unit}')
    mantissa, prefix, written = match.groups()
    ratio = written in ('V/V', 'dB')
    if (unit == 'V/V') != ratio or (ratio and prefix) or (not ratio and written != unit):
        raise Unsupported(f'cannot interpret {value!r} exactly as {unit}')
    decibels = written == 'dB'
    try:
        scaled = Decimal(mantissa).scaleb(PREFIXES[prefix])
        number = 10 ** (float(scaled) / 20) if decibels else float(scaled)
    except (InvalidOperation, OverflowError) as exc:
        raise Unsupported(f'{value!r} exceeds finite numeric range') from exc
    if not math.isfinite(number) or (number == 0 and scaled != 0):
        raise Unsupported(f'{value!r} exceeds finite numeric range')
    return number


def exact(record, required, optional=()):
    if not isinstance(record, dict) or not set(required) <= record.keys() or set(record) - set(required) - set(optional):
        raise ValueError('template declaration has missing or unsupported fields')
    return record


def names(mapping, label):
    if not isinstance(mapping, dict) or not mapping or any(not isinstance(k, str) or not NAME.fullmatch(k) for k in mapping):
        raise ValueError(f'{label} needs a nonempty mapping with safe names')
    return mapping


def validate_reference(reference):
    exact(reference, ('library', 'architecture', 'targets', 'interface', 'block'))
    for key in ('library', 'architecture', 'targets', 'interface'):
        if not isinstance(reference[key], str):
            raise ValueError(f'template {key} must be a captured relative path')
        safe_relative(reference[key])
    if not isinstance(reference['block'], str) or not NAME.fullmatch(reference['block']):
        raise ValueError('template block must be a safe identifier')
    return reference


def is_reference(node):
    return isinstance(node, dict) and bool({'terminal', 'condition', 'target'} & node.keys())


def check_references(node, terminals, place):
    """Validate reference shapes and where each kind may appear in a template."""
    if is_reference(node):
        if 'terminal' in node:
            exact(node, ('terminal',))
            if place != 'net' or node['terminal'] not in terminals:
                raise ValueError('terminal references must name a declared template terminal in a net field')
            return
        kind = 'condition' if 'condition' in node else 'target'
        exact(node, (kind, 'unit'), ('negate',) if kind == 'target' else ())
        if not isinstance(node[kind], str) or not NAME.fullmatch(node[kind]) or node['unit'] not in UNITS:
            raise ValueError(f'{kind} reference needs a safe name and a supported unit')
        if node.get('negate', True) is not True:
            raise ValueError('negate may only be true')
        allowed = {'bound': ('target',), 'stimulus': ('condition',), 'measurement': ('condition', 'target')}
        if kind not in allowed.get(place, ()):
            raise ValueError('bounds come only from targets and stimulus only from conditions')
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = ('net' if key in NET_FIELDS else 'bound' if key in ('min', 'max') else
                     'measurement' if place in ('measurement', 'bound') else place)
            check_references(value, terminals, child)
    elif isinstance(node, list):
        for value in node:
            check_references(value, terminals, place)
    elif place in ('bound', 'net'):
        # Literal limits or native net names would bypass targets and interface bindings.
        raise ValueError('template bounds and nets must be target and terminal references')


def validate_library(document):
    exact(document, ('schema', 'version', 'templates'))
    if document['schema'] != LIBRARY_SCHEMA or not isinstance(document['version'], str) or not document['version'].strip():
        raise ValueError(f'template library must be {LIBRARY_SCHEMA} with a version')
    for role, template in names(document['templates'], 'templates').items():
        exact(template, ('description', 'terminals', 'sources', 'analyses'), ('current_sources',))
        terminals = template['terminals']
        if (not isinstance(terminals, list) or not terminals or len(set(terminals)) != len(terminals)
                or any(not isinstance(t, str) or not NAME.fullmatch(t) for t in terminals)):
            raise ValueError(f'{role}: terminals must be unique safe names')
        for key in ('sources', 'current_sources'):
            if not isinstance(template.get(key, []), list):
                raise ValueError(f'{role}: {key} must be a list')
            check_references(template.get(key, []), terminals, 'stimulus')
        identities, measured = set(), set()
        for entry in template['sources'] + template.get('current_sources', []):
            if not isinstance(entry, dict) or entry.get('id') in identities:
                raise ValueError(f'{role}: source identifiers must be unique')
            identities.add(entry.get('id'))
        if not isinstance(template['analyses'], list) or not template['analyses']:
            raise ValueError(f'{role}: analyses must be a nonempty list')
        analyses = set()
        for analysis in template['analyses']:
            if (not isinstance(analysis, dict) or not isinstance(analysis.get('measurements'), list)
                    or not isinstance(analysis.get('id'), str) or analysis['id'] in analyses):
                raise ValueError(f'{role}: analyses need unique identifiers and measurement lists')
            analyses.add(analysis['id'])
            check_references({k: v for k, v in analysis.items() if k != 'measurements'}, terminals, 'stimulus')
            for measurement in analysis['measurements']:
                if not isinstance(measurement, dict) or measurement.get('name') in measured:
                    raise ValueError(f'{role}: measurement names must be unique')
                measured.add(measurement.get('name'))
                check_references(measurement, terminals, 'measurement')
                if not {'min', 'max'} & measurement.keys():
                    raise ValueError(f'{role}: template measurements need target bounds')
    return document


def validate_architecture(document):
    exact(document, ('schema', 'blocks'))
    if document['schema'] != ARCHITECTURE_SCHEMA:
        raise ValueError(f'reference architecture must be {ARCHITECTURE_SCHEMA}')
    for block in names(document['blocks'], 'blocks').values():
        exact(block, ('role', 'terminals', 'conditions'))
        if not isinstance(block['role'], str) or not NAME.fullmatch(block['role']):
            raise ValueError('architecture role must be a safe name')
        for key in ('terminals', 'conditions'):
            if any(not isinstance(v, str) for v in names(block[key], key).values()):
                raise ValueError(f'architecture {key} must map names to strings')
    return document


def validate_targets(document):
    exact(document, ('schema', 'blocks'))
    if document['schema'] != TARGETS_SCHEMA:
        raise ValueError(f'targets must be {TARGETS_SCHEMA}')
    for block in names(document['blocks'], 'blocks').values():
        if any(not isinstance(v, str) for v in names(block, 'targets').values()):
            raise ValueError('targets must map names to value strings')
    return document


def captured(inputs, files, rel):
    from .hardware_interface import unique_object
    rel = str(safe_relative(rel))
    if inputs is None:
        raise ValueError(f'template input is missing, linked or changed: {rel}')
    path = Path(inputs) / rel
    if (rel not in files or any(p.is_symlink() for p in [path, *path.parents])
            or not path.is_file() or digest(path) != files[rel]):
        raise ValueError(f'template input is missing, linked or changed: {rel}')
    return rel, json.loads(path.read_text(), object_pairs_hook=unique_object)


def resolve_terminals(template, block, contract):
    resolved = {}
    for name in template['terminals']:
        terminal_id = block['terminals'].get(name)
        terminal = contract['terminals'].get(terminal_id)
        candidates = [i['names'] for i in contract['invariants']
                      if i['kind'] == 'named_net' and i['terminal'] == terminal_id]
        if terminal_id is None:
            resolved[name] = {'unresolved': 'architecture does not bind this template terminal'}
        elif not terminal or 'unresolved' in terminal:
            resolved[name] = {'interface_terminal': terminal_id, 'unresolved': 'interface terminal is not bound'}
        elif len(candidates) != 1 or len(candidates[0]) != 1:
            resolved[name] = {'interface_terminal': terminal_id,
                              'unresolved': 'needs exactly one named_net invariant with one native net name'}
        else:
            component = contract['components'][terminal['component']]
            resolved[name] = {'interface_terminal': terminal_id, 'component': terminal['component'],
                              'references': sorted(l['reference'] for l in component['locations']),
                              'pin': terminal['pin'], 'net': candidates[0][0]}
    return resolved


def substitute(node, terminals, conditions, targets, used):
    if is_reference(node):
        if 'terminal' in node:
            binding = terminals[node['terminal']]
            if 'net' not in binding:
                raise Unsupported(f"terminal {node['terminal']}: {binding['unresolved']}")
            return binding['net']
        kind = 'condition' if 'condition' in node else 'target'
        source = conditions if kind == 'condition' else targets
        if node[kind] not in source:
            raise Unsupported(f'{kind} {node[kind]} is not declared')
        value = parse_quantity(source[node[kind]], node['unit'])
        used.append({kind: node[kind], 'text': source[node[kind]], 'unit': node['unit'], 'value': value})
        return -value if node.get('negate') else value
    if isinstance(node, dict):
        return {k: substitute(v, terminals, conditions, targets, used) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, terminals, conditions, targets, used) for v in node]
    return node


def expand(params, scope, files, inputs):
    """Return (explicit active-spice params or None, expansion report) from captured bytes."""
    from .active_spice import validate_params
    from .hardware_interface import validate_contract
    reference = validate_reference(params['template'])
    baseline = (scope or {}).get('baseline') if isinstance(scope, dict) else None
    if not isinstance(baseline, dict):
        raise ValueError('template mode requires a scope/2 or scope/3 baseline')
    if str(safe_relative(reference['architecture'])) not in baseline.get('reference_architectures', []):
        raise ValueError('template architecture must be a baseline reference architecture')
    if str(safe_relative(reference['targets'])) not in baseline.get('targets', []):
        raise ValueError('template targets must be a baseline target source')
    loaded = {}
    for key, validator in (('library', validate_library), ('architecture', validate_architecture),
                           ('targets', validate_targets), ('interface', validate_contract)):
        rel, document = captured(inputs, files, reference[key])
        loaded[key] = (rel, validator(document))
    library, architecture = loaded['library'][1], loaded['architecture'][1]
    contract, targets = loaded['interface'][1], loaded['targets'][1]
    block_id = reference['block']
    if block_id not in architecture['blocks']:
        raise ValueError(f'block {block_id} is absent from the reference architecture')
    block = architecture['blocks'][block_id]
    report = {'schema': REPORT_SCHEMA, 'block': block_id, 'role': block['role'],
              'library': {'path': loaded['library'][0], 'sha256': files[loaded['library'][0]],
                          'version': library['version']},
              **{key: {'path': loaded[key][0], 'sha256': files[loaded[key][0]]}
                 for key in ('architecture', 'targets', 'interface')},
              'immutable_inputs': sorted(rel for rel, _ in loaded.values())}
    template = library['templates'].get(block['role'])
    if template is None:
        report.update(template=False, terminals={}, measurements=[],
                      summary=f"no template for role {block['role']!r} in library version {library['version']}")
        return None, report
    terminals = resolve_terminals(template, block, contract)
    conditions, limits = block['conditions'], targets['blocks'].get(block_id, {})
    base = {k: v for k, v in params.items() if k != 'template'}
    records, kept = [], []
    try:
        stimulus = {key: substitute(template[key], terminals, conditions, {}, [])
                    for key in ('sources', 'current_sources') if key in template}
        global_reason = None
    except Unsupported as exc:
        stimulus, global_reason = None, f'stimulus: {exc}'
    for analysis in template['analyses']:
        try:
            header = substitute({k: v for k, v in analysis.items() if k != 'measurements'},
                                terminals, conditions, {}, [])
            analysis_reason = global_reason
        except Unsupported as exc:
            header, analysis_reason = None, global_reason or f"analysis {analysis['id']}: {exc}"
        measured = []
        for measurement in analysis['measurements']:
            record = {'name': measurement['name'], 'analysis': analysis['id']}
            used = []
            try:
                if analysis_reason:
                    raise Unsupported(analysis_reason)
                explicit = substitute(measurement, terminals, conditions, limits, used)
                trial = {**base, **stimulus, 'analyses': [{**header, 'measurements': [explicit]}]}
                try:
                    validate_params(trial)
                except (ValueError, KeyError, TypeError) as exc:
                    raise Unsupported(f'expanded declaration refused: {exc}') from exc
                measured.append(explicit)
                record.update(execution='supported', values=used)
            except Unsupported as exc:
                record.update(execution='unsupported', reason=str(exc))
            records.append(record)
        if measured:
            kept.append({**header, 'measurements': measured})
    supported = [r['name'] for r in records if r['execution'] == 'supported']
    report.update(template=True, terminals=terminals, measurements=records,
                  summary=f'{len(supported)} of {len(records)} template measurements supported')
    if not kept:
        return None, report
    explicit = validate_params({**base, **stimulus, 'analyses': kept})
    return explicit, report


def confirm_terminals(report, conn):
    """Interface terminal pins must actually sit on the named native nets used."""
    for name, binding in report['terminals'].items():
        if 'net' not in binding:
            continue
        present = [ref for ref in binding['references'] if ref in conn['components']]
        if len(present) != 1:
            raise ValueError(f'template terminal {name}: interface component is absent or ambiguous natively')
        if f"{present[0]}.{binding['pin']}" not in conn['nets'].get(binding['net'], []):
            raise ValueError(f"template terminal {name}: native pin is not on {binding['net']}")


LIBRARY = Path(__file__).with_name('data') / 'spice-templates.json'


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='python -m agent_ee.spice_templates',
                                     description='Print the shipped role template library for capture.')
    parser.add_argument('command', choices=('library',))
    parser.parse_args(argv)
    validate_library(json.loads(LIBRARY.read_text()))
    sys.stdout.write(LIBRARY.read_text())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
