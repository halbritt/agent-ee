# fome-mc wideband reference (AGENTEE-107)

The rung-2 analogue/MCU reference lives in
[`fixtures/fome-mc/wbo1/`](../fixtures/fome-mc/wbo1/COVERAGE.md).
`blocks/wbo1/block.json` is the captured `agent-ee.block/1` entry;
`blocks/wbo2/block.json` is its native adaptation target. Each contains 58
physical parts, its exact source paths, commit
`df304f3321296f7e517e0d2e2aa52e1380d52993`, and SHA-256 for every payload.
Their shared rights statement is `fixtures/fome-mc/LICENSE-NOTICE.md`, supplied
by the AGENTEE-105 fixture change. Neither entry is a verified block.

## Capture

Capture uses the [offline inventory path](block-inventory.md), followed by
`reference_blocks.capture_block` to publish only the selected channels.
No source working-tree file is read. The source checkout may be dirty or at a
different HEAD: only the pinned Git object tree is used. External symbol/footprint
provenance uses `kicad-libraries` commit
`ab9c5f8a1517a04b9cada6880d67d190bfd48c05`; those library files are not copied.
The stock-library hashes identify the acquisition host (KiCad 10.0.7).

For full inventory reproducibility, run `scripts/qualify_block_inventory.py`
with the AGENTEE-105 inventory, its wbo1-to-wbo2 fixture and the pinned local
repositories. Its output belongs in scratch or ignored `build/`. It captures
twice, compares every byte and checks the exact strap differences. The fixture
README gives the selected-block publication call; this reuses the Git capture
API and does not copy design files by hand.

## Native checks and adaptation

```sh
PYTHONPATH=src python3 -m agent_ee.cli review \
  fixtures/fome-mc/wbo1/reference.scope.json \
  --out build/wbo1-reference --checks-only --max-seconds 180
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_fome_wbo1 -v
PYTHONPATH=src python3 fixtures/fome-mc/wbo1/adapt.py --out build/wbo2-adaptation
PYTHONPATH=src python3 -m agent_ee.cli review build/wbo2-adaptation/scope.json \
  --out build/wbo2-adaptation-run --checks-only --max-seconds 180
```

The original parent geometry preserves the pinned root and child UUIDs so the
omission audit can resolve the captured power symbols without rewriting the
source sheets. Its empty board supplies no PCB coverage. The scope freezes
native ERC/export requirements and the interface contract. The contract checks
wbo1 values and external conductors, PB1 ground, channel-2 PA8 high and VDDA
separation. The open PA8 assertion runs in the native regression test; the
hardware-interface check has no open-pin predicate.

The fixture writer invokes [`block_adaptation.instantiate`](block-adaptation.md),
then edits the mutable child: references +100, WBO2 suffix and the two declared
strap changes. It rebinds native instance metadata to the new parent. The golden
wbo2 payload never supplies the writer's schematic. Fresh native exports compare
all regenerated components and pins with that golden capture, and
`block_provenance` observes all ten boundary bindings. Missing straps retain the
exact U402 pin 15/18 differences; wrong offsets and missed renames differ.
Source block results never qualify the new board.

## Qualification limits

The acquisition produced byte-identical captures and no adaptation differences
after the declared straps. On KiCad 10.0.7 the two-channel wrapper exports 116
physical components and passes the interface check, while native ERC reports
834 warnings and two errors. No ERC waiver is added. A checks-only run stays
incomplete because independent reviews and adjudication have not run.

The [Rev C difference report](../fixtures/fome-mc/wbo1/UPSTREAM-PARITY.md)
lists all 78 differences as unexplained, using independently confirmed upstream
blob identity and fresh native exports. It supplies no equivalence witness.
The [coverage ledger](../fixtures/fome-mc/wbo1/COVERAGE.md) records pending
firmware decoder capture, active-model/LSU evidence, catalog/parts qualification
and Q301 copper geometry. Domain/current declarations and draft parts also keep block
validation `unverified`. Passing the adaptation regression does not satisfy
those engineering requirements.
