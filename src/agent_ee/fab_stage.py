"""Shared fab-stage export and CPL correction using the ordinary captured checks."""

import argparse
import shutil
import time
from pathlib import Path

from . import cpl_rotation
from .baseline import freeze_evidence, verify_evidence
from .candidate import capture, source_manifest
from .checks import run_checks
from .fab_export import JLC_TEMPLATE_DIR, verify_manifest
from .fab_package import ROTATION_STATUSES
from .storage import digest, read_json, write_json

CPL_ROOT = Path('generation/cpl')


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
    shutil.copytree(native, target, ignore=shutil.ignore_patterns('manifest.json'))
    shutil.copyfile(corrected, target / 'cpl.csv')
    shutil.copyfile(report_path, target / 'cpl-rotation.json')
    schematic = candidate['scope']['schematic']
    data = {'schema': 'agent-ee.rung1-fab-manifest/0-experimental', 'qualification': 'experimental-unqualified',
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
