"""Pure, parameter-aware capability descriptions for explicit scope/3 audits.

These predicates establish executable coverage, not physics qualification or
runtime success. No tool execution, model inference, or uncaptured input reads occur.
"""

import copy
import math
import re
from pathlib import PurePosixPath

from .storage import identity


KINDS = frozenset(('erc', 'drc', 'netlist', 'geometry', 'rc_transient',
                   'measurements', 'coupled_margins', 'datasheet_facts', 'pin_map'))
DOMAINS = frozenset(('electrical', 'thermal', 'parasitic', 'manufacturing', 'supply_chain'))


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be finite numeric')
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def bounds(limits):
    if not isinstance(limits, dict) or not limits:
        raise ValueError('missing nonempty limits')
    predicates = []
    for name, limit in limits.items():
        text(name, 'metric name')
        if not isinstance(limit, dict):
            raise ValueError('limit must be an object')
        for key in ('unit', 'condition'):
            text(limit.get(key), key)
        keys = [key for key in ('min', 'max') if key in limit]
        if not keys:
            raise ValueError(f'{name}: missing bounds')
        for key in keys:
            finite(limit[key], f'{name}:{key}')
            predicates.append(f'measurement:{name}:{key}')
        if len(keys) == 2 and limit['min'] > limit['max']:
            raise ValueError(f'{name}: reversed bounds')
    return predicates


def _geometry(params):
    predicates = []
    if 'criterion' in params or 'covered_primitives' in params:
        if params.get('criterion') != 'copper-width/1':
            raise ValueError('unknown geometry criterion')
        classes = params.get('covered_primitives')
        if not isinstance(classes, list) or sorted(classes) != ['PCB_ARC', 'PCB_TRACK']:
            raise ValueError('copper-width/1 requires exact track/arc coverage')
        if 'exclude_nets' not in params:
            raise ValueError('copper-width/1 requires explicit exclude_nets')
        if 'min_track_width_mm' not in params:
            raise ValueError('missing width limit')
    excluded = params.get('exclude_nets', [])
    if not isinstance(excluded, list) or any(not isinstance(n, str) for n in excluded):
        raise ValueError('exclude_nets must be a list of net names')
    if 'min_track_width_mm' in params:
        if finite(params['min_track_width_mm'], 'width limit') <= 0:
            raise ValueError('width limit must be positive')
        predicates = ['geometry:' + params.get('criterion', 'straight-width/1')]
    return predicates


def _rc_transient(params, prerequisite):
    predicates = []
    prerequisite(params.get('netlist_check'), ('netlist',))
    for key in ('input_net', 'output_net', 'ground_net'):
        text(params.get(key), key)
    step, stop = params.get('step_v', 1.0), params.get('stop_ms', 5.0)
    for label, value in (('step_v', step), ('stop_ms', stop)):
        if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f'{label} must be finite positive')
    measurements = params.get('measurements', [])
    if not isinstance(measurements, list):
        raise ValueError('measurements must be a list')
    names = set()
    for measurement in measurements:
        name = text(measurement.get('name'), 'measurement name').lower()
        if not re.fullmatch('[a-z][a-z0-9_]{0,31}', name) or name in names:
            raise ValueError('invalid/duplicate measurement name')
        names.add(name)
        at = measurement['at_ms']
        if isinstance(at, bool) or not 0 < float(at) <= float(stop):
            raise ValueError('measurement time outside simulation interval')
        limit = {key: measurement[key] for key in ('min', 'max') if measurement.get(key) is not None}
        if limit:
            predicates.extend(bounds({name: {**limit, 'unit': 'V', 'condition': 'step'}}))
    return predicates


def _measurements(params, captured):
    predicates = []
    captured(params['adapter'], immutable=True)
    tool = params['tool']
    argv = tool['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise ValueError('tool requires explicit argv')
    executable = PurePosixPath(argv[0])
    if not executable.is_absolute() or '..' in executable.parts or not executable.is_relative_to('/usr'):
        raise ValueError('tool executable must reside under /usr')
    if not isinstance(tool['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', tool['sha256']):
        raise ValueError('tool needs pinned sha256')
    if tool['domain'] not in DOMAINS:
        raise ValueError('unsupported tool domain')
    inputs = tool.get('inputs', [])
    if not isinstance(inputs, list):
        raise ValueError('tool inputs must be a list')
    for path in inputs:
        captured(path, immutable=True)
    # Candidate program arguments must be owned immutable dependencies.
    for arg in argv:
        if arg.startswith('/candidate/') and arg[len('/candidate/'):] not in inputs:
            raise ValueError('candidate tool argument missing from immutable inputs')
    predicates = bounds(params['limits'])
    return predicates


def _coupled_margins(params, prerequisite):
    members = params['members']
    if not isinstance(members, list) or len(members) < 2:
        raise ValueError('coupling requires at least two members')
    condition = text(params['condition'], 'coupling condition')
    domains, names = set(), set()
    for member in members:
        source = prerequisite(member['check'], ('measurements', 'rc_transient'))
        source_params = source.get('params', {})
        if source['kind'] == 'measurements':
            metric = source_params['limits'][member['metric']]
            domain = source_params['tool']['domain']
        else:
            m = next(m for m in source_params['measurements'] if m['name'].lower() == member['metric'])
            metric = {'unit': 'V', 'condition': f"ideal-unloaded-{source_params.get('step_v', 1.0)}V-step-at-{float(m['at_ms'])}ms"}
            domain = 'electrical'
        if metric['condition'] != condition:
            raise ValueError('coupled source condition mismatch')
        name = text(member['name'], 'member name')
        if name in names:
            raise ValueError('duplicate coupling name')
        names.add(name)
        domains.add(domain)
        target = params['limits'][name]
        if target['unit'] != metric['unit'] or target['condition'] != condition:
            raise ValueError('coupled limit identity mismatch')
    if len(domains) < 2 or names != set(params['limits']):
        raise ValueError('coupling needs distinct domains and complete limits')
    predicates = bounds(params['limits'])
    return predicates


def describe_check(spec, *, check_specs, files, scope, inputs=None):
    """Return a supported descriptor or explicit unsupported reasons.

    Immutable input names must resolve in the captured inventory. Dependencies
    must precede consumers; no automatic sorting can change execution semantics.
    """
    kind = spec.get('kind')
    result = {'check_id': spec['id'], 'kind': kind, 'capability_version': f'{kind}/1',
        'status': 'supported', 'can_evaluate_bound': False, 'predicates': [],
        'prerequisites': [], 'immutable_inputs': [], 'unsupported_reasons': []}
    params = spec.get('params', {})

    def captured(path, *, immutable=False):
        if not isinstance(path, str):
            raise ValueError('captured path must be text')
        rel = PurePosixPath(path)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or str(rel) != path or path not in files:
            raise ValueError(f'input not captured or unsafe: {path}')
        if immutable:
            result['immutable_inputs'].append(path)

    def prerequisite(cid, expected):
        prior = check_specs[:check_specs.index(spec)]
        source = next((s for s in prior if s['id'] == cid), None)
        if source is None or source.get('kind') not in expected:
            raise ValueError(f'unresolved/forward/wrong-kind prerequisite: {cid}')
        result['prerequisites'].append({'check_id': cid, 'kind': source['kind']})
        return source

    try:
        if kind not in KINDS:
            raise ValueError(f'unknown capability kind: {kind}')
        if not isinstance(params, dict):
            raise ValueError('params must be an object')
        if kind != 'geometry' and 'criterion' in params:
            raise ValueError('unknown criterion for check kind')
        if not isinstance(params.get('calibration_inputs', []), list):
            raise ValueError('calibration_inputs must be a list')
        for path in params.get('calibration_inputs', []):
            captured(path, immutable=True)
        if kind in ('erc', 'drc', 'netlist'):
            captured(scope.get('schematic'))
            if kind == 'drc':
                captured(scope.get('board'))
            if kind != 'netlist':
                native_files = [scope['schematic']] + ([scope['board']] if kind == 'drc' else [])
                for native in native_files:
                    for suffix in ('.kicad_pro', '.kicad_dru'):
                        dependency = str(PurePosixPath(native).with_suffix(suffix))
                        if dependency in files:
                            captured(dependency, immutable=True)
                result['predicates'] = [f'native:{kind}:violations']
        elif kind == 'geometry':
            captured(scope.get('board'))
            result['predicates'] = _geometry(params)
        elif kind == 'rc_transient':
            result['predicates'] = _rc_transient(params, prerequisite)
        elif kind == 'measurements':
            result['predicates'] = _measurements(params, captured)
        elif kind == 'coupled_margins':
            result['predicates'] = _coupled_margins(params, prerequisite)
        elif kind in ('datasheet_facts', 'pin_map'):
            from .datasheet_facts import load_facts, validate_extractor
            if inputs is None:
                raise ValueError('datasheet capability requires captured inputs')
            source = spec if kind == 'datasheet_facts' else prerequisite(
                params.get('facts_check'), ('datasheet_facts',))
            source_params = source['params']
            captured(source_params['facts'], immutable=True)
            validate_extractor(source_params['extractor'])
            facts = load_facts(inputs, source_params['facts'])
            for fact in facts:
                captured(fact['source']['path'], immutable=True)
            if kind == 'pin_map':
                prerequisite(params.get('netlist_check'), ('netlist',))
                symbols = params.get('symbols')
                if not isinstance(symbols, list) or not symbols:
                    raise ValueError('pin_map requires explicit symbol applicability')
                by_id = {f['id']: f for f in facts}
                refs = set()
                for symbol in symbols:
                    if not isinstance(symbol, dict) or set(symbol) != {'reference', 'part_number', 'facts'}:
                        raise ValueError('symbol needs reference, part_number and facts')
                    ref = text(symbol['reference'], 'reference')
                    part = text(symbol['part_number'], 'part_number')
                    if ref in refs:
                        raise ValueError('duplicate symbol applicability')
                    refs.add(ref)
                    selected = symbol['facts']
                    if not isinstance(selected, list) or not selected:
                        raise ValueError('symbol requires pin facts')
                    pins = set()
                    for fid in selected:
                        fact = by_id[text(fid, 'fact id')]
                        if fact['kind'] != 'pin_function' or fact['part_number'] != part:
                            raise ValueError('pin fact is inapplicable')
                        if fact['pin'] in pins:
                            raise ValueError('duplicate pin fact')
                        pins.add(fact['pin'])
                        result['predicates'].append(f"pin-map:{ref}:{fact['pin']}")
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OverflowError) as exc:
        result.update(status='unsupported', predicates=[], unsupported_reasons=[{
            'code': 'unknown_kind' if kind not in KINDS else 'invalid_contract',
            'message': str(exc)}])
    result['immutable_inputs'] = sorted(set(result['immutable_inputs']))
    result['can_evaluate_bound'] = result['status'] == 'supported' and bool(result['predicates'])
    return result


def compile_coverage(scope, files, inputs=None):
    specs = scope['checks']
    checks = {s['id']: describe_check(s, check_specs=specs, files=files, scope=scope, inputs=inputs) for s in specs}
    for cid, descriptor in checks.items():
        if descriptor['status'] != 'supported':
            raise ValueError(f"unsupported capability {cid}: {descriptor['unsupported_reasons']}")
    requirements, acceptance_mapped = {}, set()
    for req in scope['baseline']['requirements']:
        intent = req['intent']
        if intent not in ('acceptance', 'informational'):
            raise ValueError('requirement intent must be acceptance or informational')
        eligible = [cid for cid in req['checks'] if checks[cid]['can_evaluate_bound']] if intent == 'acceptance' else []
        if intent == 'acceptance' and not eligible:
            raise ValueError(f"acceptance requirement {req['id']} has no executable bound coverage")
        acceptance_mapped.update(eligible)
        requirements[req['id']] = {'intent': intent, 'mapped_checks': req['checks'], 'acceptance_checks': eligible}
    if not acceptance_mapped:
        raise ValueError('scope/3 requires at least one acceptance requirement')
    if any(c['can_evaluate_bound'] and cid not in acceptance_mapped for cid, c in checks.items()):
        raise ValueError('defect-capable check requires an acceptance mapping')
    report = {'schema': 'agent-ee.coverage/1', 'policy': 'executable-acceptance/1',
              'checks': checks, 'requirements': requirements}
    return {**report, 'coverage_id': identity(report)}


def convert_scope(scope):
    """Return a NEW scope/3; preserve every historical obligation as acceptance.

    Conversion does not certify coverage and never edits retained evidence.
    Scope/1 needs an owner-authored baseline before conversion.
    """
    if scope.get('schema') != 'agent-ee.scope/2':
        raise ValueError('conversion requires scope/2 with explicit baseline requirements')
    converted = copy.deepcopy(scope)
    converted['schema'] = 'agent-ee.scope/3'
    for requirement in converted['baseline']['requirements']:
        if 'intent' in requirement:
            raise ValueError('historical requirement unexpectedly has intent')
        requirement['intent'] = 'acceptance'
    return converted
