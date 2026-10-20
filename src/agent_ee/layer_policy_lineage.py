"""Coordinator layer_policy derivation at the pre-routing boundary (AGENTEE-260).

A sealed campaign cannot state a numeric ``pour_area_loss_pct``: AGENTEE-257 derives it
from the placed, planed, unrouted routing input, which exists only after a routing
turn's plane producer has been adopted. A campaign opts in with
``layer_policy_derivation``: the policy fields that are source-backed at configuration
(``signal_layers``, optional ``layer_costs`` and ``reference``), a coordinator capture
directory, and optionally the signal-via budget ``N`` with its recorded basis. Only an
``agent-ee.via-budget/2`` basis, bound to a coordinator pilot whose ``V_i`` re-derive from
retained attempt facts (AGENTEE-265), can derive a bound; a counts-only
``agent-ee.via-budget/1`` basis is still accepted as configuration but leaves the policy
unknown.

On every routing turn the coordinator, never the writer, runs ``pour_budget.measure`` on
the adopted placed and planed board, derives the bound from ``N`` and writes one record
before the router starts. The campaign state anchors the record; the routing step is the
frozen campaign binding with only the derived policy added. A missing or failed
measurement, a missing, counts-only or stale ``N`` basis, or a refused derivation leaves
the policy unknown and the router is not run. After the routed proposal is adopted, the record and
the measurement are copied into the next candidate and declared as
``layer_policy_provenance`` of the new derived scope; the sealed scope is never edited.

Campaign verification re-derives every record from its retained measurement and the
frozen configuration, binds it to the routing input of its turn and to the routing
receipt's exact command, and refuses a bound that is larger than an earlier bound on the
same routing input (no relaxation after a failed attempt).
"""
import copy
import math
import os
from pathlib import Path
import re

from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.layer-policy-derivation/1'
RECORD = 'agent-ee.layer-policy-record/1'
VIA_BUDGET = 'agent-ee.via-budget/1'
VIA_BUDGET_PILOT = 'agent-ee.via-budget/2'
PILOT_DIRECTORY = 'via-budget-pilot'
DECLARATION = 'layer_policy_provenance'
BOUNDARY = 'layer-policy'
RECORD_FILE = 'record.json'
MEASUREMENT_FILE = 'pour-budget.json'
# AGENTEE-257 via-budget method: K >= 3 complete diagnostic attempts, a 10 % margin of at least one via.
MIN_PILOT_ATTEMPTS = 3
SHA = re.compile(r'[0-9a-f]{64}')


def config_of(config):
    """The validated opt-in, or None when the campaign does not derive its layer policy."""
    value = config.get('layer_policy_derivation')
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA or
            not {'schema', 'directory', 'policy'} <= set(value) or
            set(value) - {'schema', 'directory', 'policy', 'via_budget'}):
        raise ValueError(f'layer_policy_derivation must be {SCHEMA} with directory, policy and optional via_budget')
    name = value['directory']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('layer_policy_derivation directory must name one top-level candidate capture directory')
    policy = value['policy']
    if not isinstance(policy, dict) or 'pour_area_loss_pct' in policy:
        raise ValueError('layer_policy_derivation policy cannot state pour_area_loss_pct; '
                         'the coordinator derives it at the pre-routing boundary')
    from .kicad_route_attempt import RequestError, validate_layer_policy
    try:
        # The derived bound is the only missing field; any value in range shows the rest is valid.
        validate_layer_policy({**policy, 'pour_area_loss_pct': 0})
    except RequestError as exc:
        raise ValueError(f'layer_policy_derivation policy: {exc}') from None
    if 'via_budget' in value:
        validate_via_budget(value['via_budget'])
    return value


def pilot_budget(counts):
    """(N, margin) from complete attempts' signal via counts (AGENTEE-257), or ValueError."""
    if (not isinstance(counts, list) or len(counts) < MIN_PILOT_ATTEMPTS or
            any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts)):
        raise ValueError(f'via budget basis needs at least {MIN_PILOT_ATTEMPTS} complete attempts\' signal via counts')
    margin = max(1, math.ceil(0.1 * max(counts)))
    if max(counts) - min(counts) > margin:
        raise ValueError('pilot attempts differ by more than the margin; the router is not repeatable '
                         'enough on this board to choose N')
    return max(counts) + margin, margin


def validate_via_budget(value):
    """The recorded basis of N (AGENTEE-257 via-budget method); N is recomputed, never trusted.

    ``agent-ee.via-budget/2`` also links the coordinator pilot basis by hash (AGENTEE-265);
    ``verify_pilot`` re-derives V_i from its retained attempt facts at campaign start. The
    counts-only ``agent-ee.via-budget/1`` shape still validates, but ``decide`` leaves its
    policy unknown: its counts are not re-derivable."""
    from .pour_budget import MAX_VIA_BUDGET
    keys = {'schema', 'via_budget', 'board_sha256', 'basis'}
    if (not isinstance(value, dict) or value.get('schema') not in (VIA_BUDGET, VIA_BUDGET_PILOT) or
            set(value) != (keys | {'pilot'} if value['schema'] == VIA_BUDGET_PILOT else keys)):
        raise ValueError(f'via_budget must be {VIA_BUDGET} with via_budget, board_sha256 and basis, '
                         f'or {VIA_BUDGET_PILOT} with them and the pilot basis link')
    n, board, basis = value['via_budget'], value['board_sha256'], value['basis']
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_VIA_BUDGET:
        raise ValueError(f'via budget must be an integer from 1 to {MAX_VIA_BUDGET}')
    if not isinstance(board, str) or not SHA.fullmatch(board):
        raise ValueError('via budget board_sha256 must be a sha256 hex digest')
    if (not isinstance(basis, dict) or set(basis) != {'method', 'pilot', 'signal_vias'} or
            basis['method'] != 'routed_demand' or basis['pilot'] != 'diagnostic_not_adoptable'):
        raise ValueError('via budget basis must be routed_demand from a diagnostic, non-adoptable pilot')
    expected, _ = pilot_budget(basis['signal_vias'])
    if n != expected:
        raise ValueError(f'via budget {n} is not V_max + max(1, ceil(0.1 V_max)) = {expected}')
    if value['schema'] == VIA_BUDGET_PILOT:
        link = value['pilot']
        if (not isinstance(link, dict) or set(link) != {'path', 'sha256'} or not isinstance(link['path'], str) or
                not link['path'] or not isinstance(link['sha256'], str) or not SHA.fullmatch(link['sha256'])):
            raise ValueError('via budget pilot must link the pilot basis by path and sha256')
        from .via_budget_pilot import BASIS_FILE
        if Path(link['path']).name != BASIS_FILE:
            raise ValueError(f'via budget pilot path must name {BASIS_FILE}')
    return value


def pilot_link(config):
    """The declared pilot basis link, or None for a campaign without a /2 via budget."""
    value = config_of(config)
    budget = None if value is None else value.get('via_budget')
    return None if budget is None or budget['schema'] != VIA_BUDGET_PILOT else budget['pilot']


def verify_pilot(base, config, *, imported, tool=None):
    """Re-derive the declared via budget from the retained pilot facts; raise on any difference.

    ``base`` is the campaign configuration's directory at start and the campaign root after
    import. ``tool``, when given, is the router identity the campaign binds."""
    from . import via_budget_pilot as pilot
    from .routing_adapter import validate_declaration
    link = pilot_link(config)
    retained = Path(base) / PILOT_DIRECTORY
    if link is None:
        if imported and os.path.lexists(retained):
            raise ValueError('via budget pilot retained without a declared pilot basis')
        return None
    if imported and link['path'] != f'{PILOT_DIRECTORY}/{pilot.BASIS_FILE}':
        raise ValueError('via budget pilot basis is not campaign-local')
    path = Path(base) / link['path']
    if path.is_symlink() or not path.is_file() or digest(path) != link['sha256']:
        raise ValueError('via budget pilot basis changed, unsafe or missing')
    basis = pilot.verify(path)
    if basis['status'] != 'derived':
        raise ValueError(f'via budget pilot derived no N: {basis["reason"]}')
    budget = config_of(config)['via_budget']
    if (basis['board_sha256'], basis['signal_vias'], basis['via_budget']) != (
            budget['board_sha256'], budget['basis']['signal_vias'], budget['via_budget']):
        raise ValueError('via budget differs from the V_i re-derived from the retained pilot attempt facts')
    if basis['declaration'] != validate_declaration(config['routing_adapter']):
        raise ValueError('via budget pilot forked another routing declaration than the campaign binds')
    if basis['pilot_policy'] != pilot.pilot_policy(config_of(config)['policy']):
        raise ValueError('via budget pilot layer policy is not the pilot form of the campaign policy')
    if tool is not None and any(a['tool'] != tool for a in basis['attempts']):
        raise ValueError('via budget pilot ran another router than the campaign routing binding')
    return basis


def import_pilot(root, config, source, *, tool=None):
    """Copy a verified pilot into the campaign root and point the frozen configuration at it.

    The caller verified the source; the copy is verified again where the campaign keeps it."""
    from . import via_budget_pilot as pilot
    link = pilot_link(config)
    if link is None:
        return config
    pilot.copy((Path(source) / link['path']).parent, Path(root) / PILOT_DIRECTORY)
    config = copy.deepcopy(config)
    config['layer_policy_derivation']['via_budget']['pilot']['path'] = f'{PILOT_DIRECTORY}/{pilot.BASIS_FILE}'
    verify_pilot(root, config, imported=True, tool=tool)
    return config


def directory(config):
    value = config_of(config)
    return None if value is None else value['directory']


def validate_campaign(config, scope, steps=None):
    """Refuse an opt-in that cannot bind, or a sealed scope or literal policy standing in for it."""
    value = config_of(config)
    if value is None:
        if DECLARATION in scope:
            raise ValueError(f'scope declares {DECLARATION} without a campaign layer_policy_derivation')
        return
    if 'routing_adapter' not in config:
        raise ValueError('layer_policy_derivation requires the owned routing_adapter')
    if 'layer_policy' in config['routing_adapter']:
        raise ValueError('layer_policy_derivation and a literal routing_adapter layer_policy are exclusive; '
                         'no fixed limit may stand in for the derived bound')
    if DECLARATION in scope:
        raise ValueError(f'sealed scope cannot declare {DECLARATION}; the coordinator derives it '
                         'at the pre-routing boundary')
    name = value['directory']
    if name == config.get('rating_provenance'):
        raise ValueError('layer_policy_derivation and rating_provenance need different capture directories')
    if any(Path(item['at']).parts[0] == name for item in scope['inputs']):
        raise ValueError(f'layer_policy_derivation directory {name} collides with captured inputs')


def decide(report, board_sha256, config):
    """(status, reason, derivation, layer_policy) for one boundary; pure, so replay recomputes it."""
    from .pour_budget import SCHEMA as MEASUREMENT, derive
    value = config_of(config)
    if not isinstance(report, dict) or report.get('schema') != MEASUREMENT:
        return 'unknown', 'no pour budget measurement of the routing input', None, None
    if report.get('execution') != 'ok':
        return 'unknown', 'pour budget measurement failed: ' + str(report.get('reason')), None, None
    if report.get('board_sha256') != board_sha256:
        return 'unknown', 'the pour budget measurement is not of this routing input', None, None
    budget = value.get('via_budget')
    if budget is None:
        return 'unknown', 'no via budget N with a recorded basis is declared', None, None
    if budget['schema'] != VIA_BUDGET_PILOT:
        # AGENTEE-265: stated counts are not evidence; only a pilot whose V_i re-derives counts.
        return 'unknown', (f'the via budget basis is a counts-only {VIA_BUDGET} declaration; its V_i are not '
                           f're-derivable from retained pilot attempt facts (declare {VIA_BUDGET_PILOT} from '
                           'agent_ee.via_budget_pilot)'), None, None
    if budget['board_sha256'] != board_sha256:
        return 'unknown', ('the via budget basis was measured on another board '
                           f'({budget["board_sha256"]}), not this routing input'), None, None
    try:
        derivation = derive(report, budget['via_budget'])
    except (ValueError, KeyError, TypeError) as exc:
        return 'unknown', f'pour budget derivation refused: {exc}', None, None
    return 'derived', None, derivation, {**value['policy'], 'pour_area_loss_pct': derivation['pour_area_loss_pct']}


def boundary(work, design, board, board_sha256, config, number, source_candidate_id):
    """Measure and derive on the adopted routing input; write and return the turn's record."""
    from .pour_budget import measure
    out = Path(work) / BOUNDARY
    out.mkdir()
    try:
        report = measure(design, board, out / 'measure')
    except (ValueError, OSError) as exc:
        report = None
        failure = f'pour budget measurement did not run: {exc}'
    measured = out / 'measure' / MEASUREMENT_FILE
    status, reason, derivation, policy = decide(report, board_sha256, config)
    if report is None:
        reason = failure
    record = {'schema': RECORD, 'implementation': number, 'source_candidate_id': source_candidate_id,
              'board': board, 'board_sha256': board_sha256,
              'measurement': ({'path': f'measure/{MEASUREMENT_FILE}', 'sha256': digest(measured)}
                              if measured.is_file() and not measured.is_symlink() else None),
              'via_budget': config_of(config).get('via_budget'), 'status': status, 'reason': reason,
              'derivation': derivation, 'layer_policy': policy}
    write_json(out / RECORD_FILE, record)
    return record


def routing_spec(config, manifest, mutable, policy):
    """The frozen routing step with only the derived layer policy added."""
    from .routing_adapter import campaign_spec
    return campaign_spec({**config['routing_adapter'], 'layer_policy': policy}, manifest, mutable)


def declared_form(spec):
    """A routing adapter step with its --layer-policy argument removed: the frozen campaign declaration."""
    argv = list(spec['argv'])
    if spec['stage'] == 'routing' and spec['step'].startswith('kicad-route-') and '--layer-policy' in argv:
        i = argv.index('--layer-policy')
        del argv[i:i + 2]
    return {**spec, 'argv': argv}


def turn_binding(binding, spec):
    """The campaign routing binding for one turn; only the derived policy argument may differ."""
    if declared_form(spec) != binding['spec'] or '--layer-policy' in binding['spec']['argv']:
        raise ValueError('derived routing step differs from the frozen campaign routing binding')
    return {**binding, 'spec': spec}


def load_record(state, work):
    """The anchored record of an implementation, or None when the turn reached no boundary."""
    path = Path(work) / BOUNDARY / RECORD_FILE
    anchor = (state.get('layer_policy_records') or {}).get(Path(work).name)
    if anchor is None:
        if path.exists() or path.is_symlink():
            raise ValueError('layer policy record without a campaign anchor')
        return None
    if path.is_symlink() or not path.is_file() or digest(path) != anchor:
        raise ValueError('layer policy record changed after it was anchored')
    return read_json(path)


def planned_spec(config, state, work, manifest, mutable):
    """The routing step a turn was owed: derived when its record derived a policy."""
    from .routing_adapter import campaign_spec
    if config_of(config) is None:
        return campaign_spec(config['routing_adapter'], manifest, mutable)
    record = load_record(state, work)
    if record is None or record.get('status') != 'derived':
        return campaign_spec(config['routing_adapter'], manifest, mutable)
    return routing_spec(config, manifest, mutable, record['layer_policy'])


def verify_turn(root, state, config, number, routing_input, routing_receipt):
    """Re-derive one turn's record and bind it to its routing input and routing receipt.

    ``routing_input`` is the board hash the router was owed (the adopted planed board);
    ``routing_receipt`` is the turn's routing receipt or None when the router never ran."""
    work = Path(root) / 'implementations' / str(number)
    record = load_record(state, work)
    if record is None:
        if routing_receipt is not None:
            raise ValueError('routing ran without a coordinator layer policy derivation')
        return None
    source = read_json(Path(state['history'][number - 1]['run']) / 'candidate.json')
    scope = source['scope']
    if (record.get('schema') != RECORD or record['implementation'] != number or
            record['source_candidate_id'] != source['candidate_id'] or record['board'] != scope['board']):
        raise ValueError('layer policy record is not this turn\'s coordinator record')
    if record['board_sha256'] != routing_input:
        raise ValueError('layer policy record is stale: it is not of this turn\'s routing input')
    if record['via_budget'] != config_of(config).get('via_budget'):
        raise ValueError('layer policy record via budget differs from the frozen configuration')
    report = None
    if record['measurement'] is not None:
        measured = work / BOUNDARY / record['measurement']['path']
        if (record['measurement']['path'] != f'measure/{MEASUREMENT_FILE}' or measured.is_symlink() or
                not measured.is_file() or digest(measured) != record['measurement']['sha256']):
            raise ValueError('pour budget measurement changed after the layer policy record')
        report = read_json(measured)
    status, reason, derivation, policy = decide(report, routing_input, config)
    if (status, derivation, policy) != (record['status'], record['derivation'], record['layer_policy']) or (
            record['measurement'] is not None and reason != record['reason']):
        raise ValueError('layer policy record differs from the derivation of its retained measurement')
    if routing_receipt is not None:
        if status != 'derived':
            raise ValueError('routing ran with an unknown layer policy')
        if routing_receipt['inputs'].get(scope['board']) != routing_input:
            raise ValueError('routing receipt input is not the measured routing input')
        if routing_receipt['spec'] != routing_spec(config, source, state['mutable_inputs'], policy):
            raise ValueError('routing receipt layer policy is not the coordinator derivation')
    return record


def verify_records(root, state, config, turns):
    """Every anchored record belongs to a verified turn; no later bound relaxes an earlier one.

    ``turns`` maps implementation name to the verified record (or None) of each routing turn."""
    anchors = state.get('layer_policy_records') or {}
    if config_of(config) is None:
        if anchors:
            raise ValueError('layer policy records without a campaign layer_policy_derivation')
        return
    if set(anchors) - {name for name, record in turns.items() if record is not None}:
        raise ValueError('layer policy record on an implementation without a routing turn')
    bounds = {}
    for name in sorted(turns, key=int):
        record = turns[name]
        if record is None or record['status'] != 'derived':
            continue
        bound = record['layer_policy']['pour_area_loss_pct']
        earlier = bounds.get(record['board_sha256'])
        if earlier is not None and bound > earlier:
            raise ValueError('layer policy bound relaxed after an earlier attempt on the same routing input')
        bounds[record['board_sha256']] = bound if earlier is None else min(earlier, bound)


# Derived candidate scopes (the AGENTEE-256 lineage pattern).

def declaration(name, record_sha256, measurement_sha256):
    return {'record': f'{name}/{RECORD_FILE}', 'record_sha256': record_sha256,
            'measurement': f'{name}/{MEASUREMENT_FILE}', 'measurement_sha256': measurement_sha256}


def _derived(state, work):
    record = load_record(state, work)
    return record if record is not None and record['status'] == 'derived' else None


def proposal_scope(scope, work, state, config):
    """Capture a turn's derived record into the next candidate's scope; inherit otherwise."""
    name = directory(config)
    record = None if name is None else _derived(state, work)
    if record is None:
        return scope
    scope = copy.deepcopy(scope)
    source = Path(work) / BOUNDARY
    target = Path(work) / 'layer-policy-capture' / name
    target.mkdir(parents=True)
    data = {RECORD_FILE: (source / RECORD_FILE).read_bytes(),
            MEASUREMENT_FILE: (source / record['measurement']['path']).read_bytes()}
    for key, value in data.items():
        (target / key).write_bytes(value)
    pinned = declaration(name, digest(target / RECORD_FILE), digest(target / MEASUREMENT_FILE))
    if (pinned['record_sha256'] != state['layer_policy_records'][Path(work).name] or
            pinned['measurement_sha256'] != record['measurement']['sha256']):
        raise ValueError('layer policy record or measurement changed before capture')
    scope['inputs'] = [i for i in scope['inputs'] if i['at'] != name] + [{'path': str(target), 'at': name}]
    scope[DECLARATION] = pinned
    return scope


def delivered_view(config, captured, delivered):
    """A captured proposal's files with the coordinator capture replaced by the delivered design's."""
    name = directory(config)
    if name is None:
        return captured
    view = {rel: sha for rel, sha in captured.items() if rel.split('/', 1)[0] != name}
    view.update({rel: sha for rel, sha in delivered.items() if rel.split('/', 1)[0] == name})
    return dict(sorted(view.items()))


def verify_candidate(root, state, config, number, manifest):
    """Refuse run ``number`` unless its layer policy provenance is what the coordinator owes it."""
    name = directory(config)
    if name is None:
        if DECLARATION in manifest['scope']:
            raise ValueError(f'candidate declares {DECLARATION} without a campaign layer_policy_derivation')
        return
    files = {rel: sha for rel, sha in manifest['files'].items() if rel.split('/', 1)[0] == name}
    if number == 1:
        expected, owed = None, {}
    else:
        previous = read_json(Path(state['history'][number - 2]['run']) / 'candidate.json')
        work = Path(root) / 'implementations' / str(number - 1)
        record = None
        if manifest.get('scope_path') == str(work / 'proposal.scope.json'):
            record = _derived(state, work)
        if record is None:
            expected = previous['scope'].get(DECLARATION)
            owed = {rel: sha for rel, sha in previous['files'].items() if rel.split('/', 1)[0] == name}
        else:
            expected = declaration(name, state['layer_policy_records'][work.name], record['measurement']['sha256'])
            owed = {expected['record']: expected['record_sha256'],
                    expected['measurement']: expected['measurement_sha256']}
    if manifest['scope'].get(DECLARATION) != expected or files != owed:
        raise ValueError('candidate layer_policy_provenance is not the coordinator binding of the '
                         'pre-routing derivation of its lineage')

