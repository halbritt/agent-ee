"""Rung-1 step `write-scaffold`: the empty native project from the immutable generation inputs.

Run by the rung-1 driver through stage_receipts.run_step before the campaign exists
(networkless sandbox, /candidate read-only, /work writable). The fixture project is
mounted read-only at /tool/template. Arguments are passed through unchanged.
"""
import sys

sys.path.insert(0, '/tool/agent-ee')
from agent_ee.rung1_campaign import scaffold_main  # noqa: E402

raise SystemExit(scaffold_main(sys.argv[1:]))
