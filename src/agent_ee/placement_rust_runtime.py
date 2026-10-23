"""One whole planning request to the standalone Rust placement executable."""
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time

from . import placement_runtime

_SCHEMA = 'agent-ee.rust-planning-output/1'


_INSTALLED = Path('/usr/local/libexec/agent-ee/agent-ee-placement')


def resolve_executable():
    """Resolve a prebuilt planner; never compile, change backend or ignore a bad override."""
    configured = os.environ.get('AGENT_EE_RUST_PLANNER')
    candidate = configured if configured is not None else (
        _INSTALLED if _INSTALLED.exists() else shutil.which('agent-ee-placement'))
    if candidate is None or not str(candidate):
        raise placement_runtime.PlanningProcessError(
            'Rust placement planner is not installed; build with scripts/build_placement_engine.py '
            'and install agent-ee-placement (see docs/installation.md)')
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise placement_runtime.PlanningProcessError(f'Rust placement planner is not executable: {path}')
    return path.resolve(strict=True)


@contextmanager
def using(executable):
    if placement_runtime.active() is not None:
        raise ValueError('a planning runtime is already active in this context')
    session = Session(executable)
    token = placement_runtime._ACTIVE.set(session)
    try:
        yield session
    finally:
        placement_runtime._ACTIVE.reset(token)


class Session:
    implementation = 'rust'

    def __init__(self, executable=None):
        self.executable = resolve_executable() if executable is None else Path(executable).resolve(strict=True)
        self.calls = []

    def plan(self, facts, request, policy, netlist, *, heartbeat):
        from . import placement_trace
        if placement_trace.active() is not None:
            raise ValueError('Rust planning cannot run with an active Python placement trace')
        started = time.perf_counter_ns()
        record = {'status': 'starting', 'exit_code': None, 'error': None,
                  'boundary': 'whole planner including serialization, executable identity, startup and IPC'}
        self.calls.append(record)
        try:
            payload = {'schema': 'agent-ee.rust-planning-input/1', 'facts': facts, 'request': request,
                       'policy': policy, 'netlist_xml': None if netlist is None else Path(netlist).read_bytes().decode('utf-8')}
            placement_runtime._json_input(payload)
            encoded = json.dumps(payload, allow_nan=False).encode('utf-8')
            record['input_sha256'] = hashlib.sha256(encoded).hexdigest()
            executable_hash = hashlib.sha256(self.executable.read_bytes()).hexdigest()
            record['executable_sha256'] = executable_hash
            receipt_path = self.executable.with_name(self.executable.name + '.json')
            receipt_bytes = receipt_path.read_bytes() if receipt_path.is_file() else None
            if receipt_bytes is not None:
                try:
                    receipt = json.loads(receipt_bytes)
                except (ValueError, UnicodeError) as exc:
                    raise placement_runtime.PlanningProcessError('invalid Rust build receipt') from exc
                if (not isinstance(receipt, dict) or receipt.get('schema') != 'agent-ee.placement-engine-install/1'
                        or receipt.get('binary_sha256') != executable_hash):
                    raise placement_runtime.PlanningProcessError('Rust executable differs from its build receipt')
                record['build_receipt_sha256'] = hashlib.sha256(receipt_bytes).hexdigest()
            response = _exchange([str(self.executable), '--events'], encoded, heartbeat, record)
            if hashlib.sha256(self.executable.read_bytes()).hexdigest() != executable_hash:
                raise placement_runtime.PlanningProcessError('Rust executable changed during planning')
            if receipt_bytes is not None and receipt_path.read_bytes() != receipt_bytes:
                raise placement_runtime.PlanningProcessError('Rust build receipt changed during planning')
            if 'error' in response:
                if not isinstance(response['error'], str):
                    raise placement_runtime.PlanningProcessError('invalid Rust planning error record')
                error = (ValueError if response.get('error_type') == 'ValueError'
                         else placement_runtime.PlanningProcessError)
                raise error(response['error'])
            if record['exit_code'] != 0 or not isinstance(response.get('plan'), dict):
                raise placement_runtime.PlanningProcessError(f"Rust planning exited {record['exit_code']} without a plan")
            record['status'] = 'ok'
            return response['plan']
        except BaseException as exc:
            record['status'] = 'error'
            record['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            record['wall_ns'] = time.perf_counter_ns() - started


def _exchange(argv, encoded, heartbeat, record):
    """Own and reap one child; stderr cannot block its streamed stdout protocol."""
    response = None
    with tempfile.TemporaryFile(mode='w+b') as errors:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            process.stdin.write(encoded)
            process.stdin.close()  # The native reader needs EOF before planning starts.
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise placement_runtime.PlanningProcessError('invalid Rust planning JSON record') from exc
                if not isinstance(event, dict) or event.get('schema') != _SCHEMA or response is not None:
                    raise placement_runtime.PlanningProcessError('unexpected Rust planning protocol record')
                if event.get('kind') == 'heartbeat':
                    if not isinstance(event.get('record'), dict):
                        raise placement_runtime.PlanningProcessError('invalid Rust planning heartbeat')
                    heartbeat(event['record'])
                elif 'kind' not in event and (('plan' in event) != ('error' in event)):
                    response = event
                else:
                    raise placement_runtime.PlanningProcessError('unknown Rust planning protocol record')
            process.wait()
            if response is None:
                errors.seek(0)
                diagnostic = errors.read().decode('utf-8', errors='replace')
                raise placement_runtime.PlanningProcessError(
                    f'Rust planning exited {process.returncode} without a result: {diagnostic}')
            return response
        except BrokenPipeError as exc:
            raise placement_runtime.PlanningProcessError('Rust planning closed its input pipe') from exc
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            record['exit_code'] = process.returncode
            if not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass  # The original failure remains visible; the owned child is reaped.
            process.stdout.close()
            errors.seek(0)
            record['stderr'] = errors.read().decode('utf-8', errors='replace')
