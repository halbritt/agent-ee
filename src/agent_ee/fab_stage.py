"""Shared fab-stage export and CPL correction using the ordinary captured checks."""

import argparse
import shutil
import time
from pathlib import Path

from . import cpl_rotation
from .baseline import freeze_evidence, verify_evidence
from .candidate import capture, source_manifest
from .checks import run_checks
from .fab_export import JLC_TEMPLATE_DIR, inventory, verify_manifest
from .fab_package import ROTATION_STATUSES, preview_obligations
from .storage import digest, read_json, write_json

CPL_ROOT = Path('generation/cpl')
FAB_CHECKS = frozenset({'fab_export', 'cpl_rotation', 'fab_package'})
PACKAGE_SCHEMA = 'agent-ee.rung1-fab-manifest/0-experimental'


def campaign_context(root, state, config, scope):
    """Select the declared fab producer and its latest anchored campaign receipt."""
    from .stage_receipts import validate_step
    from .stage_done import _receipts
    if 'fab' not in (scope.get('stages') or {}).get('order', []):
        return None
    steps = [validate_step(s, host=False) for s in config.get('implementation_steps', [])
             if s['stage'] == 'fab']
    if not steps:
        return None
    if len(steps) != 1:
        raise ValueError('fab checks require exactly one declared fab package producer')
    context = {'spec': steps[0], 'receipt': None}
    for number in range(1, len(state['history']) + 1):
        work = Path(root) / 'implementations' / str(number)
        for k, sha, receipt in _receipts(root, state, str(number)):
            if receipt['spec'] == steps[0]:
                link = read_json(work / 'stage-receipts.json')['receipts'][k - 1]
                context['receipt'] = {'path': str(work / link['path']), 'sha256': sha,
                                      'binding': receipt['binding']}
    return context


def before_fab_check(ctx):
    """Unknown until the declared step runs; a failed/stale receipt is never pending."""
    from .checks import CheckError
    from .stage_receipts import after_tree, validate_receipt, ADOPTABLE
    context = getattr(ctx, 'fab_context', None)
    if context is None or ctx.spec['kind'] not in FAB_CHECKS:
        return
    write_json(ctx.work / 'fab-step.json', context)
    ctx.evidence('fab-step.json')
    ctx.result['details']['fab_step'] = context
    link = context['receipt']
    if link is None:
        ctx.result['details']['reason'] = 'fab_step_pending'
        raise CheckError('not_run', 'declared fab step has no sealed output; run the coordinator fab step')
    try:
        path = Path(link['path'])
        if path.is_symlink() or digest(path) != link['sha256']:
            raise ValueError('fab step receipt changed')
        receipt = validate_receipt(path.parent, binding=link['binding'])
        if receipt['spec'] != context['spec'] or receipt['status'] not in ADOPTABLE:
            raise ValueError(f"fab step receipt is not adoptable or differs from the declaration; "
                             f"violations {receipt['violations']}; receipt kept at {path}")
        if after_tree(receipt) != ctx.manifest['files']:
            raise ValueError('fab step output is not applicable to this captured candidate')
        if not receipt['artifacts'] or any(sha is None for sha in receipt['artifacts'].values()):
            raise ValueError('fab step lacks complete sealed artifacts')
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', f'declared fab step refused: {exc}') from exc


def _step_package(ctx):
    """Retained output paths only after receipt validation, with every byte in its seal."""
    from .checks import CheckError
    context = getattr(ctx, 'fab_context', None)
    if context is None:
        return None
    path = Path(context['receipt']['path'])
    receipt = read_json(path)
    output = path.parent / 'run/out/fab'
    manifest = path.parent / 'run/out/fab-manifest.json'
    files = inventory(output)
    expected = {f'out/fab/{rel}': sha for rel, sha in files.items()}
    if not files or any(receipt['artifacts'].get(rel) != sha for rel, sha in expected.items()):
        raise CheckError('malformed', 'declared fab step output is missing or not sealed by its receipt')
    # The shared producer retains native exports separately from the corrected package.
    if not manifest.is_file() or receipt['artifacts'].get('out/fab-manifest.json') != digest(manifest):
        raise CheckError('malformed', 'declared fab step has no sealed package manifest')
    record = read_json(manifest)
    if (not isinstance(record, dict) or record.get('schema') != PACKAGE_SCHEMA
            or record.get('status') not in ROTATION_STATUSES
            or record.get('physical_validation') is not (record['status'] == 'ok')
            or len(record['files']) != len(files)
            or {f['path']: f['sha256'] for f in record['files']} != files
            or record['board']['sha256'] != ctx.manifest['files'][ctx.scope['board']]
            or record['schematic']['sha256'] != ctx.manifest['files'][ctx.scope['schematic']]):
        raise CheckError('malformed', 'declared fab package manifest disagrees with its output or candidate')
    native = path.parent / 'run/out/checks/work/fab/fab'
    native_files = verify_manifest(native, read_json(native / 'manifest.json'))
    for rel, sha in {**native_files, 'manifest.json': digest(native / 'manifest.json')}.items():
        if receipt['artifacts'].get(f'out/checks/work/fab/fab/{rel}') != sha:
            raise CheckError('malformed', 'declared fab step native export is not sealed')
    report = read_json(output / 'cpl-rotation.json')
    if (record['status'] != report['status'] or record['physical_validation'] != report['physical_validation']
            or record['rotation']['report_sha256'] != files['cpl-rotation.json']
            or record['native_export']['sha256'] != digest(native / 'manifest.json')):
        raise CheckError('malformed', 'declared fab package manifest disagrees with its sealed reports')
    return native, output, files


def grade_native(ctx):
    """Compare fresh sandbox output, then grade the receipted producer's native bytes."""
    from .checks import CheckError
    try:
        package = _step_package(ctx)
        if package is None:
            return
        native, output, files = package
        fresh_manifest = read_json(ctx.work / 'fab/manifest.json')
        native_manifest = read_json(native / 'manifest.json')
        actual = verify_manifest(ctx.work / 'fab', fresh_manifest)
        sealed = verify_manifest(native, native_manifest)
        context_fields = {'candidate_id', 'generated_utc'}
        if ({k: v for k, v in fresh_manifest.items() if k not in context_fields}
                != {k: v for k, v in native_manifest.items() if k not in context_fields}):
            raise ValueError('declared fab step manifest differs from the fresh sandbox export')
        if actual != sealed or {k: v for k, v in files.items() if k not in ('cpl.csv', 'cpl-rotation.json')} != {
                k: v for k, v in actual.items() if k != 'cpl.csv'}:
            raise ValueError('declared fab step output differs from the fresh sandbox export')
        if not any(c['kind'] == 'cpl_rotation' for c in ctx.scope['checks']) and files['cpl.csv'] != actual['cpl.csv']:
            raise ValueError('corrected step CPL requires a declared cpl_rotation check')
        for rel in actual:
            source = native / rel if rel == 'cpl.csv' else output / rel
            shutil.copyfile(source, ctx.work / 'fab' / rel)
        verify_manifest(ctx.work / 'fab', fresh_manifest)
        ctx.result['details']['fab_step_files'] = files
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'declared fab step refused: {exc}') from exc


def grade_cpl(ctx):
    """Fresh correction must equal the sealed step CPL, including its preview status."""
    from .checks import CheckError
    try:
        package = _step_package(ctx)
        if package is None:
            return
        _, output, files = package
        report = read_json(ctx.work / 'cpl-rotation.json')
        retained = read_json(output / 'cpl-rotation.json')
        references = {'candidate_id', 'export', 'geometry'}
        if ({k: v for k, v in report.items() if k not in references}
                != {k: v for k, v in retained.items() if k not in references}
                or report['export']['cpl_sha256'] != retained['export']['cpl_sha256']):
            raise ValueError('declared fab step CPL report differs from the fresh correction')
        corrected = ctx.work / 'cpl/cpl.csv'
        if digest(corrected) != files['cpl.csv']:
            raise ValueError('declared fab step CPL differs from the fresh sandbox correction')
        shutil.copyfile(output / 'cpl.csv', corrected)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'declared fab step refused: {exc}') from exc


def pending_checks(result):
    """A coordinator obligation can schedule a turn, while the review stays incomplete."""
    pending = [c['id'] for c in result['checks']
               if c['kind'] in FAB_CHECKS and (c['execution'], c['outcome']) == ('not_run', 'unknown')
               and c.get('details', {}).get('reason') == 'fab_step_pending'
               and c['details'].get('fab_step', {}).get('receipt', 'absent') is None]
    if not pending:
        return []
    from .cli import outcome
    # Evaluate readiness without the absent artifacts, never change their recorded unknown verdicts.
    ready = {**result, 'checks': [c for c in result['checks'] if c['id'] not in pending],
             'expected_check_ids': [c for c in result['expected_check_ids'] if c not in pending]}
    return pending if outcome(ready) == 'criteria_satisfied' else []


def verify_check_context(run, result, expected):
    """Replay the coordinator context from frozen config and the preceding receipt indexes."""
    for check in result['checks']:
        if check['kind'] not in FAB_CHECKS:
            continue
        context = check.get('details', {}).get('fab_step')
        gated = check.get('details', {}).get('reason') in ('stage_blocked', 'stage_not_reached')
        if context != (None if gated else expected):
            raise ValueError('fab check context differs from the declared step and anchored receipt')
        if context is not None:
            rel = f"work/{check['id']}/fab-step.json"
            if rel not in check['evidence'] or read_json(Path(run) / rel) != context:
                raise ValueError('fab check omits its sealed step witness')


def rotation_check(inputs):
    inputs = Path(inputs)
    paths = sorted((inputs / CPL_ROOT / 'supplier').glob('*.provenance.json'))
    return {'id': 'cpl', 'kind': 'cpl_rotation', 'params': {
        'export_check': 'fab', 'geometry_check': 'geometry',
        'policy': (CPL_ROOT / 'jlc-cpl-policy.json').as_posix(),
        'supplier_footprints': [p.relative_to(inputs).as_posix() for p in paths],
        'source_tolerance_nm': '200'}}


def copy_rotation_inputs(source, inputs):
    """Copy an explicit input tree; validate with the existing captured-input readers."""
    inputs = Path(inputs)
    scope = {'inputs': [{'path': str(Path(source).absolute()), 'at': CPL_ROOT.as_posix()}]}
    before, sources, _ = source_manifest(scope, inputs)
    for target, src in sources.items():
        dest = inputs / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        if digest(dest) != before[target]:
            raise ValueError(f'CPL input changed during capture: {src}')
    after, _, _ = source_manifest(scope, inputs)
    if before != after:
        raise ValueError('CPL input source changed during capture')
    params = rotation_check(inputs)['params']
    cpl_rotation.load_policy((inputs / params['policy']).read_bytes())
    if not params['supplier_footprints']:
        raise ValueError('CPL supplier footprint set is missing')
    files = {p.relative_to(inputs).as_posix(): digest(p) for p in (inputs / CPL_ROOT).rglob('*') if p.is_file()}
    cpl_rotation.capability(params, files, inputs)
    return inputs / CPL_ROOT


def step(source_root, board, schematic, bom_variant=None):
    """The same receipted, networkless export step for both generation drivers.

    A `bom_variant` (generator `--bom-variant`) becomes part of the step argv, so
    the receipt's spec digest binds it, and reaches only fab_export's BOM export.
    """
    from .tool_source import snapshot
    source_root = snapshot(source_root)
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'export-fab',
            'argv': ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
                     '-m', 'agent_ee.fab_stage',
                     '--board', board, '--schematic', schematic]
                    + (['--bom-variant', bom_variant] if bom_variant is not None else []),
            'mounts': [{'source': str(Path(source_root).absolute()), 'at': '/tool/agent-ee'},
                       {'source': str(JLC_TEMPLATE_DIR.absolute()), 'at': '/tool/fixtures/fab-export'}],
            'source': '/tool/agent-ee', 'version_argv': ['/usr/bin/kicad-cli', 'version'],
            # Existing receipt contract requires a write; the input board remains identical.
            'writes': {board: f'design/{board}'},
            'artifacts': ['out/fab-manifest.json', 'out/fab', 'out/checks', 'out/scope.json', 'out/requirements.md'],
            'adoptable_exits': [0], 'timeout_s': 900}


def export(inputs, out, board, schematic, bom_variant=None):
    """Retain the native export and promote only a fully checked corrected CPL."""
    inputs, out = Path(inputs), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cpl = rotation_check(inputs)
    params = cpl['params']
    if not (inputs / params['policy']).is_file():
        raise ValueError('CPL rotation policy is not captured')
    if not params['supplier_footprints']:
        raise ValueError('CPL supplier footprint set is missing')
    fab = {'netlist_check': 'netlist', **({'bom_variant': bom_variant} if bom_variant is not None else {})}
    checks = [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'geometry', 'kind': 'geometry'},
              {'id': 'fab', 'kind': 'fab_export', 'params': fab}, cpl]
    requirements = out / 'requirements.md'
    requirements.write_text('R-cpl: Export the captured board and correct its CPL using captured policy and supplier frames.\n')
    scope = {'schema': 'agent-ee.scope/2', 'name': 'Captured fab-stage export',
             'scope': 'Offline fab export with policy and supplier pin-1 CPL checks; physical preview required.',
             'board': board, 'schematic': schematic, 'checks': checks,
             'inputs': [{'path': str(p.absolute()), 'at': p.name} for p in sorted(inputs.iterdir())]
                       + [{'path': str(requirements.absolute()), 'at': 'fab-stage/requirements.md'}],
             'baseline': {'requirements': [{'id': 'R-cpl', 'statement': requirements.read_text().strip(),
                            'source': {'path': 'fab-stage/requirements.md', 'locator': 'R-cpl'},
                            'checks': [c['id'] for c in checks]}],
                          'targets': ['fab-stage/requirements.md'],
                          'reference_architectures': ['fab-stage/requirements.md']}}
    scope_path = out / 'scope.json'
    write_json(scope_path, scope)
    run = out / 'checks'
    run.mkdir()
    capture(scope_path, run)
    results = run_checks(run, time.monotonic() + 840)
    freeze_evidence(run, results)
    failed = [f"{r['id']}: {r['execution']}/{r['outcome']}: {r['summary']}" for r in results
              if (r['execution'], r['outcome']) not in (('ok', 'pass'), ('ok', 'info'))]
    if failed:
        raise ValueError('fab checks refused package: ' + '; '.join(failed))
    return package(run, out / 'fab', out / 'fab-manifest.json')


def package(run, target, manifest_path):
    """Create a derived package; never mutate the sealed native export or its manifest."""
    run, target = Path(run), Path(target)
    candidate = read_json(run / 'candidate.json')
    results = [read_json(run / 'checks' / f"{spec['id']}.json") for spec in candidate['scope']['checks']]
    verify_evidence(run, results, required=True)
    native = run / 'work/fab/fab'
    manifest = read_json(native / 'manifest.json')
    verify_manifest(native, manifest)
    report_path = run / 'work/cpl/cpl-rotation.json'
    report = read_json(report_path)
    if (report['execution'], report['outcome'], report['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('CPL correction lacks complete passing coverage')
    if (report['status'] not in ROTATION_STATUSES
            or report['physical_validation'] is not (report['status'] == 'ok')):
        raise ValueError('CPL physical preview status is invalid')
    if (report['candidate_id'] != manifest['candidate_id']
            or report['export']['manifest_sha256'] != digest(native / 'manifest.json')
            or report['cpl']['path'] != 'cpl/cpl.csv'):
        raise ValueError('CPL correction is not bound to this native export')
    corrected = run / 'work/cpl/cpl/cpl.csv'
    if digest(corrected) != report['cpl']['sha256'] or report['board_sha256'] != manifest['board_sha256']:
        raise ValueError('CPL correction digest or board binding disagrees')
    declared = next(spec for spec in candidate['scope']['checks'] if spec['id'] == 'fab')
    variant = declared.get('params', {}).get('bom_variant')
    if variant != (manifest.get('bom_variant') or {}).get('name'):
        raise ValueError(f'native export did not honour the declared BOM variant {variant!r}')
    bom_designators = sorted(cpl_rotation.read_bom_bindings((native / 'bom.csv').read_text()))
    cpl_designators = [r['designator'] for r in report['designators']]
    if sorted(bom_designators) != sorted(cpl_designators):
        # The CPL is always the default variant; a BOM variant may not change the populated set.
        raise ValueError(f'BOM designators {bom_designators} differ from CPL designators {cpl_designators}'
                         + (f'; BOM variant {variant!r} changes the populated set' if variant else ''))
    obligations = preview_obligations(report['status'], report['policy']['sha256'], report,
                                      corrected.read_text(encoding='utf-8'))
    shutil.copytree(native, target, ignore=shutil.ignore_patterns('manifest.json'))
    shutil.copyfile(corrected, target / 'cpl.csv')
    shutil.copyfile(report_path, target / 'cpl-rotation.json')
    schematic = candidate['scope']['schematic']
    data = {'schema': PACKAGE_SCHEMA, 'qualification': 'experimental-unqualified',
            'status': report['status'], 'physical_validation': report['physical_validation'],
            'board': {'path': manifest['board_path'], 'sha256': manifest['board_sha256']},
            'schematic': {'path': schematic, 'sha256': candidate['files'][schematic]},
            'bom_designators': bom_designators, 'cpl_designators': cpl_designators,
            'native_export': {'manifest': 'work/fab/fab/manifest.json',
                              'candidate_id': manifest['candidate_id'],
                              'sha256': digest(native / 'manifest.json')},
            'rotation': {'report_sha256': digest(report_path), 'policy': report['policy'],
                         'applied_corrections': report['applied_corrections'],
                         'unmatched_footprints': report['unmatched_footprints']},
            'preview_obligations': obligations,
            'files': [{'path': p.relative_to(target).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                      for p in sorted(target.rglob('*')) if p.is_file()],
            'limitations': (['PREVIEW_REQUIRED: inspect every part in the JLCPCB placement preview',
                             'Bottom-side transform remains unvalidated against a real JLCPCB preview']
                            if report['status'] == 'PREVIEW_REQUIRED' else [])}
    if variant is not None:
        data['bom_variant'] = manifest['bom_variant']
    write_json(manifest_path, data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', required=True)
    parser.add_argument('--schematic', required=True)
    parser.add_argument('--bom-variant')
    args = parser.parse_args(argv)
    try:
        export('/candidate', '/work/out', args.board, args.schematic, args.bom_variant)
    except (ValueError, OSError) as exc:
        parser.exit(3, f'fab stage refused: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
