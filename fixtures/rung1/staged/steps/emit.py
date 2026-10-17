"""Rung-1 campaign step `emit-schematic`: agent_ee.schematic_emit from the read-only /tool/agent-ee mount.

Run by the campaign coordinator through stage_receipts.run_step (networkless sandbox,
/candidate read-only, /work writable). Arguments are passed through unchanged.
"""
import sys

sys.path.insert(0, '/tool/agent-ee')
from agent_ee.schematic_emit import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
