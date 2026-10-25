"""Resumable composition of the existing engineering and native producers.

Engineering decisions remain captured inputs. Every stage consumes the preceding
stage's real files; final acceptance comes from independently sealed checks.
"""
import copy
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from . import parts_stage, requirement_map, scaffold, schematic_stage, spec_record
from .catalog import strict_json
from .stage_receipts import _relative, adopt, file_tree, run_step, tree_identity
from .storage import digest, identity, read_json, write_json

SCHEMA = 'agent-ee.build-plan/1'
REPORT_SCHEMA = 'agent-ee.build-report/1'
STAGES = ('requirements', 'parts', 'scaffold', 'schematic', 'placement-routing', 'artwork', 'package')
GENERATED = 'build-generated/connectivity-intent.json'
RATINGS = 'build-generated/parts-ratings'
SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = SOURCE_ROOT.parent
MANDATORY = {'erc', 'drc', 'netlist', 'geometry', 'parts_subset', 'placement',
             'board_envelope', 'fab_profile', 'assembly_readiness', 'fab_package'}


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('build declaration fields differ from ' + ', '.join(required))


def validate_plan(plan):
    _keys(plan, ('schema', 'source', 'requirements', 'parts', 'envelope', 'placement'),
          ('verification_timeout_s',))
    if plan['schema'] != SCHEMA or not isinstance(plan['source'], str) or not plan['source']:
        raise ValueError('expected agent-ee.build-plan/1 with a source directory')
    _keys(plan['requirements'], ('map', 'record', 'spec'))
    _keys(plan['parts'], ('manifest', 'circuit', 'layout'), ('deviations', 'ratings'))
    _keys(plan['placement'], ('intent', 'groups', 'budget_s', 'search', 'router'), ('planes',))
    for path in [plan['envelope'], *plan['requirements'].values(), *plan['parts'].values(),
                 plan['placement']['intent'], plan['placement']['groups']]:
        _relative(path, 'build input')
    if 'planes' in plan['placement']:
        _relative(plan['placement']['planes'], 'plane plan')
    timeout = plan.get('verification_timeout_s', 3600)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('verification_timeout_s must be an integer from 1 to 86400')
    return plan


def _inventory(root):
    files, other = file_tree(root)
    if other:
        raise ValueError('build inputs contain links or special files: ' + str(other))
    return files


def _scope(scope, candidate):
    result = copy.deepcopy(scope)
    result['inputs'] = [dict(path=str(path), at=path.name) for path in sorted(candidate.iterdir())]
    return result


def _step(stage_dir, candidate, declaration, index):
    path = stage_dir / 'receipts' / str(index)
    receipt = run_step(path, candidate, declaration,
        dict(implementation=1, source_candidate_id=tree_identity(candidate)['sha256'], receipt_index=index))
    if receipt['status'] != 'completed':
        raise ValueError(f"{declaration['step']}: {receipt['status']}; see {path / 'run/console.log'}")
    adopt(path, receipt, candidate)
    return path, receipt


def _capture_ratings(path, receipt, candidate):
    """Carry the adopted producer's exact evidence into the next native candidate."""
    target = candidate / RATINGS
    target.mkdir()
    shutil.copyfile(path / 'receipt.json', target / 'receipt.json')
    shutil.copyfile(path / 'run/out/report.json', target / 'report.json')
    if (read_json(target / 'receipt.json') != receipt or
            digest(target / 'report.json') != receipt['artifacts'].get('out/report.json')):
        raise ValueError('parts rating evidence changed before capture')
    from .passive_ratings import load_provenance
    load_provenance(candidate, _rating_declaration(candidate))


def _rating_declaration(candidate):
    return dict(receipt=RATINGS + '/receipt.json', report=RATINGS + '/report.json',
                receipt_sha256=digest(candidate / RATINGS / 'receipt.json'))


def _rated_scope(plan, candidate, scope):
    """Derive only rating provenance; authored checks and requirements stay intact."""
    if 'ratings' not in plan['parts']:
        return scope
    scope = copy.deepcopy(scope)
    checks = [c for c in scope['checks'] if c['kind'] == 'assembly_readiness']
    if not checks:
        raise ValueError('parts ratings require an assembly_readiness check')
    for check in checks:
        params = check.setdefault('params', {})
        if 'rating_provenance' in params:
            raise ValueError('build derives rating_provenance from its adopted parts receipt')
        params['rating_provenance'] = _rating_declaration(candidate)
    return scope


def _requirements(plan, candidate, stage_dir):
    names = plan['requirements']
    raw = {key: (candidate / value).read_bytes() for key, value in names.items()}
    declaration = requirement_map.map_declaration(raw['map'])
    if declaration['spec_record_sha256'] != spec_record.sha256(raw['record']):
        raise ValueError('requirement map names different spec-record bytes')
    record = spec_record.verify(raw['record'], raw['spec'])
    provenance = candidate / requirement_map.PROVENANCE
    provenance.mkdir()
    for key, filename in [('map', 'requirement-map.json'), ('record', 'spec-record.json'), ('spec', 'spec.md')]:
        (provenance / filename).write_bytes(raw[key])
    scope, accounting = requirement_map.account_requirements(declaration, record, raw['spec'],
        files=_inventory(candidate), inputs=candidate)
    write_json(stage_dir / 'scope.json', _scope(scope, candidate))
    write_json(stage_dir / 'accounting.json', dict(requirements=accounting,
        coverage='pending native scaffold', acceptance=False))


def _compile_scaffold_requirements(plan, candidate, stage_dir):
    names = plan['requirements']
    files = {name: pin for name, pin in _inventory(candidate).items()
             if not name.startswith(requirement_map.PROVENANCE + '/')}
    scope, receipt = requirement_map.compile_map(
        (candidate / names['map']).read_bytes(), (candidate / names['record']).read_bytes(),
        (candidate / names['spec']).read_bytes(), files=files, inputs=candidate)
    write_json(stage_dir / 'requirements.json', receipt)
    scaffold_receipt = read_json(stage_dir / 'product/scaffold-receipt.json')
    produced = read_json(stage_dir / 'product/scope.json')
    from .baseline import compile_baseline
    from .rating_lineage import strip
    # The producer's receipt cannot exist in the original requirement map. As
    # with campaign rating lineage, remove only this derived parameter when
    # comparing against the authored baseline; retain it in the final baseline.
    authored = strip(produced) if 'ratings' in plan['parts'] else produced
    authored_baseline = compile_baseline(authored, _inventory(candidate), candidate)
    derived_baseline = compile_baseline(produced, _inventory(candidate), candidate)
    if (receipt['baseline_id'] != authored_baseline['baseline_id'] or
            derived_baseline['baseline_id'] != scaffold_receipt['baseline_id']):
        raise ValueError('requirements compiler and native scaffold disagree on the acceptance baseline')
    if 'ratings' in plan['parts']:
        expected = _rated_scope(plan, candidate, scope)
        if expected['checks'] != produced['checks']:
            raise ValueError('scaffold changed authored checks or adopted parts rating provenance')
        write_json(stage_dir / 'rating-binding.json', dict(
            provenance=_rating_declaration(candidate), authored_baseline_id=receipt['baseline_id'],
            derived_baseline_id=derived_baseline['baseline_id']))
    write_json(stage_dir / 'scope.json', _scope(produced, candidate))
    return produced


def _manufacturing_coverage(plan, scope, accounting):
    from .baseline import generation_marker
    missing = MANDATORY - {check['kind'] for check in scope['checks']}
    if missing:
        raise ValueError('manufacturing coverage missing: ' + ', '.join(sorted(missing)))
    unresolved = [r['id'] for r in accounting if r['mode'] == 'limitation']
    if unresolved:
        raise ValueError('required engineering coverage remains unresolved: ' + ', '.join(unresolved))
    if generation_marker(scope) is None:
        raise ValueError('manufacturing build requires the ordinary generation done gate')
    if 'envelope' not in scope:
        raise ValueError('manufacturing build requires captured envelope acceptance')
    if scope['envelope']['path'] != plan['envelope'] or scope['envelope']['profile'] is None:
        raise ValueError('manufacturing checks must bind the generated envelope and its selected profile')
    memberships = [c for c in scope['checks'] if c['kind'] == 'parts_subset']
    if not any(c['params']['manifest'] == plan['parts']['manifest'] for c in memberships):
        raise ValueError('native membership checks must bind the produced parts manifest')
    if 'planes' in plan['placement']:
        bound = [link['path'] for c in scope['checks'] if c['kind'] == 'drc'
                 for link in c.get('params', {}).get('plane_plans', [])]
        bound += [link['plan']['path'] for c in scope['checks'] if c['kind'] == 'drc'
                  for link in c.get('params', {}).get('plane_obligations', [])]
        if plan['placement']['planes'] not in bound:
            raise ValueError('manufacturing plane checks must bind the generated plane plan')


def _review_candidate(scope, directory, timeout):
    """Use the generated acceptance scope against this actual native candidate."""
    from .cli import run_review, verify
    path = directory / 'scope.json'
    write_json(path, scope)
    run_review(path, directory / 'review', timeout, checks_only=True, output_stream=sys.stderr)
    return verify(directory / 'review')


def _package(plan, candidate, stage_dir, scope, accounting):
    from .baseline import compile_baseline
    _manufacturing_coverage(plan, scope, accounting)
    result = _review_candidate(_scope(scope, candidate), stage_dir, plan.get('verification_timeout_s', 3600))
    baseline = compile_baseline(scope, _inventory(candidate), candidate)
    required = {cid for row in baseline['coverage']['requirements'].values() for cid in row['acceptance_checks']}
    checks = {check['id']: check for check in result['checks']}
    failures = [cid for cid in required if cid not in checks or
                (checks[cid]['execution'], checks[cid]['outcome']) != ('ok', 'pass')]
    failures += [cid for cid, check in checks.items() if check['execution'] != 'ok' or
                 check['outcome'] not in ('pass', 'info')]
    if failures:
        raise ValueError('required final checks did not pass: ' + ', '.join(sorted(set(failures))))
    packages = [checks[c['id']] for c in scope['checks'] if c['kind'] == 'fab_package']
    if len(packages) != 1 or packages[0]['outcome'] != 'pass':
        raise ValueError('exactly one passing final manufacturing package is required')
    artifacts = packages[0].get('details', {}).get('artifacts')
    if not artifacts or artifacts['board_sha256'] != digest(candidate / scope['board']):
        raise ValueError('manufacturing package is not bound to the final native board')
    write_json(stage_dir / 'accepted-package.json', dict(artifacts=artifacts,
        native_files={name: pin for name, pin in _inventory(candidate).items()
                      if name.endswith(('.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_dru'))},
        physical_obligations=[r for r in accounting if r['mode'] == 'bench'],
        physical_validation_performed=False, supplier_preview_performed=False))


def _artwork(candidate, directory, board):
    """Finish visible field positions using the existing bounded native producer."""
    import pcbnew as k
    from . import routed_artwork
    from .cli import policy_files
    from .silk_artwork import DECLARATION, movable_names
    native = k.LoadBoard(str(candidate / board))
    targets = []
    for footprint in native.GetFootprints():
        fields = list(footprint.GetFields())
        declaration = next((field.GetText() for field in fields if field.GetName() == DECLARATION), None)
        movable = movable_names(declaration)
        targets.extend((footprint.GetReference(), field.GetName()) for field in fields
                       if field.GetName() in movable and field.IsVisible()
                       and field.GetLayer() in (k.F_SilkS, k.B_SilkS))
    report = dict(board=board, source_sha256=digest(candidate / board),
                  targets=sorted(targets), acceptance_ready=False)
    if not targets:
        # An omitted target list would enable graphic translations. Leave them
        # intact; the final native release gate still rejects their conflicts.
        write_json(directory / 'artwork.json', dict(report, status='no_movable_fields'))
        return
    declaration = routed_artwork.step(directory / 'tool', board, targets=targets)
    path, receipt = _step(directory, candidate, declaration, 0)
    proof = routed_artwork.qualify_receipt(path, receipt, board, policy_files())
    produced = read_json(path / 'run' / routed_artwork.REPORT)
    conflicts = produced['open_defects']
    write_json(directory / 'artwork.json', dict(report, board_sha256=digest(candidate / board),
        status='unresolved' if conflicts else 'repaired', write_scope=proof, open_defects=conflicts,
        receipt=str(path.relative_to(directory)), receipt_sha256=digest(path / 'receipt.json')))
    if conflicts:
        raise ValueError(f'artwork retains {len(conflicts)} native silkscreen conflict(s); see {directory / "artwork.json"}')


def _execute(stage, plan, previous, directory, completed, out, *, diagnostic=False):
    requirements_dir = out / completed[0]['directory'] if completed else None
    scope = read_json(requirements_dir / 'scope.json') if requirements_dir is not None else None
    scaffold_row = next((row for row in completed if row['stage'] == 'scaffold'), None)
    if scaffold_row is not None:
        scope = read_json(out / scaffold_row['directory'] / 'product/scope.json')
    if stage == 'scaffold':
        planning = directory / 'planning.scope.json'
        write_json(planning, _scope(_rated_scope(plan, previous, scope), previous))
        scaffold.scaffold(previous / plan['envelope'], previous / plan['parts']['manifest'], directory / 'product',
            scope_path=planning, intent_path=previous / GENERATED, layout_path=previous / plan['parts']['layout'])
        candidate = directory / 'product/inputs'
        _compile_scaffold_requirements(plan, candidate, directory)
        return candidate
    candidate = directory / 'candidate'
    shutil.copytree(previous, candidate)
    if stage == 'requirements':
        _requirements(plan, candidate, directory)
    elif stage == 'parts':
        target = candidate / GENERATED
        target.parent.mkdir()
        target.write_bytes(b'{}\n')
        path, receipt = _step(directory, candidate, parts_stage.configuration(SOURCE_ROOT, bundle='.',
            **plan['parts'], output=GENERATED), 0)
        if 'ratings' in plan['parts']:
            _capture_ratings(path, receipt, candidate)
    elif stage == 'schematic':
        scaffold_dir = out / next(row['directory'] for row in completed if row['stage'] == 'scaffold')
        receipt = read_json(scaffold_dir / 'product/scaffold-receipt.json')
        sheets = list(receipt['sheets'].values())
        native = schematic_stage.configuration(SOURCE_ROOT, board=scope['board'], schematic=scope['schematic'],
            child_schematics=[path for path in sheets if path != scope['schematic']], intent=GENERATED,
            layout=plan['parts']['layout'], captures='.',
            footprints=read_json(candidate / plan['parts']['manifest'])['cad']['manifest']['path'])
        content = scaffold.content_step(REPOSITORY, plan['envelope'], plan['parts']['manifest'], scope['board'],
            intent=GENERATED, layout=plan['parts']['layout'])
        for index, step in enumerate([content, *native['implementation_steps']]):
            _step(directory, candidate, step, index)
    elif stage == 'placement-routing':
        from . import placement_job
        accounting = read_json(requirements_dir / 'accounting.json')['requirements']
        try:
            _manufacturing_coverage(plan, scope, accounting)
        except ValueError as exc:
            if not diagnostic:
                raise
            # Final copper can be an input to unfinished electrical coverage.
            # Only an explicit checkpoint may proceed; _package checks again.
            coverage = dict(status='incomplete', reason=str(exc))
        else:
            coverage = dict(status='declared')
        write_json(directory / 'manufacturing-coverage.json', coverage)
        placement = plan['placement']
        config = dict(schema=placement_job.CONFIG_SCHEMA,
            **{key: placement[key] for key in ('budget_s', 'search', 'router')}, inputs={})
        for key, name in dict(board=scope['board'], project=str(Path(scope['board']).with_suffix('.kicad_pro')),
                              intent=placement['intent'], groups=placement['groups']).items():
            config['inputs'][key] = dict(path=str(candidate / name), sha256=digest(candidate / name))
        rules = (candidate / scope['board']).with_suffix('.kicad_dru')
        if rules.exists():
            config['inputs']['rules'] = dict(path=str(rules), sha256=digest(rules))
        if 'planes' in placement:
            path = candidate / placement['planes']
            config['inputs']['planes'] = dict(path=str(path), sha256=digest(path))
        locality = {c['params']['locality'] for c in scope['checks'] if c['kind'] == 'placement'
                    and c['params'].get('criterion') == 'electrical-locality/1'}
        if len(locality) > 1:
            raise ValueError('placement build requires one shared electrical locality document')
        if locality:
            path = candidate / locality.pop()
            config['inputs']['locality'] = dict(path=str(path), sha256=digest(path))
        write_json(directory / 'placement-config.json', config)
        report = placement_job.run_job(config, directory / 'placement')
        if report['status'] != 'completed' or report.get('final', {}).get('eligibility') != 'eligible':
            raise ValueError('placement/routing did not pass: ' + report['status'])
        native = Path(report['final']['board'])
        if digest(native) != report['final']['board_sha256']:
            raise ValueError('routed native board changed before adoption')
        shutil.copyfile(native, candidate / scope['board'])
    elif stage == 'artwork':
        _artwork(candidate, directory, scope['board'])
    elif stage == 'package':
        _package(plan, candidate, directory, scope, read_json(requirements_dir / 'accounting.json')['requirements'])
    return candidate


def _runtime():
    from .cli import pcbnew_extension, policy_files
    import pcbnew
    cli = shutil.which('kicad-cli')
    if cli is None:
        raise ValueError('kicad-cli is unavailable')
    version = subprocess.run([cli, 'version'], capture_output=True, text=True, timeout=30, check=True)
    extension, diagnostic = pcbnew_extension()
    if extension is None:
        raise ValueError('pcbnew native extension unavailable: ' + str(diagnostic))
    return dict(source=policy_files(), python=digest(sys.executable), kicad_cli=digest(cli),
                kicad_version=version.stdout.strip(), pcbnew_version=pcbnew.Version(), pcbnew_sha256=digest(extension))


def _retained_path(root, relative):
    path = root / _relative(relative, 'retained build path')
    if path == root or not path.resolve().is_relative_to(root):
        raise ValueError('retained build path escapes its run')
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError('retained build path contains a link')
    return path


def _review_source(run):
    """Only receipted completed stages may supply a diagnostic candidate."""
    path = _retained_path(run, 'build-report.json')
    report = strict_json(path.read_bytes())
    if not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA:
        raise ValueError('expected a retained agent-ee build report')
    inputs = report.get('inputs')
    _keys(inputs, ('plan_sha256', 'source', 'runtime'))
    if report.get('identity') != identity(inputs) or _inventory(_retained_path(run, 'source')) != inputs['source']:
        raise ValueError('retained build source identity changed')
    completed = report.get('completed')
    if not isinstance(completed, list) or not 4 <= len(completed) <= len(STAGES):
        raise ValueError('build review requires completed native schematic generation')
    for index, row in enumerate(completed):
        _keys(row, ('stage', 'directory', 'candidate', 'tree'))
        directory = _retained_path(run, row['directory'])
        candidate = _retained_path(run, row['candidate'])
        if candidate == directory or not candidate.is_relative_to(directory):
            raise ValueError('build candidate is outside its completed stage')
        if row['stage'] != STAGES[index] or tree_identity(directory) != row['tree']:
            raise ValueError('completed build stage identity changed: ' + str(row['stage']))
    if report.get('candidate') != completed[-1]['candidate']:
        raise ValueError('build candidate differs from its last completed stage')
    candidate = _retained_path(run, completed[-1]['candidate'])
    scope_path = _retained_path(run, completed[2]['directory']) / 'product/scope.json'
    scope = strict_json(scope_path.read_bytes())
    selection = dict(schema='agent-ee.build-review-source/1', build_run=str(run),
        build_report_sha256=digest(path), build_identity=report['identity'],
        build_runtime=inputs['runtime'], selected=completed[-1],
        scope_sha256=digest(scope_path), scaffold=completed[2])
    return candidate, scope, selection


def review_build(run, out, *, max_seconds=900):
    """Fresh checks without adopting a candidate or accepting a package."""
    run, out = Path(run).resolve(), Path(out).resolve()
    if out == run or run in out.parents:
        raise ValueError('build review output must be outside the retained build')
    if type(max_seconds) is not int or not 1 <= max_seconds <= 86400:
        raise ValueError('max-seconds must be an integer from 1 to 86400')
    # Reuse the build writer's exact lease; do not create a new lock for a
    # missing or incomplete run. The review itself owns its ordinary lease.
    with _retained_path(run, '.lock').open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another writer owns the build') from exc
        candidate, scope, selection = _review_source(run)
        if (candidate / 'build-review').exists():
            raise ValueError('candidate occupies reserved build-review provenance namespace')
        out.mkdir(parents=True, exist_ok=False)
        # The source selection is an immutable captured input to the ordinary
        # review. It adds provenance, never a passing acceptance predicate.
        source = out / 'source.json'
        write_json(source, selection)
        bound_scope = _scope(scope, candidate)
        bound_scope['inputs'].append(dict(path=str(source), at='build-review/source.json'))
        result = _review_candidate(bound_scope, out, max_seconds)
        if _review_source(run)[2] != selection:
            raise ValueError('retained build changed during diagnostic verification')
        summary = dict(schema='agent-ee.build-review/1', accepted=False,
            outcome=result['outcome'], candidate_id=result['candidate_id'],
            source=selection, review=str(out / 'review'),
            checks=[{key: row[key] for key in ('id', 'execution', 'outcome')} for row in result['checks']],
            limitations=result['limitations'])
        write_json(out / 'report.json', summary)
        return summary


def run(plan_path, out, *, resume=False, through='package'):
    if through not in STAGES:
        raise ValueError('unknown build stage')
    plan_path, out = Path(plan_path).resolve(), Path(out).resolve()
    plan = validate_plan(strict_json(plan_path.read_bytes()))
    source = (plan_path.parent / plan['source']).resolve()
    if out == source or source in out.parents:
        raise ValueError('build output must be outside the source bundle')
    source_files = _inventory(source)
    from .placement_job import validate_intent
    validate_intent(strict_json((source / plan['placement']['intent']).read_bytes()),
                    strict_json((source / plan['placement']['groups']).read_bytes()))
    for reserved in ('build-generated', requirement_map.PROVENANCE):
        if any(path == reserved or path.startswith(reserved + '/') for path in source_files):
            raise ValueError('build source occupies generated namespace: ' + reserved)
    pin = dict(plan_sha256=digest(plan_path), source=source_files, runtime=_runtime())
    binding = identity(pin)
    if resume:
        report = read_json(out / 'build-report.json')
        if report.get('identity') != binding or report.get('schema') != REPORT_SCHEMA:
            raise ValueError('build resume identity differs from captured inputs or runtime')
    else:
        out.mkdir(parents=True, exist_ok=False)
        report = dict(schema=REPORT_SCHEMA, identity=binding, inputs=pin, status='running', accepted=False,
                      completed=[], attempts=[], candidate=None)
        write_json(out / 'build-report.json', report)
    with (out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            report = read_json(out / 'build-report.json')
            if report['identity'] != binding:
                raise ValueError('build resume identity changed while acquiring the lock')
        if not (out / 'source').exists():
            capturing = out / ('.capture-' + uuid.uuid4().hex)
            shutil.copytree(source, capturing)
            if _inventory(capturing) != source_files:
                raise ValueError('build input changed during capture')
            capturing.rename(out / 'source')
        if _inventory(out / 'source') != source_files:
            raise ValueError('retained build source identity changed')
        for index, row in enumerate(report['completed']):
            if row['stage'] != STAGES[index] or tree_identity(out / row['directory']) != row['tree']:
                raise ValueError('completed build stage identity changed: ' + row['stage'])
        if report['accepted']:
            from .cli import verify
            verify(out / report['completed'][-1]['directory'] / 'review')
            return report
        previous = out / report['candidate'] if report['candidate'] else out / 'source'
        try:
            for stage in STAGES[len(report['completed']):STAGES.index(through) + 1]:
                if (out / 'CANCEL').exists():
                    raise KeyboardInterrupt('build CANCEL requested')
                index = len(report['completed'])
                directory = out / 'stages' / f'{index + 1:02d}-{stage}-{uuid.uuid4().hex}'
                directory.mkdir(parents=True)
                report['attempts'].append(dict(stage=stage, directory=str(directory.relative_to(out)), status='running'))
                write_json(out / 'build-report.json', report)
                before = tree_identity(previous)
                candidate = _execute(stage, plan, previous, directory, report['completed'], out,
                                     diagnostic=through != 'package')
                if tree_identity(previous) != before or _inventory(source) != source_files or _runtime() != pin['runtime']:
                    raise ValueError('build input or runtime changed during stage execution')
                report['candidate'] = str(candidate.relative_to(out))
                report['completed'].append(dict(stage=stage, directory=str(directory.relative_to(out)),
                    candidate=report['candidate'], tree=tree_identity(directory)))
                report['attempts'][-1]['status'] = 'completed'
                previous = candidate
                write_json(out / 'build-report.json', report)
            report['accepted'] = len(report['completed']) == len(STAGES)
            report['status'] = 'accepted' if report['accepted'] else 'checkpoint'
            report.pop('reason', None)
        except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            report['reason'] = f'{type(exc).__name__}: {exc}'
            if report['attempts'] and report['attempts'][-1]['status'] == 'running':
                report['attempts'][-1].update(status=report['status'], reason=report['reason'])
        write_json(out / 'build-report.json', report)
        return report


def configure_parser(parser):
    sub = parser.add_subparsers(dest='build_command', required=True)
    command = sub.add_parser('run', help='run or resume the captured engineering build')
    command.add_argument('plan', type=Path)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--resume', action='store_true')
    command.add_argument('--through', choices=STAGES, default='package', help='diagnostic checkpoint; never acceptance')
    command = sub.add_parser('review', help='run fresh native checks on the last completed build candidate')
    command.add_argument('run', type=Path)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--max-seconds', type=int, default=900)


def execute(args):
    if args.build_command == 'review':
        report = review_build(args.run, args.out, max_seconds=args.max_seconds)
        print(json.dumps({key: report[key] for key in ('accepted', 'outcome', 'candidate_id', 'review')}, indent=2))
        return 0 if report['outcome'] == 'criteria_satisfied' else 1
    report = run(args.plan, args.out, resume=args.resume, through=args.through)
    print(json.dumps({key: report[key] for key in ('status', 'accepted', 'candidate')} |
                     {'reason': report.get('reason')}, indent=2))
    return 0 if report['status'] in ('accepted', 'checkpoint') else 1
