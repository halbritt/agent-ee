"""Keep refused setup receipts outside disposable test fixtures for diagnosis."""

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path


RETENTION_ROOT = Path(__file__).resolve().parents[1] / 'build/refused-step-receipts'


def _copy_receipt(receipt_dir):
    target = RETENTION_ROOT / str(uuid.uuid4())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(receipt_dir, target, symlinks=True)
    return target


def retain_refusal(receipt_dir, receipt):
    if receipt['status'] != 'refused':
        return
    target = _copy_receipt(receipt_dir)
    raise AssertionError(f"step refused; violations {receipt['violations']}; receipt kept at {target / 'receipt.json'}")


def _retain_refusals(root):
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
            target = _copy_receipt(path.parent)
            print(f"step refused; violations {receipt['violations']}; receipt kept at {target / 'receipt.json'}",
                  file=sys.stderr)


class ReceiptTemporaryDirectory(tempfile.TemporaryDirectory):
    """Retain refusals before normal, exceptional or abandoned-fixture cleanup."""

    def cleanup(self):
        _retain_refusals(self.name)
        super().cleanup()

    @classmethod
    def _cleanup(cls, name, warn_message, **kwargs):
        _retain_refusals(name)
        super()._cleanup(name, warn_message, **kwargs)
