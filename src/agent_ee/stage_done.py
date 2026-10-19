"""Done through declared stage steps (decision 1 amendment, AGENTEE-144).

For every candidate path a declared agent-ee.stage-step/1 step writes, the bytes the final
checks graded must be the output of that path's last declared step receipt, and every stage
with a declared step for the path must have a receipt. In a staged campaign the stages are
the scope's stage order; a step declared for another stage never runs and is listed as excluded. Writer edits to such a path after a
declared stage's last receipt fail done even when every check passes. Stages with no declared
step are unaffected: the writer remains their producer. The coordinator placement adapter,
whose retained record limits the writer to footprint moves, may change a path after the
receipts of stages that precede placement. Everything is recomputed from retained,
anchored coordinator records; nothing is trusted from state.
"""

from pathlib import Path

from .storage import digest, identity, read_json

SCHEMA = 'agent-ee.stage-done/1'
ADOPTABLE = ('completed', 'partial')


def declared_steps(config, state):
    """Normalized stage-step declarations of a campaign: implementation_steps and the routing adapter."""
    from .stage_receipts import validate_step
    steps = [validate_step(s, host=False) for s in config.get('implementation_steps', [])]
    if 'routing_adapter' in config:
        binding = state.get('routing_binding')
        if not isinstance(binding, dict) or 'spec' not in binding:
            raise ValueError('stage_step_unreceipted: routing adapter has no campaign binding')
        steps.append(validate_step(binding['spec'], host=False))
    return steps


def _receipts(root, state, name):
    """Anchored receipts of one implementation, in index order, with their index link."""
    work = Path(root) / 'implementations' / name
    path = work / 'stage-receipts.json'
    if not path.is_file():
        return []
    anchors = state.get('stage_receipts') or {}
    if anchors.get(name) != digest(path):
        raise ValueError('stage receipt index changed or missing')
    receipts = []
    for k, link in enumerate(read_json(path)['receipts'], 1):
        receipt_path = work / link['path']
        if not receipt_path.is_file() or digest(receipt_path) != link['sha256']:
            raise ValueError('stage receipt changed after indexing')
        receipts.append((k, link['sha256'], read_json(receipt_path)))
    return receipts


def _events(root, state, config, declared, paths):
    """Ordered changes of each path across the campaign's implementations."""
    from .stage_receipts import after_tree
    history = state['history']
    events = {rel: [] for rel in paths}
    adapter = 'placement_adapter' in config
    for number in range(1, len(history)):
        name = str(number)
        work = Path(root) / 'implementations' / name
        source = read_json(Path(history[number - 1]['run']) / 'candidate.json')
        captured = read_json(Path(history[number]['run']) / 'candidate.json')
        staged = source['scope'].get('stages') is not None
        turn = history[number - 1].get('earliest_failing_stage') if staged else None
        where = {'implementation': number, 'turn': turn}
        if captured.get('scope_path') != str(work / 'proposal.scope.json'):
            # A run not captured from this implementation's proposal, for example a bench transition.
            for rel in paths:
                if captured['files'].get(rel) != source['files'].get(rel):
                    events[rel].append({**where, 'kind': 'outside_implementation'})
            continue
        writer = 'adapter_writer' if adapter and turn == 'placement' else 'writer'
        current = dict(source['files'])
        receipts = [r for r in _receipts(root, state, name) if r[2]['status'] in ADOPTABLE]
        for position, (k, sha, receipt) in enumerate(receipts):
            for rel in paths:
                if receipt['inputs'].get(rel) != current.get(rel):
                    events[rel].append({**where, 'kind': writer if position == 0 else 'coordinator'})
            spec = receipt['spec']
            produced = after_tree(receipt)
            for rel in paths:
                if spec in declared and rel in spec['writes']:
                    events[rel].append({**where, 'kind': 'step', 'stage': spec['stage'], 'step': spec['step'],
                                        'receipt_index': k, 'receipt_sha256': sha, 'sha256': produced.get(rel)})
                elif produced.get(rel) != receipt['inputs'].get(rel):
                    helper = adapter and turn == 'placement' and spec['stage'] == 'placement'
                    events[rel].append({**where, 'kind': 'adapter_helper' if helper else 'coordinator',
                                        'step': spec['step']})
            current = produced
        for rel in paths:
            if captured['files'].get(rel) != current.get(rel):
                events[rel].append({**where, 'kind': writer if not receipts else 'coordinator'})
    return events


def stage_done(root, state, config):
    """The stage-step binding of a criteria_satisfied campaign, or None with no declared step.

    Raises ValueError naming the unreceipted stage or the writer-made content."""
    from .stage_receipts import STAGES
    declared = declared_steps(config, state)
    if not declared:
        return None
    final = read_json(Path(state['history'][-1]['run']) / 'candidate.json')
    # A staged campaign's stages are its scope's stage order; the step plan never runs another stage.
    staged = final['scope'].get('stages')
    order = staged['order'] if staged is not None else STAGES
    excluded = [{'stage': s['stage'], 'step': s['step'], 'reason': 'stage not in the scope stage order'}
                for s in declared if s['stage'] not in order]
    # Opt-in authored inputs must still be the bytes consumed by each producer's
    # last receipt. Output provenance alone would allow stale intent at done.
    for spec in (s for s in declared if s['stage'] in order and s.get('inputs')):
        latest = None
        for number in range(1, len(state['history'])):
            for _, _, receipt in _receipts(root, state, str(number)):
                if receipt['spec'] == spec and receipt['status'] in ADOPTABLE:
                    latest = receipt
        if latest is None:
            raise ValueError(f"stage_step_unreceipted: {spec['stage']}/{spec['step']} has no adopted receipt")
        for rel in spec['inputs']:
            if latest['inputs'].get(rel) is None or latest['inputs'].get(rel) != final['files'].get(rel):
                raise ValueError(f"stage_step_stale_input: {spec['stage']}/{spec['step']} did not consume "
                                 f"the final {rel}")
    owners = {}
    for spec in (s for s in declared if s['stage'] in order):
        for rel in spec['writes']:
            owners.setdefault(rel, set()).add(spec['stage'])
    events = _events(root, state, config, declared, sorted(owners))
    paths = {}
    for rel, stages in sorted(owners.items()):
        timeline = events[rel]
        last = {}
        for i, event in enumerate(timeline):
            if event['kind'] == 'step':
                last[event['stage']] = i
        for stage in sorted(stages, key=STAGES.index):
            if stage not in last:
                names = sorted(s['step'] for s in declared if s['stage'] == stage and rel in s['writes'])
                raise ValueError(f"stage_step_unreceipted: {stage}/{','.join(names)} has no adopted receipt "
                                 f"producing {rel}; writer-made content cannot satisfy a declared stage")
        for i, event in enumerate(timeline):
            if event['kind'] == 'step':
                continue
            closed = [s for s in stages if last[s] < i]
            if not closed:
                continue  # an input prepared before every declared stage's last step consumed it
            if event['kind'] in ('adapter_writer', 'adapter_helper') and all(
                    STAGES.index(s) < STAGES.index('placement') for s in closed):
                continue
            raise ValueError(f"writer_made_stage_content: {rel} changed by {event['kind']} in implementation "
                             f"{event['implementation']} (turn {event['turn']}) after the last receipt of "
                             f"declared stage {sorted(closed, key=STAGES.index)[-1]}")
        producer = timeline[max(last.values())]
        if final['files'].get(rel) != producer['sha256']:
            raise ValueError(f'writer_made_stage_content: graded {rel} differs from the output of its last '
                             f"declared step {producer['stage']}/{producer['step']}")
        keep = ('stage', 'step', 'implementation', 'receipt_index', 'receipt_sha256')
        paths[rel] = {'sha256': producer['sha256'], 'producer': {k: producer[k] for k in keep},
                      'stages': {s: {k: timeline[last[s]][k] for k in keep}
                                 for s in sorted(stages, key=STAGES.index)}}
    record = {'schema': SCHEMA, 'candidate_id': final['candidate_id'], 'paths': paths, 'excluded': excluded}
    return {**record, 'identity': identity(record)}


def record_done(root, state, config):
    """At criteria_satisfied: bind the stage receipts, or fail done with the named reason."""
    try:
        record = stage_done(root, state, config)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        state.pop('fab_artifacts', None)
        state.update(state='incomplete', error=f'{type(exc).__name__}: {exc}')
        return
    if record is not None:
        state['stage_done'] = record


def verify_done(root, state, config):
    """verify-campaign: a criteria_satisfied campaign with declared steps carries the recomputed binding."""
    expected = stage_done(root, state, config) if state['state'] == 'criteria_satisfied' else None
    if state.get('stage_done') != expected:
        raise ValueError('campaign stage-step done binding differs from the retained stage receipts')


def driver_term(root, state):
    """The rung-1 drivers' stage-step done term, read from the campaign root's configuration.

    With declared steps the final state must carry a binding; verify-campaign, which the drivers
    also require to exit 0 on this same root, has then recomputed it. A root without config.json
    never verifies, so required is None there and the driver's verify-campaign term decides."""
    root = Path(root)
    config = read_json(root / 'config.json') if (root / 'config.json').is_file() else None
    required = None if config is None else bool(config.get('implementation_steps')) or 'routing_adapter' in config
    record = (state or {}).get('stage_done')
    bound = not required or (isinstance(record, dict) and record.get('schema') == SCHEMA and
                             isinstance(record.get('paths'), dict) and bool(record['paths']))
    return {'required': required, 'stage_done': record, 'bound': bound}
