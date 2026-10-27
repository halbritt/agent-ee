"""Test seam over agent_ee.native_run: the real sandbox run with a hook around it.

Replaces patches of executor.execute (AGENTEE-540). wrap(call, run) receives the
native_run.Call and run(intent=..., **overrides), which performs the real sandboxed run (a
replacement intent, or overrides such as timeout= for the adapter arguments); it may act
before or after run(), wrap it in try/finally, or not call it at all (to simulate a coordinator
interrupted before the executor wrote its record). Install it with native_run.using(Hooked(...)).
"""

from pathlib import Path

from agent_ee import native_run


class Hooked(native_run.Sandbox):
    def __init__(self, wrap):
        self.wrap = wrap
        self.calls = []

    def run(self, intent, *, inputs, work, timeout, mounts, cancel, **callbacks):
        call = native_run.Call(intent, None if inputs is None else Path(inputs), None if work is None else Path(work),
                               timeout, tuple(mounts))
        self.calls.append(call)
        def run(intent=intent, **overrides):
            options = dict(inputs=inputs, work=work, timeout=timeout, mounts=mounts, cancel=cancel, **callbacks)
            return native_run.Sandbox.run(self, intent, **{**options, **overrides})

        return self.wrap(call, run)


def after(hook):
    """A Hooked adapter that runs hook(call, record) after each real run and returns the record."""
    def wrap(call, run):
        record = run()
        hook(call, record)
        return record
    return Hooked(wrap)


def before(hook):
    """A Hooked adapter that runs hook(call) before each real run."""
    def wrap(call, run):
        hook(call)
        return run()
    return Hooked(wrap)
