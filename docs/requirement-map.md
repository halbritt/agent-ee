# Compiling a spec-record requirement map

`agent_ee.requirement_scope` accepts an authored
`agent-ee.requirement-map/1` alongside a verified [spec record](spec-record.md);
a typed `agent-ee.requirement-map/2` adds source-bound obligations (see
below) and is compiled through the same `requirement_map` entry points.
It compiles the declared checks into scope/3 using `capabilities.KINDS`,
`cli.validate_scope`, `capabilities.compile_coverage` and
`baseline.compile_baseline`. It adds no check kind and runs no tools or models.
The older [rung-1 requirement-record/1](requirement-scope.md) path retains its
scope/2 output.

```sh
PYTHONPATH=src python3 -m agent_ee.requirement_scope \
  fixtures/telemetry-pod/requirement-map.json \
  --spec-record fixtures/telemetry-pod/spec-record.json \
  --spec fixtures/telemetry-pod/spec.md --out NEW_DIRECTORY
```

Exit 0 means compilation succeeded. Exit 1 means a mapping was refused, with
JSON findings naming the requirement ID. Exit 2 means malformed input, a
capability/coverage validation failure, an existing output directory or an I/O
failure. Refused compilation creates no output directory.

## Map fields

Top-level fields are exactly `schema`, `spec_record_sha256`, `scope`,
`requirements` and `prose`. Duplicate JSON keys and non-finite numbers are
refused. The record hash binds the map to the exact capture, and
`spec_record.verify` recaptures the supplied spec before any mapping is used.

`scope` is an ordinary scope/3 template without `checks` or `baseline`.
It supplies design and input paths, name, scope description, mutable paths,
limitations and the existing [stages declaration](staged-campaign.md).
Input paths in the resulting scope are relative to the output directory.
The compiler reserves the `requirements-map/` input destination.

`requirements` is a list with exactly one entry per spec-record requirement
ID and, when `scope.envelope` is declared, every requirement derived from its
captured envelope. Each entry has `id` and exactly one of these dispositions:

| Disposition | Value | Result |
| --- | --- | --- |
| `checks` | Nonempty list of `{id, kind, params, stage}` | Executable baseline acceptance requirement |
| `limitation` | `{reason}` with nonempty text | `ID: NOT CHECKED: reason` in scope and run limitations |
| `bench` | `{criterion, procedure}` with nonempty text | `ID: BENCH REQUIRED: criterion; procedure: procedure` in scope and run limitations |

Kinds come from the live registry. Parameters, prerequisites, stages and
immutable dependencies go through the existing capability and baseline
validators. First declarations in map-list order determine execution order;
checks first declared in prose follow those in `requirements`. An identical
check declaration may be shared by multiple requirements. A repeated check in
one mapping or conflicting declarations for the same check ID are refused.

Only checks with executable predicates can supply acceptance coverage. An
informational `netlist` check may supply a prerequisite alongside a bound
check; it cannot cover an acceptance requirement alone. Empty mappings and
maps containing only limitations or bench obligations cannot compile into a
passing empty check set. Unknown kinds never become fallback checks.
Specialized kinds retain their own source-binding and one-requirement-per-check
rules; a map must satisfy those contracts to use them.

A declared `scope.envelope` uses the ordinary `{path, profile, dialog}`
contract and requires captured inputs. The compiler derives its `R-envelope-…`
IDs from the verified envelope and manufacturing sources. Map entries bind
those IDs to checks in the same authored execution order as other requirements.
The resulting baseline retains each derived statement, field locator, source
path and authority intent. It does not recast geometry as specification prose.
The ordinary envelope coverage validator still rejects missing obligations,
wrong field predicates, stale sources and deferred required physical checks.
No new declaration is needed in the [build plan](build-workflow.md).

The compiler does not establish that a chosen predicate covers the meaning of
a whole requirement. The owner reviews that relationship, including requirements
containing several clauses. A native connectivity check cannot establish radio
range, automotive transient survival or sensor accuracy. Those need explicit
limitations or bench obligations until suitable evidence is declared.

For the telemetry pod, `POD-AC-8` and `POD-AC-9` must use `bench`; both check and
limitation mappings for these IDs are refused.

## Typed maps: obligations and evidence methods (requirement-map/2)

`agent-ee.requirement-map/2` is the same declaration with two more
dispositions and a mandatory companion file, `agent-ee.obligations/1`. A
map/1 compiles exactly as before and carries no method binding; a map/2 binds
every mapped source span to an authored obligation kind so that a clause can
only be discharged by an appropriate kind of evidence. This implements the
[claims contract](planning/2026-10-25-recovery/contracts/claims-evidence-qualification.md)
§4. The compiler still executes nothing and assesses no evidence.

The obligations file names the spec bytes it binds:

```json
{ "schema": "agent-ee.obligations/1", "spec_sha256": "…",
  "clauses": [
    { "id": "ACQ-DELIV-2", "spans": [
        { "byte_start": 4462, "byte_end": 4700, "text_sha256": "…", "obligation": "operational" } ] },
    { "id": "ACQ-MECH-3", "spans": [
        { "byte_start": 3536, "byte_end": 3703, "text_sha256": "…", "obligation": "executable" },
        { "byte_start": 3703, "byte_end": 3852, "text_sha256": "…", "obligation": "judgment" } ] } ] }
```

Every captured requirement and prose clause needs an entry
(`obligation_missing`); unknown ids, duplicate entries and a `spec_sha256`
that is not the spec refuse (`obligation_unknown_id`, `duplicate_mapping`,
`obligation_spec`). Spans follow the clause-partition rules: ordered,
non-overlapping, inside the clause, no uncovered non-blank bytes, no split
code point (`obligation_partition`), and each `text_sha256` must equal the
hash of those spec bytes (`obligation_text_drift`), so an edited clause
invalidates its obligation. Kinds are `executable`, `operational`,
`judgment`, `physical` and `context`; `context` is allowed only for prose
(`obligation_kind`). `POD-AC-8`/`POD-AC-9` must be `physical`. Derived
envelope requirements are executable by construction and are not listed.

| Obligation | Admissible dispositions | Receipt and scope result |
| --- | --- | --- |
| `executable` | `checks`, `limitation` | checks become baseline acceptance checks as in map/1 |
| `operational` | `operational {criterion}`, `limitation` | `ID: OPERATIONAL PENDING: criterion; …` limitation; no check; the build consumer assesses it from sealed receipts |
| `judgment` | `judgment {record}`, `limitation` | `ID: JUDGMENT PENDING: record path; …` limitation; the build consumer admits the record |
| `physical` | `bench`, `limitation` | `ID: BENCH REQUIRED: …` as in map/1 |
| `context` | `context` | prose context as in map/1 |

A map entry, whole or per `spans[]` mapping, must coincide exactly with one
obligation span: a map may neither merge two obligation spans nor split one
(`span_alignment`). A disposition the span's kind does not admit refuses
`incompatible_evidence_method`. The only supported operational criterion is
`public_build_and_resume`; any other string, including prose that asserts
the build resumed, refuses `unsupported_criterion`, because prose is source
context and never a predicate. A judgment `record` is a safe relative path.
Typed dispositions in a map/1 remain the ordinary exactly-one refusal.

A map/2 without obligations refuses `obligations_required`; a map/1 with
obligations refuses `obligations_unexpected`. `compile_map`,
`compile_map_file` and `write_map_outputs` take the obligations as an
optional keyword (`obligations_raw`, `obligations_path`); the bytes join the
`requirements-map/` provenance as `obligations.json`, so the baseline
identity covers them while the compiled scope is byte-identical to the map/1
compile of the same dispositions.

The receipt becomes `agent-ee.requirement-map-receipt/2` with
`method_binding: "typed"`, `obligations_sha256`, and per source row
`obligation`, `obligation_text_sha256` and `bound_ids` (check ids for
`checks`, otherwise empty). A map/1 receipt is unchanged.

`requirement_map.coverage_view(receipt, result_rows)` turns a receipt and
ordinary result/1 check rows into `{clause id: {status, spans: [{id,
byte_start, byte_end, obligation, mode, bound_ids, executed: [{id, kind,
class, execution, outcome}], status, reason}]}}`. The method `class`
(`native`, `topology`, `model`, `measurement`) is derived from the executed
row's `kind` through `requirement_map.KIND_CLASS`; a kind outside that table
is `unclassified` and provides no coverage (`unknown`). A `checks` span is
`pass` only when every bound id executed `ok` with `pass`/`info`, `failed`
on a `defect`, `pending` when a row is missing and `unknown` otherwise. A
`limitation` span is `failed` (`NOT CHECKED`), `bench`, `operational` and
`judgment` spans are `pending` because this view cannot assess physical,
operational or judgment evidence; the build consumer supplies that bound
assessment. Prose context is `not_applicable`. A clause's status is the
worst of its spans. A map/1 receipt reports every clause `unknown` with
reason `legacy map/1: method binding unqualified`; nothing is promoted.

## Prose accounting

Every captured prose ID needs an entry in `prose`. A whole paragraph can use
`checks`, `limitation` or `bench`, or `{id, context: {reason}}` for non-requirement
text. A paragraph is never promoted to a requirement by its heading or by
keywords. Context entries remain in the receipt and supply no acceptance credit.

For a paragraph containing separate obligations, the owner can instead declare
`{id, spans: [...]}`. Each span is exactly
`{byte_start, byte_end, mapping}`, where `mapping` has its own unique `id` and
one of the dispositions above, including `context`. Offsets are absolute UTF-8
byte offsets into the verified spec. Spans must be ordered, non-overlapping,
inside their paragraph, nonempty, and on UTF-8 boundaries. Only whitespace may
lie between spans or at the paragraph's ends. Uncovered prose bytes are refused.
Each span receipt retains its parent paragraph ID, exact locator and quote hash.
No sentence splitter chooses the boundaries.

## Compound requirement clauses

A specification item can contain several independently checked clauses. Its
`requirements` entry may use the same `{id, spans: [...]}` form as prose.
Each child has its own unique ID and `checks`, `limitation` or `bench`
disposition. Requirement clauses cannot use `context`. This is an additive
input form in `agent-ee.requirement-map/1`; the compiler identifies its
implementation as `agent-ee.requirement-scope/3`.

For example, separate a native distance requirement from a following routed
return-path requirement using their exact byte boundaries:

```json
{"id": "ACQ-MECH-3", "spans": [
  {"byte_start": 3400, "byte_end": 3500,
   "mapping": {"id": "ACQ-MECH-3-DISTANCES", "checks": [
     {"id": "locality", "kind": "placement", "stage": "verification",
      "params": {"criterion": "electrical-locality/1", "drc_check": "drc",
                 "locality": "electrical-locality.json"}}]}},
  {"byte_start": 3500, "byte_end": 3600,
   "mapping": {"id": "ACQ-MECH-3-RETURNS", "limitation":
     {"reason": "Routed loop and return-path verification remains unresolved."}}}
]}
```

The offsets above are illustrative; derive them from the actual verified spec.
All non-whitespace bytes of the original item must be covered exactly once,
with ordered, nonempty spans on UTF-8 boundaries. A child ID must not collide
with any original requirement, prose or envelope ID, including its parent, or
with another child. Nested partitions are refused. Envelope-derived field
obligations cannot be partitioned or relabeled; their original authority and
coverage checks still apply. Every child of the physical-only `POD-AC-8` and
`POD-AC-9` requirements must remain a bench obligation.

The receipt contains each clause's `parent_id`, exact source locator and text
hash. Executable baseline statements use those exact source bytes. Requirements
retain specification order, while checks execute in authored mapping order.
A passing clause supplies no acceptance credit to a sibling: an unresolved
clause remains `NOT CHECKED` and blocks the normal manufacturing package stage.
Physical obligations remain explicit and unperformed. Partitioning accounts
for existing text; it neither revises a requirement nor establishes that a
selected check covers the meaning of that text.

## Inputs, outputs and evidence

Without `--inputs`, the compiler can validate checks that need only declared
native scaffold paths, such as ERC and DRC. This prepares a scope before the
scaffold exists; it supplies no native execution evidence.

Use `--inputs CAPTURED_ROOT` for checks that consume captured contracts,
catalogs, envelopes or policies. The root contains candidate-relative paths.
The existing candidate input selector inventories it; the compiler validates
hashes and creates a temporary copy with its own provenance for the ordinary
baseline readers. It does not modify the supplied root. A later candidate
capture must reproduce the receipt's baseline ID using the same immutable
inputs. The scaffold and other input producers remain separate steps.

The new output directory contains:

| Path | Content |
| --- | --- |
| `scope.json` | Compiled scope/3 with executable acceptance requirements and explicit limitations |
| `receipt.json` | `agent-ee.requirement-map-receipt/1`: compiler identity, source/scope/baseline/coverage hashes and every disposition |
| `requirements-map/spec.md` | Exact spec bytes |
| `requirements-map/spec-record.json` | Exact verified capture |
| `requirements-map/requirement-map.json` | Exact authored map bytes |

Every receipt entry includes its ID, locator, text hash, mode and declared
mapping. Executable mappings also include their capability descriptors and
predicates. The map, spec and record are immutable baseline sources. A changed
map changes the baseline even if its executable checks remain identical.
Source changes between compilation and writing outputs are refused.

Limitations and bench obligations are excluded from `checks` and executable
baseline requirements. They are retained as ID-bearing limitations in the
scope and the ordinary run record, so passing file checks cannot be read as
passing those obligations. There is no synthetic passing check for them.

## Telemetry pod accounting fixture

[The example map](../fixtures/telemetry-pod/requirement-map.json) accounts for
all 38 revision-2 requirements and all 10 prose entries. It declares ERC and
DRC for `POD-AC-3`, the two required bench obligations, and explicit limitations
where pod-specific captured inputs or physical evidence are still needed.
It is an accounting example for review, not an owner approval or a completed
pod design. Its verification-stage subject expects a schematic instance `U1`;
a real scaffold producer must declare its actual subject inventory.

`POD-FN-P2` and `POD-IF-SENSOR-SUPPLY` both refer to the pod-owned 5 V rail in
revision 2. They use ordinary mappings; there is no contradiction disposition
or rationale-only exception.

The interface limitations explicitly retain regulator accuracy, connector-referenced
ratiometry, automotive transient and reverse-polarity survival, installed BLE
range, CAN timing and firmware behavior. Native connectivity or selected-part
checks do not discharge those physical or functional obligations. No captured
vendor regulator model supplies partial SPICE evidence in this fixture.

The final run's `result.json` carries every compiled limitation verbatim; verified
campaign history links that completion record. The [evidence index](evidence-index.md)
repeats the same list, and verification refuses a changed or removed prefix.
To capture an index criteria map through this compiler, include the JSON file in
the template's `inputs` and `requirements`; it becomes an immutable baseline
source without replacing the compiler-owned baseline. A passing native check
does not mark a declared limitation or built-board bench obligation as met.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest \
  tests.test_spec_record tests.test_requirement_scope tests.test_requirement_map \
  tests.test_capabilities tests.test_tree_hygiene -v
```

Tests remove every required mapping in turn, exercise prose spans and refusal
boundaries, and validate a captured interface contract through the existing
capability code. A native RC-fixture run reproduces the compiled baseline,
passes ERC/DRC and retains every limitation, including both bench obligations,
in `result.json`. It qualifies the compiler integration, not pod hardware.
