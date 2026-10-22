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


src, board, sch = Path(opts['--dir']), opts['--board'], opts['--schematic']
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
routing = {'COMPLETE': {'status': 'COMPLETE', 'unconnected_in_scope': 0}, 'INCOMPLETE':
           {'status': 'INCOMPLETE', 'unconnected_in_scope': 2, 'unrouted_nets': ['/IN']},
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
if name == 'env':
    write(run / 'validate/env.json', {'environ': dict(os.environ), 'cwd': os.getcwd(),
                                      'dont_write_bytecode': sys.flags.dont_write_bytecode,
                                      'pycache_prefix': sys.pycache_prefix})
text = 'not json {' if name == 'malformed_json' else json.dumps(result, indent=2) + '\n'
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
