"""Compile captured routing declarations; native evidence runs only in staging.

No solver or design inference: names and physical units are explicit. Each
constraint gets its own DRC invocation so KiCad's last-rule precedence cannot
silently discard overlapping acceptance bounds.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

from .storage import digest, write_json

ID = re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}\Z')
NET = re.compile(r"[^'\"\\\x00-\x1f]+\Z")
RULE_TYPES = {'length': ['length_out_of_range'], 'skew': ['skew_out_of_range'],
              'via_count': ['too_many_vias'], 'clearance': ['clearance']}


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def number(value, label, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value < 0 or
            (integer and not isinstance(value, int))):
        raise ValueError(f'{label} must be finite nonnegative ' + ('integer' if integer else 'numeric'))
    return value


def rule_text(name, condition, constraint):
    return ('(version 1)\n' + f'(rule "{name}"\n'
            + f'  (condition {json.dumps(condition, ensure_ascii=False)})\n  (constraint {constraint}))\n')


def compile_constraint(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get('id'), str) or not ID.fullmatch(spec['id']):
        raise ValueError('constraint requires safe id')
    if not isinstance(spec.get('requirement'), str) or not spec['requirement'].strip():
        raise ValueError('constraint requires mapped requirement')
    nets = spec.get('nets')
    if (not isinstance(nets, list) or not nets or any(not isinstance(n, str) or not NET.fullmatch(n) for n in nets) or
            len(set(nets)) != len(nets)):
        raise ValueError('constraint requires unique explicit safe net names')
    kind = spec.get('kind')
    marker = 'agent_ee_' + spec['id']
    base = {'id': spec['id'], 'requirement': spec['requirement'], 'nets': sorted(nets),
            'kind': kind, 'marker': marker, 'expected': copy.deepcopy(spec)}
    allowed = {'id', 'requirement', 'nets', 'kind'}
    if kind == 'impedance':
        allowed |= {'target_ohm', 'tolerance_ohm', 'stackup', 'solver'}
        number(spec.get('target_ohm'), 'target_ohm')
        if set(spec) - allowed:
            raise ValueError('unknown impedance fields')
        return {**base, 'execution': 'unsupported', 'reason':
                'impedance requires a separately qualified captured stackup and solver; none implemented'}
    if kind not in RULE_TYPES:
        raise ValueError('unsupported routing constraint kind')
    integer = kind == 'via_count'
    suffix = '' if integer else 'mm'
    keys = {'length': ('min_mm', 'max_mm', 'target_mm', 'tolerance_mm'),
            'skew': ('max_mm',), 'via_count': ('max_count',),
            'clearance': ('min_mm',)}[kind]
    allowed.update(keys)
    if set(spec) - allowed:
        raise ValueError('unknown constraint fields')
    limits = {k: number(spec[k], k, integer=integer) for k in keys if k in spec}
    if kind == 'skew' and len(nets) < 2:
        raise ValueError('skew requires at least two nets')
    if kind == 'length' and ('target_mm' in limits or 'tolerance_mm' in limits):
        if set(limits) != {'target_mm', 'tolerance_mm'}:
            raise ValueError('target length requires only target_mm and tolerance_mm')
        target, tolerance = limits['target_mm'], limits['tolerance_mm']
        if tolerance > target:
            raise ValueError('tolerance exceeds target')
        limits = {'min_mm': target - tolerance, 'max_mm': target + tolerance}
    if not limits or ('min_mm' in limits and 'max_mm' in limits and limits['min_mm'] > limits['max_mm']):
        raise ValueError('missing or reversed limits')
    bounds = ' '.join(f'({key.split("_")[0]} {value:.12g}{suffix})'
                      for key, value in sorted(limits.items()))
    condition = ' || '.join(f"A.NetName == '{net}'" for net in sorted(nets))
    rules = rule_text(marker, condition, f'{kind} {bounds}')
    return {**base, 'execution': 'ready', 'rules': rules, 'rules_sha256': sha_text(rules),
            'native_types': list(RULE_TYPES[kind])}


def compile_routing(document):
    if (not isinstance(document, dict) or document.get('schema') != 'agent-ee.routing-rules/1'
            or set(document) != {'schema', 'constraints'} or
            not isinstance(document['constraints'], list) or not document['constraints']):
        raise ValueError('nonempty agent-ee.routing-rules/1 document required')
    records, seen = [], set()
    for spec in document['constraints']:
        compiled = compile_constraint(spec)
        if compiled['id'] in seen:
            raise ValueError('duplicate constraint id')
        seen.add(compiled['id'])
        records.append(compiled)
    return {'schema': 'agent-ee.compiled-routing/1',
            'constraints': sorted(records, key=lambda c: c['id'])}


def captured_file(inputs, files, relative):
    if (not isinstance(relative, str) or not relative or PurePosixPath(relative).is_absolute()
            or '..' in PurePosixPath(relative).parts or str(PurePosixPath(relative)) != relative):
        raise ValueError('captured input needs a complete candidate-relative path')
    path = Path(inputs) / relative
    if path.is_symlink() or not path.resolve().is_relative_to(Path(inputs).resolve()):
        raise ValueError('captured path escapes inputs')
    if relative not in files or not path.is_file() or digest(path) != files[relative]:
        raise ValueError(f'captured file missing or hash changed: {relative}')
    return path


def validate_mappings(constraints, scope, files, cid):
    requirements = {r['id']: r for r in scope.get('baseline', {}).get('requirements', [])}
    declared = {c['requirement'] for c in constraints}
    mapped = {rid for rid, req in requirements.items() if cid in req['checks']}
    if declared != mapped:
        raise ValueError('rule requirement mappings must exactly match baseline check mappings')
    if len(declared) != 1:
        raise ValueError('routing check must bind exactly one requirement; constraints are conjunctive')
    for rid in declared:
        source = requirements[rid]['source']
        if source['path'] not in files or not source.get('locator'):
            raise ValueError('rule requirement needs captured source and locator')


def load_document(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f'cannot read captured routing contract: {exc}') from exc


def capability(params, scope, files, inputs, cid=None):
    if set(params) != {'routing_rules'}:
        raise ValueError('routing check requires only routing_rules')
    captured_file(inputs, files, scope['board'])
    path = captured_file(inputs, files, params['routing_rules'])
    compiled = compile_routing(load_document(path))
    validate_mappings(compiled['constraints'], scope, files, cid)
    immutable = [params['routing_rules']]
    for suffix in ('.kicad_pro', '.kicad_dru'):
        sidecar = str(PurePosixPath(scope['board']).with_suffix(suffix))
        if sidecar in files:
            captured_file(inputs, files, sidecar)
            immutable.append(sidecar)
    return {'compiled': compiled, 'immutable_inputs': immutable,
            'predicates': ['routing:' + c['id'] for c in compiled['constraints']
                           if c['execution'] == 'ready']}


def run_compiled(ctx, compiled, source):
    """Coordinator adapter; all generated artifacts stay in this check workspace."""
    from .checks import CheckError
    try:
        board = captured_file(ctx.inputs, ctx.manifest['files'], ctx.scope['board'])
        project = board.with_suffix('.kicad_pro')
        custom = board.with_suffix('.kicad_dru')
        for sidecar in (project, custom):
            if sidecar.exists():
                captured_file(ctx.inputs, ctx.manifest['files'], sidecar.relative_to(ctx.inputs).as_posix())
        request = {'schema': 'agent-ee.native-rules-request/1', 'board': '/candidate/' + ctx.scope['board'],
                   'compiled': compiled}
        write_json(ctx.work / 'rule-request.json', request)
        ctx.evidence('rule-request.json')
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.routing_native',
                     '/work/rule-request.json'], timeout=900)
        report = ctx.load_json('rule-results.json', 'native routing rules')
        if report.get('schema') != 'agent-ee.native-rules-result/1':
            raise CheckError('malformed', 'native rules result schema mismatch')
        for path in sorted(ctx.work.rglob('*')):
            if path.is_file():
                ctx.evidence(path.relative_to(ctx.work).as_posix())
        records = report['constraints']
        if [r['id'] for r in records] != [c['id'] for c in compiled['constraints']]:
            raise CheckError('malformed', 'native rules result coverage mismatch')
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    complete = all(r['execution'] == 'ok' and r['status'] in ('satisfied', 'violation')
                   for r in records)
    violations = [r['id'] for r in records if r['status'] == 'violation']
    write_json(ctx.work / 'rules-evaluation.json', {
        'schema': 'agent-ee.rules-evaluation/1', 'candidate_id': ctx.manifest['candidate_id'],
        'source': source, 'constraints': records})
    ctx.evidence('rules-evaluation.json')
    ctx.result['details'].update(source=source, constraints=records, violations=violations)
    ctx.result.update(execution='ok' if complete else 'unsupported',
                      outcome=('defect' if violations else 'pass') if complete else 'unknown',
                      summary=f'{len(violations)} rule violation(s); complete={complete}')
    return records


def check_routing_rules(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        source = {'path': ctx.params['routing_rules'],
                  'sha256': digest(ctx.inputs / ctx.params['routing_rules'])}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    run_compiled(ctx, descriptor['compiled'], source)
