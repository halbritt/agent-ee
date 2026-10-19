# Compiling a spec-record requirement map

`agent_ee.requirement_scope` accepts an owner-authored
`agent-ee.requirement-map/1` alongside a verified [spec record](spec-record.md).
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
ID. Each entry has `id` and exactly one of these dispositions:

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

The compiler does not establish that a chosen predicate covers the meaning of
a whole requirement. The owner reviews that relationship, including requirements
containing several clauses. A native connectivity check cannot establish radio
range, automotive transient survival or sensor accuracy. Those need explicit
limitations or bench obligations until suitable evidence is declared.

For the telemetry pod, `POD-AC-8` and `POD-AC-9` must use `bench`; both check and
limitation mappings for these IDs are refused.

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
| `requirements-map/requirement-map.json` | Exact owner-authored map bytes |

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
