# Implementation decisions

The original decisions below describe the bounded standalone-review release.
The current scope/2 extension is documented in the [closed-loop overview](closed-loop.md),
with [implementation review dispositions](closed-loop-review.md) and
[decision provenance](closed-loop-doctrine.md). Those additions introduce explicit
single-writer campaigns and empirical re-review while preserving the original
candidate/reviewer/adjudicator information boundary.

## Small standalone Python CLI and skill

The owner requested a working reusable review capability and authorized ordinary
implementation decisions. Fable (`agent-413`, Claude Max) and Argon (`agent-414`,
Google AI Ultra through `agy`) inspected the local toolchain independently in
Herdr. Their [Fable](fable-assessment.md) and [Argon](argon-assessment.md) assessments
supported native check reuse, explicit candidate identity, isolated initial
reviews, and evidence-based dispositions. The coordinator selected the final
interfaces in [contracts.md](contracts.md); unresolved implementation details
were settled through executable probes and integration tests.

The new repository had no language/build conventions. Python 3.12 with standard
library orchestration uses the same system interpreter as installed `pcbnew`.
An executable source launcher avoids a mandatory virtual environment. Native
KiCad remains the design authority. The skill is a short operator entry point;
the CLI owns actual execution and records.

## Adapt native validation methods, keep project ownership separate

The concurrently developed FOME-MC `toolchain/eda.py` already handled native
reports, hierarchy coverage, silently skipped parity, and ngspice measurement
validation. Its snapshot code used live external-library links, and its CLI
wrote into the active FOME-MC checkout. Calling it in place would couple review
identity to another changing workspace. Copying the entire project toolchain
would add unrelated export/bootstrap and project configuration responsibilities.

`agent-ee` adapts the required validation methods and fixture with provenance;
it calls installed native tools directly. KiBot and kicad-happy can inform later
specialist work, but no check is credited merely because either is installed.
FOME-MC requirements stay in scopes/references rather than reusable orchestration.

## Copied candidates and filesystem isolation

Each scope explicitly selects files, including external libraries and relevant
requirements/models/configuration. Capture copies bytes, refuses undeclared
symlinks, and compares sources and copies before/after capture. The candidate
hash covers selected file hashes and exact scope. Run identity, packet identity,
implementation hashes and runtime binary hashes are separate recorded values.

Bubblewrap exposes the candidate read-only, a separate writable scratch for each
invocation, and no other reviewer workspace or host home. An actual write probe
was refused by the OS. Hash checks detect external changes at verification
boundaries; neither prompt wording nor hashes are described as write prevention.
This boundary does not protect against privileged host mutation or a compromised
kernel/provider. Shared libraries, OS, firmware and physical environment are
outside a fully reproducible machine-image guarantee.

## Initial judgments and later adjudication

The original implementation used Fable for both roles and a hard-coded Fable
adjudicator without comparative engineering evaluation. The owner rejected this
choice. Explicit versioned profiles now select every harness/model/effort/timeout,
including the adjudicator. Claude, Codex and Agy are supported subscription routes.
The [selection experiment](model-evaluation-plan.md) measures engineering findings,
control allegations and uncertainty separately from execution reliability.

The owner also rejected disabling tools and excessive sandbox construction.
Ordinary investigation tools are enabled in separate workspaces, with the existing
read-only candidate mounts. No hermetic environment is required. Fixed packets
remain an optional limited experiment mode, not the normal workflow. Initial
judgments cannot see each other's findings; adjudication can inspect their
retained experiments. Model diversity alone does not establish independence.

Every claim is retained before adjudication. A fresh adjudicator sees both
judgments and must explain acceptance, dismissal or deferral against evidence.
Structural citation validation checks identity and paths; it does not prove the
semantics of the argument. The runtime policy prohibits majority disposal.
Tests exercise singleton survival and uncertainty; observed model judgments are
retained so failures can be assessed rather than hidden.

## Bounded runs and conservative rerun

The default wall-clock work budget is 1,200 seconds; individual reviewer limits
come from the explicit profile (300 seconds in small evaluation profiles, 900 in
donor profiles). The run deadline further bounds every check and reviewer call.
Candidate inspection imaging has its own 180-second executor guard; its elapsed
time is excluded from this budget so visual availability does not consume check
or reviewer time. A campaign's `max_seconds` is not extended this way: it still
pays for coordinator imaging time. Other admission/setup costs remain charged. A run has a finite declared check/reviewer list, no
automatic provider fallback and no autonomous source repair. The later
[bounded output decision](#bounded-resubmission-after-an-output-format-refusal-2026-10-17)
permits one separately recorded schema resubmission per role in campaigns and
standalone reviews by default (AGENTEE-200). Completed
records are saved immediately. A crash, interruption, invalid output or exhausted
budget leaves incomplete coverage.

Resume creates a new run and reruns from a fresh capture. Selective evidence reuse
could save work, but its dependency/invalidation rules would add complexity before
this small set of checks needs it. Correction closure appends a link from an
accepted old finding to a distinct saved candidate and fresh passing review/checks,
with original criteria preserved. Repair demonstration is on disposable native
files; the real donor and active FOME-MC source remain protected.

## Distinguish supported observations from established defects

The full control exercise at `2a0e7f0` passed every native and simulation
criterion, but the adjudicator accepted several minor/info concerns as accurate
observations. Treating every accepted claim as a defect falsely blocked the
control even though no claim established a violated criterion.

The corrected contract adds `noted` for evidence-supported minor/info concerns.
`accepted` requires an established defect; major/blocking uncertainty remains
deferred. Unsupported allegations and defects cannot be converted to notes, and
duplicate links cannot hide defects behind notes. This is a general disposition
rule; the fixture, its electrical limits and its geometry requirements did not
change. The observed failure and subsequent fresh runs are retained separately.

## Profile evaluation and provisional allocation

[Observed comparison and baseline results](../evidence/MODEL-SELECTION.md) support
a provisional Codex/high starting configuration. Selection is explicit and
versioned, including role, harness, model, effort and deadline. No model family
is treated as independent proof. Agy timeouts leave a comparison gap; Claude and
Codex donor adjudications exposed protocol failures despite passing the simple
adjudicator trials. Preserve those failures and separate assisted recovery from
automatic completion.

Ordinary system tools and network access remain available. Codex initially had
a redundant inner workspace sandbox that broke DNS despite the outer workspace
allowing networking; the native Codex investigation call now uses full access
inside the existing read-only candidate boundary. A real HTTP and write-refusal
probe verified the behavior. No hermetic runtime or provider fallback was added.

Correction closure now requires changed captured input bytes in addition to a
new candidate identity, fresh passing evidence and unchanged criteria. Changing
only reviewer allocation cannot be represented as a saved-design repair.

## Generator direction (2026-10-17)

The owner set the direction below for turning agent-ee, kicad-route and FOME-MC
into one AI-driven PCB generator: the user describes a board, discusses parts,
says "go" and receives a JLCPCB-ready package in which every step is
tool-verified. The coordinator (`agent-423`) recorded the decisions; the
[generator roadmap](generator-roadmap.md) turns them into stages, a benchmark
ladder and Plane epics AGENTEE-78 to AGENTEE-84. The owner directive is the
authority for these decisions. An objection is filed as a Plane item for the
owner, not made as an edit to this section or as a change of direction in code.

Each decision below quotes the owner directive, then states what it preserves
and what it excludes.

### 1. Generation is a repair campaign from an empty candidate

> Generation is a repair campaign started from an empty candidate. The existing
> writer/reviewer/adjudicator/check loop is the engine. "Done" means the scope/2
> check set passes and the fab exports exist. No second architecture.

Preserves the single implementation writer, captured immutable candidates,
independent initial reviews, central adjudication and the convergence reducer
described in the [campaign guide](campaign-guide.md). Acceptance stays the
recorded `criteria_satisfied` state of a verified campaign.

Excludes a separate generation pipeline with its own acceptance logic, a
generator that writes into the source checkout, and any "done" that is not the
declared check set passing plus fab exports present as checked artifacts.

### 2. Boards are composed from verified reference blocks

> Boards are composed from a library of verified reference blocks (power, MCU
> core, CAN, wideband channel, drivers), each captured with its evidence, then
> adapted; not synthesized part by part. fome-mc's history (carrier + wideband
> Rev C + H7 core) is the model.

Preserves the evidence a block already earned: its captured bytes, check
results and review records stay bound to the block, and adapting it to a board
is an ordinary repair with fresh checks.

Excludes part-by-part synthesis of a whole board as the default route, and
treating a block as verified because it was copied from a verified source
without its evidence.

### 3. Pipeline stages, each a verified step

> Pipeline stages, each a verified step: requirements, parts (JLCPCB/LCSC stock
> and footprint data captured as evidence), schematic, placement, routing,
> verification, fab package. kicad-route is the routing stage and runs as a
> sandboxed writer. KRT is the default because it is deterministic; Freerouting
> runs are attempts, not measurements.

Preserves native KiCad files as the design authority and native KiCad grading
as the judge of routed copper. Each stage's output is a captured input of the
next stage.

Excludes live catalogue or stock queries during checks, routing inside
agent-ee's own code, and citing a Freerouting run as a reproducible
measurement. A Freerouting candidate is graded like any other attempt.

### 4. Placement is a new writer-proposes, checks-grade stage

> Placement is the one stage with no tool anywhere. Make it a new stage: a
> writer proposes placements, checks grade them (courtyard DRC, ratsnest
> length, keepouts, connector-on-edge), the campaign loop iterates.

Preserves the rule that a writer's summary is never evidence: positions count
only after capture, and grades come from coordinator checks.

Excludes a placement optimizer whose own score accepts a board, and placement
inside the routing tool.

### 5. The evidence model is non-negotiable

> The evidence model is non-negotiable: hashed inputs, recorded tool versions,
> sandboxed execution, independent reviewers, adjudication gated on tool
> evidence. Every new stage inherits it. Tool failure is missing coverage,
> never a pass.

Preserves every boundary in the [closed-loop overview](closed-loop.md),
including execution/outcome separation and the rule that only sealed check
findings become accepted scope/2 defects.

Excludes stage-specific shortcuts: a stage without hashed inputs, recorded
versions or sandboxed execution is not a verified stage, and an unavailable
tool cannot be recorded as a pass.

### 6. Tools stay upstream

> Tools stay upstream, never vendored. Record versions and hashes per run. GPL
> tools are called as processes, never linked.

Preserves the existing practice of calling installed native tools directly
and recording their identity. kicad-route's `vendor/` directory is a
gitignored install location under its state directory, not a vendored copy;
kicad-route records backend versions in every `result.json`. That complies.

Excludes copying upstream source into agent-ee, importing GPL code into
agent-ee's process, and runs whose tool versions cannot be reconstructed.

### Placement lives in agent-ee

The coordinator placed the placement stage in agent-ee rather than kicad-route,
for three reasons:

1. Decision 1 makes the campaign loop the engine, and placement is exactly
   "writer proposes, checks grade".
2. kicad-route's contract is to preserve footprints, outline, zones and rules
   unchanged and to grade only copper it added (its `footprints` and
   `board_outline` preservation checks). Moving footprints would contradict
   those invariants.
3. The grading checks (courtyard DRC through `kicad-cli`, ratsnest length and
   keepouts through the pcbnew probe, connector-on-edge geometry) are
   native-tool checks of the kind agent-ee already runs in its sandbox.

kicad-route contributes routability evidence: its blocked-by receipts and
`kicad-route blocked` output are inputs the placement stage consumes as check
evidence. It does not move footprints.

### Bounded resubmission after an output-format refusal (2026-10-17)

> bounded resubmission is allowable

The owner gave this answer to the coordinator's note that three of the first
four rung-1 campaigns completed the whole design loop and were then refused for
reasons unrelated to the board: a reviewer witness that failed validation
(AGENTEE-115), an adjudication that cited `baseline.json`, a path the citation
validator refuses (AGENTEE-111), and a runtime-drift refusal after an unused
harness binary updated itself mid-review (AGENTEE-112). Each refusal was
correct under the evidence model; together they discarded roughly three
quarters of the day's inference spend.

Preserves every refusal as recorded evidence. A refused reviewer, adjudicator
or writer output stays in the run directory with its validation error; the
resubmission is a new, separately recorded call with the same inputs, the same
pinned harness identity and the same prompt plus the exact validation error.
The bound is declared in the campaign configuration, defaults to one
resubmission per role per iteration, and is recorded in the run record with the
count used. A resubmission that still fails validation ends the run as before.
Checks are never resubmitted: a check's execution failure remains missing
coverage (decision 5 and the [campaign guide](campaign-guide.md)).

Excludes any resubmission that changes the inputs, the candidate, the pinned
tool identity or the finding set; silently dropping or repairing a malformed
witness or citation on the reviewer's behalf; and any retry that could make a
refused output look accepted. Runtime drift (AGENTEE-112) is a changed
environment, not a malformed output: a resubmission is permitted only if the
harness identity still matches the run's pinned hashes when re-checked;
otherwise the run stays refused and the drift is the finding.

AGENTEE-111, AGENTEE-112 and AGENTEE-115 implement this decision; their
acceptance criteria name the bound, the recorded count and the retained refused
output. The coordinator (`agent-423`) recorded this entry; the owner's answer
is the authority.

## Owner decisions of 2026-10-18

The owner answered eight questions the burndown reviews had parked. The
coordinator (`agent-423`) put each as a multiple choice with a recommendation;
the owner's choice is quoted. Plane items are updated by the integrator
(`agent-449`). Each entry states what it preserves and excludes.

### Konnect fixtures are replaced by owned equivalents

> Owned fixtures, mark Done

AGENTEE-28 (part selection) and AGENTEE-53 (placement judge) named a Konnect
fixture in an acceptance criterion. Konnect is AGPL-3.0. Both criteria are
reworded to the owned equivalents already in the tree, which the reviews proved
exercise the same behaviour (for 53, a local read-only probe of the real Konnect
board scored the expected 70 with both decoupling deductions). Preserves the
licence boundary: no Konnect bytes, hashes, identifiers or derived geometry
enter agent-ee. Excludes vendoring Konnect material under any label.

### fome-mc reference sheets are committed as fixtures

> Commit captured sheets as fixtures

AGENTEE-89, 105 and 118 capture the fome-mc PSU and wideband sheets as reference
blocks. The captured sheets go into agent-ee under `fixtures/`, each with the
fome-mc commit and sha256 it was captured from, through the offline Git capture
path (no network, no remote transport). Preserves decision 2: a block is a
captured artifact bound to its source commit and evidence. Excludes copying by
hand, capturing from a dirty or unpinned checkout, and committing anything from
fome-mc that is not a block the generator consumes. A licence statement for the
fome-mc material must accompany the first such fixture; the owner holds the
rights to both repositories.

### Scaffold is done as stage one

> Done as stage one, new item for producer

AGENTEE-87 closes with its declared limits (a hand-written scope and a captured
project template). One new item carries the automatic producer that builds the
scope and project rules from the envelope and parts list; AGENTEE-134 carries
migrating the two rung-1 drivers onto `scaffold.py`. Excludes a third scaffold
implementation.

### Convergence contract signed off with two guards

> Sign off with two small guards

AGENTEE-88's contract stands as implemented: progress is stage advancement, so a
writer must deliver a whole stage in one turn; `finding_retry_limit` counts the
first appearance. Two guards are added: a `finding_retry_limit` below 2 is
refused at config load (a limit of 1 would escalate before any repair), and
regression protection covers `info` checks as well as `pass`. Excludes counting
progress by remaining findings.

### Assembly readiness: three of nine

> Amend to 3 of 9, keep Done

AGENTEE-29's criterion read 4 of 9 components on the clean_boost-style fixture;
the code and the real fixture give 3 of 9 (C1, R2, U1) and the doc states which
six are not reached and why. The criterion is amended to 3 of 9. Excludes
reopening the item to reach a fourth binding on a non-target fixture.

### Bounded resubmission covers disposition-rule refusals

> Yes, keep as merged

The resubmission catch in `review_run.structured_call` includes the
adjudicator's six evidence-model disposition rules in `findings.adjudicate`
(for example, an established check defect cannot be `noted`). One recorded
resubmission with the rule appended is within the 2026-10-17 decision. Preserves
the gates: the evidence rules still decide the final output; the refused attempt
is retained; the finding set is invariant. Excludes narrowing the catch to
structural errors.

### Routing determinism is geometric equivalence

> Geometric equivalence

AGENTEE-96 and KICADROUTE-7 asked that a repeated KRT run reproduce the same
board hash. Two runs produce identical copper geometry with different bytes
(segment order, UUIDs). Determinism is defined as an identical normalized copper
geometry set; a check compares geometry, not file bytes, and records byte
differences without failing them. kicad-route records the same definition in its
routing decisions. Excludes a byte-identical criterion and a writer-side
normalization requirement.

### Done requires each stage through its declared step (amends decision 1)

> Yes, require stage receipts for done

Decision 1's "done" (scope/2 checks pass and fab exports exist) gains a third
condition: for every stage with a declared `agent-ee.stage-step/1` step, the
content the checks graded must be the content that step's receipt produced.
Writer-made content in such a stage fails done even when the checks pass. This
closes AGENTEE-114 by contract: the qualified rung-1 run showed the live writer
hand-routing with pcbnew where a routing step was declared. Preserves the single
writer and the check set as the acceptance authority; receipts become part of
the acceptance rule rather than provenance only. Excludes stages with no
declared step, where the writer remains the producer.

### Telemetry pod questions (second batch, 2026-10-18)

The owner answered eight further questions raised by the telemetry-pod surveys and
the burndown reviews. Choices are quoted; each states what it preserves and excludes.

**Criterion 4 analyzer gate.** > New severity-gate check, four offline analyzers
A new check kind runs kicad-happy's schematic, PCB, cross-analysis and EMC analyzers
offline, fails on any error-level finding, and requires a recorded disposition for
every warning finding id. The existing `kicad_happy` check keeps its declared-rules
policy. SPICE is excluded (it may reach the network and is not deterministic).

**CAN transceiver.** > TJA1051T/3, compose from both sheets
The pod's CAN block takes the TJA1051T/3 from fome-mc's wideband sheets and the
PESD1CAN-UX and 120 ohm termination from the PSU sheet, 5 V on VCC and 3.3 V on VIO.
The departure from "the verified block carries over unchanged" is recorded.

**Stock evidence source.** > JLCPCB public search endpoint, scraped per LCSC id
A host-side capture queries JLCPCB's component search once per LCSC id, rate-limited,
saving each raw response with its sha256 and UTC timestamp into an
`agent-ee.jlc-catalog/1` snapshot that `assembly_readiness` consumes in the sandbox
with `as_of` bound to the run. Excludes using LCSC's own stock figure as JLCPCB stock
and any capture inside a check.

**JLCPCB DFM.** > You upload once; I bind the report as evidence
The owner uploads the sealed Gerber zip at JLCPCB and saves the DFM result; a
sandboxed check binds the report hash and the Gerber zip sha256 to the candidate.
Geometry pre-checks run too but do not satisfy criterion 6 on their own.

**PREVIEW_REQUIRED rotations.** > Operator obligation recorded at done
`done` is reachable with PREVIEW_REQUIRED designators; the record lists each as an
obligation the owner confirms in the JLCPCB placement preview before ordering.
Excludes upgrading the status to pass.

**fome-mc planning documents.** > Pinned-identity citations only
Block captures cite requirement sources by fome-mc commit, path, blob id and sha256;
DESIGN.md and BLOCK_LIBRARY.md are not committed as fixtures.

**Derived-upstream sheets.** > Admit with upstream licence recorded
fome-mc sheets derived from Polygonus / rusEFI Proteus may be captured as blocks; the
block record carries the upstream origin and licence and the fixture includes the
notice; the origin check reports them as derived, not owner-confirmed.

**Default net class clearance.** > Default clearance = fab minimum
The scaffold producer sets the Default class clearance to the fab profile's minimum,
since KiCad DRC enforces class clearance as a hard floor; preferred values remain
router targets.

### Telemetry pod questions (third batch, 2026-10-18)

**Harness connector.** > the pod is a green-field build. one presumes it would match the style of connector used on the FOME-MC. 776228-1, right-angle PCB header, black seems like the right choice.
The pod's AMPSEAL 23-position header is TE 776228-1 (right-angle, black), the ECU J1 family. AGENTEE-169 captures its datasheet and verifies pad geometry and pin numbering against it.

**Parts JLCPCB does not stock.** > Hand-fitted after delivery, recorded deviation
Such parts (the header, NEO-M9N and SMA if unstocked) are excluded from JLCPCB assembly with a `not_assembled` marker and a recorded deviation naming the purchase source; every other BOM line must be in stock on the day of generation. Excludes silent substitution and failing criterion 5 on hand-fitted parts.

**ERC/DRC waivers.** > Owner-recorded dispositions bound to the exact violation
Each waiver names the rule, the items and a reason and is bound to the violation's location hash; a moved or new violation needs a new disposition; the owner approves the list before done. Excludes agent-issued waivers and blanket severity downgrades beyond fome-mc's project file. [The implementation](violation-waivers.md) (AGENTEE-189) captures the owner record as an immutable input, verifies list approval against its digest and matches ERC/DRC items exactly; every unmatched error or warning still fails, and each actual waiver list still needs its own owner approval.

**Pod power and travel ranges.** > Harness splice on the relay circuit, own 2 A fuse; 130 and 70 mm
The pod taps the switched 12 V relay output in the harness (the circuit that also feeds the ECU's J1-27 input) through its own 2 A fuse and grounds to chassis with the ECU; it never draws from the ECU. Fork travel 130 mm and shock travel 70 mm stand; fome-mc's telemetry document is corrected (FOMEMC-31).

**Rung 3.** > Pod replaces the wideband controller as rung 3
AGENTEE-98 is reworded to the telemetry pod and parents the pod epic AGENTEE-157; the standalone wideband controller becomes a later board if still wanted. The generator roadmap's ladder reads: RC filter, one fome-mc sheet, telemetry pod, fome-mc.

## Generation benchmark external series anchor (AGENTEE-285)

The AGENTEE-278 review reproduced coordinated check resealing, caller generation
claim rewrites and retained task metadata re-stratification. Local hashes cannot
authenticate the writer of those hashes. The benchmark therefore freezes a
series manifest binding report, task pins, evidence seal and executor-record
digests; its digest must remain outside the trial-store writer's control.
Retained executor records and logs also enter the captured candidate so sealed
check evidence identifies the generation execution it evaluated.

External task pins are mandatory at `freeze`; an authenticated aggregate needs
both the manifest and its independently retained digest. Existing unanchored
aggregation keeps diagnostic verdicts. This bounds the behavior change to the
new authenticated workflow and contradictory executor claims. Unknown remains
unknown. Native reruns were rejected here because they repeat evaluation cost,
depend on the original runtime, and do not independently authenticate generation
execution or task metadata. The external freeze assumes trusted records at the
time of freezing; it does not create an owner evidence store or repair already
forged history. See [the freeze contract](generation-benchmark.md#authenticated-series-freeze).

## Historical datasheet acquisition is separate from live capture (AGENTEE-306)

Retained PDFs can have a hash-bound URL and capture time without any recorded
redirect URL or fetch time. Allowing nulls on successful live records would erase
that distinction. `datasheet-capture/2` therefore represents historical imports
only, while `/1` keeps its existing live requirements. The shared acquisition
loader verifies the pinned original source note and PDF for both fact checking
and part-record consumers. A historical capture time is preserved separately
from fetch time; missing fetch and redirect observations remain unknown.

## Reduced operating-temperature acceptance (2026-10-20)

The owner ruled: "Obviously, that needs to be corrected. It should be tested
against its actual limit." AGENTEE-310 retires AGENTEE-281's exemption semantics.
A reduced part retains the ordinary DigiKey identity/package binding and is
judged against its owner-cited accepted range. Captured thermal validation
must also fit its actual rating; without it the result is unknown. The pod's
U1/U2/U4 acceptance at 85 degC cannot suppress a rating or thermal defect. See
[assembly readiness](assembly-readiness.md#digikey-operating-temperature) for
the immutable owner and thermal file formats.

### Delegated engineering acceptance (2026-10-22)

A repository can delegate owner-reserved selection decisions to its agents and
have them recorded, as fome-mc's `AGENTS.md` does. Under agent-509 request A,
such a decision is accepted only as a distinct `delegated_acceptance` record,
never as an owner line. The record states `authority: delegated_engineering`,
and the report names the author (`agent`) separately from that authority. The
record binds the recorded decision and the repository delegation by hash and
line locator. It must match the decision's subject and stated range, and it
fails closed on owner, withdrawn or non-autonomous decisions and on
unrecognized delegations. Like owner acceptance, it records a selection
exception only: the actual rating and thermal evidence are still judged.
Direct-owner declarations are unchanged.

Review of that first form (HOLD H1-H3) showed that captured files cannot carry
authority. An agent could write its own grant and decisions and capture them,
cite a different part under a genuine decision, or cite a prohibition that
mentions "autonomously". Authority is now anchored in agent-ee itself.
`OWNER_GRANTS` pins the owner's grant by repository, owner commit, path and
the SHA-256 of the exact committed item. `DELEGATED_DECISIONS` registers each
decision by recording commit and member hash, with its one reference, range
and exact part (MPN and manufacturer). A captured grant or decision is accepted
only when its bytes equal the pinned ones, and the evaluated part must be the
registered part. Registering an entry is a reviewed agent-ee change that
traces it to the owner's commit, and the tests repeat that trace against the
fome-mc object store. See
[the authority anchor](assembly-readiness.md#authority-anchor).

Review of the anchored form (ee310e HOLD H1) found that the exact grant item
still authorized when an agent placed it in a newly composed AGENTS.md: fenced
as a revoked historical example, under an unmet owner-approval condition, or
under a heading that scoped it to another part. The pinned item alone did not
carry its context. Each pinned grant and registered decision now also lists
its approved owner `snapshots` by full-file SHA-256. Both whole captured files
must be one of these snapshots, so a grant authorizes only in the owner context
that was reviewed. Byte-exact copies keep their provenance, wherever they were
captured from.


## Copper locality and captured policy binding (AGENTEE-328)

Electrical locality measures copper-land centroids before drill subtraction.
A pad anchor can be remote from its copper, so the native probe supplies an
exact rational centroid displacement from the anchor. Symmetric stock shapes
use native shape centres; qualified straight asymmetric copper uses area
moments. Unsupported centroid geometry remains unknown. Packing transforms
these displacements and the saved-board check measures the native board again.

A repeated pin number can name several physical lands. Optional captured pad
UUIDs select one land while retaining reference, number and net validation;
unselected ambiguity still refuses. Separate obligations express all-land
requirements without an implicit minimum-distance aggregation.

The coordinator adapter derives packing obligations from the locality check's
immutable captured file and records its raw hash. It refuses divergent inline
obligations or bindings. The coordinator check records the same file identity
independently. Standalone packing remains diagnostic proposal generation.

## Locality cluster search preserves legacy plans (AGENTEE-335)

The coordinator's legacy-first ruling preserves every plan found by per-part
search, including its placements and counted steps. Joint cluster search runs
only after a legacy failure, with remaining candidate steps and the original
absolute opt-in search deadline. Only an independently regraded completion can
replace that failure. Otherwise the legacy typed failure remains authoritative
and the joint attempt is diagnostic.

The joint finite candidate set is not a superset of the per-part set, so it
cannot justify discarding a legacy completion or claiming physical infeasibility.
Cluster guards count steps; there is no default wall-clock cluster guard.
Real-pod fail-fast is no longer this item's criterion; block-level placement is
the pod path. See [joint locality clusters](placer.md#joint-locality-clusters-agentee-335)
for the budget and diagnostic contract.

## Design-rule authority (AGENTEE-331, 2026-10-20)

The owner rejected arbitrary scaffold specifications becoming hard limits.
Numeric limits require class and citation: owner, manufacturer, datasheet and
engineering limits gate; default or unresolved limits remain reported advisories.
The coordinator's ruling preserves owner custom rules as owner authority and
AGENTEE-318/325 structural invariants as engineering authority. Native crashes,
missing reports and ignored evaluations remain failures. Captured constraint
sources and owner rule bytes remain immutable. See [constraint sources](scaffold.md#declared-design-rule-sources-agentee-331).

The AGENTEE-331 continuation applies the coordinator's named oracle rulings:
the native thin-track default remains visible advisory while the independently
sourced width requirement remains a defect. The pinned 2026-10-19 JLCPCB via
profile declares no copper-to-edge limit, so the scaffold campaign's unsourced
edge findings are advisory; final unconnected items still prevent acceptance.
The provisioned J1 drawing is a read-only test input and is not retained here.

AGENTEE-331 continuation (coordinator rulings H1/H2/G1/G2/G3/G6): compare actual
native measurements against all applicable sourced minima, rather than relying
on the largest limit printed by KiCad. Co-located drills and copper beyond the
outline are structural engineering findings. Reject declarations that demote
owner rules. Plane production and the row stand-in share provenance grading.
G5 (AGENTEE-425) uses the explicit inventory alternative: scaffold and board-build
bind retained unsourced geometry settings and their potential router/zone-filler
consumers in `unsourced_geometry`. Native values and existing DRC verdicts stay
unchanged; unsourced numeric limits remain advisory. Only owner, manufacturer
and datasheet sources omit value-matched settings from that inventory.
G4 (verified reference authority) remains owner work; importing a class and
citation does not validate their authority.

AGENTEE-437 preserves name-based owner-rule DRC authority while disclosing
shadowed generated geometry by position and its original source. Source-class
exclusion requires a citation. Grading re-derives bound geometry inventories;
stale, edited or missing inventories refuse evaluation as unknown. This is
captured-input consistency, not the owner authority verification in G4 above.
Coordinator ruling 437a binds a serialized emission witness and permits only
materialized additions equal to a closed, cited KiCad 10.0.7 native default.
The witness hash excludes itself to avoid a circular digest. Changed/removed
settings and unknown/non-default additions remain stale; native saves preserve
the existing IMU pass, defect and unsupported expectations.

The coordinator's 2026-10-21 ruling preserves the group-copper campaign's
initial structural findings: its unplaced copper fails both `drc-placement` and
`outline`. Reusing the module must still pass fresh DRC and every later campaign
assertion, including `criteria_satisfied`; containment witnesses stay visible.

A further 2026-10-21 coordinator ruling keeps the native DRC waiver contract's
initial defect. Its fixture declares its 0.9 mm track-width minimum through the
constraint-source import, as an owner-class synthetic test-fixture source bound
in the project receipt. Those violations stay hard; waiver assertions are unchanged.

## Unsourced packing gaps (2026-10-21, AGENTEE-338)

The coordinator's AGENTEE-338 ruling applies the owner's requirement that limits
be validated against a specification. The pod's 0.25 mm packing gap came from
agent-491's policy, without a captured assembly requirement. It remains an
advisory search reservation. A capacity proof that depends on that unsourced gap
reports unknown and names the gap for confirmation. A zero-gap structural proof
keeps its defect verdict. Original fixtures retain explicit fixture-author gap
requirements; qualified captured manufacturer requirements remain hard. The
[placer reference](placer.md#declared-obstacle-unions-and-reserved-gap-provenance-agentee-338)
describes the source entry and its caller-qualification boundary.

## Locality numeric authority (AGENTEE-337, owner 2026-10-21)

Numeric locality maxima require a component numeric/scale derivation or an
explicit owner-recorded decision reference. Agent-selected D-PL-1-style limits
and prose-only datasheet citations are advisory, including legacy declarations.
They may rank placements but cannot reject a proposal by distance. Manufacturing
spacing is a minimum and cannot establish an electrical maximum. Captured JLCPCB
PCB-clearance facts and native geometry can prove a manufacturer bound
infeasible below a necessary copper floor; assembly pad envelopes are advisory; the exact whole-footprint minimum remains an implementation gap.
See [locality provenance](placer.md#locality-provenance-agentee-337).

## Unsourced courtyard margins (2026-10-21, AGENTEE-358)

Apply the AGENTEE-338 advisory rule to `min_courtyard_edge_mm`: a capacity proof
depending on an unsourced margin reports unknown and names it. Retry without
unsourced margin/gap limits to preserve independent structural defects. Authored
fixture requirements receive explicit fixture-author provenance. Search keeps
its reservations, and native saved-board checks retain their authority.
The [policy audit](placer.md#unsourced-policy-limits-agentee-358) records the
remaining numeric limits and their effects; gap-source authentication remains
AGENTEE-348.

## Placer capacity proof by default (AGENTEE-249, 2026-10-22)

The coordinator's burndown assignment (agent-449, AGENTEE-249 follow-up) made
this agent-decidable; it is not an owner decision. Agent reasoning: the
capacity bound is sound, so a feasible input never exceeds it, and a proof
reaches `defect` only when its binding limits are sourced. A proof that binds an
unsourced gap or margin stays unknown and names the limit. Enabling it turns
some search unknowns into proved `packing_infeasible` results without relaxing
any placement constraint.

The default is legacy-first. The unchanged search passes run first, and the
proof replaces only a failed search's result, keeping its search record and
steps. `capacity_proof: true` keeps the pre-search proof, and `capacity_proof:
false` opts out. See [board capacity](placer.md#board-capacity-agentee-249).
