"""Amendment A1 to the K91 v2 classifier: the retained wrong-commit runtime refusal (AGENTEE-442, positive-c).

A separately versioned contract extension (agent-521 ruling f14c71ec, relayed by agent-449; the
specification is the independent review's "Control A remedy proposal",
reviews/ee442-native-positive-c-sol.md). It is a coordinator decision, not owner text, and not a
retrospective claim that verdict-2r4 refused control A: scripts/k91_classify.py (CLASSIFIER_V2,
verdict-2r4) is unchanged, stays callable, and its INCOMPLETE stands until a separately reviewed
evaluation under CLASSIFIER_V2A1 exists.

What A1 adds, and nothing else: for a_mismatched_runtime ONE further accepted refusal reason, the
exact full message WRONG_HEAD_MESSAGE that production routing_adapter.validate_campaign_step raised
when the dispatched different-commit runtime M (kicad-route b5774496) was presented to the proposed
bd1e633f pin. It is accepted by literal equality only (no regular expression, substring, prefix or
suffix), at the consumer root AND at the separate host binding, and only when every one of these
holds:

- the consumer chain is one schema-valid, null-terminated link: type ValueError, origin
  routing_adapter.py:validate_campaign_step, frames passing, in order, CONSUMER_PATH;
- the host binding is an observed refusal whose chain is one such link with the same message, origin
  and type, frames passing, in order, HOST_PATH;
- the two commit identities in the message are the retained captured M (identities['mismatch_commit'])
  and the proposed L (the manifest's runtime.commit and pin.commit, identities['proposed_commit']),
  and they differ;
- the consumer's run_step, verify and adopt layers are each an explicit not-called record, the
  consumer design is unchanged, the run_step was executed, and the consumer and direct design copies
  are distinct (k91_classify.schema_problems, unchanged).

Every other control, and the original same-commit reasons of control A, are judged by
k91_classify exactly as before: A1 first asks the original contract, and adds the literal path
only where that contract did not already refuse. Without identities the literal path never
applies. Nothing here runs a router, writes a record or reads anything but the dicts it is given.
"""
import k91_classify as v2

CLASSIFIER_V2A1 = 'agent-ee.k91-native-positive/verdict-2r4-a1'
AMENDMENT = 'agentee-442/control-a-wrong-head/1 (agent-521 f14c71ec; reviews/ee442-native-positive-c-sol.md)'
CONTROL = 'a_mismatched_runtime'
MISMATCH_COMMIT = 'b5774496d8466b67ffa0362b6665fee2108155a3'
PROPOSED_COMMIT = 'bd1e633f2e585f4fc931ceab001299b78d0d8bb7'
WRONG_HEAD_MESSAGE = (
    'kicad-route does not qualify: checkout HEAD b5774496d8466b67ffa0362b6665fee2108155a3 is not the pinned '
    'bd1e633f2e585f4fc931ceab001299b78d0d8bb7; no automatic update; '
    'tool file differs from pin: src/kicad_route/cli.py; '
    'tool file differs from pin: src/kicad_route/common.py; '
    'tool file differs from pin: src/kicad_route/configuration_evidence.py; '
    'tool file differs from pin: src/kicad_route/copper_ownership.py; '
    'tool file differs from pin: src/kicad_route/corpus.py; '
    'tool file differs from pin: src/kicad_route/evidence.py; '
    'pinned file missing: src/kicad_route/experiment.py; '
    'pinned file missing: src/kicad_route/kicad/emit_additions.py; '
    'pinned file missing: src/kicad_route/repair_child.py; '
    'pinned file missing: src/kicad_route/repair_trial.py; '
    'tool file differs from pin: src/kicad_route/router/py_router/pour_guard.py')
assert WRONG_HEAD_MESSAGE.startswith(
    f'kicad-route does not qualify: checkout HEAD {MISMATCH_COMMIT} is not the pinned {PROPOSED_COMMIT}; ')
ORIGIN = ('routing_adapter.py', 'validate_campaign_step')
CONSUMER_PATH = ('campaign.py:run_implementation_steps', 'routing_adapter.py:validate_live_binding',
                 'routing_adapter.py:validate_campaign_step')
HOST_PATH = ('k91_consumer.py:binding', 'routing_adapter.py:validate_campaign_step')
assert CONSUMER_PATH == v2.CONTRACTS[CONTROL]['path'] and ORIGIN == v2.CONTRACTS[CONTROL]['origin']


def identities_of(manifest, mismatch_capture):
    """The proposed L from the manifest (runtime.commit, which must equal pin.commit) and the retained
    captured M (a record with its 'commit'); None when either is absent or L is inconsistent."""
    runtime = manifest.get('runtime') if isinstance(manifest, dict) else None
    pin = manifest.get('pin') if isinstance(manifest, dict) else None
    proposed = runtime.get('commit') if isinstance(runtime, dict) else None
    mismatch = mismatch_capture.get('commit') if isinstance(mismatch_capture, dict) else None
    if not isinstance(pin, dict) or pin.get('commit') != proposed or not isinstance(proposed, str) \
            or not isinstance(mismatch, str):
        return None
    return {'proposed_commit': proposed, 'mismatch_commit': mismatch}


def _literal_link(link, path):
    return (link['type'] == 'ValueError' and link['message'] == WRONG_HEAD_MESSAGE
            and (link['origin']['file'], link['origin']['function']) == ORIGIN and v2._in_order(link['frames'], path))


def wrong_head_problems(record, identities):
    """[] when the record is the positively observed wrong-commit refusal of A1, else the reasons not."""
    problems = []
    if not isinstance(identities, dict) or identities.get('mismatch_commit') != MISMATCH_COMMIT \
            or identities.get('proposed_commit') != PROPOSED_COMMIT or MISMATCH_COMMIT == PROPOSED_COMMIT:
        problems.append('the message commits are not the retained captured M and the proposed L')
    consumer = v2.observed(record.get('consumer'))
    chain = v2.chain_of(consumer) if consumer is not None and not consumer['accepted'] else None
    if not chain or len(chain) != 1 or not _literal_link(chain[0], CONSUMER_PATH):
        problems.append('the consumer chain is not the single A1 literal refusal on the consumer path')
    host = v2.observed(record.get('host_binding'))
    host_chain = v2.chain_of(host) if host is not None and not host['accepted'] else None
    if not host_chain or len(host_chain) != 1 or not _literal_link(host_chain[0], HOST_PATH):
        problems.append('the host binding is not the single A1 literal refusal on the host path')
    for layer in v2.CONTRACTS[CONTROL]['layers']:
        if v2.observed(record.get(layer)) is not None or not v2.skipped(record.get(layer)):
            problems.append(f'{layer} is not not_called')
    problems += [f'precondition not shown: {p}' for p in ('design_unchanged', 'executed')
                 if not v2._precondition(p, record, v2.CONTRACTS[CONTROL])]
    return problems


def contract_match(name, record, identities=None):
    """k91_classify.contract_match, plus A1's literal wrong-commit path for control A only."""
    problems = v2.contract_match(name, record)
    if name != CONTROL or not problems:
        return problems
    amended = wrong_head_problems(record, identities)
    return [] if not amended else problems + [f'A1: {p}' for p in amended]


def consumer_state(record, name=None, identities=None):
    """k91_classify.consumer_state with A1's contract_match (the same schema and not_refused rules)."""
    if name not in v2.CONTRACTS or v2.schema_problems(record):
        return v2.INCOMPLETE
    adopt = v2.observed(record['consumer_adopt'])
    if record['consumer']['accepted'] or record['design_before'] != record['design_after'] or (adopt and adopt['accepted']):
        return v2.NOT_REFUSED
    return v2.REFUSED if not contract_match(name, record, identities) else v2.INCOMPLETE


def explain(record, name, identities=None):
    if name not in v2.CONTRACTS:
        return ['no contract for this control']
    problems = v2.schema_problems(record)
    if problems:
        return problems
    if consumer_state(record, name, identities) == v2.NOT_REFUSED:
        return ['the consumer returned, adopted or changed the design']
    return contract_match(name, record, identities)


def v2a1_verdict(manifest, identities=None):
    """k91_classify.v2_verdict under amendment A1. identities: identities_of(manifest, captured M)."""
    controls = manifest.get('controls') if isinstance(manifest.get('controls'), dict) else {}
    states = {name: consumer_state(controls.get(name), name, identities) for name in v2.CONTROLS_V2}
    return {'classifier': CLASSIFIER_V2A1, 'amends': v2.CLASSIFIER_V2, 'amendment': AMENDMENT,
            'identities': identities, 'positive_accepted': v2.positive_accepted(manifest.get('positive')),
            'controls': states, 'required': list(v2.REQUIRED_V2),
            'controls_refused': all(states[name] == v2.REFUSED for name in v2.REQUIRED_V2),
            'not_refused_because': {name: explain(controls.get(name), name, identities) for name in v2.CONTROLS_V2
                                    if states[name] != v2.REFUSED},
            'low_level': {name: (v2.runtime_state if name == CONTROL else v2.control_state)(v2._low(controls, name))
                          for name in v2.CONTROLS_V2}}
