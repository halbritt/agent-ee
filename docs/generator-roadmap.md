# Generator roadmap

**Status: planning.** This page plans how agent-ee becomes the engine of an
AI-driven PCB generator. Nothing on it has shipped unless it links to an
existing reference page. The owner's [generator direction](decisions.md#generator-direction-2026-10-17)
is the authority; this page applies it. Plane epics AGENTEE-78 to AGENTEE-84
track the stages, and new items AGENTEE-85 to AGENTEE-99 track the gaps found
while writing it. An objection to a decision is a Plane item for the owner, not
an edit here.

## Target experience

Stage-three repair turns can opt into [stable schematic regeneration](schematic-regeneration.md)
(AGENTEE-43): prior symbols retain their placement and identities, new symbols
occupy free cells, and a hash-bound change report records removals and renames.
The producer remains subject to native checks and independent review.

The user describes a board, discusses parts with the agent, and says "go". The
result is a JLCPCB-ready package: Gerbers, drill files, BOM and CPL, from a
native KiCad project in which every step has tool evidence. The target product
is FOME-MC. agent-ee supplies the loop and the checks, kicad-route supplies
routing, and FOME-MC supplies the reference blocks and the final benchmark.

"Done" has one meaning (decision 1): a campaign reaches `criteria_satisfied`,
so every declared check passed on the same captured candidate, and a fab package
check found the exports present and consistent. Since 2026-10-18, each stage with a
declared step must also have produced its graded content through that step's receipt
([done through declared steps](stage-receipts.md#done-through-declared-steps)). Model prose, a writer's summary
or a router's self-report never establishes done.

## Benchmark ladder

Each rung is one end-to-end run, from requirements to fab package, recorded as
evidence (AGENTEE-96 to AGENTEE-99).

| Rung | Board | Size | Record |
| --- | --- | --- | --- |
| 1 | RC filter; [fixtures/rc](../fixtures/rc/PROVENANCE.md) is the hand-made reference | 3 footprints, 2 layers | AGENTEE-96. Rung 1 evidence: see `build/rung1-evidence-index-2026-10-17.md` (gitignored; AGENTEE-20) and Plane AGENTEE-96 |
| 2 | One FOME-MC sheet as a standalone board: `sheets/psu.kicad_sch` or `sheets/wbo1.kicad_sch` | One block plus a connector | AGENTEE-97 |
| 3 | Telemetry pod | Power, MCU/radio, CAN, IMU, GNSS and analog front end | AGENTEE-98 |
| 4 | FOME-MC itself | 15 sheets, 4 layers, about 492 footprints | AGENTEE-99 |

The telemetry pod's [CAN reference-set capture](pod-can.md) binds the two
pinned source instances. A selected-reference composition plan now emits four
parts with eight native ports and grades provenance against both source exports.
AGENTEE-176 may consume that producer; source-review qualification and actual pod
acceptance remain separate open gates.

What must exist at each stage before a rung can run:

The pod's pinout CSV now compiles through the [io-map compiler](io-map.md)
(AGENTEE-188). Its pinned fixture retains unresolved MCU pins and native
bindings, so it establishes traceability without claiming schematic acceptance.

| Stage | Rung 1 | Rung 2 | Rung 3 | Rung 4 |
| --- | --- | --- | --- | --- |
| 1 Requirements | Requirement IDs `R-native`, `R-width`, `R-response`, `R-fab`, `R-parts`, compiled from the spec by the [requirement-record compiler](requirement-scope.md) (AGENTEE-100); envelope with the fixture outline, 2-layer stackup and header edge | Sheet requirements from FOME-MC sources; envelope for a standalone board with one connector | Board requirements captured from the pod spec's tables and lists by [spec capture](spec-record.md) (AGENTEE-158, `fixtures/telemetry-pod/spec-record.json`); envelope with enclosure connectors | FOME-MC requirements as captured; 4-layer envelope |
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

The [`parts_subset`](approved-parts.md) check and immutable manifest are
registered for scope/2 and scope/3 (AGENTEE-86). The optional
[Rung-1 captured-parts route](rung1-parts.md) consumes that bundle and binds its
RC/header fields and records a real reference-board parts run. Complete
empty-candidate campaign qualification remains pending; the default generator
still uses its experimental parts record.
The telemetry pod's [hand-fit deviation contract](assembly-readiness.md#declared-hand-fit-deviations)
records unavailable 776228-1, NEO-M9N and SMA parts with named purchase sources
and assembly exclusions. It grades remaining BOM stock and preserves explicit
fab-manifest exclusions; it does not establish a complete pod campaign.
`placement` is also registered ([details](placement.md#placement-check-kind));
its native qualification does not establish a complete generation campaign
(AGENTEE-92). `block_provenance` and `fab_package` remain pending (AGENTEE-90,
AGENTEE-94). The export half of the
fab package exists as [`fab_export`](fab-export.md) (AGENTEE-23); the
[consistency gate](fab-consistency.md) also exists (AGENTEE-73), including native
CPL coordinate and export-log checks. The separate
[Gerber/Excellon round-trip check](fab-roundtrip.md) (AGENTEE-74, AGENTEE-195)
compares layer presence, drill histograms, copper pad/via flashes and extents,
track/arc and zone-region counts and outline bounds, through KiCad 10's
aperture macros, arcs and regions.
CPL rotation policy remains AGENTEE-72.

CPL coordinate and export-log checks. CPL rotation policy remains AGENTEE-72.
The rung-3 DFM geometry rules exist as [`dfm_geometry`](dfm-geometry.md) (AGENTEE-76).

CPL rotation correction is [`cpl_rotation`](cpl-rotation.md) (AGENTEE-72).
The rung-4 cost estimate is [`jlc_cost_estimate`](jlc-cost-estimate.md)
(AGENTEE-77). It is informational only and maps to no acceptance requirement.

[`fab_package`](fab-package.md)
(AGENTEE-94) is the done gate: declared last, it runs the
[`fab_export`](fab-export.md) producer (AGENTEE-23) and the
[consistency gate](fab-consistency.md) (AGENTEE-73), including native CPL
coordinate and export-log checks, and seals the artifact hashes. A scope with a
`generation` marker is refused at capture without it.

[Net constraints](net-constraints.md) (AGENTEE-47) emit intent netclasses, width
rules and the record that `copper-width/1` binds. The [scaffold producer](scaffold.md#per-net-classes)
(AGENTEE-184) consumes captured constraints directly and emits the classes,
assignments, rules and record beside the board. A native routing attempt must
still consume that project; scaffolding establishes no routed-board acceptance.
Requirements a rung cannot check must be listed as limitations in its scope
and its record; they are not counted as passing.

The [original RC stage fixtures](rc-stage-fixtures.md) provide unplaced,
placed-unrouted and seeded placement inputs with native checks and a recorded
KRT attempt. They are independent alternatives to the FOME-MC reference;
AGENTEE-103's exact control-derived variants remain blocked by unresolved
third-party rights. These fixtures do not qualify the complete rung.

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

The [board envelope check](board-envelope.md#native-envelope-check) now owns exact
native outline and copper-layer comparisons. AGENTEE-161 adds source-bound
profile stackup authority and individual reference selectors for fixed connectors.
AGENTEE-162 adds per-hole and per-keepout bindings. Its legacy dynamic antenna
derivation is now withdrawn because it mistook a pin label for clearance; that
item remains unknown pending case-specific physical qualification. A scope can
capture; unresolved generator mechanical obligations still refuse acceptance
by name. The diagnostic round trip does not satisfy them.

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

The [scaffold command](scaffold.md) now creates deterministic native files from
production envelope and parts records plus an explicit stage-one scope and
project-rule template. It copies selected dependencies, pre-creates child sheets
and preserves checks and baseline mappings. Its stage-two producer (AGENTEE-141)
compiles the scope/2 checks, mappings, stage policy and `.kicad_pro` rules from
the envelope's fab profile and the parts manifest. Per-net classes (AGENTEE-47),
hierarchical staged presence and complete production envelope acceptance remain
unavailable. Both rung-1 drivers build their empty project with the same
module's builder (AGENTEE-134; [RC campaign scaffold](rung1-campaign.md#empty-scaffold)),
from their own generation inputs and stock library tables rather than through the command.

The [block adapter](block-adaptation.md) can now fill pre-created child sheets
from captured flat blocks, freeze instance records and map block requirements
to fresh board checks. It is a standalone scaffold seam; AGENTEE-87 still owns
the command that creates the complete empty candidate. Two properties of the
current reducer also stop a generation campaign early, and AGENTEE-88 addresses them:

Opt-in [staged campaigns](staged-campaign.md) implement AGENTEE-48/88 in the
existing check scheduler and convergence reducer:

1. A successful native probe that confirms missing expected content returns
   `ok/defect`, reason `stage_not_reached`. Empty ERC and DRC subjects cannot
   earn passing results before components exist.
2. Earlier defects block later checks, including simulation, until their gates
   open. Tool crashes and missing output remain unknown coverage and stop the
   campaign as `execution_incomplete`.
3. Progress comes from shrinking the failed set of the earliest failing stage.
   Checks previously evaluated as `ok/pass` or `ok/info` remain protected against
   regression. Configuration loading refuses a `finding_retry_limit` below 2;
   its count includes the first finding appearance.

Scopes without stage declarations keep their unstaged progress rules. The declaration
and expected subjects are part of the immutable baseline; history records retain
`earliest_failing_stage` and the per-stage failed sets. The owner
[signed off the convergence contract with two guards](decisions.md#convergence-contract-signed-off-with-two-guards);
executable behavior does not establish independent review. A complete native
generation rung has the additional limits recorded in the
[qualified RC campaign](rung1-qualified.md).
The [stage-ordered driver](rung1-stages.md) (AGENTEE-104) runs one RC candidate
through the fixed stage order without inference. It records each stage as passed
from sealed checks, completed by a receipt, or not reached with its owning item.
Hierarchical presence is still unsupported; an empty flat subject needs a
successful native probe.

## Stages, epics and work

Owners are the agents holding the lanes on 2026-10-17. "Gap" items were filed
with this page; mined candidates are AGENTEE-23 to AGENTEE-77, each parented to
one epic (first stage label; with two labels the earlier stage, except that
fab-package items stay in stage 7).

| Stage | Epic | In flight (branch, owner) | Mined candidates | Gaps filed |
| --- | --- | --- | --- | --- |
| 1 Requirements | AGENTEE-78 | `impl/intent-record` AGENTEE-26, then AGENTEE-25 (Sol); `impl/interface-contract` AGENTEE-8 | 25, 26 | AGENTEE-85 envelope record |
| 2 Parts | AGENTEE-79 | `impl/catalog-selection` AGENTEE-27, then 28 (Sol); `impl/library-context` AGENTEE-36, then 35 (Sol); `impl/datasheet-facts` AGENTEE-18 | 27, 28, 31, 33–38, 61 | AGENTEE-86 approved-parts manifest |
| 3 Schematic | AGENTEE-80 | `impl/connectivity-ir` AGENTEE-39 and `impl/netlist-parity` AGENTEE-41 (Sol); AGENTEE-65, then 58 (Sol); [`net_names`](net-names.md) AGENTEE-44; [`regeneration_determinism`](design-diff.md) AGENTEE-51; [`design_rules`](design-rules.md) AGENTEE-62; [`topology_rules`](topology-rules.md) AGENTEE-60 | 39–52, 60, 62, 65, 69 | AGENTEE-87 scaffold, AGENTEE-88 non-vacuous progress, AGENTEE-89 block format, AGENTEE-90 block adaptation |
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
inventory changes are today (AGENTEE-91). Envelope mounting holes are protected
the same way, and the packer treats fixed courtyards as exact obstacles
(AGENTEE-179). The writer's summary is not evidence.
[Sub-layout reuse](sublayout-reuse.md) (AGENTEE-56) copies a captured module
layout's placement and group-local copper into new instances. A placement
iteration admits that copper only when the declaration names the captured
request and sub-boards: whole groups of new members, copied exactly from the
captured sub-board onto the voted nets inside the group region (AGENTEE-129).

**Checks** (AGENTEE-92, extending AGENTEE-53):

| Predicate | Source | Outcome |
| --- | --- | --- |
| Courtyard overlap, malformed or missing courtyard | Native `kicad-cli pcb drc` JSON from the preceding `drc` check, plus probe courtyard inventory | Native overlap is a defect; malformed or missing courtyard is unknown for that footprint |
| Ratsnest length | pcbnew probe: per-net minimum spanning tree over pad centres, Manhattan and Euclidean | Metric bounds the reducer can compare; a proxy, not routed length |
| Keepouts and edge clearance | Native footprint-keepout DRC plus probe cross-check; exact courtyard-to-outline distance against explicit `min_courtyard_edge_mm` | Proved violation is a defect; unqualified geometry is unknown |
| Connector on edge | Envelope declarations (AGENTEE-85) with the AGENTEE-64 predicate: edge, tolerance, insertion direction | Defect; outline not provably closed is unknown |
| Connector facing, test-point access and coverage | [`dfa_dft`](dfa-dft.md) (AGENTEE-64): declared insertion direction against the nearest saved edge; declared test points against same-side courtyards; declared probe points on every rail and acceptance-measured net, with `bringup_plan.json` | Defect; undeclared values, unproven courtyards and corners are unknown |
| Routability | A captured kicad-route `result.json`: `routing.blocked_by` and `kicad-route blocked` | Information only; never a pass |

Unconnected items are expected before routing and are not graded by
`placement`. The saved-board probe extracts courtyards, outlines, rule areas
and pad centres. Placement checks reuse the preceding native DRC report;
they do not run a second DRC. The [placement reference](placement.md#native-qualification)
maps the ticket's acceptance criteria to executable tests.

The fab profile's `min_edge_clearance_mm` is a copper-to-edge limit. It does
not supply a courtyard-to-edge minimum. Binding a sourced fabrication limit
for courtyards and adopting these checks in a complete generation campaign
remain separate work.

## kicad-route integration contract

kicad-route is the routing stage (decision 3, AGENTEE-93). The
[routing adapter](routing-adapter.md) runs one attempt as a coordinator step of
the implementation (AGENTEE-95) in the networkless sandbox. The writer neither
runs the router nor supplies its arguments. The adapter is pinned to
kicad-route `a132899`. At that commit agent-ee relies on the following from
`src/kicad_route/cli.py`, checked by the adapter rather than trusted:

- **Exit codes** (`overall_exit`, `ledger.REFUSED_EXIT`): 0 complete and valid;
  1 backend or process failure, or an early failure with no `result.json`;
  2 valid but incomplete; 3 fails validation,
  regardless of completion; 4 refused before routing (usage, input, ledger, canary or state), with
  the run directory removed. agent-ee uses the board from 0 and 2 as a
  proposal only when the exact status combination and a contained candidate
  agree with the exit. It records 1 and 4 as missing routing coverage, never
  uses the board from 3, and treats any other combination as a contradiction.
- **Outputs**: `--out` is accepted before or after `run`; the adapter uses `run --out /work/routing/out --json`. Each attempt writes
  `OUT/runs/<name>/<label>/<stamp>/` with a mutable `latest` link and no
  top-level result. `result.json` requires schema `kicad-route/route/1`, or `route/2` only with
  coordinator-declared `diff_pairs: "auto"`; unknown versions are
  refused. The pin has no `--version` flag, so the tool version is recorded as unknown. Backend
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
  the same geometry but not identical bytes. Determinism means
  [normalized copper geometry equality](routing-equivalence.md);
  `routing_equivalence` checks it while recording byte differences. A Freerouting run is an attempt; agent-ee does not cite it as a
  reproducible measurement.

kicad-route's validation is diagnostic for agent-ee. Acceptance comes only from
agent-ee's own fresh checks on the captured proposal: DRC with parity and no
unconnected items, `copper-width/1`, and compiled routing rules (AGENTEE-63).

## Obligations every new stage inherits

From decision 5 and the [closed-loop boundaries](closed-loop.md#five-enforced-boundaries):

- Inputs are captured and hashed; a stage reads captured bytes, never a live
  checkout, catalogue or network source.
- Tool versions and executable hashes are recorded per run. Writer-side tools
  (scaffold, placers, kicad-route) get [stage receipts](stage-receipts.md)
  (AGENTEE-95). The campaign runs declared kicad-route steps through the routing
  adapter and verifies their receipts and semantics before adoption and on replay.
  The placement adapter also uses this receipt pipeline. The [rung 1 campaign
  driver](rung1-campaign.md) also records a receipt for its scaffold step, which its
  record's `done` binds to the first capture. New campaign configurations import it under
  `pre_receipts`, so verify-campaign revalidates it against that capture as well.
- Checks run sandboxed with `execution` separate from `outcome`. A crash,
  missing output or unsupported input is unknown coverage, never a pass.
- Initial reviewers are independent; only sealed coordinator check findings
  become accepted scope/2 defects.
- The writer's output counts only after capture; its summary is not evidence.
- A stage that cannot meet these is not a verified stage and cannot satisfy a
  requirement.

## Licence posture

- agent-ee: MIT is proposed in main (AGENTEE-14), with
  a [file-origin inventory](file-origins.json) of every tracked file and a
  [summary](third-party-origins.md). The proposal is merged; owner confirmation remains pending.
  FOME-MC fixture, donor, vendor and other third-party files stay excluded
  until the owner decides each origin.
- kicad-route: no `LICENSE` file and no licence field in `pyproject.toml` yet;
  MIT is in progress in workstream B.
- KRT (KiCadRoutingTools) is MIT. Freerouting (GPL-3.0) runs as a separate
  Java process. KiCad's `kicad-cli` and ngspice run as separate processes.
- Decision 6: GPL tools are called as processes, never linked into agent-ee.
  `agent_ee/geometry_probe.py` and kicad-route's facts and Specctra bridge
  import KiCad's `pcbnew` Python module in their own child processes. Whether
  that satisfies the process boundary for distribution is open, not settled
  here. AGENTEE-14 records it as an owner question in
  [third-party origins](third-party-origins.md#limits), which also notes that
  `benchmark.verify_pcb_protection` imports `pcbnew` inside the seeded-benchmark
  preparation process.

## Limits

- The ladder records are plans. No rung has run end to end.
- Campaigns are capped at 100 iterations and 86400 seconds, and packets at
  1000000 bytes of embedded artifact text (100000 per text artifact). The
  serialized reviewer packet also contains check receipts, baseline and navigation
  metadata, so its total JSON bytes can exceed that configured text budget.
  AGENTEE-198's no-inference packet probe measures both quantities before a
  limit change is proposed; neither packet caps nor profile timeouts are changed.
  Run it on explicit captured pod stage runs, using a fresh output directory:

  ```sh
  PYTHONPATH=src python3 -m agent_ee.packet_probe \
    --candidate empty=/path/to/empty/dry-run \
    --candidate schematic=/path/to/schematic/dry-run \
    --candidate placement=/path/to/placement/dry-run \
    --candidate routing=/path/to/routing/dry-run \
    --out build/pod-packet-budget
  ```

  `packet-budget.json` records exact reviewer JSON bytes by stage and by section
  (inputs, netlist, DRC, probe output, check receipts, and metadata), source hashes,
  overflow contributions, artifact truncation and local assembly time. The copies
  under the output directory retain assembled packets without changing source runs.
  Process and network launches are refused during the probe; no model runs.
  Missing or damaged captures are recorded as unavailable and exit status is 1
  when any stage is unavailable. Failed native checks retain their failed status;
  a measured packet is not a passed stage. Stage labels identify operator-supplied
  captures and do not certify their maturity or project. Synthetic test evidence
  covers oversize detection and no harness invocation, not pod-scale qualification.
  Actual pod stage measurements remain pending until those captured candidates
  exist. Per-call inference time, tokens and provider cost are unmeasured; the
  probe neither estimates them nor changes the profile timeout. AGENTEE-99 still
  requires measurements at rung 4.
- Ratsnest length is a placement proxy. It is not routed length, signal
  integrity or thermal evidence.
- Block evidence is provenance. A board's acceptance comes from the board's own
  checks, even for an unchanged block.
- Nothing here confers hardware release authority or bench qualification.

## Captured symbol subsets

AGENTEE-172 reduces schematic input size through the existing symbol-capture
boundary. Generator and rung-1 scaffold inputs retain selected symbols and
ancestors with both source and subset hashes; emission shares parsed libraries
within one invocation. The [emitter benchmark](schematic-emitter.md#library-subsets-and-emission-cost)
records pod-scale timing and native connectivity evidence without changing
generation acceptance or claiming a completed pod design.

## Captured CPL correction in fab stages

AGENTEE-126 wires `cpl_rotation` after `fab_export` in the generator and, when
`--cpl-inputs` is given, in both rung-1 scopes, and uses the shared receipted fab
step in the generator and rung implementations. Explicit `--cpl-inputs`
captures freeze the policy, supplier provenance and raw geometry. In the rung-1
campaign the `fab_package` done gate seals the corrected CPL and records the
rotation status and policy digest. The corrected package records all applied
corrections and retains `PREVIEW_REQUIRED`; done predicates are allow-lists over
`fab_package.DONE_ROTATION_STATUSES`, and by the owner decision of 2026-10-18
(AGENTEE-151) a pending preview is done only as an operator obligation: each
record lists the recomputed `preview_obligations` (every corrected CPL
designator with its side) to confirm in the JLCPCB placement preview before
ordering. Missing inputs, incomplete rotation coverage and missing obligations
fail closed.
See [CPL integration](cpl-rotation.md#generator-fab-integration) for the input
tree, evidence layout and the separate non-DNP header export limitation.

AGENTEE-164 adds the [named-part path](approved-parts.md#named-ics-and-modules)
for stage 2: ICs and modules can enter `parts/1` without extending the generic
catalog categories. Per-LCSC records replay captured supplier identity and
AGENTEE-33 datasheets; `parts_subset` and the scaffold producer retain their
immutable evidence closure. Synthetic tests establish this admission path;
a live public-search capture and an actual telemetry-pod design remain separate
qualification work.

AGENTEE-165 adds generic `switching_regulator` selection with typed input-voltage
maximum, output-current, frequency and temperature claims, exact `sync`/`async`
rectification, and captured package matching. The native DC-DC importer preserves
source claims; selection receipts name unknown-attribute exclusions. This shares
the catalog replay already consumed by `parts/1` and complements AGENTEE-164's
exact named-IC records. It does not add parametric claims to named records or
select an actual pod regulator without captured rows and declared constraints.
