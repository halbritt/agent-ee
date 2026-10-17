"""Qualified installed kicad-happy output, with explicit evaluation coverage."""
import math
from pathlib import Path

from .storage import digest, identity, read_json, write_json

ANALYZERS = {
    'schematic': 'skills/kicad/scripts/analyze_schematic.py',
    'pcb': 'skills/kicad/scripts/analyze_pcb.py',
    'thermal': 'skills/kicad/scripts/analyze_thermal.py',
    'emc': 'skills/emc/scripts/analyze_emc.py',
}
# Only these observed calculation records have qualified scalar semantics.
CALCULATIONS = {('schematic', 'RC-DET'): {'cutoff_hz', 'time_constant_s'}}


def installation(root):
    """Conservative identity includes data and unexecuted helpers as well as code."""
    root = Path(root)
    files = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if rel.parts[0] not in ('skills', '.claude-plugin') or any(
                p in ('__pycache__', '.pytest_cache') for p in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError('analyzer installation contains a symlink: ' + str(rel))
        if path.is_file():
            files[rel.as_posix()] = digest(path)
    metadata = read_json(root / '.claude-plugin/plugin.json')
    if metadata.get('name') != 'kicad-happy' or metadata.get('version') != '2.3.1':
        raise ValueError('only installed kicad-happy 2.3.1 is qualified')
    return {'version': metadata['version'], 'files': files, 'sha256': identity(files)}


def normalize(reports, rules):
    from .checks import CheckError
    findings = []
    for analyzer, report in reports.items():
        if (not isinstance(report, dict) or report.get('schema_version') != '1.4.0'
                or report.get('analyzer_type') != analyzer
                or not isinstance(report.get('findings'), list)):
            raise CheckError('unsupported', f'unrecognized {analyzer} analyzer schema')
        for index, finding in enumerate(report['findings']):
            if (not isinstance(finding, dict) or not isinstance(finding.get('rule_id'), str)
                    or finding.get('severity') not in ('error', 'warning', 'info')):
                raise CheckError('unsupported', f'unrecognized {analyzer} finding schema')
            locator = {}
            for key in ('components', 'nets', 'pins'):
                value = finding.get(key, [])
                if not isinstance(value, list):
                    raise CheckError('unsupported', f'unrecognized native locator {key}')
                locator[key] = value
            findings.append({'analyzer': analyzer, 'rule_id': finding['rule_id'],
                'severity': finding['severity'], 'native_locator': locator,
                'raw_output_path': f'{analyzer}.json', 'raw_locator': f'/findings/{index}',
                'native': finding})
    evaluations = []
    for rule in rules:
        if 'adapter' in rule:
            from .happy_adapters import evaluate
            evaluations.append(evaluate(reports, rule))
            continue
        matches = [f for f in findings if f['analyzer'] == rule['analyzer']
            and f['rule_id'] == rule['rule_id']
            and sorted(f['native_locator']['components']) == sorted(rule['components'])]
        evaluation = {'id': rule['id'], 'status': 'not_evaluated'}
        if len(matches) == 1:
            match = matches[0]
            native = match['native']
            if (rule.get('field') in CALCULATIONS.get((rule['analyzer'], rule['rule_id']), set())
                    and native.get('type') == 'low-pass' and not native.get('suppressed')):
                value = native.get(rule['field'])
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    failed = ('min' in rule and value < rule['min'] or
                              'max' in rule and value > rule['max'])
                    evaluation.update(status='violation' if failed else 'satisfied', value=value,
                        native_locator=match['native_locator'], raw_output_path=match['raw_output_path'],
                        raw_locator=match['raw_locator'])
        evaluations.append(evaluation)
    return findings, evaluations


def declarations(params):
    rules = params.get('rules')
    counted, blocking = params.get('counted_rules'), params.get('blocking_rules')
    if not isinstance(rules, list) or not isinstance(counted, list) or not isinstance(blocking, list):
        raise ValueError('rules, counted_rules and blocking_rules must be explicit lists')
    ids = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {'id', 'analyzer', 'rule_id', 'components', 'field', 'min', 'max',
                'adapter', 'assumptions', 'assumptions_source'}:
            raise ValueError('invalid rule declaration')
        for key in ('id', 'analyzer', 'rule_id'):
            if not isinstance(rule.get(key), str) or not rule[key]:
                raise ValueError('rule requires ' + key)
        if rule['analyzer'] not in ANALYZERS:
            raise ValueError('unknown analyzer')
        if (not isinstance(rule.get('components'), list) or not rule['components']
                or any(not isinstance(c, str) or not c for c in rule['components'])
                or len(set(rule['components'])) != len(rule['components'])):
            raise ValueError('rule requires unique native component references')
        if 'adapter' in rule:
            from .happy_adapters import validate
            validate(rule)
        elif not isinstance(rule.get('field'), str) or not {'min', 'max'} & rule.keys():
            raise ValueError('rule requires a calculation field and bounds')
        for key in ('min', 'max'):
            if key in rule and (isinstance(rule[key], bool) or not isinstance(rule[key], (int, float))
                                or not math.isfinite(rule[key])):
                raise ValueError('rule bounds must be finite numbers')
        if 'min' in rule and 'max' in rule and rule['min'] > rule['max']:
            raise ValueError('reversed rule bounds')
        ids.append(rule['id'])
    if (len(set(ids)) != len(ids) or any(not isinstance(i, str) for i in counted + blocking)
            or len(set(counted)) != len(counted) or len(set(blocking)) != len(blocking)
            or not set(blocking) <= set(counted) <= set(ids)):
        raise ValueError('blocking rules must be unique declared counted rules')
    return rules, counted, blocking


def check_kicad_happy(ctx):
    from .checks import CheckError, verified_hierarchy
    try:
        rules, counted, blocking = declarations(ctx.params)
        for rule in rules:
            if 'adapter' in rule:
                source = rule['assumptions_source']['path']
                if source not in ctx.scope.get('requirements', []):
                    raise ValueError('adapter assumptions source must be a captured immutable requirement')
                ctx.candidate_path(source)
        ambient = ambient_temperature(ctx.params)
        for rule in rules:
            if rule.get('adapter') == 'thermal-ldo-estimate/1' and rule['assumptions']['ambient_c'] != ambient:
                raise ValueError('thermal rule ambient differs from immutable invocation')
        root = Path(ctx.params['installation']).expanduser().resolve()
        if not (root / '.claude-plugin/plugin.json').is_file():
            raise CheckError('unavailable', 'kicad-happy installation not found')
        try:
            before = installation(root)
        except ValueError as exc:
            raise CheckError('unsupported', str(exc)) from exc
        if before['sha256'] != ctx.params['installation_sha256']:
            raise CheckError('unsupported', 'installed analyzer bytes differ from declared pin')
        ctx.result['details']['installation'] = {'root': str(root), **before}
        write_json(ctx.work / 'installation.json', {'root': str(root), **before})
        ctx.evidence('installation.json')
        verified_hierarchy(ctx)
        request = {'ambient_c': ambient, 'schematic': ctx.candidate_path(ctx.scope['schematic']),
                   'pcb': ctx.candidate_path(ctx.scope['board'])}
        write_json(ctx.work / 'request.json', request)
        write_json(ctx.work / 'config.json', {'version': 1, 'project': {}, 'suppressions': []})
        ctx.evidence('request.json', 'config.json')
        try:
            ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.kicad_happy_runner'], 600,
                extra_ro=[(root, '/analyzer'), (ctx.work / 'request.json', '/work/request.json'),
                          (ctx.work / 'config.json', '/work/config.json')])
        finally:
            for path in sorted(ctx.work.rglob('*')):
                if path.is_file():
                    ctx.evidence(path.relative_to(ctx.work).as_posix())
        if installation(root) != before:
            raise CheckError('unsupported', 'installed analyzer bytes changed during execution')
        executed = ctx.load_json('executed.json', 'executed analyzer helpers')
        if not isinstance(executed, dict) or not executed:
            raise CheckError('unsupported', 'missing executed analyzer helper inventory')
        for rel, sha in executed.items():
            if before['files'].get(rel) != sha:
                raise CheckError('unsupported', 'executed helper differs from installed identity')
        stage = ctx.load_json('stage-status.json', 'analyzer stages')
        if stage.get('execution') != 'ok':
            raise CheckError(stage['execution'], stage['summary'])
        reports = {name: ctx.load_json(f'{name}.json', name) for name in ANALYZERS}
        findings, evaluations = normalize(reports, rules)
        normalized = {'candidate_id': ctx.manifest['candidate_id'],
                      'findings': findings, 'evaluations': evaluations,
                      'counted_rules': counted, 'blocking_rules': blocking}
        write_json(ctx.work / 'normalized.json', normalized)
        ctx.evidence('normalized.json')
        ctx.result['details'].update(normalized=normalized, executed_files=executed)
        missing = [e['id'] for e in evaluations if e['id'] in counted and e['status'] == 'not_evaluated']
        if missing:
            raise CheckError('unsupported', 'declared rules not evaluated: ' + ', '.join(missing))
        failed = [e for e in evaluations if e['id'] in blocking and e['status'] == 'violation']
        ctx.result.update(execution='ok', outcome='defect' if failed else 'pass' if blocking else 'info',
            summary='analyzer blocking rules violated: ' + ', '.join(e['id'] for e in failed)
                    if failed else 'declared analyzer calculations satisfy bounds' if blocking
                    else 'analyzer facts retained; no blocking rules declared')
        if failed:
            ctx.result['location'] = '; '.join(','.join(e['native_locator']['components']) for e in failed)
        ctx.result['limitations'].append('Heuristic analyzer output is not electrical, thermal or EMC certification; '
            'absent rule findings never establish evaluated coverage. Qualified adapters evaluate only their explicit target, topology and immutable assumptions; other native findings remain informative.')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc


def verify_installations(checks):
    for check in checks:
        if check.get('kind') != 'kicad_happy':
            continue
        recorded = check.get('details', {}).get('installation')
        if recorded:
            try:
                current = installation(recorded['root'])
            except (ValueError, OSError) as exc:
                raise ValueError('kicad-happy installation changed or unavailable') from exc
            if current != {k: v for k, v in recorded.items() if k != 'root'}:
                raise ValueError('kicad-happy installation bytes changed since review')


def describe_capability(params, files):
    """Pure descriptor used by scope/3 coverage; scope/2 is unchanged.

    ``files`` is the captured path->SHA256 inventory. No installed-file read or
    runtime qualification is performed. Only blocking qualified predicates can
    describe acceptance capability; runtime missing target/skip remains unknown.
    """
    import re
    descriptor = {'capability_version': 'kicad_happy/1', 'status': 'unsupported',
                  'can_evaluate_bound': False, 'predicates': [], 'immutable_inputs': [],
                  'unsupported_reasons': []}
    try:
        rules, counted, blocking = declarations(params)
        if (not isinstance(params.get('installation'), str) or not params['installation']
                or not isinstance(params.get('installation_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', params['installation_sha256'])):
            raise ValueError('missing pinned installed analyzer identity')
        ambient = ambient_temperature(params)
        predicates, immutable = [], {}
        for rule in rules:
            if 'adapter' not in rule:
                if rule['field'] not in CALCULATIONS.get((rule['analyzer'],rule['rule_id']),set()):
                    raise ValueError('unqualified native calculation declaration')
                name = 'low-pass-rc/1'
            else:
                name = rule['adapter']
                path = rule['assumptions_source']['path']
                sha = files.get(path)
                if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}',sha):
                    raise ValueError('missing captured immutable assumption source')
                immutable[path] = sha
                if name == 'thermal-ldo-estimate/1' and rule['assumptions']['ambient_c'] != ambient:
                    raise ValueError('thermal ambient differs from invocation')
            if rule['id'] in blocking:
                predicates.append(f"happy:{rule['id']}:{name}")
        descriptor.update(status='supported', can_evaluate_bound=bool(predicates), predicates=predicates,
                          immutable_inputs=[{'path':p,'sha256':sha} for p,sha in sorted(immutable.items())])
    except (ValueError, KeyError, TypeError) as exc:
        descriptor['unsupported_reasons'] = [str(exc)]
    return descriptor


def ambient_temperature(params):
    ambient = params.get('ambient_c',25.0)
    if (isinstance(ambient,bool) or not isinstance(ambient,(int,float))
            or not math.isfinite(ambient) or not -55 <= ambient <= 150):
        raise ValueError('ambient_c requires finite temperature in [-55,150] C')
    return ambient
