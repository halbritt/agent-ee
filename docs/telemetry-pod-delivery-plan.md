# Telemetry pod delivery plan

Revision 1 — owner resumed 2026-10-22 after stopping the campaign.
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

## Decision after that result

Agent-521 compares the observed result with the prediction. If placement is usable, assign one bounded isolated routing attempt with the existing qualified kicad-route runtime. If it fails, choose the smallest action justified by the observed blocker. Do not repeat an unchanged attempt, open a general optimization project, or treat every review finding as a prerequisite.

For each next action, update this document with the current artifact, missing result, demonstrated dependency, predicted improvement, assigned owner and finite budget, and the observation that would change the plan. Completion of tickets, reviews, merges or infrastructure is not evidence of board progress without an artifact or a removed demonstrated blocker.

Independent electrical, mechanical, RF and fabrication qualification remains required before release. Apply it to the actual candidate at the stage when its result can change that candidate or a release decision. A failure that invalidates placement or routing inputs must be handled when demonstrated; unrelated release preparation does not delay the first physical attempt.

## Scope of resumed work

Only this direct placement/routing path resumes. Old review/correction queues, research experiments, cloud benchmarks and broad preparation campaigns remain held. The Rust scorer effort is owner-directed. AppArmor work remains cancelled; no profile changes or profile tests. Use existing application/runtime access and report a concrete inability to execute if encountered.

GCP total authorization remains at most $100 with a $90 operational hold; this task uses a local host. Do not restart cloud instances. Preserve stopped partial work. Restore useful render publication as candidate artifacts change, then approximately every four hours during active work; label incomplete boards accurately.

All campaign communications go through agent-519. It consolidates routine acknowledgments and sends agent-521 only a needed decision, a material exception, or the next artifact result. Agent-521 retains planning responsibility. Native inbox ownership remains per session; no agent consumes another agent's inbox.

This committed file is the source of truth. Cairn contains its path and current revision, not a competing plan. Assignments name the revision. Agent-449 commits updates through the established integration path.
