#!/usr/bin/env python3
"""One bounded native K91 positive through the real routing consumer path (AGENTEE-442).

    k91_native_positive.py --checkout L --frozen-source F --mismatch M --out OUT [--budget S]

Runs agent-ee's own routing consumer, routing_adapter.step(),
stage_receipts.run_step(), routing_adapter.verify() and stage_receipts.adopt(),
once on a disposable copy of the RC fixture. The router is an ISOLATED proposed
pin, never the production pin and never the defaults:

- L is a private checkout-mode layout: a git checkout of the proposed commit
  (HEAD read from its own git files), its state manifest, `.venv-krt` and
  `vendor/`. The pin and its code map exist only for this run (patched for
  step(), passed to the in-sandbox adapter by the same driver the fixture tests
  use, and given to verify() as its layout). attempt.PIN and PINNED_CODE on disk
  are unchanged.
- F is the frozen source the qualified runtime was recorded against. Its engine
  source digest must equal L's, and both must equal --source-sha256.
- M is a layout presenting another runtime, used only for negative control (a).

Nothing runs unless every recomputed identity (commit, engine source, extension
module, venv packages and Python, manifest drift) matches the expected ones:
an actual mismatch stops with exit 3 and no router run. After the positive the
script runs three negative controls without running the router again: (a) the
mismatched runtime, (b) a tampered output byte, (c) a stale input identity.
Each is recorded with the exact exception or classification it produced. The
manifest is written to OUT/evidence-manifest.json. Exit 0 means the positive was
retained and adopted and every control was refused; anything else is exit 1.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from agent_ee import kicad_route_attempt as attempt  # noqa: E402
from agent_ee import routing_adapter, stage_receipts  # noqa: E402

DRIVER = ('import json, sys\nsys.path.insert(0, "/opt/agent-ee-routing")\n'
          'import kicad_route_attempt as a\n'
          'cfg = json.load(open("/opt/driver/layout.json"))\n'
          'layout = a.default_layout(a.parse_request(sys.argv[1:]))\n'
          'layout.update(pin=cfg["pin"], code=cfg["code"])\n'
          'sys.exit(a.main(sys.argv[1:], layout))\n')
BINDING = {'implementation': 1, 'receipt_index': 1}
FIXTURE = ROOT / 'fixtures/rc'
BOARD, SCHEMATIC, NAME = 'rc_filter.kicad_pcb', 'rc_filter.kicad_sch', 'rc_filter'
EXTENSION = '.venv-krt/lib/python3.12/site-packages/grid_router.so'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def engine_identity(checkout, router=None):
    """Engine source digest, venv packages digest and Python version, by the checkout's own code."""
    script = ('import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1] + "/src")\n'
              'from kicad_route import engine, statemanifest as s\n'
              'c = Path(sys.argv[1]); r = Path(sys.argv[2]) if sys.argv[2] else c / "src/kicad_route/router"\n'
              'v = c / ".venv-krt"\n'
              'm = json.loads((c / "manifest.json").read_text()) if (c / "manifest.json").is_file() else None\n'
              'print(json.dumps({"source_sha256": engine.source_sha256(r),\n'
              '  "packages_sha256": s.packages_sha256(s.venv_packages(v)) if v.is_dir() else None,\n'
              '  "python_version": s.venv_python_version(v) if v.is_dir() else None,\n'
              '  "drift": s.verify(m, c) if m is not None else None}))\n')
    out = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', script, str(checkout), str(router or '')],
                         capture_output=True, text=True, timeout=600, check=True,
                         env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
    return json.loads(out.stdout)


def code_map(checkout, commit):
    """sha256 of every bin/ and src/ blob at the commit, read from git objects, not the work tree."""
    listing = subprocess.run(['git', '-C', str(checkout), 'ls-tree', '-r', '-z', commit, '--', 'bin', 'src'],
                             capture_output=True, check=True).stdout.split(b'\0')
    code = {}
    for line in filter(None, listing):
        meta, path = line.split(b'\t', 1)
        mode, kind, blob = meta.split()
        if kind != b'blob' or mode == b'120000':
            raise SystemExit(f'unexpected tree entry {line!r}')
        data = subprocess.run(['git', '-C', str(checkout), 'cat-file', 'blob', blob.decode()],
                              capture_output=True, check=True).stdout
        code[path.decode()] = hashlib.sha256(data).hexdigest()
    return code


def identities(args):
    checkout = Path(args.checkout)
    commit = attempt.checkout_commit(checkout)
    engine = engine_identity(checkout)
    frozen = engine_identity(checkout, args.frozen_source)['source_sha256']
    manifest = json.loads((checkout / 'manifest.json').read_text())
    k = manifest['krt']
    found = {
        'commit': commit, 'source_sha256': engine['source_sha256'], 'frozen_source_sha256': frozen,
        'extension_sha256': sha256(checkout / EXTENSION),
        'packages_sha256': engine['packages_sha256'], 'python_version': engine['python_version'],
        'manifest_drift': engine['drift'],
        'manifest': {'sha256': sha256(checkout / 'manifest.json'), 'state_dir': manifest['state_dir'],
                     'generation': k['generation'], 'commit': k['commit'], 'source_sha256': k['source_sha256'],
                     'extension_sha256': k['extension_module']['sha256'],
                     'packages_sha256': k['venv']['packages_sha256']},
        'interpreter': {'lexical': str(checkout / '.venv-krt/bin/python'),
                        'link': os.readlink(checkout / '.venv-krt/bin/python'),
                        'resolved': str((checkout / '.venv-krt/bin/python').resolve())},
    }
    expected = {'commit': args.commit, 'source_sha256': args.source_sha256, 'frozen_source_sha256': args.source_sha256,
                'extension_sha256': args.extension_sha256, 'manifest_drift': []}
    mismatch = {key: {'expected': want, 'found': found[key]} for key, want in expected.items() if found[key] != want}
    for key, want in (('commit', args.commit), ('source_sha256', args.source_sha256),
                      ('extension_sha256', args.extension_sha256),
                      ('packages_sha256', found['packages_sha256'])):
        if found['manifest'][key] != want:
            mismatch[f'manifest.{key}'] = {'expected': want, 'found': found['manifest'][key]}
    if found['manifest']['generation'] is not None:
        mismatch['manifest.generation'] = {'expected': None, 'found': found['manifest']['generation']}
    return found, mismatch


def design_copy(root):
    shutil.copytree(FIXTURE / 'control', root / 'project')
    shutil.copytree(FIXTURE / 'requirements', root / 'requirements')
    return root


def tree_record(design):
    files = attempt.files_of(attempt.tree(design))
    return {'files': files, 'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def declare(args, checkout, pin, code, policy):
    with patch.object(attempt, 'PIN', pin), patch.object(attempt, 'PINNED_CODE', code):
        return routing_adapter.step(project='project', board=BOARD, schematic=SCHEMATIC, name=NAME,
                                    label='k91-native-positive', checkout=checkout, budget=args.budget,
                                    experiment_policy=policy)


def drive(spec, driver):
    """The fixture tests' driver: the same adapter module, with this run's pin and code map."""
    spec = json.loads(json.dumps(spec))
    assert spec['argv'][3] == routing_adapter.ADAPTER_AT, spec['argv']
    spec['argv'][3] = '/opt/driver/run.py'
    spec['mounts'].append({'source': str(driver), 'at': '/opt/driver'})
    return spec


def outcome(fn):
    try:
        return {'accepted': True, 'value': fn()}
    except Exception as exc:  # recorded verbatim; every control expects a refusal here
        return {'accepted': False, 'error': f'{type(exc).__name__}: {exc}'}


def file_record(path):
    return {'path': str(path), 'sha256': sha256(path)} if Path(path).is_file() else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--checkout', required=True)
    ap.add_argument('--frozen-source', required=True)
    ap.add_argument('--mismatch', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--commit', default='bd1e633f2e585f4fc931ceab001299b78d0d8bb7')
    ap.add_argument('--source-sha256', default='cb003b30f75f7b97859dcaaae6097ff1cbdeca00e7e38c2b1ab4b4a11e4790c2')
    ap.add_argument('--extension-sha256', default='610b3acdfa0b5a2ee95fedd2a3526ff23b457c115c2f93bbac9fedf2ffcd99d8')
    ap.add_argument('--rule-areas-sha256', required=True)
    ap.add_argument('--budget', type=int, default=300)
    args = ap.parse_args(argv)
    started = time.monotonic()
    out = Path(args.out)
    out.mkdir(parents=True)
    checkout = Path(args.checkout)
    manifest = {'schema': 'agent-ee.k91-native-positive/1', 'item': 'AGENTEE-442',
                'agent_ee_head': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True,
                                                text=True, check=True).stdout.strip(),
                'harness_sha256': sha256(__file__), 'adapter_sha256': sha256(routing_adapter.ADAPTER_SOURCE),
                'production_pin': attempt.PIN, 'qualified': False}

    found, mismatch = identities(args)
    manifest['runtime'] = {'description': 'proposed pin source + copied existing venv, private checkout-mode layout; '
                                          'not an installed router generation', **found}
    if mismatch:
        manifest['stopped'] = {'reason': 'identity mismatch; the router was not run', 'mismatch': mismatch}
        (out / 'evidence-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest['stopped'], indent=2))
        return 3
    code = code_map(checkout, args.commit)
    if attempt.verify_code(checkout, code):
        raise SystemExit('checkout work tree differs from its commit: ' + '; '.join(attempt.verify_code(checkout, code)))
    layout = {'pin': args.commit, 'code': code}
    manifest['pin'] = {'commit': args.commit, 'code_files': len(code),
                       'code_sha256': hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()}
    driver = out / 'driver'
    driver.mkdir()
    (driver / 'layout.json').write_text(json.dumps(layout))
    (driver / 'run.py').write_text(DRIVER)

    policy = {'schema': 'routability-policy/1', 'experiment_class': 'current-policy',
              'allowed_trace_layers': ['F.Cu', 'B.Cu'], 'allowed_via_spans': [['F.Cu', 'B.Cu']],
              'rule_areas_sha256': args.rule_areas_sha256}
    manifest['policy'] = {'declared': policy, 'canonical_sha256': hashlib.sha256(
        attempt.canonical_json(policy).encode()).hexdigest()}

    # The positive: step -> run_step -> verify -> adopt, once, on a disposable copy.
    design = design_copy(out / 'design')
    before = tree_record(design)
    spec = drive(declare(args, checkout, args.commit, code, policy), driver)
    (out / 'step.json').write_text(json.dumps(spec, indent=2) + '\n')
    receipt_dir = out / 'receipts/1-kicad-route-builtin'
    t0 = time.monotonic()
    receipt = stage_receipts.run_step(receipt_dir, design, spec, BINDING)
    run_s = time.monotonic() - t0
    validated = stage_receipts.validate_receipt(receipt_dir) == receipt
    run = receipt_dir / 'run'
    report = json.loads((run / 'routing/adapter-report.json').read_text())
    summary = outcome(lambda: routing_adapter.verify(run, receipt=receipt, layout=layout))
    adopted = outcome(lambda: stage_receipts.adopt(receipt_dir, receipt, design)) \
        if summary['accepted'] else {'accepted': False, 'error': 'not attempted: verify refused'}
    after = tree_record(design)
    attempt_dir = run / 'routing/attempt'
    result = json.loads((attempt_dir / 'result.json').read_text()) if (attempt_dir / 'result.json').is_file() else None
    sidecar = json.loads((attempt_dir / 'routability-experiment.json').read_text()) \
        if (attempt_dir / 'routability-experiment.json').is_file() else None
    manifest['positive'] = {
        'step_sha256': sha256(out / 'step.json'), 'receipt_id': receipt['receipt_id'],
        'receipt_sha256': sha256(receipt_dir / 'receipt.json'), 'receipt_validated': validated,
        'exit_code': receipt['exit_code'], 'status': receipt['status'], 'violations': receipt['violations'],
        'duration_s': receipt['duration_s'], 'run_step_wall_s': round(run_s, 1),
        'receipt_inputs_sha256': hashlib.sha256(json.dumps(receipt['inputs'], sort_keys=True).encode()).hexdigest(),
        'receipt_outputs': receipt['outputs'], 'receipt_artifacts': receipt['artifacts'],
        'receipt_tool': receipt['tool'], 'receipt_identity': receipt['identity'],
        'adapter_report_sha256': sha256(run / 'routing/adapter-report.json'),
        'report_tool': report.get('tool'), 'report_backend': report.get('backend'),
        'semantics': report.get('semantics'),
        'result_experiment': (result or {}).get('experiment'),
        'result_backend_runtime': ((result or {}).get('backend') or {}).get('runtime'),
        'sidecar': None if sidecar is None else {
            **file_record(attempt_dir / 'routability-experiment.json'),
            'identity_sha256': sidecar.get('identity_sha256'),
            'comparison_eligible': sidecar.get('comparison_eligible'),
            'ineligible_reasons': sidecar.get('ineligible_reasons'),
            'policy_qualification': sidecar.get('policy_qualification'),
            'identity_policy': (sidecar.get('identity') or {}).get('policy'),
            'identity_build': (sidecar.get('identity') or {}).get('build'),
            'identity_input': {k: v for k, v in ((sidecar.get('identity') or {}).get('input') or {}).items()
                               if k in ('fingerprint', 'board', 'project', 'source')},
            'candidate': sidecar.get('candidate'), 'outcome': sidecar.get('outcome')},
        'policy_copy': file_record(attempt_dir / 'experiment-policy.json'),
        'effective_configuration': file_record(attempt_dir / 'work/effective-configuration.json'),
        'effective_configuration_request': file_record(attempt_dir / 'work/effective-configuration-request.json'),
        'verify': summary, 'adopt': adopted,
        'design_before': before['sha256'], 'design_after': after['sha256'],
        'design_changed': sorted(k for k in set(before['files']) | set(after['files'])
                                 if before['files'].get(k) != after['files'].get(k)),
    }

    controls = {}
    # (a) another runtime, presented to the same proposed pin.
    mismatch_host = outcome(lambda: declare(args, args.mismatch, args.commit, code, policy))
    neg_design = design_copy(out / 'neg-runtime/design')
    neg_spec = json.loads(json.dumps(spec))
    neg_spec['mounts'] = [{'source': str(Path(args.mismatch).resolve()), 'at': m['at']}
                          if m['at'] == attempt.SANDBOX['checkout'] else m for m in neg_spec['mounts']]
    neg_spec['mounts'] = [{'source': str(driver), 'at': m['at']} if m['at'] == '/opt/driver' else m
                          for m in neg_spec['mounts']]
    neg_dir = out / 'neg-runtime/receipts/1-kicad-route-builtin'
    neg = stage_receipts.run_step(neg_dir, neg_design, neg_spec, BINDING)
    neg_report = json.loads((neg_dir / 'run/routing/adapter-report.json').read_text())
    controls['a_mismatched_runtime'] = {
        'runtime': engine_identity(args.mismatch) | {'extension_sha256': sha256(Path(args.mismatch) / json.loads(
            (Path(args.mismatch) / 'manifest.json').read_text())['krt']['extension_module']['path'])},
        'host_step': mismatch_host if not mismatch_host['accepted'] else {'accepted': True},
        'sandbox': {'receipt_id': neg['receipt_id'], 'exit_code': neg['exit_code'], 'status': neg['status'],
                    'classification': neg_report['semantics']['classification'],
                    'reasons': neg_report['semantics']['reasons'],
                    'experiment': neg_report['semantics'].get('experiment'), 'candidate': neg_report['candidate']},
        'verify': outcome(lambda: routing_adapter.verify(neg_dir / 'run', receipt=neg, layout=layout)),
        'adopt': outcome(lambda: stage_receipts.adopt(neg_dir, neg, neg_design)),
    }
    # (b) one output byte tampered in a copy of the positive receipt.
    tamper_dir = out / 'neg-tamper/receipts/1-kicad-route-builtin'
    shutil.copytree(receipt_dir, tamper_dir, symlinks=True)
    tamper_design = design_copy(out / 'neg-tamper/design')
    tampered = {}
    for target, rel in (('candidate', next(iter(spec['writes'].values()))),
                        ('sidecar', 'routing/attempt/routability-experiment.json')):
        path = tamper_dir / 'run' / rel
        if not path.is_file():
            tampered[target] = {'skipped': f'{rel} absent in the positive'}
            continue
        original = path.read_bytes()
        data = bytearray(original)
        data[len(data) // 2] ^= 0x01
        path.write_bytes(bytes(data))
        tampered[target] = {
            'file': rel, 'offset': len(data) // 2, 'sha256_before': hashlib.sha256(original).hexdigest(),
            'sha256_after': hashlib.sha256(bytes(data)).hexdigest(),
            'validate_receipt': outcome(lambda: stage_receipts.validate_receipt(tamper_dir) == receipt),
            'verify': outcome(lambda: routing_adapter.verify(tamper_dir / 'run', receipt=receipt, layout=layout)),
            'adopt': outcome(lambda: stage_receipts.adopt(tamper_dir, receipt, tamper_design)),
        }
        path.write_bytes(original)
    controls['b_tampered_output'] = tampered
    # (c) a stale input identity: the same receipt against a design whose board differs by one byte.
    stale_design = design_copy(out / 'neg-stale/design')
    board = stale_design / 'project' / BOARD
    board.write_bytes(board.read_bytes().replace(b'(generator_version', b'(generator_version ', 1)
                      if b'(generator_version' in board.read_bytes() else board.read_bytes() + b'\n')
    stale_inputs = attempt.files_of(attempt.tree(stale_design))
    controls['c_stale_input'] = {
        'board_sha256': {'receipt_input': receipt['inputs'].get(f'project/{BOARD}'),
                         'stale': stale_inputs[f'project/{BOARD}']},
        'verify': outcome(lambda: routing_adapter.verify(run, stale_inputs, layout=layout)),
        'adopt': outcome(lambda: stage_receipts.adopt(receipt_dir, receipt, stale_design)),
    }
    manifest['controls'] = controls
    manifest['wall_s'] = round(time.monotonic() - started, 1)

    experiment = (summary.get('value') or {}).get('experiment') or {}
    refused = all(not c['accepted'] for c in (controls['a_mismatched_runtime']['verify'],
                                              controls['a_mismatched_runtime']['adopt'],
                                              controls['c_stale_input']['verify'], controls['c_stale_input']['adopt'])) \
        and controls['a_mismatched_runtime']['sandbox']['classification'] == 'not_run' \
        and not controls['a_mismatched_runtime']['host_step']['accepted'] \
        and all(not t['verify']['accepted'] and not t['adopt']['accepted']
                for t in tampered.values() if 'verify' in t)
    manifest['verdict'] = {'positive_retained_and_adopted': bool(
        summary['accepted'] and adopted['accepted'] and experiment.get('status') == 'retained'
        and experiment.get('qualified') is False), 'controls_refused': refused}
    (out / 'evidence-manifest.json').write_text(json.dumps(manifest, indent=2, default=str) + '\n')
    print(json.dumps(manifest['verdict']))
    return 0 if all(manifest['verdict'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
