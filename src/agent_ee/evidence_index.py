"""Read-only navigation over ordinary sealed pod evidence; never an acceptance record."""

import argparse
import json
from pathlib import Path
import sys

from .baseline import evidence_seal, load_baseline
from .candidate import safe_relative
from .cli import verify
from .locking import reader_lock
from .storage import digest, read_json, write_json


BENCH = {
    '8': 'Built-board CAN/ECU bench: required frames and rates within 5%, and ECU .mlg logging.',
    '9': 'Built-board RaceChrono Pro BLE pairing, ECU RPM, pod lean and 25 Hz GPS speed.',
    '10': 'Built-board concurrent eight-channel analog acquisition, wheel-speed range and sensor-fault validation.',
    '11': 'Built-board standalone logging duration, power interruption, full-media and digital tire-sensor validation.',
}


class CriteriaMapError(ValueError):
    """A captured criterion declaration cannot be mapped without loss or invention."""


def criteria_map(run, relative, baseline):
    """Select existing baseline requirements from an immutable captured input."""
    relative = str(safe_relative(relative))
    if relative not in baseline['files']:
        raise ValueError('criteria map must be an immutable captured baseline input')
    path = run / 'inputs' / relative
    mapping = read_json(path)
    rev2, rev3 = ({str(i) for i in range(1, n)} for n in (10, 12))
    if not isinstance(mapping, dict) or set(mapping) not in (rev2, rev3):
        raise CriteriaMapError('criteria map must contain exactly keys 1 through 9 (Rev-2) or 1 through 11 (Rev-3)')
    requirements = {r['id']: r for r in baseline['requirements']}
    covered = set()
    for number, ids in mapping.items():
        if (not isinstance(ids, list) or any(not isinstance(rid, str) for rid in ids)
                or len(ids) != len(set(ids)) or not set(ids) <= requirements.keys()):
            raise CriteriaMapError(f'criterion {number} must list unique existing baseline requirement IDs')
        covered.update(ids)
    if covered != requirements.keys():
        raise CriteriaMapError('criteria map omits baseline requirements: ' + ', '.join(sorted(requirements.keys() - covered)))
    return mapping, {'path': 'inputs/' + relative, 'sha256': baseline['files'][relative]}


def campaign_receipts(run):
    """Verified prior stage history, not a claim that a receipt satisfies a criterion."""
    from .campaign import verify_campaign_records

    root = next((p for p in run.parents if (p / '.campaign-root').is_file()), None)
    if root is None:
        return None, []
    state = verify_campaign_records(root)
    positions = [i for i, item in enumerate(state['history']) if Path(item['run']).resolve() == run]
    if len(positions) != 1:
        raise ValueError('selected run must occur exactly once in its enclosing campaign history')
    receipts = []
    for path, sha in sorted(state.get('pre_receipts', {}).items()):
        receipt = read_json(root / path)
        receipts.append({'path': str(root / path), 'sha256': sha, 'stage': receipt['spec']['stage'],
                         'status': receipt['status'], 'relation': 'pre-campaign capture'})
    for number in sorted(state.get('stage_receipts', {}), key=int):
        # Implementation N feeds run N+1. Later work is not evidence for this run.
        if int(number) > positions[0]:
            continue
        work = root / 'implementations' / number
        index = read_json(work / 'stage-receipts.json')
        for link in index['receipts']:
            receipt = read_json(work / link['path'])
            receipts.append({**link, 'path': str(work / link['path']), 'stage': receipt['spec']['stage'],
                             'status': receipt['status'],
                             'relation': 'prior campaign stage; not criterion acceptance'})
    return {'path': str(root), 'state_sha256': digest(root / 'state.json'), 'state': state['state']}, receipts


def input_references(value, files):
    """Exact captured path values in an existing check declaration, without guessing filenames."""
    if isinstance(value, str):
        return {value} if value in files else set()
    if isinstance(value, dict):
        return set().union(*(input_references(v, files) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(input_references(v, files) for v in value))
    return set()


def build_index(run, criteria):
    run = Path(run).resolve()
    with reader_lock(run):
        result = verify(run, current_source=False)
        baseline = load_baseline(run)
        if baseline is None:
            raise ValueError('evidence index requires a sealed baseline')
        mapping, mapping_source = criteria_map(run, criteria, baseline)
        seal = evidence_seal(run, result['checks'])
        retained_seal = run / 'work/verification/evidence.json'
        if read_json(retained_seal) != seal:
            raise ValueError('evidence index requires the retained check evidence seal')
        manifest = read_json(run / 'candidate.json')
        checks = {c['id']: c for c in result['checks']}
        specs = {c['id']: c for c in baseline['criteria']}
        requirements = {r['id']: r for r in baseline['requirements']}
        campaign, receipts = campaign_receipts(run)
        owner_obligations = []
        from .fab_package import artifact_record
        for check in checks.values():
            if check['kind'] == 'fab_package' and (check['execution'], check['outcome']) == ('ok', 'pass'):
                rotation = artifact_record(run, check)['rotation']
                if rotation.get('preview_obligations'):
                    owner_obligations.append({'check_id': check['id'], **rotation['preview_obligations']})
        rows = []
        for number in sorted(mapping, key=int):
            selected = [requirements[rid] for rid in mapping[number]]
            ids = sorted({cid for requirement in selected for cid in requirement['checks']})
            entries, inputs = [], set()
            for cid in ids:
                check = checks[cid]
                proof = seal['checks'][cid]
                inputs.update(input_references(specs[cid].get('params', {}), manifest['files']))
                entries.append({'id': cid, 'kind': check['kind'], 'execution': check['execution'],
                    'outcome': check['outcome'], 'result': {'path': f'checks/{cid}.json', 'sha256': proof['result']},
                    'evidence': [{'path': p, 'sha256': sha} for p, sha in sorted(proof['artifacts'].items())],
                    'limitations': check.get('limitations', [])})
            for requirement in selected:
                inputs.add(requirement['source']['path'])
            reasons = []
            if not ids:
                reasons.append('no mapped checks')
            if result['state'] != 'complete':
                reasons.append('run process is not complete')
            reasons.extend(f"{c['id']}: {c['execution']}/{c['outcome']}" for c in entries
                           if (c['execution'], c['outcome']) != ('ok', 'pass'))
            if number in BENCH:
                reasons.append('built-board bench validation remains required; file checks cannot discharge it')
            rows.append({'criterion': number, 'status': 'not_met' if reasons else 'met',
                'meaning': 'mapped sealed checks only; not specification completeness or board acceptance',
                'requirements': selected, 'checks': entries,
                'captured_inputs': [{'path': 'inputs/' + p, 'sha256': manifest['files'][p]} for p in sorted(inputs)],
                'receipts': [item for item in receipts if item['relation'] == 'pre-campaign capture'
                             or item['stage'] in {specs[cid].get('stage') for cid in ids}],
                'reasons': reasons, 'bench_obligation': BENCH.get(number),
                'owner_obligations': owner_obligations if number == '6' else []})
        known = set(result['artifact_hashes']) | {'result.json', 'candidate.json', 'baseline.json'}
        if result.get('packet_file_sha256'):
            known.add('packet.json')
        ignored = sorted(p.relative_to(run).as_posix() for p in run.rglob('*')
                         if (p.is_file() or p.is_symlink()) and p.relative_to(run).as_posix() not in known)
        return {'kind': 'pod-evidence-index', 'acceptance': False, 'run': str(run),
            'candidate_id': manifest['candidate_id'], 'baseline_id': baseline['baseline_id'],
            'run_id': result['run_id'], 'run_state': result['state'], 'run_outcome': result['outcome'],
            'result_sha256': digest(run / 'result.json'), 'criteria_mapping': mapping_source,
            'evidence_seal': {'path': 'work/verification/evidence.json', 'sha256': digest(retained_seal)},
            'criteria': rows, 'campaign': campaign, 'receipts': receipts,
            'captured_inputs': [{'path': 'inputs/' + p, 'sha256': sha} for p, sha in sorted(manifest['files'].items())],
            'owner_obligations': owner_obligations, 'ignored_unsealed': ignored,
            'limitations': [*result.get('limitations', []),
                'This index reports the captured mapping; it does not establish that the mapping covers the specification.',
                'Receipt history and captured inputs are provenance, not passing criterion results.',
                'No writer text is used to establish a criterion. Bench criteria 8 and 9 remain obligations.',
                *(['Rev-3 bench criteria 10 and 11 remain obligations.'] if '10' in mapping else [])]}


def render_index(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')

    lines = ['# Telemetry pod evidence index', '', 'This is evidence navigation, not board acceptance.', '',
             f"Candidate: `{report['candidate_id']}`", f"Run: `{report['run_id']}`", '',
             '| Criterion | Mapped check status | Checks | Remaining obligations |', '|---|---|---|---|']
    for row in report['criteria']:
        lines.append('| ' + ' | '.join(cell(v) for v in (row['criterion'], row['status'],
            ', '.join(f"{c['id']} ({c['kind']}): {c['execution']}/{c['outcome']}" for c in row['checks']),
            '; '.join(row['reasons'] + ([row['bench_obligation']] if row['bench_obligation'] else [])))) + ' |')
    for row in report['criteria']:
        lines += ['', f"## Criterion {row['criterion']}", '', row['meaning'], '']
        for requirement in row['requirements']:
            lines.append(f"- {requirement['id']}: {requirement['statement']} — {requirement['source']}")
        for check in row['checks']:
            lines.append(f"- Check `{check['id']}` result `{check['result']['path']}` SHA-256 `{check['result']['sha256']}`")
            for item in check['evidence']:
                lines.append(f"  - Evidence `{item['path']}` SHA-256 `{item['sha256']}`")
            lines += [f'  - Limitation: {item}' for item in check['limitations']]
        for item in row['receipts']:
            lines.append(f"- Receipt context `{item['path']}` SHA-256 `{item['sha256']}`; {item['relation']}")
    for title, key in [('Captured inputs', 'captured_inputs'), ('Verified campaign receipts', 'receipts')]:
        lines += ['', '## ' + title, '']
        lines += [f"- `{item['path']}` SHA-256 `{item['sha256']}`" for item in report[key]] or ['None recorded.']
    lines += ['', '## Owner obligations', '']
    for item in report['owner_obligations']:
        lines.append(f"- {item['status']}: {item['action']}; policy SHA-256 `{item['policy_sha256']}`")
        lines += [f"  - {part['designator']} ({part['side']})" for part in item['designators']]
    if not report['owner_obligations']:
        lines.append('None established by a passing sealed fab-package check; absence is not clearance to order.')
    lines += ['', '## Limitations', '', *('- ' + item for item in report['limitations']),
              '', '## Ignored unsealed files', '', *('- `' + p + '`' for p in report['ignored_unsealed']), '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--criteria', required=True, help='captured input-relative criteria mapping path')
    parser.add_argument('--out', type=Path, help='new directory outside the run/campaign; default: sibling evidence-index')
    args = parser.parse_args(argv)
    try:
        run = args.run.resolve()
        retained = next((p for p in run.parents if (p / '.campaign-root').is_file()), run)
        out = (args.out or retained.with_name(retained.name + '-evidence-index')).resolve()
        if out.is_relative_to(retained):
            raise ValueError('index output must be outside retained run/campaign evidence')
        report = build_index(run, args.criteria)
        out.mkdir(parents=True, exist_ok=False)
        write_json(out / 'index.json', report)
        (out / 'index.md').write_text(render_index(report), encoding='utf-8')
        print(json.dumps({'output': str(out), 'acceptance': False}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'evidence index refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
