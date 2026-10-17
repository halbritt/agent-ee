# Physical review experiment record

Candidate: 329f916010187406f161061477ccaa609a7cfa22231e8b51f90b321f35ab1028
Run: 09798e99-2197-4e50-8731-974e12ae3215

## Commands and source inspection

- `rg --files -g AGENTS.md -g '!home/**' /work /candidate /evidence`: no matching instruction files, exit 1. No credential files opened.
- Read `/candidate/requirements/fixture-requirements.md`, `/candidate/project/rc_filter.kicad_pro`, supplied full native schematic/PCB excerpts and on-disk component/track/pad/zone locators with `rg -n`.
- Read `/evidence/work/drc/drc.json`, `/evidence/work/erc/erc.json`, `/evidence/work/netlist/design.xml`, `/evidence/work/rc-step/{deck.cir,control.sp,ngspice.log}` and geometry check record. These are native completed check records, not prior reviewer judgments.
- `kicad-cli version`: 10.0.7; `ngspice --version`: ngspice-42; `/usr/bin/python3 --version`: 3.12.3. Exact version output is also retained in review-results.json.
- Inspected pcbnew API docstrings for connectivity and polygon queries. Imports emit three PROPERTY_ENUM assertions, matching supplied geometry stderr. They are diagnostics, not an electrical violation. Subsequent extraction completed; native segment widths are also parsed independently of pcbnew.
- `python3 -c 'import shapely; print(shapely.__version__)'`: module unavailable, exit 1. No installation or dependency on shapely; used native parsing and installed pcbnew.
- `/usr/bin/python3 /work/experiments/review.py > /work/experiments/review-console.log 2>&1`: first invocation had a Python syntax error in polygon sampling, corrected before any experiment ran. Second invocation exit 0. Final script, console and structured results retained. No native source was modified and no zone refill/save was performed.

## Outcomes and interpretation

`review-results.json` records source hashes matching the packet, native segment geometry, physical connected sets, saved fill samples, populated values, exported topology, ideal analytic calculation, and decoded original waveform. `physical_adjacency` contains complete connected sets returned by KiCad, rather than only immediate neighbors.

All five native copper segments are 0.25 mm, above the declared 0.20 mm minimum. The closed rectangular outline is 20 by 12 mm. Read placement/pad/courtyard geometry shows the three footprints within the outline without courtyard overlaps. This is a geometry inspection, not fabrication qualification.

Saved connectivity has J1.1 to R1.1, J1.2 to R1.2/C1.1, and C1.2 through its F.Cu trace and (113,100) via into the saved B.Cu fill and J1.3. Polygon samples confirm copper at the via and along two J1.3 thermal-spoke directions. These are geometric observations, not an impedance or electromagnetic simulation. Native DRC record reports zero violations, zero unrouted items and zero parity issues; a fresh DRC rerun was not necessary or performed. The record's ignored categories remain coverage exclusions.

R1 is 100k in both native schematic and board; C1 is 100n. Exported topology agrees with the saved physical connected sets. Ideal initially discharged unloaded response is Vout=1-exp(-t/(R*C)), tau=10 ms, yielding 0.095162582 V at 1 ms and 0.393469340 V at 5 ms. The requirement is 0.62 to 0.65 V at 1 ms, implying tau about 0.95254 to 1.03350 ms. This supports a major response defect without relying on a successful simulation exit or the check's conclusion.

Independently decoded all 1025 original binary waveform points as real little-endian doubles, checking header counts, zero initial voltage and final time. Linear interpolation gives 0.095162514 V at 1 ms and 0.393469316 V at 5 ms; Vin at 1 ms is 1 V. Stored-point deviation from the ideal instantaneous-step analytic curve is at most 5.0e-8 V. The original deck ramps in 1 ns and measures from t=0; this negligible difference cannot explain the approximately 0.525 V miss below the lower limit. There is no acceptance bound at 5 ms, so no separate 5 ms defect is alleged.

## Coverage omissions

- Saved fill only; no regeneration, Gerber/drill export audit or fabricated continuity measurement.
- pcbnew connectivity shares KiCad's implementation with supplied DRC; native width parsing and analytic response are independent checks.
- ERC ignores single_global_label, four_way_junction, simulation_model_issue and footprint_filter. DRC ignores missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch and footprint_type_mismatch. The saved three footprints do include courtyards, and their pad types/assignments were inspected directly.
- No tolerance, temperature, external load, ADC sampling, EMI/parasitic, hardware, assembly or environmental tests.
- No exact purchased part/connector models, copper thickness, fabrication capability or thermal boundary conditions supplied; no mating, ampacity or thermal qualification inferred. These omissions are not independent requirement violations for this disposable ideal-response fixture.
