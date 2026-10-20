"""Content-addressed Python tool snapshots, separate from host import caches."""

import fcntl
import os
import shutil
import stat
import tempfile
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
    cache = Path(os.environ.get('AGENT_EE_TOOL_SOURCE_CACHE',
                                Path.home() / '.cache/agent-ee/tool-source')).absolute()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / expected['sha256']
    with (cache / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists() or target.is_symlink():
            if (target.is_symlink() or not target.is_dir() or file_tree(target)[1]
                    or tree_identity(target) != expected):
                raise ValueError(f'tool source snapshot changed: {target}')
        else:
            with tempfile.TemporaryDirectory(prefix='.capture-', dir=cache) as temporary:
                captured = Path(temporary) / 'src'
                captured.mkdir()
                for name in files:
                    destination = captured / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / name, destination, follow_symlinks=False)
                if tree_identity(captured) != expected or _files(source) != files:
                    raise ValueError('tool source changed during snapshot')
                captured.rename(target)
    return target
