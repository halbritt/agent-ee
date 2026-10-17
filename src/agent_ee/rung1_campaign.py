"""Rung 1 as a staged repair campaign that starts from an EMPTY candidate (decision 1).

Run ``python3 -m agent_ee.rung1_campaign SPEC.md --out RUNDIR [--profile P] [--dry-run]``
(see docs/rung1-campaign.md). The driver prepares immutable generation inputs with
``agent_ee.generate``'s stage-1 and stage-2 functions, writes an empty native
project, compiles a staged scope/2 and an ``agent-ee.campaign/1`` config whose
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
SCH_VERSION = '20260306'
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/halbritt/agent-ee/rung1-campaign/1')
STAGE_ORDER = ['schematic', 'placement', 'routing', 'verification']
CHECK_STAGES = {'erc': 'schematic', 'netlist': 'schematic', 'drc': 'placement',
                'geometry': 'routing', 'rc-step': 'verification'}
# kicad-route: 0 complete and valid; 2 valid but incomplete; 3 fails validation (never adoptable).
NEVER_ADOPTABLE = {'routing': {1, 2, 3, 4}}
MOUNT_STEPS, MOUNT_SOURCE, MOUNT_ROUTE = '/tool/steps', '/tool/agent-ee', '/tool/kicad-route'
DROPPED_BOARD_ITEMS = ('footprint', 'gr_line', 'gr_rect', 'gr_arc', 'gr_circle', 'gr_poly', 'gr_text',
                       'segment', 'arc', 'via', 'zone', 'group', 'dimension', 'image', 'net')


class PrepareFailure(Exception):
    """Generation inputs or the scaffold could not be established; nothing runs after it."""


# ------------------------------------------------------------------ preparation (immutable inputs)

def prepare(spec, rundir, catalog_checkout=None):
    """Stage 1 and 2 of agent_ee.generate, then intent, resolved record, envelope and request."""
    from .connectivity_ir import canonical_resolved, resolve_intent, validate_resolved
    prep = Path(rundir) / 'prep'
    state, options = {}, {'spec': str(Path(spec).absolute()), 'catalog_checkout': catalog_checkout}
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
    libraries, captured = [], {}
    for library in g.SYMBOL_LIBRARIES:
        raw = (g.STOCK / 'symbols' / f'{library}.kicad_sym').read_bytes()
        libraries.append({'name': library, 'path': f'{library}.kicad_sym', 'sha256': g.sha256_bytes(raw)})
        captured[f'{library}.kicad_sym'] = raw
    intent = g.connectivity_intent(params, parts['parts'], libraries)
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
            'catalog': parts['catalog']}


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


def project_bytes(template, root):
    """The fixture project with the emitter's own filename/sheet rewrite (schematic_emit._project)."""
    project = json.loads(template)
    project['meta']['filename'] = NAME + '.kicad_pro'
    if 'sheets' in project:
        project['sheets'] = [[root, 'Root']]
    if 'top_level_sheets' in project.get('schematic', {}):
        project['schematic']['top_level_sheets'] = [{'filename': NAME + '.kicad_sch', 'name': NAME, 'uuid': root}]
    return (json.dumps(project, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def empty_schematic(root):
    return ('(kicad_sch\n'
            f'\t(version {SCH_VERSION})\n'
            '\t(generator "eeschema")\n'
            '\t(generator_version "10.0")\n'
            f'\t(uuid "{root}")\n'
            '\t(paper "A4")\n'
            '\t(title_block\n'
            f'\t\t(title "{NAME}")\n'
            '\t\t(comment 1 "Empty rung-1 scaffold: no symbols; generated by the campaign\'s schematic step")\n'
            '\t)\n'
            '\t(lib_symbols)\n'
            '\t(sheet_instances\n'
            '\t\t(path "/"\n'
            '\t\t\t(page "1")\n'
            '\t\t)\n'
            '\t)\n'
            '\t(embedded_fonts no)\n'
            ')\n').encode('utf-8')


def top_level_blocks(text):
    """Split a tab-indented KiCad board into its top-level items; refuse anything else."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\n') != '(kicad_pcb' or lines[-1].rstrip('\n') != ')':
        raise PrepareFailure('template board is not a tab-indented kicad_pcb')
    blocks, current = [], None
    for line in lines[1:-1]:
        if current is None:
            match = re.match(r'\t\(([a-z_]+)', line)
            if not match:
                raise PrepareFailure('unexpected template board line: ' + line[:60])
            current = [match[1], [line]]
            if line.rstrip('\n').endswith(')') and line.count('(') == line.count(')'):
                blocks.append((current[0], ''.join(current[1])))
                current = None
            continue
        current[1].append(line)
        if line.rstrip('\n') == '\t)':
            blocks.append((current[0], ''.join(current[1])))
            current = None
    if current is not None:
        raise PrepareFailure('unterminated template board item: ' + current[0])
    return blocks


def edge_line(start, end, ident):
    return ('\t(gr_line\n'
            f'\t\t(start {g.fmt(start[0])} {g.fmt(start[1])})\n'
            f'\t\t(end {g.fmt(end[0])} {g.fmt(end[1])})\n'
            '\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n'
            '\t\t(layer "Edge.Cuts")\n'
            f'\t\t(uuid "{uuid.uuid5(UUID_NAMESPACE, ident)}")\n'
            '\t)\n')


def empty_board(template_text, envelope):
    """Fixture setup, layers and stackup; NO footprints, copper, zones or nets; outline from the envelope."""
    kept, dropped = [], {}
    for kind, body in top_level_blocks(template_text):
        if kind in DROPPED_BOARD_ITEMS:
            dropped[kind] = dropped.get(kind, 0) + 1
        else:
            kept.append((kind, body))
    kinds = [k for k, _ in kept]
    for required in ('version', 'layers', 'setup'):
        if required not in kinds:
            raise PrepareFailure(f'template board lacks {required}')
    version = re.search(r'\(version (\d+)\)', kept[kinds.index('version')][1])[1]
    if version not in ('20250114', '20260101', '20260206'):
        raise PrepareFailure('template board version outside the qualified presence set: ' + version)
    o = envelope['outline_mm']
    x0, y0, x1, y1 = o['x'], o['y'], o['x'] + o['w'], o['y'] + o['h']
    corners = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    outline = ''.join(edge_line(a, b, f'edge:{i}:{a}:{b}') for i, (a, b) in enumerate(corners))
    tail = [body for kind, body in kept if kind == 'embedded_fonts']
    head = ''.join(body for kind, body in kept if kind != 'embedded_fonts')
    text = '(kicad_pcb\n' + head + outline + ''.join(tail) + ')\n'
    return text.encode('utf-8'), dropped


def lib_table_check(project_dir):
    for table in ('sym-lib-table', 'fp-lib-table'):
        if '${KIPRJMOD}' in (project_dir / table).read_text():
            raise PrepareFailure(f'{table} must use stock references only')


def write_scaffold(rundir, prep):
    """RUNDIR/scaffold/{project,requirements,generation}; returns a scaffold record."""
    scaffold = Path(rundir) / 'scaffold'
    project, generation = scaffold / 'project', scaffold / 'generation'
    project.mkdir(parents=True)
    root = root_sheet_uuid(prep['resolved'])
    template_pro = (FIXTURE_PROJECT / 'rc_filter.kicad_pro').read_bytes()
    pro = project_bytes(template_pro, root)
    board, dropped = empty_board((FIXTURE_PROJECT / 'rc_filter.kicad_pcb').read_text(), prep['envelope'])
    (project / f'{NAME}.kicad_pro').write_bytes(pro)
    (project / f'{NAME}.kicad_sch').write_bytes(empty_schematic(root))
    (project / f'{NAME}.kicad_pcb').write_bytes(board)
    for table in ('sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(FIXTURE_PROJECT / table, project / table)
    lib_table_check(project)
    shutil.copytree(prep['requirements'], scaffold / 'requirements')
    generation.mkdir()
    write_json(generation / 'intent.json', prep['intent'])
    (generation / 'resolved.json').write_bytes(prep['resolved_bytes'])
    shutil.copyfile(prep['parts_path'], generation / 'parts-manifest.json')
    catalog = prep['parts_path'].parent / 'catalog'
    shutil.copytree(catalog, generation / 'catalog')
    write_json(generation / 'envelope.json', prep['envelope'])
    write_json(generation / 'placement-request.json', prep['request'])
    # Immutable template for board_build (sibling .kicad_pro required); the writer cannot alter it.
    (generation / 'template').mkdir()
    (generation / 'template' / f'{NAME}.kicad_pcb').write_bytes(board)
    (generation / 'template' / f'{NAME}.kicad_pro').write_bytes(pro)
    files = {p.relative_to(scaffold).as_posix(): digest(p) for p in sorted(scaffold.rglob('*')) if p.is_file()}
    record = {'schema': SCAFFOLD_SCHEMA, 'qualification': QUALIFICATION, 'root_sheet_uuid': root,
              'template_project': {'path': str(FIXTURE_PROJECT), 'sha256': {
                  n: digest(FIXTURE_PROJECT / n) for n in ('rc_filter.kicad_pro', 'rc_filter.kicad_pcb')}},
              'board_items_dropped_from_template': dropped, 'files': files,
              'catalog': {k: prep['catalog'][k] for k in ('path', 'sha256')} | {
                  'source': prep['catalog'].get('source')}}
    write_json(Path(rundir) / 'scaffold.json', record)
    return scaffold, record


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
    template = json.loads(g.SCOPE_TEMPLATE.read_text())
    scope = g.compile_scope(template, params, NAME)
    base = Path(scope_dir)
    inputs = []
    for item in template['inputs']:
        if item['at'] == 'project':
            item = {'path': os.path.relpath(scaffold / 'project', base), 'at': 'project'}
        elif item['at'] == 'requirements':
            item = {'path': os.path.relpath(scaffold / 'requirements', base), 'at': 'requirements'}
        inputs.append(item)
    inputs.append({'path': os.path.relpath(scaffold / 'generation', base), 'at': 'generation'})
    scope['inputs'] = inputs
    scope['name'] = 'Rung-1 RC low-pass, staged repair campaign from an empty candidate'
    scope['scope'] = ('Review the rung-1 RC low-pass candidate against the requirements compiled from its '
                      'one-paragraph spec. The campaign starts from an empty native project (no symbols, '
                      'footprints or copper). After each implementation session the coordinator runs the '
                      'declared generation steps (schematic emitter, board build with row placement, '
                      'kicad-route KRT, fab export) on the delivered design and records stage receipts; '
                      'only fresh checks and reviews of the captured result decide acceptance. Immutable '
                      'generation inputs are under generation/. This is a disposable benchmark board.')
    by_id = {c['id']: c for c in scope['checks']}
    if set(by_id) != set(CHECK_STAGES):
        raise PrepareFailure(f'scope template checks {sorted(by_id)} differ from {sorted(CHECK_STAGES)}')
    scope['checks'] = [{**by_id[cid], 'stage': stage} for cid, stage in CHECK_STAGES.items()]
    scope['stages'] = {
        'schema': 'agent-ee.stages/1', 'order': list(STAGE_ORDER),
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
        {'schema': 'agent-ee.stage-step/1', 'stage': 'fab', 'step': 'export-fab',
         'argv': ['/usr/bin/python3', '-B', '-P', f'{MOUNT_STEPS}/fab.py', NAME],
         'mounts': [steps, source], 'source': MOUNT_SOURCE,
         'version_argv': ['/usr/bin/kicad-cli', 'version'],
         # validate_step requires a write; the fab step rewrites nothing, so it declares the
         # board in place and the receipt shows it unchanged.
         'writes': {board: f'design/{board}'},
         'artifacts': ['out/fab-manifest.json', 'out/fab', 'out/raw'],
         'adoptable_exits': [0], 'timeout_s': 600},
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


def campaign_config(scope_path, profile, steps, max_seconds=3600, max_iterations=3):
    adjudicator = profile['adjudicator']
    return {'schema': 'agent-ee.campaign/1', 'scope': Path(scope_path).name, 'review_profile': profile,
            'implementation': {'harness': adjudicator['harness'], 'model': adjudicator['model'],
                               'effort': adjudicator['effort'], 'timeout_s': 900},
            'max_iterations': max_iterations, 'max_seconds': max_seconds, 'implementation_steps': steps}


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
            'exports_exist': ok, 'board_sha256': manifest.get('board', {}).get('sha256'),
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


def write_record(rundir, base, *, verdict=None, fab=None, fab_bound=False):
    exists = bool(fab and fab['exports_exist'])
    done = verdict == 'criteria_satisfied' and exists and fab_bound
    record = {'schema': RECORD_SCHEMA, 'qualification': QUALIFICATION, **base, 'verdict': verdict,
              'fab_manifest': fab, 'fab_exports_exist': exists, 'fab_bound_to_accepted_candidate': fab_bound,
              'done': done,
              'done_rule': ('campaign verdict criteria_satisfied AND fab exports exist, are nonempty and match '
                            'their manifest AND the fab step consumed the accepted candidate\'s board and schematic')}
    write_json(Path(rundir) / 'rung1-campaign-record.json', record)
    return record


# ------------------------------------------------------------------ driver

def build(spec, rundir, *, profile_path, kicad_route, catalog_checkout=None, max_seconds=3600):
    rundir = Path(rundir).absolute()
    prep = prepare(spec, rundir, catalog_checkout)
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
                         'root_sheet_uuid': scaffold_record['root_sheet_uuid'], 'load_checks': loads},
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


def run(spec, rundir, *, profile_path, kicad_route, dry_run, catalog_checkout=None, max_seconds=3600):
    rundir = Path(rundir).absolute()
    if rundir.exists():
        raise FileExistsError(f'{rundir} exists; choose a fresh run directory')
    rundir.mkdir(parents=True)
    try:
        base, scope_path, config_path = build(spec, rundir, profile_path=profile_path, kicad_route=kicad_route,
                                              catalog_checkout=catalog_checkout, max_seconds=max_seconds)
    except (PrepareFailure, ValueError, OSError) as exc:
        return write_record(rundir, {'mode': 'dry_run' if dry_run else 'campaign',
                                     'error': f'{type(exc).__name__}: {exc}'})
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
        return write_record(rundir, base, fab=fab)
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
    if verify['exit_code'] != 0 and verdict == 'criteria_satisfied':
        verdict = 'unverified:' + verdict
    return write_record(rundir, base, verdict=verdict, fab=fab,
                        fab_bound=fab_final and verdict == 'criteria_satisfied')


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
                     dry_run=args.dry_run, max_seconds=args.max_seconds)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f'rung1_campaign refused: {exc}\n')
    print(json.dumps({k: record.get(k) for k in ('mode', 'error', 'verdict', 'fab_exports_exist',
                                                 'fab_bound_to_accepted_candidate', 'done')}))
    return 0 if record['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
