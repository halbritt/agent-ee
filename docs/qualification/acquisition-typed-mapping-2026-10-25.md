# Acquisition M1 typed obligation mapping — 2026-10-25

Prepared by agent-565 under AGENTEE-521/515 for the chief integrator. This
is method classification and input preparation for the **same** retained
acquisition design; it is not a build, a review, a manufacture approval, or
a claim that any obligation is satisfied. No retained file under `build/`
was modified; no judgment record, witness or candidate was created.

## Base and derived identities

| Item | Value |
| --- | --- |
| Base inputs | `build/acquisition-release/inputs` (git-ignored retained capture), 198 regular files |
| `spec.md` | sha256 `fbf659b7909af7af4c370ce309c6138c661029261a9a4906237f86be45444373` (equal to `spec-record.json` `spec.sha256`) |
| `spec-record.json` | 18 requirement clauses + 1 prose clause, byte-identical capture verified by `spec_record.verify` |
| `requirement-map.json` (map/1) | sha256 `df11c81d221f410ed016f3583ae14860570d117bbc81fcc40f9f6121367dc863` |
| Overlay | `fixtures/acquisition-typed/overlay.json`, sha256 `076eed026d341403e53a8fb509e706a1bb0e4d5160a5a041cb937d98b8455eb0` |
| Derived `requirement-map.json` (map/2) | sha256 `53f632d4a86fa92024466755e395f2fa52ab0af29708c78d4bfac1460e1b0772` |
| Derived `analysis/electrical-intervals.json` | base `8998b329e058d39d48bd3568240b58b4904ba079460d46032105ef5cc51654a7` → derived `93d049c4d7bf30b2e74df37817e8bc3dbfa2d4de4b29312e7203693e6fe2f203` (requirement ids and source locators re-bound to spans; parameters, assertions and bounds unchanged) |
| Added `obligations.json` | sha256 `e8c76333b9135fd03993908a74ce416b54c4ff93dd2dba4ded73fc440f047b51` |
| Unchanged files | 196 of 198 |
| Added CPL inputs (`cpl/`, agent-566 revision) | `assembly-profile.json` `f8182f507c12a5e9b5113b545f675d5960efeb5c2f8ad977b649d5dbaa2ab755`, `jlc-cpl-policy.json` `36b349c7a2ed1622e03a5193574c8b60c75a23ce6b2dde041edcfbb1c175db4b` (= profile `policy_sha256`), 11 `supplier/<LCSC>.raweasy.json` + 11 `.provenance.json` captures; every hash is in the overlay and verified on copy; third-party CAD bytes stay outside Git |
| Added checks | `fab-export` (`fab_export`) and `cpl` (`cpl_rotation`, policy + 11 supplier provenance files) declared verbatim from agent-566's acq-partb-2 scope; `fab` gains `assembly_profile: cpl/assembly-profile.json` and nothing else |
| Typed receipt | `agent-ee.requirement-map-receipt/2`, `method_binding: typed`, compiled against the accepted `release-build-v7` scaffold candidate `stages/03-scaffold-d1c92e58…/product/inputs` overlaid with the three derived files; `scope_sha256 79a76e38ac5f06644c99b0728797c03b59d38df72e45579e0a0f151a3383c5dc`, `baseline_id 986f6fcbea50dbb89c129a6f52155663b78744999c3dfa05f63f18a3d74b2201`, `coverage_id 10e725a2ade3f5e2745be8e7104d3d4c5dc82207872003721422310d8d455bca`; compile root inventory `914810bf00f477bfd42cebcd0bf175903bdaa78f45a7b8262cce99eb6f1cbcac` (27 overlaid files); compiled on main at 903721fb, which strips the profile from the delegated export |

Accounting of the derived declaration: 46 receipt rows; modes `checks` 29,
`judgment` 13, `bench` 2, `operational` 1, `context` 1, `limitation` 0;
obligation kinds executable 29 (including the 8 envelope-derived rows),
judgment 13, physical 2, operational 1, context 1. Every check id bound in
map/1 remains bound; no check definition was re-authored. Thirteen judgment
records are declared and none exists, so `coverage_view` reports every
judgment span `pending`, the operational span `pending`, both physical spans
`pending`, and every executable span `pending` until check rows exist.

Reproduce (reads the retained inputs, writes outside `build/`):

```sh
PYTHONPATH=src python3 -m agent_ee.typed_overlay prepare \
  build/acquisition-release/inputs fixtures/acquisition-typed/overlay.json \
  --out /path/outside/build/acq-typed \
  --added-input cpl=/path/to/agent-566/cpl-inputs-rev2 \
  --compile-against build/acquisition-release/release-build-v7/stages/03-scaffold-d1c92e58719b49298406561cdd1f0acb/product/inputs \
  --receipt /path/outside/build/acq-typed-receipt.json
```

## Mapping ledger

Byte ranges are `spec.md` offsets from the spec record; split points are the
sentence boundaries inside each clause. `executable` spans bind the map/1
check ids listed; nothing else changed in those check definitions.

| Clause / span | Bytes | Obligation → mode | Bound evidence | Why |
| --- | --- | --- | --- | --- |
| ACQ-NATIVE-1..5 | whole | executable → checks | `erc`; `drc`; `netlist`+`topology`; `parts`; `assembly` | native checks as map/1 |
| ACQ-ELEC-1-SUPPLY | 1074:1188 | executable → checks | `engineering-intervals` (input-headroom, input-rating), `review-acq-elec-1-topology` | supply window and polarity are arithmetic plus topology |
| ACQ-ELEC-1-TEMPERATURE | 1188:1289 | judgment | `judgments/acq-elec-1-temperature-applicability.json` | 25 C applicability of captured limits; 0–50 C is a non-claim unless shown |
| ACQ-ELEC-1-UNQUALIFIED | 1289:1382 | judgment | `judgments/acq-elec-1-unqualified-conditions.json` | hot plug, surge, unpowered drive: recorded as not qualified |
| ACQ-ELEC-2-RAIL | 1385:1546 | executable → checks | `engineering-intervals` (input-current-budget, minimum-load, lower-rail, upper-rail), `engineering-geometry` (rail_drop), `power-startup` | rail accuracy under declared load is modelled |
| ACQ-ELEC-2-SOURCE-EVALUATION | 1546:1661 | judgment | `judgments/acq-elec-2-source-evaluation.json` | bias, temperature, tolerance, dropout, dissipation operands trace to captured rows |
| ACQ-ELEC-3-REVERSE | 1664:1822 | executable → checks | `engineering-intervals` (reverse-input), `review-acq-elec-3-topology` | reverse stress arithmetic plus D1/R5/R6 topology |
| ACQ-ELEC-3-REMOVAL | 1822:1929 | executable → checks | `power-removal` | existing SPICE removal corners |
| ACQ-ELEC-3-BACK-POWER | 1929:1995 | judgment | `judgments/acq-elec-3-back-power-contract.json` | operating-contract statement, not a board property |
| ACQ-ELEC-4-MEASUREMENT | 1998:2180 | executable → checks | `engineering-intervals` (input-range, filter-settling), `review-acq-elec-4-topology` | range, bandwidth, source impedance arithmetic plus J3 topology |
| ACQ-ELEC-4-FILTER-SCOPE | 2180:2255 | judgment | `judgments/acq-elec-4-filter-scope.json` | declared non-claim (no antialias guarantee) |
| ACQ-ELEC-4-ERROR-EVALUATION | 2255:2344 | judgment | `judgments/acq-elec-4-error-evaluation.json` | loading, settling, ADC error from captured tables; no invented precision |
| ACQ-ELEC-5-HOST-BUS | 2347:2468 | judgment | `judgments/acq-elec-5-host-bus-contract.json` | host bus contract; address 0x48 follows from `adc-ties` in ELEC-6 |
| ACQ-ELEC-5-PULLUPS | 2468:2527 | executable → checks | `engineering-intervals` (i2c-rise, i2c-sink, i2c-high, i2c-low), `review-acq-elec-5-topology` | R3/R4 arithmetic |
| ACQ-ELEC-5-CONFIGURATION | 2527:2726 | judgment | `judgments/acq-elec-5-configuration.json` | register words and read sequence are instructions, not file checks |
| ACQ-ELEC-6-TIES | 2729:2808 | executable → checks | `adc-ties` (topology_rules) | the wrong-address discriminator |
| ACQ-ELEC-6-HIGH-IMPEDANCE | 2808:2894 | judgment | `judgments/acq-elec-6-high-impedance-contract.json` | external host behaviour; not provable from files |
| ACQ-ELEC-6-J2-PIN4 | 2894:2940 | executable → checks | `review-acq-elec-6-remaining-topology` | J2 pin 4 net connectivity |
| ACQ-MECH-1-BOARD | 2971:3077 | executable → checks | `review-acq-mech-1-outline/-layers/-stackup` | envelope checks at 1.2 mm |
| ACQ-MECH-1-SCOPE | 3077:3140 | judgment | `judgments/acq-mech-1-bench-scope.json` | bench article, no enclosure; cites the 1.6→1.2 mm correction |
| ACQ-MECH-1-SUPPLIER | 3140:3229 | physical → bench | supplier preview / DFM confirmation, not performed | supplier confirmation is a pending physical-side obligation |
| ACQ-MECH-2 | whole | executable → checks | `engineering-geometry`, three anchor `placement` checks | as map/1 |
| ACQ-MECH-3-DISTANCES | 3536:3703 | executable → checks | `locality` | 3 mm pad distances measured |
| ACQ-MECH-3-LIMIT-RATIONALE | 3703:3780 | judgment | `judgments/acq-mech-3-limit-rationale.json` | agent-selected limits need their rationale recorded |
| ACQ-MECH-3-LOOPS | 3780:3852 | executable → checks | `engineering-geometry` (loop lengths), `locality`, `drc` | routed loops and return are measured |
| ACQ-MECH-4-BODIES | 3855:3947 | executable → checks | `courtyard`, `containment`, `dfm` | native geometry |
| ACQ-MECH-4-MODEL-ENVELOPE | 3947:4027 | executable → checks | `review-acq-mech-4-model-height` | model height against 8 mm envelope |
| ACQ-MECH-4-MATED-HEIGHT-SCOPE | 4027:4137 | judgment | `judgments/acq-mech-4-mated-height-scope.json` | scope correction; no absolute height claim |
| ACQ-MECH-4-ACCESS | 4137:4209 | executable → checks | `engineering-geometry` (access_obstruction) | measured |
| ACQ-MECH-4-ASSEMBLY-APPLICABILITY | 4209:4300 | judgment | `judgments/acq-mech-4-assembly-applicability.json` | captured-source geometry and process applicability |
| ACQ-DELIV-1-FILES | 4341:4609 | executable → checks | `geometry`, `courtyard`, `containment`, `locality`, `dfm`, `fab-export`, `cpl`, `fab` (with `assembly_profile`) | map/1 checks plus the expected export, CPL-rotation and orientation record; agreed with agent-566 as the binding for "placement files with the same … orientations" |
| ACQ-DELIV-2-REPRODUCTION | 4612:4850 | operational | criterion `public_build_and_resume` | proved only by the build journal (runs A/B of the contract) |
| ACQ-BENCH-1 | whole | physical → bench | as map/1, not performed | bench obligations |
| R-envelope-acquisition-* (8) | derived | executable → checks | as map/1 | envelope-derived obligations are executable by construction |
| ACQ-INTENT-P1 | prose | context | as map/1 | intent and authority boundary |

Changes from the retained map/1 in substance: `ACQ-DELIV-2` no longer
claims reproducibility through `netlist_parity` (its former check
`review-acq-deliv-2-topology` is the only base check id no longer bound;
the equivalent `netlist_parity` checks of the ELEC spans remain bound); thirteen prose-only sentences that
map/1 folded into check-bound clauses are now visible judgment obligations;
the supplier-confirmation sentence of ACQ-MECH-1 is a declared physical
obligation instead of being covered by stackup checks.

## Engineering questions for the judgment records

The briefs in the overlay name, per record, the questions and the captured
sources to answer them. They are the proposed scope of the qualified review;
no answer is recorded here. Summary:

1. **ELEC-1 temperature and unqualified conditions**: 25 C applicability of
   TPS7A20, ADS1115, BAT46W and passive limits with the interval operands;
   explicit non-claim of 0–50 C, hot plug, surge and unpowered drive
   (sources: regulator/core datasheet captures, `electrical-intervals.json`,
   `engineering-calculations.json`, `ENGINEERING-REVIEW.md`, `OPERATING.md`).
2. **ELEC-2 source evaluation**: traceability of bias (Samsung bias data),
   temperature, tolerance, dropout and dissipation operands; load allocation
   and DBV dissipation at 25 C.
3. **ELEC-3 back-power contract**: the forbidding instruction, the nets that
   would carry back-power, and the justification of the power-removal
   effective-capacitance bound from the BOM.
4. **ELEC-4 filter scope and error evaluation**: RC corner from captured
   values, no antialias claim; loading, settling and ADC error at the declared
   configuration from ADS1115 tables, stated with limits.
5. **ELEC-5 host bus and configuration**: host contract as instruction;
   address 0x48 from ADDR grounded; exact configuration words and read
   sequence from the register map.
6. **ELEC-6 high impedance**: why the no-drive-until-valid condition is an
   external obligation (no isolation on J2/J3 nets).
7. **MECH-1 bench scope**: no holes, no enclosure, and the 1.2 mm correction
   against JST PH 0.8–1.6 mm with the JLCPCB tolerance.
8. **MECH-3 limit rationale**: source or engineering rationale for the 3 mm
   pad limits and the loop-length limits in `geometry-model.json`.
9. **MECH-4 mated height scope and assembly applicability**: ePH 8 mm
   reference and the scope correction; captured-source body geometry and
   SMT versus through-hole process applicability under `dfm-profile.json`.

## Limits and pending dependencies

- **D1 polarity.** Agent-566's real run with these exact CPL inputs
  (acq-partb-2) reports 16 of 17 CPL rows assessed and D1 `polarity_unknown`,
  which makes the rotation report incomplete and `fab_package` unsupported.
  That is their run's result, not this preparation's; here `cpl` and `fab`
  are declared expected checks with status pending. No row is claimed
  complete and no orientation judgment is recorded. The bounded source
  investigation of D1 belongs to agent-566.

- This preparation compiles the typed declaration; it does not execute
  checks. Executing the 29 bound checks on a candidate needs the AGENTEE-519
  runtime and a build from the derived inputs (a new run, since the
  derived inputs differ from the retained v7 identity).
- The qualification consumer (AGENTEE-521 part B, agent-568) must declare the
  obligations file and judgments directory in the build plan and pass
  `obligations_raw` for typed inputs; until then `build run` on the derived
  inputs refuses as a map/2 without obligations.
- Judgment records and their witnesses are later acceptance steps once a
  package exists; declaring them here makes their absence visible, not
  satisfied.
- `ACQ-DELIV-2` is proved only by the journal of a cancelled-and-resumed run
  of the derived inputs (contract §2.2 run B), not by this compile.
- The physical dimension stays pending: supplier confirmation and bench
  tests are declared and not performed.
