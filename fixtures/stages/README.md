# Original stage-presence fixtures

These hand-authored native files were created for AGENTEE-48/88. They contain
no donor design or stock symbol/footprint graphics. The expected component
inventory is exactly R1 and C1.

| Snapshot | Schematic | Board | Expected presence gate |
| --- | --- | --- | --- |
| Empty | empty.kicad_sch | empty.kicad_pcb | Missing R1 and C1 symbols and footprints |
| Schematic | schematic.kicad_sch | empty.kicad_pcb | Symbols present, footprints absent |
| Placed | schematic.kicad_sch | placed.kicad_pcb | Symbols and footprints present, no copper |
| Routed | schematic.kicad_sch | routed.kicad_pcb | Same inventory, one 0.25 mm OUT segment |

These snapshots qualify bounded native Reference extraction and campaign
orchestration. They are deliberately incomplete electrical designs: the schematic
pins are unconnected; board IN/GND pads have no assigned nets, there is no outline,
no native footprint-library closure and no schematic/board UUID association.
The routed snapshot is a copper-presence fixture, not a completed RC design.
Neither presence nor the file name establishes ERC, DRC, parity, simulation,
placement suitability or fabrication acceptance. The full native RC rung remains
unmet. Tests with scripted handlers and a synthetic measurement producer are
explicitly orchestration tests; they do not replace these missing checks.
