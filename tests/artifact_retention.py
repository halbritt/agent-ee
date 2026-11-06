"""Opt-in test evidence capture before the test's temporary workspace is removed."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def retain(test_id, payload=None, roots=()):
    destination = os.environ.get('AGENT_EE_EVIDENCE_DIR')
    if not destination:
        return None
    evidence = Path(destination).resolve()
    for source in roots:
        if evidence.is_relative_to(Path(source).resolve()):
            raise ValueError('test evidence destination must be outside the captured workspace')
    parent = evidence / 'tests' / hashlib.sha256(test_id.encode()).hexdigest()[:16]
    parent.mkdir(parents=True, exist_ok=True)
    saved = Path(tempfile.mkdtemp(prefix='capture-', dir=parent))
    record = {'schema': 'agent-ee.test-artifacts/1', 'test_id': test_id,
              'captured_at': datetime.now(timezone.utc).isoformat(), 'payload': payload, 'roots': []}
    receipt = os.environ.get('AGENT_EE_RUN_INPUT_RECEIPT')
    if receipt:
        raw = Path(receipt).read_bytes()
        (saved / 'run-inputs.json').write_bytes(raw)
        record['run_input_receipt_sha256'] = hashlib.sha256(raw).hexdigest()
    for index, source in enumerate(roots):
        source = Path(source)
        row = {'source': str(source), 'retained': f'root-{index}', 'files': {}}
        target = saved / row['retained']
        if source.is_symlink():
            row['source_link'] = os.readlink(source)
        elif not source.exists():
            row['missing'] = True
        else:
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
                entries = sorted(target.rglob('*'))
            else:
                shutil.copyfile(source, target, follow_symlinks=False)
                entries = [target]
            for path in entries:
                name = path.relative_to(target).as_posix() if path != target else '.'
                if path.is_symlink():
                    row['files'][name] = {'symlink': os.readlink(path)}
                elif path.is_file():
                    row['files'][name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                          'bytes': path.stat().st_size}
        record['roots'].append(row)
    (saved / 'evidence.json').write_text(json.dumps(record, indent=2, allow_nan=False) + '\n')
    return saved


class RetainedTemporaryDirectory(tempfile.TemporaryDirectory):
    def __init__(self, test_id, **kwargs):
        self.test_id = test_id
        self.retained = False
        super().__init__(**kwargs)

    def cleanup(self):
        try:
            if not self.retained:
                self.retained = True  # A failed capture must not be retried by cleanup.
                retain(self.test_id, roots=[Path(self.name)])
        finally:
            super().cleanup()
