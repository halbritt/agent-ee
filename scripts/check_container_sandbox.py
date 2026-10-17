#!/usr/bin/env python3
"""Exercise the production networkless executor before expensive CI checks."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_ee.executor import execute


with tempfile.TemporaryDirectory(prefix='agent-ee-sandbox-probe-') as tmp:
    root = Path(tmp)
    inputs = root / 'inputs'
    inputs.mkdir()
    record = execute(inputs, root / 'work', ['/usr/bin/true'], timeout=15)
    print(json.dumps(record, indent=2))
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        print((root / 'work/console.log').read_text(), file=sys.stderr)
        raise SystemExit(1)
