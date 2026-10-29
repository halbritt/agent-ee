# Acquisition ADC pre-route return seed

`agent_ee.acquisition_local_return.produce` is a narrow placement-lifecycle
operation for the captured acquisition recipe. It connects fresh native C3 pad
2 to fresh native U2 pad 3 on F.Cu using 0.25 mm tracks. It changes no original
board bytes, component poses, pad geometry, library identifiers, existing
copper, project, or custom rules. It requires the pinned KiCad 10 saved-board
version 20260206 and the captured project's 0.2 mm minimum clearance. The caller
retains the existing exact acquisition recipe admission; this helper is not a
recipe registry. Missing/ambiguous endpoints, non-GND endpoints, non-front-only
pads, and unsupported source formats are refused. It does not move components
or bridge planes.

The operation is source preparation for a later global route. It does not
establish final engineering or manufacturing acceptance. The unchanged final
engineering model must find actual trace paths for **both** fresh supply and
return legs and measure their sum at most 10 mm. Missing paths remain failures.
No old supply measurement or old return allowance enters this operation.

## Interface and custody

```python
from agent_ee.acquisition_local_return import produce

seed = produce(
    accepted_placement_directory, new_evidence_directory,
    expected_inputs={
        'board.kicad_pcb': accepted_placement_board_sha256,
        'board.kicad_pro': captured_project_sha256,
        'board.kicad_dru': captured_rules_sha256_or_None,
    },
    clock=existing_placement_clock,
    cancel_dir=existing_placement_job_directory,
)
route_input = seed['output']  # complete board/project/optional rules directory
```

The caller supplies hashes from the accepted placement and captured input
custody; it must not bless changed bytes by recapturing them after a discrepancy.
The operation binds these hashes and its own helper source hash in `record.json`,
checks them again around native work and before adoption, and reloads the saved
native seed to prove exact new UUIDs, net codes, front layer, endpoints and width.
The complete original S-expression tree remains intact; only new root segments
are appended, with new UUIDs. Source and destination aliases, existing output,
and an output inside the source directory are refused.

Up to five unique paths are tried in fixed order: direct, two orthogonal, and
two 45-degree-plus-axis paths, derived solely from current integer-nanometre pad
centres. Each trial retains the proposed board and raw native DRC report/log.
Native rule-loading proof is reused when custom rules exist. The existing
placement DRC runner owns subprocess cancellation and deadline handling. Every
native conductive/unknown finding, exclusion or parity finding refuses the
proposal. The library-warning exception requires a fresh exact baseline
match as described below. Warning-only `silk_overlap` and `silk_over_copper`
findings are retained separately for the required later artwork stage; this
pre-route operation cannot establish artwork acceptance. Unconnected pre-route nets are retained and counted;
they are not clearance failures. Malformed or unavailable DRC never establishes
a pass. Exhausting the finite proposals refuses without an `accepted` directory.
The operation has no separate clock, retry budget, or resumable repair identity.
All trials consume the caller's admitted placement budget.

The coverage gate requires `$schema=https://schemas.kicad.org/drc.v1.json`,
KiCad `10.0.7`, millimetre units, and exactly the included severities `error`,
`warning`, `exclusion`. Missing/malformed ignored-check metadata is a refusal,
even with an empty violation list. Ignored entries need unique keys and nonempty
descriptions. Every ignored key outside the existing `dc_rail.NONCONDUCTIVE_IGNORES`
set is refused, including clearance, track width, copper edge, short and keepout
checks. Sharing that finite category set does **not** borrow the DC model's
ideal contacts or establish a full DRC/placement/parity pass.

Applicability here comes from the operation's proven write scope and separate
placement/final gates, not from an example report having those defaults:

- `missing_courtyard`: no footprint or courtyard changes; the caller must already
  have accepted placement/body/access checks. The trace operation cannot claim
  that an absent courtyard was evaluated or that body placement is accepted.
- `footprint_filters_mismatch` and `footprint_type_mismatch`: library identities,
  footprint metadata and all pads stay byte-identical. These symbol/package
  metadata predicates do not evaluate the added trace's clearance or width.
  Final schematic parity remains a separate obligation.
- `track_not_centered_on_via`: no vias are created/moved. New endpoints are actual
  pad centres or the finite path's exact corners. Via-centering preference does
  not suppress native conductive clearance/width/open checks; final trace length
  uses actual saved geometry, without a zero-cost via/plane shortcut.
- `tuning_profile_track_geometries`: this producer adds only plain segments,
  with no tuning profile or tuning operation. Existing geometry is unchanged.

The captured project must have absent or empty `rule_severities` and
`drc_exclusions`. Nonempty overrides are refused even when they would make a
check stricter. Malformed/null values are refused. Captured custom rules may
not carry any `(severity ...)` override. The existing native rule-loading
witness still applies. This finite no-suppression prerequisite preserves the
authored project and rules exactly; it never clears exclusions or rewrites
severities. Report exclusions are refused in every finding category, including
pre-route opens. An arbitrary warning type cannot be treated as a clean result.

Before generating paths, the operation obtains **one fresh native DRC baseline**
on a private byte-identical copy of the same accepted-placement board, project
and rules, using the same DRC runner, environment, outer clock and CANCEL. There
is no baseline argument, caller qualification, historical baseline reuse, new
clock, or extra retry. This adds one baseline to the existing at-most-five path
checks; the existing custom-rule loading witness is separate and unchanged.

The baseline must satisfy the same full coverage/no-suppression gate. Apart from
the two deferred artwork warning types, permitted baseline records are `lib_footprint_issues` at WARNING
severity, with the exact diagnostic form `The current configuration does not
include the footprint library 'NAME'`. Each item must resolve to the fresh
original native footprint UUID, reference, exact position and library nickname.
Other library diagnostic forms, unbound items, conductive or unknown warnings,
errors and exclusions refuse. This exception records immutable missing-library
metadata; it does not declare the affected library configuration repaired.

Each trial must preserve the **identical complete-record multiset** of those
warnings, comparing full JSON records including diagnostics, severity, item
UUID/reference/position and any extra record fields. Record order may change;
internal record content and multiplicities may not. A new, changed, missing or
duplicated record rejects the proposal. Counts alone and a type-only waiver
cannot pass. No conductive/unknown warning is carried, even from the baseline.

`baseline/` retains the original copied inputs and raw native report/log;
`record.json` binds exact input board and report hashes plus execution receipt.
Every trial similarly binds its exact board/report hashes and records full
added/missing warning records on mismatch. Baseline inputs/report are checked
again around trials and before adoption. Raw reports must be ordinary unaliased
files. Selected trial and copied reports are rehashed before publication; a
changed report cannot survive behind a stale receipt hash. The original source remains immutable.
Native raw findings and byte preservation establish custody; the operation still
does not claim final routing, library health, parity or engineering acceptance.

A complete checked copy is published under `accepted/` only after custody and
native checks. Failure evidence remains under the new evidence directory. Never
reuse a failed destination. A `seeded` record is a production-operation receipt,
not a stage receipt, qualification, judgment, or release decision.

## Placement integration

The existing `mechanics` declaration admits only the captured acquisition model
and checker. That declaration enables this operation automatically; there is no
second caller-qualified boolean. Preparation and the placement checkpoint bind
the helper source hash. Jobs without the declaration retain their existing path.

Inside `_execute_job`, an eligible placement with a passing, matching mechanics
result enters `produce` before global routing. Its fresh `local-return-SUFFIX`
directory retains the operation. The baseline and proposals consume the existing
candidate's equal share of the remaining job deadline, with the same CANCEL
directory. Only the unspent share goes to global routing. Refusal aborts that
job without an unseeded fallback or exploratory-placement seed.

The original placement stays immutable. Global routing consumes the accepted
seed directory, detects existing copper, and selects `preserve_existing`
(`--protect existing`), disabling owned-copper feedback retries. Existing final
checks retain their original placement/rule/geometry basis. `verify_seed` then
reloads the final board and requires every exact seed UUID, segment endpoint,
width, layer and net plus the original native endpoint identities to survive.
It also verifies the retained seed/report/operation receipt. The routing trial
and final result retain original, seed and final board identities separately.

Only warning-severity overlap of silk with silk or copper is deferred to the
existing required artwork stage. Both types already occur on actual acquisition
placements before artwork; added tracks may create additional cosmetic overlap.
All such records are retained, including differences. No text/footprint is
modified by the seed operation. An error, exclusion, unknown warning or any
conductive finding still refuses, and final artwork/package gates remain intact.

The native production proof requires a later root admission on a new,
source-bound development identity with a finite budget covering one fresh baseline and up to five
pre-route path checks and the existing global route. Existing prep 3/3 and saved-board
repair 2/2 are exhausted and remain unchanged. No real target preparation,
KiCad, routing, render, provider call, or acceptance run is performed by this
source patch. Pure tests exercise custody, candidate derivation, endpoint
resolution, preservation, collision refusal and stop propagation using explicit
native-loader/DRC test doubles. They do not prove native geometry legality.
