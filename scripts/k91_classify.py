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

Revision 3 (coordinator boundary ruling a01d6f91, agent-521; a coordinator
decision, not owner text): the protected boundary is the production routing
consumer, campaign.run_implementation_steps (run_step, verify_campaign_receipt,
adopt; scripts/k91_consumer.py), not the generic stage adoption. A required
control (REQUIRED_V2) is REFUSED only when, in schema-valid records:

- the production consumer call was made and raised (observed refusal);
- the consumer's design digests before and after are present and equal;
- each consumer layer (`consumer_run_step`, `consumer_verify`,
  `consumer_adopt`) is observed or recorded as not called, and no consumer
  adoption succeeded;
- the bare `direct_adopt` was observed, or recorded as not made, on a design
  copy other than the consumer's. It is kept for diagnosis, whatever it
  returned: a bare adoption that succeeds stays visible and is never counted
  as a refusal, and a skipped one is never a refusal either;
- for the runtime control, the production binding (preflight) was also
  observed to refuse.

A consumer that returned, or changed the design, is not_refused; anything
missing or malformed is incomplete. The positive needs the consumer to return
after an observed verify of a delivered candidate with a retained,
unqualified experiment record and an observed successful consumer adoption
that delivered exactly that board. control_state (the round-2 low-level rule,
for records without a consumer layer, such as the historical ones) is kept
and reported as `low_level`.

Revision 4 (the lane's third-HOLD root redesign; review round 3 H4): rounds
1-3 inferred "refused" from the ABSENCE of success, so a wrong replay
binding, invalid or missing receipt JSON, or a campaign finalization error
counted as a refusal. A required control is now REFUSED only on a POSITIVE
observation of its intended refusal: CONTRACTS names, per control, the origin
(file and function that raised it), the reason pattern, the path the root
exception took, the consumer layers' required outcomes, and the preconditions
(e.g. the replayed run_step SUCCEEDED and the tamper was applied). Every link
of the recorded exception chain must be that refusal. The only other link
allowed is the same refusal raised again by campaign finalization, and only
where that was observed (the board tamper: bind_implementation re-validates
the receipt in its finally block). Anything else is INCOMPLETE: absent,
skipped, missing or malformed records, unrelated exceptions, replay or
plumbing failures, other finalization errors. A consumer that returned,
adopted or changed the design is not_refused.

Nothing here runs a router, writes a receipt or reads anything but the dicts it
is given.
"""

import re

CLASSIFIER_V1 = 'agent-ee.k91-native-positive/verdict-1'
CLASSIFIER_V2 = 'agent-ee.k91-native-positive/verdict-2r4'
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
    if (accepted is False and set(outcome) in ({'attempted', 'accepted', 'error'},
                                                {'attempted', 'accepted', 'error', 'exception'})
            and isinstance(outcome['error'], str) and outcome['error']
            and ('exception' not in outcome or chain_of(outcome) is not None)):
        return outcome
    return None


LINK_KEYS = {'type', 'message', 'origin', 'frames', 'link'}


def chain_of(outcome):
    """The schema-valid exception chain of a refused observation, outermost first, or None."""
    chain = outcome.get('exception') if isinstance(outcome, dict) else None
    if not isinstance(chain, list) or not chain:
        return None
    for link in chain:
        origin = link.get('origin') if isinstance(link, dict) else None
        if (not isinstance(link, dict) or set(link) != LINK_KEYS or not isinstance(link['type'], str)
                or not isinstance(link['message'], str) or not isinstance(origin, dict)
                or set(origin) != {'file', 'function', 'line'} or not isinstance(origin['file'], str)
                or not isinstance(origin['function'], str) or not isinstance(link['frames'], list)
                or not all(isinstance(f, str) for f in link['frames']) or link['link'] not in (None, 'cause', 'context')):
            return None
    if outcome.get('error') != f"{chain[0]['type']}: {chain[0]['message']}":
        return None
    return chain


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


SHA256 = frozenset('0123456789abcdef')


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA256


def _layer(value):
    return observed(value) is not None or skipped(value)


# The control contract table (round-3 actual-consumer observations, reviews/ee442-native-positive-b-sol-r3.md,
# "Per-control adjudication"; the native runtime reason is the 9352b0f1 host refusal). Each entry:
#   origin       - (file, function) that raised the refusal (innermost frame of every link);
#   reason       - regular expression the exception message must match (type ValueError);
#   path         - frames the ROOT exception must have passed, in order (the production route);
#   layers       - required consumer layer outcomes: 'accepted', 'refused' (refused by this same
#                  contract), or 'not_called';
#   finalization - True where campaign finalization was observed to re-raise the same refusal
#                  (bind_implementation -> build_index -> validate_receipt); otherwise one link only;
#   preconditions- named facts the record must state explicitly (see PRECONDITIONS).
CONTRACTS = {
    'a_mismatched_runtime': {
        'origin': ('routing_adapter.py', 'validate_campaign_step'),
        'reason': r'kicad-route does not qualify: (tool file differs from pin|owned engine source digest differs)',
        'path': ('campaign.py:run_implementation_steps', 'routing_adapter.py:validate_live_binding',
                 'routing_adapter.py:validate_campaign_step'),
        'layers': {'consumer_run_step': 'not_called', 'consumer_verify': 'not_called', 'consumer_adopt': 'not_called'},
        'finalization': False, 'preconditions': ('design_unchanged', 'executed', 'host_binding_refused')},
    'b_tampered_candidate': {
        'origin': ('stage_receipts.py', 'validate_receipt'), 'reason': r'stage receipt outputs changed',
        'path': ('campaign.py:run_implementation_steps', 'routing_adapter.py:verify_campaign_receipt',
                 'stage_receipts.py:validate_receipt'),
        'layers': {'consumer_run_step': 'accepted', 'consumer_verify': 'refused', 'consumer_adopt': 'not_called'},
        'finalization': True, 'preconditions': ('design_unchanged', 'replayed', 'candidate_tampered')},
    'b_tampered_sidecar': {
        'origin': ('routing_adapter.py', '_verify'), 'reason': r'retained routing artifacts differ from the adapter report',
        'path': ('campaign.py:run_implementation_steps', 'routing_adapter.py:verify_campaign_receipt',
                 'routing_adapter.py:verify', 'routing_adapter.py:_verify'),
        'layers': {'consumer_run_step': 'accepted', 'consumer_verify': 'refused', 'consumer_adopt': 'not_called'},
        'finalization': False, 'preconditions': ('design_unchanged', 'replayed', 'sidecar_tampered')},
    'c_stale_input': {
        'origin': ('campaign.py', 'run_implementation_steps'),
        'reason': r'routing receipt chain input differs from preceding adopted output',
        'path': ('campaign.py:run_implementation_steps',),
        'layers': {'consumer_run_step': 'accepted', 'consumer_verify': 'not_called', 'consumer_adopt': 'not_called'},
        'finalization': False, 'preconditions': ('design_unchanged', 'replayed', 'input_stale')},
}
FINALIZATION_PATH = ('campaign.py:run_implementation_steps', 'stage_receipts.py:bind_implementation',
                     'stage_receipts.py:build_index', 'stage_receipts.py:validate_receipt')
SIDECAR = 'routing/attempt/routability-experiment.json'


def _in_order(frames, path):
    position = 0
    for frame in frames:
        if position < len(path) and frame == path[position]:
            position += 1
    return position == len(path)


def _is_refusal(link, contract):
    return (link['type'] == 'ValueError' and re.match(contract['reason'], link['message']) is not None
            and (link['origin']['file'], link['origin']['function']) == contract['origin'])


def _tampered(record, rel):
    return (record.get('file') == rel and _digest(record.get('sha256_before')) and _digest(record.get('sha256_after'))
            and record['sha256_before'] != record['sha256_after'])


def _precondition(name, record, contract):
    if name == 'design_unchanged':
        return record['design_before'] == record['design_after']
    if name == 'replayed':
        return record.get('run_step') == 'replayed'
    if name == 'executed':
        return record.get('run_step') == 'executed'
    if name == 'host_binding_refused':
        host = observed(record.get('host_binding'))
        chain = chain_of(host) if host is not None and not host['accepted'] else None
        return bool(chain) and len(chain) == 1 and _is_refusal(chain[0], contract)
    if name == 'candidate_tampered':
        rel = record.get('file')
        return (isinstance(rel, str) and rel.startswith('routing/candidate/') and rel.endswith('.kicad_pcb')
                and _tampered(record, rel))
    if name == 'sidecar_tampered':
        return _tampered(record, SIDECAR)
    if name == 'input_stale':
        return (_digest(record.get('board_sha256')) and _digest(record.get('receipt_board_sha256'))
                and record['board_sha256'] != record['receipt_board_sha256'])
    return False


def contract_match(name, record):
    """[] when the record is a positive observation of the control's intended refusal, else the reasons not."""
    contract = CONTRACTS[name]
    consumer = observed(record.get('consumer'))
    chain = chain_of(consumer) if consumer is not None and not consumer['accepted'] else None
    if not chain:
        return ['the consumer refusal carries no schema-valid exception chain']
    problems = []
    root = chain[-1]
    if not _is_refusal(root, contract):
        problems.append(f"root exception is not the contract refusal: {root['type']}: {root['message']} "
                        f"at {root['origin']['file']}:{root['origin']['function']}")
    elif not _in_order(root['frames'], contract['path']):
        problems.append('root refusal did not take the contract path ' + ' > '.join(contract['path']))
    for link in chain[:-1]:
        if not (contract['finalization'] and _is_refusal(link, contract) and _in_order(link['frames'], FINALIZATION_PATH)):
            problems.append(f"other exception in the chain: {link['type']}: {link['message']} "
                            f"at {link['origin']['file']}:{link['origin']['function']}")
    for layer, want in contract['layers'].items():
        got = observed(record.get(layer))
        if want == 'not_called':
            ok = got is None and skipped(record.get(layer))
        elif want == 'accepted':
            ok = got is not None and got['accepted']
        else:
            layer_chain = chain_of(got) if got is not None and not got['accepted'] else None
            # The layer saw the same exception the consumer's chain ends in (frames grow as it propagates).
            ok = bool(layer_chain) and len(layer_chain) == 1 and all(
                layer_chain[0][k] == root[k] for k in ('type', 'message', 'origin'))
        if not ok:
            problems.append(f'{layer} is not {want}')
    problems += [f'precondition not shown: {p}' for p in contract['preconditions'] if not _precondition(p, record, contract)]
    return problems


def schema_problems(record):
    """Why a control record is not a schema-valid consumer observation (empty when it is)."""
    if not isinstance(record, dict) or 'skipped' in record:
        return ['control absent or skipped']
    problems = []
    if observed(record.get('consumer')) is None:
        problems.append('consumer observation absent, skipped or malformed')
    problems += [f'{key} absent or malformed' for key in ('consumer_run_step', 'consumer_verify', 'consumer_adopt',
                                                          'direct_adopt') if not _layer(record.get(key))]
    problems += [f'{key} is not a sha256 digest' for key in ('design_before', 'design_after')
                 if not _digest(record.get(key))]
    if not isinstance(record.get('consumer_design'), str) or not isinstance(record.get('direct_design'), str):
        problems.append('consumer_design or direct_design absent')
    elif record['consumer_design'] == record['direct_design']:
        problems.append('the bare adoption used the consumer design copy')
    return problems


def consumer_state(record, name=None):
    """refused / not_refused / incomplete for one control (revision 4: by contract only)."""
    if name not in CONTRACTS or schema_problems(record):
        return INCOMPLETE
    adopt = observed(record['consumer_adopt'])
    if record['consumer']['accepted'] or record['design_before'] != record['design_after'] or (adopt and adopt['accepted']):
        return NOT_REFUSED
    return REFUSED if not contract_match(name, record) else INCOMPLETE


def explain(record, name):
    """Why a control is not REFUSED (empty when it is)."""
    if name not in CONTRACTS:
        return ['no contract for this control']
    problems = schema_problems(record)
    if problems:
        return problems
    if consumer_state(record, name) == NOT_REFUSED:
        return ['the consumer returned, adopted or changed the design']
    return contract_match(name, record)


def positive_accepted(positive):
    """The positive at the production consumer: returned after an observed verify of a delivered
    candidate (retained, unqualified record) and an observed adoption that delivered that board."""
    if not isinstance(positive, dict):
        return False
    consumer, verify = observed(positive.get('consumer')), observed(positive.get('consumer_verify'))
    adopt = observed(positive.get('consumer_adopt'))
    if None in (consumer, verify, adopt) or not (consumer['accepted'] and verify['accepted'] and adopt['accepted']):
        return False
    value = verify['value'] if isinstance(verify['value'], dict) else {}
    experiment = value.get('experiment') if isinstance(value.get('experiment'), dict) else {}
    return bool(_digest(value.get('candidate_sha256')) and value.get('proposal') not in (None, 'none')
                and experiment.get('status') == 'retained' and experiment.get('qualified') is False
                and _digest(positive.get('design_before')) and _digest(positive.get('design_after'))
                and positive['design_before'] != positive['design_after']
                and positive.get('delivered_board_sha256') == value['candidate_sha256'])


def v2_verdict(manifest):
    """Revision 4: each control by its contract (CONTRACTS); REQUIRED_V2 decide controls_refused; the sidecar,
    the reasons a control is not refused, and the round-2 low-level states are reported beside them."""
    controls = manifest.get('controls') if isinstance(manifest.get('controls'), dict) else {}
    states = {name: consumer_state(controls.get(name), name) for name in CONTROLS_V2}
    return {'classifier': CLASSIFIER_V2, 'positive_accepted': positive_accepted(manifest.get('positive')),
            'controls': states, 'required': list(REQUIRED_V2),
            'controls_refused': all(states[name] == REFUSED for name in REQUIRED_V2),
            'not_refused_because': {name: explain(controls.get(name), name) for name in CONTROLS_V2
                                    if states[name] != REFUSED},
            'low_level': {name: (runtime_state if name == 'a_mismatched_runtime' else control_state)(_low(controls, name))
                          for name in CONTROLS_V2}}


def _low(controls, name):
    """A control's low-level (round-2 shape) record: its `low_level` part, or the record itself (historical)."""
    record = controls.get(name)
    return record.get('low_level', record) if isinstance(record, dict) else record


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
