# Circuit review record

Invocation: `python3 /work/experiments/review.py`. Reproducible audit code is in review.py; subprocess commands, exit codes and version outputs are in commands.log. Initial inspection used Python pathlib reads of the requirements, RC check record, deck/control/log and native ERC/DRC JSON reports. Review artifacts only were written under /work/experiments. Candidate files were not edited or refilled. No external sources or part models were needed.

## Conditions and outcomes

The governing conditions are an ideal unloaded 1 V input step and a 0.62–0.65 V output band at 1 ms (inputs/requirements/fixture-requirements.md, requirement 1). All five copper track segments must be at least 0.20 mm wide (requirement 2).

Embedded native symbol pin positions, symbol rotation and wire/label connections independently give J1.1=R1.1=/IN, J1.2=R1.2=C1.1=/OUT, and J1.3=C1.2=GND. R1 is Device:R, 10k, populated; C1 is Device:C, 100n, populated. Fresh native XML export and supplied work/netlist/design.xml agree with these connections. Board pad names, net assignments and component values also agree. See audit.json, fresh-netlist.xml and native-locators.json.

The generated check deck correctly retains R1 between input and output and C1 from output to ground, and excludes only the connector from element modeling. It uses a 1 ns, 0-to-1 V linear rise with zero initial capacitor voltage, 5 us requested transient increment and a 5 ms stop. There is no load, active device or substituted behavioral model. The control script runs the sourced deck and writes a raw trace. work/rc-step/ngspice.log contains actual measurements, not merely a successful tool exit.

Independently decoded work/rc-step/result.raw contains 1025 real points with time, v(in), v(out) and source current. Interpolated Vout at 1 ms is 0.6321201340 V, within the required band; at 5 ms it is 0.9932621197 V. The exact ideal step formula, Vout=1-exp(-t/(R*C)), with R*C=0.001 s gives 0.6321205588 V and 0.9932620530 V respectively. The 1 ms trace discrepancy is about -0.425 microvolt. A separate ngspice-42 deck with a 100 us pre-step hold and 0.5 us maximum step gives 0.6321204 V at 1 ms after the step and 0.9932621 V at 5 ms. This is a scratch verification experiment, not a candidate modification. See independent.cir, independent.log and audit.json. The 5 ms sample has no declared pass/fail bounds.

Direct native board parsing finds five segment records, all 0.25 mm wide, plus one ground via and one saved ground zone. This agrees with work/geometry/geometry.json. The native geometry console contains PCB API assertions, but the directly parsed native segment widths independently support the width result. No electrical violation is inferred from those diagnostics.

The native work/erc/erc.json report has zero violations on the one sheet; work/drc/drc.json has zero violations, unconnected items or parity issues. These reports and checks/*.json were inspected; ERC and DRC were not rerun. Their ignored checks are recorded verbatim in check-records-reviewed.json. Native pin and board pad matching plus analytical and waveform checks are used for circuit behavior; ERC/DRC exit codes are not treated as behavioral proof.

## Limitations and omissions

Only nominal ideal unloaded behavior was tested. Tolerance, temperature, DC bias dependence, parasitics, external loading/source impedance, ADC sampling, EMI, physical component ratings and hardware behavior were not established. No physical part numbers or ratings were supplied. Reset and brownout logic are absent; shutdown and arbitrary initial-charge behavior have no stated acceptance criteria and were not simulated. Saved zone fill was not recalculated; actual copper connectivity relies on the supplied saved-board DRC rather than an independent polygon connectivity solver. The ignored ERC/DRC categories were not enabled or exhaustively assessed. No manufacturing, assembly or hardware test was conducted.

No evidence-supported circuit defect or additional concern was identified within the captured requirements.
