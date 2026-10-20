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
`5f1c072b86e0caee82c7656226c4d0e3ec580a58` only (AGENTEE-277).
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
state=None, budget=300, timeout=None, backend='krt', diff_pairs=None,
layer_policy=None)` returns:

| Field | Value |
| --- | --- |
| `argv` | `/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py --project P --board B --schematic S --name N --label L --budget T --timeout U --backend krt --state checkout` |
| `mounts` | adapter file at `/opt/agent-ee-routing/kicad_route_attempt.py`; checkout at `/opt/kicad-route`; a separate state directory, when there is one, at `/opt/kicad-route-state` |
| `source` | `/opt/kicad-route/src` |
| `writes` | `{"P/B": "routing/candidate/P/B"}`: the board is the only file the step can change |
| `artifacts` | `routing/adapter-report.json`, the router's stdout/stderr, and `routing/attempt/{result.json, attempt.json, RESULT.md, snapshot.json, validate, facts, work}` |
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
  run --out /work/routing/out --json --backend krt --dir /candidate/P --board B --schematic S
  --name N --label L --protect existing --budget T
```

`--out` names the sandbox's writable output directory; the pin accepts it
before or after `run`. `--json` produces a versioned record on stdout. The router reads the
read-only `/candidate` copy, so it cannot edit the design it routes. Its
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
  example `KICAD_ROUTE_STATE/.venv-krt/bin/python`;
- a write probe succeeds. The script tries to create a file in the state
  directory, in each backend component (`.venv-krt` for KRT,
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
  with the literal target `/candidate/<same name>` (the input root in the
  sandbox), and the two link sets equal, as `copy_project` links beside the
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
hashed under `omitted_design_files` and not copied. The only board that leaves
an attempt is `routing/candidate/P/B` of an eligible exit. Missing reports are
listed under `missing_artifacts`; none is invented. The raw OUT stays in the
step's work directory.

The report records the request, the fixed argv and environment, the process
exit or signal, the discovery, the retained file hashes, the full input
inventory, the code and state identities before and after, the write probes,
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

The current pin `5f1c072b86e0caee82c7656226c4d0e3ec580a58` includes the routing engine under
`src/kicad_route/router/`; `vendor/krt` is no longer an installed dependency.
The engine retains MIT notices and `UPSTREAM.json`, tracing the owner's fork
`0494a05e` and its tap-grid and forbidden-layer weld fixes
([upstream #1291](https://github.com/drandyhaas/KiCadRoutingTools/issues/1291),
[upstream #1292](https://github.com/drandyhaas/KiCadRoutingTools/issues/1292)).
The historical backend name `krt` now selects that owned implementation.

Installation requires Rust and builds the included locked crate. The
[container build](container.md#pinned-routing-tool) pins Rust 1.99.1 in its build
stage. No Rust compiler, external KRT checkout or acquisition step is needed at
routing time. Host installers must set HOME and CODEX_HOME to scratch directories
when invoking upstream install/update, to contain generated skill links.

Both host preflight and the sandbox adapter use the qualified tool's existing
state-manifest reader to verify the source digest and installed extension,
requirements, package and KiCad hashes. Missing manifests or extension digests,
source drift and modules outside `.venv-krt` refuse routing. Manifest verification
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
  qualification. Nothing upgrades automatically.
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
