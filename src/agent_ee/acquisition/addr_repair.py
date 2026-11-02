"""Finite U2 ADDR repair routines for the ADS1115 acquisition campaign."""
import json
from pathlib import Path
import shutil
import time

from .. import native_run
from ..baseline import load_baseline, validate_witnesses
from ..campaign import advance
from ..preflight import capture_expectation
from ..profiles import validate_definition
from ..rating_lineage import same_authored_baseline
from ..review_run import structured_call, verify_role_call
from ..reviewers import reviewer_call
from ..run_record import verify
from ..stage_receipts import file_tree, tree_identity
from ..storage import digest, read_json, write_json

ADDR_RESPONSE = {'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'addr_tie', 'finding_ids'], 'properties': {
        'summary': {'type': 'string'}, 'addr_tie': {'type': 'string', 'enum': ['GND', 'V3V3']},
        'finding_ids': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                        'items': {'type': 'string'}}}}


def addr_feedback(run, *, host=None):
    """Only a sealed public functional finding can authorize the finite ADDR turn."""
    result = verify(Path(run), host=host)
    manifest = read_json(Path(run) / 'candidate.json')
    if result.get('max_schema_resubmissions') != 0 or not result.get('adjudicated'):
        raise ValueError('ADDR repair requires actual adjudication with zero schema resubmissions')
    checks = {c['id']: c for c in result['checks']}
    if (checks.get('adc-ties', {}).get('execution') != 'ok' or
            checks['adc-ties'].get('outcome') != 'defect'):
        raise ValueError('ADDR repair requires actual adc-ties defect')
    baseline = load_baseline(run)
    witness = {'requirement_id': 'ACQ-ELEC-6-TIES', 'check_id': 'adc-ties'}
    if baseline is None or not any(r['id'] == witness['requirement_id'] and
            witness['check_id'] in r['checks'] for r in baseline['requirements']):
        raise ValueError('ADDR criterion is not bound to the public immutable requirement')
    findings = []
    for finding in result['findings']:
        if (finding.get('source') == 'check' and finding.get('status') == 'accepted' and
                finding.get('local_id') == 'adc-ties' and finding.get('id') == 'check/adc-ties'):
            # Coordinator check findings derive witnesses from the immutable mapping,
            # exactly as findings.adjudicate does; they do not serialize a witnesses field.
            validate_witnesses(run, [witness], finding.get('decision_evidence', []))
            findings.append(finding)
    if not findings:
        raise ValueError('no accepted source-bound ADDR finding')
    record, diagnostic = advance([], result, manifest, 2)
    if diagnostic['state'] != 'repair':
        raise ValueError('public campaign does not permit repair: ' + diagnostic['state'])
    baseline = load_baseline(run)
    if baseline is None:
        raise ValueError('ADDR repair requires complete immutable baseline')
    return result, manifest, baseline, findings, record


def select_generated_addr(root, first, *, definition, launch_environment, deadline, cancellation, host=None):
    """One real writer chooses a source selector; it has no CAD edit authority."""
    native_run.checkpoint(deadline, cancellation)
    root, first = Path(root).absolute(), Path(first).absolute()
    result, manifest, baseline, findings, initial = addr_feedback(first, host=host)
    definition = {**validate_definition(definition), 'tool_mode': 'fixed_packet'}
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(first / 'inputs', root / 'inputs')
    shutil.copyfile(first / 'baseline.json', root / 'baseline.json')
    write_json(root / 'candidate.json', manifest)
    config = dict(schema='agent-ee.generated-addr-campaign/1', max_iterations=2,
                  max_schema_resubmissions=0, first=str(first), deadline=deadline,
                  definition=definition, launch_environment=launch_environment)
    write_json(root / 'config.json', config)
    expected = capture_expectation({**result, **launch_environment}, manifest, [definition], inputs=first / 'inputs')
    write_json(root / 'launch-expectation.json', expected)
    ids = sorted(f['id'] for f in findings)
    prompt = ('Repair the actual public U2 ADDR functional defect by selecting the original source recipe tie. '
        'Return summary, addr_tie (GND or V3V3), and the exact supporting finding_ids. '
        'No file edits, private evaluation, schema retries or board transplantation are permitted. '
        'A fresh public build and new independent judgments will qualify the generated successor.\n' +
        json.dumps(dict(candidate_id=manifest['candidate_id'], baseline=baseline, findings=findings)))
    (root / 'implementation-prompt.txt').write_text(prompt)
    def invariant():
        actual, other = file_tree(root / 'inputs')
        if other or actual != manifest['files'] or read_json(root / 'baseline.json') != baseline:
            raise ValueError('selector changed immutable captured inputs')
        native_run.checkpoint(deadline, cancellation)
    def validate(value):
        if (not isinstance(value, dict) or set(value) != {'summary', 'addr_tie', 'finding_ids'}
                or not isinstance(value['summary'], str) or not value['summary'].strip()
                or value['addr_tie'] != 'GND' or value['finding_ids'] != ids):
            raise ValueError('ADDR writer must make attributable GND correction, not a no-op/foreign finding')
        return value
    with native_run.using(deadline=deadline, cancel=cancellation):
        record, response = structured_call(root, 'implementation', prompt, ADDR_RESPONSE,
            lambda supplied: reviewer_call(root, 'implementation', definition, supplied, ADDR_RESPONSE,
                min(definition['timeout_s'], max(.001, deadline - time.monotonic())),
                cancellation=cancellation, launch_context=dict(expected=expected, boundary='implementation',
                    deadline=deadline, **({} if host is None else {'host': host}))),
            validate, limit=0, invariant=invariant)
    verify_role_call(root, record, expected, 'implementation', 'implementation', limit=0)
    invariant()
    if record['execution'] != 'ok' or response is None:
        raise ValueError('ADDR implementation did not complete; no retry/fallback')
    write_json(root / 'selection.json', response)
    state = dict(schema='agent-ee.campaign-state/1', state='repair', history=[initial],
        config_sha256=digest(root / 'config.json'), first_tree=tree_identity(first), baseline=baseline,
        implementation_sha256=digest(root / 'implementation-execution.json'),
        selection_sha256=digest(root / 'selection.json'))
    write_json(root / 'state.json', state)
    return response


def verify_generated_addr(root, *, successor=None, host=None):
    """Replay the public finding, writer call, complete authored baseline and convergence."""
    root = Path(root)
    config, state = read_json(root / 'config.json'), read_json(root / 'state.json')
    if (config['schema'] != 'agent-ee.generated-addr-campaign/1' or config['max_iterations'] != 2
            or config['max_schema_resubmissions'] != 0 or digest(root / 'config.json') != state['config_sha256']):
        raise ValueError('generated ADDR configuration changed')
    first = Path(config['first'])
    result, manifest, baseline, findings, initial = addr_feedback(first, host=host)
    if (tree_identity(first) != state['first_tree'] or baseline != state['baseline'] or
            file_tree(root / 'inputs') != (manifest['files'], []) or
            read_json(root / 'baseline.json') != baseline):
        raise ValueError('generated ADDR original candidate/baseline changed')
    record = read_json(root / 'implementation-execution.json')
    expected = read_json(root / 'launch-expectation.json')
    if expected != capture_expectation({**result, **config['launch_environment']}, manifest,
                                      [config['definition']], inputs=first / 'inputs'):
        raise ValueError('generated ADDR writer context changed')
    verify_role_call(root, record, expected, 'implementation', 'implementation', limit=0)
    selection = read_json(root / 'selection.json')
    if (record['execution'] != 'ok' or digest(root / 'implementation-execution.json') != state['implementation_sha256']
            or digest(root / 'selection.json') != state['selection_sha256'] or selection['addr_tie'] != 'GND'
            or selection['finding_ids'] != sorted(f['id'] for f in findings)):
        raise ValueError('generated ADDR writer selection changed')
    response = read_json(root / 'review-attempts/implementation/0/response.json')
    if response != selection:
        raise ValueError('generated selector differs from actual writer response')
    if successor is None:
        if state['history'] != [initial] or state['state'] != 'repair':
            raise ValueError('generated ADDR initial history changed')
        return selection
    successor = Path(successor)
    final = verify(successor, host=host)
    if not same_authored_baseline(first, successor) or final.get('max_schema_resubmissions') != 0:
        raise ValueError('generated successor changed complete immutable baseline/review bound')
    following, diagnostic = advance([initial], final, read_json(successor / 'candidate.json'), 2)
    if diagnostic['state'] != 'criteria_satisfied':
        raise ValueError('generated successor did not converge: ' + diagnostic['state'])
    return dict(history=[initial, following], state=diagnostic['state'], successor=str(successor),
                successor_tree=tree_identity(successor))
