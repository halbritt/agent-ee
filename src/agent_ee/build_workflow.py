"""Resumable composition of the existing engineering and native producers.

Engineering decisions remain captured inputs. Every stage consumes the preceding
stage's real files; final acceptance comes from independently sealed checks.
"""
import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

from . import parts_stage, requirement_map, scaffold, schematic_stage, spec_record
from .catalog import strict_json
from .stage_receipts import _relative, adopt, run_step, tree_identity
from .storage import digest, digest_fd, identity, publish, read_json, write_json

SCHEMA = 'agent-ee.build-plan/1'
TYPED_SCHEMA = 'agent-ee.build-plan/2'
REPORT_SCHEMA = 'agent-ee.build-report/1'
QUALIFICATION_SCHEMA = 'agent-ee.qualification/1'
CLAIM = ('software file obligations only; delivery, judgment, supplier and physical '
         'obligations are listed under coverage with their own status')
DIMENSIONS = ('checks', 'file_release', 'operational', 'engineering_review', 'judgments', 'supplier', 'physical')
WITNESS_CHECKS = ('source_citations_resolved', 'applicability_recomputed', 'conclusion_consistent_with_bound_checks')
STAGES = ('requirements', 'parts', 'scaffold', 'schematic', 'placement-routing', 'artwork', 'package')
GENERATED = 'build-generated/connectivity-intent.json'
RATINGS = 'build-generated/parts-ratings'
SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = SOURCE_ROOT.parent
MANDATORY = {'erc', 'drc', 'netlist', 'geometry', 'parts_subset', 'placement',
             'board_envelope', 'fab_profile', 'assembly_readiness', 'fab_package'}


class QualificationError(ValueError):
    """A current claim cannot be reconstructed from the retained evidence."""
    def __init__(self, message, *, integrity=False):
        self.integrity = integrity
        super().__init__(message)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('build declaration fields differ from ' + ', '.join(required))


def validate_plan(plan):
    _keys(plan, ('schema', 'source', 'requirements', 'parts', 'envelope', 'placement'),
          ('verification_timeout_s', 'judgments'))
    if plan['schema'] not in (SCHEMA, TYPED_SCHEMA) or not isinstance(plan['source'], str) or not plan['source']:
        raise ValueError('expected agent-ee.build-plan/1 or /2 with a source directory')
    typed = plan['schema'] == TYPED_SCHEMA
    _keys(plan['requirements'], ('map', 'record', 'spec', *(['obligations'] if typed else [])))
    if 'judgments' in plan:
        if not typed:
            raise ValueError('judgments require a typed build-plan/2')
        _relative(plan['judgments'], 'judgments directory')
        if plan['judgments'] == '.' or any(
                plan['judgments'] == reserved or plan['judgments'].startswith(reserved + '/')
                or reserved.startswith(plan['judgments'] + '/') for reserved in ('build-generated', requirement_map.PROVENANCE)):
            raise ValueError('judgments directory overlaps the source root or generated namespace')
    _keys(plan['parts'], ('manifest', 'circuit', 'layout'), ('deviations', 'ratings'))
    _keys(plan['placement'], ('intent', 'groups', 'budget_s', 'search', 'router'), ('planes',))
    paths = [plan['envelope'], *plan['requirements'].values(), *plan['parts'].values(),
             plan['placement']['intent'], plan['placement']['groups']]
    if 'planes' in plan['placement']:
        paths.append(plan['placement']['planes'])
    for path in paths:
        _relative(path, 'build input')
        if 'judgments' in plan and (path == plan['judgments'] or path.startswith(plan['judgments'] + '/')):
            raise ValueError('pre-package build input occupies the judgments directory')
    timeout = plan.get('verification_timeout_s', 3600)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('verification_timeout_s must be an integer from 1 to 86400')
    return plan


_inventory = requirement_map.input_inventory


def _source_inventory(root, plan):
    prefix = plan.get('judgments')
    if prefix is None:
        return _inventory(root)
    root = Path(root)
    files = {}
    def walk_error(exc):
        raise exc
    # Prune before opening any late-bound file. Hashing and then filtering would
    # still read editable testimony during pre-package stages.
    for current, directories, names, descriptor in os.fwalk(root, follow_symlinks=False, onerror=walk_error):
        relative = Path(current).relative_to(root)
        if len(relative.parts) > 128:
            raise ValueError('build input directory nesting exceeds 128 levels')
        for name in list(directories):
            mode = os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode
            if not stat.S_ISDIR(mode):
                raise ValueError('build inputs contain links or special files')
            if str(relative / name) == prefix:
                directories.remove(name)
        for name in names:
            path = str(relative / name)
            if path == prefix:
                raise ValueError('judgments directory must be a directory')
            if not stat.S_ISREG(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                raise ValueError('build inputs contain links or special files')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('build inputs contain links or special files')
                files[path] = digest_fd(os.dup(fd))
            finally:
                os.close(fd)
    return dict(sorted(files.items()))


def _capture_source(source, capturing, plan):
    prefix = plan.get('judgments')
    omitted = source / prefix if prefix else None
    def ignore(directory, names):
        return {omitted.name} if omitted is not None and Path(directory) == omitted.parent else set()
    shutil.copytree(source, capturing, ignore=ignore)


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
    options = {}
    if 'obligations' in raw:
        options['obligations'] = requirement_map.load_obligations(raw['obligations'], record, raw['spec'])
    scope, accounting = requirement_map.account_requirements(declaration, record, raw['spec'],
        files=_inventory(candidate), inputs=candidate, **options)
    if 'obligations' in raw:
        (provenance / 'obligations.json').write_bytes(raw['obligations'])
    write_json(stage_dir / 'scope.json', _scope(scope, candidate))
    write_json(stage_dir / 'accounting.json', dict(requirements=accounting,
        coverage='pending native scaffold', acceptance=False))


def _compile_scaffold_requirements(plan, candidate, stage_dir):
    names = plan['requirements']
    files = {name: pin for name, pin in _inventory(candidate).items()
             if not name.startswith(requirement_map.PROVENANCE + '/')}
    scope, receipt = requirement_map.compile_map(
        (candidate / names['map']).read_bytes(), (candidate / names['record']).read_bytes(),
        (candidate / names['spec']).read_bytes(), files=files, inputs=candidate,
        **({'obligations_raw': (candidate / names['obligations']).read_bytes()} if 'obligations' in names else {}))
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
    _manufacturing_coverage(plan, scope, accounting)
    result = _review_candidate(_scope(scope, candidate), stage_dir, plan.get('verification_timeout_s', 3600))
    artifacts = package_artifacts(result, scope, candidate)
    extra = {}
    if plan['schema'] == TYPED_SCHEMA:
        extra['judgment_inventory_sha256'] = digest(stage_dir / 'judgment-inventory.json')
    write_json(stage_dir / 'accepted-package.json', dict(artifacts=artifacts,
        native_files={name: pin for name, pin in _inventory(candidate).items()
                      if name.endswith(('.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_dru'))},
        physical_obligations=[r for r in accounting if r['mode'] == 'bench'],
        physical_validation_performed=False, supplier_preview_performed=False, **extra))


def package_artifacts(result, scope, candidate):
    """Replay the existing file-package predicate from verified check results."""
    from .baseline import compile_baseline
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
    return artifacts


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
    plan_path = _retained_path(run, 'build-plan.json')
    if plan_path.exists():
        if digest(plan_path) != inputs['plan_sha256']:
            raise ValueError('captured build plan identity changed')
        selection['qualification_mode'] = 'typed' if strict_json(plan_path.read_bytes())['schema'] == TYPED_SCHEMA else 'legacy'
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
        qualified = assess_review(out, result, selection)
        summary['qualification'] = dict(path='qualification.json', sha256=digest(out / 'qualification.json'),
            mode=qualified['mode'], dimensions={name: row['status'] for name, row in qualified['dimensions'].items()})
        write_json(out / 'report.json', summary)
        return summary


def preflight_build(plan_path, out):
    """Validate plan schema, pruned source inventory, placement intent, and map binding before any stage runs."""
    plan_path, out = Path(plan_path).resolve(), Path(out).resolve()
    plan_raw = plan_path.read_bytes()
    plan = validate_plan(strict_json(plan_raw))
    source = (plan_path.parent / plan['source']).resolve()
    if out == source or source in out.parents:
        raise ValueError('build output must be outside the source bundle')
    source_files = _source_inventory(source, plan)
    # Preserve intent preflight before map-schema validation and output creation.
    # The source inventory above has already read the source files.
    from .placement_job import normalize_intent
    normalize_intent(strict_json((source / plan['placement']['intent']).read_bytes()),
                     strict_json((source / plan['placement']['groups']).read_bytes()))
    map_schema = strict_json((source / plan['requirements']['map']).read_bytes()).get('schema')
    if (map_schema == requirement_map.SCHEMA_2) != (plan['schema'] == TYPED_SCHEMA):
        raise ValueError('typed build-plan/2 requires map/2 and obligations; legacy map/1 cannot mix')
    for reserved in ('build-generated', requirement_map.PROVENANCE):
        if any(path == reserved or path.startswith(reserved + '/') for path in source_files):
            raise ValueError('build source occupies generated namespace: ' + reserved)
    return plan_path, out, plan_raw, plan, source, source_files


def run(plan_path, out, *, resume=False, through='package'):
    if through not in STAGES:
        raise ValueError('unknown build stage')
    plan_path, out, plan_raw, plan, source, source_files = preflight_build(plan_path, out)
    pin = dict(plan_sha256=hashlib.sha256(plan_raw).hexdigest(), source=source_files, runtime=_runtime())
    binding = identity(pin)
    if resume:
        report = read_json(out / 'build-report.json')
        if report.get('schema') != REPORT_SCHEMA:
            raise ValueError('expected retained build report')
    else:
        out.mkdir(parents=True, exist_ok=False)
        report = dict(schema=REPORT_SCHEMA, identity=binding, inputs=pin, status='running', accepted=False,
                      completed=[], attempts=[], candidate=None)
        write_json(out / 'build-report.json', report)
    with (out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            report = read_json(out / 'build-report.json')
            try:
                retained_plan = verify_retained(out)
            except QualificationError as exc:
                _invalidate_retained(out, report, str(exc), plan)
            if retained_plan is None:
                _refuse_resume(out, report, 'historical build has no captured plan; resume migration is unsupported',
                               pin, kind='historical_migration')
            if report['identity'] != binding:
                _refuse_resume(out, report, 'build resume identity differs from captured inputs or runtime', pin)
        captured_plan = out / 'build-plan.json'
        if not captured_plan.exists():
            captured_plan.write_bytes(plan_raw)
        if digest(captured_plan) != pin['plan_sha256']:
            _invalidate_retained(out, report, 'captured build plan identity changed', plan)
        if not (out / 'source').exists():
            capturing = out / ('.capture-' + uuid.uuid4().hex)
            _capture_source(source, capturing, plan)
            if _inventory(capturing) != source_files:
                raise ValueError('build input changed during capture')
            capturing.rename(out / 'source')
        if _inventory(out / 'source') != source_files:
            _invalidate_retained(out, report, 'retained build source identity changed', plan)
        for index, row in enumerate(report['completed']):
            if row['stage'] != STAGES[index] or tree_identity(out / row['directory']) != row['tree']:
                _invalidate_retained(out, report, 'completed build stage identity changed: ' + row['stage'], plan)
        package = next((row for row in report['completed'] if row['stage'] == 'package'), None)
        if 'judgment_inventory' in report:
            frozen = report['judgment_inventory']
            path = _retained_path(out, frozen['path'])
            if not path.is_file() or digest(path) != frozen['sha256']:
                _invalidate_retained(out, report, 'judgment inventory changed after freeze', plan)
            try:
                verify_judgment_source(source, plan, path.parent)
            except ValueError as exc:
                _refuse_resume(out, report, str(exc), pin, kind='judgment_inputs',
                               judgments=identity(judgment_inventory(source, plan)))
        if package is not None and plan['schema'] == TYPED_SCHEMA:
            try:
                verify_judgment_source(source, plan, out / package['directory'])
            except ValueError as exc:
                _refuse_resume(out, report, str(exc), pin, kind='judgment_inputs',
                               judgments=identity(judgment_inventory(source, plan)))
        if report['accepted']:
            from .cli import verify
            try:
                verify(out / report['completed'][-1]['directory'] / 'review')
            except (ValueError, OSError) as exc:
                # Completed-tree custody was verified independently above. This
                # verifier also gates current tools/rules; its refusal does not
                # establish that historical evidence bytes were corrupted.
                _refuse_resume(out, report, str(exc), pin, kind='verification_unavailable')
            return _assess_report(out, report, typed=plan['schema'] == TYPED_SCHEMA)
        previous = out / report['candidate'] if report['candidate'] else out / 'source'
        try:
            for stage in STAGES[len(report['completed']):STAGES.index(through) + 1]:
                if (out / 'CANCEL').exists():
                    raise KeyboardInterrupt('build CANCEL requested')
                index = len(report['completed'])
                directory = out / 'stages' / f'{index + 1:02d}-{stage}-{uuid.uuid4().hex}'
                directory.mkdir(parents=True)
                report['attempts'].append(dict(stage=stage, directory=str(directory.relative_to(out)),
                                              status='running', identity=binding))
                write_json(out / 'build-report.json', report)
                before = tree_identity(previous)
                if stage == 'package' and plan['schema'] == TYPED_SCHEMA:
                    freeze_judgments(source, previous, plan, directory)
                    path = directory / 'judgment-inventory.json'
                    report['judgment_inventory'] = dict(path=str(path.relative_to(out)), sha256=digest(path))
                    write_json(out / 'build-report.json', report)
                candidate = _execute(stage, plan, previous, directory, report['completed'], out,
                                     diagnostic=through != 'package')
                if (tree_identity(previous) != before or _source_inventory(source, plan) != source_files
                        or digest(plan_path) != pin['plan_sha256'] or _runtime() != pin['runtime']):
                    raise ValueError('build input or runtime changed during stage execution')
                if stage == 'package' and plan['schema'] == TYPED_SCHEMA:
                    verify_judgment_source(source, plan, directory)
                report['candidate'] = str(candidate.relative_to(out))
                report['completed'].append(dict(stage=stage, directory=str(directory.relative_to(out)),
                    candidate=report['candidate'], tree=tree_identity(directory)))
                report['attempts'][-1]['status'] = 'completed'
                previous = candidate
                write_json(out / 'build-report.json', report)
            report['accepted'] = False
            report['status'] = 'assessing' if len(report['completed']) == len(STAGES) else 'checkpoint'
            report.pop('reason', None)
        except (ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            report['reason'] = f'{type(exc).__name__}: {exc}'
            if report['attempts'] and report['attempts'][-1]['status'] == 'running':
                report['attempts'][-1].update(status=report['status'], reason=report['reason'])
        return _assess_report(out, report, typed=plan['schema'] == TYPED_SCHEMA)


def _assess_report(out, report, *, typed=False):
    if report['accepted']:
        report['supersession'] = _archive_records(out, 'Reassessment of a previously accepted run')
    report['accepted'] = False
    report.pop('qualification', None)
    write_json(out / 'build-report.json', report)
    try:
        record = assess(out)
    except QualificationError as exc:
        report.update(status='failed', accepted=False, reason='QualificationError: ' + str(exc))
        record = failed_assessment(out, report, str(exc),
            typed=typed, integrity=exc.integrity)
        report['qualification'] = dict(path='qualification.json', sha256=digest(out / 'qualification.json'),
            mode=record['mode'], dimensions={name: row['status'] for name, row in record['dimensions'].items()})
    else:
        report['qualification'] = dict(path='qualification.json', sha256=digest(out / 'qualification.json'),
            mode=record['mode'], dimensions={name: row['status'] for name, row in record['dimensions'].items()})
        report['accepted'] = record['accepted']
        if len(report['completed']) == len(STAGES):
            pending = record['dimensions']['file_release']['status'] == 'pending'
            report['status'] = 'accepted' if report['accepted'] else 'pending' if pending else 'failed'
            if not report['accepted']:
                report['reason'] = ('Required file-release evidence is pending' if pending
                                    else 'QualificationError: required file-release evidence failed')
    write_json(out / 'build-report.json', report)
    return report


def _refuse_resume(out, report, reason, pin, *, kind='invocation_identity', judgments=None):
    """Record a refused invocation without changing the build it tried to resume."""
    directory = _retained_path(out, 'resume-refusals')
    directory.mkdir(exist_ok=True)
    retained = {name: digest(_retained_path(out, name)) for name in ('build-report.json', 'qualification.json')
                if (out / name).exists()}
    receipt = dict(schema='agent-ee.resume-refusal/1', kind=kind, reason=reason,
        attempted_identity=identity(pin) if judgments is None else identity(dict(build=pin, judgments=judgments)),
        retained_identity=report['identity'], retained=retained,
        attempted=dict(plan_sha256=pin['plan_sha256'], source_sha256=identity(pin['source']),
                       runtime_sha256=identity(pin['runtime'])))
    if judgments is not None:
        receipt['attempted']['judgments_sha256'] = judgments
    write_json(directory / (uuid.uuid4().hex + '.json'), receipt)
    raise ValueError(reason)


def _archive_records(out, reason):
    """Retain the exact previous root records before superseding their claims."""
    directory = _retained_path(out, 'superseded') / uuid.uuid4().hex
    directory.mkdir(parents=True)
    previous = {}
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in ('build-report.json', 'qualification.json'):
            path = _retained_path(out, name)
            if path.exists():
                raw = path.read_bytes()
                publish(descriptor, name, raw)
                previous[name] = hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)
    supersession = dict(path=str(directory.relative_to(out)), previous=previous, reason=reason)
    write_json(directory / 'supersession.json', dict(schema='agent-ee.build-supersession/1', **supersession))
    descriptor = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return supersession


def _invalidate_retained(out, report, reason, plan):
    """Preserve superseded root bytes, then withdraw a claim on corrupted evidence."""
    supersession = _archive_records(out, reason)
    report.update(status='failed', accepted=False, reason='QualificationError: ' + reason,
                  supersession=supersession)
    report.pop('qualification', None)
    write_json(out / 'build-report.json', report)
    record = failed_assessment(out, report, reason, typed=plan['schema'] == TYPED_SCHEMA, integrity=True)
    record['supersession'] = supersession
    write_json(out / 'qualification.json', record)
    report['qualification'] = dict(path='qualification.json', sha256=digest(out / 'qualification.json'),
        mode=record['mode'], dimensions={name: row['status'] for name, row in record['dimensions'].items()})
    write_json(out / 'build-report.json', report)
    raise ValueError(reason)


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



def _publish(root, record):
    target = _retained_path(root, 'qualification.json')
    temporary = root / ('.qualification-' + uuid.uuid4().hex)
    try:
        write_json(temporary, record)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def _dimension(status='not_assessed', *, binding=None, evidence=(), reason=None):
    row = dict(status=status, binding=dict(binding) if binding is not None else dict(
        board_sha256=None, scope_sha256=None, stage=None), evidence=copy.deepcopy(list(evidence)))
    if reason:
        row['reason'] = reason
    return row


def _snapshot(root):
    report = strict_json(_retained_path(root, 'build-report.json').read_bytes())
    if report.get('schema') != REPORT_SCHEMA or report.get('identity') != identity(report['inputs']):
        raise QualificationError('build identity differs from captured inputs', integrity=True)
    if _inventory(_retained_path(root, 'source')) != report['inputs']['source']:
        raise QualificationError('retained build source identity changed', integrity=True)
    completed = report['completed']
    if len(completed) > len(STAGES):
        raise QualificationError('completed stage inventory is invalid')
    for index, row in enumerate(completed):
        directory = _retained_path(root, row['directory'])
        candidate = _retained_path(root, row['candidate'])
        if (row['stage'] != STAGES[index] or candidate == directory or not candidate.is_relative_to(directory)
                or tree_identity(directory) != row['tree']):
            raise QualificationError('completed stage identity changed: ' + row['stage'], integrity=True)
    if completed and report.get('candidate') != completed[-1]['candidate']:
        raise QualificationError('candidate differs from the last completed stage')
    return report


def _plan(root, report):
    path = _retained_path(root, 'build-plan.json')
    if not path.exists():
        return None
    if digest(path) != report['inputs']['plan_sha256']:
        raise QualificationError('captured build plan identity changed', integrity=True)
    return validate_plan(strict_json(path.read_bytes()))


def verify_retained(out):
    """Verify retained custody independently of a proposed resume invocation."""
    root = Path(out)
    try:
        report = _snapshot(root)
        plan = _plan(root, report)
        if plan is None and 'qualification' in report:
            raise ValueError('captured build plan is missing from a qualified run')
        if report.get('accepted') and len(report['completed']) != len(STAGES):
            raise ValueError('accepted build has no complete stage inventory')
        if 'judgment_inventory' in report:
            frozen = report['judgment_inventory']
            path = _retained_path(root, frozen['path'])
            if digest(path) != frozen['sha256'] or _inventory(path.parent / 'judgments') != read_json(path):
                raise ValueError('retained judgment inventory changed after freeze')
        return plan
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc), integrity=True) from exc


def judgment_inventory(source, plan):
    if 'judgments' not in plan:
        return {}
    directory = _retained_path(source, plan['judgments'])
    if not directory.exists():
        return {}
    return _inventory(directory)


def applicability_facts(candidate, plan):
    envelope = _retained_path(candidate, plan['envelope'])
    raw = strict_json(envelope.read_bytes())
    stackup = raw['stackup']
    return dict(envelope_sha256=digest(envelope),
        board_thickness_mm=stackup.get('nominal_thickness_mm', stackup.get('thickness_mm')),
        profile_sha256=raw.get('fab_profile', {}).get('sha256'),
        layer_count=raw.get('layer_count'), max_component_height_mm=raw.get('max_component_height_mm'))


def freeze_judgments(source, candidate, plan, stage):
    """Copy authored records once, before the package stage is sealed."""
    inventory = judgment_inventory(source, plan)
    target = stage / 'judgments'
    target.mkdir()
    if inventory:
        shutil.copytree(_retained_path(source, plan['judgments']), target, dirs_exist_ok=True)
    if _inventory(target) != inventory or judgment_inventory(source, plan) != inventory:
        raise QualificationError('judgment inputs changed while freezing package')
    write_json(stage / 'judgment-inventory.json', inventory)
    write_json(stage / 'applicability-inventory.json', dict(values=applicability_facts(candidate, plan),
                                                        inputs=_inventory(candidate)))


def verify_judgment_source(source, plan, stage):
    if judgment_inventory(source, plan) != read_json(stage / 'judgment-inventory.json'):
        raise QualificationError('judgment inputs changed after package')


def _evidence(root, path):
    relative = str(path.relative_to(root))
    path = _retained_path(root, relative)
    return dict(path=relative, sha256=digest(path))


def _operational(report):
    if len(report['completed']) != len(STAGES):
        return _dimension()
    failed = {}
    completed = {row['stage']: row for row in report['completed']}
    for attempt in report['attempts']:
        if attempt.get('identity') != report['identity'] or attempt['stage'] not in completed:
            continue
        if attempt['status'] in ('interrupted', 'failed'):
            failed[attempt['stage']] = attempt
        elif (attempt['status'] == 'completed' and attempt['stage'] in failed
                and attempt.get('directory') == completed[attempt['stage']]['directory']
                and failed[attempt['stage']].get('directory') != attempt['directory']):
            return dict(_dimension('pass', evidence=[dict(path=row['directory'], sha256=row['tree']['sha256'],
                kind='sealed_stage_tree') for row in report['completed']]),
                clean_execution=[row['tree']['sha256'] for row in report['completed']],
                resume=dict(interrupted_attempt=failed[attempt['stage']], resumed_attempt=attempt,
                            stage=attempt['stage'], identity=report['identity']))
    return _dimension('pending', reason='No failed/interrupted stage followed by completion under this identity')


def _checked_review(root, report):
    from .cli import verify
    candidates = list(reversed(report['completed']))
    if len(report['completed']) == len(STAGES) - 1:
        for attempt in reversed(report['attempts']):
            if (attempt['stage'] == 'package' and attempt['status'] in ('failed', 'interrupted')
                    and attempt.get('identity') == report['identity']):
                candidates.insert(0, dict(stage='package', directory=attempt['directory'],
                                         candidate=attempt['directory'] + '/candidate'))
                break
    for stage in candidates:
        directory = _retained_path(root, stage['directory'])
        review = directory / 'review'
        if not (review / 'result.json').exists():
            continue
        result_sha256 = digest(review / 'result.json')
        result = verify(review)
        if digest(review / 'result.json') != result_sha256:
            raise QualificationError('Ordinary review changed during verification', integrity=True)
        manifest = read_json(review / 'candidate.json')
        scope = manifest['scope']
        candidate = _retained_path(root, stage['candidate'])
        board_hash = manifest['files'].get(scope.get('board'))
        if scope.get('board') and board_hash != digest(_retained_path(candidate, scope['board'])):
            raise QualificationError('review board differs from its sealed stage candidate')
        scaffold_row = next((row for row in report['completed'] if row['stage'] == 'scaffold'), None)
        scope_path = (_retained_path(root, scaffold_row['directory']) / 'product/scope.json'
                      if scaffold_row else directory / 'scope.json')
        binding = dict(board_sha256=board_hash, scope_sha256=digest(scope_path), stage=stage['stage'])
        return directory, result, scope, binding, result_sha256
    return None


def checks_dimension(root, review):
    if review is None:
        return _dimension()
    directory, result, scope, binding = review[:4]
    rows = result['checks']
    status = 'pass' if rows else 'not_assessed'
    if any(row['execution'] != 'ok' or row['outcome'] not in ('pass', 'info') for row in rows):
        status = 'failed'
    return dict(_dimension(status, binding=binding, evidence=[_evidence(root, directory / 'review/result.json')]),
                executed=[{key: row[key] for key in ('id', 'kind', 'execution', 'outcome')} for row in rows])


def _review_dimension(root, review):
    if review is None:
        return _dimension()
    directory, result, scope, binding = review[:4]
    if (len(result.get('reviews', [])) < 2 or len(result.get('review_executions', [])) < 2 or not result.get('adjudicated')
            or not all(row['execution'] == 'ok' for row in result.get('review_executions', []))):
        return _dimension(reason='No complete full engineering review is referenced')
    state = result['outcome']
    status = 'pass' if state == 'criteria_satisfied' else 'failed' if state == 'defects_or_unresolved' else 'not_assessed'
    return _dimension(status, binding=binding, evidence=[_evidence(root, directory / 'review/result.json')])


def rules_register(root, review):
    """Expose the existing sourced/default classifications without changing their grade."""
    from .design_rule_provenance import advisory_numeric, require_evaluated_limits
    if review is None:
        return []
    directory, result, scope, binding = review[:4]
    records = []
    for row in result['checks']:
        if row['kind'] not in ('erc', 'drc'):
            continue
        details = row['details']
        item = dict(check=row['id'], kind=row['kind'], status='pass' if row['execution'] == 'ok' else 'failed',
            **{key: copy.deepcopy(details.get(key)) for key in (
                'ignored_checks', 'by_severity', 'constraint_classes', 'advisory_findings_count',
                'waived', 'waived_by_severity', 'graded_unwaived')}, advisories=[], evaluated_sources={})
        if row['kind'] == 'drc':
            constraints = [name for name in row['evidence'] if name.endswith('/drc-constraints.json')]
            native = [name for name in row['evidence'] if name.endswith('/drc.json')]
            if len(constraints) != 1 or len(native) != 1:
                item.update(status='pending', reason='Sourced-rule evaluation evidence is unavailable')
            else:
                constraint_path = _retained_path(directory / 'review', constraints[0])
                document = read_json(constraint_path)
                item['evaluated_sources'] = document['sources']
                item['advisories'] = [v for v in document['violations'] if advisory_numeric(v)]
                item['evidence'] = [_evidence(root, constraint_path)]
                try:
                    require_evaluated_limits(read_json(_retained_path(directory / 'review', native[0])), document['sources'])
                except ValueError as exc:
                    item.update(status='pending', reason=str(exc))
        records.append(item)
    return records


def decision_record(root, plan, candidate, review, judgments):
    """Retain the current captured envelope alongside superseded diagnostic bytes."""
    if plan is None or not _retained_path(candidate, plan['envelope']).exists():
        return dict(status='pending', reason='No captured envelope decision')
    facts = applicability_facts(candidate, plan)
    record = dict(status='pending', **facts, nominal_thickness_mm=facts['board_thickness_mm'],
        thickness_tolerance_source=None,
        height_scope=dict(max_component_height_mm=facts['max_component_height_mm'],
                          source=plan['envelope'], scope='captured component-height limit; supplier/physical fit pending'),
        judgments=[row['sha256'] for row in judgments if 'sha256' in row], supersedes=[])
    previous = _retained_path(candidate, 'manufacturing-decision.json')
    if previous.exists():
        record['supersedes'].append(dict(path='manufacturing-decision.json', sha256=digest(previous),
            reason='Current captured envelope and saved stackup evidence supersede this diagnostic decision'))
    rows = review[1]['checks'] if review else []
    stacks = [row for row in rows if row['kind'] == 'board_envelope'
              and row['details'].get('criterion') == 'stackup/1']
    if len(stacks) != 1:
        record['reason'] = 'Exactly one bound native stackup assessment is required'
        return record
    row = stacks[0]
    details = row['details']
    record['thickness_tolerance_source'] = details.get('finished_thickness_tolerance')
    try:
        same = Decimal(str(details['saved']['thickness_mm'])) == Decimal(str(facts['board_thickness_mm']))
    except (KeyError, InvalidOperation):
        same = False
    record['status'] = ('pass' if same and row['execution'] == 'ok' and row['outcome'] == 'pass'
        and details.get('envelope', {}).get('sha256') == facts['envelope_sha256']
        and details.get('board_sha256') == review[3]['board_sha256'] else 'failed')
    if record['status'] != 'pass':
        record['reason'] = 'Captured nominal thickness or envelope differs from native stackup evidence'
    return record


def _author(value):
    if not isinstance(value, dict) or set(value) != {'agent', 'model', 'date_utc'}:
        raise QualificationError('author/verifier needs agent, model and date_utc metadata')
    if any(not isinstance(value[key], str) or not value[key].strip() or value[key] != value[key].strip() for key in value):
        raise QualificationError('author/verifier metadata must be nonempty text')
    try:
        stamp = datetime.fromisoformat(value['date_utc'].replace('Z', '+00:00'))
    except ValueError as exc:
        raise QualificationError('author/verifier date_utc is invalid') from exc
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise QualificationError('author/verifier date_utc must use UTC')


def admit_judgment(document, *, requirement, span, files, source_text, applicability, binding,
                   judgment_sha256, witness=None):
    """Admit bounded authored testimony; matching metadata alone never evaluates a circuit."""
    from .source_authority import verified_reference
    try:
        _keys(document, ('schema', 'requirement', 'span', 'conclusion', 'assumptions', 'sources',
                         'applicability', 'inputs', 'candidate', 'author', 'unresolved_defects'))
        if document['schema'] != 'agent-ee.engineering-judgment/1':
            raise QualificationError('unsupported judgment schema')
        if document['requirement'] != requirement or document['span'] != span:
            raise QualificationError('judgment requirement/source span is stale')
        _author(document['author'])
        if not isinstance(document['conclusion'], str) or not document['conclusion'].strip():
            raise QualificationError('judgment needs an authored conclusion')
        for key in ('assumptions', 'unresolved_defects'):
            if not isinstance(document[key], list) or any(not isinstance(v, str) or not v.strip() for v in document[key]):
                raise QualificationError('judgment ' + key + ' must be a list of nonempty text')
        if not isinstance(document['inputs'], list) or not isinstance(document['applicability'], dict):
            raise QualificationError('judgment inputs/applicability have invalid shapes')
        if document['candidate'] is None:
            if not document['inputs'] or not document['applicability']:
                raise QualificationError('candidate-independent judgment needs inputs and applicability')
        else:
            expected_candidate = {key: binding.get(key) for key in ('board_sha256', 'scope_sha256')}
            if (any(not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None
                    for value in expected_candidate.values()) or document['candidate'] != expected_candidate):
                raise QualificationError('judgment candidate binding is stale')
        for entry in document['inputs']:
            _keys(entry, ('path', 'sha256'))
            _relative(entry['path'], 'judgment input')
            if files.get(entry['path']) != entry['sha256']:
                raise QualificationError('judgment input hash is stale: ' + entry['path'])
        for key, expected in document['applicability'].items():
            actual = applicability.get(key)
            if actual is None or type(expected) is bool or expected != actual:
                raise QualificationError('judgment applicability is unsupported or mismatched: ' + key)
        if not isinstance(document['sources'], list) or not document['sources']:
            raise QualificationError('judgment needs captured source citations')
        for source in document['sources']:
            _keys(source, ('kind', 'citation'))
            if (source['kind'] not in ('datasheet', 'manufacturer', 'owner', 'engineering')
                    or verified_reference(source['citation'], source_text)['enforcement'] != 'verified'):
                raise QualificationError('judgment source citation is unresolved')
        if document['unresolved_defects']:
            return dict(status='failed', witness='missing', reason='Unresolved engineering defects')
        if witness is None:
            return dict(status='pending', record_status='recorded', witness='missing', reason='Independent witness is missing')
        _keys(witness, ('schema', 'judgment_sha256', 'verifier', 'checked', 'result', 'defects'))
        if witness['schema'] != 'agent-ee.judgment-verification/1':
            raise QualificationError('unsupported judgment witness schema')
        if witness['judgment_sha256'] != judgment_sha256:
            raise QualificationError('judgment witness hash is stale')
        _author(witness['verifier'])
        if witness['verifier']['agent'].casefold() == document['author']['agent'].casefold():
            raise QualificationError('judgment witness is not independent of its author')
        if (not isinstance(witness['checked'], list) or any(not isinstance(v, str) for v in witness['checked'])
                or len(witness['checked']) != len(WITNESS_CHECKS) or set(witness['checked']) != set(WITNESS_CHECKS)):
            raise QualificationError('judgment witness does not attest every required verification')
        if witness['result'] not in ('concur', 'dissent') or not isinstance(witness['defects'], list):
            raise QualificationError('judgment witness has invalid result/defects')
        if witness['result'] == 'dissent' or witness['defects']:
            return dict(status='failed', witness='dissent', reason='Independent witness records dissent or defects')
        return dict(status='recorded', witness='concur')
    except ValueError as exc:
        return dict(status='failed', witness='missing', reason=str(exc))


def _judgments(root, stage, entries, binding, plan):
    directory = stage / 'judgments'
    inventory = read_json(stage / 'judgment-inventory.json')
    if _inventory(directory) != inventory:
        raise QualificationError('frozen judgment inventory differs from package')
    captured = read_json(stage / 'applicability-inventory.json')
    candidate = _retained_path(root, entries['candidate'])
    if _inventory(candidate) != captured['inputs'] or applicability_facts(candidate, plan) != captured['values']:
        raise QualificationError('frozen applicability inputs differ from final package candidate')
    source_text = {}
    witnesses = {}
    for path, pin in inventory.items():
        if path.startswith('verification/') and path.endswith('.json'):
            witness = strict_json(_retained_path(directory, path).read_bytes())
            if not isinstance(witness, dict) or witness.get('schema') != 'agent-ee.judgment-verification/1':
                raise QualificationError('unknown frozen witness schema: ' + path)
            witnesses.setdefault(witness.get('judgment_sha256'), []).append((path, witness))
    output = []
    for entry in entries['requirements']:
        if entry['mode'] != 'judgment':
            continue
        rid = entry.get('parent_id', entry['id'])
        locator = entry['locator']
        span = dict(byte_start=locator['byte_start'], byte_end=locator['byte_end'],
                    text_sha256=entry['obligation_text_sha256'])
        relative = entry['mapping']['record']
        path = _retained_path(directory, relative)
        row = dict(id=rid, span_id=entry['id'], span=span, path=relative)
        if relative not in inventory:
            output.append(dict(row, status='pending', witness='missing', reason='Judgment record is missing'))
            continue
        pin = digest(path)
        document = strict_json(path.read_bytes())
        matched = witnesses.get(pin, [])
        if len(matched) > 1:
            output.append(dict(row, sha256=pin, status='failed', witness='dissent', reason='Ambiguous duplicate witnesses'))
            continue
        sources = document.get('sources', []) if isinstance(document, dict) else []
        for source in sources if isinstance(sources, list) else []:
            citation = source.get('citation', {}) if isinstance(source, dict) else {}
            name = citation.get('path') if isinstance(citation, dict) else None
            if name in captured['inputs']:
                try:
                    source_text[name] = _retained_path(candidate, name).read_text(encoding='utf-8')
                except UnicodeError:
                    continue
        admitted = admit_judgment(document, requirement=rid, span=span, files=captured['inputs'],
            source_text=source_text, applicability=captured['values'], binding=binding,
            judgment_sha256=pin, witness=matched[0][1] if matched else None)
        output.append(dict(row, sha256=pin, **admitted, record=copy.deepcopy(document),
                           verification=copy.deepcopy(matched[0][1]) if matched else None,
                           evidence=[_evidence(root, path), *([_evidence(root, directory / matched[0][0])] if matched else [])]))
    return output


def _clause_status(spans):
    values = [span['status'] for span in spans if span['status'] != 'not_applicable']
    for state in ('failed', 'unknown', 'pending'):
        if state in values:
            return state
    if not values:
        return 'not_applicable'
    return 'recorded' if all(value == 'recorded' for value in values) else 'pass'


def _receipt(root, report, typed):
    scaffold_row = next((row for row in report['completed'] if row['stage'] == 'scaffold'), None)
    if scaffold_row:
        return read_json(_retained_path(root, scaffold_row['directory']) / 'requirements.json')
    requirements = next((row for row in report['completed'] if row['stage'] == 'requirements'), None)
    rows = read_json(_retained_path(root, requirements['directory']) / 'accounting.json')['requirements'] if requirements else []
    return dict(schema='agent-ee.requirement-map-receipt/' + ('2' if typed else '1'), requirements=rows,
                **({'method_binding': 'typed'} if typed else {}))


def _bind_receipt(root, receipt, plan, *, provisional, accounting):
    expected = {}
    for name, field in (('map', 'map_sha256'), ('record', 'spec_record_sha256'),
                        ('spec', 'spec_sha256'), ('obligations', 'obligations_sha256')):
        if name in plan['requirements']:
            expected[field] = digest(_retained_path(root / 'source', plan['requirements'][name]))
    spec = _retained_path(root / 'source', plan['requirements']['spec']).read_bytes()
    try:
        requirement_map.bind_receipt(receipt, spec, source_hashes=expected,
                                     accounting=accounting, provisional=provisional)
    except ValueError as exc:
        raise QualificationError(str(exc)) from exc


def _package_records(rows):
    records = {}
    for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation')):
        found = [row for row in rows if row['kind'] == kind]
        records[name] = ('pending' if not found else 'pass' if len(found) == 1
            and found[0]['execution'] == 'ok' and found[0]['outcome'] == 'pass' else 'failed')
    packages = [row for row in rows if row['kind'] == 'fab_package']
    orientation = packages[0].get('details', {}).get('assembly_orientation') if len(packages) == 1 else None
    records['orientation_record'] = copy.deepcopy(orientation)
    if records['orientation'] == 'pass' and (not isinstance(orientation, dict)
            or orientation.get('software_assessment') != 'complete' or orientation.get('unassessed_rows')
            or not orientation.get('profile_sha256')):
        records['orientation'] = 'pending'
    return records


def _methods(root, review):
    if review is None:
        return []
    return [dict(id=row['id'], kind=row['kind'], **{'class': requirement_map.method_class(row['kind'])},
                 evidence=_evidence(root, review[0] / 'review/checks' / (row['id'] + '.json')))
            for row in review[1]['checks']]


def _assess(out):
    root = Path(out).resolve()
    try:
        report = _snapshot(root)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc), integrity=True) from exc
    plan = _plan(root, report)
    typed = plan is not None and plan['schema'] == TYPED_SCHEMA
    receipt = _receipt(root, report, typed)
    if (receipt['schema'] == 'agent-ee.requirement-map-receipt/2') != typed:
        raise QualificationError('Requirement receipt method binding differs from the captured build plan')
    if typed:
        first = next((row for row in report['completed'] if row['stage'] == 'requirements'), None)
        accounting = read_json(_retained_path(root, first['directory']) / 'accounting.json')['requirements'] if first else []
        _bind_receipt(root, receipt, plan, provisional=not any(row['stage'] == 'scaffold' for row in report['completed']),
                      accounting=accounting)
    review = _checked_review(root, report)
    rows = review[1]['checks'] if review else []
    package = next((row for row in report['completed'] if row['stage'] == 'package'), None)
    dimensions = {name: _dimension() for name in DIMENSIONS}
    for name in ('supplier', 'physical'):
        dimensions[name] = _dimension('pending', reason='No supplier or physical validation performed')
    dimensions['checks'] = checks_dimension(root, review)
    dimensions['engineering_review'] = _review_dimension(root, review)
    coverage = requirement_map.coverage_view(receipt, rows)
    methods = _methods(root, review)
    rules = rules_register(root, review)
    judgments, decision, blockers = [], {}, []
    demonstrated_failure = False
    if typed:
        dimensions['operational'] = _operational(report)
        if 'judgments' in plan:
            for stage in report['completed']:
                if stage['stage'] == 'package':
                    continue
                directory = _retained_path(root, stage['directory'])
                prefix = plan['judgments'] + '/'
                if any(name.startswith(prefix) or '/' + prefix in name for name in _inventory(directory)):
                    raise QualificationError('pre-package stage contains editable judgments')
    if package is not None:
        directory = _retained_path(root, package['directory'])
        candidate = _retained_path(root, package['candidate'])
        marker_path = directory / 'accepted-package.json'
        marker = read_json(marker_path)
        if ('qualification' in marker or marker.get('physical_validation_performed') is not False
                or marker.get('supplier_preview_performed') is not False):
            blockers.append('Package contains unsupported claims')
            demonstrated_failure = True
        elif review is None or review[0] != directory:
            blockers.append('Package has no matching verified checks-only file gate')
        else:
            try:
                gate_plan = plan
                if gate_plan is None:
                    memberships = {row['params']['manifest'] for row in review[2]['checks'] if row['kind'] == 'parts_subset'}
                    if len(memberships) != 1 or 'envelope' not in review[2]:
                        raise QualificationError('Legacy package has no unambiguous manufacturing envelope/parts binding')
                    gate_plan = dict(envelope=review[2]['envelope']['path'],
                        parts=dict(manifest=memberships.pop()), placement={})
                _manufacturing_coverage(gate_plan, review[2], receipt['requirements'])
                artifacts = package_artifacts(review[1], review[2], candidate)
                native = {name: pin for name, pin in _inventory(candidate).items()
                          if name.endswith(('.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_dru'))}
                if marker['artifacts'] != artifacts or marker['native_files'] != native:
                    raise QualificationError('accepted package differs from the final gate evidence')
            except ValueError as exc:
                blockers.append(str(exc))
                demonstrated_failure = (isinstance(exc, QualificationError)
                    or any(row['execution'] != 'ok' or row['outcome'] not in ('pass', 'info') for row in rows))
        if typed:
            if marker.get('judgment_inventory_sha256') != digest(directory / 'judgment-inventory.json'):
                raise QualificationError('package judgment inventory binding differs')
            judgments = _judgments(root, directory, dict(receipt, candidate=package['candidate']),
                                   review[3] if review else {}, plan)
            state = 'failed' if any(row['status'] == 'failed' for row in judgments) else (
                'pending' if any(row['status'] != 'recorded' or row['witness'] != 'concur' for row in judgments) else 'pass')
            dimensions['judgments'] = _dimension(state, evidence=[_evidence(root, directory / 'judgment-inventory.json')])
            if state != 'pass':
                blockers.append('Required engineering judgments or independent concurrence remain unresolved')
                demonstrated_failure |= state == 'failed'
            decision = decision_record(root, plan, candidate, review, judgments)
            if decision['status'] != 'pass':
                blockers.append('Current decision is not established by matching native stackup evidence')
                demonstrated_failure |= decision['status'] == 'failed'
            if any(row['status'] != 'pass' for row in rules):
                blockers.append('Applicable sourced-rule evaluation is incomplete')
                demonstrated_failure |= any(row['status'] == 'failed' for row in rules)
    judgments_by_span = {row['span_id']: row for row in judgments}
    entries = {row['id']: row for row in receipt['requirements']}
    if typed:
        for clause in coverage.values():
            for span in clause['spans']:
                if span['mode'] == 'operational':
                    criterion = entries[span['id']]['mapping']['criterion']
                    span['status'] = (dimensions['operational']['status'] if criterion == 'public_build_and_resume'
                                      else 'pending')
                    if span['status'] == 'not_assessed':
                        span['status'] = 'pending'
                    if criterion != 'public_build_and_resume':
                        span['reason'] = 'Unsupported operational criterion'
                elif span['mode'] == 'judgment' and span['id'] in judgments_by_span:
                    judgment = judgments_by_span[span['id']]
                    span.update(status=judgment['status'], witness=judgment['witness'])
            clause['status'] = _clause_status(clause['spans'])
        if any(span['status'] not in ('pass', 'recorded', 'not_applicable')
               for clause in coverage.values() for span in clause['spans']
               if span['obligation'] in ('executable', 'judgment')):
            blockers.append('Required software-file or judgment source spans are not satisfied')
            demonstrated_failure |= any(span['status'] == 'failed' for clause in coverage.values()
                for span in clause['spans'] if span['obligation'] in ('executable', 'judgment'))
        if any(span['status'] == 'failed' for clause in coverage.values() for span in clause['spans']
               if span['obligation'] == 'operational'):
            blockers.append('Required operational assessment failed')
            demonstrated_failure = True
    package_records = _package_records(rows)
    bound = {cid for clause in coverage.values() for span in clause['spans'] for cid in span['bound_ids']}
    if typed and any(package_records[name] != 'pass' and any(row['kind'] == kind and row['id'] in bound for row in rows)
                     for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation'))):
        blockers.append('Required exact-package evidence is incomplete')
        demonstrated_failure |= any(package_records[name] == 'failed'
            and any(row['kind'] == kind and row['id'] in bound for row in rows)
            for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation')))
    if package is not None:
        dimensions['file_release'] = _dimension('failed' if demonstrated_failure else 'pending' if blockers else 'pass',
            binding=review[3] if review else None, evidence=[_evidence(root, marker_path)],
            reason='; '.join(blockers) if blockers else None)
    elif report['status'] == 'failed' and report.get('attempts') and report['attempts'][-1]['stage'] == 'package':
        dimensions['file_release'] = _dimension('failed', reason=report.get('reason'))
    record = dict(schema=QUALIFICATION_SCHEMA, mode='typed' if typed else 'legacy', claims_withheld=not typed,
        accepted=dimensions['file_release']['status'] == 'pass', claim=CLAIM, dimensions=dimensions,
        coverage=coverage, methods=methods, judgments=judgments, rules=rules, decision=decision,
        package_records=package_records, summary=dict(pending_required=sorted(
            rid for rid, clause in coverage.items() if clause['status'] not in ('pass', 'recorded', 'not_applicable')),
            file_release_blockers=blockers), related_runs=[])
    if _snapshot(root) != report:
        raise QualificationError('Build evidence changed during qualification assessment', integrity=True)
    if review is not None:
        from .cli import verify
        if verify(review[0] / 'review') != review[1] or digest(review[0] / 'review/result.json') != review[4]:
            raise QualificationError('Ordinary review evidence changed during assessment', integrity=True)
    return _publish(root, record)


def assess(out):
    """Regenerate a root record from verified completed stages, never from old qualification."""
    try:
        return _assess(out)
    except QualificationError:
        raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc)) from exc


def failed_assessment(out, report, reason, *, typed, integrity=False):
    """Publish only failure claims when custody or assessment cannot be verified."""
    dimensions = {name: _dimension(reason='Evidence assessment did not complete') for name in DIMENSIONS}
    dimensions['file_release'] = _dimension('failed', reason=reason)
    if integrity:
        dimensions['operational'] = _dimension('failed', reason=reason)
    for name in ('supplier', 'physical'):
        dimensions[name] = _dimension('pending')
    return _publish(Path(out), dict(schema=QUALIFICATION_SCHEMA, mode='typed' if typed else 'legacy',
        claims_withheld=True, accepted=False, claim=CLAIM, dimensions=dimensions, coverage={},
        methods=[], judgments=[], rules=[], decision={}, package_records=dict(roundtrip='pending', orientation='pending'),
        summary=dict(pending_required=[], assessment_error=reason), related_runs=[]))


def assess_review(out, result, selection):
    """A diagnostic checks-only review cannot grant a build or package claim."""
    root = Path(out)
    manifest = read_json(root / 'review/candidate.json')
    binding = dict(board_sha256=manifest['files'].get(manifest['scope'].get('board')),
        scope_sha256=selection['scope_sha256'], stage=selection['selected']['stage'])
    dimensions = {name: _dimension() for name in DIMENSIONS}
    review = (root, result, manifest['scope'], binding)
    dimensions['checks'] = checks_dimension(root, review)
    return _publish(root, dict(schema=QUALIFICATION_SCHEMA, mode=selection.get('qualification_mode', 'legacy'),
        claims_withheld=True, accepted=False, claim=CLAIM, dimensions=dimensions, coverage={},
        methods=_methods(root, review), judgments=[], rules=rules_register(root, review), decision={},
        package_records=dict(roundtrip='pending', orientation='pending'), summary=dict(pending_required=[]), related_runs=[]))
