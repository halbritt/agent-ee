#!/usr/bin/env python3
"""Capped matched-snapshot reviewer A/B for packet navigation (AGENTEE-12)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_ee import packet_ab
from agent_ee.storage import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('plan', help='print the preregistered matrix and its identity')
    ledger = sub.add_parser('ledger', help='print call ledger counts')
    ledger.add_argument('ledger', type=Path)
    prepare = sub.add_parser('prepare', help='checks-only capture and both arm packets; no provider call')
    prepare.add_argument('root', type=Path)
    execute = sub.add_parser('run', help='run every planned cell through the call ledger')
    execute.add_argument('root', type=Path)
    execute.add_argument('--ledger', type=Path, required=True)
    execute.add_argument('--mode', choices=('dry-run', 'live'), required=True)
    report = sub.add_parser('report', help='summarise a run record')
    report.add_argument('run_record', type=Path)
    report.add_argument('--out', type=Path)
    manifest = sub.add_parser('manifest', help='SHA-256 manifest of every file under a directory')
    manifest.add_argument('root', type=Path)
    manifest.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        plan = packet_ab.plan()
        value = {**plan, 'plan_id': packet_ab.identity(plan)}
    elif args.command == 'ledger':
        value = packet_ab.CallLedger(args.ledger).state()
    elif args.command == 'prepare':
        value = packet_ab.prepare(args.root, repository=Path(__file__).resolve().parents[1])
    elif args.command == 'run':
        outcome = packet_ab.run(args.root, args.ledger, args.mode)
        value = {'ledger': outcome['ledger'], 'cells': [{k: c.get(k) for k in ('id', 'execution', 'score')}
                                                        for c in outcome['cells']]}
    elif args.command == 'report':
        value = packet_ab.report(read_json(args.run_record))
        if args.out:
            write_json(args.out, value)
    else:
        value = packet_ab.manifest(args.root)
        write_json(args.out, value)
        value = {'files': len(value['files'])}
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
