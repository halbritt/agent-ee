"""One native run: a tool intent, mounts, a deadline and a cancel handle in; one execution record out.

Whether a child is sandboxed, deadline-clamped and sees the check baseline is decided here,
not by the caller (AGENTEE-540). Isolation is an adapter behind run():

  Sandbox   the bwrap view of docs/security.md, through executor.execute; its records are
            the executor's records, byte for byte.
  Host      a host process in its own process group, for the few runs that need the host
            (an outer coordinator, a container client). Same deadline and cancel rules.
  Docker    a pinned image without network, launched as a host process.
  Recorded  canned outcomes for tests; it writes the same record files and keeps every call.

execution.json keeps the executor's shape. profile() parses a record's bwrap argv once, so
readers ask for binds, network and the mount guard instead of re-tokenising the argv.
"""

import contextlib
import contextvars
import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import executor
from .executor import Cancellation, held_interrupt
from .storage import forget_scope, json_bytes, publish

# Checks see the run's baseline here when it exists (Ctx.execute's mount, now the module's).
BASELINE_AT = "/evidence/baseline.json"
# A run with this little of its deadline left is refused rather than launched.
LAUNCH_FLOOR_S = 1
ISOLATIONS = ("sandbox", "host", "docker")


class DeadlineExhausted(Exception):
    """The deadline left too little time to launch the tool."""


@dataclass(frozen=True)
class Intent:
    """What to run: a named tool, its argv and what it may see.

    tool names the native intent (drc, netlist, pcbnew_script, router, render3d, probe, …)
    for adapters and tests; it never changes the argv. timeout is the tool's own limit,
    which run() clamps to the caller's deadline; a host or docker intent may pass None when
    the tool has no limit of its own (the deadline, if any, still applies). env and cwd apply
    to host runs only; the sandbox clears its environment and starts in /work. stop_grace_s
    asks for SIGTERM to the group and that many seconds before SIGKILL when a host or docker
    run is stopped early (timeout, cancel, a failing callback); None kills at once.
    """
    tool: str
    argv: tuple
    timeout: Optional[float] = 120
    stdin: Optional[str] = None
    network: bool = False
    skip_visuals: Optional[bool] = None
    isolation: str = "sandbox"
    env: Optional[Mapping[str, str]] = None
    cwd: Optional[str] = None
    stop_grace_s: Optional[float] = None
    # Bounded sandbox runs (replay): paths remounted read-only inside the view, and a ceiling on
    # the combined stdout+stderr bytes kept; past it the run stops as output_limit.
    remount_ro: tuple = ()
    log_bytes: Optional[int] = None
    # Host/docker: stderr joins stdout as one stream in arrival order (a container console).
    merge_stderr: bool = False

    def __post_init__(self):
        if not self.tool or not isinstance(self.tool, str):
            raise ValueError("native intent needs a tool name")
        if isinstance(self.argv, (str, bytes)) or not self.argv:
            raise ValueError("native intent argv must be a non-empty sequence")
        object.__setattr__(self, "argv", tuple(map(str, self.argv)))
        if self.isolation not in ISOLATIONS:
            raise ValueError(f"unknown native isolation {self.isolation!r}")
        if self.isolation == "sandbox" and (self.env is not None or self.cwd is not None):
            raise ValueError("a sandboxed run takes no host environment or working directory")
        if self.timeout is None and self.isolation == "sandbox":
            raise ValueError("a sandboxed run needs a timeout")
        if self.stop_grace_s is not None and (self.isolation == "sandbox" or self.stop_grace_s < 0):
            raise ValueError("stop_grace_s is a non-negative grace for host or docker runs")
        object.__setattr__(self, "remount_ro", tuple(map(str, self.remount_ro)))
        if self.remount_ro and self.isolation != "sandbox":
            raise ValueError("only a sandboxed run has a view to remount")
        if self.log_bytes is not None and (type(self.log_bytes) is not int or self.log_bytes <= 0):
            raise ValueError("log_bytes must be a positive integer")
        if self.merge_stderr and self.isolation == "sandbox":
            raise ValueError("merge_stderr applies to host or docker runs")


class Execution(dict):
    """The execution record (exactly what execution.json holds) plus the captured output.

    stdout and stderr are attributes, not keys, so the record serializes unchanged. returncode is
    the reaped process status of a host or docker run, also after a timeout, cancel or failing
    callback, where the record's exit_code stays None as the executor's does (None for sandbox runs)."""

    def __init__(self, record, stdout=b"", stderr=b"", returncode=None):
        super().__init__(record)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# --- argv builders: each native command is spelled in one place ---------------------------------

def drc_argv(board, report, *, parity=False, refill=False, save_board=False, units=None,
             severity_all=True, output_flag="-o", executable="kicad-cli"):
    """kicad-cli pcb drc with a JSON report."""
    if output_flag not in ("-o", "--output"):
        raise ValueError("DRC output flag must be -o or --output")
    return [executable, "pcb", "drc", "--format", "json",
            *(["--severity-all"] if severity_all else []),
            *(["--schematic-parity"] if parity else []),
            *(["--refill-zones"] if refill else []),
            *(["--save-board"] if save_board else []),
            *(["--units", units] if units else []),
            output_flag, str(report), str(board)]


def netlist_argv(schematic, output, *, executable="kicad-cli"):
    """kicad-cli sch export netlist as KiCad XML."""
    return [executable, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(output), str(schematic)]


def python_module_argv(module, *args, no_bytecode=True, interpreter="/usr/bin/python3"):
    """A pcbnew (or other agent_ee) module run as python -m; the sandbox puts /app on PYTHONPATH."""
    return [interpreter, *(["-B"] if no_bytecode else []), "-m", module, *map(str, args)]


def drc(board, report, *, timeout, **options):
    return Intent("drc", drc_argv(board, report, **options), timeout=timeout)


def netlist(schematic, output, *, timeout, executable="kicad-cli"):
    return Intent("netlist", netlist_argv(schematic, output, executable=executable), timeout=timeout)


def pcbnew_script(module, *args, timeout, no_bytecode=True, **intent):
    return Intent("pcbnew_script", python_module_argv(module, *args, no_bytecode=no_bytecode),
                  timeout=timeout, **intent)


# --- the one entry point ---------------------------------------------------------------------

_active = contextvars.ContextVar("agent_ee_native_adapter", default=None)


def clamp(intent, deadline):
    """The timeout the tool gets: its own, cut to what the deadline (monotonic) leaves."""
    if deadline is None:
        return intent.timeout
    remaining = deadline - time.monotonic()
    if remaining <= LAUNCH_FLOOR_S:
        raise DeadlineExhausted(f"deadline exhausted before {intent.tool} could run")
    return remaining if intent.timeout is None else min(intent.timeout, remaining)


def cut_by_deadline(record, intent):
    """True when the tool timed out only because the deadline clamped its own limit."""
    if record.get("execution") != "timeout":
        return False
    return intent.timeout is None or record.get("timeout_s", intent.timeout) < intent.timeout


def baseline_mounts(baseline):
    """The baseline mount for a check run; nothing when the run has no baseline."""
    if baseline is None:
        return ()
    baseline = Path(baseline)
    return ((baseline, BASELINE_AT),) if baseline.is_file() else ()


def run(intent, *, work, inputs=None, deadline=None, mounts=(), baseline=None, cancel=None, adapter=None,
        on_line=None, on_launch=None, on_tick=None, sink=None):
    """Run intent and return its Execution.

    inputs is the read-only candidate (sandbox runs). deadline is an absolute monotonic time;
    the tool's timeout is clamped to it and a nearly spent deadline raises DeadlineExhausted.
    baseline names the run's baseline.json; when it exists it is mounted before mounts.
    cancel is an executor.Cancellation. on_line receives each stdout line, on_launch the
    child's pid once it starts, and on_tick is called on the caller's thread before every
    supervision poll (at most 0.05 s apart, also while a descendant still holds a pipe after
    the leader exits) for cooperative checkpoints (host and docker). When a callback raises, the run is stopped and
    reaped and the callback's exception propagates unchanged, carrying the stopped run's
    Execution as its native_execution attribute (execution "callback_failed"). An operator
    interrupt (SIGINT held during the launch) likewise re-raises KeyboardInterrupt carrying the
    stopped run's Execution (execution "interrupted") for host, docker and bounded sandbox runs.
    sink, a binary file (host and docker), receives stdout as it arrives; it is also kept.
    """
    if not isinstance(intent, Intent):
        raise TypeError("native run needs an Intent")
    timeout = clamp(intent, deadline)
    mounts = (*baseline_mounts(baseline), *mounts)
    chosen = adapter or _active.get() or DEFAULT_ADAPTERS[intent.isolation]
    return chosen.run(intent, inputs=inputs, work=work, timeout=timeout, mounts=mounts,
                      cancel=cancel, on_line=on_line, on_launch=on_launch, on_tick=on_tick,
                      **({} if sink is None else {"sink": sink}))


@contextlib.contextmanager
def using(adapter):
    """Route every run() in this context (and its threads' copied contexts) to adapter."""
    token = _active.set(adapter)
    try:
        yield adapter
    finally:
        _active.reset(token)


# --- adapters ----------------------------------------------------------------------------------

class Sandbox:
    """bwrap through executor.execute: the candidate read-only at /candidate, one writable /work."""

    name = "sandbox"

    def run(self, intent, *, inputs, work, timeout, mounts, cancel, on_line=None, on_launch=None, on_tick=None,
            **callbacks):
        if intent.isolation != "sandbox":
            raise ValueError(f"the sandbox adapter cannot run a {intent.isolation} intent")
        if inputs is None:
            raise ValueError("a sandboxed run needs its read-only inputs")
        if on_line is not None or on_launch is not None or on_tick is not None or callbacks:
            raise ValueError("the sandbox adapter returns output with the record, not by callback")
        if intent.remount_ro or intent.log_bytes is not None:
            return self._bounded(intent, inputs=inputs, work=work, timeout=timeout, mounts=mounts, cancel=cancel)
        record = executor.execute(inputs, work, list(intent.argv), timeout=timeout, stdin=intent.stdin,
                                  network=intent.network, extra_ro=tuple(mounts), cancellation=cancel,
                                  skip_visuals=intent.skip_visuals)
        return Execution(record, *_read_logs(work))


    def _bounded(self, intent, *, inputs, work, timeout, mounts, cancel):
        """The same bwrap view plus read-only remounts and a retained-log ceiling, supervised here."""
        if any(isinstance(source, int) for source, _ in mounts):
            raise ValueError("a bounded sandbox run takes path mounts only")
        forget_scope()
        try:
            with _records(work) as opened:
                command = sandbox_command(intent, inputs, work, mounts)

                def finish(fields, output, stderr):
                    record = {"argv": list(intent.argv), "sandbox_argv": command, "timeout_s": timeout, **fields}
                    record.setdefault("duration_s", 0.0)
                    _digests(record, output, stderr)
                    _write_records(opened, record, output, stderr)
                    return Execution(record, output, stderr)

                return _supervised(finish, command, timeout=timeout, stdin=intent.stdin, cancel=cancel,
                                   log_bytes=intent.log_bytes)
        finally:
            forget_scope()


def sandbox_command(intent, inputs, work, mounts):
    """The executor's bwrap argv for intent, with intent.remount_ro inserted before the tool argv."""
    command = executor.sandbox_argv(inputs, work, list(intent.argv), network=intent.network,
                                    extra_ro=tuple(mounts), skip_visuals=intent.skip_visuals)
    boundary = command.index("--")
    command[boundary:boundary] = [arg for path in intent.remount_ro for arg in ("--remount-ro", path)]
    return command


def declared_sandbox_argv(inputs, work, argv, *, network=False, mounts=(), mount_identities=None):
    """The bwrap argv a sandbox run of argv would carry, for checking a retained record against its
    declaration; mount_identities stands in for descriptors that are no longer open. Nothing runs."""
    return executor.sandbox_argv(inputs, work, list(argv), network=network, extra_ro=tuple(mounts),
                                 mount_identities=mount_identities)


def _read_logs(work):
    out = []
    for name in ("stdout.log", "stderr.log"):
        try:
            out.append((Path(work) / name).read_bytes())
        except OSError:
            out.append(b"")
    return out


def supervise(argv, *, timeout, stdin=None, cancel=None, env=None, cwd=None, on_line=None, on_stop=None,
              on_launch=None, stop_grace=None, on_tick=None, log_bytes=None, sink=None, merge_stderr=False):
    """Run argv as a host process group under one deadline and cancel handle.

    Returns (record fields, stdout, stderr). Output is drained without blocking, so a child
    that never reads stdin or never closes stdout cannot outlive its deadline. Timeout and
    cancel kill the whole group and then keep what it already wrote. on_stop (a container
    client's cleanup) runs after the kill on every exit except the tool's own completion.
    fields["returncode"] is the reaped status on every path that launched the child (adapters move it
    to Execution.returncode). timeout None means no limit of its own. on_launch gets the child's pid. stop_grace sends
    SIGTERM to the group and waits that long before SIGKILL on an early stop. on_tick runs before
    every poll of the loop. log_bytes caps the combined stdout+stderr kept; the first read past it
    stops the run as output_limit (logs_truncated), and the post-stop drain stays within the cap.
    sink gets each stdout chunk as it arrives (flushed); merge_stderr sends stderr into stdout.
    When on_line, on_launch or on_tick raises, the run stops like a timeout (execution "callback_failed") and the exception
    is re-raised with (fields, stdout, stderr) attached as native_execution.
    """
    cancel = cancel or Cancellation()
    fields = {"execution": "not_run", "exit_code": None}
    output, errors, pending = bytearray(), bytearray(), bytearray()
    start = time.monotonic()
    proc = None
    with tempfile.TemporaryFile() as input_stream:
        if stdin is not None:
            input_stream.write(stdin.encode() if isinstance(stdin, str) else stdin)
        input_stream.seek(0)
        try:
            with held_interrupt() as interrupt, cancel.launch_lock:
                if not cancel.cancelled.is_set():
                    proc = subprocess.Popen(list(argv), stdin=input_stream, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                                            start_new_session=True,
                                            env=None if env is None else dict(env), cwd=cwd)
        except FileNotFoundError as exc:
            fields["execution"] = "unavailable"
            return fields, str(exc).encode(), b""
    if proc is None:
        fields["execution"] = "interrupted"
        if interrupt:
            stop = KeyboardInterrupt()
            stop.native_execution = (fields, b"", b"")
            raise stop
        return fields, b"", b""
    stopped, completed, failure = None, False, None
    if log_bytes is not None:
        fields["logs_truncated"] = False
    limit_at = float("inf") if timeout is None else start + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)

            def read(wait, deliver):
                """Read what is ready; True when the retained-log ceiling was passed."""
                for key, _ in selector.select(timeout=wait):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    overflow = False
                    if log_bytes is not None:
                        available = log_bytes - len(output) - len(errors)
                        overflow = len(chunk) > available
                        chunk = chunk[:max(0, available)]
                        if overflow:
                            fields["logs_truncated"] = True
                    if key.fileobj is proc.stderr:
                        errors.extend(chunk)
                    else:
                        output.extend(chunk)
                        if sink is not None:
                            sink.write(chunk)
                            sink.flush()
                        if deliver and on_line is not None:
                            pending.extend(chunk)
                            while b"\n" in pending:
                                line, _, rest = pending.partition(b"\n")
                                pending[:] = rest
                                on_line(bytes(line))
                    if overflow:
                        return True
                return False

            try:
                if on_launch is not None:
                    on_launch(proc.pid)
                while selector.get_map() or proc.poll() is None:
                    if on_tick is not None:
                        on_tick()
                    if cancel.cancelled.is_set() or interrupt:
                        stopped = "interrupted"
                        break
                    remaining = limit_at - time.monotonic()
                    if remaining <= 0:
                        stopped = "timeout"
                        break
                    if read(min(.05, remaining), True):
                        stopped = "output_limit"
                        break
            except BaseException as exc:
                # An operator interrupt (SIGINT delivered while supervising) stops the run as interrupted;
                # anything else raised here came from a callback.
                failure = exc
                stopped = "interrupted" if isinstance(exc, KeyboardInterrupt) else "callback_failed"
            if stopped is not None:
                # Stop first, then keep what the group already wrote, bounded as the executor's drain is.
                _stop_group(proc, stop_grace)
                limit = time.monotonic() + 1
                capped = False
                while selector.get_map() and time.monotonic() < limit and not capped:
                    capped = read(max(0.0, limit - time.monotonic()), False)
                if selector.get_map() and not capped:
                    fields["cleanup_error"] = "process group killed but output drain exceeded one second"
        if stopped is None:
            try:
                if pending and on_line is not None:
                    on_line(bytes(pending))
            except BaseException as exc:
                failure = exc
                stopped = "interrupted" if isinstance(exc, KeyboardInterrupt) else "callback_failed"
                _stop_group(proc, stop_grace)
            else:
                fields["exit_code"] = proc.wait()
                fields["execution"] = "ok" if fields["exit_code"] == 0 else "failed"
                completed = True
        if stopped is not None:
            fields["execution"] = stopped
    finally:
        # Reap the owned group even when its leader exited and a descendant holds a pipe.
        _kill_group(proc)
        proc.wait()
        fields["returncode"] = proc.returncode
        if failure is not None:
            fields["exit_code"] = proc.returncode
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        # Any exit short of the tool's own completion (timeout, cancel, an interrupt or a failing
        # on_line) runs the stop cleanup; a killed container client leaves its container running.
        if not completed and on_stop is not None:
            try:
                on_stop()
            except Exception as exc:
                fields["cleanup_error"] = f"stop cleanup failed: {exc}"
    fields["duration_s"] = round(time.monotonic() - start, 3)
    if failure is not None:
        failure.native_execution = (fields, bytes(output), bytes(errors))
        raise failure
    if stopped == "interrupted" and interrupt:
        stop = KeyboardInterrupt()
        stop.native_execution = (fields, bytes(output), bytes(errors))
        raise stop
    return fields, bytes(output), bytes(errors)


def _stop_group(proc, grace):
    """SIGTERM the group and allow grace seconds before SIGKILL; kill at once without a grace."""
    if grace:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    _kill_group(proc)


def _supervised(finish, argv, **options):
    """supervise() and turn its fields into an Execution with finish; a failing callback's
    exception propagates with that Execution as native_execution."""
    def complete(fields, output, stderr):
        returncode = fields.pop("returncode", None)
        execution = finish(fields, output, stderr)
        execution.returncode = returncode
        return execution

    try:
        fields, output, stderr = supervise(argv, **options)
    except BaseException as exc:
        partial = getattr(exc, "native_execution", None)
        if isinstance(partial, tuple):
            exc.native_execution = complete(*partial)
        raise
    return complete(fields, output, stderr)


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@contextlib.contextmanager
def _records(work):
    """Open work and snapshot the record names before the tool runs, as executor.execute does,
    so a record the tool plants is displaced (docs/security.md) instead of overwritten."""
    if work is None:
        yield None
        return
    _, directory = executor._open_work(work)
    try:
        yield directory, {name: executor._entry(directory, name) for name in executor.RECORDS}
    finally:
        os.close(directory)


def _write_records(opened, record, output, stderr):
    """The executor's record files, written the executor's way (exclusive temp + rename)."""
    if opened is None:
        return
    directory, before = opened
    displaced = executor._displace(directory, before)
    if displaced:
        record["displaced_records"] = displaced
    for name, data in (("stdout.log", output), ("stderr.log", stderr), ("console.log", output + stderr)):
        publish(directory, name, data, sync=False)
    publish(directory, "execution.json", json_bytes(record))


def _digests(record, output, stderr):
    record["console_sha256"] = hashlib.sha256(output + stderr).hexdigest()
    record["console"] = "console.log"
    record["stdout_sha256"] = hashlib.sha256(output).hexdigest()
    record["stderr_sha256"] = hashlib.sha256(stderr).hexdigest()


class Host:
    """A host process in its own group. No sandbox: only for runs the sandbox cannot host."""

    name = "host"

    def run(self, intent, *, inputs, work, timeout, mounts, cancel, on_line=None, on_launch=None, on_tick=None,
            sink=None):
        if intent.isolation != "host":
            raise ValueError(f"the host adapter cannot run a {intent.isolation} intent")
        if mounts:
            raise ValueError("a host run has no mount namespace; pass host paths in argv")
        forget_scope()
        try:
            with _records(work) as opened:
                def finish(fields, output, stderr):
                    record = {"argv": list(intent.argv), "isolation": "host", "timeout_s": timeout, **fields}
                    record.setdefault("duration_s", 0.0)
                    _digests(record, output, stderr)
                    _write_records(opened, record, output, stderr)
                    return Execution(record, output, stderr)

                return _supervised(finish, intent.argv, timeout=timeout, stdin=intent.stdin, cancel=cancel,
                                   env=intent.env, cwd=intent.cwd, on_line=on_line, on_launch=on_launch,
                                   on_tick=on_tick, stop_grace=intent.stop_grace_s, sink=sink,
                                   merge_stderr=intent.merge_stderr)
        finally:
            forget_scope()


# The documented outer confinement for a pinned image (benchmark_run.container_command).
DOCKER_SECURITY = ("seccomp=unconfined", "apparmor=unconfined", "systempaths=unconfined")


@dataclass(frozen=True)
class Docker:
    """A pinned image without network. The client runs as a host process; a stopped run also
    kills the named container, which outlives a killed client."""

    image: str
    name: str
    volumes: tuple = ()           # (host path, container path, read_only)
    env: tuple = ("HOME=/tmp",)
    workdir: Optional[str] = None
    entrypoint: Optional[str] = None
    security: tuple = DOCKER_SECURITY
    user: Optional[str] = field(default=None)

    def argv(self, intent):
        user = self.user or f"{os.getuid()}:{os.getgid()}"
        command = ["docker", "run", "--rm", "--init", "--name", self.name]
        if not intent.network:
            command += ["--network", "none"]
        command += ["--user", user]
        for value in self.env:
            command += ["-e", value]
        for option in self.security:
            command += ["--security-opt", option]
        for host, inside, read_only in self.volumes:
            command += ["-v", f"{host}:{inside}" + (":ro" if read_only else "")]
        if self.workdir is not None:
            command += ["-w", self.workdir]
        if self.entrypoint is not None:
            command += ["--entrypoint", self.entrypoint]
        return command + [self.image, *intent.argv]

    def run(self, intent, *, inputs, work, timeout, mounts, cancel, on_line=None, on_launch=None, on_tick=None,
            sink=None):
        if intent.isolation != "docker":
            raise ValueError(f"the docker adapter cannot run a {intent.isolation} intent")
        if mounts:
            raise ValueError("docker runs declare their volumes on the adapter")
        command = self.argv(intent)

        def kill_container():
            subprocess.run(["docker", "kill", self.name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=False)

        with _records(work) as opened:
            def finish(fields, output, stderr):
                record = {"argv": list(intent.argv), "isolation": "docker", "docker_argv": command,
                          "image": self.image, "timeout_s": timeout, **fields}
                record.setdefault("duration_s", 0.0)
                _digests(record, output, stderr)
                _write_records(opened, record, output, stderr)
                return Execution(record, output, stderr)

            return _supervised(finish, command, timeout=timeout, stdin=intent.stdin, cancel=cancel,
                               on_line=on_line, on_stop=kill_container, on_launch=on_launch,
                               on_tick=on_tick, stop_grace=intent.stop_grace_s, sink=sink,
                               merge_stderr=intent.merge_stderr)


@dataclass
class Outcome:
    """One canned tool result for the Recorded adapter."""
    exit_code: Optional[int] = 0
    stdout: bytes = b""
    stderr: bytes = b""
    files: Mapping[str, bytes] = field(default_factory=dict)   # written into work before the records
    execution: Optional[str] = None                            # default: ok / failed from exit_code
    pid: int = 0                                               # what on_launch receives


@dataclass(frozen=True)
class Call:
    intent: Intent
    inputs: Optional[Path]
    work: Optional[Path]
    timeout: float
    mounts: tuple


class Recorded:
    """Test adapter: answer each run from responder(call) -> Outcome (or an exception to raise).

    It records every call and writes the record files a real run would, with the sandbox argv
    the executor would have built, so code reading execution.json sees the true shape."""

    name = "recorded"

    def __init__(self, responder: Callable[[Call], object] = None):
        self.responder = responder or (lambda call: Outcome())
        self.calls = []

    def run(self, intent, *, inputs, work, timeout, mounts, cancel, on_line=None, on_launch=None, on_tick=None,
            sink=None):
        call = Call(intent, None if inputs is None else Path(inputs), None if work is None else Path(work),
                    timeout, tuple(mounts))
        self.calls.append(call)
        outcome = self.responder(call)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, Outcome):
            raise TypeError("a recorded responder returns an Outcome or an exception")
        if cancel is not None and cancel.cancelled.is_set():
            outcome = Outcome(exit_code=None, execution="interrupted")
        execution = outcome.execution or ("ok" if outcome.exit_code == 0 else "failed")
        if on_launch is not None:
            on_launch(outcome.pid)
        if on_tick is not None:
            on_tick()
        record = {"argv": list(intent.argv)}
        if intent.isolation == "sandbox":
            record["sandbox_argv"] = executor.sandbox_argv(inputs, work, list(intent.argv), network=intent.network,
                                                           extra_ro=tuple(mounts), skip_visuals=intent.skip_visuals)
        else:
            record["isolation"] = intent.isolation
        record.update(timeout_s=timeout, execution=execution, exit_code=outcome.exit_code)
        if intent.isolation == "sandbox" and any(isinstance(source, int) for source, _ in mounts):
            record["mount_guard"] = True
        record["duration_s"] = 0.0
        _digests(record, outcome.stdout, outcome.stderr)
        with _records(work) as opened:
            # The canned files are the tool's output, written after the record snapshot.
            for name, data in (outcome.files.items() if opened is not None else ()):
                target = Path(work) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            _write_records(opened, record, outcome.stdout, outcome.stderr)
        if sink is not None:
            sink.write(outcome.stdout)
        if on_line is not None:
            for line in outcome.stdout.splitlines():
                on_line(line)
        result = Execution(record, outcome.stdout, outcome.stderr,
                           returncode=None if intent.isolation == "sandbox" else outcome.exit_code)
        if execution == "interrupted":
            stop = KeyboardInterrupt()
            stop.native_execution = result
            raise stop
        return result


DEFAULT_ADAPTERS = {"sandbox": Sandbox(), "host": Host()}


class _NoDefaultDocker:
    name = "docker"

    def run(self, intent, **_):
        raise ValueError("a docker intent needs a Docker adapter naming its pinned image")


DEFAULT_ADAPTERS["docker"] = _NoDefaultDocker()


# --- the parsed sandbox profile ----------------------------------------------------------------

# Operand counts of every bwrap option the executor (and replay's read-only remounts) emit.
# Anything else is an unknown layout and refused, never guessed.
_ARITY = {
    "--die-with-parent": 0, "--new-session": 0, "--unshare-pid": 0, "--unshare-ipc": 0,
    "--unshare-uts": 0, "--unshare-net": 0, "--clearenv": 0,
    "--proc": 1, "--dev": 1, "--tmpfs": 1, "--dir": 1, "--chdir": 1, "--remount-ro": 1,
    "--ro-bind": 2, "--bind": 2, "--ro-bind-fd": 2, "--symlink": 2, "--setenv": 2,
}
BIND_OPTIONS = ("--ro-bind", "--bind", "--ro-bind-fd")


@dataclass(frozen=True)
class Bind:
    option: str      # --ro-bind, --bind or --ro-bind-fd
    source: str      # a host path, or a descriptor number for --ro-bind-fd
    dest: str
    index: int       # position of source in the sandbox argv

    @property
    def writable(self):
        return self.option == "--bind"


@dataclass(frozen=True)
class SandboxProfile:
    """The sandbox options of one execution record, parsed once."""
    options: tuple                       # every token before the first '--'
    binds: tuple                         # Bind, in argv order
    network: bool                        # True unless --unshare-net
    tool_argv: tuple                     # the declared tool argv, inside the mount guard
    mount_identities: Optional[dict]     # dest -> (device, inode) when the guard is present

    @property
    def guarded(self):
        return self.mount_identities is not None

    def bound(self, dest):
        """Sources bound at dest, in order."""
        return [bind.source for bind in self.binds if bind.dest == dest]

    def normalized(self):
        """Options with per-run host bind sources replaced by '<host>' (a source bound at its
        own path stays); the form stage receipts hash as sandbox_profile_sha256."""
        profile, index = [], 0
        sources = {bind.index: bind for bind in self.binds}
        while index < len(self.options):
            bind = sources.get(index + 1)
            if bind is not None and self.options[index] == bind.option:
                profile += [bind.option, bind.source if bind.source == bind.dest else "<host>", bind.dest]
                index += 3
            else:
                profile.append(self.options[index])
                index += 1
        return profile

    def path_source_indexes(self):
        """Argv positions of the --ro-bind/--bind host path sources (not descriptors)."""
        return [bind.index for bind in self.binds if bind.option != "--ro-bind-fd"]


def parse_profile(sandbox_argv):
    """Parse an executor-built bwrap argv; ValueError on any layout this module did not emit."""
    if (not isinstance(sandbox_argv, list) or not sandbox_argv or sandbox_argv[0] != "bwrap" or
            "--" not in sandbox_argv or not all(isinstance(token, str) for token in sandbox_argv)):
        raise ValueError("malformed sandbox argv")
    end = sandbox_argv.index("--")
    binds, index = [], 1
    while index < end:
        option = sandbox_argv[index]
        arity = _ARITY.get(option)
        if arity is None:
            raise ValueError(f"unknown sandbox option {option!r}")
        if index + arity >= end:
            raise ValueError(f"sandbox option {option} lacks its operands")
        if option in BIND_OPTIONS:
            binds.append(Bind(option, sandbox_argv[index + 1], sandbox_argv[index + 2], index + 1))
        index += 1 + arity
    tool_argv, identities = executor.sandbox_invocation(sandbox_argv)
    return SandboxProfile(options=tuple(sandbox_argv[:end]), binds=tuple(binds),
                          network="--unshare-net" not in sandbox_argv[:end],
                          tool_argv=tuple(tool_argv), mount_identities=identities)


def profile(record):
    """The parsed sandbox profile of an execution record."""
    if not isinstance(record, dict) or "sandbox_argv" not in record:
        raise ValueError("execution record has no sandbox argv")
    return parse_profile(record["sandbox_argv"])
