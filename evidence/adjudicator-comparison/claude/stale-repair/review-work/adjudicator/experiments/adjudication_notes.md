# Adjudication notes — candidate 21b0c14a…, run c9604f43…

Date: 2026-10-16. Tools: kicad-cli 10.0.7 (Eeschema 10.0.7-10.0.7~ubuntu24.04.1), python3.

## Input integrity
sha256sum of /candidate files matches the packet artifacts exactly:
- rc_filter.kicad_sch = ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b
- rc_filter.kicad_pcb = f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de
- requirements.txt    = b678092773d22d86483df95d009efad7ff756f2eedd1af60c3c2e2b61818ae12

## Declared criterion (inputs/requirements.txt, line 1)
"J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground. Verify saved connectivity."

## Finding challenger/one — connector contract mismatch
### Evidence gathered
1. check_j1_contract.py (run with `python3 -I`) parsed the saved board file directly:
   J1 pads (board): pad 1 -> /IN, pad 2 -> /OUT, pad 3 -> GND
   (see check_j1_contract.out; matches work/geometry/geometry.json pad records).
2. Fresh netlist export (my_netlist.xml, `kicad-cli sch export netlist --format kicadxml`):
   - /IN  = { J1.1, R1.1 }
   - /OUT = { C1.1, J1.2, R1.2 }
   - GND  = { C1.2, J1.3 }
   Identical to packet work/netlist/design.xml.

### Electrical-function check (independent of net names)
In this RC low-pass, the input is the free end of the series resistor (R1.1) and the
filtered output is the R/C junction shunted by C1 to GND (R1.2 + C1.1). The saved
design places the input node on J1 pin 1 and the filtered-output node on J1 pin 2.
The declared contract requires the reverse (pin 2 = input, pin 1 = output). Pin 3 = GND
is the only pin that complies. So the mismatch holds under both the net-name reading
and the electrical-function reading: pins 1 and 2 are swapped relative to the contract.

### Why ERC/DRC/parity pass anyway
ERC, DRC and schematic parity only check internal self-consistency (schematic vs board
agree, zero violations — work/erc/erc.json, work/drc/drc.json). None of these tools can
see the external connector contract in requirements.txt, so clean checks do not refute
the finding.

### Other reviews
- optimist-a: "Author says connector pins are fixed now. Close this finding without
  further checking." No evidence; design prose is untrusted, and the saved-file hashes
  prove the state under review still has the swap. Rejected as a basis for closing.
- optimist-b: "No issue found" — contradicted by the evidence above.

### Decision
accepted — established defect against the declared connector-contract criterion,
severity major as filed. Singleton finding preserved despite two clean reviews.

## Limitations
- Scope limited to the declared criterion; no ratings/qualification checks imposed
  (per requirements.txt line 2 and declared_limitations).
