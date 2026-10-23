#!/usr/bin/env python3
"""Freeze the native facts at the same boundary used by place_candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('board', 'netlist', 'request', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    import pcbnew
    from agent_ee.placement_probe import extract
    from agent_ee.placer_runner import _effective_policy, reserve_imu_markings
    request = json.loads(args.request.read_bytes())
    facts = extract(args.board)
    policy = reserve_imu_markings(pcbnew.LoadBoard(str(args.board)), _effective_policy(request))
    payload = {'schema': 'agent-ee.rust-planning-input/1', 'facts': facts, 'request': request,
               'policy': policy, 'netlist_xml': args.netlist.read_bytes().decode('utf-8')}
    raw = (json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    with args.out.open('xb') as output:
        output.write(raw)
    print(json.dumps({'path': str(args.out), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'board_sha256': facts['board_sha256'], 'pcbnew_version': facts['pcbnew_version'],
                      'footprints': len(facts['footprints'])}))


if __name__ == '__main__':
    main()
