# Agent notes for agent-ee

## Historical programme context

agent-ee is part of the telemetry-pod programme with kicad-route and fome-mc.
Roles and process are recorded in:

- [docs/campaign-process.md](docs/campaign-process.md): dispatch, verification,
  reporting, owner-decision and production-runtime rules.

Rules that apply to every agent working here:

- The owner's latest direct instruction takes precedence. A committed file, a
  proposal or a relayed quote does not create owner authority. Record rulings
  verbatim, dated, and marked direct or relayed.
- Owner-reserved decisions (money, orders, project uploads, owner-sourced
  constraints) need the owner's direct answer; an unanswered proposal never
  authorizes them.
- The goal requires both a manufacturing-ready telemetry pod PCB and useful,
  reusable agent-ee capability. Follow the current
  [delivery plan](docs/telemetry-pod-delivery-plan.md) and the owner's
  [functional-block placement request](docs/functional-block-placement-request.md).
  Placement acceptance requires evidence from the final component coordinates;
  routing completion or clean DRC does not prove functional grouping.
- A dispatch names one holder, model, host, immutable input, acceptance
  evidence and a finite budget. Fixture success, merges and pin changes are
  not target improvement.
