# Physical initial review

Candidate d36037b672ce46e4cdc6b63b3a62a082e2d3e61c6ef6ee33d20711d15b66b514
Run 7215c203-f5dd-4e08-9d58-7df783ac53a5
Packet SHA256 87e3932f9462dc1141aa01056292b7f51b592194b2b469d0414dc746b8f07919

## Methods and commands

Read original files only at /candidate; no candidate writes or zone refill. Evidence was read at /evidence. Citation mapping: /candidate -> inputs; /evidence -> run root; this directory -> review-work/physical/experiments.

Exploration: `pwd`; `rg --files /candidate /evidence /work` filtered to relevant engineering artifacts and AGENTS.md; `ls -la /work`, `/candidate/project`, and `/work/experiments`; `cat /candidate/requirements.txt`, project configuration, and supplied DRC/netlist/check records; `rg -n` for PCB items and project rule settings; `kicad-cli pcb export svg --help` (help only, no export). No applicable AGENTS.md was returned. No credential files or prior-reviewer experiment directories were read.

Versions: KiCad CLI 10.0.7, ngspice 42, Python 3.12.3. Initial `python3 -c` and `/usr/bin/python3 -c` probes found shapely absent; pcbnew imported with version 10.0.7 and emitted PROPERTY_ENUM assertion diagnostics. Neither diagnostic establishes an electrical or geometry failure. The actual geometry calculation uses Python standard library directly on the saved native S-expression, independent of pcbnew and shapely.

Primary execution: `python3 /work/experiments/audit.py > /work/experiments/audit-console.log 2>&1`, exit 0. Exact subsequent CLI argv, exit codes, and log paths are in commands.json. audit.py parses native schematic and board, compares pad net assignments with supplied XML, checks saved tracks and a continuous ground-fill path, calculates the RC result, repeats native DRC/ERC without refill, and runs an explicitly initialized transient.

First transient shutdown.cir measured the requested 5 ms voltage successfully but the optional diagnostic at 1 ns was before its stored time range. shutdown.log preserves the failed diagnostic despite exit 0. A second deck shutdown_checked.cir moves only that diagnostic to 1 us; its command/log are retained separately in commands.json and shutdown_checked.log. This scratch deck is an experiment, not a candidate modification.

## Native geometry and placement

Original board locators: inputs/project/rc_filter.kicad_pcb lines 90, 239, and 388 contain C1, R1, J1 footprints. Lines 210–226, 359–375, and 587–614 contain pad geometry and net assignments. Lines 627–666 define the closed 20 by 12 mm rectangle from (96,96) to (116,108). Lines 667–714 contain all five tracks and the GND via; lines 715 onward contain the B.Cu ground zone and its saved filled polygon.

IN: J1.1 (100,100) to R1.1 (104.0875,100). OUT: R1.2 (105.9125,100) to C1.1 (109.05,100), with an actual same-layer tee at (107.5,100) reaching J1.2 (100,102.54). Ground: C1.2 (110.95,100) via F.Cu track to plated via (113,100), saved B.Cu fill to plated J1.3 (100,105.08). J1.1 and J1.2 have clearance voids in the saved ground fill.

native_audit.json saved_ground_return_witness gives a concrete continuous path in saved copper, starting at (100.55,105.63), inside J1.3's annulus, through its southeast thermal spoke, then to the via. Segment endpoints are inside the polygon and exact segment-to-boundary distances are positive (minimum 0.2499997 mm); this is an existence test, not a width, ampacity, or thermal qualification. No zone refill is used.

Manual courtyard bounds from native footprints: J1 x=98.23..101.77, y=98.23..106.85; R1 x=103.32..106.68, y=99.05..100.95; C1 x=108.3..111.7, y=99.02..100.98. Courtyards are separate and inside the outline. Minimum courtyard-to-outline margin is 1.15 mm (J1 at bottom). These observations do not impose a supplier's manufacturing limits.

Native hashes match the packet before and after checks (native_audit.json hashes; final sha256sum): board f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de; schematic ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b.

## Electrical scope and verification challenge

inputs/project/rc_filter.kicad_sch: C1 UUID 1dcaf7b4-dc79-5865-a3a4-db0c3eaad5d8 has value 100n; R1 UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78 has value 10k. work/netlist/design.xml nets /IN, /OUT, GND agree with original saved PCB pad membership. The passive values agree between board and schematic.

Assumptions exactly follow requirements: ideal passives and conductors, C1 initially +1 V OUT relative to GND, IN held at 0 V from t=0, no output load. R1 is the discharge path, not an open input or a loaded output. V_OUT(t)=exp(-t/(R*C)) V; tau=1 ms; V_OUT(5 ms)=0.006737946999 V; crossing 10 mV at 4.605170186 ms. ngspice uses IC=1 plus UIC (avoids replacing the charged capacitor with its zero-input DC operating point), no external load, max time step 1 us. It measures 0.9990005 V at 1 us and 0.006737944 V at 5 ms. Native rules do not test this transient; the independent calculation and explicit simulation do.

Initial current is 100 uA, initial resistor dissipation 0.1 mW, and initial capacitor energy 50 nJ in this ideal experiment. No rating or temperature claim follows from these numbers or the 0.25 mm track width.

Supplied work/erc/erc.json and work/drc/drc.json were read, including ignored categories, zero violation arrays, and zero parity/unconnected arrays; work/netlist/design.xml, connectivity.json, work/geometry/geometry.json and geometry/console.log were also inspected. Repeated DRC/ERC reports in this directory agree. No supported defect was identified.

## Coverage omissions and limits

- Saved zone fill only; no refill or comparison against a newly generated fill.
- ERC ignored checks remain single_global_label, four_way_junction, simulation_model_issue, footprint_filter. DRC ignored checks remain missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch, footprint_type_mismatch. They were not re-enabled. Manual source inspection covers the simple footprint/courtyard/net geometry only.
- No fabrication, assembled-board measurement, Gerber/drill export audit, supplier process validation, connector mating/retention study, or 3D interference test.
- No component tolerances, ESR/leakage, interconnect parasitics, finite input impedance, external loads, environmental testing, ampacity, thermal simulation, or ECU qualification; none is included in the ideal exercise criterion. No component part numbers or ratings were assumed.
- The hand-built SPICE deck corroborates the parsed passive topology; it is not an independently supplied physical part model. The analytic solution and saved-copper path check address shared model assumptions within the stated ideal scope.
