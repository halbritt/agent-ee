"""Run one command so that its whole process tree stops with it.

`design generate` runs each phase (capture, compile, build, review) through this module. A
host run only kills its own process group, but a phase starts descendants in their own
sessions (sandbox launchers, host runs), and those would outlive a stopped coordinator.
This process is a child subreaper: when such a descendant's parent exits, the descendant
is reparented here, so everything the phase started stays below this process.

Only this process's own children are ever signalled. Until this process collects a child,
the child's PID cannot be reused, and a descendant whose parent exits becomes a child here.
So the tree is stopped from the top: each child is killed only after a fresh check, made
just before the signal, that it is still this process's child with the start time first
observed; then it is collected, and the descendants reparented here are handled the same
way. A PID from an older reading of /proc that now names another process is never signalled.

A stop (SIGTERM, SIGINT or SIGHUP, and SIGTERM when the coordinator dies) first sends
SIGINT to the phase's process group, so an agent-ee phase unwinds and keeps its records.
After GRACE_S the phase is killed. On every exit, a stop or the phase's own completion, the
remaining tree is killed. When /proc or prctl does not work, nothing is launched; when the
tree cannot be shown empty, the exit status is REFUSED.

    python3 -m agent_ee.process_tree COORDINATOR_PID -- COMMAND [ARG ...]

Linux only. A SIGKILL of this process cannot be handled: the phase then dies through its
parent-death signal, but its own session-detached descendants can remain.
"""

import ctypes
import os
import signal
import subprocess
import sys
import time

PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
PROC = '/proc'
# How long a stopped phase may unwind after SIGINT before it is killed.
GRACE_S = 5.0
# Exit status when the phase is not launched, or its tree cannot be shown empty.
REFUSED = 125
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def stat(pid):
    """(parent pid, state, start time in clock ticks) of pid, or None when it is gone."""
    try:
        with open(f'{PROC}/{pid}/stat', 'rb') as stream:
            data = stream.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = data[data.rindex(b')') + 2:].split()
    return int(fields[1]), fields[0], int(fields[19])


def children():
    """[(pid, start time)] of this process's live children, read from /proc."""
    me, found = os.getpid(), []
    for name in os.listdir(PROC):
        if name.isdigit():
            observed = stat(int(name))
            if observed is not None and observed[0] == me and observed[1] != b'Z':
                found.append((int(name), observed[2]))
    return found


def owned(pid, start):
    """True when pid is, now, a live child of this process that started at start."""
    observed = stat(pid)
    return observed is not None and observed[0] == os.getpid() and observed[2] == start and observed[1] != b'Z'


def reap():
    """Collect every exited child, including descendants reparented to this subreaper."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_tree(rounds=300):
    """SIGKILL this process's children, then the descendants reparented here, until none is
    left; True when none is left. Raises OSError when /proc cannot be read."""
    for _ in range(rounds):
        reap()
        live = [pid for pid, start in children() if owned(pid, start)]
        if not live:
            return True
        for pid in live:
            # Nothing collects children between owned() and here, so each PID is still this child.
            try:
                os.kill(pid, signal.SIGKILL)
            except PermissionError:
                pass  # A setuid child cannot be signalled; it remains, and the tree is not shown empty.
        time.sleep(0.01)
    reap()
    return not any(owned(pid, start) for pid, start in children())


def support_problem():
    """Why this process cannot own a phase tree here, or None. Changes no process state."""
    try:
        if str(os.getpid()) not in os.listdir(PROC):
            return f'{PROC} does not list this process'
        observed = stat(os.getpid())
        if observed is None or observed[0] != os.getppid():
            return f'{PROC} does not describe this process'
    except (OSError, ValueError, IndexError) as exc:
        return f'{PROC} cannot be read ({exc})'
    try:
        value = ctypes.c_int()
        if ctypes.CDLL(None, use_errno=True).prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) != 0:
            return 'prctl is refused (' + os.strerror(ctypes.get_errno()) + ')'
    except (OSError, AttributeError) as exc:
        return f'prctl is unavailable ({exc})'
    return None


def _die_with_parent():
    prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 3 or not argv[0].isdigit() or argv[1] != '--':
        print('usage: python3 -m agent_ee.process_tree COORDINATOR_PID -- COMMAND [ARG ...]', file=sys.stderr)
        return 2
    coordinator, command = int(argv[0]), argv[2:]
    stops = []
    for number in STOP_SIGNALS:
        signal.signal(number, lambda signum, frame: stops.append(signum))
    # Children are collected only here: an inherited "ignore" would let the kernel reap them.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    problem = support_problem()
    if problem is None:
        try:
            prctl(PR_SET_CHILD_SUBREAPER, 1)
            prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except OSError as exc:
            problem = f'prctl is refused ({exc})'
    if problem is not None:
        print(f'agent-ee process tree: {problem}; the phase was not launched', file=sys.stderr)
        return REFUSED
    if os.getppid() != coordinator:
        # The coordinator exited before the parent-death signal was armed.
        stops.append(signal.SIGTERM)
    child, emptied = None, False
    try:
        if not stops:
            try:
                child = subprocess.Popen(command, process_group=0, preexec_fn=_die_with_parent)
            except OSError as exc:
                print(f'agent-ee process tree: cannot start {command[0]}: {exc}', file=sys.stderr)
                return 127
        while child is not None and child.poll() is None and not stops:
            time.sleep(0.05)
        if child is not None and child.returncode is None:
            # The child is not collected yet, so its process group is still the phase's.
            try:
                os.killpg(child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                child.wait(GRACE_S)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    finally:
        try:
            emptied = stop_tree()
        except OSError as exc:
            print(f'agent-ee process tree: the phase tree could not be inspected ({exc})', file=sys.stderr)
        if not emptied:
            print('agent-ee process tree: the phase tree could not be shown empty; descendants may remain',
                  file=sys.stderr)
    if not emptied:
        return REFUSED
    if stops:
        return 128 + stops[0]
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == '__main__':
    sys.exit(main())
