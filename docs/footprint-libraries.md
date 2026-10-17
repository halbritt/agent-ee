# Captured footprint assignment

`footprint_assignment` checks an explicit captured component declaration against
complete pinned footprint membership and the shared symbol resolver's **whole
device** pin inventory. It does not infer schematic instance binding or select
packages. Every unconnected pin and every unit participates. The declaration is
the selected-component authority in this declared-only mode
(`footprint:assignment-parity/1`). Adding `netlist_check` selects the
[native-bound mode](native-binding.md), which binds the declaration to the
actual native design.

The manifest is JSON with schema `agent-ee.footprint-libraries/1`, `complete: true`,
and `libraries`, a list of `{name, members}`. Each member is an exact safe relative
`.kicad_mod` `{path, sha256}`. The manifest is the captured owner's declaration of
complete membership, including empty member lists; it does not discover ambient
libraries. Capture the manifest, every member, and all symbol libraries. All
members must be readable and hash-valid before absence can establish a defect.
A missing file, parse error or unsupported padstack yields failed/unknown coverage.

The component declaration has schema `agent-ee.footprint-assignments-input/1`,
`libraries` containing exact `{name, path, sha256}` symbol-library declarations,
and a nonempty `components` list. Each component has `reference`, exact `lib_id`,
`footprint` (possibly empty), `assembly` (`smd`, `through_hole`, or `mixed`), and
`connector` (`null` or positive integer `{rows, columns}`). The shared resolver
reads only explicit captured `.kicad_sym` bytes, resolves inheritance and returns
all device units. DNP is not an exemption. A pinned native power symbol is excluded
with its source and rationale retained; reference prefixes do not exempt anything.
A resolved native `on_board=no` flag also excludes a virtual symbol. With no
physical components the check is informational; it supplies no passing assignment
criterion.

The check parameters are `manifest` and `declaration` captured relative paths,
and `assembly_intent`, a list of `{reference, assembly, connector}` records fixed
in the scope. A declaration cannot change those owner assembly predicates.
Scope/2 and scope/3 freeze manifest and library dependencies; the selection
file remains candidate data. Scope/3 advertises only assignment/parity coverage.

The index retains every pad, including duplicate numbers. Comparison uses the
unique electrical-number set. Empty-number NPTH pads are mechanical; SMD pads
whose explicit layers are exclusively paste are paste-only. Those two roles are
excluded from electrical parity. Numbered copper pads are never silently ignored.
Repeated thermal pads sharing a number remain distinct records without inventing
extra symbol pins. Custom padstacks and unqualified forms refuse coverage.

Empty fields and absent exact selections are defects only with complete library
coverage. Missing/extra numbers identify each affected reference and footprint.
Connector dimensions are declared integers, never guessed from a name. Assembly
checks compare native electrical pad types and footprint attributes with the
fixed intent; missing/contradictory attributes and mixed technology are unknown.
No land-pattern dimensions, mechanical fit, datasheet correctness or
manufacturing quality are established.

Standalone indexing or assignment runs offline:

```sh
python3 -m agent_ee.footprint_library manifest.json --captures captured --out index.json
python3 -m agent_ee.footprint_library manifest.json --captures captured --assignments selections.json --out assignments.json
```

`--query` performs literal exact `library:footprint` lookup. Output files must be
new. Pure `resolve_package` accepts a captured table with schema
`agent-ee.package-aliases/1`, convention `explicit-imperial-to-metric`, and rows
`{family, package, footprint}`. An explicit resistor/0805 row may map to
`Resistor_SMD:R_0805_2012Metric`. It returns both table and footprint hashes.
Zero or multiple rows are unknown; an unavailable target fails, without fallback.
No stock library, alias data or third-party assets are distributed with this check.
