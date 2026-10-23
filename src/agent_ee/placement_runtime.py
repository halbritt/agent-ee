"""Explicit reference execution and shared whole-planner input validation.

Production planning uses Rust. The Python planner is available only through the
reference context, including the existing Python trace diagnostic.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_ACTIVE = ContextVar('agent_ee_placement_runtime', default=None)


class PlanningProcessError(RuntimeError):
    """A planner process or its protocol failed; no reference plan was substituted."""


def active():
    return _ACTIVE.get()


@contextmanager
def python_reference():
    """Select the independent Python oracle explicitly for parity or tracing."""
    if active() is not None:
        raise ValueError('a planning runtime is already active in this context')
    token = _ACTIVE.set(_PythonReference())
    try:
        yield
    finally:
        _ACTIVE.reset(token)


class _PythonReference:
    implementation = 'python-reference'

    def plan(self, facts, request, policy, netlist, *, heartbeat):
        from .placer_runner import _plan_local
        return _plan_local(facts, request, policy, netlist, heartbeat=heartbeat)


def _json_input(value, path='input', ancestors=None):
    """Refuse Python objects that JSON would silently turn into different types."""
    kind = type(value)
    if value is None or kind in (str, bool, int, float):
        return  # json.dumps(allow_nan=False) separately refuses non-finite floats.
    if kind not in (dict, list):
        raise ValueError(f'{path}: planning needs JSON-native values, got {kind.__name__}')
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError(f'{path}: planning needs acyclic JSON-native values')
    ancestors.add(id(value))
    try:
        if kind is dict:
            for key, member in value.items():
                if type(key) is not str:
                    raise ValueError(f'{path}: planning JSON object keys must be strings')
                _json_input(member, f'{path}[{key!r}]', ancestors)
        else:
            for index, member in enumerate(value):
                _json_input(member, f'{path}[{index}]', ancestors)
    finally:
        ancestors.remove(id(value))
