# Physical initial review record

Candidate 57d1859261fc93ec38ba0bf952f35b81117f84e4e1b119bf8be5555251f4606f; run c89610e1-4fc4-41c8-9a8a-48c6ebc5ad8c.

## Commands and versions

Read-only inventory: `ls -la /work /candidate /evidence /evidence/checks /evidence/work`, `rg --files /candidate /evidence`, and `rg --files -g AGENTS.md` under these task directories. No AGENTS.md found. No credential files read.

Read original requirements, schematic/PCB population and routing fields, project JSON and library tables with `cat`, `sed -n` and `rg -n`. Read supplied check JSON, native ERC/DRC JSON, connectivity JSON, and XML netlist directly from /evidence. Consulted `kicad-cli pcb export svg --help`; did not export/refill or alter any native file.

`kicad-cli version`: 10.0.7.
`/usr/bin/python3 -c 'import sys, pcbnew; print(sys.version); print(pcbnew.Version())'`: Python 3.12.3, pcbnew 10.0.7. pcbnew import emitted three PROPERTY_ENUM assertions, also seen in supplied geometry console; it returned version data. No resulting design defect inferred.
`/usr/bin/python3 -c 'import importlib.util; ...'`: matplotlib installed, shapely and sexpdata absent. No external references needed.

Reproducible analysis command (exit 0, stderr empty):

```sh
/usr/bin/python3 /work/experiments/physical_review.py > /work/experiments/physical_review.stdout 2> /work/experiments/physical_review.stderr
```

Versioned results and source hashes are in physical_results.json; independent Python native S-expression parser in physical_review.py. Matplotlib 3.6.3. Rendered geometry saved as saved_geometry.png and visually inspected. Roundrect pads drawn as bounding rectangles for illustration; not a manufacturing plot. All new artifacts are under /work/experiments. Candidate remains read-only and unchanged.

## Results and useful native locators

- Native R1 schematic: inputs/project/rc_filter.kicad_sch:1252-1285, UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78; Device:R, in_bom yes, on_board yes, dnp no, value 10k.
- Native R1 PCB: inputs/project/rc_filter.kicad_pcb:238-375, UUID 8404a180-a151-464e-8f25-8cf316cdcba5; attr smd without dnp; pads 1 /IN and 2 /OUT. J1 pads at :586-613 provide /IN at (100,100), /OUT at (100,102.54).
- Independent graph uses actual F.Cu track centerlines, endpoints and pad centers, without connecting by net labels. R1 is explicitly added as a component bridge only after checking native population. Path: J1.1 (100,100) -> R1.1 (104.0875,100) -> fitted R1 -> R1.2 (105.9125,100) -> OUT T junction (107.5,100) -> (107.5,102.54) -> J1.2 (100,102.54). Nominal resistive bridge is 10 kohm. Trace/contact resistance ignored; this is topology, not a physical resistance measurement.
- Without the R1 bridge, the same graph has no IN-to-OUT path. This analytical counterfactual checks that the method does not count an empty footprint as conducting. No scratch candidate modification was made.
- inputs/project/rc_filter.kicad_pcb:667-697 supplies the IN/OUT tracks, including the OUT endpoint-to-segment T junction. Actual R1 pad-edge gap is 0.800 mm, so no copper-only bridge is assumed.
- XML export net memberships (work/netlist/design.xml, nets /IN, /OUT, GND) agree with native pad assignments. work/netlist/connectivity.json components.R1.dnp is false, agreeing with native population.
- Return: C1.2 -> F.Cu track (110.95,100) to (113,100) -> plated via -> B.Cu saved GND polygon -> J1.3 plated pad. Native sources inputs/project/rc_filter.kicad_pcb:699-799. Saved polygon annulus samples show positive copper overlap at via and J1.3 thermal spokes (720/720 and 360/720 samples). The rendered saved fill also shows connection to the main plane. Samples are evidence of overlap, not an exact clearance/thermal proof.
- Board outline 20 x 12 mm, x=96..116, y=96..108. Viewed placement and copper fit inside the outline; component courtyards are present, with no obvious collision. Native DRC independently reports no violations, unconnected items, or schematic parity issues (work/drc/drc.json, respective arrays). No fabrication-specific sufficiency inferred.
- work/erc/erc.json sheet / violations is empty. Native ERC/DRC execution records agree with the stated command and version; their successful exits are not used to establish the resistive path.

## Coverage challenge and omissions

Supplied ERC/DRC cannot alone establish assembled resistive continuity, and geometry counts alone do not test the requirement. The separate population-gated graph establishes that property from the native source. No evidence-supported defect found.

No bench measurement, SPICE stimulation, load sweep, thermal model, current/voltage qualification, manufacturer capability check, assembly tolerance or reliability qualification performed. No voltage, current, load, temperature, copper weight, plating specification, or exact part model is supplied; trace width and nominal package do not establish ratings. No ECU qualification claimed. No zone refill performed: only saved copper evaluated. Independent graph is specific to these unrotated footprints and endpoint/T joins and is not a general DRC engine. Plot uses pad bounding boxes. Saved native reports retain ignored ERC checks (single_global_label, four_way_junction, simulation_model_issue, footprint_filter) and DRC checks (missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch, footprint_type_mismatch). Those disabled categories were not re-enabled; no broad all-rules claim is made.
