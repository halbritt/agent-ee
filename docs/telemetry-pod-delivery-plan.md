# Telemetry pod delivery plan

Revision 2 — 2026-10-23: first placement attempt recorded; next action is one placement with a larger search allowance (agent-521 decision update to brief 220a5aa0). Revision 1: owner resumed 2026-10-22 after stopping the campaign.
Decision owner: agent-521. Sole integrator: agent-449. Communications: agent-519.

## Destination and current evidence

Deliver a placed, completely routed telemetry-pod PCB, then qualify that candidate and produce its fabrication package. No fabrication order is authorized.

The current emitted board has 305 electrical footprints plus four mounting holes and no tracks. It is not a placed or qualified candidate. The previous diagnostic reported 499 unconnected items and 21 other DRC findings; these are historical measurements, not a fresh acceptance result.

Input: fome-mc main 881f6fc5b10e81e3a9b99fd542df991742ed68f0, telemetry-pod/telemetry-pod.kicad_pcb, SHA256 2f1c955704ab1fb38217c24cbaf5a45f4219a74a6a2309be710452c54db3b673. Paired telemetry-pod.kicad_pro SHA256 10002b987995765415313fa89da32ba9011160853596f1a78ff8fba24ac9b175. Capture composition and placement-policy evidence live under telemetry-pod/expansion/production-capture/. Confirm input identity and actual constraints before execution. Do not substitute the old 112-footprint pilot or nine-footprint seed.

## Next artifact and causal action

Next artifact: a saved placement of this complete board, with connectivity and sourced constraints preserved, ready for an isolated routing attempt.

Use the existing placement implementation on an isolated copy. The immediate uncertainty is whether the emitted board and its captured policy are usable inputs. Resolve that uncertainty in the same execution that attempts placement; do not create a readiness-review campaign. If a concrete input defect prevents the attempt, repair only that defect within the bounded task, or return the exact failure and smallest necessary correction.

Agent-449 selects one existing ordinary-model writer and one compatible local admitted host through agent-475. Agent-519 carries the assignment and replies. Reuse the qualified runtime where compatible. Allow one existing 1500-second placement budget, plus at most 15 minutes of setup and result checks. If tooling requires a different bound, return the specific reason before starting an extended run. No frontier-model review is needed to attempt placement or commit this plan.

Preserve all 305 electrical footprints and four mounting holes, reference/net mapping, native design rules, sourced mechanical limits, fixed right-angle AMPSEAL J1 1-776163-1, and the accepted four-layer/1.6 mm stackup. Check source provenance when applying numeric policy constraints: agent-selected packing heuristics are not owner/manufacturer requirements. Do not silently weaken sourced constraints to obtain completion.

Return the saved board and project, immutable hashes, run directory, exact command/runtime, elapsed time, placement completion result, connectivity/footprint preservation checks, and native diagnostic delta. A partial or timed-out placement remains partial. An actual failed attempt is useful evidence; a report describing an unexecuted attempt is not the requested result.

## Placement attempt R1 (revision 2 record)

Status: NOT PLACED. Partial stays partial; there is no placed board and nothing is ready for routing.

- Run: one bounded execution on proximal through native admission, qualified runtime image d96f8e00 with agent-ee 7d5b0d4d, using the campaign's own placement adapter declaration (helper agent-ee.placer/1, timeout 1500 s).
- Input: fome-mc 881f6fc5 telemetry-pod.kicad_pcb 2f1c9557… and telemetry-pod.kicad_pro 10002b98…, unchanged; the emission-7d5b0d4d design tree was verified file by file.
- Result (attempt 1c): packing_budget_exhausted after 316.9 s; deterministic step_budget 200000 exhausted with 260 backjumps; peak 283 of 309 placed (9 sourced-fixed plus 274 of 300 movable); first unplaced R19. About 1183 s of the 1500 s bound were unused. The 283-pose plan is diagnostic only. The placer publishes no board on a typed failure.
- Input defects repaired within the bound:
  - agent-ee main refuses the project's constraint-provenance witness, which producer 7d5b0d4d emitted before AGENTEE-348 changed geometry defaults; the attempt used the qualified 7d5b0d4d runtime.
  - The derived request policy fixed C42, D3, L3 and U3 outside the board outline with no source; policy r1 removes only those four from fixed_refs.
- Native check of the input: 309 footprints (305 electrical plus 4 holes), 235 nets, four copper layers at 1.6 mm, 21 DRC findings and 499 unconnected, matching the historical diagnostic.
- Constraints applied without a recorded source: the J1, U1 and U2 poses, the H1–H4 insets, the 90 × 65 mm outline and the antenna keepout. They were applied, not weakened.
- Evidence: fome-mc branch commit 31fe446, telemetry-pod/expansion/placement-r1/ (RESULT.md, SHA256SUMS, input copies, repair policy and diff, run evidence).

## Next action (revision 2)

The same ordinary-model worker, on the same qualified 7d5b0d4d runtime and proximal admission, makes ONE placement on the same repaired input with the identical repaired policy (r1) and the identical search order. Only the computational allowance changes: step_budget 1000000 and search_timeout_s 1400, if the existing runtime supports them. The outer placement step stays 1500 s, with at most 10 minutes of setup and checks.

Reason (agent-521 decision update): the only demonstrated terminal bound in R1 was the 200000-step budget at about 630 steps/s (316.9 s). R19 being the first unplaced part does not show that R19 is unusually difficult, and 283 of 309 placed with front free-area utilization 0.858 does not establish geometric infeasibility. Raising the agent-selected step budget to use the already-allowed time is a smaller and better-supported action than the earlier search-order hypothesis, which was withdrawn before any search ran.

Unchanged: geometry, clearances, placement constraints, the fixed sourced set, rules, outline, stackup, electrical locality and all parts. No code, scorer or review changes. The R1 partial evidence and the exact policy delta are preserved.

Return: if placement completes, the saved board and project with hashes and the full native preservation and DRC check. If the outcome is unknown, the partial count and frontier, steps and backjumps, and the terminal reason. No automatic further increase or retry; agent-521 decides the next action from the returned evidence.

## Decision after that result

Agent-521 compares the observed result with the prediction. If placement is usable, assign one bounded isolated routing attempt with the existing qualified kicad-route runtime. If it fails, choose the smallest action justified by the observed blocker. Do not repeat an unchanged attempt, open a general optimization project, or treat every review finding as a prerequisite.

For each next action, update this document with the current artifact, missing result, demonstrated dependency, predicted improvement, assigned owner and finite budget, and the observation that would change the plan. Completion of tickets, reviews, merges or infrastructure is not evidence of board progress without an artifact or a removed demonstrated blocker.

Independent electrical, mechanical, RF and fabrication qualification remains required before release. Apply it to the actual candidate at the stage when its result can change that candidate or a release decision. A failure that invalidates placement or routing inputs must be handled when demonstrated; unrelated release preparation does not delay the first physical attempt.

## Scope of resumed work

Only this direct placement/routing path resumes. Old review/correction queues, research experiments, cloud benchmarks and broad preparation campaigns remain held. The Rust scorer effort is owner-directed. AppArmor work remains cancelled; no profile changes or profile tests. Use existing application/runtime access and report a concrete inability to execute if encountered.

GCP total authorization remains at most $100 with a $90 operational hold; this task uses a local host. Do not restart cloud instances. Preserve stopped partial work. Restore useful render publication as candidate artifacts change, then approximately every four hours during active work; label incomplete boards accurately.

All campaign communications go through agent-519. It consolidates routine acknowledgments and sends agent-521 only a needed decision, a material exception, or the next artifact result. Agent-521 retains planning responsibility. Native inbox ownership remains per session; no agent consumes another agent's inbox.

This committed file is the source of truth. Cairn contains its path and current revision, not a competing plan. Assignments name the revision. Agent-449 commits updates through the established integration path.
