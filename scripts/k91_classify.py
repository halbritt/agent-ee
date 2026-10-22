"""Versioned verdict classifiers for the AGENTEE-442 native K91 harness records.

CLASSIFIER_V1 is the verdict expression of the frozen harness
scripts/k91_native_positive.py (commit 553e4151), restated unchanged so that
its historical manifest can be re-derived and compared. Its defect (review
ee442-native-positive-b, H2): a control whose actual verify() returns without
raising counts as accepted, so a coherent, verified not_run refusal makes
controls_refused false. The historical manifest keeps that value; nothing here
rewrites it.

CLASSIFIER_V2 (scripts/k91_native_positive_v2.py) classifies every control as
refused, not_refused or incomplete. Revision 2 (review round 2, H1/H2) judges
only OBSERVED, schema-valid outcomes:

- every observation is explicit: {'attempted': True, 'accepted': bool, and
  'value' (accepted) or a non-empty 'error' (refused)}, or {'attempted': False,
  'reason': str} for a call the harness did not make. Anything else, or a
  missing observation, is malformed, and the control is incomplete;
- verify() refuses when it raised, or when it returned the coherent not_run
  record whose keys are all present: classification not_run, proposal none,
  router_exit null (no router process) and candidate_sha256 null (no
  candidate). An absent key is never read as null;
- a control is refused only when verify() refused AND at least one adoption
  call was attempted and observed to refuse, and no attempted adoption
  succeeded. Each control records `adopt` (the consumer's adoption, attempted
  only after a verify() that did not refuse) and `direct_adopt` (the bare
  stage adoption, always attempted). A skipped call is never a refusal, and a
  verify() refusal never stands in for one. A refused verify() with no observed
  adoption refusal, or with a successful adoption, is incomplete. A verify()
  that accepted is not_refused;
- the mismatched-runtime control also needs the observed host step() refusal
  and the in-sandbox not_run with its reasons and an explicit null candidate;
- the candidate-board and sidecar tamper controls are separate. A control
  whose target file did not exist (no candidate was delivered) is incomplete;
- REQUIRED_V2 (mismatched runtime, candidate-board tamper, stale input, the
  controls the positive-c plan names) decide controls_refused. The sidecar
  tamper is reported beside them. Its bare stage adoption checks only the
  declared outputs, so it cannot show an adoption refusal for a tampered
  routing artifact, and the control stays incomplete;
- the positive is judged separately: accepted only when an observed verify()
  returned a candidate with a retained, unqualified experiment record and an
  observed adoption of it succeeded into the disposable copy.

Nothing here runs a router, writes a receipt or reads anything but the dicts it
is given.
"""

CLASSIFIER_V1 = 'agent-ee.k91-native-positive/verdict-1'
CLASSIFIER_V2 = 'agent-ee.k91-native-positive/verdict-2'
REFUSED, NOT_REFUSED, INCOMPLETE = 'refused', 'not_refused', 'incomplete'
CONTROLS_V2 = ('a_mismatched_runtime', 'b_tampered_candidate', 'b_tampered_sidecar', 'c_stale_input')


def v1_verdict(manifest):
    """The frozen harness's verdict, from the records it wrote (unchanged expression)."""
    summary, adopted = manifest['positive']['verify'], manifest['positive']['adopt']
    controls = manifest['controls']
    tampered = controls['b_tampered_output']
    experiment = (summary.get('value') or {}).get('experiment') or {}
    refused = all(not c['accepted'] for c in (controls['a_mismatched_runtime']['verify'],
                                              controls['a_mismatched_runtime']['adopt'],
                                              controls['c_stale_input']['verify'], controls['c_stale_input']['adopt'])) \
        and controls['a_mismatched_runtime']['sandbox']['classification'] == 'not_run' \
        and not controls['a_mismatched_runtime']['host_step']['accepted'] \
        and all(not t['verify']['accepted'] and not t['adopt']['accepted']
                for t in tampered.values() if 'verify' in t)
    return {'positive_retained_and_adopted': bool(
        summary['accepted'] and adopted['accepted'] and experiment.get('status') == 'retained'
        and experiment.get('qualified') is False), 'controls_refused': refused}


REQUIRED_V2 = ('a_mismatched_runtime', 'b_tampered_candidate', 'c_stale_input')
NOT_RUN_KEYS = ('classification', 'proposal', 'router_exit', 'candidate_sha256')


def observed(outcome):
    """The schema-valid outcome of a call the harness made, or None (skipped, missing or malformed)."""
    if not isinstance(outcome, dict) or outcome.get('attempted') is not True:
        return None
    accepted = outcome.get('accepted')
    if accepted is True and set(outcome) == {'attempted', 'accepted', 'value'}:
        return outcome
    if (accepted is False and set(outcome) == {'attempted', 'accepted', 'error'}
            and isinstance(outcome['error'], str) and outcome['error']):
        return outcome
    return None


def skipped(outcome):
    """A schema-valid record of a call the harness deliberately did not make."""
    return (isinstance(outcome, dict) and set(outcome) == {'attempted', 'reason'} and outcome['attempted'] is False
            and isinstance(outcome['reason'], str) and bool(outcome['reason']))


def not_run_refusal(value):
    """verify()'s coherent not_run record, every key explicit (an absent key is never null)."""
    return (isinstance(value, dict) and all(key in value for key in NOT_RUN_KEYS)
            and value['classification'] == 'not_run' and value['proposal'] == 'none'
            and value['router_exit'] is None and value['candidate_sha256'] is None)


def control_state(record):
    """refused / not_refused / incomplete for one recorded control (see the module doc)."""
    if not isinstance(record, dict) or 'skipped' in record:
        return INCOMPLETE
    verify = observed(record.get('verify'))
    adoptions = [record.get('adopt'), record.get('direct_adopt')]
    if verify is None or not all(observed(a) is not None or skipped(a) for a in adoptions):
        return INCOMPLETE
    if verify['accepted'] and not not_run_refusal(verify['value']):
        return NOT_REFUSED
    made = [a for a in adoptions if observed(a) is not None]
    if not made or any(a['accepted'] for a in made):
        return INCOMPLETE
    return REFUSED


def runtime_state(record):
    """The mismatched-runtime control: observed host refusal, sandbox not_run, verify and adoption refusal."""
    if not isinstance(record, dict):
        return INCOMPLETE
    host, sandbox = observed(record.get('host_step')), record.get('sandbox')
    if (host is None or not isinstance(sandbox, dict) or not {'classification', 'reasons', 'candidate'} <= set(sandbox)
            or not isinstance(sandbox['reasons'], list)):
        return INCOMPLETE
    if host['accepted'] or sandbox['classification'] != 'not_run' or not sandbox['reasons'] \
            or sandbox['candidate'] is not None:
        return NOT_REFUSED
    return control_state(record)


def positive_accepted(positive):
    """The positive: an observed verified candidate, a retained unqualified record, and an observed adoption."""
    if not isinstance(positive, dict):
        return False
    verify, adopt = observed(positive.get('verify')), observed(positive.get('adopt'))
    if verify is None or adopt is None or not verify['accepted'] or not adopt['accepted']:
        return False
    value = verify['value'] if isinstance(verify['value'], dict) else {}
    experiment = value.get('experiment') if isinstance(value.get('experiment'), dict) else {}
    return bool(value.get('candidate_sha256') and value.get('proposal') not in (None, 'none')
                and experiment.get('status') == 'retained' and experiment.get('qualified') is False
                and positive.get('design_changed'))


def v2_verdict(manifest):
    """The corrected verdict for a v2 manifest: REQUIRED_V2 decide controls_refused; the sidecar is reported."""
    controls = manifest.get('controls') if isinstance(manifest.get('controls'), dict) else {}
    states = {name: (runtime_state if name == 'a_mismatched_runtime' else control_state)(controls.get(name))
              for name in CONTROLS_V2}
    return {'classifier': CLASSIFIER_V2, 'positive_accepted': positive_accepted(manifest.get('positive')),
            'controls': states, 'required': list(REQUIRED_V2),
            'controls_refused': all(states[name] == REFUSED for name in REQUIRED_V2)}


V1_NOT_ATTEMPTED = {'accepted': False, 'error': 'not attempted: verify refused'}


def _v1_observation(outcome):
    """A v1 outcome as a v2 observation. The v1 harness called every function it recorded
    (outcome(fn) runs fn), so each record is an attempted call; its bytes are not edited."""
    if not isinstance(outcome, dict):
        return outcome
    if outcome == V1_NOT_ATTEMPTED:  # the v1 positive skipped adoption after a refused verify()
        return {'attempted': False, 'reason': outcome['error']}
    return {'attempted': True, **outcome}


def v1_controls_as_v2(manifest):
    """The historical (v1) records under the v2 rules, without editing them. v1 called verify() and the bare
    stage adoption directly for every control, so its adopt is the direct_adopt observation and the consumer
    adoption is recorded as not made; b_tampered_output maps to the two distinct tamper controls."""
    controls = manifest['controls']
    tampered = controls['b_tampered_output']
    not_made = {'attempted': False, 'reason': 'v1 harness: no consumer-order adoption; adopt recorded as direct_adopt'}

    def control(record, runtime=False):
        if not isinstance(record, dict) or 'verify' not in record:
            return record
        mapped = {**record, 'verify': _v1_observation(record['verify']), 'adopt': not_made,
                  'direct_adopt': _v1_observation(record.get('adopt'))}
        if runtime:
            mapped['host_step'] = _v1_observation(record.get('host_step'))
        return mapped

    positive = dict(manifest['positive'])
    positive.update(verify=_v1_observation(positive.get('verify')), adopt=_v1_observation(positive.get('adopt')))
    return {'positive': positive,
            'controls': {'a_mismatched_runtime': control(controls['a_mismatched_runtime'], runtime=True),
                         'b_tampered_candidate': control(tampered.get('candidate')),
                         'b_tampered_sidecar': control(tampered.get('sidecar')),
                         'c_stale_input': control(controls['c_stale_input'])}}
