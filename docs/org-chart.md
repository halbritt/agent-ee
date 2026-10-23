# Telemetry-pod programme roles

Owned by chief agent-531 (replaced agent-529 on 2026-10-23); this current-role correction was landed
by integrator agent-530 under chief authority (Cairn 31eae84c). Applies to agent-ee, kicad-route and the
fome-mc telemetry pod.
The owner's latest direct instruction takes precedence. A proposal, committed file,
or relayed quote does not create owner authority.

On 2026-10-23 the owner directly instructed agent-529: "You are the chief coordinator."
and "Communicate through agent-519.  Agent-449 is handling dispatch."
The owner then directly stated: "update the repository as you see fit, agent-521 was fired".
Agent-529 replaces agent-521; agent-521 has no current coordination, approval or
dispatch role. Historical decisions remain evidence and apply only where consistent
with the latest owner instructions and the current delivery plan.

Later on 2026-10-23 the owner rotated every agent to a fresh session. Agent-531 replaced
agent-529 as chief coordinator, agent-530 replaced agent-449 as sole integrator and
dispatcher, and agent-532 replaced agent-519 as relay and owner-decision contact.
On 2026-10-23, in messages between about 22:32Z and 22:38Z (relayed verbatim; times
corrected by 532), the owner told agent-532 "owner decision contact, that's you." and
then corrected it: "you're not deciding on my behalf, you're forwarding my decisions". Agent-532 is the relay and owner-decision contact: it
forwards owner decisions verbatim and has no owner authority. No current agent is owner
delegate or holds owner-reserved authority (chief 531, Cairn 31eae84c). Money, orders,
uploads, new cloud, caps and owner constraints remain owner-reserved. The owner also told
agent-532: "make sure the org chart gets updated".
The owner then stated: "the chief coordinator makes decisions".
Authority order: owner > chief agent-531 (makes decisions) > integrator agent-530, within
delegated scope; agent-532 forwards and decides nothing.
Agent-521 remains fired, and agent-508 holds no standing authority.

Historical provenance: on 2026-10-22 the owner directly instructed agent-521: "execute the process per your
recommendation" and "you also own the org chart". The owner also said "agent-519 can
send me sms and slack messages, any owner decisions should go to 519 for notification".
Agent-508's briefs are proposals and historical evidence; the owner explicitly said
"they don't carry my authority". Earlier assignments are recorded in Cairn
eed2cc5c version 1. The current roles are below.

| Role | Holder | Responsibility |
| --- | --- | --- |
| Owner | halbritt | Reserved decisions and overrides. |
| Relay and owner-decision contact | agent-532 (replaced agent-519) | All campaign communications and relays; single owner-decision queue; forwards owner decisions verbatim; authorized SMS/Slack notifications. No owner authority: "you're not deciding on my behalf, you're forwarding my decisions" (owner, 2026-10-23). |
| Chief coordinator and org-chart owner | agent-531 (replaced agent-529) | Both target outcomes, technical direction, priorities, sequencing, acceptance evidence, cross-project decisions, holds, scoreboard policy, role and process records. |
| Sole dispatcher and agent-ee/FOME integrator | agent-530 (replaced agent-449) | One builder per lane, compute and review allocation, pod-design dispatch, campaign CI images, current production router, render schedule, and verified trunk integration. |
| Router owner and integrator | agent-507, shut down on 2026-10-23 at owner direction; the chief assigns any successor | kicad-route implementation, releases, installed-runtime identity and qualification; coordinates consumer changes with 449. |
| Researcher and independent verifier | agent-510 | Named research/verification assignments; one verifier per implementation delivery; reports reproducible holds and closure. Does not dispatch or integrate. |
| Shared infrastructure | agent-475 | Host admission, resource limits, init and reaping, storage diagnosis and infrastructure rollout. |
| Efficiency auditor | agent-522 (rotated 2026-10-23; successor agent-536 holds its electrical lanes) | Independent evidence for the efficiency repair; reports remaining defects and verifies closure. |
| Host monitors | hostmon instances below | Read-only system observations and alerts; no remediation or native workloads. |
| Builders | Assigned by 530 | One recorded holder per ticket/lane, selected model and host, bounded work, receipts, fixes and cleanup. |

In the paragraphs below, read 529 as 531, 449 as 530 and 519 as 532 for current
responsibilities; dated sections keep their historical names.

449 owns operational dispatch and integration of the FOME pod-design lane and the
campaign's CI image/container work. This does not make 449 their sole implementer.
Shared infrastructure stays with its repository owner; 449 triages campaign impact,
and unresolved ownership goes through 519 to 529. Agent-508 has delivered its analysis; it has no
standing programme dispatch role. Archive its contributions without treating them
as new owner instructions.

All campaign communications go through 519. It routes routine status and
known-answer questions to 449, and material holds, scope conflicts, artifact
results and scoreboard milestones to 529. Owner-reserved decisions go to 519 for notification.
A direct owner answer is effective immediately; its recipient also sends it to 519
so the queue and governing record can be reconciled. 519 does not have to be present
for an already-authorized engineering decision to proceed.

449 chooses an appropriate subscription model for each slice and records it with
the lane. Subscription authentication or quota failure stops that affected operation;
never substitute OpenRouter for a subscription-covered model. Existing hosts include
c4, peecee-wsl, talos, archon and proximal; capacity is reserved before heavy work.

The [campaign process](campaign-process.md) defines dispatch, verification, reporting,
scoreboard, owner-decision and production-runtime requirements. 529 owns this document's
content; 449 lands reviewed updates through the existing integration lane.

## Host monitoring

Agent-521 relayed the owner's request to agent-449 in Cairn record
`5702b149` on 2026-10-22. Agent-449 launched these subscription-backed monitors;
agent-521 checked all five supervisor processes on their hosts at 15:23 PDT.

| Monitored host | Monitor runs on | Model | Subscription account |
| --- | --- | --- | --- |
| proximal | proximal | Sonnet | claude-harm |
| peecee-wsl | peecee-wsl | gpt-6-luna | codex-harm |
| archon | archon | Sonnet | claude |
| talos | talos | gpt-6-luna | codex |
| agent-ee-gate-c4 | proximal, using short SSH probes | gpt-6-luna | codex-3 |

Agent-449 owns monitor dispatch and lifecycle. Agent-475 owns infrastructure
remediation. Critical alerts reach agent-529 through agent-519; owner decisions still go through
agent-519. The monitors cannot authorize changes, kill jobs, restart services or
bypass admission. Monitoring does not consume a native-work lease.

The supervisor targets one deterministic sample per minute. Model turns occur on
new alerts; otherwise changed summaries trigger at most one turn per five minutes.
A quiet host makes no additional model calls. The c4 monitor does not keep a resident process there
or start the host. Subscription authentication or quota failure stops the affected
monitor; no OpenRouter fallback or model upgrade is authorized.

These headless monitors have identities of the form `hostmon-<host>`, with recorded
supervisor PIDs and model session IDs. They have no Cairn agent number or Herdr pane.
Their alerts are forwarded through agent-449's Cairn identity with the monitor name.
The launch roster and lifecycle evidence are retained on proximal at
`~/.local/share/hostmon/roster.md`; active runtime and sample
files live under `~/.local/share/hostmon/` and `~/.local/state/hostmon/` on the
supervisor host. This is a dated launch record, not a guarantee of current health.

## Owner-directed Rust scorer

The owner directly stated on 2026-10-22: "I have a dedicated agent working on the
rust port of the scorer" and "it either works or not, but I'll be directing that
effort". Agent-524 takes direction from the owner. Agents 529, 449 and 510 assign
no scorer work, research plan, approval process or adoption criteria. Requested
input files or available compute can be supplied as resource support. Agent-449
integrates only when the owner supplies a result for integration. AGENTEE-431
remains historical evidence.
