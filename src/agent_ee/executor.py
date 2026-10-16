"""Run tools with a read-only candidate and one writable scratch directory."""

import os
import signal
import subprocess
import time
from pathlib import Path

from .storage import digest, write_json

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


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
                "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
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


def execute(inputs, work, argv, *, timeout=120, stdin=None, network=False, extra_ro=()):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / "home").mkdir(exist_ok=True)
    command = sandbox_argv(inputs, work, argv, network=network, extra_ro=extra_ro)
    record = {"argv": list(map(str, argv)), "sandbox_argv": command,
              "timeout_s": timeout, "execution": "not_run", "exit_code": None}
    start = time.monotonic()
    output, stderr = b"", b""
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as proc:
            try:
                output, stderr = proc.communicate(stdin.encode() if stdin is not None else None,
                                             timeout=max(0.001, timeout))
                record["exit_code"] = proc.returncode
                record["execution"] = "ok" if proc.returncode == 0 else "failed"
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                os.killpg(proc.pid, signal.SIGKILL)
                output, stderr = proc.communicate()
                record["execution"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
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
