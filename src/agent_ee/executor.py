"""Run tools with a read-only candidate and one writable scratch directory."""

import os
import signal
import subprocess
import tempfile
import time
from threading import Event, Lock
from pathlib import Path

from .storage import digest, write_json

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
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


def execute(inputs, work, argv, *, timeout=120, stdin=None, network=False, extra_ro=(), cancellation=None):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / "home").mkdir(exist_ok=True)
    command = sandbox_argv(inputs, work, argv, network=network, extra_ro=extra_ro)
    record = {"argv": list(map(str, argv)), "sandbox_argv": command,
              "timeout_s": timeout, "execution": "not_run", "exit_code": None}
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
    log = work / "console.log"
    (work / "stdout.log").write_bytes(output)
    (work / "stderr.log").write_bytes(stderr)
    log.write_bytes(output + stderr)
    record["console_sha256"] = digest(log)
    record["console"] = "console.log"
    record["stdout_sha256"] = digest(work / "stdout.log")
    record["stderr_sha256"] = digest(work / "stderr.log")
    write_json(work / "execution.json", record)
    if record["execution"] == "interrupted":
        raise KeyboardInterrupt
    return record
