# Judge-gated placement plans (AGENTEE-55, AGENTEE-280, AGENTEE-287)

`placement_plans.plan(facts, packing_policy, spec)` builds an offline,
deterministic plan from the existing saved-board [probe](placement.md).
It borrows its arguments without mutating them. It uses the existing
`agent-ee.packing-policy/1` for held references, obstacles, courtyard gap,
edge margin and placement groups, and the existing
`agent-ee.placement-policy/1` to score the proposal. No third-party code,
library or runtime dependency was added.

The result reuses `agent-ee.packing-plan/1`, adding `mode`, `request`,
`judge_policy`, `before`, `after` and `reasons` to the existing plan fields.
It records the captured board digest, policies, complete footprint transforms
and every blocking reason. `judgment` also holds the `after` judgment.
Transformed geometry is
marked `transformed-proposal-not-native`. Plans are unsealed proposals;
the advisory score is not an acceptance check or proof of electrical quality.

## Modes

| `mode` | Required parameters | Behavior |
| --- | --- | --- |
| `decoupling_row` | `anchor_ref`, `capacitor_refs`, `offset_nm`, `step_nm`, `judge_policy` | Place caller-named capacitors in reference order, relative to the declared IC centre. Each must share a non-ground net with that IC and be explicitly declared in the judge policy. No capacitor is inferred from ground connectivity or its name. |
| `net_cluster_seed` | `refs`, `origin_nm`, `spacing_nm`, `judge_policy` | Union the explicit references transitively through shared non-ground nets; sort by reference and put each cluster on a near-square grid. Spacing has two positive integer nanometre components. Apply the seed through the disposable placement writer when every geometry and score gate passes. |
| `bga_fanout` | `bga_ref`, `escape_offset_nm`, `judge_policy` | Detect uniform square pitch from a complete axis-aligned grid of uniquely numbered circular SMD pads. Suggest outward diagonal escape locations by less than half the pitch. Return a plan only; this does not create vias, tracks or a routed board. |

Coordinates and offsets have two integer nanometre components. The BGA mode
does not qualify missing-ball, staggered, rotated or nonuniform grids. Its
escape points prove neither annular clearance nor routability; native copper
checks would be required for any future copper writer.

## Gates and apply

A plan is `planned` only when all targets and their full courtyards are inside
the proven outline, clear the declared obstacles and gaps, satisfy groups,
and the judge's score strictly increases. Untargeted footprints are obstacles.
Locked footprints and `fixed_refs` retain their exact transforms. A request to
move either is blocked. Any unproven outline or courtyard remains missing
coverage; an existing determinate failure stays in the retained judge record.
Ties and score decreases are blocked even when geometry passes. Cluster seeds
use the same gates as capacitor rows. Cluster records retain `clusters`; the stale
`seed_only` flag is removed because an eligible seed can be applied. BGA mode
remains `blocked` with an explicit plan-only reason; escape details remain available for inspection.
Missing required request keys raise `ValueError` before any writer lease or
staging write. Both preview judgments use the packing policy’s envelope-bound
`fixed_overhangs`, exactly as the saved-board judge does.

The existing [disposable writer](placer.md) accepts an optional `targeted`
object alongside its packing `policy` and write `authorization`:

```json
{
  "mode": "decoupling_row",
  "anchor_ref": "U1",
  "capacitor_refs": ["C1"],
  "offset_nm": [3000000, 0],
  "step_nm": [3000000, 0],
  "judge_policy": {
    "schema": "agent-ee.placement-policy/1",
    "ground_nets": ["GND"],
    "ic_refs": ["U1"],
    "connector_refs": [],
    "max_shared_refs": 8,
    "capacitors": {
      "C1": {"capacitance_nf": 100, "max_distance_mm": 4, "deduction": 15}
    }
  }
}
```

Use the same `python3 -m agent_ee.placer_runner` invocation and captured inputs
as for greedy packing. The default greedy path is unchanged. Targeted plans
are regenerated from the source under the writer lease; caller-supplied plan
bytes cannot authorize a write. A blocked preview returns before creating
even a lease file. Both the pre-lease preview and the leased plan reserve visible
`IMU_AXES` silkscreen labels through the same effective packing policy, retaining
the label UUID and reservation geometry. A target on the marking is blocked.
Successful plans are saved to a fresh staging copy, checked
against actual saved transforms and pad inventory, re-scored with the same
judge policy, checked by native courtyard DRC, and compared by the existing
write-scope validator with every fixed or untargeted UUID protected. Only a
successful result is atomically published as a new disposable candidate.
The captured source is unchanged; failure diagnostics are retained as unknown
execution, never published as placed.

## Campaign selection

An optional `targeted_plans` in the placement adapter declaration names an
immutable captured JSON file containing 1–64 targeted request objects from the
mode table. The coordinator binds its path and digest and forwards its contents
as the helper request's `targeted_plans`; the writer does not choose the mode.
For example, the file can contain a capacitor row and a cluster seed with their
explicit references and coordinates. Every request must carry an equal
`judge_policy` object (value equality, independent of mapping order); differing
policies raise `ValueError` before any lease or candidate write, including when
one candidate would be blocked. This keeps scores comparable and prevents a
lenient policy from winning selection. The helper evaluates each
request and chooses the eligible plan with the highest after score. Ties keep
file order. It retains all evaluated plans under `selection.plans` and records
`selection.selected_index`. Malformed requests abort selection. If none passes,
the result stays blocked with no greedy fallback. An unknown candidate remains
unknown in its retained judgment, even when another candidate is eligible.

Direct writer requests can also supply `targeted_plans`; combining it with
`targeted` is refused. The selected plan is regenerated under the writer lease
and rejudged from saved bytes. A campaign without `targeted_plans` retains its
greedy helper behavior. Captured requests must declare component roles, scoring
policies and target coordinates.

Campaign adoption still needs ordinary capture, receipts and fresh independent
reviews; the helper cannot seal its own result. No check registration is added.

## Executable qualification

`tests.test_placement_plans` covers typed missing-key refusal, overhang parity,
cluster eligibility, shared-policy refusal, deterministic selection with retained
blocked candidates, strict score increase, tie and decrease,
full-courtyard and concave containment, unknown coverage with a preserved
determinate defect, held/locked/untargeted obstacles, explicit capacitor roles,
non-ground binding, transitive clusters, ground exclusion and BGA pitch limits.
`tests.test_placement_plans_native` uses original generated KiCad boards to
prove saved-coordinate preservation, repeat byte-identical plans, no writes
on blocked plans, refusal of an open non-rectangular outline, native BGA pad
inventory, applied cluster seeds without `seed_only`, saved overhang parity,
IMU reservations in both targeted request forms with no lease or candidate write,
unmarked controls, mixed-policy refusal, and successful
networkless Bubblewrap execution with native DRC.
`tests.test_placement_campaign.HelperCampaignTest.test_campaign_selects_cluster_seed_and_replays_captured_plans`
runs campaign selection, fresh captured placement checks, a pinned networkless
KRT attempt on the applied seed, replay and tamper refusal.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest \
  tests.test_placement_plans tests.test_placement_plans_native -v
```

The campaign regression additionally requires a qualified router checkout in
`AGENT_EE_KICAD_ROUTE_CHECKOUT`; its native routing inputs and tool state are
mounted read-only.

The extra `tests` path supports the repository's existing top-level test-helper
imports. These tests establish the declared geometry and score contract; they
do not establish decoupling-loop physics or BGA routing feasibility.
