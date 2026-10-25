# Agent-ee recovery plan — 2026-10-25

**Status: selected planning sequence; implementation paused by owner.** This plan reconciles [Argon, Fable and Astra](baseline.md) into one execution order. It supersedes historical dispatch instructions and the “next named capability” queue for future recovery work. It does not revoke engineering requirements, accept a new board, or release the telemetry-pod hold.

Start with [the finding register](findings.md), then the [Plane backlog](backlog.md). Evidence identities and preserved refs are in [baseline.json](baseline.json). The existing recovery parent, AGENTEE-512, is reused.

## Selected decisions

| ID | Selection and rationale | Alternatives / reopening condition |
| --- | --- | --- |
| RD-01 — finite delivery | M1 is the captured **17-part acquisition** seven-stage file-release workflow plus the owner-named **imported esp_prog** placement/routing/finalization regression. Acquisition demonstrates generation and packaging; esp_prog exercises a real imported board. | Do not force esp_prog through circuit synthesis or claim its standalone result is PCBA-ready. A missing required design input is explicit. Broader functionality remains M2/M3. |
| RD-02 — orchestration and placement | Keep `build_workflow` as the generated-design orchestrator, `placement_job`/prepared Rust search as the placement boundary, and owned kicad-route as adapter. Use one versioned intent/constraint meaning and final-native predicate set across generated and imported paths. Adapt existing block/side/mechanical machinery before writing replacements. | KRT algorithms are candidates for focused adaptation, not automatic defaults. Reopen replacement only after a matched constraint-complete comparison demonstrates benefit exceeding migration cost. No silent Python fallback. |
| RD-03 — evidence and release claims | Separate deterministic checks, software file release, engineering review, supplier assembly readiness and physical qualification. `build review` remains a diagnostic check rerun. Bind requirement methods and engineering judgments to the exact candidate and source; use supported external review when the claim requires it. | Do not turn checks-only into independent review, or make model availability necessary for every deterministic stage. No blanket “accepted” alias may imply supplier/bench completion. Existing consumers get an explicit compatibility mapping. |
| RD-04 — rules and manufacture | Preserve the sourced-rule authority policy. Show default advisories separately; require complete applicable sourced/justified release rules. Evaluate groups, mask, via/pad, orientation and return requirements against selected design/process evidence. | No universal 1.5 mm group gap, 3 mm passive radius, zero-via rule or inferred supplier rotation transform. Required unknowns refuse the dependent claim. Supplier preview remains pending until actually performed. |
| RD-05 — provenance and operations | Preserve input/candidate/tool identity, artifact hashes, declared-stage receipts, rollback, native checks and stale-evidence refusal. Separate historical integrity from qualification under current code; use a release manifest and scoped dependency invalidation. | Hashes may be generated/consolidated; they are not removed as a class. Freeze the **current** integrated router for M1, changing it only for a demonstrated M1 blocker with affected qualification. No rolling upstream parity race. |

These decisions are engineering selections under the current planning request, not new owner rulings. AGENTEE-514 records them and links the dependent items. They are settled enough to implement after the pause is lifted; none requires a separate architecture committee or another comprehensive audit.

## M1 — dependable representative workflow

**Exact representatives.** Freeze `build/acquisition-release/build.json` and revised `inputs/` with the seven-stage `release-build-v7` as historical output; preserve its original 1.6 mm specification as superseded history. Use `build/agent545/board-config-portfolio.json` and its exact hashed native esp_prog inputs, groups and placement intent, not an upstream placed reference substituted for the unplaced article. Record native footprint objects separately from populated parts. The baseline file records known output hashes; the first implementation item captures all input identities and access instructions.

**Supported initial envelope.** The captured low-voltage two-layer acquisition circuit and imported esp_prog article, with existing through-via routing, quarter-turn native placement and declared fixed interfaces. Unsupported side moves, keepouts, arbitrary outlines, input pours or other intents must refuse explicitly until qualified. This is a milestone boundary, not removal of those product requirements. The container is the first supported runtime; host diagnostics must be truthful, but duplicate native-host qualification is not an M1 gate.

Completion requires all of the following observable results:

1. A clean checkout plus documented, hash-verified input access and a pinned runtime reproduces acquisition through all seven **public** build stages. No undocumented preparation script is needed for this captured design. Capture source, binaries, image, native libraries and commands. Unsupported toolchain/input conditions refuse before expensive work.
2. Every declared requirement is enforced by an appropriate measurement/native check, supported by captured engineering judgment, explicitly unsupported, or explicitly assigned to a later physical/supplier obligation. A required software-file claim cannot pass with missing, failed, stale, skipped or irrelevant evidence. ACQ-DELIV-2 is proved by clean execution and resume, not netlist parity.
3. Final saved coordinates preserve all UUIDs, connectivity, locks, edge intent, supported mechanical rules and sourced hard pin/group relationships. Provide top/bottom views and a per-block membership/side/spread/pin-distance/exception report. Group constraints stay active through legalization and routing feedback. Limits must be captured **before** evaluating candidate acceptance, with source or explicit engineering rationale; failed candidates cannot be used to invent a passing threshold.
4. Acquisition has zero opens, no unresolved required ERC/DRC or engineering defects, and a package bound to the exact final native candidate. Show all advisory/ignored categories. Recheck export geometry supported by the existing roundtrip implementation against the sealed files. Assess CPL orientation under the declared profile; native rotations may be correct. Supplier readiness remains pending when preview is required. No order/upload is part of M1.
5. esp_prog completes the public imported-native path with zero opens/errors under the preserved project rules; its finalization gives readable artwork and profile-bound dispositions for all eight historical via/pad sites and remaining warnings. Resolve actual defects; no blanket severity downgrade or agent-issued owner waiver. This establishes the tooling result, not a newly synthesized or physically qualified esp_prog product.
6. Meaningful negatives reject: ignored/violated hard intent; a wrong ADC address or reversed diode despite clean ERC/topology; altered final CPL/package bytes; changed required inputs on resume. An interruption resumes from verified completed stages; finer in-stage resume can follow M2. A successful old run remains inspectable without becoming current acceptance.
7. Run the focused changed-path checks and one native complete-workflow regression. Establish a bounded clean strict-suite result for the release, recording actual unavailable prerequisites or failures; do not equate a self-selected roster with the whole suite. Classify unrelated historical test dependencies without deleting tests to obtain green. Keep a fast relevant change gate plus scheduled/full release qualification; a nightly test alone is not a substitute for checking changes.

If final grouping repair exposes a routing failure, stop at the failed criterion and reproduce it in the same article. Do not relax geometry, swap the workload, enable unrelated KRT features or restart the pod. Use the bounded recovery item only if evidence identifies the needed repair.

## Recommended order and dependencies

The backlog gives native Plane edges. Recommended serial order for the sole implementing agent is:

1. **Runtime and captured-input baseline**: supported container procedure, truthful doctor/launcher behavior and a runnable native acceptance harness. Preserve a failing baseline for missing acceptance semantics.
2. **Reject lost intent**: versioned validation at the public placement boundary, with the exact Astra ignored-field negative. This fix does not wait for supporting every field.
3. **Requirement methods and release states**: truthful diagnostic/file/review/assembly/physical claims, correct reproducibility mapping, prominent advisories and current decision record.
4. **Functional grouping**: reuse AGENTEE-479 for the normal job's final predicate and the minimum search repair needed to satisfy predeclared relationships. Keep the prepared incremental engine. The mask investigation is independent; fix it only after a decisive reproducer identifies the cause.
5. **Finalization and package semantics**: artwork/via-profile treatment for esp_prog; exact sealed export geometry and orientation assessment for acquisition. These can be implemented independently after their input/claim contracts exist.
6. **M1 closure (AGENTEE-515)**: run the complete representative cases, negatives and resume, publish one release record, then stop expansion until that record is sound.
7. **M2**: supported engineering preparation, scoped verification/invalidation and finer resumption. A bounded matched placement comparison may inform optimization; no speed target is invented from the audits.
8. **M3**: side/keepout/mechanical extension, selectively qualified plane recovery and QFN/BGA fanout, then remaining real-design coverage through AGENTEE-517.

Dependencies encode necessary inputs, not every preference in this order. Preparation can be developed once the runtime/input/acceptance contracts exist; performance comparison does not block correctness repairs. The mask question blocks only a mask-specific change. Later router integrations depend on their restriction-preservation decision, not on proving all KRT rows.

## M2 — engineering brief to captured inputs

Use a small acquisition variant with one changed interface/load requirement and retained component-source context. A fresh operator/agent follows the supported preparation procedure to produce requirements, evidence methods, parts/topology/layout, groups/locality and applicable calculations without bespoke hidden scripts. Reuse current formats, requirements capture, parts/binding helpers and model templates; preserve original prose and version changes with author/rationale. Every unsupported inference becomes a named question, not a default pass.

Validate circuit applicability and source conditions; derive rail/rating checks where supported by the netlist. Keep manual engineering judgment for model suitability and claims not executable. Record undocumented interventions as defects. Do not hardcode “four authored files” as a product requirement. In parallel, separate archival integrity/current qualification, connect placement checkpoint resume and reserve grading time. No claimed speedup without matched measurements.

## M3 — remaining required capability

1. Adapt side/layer occupancy and existing mechanical reservations to the normal prepared job: legal opposite-side SMT overlap, through-hole conflicts, authorized flips, attached/fixed keepouts, final source-bound replay, and a congested article where an eligible underside choice preserves functional relations.
2. Investigate each disabled router feature's protected contract. Prefer existing `campaign`, `blocked` and `barrier-from` recovery where compatible. Add one bounded plane/return-via stage and one QFN fanout slice with native grading and unchanged rules/poses; BGA is a subsequent representative within the fanout milestone, not a blanket enable switch.
3. AGENTEE-517 retains two/four/six-layer coverage, analog/power/clocks, buses/differential signals, hierarchical schematic use, supported nonrectangular/placement-area/pour cases and assembly-profile constraints. Select cases by uncovered requirements. Explicit AC series-chain matching is already integrated; preserve it. Broader DDR/package timing, asymmetric/coplanar models, advanced bus alternatives and general physical SI/EMC are later evidence-driven extensions.

## Owner-held product milestone

AGENTEE-518 and the historical AGENTEE-472/485 pod campaign remain **held**. A later owner release must precede new pod preparation or execution. At that point refresh current FOME requirements and component/mechanical decisions, identify superseded fixtures, and settle actual outstanding power/mechanical inputs before using the qualified tooling. Do not adopt Rev-2 dimensions, old connector or old branch output as current intent.

Supplier preview, fabrication/assembly orders, uploads and physical tests require their own authority and evidence. The recommendation is to keep the pod held through M1 and use the above software cases first. Releasing it early increases integration variables and does not substitute for a clean representative workflow.

## Bounded investigations and stop rules

| Investigation | Decision enabled | Decisive evidence / cap | What it blocks |
| --- | --- | --- | --- |
| Mask seed6 | Whether/how the normal mask model needs repair | At most 90 minutes; inspect exact captured mask and transformed poses, reload native candidate, compare its two DRC findings and one separated control. Stop with a root-cause reproducer or explicit unlocalized boundary; retain native rejection. | Mask-specific implementation only. |
| Matched placement (AGENTEE-513) | Focused KRT adaptation versus retaining search unchanged | At most half a working day, at most two complete runs per implementation with the same frozen article, hard constraints, CPU allocation, memory, seeds policy and overall wall budget. Include setup/export/native grade and final quality. Stop on missing mapping; report “not comparable”, not a speed ratio. | Performance/replacement choice only. |
| Router restrictions | Which plane/fanout/recovery features can preserve invariants | At most half a working day; trace exact flag origins and source behavior, one isolated native control/negative for each proposed feature family within that cap. Produce allow/adapt/retain-refusal table. Stop on a rule, copper, placement or budget regression. | Only the corresponding router integration. |

The remaining assembly/placement numeric choices are engineering inputs to their items: capture sources or explicit design rationale before acceptance. No blanket owner-signoff loop is added. The only immediate owner action needed to execute this backlog is a later instruction lifting the implementation pause; the current task ends with it intact.
