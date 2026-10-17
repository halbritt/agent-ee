# Recorded acceptance evidence

The reusable implementation is in `src/agent_ee`, invoked through `agent-ee`
or the installed skill. Fable and Argon were launched in Herdr on their existing
subscriptions, assessed the architecture independently, and built/reviewed
separate implementation parts. Fresh review invocations are separate from those
builder conversations. Model/agent count is not treated as verification proof.

Independent expected electrical/geometry outcomes were committed in
[`docs/evaluation-plan.md`](../docs/evaluation-plan.md) before implementation.
The demonstrated candidate uses native KiCad files; the generated netlist and
simulation are traceable to those saved files. No expected finding prose is
matched by the implementation.

## Acceptance matrix

| Demonstration | Recorded result | Evidence |
| --- | --- | --- |
| Existing environment integration | Installed CLI/skill discovered and invoked from a fresh `/tmp` shell; tools actually probed. | [Independent operator discovery](../docs/integration-review-argon.md), [environment probes](verification/environment.json), [installer](../scripts/install.sh) |
| Valid control | `criteria_satisfied`; native ERC/DRC clean, 0.25 mm tracks meet the 0.20 mm criterion, measured 1 ms response about 0.6321201 V. Supported non-defect concerns remain noted. | [Control report](rc-verified/control/REPORT.md), [checks/reviews/result](rc-verified/control/result.json) |
| Electrical defect beyond ordinary rules | Saved R1=100k in schematic and board passes native ERC/DRC but produces about 0.0951626 V at 1 ms, violating the original 0.62–0.65 V requirement. Actual independent agents identify/support it. | [Seed report](rc-verified/seed/REPORT.md), [generated deck](rc-verified/seed/work/rc-step/deck.cir), [ngspice log](rc-verified/seed/work/rc-step/ngspice.log) |
| PCB implementation evidence | Saved-board geometry/connectivity checked, with original native artifacts, positions, nets and UUIDs retained. | [Control geometry](rc-verified/control/work/geometry/geometry.json), [donor board evidence](BASELINE.md) |
| Verified correction | Both disposable saved native Value fields restored to 10k; fresh full review satisfies the original criteria. Correction events link both the native check finding and the agent's electrical finding to the new candidate. | [Saved changes](rc-verified/repair.patch), [repair record](rc-verified/repair.json), [fresh repair report](rc-verified/repair/REPORT.md), [native finding closure](rc-verified/seed/events/60e80659-5497-4a79-b279-48bc872b6941-correction.json), [agent finding closure](rc-verified/seed/events/b88322f4-2b26-4e79-aaed-5a143da39678-correction.json) |
| Evidence freshness | An actual passing candidate's saved schematic and board were temporarily changed to 22k: old evidence was refused with exit 2. Exact restoration recovered the original candidate only. Input/library/requirement changes and stale reviewer IDs are also refused in tests. | [Live mutation challenge](verification/freshness-mutation.json), [old-rule refusal](verification/old-implementation-refusal.json), [candidate tests](../tests/test_candidate.py), [identity tests](../tests/test_reviewer_identity.py) |
| Execution/review failure | Missing executable, timeouts, malformed native output, missing/non-finite measurements, solver failure, incomplete traces/reviews remain distinct from pass/design defects. An actual malformed-role review was rejected. | [63-test log](verification/tests-verified.log), [live rejected review](rc-demonstration/control/review-executions/physical.json), [native failure tests](../tests/test_checks.py), [reviewer failure tests](../tests/test_execution_failures.py) |
| Disagreement/uncertainty | Singleton claims survive clean other reviews; unsupported claims are downgraded/refused, explained dismissal is required, and major uncertainty cannot be hidden in notes/duplicates. Live control observations are retained without invented blocking requirements; donor CAN2 termination remains open. | [Finding tests](../tests/test_findings.py), [control dispositions](rc-verified/control/result.json), [donor dispositions](donor-verified/result.json) |
| Resource limits | Actual SIGINT after real seed checks preserves useful defects and incomplete review state. Exhausted-budget tests preserve unknown coverage. | [Interruption experiments](verification/interruption-experiment.json), [preserved seed](rc-final/seed/REPORT.md), [historical replay](verification/historical-interruption-replay.log), [lifecycle tests](../tests/test_cli.py) |
| Real project baseline | Original donor is preserved and reports partial hierarchy coverage; an explicit disposable case alias restores complete native checks and actual fresh reviews. Defects/open questions remain; no hardware approval. | [Baseline analysis](BASELINE.md), [latest baseline report](donor-verified/REPORT.md), [as-received report](donor-final/REPORT.md) |

## What the small evaluation observed

One functional defect was deliberately seeded: the series resistor's saved
value changes the passive response while leaving connectivity/rule checks clean.
It was detected by the real tool check and actual agent reviews; no seeded defect
was missed in the completed run. The disposable correction passed the original
requirements with a measured 0.6321201 V response, and its closure records name
the distinct saved candidate and fresh checks. The completed
control raised minor/info concerns about saved return geometry, import-time tool
assertions and verification observations; none established a violated criterion.
They remain visible as noted observations or duplicates, rather than being
silently erased or promoted to unsupported blocking defects.

Initial interrupted/rejected iterations are retained, including the actual
reviewer-role protocol failure and the deprecated via API call discovered by an
agent. They motivated versioned corrections and fresh runs. They are not counted
as successful completed reviews or as completed seed misses. The corrected via
query removes 1,224 donor assertions with identical measured diameters.

This is one seed and one representative passive control, not a general hardware
correctness benchmark. Active-device models, broad value normalization and ECU
bench/vehicle/thermal/EMC/manufacturing qualification are not established.

## Inspecting and reproducing the records

Use `agent-ee inspect RUN` for outcome, findings and freshness. `verify` checks
freshness only; its exit 0 can accompany `defects_or_unresolved` or `incomplete`.
Acceptance also requires the recorded `criteria_satisfied` outcome.

Expanded `inputs/` copies remain on the original workstation. Versioned
archives preserve their exact bytes alongside the readable reports:

```sh
python3 scripts/evidence_archive.py restore donor-verified
python3 scripts/evidence_archive.py restore rc-verified
```

Each [archive manifest](archives/manifest.json) identifies the source commit,
archive checksum and recorded candidate/run outcomes. Restore verifies the
checksum and refuses to overwrite changed evidence. [Round-trip validation](verification/archive-roundtrip.json)
reproduced all captured hashes, and [failure cases](verification/archive-failure-cases.json)
refused changed files and corrupt archives. Historical attempts require their
recorded implementation version for integrity replay; they do not become
acceptance under new rules.
The [fresh-checkout replay](verification/fresh-checkout-replay.json) restored both
final archives into a clean detached Git worktree and verified all four final
candidate records with `--snapshot-only`. This verifies retained evidence
identity; it does not rerun the native tools or model judgments.

To reproduce a fresh full engineering demonstration with the same fixed
requirements, run `python3 scripts/demo.py --out build/my-demo` from the repo.
It invokes actual native tools and subscription reviewers, performs at most one
disposable repair, and records actual outcomes without retrying failures into a pass.

## Subscription profiles and tool-enabled follow-up

The [three-harness evaluation](MODEL-SELECTION.md) records actual Claude, Codex
and Agy calls, separate adjudicator trials, donor attempts and failures. The
[new donor report](MODEL-BASELINE.md) separates confirmed native/component-identity
findings from unresolved module, reset, CAN and thermal behavior.

Current release evidence uses core `5d01de9`: [repair demonstration](model-release/repair-demo/),
[full uncertainty result](model-release/uncertainty/REPORT.md),
[baseline with assisted adjudication](model-release/baseline/REPORT.md), and
[69-test log](model-release/tests.log). [Fresh-worktree replay](model-validation/fresh-checkout.json),
[implementation mismatch refusal](model-validation/old-policy-refusal.json),
and [source-state check](model-validation/source-state-final.json) distinguish
historical records from current acceptance. Earlier evidence above retains its
original implementation identity.
