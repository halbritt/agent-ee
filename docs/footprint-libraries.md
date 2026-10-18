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
`assembly_intent`, a list of `{reference, assembly, connector}` records fixed
in the scope, and an optional captured `declarations` path (see
[owner declarations](#owner-declarations)). A declaration cannot change those owner assembly predicates.
Scope/2 and scope/3 freeze manifest and library dependencies; the selection
file remains candidate data. Scope/3 advertises only assignment/parity coverage.

The index retains every pad, including duplicate numbers. Comparison uses the
unique electrical-number set. Empty-number NPTH pads are mechanical; SMD pads
whose explicit layers are exclusively paste are paste-only. Those two roles are
excluded from electrical parity. Numbered copper pads are never silently ignored.
Repeated thermal pads sharing a number remain distinct records without inventing
extra symbol pins. Custom padstacks and unqualified forms refuse coverage.

## Owner declarations

An optional `declarations` check parameter names a captured
`agent-ee.footprint-declarations/1` file. It is an immutable scope input, hashed
into the baseline like the manifest, never candidate data. Nothing in it is
inferred from names, tags or descriptions. It has two lists:

- `footprints`: `{footprint, mechanical_pads, thermal_via_pads, custom_pads,
  assembly, rationale}` keyed by exact `library:footprint`. `mechanical_pads`
  lists numbers, which can include `""`. Copper or numbered NPTH pads with
  those numbers get role `mechanical` with `declared: "mechanical"`, so they
  leave electrical parity and the connector count. `thermal_via_pads` lists
  numbers whose through-hole copper pads are vias beside an SMD pad with the same
  number. Those pads stay electrical, are marked `thermal_via`, and do not make
  the footprint mixed technology. `custom_pads` lists numbers whose `custom` or
  `trapezoid` shapes, `options` or `primitives` are accepted for numbering only;
  the geometry is recorded as `declared_custom` and is not qualified. `padstack`
  is still refused. `assembly` (`smd`, `through_hole` or `null`) stands in for a
  missing native `attr`.
- `pinless_symbols`: `{lib_id, rationale}` for physical symbols without pins
  (mounting holes, fiducials). A declared pinless component must still select a
  present footprint. Every electrical pad on that footprint is an extra-pad
  defect. Its assembly comes from the footprint's single native attribute, or
  from the footprint's declared assembly when it has none.

Each declaration must apply. A declared number that matches no affected pad, a
thermal-via number without both SMD and through-hole copper pads, a footprint
that is not a manifest member, an assembly declared beside a native `smd` or
`through_hole` attribute, or a pinless declaration on a symbol with pins leaves
coverage unknown. A malformed file is `malformed`. A declaration never removes
a pin's requirement. A symbol pin whose only pads are declared mechanical is
reported as `missing_pads`. Reports retain `declarations_sha256`, the
footprint's `footprint_declaration`, the `mechanical_pads` it actually applied,
and the `pinless` record. Undeclared MP pads remain extra-pad defects. Undeclared
pinless symbols, thermal vias, attr-less footprints and custom pads remain
unknown.

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
python3 -m agent_ee.footprint_library manifest.json --captures captured --assignments selections.json --declarations declarations.json --out declared.json
```

`--query` performs literal exact `library:footprint` lookup. Output files must be
new. Pure `resolve_package` accepts a captured table with schema
`agent-ee.package-aliases/1`, convention `explicit-imperial-to-metric`, and rows
`{family, package, footprint}`. An explicit resistor/0805 row may map to
`Resistor_SMD:R_0805_2012Metric`. It returns both table and footprint hashes.
Zero or multiple rows are unknown; an unavailable target fails, without fallback.
No stock library, alias data or third-party assets are distributed with this check.

## Converting pinned legacy footprints

`agent_ee.footprint_capture` turns footprints committed in KiCad 5 `(module`
syntax in another repository into a captured `.pretty` that this check can index.
It reads source bytes only from a local repository's object store, at one full
commit id, through the offline Git path of the
[reference-block capture](reference-block-interface.md#external-source-citations). `HEAD`, branch names, short or unknown
ids, trees, and untracked, staged or linked files are refused. Working-tree
edits never enter a capture.

The request has schema `agent-ee.footprint-capture-request/1`:

```json
{
  "schema": "agent-ee.footprint-capture-request/1",
  "library": "ampseal",
  "repository": "fome-mc",
  "commit": "df304f3321296f7e517e0d2e2aa52e1380d52993",
  "kicad_version": "10.0.7",
  "members": ["libs/footprints/ampseal.pretty/ampseal-23.kicad_mod"]
}
```

`repository` is the label recorded in place of the local path. The converter
is `kicad-cli fp upgrade`, run as a process in the networkless executor sandbox.
The module writes no footprint text itself. Capture is refused if:

- `kicad-cli version` differs from `kicad_version`;
- the output holds anything other than the requested members;
- a second, forced `fp upgrade` of the converted library is not byte-identical;
- the shared index does not qualify the converted footprint.

The new destination holds the converted `<library>.pretty/`, a complete
`footprint-libraries.json` manifest, and `capture.json`
(`agent-ee.footprint-capture/1`). For each member, `capture.json` records its
SHA-256 and its source as a pinned-external citation: repository, commit, path,
Git blob id and SHA-256. It also records the two conversion argument lists and
console hashes.

Each first conversion gives legacy items new random UUIDs. A recapture
therefore matches a committed capture only after UUIDs are normalized; the
second conversion of a captured file is byte-identical. 3D model references are
kept as written, unresolved. `verify` re-checks bytes, the manifest and the
index; with `--repository`, it also verifies each source blob offline. Without
it the report is `unverified`.

```sh
python3 -m agent_ee.footprint_capture capture request.json --repo ~/git/fome-mc --out captured-footprints
python3 -m agent_ee.footprint_capture verify captured-footprints --repository ~/git/fome-mc
```

Exit 0 means verified, 1 unverified, 2 refused and 3 an internal error. The
telemetry pod's AMPSEAL-23 header is the first use
([fixture](../fixtures/pod/ampseal/README.md)). That fixture shows the
`Conn_01x23` assignment and a land comparison against TE drawing 776228.
## Footprint-owned keepout rule areas and ESP32-S3-WROOM-1

Native footprint `(zone ...)` nodes that declare `(keepout ...)` on copper
layers (`F.Cu`, `B.Cu`, `In1.Cu`..`In30.Cu`) without a copper net are parsed and
qualified as `rule_areas` on each indexed footprint and attached to its
`footprint_assignment` entry. Each rule-area record retains `uuid`, `name`,
`layers`, `forbid`, `keepout`, `polygon_nm`, `polygon_mm`, and `bounds_mm`;
copper zones with nets, non-copper layers, empty forbid sets, or
self-intersecting polygons refuse coverage. `probe_rule_areas(data, ...)`
produces a sealed `agent-ee.footprint-rule-areas/1` record directly from
captured `.kicad_mod` bytes.

For the telemetry pod's `RF_Module:ESP32-S3-WROOM-1` footprint (KiCad 10.0.7
`RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod`, SHA-256
`d0cfbb31fef0c47396c0d061ec5a3bff60f0567d8d4ea8dd4769c84f52bd656f`), the
committed metadata in `fixtures/telemetry-pod/esp32-s3-wroom-1/` and runtime
helper `capture_esp32_s3_wroom_1` record:

- `footprint-declarations.json`: `thermal_via_pads: ["41"]` for the 12
  `0.2 mm` thru-hole thermal vias sharing exposed pad `41` (the 9 unnumbered
  `F.Paste` apertures are already classified as `paste`).
- `datasheet-facts.json`: four `keepout_dimension` facts citing page 45,
  `Section 11.1 PCB Land Pattern, Figure 11-1. ESP32-S3-WROOM-1 Recommended PCB
  Land Pattern` (`18 mm` body width, `25.5 mm` body length, `6 mm` antenna depth,
  `15 mm` base-board keepout clearance), from which `derive_antenna_keepout`
  derives the `(-24, -6.75)..(24, -27.75) mm` rectangle matching the footprint's
  owned rule area across all 32 copper layers (`rule-area.json`).

