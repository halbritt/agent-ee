# Circuit review experiments

Scope: saved disposable passive RC fixture; only its declared ideal unloaded
1 V rising-step requirement and 0.20 mm track-width requirement were evaluated.
Candidate files were read only. No candidate edits, board refill, or proposed
replacement-component simulation were performed. No network reference was
needed for the elementary ideal RC calculation.

## Commands and versions

- Initial inspection: `pwd`, `rg --files -g AGENTS.md -g '!home/**' /work /candidate /evidence`,
  and `ls -la /candidate/project /candidate/requirements /evidence/work /evidence/checks /work/experiments`.
  No AGENTS.md was found in that search. No credentials were read.
- Read requirements, project settings, supplied deck/control/log, ERC/DRC reports,
  and rc-step check with `cat`; used `rg -n` to locate native references, values,
  wires, labels, UUIDs, pads, nets and track widths.
- `python3 /work/experiments/audit.py` completed successfully. The script records
  each subprocess argv and exit code in commands.json, with captured consoles.
  Version outputs are in the three `*-version.console.txt` files.
- A subsequent Python standard-library read of each `/evidence/checks/*.json`
  and corresponding execution.json saved commands, exits and outcomes in
  check-records.json; geometry observations were independently corroborated
  against the saved native board.

## Methods and observations

The audit script parses the entire native schematic and board S-expressions,
extracts instantiated component identities/values and board pad nets, and
exports a fresh native XML netlist. Every populated pad net and passive value
agrees with the fresh and supplied exports. J1 pins 1, 2, 3 are /IN, /OUT, GND;
R1 connects /IN to /OUT, and C1 connects /OUT to GND. Generic resistor and
unpolarized capacitor symbols require no directional passive model here.
Hashes in audit-results.json match the packet's three cited original inputs.

R1=100000 ohm, C1=1e-7 F. With the input initially zero, an ideal unloaded 1 V
step gives tau=0.010 s and Vout(t)=1-exp(-t/tau) V. At 1 ms this is
0.095162581964 V, failing the 0.62–0.65 V requirement. At 5 ms it is
0.393469340287 V; no 5 ms acceptance limit was supplied. The 1 ms requirement
permits tau approximately 0.952542–1.033502 ms, far below the native 10 ms.

The supplied deck correctly uses native values and topology. Its 0-to-1 V
PWL edge takes 1 ns; the initial operating point and raw waveform start at
zero. Its measurements are referenced to 1 ms and 5 ms from edge onset.
Direct binary waveform interpolation reproduces 0.095162513912 V at 1 ms,
with Vin=1 V. Thus the violation is electrical behavior, not a failed tool.

The independent deck uses a 1 ms pre-step interval, 1 ns rise, tighter numerical
tolerances, 1 us maximum timestep, and measures 1 ms/5 ms after edge onset.
Ngspice reports 0.09516254 V and 0.3934693 V, consistent with the analytical
solution and supplied results. This is a different stimulus timing of the
same stated test, without changing the candidate values.

All five native copper track segments are 0.25 mm wide. There are no native
track arcs. The supplied geometry report agrees despite PROPERTY_ENUM assert
messages in its console; those messages do not establish a geometry violation.
Supplied ERC and DRC reports have empty violation arrays, DRC has empty
unconnected/parity arrays. These checks cannot establish transient compliance.

## Coverage omissions and limitations

- No tolerance, temperature, load, ADC sampling, EMI, parasitic or hardware
  validation; no manufacturer part numbers or ratings were assumed.
- Only an initially discharged ideal rising step was assessed; falling steps,
  other initial states, shutdown and brownout were not tested. There is no
  reset or powered control circuitry in the stated passive fixture.
- ERC/DRC were inspected from fresh supplied native outputs, not rerun.
  Their configured ignored checks remain outside those reports' coverage:
  ERC single_global_label, four_way_junction, simulation_model_issue,
  footprint_filter; DRC missing_courtyard, track_not_centered_on_via,
  tuning_profile_track_geometries, footprint_filters_mismatch,
  footprint_type_mismatch.
- Saved zone fill only; no refill, fabrication review, ampacity guarantee or
  exhaustive independent clearance/connectivity solver was attempted.
- Binary raw decoding assumes little-endian IEEE 64-bit values, checks the
  declared point/variable count and data length, and agrees with both logged
  measurements and the independent analytical result.
