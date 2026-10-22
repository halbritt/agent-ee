#!/usr/bin/env python3
"""Successor harness for the ONE authorized native K91 positive (AGENTEE-442, positive-c).

    k91_native_positive_v2.py --checkout L --frozen-source F --mismatch M --out OUT
        --commit SHA --source-sha256 H --extension-sha256 H --packages-sha256 H --python-version V
        --input-tree-sha256 H --board-sha256 H --rule-areas-sha256 H
        [--router-limit-s 900] [--total-limit-s 2400] [--budget S]

The consumer path, the isolated proposed pin and the private layout are those
of scripts/k91_native_positive.py (frozen at 553e4151; its helpers are imported,
the file is unchanged). This version:

- checks the EXACT runtime (commit, engine source in L and in F, extension
  module, venv packages, Python, manifest drift) and the EXACT input (tree
  digest of the disposable copy and its board digest) before anything runs;
  any mismatch stops with exit 3 and no router run;
- bounds the router process by --router-limit-s (the adapter's own router
  timeout; the budget must be below it) and the whole run by --total-limit-s
  (run_step's deadline, and every later stage checks the remaining time);
- records the three negative controls distinctly: (a) the mismatched runtime
  M, (b) one candidate-board byte tampered in a copy of the positive receipt
  (with the retained sidecar tamper as a separate control), (c) a stale input;
- judges them with scripts/k91_classify.py CLASSIFIER_V2: a coherent verified
  not_run is a refusal, a missing candidate-board control is incomplete, and
  the positive is accepted only when its candidate was adopted.

Exit 0 only for an accepted positive with every control refused; 3 for an
identity stop; 1 otherwise. OUT/evidence-manifest.json is the record.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import k91_classify as classify  # noqa: E402
import k91_native_positive as v1  # noqa: E402
from k91_native_positive import attempt, routing_adapter, stage_receipts  # noqa: E402

BINDING = v1.BINDING
SCHEMA = 'agent-ee.k91-native-positive/2'


class OutOfTime(RuntimeError):
    pass


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    for name in ('--checkout', '--frozen-source', '--mismatch', '--out', '--commit', '--source-sha256',
                 '--extension-sha256', '--packages-sha256', '--python-version', '--input-tree-sha256',
                 '--board-sha256', '--rule-areas-sha256'):
        ap.add_argument(name, required=True)
    ap.add_argument('--project-dir', default=str(v1.FIXTURE / 'control'))
    ap.add_argument('--requirements-dir', default=str(v1.FIXTURE / 'requirements'))
    ap.add_argument('--board', default=v1.BOARD)
    ap.add_argument('--schematic', default=v1.SCHEMATIC)
    ap.add_argument('--name', default=v1.NAME)
    ap.add_argument('--budget', type=int, default=300)
    ap.add_argument('--router-limit-s', type=int, default=900)
    ap.add_argument('--total-limit-s', type=int, default=2400)
    args = ap.parse_args(argv)
    if not 0 < args.budget < args.router_limit_s <= args.total_limit_s:
        ap.error('need 0 < budget < router limit <= total limit')
    return args


def design_copy(args, root):
    shutil.copytree(args.project_dir, root / 'project')
    shutil.copytree(args.requirements_dir, root / 'requirements')
    return root


def runtime_mismatch(args, found, mismatch):
    """v1's identity checks plus the exact packages and Python."""
    mismatch = dict(mismatch)
    for key, want in (('packages_sha256', args.packages_sha256), ('python_version', args.python_version)):
        if found[key] != want:
            mismatch[key] = {'expected': want, 'found': found[key]}
    return mismatch


def input_mismatch(args, record, board):
    mismatch = {}
    if record['sha256'] != args.input_tree_sha256:
        mismatch['input_tree_sha256'] = {'expected': args.input_tree_sha256, 'found': record['sha256']}
    found = record['files'].get(f'project/{args.board}')
    if found != args.board_sha256 or board != args.board_sha256:
        mismatch['board_sha256'] = {'expected': args.board_sha256, 'found': found}
    return mismatch


def observe(fn):
    """An observed call: {'attempted': True, 'accepted': bool, 'value' | 'error'} (k91_classify schema)."""
    try:
        return {'attempted': True, 'accepted': True, 'value': fn()}
    except Exception as exc:  # recorded verbatim; every control expects a refusal here
        return {'attempted': True, 'accepted': False, 'error': f'{type(exc).__name__}: {exc}' or type(exc).__name__}


def not_made(reason):
    return {'attempted': False, 'reason': reason}


def chained(verify, adopt):
    """verify(); the consumer's adoption only when verify() did not refuse (recorded as not made
    otherwise); and the bare stage adoption, always made and recorded apart as direct_adopt. A call
    not made is never a refusal (k91_classify): the bare adoption is the observed adoption evidence."""
    checked = observe(verify)
    refused = not checked['accepted'] or classify.not_run_refusal(checked['value'])
    return {'verify': checked,
            'adopt': not_made('consumer adoption follows only a verify() that did not refuse') if refused
            else observe(adopt),
            'direct_adopt': observe(adopt)}


def tamper(receipt_dir, rel, receipt, layout, design_root, args):
    """One flipped byte in a copy of the positive receipt; incomplete when the file does not exist."""
    copy = design_root / 'receipts/1-kicad-route-builtin'
    shutil.copytree(receipt_dir, copy, symlinks=True)
    design = design_copy(args, design_root / 'design')
    path = copy / 'run' / rel
    if not path.is_file():
        return {'skipped': f'{rel} absent in the positive', 'file': rel}
    original = path.read_bytes()
    data = bytearray(original)
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    return {'file': rel, 'offset': len(data) // 2, 'sha256_before': hashlib.sha256(original).hexdigest(),
            'sha256_after': hashlib.sha256(bytes(data)).hexdigest(),
            'validate_receipt': observe(lambda: stage_receipts.validate_receipt(copy) == receipt),
            **chained(lambda: routing_adapter.verify(copy / 'run', receipt=receipt, layout=layout),
                      lambda: stage_receipts.adopt(copy, receipt, design))}


def main(argv=None):
    args = parse(argv)
    started = time.monotonic()
    deadline = started + args.total_limit_s

    def remaining(stage):
        left = deadline - time.monotonic()
        if left <= 0:
            raise OutOfTime(f'total limit {args.total_limit_s} s exhausted before {stage}')
        return left

    out = Path(args.out)
    out.mkdir(parents=True)
    checkout = Path(args.checkout)
    manifest = {'schema': SCHEMA, 'classifier': classify.CLASSIFIER_V2, 'item': 'AGENTEE-442',
                'agent_ee_head': subprocess.run(['git', '-C', str(v1.ROOT), 'rev-parse', 'HEAD'],
                                                capture_output=True, text=True, check=True).stdout.strip(),
                'harness_sha256': v1.sha256(__file__), 'classifier_sha256': v1.sha256(classify.__file__),
                'base_harness_sha256': v1.sha256(v1.__file__),
                'adapter_sha256': v1.sha256(routing_adapter.ADAPTER_SOURCE), 'production_pin': attempt.PIN,
                'limits': {'router_s': args.router_limit_s, 'total_s': args.total_limit_s, 'budget_s': args.budget},
                'qualified': False}

    def write():
        manifest['wall_s'] = round(time.monotonic() - started, 1)
        (out / 'evidence-manifest.json').write_text(json.dumps(manifest, indent=2, default=str) + '\n')

    found, mismatch = v1.identities(args)
    mismatch = runtime_mismatch(args, found, mismatch)
    manifest['runtime'] = {'description': 'proposed pin source + copied existing venv, private checkout-mode layout; '
                                          'not an installed router generation', **found}
    design = design_copy(args, out / 'design')
    before = v1.tree_record(design)
    board_sha = v1.sha256(design / 'project' / args.board)
    mismatch.update(input_mismatch(args, before, board_sha))
    manifest['input'] = {'project_dir': args.project_dir, 'requirements_dir': args.requirements_dir,
                         'board': args.board, 'tree_sha256': before['sha256'], 'board_sha256': board_sha}
    if mismatch:
        manifest['stopped'] = {'reason': 'identity mismatch; the router was not run', 'mismatch': mismatch}
        write()
        print(json.dumps(manifest['stopped'], indent=2))
        return 3
    code = v1.code_map(checkout, args.commit)
    if attempt.verify_code(checkout, code):
        raise SystemExit('checkout work tree differs from its commit: ' + '; '.join(attempt.verify_code(checkout, code)))
    layout = {'pin': args.commit, 'code': code}
    manifest['pin'] = {'commit': args.commit, 'code_files': len(code),
                       'code_sha256': hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()}
    driver = out / 'driver'
    driver.mkdir()
    (driver / 'layout.json').write_text(json.dumps(layout))
    (driver / 'run.py').write_text(v1.DRIVER)
    policy = {'schema': 'routability-policy/1', 'experiment_class': 'current-policy',
              'allowed_trace_layers': ['F.Cu', 'B.Cu'], 'allowed_via_spans': [['F.Cu', 'B.Cu']],
              'rule_areas_sha256': args.rule_areas_sha256}
    manifest['policy'] = {'declared': policy, 'canonical_sha256': hashlib.sha256(
        attempt.canonical_json(policy).encode()).hexdigest()}

    def declare(where):
        with v1.patch.object(attempt, 'PIN', args.commit), v1.patch.object(attempt, 'PINNED_CODE', code):
            return routing_adapter.step(project='project', board=args.board, schematic=args.schematic,
                                        name=args.name, label='k91-native-positive', checkout=where,
                                        budget=args.budget, timeout=args.router_limit_s, experiment_policy=policy)

    try:
        spec = v1.drive(declare(checkout), driver)
        (out / 'step.json').write_text(json.dumps(spec, indent=2) + '\n')
        receipt_dir = out / 'receipts/1-kicad-route-builtin'
        t0 = time.monotonic()
        receipt = stage_receipts.run_step(receipt_dir, design, spec, BINDING, deadline=deadline)
        run_s = time.monotonic() - t0
        run = receipt_dir / 'run'
        report = json.loads((run / 'routing/adapter-report.json').read_text())
        remaining('verify')
        summary = observe(lambda: routing_adapter.verify(run, receipt=receipt, layout=layout))
        adoptable = summary['accepted'] and (summary['value'] or {}).get('candidate_sha256')
        adopted = observe(lambda: stage_receipts.adopt(receipt_dir, receipt, design)) if adoptable else \
            not_made('verify delivered no candidate')
        after = v1.tree_record(design)
        attempt_dir = run / 'routing/attempt'
        sidecar_path = attempt_dir / 'routability-experiment.json'
        sidecar = json.loads(sidecar_path.read_text()) if sidecar_path.is_file() else None
        manifest['positive'] = {
            'step_sha256': v1.sha256(out / 'step.json'), 'receipt_id': receipt['receipt_id'],
            'receipt_sha256': v1.sha256(receipt_dir / 'receipt.json'),
            'receipt_validated': stage_receipts.validate_receipt(receipt_dir) == receipt,
            'exit_code': receipt['exit_code'], 'status': receipt['status'], 'violations': receipt['violations'],
            'duration_s': receipt['duration_s'], 'run_step_wall_s': round(run_s, 1),
            'step_timeout_s': spec['timeout_s'], 'receipt_outputs': receipt['outputs'],
            'adapter_report_sha256': v1.sha256(run / 'routing/adapter-report.json'),
            'semantics': report.get('semantics'), 'report_backend': report.get('backend'),
            'sidecar': None if sidecar is None else {
                **v1.file_record(sidecar_path), 'identity_sha256': sidecar.get('identity_sha256'),
                'comparison_eligible': sidecar.get('comparison_eligible'),
                'ineligible_reasons': sidecar.get('ineligible_reasons'),
                'policy_qualification': sidecar.get('policy_qualification'),
                'identity_build': (sidecar.get('identity') or {}).get('build')},
            'policy_copy': v1.file_record(attempt_dir / 'experiment-policy.json'),
            'verify': summary, 'adopt': adopted, 'design_before': before['sha256'], 'design_after': after['sha256'],
            'design_changed': sorted(k for k in set(before['files']) | set(after['files'])
                                     if before['files'].get(k) != after['files'].get(k))}
        write()

        controls = {}
        remaining('control a')
        host = observe(lambda: declare(args.mismatch))
        neg_design = design_copy(args, out / 'neg-runtime/design')
        neg_spec = json.loads(json.dumps(spec))
        neg_spec['mounts'] = [{'source': str(Path(args.mismatch).resolve()), 'at': m['at']}
                              if m['at'] == attempt.SANDBOX['checkout'] else m for m in neg_spec['mounts']]
        neg_dir = out / 'neg-runtime/receipts/1-kicad-route-builtin'
        neg = stage_receipts.run_step(neg_dir, neg_design, neg_spec, BINDING, deadline=deadline)
        neg_report = json.loads((neg_dir / 'run/routing/adapter-report.json').read_text())
        controls['a_mismatched_runtime'] = {
            'host_step': host if not host['accepted'] else {'attempted': True, 'accepted': True, 'value': None},
            'sandbox': {'receipt_id': neg['receipt_id'], 'exit_code': neg['exit_code'], 'status': neg['status'],
                        'classification': neg_report['semantics']['classification'],
                        'reasons': neg_report['semantics']['reasons'], 'candidate': neg_report['candidate']},
            **chained(lambda: routing_adapter.verify(neg_dir / 'run', receipt=neg, layout=layout),
                      lambda: stage_receipts.adopt(neg_dir, neg, neg_design))}
        remaining('control b')
        controls['b_tampered_candidate'] = tamper(receipt_dir, next(iter(spec['writes'].values())), receipt, layout,
                                                  out / 'neg-tamper-candidate', args)
        controls['b_tampered_sidecar'] = tamper(receipt_dir, 'routing/attempt/routability-experiment.json', receipt,
                                                layout, out / 'neg-tamper-sidecar', args)
        remaining('control c')
        stale_design = design_copy(args, out / 'neg-stale/design')
        board = stale_design / 'project' / args.board
        board.write_bytes(board.read_bytes() + b'\n')
        stale_inputs = attempt.files_of(attempt.tree(stale_design))
        controls['c_stale_input'] = {
            'board_sha256': {'receipt_input': receipt['inputs'].get(f'project/{args.board}'),
                             'stale': stale_inputs[f'project/{args.board}']},
            **chained(lambda: routing_adapter.verify(run, stale_inputs, layout=layout),
                      lambda: stage_receipts.adopt(receipt_dir, receipt, stale_design))}
        manifest['controls'] = controls
    except OutOfTime as exc:
        manifest['out_of_time'] = str(exc)
        manifest.setdefault('controls', {})
    if 'positive' not in manifest:
        manifest['positive'] = {}
    manifest['verdict'] = classify.v2_verdict(manifest)
    write()
    print(json.dumps(manifest['verdict']))
    verdict = manifest['verdict']
    return 0 if verdict['positive_accepted'] and verdict['controls_refused'] and 'out_of_time' not in manifest else 1


if __name__ == '__main__':
    sys.exit(main())
