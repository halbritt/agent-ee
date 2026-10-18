# fome-mc PSU block, the rung-2 power reference (AGENTEE-106)

`fixtures/fome-mc/psu/` holds the fome-mc power supply as an `agent-ee.block/1`
entry. It also holds the original standalone board and the scope/2 review that
grades the block. The source is `sheets/psu.kicad_sch` at fome-mc commit
`df304f3321296f7e517e0d2e2aa52e1380d52993`, minus the CAN transceivers. This
commit carries FOMEMC-17 (`5954c3a`, the last change to the sheet before the
pin). The material is committed under the owner decision of 2026-10-18. Its
rights and capture rules are in `fixtures/fome-mc/LICENSE-NOTICE.md`,
which AGENTEE-105 adds, and its origin is `T16` in
[`file-origins.json`](file-origins.json). `T16` is owner-confirmed, but the
sheet derives from an upstream donor design (`T3`, unresolved), which `T16` lists in
`embedded_origins`. The [origin check](third-party-origins.md) therefore reports
every `T16` file as unresolved until the owner rules on that embedded content.

| Path | Content | Origin |
| --- | --- | --- |
| `inventory.json` | PSU-only [block inventory](block-inventory.md): the `psu` block, its sourced pending requirements, every other sheet occurrence unassigned | Written for agent-ee |
| `capture/` | Output of `agent_ee.block_inventory capture`: `psu/` block entry and `inventory-report.json` | Generated, with byte copies of the fome-mc sheet and its project libraries |
| `board/` | Original standalone board, its interface contract and their generator | Written for agent-ee |
| `requirements.md`, `review.scope.json` | Review requirements and the scope/2 review | Written for agent-ee |

## Capture

The capture went through the offline Git path. Only objects at the pinned commit
were read: the owner's checkout had local edits and another `HEAD`, and its
working tree was not read. Nothing was copied by hand.

```sh
PYTHONPATH=src python3 -m agent_ee.block_inventory capture fixtures/fome-mc/psu/inventory.json \
  --repo ~/git/fome-mc --repository-label fome-mc --out NEW_DIRECTORY
```

`source.repository` records the label `fome-mc`, not a local path.
`capture/psu/block.json` records `source.commit` and, in `files`, the SHA-256 of
every payload file. Its `source.paths` map each payload file to its fome-mc path.

| Payload | fome-mc path | SHA-256 |
| --- | --- | --- |
| `sheets/psu.kicad_sch` | `sheets/psu.kicad_sch` | `56b2f632b74713032e0434dd8cedae496b24c5e05ee5d71b4f5e7e39976f5dcb` |
| `libs/symbols/lmr14020.lib`, `libs/symbols/infineon_psu.lib`, `libs/footprints/sunlord-MWSA0503.pretty/MWSA0503.kicad_mod` | same paths | in `block.json` `files` |

The planning documents are not captured. The inventory sets `cite_documents`,
so every requirement and contract source is a
[pinned-external citation](reference-block-interface.md#external-source-citations):

| Document | Git blob | SHA-256 |
| --- | --- | --- |
| `docs/DESIGN.md` | `53b60f15a26a5e0db05d2168a93a84df65188adb` | `2313a4fb7835d0621c45fe01b9acbd193ad22dcd95d25bc2b70706f614e026ae` |
| `docs/BLOCK_LIBRARY.md` | `ffa17817a39b5ce12f3c071230d2a395a95f0efd` | `9544f3b3eda941cf79ad69f574832dd7844685fbc1e837baaadfaaded257859a` |

Each citation names repository `fome-mc`, commit `df304f3…` and a section
locator. `validate_block` reports them in `external_sources`. They are
`unverified` unless `--repository fome-mc=PATH` names a local object store
holding the commit, and an unverified citation keeps the block unverified.

`inventory-report.json` binds the inventory identity, the exporter
(`kicad-cli 10.0.7`) and the SHA-256 of `block.json` and `block-contract.json`.
The block's libraries are all project (`source`) or KiCad stock libraries, so no
external library checkout is needed. The inventory still declares
`kicad-libraries` for the design.

Two captures inside `bwrap --unshare-net`, with the network confirmed
unreachable, gave byte-identical directories. Both matched the committed
`capture/`, including the native `kicad-cli` export.

The block is the 31 power parts of the `/psu/` occurrence
(`/00000000-0000-0000-0000-00005da72eff/`). The CAN set U4, U5, D4, D5, R7, R8, R9
and R10 is excluded and named in `unassigned`. Every other fome-mc sheet
occurrence is `unassigned` with `references: "all"`, so the capture still
accounts for every native component of the design.

## Contract

`capture/psu/interface.json` is the derived `agent-ee.interface/1` contract.
Every invariant belongs to requirement `psu-interface`:

- One `component` and one `value` invariant per part. For example D1 `SMCJ24A`,
  U3 `LMR16020PDDAR`, R5 `12k`, R6 `68k` and C12 `10u`.
- One `named_net` invariant per block terminal on a net that leaves the block.
  Together they cover each boundary net: `/12V_RAW` (in), `+5V`, `+3V3`,
  `/5V_SENSOR_1`, `/5V_SENSOR_2`, `/VBOOST`, `/mcu/5V_SENSOR_1_PG`,
  `/mcu/5V_SENSOR_2_PG`, and the shared `GND`.

The capture checks that the contract holds on its own export before it writes
anything.

## Requirements

`psu-native` and `psu-interface` are the bound block requirements. Eight more are
pending requirements, each with a cited source section, its conditions and
the item it waits on. The five known PSU findings are:

| Requirement | Finding | Source |
| --- | --- | --- |
| `psu-tvs-clamp` | FOMEMC-7, TVS clamp against downstream absolute maxima | `DESIGN.md` 6.3 item 1 |
| `psu-c12-part` | FOMEMC-15, C12 value/part (DESIGN.md credits the upgrade to FOMEMC-13) | `DESIGN.md` 6.3 item 2 |
| `psu-c4-rating` | FOMEMC-17, C4 16 V on the 12 V rail | `DESIGN.md` 6.3 item 2 |
| `psu-brownout-floor` | FOMEMC-17, brownout margin and guaranteed floor | `DESIGN.md` 6.3 item 3 |
| `psu-reverse-block` | Q1 AO4485 reverse-battery block, which replaced the series SS34 | `DESIGN.md` 6.3 item 1 |

The remaining three are `psu-rails` (rail accuracy, `BLOCK_LIBRARY.md`),
`psu-capacitor-ratings` (every capacitor rated above its rail clamp) and
`psu-input-filter` (the L1/C6 filter). The table with the checks is in
[`requirements.md`](../fixtures/fome-mc/psu/requirements.md).

## Standalone board

`board/generate.py` writes the board. It copies no fome-mc file content:

- An original root sheet. It includes the captured `sheets/psu.kicad_sch`
  unchanged and reuses the fome-mc root and `/psu/` sheet UUIDs, so the captured
  symbols keep their references.
- An `mcu` stub, so the power-good and CAN logic nets keep their native names.
- Headers J1 (`/12V_RAW`, GND), J2 (`/12V_PROT`, `/VBOOST`, both sensor rails, GND)
  and J3 (both CAN buses).
- An empty saved board.
- A project file that restates the pinned fome-mc ERC rule severities.
- `interface.json`, the board contract: the captured block contract with each
  invariant's source moved to `block-contract.json`. A board check needs a
  captured requirement source, and the captured contract preserves the citation.

`+5V` and `+3V3` are global power nets inside the sheet and reach no header. Both
reviewers of the first run pointed this out (see [Runs](#runs)).

## Review scope

`review.scope.json` is scope/2. The candidate places the block payload at its
block paths. Each baseline requirement keeps the block's statement and cites
`block-contract.json` at `requirements/ID/source` (or
`pending_requirements/ID/source`), the captured record of its pinned-external
citation. That is the form the
[reference-block validator](reference-block-interface.md) requires, so a passing
run could credit `psu-native` and `psu-interface`. Reviewers see
`requirements.md`, which restates each claim with its document and section.

| Check | Kind | Requirement | Expected result |
| --- | --- | --- | --- |
| `erc` | `erc` | `psu-native` | ok/pass (no violations under the pinned ERC severities) |
| `netlist` | `netlist` | `psu-native` | ok/info (43 components, 3 sheet instances) |
| `interface` | `hardware_interface` on `board-interface.json` (`board/interface.json`) | `psu-interface` | ok/pass (every invariant satisfied) |
| `input-filter` | `rc_transient`, `/12V_PROT` to `/VBOOST` | `psu-input-filter` | unsupported/unknown |
| `tvs-clamp` | `datasheet_limits` (not registered) | `psu-tvs-clamp` | unsupported/unknown |
| `reverse-block`, `brownout-floor`, `rail-regulation` | `active_model` (not registered) | the matching requirement | unsupported/unknown |
| `c12-part` | `catalog_part_identity` (not registered) | `psu-c12-part` | unsupported/unknown |
| `c4-rating`, `capacitor-ratings` | `catalog_rating` (not registered) | the matching requirement | unsupported/unknown |

Unknown kinds are kept as unsupported, unknown coverage. That is how the scope
records claims that no agent-ee check can establish yet: datasheet facts
(AGENTEE-18), active models (AGENTEE-19) and catalog ratings and identities
(AGENTEE-27, AGENTEE-29, AGENTEE-61, AGENTEE-137). `input-filter` is unsupported
because two PSU net names, `/CAN+` and `/CAN-`, collide after SPICE node
sanitisation. Even without that collision, the sheet's active parts would keep
it unsupported.

```sh
PYTHONPATH=src python3 -m agent_ee.cli review fixtures/fome-mc/psu/review.scope.json \
  --out NEW_RUN --profile profiles/claude-baseline.json --max-seconds 3600
```

## Runs

The repair for the AGENTEE-106 review (no planning documents, a repository
label, the `T16` origin) changed the block hash, the contract and the scope, so
the baseline changed. The current scope compiles baseline
`9641a8758e9b44e7cec8e7737dae5345bff668267dae7865a7ed440d472929c9` with
candidate `2513e113752149fbcfda45252fbad28b032e43c90bd814fa72ed722bdb31ad8a`.
A checks-only run executes all 11 checks with the results in the table above.
No reviewed run exists yet on this baseline.

The two earlier runs below used the superseded scope at `e869806`, which fixed
that baseline before its first run. They share baseline `39534bca406c7a37c771c42aca233a68aa6a8cc30cc30495d04e9ff68e2ba431`
and candidate `3a03ce81d5e721bfc5447076b4909407837e19facfcd8f81f5f413bc41c35f18`.
They used the `profiles/claude-baseline.json` reviewers (`claude-fable-5`). The run
directories stay on the host. No evidence store is created.

| Run | Checks | Initial reviews | Adjudication | Outcome |
| --- | --- | --- | --- | --- |
| `1a793552-2380-4acf-8b10-6197681262c9` (CLI `review`) | All 11 executed: `erc` pass, `netlist` info, `interface` pass, 8 unsupported/unknown | Both completed, then were refused as `malformed`: each attached witnesses to unknown-coverage checks, and a witness must cite a freshly failed check | Not reached | `incomplete` |
| `88f614fe-bd7d-4c73-a38e-66ce864345ba` (`run_review(..., max_schema_resubmissions=1)`) | Same 11 results | Both `failed`: the provider returned 429, "You've reached your Fable limit" | Not reached | `incomplete` |

The refused first-run reviews are retained under
`review-attempts/{circuit,physical}/0`. They do not count as evidence. Both
reviewers independently reported the same defects in the captured sheet:

- C12 is LCSC C96446, not the required C3092605.
- The documented LMR16020 EN UVLO divider (R3 47k / R10 15k) is absent, and U3 EN
  is tied to VIN.
- C6's part-number field names a 56 uF part.
- R2 is 1 ohm where DESIGN.md gives 2 ohm.
- The SMCJ24A clamp leaves about 1.1 V of margin to the 40 V parts.
- The board gives `+5V` and `+3V3` no header.

Independent review and adjudication of a run on the current baseline remain to
be done.

## Adaptation

`tests/test_fome_psu_block.py` instantiates the block with
[block adaptation](block-adaptation.md) on the same original board. In that test
the PSU child is a pre-created empty `psu.kicad_sch` and every port is bound to
the net that fome-mc gives it.

- With the copy unchanged, the board's `block_provenance` passes with
  `same_bytes`, all 15 boundary ports are satisfied and ERC passes.
- With the buck divider retuned (R6 68k to 75k), the copy is no longer the same
  bytes. That value change is the only one listed.

## Tests

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_fome_psu_block -v
```

The tests check:

- The committed capture's identities and hashes.
- The contract's boundary and value invariants.
- That every source is a pinned-external citation with the recorded blob and
  SHA-256, and stays unverified without a mapped object store.
- That the findings are sourced requirements bound in the scope through
  `block-contract.json`, and that the board contract differs from the block
  contract only in its sources.
- That the board generator reproduces the committed files.
- The `"all"` unassigned rule on the owned inventory fixture.
- A checks-only run of the review scope, and a block run reference to it that
  binds the exact bytes but credits nothing.
- The adaptation smoke.

None of these needs the fome-mc repository, and none is skipped in strict mode.
Recapture is a manual host step, because it needs the fome-mc object store. It
uses the shared qualification script. That script captures the pinned commit
twice, requires the two captures to be byte-identical and requires the first to
equal the committed `capture/`:

```sh
PYTHONPATH=src python3 scripts/qualify_block_inventory.py \
  --inventory fixtures/fome-mc/psu/inventory.json --repo ~/git/fome-mc --out /tmp/NEW_DIRECTORY
```

It prints `status: qualified` with `expected.differing: []`. The output
directory must lie outside the work tree.

## Limits

- The block stays unverified. Its eight requirements are pending, its boundary
  voltage domains and currents are undeclared owner facts, and `parts.json` is a
  draft listing.
- No layout is captured. The ticket's sub-board, the `/psu/` footprints, tracks
  and zones from `fome-mc.kicad_pcb`, is not part of this fixture.
- AGENTEE-105's full inventory capture also holds a `psu` entry, which differs
  from this one in its pending requirements. This entry is the rung-2 reference.
