# Saved-board block contract

`request.blocks.contract` is an `agent-ee.block-contract/1` declaration, required for new candidate sets.
Once supplied, it binds the final saved-coordinate report; it is not a source of
new numeric authority. Candidate documents remain `agent-ee.block-candidate/2`.

Required fields:

- `allocation`: a full `agent-ee.block-allocation/2` record, bound by
  `board_sha256` to the original native capture. Each native footprint, including
  board-only parts, must occur exactly once in a block, `fixed`, or `shared`.
  Blocks have `id`, `references`, an explicit `anchor` (`member`, `fixed`, or
  `none: "centroid of members"`), and `interfaces_to_fixed` (reference or null).
  Fixed entries have `reference`; shared entries also have an explicit `role`.
- `eligibility`: the complete `fome-mc.side-eligibility/1` record, with
  `sources.board_sha256` bound to that same original capture. It must equal the
  candidate-set section's side eligibility. Block faces describe anchor-face alternatives. Explicit member faces may
  differ from the anchor within the default face permission. Nonblock roles need explicit face
  eligibility too. A fixed *role* grants neither pose nor side authority.
- `relationships`: the existing `agent-ee.electrical-locality/1` document, with
  explicit physical pad endpoints and captured source records. Numeric
  enforcement still uses `locality_provenance.classify`; an engineering estimate
  or uncommitted owner label does not become a hard bound.
- `templates`: explicit instances with `relationship`, `role` (`supply`,
  `return`, or `switch`), and `template` (`same-face-only` or
  `decoupler-under-device`). The relationship's `from` endpoint is the device;
  `to` is its support. No template is inferred from a net or reference name.
- `thickness_nm`: positive integer board thickness, or null when unresolved.

The frozen preparation spellings of allocation/2 and side-eligibility/1 are
accepted without rewriting their records. The preparation's prose relationship
catalogue must first be resolved to explicit physical pad endpoints; prose is
not parsed into inferred hard constraints.

`block_contract.membership(capture, allocation)` returns normalized roles,
anchors and block membership with the original allocation digest.
`block_contract.judge(saved_capture, contract, original_capture=..., plan=...)`
returns `agent-ee.block-contract-report/1`. The plan is hash-bound for traceability;
all reported coordinates and distances come from the supplied native recapture.
The caller is responsible for actually saving and reloading the board first.

The report includes member side assignments, anchor spread, courtyard bounding
boxes, and per-net internal 3D Manhattan half-perimeter estimates. These estimates
are not routed lengths, inductances, or routing acceptance. Physical pin distances
use exact rational native copper centroids and add thickness squared for opposite
mounting faces. Display millimetres do not decide acceptance.

A deliberate split requires a declared template, compatible logical membership,
a governed numeric bound, the 3D bound satisfied, and the support endpoint within
the device courtyard expanded by `sqrt(bound² - thickness²)`. Curved-courtyard
uncertainty is retained. Missing templates or forbidden splits report lost
cohesion; missing geometry, authority, thickness or unresolved preparation
constraints remain explicit requirements. Defects take precedence over unrelated
unknowns. Ungoverned numbers remain advisory and cannot establish acceptance.

This report does not itself implement routing feedback, a legalization search,
or mechanical qualification. Those remain separate obligations in AGENTEE-479.

Planning preflight uses `validate(capture, section, effective_policy)` in Python
and Rust. It requires exact relationship-policy and eligibility-record identity,
and an allocation/1 bridge with explicit references and matching member anchors.
Sheet selectors must be resolved into references before this strict boundary.
`check_complete(capture, contract)` emits the same minimal
`agent-ee.block-contract-check/1` decision record in both implementations. It
checks each complete macro/residual placement before search accepts it; the
writer then uses the richer independent report after saving and reloading.

The saved report separately lists actual pose constraints from the original native
`locked` flag and from `plan.policy.fixed_refs`, with their original and saved
position, rotation and face. These are explicit pose authorities, not sourced
numeric electrical limits. A logical allocation `fixed` role alone does not enter
this list. If no plan policy is supplied, the report labels that input unavailable
and reports only native locks; it never guesses pose authority from membership.

Member-refinement witnesses name their capture hash basis as
`public-capture-integral-angles/1`. This binds every public capture field,
including pad identities, geometry, owned rule areas, and metadata. Only private
footprint `_packing_*` caches are excluded; finite native-range quarter-turn
footprint and pad angles use integral JSON spelling. Capture outputs and strict
plan comparison retain their original representation.
