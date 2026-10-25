# agent-ee manual

This manual describes the current agent-ee worktree (package version 0.1.0),
including scope/2 campaigns and empirical feedback. Package version alone does
not identify a compatible evidence policy; recorded Python/runtime hashes do. The implementation is a Python CLI and an
agent discovery skill. Native KiCad files are the design authority. Review
outputs apply to a captured candidate and declared criteria, not to an entire
product's hardware qualification.

| Task | Read |
| --- | --- |
| Understand the five closed-loop invariants and migration | [Closed-loop overview](closed-loop.md) |
| Declare stage gates and interpret absent design content | [Staged campaigns](staged-campaign.md) |
| Configure single-writer repair and interpret convergence stops | [Campaign guide](campaign-guide.md) |
| Prepare captured pod inputs and inspect a checks-only scaffold | [Pod campaign driver](pod-driver.md) |
| See how far the actual pod inputs compose, and what is missing | [Pod composition record](pod-composition.md) |
| Bind a simulator/parser or combine cross-domain margins | [Measurement adapters](measurement-adapters.md) |
| Qualify an unloaded RC tolerance envelope | [Passive corners](passive-corners.md) |
| Simulate captured active models; derive regulator/amplifier/filter testbenches from targets | [Active SPICE](active-spice.md), [SPICE templates](spice-templates.md) |
| Request missing concern evidence or export a proposed check | [Evidence requests](evidence-requests.md) |
| Ingest bench samples and calibrate subsequent verification | [Bench feedback](bench-feedback.md) |
| Map telemetry-pod criteria to sealed checks and outstanding obligations | [Pod evidence index](evidence-index.md) |
| Install, authenticate, upgrade or remove links | [Installation](installation.md) |
| Build the pinned image, run checks in CI or use the GitHub Action | [Container image and CI action](container.md) |
| Run a review, investigate findings and verify a repair | [Operator guide](operator-guide.md) |
| Inspect a saved board or captured candidate in 3D | [3D PCB viewer](pcb-viewer.md) |
| Render top, bottom and isometric board images | [Native 3D renders](pcb-renders.md) |
| Read the ticket-by-ticket record of implemented capabilities | [Capability log](capability-log.md) |
| Build a captured design through the seven stages to a fabrication package | [Build workflow](build-workflow.md), [system acceptance map](system-acceptance.md) |
| Look up a command, flag, exit code or work budget | [CLI reference](cli-reference.md) |
| Select inputs, libraries, reviewers and packet contents | [Scope reference](scope-reference.md) |
| Capture offline catalog data and select declared part intervals | [Catalog](catalog.md), [part selection](part-selection.md) |
| Resolve declared IC/module MPNs to candidate LCSC ids and datasheet URLs offline | [Catalog identity index](catalog-identity.md) |
| Capture declared datasheet URLs before offline verification | [Datasheet acquisition](datasheet-capture.md) |
| Capture JLCPCB stock per LCSC id (owner-run) for assembly readiness | [JLCPCB stock capture](jlc-stock-capture.md) |
| Capture DigiKey API v4 identity, datasheet URL and ratings for external parts (owner-run) | [DigiKey capture](digikey-capture.md) |
| Resolve a part's datasheet across DigiKey, JLCPCB/LCSC and optional Exa search into a source selection | [Datasheet source cascade](datasheet-cascade.md) |
| Capture reference blocks and verify their native evidence coverage | [Reference block interface](reference-block-interface.md) |
| Inspect the incomplete wbo1 and PSU host review attempts | [FOME block host review attempts](fome-block-review-runs.md) |
| Capture a design's block plan as draft block entries; check a block adaptation | [Block inventory](block-inventory.md) |
| Review the captured fome-mc PSU block as the rung-2 power reference | [fome-mc PSU block](fome-psu-block.md) |
| Verify captured routing constraints | [Routing rules](routing-rules.md) |
| Emit intent net constraints as KiCad netclasses and width rules | [Net constraints](net-constraints.md) |
| Compare native connectivity with captured intent and direct-net constraints | [Netlist parity](netlist-parity.md), [additional netlist checks](netlist-extra.md) |
| Check worst-case algebraic requirements (dividers, ratings) across part tolerances | [Interval assertions](interval-assertions.md) |
| Check requirement topology: pin nets, part counts between nets, series and forbidden parts, expected components | [Requirement topology rules](topology-rules.md) |
| Keep owner-declared net names stable through generation | [Net-name provenance](net-names.md) |
| Check that regenerated boards, schematics or netlists match without UUID churn, or diff two repair candidates | [Canonical design diff](design-diff.md) |
| Give the writer rule-card guidance that only qualified adapters grade | [Design-rule cards](design-rules.md) |
| Check a board against a captured fabricator profile | [Fab profiles](fab-profile.md) |
| Run DFM geometry rules (annular ring, aspect ratio, acid traps, bridge gap, spacing, skew, copper balance) | [DFM geometry](dfm-geometry.md) |
| Check grounded-CPW width, gap and reference, stitching-via pitch, branched RF net length and the declared return-corridor guard on the routed board | [RF geometry](rf-geometry.md) |
| Check the ESP32-S3-WROOM-1 outside-board antenna placement (pose, edge, feed-point corner, module keepout) as a bare-board fab gate | [ESP32 antenna placement](antenna-placement.md) |
| Report pre-route pad overlaps, congested regions and suggested moves for the placement writer | [Placement congestion](placement-congestion.md) |
| Capture raw routing geometry and report diagnostic local copper-access witnesses or unknowns per terminal | [Local copper access](placement-access.md) |
| Check assigned footprints against catalog package names | [Package consistency](package-consistency.md) |
| Gate the BOM for JLC assembly: LCSC binding, stock freshness, Extended parts | [Assembly readiness](assembly-readiness.md) |
| Check source-bound component choices and captured deviations | [Component decisions](part-decisions.md) |
| Check part-binding properties in symbols and footprints | [Part binding](part-binding.md) |
| Export a captured board into a JLCPCB fab package | [Fab export](fab-export.md) |
| Verify sealed fabrication outputs against captured design evidence | [Fab-package consistency](fab-consistency.md) |
| Compare Gerber/Excellon counts and outline with native geometry | [Fab round-trip conservation](fab-roundtrip.md) |
| Correct JLCPCB CPL midpoints and rotations against the supplier footprint | [CPL rotation](cpl-rotation.md) |
| Estimate JLCPCB fab and assembly cost with a Basic/Extended breakdown (information, not a quote) | [JLCPCB cost estimate](jlc-cost-estimate.md) |

| Gate generation "done" on a passing, hash-sealed fab package | [Fab package done gate](fab-package.md) |
| Understand each native/engineering check | [Check reference](check-reference.md) |
| See how ERC coordinates and BOM variant names are guarded across KiCad 10.0.x | [KiCad report-format quirk guards](kicad-quirks.md) |
| Protect owner-declared schematic terminals, loads and probes | [Hardware interfaces](hardware-interface.md) |
| Parse records or understand finding dispositions | [Result reference](result-reference.md) |
| Understand capture, isolation and orchestration | [Architecture](architecture.md) |
| Assess permissions, inference data exposure and trust | [Security and trust](security.md) |
| Diagnose a refusal, incomplete run or tool failure | [Troubleshooting](troubleshooting.md) |
| Change checks, adapters, schemas or tests | [Maintainer guide](maintainer-guide.md) |
| Audit manual coverage, peer review and documentation checks | [Documentation review](documentation-review.md) |
| Plan generation from an empty candidate: stages, benchmark ladder and epics | [Generator roadmap](generator-roadmap.md) |
| Record a board's outline, stackup, keepouts and fixed connectors before generation | [Board envelope](board-envelope.md) |
| Record coordinator-run writer-side steps and their exact applicability | [Stage receipts](stage-receipts.md) |
| Prepare and run synthetic seeded controls/variants | [Seeded benchmark](seeded-benchmark.md) |
| Measure reviewer false positives on proven-equivalent design variants | [False-positive calibration](fp-calibration.md) |
| Restore or reproduce the evaluation | [Evidence reproduction](reproducing-evidence.md) |
| Check the proposed license and each file's origin and publication status | [Third-party origins](third-party-origins.md) |

The [acceptance index](../evidence/README.md) links all ten demonstrations,
including actual native tools and agent reviews. The [donor baseline](../evidence/BASELINE.md)
reports what the available Universal 112 snapshot establishes for FOME-MC.
The [coverage overview](coverage.md) gives a shorter account of supported checks
and omitted engineering areas.

## Decisions and historical records

[Implementation decisions](decisions.md) explains the architecture and tradeoffs.
[Independent evaluation expectations](evaluation-plan.md) was committed before
the implementation. [Fixture provenance](../fixtures/rc/PROVENANCE.md) identifies
the adapted native fixture and its source.

The following are builder records, not the current operator reference:

- [Original implementation agreement](contracts.md).
- [Fable assessment](fable-assessment.md) and [Argon assessment](argon-assessment.md).
- [Argon module agreement](contracts-argon.md) and [integration review](integration-review-argon.md).
- [Via-query correction](via-probe-correction.md), including actual measured evidence.

The current command parser and validators are the final authority if a historical
agreement differs. The manual references their actual behavior and names limits
that remain unenforced or outside the demonstrated scope.

Reviewer routing and comparison: [Profiles](reviewer-profiles.md), [evaluation plan](model-evaluation-plan.md), [observed results and provisional allocation](../evidence/MODEL-SELECTION.md), and [donor follow-up](../evidence/MODEL-BASELINE.md).

## Reading routes and diagrams

For a first review, follow installation → operator guide → result reference.
For an automatic repair loop, add the scope/2 baseline and campaign guide.
For a new engineering analysis, read measurement adapters → coverage → maintainer
guide before asserting tool qualification. For physical discrepancies, start
with bench feedback and its raw-sample contract.

Diagrams accompany the contracts they explain:

- [Pipeline and sandbox information flow](architecture.md).
- [Campaign sequence and state transitions](campaign-guide.md).
- [Requirement-to-check baseline](scope-reference.md#version-2-baseline).
- [Producer/parser evidence flow](measurement-adapters.md#producer-parser-and-acceptance-boundary).
- [Witnesses and adjudication](result-reference.md#version-2-witnesses-and-evidence-seal).
- [Bench re-entry and calibration epochs](bench-feedback.md).

The Mermaid source is embedded in these Markdown pages for repository viewers
that support it. Captions and tables state the same contracts for text-only readers.
Historical assessment, evaluation and decision records retain their original
context; they are not substitutes for the current references above.

[PCB DC path resistance](dc-path.md) specifies the optional native endpoint-only
conduction model, explicit material contracts and independent owned oracles.

- [Captured footprint assignment](footprint-libraries.md) — complete captured member inventory and exact electrical pad/pin parity; [pinned legacy footprint conversion](footprint-libraries.md#converting-pinned-legacy-footprints) through `kicad-cli fp upgrade`.
- [Datasheet-derived footprints](datasheet-footprint.md) — two isolated agent extractions must agree within 0.01 mm and cite a verified datasheet fact for every pad value before a kicad-cli-loaded footprint and parity-checked symbol are published.
- [Supplier land-pattern cross-check](land-pattern.md) — captured EasyEDA land pattern versus KiCad footprint copper/hole IoU and pin agreement through a registered check.
- [JLCPCB CPL rotation](cpl-rotation.md) — pad-box midpoints, versioned rotation policy and pin-1 frame verification against captured supplier footprints.
- [JLCPCB cost estimate](jlc-cost-estimate.md) — informational fab and assembly cost from a captured, versioned pricing table; unknown part classes stay unknown.
- [Placement geometry and checks](placement.md) and [initial placement](placer.md) — saved-board polygon judge, the registered one-criterion `placement` check over a prior native DRC, and an isolated proposal writer; the placement writer is integrated with staged campaigns.
- [Placement blocks](blocks.md) — blocks from the sealed hierarchy or an allocation file, packed alone into rigid `agent-ee.block-candidate/2` records with provenance, admission and matching rules (two-level placement stage A), then arranged as rigid macros with cited block rules and residual packing (stage B).
- [Placement trace](placement-trace.md) — opt-in bounded per-stage timing and Stage-A replay trace of the placement pipeline (GPU plan G0), recorded as the shared [`pcb-trace-envelope/1`](trace-envelope.md); outputs unchanged, overhead an experiment gate.
- [Block library](block-library.md) — versioned `blocks/` catalogue of known-good layouts with provenance receipts, imported from KiCad board regions, scale-verified datasheet figures and project clusters (search-derived, advisory), rebuilt and re-graded on each use.
- [Assembly and test access](dfa-dft.md) — declared connector orientation, test-point probe access and coverage, and the derived `bringup_plan.json`.
- [Design intent](design-intent.md) and [decision capture](design-dialog.md) — source-preserving preparation with explicit unresolved inputs; neither supplies engineering acceptance.
- [Spec capture](spec-record.md) — markdown requirements spec (headings, pipe tables, bullet and numbered lists) to `agent-ee.spec-record/1` with declared-code IDs and byte-exact locators; malformed or unsupported markdown is refused with its byte offset.
- [Requirement record to scope](requirement-scope.md) — deterministic compilation of a spec-quoting requirement record into a scope/2 with every requirement and check mapped; the rung-1 instance.
- [Native-bound footprint assignment](native-binding.md) — exact native board inventory, selection and library-pin binding.
- [Land-pattern geometry](land-geometry.md) and [validation](land-geometry-validation.md) — bounded pad-shape comparison used by the supplier/native check.
- [Netlist parity](netlist-parity.md) — exact intent or golden netlist parity against full native XML.
- [Schematic readability lint](schematic-lint.md) — deterministic presentation findings over captured sheets and native nets; style only unless a profile raises a rule to defect.
- [Native schematic emission](schematic-emitter.md) and [forward annotation](forward-annotation.md) — captured-intent generation and native-netlist board updates; full campaign integration remains pending.
- [Sub-layout reuse](sublayout-reuse.md) — group-local copy of a captured module's placement and routing into new staged instances, with pad-vote nets, exact verification and a per-group DRC delta; a placement declaration's `sublayout` admits exactly that group-local copper in a staged campaign (AGENTEE-129).
- [kicad-route routing adapter](routing-adapter.md) — pinned read-only routing attempts, exit semantics and retained reports; campaign integration with AGENTEE-95 remains pending.
- [Captured plane production](add-planes.md) — declared routing-stage zones, native fill and DRC, exact preservation of other board bytes, before the owned router.
- [Routing policy contract](routing-policy-contract.md) — for KICADROUTE-91 routability experiments: the trace, via, clearance and layer policy agent-ee passes for the pod and fixtures, with each value's source and provenance class; what identifies a routing target and the identity gaps; and what agent-ee must change when it re-pins kicad-route onto `campaign/2` and `routability-experiment/1` (AGENTEE-388).
- [Manual physical intent compiler](physical-intent.md) — opt-in S1 compiler: joins proposed terminal functions to a sealed run's verified `pin_map` facts, native pad UUIDs and netlist, then emits existing topology, locality and allocation contracts with an atomic, digest-bound receipt; Kelvin/current-loop obligations stay unsupported; not wired into any runtime path, activation gated by KICADROUTE-91 (AGENTEE-388).
- [Semantic architect producer](physical-architect.md) — opt-in S2 producer: one configured subscription call proposes physical-intent assertions over captured evidence, the S1 compiler grades them, one bounded correction, auth/quota failure stops with no fallback, an optional captured reference exposes every unresolved assertion; not wired into any runtime path (AGENTEE-389).
- [Pod routing policy determination](pod-routing-policy-determination.md) — what kicad-route's pour `area_loss` measures, why no pod `pour_area_loss_pct` is source-backed before placement, the `pour_budget` measure/derive recipe and decision rule, the classification of open decisions (captured source, engineering choice, one owner fact), engineering paths for the via budget, RF length, return corridor and stitching pitch, and what the routing guard leaves unqualified for RF (AGENTEE-257).
- [Placement campaign adapter](placement-adapter.md) — placement turns of a staged campaign: writer context, coordinator-run placer step, placement-only structural write scope with qualified side flips, and retained verification.
- [Captured symbol context](symbol-context.md) — exact source-bound pin inventory and literal offline lookup tools.
- [Symbol shortlist](symbol-shortlist.md) — two-stage, library-restricted model pick of `Library:Symbol` names with a deterministic existence gate, lossless chunking and hash-bound owner approval (AGENTEE-37).
- [Large-symbol capture](symbol-capture.md) — bounded extraction of selected symbols from libraries above the shared reader limit, with source-bound provenance.
