"""Rung 1 as a staged repair campaign that starts from an EMPTY candidate (decision 1).

Run ``python3 -m agent_ee.rung1_campaign SPEC.md --out RUNDIR [--profile P] [--dry-run]``
(see docs/rung1-campaign.md). The driver prepares immutable generation inputs with
``agent_ee.generate``'s stage-1 and stage-2 functions, writes an empty native
project with ``agent_ee.scaffold``'s builder in a sandboxed pre-step, compiles a staged scope/2 and an ``agent-ee.campaign/1`` config whose
``implementation_steps`` are the schematic emitter, the board builder, kicad-route
and a fab export. The existing campaign is the engine: checks, independent
reviewers, the adjudicator and convergence decide; step receipts are provenance.

Every record this module writes is ``qualification: experimental-unqualified``.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from . import executor
from . import generate as g
from .storage import canonical, digest, identity, read_json, write_json

QUALIFICATION = 'experimental-unqualified'
RECORD_SCHEMA = 'agent-ee.rung1-campaign-record/0-experimental'
SCAFFOLD_SCHEMA = 'agent-ee.rung1-scaffold/0-experimental'
NAME = g.PROJECT_NAME
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
STEPS_DIR = REPO_ROOT / 'fixtures' / 'rung1' / 'staged' / 'steps'
FIXTURE_PROJECT = g.FIXTURE_PROJECT
DEFAULT_KICAD_ROUTE = Path.home() / 'git' / 'kicad-route'
STAGE_ORDER = ['schematic', 'placement', 'routing', 'verification']
CHECK_STAGES = {'erc': 'schematic', 'netlist': 'schematic', 'drc': 'placement',
                'geometry': 'routing', 'rc-step': 'verification'}
# The generation done gate (decision 1, AGENTEE-94): declared last, mapped to an acceptance requirement.
DONE_CHECK = {'id': 'fab', 'kind': 'fab_package', 'params': {'netlist_check': 'netlist'}, 'stage': 'verification'}
FAB_REQUIREMENT = {'id': 'R-fab', 'statement': 'A JLCPCB fab package is exported from the captured design, '
                   'passes the fab consistency gate and is sealed',
                   'source': {'path': 'requirements/fab-package.md', 'locator': 'R-fab'}, 'checks': ['fab']}
FAB_PACKAGE_TEXT = ('# Fab package requirement\n\nR-fab: The accepted candidate must yield a JLCPCB fab package '
                    '(Gerber zip, PTH/NPTH drill files, BOM and CPL with a hash manifest) exported by the pinned '
                    'kicad-cli from the captured board and schematic, with no fab consistency defect or coverage gap. '
                    'This establishes package existence and internal consistency, not DFM, CPL rotation policy, '
                    'stock or orderability.\n')
# Declared only when --cpl-inputs captured a rotation policy and supplier set (AGENTEE-126).
CPL_REQUIREMENT = {'id': 'R-cpl', 'statement': 'Export the board and correct its CPL from captured policy and '
                   'supplier frames', 'source': {'path': 'requirements/fab.md', 'locator': 'R-cpl'},
                   'checks': ['fab-export', 'cpl']}
CPL_REQUIREMENT_TEXT = ('R-cpl: Export the captured board with policy- and supplier-checked CPL rotations. '
                        'Physical preview remains required.\n')
# kicad-route: 0 complete and valid; 2 valid but incomplete; 3 fails validation (never adoptable).
NEVER_ADOPTABLE = {'routing': {1, 2, 3, 4}}
MOUNT_STEPS, MOUNT_SOURCE, MOUNT_ROUTE = '/tool/steps', '/tool/agent-ee', '/tool/kicad-route'
MOUNT_TEMPLATE = '/tool/template'
SCAFFOLD_DIRS = ('project', 'generation', 'requirements')


class PrepareFailure(Exception):
    """Generation inputs or the scaffold could not be established; nothing runs after it."""


# ------------------------------------------------------------------ preparation (immutable inputs)

def prepare(spec, rundir, catalog_checkout=None, parts_manifest=None, cpl_inputs=None):
    """Stage 1 and 2 of agent_ee.generate, then intent, resolved record, envelope and request."""
    from .connectivity_ir import canonical_resolved, resolve_intent, validate_resolved
    prep = Path(rundir) / 'prep'
    state, options = {}, {'spec': str(Path(spec).absolute()), 'catalog_checkout': catalog_checkout,
                         'parts_manifest': parts_manifest, 'cpl_inputs': cpl_inputs}
    receipts = []
    for number, name, function in ((1, 'requirements', g.stage_requirements), (2, 'parts', g.stage_parts)):
        stage = g.Stage(prep, number, name)
        stage.start()
        try:
            function(stage, state, options)
        except Exception as exc:  # noqa: BLE001 - preserved verbatim; nothing later runs
            stage.finish('failed', f'{type(exc).__name__}: {exc}')
            raise PrepareFailure(f'prep {number}-{name} failed: {type(exc).__name__}: {exc}') from exc
        stage.finish('ok')
        path = stage.dir / 'receipt.json'
        receipts.append({'stage': f'{number}-{name}', 'path': rel(path, rundir), 'sha256': digest(path)})
    params = state['params']
    parts = read_json(state['parts'])
    intent = g.connectivity_intent(params, parts['parts'], [])
    libraries, captured, symbol_captures = g.capture_intent_libraries(intent)
    intent['libraries'] = libraries
    resolved = resolve_intent(intent, captured)
    try:
        validate_resolved(resolved)
    except ValueError as exc:
        raise PrepareFailure('connectivity intent did not resolve: ' + str(exc)) from exc
    request = {'policy': g.packing_policy(params),
               'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True}}
    return {'params': params, 'parts_path': Path(state['parts']), 'parts': parts,
            'requirements': prep / 'requirements', 'intent': intent,
            'resolved_bytes': canonical_resolved(resolved) + b'\n', 'resolved': resolved,
            'envelope': g.envelope(params), 'request': request, 'prep_receipts': receipts,
            'catalog': parts['catalog'], 'cpl_inputs': state.get('cpl_inputs'),
            'symbol_libraries': captured, 'symbol_captures': symbol_captures}


def rel(path, root):
    try:
        return str(Path(path).absolute().relative_to(Path(root).absolute()))
    except ValueError:
        return str(path)


# ------------------------------------------------------------------ empty native scaffold

def root_sheet_uuid(resolved):
    """The emitter's deterministic root-sheet UUID, so project and schematic agree before and after."""
    from .schematic_emit import UUID_NAMESPACE as EMIT_NAMESPACE
    return str(uuid.uuid5(EMIT_NAMESPACE, resolved['canonical_sha256'] + '\x00' + 'sheet'))


def layout_root_uuid(layout):
    """schematic_emitter's root-sheet UUID for an agent-ee.schematic-layout/1 (rung1_qualified)."""
    from .schematic_emitter import _uuid
    return _uuid(uuid.UUID(layout['namespace']), 'sheet', '/')


def envelope_record(params):
    """agent-ee.envelope/1 (AGENTEE-85): outline, stackup and the fixed header on the left edge."""
    board, header = params['board'], params['header']
    x0, y0 = 100, 100
    x1, y1 = x0 + board['width_mm'], y0 + board['height_mm']
    pitch = 2.54
    # Pin 1 is the footprint origin; the column of pins is centred vertically, one pitch inside.
    pin1_y = round(y0 + board['height_mm'] / 2 - pitch * (header['pins'] - 1) / 2, 4)
    return {
        'schema': 'agent-ee.envelope/1', 'id': 'rung1-rc',
        'outline': {'edges': [
            {'name': 'top', 'start': [x0, y0], 'end': [x1, y0]},
            {'name': 'right', 'start': [x1, y0], 'end': [x1, y1]},
            {'name': 'bottom', 'start': [x1, y1], 'end': [x0, y1]},
            {'name': 'left', 'start': [x0, y1], 'end': [x0, y0]}]},
        'layer_count': board['copper_layers'],
        'stackup': {'thickness_mm': 1.6, 'layers': [
            {'name': 'F.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1},
            {'name': 'core', 'kind': 'dielectric', 'thickness_mm': 1.53},
            {'name': 'B.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1}]},
        'fab_profile': None, 'mounting_holes': [], 'keepouts': [],
        'fixed_footprints': [{'reference': header['ref'], 'side': 'front', 'edge': 'left',
                              'position_mm': round(y1 - pin1_y, 4), 'inset_mm': pitch, 'rotation_deg': 0,
                              'insertion': 'from_top', 'tolerance_mm': 0.5}]}


def scaffold_files(envelope_raw, root, template_dir):
    """The empty native project and builder template, by scaffold path.

    The schematic, board and project come from agent_ee.scaffold.native_files, main's one
    empty-scaffold builder; this adds only the stock library tables and generation/template/.
    """
    from .board_envelope import validate
    from .scaffold import native_files
    template_dir = Path(template_dir)
    try:
        record = validate(envelope_raw)
        project = json.loads((template_dir / 'rc_filter.kicad_pro').read_bytes())
        seed = identity({'envelope': g.sha256_bytes(envelope_raw), 'root': root})
        native = native_files(record, f'project/{NAME}.kicad_sch', project, root, seed)
    except ValueError as exc:
        raise PrepareFailure(f'empty scaffold refused: {exc}') from exc
    files = dict(native)
    # Immutable template for board_build (sibling .kicad_pro required); the writer cannot alter it.
    for ext in ('kicad_pcb', 'kicad_pro'):
        files[f'generation/template/{NAME}.{ext}'] = native[f'project/{NAME}.{ext}']
    for table in ('sym-lib-table', 'fp-lib-table'):
        data = (template_dir / table).read_bytes()
        if b'${KIPRJMOD}' in data:
            raise PrepareFailure(f'{table} must use stock references only')
        files[f'project/{table}'] = data
    return files


def scaffold_main(argv=None):
    """Sandboxed `write-scaffold` step: write scaffold_files beneath --out; nothing else."""
    parser = argparse.ArgumentParser(description=scaffold_main.__doc__)
    parser.add_argument('envelope', type=Path, help='captured agent-ee.envelope/1')
    root = parser.add_mutually_exclusive_group(required=True)
    root.add_argument('--resolved', type=Path, help='resolved record: agent_ee.schematic_emit root sheet')
    root.add_argument('--layout', type=Path, help='schematic layout: agent_ee.schematic_emitter root sheet')
    parser.add_argument('--template', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        sheet = (root_sheet_uuid(json.loads(args.resolved.read_text())) if args.resolved else
                 layout_root_uuid(json.loads(args.layout.read_text())))
        files = scaffold_files(args.envelope.read_bytes(), sheet, args.template)
    except (PrepareFailure, ValueError, KeyError, TypeError) as exc:
        print(f'write-scaffold refused: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 3
    for path, data in files.items():
        target = args.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return 0


SCAFFOLD_PATHS = tuple([f'project/{NAME}.{ext}' for ext in ('kicad_pro', 'kicad_sch', 'kicad_pcb')] +
                       ['project/sym-lib-table', 'project/fp-lib-table'] +
                       [f'generation/template/{NAME}.{ext}' for ext in ('kicad_pcb', 'kicad_pro')])
BOARD_ENVELOPE = 'generation/board-envelope.json'


def scaffold_step(source_root, steps_dir, template_dir, *, envelope=BOARD_ENVELOPE,
                  root=('--resolved', 'generation/resolved.json')):
    """agent-ee.stage-step/1 for the empty scaffold; the driver runs it before the campaign exists.

    In place: every input is read at its candidate path, so the receipt's after-tree is the
    first capture (AGENTEE-139 pre_receipt)."""
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'scaffold', 'step': 'write-scaffold',
            'argv': ['/usr/bin/python3', '-B', '-P', f'{MOUNT_STEPS}/scaffold.py', f'/candidate/{envelope}',
                     root[0], f'/candidate/{root[1]}', '--template', MOUNT_TEMPLATE, '--out', '/work/out'],
            'mounts': [{'source': str(Path(steps_dir)), 'at': MOUNT_STEPS},
                       {'source': str(Path(source_root)), 'at': MOUNT_SOURCE},
                       {'source': str(Path(template_dir)), 'at': MOUNT_TEMPLATE}],
            'source': MOUNT_SOURCE, 'version_argv': ['/usr/bin/python3', '--version'],
            'writes': {path: 'out/' + path for path in SCAFFOLD_PATHS}, 'adoptable_exits': [0], 'timeout_s': 120}


def run_scaffold_step(rundir, scaffold, step):
    """Run the write-scaffold step on SCAFFOLD, adopt exactly its outputs; returns the receipt summary."""
    from .stage_receipts import ADOPTABLE, adopt, run_step
    receipt_dir = Path(rundir) / 'scaffold-receipt'
    receipt = run_step(receipt_dir, scaffold, step, {'pre_receipt': 1})
    if receipt['status'] not in ADOPTABLE:
        raise PrepareFailure(f"write-scaffold step {receipt['status']} (exit {receipt['exit_code']}, "
                             f"violations {receipt['violations']}); receipt kept at {receipt_dir}")
    adopt(receipt_dir, receipt, scaffold)
    return {**receipt_summary(receipt_dir), 'receipt': 'scaffold-receipt/receipt.json',
            'binding': {'pre_receipt': 1}}


def write_scaffold(rundir, prep, *, source_root=PACKAGE_ROOT, steps_dir=STEPS_DIR, template_dir=FIXTURE_PROJECT):
    """RUNDIR/scaffold/{project,requirements,generation}; returns the scaffold directory and record.

    The driver writes the immutable generation inputs; the empty project and the builder template
    come only from the sandboxed `write-scaffold` step, whose stage receipt is kept in
    RUNDIR/scaffold-receipt and adopted exactly.
    """
    scaffold = Path(rundir) / 'scaffold'
    generation = scaffold / 'generation'
    scaffold.mkdir()
    shutil.copytree(prep['requirements'], scaffold / 'requirements')
    (scaffold / 'requirements' / 'review-conventions.md').write_text(REVIEW_CONVENTIONS)
    (scaffold / 'requirements' / 'fab-package.md').write_text(FAB_PACKAGE_TEXT)
    if prep.get('cpl_inputs'):
        (scaffold / 'requirements' / 'fab.md').write_text(CPL_REQUIREMENT_TEXT)
    generation.mkdir()
    if prep.get('cpl_inputs'):
        shutil.copytree(prep['cpl_inputs'], generation / 'cpl')
    write_json(generation / 'intent.json', prep['intent'])
    write_json(generation / 'symbol-captures.json', prep['symbol_captures'])
    for path, data in prep['symbol_libraries'].items():
        target = generation / 'symbols' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (generation / 'resolved.json').write_bytes(prep['resolved_bytes'])
    shutil.copyfile(prep['parts_path'], generation / 'parts-manifest.json')
    catalog = prep['parts_path'].parent / 'catalog'
    shutil.copytree(catalog, generation / 'catalog')
    write_json(generation / 'envelope.json', prep['envelope'])
    # The owned envelope the scaffold board is built from; board_build keeps its own envelope.json.
    envelope_raw = canonical(envelope_record(prep['params'])) + b'\n'
    (scaffold / BOARD_ENVELOPE).write_bytes(envelope_raw)
    write_json(generation / 'placement-request.json', prep['request'])
    stage_receipt = run_scaffold_step(rundir, scaffold, scaffold_step(source_root, steps_dir, template_dir))
    files = {p.relative_to(scaffold).as_posix(): digest(p) for p in sorted(scaffold.rglob('*')) if p.is_file()}
    record = {'schema': SCAFFOLD_SCHEMA, 'qualification': QUALIFICATION,
              'root_sheet_uuid': root_sheet_uuid(prep['resolved']), 'builder': 'agent_ee.scaffold.native_files',
              'envelope': {'path': BOARD_ENVELOPE, 'sha256': g.sha256_bytes(envelope_raw)},
              'template_project': template_record(template_dir), 'files': files, 'stage_receipt': stage_receipt,
              'catalog': {k: prep['catalog'][k] for k in ('path', 'sha256')} | {
                  'source': prep['catalog'].get('source')}}
    write_json(Path(rundir) / 'scaffold.json', record)
    return scaffold, record


def template_record(template_dir):
    """The fixture files the step reads: the project settings and the stock library tables."""
    return {'path': str(template_dir), 'sha256': {
        n: digest(Path(template_dir) / n) for n in ('rc_filter.kicad_pro', 'sym-lib-table', 'fp-lib-table')}}


def scaffold_binding(record, candidate_manifest, *, receipt_root):
    """Whether the scaffold receipt's after-tree equals the first capture in its folders, both ways."""
    from .stage_receipts import pre_receipt_binding, validate_receipt
    summary = record['stage_receipt']
    path = Path(receipt_root) / summary['receipt']
    binding = {'pre_receipt': 1} if 'binding' in summary else {'rung1': 'scaffold', 'receipt_index': 1}
    if summary.get('binding', binding) != binding:
        raise ValueError('foreign scaffold stage receipt binding')
    receipt = validate_receipt(path.parent, binding=binding)
    if digest(path) != record['stage_receipt']['receipt_sha256']:
        raise ValueError('scaffold stage receipt changed after it was recorded')
    return {'receipt': summary['receipt'],
            **pre_receipt_binding(receipt, candidate_manifest['files'], folders=SCAFFOLD_DIRS)}


def first_capture_binding(base, run, *, receipt_root):
    """scaffold_binding against RUN/candidate.json; a missing or invalid receipt is unbound, never skipped."""
    record = base.get('scaffold') or {}
    manifest = Path(run) / 'candidate.json'
    if not record.get('stage_receipt'):
        return {'bound': False, 'error': 'no scaffold stage receipt recorded'}
    if not manifest.is_file():
        return {'bound': False, 'error': f'no captured candidate at {manifest}'}
    try:
        campaign = Path(run).parent.parent
        config_path = campaign / 'config.json'
        if config_path.is_file() and 'pre_receipts' in read_json(config_path):
            from .stage_receipts import verify_pre_receipts
            config = read_json(config_path)
            verify_pre_receipts(campaign, read_json(campaign / 'state.json'), config['pre_receipts'])
            summary = record['stage_receipt']
            links = [link for link in config['pre_receipts'] if link['binding'] == summary.get('binding')
                     and link['sha256'] == summary['receipt_sha256']]
            if len(links) != 1:
                raise ValueError('scaffold pre_receipt missing from campaign')
            record = {'stage_receipt': {**summary, 'receipt': links[0]['path']}}
            receipt_root = campaign
        return scaffold_binding(record, read_json(manifest), receipt_root=receipt_root)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {'bound': False, 'error': f'{type(exc).__name__}: {exc}'}


def scaffold_load_checks(rundir, scaffold):
    """kicad-cli ERC, XML netlist and DRC load the EMPTY files, on sandboxed copies outside the scaffold."""
    base = Path(rundir) / 'scaffold-checks'
    candidate = g.native_candidate(base / 'candidate', scaffold / 'project',
                                   ['Resistor_SMD', 'Capacitor_SMD', 'Connector_PinHeader_2.54mm'])
    results = {}
    commands = {
        'erc': ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm',
                '-o', '/work/report.json', f'/candidate/project/{NAME}.kicad_sch'],
        'netlist': ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                    '-o', '/work/report.xml', f'/candidate/project/{NAME}.kicad_sch'],
        'drc': ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity',
                '--units', 'mm', '-o', '/work/report.json', f'/candidate/project/{NAME}.kicad_pcb'],
    }
    for label, argv in commands.items():
        work = base / label
        g.seed_kicad_config(work, candidate)
        record = executor.execute(candidate, work, argv, timeout=300)
        report = next((p for p in (work / 'report.json', work / 'report.xml') if p.is_file()), None)
        entry = {'argv': argv, 'execution': record['execution'], 'exit_code': record['exit_code'],
                 'report': rel(report, rundir) if report else None,
                 'report_sha256': digest(report) if report else None}
        if report is not None and report.suffix == '.json':
            data = json.loads(report.read_text())
            if label == 'erc':
                entry['violations'] = sum(len(s.get('violations', [])) for s in data.get('sheets', []))
            else:
                entry.update({k: len(data.get(k, [])) for k in ('violations', 'unconnected_items',
                                                                 'schematic_parity')})
        elif report is not None:
            entry['components'] = len(re.findall(r'<comp ', report.read_text()))
        entry['loaded'] = record['execution'] == 'ok' and report is not None and report.stat().st_size > 0
        results[label] = entry
    return results


# ------------------------------------------------------------------ staged scope

def compile_staged_scope(params, scope_dir, scaffold):
    """scope/2 from the closed-loop control template with an agent-ee.stages/1 declaration."""
    from .fab_stage import CPL_ROOT, rotation_check
    template = json.loads(g.SCOPE_TEMPLATE.read_text())
    scope = g.compile_scope(template, params, NAME)
    base = Path(scope_dir)
    inputs = []
    for item in template['inputs']:
        if item['at'] == 'project':
            item = {'path': os.path.relpath(scaffold / 'project', base), 'at': 'project'}
        elif item['at'] == 'requirements':
            item = {'path': os.path.relpath(scaffold / 'requirements', base), 'at': 'requirements'}
        elif item['at'] == 'stock/symbols':
            item = {'path': os.path.relpath(scaffold / 'generation/symbols', base), 'at': 'stock/symbols'}
        inputs.append(item)
    inputs.append({'path': os.path.relpath(scaffold / 'generation', base), 'at': 'generation'})
    scope['inputs'] = inputs
    scope['name'] = 'Rung-1 RC low-pass, staged repair campaign from an empty candidate'
    scope['requirements'] = list(scope.get('requirements', [])) + ['requirements/fab-package.md',
                                                                    'requirements/review-conventions.md']
    scope['scope'] = ('Review the rung-1 RC low-pass candidate against the requirements compiled from its '
                      'one-paragraph spec. The campaign starts from an empty native project (no symbols, '
                      'footprints or copper). After each implementation session the coordinator runs the '
                      'declared generation steps (schematic emitter, board build with row placement, '
                      'kicad-route KRT, fab export) on the delivered design and records stage receipts; '
                      'only fresh checks and reviews of the captured result decide acceptance, ending with '
                      'the fab_package done gate. Immutable '
                      'generation inputs are under generation/. This is a disposable benchmark board.')
    by_id = {c['id']: c for c in scope['checks']}
    if set(by_id) != set(CHECK_STAGES):
        raise PrepareFailure(f'scope template checks {sorted(by_id)} differ from {sorted(CHECK_STAGES)}')
    scope['checks'] = [{**by_id[cid], 'stage': stage} for cid, stage in CHECK_STAGES.items()]
    done_check = json.loads(json.dumps(DONE_CHECK))
    order = list(STAGE_ORDER)
    if (scaffold / CPL_ROOT).is_dir():
        # A --cpl-inputs capture declares the correction (AGENTEE-126): export, correct, then the done
        # gate, which seals the corrected CPL; all three are the final `fab` stage.
        cpl = rotation_check(scaffold)
        cpl['params']['export_check'] = 'fab-export'
        scope['checks'] += [
            {'id': 'fab-export', 'kind': 'fab_export', 'stage': 'fab', 'params': {'netlist_check': 'netlist'}},
            {**cpl, 'stage': 'fab'}]
        scope['baseline']['requirements'].append(json.loads(json.dumps(CPL_REQUIREMENT)))
        scope['requirements'].append('requirements/fab.md')
        done_check['stage'] = 'fab'
        order.append('fab')
    scope['checks'].append(done_check)
    scope['baseline']['requirements'].append(json.loads(json.dumps(FAB_REQUIREMENT)))
    scope['generation'] = {'schema': 'agent-ee.generation/1', 'done_check': DONE_CHECK['id']}
    scope['stages'] = {
        'schema': 'agent-ee.stages/1', 'order': order,
        'subjects': {
            'schematic': {'path': scope['schematic'], 'kind': 'symbols',
                          'references': [params['resistor']['ref'], params['capacitor']['ref'],
                                         params['header']['ref']]},
            'placement': {'path': scope['board'], 'kind': 'footprints',
                          'references': [params['resistor']['ref'], params['capacitor']['ref'],
                                         params['header']['ref']]},
            'routing': {'path': scope['board'], 'kind': 'tracks', 'minimum': 1}},
        'finding_retry_limit': 3}
    scope['mutable_inputs'] = [scope['schematic'], scope['board']]
    return scope


def validate_scope_file(scope_path):
    """The real loaders: capture selection, baseline compilation, stage declaration, scope fields."""
    from .baseline import compile_baseline
    from .cli import validate_scope
    from .candidate import source_manifest
    from .stages import declaration
    scope_path = Path(scope_path)
    scope = read_json(scope_path)
    files, _, _ = source_manifest(scope, scope_path.parent)
    validate_scope(scope)
    stages = declaration(scope, files)
    baseline = compile_baseline(scope, files)
    mutable = scope['mutable_inputs']
    if sorted(mutable) != sorted([scope['schematic'], scope['board']]):
        raise PrepareFailure('mutable_inputs must be exactly the schematic and board')
    immutable = [p for p in files if p.startswith(('generation/', 'requirements/'))]
    if set(mutable) & set(immutable) or set(mutable) & set(baseline['files']):
        raise PrepareFailure('mutable inputs overlap immutable generation or baseline sources')
    return {'files': len(files), 'baseline_id': baseline['baseline_id'], 'stage_policy_id': identity(stages),
            'mutable_inputs': mutable, 'immutable_generation_inputs': sorted(immutable),
            'baseline_files': sorted(baseline['files'])}


# ------------------------------------------------------------------ campaign configuration

def step_specs(source_root, steps_dir, kicad_route):
    """agent-ee.stage-step/1 declarations; mounts are read-only and outside reserved sandbox paths."""
    from .fab_stage import step as fab_step
    source = {'source': str(Path(source_root)), 'at': MOUNT_SOURCE}
    steps = {'source': str(Path(steps_dir)), 'at': MOUNT_STEPS}
    route = {'source': str(Path(kicad_route)), 'at': MOUNT_ROUTE}
    board, sch = f'project/{NAME}.kicad_pcb', f'project/{NAME}.kicad_sch'
    return [
        {'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'emit-schematic',
         'argv': ['/usr/bin/python3', '-B', '-P', f'{MOUNT_STEPS}/emit.py', '/candidate/generation/resolved.json',
                  '--libraries', '/candidate/stock/symbols', '--parts', '/candidate/generation/parts-manifest.json',
                  '--project-template', f'/candidate/project/{NAME}.kicad_pro', '--name', NAME,
                  '--out', '/work/out/sch'],
         'mounts': [steps, source], 'source': MOUNT_SOURCE,
         'version_argv': ['/usr/bin/python3', '--version'],
         'writes': {sch: f'out/sch/{NAME}.kicad_sch'},
         'artifacts': ['out/sch/emit-receipt.json', f'out/sch/{NAME}.kicad_pro', 'out/sch/sym-lib-table',
                       'out/sch/fp-lib-table'],
         'adoptable_exits': [0], 'timeout_s': 300},
        {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': 'build-board',
         'argv': ['/bin/bash', f'{MOUNT_STEPS}/placement.sh', NAME],
         'mounts': [steps, source], 'source': MOUNT_SOURCE,
         'version_argv': ['/usr/bin/kicad-cli', 'version'],
         'writes': {board: f'out/board/placed/{NAME}.kicad_pcb'},
         'artifacts': ['out/netlist.xml', 'out/board/build-receipt.json',
                       'out/board/placed/placement-result.json'],
         'adoptable_exits': [0], 'timeout_s': 900},
        {'schema': 'agent-ee.stage-step/1', 'stage': 'routing', 'step': 'route-krt',
         'argv': ['/bin/bash', f'{MOUNT_STEPS}/route.sh', NAME],
         'mounts': [steps, route],
         'version_argv': ['/usr/bin/git', '-C', MOUNT_ROUTE, 'describe', '--tags', '--always', '--dirty'],
         'backend_report': {'artifact': 'out/result.json', 'key': 'backend'},
         'writes': {board: f'out/candidate/{NAME}.kicad_pcb'},
         'artifacts': ['out/result.json', 'out/route'],
         'adoptable_exits': [0], 'timeout_s': 1500},
        fab_step(source_root, board, sch),
    ]


def check_steps(steps, mutable):
    """Every declaration passes stage_receipts.validate_step plus the rung-1 adoption rules."""
    from .stage_receipts import validate_step
    normalized = []
    for spec in steps:
        value = validate_step(spec)
        forbidden = set(value['adoptable_exits']) & NEVER_ADOPTABLE.get(value['stage'], set())
        if forbidden:
            raise ValueError(f"step {value['step']}: kicad-route exits {sorted(forbidden)} are never adoptable")
        if value['adoptable_exits'] != [0]:
            raise ValueError(f"step {value['step']}: rung 1 adopts exit 0 only")
        outside = sorted(set(value['writes']) - set(mutable))
        if outside:
            raise ValueError(f"step {value['step']} writes outside mutable_inputs: {outside}")
        normalized.append(value)
    return normalized



REVIEW_CONVENTIONS = """# Review and adjudication conventions for this scope

These are captured owner conventions for the independent reviewers and the adjudicator.
They do not change any check, limit or acceptance rule; they state how the coordinator
validates structured responses so that a valid judgment is not lost to a path or shape error.

- Evidence citations must name retained run files only, with paths under `inputs/`
  (captured sources, for example `inputs/requirements/system.md`), `checks/<check-id>.json`,
  `work/<check-id>/<file>` (raw tool outputs) or `review-work/<role>/experiments/<file>`
  (your own retained experiments). The baseline file (`baseline.json`, mounted at
  `/evidence/baseline.json`) is citable when it matches the immutable captured baseline.
  Cite requirements there or in the captured source files under `inputs/requirements/`.
- A witness for an accepted defect must name an existing requirement id from the baseline and
  the id of a coordinator check whose retained result is cited unchanged. Do not restate,
  summarise or alter a check result inside a witness; reviewer hypotheses without a failed
  coordinator check remain concerns.
- Locators must point at content that exists in the cited file. Each citation is validated
  against the retained run; a single invalid citation or witness invalidates the whole
  response. A structured-output refusal permits one separately recorded resubmission
  (`max_schema_resubmissions`, default 1) with the exact error and unchanged inputs; a
  second refusal stays incomplete. Checks are never resubmitted.
"""


def scaffold_pre_receipt(record):
    summary = record['stage_receipt']
    return {'path': summary['receipt'], 'sha256': summary['receipt_sha256'], 'binding': summary['binding']}


def campaign_config(scope_path, profile, steps, max_seconds=3600, max_iterations=2):
    adjudicator = profile['adjudicator']
    return {'schema': 'agent-ee.campaign/1', 'scope': Path(scope_path).name, 'review_profile': profile,
            'implementation': {'harness': adjudicator['harness'], 'model': adjudicator['model'],
                               'effort': adjudicator['effort'], 'timeout_s': 900},
            'max_iterations': max_iterations, 'max_seconds': max_seconds, 'max_schema_resubmissions': 1,
            'implementation_steps': steps}


# ------------------------------------------------------------------ records

def agent_ee_command(*args):
    return [sys.executable, '-B', '-m', 'agent_ee.cli', *map(str, args)]


def run_logged(argv, log_dir, label, timeout, source=PACKAGE_ROOT):
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONPATH': str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=REPO_ROOT, env=env)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = None, exc.stdout or b'', exc.stderr or b''
    (log_dir / f'{label}.stdout.log').write_bytes(out)
    (log_dir / f'{label}.stderr.log').write_bytes(err)
    return {'argv': [str(a) for a in argv], 'exit_code': code,
            'stdout': f'{label}.stdout.log', 'stdout_sha256': g.sha256_bytes(out),
            'stderr': f'{label}.stderr.log', 'stderr_sha256': g.sha256_bytes(err),
            'stdout_tail': out.decode(errors='replace')[-2000:], 'stderr_tail': err.decode(errors='replace')[-2000:]}


def summarize_checks(run):
    result = read_json(Path(run) / 'result.json')
    checks = {c['id']: {'execution': c.get('execution'), 'outcome': c.get('outcome'),
                        'reason': (c.get('details') or {}).get('reason'), 'stage': (c.get('details') or {}).get('stage'),
                        'summary': c.get('summary')}
              for c in result.get('checks') or []}
    findings = [{k: f.get(k) for k in ('id', 'role', 'source', 'severity', 'assessment', 'title', 'status',
                                        'disposition', 'disposition_reason')}
                for f in result.get('findings') or []]
    return {'run': str(run), 'run_id': result.get('run_id'), 'outcome': result.get('outcome'),
            'state': result.get('state'), 'error': result.get('error'), 'checks': checks,
            'findings': findings, 'review_executions': [
                {k: e.get(k) for k in ('role', 'execution', 'exit_code', 'harness', 'model')}
                for e in result.get('review_executions') or []]}


def receipt_summary(receipt_dir):
    receipt = read_json(receipt_dir / 'receipt.json')
    tool = receipt['tool']
    return {'step': receipt['spec']['step'], 'stage': receipt['spec']['stage'],
            'receipt': str(receipt_dir / 'receipt.json'), 'receipt_sha256': digest(receipt_dir / 'receipt.json'),
            'receipt_identity': receipt['identity'], 'status': receipt['status'], 'exit_code': receipt['exit_code'],
            'violations': receipt['violations'], 'changed': receipt['changed'],
            'inputs_identity': identity(receipt['inputs']), 'outputs': receipt['outputs'],
            'artifacts': {k: v for k, v in receipt['artifacts'].items() if '/route/' not in k and '/raw/' not in k},
            'artifact_count': len(receipt['artifacts']),
            'tool': {'executable': tool['executable'], 'mounts': tool['mounts'], 'source_tree': tool['source_tree'],
                     'reported_version': tool['reported_version'], 'backend': tool['backend']},
            'duration_s': receipt['duration_s'], 'sandbox_profile_sha256': receipt['sandbox_profile_sha256']}


def fab_package(receipt_dir):
    """The fab step's manifest and whether every listed file exists, is nonempty and matches."""
    run = receipt_dir / 'run' / 'out'
    manifest_path = run / 'fab-manifest.json'
    if not manifest_path.is_file():
        return None
    manifest = read_json(manifest_path)
    files = manifest.get('files') or []
    ok = bool(files) and all((run / 'fab' / f['path']).is_file() and (run / 'fab' / f['path']).stat().st_size > 0
                             and digest(run / 'fab' / f['path']) == f['sha256'] for f in files)
    return {'path': str(manifest_path), 'sha256': digest(manifest_path), 'files': len(files),
            'exports_exist': ok, 'status': manifest.get('status'),
            'physical_validation': manifest.get('physical_validation'),
            'board_sha256': manifest.get('board', {}).get('sha256'),
            'schematic_sha256': manifest.get('schematic', {}).get('sha256'),
            'bom_designators': manifest.get('bom_designators'), 'cpl_designators': manifest.get('cpl_designators'),
            'assembly_exclusions': manifest.get('assembly_exclusions')}


def campaign_summary(root):
    root = Path(root)
    state = read_json(root / 'state.json') if (root / 'state.json').is_file() else None
    iterations = []
    runs = sorted((p for p in (root / 'runs').iterdir() if p.name.isdigit()), key=lambda p: int(p.name)) \
        if (root / 'runs').is_dir() else []
    for index, run in enumerate(runs):
        entry = summarize_checks(run) if (run / 'result.json').is_file() else {'run': str(run), 'result': None}
        if state and index < len(state.get('history', [])):
            entry['convergence'] = {k: state['history'][index].get(k) for k in (
                'failed', 'passing', 'outcome', 'earliest_failing_stage', 'stage_failures', 'blocked_checks',
                'recurring_findings', 'candidate_id', 'design_id')}
        iterations.append(entry)
    implementations = []
    for work in sorted((p for p in (root / 'implementations').glob('*') if p.name.isdigit()),
                       key=lambda p: int(p.name)) if (root / 'implementations').is_dir() else []:
        execution = read_json(work / 'implementation-execution.json') \
            if (work / 'implementation-execution.json').is_file() else None
        receipts = sorted((p for p in (work / 'receipts').glob('*') if (p / 'receipt.json').is_file()),
                          key=lambda p: int(p.name.split('-')[0])) if (work / 'receipts').is_dir() else []
        index = read_json(work / 'stage-receipts.json') if (work / 'stage-receipts.json').is_file() else None
        implementations.append({
            'implementation': work.name,
            'writer': {k: execution.get(k) for k in ('execution', 'exit_code', 'duration_s')} if execution else None,
            'receipts': [receipt_summary(p) for p in receipts],
            'index': ({'path': str(work / 'stage-receipts.json'), 'sha256': digest(work / 'stage-receipts.json'),
                       'applicability': [{k: r.get(k) for k in ('receipt', 'step', 'status', 'applicability')}
                                         for r in index.get('receipts', [])]} if index else None),
            'fab': next((fab_package(p) for p in receipts if p.name.endswith('-export-fab')), None)})
    return state, iterations, implementations


def recomputed_obligations(lines):
    """The preview obligations verify-campaign printed before the artifact hashes; False when malformed."""
    from .fab_package import PREVIEW_ACTION
    start = [i for i, line in enumerate(lines) if line.startswith('fab_preview_obligations ')]
    listed = [line for line in lines if line.startswith('fab_preview_obligation ')]
    if not start:
        return False if listed else None
    rotation = [line.split() for line in lines if line.startswith('fab_rotation ')]
    head = re.fullmatch(r'fab_preview_obligations ([1-9][0-9]*) policy ([0-9a-f]{64}): (.*)', lines[start[0]])
    if len(start) != 1 or not head or head[3] != PREVIEW_ACTION or len(rotation) != 1 or len(rotation[0]) != 4:
        return False
    entries = lines[start[0] + 1:start[0] + 1 + int(head[1])]
    if entries != listed or len(listed) != int(head[1]) or not all(len(e.split()) == 3 for e in entries):
        return False
    return {'status': rotation[0][1], 'policy_sha256': head[2], 'action': head[3],
            'designators': [{'designator': e.split()[1], 'side': e.split()[2]} for e in entries]}


def recomputed_artifacts(stdout, run):
    """The fab artifact hashes and preview obligations verify-campaign printed for the final run, or None."""
    lines = stdout.splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith('fab_artifacts ')), None)
    if header is None:
        return None
    obligations = recomputed_obligations(lines[:header])
    if obligations is False:
        return None
    fields = lines[header].split()
    if len(fields) != 4 or fields[2] != 'candidate':
        return None
    files, prefix = {}, f'{run}/'
    for line in lines[header + 1:]:
        sha, sep, path = line.partition('  ')
        if not sep or not path.startswith(prefix) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            return None
        files[path[len(prefix):]] = sha
    return {'artifacts_id': fields[1], 'candidate_id': fields[3], 'files': files,
            'preview_obligations': obligations}


def artifacts_agree(stored, recomputed):
    return (bool(stored and recomputed)
            and all(stored.get(k) == recomputed[k] for k in ('artifacts_id', 'candidate_id', 'files'))
            and ((stored.get('rotation') or {}).get('preview_obligations')
                 == recomputed.get('preview_obligations')))


def write_record(rundir, base, *, verdict=None, fab=None, fab_artifacts=None, recomputed=None, scaffold=None, stage=None):
    from .fab_package import rotation_blocker
    exists = bool(fab and fab['exports_exist'])
    verified = artifacts_agree(fab_artifacts, recomputed)
    scaffold_bound = bool(scaffold and scaffold.get('bound'))
    rotation = (fab_artifacts or {}).get('rotation') or {}
    obligations = rotation.get('preview_obligations') if verified else None
    blocker = (rotation_blocker(rotation.get('status'), obligations, rotation.get('policy_sha256'))
               if verified else None)
    done = (verdict == 'criteria_satisfied' and verified and scaffold_bound and blocker is None
            and (stage is None or stage['bound']))
    record = {'schema': RECORD_SCHEMA, 'qualification': QUALIFICATION, **base, 'verdict': verdict,
              'fab_manifest': fab, 'fab_exports_exist': exists, 'fab_artifacts': fab_artifacts,
              'fab_artifacts_recomputed_by_verify_campaign': recomputed, 'fab_artifacts_verified': verified,
              'fab_rotation': rotation or None, 'fab_rotation_blocker': blocker,
              # AGENTEE-151: what the operator must confirm in the JLCPCB placement preview before
              # ordering, as verify-campaign recomputed it; the rotation status is not upgraded.
              'preview_obligations': obligations,
              'scaffold_receipt_binding': scaffold, 'stage_receipts_done': stage, 'done': done,
              'done_rule': ('campaign verdict criteria_satisfied AND the final campaign state lists the fab '
                            'artifact hashes of the passing fab_package done check AND verify-campaign recomputed '
                            'the same hashes from the retained run AND the scaffold receipt\'s after-tree is exactly the '
                            'first captured candidate on project/, generation/ and requirements/ AND the sealed '
                            'CPL rotation status is in fab_package.DONE_ROTATION_STATUSES, a PREVIEW_REQUIRED '
                            'status only with the preview_obligations verify-campaign recomputed (AGENTEE-151) '
                            'AND, with declared '
                            'stage steps, the final state lists the agent-ee.stage-done/1 binding verify-campaign '
                            "recomputed: each declared stage's graded content is its step receipt output "
                            '(decision 1 amendment)')}
    write_json(Path(rundir) / 'rung1-campaign-record.json', record)
    return record


# ------------------------------------------------------------------ driver

def build(spec, rundir, *, profile_path, kicad_route, catalog_checkout=None, max_seconds=3600, cpl_inputs=None):
    rundir = Path(rundir).absolute()
    prep = prepare(spec, rundir, catalog_checkout, cpl_inputs=cpl_inputs)
    scaffold, scaffold_record = write_scaffold(rundir, prep)
    loads = scaffold_load_checks(rundir, scaffold)
    if not all(entry['loaded'] for entry in loads.values()):
        raise PrepareFailure('kicad-cli did not load the empty scaffold: ' + json.dumps(loads))
    scope = compile_staged_scope(prep['params'], rundir, scaffold)
    scope_path = rundir / 'scope.json'
    write_json(scope_path, scope)
    validation = validate_scope_file(scope_path)
    profile = read_json(profile_path)
    steps = check_steps(step_specs(PACKAGE_ROOT, STEPS_DIR, kicad_route), scope['mutable_inputs'])
    config = campaign_config(scope_path, profile, steps, max_seconds=max_seconds)
    config['pre_receipts'] = [scaffold_pre_receipt(scaffold_record)]
    from .profiles import validate_profile
    validate_profile(config['review_profile'])
    validate_profile({**config['review_profile'], 'adjudicator': config['implementation']})
    config_path = rundir / 'campaign.json'
    write_json(config_path, config)
    base = {'spec': {'path': str(Path(spec).absolute()), 'sha256': digest(spec)},
            'generator': g.tool_identity('agent_ee'),
            'kicad_route': route_identity(kicad_route),
            'prep_receipts': prep['prep_receipts'],
            'scaffold': {'path': 'scaffold.json', 'sha256': digest(rundir / 'scaffold.json'),
                         'root_sheet_uuid': scaffold_record['root_sheet_uuid'], 'load_checks': loads,
                         'stage_receipt': scaffold_record['stage_receipt']},
            'scope': {'path': 'scope.json', 'sha256': digest(scope_path), **validation},
            'campaign_config': {'path': 'campaign.json', 'sha256': digest(config_path),
                                'profile': str(Path(profile_path).absolute()), 'profile_sha256': digest(profile_path),
                                'steps': [s['step'] for s in steps]}}
    return base, scope_path, config_path


def route_identity(path):
    path = Path(path)
    def git(*args):
        proc = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=60)
        return proc.stdout.strip() if proc.returncode == 0 else None
    return {'path': str(path), 'describe': git('describe', '--tags', '--always', '--dirty'),
            'commit': git('rev-parse', 'HEAD'), 'origin': git('remote', 'get-url', 'origin'),
            'krt_commit': git_dir(path / 'vendor' / 'krt'),
            'note': 'read-only snapshot mounted at /tool/kicad-route with KICAD_ROUTE_STATE=/tool/kicad-route'}


def git_dir(path):
    proc = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=60)
    return proc.stdout.strip() if proc.returncode == 0 else None


def rehearse(rundir, scope_path, config_path, review_run):
    """Dry-run only: the four steps on a copy of the captured EMPTY design, as the coordinator runs them."""
    from .stage_receipts import adopt, run_step
    base = Path(rundir) / 'dry-run' / 'rehearsal'
    design = base / 'design'
    shutil.copytree(Path(review_run) / 'inputs', design)
    receipts = []
    for k, spec in enumerate(read_json(config_path)['implementation_steps'], 1):
        receipt_dir = base / 'receipts' / f"{k}-{spec['step']}"
        receipt = run_step(receipt_dir, design, spec, {'implementation': 0, 'rehearsal': True,
                                                       'receipt_index': k})
        receipts.append(receipt_summary(receipt_dir))
        try:
            adopt(receipt_dir, receipt, design)
        except ValueError as exc:
            receipts[-1]['adoption_error'] = str(exc)
            break
    scope = read_json(scope_path)
    scope['inputs'] = [{'path': str(p), 'at': p.name} for p in sorted(design.iterdir())]
    rehearsal_scope = base / 'rehearsal.scope.json'
    write_json(rehearsal_scope, scope)
    fab = next((fab_package(base / 'receipts' / f"{k}-{r['step']}") for k, r in enumerate(receipts, 1)
                if r['step'] == 'export-fab'), None)
    return receipts, rehearsal_scope, fab


def run(spec, rundir, *, profile_path, kicad_route, dry_run, catalog_checkout=None, max_seconds=3600,
        cpl_inputs=None):
    rundir = Path(rundir).absolute()
    if rundir.exists():
        raise FileExistsError(f'{rundir} exists; choose a fresh run directory')
    rundir.mkdir(parents=True)
    try:
        base, scope_path, config_path = build(spec, rundir, profile_path=profile_path, kicad_route=kicad_route,
                                              catalog_checkout=catalog_checkout, max_seconds=max_seconds,
                                              cpl_inputs=cpl_inputs)
    except (PrepareFailure, ValueError, OSError) as exc:
        failed = {'mode': 'dry_run' if dry_run else 'campaign', 'error': f'{type(exc).__name__}: {exc}'}
        if (rundir / 'scaffold-receipt' / 'receipt.json').is_file():
            failed['scaffold_stage_receipt'] = receipt_summary(rundir / 'scaffold-receipt')
        return write_record(rundir, failed)
    logs = rundir / 'logs'
    if dry_run:
        run_dir = rundir / 'dry-run' / 'empty-checks-only'
        command = run_logged(agent_ee_command('review', scope_path, '--out', run_dir, '--checks-only'),
                             logs, 'dry-run-empty-review', 1800)
        empty = summarize_checks(run_dir) if (run_dir / 'result.json').is_file() else None
        receipts, rehearsal_scope, fab = rehearse(rundir, scope_path, config_path, run_dir)
        after_dir = rundir / 'dry-run' / 'rehearsed-checks-only'
        after_command = run_logged(agent_ee_command('review', rehearsal_scope, '--out', after_dir, '--checks-only'),
                                   logs, 'dry-run-rehearsed-review', 1800)
        after = summarize_checks(after_dir) if (after_dir / 'result.json').is_file() else None
        base.update(mode='dry_run', dry_run={
            'empty_review': {'command': command, 'result': empty},
            'rehearsal': {'receipts': receipts, 'note': 'rehearsal outside any campaign; not acceptance evidence'},
            'rehearsed_review': {'command': after_command, 'result': after}})
        return write_record(rundir, base, fab=fab, scaffold=first_capture_binding(base, run_dir, receipt_root=rundir))
    root = rundir / 'campaign'
    command = run_logged(agent_ee_command('campaign', config_path, '--out', root), logs, 'campaign',
                         max_seconds + 3600)
    return campaign_record(rundir, base, command)


def campaign_record(rundir, base, command, source=PACKAGE_ROOT):
    """Summarize the retained campaign, run verify-campaign (no inference) and write the record.

    verify-campaign runs from `source`, the package the campaign itself ran under.
    """
    rundir = Path(rundir)
    root = rundir / 'campaign'
    verify = run_logged(agent_ee_command('verify-campaign', root), rundir / 'logs', 'verify-campaign', 1800,
                        source=source)
    state, iterations, implementations = campaign_summary(root)
    fab = next((i['fab'] for i in reversed(implementations) if i['fab']), None)
    verdict = state.get('state') if state else None
    # The fab step must have consumed exactly the board and schematic of the final captured run.
    fab_final = False
    if fab and iterations and iterations[-1].get('checks') is not None:
        manifest = read_json(Path(iterations[-1]['run']) / 'candidate.json')
        fab_final = (fab['board_sha256'] == manifest['files'].get(f'project/{NAME}.kicad_pcb') and
                     fab['schematic_sha256'] == manifest['files'].get(f'project/{NAME}.kicad_sch'))
    base.update(mode='campaign', campaign={
        'root': str(root), 'command': command, 'exit_code': command['exit_code'] if command else None,
        'state': verdict, 'error': state.get('error') if state else None,
        'diagnostic': state.get('diagnostic') if state else None,
        'stage_receipt_anchors': state.get('stage_receipts') if state else None,
        'iterations': iterations, 'implementations': implementations},
        fab_matches_final_captured_candidate=fab_final,
        verify_campaign={'exit_code': verify['exit_code'], 'stdout_tail': verify['stdout_tail'],
                         'stderr_tail': verify['stderr_tail']})
    recomputed = None
    if verify['exit_code'] == 0:
        stdout = (rundir / 'logs' / verify['stdout']).read_text(errors='replace') if verify.get('stdout') else ''
        final_run = state['history'][-1]['run'] if state and state.get('history') else None
        recomputed = recomputed_artifacts(stdout, final_run) if final_run else None
    elif verdict == 'criteria_satisfied':
        verdict = 'unverified:' + verdict
    from .stage_done import driver_term
    return write_record(rundir, base, verdict=verdict, fab=fab,
                        fab_artifacts=state.get('fab_artifacts') if state else None, recomputed=recomputed,
                        scaffold=first_capture_binding(base, root / 'runs' / '1', receipt_root=rundir),
                        stage=driver_term(root, state))


def rebuild_record(rundir):
    """Recompute the record of a finished campaign run from its retained files; spends no inference."""
    rundir = Path(rundir).absolute()
    path = rundir / 'rung1-campaign-record.json'
    original = read_json(path)
    if original.get('mode') != 'campaign' or not (rundir / 'campaign' / 'state.json').is_file():
        raise ValueError('record rebuild needs a finished campaign run directory')
    previous = rundir / 'rung1-campaign-record.previous.json'
    shutil.copyfile(path, previous)
    base = {k: v for k, v in original.items() if k in (
        'spec', 'generator', 'kicad_route', 'prep_receipts', 'scaffold', 'scope', 'campaign_config')}
    base['record_rebuilt'] = {'by': g.tool_identity('agent_ee')['version'], 'previous': previous.name,
                              'previous_sha256': digest(previous)}
    # verify-campaign must run under the package that ran the campaign (its policy files are pinned).
    source = Path(original['generator']['path']).parent
    base['record_rebuilt']['verify_campaign_source'] = str(source)
    return campaign_record(rundir, base, original.get('campaign', {}).get('command'), source=source)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--profile', type=Path, default=REPO_ROOT / 'profiles' / 'claude-baseline.json')
    parser.add_argument('--kicad-route', type=Path, default=DEFAULT_KICAD_ROUTE,
                        help='kicad-route checkout or snapshot holding vendor/krt and .venv-krt')
    parser.add_argument('--cpl-inputs', type=Path, help='explicit CPL policy and supplier capture tree')
    parser.add_argument('--dry-run', action='store_true',
                        help='scaffold, scope and config validation, checks-only review of the empty candidate '
                             'and a step rehearsal; no inference')
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--rebuild-record', action='store_true',
                        help='recompute the record of the finished campaign in --out (verify-campaign only)')
    args = parser.parse_args(argv)
    try:
        if args.rebuild_record:
            record = rebuild_record(args.out)
            print(json.dumps({k: record.get(k) for k in ('mode', 'verdict', 'done')}))
            return 0 if record['done'] else 1
        record = run(args.spec, args.out, profile_path=args.profile, kicad_route=args.kicad_route.absolute(),
                     dry_run=args.dry_run, max_seconds=args.max_seconds, cpl_inputs=args.cpl_inputs)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f'rung1_campaign refused: {exc}\n')
    print(json.dumps({k: record.get(k) for k in ('mode', 'error', 'verdict', 'fab_exports_exist',
                                                 'fab_artifacts_verified', 'done')}))
    return 0 if record['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
