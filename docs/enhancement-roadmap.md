# agent-ee enhancement roadmap

**Status: implementation in progress.** The detailed designs below preserve the
agreed planning contracts. They are not evidence that every planned capability
has shipped. See the [README](../README.md#what-is-implemented-today) and
[coverage guide](coverage.md) for the current implementation and its limits.

Turn three independent ADHD explorations into a jointly ordered backlog of complete engineering improvements for agent-ee, preserving the existing single-writer, immutable-candidate adversarial review topology.

## Priority order

| Rank | Enhancement | Proposal | Work item |
| --- | --- | --- | --- |
| 1 | Normalize RC step defaults through measured-result generation | 1 | AGENTEE-2 |
| 2 | Make copper-width coverage explicit and include native arcs | 3 | AGENTEE-3 |
| 3 | Audit executable acceptance coverage before launching reviews | 4 | AGENTEE-4 |
| 4 | Reject empirical correction outside its declared design applicability | 5 | AGENTEE-5 |
| 5 | Explain incomplete campaigns without changing their state | 6 | AGENTEE-6 |
| 6 | Refuse environment drift before spending review inference | 13 | AGENTEE-7 |
| 7 | Enforce declared hardware interfaces across candidate repairs | 7 | AGENTEE-8 |
| 8 | Turn unresolved concerns into reproducible evidence requests | 8 | AGENTEE-9 |
| 9 | Ship a qualified passive tolerance-corner adapter | 9 | AGENTEE-10 |
| 10 | Recover interrupted bench-feedback transactions safely | 10 | AGENTEE-11 |
| 11 | Index reviewer packets by entities and requirement coverage | 11 | AGENTEE-12 |
| 12 | Qualify PCB DC-resistance back-annotation for declared paths | 12 | AGENTEE-13 |

Rank expresses delivery preference; hard dependencies are listed per design. Native parent relationships group the items under AGENTEE-1. Dependency references in issue bodies are not native blocking edges; the installed Plane interfaces do not expose relation creation.

The roadmap parent is **AGENTEE-1**. Plane tracks implementation, validation and
owner review separately; the original backlog snapshot below is historical.
Implemented parts include RC defaults, explicit copper-width coverage,
acceptance-coverage auditing, calibration applicability, campaign diagnostics,
pre-inference guards, hardware-interface contracts, bounded RC tolerance
corners, evidence replay, interrupted bench recovery, packet indexing and PCB
DC-resistance extraction (AGENTEE-9, 11, 12, 13 audited and integrated on
2026-10-18; the packet-index live A/B comparison stays owner-gated). A source
change and passing tests do not replace the owner's acceptance review.

The next direction, generating boards from an empty candidate through the same
campaign loop, is planned in the [generator roadmap](generator-roadmap.md).

## Preserved engineering contract

Requirements, targets and reference architectures remain immutable captured ground truth. Each candidate receives independent initial reviews before centralized adjudication. Only sealed coordinator checks establish accepted scope/2 defects. Implementation is the sole design writer; bounded convergence and empirical supersession remain enforced. Synthetic and scripted harness tests are not live-model reliability or physical validation. The planning contracts below do not confer hardware release authority.

## Independent exploration and discussion

Parent, Fable and Argon each generated 30 ideas using five isolated parallel frames and then deepened three selected ideas in separate calls. Each pool includes a wild frame. Composites use 0.35 novelty + 0.40 viability + 0.25 fit. Scores helped explore the space; source evidence, authority boundaries and implementation dependencies determine delivery order.

- [Parent exploration](planning/2026-10-17/parent-exploration.md) and [scored pool](planning/2026-10-17/parent-ideas.json).
- [Fable exploration](planning/2026-10-17/fable-exploration.md) and [scored pool](planning/2026-10-17/fable-ideas.json).
- [Argon exploration](planning/2026-10-17/argon-exploration.md) and [scored pool](planning/2026-10-17/argon-ideas.json).
- [Protocol and score audit](planning/2026-10-17/audit.json).

The independent reports are historical proposals. Their unverified source claims and speculative designs do not override the final contracts below. Fable withdrew its checks-only repair lane because it skipped independent reviews before adjudication. Arbitrary reviewer script replay remains an experiment, not defect authority. Topology equality cannot qualify empirical residual transfer. Identical failure sets can still show improving negative margins, so unconditional signature rejection was rejected.

Proposal 2 was withdrawn: capture already embeds external reviewer profiles, and bench re-entry preserves them. A focused execution confirmed scope propagation, with ingestion, verification and model invocation stubbed. The mistaken documentation claim was corrected in commit `5516e13`; no runtime repair ticket is needed.

Preserve all 90 original ideas and focus sketches. Reject checks-only intermediate acceptance, arbitrary script exit-code promotion, automatic calibration widening, passive-transparent circuit equivalence, inferred physical Jacobians, and unconditional rejection of identical failure signatures. Defer general constraint solving, inverse parasitic synthesis, full electromagnetic extraction and broad recovery until their evidence and authority contracts are established. Existing behavior remains a valid no-change option where benefits have not been measured.

## Consensus and publication evidence

[Fable’s confirmation](planning/2026-10-17/fable-consensus.md) and
[Argon’s confirmation](planning/2026-10-17/argon-consensus.md) explicitly agree
to the same order and detailed contracts. Parent agent-417 adopts that order.
The [initial proposal](planning/2026-10-17/discussion-proposal.md),
[Fable discussion](planning/2026-10-17/fable-discussion.md) and
[Argon discussion](planning/2026-10-17/argon-discussion.md) preserve the disagreements
and corrections. Fable withdrew its optional diagnostics/calibration reorder;
calibration applicability remains first because it protects evidence soundness.
Environment preflight was added without a quota probe or provider fallback.

The [machine-readable contracts](planning/2026-10-17/contracts.json) are the exact
reviewed designs. [Plane readback records](planning/2026-10-17/plane-items.json)
map proposal IDs to published work items and record the fields checked. Plane
adds an outer HTML container; content comparison accounted for that wrapper
while preserving headings, list structure and text. The
[decision receipt](planning/2026-10-17/decision-receipt.json) and
[doctrine provenance](planning/2026-10-17/doctrine-provenance.json) distinguish
the planning recommendation from future implementation and physical validation.

Argon’s additional unbounded-RC bench-ingestion suggestion is retained in the
discussion as follow-on investigation rather than expanding the small default-value
repair. Its proposed profile-provenance rewrite was not adopted: historical
verification and live-source freshness have distinct purposes, and no missing
profile capability was established. Geometry coverage and the interrupted
empirical supersession window are covered by the agreed contracts.

## Detailed designs

### 1. Normalize RC step defaults through measured-result generation

Proposal 1. AFK; scope estimate: small; priority: high.

**Problem and evidence.** The documented 1 V default is applied during deck validation but the measured-condition path indexes the original step_v parameter. Native reproduction retained in the documentation audit raises KeyError after simulator execution.

**Design.** Deck generation and metric-condition construction consume one validated normalized parameter object. Persist effective step voltage without rewriting input scope or retroactively changing historical identities. Omitted step_v and explicit 1.0 produce equivalent metrics/condition text, while candidate identities may differ because scopes differ.

**Compatibility.** No new baseline semantics; runtime policy identity changes and historical results remain historical. Do not silently rewrite scopes.

**Failure behavior.** Propagate execution errors as incomplete, never missing-data pass.

**Acceptance evidence required:**

- Native ngspice control with bounded measurements and omitted step_v completes checks; measured value agrees with analytic V*(1-exp(-t/RC)) within declared numerical tolerance.
- Explicit and omitted defaults produce matching conditions; nondefault amplitude scales measured response.
- Zero, negative, nonfinite and nonnumeric amplitudes remain unsupported; a failed simulator cannot produce a passing metric.

**Hard dependencies:** none.

**Priority rationale.** Remove a demonstrated default-path failure before expanding verification scope.

### 2. Make copper-width coverage explicit and include native arcs

Proposal 3. AFK; scope estimate: medium; priority: high.

**Problem and evidence.** Native reproduction at a834201 added a 0.05 mm PCB_ARC to a board with five legal straight segments. The 0.20 mm geometry criterion passed because its documented coverage is straight segments only. Broader copper-width claims need explicit arc and unknown-primitive coverage.

**Design.** New versioned geometry criterion declares covered primitive classes and excludes nets explicitly. Width evidence lists primitive UUID/type/net/layer/value/limit plus eligible, checked, excluded and unsupported counts. Include native PCB_ARC with straight copper tracks. Via drill/annular ring and zone geometry are separate future criteria, not coerced into width.

**Compatibility.** Opt-in new criterion/version; old scope/2 check definitions retain explicit straight-segment semantics. Require a new baseline to broaden existing campaign acceptance coverage.

**Failure behavior.** Empty or unknown eligible geometry produces incomplete coverage. Deterministic proven below-width primitive yields defect.

**Acceptance evidence required:**

- Actual KiCad fixture containing a 0.05 mm arc and five legal straight segments fails declared 0.20 mm broader copper-width criterion; straight-only legacy result retains its narrower meaning.
- Arc-only valid and invalid boards, empty eligible set, excluded-net arcs, malformed primitive and unsupported copper class exercise coverage outcomes.
- Evidence tampering or wrong candidate binding fails historical verification; no accepted defect from unsupported geometry.

**Hard dependencies:** none.

**Priority rationale.** Close a demonstrated geometry coverage gap with a bounded native check.

### 3. Audit executable acceptance coverage before launching reviews

Proposal 4. AFK; scope estimate: medium; priority: high.

**Problem and evidence.** Baseline compilation currently checks requirement-to-check references, not whether those checks can produce a bound violation. Informational netlist export and geometry without limits can be mapped without establishing an executable acceptance gate.

**Design.** Versioned requirement intent distinguishes acceptance from informational obligations. A parameter-aware registry exposes can_evaluate_bound, required prerequisites and unsupported reasons. Compile a captured coverage report mapping each acceptance requirement to at least one defect-capable check. Informational requirements/checks remain traceable but cannot satisfy an acceptance obligation. Unknown capability blocks new-policy launch.

**Compatibility.** New scope/baseline contract; do not reinterpret historical scope/2 results as audited. Explicit conversion preserves all acceptance requirements; no arbitrary waiver field.

**Failure behavior.** Capability is necessary coverage evidence, not semantic proof of requirement completeness. No model inference sets capability.

**Acceptance evidence required:**

- Baseline mapping a functional acceptance requirement solely to netlist export or unbounded geometry is refused before model calls.
- Mixed informational and bounded acceptance requirements compile and map every check.
- Built-in mutation corpus demonstrates legal/wrong RC values, width violation and rule-check defects change expected criteria; unsupported/malformed mutants are not counted as detected physical defects.
- Missing limits, unresolved prerequisites, unknown registry kind and informational-only baseline exercise refusals.
- Run the full pipeline with scripted independent reviewer/adjudicator fixtures on deliberately broken reference boards and matched controls. Seal expected requirement violations separately from implementation outputs. Distinguish this harness test from live-model defect detection performance.

**Hard dependencies:** none.

**Priority rationale.** Prevent structurally valid but ineffective acceptance plans; retain honest informational evidence.

### 4. Reject empirical correction outside its declared design applicability

Proposal 5. AFK; scope estimate: large; priority: high.

**Problem and evidence.** Calibration currently checks baseline, retained observations, immutable model-input hashes and condition/unit. Mutable candidate design changes are not independently bounded by a qualified transfer contract. Similar topology or nominal outputs cannot establish unchanged physics.

**Design.** Versioned calibration bundle binds each correction to source observation identity, baseline/check/metric/unit/condition, immutable model dependencies and a design applicability predicate. Initial predicate is exact captured design-dependency hashes; include all design inputs by default. Evidence producer/parser/model versions and calibration provenance remain captured. Mismatch prevents calibrated acceptance, records the failed predicate and requests new bench evidence. Qualified transfer envelopes are a later explicit policy, never inferred from topology similarity.

**Compatibility.** Legacy bundles stay verifiable under historical policy but do not acquire qualification. New strict campaigns require upgraded applicability; report migration need rather than silently widening predicates.

**Failure behavior.** Use missing-evidence/incomplete classification and structured recalibration request. No automatic widening, inverse model fit or claim that metadata authenticates instruments.

**Acceptance evidence required:**

- Unchanged measured design replays its discrepancy interval against unchanged bounds.
- Change a component value, connectivity, PCB geometry or unclassified design dependency: calibration becomes unavailable rather than silently transferring or producing a fabricated defect.
- Changed model, metric/unit/condition, omitted observation, tampered raw sample and wrong baseline fail closed.
- Re-ingest new candidate observations produces a new epoch; old acceptance remains superseded and old evidence retained.

**Hard dependencies:** none.

**Priority rationale.** Constrain evidence transfer before adding richer correction or recovery features.

### 5. Explain incomplete campaigns without changing their state

Proposal 6. AFK; scope estimate: medium; priority: medium.

**Problem and evidence.** Interrupted campaigns can leave partial artifacts across multiple writes. Existing verification detects integrity problems but does not provide a structured account of known/unknown transition facts and safe operator options.

**Design.** A diagnostic command produces a versioned machine report and readable evidence timeline. Include campaign/run/baseline identities, verified facts, failed invariants, unavailable facts, ambiguous transition candidates and operator actions with preconditions. Share pure verifier semantics; do not hide verifier exceptions. Read under an existing coherent lease or explicitly report unverified snapshot. Do not create lock files solely to diagnose historical data. Do not use F_GETLK as evidence about flock ownership without demonstrated platform-specific interoperability. Default diagnostic is advisory/unverified unless coherent reads are actually established. A .tmp file alone may reflect an active writer, not necessarily an interrupted write.

**Compatibility.** Additive output contract, no mutation or automatic recovery. Existing unreadable histories produce partial diagnostic with reasons.

**Failure behavior.** Unknown is an allowed result; offer zero/multiple actions when no single safe action is established. Pure verifier extraction can drift: read-only does not mean zero trust-kernel risk.

**Acceptance evidence required:**

- Inject interruption at capture/check/review/adjudication/empirical boundaries and show only facts justified by retained artifacts.
- Differential results match ordinary integrity verification on tampered and intact runs.
- Byte/hash directory inventory before/after diagnosis is identical, including failure paths.
- Concurrent writer yields an explicit busy/unverified result; partial .tmp files do not imply a uniquely known phase or safe retry.

**Hard dependencies:** none.

**Priority rationale.** Make failures inspectable before expanding state-mutating recovery.

### 6. Refuse environment drift before spending review inference

Proposal 13. AFK; scope estimate: small; priority: medium.

**Problem and evidence.** Captured runtime/policy identity enables historical checks, but a toolchain change between deterministic checks and an agent call can make expensive inference unusable. Preflight should detect observable drift at launch boundaries; it cannot remove later races or establish provider quota.

**Design.** Before each reviewer, adjudicator and implementation call, compare declared tool/runtime/policy identities to captured execution expectations and emit a retained preflight receipt. Validate required binaries and sandbox availability without invoking a model. Optional qualified native canary uses declared numerical tolerances and supported version ranges; no bit-identical numerical promise. Refusal consumes no inference call and cannot adjudicate or repair a candidate.

**Compatibility.** Additive versioned receipt for new executions; preserve old evidence policy identities. No new acceptance criterion or automatic repair lane.

**Failure behavior.** No one-token model quota canary or automatic provider fallback. Known authentication/quota failure stops affected operation under existing routing rules.

**Acceptance evidence required:**

- Replace a captured binary or policy between check completion and reviewer launch: call hook records zero provider invocations and explicit incomplete drift result.
- Unchanged supported environment launches normal independent calls; no prior review content enters other reviewer contexts.
- Missing binary, broken sandbox and malformed receipt refuse with specific diagnostic; canary failure is execution coverage failure, not design defect.
- Check drift separately at implementation and adjudication boundaries; report that preflight does not eliminate races with later external upgrades or establish subscription quota.

**Hard dependencies:** none.

**Priority rationale.** Catch actionable execution drift before spending subscription inference.

### 7. Enforce declared hardware interfaces across candidate repairs

Proposal 7. AFK; scope estimate: large; priority: medium.

**Problem and evidence.** The implementation writer is restricted to allowed design files, but that alone does not express owner-required external pinouts, loads or testpoints. A legal edit can remove functionality outside existing measured criteria. This is a proposed coverage improvement, not a reproduced malicious repair.

**Design.** Owner-authored immutable interface specification enumerates required terminal identities, pin-to-net relations, required component/testpoint presence and allowed edits. A deterministic check maps those declarations onto each native candidate and emits bound violations. Reference identities derive from requirements, not prefix heuristics or a possibly defective first board. Distinguish named/anonymous nets and make renames explicit.

**Compatibility.** New check and captured contract require new baseline; no inferred obligations imposed retroactively. Public contract documents limits of graph equivalence.

**Failure behavior.** No changes to source candidate, scope or owner declarations by implementation agent. Unsupported mappings cannot become pass.

**Acceptance evidence required:**

- Deletion of required load/testpoint, swapped external pins and disconnection each fail their declared invariant.
- A legitimate resistor-value repair preserves interface checks and passes functional measurements.
- R/C/L are not treated as transparent shorts; frequency-dependent networks do not become falsely equivalent.
- Unknown symbol mapping and ambiguous terminal identities yield incomplete coverage; undeclared topology is not silently claimed protected.

**Hard dependencies:** proposal 4.

**Priority rationale.** Protect semantic interfaces explicitly rather than inferring them from a starting design.

### 8. Turn unresolved concerns into reproducible evidence requests

Proposal 8. AFK; scope estimate: large; priority: medium.

**Problem and evidence.** Scope/2 correctly restricts accepted defects to sealed coordinator check findings. Novel reviewer concerns can therefore remain unresolved without a structured path to request a missing measurement or propose a future check. Reviewer experiment artifacts already persist; this proposal adds workflow, not storage that was absent.

**Design.** Versioned request binds candidate/baseline/requirement/concern, operating condition, requested observable and existing declared check if available. Coordinator may schedule already authorized deterministic verification. Reviewer experiments run only in isolated networkless sandbox, with bounded time/resources and retained script/tool/input/output hashes. Receipt reports execution and assertions separately; arbitrary exit code is never acceptance authority. New test proposals export a graduation bundle for a new explicitly authorized baseline/campaign.

**Compatibility.** Additive concern/request/receipt schemas; current disposition vocabulary and acceptance rules remain authoritative. Human-readable actionable request even when no adapter supports it.

**Failure behavior.** Failed replay leaves concern unresolved. No automatic new thresholds, checks or requirement edits.

**Acceptance evidence required:**

- Concern tied to existing declared failed bound becomes accepted only through normal sealed coordinator-check witness validation.
- Script reading candidate then exiting 1 cannot manufacture a defect; script success cannot resolve concern automatically.
- Timeout, malformed output, missing interpreter, attempted network/write escape and changed candidate produce retained failed/unsupported receipts.
- New check proposal leaves current baseline byte-identical and every initial reviewer context isolated.

**Hard dependencies:** proposal 4.

**Priority rationale.** Make novel findings actionable while keeping deterministic replay separate from acceptance authority.

### 9. Ship a qualified passive tolerance-corner adapter

Proposal 9. AFK; scope estimate: large; priority: medium.

**Problem and evidence.** Built-in passive simulation measures nominal declared values. Generic adapters exist, but a bundled qualified tolerance workflow and independent oracle would demonstrate coverage beyond a nominally passing RC fixture. This benefit is proposed, not a claim of existing corner-analysis failure.

**Design.** Captured adapter specification declares supported passive topology, component tolerances, excitation/measurement conditions, finite corner grid, tool/model versions and qualification fixtures. Enumerate corners deterministically, retain each result and aggregate only the declared envelope. Initial analytically tractable RC response provides an independent oracle; expanded RLC support requires separate demonstrated bounds. Qualification must establish when endpoint extrema suffice; otherwise label sampled coverage rather than worst-case guarantee.

**Compatibility.** New optional captured adapter/qualification contract and baseline criteria. No qualification conferred on arbitrary third-party solvers by hashing alone.

**Failure behavior.** Bounded resource budget; partial ensemble cannot masquerade as complete. Physical validation remains separate.

**Acceptance evidence required:**

- Nominally passing RC fixture violates declared response at a tolerance corner and receives an evidence-backed defect.
- Zero-tolerance result agrees with nominal analysis; independent analytic solution bounds each simulated corner within documented numerical error.
- Unsupported topology/nonlinear parts, missing tolerance, excessive corner count and failed corner simulation yield incomplete coverage.
- Model/tool/parser version change invalidates qualification; outputs preserve condition and unit identity for coupled metrics and empirical checks.

**Hard dependencies:** proposal 4, proposal 5.

**Priority rationale.** Deliver one qualified extension through the full measurement contract before broad solver integration.

### 10. Recover interrupted bench-feedback transactions safely

Proposal 10. AFK; scope estimate: large; priority: medium.

**Problem and evidence.** Bench ingestion, feedback registration, acceptance supersession and fresh review span multiple durable files. Atomic single-file replacement does not make the whole operation atomic, and existing output directories prevent blind retries.

**Design.** Versioned journal records operation identity from source candidate/report hash, target paths and intended review configuration. Explicit prepared, supersession_committed, review_started and completed states have durable receipts and directory fsync ordering. Under one writer lease, recovery validates identities/artifacts and reconciles only known deterministic transition steps. Same operation retries idempotently; changed report under same identity conflicts. A new model review remains a new identified attempt, never replayed automatically after uncertain external execution.

**Compatibility.** Opt-in journal version for new operations. Legacy partial directories remain read-only diagnostic inputs unless explicit conversion proves their state; no guessed migration.

**Failure behavior.** Recovery scope ends at empirical ingestion and review launch. Preserve current convergence budget/epoch rules and all superseded evidence.

**Acceptance evidence required:**

- Crash injection before/after each durable write and rename produces a recoverable state or explicit ambiguity without stale accepted outcome.
- Two processes contend for same target: only one commits supersession/review start.
- Duplicate operation produces one feedback event; conflicting identities refuse without clobbering files.
- Uncertain provider execution is reported for operator resolution, not rerun; no instrument acquisition replay.
- Recovered history verifies with retained original candidate and empirical receipts.

**Hard dependencies:** proposal 5, proposal 6.

**Priority rationale.** Use explicit operation identity and durable reconciliation for one bounded empirical path.

### 11. Index reviewer packets by entities and requirement coverage

Proposal 11. AFK; scope estimate: medium; priority: low.

**Problem and evidence.** Reviewers receive retained candidate/evidence packets; large designs make navigation across entities, requirements and coupled criteria difficult. No measured attention or cost improvement is established yet.

**Design.** Deterministic packet index maps native component/pin/net/primitive IDs to requirements, checks, retained artifacts and declared coupling criteria. Full baseline and captured evidence remain programmatically reachable; index entries contain factual references, not prior reviewer opinions or inferred acceptance. Show missing/unmapped entities explicitly. Bounded display truncation includes continuation locators and coverage counts.

**Compatibility.** Additive versioned packet index; existing reviewers can ignore it. No narrowing of baseline/evidence permissions.

**Failure behavior.** Index failures cannot erase underlying evidence or grant pass; expose unavailable navigation.

**Acceptance evidence required:**

- Stable candidate produces stable index; entity rename and candidate change invalidate stale references.
- Cross-domain check remains visible from both implicated entities with condition identity intact.
- Fresh reviewer packets contain no peer judgments or campaign reviewer history.
- Measured comparison on recorded fixtures reports token/latency and missed-defect outcomes; no savings claim without measurements.
- Large/ambiguous netlists preserve full artifact access and mark partial index coverage.

**Hard dependencies:** none.

**Priority rationale.** Improve factual navigation and measure benefit without hiding constraints or sharing reviewer judgments.

### 12. Qualify PCB DC-resistance back-annotation for declared paths

Proposal 12. AFK; scope estimate: large; priority: low.

**Problem and evidence.** Native geometry and nominal passive simulation exist, but no bundled qualified PCB DC path resistance adapter connects layout dimensions to declared IR-drop/loss limits. Generic adapters alone do not establish extraction physics.

**Design.** Captured stackup, copper thickness/resistivity/reference temperature, operating temperature and terminal mapping drive deterministic resistance extraction for an explicitly supported trace/via network. Native straight and arc lengths/widths become resistor-network edges. Solve declared DC load cases and emit voltage-drop/loss metrics through existing measurement/coupling contracts. Unsupported zones or ambiguous connections refuse until a qualified model exists. Initial scope is DC conduction, not switching parasitics or thermal fields.

**Compatibility.** Optional new adapter and explicit baseline requirements. No retroactive claim of RLC extraction or EMC/DFM certification.

**Failure behavior.** Do not infer board materials or fabrication tolerances from defaults. Refuse outside qualified geometry and temperature domain.

**Acceptance evidence required:**

- Straight and arc paths agree with rho*L/A; series/parallel supported networks agree with independent analytic resistance solutions.
- Actual KiCad fixture with narrowed/lengthened trace produces expected voltage-drop failure under declared current.
- Missing stackup, temperature, unresolved terminal, unsupported zone and disconnected path yield incomplete coverage.
- Model inputs, extracted network and solver outputs are captured and tamper checked; nominal/worst dimensional cases are distinguished.
- Any added via or parallel-path support has its own oracle and qualification before inclusion.

**Hard dependencies:** proposal 3, proposal 4.

**Priority rationale.** Make one layout/electrical coupling concrete and qualified, with explicit unsupported geometries.
