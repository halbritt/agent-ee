# Manual physical intent compiler (AGENTEE-388 S1)

`agent_ee.physical_intent` compiles a manually authored
`agent-ee.physical-intent/1` document against one sealed review run into the
existing executable contracts. It is the S1 slice of the
[routability design](research/2026-10-21-routability-design.md#48-semantic-architect-and-deterministic-intent-compilation).

**Status: manual, opt-in and isolated.** It is a Python API only. No CLI,
profile, campaign step, placement adapter or routing consumer calls it, and
`tests.test_physical_intent` fails if any other module under `src/agent_ee/`
names it. Runtime activation and deployment remain gated by KICADROUTE-91. The
kicad-route pin is unchanged. There is no `architect` mode and no model call:
`mode` is `manual` or `disabled`, and `disabled` returns nothing and publishes
nothing, so existing manually captured contracts are untouched.

```python
from agent_ee.physical_intent import compile_and_publish, verify_receipt
compile_and_publish(run_dir, intent, out_dir)   # IntentRefused: nothing published
verify_receipt(out_dir, run_dir, intent)        # recompiles; byte-exact or refused
```

## Inputs

The run must be a captured review run with an immutable baseline and a
[sealed evidence](check-reference.md) record (`work/verification/evidence.json`).
The compiler verifies the snapshot (`verify_snapshot`), the baseline and the seal
(`verify_evidence(required=True)`) before reading anything else.

The intent's `sources` bind, by SHA-256, the run's `candidate_id`, its evidence
seal, the native board, the `pin_map` check's native XML netlist, its
`datasheet_facts` report (`facts.json`) and its `pin-map.json`. `pin_map_check`
names the run's [`pin_map`](datasheet-facts.md) check; its `facts_check` and
`netlist_check` parameters select the other two. Optional `records` maps
captured candidate input paths to their hashes. They become
`source_records` for locality provenance. Any difference is a stale source.

## Terminal join

Each `terminals` entry names `id`, `reference`, `pin`, the native `pad_uuid`,
the claimed `function`, the `fact` id, the expected `net`, the topology `class`
and a baseline `requirement`. The compiler consumes, and does not repeat, the
existing pin evidence:

1. the fact must be `verified` in the run's `facts.json`, of kind
   `pin_function`, and its `value` must equal the claimed function;
2. `pin-map.json` must hold exactly one comparison for that reference and fact.
   That is the engineer-declared applicability of the `pin_map` check. Its pin
   and the fact's pin must equal the proposed pin, and it must match the native
   symbol pin name. The `pin_map` check itself must be `ok`/`pass`;
3. the native board must have exactly one pad with that number on that
   footprint, and the declared UUID must be exactly that pad;
4. that pad's native net must equal the netlist membership of the pin.

On the TJA1051 fixture, U1 pin 6 is CANL and pin 7 is CANH. A proposal selecting
pin 7 as CANL is refused with `verified CANL fact binds U1 pin 6, not proposed
pin 7` before anything is written.

Resolved terminals compile to a [`topology-rules/1`](topology-rules.md)
document: one `pin_net` rule per terminal, a class declaration per native
`lib:symbol` with the function-to-pin map. The document passes
`topology_rules.validate_rules` and must evaluate `pass` against the strict
native inventory. Any violation or unknown is `failed topology`. Requirements
must exist in the run baseline; the compiler adds none.

## Locality, blocks and moves

`locality` holds [`electrical-locality/1`](placer.md#pad-level-electrical-locality-agentee-322) obligations
as authored. They are emitted unchanged except for owner provenance (below).
locality/1 has no fact or function field, so the compiler adds no guessed IN/GND
or role semantics to a supply/return pair. Every endpoint must carry its native pad `uuid`. The UUID,
pad number and net must match the native board. Each pair is qualified with
`locality_provenance.classify` on the captured records:

- an integrity failure (record hash mismatch) is a stale source;
- a pair declaring `owner` must first pass `classify`: a captured, hash-verified
  decision record containing the quote, from an owner that is not an agent label.
  The cited decision must then be exactly one record line of the form
  `<locator> (<owner identity>): <text containing the quote>`. The
  `decision_reference.owner` must equal the stated identity, so `Mallory`
  citing `D-1 (owner of record): ...` is refused. Any failure is
  `owner authority not established`;
- that decision text must state exactly one length in `nm` or `mm`, and it must
  equal the declared `max_distance_nm`. A different value (3 mm cited, 999 mm
  declared), no stated value, two values, a length in another unit (`mil`, `in`,
  `um`, `millimetres`, `MM` and so on) or no declared `max_distance_nm` is a
  `numeric mismatch`;
- every numeral in the decision text is allowlisted whole. The text is split at
  whitespace, and each token holding a digit (ASCII or any other Unicode digit
  or numeral) must be, after at most one trailing sentence mark (`.`, `,`, `;`,
  `:`), either an identifier such as `U1`, `C128`, `R-0` or `D-1` (ASCII letters
  first, then letters, digits and single hyphens), or a complete unsigned length:
  a plain decimal (`3`, `0.5`), at most one ASCII space, then exactly `nm` or
  `mm` (`3 mm` and `3mm` both bind 3 mm). Every other numeral refuses as a
  `numeric mismatch`, with no suffix extracted and no length skipped: a fraction
  (`1/3 mm`), an apostrophe, quote, comma, nbsp or thin-space grouping
  (`1’234 mm`, `1,003 mm`, `1 003 mm`), a sign (`-3 mm`, `− 3 mm`, `+3 mm`) or
  range (`2-3 mm`), an exponent or `^`, leading zeros, a bare decimal point,
  non-ASCII digits, a bare number (`3 more pads`, `pin 4`), a length in another
  unit (`3 mm and 999 yd`, `999 MM`) or a length word attached to no resolvable
  numeral (`x3 mm`, `mm3`). Two different stated lengths are ambiguous and
  refuse;
- a pair declaring `datasheet` must classify `hard`: the numeric derivation equals
  the declared `max_distance_nm`. Otherwise it is a `numeric mismatch`.

**Owner maxima never enforce.** Agents run as the owner's Unix user, so no
in-process check can authenticate an owner or manufacturer author until
AGENTEE-348 provides a signing boundary (coordinator ruling, consistent with the
AGENTEE-342 trust-boundary ruling). A bound owner maximum is therefore published
as `authority_unavailable`. In the emitted contract, its provenance keeps
`class: owner` and the declared limit. It gains `authority:
authority_unavailable` and `authority_reason`, and the cited decision moves from
`decision_reference` to `unauthenticated_decision_reference`. As a result,
`locality_provenance.classify`, and every downstream grader, treat it as
advisory. The receipt's `locality_qualification` entry records
`enforcement: authority_unavailable`, the reason, the stated `owner` and
`stated_max_distance_nm`. A manufacturer record grades `feasibility` (a floor),
never a hard maximum. The compiler refuses any emitted non-datasheet pair that
would still classify `hard`.

Requested enforcement never raises provenance. Other classes are compiled and
reported as `advisory` or `feasibility` in `locality_qualification`.

`blocks` compile to `block-allocation/1` and pass the placement allocation
validator. A reference in two blocks is `conflicting membership`. `moves`
(`translate`, `rotate`, `flip`) on a native locked footprint are `conflicting
fixed move`. Allowed moves are recorded in the receipt only; no existing
executable consumer reads them in this slice.

## Unsupported and diagnostic assertions

`obligations` of kind `kelvin` or `current_loop` name resolved terminals,
optional join pad UUIDs, forbidden shared terminal pairs and an optional
locality `proxy`. No owned routed-graph predicate exists for them. They are
always emitted as `status: unsupported`. A required one sets
`semantic_completion: unsupported` and is listed in `required_unsupported`. A
locality proxy is noted as not electrical compliance: a nearest-pad distance
proves no current path, loop area or Kelvin connection. Stale join UUIDs and
unknown terminals refuse.

`diagnostics` of kind `access` or `cut` are carried with
`authority: diagnostic` and never mapped to a contract. Their status is
`unknown` or `observed`. A status of `proof` is refused, because a cut
inequality is not a routability proof.

## Output and publication

Compilation is pure and deterministic: no timestamps, no host paths. The
package contains `contracts/topology-rules.json`, and when declared,
`contracts/electrical-locality.json` and `contracts/block-allocation.json`. It
also contains `receipt.json` (`agent-ee.physical-intent-receipt/1`), which
records:

- the bound sources, the intent identity and the compiler file's SHA-256;
- the identity of each artifact;
- a map from assertion ID to JSON pointer;
- per-terminal fact, page and native pin evidence;
- locality qualification, allowed moves, unsupported obligations and diagnostics;
- limitations;
- a `package_id` over all of the above.

Assertion IDs share one namespace: terminal IDs, locality obligation IDs,
generated `block:<id>` keys, unsupported obligation IDs and diagnostic IDs. A
collision, such as a locality obligation named `u1-canl` beside the CANL
terminal, or one named `block:can` beside block `can`, is refused as `duplicate
assertion id`. No receipt mapping is ever overwritten. The compiler works on a
private JSON snapshot of the intent, so editing the caller's intent after
compilation cannot change a compiled artifact without changing its digest.

Any refusal raises `IntentRefused` before any write. Publication writes the
complete package to a staging directory beside the destination and renames it
into place with `RENAME_NOREPLACE`. An existing destination is refused, and a
failed write removes the staging directory. `verify_receipt` recompiles from
current sources and requires byte equality and no extra files.

Pure compilation of the fixture takes well under the provisional 10-second
budget. The test asserts the bound. The budget excludes native netlist export
and PDF extraction, which the review run has already done.

## Tests and limits

`tests/test_physical_intent.py` captures the unchanged
`fixtures/datasheet-pins/control.scope.json` and `swapped.scope.json`. It also
captures two authored board edits of the control fixture: one adds a decoupling
footprint C1 for locality, and one adds a locked U1 with a duplicated pad 7. It
covers:

- the positive deterministic package and its digest binding;
- the pin-7-as-CANL rejection;
- unverified and defective pin evidence;
- stale sources, pad UUIDs and tampered runs;
- duplicate pins and pad numbers;
- failed topology and invented requirements;
- conflicting membership and fixed moves;
- owner spoofing, owner identity binding (including the `Mallory` review
  probe), owner value binding (3 mm against 999 mm, a missing value, two
  values, an unresolvable unit; signed, grouped, fractional and second
  unresolved lengths, and a separator by unit sweep, against the matching 3 mm,
  3mm, 0.5 mm and 1.3 mm controls) and the non-hard `authority_unavailable` owner
  output;
- datasheet numeric mismatch and locality endpoint binding;
- assertion ID collisions (`u1-canl`, `block:can`, a diagnostic and an
  unsupported obligation) and the input snapshot;
- Kelvin and current-loop visibility, diagnostics, manual and disabled modes,
  isolation and failed publication.

Not in this slice:

- the captured pod 17-block/12-locality-obligation equivalence target;
- the TPS25982 invalid-pin controls from the Rev5 review;
- the S2 architect producer;
- any campaign, profile or placement-adapter binding.

Citation verification remains textual (see [datasheet facts](datasheet-facts.md)).
