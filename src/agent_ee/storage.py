"""Atomic records and explicit content identity."""

import contextlib
import contextvars
import hashlib
import json
import os
import secrets
from pathlib import Path


# No digest is reused across verification calls: file status cannot prove unchanged bytes
# (a shared writable mapping changes them silently), and with sudo no file is immutable to
# this user's processes (AGENTEE-250).
_scope = contextvars.ContextVar("agent_ee_digest_scope", default=None)


def _status(info):
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_mode


def _hash_stream(stream, checkpoint):
    h = hashlib.sha256()
    while True:
        if checkpoint is not None:
            checkpoint()
        block = stream.read(1024 * 1024)
        if not block:
            return h.hexdigest()
        h.update(block)


@contextlib.contextmanager
def digest_scope():
    """Hash each file at most once within one verification call (AGENTEE-250).

    A verification call establishes that each file had its recorded bytes at some instant
    during the call; it never pins bytes for later readers. One hash per file inside the
    call establishes exactly that, so a file (device, inode and status) already hashed in
    the active scope reuses that observation. A nested scope joins the enclosing one, the
    outermost exit discards every entry, and executor.execute() discards them on entry
    and exit so no observation spans a tool run. No reuse crosses verification calls.
    """
    if _scope.get() is not None:
        yield
        return
    token = _scope.set({})
    try:
        yield
    finally:
        _scope.reset(token)


def forget_scope():
    """Discard the active verification scope's observations, if any."""
    observed = _scope.get()
    if observed is not None:
        observed.clear()


def _scoped_digest(stream, checkpoint):
    before = os.fstat(stream.fileno())
    key = (before.st_dev, before.st_ino)
    observed = _scope.get()
    if observed is not None and key in observed and observed[key][0] == _status(before):
        if checkpoint is not None:
            checkpoint()
        return observed[key][1]
    value = _hash_stream(stream, checkpoint)
    if observed is not None:
        observed[key] = (_status(before), value)
    return value


def digest(path, *, checkpoint=None, follow_symlinks=True):
    """SHA-256 of the file bytes; checkpoint, when given, runs before each block.

    follow_symlinks=False refuses a link at path with OSError(ELOOP)."""
    opener = None if follow_symlinks else (lambda name, flags: os.open(name, flags | os.O_NOFOLLOW))
    with open(path, "rb", opener=opener) as stream:
        return _scoped_digest(stream, checkpoint)


def digest_fd(descriptor):
    """SHA-256 of an already opened regular file; consumes descriptor."""
    with os.fdopen(descriptor, "rb") as stream:
        return _scoped_digest(stream, None)


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
