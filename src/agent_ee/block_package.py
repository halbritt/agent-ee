"""Final saved-board block evidence owned by the artwork and package stages."""
from pathlib import Path

from . import block_source_stage, placement_job, retained_run
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
    locality = block_source_stage.locality_path(scope)
    if locality:
        names['locality'] = locality
    source_bound = 'block_source' in plan['placement']
    names['block_contract'] = (block_source_stage.DIRECTORY + '/contract.json' if source_bound
                               else plan['placement']['block_contract'])
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
    declarations = {name: placement_job.sha256_bytes(raw[key]) for key, name in names.items()
                    if key not in ('board', 'project', 'rules')}
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
        policy_sha256=identity(policy_files()))
    return normalized, basis


def original_basis(out, scope, plan):
    try:
        return _original_basis(out, scope, plan)
    except (KeyError, TypeError) as exc:
        raise ValueError('strict original placement basis is incomplete: ' + str(exc)) from exc


def _validate(proof, normalized, basis, files, board):
    binding = normalized['block_contract_binding']
    if (not isinstance(proof, dict) or set(proof) != {'basis', 'final_files', 'report', 'report_sha256'}
            or not isinstance(proof['report'], dict)):
        raise ValueError('strict final artwork proof is incomplete')
    report = proof['report']
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
    if report.get('status') not in ('pass', 'info') or report['defects'] or report['unknown']:
        raise ValueError('strict final artwork groups contain defects or required unknowns')


def produce(out, candidate, directory, scope, plan):
    if not declared(plan):
        return
    normalized, basis = original_basis(out, scope, plan)
    files = input_inventory(candidate)
    try:
        report = placement_job.check_block_contract(candidate / scope['board'], normalized,
            clock=placement_job.Clock(60), cancel_dir=Path(out))
    except placement_job.JobStopped as exc:
        raise ValueError('strict final artwork native read stopped: ' + exc.status) from exc
    proof = dict(basis=basis, final_files=files, report=report, report_sha256=identity(report))
    # Retain evaluator defects/unknowns too; a failed artwork stage is never sealed.
    write_json(directory / PROOF, proof)
    if original_basis(out, scope, plan)[1] != basis or input_inventory(candidate) != files:
        raise ValueError('strict final artwork inputs changed during fresh native read')
    _validate(proof, normalized, basis, files, scope['board'])


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
    _validate(proof, normalized, basis, files, scope['board'])
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
