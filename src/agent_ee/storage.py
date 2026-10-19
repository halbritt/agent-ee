"""Atomic records and explicit content identity."""

import contextlib
import contextvars
import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path


# A file whose status changed this recently may still change within one timestamp tick
# without a visible status change, so its digest is never reused (as Git treats racy entries).
RACY_NS = 2_000_000_000
# Filesystems whose reported owner and mode are enforced by the kernel for this host.
# FUSE and network filesystems may report root ownership for bytes a user process serves.
LOCAL_FILESYSTEMS = frozenset(("ext2", "ext3", "ext4", "xfs", "btrfs", "squashfs", "zfs", "f2fs"))
_memo = None
_scope = contextvars.ContextVar("agent_ee_digest_scope", default=None)


@contextlib.contextmanager
def digest_memo():
    """Reuse within one command run only the digests of files no agent process can write.

    Status metadata cannot prove unchanged bytes for a file this uid can write: a shared
    writable mapping changes the bytes without changing size, mtime or ctime. A digest is
    therefore reused only for a regular file that immutable_to_agents() accepts (owned by
    root, no group or other write bit, no access ACL, every ancestor directory alike, on a
    kernel-enforced local filesystem, while this process runs unprivileged in the initial
    user namespace), and only while its status equals the status seen before and after it
    was hashed, older than RACY_NS. Every other file is hashed on every call. Nothing
    outlives the outermost block (AGENTEE-250).
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


def _unprivileged_initial_namespace():
    """True when this process holds no capability and runs in the initial user namespace.

    Inside another user namespace a file shown as root-owned may belong to this user."""
    if os.getuid() == 0 or os.geteuid() == 0:
        return False
    try:
        with open("/proc/self/uid_map") as stream:
            if stream.read().split() != ["0", "0", "4294967295"]:
                return False
        with open("/proc/self/status") as stream:
            capabilities = [line.split()[1] for line in stream if line.startswith("CapEff:")]
    except OSError:
        return False
    return capabilities == ["0" * 16]


def _filesystem(device):
    """Type of the mount whose device number is device, or None when not found once."""
    found = set()
    try:
        with open("/proc/self/mountinfo") as stream:
            for line in stream:
                fields, _, rest = line.partition(" - ")
                major, _, minor = fields.split()[2].partition(":")
                if os.makedev(int(major), int(minor)) == device:
                    found.add(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return found.pop() if len(found) == 1 else None


def _sealed(info):
    return info.st_uid == 0 and not info.st_mode & 0o022


def _has_access_acl(target, *, follow_symlinks=True):
    try:
        os.getxattr(target, "system.posix_acl_access", follow_symlinks=follow_symlinks)
    except OSError as error:
        import errno
        if error.errno in (errno.ENODATA, errno.ENOTSUP):
            return False
        return True
    return True


def immutable_to_agents(descriptor):
    """Whether no process of this uid, its sandboxes or agents can change this open file's bytes.

    The opened inode must be a root-owned regular file without group or other write
    permission and without an access ACL, reached through root-owned directories that are
    neither group nor other writable, on a local filesystem from LOCAL_FILESYSTEMS, while
    this process is unprivileged in the initial user namespace. Any unknown answers False.
    Processes that obtain root are outside this guarantee.
    """
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not _sealed(info):
        return False
    if not _unprivileged_initial_namespace() or _has_access_acl(descriptor):
        return False
    if _filesystem(info.st_dev) not in LOCAL_FILESYSTEMS:
        return False
    try:
        location = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError:
        return False
    if not location.startswith("/") or location.endswith(" (deleted)"):
        return False
    parent = os.path.dirname(location)
    while True:
        try:
            ancestor = os.lstat(parent)
        except OSError:
            return False
        if not stat.S_ISDIR(ancestor.st_mode) or not _sealed(ancestor) or \
                _has_access_acl(parent, follow_symlinks=False):
            return False
        if parent == "/":
            break
        parent = os.path.dirname(parent)
    # The descriptor must still name the inode whose ancestors were checked.
    try:
        current = os.stat(location, follow_symlinks=False)
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)


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


def _memo_digest(stream, checkpoint):
    before = os.fstat(stream.fileno())
    key = (before.st_dev, before.st_ino)
    observed = _scope.get()
    if observed is not None and key in observed and observed[key][0] == _status(before):
        if checkpoint is not None:
            checkpoint()
        return observed[key][1]
    memo = _memo
    eligible = memo is not None and immutable_to_agents(stream.fileno())
    cached = memo.get(key) if eligible else None
    if cached is not None and cached[0] == _status(before):
        value = cached[1]
        if checkpoint is not None:
            checkpoint()
    else:
        started = time.time_ns()
        value = _hash_stream(stream, checkpoint)
        after = os.fstat(stream.fileno())
        if (eligible and _status(after) == _status(before) and
                max(before.st_mtime_ns, before.st_ctime_ns) < started - RACY_NS):
            memo[key] = (_status(before), value)
        elif memo is not None:
            memo.pop(key, None)
    if observed is not None:
        observed[key] = (_status(before), value)
    return value


def digest(path, *, checkpoint=None, follow_symlinks=True):
    """SHA-256 of the file bytes; checkpoint, when given, runs before each block.

    follow_symlinks=False refuses a link at path with OSError(ELOOP)."""
    opener = None if follow_symlinks else (lambda name, flags: os.open(name, flags | os.O_NOFOLLOW))
    with open(path, "rb", opener=opener) as stream:
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
