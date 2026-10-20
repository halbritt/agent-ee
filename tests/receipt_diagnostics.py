"""Keep refused setup receipts outside disposable test fixtures for diagnosis."""

import shutil
import uuid
from pathlib import Path


def retain_refusal(receipt_dir, receipt):
    if receipt['status'] != 'refused':
        return
    target = Path(__file__).resolve().parents[1] / 'build/refused-step-receipts' / str(uuid.uuid4())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(receipt_dir, target, symlinks=True)
    raise AssertionError(f"step refused; violations {receipt['violations']}; receipt kept at {target / 'receipt.json'}")
