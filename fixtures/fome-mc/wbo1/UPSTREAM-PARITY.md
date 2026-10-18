# Rev C parity: exhaustive differences, no equivalence claim

`upstream-parity.json` lists all 78 differences from the pinned Rev C sheet:
three removed connectors (`J301`, `J302`, `J303` after reference offset +300),
one added CAN transceiver (`U307`), and 74 physical-pin conductor/name changes.
The 57 common components have identical native symbols, values, footprints and
population. Every difference is explicitly `unexplained`, with `reason: null`.
No DESIGN.md rationale is inferred from a matching topic or net name.

## Independent source identity

The [upstream Git commit metadata](https://api.github.com/repos/mck1117/wideband/git/commits/ca2adce4aaea1deee66e459cc8949296763a0ad0)
identifies commit `ca2adce4aaea1deee66e459cc8949296763a0ad0` and tree
`8ddd75016d206b7187cd6bcbf99e7b606acefe9e`. The
[upstream tree metadata](https://api.github.com/repos/mck1117/wideband/git/trees/ca2adce4aaea1deee66e459cc8949296763a0ad0?recursive=1)
identifies `hardware/board_module/wideband_controller.kicad_sch` as blob
`a7701fc25373e1bcd28ace718cecb35d3b2b5d0e`, 231356 bytes.

The upstream schematic already retained in agent-ee at
`evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_hardware_board_module_wideband_controller.kicad_sch`
has that exact Git blob identity and SHA-256
`e54843a1e6ef92a79153d8a7bb491ea1cd8ce8216adbdcd9b8f9d665338a1508`.
Its old fetch record names a later commit, but byte identity with Rev C was
independently confirmed on 2026-10-18 through public metadata only. No new
upstream design file was copied. The reproduction tool checks both hashes and
performs no network I/O.

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
