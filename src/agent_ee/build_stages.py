"""The build's stage producers; retained-run owns their retained records."""
import copy
from pathlib import Path
import shutil

from . import parts_stage, qualification, requirement_map, retained_run, scaffold, schematic_stage, spec_record
from .stage_receipts import adopt, run_step, tree_identity
from .storage import digest, read_json, write_json
from .run_record import policy_files

TYPED_SCHEMA = retained_run.TYPED_SCHEMA
GENERATED = 'build-generated/connectivity-intent.json'
RATINGS = 'build-generated/parts-ratings'
SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = SOURCE_ROOT.parent
_inventory = requirement_map.input_inventory


def candidate_scope(scope, candidate):
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
    retained_run.write_scope(stage_dir, candidate_scope(scope, candidate))
    retained_run.write_accounting(stage_dir, dict(requirements=accounting,
        coverage='pending native scaffold', acceptance=False))



def _compile_scaffold_requirements(plan, candidate, stage_dir):
    names = plan['requirements']
    files = {name: pin for name, pin in _inventory(candidate).items()
             if not name.startswith(requirement_map.PROVENANCE + '/')}
    scope, receipt = requirement_map.compile_map(
        (candidate / names['map']).read_bytes(), (candidate / names['record']).read_bytes(),
        (candidate / names['spec']).read_bytes(), files=files, inputs=candidate,
        **({'obligations_raw': (candidate / names['obligations']).read_bytes()} if 'obligations' in names else {}))
    retained_run.write_requirements(stage_dir, receipt)
    scaffold_receipt = read_json(stage_dir / 'product/scaffold-receipt.json')
    produced = retained_run.read_scope(stage_dir, product=True)
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
    retained_run.write_scope(stage_dir, candidate_scope(produced, candidate))
    return produced



def _package(plan, candidate, stage_dir, scope, accounting):
    qualification.manufacturing_coverage(plan, scope, accounting)
    result = retained_run.review_candidate(candidate_scope(scope, candidate), stage_dir, plan.get('verification_timeout_s', 3600))
    artifacts = qualification.package_artifacts(result, scope, candidate)
    extra = {}
    if plan['schema'] == TYPED_SCHEMA:
        extra['judgment_inventory_sha256'] = digest(stage_dir / 'judgment-inventory.json')
    write_json(stage_dir / 'accepted-package.json', dict(artifacts=artifacts,
        native_files={name: pin for name, pin in _inventory(candidate).items()
                      if name.endswith(('.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_dru'))},
        physical_obligations=[r for r in accounting if r['mode'] == 'bench'],
        physical_validation_performed=False, supplier_preview_performed=False, **extra))



def _artwork(candidate, directory, board):
    """Finish visible field positions using the existing bounded native producer."""
    import pcbnew as k
    from . import routed_artwork
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



def execute(attempt, plan, previous, *, diagnostic=False):
    stage, directory, out = attempt.name, attempt.directory, attempt.root
    scope_record = retained_run.scope(out)
    scope = scope_record.document if scope_record is not None else None
    if stage == 'scaffold':
        planning = retained_run.write_planning_scope(
            directory, candidate_scope(_rated_scope(plan, previous, scope), previous))
        scaffold.scaffold(previous / plan['envelope'], previous / plan['parts']['manifest'], directory / 'product',
            scope_path=planning, intent_path=previous / GENERATED, layout_path=previous / plan['parts']['layout'])
        candidate = directory / 'product/inputs'
        _compile_scaffold_requirements(plan, candidate, directory)
        return candidate
    candidate = attempt.capture(previous)
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
        scaffold_dir = retained_run.completed_stage(out, 'scaffold').directory
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
        accounting = retained_run.accounting(out)['requirements']
        try:
            qualification.manufacturing_coverage(plan, scope, accounting)
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
        _package(plan, candidate, directory, scope, retained_run.accounting(out)['requirements'])
    return candidate
