# Acquisition completion in generation cases

`generation_benchmark.run_task` keeps its existing defaults. Opt-in acquisition
uses `completion_context=PATH, expect_context=SHA256` (CLI
`--completion-context`, `--expect-context`) and at most two attempts. The writer
sees public script/prompt inputs, optional task `public_context`, and its own scratch. It emits only bounded
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

`complete_prepared` builds through actual artwork and runs the ordinary
`review_build(profile=None)` checks before invoking the fixed module. Both use
the original deadline and cancellation handle:

```python
judgment_seats.produce(build_run, out, author=author, verifier=verifier,
    private_evaluator_pair=private_evaluator_pair, deadline=deadline,
    cancellation=cancellation, launch_environment=launch_environment, host=host,
    native_checks=native_checks)
```

The producer captures exact artwork/source bytes, specification spans, board
and scope hashes, applicability, and completed public stage evidence. It also
captures the complete requirements text, declared scope and limitations, and
the verified current checks at `/evidence/work/native-checks/review`. Those
checks must bind the exact selected artwork, candidate files and scope. Their
raw outputs remain available even when checks fail; they confer no acceptance.
The copied evidence tree is sealed and rechecked during seat calls and replay.
Two
serial batch calls use existing `reviewer_call`/`structured_call`/preflight,
with no schema retries. They author fresh `engineering-judgment/1` testimony
and independent `judgment-verification/1` witnesses. Actual call records,
prompts, schemas, responses, native streams, invocation IDs and output hashes
remain outside published judgment documents. Witness validation is strict even
when author testimony records unresolved defects. A negative or dissent remains
negative. The source material and check observations support actual reasoning;
check statuses never generate testimony.

The AGY judgment verifier may make one fresh call after a confirmed stream-idle
transport failure. New `judgment-seat-production/4` receipts declare this policy;
version 3 receipts retain their original single-attempt replay. Both attempts
share the original verifier deadline and case
deadline, including preflight and evidence retention. The failed call is archived
before its scratch workspace is cleared; the retry gets the same read-only
inputs, schema, model and base prompt, with a fresh preflight. Its failed answer
and experiments are not supplied to the retry. Transport attempts have separate
counters; schema resubmissions remain zero. Replay verifies the retained failure
stream and both launch receipts before reading the final successful response.
Authentication or quota failures, cancellation, process timeouts, unknown errors,
malformed output and engineering dissent do not trigger this recovery. Authors,
implementation writers and other reviewer roles keep their existing policies.

The complete brief remains at `/evidence/packet.json`; prompts reference it
instead of repeating the captured file inventory. The author writes the full
`{"judgments":[...]}` document to `/work/engineering-seat/judgments.json`, using
the read-only contract at `/evidence/work/judgment-contract.json`. Its final
structured response is an `agent-ee.judgment-delivery/1` manifest with that
fixed relative path and the file's SHA-256. A successful terminal response is
required. A completed scratch file after timeout, cancellation or a missing
response is never recovered as testimony.

The producer requires an unaliased regular file no larger than 4 MiB. It rejects
oversized files before reading their payload and retains the existing deadline
and cancellation checks. It verifies the digest and strict JSON, and validates
every required span, source, applicability fact and candidate
binding. It rereads the archived role file after call-custody verification and
publishes only those validated records for the independent witness. Replay uses
the archived bytes; mutable scratch is not a source. The witness reads the
complete records from its read-only evidence mount. File delivery grants no extra
time or schema retries and does not change engineering acceptance criteria.
Captured text is decoded without newline conversion in both seat validation and
package qualification, so CRLF captures retain their original hashes and quotes.
The packet explicitly lists files that cannot be cited as strict UTF-8 text;
they remain available as hash-bound native inputs. Both seats receive the
distinction between a scoped defect and an explicitly deferred physical or
supplier obligation. Genuine unresolved defects still prevent admission.

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

A task may declare one canonical task-relative `public_context` file outside
`evaluator/` and the deliverables. Its exact `agent-ee.case-context/1` wire schema
and label follow the selected public protocol v2. It is copied beside the prompt;
its external task pin is carried through ledger, evaluation binding, completion
receipt and series replay. Before any attempt, catalog identities are matched by
the existing `acquisition_resistor.policy` against the frozen common catalogs;
common resistor cohorts use its existing `read` qualification replay. The trusted
child passes the exact file/hash to `acquisition_prepare.prepare`. Optional
`resistor_context`, `resistor_context_sha256`, `resistor_source` form one complete
group, require the ADC alternative group and exclude an explicit role group.
There is no second selector. This integration requires the producer APIs from
3bfcb39b or their integrated descendants.

The separately requested completion-policy hash is frozen before generation in
`completion-policy.json`, ledger, evaluation binding, completion receipt and
series evidence. Policy bytes stay outside the public writer view. Legacy calls
without these optional fields keep their defaults.

Attempt two supports only the finite generated U2 ADDR repair. Its request has
`action: repair`. The parent selects the fixed sibling `production-1`, verifies
its failed completion and captured wrong V3V3 recipe, and requires an unchanged
captured evaluation clock. On the first productive failure, an eligible retained
prefix (at least four completed stages) is reviewed with the existing
`build_workflow.review_build` inside the original deadline/cancellation. Its
`accepted: false` diagnostic remains separate from package acceptance. Missing
candidate, incomplete review, unavailable seats, missing sealed `adc-ties`
defect or missing accepted public `ACQ-ELEC-6-TIES` finding stops repair.

The existing campaign convergence function must permit repair. One fresh
subscription implementation call (optional parent-policy `implementation`
definition) returns `summary`, `addr_tie`, and `finding_ids`; only an attributable
GND correction is admitted. Captured inputs remain byte-identical. No CAD edit,
private evaluator feedback, intent retry or schema resubmission is authorized.
A fixed child regenerates from the original pinned preparation source into fresh
`production-2/prepared`, followed by the existing seven-stage completion with
new judgments and package review. Complete authored baselines, complete
original preparation recipes (except the finite tie), first-attempt custody,
actual writer/preflight/response and final package/native candidate identity are
replayed before the successor is exposed. Generated history uses existing
`convergence.advance`; it fabricates no ordinary implementation-step receipts.
The production package verifier remains the acceptance authority.

Each build retains its own derived passive-rating baseline. Generated repair
requires matching rating declarations except for the build-specific receipt hash,
validates both captured receipt/report pairs, and compares the complete authored
baselines compiled after removing only the rating derivation. Changed authored
criteria or sources still refuse repair. Raw baselines and convergence state
retain their original per-build identities.

All acquisition reviews inherit the original cancellation object and cap setup,
imaging, checks and reviewer deadlines at the original productive cutoff. The
actual review and campaign declarations use zero schema resubmissions. Standalone
review defaults and adjudicator inheritance remain unchanged. Attempt count is
at most two, with at most three phase credits per attempt; the final grading
reserve is never productive time. Private evaluator checks are withheld from the
acquisition repair workspace. All started failures and missing records remain
in the frozen case tree.

Root must assemble and qualify the final common source cohorts, freeze the
parent policy and composed source/runtime, and admit one public repair experiment
before any G4 use. Full baseline equality and an actionable first diagnostic are
eligibility checks, not assumed properties of an unmeasured target. No numeric
case fit, source qualification, native success or G4 acceptance is claimed here.
