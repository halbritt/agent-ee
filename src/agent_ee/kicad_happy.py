"""Qualified installed kicad-happy output, with explicit evaluation coverage.

A run uses the analyzer version that is installed; the image installs the newest
release of the 2.x line when it is built. Each run records that version, the
install record's tag and commit and the installed-file identity with its evidence.
No declared version or digest is required. An unknown output format, and a
finding field that acceptance reads in an unknown shape, are refused.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import hashlib
import json
import math
import shutil
from pathlib import Path

from .kicad_happy_format import (ANALYZERS, SEVERITY_ANALYZERS, SHA256, FormatError, check_install_record,
                                 check_report, release_major)
from .storage import digest, identity, read_json, write_json

# Only these observed calculation records have qualified scalar semantics.
CALCULATIONS = {('schematic', 'RC-DET'): {'cutoff_hz', 'time_constant_s'}}
KINDS = ('kicad_happy', 'kicad_happy_severity_gate')
# The output contract agent-ee reads: the release line and the analyzer envelope formats.
# The image build (docker/kicad-happy/install.py) selects its release with the same file.
CONTRACT = read_json(Path(__file__).resolve().parent / 'data/kicad-happy-contract.json')
OUTPUT_FORMATS = frozenset(CONTRACT['output_formats'])
INSTALLATION_KEYS = {'root', 'version', 'files', 'sha256'}
PROVENANCE_KEYS = {'tag', 'commit', 'install_record'}


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
    if (metadata.get('name') != 'kicad-happy' or not isinstance(metadata.get('version'), str)
            or not metadata['version']):
        raise ValueError('unrecognized kicad-happy plugin metadata')
    return {'version': metadata['version'], 'files': files, 'sha256': identity(files)}


def install_record(root):
    """The image build's record beside the installation (<root>.install.json), or None.

    A plain checkout has no record; its tag and commit are then recorded as unknown.
    The record's fields must be exactly the installer's; the measured path and
    digest are added last, so the file's content never stands for them."""
    root = Path(root)
    path = root.with_name(root.name + '.install.json')
    if not path.is_file():
        return None
    data = path.read_bytes()
    try:
        record = check_install_record(json.loads(data), OUTPUT_FORMATS)
    except ValueError as exc:
        raise ValueError(f'unrecognized kicad-happy install record {path}: {exc}') from exc
    return {**record, 'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}


def installation_record(root):
    """What a run records: version, tag, commit, installed-file identity and install record."""
    installed = installation(root)
    record = install_record(root)
    if record is not None and (record['installation_sha256'], record['version']) != (
            installed['sha256'], installed['version']):
        raise ValueError('kicad-happy install record does not describe the installed files')
    return {'root': str(Path(root)), **installed,
            'tag': record['tag'] if record else None,
            'commit': record['commit'] if record else None,
            'install_record': record}


def legacy_declaration(params):
    """installation_sha256 from scopes written before run-recorded identities.

    Optional and never a requirement: a run records the identity it used instead."""
    if 'installation_sha256' in params and (not isinstance(params['installation_sha256'], str)
                                            or not SHA256.fullmatch(params['installation_sha256'])):
        raise ValueError('installation_sha256, when present, must be a SHA-256 hex digest')
    return params.get('installation_sha256')


def in_release_line(version):
    """True for a release or prerelease (X.Y.Z or X.Y.Z-pre) of the contract's major line."""
    return release_major(version) == CONTRACT['release_major']


def require_release_line(version):
    """Refuse a version outside the contract line, whatever its envelope reports.

    A different major release can change keys under the same envelope format (the
    unreleased v3.0.0 drops the EMC findings' `title` key and still writes 1.4.0),
    so the release line is part of the output contract. Prereleases of the line are
    accepted and recorded; the image build installs releases only."""
    from .checks import CheckError
    if not in_release_line(version):
        raise CheckError('unsupported', f"kicad-happy {version!r} is outside the "
                         f"{CONTRACT['release_major']}.x output contract agent-ee reads")


def normalize(reports, rules):
    from .checks import CheckError
    from .happy_adapters import Native, NotEvaluated, require
    findings = []
    for analyzer, report in reports.items():
        try:
            check_report(analyzer, report, OUTPUT_FORMATS)
        except FormatError as exc:
            raise CheckError('unsupported', str(exc)) from exc
        for index, finding in enumerate(report['findings']):
            locator = {key: finding.get(key, []) for key in ('components', 'nets', 'pins')}
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
                    native_targets = Native(reports)
                    try:
                        for ref in rule['components']:
                            component = native_targets.component(ref)
                            require(component.get('dnp') is False,
                                    'target requires explicit non-DNP evidence')
                    except NotEvaluated as exc:
                        evaluation['reason'] = str(exc)
                        evaluations.append(evaluation)
                        continue
                    failed = ('min' in rule and value < rule['min'] or
                              'max' in rule and value > rule['max'])
                    evaluation.update(status='violation' if failed else 'satisfied', value=value,
                        native_locator=match['native_locator'], raw_output_path=match['raw_output_path'],
                        raw_locator=match['raw_locator'],
                        native_sources=[{'raw_output_path': match['raw_output_path'],
                                         'raw_locator': match['raw_locator']}] + native_targets.locators)
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
        elif {'assumptions', 'assumptions_source'} & rule.keys():
            raise ValueError('assumptions fields require an adapter')
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


def run_analyzers(ctx, *, severity_gate=False, ambient=25.0):
    """Execute the installed sources, record their identity and retain every sandbox artifact."""
    from .checks import CheckError, verified_hierarchy
    analyzers = SEVERITY_ANALYZERS if severity_gate else ANALYZERS
    declared = legacy_declaration(ctx.params)
    root = Path(ctx.params['installation']).expanduser().resolve()
    if not (root / '.claude-plugin/plugin.json').is_file():
        raise CheckError('unavailable', 'kicad-happy installation not found')
    try:
        before = installation_record(root)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    # Recorded first, so a refused release is named in the run's evidence too. The
    # install record's raw bytes are kept so verify can recheck its recorded digest.
    ctx.result['details']['installation'] = before
    write_json(ctx.work / 'installation.json', before)
    ctx.evidence('installation.json')
    if before['install_record'] is not None:
        shutil.copyfile(before['install_record']['path'], ctx.work / 'install-record.json')
        if digest(ctx.work / 'install-record.json') != before['install_record']['sha256']:
            raise CheckError('unsupported', 'kicad-happy install record changed while it was recorded')
        ctx.evidence('install-record.json')
    require_release_line(before['version'])
    if declared is not None:
        ctx.result['details']['declared_installation_sha256'] = declared
        if declared != before['sha256']:
            ctx.result['limitations'].append(
                f"The scope's installation_sha256 {declared} is a retired declaration, not a "
                f"requirement; this run used and recorded kicad-happy {before['version']} "
                f"with installed-file identity {before['sha256']}.")
    verified_hierarchy(ctx)
    request = {'ambient_c': ambient, 'schematic': ctx.candidate_path(ctx.scope['schematic']),
               'pcb': ctx.candidate_path(ctx.scope['board'])}
    if severity_gate:
        request['profile'] = 'severity_gate'
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
    if installation(root) != {key: before[key] for key in ('version', 'files', 'sha256')}:
        raise CheckError('unsupported', 'installed analyzer bytes changed during execution')
    # The sandbox writes in the same work directory; its saved records must be intact.
    if (ctx.load_json('installation.json', 'saved installation record') != before
            or before['install_record'] is not None
            and digest(ctx.work / 'install-record.json') != before['install_record']['sha256']):
        raise CheckError('unsupported', 'saved kicad-happy installation evidence changed during execution')
    executed = ctx.load_json('executed.json', 'executed analyzer helpers')
    if not isinstance(executed, dict) or not executed:
        raise CheckError('unsupported', 'missing executed analyzer helper inventory')
    for rel, sha in executed.items():
        if before['files'].get(rel) != sha:
            raise CheckError('unsupported', 'executed helper differs from installed identity')
    stage = ctx.load_json('stage-status.json', 'analyzer stages')
    if stage.get('execution') != 'ok':
        raise CheckError(stage['execution'], stage['summary'])
    reports = {name: ctx.load_json(f'{name}.json', name) for name in analyzers}
    return reports, executed


def check_kicad_happy(ctx):
    from .checks import CheckError
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
        reports, executed = run_analyzers(ctx, ambient=ambient)
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


def _digest_map(value):
    return (isinstance(value, dict) and bool(value) and all(
        isinstance(rel, str) and rel and isinstance(sha, str) and SHA256.fullmatch(sha)
        for rel, sha in value.items()))


def check_installation_record(recorded, *, succeeded):
    """Refuse a recorded installation that run_analyzers could not have written.

    Every record needs its fields, SHA-256 digests and an identity that its file
    inventory gives. The top-level version, tag, commit and identity must equal the
    nested install record's, which must itself be well formed. A check that succeeded
    must name a version of the contract's release line."""
    keys = set(recorded) if isinstance(recorded, dict) else None
    if keys not in (INSTALLATION_KEYS, INSTALLATION_KEYS | PROVENANCE_KEYS):
        raise ValueError('installation record fields differ from a recorded installation')
    if not isinstance(recorded['root'], str) or not recorded['root'] or not isinstance(recorded['version'], str):
        raise ValueError('installation record lacks its root or version')
    if succeeded and not in_release_line(recorded['version']):
        raise ValueError(f"a successful check names kicad-happy {recorded['version']!r}, outside the release line")
    files = recorded['files']
    if (not _digest_map(files) or '.claude-plugin/plugin.json' not in files
            or recorded['sha256'] != identity(files)):
        raise ValueError('installation record files are not SHA-256 digests giving the recorded identity')
    if keys == INSTALLATION_KEYS:
        return
    nested = recorded['install_record']
    if nested is None:
        if (recorded['tag'], recorded['commit']) != (None, None):
            raise ValueError('installation record names a tag or commit without an install record')
        return
    if not isinstance(nested, dict) or not {'path', 'sha256'} <= set(nested):
        raise ValueError('nested install record lacks its measured path and digest')
    content = install_record_content(nested)
    check_install_record(content, OUTPUT_FORMATS)
    if (not isinstance(nested['path'], str) or not nested['path']
            or not isinstance(nested['sha256'], str) or not SHA256.fullmatch(nested['sha256'])
            or (content['version'], content['tag'], content['commit'], content['installation_sha256'])
            != (recorded['version'], recorded['tag'], recorded['commit'], recorded['sha256'])):
        raise ValueError('installation record and its nested install record disagree')


def install_record_content(nested):
    """The installer-written fields of a recorded install record, without its measurements."""
    return {key: value for key, value in nested.items() if key not in ('path', 'sha256')}


def verify_recorded(run, checks):
    """Check each recorded analyzer installation against the run's own saved evidence.

    A run used the installation it recorded. A later or different installation on the
    verifying host does not change that record, so the host is not consulted. The
    record must be one the recording code could write, and its saved copies, the raw
    install record and the executed-helper inventory must agree with it. A writer who
    rewrites every saved record and seal consistently is not detectable here; that
    needs independent custody of the evidence."""
    run = Path(run)
    for check in checks:
        if check.get('kind') not in KINDS:
            continue
        cid, evidence = check.get('id'), check.get('evidence', [])
        details = check.get('details', {})
        recorded, executed = details.get('installation'), details.get('executed_files')
        succeeded = check.get('execution') == 'ok'
        if recorded is None and executed is None and not succeeded:
            continue
        if recorded is None or succeeded and executed is None:
            raise ValueError(f'kicad-happy check {cid} lacks its installation or executed-helper record')
        try:
            check_installation_record(recorded, succeeded=succeeded)
        except ValueError as exc:
            raise ValueError(f'kicad-happy check {cid}: {exc}') from exc
        def saved(name):
            """The cited saved file's bytes; unreadable or uncited evidence is refused."""
            if f'work/{cid}/{name}' not in evidence:
                raise ValueError(f'kicad-happy check {cid} does not cite its saved {name}')
            try:
                return (run / f'work/{cid}/{name}').read_bytes()
            except OSError as exc:
                raise ValueError(f'kicad-happy check {cid} lacks its saved {name}') from exc

        def parsed(name, data):
            try:
                return json.loads(data)
            except ValueError as exc:
                raise ValueError(f'kicad-happy check {cid} saved {name} is not JSON') from exc

        if parsed('installation.json', saved('installation.json')) != recorded:
            raise ValueError(f'kicad-happy installation recorded by check {cid} differs from its saved evidence')
        if executed is not None and parsed('executed.json', saved('executed.json')) != executed:
            raise ValueError(f'kicad-happy helpers recorded by check {cid} differ from its saved evidence')
        nested = recorded.get('install_record')
        if nested is not None:
            raw = saved('install-record.json')
            if (hashlib.sha256(raw).hexdigest() != nested['sha256']
                    or parsed('install-record.json', raw) != install_record_content(nested)):
                raise ValueError(f'kicad-happy install record of check {cid} differs from its saved raw copy')
        if executed is not None and (not _digest_map(executed)
                                     or any(recorded['files'].get(rel) != sha for rel, sha in executed.items())):
            raise ValueError(f'kicad-happy helpers executed by check {cid} differ from its recorded installation')


def verify_unchanged(checks):
    """Within one run: each recorded installation and install record are still the recorded ones."""
    for check in checks:
        if check.get('kind') not in KINDS:
            continue
        recorded = check.get('details', {}).get('installation')
        if recorded:
            try:
                current = installation_record(recorded['root'])
            except (ValueError, OSError) as exc:
                raise ValueError('kicad-happy installation changed or unavailable during the run') from exc
            if any(current[key] != recorded.get(key) for key in ('version', 'files', 'sha256')):
                raise ValueError('kicad-happy installation bytes changed during the run')
            if (current['install_record'] or {}).get('sha256') != (recorded.get('install_record') or {}).get('sha256'):
                raise ValueError('kicad-happy install record changed during the run')


def describe_capability(params, files):
    """Pure descriptor used by scope/3 coverage; scope/2 is unchanged.

    ``files`` is the captured path->SHA256 inventory. No installed-file read or
    runtime qualification is performed. Only blocking qualified predicates can
    describe acceptance capability; runtime missing target/skip remains unknown.
    """
    descriptor = {'capability_version': 'kicad_happy/1', 'status': 'unsupported',
                  'can_evaluate_bound': False, 'predicates': [], 'immutable_inputs': [],
                  'unsupported_reasons': []}
    try:
        rules, counted, blocking = declarations(params)
        if not isinstance(params.get('installation'), str) or not params['installation']:
            raise ValueError('missing installed analyzer path')
        legacy_declaration(params)
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
                if not isinstance(sha, str) or not SHA256.fullmatch(sha):
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


def describe_kicad_happy(ctx):
    ctx.captured(ctx.scope.get('schematic'))
    ctx.captured(ctx.scope.get('board'))
    descriptor = describe_capability(ctx.params, ctx.files)
    if descriptor['status'] != 'supported':
        raise ValueError('; '.join(descriptor['unsupported_reasons']))
    for dependency in descriptor['immutable_inputs']:
        path = dependency['path']
        if path not in ctx.scope.get('requirements', []):
            raise ValueError('adapter assumptions source must be a captured immutable requirement')
        ctx.captured(path, immutable=True)
    ctx.result['capability_version'] = descriptor['capability_version']
    ctx.result['predicates'] = descriptor['predicates']


if __name__ != '__main__':
    _register_check('kicad_happy', describe_kicad_happy, check_kicad_happy,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
