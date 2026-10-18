# Compiling a requirement record into a scope/2

`agent_ee.requirement_scope` (AGENTEE-100) turns a stage-1 requirement record
and the verbatim spec it cites into an `agent-ee.scope/2` file. The compiler
uses only the Python standard library. It makes no model calls and infers
nothing: every value in the scope comes from the record, from a fixed table in
the compiler, or from an exact quote of the spec. The
[design dialog](design-dialog.md) (AGENTEE-25) produces the owner's decisions.
This compiler covers only the step from record to scope, and the rung-1
instance of that step.

```sh
PYTHONPATH=src python3 -m agent_ee.requirement_scope fixtures/rung1/requirements.record.json --out NEW_DIRECTORY
```

Exit 0 means the scope compiled and `compile_baseline` accepted it. Exit 1
means the record is well formed but was refused. The findings are printed as
JSON, and nothing is written. Exit 2 means malformed input, an existing output
directory or an I/O failure.

## Record

The record uses `agent-ee.requirement-record/1`. Its fields are exactly
`schema`, `name`, `scope`, `spec`, `project`, `stock`, `requirements`, `checks`,
`context`, `unchecked` and `limitations`. Unknown and duplicate keys are refused.
Numbers are read with the [intent importer's](design-intent.md) exact decoder,
so a bound keeps its source token.

| Field | Content |
| --- | --- |
| `spec` | `{path, sha256}`. The path is relative to the record. The hash must match the spec bytes |
| `project` | `{name}`: the file stem of the scaffold's `.kicad_sch` and `.kicad_pcb` |
| `stock` | `{symbols, footprints}`: include patterns for the stock KiCad library subsets |
| `requirements` | `{id, statement, locator, checks}` per requirement |
| `checks` | Typed check declarations in execution order (see below) |
| `context` | `{locator, reason}` for descriptive spec sentences that are not requirements |
| `unchecked` | `{locator, reason}` for spec text that no declared check can evaluate |
| `limitations` | Free-text scope limitations |

A locator is an exact quote of the spec with normalized whitespace. Markdown
heading lines are excluded. The quote must occur exactly once and lie inside
one sentence; sentences end at a period followed by a space. The compiler
refuses the record if any spec sentence is quoted by no requirement, `context`
or `unchecked` entry. So no sentence of the spec is silently dropped. Each
`unchecked` entry becomes a `Not checked: "…" (reason)` scope limitation. These
requirements are not counted as passing.

Check declarations, and the scope specifications the compiler writes for them:

| Record kind | Record fields besides `id`, `kind` | Scope specification |
| --- | --- | --- |
| `erc`, `drc`, `netlist` | none | The same kind with no parameters |
| `geometry` | `min_track_width_mm` | `copper-width/1` over `PCB_TRACK` and `PCB_ARC`, `exclude_nets: []` |
| `rc_transient` | `input_net`, `output_net`, `ground_net`, `step_v`, `stop_ms`, `bounds` (`{at_ms, min, max}` list), `samples_ms` | Bounded measurements `vout_<t>ms`, then informational samples; `netlist_check` set to the preceding netlist check |
| `fab_export` | none | `netlist_check` set to the preceding netlist check |

`rc_transient` and `fab_export` need exactly one earlier `netlist` check.

## Refusals

All refusals in one record are reported together. Each finding has a `code`, an
`id` and a `message`:

| Code | Meaning |
| --- | --- |
| `unmapped_requirement` | A requirement maps to no check |
| `unmapped_check` | A declared check is mapped to no requirement |
| `unknown_check` | A requirement names an undeclared check |
| `locator` | A quote is not in the spec, occurs more than once, or crosses a sentence boundary |
| `uncited_sentence` | A spec sentence is quoted by nothing in the record |
| `unsourced_bound` | A declared bound (track width, step voltage, measurement time or limit) is not written as a number in the quote of any requirement mapped to that check |
| `spec_hash` | The spec bytes differ from `spec.sha256` |
| `prerequisite` | A transient or fab check has no single preceding netlist check |
| `unsupported_check` | `capabilities.describe_check` rejects a compiled check, for example a bound time after `stop_ms` |

The compiler reports `unmapped_requirement` because `compile_baseline` alone
cannot see that a requirement lost its mapping. If the record maps a check to
two requirements, the scope still covers the check after one of them is
removed. For example, R-parts also maps `fab`, so a hand-written scope without
R-fab compiles silently. The compiler checks requirement IDs and sentences,
not the meaning of each requirement. Every requirement the spec states needs
its own record entry. The owner reviews that list.

## Outputs

| Path | Content |
| --- | --- |
| `scope.json` | The `agent-ee.scope/2`, written with `storage.write_json` formatting |
| `receipt.json` | `agent-ee.requirement-compile-receipt/1` |
| `scaffold/requirements/spec.md`, `record.json` | Byte-exact copies of the spec and the record |

The scope's inputs are `scaffold/project` (the empty-candidate scaffold,
AGENTEE-87), the stock symbol, footprint and template subsets, and
`scaffold/requirements`. Paths never point at `fixtures/rc`. The compiler does
not write the scaffold project. Every baseline requirement cites
`requirements/spec.md` with its quote as the locator. The targets are
`requirements/record.json`, and the reference architecture is
`requirements/spec.md`. `mutable_inputs` is the schematic and the board.

The baseline binds only the two requirement files and the check criteria. The
compiler therefore computes `baseline_id` with `compile_baseline` before the
scaffold exists. A later capture of a scope compiled from the same bytes gets
the same `baseline_id`, even if its project path points at a different design.
The receipt records the compiler version (`agent-ee.requirement-scope/1`), the
compiler source hash, the record, spec and scope hashes, the `baseline_id`, and
the sentence index and character span of each quote.

## Rung-1 instance

[fixtures/rung1/requirements.record.json](../fixtures/rung1/requirements.record.json)
is the rung-1 record for [fixtures/rung1/spec.md](../fixtures/rung1/spec.md).
[fixtures/rung1/compiled.scope.json](../fixtures/rung1/compiled.scope.json) is
its committed compiled scope.

| Requirement | Checks | Quote |
| --- | --- | --- |
| R-native | `erc`, `drc`, `netlist` | "Native ERC and DRC must pass and the netlist must export." |
| R-width | `geometry` (copper-width/1, 0.2 mm) | "Copper tracks must be at least 0.20 mm wide." |
| R-response | `rc-step` (1 V step, /OUT at 1 ms in [0.62, 0.65] V, 5 ms sample) | "With an ideal unloaded 1 V step at /IN, …" |
| R-fab | `fab` (`fab_export`) | "R1 and C1 are JLCPCB-assembled SMD parts" |
| R-parts | `netlist`, `rc-step`, `fab` | "A 10k resistor R1 connects /IN to /OUT, …" |

The header pin assignment, the board outline and layer count, and the package
and hand-solder classes are `unchecked` limitations. They need the
`hardware_interface` and `board_envelope` checks, which are not in the rung-1
check list. The compiled `baseline_id` is
`6c94b7dcc057181fba564f09924dda870bd6b29e3dc14c4a1b27cd172486e0af`.

A checks-only run on the reference board `fixtures/rc/control`, with the
project path and file names substituted, gave these results (2026-10-17,
KiCad 10.0.7):

- `erc`, `drc`, `geometry` and `rc-step` pass, and `netlist` is `info`. These
  are the same results as the hand-written
  [closed-loop control](../fixtures/closed-loop/control.scope.json).
- `fab` is `execution: unsupported`, `outcome: unknown`. `flagged_parts` lists
  exactly C1, J1 and R1, each with `missing part-binding properties: LCSC,
  Manufacturer, Partnumber`. The fixture has no part bindings. Merged
  `fab_export` reports an unbound BOM line as unsupported, so the result is
  unknown coverage rather than a check defect.
- The captured `baseline.json` has the compiled `baseline_id`.

## Tests

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_requirement_scope -v
```

The unit tests cover byte-identical compilation against the committed scope,
the mappings and `compile_baseline` acceptance, the R-fab unmapped-requirement
refusal, and each refusal code. The native class runs the compiled and
closed-loop scopes checks-only on the reference board and compares the
results above.

## Limits

- The record vocabulary covers only the rung-1 check kinds. Other kinds need a
  compiler extension and a new compiler version.
- The compiler is not wired into `agent_ee.generate`, `agent_ee.rung1_campaign`
  or `agent_ee.rung1_qualified`, which still compile their own scopes.
  `rung1_campaign.write_scaffold` copies its own `scaffold/requirements` tree.
- An AGENTEE-26 intent record has a closed field set, with no requirement IDs
  or check mappings. The requirement record is therefore a separate schema. It
  cites the spec directly, not an intent import.
- Scope/2 serializes baseline/1 without a coverage audit. Conversion to
  scope/3 with `capabilities.convert_scope` (AGENTEE-4) is not exercised here.
