"""Explicit PyPy execution of one complete pure planning operation.

KiCad capture, board writing and native checks stay in the calling CPython process.
There is no fallback, per-score IPC, persistent worker or additional search budget.
An active placement trace or Rust scorer is refused rather than silently omitted.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback

_ACTIVE = ContextVar('agent_ee_placement_runtime', default=None)
_SCHEMA = 'agent-ee.pypy-planning/1'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sources():
    return {path.name: _sha(path) for path in sorted(Path(__file__).parent.glob('*.py'))}


def _json_input(value, path='input', ancestors=None):
    """Refuse Python objects that JSON would silently turn into different types."""
    kind = type(value)
    if value is None or kind in (str, bool, int, float):
        return  # json.dumps(allow_nan=False) separately refuses non-finite floats.
    if kind not in (dict, list):
        raise ValueError(f'{path}: PyPy planning needs JSON-native values, got {kind.__name__}')
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError(f'{path}: PyPy planning needs acyclic JSON-native values')
    ancestors.add(id(value))
    try:
        if kind is dict:
            for key, member in value.items():
                if type(key) is not str:
                    raise ValueError(f'{path}: PyPy planning JSON object keys must be strings')
                _json_input(member, f'{path}[{key!r}]', ancestors)
        else:
            for index, member in enumerate(value):
                _json_input(member, f'{path}[{index}]', ancestors)
    finally:
        ancestors.remove(id(value))


def active():
    return _ACTIVE.get()


@contextmanager
def experimental(interpreter):
    """Opt in around place_candidate(); session.calls retains each process outcome."""
    if active() is not None:
        raise ValueError('a planning runtime is already active in this context')
    session = Session(interpreter)
    token = _ACTIVE.set(session)
    try:
        yield session
    finally:
        _ACTIVE.reset(token)


class PlanningProcessError(RuntimeError):
    """A planner process or its protocol failed; no local plan was substituted."""


class Session:
    def __init__(self, interpreter):
        self.interpreter = Path(interpreter).resolve(strict=True)
        self.calls = []

    def plan(self, facts, request, policy, netlist, *, heartbeat):
        from . import placement_scoring, placement_trace
        if placement_trace.active() is not None:
            raise ValueError('PyPy planning does not support an active placement trace')
        if placement_scoring.active() is not None:
            raise ValueError('PyPy planning cannot run with an active Rust scorer')
        started = time.perf_counter_ns()
        record = {'schema': _SCHEMA, 'status': 'starting', 'runtime': None,
                  'first_wire_cost': None, 'error': None, 'exit_code': None}
        self.calls.append(record)
        try:
            _json_input({'facts': facts, 'request': request, 'policy': policy})
            sources = _sources()
            executable_sha256 = _sha(self.interpreter)
            with tempfile.TemporaryDirectory(prefix='agent-ee-pypy-plan-') as directory:
                scratch = Path(directory)
                captured_netlist = None
                if netlist is not None:
                    captured_netlist = scratch / 'native.xml'
                    captured_netlist.write_bytes(Path(netlist).read_bytes())
                payload = {'schema': _SCHEMA, 'facts': facts, 'request': request, 'policy': policy,
                           'netlist': None if captured_netlist is None else str(captured_netlist),
                           'source_sha256': sources, 'executable_sha256': executable_sha256}
                input_path = scratch / 'input.json'
                input_path.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
                argv = [str(self.interpreter), '-I', '-B', str(Path(__file__).resolve()), str(input_path)]
                response = _exchange(argv, scratch, heartbeat, record)
            record.update({key: response[key] for key in ('runtime', 'source_sha256', 'first_wire_cost', 'error')})
            if response['runtime']['implementation'] != 'PyPy':
                raise PlanningProcessError('planning worker did not identify itself as PyPy')
            if response['runtime']['executable_sha256'] != executable_sha256 or response['source_sha256'] != sources:
                raise PlanningProcessError('planning worker interpreter or source identity differs')
            if _sources() != sources or _sha(self.interpreter) != executable_sha256:
                raise PlanningProcessError('planning interpreter or sources changed during execution')
            if response['error'] is not None:
                _raise_remote(response['error'])
            if record['exit_code'] != 0:
                raise PlanningProcessError(f"planning process exited {record['exit_code']}")
            record['status'] = 'ok'
            return response['plan']
        except BaseException as exc:
            record['status'] = 'error'
            if record['error'] is None:
                record['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            record['wall_ns'] = time.perf_counter_ns() - started
            record['boundary'] = 'whole planner call including input copy, identity checks, process startup and IPC'


def _exchange(argv, scratch, heartbeat, record):
    response = None
    with (scratch / 'stderr.log').open('w+', encoding='utf-8') as errors:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding='utf-8')
        try:
            for line in process.stdout:
                event = json.loads(line)
                if event.get('schema') != _SCHEMA or response is not None:
                    raise PlanningProcessError('unexpected planning worker protocol record')
                if event.get('kind') == 'heartbeat':
                    heartbeat(event['record'])
                elif event.get('kind') == 'result':
                    response = event
                else:
                    raise PlanningProcessError('unknown planning worker protocol record')
            record['exit_code'] = process.wait()
            errors.seek(0)
            record['stderr'] = errors.read()
            if response is None:
                raise PlanningProcessError(f"planning process exited {process.returncode} without a result: "
                                           + record['stderr'])
            return response
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            record['exit_code'] = process.returncode
            process.stdout.close()
            errors.seek(0)
            record['stderr'] = errors.read()


def _raise_remote(error):
    # Preserve the public planner's ordinary validation errors, and retain all other
    # child exceptions as explicit process failures with their original traceback.
    ordinary = {'ValueError': ValueError, 'TypeError': TypeError, 'OverflowError': OverflowError}
    cls = ordinary.get(error['type']) if error['module'] == 'builtins' else None
    exc = (cls(error['message']) if cls is not None else
           PlanningProcessError(f"{error['module']}.{error['type']}: {error['message']}"))
    exc.add_note('PyPy planning traceback:\n' + error['traceback'])
    raise exc


def _runtime():
    executable = Path(sys.executable).resolve()
    return {'implementation': platform.python_implementation(), 'version': sys.version,
            'python_version': list(sys.version_info[:3]),
            'executable': str(executable), 'executable_sha256': _sha(executable),
            'pypy_version': list(getattr(sys, 'pypy_version_info', ()))}


def _emit(kind, **fields):
    print(json.dumps({'schema': _SCHEMA, 'kind': kind, **fields}, allow_nan=False), flush=True)


def _worker(input_path):
    response = {'runtime': _runtime(), 'source_sha256': _sources(), 'first_wire_cost': None,
                'plan': None, 'error': None}
    try:
        if response['runtime']['implementation'] != 'PyPy':
            raise ValueError('the planning interpreter must be PyPy')
        if sys.version_info < (3, 12):
            raise ValueError('PyPy planning requires Python 3.12 or later')
        payload = json.loads(input_path.read_text(encoding='utf-8'))
        if payload['schema'] != _SCHEMA or payload['source_sha256'] != response['source_sha256'] \
                or payload['executable_sha256'] != response['runtime']['executable_sha256']:
            raise ValueError('planning worker input or runtime identity differs')
        # -I ignores ambient PYTHONPATH and the working directory. Import precisely
        # the source tree containing this worker, never an installed agent-ee copy.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from agent_ee import placer, placer_runner
        original = placer._wire_cost

        def first(pads, peers):
            placer._wire_cost = original
            response['first_wire_cost'] = {'function': 'agent_ee.placer._wire_cost', 'returned': False}
            cost = original(pads, peers)
            response['first_wire_cost'].update(returned=True, cost_nm=cost)
            return cost

        placer._wire_cost = first
        try:
            response['plan'] = placer_runner._plan_local(payload['facts'], payload['request'], payload['policy'],
                                                        payload['netlist'], heartbeat=lambda row: _emit('heartbeat', record=row))
        finally:
            placer._wire_cost = original
    except Exception as exc:
        response['error'] = {'module': type(exc).__module__, 'type': type(exc).__name__,
                             'message': str(exc), 'traceback': traceback.format_exc()}
    _emit('result', **response)
    return int(response['error'] is not None)


if __name__ == '__main__':
    raise SystemExit(_worker(Path(sys.argv[1])))
