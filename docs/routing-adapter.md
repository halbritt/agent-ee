# kicad-route routing adapter (AGENTEE-93)

kicad-route is the routing stage of the [generator](generator-roadmap.md)
(decision 3). agent-ee does not route. The adapter runs one kicad-route
attempt as a coordinator step in the networkless sandbox, with the kicad-route
checkout and its state directory mounted read-only. It retains what the attempt
produced and decides whether the attempt's board may become a proposal. It
never decides whether the board is acceptable: that comes only from agent-ee's
own fresh checks on the captured proposal.

The adapter has two halves:

- [`kicad_route_attempt.py`](../src/agent_ee/kicad_route_attempt.py) runs
  inside the sandbox. It imports only the standard library and is mounted as a
  single read-only file, so its bytes are part of the step's tool identity.
- [`routing_adapter.py`](../src/agent_ee/routing_adapter.py) runs in the
  coordinator. `preflight()` checks the external tool, `step()` declares the
  attempt as an AGENTEE-95 `agent-ee.stage-step/1`, and `verify()` re-derives
  the outcome from the retained bytes.

Campaigns opt in with `routing_adapter` and run it through the existing step plan.
The coordinator verifies both the generic receipt and routing semantics before
adoption, and repeats those checks in `verify-campaign`.

## Pinned compatibility

The adapter supports kicad-route commit
`b5774496d8466b67ffa0362b6665fee2108155a3` only (AGENTEE-450; see the
[pin qualification record](#pin-qualification-record-agentee-450-2026-10-22)).
Routing evidence from any other pin is refused as `StaleRoutingEvidence`.
It requires `result.json` schema `kicad-route/route/1` and the exact top-level
result and attempt keys written by this pin. Unknown or absent result schema
versions are contradictions, even when the statuses and exit look successful.
Default `attempt.json` requires `kicad-route/attempt/1`, including `schema` and
`rules_files`. The coordinator can declare `diff_pairs="auto"` to pass
`--diff-pairs auto` (KRT only). This requires `route/2` plus `attempt/2`, even
for an empty detected plan. Only these declared runs admit the result `pairs`,
attempt `pairs`/`pair_routing`, and settings `diff_pairs`/`pairs` fields.
Missing fields, downgraded versions, undeclared pair keys, and disagreement
between the settings plan, attempt plan and result pair identities are
contradictions. Pair evidence remains provenance; it does not pass a check.
The adapter checks the tool as well as its reports:

- HEAD is read from the checkout's git files. agent-ee does not run `git`
  there, because `git status` can rewrite the index.
- Every regular file under `bin/` and `src/` must match the pinned blob hashes
  exactly. An extra file, such as a `sitecustomize.py`, is refused. `__pycache__`
  is ignored because the router runs with `-B` and a private
  `PYTHONPYCACHEPREFIX` under `/work`, so cached bytecode in the checkout is
  never read or written.
- Any mismatch stops the step before the router runs. Nothing updates or
  repairs the external checkout.

The reported tool version is recorded as `unknown`: the pin has no version
flag, and `doctor` is not used because it looks up upstream releases. The
backend identity (`result.json` `backend`: KRT version, build, commit, Rust
router) is copied verbatim when a result exists. Nothing is filled in when it
does not.

## The step

`routing_adapter.step(project=, board=, schematic=, name=, label=, checkout=,
state=None, budget=300, timeout=None, backend='builtin', diff_pairs=None,
layer_policy=None)` returns:

| Field | Value |
| --- | --- |
| `argv` | `/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py --project P --board B --schematic S --name N --label L --budget T --timeout U --backend builtin --state checkout` |
| `mounts` | adapter file at `/opt/agent-ee-routing/kicad_route_attempt.py`; checkout at `/opt/kicad-route`; a separate state directory, when there is one, at `/opt/kicad-route-state` |
| `source` | `/opt/kicad-route/src` |
| `writes` | `{"P/B": "routing/candidate/P/B"}`: the board is the only file the step can change |
| `artifacts` | `routing/adapter-report.json`, the router's stdout/stderr, and `routing/attempt/{result.json, attempt.json, RESULT.md, snapshot.json, validate, facts, work}`, and the staged input tree `routing/agent-ee-original` |
| `backend_report` | `routing/attempt/result.json`, key `backend` |
| `adoptable_exits` | `[0, 2]` |
| `timeout_s` | the attempt timeout plus 300 s for hashing and retention |

There is no `version_argv`. The coordinator, not the writer, declares the step.

Include a strict declaration in the campaign's frozen configuration. For example,
where `config` is an `agent-ee.campaign/1` object:

```python
config["routing_adapter"] = {
    "schema": "agent-ee.routing-adapter/1",
    "project": "project", "board": "rc_filter.kicad_pcb", "schematic": "rc_filter.kicad_sch",
    "name": "rc_filter", "label": "routing", "checkout": "/opt/kicad-route", "budget": 120,
}
```

The declaration allows only the fields shown plus `state`, `timeout`, `backend`, opt-in `diff_pairs: "auto"`
and an opt-in [`layer_policy`](#declared-layer-policy-agentee-231).
Defaults match `step()`: state is the checkout, backend is KRT, budget is 300 s,
and timeout is twice the budget plus 300 s. No argv, mount, environment or pin override
is accepted. The selected board and schematic must equal the scope's identities,
and the board must be in `mutable_inputs`. The scope must be staged and declare
routing. The campaign dispatches exactly one routing entry only when routing is
its earliest failing stage; explicit routing `implementation_steps` cannot accompany
this opt-in. The writer must leave the complete placed source tree unchanged on
that turn. A writer edit stops before the router starts.

The first captured candidate freezes `state.json.routing_binding`: the canonical
step, production adapter hash and pin, executable, source tree and complete mount
identities. The adapter hash must match the captured policy. Before launch, a
changed checkout or backend state is refused rather than rebaselined. Receipt
observations before and after execution must equal this binding. Replay first
checks the anchored receipt chain, then the routing binding and exact executor
command, including read-only mount source/target pairs and network isolation.
It does not require the external checkout still to exist.
Generic stage-step programs, including historical rung scripts, retain their
existing behavior and do not produce the adapter's qualified routing evidence.

Inside the sandbox the attempt script accepts only those options. Each value
must match a fixed pattern. The script does not use argparse, because its usage
exit 2 would read as a partial route. It then runs:

```text
/usr/bin/python3 -B -X pycache_prefix=/work/routing/pycache /opt/kicad-route/bin/kicad-route
  run --out /work/routing/out --json --backend builtin --dir /work/routing/agent-ee-original/P --board B --schematic S
  --name N --label L --protect existing --budget T
```

`--out` names the sandbox's writable output directory; the pin accepts it
before or after `run`. `--json` produces a versioned record on stdout. The design
stays in the read-only `/candidate` mount, so the router cannot edit it. The router
reads a staged copy instead (AGENTEE-442). After every other precondition holds,
the adapter copies the verified `/candidate` tree to
`/work/routing/agent-ee-original/`. The copy is real: regular files and
directories with the same bytes and modes, never a link. That directory is
outside the router's `--out`, and `--dir` is `/work/routing/agent-ee-original/P`.
kicad-route's `experiment._origin` treats a `--dir` whose parent is named
`candidate`, or whose grandparent holds `attempt.json` or `result.json`, as a
run candidate with a missing predecessor. So `/candidate/P` is never an original
(agent-507 origin adjudication). The staged path has neither, so the producer
reads it as an original at depth 0. `report.inputs.staged` records the staged
`path` and `root`, the per-file sha256 manifest (`files`) and the tree identity
(`sha256`) of the copy. A copy that differs from the captured input is
`not_run` and the router does not start.

After the router exits, the adapter observes the staged tree again. It seals that
observation as `report.inputs.staged_after`. The status is `observed`, with the
files and tree identity, or `missing`, or `unreadable`. Anything other than the
unchanged copy makes the attempt a contradiction ("the staged original changed
during the attempt"). Such an attempt is never adoptable.

The staged tree is required replay evidence. It is a step artifact, so the stage
receipt seals the digest of every staged file, and `adopt` refuses once
`validate_receipt` sees a staged file deleted, added or changed. `verify`
requires the following:
- the staged record is at the adapter's staging path;
- its manifest and identity equal the declared input (the receipt's `inputs`);
- the retained tree is exactly the sealed post-run observation, for every
  classification, contradictions included;
- the receipt's staged-file digests are the report's.

So a deleted tree, a deleted file, or a stale or edited tree refuses. A later
edit to an honest write contradiction refuses too (review H1, H3).

The input's owning context is judged before anything is copied (review H2).
`stage_receipts.run_step` asks `routing_adapter.input_refusal` about the host
`design/<project>`, using kicad-route's own rule (`cli.previous_step`,
`experiment._origin`):
- **Genuine predecessor.** The input is the candidate named by the
  `attempt.json` `candidate_dir` two levels up.
- **Missing predecessor.** The input's parent is named `candidate`, or a run
  record lies beside it, without that owner.

The adapter retains no predecessor receipts, and staging would restage the input
at depth 0. So both cases are refused before any receipt directory exists. A
genuine owner, or a run record beside the input, refuses every routing step. A
parent that is only named `candidate`, with no run record, is kicad-route's
missing predecessor only for its experiment origin. It therefore refuses only a
step that declares `experiment_policy`, which is the one request whose evidence
records origin. With a declared policy, only an input that kicad-route itself
would read as an original is staged. Its
environment is rebuilt from constants: `PATH`, `HOME`/`TMPDIR`/XDG under
`/work/routing`, `LANG`, `PYTHONDONTWRITEBYTECODE`, `PYTHONPYCACHEPREFIX`,
`PYTHONNOUSERSITE`, `KICAD_ROUTE_STATE` set to the read-only mount, and
`GIT_OPTIONAL_LOCKS=0` with no system or global git configuration. Nothing is
inherited. `--backend-env`, `--force`, `--canary`, `--rev`,
`KICAD_ROUTE_LEDGER` and `KICAD_ROUTE_OUT` are never passed. `--backend-arg` and
`--pour-area-loss` are passed only for a declared layer policy, as described
below.

## Declared layer policy (AGENTEE-231)

Without a policy, KRT routes on every copper layer at equal cost on a four-layer
board, and kicad-route refills zones after routing, so signal tracks on In1/In2
cut the reference planes. pour_integrity then fails only on lost pad contact,
starved spokes or stranded pads. Area loss is reported but not failed.

A coordinator can declare `layer_policy` in the step or campaign declaration:

```json
{"signal_layers": ["F.Cu", "B.Cu"], "layer_costs": [1.0, 3.0], "pour_area_loss_pct": 0.5,
 "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}}
```

The `0.5` above is an example, not a pod value. For how to derive a bound from
the placed routing input and a declared signal-via budget, see the
[pod routing policy determination](pod-routing-policy-determination.md)
(`agent_ee.pour_budget`).

`signal_layers` and `pour_area_loss_pct` (0 to 100) are required. `layer_costs`
(one multiplier from 1 to 1000 per signal layer) and `reference` are optional.
The attempt receives it as one canonical JSON argument, `--layer-policy`. The
policy requires KRT. It cannot be combined with `diff_pairs`, because the pin
refuses `--backend-arg` with `--diff-pairs`. Only flags the pin defines are used:

| Policy field | Router argument |
| --- | --- |
| `signal_layers` | `--backend-arg=--layers --backend-arg=F.Cu ...` (KRT `--layers`) |
| `layer_costs` | `--backend-arg=--layer-costs --backend-arg=1.0 ...` (KRT `--layer-costs`) |
| `pour_area_loss_pct` | `--pour-area-loss PCT` (kicad-route pour_integrity `area_loss`) |

KRT appends every board copper layer not listed at its FORBIDDEN cost. That layer
stays an obstacle and through-vias may span it, but KRT places no track copper on
it. The expected `result.json` settings change accordingly: `backend_args` is the
KRT list above, and `pour_area_loss_pct` is the bound as a float. Any other value
is a contradiction.

The attempt grades the policy after routing, and `verify` grades it again from the
retained bytes. The grade is `semantics.layer_policy`, and `verify` returns it as
`layer_policy`:

| Item | Source | Defect | Unknown |
| --- | --- | --- | --- |
| `disallowed_tracks` | the router's `validate/candidate-facts.json`, bound to the validated board hash, and `facts/reference-facts.json`, bound to the input board hash in `snapshot.json` | a track or arc on a layer outside `signal_layers` that no input track matches, listed by UUID, layer and net; matched input tracks are listed as `preexisting`. Every listed track is a defect when the reference facts are not of the input board | facts missing or malformed, candidate facts of another board, or no input board hash in the snapshot |
| `pour_integrity` | `validate/preservation.json` | the pin's check is FAIL | the check is missing |
| `area_loss` | `validate/pour-integrity.json` | a net/layer lost more than the bound, listed under `exceeded`; a declared reference plane whose delta is null because it had no initial fill | no deltas, no pour, a bound other than the declared one, a null delta (no initial fill, listed under `unmeasured`), or a declared reference net/layer the router did not measure (listed under `absent`) |
| `reference_continuity` (when declared) | `attempt/agent-ee-reference-continuity.json`, measured by KiCad's Python on the validated candidate | an RF track whose footprint is not covered by the reference net's fill on a reference layer (`interrupted`, with the uncovered mm²), or an RF via through a reference layer (`vias`) | a missing net, layer, zone or fill, custom clearance rules, an RF track on its own reference layer, no RF track, a measurement of another board or another specification, or a board the router rejected |

A track on a disallowed layer is pre-existing only when the input board's facts,
whose `sha256` must equal the snapshot's hash of the input board, hold a track of
the same kind, layer, net, width and endpoints (and arc mid point). Matching is
one to one, so a second track of the same geometry is new. UUIDs are not
compared, because the router may rewrite them; a moved or widened input track is
new copper. Without a stage receipt, `verify` can only check that the retained
reference facts name the input board's hash: the facts themselves are sealed by
the receipt's `routing/attempt/facts` directory hash.

`reference_continuity` is a 2D fill-coverage check of the declared plane under
each RF track footprint. It is not an impedance or return-path qualification: it
does not compute characteristic impedance, look at the stackup, or check stitching
vias near a layer change. The record and the grade carry this label as `scope`.

The measurement subtracts each reference layer's filled polygons from every RF
track's copper shape. Only the clearance the zone filler cut for the RF net's own
pads is excused, and it is rebuilt as the filler builds it: the pad shape on the
layer, grown by the larger of the zone's and the pad's clearance and the board
minimum, plus KiCad's 0.0005 mm extra zone clearance, at the board's own arc
error. A pad hole is excused only to the smaller of that clearance and the board
hole clearance. With several reference zones on a layer, the smallest zone
clearance is used. Any area left uncovered above 0.000001 mm² is an interruption;
a clean pad launch leaves about 2e-8 mm². A board rules file with clearance
constraints can change that cut, which pcbnew's Python cannot evaluate, so it
makes continuity unknown. An RF via that passes through a reference layer is an
interruption by its antipad, whichever layers its tracks use, so an RF route that
changes side between F.Cu and B.Cu through In1/In2 is a defect. The measurement
runs in the same sandbox, after the candidate binds and only for an eligible exit.
Its record and log are retained beside the router's reports.

Changing this measurement or the grading changes `src/agent_ee/kicad_route_attempt.py`.
A campaign that froze the adapter's hash (`policy_files['kicad_route_attempt.py']`
and `state.json.routing_binding.adapter_sha256`) refuses the new adapter until the
binding is recaptured; adopting a layer policy in the pod campaign is the pod
lane's step.

The overall grade is `defect` if any item is a defect, otherwise `unknown` if any
item is unknown, otherwise `pass`. An exit 0 or 2 whose grade is not `pass`
becomes `policy_failed` (adapter exit 5). Its board is hashed as
`rejected_candidate_sha256` and is never delivered, so the receipt is not adoptable.
When the pin's own pour_integrity fails on the bound, the result is exit 3,
`validation_failed`, and the grade records the defect. Classes that never graded
a board carry an `unknown` grade. `verify` refuses a grade that differs from its
own re-derivation, a grade without a declared policy, a delivered board whose grade
is not `pass`, and a policy-failed board other than the validated one.

Undeclared attempts are unchanged. Their argv, settings, report and verify summary
have no policy fields.

### Per-net signal layers (AGENTEE-258)

The optional `layer_policy.per_net_signal_layers` maps exact board net names to
nonempty allowed copper-layer lists. Each list must be a subset of the global
`signal_layers`. This illustrates the declaration shape for the pod's RF net:

```json
{"signal_layers": ["F.Cu", "B.Cu"], "pour_area_loss_pct": 0.5,
 "per_net_signal_layers": {"/POD_GNSS_RF": ["F.Cu"]},
 "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}}
```

The 0.5 % bound here is an example, not a source-backed pod limit. AGENTEE-257
must determine its basis before campaign adoption; this feature supplies no new
area-loss threshold or RF qualification.

This declaration adds `items.per_net_signal_layers` to the adapter grade. It
reads the pinned native `validate/candidate-facts.json`, bound to the validated
board hash, and checks every segment, arc and via of each listed net. All copper
layers between a via's endpoints in the native copper stack count, including
intermediate planes. A through-via therefore violates an F.Cu-only declaration.
Input copper has no exemption. Violations list UUID, net, kind, full layers and
disallowed layers, and withhold the candidate with `policy_failed`.

Missing or malformed native geometry, an absent declared layer, missing native
facts, a mismatched board hash, or a net with no measured routed copper is unknown.
A measured violation remains a defect alongside unknown geometry. The existing
`reference` declaration independently measures full-track fill coverage for its
`rf_nets`, including plane interruptions and via antipads described above. Declare
both fields to enforce signal layers and the reference plane; neither establishes
impedance qualification.

Every per-net item records `board_sha256`, the router-validated board hash it
grades, and every declared item also records `declared`, the per-net map it
checked. Once the inventory is readable it also lists `copper_measured` per net,
`violations` and `problems`. For a delivered board `board_sha256`
equals the summary's `candidate_sha256` and the native facts' `sha256`; for a
withheld board it equals `rejected_candidate_sha256`. A consumer such as the RF
assessment (AGENTEE-259) binds the item to a candidate only by comparing that
hash with the candidate it assesses. `routing_adapter.verify` regrades the item
from the retained facts, so an edited item fails replay. A board whose copper
layers do not use the canonical `F.Cu`, `In<n>.Cu`, `B.Cu` names is unknown.

The [RF review](rf-review.md#per-net-rf-layer-requirements) retains these native facts with
a verified receipt. It grades the captured requirement's own layers with this function.
Satisfaction also needs passing reference continuity over every routed track.

`per_net_layer_grade(policy, facts, validated_board_sha256)` returns unknown when
the per-net declaration is absent. A global allowlist is never evidence for an
undeclared per-net requirement. To preserve existing global-only verdicts, the
adapter adds this item to its overall policy grade only when declared; consumers
must treat a missing item as unknown for any per-net requirement. There is no new
scope check kind, and the adapter grade alone satisfies no RF requirement. AGENTEE-257 owns the
pod's policy determination and campaign adoption; this mechanism does not choose
those values. Frozen adapter bindings must be recaptured after this change.

[`test_per_net_layers.py`](../tests/test_per_net_layers.py) exercises native
routing of original boards: a compliant net under the pod-shaped declaration
(F.Cu only over an In1.Cu GND reference, B.Cu globally allowed), a back-layer
segment permitted globally but forbidden for the net (the global items all pass),
a via to another layer, and a reference void under part of the route. Each checks
the item's `board_sha256` against the delivered or rejected board. Contract probes
also cover arcs, blind via spans, absent declarations, malformed inventory and
changed replay grades.

The native test [`test_route_layer_policy.py`](../tests/test_route_layer_policy.py)
uses an original four-layer probe with GND planes on In1 and In2. An F.Cu/B.Cu
allowlist delivers a board with no inner tracks, and its grade passes, including
continuity under the routed net. A control that allows the inner layers at cost 1,
with the outer layers at 1000, routes on the planes. The 0.01 % bound then gives
exit 3 with an `area_loss` defect. A route across an owned In1/In2 fill keepout is
`policy_failed`, with `reference_continuity` naming the interrupted track UUIDs.
An RF route from an F.Cu pad to a B.Cu pad changes side through a via, which is a
`reference_continuity` defect on both planes. A finished In1.Cu connection in the
input is `preexisting` and passes; its reference facts with one endpoint moved by
1 µm make it a defect. A policy that declares In2.Cu as a reference plane the
board does not fill is `policy_failed`, with `area_loss` unknown. On original
measurement boards, a plane void 45 µm beyond the clearance cut for an RF pad
(inside the 0.05 mm ring the first version excused) is interrupted
(0.011217 mm² under the launch), and a clean launch leaves nothing uncovered.

### Derived at the pre-routing boundary (AGENTEE-260)

A numeric `pour_area_loss_pct` cannot be stated when a campaign is configured: the
[AGENTEE-257 recipe](pod-routing-policy-determination.md#recipe) derives it from the
placed, planed, unrouted routing input, which exists only after a routing turn's
`add-planes` receipt is adopted. A campaign therefore opts in with the
`agent-ee.campaign/1` field `layer_policy_derivation` instead of a literal
`routing_adapter.layer_policy`:

```json
{"schema": "agent-ee.layer-policy-derivation/1", "directory": "layer-policy",
 "policy": {"signal_layers": ["F.Cu", "B.Cu"],
            "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}},
 "via_budget": {"schema": "agent-ee.via-budget/2", "via_budget": 5, "board_sha256": "<routing input sha256>",
                "basis": {"method": "routed_demand", "pilot": "diagnostic_not_adoptable", "signal_vias": [3, 4, 4]},
                "pilot": {"path": "/abs/PILOT/via-budget-basis.json", "sha256": "<basis sha256>"}}}
```

Start refuses the opt-in when:

- `policy` states `pour_area_loss_pct`;
- the routing adapter declaration also has a `layer_policy`. A fixed limit never
  stands in for the derived bound;
- the sealed scope declares `layer_policy_provenance`;
- a captured input lies under `directory`, or `directory` equals `rating_provenance`;
- `via_budget` is present but malformed.

`via_budget` is optional; without it the policy stays unknown. Its basis is the
[via-budget method](pod-routing-policy-determination.md#via-budget-n): at least
three diagnostic, non-adoptable pilot attempts on the routing input `board_sha256`.
Start recomputes `N = V_max + max(1, ceil(0.1 V_max))` from the recorded
`signal_vias`. It refuses a different `N`, and attempts that differ by more than
the margin. The pilot attempts are not part of the campaign and are never adopted.
Only an `agent-ee.via-budget/2` basis can derive a bound. The
[coordinator via-budget pilot](#via-budget-pilot-agentee-265) produces it, and start
re-derives its counts from retained router facts. The legacy counts-only
`agent-ee.via-budget/1` basis (the same fields without `pilot`) carries counts as
stated by the pod lane. It still passes the shape and `N` checks above, but its
counts cannot be re-derived, so every routing turn leaves the policy unknown
(AGENTEE-265 tightening; it never derived a bound from evidence).

On every routing turn, after the plane producer is adopted and before the router
starts, the coordinator (never the writer):

1. runs `pour_budget.measure` on the delivered design into
   `implementations/N/layer-policy/measure/`;
2. derives the bound with `pour_budget.derive` and the frozen `N`;
3. writes `implementations/N/layer-policy/record.json`
   (`agent-ee.layer-policy-record/1`) and anchors its sha256 in campaign state as
   `layer_policy_records[N]`. The record holds the routing input hash, the
   measurement hash, `N` with its basis, the derivation and the composed
   `layer_policy`;
4. runs the frozen routing binding with only `--layer-policy` added.

The policy is `unknown` and the router does not run when:

- the measurement fails or is missing;
- the measured board is not the adopted routing input;
- no `via_budget` is declared;
- the `via_budget` is a counts-only `agent-ee.via-budget/1` basis, even for this
  routing input;
- the basis `board_sha256` is another board (stale);
- `derive` refuses, for example for an unplaced board or an unknown probe.

The record is retained and verifies. The campaign stops `incomplete` with
`layer policy unknown at the pre-routing boundary`. This is never a pass.

The basis names one routing input. A later routing turn on another input is
therefore unknown, for example the plane producer's output over a partially routed
proposal. One budget is never spent again on top of an earlier route. That turn
needs a new pilot and a new campaign configuration.

After the routed proposal is adopted, the record and the measurement are copied into
`<directory>/record.json` and `<directory>/pour-budget.json` of the next candidate.
The new `proposal.scope.json` declares them as top-level `layer_policy_provenance`
(`record`, `record_sha256`, `measurement`, `measurement_sha256`). The sealed scope
and earlier proposals are never edited. Later candidates inherit the declaration
and bytes until another routing turn derives one. This is the
[rating-provenance lineage](stage-receipts.md#coordinator-rating-provenance) pattern.

`verify-campaign` re-derives every record from its retained measurement and the
frozen configuration. It refuses:

- a record whose bytes differ from its anchor;
- a measurement whose bytes differ from the record;
- a record whose routing input is not the adopted output of the producers before
  the router (stale);
- a derivation or policy that differs from the recomputation;
- an `N` other than the configured one;
- a routing receipt whose input board or exact command is not the derived one;
- a routing receipt without a derived record;
- a later bound that exceeds an earlier bound on the same routing input. A failed
  attempt's limit is never relaxed after the fact;
- a candidate declaration that is not owed by its lineage.

Stage done, the receipt-chain replay and the RF reviewer's routing context compare
the routing step with its `--layer-policy` removed against the frozen binding. They
never compare it with a policy from the writer. The derived bound remains a
geometric routing guard; it is not RF, impedance or return-path qualification.
[`tests/test_layer_policy_lineage.py`](../tests/test_layer_policy_lineage.py) runs
the real plane producer, receipt executor, via-budget pilot and native measurement
with the kicad-route fixture router. Its derived bounds come from `/2` pilot bases,
and it shows that a `/1` basis of the routing input stays unknown.

### Via-budget pilot (AGENTEE-265)

`python3 -m agent_ee.via_budget_pilot run --design DIR --declaration ROUTING.json
--policy POLICY.json --out PILOT [--attempts K]` is the coordinator's pilot step for
the [via-budget method](pod-routing-policy-determination.md#via-budget-n). `DIR` is
the planed, unrouted routing input as the routing step receives it (the candidate tree
after the adopted `add-planes` output). `ROUTING.json` is the campaign's
`agent-ee.routing-adapter/1` declaration without a `layer_policy`. `POLICY.json` is the
`layer_policy_derivation` `policy`.

The pilot runs `K` attempts (3 to 20, default 3). Each one is an ordinary coordinator
stage step: the pinned adapter in the networkless sandbox on a copy of `DIR`. Attempt
`i` is the campaign routing declaration with only two changes. Its label is
`<label>-pilot-<i>`. Its layer policy keeps `signal_layers` and `layer_costs`, drops
`reference`, and uses the permissive `pour_area_loss_pct` 100. The receipt binding is
`{"via_budget_pilot": i, "adoptable": false}`. No receipt is adopted and the routing
input is never written. A pilot receipt is also refused as a `pre_receipt`. The
pilot is diagnostic. It has the status `not_accepted` and plays no part in final
routing.

For each attempt the coordinator verifies the receipt and the adapter's classification
from the retained bytes (`verify_campaign_receipt`). It takes
`validate/candidate-facts.json` and `facts/reference-facts.json` at the hashes the
adapter report retained, and counts `V_i` with the AGENTEE-257 `signal_vias` rule:
through-vias of nets other than `/GND` that no via of the same net, position, diameter
and drill in the reference facts accounts for. It writes
`PILOT/via-budget-basis.json` (`agent-ee.via-budget-pilot/1`). The basis holds every
attempt's receipt hash, classification, routing input hash, tool identity, facts paths
and hashes, and `V_i`. It also holds `N = V_max + max(1, ceil(0.1 V_max))`. `N` is
unknown (status `unknown`, exit 1) when:

- an attempt is not `complete`;
- the receipt or facts do not verify;
- the facts have no via list;
- an attempt routed another input;
- the attempts differ by more than the margin.

On `derived` the command prints the campaign `via_budget`:

```json
{"schema": "agent-ee.via-budget/2", "via_budget": 5, "board_sha256": "<routing input sha256>",
 "basis": {"method": "routed_demand", "pilot": "diagnostic_not_adoptable", "signal_vias": [3, 4, 4]},
 "pilot": {"path": "/abs/PILOT/via-budget-basis.json", "sha256": "<basis sha256>"}}
```

`verify --basis PATH` re-derives a basis from its retained attempts. A relative
`pilot.path` resolves against the campaign configuration's directory.

Campaign start re-derives the basis from the retained receipts and facts. It refuses
the campaign before it creates the campaign root when:

- the basis bytes differ from `pilot.sha256`;
- a facts file, adapter report or receipt was edited;
- the re-derived basis differs from the file, including a rewritten basis whose new
  hash is declared;
- the re-derived `V_i`, `N` or routing input differs from the stated `via_budget`;
- the pilot forked another routing declaration;
- its policy is not the pilot form of the campaign `policy`;
- its attempts ran another router identity than the campaign binds.

It then copies the basis and attempts into `<campaign>/via-budget-pilot/` and points the
frozen `config.json` at that copy. `verify-campaign` re-derives the copy against the
campaign routing binding's tool identity. A retained pilot without a `/2` declaration
is refused. The sandbox command each receipt recorded still names the directory where
the attempt ran (`location`), and the copy is verified against it.
[`tests/test_via_budget_pilot.py`](../tests/test_via_budget_pilot.py) runs the pilot
on the kicad-route fixture router with stated via facts, then a full derived-policy
campaign on the original probe board.

## Router targets from the scaffold producer

The [scaffold producer](scaffold.md#default-class-clearance) writes only fab
minimums as DRC floors (owner decision 2026-10-18). It records preferred limits as
router targets in its receipt's `derivation.router_targets`. The preferred Default
track width and via size are applied as the Default class routing defaults in the
project file the router receives. agent-ee has not established how the pinned
router uses class widths; its result is graded only by agent-ee's own checks. The preferred
clearance is not applied, because a class clearance is a DRC floor. The pinned
adapter does not pass it on either: `default_clearance_mm` and
`clearance_margin_mm` are fixed to `null` in the expected settings. A router that
consumes the clearance target needs a new adapter protocol and its own pinned
validation.

## Before the router runs

The attempt is `not_run` (exit 64), and the router does not start, when:

- `/work/routing` already exists, since every attempt needs a fresh private OUT;
- the candidate lacks the board or schematic, or contains a link;
- the checkout is not the pin or its code differs;
- an installed backend input is missing. The report names the exact path, for
  example `KICAD_ROUTE_STATE/.venv-krt/bin/python`, or, when the state manifest
  selects a router generation, `KICAD_ROUTE_STATE/.router-generations/<hex>/venv/bin/python`
  (also its `installation.json`, `router/VERSION` and `router/UPSTREAM.json`);
- the state manifest selects a router generation outside
  `.router-generations/<32 hex>`;
- a write probe succeeds. The script tries to create a file in the state
  directory, in each backend component (`.venv-krt`, or the selected
  `.router-generations/<hex>` directory, for the owned engine;
  `vendor/freerouting` for Freerouting), and in the checkout when it is separate.
  Every probe must be
  refused by the OS. A probe file that was created is removed and reported.

Tree identities of the tool code and the state components are recorded before
and after the attempt. Any difference makes the attempt a contradiction.

## Finding the attempt

The pin writes `OUT/runs/<name>/<label>/<UTC stamp>/` with a mutable `latest`
link beside it. It has no top-level result. The adapter looks only inside its
fresh OUT and requires exactly one real stamp directory. It never follows
`latest` or a path named in a report. Any other entry under `runs/`, a second
attempt, or a `latest` that does not name the attempt is a contradiction.
Report paths are untrusted strings, resolved against the router's known
working directory, and must land on the expected contained location.

## Exit semantics

The numeric exit is never trusted alone. Each exit needs its exact status
combination in a `result.json` and `attempt.json` of the pinned shape, plus the
fixed settings (`protect: existing`, empty backend environment, all nets, and
backend args and pour bound exactly as the declared layer policy implies).

| Router exit | Required observation | Classification | Proposal | Adapter exit |
| --- | --- | --- | --- | --- |
| 0 | process PASS, validation PASS, routing COMPLETE, contained candidate | `complete` | `eligible` | 0 |
| 2 | process PASS, validation PASS, routing INCOMPLETE, contained candidate | `incomplete` | `eligible_partial`; coverage `routing_incomplete` | 2 |
| 1 | process FAIL with no candidate, or no `result.json` (early failure) | `backend_failure` | none; coverage `missing_routing` | 1 |
| 3 | process PASS, validation FAIL or NOT_RUN | `validation_failed` | none; the board is hashed as `rejected_candidate_sha256` and never copied | 3 |
| 4 | no attempt directory, a `kicad-route/refusal/1` machine record, no `result.json` | `refused` | none; coverage `missing_routing` | 4 |
| 0 or 2 | as `complete`/`incomplete`, but the declared layer policy grade is `defect` or `unknown` | `policy_failed` | none; the board is hashed as `rejected_candidate_sha256` and never copied | 5 |
| anything else | for example exit 2 without a result (usage error), exit 0 with INCOMPLETE, unknown fields or statuses, an escaping or linked candidate | `contradiction` | none; coverage `unknown` | 65 |
| — | the adapter's timeout or a cancellation (SIGTERM) | `timeout`, `cancelled` | none; the router's process group is killed | 75, 143 |
| — | an unexpected fault in the adapter itself | no report | none; `verify()` refuses the attempt | 70 |

The pin's own `overall_exit` returns 2 for any routing status other than
COMPLETE after validation passes. The adapter accepts only INCOMPLETE. An
AGENTEE-95 receipt adopts only exits 0 and 2. Every other exit is
non-adoptable, so the pre-step design stays the iteration's diagnostic input
and the stage stops incomplete. No routed board is substituted.

For 0 and 2 the candidate must also bind to the attempt and its inputs:

- `run_dir` and both candidate paths resolve to the discovered attempt;
- the board is reached without links;
- `candidate/` and `reference/` each hold the real project directory and
  nothing else except the links the pin places beside it so that
  `${KIPRJMOD}/../<dir>` library paths resolve: each named for a top-level
  input directory other than the project (never a top-level input file),
  with the literal target `/work/routing/agent-ee-original/<same name>` (the
  staged input root in the sandbox), and the two link sets equal, as `copy_project` links beside the
  candidate every sibling the snapshot linked. Any other entry, a link whose
  name or target differs (relative, elsewhere, with a trailing slash or
  `..`), a link named for a top-level input file, a real file or directory
  with a sibling name, a link in place of the project directory, or link
  sets that differ is a contradiction. The links are read, never followed;
  only the project directory is hashed and delivered;
- the candidate and reference project trees contain only regular files;
- every reference snapshot file equals the captured input byte for byte, and
  `snapshot.json` and the input fingerprint agree with them;
- the candidate changes no project file other than the board, and the router
  restored none;
- the board hash equals both `candidate_board_sha256` and
  `validated_board_sha256`, the board kicad-route graded after its refill;
- `delivered_board_sha256` is a SHA-256 digest of the pre-refill board, and
  `refill_modified_board` agrees with whether that digest differs.

`kicad-cli pcb drc --save-board` writes `<board>.kicad_prl` beside the board.
It is the one extra candidate file allowed. It is hashed as editor state and is
never delivered.

## Retention and the report

All outcomes write `routing/adapter-report.json`
(`agent-ee.kicad-route-attempt/1`), including timeouts, cancellations and
refusals. The router's stdout and stderr are kept. The attempt's
`result.json`, `attempt.json`, `RESULT.md`, `snapshot.json`, `validate/`,
`facts/` and `work/` logs are byte copies under `routing/attempt/`. Design files
there (boards, project and rule files, including KRT's project writeback) are
hashed under `omitted_design_files` and not copied. The router's prepared
backend input under `work/input` is observed the same way: its regular design
files are hashed under `omitted_design_files`, and nothing under `work/input`
is copied. A design-file name there that is not a regular file is recorded by
its path type alone, never followed: `link` for a symbolic link (valid,
dangling or looping) and `special` for a directory or special file, so an
absent file (no entry) stays distinct from a linked one (agent-510 R4). Other
links under `work/input` are ignored. The only board that leaves
an attempt is `routing/candidate/P/B` of an eligible exit. Missing reports are
listed under `missing_artifacts`; none is invented. The raw OUT stays in the
step's work directory.

The report records the request, the fixed argv and environment, the process
exit or signal, the discovery, the retained file hashes, the full input
inventory and its staged copy (`inputs.staged`), the code and state identities before and after, the write probes,
the executables (`kicad-route`, Python, `kicad-cli`) and the KiCad template
library tables. For an eligible attempt it also records `candidate_files`, the
hash of every file in the attempt's candidate project directory, because the
candidate tree itself is a design file and is not retained. KiCad library
directories come from the image's `/usr/share/kicad`, as the pin hard-codes.
That is recorded, not qualified as catalogue authority. Every report has the
same fields, whatever the outcome. Its `identity` covers every field.

## Verification

`verify(run, inputs=None, *, receipt=None, layout=None)` rebuilds the outcome
from the retained bytes, the pin and the adapter's constants, and raises
`ValueError` on any disagreement. The report's identity and retained hashes
only show that the bytes agree with each other. They authenticate nothing.

The authority for the bytes is the AGENTEE-95 receipt, anchored by the
campaign. `verify` does not validate the receipt and does not replace it. The
caller runs `stage_receipts.validate_receipt` first, then passes the validated
receipt. The receipt supplies the inputs, and the report must agree with:

- the receipt's exit, which must be the adapter exit;
- its output for the board, which must be the delivered candidate's hash, or
  null when nothing was delivered;
- its hash of `routing/adapter-report.json`;
- its backend identity, when the retained `result.json` reports one;
- its step declaration: the routing stage, this request, the declared write,
  artifacts and mounts, and no version probe.

A report paired with another attempt's receipt is refused.

`layout` overrides the production sandbox paths and pin. Only the fixture
router's tests pass it. Without it, a report that claims any pin other than
the supported pin is refused, so a fixture attempt never verifies as a kicad-route
attempt.

For every outcome, `verify` refuses a report when:

- a field is missing, unknown or malformed, or a retained hash differs;
- the inputs differ from the captured pre-step candidate;
- an attempt that ran has no `inputs.staged` or `inputs.staged_after`, or the
  staged record is not at the adapter's staging path, or its manifest or
  identity is not the inputs', or the post-run observation disagrees with the
  classification, or the retained staged tree (absent, partial, stale or edited)
  is not the sealed post-run observation, or the receipt's staged-file digests
  are not the report's (AGENTEE-442);
- the classification, proposal, coverage and adapter exit do not match;
- the reported router exit differs from the observed process exit;
- the claimed pin, adapter compatibility or `kicad-route` path is not the pin's;
- the tool version is anything but the pinned `unknown`, or the
  reproducibility label is not the backend's;
- the backend identity differs from the retained `result.json` `backend`, or is
  claimed without one;
- the missing-artifact list differs from what is retained;
- a non-eligible attempt has a delivered board, or, other than a
  contradiction, a bound candidate inventory;
- an attempt other than exit 3 has a rejected-candidate hash.

When the router ran, it also refuses:

- a commit or executable hash that is not the pin's code;
- a write probe set that is not every state directory, each refused by the OS;
- an argv, environment, working directory or timeout other than the fixed
  ones;
- a discovered attempt directory other than `out/runs/<name>/<label>/<stamp>`.

Each classification other than a contradiction, timeout or cancellation must be
re-derived from the retained `result.json`, `attempt.json`, the router's
stdout and the observed exit. Malformed retained reports, discovery problems or
changed tool state make it a contradiction.

For exits 0 and 2, `verify` re-checks every candidate binding the retained
evidence supports:

- `delivered_board_sha256` and `validated_board_sha256` both equal the
  delivered board, and `restored_project_files` is empty;
- `run_dir` and the candidate paths in `result.json` resolve to the discovered
  attempt;
- the retained `snapshot.json` names the board and schematic, every snapshot
  file equals the captured input, and its recomputed fingerprint is the
  result's input fingerprint;
- the candidate inventory is exactly the snapshot with only the board changed,
  plus at most the board's editor-state file;
- every expected report is retained.

A contradiction may sit over reports that alone look eligible, because some
contradictions come from the file system. It never delivers a board, so
`verify` does not re-derive why it is a contradiction.

The candidate inventory and the before/after tool identities are the adapter's
own in-sandbox observations. `verify` checks them for consistency but cannot
re-hash trees that are not retained. The receipt's mount identities are the
authority for the adapter, checkout and state bytes.

## Routability experiment evidence (AGENTEE-442)

KICADROUTE-91 M1b adds a versioned extension to `run`: `result.json` may carry
`experiment = {path, sha256, schema: "routability-experiment/1"}`. `path` is
relative to the run directory and is always `routability-experiment.json`, the
root sidecar. The router can also take `--experiment-policy PATH`, a
`routability-policy/1` declaration. The adapter consumes both at its boundary.
It retains the evidence with its identity, or refuses it with a typed reason.
Experiment evidence is never dropped and is never qualified.

- **Declaration.** The step declaration, `step(experiment_policy=...)` and the
  `agent-ee.routing-adapter/1` field `experiment_policy` take the policy inline.
  It must have exactly the five fields that kicad-route's reviewed
  `experiment_policy.load` (kicad-route `a5fa636d`, `docs/experiment-policy.md`)
  accepts, with the same value rules:
  - `schema`: `routability-policy/1`;
  - `experiment_class`: `current-policy` or `historical-all-layer`;
  - `allowed_trace_layers`: unique nonempty layer names, at least one;
  - `allowed_via_spans`: distinct two-layer endpoint pairs, no duplicates. An
    empty list prohibits all vias;
  - `rule_areas_sha256`: 64 lowercase hex digits.

  Duplicate keys, any other key, schema version or class, and the superseded
  two-field `{schema, policy}` draft are refused before the router runs (exit 64
  when they reach the sandbox). The request is bound to the stage receipt as
  usual. The adapter writes the canonical bytes (sorted keys, no spaces) to
  `routing/attempt/agent-ee-experiment-policy.json` and passes that path as
  `--experiment-policy`. A router that changes the file makes the attempt a
  contradiction. agent-ee does not check `rule_areas_sha256` against the board.
  That digest binds the native board and footprint rule areas, and only the
  router's native qualification can establish it.
- **Retention.** Two run-relative root files are copied to `routing/attempt/`
  and hashed in `retained`, whether or not `result.json` names them:
  `routability-experiment.json` and `experiment-policy.json`, the router's
  retained copy of the declaration. A link or special file at either name is a
  link violation (contradiction). Other sidecar names are never retained.
- **Record.** `semantics.experiment` is re-derived from the retained bytes. It is
  present whenever a policy was declared, `result.json` has a reference, or one
  of the root files was retained. Every record carries `policy` (`declared`, the
  `sha256` of the canonical policy file and `policy_sha256`, which is kicad-route's
  `common.sha256_of(declared)`, or `null` when undeclared), `policy_file`,
  `policy_copy` (path and recomputed hash of the retained copy, or `null`),
  `contract` and `qualified: false`.
  - `retained`: `path` (the retained root sidecar), the recomputed `sha256`,
    `schema`, `reference` (the result.json reference, verbatim) and the
    producer's `identity_sha256`, `comparison_eligible` and
    `ineligible_reasons`, copied from the sidecar. Without a declared policy,
    kicad-route always marks its receipt not comparison-eligible, and the
    record keeps that marking.
  - `refused`: `reason` is one of the following:
    - `missing`: no reference despite a declared policy, no regular root
      sidecar was retained, or the sidecar's `artifacts` binds an
      `experiment-policy.json` that was not retained;
    - `malformed`: the reference shape, a hash that is not hex, a `path` other than
      the literal run-relative `routability-experiment.json` (a cwd-relative,
      absolute, `..` or other file name is unsupported), a sidecar that is not a
      JSON object, or one whose top-level or `identity` key set is not exactly
      the producer's (`EXPERIMENT_SIDECAR_KEYS`, `EXPERIMENT_IDENTITY_KEYS`,
      after kicad-route `a0ed434` `schemas/routability-experiment-1.json`,
      which forbids other properties). That schema requires
      `identity.effective.input` (`board_sha256` and `rules` `.kicad_pro`,
      `.kicad_dru`) and `candidate.raw_backend_output_board_sha256`, so the
      interim `4f06368f` receipts, which lack them, are malformed: retired,
      never reinterpreted. Also malformed: `replay_authority` other
      than `false`, a `comparison_eligible` that is not a boolean true exactly
      when `ineligible_reasons` is empty, `artifacts` digests that are not hex,
      and an `identity_sha256` that is not hex. Any other member outside the
      producer schema is malformed too: `EXPERIMENT_SHAPE` restates every
      required member, JSON type, constant, enumeration, sha256 leaf and closed
      object of that schema file, and the stdlib `_shape_error` reports the first
      path that leaves it (for example `policy_qualification` that is not an
      object, or an `outcome` with an unsupported field). An eligible receipt
      whose retained `attempt.json`, `facts/reference-facts.json` or
      `validate/candidate-facts.json` is not a JSON object of the producer's
      shape is malformed (K91 review EI4). Repeated keys are malformed
      too, never resolved to their last value: a repeated `experiment` key in
      `result.json` or a repeated key in its reference, and any repeated key in
      the sidecar. So are numbers the parser cannot represent: NaN, Infinity, a
      float that overflows to infinity, an integer beyond the interpreter's
      4300-digit limit, and nesting deeper than 64 levels, in the sidecar, the
      policy copy or the reference. Each of these is a typed refusal, never an
      escaping exception. The retained `experiment-policy.json`
      is read like `experiment_policy.load` (repeated keys refused, exactly the
      five fields and their value rules). One that fails that is malformed. So
      is one that decodes to the declaration in bytes other than kicad-route's
      `common.write_json` form (`json.dumps(policy, indent=2)` and a newline, in
      the copy's own key order). An integer beyond the digit limit anywhere in
      `result.json` makes it unreadable, so a retained sidecar is `unbound`. A
      `result.json` with repeated keys is also refused by `verify`: the stage
      receipt's strict parser reports its backend as unknown;
    - `unknown_schema`: the reference schema;
    - `tampered`: the recomputed sha256 differs from the reference; the
      sidecar's `identity_sha256` is not kicad-route's `common.sha256_of` of
      its `identity`, so an identity edited after sealing is refused even when
      `result.json` re-binds the edited file; or the retained
      `experiment-policy.json` is not the file the sidecar's `artifacts` digest
      binds;
    - `incompatible_schema`: the sidecar's own `schema`;
    - `incompatible_policy`: the sidecar `identity.policy` (`declared`,
      `declared_sha256`, `experiment_class`) differs from the declared identity,
      including a policy the coordinator never declared, or the retained
      `experiment-policy.json` is a valid policy other than the declaration.
      Identities compare as canonical JSON bytes, not decoded values;
    - `unbound`: a retained root file that no reference binds, because
      `result.json` is absent or unreadable or has no `experiment`;
    - `ineligible`: a policy was declared, and the producer marked the
      receipt not `comparison_eligible` (for example a routing-environment
      binding mismatch).
      The record keeps `identity_sha256`, `comparison_eligible` and
      `ineligible_reasons`, and the detail quotes the reasons;
    - `unqualified`: the sidecar claims `comparison_eligible`, but this
      boundary cannot requalify the claim from the retained bytes. The flag
      and the stored `bound` status are claims, never authority. `_requalify`
      re-derives the obligations, reading only bytes the sidecar names:
      - every digest in the sidecar's `artifacts` is bound to the retained file
        it names, recomputed from the retained bytes. The sidecar must name
        `attempt.json`, `facts/reference-facts.json`,
        `validate/candidate-facts.json`, `work/effective-configuration.json`,
        `work/effective-configuration-request.json` and the raw backend output
        `work/effective-configuration-output.kicad_pcb` (`EXPERIMENT_BOUND`);
        an unnamed or null one is `unbound`. A named file that was not
        retained is `missing`, other bytes are `tampered`, and a name that is
        not a run-relative retained report is `malformed`. The raw output is a
        design file, which is never retained: its digest must be the one the
        adapter itself recorded for the run in `omitted_design_files` (the
        receipt anchors the report), else `missing` or `tampered`. Any other
        named design file is `malformed`. The adapter's own retained files are
        never the identity source;
      - the manifest must cover exactly one outer batch and no later subrun
        (`execution` `{outer_batches: 1, later_subruns: 0}`), have coverage
        `resolved_outer_batch_only`, a `complete` capture without `issues`,
        and a `request_sha256` that is the retained request's digest. Its
        `source_sha256` and `producer_sha256` must equal the request's
        (K91 review EI2);
      - the sidecar's `identity.effective` and `identity.build` must restate
        the manifest, and `result.json` `process.effective_configuration`
        must bind it (status `bound`, both digests, coverage and capture);
      - the origin must be a single run: depth 0, no predecessors (their
        receipts are not retained here, so a chain is unsupported), a root
        fingerprint equal to the sidecar's input fingerprint, and an
        `attempt.json` `source_dir` that is not inside this run. That case is a
        self-cycle that kicad-route truncates to depth 0 (K91 review EI3).
        `source_dir` is a string in the router's namespace. It is normalised
        lexically (`.`, `..`, repeated and trailing separators), never
        followed on the host, and compared with this run's directory
        (`experiment_run`: the `--out` run the adapter discovered). So
        `RUN/hop/../candidate/project`, `RUN/candidate/project/` or a link
        inside the run are self-cycles too (review H2, ee442f).

      The record keeps `identity_sha256`, `comparison_eligible` and
      `ineligible_reasons` as observed. A wrong manifest or request schema is
      `incompatible_schema`;
    - `run_mismatch`: the eligible sidecar, with every digest correct, does
      not describe this execution. These must agree:
      - `source_dir` and the sidecar's `identity.input.source` must be exactly
        the `--dir` the adapter passed, the staged original
        `/work/routing/agent-ee-original/<project>`. The read-only
        `/candidate/<project>` mount is not that input;
      - the retained `snapshot.json` must have a non-empty `files` map of
        sha256 digests (otherwise `missing` or `malformed`, never a crash:
        review H2, ee442g);
      - the input fingerprint must equal `attempt.json`, `result.json` and the
        fingerprint of the retained `snapshot.json` files, and the snapshot
        files, input board name and digest must match those files;
      - the ORIGINAL input and its named files (review H1, ee442g; agent-510
        R1): `attempt.json` `board` must be a plain file name, so an empty or
        dot name (`.`, `..`), a separator (`/`, `sub/b`) or a trailing separator
        is refused before any name is derived from it (review H1, ee442j); the
        snapshot must hold the board's own `<stem>.kicad_pro` (kicad-route
        refuses an input without it), and `identity.input.project` must be that
        file with its snapshot digest. The rules inventory is derived from the
        snapshot as kicad-route `a0ed434` `cli.rules_files` derives it: the custom
        rules file is `<stem>.kicad_dru` when the snapshot has it, else null, and
        `not_loaded` is every other `.kicad_pro`/`.kicad_dru` beside the board,
        sorted. `attempt.json` `rules_files` (`project`, `custom_rules`,
        `not_loaded`) and `identity.input.rules` (name, snapshot digest,
        `not_loaded`) must both equal that one derived inventory, so a loaded
        rules file declared absent, or an invented or omitted not-loaded file, is
        refused even when the attempt and the sidecar agree;
      - the PREPARED input (agent-510 R2): protection and preprocessing change
        the backend input, so it is not the snapshot. The adapter observes it in
        `work/input/<reference_dir name>/<board>` and the `.kicad_pro` and
        `.kicad_dru` beside it (kicad-route `experiment._retained_backend_input`)
        by the digests `retain()` recorded in `omitted_design_files`.
        `identity.effective.input` and the `input` of both sealed manifests must
        equal it, and a run without a regular prepared board and project there
        is refused. The optional `.kicad_dru` is null only when it is absent: a
        link (valid or dangling) or a directory or special file there is
        refused without being followed, as kicad-route's input guard rejects it
        (agent-510 R4). The original and prepared boards may differ: a
        `protect existing` run locks copper in the prepared input;
      - the request settings must restate `attempt.json` `settings`, which the
        adapter checks against its fixed request: `protect`, `mode`, the
        backend, the budgets, `experiment_policy_sha256` and the other
        `EXPERIMENT_REQUEST_SETTINGS`;
      - three distinct board seals, each against its own stage record
        (kicad-route `a0ed434`): the raw backend output
        (`raw_backend_output_board_sha256`) must equal the sealed raw-output
        artifact, the manifest `output.board_sha256` and the `result.json`
        binding's `backend_output_sha256`, at stage
        `backend_output_before_wrapper_lock_restore_and_native_refill` in both
        records; the wrapper output (`backend_output_board_sha256`,
        `pre_refill_board_sha256`) must equal the binding's
        `wrapper_output_board_sha256` (stage
        `after_wrapper_postprocessing_before_native_refill`) and the
        `delivered_board_sha256` `result.json` validated; the graded refilled
        board (`candidate_board_sha256`, `validated_board_sha256`,
        `file_sha256`) must equal the board `result.json` validated and
        `validate/candidate-facts.json`. The binding's `captured_input_sha256`
        must be the observed prepared input board, never the original;
      - the outcome statuses must equal `result.json`'s;
      - the manifest's `requested_output` must be `RUN/work/krt-out/<board>`.

      A same-policy receipt of another run, transplanted with every file it
      seals, and a real sidecar resealed over this run's digests are both
      refused here.

    The record keeps the reference and, when a root sidecar was retained, its
    path and recomputed hash. For `unbound`, that is the unbound file.
  - `absent`: the router wrote neither `result.json` nor a root file, for
    example a refusal or a not-run attempt.
- **Run record.** A declared policy is also part of the attempt the adapter
  requested. kicad-route `a0ed434` onward writes `settings.experiment_policy_sha256`
  (`common.sha256_of` of the loaded declaration) into `result.json` and
  `attempt.json`, and the declaration itself as `attempt.json` `experiment_policy`.
  The fixed settings include that digest exactly when a policy was declared, and
  `attempt.json` must carry the declaration exactly then, equal as canonical
  JSON. A run that omits either, or records another declaration, is a
  contradiction (`result settings differ from the fixed adapter settings`,
  `attempt.json missing or shaped differently from the pinned tool` or
  `attempt.json experiment_policy differs from the declared policy`). The
  synthetic fixture router omitted both until the native bd1e633 run below
  showed it.
- **Classification.** A refusal is a report violation. A complete or incomplete
  attempt becomes a `contradiction` (exit 65) and delivers no board, and so
  does a `validation_failed` attempt: a contradiction rejects no candidate, so
  `rejected_candidate_sha256` is cleared and `verify` still returns the typed
  record. A timeout or cancellation keeps its class with the refusal in
  `reasons`. Without a declared policy, a reference or a root file, reports,
  argv and `verify` summaries are unchanged. `verify` returns the record as `experiment`, refuses a record
  that differs from the retained bytes, and refuses a refused record on an
  attempt that is not a contradiction, timeout or cancellation.
- **Real producer replay.** The sidecar shape follows agent-507's final frozen
  KICADROUTE-91 producer, kicad-route `a0ed434`
  (`a0ed43494d44fd8312054b204083881fe76f3a81`), and its clean native receipt
  (`tool_identity` `a0ed434`, `dirty: false`, native PASS/COMPLETE, scope
  `/SIG_A`, eligible). The receipt's sidecar, policy copy and sealed manifests
  are committed byte for byte in `tests/fixtures/k91-real` (origin T17; see its
  `PROVENANCE.md`). Its boards and facts hold route_demo geometry and are not
  committed. `tests/test_routing_experiment_boundary.py` has these real replays:
  - `FinalNativeReceiptTest` grades the receipt with its own run, source,
    input and candidate. `retain()` copies the run's diagnostics as the
    adapter does, and `experiment_grade()` grades the retained bytes against
    the receipt's execution identity (its run directory, the producer's
    `--dir`, and the design-file digests `retain()` observed). The record is
    `retained`, `qualified: false`, with the receipt's file and identity
    digests, and it is re-derived unchanged after the run is deleted. The same
    path refuses the same bytes graded as another run or another input
    directory (including one that exists on the verifier host), with another
    observed raw output, and, resealed so that every digest verifies, with
    another run's facts, input or seals, or with aliased, linked or looping
    self-origins. The receipt is external: `AGENT_EE_K91_FINAL_RECEIPT` names a
    byte copy (default `build/k91-final-clean-receipt` in the checkout, then
    the original). The test pins its key hashes and skips, saying so, only
    when it is absent. Resealed with the rules declared absent, or with an
    invented not-loaded file, in both the attempt record and the sidecar, it is
    refused (agent-510 R1), and so is a prepared-input observation that is not
    the one the manifests captured. Private copies whose prepared
    `route_demo.kicad_dru` is removed, or replaced by a link to the known rules
    file or by a dangling link, resealed to claim it null, are adversarial
    copies, not native executions: the absent one is `retained` and both links
    are refused (agent-510 R4). The synthetic adapter router exercises the same
    through `step`, `verify` and `adopt`: absent and regular prepared rules are
    adopted, linked, dangling, self-looping and directory ones refused;
  - `ProtectedExistingReceiptTest` and `ProtectedHumanFallbackReceiptTest`
    grade agent-507's protected native `a0ed434` receipts (`--protect
    existing`, and `--protect human` falling back to existing protection) the
    same way. Their original snapshot board and their prepared `work/input`
    board differ, and both are `retained`. The stage conflation, resealed so
    that both manifests, the identity and the binding claim the original
    input, is refused. The receipts are external (`AGENT_EE_K91_PROTECTED_EXISTING`
    and `AGENT_EE_K91_PROTECTED_HUMAN`, default
    `build/k91-protected-existing` and `build/k91-protected-human`). The human
    run makes no selective-human claim;
  - the same receipt delivered by a router the adapter ran, with every file it
    seals, goes through `step`, `stage_receipts.run_step`, `verify` and `adopt`
    and is refused `run_mismatch` (its `source_dir` is the producer's, not
    the staged `/work/routing/agent-ee-original/<project>`), with no candidate and a rejected adoption, also
    after `OUT` is removed. `RealReceiptReplayTest` replays the committed
    sidecar and manifests the same way: refused at the binding (`tampered`,
    `attempt.json`), as are the K91 review's resealed real variants (EI2, EI3,
    EI4). The same cases sealed coherently on the synthetic receipt are refused
    by their own checks. A real sidecar resealed over this run's digests is
    `run_mismatch`; edited, re-bound, re-ordered, missing and out-of-range
    variants are refused as `tampered`, `missing`, `incompatible_policy` or
    `malformed`; under the other declared policy it is `incompatible_policy`;
    marked not eligible it is `ineligible`;
  - the interim `4f06368f` sidecars are retired: refused `malformed`.

  The adapter cannot itself run this receipt: its pinned router writes no
  experiment evidence, and it issues only `--protect existing`, mode `all` and
  `--dir /work/routing/agent-ee-original/<project>`, while the receipt's request is `locked`,
  `selected` and the producer's fixture directory. An adapter-run real positive
  needs the re-pin. **Integration activation is gated:** the native positives
  above go through `retain()` and `experiment_grade()`, the consumer's own
  helpers, not through `step`, `run_step`, `verify` and `adopt`, where the real
  receipt is still refused. They are not the matching adapter-run positive. That
  positive needs a qualification under the actual adapter request against an
  isolated proposed pin, after agent-507's producer qualification; the
  production pin and the defaults are unchanged. The real replay qualifies the consumer's handling of
  two-layer route_demo current-policy evidence only. It is not target
  qualification, not a re-pin and not a router release claim. A retained record
  is still `qualified: false`: binding proves which run the evidence describes,
  not that the producer measured it correctly (only
  `kicad_route.experiment.verify` and producer qualification can). The
  `K91_REVIEWED` hook records the producer commit, the sha256 of its schema
  file and the receipt's file and identity digests. `EXPERIMENT_CONTRACT`
  names the producer as unpinned and not qualified. The protection modes
  `human` and `existing` are not counted as distinct evidence: the receipt is
  `locked`, and `protection.mode` is only compared with the attempt's own
  `protect` setting.
- **Native attempt on an isolated proposed pin (2026-10-22).**
  `scripts/k91_native_positive.py` ran the consumer path once, `step`,
  `run_step`, `verify`, `adopt`, on a disposable copy of `fixtures/rc/control`.
  The router was kicad-route `bd1e633f` (engine source `cb003b30…`) with the
  existing grid_router extension `610b3acd…` agent-507 qualified it with. These
  were copied into a private checkout-mode layout, because the executor reserves
  `/home` and the private state's manifest names its `/home` directory. The pin,
  the code map and the defaults are unchanged. The policy was current-policy,
  F.Cu/B.Cu, and the router's own policy qualification was PASS. The attempt is
  not a positive: it was a contradiction, with no candidate and adoption rejected.
  It had two causes. The adapter refused the run settings above, which this
  change repairs. The producer itself marked the receipt not comparison-eligible
  (`origin predecessor is missing or does not own this input`): kicad-route
  `experiment._origin` reads any `--dir` whose parent directory is named
  `candidate` as a run candidate with a missing predecessor. For a top-level
  project input the adapter's `--dir` is `/candidate/<project>`, so every
  adapter-run K91 receipt for a top-level project is `ineligible`. A nested
  project path (`/candidate/nested/project`) does not meet that rule (review
  probe of the producer's `_origin`; not a receipt and not a route). Agent-ee does
  not relabel it. agent-507 adjudicated that this is the intended producer
  contract. The consumer now routes a staged copy of the original (see the
  router invocation above). The read-only mount is unchanged. A fresh
  adapter-run attempt is needed for a positive. This change does not run one.
  Records, receipts and negative controls are in
  `evidence/k91-native-positive/` (`evidence-manifest.json`, `SHA256SUMS`).
  They predate staging: they record `--dir /candidate/project` and no
  `inputs.staged`, which the current `verify` requires. They are kept as they
  are, as the record of that attempt. They are not re-graded. The
  harness's recorded `controls_refused: false` is a harness defect: it counted
  the mismatched-runtime control's verified `not_run` as accepted. The record is
  kept as written. `scripts/k91_classify.py` re-derives it (`CLASSIFIER_V1`) and
  classifies with `CLASSIFIER_V2`.

  **Boundary.** Under coordinator boundary ruling a01d6f91 (agent-521; a
  coordinator decision, not owner text), the protected boundary is the
  production routing consumer, `campaign.run_implementation_steps`: `run_step`,
  then `verify_campaign_receipt`, then `adopt`, in its own order. The generic
  `stage_receipts.adopt` is not that boundary.

  **Harness plumbing.** `scripts/k91_consumer.py` calls that function and
  observes `run_step`, `verify_campaign_receipt` and `adopt` with pass-through
  observers. Each call is recorded as made (an explicit boolean `accepted`,
  with a value or an error) or as not called. The design digest is recorded
  before and after. The proposed pin takes the production adapter shape: a
  copy of the adapter source with that pin's constants, mounted at the
  production path. For the board, sidecar and stale controls, replay plumbing
  stands in for `run_step` only. It returns the retained receipt, as written,
  from a disposable copy placed at its own path, so the consumer's own checks
  find any change.

  **Controls.** A required control (mismatched runtime, candidate-board
  tamper, stale input) is refused only when all of these hold:
  - the consumer call was observed to raise;
  - the design digest is unchanged;
  - every layer record is schema-valid;
  - no consumer adoption succeeded;
  - for the runtime control, the production binding preflight was also
    observed to refuse.

  This lane's third-HOLD root redesign (review round 3, H4) narrows that
  further. Up to round 3 the classifier inferred a refusal from the absence of
  success, so a wrong replay binding, invalid or missing receipt JSON, or a
  campaign finalization error counted as refused. Now a required control is
  refused only on a positive match of its contract, in
  `k91_classify.CONTRACTS`. Each contract names:
  - the raising origin (file and function);
  - the reason pattern;
  - the production path the root exception took;
  - each consumer layer's outcome;
  - the preconditions, such as a replayed `run_step` that succeeded and an
    applied tamper.

  `observe` records the full exception chain: each link's type, message,
  origin frame and frames. Every link must be the contract refusal. The one
  exception is the board tamper: campaign finalization (`bind_implementation`)
  was observed to raise the same refusal again around it. Anything else is
  incomplete: unrelated exceptions, replay or plumbing failures, other
  finalization errors.

  The chain must also be complete (review round 4, H1). Every non-terminal
  link names its `cause` or `context`, the last link is null-terminated, and
  no link repeats. A truncated, dangling, prematurely ended or cyclic chain is
  malformed, so the control is incomplete even when the remaining links match
  the contract.

  `scripts/k91_control_matrix.py` is the lane's regression gate. It runs the
  positives, every intended control, each plumbing failure in each control
  slot, and every round-1 to round-3 counterexample through the actual
  consumer. `tests/test_k91_control_matrix.py` runs it, and
  `tests/test_k91_reason_probe.py` is the round-3 reviewer's probe.

  The round-1 record predates the staged original. The matrix replays its
  committed `not_run` report under the verifier of its own era: the adapter at
  `7552f6db`, which wrote and committed it. Its modules are committed
  byte-exact under `tests/fixtures/k91-era-7552f6db/`, with a manifest of each
  git blob id and sha256, so the gate image (built without `.git`) needs no git
  history. The matrix loads only fixture files whose sha256 matches the
  manifest. `tests/test_k91_era_fixture.py` checks the files against
  `git show 7552f6db:<path>` when history is present. Without history it
  checks each file's git blob id, computed in Python. That verifier must
  reproduce the recorded value and refuse an edited copy. Today's
  verifier must refuse the same report, for its missing staged original at the
  report's own pin and as stale evidence at the production pin. The committed
  bytes are never rewritten or re-graded.

  An agent-ee capture run (`runs/N` holding `result.json` beside
  `inputs/<project>`) has the layout that kicad-route reads as a missing
  predecessor. So the adapter refuses `runs/N/inputs` as a routing input. A
  caller that routes a captured candidate copies its inputs to a neutral path
  first. The production campaign routes the implementation design copy, which
  has no run record beside it.

  Anything missing or malformed is incomplete. A skipped adoption is never a
  refusal. The bare `stage_receipts.adopt` runs on another design copy as a
  diagnostic (`direct_adopt`), and its result stays visible: it accepts a
  tampered sidecar, which only `verify_campaign_receipt` refuses. The
  sidecar control is reported beside the required ones. The positive needs
  the consumer to verify a delivered candidate with a retained, unqualified
  record and to adopt exactly that board.

  On the synthetic router (`tests/test_k91_harness_classifier.py`), the
  consumer refuses each control at its own layer:
  - the runtime at the live binding, before `run_step`;
  - the board and the sidecar at `verify_campaign_receipt`;
  - the stale input after `run_step`.

  It accepts and adopts the positive and an untampered replay. The round-2
  low-level observations (direct `run_step`, `verify`, bare adoption) are
  kept apart as `low_level`. The successor `scripts/k91_native_positive_v2.py`
  takes the exact runtime and input identities and the router and total
  limits.

  Amendment A1 (`scripts/k91_classify_a1.py`, classifier
  `verdict-2r4-a1`; agent-521 ruling f14c71ec, a coordinator decision)
  is a separate, versioned extension. `k91_classify.py` (`verdict-2r4`) is
  unchanged, and its INCOMPLETE for the positive-c control A stands.
  A1 accepts one more control-A reason. It is the exact wrong-commit
  message `validate_campaign_step` raised for the different-commit runtime
  (b5774496 presented to the proposed bd1e633f), compared by literal
  equality. It must appear at the consumer root and at the host binding,
  on their frame paths, with both commits checked against the captured
  runtime and the proposed pin. All other v2 conditions still apply.
  Every other control, and the same-commit reasons, are judged as before
  (`tests/test_k91_classify_a1.py`). No evaluation under A1 is recorded
  until its review accepts it.
- **Pin.** The pinned `b5774496` (AGENTEE-450) `cli.py` has no
  `--experiment-policy` (the K91 producer commits `a0ed434` and `bd1e633f` are
  not its ancestors), and its `main` turns every usage error into a refusal
  (exit 4). A declared policy at
  this pin is therefore a `refused` attempt with an `absent` record, or a
  contradiction if the refusal output is not the known machine form; it never
  delivers a board. This was read from the pinned source, not run. Consuming real K91 evidence needs the re-pin in the
  [routing policy contract](routing-policy-contract.md#3-consumer-impact-of-campaign2-and-routability-experiment1).
  This change does not re-pin and makes no router release claim.

## Acceptance stays with agent-ee checks

A routing receipt, `result.json` and kicad-route's own validation are
provenance and diagnostics, not check witnesses. kicad-route's validation
permits pre-existing DRC findings. After capture, the campaign's ordinary
checks decide:

- DRC with schematic parity and zero unconnected items;
- `copper-width/1`;
- the configured compiled routing rules (AGENTEE-63);
- preservation of placement and outline.

Freerouting attempts carry the label "attempt only; never a reproducible
measurement". KRT attempts carry "deterministic backend, repeatability not
qualified by this attempt".

## AGENTEE-95 interface

The adapter uses the AGENTEE-95 step and receipt as drafted on
`impl/stage-receipts` and adds no receipt of its own. It relies on the
following from that draft:

- The coordinator runs the step after the writer session. The writer never
  invokes the router or supplies its argv, mounts or environment.
- The declared mounts are read-only. The receipt hashes each mount, the
  executable and the source tree before and after; a change is a violation.
- Only exits 0 and 2 can be adopted, and only the declared write is copied
  back, and only while the design still equals the receipt's inputs.
- Every other exit, timeout and cancellation keeps its receipt, with missing
  artifacts recorded as null, and stops the iteration incomplete.
- Applicability compares the whole delivered tree with the receipt's inputs
  plus its outputs. A later writer edit to the board makes the receipt
  `not_applicable`.

`verify_campaign_receipt(receipt_dir, expected=..., binding=...)` checks the
implementation/source/index binding, frozen tool identity, command and mounts,
then calls `verify(run, receipt=receipt)` after `validate_receipt`, both before
adoption and when verifying a campaign. Generic stage-receipt checks do not
interpret the routing report. A crashed or killed adapter with no report remains
an unadoptable receipt with unknown routing coverage; a missing report never
authorizes a proposal. `test_kicad_route_stage_receipt.py` and the receipt case in
`test_kicad_route_native.py` import `agent_ee.stage_receipts` unconditionally;
a missing or broken module fails their collection.

## Qualification on the original placed RC

The placed RC is the existing [RC control](../fixtures/rc/PROVENANCE.md) with
every track, via and zone removed through pcbnew. Footprint placement, outline,
schematic, project and library tables are unchanged.
[`test_kicad_route_native.py`](../tests/test_kicad_route_native.py) runs the
real router on it when `AGENT_EE_KICAD_ROUTE_CHECKOUT` names a pinned checkout,
once directly under the executor and once as an AGENTEE-95 receipt. Each run
verifies with the production pin, then forges the retained validated-board hash
on the real bytes and checks that `verify` refuses it.

The [standard image](container.md#pinned-routing-tool) installs the pinned
checkout at `/opt/kicad-route` and sets the variable, so strict CI runs these
tests. Their prerequisite, `require_kicad_route()` in
[tests/toolchain.py](../tests/toolchain.py), behaves as follows:

| Configuration | Without `AGENT_EE_REQUIRE_TOOLCHAIN` | With `AGENT_EE_REQUIRE_TOOLCHAIN=1` |
| --- | --- | --- |
| Variable unset | Class skipped: `AGENT_EE_KICAD_ROUTE_CHECKOUT is unset` | Failure |
| Set; HEAD, pinned code or installed-engine manifest/state fail `preflight` | Failure naming every problem | Failure |
| Set; KRT's venv cannot import `grid_router`, numpy, scipy or shapely | Failure with the import error | Failure |

`NativePrerequisiteTest` exercises the first two rows in a child unittest
process. The native class also checks that pinned code with an empty venv and
no installed-engine manifest refuses during preflight, before runtime import
or any routing attempt (AGENTEE-277). The import probe runs only after preflight
qualifies the installed state.
[`test_kicad_route_adapter.py`](../tests/test_kicad_route_adapter.py) covers
exits 0 to 4, contradictions, timeout and cancellation with a coordinator-owned
fixture router. Its retained-verification cases forge one claim at a time with
fresh hashes and identity. Those tests qualify adapter policy, not KRT
behaviour.

[`test_routing_campaign.py`](../tests/test_routing_campaign.py) exercises exits
0 through 4 through the campaign writer and checks the anchored implementation
record and receipt applicability for each exit. It also rejects altered command,
mount, timeout and adoption declarations, and a rehashed execution record claiming
a writable state mount. Its native case routes the placed RC inside a staged
campaign, checks the full host checkout identity and OS-refused write probes, and
runs fresh DRC and `copper-width/1` checks on the captured proposal. Reviewers are
scripted in these orchestration tests; native tool evidence is produced by the
installed KiCad and pinned KRT processes.

## Pin qualification record (AGENTEE-187, 2026-10-18)

The previous pin was `08179cdc97d08864da127f14d53ac46cf5a27851`.
The new pin is the local upstream HEAD captured at implementation start,
`e4979160a19b89837f9a9e9e25db620f3449f40a`, rather than the older `a471b36`
mentioned when the ticket was filed. `PINNED_CODE` was regenerated from this
commit's Git blobs under `bin/` and `src/`. The image installer fetches the same
commit; KRT remains `v0.23.1` at `295f9ee197517e8323726853361eb137cc08ba9e`
with its existing binary and wheel hashes.

Review of the new `cli.run_attempt`, `contract`, and `validate` code identified
these changes: the versioned route record, additional provenance and sandbox
fields, `attempt.rules_files`, `settings.barrier`, machine refusal records,
and separate hashes before and after refill. The adapter adopts these without
relaxing the fixed routing settings, input binding or project preservation.
The full code pin still rejects added, removed or changed source files.

Reproduce qualification with a detached clone at the new pin and the existing
KRT state installed, setting `AGENT_EE_KICAD_ROUTE_CHECKOUT` to that clone:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_kicad_route_native -v
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_kicad_route_adapter -v
```

The native suite qualifies the original placed RC directly and through a stage
receipt, refuses a forged validated-board hash, and compares two fresh runs
with `routing_equivalence.compare_boards`. Equality is normalized copper geometry;
UUID and file-order differences are recorded separately. It also demonstrates
that a duplicate or moved segment is unequal. Policy tests include
`test_unknown_result_version_refused`, missing schemas and changed attempt
shapes, and acceptance of a refilled candidate only when its graded hashes bind.
Independent integration review remains the coordinator's responsibility.

## Pin qualification record (AGENTEE-202, 2026-10-18)

The AGENTEE-202 pin was `a132899a26ed9d7bf95c9978f0c1c992ee066f63`, the merge
of KICADROUTE-61 at `398f6c4`. It replaces `e497916`; the old checkout no
longer qualifies. `PINNED_CODE` includes the new `diff_pairs.py` and
`kicad/pairs.py` modules, and changed CLI, contract and evidence blobs.
`schemas/result-1.json` is byte-identical between the two commits. Default
route results retain their exact key set; default attempts add the required
`schema: kicad-route/attempt/1` field. The image installer uses the new pin;
KRT and its wheel/binary pins are unchanged.

The adapter does not call upstream `validate`; revalidation's
`route-validate/2` is a different kind and cannot substitute for a route result.
Campaign declarations bind the pair opt-in to the exact command and frozen
receipt. Retained verification enforces the version and exact key set together,
including attempt schema IDs even though upstream's contract checker does not
cross-check those IDs against shape.

The native suite exercises the default path, opt-in empty-plan routing through
a stage receipt, and an original two-net pad-row board with a nonempty pair
plan, all in a read-only sandbox. Policy fixtures additionally exercise missing
fields, version downgrades, undeclared pair fields and plan mismatches after
retained hashes have been refreshed.
Independent pin review remains the coordinator's responsibility.

## Owned routing engine (AGENTEE-277)

The pin since AGENTEE-277 includes the routing engine under
`src/kicad_route/router/`; `vendor/krt` is no longer an installed dependency.
The engine retains MIT notices and `UPSTREAM.json`, tracing the owner's fork
`0494a05e` and its tap-grid and forbidden-layer weld fixes
([upstream #1291](https://github.com/drandyhaas/KiCadRoutingTools/issues/1291),
[upstream #1292](https://github.com/drandyhaas/KiCadRoutingTools/issues/1292)).
The historical backend name `krt` now selects that owned implementation.

**Backend names (KICADROUTE-82, AGENTEE-343).** kicad-route renames the owned backend to
`builtin` and keeps `krt` as a deprecated alias. The adapter accepts both in requests,
campaign declarations and parsed `result.json`/`attempt.json`, via
`kicad_route_attempt.canonical_backend`; any other name is refused. A request and its
retained receipts keep the spelling they were written with, so historical `krt`
receipts keep their recorded bytes; the names are normalized only to compare
and look up. Since the `b577449` pin (AGENTEE-450) agent-ee emits only `builtin`: it is
the default of `parse_request`, `routing_adapter.step`, `preflight` and
`validate_declaration`, the router is given `--backend builtin` for either spelling
(`PIN_BACKEND_NAMES`), and `generate.py` and the rung-1 `route.sh` step pass `builtin`.
`krt` is read-only legacy: it is still parsed so older records can be read, and the
default step name is now `kicad-route-builtin`.

Installation requires Rust and builds the included locked crate. The
[container build](container.md#pinned-routing-tool) pins Rust 1.99.1 in its build
stage. No Rust compiler, external KRT checkout or acquisition step is needed at
routing time. Host installers must set HOME and CODEX_HOME to scratch directories
when invoking upstream install/update, to contain generated skill links.

Both host preflight and the sandbox adapter use the qualified tool's existing
state-manifest reader to verify the source digest and installed extension,
requirements, package and KiCad hashes. Missing manifests or extension digests,
source drift and modules outside the layout's venv (`.venv-krt`, or the selected
generation's `venv`) refuse routing. Manifest verification
sets Python's cache prefix to `/dev/null` with bytecode writes disabled, so an
ignored `__pycache__` file cannot replace verified tool source. The source path is
rebased in memory for the sandbox mount before checking its recorded requirements
hash; the manifest on disk stays unchanged. The adapter records the manifest
hash alongside its before/after code and environment identities. Synthetic
policy fixtures explicitly use their own code maps without an engine and do not
establish native routing qualification.

The older pin notes above describe historical qualifications. New native results
must be gathered against this pin; historical results do not transfer by editing
hashes. A routed board remains a proposal requiring fresh agent-ee checks.

## Limits

- One pin. A new kicad-route commit needs a reviewed new pin and fixture
  qualification. Nothing upgrades automatically. The
  [routing policy contract](routing-policy-contract.md#3-consumer-impact-of-campaign2-and-routability-experiment1)
  lists what a re-pin changes.
- When the state is the checkout itself, the read-only mount includes the
  checkout's `.git` and `build/` directories. Host-side kicad-route runs by
  others still change directory metadata there, for example a transient git
  lock. AGENTEE-95 mount identity hashes contents and links, not times.
- Pre-routing refusals include usage, input, ledger, canary and state failures.
  The policy fixture covers known machine refusals, unknown refusal versions
  and obsolete human output; the last two cannot establish a refusal verdict.
- KRT byte-level repeatability is not established. Two identical attempts on
  the placed RC gave the same segment geometry but different new-segment UUIDs
  and file order. The [routing equivalence check](routing-equivalence.md)
  tests normalized copper geometry equality and records byte differences
  without failing them. The native repeat test compares two real KRT outputs
  and identifies a moved segment.

### CAN pair coverage

The additive `semantics.pair_coverage` diagnostic distinguishes reported coupled
native routing from missing, preserved, rejected or single-ended geometry.
Replay rederives it; it never changes routing adoption or grants acceptance.
See the [CAN native probe](pod-can-routing.md) for captured class/gap settings,
independent native length/skew checks and the controlled five-millimetre negative.

## Pin qualification record (AGENTEE-450, 2026-10-22)

The previous pin was `5f1c072b86e0caee82c7656226c4d0e3ec580a58`. The new pin is
kicad-route main `b5774496d8466b67ffa0362b6665fee2108155a3`, 81 commits later, carrying
KICADROUTE-90 (campaign selection publishes only native-safe incumbents) and
KICADROUTE-98 (native terminal preservation). It is not the KICADROUTE-91 release;
that re-pin belongs to the AGENTEE-442 integration.

`PINNED_CODE` was regenerated from every tracked `bin/` and `src/` blob at the new
commit: 309 files (276 before), including new modules such as `copper_ownership.py`,
`native_components.py`, `backend_names.py` and `installation.py`. The image installer
fetches the same commit; the engine is still the owned `grid_router` 0.23.1 built from
the included locked crate, now with changed `rust_router` sources, so its extension
hash differs from the old pin's.

What changed at the adapter boundary, and what agent-ee does about it:

- **Location-bound state.** The pin's `common.state_manifest` refuses a manifest whose
  `state_dir` is not the directory it is read from, and `engine.py` reads it at import.
  The sandbox mounts a checkout state at `/opt/kicad-route` and a separate state at
  `/opt/kicad-route-state`, so the state manifest must be recorded there.
  `verify_state(..., mounted_at=)` refuses any other `state_dir` with a typed problem
  (`state manifest describes ..., not the sandbox state ...`), on the host at preflight
  and again inside the sandbox; the remaining checks read the files wherever the caller
  sees them. The image records `/opt/kicad-route`
  natively. A host checkout records it by running `kicad-route state-manifest` in a
  bwrap view with the checkout bound writable at `/opt/kicad-route` and
  `KICAD_ROUTE_STATE=/opt/kicad-route`.
- **Copper custody.** A `builtin` result whose process passed adds the top-level
  `copper_ownership` pointer (`bound` to `validate/copper-ownership.json`, or
  `unavailable` with reasons; `replay_authority: false`). Its exact shape is required
  on exactly those results and refused elsewhere; a bound pointer must hash the
  retained record and appear in `result.evidence.records`. agent-ee never grades from it.
- **Native terminal preservation.** An eligible attempt must carry
  `validation.net_completion` with status `PASS`, no `split_components` and no
  `nets_lost`, and `validation.native_terminal_evidence` with its two component
  records and metrics; a `COMPLETE` route whose candidate terminals are still split is
  refused. The pin already reports a capped raw DRC list as routing `NOT_RUN`, which
  the adapter classifies as a contradiction without a board.
- **Stale evidence.** `routing_adapter.verify` refuses a report whose
  `adapter.compatibility` names another pin with `StaleRoutingEvidence` (a
  `ValueError` carrying `.pin` and `.expected`) before any other check;
  `validate_binding` does the same for a frozen campaign binding. Historical receipts,
  such as the `08179cd` fixture under `fixtures/rc/stages/receipts/`, are never
  rewritten. Via-budget pilots and campaigns that froze the adapter hash refuse the new
  adapter until recaptured, as before.

- **Router generations.** The pin installs the owned engine in one of two layouts, and
  both qualify only when built from the pinned source. Legacy (manifest
  `krt.generation` null): the engine runs from the checkout's `src/kicad_route/router`
  with `STATE/.venv-krt`; the image's `docker/kicad-route/install.sh` builds this.
  Router generation (`kicad-route update`): `krt.generation` selects
  `STATE/.router-generations/<32 hex>/`, which holds a copy of the source (`router/`),
  its own `venv/` and `installation.json`. For a generation, the manifest's source,
  requirements, venv and extension paths must all lie inside it; the recorded source
  digest must equal `engine.source_sha256(engine.SOURCE_ROOT)` of the verified checkout
  (not the generation's own `engine.ROOT`, which would make the check compare the
  generation with itself); `installation.json` must record the same source digest and
  extension hash; and the pin's drift check hashes the generation's copy. A generation
  built from another commit is self-consistent, so `kicad-route doctor` passes it;
  agent-ee refuses it (`owned engine source digest differs; router generation ... was
  built from commit ...`). State components, required files and write-probe targets
  follow the layout (`state_generation`, `state_components`, `state_required`), and
  `routing_adapter.verify` recovers the layout from the report's state identities.
- **Nested result keys.** Every `result.json` carries `process.measurement_qualification`
  with exactly `measurement`, `build_reproducible` and `reasons`, and its `measurement`
  must equal the top-level one. An owned-engine result's `backend` carries `runtime` with
  exactly `source`, `venv` and `generation`; it must name the engine verified before the
  run (the checkout's `src/kicad_route/router` and `STATE/.venv-krt` with generation
  null, or the selected generation's `router` and `venv`). Freerouting results carry no
  `runtime`. Any other shape, or a runtime other than the verified engine, is a
  contradiction without a board. Covered by `tests.test_kicad_route_generations`.

Settings, refusal classes, retained files and the attempt key set are unchanged. No
routing constraint was relaxed: the fixed argv, settings, `--protect existing`, layer
policy, net classes and preservation checks are as before.

Qualify with `AGENT_EE_KICAD_ROUTE_CHECKOUT` set to the pinned checkout:
`tests.test_kicad_route_repin` (policy and native cases, including a partially joined
net and a 511-net capped raw report), `tests.test_kicad_route_generations` (both
installed layouts, stale-generation refusal, nested result keys, and a native run from a
generation layout), `test_kicad_route_native`,
`test_kicad_route_adapter`, `test_owned_router_pin` and the other modules listed in the
[routing policy contract](routing-policy-contract.md).


### Landed-image production validation (2026-10-22)

The production-validation lane executed one coordinator routing step on the same
`fixtures/rc/control` case as the generation positive, with requirements captured,
through `routing_adapter.step` and `stage_receipts.run_step`. It used the existing
image for landed agent-ee `854f4e023c56be32e834f194a68e7746f59a5b2f` without rebuilding.
The selected manifest had `krt.generation: null` (the supported legacy layout),
`owned-9cfc5d5e9369`, and the b5774496 source identity. The actual engine command
selected `/opt/kicad-route/.venv-krt/bin/python` and the checkout's owned router.
The receipt completed with exit 0 in 17.835 s; adapter replay returned `complete`
and `eligible`, and native validation returned `PASS`. A controlled mismatched
source generation was refused by the same step declaration before routing.

The retained delivery, the independent Opus review and its probes are kept
durably in the private evidence bucket as
`s3://agent-ee-evidence/closeout/AGENTEE-450/AGENTEE-450-evidence-db54e86f0f2cdd162de40be98d7f8c17a66097c8b4f2a8fbbf879740bd753d98.tar.zst`
(the name carries the archive's sha256; its `AGENTEE-450/SHA256SUMS` lists
27,054 files). The worker result records the image identity, exact commands,
evidence hashes, and a corrected harness path assertion (no second routing
attempt). The receipt's `generation: null` is the supported checkout-local
layout, and the image build is not claimed to be reproducible. This qualifies execution
identity on the RC control; it does not demonstrate target-board improvement,
repeatability, differential-pair acceptance, or close AGENTEE-442.

### Production runtime repin to 7d5b0d4d (2026-10-22, AGENTEE-466)

The qualified production runtime moves from the 854f4e02 image to the gate image of agent-ee
`7d5b0d4de65a5b8e8f925ad6f366a46653aae563`, which contains the AGENTEE-454 read-once repair.
The gate built it from `b29cead1c3bfdd8fb8938dcba77bb14956810c90`; `7d5b0d4d` is an empty
stack-marker commit on top of it, with the same tree. Image tag
`agent-ee:ci-b29cead1c3bfdd8fb8938dcba77bb14956810c90`, OCI index
`sha256:6dd73b1559920a8417ac51af6a148b134aff6ee7dacf8e93842156beabce2334` (agent-ee-gate-c4,
containerd store), config `sha256:d96f8e00a50a1bb6fab119ac8973a083af9373e2c9c235f3df20f655cfc3b5b7`
(the ID a classic Docker store reports for the same image). All 1,804 files under
`/opt/agent-ee` match the 7d5b0d4d tree by sha256, with none missing and none extra. The router is
unchanged: b5774496, `krt.generation: null` (the legacy layout), source
`9cfc5d5e9369fd044ac6555afe57bdc4ccc829421e5496d42599c2d4a71951b2` (recomputed from the
checkout), extension `ec5fb733d321343456917b2925ec275af233e3a44d34b2f332800d51352b2cd9`
(hashed from the actual bytes), interpreter `/opt/kicad-route/.venv-krt/bin/python`
(Python 3.12.3). The selected manifest is byte-identical to the one the 854f4e02 validation
selected.

The qualification repeats the validation above on the same `fixtures/rc/control` case. One
routing step ran through `routing_adapter.step`, `stage_receipts.run_step` and
`routing_adapter.verify`. The receipt completed with exit 0 in 35.2 s; the adapter classified
it `complete` and `eligible`, and native validation returned `PASS`. Three mismatched runtimes
were refused by the same step declaration before routing:
- a router generation built from edited source (`owned engine source digest differs`);
- a checkout whose HEAD names the old 5f1c072 pin (`is not the pinned`);
- an installed extension whose bytes differ from its manifest (`state manifest drift`).

One pod-preparation dry run ran under native-admit in this image, using the production-capture
`build.py` and `pipeline.py` at fome-mc `5d923c5412d3659638c67c9cfa9f097e24620518`, with only
`AGENT_EE_PIN` moved to 7d5b0d4d. The committed capture reproduced byte for byte, and all 28
stages were accepted, as in the retained 81e387ee run. The read-once path executed in the
actual pipeline: each of the 90 `parts_manifest.dependency_closure` and 22 `qualify_manifest`
calls read about 128.3 MB, against about 31.3 GB per call in the AGENTEE-454 "before"
measurement. Of the retained run's 211 mapped files, 200 emitted design files are
byte-identical. Eleven mapped records differ. The actual 27 bounded check outcomes remain
execution `ok`, outcome `defect`, and the final blocking obligations remain open. The repinned
baseline additionally records AGENTEE-448 J1 nominal component-thickness screening and
source/current obligation statements; this is a semantic baseline addition, and the accepted
policy is unchanged. All 18 source-revision annotations become null in the exported non-Git
context. Source, run, path, stock-age and policy identities also change. Of the separately
compared 114 emission receipt files, 92 are byte-identical and 22 differ. This proves the
stated design-byte and observed-check equivalence on these inputs, not byte or semantic
identity of every baseline, receipt or metadata record. The pod pin
itself (`AGENT_EE_PIN` in fome-mc `production-capture/pipeline.py`) is fome-mc's and is still
81e387ee on the j6 and j6b2 branches; it moves only in fome-mc. This qualifies execution
identity and the read-once path on these inputs. It is not a latency claim, a routing campaign
or project adoption.


### Intra-pair matching pin qualification (2026-10-24)

The current pin is `1c895b67dd6ca36620c0b5f757d6402e9692bf78` (311 verified
`bin/` and `src/` blobs; code-map SHA-256
`106c7dd3996caae3efdd96553a8267ffa6cfbf03e217cf1a92643752e8539f70`).
Only the public CLI and pair adapter changed; owned backend source, native facts,
pour calculation/refill, layer-policy reports and via-count semantics are
unchanged. Matching is opt-in through the normal placement job's router controls;
its saved-copper grade is independent of backend success. Existing frozen
receipts retain their original pins and are never relabelled.

The [qualification](qualification/pair-matching-2026-10-24.md) records the
fresh public/build-stage positives, meaningful negatives, same-input stock KRT
comparison, clean environment and all 46 required modules with passing evidence.
Three stale pin/documentation expectations in the initial gate were corrected;
the two affected modules pass again in strict native mode. This establishes the
bounded intra-pair operation, not broader signal-integrity or manufacturing
acceptance. The accepted acquisition package remains unchanged.


### Coupled inner-layer transition qualification — 2026-10-24

Pin `86f586c3ae262be12552652e963a3e11ba7b9287` adds opt-in permitted pair
layers, independent saved-layer grading, symmetric straight-transition fanout
and tuning after cleanup. The normal placement job preserves routing-only
native keepouts and carries the layer policy through the actual build producer.
The [qualification record](qualification/pair-transition-2026-10-24.md) binds
positive and rejection artifacts, original rules and the matched stock result.
The full 48-module consumer roster has passing evidence: 972 checks in the
47-module pin run plus five new native qualification tests, with zero skips.
Native facts/pour contracts and mirrored reader behavior remain unchanged.
This qualifies the bounded coupled-transition case; it does not establish
impedance, delay, AC/multi-pair matching or manufacturing acceptance.

### Nominal differential microstrip integration — 2026-10-25

Pin `5801a9f39618853ab941beac7069c68a5270e16e` adds opt-in impedance
synthesis from explicit native stackup and independent saved-geometry/reference
grading. Its exact code map contains 312 files. The ordinary placement job and
build producer carry the target, tolerance, reference net and escape allowance;
the original stackup is checked before native save can insert defaults, and
the actual final board is checked after zone fill. Default adapter schemas,
facts/pour producers and mirrored readers remain unchanged.

The [qualification record](qualification/pair-impedance-2026-10-25.md) binds
public positives, complete-connectivity negatives, unchanged native rules,
an independent KiCad analytical calculation and a matched stock comparison.
All 983 required cases across 49 modules have passing evidence without skips;
the initial missing-`esp_prog`-input failure and its corrected rerun are retained.
Only nominal outer-layer differential microstrip
trunks are qualified here. Inner-layer/multilayer impedance, coplanar models,
supplier tolerances and physical measurement are not discharged by this result.

### Symmetric differential stripline integration — 2026-10-25

Pin `95c3baf2dcbc3d38759cf08839f76d71901d28a5` extends the existing nominal
impedance operation to one symmetric homogeneous inner layer. Both saved
reference planes and the complete coupled corridor are checked. PCB-only
snapshots now preserve captured footprint-library directories using the same
snapshot implementation as schematic projects. Default adapter schemas, native
facts/pour calculations and mirrored reader behavior remain unchanged.

The [qualification](qualification/pair-stripline-2026-10-25.md) records normal
public job/build-producer operation, native rejection cases and a matched stock
comparison. All 987 required consumer cases across 50 modules and 1,064 router
cases have passing evidence. Three stale pin/documentation assertions and one
missing wheel-runtime environment variable were corrected and their exact
cases rerun; the original failed/skipped results remain retained. This qualifies
a nominal software article, not fabricated impedance or a circuit release.
