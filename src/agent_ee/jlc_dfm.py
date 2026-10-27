"""Offline binding of an owner-saved JLCPCB DFM report to sealed fabrication bytes."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import argparse
import hashlib
import re
from pathlib import Path, PurePosixPath

from .catalog import exact_fields, nonempty, strict_json
from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.jlc-dfm-report/1'
PREDICATES = ['jlc-dfm:sealed-gerber-binding', 'jlc-dfm:no-errors']
MAX_BYTES = 16 * 1024 * 1024
LIMITATIONS = [
    'Owner-saved normalization and completeness declaration; supplier authenticity and omitted '
    'findings are not independently verified.',
    'DFM evidence covers only the uploaded Gerber zip; assembly preview, stock, electrical '
    'function and physical qualification require separate evidence.',
]


def relative_path(value):
    if (not isinstance(value, str) or not value or PurePosixPath(value).is_absolute()
            or not PurePosixPath(value).parts or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value)) != value):
        raise ValueError('report inputs must be canonical candidate-relative paths')
    return value


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('expected lowercase sha256')
    return value


def validate_params(params):
    from .checks import SAFE_ID
    exact_fields(params, ('fab_check', 'report'))
    if not isinstance(params['fab_check'], str) or not SAFE_ID.fullmatch(params['fab_check']):
        raise ValueError('fab_check must name a safe preceding fab_package check')
    relative_path(params['report'])
    return params


def source_record(document):
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise ValueError(f'expected {SCHEMA}')
    source = document.get('source')
    exact_fields(source, ('path', 'sha256'))
    relative_path(source['path'])
    sha256(source['sha256'])
    return source


def read_bounded(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('report input is a symlink')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError('report input is empty or exceeds 16 MiB')
    return data


def immutable_inputs(params, files, inputs):
    """Discover the retained source through the captured normalization, for scope/2 and /3."""
    validate_params(params)
    report = params['report']
    if report not in files:
        raise ValueError(f'DFM report not captured: {report}')
    if inputs is None:
        raise ValueError('DFM source discovery requires captured input bytes')
    document = strict_json(read_bounded(Path(inputs) / report).decode('utf-8'))
    source = source_record(document)
    if source['path'] == report or source['path'] not in files:
        raise ValueError('DFM source must be a separate captured input')
    return [report, source['path']]


def capability(params, scope, spec, files, inputs):
    paths = immutable_inputs(params, files, inputs)
    prior = scope['checks'][:scope['checks'].index(spec)]
    if not any(s['id'] == params['fab_check'] and s['kind'] == 'fab_package' for s in prior):
        raise ValueError('DFM needs an earlier fab_package check')
    return {'predicates': list(PREDICATES), 'immutable_inputs': paths}


def parse_report(data):
    """Our owner-normalized format; never heuristically interpret vendor HTML or PDFs."""
    document = strict_json(data.decode('utf-8'))
    exact_fields(document, ('schema', 'supplier', 'gerber_zip_sha256', 'source', 'status', 'findings'))
    source_record(document)
    if document['supplier'] != 'JLCPCB':
        raise ValueError('report supplier must be JLCPCB')
    sha256(document['gerber_zip_sha256'])
    if document['status'] != 'complete':
        raise ValueError('DFM report is not complete')
    validate_findings(document['findings'])
    return document


def validate_findings(findings):
    if not isinstance(findings, list) or len(findings) > 10000:
        raise ValueError('findings must be a list with at most 10000 entries')
    seen = set()
    for finding in findings:
        exact_fields(finding, ('id', 'severity', 'message', 'page', 'locator'))
        for key in ('id', 'message', 'locator'):
            nonempty(finding[key])
        if finding['id'] in seen:
            raise ValueError('duplicate DFM finding id')
        seen.add(finding['id'])
        if finding['severity'] not in ('error', 'warning', 'info'):
            raise ValueError('unsupported DFM severity')
        if type(finding['page']) is not int or finding['page'] < 1:
            raise ValueError('finding page must be a positive integer')


def parse_captured(inputs, report, expected):
    """Sandbox worker: hash both immutable inputs and retain their exact bytes."""
    report = relative_path(report)
    data = read_bounded(Path(inputs) / report)
    normalization_sha = hashlib.sha256(data).hexdigest()
    if normalization_sha != expected[report]:
        raise ValueError('DFM normalization differs from captured digest')
    Path('/work/report.json').write_bytes(data)
    document = parse_report(data)
    source = document['source']
    source_data = read_bounded(Path(inputs) / source['path'])
    report_sha = hashlib.sha256(source_data).hexdigest()
    if source['path'] == report or report_sha != source['sha256'] or report_sha != expected[source['path']]:
        raise ValueError('saved DFM source differs from declared or captured digest')
    Path('/work/saved-report').write_bytes(source_data)
    return {'normalization_sha256': normalization_sha, 'report_sha256': report_sha,
            'gerber_zip_sha256': document['gerber_zip_sha256'],
            'source_path': source['path'], 'findings': document['findings']}


def check_jlc_dfm(ctx):
    from .checks import CheckError
    from .fab_package import artifact_record
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.spec, ctx.manifest['files'], ctx.inputs)
    except (OSError, ValueError, UnicodeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    fid = ctx.params['fab_check']
    try:
        prior = read_json(ctx.run_dir / 'checks' / f'{fid}.json')
        if prior.get('id') != fid or prior.get('candidate_id') != ctx.manifest['candidate_id']:
            raise ValueError('fab_package result belongs to another candidate or check')
        artifacts = artifact_record(ctx.run_dir, prior)
        if artifacts != prior.get('details', {}).get('artifacts'):
            raise ValueError('fab_package artifact record differs from retained package')
        if (artifacts['board_path'] != ctx.scope.get('board') or
                artifacts['board_sha256'] != ctx.manifest['files'].get(ctx.scope.get('board'))):
            raise ValueError('fab_package is bound to another board')
        zip_rel = f'work/{fid}/fab/gerbers.zip'
        zip_sha = artifacts['files'][zip_rel]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', f'unverified fab_package prerequisite: {exc}') from exc
    ctx.result['evidence'] += list(artifacts['files'])
    details = ctx.result['details']
    details.update(fab_check=fid, artifacts_id=artifacts['artifacts_id'], gerber_zip=zip_rel,
                   sealed_gerber_zip_sha256=zip_sha)
    expected = {p: ctx.manifest['files'][p] for p in descriptor['immutable_inputs']}
    write_json(ctx.work / 'expected.json', expected)
    ctx.evidence('expected.json')
    try:
        rec = ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.jlc_dfm',
                           ctx.params['report']], timeout=60)
    finally:
        ctx.evidence('parsed.json', 'report.json', 'saved-report')
    if rec['exit_code'] != 0:
        raise CheckError('failed', 'DFM parser exited nonzero')
    parsed = ctx.load_json('parsed.json', 'DFM parser')
    if not isinstance(parsed, dict) or parsed.get('execution') not in ('ok', 'malformed'):
        raise CheckError('malformed', 'DFM parser returned invalid status')
    if parsed['execution'] != 'ok':
        raise CheckError('malformed', parsed.get('error', 'DFM parser returned no facts'))
    try:
        exact_fields(parsed, ('execution', 'facts'))
        facts = parsed['facts']
        exact_fields(facts, ('normalization_sha256', 'report_sha256', 'gerber_zip_sha256',
                             'source_path', 'findings'))
        for key in ('normalization_sha256', 'report_sha256', 'gerber_zip_sha256'):
            sha256(facts[key])
        source_path = descriptor['immutable_inputs'][1]
        if (facts['source_path'] != source_path or
                facts['normalization_sha256'] != expected[ctx.params['report']] or
                facts['report_sha256'] != expected[source_path]):
            raise ValueError('DFM parser returned inconsistent input digests')
        validate_findings(facts['findings'])
        for name, expected_sha in (('report.json', facts['normalization_sha256']),
                                   ('saved-report', facts['report_sha256'])):
            path = ctx.work / name
            if path.is_symlink() or not path.is_file() or digest(path) != expected_sha:
                raise ValueError(f'retained DFM copy missing or changed: {name}')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CheckError('malformed', f'invalid DFM parser output: {exc}') from exc
    details.update(facts)
    details['binding'] = 'mismatch'
    if facts['gerber_zip_sha256'] != zip_sha:
        raise CheckError('unsupported', 'DFM report Gerber zip sha256 differs from sealed fab_package')
    details['binding'] = 'matched'
    findings = [{**f, 'report_sha256': facts['report_sha256'],
                 'evidence_path': f'work/{ctx.cid}/saved-report'} for f in facts['findings']]
    details['findings'] = findings
    errors = [f for f in findings if f['severity'] == 'error']
    warnings = [f for f in findings if f['severity'] == 'warning']
    details.update(errors=len(errors), warnings=len(warnings))
    ctx.result['limitations'] += LIMITATIONS
    if warnings:
        ctx.result['limitations'].append(f'{len(warnings)} DFM warnings are advisory and require owner review.')
    locations = [f"sha256:{f['report_sha256']} page {f['page']} {f['locator']} ({f['id']})" for f in errors]
    ctx.result.update(execution='ok', outcome='defect' if errors else 'pass',
                      location='; '.join(locations) if errors else ctx.params['report'],
                      summary=f'DFM report bound to sealed Gerber zip: {len(errors)} errors, {len(warnings)} warnings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    args = parser.parse_args()
    try:
        facts = parse_captured('/candidate', args.report, read_json('/work/expected.json'))
    except (OSError, ValueError, UnicodeError, KeyError) as exc:
        write_json('/work/parsed.json', {'execution': 'malformed', 'error': str(exc)})
        return
    write_json('/work/parsed.json', {'execution': 'ok', 'facts': facts})


if __name__ == '__main__':
    main()


def describe_jlc_dfm(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.spec, ctx.files, ctx.inputs)
    ctx.prerequisite(ctx.params['fab_check'], ('fab_package',))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('jlc_dfm', describe_jlc_dfm, check_jlc_dfm,
        early_prerequisites=(_requires.placement_clear,), prerequisites=(_requires.stage_progress,))
