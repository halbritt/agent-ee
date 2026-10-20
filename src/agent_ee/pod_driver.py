"""Prepare a pod campaign from explicit captured inputs and declared producers.

No component selection, inference or RC fallback occurs during preparation.
See docs/pod-driver.md for the plan and authority boundaries.
"""
import argparse
import copy
import io
from pathlib import Path
import shutil
import sys
import tempfile

from . import candidate, scaffold, stage_receipts, stages
from .baseline import compile_baseline
from .cli import validate_scope
from .profiles import validate_profile
from .storage import digest, identity, read_json, write_json

SCHEMA = 'agent-ee.pod-plan/1'
ORDER = ['schematic', 'placement', 'routing', 'verification']
STEPS = [('binding-fill', 'schematic'), ('emit-schematic', 'schematic'),
         ('forward-annotate', 'schematic'), ('place-board', 'placement'),
         ('add-planes', 'routing')]
# Coordinator capture directory of the adopted binding-fill --ratings receipt (AGENTEE-256).
RATING_PROVENANCE = 'rating-provenance'
# Optional envelope-content inputs; envelope, parts and board are the plan's and scope's own.
CONTENT_PATHS = ('intent', 'layout', 'antenna_facts', 'captured_rule_areas')
SOURCE = Path(__file__).resolve().parents[1]
BUDGET_NOTE = ('Historical rung-1 estimate: $10-15 review plus $1-3.6 writing per iteration '
               'for three parts; pod work may multiply this. Estimate, not measurement or spending authority.')


def _path(value):
    if not isinstance(value, str) or str(candidate.safe_relative(value)) != value:
        raise ValueError('canonical candidate-relative path required')
    return value


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return value


def _require_rf_reviewer(plan):
    from .rf_review import ROLE as RF_ROLE
    # The telemetry pod carries a GNSS receiver-to-SMA path and a BLE module antenna.
    if RF_ROLE not in [r['role'] for r in plan['review_profile']['reviewers']]:
        raise ValueError('pod review_profile requires the dedicated rf reviewer (GNSS and BLE RF paths)')


def validate_plan(plan):
    required = {'schema', 'scope', 'envelope', 'parts', 'spec', 'spec_record',
                'writer_inputs', 'implementation_steps', 'review_profile', 'implementation',
                'routing_adapter', 'routing_budget_s', 'review_budget_s'}
    if (not isinstance(plan, dict) or plan.get('schema') != SCHEMA or
            not required <= plan.keys() or
            set(plan) - required - {'max_iterations', 'max_seconds', 'placement_adapter', 'envelope_content', 'net_constraints',
                                    'layer_policy_derivation'}):
        raise ValueError('expected agent-ee.pod-plan/1 fields')
    if not isinstance(plan['scope'], str) or not plan['scope']:
        raise ValueError('scope must name a planning scope file')
    if not isinstance(plan['implementation_steps'], list):
        raise ValueError('implementation_steps must be a list')
    for step in plan['implementation_steps']:
        stage_receipts.validate_step(step, host=False)
    for key in ('envelope', 'parts', 'spec', 'spec_record'):
        _path(plan[key])
    if (not isinstance(plan['writer_inputs'], dict) or
            set(plan['writer_inputs']) != {'intent', 'deviations'}):
        raise ValueError('writer_inputs requires intent and deviations paths')
    paths = [_path(p) for p in plan['writer_inputs'].values()]
    if len(set(paths)) != 2:
        raise ValueError('intent and deviations must be different files')
    if any(Path(p).suffix != '.json' for p in paths):
        raise ValueError('writer inputs must be captured JSON records')
    if 'net_constraints' in plan:
        _path(plan['net_constraints'])
    if 'envelope_content' in plan:
        content = plan['envelope_content']
        if not isinstance(content, dict) or set(content) - {*CONTENT_PATHS, 'imu_reference'}:
            raise ValueError('envelope_content declares only ' + ', '.join((*CONTENT_PATHS, 'imu_reference')))
        for key in set(content) & set(CONTENT_PATHS):
            _path(content[key])
        if 'imu_reference' in content and (not isinstance(content['imu_reference'], str) or
                                           not content['imu_reference']):
            raise ValueError('envelope_content imu_reference must name a fixed footprint reference')
    if 'placement_adapter' in plan:
        from .placement_adapter import validate_declaration as placement_declaration
        placement = placement_declaration(plan['placement_adapter'])
        if placement['helper'] is None or placement['policy'] is None:
            raise ValueError('pod placement_adapter requires a helper and a captured packing policy')
    validate_profile(plan['review_profile'])
    validate_profile({**plan['review_profile'], 'adjudicator': plan['implementation']})
    _require_rf_reviewer(plan)
    iterations = _integer(plan.get('max_iterations', 10), 'max_iterations', 8, 100)
    routing = _integer(plan['routing_budget_s'], 'routing_budget_s', 1, 3600)
    review = _integer(plan['review_budget_s'], 'review_budget_s', 1, 86400)
    # Reserve two full routing turns, each with coordinator overhead, plus explicit review time.
    from .routing_adapter import STEP_MARGIN_S, validate_declaration
    route = validate_declaration(plan['routing_adapter'])
    if route['budget'] != routing or route['timeout'] < 2 * routing + 300:
        raise ValueError('routing adapter budget must match and timeout must cover twice budget plus 300 seconds')
    minimum = 2 * (route['timeout'] + STEP_MARGIN_S) + review
    seconds = _integer(plan.get('max_seconds', minimum), 'max_seconds', minimum, 86400)
    return iterations, seconds


def validate_configuration(plan, scope, files, inputs):
    iterations, seconds = validate_plan(plan)
    if scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('pod campaign requires scope/3')
    validate_scope(scope)
    declaration = stages.declaration(scope, files)
    if declaration is None:
        raise ValueError('pod campaign requires declared stages')
    order = declaration['order']
    if 'fab' in order:
        raise ValueError('fab in stages.order requires AGENTEE-146-compatible pod step wiring; this driver excludes fab')
    if order != ORDER:
        raise ValueError('pod stages must be schematic, placement, routing, verification')
    baseline = compile_baseline(scope, files, inputs)
    from .campaign import mutable_inputs
    mutable = set(mutable_inputs({'scope': scope, 'files': files}, baseline))
    writer = set(plan['writer_inputs'].values())
    if not writer <= mutable or writer & {scope['schematic'], scope['board']}:
        raise ValueError('captured intent/deviations must be mutable non-native inputs')
    raw_steps = plan['implementation_steps']
    if not isinstance(raw_steps, list):
        raise ValueError('implementation_steps must be a list')
    normalized = [stage_receipts.validate_step(s) for s in raw_steps]
    # The owned placement adapter replaces the generic placement step; the campaign forbids both.
    placer = 'placement_adapter' in plan
    expected = [s for s in STEPS if not (placer and s[0] == 'place-board')]
    if [(s['step'], s['stage']) for s in normalized] != expected:
        raise ValueError('pod requires ' + ', '.join(name for name, _ in expected) + ' in that order; '
                         'routing uses the owned routing_adapter' +
                         ('; placement uses the declared placement_adapter' if placer else ''))
    written = set()
    for step in normalized:
        writes = set(step['writes'])
        if step['adoptable_exits'] != [0]:
            raise ValueError('pod generic steps adopt exit 0 only; the owned router controls partial routing')
        if not writes or not writes <= mutable or writes & writer:
            raise ValueError('step writes must be coordinator outputs, outside writer inputs and inside mutable_inputs')
        written.update(writes)
    if mutable != writer | written:
        raise ValueError('mutable_inputs may contain only intent/deviations and declared coordinator outputs')
    for name, path in [('emit-schematic', scope['schematic']), ('forward-annotate', scope['board']),
                       ('place-board', scope['board']), ('add-planes', scope['board'])]:
        step = next((s for s in normalized if s['step'] == name), None)
        if step is not None and path not in step['writes']:
            raise ValueError(f'{name} must produce its native design output')
    placement = None
    if placer:
        from .placement_adapter import bind_declaration
        placement = bind_declaration(plan['placement_adapter'], {'scope': scope, 'files': files}, sorted(mutable))
        from .placer import _validate_policy
        path = inputs / placement['policy']
        if path.is_symlink() or not path.is_file() or digest(path) != files[placement['policy']]:
            raise ValueError('captured placement policy changed')
        try:
            policy = read_json(path)
            if not isinstance(policy, dict):
                raise ValueError('expected an agent-ee.packing-policy/1 object')
            if 'fixed_overhangs' in policy:
                raise ValueError('fixed_overhangs come only from a captured envelope, never from the packing policy')
            if 'constraint_provenance' in policy:
                raise ValueError('constraint_provenance comes only from a captured envelope, never from the packing policy')
            # The scaffold is still empty. Resolve references against the declared placement
            # subjects here; the adapter binds them to native footprints on each candidate.
            _validate_policy(policy, set(declaration['subjects'].get('placement', {}).get('references', [])))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f'invalid pod placement policy {placement["policy"]}: {exc}') from exc
    rating = _rating_provenance(scope, normalized)
    from .routing_adapter import campaign_spec, validate_campaign_step
    from .campaign import validate_routing_predecessors
    validate_routing_predecessors(normalized, scope)
    route = campaign_spec(plan['routing_adapter'], {'scope': scope}, sorted(mutable))
    validate_campaign_step(route)
    # AGENTEE-260: the numeric pour bound is derived by the coordinator before each routing attempt.
    from .layer_policy_lineage import validate_campaign as validate_layer_policy_derivation
    derivation = {k: plan[k] for k in ('routing_adapter', 'layer_policy_derivation') if k in plan}
    validate_layer_policy_derivation({**derivation, **({'rating_provenance': rating} if rating else {})}, scope)
    generation = scope.get('generation')
    checks = {c['id']: c for c in scope['checks']}
    if (not isinstance(generation, dict) or
            checks.get(generation.get('done_check'), {}).get('kind') != 'fab_package'):
        raise ValueError('pod generation must retain the fab_package done gate')
    if checks[generation['done_check']]['stage'] != 'verification':
        raise ValueError('fab_package must grade the final declared stage')
    from .spec_record import verify
    spec_path, record_path = inputs / plan['spec'], inputs / plan['spec_record']
    verify(record_path.read_bytes(), spec_path.read_bytes())
    spec = read_json(record_path)
    if spec['ids']['prefix'] != 'POD' or not {'POD-AC-8', 'POD-AC-9'} <= {r['id'] for r in spec['requirements']}:
        raise ValueError('expected the captured telemetry pod requirement record')
    if not {plan['spec'], plan['spec_record']} <= baseline['files'].keys():
        raise ValueError('pod spec and capture must be immutable baseline sources')
    for requirement in ('POD-AC-8', 'POD-AC-9'):
        if not any(requirement in text and 'BENCH REQUIRED' in text for text in scope.get('limitations', [])):
            raise ValueError(f'{requirement} must remain an explicit BENCH REQUIRED obligation')
    return dict(baseline_id=baseline['baseline_id'], stage_policy_id=identity(declaration),
                mutable_inputs=sorted(mutable), writer_inputs=sorted(writer),
                coordinator_outputs=sorted(written), max_iterations=iterations,
                max_seconds=seconds, implementation_steps=normalized, routing_step=route,
                **({'placement_adapter': placement} if placer else {}),
                **({'rating_provenance': rating} if rating else {}),
                **({'layer_policy_derivation': plan['layer_policy_derivation']}
                   if 'layer_policy_derivation' in plan else {}))


def _rating_provenance(scope, steps):
    """The coordinator rating capture when binding-fill --ratings feeds assembly_readiness checks.

    AGENTEE-242 grades AGENT_EE_Ratings unknown without receipted provenance, which a sealed
    scope cannot name before the step runs; the campaign derives it per candidate instead."""
    from .rating_lineage import rating_step, validate_campaign
    if not any(rating_step(s) for s in steps) or not any(c['kind'] == 'assembly_readiness' for c in scope['checks']):
        return None
    validate_campaign({'rating_provenance': RATING_PROVENANCE}, scope, steps)
    return RATING_PROVENANCE


def _scope_for_inputs(scope, inputs):
    result = copy.deepcopy(scope)
    result['inputs'] = [dict(path=str(inputs / name), at=name)
                        for name in sorted(p.name for p in inputs.iterdir())]
    return result


def _final_scope(scope, plan, inputs):
    scope = _scope_for_inputs(scope, inputs)
    scope['mutable_inputs'] = sorted(set(scope['mutable_inputs']) |
                                    set(plan['writer_inputs'].values()) |
                                    {p for s in plan['implementation_steps'] for p in s['writes']})
    return scope


def _scaffold_step(plan_dir, outputs):
    return dict(schema='agent-ee.stage-step/1', stage='scaffold', step='write-scaffold',
        argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
              '-m', 'agent_ee.pod_driver', '_scaffold', '/tool/plan/scaffold.json'],
        mounts=[dict(source=str(SOURCE), at='/tool/agent-ee'),
                dict(source=str(plan_dir), at='/tool/plan')], source='/tool/agent-ee',
        version_argv=['/usr/bin/python3', '--version'],
        writes={p: 'scaffold/inputs/' + p for p in outputs},
        artifacts=['scaffold/scaffold-receipt.json'], adoptable_exits=[0], timeout_s=300)


def _content_step(plan, scope):
    """The shared envelope-content producer, bound to the plan's envelope and parts and the scope board."""
    return stage_receipts.validate_step(scaffold.content_step(
        SOURCE.parent, plan['envelope'], plan['parts'], scope['board'], **plan['envelope_content']))


def _validate_content(plan, scope, sources):
    """Every declared content input is captured, distinct from native outputs, and sufficient."""
    content = plan['envelope_content']
    natives = {scope['board'], scope['schematic']}
    for key in sorted(set(content) & set(CONTENT_PATHS)):
        if content[key] not in sources:
            raise ValueError(f'uncaptured pod envelope_content {key}: {content[key]}')
        if content[key] in natives:
            raise ValueError(f'envelope_content {key} collides with a native output')
    from .board_envelope import validate as envelope_record
    record = envelope_record(Path(sources[plan['envelope']]).read_bytes())
    if record['fixed_footprints'] and not {'intent', 'layout'} <= content.keys():
        raise ValueError('envelope fixed footprints require declared envelope_content intent and layout')
    if any('antenna' in area for area in record['keepouts']) and 'antenna_facts' not in content:
        raise ValueError('antenna keepout requires declared envelope_content antenna_facts')
    return _content_step(plan, scope)


def _constraint_paths(scope):
    project = str(Path(scope['board']).with_suffix('.kicad_pro'))
    return project, str(Path(project).with_suffix('.kicad_dru')), str(Path(project).with_suffix('.net-constraints.record.json'))


def _apply_constraints(plan, scope, inputs, out):
    """Apply after scaffold metadata; preserve emitter record bytes without hash rewriting."""
    from .net_constraints import emit
    project, rules, record = _constraint_paths(scope)
    rules_path = inputs / rules
    artifacts = emit((inputs / plan['net_constraints']).read_bytes(), (inputs / project).read_bytes(),
                     envelope_raw=(inputs / plan['envelope']).read_bytes(),
                     rules_raw=rules_path.read_bytes() if rules_path.exists() else b'(version 1)\n')
    if 'project' not in artifacts:
        raise ValueError('net constraints could not be applied: ' + artifacts['record'].decode())
    for key, rel in (('project', project), ('rules', rules), ('record', record)):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifacts[key])


def _constraint_scope(scope, plan, inputs):
    from .net_constraints import parse_document
    scope = copy.deepcopy(scope)
    _, classes = parse_document((inputs / plan['net_constraints']).read_bytes())
    project, _, record = _constraint_paths(scope)
    check_id = 'net-constraint-assignment'
    if any(c['id'] == check_id for c in scope['checks']):
        raise ValueError('net constraint check id is reserved: ' + check_id)
    requirements = {r['id']: r for r in scope['baseline']['requirements']}
    for spec in classes:
        if spec['requirement'] not in requirements:
            raise ValueError('net constraint requires a mapped baseline requirement: ' + spec['requirement'])
        mapped = requirements[spec['requirement']]['checks']
        if check_id not in mapped:
            mapped.append(check_id)
    index = next(i for i, c in enumerate(scope['checks']) if ORDER.index(c['stage']) > ORDER.index('placement'))
    scope['checks'].insert(index, dict(id=check_id, kind='net_class_assignment', stage='placement',
                               params=dict(project=project, record=record, constraints=plan['net_constraints'])))
    return scope


def _constraint_step(plan_dir, scope):
    outputs = _constraint_paths(scope)
    return dict(schema='agent-ee.stage-step/1', stage='scaffold', step='apply-net-constraints',
        argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
              '-m', 'agent_ee.pod_driver', '_constraints', '/tool/plan/constraints.json'],
        mounts=[dict(source=str(SOURCE), at='/tool/agent-ee'), dict(source=str(plan_dir), at='/tool/plan')],
        source='/tool/agent-ee', version_argv=['/usr/bin/python3', '--version'],
        writes={p: 'constraints/' + p for p in outputs}, adoptable_exits=[0], timeout_s=120)


def run_scaffold(recipe):
    """Only the shared scaffold producer writes native files, in the receipt sandbox."""
    recipe = read_json(recipe)
    scaffold.scaffold(Path('/candidate') / recipe['envelope'], Path('/candidate') / recipe['parts'],
                      '/work/scaffold', scope_path='/tool/plan/scope.json')


def build(plan_path, out, *, dry_run=False):
    """Validate and prepare without inference; failed native work retains its receipt."""
    plan_path, out = Path(plan_path).resolve(), Path(out).absolute()
    raw = plan_path.read_bytes()
    plan = scaffold.strict_json(raw)
    validate_plan(plan)
    scope_path = (plan_path.parent / plan['scope']).resolve()
    scope_raw = scope_path.read_bytes()
    scope = scaffold.strict_json(scope_raw)
    validate_scope(scope)
    before, sources, _ = candidate.source_manifest(scope, scope_path.parent)
    for key in ('envelope', 'parts', 'spec', 'spec_record', *(['net_constraints'] if 'net_constraints' in plan else [])):
        if plan[key] not in sources:
            raise ValueError(f'uncaptured pod {key}: {plan[key]}')
    content_spec = _validate_content(plan, scope, sources) if 'envelope_content' in plan else None
    if out.exists() or out.is_symlink():
        raise ValueError('pod output must not exist')
    out = out.resolve()
    for src in sources.values():
        if out == Path(src) or out in Path(src).parents:
            raise ValueError('pod output must not contain source inputs')
    for item in scope['inputs']:
        src = candidate.source_path(item['path'], scope_path.parent)
        if src.is_dir() and (out == src or src in out.parents):
            raise ValueError('pod output must be outside source input directories')
    # This temporary preparation exercises the exact existing validators before publishing any run.
    with tempfile.TemporaryDirectory(prefix='agent-ee-pod-preflight-') as tmp:
        preview = Path(tmp) / 'scaffold'
        scaffold.scaffold(sources[plan['envelope']], sources[plan['parts']], preview, scope_path=scope_path)
        initial = read_json(preview / 'scope.json')
        prepared_scope = _final_scope(initial, plan, preview / 'inputs')
        files, _, _ = candidate.source_manifest(prepared_scope, preview)
        outputs = sorted(p for p in files if before.get(p) != files[p])
        if not {scope['schematic'], scope['board']} <= set(outputs):
            # Empty input placeholders may already equal the scaffold; still receipt-own both outputs.
            outputs = sorted(set(outputs) | {scope['schematic'], scope['board']})
        expected = dict(files)
        if 'net_constraints' in plan:
            if _constraint_paths(scope)[2] in before:
                raise ValueError('net constraint record collides with captured inputs')
            _apply_constraints(plan, scope, preview / 'inputs', preview / 'inputs')
            initial = _constraint_scope(initial, plan, preview / 'inputs')
        prepared_scope = _final_scope(initial, plan, preview / 'inputs')
        files, _, _ = candidate.source_manifest(prepared_scope, preview)
        validation = validate_configuration(plan, prepared_scope, files, preview / 'inputs')
    if plan_path.read_bytes() != raw or scope_path.read_bytes() != scope_raw or candidate.source_manifest(scope, scope_path.parent)[0] != before:
        raise ValueError('pod preparation inputs changed during validation')
    out.mkdir(parents=True)
    record = dict(schema='agent-ee.pod-driver-receipt/1', status='preparing', done=False,
                  plan_sha256=digest(plan_path), source_scope_sha256=digest(scope_path),
                  tool=dict(module_sha256=digest(Path(__file__)), python=sys.version.split()[0]),
                  source_files=before, validation=validation, inference_budget_note=BUDGET_NOTE,
                  limitations=scope.get('limitations', []),
                  acceptance='Preparation is not design acceptance; owner DFM and physical bench obligations remain.')
    write_json(out / 'driver-receipt.json', record)
    (out / 'plan.json').write_bytes(raw)
    inputs = out / 'inputs'
    for rel, src in sources.items():
        target = inputs / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        if digest(target) != before[rel]:
            raise ValueError('pod input changed while copying: ' + rel)
    tools = out / 'scaffold-plan'
    tools.mkdir()
    write_json(tools / 'scaffold.json', {'envelope': plan['envelope'], 'parts': plan['parts']})
    sandbox_scope = _scope_for_inputs(scope, inputs)
    sandbox_scope['inputs'] = [dict(path='/candidate/' + e['at'], at=e['at']) for e in sandbox_scope['inputs']]
    write_json(tools / 'scope.json', sandbox_scope)
    if 'net_constraints' in plan:
        write_json(tools / 'constraints.json', dict(plan=plan, scope=scope))
    step = _scaffold_step(tools, outputs)
    receipt_dir = out / 'scaffold-receipt'
    receipt = stage_receipts.run_step(receipt_dir, inputs, step, {'pre_receipt': 1})
    stage_receipts.adopt(receipt_dir, receipt, inputs)
    if stage_receipts.file_tree(inputs) != (expected, []):
        raise ValueError('receipted scaffold differs from validated preparation')
    link = dict(path='scaffold-receipt/receipt.json', sha256=digest(receipt_dir / 'receipt.json'), binding={'pre_receipt': 1})
    links = [link]
    constraint_link = None
    if 'net_constraints' in plan:
        constraint_dir = out / 'constraints-receipt'
        constraint = stage_receipts.run_step(constraint_dir, inputs, _constraint_step(tools, scope), {'pre_receipt': 2})
        stage_receipts.adopt(constraint_dir, constraint, inputs)
        constraint_link = dict(path='constraints-receipt/receipt.json', sha256=digest(constraint_dir / 'receipt.json'),
                               binding={'pre_receipt': 2})
        links.append(constraint_link)
    if content_spec is not None:
        # Envelope content consumes the adopted scaffold in place; its board is the only write.
        content_dir = out / 'content-receipt'
        content_binding = {'pre_receipt': len(links) + 1}
        content = stage_receipts.run_step(content_dir, inputs, content_spec, content_binding)
        stage_receipts.adopt(content_dir, content, inputs)
        links.append(dict(path='content-receipt/receipt.json', sha256=digest(content_dir / 'receipt.json'),
                          binding=content_binding))
    final_scope = _final_scope(initial, plan, inputs)
    files, _, _ = candidate.source_manifest(final_scope, out)
    final_validation = validate_configuration(plan, final_scope, files, inputs)
    if final_validation != validation:
        raise ValueError('pod validation changed after scaffold execution')
    receipts = stage_receipts.validate_pre_receipt_chain(out, links)
    binding = stage_receipts.pre_receipt_binding(receipts[-1], files)
    if not binding['bound']:
        raise ValueError('pre_receipt chain does not bind the initial candidate')
    final_scope['inputs'] = [dict(path='inputs/' + e['at'], at=e['at']) for e in final_scope['inputs']]
    config = dict(schema='agent-ee.campaign/1', scope='scope.json',
                  review_profile=plan['review_profile'], implementation=plan['implementation'],
                  writer_editable=validation['writer_inputs'],
                  implementation_steps=validation['implementation_steps'], pre_receipts=links,
                  routing_adapter=plan['routing_adapter'],
                  **({'placement_adapter': plan['placement_adapter']} if 'placement_adapter' in plan else {}),
                  **({'rating_provenance': validation['rating_provenance']} if 'rating_provenance' in validation else {}),
                  **({'layer_policy_derivation': validation['layer_policy_derivation']}
                     if 'layer_policy_derivation' in validation else {}),
                  max_iterations=validation['max_iterations'], max_seconds=validation['max_seconds'],
                  max_schema_resubmissions=1)
    write_json(out / 'scope.json', final_scope)
    write_json(out / 'campaign.json', config)
    record.update(status='prepared', campaign_sha256=digest(out / 'campaign.json'),
                  scope_sha256=digest(out / 'scope.json'), scaffold=link, first_capture_binding=binding,
                  **({'envelope_content': links[-1]} if content_spec is not None else {}),
                  **({'net_constraints': constraint_link} if constraint_link is not None else {}))
    write_json(out / 'driver-receipt.json', record)
    if dry_run:
        run_dry(out)
    return record


def run_dry(out):
    """Native checks only; keep engine outcomes and identify unexecuted later stages."""
    from .cli import run_review
    out = Path(out)
    stream = io.StringIO()
    code = run_review(out / 'scope.json', out / 'dry-run', 1200, checks_only=True, output_stream=stream)
    (out / 'dry-run-console.txt').write_text(stream.getvalue(), encoding='utf-8')
    result = read_json(out / 'dry-run/result.json')
    scope = read_json(out / 'scope.json')
    checks = result['checks']
    stages_record = dry_stage_results(checks, scope)
    reached = next((entry['stage'] for entry in stages_record
                    if any(c['execution'] != 'ok' or c['outcome'] not in ('pass', 'info')
                           for c in entry['checks'])), None)
    record = dict(schema='agent-ee.pod-dry-run/1', done=False, inference=False, exit_code=code,
                  earliest_failing_stage=reached, stages=stages_record,
                  result_sha256=digest(out / 'dry-run/result.json'))
    write_json(out / 'dry-run-record.json', record)
    return record


def dry_stage_results(checks, scope):
    """Keep unknown execution and explicit gate reasons separate from evaluated checks."""
    check_stages = {c['id']: c['stage'] for c in scope['checks']}
    result = []
    for stage in scope['stages']['order']:
        selected = [c for c in checks if check_stages[c['id']] == stage]
        reasons = {c.get('details', {}).get('reason') for c in selected}
        if selected and reasons == {'stage_blocked'}:
            status = 'stage_blocked'
        elif not selected or any(c['execution'] != 'ok' or c['outcome'] == 'unknown' for c in selected):
            status = 'unknown'
        elif selected and reasons == {'stage_not_reached'}:
            status = 'stage_not_reached'
        else:
            status = 'evaluated'
        result.append(dict(stage=stage, status=status, checks=selected))
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '_constraints':
        recipe = read_json(argv[1])
        _apply_constraints(recipe['plan'], recipe['scope'], Path('/candidate'), Path('/work/constraints'))
        return 0
    if argv and argv[0] == '_scaffold':
        run_scaffold(argv[1])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--run', action='store_true', help='explicitly launch subscription inference after preparation')
    args = parser.parse_args(argv)
    try:
        build(args.plan, args.out, dry_run=args.dry_run)
        if args.run:
            from .campaign import run_campaign
            return run_campaign(args.out / 'campaign.json', args.out / 'campaign')
        return 0
    except (OSError, ValueError) as exc:
        print(f'pod driver refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
