#!/usr/bin/python3
"""Verify retained inputs, prepare relocated copies, and record public native runs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'fixtures/recovery/inputs.json'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, record):
    path.write_text(json.dumps(record, indent=2) + '\n')


def verify(inputs_root):
    manifest = json.loads(MANIFEST.read_text())
    problems = []
    for relative, pin in manifest['files'].items():
        path = inputs_root / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(inputs_root.resolve()):
            problems.append(relative + ': missing file, link or escaped input root')
        elif path.stat().st_size != pin['bytes'] or sha256(path) != pin['sha256']:
            problems.append(relative + ': differs from captured bytes')
    if problems:
        raise ValueError('Captured inputs unavailable or changed:\n' + '\n'.join(problems)
                         + '\nSee fixtures/recovery/inputs.json access instructions; no replacement is downloaded.')
    return manifest


def prepare(inputs_root, out):
    manifest = verify(inputs_root)
    out.mkdir(parents=True, exist_ok=False)
    captured = out / 'captured'
    for relative in manifest['files']:
        destination = captured / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inputs_root / relative, destination)
    verify(captured)
    esp = manifest['representatives']['esp_prog']['config']
    config = json.loads((captured / esp).read_text())
    for entry in config['inputs'].values():
        entry['path'] = str(captured / Path(entry['path']).relative_to('/opt/agent-ee'))
    write_json(out / 'esp-prog.json', config)
    prepared = {'schema': 'agent-ee.recovery-prepared/1', 'manifest_sha256': sha256(MANIFEST),
                'input_origin': str(inputs_root), 'captured': str(captured),
                'acquisition_plan': str(captured / manifest['representatives']['acquisition']['plan']),
                'esp_prog_config': str(out / 'esp-prog.json'),
                'derived_esp_prog_config_sha256': sha256(out / 'esp-prog.json'),
                'changes': 'Only esp_prog input locators relocated; captured originals and all content hashes preserved.'}
    write_json(out / 'prepared.json', prepared)
    return prepared


def command_record(argv, out, label):
    started = time.monotonic()
    with (out / (label + '.stdout')).open('wb') as stdout, (out / (label + '.stderr')).open('wb') as stderr:
        try:
            completed = subprocess.run(argv, stdout=stdout, stderr=stderr,
                                       timeout=60 if label in ('doctor', 'planner-receipt', 'esp-preflight') else 3600)
            record = {'argv': argv, 'exit_code': completed.returncode}
        except (OSError, subprocess.TimeoutExpired) as exc:
            record = {'argv': argv, 'exit_code': None, 'error': str(exc)}
    return {**record, 'elapsed_s': round(time.monotonic() - started, 3),
            'stdout': label + '.stdout', 'stderr': label + '.stderr'}


def cancelled_preflight(directory, exit_code):
    job_report = directory / 'job-report.json'
    if not job_report.is_file():
        return {'runtime_ready_before_cancellation': False}
    job = json.loads(job_report.read_text())
    record = {key: job.get(key) for key in ('status', 'reason', 'preflight', 'identity', 'engine', 'phases')}
    engine = job.get('engine', {})
    record['runtime_ready_before_cancellation'] = (
        exit_code == 1 and job.get('status') == 'cancelled' and bool(job.get('preflight'))
        and bool(job.get('identity')) and engine.get('interruption') == 'cancelled'
        and engine.get('exit_code') is None and not engine.get('counters') and not engine.get('seeds'))
    capture_path = directory / 'capture.json'
    if capture_path.is_file():
        captured = json.loads(capture_path.read_text())
        record['native_footprint_objects'] = len(captured['footprints'])
    else:
        record['runtime_ready_before_cancellation'] = False
    return record


def run(prepared_root, out, full):
    prepared = json.loads((prepared_root / 'prepared.json').read_text())
    if prepared['manifest_sha256'] != sha256(MANIFEST):
        raise ValueError('prepared inputs belong to a different recovery manifest')
    verify(Path(prepared['captured']))
    if sha256(Path(prepared['esp_prog_config'])) != prepared['derived_esp_prog_config_sha256']:
        raise ValueError('derived esp_prog configuration changed')
    out.mkdir(parents=True, exist_ok=False)
    esp_preflight = out / 'esp-preflight'
    esp_preflight.mkdir()
    (esp_preflight / 'CANCEL').write_text('Runtime qualification only; no search authorized.\n')
    cli = str(ROOT / 'bin/agent-ee')
    report = {'schema': 'agent-ee.recovery-runtime/1', 'mode': 'full' if full else 'checkpoint',
              'manifest_sha256': sha256(MANIFEST), 'prepared_sha256': sha256(prepared_root / 'prepared.json'),
              'acceptance': False, 'status': 'running', 'commands': {},
              'note': 'Runtime/checkpoint evidence only. Full release acceptance belongs to AGENTEE-515; retained historical success is unchanged.'}
    report['implementation_sha256'] = {relative: sha256(ROOT / relative) for relative in
        ('bin/agent-ee', 'src/agent_ee/cli.py', 'src/agent_ee/placement_job.py',
         'scripts/recovery_runtime.py', 'scripts/recovery-container.sh', 'scripts/build_placement_engine.py')}
    commands = [('doctor', [cli, 'doctor', '--checks-only', '--router-checkout', '/opt/kicad-route']),
                ('planner-receipt', [sys.executable, str(ROOT / 'scripts/build_placement_engine.py'),
                                     'verify', '--output', '/usr/local/libexec/agent-ee/agent-ee-placement']),
                ('esp-preflight', [cli, 'place', 'run', prepared['esp_prog_config'], '--out', str(esp_preflight)]),
                ('acquisition', [cli, 'build', 'run', prepared['acquisition_plan'], '--out', str(out / 'acquisition'),
                                 '--through', 'package' if full else 'schematic'])]
    if full:
        commands.append(('esp-prog', [cli, 'place', 'run', prepared['esp_prog_config'], '--out', str(out / 'esp-prog')]))
    write_json(out / 'runtime-report.json', report)
    for label, argv in commands:
        if label == 'esp-prog' and not report['commands']['esp-preflight'].get('runtime_ready_before_cancellation'):
            report['commands'][label] = {'argv': argv, 'exit_code': None, 'error': 'esp_prog preflight did not establish readiness'}
            continue
        report['commands'][label] = command_record(argv, out, label)
        if label == 'esp-preflight':
            selected = cancelled_preflight(esp_preflight, report['commands'][label]['exit_code'])
            report['esp_prog_preflight'] = selected
            report['commands'][label]['runtime_ready_before_cancellation'] = selected['runtime_ready_before_cancellation']
        write_json(out / 'runtime-report.json', report)
        # Do not spend a search budget when input/tool readiness is already known to fail.
        if report['commands'][label]['exit_code'] != 0 and label in ('doctor', 'planner-receipt'):
            break
    build_report = out / 'acquisition/build-report.json'
    if build_report.is_file():
        build = json.loads(build_report.read_text())
        report['acquisition'] = {key: build.get(key) for key in ('status', 'accepted', 'completed', 'reason', 'candidate')}
    report['status'] = ('execution_completed' if len(report['commands']) == len(commands)
                        and all(row['exit_code'] == 0 or (label == 'esp-preflight'
                                and row.get('runtime_ready_before_cancellation') is True)
                                for label, row in report['commands'].items()) else 'failed')
    write_json(out / 'runtime-report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('verify', 'prepare'):
        command = sub.add_parser(name)
        command.add_argument('--inputs-root', type=Path, required=True)
        if name == 'prepare':
            command.add_argument('--out', type=Path, required=True)
    command = sub.add_parser('run')
    command.add_argument('--prepared', type=Path, required=True)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--full', action='store_true', help='run both full workflows; spends captured placement/routing budgets')
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            manifest = verify(args.inputs_root.resolve())
            report = {'status': 'verified', 'files': len(manifest['files']), 'manifest_sha256': sha256(MANIFEST)}
        elif args.command == 'prepare':
            report = prepare(args.inputs_root.resolve(), args.out.resolve())
        else:
            report = run(args.prepared.resolve(), args.out.resolve(), args.full)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 1 if report.get('status') == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
