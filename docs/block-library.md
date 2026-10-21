# Known-good block library (AGENTEE-342)

`agent_ee.block_library` keeps a versioned catalogue of placement blocks in
`blocks/`, separate from any project. Each entry has a provenance receipt. The
entries build on the [block candidate](blocks.md#block-candidate-schema) of
AGENTEE-339. Their provenance classes follow
[blocks.md](blocks.md#provenance): a lookup returns each block with its class, so
a reviewer sees, for example, `TI SNVSBF0C Fig. 8-22, scale-verified` versus
`search-derived (advisory): project cluster U5 from ...`.

```sh
PYTHONPATH=src python3 -m agent_ee.block_library validate
PYTHONPATH=src python3 -m agent_ee.block_library lookup --part LMR36520-DDA
PYTHONPATH=src python3 -m agent_ee.block_library import-board --board evm.kicad_pcb \
  --probe build/evm-probe.json --netlist build/evm.xml --policy design/packing.json \
  --references U1,C1,C2,L1 --anchor U1 --class manufacturer-evm --locator "Owned EVM rev A" \
  --id owned-evm --version 1 --part OWNED-BUCK --citation "..." --out build/entry.json
PYTHONPATH=src python3 -m agent_ee.block_library import-figure --figure design/fig-8-22.json \
  --document private/datasheet.pdf --probe ... --netlist ... --policy ... --id ... --version 1 \
  --part ... --citation ... --out build/entry.json
PYTHONPATH=src python3 -m agent_ee.block_library import-cluster --cluster-poses cluster-poses.json \
  --cluster U5 --repository ... --commit <40 hex> --path ... --probe ... --netlist ... --policy ... \
  --id ... --version 1 --part ... --citation ... --out build/entry.json
PYTHONPATH=src python3 -m agent_ee.block_library add --entry build/entry.json
PYTHONPATH=src python3 -m agent_ee.block_library materialize --entry blocks/entries/<id>/v1.json \
  --probe build/placement-probe.json --netlist build/design.xml --policy design/packing.json \
  --mapping design/mapping.json --out build/block.json
```

Imports and `materialize` write a new `--out` file; an existing file is refused.
Each command prints one JSON status line. The exit code is 0 for `valid`, a
nonempty lookup, `added`, `imported` and `materialized`, and 1 otherwise. A
refused input prints `{"status": "refused", "reason": ...}` and writes nothing.

## What is stored

An entry stores only numeric records and source identities. It does not store a
board file, netlist, Gerber, figure, courtyard polygon, net name or pin name, so no
third-party design data is published (owner gate). The full
`agent-ee.block-candidate/2` is rebuilt from a board's own footprints whenever the
entry is used ([Materialization](#materialization)).

`agent-ee.block-library-entry/1`. Every field is required and unknown fields refuse:

| Field | Content |
| --- | --- |
| `id`, `version` | Lowercase id (letters, digits, hyphens); version from 1 |
| `part` | Lookup key, for example `LMR36520-DDA` |
| `provenance` | `class`, `enforcement`, `citation`, `label`; a hard class adds `source: {locator, sha256}` equal to the receipt source |
| `layout` | `anchor` and `members[]`: `reference`, `fpid`, `side`, `value` rule, anchor-relative `pose`, `pad_geometry_sha256`, `courtyard_sha256`, `pin_functions_sha256`, `pins` (pad number to `[role, boundary]` or `null`) |
| `copper` | Board-region entries only: framed `tracks`, `arcs` and `vias`, each with its net role; `null` otherwise |
| `receipt` | `importer` (`agent_ee.block_library/1`), `import` path, `source` (`locator`, `sha256`, optional `repository`, `commit`, `path`, `owner_record`), `inputs` (`board_sha256`, `inventory_sha256`, `policy_sha256`), `candidate_sha256`; a figure entry adds `figure` and `scale` |

Poses, the frame, value rules and pad-geometry and courtyard hashes are those of
[blocks.md](blocks.md#block-candidate-schema). `pin_functions_sha256` hashes each
pad's native pin name and electrical type. Roles are numbered as in the
candidate; net names never identify a role.

### Catalogue and versions

`blocks/catalogue.json` is `agent-ee.block-catalogue/1`: a list of
`{id, version, path, sha256}`. `validate_library(root)` refuses unless:

- each entry is at `blocks/entries/<id>/v<version>.json`;
- the file bytes match the catalogued SHA-256 and are in canonical form
  (sorted keys, one-space indent);
- the entry validates, with the catalogued id and version;
- each id's versions run 1, 2, ... in order.

`add_entry` appends only the next version of an id. It refuses an existing
id/version, so a published version is never rewritten. A correction is a new
version.

`lookup(root, part=, block_id=, version=, classes=)` returns each id's latest
version, unless `version` is given. Results are ordered by authority:
`manufacturer-reference-layout`, `manufacturer-evm`, `qualified-owner-board`,
then `search-derived`. Each result carries `provenance`, `enforcement`, `label`
and the entry.

## Import paths

Each import places the source board's own footprints at the imported poses. It
builds the candidate through `blocks.candidate` with the same graders as stage A
and refuses (`import_refused`) unless the candidate passes on its own evidence:
the placer's full-polygon judgment, the exact courtyard witness at the policy gap
and every internal locality and group grade. An import moves nothing, so policy
fixes (`fixed_refs`, `fixed_overhangs`) on block members are dropped for the
import. Fixes on other parts are kept, and the receipt records the hash of that
policy. Locked members are read as unlocked for the same reason. The search
record is `imported-layout/1` with zero steps, and the judgment basis is
`transformed-import-not-native`.

| Import | Classes it may carry | Source |
| --- | --- | --- |
| `board-region/1` | any; `qualified-owner-board` needs `repository`, `commit` and `owner_record` | The board bytes, which must hash to the probe's `board_sha256` |
| `reference-figure/1` | `manufacturer-reference-layout`, `manufacturer-evm` | The captured document bytes, which must hash to the figure record's `document.sha256` |
| `project-cluster/1` | `search-derived` only | The cluster-poses file, with `repository`, full `commit` and `path` |

### (a) KiCad board region

`import_board_region` selects footprints by reference set. Each member pose is
framed on the anchor; a rotation that is not a quarter turn refuses. Copper is read from
the board file with the shared S-expression parser: segments, arcs and vias whose
net (named, or numbered through the board net table) is a block net and whose
points all lie inside the members' courtyard extent. They are framed like the
poses and recorded by role. Zones are not captured.

### (b) Datasheet reference-layout figure

The figure is a numeric `agent-ee.reference-layout-figure/1` record (see
`validate_figure`). The figure itself is never committed.

```json
{"schema": "agent-ee.reference-layout-figure/1", "class": "manufacturer-reference-layout",
 "document": {"locator": "TI SNVSBF0C", "sha256": "<captured document SHA-256>", "figure": "Fig. 8-22", "page": 30},
 "axes": "x-right-y-down", "anchor": "U1",
 "scale": {"reference": {"from": [0, 0], "to": [40, 0], "length_nm": 5000000, "basis": "dimensioned 5 mm"},
           "checks": [{"from": [12.5, 40.0], "to": [22.66, 40.0], "length_nm": 1270000,
                       "basis": "pad pitch 1.27 mm"}],
           "tolerance": [1, 50]},
 "members": [{"reference": "U1", "pads": {"1": [12.5, 40.0], "8": [12.5, 31.1]}}]}
```

This follows AGENTEE-337's datasheet method: a measurement of the recommended
layout with its scale reference recorded. The reference dimension sets the scale
in nanometres per figure unit. The squared scale is kept exact; `verify_scale`
then checks every independent dimension. A check passes when its scaled figure
length lies within the relative tolerance of its cited length, both ends
inclusive. Any failing check refuses with `scale_unverified`, and at least one
check is required.

Each member measures at least two pad centres. Its rotation is the quarter
turn whose footprint-local pad positions fit the measurements best. The fit must
be within the tolerance of the measured pads' span, otherwise the import refuses
with `member_orientation_unresolved`. The origin is the mean of the pad-implied
origins. The entry keeps the figure record and the scale verification, and
validation recomputes the scale. Materialization re-derives the poses from the
figure and the target's lands, and an edited pose refuses as `figure_pose_mismatch`.

### (c) Reviewed project clusters

`import_project_cluster` reads `{anchor: {reference: [dx_nm, dy_nm, rotation]}}`
with the anchor at the origin, rotation 0. Owner ruling (2026-10-21): the
telemetry pod's hand-placed clusters are imported as advisory. They were arranged
against agent-chosen bounds (D-PL-1), and the pod has not been fabricated or
tested. The function has no class argument. Validation refuses a
`project-cluster/1` entry with any class other than `search-derived`, and
`tests.test_block_library` asserts both.

`scripts/import_pod_blocks.py` reads fome-mc `7a8baa137bb5072016c49b5b57c1e38bf0634cff`
from the local object store only:

- iteration 3 `cluster-poses.json`, `seed.kicad_pcb.gz` and `request.json`;
- `revised-native/native.xml`, whose bytes `inputs/input-hashes.txt` names as
  `inputs/native.xml`;
- the D-PL-1 `electrical-locality.json`.

It probes the seed natively in a temporary directory and writes the four
committed entries:

| Entry | Part | Members | Internal locality pairs |
| --- | --- | ---: | ---: |
| `pod-lmr36520-dda-u5` | LMR36520-DDA | 12 | 11 |
| `pod-lmr36520-dda-u7` | LMR36520-DDA | 13 | 11 |
| `pod-tps62162-dsg-u15` | TPS62162-DSG | 5 | 7 |
| `pod-lm2903-soic8-u26` | LM2903-SOIC8 | 3 | 4 |

`--check` re-imports and requires byte-identical, catalogued entry files.
No validated (hard-class) entry is committed yet. Seeding one needs a captured TI
reference-layout figure measured with its scale reference, or an owner-recorded
qualified fome-mc board region.

## Materialization

`materialize(entry, capture, inventory, sheet_names, inventory_sha256, policy, mapping)`
uses an entry on a board. `mapping` pairs every entry member with a distinct
target reference, identity by default. The result is
`{status: materialized | refused, id, version, provenance, enforcement, label, problems}`,
plus `candidate` only when materialized. Identity follows
[blocks.match](blocks.md#matching): `target_missing`, `fpid`, `side`,
`pad_geometry`, `courtyard`, `pins`, `pin_function`, `value`, then `net_role`,
`net_role_split` and `net_role_merged` over the one-to-one role partition. The
board's footprints are then placed at the entry poses, and the candidate is
rebuilt and re-graded under the board policy. A failed judgment, witness or
grade is listed (`placer_judgment`, `witness`, `grade`) and refuses; it is never
returned as a candidate. A board-policy fix on a member refuses, as in stage A.
The candidate's provenance is the entry's class, enforcement, citation and
source, with `method` set to the import path. Arrangement (AGENTEE-340) decides
how to use it. `hard` means keep the block rigid; `advisory` means a proposal
that may be re-packed.

`reproduce(entry, ...)` re-runs the receipt on its recorded inputs. The board,
netlist and policy hashes must match, and the rebuilt candidate must hash to
`candidate_sha256`.

## Validation

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 AGENT_EE_FOME_MC_CHECKOUT=/home/halbritt/git/fome-mc \
  PYTHONPATH=src:tests python3 -m unittest tests.test_block_library -v
```

`tests.test_block_library` uses the authored AGENTEE-339 fixture. It covers:

- each import path with receipt reproduction;
- the owner-ruling assertions on project clusters and on the committed pod entries;
- board-region framing under anchor rotation, with copper selection by net and extent;
- figure scale verification at its inclusive boundary, axis flips, orientation
  and document-hash refusals, and figure-bound poses;
- materialization of a second instance and each identity mismatch;
- a failed re-grade;
- catalogue immutability, byte binding and ordering;
- entry structure refusals;
- the CLI.

Two native tests run under the toolchain. One re-imports the pod entries from
the pinned fome-mc objects. The other probes an authored two-footprint KiCad
board with pcbnew and imports its region with copper.
