"""Fixed schematic-stage producer of native-bound block evidence, never acceptance."""
import argparse
from copy import deepcopy
from pathlib import Path

from . import placement_contract, placement_job, retained_run
from .catalog import strict_json
from .run_record import policy_files
from .stage_receipts import _relative, after_tree, file_tree, tree_identity, validate_receipt, validate_step
from .storage import canonical, digest, identity, read_json, write_json
from .tool_source import snapshot, _files

DIRECTORY = 'build-generated/block-contract'
STEP = 'bind-block-source'
TIMEOUT = 60
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def locality_path(scope, candidate=None):
    """Select one captured document preserving every declared locality constraint."""
    paths = {c['params']['locality'] for c in scope['checks'] if c['kind'] == 'placement'
             and c['params'].get('criterion') == 'electrical-locality/1'}
    if len(paths) > 1:
        if candidate is not None:
            from .electrical_locality import validate
            documents = {path: validate(strict_json(_read(candidate, path))) for path in sorted(paths)}
            for path, complete in documents.items():
                if all(all(row in complete['obligations'] for row in other['obligations'])
                       and all(complete.get('source_records', {}).get(key) == value
                               for key, value in other.get('source_records', {}).items())
                       for other in documents.values()):
                    return path
        raise ValueError('placement build requires one shared electrical locality document')
    return next(iter(paths), None)


def _read(root, path):
    _relative(path, 'block source input')
    target = Path(root) / path
    for part in [target, *target.parents]:
        if part == Path(root):
            break
        if part.is_symlink():
            raise ValueError('block source input contains a link: ' + path)
    if not target.is_file():
        raise ValueError('block source input is not a regular file: ' + path)
    return target.read_bytes()


def admit(root, plan, files):
    """Shape and source authority only; no native identity or B criterion invented."""
    if any(p == DIRECTORY or p.startswith(DIRECTORY + '/') or DIRECTORY.startswith(p + '/') for p in files) or (Path(root) / DIRECTORY).exists():
        raise ValueError('captured source occupies block source output namespace')
    source = placement_contract.validate_source(strict_json(_read(root, plan['placement']['block_source'])))
    groups = placement_job.normalize_intent(strict_json(_read(root, plan['placement']['intent'])),
                                          strict_json(_read(root, plan['placement']['groups'])))['groups']['groups']
    if {g['name']: (set(g['refs']), {'member': g['anchor']}) for g in groups} != {
            b['id']: (set(b['references']), b['anchor']) for b in source['allocation']['blocks']}:
        raise ValueError('block source membership/member anchors differ from prepared groups')


def paths(candidate, scope, plan):
    board = scope['board']
    return dict(board=board, project=str(Path(board).with_suffix('.kicad_pro')),
        rules=str(Path(board).with_suffix('.kicad_dru')) if (Path(candidate) / board).with_suffix('.kicad_dru').exists() else None,
        source=plan['placement']['block_source'], intent=plan['placement']['intent'],
        groups=plan['placement']['groups'], locality=locality_path(scope, candidate))


def _inputs(root, names):
    raw = {k: _read(root, v) for k, v in names.items() if v is not None}
    # Rules absence is an identity fact too, not permission to ignore a late file.
    if names['rules'] is None and (Path(root) / names['board']).with_suffix('.kicad_dru').exists():
        raise ValueError('block source native rules presence changed')
    normalized = placement_job.normalize_intent(strict_json(raw['intent']), strict_json(raw['groups']),
        locality=strict_json(raw['locality']) if 'locality' in raw else None,
        board_sha256=placement_job.sha256_bytes(raw['board']), groups_sha256=placement_job.sha256_bytes(raw['groups']))
    source = placement_contract.validate_source(strict_json(raw['source']), normalized)
    return raw, source, normalized


def _pins(raw, names):
    return {k: None if v is None else dict(path=v, sha256=placement_job.sha256_bytes(raw[k]))
            for k, v in names.items()}


def _outputs(names):
    outputs = {name + '.json': 'out/' + name + '.json' for name in ('contract', 'binding', 'production')}
    for key in ('board', 'project', 'rules'):
        if names[key] is not None:
            outputs['original/board' + Path(names[key]).suffix] = 'out/original/board' + Path(names[key]).suffix
    return {DIRECTORY + '/' + target: produced for target, produced in outputs.items()}


def _spec(names, mount):
    args = []
    for key, value in names.items():
        if value is not None:
            args += ['--' + key, value]
    return validate_step(dict(schema='agent-ee.stage-step/1', stage='schematic', step=STEP,
        argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
              '-m', 'agent_ee.block_source_stage', *args],
        mounts=[dict(source=str(mount), at='/tool/agent-ee')], source='/tool/agent-ee',
        version_argv=None, inputs=[v for v in names.values() if v is not None],
        writes=_outputs(names), artifacts=['out'], adoptable_exits=[0], timeout_s=TIMEOUT), host=False)


def configuration(source_root, candidate, scope, plan):
    if (Path(candidate) / DIRECTORY).exists():
        raise ValueError('block source output namespace already exists')
    return _spec(paths(candidate, scope, plan), snapshot(source_root))


def _binding(binding, contract, normalized, facts_sha256=None):
    """Reconstruct the existing binding; do not trust a recomputed self-hash alone."""
    original = deepcopy(binding['original_capture'])
    for fp in original['footprints']:
        if fp['uuid'] in binding['references']:
            fp['ref'] = fp.pop('native_ref')
    if facts_sha256 is not None and placement_job.sha256_bytes(canonical(original)) != facts_sha256:
        raise ValueError('block source original facts identity differs')
    records = []
    for fp in original['footprints']:
        alias = binding['references'].get(fp['uuid'], fp['ref'])
        role = 'fixed_copper_mark' if fp['uuid'] in binding['references'] else (
            'fixed_body' if alias in binding['fixed_refs'] else 'movable')
        records.append(dict(uuid=fp['uuid'], native_ref=fp['ref'], role=role))
    fixed_order = {ref: i for i, ref in enumerate(binding['fixed_refs'])}
    records.sort(key=lambda r: fixed_order.get(binding['references'].get(r['uuid'], r['native_ref']), len(fixed_order)))
    if placement_contract.bind(original, contract, normalized, records) != binding:
        raise ValueError('block source prepared binding differs from declared basis')


def produce(candidate, out, names, *, clock):
    """Private fixed-stage body. Publication follows successful bounded native capture."""
    candidate, out = Path(candidate), Path(out)
    if (candidate / DIRECTORY).exists():
        raise ValueError('block source output namespace already exists')
    raw, source, normalized = _inputs(candidate, names)
    policy = identity(policy_files())
    contract = placement_contract.materialize_source(source, placement_job.sha256_bytes(raw['board']))
    normalized['block_contract'] = contract
    _, record = placement_job.capture(candidate / names['board'], normalized, clock=clock)
    binding = record.get('block_contract')
    if binding is None:
        raise ValueError('native capture omitted requested block source binding')
    _binding(binding, contract, normalized, record['facts_sha256'])
    if _inputs(candidate, names)[0] != raw or identity(policy_files()) != policy:
        raise ValueError('block source inputs or checking policy changed during native capture')
    placement_job._check_stop(clock, None)
    # No output directory or partial successful binding is published on refusal.
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'contract.json', contract)
    write_json(out / 'binding.json', binding)
    original = out / 'original'
    original.mkdir()
    for key in ('board', 'project', 'rules'):
        if key in raw:
            (original / ('board' + Path(names[key]).suffix)).write_bytes(raw[key])
    payload = {p.relative_to(out).as_posix(): digest(p) for p in out.rglob('*') if p.is_file()}
    write_json(out / 'production.json', dict(schema='agent-ee.block-source-production/1',
        inputs=_pins(raw, names), source_sha256=identity(source), contract_sha256=identity(contract),
        binding_sha256=binding['binding_sha256'], groups_sha256=identity(normalized['groups']),
        normalized_sha256=identity(normalized), policy_sha256=policy, facts_sha256=record['facts_sha256'],
        outputs=payload))


def verify(out, candidate, scope, plan):
    """Revalidate the actual completed schematic producer and predecessor/adoption chain."""
    report = retained_run.snapshot(Path(out))
    stage = retained_run.completed_stage(Path(out), 'schematic', report)
    scaffold = retained_run.completed_stage(Path(out), 'scaffold', report)
    if stage is None or scaffold is None:
        raise ValueError('block source requires a completed schematic producer')
    names = paths(candidate, scope, plan)
    raw, source, normalized = _inputs(candidate, names)
    chain = sorted((stage.directory / 'receipts').iterdir(), key=lambda p: int(p.name))
    if [p.name for p in chain] != ['0', '1', '2', '3']:
        raise ValueError('block source schematic receipt chain differs')
    previous, other = file_tree(scaffold.candidate)
    if other:
        raise ValueError('block source predecessor contains unsafe files')
    for i, path in enumerate(chain):
        receipt = validate_receipt(path, binding=dict(implementation=1,
            source_candidate_id=identity(previous), receipt_index=i))
        if receipt['status'] != 'completed' or receipt['inputs'] != previous:
            raise ValueError('block source producer predecessor is not adopted/completed')
        previous = after_tree(receipt)
    if file_tree(stage.candidate) != (previous, []):
        raise ValueError('block source schematic adoption differs from receipt outputs')
    spec = receipt['spec']
    if len(spec['mounts']) != 1 or spec != _spec(names, spec['mounts'][0]['source']):
        raise ValueError('block source receipt is not the fixed producer')
    expected_tool = dict(path='/tool/agent-ee', sha256=identity(_files(SOURCE_ROOT)), files=len(_files(SOURCE_ROOT)))
    if receipt['tool']['source_tree'] != expected_tool or receipt['tool_after']['source_tree'] != expected_tool:
        raise ValueError('block source producer source identity changed')
    for target, produced in spec['writes'].items():
        if digest(Path(candidate) / target) != receipt['outputs'][target] or receipt['artifacts'][produced] != receipt['outputs'][target]:
            raise ValueError('block source adopted evidence changed: ' + target)
    production = read_json(Path(candidate) / DIRECTORY / 'production.json')
    contract = placement_contract.materialize_source(source, placement_job.sha256_bytes(raw['board']))
    binding = read_json(Path(candidate) / DIRECTORY / 'binding.json')
    normalized['block_contract'] = contract
    expected = dict(schema='agent-ee.block-source-production/1', inputs=_pins(raw, names),
        source_sha256=identity(source), contract_sha256=identity(contract), binding_sha256=binding['binding_sha256'],
        groups_sha256=identity(normalized['groups']), normalized_sha256=identity(normalized),
        policy_sha256=identity(policy_files()), facts_sha256=production['facts_sha256'],
        outputs={str(Path(target).relative_to(DIRECTORY)): digest(Path(candidate) / target)
                 for target in _outputs(names) if not target.endswith('/production.json')})
    if (production != expected or read_json(Path(candidate) / DIRECTORY / 'contract.json') != contract
            or binding['original_capture']['board_sha256'] != placement_job.sha256_bytes(raw['board'])):
        raise ValueError('block source production no longer matches captured source/native/policy identities')
    for key in ('board', 'project', 'rules'):
        if key in raw and _read(candidate, DIRECTORY + '/original/board' + Path(names[key]).suffix) != raw[key]:
            raise ValueError('block source original sibling differs: ' + key)
    _binding(binding, contract, normalized, production['facts_sha256'])
    return binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('board', 'project', 'source', 'intent', 'groups'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--rules')
    parser.add_argument('--locality')
    names = vars(parser.parse_args(argv))
    clock = placement_job.Clock(TIMEOUT)
    # The existing exact outer-layout witness refuses standalone host invocation.
    from .forward_annotation import _StageExecution
    _StageExecution()
    produce('/candidate', '/work/out', names, clock=clock)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
