# Real KICADROUTE-91 routability-experiment/1 receipts (AGENTEE-442)

Byte copies of kicad-route output, not agent-ee material. The krt91c-experiment
worker (for agent-507) ran kicad-route commit
`4f06368fbd68bab44cbfde839647aa5b3f7ef7c5` (MIT, github.com/halbritt/kicad-route)
on kicad-route's own `fixtures/route_demo` board. Its artifacts directory's
`SHA256SUMS` verified (610 files OK) before these were copied on 2026-10-22.

| File | Source run file |
|---|---|
| `positive-current.routability-experiment.json` | `positive-current/routability-experiment.json` |
| `positive-historical.routability-experiment.json` | `positive-historical/routability-experiment.json` |
| `negative-stale-runtime.routability-experiment.json` | `negative-stale-runtime/routability-experiment.json` |
| `current-policy.experiment-policy.json` | `positive-current/experiment-policy.json` (the same bytes as the stale run's copy) |
| `historical-all-layer.experiment-policy.json` | `positive-historical/experiment-policy.json` |

`receipts.json` is agent-ee's record of each run's `result.json` `experiment`
binding and of the producer schema digest (`schemas/routability-experiment-1.json`
at that commit). No board, schematic, library or other design file is copied.
The receipts contain hashes, router configuration and the producer host's
absolute paths. `docs/file-origins.json` classifies them as origin T17 and leaves
publication to an owner decision.

tests/test_routing_experiment_boundary.py `RealReceiptReplayTest` replays them
through step, run_step, verify and adopt. `SHA256SUMS` lists the copied bytes.
