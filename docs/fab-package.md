# Fab package done gate

Decision 1 in [decisions](decisions.md#1-generation-is-a-repair-campaign-from-an-empty-candidate)
defines "done" for generation as the declared check set passing and the fab
exports existing as checked artifacts. `fab_package` (AGENTEE-94) is the check
that makes the second half executable, and the `generation` scope marker makes
it mandatory.

## The check

```json
{"id": "fab", "kind": "fab_package", "params": {"netlist_check": "netlist"}}
```

- Params are exactly `{netlist_check}`, the ID of an earlier `netlist` check.
  Layer and output overrides are refused, as in `fab_export/1`.
- It must be the last declared check. Declared anywhere else it is
  `unsupported/unknown`, and scope/3 coverage refuses it.
- Scope/3 also requires an earlier `geometry` check, the native footprint
  inventory the consistency gate reads.

One run of the check does three things in order, in its own workspace
`work/ID/`:

1. Export. It runs the [`fab_export`](fab-export.md) producer: the pinned
   `kicad-cli` in the executor sandbox, the same steps, normalisation, BOM/CPL
   rules and refusals, writing `work/ID/fab/` and its
   `agent-ee.fab-manifest/1`.
2. Gate. It runs the [`fab_consistency`](fab-consistency.md) gate on that
   staging tree, with this check as the export. The gate's details are kept
   under `details.consistency` and its report at `work/ID/fab-consistency.json`.
3. Seal. It recomputes the manifest inventory and records `details.artifacts`:

```json
{
  "schema": "agent-ee.fab-artifacts/1",
  "check_id": "fab",
  "candidate_id": "<candidate sha256>",
  "board_path": "design/fab.kicad_pcb",
  "board_sha256": "<captured board sha256>",
  "files": {
    "work/fab/fab/bom.csv": "<sha256>",
    "work/fab/fab/cpl.csv": "<sha256>",
    "work/fab/fab/drill/fab-NPTH-drl_map.gbr": "<sha256>",
    "work/fab/fab/drill/fab-NPTH.drl": "<sha256>",
    "work/fab/fab/drill/fab-PTH-drl_map.gbr": "<sha256>",
    "work/fab/fab/drill/fab-PTH.drl": "<sha256>",
    "work/fab/fab/gerbers.zip": "<sha256>",
    "work/fab/fab/manifest.json": "<sha256>"
  },
  "artifacts_id": "<sha256 of the canonical record above>"
}
```

Every listed file is also check evidence, so the run's evidence seal
(`work/verification/evidence.json`) binds the same digests to the candidate and
baseline, and `verify` refuses the run if any of them changes.

## Results

| Condition | Result |
| --- | --- |
| Params not exactly `{netlist_check}`, or the check is not declared last | `unsupported`, `unknown` |
| Any `fab_export` refusal or failure: netlist prerequisite missing, unsupported board feature, unbound part, native nonzero exit, missing/empty/unparseable native output, designator disagreement | The export's `failed`, `malformed`, `unsupported` or `interrupted`, `unknown`; no `fab/` package |
| Any `fab_consistency` structural fault or coverage gap: missing, empty, unlisted or rehashed staged file, broken zip, unparseable CSV or summary, unverifiable tool identity, missing geometry | The gate's `malformed` or `unsupported`, `unknown` |
| A gate defect: designator parity, LCSC/footprint conflict, CPL side or coordinates, unresolved 3D model, export-log diagnostic | `ok`, `defect`, summary `fab package done gate failed: ...` |
| `gerbers.zip`, `bom.csv`, `cpl.csv`, the PTH or NPTH drill or `manifest.json` absent, empty, unsealed or not matching the manifest at seal time | `malformed`, `unknown` |
| Export, gate and seal all succeed | `ok`, `pass`, `details.artifacts` recorded |

A missing, empty or unparseable export file is therefore always `unknown` or a
`defect`, never `pass`. Outcome `pass` establishes that the package exists, is
internally consistent with the captured design and is hash-bound. It does not
establish DFM, CPL rotation correction (AGENTEE-72), stock or orderability.

## Generation scopes

A scope written by a generation scaffold carries a marker:

```json
{"generation": {"schema": "agent-ee.generation/1", "done_check": "fab"}}
```

Baseline compilation, which runs at capture, refuses the scope unless:

- the schema is scope/2 or scope/3;
- the marker is exactly `{schema, done_check}`;
- the last declared check is `done_check` and its kind is `fab_package`;
- a baseline requirement maps to `done_check`, with `intent: "acceptance"` on
  scope/3.

The marker is copied into the baseline, so it is part of the `baseline_id`.
A campaign therefore cannot drop it, or the done check, without changing its
immutable baseline, which the campaign refuses. Removing the `fab_package`
check or its requirement from a generation scope is refused at capture with
`generation scope must declare its fab_package done check last` (or, when only
the requirement is removed, `every check must trace to a baseline requirement`
on scope/2 and `generation scope requires an acceptance requirement mapped to
fab_package` on scope/3).

The [rung-1 campaign](rung1-campaign.md) scaffold writes the marker, the
`fab` check in its `verification` stage and requirement `R-fab`, sourced from
`requirements/fab-package.md`.

## Campaign state

When a campaign over a generation scope reaches `criteria_satisfied`, its
`state.json` lists `fab_artifacts`, the `agent-ee.fab-artifacts/1` record
recomputed from the final run's retained `checks/ID.json` and files. If the
done check did not pass or an artifact does not verify, the campaign stops
`incomplete` instead.

`agent-ee verify-campaign ROOT` recomputes the record from the final run and
refuses the campaign if the stored hashes differ, are missing from a satisfied
generation state, or are present on any other state. It prints them after the
state:

```text
criteria_satisfied
fab_artifacts <artifacts_id> candidate <candidate_id>
<sha256>  <run>/work/fab/fab/bom.csv
...
```

The rung-1 record (`rung1-campaign-record.json`) copies `fab_artifacts` from
the state and the hashes parsed from that verify-campaign output; `done`
requires the `criteria_satisfied` verdict and the two to agree.

## Tests

`tests/test_fab_package.py` runs the real export, gate and seal on the owned
`fixtures/fab-export` board, damages the staging tree between producer and gate
(missing, empty and unparseable files, a dropped CPL row), refuses generation
scopes without the done gate at capture, and runs a one-iteration generation
campaign with scripted reviewer responses whose final state verify-campaign
recomputes and whose tampering it refuses.
