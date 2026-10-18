"""Generation done gate: one check that exports, gates and seals the fab package (AGENTEE-94).

Check kind `fab_package` (params exactly `{netlist_check}`) must be the last
declared check. It runs the AGENTEE-23 `fab_export` producer with the pinned
`kicad-cli` in the executor sandbox into its own `work/<id>/fab/`, then the
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
"""

import copy

from . import fab_consistency, fab_export
from .storage import digest, identity, read_json

ARTIFACT_SCHEMA = 'agent-ee.fab-artifacts/1'
REQUIRED_FILES = ('gerbers.zip', 'bom.csv', 'cpl.csv')
PREDICATES = ['fab-package:done-gate', 'fab-package:sealed-artifacts']
EXPORT_SCOPE_NOTE = 'pass means the exports exist, parsed, agree on designators and are hashed'


def validate_params(params):
    if not isinstance(params, dict) or set(params) != {'netlist_check'}:
        raise ValueError('fab_package params must be exactly {"netlist_check"}')
    if not isinstance(params['netlist_check'], str) or not params['netlist_check']:
        raise ValueError('netlist_check must name the preceding netlist check')
    return params


def require_last(specs, spec):
    if not specs or (specs[-1] is not spec and specs[-1].get('id') != spec.get('id')):
        raise ValueError('fab_package must be the last declared check')


def capability(params, scope, spec, prerequisite, captured):
    """Static scope/3 contract: exact params, declared last, earlier netlist and geometry."""
    validate_params(params)
    require_last(scope.get('checks', []), spec)
    prerequisite(params['netlist_check'], ('netlist',))
    fab_consistency.earlier_geometry(scope, prerequisite, 'fab_package')
    predicates = (fab_export.capability(params)['predicates'] + list(fab_consistency.PREDICATES)
                  + PREDICATES)
    return predicates


def artifact_record(run_dir, check):
    """`agent-ee.fab-artifacts/1` recomputed from a retained passing fab_package result.

    Raises ValueError when the result did not pass or a listed file is absent,
    empty, unsealed or differs from its manifest digest.
    """
    from pathlib import Path
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
    sealed = {e.get('path') if isinstance(e, dict) else e for e in check.get('evidence', [])}
    files = {}
    for rel in [*wanted, 'manifest.json']:
        path = f'work/{cid}/fab/{rel}'
        if path not in sealed:
            raise ValueError(f'fab_package {cid!r} did not seal {path}')
        files[path] = listed[rel] if rel != 'manifest.json' else digest(manifest_path)
    value = {'schema': ARTIFACT_SCHEMA, 'check_id': cid, 'candidate_id': check['candidate_id'],
             'board_path': manifest['board_path'], 'board_sha256': manifest['board_sha256'],
             'files': dict(sorted(files.items()))}
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
    try:
        artifacts = artifact_record(ctx.run_dir, {**ctx.result, 'execution': 'ok', 'outcome': 'pass'})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'fab artifacts do not verify: {exc}') from exc
    details['artifacts'] = artifacts
    ctx.result.update(execution='ok', outcome='pass',
                      summary=f'fab package done gate passed: {export_summary}; {sub.result["summary"]}; '
                              f'{len(artifacts["files"])} sealed artifacts {artifacts["artifacts_id"][:12]}')
