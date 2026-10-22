# Telemetry-pod campaign process

Coordinator decision by agent-521, 2026-10-22, implementing the owner's instruction
to execute the reviewed process. Scope and roles are in [org-chart.md](org-chart.md).

## Target scoreboard and dispatch

The canonical board scoreboard is fome-mc
`telemetry-pod/expansion/routing-diagnostic/SCOREBOARD.md`. 521 owns its outcome policy;
449 maintains it through the pod lane after each measured run or material gate change.
Keep production qualification separate from historical diagnostic attempts. Record
placement/source identity, router and executed engine identity, connected nets and
total nets, unresolved connections as a separate quantity, introduced and pre-existing
DRC, total wall time, qualification state, blockers, and exact evidence locations.
Unknown is not zero. A new image, clean tool exit or merged primitive is not board acceptance.

Every dispatch names an outcome to improve or a prerequisite blocking that outcome.
It records one accountable holder, worker agent/model/host, ticket, immutable input,
acceptance evidence, verifier when needed, finite time/trial budget and stopping condition.
Use a valid Plane assignee for accountability and separate lane fields in the description
for session identity/model. Do not invent users or put model names into a user-ID field.
449 reconciles Plane and Cairn before dispatch; join an existing lane instead of starting
a duplicate. Record waits with the specific unmet gate and next action.

Research must name a measured target problem and a bounded experiment. Fixture success,
source merge, pin changes and diagnostics do not prove target improvement. Matched
comparisons hold inputs, rules, ordering and budget constant except the declared axis;
include selection, routing, refill and validation in wall time and retain failures.
A useful prerequisite may proceed before a plateau experiment; no blanket research freeze.

## Verification and reporting

Builder receipts name commands, results/counts, source/runtime/input identities and
native ERC/DRC where required. They are reusable evidence, not a substitute for required
independent qualification. Assign one verifier per implementation delivery. Recheck only
when candidate, dependencies, runtime or test contract changes, or new evidence invalidates
the old result. Distinct circuit, physical, RF and fabrication reviews remain required.
A hold names revision, reproducer, consequence and fix owner. Report its closure once;
do not repeatedly report passing checks of an unchanged boundary. 449 owns review capacity.

Status notices target fewer than 600 bytes; holds fewer than 2 kB. Lead with verdict,
revision and evidence path. Put detailed logs/hashes in retained evidence. Do not omit
material conditions to meet a length target. Known-answer traffic goes to 449.

## Owner decisions

519 owns the single queue for owner-decision notification, including requests and answers
received directly by other agents. Each entry records question, provenance, allowed work
while waiting, impact, answer and closure. SMS/Slack delivery is owner-authorized for519.
Money, orders, project uploads and changes to owner-sourced constraints require their
existing authorization; an unanswered proposal never supplies it. Continue independent
work within existing authority. Missing thermal/bench evidence is an engineering obligation,
not automatically an unanswered owner decision. Close obsolete questions against the
actual answer; do not reopen the retired eMMC via-in-pad choice after microSD selection.

Record rulings verbatim, dated and with direct/relayed provenance in the governing file.
A recorded relay remains a relay. Latest direct owner instructions take precedence until
records catch up. 519 coordinates file updates through their owners/integrators;521 owns
org-chart content. No universal requirement that every ruling originate with519.

## Production runtime and renders

Production must use the current integrated kicad-route with a qualified installed engine
and compatible agent-ee adapter/image. Check upstream freshness against a recorded main
revision, separately from deterministic tests of a frozen pin. Refuse production execution
when the selected runtime does not match that pin. An intentionally retained historical
runtime may be healthy for replay while still being ineligible for current production.
A main update does not mutate or invalidate a running frozen experiment; qualify it for
the next run. Any temporary production exception is explicit, dated and owned by521.

449 owns a persistent render job, approximately every four hours, with an initial refresh
now. Render top/bottom/isometric views from the latest consistent snapshot and publish to
the existing private gallery. Bind board/model hashes, timestamp, routing/qualification
state and whether the board changed. Unchanged-board renders must say so. Serialize jobs,
bound runtime, retain failures and notify449 on failure; no simultaneous copy of a mutating
board and no repeated native regression suite merely for a scheduled image. Stop or hand
off the schedule when the programme ends. 449 reports the job identity, next run and first
published bundle as implementation evidence.
