# Pod v5 stage-gated stock check (AGENTEE-251)

A reduced copy of the pod v5 campaign run 1 (`driver-preparation-v5/campaign/runs/1`,
produced on main 255782f). Its `assembly` check is `execution: ok`,
`outcome: defect`, `reason: stage_not_reached` at the `verification` stage, with
104 gate findings and no stock evaluation. Before AGENTEE-251, campaign replay
refused it with `JLC stock check lacks its evaluation witness`.

Copied byte-for-byte from the run:

- `checks/assembly.json`
- `work/assembly/stage-subject.json`
- `work/assembly/stage-subject/{execution.json,console.log,native-observation.json,references.json}`
- `inputs/assembly-stock/catalog.json` (JLC stock facts: LCSC ids, packages, counts)

Reduced:

- `candidate.json` keeps the run's `schema`, `candidate_id` and full `scope`
  verbatim. `files` is limited to the board, schematic and catalog hashes.
- `baseline-id.json` holds only the run's `baseline_id`. The full baseline
  compiles from the entire captured input tree. That tree includes third-party
  library content, so it is not reproduced here. The campaign passes its
  verified baseline instead.

Original stand-in:

- `inputs/project/telemetry-pod.kicad_pcb` is hand-authored. It holds the same
  eight observed references (H1-H4, J1, J2, U1, U2) as `Owned:Placeholder`
  footprints. The captured board embeds KiCad-library footprint graphics, which
  are not redistributed. The manifest hash still names the captured board.
  Input-hash sealing is exercised by the campaign test in `tests/test_stages.py`,
  not here.
