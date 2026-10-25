# Recovery execution — 2026-10-25

The audit consolidation was completed and pushed as `9c290c1622b4052de948516418ab1b3ee15cfb82` with implementation paused. Its finding register, baseline, backlog and Plane readback files record that completed planning snapshot. Live execution follows this record and Plane; historical receipts remain unchanged.

## Authority and scope

Direct owner instruction to agent-564, 2026-10-25, after the planning request:

> There are 5 agents already spawned, 565 through 570, once the plan is complete, you take over as chief integrator, create a goal for yourself to parallelize the work and utilize the capabilities of the agents in question, sol and opus for builders, fable and argon for more complex tasks.

Agent-564 completed consolidation before creating the implementation goal. This instruction authorizes parallel execution of the reconciled recovery backlog and supersedes the older sole-agent direction. AGENTEE-518 and existing telemetry-pod campaigns remain held. It does not authorize fabrication, purchases or project uploads. RD-01–05 and all sourced engineering constraints remain in force.

Cairn shows six sessions in the stated range. Native-session reports first corrected two initial registry labels: agent-566 was Opus 5.5 (`claude-opus-5-5`), and agent-569 is Sol (`gpt-6.1-sol`). At 13:08 PDT agent-566 reported its session had switched to Fable 5.1 (`claude-fable-5-1`); the chief did not initiate that switch. Its current work is assembly-profile assessment and implementation. The initial allocation and dispatch receipt preserve earlier snapshots. Use these existing sessions without switching their models or launching substitutes. Subscription authentication or quota failure stops the affected operation; no OpenRouter fallback.

## Initial allocation

| Holder | Observed model | Initial responsibility | Boundary |
| --- | --- | --- | --- |
| agent-564 | Astra | Chief integrator, Plane state, acceptance and serial main integration | Owns final acceptance and cleanup; no board campaign restart |
| agent-567 | Sol | AGENTEE-519 runtime, captured inputs and native harness | Implementation authorized now; no acceptance-semantic expansion |
| agent-568 | Sol | AGENTEE-520 unsupported-intent rejection | Inspect and prepare focused implementation now; integration and native qualification wait for 519 |
| agent-565 | Fable | AGENTEE-521 evidence-method and qualification contract | Define the concrete compatibility contract first; dependent implementation follows 519/520 |
| agent-566 | Opus 5.5 (initial registry label corrected) | AGENTEE-524 exact-package and assembly-profile contract, then package builder | Selected contract precedes implementation; 521 owns the build-workflow consumer |
| agent-569 | Sol (initial registry label corrected) | M1 acceptance-case preparation, bounded supplier-reader investigation and repair | Targeted evidence and focused changes; independent acceptance of its repair belongs to the chief or another reviewer |
| agent-570 | Argon | KICADROUTE-104 bounded restriction-preservation investigation | No enabling flags; bounded allow/adapt/retain-refusal recommendation with source and native evidence |

This allocation overlaps independent preparation, not unmet acceptance. Native Plane blockers remain in place. The first three items to land are 519, 520 and 521. Grouping follows; finalization and package work can proceed independently after their contracts. Mask investigation can run once 519 supplies the runtime. M1 closure precedes expansion to M2/M3.

## Delivery and integration contract

Use `git-wt create <slug>` under the managed cache, never `/tmp`. Each dispatch names the immutable input commit, exact issue, owned scope, finite first checkpoint and decisive acceptance evidence. Preserve the captured acquisition and esp_prog input originals, local branches and retained artifacts. Do not rewrite the audit or planning snapshot to make later work appear pre-existing.

Builders commit focused changes on their assigned worktree branch and return the commit, checks, artifact locations and remaining failures to agent-564 through their existing Cairn session. They must not push main, switch the shared root checkout, alter another holder's files, or close a dependent issue themselves. A handoff is pending integration, not accepted completion. Agent-564 reviews the diff, obtains independent verification proportional to the change, runs the necessary checks, lands and pushes one change at a time, then removes only the newly owned branch/worktree after preserving its evidence. End state must be clean with no newly orphaned worktrees or unmerged deliveries.

Keep code changes tied to the existing issue acceptance criteria. Reproductions have explicit time/run limits; report a decisive failure or an unresolved boundary instead of widening the audit. A failed dependency blocks only its consumers. Do not invent geometry thresholds, degrade native rules, remove provenance, auto-pass unavailable reviewers, substitute a nicer design, or call intermediate board output manufacturing-ready.

Plane is the current state ledger. Agent identity is recorded in issue descriptions/comments because Cairn agents are not Plane user accounts. The integration record must distinguish dispatched, acknowledged, implemented, independently verified and landed. A sent Cairn event alone is not proof of work.

## Integration decisions

- [Retained esp_prog compatibility](intent-compatibility.md): 520 truthfully refuses the original richer unsupported intent; 479 must supply an explicit source-bound mapping before M1. Preserve all original declarations and the same design. This corrects the conflicting immediate-pass criterion in the planning snapshot, not the overall delivery requirement.
- 519 and 520 have separate write ownership: 519 uses the existing public cancellation/checkpoint boundary for runtime preflight; 520 owns placement intent validation. Do not add a parallel preflight validator.

## Integration ledger

This ledger records accepted integration separately from worker delivery. A design document landing does not complete its implementation item.

| Item | Inspected or integrated evidence | Current result |
| --- | --- | --- |
| AGENTEE-515 verification preparation | `fdedf17e2736a37a58a2250f8a59abfcb9cd1e82`, [M1 verification matrix](contracts/m1-verification-matrix.md) | Landed and pushed; native M1 acceptance remains pending |
| AGENTEE-524 package design and sealed-byte checks | Design `21a39d0f374de56d0b6f5f7d58ffd21cdb7678a4`; part A `877b41536b80669a0b399366b0c8132e5afbee00`; [native verification](contracts/AGENTEE-524-partA-verification.md) | Landed and pushed; chief strict 85 tests/no skips passed. Exact sealed acquisition bytes pass; an exporter fault passes custody but fails roundtrip geometry. Agent-566 owns profile/orientation part B; agent-568 owns the build consumer |
| AGENTEE-524 U1 reader | Investigation `5b714a6d8109ba277664f37e78d0548d5a006f0d`; repair `b74cac20f0281fb34309b265526621f03f2b796d`; [repair evidence](contracts/AGENTEE-524-RECT-repair-evidence.md) | Landed and pushed; chief strict 55 tests/no skips plus exact private capture replay passed. One-to-one corner correspondence preserves the existing tolerance and declared geometry. This does not assess assembly orientation |
| AGENTEE-519 runtime | Implementation `61c6cd49052487fa18fda7a55cec07618d10b3e6`; [full result](runtime-519-full-agent-567.md) landed `c6446c99b0d53617079328228a73508c73d3d03c` | Plane Done. Chief verified 13 receipt/native hashes. Acquisition completed seven stages with legacy acceptance; esp_prog failed before search on duplicate fixed-mark reference. All 204 originals preserved. REC-30 / AGENTEE-528 owns that separate repair; M1 remains open |
| AGENTEE-520 intent | Landed `01227b586da29660d55b070f6677da539571deac`; 34 strict tests including all previously pending planner guards, docs/origins passed | Pushed and worktree removed; Plane Done. Chief exact-input replay confirms original esp_prog refuses before capture at `intent.assembly.sides` while acquisition still reaches schematic. Raw esp_prog compatibility remains 479 work |
| AGENTEE-521 claims | Contract `e7a92977fda105e322c2775527834882ca950bdc`; method compiler `ae477fc774ba1d7d3c6e5d1a91242bc708d3f0f7` | Compiler landed after chief strict 76 native tests/no skips and docs/origins. Agent-568 implements qualification/build consumer; agent-565 prepares actual acquisition typed inputs. Legacy receipt coverage remains unknown; no fabricated judgment concurrence |
| KICADROUTE-104 restrictions | Selected contract `7bb195de14f6874202df196830272d0e17700770`, [evidence and decisions](krt104-router-preservation-contract.md) | Plane Done; no feature enabled. M3 adapters must preserve rule/pose/copper/pair/budget protections and generated pour-serving promises. QFN control proves preservation only, not native DRC |
| AGENTEE-528 fixed-mark identity | REC-30, observed at runtime source `61c6cd49`, unchanged parser at `877b4153` | Agent-567 assigned narrow identity repair from `c6446c99`; native graph is 519 → 528 → 515. Grouping contract preparation remains independent |
| AGENTEE-479 compatibility/grouping | Agent-570 assigned original esp_prog field mapping and final-native grouping contract | Bounded design preparation; no stripped input or unsourced universal gap. Historical parent 472 remains held |
| AGENTEE-522 mask mismatch | Agent-569 assigned one retained failing article and one legal control | Bounded localization only; no full search or production repair. Final native rejection remains mandatory |

Qualification records must depend on frozen package evidence in one direction:
package evidence → completed stage receipt → run-root qualification → build-report summary.
Do not embed a qualification hash into the package that qualification itself hashes.
Required engineering judgments need independent source/candidate-bound concurrence for M1;
formal engineering review remains a separate claim requiring actual review evidence.

Chief native evidence is retained at
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/runtime-519/`
and `intent-520/` beside it. The latter harness honestly reports overall
`failed`: original richer esp_prog intent is unsupported after 520. Its
acquisition checkpoint succeeds with `accepted:false`; this is neither a
missing-runtime regression nor M1 acceptance. Do not remove declarations or
change the harness to call this input ready before 479 implements its mapping.

Current Plane state and the new execution-only dependency are recorded in
[the 13:18 checkpoint](execution-checkpoint-1318.json). This supplements the
completed consolidation snapshot; it does not rewrite the three audit reports.
The original full runtime attempt is a baseline, not a comparative performance
claim or final manufacturing qualification.
