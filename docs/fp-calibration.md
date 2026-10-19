# Reviewer false-positive calibration

AGENTEE-70 measures the reviewers themselves. It takes one captured design and
generates variants that keep its electrical function, plus seeded variants that
change it. It then asks each profile reviewer, in both presentation orders and
repeatedly, whether each pair is electrically equivalent. The report gives each
reviewer's false-positive rate, false-negative rate, agreement and order
sensitivity. This complements the [seeded benchmark](seeded-benchmark.md), which
measures recall of checks against seeded defects. Calibration results are
reported separately, are not design evidence and do not change adjudication.

[fp_calibration.py](../src/agent_ee/fp_calibration.py) has three commands.
Variant generation, the equivalence proof and the report are deterministic and
offline. Only `run` calls a model.

```bash
PYTHONPATH=src python3 -m agent_ee.fp_calibration prepare fixtures/rc/control \
  --root rc_filter.kicad_sch --seed 7 --out /tmp/cal
PYTHONPATH=src python3 -m agent_ee.fp_calibration run /tmp/cal \
  --profile profiles/claude.json --repeats 3 --max-seconds 7200
PYTHONPATH=src python3 -m agent_ee.fp_calibration report /tmp/cal --out /tmp/cal-report.json
```

Each command exits 0 on success and 2 on a refusal. Output directories and
existing calls are never overwritten.

## Variants

`prepare` copies the design's `.kicad_sch` and `.kicad_pro` files. Only flat
designs with one schematic are supported. Hierarchical sheets and a second
schematic are refused. Each transform reads the root schematic with the shared
`symbol_library.parse_sexpr` reader, through the quote-preserving
`schematic_emit.parse_quoted`, and writes it back in KiCad layout. Each variant
kind gets its own random stream, `random.Random("SEED:KIND")`, so the same seed
and design always give the same plan identity.

| Kind | Expected | Change |
| --- | --- | --- |
| `reference_renumber` | equivalent | Each reference prefix is renumbered from a seeded base (11, 21 or 101 upward) in seeded order, in both the `Reference` property and the instance paths. Power references (`#...`) are left alone. |
| `net_rename` | equivalent | Every local and global label name gets one seeded suffix, such as `IN` to `IN_NET53`. |
| `sheet_reshuffle` | equivalent | Wires, labels, symbols, junctions, no-connects and graphics are permuted in file order. Headers, `lib_symbols` and `sheet_instances` stay in place. |
| `symbol_relocation` | equivalent | One seeded non-power symbol moves 5.08, 7.62 or 10.16 mm along one axis with its fields. A new wire joins each old pin point to the moved pin, and a no-connect flag on a pin moves with it. |
| `value_change` | different | The leading number of one numeric `Value` is replaced, for example `100n` to `2n`. |
| `label_reassign` | different | One local label is given another existing label's name. |
| `wire_removal` | different | One seeded wire is deleted. |

A transform that does not apply to the design (for example `net_rename` with no
labels) is excluded with the reason `transform not applicable to this design`.

## Equivalence proof

Each variant's label is proven before any reviewer sees it. If it cannot be
proven, the variant is excluded and never presented.

1. The original and each variant are exported with sandboxed
   `kicad-cli sch export netlist --format kicadxml`, with no network.
2. The variant netlist is mapped back through its declared renaming:
   references for `reference_renumber`, and `NAME` and `/NAME` for
   `net_rename`. KiCad names unlabelled nets after one of their pins
   (`Net-(R1-Pad1)`, `unconnected-(...)`). On both sides such a net is renamed
   to its sorted `ref.pin` set, so renumbering cannot rename it.
3. The two normalized netlists are compared with the
   [canonical design diff](design-diff.md) (AGENTEE-51) in netlist format.

An `equivalent` variant needs netlist identity, meaning an empty canonical
diff. A `different` variant needs a non-empty diff. Any other result excludes
the variant:

| Situation | Status, reason |
| --- | --- |
| Equivalent kind, diff not empty after the declared renaming | `excluded`, `netlist identity not established after the declared renaming` |
| Different kind, diff empty | `excluded`, `change left the netlist identical` |
| Variant export failed or produced no netlist | `excluded`, `variant netlist export failed: EXECUTION` |
| Netlist does not parse | `excluded`, `netlist did not parse: ...` |

If the original export fails, `prepare` refuses. The renaming is never inferred
or repaired. A wrong declared map stays unproven. A symbol moved without its
connecting wires is excluded, and the tests check this. `plan.json` records each
variant's change, file hashes, the design-diff hashes and the first 20 diff
entries. It also records the `kicad-cli` version and a plan identity. Raw and
normalized exports stay under `proof/`.

## Reviewer calls

`run` takes an ordinary [reviewer profile](reviewer-profiles.md). Every
`reviewers` entry is measured. The profile's adjudicator is validated but never
launched. A profile that lists Claude, Codex and Agy reviewers measures all
three in one run. For each reviewer, repeat, included variant and order there
is one call:

- `forward` shows the original as `/candidate/A` and the variant as
  `/candidate/B`. `reverse` swaps them.
- The fixed prompt defines equivalence as the same components (library part,
  value, footprint) and pin-to-pin connectivity, up to one consistent renaming
  of references and net labels. It names no variant kind, label or order. Its
  only call-specific text is an opaque `call_id`, derived from the plan
  identity, role, variant, order and repeat.
- The verdict schema is `agent-ee.pair-verdict/1`:
  `{schema, call_id, verdict: equivalent|different, differences[{title, location, reasoning}], limitations[]}`.
  A `different` verdict needs at least one difference. An `equivalent` verdict
  needs none. A record that is unbound, inconsistent or has extra fields is
  invalid.

Calls go through `reviewers.reviewer_call`, the same seam as initial reviews. A
launch expectation is captured once before the first call. Each call gets the
ordinary [preflight guard](reviewer-profiles.md#tools-and-workspaces) receipt
and the existing harness adapters, Bubblewrap mounts and credential rules. Calls
run one at a time. Every settled call is written to `calls.json` before the next
call is admitted.

| Situation | Ledger `execution`, verdict |
| --- | --- |
| Valid verdict | `ok`, `equivalent` or `different` |
| Provider turn succeeded but the verdict is invalid | `malformed`, missing |
| Preflight refusal, unavailable, failed, timed out | that execution, missing; later calls of the same harness are `not_run` |
| Deadline exhausted | `not_run`, missing |
| Operator interrupt | the active call `interrupted`, later calls `not_run`; the ledger is written and the interrupt is re-raised |

A missing call is never agreement and is never retried or replayed. `run`
refuses when `calls/` or `calls.json` already exists. A new measurement needs a
new `prepare` output. Each launched call keeps `inputs/A`, `inputs/B`,
`prompt.txt`, `execution.json`, `response.json` and the harness's own
`review-work/ROLE` logs, including the preflight receipt.

## Report

`report` reads only `plan.json`, `calls.json` and the retained call files. It
imports no reviewer code and makes no model call. It refuses with a
`CalibrationError` naming the call if any of these does not hold:

- The plan identity and every design hash are unchanged.
- Each ledger entry's call ID and expected label match the plan.
- The ledger lists every scheduled call exactly once, including unlaunched ones.
- Each retained record hash matches.
- The presented A/B bytes match the entry's order.
- The prompt is the fixed prompt.
- Each verdict recomputed from `execution.json` and `response.json` matches the
  ledger.

For each reviewer:

| Field | Meaning |
| --- | --- |
| `false_positive_rate` | `different` verdicts on equivalent pairs, divided by valid calls on equivalent pairs |
| `false_negative_rate` | `equivalent` verdicts on different pairs, divided by valid calls on different pairs |
| `agreement_rate` | valid calls matching the proven label, divided by valid calls |
| `false_positives_by_kind` | false positives per equivalent kind |
| `pairs[].orders` | per order: `different`, `equivalent` and `missing` counts, `different_fraction`, `majority` (`different`, `equivalent`, `split`, or `unknown` with no valid vote) |
| `order_dependent` | `true` when the two orders' majorities differ, `false` when they agree, `null` when either order has no valid vote |
| `order_dependent_pairs`, `order_sensitivity` | flagged pairs, and flagged pairs divided by pairs with both orders evaluable |
| `repeat_consistency` | share of evaluable (pair, order) cells whose valid votes all agree |
| `missing_calls`, `missing_by_execution` | calls without a valid verdict, by execution |

Missing calls are left out of every denominator and listed. A rate with no
valid call is `null`, never `0`. An unknown order dependence is `null`, never
`false`. Excluded variants are listed with their reasons.

## Sources

The approach follows tscircuit/schematic-benchmark01's multimodal judge
(`README.md`, `src/evaluate-judge.ts` and `src/judge.ts`). That judge uses
style-only and real-change validation pairs, a randomised presentation order,
repeated calls and a vote fraction, and treats self-reported similarity as
poorly calibrated. Its prompt-benchmarks `ai-circuit-scorer.ts` is the
uncalibrated yes/no contrast. The repository declares no licence. It was used
as a description of the method only. No code, prompt, pair or text was
retrieved or copied.

## Evidence and limits

[tests/test_fp_calibration.py](../tests/test_fp_calibration.py) prepares the
owned [RC control fixture](../fixtures/rc/PROVENANCE.md) with real sandboxed
`kicad-cli` exports. It shows:

- All seven kinds are proven. The renumbered and renamed raw exports differ, so
  the identity depends on the declared renaming.
- The same seed gives the same plan identity.
- Moved-without-wires, wrong-map, unchanged-different, failed-export and
  unparseable cases are excluded.
- Inapplicable transforms and hierarchical or multi-schematic designs are
  handled as described above.

The run tests replace only `reviewers.dispatch`, the harness call, with a
scripted transport. Preflight receipts and the Bubblewrap probe still run. The
scripted reviewers produce:

- an order-dependent false positive on `reference_renumber`;
- a provider failure that stops that harness after the third call;
- an inconsistent record counted as `malformed`;
- consistent false negatives on `value_change`.

The tests check the exact rates and flags. They also check that the report is
reproducible with every reviewer entry point patched to fail, and that edited
responses, a forged ledger verdict, a verdict on an unlaunched call and swapped
A/B inputs and a ledger with an entry removed are refused. Further tests cover no replay, interruption and a zero
deadline, where every rate is `null`.

The tests show the mechanism, not any model's rates: no live model call was
made. The variants cover one flat sheet. Sheet reshuffle reorders items within
that sheet; it does not move symbols between hierarchical sheets. Only one
variant of each kind is generated per seed. Rates from one small design have
wide uncertainty and say nothing about reviewer recall on real defects.
