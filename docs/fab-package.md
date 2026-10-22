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

- Params require `netlist_check`, the ID of an earlier `netlist` check. Optional
  `bom_variant`, `assembly_exclusion`, `copper_features` and `probe_manifest` use the same validation
  and evidence as `fab_export`; optional `model_libraries` maps stock variables to
  captured model receipts for `fab_consistency`. Unknown keys, layer and output
  overrides are refused. Model receipts and the probe declaration's source closure
  are immutable baseline inputs. Probe admission requires an earlier matching,
  passing `parts_subset` check bound to the same manifest and native netlist.
- It is last except for following [`jlc_dfm`](jlc-dfm.md) checks naming this
  package. Any other following check is `unsupported/unknown`, and scope/3
  coverage refuses the package.
- Scope/3 also requires an earlier `geometry` check, the native footprint
  inventory the consistency gate reads.

One run of the check does three things in order, in its own workspace
`work/ID/`:

1. Export. It runs the [`fab_export`](fab-export.md) producer: the pinned
   `kicad-cli` in the executor sandbox, the same steps, normalisation, BOM/CPL
   rules and refusals, writing `work/ID/fab/` and its
   `agent-ee.fab-manifest/2`.
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
  "rotation": {"status": "not_declared"},
  "artifacts_id": "<sha256 of the canonical record above>"
}
```

Every listed file is also check evidence, so the run's evidence seal
(`work/verification/evidence.json`) binds the same digests to the candidate and
baseline, and `verify` refuses the run if any of them changes.

## Results

| Condition | Result |
| --- | --- |
| Params not exactly `{netlist_check}`, or a following check is not a bound `jlc_dfm` | `unsupported`, `unknown` |
| Any `fab_export` refusal or failure: netlist prerequisite missing, unsupported board feature, unbound part, native nonzero exit, missing/empty/unparseable native output, designator disagreement | The export's `failed`, `malformed`, `unsupported` or `interrupted`, `unknown`; no `fab/` package |
| Any `fab_consistency` structural fault or coverage gap: missing, empty, unlisted or rehashed staged file, broken zip, unparseable CSV or summary, unverifiable tool identity, missing geometry | The gate's `malformed` or `unsupported`, `unknown` |
| A gate defect: designator parity, LCSC/footprint conflict, CPL side or coordinates, unresolved 3D model, export-log diagnostic | `ok`, `defect`, summary `fab package done gate failed: ...` |
| `gerbers.zip`, `bom.csv`, `cpl.csv`, the PTH or NPTH drill or `manifest.json` absent, empty, unsealed or not matching the manifest at seal time | `malformed`, `unknown` |
| Export and gate succeed, but the captured baseline envelope has open final qualification ([final guard](board-envelope.md#final-acceptance-guard)) | `unsupported`, `unknown`, summary `fab package done gate held: ...` naming each obligation and open item; the exports stay in `work/ID/fab/` as diagnostics, no `details.artifacts` |
| Export, gate and seal all succeed | `ok`, `pass`, `details.artifacts` recorded |

A missing, empty or unparseable export file is therefore always `unknown` or a
`defect`, never `pass`. Outcome `pass` establishes that the package exists, is
internally consistent with the captured design and is hash-bound. It does not
establish DFM, stock or orderability, nor, without a declared `cpl_rotation`
check, CPL rotation correction.

## CPL rotation

When the scope declares a [`cpl_rotation`](cpl-rotation.md) check (AGENTEE-126),
the done gate consumes its sealed result instead of sealing the native CPL. At
most one may be declared; it precedes the gate (which is last) and needs its own
earlier `fab_export` check, for example
`netlist, geometry, fab-export (fab_export), cpl (cpl_rotation, export_check fab-export), fab (fab_package)`.
Scope/3 coverage lists it as a prerequisite of the gate.

After the export and the consistency gate pass, the gate requires that:

- the `cpl_rotation` result is `ok`/`pass` for this candidate, and its report
  has complete coverage;
- the report digest equals the check's `details.report_sha256`;
- the report's board digest and its native `cpl.csv` digest equal this gate's
  own manifest (both exports are byte-identical on the same captured board);
- the corrected `work/CPL/cpl/cpl.csv` equals the report's digest;
- the status is `ok` or `PREVIEW_REQUIRED`, with `physical_validation` true
  only for `ok`.

The corrected `work/CPL/cpl/cpl.csv` then replaces `work/ID/fab/cpl.csv` in
`files` (the native file stays in the package and its manifest), and the gate
adds the corrected CPL and the report to its own evidence. `rotation` records
the status, the check id, the policy id and sha256, the report path and
digest, the corrected and native CPL digests, the applied/unmatched counts and
`preview_obligations` (below); the same record is `details.rotation`. Without a
declared check `rotation` is `{"status": "not_declared"}`.

| Condition | Result |
| --- | --- |
| Two `cpl_rotation` checks declared | `unsupported` (capability) |
| The declared correction is a `defect` | `ok`, `defect`, summary `fab package done gate failed: cpl_rotation 'cpl' found a defect: ...` |
| The declared correction did not pass (`unsupported`, `malformed`, ...) | `unsupported`, `unknown` |
| Report, corrected CPL, board or native CPL binding disagrees | `malformed`, `unknown` |
| Everything binds | `ok`, `pass`; summary ends `cpl rotation PREVIEW_REQUIRED` |

The gate never upgrades a status. Whether a status may mark a generation
done is `fab_package.DONE_ROTATION_STATUSES`, `{not_declared, ok,
PREVIEW_REQUIRED}`, applied through `rotation_blocker` by the generator, the
rung-1 campaign record and the qualified record. Any other or missing status
keeps `done` false with the reason
`fab: cpl rotation status '<status>' is not a done status`.

### Preview obligations (AGENTEE-151)

The owner decided on 2026-10-18 that `PREVIEW_REQUIRED` is an operator
obligation recorded at done ([decisions](decisions.md), "Telemetry pod questions
(second batch, 2026-10-18)", PREVIEW_REQUIRED rotations). A status in
`fab_package.OBLIGATION_STATUSES` (`PREVIEW_REQUIRED`, the only status
`cpl_rotation` emits) is done only with its `preview_obligations` record:

```json
{"status": "PREVIEW_REQUIRED", "policy_sha256": "<policy sha256>",
 "action": "confirm each designator in the JLCPCB placement preview before ordering",
 "designators": [{"designator": "C1", "side": "Top"}, {"designator": "JB0", "side": "Bottom"}]}
```

`fab_package.preview_obligations` computes it from the sealed corrected CPL:
every row, with its `Layer` as `side`, naturally sorted, bound to the policy
sha256. The report's designator records must list the same designators on the
same sides, or the record is refused. Every driver recomputes it from sealed
evidence rather than trusting a stored copy, and `rotation_blocker(status,
obligations, policy_sha256)` blocks done unless it is well formed: exact keys,
the same status and action, the sealed policy digest, a nonempty, unique,
sorted designator list with `Top`/`Bottom` sides. Missing obligations give
`fab: cpl rotation PREVIEW_REQUIRED: no preview obligations recorded`. A
status that carries no obligation (`ok`, `not_declared`) is blocked if
obligations are attached. The status stays `PREVIEW_REQUIRED` in every
receipt and record; it is never upgraded to a pass. The passing gate adds a
limitation naming each designator and side, and its summary ends
`cpl rotation PREVIEW_REQUIRED; N preview obligations`.

## Generation scopes

A scope written by a generation scaffold carries a marker:

```json
{"generation": {"schema": "agent-ee.generation/1", "done_check": "fab"}}
```

Baseline compilation, which runs at capture, refuses the scope unless:

- the schema is scope/2 or scope/3;
- the marker is exactly `{schema, done_check}`;
- `done_check` is `fab_package`, followed only by bound `jlc_dfm` checks;
- a baseline requirement maps to `done_check`, with `intent: "acceptance"` on
  scope/3.

The marker is copied into the baseline, so it is part of the `baseline_id`.
A campaign therefore cannot drop it, or the done check, without changing its
immutable baseline, which the campaign refuses. Removing the `fab_package`
check or its requirement from a generation scope is refused at capture with
`generation scope must declare its fab_package done check last` or
`fab_package done check last: only bound jlc_dfm checks may follow` (or, when only
the requirement is removed, `every check must trace to a baseline requirement`
on scope/2 and `generation scope requires an acceptance requirement mapped to
fab_package` on scope/3).

The [rung-1 campaign](rung1-campaign.md) scaffold writes the marker, the
`fab` check in its `verification` stage and requirement `R-fab`, sourced from
`requirements/fab-package.md`. With `--cpl-inputs` the scope instead ends with a
`fab` stage holding `fab-export`, `cpl` and the `fab` done gate.

## Campaign state

When a campaign over a generation scope reaches `criteria_satisfied`, its
`state.json` lists `fab_artifacts`, the `agent-ee.fab-artifacts/1` record
recomputed from the final run's retained `checks/ID.json` and files. If the
done check did not pass, an artifact does not verify or the captured envelope
qualification is open, the campaign stops `incomplete` instead.
`artifact_record` applies the same final guard, so no release is sealed while it
blocks.

`agent-ee verify-campaign ROOT` recomputes the record from the final run and
refuses the campaign if the stored hashes differ, are missing from a satisfied
generation state, or are present on any other state. It prints them after the
state:

```text
criteria_satisfied
fab_rotation PREVIEW_REQUIRED policy <policy sha256>
fab_preview_obligations 2 policy <policy sha256>: confirm each designator in the JLCPCB placement preview before ordering
fab_preview_obligation C1 Top
fab_preview_obligation JB0 Bottom
fab_artifacts <artifacts_id> candidate <candidate_id>
<sha256>  <run>/work/fab/fab/bom.csv
...
```

The `fab_rotation` line is always printed (`fab_rotation not_declared policy
None` without a declared check). The obligation lines, recomputed from the
final run's sealed report and corrected CPL, follow for a status with
obligations; a `fab: cpl rotation ...` reason line follows when
`rotation_blocker` refuses the status. The rotation record, obligations
included, is part of the `artifacts_id`, so a forged status or obligation list
in the state is refused like a forged hash.

The rung-1 record (`rung1-campaign-record.json`) copies `fab_artifacts` from
the state and the hashes and obligations parsed from that verify-campaign
output; `done` requires the `criteria_satisfied` verdict, the two to agree
(obligations included; any malformed or inconsistent obligation line refuses
the parse), and the rotation status to be allowed with its obligations
(`fab_rotation`, `fab_rotation_blocker`, `preview_obligations`). A record
rebuilt from a state written before AGENTEE-126 has no rotation record and is
never `done`; one written before AGENTEE-151 has a `PREVIEW_REQUIRED` rotation
without obligations and is not `done` either.

## Tests

`tests/test_fab_package.py` runs the real export, gate and seal on the owned
`fixtures/fab-export` board, damages the staging tree between producer and gate
(missing, empty and unparseable files, a dropped CPL row), refuses generation
scopes without the done gate at capture, and runs a one-iteration generation
campaign with scripted reviewer responses whose final state verify-campaign
recomputes and whose tampering it refuses. `RotationCompositionTests` run
`fab-export`, `cpl_rotation` and the gate on the owned `fixtures/cpl-rotation`
board: the corrected CPL is sealed with `PREVIEW_REQUIRED` and the policy
digest, native/board/report/corrected-CPL disagreements are refused, a
correction without a pass fails the gate, and two declared corrections are
unsupported. The obligations equal the corrected CPL's rows and sides,
malformed obligation records are refused by `rotation_blocker`, and a report
or CPL that disagree are refused by `preview_obligations`. A second generation
campaign on that scope prints the obligations in verify-campaign output, makes
the rung-1 record done with `preview_obligations` listed, is not done when the
constant is patched to drop `PREVIEW_REQUIRED`, and refuses a state whose
obligation list was tampered with.

## Declared fab producer

In a staged campaign with a declared fab step, this gate grades the receipted
package after comparing it with fresh sandbox exports and correction. It cannot
pass before that step runs. Missing output stays unknown; a completed fab turn
can leave the board bytes unchanged while adding the required package evidence.
The gate's sealed artifacts, rotation status and declared-stage done binding
are all still required. See [declared fab steps](fab-export.md#declared-fab-steps)
for receipt applicability, comparison rules and the pending-step scheduling path.

Receipted Python steps mount a [content-addressed source snapshot](stage-receipts.md#python-tool-source-snapshots), separate from host import caches. A refused fab step reports its violations and retained receipt path; mounted-file mutation still refuses adoption.
