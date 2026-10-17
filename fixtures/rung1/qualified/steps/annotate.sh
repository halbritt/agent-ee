#!/bin/bash
# Rung-1 qualified step `forward-annotate` (stage schematic): the native XML netlist of the
# emitted schematic, then the owned AGENTEE-52 forward annotation (plan, then apply to a new
# board) against the captured footprint manifest. Footprints land on its staging column;
# placement is the placement stage's job. Reads /candidate only, writes /work only.
set -euo pipefail
name="$1"
export PYTHONPATH=/tool/agent-ee PYTHONDONTWRITEBYTECODE=1
mkdir -p /work/out/board /work/kicad-config/10.0
cp /candidate/stock/template/sym-lib-table /candidate/stock/template/fp-lib-table /work/kicad-config/10.0/
# Read-only source: kicad-cli cannot leave a .kicad_prl in the design copy.
kicad-cli sch export netlist --format kicadxml -o /work/out/netlist.xml "/candidate/project/$name.kicad_sch"
/usr/bin/python3 -B -m agent_ee.forward_annotation plan "/candidate/project/$name.kicad_pcb" \
  --netlist /work/out/netlist.xml --manifest /candidate/generation/footprints.json \
  --captures /candidate/stock/footprints --work /work/fa-plan --record /work/out/plan.json
exec /usr/bin/python3 -B -m agent_ee.forward_annotation apply "/candidate/project/$name.kicad_pcb" \
  --netlist /work/out/netlist.xml --manifest /candidate/generation/footprints.json \
  --captures /candidate/stock/footprints --plan /work/out/plan.json \
  --out "/work/out/board/$name.kicad_pcb" --work /work/fa-apply --record /work/out/apply.json
