"""Content-addressed Python tool snapshots, separate from host import caches."""

import argparse
import fcntl
import math
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .stage_receipts import file_tree, tree_identity
from .storage import digest, identity


def _files(source):
    def scan_error(error):
        raise error

    files = {}
    for current, dirs, names in os.walk(source, onerror=scan_error):
        dirs[:] = sorted(name for name in dirs if name != '__pycache__')
        for name in [*dirs, *sorted(names)]:
            path = Path(current) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f'tool source contains a link or special file: {path}')
            if stat.S_ISREG(mode):
                files[path.relative_to(source).as_posix()] = digest(path)
    return files


def _private_directory(path):
    if path.is_symlink():
        raise ValueError(f'tool source cache directory is a link: {path}')
    if not path.exists():
        _private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f'tool source cache path is not a directory: {path}')
    path.chmod(0o700, follow_symlinks=False)


def _cache_root():
    value = os.environ.get('AGENT_EE_TOOL_SOURCE_CACHE')
    cache = Path(value) if value else Path.home() / '.cache/agent-ee/tool-source'
    if not cache.is_absolute():
        raise ValueError('AGENT_EE_TOOL_SOURCE_CACHE must be absolute')
    return cache


@contextmanager
def _locked_cache():
    cache = _cache_root()
    # Tighten the owned cache, never chmod existing ancestors such as HOME.
    if not cache.exists():
        missing = []
        parent = cache
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for path in reversed(missing):
            _private_directory(path)
    _private_directory(cache)
    descriptor = os.open(cache / '.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield cache


def _remove_tree(path):
    # A crash can occur between mkdir and chmod under a restrictive umask.
    path.chmod(0o700, follow_symlinks=False)
    for current, dirs, _ in os.walk(path):
        for name in dirs:
            directory = Path(current) / name
            if not directory.is_symlink():
                directory.chmod(0o700, follow_symlinks=False)
    shutil.rmtree(path)


def _clean_captures(cache):
    removed = []
    for path in sorted(cache.glob('.capture-*')):
        # All publishers hold the same lock for the full capture. Any capture
        # visible under the lock is abandoned. Never follow a planted link.
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f'unsafe abandoned tool source capture: {path}')
        _remove_tree(path)
        removed.append(path)
    return removed


def prune(*, older_than_days=30):
    """Remove old snapshots only while their campaigns are quiescent (operator gate)."""
    if not math.isfinite(older_than_days) or older_than_days < 0:
        raise ValueError('snapshot age must be finite and nonnegative')
    cutoff = time.time() - older_than_days * 86400
    with _locked_cache() as cache:
        removed = _clean_captures(cache)
        for path in sorted(cache.iterdir()):
            if not re.fullmatch(r'[0-9a-f]{64}', path.name):
                continue
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f'unsafe tool source snapshot: {path}')
            if path.stat().st_mtime < cutoff:
                _remove_tree(path)
                removed.append(path)
    return removed


def snapshot(source):
    """Capture all regular source bytes except __pycache__; verify before reuse.

    The stable cache root and byte identity determine the mount source path. No
    receipt observation filters files: additions to the snapshot still refuse.
    """
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('tool source must be a directory')
    files = _files(source)
    if not files:
        raise ValueError('tool source is empty')
    expected = {'sha256': identity(files), 'files': len(files)}
    with _locked_cache() as cache:
        _clean_captures(cache)
        target = cache / expected['sha256']
        if target.exists() or target.is_symlink():
            if (target.is_symlink() or not target.is_dir() or file_tree(target)[1]
                    or tree_identity(target) != expected):
                raise ValueError(f'tool source snapshot changed: {target}')
        else:
            with tempfile.TemporaryDirectory(prefix='.capture-', dir=cache) as temporary:
                Path(temporary).chmod(0o700, follow_symlinks=False)
                captured = Path(temporary) / 'src'
                captured.mkdir(mode=0o700)
                captured.chmod(0o700, follow_symlinks=False)
                for name in files:
                    destination = captured / name
                    _private_directory(destination.parent)
                    shutil.copyfile(source / name, destination, follow_symlinks=False)
                    if destination.is_symlink():
                        raise ValueError('tool source changed during snapshot')
                    destination.chmod(0o400, follow_symlinks=False)
                if tree_identity(captured) != expected or _files(source) != files:
                    raise ValueError('tool source changed during snapshot')
                captured.rename(target)
        for current, dirs, names in os.walk(target):
            Path(current).chmod(0o700, follow_symlinks=False)
            for name in names:
                (Path(current) / name).chmod(0o400, follow_symlinks=False)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pruning = commands.add_parser('prune', help='remove old snapshots after all dependent campaigns finish')
    pruning.add_argument('--older-than-days', type=float, default=30)
    args = parser.parse_args(argv)
    for path in prune(older_than_days=args.older_than_days):
        print(path)


if __name__ == '__main__':
    main()
