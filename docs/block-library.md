# Known-good block library (AGENTEE-342)

`agent_ee.block_library` keeps a versioned catalogue of placement blocks in
`blocks/`, separate from any project. Each entry has a provenance receipt. The
entries build on the [block candidate](blocks.md#block-candidate-schema) of
AGENTEE-339. Their provenance classes follow
[blocks.md](blocks.md#provenance): a lookup returns each block with its class, so
a reviewer sees, for example, `search-derived (advisory): TI SNVSBF0C Fig. 8-22,
scale-verified` or `search-derived (advisory): project cluster U5 from ...`.

**Hard classes are disabled** (coordinator ruling 2026-10-21, AGENTEE-342 delta-review
HOLD). agent-ee has no enforceable owner or manufacturer trust boundary yet: agents run
as the same user and can author any record, file, catalogue root or CLI argument. Until
AGENTEE-348 delivers a verified authority mechanism, every entry is `search-derived`
and advisory, and every path refuses a hard class with `authority_unavailable`
([Authority](#authority-for-hard-classes)).

```sh
PYTHONPATH=src python3 -m agent_ee.block_library validate
PYTHONPATH=src python3 -m agent_ee.block_library lookup --part LMR36520-DDA
PYTHONPATH=src python3 -m agent_ee.block_library import-board --board evm.kicad_pcb \
  --probe build/evm-probe.json --netlist build/evm.xml --policy design/packing.json \
  --references U1,C1,C2,L1 --anchor U1 --class search-derived --locator "Owned EVM rev A" \
  --id owned-evm --version 1 --part OWNED-BUCK --citation "..." --out build/entry.json
PYTHONPATH=src python3 -m agent_ee.block_library import-figure --figure design/fig-8-22.json \
  --document private/datasheet.pdf \
  --probe ... --netlist ... --policy ... --id ... --version 1 \
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
refused input prints `{"status": "refused", "reason": ...}` and writes nothing; a
hard class or an `--authority` record adds `"kind": "authority_unavailable"`.

## What is stored

An entry stores only numeric records and source identities. It does not store a
board file, netlist, Gerber, figure, courtyard polygon, net name or pin name, so no
third-party design data is published (owner gate). The full
`agent-ee.block-candidate/2` is rebuilt from a board's own footprints whenever the
entry is used ([Materialization](#materialization)).

`agent-ee.block-library-entry/2`. Every field is required and unknown fields refuse:

| Field | Content |
| --- | --- |
| `id`, `version` | Lowercase id (letters, digits, hyphens); version from 1 |
| `part` | Lookup key, for example `LMR36520-DDA` |
| `provenance` | `class` (`search-derived`), `enforcement` (`advisory`), `citation`, `label`; no `source` |
| `layout` | `anchor` and `members[]`: `reference`, `fpid`, `side`, `value` rule, anchor-relative `pose`, `pad_geometry_sha256`, `courtyard_sha256`, `pin_functions_sha256`, `pins` (pad number to `[role, boundary]` or `null`) |
| `copper` | Board-region entries only: framed `tracks`, `arcs` and `vias`, each with its net role; `null` otherwise |
| `receipt` | `importer` (`agent_ee.block_library/1`), `import` path, `source` (`locator`, `sha256`, optional `repository`, `commit`, `path`), `inputs` (`board_sha256`, `inventory_sha256`, `policy_sha256`), `candidate_sha256`; a figure entry adds `figure` and `scale`; an `authority_sha256` refuses with `authority_unavailable` ([Authority](#authority-for-hard-classes)) |

Poses, the frame, value rules and pad-geometry and courtyard hashes are those of
[blocks.md](blocks.md#block-candidate-schema). `pin_functions_sha256` hashes each
pad's native pin name and electrical type. Roles are numbered as in the
candidate; net names never identify a role.

### Retired `/1` identities (AGENTEE-370)

An `agent-ee.block-library-entry/1` entry hashed each courtyard in the vertex
order pcbnew captured. That order changes with the footprint's board rotation, so
a member rotated in the source refused against the same footprint at another
rotation. `/2` hashes the [canonical rings](blocks.md#pose-independent-identity-agentee-370).
A `/1` entry still validates, so a catalogue holding one still loads and its bytes
stay bound to its catalogue hash. It is never used: `materialize` and `reproduce`
refuse it with one `identity_version` problem (`expected` `/2`, `actual` `/1`),
and `add_entry` raises `identity_version`. Import the block again as a new version.
The four committed pod entries are `/1` and need a coordinator re-pin
(AGENTEE-370 result).

### Catalogue and versions

`blocks/catalogue.json` is `agent-ee.block-catalogue/1`: a list of
`{id, version, path, sha256}`. `validate_library(root)` refuses unless:

- each entry is at `blocks/entries/<id>/v<version>.json`;
- the file bytes match the catalogued SHA-256 and are in canonical form
  (sorted keys, one-space indent);
- the entry validates, with the catalogued id and version;
- each id's versions run 1, 2, ... in order;
- no entry carries a hard class or an authority hash (`authority_unavailable`);
  records placed anywhere under the root, including `owner/`, are not consulted.

`add_entry` appends only the next version of an id. It refuses an existing
id/version, so a published version is never rewritten. A correction is a new
version.

`lookup(root, part=, block_id=, version=, classes=)` returns each id's latest
version, unless `version` is given. Results are ordered by authority:
`manufacturer-reference-layout`, `manufacturer-evm`, `qualified-owner-board`,
then `search-derived` (today every result is `search-derived`). Each result carries
`provenance`, `enforcement`, `label` and the entry.

## Authority for hard classes

A hard class (`manufacturer-reference-layout`, `manufacturer-evm`,
`qualified-owner-board`) is disabled. Per the owner ruling of 2026-10-21, pod and
agent work is search-derived and advisory only. Per the coordinator ruling of the
same day, no caller-supplied record, string or file can yield enforcement `hard`:
validation can check a record's structure and binding but cannot authenticate who
wrote it, and every writer in agent-ee runs as the same user. The reviewer showed
that importer-authored records, owner-namespace files in a catalogue root, coherent
relabels and invented figure dimensions all reached `hard` before this ruling.

Every path therefore raises `AuthorityUnavailable` (a `ValueError`, message
`authority_unavailable: ...`):

- `import_board_region` with a hard `provenance_class` (CLI `--class`), before any
  file is read;
- any `authority_raw` record (CLI `--authority`) given to `import_board_region`,
  `import_reference_figure`, `materialize` or `reproduce`;
- `validate_entry` on an entry with a hard class or an `authority_sha256`, and so
  `add_entry`, `validate_library`, `lookup` and `materialize` on such an entry, and
  the CLI `add`, `validate`, `lookup` and `materialize` on a planted catalogue root.

A datasheet figure is downgraded rather than refused: its `class` records what the
document claims to be, and the entry is `search-derived` and advisory.

### Binding kept for AGENTEE-348

The `agent-ee.block-authority/1` format and `verify_authority(raw, entry)` stay, so
the verified mechanism of AGENTEE-348 can use them. Verifying a binding confers
nothing; `validate_entry` still refuses the class.

```json
{"schema": "agent-ee.block-authority/1", "class": "manufacturer-evm", "import": "board-region/1",
 "source": {"locator": "Owned EVM rev A", "sha256": "<source bytes SHA-256>"},
 "references": ["C1", "C3", "L1", "U1"], "payload_sha256": "<payload_sha256(entry)>",
 "capture": {"origin": "<where the vendor bytes were obtained>", "captured_by": "<who captured them>"},
 "owner": "<owner identity>", "disposition": "approved", "reference": "<where the owner recorded it>"}
```

- `payload_sha256` binds the complete immutable payload: `payload_sha256(entry)` is
  the canonical hash of the entry without its `receipt.authority_sha256`, so it covers
  provenance, every layout pose and identity hash, copper, and every receipt identity
  (source, inputs, candidate hash, figure and scale). An approved record does not
  endorse an edited layout, copper item or receipt (`authority_mismatch: record
  payload_sha256 differs`).
- `class`, `import`, `source` and `references` (members in natural order) must equal
  the entry; a qualified owner board names `repository`, full `commit` and `path`.
- A manufacturer class needs `capture` (`origin`, `captured_by`), and the importer
  (`agent_ee.block_library/1`) cannot be the capturer.
- A figure record lists `dimensions` (`basis`, `length_nm`, at least two), and every
  scale reference and check dimension must cite one of them.
- `disposition` must be `approved`; the entry's `authority_sha256` must be the
  record's SHA-256 (`authority_unverified` without bytes, `authority_mismatch` on any
  difference).

A relabelled receipt also refuses on structure:

- a `project-cluster/1` entry can carry no class but `search-derived`;
- a `board-region/1` source must be the probed board itself (source `sha256` equal
  to `inputs.board_sha256`), and a hard class refuses with `authority_unavailable`;
- a `reference-figure/1` entry needs its figure record, with the document hash
  equal to the source.

## Import paths

Each import places the source board's own footprints at the imported poses. It
builds the candidate through `blocks.candidate` with the same graders as stage A
and refuses (`import_refused`) unless the candidate passes on its own evidence:
the placer's full-polygon judgment, the exact courtyard witness at the policy gap
and every internal locality and group grade. An import moves nothing, so policy
fixes (`fixed_refs`, `fixed_overhangs`) on block members are dropped for the
import. Fixes on other parts are kept, and the receipt records the hash of that
policy. Locked members are read as unlocked for the same reason. This read-only
source import is the only place members are unlocked; a target board is always
used with its own lock state ([Materialization](#materialization)). The search
record is `imported-layout/1` with zero steps, and the judgment basis is
`transformed-import-not-native`.

| Import | Classes it may carry | Source |
| --- | --- | --- |
| `board-region/1` | `search-derived`; a hard class refuses (`authority_unavailable`) | The board bytes, which must hash to the probe's `board_sha256` |
| `reference-figure/1` | `search-derived`; the figure records its claimed `manufacturer-reference-layout` or `manufacturer-evm` | The captured document bytes, which must hash to the figure record's `document.sha256` |
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

A check must be independent of the reference dimension, otherwise the figure
refuses with `scale_self_check`. The rules are:

- no check may repeat the reference segment (in either direction), another
  check's segment, or a `basis` already used;
- no check may lie on the reference dimension's own line;
- at least one check must lie at 45 degrees or more to the reference (inclusive),
  so the other figure axis is verified too.

When AGENTEE-348 enables authority, the cited lengths must also be documented by
the figure's [authority record](#authority-for-hard-classes).

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
No validated (hard-class) entry is committed, and none can be while hard classes
are disabled. Seeding one needs the verified owner authority of AGENTEE-348, either
for a captured TI reference-layout figure measured with its scale reference or for a
qualified fome-mc board region.

## Materialization

`materialize(entry, capture, inventory, sheet_names, inventory_sha256, policy, mapping, authority_raw)`
uses an entry on a board. A hard-class entry or any `authority_raw` raises
`AuthorityUnavailable` before anything is built. `mapping` pairs every entry member with a distinct
target reference, identity by default. The result is
`{status: materialized | refused, id, version, provenance, enforcement, label, problems}`,
plus `candidate` only when materialized. Identity follows
[blocks.match](blocks.md#matching): `target_missing`, `fpid`, `side`,
`pad_geometry`, `courtyard`, `pins`, `pin_function`, `value`, then `net_role`,
`net_role_split` and `net_role_merged` over the one-to-one role partition. The
board's footprints are then placed at the entry poses, and the candidate is
rebuilt and re-graded under the board policy. A failed judgment, witness or
grade is listed (`placer_judgment`, `witness`, `grade`) and refuses; it is never
returned as a candidate.

The candidate is built on the target capture as given: members are never unlocked.
It is then passed to `blocks.admit` against the target's own capture, derivation
and policy. Admission binds the sources, recomputes the isolated policy and
deferred record, requires an identity match and re-grades natively. Anything other
than `admitted` (refused, unknown, an error) refuses with an `admission` problem
that carries the admission status and evidence. A locked target member, or a
board-policy fix on a member, refuses as an `admission` problem with stage A's
reason.
The candidate's provenance is the entry's class, enforcement, citation and
source, with `method` set to the import path. Arrangement (AGENTEE-340) decides
how to use it. `hard` means keep the block rigid; `advisory` means a proposal
that may be re-packed.

`reproduce(entry, source_raw, capture, inventory, sheet_names, inventory_sha256, policy, authority_raw)`
replays the receipt. `source_raw` is the source bytes the import read: the board,
the captured document or the cluster-poses file. Their SHA-256 and the board,
netlist and policy hashes must match (`source_mismatch`). The import is then
re-run, and the rebuilt entry must equal the stored one field for field
(`entry_mismatch` lists the fields). `authority_raw` raises `AuthorityUnavailable`. Altered copper, poses, labels or candidate
hashes therefore refuse. A re-import that itself refuses is reported as
`import_refused`.

## Validation

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 AGENT_EE_FOME_MC_CHECKOUT=/home/halbritt/git/fome-mc \
  PYTHONPATH=src:tests python3 -m unittest tests.test_block_library -v
```

`tests.test_block_library` uses the authored AGENTEE-339 fixture. It covers:

- each import path with receipt reproduction;
- the review-HOLD regressions (`AuthorityHoldTests`):
  - admission on the target's own lock state;
  - hard classes refused with `authority_unavailable` on import, and the record
    binding contract, with relabelled cluster receipts refused;
  - replay binding copper and layout;
  - scale self-checks refused;
- the delta-review HOLD regressions (`AuthorityDisabledTests`), one per reviewer
  probe: importer-authored records for each hard class on import, use, replay,
  `add_entry` and a planted catalogue root with an `owner/` record; a coherent
  relabel; a copied, rotated scale check with a fabricated record; an approved
  record against a layout, copper, receipt, input or member edit (payload binding);
  and every CLI path (`--class`, `--authority`, `materialize`, `validate`, `lookup`,
  `add`);
- the owner-ruling assertions on project clusters and on the committed pod entries;
- board-region framing under anchor rotation, with copper selection by net and extent;
- figure scale verification at its inclusive boundary, axis flips, orientation
  and document-hash refusals, and figure-bound poses;
- materialization of a second instance and each identity mismatch;
- materialization with source and target members at every quarter-turn, each
  captured in pose-dependent vertex order, a grown courtyard still refusing, and
  retired `/1` entries refused with `identity_version` (AGENTEE-370);
- a failed re-grade;
- catalogue immutability, byte binding and ordering;
- entry structure refusals;
- the CLI.

Two native tests run under the toolchain. One re-imports the pod entries from
the pinned fome-mc objects. The other probes an authored two-footprint KiCad
board with pcbnew and imports its region with copper.
