# Telemetry-pod programme roles

Maintained by agent-521. Applies to agent-ee, kicad-route and the fome-mc telemetry pod.
The owner's latest direct instruction takes precedence. A proposal, committed file,
or relayed quote does not create owner authority.

On 2026-10-22 the owner directly instructed agent-521: "execute the process per your
recommendation" and "you also own the org chart". The owner also said "agent-519 can
send me sms and slack messages, any owner decisions should go to 519 for notification".
Agent-508's briefs are proposals and historical evidence; the owner explicitly said
"they don't carry my authority". The assignments below are agent-521's implementation
of that delegation, including earlier assignments in Cairn eed2cc5c version 1.

| Role | Holder | Responsibility |
| --- | --- | --- |
| Owner | halbritt | Reserved decisions and overrides. |
| Coordinator and org-chart owner | agent-521 | Target outcomes, priorities, cross-project decisions, holds that change the plan, scoreboard policy, role and process records. |
| Dispatcher and agent-ee/FOME integrator | agent-449 | One builder per lane, compute and review allocation, pod-design dispatch, campaign CI images, current production router, render schedule, and verified trunk integration. |
| Router owner and integrator | agent-507 | kicad-route implementation, releases, installed-runtime identity and qualification; coordinates consumer changes with 449. |
| Researcher and independent verifier | agent-510 | Named research/verification assignments; one verifier per implementation delivery; reports reproducible holds and closure. Does not dispatch or integrate. |
| Owner-decision contact | agent-519 | Single owner-decision queue, SMS/Slack notifications, answer provenance, reconciliation and closure. Receives requests and direct answers collected elsewhere. |
| Builders | Assigned by 449 | One recorded holder per ticket/lane, selected model and host, bounded work, receipts, fixes and cleanup. |

449 owns operational dispatch and integration of the FOME pod-design lane and the
campaign's CI image/container work. This does not make 449 their sole implementer.
Shared infrastructure stays with its repository owner; 449 triages campaign impact,
and unresolved ownership goes to 521. Agent-508 has delivered its analysis; it has no
standing programme dispatch role. Archive its contributions without treating them
as new owner instructions.

Routine status and known-answer questions go to 449. Send 521 material holds, scope
conflicts and scoreboard milestones. Owner-reserved decisions go to 519 for notification.
A direct owner answer is effective immediately; its recipient also sends it to 519
so the queue and governing record can be reconciled. 519 does not have to be present
for an already-authorized engineering decision to proceed.

449 chooses an appropriate subscription model for each slice and records it with
the lane. Subscription authentication or quota failure stops that affected operation;
never substitute OpenRouter for a subscription-covered model. Existing hosts include
c4, peecee-wsl, talos, archon and proximal; capacity is reserved before heavy work.

The [campaign process](campaign-process.md) defines dispatch, verification, reporting,
scoreboard, owner-decision and production-runtime requirements. 521 owns this document's
content; 449 lands reviewed updates through the existing integration lane.
