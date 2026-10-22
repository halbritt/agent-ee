# Subscription reviewer evaluation, 2026-10-16

Agent-ee now requires an explicit profile for every full review. Codex, Claude
and Agy use their native subscription CLIs. There is no implicit model or
OpenRouter fallback. Normal review enables installed engineering tools, shell
calculations and reference retrieval. The existing read-only candidate mounts
remain; this work adds no hermetic environment.

The practical starting choice is **provisional Codex/high**, using
`profiles/codex.json` for small scopes and `profiles/codex-baseline.json` for the
Universal 112 donor. This recommendation concerns observed completion and useful
engineering evidence in this small evaluation. It is not a general model ranking,
and it does not establish that two Codex judgments are independent verification.
Claude and Agy remain explicit options; mixed profiles are supported. Their
baseline attempts and partial results are retained, not replaced by Codex results.

```sh
cd ~/git/agent-ee
agent-ee review scopes/donor-case-alias.json \
  --profile profiles/codex-baseline.json \
  --out build/donor-new --max-seconds 3600
agent-ee inspect build/donor-new
agent-ee verify build/donor-new
```

The donor has known defects and unresolved conditions; a successful execution
should not be interpreted as design acceptance. For a passing small control use
`fixtures/rc/control.scope.json`, `profiles/codex.json`, and a fresh output path.

## Method and bounds

The [plan](../docs/model-evaluation-plan.md) was committed at `12da8e9` before
comparison calls. Its original bytes match `expectations.json`'s plan hash; the
current document additionally records the later explicit baseline budget change.
It provides independent native-connectivity, calculation and geometric answer
keys. Six pairs cover connector mapping, operating power, shutdown, track width,
DNP population and verification adequacy. The last four pairs are held out.
Two roles run on each candidate, with one repeat of the four development cases:
32 planned initial judgments per model, 96 total. Calls have equal 300-second
limits. A failed execution stops that harness's comparison campaign; unrun cases
are not counted as engineering misses. No prompts were tuned on held-out scores.

The functional tests are not included as automated failure flags in comparison
packets. Native ERC/DRC, netlist and saved-board geometry are supplied. Reviewers
have the full candidate and may run additional tools. Artificial resistor limits
and stimuli are explicit exercise assumptions, not ECU component specifications.

The [SHA-bound manual grades](model-comparison-grades.json) assess reasoning and
citations against these expectations. They are explicit assessments by the root engineering agent, not human
sign-off or a keyword oracle. `scripts/comparison_summary.py` checks each graded review's hash
before summarizing execution, findings, grades, latency and native usage records.
Raw usage metrics are harness-specific and should not be treated as comparable
prices or unique context sizes.

The comparison ran on `12ee1e3`, before the Codex nested-sandbox DNS correction.
Its local engineering tools worked, but shell network retrieval was restricted;
native web tools were separately available. These synthetic cases require only
local native evidence and explicit exercise assumptions. This is still an adapter
difference and limits cross-harness comparison. The later baseline calls and
release demonstrations exercise corrected ordinary network access; they do not
retroactively make the earlier trials an identical-tool-policy benchmark.

## Final execution and repeats

[Machine-readable summary](model-comparison-summary.json) retains all 96 planned
judgments, including unrun slots and each graded review's SHA-256.

| Harness / requested model (high effort) | Valid judgments | Timed out | Unrun | Median attempted call |
| --- | ---: | ---: | ---: | ---: |
| Codex / `gpt-6.1-sol` | 32 | 0 | 0 | 88.5 s |
| Claude / `claude-fable-5` | 25 | 1 | 6 | 171.0 s |
| Agy / `gemini-4-argon-high` | 1 | 1 | 30 | 221.2 s |

The duration medians include timed-out attempts and exclude unrun slots. Agy's
median is just two attempts and says little about typical latency. Harness-native
usage is retained per call. Codex records requested identity; its JSONL does not
attest the server model.

Codex repeated all four development cases with consistent primary conclusions:
controls remained clean and both roles detected both repeated seeded defects.
That gives 16 valid-control judgments, 14 supported seeded-defect judgments and
two ADC uncertainty judgments. Claude repeated only the connector-control circuit
judgment successfully; its physical repeat timed out at 300 seconds, stopping the
remaining six judgments. Claude's repeated defect consistency and Agy's defect
coverage are unestablished. No successful retry is substituted for either timeout.

Codex is the provisional starting choice because it completed this bounded
comparison, produced fewer unsupported supplemental claims in these cases, and
completed the release control/repair and donor investigations with ordinary tools.
The donor required assisted adjudication, so this is not an endorsement of its
automatic adjudicator as independently reliable. Claude's useful actual geometry
and circuit work and Agy's incomplete execution are preserved in full.

## First-pass engineering observations

| Case pair | Codex circuit / physical | Claude circuit / physical | Agy |
| --- | --- | --- | --- |
| Connector assignment | Control clean; both detect reversed contract | Control has nonblocking return-path observations; both detect reversal | Circuit control correct; physical timed out; defect unrun |
| Continuous resistor power | Control clean; both derive 0.2304 W defect | Control clean; both derive 0.2304 W defect | Unrun |
| Shutdown discharge | Control clean; both establish 0.60653 V failure | Control nonblocking margin note; both establish failure | Unrun |
| Saved track width | Control clean; both measure 0.25 mm below 0.30 mm | Both detect width defect; physical also overclaims that automation itself must enforce the threshold | Unrun |
| Assembly population | Control clean; both detect absent fitted bridge | Both detect absent bridge; circuit adds questionable DNP/BOM metadata concern | Unrun |
| Verification adequacy | Ideal control clean; both identify unknown ADC behavior, circuit severity too weak | Ideal control has no blocking defects; both identify missing ADC data but make supplemental quantitative overclaims | Unrun |

All five seeded defect types were detected by both completed models' two roles
in the first pass. No consequential seed miss was observed in those ten judgments
per model. Agy's unrun seeds are missing coverage, not detections or misses.
All completed comparison native ERC/DRC checks passed, so the functional and
exercise-limit findings were not repetitions of native failure reports.

Neither completed model raised a blocking defect on any first-pass valid control.
Claude's nonblocking observations on controls included single-via return reliance,
nominal timing margin and the lack of packaged automated functional/width checks.
Those must not be recast as established control defects.

The ADC case shows why the supporting argument still needs review. Claude's
circuit reviewer used a steady voltage-drop approximation as though it consumed
the full transient margin at 1 ms. Its physical reviewer used a necessary
asymptotic load bound as a sufficient timing guarantee. An independent
[calculation and counterexample](model-validation/adc-bound-check.json) preserve
the correct missing-data conclusion while rejecting those assurances. The
second review also underclassified the ADC unknown and emphasized unspecified
component tolerances. Codex's circuit role likewise assigned too-weak info
severity to its correct ADC uncertainty; the physical role preserved it as major,
and the separate full release run correctly deferred it.

## Adjudication and genuine failures

[Separate actual adjudicator calls](adjudicator-comparison/) exercised a supported
singleton against clean reviews, an unsupported allegation, conflicting arithmetic
and a stale author repair assertion. All twelve calls (four per model) returned
the expected dispositions. These are authored, evidence-grounded challenge inputs,
not a claim that three reviewers independently discovered them.

The donor exposed failures those small trials did not catch:

- Expanded Claude attempted to merge a major CAN concern into a minor `noted`
  observation and inferred assembled-bus functionality from an upstream module.
- Network-enabled Codex and the release Codex run attempted to make a component
  identification defect `noted`, despite the validator's explicit rule.
- The validator rejected each response as malformed and retained all findings.
  The root engineering agent then inspected native artifacts and source passages
  and recorded separate `operator-decisions.json` and adjudication events. These
  are **assisted recovery**, not successful automatic adjudications. Original
  execution records and raw rejected responses remain intact.

The correction accepts the native part-identity conflict within the declared
component-identity scope. It does not assert harmful substitution. Actual module
revision, GPIO drive and CAN bus behavior remain deferred. Thermal estimates with
assumed currents and copper do not establish overheating or qualification.

This is a material operational limit: inspect failed adjudication and reconcile
through the documented command; do not assume the recommended profile always
finishes without engineering oversight. There are no silent retries or majority
votes.

## Integration evidence and implementation versions

| Evidence group | Core | What it demonstrates |
| --- | --- | --- |
| [model-selection](model-selection/) | `12ee1e3` | Native versions, actual shell probes, initial Codex host-mount failure and corrected tool execution |
| [model-comparison](model-comparison/) | `12ee1e3` | Preregistered three-harness initial-review comparison |
| [adjudicator-comparison](adjudicator-comparison/) | `12ee1e3` | Twelve separate adjudication trials |
| [model-baselines](model-baselines/) | `12ee1e3` | Actual donor calls at 300 seconds per role |
| [model-baselines-expanded](model-baselines-expanded/) | `12ee1e3` | Explicit 900-second donor calls, including Claude recovery and Agy timeout |
| [model-network-probes](model-network-probes/) | `4be267c` | Codex nested sandbox DNS failure, then HTTP 200 and refused candidate overwrite after removing the redundant inner sandbox |
| [model-baselines-final](model-baselines-final/) | `4be267c` | All three donor routes with ordinary network/tool access; Codex evidence and assisted recovery; Claude/Agy circuit timeouts |
| [model-repair-final](model-repair-final/) | `4be267c` | Real native control, seed, repair, fresh agents and closure |
| [model-release](model-release/) | `5d01de9` | Latest 69 tests, donor, real repair demonstration and full uncertainty run |

The release adds a correction guard: changing only a profile cannot qualify as
a changed saved design. A regression test first reproduced the erroneous empty
`changed_inputs` closure, then passed after the fix. This does not alter reviewer
prompts or engineering criteria. Historical runs stay tied to their actual core;
no historical acceptance is silently transferred to the new implementation.

Agy's initial circuit control review completed, but its physical call timed out;
the remaining comparison campaign stopped. Donor circuit calls also timed out
at both 300 and 900 seconds. Extensive partial investigations remain useful but
do not count as a validated terminal review. Claude's 300-second donor circuit
and the later network-enabled 900-second circuit timed out; its separate expanded
900-second run produced both reviews and the failed adjudication described above.
No authentication failure or quota fallback was observed. A complete end-to-end
Agy engineering review was **not** demonstrated within these budgets. Its adapter
was exercised through actual tool use, one valid initial review and four valid
adjudicator responses; that narrower evidence must not be described as full
workflow qualification.

## End-to-end release demonstrations

The release [control](model-release/repair-demo/control/REPORT.md) satisfies its
declared criteria without unsupported blocking findings. The
[100k seed](model-release/repair-demo/seed/REPORT.md) fails the response requirement,
and both actual reviewers identify it. The saved schematic and board are restored
to 10k on a disposable candidate; [new native checks, ngspice and fresh reviews](model-release/repair-demo/repair/REPORT.md)
satisfy the original limits. The seed's event history links closure to the new
candidate and the changed native file hashes. No real donor circuit was modified.

The [ADC uncertainty run](model-release/uncertainty/REPORT.md) ends
`defects_or_unresolved`: the ideal unloaded response is supported, but the
requested ADC-connected behavior lacks capacitance, switch resistance, initial
charge and sampling timing. The major concern survives; the info concern is a
duplicate of it. No confirmed ADC circuit defect is invented.

The [release baseline](model-release/baseline/REPORT.md) retains native rule
failures, component identity ambiguity and deferred module-dependent behavior.
See [the engineering follow-up](MODEL-BASELINE.md) for source-specific conclusions.
The original [ten-criterion acceptance index](README.md) remains applicable to
its recorded implementation; this follow-up records the new harness/profile
behavior and its limits explicitly.

## Reproduction

Exact input snapshots, experiments, retrieved references and native logs are in
[versioned archives](archives/manifest.json), indexed by implementation commit.
The archives and most run files are in the owner's private evidence store
(AGENTEE-20). Fetch the `archives` group and the recorded group, then restore
with `python3 scripts/evidence_archive.py restore GROUP --evidence FETCHED_DIR`.
Use its recorded core for historical policy verification; current `verify` is
supposed to reject an older implementation as current acceptance. See
[reproduction instructions](../docs/reproducing-evidence.md).

To repeat the comparison, use a fresh directory and the preregistered command.
Do not overwrite failed trials, cherry-pick retries, or silently use changed model
allocations. A new tool policy or model selection warrants a new evidence series.
Bench validation, automotive transients, real loads, EMC, thermal behavior and
manufacturing qualification remain outside these demonstrations.
