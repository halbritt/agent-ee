"""Run one command so that its whole process tree stops with it.

`design generate` runs each phase (capture, compile, build, review) through this module. A
host run only kills its own process group, but a phase starts descendants in their own
sessions (sandbox launchers, host runs), and those would outlive a stopped coordinator.
This process is a child subreaper: when such a descendant's parent exits, the descendant
is reparented here, so everything the phase started stays below this process.

A stop (SIGTERM, SIGINT or SIGHUP, and SIGTERM when the coordinator dies) first sends
SIGINT to the phase's process group, so an agent-ee phase unwinds and keeps its records.
After GRACE_S the phase is killed. On every exit, a stop or the phase's own completion,
each remaining descendant is killed and reaped.

    python3 -m agent_ee.process_tree COORDINATOR_PID -- COMMAND [ARG ...]

Linux only (prctl and /proc). A SIGKILL of this process cannot be handled: the phase then
dies through its parent-death signal, but its own session-detached descendants can remain.
"""

import ctypes
import os
import signal
import subprocess
import sys
import time

PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36
# How long a stopped phase may unwind after SIGINT before it is killed.
GRACE_S = 5.0
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def descendants(root):
    """Live (not zombie) processes below root, from the parent links in /proc."""
    children = {}
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open(f'/proc/{name}/stat', 'rb') as stream:
                data = stream.read()
            state, parent = data[data.rindex(b')') + 2:].split()[:2]
        except (OSError, ValueError):
            continue
        children.setdefault(int(parent), []).append((int(name), state))
    found, pending = [], [root]
    while pending:
        for pid, state in children.get(pending.pop(), ()):
            pending.append(pid)
            if state != b'Z':
                found.append(pid)
    return found


def reap():
    """Collect every exited child, including descendants reparented to this subreaper."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_tree(root, rounds=300):
    """SIGKILL each live descendant of root until none is left; True when none is left.

    A descendant this user may not signal (a setuid launcher) dies with its killed parent."""
    for _ in range(rounds):
        live = descendants(root)
        if not live:
            return True
        for pid in live:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.01)
        reap()
    return not descendants(root)


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
    prctl(PR_SET_CHILD_SUBREAPER, 1)
    prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if os.getppid() != coordinator:
        # The coordinator exited before the parent-death signal was armed.
        stops.append(signal.SIGTERM)
    child = None
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
        if not stop_tree(os.getpid()):
            print('agent-ee process tree: descendants survived SIGKILL', file=sys.stderr)
    if stops:
        return 128 + stops[0]
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == '__main__':
    sys.exit(main())
