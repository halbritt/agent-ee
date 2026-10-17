# Circuit review notes — RC low-pass fixture (run e6a69ca8)
Date: 2026-10-16. Tools: kicad-cli 10.0.7, ngspice (batch), python3 -I.

## Scope criterion
"A fitted resistive DC path must connect /IN to /OUT" (inputs/requirements.txt).
Only this criterion plus native rule violations are evaluated; no ratings imposed.

## Input integrity
sha256 of /candidate/project/rc_filter.kicad_sch, rc_filter.kicad_pcb, requirements.txt
match packet artifact hashes exactly (hashes.txt).

## Population (population_check.txt)
Schematic: R1 Device:R value=10k, dnp=no, in_bom=yes (also C1 100n and J1 fitted; no DNP anywhere).
Board: R1 footprint Resistor_SMD:R_0805_2012Metric, attr=smd only (no dnp/exclude attrs),
pad1 net /IN, pad2 net /OUT. Board path uuid /c5c38e1a-... matches schematic R1 uuid.

## Netlist
Packet work/netlist/design.xml: /IN = {J1.1, R1.1}; /OUT = {C1.1, J1.2, R1.2}; GND = {C1.2, J1.3}.
So the fitted 10k resistor R1 is the resistive element directly bridging /IN and /OUT. Criterion met.

## Copper continuity (copper_continuity.txt)
/IN: F.Cu track (100,100)-(104.0875,100) spans J1 pad1 center to R1 pad1 center exactly.
/OUT: track (105.9125,100)-(109.05,100) spans R1 pad2 to C1 pad1; J1 pad2 route
(100,102.54)->(107.5,102.54)->(107.5,100) terminates at x=107.5 which lies on the
collinear /OUT track interval [105.9125,109.05] at y=100 -> continuous copper.
GND: C1 pad2 -> track -> via (113,100) -> filled B.Cu zone covering J1 pad3; DRC confirms 0 unconnected.

## Electrical check (rc.cir, ngspice_op.log)
DC operating point, VIN=1V at /IN, R1=10k to /OUT, C1=100n to GND:
v(out)=1.000000e+00, i(vin)=0 (no DC load) -> DC conduction through R1 confirmed;
behaves as first-order RC low-pass (fc = 1/(2*pi*10k*100n) ~ 159 Hz, not a stated requirement).

## Native check reproduction (rerun/erc.json, rerun/drc.json)
ERC: 0 violations. DRC (--schematic-parity): 0 violations, 0 unconnected, 0 parity issues.
Matches packet work/erc and work/drc results (kicad-cli 10.0.7).

## Non-issues examined
- J1 exclude_from_sim=yes: connector only; no effect on assembly or connectivity.
- Geometry probe stderr PROPERTY_ENUM asserts (work/geometry/console.log): tool-side wxWidgets
  debug asserts, exit 0, output complete; not a design signal.
- Local labels IN/OUT (not global): single sheet, net names /IN,/OUT consistent sch<->pcb.
- Footprint filters all satisfied (R_* / C_* / Connector*:*_1x??_*).

## Limitations
- Zone fill accepted as saved (read-only candidate; no refill pass), consistent with packet limitation.
- No fabrication outputs / stackup review attempted; not in scope.
- ngspice model is ideal R/C from schematic values; no parasitics (none specified).
