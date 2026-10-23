# Real KICADROUTE-91 routability-experiment/1 receipts (AGENTEE-442)

Byte copies of kicad-route output, not agent-ee material. kicad-route is MIT
(github.com/halbritt/kicad-route). No board, schematic, library, facts or other
design file is copied.

## Final producer (replayed)

agent-507's final frozen KICADROUTE-91 producer, kicad-route commit
`a0ed43494d44fd8312054b204083881fe76f3a81`, wrote a clean native receipt
(`tool_identity` `a0ed434`, `dirty: false`) on kicad-route's own
`fixtures/route_demo` board at
`/home/halbritt/git/kicad-route/build/krt91-final-native-agent507/final-clean-receipt`.
It was copied on 2026-10-22 and every file's sha256 was checked against the
original before these were taken from the copy.

| File | Source run file |
|---|---|
| `final-current.routability-experiment.json` | `routability-experiment.json` |
| `current-policy.experiment-policy.json` | `experiment-policy.json` (the same bytes as the interim run's copy) |
| `final-current.effective-configuration.json` | `work/effective-configuration.json` |
| `final-current.effective-configuration-request.json` | `work/effective-configuration-request.json` |

`receipts.json` `runs` is agent-ee's record of the run's `result.json`
`experiment` binding and its `process.effective_configuration` binding, and of
the producer schema digest (`schemas/routability-experiment-1.json` at that
commit, from the git object). The rest of the receipt, including its route_demo
boards and facts, stays external: `FinalNativeReceiptTest` reads a byte copy
named by `AGENT_EE_K91_FINAL_RECEIPT` and pins its key hashes.

## Interim producer (retired)

The krt91c-experiment worker ran the interim producer, kicad-route
`4f06368fbd68bab44cbfde839647aa5b3f7ef7c5`, on the same board. Its sidecars
(`interim-positive-current`, `interim-positive-historical`,
`interim-negative-stale-runtime`) and `historical-all-layer.experiment-policy.json`
are kept only to show that they are refused: they lack the final producer's
`identity.effective.input` and `candidate.raw_backend_output_board_sha256`.
`receipts.json` `retired_interim` records their `result.json` bindings. Their
sealed manifests were removed.

The receipts contain hashes, router configuration and the producer host's
absolute paths. `docs/file-origins.json` classifies them as origin T17 and leaves
publication to an owner decision. `SHA256SUMS` lists the copied bytes.
