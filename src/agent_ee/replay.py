"""Bounded single-process experiments, separate from coordinator check evidence."""

import json
import math
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from threading import BoundedSemaphore

from .evidence_requests import fresh_directory, read_request, seal_operation, validate_request
from . import native_run
from .executor import Cancellation
from .storage import digest, read_json, write_json


_ADMISSION = BoundedSemaphore(1)
LIMITS = {'wall_seconds': (0.05, 60), 'cpu_seconds': (1, 30),
          'memory_bytes': (64 * 1024**2, 512 * 1024**2), 'log_bytes': (128, 1024**2)}


class UnsupportedReplay(ValueError):
    pass


def supported_limits(spec):
    limits = spec['resources']
    if set(limits) != set(LIMITS):
        raise UnsupportedReplay('only wall_seconds, cpu_seconds, memory_bytes and log_bytes are supported; '
                                'aggregate job/directory budgets and writable outputs are unsupported')
    for name, (low, high) in LIMITS.items():
        value = limits[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not low <= value <= high or
                (name != 'wall_seconds' and not isinstance(value, int))):
            raise UnsupportedReplay(f'unsupported resource bound {name}: expected {low}..{high}')
    return limits


# The script sees the candidate, the bundle and its own scratch, all read-only.
REMOUNT_RO = ('/work', '/tmp', '/dev', '/proc', '/')


def _execute(intent, inputs, work, bundle, out, cancellation):
    """One bounded sandbox run through native_run, kept as the receipt fields replay records."""
    try:
        execution = native_run.run(intent, inputs=inputs, work=work, mounts=((bundle, '/bundle'),),
                                   cancel=cancellation)
    except KeyboardInterrupt as stop:
        # The run was stopped and reaped; its outcome is still the replay's evidence.
        cancellation.cancel()
        execution = getattr(stop, 'native_execution', None)
        if execution is None:
            raise
    returncode = execution.returncode
    record = {'execution': execution['execution'], 'exit_code': returncode,
              'sandbox_argv': execution['sandbox_argv'],
              'logs_truncated': execution.get('logs_truncated', False),
              'cleanup_error': execution.get('cleanup_error'),
              'duration_s': execution['duration_s']}
    if returncode is not None:
        record['signal'] = -returncode if returncode < 0 else None
    unavailable = execution['execution'] == 'unavailable'
    if unavailable:
        record['error'] = execution.stdout.decode(errors='replace')
    (out / 'stdout.log').write_bytes(b'' if unavailable else execution.stdout)
    (out / 'stderr.log').write_bytes(execution.stderr)
    return record


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def parse_assertions(stdout):
    value = json.loads(stdout.decode('utf-8'), object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {'assertions'} or not isinstance(value['assertions'], list):
        raise ValueError('expected only an assertions list')
    if len(value['assertions']) > 100:
        raise ValueError('too many assertions')
    for item in value['assertions']:
        if (not isinstance(item, dict) or set(item) != {'name', 'passed'} or
                not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 200 or
                not isinstance(item['passed'], bool)):
            raise ValueError('assertion requires a bounded name and Boolean passed')
    return value['assertions']


def replay(request_dir, out, *, cancellation=None, host=None):
    request_dir = Path(request_dir).resolve()
    # Raw fields label diagnostic receipts only. fresh_directory independently
    # rejects retained-run/operation ancestry before any output is created.
    initial_error = None
    try:
        raw = read_request(request_dir)
        if not isinstance(raw, dict) or not isinstance(raw.get('run'), str):
            raise ValueError('request must be an object with a run locator')
    except (ValueError, OSError) as exc:
        raw = {}
        initial_error = str(exc)
    request_file = request_dir / 'request.json'
    request_hash = (digest(request_file) if not request_file.is_symlink() and request_file.is_file()
                    and request_file.stat().st_size <= 2_000_000 else None)
    out = fresh_directory(out, request_dir, raw.get('run', request_dir))
    receipt = {'schema': 'agent-ee.experiment-receipt/1', 'request_id': raw.get('request_id'),
               'candidate_id': raw.get('candidate_id'), 'baseline_id': raw.get('baseline_id'),
               'acceptance_authority': 'none', 'concern_status': 'unresolved',
               'execution': 'not_run', 'exit_code': None, 'signal': None, 'assertions': [], 'parsing': 'not_attempted',
               'requested_script_sha256': raw.get('script_sha256'),
               'requested_interpreter_sha256': raw.get('interpreter_sha256'),
               'request_file_sha256': request_hash,
               'enforced_limits': None, 'policy': 'python-readonly-single-process/1'}
    cancellation = cancellation or Cancellation()
    acquired = False
    try:
        if initial_error:
            raise ValueError(initial_error)
        request = validate_request(request_dir, host=host)
        if request['spec']['mode'] != 'experiment':
            raise UnsupportedReplay('this request is actionable but has no Python replay adapter')
        limits = supported_limits(request['spec'])
        runtime = Path('/usr/bin/python3')
        if (request['spec']['interpreter'] != str(runtime) or not runtime.is_file() or
                request['interpreter_sha256'] != digest(runtime)):
            raise UnsupportedReplay('only the captured /usr/bin/python3 identity is supported')
        if sys.platform != 'linux' or platform.machine() != 'x86_64':
            raise UnsupportedReplay('only Linux x86-64 replay is supported')
        if not _ADMISSION.acquire(blocking=False):
            raise UnsupportedReplay('another replay is active in this coordinator process')
        acquired = True
        bundle = out / 'bundle'
        bundle.mkdir()
        shutil.copyfile(request_dir / 'script.py', bundle / 'script.py')
        if digest(bundle / 'script.py') != request['script_sha256']:
            raise ValueError('script changed during replay capture')
        # Only the candidate and captured script are visible. No sealed check
        # artifacts or peer scratch are mounted in this deliberately narrow lane.
        work = out / 'scratch'
        work.mkdir()
        launcher = Path(__file__).with_name('replay_child.py')
        shutil.copyfile(launcher, bundle / 'launcher.py')
        intent = native_run.Intent('replay', [str(runtime), '-I', '-B', '/bundle/launcher.py', json.dumps(limits)],
                                   timeout=limits['wall_seconds'], remount_ro=REMOUNT_RO, log_bytes=limits['log_bytes'])
        receipt.update(script_sha256=digest(bundle / 'script.py'),
            interpreter_sha256=digest(runtime), launcher_sha256=digest(bundle / 'launcher.py'),
            candidate_inputs=read_json(Path(request['run']) / 'candidate.json')['files'],
            enforced_limits={**limits, 'processes': 1, 'writable_files': 0, 'network': False,
                             'memory_semantics': 'RLIMIT_AS for the single script process',
                             'cpu_semantics': 'RLIMIT_CPU for the single script process',
                             'log_semantics': 'combined retained stdout and stderr byte ceiling'})
        receipt.update(_execute(intent, Path(request['run']) / 'inputs', work, bundle, out, cancellation))
        receipt['isolation_established'] = (out / 'stderr.log').read_bytes().startswith(
            b'agent-ee replay policy ready\n')
        if not receipt['isolation_established']:
            receipt['enforced_limits'] = None
            if receipt['execution'] in ('ok', 'failed', 'unavailable'):
                receipt.update(execution='unsupported', error='sandbox/launcher policy did not initialize')
        try:
            receipt['assertions'] = parse_assertions((out / 'stdout.log').read_bytes())
            receipt['parsing'] = 'ok'
        except (ValueError, UnicodeError, RecursionError) as exc:
            receipt.update(parsing='malformed', parsing_error=str(exc))
        # Check the original bindings after execution as well; a host mutation
        # invalidates even an otherwise successful experiment.
        validate_request(request_dir, host=host)
    except UnsupportedReplay as exc:
        receipt.update(execution='unsupported', error=str(exc))
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        receipt.update(execution='failed', error=f'{type(exc).__name__}: {exc}')
    except KeyboardInterrupt:
        cancellation.cancel()
        receipt.update(execution='interrupted', error='operator interrupted replay')
    finally:
        if acquired:
            _ADMISSION.release()
        for name in ('stdout', 'stderr'):
            path = out / f'{name}.log'
            if not path.exists():
                path.write_bytes(b'')
            receipt[f'{name}_sha256'] = digest(path)
        receipt['status'] = ('unsupported' if receipt['execution'] == 'unsupported' else
                             'completed' if receipt['execution'] == 'ok' and receipt['parsing'] == 'ok' else 'failed')
        write_json(out / 'receipt.json', receipt)
        seal_operation(out, 'replay')
    return receipt
