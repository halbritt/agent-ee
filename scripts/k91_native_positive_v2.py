#!/usr/bin/env python3
"""Successor harness for the ONE authorized native K91 positive (AGENTEE-442, positive-c).

    k91_native_positive_v2.py --checkout L --frozen-source F --mismatch M --out OUT
        --commit SHA --source-sha256 H --extension-sha256 H --packages-sha256 H --python-version V
        --input-tree-sha256 H --board-sha256 H --rule-areas-sha256 H
        [--router-limit-s 900] [--total-limit-s 2400] [--budget S]

The runtime identity checks and the private layout are those of
scripts/k91_native_positive.py (frozen at 553e4151; its helpers are imported,
the file is unchanged). The boundary is the production routing consumer
(coordinator boundary ruling a01d6f91): campaign.run_implementation_steps,
called through scripts/k91_consumer.py with the proposed pin in the
production adapter shape. This version:

- checks the EXACT runtime (commit, engine source in L and in F, extension
  module, venv packages, Python, manifest drift) and the EXACT input (tree
  digest of the disposable copy and its board digest) before anything runs;
  any mismatch stops with exit 3 and no router run;
- bounds the router process by --router-limit-s (the adapter's own router
  timeout; the budget must be below it) and the whole run by --total-limit-s
  (the deadline run_step receives from the consumer, checked again before the
  controls);
- runs the positive once through the production consumer (run_step executes
  the router; verify_campaign_receipt and adopt follow in the consumer's order);
- runs the controls through the same consumer: (a) the mismatched runtime M
  (production binding refusal, and the consumer executing it), (b) one
  candidate-board byte, and separately one sidecar byte, flipped in a
  disposable copy of the positive receipt replayed at its own path, (c) a
  stale input. The bare stage adoption is recorded apart, on another design
  copy;
- judges them with scripts/k91_classify.py CLASSIFIER_V2 revision 4: each control is refused
  only by a positive match of its contract (CONTRACTS: origin, reason, path, layers, preconditions).

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
import k91_consumer as consumer  # noqa: E402
import k91_native_positive as v1  # noqa: E402
from k91_native_positive import attempt, routing_adapter  # noqa: E402

SCHEMA = 'agent-ee.k91-native-positive/2'


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


def main(argv=None):
    args = parse(argv)
    started = time.monotonic()
    deadline = started + args.total_limit_s
    out = Path(args.out)
    out.mkdir(parents=True)
    checkout = Path(args.checkout)
    manifest = {'schema': SCHEMA, 'classifier': classify.CLASSIFIER_V2, 'item': 'AGENTEE-442',
                'boundary': 'campaign.run_implementation_steps (coordinator boundary ruling a01d6f91)',
                'agent_ee_head': subprocess.run(['git', '-C', str(v1.ROOT), 'rev-parse', 'HEAD'],
                                                capture_output=True, text=True, check=True).stdout.strip(),
                'harness_sha256': v1.sha256(__file__), 'classifier_sha256': v1.sha256(classify.__file__),
                'consumer_sha256': v1.sha256(consumer.__file__), 'base_harness_sha256': v1.sha256(v1.__file__),
                'production_adapter_sha256': v1.sha256(routing_adapter.ADAPTER_SOURCE),
                'production_pin': attempt.PIN,
                'limits': {'router_s': args.router_limit_s, 'total_s': args.total_limit_s, 'budget_s': args.budget},
                'qualified': False}

    def write():
        manifest['wall_s'] = round(time.monotonic() - started, 1)
        (out / 'evidence-manifest.json').write_text(json.dumps(manifest, indent=2, default=str) + '\n')

    found, mismatch = v1.identities(args)
    mismatch = runtime_mismatch(args, found, mismatch)
    manifest['runtime'] = {'description': 'proposed pin source + copied existing venv, private checkout-mode layout; '
                                          'not an installed router generation', **found}
    probe = design_copy(args, out / 'input-identity')
    record = v1.tree_record(probe)
    board_sha = v1.sha256(probe / 'project' / args.board)
    mismatch.update(input_mismatch(args, record, board_sha))
    manifest['input'] = {'project_dir': args.project_dir, 'requirements_dir': args.requirements_dir,
                         'board': args.board, 'tree_sha256': record['sha256'], 'board_sha256': board_sha}
    if mismatch:
        manifest['stopped'] = {'reason': 'identity mismatch; the router was not run', 'mismatch': mismatch}
        write()
        print(json.dumps(manifest['stopped'], indent=2))
        return 3
    code = v1.code_map(checkout, args.commit)
    if attempt.verify_code(checkout, code):
        raise SystemExit('checkout work tree differs from its commit: ' + '; '.join(attempt.verify_code(checkout, code)))
    manifest['pin'] = {'commit': args.commit, 'code_files': len(code),
                       'code_sha256': hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()}
    policy = {'schema': 'routability-policy/1', 'experiment_class': 'current-policy',
              'allowed_trace_layers': ['F.Cu', 'B.Cu'], 'allowed_via_spans': [['F.Cu', 'B.Cu']],
              'rule_areas_sha256': args.rule_areas_sha256}
    manifest['policy'] = {'declared': policy, 'canonical_sha256': hashlib.sha256(
        attempt.canonical_json(policy).encode()).hexdigest()}
    with consumer.pinned(out, args.commit, code) as adapter:
        manifest['pinned_adapter'] = adapter
        decl = consumer.declaration(checkout, project='project', board=args.board, schematic=args.schematic,
                                    name=args.name, budget=args.budget, timeout=args.router_limit_s, policy=policy)
        bind = consumer.binding(decl)
        (out / 'binding.json').write_text(json.dumps(bind, indent=2, default=str) + '\n')
        work = out / 'positive' / '1'
        work.mkdir(parents=True)
        positive = consumer.consume(work, design_copy(args, out / 'positive/design'), bind, deadline=deadline)
        receipt_path = work / consumer.STEP_DIR / 'receipt.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        target = f'project/{args.board}'
        positive.update(receipt_id=receipt.get('receipt_id'),
                        receipt_sha256=v1.sha256(receipt_path) if receipt_path.is_file() else None,
                        exit_code=receipt.get('exit_code'), status=receipt.get('status'),
                        duration_s=receipt.get('duration_s'),
                        delivered_board_sha256=(receipt.get('outputs') or {}).get(target))
        manifest['positive'] = positive
        write()
        if time.monotonic() >= deadline:
            manifest['out_of_time'] = f'total limit {args.total_limit_s} s exhausted before the controls'
            manifest['controls'] = {}
        else:
            manifest['controls'] = consumer.run_controls(out, work, decl, bind, args.mismatch,
                                                         lambda path: design_copy(args, path), target,
                                                         deadline=deadline)
    manifest['verdict'] = classify.v2_verdict(manifest)
    write()
    print(json.dumps(manifest['verdict']))
    verdict = manifest['verdict']
    return 0 if verdict['positive_accepted'] and verdict['controls_refused'] and 'out_of_time' not in manifest else 1


if __name__ == '__main__':
    sys.exit(main())
