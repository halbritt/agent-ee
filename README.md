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

Electrical graph checks support [bounded design inventories](docs/design-size-limits.md)
of up to 1,024 components, 2,048 nets and 8,192 physical terminals. Exact-search,
byte and geometric work guards still apply.

[Operating-temperature acceptance](docs/assembly-readiness.md#digikey-operating-temperature)
checks reduced-limit parts against their own captured ratings and requires
captured thermal validation before they can pass (AGENTEE-310).

The [telemetry-pod evidence index](docs/evidence-index.md) maps sealed checks to
the pod's nine criteria while retaining bench and operator obligations.

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
5. **Adjudicate centrally.** Findings get explicit dispositions. New reviewer
   and adjudicator citations include [validated document locations](docs/result-reference.md#structured-citations).
   Packets list evidence formats and locator kinds, and citation resubmissions
   identify the file and required kind (line ranges for native KiCad text).
   Retained records keep their versioned compatibility rule. Under scope/2 and scope/3,
   an accepted defect must be a coordinator check finding backed by sealed
   evidence and a mapped requirement violation. Reviewer hypotheses need
   reproduction or remain concerns; agreement alone cannot establish a defect.
6. **Repair and recheck when authorized.** One implementation agent may edit
   declared design files in a disposable copy. A new candidate goes through the
   checks and independent reviews again. Requirements and verification inputs
   cannot be edited to make the repair pass.

The [architecture diagrams](docs/architecture.md) show the information and write
boundaries. The [placer](docs/placer.md#through-hole-reservations-and-native-publication-gate-agentee-318)
reserves native through-hole copper and drills across both sides and refuses
proposals that introduce native DRC shorts, clearance, PTH courtyard findings
or inter-footprint solder-mask bridges. Collision comparisons include saved
geometry and footprint transforms (AGENTEE-325). Shared native item identities
across footprints and custom rules with ignored severity refuse publication.

Declared [placer obstacles](docs/placer.md#declared-obstacle-unions-and-reserved-gap-provenance-agentee-338)
reserve the exact union of touching or overlapping polygons. The pod's 0.25 mm
packing gap is an agent-chosen advisory default. Its supplied value remains a
search reservation, including against fixed courtyards; capacity proofs that
bind an unsourced gap report unknown and name it for confirmation.

The [artwork pass](docs/silk-artwork.md) relocates permitted silk fields and
publishes silk-only conflicts as open defects in the placement receipt, preserving
graphics and required markings. This establishes placement feasibility only;
downstream fab, verification and release gates refuse carried open defects,
naming their native fields/graphics and source receipts (AGENTEE-333).
Declared [pad-level locality](docs/placer.md#pad-level-electrical-locality-agentee-322)
guides packing and separately checks supply, return and switch/inductor pad
copper-centroid distances against sourced limits.
[Locality provenance](docs/placer.md#locality-provenance-agentee-337) keeps unsourced
agent-selected bounds advisory in checks, packing and block-candidate admission.
Captured JLCPCB spacing supplies necessary copper floors and advisory assembly estimates, with
whole-footprint minima still unqualified. Captured pad UUIDs select
multi-pad pins. Explicit `buck-output-loop` declarations bind the supply source
and switch target to the inductor, the return source and switch source to the
IC, and return pads to a declared net distinct from supply. Switch nets must
differ from supply and return. Distinct-source supply/return net sets must be
disjoint even without a kind. Direct placement-index construction re-validates
these declarations and snapshots the validated document before indexing it.
Coordinator packing binds to the check's immutable locality file. Origin-group
distance supplies no electrical acceptance.
[Incremental locality search](docs/placer.md#incremental-locality-search-and-diagnostics-agentee-332)
caches selected pads and retains typed partial diagnostics on its optional search deadline.
[Joint locality clusters](docs/placer.md#joint-locality-clusters-agentee-335)
preserve per-part plans, then try capacitors and switch peers together with the
remaining budget. Joint refusals retain named cluster diagnostics.
[Placement blocks](docs/blocks.md) come from the sealed child sheets or a hashed
engineer allocation file. Each block is packed alone into a rigid, versioned block
candidate, with relative poses, an obligation grade, a courtyard witness,
per-side outlines and source hashes. A compaction pass re-packs each block on
shrinking outlines and keeps the smallest box with the same obligations.
Candidates are search-derived and advisory.
A candidate with missing, failed or stale evidence refuses: admission binds its
sources and policy and re-grades every member from the board.
Matching by footprint, land-pattern hash, pin roles and declared value tolerance
refuses any mismatch.
The [block library](docs/block-library.md) keeps versioned known-good blocks in
`blocks/` with provenance receipts. Its entries come from KiCad board regions,
scale-verified datasheet figures and project clusters. A lookup returns each block
with its provenance class. Project clusters, including the four telemetry pod
regulator clusters, are search-derived and advisory. Hard classes are disabled
(`authority_unavailable`) until a verified owner authority exists (AGENTEE-348), and
every use is admitted by `blocks.admit` on the target.

[Block arrangement](docs/blocks.md#stage-b-arrangement-agentee-340) places the
admitted candidates as rigid macros, then packs the residual parts with the
placer, with block members fixed. The search uses deterministic conflict-directed
backjumping with typed exhaustion. Candidate origins align to block members as
well as block outlines, and the blocked regions are cached per placed block, so
a backjump recomputes only what moved. Block rules (keep-outs, opposite-side
shadows, no block over a fixed part, thermal spread) are hard only with a
citation. Boundary roles are bound to their board nets. The complete plan goes
through the writer's native gate.

Native tools and agents run in Bubblewrap sandboxes; supported writers
also acquire process leases to prevent concurrent campaign mutations.

The [synthetic benchmark utilities](docs/seeded-benchmark.md) preregister five
pinned public boards and 30 assembly, schematic-wiring and pad-geometry mutations.
A second corpus seeds 16 review-derived defect classes into scratch copies of the
hierarchical fome-mc board; its expectations and reports live in a private
Git-backed store until the owner decides on publication (AGENTEE-118). Sources
and generated designs remain outside the repository. The public corpus records a
verified dependency closure, deterministic control false-alarm bounds and native
rerun comparisons. The closure audit exits 1 whenever tool or font licence
evidence is unverified; owner review and any publication remain outstanding.

The offline [generation benchmark](docs/generation-benchmark.md) pins task files,
keeps evaluator rules outside the generator sandbox, and reports exact score
bounds while coverage is unknown. A series freeze requires external task pins,
retains executor evidence, and produces a manifest digest for independent custody.
Authenticated summaries verify that digest and replay retained sealed evidence
before scoring; recorded failed generation never passes. Unanchored summaries
remain diagnostic local consistency checks.
Unsafe writer outputs remain failed attempts with unknown checks.
Its original native fixture qualifies the
harness; importing the ten unlicensed upstream prompts remains blocked.

The [false-positive calibration](docs/fp-calibration.md) measures the reviewers
themselves. It shows each profile reviewer pairs made from a design and its seeded
variants, in both orders and repeatedly. Every variant's label is first proven by
native netlist identity. The report gives false-positive and false-negative rates
and order sensitivity, and is computed only from the retained verdicts.

The optional [pin-contract check](docs/pin-contracts.md) checks page-cited voltage,
connection and capacitor/pull-up obligations against native connectivity. Missing
contracts, unverified citations and ambiguous identities remain unknown coverage.

Optional [schematic images](docs/schematic-renders.md) give reviewers a 20 mm
coordinate grid and source-bound per-sheet pixel drift. Missing images and
renderer changes remain unknown visual coverage.

## Beyond a single review

| Capability | What it is for |
| --- | --- |
| [Bounded repair campaigns](docs/campaign-guide.md) | Turn established defects into implementation work, protect prior successful `pass` and `info` checks, detect repeated designs and regressions, and stop on stalled progress or exhausted budgets. A stop diagnostic preserves the competing constraints for an engineering decision. |
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

[Historical datasheet imports](docs/datasheet-capture.md#offline-historical-import-agentee-306)
retain hash-bound source notes and PDF bytes offline, explicitly preserving unknown
redirect history and unrecorded fetch times (AGENTEE-306). Consumer receipts
retain the acquisition method; live cascade replay refuses historical acquisitions
(AGENTEE-311).

An original [pod-scale placement qualification](docs/placer.md#pod-scale-qualification-agentee-181)
exercises 90–112 mechanical stand-ins on both sides across 80 × 50 to
90 × 65 mm outlines, with a 48 × 21 mm keepout and native placement evidence.
It measures fixture capacity and determinism; actual pod geometry needs its own checks.

Original [RC stage fixtures](docs/rc-stage-fixtures.md) exercise unplaced,
placed-unrouted and seeded placement defects with native checks and a KRT
input receipt. The [routing equivalence check](docs/routing-equivalence.md)
compares repeated routed outputs by normalized copper geometry and records
byte differences without failing them. The exact FOME-MC control derivatives requested by AGENTEE-103
remain blocked by unresolved third-party rights.

The [empty-candidate scaffold](docs/scaffold.md) creates deterministic native
projects from captured envelope and parts records plus an explicit stage-one
scope. It preserves project rules, checks and baseline sources, pre-creates child
sheets and leaves native execution to the ordinary review/campaign workflow. Its
stage-two producer derives the checks, mappings, stage policy and `.kicad_pro`
rules from the envelope, its fab profile and the parts manifest instead, and
refuses incomplete inputs. Optional captured net constraints add per-net classes,
assignments, width rules and a Default completeness check. Stage-three preparation
writes declared holes, keepouts, locked fixed footprints and the IMU marking through
a campaign pre-receipt. Its [packed-board silkscreen policy](docs/scaffold.md#packed-board-silkscreen-policy-agentee-276)
keeps hole and dense passive references on fabrication layers, handles declared
overhang graphics, adds in-board reference/angle callouts for overhang parts,
and reserves those callouts and the IMU marking during packing. Explicit copper
layer roles support mixed signal/plane stackups.
The [IMU axes criterion](docs/placement.md#imu-axes) checks that marking and the IMU
transform using captured axis facts. Hierarchical staged
presence remains unavailable.

The [footprint index](docs/footprint-libraries.md#native-custom-pads-agentee-300)
qualifies bounded solid-connected custom SMD pads through sandboxed pcbnew loads,
retaining native copper extents and electrical numbers for forward annotation.
Primitive coordinates and local copper extents are limited to +/-1000 mm;
widths are limited to 1000 mm. Rotation-invariant copper reach, including pad
translation and half the stroke width, must stay within 1000 mm of the footprint
origin. The native merged pad must have exactly one
copper outline, including its anchor (AGENTEE-304). Declared custom-pad primitives share these bounds and require one merged outline
on each copper layer while retaining numbering-only records (AGENTEE-324).
Unsupported custom-pad constructs remain unknown coverage (AGENTEE-300).
The [placer](docs/placer.md#native-custom-pads-agentee-307) also qualifies this
subset for rigid placement, preserving its complete primitives and native copper
extents through save/reload. The [placement write scope](docs/placement-adapter.md#custom-pad-side-flips-agentee-307)
also qualifies exact native side flips of these footprints and their arcs (AGENTEE-307).

The [schematic emitter](docs/schematic-emitter.md) preserves KiCad 10 private
symbol metadata, supports explicit component BOM flags, and preserves canonical
structured datasheet bindings up to 2048 bytes and matching plain datasheet URLs
beyond 256 characters (AGENTEE-293, AGENTEE-296). JSON-looking legacy datasheet
text receives a typed diagnostic. Declared
[copper features](docs/fab-export.md#copper-features) qualify for fab BOM/CPL
exclusion through both fab gates, with manufacturer/distributor binding detection
and typed refusal of malformed property values (AGENTEE-289, AGENTEE-290).
The [parts manifest](docs/approved-parts.md#declared-on-board-copper-features)
also admits source-bound copper jumper declarations with exact native pins, nets
and population flags (AGENTEE-305). [Binding fill](docs/binding-fill.md#explicit-source-migration-agentee-309)
projects those declarations and explicitly migrates validated legacy probe source
markers with a receipt artifact recording old/new encoding and decoded identity hashes (AGENTEE-309). Fab checks compare those declarations with
their explicit copper reference list; catalog interval checks exclude copper,
and unsupported purchased-part consumers return typed refusals (AGENTEE-315).

An optional [staged campaign](docs/staged-campaign.md) keeps later checks behind
earlier gates. Missing expected design content requires a native observation; it
cannot pass merely because an empty design produces no rule violations. Progress
is measured in the earliest failing stage, with regression and retry limits.
Schematic presence excludes only library power symbols explicitly marked outside
both BOM and board; contradictory `#` references leave coverage unknown.
Configuration loading refuses a `finding_retry_limit` below 2 so that the first
finding appearance leaves room for at least one repair attempt.
Existing unstaged campaigns keep their convergence rules. Coordinator steps
follow the earliest failing stage. The retained writer prompt lists declared steps
and their outputs; staged campaigns refuse writer edits to outputs owned by a
scheduled step. Arbitrary writer commands have no stage-specific OS allowlist.
A [mutable-intent schematic stage](docs/intent-schematic-stage.md) binds writer-authored
connectivity intent to receipted emission and forward annotation on each schematic turn.
[Stable regeneration](docs/schematic-regeneration.md) optionally retains prior
symbol placement, fields, UUIDs and reference bindings across those turns,
with explicit change reports and refusal of conflicting native edits.
[Stage receipts](docs/stage-receipts.md) support artifact-only steps,
such as fab export, with an empty write scope and retained artifact hashes.
New receipts bind tool identity observations to held mount descriptors and refuse
hard links into live package source (AGENTEE-349); historical path-form replay
retains its documented lower assurance.
An artifact-only step is unadoptable if any declared artifact entry lacks a hash.
With a declared fab step in the stage order, the fab checks require its sealed
package and compare it with fresh sandbox exports before passing.
Optional campaign `pre_receipts` import coordinator receipts made before run 1;
`verify-campaign` revalidates them against the exact initial capture in their folders.
The [block-first gate](docs/block-first.md) checks separately captured child sheets
with native ERC and exact topology parity, refuses integration until every block
has current sealed evidence, and runs fresh checks before publishing the proposed
hierarchy. Interface or dependency edits require a new block run.
A complete generation rung with live model review and fabrication
acceptance remains pending.

An [offline intent importer](docs/design-intent.md) preserves source requirements
and exact voltage values. [Decision capture](docs/design-dialog.md) records
answers and their dependencies; missing or invalid answers, including JSON that
exceeds the decoder's recursion limit, remain recorded as unresolved. These tools
prepare inputs, but do not choose executable acceptance checks or generate a
verified circuit. The [requirement-record compiler](docs/requirement-scope.md)
turns an owner-authored record of spec quotes and check mappings into a scope/2;
it refuses unmapped requirements and uncited spec sentences. Its
[spec-record map path](docs/requirement-map.md) compiles scope/3 with registered
check predicates, explicit limitations and bench obligations; prose needs
explicit paragraph or byte-span accounting. [Spec capture](docs/spec-record.md)
turns a markdown spec's table rows, bullets and numbered items into requirement
IDs with byte-exact locators, verbatim cell text and per-item hashes; it refuses
malformed tables and colliding IDs with the byte offset and infers no values.

[Board envelope declarations](docs/board-envelope.md) bind mechanical constraints
and derived requirements into the captured baseline. A reference document becomes
binding only when declared or consumed by the scope. The registered native check
compares exact outline and copper layers, and grades stackup against the referenced
source-verified profile without converting thickness to copper ounces. Several fixed
connectors receive separate reference-selected witnesses. Capture succeeds for a
fully bound scope, including id-selected holes and keepouts. Antenna polygons
are derived from verified datasheet facts and compared to the module-owned rule
area after its native transform. Unbound generator mechanics remain refusals.
Each constraint may carry [provenance](docs/board-envelope.md#constraint-provenance):
owner-stated (quoted from a hashed source), derived, or assumed. Assumed or
owner-weakened limits are soft. They are reported for owner confirmation and
never fail placement as a defect. Constraints without provenance stay enforced
and are reported as unknown provenance.
Declared [keep-apart rules](docs/placement.md#keep-tall-and-heavy-parts-apart)
grade courtyard separation; [component height](docs/board-envelope.md#component-height)
grades sourced installed heights against an envelope limit. Missing height
sources remain unknown, including hand-fitted connectors.

The [telemetry-pod fabrication inputs](docs/telemetry-pod-fab-profile.md) record
eight dated JLCPCB JLC04161H-7628 limits and bind known mechanics to the pod spec.
The envelope records nominal 1.6 mm and physical 1.5862 mm thickness separately,
with a cited ±10% tolerance. Generator-selected mechanics remain explicit
unbound obligations for AGENTEE-177/179, so acceptance stays unknown.

The offline [io-map compiler](docs/io-map.md) turns a declared pinout CSV into
a hardware-interface contract, preserving no-connects and unresolved pins.
Concrete MCU rows require a declared direct conductor or a declared indirect
path through named intermediate parts. Each conductor segment and part identity
is then checked on the native netlist; transfer behavior is not claimed.
The CLI requires readable requirement source bytes and records their SHA-256;
a missing source exits 2 with a typed error before writing a contract.
Each local source path uses one digest on all invariants or none; a hashed
requirement source becomes an immutable hardware-interface input.

Built-in checks cover KiCad ERC, DRC and schematic parity, with optional
[captured owner-approved exact violation waivers](docs/violation-waivers.md), native netlist export
with captured symbol-instance coverage in both directions,
saved-board geometry, [declared hardware interfaces](docs/hardware-interface.md),
[page-cited datasheet facts and native symbol pin maps](docs/datasheet-facts.md),
and an ideal passive R/C/L transient simulation through ngspice. An optional
[qualified RC tolerance check](docs/passive-corners.md) retains complete endpoint
ensembles for one unloaded first-order topology. A separate
[captured active-device check](docs/active-spice.md) supports explicit subcircuit
pin bindings and declared transient/AC/DC-sweep measurements, with
[role-keyed templates](docs/spice-templates.md) that derive regulator,
amplifier and filter testbenches from captured targets without defaults. Its
committed control models are synthetic; vendor-model qualification and publication rights are separate
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
produces flat and supported hierarchical schematics from that captured intent,
including inherited pins and units of captured `extends` symbols. A flat
layout can opt into the [functional presentation](docs/schematic-emitter.md#functional-presentation):
titled groups, decoupling and series parts wired next to their pins, and the
same netlist and identities.
Generator and rung-1 drivers capture only selected symbols and their ancestors
by default; the emitter parses each library once per emission. The
[130-component benchmark](docs/schematic-emitter.md#library-subsets-and-emission-cost)
records timings and native netlist parity against captured library hashes.
A [schematic readability lint](docs/schematic-lint.md) reports overlaps, wires
through pins, rail and divider orientation, rail labels, grid and title-block
findings that ERC does not produce. They are style findings unless a profile
raises a rule to defect.
[Forward annotation](docs/forward-annotation.md) plans and applies native-netlist
changes to a board with protected placement and routing. Full campaign generation
and hierarchical annotation application remain unqualified.
The board producers share KiCad native slash escaping for net names; see the
[qualified encoding boundary](docs/forward-annotation.md#native-net-name-encoding-agentee-220).

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
Explicit fixed-reference anchors and captured required-name policies seed the
exact search for repeated channels; the report records anchors and search steps.
[Additional netlist checks](docs/netlist-extra.md) test declared direct-net shorts
and anchored rail pairs, and warn about same-name net islands. Island warnings
are advisory. Neither check establishes electrical adequacy.
[Requirement topology rules](docs/topology-rules.md) check requirement-tagged
structural rules over the native netlist, such as at least eight capacitors
between two rails, no diode between two nets, or a series gate resistor. An
expected-component set is matched to references by bounded backtracking.
Absent names, unreadable values and an exhausted search are unknown.
[Net-name provenance](docs/net-names.md) checks that owner-declared net names
survive generation: each names exactly one native net at its declared terminals.
Suggested-name collisions are reported as information with deterministic
proposals, and names without a level annotation are unknown.
[Interval assertions](docs/interval-assertions.md) evaluate owner-declared
relations such as divider outputs and rating margins over pinned catalog
tolerances in exact worst-case interval arithmetic, tracing every bound to its
requirement or catalog claim; unbounded operands stay unknown.
The [regeneration determinism check](docs/design-diff.md) compares a regenerated
board, schematic or netlist with a reference after removing UUIDs, timestamps
and tool headers. It reports per-path differences and structural and placement
hashes, and records byte differences without grading them.

[Design-rule cards](docs/design-rules.md) give the implementation writer
versioned guidance, such as I2C pull-ups and addresses, crystal load caps and
reset RC. Each card is graded only by its qualified connectivity adapter.
Advisory cards have no adapter: the writer sees them, but they can never
satisfy a requirement.
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
The [pod driver](docs/pod-driver.md#net-constraint-application) can apply captured
net constraints under receipt and register saved native class-assignment checks.
Standalone class-assignment checks also refuse declared-class patterns that
match undeclared board nets or use legacy escaped slashes.
[Net constraints](docs/net-constraints.md) turn the intent's per-net current,
clearance, differential-pair and impedance declarations into native netclasses
and DRC width rules. Widths for declared currents come from an IPC-2152 fit, and
`copper-width/1` reads the per-net minimums. Impedance is recorded, not computed.
The [GNSS RF controls](docs/gnss-rf-path.md) exercise width and length separately;
a captured W2 calculation supplies RF_50 width and clearance on the source-backed
native `/POD_GNSS_RF` net. Unmatched exact constraint nets refuse check and
router setup with the missing names. The 50 ohm target remains recorded as unenforced. The pinned router probe passes
the emitted width minimum and detects a narrower segment. Actual impedance and
the pod RF maximum length remain unverified; the controls' 15 mm is test-only.
A [dedicated RF reviewer](docs/rf-review.md) is required whenever captured sources
carry RF parts, nets or constraint classes. Non-RF runs record an explicit
not-applicable basis. Its findings join adjudication and correction closure;
its review is never RF qualification.
The [fab exporter](docs/fab-export.md) produces Gerbers, drills, BOM, CPL and a
board-bound manifest offline. It requires identical assembled designators
across the board and CSVs before completing the package. A declared
`JLCPCB_Assembly=not_assembled` rule leaves marked populated parts out of the
BOM and CPL and lists them in the manifest. Explicitly declared
[copper features](docs/fab-export.md#copper-features), such as solder jumpers,
need both native exclusions, no model and fields qualified by the copper
allow-list, which admits only exact standard and KiCad internal field names
with nonempty values; both fab gates verify their absence from the BOM and CPL.
For recorded hand-fit parts, [assembly readiness](docs/assembly-readiness.md#declared-hand-fit-deviations)
requires a purchase source, captured evidence and the assembly marker, reports
them as declared deviations, and grades stock for the remaining BOM. Stock
exceptions and the [owner hand-fit policy](docs/assembly-readiness.md#owner-hand-fit-policy)
are distinct classes. The policy class admits only parts that the cited owner
text names unconditionally.

Declared copper features also admit courtyard graphics and bounded own-side mask
openings over their pad/copper extent, with a 0.05 mm envelope margin.
Graphics and properties with an extra `(layers ...)` token remain refused,
even when `(layer ...)` names silk, fab, courtyard or mask.

The [fab-package consistency gate](docs/fab-consistency.md) checks a sealed
`fab_export` staging tree against its manifest, the pinned kicad-cli, the captured
board, the sealed netlist and the sealed geometry. It checks native CPL anchor
coordinates within 0.01 mm and sealed export logs for warnings. Stock
`${KICAD10_3DMODEL_DIR}` models resolve only against a captured, pinned
stock-model receipt that the scope declares. Gerber geometry,
DFM and assembly rotation corrections stay outside its coverage.
The [fab round-trip check](docs/fab-roundtrip.md) re-parses native Gerber and
Excellon output against the sealed geometry: enabled layers, PTH/NPTH drill
histograms, copper pad/via flash counts and extents, track/arc and zone-region
counts, and outline bounds within 1 µm. Its owned parser covers KiCad 10's
aperture macros, G75 arcs and regions and fails closed on anything else;
artwork is not rendered.
The [CPL rotation check](docs/cpl-rotation.md) rewrites the exported CPL to
native pad-box midpoints. It applies a captured, versioned rotation policy and
requires every rotation to agree with the pin-1 frame of the captured supplier
footprint. Disagreement is a defect: a quarter-turn mismatch, a reflection or a
diode polarity mismatch. Its status stays `PREVIEW_REQUIRED`, because only the
JLCPCB placement preview validates rotation physically. The generator fab stage
and both rung-1 scopes apply this check from `--cpl-inputs` captures and retain
the corrected package with its policy and correction manifest; in the rung-1
campaign the done gate seals the corrected CPL. A missing capture fails closed
in the generator. Done predicates admit only the rotation statuses in
`fab_package.DONE_ROTATION_STATUSES`; a pending preview reaches done only as an
operator obligation, with every corrected CPL designator and its side listed as
`preview_obligations` to confirm in the JLCPCB placement preview before
ordering ([preview obligations](docs/fab-package.md#preview-obligations-agentee-151)).
The [JLCPCB cost estimate](docs/jlc-cost-estimate.md) is informational and
never grades a design. It prices the sealed package from a captured, versioned
pricing table and the catalog snapshot, and every number cites its table entry.
It reports Basic/Preferred/Extended counts, with unknown-class parts counted as
unknown.

The [saved JLCPCB DFM check](docs/jlc-dfm.md) binds an owner-uploaded Gerber zip
and its retained report to a candidate offline. Error findings are defects;
warnings remain advisory, and missing or mismatched reports remain unknown.

The [fab package done gate](docs/fab-package.md) (`fab_package`, followed only by
bound DFM checks) runs that export and gate in one check and seals the package's file hashes.
It accepts the export's variant, assembly exclusion and probe options, plus the
consistency gate's captured model libraries. A scope with a `generation` marker is refused at capture without it, and a
satisfied generation campaign's `state.json` lists the hashes, which
`verify-campaign` recomputes.

The [routing-rule check](docs/routing-rules.md) tests captured length/skew and
other supported bounds through native KiCad rules, with complete copper-contact
coverage required. Arcs, zones and unsupported topology remain unknown.
The [campaign routing adapter](docs/routing-adapter.md) runs pinned kicad-route
`5f1c072` (versioned `kicad-route/route/1` results; opt-in `route/2` differential pairs)
with read-only state, retains hashed attempt diagnostics, and verifies routing
semantics before adopting a proposal. Fresh checks determine acceptance.
Its optional [per-net layer policy](docs/routing-adapter.md#per-net-signal-layers-agentee-258)
rejects disallowed tracks and via spans and can require reference-plane fill
under the declared nets' routed length. The RF review grades captured per-net
RF layer requirements on the verified routed board's native facts.
The [fab profile check](docs/fab-profile.md) runs captured, source-cited
fabrication limits through the same native rules. It also compares layer count
and outline size with the saved board and lists legal surcharged features
separately. Its `/2` conjunctive rules charge a via only when the drill and the
diameter are both under their bounds. An explicit [dated JLCPCB via preset](docs/fab-profile.md#dated-jlcpcb-standard-via-preset)
records the published small-via surcharge options, grades drills below the
0.15 mm minimum as defects and reports unlisted small drills as unknown; it
covers a subset of DFM
and supplies no pricing or supplier approval.
The [DFM geometry check](docs/dfm-geometry.md) applies seven deterministic rules
with captured thresholds to exact pcbnew geometry from the saved board: annular
ring, via aspect ratio, acid traps, SMD bridge gap, courtyard spacing,
differential-pair skew including arcs, and copper balance. It reports coded
findings with coordinates and gives no score. A missing fact, such as the saved
board thickness, leaves that rule unknown.
The [RF geometry check](docs/rf-geometry.md) grades one declared RF net on the
routed board against a captured structure: grounded-CPW width, gap and coplanar
and reference ground (calculation sources require a declared matching SHA-256),
stitching via pitch along each branch, routed length per branch of an explicit trunk
and stub tree, and the reference-plane return-corridor guard at a declared width
with a recorded basis. An absent structure is a defect and never counts as
microstrip. Missing bounds, widths or fills are unknown. It is not RF
qualification.

[Offline catalog capture](docs/catalog.md) retains pinned snapshots and verifies
that derived quantities match retained raw attributes. A pinned jlcparts feed
has a bounded host downloader and a native stocked-parts adapter; raw data stays
in private scratch. [Part selection](docs/part-selection.md)
compares declared intervals, switching-regulator rectification and stock within
that snapshot. The DC-DC importer retains raw claims and typed ratings; missing
constrained attributes exclude a row with a reason in the receipt. Both have checks
that use the captured baseline, sandbox and coordinator seal; selection binds one
explicit requirement. Their [full-feed runtime qualification](docs/catalog.md#full-feed-runtime-qualification)
uses a declared 600-second helper ceiling and retains sealed evidence in private
scratch. They do not authenticate upstream claims or establish
circuit acceptance. Snapshot stock and prices describe their recorded dates.
A separate pinned [catalog identity index](docs/catalog-identity.md) retains
non-passive LCSC/MPN/manufacturer/package/tier/stock/datasheet-URL facts and
resolves declared MPNs offline to candidate LCSC ids and datasheet URLs;
ambiguous matches need an explicit reasoned narrowing and misses stay unknown.
[Named ICs and modules](docs/approved-parts.md#named-ics-and-modules) can enter
`parts/1` through per-LCSC records derived offline from captured JLCPCB responses
and datasheet acquisitions. The subset check and scaffold producer replay exact
identity and freeze those sources; suitability and live stock need separate evidence.
Captured part bindings can pin
[selected and rejected datasheet sources](docs/binding-fill.md#captured-datasheet-source-selection)
while preserving the purchased part identity.
Opt-in [source-derived passive ratings](docs/binding-fill.md#source-derived-passive-ratings)
project R/C power and voltage ratings from the purchased catalog row or from verified
facts on the selected datasheet. The ratings carry hashed provenance, and absent
ratings stay missing.

An [approved-parts manifest](docs/approved-parts.md) freezes the declared parts
and their catalog, CAD and datasheet provenance in the campaign baseline.
The `parts_subset` check compares native symbol/footprint/supplier identities
and required instances; absent native evidence leaves coverage unknown.
The [Rung-1 captured-parts route](docs/rung1-parts.md) consumes that bundle
with an explicitly assembled J1 and runs the existing assembly and package checks.
The [rung-1 stage-ordered driver](docs/rung1-stages.md) runs one RC candidate
through requirements, parts, schematic, placement, routing, verification and
fab. It records stages that were not reached rather than passed, and writes a
record that `verify` recomputes against the same agent-ee commit and each
review's retained source inputs, independently of later design adoptions.
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

[JLCPCB stock capture](docs/jlc-stock-capture.md) is an opt-in, owner-run
producer. It sends one rate-limited request per LCSC id to the public JLCPCB
search endpoint and keeps each raw response, hashed and time-stamped.
`assembly_readiness` consumes the capture offline: it re-derives every part
from those bytes and binds `as_of` to the generation receipt in campaigns
(review time for standalone checks), with a 24-hour maximum age. The
[JLC catalog schema](docs/jlc-catalog.md) requires verifiable responses and
reports per-part hash verdicts. It refuses a capture that is
stale or lacks an observation for a populated id. Its response mapping is not
yet qualified against a live JLCPCB response.

[DigiKey capture](docs/digikey-capture.md) is the owner-run Stage 2 producer
for external parts without an LCSC code. It needs owner-provided DigiKey
API v4 client credentials and an explicit `--allow-network`. It retains each raw
response with its hash and UTC time. An offline validator re-derives every
field. An adapter projects a found part into an
`agent-ee.external-part-identity/1` declaration, a datasheet binding and
hand-fit purchase-source evidence. Its response mapping is not yet qualified
against a live DigiKey response.

The [datasheet source cascade](docs/datasheet-cascade.md) walks DigiKey, then
JLCPCB/LCSC catalog URLs, then an optional Exa search (`--allow-network`, in a
bounded child with retained raw JSON). It fetches candidates in order and skips
non-PDF responses and previously rejected hashes. It writes a replayable
`agent-ee.datasheet-source-selection/1` decision that records every selected,
rejected and failed source.

[Reference-block capture](docs/reference-block-interface.md) retains files from a
pinned local commit without Git transports or replacement refs, and checks
declared ports against the native sheet. Missing captured blobs are refused;
pinned external requirement citations with unavailable Git objects stay
unverified without copying the cited documents. It credits
sealed interface and parts membership evidence only for the block's exact
manifest, native components and hierarchy; malformed parts manifests are refused.
[Block adaptation](docs/block-adaptation.md) copies a flat block into
a pre-created child, freezes its provenance and maps its requirements to fresh board checks.
Its native provenance check lists value/connectivity edits and grades boundary
bindings. Optional layouts still need qualification;
a passing run on a separate schematic cannot qualify the block.
The [fome-mc PSU block](docs/fome-psu-block.md) is the rung-2 power reference: a
pinned capture with its contract, sourced requirements and a scope/2 review on an
original standalone board.
[Block inventory capture](docs/block-inventory.md) writes one draft entry per
planned block from a pinned design commit, all unverified, each with its
reference set, a derived interface contract, library hashes and sourced pending
requirements. It also replays block adaptation fixtures. The fome-mc inventory
and its capture at a pinned commit are committed under `fixtures/fome-mc/`
(owner decision of 2026-10-18, [licence notice](fixtures/fome-mc/LICENSE-NOTICE.md));
planning documents are cited by pinned identity, not copied.

The owner decision of 2026-10-18 permits the [pinned wideband reference](docs/fome-wbo1.md)
under `fixtures/fome-mc/wbo1/`. Its native wbo1-to-wbo2 adaptation fixture uses
fresh checks; upstream parity, functional behavior, heater geometry and independent
review remain pending, and both source blocks remain unverified.
The [FOME host review attempts](docs/fome-block-review-runs.md) retain failed
review identities for wbo1 and PSU. Invalid citations prevented adjudication;
committed metadata receipts give no passing credit.

The [pod CAN reference sets](docs/pod-can.md) capture the wideband TJA1051T/3
and PSU protection/termination parts together. An explicit captured composition
plan emits only four parts with eight native ports and provenance covering both
source instances. S remains grounded and VIO maps explicitly to +3V3. The source
capture stays unverified; source reviews and actual pod qualification remain open.

[Captured library checks](docs/footprint-libraries.md) compare declared symbol pins
with footprint pad inventories. [Symbol context](docs/symbol-context.md) exposes
captured library pin references and optional exact name/type expectations, as
used by the [owned pod symbols](fixtures/telemetry-pod/symbols/README.md).
The [symbol shortlist](docs/symbol-shortlist.md) picks candidate libraries,
then symbols, through one subscription route. It keeps only names in the
captured index, recording each dropped name with a reason. The list waits for
an owner approval bound to its hash.
The [native-bound mode](docs/native-binding.md)
requires the declaration to match every native board component, its exact
selection and its captured library pins; declared-only reports remain distinct.
[Pinned legacy footprints](docs/footprint-libraries.md#converting-pinned-legacy-footprints)
from another repository's object store are converted with `kicad-cli fp upgrade`
and recorded with commit, path, blob id and SHA-256 (the telemetry pod's AMPSEAL-23).
[Datasheet-derived footprints](docs/datasheet-footprint.md) are published only
when two isolated agent extractions of one captured PDF agree within 0.01 mm,
every pad value equals a verified page-cited fact, kicad-cli loads the result and
the footprint assignment check confirms symbol pin/pad parity.
Coordinate citations require quoted pitch/span declarations and supported
centered pad indices or half-spans; arbitrary rational multipliers leave
coverage unknown.
A [land-pattern geometry kernel](docs/land-geometry.md) compares supported pad
shapes using exact areas or certified bounds. Its supplier/native check adapter
and supplier qualification remain pending; the kernel alone cannot accept a part.

The [placement check](docs/placement.md#placement-check-kind) grades one
declared criterion per check: native DRC courtyard overlap and footprint
keepouts from an earlier DRC, outline containment, courtyard edge clearance,
declared connector edges and a pad-centre ratsnest proxy. The advisory
[pre-route congestion report](docs/placement-congestion.md) ranks pad overlaps,
courtyard issues and congested regions with suggested moves; it never passes a board. An
[initial placer](docs/placer.md) proposes deterministic positions on an isolated
copy and validates the saved result with native tools. Opt-in
[targeted plans](docs/placement-plans.md) place caller-named decoupling capacitors
or apply net-cluster seeds only when the placement score strictly improves; BGA
escape suggestions remain plans for inspection. Campaigns can select among
captured targeted requests with one shared judge policy and retain every
candidate’s judgment. Both targeted request forms reserve IMU axis markings
before a writer lease and again under the lease. On placement
turns of a staged campaign, the [placement campaign adapter](docs/placement-adapter.md) gives the writer the
sealed placement findings, can run the placer as a receipted coordinator step,
and refuses edits other than footprint translations, quarter turns and qualified
side flips, including rigid movement of footprint-owned zone polygons. The placer
keeps peer courtyards outside owned footprint keepouts. Envelope fixed footprints
and mounting holes written by the scaffold are fixed references, and their
exact courtyards are obstacles (AGENTEE-179). Circular probe courtyards are
bounded exactly within a declared 5 um tolerance. A fixed courtyard may leave the
outline only across envelope-declared edges and distances. Declared group
regions and anchor distances are enforced and graded by `placement-groups/1`, and
parts without courtyards are refused by name
([AGENTEE-212](docs/placer.md#real-pod-residuals-agentee-212)). Every placer
result past the fixed reservations records the board's capacity at the packing
gap, including a fragmentation-aware bound, as a diagnostic; an exceeded bound
is a proved infeasibility only when the policy opts in
([AGENTEE-249](docs/placer.md#board-capacity-agentee-249)). A declared captured
sub-layout also admits exactly the copper of whole reused groups. KiCad's
unchanged custom-property font default is tolerated on resave. Fresh checks grade
every proposal. These checks do not establish routing quality or a
fabrication-ready board.

Campaign writers must account for `pcbnew.SaveBoard` rewriting sibling project
files. The immutable-input guard diagnoses this likely cause and still refuses
the change. Follow the [board-saving writer rule](docs/campaign-guide.md#saving-boards-without-changing-project-siblings)
to preserve project siblings while adopting a saved board.

An installed kicad-happy check retains schematic/PCB/thermal/EMC
outputs, with [qualified narrow adapters](docs/kicad-happy.md) for low-pass RC,
ideal regulator feedback, complete typed power-pin inventory, USB protection
presence, capacitor voltage utilization, generic LDO temperature estimates and
ESD placement distance. Other analyzer rules remain informational for that kind.
The separate [severity gate](docs/happy-severity-gate.md) runs schematic, PCB,
cross-analysis and EMC; any error or warning without an immutable recorded
disposition fails it. Generic
producer/parser interfaces support additional measurements and coupled margins. Reviewer profiles explicitly select Claude, Codex or Agy through
their subscription CLIs; authentication or quota failures stop the affected call
without a provider fallback.

Before each reviewer, adjudicator or implementation call, a
[preflight guard](docs/result-reference.md#pre-inference-environment-receipts)
checks captured tool and policy identities and tests sandbox execution. Drift or
an unavailable prerequisite stops the call with a retained diagnostic.
Campaigns and standalone reviews allow one
[bounded output resubmission](docs/campaign-guide.md#bounded-output-resubmission)
per role per review by default. `run_review(..., max_schema_resubmissions=0)`
keeps a single model call. Both calls and the original validation error
are retained; checks, execution failures and environment drift remain terminal.
Adjudicators may cite the verified immutable `baseline.json`.

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
The [file digest contract](docs/result-reference.md) has mmap regressions that
keep metadata observations fixed while hashing real changed bytes; kernel
writeback timing cannot invalidate their stat-unchanged precondition.
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
The fome-mc block fixtures under `fixtures/fome-mc/` are the one exception
with a recorded owner decision (2026-10-18). The owner holds the rights to
fome-mc. Those files are still outside the MIT grant. See their
[licence notice](fixtures/fome-mc/LICENSE-NOTICE.md).

[Captured bare copper probe features](docs/probe-features.md) carry required pad, net and CAD identity through binding, board construction and fab export without a supplier or BOM/CPL row. Probe identity properties use a KiBot-safe
encoding; admission checks refuse legacy properties, and
[forward annotation migrates validated legacy properties before KiBot export](docs/probe-features.md#migrating-legacy-inline-json-properties).
Each component's XML marker field and property must agree exactly. Annotated
boards still require admission against the immutable parts declaration.

The [functional schematic presentation](docs/schematic-emitter.md#detached-text-and-native-measurement-agentee-301)
places detached fields clear of net labels and gives vertical test points label
leads beyond their pin-name text. A native SVG measurement helper reports
label/text overlaps. Opt-in `--native-text-work` emission uses measured bounds
and refuses unresolved overlaps before publication, without changing electrical
check verdicts.

Fab exports, campaign/qualified/stage-ordered rung-one steps, pod scaffold/constraint and envelope-content steps, schematic emission/annotation, plane preparation and binding fill mount [content-addressed source snapshots](docs/stage-receipts.md#python-tool-source-snapshots), isolating host import caches while preserving full mounted-file identity checks. Runtime validation refuses mounts of live package source directories, their ancestors or unqualified source files, including symlink and bind aliases (AGENTEE-327/329). Validated tool mounts pass opened descriptors and verify mounted identities inside the sandbox before execution to prevent rename races; directory sources containing submounts are refused. Refused fab receipts retain their violations and diagnostic paths.
