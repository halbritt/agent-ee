# Semantic architect producer (AGENTEE-389 S2)

`agent_ee.physical_architect` asks one configured subscription model to propose
the assertions of an `agent-ee.physical-intent/1` document for one sealed review
run, then hands the document to the unchanged
[S1 compiler](physical-intent.md). It is the S2 slice of the
[routability design](research/2026-10-21-routability-design.md#48-semantic-architect-and-deterministic-intent-compilation).

**Status: opt-in and isolated.** It is a Python API only. No CLI, profile,
campaign step, placement adapter or routing consumer calls it, and
`tests.test_physical_architect` fails if any other module under `src/agent_ee/`
names it. Runtime activation remains gated by KICADROUTE-91, as for S1. The
model never edits a design file. It cannot swap pins, edit a schematic or
change a net, and the producer makes no physical compliance claim.

```python
from agent_ee.physical_architect import produce, verify_stage
produce('disabled', run, out)                     # None; publishes nothing
produce('manual', run, out, intent=intent)        # the S1 compile_and_publish
receipt = produce('architect', run, out, definition=definition, deadline=deadline,
                  stage=stage, reference=captured_intent)   # reference is optional
verify_stage(stage, run, out)                     # recheck launch records and package
```

## Modes

`produce(mode, ...)` is the proposed `physical_intent.mode` setting:

- `disabled` returns `None` and writes nothing. Existing manually captured
  contracts are untouched.
- `manual` compiles one explicitly selected intent through
  `physical_intent.compile_and_publish`. It refuses without an intent and refuses
  architect arguments, so it never starts a model call.
- `architect` runs the producer below. A manual intent is refused here: a
  manual file is never an automatic substitute for a failed architect call.

## One proposal and one bounded correction

The call uses the existing role boundary of the campaign's implementation
writer:

- `profiles.validate_definition` admits the definition. One explicit harness,
  model, effort and `timeout_s` are required. The producer forces
  `tool_mode: fixed_packet`, so the model gets no tools and can write no file.
- `preflight.capture_expectation` and the `reviewers.reviewer_call` preflight
  guard run under a new `architect` boundary. The guard checks the policy,
  runtime and declared-tool pins and runs the networkless sandbox probe.
- `review_run.structured_call` runs with a resubmission limit of 1.

The stage directory (`stage`) holds a copy of the run's `inputs`, `checks`,
`work`, `baseline.json` and `candidate.json`, so the sealed run itself gains no
file. It also holds the prompt, the launch expectation, every attempt under
`review-attempts/architect/<n>/`, the final `architect-execution.json` and the
`architect-receipt.json`.

The prompt carries the captured evidence: the baseline requirement IDs, the
run's `facts.json` and `pin-map.json`, the native footprints with pad numbers,
UUIDs and nets, and the captured input paths. The model returns only assertion
lists (`terminals`, `blocks`, `residual`, `moves`, `locality`, `obligations`,
`diagnostics`) and optional `records`, which are captured input paths.
`PROPOSAL_SCHEMA` gives every assertion a concrete nested shape. It covers the
locality endpoints, pairs, provenance (`decision_reference` and a `numeric`
derivation) and loop fields, with enums for the classes, metrics, moves, kinds
and statuses the compiler admits. For Codex, `harnesses.strict_schema` closes
every object and makes each optional property nullable. The producer drops those
nulls before composing the intent, because no intent field is ever null (review
ee389-s2 H3). The
coordinator composes the intent. The schema, `mode` and every source hash come
from the run itself. A `sources` key or any other key in the output is invalid.
A `records` path that is not a captured input is invalid.

The S1 `compile_plan` then grades the composed intent. A compiler refusal, or an
invalid output, is fed back once as `OUTPUT VALIDATION ERROR:` with the exact
finding, such as `verified CANL fact binds U1 pin 6, not proposed pin 7`. A
correction must not weaken the first proposal. It may repair what the compiler
refused, such as a pin or a pad UUID, but nothing the first proposal restricted.
It is refused if it:

- drops a required obligation or sets one to `required: false`;
- changes a required obligation's `kind`, `requirement`, `terminals`, `joins`,
  `forbidden_shared` or `proxy` (list order aside). Keeping the ID alone is not
  enough;
- drops a locality obligation, or changes its relationship: metric, loop fields
  and each role's endpoint reference, pad and net (a supply/return swap, for
  example);
- raises any `max_distance_nm`.

Conflicting duplicate IDs (two required obligations, or two locality
obligations, that share an ID but differ) are refused in either proposal,
whatever their order. No entry may stand in for another; exact duplicates are
treated as one. This rule is exact and conservative (review ee389-s2 H1). A second invalid output is final
(`status: refused`). There is never a third call.

The caller's monotonic `deadline` and `cancellation` bound the whole stage (review
ee389-s2 H2). Calls launch only before `deadline - reserve_s` (`RESERVE_S`,
5 s by default). Each call's timeout is the smaller of `timeout_s` and the time
left before that point. Validation does not start once the deadline has passed
or the stage is cancelled. Compilation that ends after either is discarded. The
package is written to a hidden sibling of `out` and verified with
`verify_receipt`. The deadline and cancellation are checked again, and only then
is it renamed into place. Any stop, even after an on-time response, gives
`status: stopped` with its reason, and nothing is published.

## Failure and stop

An execution failure, for example `unavailable` (no subscription credential),
`failed` (auth or quota) or `not_run` (cancelled, deadline, preflight refusal),
stops the operation with `status: stopped`. The producer does not retry it and
tries no other harness, model or provider; there is no OpenRouter or paid
fallback, and the receipt records `fallback: null`. Nothing is published in any
outcome other than `accepted`.

## Acceptance, binding and the reference contract

A compiled proposal is `accepted` only if every proposed assertion is bound. The
receipt's `bindings` map each terminal, locality obligation, `block:<id>` and
`allocation:residual` to its compiled JSON pointer. A required or optional
Kelvin or current-loop obligation maps to `unsupported`, a diagnostic to
`diagnostic` and an allowed move to `receipt:allowed_moves`. The package is then
published atomically and checked with `verify_receipt`. The receipt copies
`semantic_completion` and `required_unsupported`, so a required unsupported
obligation stays visible.

With a `reference` (a captured manual intent, itself compiled first), `compare`
checks the proposal one assertion at a time, diagnostics included. Any assertion that differs, is
missing or is extra is listed in `comparison.unresolved`. The stage is then
`unresolved` and publishes nothing. On the locality fixture, a proposal that
swaps the supply and return pairs compiles under S1 alone, because both pairs
join same-net native pads. Against the reference it is reported as differing,
with both versions recorded. The run's board, netlist, facts and evidence hashes
stay unchanged.

The receipt records `model_stage_s` (model wall time) and any provider
`model_usage` separately from compilation. It is identity-bound
(`receipt_id`). `verify_stage` rechecks the receipt, every preflight receipt and
attempt archive. For an accepted stage it also rechecks the published package
against the stored proposed intent.

## Tests and limits

`tests/test_physical_architect.py` runs on the sealed S1 fixture runs and never
calls a model. A scripted transport replaces `reviewers.dispatch`, the provider
launch itself. The real preflight guard, its sandbox probe, structured
resubmission, launch-record verification and the S1 compiler all run. The tests
cover:

- one accepted proposal and its bindings, including a required unsupported
  Kelvin obligation;
- a compiler refusal followed by exactly one correction;
- forged sources, a missing object, an uncaptured record, a wrong shape and an
  invented requirement, each refused after two calls;
- auth and quota failures, each with one call and no fallback;
- cancellation and an exhausted deadline before launch, and a correction that
  would start after the deadline;
- the seeded misbound return, compared with the reference contract, with
  missing and extra assertions;
- corrections that drop or unrequire a Kelvin obligation, drop a locality
  obligation or loosen a bound;
- corrections that keep a required obligation's ID but change its content, and
  a locality relationship swap;
- conflicting duplicate required and locality IDs, in both orders, beside an
  accepted exact duplicate;
- on-time responses whose validation ends after the deadline, late responses,
  cancellation during the call, and the validation reserve;
- the strict Codex form of the schema: every object has concrete properties, a
  full compiler-shaped proposal and its null-filled wire form conform, extra keys
  are refused, and the wire form compiles end to end;
- the manual and disabled modes, and isolation.

Not in this slice:

- campaign, profile and placement-adapter wiring (gated by KICADROUTE-91);
- any live model run. No captured agent proposal for the pod exists yet, so the
  design's comparison of a captured pod proposal against the 17-block contract
  is still open;
- the M6 feedback loop.
