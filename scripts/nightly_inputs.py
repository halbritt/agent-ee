"""Nightly consumer of the runner's native input receipt, and fresh stock preparation.

Part B (AGENTEE-619/621/622/624): the installed runner, given AE_CI_INPUT_MANIFEST and
AE_CI_EVIDENCE_DIR, prepares finite read-only input snapshots through its helper,
binds the resolved image into one receipt (agent-ee.native-inputs/1) and announces
`ae-ci-exec: native input receipt /absolute/path` in the log. This module reads that
announcement and binds the receipt to the attempt: selected manifest bytes, source
commit/tree, runner hash and image. It parses no helper internals beyond that
contract and validates no mounts; the runner-invoked helper owns mount policy.

Part C (AGENTEE-621 freshness): before an actually selected native attempt, refresh
the acquisition stock context with the EXISTING producer (agent_ee.jlc_stock_capture)
and the EXISTING conservative union algebra (agent_ee.acquisition.alternative_context),
generalizing the reviewed one-off restoration. Requested IDs come from the current
context's two retained parent catalogs; raw producer bytes and provenance are kept;
there is no retry, no backdating and no stale fallback. Any failure raises, which the
controller records as a failed preparation before any native work.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import nightly_stock_prepare  # config contract shared with the child; no agent_ee import here

RECEIPT_SCHEMA = 'agent-ee.native-inputs/1'
STOCK_CONFIG_SCHEMA = 'agent-ee.nightly-stock-config/1'
RECEIPT_LINE = re.compile(r'^ae-ci-exec: native input receipt (/\S.*)$', re.MULTILINE)


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate record field: {key}')
        result[key] = value
    return result


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root: Path) -> dict:
    """Regular files only, relative POSIX paths; links and special entries refuse."""
    result, pending = {}, [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if path.is_symlink():
                raise ValueError(f'prepared input contains a symlink: {path}')
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = digest_file(path)
            else:
                raise ValueError(f'prepared input contains a special entry: {path}')
    return result


# --- Part B: receipt binding -----------------------------------------------------------

def announced_receipts(log_bytes: bytes) -> list[str]:
    return RECEIPT_LINE.findall(log_bytes.decode('utf-8', errors='replace'))


def bind_receipt(log_bytes: bytes, *, manifest: Path, evidence_dir: Path, commit: str, tree: str,
                 runner_sha256: str, image_ids: list[str]) -> dict:
    """Bind the one announced receipt to this attempt; anything else is a refusal."""
    announced = announced_receipts(log_bytes)
    if len(announced) != 1:
        raise ValueError(f'expected exactly one native input receipt announcement, found {len(announced)}')
    path = Path(announced[0])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('announced native input receipt is not a regular absolute file')
    if not path.resolve().is_relative_to(evidence_dir.resolve()):
        raise ValueError('announced native input receipt is outside this attempt evidence directory')
    raw = path.read_bytes()
    record = json.loads(raw, object_pairs_hook=unique_fields)
    if not isinstance(record, dict) or record.get('schema') != RECEIPT_SCHEMA:
        raise ValueError('native input receipt has an unsupported schema')
    if record.get('status') != 'prepared':
        raise ValueError(f"native input receipt status is {record.get('status')!r}, not prepared")
    manifest_sha256 = digest_file(manifest)
    if record.get('manifest_sha256') != manifest_sha256:
        raise ValueError('native input receipt binds a different manifest than this attempt selected')
    if record.get('image') not in image_ids:
        raise ValueError('native input receipt image differs from the image the runner announced')
    source = record.get('agent_ee')
    if not isinstance(source, dict) or source.get('commit') != commit or source.get('tree') != tree:
        raise ValueError('native input receipt binds a different source commit/tree')
    runner = record.get('runner')
    if not isinstance(runner, dict) or runner.get('sha256') != runner_sha256:
        raise ValueError('native input receipt binds a different runner')
    inputs = record.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('native input receipt lists no inputs')
    for item in inputs:
        if (not isinstance(item, dict) or item.get('read_only') is not True or not isinstance(item.get('name'), str)
                or not isinstance(item.get('env'), str) or not (item.get('files') or item.get('git'))):
            raise ValueError('native input receipt input entry is incomplete or writable')
    for field in ('policy_sha256', 'preparer', 'mount_plan_sha256', 'launch', 'launch_sha256', 'validated_at'):
        if not record.get(field):
            raise ValueError(f'native input receipt lacks {field} (launch mounts not validated before Docker)')
    launch = record['launch']
    if (not isinstance(launch, list) or not all(isinstance(arg, str) for arg in launch)
            or record['launch_sha256'] != digest_bytes(json.dumps(launch).encode())):
        raise ValueError('native input receipt launch argv does not match its recorded digest')
    return {
        'path': str(path), 'sha256': digest_bytes(raw), 'manifest': str(manifest),
        'manifest_sha256': manifest_sha256, 'image': record['image'],
        'policy_sha256': record['policy_sha256'], 'module_env_sha256': record.get('module_env_sha256'),
        'mount_plan_sha256': record['mount_plan_sha256'], 'launch_sha256': record['launch_sha256'],
        'validated_at': record['validated_at'],
        'preparer_sha256': (record['preparer'] or {}).get('sha256') if isinstance(record['preparer'], dict) else None,
        'output_directory': record.get('output_directory'),
        'inputs': [{'name': item['name'], 'env': item['env'], 'identity': item.get('git') or item.get('files')}
                   for item in inputs],
    }


def verify_bound_receipt(binding: dict, *, manifest_sha256: str) -> None:
    """Re-verify a retained binding (import-baseline): bytes unchanged, manifest identical."""
    path = Path(binding['path'])
    if path.is_symlink() or not path.is_file() or digest_file(path) != binding['sha256']:
        raise ValueError('retained native input receipt is missing or changed')
    if binding['manifest_sha256'] != manifest_sha256:
        raise ValueError('selected input manifest differs from the retained receipt binding')


# --- Part C: fresh stock preparation ---------------------------------------------------

CHILD = Path(__file__).with_name('nightly_stock_prepare.py')


def load_stock_config(path: Path, limits: dict | None = None) -> dict:
    """Structural/finite/bounded validation; producer limits are applied by the child from the selected source."""
    return nightly_stock_prepare.load_config(path, limits)


def prepare_fresh_stock(config_path: Path, worktree: Path, stock_dir: Path, *, python: str = sys.executable) -> dict:
    """Refresh the acquisition stock context for one selected native attempt.

    Runs scripts/nightly_stock_prepare.py in a fresh interpreter whose only agent_ee is
    WORKTREE/src (clean import custody); the controller never imports the producer.
    Returns {'context_path', 'source_root', 'replaced': {...}, 'record'} with Path values.
    Raises ValueError/RuntimeError on any refusal; nothing is retried or backdated.
    """
    config = load_stock_config(config_path)
    argv = [python, '-B', '-P', '-s', str(CHILD), str(config_path), str(worktree), str(stock_dir)]
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'PYTHONPATH': str(worktree / 'src'), 'PYTHONSAFEPATH': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    budget = 2 * config['invocation_timeout_seconds'] + 60
    try:
        completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('stock preparation exceeded its bounded time') from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else f'exit {completed.returncode}'
        raise RuntimeError(f'stock preparation failed: {detail}')
    try:
        result = json.loads(completed.stdout, object_pairs_hook=unique_fields)
    except ValueError as error:
        raise RuntimeError('stock preparation produced no readable result') from error
    for key in ('context_path', 'source_root'):
        result[key] = Path(result[key])
    result['replaced'] = {key: Path(value) for key, value in result['replaced'].items()}
    return result


def rebind_manifest(manifest: Path, destination: Path, prepared: dict) -> Path:
    """Write the per-attempt manifest: only the two stock-context entries change paths/hashes."""
    document = json.loads(manifest.read_bytes(), object_pairs_hook=unique_fields)
    if not isinstance(document, dict) or document.get('schema') != RECEIPT_SCHEMA or not isinstance(document.get('inputs'), list):
        raise ValueError('input manifest must be agent-ee.native-inputs/1 with an inputs list')
    old_source, old_context = str(prepared['replaced']['source_root']), str(prepared['replaced']['context_path'])
    updated = []
    for item in document['inputs']:
        if not isinstance(item, dict):
            raise ValueError('input manifest entry must be an object')
        if item.get('path') == old_source:
            item['path'], item['files'] = str(prepared['source_root']), inventory(prepared['source_root'])
            updated.append(item.get('name'))
        elif item.get('path') == old_context:
            item['path'], item['files'] = str(prepared['context_path']), {'.': digest_file(prepared['context_path'])}
            updated.append(item.get('name'))
    if len(updated) != 2:
        raise ValueError(f'input manifest must reference the replaced stock source root and context exactly once each; updated {updated}')
    if destination.exists() or destination.is_symlink():
        raise ValueError('per-attempt input manifest must start absent')
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    return destination
