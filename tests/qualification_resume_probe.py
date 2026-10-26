"""Native resume regression using an already qualified, sealed build.

Run with --plan PLAN --run RUN --out NEW_DIRECTORY in the build's supported
runtime. Only copies are changed. Unlike a synthetic seven-directory journal,
the input must pass the actual qualifier and package gate before any refusal
assertion. Typed runs also exercise their frozen judgment-input refusal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_ee import build_workflow as build, qualification
from agent_ee.storage import read_json, write_json



def probe(plan_path, run, out):
    if out.is_relative_to(run):
        raise ValueError('probe output must be outside the retained input run')
    plan = build.validate_plan(read_json(plan_path))
    source = Path(plan['source'])
    if (not source.parts or source.is_absolute() or '..' in source.parts
            or source.parts[0] in ('run', 'build.json', 'other-runtime')):
        raise ValueError('probe requires a relative source directory isolated below its output')
    if not read_json(run / 'build-report.json')['accepted']:
        raise ValueError('probe requires an already accepted build')
    # No passing check rows, witnesses or package markers are synthesized here.
    out.mkdir(parents=True, exist_ok=False)
    copied = out / 'run'
    shutil.copytree(run, copied, symlinks=True)
    assert qualification.assess(copied)['accepted']
    shutil.copytree(run / 'source', out / source, symlinks=True)
    if 'judgments' in plan:
        package = read_json(run / 'build-report.json')['completed'][-1]
        shutil.copytree(run / package['directory'] / 'judgments', out / source / plan['judgments'])
    plan_copy = out / 'build.json'
    plan_bytes = plan_path.read_bytes()
    plan_copy.write_bytes(plan_bytes)
    command = [sys.executable, '-m', 'agent_ee.cli', 'build', 'run', str(plan_copy),
               '--out', str(copied), '--resume']
    positive = subprocess.run(command, text=True, capture_output=True, timeout=180)
    assert positive.returncode == 0, positive.stderr + positive.stdout
    before = {name: (copied / name).read_bytes() for name in ('build-report.json', 'qualification.json')}
    results = {}

    def refused(name, *, env=None, kind='invocation_identity'):
        previous = set((copied / 'resume-refusals').glob('*.json'))
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)
        assert result.returncode != 0, result.stdout
        assert before == {key: (copied / key).read_bytes() for key in before}, name
        assert read_json(copied / 'build-report.json')['accepted']
        receipt_path, = set((copied / 'resume-refusals').glob('*.json')) - previous
        receipt = read_json(receipt_path)
        assert receipt['kind'] == kind, receipt
        assert receipt['retained'] == {key: hashlib.sha256(raw).hexdigest() for key, raw in before.items()}
        results[name] = dict(exit=result.returncode, refusal=receipt_path.name,
                             root_records_byte_identical=True)

    spec = out / source / plan['requirements']['spec']
    raw = spec.read_bytes()
    spec.write_bytes(raw + b'\nExternal invocation source drift.\n')
    refused('source')
    spec.write_bytes(raw)
    changed_plan = dict(plan, verification_timeout_s=30 if plan.get('verification_timeout_s') != 30 else 31)
    write_json(plan_copy, changed_plan)
    refused('plan')
    plan_copy.write_bytes(plan_bytes)
    runtime_source = out / 'other-runtime'
    shutil.copytree(Path(build.__file__).parent, runtime_source / 'agent_ee')
    with (runtime_source / 'agent_ee/build_workflow.py').open('a') as stream:
        stream.write('\n# Inert runtime-identity regression control.\n')
    refused('runtime', env=dict(os.environ, PYTHONPATH=str(runtime_source)))
    if 'judgments' in plan:
        draft = out / source / plan['judgments'] / 'new-after-freeze.json'
        draft.write_text('{}\n')
        refused('judgments', kind='judgment_inputs')
        draft.unlink()
    else:
        results['judgments'] = 'not_applicable_legacy; typed freeze covered by test_qualification'

    # An old accepted run without the capture/reference must not be migrated.
    historical = read_json(copied / 'build-report.json')
    historical.pop('qualification')
    write_json(copied / 'build-report.json', historical)
    historical_bytes = (copied / 'build-report.json').read_bytes()
    (copied / 'qualification.json').unlink()
    captured_plan = (copied / 'build-plan.json').read_bytes()
    (copied / 'build-plan.json').unlink()
    result = subprocess.run(command, text=True, capture_output=True, timeout=180)
    assert result.returncode != 0
    assert 'historical build' in result.stderr, result.stderr
    assert historical_bytes == (copied / 'build-report.json').read_bytes()
    assert not (copied / 'qualification.json').exists()
    results['historical_migration'] = dict(exit=result.returncode, report_byte_identical=True)
    for name, raw in before.items():
        (copied / name).write_bytes(raw)
    (copied / 'build-plan.json').write_bytes(captured_plan)

    report = read_json(copied / 'build-report.json')
    package = report['completed'][-1]
    scope = read_json(copied / package['directory'] / 'review/candidate.json')['scope']
    board = copied / package['candidate'] / scope['board']
    results['retained_corruption'] = {}
    for name, target in [('source', copied / 'source' / plan['requirements']['spec']),
                         ('stage', board), ('review', copied / package['directory'] / 'review/result.json')]:
        original = target.read_bytes()
        target.write_bytes(original + b'\n')
        result = subprocess.run(command, text=True, capture_output=True, timeout=180)
        assert result.returncode != 0
        invalid = read_json(copied / 'build-report.json')
        assert not invalid['accepted']
        assert read_json(copied / 'qualification.json')['dimensions']['file_release']['status'] == 'failed'
        archived = copied / invalid['supersession']['path']
        assert before == {key: (archived / key).read_bytes() for key in before}
        results['retained_corruption'][name] = dict(exit=result.returncode, current_accepted=False,
                                                  previous_root_bytes_preserved=True)
        target.write_bytes(original)
        # Reset only this disposable control between independent corruptions.
        for key, raw in before.items():
            (copied / key).write_bytes(raw)
    write_json(out / 'probe.json', results)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'run', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.plan.resolve(), args.run.resolve(), args.out.resolve()), indent=2))
