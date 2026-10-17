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
- A global label attaches to a segment interior. Identical exact label text
  joins remote anchors; case and Unicode spelling are preserved.
- Duplicate and zero-length wires retain their native connectivity. Overlapping
  collinear segments remain separate unless the qualified attachment rules join
  them. Diagonal segments use exact integer cross products.

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

## Independent qualification and remaining round trip

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
The authoritative baseline for this implementation contains no reviewed native
emitter entry point. The remaining AGENTEE-42 acceptance is to consume that
reviewed AGENTEE-40 entry point, export its emitted schematic with native KiCad,
and require original-native versus emitted-native exact inventory/instance
parity and Jaccard 1.0, retaining input/output/tool hashes and invocation records.
Shared emitter/decompiler agreement alone cannot satisfy this requirement.
