#!/usr/bin/env python3
"""Measure placement trace overhead on owned fixtures (AGENTEE-430 G0 experiment gate).

Runs ``placement_trace.overhead`` (matched, alternating tracing-off/on ``pack`` runs)
on the original pod-scale qualification boards (scripts/qualify_pod_placement.py,
native generation in the sandbox) and the original search/cluster unit fixtures, and
prints one JSON record. The <= 5% median gate is an experiment gate, not a physical
constraint; a new host or session is a new measurement. Per the coordinator ruling on
AGENTEE-430 H6 it applies only to fixtures whose tracing-off median is >= 100 ms, on an
unloaded host (1-minute load average < 2 when the measurement starts and ends); shorter
fixtures are reported, not gated.

    PYTHONPATH=src:tests:. python3 scripts/placement_trace_overhead.py --runs 5 [--no-native]
"""
import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT)]

from agent_ee import placement_trace  # noqa: E402

MAX_LOAD = 2.0  # The gate is measured on an unloaded host: 1-minute load average below this.
# Exit 0 asserts only that every plan was identical with tracing on and off; never gate passage.
EXIT_STATUS = 'output identity only: 0 when every plan is identical with tracing on and off; gate.met is the gate'


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
    load_before = os.getloadavg()
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
                         **{k: result[k] for k in ('median_off_ns', 'median_on_ns', 'median_recording_ns',
                                                   'median_export_ns', 'median_overhead', 'median_recording_overhead',
                                                   'outputs_identical', 'trace_bytes_max', 'off_ns', 'on_ns',
                                                   'recording_ns', 'export_ns')},
                         'gate_applies': result['gate']['applies'], 'gate_met': result['gate']['met']})
    load_after = os.getloadavg()
    gated = [r for r in rows if r['gate_applies']]
    unloaded = max(load_before[0], load_after[0]) < MAX_LOAD
    # Met only on an unloaded host, by every gated fixture, with at least one gated.
    met = unloaded and bool(gated) and all(r['gate_met'] for r in gated)
    print(json.dumps({'schema': 'agent-ee.placement-trace-overhead-report/3', 'runs': args.runs,
                      'warmups': args.warmups, 'python': platform.python_version(), 'machine': platform.machine(),
                      'host': platform.node(), 'cpus': os.cpu_count(),
                      'load_average': {'before': list(load_before), 'after': list(load_after)},
                      'gate': {'kind': 'experiment',
                               'provisional_max_median_overhead': placement_trace.GATE_MAX_OVERHEAD,
                               'boundary': 'recording, envelope assembly and export to a fresh file',
                               'min_off_ns': placement_trace.GATE_MIN_OFF_NS, 'max_load_average_1m': MAX_LOAD,
                               'host_unloaded': unloaded,
                               'met': met,
                               'met_by': [r['case'] for r in gated if r['gate_met']],
                               'exceeded_by': [r['case'] for r in gated if not r['gate_met']],
                               'reported_not_gated': [r['case'] for r in rows if not r['gate_applies']]},
                      'exit_status': EXIT_STATUS, 'cases': rows}, indent=2, sort_keys=True))
    if not met:
        print('placement trace overhead: gate.met=false (experiment gate not met; tracing stays an off-by-default '
              'experiment)', file=sys.stderr)
    return 0 if all(r['outputs_identical'] for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
