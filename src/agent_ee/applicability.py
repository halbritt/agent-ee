"""Versioned exact-design calibration provenance and applicability predicates."""
from pathlib import Path
import shutil

from .storage import digest, identity, read_json, write_json

OBSERVATION_FIELDS = {'check_id', 'metric', 'unit', 'condition', 'value', 'uncertainty', 'artifact', 'locator'}


class ApplicabilityUnavailable(Exception):
    def __init__(self, diagnostic):
        super().__init__('calibrated acceptance requires fresh bench evidence')
        self.diagnostic = diagnostic


def design_inputs(files, baseline_files, empirical_paths):
    if set(baseline_files) & set(empirical_paths):
        raise ValueError('empirical evidence overlaps immutable dependencies')
    return {p: sha for p, sha in files.items()
            if p not in baseline_files and p not in empirical_paths}


def derivation():
    executables = {}
    for command in ('python3', 'kicad-cli', 'ngspice'):
        path = shutil.which(command, path='/usr/local/bin:/usr/bin:/bin')
        if command == 'python3':
            path = '/usr/bin/python3'
        executables[command] = {'path': path, 'sha256': digest(path) if path else None}
    return {'policy': 'exact-design/1', 'residual': 'additive-envelope/1', 'executables': executables,
            'coordinator': {name: digest(Path(__file__).with_name(name))
                            for name in ('applicability.py', 'empirical.py', 'measurements.py', 'checks.py')}}


def obligation(observation):
    return identity({k: observation[k] for k in ('check_id', 'metric', 'unit', 'condition')})


def source_prediction(check):
    return check['execution'] == 'ok' or (
        check['execution'] == 'unavailable' and check['outcome'] == 'unknown' and
        check['details'].get('nominal_prediction') == 'executed-unqualified/1' and
        check['details'].get('calibration_applicability', {}).get('schema') ==
        'agent-ee.recalibration-request/1')


def validate_bundle(inputs, bundle, baseline):
    """Reconstruct the entire evidence closure before using any exclusions."""
    from .empirical import validate_observations
    fields = {'schema', 'candidate_id', 'baseline_id', 'source_result_sha256', 'parent',
              'source', 'report', 'artifacts', 'model_inputs', 'observations', 'obligations'}
    if set(bundle) != fields:
        raise ValueError('invalid calibration/2 fields')
    if bundle.get('schema') != 'agent-ee.calibration/2':
        raise ValueError('strict applicability requires calibration/2 migration')
    if bundle['baseline_id'] != baseline['baseline_id']:
        raise ValueError('empirical calibration belongs to a different baseline')
    artifacts = bundle['artifacts']
    if set(artifacts) & set(baseline['files']):
        raise ValueError('empirical artifacts overlap immutable dependencies')
    for rel, sha in artifacts.items():
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('invalid empirical content hash')
        if rel != f'empirical/artifacts/{sha}' or digest(inputs / rel) != sha:
            raise ValueError('empirical provenance is not intact content-addressed evidence')
    expected = set()
    prior = []
    parent = bundle['parent']
    if parent is not None:
        if parent not in artifacts:
            raise ValueError('unretained parent epoch')
        previous = read_json(inputs / parent)
        validate_bundle(inputs, previous, baseline)
        expected.update(previous['artifacts'])
        expected.add(parent)
        prior = previous['observations']
    source_ref, report_ref = bundle['source'], bundle['report']
    if source_ref not in artifacts or report_ref not in artifacts:
        raise ValueError('unretained epoch provenance')
    source, report = read_json(inputs / source_ref), read_json(inputs / report_ref)
    source_fields = {'schema', 'candidate_id', 'scope', 'files', 'baseline_id', 'baseline_files',
                     'criteria', 'empirical', 'design_inputs', 'derivation', 'checks', 'source_result_sha256'}
    if set(source) != source_fields:
        raise ValueError('invalid prediction snapshot fields')
    if (source['schema'] != 'agent-ee.predictions/2' or
            bundle['candidate_id'] != source['candidate_id'] or
            bundle['source_result_sha256'] != source['source_result_sha256'] or
            source['baseline_id'] != baseline['baseline_id'] or
            source['baseline_files'] != baseline['files'] or
            source['criteria'] != baseline['criteria'] or
            source['candidate_id'] != report['candidate_id']):
        raise ValueError('source prediction identity mismatch')
    empirical = set()
    if parent is not None:
        previous = read_json(inputs / parent)
        if source['empirical'] is None or source['files'].get(source['empirical']) != artifacts[parent]:
            raise ValueError('parent epoch differs from captured source')
        empirical = set(previous['artifacts']) | {source['empirical']}
        if any(source['files'].get(p) != sha for p, sha in previous['artifacts'].items()):
            raise ValueError('source omits parent evidence')
    elif source['empirical'] is not None:
        raise ValueError('source epoch lacks parent closure')
    from .baseline import compile_baseline
    if (identity({'scope': source['scope'], 'files': source['files']}) != source['candidate_id'] or
            compile_baseline(source['scope'], source['files']) != baseline):
        raise ValueError('source candidate or baseline identity mismatch')
    if source['empirical'] != source['scope'].get('empirical'):
        raise ValueError('source empirical identity mismatch')
    protected = {source['scope'].get('board'), source['scope'].get('schematic')}
    if protected & set(artifacts):
        raise ValueError('empirical artifacts overlap native design')
    inventory = design_inputs(source['files'], baseline['files'], empirical)
    if (source['design_inputs'] != inventory or
            any(source['files'].get(p) != sha for p, sha in baseline['files'].items())):
        raise ValueError('source dependency inventory mismatch')
    if set(artifacts) & set(inventory):
        raise ValueError('empirical provenance overlaps source design dependencies')
    if report.get('schema') != 'agent-ee.bench/1':
        raise ValueError('invalid retained bench report schema')
    for field in ('prototype', 'instrument', 'instrument_calibration', 'procedure', 'observed_utc'):
        if not isinstance(report.get(field), str) or not report[field].strip():
            raise ValueError('invalid retained bench provenance')
    fresh = bundle['observations'][len(prior):]
    if bundle['observations'][:len(prior)] != prior or len(fresh) != len(report['observations']):
        raise ValueError('epoch omits retained observations')
    raw_paths = {a['path']: f"empirical/artifacts/{a['sha256']}" for a in report['artifacts']}
    if len(raw_paths) != len(report['artifacts']):
        raise ValueError('duplicate report artifact')
    expected.update([source_ref, report_ref, *raw_paths.values()])
    if expected != set(artifacts):
        raise ValueError('empirical inventory is not the reconstructed provenance closure')
    for observation, item in zip(fresh, report['observations']):
        correction_fields = OBSERVATION_FIELDS | {'source_result', 'report', 'candidate_id', 'prediction',
            'residual_lower', 'residual_upper', 'applicability', 'derivation', 'model_inputs'}
        if set(item) != OBSERVATION_FIELDS or set(observation) != correction_fields:
            raise ValueError('invalid correction fields')
        copied = {**item, 'artifact': raw_paths[item['artifact']]}
        if (any(observation[k] != v for k, v in copied.items()) or
                observation['source_result'] != source_ref or observation['report'] != report_ref or
                observation['applicability'] != {'policy': 'exact-design/1', 'design_inputs': inventory,
                                                'dependency_inventory_id': identity(inventory)} or
                observation['derivation'] != source['derivation'] or
                observation['model_inputs'] != bundle['model_inputs']):
            raise ValueError('correction identity differs from source epoch')
    models = {}
    for spec in baseline['criteria']:
        if any(o['check_id'] == spec['id'] for o in bundle['observations']):
            params = spec.get('params', {})
            paths = params.get('calibration_inputs', []) + params.get('tool', {}).get('inputs', [])
            if params.get('adapter'):
                paths = [params['adapter'], *paths]
            for path in paths:
                models[path] = baseline['files'][path]
    if bundle['model_inputs'] != models:
        raise ValueError('model dependency identity mismatch')
    ids = [identity(o) for o in bundle['observations']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate correction identity')
    if bundle['obligations'] != sorted({obligation(o) for o in bundle['observations']}):
        raise ValueError('epoch correction obligations changed')
    validate_observations(inputs, bundle)
    return set(artifacts)


def evaluate(bundle, files, baseline, empirical_path):
    current = design_inputs(files, baseline['files'], set(bundle['artifacts']) | {empirical_path})
    active = [o for o in bundle['observations'] if o['applicability']['design_inputs'] == current]
    missing = set(bundle['obligations']) - {obligation(o) for o in active}
    if any(o['derivation'] != derivation() for o in active):
        raise ValueError('calibration producer/parser derivation identity changed')
    if missing:
        expected = bundle['observations'][-1]['applicability']['design_inputs']
        paths = sorted(set(expected) | set(current))
        required = {obligation(o): {k: o[k] for k in ('check_id', 'metric', 'unit', 'condition')}
                    for o in bundle['observations'] if obligation(o) in missing}
        raise ApplicabilityUnavailable({'schema': 'agent-ee.recalibration-request/1',
            'policy': 'exact-design/1', 'category': 'design-dependencies-changed',
            'required_obligations': sorted(missing),
            'required_observations': [required[key] for key in sorted(required)], 'design_inputs': current,
            'source_corrections': [identity(o) for o in bundle['observations']],
            'differences': [{'path': p, 'expected': expected.get(p), 'observed': current.get(p)}
                            for p in paths if expected.get(p) != current.get(p)]})
    return {**bundle, 'observations': active}


def unavailable(ctx, metrics, diagnostic):
    from .checks import CheckError
    request = {**diagnostic, 'candidate_id': ctx.manifest['candidate_id'], 'check_id': ctx.cid,
               'required_metrics': sorted(metrics)}
    ctx.result['details'].update(metrics=metrics, nominal_prediction='executed-unqualified/1',
                                 calibration_applicability=request)
    for name in ('calibration-applicability.json', 'recalibration-request.json'):
        write_json(ctx.work / name, request)
        ctx.evidence(name)
    raise CheckError('unavailable', 'exact design applicability failed; fresh bench evidence required')


def pending_recalibration(run):
    """Admit only intact coordinator requests, never arbitrary incomplete work."""
    from .cli import verify
    from .baseline import load_baseline
    from .empirical import calibration_for
    result = verify(run, current_source=False)
    manifest = read_json(run / 'candidate.json')
    requests = [c for c in result['checks'] if c['execution'] != 'ok']
    if not requests or any(not source_prediction(c) for c in requests):
        return False
    bundle = calibration_for(run, manifest['scope'], manifest['files'], provenance_only=True)
    if bundle is None:
        return False
    try:
        evaluate(bundle, manifest['files'], load_baseline(run), manifest['scope']['empirical'])
    except ApplicabilityUnavailable as exc:
        for check in requests:
            request = check['details']['calibration_applicability']
            if (any(request.get(k) != v for k, v in exc.diagnostic.items()) or
                    request.get('candidate_id') != manifest['candidate_id'] or
                    request.get('check_id') != check['id'] or
                    read_json(run / f"work/{check['id']}/recalibration-request.json") != request):
                return False
        return True
    return False
