"""Final saved-board block evidence owned by the artwork and package stages."""
from copy import deepcopy
from pathlib import Path

from . import block_source_stage, placement_contract, placement_job, retained_run
from .catalog import strict_json
from .run_record import policy_files
from .storage import digest, identity, read_json, write_json
from .requirement_map import input_inventory

PROOF = 'block-contract-final.json'


def declared(plan):
    return bool({'block_source', 'block_contract'} & plan.get('placement', {}).keys())


def _original_basis(out, scope, plan):
    """Reconstruct original normalization from sealed files, never the routed config path."""
    report = retained_run.snapshot(Path(out))
    schematic = retained_run.completed_stage(Path(out), 'schematic', report)
    placement = retained_run.completed_stage(Path(out), 'placement-routing', report)
    if schematic is None or placement is None:
        raise ValueError('strict final artwork proof requires sealed schematic and placement stages')
    original = schematic.candidate
    config = read_json(placement.directory / 'placement-config.json')
    names = dict(board=scope['board'], project=str(Path(scope['board']).with_suffix('.kicad_pro')),
                 intent=plan['placement']['intent'], groups=plan['placement']['groups'])
    rules = str(Path(scope['board']).with_suffix('.kicad_dru'))
    if (original / rules).exists():
        names['rules'] = rules
    locality = block_source_stage.locality_path(scope, original)
    if locality:
        names['locality'] = locality
    source_bound = 'block_source' in plan['placement']
    names['block_contract'] = (block_source_stage.DIRECTORY + '/contract.json' if source_bound
                               else plan['placement']['block_contract'])
    mechanics = 'mechanics' in plan['placement']
    if mechanics:
        names['mechanics'] = plan['placement']['mechanics']
    if set(config['inputs']) - set(names) - {'planes'}:
        raise ValueError('strict package original normalization has unsupported placement inputs')
    raw = {key: block_source_stage._read(original, name) for key, name in names.items()}
    for key, name in names.items():
        expected = dict(path=str(placement.candidate / name), sha256=placement_job.sha256_bytes(raw[key]))
        if config['inputs'].get(key) != expected:
            raise ValueError('strict original placement input differs: ' + key)
    if ('rules' in config['inputs']) != ('rules' in names):
        raise ValueError('strict original rules presence differs')
    normalized = placement_job.normalize_intent(strict_json(raw['intent']), strict_json(raw['groups']),
        locality=strict_json(raw['locality']) if 'locality' in raw else None,
        board_sha256=placement_job.sha256_bytes(raw['board']), groups_sha256=placement_job.sha256_bytes(raw['groups']))
    normalized['block_contract'] = strict_json(raw['block_contract'])
    capture = read_json(placement.directory / 'placement/capture.json')
    binding = capture.get('block_contract')
    if binding is None:
        raise ValueError('strict original placement capture binding is missing')
    if source_bound and block_source_stage.verify(out, original, scope, plan) != binding:
        raise ValueError('strict source binding differs from sealed placement capture')
    block_source_stage._binding(binding, normalized['block_contract'], normalized, capture['facts_sha256'])
    if (binding['original_capture']['board_sha256'] != placement_job.sha256_bytes(raw['board'])
            or capture['board_sha256'] != placement_job.sha256_bytes(raw['board'])
            or capture['pcbnew_version'] != binding['original_capture']['pcbnew_version']):
        raise ValueError('strict original native capture identity differs')
    normalized['block_contract_binding'] = binding
    mechanics_basis = _mechanics_basis(placement, original, scope, normalized, capture, names.get('mechanics'), raw)

    declarations = {name: placement_job.sha256_bytes(raw[key]) for key, name in names.items()
                    if key not in ('board', 'project', 'rules')}
    if mechanics:
        declarations.update(normalized['mechanics']['declaration']['sources'])
    if source_bound:
        declarations[plan['placement']['block_source']] = digest(original / plan['placement']['block_source'])
        declarations.update({name: pin for name, pin in input_inventory(original).items()
                             if name.startswith(block_source_stage.DIRECTORY + '/')})
    basis = dict(schematic=schematic.record, placement=placement.record, declarations=declarations,
        inputs={key: placement_job.sha256_bytes(value) for key, value in raw.items()},
        source_kind='block_source' if source_bound else 'block_contract',
        source_sha256=digest(original / plan['placement']['block_source']) if source_bound else None,
        normalized_sha256=identity(normalized), contract_sha256=identity(binding['contract']),
        binding_sha256=binding['binding_sha256'], groups_sha256=identity(normalized['groups']),
        policy_sha256=identity(policy_files()), **({'mechanics': mechanics_basis} if mechanics else {}))
    return normalized, basis



def _mechanics_basis(placement, original, scope, normalized, capture, recipe_name, raw):
    from . import acquisition_mechanics, placement_problem
    job = placement.directory / 'placement'
    problem_path = job / 'problem.json'
    problem = read_json(problem_path) if problem_path.exists() else {}
    if recipe_name is None:
        if 'mechanics' in capture or 'mechanics' in problem:
            raise ValueError('undeclared mechanics in sealed placement evidence')
        return None
    recipe = acquisition_mechanics.load(original / recipe_name)
    if recipe['declaration'] != strict_json(raw['mechanics']):
        raise ValueError('mechanics recipe differs from original captured bytes')
    facts = deepcopy(capture['block_contract']['original_capture'])
    for fp in facts['footprints']:
        if fp['uuid'] in capture['block_contract']['references']:
            fp['ref'] = fp.pop('native_ref')
    if identity(facts) != capture['facts_sha256']:
        raise ValueError('mechanics original facts differ from placement capture')
    bound = acquisition_mechanics.bind(recipe, facts)
    if bound != capture.get('mechanics'):
        raise ValueError('mechanics binding differs from original native owners')
    description = {'footprints': [dict(uuid=fp['uuid'], ref=fp['ref'], side=fp['side'],
        position_nm=fp['position_nm'], rotation_deg=fp['rotation_deg']) for fp in facts['footprints']]}
    acquisition_mechanics.attach(description, bound, facts)
    if (problem.get('schema') != placement_problem.INPUT_SCHEMA
            or problem.get('problem_sha256') != placement_problem.identity(problem)
            or problem.get('mechanics') != description['mechanics']):
        raise ValueError('mechanics original problem identity differs')
    rows = {fp['uuid']: fp for fp in problem['footprints']}
    if len(rows) != len(problem['footprints']) or set(rows) != {fp['uuid'] for fp in facts['footprints']}:
        raise ValueError('mechanics original problem owner coverage differs')
    attached = {fp['uuid']: fp for fp in description['footprints']}
    fixed_owners = placement_problem.fixed_pose_owners(
        normalized['intent'].get('fixed_poses', []), facts['footprints'])
    for fp in facts['footprints']:
        row = rows[fp['uuid']]
        if (row['native_ref'] != fp['ref'] or row['role'] != (
                'fixed_body' if fp['locked'] or fp['uuid'] in fixed_owners else 'movable')
                or row['pose'] != dict(x_nm=fp['position_nm'][0], y_nm=fp['position_nm'][1],
                                       quarter_turns=int(fp['rotation_deg']//90)%4)
                or any(row.get(k) != attached[fp['uuid']][k] for k in ('source_body_local_nm','access_local_nm'))
                or len(row['pads']) != len(fp['pads'])
                or {p['uuid'] for p in row['pads']} != {p['uuid'] for p in fp['pads']}):
            raise ValueError('mechanics original problem native owner/pose differs')
        pads = {p['uuid']: p for p in row['pads']}
        q = int(fp['rotation_deg']//90)%4
        for pad in fp['pads']:
            measured = pads[pad['uuid']]
            local = list(placement_job._unrotate(pad['position_nm'][0]-fp['position_nm'][0],
                                                 pad['position_nm'][1]-fp['position_nm'][1], q))
            if (any(measured.get(k) != pad.get(k) for k in ('number','net'))
                    or measured.get('local_nm') != local):
                raise ValueError('mechanics original problem native pads differ')
    normalized.update(mechanics=recipe, mechanics_binding=bound)
    report_path = job / 'job-report.json'
    report = read_json(report_path)
    config = read_json(placement.directory / 'placement-config.json')
    final = report['final']; checked = final.get('mechanics', {})
    final_path = Path(final['board'])
    if not final_path.is_absolute() or not final_path.is_relative_to(job):
        raise ValueError('mechanics final board is outside its owned placement job')
    final_path = retained_run.retained_path(job, final_path.relative_to(job).as_posix())
    accepted = placement.candidate / scope['board']
    board_sha = digest(accepted)
    if (report.get('status') != 'completed' or report.get('config_sha256') != identity(config)
            or final.get('eligibility') != 'eligible' or final.get('board_sha256') != board_sha
            or digest(final_path) != board_sha or checked.get('status') != 'pass' or checked.get('defects') != []
            or checked.get('binding_sha256') != bound['binding_sha256'] or checked.get('board_sha256') != board_sha):
        raise ValueError('mechanics final placement evidence is missing, failed or stale')
    return dict(binding_sha256=bound['binding_sha256'], original_board_sha256=bound['original_board_sha256'],
        capture_sha256=digest(job/'capture.json'), problem_file_sha256=digest(problem_path),
        problem_sha256=problem['problem_sha256'], job_report_sha256=digest(report_path),
        final_board=final_path.relative_to(job).as_posix(), final_board_sha256=board_sha)


def _mechanics_artwork(out, candidate, directory, scope, basis):
    if 'mechanics' not in basis:
        return None
    from . import routed_artwork
    from .stage_receipts import validate_receipt, after_tree
    placement = retained_run.completed_stage(Path(out), 'placement-routing')
    source = placement.candidate / scope['board']
    board = Path(candidate) / scope['board']
    report_path = Path(directory) / 'artwork.json'
    report = read_json(report_path)
    source_sha = basis['mechanics']['final_board_sha256']
    if report.get('board') != scope['board'] or report.get('source_sha256') != source_sha or digest(source) != source_sha:
        raise ValueError('mechanics artwork source differs from accepted placement')
    transition = dict(artwork_sha256=digest(report_path), source_board_sha256=source_sha,
                      output_board_sha256=digest(board), status=report.get('status'))
    if report.get('status') == 'no_movable_fields':
        if board.read_bytes() != source.read_bytes():
            raise ValueError('mechanics no-movable artwork changed board bytes')
    elif report.get('status') == 'repaired':
        receipt_dir = retained_run.retained_path(Path(directory), report['receipt'])
        source_files = input_inventory(placement.candidate)
        receipt = validate_receipt(receipt_dir, binding=dict(implementation=1, receipt_index=0,
            source_candidate_id=routed_artwork.tree_identity_from_files(source_files)['sha256']))
        proof = routed_artwork.qualify_receipt(receipt_dir, receipt, scope['board'], policy_files())
        if (digest(receipt_dir/'receipt.json') != report.get('receipt_sha256')
                or receipt['inputs'] != source_files or after_tree(receipt) != input_inventory(candidate)
                or receipt['inputs'].get(scope['board']) != source_sha
                or (receipt_dir/'inputs'/scope['board']).read_bytes() != source.read_bytes()
                or receipt['outputs'].get(scope['board']) != digest(board)
                or (receipt_dir/'run'/routed_artwork.BOARD).read_bytes() != board.read_bytes()
                or report.get('board_sha256') != digest(board) or report.get('write_scope') != proof
                or proof.get('status') != 'allowed' or report.get('open_defects') != []):
            raise ValueError('mechanics artwork receipt/adoption differs')
        transition.update(receipt_sha256=digest(receipt_dir/'receipt.json'), write_scope_sha256=identity(proof))
    else:
        raise ValueError('mechanics artwork transition is missing or unresolved')
    return transition


def original_basis(out, scope, plan):
    try:
        return _original_basis(out, scope, plan)
    except (KeyError, TypeError) as exc:
        raise ValueError('strict original placement basis is incomplete: ' + str(exc)) from exc


def _validate(proof, normalized, basis, files, board, transition=None):
    binding = normalized['block_contract_binding']
    if (not isinstance(proof, dict) or set(proof) != ({'basis', 'final_files', 'report', 'report_sha256'} | ({'mechanics_artwork'} if transition is not None else set()))
            or not isinstance(proof['report'], dict)):
        raise ValueError('strict final artwork proof is incomplete')
    report = proof['report']
    if transition is not None and proof.get('mechanics_artwork') != transition:
        raise ValueError('mechanics artwork preservation proof differs')
    if (any(files.get(name) != pin for name, pin in basis['declarations'].items())
            or proof['basis'] != basis or proof['final_files'] != files
            or proof['report_sha256'] != identity(report)
            or report.get('schema') != 'agent-ee.block-contract-report/1'
            or report.get('board_sha256') != files[board]
            or report.get('original_board_sha256') != binding['original_capture']['board_sha256']
            or report.get('contract_sha256') != basis['contract_sha256']
            or report.get('binding_sha256') != basis['binding_sha256']
            or report.get('groups_sha256') != basis['groups_sha256']
            or report.get('reference_aliases') != binding['references']
            or any(not isinstance(report.get(key), list) for key in ('defects', 'unknown', 'advisories'))):
        raise ValueError('strict final artwork proof identity differs')
    placement_contract.verify_saved_report(report, binding)
    if report.get('status') not in ('pass', 'info') or report['defects'] or report['unknown']:
        raise ValueError('strict final artwork groups contain defects or required unknowns')


def produce(out, candidate, directory, scope, plan):
    if not declared(plan):
        return
    normalized, basis = original_basis(out, scope, plan)
    files = input_inventory(candidate)
    transition = _mechanics_artwork(out, candidate, directory, scope, basis)
    try:
        report = placement_job.check_block_contract(candidate / scope['board'], normalized,
            clock=placement_job.Clock(60), cancel_dir=Path(out), retain_capture=True)
    except placement_job.JobStopped as exc:
        raise ValueError('strict final artwork native read stopped: ' + exc.status) from exc
    proof = dict(basis=basis, final_files=files, report=report, report_sha256=identity(report))
    if transition is not None:
        proof['mechanics_artwork'] = transition
    # Retain evaluator defects/unknowns too; a failed artwork stage is never sealed.
    write_json(directory / PROOF, proof)
    if original_basis(out, scope, plan)[1] != basis or input_inventory(candidate) != files:
        raise ValueError('strict final artwork inputs changed during fresh native read')
    if _mechanics_artwork(out, candidate, directory, scope, basis) != transition:
        raise ValueError('mechanics artwork transition changed during fresh native read')
    _validate(proof, normalized, basis, files, scope['board'], transition)


def verify(out, candidate, scope, plan):
    """Replay basis/custody and report identities without trusting an acceptance flag."""
    if not declared(plan):
        return None
    normalized, basis = original_basis(out, scope, plan)
    artwork = retained_run.completed_stage(Path(out), 'artwork')
    if artwork is None:
        raise ValueError('strict package requires completed final artwork proof')
    path = artwork.directory / PROOF
    if not path.is_file() or path.is_symlink():
        raise ValueError('strict final artwork proof is missing')
    proof = read_json(path)
    files = input_inventory(candidate)
    if input_inventory(artwork.candidate) != files:
        raise ValueError('strict package differs from sealed final artwork candidate')
    transition = _mechanics_artwork(out, candidate, artwork.directory, scope, basis)
    _validate(proof, normalized, basis, files, scope['board'], transition)
    return dict(proof_sha256=digest(path), report_sha256=proof['report_sha256'],
                basis_sha256=identity(basis), board_sha256=files[scope['board']],
                policy_sha256=basis['policy_sha256'])


def verify_package(out, candidate, scope, plan, marker, reviewed_board_sha256):
    try:
        binding = verify(out, candidate, scope, plan)
    except ValueError as exc:
        raise retained_run.QualificationError(str(exc)) from exc
    if binding is not None and (marker.get('block_contract') != binding
            or reviewed_board_sha256 != binding['board_sha256']):
        raise retained_run.QualificationError('strict package group proof differs from final review/marker')
    return binding
