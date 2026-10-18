"""Rung-1 step `write-scaffold`: the empty native project from the immutable generation inputs.

Run by both rung-1 drivers through stage_receipts.run_step before the campaign exists
(networkless sandbox, /candidate read-only, /work writable), in place: it reads the captured
agent-ee.envelope/1 and the emitter's root-sheet source at their candidate paths. The empty
schematic, board and project come from agent_ee.scaffold.native_files. The fixture project
is mounted read-only at /tool/template. Arguments are passed through unchanged.
"""
import sys

sys.path.insert(0, '/tool/agent-ee')
from agent_ee.rung1_campaign import scaffold_main  # noqa: E402

raise SystemExit(scaffold_main(sys.argv[1:]))
