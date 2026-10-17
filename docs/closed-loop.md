# Closed-loop workflow overview

The scope/2 workflow preserves one implementation writer, a captured candidate,
independent domain reviewers and centralized adjudication. Requirements remain
fixed while tool evidence, candidate history and physical observations determine
whether another repair is allowed.

## Choose the relevant contract

| Task | Reference |
| --- | --- |
| Capture requirements, targets and reference architecture | [Scope/2 baseline and migration](scope-reference.md#version-2-baseline) |
| Run bounded implementation/review iterations | [Campaign guide](campaign-guide.md) |
| Integrate a simulator, extractor, manufacturing check or captured telemetry parser | [Measurement adapters](measurement-adapters.md) |
| Expose thermal, electrical, parasitic and manufacturing margins together | [Cross-domain coupling](measurement-adapters.md#cross-domain-coupling) |
| Ingest prototype data and calibrate later checks | [Bench feedback](bench-feedback.md) |
| Parse baseline, seal, witnesses, history or calibration records | [Result reference](result-reference.md) |
| Understand information flow and writer isolation | [Architecture diagrams](architecture.md) and [security](security.md) |
| Diagnose a stopped campaign or failed feedback attempt | [Troubleshooting](troubleshooting.md#closed-loop-refusals) |

## Five enforced boundaries

| Invariant | Enforcement | Limit of the conclusion |
| --- | --- | --- |
| Universal ground truth | Complete structured baseline, captured normative sources and check specifications; reconstruction on verification; direct access in tools, reviewers and adjudication | The operator still owns requirement completeness, source authority and the semantic requirement/check mapping |
| Deterministic evidence | Coordinator check seal; pinned producer executable; read-only request/raw files; candidate-bound metrics; requirement witnesses; only check findings can become accepted scope/2 defects | A retained executable/parser/model still needs physical qualification; hashes are not proof that a physical interpretation is correct |
| Cross-domain visibility | Same-condition measurements from distinct declared domains, conjunctive bounds and retained source facts | Tool adapters must calculate the actual coupled physics; no universal electro-thermal solver is bundled |
| Bounded convergence | Historical passing checks and metric bounds, repeated-design detection, progress rules and a finite total iteration allowance | Escalation is conservative termination, not a proof of physical infeasibility or a globally optimal design |
| Empirical grounding | Candidate-bound raw samples, cumulative discrepancy envelopes, replay cases, superseded acceptance, fresh calibrated review and campaign epochs | Instrument authenticity and model applicability require engineering validation; synthetic fixtures do not establish bench behavior |

The [campaign state diagram](campaign-guide.md#states-and-stop-rules) defines the
exact progress order, including how a shrinking failed-check set differs from
partial metric progress. The [bench sequence](bench-feedback.md#capture-and-re-entry)
shows when old acceptance is superseded and why a later execution failure cannot
restore it.

## Compatibility and introduction

The package metadata remains 0.1.0. Scope schema version, captured policy hashes
and runtime identities establish the applicable behavior; a package version
alone is insufficient. Existing `agent-ee.scope/1` reviews remain supported and
do not acquire scope/2 evidence guarantees simply by running newer code.
Campaigns and bench ingestion require a scope/2 baseline. There is no automatic
migration of historical acceptance.

Use [the complete control scope](../fixtures/closed-loop/control.scope.json) and
[explicit campaign configuration](../fixtures/closed-loop/campaign.json) as
examples of declared requirements and subscription allocation. Adapt their actual
requirements and paths for another project; the RC fixture limits are not generic
electronics acceptance criteria.

## Recorded implementation verification

The closed-loop implementation was checked with 98 Python tests after independent
review fixes. The tests include real Bubblewrap producer/parser execution with
scripted agent responses. Their relevant coverage is:

| Area | Exercised evidence |
| --- | --- |
| Baseline and claims | [test_baseline.py](../tests/test_baseline.py): text budgets, changed sources/evidence, unmapped witnesses, claim laundering, shared manual/automatic gates |
| Measurement and coupling | [test_measurements.py](../tests/test_measurements.py): nonfinite metrics, condition/domain mismatches, forged request/raw artifacts, binary mismatch, coupled limits |
| Convergence | [test_convergence.py](../tests/test_convergence.py): A/B regression, partial metric progress, stall and iteration bounds |
| Writer ownership | [test_locking.py](../tests/test_locking.py): process contention, exception release and ancestor campaign lease |
| Empirical correction | [test_empirical.py](../tests/test_empirical.py): bias changes acceptance, replay evidence, model/condition/raw-evidence validation |
| Complete coordinator loops | [test_campaign.py](../tests/test_campaign.py): repair and fresh review; immutable-input refusal; history verification; cumulative bench evidence; calibrated epoch repair; write-authority tampering |

The final native control in local `build/closed-loop-final-native2` passed KiCad
ERC, DRC, geometry and ngspice checks; netlist export was informational.
Verification reported fresh evidence. Its checks-only outcome was intentionally
incomplete. These local build paths are observations, not portable committed
archives; use the fixture and test commands to reproduce current behavior.

### Live subscription failure-path demonstration

The local `build/closed-loop-live-campaign` ran the seeded native RC candidate
through actual Claude subscription circuit and physical reviewers. Both completed
and independently identified the response violation. Adjudication then cited
`review-work/adjudicator/experiments/NOTES.md`, while the actual file was under
`experiments/adjudicator/NOTES.md`. Citation validation refused the response, the
campaign stayed incomplete and no implementation agent ran. Its history verified
under the implementation used at that time, before the final integrity repairs.

This establishes actual independent calls and fail-closed adjudication on the new
contracts. It does not establish a completed live-model repair cycle. Complete
repair and empirical continuation are demonstrated by integration tests with
scripted model responses. No provider fallback or silent citation correction was
used. No physical prototype qualification is claimed.

## Review and decision provenance

[Independent review dispositions](closed-loop-review.md) record Fable and Argon's
implementation findings and the evidence-based fixes or refusals.
[Decision provenance](closed-loop-doctrine.md) and the
[structured decision receipt](closed-loop-decision.json) preserve the bounded
architectural rationale; they do not enlarge the engineering claims above.
