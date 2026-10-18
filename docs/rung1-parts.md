# Captured parts for Rung 1

The qualified RC driver accepts a captured `agent-ee.parts/1` bundle through
`--parts-manifest`. The manifest must sit at the root of its dependency tree;
all paths in it keep their existing candidate-relative meaning. Preparation
uses `parts_manifest.qualify_manifest` to replay the complete canonical
selection against the pinned catalog, check CAD assignments and verify every
dependency hash. It never reads a live supplier catalog or invents manufacturer,
MPN, tolerance, stock or rating data.

```sh
PYTHONPATH=src python3 -m agent_ee.rung1_qualified \
  fixtures/rung1/spec-assembled.md --parts-manifest PRIVATE_BUNDLE/parts.json \
  --out RUNDIR --dry-run
```

The bundle uses the existing [approved-parts schema](approved-parts.md),
[selection constraints](part-selection.md) and [catalog snapshot](catalog.md).
Supply a complete canonical selection record and an explicit declaration
source. Hashes and replay establish captured identity; they do not authenticate
owner approval. This entry point consumes the declaration and does not create
an owner evidence store.

The three parts must be required, single root-sheet instances with the spec's
references and these CAD roles:

| Role | Symbol | Footprint |
| --- | --- | --- |
| R1, 10k | `Device:R` | `Resistor_SMD:R_0805_2012Metric` |
| C1, 100n | `Device:C` | `Capacitor_SMD:C_0805_2012Metric` |
| J1, 1x03 | `Connector_Generic:Conn_01x03` | `Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical` |

R/C selection must have the spec's nominal value, explicit tolerance and exact
0805 package. The canonical selector retains eligible/rejected rows and ranks
Basic before Preferred before Extended, then price, available stock and LCSC
number. The rung adapter refuses missing roles, optional or hierarchical
instances, wrong CAD/category assignments and a nominal value that differs
from the spec. The driver also compares the selected symbol definitions and
footprint hashes with the stock CAD it will actually emit; matching library
names alone cannot substitute different symbol definitions or footprint bytes.

J1 is **assembled** on this route and needs its own catalog selection. The
assembled-header spec records that choice. Supplying the original hand-soldered
spec is refused before any native output: v1 fab export refuses populated BOM
exclusions, and forward annotation refuses DNP. No authored assembly field
bypasses those checks. This software route does not establish that the supplier
will assemble a particular through-hole part.

Preparation retains the exact manifest as `approved-parts.json`, its complete
closure and selection result, plus the experimental stage-2 receipt and
`generation/parts-manifest.json` property projection. The scaffold captures the
closure at its declared paths; collisions with project, requirements, generation
or stock inputs are refused. The baseline freezes the manifest, declaration,
selection, catalog manifest/database, CAD records/members and datasheet blobs.
A changed catalog needs a newly pinned manifest and canonical selection; it
cannot alter a previously captured design or receipt.

The owned schematic emitter writes `part_binding` string properties, including
`LCSC`, `Manufacturer`, `Partnumber` and normalized `PARAM_*` ratings. It replaces
matching library defaults and emits new fields hidden. Binding data cannot
override native identity or population flags. Forward annotation copies those
native-exported fields onto board footprints. `part_binding` grades their
schematic/board parity, and `fab_export` grades BOM/CPL designator agreement.

The captured-parts scope also runs the existing `parts_subset`,
`assembly_readiness` and `package_consistency` checks. They use the normal
coordinator evidence and sealing rules. The package check captures the committed
KiCad/LCSC mapping; a header package outside that map stays unknown. Missing
stock freshness or ratings and required unavailable datasheet provenance also
stay unknown. Without `--parts-manifest`, the older experimental BOM route
remains available and explicitly lists these three checks as missing coverage.

## Real reference qualification (2026-10-17)

A private run used the repository-pinned jlcparts feed dated 2026-09-14.
Its full snapshot has 334,455 rows, manifest SHA-256
`6f5a2767e6a7f54840e9bd17cccd1ebe54585d0c1b7d18049cb141013eda2d09`
and database SHA-256
`a32e101342e43e2358d74a0a19d0e63231102de802461a97a0582f025f461a11`.
The shared loader verified that snapshot before extraction. The explicit role
view retained 102 eligible resistor rows, 120 capacitor rows and 12 header rows.
It records the full-source manifest/database/row hashes and upstream provenance
in every row, and the importer identity and filters in the declaration. This
view is an authored extract of captured supplier facts, not an upstream CSV.

| Role | LCSC | Manufacturer / MPN | Tier |
| --- | --- | --- | --- |
| R1 | `C17414` | UNI ROYAL Uniroyal Elec / `0805W8F1002T5E` | Basic |
| C1 | `C49678` | YAGEO / `CC0805KRX7R9BB104` | Basic |
| J1 | `C6332196` | Hanbo Electronic / `HB-PH3-25413PB2GOP` | Extended |

The constraints were 10k ±1%, 0805, at least 0.1 W; 100n ±10%, 0805,
at least 25 V; and a 3-pin, single-row, 2.54 mm through-hole pin header rated
for at least the 1 V stimulus. Manufacturer and MPN must be nonempty ASCII for
verbatim BOM export. Build quantity was 10, with a stock floor of 100. Values,
stock and tiers describe the captured date. The repaired owned importer maps
the feed's `Voltage Rating` and `Voltage Rating (Max)` fields before the
explicit derived capture; the old full snapshot is unchanged.

Canonical selection on the role view chose these three parts. Its manifest
SHA-256 is
`af572a05d08ae010c382abde77a218f86692ca8d80415f742dbc92dfa89d470a`;
the `parts/1` manifest SHA-256 is
`9997d0604460fe23ea7a93fcd13dc738e7778fbe18d99e1a5eb490dc7e9fe14a`.
The owned stage-2 adapter froze that bundle. On a private copy of the original
RC reference, only symbol binding fields were written, then owned forward
annotation updated three footprints: zero adds/removals, net renames, new nets
or pad changes. Native ERC, DRC, geometry, RC response, binding, membership,
assembly readiness and fab export passed. The BOM has the three populated LCSC
cells above; BOM/CPL/board designators agree. Public `verify` succeeded.
A separate capture changed only valid CSV record line endings: the
snapshot and canonical selection hashes changed, the projected fields and
original sealed board stayed unchanged, and the original run still verified.

The package check ran: R1/C1 match, J1 is unknown because the generic catalog
package `插件,P=2.54mm` is absent from the captured mapping. Header body/lead
geometry and straight-versus-right-angle qualification remain missing coverage.
This checks-only run is incomplete overall and establishes neither a completed
empty-candidate campaign nor hardware release. Private source volumes, CAD
captures, manifests and fab outputs are not published with this code. Regression
tests use original synthetic catalog claims; they do not substitute for this
separate real-data probe.
