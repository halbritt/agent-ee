"""AGENTEE-454 profile harness: count qualification passes inside existing unittest cases.

Runs the named test ids under cProfile and reports, for each watched agent_ee
function, its call count and its direct callers with their call counts. Only the
current process is profiled; sandboxed child steps (stage_receipts.run_step) run
in their own processes and are not counted. Diagnostic tooling, not src/.
"""
import argparse
import cProfile
import json
from pathlib import Path
import pstats
import sys
import unittest

WATCH = ('parts_manifest.py:dependency_closure', 'parts_manifest.py:qualify_manifest',
         'part_record.py:qualify_record', 'part_record.py:_sources',
         'datasheet_facts.py:load_acquisition', 'baseline.py:compile_baseline', 'baseline.py:load_baseline',
         'pod_driver.py:validate_configuration', 'scaffold.py:scaffold', 'pod_driver.py:run_dry',
         'board_envelope.py:identity_witness', 'storage.py:digest')


def label(key):
    filename, line, func = key
    return f'{Path(filename).name}:{func}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tests', nargs='+')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests)
    profiler = cProfile.Profile()
    profiler.enable()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    profiler.disable()
    stats = pstats.Stats(profiler)
    watched = {}
    for key, (cc, nc, tt, ct, callers) in stats.stats.items():
        name = label(key)
        if name in WATCH:
            entry = watched.setdefault(name, dict(calls=0, cumtime=0.0, callers={}))
            entry['calls'] += nc
            entry['cumtime'] = round(entry['cumtime'] + ct, 4)
            for caller, values in callers.items():
                entry['callers'][label(caller)] = entry['callers'].get(label(caller), 0) + values[1]
    record = dict(schema='agent-ee.ee454-call-count/1', tests=args.tests, tests_run=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                  python=sys.version.split()[0], watched=dict(sorted(watched.items())))
    Path(args.out).write_text(json.dumps(record, indent=2) + '\n')
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    sys.exit(main())
