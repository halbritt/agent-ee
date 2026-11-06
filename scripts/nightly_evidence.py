"""Inventory retained run outputs without following links or deleting evidence."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from datetime import datetime, timezone


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root: Path) -> dict:
    entries = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if name == 'output-manifest.json':
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entries[name] = {'kind': 'symlink', 'target': os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                entries[name] = {'kind': 'directory'}
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                entries[name] = {'kind': 'file', 'bytes': info.st_size, 'sha256': digest(path)}
            else:
                raise ValueError(f'retained output contains a special entry: {path}')
    return entries


def write_manifest(run_dir: Path) -> Path:
    path = run_dir / 'output-manifest.json'
    payload = {'schema': 'agent-ee.nightly-outputs/1', 'entries': inventory(run_dir)}
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)
    return path


def verify_manifest(run_dir: Path, expected_hash: str) -> dict:
    path = run_dir / 'output-manifest.json'
    if path.is_symlink() or digest(path) != expected_hash:
        raise ValueError('retained output manifest hash differs from the selected gate')
    payload = json.loads(path.read_text())
    if payload.get('schema') != 'agent-ee.nightly-outputs/1' or payload.get('entries') != inventory(run_dir):
        raise ValueError('retained gate outputs are missing, changed or unrecorded')
    return payload


def retained_bytes(root: Path, seen: set | None = None) -> int:
    seen = seen if seen is not None else set()
    total, pending = 0, [root]
    while pending:
        for path in pending.pop().iterdir():
            info = path.lstat()
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def read_index(state_dir: Path) -> dict:
    path = state_dir / 'retention-index.json'
    if path.is_symlink():
        raise ValueError('retention index cannot be a symlink')
    if not path.exists():
        return {'schema': 'agent-ee.nightly-history/1', 'entries': {}}
    record = json.loads(path.read_text(), object_pairs_hook=unique_fields)
    if (not isinstance(record, dict) or record.get('schema') != 'agent-ee.nightly-history/1'
            or not isinstance(record.get('entries'), dict)):
        raise ValueError('retention index is malformed; lead reconciliation required')
    return record


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate retention field: {key}')
        result[key] = value
    return result


def save_index(state_dir: Path, record: dict) -> None:
    path = state_dir / 'retention-index.json'
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(path)


def register_run(state_dir: Path, run_dir: Path, attempt: str, worktree: Path | None = None,
                 state: str = 'preparing') -> None:
    index = read_index(state_dir)
    if run_dir.name in index['entries']:
        raise ValueError('retained attempt is already registered')
    index['entries'][run_dir.name] = {'attempt_id': attempt, 'state': state,
        'worktree': str(worktree) if worktree else None, 'scratch_root': None, 'scratch': [],
        'execution_sha256': None}
    save_index(state_dir, index)


def update_run(state_dir: Path, run_dir: Path, state: str) -> None:
    index = read_index(state_dir)
    entry = index['entries'][run_dir.name]
    entry['state'] = state
    path = run_dir / 'execution.json'
    if path.exists():
        receipt = json.loads(path.read_text())
        entry['execution_sha256'] = digest(path)
        entry['scratch_root'] = receipt.get('scratch_root')
        entry['scratch'] = receipt.get('retained_scratch', [])
    save_index(state_dir, index)


def history_usage(state_dir: Path) -> tuple[int, int]:
    runs = state_dir / 'runs'
    if runs.is_symlink():
        raise ValueError('retained history root cannot alias another directory')
    selected = list(runs.iterdir()) if runs.exists() else []
    index = read_index(state_dir)
    if set(index['entries']) != {path.name for path in selected}:
        raise ValueError('retention index/history differs or is missing; lead must reconcile every retained attempt')
    seen = set()
    size = retained_bytes(runs, seen) if runs.exists() else 0
    scratch, worktrees, attempts = set(), set(), set()
    for run in selected:
        if run.is_symlink() or not run.is_dir():
            raise ValueError('retained run must be an owned directory, not a link or file')
        entry = index['entries'][run.name]
        if (not isinstance(entry, dict) or entry.get('state') not in (
                'preparing', 'complete', 'not_started', 'incomplete', 'imported')
                or not isinstance(entry.get('scratch'), list)):
            raise ValueError('retained attempt index entry is malformed')
        attempt = entry.get('attempt_id')
        if not isinstance(attempt, str) or str(uuid.UUID(attempt)) != attempt:
            raise ValueError('retained attempt identity is malformed')
        attempts.add(attempt)
        origin = run / ('baseline-origin.json' if entry['state'] == 'imported' else 'attempt.json')
        identity = json.loads(origin.read_text())
        if (not isinstance(identity, dict)
                or identity.get('original_attempt_id' if entry['state'] == 'imported' else 'attempt_id') != attempt):
            raise ValueError('retained attempt index differs from its immutable identity')
        if entry.get('worktree'):
            path = Path(entry['worktree'])
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise ValueError('selected retained worktree is missing or aliased')
            worktrees.add(path)
        receipt = run / 'execution.json'
        if entry['state'] in ('complete', 'not_started') and not entry.get('execution_sha256'):
            raise ValueError('completed retained attempt has no bound execution receipt')
        if entry.get('execution_sha256') and (not receipt.is_file() or digest(receipt) != entry['execution_sha256']):
            raise ValueError('retained execution receipt is missing or corrupt')
        if receipt.exists():
            record = json.loads(receipt.read_text())
            if (not isinstance(record, dict) or record.get('nightly_attempt_id') != attempt
                    or entry['scratch'] != record.get('retained_scratch', [])
                    or entry['scratch_root'] != record.get('scratch_root')):
                raise ValueError('retained execution/index custody differs')
        for value in entry['scratch']:
            if not isinstance(value, str) or not isinstance(entry['scratch_root'], str):
                raise ValueError('retained scratch reference is malformed')
            root = Path(entry['scratch_root']).resolve()
            path = Path(value)
            if path.is_symlink() or path.parent.resolve() != root or not path.name.startswith('run-'):
                raise ValueError('retained scratch identity is outside its recorded runner root')
            scratch.add(path)
    for path in scratch | worktrees:
        if not path.is_dir():
            raise ValueError('selected retained scratch/worktree is missing; reconcile before new work')
        size += retained_bytes(path, seen)
    return len(attempts), size


def guard_retention(state_dir: Path, max_runs: int, max_bytes: int) -> None:
    """Index every state; hold at high-water thresholds without deleting bytes."""
    check = {'schema': 'agent-ee.nightly-retention-check/1', 'runs': None, 'bytes': None,
             'max_runs': max_runs, 'max_bytes': max_bytes, 'checked_at': datetime.now(timezone.utc).isoformat(),
             'remediation': 'Lead must reconcile/export indexed history before authorizing any release; delete nothing automatically.'}
    try:
        if max_runs < 1 or max_bytes < 1:
            raise ValueError('retention thresholds must be positive')
        count, size = history_usage(state_dir)
        check.update(runs=count, bytes=size)
        if count >= max_runs or size >= max_bytes:
            raise ValueError(f'retained history threshold reached: {count} runs, {size} bytes; lead reconciliation required')
        check['decision'] = 'admit'
    except (OSError, ValueError, KeyError, TypeError) as error:
        check.update(decision='hold', reason=str(error))
        raise ValueError(f'retention accounting refused: {error}') from error
    finally:
        (state_dir / 'retention-check.json').write_text(json.dumps(check, indent=2) + '\n')
