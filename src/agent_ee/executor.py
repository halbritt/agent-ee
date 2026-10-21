"""Run tools with a read-only candidate and one writable scratch directory."""

import contextlib
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import tempfile
import time
from threading import Event, Lock, current_thread, main_thread
from pathlib import Path

from .storage import forget_scope, json_bytes, publish

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Written by the executor into the tool's writable scratch after the tool exits.
RECORDS = ("stdout.log", "stderr.log", "console.log", "execution.json")
# Tools resolve on this fixed PATH inside the sandbox; /usr is the host's, read-only.
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"
# Some bwrap versions canonicalize even fd sources to pathnames. Verify the
# mounted objects before exec; only this prelude can emit the first output line.
MOUNT_GUARD_OK = b'agent-ee: mount identities verified\n'
MOUNT_GUARD = '''import ctypes, json, os, signal, sys
for path, device, inode in json.loads(sys.argv[1]):
    observed = os.stat(path, follow_symlinks=False)
    if (observed.st_dev, observed.st_ino) != (device, inode):
        print('mount_source_changed:' + path, file=sys.stderr)
        raise SystemExit(125)
os.write(1, b'agent-ee: mount identities verified\\n')
for name in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ'):
    number = getattr(signal, name, None)
    if number is not None:
        signal.signal(number, signal.SIG_DFL)
arguments = [os.fsencode(value) for value in sys.argv[3:]]
native_argv = (ctypes.c_char_p * (len(arguments) + 1))(*arguments, None)
launch = ctypes.CDLL(None, use_errno=True).execvp
launch.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]
launch.restype = ctypes.c_int
launch(arguments[0], native_argv)
error = ctypes.get_errno()
raise OSError(error, os.strerror(error), sys.argv[3])
'''
MOUNT_GUARD_PREFIX = ['/usr/bin/python3', '-I', '-B', '-c', MOUNT_GUARD]


def sandbox_argv(inputs, work, argv, *, network=False, extra_ro=(), mount_identities=None):
    # A small filesystem view also hides other reviewers' scratch and the source
    # checkout. No host home, /run sockets or host /tmp are exposed.
    command = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid",
               "--unshare-ipc", "--unshare-uts", "--ro-bind", "/usr", "/usr"]
    for path in ("/lib", "/lib64", "/bin", "/sbin"):
        if Path(path).is_symlink():
            command += ["--symlink", os.readlink(path), path]
        elif Path(path).exists():
            command += ["--ro-bind", path, path]
    command += ["--ro-bind", "/etc", "/etc", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--dir", "/home", "--dir", "/run",
                "--ro-bind", str(Path(inputs).resolve()), "/candidate",
                "--ro-bind", str(PACKAGE_ROOT), "/app",
                "--bind", str(Path(work).resolve()), "/work", "--chdir", "/work",
                "--clearenv", "--setenv", "PATH", SANDBOX_PATH,
                "--setenv", "HOME", "/work/home", "--setenv", "LANG", "C.UTF-8",
                "--setenv", "PYTHONPATH", "/app",
                "--setenv", "KICAD_CONFIG_HOME", "/work/kicad-config",
                "--setenv", "KICAD10_SYMBOL_DIR", "/candidate/stock/symbols",
                "--setenv", "KICAD10_FOOTPRINT_DIR", "/candidate/stock/footprints",
                "--setenv", "KICAD10_TEMPLATE_DIR", "/candidate/stock/template"]
    if not network:
        command += ["--unshare-net"]
    # /etc/resolv.conf can point into /run, which is deliberately hidden.
    if network and Path("/etc/resolv.conf").exists():
        resolver = str(Path("/etc/resolv.conf").resolve())
        command += ["--dir", str(Path(resolver).parent), "--ro-bind", resolver, resolver]
    bindings = []
    for source, dest in extra_ro:
        if isinstance(source, int):
            command += ["--ro-bind-fd", str(source), dest]
            if mount_identities is None:
                observed = os.fstat(source)
                expected = observed.st_dev, observed.st_ino
            else:
                expected = mount_identities[dest]
            bindings.append([dest, *expected])
        else:
            command += ["--ro-bind", str(Path(source).resolve()), dest]
    if bindings:
        argv = [*MOUNT_GUARD_PREFIX, json.dumps(bindings, separators=(',', ':')), '--', *argv]
    return command + ["--", *map(str, argv)]


def sandbox_invocation(command):
    """Return declared argv and fd identities from the exact retained prelude."""
    if not isinstance(command, list) or '--' not in command:
        raise ValueError('malformed sandbox argv')
    end = command.index('--')
    options, argv = command[:end], command[end + 1:]
    if argv[:5] != MOUNT_GUARD_PREFIX:
        if '--ro-bind-fd' in options:
            raise ValueError('fd mounts lack the exact sandbox identity guard')
        return argv, None
    if len(argv) < 8 or argv[6] != '--':
        raise ValueError('fd mounts lack the exact sandbox identity guard')
    try:
        bindings = json.loads(argv[5])
    except (TypeError, ValueError) as exc:
        raise ValueError('malformed sandbox mount identities') from exc
    if (not isinstance(bindings, list) or not bindings or
            any(not isinstance(item, list) or len(item) != 3 or not isinstance(item[0], str) or
                any(type(number) is not int or number < 0 for number in item[1:])
                for item in bindings) or
            len({item[0] for item in bindings}) != len(bindings)):
        raise ValueError('malformed sandbox mount identities')
    return argv[7:], {path: (device, inode) for path, device, inode in bindings}


class Cancellation:
    """One operation's launch gate and cooperative subprocess cancellation."""

    def __init__(self):
        self.cancelled = Event()
        self.launch_lock = Lock()

    def cancel(self):
        # Do not wait behind a worker inside Popen. A launch already admitted
        # there observes this flag immediately after Popen returns.
        self.cancelled.set()


def _open_work(work):
    """Create work if needed; return its resolved path and an open descriptor for it.

    The caller owns work's parent, which is resolved like any coordinator path. work
    itself may sit in an earlier tool's scratch, so a link there is refused, never followed.
    """
    work = Path(os.path.abspath(work))
    if not work.name:
        raise ValueError("executor work directory must not be the filesystem root")
    parent = work.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    above = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            os.mkdir(work.name, dir_fd=above)
        except FileExistsError:
            pass
        directory = os.open(work.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=above)
    finally:
        os.close(above)
    try:
        try:
            os.mkdir("home", dir_fd=directory)
        except FileExistsError:
            if not stat.S_ISDIR(os.stat("home", dir_fd=directory, follow_symlinks=False).st_mode):
                raise
    except BaseException:
        os.close(directory)
        raise
    return parent / work.name, directory


def _entry(directory, name):
    try:
        st = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _kind(mode):
    for test, kind in ((stat.S_ISLNK, "link"), (stat.S_ISDIR, "directory"), (stat.S_ISREG, "file"),
                       (stat.S_ISFIFO, "fifo"), (stat.S_ISSOCK, "socket")):
        if test(mode):
            return kind
    return "special"


def _displace(directory, before):
    """Move each record entry the tool created or changed aside, unread and unfollowed.

    The entry itself, a link included, is kept as the tool left it so the
    interference stays visible; only the executor's own bytes take the record name.
    """
    displaced = {}
    for name in RECORDS:
        entry = _entry(directory, name)
        if entry is None or entry == before[name]:
            continue
        while True:
            aside = f"{name}.displaced-{secrets.token_hex(8)}"
            if _entry(directory, aside) is None:
                break
        os.rename(name, aside, src_dir_fd=directory, dst_dir_fd=directory)
        displaced[name] = {"kind": _kind(entry[2]), "moved_to": aside}
    return displaced


def execute(inputs, work, argv, *, timeout=120, stdin=None, network=False, extra_ro=(), cancellation=None):
    # A tool may change files in place; no digest observation spans its run (AGENTEE-250).
    forget_scope()
    work, directory = _open_work(work)
    try:
        return _execute(inputs, work, directory, argv, timeout=timeout, stdin=stdin, network=network,
                        extra_ro=extra_ro, cancellation=cancellation)
    finally:
        os.close(directory)
        forget_scope()


@contextlib.contextmanager
def held_interrupt():
    """Hold an operator SIGINT while a sandbox is launched.

    bwrap is running (and visible in /proc) before Popen returns its handle; a
    KeyboardInterrupt raised in between loses the only handle that can kill and
    reap it. The yielded list is non-empty when an interrupt was held; the caller
    acts on it once the process handle exists."""
    held = []
    if current_thread() is not main_thread() or signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        yield held
        return
    previous = signal.signal(signal.SIGINT, lambda signum, frame: held.append(signum))
    try:
        yield held
    finally:
        signal.signal(signal.SIGINT, previous)


def _execute(inputs, work, directory, argv, *, timeout, stdin, network, extra_ro, cancellation):
    extra_ro = tuple(extra_ro)
    command = sandbox_argv(inputs, work, argv, network=network, extra_ro=extra_ro)
    record = {"argv": list(map(str, argv)), "sandbox_argv": command,
              "timeout_s": timeout, "execution": "not_run", "exit_code": None}
    # Records an earlier run left here are replaced; anything else at these names is the tool's.
    before = {name: _entry(directory, name) for name in RECORDS}
    start = time.monotonic()
    output, stderr = b"", b""
    try:
        cancellation = cancellation or Cancellation()
        # A private, unlinked regular file supplies complete input independently
        # of communicate()'s short output/cancellation polling intervals. Retrying
        # communicate(input=None) can abandon pending pipe writes after a timeout.
        with tempfile.TemporaryFile() as input_stream:
            if stdin is not None:
                input_stream.write(stdin.encode())
            input_stream.seek(0)
            with held_interrupt() as interrupt, cancellation.launch_lock:
                if cancellation.cancelled.is_set():
                    proc = None
                else:
                    proc = subprocess.Popen(command, stdin=input_stream, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, start_new_session=True,
                                            pass_fds=tuple(source for source, _ in extra_ro
                                                           if isinstance(source, int)))
        if proc is None and interrupt:
            raise KeyboardInterrupt
        if proc is not None:
            try:
                try:
                    while True:
                        if cancellation.cancelled.is_set() or interrupt:
                            raise KeyboardInterrupt
                        remaining = timeout - (time.monotonic() - start)
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, timeout)
                        try:
                            output, stderr = proc.communicate(timeout=min(.05, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    record["exit_code"] = proc.returncode
                    record["execution"] = "ok" if proc.returncode == 0 else "failed"
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    record["execution"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                    try:
                        output, stderr = proc.communicate(timeout=1)
                    except subprocess.TimeoutExpired as cleanup:
                        output, stderr = cleanup.output or b"", cleanup.stderr or b""
                        record["cleanup_error"] = "process group killed but output drain exceeded one second"
            finally:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
    except FileNotFoundError as exc:
        record["execution"] = "unavailable"
        output = str(exc).encode()
    if any(isinstance(source, int) for source, _ in extra_ro):
        record['mount_guard'] = output.startswith(MOUNT_GUARD_OK)
        if record['mount_guard']:
            output = output[len(MOUNT_GUARD_OK):]
    record["duration_s"] = round(time.monotonic() - start, 3)
    console = output + stderr
    record["console_sha256"] = hashlib.sha256(console).hexdigest()
    record["console"] = "console.log"
    record["stdout_sha256"] = hashlib.sha256(output).hexdigest()
    record["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
    # The tool may have planted links, directories or files at the record names, or
    # hard links to its own outputs. Every write below goes to a new exclusive file
    # relative to the open work directory and is renamed into place.
    try:
        displaced = _displace(directory, before)
        if displaced:
            record["displaced_records"] = displaced
        for name, data in (("stdout.log", output), ("stderr.log", stderr), ("console.log", console)):
            publish(directory, name, data, sync=False)
        publish(directory, "execution.json", json_bytes(record))
    except Exception as exc:
        # The process outcome is in record; an interrupt still propagates as one.
        if record["execution"] == "interrupted":
            raise KeyboardInterrupt from exc
        raise
    if record["execution"] == "interrupted":
        raise KeyboardInterrupt
    return record
