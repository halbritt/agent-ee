# Manual physical intent compiler (AGENTEE-388 S1)

`agent_ee.physical_intent` compiles a manually authored
`agent-ee.physical-intent/1` document against one sealed review run into the
existing executable contracts. It is the S1 slice of the
[routability design](research/2026-10-21-routability-design.md#48-semantic-architect-and-deterministic-intent-compilation).

**Status: manual, opt-in and isolated.** It is a Python API only. No CLI,
profile, campaign step, placement adapter or routing consumer calls it, and
`tests.test_physical_intent` fails if any module under `src/agent_ee/` other
than the equally unwired [S2 architect producer](physical-architect.md) names it. Runtime activation and deployment remain gated by KICADROUTE-91. The
kicad-route pin is unchanged. The compiler has no `architect` mode and makes no
model call: `mode` is `manual` or `disabled`, and `disabled` returns nothing and
publishes nothing, so existing manually captured contracts are untouched. The S2
producer composes an intent from a model proposal and hands it to this compiler
unchanged.

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

## Native admission

The bound board is admitted natively before the compiler reads any board fact
(review ee388f-native-forms H1). The existing `placement_probe` runs
`pcbnew.LoadBoard` on the captured board inside the networkless tool sandbox.
If pcbnew cannot load the board, the compiler refuses with `native admission:
pcbnew does not load the bound board`. The probe must also report the bound
board SHA-256. The board text is then read with the quote-preserving
`schematic_emit.parse_quoted`, which the shared `symbol_library.parse_sexpr`
checks. Every node must be headed by a bare keyword, so `("locked" no)` and
`("uuid" ...)` refuse. Every `uuid` node must hold exactly one atom, so a nested
or second UUID refuses. A pad with two `uuid` nodes also refuses. The text
parse and the pcbnew objects must agree on every footprint reference, UUID,
library ID, lock state and pad number, UUID and net. Otherwise the compiler
refuses with `native admission: pcbnew and the board text disagree`. For
example, pcbnew invents an identity for a pad that has no `uuid`. The rows are
compared as multisets, never ordered, so a missing pad UUID beside a present one
with the same pad number is a typed refusal, not a comparison error, and no
identity is fabricated for it (ee388j). The compiler uses the native values. `verify_receipt` recompiles, so a package bound to a
board that pcbnew cannot load also fails verification.

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
`lib:symbol` with the function-to-pin map. A physical pin has exactly one
function. A function may name several verified physical pins of one symbol,
such as two GND pins, each with its own fact and pad UUID. Each of those pins
then gets its own class terminal, named `function@pin` (`GND@2`, `GND@5`); a
function bound to one pin keeps its plain name. The alias depends only on the
bound pins, never on authoring order, and each receipt terminal records its
`class_terminal`. A pin bound to two functions (U1 and U2 of one symbol citing
pin 1 as GND and as VCC) is a `contradictory pin identity`, and an alias that
would equal another bound function name is refused. The document passes
`topology_rules.validate_rules` and must evaluate `pass` against the strict
native inventory. Any violation or unknown is `failed topology`. Requirements
must exist in the run baseline; the compiler adds none.

## Locality, blocks and moves

`locality` holds [`electrical-locality/1`](placer.md#pad-level-electrical-locality-agentee-322) obligations
as authored. A declaration that is not valid `electrical-locality/1` (for example, supply and
return naming different capacitors) refuses with `IntentRefused`, never a bare `ValueError`. They are emitted unchanged except for owner provenance (below).
locality/1 has no fact or function field, so the compiler adds no guessed IN/GND
or role semantics to a supply/return pair. Every endpoint must carry its native pad `uuid`. The UUID,
pad number and net must match the native board.

Every emitted role must be executable by the real consumers. Each pair must
declare `max_distance_nm` and a `citation`; the compiler invents no bound, so a
pair without them is `incomplete locality`. Both endpoints must declare the same
net, and the metric must be `pad-centre-to-centre`, the only metric
`electrical_locality.grade` measures. The finished document must then construct
the actual `electrical_locality.PlacementIndex`. Before this check, a return pair
without a bound published, passed `verify_receipt` and then crashed
`PlacementIndex` with `KeyError` (verification 510 R1). Each pair is qualified with
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
  the declared `max_distance_nm`. Otherwise it is a `numeric mismatch`. The
  compiler then binds the derivation to one captured record line. That line
  must hold the derivation's `locator` as a whole token and its quote exactly
  once, on token boundaries. Every numeral on the line must pass the same
  complete-lexeme rule as owner decisions and resolve to one length equal to
  `value_nm`. The shared classifier accepts any matching token. Before this
  check, `within -3 mm`, `within 1,003 mm`, `within 1/3 mm`, `within 1’234 mm`,
  `3 mm and 999 yd` and `3 mm and 4 mm` all published hard suffix maxima
  (review ee388e-510-r1r5 H2). They now refuse, both before output and at
  `verify_receipt`;
- only `derivation.kind: numeric` compiles. Its verified captured quote must
  state the declared maximum. A `layout` derivation is refused as
  `unverifiable derivation kind 'layout'`. Its `distance_units`,
  `scale_nm_per_unit` and `scale_reference` are authored values, not captured
  evidence. Before this refusal, a captured `within 3 mm` quote with an invented
  1 nm-per-unit scale compiled to a hard 1 nm datasheet maximum (verification 510
  R2). The shared `locality_provenance.classify` still grades such a manually
  authored layout derivation `hard`. That existing contract is outside this
  compiler, and changing it would alter existing locality verdicts.

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

`blocks` compile to `block-allocation/1`. A reference in two blocks is
`conflicting membership`. The optional `residual` list is the allocation's declared residual
set: footprints placed outside every block, such as fixed anchors. It needs nonempty `blocks`.
A residual reference that is also a block member is `conflicting membership`, and one that is
not on the native board refuses. The receipt maps it to the assertion `allocation:residual`. The allocation is then admitted by the actual
`blocks.derive`. It runs on the native netlist inventory and sheet names, and on
a capture of the digest-bound native board (footprint reference, UUID, library
ID, lock state and pad nets). The footprint UUID is the footprint's own single
`uuid`, never a pad UUID. A footprint with no single UUID, or one that shares a
UUID with another footprint, refuses as `missing or ambiguous footprint
identity`, the same refusal `derive` gives on a pcbnew capture (review
ee388e-510-r1r5 H3). Lock state follows pcbnew: the bare `locked` footprint
atom, `(locked)`, `(locked yes)` and `(locked true)` are locked, and `(locked
no)` and `(locked false)` are not. Any other lock value (quoted, nested or
unknown), a second lock declaration (pcbnew lets the last one win) or an
unrecognized or quoted footprint atom refuses; no unrecognized form defaults to unlocked (review
ee388e-510-r1r5 H1). `derive` refuses a locked member, with or without
a `moves` entry, so the locked U1 of the defect board is refused (verification
510 R3). It also refuses a footprint that is not an on-board native component,
such as the netted, board-only C1 of the locality board, and a footprint that
differs from the netlist. A child-sheet component left in neither a block nor
the residual set is refused too. The receipt's `allocation_derivation` records
the derived blocks, residual set and allocation SHA-256. These equal a
`blocks.derive` of the published file on a pcbnew placement capture. `moves`
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
- per-terminal fact, page, native pin evidence and class terminal;
- the `blocks.derive` result for the allocation, when declared;
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
current sources and requires byte equality and no extra files. Every refusal
above therefore also refuses `verify_receipt` for a package that a laxer earlier
compiler published.

Compilation takes well under the provisional 10-second budget, including the
sandboxed pcbnew board load of native admission. The tests assert the bound for the
fixture and the captured pod. The budget excludes native netlist export and PDF
extraction, which the review run has already done.

## Captured pod equivalence

`PodEquivalenceTests` compiles the captured telemetry pod intent back to the
reviewed iteration-7 contracts: 17 blocks and 12 locality obligations (26 pairs).
Every input is read from the pinned objects of a local fome-mc store
(`19522e37de01103a93fe171a09fe624d8aba6bad`, `AGENT_EE_FOME_MC_CHECKOUT`) into a
temporary run, after a SHA-256 check. No pod file is copied into this repository.
The run captures the microSD source schematic
(`storage-microsd/project/`) and the iteration-7 seed board. It exports the native
netlist and checks two authored GND facts against the captured TI TPS7B4253-Q1
datasheet. The intent is the reviewed `inputs/allocation.json` (blocks and
residual) and the pad obligations of `inputs/policy.json`, with each endpoint bound
to its native pad UUID. It also declares the GND pins 3 and 6 of U6 and U12.

The test establishes the following:

- the emitted allocation equals the reviewed document. The actual `blocks.derive` in
  the receipt reproduces the reviewed `inputs/derived.json` blocks, members, anchors
  and residual reasons exactly (17 blocks, 53 residual);
- the emitted obligations, without the added `uuid` fields, equal the reviewed
  ones. All 26 pairs qualify `advisory` (they carry no provenance);
- graded on the reviewed iteration-7 candidate board, the compiled and reviewed
  locality contracts give identical squared distances. The compiled contract
  reproduces every distance in `evidence/obligations-measured.json`, with all 26 pairs
  within bound;
- the repeated GND function compiles to `GND@3`/`GND@6` and evaluates `pass`
  against the native netlist;
- compilation, including native admission of the 309-footprint board, is under 10 s;
- the package publishes and passes `verify_receipt`.

Two mismatches are exact and reported, not hidden:

- the compiled allocation is canonical JSON, so its SHA-256 differs from the
  reviewed file's `30ff5334…`. `derive` over the reviewed bytes, on the same run,
  returns the reviewed derivation unchanged;
- the reviewed derivation's netlist (`inventory_sha256` `7791d443…`) is not committed
  in fome-mc. The run re-exports the netlist from the committed source schematic, so
  its digest differs, while its derivation is identical.

On the same device, a verified GND fact proposed at NC pin 7, a GND fact claimed as
`IN`, an unverified fact and a repeated pad all refuse.

## Fixture dispositions

`physical_intent.FIXTURE_DISPOSITIONS` records each device control named by the
reviews. A test checks that every supported entry names existing tests:

| Control | Disposition |
| --- | --- |
| phantom L1 | supported: L1 as terminal, locality endpoint, block or residual member, move or join pad refuses |
| native repeated physical functions | supported: the captured TPS7B4253 GND pins 3 and 6 of pod U6/U12 |
| child sheet | supported: an authored two-level hierarchy of the control fixture (U1 in `/can/`, C1 on the root sheet). A real KiCad export feeds the actual `derive`. Leaving U1 in neither a block nor the residual set refuses, before output and at `verify_receipt`. Declaring it residual or allocating it compiles, and `derive` on a pcbnew capture agrees |
| TPS25982 | unsupported: no captured TPS25982 datasheet or native symbol exists here or in the pinned store; capturing the third-party datasheet is an owner gate. Its invalid-pin classes run on the TPS7B4253 and TJA1051 |

## Tests and limits

`tests/test_physical_intent.py` captures the unchanged
`fixtures/datasheet-pins/control.scope.json` and `swapped.scope.json`. It also
captures three authored edits of the control fixture: one adds a decoupling
footprint C1 for locality, one adds a locked U1 with a duplicated pad 7, and one
moves U1 into a child sheet beside a root-sheet C1. `PodEquivalenceTests` builds the
captured pod run above. Together they cover:

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
- incomplete locality (no return bound or citation), an unsupported metric and a
  mixed-net pair, both before output and at `verify_receipt`. The positive
  locality is checked through the real `PlacementIndex` and graded against a
  pcbnew placement capture of the sealed board;
- invented and plausible authored layout scales, both before output and at
  `verify_receipt`, beside the hard numeric positive;
- allocation through the actual `blocks.derive`: the locked U1 without moves,
  the board-only C1, and a positive whose receipt derivation equals `derive` on
  a pcbnew capture;
- every native lock form (bare atom, `(locked)`, `(locked yes)`, `(locked true)`,
  `(locked no)`, `(locked false)`), checked against pcbnew, with allocation and
  fixed-move refusals for the locked forms and actual-`derive` positives for the
  unlocked forms. Repeated, unknown and quoted lock forms and unknown atoms
  refuse;
- native admission: boards pcbnew does not load (quoted `locked` and `uuid`
  keywords, a nested UUID, quoted lock values, a quoted footprint flag, a nested
  lock value and an unknown node only pcbnew reads), duplicate footprint and pad
  `uuid` nodes, and a pad without a UUID that pcbnew and the text disagree on,
  also when another pad is renumbered to share its number. Each refuses before output and at `verify_receipt`;
- footprint identity equal to pcbnew on the fixture boards. Duplicate and
  missing footprint UUIDs refuse, both before output and at `verify_receipt`;
- datasheet record-line binding: the six signed, grouped, fractional and
  two-length lines, partial-token quotes and locator mismatches refuse, both
  before output and at `verify_receipt`. The 3 mm and 0.5 mm controls remain
  hard in the actual `PlacementIndex`;
- repeated physical functions (synthetic two-GND/two-VCC symbol through the
  real terminal join and topology evaluator), with wrong-pin, contradictory-pin
  and alias-collision refusals, and natively on the captured pod TPS7B4253;
- child-sheet coverage, the declared residual set and phantom-L1 refusals;
- captured pod 17-block/12-obligation equivalence and its compilation time;
- assertion ID collisions (`u1-canl`, `block:can`, a diagnostic and an
  unsupported obligation) and the input snapshot;
- Kelvin and current-loop visibility, diagnostics, manual and disabled modes,
  isolation and failed publication.

Not in this slice:

- TPS25982 controls on that device (see the dispositions above);
- the S2 architect producer (see [physical-architect.md](physical-architect.md));
- any campaign, profile or placement-adapter binding.

Citation verification remains textual (see [datasheet facts](datasheet-facts.md)).
