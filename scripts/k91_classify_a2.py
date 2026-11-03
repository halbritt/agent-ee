"""Amendment A2 to the K91 v2 classifier: control A under recorded router versions (AGENTEE-442).

Integrator decision (agent-724, 2026-11-03), made after the owner's direction of 2026-11-03 that
dependency versions are fixed and recorded per run rather than held at one commit. It is a
coordinator decision, not owner text. scripts/k91_classify.py (CLASSIFIER_V2) and
scripts/k91_classify_a1.py (CLASSIFIER_V2A1) are unchanged records and stay callable.

Control A (a_mismatched_runtime) was written for a committed table that named one kicad-route
commit: a runtime at any other commit was refused when it was bound. That premise is retired: a
fresh run on another clean commit is accepted and recorded. The routing policy has two parts, and
A2 accepts control A only as a positive observation of both:

- the fresh host binding of the different clean commit M is accepted and records M;
- the production consumer, continuing a campaign whose routing binding recorded commit L, refuses M
  at routing_adapter.validate_live_binding with StaleRoutingEvidence, whose full message is
  continuation_message(L, M), compared by literal equality (no regular expression, substring,
  prefix or suffix).

Every one of these must hold: L and M are 40-hex commits given by the caller (identities_of: the
commit the campaign binding recorded and the HEAD of the runtime the harness presented), and they
differ; the consumer chain is one schema-valid, null-terminated link of that type, message and
origin, whose frames pass CONSUMER_PATH in order; the host binding is an observed acceptance whose
value is exactly {'commit': M}; the consumer's run_step, verify and adopt layers are explicit
not-called records; the design is unchanged and the run_step was executed. A refusal for any other
reason, a dirty runtime among them, never refuses control A here.

Every other control is judged by k91_classify exactly as before; A2 first asks the v2 contract and
adds its path only for control A where v2 did not already refuse. Without identities the A2 path
never applies. The low-level observation of control A (run_step executed directly on M) is reported
as fresh_run_state: under the retired premise it was a refusal, now it is a fresh run that records M.
Nothing here runs a router, writes a record or reads anything but the dicts it is given.
"""
import re

import k91_classify as v2

CLASSIFIER_V2A2 = 'agent-ee.k91-native-positive/verdict-2r4-a2'
AMENDMENT = ('agentee-442/control-a-continuation/1 (integrator agent-724, 2026-11-03, after the owner '
             'direction of 2026-11-03 that versions are fixed and recorded, not held)')
CONTROL = 'a_mismatched_runtime'
REFUSAL_TYPE = 'StaleRoutingEvidence'
ORIGIN = ('routing_adapter.py', 'validate_live_binding')
CONSUMER_PATH = ('campaign.py:run_implementation_steps', 'routing_adapter.py:validate_live_binding')
LAYERS = ('consumer_run_step', 'consumer_verify', 'consumer_adopt')
FRESH_RUN = 'fresh_run_recorded'
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')


def continuation_message(recorded, current):
    """The message routing_adapter.StaleRoutingEvidence carries for a campaign routing binding."""
    return (f'stale routing evidence: campaign routing binding recorded kicad-route {recorded}, but the '
            f'checkout is now {current}; rerun it on the current checkout')


def identities_of(binding, runtime_commit):
    """{'binding_commit': L, 'runtime_commit': M}: the commit the campaign routing binding recorded and
    the HEAD of the runtime presented in its place; None unless both are commits and they differ."""
    recorded = binding.get('commit') if isinstance(binding, dict) else None
    if not all(isinstance(c, str) and _COMMIT.match(c) for c in (recorded, runtime_commit)) \
            or recorded == runtime_commit:
        return None
    return {'binding_commit': recorded, 'runtime_commit': runtime_commit}


def continuation_problems(record, identities):
    """[] when the record shows both parts of the policy for control A, else the reasons not."""
    if not isinstance(identities, dict) or identities_of({'commit': identities.get('binding_commit')},
                                                         identities.get('runtime_commit')) != identities:
        return ['the binding commit L and the presented runtime commit M are not two different commits']
    recorded, current = identities['binding_commit'], identities['runtime_commit']
    problems = []
    consumer = v2.observed(record.get('consumer'))
    chain = v2.chain_of(consumer) if consumer is not None and not consumer['accepted'] else None
    link = chain[0] if chain and len(chain) == 1 else None
    if (link is None or link['type'] != REFUSAL_TYPE or link['message'] != continuation_message(recorded, current)
            or (link['origin']['file'], link['origin']['function']) != ORIGIN
            or not v2._in_order(link['frames'], CONSUMER_PATH)):
        problems.append('the consumer chain is not the single continuation-guard refusal of L by M')
    host = v2.observed(record.get('host_binding'))
    if host is None or not host['accepted'] or host['value'] != {'commit': current}:
        problems.append('the fresh host binding of M is not an observed acceptance recording M')
    for layer in LAYERS:
        if v2.observed(record.get(layer)) is not None or not v2.skipped(record.get(layer)):
            problems.append(f'{layer} is not not_called')
    problems += [f'precondition not shown: {p}' for p in ('design_unchanged', 'executed')
                 if not v2._precondition(p, record, v2.CONTRACTS[CONTROL])]
    return problems


def contract_match(name, record, identities=None):
    """k91_classify.contract_match, plus A2's continuation path for control A only."""
    problems = v2.contract_match(name, record)
    if name != CONTROL or not problems:
        return problems
    amended = continuation_problems(record, identities)
    return [] if not amended else problems + [f'A2: {p}' for p in amended]


def consumer_state(record, name=None, identities=None):
    """k91_classify.consumer_state with A2's contract_match (the same schema and not_refused rules)."""
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


def fresh_run_state(low, identities):
    """Control A's low-level record: FRESH_RUN when run_step, executed directly on M, ran the router
    and its adapter report records the clean commit M; v2.INCOMPLETE otherwise."""
    if not isinstance(low, dict) or not isinstance(identities, dict):
        return v2.INCOMPLETE
    ran, sandbox = v2.observed(low.get('run_step')), low.get('sandbox')
    if (ran is None or not ran['accepted'] or not isinstance(sandbox, dict)
            or sandbox.get('classification') not in ('complete', 'incomplete')
            or sandbox.get('commit') != identities.get('runtime_commit') or sandbox.get('clean') is not True):
        return v2.INCOMPLETE
    return FRESH_RUN


def v2a2_verdict(manifest, identities=None):
    """k91_classify.v2_verdict under amendment A2. identities: identities_of(binding, runtime commit)."""
    controls = manifest.get('controls') if isinstance(manifest.get('controls'), dict) else {}
    states = {name: consumer_state(controls.get(name), name, identities) for name in v2.CONTROLS_V2}
    return {'classifier': CLASSIFIER_V2A2, 'amends': v2.CLASSIFIER_V2, 'amendment': AMENDMENT,
            'identities': identities, 'positive_accepted': v2.positive_accepted(manifest.get('positive')),
            'controls': states, 'required': list(v2.REQUIRED_V2),
            'controls_refused': all(states[name] == v2.REFUSED for name in v2.REQUIRED_V2),
            'not_refused_because': {name: explain(controls.get(name), name, identities) for name in v2.CONTROLS_V2
                                    if states[name] != v2.REFUSED},
            'low_level': {name: (fresh_run_state(v2._low(controls, name), identities) if name == CONTROL
                                 else v2.control_state(v2._low(controls, name))) for name in v2.CONTROLS_V2}}


class Judge:
    """The v2 module under A2 for one harness run: v2_verdict applies A2 with that run's identities;
    every other name is k91_classify's own."""

    def __init__(self, identities):
        self.identities = identities

    def __getattr__(self, name):
        return getattr(v2, name)

    def v2_verdict(self, manifest):
        return v2a2_verdict(manifest, self.identities)
