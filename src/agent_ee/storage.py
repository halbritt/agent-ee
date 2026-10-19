"""Atomic records and explicit content identity."""

import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path


# A file whose status changed this recently may still change within one timestamp tick
# without a visible status change, so its digest is never reused (as Git treats racy entries).
RACY_NS = 2_000_000_000
_memo = None


@contextlib.contextmanager
def digest_memo():
    """Reuse file digests within one command run while the file status is unchanged.

    An entry is keyed by device and inode and reused only while size, mtime and ctime all
    equal the status observed before and after the bytes were hashed, and that status is
    older than RACY_NS at hashing time. Any write, truncation, rename-over or timestamp
    change alters ctime, so a file changed mid-run is hashed again and the changed digest
    fails the caller's comparison exactly as before. Nothing outlives the outermost block.
    """
    global _memo
    if _memo is not None:
        yield
        return
    _memo = {}
    try:
        yield
    finally:
        _memo = None


def _status(info):
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _hash_stream(stream, checkpoint):
    h = hashlib.sha256()
    while True:
        if checkpoint is not None:
            checkpoint()
        block = stream.read(1024 * 1024)
        if not block:
            return h.hexdigest()
        h.update(block)


def _memo_digest(stream, checkpoint):
    memo = _memo
    if memo is None:
        return _hash_stream(stream, checkpoint)
    before = os.fstat(stream.fileno())
    key = (before.st_dev, before.st_ino)
    cached = memo.get(key)
    if cached is not None and cached[0] == _status(before):
        if checkpoint is not None:
            checkpoint()
        return cached[1]
    started = time.time_ns()
    value = _hash_stream(stream, checkpoint)
    after = os.fstat(stream.fileno())
    if (_status(after) == _status(before) and
            max(before.st_mtime_ns, before.st_ctime_ns) < started - RACY_NS):
        memo[key] = (_status(before), value)
    else:
        memo.pop(key, None)
    return value


def digest(path, *, checkpoint=None):
    """SHA-256 of the file bytes; checkpoint, when given, runs before each block."""
    with Path(path).open("rb") as stream:
        return _memo_digest(stream, checkpoint)


def digest_fd(descriptor):
    """SHA-256 of an already opened regular file; consumes descriptor."""
    with os.fdopen(descriptor, "rb") as stream:
        return _memo_digest(stream, None)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def json_bytes(value):
    """The exact bytes written by write_json."""
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def publish(directory, name, data, *, sync=True):
    """Atomically replace name in the open directory with data.

    The temporary file is new and exclusive, so a link or file planted at any
    name in the directory is never opened or written through. Renaming over name
    replaces whatever entry is there rather than following it.
    """
    temp = f".{name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o666, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            if sync:
                os.fsync(stream.fileno())
        os.rename(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass
        raise
    if sync:
        os.fsync(directory)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(value)
    temp = path.with_name(path.name + ".tmp")
    # A stale or planted entry at the temporary name is removed, never followed,
    # and the file is created exclusively; replace() renames over a link at path.
    try:
        os.unlink(temp)
    except FileNotFoundError:
        pass
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o666)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def json_digest(value):
    """Identity of the exact bytes produced by write_json."""
    return hashlib.sha256(json_bytes(value)).hexdigest()


def rename_noreplace(source, destination):
    """Linux RENAME_NOREPLACE: publish a complete staged directory and refuse races."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))
