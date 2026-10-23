# Telemetry-pod campaign process

Maintained by chief coordinator agent-529 until 2026-10-23, now owned by chief agent-531
(this current-role correction was landed by integrator agent-530 under chief authority).
It preserves the 2026-10-22 process history and updates current responsibilities under
the owner's direct instructions. Scope and roles are in [org-chart.md](org-chart.md).

The programme must deliver both a manufacturing-ready telemetry pod PCB and
useful, reusable agent-ee capability. Agent-531 (replaced 529) owns direction,
sequencing and acceptance; agent-530 (replaced 449) is the sole dispatcher and
integrator. All campaign communications go through agent-532 (replaced 519), which
consolidates routine traffic and returns consequential decisions and evidence to 531.
Agent-532 is also the owner-decision contact and, by the owner's direct word on
2026-10-23 ("owner decision contact, that's you."), the owner delegate for owner-reserved
questions the owner has not ruled on; money, orders, uploads, new cloud, caps and owner
constraints remain owner-reserved, and the owner's own word overrides 532. Elsewhere in
this file, read 529 as 531, 449 as 530 and 519 as 532 for current responsibilities;
dated decisions keep their historical names. Detailed investigation and implementation stay
delegated. The current [delivery plan](telemetry-pod-delivery-plan.md) and
[owner placement request](functional-block-placement-request.md) govern the work.

## Target scoreboard and dispatch

The canonical board scoreboard is fome-mc
`telemetry-pod/expansion/routing-diagnostic/SCOREBOARD.md`. 529 owns its outcome policy;
449 maintains it through the pod lane after each measured run or material gate change.
Keep production qualification separate from historical diagnostic attempts. Record
placement/source identity, router and executed engine identity, connected nets and
total nets, unresolved connections as a separate quantity, introduced and pre-existing
DRC, total wall time, qualification state, blockers, and exact evidence locations.
Unknown is not zero. A new image, clean tool exit or merged primitive is not board acceptance.

Track placement intent as well as routing: membership/shared/fixed coverage,
final-coordinate block cohesion, pin/interface adjacency, side eligibility and
selection, sourced mechanical keepouts, and unresolved constraints or exceptions.
Bind these results and annotated top/bottom views to the saved board. Revalidate
affected placement constraints after legalization, routing feedback or any other
component movement. A clean DRC or completed route does not establish functional
grouping. Tool acceptance also needs a meaningful congestion/underside regression
and evidence on the full pod.

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

## Native workload admission and validation

Coordinator decision by agent-521, 2026-10-22, implementing the owner's direct instruction to fix agent-522's efficiency findings. Tracking: AGENTEE-464.

Native execution needs a host-wide reservation before it starts. A model session does not reserve CPU or storage capacity. Until measured limits are deployed, admit at most one new native suite per host and at most four module workers in that suite. Admit no new heavy work on Talos until its backlog drains. A sole authoritative c4 integration gate may use twelve module workers after host admission, with CPU and I/O measurements retained; this does not bypass the one-job or draining checks. Unknown capacity refuses or queues the job. Account for child processes and storage pressure, preserve capacity for the actual pod, and keep performance comparisons on a quiet compatible host. Record the requested and granted capacity.

Use fast tests and affected native modules for ordinary implementation and review. Documentation or metadata changes use relevant documentation and schema checks unless they change executable inputs or validation policy. Unknown dependencies require the full suite. Select by affected behavior and record the reason; a small diff alone does not justify skipping native coverage.

A composed integration candidate gets one authoritative full gate. Reuse it only while source, dependencies, fixtures, native runtime, and test contract retain their recorded identities. Builders, reviewers, and integrators must not each repeat that unchanged gate. Nightly and release checkpoints retain full regression and applicable native acceptance. A changed board geometry always needs its own native acceptance.

Coalesce superseded broad CI work after retaining logs and failure evidence, and ensure the current composed candidate still receives a complete gate. Do not cancel a live candidate run merely because observation timed out. Every cancellation records why the work is redundant and which gate replaces it.

The compatibility record binds agent-ee, kicad-route source and engine, execution image, and design/rules inputs. Test a proposed router through the actual consumer positive and material negative cases before propagating dependent changes. Limit concurrent work on one unresolved contract.

For each candidate, retain queue, build/test, review-wait, integration-wait and complete elapsed time, including failed attempts. Model time remains unknown when not measured. Report accepted board outcomes: connected nets, unresolved connections, introduced and pre-existing DRC, and qualification. Merge counts and busy CPUs are not outcomes.

Agent-475 owns host admission, init-image rollout, and bounded storage diagnostics; agent-449 owns dispatch enforcement, CI selection, compatibility and integration; agent-522 independently verifies deployed behavior; agent-529 owns policy and closure. Reuse AGENTEE-431's negative compiled-scorer experiment; another experiment needs a distinct causal hypothesis and a quiet reservation. No broad language or repository migration is part of this repair.

### Approved gate exception

agent-521, 2026-10-22: "the single authoritative integration gate may run JOBS=12 on c4 only when no other suite executes and agent-475's host admission grants the reservation; 4 workers remain the default elsewhere."

See [org-chart.md](org-chart.md) for current roles. The dated exception above
is historical provenance; agent-529 now owns this policy.

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
records catch up. 519 coordinates file updates through their owners/integrators;529 owns
org-chart content. No universal requirement that every ruling originate with519.

## Production runtime and renders

Production must use the current integrated kicad-route with a qualified installed engine
and compatible agent-ee adapter/image. Check upstream freshness against a recorded main
revision, separately from deterministic tests of a frozen pin. Refuse production execution
when the selected runtime does not match that pin. An intentionally retained historical
runtime may be healthy for replay while still being ineligible for current production.
A main update does not mutate or invalidate a running frozen experiment; qualify it for
the next run. Any temporary production exception is explicit, dated and owned by529.

449 owns a persistent render job, approximately every four hours, with an initial refresh
now. Render top/bottom/isometric views from the latest consistent snapshot and publish to
the existing private gallery. Bind board/model hashes, timestamp, routing/qualification
state and whether the board changed. Unchanged-board renders must say so. Serialize jobs,
bound runtime, retain failures and notify449 on failure; no simultaneous copy of a mutating
board and no repeated native regression suite merely for a scheduled image. Stop or hand
off the schedule when the programme ends. 449 reports the job identity, next run and first
published bundle as implementation evidence.
