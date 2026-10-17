# Captured schematic decompiler

`python -m agent_ee.schematic_decompile` extracts the supported electrical
connectivity of captured KiCad schematic bytes. It uses the existing
[connectivity intent](connectivity-ir.md) resolver and produces intent/1,
resolved/1 and a diagnostic report. It does not register a check, create a
coordinator witness, run a campaign, or establish acceptance.

Supply a JSON list of the existing intent library manifests (`name`, safe
relative `path`, `sha256`), and a directory containing those captured bytes:

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_decompile captured.kicad_sch \
  --libraries libraries.json --captures captures --out decompiled
```

The output directory must be new. Success writes `intent.json`, `resolved.json`
and `report.json`. Unsupported electrical constructs produce exit 1 and only
`report.json`; no partial intent or resolution is written. Invalid CLI arguments,
manifest JSON and filesystem errors fail before creating output. No installed
symbol library is substituted for a missing capture. Symlink library captures
are refused. No new dependency or network access is required.

The report binds the source SHA-256, selected library manifests, algorithm
version, exact source pin positions in integer nanometres, native UUID anchors,
construct inventory, ignored non-electrical graphics, and intent/resolution
hashes. Each net has a deterministic star spanning tree of physical terminals:
exactly N−1 `connect_pins` edges. These edges are presentation data in the report;
the existing IR retains its terminal-list nets. No-connects are never inferred.
Canonical JSON has no timestamps and repeats byte for byte for the same inputs.

## Qualified subset

The supported format is single-sheet `20250114`, with explicit single-unit
symbols, wires, junctions and global labels. Physical pin geometry, orientation,
unit and electrical metadata in embedded symbols must agree with the captured
library resolution. Explicit orthogonal rotations and `x`/`y` mirrors are
qualified against native exports of an asymmetric owned symbol. Mirrors apply
after rotation in sheet coordinates. Source properties retain the actual
reference, value and footprint; reference and UUID identities must be unique.

Native KiCad 10.0.7 controls establish these connectivity rules for saved source:

- Wire endpoints at the same coordinate join. Co-located pins join without wires.
- Interior wire crossings do not join without a junction or global label.
- A pin or another wire endpoint on an **unsplit** segment's interior does not
  attach by itself. An explicit junction joins the incident conductors.
- A global label attaches to a segment interior. Qualified native label identity
  joins remote anchors, including encoded/decoded aliases described below.
  Case and Unicode spelling are preserved.
- Duplicate and zero-length wires retain their native connectivity. Overlapping
  collinear segments remain separate unless the qualified attachment rules join
  them. Diagonal segments use exact integer cross products.

Global label identity is qualified against original-native KiCad 10.0.7 exports.
`{slash}`, `{backslash}`, `{colon}`, `{space}` and `{quote}` decode to `/`,
backslash, `:`, space and apostrophe respectively. Thus `A/B` and `A{slash}B`
join one net; the IR net name is the exported `A/B`, while the diagnostic
`global_labels` entries retain each original source spelling. Aliases on the
same conductor also share that identity. Ordinary spaces and underscores,
uppercase and lowercase, composed and decomposed Unicode, and `~{RESET}` versus
`RESET` remain distinct. Standalone overbars `~{NAME}` with nonempty uppercase ASCII letters,
digits or underscores are preserved verbatim. Other tilde forms are unsupported;
for example native `~{slash}` joins `~/` with exported name `~{slash}`.

Other brace forms, including `{brace}`, unknown encodings, unmatched braces,
nested overbars and escaped literal token spellings, refuse the entire source.
Native `{brace}` decoding can expose another token: `A{brace}slash}B` joins
`A/B`. A generic single replacement pass would therefore produce incorrect
connectivity. Dynamic `${...}` labels and control characters (including encoded
`{tab}`) are unsupported. These refusals produce no partial IR even when other
nets are fully supported. This is a bounded qualified grammar, not a general
KiCad display-text interpreter.

No grid snapping is performed. Inputs finer than KiCad's qualified 100 nm
schematic lattice, nonfinite/sub-nanometre coordinates, or coordinates beyond
±10 m are refused. A one-nanometre gap therefore cannot silently close through
native rounding. The shared reader also bounds bytes, AST nodes and depth;
extraction caps source bytes at 8 MiB, aggregate captured libraries at 32 MiB,
components at 128, physical pins and wire segments at 2048 each, coordinate
nodes at 8192 and coordinate/segment work at two million.

Local/hierarchical labels, child sheets, buses/entries/aliases, multiunit symbols,
no-connect markers or pin types, power symbols, hidden pins, alternate pin
styles, DNP/excluded instances, text variables, inconsistent instance metadata,
unknown electrical constructs and unrepresented custom instance properties are
refused. Conflicting global names on one net, singleton/unaccounted physical
pins, unattached junctions and conductors lacking two physical terminals also
refuse the entire extraction. A pinless conductor is not silently discarded
merely because native netlist export omits it.

## Independent qualification and native round trip

The original [owned fixture](../tests/fixtures/schematic-decompile/PROVENANCE.md)
is first exported with native `kicad-cli`. Tests use the existing strict
`native_netlist.read_native_inventory` reader and `netlist_parity.compare` with
source UUID anchors to compare that original export against the extracted
resolution. Exact component and physical pin inventories, required instance
correspondence, and topology must all pass; Jaccard 1.0 alone is insufficient.
The tests also require swapped sensor pins, a changed label, a removed wire or
junction, and an extra component to fail against the original.

The extraction report explicitly leaves `native_qualification` and `roundtrip`
as `not_run`: extraction itself executes no native tools. Test qualification is
a diagnostic result, separate from sealed campaign evidence and owner review.

`python3 -m agent_ee.schematic_roundtrip` performs the separate native diagnostic:

```sh
PYTHONPATH=src python3 -m agent_ee.schematic_roundtrip captured.kicad_sch \
  --libraries libraries.json --captures captures --layout layout.json \
  --out roundtrip-diagnostic
```

The layout uses the existing [emitter layout schema](schematic-emitter.md).
The caller must choose a new private output directory. Existing outputs and
symlink output paths are refused. Captured input bytes are retained there and
mounted read-only for native tools through the production executor, with no
network and a finite timeout (120 seconds by default, at most 600 through the
Python function). No global KiCad configuration is changed.

`report.json` binds the source, manifest/member libraries, actual layout,
extracted intent/resolution, emitted files, native XML and ERC artifact hashes.
Execution receipts record exact sandbox argv, timeout, exit status and log hashes;
the report retains their hashes separately in `execution_receipts`. The native
executable hash, observed version and XML exporter identity are retained. Timing,
receipt hashes and native date-bearing artifact hashes can vary across runs;
pure extraction/emission bytes and the structural comparisons are deterministic.

The shared41 reader and parity engine compare each native export against the
same extracted resolution for exact component multiplicity, full-pin topology
and required instance correspondence. Source UUID anchors and independently
derived emitter UUID anchors link both native inventories through immutable
logical component IDs. A separate native-to-native structural comparison must
also pass. Ambiguous correspondence remains unknown; structural Jaccard alone
cannot qualify the result. The emitted local-label file is judged through its
native export, rather than being passed through the deliberately narrower source
decompiler.

The unchanged owned IMU qualifies with three components, ten physical pins,
four nets and Jaccard 1.0. Native tests independently check endpoints under all
quarter-turn/mirror combinations, including an exact 100 nm offset. Removing,
shorting or swapping emitted labels fails topology. Changing only an emitted
symbol UUID preserves topology but fails instance correspondence. The original
50 mil regulator ERC/current-format control and hierarchical emitter parity
remain separate regression controls.

The IMU's native off-grid ERC observations are retained without filtering or
suppression. A topology pass does not assert ERC cleanliness or engineering
adequacy. Missing/failed native exports, missing/malformed XML, cancellation,
unsupported source constructs, library mismatch and emitter refusal cannot
produce a completed passing roundtrip. Failure records and process receipts
remain available. No sealed coordinator finding or campaign acceptance witness
is created by this diagnostic; owner review remains separate.
