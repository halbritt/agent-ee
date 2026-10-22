#!/usr/bin/env python3
"""One sequential telemetry-pod Python/Rust or Python/PyPy runner pair.

Native workload admission is external to this driver. Raw evidence is retained.
Comparison normalizes documented timing/path/date fields and clearance finding
order, and reconstructs dependent hashes after verifying the original links.
"""
import argparse
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src')]
TIMEOUT_SECONDS = 900
DRC_FILES = {
    'native-drc.json', 'baseline/native-drc.json', 'pre-artwork-drc.json',
    'native-drc-execution.json', 'baseline/native-drc-execution.json',
    'native-drc.stdout.txt', 'baseline/native-drc.stdout.txt',
    'native-drc.stderr.txt', 'baseline/native-drc.stderr.txt', 'native-drc-restamp.json',
}
DATED_DRC = {'native-drc.json', 'baseline/native-drc.json', 'pre-artwork-drc.json'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def save(path, document):
    path.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n')


def environment():
    cpu = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')), platform.processor())
    return {'unix_time_ns': time.time_ns(), 'host': platform.node(), 'platform': platform.platform(),
            'cpu_model': cpu, 'cpus': os.cpu_count(), 'affinity': sorted(os.sched_getaffinity(0)),
            'python': sys.version, 'load_average': list(os.getloadavg())}


def usage(who):
    observed = resource.getrusage(who)
    return {'user_s': observed.ru_utime, 'system_s': observed.ru_stime,
            'maxrss_kib': observed.ru_maxrss}


def cpu_delta(before, after):
    return {key: after[key] - before[key] for key in ('user_s', 'system_s')}


def input_identity(config):
    board = Path(config['board'])
    paths = {key: Path(config[key]) for key in ('board', 'netlist', 'request', 'library', 'pypy')
             if key in config}
    paths.update(project=board.with_suffix('.kicad_pro'), rules=board.with_suffix('.kicad_dru'))
    return {key: {'path': str(path), 'sha256': sha(path.read_bytes()) if path.is_file() else None}
            for key, path in paths.items()}


def source_identity():
    modules = sorted((ROOT / 'src' / 'agent_ee').rglob('*.py'))
    modules.append(ROOT / 'src/agent_ee/data/placement_score.rs')
    module_names = {path.relative_to(ROOT).as_posix() for path in modules}
    manifest_path = ROOT / 'SOURCE.json'
    if manifest_path.exists() or manifest_path.is_symlink():
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        valid_hex = lambda value, length: isinstance(value, str) and len(value) == length and all(
            c in '0123456789abcdef' for c in value)
        if (manifest.get('schema') != 'agent-ee.source-archive/1'
                or not valid_hex(manifest.get('revision'), 40)
                or not valid_hex(manifest.get('archive_sha256'), 64)
                or not isinstance(manifest.get('files_sha256'), dict)):
            raise ValueError('invalid explicit SOURCE.json archive manifest')
        expected = manifest['files_sha256']
        missing = sorted(module_names - expected.keys())
        if missing:
            raise ValueError(f'archive manifest omits present source modules: {missing}')
        observed = {}
        for relative, digest in expected.items():
            path = ROOT / relative
            if (not isinstance(relative, str) or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or not path.resolve().is_relative_to(ROOT)
                    or not valid_hex(digest, 64)):
                raise ValueError(f'invalid archive manifest file entry: {relative!r}')
            observed[relative] = sha(path.read_bytes())
            if observed[relative] != digest:
                raise ValueError(f'archive source file hash mismatch: {relative}')
        identity = {'provenance': 'archive-manifest-verified-files', 'revision': manifest['revision'],
                    'archive_sha256': manifest['archive_sha256'], 'manifest_sha256': sha(raw),
                    'archive_identity_scope': 'revision/export hash supplied by SOURCE.json; '
                    'every listed file and coverage of present Python/Rust modules verified here',
                    'files_sha256': observed}
    else:
        identity = {'provenance': 'observed-git-checkout',
                    'git_head': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                    'files_sha256': {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in modules}}
    identity['scratch_driver'] = {'path': str(Path(__file__).resolve().relative_to(ROOT)),
                                  'sha256': sha(Path(__file__).read_bytes())}
    return identity


def record_first_native_call(native, evidence):
    """Record one invocation, removing both hooks before dispatching its kernel call."""
    original_one, original_score = native.one, native.score

    def restore():
        # Restore the class descriptors, avoiding instance-bound-method reference cycles.
        native.__dict__.pop('one', None)
        native.__dict__.pop('score', None)

    def one(packed, x, y, rotation):
        restore()
        evidence.update(method='one', pose_nm_rotation_deg=[x, y, rotation], returned=False)
        result = original_one(packed, x, y, rotation)
        evidence['returned'] = True
        return result

    def score(packed, poses):
        restore()
        evidence.update(method='score', batch_size=len(poses), returned=False)
        result = original_score(packed, poses)
        evidence['returned'] = True
        return result

    native.one, native.score = one, score
    return restore


def worker(config_path):
    verified_source = source_identity()  # The worker checks the same pinned files before importing them.
    from agent_ee import placement_runtime, placement_scoring
    from agent_ee.placer_runner import place_candidate

    config = json.loads(config_path.read_bytes())
    arm = Path(config['arm_root'])
    request = json.loads(Path(config['request']).read_bytes())
    request['authorization']['writable_root'] = str(arm)
    request['skip_visuals'] = True
    save(arm / 'adapted-request.json', request)
    before_self, before_children = usage(resource.RUSAGE_SELF), usage(resource.RUSAGE_CHILDREN)
    before_environment = environment()
    report, failure, events = None, None, []
    first_native_invocation = {}
    planning_calls = []
    started = time.perf_counter_ns()
    try:
        if config['backend'] == 'pypy':
            context = placement_runtime.experimental(config['pypy'])
        elif config['backend'] == 'rust':
            context = placement_scoring.experimental(config['library'])
        else:
            context = nullcontext(None)
        with context as session:
            restore_probe = (record_first_native_call(session.native, first_native_invocation)
                             if config['backend'] == 'rust' and session.native is not None else None)
            try:
                report = place_candidate(config['board'], config['netlist'], arm / 'proposal', request)
            finally:
                if restore_probe is not None:
                    restore_probe()
                if config['backend'] == 'rust':
                    events = list(session.events)
                elif config['backend'] == 'pypy':
                    planning_calls = list(session.calls)
    except Exception as exc:
        failure = {'type': type(exc).__name__, 'message': str(exc)}
        error_traceback = traceback.format_exc()
    finally:
        elapsed_ns = time.perf_counter_ns() - started
        after_self, after_children = usage(resource.RUSAGE_SELF), usage(resource.RUSAGE_CHILDREN)
    if failure is not None:
        (arm / 'exception.txt').write_text(error_traceback)
    if report is not None:
        save(arm / 'raw-report.json', report)
    metadata = {'backend': config['backend'], 'runner_wall_ns': elapsed_ns,
                'runner_self_cpu': cpu_delta(before_self, after_self),
                'runner_children_cpu': cpu_delta(before_children, after_children),
                'self_maxrss_kib': after_self['maxrss_kib'], 'children_maxrss_kib': after_children['maxrss_kib'],
                'environment_before': before_environment, 'environment_after': environment(),
                'scorer_events': events, 'exception': failure, 'verified_source': verified_source,
                'first_native_invocation': first_native_invocation or None,
                'pypy_planning_calls': planning_calls,
                'invocation_probe': ('first native call only; both hooks removed before dispatch; '
                                     'setup and one-call recording overhead included; no oracle'
                                     if config['backend'] == 'rust' else None),
                'status': report.get('status') if report is not None else None,
                'boundary': 'scorer context/library loading and complete place_candidate; imports and external result serialization excluded'}
    save(arm / 'worker-metadata.json', metadata)
    return 1 if failure or events else (0 if report is not None and report.get('status') == 'placed' else 2)


def run_arm(config, backend, root):
    arm = root / backend
    arm.mkdir()
    specification = {**config, 'backend': backend, 'arm_root': str(arm)}
    save(arm / 'worker-input.json', specification)
    argv = [shutil.which('timeout'), '--signal=TERM', '--kill-after=5s', f'{TIMEOUT_SECONDS}s',
            sys.executable, '-B', str(Path(__file__).resolve()), '--worker', str(arm / 'worker-input.json')]
    before_cpu, before_environment = usage(resource.RUSAGE_CHILDREN), environment()
    started = time.perf_counter_ns()
    with (arm / 'stdout.log').open('wb') as stdout, (arm / 'stderr.log').open('wb') as stderr:
        process = subprocess.run(argv, stdout=stdout, stderr=stderr, check=False)
    elapsed = time.perf_counter_ns() - started
    after_cpu = usage(resource.RUSAGE_CHILDREN)
    record = {'backend': backend, 'argv': argv, 'exit_code': process.returncode,
              'timeout_s': TIMEOUT_SECONDS, 'process_wall_ns': elapsed,
              'process_tree_cpu': cpu_delta(before_cpu, after_cpu),
              'environment_before': before_environment, 'environment_after': environment(),
              'boundary': 'GNU timeout plus Python worker process, imports, runner and metadata writes'}
    metadata = arm / 'worker-metadata.json'
    if metadata.is_file():
        record['worker'] = json.loads(metadata.read_bytes())
    save(arm / 'process-metadata.json', record)
    return record


def comparable_plan(plan, removed):
    plan = copy.deepcopy(plan)
    search = plan.get('block_arrangement', {}).get('search', {})
    if 'elapsed_s' in search:
        elapsed = search.pop('elapsed_s')
        if type(elapsed) not in (int, float):
            raise ValueError('unexpected block_arrangement.search.elapsed_s type')
        removed['plan.block_arrangement.search.elapsed_s'] = elapsed
    return plan


def normalize_clearance_order(document, fields, label, normalizations):
    """Sort complete clearance rows in their existing slots; preserve every other order.

    Native clearance finding order varies between DRC invocations (the runner's
    _comparison_changes documents the same fact). This deliberately does not
    reorder other finding types, item pairs, nested arrays, or unrelated lists.
    """
    for field in fields:
        rows = document[field]
        if not isinstance(rows, list):
            raise ValueError(f'{label}.{field} must be a finding list')
        slots = [i for i, row in enumerate(rows)
                 if isinstance(row, dict) and row.get('type') == 'clearance']
        original = [rows[i] for i in slots]
        for i, row in zip(slots, sorted(original, key=canonical)):
            rows[i] = row
        normalizations[f'{label}.{field}.clearance_order'] = {
            'count': len(slots), 'slots': slots,
            'original_complete_row_sha256': [sha(canonical(row)) for row in original],
            'rule': 'complete clearance rows sorted within existing clearance slots; all fields and multiplicities retained',
        }
    return document


def comparison_artifacts(arm):
    directory = arm / 'proposal'
    raw_report = json.loads((arm / 'raw-report.json').read_bytes())
    removed, links = {}, []
    artifacts = {}
    if raw_report.get('status') != 'placed':
        normalized = comparable_plan(raw_report, removed)
        return {'raw-report.json': canonical(normalized)}, removed, links
    files = {str(path.relative_to(directory)): path.read_bytes()
             for path in sorted(directory.rglob('*')) if path.is_file()}
    report = json.loads(files['placement-result.json'])
    if report != raw_report:
        raise ValueError('returned report differs from retained placement-result.json')
    plan = json.loads(files['placement-plan.json'])
    if report['plan'] != plan:
        raise ValueError('result plan differs from retained placement-plan.json')
    if sha(files['candidate.kicad_pcb']) != report['board_sha256']:
        raise ValueError('result board hash differs from retained candidate bytes')
    links.append('result.board_sha256 == sha256(candidate.kicad_pcb)')
    normalized_plan = comparable_plan(plan, removed)
    normalized_report = copy.deepcopy(report)
    normalized_report['plan'] = normalized_plan
    artifacts['placement-plan.json'] = canonical(normalized_plan)
    comparison = json.loads(files['native-drc-comparison.json'])
    if report['native_drc_comparison'] != comparison:
        raise ValueError('result native DRC comparison differs from retained raw comparison')
    links.append('result.native_drc_comparison == raw native-drc-comparison.json')
    normalized_comparison = normalize_clearance_order(
        copy.deepcopy(comparison), ('baseline_findings', 'candidate_findings', 'advisory_findings'),
        'native-drc-comparison.json', removed)
    artifacts['native-drc-comparison.json'] = canonical(normalized_comparison)
    normalized_report['native_drc_comparison'] = normalized_comparison
    if ('block_arrangement' in plan) != ('block-provenance.json' in files) \
            or ('block_provenance_sha256' in report) != ('block-provenance.json' in files):
        raise ValueError('block plan/provenance/result hash presence disagrees')
    if 'block-provenance.json' in files:
        provenance = json.loads(files['block-provenance.json'])
        if provenance['plan_sha256'] != sha(files['placement-plan.json']):
            raise ValueError('block provenance does not identify its raw plan')
        if provenance['board_sha256'] != report['board_sha256']:
            raise ValueError('block provenance board differs from report')
        if report['block_provenance_sha256'] != sha(files['block-provenance.json']):
            raise ValueError('result does not identify its raw block provenance')
        links += ['provenance.plan_sha256 == sha256(raw plan)',
                  'provenance.board_sha256 == result.board_sha256',
                  'result.block_provenance_sha256 == sha256(raw provenance)']
        provenance['plan_sha256'] = sha(canonical(normalized_plan))
        artifacts['block-provenance.json'] = canonical(provenance)
        normalized_report['block_provenance_sha256'] = sha(artifacts['block-provenance.json'])
    artifacts['placement-result.json'] = canonical(normalized_report)
    artifacts['raw-report.json'] = canonical(normalized_report)
    execution = json.loads((arm / 'worker-input.json').read_bytes())
    recorded_arm = Path(execution['arm_root'])
    if execution['backend'] != arm.name or not recorded_arm.is_absolute() or recorded_arm.name != arm.name:
        raise ValueError('recorded execution arm does not identify this artifact directory')
    removed['execution_staging_root'] = str(recorded_arm)
    staging = re.compile(re.escape(str(recorded_arm).encode()) + rb'/\.placement-staging-[A-Za-z0-9_]+')
    for name, raw in files.items():
        if name in artifacts:
            continue
        if name in DRC_FILES:
            raw = staging.sub(b'COMPARISON_STAGING', raw)
            if name in DATED_DRC:
                document = json.loads(raw)
                normalize_clearance_order(document, ('violations',), name, removed)
                if 'date' in document:
                    removed[name + '.date'] = document['date']
                    document['date'] = 'COMPARISON_DATE'
                raw = canonical(document)
        artifacts[name] = raw
    return artifacts, removed, links


def compare(root, records):
    backends = [record['backend'] for record in records]
    if len(backends) != 2 or backends.count('python') != 1:
        raise ValueError('comparison requires one Python baseline and one candidate backend')
    candidate = next(backend for backend in backends if backend != 'python')
    if candidate not in ('rust', 'pypy'):
        raise ValueError('unsupported candidate backend')
    if any(record['exit_code'] not in (0, 2) or record.get('worker', {}).get('scorer_events')
           for record in records):
        return {'status': 'not-comparable', 'reason': 'an arm failed, timed out or fell back; raw evidence retained'}
    if candidate == 'pypy':
        candidate_worker = next(record.get('worker', {}) for record in records if record['backend'] == 'pypy')
        calls = candidate_worker.get('pypy_planning_calls', [])
        score_observed = any((call.get('first_wire_cost') or {}).get('returned') is True for call in calls)
        if not calls or any(call.get('status') != 'ok' or call.get('runtime', {}).get('implementation') != 'PyPy'
                            for call in calls) or (candidate_worker.get('status') == 'placed' and not score_observed):
            return {'status': 'not-comparable',
                    'reason': 'missing successful PyPy planning/scorer invocation evidence; raw evidence retained'}
    expected, expected_removed, expected_links = comparison_artifacts(root / 'python')
    actual, actual_removed, actual_links = comparison_artifacts(root / candidate)
    missing = sorted(set(expected) ^ set(actual))
    changed = [name for name in sorted(set(expected) & set(actual)) if expected[name] != actual[name]]
    same_normalization_fields = set(expected_removed) == set(actual_removed)
    equal = not missing and not changed and same_normalization_fields
    placed = all(record.get('worker', {}).get('status') == 'placed' for record in records)
    board_equal = (expected.get('candidate.kicad_pcb') == actual.get('candidate.kicad_pcb')) if placed else None
    summary = {'status': 'equal' if equal else 'different', 'both_placed': placed,
               'candidate_board_bytes_equal': board_equal, 'file_set_difference': missing, 'changed_files': changed,
               'same_normalization_fields': same_normalization_fields,
               'normalizations': {'python': expected_removed, candidate: actual_removed},
               'verified_links': {'python': expected_links, candidate: actual_links},
               'comparison_sha256': {backend: {name: sha(raw) for name, raw in artifacts.items()}
                                     for backend, artifacts in (('python', expected), (candidate, actual))},
               'scope': 'comparison copies only; all raw hash-bound evidence remains unchanged'}
    if equal and placed:
        wall_by_backend = {record['backend']: record['worker']['runner_wall_ns'] for record in records}
        summary['single_pair_runner_speedup'] = wall_by_backend['python'] / wall_by_backend[candidate]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    for name in ('board', 'netlist', 'request', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--candidate-backend', choices=('rust', 'pypy'), default='rust')
    parser.add_argument('--library', type=Path, help='required for the Rust candidate')
    parser.add_argument('--pypy', type=Path, help='required for the PyPy candidate; native KiCad stays in CPython')
    parser.add_argument('--candidate-first', '--rust-first', dest='candidate_first', action='store_true',
                        help='run the candidate before Python for a counterbalanced pair')
    args = parser.parse_args()
    required_runtime = 'library' if args.candidate_backend == 'rust' else 'pypy'
    if getattr(args, required_runtime) is None:
        parser.error(f'--{required_runtime} is required for {args.candidate_backend}')
    if shutil.which('timeout') is None:
        parser.error('GNU timeout is required')
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = {name: str(getattr(args, name).resolve()) for name in ('board', 'netlist', 'request', required_runtime)}
    order = [args.candidate_backend, 'python'] if args.candidate_first else ['python', args.candidate_backend]
    manifest = {'schema': 'agent-ee.telemetry-scorer-pair/1', 'status': 'running',
                'order': order, 'pairs': 1, 'timeout_s_per_arm': TIMEOUT_SECONDS,
                'environment_before': environment(), 'arms': [],
                'changes': ['authorization.writable_root per arm', 'skip_visuals=True for both'],
                'scope': 'full placer_runner including block arrangement, save, native DRC and receipts; visuals explicitly skipped',
                'interpretation': 'one ordered pair; no statistical performance conclusion or default-backend change'}
    try:
        for name in config:
            if not Path(config[name]).is_file():
                raise FileNotFoundError(config[name])
        if Path(config['board']).is_relative_to(root):
            raise ValueError('source board must be outside experiment output root')
        manifest['inputs_before'] = input_identity(config)
        manifest['source_before'] = source_identity()
        save(root / 'manifest.json', manifest)
        for backend in order:
            manifest['arms'].append(run_arm(config, backend, root))
            save(root / 'manifest.json', manifest)
        manifest['inputs_after'] = input_identity(config)
        manifest['source_after'] = source_identity()
        manifest['identities_stable'] = (manifest['inputs_before'] == manifest['inputs_after']
                                         and manifest['source_before'] == manifest['source_after'])
        manifest['comparison'] = compare(root, manifest['arms'])
        if manifest['identities_stable'] and manifest['comparison']['status'] == 'equal':
            manifest['status'] = 'completed' if manifest['comparison']['both_placed'] else 'matched-refusal'
        else:
            manifest['status'] = 'failed-comparison'
    except Exception as exc:
        manifest['status'] = 'failed'
        manifest['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        (root / 'driver-exception.txt').write_text(traceback.format_exc())
    finally:
        manifest['environment_after'] = environment()
        save(root / 'manifest.json', manifest)
    print(json.dumps({'status': manifest['status'], 'manifest': str(root / 'manifest.json'),
                      'comparison': manifest.get('comparison', {}).get('status'),
                      'both_placed': manifest.get('comparison', {}).get('both_placed')}, sort_keys=True))
    return 0 if manifest['status'] == 'completed' else (2 if manifest['status'] == 'matched-refusal' else 1)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        raise SystemExit(worker(Path(sys.argv[2])))
    raise SystemExit(main())
