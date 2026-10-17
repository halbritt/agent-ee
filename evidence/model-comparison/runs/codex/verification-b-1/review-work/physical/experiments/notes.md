# Physical review record

Candidate: 7a48855eff011bbbb0e59bd670ca885cfd820dde585082a2f1a4afd688b35174
Run: 833f2a36-b218-43be-93ec-ee001825e3a7
Packet: 4a23740266f405da87b8da6c322a3a2c6518ff669b385dac3fcca9122dbd14a1

## Reproduction and provenance

Run `/usr/bin/python3 /work/experiments/review.py > /work/experiments/review.stdout 2> /work/experiments/review.stderr`.
`commands.log` records the subprocess commands, versions, return codes, stdout and stderr.
`read_manifest.json` records hashes of original native files and supplied records read.
Candidate PCB hash matches packet f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de;
schematic hash matches ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b.
No candidate file was modified, saved, or refilled. All generated outputs are review experiments.
No network references were needed. No credentials or prior reviewer experiments were read.

Initial script run stopped on a pcbnew Python binding mismatch: GetParent() exposes
BOARD_ITEM_CONTAINER, so pad names were changed to use GetParentFootprint(). The first
attempt stderr and commands are preserved. This is an experiment issue, not a design defect.
A later inline diagnostic had an unmatched parenthesis and did not execute; its intended
annulus samples were incorporated into review.py. A via GetWidth() warning was removed by
passing F.Cu explicitly; saved native via diameter remains 0.6 mm. pcbnew emits the same
three PROPERTY_ENUM assertions seen in the supplied geometry console but returns geometry.
Neither these diagnostics nor successful execution is treated as electrical evidence alone.

## Native physical observations

Native PCB inputs/project/rc_filter.kicad_pcb lines 587-613 assigns J1.1=/IN,
J1.2=/OUT and J1.3=GND, consistent with native XML netlist export.
Lines 627-665 form a closed 20 by 12 mm rectangular board outline. Native footprint
courtyards are present, separated and within the board. Generic footprints are not actual
component procurement specifications.

Lines 667-714 show J1.1 to R1.1, R1.2 to C1.1, a J1.2 branch joining that output
track at (107.5,100), and C1.2 to the GND via at (113,100). The C1 ground trace is
2.05 mm center to center. The output path from C1.1 to J1.2 is 1.55+2.54+7.5=11.59 mm.
No electrical failure is inferred from these lengths or widths.

The single B.Cu filled polygon (lines 715-785, UUID 38a83012-bcf9-4bb9-b9f4-50d49cd91d90)
contains the GND via position. Native polygon point tests show saved fill within J1.3's
annulus on four diagonal thermal spokes (radius 0.8 mm, angles 45, 135, 225, 315 degrees),
as expected from its native contour. Axial test points are voids in the thermal relief.
This, direct pad/track contact extraction, and the native DRC report support the saved
return path C1.2 -> front trace -> via -> back fill -> J1.3; there is no established open.
GetConnectedPads() returns directly touching pads, not transitive graph reachability:
empty direct pad lists do not mean the routed nets are open.

Fresh native DRC with schematic parity has empty violations, unconnected_items and
schematic_parity arrays (`drc.json`). The supplied work/drc/drc.json agrees.
Supplied work/erc/erc.json has no violations. Its ignored checks and DRC ignored checks
were reviewed; they do not verify transient behavior or fabricator-specific acceptance.

## Criterion and verification coverage

inputs/project/rc_filter.kicad_sch line 955: C1=100n; line 1274: R1=10k.
Only C1, R1, J1 appear in native export; no ADC load or switching timing is described.
The connector is excluded from schematic simulation (line 1096). This does not mean a
real attached ADC is open circuit; it is an explicit boundary of any bare RC model.
The supplied checks are ERC, DRC, netlist and geometry extraction, not loaded transients.

`calculations.json` states assumptions, nominal equations, results and switched-load
equations without choosing ADC parameters. Under an ideal 0-to-1 V step, zero initial
C1 voltage and no ADC load, tau=1 ms and Vout(1 ms)=1-exp(-1)=0.632120559 V.
The margins are 12.1206 mV to the lower bound and 17.8794 mV to the upper bound.
The same assumptions allow tau between 0.952542 and 1.033502 ms; these are calculated
sensitivity limits, not required component tolerances or evidence that actual parts fail.

`baseline.cir` uses the native nominal values, zero operating-point voltage, a step
delayed by 1 us with a 1 ns rise, and samples 1 ms after the rise midpoint. A 0.1 us
maximum timestep gives ngspice v_out_1ms=0.6321206 V (`commands.log`). This validates the
unloaded calculation only. It does not establish the specified switched-ADC result.

With the switch closed the output and ADC sampling capacitor have coupled state
equations dependent on Cs, Ron, initial charge and timing. The fast charge-sharing limit
Vafter=(C1*Vbefore+Cs*Vs_initial)/(C1+Cs) also makes that dependence explicit. No numerical
ADC model was invented. Neither pass nor failure of the ADC-connected criterion is
established. This is a coverage concern, not an established design defect.

The ideal unloaded step initially gives R1 current 100 uA, power 100 uW and total
resistor step energy 50 nJ. Those conditional numbers do not bound unspecified ADC
switching currents and do not establish actual resistor ratings, copper ampacity or
thermal performance.

## Explicit coverage omissions

- No loaded ADC transient, acquisition/repeated sampling sequence, prior-channel charge,
  switch charge injection or ADC measurement: sampling parameters are unspecified.
- No component tolerance, temperature, voltage coefficient, leakage or parasitic corner
  analysis; specific parts and such parameters are absent. No resulting failure alleged.
- No PCB parasitic extraction or fast-switch return impedance measurement, source/cable
  model, hardware measurement, or thermal measurement.
- No zone refill comparison; saved copper only.
- No fabricator-specific minimum-feature, stackup/copper thickness, solder process,
  assembly, mechanical mating or production-output acceptance qualification. Native
  geometry and enabled DRC rules were checked; no unstated fabrication rules imposed.
- ERC was inspected from the supplied fresh native report rather than independently
  rerun. Ignored native checks remain outside reported rule coverage.

Conclusion: no established physical or native-rule defect found. The unloaded nominal
baseline meets the point-in-time band. The specified ADC-connected response is undetermined.
