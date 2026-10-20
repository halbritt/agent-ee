"""Keep refused setup receipts outside disposable test fixtures for diagnosis."""

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


RETENTION_ROOT = Path(__file__).resolve().parents[1] / 'build/refused-step-receipts'


def _copy_at(receipt_dir, root):
    target = root / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(receipt_dir, target, symlinks=True)
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _copy_receipt(receipt_dir):
    configured = os.environ.get('AGENT_EE_REFUSED_RECEIPTS')
    if configured is not None:
        root = Path(configured)
        if not root.is_absolute():
            raise ValueError('AGENT_EE_REFUSED_RECEIPTS must be absolute')
        return _copy_at(receipt_dir, root)
    try:
        return _copy_at(receipt_dir, RETENTION_ROOT)
    except OSError:
        # mkdtemp atomically creates a private 0700 directory, without trusting
        # a predictable shared /tmp entry or changing another user's directory.
        root = Path(tempfile.mkdtemp(prefix=f'agent-ee-refused-{os.getuid()}-'))
        try:
            root.chmod(0o700)
            return _copy_at(receipt_dir, root)
        except OSError:
            shutil.rmtree(root, ignore_errors=True)
            raise


def _refusal_message(receipt_dir, receipt):
    message = f"step refused; violations {receipt['violations']}"
    try:
        target = _copy_receipt(receipt_dir)
    except (OSError, ValueError) as exc:
        return f"{message}; receipt retention failed: {exc}; original receipt at {Path(receipt_dir) / 'receipt.json'}"
    return f"{message}; receipt kept at {target / 'receipt.json'}"


def retain_refusal(receipt_dir, receipt):
    if receipt['status'] == 'refused':
        raise AssertionError(_refusal_message(receipt_dir, receipt))


def _scan_refusals(root):
    for path in Path(root).rglob('receipt.json'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            # Tests also author deliberately invalid receipt JSON.
            continue
        if (isinstance(receipt, dict) and receipt.get('schema') == 'agent-ee.stage-receipt/1'
                and receipt.get('status') == 'refused'):
            print(_refusal_message(path.parent, receipt), file=sys.stderr)


def _retain_refusals(root):
    try:
        _scan_refusals(root)
    except OSError as exc:
        print(f'receipt retention scan failed for {root}: {exc}', file=sys.stderr)


class ReceiptTemporaryDirectory(tempfile.TemporaryDirectory):
    """Retain refusals before normal, exceptional or abandoned-fixture cleanup."""

    def cleanup(self):
        _retain_refusals(self.name)
        super().cleanup()

    @classmethod
    def _cleanup(cls, name, warn_message, **kwargs):
        _retain_refusals(name)
        super()._cleanup(name, warn_message, **kwargs)
