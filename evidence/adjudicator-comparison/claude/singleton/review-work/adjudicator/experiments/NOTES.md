# Adjudication: challenger/one (connector contract mismatch)

## Commands
- `python3 -I /work/experiments/verify_j1_contract.py` (output: verify_j1_contract.out)
- Inputs read: /candidate/project/rc_filter.kicad_pcb (read-only), /evidence/work/netlist/design.xml
- Tooling: python3 (stdlib re only); kicad evidence produced by KiCad 10.0.7 per packet.

## Requirement (inputs/requirements.txt)
"J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground. Verify saved connectivity."

## Observed saved connectivity (both domains agree)
- Schematic netlist (work/netlist/design.xml, nets section):
  /IN  = {J1.1, R1.1}
  /OUT = {C1.1, J1.2, R1.2}
  GND  = {C1.2, J1.3}
- Saved board (inputs/project/rc_filter.kicad_pcb J1 pad net properties;
  corroborated by work/geometry/geometry.json J1 pad entries):
  J1 pad 1 -> /IN, pad 2 -> /OUT, pad 3 -> GND

## Electrical topology analysis
R1 is the series element; C1 shunts to GND. The filter input node is the one
connected only to R1 (one R1 pin, no C1) = /IN. The filtered output node is the
R1–C1 junction = /OUT (R1.2 + C1.1, C1.2 to GND).
- Contract requires: J1 pin 2 = input node, J1 pin 1 = filtered output node.
- Saved design has: J1 pin 1 = input node (/IN), J1 pin 2 = filtered output (/OUT).
Pins 1 and 2 are swapped relative to the declared contract. Pin 3 = GND is correct.

No alternate interpretation satisfies the contract: driving pin 2 (/OUT, the R–C
junction) places C1 directly across the source and takes the "output" through R1
from the driven node — not a low-pass filtered output at pin 1.

## Why native checks pass
ERC, DRC with schematic parity, and geometry all pass because schematic and board
are internally self-consistent; the defect is a mismatch against the external
requirements.txt contract, which no native rule encodes. Passing ERC/DRC is not
counter-evidence.

## Conclusion
challenger/one is an established defect (major): saved connectivity violates the
declared external connector contract (pins 1/2 swapped).

## Limitations
- Regex extraction of S-expression/XML; cross-checked against packet geometry.json
  and connectivity.json, all consistent.
