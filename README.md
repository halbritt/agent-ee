# agent-ee

**agent-ee is an evidence-driven review and repair workflow for KiCad electronics
projects.** It combines native electrical and board checks, simulation, and
independent AI reviewers to find requirement violations, preserve the evidence,
and verify that a repair actually fixes them.

It is for engineers using coding agents to help with circuit and PCB work who
need a repeatable answer to three questions: **What is wrong? What proves it?
Did the change fix it without breaking something else?**

## Why this project exists

A board can pass electrical rule checks and design rule checks while still doing
the wrong thing. A connected, legally routed filter can have the wrong cutoff
frequency. A component substitution can change a circuit's response. A layout
change that helps one constraint can consume margin in another.

AI review adds another problem: a persuasive explanation is easy to produce,
but it may describe the wrong revision, assume an unstated requirement, or claim
that a simulation proves something it never measured. Asking the same agent to
make a change and approve its own work leaves those assumptions unchallenged.

agent-ee makes the review an explicit engineering process. It captures the design
and its requirements, runs declared checks, asks independent reviewers to examine
the same evidence, and validates a central adjudication of their findings. An
optional repair campaign gives one implementation agent a disposable design copy,
then submits every proposed change to a fresh review. The resulting record tells
you which candidate was evaluated, against which requirements, using which tools,
and why a finding was accepted or left unresolved.

The purpose is to make agent-assisted electronics work inspectable and repeatable.
The engineer still defines the requirements, supplies applicable models, and owns
physical validation.

## A concrete example

The repository includes a small RC low-pass filter with a declared requirement:
after a 1 V step, its output at 1 ms must be between 0.62 V and 0.65 V.

In the [recorded demonstration](evidence/README.md), a deliberately wrong resistor
value passed native ERC and DRC. Simulation using the actual schematic's
connectivity and component values produced about **0.095 V** at 1 ms. Reviewers
identified the response violation from that evidence. A repair in a disposable
copy then passed fresh checks and independent review. The original defective
candidate and its evidence remained available.

This is the distinction agent-ee is built around: valid connections and geometry
are necessary checks, but functional requirements need their own measurements.
The RC example is a small demonstrated case; larger designs need their own
requirements, checks and models.

## How the workflow works

1. **Define the engineering question.** A scope selects the native design files,
   dependencies, requirements, operating conditions and acceptance checks.
   Scope/2 binds requirements, target specifications and reference architecture
   sources into an immutable baseline.
2. **Capture one design candidate.** Inputs are copied and hashed. Every check
   and review refers to those exact bytes, including selected uncommitted files.
3. **Produce tool evidence.** KiCad, ngspice and configured measurement tools
   generate retained reports, traces and metrics. A failed tool invocation is
   recorded as missing coverage, never as a passing design.
4. **Review independently.** Fresh circuit and physical-design reviewers receive
   the same candidate, requirements and check evidence. They cannot read each
   other's initial judgments. Calls currently run sequentially in separate
   contexts and workspaces.
5. **Adjudicate centrally.** Findings get explicit dispositions. Under scope/2,
   an accepted defect must be a coordinator check finding backed by sealed
   evidence and a mapped requirement violation. Reviewer hypotheses need
   reproduction or remain concerns; agreement alone cannot establish a defect.
6. **Repair and recheck when authorized.** One implementation agent may edit
   declared design files in a disposable copy. A new candidate goes through the
   checks and independent reviews again. Requirements and verification inputs
   cannot be edited to make the repair pass.

The [architecture diagrams](docs/architecture.md) show the information and write
boundaries. Native tools and agents run in Bubblewrap sandboxes; supported writers
also acquire process leases to prevent concurrent campaign mutations.

## Beyond a single review

| Capability | What it is for |
| --- | --- |
| [Bounded repair campaigns](docs/campaign-guide.md) | Turn established defects into implementation work, protect previously passing criteria, detect repeated designs and regressions, and stop on stalled progress or exhausted budgets. A stop diagnostic preserves the competing constraints for an engineering decision. |
| [Measurement adapters](docs/measurement-adapters.md) | Connect an installed analysis tool and a captured parser to the same requirement and evidence contracts. The coordinator records the tool identity and raw outputs, then evaluates declared bounds. |
| [Cross-domain checks](docs/measurement-adapters.md#cross-domain-coupling) | Evaluate electrical, thermal, parasitic or manufacturing margins together at the same operating condition so a local improvement cannot hide a failed coupled limit. The supplied tools must calculate the relevant physics. |
| [Bench feedback](docs/bench-feedback.md) | Bind prototype observations to the measured candidate, retain raw samples, and feed simulation-to-bench discrepancies back into subsequent acceptance checks and fresh reviews. |
| [Historical verification](docs/result-reference.md) | Check that retained evidence still belongs to the captured inputs and policy. Changed sources do not inherit an old candidate's passing review. |

Campaigns are designed to terminate with satisfied criteria, an explicit
escalation, or an incomplete execution. They do not promise that an arbitrary
set of physical constraints has a feasible solution. Bench feedback updates
empirical correction envelopes; it does not automatically discover a new physical
model or acquire measurements from instruments.

## What is implemented today

Built-in checks cover KiCad ERC, DRC and schematic parity, native netlist export,
saved-board geometry, [page-cited datasheet facts and native symbol pin maps](docs/datasheet-facts.md),
and an ideal passive R/C/L transient simulation through
ngspice. Generic producer/parser interfaces support additional measurements and
coupled margins. Reviewer profiles explicitly select Claude, Codex or Agy through
their subscription CLIs; authentication or quota failures stop the affected call
without a provider fallback.

Thermal solvers, post-layout parasitic extraction, DFM/DFA/DFT analysis and BOM
lifecycle services are **not bundled**. They need separately qualified tools,
models and adapters. The built-in passive simulation is not a general active
circuit simulator. The [coverage guide](docs/coverage.md) spells out these limits.

A passing result applies to the declared criteria for one captured candidate.
It is not manufacturing approval, EMC certification or proof of hardware safety.
Review and repair do not modify the source checkout, order components or approve
fabrication. The engineer retains those decisions.

## Try the included design

The demonstrated environment is Linux with KiCad 10.0.7, ngspice 42, Bubblewrap
0.9.0 and system Python with KiCad's `pcbnew` bindings. The Python CLI itself has
no third-party package dependencies. See [installation](docs/installation.md)
for prerequisites, subscription setup and installation details.

From the repository root:

```sh
./scripts/install.sh
agent-ee doctor
agent-ee review fixtures/closed-loop/control.scope.json \
  --out build/first-checks --checks-only
agent-ee inspect build/first-checks
agent-ee verify build/first-checks
```

This first run exercises the included design and native tools without model
calls. **The review command intentionally exits 1:** checks-only execution leaves
the independent review unfinished, even when all checks pass. `verify` checks
integrity and freshness; its success does not mean the design was accepted.
Every run needs a new output directory.

To include independent reviewers and adjudication, authenticate the selected
subscription CLI and pass an explicit profile:

```sh
agent-ee review fixtures/closed-loop/control.scope.json \
  --profile profiles/codex-baseline.json \
  --out build/first-review --max-seconds 3600
```

This spends subscription inference. Profiles have no implicit model defaults;
see [profile configuration](docs/reviewer-profiles.md) and the
[recorded model evaluation](evidence/MODEL-SELECTION.md) before choosing an
allocation. To enable automatic design edits, use an explicit
[campaign configuration](docs/campaign-guide.md) within your authorized repair
scope.

For your own board, adapt a [scope](docs/scope-reference.md) to capture its native
project, libraries, custom rules, requirements and models. Include functional
checks for the behavior you want to establish. The fixture's requirements are
specific to that filter and should not be copied as generic acceptance criteria.

## What you get back

Each run retains a human-readable `REPORT.md`, machine-readable `result.json`,
the captured inputs, tool commands and raw outputs, reviewer judgments and finding
dispositions. Campaigns additionally retain candidate history, implementation
attempts and convergence decisions. You can inspect a failure without erasing it
when a later candidate succeeds.

| Review outcome | Meaning |
| --- | --- |
| `criteria_satisfied` | Required checks and reviews completed with no accepted defects or unresolved findings; noted nonblocking observations may remain. |
| `defects_or_unresolved` | Review completed with defects or open questions. |
| `incomplete` | A tool, reviewer, evidence gate, adjudication or work budget left required work unfinished. |

See the [operator guide](docs/operator-guide.md) for interpreting findings and
closing corrections, and [troubleshooting](docs/troubleshooting.md) for refusals
and partial runs.

## Evidence, development and documentation

The project has tests covering native checks, evidence integrity, isolated
review contexts, single-writer ownership, regression detection and empirical
feedback. The original RC defect-and-repair demonstration used actual agent
reviews. The newer complete campaign and bench-feedback loops are exercised with
scripted agent responses and synthetic bench samples. A live subscription
campaign demonstrated independent review and rejection of an invalid adjudicator
citation; it did not complete an automatic repair. These are distinct levels of
evidence, recorded in the [closed-loop verification report](docs/closed-loop.md).

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
python3 scripts/check_docs.py
```

Native-dependent tests skip with a stated prerequisite reason when their
toolchain is unavailable. Set `AGENT_EE_REQUIRE_TOOLCHAIN=1` to make missing
prerequisites fail (the CI mode). Assertions remain failures. See the
[maintainer guide](docs/maintainer-guide.md#development-and-checks).

The [full manual](docs/README.md) is the entry point for command, scope, tool and
state contracts. The [maintainer guide](docs/maintainer-guide.md) covers extension
interfaces and tests; [security](docs/security.md) explains sandbox and trust
limits; [evidence reproduction](docs/reproducing-evidence.md) describes replaying
historical demonstrations. [Independent review dispositions](docs/closed-loop-review.md)
record Fable and Argon's findings on the closed-loop implementation.

The [enhancement roadmap](docs/enhancement-roadmap.md) records proposed next
steps, their engineering constraints and acceptance tests, with the independent
idea pools and review decisions that informed their priority.
