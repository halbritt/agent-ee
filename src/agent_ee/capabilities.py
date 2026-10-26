"""Pure, parameter-aware capability descriptions for explicit scope/3 audits.

These predicates establish executable coverage, not physics qualification or
runtime success. No tool execution, model inference, or uncaptured input reads occur.
"""

import copy
import math
import re
from pathlib import PurePosixPath

from .storage import identity


from .check_registry import Description, registry

KINDS = registry.keys()

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
        if not registry[kind].accepts_criterion and 'criterion' in params:
            raise ValueError('unknown criterion for check kind')
        if not isinstance(params.get('calibration_inputs', []), list):
            raise ValueError('calibration_inputs must be a list')
        for path in params.get('calibration_inputs', []):
            captured(path, immutable=True)
        registration = registry[kind]
        registration.capability(Description(spec, check_specs, files, scope, inputs, result, captured, prerequisite))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, OverflowError) as exc:
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
    qualified = {cid: c['requirement_qualification'] for cid, c in checks.items() if 'requirement_qualification' in c}
    if qualified:
        # Citations are reported, not coverage: only sourced IDs are qualified; carry-over never satisfies a requirement.
        report['requirement_citations'] = {
            'qualified': sorted({r for q in qualified.values() for r in q['sourced']}),
            'unqualified-carryover': {cid: q['unqualified-carryover'] for cid, q in qualified.items()
                                      if q['unqualified-carryover']},
            'counted_as_coverage': False}
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
