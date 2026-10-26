#!/usr/bin/env python3
"""Measure navigation mechanics on an existing retained run, without model calls."""
import argparse
import json
import statistics
import time
from pathlib import Path

from agent_ee.baseline import load_baseline
from agent_ee.run_record import verify
from agent_ee.packet_index import build_index, page
from agent_ee.storage import canonical, identity, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    manifest = read_json(args.run / 'candidate.json')
    result = verify(args.run, current_source=False)
    packet = read_json(args.run / 'packet.json')
    baseline = load_baseline(args.run)
    durations = []
    for _ in range(10):
        start = time.perf_counter()
        built = build_index(args.run, manifest, baseline, result['checks'], result['run_id'])
        durations.append(time.perf_counter() - start)
    start = time.perf_counter()
    view = page(built['index'])
    page_seconds = time.perf_counter() - start
    prefix_packet = {k: v for k, v in packet.items() if k not in ('navigation', 'evidence_access', 'packet_sha256')}
    prefix_packet['packet_sha256'] = identity(prefix_packet)
    print(json.dumps({'schema': 'agent-ee.navigation-benchmark/1',
        'candidate_id': manifest['candidate_id'], 'baseline_id': built['index']['baseline_id'],
        'index_id': built['index']['index_id'], 'repetitions': len(durations),
        'build_seconds_median': statistics.median(durations), 'page_seconds': page_seconds,
        'prefix_packet_bytes': len(canonical(prefix_packet)),
        'indexed_packet_bytes': len(canonical(packet)), 'display_bytes': len(json.dumps(view).encode('utf-8')),
        'index_bytes': len(canonical(built['index'])), 'entry_count': len(built['index']['entries']),
        'token_counts': None, 'tokenizer': None, 'provider_usage': None,
        'model_missed_defect_outcomes': 'not measured; no provider calls',
        'limits': ['Packet/index bytes use canonical UTF-8 JSON; display bytes use prompt JSON encoding. Neither measures tokens or provider cost.',
                   'Timings measure local navigation construction/retrieval, not reviewer latency.',
                   'No model detection or savings claim.']}, indent=2))


if __name__ == '__main__':
    main()
