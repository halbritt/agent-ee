"""Coordinator `placement` check: one declared criterion per check.

The coordinator witness names a requirement and a check, not a predicate, so
mixed defect/unknown results cannot be isolated inside one check; each check
evaluates exactly one criterion. Native DRC comes from an earlier `drc` check
of the same candidate and is never re-run here: the only tool execution is the
read-only saved-board probe.
"""
import json
from pathlib import PurePosixPath

from . import electrical_locality, placement_groups, placement_provenance
from . import placement_predicates as predicates
from .placement import judge, validate_capture
from .storage import digest, read_json, write_json

COMMON = {'criterion', 'drc_check', 'policy', 'routing_feedback'}
EXTRA = {'edge-clearance/1': {'min_courtyard_edge_mm', 'constraint_provenance'}, 'connector-edge/1': {'envelope', 'reference'},
         'ratsnest-mst/1': {'max_manhattan_mm', 'max_euclidean_mm'},
         'imu-axes/1': {'envelope', 'imu_ref', 'graphic_id', 'marker_ref', 'forward_axis', 'side',
                        'max_distance_mm', 'marker_rotation_deg', 'facts_check', 'axis_fact'},
         'outline-containment/1': {'envelope'}, 'placement-groups/1': {'groups'}, 'electrical-locality/1': {'locality'}}
REQUIRED = {'edge-clearance/1': {'min_courtyard_edge_mm'}, 'connector-edge/1': {'envelope'},
            'placement-groups/1': {'groups'}, 'electrical-locality/1': {'locality'}}
MAXIMA = {'max_manhattan_mm': 'manhattan', 'max_euclidean_mm': 'euclidean'}


def parameters(params):
    """Validate declared parameters; return the immutable input paths. Raises ValueError."""
    if not isinstance(params, dict):
        raise ValueError('placement params must be an object')
    criterion = params.get('criterion')
    if criterion not in predicates.CRITERIA:
        raise ValueError(f'placement requires one criterion of {list(predicates.CRITERIA)}')
    if criterion == 'imu-axes/1':
        from .imu_axes import parameters as imu_parameters
        imu_parameters(params)
    if criterion == 'keep-apart/1':
        from .placement import keep_apart_rules
        if not keep_apart_rules(params.get('policy')):
            raise ValueError('keep-apart/1 requires nonempty declared keep_apart rules')
    unknown = set(params) - COMMON - EXTRA.get(criterion, set())
    missing = REQUIRED.get(criterion, set()) - set(params)
    if unknown or missing:
        raise ValueError(f'placement {criterion} params: unknown {sorted(unknown)}, missing {sorted(missing)}')
    if not isinstance(params.get('drc_check'), str) or not params['drc_check']:
        raise ValueError('placement requires the id of an earlier drc check')
    if 'reference' in params:
        from .board_envelope import name
        name(params['reference'], 'connector reference')
    if 'min_courtyard_edge_mm' in params:
        value_nm = predicates.exact_nm(params['min_courtyard_edge_mm'], 'min_courtyard_edge_mm')
        declared = params.get('constraint_provenance', {})
        if not isinstance(declared, dict) or set(declared) - {'min_courtyard_edge_mm'}:
            raise ValueError('edge-clearance constraint_provenance names only min_courtyard_edge_mm')
        placement_provenance.classify(declared, 'min_courtyard_edge_mm', 'courtyard edge margin', value_nm)
    for key in MAXIMA:
        if key in params:
            predicates.exact_nm(params[key], key)
    paths = []
    if 'envelope' in params:
        paths.append(_path(params['envelope'], 'envelope'))
    if 'locality' in params:
        paths.append(_path(params['locality'], 'electrical locality'))
    if 'groups' in params:
        paths.append(_path(params['groups'], 'placement groups'))
    if 'policy' in params and not isinstance(params['policy'], dict):
        raise ValueError('placement policy must be an inline captured declaration')
    feedback = params.get('routing_feedback')
    if feedback is not None:
        if (not isinstance(feedback, dict) or set(feedback) != {'result', 'reference_files'}
                or not isinstance(feedback['reference_files'], dict) or not feedback['reference_files']):
            raise ValueError('routing_feedback needs result and nonempty reference_files')
        paths.append(_path(feedback['result'], 'routing result'))
        for name, path in feedback['reference_files'].items():
            if not isinstance(name, str) or not name:
                raise ValueError('routing reference file names must be text')
            paths.append(_path(path, 'routing reference file'))
    return paths


def predicate_names(params):
    criterion = params['criterion']
    if criterion == 'imu-axes/1':
        return [f"placement:{criterion}:{params['imu_ref']}"]
    if criterion == 'connector-edge/1' and 'reference' in params:
        return [f"placement:{criterion}:{params['reference']}"]
    if criterion != 'ratsnest-mst/1':
        return [f'placement:{criterion}']
    return [f'placement:{criterion}:{MAXIMA[key]}:max' for key in sorted(MAXIMA) if key in params]


def _path(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label} path must be text')
    rel = PurePosixPath(value)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or str(rel) != value:
        raise ValueError(f'{label} path must be candidate-relative')
    return value


def prerequisite_drc(ctx, cid):
    """The earlier native DRC of this exact candidate board, without re-running it."""
    from .checks import CheckError
    path = ctx.run_dir / 'checks' / f'{cid}.json'
    if not path.is_file():
        raise CheckError('not_run', f'prerequisite drc check {cid!r} has no result')
    try:
        prior = read_json(path)
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'prerequisite drc result unreadable: {exc}') from exc
    if prior.get('kind') != 'drc':
        raise CheckError('malformed', f'prerequisite {cid!r} is not a drc check')
    if prior.get('execution') != 'ok' or prior.get('outcome') not in ('pass', 'defect'):
        raise CheckError('not_run', f"prerequisite drc check {cid!r} was "
                                    f"{prior.get('execution')}/{prior.get('outcome')}")
    board = ctx.manifest['files'].get(ctx.scope['board'])
    details = prior.get('details') if isinstance(prior.get('details'), dict) else {}
    if prior.get('candidate_id') != ctx.manifest['candidate_id'] or details.get('board_sha256') != board:
        raise CheckError('malformed', 'prerequisite drc result belongs to a different candidate or board')
    rel = f'work/{cid}/drc.json'
    if rel not in prior.get('evidence', []) or not (ctx.run_dir / rel).is_file():
        raise CheckError('malformed', 'prerequisite drc result retains no raw drc.json')
    try:
        report = predicates.validate_drc(json.loads((ctx.run_dir / rel).read_text()))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CheckError('malformed', f'prerequisite raw DRC unusable: {exc}') from exc
    if report.get('source') != PurePosixPath(ctx.scope['board']).name:
        raise CheckError('malformed', 'raw DRC report names a different board file')
    if details.get('kicad_version') != report['kicad_version']:
        raise CheckError('malformed', 'raw DRC version differs from its retained check result')
    ctx.result['evidence'].append(rel)
    return report, {'check_id': cid, 'result_sha256': digest(path), 'drc_json': rel,
                    'drc_sha256': digest(ctx.run_dir / rel), 'kicad_version': report['kicad_version'],
                    'outcome': prior['outcome'], 'projection': details.get('projection')}


def _captured(ctx, rel):
    from .checks import CheckError
    if rel not in ctx.manifest['files'] or not (ctx.inputs / rel).is_file():
        raise CheckError('malformed', f'declared input {rel!r} was not captured')
    data = (ctx.inputs / rel).read_bytes()
    if digest(ctx.inputs / rel) != ctx.manifest['files'][rel]:
        raise CheckError('malformed', f'captured input {rel!r} changed')
    return data


def _envelope_declarations(ctx, criterion):
    """Connector declarations or courtyard overhangs read from the captured envelope bytes."""
    from .checks import CheckError
    try:
        from .board_envelope import validate
    except ImportError as exc:
        raise CheckError('unsupported', 'AGENTEE-85 envelope validator is not integrated') from exc
    raw = _captured(ctx, ctx.params['envelope'])
    try:
        validation = validate(raw)
        if criterion == 'outline-containment/1':
            return predicates.envelope_overhangs(validation), validation
        return connector_declarations(validation, ctx.params), validation
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'envelope refused: {exc}') from exc


def _groups(ctx):
    from .checks import CheckError
    try:
        return placement_groups.load(json.loads(_captured(ctx, ctx.params['groups'])))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CheckError('malformed', f'placement groups refused: {exc}') from exc


def connector_declarations(validation, params):
    declarations = predicates.envelope_declarations(validation)
    reference = params.get('reference')
    if reference is not None:
        declarations = [d for d in declarations if d['reference'] == reference]
    if not declarations:
        raise ValueError(f'no fixed connector declared for reference {reference!r}')
    if len(declarations) != 1:
        raise ValueError('connector-edge/1 requires reference when several fixed footprints are declared')
    return declarations


def _routing(ctx, facts):
    feedback = ctx.params.get('routing_feedback')
    if feedback is None:
        return None
    try:
        result = json.loads(_captured(ctx, feedback['result']))
    except ValueError as exc:
        return {'status': 'unavailable', 'authority': 'advisory', 'reason': f'result.json unreadable: {exc}'}
    digests = {name: ctx.manifest['files'].get(path) for name, path in feedback['reference_files'].items()}
    if None in digests.values():
        return {'status': 'unavailable', 'authority': 'advisory', 'reason': 'reference file not captured'}
    return predicates.routing_feedback(result, digests, facts['board_sha256'], facts['footprints'])


def evaluate(criterion, facts, report, params, declarations=None):
    if criterion == 'keep-apart/1':
        return predicates.keep_apart(facts, params['policy'])
    if criterion == 'imu-axes/1':
        from .imu_axes import evaluate as imu_evaluate
        return imu_evaluate(facts, params, declarations)
    if criterion == 'courtyard-overlap/1':
        return predicates.courtyard_overlap(facts, report)
    if criterion == 'outline-containment/1':
        return predicates.outline_containment(facts, declarations)
    if criterion == 'electrical-locality/1':
        return electrical_locality.grade(facts, declarations)
    if criterion == 'placement-groups/1':
        return placement_groups.grade(facts, declarations)
    if criterion == 'edge-clearance/1':
        return predicates.edge_clearance(facts, predicates.exact_nm(params['min_courtyard_edge_mm'],
                                                                    'min_courtyard_edge_mm'),
                                         params.get('constraint_provenance'))
    if criterion == 'footprint-keepout/1':
        return predicates.footprint_keepout(facts, report)
    if criterion == 'connector-edge/1':
        return predicates.connector_edge(facts, declarations)
    proxy = predicates.ratsnest(facts)
    maxima = {MAXIMA[k]: predicates.exact_nm(params[k], k) for k in MAXIMA if k in params}
    result = predicates.ratsnest_bounds(proxy, maxima) if maxima else {
        'criterion': criterion, 'status': 'info', 'defects': [], 'unknown': []}
    return {**result, 'totals': proxy['totals'], 'proxy': proxy}


def check_placement(ctx):
    from .checks import CheckError
    try:
        parameters(ctx.params)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    criterion = ctx.params['criterion']
    report, drc = prerequisite_drc(ctx, ctx.params['drc_check'])
    declarations = validation = None
    if criterion == 'imu-axes/1':
        from .imu_axes import captured_declaration
        declarations, validation = captured_declaration(ctx)
    elif 'envelope' in ctx.params:
        declarations, validation = _envelope_declarations(ctx, criterion)
    elif criterion == 'placement-groups/1':
        declarations = _groups(ctx)
    elif criterion == 'electrical-locality/1':
        try:
            declarations = electrical_locality.validate(electrical_locality.load_json(_captured(ctx, ctx.params['locality'])))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CheckError('malformed', f'electrical locality refused: {exc}') from exc
    board = ctx.candidate_path(ctx.scope['board'])
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe', board,
                 '--out', '/work/placement-probe.json'], timeout=600)
    ctx.evidence('placement-probe.json')
    facts = ctx.load_json('placement-probe.json', 'placement probe')
    if facts.get('board_sha256') != ctx.manifest['files'].get(ctx.scope['board']):
        raise CheckError('malformed', 'placement probe does not bind the captured board')
    if facts.get('pcbnew_version') != drc['kicad_version']:
        raise CheckError('malformed', 'probe pcbnew and prerequisite DRC versions differ')
    try:
        validate_capture(facts)
        result = evaluate(criterion, facts, report, ctx.params, declarations)
        advisory = judge(facts, ctx.params['policy']) if 'policy' in ctx.params and criterion != 'keep-apart/1' else None
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'placement input/policy unusable: {exc}') from exc
    proxy = result.pop('proxy', None)
    record = {'schema': 'agent-ee.placement-predicate/1', 'candidate_id': ctx.manifest['candidate_id'],
              'board_sha256': facts['board_sha256'], 'pcbnew_version': facts['pcbnew_version'],
              'drc': drc, **result, 'routing_feedback': _routing(ctx, facts),
              'advisory': None if advisory is None else {k: advisory[k] for k in (
                  'score', 'associations', 'advisories', 'policy')}}
    if criterion == electrical_locality.CRITERION:
        record['locality'] = {'path': ctx.params['locality'],
                              'sha256': ctx.manifest['files'][ctx.params['locality']]}
    if validation is not None:
        record['envelope'] = {'path': ctx.params['envelope'], 'envelope_id': validation['envelope_id'],
                              'envelope_sha256': validation['envelope_sha256']}
    if proxy is not None:
        write_json(ctx.work / 'ratsnest.json', proxy)
        ctx.evidence('ratsnest.json')
    write_json(ctx.work / 'placement-judgment.json', record)
    ctx.evidence('placement-judgment.json')
    ctx.result['details'].update({k: v for k, v in record.items() if k != 'schema'})
    ctx.result['limitations'].append(
        'placement predicate over saved geometry and the prerequisite native DRC; no copper, thermal, '
        'EMC or routed-length analysis; advisory score and routing feedback never pass or fail')
    status = result['status']
    if status == 'unknown':
        raise CheckError('unsupported', f'{criterion} coverage incomplete: {len(result["unknown"])} unknown item(s)')
    if status == 'info' and criterion == electrical_locality.CRITERION:
        ctx.result.update(execution='ok', outcome='info',
                          summary=f"{criterion}: {len(result['advisories'])} advisory locality bound(s), "
                                  f"{sum(a['exceeded'] for a in result['advisories'])} exceeded")
        return
    if status == 'info' and 'advisories' in result:
        ctx.result.update(execution='ok', outcome='info',
                          summary=f"{criterion}: {len(result['advisories'])} advisory unsourced limit(s), "
                                  f"{sum(a['exceeded'] for a in result['advisories'])} exceeded; no proved violation")
        return
    if status == 'info':
        ctx.result.update(execution='ok', outcome='info',
                          summary=f"{criterion}: pad-centre MST proxy {result['totals']['manhattan_nm'] / 10**6} mm "
                                  'Manhattan; no declared maximum')
        return
    if status == 'defect':
        findings = gate_findings(ctx.cid, result['defects'])
        if findings:
            ctx.result['details']['gate_findings'] = findings
    ctx.result.update(execution='ok', outcome='defect' if status == 'defect' else 'pass',
                      summary=f"{criterion}: {len(result['defects'])} proved violation(s)")


def gate_findings(cid, defects):
    """Findings per proved defect. Only connector-edge violations carry a suggested_fix, taken
    from the owner declaration that the predicate grades against; fixes stay advisory."""
    from .stages import project_findings
    entries = []
    for defect in defects:
        if 'violations' in defect:
            entries += [(defect.get('reference'), f"connector_{v.get('kind')}", connector_fix(defect, v))
                        for v in defect['violations']]
        elif 'refs' in defect:
            refs = defect['refs']
            entries.append(('+'.join(sorted(refs)) if all(isinstance(r, str) and r for r in refs) else None,
                            defect.get('kind')))
        else:
            entries.append((defect.get('ref', defect.get('metric')), defect.get('kind')))
    return project_findings(cid, entries)


def connector_fix(defect, violation):
    if violation.get('kind') == 'off_declared_edge_position':
        x, y = defect['measured']['anchor_mm']
        return {'op': 'move', 'params': {'x_mm': x, 'y_mm': y}}
    if violation.get('kind') == 'rotation':
        return {'op': 'rotate', 'params': {'rotation_deg': violation['declared_deg']}}
    if violation.get('kind') == 'side':
        return {'op': 'flip', 'params': {'side': violation['declared']}}
    return None
