# M1 independent verification plan — agent-569, 2026-10-25

This is the acceptance preparation for AGENTEE-515 at immutable input
`dab756e56a6a5358c00983074565af0e8e09862c`. It maps all seven M1 criteria in
[the recovery plan](../recovery-plan.md) to public observations and negatives.
Agent-564 owns integration and acceptance under [execution.md](../execution.md).
This document delivers a verifier plan; subsequent candidate verification needs
its separate dispatch. No production code or design input is changed here.

## Articles and preserved evidence

Paths in this section are relative to the main checkout unless absolute. These
inputs are retained locally, outside Git; a clean checkout alone cannot supply
them. AGENTEE-519 must supply hash-verified access, origins/licences and a pinned
runtime. Copy into an isolated run; never overwrite either retained success.

| Article | Exact retained input and historical witness |
| --- | --- |
| Acquisition | `build/acquisition-release/build.json`, SHA-256 `d55768209c977d8c71b9c8d8623fb9a9ff38a8033bf80479ebaa4ae6f777766f`, and its complete `inputs/` dependency closure. Revised specification hash `fbf659b7909af7af4c370ce309c6138c661029261a9a4906237f86be45444373`; preserve `inputs/history/initial-qualification/spec.md` separately. |
| Acquisition history | `build/acquisition-release/release-build-v7/build-report.json`, SHA-256 `6451b3f3a27c8fe494e27e707a1f43eb837c67688e2dd0009c684183e8d5a109`. Its completed sequence is requirements, parts, scaffold, schematic, placement-routing, artwork, package. The producing revision is `466e6feb859e2cd9f34693155e24ccfe3b0aca8f`, per the retained baseline. Its historical `accepted:true` is not current qualification. |
| Imported esp_prog | `build/agent545/board-config-portfolio.json`, SHA-256 `2c34cbb527fde4e5ea528bdf87b3ee28c800ec70525a5d90efb02e6b58301c8b`. Preserve its native board/project and intent/groups, hashes below, with the original six-seed portfolio. |
| esp_prog history | `build/agent545/board-run-portfolio/job-report.json`, SHA-256 `f0b23ec1200af3a0c113d6156d7eeb283925074c29eae76c7c972e7b4154f7a2`; `final-a1/board.kicad_pcb`, SHA-256 `dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1`. The retained final native DRC has zero unconnected items, 258 violations and five ignored-check entries. The baseline identifies eight via-centre/pad sites needing profile assessment. |

The four esp_prog input hashes in configuration order are:

| File under `build/agent545/inputs/` | SHA-256 |
| --- | --- |
| `board.kicad_pcb` | `b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7` |
| `board.kicad_pro` | `9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395` |
| `intent.json` | `320cf961d58dab9aea164f34afc11099ae48f75fc0b100273a155d4d62330d3c` |
| `groups.json` | `d48e2a5d95f733be6d4bdee06736034a5e34acd68e6289edef00fe89b86837e8` |

All 16 non-document artifact entries in [baseline.json](../baseline.json) were
rehashed and matched during this preparation. The selected receipt is local:
`/home/halbritt/.cache/agent-verification/agent-ee-569-m1-plan/retained-input-check.json`.
That verifies retention, not a fresh native execution or complete input closure.
Count the acquisition's 17 populated parts separately from native objects;
esp_prog's historical 35 footprint objects include graphics. Reconcile population
against the captured BOM/parts evidence instead of treating object count as parts.

## Public command catalogue

Run these inside the pinned supported runtime delivered by AGENTEE-519, using
its documented mounts and captured-input manifest. `ACQ_PLAN` and `ESP_CONFIG`
select isolated copies of the exact inputs above; record any path-only rebinding
and both config hashes. The raw esp_prog intent is initially a required refusal
under 520. A later derived representation is eligible only under the source-bound
479 compatibility obligations at main commit
`46532a013c0e897b77af819ed7a7c8f0b1a19496`, recorded in
`docs/planning/2026-10-25-recovery/intent-compatibility.md`.
`OUT` is a new writable evidence directory. `FINAL_BOARD`,
`FINAL_GROUPS`, `FINAL_INTENT`, `FINAL_SCHEMATIC` and `REVIEW_DIR` are selected from the completed
candidate's receipts, never an earlier or unfinished attempt. These are command
templates for later dispatch, not claims that the commands passed here.

| ID | Command |
| --- | --- |
| D | `agent-ee doctor --checks-only` |
| B0 | `agent-ee build run "$ACQ_PLAN" --out "$OUT/acq-checkpoint" --through schematic` |
| B | `agent-ee build run "$ACQ_PLAN" --out "$OUT/acq"` |
| BR | `agent-ee build run "$ACQ_PLAN" --out "$OUT/acq" --resume` |
| Q | `agent-ee build review "$OUT/acq" --out "$OUT/acq-diagnostic" --max-seconds 1800` |
| P | `agent-ee place run "$ESP_CONFIG" --out "$OUT/esp"` |
| PR | `agent-ee place run "$ESP_CONFIG" --out "$OUT/esp" --resume` |
| M | `agent-ee place metrics "$FINAL_BOARD" --groups "$FINAL_GROUPS" --intent "$FINAL_INTENT"` |
| N | `kicad-cli pcb drc --format json --severity-all --refill-zones --output "$OUT/final-drc.json" "$FINAL_BOARD"` |
| E | `kicad-cli sch erc --format json --severity-all --output "$OUT/final-erc.json" "$FINAL_SCHEMATIC"` (acquisition) |
| C | `agent-ee review "$NEGATIVE_SCOPE" --out "$OUT/negative-review" --checks-only --max-seconds 1800` |
| V | `agent-ee verify "$REVIEW_DIR"` |
| R | `agent-ee render3d "$FINAL_BOARD" --out "$OUT/final-views" --jobs 1 --timeout 300` |
| S0 | `AGENT_EE_REQUIRE_TOOLCHAIN=1 python3 scripts/strict_suite.py --jobs 1 --module-timeout 3600 test_build_review test_parts_stage test_placement_job_rules test_placement_job_guards test_placement_job_refusal` |
| S | `AGENT_EE_REQUIRE_TOOLCHAIN=1 python3 scripts/strict_suite.py --jobs 1 --module-timeout 3600 --render-mode run --summary "$OUT/strict-summary.md"` |

For C, `NEGATIVE_SCOPE` is the ordinary candidate scope for an isolated negative,
with its original requirement oracle and supported check ordering preserved;
each negative uses a separate new output directory. It invokes existing checks,
not a second implementation of the builder. Keep both the intact positive and
the mutation, including valid custody for semantic negatives. Native ERC and DRC
reports must be parsed; neither default exit status is a defect-count oracle.

The command surface is grounded in `src/agent_ee/{cli,build_workflow,placement_job}.py`
and `scripts/strict_suite.py`; [build-workflow.md](../../../build-workflow.md) and
[placement-job.md](../../../placement-job.md) describe the existing journals.
`place` exposes only `run` and `metrics` at this input: there is no public
preflight-only or imported-finalization subcommand. AGENTEE-519 uses the existing
public run/CANCEL boundary to retain a capture checkpoint before search, recording
cancellation honestly; its harness must document that actual invocation, without
a second validator. After 520, raw esp_prog may refuse before this checkpoint.
AGENTEE-523 must supply the public finalization route
before the corresponding rows can close. Do not invent flags to fill these gaps.

R supplies top/bottom/isometric views when captured model dependencies exist.
Retain a readable native 2D artwork view as well if 3D obscures markings; the
finalization delivery must document its actual command. Inspect the images.
Views alone establish neither grouping nor electrical or assembly acceptance.

## Verification matrix

Each row needs the actual argv, exit status, stdout/stderr, source/runtime and
input identities, final native hash, check IDs and evidence paths. A command's
zero exit is insufficient: B0 returns a nonaccepted checkpoint, M's group spread
is descriptive, N can succeed with warnings/ignored categories, and V verifies
custody rather than engineering sufficiency. Expected negative refusals also
need a matched unmodified control, exact offending path/check and retained output.

| Case / M1 criterion | Public exercise and assertion | Evidence / decisive negative | Earliest useful execution; closure dependency |
| --- | --- | --- | --- |
| A — runtime/input access; M1.1 | D, B0 and P through its reported readiness phase. A second clean operator resolves every captured dependency; actual launcher/interpreter, KiCad/pcbnew, planner binary/source receipt and router match the declared runtime. B0 has exactly four completed stages and `accepted:false`. | Input closure manifest, image digest/ID, source/router commits, binary/library hashes, command logs and generated native files. Missing planner/router/library, wrong input hash, stale launcher and unrelated executable controls refuse honestly before expensive work. | Hash/help inspection now; native through-schematic and readiness checks after 519. A diagnostic failing later is retained rather than called accepted. |
| B — seven acquisition stages; M1.1/M1.2/M1.4 | B plus Q/V on its final selected candidate. Exactly seven ordered completed stages, verified trees/receipts, exact final native/package binding and explicit diagnostic/file/review/supplier/physical states. No undocumented preparation. | New `build-report.json`, every completed stage and package review; compare the full authored requirements including paragraph spans and derived envelope requirements. Reordered/aliased stage, missing required check, `skip`, stale judgment or irrelevant check mapping must block its claimed software-file result. | Capture a failing baseline after 519; complete closure after 520/521/479/524. A diagnostic Q rerun is not independent engineering review. |
| C — final placement invariants; M1.3 | P/B, then M/N and R on each final saved board. UUIDs, connectivity, locks/fixed poses, edge intent and supported mechanical rules survive materialization, legalization and routing feedback. Report each block's members, side, spread, pin distances and exceptions. | Compare native input/placed/final inventories and exact rule files; final predicates bind the saved native hash. Predeclare sourced/rationale-backed limits before grading. Move a fixed part, connector to the wrong edge, or a supported hard-locality endpoint beyond its declared bound; a DRC-clean board must still fail. | Existing preservation tests after 519; complete group predicates/report after 479. Current M emits maximum member-anchor spread but does not enforce grouping acceptance or supply the full required report. |
| D — acquisition electrical/native claims; M1.2/M1.4/M1.6 | B/Q plus N/E and C for negatives. Zero opens; no unresolved required ERC/DRC or engineering defects. Check methods match the actual requirement, conditions and candidate. Show every warning and ignored/advisory category. | Requirement-to-method register, native reports, actual analysis results and candidate/source-bound engineering judgments. Change ADC address away from required `0x48` or reverse D1 in separate copies while retaining the requirement oracle; even if ERC is clean, the electrical claim must refuse. Regenerating a matching connectivity intent cannot redefine the requirement. | Record baseline after 519; correct method/claim closure after 521. Use `adc-ties` and the existing power analyses as starting evidence, not an assumption that they catch both mutations. |
| E — exact package/orientation; M1.4/M1.6 | B, Q/V, C for negatives and the ordinary `fab_package` result. The package names the final board, and supported roundtrip geometry is checked against the actual sealed bytes. Assess native/CPL orientations under the declared profile and retained exceptions. | Sealed manifest, exact Gerber/drill/BOM/CPL hashes, roundtrip comparison, rotation assessment and preview obligations. Mutate CPL angle/side or export geometry in a copy: ordinary V must reject broken custody; separately regenerate valid seals around semantically wrong bytes and require the existing package/roundtrip checker to reject the mismatch. | Existing package controls can run after 519; finite acquisition closure after 521/524. Preview-required supplier state remains pending. No upload/order. |
| F — imported esp_prog finalization; M1.3/M1.5 | P then the public finalization route supplied by 523, followed by M/N/R and its profile assessment. Zero opens/errors under preserved rules; readable markings; every via/pad site and warning has a candidate/profile-bound disposition. | Before/after board hashes, artwork mutation proof, full final warning/ignored inventory and per-site assessment for the eight historical via/pad sites, including any sites added/removed by the new result. Unresolved disallowed site, illegible polarity marking or changed protected geometry blocks. Blanket severity reduction/owner waiver cannot pass. | Baseline P after 519/520; closure after 479/523 and applicable 521 states. If a native mask blocker appears, preserve it and use 522 only for that blocker. An imported placement result is not a generated or assembled product. |
| G — ignored input/intent; M1.3/M1.6 | P for the exact Astra public ignored-intent bundle and its control; B at the equivalent input boundary. Independently test keepouts, `assembly.sides:"B"`, `blocks[].side:"B"`, combined case, unknown nested key, malformed value and supported-constraint violation. | Reuse `/home/halbritt/.cache/agent-audits/agent-ee-556-20261025/probes/public-ignored-intent/inputs/` and `probes/ignored-intent.json`. Rebind/hash copied paths explicitly. Refusal identifies the offending path and produces no eligible candidate/search result. The original esp_prog file itself must refuse after 520 until its richer declarations are handled; acquisition and supported-contract inputs are immediate positive controls. After 479, verify original-to-derived hashes, every field disposition, supported problem constraints and final native identities. A silently filtered intent, unmapped operative field or substituted representative must fail even if DRC passes. | Reproduce baseline after 519; expected raw/unsupported-input refusal after 520; original esp_prog compatibility and final supported-constraint enforcement need 479. Input rejection alone does not prove final enforcement. |
| H — interruption/resume; M1.1/M1.2/M1.6 | Use an isolated acquisition run through parts, create its `CANCEL`, resume through schematic and expect interruption, remove only that marker, then resume toward package. Exercise P/PR similarly at a completed checkpoint. | Completed stage trees/receipts and candidate identities remain unchanged; attempts are retained; source/runtime changes refuse resume. Independently alter required spec/intent/rules, a completed native file and runtime identity in separate copies. Nonempty foreign outputs are preserved. Keep old v7 inspectable without promoting it to current qualification. | Stage-level CANCEL/resume after 519; complete final-input ACQ-DELIV-2 proof after B closes. A supported mid-stage interruption restarts from the last verified stage; finer in-stage resumption is M2, not an M1 prerequisite. |
| I — change gate/release; M1.7 | S0, additional touched-path modules, one complete real native workflow regression covering the cases above, then S without a module roster. Record resource allocation, module and total wall bounds, native coverage, actual failures/prerequisites and release status. | Full roster/results, logs, input partitions, no hidden skips or fabricated outputs. `tests/toolchain.py` names `fome-mc-store`; demonstrate any missing partition's irrelevance before treating it as a residual. Failure affecting the workflow blocks M1. A targeted roster passing cannot stand in for S. | Focused checks after 519 and each change. Final release after all row dependencies. Schedule full qualification in addition to the relevant per-change gate. |

The full workflow run retains the captured acquisition placement budget of
900 seconds, esp_prog's 1200-second overall job budget and acquisition's
1800-second verification timeout. Do not renew a failed attempt's budget or
discard its output. The final verifier dispatch must set a finite total wall
limit for the complete workflow and strict suite; it must cover legitimate stage
and module bounds rather than silently shortening them into unavailable coverage.
This preparation starts no board job or suite run.

## Builder acceptance checks and execution order

These are scoped clarifications to the two Sol builder items in
[backlog.json](../backlog.json), not duplicate implementations or new owner rulings.

* **519 / RUNTIME:** the acceptance definition permits landing native generation
  through schematic and placement readiness before later semantics pass. Require
  the full transitive input closure, origins/licences, exact image and planner
  receipt, explicit path rebinding and launch interpreter evidence. A historical
  build config alone is insufficient. Its promised harness must record actual
  failure stage/result and have an operator-visible command using the existing
  public run/CANCEL capture-checkpoint boundary; there is currently no
  `place --preflight` flag and no second validator is needed. After 520, raw
  esp_prog refusal is the truthful recovered baseline, not a runtime regression.
  Missing-Rust and stale-launcher negatives need an
  unmodified positive and protection for unrelated executable paths. Neither
  doctor success nor B0 closes RELEASE.
* **520 / CONTRACT:** require each ignored field independently and in combination,
  both public place/build boundaries, version/type/nested-key errors, early
  rejection before search and no eligible candidate. The captured esp_prog intent
  already includes `assembly.sides:"F"` with observed-baseline provenance and
  metadata. The exact original is a required negative until 479 maps those declarations;
  it is not an immediate 520 positive. Preserve all original board/project/intent/
  group hashes. Require a versioned derived representation with source binding
  and a disposition for every field; compare metric semantics and final native
  faces, envelope, zero-outside predicate, edge/fixed/proximity constraints, empty
  declarations, group relationships and provenance. Reject unmapped operative
  semantics or silently filtered/substituted inputs. Metadata is not an executed
  check. Compare the supported captured problem constraints. Distinguish unsupported
  declarations rejected at input from supported hard intent violated at final
  native grading; the latter remains a GROUPS/RELEASE obligation.

For the 479 mapping, compare meanings as well as field coverage: a pad-edge
distance cannot silently become a pad-centre distance, a zero-outside predicate
needs matching geometry semantics, and observed faces/defaults retain their
original authority. Validate declared empty collections explicitly; nonempty
unsupported forms and foreign unknown schema versions refuse. Qualitative group
`near` relations remain visible without inventing universal hard distances.

Run A and early H first after 519; run G after 520 without waiting for package or
artwork changes. Method/claim fixtures and existing package negatives can also
run before the final designs are repaired. C/D/E/F closure waits only on the named
producer contracts/fixes. Preserve failures in the same articles. Do not add a
matched-speed, full-KRT-parity, new circuit-synthesis or telemetry-pod gate.

Existing regression surfaces to reuse are `tests/test_parts_stage.py` (public
interrupted/resumed native generation), `tests/test_build_review.py` (custody),
`tests/test_placement_job{,_rules,_guards,_refusal}.py` (identity/native preservation
and cancellation), and `tests/test_fab_{package,roundtrip,consistency}.py` (package
and export negatives). They do not collectively establish the complete M1 run.
The captured requirement map still maps ACQ-DELIV-2 to `netlist_parity`; replace
that proof through the 521 contract with actual clean execution/resume evidence.
The explicit requirement in `inputs/ENGINEERING-REVIEW.md` already says so.

## Release record and preparation result

The later release record must list the exact landed source/runtime/input pins,
all cases A–I with pass/fail/blocked and evidence locations, predeclared engineering
limits, requirement-method decisions, final native/export hashes, warning/site
dispositions, explicit review/supplier/physical states, changed-path checks and
full-suite result. Required failures stop M1; named residuals need a bounded
irrelevance argument. Agent-564 accepts that record before M2/M3 expansion.

Preparation performed: current instructions/backlog/source/tests inspected;
16/16 retained artifact hashes matched; source CLI help for build run, place,
place metrics and strict-suite options executed successfully in the isolated
worktree. No native acceptance tests, electrical mutations, board campaigns or
full strict suite were executed. Remaining prerequisites are 519's runnable
input/runtime/harness delivery, 521's methods/states contract, 479's final group
predicate/report and original-intent compatibility mapping, 523's public imported
finalization route and 524's exact-package
assessment. These are dependencies to verify, not claimed defects in unreviewed
future deliveries.

Planning provenance: Pincite retrieval-state gate passed at release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, retriever
`retriever-ec995ecdd083b2c8`. Evidence packet `pkt-6608067e0bd39213`, content hash
`6608067e0bd39213dff5db230ddc36c507df90da1ffdcc1f7b1425f3906034ad`, supports
the narrow use of observable contract assertions and negative controls
(`implementation-risk-driven-tests`); repository criteria supply the oracles.
Other packet obligations concerning incident restoration, architectural change,
test-performance history and broad repository assessment are nonmaterial to this
documentation-only plan. Fresh defect reproductions and candidate performance
remain material to later verification; this preparation abstains from declaring
their outcomes. Alternative: wait for every producer before designing checks;
rejected because early input/resume/refusal checks have independent value.
Reopen this matrix if public commands, input identities or M1 criteria change.
