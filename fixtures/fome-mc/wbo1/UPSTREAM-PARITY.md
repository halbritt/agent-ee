# Rev C parity: exhaustive differences, no equivalence claim

`upstream-parity.json` lists all 78 differences from the pinned Rev C sheet:
three removed connectors (`J301`, `J302`, `J303` after reference offset +300),
one added CAN transceiver (`U307`), and 74 physical-pin conductor/name changes.
The 57 common components have identical native symbols, values, footprints and
population. Every difference is explicitly `unexplained`, with `reason: null`.
No DESIGN.md rationale is inferred from a matching topic or net name.

## Independent source identity

The offline Git identities are:

- Repository: `mck1117/wideband`
- Commit: `ca2adce4aaea1deee66e459cc8949296763a0ad0`
- Tree: `8ddd75016d206b7187cd6bcbf99e7b606acefe9e`
- Path: `hardware/board_module/wideband_controller.kicad_sch`
- Blob: `a7701fc25373e1bcd28ace718cecb35d3b2b5d0e` (231356 bytes)
- SHA-256: `e54843a1e6ef92a79153d8a7bb491ea1cd8ce8216adbdcd9b8f9d665338a1508`

These identities were checked offline against the existing upstream Git object
store at `~/git/fome-fw/firmware/ext/wideband_firmware`. No upstream bytes were
downloaded for this repair or the original parity capture; the original capture
queried host-side metadata only. This attestation uses no network-derived URLs.
The schematic was already retained in agent-ee at
`evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_hardware_board_module_wideband_controller.kicad_sch`.
The reproduction tool verifies its blob identity and SHA-256 on every run.
Those retained bytes alone cannot establish commit/path membership. The report
explicitly records that membership requires the local upstream object store.
It remains unverified when those objects are unavailable.

To reproduce the commit/tree/path attestation without checkout files or network,
set `UPSTREAM` to that existing local repository and run:

```sh
export GIT_NO_REPLACE_OBJECTS=1 GIT_NO_LAZY_FETCH=1 GIT_ALLOW_PROTOCOL=
git -C "$UPSTREAM" cat-file -p ca2adce4aaea1deee66e459cc8949296763a0ad0
git -C "$UPSTREAM" rev-parse ca2adce4aaea1deee66e459cc8949296763a0ad0^{tree}
git -C "$UPSTREAM" ls-tree ca2adce4aaea1deee66e459cc8949296763a0ad0 hardware/board_module/wideband_controller.kicad_sch
git -C "$UPSTREAM" cat-file blob a7701fc25373e1bcd28ace718cecb35d3b2b5d0e | sha256sum
```

## Reproduce

```sh
PYTHONPATH=src python3 fixtures/fome-mc/wbo1/parity.py --out build/wbo1-parity
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_fome_wbo1_parity -v
```

Both XML exports run in the existing network-less executor. The full Rev C
export contains 60 physical parts. The current export includes both captured
channels; the report selects only the 58 wbo1 parts by its exact native instance
UUID. The report uses the existing inventory reader, block projection,
reference-offset adapter and terminal comparator. Anonymous conductor changes
retain their exact terminal groups. Added/removed parts retain their properties
and complete pin projections. Native XML hashes and execution argv are retained.

The comparison uses the declared physical-reference offset +300, with no
suppressed net or library aliases. It covers symbol/value/footprint/population,
physical-pin inventory and every conductor/name change. XML dates, graphical
positions, virtual power markers, third-party licensing and physical circuit
behavior are outside this report. Its output is a diagnostic artifact, not a
sealed `netlist_parity` acceptance witness. DESIGN.md sections 3 and 5 remain
context for an independent reviewer to disposition every unexplained difference.
