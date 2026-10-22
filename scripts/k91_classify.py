"""Versioned verdict classifiers for the AGENTEE-442 native K91 harness records.

CLASSIFIER_V1 is the verdict expression of the frozen harness
scripts/k91_native_positive.py (commit 553e4151), restated unchanged so that
its historical manifest can be re-derived and compared. Its defect (review
ee442-native-positive-b, H2): a control whose actual verify() returns without
raising counts as accepted, so a coherent, verified not_run refusal makes
controls_refused false. The historical manifest keeps that value; nothing here
rewrites it.

CLASSIFIER_V2 (scripts/k91_native_positive_v2.py) classifies every control as
refused, not_refused or incomplete:

- a verify() that raises is a refusal, and so is a verify() that returns the
  coherent not_run record (classification not_run, proposal none, no router
  process, no candidate). Either way the control's adoption must have failed,
  or it is not_refused. `adopt` is the consumer's adoption, attempted only after
  a verify() that did not refuse; the bare stage adoption is kept apart as
  `direct_adopt` (it checks only the declared outputs, so it accepts a receipt
  whose routing artifacts verify() refuses);
- the mismatched-runtime control also needs the host step() refusal, the
  in-sandbox not_run with its reasons, which are kept in the record, and a
  failed bare adoption;
- the candidate-board and sidecar tamper controls are separate. A control
  whose target file did not exist (no candidate was delivered) is incomplete,
  never refused or passed;
- the positive is judged separately: accepted only when verify() returned a
  candidate with a retained, unqualified experiment record and the candidate
  was adopted into the disposable copy.

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


def not_run_refusal(value):
    """True for verify()'s coherent record of an attempt that never ran the router."""
    return (isinstance(value, dict) and value.get('classification') == 'not_run'
            and value.get('proposal') == 'none' and value.get('router_exit') is None
            and value.get('candidate_sha256') is None)


def verify_refused(outcome):
    """A verify() outcome {'accepted', 'value'|'error'} that refuses: raised, or a coherent not_run."""
    return not outcome['accepted'] or not_run_refusal(outcome.get('value'))


def control_state(record):
    """refused / not_refused / incomplete for one recorded control (see the module doc)."""
    if record is None or 'skipped' in record:
        return INCOMPLETE
    if 'verify' not in record or 'adopt' not in record:
        return INCOMPLETE
    return REFUSED if verify_refused(record['verify']) and not record['adopt']['accepted'] else NOT_REFUSED


def runtime_state(record):
    """The mismatched-runtime control: host refusal, sandbox not_run with reasons, verify and adopt refusal."""
    if record is None:
        return INCOMPLETE
    host, sandbox = record.get('host_step') or {}, record.get('sandbox') or {}
    if (host.get('accepted') is not False or not host.get('error') or sandbox.get('classification') != 'not_run'
            or not sandbox.get('reasons') or sandbox.get('candidate') is not None
            or (record.get('direct_adopt') or {}).get('accepted')):
        return NOT_REFUSED
    return control_state(record)


def positive_accepted(positive):
    """The positive: a verified candidate, a retained unqualified experiment record, and adoption."""
    verify, adopt = positive.get('verify') or {}, positive.get('adopt') or {}
    value = verify.get('value') or {}
    experiment = value.get('experiment') or {}
    return bool(verify.get('accepted') and adopt.get('accepted') and value.get('candidate_sha256')
                and value.get('proposal') not in (None, 'none')
                and experiment.get('status') == 'retained' and experiment.get('qualified') is False
                and positive.get('design_changed'))


def v2_verdict(manifest):
    """The corrected verdict for a v2 manifest (controls named as in CONTROLS_V2)."""
    controls = manifest['controls']
    states = {name: (runtime_state if name == 'a_mismatched_runtime' else control_state)(controls.get(name))
              for name in CONTROLS_V2}
    return {'classifier': CLASSIFIER_V2, 'positive_accepted': positive_accepted(manifest['positive']),
            'controls': states, 'controls_refused': all(s == REFUSED for s in states.values())}


def v1_controls_as_v2(manifest):
    """The historical (v1) records under the v2 rules, without editing them: the v1 b_tampered_output
    entries map to the two distinct tamper controls; the historical value of each stays as written."""
    controls = manifest['controls']
    tampered = controls['b_tampered_output']
    return {'positive': manifest['positive'],
            'controls': {'a_mismatched_runtime': controls['a_mismatched_runtime'],
                         'b_tampered_candidate': tampered.get('candidate'),
                         'b_tampered_sidecar': tampered.get('sidecar'),
                         'c_stale_input': controls['c_stale_input']}}
