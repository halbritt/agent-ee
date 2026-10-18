# Assembly and test access (DFA/DFT)

`dfa_dft` checks assembly and test access that the owner declared in the scope
(AGENTEE-64). It evaluates one criterion per check, because the coordinator
witness binds a whole check. Every criterion reads the saved board through the
same sandboxed [placement probe](placement.md#placement-check-kind)
(`agent_ee.placement_probe`). That probe records footprints, sides, rotations,
courtyards, pads and the Edge.Cuts outline. Each pad record has its pad
attribute and layer names, and each footprint has its library ID. The
implementation is [dfa_dft.py](../src/agent_ee/dfa_dft.py).

Nothing is inferred from reference prefixes, symbol classes, footprint
libraries or values. Connectors, test points, probe pads, rails and first-power
values come only from the check parameters. Those parameters are part of the
immutable scope baseline. A value the scope does not declare is unknown. There
is no default current limit, tolerance or insertion direction.

```json
{"id": "usb-access", "kind": "dfa_dft",
 "params": {"criterion": "connector-orientation/1",
            "connectors": [{"reference": "J1", "insertion": "from_left"}]}}
```

| Criterion | Parameters | Capability predicate |
| --- | --- | --- |
| `connector-orientation/1` | `connectors` | `dfa-dft:connector-orientation/1` |
| `testpoint-access/1` | `test_points` | `dfa-dft:testpoint-access/1` |
| `testpoint-coverage/1` | `rails`, `first_power`; optional `test_points`, `probe_pads`, `measurements` | `dfa-dft:testpoint-coverage/1` |

Unknown or missing fields are refused as `unsupported` before any tool runs.
The check needs a captured `board`. If the probe does not run, writes nothing, or
finds no footprints, the result is `execution: failed`. A board without
probe data therefore never produces a pass or a defect.

## Connector orientation

Each `connectors` entry names a `reference` and at most one of:

- `insertion`: the direction the mating part arrives from. The values
  `from_left`, `from_right`, `from_up` and `from_down` are footprint-local
  axes `x-`, `x+`, `y-` and `y+`, with KiCad's downward y. `from_top` and
  `from_bottom` mean the part is mated perpendicular to the board, from the
  front or the back.
- `cable_insertion_mm`: a footprint-local point where the cable enters, as
  `[x, y]` exact decimal text. The local axis is the larger component of that
  point. Equal magnitudes do not select an axis and are unknown.

The local axis is mapped to board axes by mirroring y on the back and rotating
by the saved orientation (`placement_predicates.board_axis`, qualified against
pcbnew pad transforms). The connector must face the outward normal of the
outline edge nearest its saved origin. Distances are exact integer nanometres.
For example, a USB-C receptacle declared `from_left` on the right edge at 0°
faces `x-`, into the board. That is a `facing` defect with `recommended: "x+"`.
At 180° it faces `x+` and passes. `from_top` requires the front side and
`from_bottom` the back. Neither has an edge requirement.

The result is unknown when:

- no direction is declared or derivable;
- the reference is missing or duplicated;
- the rotation is not a quarter turn;
- the outline is not one proven contour;
- the nearest edge is not axis-aligned;
- the origin is equidistant from edges facing different directions, such as at a corner.

[`connector-edge/1`](placement.md#placement-check-kind) remains the check for a
connector whose edge, anchor and tolerance are fixed by a board envelope. This
criterion only needs an insertion direction. It does not model cable bend
radius, mating clearance or the enclosure.

## Test-point probe access

`test_points` lists the footprint references the owner declares as test points.
For every pad of each test point, the check considers each outer side where the
pad has both copper and a solder-mask opening (`F.Cu` + `F.Mask`, or `B.Cu` +
`B.Mask`). On each such side it locates the pad centre exactly against every
other footprint's proven courtyard on the same side:

- Strictly inside a courtyard: that side is blocked. The record names the
  covering footprint in `under_courtyards`.
- On a courtyard boundary: unknown.
- A courtyard on the opposite side does not obstruct.
- An unproven courtyard on that side, such as one with arcs or open contours: unknown.
- A footprint with no courtyard at all: an `info` finding (`missing_courtyard`).
  It is listed, and also stated in the result limitations, but it is not tested
  as an obstruction.

A test point passes when at least one pad side is accessible. It is a defect
when every exposed pad side is blocked, or when no pad copper is exposed through
mask. A test point under a QFN courtyard on the same side is a defect. The same
test point flipped to the opposite side passes.

## Test-point coverage and the bring-up plan

Coverage requires a probe-accessible pad on every net that bring-up or acceptance
measurement touches:

- each declared rail `net` and `return_net`;
- the output, ground and stimulus nets of every earlier acceptance-mapped
  `rc_transient` check with bounded measurements;
- for every bounded metric of an earlier acceptance-mapped `measurements` check,
  the `net` and `return_net` declared in `measurements` as
  `{check, metric, net, return_net}`. An undeclared metric is a gap: the
  capability audit refuses it and the runtime result is unknown.

Only declared probe points count. These are pads of the declared `test_points`
and the owner-declared `probe_pads` (`{reference, pad}`), each judged by the
access rule above. Saved geometry has no body model, so it cannot prove that an
undeclared component pad can be probed: a QFN exposed pad or a BGA ball is
under its own package. Undeclared pads with exposed copper on a failing net are
listed as `undeclared_exposed_pads`, as diagnostics only. A net with no
accessible declared pad is a `net_without_probe_access` defect that names the
net. If a declared pad's access is unknown, the net is unknown instead.

```json
{"id": "bringup", "kind": "dfa_dft",
 "params": {"criterion": "testpoint-coverage/1",
            "test_points": ["TP1", "TP2", "TP3"],
            "probe_pads": [{"reference": "J1", "pad": "1"}],
            "rails": [{"id": "vbus", "net": "VBUS", "return_net": "GND",
                       "nominal_v": 5, "tolerance_v": 0.25},
                      {"id": "3v3", "net": "+3V3", "return_net": "GND",
                       "nominal_v": 3.3, "tolerance_v": 0.1}],
            "first_power": {"rail": "vbus", "current_limit_a": 0.1},
            "measurements": [{"check": "response", "metric": "output",
                              "net": "OUT", "return_net": "GND"}]}}
```

A rail may also name `measurement: {check, metric}` for an earlier
`measurements` check that predicts it. The coverage check writes
`work/<id>/bringup_plan.json` (`agent-ee.bringup-plan/1`). The plan is bound to
the candidate ID and board hash, and to the hashes of the source check results
it read. It is retained as check evidence and sealed with the other evidence.
It contains:

- `first_power`: the declared rail, supply voltage (the rail's `nominal_v`) and
  `current_limit_a`. Bring-up applies the declared supply current-limited before
  any measurement. A missing value is `null` and listed in `missing`.
- `probes`: one `probe:<net>` record per required net, with its roles and
  accessible pads.
- `measurements`: a `rail:<id>` entry per rail, with nominal and tolerance from
  the scope, and a `<check>:<metric>` entry per acceptance measurement, with the
  scope's `min`/`max`. A `bench` binding is the exact `{check_id, metric, unit,
  condition}` of the executed source metric of this candidate.
- `steps`: first power, then one measurement step per entry.

`dfa_dft.bench_sample(plan, id, value, uncertainty)` builds the raw
[`agent-ee.bench/1`](bench-feedback.md#report-and-raw-sample-contracts) sample
for a bench-bound measurement. `plan_measurement(plan, candidate_id, sample)`
maps a sample back to its plan ID, and refuses a sample for a different
candidate. Ordinary bench ingestion accepts those samples for the same candidate
hash. A rail without a declared prediction check has no bench binding: its
reading is a procedural bring-up observation and cannot be ingested as
calibration.

The coverage result is unknown, with `execution: unsupported`, when any of these
holds:

- the first-power current limit or supply voltage is undeclared;
- a rail lacks `nominal_v` or `tolerance_v`;
- a source check did not execute for this candidate, so its bench binding is
  unavailable;
- an acceptance-measured metric has no declared net.

The plan is still written and records every such item under `unknown`. A proved
coverage defect stays a defect beside unrelated unknown items.

## Evidence and limits

Each check retains `placement-probe.json` and `dfa-dft.json`
(`agent-ee.dfa-dft/1`), and the coverage criterion also retains
`bringup_plan.json`. Native tests build owned boards through pcbnew, save and
reload them, and run the real probe inside Bubblewrap through `run_checks`. The
round-trip test ingests a plan-derived sample through `empirical.ingest` against
a real coordinator run. These are software geometry and contract
qualifications. They are not fabrication, assembly or bench approval.

The checks do not model probe-tip size, fixture keep-outs, cable bend radius or
the enclosure. They drive no instruments. Component-over-cutout checks are
outside this kind. The mechanism follows the tscircuit/checks connector and
test-point checks and circuit-synth's test-plan schema ideas, both MIT, as
described in the AGENTEE-64 ticket. No code was copied, and their default
current limit and tolerance are deliberately not used.
