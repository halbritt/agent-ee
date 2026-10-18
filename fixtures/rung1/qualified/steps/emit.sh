#!/bin/bash
# Rung-1 qualified step `emit-schematic` (stage schematic): the owned AGENTEE-40 emitter,
# unchanged, on the captured connectivity intent, layout and symbol captures. Run by the
# campaign coordinator through stage_receipts.run_step; reads /candidate, writes /work/out.
set -euo pipefail
export PYTHONPATH=/tool/agent-ee PYTHONDONTWRITEBYTECODE=1
mkdir -p /work/out
exec /usr/bin/python3 -B -m agent_ee.schematic_emitter /candidate/generation/intent.json \
  --layout /candidate/generation/layout.json --captures /candidate/stock/symbols \
  --out /work/out/sch --receipt /work/out/emit-receipt.json
