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
  `return`, `switch`, or explicit single-leg `leg`), and `template` (`same-face-only` or
  `decoupler-under-device`). The relationship's `from` endpoint is the device;
  `to` is its support. No template is inferred from a net or reference name.
- `thickness_nm`: positive integer board thickness, or null when unresolved.

The frozen preparation spellings of allocation/2 and side-eligibility/1 are
accepted without rewriting their records. The preparation's prose relationship
catalogue must first be resolved to explicit physical pad endpoints; prose is
not parsed into inferred hard constraints.

For AGENTEE-488, the preparation producer must emit these physical endpoint
pairs in the existing `agent-ee.electrical-locality/1` obligations:

| Obligation | Role | `from.reference` / `from.pad` | `to.reference` / `to.pad` | Both endpoints' `net` |
|---|---|---|---|---|
| U100 VIO decoupler | `supply` | `U100` / `5` | `C133` / `1` | `/+3V3` |
| U100 VIO decoupler | `return` | `U100` / `3` | `C133` / `2` | `/+5V_CAN` |
| U21 VIO decoupler | `supply` | `U21` / `5` | `C84` / `1` | `/+3V3` |
| U21 VIO decoupler | `return` | `U21` / `3` | `C84` / `2` | `/+5V_CAN` |

Each obligation needs its own `id`, `metric: "pad-centre-to-centre"`, `supply`
and `return`. Every endpoint names `reference`, `pad` and `net`; a captured pad
`uuid` is optional. The return is the explicitly named node, with no assumption
that it is ground. The declared 5 mm return limit is `max_distance_nm: 5000000`;
carry its existing citation, numeric provenance and captured `source_records`
so the consumer can verify authority. This table specifies endpoint identity,
not a new numeric authority. The block contract's relationship document must
also match the effective policy's `electrical_locality` document exactly.

An omitted `return` is invalid input. A missing, ambiguous, wrong-net or
unmeasurable named return is unsatisfied: the generic relationship check and
block check cannot report `pass`. Missing geometry is reported as `unknown`,
and an exceeded governed distance as `defect`; neither is acceptance. A valid
supply leg cannot compensate for an unsatisfied return. The producer must not
translate a missing measurement into a satisfied bound.

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

A deliberate split requires a declared template and compatible logical membership.
For a governed numeric bound, the 3D bound must be satisfied and the support
endpoint must lie within the device courtyard expanded by
`sqrt(bound² - thickness²)`. For an advisory engineering target, the support
endpoint must lie **within the device courtyard itself**: an advisory radius
cannot expand this qualitative under-device permission or veto a legal split.
Curved-courtyard uncertainty is retained. Missing templates or forbidden splits
report lost cohesion; missing geometry, numeric measurement targets, thickness
or unresolved preparation constraints remain explicit requirements. Defects take
precedence over unrelated unknowns. An advisory target is measured, scored and
reported with its provenance and `exceeded` flag; exceeding it is an explicit
reported relaxation, never a manufacturer gate or an UNKNOWN solely for lacking
numeric authority. Planning checks return `pass` with `advisories`; the richer
saved report returns `info` when advisories are its only findings. Neither status
qualifies routing, impedance or mechanical access.

AGENTEE-503 adds the explicit single-leg alternative to `relationships`:

```json
{"id": "signal-to-support", "kind": "single-leg",
 "metric": "pad-centre-to-centre",
 "leg": {"from": {"reference": "U1", "pad": "3", "net": "/SIGNAL"},
         "to": {"reference": "R1", "pad": "1", "net": "/SIGNAL"},
         "max_distance_nm": 5000000,
         "provenance": {"class": "engineering", "basis": "Declared placement target"}}}
```

This is a schema example, not a pod limit. Exactly one nonempty `leg` is required;
`supply`, `return`, `switch` and buck-loop fields cannot be mixed into this form.
Its endpoints retain all existing native reference/pad/net/UUID and centroid
checks. Templates use `role: "leg"`. Packing, refinement, candidate-policy hashes,
and final native-coordinate reports carry that role. Legacy paired obligations
still require both supply and return; deleting a return never creates a single
leg. The entire relationship document must still equal effective policy.

An optional `routing_obligations` array carries stage-scoped requirements without
claiming they were proved by placement. Each row has exactly `id`,
`stage: "routing"`, `kind` (`no-via`, `rf-corridor`, or `controlled-impedance`),
nonempty `pairs`, nonempty `requirement`, and `source`. Every pair has exactly
`from` and `to`, using the single-leg endpoint format; native pad identity, net
and centroid must resolve before placement. This binds the future evidence to
specific terminals without pretending that unrouted pads prove a routed path.
The source has `path`, `sha256`, `locator`, and `quote`; its bytes and quote must
verify against `relationships.source_records`. The contract hash binds the
whole declaration. Both planning and saved reports repeat each row as `pending`
with required final evidence:

| Kind | Required routed evidence |
|---|---|
| `no-via` | Final native connectivity and terminal-path via count |
| `rf-corridor` | Final native connectivity and RF geometry |
| `controlled-impedance` | Final native connectivity, RF geometry and impedance qualification |

These rows never accept an asserted `status: pass`. Untyped eligibility
`material_unknowns` remain blocking; the producer must explicitly migrate only
identified routing clauses, preserving their source quotes and identities.
For the 485 A2 packet, the eight no-via clauses and three RF corridor/50-ohm
records remain routing obligations, not waived placement constraints.

The remaining implementation boundary is the routing-stage bridge. Existing
`routing_rules` supports a net-wide `via_count` with `max_count: 0`; it does not
select a path between two declared terminals. Applying that rule to an entire
shared supply net would silently expand a path-scoped requirement. No terminal-
path no-via checker or automatic discharge of this array exists. The existing
`rf_geometry` check can measure routed topology/CPW/corridor geometry when given
its captured `agent-ee.rf-geometry/1` declaration, but it does not consume this
array and explicitly does not qualify impedance. After placement, the next
stage must bind terminal paths and RF declarations to the final routed board,
run native connectivity/via and RF geometry checks, and obtain separate
impedance qualification evidence for controlled-impedance rows. Pending records
must remain pending until that evidence is implemented and linked; a placement
`pass` cannot serve as that evidence.

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

## Strict prepared placement jobs (ZA-05 source slice)

A normal `place run` config may supply `inputs.block_contract` with `path` and
`sha256` for this existing contract. Build-plan validation also admits a captured
file path as `placement.block_contract`. This is admission only: its allocation
and eligibility must already name the exact pre-placement native board hash.
That board is generated inside a build run, and this slice does not generate or
bind a contract against that in-run identity. An earlier run is usable only if
the resulting pre-placement board bytes match exactly; this is not established
by the CPU tests. ZA-02/03b owns the generated-board binding follow-up. No strict
native build is demonstrated here. Omitting this input preserves the historical
narrower path; a historical result is not strict block acceptance.

Capture binds the contract to the original board, the complete allocation,
normalized groups, effective locality document and checking-policy hashes.
Membership and member anchors must match the prepared groups exactly. The
current bridge supports group member anchors; fixed-interface and centroid
anchors remain unsupported here. It does not invent numeric relationships or
translate qualitative groups into arbitrary distance limits.

Native fixed copper marks and silk-only graphics use explicit `uuid:UUID`
references in allocation and eligibility, including when their native labels
are duplicated or empty. This is a report projection keyed by the native UUID;
it does not rename footprints in the source board. These objects still require
an explicit fixed/shared allocation. Duplicate circuit references still refuse.
Logical fixed roles grant no new pose authority: the bridge separately carries
native locks and the prepared job's actual fixed-pose roles.

After materialization and after final save/refill, the job's existing bounded
native-read seam reloads the board and delegates to `block_contract.judge`.
The result binds the final board, original capture, contract, groups and policy.
Defect or unknown blocks promotion and final eligibility for the requested
strict path. Advisory-only `info` keeps its existing meaning; it is not an
independent qualitative engineering judgment. Missing pin coverage remains
unknown. A later board change invalidates the saved result.

This remains a source/CPU-tested bridge. Native calibration and the real public
control/mutation remain required. The placement report cannot qualify different
artwork bytes. Plans without a strict declaration keep their existing file-only
package behavior.

## Boardless source producer (Slice A; diagnostic only)

A build plan may instead capture `placement.block_source`, mutually exclusive
with `placement.block_contract`. Its outer schema is
`agent-ee.block-contract-source/1`: the same allocation/eligibility fragments,
relationships, templates, thickness and optional routing obligations, except
`allocation.board_sha256` and `eligibility.sources.board_sha256` must be absent.
Keep `eligibility.sources` explicit. Source authority and every authored role,
member anchor and physical pad endpoint remain explicit; this producer does not
compile a brief, guess native membership or migrate an old native contract.

After the last successful schematic step, a fixed receipted producer inserts
only those two observed native identity leaves into a NEW native contract.
It retains exact original board/project/present rules bytes, records rules
absence, and uses the normal normalized intent, groups and uniquely declared
locality file. The existing bounded capture child returns the prepared binding.
All outputs are individually declared regular files in
`build-generated/block-contract/`; captured inputs cannot occupy that namespace.
The producer publishes derivation evidence only after native capture and
unchanged-input/policy checks succeed. The fixed entrypoint reuses the existing
outer-stage filesystem witness before its supervised Host capture child.

Before placement the completed schematic receipt chain, fixed tool source,
original bytes, source, effective policy and derived outputs must verify. The
normal placement job recaptures the same strict contract; its retained binding
is compared before routed output adoption. Repeated fixed/silk labels keep the
UUID projection; no native labels are edited. Source/runtime changes refuse
resume, and a partial producer is not a completed stage.

The authored requirement map and baseline stay unchanged: this producer does not
add a check criterion or upgrade captured coverage. Diagnostic checkpoints remain
`accepted:false`. Package release additionally requires the stage-owned final
saved-board proof below, plus every existing manufacturing, review and judgment
gate. A previously completed artwork stage without that proof cannot resume to
strict package release; start a new captured run under the implemented source.

## Final artwork and package binding

After either successful artwork branch (`repaired` or `no_movable_fields`), the
stage calls the existing bounded fresh `placement_job.check_block_contract`
reader on the actual saved board. It retains `block-contract-final.json` beside
the artwork candidate, including the actual saved capture, full evaluator report and its advisories,
original-basis identities, checking policy and exact final candidate inventory.
Defects or required unknowns fail the stage; there is no separate acceptance
boolean or substitute geometry predicate.

Original normalization is reconstructed from the sealed schematic candidate,
with the original board/project/present rules or explicit rules absence and
captured intent/groups/locality/contract pins checked against placement config.
The config board path may now contain routed bytes and is never read as the
original. For boardless source, the fixed schematic producer and adoption chain
are independently verified against that schematic candidate; its binding must
equal the sealed placement capture. Native-bound declarations retain their exact
original board identity. Complete role allocation, fixed UUID aliases, effective
locality and policy use the existing binding validator unchanged.

Package checks the completed artwork proof before freezing judgments and again
at production, then binds it to the reviewed board and accepted-package marker.
The fresh-reader parent and retained verification replay the existing grade
function on those saved facts and require exact report equality, including complete
membership, roles, blocks, measurements, fixed poses and advisories. Matching hashes
and a passing status cannot substitute for coverage. Standalone callers retain
the existing report format unless they explicitly request the retained capture.
Qualification/resume independently replay the original basis, proof structure,
policy, declaration bytes, full final candidate custody, marker and reviewed
board identity. Missing, incomplete, stale or wrong-board evidence refuses strict
release. The proof is linked in file-release evidence so advisories remain
inspectable. Unsupported placement inputs that cannot reproduce normalization
(such as a separately supplied group-rails input) refuse explicitly. Standalone
package production without its retained run also refuses. Review profiles,
manufacturing coverage and engineering judgment requirements remain separate.

CPU controls establish these source/custody contracts only. Native feasibility,
actual saved-coordinate grading, package operation, ZA-05/M1/G4 and manufacturing
or engineering acceptance still require independent native evidence.
