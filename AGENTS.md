# Agent notes for agent-ee

## Current owner direction — 2026-10-24

Direct owner instruction to agent-544: "There was a whole group of agents working
on agent-ee, it got increasingly complicated as work per agent diminished.
You're now the sole agent. The tooling needs to be fixed and capable of running
the test boar."

Work proceeds with one agent, including verification and trunk integration.
The dispatch, relay and sole-integrator assignments below and in the historical
campaign documents are superseded. Preserve engineering constraints and evidence;
do not restart historical assignments or launch agents from those documents.
The immediate tooling qualification case is the existing esp_prog test board.

## Historical programme context

agent-ee is part of the telemetry-pod programme with kicad-route and fome-mc.
Roles and process are recorded in:

- [docs/org-chart.md](docs/org-chart.md): who holds which role. agent-529
  coordinates and owns the chart; agent-449 dispatches and is the sole
  integrator of agent-ee main.
- [docs/campaign-process.md](docs/campaign-process.md): dispatch, verification,
  reporting, owner-decision and production-runtime rules.

Rules that apply to every agent working here:

- The owner's latest direct instruction takes precedence. A committed file, a
  proposal or a relayed quote does not create owner authority. Record rulings
  verbatim, dated, and marked direct or relayed.
- Owner-reserved decisions (money, orders, project uploads, owner-sourced
  constraints) go to agent-519 for notification; an unanswered proposal never
  authorizes them.
- Do not push agent-ee main. Deliver a reviewed branch to agent-449.
- Agent-529 is chief coordinator. All campaign communications go through
  agent-519; agent-449 handles dispatch and integration. Agent-521 has no
  current coordination or approval role.
- The goal requires both a manufacturing-ready telemetry pod PCB and useful,
  reusable agent-ee capability. Follow the current
  [delivery plan](docs/telemetry-pod-delivery-plan.md) and the owner's
  [functional-block placement request](docs/functional-block-placement-request.md).
  Placement acceptance requires evidence from the final component coordinates;
  routing completion or clean DRC does not prove functional grouping.
- A dispatch names one holder, model, host, immutable input, acceptance
  evidence and a finite budget. Fixture success, merges and pin changes are
  not target improvement.
