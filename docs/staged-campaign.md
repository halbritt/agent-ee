# Opt-in staged campaign contract

AGENTEE-48/88 extends the existing repair campaign. Independent initial reviews,
central adjudication, sealed check evidence, the immutable baseline and the one
implementation writer remain the acceptance path. This convergence change needs
independent review and owner acceptance; tests do not supply either approval.
No new generator, router or acceptance state is introduced.

Scopes without stage declarations keep their previous convergence projection
and rules. A staged scope/2 or scope/3 adds this field:

```json
{
  "stages": {
    "schema": "agent-ee.stages/1",
    "order": ["schematic", "placement", "routing"],
    "subjects": {
      "schematic": {
        "path": "project/design.kicad_sch",
        "kind": "symbols",
        "references": ["R1", "C1"]
      },
      "placement": {
        "path": "project/design.kicad_pcb",
        "kind": "footprints",
        "references": ["R1", "C1"]
      },
      "routing": {
        "path": "project/design.kicad_pcb",
        "kind": "tracks",
        "minimum": 1
      }
    },
    "finding_retry_limit": 3
  }
}
```

This is a field fragment, not a complete scope. Every check must also declare
`stage`, for example `"stage": "schematic"`. Checks must follow stage order;
every declared stage needs at least one check. `order` is a unique subsequence
of `schematic`, `placement`, `routing`, `verification`, `fab`. The retry limit is
an integer in 1..100. At least one subject must be declared; stages without a
subject use their ordinary checks after preceding gates open. All other scope
requirements and executable-coverage rules still apply.

The complete declaration participates in the immutable baseline identity.
Subjects name captured native design files, outside immutable verification
sources. Symbol and footprint subjects require nonempty unique Reference strings;
track subjects require an integer minimum in 1..1000000. Expected inventory is an
explicit owner declaration, not inferred intent or a passing parts-subset check.
Changing it requires a new baseline and fresh qualification.

## Gate evidence

Before a stage check, a networkless sandbox reads its captured native subject.
Schematic subjects require successful KiCad XML netlist export; board subjects
require successful pcbnew loading. The native inventory must agree with the
bounded text reader before an absence witness can be produced. The probe retains
its native observation and, for schematics, the raw XML netlist. Public verification
requires successful probe execution and checks these observations against the
captured inventory. It counts top-level placed symbols or footprints by explicit Reference property,
or saved straight/arc segments. Nested library definitions are not instances. Symbols explicitly excluded from
both BOM and board (`in_bom no`, `on_board no`) are outside component presence;
the resulting inventory must still agree with native export.
Duplicate/missing References, malformed syntax and hierarchical schematic
subjects are unsupported probe execution, never absence evidence. The parser is
bounded to 32 MiB and 128 levels. The qualified source-version set is schematic
20250114/20260306 and board 20250114/20260101/20260206; other versions remain
unknown even if the native reader accepts an empty document. This is presence extraction, not native ERC,
parity, placement suitability, connectivity or routing-quality verification.

Missing expected content produces `execution: ok`, `outcome: defect`, with
`details.reason: stage_not_reached`. Its coordinator-produced
`work/<check>/stage-subject.json` records candidate and baseline IDs, source hash,
expected inventory, observed inventory and missing content. The probe's execution,
console and output artifacts are retained. Public verification reconstructs the
witness from the captured native source and immutable declaration.

A defect in an earlier stage blocks later handlers. Later declared subjects may
still receive cheap native presence probes: absent content gets its own
`stage_not_reached` witness. When a later subject is present, or has no declaration,
its coordinator gate verdict is `ok/defect`, reason `stage_blocked`, with a witness binding the earlier
check results. It says that the gate is closed; it does not claim that the later
tool executed. An earlier failed/unknown check instead leaves later checks
`not_run/unknown`. Crashes, unsupported inputs, missing output, malformed findings,
timeouts and cancellation remain missing coverage and terminate incomplete.
Unknown tools are not relabelled as stage absence. A simulation with missing nets
or an unsupported model remains unknown once its declared stage opens.

## Progress and repair

The reducer records `earliest_failing_stage`, per-stage failed sets,
`blocked_checks`, the stage-policy identity and recurring finding identities in
each history record. Only shrinking the failed set of the previously earliest
failing stage earns stage progress. Clearing that stage permits the next one.
An unchanged failed set can earn progress only through the existing strict metric
rule applied within that active stage: comparable bounds, at least one negative
margin improves, no other negative margin worsens, and no new failed check.
Improving a later stage cannot mask an earlier stall.

All previously evaluated passing checks and nonnegative metric bounds remain
protected, including checks in completed stages. Repeated captured designs,
regression, stalled progress, unresolved review claims and iteration/wall-clock
budgets retain their existing stop behavior. The configured retry limit also
stops recurring finding identities, even when their metrics improve. Deferred
`stage_blocked` verdicts and later-stage content absence do not consume the retry
count; only findings in the currently earliest failing stage consume attempts. Campaign public
verification recomputes the same projection from each retained run.

Checks may attach `details.gate_findings`:

```json
[
  {
    "check_id": "placement",
    "entity": "R1",
    "kind": "overlap",
    "suggested_fix": {"op": "move", "params": {"x_mm": 20, "y_mm": 30}}
  }
]
```

Each finding requires nonempty `check_id`, `entity`, `kind`; `check_id` must match
its enclosing check. Duplicate entity/kind identities, unknown fields, malformed
fixes and nonfinite JSON parameters are rejected. `suggested_fix` is optional and
contains exactly a nonempty `op` and object `params`. These hints are retained on
collected coordinator findings, then exposed to the sole writer only through
accepted findings. They never apply edits, establish evidence, reset retry counts
or satisfy a criterion. Every captured proposal receives fresh checks.

## Qualification limits

[Original snapshots](../fixtures/stages/README.md) exercise empty, schematic,
placed and saved-copper presence. Tests run sandboxed presence probes, real narrow
board geometry checks and the existing campaign with a synthetic measurement
producer and scripted independent reviewer/adjudicator responses. Scripted ERC
in the transition test is explicitly not native ERC evidence. Existing unstaged
convergence tests remain unchanged.

A complete native RC ladder is unmet: these snapshots do not supply ERC-clean
connectivity, native footprint closure, schematic/PCB parity, placement acceptance,
qualified simulation or a sealed fabrication-package gate. The production scaffold,
placement/routing writer constraints and AGENTEE-95 implementation-step receipt
integration remain separate lanes. The writer prompt limits work to the earliest
failing stage; arbitrary writer investigative commands are not a stage-specific
OS allowlist. Coordinator helper execution must honor the same gate when root
integrates those lanes. Owner convergence-contract acceptance remains outstanding.

The ordered scheduler changes the identity of `checks.py`, which the existing
passive-corner contract pins as its parser module. Requalification compared the
changed source with the assigned baseline: only `run_checks` changed; RC value
extraction, deck production, simulation parsing and the corner model were unchanged.
The native corner corpus reran complete control/defect/zero-tolerance ensembles,
independent analytical endpoint comparisons, partial/corrupt output and identity
failure boundaries in the pinned image before refreshing the shipped qualification
record. Existing electrical expectations and tests were preserved. This numerical
requalification supplies no physical validation or owner acceptance.
