# Routing policy contract for routability experiments (KICADROUTE-91)

This page states what agent-ee passes to kicad-route, and expects back, when a
routing attempt is run for a routability experiment. It answers the kicad-route
owner's KICADROUTE-91 boundary questions (AGENTEE-388). It covers:

1. the trace, via, clearance and layer policy that applies to the pod and the
   fixtures, with each value's source and provenance class;
2. what identifies a routing target for comparison, and what is missing;
3. what agent-ee must change when it re-pins kicad-route onto the planned
   `campaign/2` and `routability-experiment/1` work.

The machine-readable summary is
[`routing-policy-contract.json`](routing-policy-contract.json).
[`tests/test_routing_policy_contract.py`](../tests/test_routing_policy_contract.py)
checks the summary against the adapter's constants: pin, pinned-code digest,
accepted schema versions, refusal classes, retained reports, the layer-policy
argument translation and the fixed settings. It fails on any re-pin until this
page and the summary are updated.

The [routing adapter](routing-adapter.md) remains the authority for the protocol.
This page adds no check, threshold or schema that code reads. It adopts no pod
value: adoption stays with the pod lane.

## Sources read

| Alias | Location | Revision |
| --- | --- | --- |
| A | this repository | base `f06b30bc` |
| K | pinned checkout `build/kicad-route-pin-b577449`, read only; `routing_adapter.preflight` reports no problems (re-pinned by AGENTEE-450; the sections below were first written against `5f1c072`) | `b5774496d8466b67ffa0362b6665fee2108155a3` |
| F | fome-mc store, read only | `8c42f2ee3e7c5c77c51e21632a8900bfbef23726` |

F paths below are relative to `telemetry-pod/expansion/`. File hashes were
computed with `sha256sum`:

| F file | sha256 |
| --- | --- |
| `design-rules/board-setup.kicad_pro` | `7c7933bf09fad1617887d95dce24ae2b994737bf4cc46c20662e447561515b74` |
| `design-rules/telemetry-pod.kicad_dru` | `ff62208301c38bca2c963a0682e0a2ee843d8a546bace3719340cfcca1b08d0d` |
| `design-rules/constraints.json` (`fome-mc.constraint-provenance/1`) | `6c73a35a7c2fb427f892d0e93e099709982ec236d336da159acc14c51590a9a6` |
| `design-rules/planes.json` (`agent-ee.plane-plan/1`) | `9e592e3ee4bdf5b6042fd3a595235cc5425359f4a69f87787ce00fc3624779aa` |
| `placement-seed-microsd/base-seed.kicad_pcb` (uncompressed) | `05da3eb69336089a8f8ab7111c900c4b4431508c45c99aed8ffee0ce8b6523c8` |

The project and rule-file hashes are the ones the
[routability design](research/2026-10-21-routability-design.md#25-board-constraints-and-results)
records for the iteration-8d campaign round 0 (project `7c7933bf…`, rules
`ff622083…`). That campaign's own receipts are at F `19522e37`, which is not in
the local store, so they were not re-read here. The iteration-2 and iteration-3
diagnostic receipts under `routing-diagnostic/` carry the same rules file.

Native values were read with KiCad 10.0.7's pcbnew on scratch copies: the microSD
base seed, with `board-setup.kicad_pro` and `telemetry-pod.kicad_dru` renamed to
the board's stem. Provenance classes are the ones `design_rule_provenance.inventory`
and `finding_sources` (AGENTEE-331) return for those files.

## 1. Trace, via, clearance and layer policy

### How agent-ee passes policy today

agent-ee does not pass widths, clearances or vias to kicad-route as arguments. The
router reads them from the board's own project and rule files, which are part of
the read-only input tree. The adapter's request carries only:

- the project directory, board, schematic, name, label, budget, timeout, backend
  and state mode;
- an optional [`layer_policy`](routing-adapter.md#declared-layer-policy-agentee-231):
  `signal_layers`, optional `layer_costs`, `pour_area_loss_pct`, optional
  `reference` and optional `per_net_signal_layers`;
- an optional `diff_pairs: "auto"`, which cannot be combined with a layer policy.

The fixed argv is `kicad-route run --out /work/routing/out --json --backend builtin
--dir /candidate/P --board B --schematic S --name N --label L --protect existing
--budget T`. A layer policy adds `--pour-area-loss PCT` and
`--backend-arg=--layers --backend-arg=<layer> ...`, plus `--layer-costs` when
declared (`kicad_route_attempt.tool_argv`, `policy_argv`, `policy_backend_args`).
The result's `settings` must equal `expected_settings` exactly. In particular,
`default_clearance_mm`, `clearance_margin_mm` and `rule_floor_sha256` are null and
`backend_env` is `{}`.

Before launch the adapter refuses an input whose project declares netclass
patterns that do not bind to the board's nets (`constraint_binding_problems`).
After routing it grades only the declared layer policy. Widths, clearances and
vias are graded afterwards by agent-ee's own DRC, `copper-width/1` and the
[compiled routing rules](routing-rules.md), never by the router's report.

### Pod values

On the probe, every one of the 235 nets is in `Default`, including
`/POD_GNSS_RF`. The project has `netclass_assignments: []` (`board-setup.kicad_pro:299`)
and `netclass_patterns: []` (line 300). The four copper layers are all native type
`signal`, and the board has **zero rule areas**.

| Item | Value | Source | fome-mc class | agent-ee class (enforcement) |
| --- | --- | --- | --- | --- |
| Default track width | 0.20 mm | `board-setup.kicad_pro:288` | engineering (`constraints.json:153`) | default (advisory) |
| Default clearance | 0.20 mm | `board-setup.kicad_pro:277` | engineering (`constraints.json:152`) | default (advisory) |
| Default via diameter / drill | 0.60 / 0.30 mm | `board-setup.kicad_pro:290-291` | engineering (`constraints.json:154-155`) | default (advisory) |
| Thermal vias under exposed pads | 0.70 / 0.30 mm | footprint copies, `design-rules/README.md:6` | engineering (`constraints.json:108`) | footprint geometry, not a routing value |
| Minimum track width | 0.09 mm | `board-setup.kicad_pro:156` | manufacturer, JLCPCB (`constraints.json:25`) | manufacturer (hard) |
| Minimum clearance | 0.09 mm | `board-setup.kicad_pro:143` | manufacturer (`constraints.json:18`) | manufacturer (hard) |
| Minimum via diameter | 0.50 mm | `board-setup.kicad_pro:158` | engineering (`constraints.json:38`) | engineering (hard) |
| Minimum through-hole | 0.30 mm | `board-setup.kicad_pro:155` | engineering (`constraints.json:32`) | engineering (hard) |
| Minimum via annular ring | 0.10 mm | `board-setup.kicad_pro:157` | engineering (`constraints.json:44`) | engineering (hard) |
| Hole clearance / hole to hole | 0.25 / 0.25 mm | `board-setup.kicad_pro:147-148` | engineering (`constraints.json:56`, `50`) | engineering (hard) |
| Copper to edge | 0.30 mm | `board-setup.kicad_pro:145` | engineering (`constraints.json:62`) | engineering (hard) |
| `agent_ee_netclass_RF_50` track width | min 0.335 mm | `telemetry-pod.kicad_dru:2-4` | engineering (`constraints.json:96-97`) | owner (hard), but **inert** |
| Fine-pitch pad-to-pad, U2 and U16–U19 | 0.15 mm | `telemetry-pod.kicad_dru:5-19` | datasheet (`constraints.json:101-102`) | owner (hard), pad pairs inside one footprint only |

Notes on the classes:

- **The Default net class is advisory in agent-ee.** `constraints.json` records
  the class under `net_classes.Default` (line 150). The agent-ee importer reads
  `net_class_default` (`design_rule_provenance.declaration`). It therefore classes
  0.20/0.20/0.60/0.30 as `default`. fome-mc's own rule for `engineering`
  (`constraints.json:8`) says the cited floor gates and the margin is advisory.
  Both readings make the gating values the board minimums above, not the class
  values. Aligning the two field names is a pod-lane or importer change. It would
  make these four values hard, so it is out of scope for this page.
- **Same-stem custom rules are owner rules.** `finding_sources` classes each rule
  in the board's own `.kicad_dru` as `owner`, unless a binding declares it
  generated. Their `constraints.json` descriptions are not exact native rule names.
- **RF_50 is inert.** The rule's condition is `A.NetClass == 'RF_50'`. No project
  read here defines that class: only the older 80×50 mm pod project
  (`telemetry-pod/telemetry-pod.kicad_pro:185`) does. On the expanded board an RF
  track is routed and graded at the Default 0.20 mm. The iteration-3 diagnostic's
  native facts show the same thing: all 369 nets are `Default`, and all but four
  of its 3,918 tracks are 0.20 mm wide (the four are 0.4 and 0.8 mm on B.Cu). The captured GCPW width is 0.3344 mm (see the
  [pod routing policy determination](pod-routing-policy-determination.md)).
  Assigning `/POD_GNSS_RF` to an `RF_50` class is a pod input change.

### Pod layers

| Layer | Role | Source |
| --- | --- | --- |
| F.Cu | signal | D-PCB-7 v2, `constraints.json:148` |
| In1.Cu | `/GND` plane over the outline; **no tracks of any length**; through vias may cross it | D-PCB-5 `constraints.json:146`, `planes.json:4-17`; D-PCB-7 v2 |
| In2.Cu | `/+3V3` plane over the outline, **opened to signal tracks**; the pour fills around them | D-PCB-5, `planes.json:18-31`; D-PCB-7 v2 |
| B.Cu | signal | D-PCB-7 v2 |
| RF corridor | no tracks and no vias on In1/In2 in x 76..88, y 26.5..30.6 mm | D-PCB-6, `constraints.json:147` |

The stack is JLC04161H-7628 (`constraints.json:136-140`, class `engineering`).
D-PCB-7 was first "both inner layers carry no tracks" (two signal layers plus two
planes). Revision v2 (2026-10-21) opened In2 because closing both inner layers
starved routing. D-PCB-5, 6 and 7 all sit under `decisions_pending_next_seed`.
None is in a native board yet, since the seed has zero rule areas, and they carry
no provenance class in `constraints.json`. They are recorded design decisions,
not numeric limits.

**Short plane-layer tracks are not allowed.** Neither the decision nor the adapter
has a length exception. With In1.Cu outside `signal_layers`, KRT places no track
copper on it (FORBIDDEN cost; vias may still span it). A new In1 track of any
length is a `disallowed_tracks` defect, so the attempt becomes `policy_failed`.
Only an input track matched one to one in the reference facts is `preexisting`.
On In2, track length is limited only by the derived area-loss bound.

The D-PCB-7 v2 policy as an adapter declaration:

```json
{"signal_layers": ["F.Cu", "In2.Cu", "B.Cu"],
 "per_net_signal_layers": {"/POD_GNSS_RF": ["F.Cu"]},
 "reference": {"net": "/GND", "layers": ["In1.Cu"], "rf_nets": ["/POD_GNSS_RF"]}}
```

This is the `policy` of a `layer_policy_derivation`. Its `pour_area_loss_pct` is
never written in configuration. The coordinator derives it on each routing turn
from an `agent-ee.via-budget/2` pilot
([AGENTEE-260](routing-adapter.md#derived-at-the-pre-routing-boundary-agentee-260)).
Without a pilot the policy is unknown and the router does not run. No layer costs
are sourced, so none are declared. The D-PCB-6 corridor cannot be expressed in a
layer policy: it must be a native rule area on the input board, produced before
routing. `reference_continuity` then measures RF coverage, including RF via
antipads.

**Historical runs.** The pod's KRT diagnostics were run directly with kicad-route,
not through the adapter. They used `backend_env`
`KICAD_NET_RESCUE=0`, and sometimes `KICAD_PLANE_FINALIZE=0`
(`routing-diagnostic/iteration-3/candidate/planed-krt/result.json`). They passed
no `--layers`, so all four layers had equal cost. That iteration-3 candidate put
473 tracks on In1 and 235 on In2. Every via was 0.60/0.30 mm through-hole. The
adapter fixes `backend_env` to `{}`, so it cannot reproduce those settings, and
an all-layer run is not a D-PCB-7 result.

### Fixture values

| Fixture | Policy | Source |
| --- | --- | --- |
| placed RC (`fixtures/rc/control`) | 2 layers. The project declares no net classes, so KiCad's built-in Default applies: 0.20 mm track, 0.20 mm clearance, 0.60/0.30 mm via. Board minimums are 0.0 mm clearance, 0.20 mm track, 0.50 mm via and 0.30 mm hole | native probe; class `default`, advisory |
| scaffold-produced pod project | Default via 0.45/0.20 mm, with DRC floors at 0.25/0.15 mm | `test_kicad_route_native.test_scaffold_four_layer_default_via_routes_without_annular_errors`; preferred values are [router targets](routing-adapter.md#router-targets-from-the-scaffold-producer) |
| four-layer layer-policy probe | `OUTER`: F.Cu/B.Cu with GND reference on In1/In2; `INNER`: all four layers, outer cost 1000 | `tests/test_route_layer_policy.py:130-134`; test inputs, not limits |

## 2. Routing target identity

A comparison must hold every target-identity field below equal, except the
declared experiment axis and the measured `result.runtime_s`, which is an
observed outcome and never an input to hold equal. Every field is already in the adapter report, the retained reports or the
stage receipt:

| Identity | Where agent-ee captures it |
| --- | --- |
| Board, project, custom rules, schematic, library tables | `report.inputs.files` (sha256 of every input file) and `report.inputs.sha256` (canonical tree identity) |
| Router input fingerprint | `result.input.fingerprint`, recomputed by `verify` from `snapshot.json` |
| Rule files the router loaded | `result.input.rules_files` / `attempt.rules_files` (names: `project`, `custom_rules`, `not_loaded`); their digests are in the snapshot |
| Effective per-net class values | retained `facts/reference-facts.json` `nets[net_name].{netclass, track_width, clearance, via_diameter, via_drill}`, bound to the input board hash |
| Layer stack | retained reference facts `copper_layers`, `stackup` |
| Layer policy | `report.request.layer_policy`; `result.settings.backend_args`, `pour_area_loss_pct`; derived record `layer_policy_records[N]` |
| kicad-route code | `report.tool.commit` equal to `PIN`; `identity_before/after.code` for `bin/` and `src/`; `PINNED_CODE` inside the adapter bytes |
| Engine and installed state | `identity_before/after.state` (`.venv-krt`), the state-manifest digest, `result.backend` (`version`, `build`, `commit`, `source_sha256`) |
| Toolchain | `report.tool.{kicad_route, python, kicad_cli}.sha256`, KiCad template library tables |
| Adapter and execution | receipt mount identities, `state.json.routing_binding.adapter_sha256`, exact sandbox argv and environment |
| Budget | `request.budget_s`, `timeout_s` (the measured `result.runtime_s` is an outcome, not identity) |

Gaps. None of these is captured today:

- **No separate placement, netlist or mechanical identity.** The board sha256
  changes with any edit. A placement experiment can only be compared by also
  asserting equal project, rules and schematic hashes.
- **No policy digest independent of the whole `.kicad_pro`.** Editor-only changes
  in the project also change the target. This is conservative, but it hides
  equivalent targets. The effective values per net come from the reference facts.
- **No effective KRT configuration.** `route/1` does not report the grid, per-layer
  costs after defaults, iteration limits, net order, seed, net-rescue or
  plane-finalize defaults, or the plane-fragility cost. `routability-experiment/1`
  is meant to add these.
- **No `backend_env`.** It is fixed to `{}`, so environment-controlled router
  modes are not an experiment axis through the adapter.
- **No chain identity.** The adapter runs one attempt, with no original chain
  root, predecessor or campaign round.
- **No resource measurements.** RSS, host identity and repair or history counters
  are not recorded.
- **kicad-route `schemas/` is outside the code pin.** `verify_code` covers only
  `bin/` and `src/`. agent-ee checks exact key sets itself and does not validate
  against the JSON Schema files.

## 3. Consumer impact of `campaign/2` and `routability-experiment/1`

agent-ee runs only `kicad-route run`. It accepts:

- `kicad-route/route/1`;
- `route/2` together with `attempt/2`, only with `diff_pairs: "auto"`;
- `kicad-route/attempt/1`;
- `kicad-route/refusal/1`, with `reason_class` one of usage, input, ledger, canary
  or state;
- on a `builtin` result whose process passed, the `copper_ownership` pointer
  (`status` `bound` or `unavailable`, `replay_authority: false`). A bound pointer
  must name `validate/copper-ownership.json` with the hash of the retained bytes,
  also listed in `result.evidence.records`. agent-ee binds it and never grades
  from it.

It never reads `kicad-route/route-campaign/1` or `/2` (the pin's campaign record),
`blocked/2`, revalidation records, the `kicad-route/copper-ownership/1` record's
content, or any planned `campaign/2` or `routability-experiment/1`. A `grep` of
`src/` finds no other kicad-route schema.

The code pin is still byte-exact over every tracked `bin/` and `src/` file:
309 files, including `src/kicad_route/campaign.py`
(`c70e888fa40f7e0605fc14b8656ccd34a5e977a295f71c348876ed9dd40e75a9`). A
campaign-only change therefore stops every agent-ee routing step:

- a changed file is refused as `tool file differs from pin`;
- a new module, such as a sidecar producer, is refused as `unpinned file in tool code`.

The refusal happens before the router runs. Nothing upgrades automatically.

When agent-ee re-pins onto a commit with that work:

1. **Hash list.** Set `kicad_route_attempt.PIN`. Regenerate `PINNED_CODE` from
   every tracked `bin/` and `src/` blob at the new commit, including
   `campaign.py`, the engine under `src/kicad_route/router/` and any new module.
   Update `routing-policy-contract.json` (`pin`, `pinned_code_files`,
   `pinned_code_sha256`, `pinned_campaign_py_sha256`).
2. **Installed state.** Rebuild the engine and state for the new pin. Point
   `docker/kicad-route/install.sh` `KICAD_ROUTE_COMMIT` and the host
   `build/kicad-route-pin-<sha>` checkout at it. Since `b577449` the state manifest
   is bound to its location: record it where the sandbox mounts it
   (`/opt/kicad-route`, or `/opt/kicad-route-state` for a separate state). `preflight` must report no
   problems, because `verify_state` imports the pin's own `engine` and
   `statemanifest` and checks the manifest digests.
3. **Schema acceptance.** The adapter checks exact shapes:
   - schema strings in `semantics`;
   - `RESULT_KEYS` and `ATTEMPT_KEYS`, as exact top-level key sets;
   - `expected_settings`, as an exact dict;
   - `refused_output`: refusal kind, exit 4, `command: run` and the five reason classes.

   If the `run` result, attempt or refusal changes, each change must be reviewed
   and accepted explicitly. Examples are a new top-level key that references a
   sidecar, a new settings key such as a seed or effective configuration, a new
   refusal class or a new exit code. Until then each is a `contradiction`
   (adapter exit 65). If `campaign/2` and the sidecar touch only the `campaign`
   command, no acceptance change is needed beyond step 1.
4. **Sidecar retention.** `retain` copies only `result.json`, `attempt.json`,
   `RESULT.md`, `snapshot.json` and the `validate/`, `facts/` and `work/` trees. A
   new top-level file in the attempt directory, such as
   `routability-experiment.json`, is neither refused nor retained. The receipt
   would silently omit it. To consume the sidecar, add it to `RETAINED_FILES`
   and `EXPECTED_REPORTS`, and verify its hashes against `inputs`, the snapshot
   fingerprint and `result.backend` in `verify`. Its comparison key must be
   rebuilt from the bytes agent-ee retains, not trusted as a label.
5. **Re-implemented kicad-route behaviour.** Re-audit every place agent-ee mirrors
   kicad-route internals, whenever the pin changes `cli.py`, `kicad/facts.py`,
   `kicad/pour.py` or `pour.py`:
   - `pour_budget` and `pour_budget_probe`: area summation, 0.001 % rounding and
     the refill command;
   - `layer_policy_grade`: `validate/preservation.json`,
     `validate/pour-integrity.json`, `validate/candidate-facts.json` and
     `facts/reference-facts.json`;
   - `via_budget_pilot`: via counts from the same facts;
   - `rf_review`: `FACTS_FILE`.
6. **Frozen bindings and evidence.** Campaigns that froze the adapter hash
   (`policy_files['kicad_route_attempt.py']`, `state.json.routing_binding`)
   refuse the new adapter until the binding is recaptured. Via-budget pilot bases
   bind the router identity, so they need a new pilot. Results from older pins
   do not transfer by editing hashes.
7. **Qualification and text.** Run every test that imports the adapter or needs
   the router against the new checkout, with `AGENT_EE_REQUIRE_TOOLCHAIN=1` and
   zero skips. They include `test_kicad_route_native`, `test_kicad_route_adapter`,
   `test_kicad_route_stage_receipt`, `test_owned_router_pin`, `test_routing_adapter`,
   `test_routing_campaign`, `test_route_layer_policy`, `test_per_net_layers`,
   `test_layer_policy_lineage`, `test_via_budget_pilot`, `test_pour_budget`,
   `test_rf_layer_wiring`, `test_gnss_rf_path` and `test_routing_policy_contract`.
   Add a pin qualification record to the [routing adapter](routing-adapter.md)
   page. Update the pin text in `Dockerfile`, `README.md`,
   `docs/check-reference.md`, `docs/container.md`, `docs/generator-roadmap.md`,
   `docs/rung1-qualified.md`, `docs/pod-composition.md`,
   `docs/pod-can-routing.md`, `docs/gnss-rf-path.md`, `docs/rf-review.md`, the
   strings in `src/agent_ee/rung1_qualified.py`, and the
   `tests/test_kicad_route_native.py` docstring.
