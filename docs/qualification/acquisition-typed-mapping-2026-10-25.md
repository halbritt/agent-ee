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
| Overlay | `fixtures/acquisition-typed/overlay.json`, sha256 `d65c660b8ccf1c843ca8151dae1d2c21e2943fab74f3f4b7e914623cc8232b8d` |
| Derived `requirement-map.json` (map/2) | sha256 `f2adf786ed345274fdcdaa8658e797fdf6c6a5ac758605d6b200c2aa2120ea95` |
| Derived `analysis/electrical-intervals.json` | base `8998b329e058d39d48bd3568240b58b4904ba079460d46032105ef5cc51654a7` → derived `93d049c4d7bf30b2e74df37817e8bc3dbfa2d4de4b29312e7203693e6fe2f203` (requirement ids and source locators re-bound to spans; parameters, assertions and bounds unchanged) |
| Added `obligations.json` | sha256 `851029c041ec92aaa96133f7720e507b95217c6989b85ecf8e3c8863ce7bbdc2` |
| Unchanged files | 196 of 198 |
| Added CPL inputs (`cpl/`, agent-566 revision) | `assembly-profile.json` `f8182f507c12a5e9b5113b545f675d5960efeb5c2f8ad977b649d5dbaa2ab755`, `jlc-cpl-policy.json` `36b349c7a2ed1622e03a5193574c8b60c75a23ce6b2dde041edcfbb1c175db4b` (= profile `policy_sha256`), 11 `supplier/<LCSC>.raweasy.json` + 11 `.provenance.json` captures; every hash is in the overlay and verified on copy; third-party CAD bytes stay outside Git |
| Added checks | `fab-export` (`fab_export`) and `cpl` (`cpl_rotation`, policy + 11 supplier provenance files) declared verbatim from agent-566's acq-partb-2 scope; `fab` gains `assembly_profile: cpl/assembly-profile.json` and nothing else |
| Typed receipt | `agent-ee.requirement-map-receipt/2`, `method_binding: typed`, compiled against the accepted `release-build-v7` scaffold candidate `stages/03-scaffold-d1c92e58…/product/inputs` overlaid with the three derived files; `scope_sha256 82c27ec7e467d82a6e3bf10dae3e15f212ea502c55bb5d17a8efdb690d593d8e`, `baseline_id 8ecc4771c66e7756b32d4f4329cc93a8e9ba665760333411f6acc8aa98709d8d`, `coverage_id 10e725a2ade3f5e2745be8e7104d3d4c5dc82207872003721422310d8d455bca`; compile root inventory `fb589578c992a5900f30bddbfc671778e00158043b68f912fb8836d0847c46ed` (27 overlaid files); compiled on main at 903721fb, which strips the profile from the delegated export |

**Inventory scopes preserved.** The acquisition capture is 198 regular files
(inventory sha256 `718b4619c597efdfb730727804614546fac174feacc93ec0fec2a7a39084cd54`
in the manifest); 196 are copied unchanged, two are derived with both hashes
recorded, and 25 files are added. The esp_prog article is a separate scope
that this preparation never reads: the recovery baseline's frozen esp_prog
configuration names `board.kicad_pcb` `b4aa540d…93eef7`, `board.kicad_pro`
`9a9ab694…b6395`, `intent.json` `320cf961…330d3c` and `groups.json`
`d48e2a5d…6837e8`, all untouched. The combined preservation figure of 204 in
`fixtures/recovery/inputs.json` (204 hashed entries) is 198 acquisition
input files + the acquisition `build.json` + the esp_prog
`board-config-portfolio.json` + four esp_prog input files. No original
input in either scope was changed.

**Revision history of this preparation.** `f30a0701` (first delivery, base
`ae477fc7`) was rebased without content change onto `903721fb` as
`0766bc6b` and then onto `82133d6d` as `e2572d98`; the CPL extension
`880db1c7` became `abd191be` on the same rebase. The rebased commits
supersede the earlier hashes; the frozen delivery is the branch head named
in the chief notice.

Accounting of the derived declaration: 47 receipt rows; modes `checks` 29,
`judgment` 14, `bench` 2, `operational` 1, `context` 1, `limitation` 0;
obligation kinds executable 29 (including the 8 envelope-derived rows),
judgment 14, physical 2, operational 1, context 1. Every check id bound in
map/1 remains bound; no check definition was re-authored. Fourteen judgment
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
| ACQ-MECH-3-LOOPS | 3780:3834 | executable → checks | `engineering-geometry` (loop lengths), `locality`, `drc` | "final routing shall preserve short corresponding loops": loop lengths are measured |
| ACQ-MECH-3-RETURN-PATHS | 3834:3852 | judgment | `judgments/acq-mech-3-return-paths.json` | "and return paths": loop-length, locality and DRC measurements do not establish return-path adequacy; candidate-bound copper judgment, no universal return-via threshold |
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

### Sentences reviewed and kept executable

Checked after the chief's ACQ-MECH-3 correction, without widening into a new
audit: `ACQ-ELEC-3-REMOVAL` 1822:1929 ("evaluated with effective capacitance
bounds") stays bound to `power-removal`; the bounds are authored operands and
their justification is a question in `acq-elec-3-back-power-contract.json`,
since the sentence has no byte split that would separate them.
`ACQ-ELEC-2-RAIL` 1385:1546 ("all internal loads included") stays executable;
the load allocation question lives in `acq-elec-2-source-evaluation.json`.
`ACQ-MECH-4-ACCESS` 4137:4209 ("mating columns and cable exit clear") stays
bound to the `access_obstruction` measurement; the cable-exit question beyond
the modelled columns is added to `acq-mech-4-mated-height-scope.json`.
`ACQ-NATIVE-2` warning dispositions are the existing waiver mechanism, and
`ACQ-DELIV-1`'s "absent or stale evidence must reject the package" is the
package gate itself. None of these drops a judgment obligation as direct as
the return-path sentence.

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
8. **MECH-3 limit rationale and return paths**: source or engineering
   rationale for the 3 mm pad limits and the loop-length limits in
   `geometry-model.json`; continuity of the B.Cu return under U1, U2 and the
   filters beneath each measured loop on the final candidate, with the
   captured plane declarations and the removed-plane negative as sources.
9. **MECH-4 mated height scope and assembly applicability**: ePH 8 mm
   reference and the scope correction; captured-source body geometry and
   SMT versus through-hole process applicability under `dfm-profile.json`.

## Supplemental revision: D1 polarity evidence (AGENTEE-524)

Authorized by the chief once agent-566 supplied the evidence file. The
overlay now pins agent-566's `cpl-inputs-rev3`, which is rev2 (24 files,
all hashes unchanged) plus one file, `cpl/polarity/C83152.polarity.json`,
sha256 `a3fff2b33cb0669108a52cd0847b49aeb4aa54ee1ef5172e3a2208d6c115648c`
(`agent-ee.component-polarity/1`, LCSC C83152, MPN BAT46W-7-F, pin polarity
1 = cathode / 2 = anode, supplier capture `9939a5a6…`, manufacturer source
the captured BAT46W PDF `3bc31f4a…` page 1 "Polarity: Cathode Band",
package features gge1050/gge1083/gge1003/gge1002, symbol features
gge20/gge19/gge3/gge10, drawing-fidelity limitation). Every binding in that
file was re-verified against the bytes here: the supplier capture hash, the
datasheet path and hash inside the 198-file base, and the presence of every
feature id in the capture. The declared `cpl` check gains
`polarity_evidence: ["cpl/polarity/C83152.polarity.json"]`; nothing else
changes. The record is supplemental interpreted component data, not a
judgment record and not supplier confirmation; the independent inspection
(agent-565, accepted by the chief as a software inference only) is retained
by the chief outside this repository.

Identities of the supplemental revision (`prepare --added-input
cpl=<rev3>`, accounting only, exit 0): overlay sha256
`d3c49916567d7aa35e6ff64f33eeee44449e770a27c119bdebcb154e0d2098bd`;
derived map `5b6780041d9312acd3d38877c4731aea3e9262f3e612814d1acecaa61d8229d0`;
obligations unchanged `851029c0…`; base 198 files, 196 unchanged, 2
derived, 26 added; 47 rows, 14 judgment records declared, 0 present.

**Compile pending.** On main at `82133d6d` the capability validator refuses
the new parameter: `cpl_rotation params must be exactly {export_check,
geometry_check, policy, supplier_footprints, source_tolerance_nm}` (exit 2;
the derived inputs are complete, no receipt is produced). The typed receipt
for this revision therefore waits for agent-566's `polarity_evidence`
implementation to land; until then the last compiled receipt is the
pre-supplement revision recorded above (`scope_sha256 82c27ec7…`).

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

## Chief independent verification of the revision without D1 supplemental data

Agent-564 inspected `268b3e8f0d4489fa4e1ee3c92ad0c0f0b30f6566` in a
separate immutable managed checkout and ran five strict native modules:
`test_typed_overlay`, `test_requirement_map`, `test_requirement_map_envelope`,
`test_requirement_scope`, and `test_spec_record`. All 88 test executions passed
with no skips, including the retained 198-file acquisition input and 24-file
CPL revision, preservation negatives and real requirement-scope checks.

An independent preparation and compilation against the retained v7 native
scaffold reproduced scope `82c27ec7e467d82a6e3bf10dae3e15f212ea502c55bb5d17a8efdb690d593d8e`
and baseline `8ecc4771c66e7756b32d4f4329cc93a8e9ba665760333411f6acc8aa98709d8d`.
The 47 rows include 14 declared judgments with no records, one pending
operational obligation and two pending physical obligations. This accepts the
preparation procedure and clause accounting, not a new build, engineering
concurrence or assembly result. The later D1 supplemental capture changes the
input identity and requires its own validation.

Evidence is retained at
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/typed-overlay-268b3e8f/`;
`typed-overlay-native.log` and `typed-overlay-compile.log` are in its parent.
Ten tested source/test/overlay hashes are checked again after integration rebase.
