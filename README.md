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
| [3D PCB viewer](docs/pcb-viewer.md) | Inspect a saved board or captured candidate in a local browser, using KiCad's native GLB export and bundled browser assets. |
| [Native 3D renders](docs/pcb-renders.md) | Produce top, bottom and isometric PNGs with KiCad's ray tracer, model coverage and retained run records. |

Campaigns are designed to terminate with satisfied criteria, an explicit
escalation, or an incomplete execution. They do not promise that an arbitrary
set of physical constraints has a feasible solution. Bench feedback updates
empirical correction envelopes; it does not automatically discover a new physical
model or acquire measurements from instruments.

## What is implemented today

Original [RC stage fixtures](docs/rc-stage-fixtures.md) exercise unplaced,
placed-unrouted and seeded placement defects with native checks and a KRT
input receipt. The exact FOME-MC control derivatives requested by AGENTEE-103
remain blocked by unresolved third-party rights.

The [empty-candidate scaffold](docs/scaffold.md) creates deterministic native
projects from captured envelope and parts records plus an explicit stage-one
scope. It preserves project rules, checks and baseline sources, pre-creates child
sheets and leaves native execution to the ordinary review/campaign workflow.
Automatic stage-one scope compilation and hierarchical staged presence remain
unavailable.

An optional [staged campaign](docs/staged-campaign.md) keeps later checks behind
earlier gates. Missing expected design content requires a native observation; it
cannot pass merely because an empty design produces no rule violations. Progress
is measured in the earliest failing stage, with regression and retry limits.
Existing unstaged campaigns keep their convergence rules. Coordinator steps
follow the earliest failing stage. The retained writer prompt lists declared steps
and their outputs; staged campaigns refuse writer edits to outputs owned by a
scheduled step. Arbitrary writer commands have no stage-specific OS allowlist.
[Stage receipts](docs/stage-receipts.md) support artifact-only steps,
such as fab export, with an empty write scope and retained artifact hashes.
An artifact-only step is unadoptable if any declared artifact entry lacks a hash.
A complete generation rung with live model review and fabrication
acceptance remains pending.

An [offline intent importer](docs/design-intent.md) preserves source requirements
and exact voltage values. [Decision capture](docs/design-dialog.md) records
answers and their dependencies; missing answers remain unresolved. These tools
prepare inputs, but do not choose executable acceptance checks or generate a
verified circuit. The [requirement-record compiler](docs/requirement-scope.md)
turns an owner-authored record of spec quotes and check mappings into a scope/2;
it refuses unmapped requirements and uncited spec sentences.

[Board envelope declarations](docs/board-envelope.md) bind mechanical constraints
and derived requirements into the captured baseline. A reference document becomes
binding only when declared or consumed by the scope. The registered native check
compares exact outline and copper layers. Complete production envelope capture
remains unavailable: stackup lacks qualified copper-weight authority, and holes,
keepouts and multiple fixed connectors lack individual bindings. Capture refuses
those gaps.

Built-in checks cover KiCad ERC, DRC and schematic parity, native netlist export
with captured symbol-instance coverage in both directions,
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
symbol pins and scoped sheet ports into physical nets. Symbols from libraries above
its node limit, such as `MCU_ST_STM32H7`, are first copied into a
[captured subset library](docs/symbol-capture.md) with source provenance. Hierarchical port names
that would short distinct local nets are rejected. Repeated sheet files and
multi-sheet global power remain unsupported. The [native emitter](docs/schematic-emitter.md)
produces flat and supported hierarchical schematics from that captured intent.
A [schematic readability lint](docs/schematic-lint.md) reports overlaps, wires
through pins, rail and divider orientation, rail labels, grid and title-block
findings that ERC does not produce. They are style findings unless a profile
raises a rule to defect.
[Forward annotation](docs/forward-annotation.md) plans and applies native-netlist
changes to a board with protected placement and routing. Full campaign generation
and hierarchical annotation application remain unqualified.
[Sub-layout reuse](docs/sublayout-reuse.md) copies a captured module layout's
placement and routing into new staged instances by anchor offset, quarter-turn
rotation and a unanimous pad vote for nets. Copper on unmapped nets is withheld
and reported, and a missing sub-board leaves its group unknown.
[Netlist parity](docs/netlist-parity.md) compares native connectivity with captured
intent and libraries or an explicit golden netlist. Resolved intent is rederived
before comparison. Qualified virtual power symbols omitted by native XML are
listed in the parity report; physical terminals remain exact. Exact instance
connectivity catches exchanges that collapsed part labels can miss; ambiguous
correspondence and search exhaustion remain unknown. Missing, malformed or
contradictory native connectivity artifacts leave parity coverage unknown.
[Additional netlist checks](docs/netlist-extra.md) test declared direct-net shorts
and anchored rail pairs, and warn about same-name net islands. Island warnings
are advisory. Neither check establishes electrical adequacy.
The [package consistency check](docs/package-consistency.md) compares each
footprint with its LCSC part's package in a pinned catalog through a captured
mapping table. Unmapped names are unknown; land-pattern geometry is not compared.
The [part-binding check](docs/part-binding.md) validates `LCSC`, `Manufacturer`,
`Partnumber` and typed `PARAM_*` properties across schematic sheets and board
footprints. Copper pads require binding even on footprints marked `virtual`,
`board_only` or `REF**`. Schematic/board disagreement is a defect; missing
properties are unknown.
The [netclass completeness check](docs/netclass-completeness.md) checks captured
Default netclass field presence for the qualified KiCad settings schema. It does
not establish that the declared clearance or track-width values are suitable.
[Net constraints](docs/net-constraints.md) turn the intent's per-net current,
clearance, differential-pair and impedance declarations into native netclasses
and DRC width rules. Widths for declared currents come from an IPC-2152 fit, and
`copper-width/1` reads the per-net minimums. Impedance is recorded, not computed.
The [fab exporter](docs/fab-export.md) produces Gerbers, drills, BOM, CPL and a
board-bound manifest offline. It requires identical assembled designators
across the board and CSVs before completing the package. A declared
`JLCPCB_Assembly=not_assembled` rule leaves marked populated parts out of the
BOM and CPL and lists them in the manifest.
The [fab-package consistency gate](docs/fab-consistency.md) checks a sealed
`fab_export` staging tree against its manifest, the pinned kicad-cli, the captured
board, the sealed netlist and the sealed geometry. It checks native CPL anchor
coordinates within 0.01 mm and sealed export logs for warnings. Gerber geometry,
DFM and assembly rotation corrections stay outside its coverage.
The [fab round-trip check](docs/fab-roundtrip.md) re-parses native Gerber and
Excellon output against the sealed geometry: enabled layers, PTH/NPTH drill
histograms, copper pad/via flash counts and outline bounds within 1 µm. Its
narrow parser fails closed on unsupported constructs; artwork is not compared.
The [CPL rotation check](docs/cpl-rotation.md) rewrites the exported CPL to
native pad-box midpoints. It applies a captured, versioned rotation policy and
requires every rotation to agree with the pin-1 frame of the captured supplier
footprint. Disagreement is a defect: a quarter-turn mismatch, a reflection or a
diode polarity mismatch. Its status stays `PREVIEW_REQUIRED`, because only the
JLCPCB placement preview validates rotation physically.

The [fab package done gate](docs/fab-package.md) (`fab_package`, declared last)
runs that export and gate in one check and seals the package's file hashes. A
scope with a `generation` marker is refused at capture without it, and a
satisfied generation campaign's `state.json` lists the hashes, which
`verify-campaign` recomputes.

The [routing-rule check](docs/routing-rules.md) tests captured length/skew and
other supported bounds through native KiCad rules, with complete copper-contact
coverage required. Arcs, zones and unsupported topology remain unknown.
The [campaign routing adapter](docs/routing-adapter.md) runs pinned kicad-route
with read-only state, retains hashed attempt diagnostics, and verifies routing
semantics before adopting a proposal. Fresh checks determine acceptance.
The [fab profile check](docs/fab-profile.md) runs captured, source-cited
fabrication limits through the same native rules. It also compares layer count
and outline size with the saved board and lists legal surcharged features
separately. Its `/2` conjunctive rules charge a via only when the drill and the
diameter are both under their bounds. No real fabricator preset is supplied or
qualified.
The [DFM geometry check](docs/dfm-geometry.md) applies seven deterministic rules
with captured thresholds to exact pcbnew geometry from the saved board: annular
ring, via aspect ratio, acid traps, SMD bridge gap, courtyard spacing,
differential-pair skew including arcs, and copper balance. It reports coded
findings with coordinates and gives no score. A missing fact, such as the saved
board thickness, leaves that rule unknown.

[Offline catalog capture](docs/catalog.md) retains pinned snapshots and verifies
that derived quantities match retained raw attributes. A pinned jlcparts feed
has a bounded host downloader and a native stocked-parts adapter; raw data stays
in private scratch. [Part selection](docs/part-selection.md)
compares declared intervals and stock within that snapshot. Both now have checks
that use the captured baseline, sandbox and coordinator seal; selection binds one
explicit requirement. They do not authenticate upstream claims or establish
circuit acceptance. Snapshot stock and prices describe their recorded dates.
An [approved-parts manifest](docs/approved-parts.md) freezes the declared parts
and their catalog, CAD and datasheet provenance in the campaign baseline.
The `parts_subset` check compares native symbol/footprint/supplier identities
and required instances; absent native evidence leaves coverage unknown.
The [Rung-1 captured-parts route](docs/rung1-parts.md) consumes that bundle
with an explicitly assembled J1 and runs the existing assembly and package checks.
A [supplier land-pattern check](docs/land-pattern.md) compares captured supplier
geometry with native KiCad footprints, including pad numbering. Its owned controls
are qualified; the actual C11702/0402 pair does not meet the requested 0.98 overlap
threshold, so that acceptance example remains unresolved pending an owner decision.

[Datasheet acquisition](docs/datasheet-capture.md) captures explicitly supplied
URLs before offline checks, with byte limits, deadlines and per-part missing
records. Explicit part manifests can preserve these obligations through capture
and offline facts checks, including missing or uncited PDFs. The rung-1 driver
captures every selected part's Datasheet in stage 2 and the emitter carries the URL
onto the symbol; capturing a PDF does not verify its applicability or its
engineering claims.

[Reference-block capture](docs/reference-block-interface.md) retains files from a
pinned local commit without Git transports or replacement refs, and checks
declared ports against the native sheet. Missing local blobs are refused. It credits
sealed interface evidence only for the block's actual native components and
hierarchy. [Block adaptation](docs/block-adaptation.md) copies a flat block into
a pre-created child, freezes its provenance and maps its requirements to fresh board checks.
Its native provenance check lists value/connectivity edits and grades boundary
bindings. Parts subsets and layouts still need qualification;
a passing run on a separate schematic cannot qualify the block.
[Block inventory capture](docs/block-inventory.md) writes one draft entry per
planned block from a pinned design commit, all unverified, each with its
reference set, a derived interface contract, library hashes and sourced pending
requirements. It also replays block adaptation fixtures. The owner's fome-mc
inventory and captures stay private until the owner decides on publication.

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
copy and validates the saved result with native tools. On placement turns of a staged campaign, the
[placement campaign adapter](docs/placement-adapter.md) gives the writer the
sealed placement findings, can run the placer as a receipted coordinator step,
and refuses edits other than footprint translations, quarter turns and qualified
side flips. Fresh checks grade every proposal. These checks do not establish
routing quality or a fabrication-ready board.

Campaign writers must account for `pcbnew.SaveBoard` rewriting sibling project
files. The immutable-input guard diagnoses this likely cause and still refuses
the change. Follow the [board-saving writer rule](docs/campaign-guide.md#saving-boards-without-changing-project-siblings)
to preserve project siblings while adopting a saved board.

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
Campaigns allow one [bounded output resubmission](docs/campaign-guide.md#bounded-output-resubmission)
per role per iteration by default. Both calls and the original validation error
are retained; checks, execution failures and environment drift remain terminal.

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

Thermal solvers, post-layout parasitic extraction, DFM analysis and BOM
lifecycle services are **not bundled**. The [`dfa_dft` check](docs/dfa-dft.md)
covers only declared connector orientation, test-point probe access and
test-point coverage, and derives a bring-up plan. They need separately qualified tools,

Thermal solvers, post-layout parasitic extraction, DFA/DFT analysis, DFM beyond the
seven geometry rules, and BOM lifecycle services are **not bundled**. They need separately qualified tools,
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
feedback, doctor's binary hashes, the CI action's result gate and the
file-origin inventory. The original
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

Optional [PCB DC path resistance](docs/dc-path.md) connects explicitly captured
materials and saved native copper dimensions to declared DC drop/loss limits.
Its initial qualification is limited to ideal F.Cu endpoint paths and bounded
series/parallel networks; unsupported conductors leave coverage incomplete.

## License

The [MIT License](LICENSE) is proposed for material written for agent-ee and
for outputs of its runs on that material, and it is declared in the package
metadata. The proposal is included for review; the owner has not yet approved it.

The MIT grant does not cover files that come from other projects. These include
the RC fixture copied from FOME-MC, logic in `src/agent_ee/checks.py` adapted
from FOME-MC, donor ECU design files and their libraries, stock KiCad libraries
inside the evidence archives, datasheets and fetched web pages. The
[file-origin inventory](docs/file-origins.json) classifies every tracked file by
origin, upstream license and publication status, and
[third-party origins](docs/third-party-origins.md) summarizes it for review.
Publication rights for those files are unresolved, and each origin needs an
owner decision. The public repository already contains many of them. That does
not establish a right to publish them.
