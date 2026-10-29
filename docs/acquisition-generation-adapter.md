# Acquisition completion in generation cases

`generation_benchmark.run_task` keeps its existing defaults. Opt-in acquisition
uses `completion_context=PATH, expect_context=SHA256` (CLI
`--completion-context`, `--expect-context`) and at most two attempts. The writer
sees only public script/prompt inputs and its own scratch. It emits only bounded
`output/request.json`:

```json
{"schema":"agent-ee.acquisition-request/1","brief_sha256":"EXACT_PROMPT_SHA256","action":"prepare"}
```

The trusted public context is a separate ordinary file tree. `pins.json` has
`schema: agent-ee.acquisition-context-pins/1` and `files`, the exact inventory
excluding itself; its externally supplied SHA-256 binds that inventory.
`context.json` has exactly `schema`, `plan`, `added_sources`, `review_profile`,
plus optional `prepare_options`:

```json
{"schema":"agent-ee.acquisition-completion/1","plan":"recipe/build.json","added_sources":{"cpl":"additions/cpl","manufacturer-text":"additions/manufacturer-text"},"review_profile":"review-profile.json","prepare_options":{"final_groups":true}}
```

All paths are canonical, contained in the pinned tree, and of the required file
or directory type. Additions also allow `operating-supplement`,
`source-supplement`, and `power-repair`. Optional preparation arguments preserve
`acquisition_prepare.prepare` defaults: role context/hash/source, alternative
context/hash/source, evaluation context/hash, expected intent/hash and parts
receipt/hash, final groups, address tie, and verification timeout. Related
argument groups must be complete. Files have explicit existing hash arguments;
source directories are covered by the complete context inventory. This adapter
adds no alternative selector or qualification rule.

The parent also receives `completion_policy=PATH,
expect_completion_policy=SHA256` (CLI `--completion-policy`,
`--expect-completion-policy`). This separately pinned, parent-only JSON contains:

```json
{"author":{"harness":"codex","model":"POLICY_MODEL_A","effort":"high","timeout_s":300},"verifier":{"harness":"claude","model":"POLICY_MODEL_B","effort":"high","timeout_s":300},"private_evaluator_pair":["HARNESS","MODEL"],"launch_environment":{"run_id":"UNIQUE_CASE","policy_files":{},"runtime_files":{}}}
```

The environment maps must contain the actual final composed source/runtime
pins expected by ordinary reviewer preflight. These example values are not a
runnable policy. Author/verifier pairs must differ from each other and from the
private evaluator. Existing package-review pairs may be reused through separate
calls. Seats require source-inspection tools and use subscription routes only;
authentication/quota failures do not retry or fall back.

`acquisition_completion.complete` supervises the fixed preparation child with
existing `native_run`. Only a genuine `PreparationRefused.report` can supply a
refusal. Otherwise the child emits `{"kind":"prepared"}`, an intermediate
terminal. The trusted parent calls `complete_prepared`, using the SAME parent
Cancellation object and original absolute productive cutoff. No Python object
identity is serialized to the child. Raw child execution and separate
`parent-completion.json` records are preserved; a zero child exit alone cannot
supply a package. Missing records, parent failure, cleanup failure, late finish,
cancellation and retention failure cannot count as success.

`complete_prepared` builds through actual artwork, then invokes the fixed module:

```python
judgment_seats.produce(build_run, out, author=author, verifier=verifier,
    private_evaluator_pair=private_evaluator_pair, deadline=deadline,
    cancellation=cancellation, launch_environment=launch_environment, host=host)
```

The producer captures exact artwork/source bytes, specification spans, board
and scope hashes, applicability, and completed public stage evidence. Two
serial batch calls use existing `reviewer_call`/`structured_call`/preflight,
with no schema retries. They author fresh `engineering-judgment/1` testimony
and independent `judgment-verification/1` witnesses. Actual call records,
prompts, schemas, responses, native streams, invocation IDs and output hashes
remain outside published judgment documents. Witness validation is strict even
when author testimony records unresolved defects. A negative or dissent remains
negative. The source material and check observations support actual reasoning;
check statuses never generate testimony.

The producer returns `execution`, `outcome`, `judgment_directory`,
`judgment_inventory`, `producer_receipt`, `producer_receipt_sha256`.
`judgment_seats.verify` replays both call/output bindings without another launch.
The parent adopts only verified fresh records into the prepared source, refuses
to overwrite existing testimony, then resumes through the package stage. Only
existing full production qualification, seven-stage custody and requested full
package review can establish a package binding. The adapter adds no release
boolean. Read-only replay uses frozen runtime observations and current policy
source identity. Source readiness and pure tests are not native qualification.

Generation, preparation, build, seats, package and grading retain the original
case budget and cancellation. Parent completion cannot consume the final grading
reserve or renew the productive clock. Raw failures stay in series custody.
Supplier/physical acceptance remains outside these software results.

The optional completion seam `public_context=PATH,
public_context_sha256=SHA256` (fixed child flags `--public-context`,
`--expect-public-context`) reserves the pinned public per-case protocol.
Transport/hash/replay in `generation_benchmark` and the catalog selector are
separately owned integrations. Until connected, supplying it explicitly refuses;
no private evaluator logic or duplicate selector lives here. Attempt two's
campaign repair connector also remains explicit `CompletionUnavailable`.
Root must assemble/freeze final role context, seat policy and complete composed
source/runtime identity before any execution. No full runtime fit is claimed.
