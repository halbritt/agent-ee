# AGENTEE-479 block placement result

The generic engine now searches native face variants, retries block placement
when residual packing fails, and refines individual members after global
placement. Rust and the Python reference use the same bounded search and final
constraints. Saved-board admission rechecks membership, sides, pin distances,
fixed poses and accepted refinement moves from native coordinates.

This is authored-fixture evidence. It does not accept a regenerated telemetry
pod. Routing feedback is explicitly unavailable; target mechanical restrictions
and unresolved relationships remain required inputs.

## Delivery and inputs

- Holder: agent-524, existing Codex subscription session on proximal;
  dispatch-reported model `gpt-6-astra`.
- Branch: `wt/agentee-479`; agent-449 owns integration. No main push.
- Base: `7300471082154f3453584c7011bb6e069b612ba1`.
- Frozen contract: fome-mc `f65d7903ddd86362643fd9c1271b90ca82f5cef3`,
  `telemetry-pod/expansion/agentee-473-trace/RESULT.md` sections 4 and 5.
- Dispatch: Cairn `b8aef96c-c1ca-468c-b289-f77d7010b4ff` v1; AGENTEE-479,
  parent AGENTEE-472, preparation AGENTEE-478.
- Authorized interval: 2026-10-23 09:40:21–11:40:21 UTC. All Rust builds and
  native tests ran through host admission and the pinned CI image. No cloud,
  new agent session or AppArmor change. Model time was not measured.

## Observable behavior

| Case | Result | Evidence |
|---|---|---|
| Congested six-part board | First A/F+B/F placement cannot fit R1; residual retry changes B to back, then completes | `test_public_planner_moves_block_after_residual_conflict` |
| Conditional capacity proof | A proof against one fixed arrangement triggers another block arrangement; B/back completes | `test_capacity_proof_of_one_arrangement_does_not_end_global_search` |
| Same board, side choice disabled | `arrangement_search_exhausted`, outcome unknown; retained evidence shows 28 macro steps plus 8 charged residual attempts (the test asserts unknown exhaustion, not the step split) | `test_same_input_without_side_choice_exhausts_without_claiming_infeasible` |
| Spare front capacity | B stays front with lower inter-block wire cost; no side-balance objective | `test_spare_front_prefers_shorter_inter_block_wire` |
| Deliberate mixed-face block | U1/front and C1/back remain one membership; declared 3D pin relationships and saved-board contract pass | `test_native_cross_face_members_keep_template_cohesion` |
| Native three-member chain | C1 moves (3.0, 4.0)→(3.0, 4.2) mm on B after global placement; internal HPWL 2.3→2.1 mm, whole-board HPWL 7.1→6.9 mm; native replay verifies the move | `test_native_chain_refines_member_after_global_arrangement` |

Retained evidence shows byte-identical captured board facts and native XML for
the first and side-disabled cases; the test does not assert that identity. The
test does assert that their requests differ only in `arrangement.side_choice`. Exhaustion
is not physical infeasibility: the independent hand-built back-side witness
passes placement, group, pin and native courtyard checks.

The original five cases retain their literal geometry. The added chain has its
own asymmetric C2 courtyard geometry, captured 4 mm pin bounds and a hard 3.25 mm C1-to-U1
origin-distance group; it is not a matched arm of
the congested six-part comparison. Its members come from actual native Stage-A
search, with no transplanted layout. The chain declares a
20,000-step packing budget and a 1,000-step macro budget, and uses the default
64-step member refinement budget; the original comparison keeps its 1,000-step limits.

## Frozen regression contract

| Item | Evidence |
|---|---|
| 1. Deterministic literal fixture | Six parts, outline/courtyards/fixed parts/shadow/access bounds in `tests/underside_fixture.py`; fixed ordering and 1,000-step budgets |
| 2. Known feasible underside witness | Independently saved hand-built board, placement/group/pin checks and native courtyard DRC; exclusive residual slot checked geometrically |
| 3. Actual block adjustment | Retained first/final arrangements and `residual_packing_failed` trace show B F→B; conditional zero-step capacity failures consume one residual attempt |
| 4. Obligations after movement and save | Native recapture reports full membership, allowed faces, anchor spread, internal connection estimates, exact pin distances and deliberate split classification |
| 5. Negative saved mutations | Displaced member fails with typed pin-distance violation; forbidden face fails with typed side/anchor-side violations |
| 6. Matched side-disabled arm | Same input/order/budget except side-choice flag; retained exhaustion is explicitly unknown |
| 7. No side balancing | Spare-front case selects F; objective is locality, inter-block wire and thermal spread |
| 8. Write authorization | Native variants are recreated from the original board; derived eligibility authorizes only exact selected flips; forged explicit side changes are refused |

## Implementation boundaries

`agent-ee.block-candidate-set/1` wraps unchanged inner
`agent-ee.block-candidate/2` documents. Each eligible anchor face gets a native
reproduction attempt, immutable input binding, and either a packed candidate or
a typed failure. The unchanged face retains the original bytes; a refused native
flip has no capture. Explicit member-face permissions allow deliberate mixed-face members.
Candidate provenance remains the starting layout; final member poses and the
refinement witness are recorded separately.

Strict allocation/2 covers every native footprint exactly once, including
board-only parts. A logical fixed/shared role grants no pose or side authority.
Native locks and actual policy fixed refs are separately reported with their
original and saved poses. Numeric pin bounds require captured source authority;
missing geometry, required cross-face templates, thickness or material
restrictions cannot become PASS. Thickness is a declared contract input, not an independently extracted
stackup measurement. Pin distances use native copper-land centroids plus that
declared thickness, not routed electrical length.

Post-global refinement moves only search-derived, non-anchor, unlocked members
within the selected block's original courtyard window. It preserves faces and
rotations. Every accepted move strictly reduces internal non-ground HPWL,
never increases whole-board HPWL or any declared 3D pin-pair distance, and never
reduces thermal separation. Native geometry, gap, edge, access, owned rule
areas, copper, groups, locality and applicable hard block rules remain hard gates. This is a
finite local search, not a proof of an optimal placement. Targets include exact
non-ground pad-centroid alignments, using integer floor/ceiling neighbors for
fractional centers; no unrelated pad-axis Cartesian product is constructed.

Saved admission reverses accepted translations from native recapture, verifies
the original selected Stage-A layout, then replays and recomputes every accepted
move's metrics and constraints. Capture hashes explicitly bind all public
fields, excluding private packing caches and normalizing only integral
quarter-turn angle spelling. Planned and saved-native capture hashes have
separate meanings; they are not asserted equal.

## Validation and retained evidence

| Check | Result and source | Retained record |
|---|---|---|
| Rust unit tests | 63 passed; production code `562a4bff` | `logs/native-build-green8.log` |
| Refinement, provenance, original five native scenarios | 23 passed at `562a4bff`; the added chain failed Stage-A budget and was retained | `logs/green8.log`, `green8-summary.md`, `green8-fixture/` |
| Final native chain | 1 passed, no skips, at `1b38922d`; only the chain fixture changed after green8 | `logs/chain11.log`, `chain11-review.json`, `chain11-fixture/` |
| Contract coverage | 22 passed at `84f01357` | `logs/green4.log` |
| Legacy block arrangement and file origins | 27 + 31 passed at `148fb1dd` | `logs/green3.log` |
| Native variant and request adapter cases | 5 + 16 passed in green2 | `logs/green2.log` |

The 24 affected refinement/provenance/native-scenario tests pass across green8
and the focused chain11 repair. Green8 itself was a failed run, not a clean gate.
The earlier supporting checks are recorded at their own revisions; this is not
a claim that a full repository suite ran at final HEAD.

The current executable is from `agent-ee:exec-149276250b0861ec`, image ID
`sha256:746a046f13cb1c7987f42b1187818cf19da2d209a7d84308a3d23a214070624d`.
Its Rust executable SHA-256 is
`746693934a3302ce817220bb28bf54999f5d73a67aa136d76457ecf1aa295401`.
KiCad is 10.0.7; Rust is 1.99.1. CI's separate frozen fome-mc dependency is
`1289f3149d2bcaa83a3b490a0bfbd11ed8a17589`; that does not change the
`f65d7903` requirements pin or establish production-pod qualification.

To reproduce the affected tests under host admission:

```sh
AE_CI_WEIGHT=4 AE_CI_MAX_WAIT=600 ae-ci-exec . -- \
  env PYTHONPATH=src:tests python3 scripts/strict_suite.py --jobs 3 \
  test_block_refinement test_block_provenance test_congested_underside
```

The final chain's native face variants each packed in 16 Stage-A steps. Its
complete plan uses 54 steps: 2 macro, 1 residual, 51 member-refinement trials.
The four squared 3D pair distances, in mm², change from
`[3.65, 6.29, 2.60, 4.20]` to `[3.65, 6.29, 2.56, 3.84]`.
Both eligible faces were attempted and packed. The selected board has a passing
native contract with no defects or unknowns, and independently replayed metrics
match exactly. R1's owned pad-rule area follows its residual placement.

Failed chain trials are retained: incomplete pin inventory, finite Stage-A
exhaustion, and layouts with no admissible improvement. Those results motivated
fixture corrections and exact pad-centroid targets; their geometry or budgets
are not presented as identical to the final chain. No failure was relabelled
PASS and no hard distance/window admission guard was removed.

The archived base planner was run on the retained final congested request and
refused the new `contract`/`side_eligibility` fields. That establishes the old
interface's feature refusal on the exact fixture input; it does not claim an
old search result on a request that the old interface cannot parse.

Native DRC evidence here covers courtyard placement. The final chain retains
7 library-footprint findings and 9 unconnected items; earlier authored boards
also retain such findings. No routed or
full-DRC acceptance is claimed. The complete integration gate belongs to 449;
passed checks were reused until a relevant source, fixture or contract changed.

All logs, failed trials, native boards, request/capture JSON, plan comparisons,
mutation reports and manifests are retained under `build/agentee-479/` in the
worktree. The portable archive and SHA-256 receipt are
`/home/halbritt/.cache/agent-ee-evidence/agentee-479-1b38922d.tar.gz` and its
`.sha256` sibling. `evidence-manifest.json` inside binds the final branch revision,
CI image and every retained evidence file. The source commit, archive hash and
review verdict accompany the single Cairn delivery to 449 for relay to 519.

## Remaining target acceptance

The emitted production pod still needs total membership coverage, captured
physical pin endpoints and bounds, sourced mechanical/face restrictions,
production request binding, and saved-board acceptance on its own exact bytes.
Routing feedback remains `unavailable` with an explicit reason. Fixture success,
branch delivery and integration do not close those obligations.

## Integration record (agent-449, 2026-10-23)

This section reconciles the critical review's "pending" check list (its gap G8) with the completed logs. Only terminal results are listed; earlier invalid or pre-final attempts are not evidence.

- Landed on main as `c4724465` (479 at `09c0076b`) and `a28e80a0` (return-leg proof `1e1d2367`, tests and docs only).
- Integration gate on `09c0076b`, pinned CI image, `PYTHONPATH=src:tests`: 349 tests OK, exit 0, no import failures. Two earlier attempts were invalid harness invocations (import errors only) and are not results.
- On the merged result `a28e80a0`: `scripts/check_docs.py` exit 0, `scripts/check_origins.py` exit 0, and `test_block_contract`, `test_file_origins` and `test_tree_hygiene` 63 OK.
- Runtime identity: the installed engine `agent-ee-placement` sha256 `746693934a3302ce817220bb28bf54999f5d73a67aa136d76457ecf1aa295401` and its receipt `agent-ee-placement.json` sha256 `d9cd854c70e9dbbe30bd2ad5e7b01af12b1a6dd80c7e9950f79b3585568fa733` were extracted from image `agent-ee:exec-149276250b0861ec`. The receipt's `binary_sha256` equals the engine hash, and all 27 receipt source hashes equal `09c0076b`.
- Evidence archive `agentee-479-1b38922d.tar.gz` (sha256 `a456822183434ff9a39e8160b58848609085fef34e60a384d6cd5ad74d1ea51a`): 1040 of 1040 manifest files match by size and hash.
- Return-leg probe (review Q4 c–f, corrected second run, exit 0; log sha256 `815cef5003c3b51d…`):
  - a missing return role is refused;
  - a named non-ground return passes within bound and is a defect beyond it;
  - a return without a numeric bound is unknown;
  - a changed saved return pad is a defect;
  - a complete check without a return bound is unknown, never pass.
- Open, outside this increment: AGENTEE-489 mechanical consumption and actual-pod final-coordinate evidence (required before AGENTEE-485); review follow-ups G1, G4, G5 and G6 (agent-524).
