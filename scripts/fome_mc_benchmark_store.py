#!/usr/bin/env python3
"""Place and verify the private, Git-backed fome-mc seeded-benchmark store.

The AGENTEE-108 expectations and run-1 reports carry fome-mc design structure
(sheet files, designators, pin-to-net maps, wire geometry, file inventories).
fome-mc has no licence file and publication is the AGENTEE-118 owner decision,
so the files stay out of this repository. They were first committed on the
unpublished local branch impl/ee108-fome-benchmark (expectations in f5dbaba,
before any run; reports in 0bafbd3).

The store must be a Git checkout of its own: `run` proves preregistration by
Git ancestry (`benchmark_run.committed_manifest`) and refuses a manifest that no
Git checkout tracks. `place` builds the store from a local clone holding the
source commit: a shallow, blob-filtered fetch down to the preregistration
commit, with a sparse checkout of only the three files. That clone is the
store's promisor remote and only those three blobs are copied. `verify` checks
the store is its own Git top level and clean for these paths, the SHA-256 of each
file, and that the expectations pass `committed_manifest` at the
preregistration commit.

    AGENT_EE_FOME_MC_BENCHMARK   store directory (default build/fome-mc-benchmark, ignored)

    PYTHONPATH=src python3 scripts/fome_mc_benchmark_store.py place [--source CLONE]
    PYTHONPATH=src python3 scripts/fome_mc_benchmark_store.py verify

This is a manual opt-in step. Exit 0 when verified, 2 when refused.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_ee.benchmark_run import committed_manifest  # noqa: E402
from agent_ee.storage import digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = 'benchmarks/seeded/fome-mc-expectations.json'
FILES = {
    EXPECTATIONS: '1a3403a943cf104e708e76942e8e4d0daf58f519633555c75475e24e13de1f6a',
    'benchmarks/seeded/fome-mc-report-run1.json': '1320d15029e3f0e9ffc2de412f3750fe54e935f5d05e6bfd7325b52d963ae907',
    'benchmarks/seeded/fome-mc-report-run1-corrected.json': '56c35d9e9b7107329b11502e1741101b20ad42313f14be524b249c6a0b7f848d',
}
SOURCE_COMMIT = '0bafbd30e4d9d461ee964ed26083f8f2df442f19'
PREREGISTRATION = 'f5dbabac9c81bbee1fce6df0188475c555c2dd94'
BRANCH = 'fome-mc-benchmark'
# The source clone serves filtered and by-identity requests for this fetch only.
UPLOAD_PACK = 'git -c uploadpack.allowFilter=true -c uploadpack.allowAnySHA1InWant=true upload-pack'


def store_directory():
    return Path(os.environ.get('AGENT_EE_FOME_MC_BENCHMARK', ROOT / 'build/fome-mc-benchmark'))


def git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True, check=True).stdout.strip()


def require_private(path):
    """Refuse a store path that an enclosing Git work tree would track."""
    path = Path(path).absolute()
    existing = next(p for p in (path, *path.parents) if p.exists())
    if not existing.is_dir():
        raise ValueError(f'private store is under a non-directory: {existing}')
    inside = subprocess.run(['git', '-C', str(existing), 'rev-parse', '--is-inside-work-tree'],
                            capture_output=True, text=True)
    if inside.returncode or inside.stdout.strip() != 'true':
        return
    # The trailing slash names a directory, so directory-only patterns apply before it exists.
    if subprocess.run(['git', '-C', str(existing), 'check-ignore', '-q', str(path) + '/'],
                      capture_output=True).returncode:
        raise ValueError(f'private store would be tracked by a Git work tree: {path}')


def place(store, source, commit=SOURCE_COMMIT, preregistration=PREREGISTRATION, files=FILES):
    """Create the store from a local clone that holds `commit`; never writes to the clone."""
    store = Path(store).absolute()
    if store.exists() or store.is_symlink():
        raise FileExistsError(str(store))
    require_private(store)
    source = Path(source).resolve()
    git(source, 'cat-file', '-e', commit + '^{commit}')
    git(source, 'merge-base', '--is-ancestor', preregistration, commit)
    depth = 1 + int(git(source, 'rev-list', '--count', '--ancestry-path', f'{preregistration}..{commit}'))
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(['git', 'init', '-q', str(store)], check=True, capture_output=True)
        for key, value in (('remote.source.url', str(source)), ('remote.source.promisor', 'true'),
                           ('remote.source.partialclonefilter', 'blob:none'),
                           ('remote.source.uploadpack', UPLOAD_PACK)):
            git(store, 'config', key, value)
        git(store, 'fetch', '-q', '--no-tags', f'--depth={depth}', '--filter=blob:none', 'source', commit)
        git(store, 'sparse-checkout', 'set', '--no-cone', *sorted(files))
        git(store, 'switch', '-q', '-c', BRANCH, commit)
    except BaseException:
        shutil.rmtree(store, ignore_errors=True)
        raise
    return verify(store, preregistration, files)


def verify(store, preregistration=PREREGISTRATION, files=FILES):
    store = Path(store).resolve()
    if not store.is_dir():
        raise FileNotFoundError(f'private store missing: {store}')
    top = subprocess.run(['git', '-C', str(store), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode or Path(top.stdout.strip()).resolve() != store:
        raise ValueError(f'private store is not its own Git checkout: {store}')
    if git(store, 'status', '--porcelain', '--', *sorted(files)):
        raise ValueError('private store has uncommitted changes to benchmark files')
    for rel, expected in sorted(files.items()):
        if digest(store / rel) != expected:
            raise ValueError(f'private store hash mismatch: {rel}')
    manifest = committed_manifest(store / EXPECTATIONS, preregistration)
    return dict(schema='agent-ee.fome-mc-benchmark-store/1', status='verified', store=str(store),
                head=git(store, 'rev-parse', 'HEAD'), preregistration=preregistration,
                files=dict(sorted(files.items())), cases=len(manifest['cases']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    placing = sub.add_parser('place')
    placing.add_argument('--source', default=str(ROOT), help='local clone holding the source commit (read only)')
    placing.add_argument('--commit', default=SOURCE_COMMIT)
    sub.add_parser('verify')
    args = parser.parse_args(argv)
    try:
        if args.command == 'place':
            result = place(store_directory(), args.source, args.commit)
        else:
            result = verify(store_directory())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, 'stderr', None) or ''
        print(json.dumps(dict(schema='agent-ee.fome-mc-benchmark-store/1', status='refused',
                              error=(str(exc) + (': ' + detail.strip() if detail else '')))))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
