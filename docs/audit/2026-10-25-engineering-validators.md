# Validators and engineering correctness (HEAD 404eba0e)

Supporting investigation for the [independent audit of 2026-10-25](2026-10-25-independent-audit.md).
Read-only; produced by an investigation sub-agent with three delegated sub-audits
(native round-trip, the accepted v7 design, the pod and esp_prog), whose
load-bearing claims were spot-verified. Findings F5, F10, F11 and F13 of the
main report were re-verified in source by the auditor.

## Ranked findings

### 1. Confirmed. Native DRC numeric violations against unsourced limits are advisory and never fail the DRC check

- `src/agent_ee/checks.py:364-387` (`grade_drc`), `src/agent_ee/design_rule_provenance.py:552-657, 692-694` (`classify_finding`, `advisory_numeric`): any `clearance`, `track_width`, `via_diameter` or similar finding whose limit resolves to class `default` gets `enforcement: advisory` and is dropped from `failing`; the summary becomes "N advisory numeric violation(s) and zero unwaived graded issues", outcome `pass`. `require_evaluated_limits` (`:705-731`) likewise tolerates a project that ignores a numeric check when its limit is default-class.
- Experiment (pure Python): a 0.05 mm clearance against a default 0.2 mm board limit yields `advisories 1, failing 0`; the same finding with a `manufacturer` citation yields `failing 1`.
- In release-build-v7 every board setting is `class: default, enforcement: advisory` (`acquisition.kicad_pro`, `agent_ee_constraint_provenance`), so "zero unwaived DRC findings" means "no shorts, outline or courtyard structural findings and no finding that KiCad happened to emit"; a real 0.15 mm clearance would have passed. The mitigation is that `fab_profile` is MANDATORY (`src/agent_ee/build_workflow.py:27-28`) and re-runs DRC with generated sourced rules at the JLC 0.10/0.10 mm minima (`review/checks/profile.json`, status satisfied). The hard floor actually enforced on v7 is 0.1 mm, not the 0.2 mm the project advertises.
- Correction: a numeric native violation is geometric fact regardless of who sourced the limit. Grade it `defect` with provenance `advisory` (so a waiver can cite the provenance), or refuse (`unknown`) until the limit is sourced. Never `pass`. Rename the summary so "zero unwaived issues" cannot be read without the advisory count.

### 2. Confirmed. `dc_path` and `dc_rail` refuse on host-binary hashes, not on physics

- `src/agent_ee/dc_path.py:11-13` pins `/usr/bin/python3`, `pcbnew.py` and `_pcbnew.so` by SHA-256 as a source constant; `:448-449, 463-464` and `dc_rail.py:220` raise `DC native tool identity requires requalification`, reported as `unsupported`. Any apt update of python3 or KiCad on the gate host turns the check off until someone edits source. The check's claim is ideal `rho*L/A` over an extracted, retained `geometry.json`; the interpreter hash does not bear on it, and the extraction already pins `pcbnew_version`.
- Correction: record tool identity as provenance in `analysis.json` (already done) and gate on `pcbnew_version` plus probe schema; move any hash pin into a captured qualification record rather than a code constant. The same pattern exists in `fab_consistency.pinned_kicad_cli` (`fab_consistency.py:244`), `footprint_library.py` (5 pins) and `passive_corners.py`.

### 3. Confirmed. `assembly_readiness` operating-temperature acceptance hardcodes one customer's decisions, and its exemptions path can only yield unknown

- `src/agent_ee/assembly_readiness.py:985-1055`: `OWNER_GRANTS` and `DELEGATED_DECISIONS` embed fome-mc commit SHAs, `D-PCB-9` and `D-PCB-12`, references `U3` and `J3`, MPNs `NEO-M9N-00B` and `DM3AT-SF-PEJM5` in library code. `:1340-1348`: every entry in `exemptions` is validated (`:937-947`) and then unconditionally raises "operating-temperature exemptions are retired", giving `unknown`. The field is dead but still accepted by the schema.
- AGENTEE-310's "test the actual limit" intent is implemented correctly (`ambient_verdict` against the DigiKey range; `reduced_acceptance` without passing `thermal_evidence` stays `unknown`, `:1420-1424`), but the rating source is the DigiKey catalog "Operating Temperature" string, not a datasheet, and self-heating is not modelled. In v7 `operating_temperature` is `null` (`review/checks/assembly.json`), so the accepted release never exercised this code.
- Correction: move grants and decisions to a captured, SHA-bound declaration file validated like `dispositions` in `happy_severity_gate.py:34-50`; delete the `exemptions` key; cite datasheet facts via `passive_ratings`'s `verify_citations` path.

### 4. Confirmed. Passive voltage and power ratings are checked for presence, never against applied stress

- `src/agent_ee/assembly_readiness.py:792-802` (`_has_passive_rating`) passes on any non-empty `Voltage` or `Power` property string. `src/agent_ee/passive_ratings.py:28-29` LIMITATION: "no derating, applied stress ... or engineering acceptance is established". v7 `source/ratings.json` is `{"catalog": true, "extractor": null, "facts": null}`; ratings came from JLC catalog attributes. The only numeric guard is a hand-authored `interval_assertions` relation.
- v7 happens to be fine (25 V and 50 V capacitors on 5 V; 5.2 mW on 100 mW resistors, verified by the design sub-audit), but a 6.3 V capacitor on a 12 V rail would pass every check unless an engineer wrote the inequality.
- Correction: derive rail membership from the native netlist (already in `connectivity.json`) and declared rail maxima (already in `electrical-intervals.json`) and generate the `rating >= rail_max` interval automatically; keep the authored ones as additions.

### 5. Confirmed. Electrical verification is authored arithmetic over declared numbers, labelled honestly in limitations but summarised as closed obligations

- `interval_assertions` (v7 `engineering-intervals.json`): 13 relations, every one of 31 operands `source: "declared"`; the limitation text says "the authored equation is not derived from the schematic, catalog claims are not authenticated datasheet facts". The geometry `measurements` limits were set after measurement (`body_gap` 0.200 versus min 0.200, margin 2.8e-15 mm; placement gap 0.050 versus 0.049).
- `active_spice` v7 decks (`review/work/power-startup/startup/deck.cir`): the fixed-output TPS7A2033 is simulated with TI's TPS7A20_ADJ transient model plus `.param V_out=3.3` (`source/analysis/power-startup/power.lib`); the ADS1115 is a hand-written 300 µA current sink stub; input and output capacitors are "agent-selected application sensitivity" corners (3 µF and 12.65 µF). The 3.2997 V result proves the TI model regulates into a 300 µA load, not much more.
- `docs/system-acceptance.md:43` ("electrical obligations now have reviewed source/model evidence") and the closure doc read as if a checker proved them. The ENGINEERING-REVIEW itself says the prose "has not been proved by a software checker".
- Correction: require interval operands to be `cited` (datasheet pointer via `passive_ratings.verify_citations`) for any relation bound to a requirement; refuse `declared`-only operands in release scope. Reject geometry limits recorded after the measurement they bound (compare receipt timestamps).

### 6. Confirmed. The accepted release exercised 16 validator kinds; most modules under audit have only unit-test evidence

- v7 scope (`review/candidate.json`): `active_spice`, `assembly_readiness`, `board_envelope` (8), `dfm_geometry`, `drc`, `erc`, `fab_package`, `fab_profile`, `geometry`, `interval_assertions`, `measurements`, `netlist`, `netlist_parity` (7), `parts_subset`, `placement` (9), `topology_rules`. Not exercised: `part_binding`, `cpl_rotation` (rotation `not_declared`, CPL rotations left to supplier preview), `fab_roundtrip`, `dc_path`, `rf_geometry`, `happy_severity_gate`, `jlc_dfm`, operating temperature. The "37 passing checks" are inflated by seven `netlist_parity` re-labels of one test (`review-acq-elec-1/3/4/5/6`, `deliv-2`, `topology`).
- Correction: state check-kind coverage in the acceptance doc; stop binding one check result to several requirements by copy.

### 7. Confirmed. v7 ground return is not checked and is weak: zero GND vias

- Board parse: six vias total (`/V3V3` twice, `/SCL` twice, `/ANALOG0` twice), one zone (B.Cu); the B.Cu GND pour is reached only through the four THT connector pins; every SMD ground returns over top-side traces. `electrical_locality.py:222-275` grades only pad-centre-to-centre distance (limitation: "no copper, thermal, EMC"); nothing evaluates via stitching or return-path continuity for a non-RF board. Harmless at about 6 mA and 128 SPS, but the ENGINEERING-REVIEW's "plane witnesses connect all interface ground pads" is true only for connector pads.
- Correction: add a `return-path/1` predicate (ground pad to plane connection via pad or via within N mm) to the placement and routing feedback; it is cheap on the existing pcbnew probe.

### 8. Confirmed. Pod owner constraints in the briefs are not owner constraints in the record

- fome-mc `telemetry-pod/REQUIREMENTS.md:163` (Rev 3) quotes the owner: "I don't know how the size got fixed. I didn't specify" and "I never set 12mm, that was arbitrary." `src/agent_ee/board_envelope.py:98` agrees. Meanwhile `fixtures/telemetry-pod/spec.md`, `fixtures/telemetry-pod/envelope.json` (0..80 by 0..50, "Height at most 12 mm", 776228-1) and `docs/telemetry-pod-fab-profile.md:91,119` are still Rev 2; `docs/pod-driver.md:144` registers the Rev-2 spec SHA. The bound J1 STEP is 27.77 mm tall and overhangs 22.35 mm (fome-mc `connector-candidate/README.md:51,97`). The 5 V 250 mA target is correctly recorded in `docs/telemetry-pod-delivery-plan.md:45-49`.
- Correction: regenerate the agent-ee pod fixtures from Rev 3 or mark them historical; never carry 80 by 50 or 12 mm as requirements.

### 9. Confirmed. Stale and contradictory release prose (housekeeping, but it travels with the delivery)

- `release-build-v7/source/manufacturing-decision.json` still says nominal 1.6 mm FR4 and "8 mm including mated connectors"; the PCB is `(thickness 1.2)` and the spec was revised. `component-selections.json` marks U1, U2, J1 to J3, D1 and C1 to C5 "pending electrical and physical qualification". `review/REPORT.md` says "Outcome: incomplete ... adjudication not performed" while `build-report.json` says `accepted: true`. ENGINEERING-REVIEW labels the final geometry numbers as "earlier measurements".

### 10. Plausible. Custody is sound but heavy; refusals are typed, not engineering-motivated

- `stage_receipts.py:971-1041`: any undeclared file in `run/design`, a tool whose digest cannot be read, or a displaced executor record gives `refused`. Correct for provenance; none of these refusals is tied to an engineering property, and a transient `/work` permission issue reads as "refused" rather than "retry". `happy_severity_gate.py:55-70` refuses unless all four kicad-happy heuristic analyzers return exact self-consistent counts (acceptable; it never passes on partial output). `final_planes.validate` (`final_planes.py:22-50`) requires `island_removal=always`, refuses if `isolated_copper` is ignored, and re-runs DRC. `cpl_rotation` stays `PREVIEW_REQUIRED` by design (`cpl_rotation.py:29-32`), which is honest.

## Electrical versus geometric versus manufacturing versus physical: where the lines blur

- Geometric legality (ERC, DRC, parity, containment, courtyard, envelope): the strongest layer, native, independently reproduced here. Weakened only by finding 1 (advisory numeric limits) and by the fact that the project's own 0.2 mm limits are not the enforced floor.
- Electrical correctness: `topology_rules` is structural (pin-to-net, presence); `interval_assertions`, `measurements`, `active_spice` and `dc_path` are authored, bounded models (findings 4 and 5). Nothing derives a check from the schematic; nothing evaluates ratings versus stress, loop or return quality (finding 7), or thermal. The docs' phrase "zero native errors/opens" is a routing-completeness statement, not an electrical one; `system-acceptance.md:43` does say "Clean ERC is not functional acceptance", which is the right sentence.
- Manufacturing-file consistency: `fab_package` (export plus `fab_consistency`) is solid and was in scope; `cpl_rotation` and `fab_roundtrip` exist but rotation status in v7 is `not_declared`, so SOT-23-5 and VSSOP rotations are unverified.
- Impedance: `docs/qualification/pair-impedance-2026-10-25.md:22-24, 59-60` describes nominal microstrip from the native stackup; KiCad inserts a relative permittivity of 4.5 when absent (the job refuses missing data, which is right); "analytical agreement, not a field measurement, supplier stackup approval". `rf_geometry.py:309-341` reads the effective permittivity from a captured KiCad-calculator JSON bound by SHA. These are presented as nominal; no fab stackup is involved anywhere.
- Physical validation: none; consistently labelled "unperformed".

## Qualification designs

- Acquisition v7 (TPS7A2033 plus ADS1115, 17 parts): electrically sane and conservative. LDO and ADC pin use, ADDR to GND (0x48), ALERT/RDY and AIN2/3 handling per datasheet, 1 kΩ and 10 nF C0G front end (corner 15.9 kHz, 0.82 mV loading error), 2.2 kΩ I2C pull-ups (188 ns rise), BAT46W series reverse-polarity with R5 and R6 bleeds, EN tied to IN, thickness 1.2 mm inside the JST PH range. The avoidable complexity is in the inputs, not the circuit: 198 source files for 17 parts, 12 `datasheets-*` directories (4 empty), 10 `stock-*` directories, 11 binding files, 4 superseded part records shipped as live inputs, 7 release-build versions. Layout weakness: finding 7. Verdict: right-sized circuit, 3 to 5 times too much apparatus; a clean demo would ship one datasheet directory, one stock snapshot and `history/`.
- Telemetry pod: 309 real parts, 29 ICs, 19 blocks on a 90 by 65 mm 4-layer board (intent `fome-mc/telemetry-pod/expansion/production-capture/composition/intent.bound.json`): ESP32-S3, LSM6DSO32, NEO-M9N plus SMA and antenna bias, TJA1051T/3 CAN plus a full MCP2518FD CAN-FD channel for an unselected tire sensor, two LMR36520 feeding REF5050-tracked TPS7B4253 pairs, four TPS26625 eFuses, three TPS389001 supervisors, three ADS1119 rail monitors (46 parts), eight ADA4096-buffered MCP3208 channels, Hall-only wheel inputs, microSD. Best historical routing 203 to 205 of 235 nets; nothing routed to completion. Anti-alias at about 318 Hz for 200 SPS channels is under-filtered. Verdict: block-level electronics are conventional and match the Rev-3 decisions table, but as a generator demonstration it is badly over-scoped (second CAN, thermistor and diagnostic subsystems with no selected sensors) and its fixture envelope is wrong (finding 8).
- esp_prog: KRT's bundled MIT example (21 footprints, 2 layers, 31.75 by 14.5 mm, CH340H and ME6210; not Espressif's ESP-Prog). Recorded: a 139 s run with 0 errors, 0 unconnected, 262 warnings, `accepted:false, clean:false`, two declared connector edges violated (`docs/benchmarks/placement-job-2026-10-24.md`, `docs/placement-job.md:3`); a 721 s portfolio run with 0/0/258 warnings, 8 via-in-pad, USB skew 2.96 mm; stock baseline failed and the 44-opens matched comparison outstanding (`docs/placement-routing-plan.md:15,25,46`). Criteria are engineering-flavoured (DRC errors, opens, edge intent) but no pair or impedance targets. Verdict: a legitimate first rung, about 15 times smaller than the pod and not closed; inadequate as the sole tooling qualification.

## Native KiCad identity and round-trip (verified)

- UUIDs: `uuid5(layout namespace, json(parts))` keyed by component id, sheet file and pin number, never refdes (`schematic_emitter.py:228-229, 677, 703, 812`); refdes renumber keeps UUIDs; changing a component id churns them (regeneration re-binds by connectivity signature, `schematic_regenerate.py:89`).
- Manual edits: the emitter refuses to write into an existing directory (`schematic_emitter.py:1050-1056`); the only merge path is `schematic_regenerate` and it requires flat `connectivity-intent/1` and rows presentation (`:277-278`, `:141`), refusing `human_edit_conflict` otherwise. Hierarchical projects have no regeneration path; a re-emit is an overwrite. Forward annotation is a true netlist-diff sync with pcbnew that verifies retained footprints did not move (`forward_annotation.py:95-295, 359-401, 550-620`).
- Hierarchy: emitted (sheets, sheet pins, hierarchical labels, instance paths, `schematic_emitter.py:805-829, 892-964`); IR v1 refuses it, v2 flattens via `_connectivity_hierarchy.py`; `fab_roundtrip` is Gerber and Excellon only and has no sheet concept.
- A real round trip exists: `schematic_roundtrip.roundtrip()` decompiles, emits, runs `kicad-cli sch export netlist` on both, runs ERC and `netlist_parity.compare`, with mutant tests. `tests.test_schematic_roundtrip` plus `tests.test_forward_annotation`: 27 tests, 115 s, OK, 0 skipped. Caveat: absence of bwrap or kicad-cli is a skip unless `AGENT_EE_REQUIRE_TOOLCHAIN=1` (`tests/toolchain.py:84-90`). "Opens cleanly" is evidenced by `kicad-cli sch upgrade --force` re-save parity, not a GUI load.

## Runtime behaviour

- Reproducibility: Rust planner with `splitmix64/agent-ee-515-v1` and explicit seeds (4 to 9), retained per-seed outputs; v7 placement job 286.7 s = 6 seeds at about 40 s plus route 35.2 s (router budget 217.7 s) for a 17-part 2-layer board, slow for the size. Build identity is the plan SHA plus the source inventory plus runtime hashes; resume refuses drift and re-verifies completed stage tree identities under a lock (`build_workflow.py:438-480`).
- Diagnostics: typed `execution` and `outcome` per check, `limitations` everywhere, `agent-ee build review`. `doctor` on this host: kicad-cli 10.0.7, ngspice 42, bwrap, pcbnew OK; the Rust `agent-ee-placement` is not installed here (v7 ran in `/opt/agent-ee`).
- Maintainability: 238 modules, 121,341 lines; `kicad_route_attempt.py` 3,061 lines with 314 hardcoded 64-hex pins, `assembly_readiness.py` 2,762; `_text(` redefined in 24 modules, `_number(` in 10, `_mm(` in 7, `strict_json` or `_strict_json` in 8; 9 modules carry hardcoded SHA pins; 8 library modules reference fome-mc or owner specifics. Tests: 338 files, 158,080 lines.

## What was run

```text
kicad-cli pcb drc --format json --severity-all --schematic-parity --units mm -o drc-indep.json acquisition.kicad_pcb
  -> 17 violations, all lib_footprint_issues warnings (host has no library table for the sandbox libs), 0 unconnected, 0 parity, 0.77 s;
     ignored_checks are KiCad defaults (missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries,
     footprint_filters_mismatch, footprint_type_mismatch); footprints do carry courtyards (47 F.CrtYd lines for 17 footprints)
kicad-cli sch erc --format json --severity-all -o erc-indep.json acquisition.kicad_sch
  -> 37 violations, all lib_symbol and footprint_link library-path warnings; 0 electrical
PYTHONPATH=src /usr/bin/python3 -m agent_ee.cli doctor      -> Rust planner missing on host
PYTHONPATH=src:tests /usr/bin/python3 -m unittest tests.test_assembly_readiness   -> 91 tests, 250.5 s, OK
PYTHONPATH=src:tests /usr/bin/python3 -m unittest tests.test_checks_drc           -> 5 tests, 16.5 s, OK
PYTHONPATH=src:tests /usr/bin/python3 -m unittest tests.test_part_binding         -> 22 tests, 1.0 s, OK
PYTHONPATH=src:tests /usr/bin/python3 -m unittest tests.test_schematic_roundtrip tests.test_forward_annotation -v -> 27 tests, 115.2 s, OK, 0 skipped
PYTHONPATH=src python3 (grade_drc and classify_finding on a synthetic 0.05 mm clearance finding) -> default-class: advisories 1, failing 0; sourced: failing 1
```

Independent DRC and ERC confirm the "zero unwaived DRC/ERC" claim for v7 modulo finding 1's interpretation.

## Not verified

- TPS7A20 absolute-maximum input, YAGEO 75 V and UNI-ROYAL thickness notes, KiCad `TSSOP-10_3x3mm_P0.5mm` versus the TI DGS drawing; TI SPICE model fidelity for reverse and removal behaviour.
- Pod: TE 776163 drawing height (only fome-mc's STEP-derived 27.77 mm); LMR36520 and SMCJ24CA limits; the 305 versus 309 component-count mismatch; AGENTEE-513 stock numbers (Cairn, not in the repo); esp_prog upstream authorship.
- No GUI load of generated projects; hierarchy, parity and regenerate native suites were classified by reading, not run; `test_fome_mc_gerbers_reparse_...` skip status.
- Whether any production scope has ever bound `dc_path`, `rf_geometry`, `cpl_rotation` or `operating_temperature` to an accepted release (none found in `build/acquisition-release`).
