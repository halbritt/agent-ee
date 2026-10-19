"""Run tools with a read-only candidate and one writable scratch directory."""

import hashlib
import os
import secrets
import signal
import stat
import subprocess
import tempfile
import time
from threading import Event, Lock
from pathlib import Path

from .storage import forget_scope, json_bytes, publish

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Written by the executor into the tool's writable scratch after the tool exits.
RECORDS = ("stdout.log", "stderr.log", "console.log", "execution.json")
# Tools resolve on this fixed PATH inside the sandbox; /usr is the host's, read-only.
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"


def sandbox_argv(inputs, work, argv, *, network=False, extra_ro=()):
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
    for source, dest in extra_ro:
        command += ["--ro-bind", str(Path(source).resolve()), dest]
    return command + ["--", *map(str, argv)]


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


def _execute(inputs, work, directory, argv, *, timeout, stdin, network, extra_ro, cancellation):
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
            with cancellation.launch_lock:
                if cancellation.cancelled.is_set():
                    proc = None
                else:
                    proc = subprocess.Popen(command, stdin=input_stream, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, start_new_session=True)
        if proc is not None:
            try:
                try:
                    while True:
                        if cancellation.cancelled.is_set():
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
