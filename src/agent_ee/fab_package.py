"""Generation done gate: one check that exports, gates and seals the fab package (AGENTEE-94).

Check kind `fab_package` (params exactly `{netlist_check}`) must be the last
declared check except for bound `jlc_dfm` consumers. It runs the AGENTEE-23
`fab_export` producer with the pinned `kicad-cli` in the executor sandbox into its own `work/<id>/fab/`, then the
AGENTEE-73 `fab_consistency` gate on that staging tree (the same code paths as
the standalone kinds, with the export being this check itself), then
recomputes the manifest inventory and records the sealed artifact set:

    gerbers.zip, every drill/ file (PTH, NPTH and their maps), bom.csv,
    cpl.csv and manifest.json

as `details.artifacts` (`agent-ee.fab-artifacts/1`) bound to the candidate id
and the captured board digest. The run's evidence seal binds the same files by
digest to the candidate and baseline.

Outcome `pass` requires every export to succeed and validate, the gate to find
no defect and no coverage gap, and every artifact to be present, nonempty and
listed by digest. A missing, empty or unparseable export file is execution
`failed`/`malformed`/`unsupported` with outcome `unknown`, or a gate `defect`;
never a pass. A scope carrying a `generation` marker must declare this check
last and map an acceptance requirement to it (`baseline.generation_marker`).

CPL rotation (AGENTEE-126). When the scope declares a `cpl_rotation` check
(at most one, before this one), the done gate consumes its sealed result: it
must have passed with complete coverage, be bound to this candidate and board,
and have corrected exactly the native `cpl.csv` bytes this gate exported. The
corrected `work/<cpl>/cpl/cpl.csv` then replaces the native `cpl.csv` as the
sealed CPL artifact, and `details.rotation` and the artifact record carry the
rotation status (`ok` or `PREVIEW_REQUIRED`), the policy sha256 and the report
digest. A failed or defective correction fails the gate. Without a declared
`cpl_rotation` check the native CPL is sealed and the rotation status is
`not_declared`. Whether a status may mark a generation done is
`DONE_ROTATION_STATUSES` (`rotation_blocker`), applied by every driver.
"""

import copy
from pathlib import Path

from . import fab_consistency, fab_export
from .storage import digest, identity, read_json

ARTIFACT_SCHEMA = 'agent-ee.fab-artifacts/1'
REQUIRED_FILES = ('gerbers.zip', 'bom.csv', 'cpl.csv')
PREDICATES = ['fab-package:done-gate', 'fab-package:sealed-artifacts']
EXPORT_SCOPE_NOTE = 'pass means the exports exist, parsed, agree on designators and are hashed'
# Rotation statuses a done predicate accepts (decision B, AGENTEE-126). `not_declared` keeps the
# native-CPL behaviour of a scope without a cpl_rotation check; `ok` is a declared correction with
# nothing physically pending. Every cpl_rotation result is PREVIEW_REQUIRED today, which keeps
# `done` false until the owner accepts the unvalidated bottom-side transform; that decision is
# the one-place change of adding 'PREVIEW_REQUIRED' here.
DONE_ROTATION_STATUSES = frozenset({'not_declared', 'ok'})
ROTATION_STATUSES = frozenset({'ok', 'PREVIEW_REQUIRED'})
ROTATION_REASONS = {
    'PREVIEW_REQUIRED': 'fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)'}


def rotation_blocker(status):
    """None when a rotation status may mark a generation done, else the named reason (fail closed)."""
    if status in DONE_ROTATION_STATUSES:
        return None
    return ROTATION_REASONS.get(status, f'fab: cpl rotation status {status!r} is not a done status')


def validate_params(params):
    if not isinstance(params, dict) or set(params) != {'netlist_check'}:
        raise ValueError('fab_package params must be exactly {"netlist_check"}')
    if not isinstance(params['netlist_check'], str) or not params['netlist_check']:
        raise ValueError('netlist_check must name the preceding netlist check')
    return params


def require_last(specs, spec):
    position = next((i for i, s in enumerate(specs) if s.get('id') == spec.get('id')), None)
    if position is None or any(s.get('kind') != 'jlc_dfm' or
                               s.get('params', {}).get('fab_check') != spec.get('id')
                               for s in specs[position + 1:]):
        raise ValueError('fab_package done check last: only bound jlc_dfm checks may follow')


def declared_rotation(scope):
    """The scope's one cpl_rotation check spec, or None."""
    found = [c for c in scope.get('checks', []) if c.get('kind') == 'cpl_rotation']
    if len(found) > 1:
        raise ValueError('fab_package consumes at most one cpl_rotation check')
    return found[0] if found else None


def capability(params, scope, spec, prerequisite, captured):
    """Exact params, terminal package producer, earlier netlist and geometry."""
    validate_params(params)
    require_last(scope.get('checks', []), spec)
    prerequisite(params['netlist_check'], ('netlist',))
    fab_consistency.earlier_geometry(scope, prerequisite, 'fab_package')
    rotation = declared_rotation(scope)
    if rotation is not None:
        prerequisite(rotation.get('id'), ('cpl_rotation',))
    predicates = (fab_export.capability(params)['predicates'] + list(fab_consistency.PREDICATES)
                  + PREDICATES)
    return predicates


def rotation_record(run_dir, scope, candidate_id, manifest):
    """The declared CPL correction this gate seals, recomputed from retained evidence.

    `{status: not_declared}` without a cpl_rotation check. Otherwise the check
    must have passed with complete coverage on this candidate and board, and
    have corrected exactly the native cpl.csv in `manifest`; raises ValueError
    on any disagreement. The record never upgrades the report's status.
    """
    run_dir = Path(run_dir)
    spec = declared_rotation(scope)
    if spec is None:
        return {'status': 'not_declared'}
    cid = spec['id']
    result = read_json(run_dir / 'checks' / f'{cid}.json')
    if (result.get('kind') != 'cpl_rotation' or result.get('candidate_id') != candidate_id
            or (result.get('execution'), result.get('outcome')) != ('ok', 'pass')):
        raise ValueError(f'cpl_rotation {cid!r} did not pass')
    report_rel, cpl_rel = f'work/{cid}/cpl-rotation.json', f'work/{cid}/cpl/cpl.csv'
    if report_rel not in result.get('evidence', []) or cpl_rel not in result.get('evidence', []):
        raise ValueError(f'cpl_rotation {cid!r} did not seal its report and corrected CPL')
    report_path, cpl_path = run_dir / report_rel, run_dir / cpl_rel
    if (report_path.is_symlink() or cpl_path.is_symlink()
            or digest(report_path) != result.get('details', {}).get('report_sha256')):
        raise ValueError(f'cpl_rotation {cid!r} report differs from its check result')
    report = read_json(report_path)
    if (report.get('execution'), report.get('outcome'), report.get('coverage')) != ('ok', 'pass', 'complete'):
        raise ValueError(f'cpl_rotation {cid!r} report lacks complete passing coverage')
    status = report.get('status')
    if status not in ROTATION_STATUSES or report.get('physical_validation') is not (status == 'ok'):
        raise ValueError(f'cpl_rotation {cid!r} status {status!r} is invalid')
    native_cpl = manifest.get('files', {}).get('cpl.csv')
    if (report.get('candidate_id') != candidate_id or report.get('board_sha256') != manifest.get('board_sha256')
            or not native_cpl or report.get('export', {}).get('cpl_sha256') != native_cpl):
        raise ValueError(f'cpl_rotation {cid!r} did not correct this package\'s native cpl.csv')
    corrected = report.get('cpl') or {}
    if corrected.get('path') != 'cpl/cpl.csv' or digest(cpl_path) != corrected.get('sha256'):
        raise ValueError(f'cpl_rotation {cid!r} corrected CPL differs from its report')
    policy = report.get('policy') or {}
    return {'status': status, 'check_id': cid, 'policy_id': policy.get('policy_id'),
            'policy_sha256': policy.get('sha256'), 'report': report_rel, 'report_sha256': digest(report_path),
            'cpl': cpl_rel, 'cpl_sha256': corrected['sha256'], 'native_cpl_sha256': native_cpl,
            'applied_corrections': len(report.get('applied_corrections', [])),
            'unmatched_footprints': len(report.get('unmatched_footprints', []))}


def artifact_record(run_dir, check):
    """`agent-ee.fab-artifacts/1` recomputed from a retained passing fab_package result.

    Raises ValueError when the result did not pass or a listed file is absent,
    empty, unsealed or differs from its manifest digest. With a declared
    cpl_rotation check the CPL artifact is its corrected cpl.csv.
    """
    run_dir = Path(run_dir)
    cid = check.get('id')
    if (check.get('kind') != 'fab_package' or check.get('execution') != 'ok'
            or check.get('outcome') != 'pass'):
        raise ValueError(f'fab_package {cid!r} did not pass')
    fab = run_dir / 'work' / cid / 'fab'
    manifest_path = fab / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size == 0:
        raise ValueError(f'fab_package {cid!r} manifest is absent or empty')
    manifest = read_json(manifest_path)
    listed = fab_export.verify_manifest(fab, manifest)
    if manifest.get('candidate_id') != check.get('candidate_id'):
        raise ValueError(f'fab_package {cid!r} manifest belongs to another candidate')
    drill = manifest.get('drill') or {}
    drills = sorted(rel for rel in listed if rel.startswith('drill/'))
    wanted = [*REQUIRED_FILES, *drills]
    missing = [rel for rel in (*REQUIRED_FILES, *(f'drill/{drill.get(r)}' for r in ('pth', 'npth')))
               if rel not in listed]
    if missing:
        raise ValueError(f'fab_package {cid!r} package lacks {missing}')
    scope = read_json(run_dir / 'candidate.json')['scope']
    rotation = rotation_record(run_dir, scope, check['candidate_id'], manifest)
    if rotation['status'] != 'not_declared':
        wanted.remove('cpl.csv')
    sealed = {e.get('path') if isinstance(e, dict) else e for e in check.get('evidence', [])}
    files = {}
    for rel in [*wanted, 'manifest.json']:
        path = f'work/{cid}/fab/{rel}'
        if path not in sealed:
            raise ValueError(f'fab_package {cid!r} did not seal {path}')
        files[path] = listed[rel] if rel != 'manifest.json' else digest(manifest_path)
    if rotation['status'] != 'not_declared':
        if rotation['cpl'] not in sealed:
            raise ValueError(f"fab_package {cid!r} did not seal {rotation['cpl']}")
        files[rotation['cpl']] = rotation['cpl_sha256']
    value = {'schema': ARTIFACT_SCHEMA, 'check_id': cid, 'candidate_id': check['candidate_id'],
             'board_path': manifest['board_path'], 'board_sha256': manifest['board_sha256'],
             'files': dict(sorted(files.items())), 'rotation': rotation}
    return {**value, 'artifacts_id': identity(value)}


def check_fab_package(ctx):
    from .checks import CheckError
    try:
        validate_params(ctx.params)
        require_last(ctx.scope['checks'], ctx.spec)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    details = ctx.result['details']
    fab_export.check_fab_export(ctx)
    ctx.result['limitations'] = [text for text in ctx.result['limitations']
                                 if not text.startswith(EXPORT_SCOPE_NOTE)]
    export_summary = ctx.result['summary']
    export_result = copy.deepcopy(ctx.result)

    sub = copy.copy(ctx)
    sub.result = {**{k: copy.deepcopy(v) for k, v in ctx.result.items()},
                  'execution': 'not_run', 'outcome': 'unknown', 'summary': '',
                  'evidence': [], 'details': {}, 'limitations': []}
    try:
        fab_consistency.gate(sub, ctx.cid, ctx.spec, export_result, ctx.work / 'fab')
    finally:
        details['consistency'] = sub.result['details']
        for entry in sub.result['evidence']:
            if entry not in ctx.result['evidence']:
                ctx.result['evidence'].append(entry)
        ctx.result['limitations'] += sub.result['limitations']
    if sub.result['outcome'] == 'defect':
        ctx.result.update(execution='ok', outcome='defect', location=sub.result['location'],
                          summary=f'fab package done gate failed: {sub.result["summary"]}')
        return
    if (sub.result['execution'], sub.result['outcome']) != ('ok', 'pass'):
        raise CheckError('malformed', 'fab consistency gate returned no verdict')
    rotation = declared_rotation(ctx.scope)
    if rotation is not None:
        prior = ctx.run_dir / 'checks' / f"{rotation['id']}.json"
        prior = read_json(prior) if prior.is_file() else {}
        if prior.get('candidate_id') == ctx.manifest['candidate_id'] and prior.get('outcome') == 'defect':
            ctx.result.update(execution='ok', outcome='defect', location=prior.get('location', ''),
                              summary=f"fab package done gate failed: cpl_rotation {rotation['id']!r} "
                                      f"found a defect: {prior.get('summary', '')}")
            return
        if (prior.get('execution'), prior.get('outcome')) != ('ok', 'pass'):
            raise CheckError('unsupported', f"declared cpl_rotation {rotation['id']!r} has no passing result "
                                            f"({prior.get('execution')}/{prior.get('outcome')})")
        for rel in (f"work/{rotation['id']}/cpl-rotation.json", f"work/{rotation['id']}/cpl/cpl.csv"):
            if rel in prior.get('evidence', []) and rel not in ctx.result['evidence']:
                ctx.result['evidence'].append(rel)
    try:
        artifacts = artifact_record(ctx.run_dir, {**ctx.result, 'execution': 'ok', 'outcome': 'pass'})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'fab artifacts do not verify: {exc}') from exc
    details['artifacts'] = artifacts
    details['rotation'] = artifacts['rotation']
    status = artifacts['rotation']['status']
    if status != 'not_declared':
        ctx.result['limitations'].append(
            f"the sealed CPL is cpl_rotation {artifacts['rotation']['check_id']!r}'s corrected cpl.csv; "
            f'rotation status {status}')
    ctx.result.update(execution='ok', outcome='pass',
                      summary=f'fab package done gate passed: {export_summary}; {sub.result["summary"]}; '
                              f'{len(artifacts["files"])} sealed artifacts {artifacts["artifacts_id"][:12]}; '
                              f'cpl rotation {status}')
