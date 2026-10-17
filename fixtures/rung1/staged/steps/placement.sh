#!/bin/bash
# Rung-1 campaign step `build-board`: native XML netlist of the emitted schematic, then
# agent_ee.board_build with the unqualified row stand-in (AGENTEE-109). Run by the campaign
# coordinator through stage_receipts.run_step; reads only /candidate, writes only /work/out.
set -euo pipefail
name="$1"
export PYTHONPATH=/tool/agent-ee
mkdir -p /work/out /work/kicad-config/10.0
cp /candidate/stock/template/sym-lib-table /candidate/stock/template/fp-lib-table /work/kicad-config/10.0/
# Read-only source: kicad-cli cannot leave a .kicad_prl in the design copy.
kicad-cli sch export netlist --format kicadxml -o /work/out/netlist.xml "/candidate/project/$name.kicad_sch"
exec /usr/bin/python3 -B -P -m agent_ee.board_build /candidate/project \
  --netlist /work/out/netlist.xml --footprints /candidate/stock/footprints \
  --envelope /candidate/generation/envelope.json \
  --template-board "/candidate/generation/template/$name.kicad_pcb" \
  --placement-request /candidate/generation/placement-request.json \
  --placement-fallback row --out /work/out/board
