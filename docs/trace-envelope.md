# Shared trace envelope `pcb-trace-envelope/1`

`pcb-trace-envelope/1` is the shared, additive, versioned record for PCB pipeline
timing and replay traces. It was decided by agent-449 with agent-507 on 2026-10-22.
Its first implementer is the [placement trace](placement-trace.md) (AGENTEE-430).
kicad-route (KICADROUTE-99) keeps a byte-identical copy of the JSON Schema with a
sha256 parity check. The envelope is consumer-neutral and carries no placement or
routing policy.

- Schema: [`schemas/pcb-trace-envelope-1.json`](../schemas/pcb-trace-envelope-1.json) (JSON Schema 2020-12).
- Reference reader: `agent_ee.trace_envelope.validate(document)` returns a list of problems.
  It is stdlib only. It applies the schema's field rules exactly: a present field
  is checked whatever its record's status, so an `unknown` identity with a
  malformed `sha256` is invalid. It adds only cross-field rules that the schema
  does not express: every `parent` and `within` name exists, duration names are
  unique, and `sampling.bytes` does not exceed `sampling.limits.bytes`.

## Fields

All of these fields are required.

| Field | Content |
|---|---|
| `schema` | exactly `pcb-trace-envelope/1` |
| `consumer` | `id` (lowercase identifier) and `version` |
| `stage` | `id` of the traced stage or entry point |
| `identity` | `pcb`, `project`, `rules`, `experiment`, plus any further named identities. Each is `{"status": "qualified", "sha256": …}` (or an `id`), or `{"status": "unknown", "reason": …}`. |
| `hashes` | `source`, `binary`, `native_tools`: lists of named hash entries, qualified or unknown with a reason |
| `state` | `status` is `completed`, `failed` or `interrupted`. A run that did not complete needs a `reason`. |
| `generation` | `id` of the consumer's generation scheme, or unknown with a reason |
| `objective` | `id` and `version`, or unknown with a reason |
| `clock` | `source: monotonic` and a named `unit` (`ns`) |
| `durations` | rows of `name, unit, value, count, boundary (inclusive/exclusive), overlap (disjoint/nested), parent`. Nested rows list `within`. |
| `counters` | names mapped to non-negative integers (consumers use lowercase identifiers) |
| `sampling` | `limits`, `recorded`, `bytes`, `dropped`, `coverage` (complete/partial), `partial_reasons` |
| `payload` | a consumer-owned object naming its own `schema` |

A field the consumer cannot qualify is `unknown` with a reason. It is never
omitted, and it is never given a guessed value.

## Truncation and parity

`complete` coverage means nothing was dropped and there are no partial reasons.
A truncated sample set, a byte-limited sample set, or a run that did not complete
is `partial` with at least one reason. `trace_envelope.complete_parity_allowed(document)`
is true only for a valid envelope of a `completed` run with complete coverage. A
`failed` or `interrupted` run never qualifies, even if its coverage label says
`complete`. The record stays valid and can be retained. A partial trace can
support partial comparisons, but it can never qualify complete parity. A consumer
reader must also reconcile its payload with the coverage it claims. For example,
the [placement trace](placement-trace.md#replay-sample) refuses samples that the
counters do not account for.

## Version rule

- A `/1` reader ignores unknown fields at any level. New optional fields are additive.
- A `/1` reader refuses any other `schema` value, `/2` included, with `unsupported envelope version`.
- Removing a required field, changing its type, or changing a field's meaning
  requires `pcb-trace-envelope/2`. Consumer payloads version independently through
  `payload.schema`.

`tests/test_placement_trace.py` (`EnvelopeSchemaTest`) checks each rule against
both the reference reader and the published schema file: required fields, unknown
handling, truncation marking, additive fields and version refusal. A field-by-field
table checks that the reader and the schema accept and reject the same documents.
