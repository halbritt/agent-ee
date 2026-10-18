# Canonical design diff and regeneration determinism

`regeneration_determinism` (AGENTEE-51) checks that a regenerated KiCad board,
schematic or netlist matches a reference one once IDs are removed. The same
comparison is also available as a command for a readable record of what
changed between two repair candidates. Both read captured bytes only, launch no
tool and stay offline. [design_diff.py](../src/agent_ee/design_diff.py) reads
S-expressions with the shared `symbol_library.parse_sexpr` reader.

Determinism means equal canonical content, not equal bytes. The 2026-10-18
decision [routing determinism is geometric equivalence](decisions.md#routing-determinism-is-geometric-equivalence)
already defines it this way for copper. The raw byte difference is recorded on
every result and never graded. The check does not judge whether a difference is
electrically acceptable.

The approach follows atopile's `compare_without_uuid` and frozen-build path
table, tscircuit's round-trip counts and placement-free structural hash, and
Konnect's header-free netlist identity. atopile and tscircuit are MIT; Konnect
contributed only the idea. None of their code or design files is used.

## Canonical form

Each side is parsed into one ID-free tree:

- `uuid`, `tstamp` and `tstamps` nodes are dropped. Any other atom that is a
  UUID or a UUID path (such as a footprint `path` or a symbol instance path)
  becomes `<id>`.
- Tool and date headers are recorded under `headers` but not compared. These
  are `generator` and `generator_version` for boards and schematics, and the
  netlist `design` element's `date`, `tool` and `source`.
- Netlist net `code` numbers are dropped. In pre-KiCad-10 boards,
  `(net CODE "NAME")` declarations and `(net CODE)` references become the net
  name. KiCad 10 boards already name nets directly.
- Only the atoms of coordinate and size nodes are rounded to the declared
  `decimals`, with ties going to the even digit: `at`, `xy`, `start`, `end`,
  `mid`, `center`, `size`, `width`, `thickness`, `offset`, `drill`,
  `rect_delta`, `radius` and `length`. Text such as a `Value` stays exact.
  Because this is rounding, two values just either side of a rounding boundary
  can still differ by less than the precision.
- Children are keyed so that file order does not matter. Footprints and
  schematic symbols are keyed by `Reference` (plus the unit when it is not 1).
  Sheets are keyed by `Sheetname`. Pads, properties, pins, netlist components,
  nets, nodes, library parts and fields are keyed by number or name. Unordered
  primitives such as segments, vias, zones, drawing primitives, wires, labels
  and junctions are keyed by the hash of their canonical content. Other repeated
  nodes, such as polygon `xy` vertices, keep file order. Repeated keys get a
  `#2`, `#3` suffix.

XML netlists (`kicadxml`) and S-expression netlists (`kicadsexpr`) read into the
same tree. XML attributes become `(name value)` children, so both exports give
the same structural hash. JSON probe outputs such as `connectivity.json` compare
as objects by key and arrays by index. Their UUID-valued strings become `<id>`,
and floats are rounded only when `decimals` is given.

## Diff, hashes and typed changes

`entries` lists each difference as `{path, before, after}`. Paths look like
`footprint[R2]/at`, `footprint[R1]/pad[2]/net` or `segment@3fa2c1d9e0b4`. When
a key exists on one side only, its entry holds the canonical text, capped at
400 characters with a hash of the rest, and `null` for the other side. Moving
one footprint by 0.1 mm with `decimals: 2` gives exactly the entry
`footprint[R2]/at`, `105 100` to `105.1 100`. A 0.001 mm move gives no entry.

Each side gets three hashes:

| Hash | Covers |
| --- | --- |
| `structural` | Boards: per footprint the reference, footprint ID, `Value` and pad number with its net; the net-name set; the layer table. Schematics: per symbol the reference/unit, `lib_id`, `Value` and `Footprint`; label and power-symbol net names; sheet names. Netlists: components with value and footprint; nets with their nodes. |
| `placement` | Boards: each footprint's `at` and `layer`. Schematics: each symbol's `at` and `mirror` and its property text positions (absolute in schematic files). `null` for netlists and JSON. |
| `identity` | The whole canonical tree minus the placement fields, so routing, graphics and settings changes appear here. |

The diff is empty exactly when `identity` and `placement` are both equal. A
placement-only repair changes only `placement`. A net rename changes
`structural` and `identity`. Flipping a footprint to the other side also rewrites
its pad and graphic layers, so it changes `identity` as well as `placement`.

`typed` summarises the diff:

- `counts`: before and after counts that changed, for footprints, pads,
  segments, arcs, vias, zones and groups; symbols, sheets, wires, buses,
  junctions, no-connects and labels; or netlist components, library parts, nets
  and nodes.
- `net_names`: names `removed` and `added`. A rename lists both names.
- `transforms`: footprint `at` and `layer`, or symbol `at` and `mirror`, for
  each reference whose transform changed.
- `assembly_exclusions`: board footprint `attr` flags (`exclude_from_pos_files`,
  `exclude_from_bom`, `dnp`, `board_only`) or schematic symbol `in_bom`,
  `on_board`, `in_pos_files`, `dnp` and `exclude_from_sim`, for each reference
  whose flags changed.

`raw` records both byte sizes and SHA-256 digests, whether the bytes are
identical, and how many lines appear only on each side (a multiset count, not
an ordered diff).

## The check

```json
{"id": "board-regen", "kind": "regeneration_determinism",
 "params": {"format": "board", "decimals": 2,
            "reference": {"path": "committed/rc_filter.kicad_pcb"},
            "subject": {"path": "project/rc_filter.kicad_pcb"}}}
```

Parameters:

- `format` is `board`, `schematic`, `netlist` or `json`.
- `decimals` (an integer from 0 to 6) is required for boards and schematics,
  optional for JSON, and refused for netlists, which compare exactly.
- `reference` and `subject` are each either `{"path"}` or `{"check",
  "artifact"}`, and must differ. A `path` must be in the captured inventory. A
  `reference` path is an immutable baseline input. It cannot be the scope's
  writer-mutable `board` or `schematic`, so the writer cannot edit the reference
  to match. A `{"check", "artifact"}` side names an output of an earlier check of
  the same candidate, such as the `netlist` check's `design.xml`. Capability
  audits record that check as a prerequisite.

Results:

| Situation | Execution, outcome |
| --- | --- |
| Canonical diff empty (bytes may differ) | `ok`, `pass` |
| Canonical diff non-empty | `ok`, `defect`; entries in `details` and `work/ID/design_diff.json` |
| Either side does not parse as the declared format, has the wrong root, or a captured path is missing | `malformed`, `unknown`; the summary names `reference:` or `subject:` |
| Producer check absent from the result set, not `ok`, from another candidate, or without the named artifact | `not_run`, `unknown` |
| Producer check not declared before this check, or invalid parameters | `unsupported`, `unknown` |

A missing probe output is never read as "no change". `details` holds the
hashes, typed changes, headers, raw byte record and the first 50 entries. The
full report is the `design_diff.json` evidence file.

## Comparing repair candidates

```bash
PYTHONPATH=src python3 -m agent_ee.design_diff --format board --decimals 2 \
  runs/a/inputs/project/rc_filter.kicad_pcb runs/b/inputs/project/rc_filter.kicad_pcb --out diff.json
```

The command writes the same `agent-ee.design-diff/1` report, with each file's
path and SHA-256, to `--out` or standard output. It exits 0 when the two are
equivalent, 1 when they differ and 2 on a parse or parameter error. It does not
pick a candidate or write to any run.

## Evidence

[tests/test_design_diff.py](../tests/test_design_diff.py) uses the original
[pair fixture](../fixtures/design-diff/README.md) and the RC control fixture.
UUID churn re-saved by sandboxed `kicad-cli pcb upgrade --force` gives an empty
diff and identical hashes; KiCad writes footprints in UUID order, so the bytes
and footprint order differ. Moves of 0.1 and 0.001 mm, net renames, numbered
legacy nets, value, count and exclusion changes, schematic symbol moves and
label renames are each covered. Two sandboxed netlist exports 1.1 s apart, in
XML and S-expression form, differ only in `date` and share every hash. The
pipeline test runs the check after a native `netlist` check against a committed
board and golden XML. It confirms the baseline pins the references but not the
candidate board, and that truncated, missing, failed or absent producer outputs
fail closed.
