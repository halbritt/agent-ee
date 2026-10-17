"""Process-wide single-writer leases for review and campaign records."""

import fcntl
import os
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

_local = threading.local()


@contextmanager
def writer_lock(*directories):
    # A thread may re-enter its own campaign lease. Other threads/processes
    # open separate descriptors and still contend on the kernel lock.
    if getattr(_local, "pid", None) != os.getpid():
        _local.pid, _local.held = os.getpid(), set()
    paths = {Path(p).resolve() for p in directories}
    for path in list(paths):
        paths.update(p for p in path.parents if (p / ".campaign-root").is_file())
    with ExitStack() as stack:
        for path in sorted(paths):
            if path in _local.held:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # Never unlink: waiters may still hold the inode after release.
            stream = stack.enter_context((path.parent / f".{path.name}.writer.lock").open("a"))
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f"another writer owns {path}") from exc
            _local.held.add(path)
            stack.callback(_local.held.remove, path)
        yield
