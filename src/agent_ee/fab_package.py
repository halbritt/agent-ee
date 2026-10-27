"""Generation done gate: one check that exports, gates and seals the fab package (AGENTEE-94).

Check kind `fab_package` (required `netlist_check` plus delegated export/model params) must be the last
declared check except for bound `jlc_dfm` consumers and bound sealed-package `fab_roundtrip`
verifications (`params.package_check`, AGENTEE-524). It runs the AGENTEE-23
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

Preview obligations (AGENTEE-151, owner decision 2026-10-18). A
`PREVIEW_REQUIRED` rotation is done-admissible only as an operator obligation:
the rotation record carries `preview_obligations`, every designator of the
sealed corrected CPL with its side and the policy sha256, recomputed from the
sealed report and CPL (`preview_obligations`). The operator confirms each in the
JLCPCB placement preview before ordering. The status itself is never upgraded.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import fab_consistency, fab_export
from .fab_export import read_native_cpl
from .storage import digest, identity, read_json

ARTIFACT_SCHEMA = 'agent-ee.fab-artifacts/1'
SCHEMA = 'agent-ee.assembly-profile/1'
ASSESSMENT_SCHEMA = 'agent-ee.assembly-orientation/1'
SUPPORTED_PROFILES = frozenset({'jlcpcb-cpl-software/1'})
PROFILE_KEYS = frozenset({'schema', 'profile_id', 'supplier', 'service', 'applicability', 'rotation_check',
                          'policy_sha256', 'position_convention', 'sources', 'rationale'})
MOUNTS = ('smd', 'through_hole')
POSITION_CONVENTION = {'x': 'board_x_mm', 'y': 'negated_board_y_mm', 'anchor': 'pad_box_centroid',
                       'angle': 'native_plus_policy_delta_bottom_mirrored'}
PAD_MOUNT = {'SMD': 'smd', 'PTH': 'through_hole', 'CONN': 'smd'}
ANGLE_BASES = ('native_supported', 'corrected', 'outside_profile', 'unassessed')
SUPPLIER_CONFIRMATION = 'pending_preview'
REQUIRED_FILES = ('gerbers.zip', 'bom.csv', 'cpl.csv')
PREDICATES = ['fab-package:done-gate', 'fab-package:sealed-artifacts']
EXPORT_SCOPE_NOTE = 'pass means the exports exist, parsed, agree on designators and are hashed'
# Rotation statuses a done predicate accepts (decision B, AGENTEE-126). `not_declared` keeps the
# native-CPL behaviour of a scope without a cpl_rotation check; `ok` is a declared correction with
# nothing physically pending. `PREVIEW_REQUIRED` (every cpl_rotation result today) is admitted by
# the owner decision of 2026-10-18 (AGENTEE-151) only as an operator obligation: a status in
# OBLIGATION_STATUSES is done only with its recorded, policy-bound `preview_obligations`, so the
# unvalidated bottom-side transform is confirmed in the JLCPCB placement preview before ordering.
DONE_ROTATION_STATUSES = frozenset({'not_declared', 'ok', 'PREVIEW_REQUIRED'})
OBLIGATION_STATUSES = frozenset({'PREVIEW_REQUIRED'})
ROTATION_STATUSES = frozenset({'ok', 'PREVIEW_REQUIRED'})
PREVIEW_ACTION = 'confirm each designator in the JLCPCB placement preview before ordering'
OBLIGATION_KEYS = frozenset({'status', 'policy_sha256', 'action', 'designators'})
SIDES = ('Top', 'Bottom')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


def preview_obligations(status, policy_sha256, report, cpl_text):
    """The operator obligations a rotation status carries to done, or None when it carries none.

    For a status in OBLIGATION_STATUSES: every designator of the corrected CPL text with its
    side, naturally sorted, bound to the policy sha256. The report's designator records must
    list exactly the same designators on the same sides; raises ValueError otherwise.
    """
    if status not in OBLIGATION_STATUSES:
        return None
    if not isinstance(policy_sha256, str) or not SHA256_RE.fullmatch(policy_sha256):
        raise ValueError(f'preview obligations need the policy sha256, not {policy_sha256!r}')
    sides = {}
    for row in read_native_cpl(cpl_text):
        if row[0] in sides:
            raise ValueError(f'corrected CPL lists {row[0]} twice')
        sides[row[0]] = row[3]
    records = report.get('designators')
    if (not isinstance(records, list) or not all(isinstance(r, dict) for r in records)
            or {r.get('designator'): r.get('layer') for r in records} != sides or len(records) != len(sides)):
        raise ValueError('cpl_rotation report designators differ from the corrected CPL rows')
    return {'status': status, 'policy_sha256': policy_sha256, 'action': PREVIEW_ACTION,
            'designators': [{'designator': ref, 'side': sides[ref]}
                            for ref in sorted(sides, key=fab_export.natural_key)]}


def obligation_problem(status, obligations, policy_sha256):
    """Why `obligations` is not the well-formed obligation record `status` requires, or None."""
    if status not in OBLIGATION_STATUSES:
        return None if obligations is None else f'status {status} carries no preview obligations'
    if not isinstance(obligations, dict) or set(obligations) != OBLIGATION_KEYS:
        return 'no preview obligations recorded'
    if (obligations['status'], obligations['action']) != (status, PREVIEW_ACTION):
        return 'preview obligations name another status or action'
    if not isinstance(policy_sha256, str) or obligations['policy_sha256'] != policy_sha256:
        return 'preview obligations are not bound to the sealed rotation policy'
    listed = obligations['designators']
    if not isinstance(listed, list) or not listed:
        return 'preview obligations list no designator'
    if not all(isinstance(e, dict) and set(e) == {'designator', 'side'} and isinstance(e['designator'], str)
               and fab_export.REF_RE.fullmatch(e['designator']) and e['side'] in SIDES for e in listed):
        return 'a preview obligation is not a designator with its side'
    refs = [e['designator'] for e in listed]
    if refs != sorted(set(refs), key=fab_export.natural_key):
        return 'preview obligations are not unique and sorted'
    return None


def rotation_blocker(status, obligations=None, policy_sha256=None):
    """None when a rotation status may mark a generation done, else the named reason (fail closed).

    A status in OBLIGATION_STATUSES additionally needs its well-formed `preview_obligations`
    bound to `policy_sha256`, the sealed rotation policy digest.
    """
    if status not in DONE_ROTATION_STATUSES:
        return f'fab: cpl rotation status {status!r} is not a done status'
    problem = obligation_problem(status, obligations, policy_sha256)
    return f'fab: cpl rotation {status}: {problem}' if problem else None


# Done-gate-only params the delegated export never sees.
PACKAGE_PARAMS = ('model_libraries', 'assembly_profile')


def validate_params(params):
    fab_export.validate_params(export_params(params))
    fab_consistency.model_libraries(params)
    profile = params.get('assembly_profile') if isinstance(params, dict) else None
    if profile is not None and (not isinstance(profile, str) or not profile or profile.startswith('/')
                                or '..' in profile.split('/')):
        raise ValueError('assembly_profile must be a captured candidate-relative path')
    return params


def export_params(params):
    """Keep the done gate's model receipts and assembly profile out of the export contract."""
    if not isinstance(params, dict):
        return params  # The export validator reports malformed parameter objects.
    return {key: value for key, value in params.items() if key not in PACKAGE_PARAMS}


# Checks that may follow the done gate, each bound to it by this param (AGENTEE-524 adds the
# sealed-package roundtrip). The package never reads them, so there is no cycle.
BOUND_FOLLOWERS = {'jlc_dfm': 'fab_check', 'fab_roundtrip': 'package_check'}


def require_last(specs, spec):
    position = next((i for i, s in enumerate(specs) if s.get('id') == spec.get('id')), None)
    if position is None or any(s.get('kind') not in BOUND_FOLLOWERS or
                               (s.get('params') or {}).get(BOUND_FOLLOWERS[s['kind']]) != spec.get('id')
                               for s in specs[position + 1:]):
        raise ValueError('fab_package done check last: only bound jlc_dfm and fab_roundtrip checks may follow')


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
    if params.get('assembly_profile') is not None:
        # An assembly profile assesses the declared correction; it cannot stand in for one (AGENTEE-524).
        if rotation is None:
            raise ValueError('assembly_profile requires a declared cpl_rotation check')
        captured(params['assembly_profile'], immutable=True)
    for receipt in fab_consistency.model_libraries(params).values():
        captured(receipt, immutable=True)
    predicates = (fab_export.capability(export_params(params))['predicates'] + list(fab_consistency.PREDICATES)
                  + PREDICATES)
    return predicates


def rotation_record(run_dir, scope, candidate_id, manifest):
    """The declared CPL correction this gate seals, recomputed from retained evidence.

    `{status: not_declared}` without a cpl_rotation check. Otherwise the check
    must have passed with complete coverage on this candidate and board, and
    have corrected exactly the native cpl.csv in `manifest`; raises ValueError
    on any disagreement. The record never upgrades the report's status; a status in
    OBLIGATION_STATUSES carries its recomputed `preview_obligations`.
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
    obligations = preview_obligations(status, policy.get('sha256'), report, cpl_path.read_text(encoding='utf-8'))
    return {'status': status, 'check_id': cid, 'policy_id': policy.get('policy_id'),
            'policy_sha256': policy.get('sha256'), 'report': report_rel, 'report_sha256': digest(report_path),
            'cpl': cpl_rel, 'cpl_sha256': corrected['sha256'], 'native_cpl_sha256': native_cpl,
            'applied_corrections': len(report.get('applied_corrections', [])),
            'unmatched_footprints': len(report.get('unmatched_footprints', [])),
            'preview_obligations': obligations}


def artifact_record(run_dir, check):
    """`agent-ee.fab-artifacts/1` recomputed from a retained passing fab_package result.

    Raises ValueError when the result did not pass or a listed file is absent,
    empty, unsealed or differs from its manifest digest, and while the captured
    baseline envelope qualification is open (require_final). With a declared
    cpl_rotation check the CPL artifact is its corrected cpl.csv.
    """
    run_dir = Path(run_dir)
    candidate_path = run_dir / 'candidate.json'
    if candidate_path.is_file():
        from .placement_defects import release_defects
        if release_defects(run_dir, check, read_json(candidate_path)):
            raise ValueError('fab release refused: carried open placement silkscreen defects')
    cid = check.get('id')
    if (check.get('kind') != 'fab_package' or check.get('execution') != 'ok'
            or check.get('outcome') != 'pass'):
        raise ValueError(f'fab_package {cid!r} did not pass')
    fab = run_dir / 'work' / cid / 'fab'
    manifest_path = fab / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size == 0:
        raise ValueError(f'fab_package {cid!r} manifest is absent or empty')
    verified = fab_export.load_verified_manifest(fab)
    manifest, listed = verified['manifest'], verified['files']
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
    from .board_envelope import require_final
    # Last, bound to the candidate the done check ran for: a re-identified run is refused.
    require_final(run_dir, check['candidate_id'])
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
    saved_params = ctx.params
    ctx.params = export_params(ctx.params)
    try:
        fab_export.check_fab_export(ctx)
    finally:
        ctx.params = saved_params
    ctx.result['limitations'] = [text for text in ctx.result['limitations']
                                 if not text.startswith(EXPORT_SCOPE_NOTE)]
    export_summary = ctx.result['summary']
    export_result = {'evidence': list(ctx.result['evidence'])}

    sub_result = {'id': ctx.cid, 'kind': ctx.spec.get('kind'),
                  'candidate_id': ctx.manifest['candidate_id'],
                  'execution': 'not_run', 'outcome': 'unknown', 'location': '', 'summary': '',
                  'evidence': [], 'details': {}, 'limitations': []}
    try:
        fab_consistency.gate(ctx, ctx.cid, ctx.spec, export_result, ctx.work / 'fab', result=sub_result)
    finally:
        details['consistency'] = sub_result['details']
        for entry in sub_result['evidence']:
            if entry not in ctx.result['evidence']:
                ctx.result['evidence'].append(entry)
        ctx.result['limitations'] += sub_result['limitations']
    if sub_result['outcome'] == 'defect':
        ctx.result.update(execution='ok', outcome='defect', location=sub_result['location'],
                          summary=f'fab package done gate failed: {sub_result["summary"]}')
        return
    if (sub_result['execution'], sub_result['outcome']) != ('ok', 'pass'):
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
    from .board_envelope import require_final
    try:
        # The exports above stay as diagnostics; the done gate and sealed release wait for qualification.
        require_final(ctx.run_dir, ctx.manifest['candidate_id'])
    except ValueError as exc:
        raise CheckError('unsupported', f'fab package done gate held: {exc}') from exc
    try:
        from .placement_defects import prepare_release
        prepare_release(ctx)
        artifacts = artifact_record(ctx.run_dir, {**ctx.result, 'execution': 'ok', 'outcome': 'pass'})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'fab artifacts do not verify: {exc}') from exc
    details['artifacts'] = artifacts
    details['rotation'] = artifacts['rotation']
    status = artifacts['rotation']['status']
    obligations = artifacts['rotation'].get('preview_obligations')
    details['assembly_orientation'] = assess_orientation(ctx, artifacts['rotation'])
    if status != 'not_declared':
        ctx.result['limitations'].append(
            f"the sealed CPL is cpl_rotation {artifacts['rotation']['check_id']!r}'s corrected cpl.csv; "
            f'rotation status {status}')
    if obligations:
        ctx.result['limitations'].append(
            f"operator obligation before ordering: {PREVIEW_ACTION}: "
            + ', '.join(f"{e['designator']} ({e['side']})" for e in obligations['designators']))
    assessment = details['assembly_orientation']
    if assessment['profile_id'] is not None:
        ctx.result['limitations'].append(
            f"assembly orientation is a software assessment under {assessment['profile_id']} "
            f"({assessment['software_assessment']}); supplier confirmation {assessment['supplier_confirmation']}"
            + (': unassessed rows ' + ', '.join(r['designator'] for r in assessment['unassessed_rows'])
               if assessment['unassessed_rows'] else ''))
    ctx.result.update(execution='ok', outcome='pass',
                      summary=f'fab package done gate passed: {export_summary}; {sub_result["summary"]}; '
                              f'{len(artifacts["files"])} sealed artifacts {artifacts["artifacts_id"][:12]}; '
                              f'cpl rotation {status}'
                              + (f"; {len(obligations['designators'])} preview obligations" if obligations else '')
                              + f"; assembly orientation {assessment['software_assessment']}")


def validate_profile(document):
    """The profile document, or ValueError naming the first defect (fail closed)."""
    if not isinstance(document, dict) or set(document) != PROFILE_KEYS:
        raise ValueError('assembly profile fields must be exactly ' + ', '.join(sorted(PROFILE_KEYS)))
    if document['schema'] != SCHEMA:
        raise ValueError(f'assembly profile schema must be {SCHEMA}')
    if document['profile_id'] not in SUPPORTED_PROFILES:
        raise ValueError(f'unsupported assembly profile {document["profile_id"]!r}')
    for key in ('supplier', 'service', 'rotation_check', 'rationale'):
        if not isinstance(document[key], str) or not document[key].strip():
            raise ValueError(f'assembly profile {key} must be a nonempty string')
    sha = document['policy_sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('assembly profile policy_sha256 must be a lowercase sha256')
    applicability = document['applicability']
    if not isinstance(applicability, dict) or set(applicability) != {'sides', 'mount'}:
        raise ValueError('assembly profile applicability must be exactly {sides, mount}')
    for key, allowed in (('sides', SIDES), ('mount', MOUNTS)):
        values = applicability[key]
        if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                or any(v not in allowed for v in values)):
            raise ValueError(f'assembly profile applicability.{key} must be a nonempty subset of {list(allowed)}')
    if document['position_convention'] != POSITION_CONVENTION:
        raise ValueError(f'assembly profile position_convention must be exactly {POSITION_CONVENTION}')
    sources = document['sources']
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError('assembly profile sources must be a nonempty list of citations')
    return document


def mount_types(pads):
    """{reference: mount type} from sealed pcbnew pad plating; ValueError on an unknown attribute."""
    mounts = {}
    for pad in pads:
        if not isinstance(pad, dict) or not isinstance(pad.get('ref'), str):
            raise ValueError('sealed geometry pad lacks its footprint reference')
        attribute = pad.get('attribute')
        if attribute == 'NPTH':
            continue
        if attribute not in PAD_MOUNT:
            raise ValueError(f'sealed geometry pad {pad["ref"]}/{pad.get("pad")} has unknown attribute {attribute!r}')
        mount = PAD_MOUNT[attribute]
        if mount == 'through_hole' or pad['ref'] not in mounts:
            mounts[pad['ref']] = mount
    return mounts


def _decimal(text, label):
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{label} {text!r} is not a decimal') from exc


def unassessed_record(rotation, native_cpl_text, reason):
    """The honest record of a package assessed under no profile: every row unassessed."""
    rows = [{'designator': ref, 'side': side, 'applicability': 'unassessed',
             'angle': {'native_degrees': angle, 'exported_degrees': None, 'delta_degrees': None,
                       'basis': 'unassessed', 'evidence': None},
             'anchor': {'native_mm': [x, y], 'exported_mm': None, 'changed': None, 'basis': 'footprint_anchor'}}
            for ref, x, y, side, angle in read_native_cpl(native_cpl_text)]
    return {'schema': ASSESSMENT_SCHEMA, 'profile_id': None, 'profile_sha256': None,
            'rotation_status': rotation.get('status'), 'reason': reason, 'rows': rows,
            'software_assessment': 'unassessed',
            'unassessed_rows': [{'designator': r['designator'], 'reason': reason} for r in rows],
            'supplier_confirmation': SUPPLIER_CONFIRMATION if rotation.get('status') == 'PREVIEW_REQUIRED'
            else 'unassessed'}


def assess_orientation(ctx, rotation):
    """`agent-ee.assembly-orientation/1` for this package from its sealed facts (AGENTEE-524 part B).

    Without an `assembly_profile` param every row is `unassessed`. With one, the captured
    profile must validate and bind to the sealed rotation evidence; otherwise the done gate
    is `unsupported`. The artifact record and `artifacts_id` are untouched either way.
    """
    from .catalog import strict_json
    from .checks import CheckError
    native_cpl = (ctx.work / 'fab' / 'cpl.csv').read_text(encoding='utf-8')
    rel = ctx.params.get('assembly_profile')
    if rel is None:
        return unassessed_record(rotation, native_cpl, 'no assembly profile declared')
    path = ctx.inputs / rel
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'captured assembly profile {rel} is absent')
        raw = path.read_bytes()
        profile = validate_profile(strict_json(raw.decode('utf-8')))
        report = read_json(ctx.run_dir / rotation['report'])
        ids, _ = fab_consistency.geometry_footprints(ctx)
        pads = read_json(ctx.run_dir / 'work' / ids[0] / 'geometry.json').get('pads')
        if not isinstance(pads, list):
            raise ValueError('sealed geometry lacks pad records; rerun the geometry probe')
        return orientation_assessment(profile, digest(path), rotation, report, native_cpl, pads)
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        raise CheckError('unsupported', f'assembly profile {rel}: {exc}') from exc


def orientation_assessment(profile, profile_sha256, rotation, report, native_cpl_text, pads):
    """`agent-ee.assembly-orientation/1` for a sealed package under `profile`.

    `rotation` is the package's recomputed `rotation_record`; `report` the sealed passing complete
    `cpl_rotation` report it names; `native_cpl_text` the package's native export CPL; `pads` the
    sealed pcbnew pads. Raises ValueError when the profile does not bind to the sealed rotation
    evidence or a row lacks the facts it needs; the caller reports that as unsupported.
    """
    validate_profile(profile)
    if rotation.get('status') == 'not_declared' or rotation.get('check_id') != profile['rotation_check']:
        raise ValueError(f'assembly profile names cpl_rotation {profile["rotation_check"]!r}, '
                         f'but the package sealed {rotation.get("check_id")!r}')
    if rotation.get('policy_sha256') != profile['policy_sha256']:
        raise ValueError('assembly profile policy_sha256 differs from the sealed rotation policy')
    if (report.get('execution'), report.get('outcome'), report.get('coverage')) != ('ok', 'pass', 'complete'):
        raise ValueError('assembly assessment needs a passing complete cpl_rotation report')
    records = {}
    for record in report.get('designators') or []:
        if not isinstance(record, dict) or record.get('designator') in records:
            raise ValueError('cpl_rotation report designator records are not unique')
        records[record.get('designator')] = record
    mounts = mount_types(pads)
    sides, mount_scope = profile['applicability']['sides'], profile['applicability']['mount']
    rows, unassessed = [], []
    for ref, x, y, side, angle in read_native_cpl(native_cpl_text):
        record = records.get(ref)
        if record is None:
            raise ValueError(f'cpl_rotation report lacks CPL row {ref}')
        if ref not in mounts:
            raise ValueError(f'sealed geometry has no plated pad for {ref}; mount type unknown')
        exported = record.get('cpl')
        if (not isinstance(exported, list) or len(exported) != 5 or exported[0] != ref
                or exported[3] != side or record.get('layer') != side):
            raise ValueError(f'cpl_rotation report row for {ref} does not match the native CPL row')
        if (record.get('execution'), record.get('outcome')) != ('ok', 'pass'):
            raise ValueError(f'cpl_rotation report row for {ref} did not pass')
        policy = record.get('policy') or None
        delta = _decimal(policy['rotation_degrees'] if policy else '0', f'{ref} rotation delta')
        pin1 = record.get('pin1') or {}
        if pin1.get('offset_degrees') is None or _decimal(pin1['offset_degrees'], f'{ref} pin-1 offset') != delta % 360:
            raise ValueError(f'{ref}: sealed pin-1 offset {pin1.get("offset_degrees")!r} does not support delta {delta}')
        mount = mounts[ref]
        in_profile = side in sides and mount in mount_scope
        if not in_profile:
            basis = 'outside_profile'
            unassessed.append({'designator': ref, 'reason': f'{side}/{mount} is outside the profile applicability'})
        else:
            basis = 'corrected' if delta % 360 else 'native_supported'
        native_xy = [_decimal(x, f'{ref} x'), _decimal(y, f'{ref} y')]
        exported_xy = [_decimal(exported[1], f'{ref} x'), _decimal(exported[2], f'{ref} y')]
        rows.append({
            'designator': ref, 'side': side, 'mount': mount,
            'applicability': 'in_profile' if in_profile else 'outside_profile',
            'angle': {'native_degrees': angle, 'exported_degrees': exported[4], 'delta_degrees': str(delta),
                      'basis': basis,
                      'evidence': {'board_pin1': pin1.get('board'), 'supplier_pin1': pin1.get('supplier'),
                                   'offset_degrees': pin1.get('offset_degrees'),
                                   'policy_id': report.get('policy', {}).get('policy_id') if policy else None,
                                   'rule_id': policy.get('rule_id') if policy else None,
                                   'polarity': record.get('polarity'), 'lcsc': record.get('lcsc'),
                                   'footprint': record.get('footprint')}},
            'anchor': {'native_mm': [x, y], 'exported_mm': [exported[1], exported[2]],
                       'changed': native_xy != exported_xy, 'basis': 'pad_box_centroid',
                       'native_basis': 'footprint_anchor'}})
    obligations = rotation.get('preview_obligations') or {}
    return {'schema': ASSESSMENT_SCHEMA, 'profile_id': profile['profile_id'], 'profile_sha256': profile_sha256,
            'supplier': profile['supplier'], 'service': profile['service'],
            'applicability': profile['applicability'], 'position_convention': POSITION_CONVENTION,
            'rotation_status': rotation['status'], 'rotation_check': rotation['check_id'],
            'policy_id': rotation.get('policy_id'), 'policy_sha256': rotation['policy_sha256'],
            'rows': rows, 'software_assessment': 'complete' if not unassessed else 'incomplete',
            'unassessed_rows': unassessed, 'supplier_confirmation': SUPPLIER_CONFIRMATION,
            'preview_obligation_count': len(obligations.get('designators') or [])}


def describe_fab_package(ctx):
    from pathlib import PurePosixPath
    ctx.result['predicates'] = capability(ctx.params, ctx.scope, ctx.spec, ctx.prerequisite, ctx.captured)
    from .probe_features import immutable_inputs
    for path in immutable_inputs(ctx.params, ctx.files, ctx.inputs, ctx.scope):
        ctx.captured(path, immutable=True)
    if 'probe_manifest' in ctx.params:
        from .probe_features import membership_check
        ctx.prerequisite(membership_check(ctx.params, ctx.scope), ('parts_subset',))
    from .copper_features import fab_parts_manifests
    for record in fab_parts_manifests(ctx.params, ctx.scope, ctx.files, ctx.inputs):
        ctx.captured(record['path'], immutable=True)
        ctx.prerequisite(record['check_id'], ('parts_subset',))
    ctx.captured(ctx.scope.get('board'))
    ctx.captured(ctx.scope.get('schematic'))
    project = str(PurePosixPath(ctx.scope['board']).with_suffix('.kicad_pro'))
    if project in ctx.files:
        ctx.captured(project, immutable=True)


if __name__ != '__main__':
    _register_check('fab_package', describe_fab_package, check_fab_package,
        early_prerequisites=(_requires.placement_clear,), prerequisites=(_requires.stage_progress, _requires.fab_output),
        context_keys=('fab_context',))
