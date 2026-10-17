# Generator roadmap

**Status: planning.** This page plans how agent-ee becomes the engine of an
AI-driven PCB generator. Nothing on it has shipped unless it links to an
existing reference page. The owner's [generator direction](decisions.md#generator-direction-2026-10-17)
is the authority; this page applies it. Plane epics AGENTEE-78 to AGENTEE-84
track the stages, and new items AGENTEE-85 to AGENTEE-99 track the gaps found
while writing it. An objection to a decision is a Plane item for the owner, not
an edit here.

## Target experience

The user describes a board, discusses parts with the agent, and says "go". The
result is a JLCPCB-ready package: Gerbers, drill files, BOM and CPL, from a
native KiCad project in which every step has tool evidence. The target product
is FOME-MC. agent-ee supplies the loop and the checks, kicad-route supplies
routing, and FOME-MC supplies the reference blocks and the final benchmark.

"Done" has one meaning (decision 1): a campaign reaches `criteria_satisfied`,
so every declared check passed on the same captured candidate, and a fab package
check found the exports present and consistent. Model prose, a writer's summary
or a router's self-report never establishes done.

## Benchmark ladder

Each rung is one end-to-end run, from requirements to fab package, recorded as
evidence (AGENTEE-96 to AGENTEE-99).

| Rung | Board | Size | Record |
| --- | --- | --- | --- |
| 1 | RC filter; [fixtures/rc](../fixtures/rc/PROVENANCE.md) is the hand-made reference | 3 footprints, 2 layers | AGENTEE-96 |
| 2 | One FOME-MC sheet as a standalone board: `sheets/psu.kicad_sch` or `sheets/wbo1.kicad_sch` | One block plus a connector | AGENTEE-97 |
| 3 | Standalone wideband controller board | Power, wideband channel, MCU core and CAN blocks | AGENTEE-98 |
| 4 | FOME-MC itself | 15 sheets, 4 layers, about 492 footprints | AGENTEE-99 |

What must exist at each stage before a rung can run:

| Stage | Rung 1 | Rung 2 | Rung 3 | Rung 4 |
| --- | --- | --- | --- | --- |
| 1 Requirements | Requirement IDs (the fixture's `R-native`, `R-width`, `R-response`); envelope with the fixture outline, 2-layer stackup and header edge | Sheet requirements from FOME-MC sources; envelope for a standalone board with one connector | Board requirements; envelope with enclosure connectors | FOME-MC requirements as captured; 4-layer envelope |
| 2 Parts | Approved-parts manifest: R, C and header with LCSC bindings; stock footprints | Block manifest with snapshot stock state | Merged block manifests; assembly readiness | About 492 footprints against one catalogue snapshot |
| 3 Schematic | One sheet; netlist parity with the fixture | One captured block, adapted, plus a root sheet | Several blocks integrated block-first | 15 sheets from power, MCU core, CAN, wideband and driver blocks |
| 4 Placement | Three footprints placed and graded | Decoupling distance, connector on edge | Sub-layout reuse, congestion report | Budget measurements before any limit change |
| 5 Routing | KRT attempt, complete and valid | Net classes from intent, routing rules | kicad-route hard-nets-first campaign, pour integrity | Chained scopes under the rule floor |
| 6 Verification | ERC, DRC with parity, copper width, ideal RC transient | Pin contracts, SPICE templates where models exist | DFM suite, fab profile rules | Full set; comparison with the hand-made board |
| 7 Fab package | Gerbers, drill, BOM, CPL, consistency gate | Plus CPL rotation correction | Plus Gerber/Excellon round trip | Full package; cost estimate as information |

The checks that constitute "done" for each rung:

| Rung | Declared check set that must pass | Fab exports |
| --- | --- | --- |
| 1 | `erc`, `drc` (with schematic parity), `netlist`, `hardware_interface`, `parts_subset`, `placement`, `geometry` with `copper-width/1`, `rc_transient` | `fab_package` passes |
| 2 | Rung 1 kinds except `rc_transient`, plus `block_provenance`, pin-contract and SPICE `measurements` checks for the sheet's declared requirements, and compiled routing rules through `drc` | `fab_package` passes, CPL rotation verified |
| 3 | Rung 2 set for every block instance and the integrated board, plus DFM geometry and fab profile rules | `fab_package` passes, round trip conserved |
| 4 | Rung 3 set at FOME-MC scale, plus FOME-MC's own rules (for example HV clearance) | `fab_package` passes |

`parts_subset`, `placement`, `block_provenance` and `fab_package` do not exist
yet (AGENTEE-86, AGENTEE-92, AGENTEE-90, AGENTEE-94). Requirements a rung
cannot check must be listed as limitations in its scope and its record; they
are not counted as passing.

## Starting a campaign from an empty candidate

The existing [campaign](campaign-guide.md) takes an `agent-ee.campaign/1`
configuration (`scope`, `review_profile`, `implementation`, `max_iterations`,
`max_seconds`) and a scope/2 or scope/3 file. It captures the scope's inputs,
runs every check, collects independent reviews and adjudicates. When checks
establish defects it gives the sole implementation writer a disposable copy of
the candidate at `/work/design`, captures the result as the next candidate and
repeats. [campaign.py](../src/agent_ee/campaign.py) refuses a proposal that
adds or deletes a file, changes a file outside `mutable_inputs`, or changes a
baseline source or adapter.

Generation reuses this loop unchanged in its roles (decision 1). The empty
candidate is an ordinary captured project:

- the owner-approved baseline: requirement IDs, targets and reference
  architectures from stage 1, plus the board envelope (AGENTEE-85);
- pinned libraries: stock symbol, footprint and template subsets, and the part
  directories approved in stage 2 (AGENTEE-86);
- an empty board: a `.kicad_pcb` holding only the Edge.Cuts outline, the chosen
  layer count and stackup and the design rules, with no footprints or copper;
- an empty root schematic, and for rung 2 and above one pre-created child sheet
  file per planned block, because the writer cannot add files;
- the block library entries selected for the board (AGENTEE-89), captured as
  read-only inputs.

The scope/2 fields that carry it:

| Field | Content in a generation scope |
| --- | --- |
| `inputs` | `project` (scaffold), `stock/symbols`, `stock/footprints`, `stock/template`, proposed `parts/` and `blocks/`, `requirements/` |
| `schematic`, `board` | The scaffold's root `.kicad_sch` and empty `.kicad_pcb` |
| `mutable_inputs` | Root sheet, pre-created child sheets and board. `.kicad_pro`, library tables and libraries stay immutable, so rules cannot be relaxed by a repair |
| `checks` | The rung's check set, ending with `fab_package` |
| `baseline.requirements` | Requirement IDs from the dialog, intent record and envelope, each mapped to checks |
| `baseline.targets`, `baseline.reference_architectures` | Captured stage-1 records and the block descriptions |
| `requirements`, `limitations` | Requirement text placed first in packets; unchecked conditions |

A proposed layout for the scaffold's scope fragment:

```json
{
  "schema": "agent-ee.scope/2",
  "inputs": [
    {"path": "scaffold/project", "at": "project"},
    {"path": "scaffold/parts", "at": "parts"},
    {"path": "scaffold/blocks", "at": "blocks"},
    {"path": "scaffold/requirements", "at": "requirements"}
  ],
  "schematic": "project/board.kicad_sch",
  "board": "project/board.kicad_pcb",
  "mutable_inputs": ["project/board.kicad_sch", "project/psu.kicad_sch", "project/board.kicad_pcb"]
}
```

No tool writes this candidate today; AGENTEE-87 adds a deterministic scaffold
command. Two properties of the current reducer also stop a generation campaign
early, and AGENTEE-88 addresses them:

1. On an empty design ERC and DRC pass vacuously. The first proposal that adds
   footprints fails DRC with unconnected items, and convergence step 2
   classifies that as `regression`.
2. A simulation whose nets do not exist yet is unknown coverage, so the campaign
   stops as `execution_incomplete`.

AGENTEE-88 proposes that a check over an empty subject fails with
`stage_not_reached` when the baseline declares expected content, and that
scopes declaring check stages count progress by the earliest failing stage.
Scopes without stage fields keep their current behavior. This changes the
convergence contract and needs owner review before it ships.

## Stages, epics and work

Owners are the agents holding the lanes on 2026-10-17. "Gap" items were filed
with this page; mined candidates are AGENTEE-23 to AGENTEE-77, each parented to
one epic (first stage label; with two labels the earlier stage, except that
fab-package items stay in stage 7).

| Stage | Epic | In flight (branch, owner) | Mined candidates | Gaps filed |
| --- | --- | --- | --- | --- |
| 1 Requirements | AGENTEE-78 | `impl/intent-record` AGENTEE-26, then AGENTEE-25 (Sol); `impl/interface-contract` AGENTEE-8 | 25, 26 | AGENTEE-85 envelope record |
| 2 Parts | AGENTEE-79 | `impl/catalog-selection` AGENTEE-27, then 28 (Sol); `impl/library-context` AGENTEE-36, then 35 (Sol); `impl/datasheet-facts` AGENTEE-18 | 27, 28, 31, 33–38, 61 | AGENTEE-86 approved-parts manifest |
| 3 Schematic | AGENTEE-80 | `impl/connectivity-ir` AGENTEE-39 and `impl/netlist-parity` AGENTEE-41 (Sol); AGENTEE-65, then 58 (Sol) | 39–52, 60, 62, 65, 69 | AGENTEE-87 scaffold, AGENTEE-88 non-vacuous progress, AGENTEE-89 block format, AGENTEE-90 block adaptation |
| 4 Placement | AGENTEE-81 | `impl/placement-judge` AGENTEE-53, then 54 (Sol) | 53–57 | AGENTEE-91 placement writer, AGENTEE-92 placement check kind |
| 5 Routing | AGENTEE-82 | `impl/routing-rules` AGENTEE-63, then 75 (Sol) | 63 | AGENTEE-93 kicad-route sandboxed writer |
| 6 Verification | AGENTEE-83 | `impl/netlist-extra-checks` AGENTEE-58 (Sol); `impl/active-spice`, `impl/kicad-happy`, `impl/passive-corners`, `impl/dc-resistance`, `impl/geometry`, `impl/seeded-benchmark` (AGENTEE-1 roadmap) | 58, 59, 67, 68, 70, 71 | AGENTEE-95 stage receipts, AGENTEE-96–99 rung records |
| 7 Fab package | AGENTEE-84 | AGENTEE-23, 72, 73 (Fable-413); AGENTEE-29, 32 (Argon) | 23, 29, 30, 32, 64, 66, 72–77 | AGENTEE-94 done gate |

Branches named here live under `~/git/agent-ee-worktrees/` as `impl/<name>`.
This page links to them; it does not implement them.

## Placement stage

Placement is the one stage with no existing tool (decision 4) and lives in
agent-ee ([reasons](decisions.md#placement-lives-in-agent-ee)).

**Writer.** When the earliest failing stage is placement, the implementation
writer proposes positions, rotations and sides in its disposable copy. It may
run pinned helpers as processes: forward annotation onto a staging grid
(AGENTEE-52), the greedy packer (AGENTEE-54) and plan-then-apply placers
(AGENTEE-55). In a placement iteration only footprint transforms may change;
outline, zones, rules, copper and envelope-fixed connectors are refused, as
inventory changes are today (AGENTEE-91). The writer's summary is not evidence.

**Checks** (AGENTEE-92, extending AGENTEE-53):

| Predicate | Source | Outcome |
| --- | --- | --- |
| Courtyard overlap, malformed or missing courtyard | Native `kicad-cli pcb drc` JSON from the preceding `drc` check | Defect; missing courtyard is unknown for that footprint |
| Ratsnest length | pcbnew probe: per-net minimum spanning tree over pad centres, Manhattan and Euclidean | Metric bounds the reducer can compare; a proxy, not routed length |
| Keepouts and edge clearance | Probe geometry against rule areas that forbid footprints and the fab profile's edge minimum | Defect |
| Connector on edge | Envelope declarations (AGENTEE-85) with the AGENTEE-64 predicate: edge, tolerance, insertion direction | Defect; outline not provably closed is unknown |
| Routability | A captured kicad-route `result.json`: `routing.blocked_by` and `kicad-route blocked` | Information only; never a pass |

Unconnected items are expected before routing and are not graded by
`placement`. The probe does not yet extract courtyards or outlines; the
`impl/placement-judge` branch adds that.

## kicad-route integration contract

kicad-route is the routing stage (decision 3, AGENTEE-93). The
[routing adapter](routing-adapter.md) runs one attempt as a coordinator step of
the implementation (AGENTEE-95) in the networkless sandbox. The writer neither
runs the router nor supplies its arguments. The adapter is pinned to
kicad-route `08179cd`. At that commit agent-ee relies on the following from
`src/kicad_route/cli.py`, checked by the adapter rather than trusted:

- **Exit codes** (`overall_exit`, `ledger.REFUSED_EXIT`): 0 complete and valid;
  1 backend or process failure, or an early failure with no `result.json`;
  2 valid but incomplete, and also argparse's usage exit; 3 fails validation,
  regardless of completion; 4 refused before routing (ledger or canary), with
  the run directory removed. agent-ee uses the board from 0 and 2 as a
  proposal only when the exact status combination and a contained candidate
  agree with the exit. It records 1 and 4 as missing routing coverage, never
  uses the board from 3, and treats any other combination as a contradiction.
- **Outputs**: `--out` is a global option before `run`. Each attempt writes
  `OUT/runs/<name>/<label>/<stamp>/` with a mutable `latest` link and no
  top-level result. `result.json` has no schema version, and the pin has no
  `--version` flag, so the tool version is recorded as unknown. Backend
  identity comes verbatim from `result.json`. agent-ee retains and hashes
  `result.json`, `attempt.json`, `RESULT.md`, `validate/` and the logs.
- **Preservation**: project files, outline, layers, stackup, footprints, zones
  and keepouts unchanged; only in-scope new copper graded. This is why
  placement cannot live in kicad-route.
- **State directory**: `KICAD_ROUTE_STATE` (the checkout by default) holds
  `vendor/krt`, `vendor/freerouting`, `.venv-krt` and `vendor/skills`. agent-ee
  mounts the checkout and state read-only, points `--out` into the step's
  `/work`, and never runs `kicad-route update`. On the original placed RC,
  KRT attempts completed with every write probe into the state refused with
  `EROFS`, and the host state's file contents unchanged. The
  [adapter page](routing-adapter.md#limits) lists what that does not cover.
- **Backends**: KRT is the default. Repeated KRT attempts on the placed RC gave
  the same geometry but not identical bytes, so KRT determinism is not
  claimed. A Freerouting run is an attempt; agent-ee does not cite it as a
  reproducible measurement.

kicad-route's validation is diagnostic for agent-ee. Acceptance comes only from
agent-ee's own fresh checks on the captured proposal: DRC with parity and no
unconnected items, `copper-width/1`, and compiled routing rules (AGENTEE-63).

## Obligations every new stage inherits

From decision 5 and the [closed-loop boundaries](closed-loop.md#five-enforced-boundaries):

- Inputs are captured and hashed; a stage reads captured bytes, never a live
  checkout, catalogue or network source.
- Tool versions and executable hashes are recorded per run. Writer-side tools
  (scaffold, placers, kicad-route) get stage receipts (AGENTEE-95).
- Checks run sandboxed with `execution` separate from `outcome`. A crash,
  missing output or unsupported input is unknown coverage, never a pass.
- Initial reviewers are independent; only sealed coordinator check findings
  become accepted scope/2 defects.
- The writer's output counts only after capture; its summary is not evidence.
- A stage that cannot meet these is not a verified stage and cannot satisfy a
  requirement.

## Licence posture

- agent-ee: MIT is proposed on `impl/license` (AGENTEE-14), with an inventory
  of third-party-origin files. It is not merged or owner-confirmed.
- kicad-route: no `LICENSE` file and no licence field in `pyproject.toml` yet;
  MIT is in progress in workstream B.
- KRT (KiCadRoutingTools) is MIT. Freerouting (GPL-3.0) runs as a separate
  Java process. KiCad's `kicad-cli` and ngspice run as separate processes.
- Decision 6: GPL tools are called as processes, never linked into agent-ee.
  `agent_ee/geometry_probe.py` and kicad-route's facts and Specctra bridge
  import KiCad's `pcbnew` Python module in their own child processes. Whether
  that satisfies the process boundary for distribution is an open question for
  AGENTEE-14, not settled here.

## Limits

- The ladder records are plans. No rung has run end to end.
- Campaigns are capped at 100 iterations and 86400 seconds, and packets at
  1000000 bytes. Rung 4 may exceed these; AGENTEE-99 measures before any
  change is proposed.
- Ratsnest length is a placement proxy. It is not routed length, signal
  integrity or thermal evidence.
- Block evidence is provenance. A board's acceptance comes from the board's own
  checks, even for an unchanged block.
- Nothing here confers hardware release authority or bench qualification.
