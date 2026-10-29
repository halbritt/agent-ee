# Acquisition completion in generation cases

`generation_benchmark.run_task` retains its legacy defaults. Opt-in acquisition
mode adds `completion_context=PATH, expect_context=SHA256` (CLI
`--completion-context`, `--expect-context`) and permits at most two attempts.
The public generator still sees only its script, prompt and its own writable
scratch. It cannot select a command, source module, credential or evaluator path.

The coordinator context is separate from the private task and never mounted
into the writer. Its `pins.json` is:

```json
{"schema":"agent-ee.acquisition-context-pins/1","files":{"context.json":"SHA256","...":"SHA256"}}
```

The external pins-file digest authenticates its complete ordinary file tree.
`context.json` has exactly `schema`, `plan`, `added_sources`, `review_profile`:

```json
{"schema":"agent-ee.acquisition-completion/1","plan":"reference/build.json","added_sources":{"cpl":"cpl","manufacturer-text":"manufacturer-text"},"review_profile":"profile.json"}
```

Paths are canonical and contained; the reference plan's source cannot escape
this public context. Root must provide/freeze the actual qualified role/group,
reference/catalog and review context. This module does not qualify a new part or
apply a per-case catalog exclusion; unsupported task/context shapes refuse.

The generator emits **only** `output/request.json`, at most4096 bytes:

```json
{"schema":"agent-ee.acquisition-request/1","brief_sha256":"EXACT_PROMPT_SHA256","action":"prepare"}
```

Second-attempt action is `repair`. Neither native outputs nor `refusal.json`
from the writer are accepted in this mode. The fixed trusted module alone calls
`acquisition_prepare.prepare`; only its actual `PreparationRefused.report` can
supply a refusal for independent private-oracle grading.

Each attempt debits generation, trusted completion and grading admissions in the
existing ledger. Default acquisition work allowance is three credits per attempt;
the case wall budget is **not increased**. Generation and completion share the
original productive cutoff; grading keeps the original final reserve. The
trusted Host child is supervised by existing `native_run`, outside the writer's
extra sandbox but inside the admitted outer runtime. Production is at the
trial-owned sibling `production-N`; generation cannot write it. Cleanup errors,
late zero exits and missing terminal records cannot establish success. Raw
completion records and all production failures enter authenticated series
custody. Untrusted request trees are never copied into acquisition repairs.

Accepted packages are replayed with the **existing production qualification
verifier**, all seven sealed stages, the requested full package review, final
native hashes and the complete retained tree. There is no adapter-owned release
boolean. Existing `evaluate`, baseline checks, evidence seals and series replay
grade those exact candidate bytes. Read-only qualification (`persist=False`)
preserves original records. Offline replay supplies the previously bound runtime
observations through the existing verifier's `host` seam and checks current
source identity; it launches no new native/model processes. Supplier and physical
limitations remain separate; differing scope candidate IDs are not equated.

## Unresolved composition (fail closed)

The default fixed child currently completes genuine preparation refusals only.
Supported preparation stops at `CompletionUnavailable`: no judgment author or
verifier is fabricated, and no package/build acceptance is claimed. Actual
campaign repair remains unwired and refuses. Native/provider jobs in source
qualification are zero.

`acquisition_completion.complete_prepared` is the trusted **parent-side**
production-module entry for the next integration. It imports only the fixed
`agent_ee.judgment_seats` module (no configurable shell/module). It builds actual
artwork, calls:

```python
judgment_seats.produce(build_run, out, author=author, verifier=verifier,
    private_evaluator_pair=private_evaluator_pair, deadline=deadline,
    cancellation=cancellation, launch_environment=launch_environment, host=host)
```

It passes the SAME original cancellation object and absolute productive deadline.
The future producer return contract is `execution`, `outcome`,
`judgment_directory`, `judgment_inventory`, `producer_receipt`,
`producer_receipt_sha256`. The directory and receipt must be owned beneath `out`;
the helper adopts their exact pinned records without overwriting prior testimony,
then resumes the same build through package/full review and replays qualification.
This return shape needs alignment/review with agent690's forthcoming module.
Author and verifier harness/model pairs differ from each other and the evaluator;
they may overlap package reviewer models through separate independent calls.

**This parent-side composition is not yet wired into the default supervised
child.** Python cancellation-object identity cannot be serialized across a Host
process boundary. Root must finish that composition while preserving the existing
case supervisor and every phase deadline; this source does not claim otherwise.
No supported design can pass until the genuine seats, role context and repair
handoff are reviewed and connected. The private four-case oracle remains701-owned.

Public interface direction was reviewed by701 at
`a61c4921-6289-40b6-843c-0ad0665212ae@1`; that was not implementation approval.
One later native admission experiment is a disposable non-G4 public control,
one attempt and one case clock, including actual preparation/native build,
author/verifier calls, package/full review and independent evaluator capture.
Root must pin the reviewed composition/image/context/profile and choose finite
budgets; no measured fit for that complete run is known. Through-artwork plus
post-case review is not this experiment and cannot qualify G4.
