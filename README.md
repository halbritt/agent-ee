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
   sources into an immutable baseline. Opt-in scope/3 also refuses acceptance
   requirements that have no configured executable check; informational checks
   cannot stand in for missing acceptance coverage.
2. **Capture one design candidate.** Inputs are copied and hashed. Every check
   and review refers to those exact bytes, including selected uncommitted files.
3. **Produce tool evidence.** KiCad, ngspice and configured measurement tools
   generate retained reports, traces and metrics. A failed tool invocation is
   recorded as missing coverage, never as a passing design.
4. **Review independently.** Fresh circuit and physical-design reviewers receive
   the same candidate, requirements and check evidence. They cannot read each
   other's initial judgments. The two calls run concurrently in separate
   contexts and workspaces; adjudication waits for both to settle.
5. **Adjudicate centrally.** Findings get explicit dispositions. Under scope/2 and scope/3,
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

The [synthetic benchmark utilities](docs/seeded-benchmark.md) preregister five
pinned public boards and 30 assembly, schematic-wiring and pad-geometry mutations.
Sources and generated designs remain outside the repository; dependency closure
and owner review are outstanding.

## Beyond a single review

| Capability | What it is for |
| --- | --- |
| [Bounded repair campaigns](docs/campaign-guide.md) | Turn established defects into implementation work, protect previously passing criteria, detect repeated designs and regressions, and stop on stalled progress or exhausted budgets. A stop diagnostic preserves the competing constraints for an engineering decision. |
| [Measurement adapters](docs/measurement-adapters.md) | Connect an installed analysis tool and a captured parser to the same requirement and evidence contracts. The coordinator records the tool identity and raw outputs, then evaluates declared bounds. |
| [Concern evidence requests](docs/evidence-requests.md) | Export an unresolved concern and reproduce a bounded Python experiment against captured inputs in a separate networkless sandbox. Replay assertions remain unverified concerns; a proposed check must still qualify through the normal evidence path. |
| [Cross-domain checks](docs/measurement-adapters.md#cross-domain-coupling) | Evaluate electrical, thermal, parasitic or manufacturing margins together at the same operating condition so a local improvement cannot hide a failed coupled limit. The supplied tools must calculate the relevant physics. |
| [Bench feedback](docs/bench-feedback.md) | Bind prototype observations to the measured candidate, retain raw samples, and feed simulation-to-bench discrepancies back into subsequent acceptance checks and fresh reviews. |
| [Historical verification](docs/result-reference.md) | Check that retained evidence still belongs to the captured inputs and policy. Changed sources do not inherit an old candidate's passing review. |

Campaigns are designed to terminate with satisfied criteria, an explicit
escalation, or an incomplete execution. They do not promise that an arbitrary
set of physical constraints has a feasible solution. Bench feedback updates
empirical correction envelopes; it does not automatically discover a new physical
model or acquire measurements from instruments.

## What is implemented today

An optional [staged campaign](docs/staged-campaign.md) keeps later checks behind
earlier gates. Missing expected design content requires a native observation; it
cannot pass merely because an empty design produces no rule violations. Progress
is measured in the earliest failing stage, with regression and retry limits.
Existing unstaged campaigns keep their convergence rules. A complete native
empty-to-routed run and enforcement of stage-specific writer tools remain pending.

An [offline intent importer](docs/design-intent.md) preserves source requirements
and exact voltage values. [Decision capture](docs/design-dialog.md) records
answers and their dependencies; missing answers remain unresolved. These tools
prepare inputs, but do not choose executable acceptance checks or generate a
verified circuit.

[Board envelope declarations](docs/board-envelope.md) bind mechanical constraints
and derived requirements into the captured baseline. A reference document becomes
binding only when declared or consumed by the scope. Complete production envelope
capture remains unavailable: outline, layer and stackup obligations still lack
all required executable predicates, so capture refuses those gaps.

Built-in checks cover KiCad ERC, DRC and schematic parity, native netlist export,
saved-board geometry, [declared hardware interfaces](docs/hardware-interface.md),
[page-cited datasheet facts and native symbol pin maps](docs/datasheet-facts.md),
and an ideal passive R/C/L transient simulation through ngspice. An optional
[qualified RC tolerance check](docs/passive-corners.md) retains complete endpoint
ensembles for one unloaded first-order topology. A separate
[captured active-device check](docs/active-spice.md) supports explicit subcircuit
pin bindings and declared transient/AC measurements. Its committed control model
is synthetic; vendor-model qualification and publication rights are separate
evidence obligations.
Optional [PCB DC path resistance](docs/dc-path.md) connects explicitly captured
materials and saved native copper dimensions to declared DC drop/loss limits.
Its initial qualification is limited to ideal F.Cu endpoint paths and bounded
series/parallel networks; unsupported conductors leave coverage incomplete.
The [connectivity resolver](docs/connectivity-ir.md) elaborates explicit captured
symbol pins and scoped sheet ports into physical nets. Hierarchical port names
that would short distinct local nets are rejected. Repeated sheet files and
multi-sheet global power remain unsupported. The [native emitter](docs/schematic-emitter.md)
produces flat and supported hierarchical schematics from that captured intent.
[Forward annotation](docs/forward-annotation.md) plans and applies native-netlist
changes to a board with protected placement and routing. Full campaign generation
and hierarchical annotation application remain unqualified.
[Netlist parity](docs/netlist-parity.md) compares native connectivity with captured
intent and libraries or an explicit golden netlist. Resolved intent is rederived
before comparison. Exact instance connectivity catches exchanges that collapsed
part labels can miss; ambiguous correspondence and search exhaustion remain unknown.
[Additional netlist checks](docs/netlist-extra.md) test declared direct-net shorts
and anchored rail pairs. Neither check establishes electrical adequacy.
The [netclass completeness check](docs/netclass-completeness.md) checks captured
Default netclass field presence for the qualified KiCad settings schema. It does
not establish that the declared clearance or track-width values are suitable.

The [routing-rule check](docs/routing-rules.md) tests captured length/skew and
other supported bounds through native KiCad rules, with complete copper-contact
coverage required. Arcs, zones and unsupported topology remain unknown.
The [fab profile check](docs/fab-profile.md) runs captured, source-cited
fabrication limits through the same native rules. It also compares layer count
and outline size with the saved board and lists legal surcharged features
separately. No real fabricator preset is supplied or qualified.

[Offline catalog capture](docs/catalog.md) retains pinned snapshots and verifies
that derived quantities match retained raw attributes. [Part selection](docs/part-selection.md)
compares declared intervals and stock within that snapshot. Both now have checks
that use the captured baseline, sandbox and coordinator seal; selection binds one
explicit requirement. They do not authenticate upstream claims or establish
circuit acceptance. Real upstream catalog qualification remains pending.
A [supplier land-pattern check](docs/land-pattern.md) compares captured supplier
geometry with native KiCad footprints, including pad numbering. Its owned controls
are qualified; the actual C11702/0402 pair does not meet the requested 0.98 overlap
threshold, so that acceptance example remains unresolved.

[Datasheet acquisition](docs/datasheet-capture.md) captures explicitly supplied
URLs before offline checks, with byte limits, deadlines and per-part missing
records. Explicit part manifests can preserve these obligations through capture
and offline facts checks, including missing or uncited PDFs. Automatic propagation
through the generation workflow remains pending; capturing a PDF does not verify
its applicability or its engineering claims.

[Reference-block capture](docs/reference-block-interface.md) retains files from a
pinned local commit and checks declared ports against the native sheet. It credits
sealed interface evidence only for the block's actual native components and
hierarchy. Parts subsets, layouts and adapted designs still need qualification;
a passing run on a separate schematic cannot qualify the block.

[Captured library checks](docs/footprint-libraries.md) compare declared symbol pins
with footprint pad inventories. [Symbol context](docs/symbol-context.md) exposes
captured library pin references. The [native-bound mode](docs/native-binding.md)
requires the declaration to match every native board component, its exact
selection and its captured library pins; declared-only reports remain distinct.
A [land-pattern geometry kernel](docs/land-geometry.md) compares supported pad
shapes using exact areas or certified bounds. Its supplier/native check adapter
and supplier qualification remain pending; the kernel alone cannot accept a part.

The [placement check](docs/placement.md#placement-check-kind) grades one
declared criterion per check: native DRC courtyard overlap and footprint
keepouts from an earlier DRC, outline containment, courtyard edge clearance,
declared connector edges and a pad-centre ratsnest proxy. An
[initial placer](docs/placer.md) proposes deterministic positions on an isolated
copy and validates the saved result with native tools. The
[placement campaign adapter](docs/placement-adapter.md) runs the placer as a
receipted coordinator step and refuses edits other than footprint translations
and quarter turns. Automatic campaign wiring remains pending; these checks do
not establish routing quality or a fabrication-ready board.

An installed kicad-happy check retains schematic/PCB/thermal/EMC
outputs, with [qualified narrow adapters](docs/kicad-happy.md) for low-pass RC,
ideal regulator feedback, complete typed power-pin inventory, USB protection
presence, capacitor voltage utilization, generic LDO temperature estimates and
ESD placement distance. Other analyzer rules remain informational. Generic
producer/parser interfaces support additional measurements and coupled margins. Reviewer profiles explicitly select Claude, Codex or Agy through
their subscription CLIs; authentication or quota failures stop the affected call
without a provider fallback.

Before each reviewer, adjudicator or implementation call, a
[preflight guard](docs/result-reference.md#pre-inference-environment-receipts)
checks captured tool and policy identities and tests sandbox execution. Drift or
an unavailable prerequisite stops the call with a retained diagnostic.

[Packet navigation](docs/result-reference.md#factual-packet-navigation) links
native components, pins, nets and board primitives to declared checks and
requirements. Bounded views retain access to the full baseline and raw evidence;
ambiguous identities stay unresolved. The index supplies navigation, not defect
authority. Its effect on model token use and detection is not yet measured.
An initial matched-version pilot stopped when both control reviewers timed out;
no replacement samples or performance claims were made.

Bench corrections bind to the complete captured design inventory. Changing a
component, connection, layout or library makes calibrated acceptance unavailable
until fresh observations cover the changed design. The nominal simulation stays
available for diagnosis; an old correction cannot make a new design pass.
[Recoverable bench transactions](docs/bench-feedback.md#recoverable-feedback-transactions)
retain publication and review-attempt history across interruptions. Recovery
never repeats inference automatically; an uncertain review needs an explicit
fresh attempt, and retained evidence remains protected from overlapping outputs.

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

A [Dockerfile](Dockerfile) pins that toolchain for checks-only runs. The image
has no reviewer harness, and Bubblewrap inside it needs three explicit Docker
confinement relaxations, documented with what each grants in the
[container reference](docs/container.md). A reusable GitHub Action runs
checks-only reviews in the image for consumer KiCad projects and judges them
from `result.json`.

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
review contexts, single-writer ownership, regression detection, empirical
feedback, doctor's binary hashes and the CI action's result gate. The original
RC defect-and-repair demonstration used actual agent
reviews. The newer complete campaign and bench-feedback loops are exercised with
scripted agent responses and synthetic bench samples. A live subscription
campaign demonstrated independent review and rejection of an invalid adjudicator
citation; it did not complete an automatic repair. These are distinct levels of
evidence, recorded in the [closed-loop verification report](docs/closed-loop.md).
Project CI is defined to build the image and run the suite, documentation check
and both RC fixtures there on pushes to `main`; those steps have been run locally
in the image, not yet on a hosted runner.

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
