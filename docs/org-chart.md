# Org chart: agent-ee, kicad-route and the fome-mc telemetry pod

Owner ruling, 2026-10-22 (direct to agent-508, verbatim): "521 is the chief
coordinator/decision maker, 449 is the dispatcher, 510 is the routing and placing
researcher and verifier, 507 owns the router ... 449 can instantiate agents at
will and has access to a cloud instance 'c4' for build, peecee-wsl, talos, archon,
and proximal."

This file is the citable source for roles. A role relayed in a Cairn message,
a handoff or a chat is not authority; this file and the owner's own words are.
Change it only on an owner ruling, quoted verbatim with its date.

| Role | Agent | Scope |
| --- | --- | --- |
| Owner | halbritt | All decisions reserved to the owner; see the owner-reserved list below. |
| Chief coordinator, decision maker | agent-521 | Decides sequencing, scope and cross-project conflicts for agent-ee, kicad-route and the telemetry pod. Receives verification holds and chooses what is built next. |
| Dispatcher | agent-449 | Instantiates builder and reviewer agents at will, assigns lanes, holds the token and compute budget, lands agent-ee integration. Build hosts: cloud instance `c4`, `peecee-wsl`, `talos`, `archon`, `proximal`. |
| Router owner | agent-507 | Owns kicad-route: its main branch, releases and the installed build identity agent-ee pins. |
| Placement and routing researcher and verifier | agent-510 | Researches placement and routing improvements and independently verifies delivered work. Verifies; does not dispatch, integrate or coordinate. |

## Models and cost (owner, 2026-10-22)

Owner's words: "449 runs opus 5.5, which has been effective and cheap, token-wise.
521 is running astra high, so it should be reserved for hard decisions. workers
are sol/opus/sonnet/haiku as needed."

- Routine dispatch, lane bookkeeping and status answers go to agent-449 (Opus 5.5).
- agent-521 (Astra high) is asked only for decisions that need judgment: scope
  conflicts, holds that change the plan, owner-reserved questions to escalate.
  Do not send it status, acknowledgements or questions 449 can answer.
- Builders and reviewers run on Sol, Opus, Sonnet or Haiku; the dispatcher picks the
  cheapest model that can do the slice and says which it used.

## Verification cap (owner approved 2026-10-22)

- One verifier per delivery. A delivery already verified against an immutable
  revision is not re-verified unless its bytes change.
- Verification reports go to the coordinator and the lane's builder only when
  they hold a delivery or close a hold. A passing re-check of unchanged bytes is
  not reported.
- A hold names the exact revision, the reproducing case and the owner of the fix.
  Model-review quota is a resource the dispatcher manages, never a gate the
  verifier waits on silently.

## Owner-reserved decisions

Spending money, ordering fabrication or parts, uploading project files, and
changing an electrical or mechanical constraint whose provenance is the owner.
Everything else is decided by the role that owns it, recorded with its rationale,
and proceeded with (fome-mc AGENTS.md rule; owner, 2026-10-20: "autonomous").

## Gaps the owner has not assigned (2026-10-22)

- fome-mc telemetry pod design lane (placement iterations, design-rule files,
  routing campaigns on the pod). Last held by agent-509; agent-508 is executing
  three owner-ordered fixes in this session (kicad-route re-pin, one routing
  campaign on the committed microSD placement, this file) and then stands down.
- CI image and container build ownership (docker/, peecee CI gate).
- Shared infrastructure (Cairn, Plane, Herdr) ownership for this programme.

The coordinator assigns these; until then they are unowned, not implicitly 449's.
