# Block inventory capture (AGENTEE-105)

`python -m agent_ee.block_inventory capture INVENTORY --repo LOCAL --out NEW_DIRECTORY`
turns one design's block plan into draft [`agent-ee.block/1`](reference-block-interface.md)
entries, one private directory per block. Every entry it writes is
**unverified**: the validator exits 1 for each. A block becomes verified only
through the existing run path, with a complete sealed run, its requirement
coverage and an independent review (AGENTEE-106/107 for the rung-2 blocks).

The tool and its tests carry no design of the owner's. Every fixture in the
repository is the original two-channel design in
`tests/block_inventory_fixture.py`. The owner's fome-mc inventory and its
captures are private material. See [fome-mc capture](#fome-mc-capture-private-agentee-118).

```sh
PYTHONPATH=src python3 -m agent_ee.block_inventory capture INVENTORY.json \
  --repo LOCAL_DESIGN --library-repo NAME=LOCAL_LIBRARY_REPO --out NEW_PRIVATE_DIRECTORY
```

## Inventory document

`agent-ee.block-inventory/1` has exactly `schema`, `design`, `documents`,
`unassigned` and `blocks`.

- `design`: `name`, full 40-hex `commit`, `root` schematic, `license` statement,
  `plan` (a `documents` key) and optional `external_libraries` (sibling directory
  name to full commit, for `${KIPRJMOD}/../NAME/` library-table URIs).
- `documents`: names mapped to repository paths. Each block captures the ones its
  requirements cite under `sources/`, so requirement sources are captured files.
- `unassigned`: `{instance, references, reason}` for native components that
  belong to no block, for example root-sheet connectors. `references` may be
  `"all"` for a whole sheet occurrence outside every captured block. A
  single-block inventory such as the [PSU block](fome-psu-block.md) uses it. Every
  native component must still be accounted for, and `"all"` on an occurrence that
  holds a block reference is refused.
- `blocks`: `id` (lowercase slug), `role`, `title`, `origin`, `instances`
  (`{path, references}`, where `references` is `"all"` or an explicit list),
  `boundary` (`{document, locator}`, the source of the interface requirement) and
  `pending` requirements (`id`, `statement`, `document`, `locator`, `conditions`,
  `blocked_by`).

## What capture does

1. Resolves the inventory commit exactly in the local repository and reads only
   committed blobs. Working-tree edits are never read and the source is never
   written.
2. Copies the commit's `.kicad_sch` and `.kicad_pro` blobs into a private
   temporary directory and runs `kicad-cli sch export netlist --format kicadxml`
   inside the network-less executor sandbox. Connectivity comes from the shared
   `hardware_interface.read_inventory` reader. Component properties come from
   `native_netlist.read_components`.
3. Walks the pinned sheet hierarchy (`Sheetname`/`Sheetfile`) to map instance
   paths such as `/CH1/` to native UUID paths and sheet files. It then requires
   every native component to belong to exactly one block or to an `unassigned`
   entry.
4. For each block it writes a draft `block.json` through
   `reference_blocks.capture_block`. The generated files are:
   - `interface.json`: an `agent-ee.interface/1` contract. It has a `component`
     invariant and a `value` invariant (non-empty values only) for every
     reference-set part. It has a `named_net` invariant for every reference-set
     terminal on a named net that also has terminals outside the reference set.
     The contract must hold on the export it came from, otherwise capture fails.
     It restates the pinned design, so it is a draft that needs owner approval.
   - `connectivity.json` (`agent-ee.block-connectivity/1`) records the conductor
     of each reference-set terminal. A conductor is a named net, an
     `anonymous:REF.PIN` group for a generated `Net-(...)` name, or `null` for a
     disconnected pin.
   - `parts.json` (`agent-ee.block-parts/1`) lists, per reference, the instance,
     lib_id, value, footprint, populated state and the native `LCSC`,
     `Manufacturer_Part_Number` and `Manufacturer_Name` properties. With
     `--catalog DIR --catalog-manifest-sha256 SHA`, it also records the
     snapshot identity from `catalog.load_catalog` and whether each LCSC number is
     present. Presence is not a selection or availability claim.
5. Boundary port `net_name` values come from source geometry: the hierarchical
   label's conductor is followed to component pins in the sheet occurrence, then
   to their native net. This is the label rule of `hardware_interface`: wires join
   at shared endpoints or junctions, and pins connect only at wire ends.
   Placement atoms are first rounded to KiCad's 100 nm unit, as eeschema does on
   load. Pre-instance (20211123) sheets get eeschema's default `(unit 1)`. A label
   that reaches no pin falls back to the exact native net
   `INSTANCE_PATH + LABEL`. A label that reaches several nets, or none, is
   refused. `voltage_domain` and `current_a` stay `null` because they are owner
   declarations.
6. Writes everything to a staging directory next to `--out`, then publishes it
   with a no-replace rename. A refusal publishes nothing, and an existing
   destination is refused.

Each block's requirements are `ID-native` (checks `erc`, `netlist`) and
`ID-interface` (check `interface`, kind `hardware_interface`, on `interface.json`)
in its `check_set`. The inventory's functional requirements become
`pending_requirements`: they have sources and conditions but no applicable check
yet, and they keep the block unverified.

`inventory-report.json` records the commit, the inventory identity, the
exporter version and console, and each block's status, `block.json` and
`block-contract.json` SHA-256 values. Two captures of the same commit with the
same KiCad and stock libraries produce byte-identical directories.

## Libraries

Every symbol `lib_id` and footprint used by the reference set is resolved
through the pinned library tables:

| Origin | When | Recorded |
| --- | --- | --- |
| `source` | `${KIPRJMOD}/...` file committed in the design | Captured file and its hash |
| `kicad-stock` | Stock URI or a nickname absent from the project table, present under `--stock-root` (default `/usr/share/kicad`) | Stock-relative path, SHA-256 and KiCad version, not copied |
| `external` | `${KIPRJMOD}/../NAME/...` with `NAME` declared and `--library-repo NAME=PATH` given | Path and blob SHA-256 at the declared commit, not copied |
| `unresolved` | Anything else, for example a nickname in no table | `null` path and hash; keeps the block unverified |

Symbols are embedded in the sheets. The library entry records where the named
library would come from, not proof that the embedded copy matches it.

## Adaptation fixture (AGENTEE-90)

`python -m agent_ee.block_inventory adapt SOURCE_BLOCK TARGET_BLOCK --parameters JSON`
validates both captured entries, then applies the parameters to the source's
connectivity:

- `reference_offset` is added to every designator number.
- `renames` are ordered `[old, new]` substring replacements on named nets.
- `straps` are `{reference, pin, net}` terminals on the adapted references; `null`
  means open.

It compares the result with the target terminal by terminal. Anonymous groups
are compared by membership, not by generated names. It prints the differences
and exits 0 only when there are none.

In the owned fixture, channel 2 is channel 1 with references +100, the
`CH1`/`CH2` rename and a moved strap. Without straps the copy differs from the
captured channel 2 only at U201 pin 3 (GND in the copy, open in channel 2) and
pin 4 (open in the copy, VDD in channel 2). With the strap (pin 3 open, pin 4 to
VDD) there are no differences. Values, symbols, footprints and every other
terminal match. This tests connectivity only: an adapted block still needs a
fresh run and review.

An `agent-ee.block-adaptation-fixture/1` document records one such expectation:
`design`, `source` and `target` block directories, `description`, `parameters`
and `expected_without_straps` (the exact difference list).

## Private qualification

`scripts/qualify_block_inventory.py` is a manual host-side step. It is not part
of the discovered test suite. It captures the inventory's pinned commit twice
into a new output directory and requires both captures to be byte-identical.
Every inventory block must be an unverified entry with no runs. Each adaptation
fixture must give exactly its expected differences without straps and none with
them. It writes `qualification.json` beside the two captures. Exit status is 0
when qualified, 1 when a check fails and 2 when refused.

The output directory must lie outside any Git work tree or be ignored by it.
Otherwise the script refuses before writing, because captures copy design files.
Its defaults point at the owner's private fome-mc layout:

| Setting | Default |
| --- | --- |
| `AGENT_EE_FOME_MC_CAPTURE` | `build/fome-mc-inventory` (ignored through `build/`), holding `block-inventory.json` and adaptation fixtures |
| `AGENT_EE_FOME_MC_CHECKOUT` | `~/git/fome-mc` |
| `--out` | `CAPTURE/captures/COMMIT12-N`, the first unused `N` |
| `--adaptation` | every adaptation fixture beside the inventory |
| `--library-repo` | each declared external library's sibling checkout of `--repo` |

The strict suite runs this script on the owned fixture only
(`QualificationScriptTests`). It never needs fome-mc and never skips on its
account.

## fome-mc capture (private, AGENTEE-118)

The fome-mc inventory, its adaptation fixture and every capture stay out of this
repository until the owner decides on publication (AGENTEE-118). fome-mc has no
licence file, and its sheets derive from upstream checkouts without one. Even
the inventory's facts are derived design structure: sheet paths, reference
designators, boundary net names, strap pins and requirement paraphrases.

The inventory was first written in commit `219dd68` on the local branch
`impl/ee105-fome-inventory`. That commit is not for publication. From a local
clone that holds it, restore the files to the private directory and qualify:

```sh
mkdir -p build/fome-mc-inventory
git show 219dd68:references/fome-mc/block-inventory.json > build/fome-mc-inventory/block-inventory.json
git show 219dd68:references/fome-mc/wbo1-to-wbo2.json > build/fome-mc-inventory/wbo1-to-wbo2.json
PYTHONPATH=src python3 scripts/qualify_block_inventory.py
```

With fome-mc at its pinned commit and `~/git/kicad-libraries` beside it, the
result covers every planned block. All are unverified with no runs, and the
channel adaptation fixture reproduces its two expected strap differences.

## Limits

- A boundary port for a reference-set block is still a hierarchical label of the
  primary sheet, as `agent-ee.block/1` requires. A block cut out of a larger
  sheet therefore lists every label of that sheet. The block's actual boundary is
  the interface contract.
- Native coverage credit in `reference_blocks` reads only the primary sheet.
  Multi-instance and reference-set blocks are not yet qualified by a run.
- The library hash identifies the host's stock library. Another KiCad install
  gives different stock hashes and therefore different entries.
- Without `--catalog`, `parts.json` has `catalog: null`.
