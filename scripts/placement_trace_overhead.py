#!/usr/bin/env python3
"""Measure placement trace overhead on owned fixtures (AGENTEE-430 G0 experiment gate).

Runs ``placement_trace.overhead`` (matched, alternating tracing-off/on ``pack`` runs)
on the original pod-scale qualification boards (scripts/qualify_pod_placement.py,
native generation in the sandbox) and the original search/cluster unit fixtures, and
prints one JSON record. The <= 5% median gate is an experiment gate, not a physical
constraint; a new host or session is a new measurement.

    PYTHONPATH=src:tests:. python3 scripts/placement_trace_overhead.py --runs 5 [--no-native]
"""
import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT)]

from agent_ee import placement_trace  # noqa: E402


def unit_cases():
    from test_placer_clusters import three_cap_case
    from test_placer_search import anchor_case, promotion_case
    return [('search-promotion', *promotion_case()), ('search-anchor', *anchor_case()),
            ('cluster-three-cap', *three_cap_case())]


def pod_cases(root):
    from scripts.qualify_pod_placement import CASES, prepare
    from agent_ee.placement_probe import extract
    policy = dict(schema='agent-ee.packing-policy/1', step_budget=200000, gap_nm=250000,
                  min_courtyard_edge_mm=0.5, fixed_refs=[], priority_refs=[], ground_nets=['GND'], obstacles=[])
    out = []
    for case in CASES[:2]:
        width, height, count = case
        inputs = prepare(root / f'{width}x{height}-{count}', case)
        out.append((f'pod-{width}x{height}-{count}', extract(inputs / 'board.kicad_pcb'), policy))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--no-native', action='store_true', help='skip the natively generated pod-scale boards')
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='agent-ee-trace-overhead-') as tmp:
        cases = unit_cases() + ([] if args.no_native else pod_cases(Path(tmp)))
        rows = []
        for name, capture, policy in cases:
            started = time.monotonic()
            result = placement_trace.overhead(capture, policy, runs=args.runs, warmups=args.warmups)
            plan, document = placement_trace.traced_pack(capture, policy)
            rows.append({'case': name, 'footprints': len(capture['footprints']), 'status': plan['status'],
                         'steps_used': plan.get('steps_used'), 'counters': document['counters'],
                         'stage_ns': {r['name']: r['value'] for r in document['durations'] if r['value']},
                         'sampling': document['sampling'], 'wall_s': round(time.monotonic() - started, 3),
                         **{k: result[k] for k in ('median_off_ns', 'median_on_ns', 'median_envelope_ns',
                                                   'median_overhead', 'median_overhead_with_envelope',
                                                   'outputs_identical', 'envelope_bytes_max', 'off_ns', 'on_ns')}})
    print(json.dumps({'schema': 'agent-ee.placement-trace-overhead-report/1', 'runs': args.runs,
                      'warmups': args.warmups, 'python': platform.python_version(), 'machine': platform.machine(),
                      'gate': {'kind': 'experiment', 'provisional_max_median_overhead': 0.05},
                      'cases': rows}, indent=2, sort_keys=True))
    return 0 if all(r['outputs_identical'] for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
