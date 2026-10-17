> Historical independent exploration, before consensus. Proposals and source claims
> here are hypotheses; the final roadmap and its dispositions supersede them.
> These are not implemented capabilities or engineering qualification evidence.

# Parent independent ADHD exploration

## Brief

Find enhancements that make agent-ee more useful for actual engineering decisions while preserving its immutable baseline, one repair writer, independent reviewers and evidence gate. This independent run was completed before reading Fable or Argon proposals.

Five isolated subscription-native Codex processes launched in parallel (regulator, logistics, 3am on-call, biology, remove the load-bearing assumption), each generating six ideas with criticism forbidden. Parent scoring began only after all five completed. Three additional isolated focus calls deepened the weighted top three. The biology frame supplies the wild perspective. No source changes or model-provider fallback were used.

## Wide set

Scores: novelty N, viability V, fit F, each 0–10. Weighted score = 0.35N + 0.40V + 0.25F. Scores are judgments for exploration, not measured engineering outcomes.

### Explicit engineering obligations

- **assumption-1** [N8 V7 F8; 7.60] Remove the assumption that a complete schematic is the starting point: review an incomplete design through explicit engineering obligations.
- **assumption-5** [N8 V7 F9; 7.85] Remove the assumption that reviewers receive one authoritative interpretation: require competing circuit interpretations where connectivity or intent is ambiguous.
- **oncall-3** [N6 V9 F9; 7.95] Introduce an explicit inconclusive outcome that identifies missing evidence and the exact engineering claims it prevents. **Trap flagged.**
- **regulator-2** [N6 V9 F9; 7.95] Make refusal a structured engineering result that identifies the missing evidence, affected decisions, and exact conditions for resuming review.

### Authority from qualified evidence

- **assumption-2** [N8 V6 F8; 7.20] Remove the assumption that the baseline is correct: independently audit whether the requirements can coexist. **Trap flagged.**
- **assumption-6** [N8 V8 F9; 8.25] Remove the assumption that a physical model must already exist: let users supply qualified behavioral contracts for unfamiliar devices and assemblies.
- **biology-3** [N8 V7 F8; 7.60] Use quorum sensing to distinguish independent reviewer agreement from several reviewers repeating the same unsupported assumption. **Trap flagged.**
- **biology-5** [N8 V8 F9; 8.25] Give bench calibration a biological metabolism that tracks how evidence ages as instruments, fixtures, firmware, and operating conditions change.
- **logistics-4** [N6 V8 F9; 7.55] Use qualified specialist analysis tools as spokes around a central evidence intake hub.
- **regulator-3** [N7 V8 F10; 8.15] Trace bench conclusions through specimen identity, board revision, firmware, instrument configuration, calibration status, and raw captures.
- **regulator-4** [N9 V9 F10; 9.25] Give every model and measurement parser a qualification envelope that automatically withdraws its authority outside validated conditions.
- **regulator-5** [N7 V8 F9; 7.90] Attach explicit evidence invalidation rules to component substitutions, layout changes, requirement edits, and manufacturing variants.
- **regulator-6** [N7 V8 F9; 7.90] Produce a reproducible acceptance dossier that exposes shared reviewer dependencies, dissent, adjudication overrides, and residual uncertainty.

### Repair interfaces and safe handoff

- **assumption-3** [N8 V6 F6; 6.70] Remove the assumption that a repair means changing the board: generate operating restrictions, assembly options and component substitutions as repair candidates. **Trap flagged.**
- **biology-2** [N7 V8 F9; 7.90] Treat repair proposals like transplanted tissue: check the interfaces for rejection before accepting local improvement.
- **logistics-6** [N8 V6 F8; 7.20] Treat the owner's physical board as the last-mile delivery destination for every accepted repair. **Trap flagged.**
- **regulator-1** [N6 V7 F9; 7.15] Require each repair to carry a claim-by-claim proof of preserved requirements, with unresolved claims blocking acceptance.

### Experiments that resolve uncertainty

- **assumption-4** [N9 V7 F9; 8.20] Remove the assumption that engineering review ends with a verdict: produce a minimal experiment that distinguishes competing explanations.
- **logistics-1** [N5 V8 F7; 6.70] Route unresolved findings through discipline-specific queues with explicit entry criteria and named resolution owners.
- **logistics-2** [N8 V6 F7; 6.95] Batch bench measurements by fixture setup, then bind each result back to its individual candidate and requirement. **Trap flagged.**
- **logistics-3** [N8 V9 F10; 8.90] Dispatch just-in-time evidence requests when an adjudication decision reaches a specific unresolved engineering question.

### Tests that learn from escapes

- **biology-1** [N7 V8 F9; 7.90] Give each review an immune memory of previously escaped defects that triggers targeted checks when a related circuit pattern reappears.
- **biology-4** [N7 V7 F8; 7.25] Build a gut flora of narrow engineering specialists that activate when the candidate contains their preferred circuit habitat.
- **biology-6** [N9 V7 F9; 8.20] Run repair campaigns through a developmental checkpoint sequence that tests whether the circuit can reach its intended operating state.
- **oncall-6** [N7 V9 F10; 8.55] Turn resolved incidents into a fault-injection corpus covering hung producers, truncated reports, disk exhaustion, stale artifacts, and interrupted bench ingestion.

### Recoverable work and incident evidence

- **logistics-5** [N7 V9 F9; 8.30] Give rejected repairs a returns process with a reason code, reproducible failure package, and explicit disposition.
- **oncall-1** [N9 V8 F10; 8.85] Seal each review stage as a resumable transaction with an explicit crash-recovery policy.
- **oncall-2** [N7 V9 F9; 8.30] Make every stalled run emit a self-contained incident capsule with inputs, tool versions, process state, bounded logs, and a replay command.
- **oncall-4** [N8 V7 F8; 7.60] Fence repair agents with expiring write authority tied to one candidate generation and revoke it before independent review begins.
- **oncall-5** [N6 V7 F8; 6.90] Provide a recovery console that explains the blocked stage and offers validated resume, evidence recollection, or candidate abandonment actions.

## Converge

1. ★ **regulator-4** — Give every model and measurement parser a qualification envelope that automatically withdraws its authority outside validated conditions. A pinned tool can execute reproducibly while producing conclusions outside its demonstrated physical or interpretive validity.
2. **logistics-3** — Dispatch just-in-time evidence requests when an adjudication decision reaches a specific unresolved engineering question. Each request specifies the measurement, operating conditions, required uncertainty, and decision it will unlock.
3. **oncall-1** — Seal each review stage as a resumable transaction with an explicit crash-recovery policy. A killed KiCad process or host reboot resumes from verified artifacts without repeating repairs or mixing candidate generations.

### Traps

- **assumption-2**: A general feasibility solver would imply physical completeness; bound any contradiction witness to declared arithmetic constraints.
- **assumption-3**: Operating restrictions cannot silently relax the frozen requirements; requires a separate owner-authorized baseline.
- **biology-3**: Corroboration scores cannot become voting authority or expose peer judgments to initial reviewers.
- **logistics-2**: Multi-board instrument scheduling precedes a proven single-board acquisition path; defer batching.
- **logistics-6**: Automatic rework instructions need board-specific physical safety validation beyond this software project.
- **oncall-3**: A new inconclusive enum alone duplicates existing incomplete outcomes; useful only with structured obligation semantics.

## Focus

### regulator-4

```json
{
  "idea_id": "regulator-4",
  "proposal": [
    "Add a qualification envelope to one measurement parser first: the envelope binds its file hash to a versioned input format, supported units, instrument modes, calibrated ranges, and validation fixtures.",
    "Each ingestion evaluates that envelope against captured raw data and acquisition metadata before parsed measurements can support acceptance; missing or out-of-range evidence produces unsupported with specific reasons.",
    "Use explicit transitions from unevaluated to qualified, unsupported, or evaluation-error, with every input, parser, or envelope change returning the assessment to unevaluated.",
    "Keep coordinator defect authority unchanged: a failed physical check can establish a defect only when its supporting measurements are qualified, while an envelope failure establishes an evidence gap.",
    "Under the existing writer lease, write immutable assessment artifacts first and publish a manifest last; reviewers consume only complete manifests binding raw samples, parser, envelope, and assessment hashes.",
    "Recovery reruns qualification from captured evidence or recaptures measurements through the existing standalone workflow, and an incomplete publication never restores superseded acceptance.",
    "Introduce this through an explicit scope/schema migration that requires qualification for the selected parser, treats legacy evidence as unqualified, and leaves broader model qualification for later vertical slices."
  ],
  "load_bearing_risk": "The envelope may certify conditions that do not actually establish measurement validity; reproducible metadata checks cannot compensate for inadequate validation fixtures or unmeasured acquisition conditions.",
  "first_concrete_step": "Select one existing measurement parser and draft a versioned envelope schema plus a pure qualification function contract: raw-data metadata, parser hash, and envelope in; status, reason codes, and evidence references out.",
  "tests": [
    "Exercise validated fixtures and exact range boundaries, including missing metadata, unsupported instrument modes, and ambiguous units.",
    "Change raw evidence, parser bytes, or envelope contents and verify that prior qualification cannot authorize the new assessment.",
    "Inject crashes before and after manifest publication and verify that readers reject incomplete artifacts and never recover superseded acceptance.",
    "Verify that unsupported and evaluation-error block acceptance without becoming physical defects, and that a qualified failed check preserves existing coordinator authority.",
    "Migrate a legacy campaign fixture and verify that fresh qualification is required without changing its normative baseline or protected margins."
  ],
  "sub_ideas": [
    {
      "name": "Model applicability envelopes",
      "description": "Extend the same mechanism to one simulation model, binding demonstrated temperature, frequency, load, and operating-mode limits to its exact artifact hash."
    },
    {
      "name": "Derived evidence qualification",
      "description": "Require a calculated margin to retain the qualification references of every contributing measurement; an unsupported dependency blocks that margin from supporting acceptance."
    },
    {
      "name": "Boundary-directed measurement",
      "description": "Turn an envelope rejection into a concrete recapture request naming the missing metadata or validated acquisition mode, without expanding the envelope automatically."
    },
    {
      "name": "Qualification regression corpus",
      "description": "Preserve adjudicated parser failures as fixtures so proposed envelope expansions must demonstrate both newly supported cases and continued rejection of known invalid inputs."
    }
  ]
}
```

### logistics-3

```json
{
  "idea_id": "logistics-3",
  "proposal": "Add a just-in-time evidence request for one unresolved engineering question: whether a specified rail stays above its minimum voltage during a defined load transient. When adjudication reaches that question, the coordinator creates a request bound to the candidate hash and pinned scope, stating probe location, load waveform, operating conditions, sampling requirements, uncertainty limit, and the decision criterion. An operator supplies raw samples and acquisition metadata; deterministic validation checks completeness, conditions, uncertainty, and binding before the existing empirical-ingestion path captures samples and residuals. Valid ingestion supersedes the old acceptance before fresh review, while missing or inadequate evidence leaves the question unresolved. Publish request transitions under the existing flock writer lease, using immutable evidence files and a final manifest write so readers can detect incomplete publication across multiple JSON writes. Initial reviewers retain their existing isolation, and this slice adds neither a fleet service nor baseline relaxation or model-based acceptance.",
  "load_bearing_risk": "A structurally valid measurement can misrepresent the physical setup; hashes establish artifact identity but cannot establish probe placement, calibration, or operating conditions.",
  "first_coder_step": "Draft a versioned evidence-request schema and one rail-transient fixture, including candidate and scope bindings, measurement requirements, uncertainty rules, and the exact decision predicate.",
  "state_transitions": [
    "unresolved \u2192 requested: coordinator publishes the bound request",
    "requested \u2192 submitted: operator publishes an immutable evidence bundle",
    "submitted \u2192 rejected: validation records actionable reasons; a new submission may follow",
    "submitted \u2192 validated: deterministic evidence checks succeed",
    "validated \u2192 ingested_pending_review: empirical ingestion records supersession of old acceptance",
    "ingested_pending_review \u2192 resolved: fresh adjudication records the evidence-backed result",
    "Any pending request \u2192 stale: candidate or pinned scope changes; replacement requires a new request"
  ],
  "failure_recovery": [
    "Ignore incomplete bundles without a committed manifest; preserve them for diagnosis.",
    "Use request ID and bundle hash as ingestion idempotency keys so retries cannot duplicate supersession.",
    "After a crash, reconcile committed manifests against ingestion receipts under the writer lease; contradictory records block resolution.",
    "A missing fresh review keeps acceptance superseded and the decision pending.",
    "Recovery is limited to this request transaction; standalone workflow resume still recaptures and reruns as required."
  ],
  "tests": [
    "Accept a complete matching fixture and reject wrong candidate bindings, missing samples, invalid conditions, and excessive uncertainty.",
    "Verify a measurement uncertainty interval crossing the voltage threshold remains unresolved.",
    "Inject crashes between each publication step and verify partial evidence never resolves a decision.",
    "Replay submission and ingestion to verify idempotency and durable acceptance supersession.",
    "Verify concurrent writers respect leases and initial reviewers cannot access peer or past judgments."
  ],
  "migration": "Introduce the request schema through an explicit scope/schema version migration. Preserve historical artifacts without synthesizing requests or changing their recorded outcomes. Require an explicit upgrade for new evidence-request runs; unknown versions remain unsupported and never count as passing.",
  "sub_ideas": [
    {
      "name": "Decision sensitivity",
      "description": "Calculate whether attainable measurement uncertainty could resolve the question before requesting an experiment."
    },
    {
      "name": "Shared acquisition",
      "description": "Let one capture satisfy several separately bound requests when their operating conditions and sampling requirements match."
    },
    {
      "name": "Instrument adapters",
      "description": "Translate supported oscilloscope exports into the evidence bundle while retaining original files and parser hashes."
    },
    {
      "name": "Evidence expiry",
      "description": "Invalidate evidence reuse when relevant component, setup, calibration, or operating-condition bindings change."
    }
  ]
}
```

### oncall-1

```json
{
  "idea": "Seal review stages as resumable transactions",
  "sketch": [
    "Start with a narrow vertical slice: resume one interrupted deterministic-check stage against an immutable candidate, while repairs and independent model reviews retain their existing restart behavior.",
    "Identify the transaction by campaign, candidate generation and hash, scope/schema version, normative baseline, tool argv/hash, and model/parser file hashes; reuse results only when every applicable identity matches.",
    "Under the existing flock writer lease, move through PREPARED \u2192 RUNNING \u2192 SEALED, with INVALIDATED for identity mismatches and FAILED for execution or validation failures; only a validated SEALED manifest makes artifacts eligible for downstream use.",
    "Write raw outputs and parsed results into a private staging directory, flush them durably, then publish a manifest containing their hashes by atomic rename and flush its parent directory, so recovery never infers completion from scattered JSON files.",
    "After a crash, reacquire the lease and validate any seal; reuse verified sealed artifacts, but quarantine unsealed attempts and rerun the check against the same candidate without repeating repairs, treating unsupported outcomes as non-passing.",
    "Keep acceptance authority unchanged: sealing certifies artifact completeness, coordinator findings still follow existing defect rules, and later independent reviewers receive neither peer nor past judgments.",
    "Test termination at each publication boundary, truncated JSON, corrupted artifacts, competing writers, changed identities, and repeated recovery; add reboot-level durability testing, and introduce an explicitly versioned resume manifest that refuses legacy or unknown formats unless an authorized migration supplies verifiable provenance."
  ],
  "load_bearing_risk": "An incomplete identity or unsound durability protocol could make stale or partial evidence appear reusable; the seal must bind every relevant input and survive the filesystem's actual crash behavior.",
  "first_concrete_step": "Define the deterministic-check transaction manifest and implement a recovery classifier that returns reuse, rerun, or refuse for fixture directories before connecting it to the check runner.",
  "sub_ideas": [
    {
      "idea": "Explainable recovery",
      "detail": "Produce a local recovery report naming the verified seal or exact mismatch that requires rerunning, preserving interrupted attempts as incident evidence."
    },
    {
      "idea": "Generation-bound convergence history",
      "detail": "Later extend sealing to coordinator convergence state so iteration limits, repeated-design detection, and protected margins survive a restart without exposing judgments to initial reviewers."
    },
    {
      "idea": "Empirical acceptance invalidation",
      "detail": "Extend the transaction protocol to atomically publish fresh sample provenance and supersede old acceptance before any new review can consume the evidence."
    },
    {
      "idea": "Deterministic crash replay",
      "detail": "Turn publication-boundary failures into replayable fixtures that exercise recovery locally without a fleet service or model-dependent verdicts."
    }
  ]
}
```

## Source-grounded interpretation and initial priority recommendation

Current evidence: the docs audit records two reproducible continuity defects (omitted RC step amplitude and lost external profile in standalone bench re-review). The campaign and bench guides describe no general in-place campaign resume, durable supersession before re-review, and collisions after partial feedback. Measurement interfaces pin program bytes but explicitly require external model/parser qualification. The baseline gate maps declared requirements to checks; it does not determine requirement completeness. These observations establish design pressure, not proof that the proposed features will succeed.

Land the two bounded correctness repairs as separate first slices before expanding behavior. My initial enhancement order is: (1) a qualified, reproducible measurement adapter plus explicit applicability envelope; (2) an explicit evidence-request lifecycle that turns unresolved claims into an actionable existing-criterion experiment; (3) recoverable deterministic check/feedback publication with fault-injection tests. Recovery should start with one transaction boundary, not blanket resume of model calls or physical actions. Lower-ranked ideas should feed those slices or remain deferred rather than inflate the backlog into a platform.

Focus critique: an evidence request is not new acceptance authority. Its first slice must target an already declared criterion; new operating points or test criteria require a new owner-approved baseline, not mutation of a running campaign. Submitted evidence must never resolve a reviewer claim by itself. Qualification changes also alter the applicable policy/baseline; the focus suggestion to migrate legacy evidence without baseline change is rejected. Model validity is bounded by independently checked fixtures and conditions, not a self-declared qualified boolean. These constraints are requirements for the later detailed designs.

The load-bearing uncertainty is which engineering adapter yields the most immediate value; a small independently solvable RC tolerance or two-domain fixture is preferable to inventing a universal thermal solver. Priority scores favor useful end-to-end verification over infrastructure breadth. No claimed physical qualification follows from synthetic fixtures.

## Provocation

What if the most useful output of an AI electronics review were the cheapest experiment that can prove the reviewer wrong, rather than another page of findings?
