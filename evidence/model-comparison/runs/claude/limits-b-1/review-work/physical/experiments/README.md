# Physical review experiments — run 6348bf9f (candidate b965f04e...)

Date: 2026-10-16. Tools: see versions.txt (ngspice, kicad-cli/pcbnew 10.0.7, python3).

## Inputs verified
sha256 of /candidate/project/rc_filter.kicad_sch, rc_filter.kicad_pcb, requirements.txt
match the packet artifact hashes (checked with sha256sum).

## 1. R1 power under exercise condition (r1_power_calc.txt, r1_power.cir, r1_power_ngspice.log)
Condition (inputs/requirements.txt): /IN held at 48 V DC, /OUT clamped to 0 V
indefinitely by external equipment; R1 continuous power limit 0.1 W.
Topology (work/netlist/design.xml): R1 pin1 on /IN, pin2 on /OUT; C1 across /OUT-GND.
With OUT clamped at 0 V, full 48 V appears across R1 = 10 kOhm:
  I = 48/10k = 4.8 mA;  P = 48^2/10k = 230.4 mW = 2.304 x 0.1 W limit.
ngspice .op confirms i = 4.8 mA, source power 0.2304 W (all in R1; C1 carries no DC;
clamp source absorbs nothing at 0 V). RESULT: R1 limit exceeded by 130%, continuously.
Any R1 >= 23.04 kOhm would meet 0.1 W at 48 V (informational only).

## 2. Board connectivity (board_connectivity.txt, zone_layer_check.txt)
Loaded the saved board in pcbnew 10.0.7, rebuilt connectivity from saved zone fill:
0 unconnected items. Pad nets match schematic netlist (R1.1=/IN, R1.2=/OUT,
C1.1=/OUT, C1.2=GND, J1.1/2/3=/IN//OUT/GND). Clamp current path is
J1.1 -> 0.25mm F.Cu track -> R1 -> 0.25mm F.Cu tracks -> J1.2; 4.8 mA DC.
GND zone is on B.Cu only and is filled; C1.2 reaches J1.3 via track + 0.6/0.3 via +
B.Cu zone (no current flows in GND under this exercise condition since C1 blocks DC
and the clamp return is external).

## Limitations
- No thermal model of the 0805 footprint/board was built; the finding rests on the
  stated 0.1 W continuous limit, not on a derating or temperature calculation.
- Zone fill evaluated as saved (candidate is read-only; no refill pass).
