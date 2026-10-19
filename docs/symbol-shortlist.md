# Library-restricted symbol shortlist

`agent_ee.symbol_shortlist` (AGENTEE-37) turns requirement text into a closed
list of `Library:Symbol` entries. It picks in two stages from the captured
KiCad symbol libraries. A model proposes names. A deterministic existence gate
over the captured library index decides which names survive. The sealed
shortlist stays pending until the owner approves its exact hash. Only then does
`approved_symbols` hand the list to generation.

The shortlist does not check stock, price, datasheets, pins or footprints.
[Symbol context](symbol-context.md) and [part selection](part-selection.md)
own those questions. A shortlisted name is a candidate for those stages, not
a qualified part.

## Request

A request has schema `agent-ee.symbol-shortlist-request/1` and exactly these
fields:

| Field | Content |
| --- | --- |
| `requirements` | Nonempty list of `{id, text}`. IDs are stable ASCII names and unique |
| `libraries` | Captured symbol libraries `{name, path, sha256}`, validated as in [symbol context](symbol-context.md) |
| `always_libraries` | Declared library names that are always selected, such as a power library; may be empty |
| `chunk_budget_chars` | Integer from 256 to 500000: the most characters of index lines in one call |

```json
{
  "schema": "agent-ee.symbol-shortlist-request/1",
  "requirements": [{"id": "R-divider", "text": "Divide the 5 V bus to 2.5 V for the ADC."}],
  "libraries": [
    {"name": "Device", "path": "symbols/Device.kicad_sym", "sha256": "<64 hex>"},
    {"name": "power", "path": "symbols/power.kicad_sym", "sha256": "<64 hex>"}
  ],
  "always_libraries": ["power"],
  "chunk_budget_chars": 20000
}
```

## Library index

`build_index` reads each declared capture after the shared path and hash
checks (`footprint_library.captured_symbol_libraries`), parsing it once with
the shared S-expression reader. The index (`agent-ee.symbol-library-index/1`,
sealed with `canonical_sha256`) lists every symbol name per library with its
`Description` (or `ki_description`) and `ki_keywords`. A derived symbol
inherits these through its `extends` chain. KiCad's `(property private "KEY"
"VALUE")` form is read as key and value. A changed hash, a missing parent, an
`extends` cycle, a repeated listed property, or a name containing `:` or a
control character refuses the whole index. No ambient KiCad library path is
consulted.

The index keeps its property-only reader after AGENTEE-214's context fix:
it checks duplicate names only for those three indexed fields and resolves no
pins or graphics. Context rejects duplicate names for every property and also
requires the shared electrical inventory. Replacing the index reader with context
would therefore change its refusal behavior and sealed index results.

## Stages

1. **Libraries.** The prompt lists every captured library with its symbol
   count. The answer is `agent-ee.symbol-library-pick/1`:
   `{schema, libraries: [{name, reason}]}`. Names absent from the index are
   dropped as `library_not_in_index`. The selected set is the named libraries
   plus `always_libraries`, in index order.
2. **Symbols.** One line per symbol of the selected libraries,
   `Library:Symbol | description | keywords: ...`, is packed in index order
   into chunks of at most `chunk_budget_chars` characters. Each chunk is a
   separate call. The answer is `agent-ee.symbol-pick/1`:
   `{schema, symbols: [{lib_id, requirement_ids, reason}]}`. The
   `requirement_ids` must be request IDs.

The concatenated chunks must equal the selected index entries, in order. The
shortlist records `coverage` (entry counts and hashes before and after
chunking) and, per chunk, its first and last entry, size and hashes of its
IDs and listing. An entry longer than the budget, or a library list longer
than the budget, is refused before any call. An entry is never truncated or
skipped.

Each returned symbol passes the existence gate. It is kept only if it is a
`Library:Symbol` name of a selected library present in the index. Otherwise
it is recorded in `dropped` with the model's reason and one code:

| Code | Meaning |
| --- | --- |
| `not_a_lib_id` | Not exactly one `Library:Symbol` separator |
| `library_not_in_index` | The library is not captured |
| `library_not_selected` | The library is captured but was not selected in stage 1 |
| `symbol_not_in_library` | The library has no symbol of that name |

Repeated picks of one symbol merge their requirement IDs; each pick stays in
`picks`. Every kept entry records its library path and hash and its indexed
description.

## Model calls

Each call is one fresh launch through the reviewer harness seam
(`reviewers.dispatch`). The definition is one explicit subscription route,
`{harness, model, effort, timeout_s}`, validated like a
[review profile](reviewer-profiles.md) entry. It runs in `fixed_packet` tool
mode: no tools are granted, and the request and listing are in the prompt.
The executor sandbox gives the harness network access for the provider call
only. No provider fallback, default model or default list exists.

Calls go through `review_run.structured_call` with zero resubmissions. Each
retains its prompt, schema, response and execution record under
`review-attempts/<label>/0/` and `review-executions/<label>.json`. Labels are
`shortlist-libraries` and `shortlist-symbols-NNNN`.

The selection fails as a whole when any call:

- reports a non-`ok` execution (`failed`, `unavailable`, `timeout` and so on);
- returns no structured output;
- returns output that fails its validator (missing or extra fields, wrong
  schema, empty names or reasons, unknown or repeated requirement IDs).

Later chunks are not called, and a failed call is not repeated. The shortlist
then has `outcome: unknown`, empty `symbols`, `libraries` and `dropped`, an
`error` naming the call label and reason, and `decision: {status:
not_approvable}`. A selection where no symbol survives the gate is also
`unknown` and not approvable.

## Shortlist and approval

`shortlist.json` (`agent-ee.symbol-shortlist/1`) binds the request hash, the
index hash, the definition and every call record hash, and is sealed with
`canonical_sha256`. A successful selection records `decision: {status:
pending_owner_approval}`. The tool never writes an approval.

Generation proceeds only through `approved_symbols(run, captures, approval)`.
It replays verification (below), then requires an owner record:

```json
{
  "schema": "agent-ee.symbol-shortlist-approval/1",
  "shortlist_sha256": "<the shortlist canonical_sha256>",
  "owner": "<owner identity>",
  "disposition": "approved",
  "reference": "<where the owner approved>"
}
```

A different hash, a `rejected` or other disposition, unknown fields or an
unapprovable shortlist refuse. As with
[violation waivers](violation-waivers.md), agents must not manufacture this
record; the tests use synthetic approvals to exercise the format only.

## Offline verification

`verify(run, captures)` needs no model. It rebuilds the index from the
captures and compares it with the retained `inputs/index.json` and the
shortlist binding. It checks the shortlist seal, each call record hash and
the zero-resubmission declaration. It checks that every retained prompt equals
the prompt rebuilt from the captured listing. It then revalidates the retained
responses and replays the chunk plan and existence gate. The libraries,
chunks, coverage, symbols and drops must equal the shortlist. A failed
selection must end at its failed call with an empty, unapprovable shortlist.
Any difference raises `ShortlistError`.

## Command line

```sh
PYTHONPATH=src python3 -m agent_ee.symbol_shortlist select request.json \
  --captures captured --definition route.json --out NEW_DIRECTORY
PYTHONPATH=src python3 -m agent_ee.symbol_shortlist verify NEW_DIRECTORY --captures captured
PYTHONPATH=src python3 -m agent_ee.symbol_shortlist verify NEW_DIRECTORY --captures captured \
  --approval owner/shortlist-approval.json
```

`select` exits 0 for a pending shortlist with symbols, 1 for a completed
selection with no surviving symbol, and 2 for a failed or malformed call or
invalid input. Request, index and budget errors write nothing. `verify` exits
0 when the replay matches (and, with `--approval`, the approval admits the
list), 1 when the approval refuses, and 2 when verification fails.

## Tests and limits

`tests.test_symbol_shortlist` covers the following:

- index reuse with one parse per capture, inherited and private properties;
- each drop code, and that every kept name exists in the index;
- chunking over a 62-entry library at a 256-character budget, and over the
  installed stock `Device` and `power` libraries;
- refusal of an oversized entry before any call;
- failed, unavailable, timed-out and malformed responses in either stage;
- the approval gate, and tamper detection on the shortlist, responses,
  prompts, call records and captures;
- a recorded provider console through the real Claude adapter.

No test makes a live model call. Relevance of the picks is model judgment and
is not graded here. Existence is deterministic.

The two-stage mechanism follows SchGen's `load_related_symbols`. That
reference is mechanism-only: no SchGen code, prompt text or data was used.
