# Circuit review experiment record

Scope: inputs/requirements.txt line 1 only: C1 initially 1 V, /IN grounded at t=0, no external load, ideal passive components; /OUT <= 0.01 V at 5 ms.

## Reproduction and tools

Command: `python3 /work/experiments/review.py` (exit 0). Script is review-work/circuit/experiments/review.py. All subprocess argument vectors, exit statuses, stdout, stderr and versions are in review-work/circuit/experiments/commands.json. Tools: Python 3.12.3, KiCad CLI 10.0.7, ngspice 42. No references were fetched; no candidate files were modified. Parsed only design/check artifacts, not credentials or other reviewers' experiments.

The script parses the original native schematic and board s-expressions, checks component values, exports a fresh native XML netlist, compares its terminal mapping with supplied connectivity, and compares board pad assignments/values with that netlist. Native paths and SHA-256 values plus UUIDs and coordinates are in native_summary.json. Fresh export is independent.xml. R1 native UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78; C1 UUID 1dcaf7b4-dc79-5865-a3a4-db0c3eaad5d8. R1 pin 1 is /IN, pin 2 /OUT; C1 pin 1 /OUT, pin 2 GND. Connector J1 pin 1 /IN, pin 2 /OUT, pin 3 GND. All values and pad assignments agree.

Native board routing was read as saved: F.Cu /IN connects J1.1 to R1.1; /OUT connects R1.2, C1.1 and J1.2 through the tee at (107.5,100) mm. C1.2 reaches the GND via at (113,100) mm; the saved B.Cu GND fill reaches J1.3. Native DRC reports no unconnected items. No refill performed.

## Electrical calculation and independent simulation

With /IN ideal-grounded, R1 is the discharge resistance for C1. Kirchhoff's law gives C*dVout/dt + Vout/R = 0. Thus tau=10,000*100e-9=0.001 s and Vout(t)=1*exp(-t/tau) V. At 5 ms the result is 0.006737946999 V (pass). The 10 mV crossing occurs at 0.004605170186 s. Exact numerical inputs/results are in calculation.json.

shutdown.cir uses only the saved ideal resistor and capacitor. Vground clamps /IN to 0 V; C1 has IC=1 V and transient UIC avoids a DC operating point that would erase that initial charge. No load is attached to /OUT. Maximum timestep is 1 us, with reltol=1e-8, abstol=1e-14, vntol=1e-10. No device/subcircuit model is involved. Stimulus, pin mapping and measurement were explicitly inspected; a command exit alone was not used to establish behavior.

ngspice measures Vout at 5 ms as 0.006737944 V and the 10 mV crossing as 4.60517 ms. measurement.json compares these with the analytic answer: 3.0e-9 V endpoint difference and maximum waveform error 3.03e-8 V. Sampled /IN remains exactly 0 V; the first sample at 10 ns is 0.99999 V, consistent with the 1 V initial charge. See shutdown.log and shutdown_samples.dat.

## Supplied verification audit

Read actual execution records, check summaries, console logs and native reports under /evidence/work and /evidence/checks; recorded in check_audit.json. ERC has an empty violations array. DRC has empty violations, unconnected_items and schematic_parity arrays. Geometry lists the expected three footprints, seven pads, five track segments, one via and one saved filled zone. Its console contains three PROPERTY_ENUM assertions despite exit 0; these diagnostics do not establish a native circuit defect and do not replace the independent native/netlist checks. No electrical transient model, shutdown stimulus or voltage measurement is supplied by these four checks. Independent analysis above fills that circuit-function coverage gap.

## Coverage omissions and limitations

- ERC and DRC were inspected, not rerun. Ignored ERC checks: single_global_label, four_way_junction, simulation_model_issue, footprint_filter. Ignored DRC checks: missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch, footprint_type_mismatch. No claim is made that ignored checks pass.
- Saved-board fill/connectivity only; no refill, fabrication output validation, physical measurement, detailed manufacturing or mechanical qualification.
- Per explicit scope: no tolerances, ESR, leakage, parasitics, ratings, real part models, temperature, finite source impedance or external load evaluated.
- Startup, reset, brownout, and other interface/operating cases are unspecified and not evaluated. This review establishes only the stated ideal shutdown case.

Conclusion: no evidence-supported circuit defect found within the specified scope.
