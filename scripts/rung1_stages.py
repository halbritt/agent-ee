#!/usr/bin/env python3
"""Rung-1 stage-ordered driver (AGENTEE-104): no inference, no review profile.

    scripts/rung1_stages.py run --out RUNDIR --reference fixtures/rc/control.scope.json
    scripts/rung1_stages.py run --out RUNDIR --spec fixtures/rung1/spec.md
    scripts/rung1_stages.py run --out RUNDIR --requirement-record
    scripts/rung1_stages.py verify RUNDIR

See docs/rung1-stages.md.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_ee.rung1_stages import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
