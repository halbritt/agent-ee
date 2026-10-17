#!/usr/bin/python3
"""Record actual verify exits/stdout/stderr for retained runs; never repair them."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def diagnostics(run):
    """Report independently observable mismatches masked by verify's first refusal."""
    candidate = json.loads((run / 'candidate.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    policy = {p.name: checksum(p) for p in (ROOT / 'src/agent_ee').glob('*.py')}
    recorded_policy = result.get('policy_files', {})
    sources = candidate.get('sources', {})
    missing, changed = 0, 0
    for name, source in sources.items():
        path = Path(source)
        if not path.is_file():
            missing += 1
        elif checksum(path) != candidate['files'][name]:
            changed += 1
    metadata = {}
    for kind in ('scope', 'profile'):
        if candidate.get(kind + '_path'):
            path = Path(candidate[kind + '_path'])
            metadata[kind] = ('missing' if not path.is_file() else
                              'matches' if checksum(path) == candidate[kind + '_sha256'] else 'changed')
    return {'policy_matches': recorded_policy == policy,
            'policy_differences': sorted(name for name in policy.keys() | recorded_policy.keys()
                                         if policy.get(name) != recorded_policy.get(name)),
            'source_files': len(sources), 'source_missing': missing, 'source_changed': changed,
            'source_metadata': metadata,
            'note': 'Independent diagnostics; CLI exits below retain the actual first refusal.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=ROOT / 'evidence')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    environment = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    records = []
    for path in sorted(args.evidence.rglob('result.json')):
        if json.loads(path.read_text()).get('schema') != 'agent-ee.result/1':
            continue
        run = path.parent
        modes = {}
        for mode, flags in (('source', []), ('snapshot', ['--snapshot-only'])):
            command = [sys.executable, '-m', 'agent_ee.cli', 'verify', str(run), *flags]
            observed = subprocess.run(command, env=environment, capture_output=True, text=True,
                                      timeout=120, check=False)
            modes[mode] = {'command': command, 'exit': observed.returncode,
                           'stdout': observed.stdout, 'stderr': observed.stderr}
        records.append({'run': run.relative_to(args.evidence).as_posix(), 'verify': modes,
                        'diagnostics': diagnostics(run)})
    manifest = args.evidence / 'files-manifest.json'
    report = {'schema': 'agent-ee.evidence-verify-baseline/1',
              'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'implementation_ref': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'environment': {'python': sys.version, 'platform': platform.platform(),
                              'worktree': str(ROOT), 'evidence': str(args.evidence.resolve())},
              'files_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None,
              'runs': records}
    with args.out.open('x') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(f'Recorded {len(records)} runs; verify failures are retained, not repaired.')


if __name__ == '__main__':
    main()
