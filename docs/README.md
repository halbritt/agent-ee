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
| Bind a simulator/parser or combine cross-domain margins | [Measurement adapters](measurement-adapters.md) |
| Qualify an unloaded RC tolerance envelope | [Passive corners](passive-corners.md) |
| Request missing concern evidence or export a proposed check | [Evidence requests](evidence-requests.md) |
| Ingest bench samples and calibrate subsequent verification | [Bench feedback](bench-feedback.md) |
| Install, authenticate, upgrade or remove links | [Installation](installation.md) |
| Build the pinned image, run checks in CI or use the GitHub Action | [Container image and CI action](container.md) |
| Run a review, investigate findings and verify a repair | [Operator guide](operator-guide.md) |
| Look up a command, flag, exit code or work budget | [CLI reference](cli-reference.md) |
| Select inputs, libraries, reviewers and packet contents | [Scope reference](scope-reference.md) |
| Capture offline catalog data and select declared part intervals | [Catalog](catalog.md), [part selection](part-selection.md) |
| Capture declared datasheet URLs before offline verification | [Datasheet acquisition](datasheet-capture.md) |
| Capture reference blocks and verify their native evidence coverage | [Reference block interface](reference-block-interface.md) |
| Verify captured routing constraints | [Routing rules](routing-rules.md) |
| Compare native connectivity with captured intent and direct-net constraints | [Netlist parity](netlist-parity.md), [additional netlist checks](netlist-extra.md) |
| Check a board against a captured fabricator profile | [Fab profiles](fab-profile.md) |
| Understand each native/engineering check | [Check reference](check-reference.md) |
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
| Restore or reproduce the evaluation | [Evidence reproduction](reproducing-evidence.md) |

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

- [Captured footprint assignment](footprint-libraries.md) — complete captured member inventory and exact electrical pad/pin parity.
- [Placement geometry and checks](placement.md) and [initial placement](placer.md) — saved-board polygon judge, the registered one-criterion `placement` check over a prior native DRC, and an isolated proposal writer; campaign writer integration remains pending.
- [Design intent](design-intent.md) and [decision capture](design-dialog.md) — source-preserving preparation with explicit unresolved inputs; neither supplies engineering acceptance.
- [Native-bound footprint assignment](native-binding.md) — exact native board inventory, selection and library-pin binding.
- [Land-pattern geometry](land-geometry.md) and [validation](land-geometry-validation.md) — bounded pad-shape comparison; supplier/native check integration remains pending.
- [Netlist parity](netlist-parity.md) — exact intent or golden netlist parity against full native XML.
- [Placement campaign adapter](placement-adapter.md) — coordinator-run placer step and placement-only structural write scope; campaign wiring remains pending.
- [Captured symbol context](symbol-context.md) — exact source-bound pin inventory and literal offline lookup tools.
