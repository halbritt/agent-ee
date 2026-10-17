Commands and review notes
=========================

All candidate access was read-only. No credential files were read. No candidate
modifications or scratch design changes were made. No external sources were
needed: resistance is native, and the power limit is stipulated by the exercise.

Initial inspection commands:
- `pwd`
- `rg --files -g AGENTS.md -g '!home/**' /work /candidate /evidence`
  (no AGENTS.md found; exit 1 means no search matches)
- `ls -la /work /candidate /evidence/work /evidence/checks`
- `cat /candidate/requirements.txt /evidence/work/erc/erc.json /evidence/work/drc/drc.json /evidence/checks/erc.json /evidence/checks/drc.json`
- `rg -n 'Reference" "R1|Value" "10k|label "IN|label "OUT|86\.36|pad "[12]"|net "/IN|net "/OUT' /candidate/project/rc_filter.kicad_sch /candidate/project/rc_filter.kicad_pcb`
- `python3 --version` -> Python 3.12.3
- `kicad-cli version` -> 10.0.7

Reproducible calculation/record inspection:
`python3 /work/experiments/r1_dc_review.py`
See r1_dc_review.py for the read-only parser, assertions and exact inputs, and
r1_dc_results.json for extracted connectivity, native hashes, calculation,
supplied command records, outcomes, assumptions and limitations.

Useful native locators:
- inputs/requirements.txt:1 sets both terminal voltages and continuous limit.
- inputs/project/rc_filter.kicad_sch:1252 onward: R1 instance, value at line 1274.
  Its Device:R library pin definitions place pin 1 above, pin 2 below at angle 0.
  Wires at lines 725 and 775 connect these pins to IN/OUT labels at 815/845.
- inputs/project/rc_filter.kicad_pcb:245 identifies R1; value at 256;
  pads 1 and 2 at 359/367 carry /IN and /OUT respectively.
- work/netlist/design.xml: components/comp[@ref='R1']/value and nets/net/node.
- work/erc/erc.json: sheets[0].violations is empty; ignored_checks is explicit.
- work/drc/drc.json: violations, unconnected_items, schematic_parity are empty;
  ignored_checks is explicit. Saved fill only, per checks/drc.json.
- work/geometry/geometry.json: footprints[ref=R1] corroborates R1 value/pads;
  work/geometry/console.log records PROPERTY_ENUM assertions.

Outcome: 12 V across 10,000 ohms gives 1.2 mA and 0.0144 W continuously.
This is 14.4% of the stipulated 0.1 W limit (0.0856 W below it). C1 and the
external clamp do not remove that steady current; it remains below the limit.
There is no evidence-supported circuit defect under the stated condition.
ERC/DRC reports are structural/rule evidence, not a power measurement. No
simulation or physical thermal experiment was performed or claimed.
