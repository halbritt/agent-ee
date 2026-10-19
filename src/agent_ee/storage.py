"""Atomic records and explicit content identity."""

import hashlib
import json
import os
import secrets
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


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
