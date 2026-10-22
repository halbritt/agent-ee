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

## Pad magnitude bounds (AGENTEE-314)

Every indexed pad must fit a rotation-invariant reach of at most 1000 mm
from the footprint origin before any native load. The bound adds the ceiling
of the pad translation magnitude to the pad's radial extent. Rectangular,
rounded and chamfered pads use the enclosing rectangle's half diagonal;
circles and ovals use half the larger size dimension. Declared trapezoids
include `rect_delta` in that rectangle. Custom pads also bound their anchor
size, alongside the primitive limits below. Declarations cannot bypass these
bounds.

Circular and oval drills use half their larger dimension. Drill offsets add
their magnitude to the copper reach, but offset drill syntax remains outside
the qualified drill grammar. Undeclared trapezoids remain unsupported. This
change does not extend shape qualification or add native geometry fields to
ordinary-pad records. Non-atom pad positions, rotations, custom polygon vertices,
drill dimensions and drill offsets produce footprint-scoped `library_coverage` refusals; other valid
manifest members are still indexed (AGENTEE-324).

Integer square-root ceilings and doubled comparisons retain odd-nanometre
half extents. Excess copper or drill reach produces `library_coverage`, failed
execution and unknown coverage, before pcbnew can silently saturate geometry.
`tests.test_footprint_library.PadReachTests` covers refusals, inclusive bounds
and exact native bounding boxes for rotated and translated near-limit pads.

## Native custom pads (AGENTEE-300)

The index natively qualifies SMD custom pads with an explicit solid zone
connection (`zone_connect 2`), one explicit front/back copper layer,
`clearance outline`, a rectangular or circular
anchor, and `gr_poly`, `gr_rect`, `gr_circle` or `gr_line` primitives. Filled
polygons, rectangles and circles may have zero width; unfilled primitives need
positive width. Each pad is bounded to 128 primitives and each polygon to
1024 vertices. Primitive coordinates are limited to +/-1000 mm and widths to
1000 mm; the complete local copper extent, including circle radius and stroke
expansion, must also fit within +/-1000 mm (AGENTEE-304). In addition, the
rotation-invariant copper reach from the footprint origin must be at most
1000 mm: the ceiling of the pad translation magnitude plus each primitive's
radial reach plus half its width. Rectangle reach includes all four corners;
line and polygon reach includes every vertex; circle reach is its centre
magnitude plus radius. Magnitudes use integer square-root ceilings and the
width comparison preserves half-nanometre expansion. This conservative bound
applies at every rotation, including zero. Excess reach refuses with
`library_coverage`: "custom-pad copper reach exceeds 1000 mm from footprint origin".
Larger geometry
refuses before native loading because KiCad can silently drop or saturate
overflowing primitives. Unsupported options, arcs, padstacks, thermal connections and
inherited zone-connect behavior refuse coverage. Polygon contours must be simple.

After the shared parser checks this subset, the ordinary networkless executor
loads the exact captured footprint bytes with pcbnew in a separate process.
Native pad numbers, types, shapes, anchors, sizes and rotations must match the
captured inventory exactly. Ambiguous custom-pad identities refuse coverage.
Custom pad records retain `geometry: "native_custom"` and `native_geometry`
with `pcbnew_version`, native `number`, `anchor` and the full copper `bbox_nm`
`[left, top, right, bottom]` in footprint coordinates. `size_nm` remains the
anchor size. Forward annotation uses the native copper extent for staging.
A failed child, missing report or mismatched inventory leaves unknown coverage.
No declaration or CLI option is required for this supported subset.

The native merged polygon, including primitives and anchor on the pad's copper
layer, must have exactly one outline (AGENTEE-304). Detached primitives and
unfilled contours separated from the anchor refuse with a `library_coverage`
finding, failed execution and unknown coverage; no footprint record qualifies.
The probe uses pcbnew `Unfracture` to recover hole contours from its merged
representation. Holes within that single outline are allowed as reported by
pcbnew, without Python connectivity approximations. Unfilled contours that touch
or overlap the anchor can qualify. Missing or invalid native outline evidence also refuses.

The existing `custom_pads` declaration still enables numbering only and records
`geometry: "declared_custom"`; it never supplies native geometry evidence.
Declarations cannot bypass these bounds (AGENTEE-324): declared primitives use
the same coordinate, width, local extent and rotation-invariant reach checks,
and declared custom shapes require exactly one merged native copper outline on
every copper layer. Numbering-only declarations retain their broader layer and
zone-connect grammar; they gain no native geometry fields. Missing native
outline evidence, a failed native load or disconnected copper refuses coverage.
Declaration-only native identity comparisons account for full-turn rotation
normalization while retaining the original angles in indexed records. Repeated
declared pad identities can share numbering evidence only when every native
instance meets the outline rule. Native-qualified pads retain their unique
identity requirement. Existing records for ordinary pads are unchanged.
Native copper bounds do not establish a correct land pattern or thermal behavior.

`tests.test_custom_pad_index` uses original fixtures for the supported primitives
and refusal boundaries. Its read-only pinned fome-mc capture probe compares the
39 previous footprint records byte-for-byte and checks electrical numbering for
all 327 native components, including JP1–JP5. Stock footprint bytes are loaded
only at test time from the external Git object store and are not distributed.
`tests.test_forward_annotation` also exercises native custom-pad indexing and
annotation through both CLI commands, then independently measures the saved
board's copper extent.

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
  the footprint mixed technology. Each such via must carry exactly one native
  `(property pad_prop_heatsink)`, or `pad_prop_mechanical` for stitching vias
  such as the edge-mount SMA ground holes. KiCad 10.0.7 skips those pads when it
  infers a footprint's type. A plated via without one makes native DRC type an
  `attr smd` part as through hole (`footprint_type_mismatch`). The index
  therefore refuses such a declaration as incomplete coverage. A declaration
  does not override KiCad's type inference, and no DRC waiver applies. The pad
  property leaves the footprint `smd`, so positions and the CPL keep it as SMD.
  This was the AGENTEE-221 pod defect. `custom_pads` lists numbers whose `custom` or
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
pinless symbols, thermal vias, attr-less footprints and custom pads outside
the native subset remain unknown.

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
[Datasheet-derived footprints](datasheet-footprint.md) use this parity check as
their symbol/footprint publication gate.

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
- `datasheet-facts.json`: three module dimensions citing page 45, Figure 11-1
  (`18 mm` body width, `25.5 mm` body length, `6 mm` antenna depth). The magenta
  `15` in that figure is pin 15, not a base-board clearance. The former fourth
  fact and its symmetric minimum derivation have been withdrawn.
- `rule-area.json`: unchanged captured CAD geometry, the
  `(-24, -6.75)..(24, -27.75) mm` rectangle across all 32 copper layers.
  `probe_rule_areas` still reproduces it from the pinned footprint bytes. This
  establishes what the library contains, not a manufacturer-derived minimum.

`derive_antenna_keepout` now refuses legacy clearance records even if a prior
text-extraction check labeled them verified. It also refuses to derive a minimum
from the three authentic dimensions alone. A numeric token's presence does not
establish its engineering meaning. Dynamic envelope antenna declarations remain
unknown with an actionable reason; no CAD polygon is substituted automatically.

`esp32_s3_wroom_1_placement_policy(case, pdf_path=...)` declares either
`outside_board` or `board_cutout`. `antenna_placement_context(policy,
captured_files)` verifies the safe capture path, exact source bytes/hash and
case-specific page/locator against the reviewed guidelines snapshot SHA-256
`057f6df63e10a2dfc155cf19d942adf5780127d839626e2beb4c1a61efeaf3a5`.
The [hardware design guidelines PDF](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/esp-hardware-design-guidelines-en-master-esp32s3.pdf)
places the outside-board policy on physical page 33 (printed 30), section 1.4.7,
and conditional asymmetric cutout Figure 26 on physical page 34 (printed 31).
The separate 15 mm housing recommendation is retained with its own scope; it is
never converted into board copper clearance. No vendor PDF is committed.

Both context records return `outcome: unknown`, `coverage: missing` and
`source_derived_minimum: null`. They expose no polygon and by themselves grant
no acceptance capability. This is a source/provenance repair, not a general
antenna placement solver. For the `outside_board` case only, the separate
[antenna placement check](antenna-placement.md) (AGENTEE-453) embeds this context
and grades module pose, crossed board edge, feed-point side and corner (the
corner only under an agent-derived class-default slot), and the retained keepout
on the saved board as a bare-board gate. The cutout case,
housing geometry and final RF testing still need qualification.
