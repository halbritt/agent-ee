"""Owner-declared severity policy over four pinned offline analyzer reports."""
import re
from collections import Counter
import shutil
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, text
from .kicad_happy import SEVERITY_ANALYZERS, normalize, run_analyzers
from .netclass_completeness import load_json
from .storage import digest, write_json

KIND = 'kicad_happy_severity_gate'
SCHEMA = 'agent-ee.happy-dispositions/1'
PREDICATES = ['happy-severity:no-errors/1', 'happy-severity:warnings-dispositioned/1']


def capability(params, scope, files, inputs=None):
    if scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('severity gate requires an immutable scope/2 or scope/3 baseline')
    exact_keys(params, 'installation installation_sha256 dispositions')
    text(params['installation'])
    if not Path(params['installation']).expanduser().is_absolute():
        raise ValueError('installation must be an absolute path')
    if not isinstance(params['installation_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', params['installation_sha256']):
        raise ValueError('installation_sha256 requires a SHA256 pin')
    path = str(safe_relative(params['dispositions']))
    if path != params['dispositions'] or path not in files or path not in scope.get('requirements', []):
        raise ValueError('dispositions must be a captured immutable requirement')
    if path in scope.get('mutable_inputs', []):
        raise ValueError('dispositions cannot be mutable')
    if inputs is not None:
        dispositions(load_json(Path(inputs) / path))
    return {'predicates': list(PREDICATES), 'immutable_inputs': [path]}


def dispositions(record):
    exact_keys(record, 'schema dispositions')
    if record['schema'] != SCHEMA or not isinstance(record['dispositions'], list):
        raise ValueError('unrecognized warning dispositions schema')
    entries = {}
    for index, entry in enumerate(record['dispositions']):
        exact_keys(entry, 'finding_id disposition rationale recorded_by')
        fid = text(entry['finding_id'])
        if fid in entries:
            raise ValueError('duplicate warning finding_id disposition')
        if entry['disposition'] not in ('accepted_risk', 'false_positive'):
            raise ValueError('warning disposition must be accepted_risk or false_positive')
        text(entry['rationale'])
        text(entry['recorded_by'])
        entries[fid] = {**entry, 'raw_locator': f'/dispositions/{index}'}
    return entries


def evaluate(reports, recorded):
    """Unknown output wins over known violations; never infer missing findings."""
    from .checks import CheckError
    if set(reports) != set(SEVERITY_ANALYZERS):
        raise CheckError('unsupported', 'severity gate requires all four analyzer reports')
    for name, report in reports.items():
        if isinstance(report, dict) and (report.get('execution', 'ok') != 'ok'
                or report.get('status', 'ok') not in ('ok', 'complete')):
            raise CheckError('unsupported', f'unknown {name} analyzer result')
    findings, _ = normalize(reports, [])
    for name, report in reports.items():
        summary = report.get('summary')
        counts = Counter(f['severity'] for f in report['findings'])
        if (not isinstance(summary, dict)
                or type(summary.get('total_findings')) is not int
                or summary['total_findings'] != len(report['findings'])
                or not isinstance(summary.get('by_severity'), dict)
                or set(summary['by_severity']) != {'error', 'warning', 'info'}
                or any(type(value) is not int or value != counts[severity]
                       for severity, value in summary['by_severity'].items())
                or summary.get('suppressed', 0) != 0
                or 'stage_filter' in report):
            raise CheckError('unsupported', f'incomplete, filtered or contradictory {name} severity summary')
    seen = set()
    errors, missing = [], []
    for finding in findings:
        native = finding['native']
        fid = native.get('finding_id')
        if (not isinstance(fid, str) or not fid.startswith(finding['analyzer'] + ':')
                or any(not p.strip() for p in fid.split(':'))
                or fid in seen or native.get('suppressed') or native.get('status') in ('unknown', 'not_evaluated')):
            raise CheckError('unsupported', 'missing, duplicate, suppressed or unknown native finding identity')
        seen.add(fid)
        finding['finding_id'] = fid
        if finding['severity'] == 'error':
            errors.append(fid)
        elif finding['severity'] == 'warning':
            if fid in recorded:
                finding['disposition'] = dict(recorded[fid])
            else:
                missing.append(fid)
    return {'findings': findings, 'error_finding_ids': errors,
            'undispositioned_warning_ids': missing,
            'unused_disposition_ids': sorted(set(recorded) - {
                f['finding_id'] for f in findings if f['severity'] == 'warning'})}


def check_severity_gate(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
        source = ctx.params['dispositions']
        record = load_json(ctx.inputs / source)
        recorded = dispositions(record)
        # A check-owned copy is cited and sealed by the existing evidence model.
        shutil.copyfile(ctx.inputs / source, ctx.work / 'dispositions.json')
        ctx.evidence('dispositions.json')
        ctx.result['details']['dispositions_source'] = {'path': source,
            'sha256': digest(ctx.inputs / source), 'evidence_path': f'work/{ctx.cid}/dispositions.json'}
        reports, executed = run_analyzers(ctx, severity_gate=True)
        result = {'candidate_id': ctx.manifest['candidate_id'], **evaluate(reports, recorded)}
        for finding in result['findings']:
            if 'disposition' in finding:
                finding['disposition']['raw_output_path'] = 'dispositions.json'
        write_json(ctx.work / 'normalized.json', result)
        ctx.evidence('normalized.json')
        ctx.result['details'].update(normalized=result, executed_files=executed)
        failed = result['error_finding_ids'] + result['undispositioned_warning_ids']
        ctx.result.update(execution='ok', outcome='defect' if failed else 'pass',
            summary=f"{len(result['error_finding_ids'])} error finding(s); "
                    f"{len(result['undispositioned_warning_ids'])} warning(s) without dispositions")
        if failed:
            ctx.result['location'] = '; '.join(failed)
        ctx.result['limitations'].append('Severity policy over installed heuristic findings only; '
            'not electrical or EMC certification, complete rule coverage, thermal analysis or SPICE. '
            'Recorded warning dispositions do not waive errors or central adjudication.')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
