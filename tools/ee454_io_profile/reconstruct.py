"""AGENTEE-454 profile harness: rebuild the Rev-3 complete-parts-manifest capture root.

Reads only pinned objects from the fome-mc Git object store (``git show``), never its
working tree, and writes a fresh directory. Every copied file is checked against the
sha256 that the fome-mc reconstruction.json records. Diagnostic tooling, not src/.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PACKET = 'telemetry-pod/expansion/complete-parts-manifest'


def show(store, commit, path):
    return subprocess.run(['git', '-C', str(store), 'show', f'{commit}:{path}'],
                          capture_output=True, check=True).stdout


def build(store, commit, out):
    out = Path(out)
    out.mkdir(parents=False, exist_ok=False)
    reconstruction = json.loads(show(store, commit, PACKET + '/reconstruction.json'))
    files = {}
    for rel, entry in sorted(reconstruction['copied'].items()):
        raw = show(store, commit, entry['source'])
        found = hashlib.sha256(raw).hexdigest()
        if found != entry['sha256']:
            raise SystemExit(f'{rel}: sha256 {found} differs from reconstruction {entry["sha256"]}')
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files[rel] = found
    # The two generated declarations are the packet's own files, not reconstruction members.
    for name in ('parts.json', 'assignments.json'):
        raw = show(store, commit, f'{PACKET}/{name}')
        (out / name).write_bytes(raw)
        files[name] = hashlib.sha256(raw).hexdigest()
    return dict(store_commit=commit, packet=PACKET, files=len(files),
                bytes=sum((out / rel).stat().st_size for rel in files),
                identity=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                sha256=files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--record', required=True)
    args = parser.parse_args()
    commit = subprocess.run(['git', '-C', args.store, 'rev-parse', args.commit + '^{commit}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    record = build(args.store, commit, args.out)
    Path(args.record).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: v for k, v in record.items() if k != 'sha256'}))


if __name__ == '__main__':
    main()
