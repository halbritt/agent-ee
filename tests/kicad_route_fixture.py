"""A coordinator-owned stand-in for kicad-route, for adapter exit-policy tests only.

It writes the pinned tool's OUT layout and report shapes for a named scenario.
It does not route, validate or run KiCad: its results test how the adapter
classifies and retains an attempt, never what kicad-route or KRT would do.
"""
import hashlib
import json
import os
from pathlib import Path

FAKE_PIN = 'f' * 40

SCRIPT = r'''#!/usr/bin/python3
import datetime, hashlib, json, os, shutil, signal, subprocess, sys, time
from pathlib import Path

RAW_STAGE = 'backend_output_before_wrapper_lock_restore_and_native_refill'

ROOT = Path(__file__).resolve().parents[1]
scenario = json.loads((ROOT / 'scenario.json').read_text())
name = scenario['name']
if name == 'argparse':
    print('usage: kicad-route [-h] [--out OUT] ...', file=sys.stderr)
    sys.exit(2)
args = sys.argv[1:]
assert args[:2] == ['run', '--out'] and args[3] == '--json', args
out = Path(args[2])
# --backend-arg=VALUE tokens, as the adapter passes KRT layer flags (AGENTEE-231).
backend_args = [a.split('=', 1)[1] for a in args if a.startswith('--backend-arg=')]
paired_args = [a for a in args if not a.startswith('--backend-arg=')]
opts = dict(zip(paired_args[4::2], paired_args[5::2]))
area_bound = float(opts['--pour-area-loss']) if '--pour-area-loss' in opts else None


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def prepare(path, text):
    """Make a prepared-input entry: text, None (absent), 'link:TARGET' or 'dir:'."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    path.unlink(missing_ok=True)
    if isinstance(text, str) and text.startswith('link:'):
        path.symlink_to(text[len('link:'):])
    elif text == 'dir:':
        path.mkdir()
    elif text is not None:
        path.write_text(text)


src, board, sch = Path(opts['--dir']), opts['--board'], opts['--schematic']
# AGENTEE-442: scenario['read_mount'] stands in for an adapter that passed the read-only
# mount itself (the first GIT_CEILING_DIRECTORIES entry) as --dir, as it did before it
# staged the original; scenario['grandparent_record'] plants a run record two levels
# above --dir, as a run's candidate laid out beside its attempt.json would have.
if scenario.get('read_mount'):
    src = Path(os.environ['GIT_CEILING_DIRECTORIES'].split(':')[0]) / src.relative_to(
        Path(os.environ['GIT_CEILING_DIRECTORIES'].split(':')[1]))
if scenario.get('grandparent_record'):
    (src.parent.parent / scenario['grandparent_record']).write_text('{}\n')
project, label = opts['--name'], opts['--label']
state = Path(os.environ['KICAD_ROUTE_STATE'])
(out / '.kicad-config').mkdir(parents=True, exist_ok=True)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
run = out / 'runs' / project / label / stamp
run.mkdir(parents=True)
(run.parent / 'latest').symlink_to(run.name)
print(f'[route] {opts["--backend"]} on {project} -> {os.path.relpath(run)}', file=sys.stderr, flush=True)
ref = run / 'reference' / src.name
ref.mkdir(parents=True)
for f in sorted(src.iterdir()):
    if f.is_file() and (f.suffix.startswith('.kicad_') or f.name.endswith('-lib-table')):
        shutil.copy2(f, ref / f.name)
if name == 'reference_differs':
    (ref / sch).write_text('(kicad_sch altered)\n')
files = {p.name: sha(p) for p in sorted(ref.iterdir())}
write(run / 'snapshot.json', {'project': project, 'files': files})
fingerprint = hashlib.sha256(''.join(f'{n}\0{d}\n' for n, d in sorted(files.items())).encode()).hexdigest()
# As the pin's facts.py: the input board's hash and its tracks (and vias, when a scenario states them).
via_facts = 'vias_by_label' in scenario or 'reference_vias' in scenario
write(run / 'facts/reference-facts.json', {'nets': {}, 'tracks': scenario.get('reference_tracks', []),
                                           'sha256': scenario.get('reference_sha256', files.get(board)),
                                           **({'vias': scenario.get('reference_vias', [])} if via_facts else {})})
if name == 'early_exit':
    sys.exit('could not read the input board: see facts/reference-facts.log')
if name in ('refused', 'unknown_refusal_version', 'human_refusal'):
    shutil.rmtree(run)
    (run.parent / 'latest').unlink()
    run.parent.rmdir()
    refusal = {'schema': 'kicad-route/refusal/1', 'kind': 'refusal', 'exit_code': 4,
               'command': 'run', 'reason_class': 'ledger', 'message': 'identical attempt already finished incomplete'}
    if name == 'unknown_refusal_version':
        refusal['schema'] = 'kicad-route/refusal/999'
    print('[route] refused: old human output' if name == 'human_refusal' else json.dumps(refusal), flush=True)
    sys.exit(4)
settings = {'backend': opts['--backend'], 'budget_s': int(opts['--budget']), 'protect': opts['--protect'],
            'max_iterations': None, 'passes': None, 'default_clearance_mm': None, 'clearance_margin_mm': None,
            'pour_area_loss_pct': area_bound, 'pour_min_spokes': None, 'backend_args': backend_args,
            'backend_env': {'KICAD_X': '1'} if name == 'backend_env' else {}, 'reserve_spokes': None,
            'stall_s': None, 'barrier': None, 'mode': 'all', 'rule_floor_sha256': None}
if '--experiment-policy' in opts and scenario.get('settings_policy') != 'omit':
    # As kicad-route a0ed434 onward: the loaded declaration's common.sha256_of, in the run settings.
    settings['experiment_policy_sha256'] = hashlib.sha256(json.dumps(
        {**json.loads(Path(opts['--experiment-policy']).read_bytes()),
         **scenario.get('settings_policy_edit', {})}, sort_keys=True).encode()).hexdigest()
paired = opts.get('--diff-pairs') == 'auto'
plans = scenario.get('pairs', [])
if paired:
    settings.update(diff_pairs='auto', pairs=plans)
attempt = {'schema': 'kicad-route/attempt/2' if paired else 'kicad-route/attempt/1',
           'backend': opts['--backend'], 'project': project, 'board': board, 'schematic': sch,
           'input_fingerprint': fingerprint, 'source': {'source_dir': str(src)},
           'reference_dir': f'reference/{src.name}', 'candidate_dir': f'candidate/{src.name}',
           'reference_facts': 'facts/reference-facts.json', 'baseline': {'report': None},
           'scope': {'nets': [], 'mode': 'all'}, 'settings': settings, 'floor': None, 'rules_files': {}}
if '--experiment-policy' in opts and scenario.get('attempt_policy') != 'omit':
    # As kicad-route a0ed434 onward: the loaded declaration, in attempt.json.
    attempt['experiment_policy'] = {**json.loads(Path(opts['--experiment-policy']).read_bytes()),
                                    **scenario.get('attempt_policy_edit', {})}
if paired:
    attempt.update(pairs=[{**p, 'initially_connected': False} for p in plans],
                   pair_routing={p['p']: {'status': 'COUPLED'} for p in plans})
if name == 'missing_attempt_schema':
    del attempt['schema']
if name == 'missing_attempt_rules':
    del attempt['rules_files']
if name == 'unknown_attempt_version':
    attempt['schema'] = 'kicad-route/attempt/999'
write(run / 'attempt.json', attempt)
if name == 'sleep':
    child = subprocess.Popen(['/bin/sleep', '600'])
    (out / 'child.pid').write_text(str(child.pid))
    time.sleep(600)
if name in ('mutate_state', 'write_state_attempt'):
    try:
        (state / '.venv-krt/written-by-router').write_text('x')
        outcome = 'written'
    except OSError as exc:
        outcome = type(exc).__name__ + ':' + str(exc.errno)
    write(run / 'validate/state-write.json', {'outcome': outcome})
if name == 'touch_input':
    (src / board).write_text('changed during the attempt\n')
# AGENTEE-442 review H3: a router that removes its staged input root, or replaces it with
# a link, after reading it (the adapter stages a top-level project at <root>/<project>).
if name in ('remove_input_root', 'link_input_root'):
    snapshot = run / 'moved-input'
    shutil.copytree(src, snapshot)
    shutil.rmtree(src.parent)
    if name == 'link_input_root':
        src.parent.symlink_to(run)
    src = snapshot
process_ok = name != 'backend_failure'
cand = run / 'candidate' / src.name
if process_ok:
    shutil.copytree(ref, cand)
    marker = 'EXIT3-SENTINEL' if name == 'validation_failed' else 'routed by fixture'
    with open(cand / board, 'a') as f:
        f.write(f'\n# {marker}\n')
    # As kicad-cli pcb drc --save-board does at the pin.
    (cand / (Path(board).stem + '.kicad_prl')).write_text('{"board": {}}\n')
    # As copy_project/make_snapshot at the pin: a link beside the project for each
    # ${KIPRJMOD}/../<dir> library directory, reading the resolved input path
    # (/candidate/<dir> in the sandbox; the fixture's --dir parent here).
    for where, entry, kind, target in scenario.get('siblings', []):
        path = run / where / entry
        target = target.format(root=src.parent)
        if kind == 'link':
            path.symlink_to(target)
        elif kind == 'dir':
            path.mkdir()
        elif kind == 'file':
            path.write_text('stand-in\n')
        elif kind == 'project_link':  # the project directory itself replaced by a link
            os.rename(path, run / f'moved-{where}')
            path.symlink_to(run / f'moved-{where}')
    checks = [{'check': 'footprints', 'status': 'PASS'}]
    if area_bound is not None and not scenario.get('no_pour'):
        # The pin's pour_integrity record and delta, graded against the declared bound.
        layers = scenario.get('pour_layers', [{'net_layer': 'GND@In1.Cu', 'area_mm2': [100.0, 100.0],
                                                'area_delta_pct': 0.0}])
        over = [r for r in layers if r['area_delta_pct'] is not None and -r['area_delta_pct'] > area_bound]
        checks.append({'check': 'pour_integrity', 'status': 'FAIL' if over else 'PASS', 'detail': 'fixture'})
        write(run / 'validate/pour-integrity.json', {'check': 'pour_integrity', 'area_loss_limit_pct': area_bound,
                                                     'layers': layers})
    write(run / 'validate/preservation.json', checks)
    (run / 'validate/candidate-drc.log').write_text('fixture DRC log\n')
    (run / 'work').mkdir()
    (run / 'work/krt.log').write_text('fixture backend log\n')
    shutil.copy2(cand / board, run / 'work' / board)
routing = {'COMPLETE': {'status': 'COMPLETE', 'unconnected_in_scope': 0, 'unconnected_total': 0}, 'INCOMPLETE':
           {'status': 'INCOMPLETE', 'unconnected_in_scope': 2, 'unconnected_total': 2, 'unrouted_nets': ['/IN']},
           'NOT_RUN': {'status': 'NOT_RUN', 'reasons': ['backend produced no candidate']}}
routing_status = {'incomplete': 'INCOMPLETE', 'exit0_incomplete': 'INCOMPLETE', 'exit2_not_run': 'NOT_RUN',
                  'backend_failure': 'NOT_RUN'}.get(name, 'COMPLETE')
board_sha = sha(cand / board) if process_ok else None
validation = {'status': 'PASS', 'reasons': [], 'delivered_board_sha256': board_sha,
              'validated_board_sha256': board_sha, 'candidate_board_sha256': board_sha,
              'refill_modified_board': False, 'restored_project_files': []}
# The pin's native terminal preservation (KICADROUTE-98), from native partitions.
deficit = 0 if routing_status == 'COMPLETE' else 2
validation['net_completion'] = {'status': 'PASS', 'nets_connected_in_input': 0, 'nets_lost': [],
                                'split_components': []}
validation['native_terminal_evidence'] = {
    'reference': os.path.relpath(run / 'validate/reference-terminal-components.json'),
    'candidate': os.path.relpath(run / 'validate/candidate-terminal-components.json'),
    'metrics': {'reference': {'terminal_nets': 2, 'incomplete_nets': 2, 'component_deficit': 4},
                'candidate': {'terminal_nets': 2, 'incomplete_nets': 1 if deficit else 0,
                              'component_deficit': deficit}}}
if name == 'terminal_split':
    validation['net_completion']['split_components'] = [{'net': '/IN', 'pads': ['u1', 'u2']}]
if name == 'terminal_missing':
    del validation['net_completion']
if name == 'terminal_complete_split':
    validation['native_terminal_evidence']['metrics']['candidate'].update(incomplete_nets=1, component_deficit=1)
if name == 'refill_modified':
    validation.update(delivered_board_sha256='1' * 64, refill_modified_board=True)
if name == 'validation_failed':
    validation.update(status='FAIL', reasons=['1 new DRC violations: clearance 1'])
if name == 'backend_failure':
    validation = {'status': 'NOT_RUN', 'reasons': ['backend produced no candidate']}
if process_ok and any(c['status'] == 'FAIL' for c in checks):
    validation.update(status='FAIL', reasons=['preservation pour_integrity: fixture'])
if process_ok and area_bound is not None:
    write(run / 'validate/candidate-facts.json', {'sha256': board_sha, 'tracks': scenario.get(
        'tracks', [{'kind': 'segment', 'layer': 'F.Cu', 'net': '/IN', 'uuid': 'fixture-track-1'}]),
        **({'vias': scenario['vias_by_label'].get(label, scenario['vias_by_label'].get('*', []))}
           if 'vias_by_label' in scenario else {})})
if name == 'board_hash_mismatch':
    validation['delivered_board_sha256'] = '0' * 64
code = {'COMPLETE': 0, 'INCOMPLETE': 2, 'NOT_RUN': 2}[routing_status]
code = 1 if not process_ok else 3 if validation['status'] != 'PASS' else code
if name == 'exit0_incomplete':
    code = 0
rel_cand = os.path.relpath(cand)
candidate = {'dir': rel_cand, 'board': os.path.join(rel_cand, board)} if process_ok else None
if name == 'escaping_candidate':
    candidate = {'dir': '/etc', 'board': '/etc/passwd'}
result = {'schema': 'kicad-route/route/1', 'measurement': False, 'evidence': {},
          'tool_identity': {}, 'sandbox': {}, 'kind': 'route', 'backend': {'name': opts['--backend'], 'version': '0.0-fixture', 'build': None,
          'commit': None}, 'input': {'project': project, 'board': board, 'fingerprint': fingerprint},
          'settings': settings, 'scope': {'mode': 'all', 'nets': 0}, 'unsupported': [], 'floor': None,
          'process': {'status': 'PASS' if process_ok else 'FAIL', 'reasons': [] if process_ok else ['KRT exited 1']},
          'routing': routing[routing_status], 'validation': validation, 'candidate': candidate,
          'run_dir': os.path.relpath(run), 'runtime_s': 0.1, 'tool_versions': {'kicad-cli': None},
          'finished_utc': '2026-10-17T00:00:00+00:00', 'exit_code': code, 'ledger': {}}
if paired:
    result.update(schema='kicad-route/route/2', pairs=[{**p, 'routed': True} for p in plans])
# The pin's nested additions: measurement qualification on every process record, and the
# runtime an owned-engine result executed (the selected router generation, else legacy).
result['process']['measurement_qualification'] = {
    'measurement': False, 'build_reproducible': None, 'reasons': ['no repeat evidence for this build']}
if opts['--backend'] == 'builtin':
    try:
        generation = json.loads((state / 'manifest.json').read_text())['krt']['generation']
    except (OSError, ValueError, KeyError, TypeError):
        generation = None
    result['backend']['runtime'] = (
        {'source': str(state / generation / 'router'), 'venv': str(state / generation / 'venv'),
         'generation': str(state / generation)} if generation else
        {'source': str(ROOT / 'src/kicad_route/router'), 'venv': str(state / '.venv-krt'), 'generation': None})
    if name == 'runtime_elsewhere':
        result['backend']['runtime'] = {'source': '/elsewhere/router', 'venv': '/elsewhere/venv',
                                        'generation': '/elsewhere'}
    if name == 'runtime_extra_key':
        result['backend']['runtime']['python'] = '/usr/bin/python3'
# A backend without an owned engine never writes backend.runtime, whatever its value.
FOREIGN_RUNTIME = {'foreign_runtime_wellformed': {'source': '/opt/router', 'venv': '/opt/venv', 'generation': None},
                   'foreign_runtime_null': None, 'foreign_runtime_empty': {},
                   'foreign_runtime_malformed': {'unexpected': 'not-a-runtime'}}
if name in FOREIGN_RUNTIME and opts['--backend'] != 'builtin':
    result['backend']['runtime'] = FOREIGN_RUNTIME[name]
REPRODUCIBLE = {'reproducible_one': 1, 'reproducible_zero': 0, 'reproducible_float': 0.0, 'reproducible_true': True,
                'reproducible_false': False}
if name in REPRODUCIBLE:
    result['process']['measurement_qualification']['build_reproducible'] = REPRODUCIBLE[name]
if name == 'measurement_missing':
    del result['process']['measurement_qualification']
if name == 'measurement_disagrees':
    result['process']['measurement_qualification']['measurement'] = True
# The pin adds copper custody to a builtin attempt whose process passed (AGENTEE-450).
if opts['--backend'] == 'builtin' and process_ok and name != 'custody_missing':
    if name == 'custody_unavailable':
        result['copper_ownership'] = {'status': 'unavailable', 'reasons': ['fixture custody unavailable'],
                                      'replay_authority': False}
    else:
        write(run / 'validate/copper-ownership.json', {'schema': 'kicad-route/copper-ownership/1',
                                                       'replay_authority': False, 'board_sha256': board_sha})
        custody = sha(run / 'validate/copper-ownership.json')
        result['copper_ownership'] = {'status': 'bound', 'record': 'validate/copper-ownership.json',
                                      'sha256': '0' * 64 if name == 'custody_forged' else custody,
                                      'coverage': 'current_run_only_input_fixed', 'replay_authority': False}
        result['evidence'] = {'records': {'validate/copper-ownership.json': custody}}
        if name == 'custody_authority':
            result['copper_ownership']['replay_authority'] = True
if name == 'result_exit_mismatch':
    result['exit_code'] = 2
if name == 'unknown_result_version':
    result['schema'] = 'kicad-route/route/999'
if name == 'missing_result_version':
    del result['schema']
if name == 'unknown_key':
    result['schema_version'] = 1
if name == 'complete_unconnected':
    result['routing'] = {**result['routing'], 'unconnected_total': 1}
if name == 'env':
    write(run / 'validate/env.json', {'environ': dict(os.environ), 'cwd': os.getcwd(),
                                      'dont_write_bytecode': sys.flags.dont_write_bytecode,
                                      'pycache_prefix': sys.pycache_prefix})
# SYNTHETIC routability-experiment/1 sidecar (AGENTEE-442), written by this
# fixture, never by the KICADROUTE-91 producer. It has the run-relative reference
# and the full shape of kicad-route a0ed434's sidecar (schemas/routability-
# experiment-1.json), and, when it claims comparison_eligible, the evidence the
# adapter requalifies: a synthetic effective-configuration manifest and request
# it seals, the result.json process binding of that manifest, and the attempt
# record and facts it was built from. Values the adapter does not read are
# shape-valid stand-ins. scenario['replay'] = {'sidecar': text, 'copy': text or
# None} instead writes REAL kicad-route bytes verbatim (tests/fixtures/k91-real),
# with scenario['payload'] {run-relative name: text} and
# scenario['effective_binding'] the real run's sealed files and binding; edits
# still apply. scenario['self_cycle'] points attempt.json source_dir at the run's
# own candidate, as the K91 review's EI3 case does; 'source_dir', 'links',
# 'effective', 'artifacts' and replay['forge'] are described where they apply.
replay = scenario.get('replay')
experiment = scenario.get('experiment', 'ok' if '--experiment-policy' in opts or replay else None)
if experiment is not None and experiment != 'omit':
    if scenario.get('self_cycle'):
        attempt['source']['source_dir'] = str(cand.resolve())
        write(run / 'attempt.json', attempt)
    # scenario['source_dir'] is a format string over this run's paths (EI3 alias
    # cases); scenario['links'] {path: target} creates links the same way.
    paths = {'run': run, 'cand': cand.resolve(), 'out': out, 'src': src}
    for link, target in scenario.get('links', {}).items():
        Path(link.format(**paths)).symlink_to(target.format(**paths))
    if 'source_dir' in scenario:
        attempt['source']['source_dir'] = scenario['source_dir'].format(**paths)
        write(run / 'attempt.json', attempt)
    # As kicad-route a0ed434: the attempt names the board's project file, its custom
    # rules file and the rule files beside it KiCad does not load (cli.rules_files;
    # scenario['rules_files'] overrides), the wrapper prepares the backend input in
    # work/input/<project dir> (scenario['prepared'] {name: text} stands in for
    # protection and preprocessing), and the effective-configuration manifest
    # captures that prepared board and the files beside it by suffix.
    stem = Path(board).stem
    attempt['rules_files'] = {'project': f'{stem}.kicad_pro',
                              'custom_rules': f'{stem}.kicad_dru' if f'{stem}.kicad_dru' in files else None,
                              'not_loaded': sorted(n for n in files if Path(n).suffix in ('.kicad_pro', '.kicad_dru')
                                                   and Path(n).stem != stem),
                              **scenario.get('rules_files', {})}
    # scenario['attempt_board'] names another board in the attempt record (review H1,
    # ee442j: '.', '/' and other names that are not a plain file name); the sidecar
    # seals the record as written.
    attempt['board'] = scenario.get('attempt_board', attempt['board'])
    write(run / 'attempt.json', attempt)
    prepared = run / 'work/input' / src.name
    prepared.mkdir(parents=True, exist_ok=True)
    for f in sorted(ref.iterdir()):
        shutil.copy2(f, prepared / f.name)
    for rel, text in scenario.get('prepared', {}).items():
        prepare(prepared / rel, text)
    # A forged capture states null for a prepared file that is not regular (agent-510 R4:
    # kicad-route itself refuses such an input; a resealed copy claims it is absent).
    captured = {'board_sha256': sha(prepared / board),
                'rules': {suffix: sha(prepared / (stem + suffix)) if (prepared / (stem + suffix)).is_file() and
                          not (prepared / (stem + suffix)).is_symlink() else None
                          for suffix in ('.kicad_pro', '.kicad_dru')}}
    # The raw backend output kicad-route seals (a design file the adapter never retains);
    # the fixture's wrapper changes nothing, so it is the delivered board.
    raw_output = run / 'work/effective-configuration-output.kicad_pcb'
    if board_sha is not None:
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cand / board, raw_output)
    raw_sha = sha(raw_output) if raw_output.is_file() else None
    if not (run / 'validate/candidate-facts.json').exists():
        write(run / 'validate/candidate-facts.json', {'sha256': board_sha, 'tracks': [], 'vias': []})
    source_sha, producer_sha = (hashlib.sha256(f'SYNTHETIC {n}'.encode()).hexdigest() for n in ('source', 'producer'))
    manifest_name, request_name = 'work/effective-configuration.json', 'work/effective-configuration-request.json'
    write(run / request_name, {'schema': 'kicad-route/effective-configuration-request/1',
                               'source_sha256': source_sha, 'producer_sha256': producer_sha, 'field_names': [],
                               'input': captured, **scenario.get('request_manifest', {})})
    manifest = {'schema': 'kicad-route/effective-configuration/1', 'capture_status': 'complete',
                'coverage': 'resolved_outer_batch_only', 'stage': 'SYNTHETIC agent-ee fixture',
                'execution': {'later_subruns': 0, 'outer_batches': 1}, 'issues': [], 'not_captured': [],
                'configuration': {'layers': ['F.Cu', 'B.Cu']}, 'effective_environment': {}, 'runtime_policy': {},
                'routing_environment': {}, 'invocation': [], 'scope': [], 'rules': None, 'net_inventory': [],
                'request_sha256': sha(run / request_name), 'source_sha256': source_sha,
                'producer_sha256': producer_sha, 'requested_output': str(run / 'work/krt-out' / board),
                'input': captured, 'output': {'board_sha256': raw_sha, 'stage': RAW_STAGE}}
    manifest.update(scenario.get('manifest', {}))
    write(run / manifest_name, manifest)
    for rel, content in scenario.get('payload', {}).items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(content)
    result['process']['effective_configuration'] = scenario.get('effective_binding') or {
        'status': 'bound', 'reasons': [], 'replay_authority': False, 'sha256': sha(run / manifest_name),
        'request_sha256': sha(run / request_name), 'capture_status': 'complete',
        'coverage': 'resolved_outer_batch_only', 'captured_input_sha256': captured['board_sha256'],
        'backend_output_sha256': raw_sha, 'output_stage': RAW_STAGE,
        'wrapper_output_board_sha256': validation.get('delivered_board_sha256'),
        'wrapper_output_stage': 'after_wrapper_postprocessing_before_native_refill'}
    result['process']['effective_configuration'].update(scenario.get('binding', {}))
    policy_path = opts.get('--experiment-policy')
    declared = None
    if policy_path:
        declared = json.loads(Path(policy_path).read_bytes())
        (run / 'experiment-policy.json').write_text(json.dumps(declared, indent=2) + '\n')
        if experiment == 'rewrite_policy':
            Path(policy_path).write_text(json.dumps({**declared, 'experiment_class': 'historical-all-layer'},
                                                    sort_keys=True, separators=(',', ':')))
        if experiment == 'policy_copy_mismatch':
            (run / 'experiment-policy.json').write_text(json.dumps({**declared, 'allowed_via_spans': []}))
    if experiment == 'incompatible_policy':
        declared = {**(declared or {}), 'experiment_class': 'historical-all-layer'}
    policy = {'declared': declared,
              'declared_sha256': None if declared is None else
              hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest(),
              'experiment_class': None if declared is None else declared.get('experiment_class'),
              'physical_stack': ['F.Cu', 'B.Cu'],
              'input_rule_areas_sha256': None if declared is None else declared.get('rule_areas_sha256'),
              'input_rule_areas_coverage': {}}
    sidecar = run / 'routability-experiment.json'

    def digest_of(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    custom = attempt['rules_files']['custom_rules']
    identity = {
        'input': {'fingerprint': fingerprint, 'board': {'name': board, 'sha256': files.get(board)},
                  'project': {'name': f'{stem}.kicad_pro', 'sha256': files.get(f'{stem}.kicad_pro')},
                  'rules': {'name': custom, 'sha256': files.get(custom) if isinstance(custom, str) else None,
                            'not_loaded': attempt['rules_files']['not_loaded']},
                  'files': files, 'snapshot_files': files,
                  'netlist_sha256': None, 'nets_sha256': None, 'placement_sha256': None,
                  'mechanical_sha256': None, 'setup_sha256': None, 'native_facts_kicad_version': None,
                  'source': attempt['source']},
        'origin': {'root_input_fingerprint': fingerprint, 'depth': 0, 'predecessors': []},
        'scope': attempt['scope'], 'protection': {'mode': settings['protect'], 'only_sha256': None},
        'request': {'fixture': 'SYNTHETIC agent-ee coordinator fixture (AGENTEE-442)',
                    **{k: v for k, v in settings.items() if k not in ('budget_s', 'stall_s')},
                    'experiment_policy_sha256': policy['declared_sha256']},
        'policy': policy,
        'budgets': {'backend_s': settings['budget_s'], 'stall_s': None, 'command_budget_s': None, 'grace_s': None},
        'native': {'kicad_cli': None, 'pcbnew_build': None, 'terminal_producer': None,
                   'terminal_native_identity': None},
        'build': {'wrapper': None, 'backend': {}, 'execution_build_id': None, 'executed_source_sha256': source_sha,
                  'producer_sha256': producer_sha, 'state_manifest_sha256': None},
        'effective': {**{k: manifest[k] for k in ('schema', 'capture_status', 'coverage', 'stage', 'execution',
                                                   'not_captured', 'configuration', 'effective_environment',
                                                   'runtime_policy', 'routing_environment', 'invocation',
                                                   'scope', 'input')},
                      'rules_sha256': None, 'net_inventory_sha256': None}}
    identity.update(scenario.get('identity', {}))
    # scenario['input'] {key: value} overrides identity.input members (named-input cases).
    identity['input'].update(scenario.get('input', {}))
    identity['effective'].update(scenario.get('effective', {}))
    if experiment == 'no_policy_identity':
        del identity['policy']
    copy = run / 'experiment-policy.json'
    reasons = [] if declared is not None else ['no experiment policy was declared (--experiment-policy)']
    # As kicad-route bd1e633 experiment._origin without a readable predecessor: an input
    # laid out as a run's candidate, or beside a run record, is not an original project.
    if src.parent.name == 'candidate' or any((src.parent.parent / n).exists() for n in ('attempt.json', 'result.json')):
        reasons.append('origin predecessor is missing or does not own this input')
    sealed = ['attempt.json', 'facts/reference-facts.json', 'validate/candidate-facts.json', manifest_name,
              request_name, 'experiment-policy.json', 'work/effective-configuration-output.kicad_pcb']
    body = {'schema': 'routability-experiment/2' if experiment == 'incompatible_schema' else
            'routability-experiment/1',
            'identity': identity,
            'identity_sha256': digest_of(identity),
            'comparison_eligible': not reasons, 'ineligible_reasons': reasons,
            'policy_qualification': {'status': 'PASS' if declared is not None else 'NOT_RUN', 'reasons': [],
                                     'policy_sha256': None, 'rule_areas_sha256': None, 'coverage': None,
                                     'actual_trace_layers': [], 'actual_via_spans': [],
                                     'effective_layers': ['F.Cu', 'B.Cu'], 'effective_via': {}},
            'native_terminals': {},
            'candidate': {'raw_backend_output_board_sha256': raw_sha,
                          'backend_output_board_sha256': validation.get('delivered_board_sha256'),
                          'pre_refill_board_sha256': validation.get('delivered_board_sha256'),
                          'candidate_board_sha256': board_sha, 'candidate_copper_signature': None,
                          'validated_board_sha256': board_sha, 'file_sha256': board_sha},
            'outcome': {'process': result['process']['status'], 'routing': routing[routing_status]['status'],
                        'validation': validation['status'], 'unconnected_total': 0, 'backend_runtime_s': 0.1,
                        'wall_clock_stop': False},
            'artifacts': {rel: sha(run / rel) if (run / rel).is_file() else None for rel in sealed},
            'replay_authority': False}
    body['candidate'].update(scenario.get('seals', {}))
    # scenario['artifacts'] [[name, digest | None | 'delete' | 'rename:NEW']] edits the
    # digests before sealing (artifact-binding cases).
    for rel, op in scenario.get('artifacts', []):
        if op == 'delete' or (isinstance(op, str) and op.startswith('rename:')):
            value = body['artifacts'].pop(rel)
            if op != 'delete':
                body['artifacts'][op[len('rename:'):]] = value
        else:
            body['artifacts'][rel] = op
    if replay is not None:
        sidecar.write_text(replay['sidecar'])
        copy.unlink(missing_ok=True)
        if replay.get('copy') is not None:
            copy.write_text(replay['copy'])
        # A self-consistent non-identity forgery: the replayed sidecar's digests rewritten
        # to this run's files, replay['input'] also restating this run's input, and resealed.
        if replay.get('forge'):
            forged = json.loads(replay['sidecar'])
            forged['artifacts'] = {rel: sha(run / rel) if (run / rel).is_file() else None
                                   for rel in forged['artifacts']}
            if replay.get('input'):
                forged['identity']['input'].update(fingerprint=fingerprint, files=files, snapshot_files=files,
                                                   board={'name': board, 'sha256': files.get(board)},
                                                   source=attempt['source'])
                forged['identity']['origin']['root_input_fingerprint'] = fingerprint
            forged['identity_sha256'] = digest_of(forged['identity'])
            sidecar.write_text(json.dumps(forged, indent=2) + '\n')
    else:
        sidecar.write_text('not json {' if experiment == 'malformed' else json.dumps(body, indent=2) + '\n')
    # scenario['remove'] [run-relative names] deletes sealed files after sealing;
    # scenario['prepared_after'] {name: text | None | 'link:TARGET' | 'dir:'} then changes
    # the prepared input the adapter observes (prepare()).
    for rel in scenario.get('remove', []):
        (run / rel).unlink()
    for rel, text in scenario.get('prepared_after', {}).items():
        prepare(prepared / rel, text)
    # Byte edits [file, old, new, 'first'|'last'] of one occurrence (AGENTEE-442
    # duplicate-key probes); an edit that finds nothing writes an unmistakable file.
    for file, before, after, which in scenario.get('edits', []):
        if file == 'result.json':
            continue
        target = run / file
        text = target.read_text()
        head, found, tail = text.partition(before) if which == 'first' else text.rpartition(before)
        target.write_text(head + after + tail if found else 'fixture edit not found')
    result['experiment'] = {'path': 'routability-experiment.json', 'sha256': sha(sidecar),
                            'schema': 'routability-experiment/1'}
    if experiment == 'unknown_schema':
        result['experiment']['schema'] = 'routability-experiment/2'
    if experiment == 'malformed_reference':
        del result['experiment']['sha256']
    if experiment == 'escape':
        result['experiment']['path'] = '../routability-experiment.json'
    if experiment == 'cwd_relative':
        result['experiment']['path'] = os.path.relpath(sidecar)
    if experiment == 'tamper' and replay is not None:
        sidecar.write_text(replay['sidecar'].replace('"replay_authority": false', '"replay_authority": true'))
    elif experiment == 'tamper':
        sidecar.write_text(json.dumps({**body, 'identity': {}}) + '\n')
    if experiment == 'missing_file':
        sidecar.unlink()
    if experiment == 'orphan':
        del result['experiment']
    if experiment == 'orphan_copy':
        sidecar.unlink()
        del result['experiment']
    if experiment == 'orphan_link':
        sidecar.unlink()
        sidecar.symlink_to(run / 'snapshot.json')
        del result['experiment']
    if experiment == 'second_sidecar':
        shutil.copyfile(sidecar, run / 'alternate.json')
        result['experiment']['path'] = 'alternate.json'
text = 'not json {' if name == 'malformed_json' else json.dumps(result, indent=2) + '\n'
for file, before, after, which in scenario.get('edits', []):
    if file == 'result.json':
        head, found, tail = text.partition(before) if which == 'first' else text.rpartition(before)
        text = head + after + tail if found else 'fixture edit not found'
(run / 'result.json').write_text(text)
print(text, flush=True)
(run / 'RESULT.md').write_text('# Routing attempt (fixture)\n')
with open(out / 'ledger.jsonl', 'a') as f:
    f.write(json.dumps({'run_dir': str(run), 'exit_code': code}) + '\n')
if name == 'two_attempts':
    (run.parent / '20260101T000000.000000Z').mkdir()
if name == 'latest_elsewhere':
    (run.parent / 'latest').unlink()
    (run.parent / 'latest').symlink_to('../../../elsewhere')
if name == 'missing_candidate':
    (cand / board).unlink()
if name == 'candidate_symlink':
    (cand / board).unlink()
    (cand / board).symlink_to(ref / board)
if name == 'extra_candidate_file':
    (cand / 'extra.kicad_dru').write_text('(rules)\n')
if name == 'exit_signal':
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(scenario.get('exit', code))
'''


def make_checkout(root, scenario):
    """A fake checkout with the pinned layout; returns (path, pinned code map)."""
    root = Path(root)
    (root / '.git/refs/heads').mkdir(parents=True)
    (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
    (root / '.git/refs/heads/main').write_text(FAKE_PIN + '\n')
    (root / 'bin').mkdir()
    (root / 'bin/kicad-route').write_text(SCRIPT)
    os.chmod(root / 'bin/kicad-route', 0o755)
    (root / 'src/kicad_route').mkdir(parents=True)
    (root / 'src/kicad_route/__init__.py').write_text('')
    (root / 'vendor').mkdir()  # Empty state directory for separate-state/Freerouting fixtures.
    (root / '.venv-krt/py_router').mkdir(parents=True)
    (root / '.venv-krt/py_router/route.py').write_text('# fixture\n')
    (root / '.venv-krt/bin').mkdir(parents=True)
    (root / '.venv-krt/bin/python').symlink_to('/usr/bin/python3')
    (root / 'scenario.json').write_text(json.dumps(scenario if isinstance(scenario, dict) else {'name': scenario}))
    code = {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
            for rel in ('bin/kicad-route', 'src/kicad_route/__init__.py')}
    return root, code


def make_layout(root, code, pin=FAKE_PIN):
    """The fixture checkout's qualified layout: its pin record written as the pin data file
    the sandbox script reads from its mount, as routing_adapter.pinned_layout returns it."""
    from agent_ee import routing_adapter
    from agent_ee.kicad_route_attempt import PIN_SCHEMA
    path = Path(root) / 'kicad-route-pin.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema': PIN_SCHEMA, 'pin': pin, 'code': code}, indent=1, sort_keys=True) + '\n')
    return routing_adapter.pinned_layout(path)


def make_candidate(root, project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch'):
    """Small owned stand-in project files; not KiCad designs."""
    root = Path(root)
    (root / project).mkdir(parents=True)
    (root / project / board).write_text('(kicad_pcb (version 20260206) (generator fixture))\n')
    (root / project / schematic).write_text('(kicad_sch (version 20260306) (generator fixture))\n')
    (root / project / 'rc.kicad_pro').write_text('{}\n')
    (root / project / 'fp-lib-table').write_text('(fp_lib_table)\n')
    (root / 'requirements').mkdir()
    (root / 'requirements/req.md').write_text('declared requirement\n')
    return root
