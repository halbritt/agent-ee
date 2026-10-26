#!/usr/bin/env python3
"""Run and seal offline catalog/1 integrity and 1 kohm / 0603 selection probes.

Use a new private output directory. This retains captured catalog rows and is
never a publication artifact. Each helper keeps its registered 600 s ceiling.
"""
import argparse
import io
from pathlib import Path

from agent_ee.run_record import run_review, verify
from agent_ee.catalog_checks import CATALOG_TIMEOUT
from agent_ee.storage import digest, read_json, write_json


def qualify(snapshot, manifest_sha256, destination, max_seconds=1500):
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    request = destination / 'request'
    request.mkdir()
    requirements = dict(schema='agent-ee.part-requirements/1', build_quantity=1, stock_floor=1,
        parts=[dict(id='R1', category='resistor', package='0603', quantity_per_board=1,
            constraints={'resistance': dict(kind='within', nominal='1 kohm', tolerance_percent='1')})])
    write_json(request / 'parts.json', requirements)
    for name in ('board.txt', 'schematic.txt', 'target.txt', 'reference.txt'):
        (request / name).write_text('Owned catalog runtime probe context; no CAD check or design claim.\n')
    scope = dict(schema='agent-ee.scope/3', name='catalog-runtime',
        scope='Offline snapshot integrity and declared 1 kohm / 0603 constraints only',
        board='request/board.txt', schematic='request/schematic.txt',
        inputs=[dict(path=str(snapshot), at='snapshot', include=['manifest.json', 'catalog.sqlite3']),
                dict(path=str(request), at='request')],
        checks=[dict(id='catalog', kind='catalog',
                     params=dict(snapshot='snapshot', manifest_sha256=manifest_sha256)),
                dict(id='selection', kind='part_selection', params=dict(snapshot='snapshot',
                     manifest_sha256=manifest_sha256, requirements='request/parts.json', requirement_id='parts'))],
        baseline=dict(requirements=[dict(id='integrity', intent='informational',
            statement='Captured snapshot integrity and derivation consistency',
            source=dict(path='snapshot/manifest.json', locator='database'), checks=['catalog']),
            dict(id='parts', intent='acceptance',
            statement='Declared 1 kohm / 0603 requirements match captured claims',
            source=dict(path='request/parts.json', locator='parts'), checks=['selection'])],
            targets=['request/target.txt'], reference_architectures=['request/reference.txt']))
    scope_path = destination / 'scope.json'
    write_json(scope_path, scope)
    run = destination / 'run'
    # Checks-only review remains incomplete by design. Its return code is not a
    # helper verdict; verify the ordinary seal and inspect each retained check.
    run_review(scope_path, run, max_seconds, checks_only=True, output_stream=io.StringIO())
    result = verify(run)
    checks, reports = {}, {}
    for cid in ('catalog', 'selection'):
        check = read_json(run / f'checks/{cid}.json')
        execution_path = run / f'work/{cid}/execution.json'
        execution = read_json(execution_path) if execution_path.is_file() else None
        report_path = run / f'work/{cid}/catalog.json'
        checks[cid] = dict(execution=check['execution'], outcome=check['outcome'],
            timeout_s=execution['timeout_s'] if execution else None,
            duration_s=execution['duration_s'] if execution else None,
            report_sha256=digest(report_path) if report_path.is_file() else None)
        reports[cid] = check['details'].get('report')
    catalog, selection = reports['catalog'], reports['selection']
    parts = selection['parts'] if selection else []
    summary = dict(qualified=all(c['execution'] == 'ok' and c['outcome'] == 'pass'
                      and c['timeout_s'] == CATALOG_TIMEOUT and c['duration_s'] < CATALOG_TIMEOUT
                      for c in checks.values()),
        candidate_id=result['candidate_id'], baseline_sha256=digest(run / 'baseline.json'),
        evidence_seal_sha256=digest(run / 'work/verification/evidence.json'),
        snapshot_manifest_sha256=manifest_sha256,
        snapshot_sha256=catalog['details']['snapshot_sha256'] if catalog else None,
        snapshot_date=catalog['details']['snapshot_date'] if catalog else None,
        row_counts=catalog['details'].get('row_counts') if catalog else None,
        chosen_lcsc=parts[0]['chosen']['lcsc'] if parts and parts[0]['chosen'] else None,
        eligible_candidates=len(parts[0]['ranked']) if parts else 0, checks=checks,
        review_state=result['state'], review_outcome=result['outcome'])
    write_json(destination / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('manifest_sha256')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-seconds', type=float, default=1500)
    args = parser.parse_args()
    summary = qualify(args.snapshot, args.manifest_sha256, args.out, args.max_seconds)
    print(args.out / 'summary.json')
    return 0 if summary['qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
