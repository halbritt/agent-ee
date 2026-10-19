# Dedicated RF review (AGENTEE-243)

The `rf` reviewer is an independent initial reviewer role. It has a fixed RF
brief, an exact source binding and a structured assessment. It runs through the
same subscription adapters, sandbox, citation rules and adjudication as the
`circuit` and `physical` roles. It is not a check, and its conclusions are never
RF qualification.

## Applicability is always recorded

After capture, every review run records `rf_review` in `result.json`, using
`agent-ee.rf-review-decision/1`. The report has an **RF review applicability**
section. The decision comes from the captured bytes:

| Indicator | Source |
| --- | --- |
| `rf_constraint_class` | An `agent-ee.net-constraints/1` class with `impedance_ohm` or an RF name, such as RF_50 |
| `schematic_rf_part` | Schematic `lib_id` values from RF libraries (`RF_GPS:`, `RF_Module:`), coaxial connectors and antennas |
| `board_rf_footprint` | Board footprints from those families, such as `Connector_Coaxial:SMA_*` |
| `schematic_rf_related_label`, `board_rf_related_net` | Net names with RF, ANT, GNSS, GPS or LNA tokens |

If any indicator is detected, the review is **required**. A scope declaration
`"rf_review": {"applicable": false, "basis": "..."}` cannot waive detected
indicators; the basis then records the contradiction. A declaration of
`applicable: true` with a nonempty basis makes the review required without
indicators. Optional `sources` binds further captured files by category. If no
indicator is found, the decision records **not applicable**. Its basis counts
the scanned lib_ids, labels, board state and constraint classes. The review is
never skipped silently. Package names such as `Diode_SMD:D_SMA` are not RF
parts.

## Exact candidate and source binding

The decision binds these source categories by run-relative path and SHA-256:
`schematic`, `board`, `stackup`, `rf_constraints`, `rf_captures`, `planes`,
`antenna_and_module_sources` and `datasheets`. A plane plan has the
`agent-ee.plane-plan/1` schema. Filled board zones also bind the board as a
plane source. The board summary uses the shared S-expression parser. It records
the stackup layers, RF parts and their RF pads, zones and fill state, copper on
each RF signal net, RF nets declared but absent from the board, and KiCad line
ranges for the stackup and each RF footprint and track. Empty categories, and
RF designs with no filled zone, appear in `missing`.

RF signal nets are declared RF classes plus coaxial-connector signal pads.
Name-only matches, such as bias, enable, fault and UART nets, are listed as
`rf_related_nets`. They are not treated as RF geometry.

The packet carries the decision as `rf_review` and the RF-relevant native checks
as `rf_native_evidence`. The relevant checks are registered kinds only: DRC,
geometry, routing rules, `net_class_assignment`, netlist and parity. Absent
`net_class_assignment`, `routing_rules` and `geometry` checks are listed in
`absent_kinds` and remain unknown, never pass.

### Routing adapter evidence

Layer policy and RF reference continuity are not check kinds. They come from
the routing adapter's AGENTEE-231 grade, `semantics.layer_policy`, which
includes its `reference_continuity` item. Before the decision is computed, each
run retains the routing evidence for its candidate in `work/rf-routing/`:

- In a campaign, the evidence is the latest anchored receipt of the owned
  routing step.
- In a standalone review, it is the receipt named by
  `agent-ee review --routing-receipt RECEIPT`.
- With no receipt, the record is `missing_evidence`.

A receipt passes only after three checks. Its anchored digest must still
match. `routing_adapter.verify_campaign_receipt` must re-verify it. Its verified
routed board must be the reviewed board.

The adapter report, the reference-continuity measurement and the router's
native board facts (`validate/candidate-facts.json`, AGENTEE-258) are copied
into `work/rf-routing/` as `adapter-report.json`, `reference-continuity.json`
and `native-facts.json`. Each copy must match the receipt artifact and the
report's retained hash. The record then has one of four statuses:

| Status | Meaning |
| --- | --- |
| `verified` | All three checks passed. |
| `missing_evidence` | No receipt was supplied, the step has no adopted receipt, or the attempt left no report. |
| `invalid` | The anchor changed, verification failed, or a copy does not match. |
| `stale` | The verified routed board is a different board. |

The decision embeds the record as `routing_adapter` with its `record_sha256`.
The `routing_adapter` entry in `category_sha256` binds it into `binding_sha256`.
`retained_routing` re-hashes the retained copies. A changed copy, or a record
replayed against another board, becomes `invalid`, which changes the binding,
so `verify` refuses the run. `rf_native_evidence.routing_adapter` repeats the
status, the hashes and the grade.

When the review is required, the decision's `missing` list includes every
non-`verified` status, an absent layer policy, and each grade item that is not
`pass`. The check id `rf-routing` is reserved.

### Per-net RF layer requirements

Captured `rf_captures` JSON can state an RF stackup. Each object with both
`signal_layer` and `reference_layer` becomes an entry in
`rf_layer_requirements`, such as the pod's F.Cu over In1.Cu. Each entry records
its source and JSON locators, the declared impedance nets, and related
evidence:

- the global signal-layer allowlist;
- the matching reference-continuity grade, when the declared reference layer
  and RF nets cover the requirement, and its reference net;
- the requirement's own per-net grade (`per_net_signal_layers`) and the
  router's per-net item, when it declared one;
- the board's observed RF copper layers.

The requirement is graded with its own layers, never the router's declaration.
`routing_adapter`'s per-net grader (`per_net_layer_grade`, AGENTEE-258) runs
with `{net: [signal_layer]}` for each requirement net on the retained native
facts of a `verified` record. Those facts must name the reviewed board's
SHA-256. A global F.Cu/B.Cu allowlist, or a looser router declaration, never
satisfies it. The status is:

- `pass` only when all of the following hold. The grade passes: every segment,
  arc and full via span of the net lies on the signal layer, so any via fails.
  The verified layer policy declares a ground reference net on the reference
  layer, covering the requirement nets. A net the statement names, such as
  `GND` in `In1.Cu GND`, must equal that net, with a leading `/` ignored. Its
  AGENTEE-231 `reference_continuity` item passes. The retained measurement, of
  the same board, has a `continuous` record on that layer for every routed
  track UUID of the net in the native facts.
- `defect` when the grade finds copper off the signal layer, or the verified
  reference-continuity grade reports a defect.
- `unknown` otherwise. This covers absent, `missing_evidence`, `invalid` or
  `stale` routing evidence; no retained facts; facts or a measurement of
  another board; no routed copper; an undeclared or non-ground reference; a
  reference on another layer; and an unknown continuity grade.

A requirement that is not `pass` is listed in `missing`. A `pass` is layer and
reference-plane evidence only. It establishes no impedance, coplanar gap,
stitching, branch length (AGENTEE-261) or RF qualification. A board that the
router withheld (`policy_failed`) is never delivered. The review then sees the
unrouted candidate, so the receipt is `stale` and the requirement is `unknown`.

The `rf` reviewer's schema requires `rf_assessment.binding_sha256` to equal the
decision's `binding_sha256`. The review's candidate, run and packet identity
are already bound. Validation then refuses:

- an assessment that omits any of the eight topics, or covers one twice;
- a `supported` topic without structured citations;
- a `missing_evidence` topic that does not name what is missing;
- `qualification_claimed` other than `false`;
- an RF finding without a citation to a bound `inputs/` source, the retained
  routing evidence, or check/experiment evidence;
- a `supported` ground-reference topic
  (`ground_reference_continuity_return_paths`) without `verified` routing
  evidence whose reference continuity passed;
- a `supported` layer-transition topic (`width_gap_layer_transitions_launches`)
  while any RF layer requirement is not `pass`;
- an applicable review whose `external_qualification` omits field solving,
  TDR or VNA for declared controlled impedance, receiver or installed GNSS
  measurements for a GNSS path, or installed BLE measurement for a BLE module.

Any refusal makes the review malformed. It gets the usual bounded resubmission.

## Brief and topics

The brief covers the GNSS receiver-to-SMA path and BLE/module antenna
integration. It covers controlled-impedance assumptions and tolerances, and the
actual width, gap, layer transitions, vias, stubs and launches. It also covers
ground-reference continuity and return paths, stitching and decoupling,
antenna keepouts with nearby copper, hardware and the enclosure, and RF supply
noise and coupling. The assessment topics are:

- `gnss_receiver_to_sma_path`
- `ble_module_antenna_integration`
- `controlled_impedance_assumptions_tolerances`
- `width_gap_layer_transitions_launches`
- `ground_reference_continuity_return_paths`
- `stitching_and_decoupling`
- `antenna_keepout_nearby_copper_enclosure`
- `rf_supply_noise_and_coupling`

Each topic is `supported`, `concern`, `missing_evidence` or `not_applicable`.
This separates conclusions supported by cited evidence from missing evidence.

None of these is RF qualification: a 50 ohm declaration, a calculator or solver
result for idealized geometry, a DRC or minimum-clearance pass, a net-class
assignment, or 2D plane coverage. The brief says so, and so does the decision
(`declared_input_not_qualification`). The adjudicator prompt repeats it.

## Completion, staleness and closure

`rf_review.gate` feeds `outcome()`. A run is not complete when a required RF
review has any of these problems:

- **missing**: the profile has no `rf` reviewer. Checks still run, but no
  reviewer is dispatched, so the review budget is not spent on an
  incomplete result.
- **failed**: any non-`ok` execution, including authentication or quota
  failure. Harness failure policy is unchanged: there is no API-key, OpenRouter
  or other provider fallback.
- **malformed**: no validated `rf` review.
- **stale**: the review's `binding_sha256` differs from the current decision.

`verify` recomputes the decision from the captured snapshot and refuses a stored
decision that differs. A forged `required: false` cannot waive the review.

Every run reviews afresh; an RF review never carries over, including between
campaign iterations. When a run names `previous` (bench re-review),
`rf_invalidation` records the changed
RF source categories, with `prior_rf_review: invalidated` or
`sources_unchanged`. Invalidation is per category, by whole-file digest: any
board change invalidates the RF review. This is conservative; it does not try to
recognize RF-neutral board edits.

RF findings go through adjudication like other reviewer findings. In
baseline-bound runs they stay concerns, or duplicates of executable checks. A
deferred major RF concern leaves the outcome `defects_or_unresolved`. Both
`link-correction` and `findings.validate_closure` require two things to close an
`rf/` finding:

- a complete, bound RF review of the new candidate;
- at least one changed RF source category.

The correction event records `rf_changed_categories`.

## Profiles, pod wiring and budget

[`profiles/pod-review.json`](../profiles/pod-review.json) assigns these routes:

| Role | Harness | Model |
| --- | --- | --- |
| `circuit` | codex | `gpt-6.1-sol` |
| `physical` | agy | `gemini-4-argon-high` |
| `rf` | claude | `claude-opus-5-5` |
| adjudicator | claude | `claude-opus-5-5` |

Each call has a 900-second timeout. This is a configuration, not a model ranking.
The pod driver refuses a `review_profile` without an `rf` reviewer. The pod has
a NEO-M9N-to-SMA GNSS path and an ESP32-S3-WROOM-1 antenna.

A third reviewer adds one subscription call per run. At most two initial calls
run concurrently, so the RF review can queue behind the others. The worst-case
initial phase is two reviewer timeouts, plus the adjudicator timeout, giving
2700 seconds with this profile. Plan `review_budget_s` and campaign
`max_seconds` must include it.

`agent-ee review SCOPE --profile P --roles rf` runs a targeted review. Only the
named roles are dispatched; the others keep `not_run` records, and adjudication
does not run. The result is always `incomplete`; it is useful for a fresh RF
review after an RF-affecting change, or for demonstration.

## Remaining external RF qualification

These obligations remain open after any RF review. No measurement exists, none
is implied, and none may be recorded from reviewer opinion:

- field solving of the as-built RF geometry: trace, coplanar gap, launch, vias,
  solder mask and fabricated stackup tolerances;
- TDR on fabricated coupons or boards, and VNA return-loss and matching
  measurements of the GNSS SMA path and the module antenna feed;
- GNSS receiver sensitivity and installed C/N0 with the actual antenna and cable;
- installed BLE/Wi-Fi measurements in the enclosure: return loss, efficiency or
  RSSI, and range;
- emissions pre-compliance where it is required.

## First live use

On 2026-10-19 one targeted RF review (`--roles rf`) ran through the Claude
subscription route. Its subject was the thermal-corrected telemetry-pod
candidate: the native schematic and board from `pod-thermal-native-v1`, with the
ee225 plane plan and the captured GNSS RF records. The model requested and
observed was `claude-opus-5-5`. The call used no resubmission, the binding
matched, and `verify` accepted the run. The result is incomplete by design.

This run predates the routing evidence binding. Its subject was the pre-230
`RF_IN` candidate, which had no RF routing, so it is historical evidence only.

The review returned eight findings:

- **Defect**: the GNSS RF path had no copper; it was witnessed by the DRC
  defect.
- **Major concern**: the captured RF_50 class still named `RF_IN`, not
  `/POD_GNSS_RF`. The decision independently lists it under
  `rf_nets_absent_from_board`.
- **Minor and info concerns**: stackup assumptions, the SMA launch pad, absent
  stitching, ESP32 antenna placement, supply noise, and an unobserved antenna
  fault flag.

Two topics were missing evidence and six were concerns. Nine external
qualification methods remain. The retained run, report and evidence references
are in the AGENTEE-243 worker scratch. They are not committed, because the run
contains captured vendor documents.

```sh
PYTHONPATH=src:tests python3 -m unittest tests.test_rf_review -v
AGENT_EE_KICAD_ROUTE_CHECKOUT=/path/to/kicad-route-a132899 \
  PYTHONPATH=src python3 -m unittest tests.test_rf_layer_wiring -v
```

[`test_rf_layer_wiring.py`](../tests/test_rf_layer_wiring.py) routes an
original four-layer probe natively under declared layer and reference
policies. It captures each receipt in a full `review` run with scripted
reviewers, then replays the run with `verify`. Its requirement is SIGNAL on
F.Cu over In1.Cu GND.

- A compliant route passes, and the layer topic may be `supported`. An edited
  `native-facts.json` copy fails `verify`.
- A B.Cu route and a via to B.Cu were delivered under a global-only policy.
  Both are `defect`.
- A route without a declared reference is `unknown`.
- A reference void makes the router withhold the board, so the requirement is
  `unknown` (`stale`).
- The clean receipt applied to another board is `stale`.
- Facts edited inside the receipt make it `invalid`.

Every non-`pass` case refuses a `supported` layer topic.
