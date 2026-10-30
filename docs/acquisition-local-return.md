# Acquisition local routing

`agent_ee.acquisition_local_routing.produce` prepares local copper and ground-strip
reservations inside an eligible acquisition placement. It preserves the original
board nodes, component poses, pads, library identifiers, copper, project and
custom rules. The captured acquisition mechanics recipe and plane plan determine
applicability. The helper requires KiCad 10 saved-board version 20260206, native
DRC version 10.0.7, and the captured 0.2 mm minimum clearance.

The producer connects both legs of four loops with 0.25 mm tracks:

| Loop | Signal or supply leg | Ground return | Combined limit |
| --- | --- | --- | --- |
| ADC | C3.1–U2.8 | C3.2–U2.3 | 10 mm |
| Regulator output | C2.1–U1.5 | C2.2–U1.2 | 10 mm |
| Filter 0 | C4.1–U2.4 | C4.2–U2.3 | 12 mm |
| Filter 1 | C5.1–U2.5 | C5.2–U2.3 | 12 mm |

Signal and supply legs precede shared ground returns so a ground crossbar cannot
close access to an adjacent signal pad. Filter 0 uses two 0.6 mm through-vias
with 0.3 mm drills and a B.Cu segment. Their positions derive from the native
capacitor pad pair, IC pad long axis, and signed U2.4-to-U2.5 pin pitch; no saved
board coordinates are imported. Unsupported or ambiguous pad frames refuse.
The producer tries at most two escapes, with fresh native DRC for each. First,
the capacitor exits perpendicular to its pad pair toward the IC. If that fails,
the second proposal follows the ray away from the capacitor's other pad to the
IC's existing outward escape rail. A parallel ray, nonforward intersection or
repeated vertex omits the second proposal. Both proposals keep the same IC
escape, via sizes and layer sequence. This bounded family is not a routability
guarantee; failure retains every attempted board and report.
The other legs remain on F.Cu. Each return receives only the loop's remaining
length allowance, including both vertical traversals of the actual board
thickness. Final engineering checks still measure the delivered board.

## Interface and custody

```python
from agent_ee.acquisition_local_routing import produce

seed = produce(
    accepted_placement_directory, new_evidence_directory,
    expected_inputs={
        'board.kicad_pcb': accepted_placement_board_sha256,
        'board.kicad_pro': captured_project_sha256,
        'board.kicad_dru': captured_rules_sha256_or_None,
    },
    mechanics_binding=captured_mechanics_binding,
    planes=captured_plane_plan_bytes,
    clock=existing_placement_clock,
    cancel_dir=existing_placement_job_directory,
)
route_input = seed['output']
```

The caller supplies hashes from accepted placement custody. It cannot recapture
changed bytes to approve a discrepancy. Source aliases, existing destinations,
and destinations inside the source refuse. Missing, ambiguous, wrong-net or
non-front-only endpoints refuse. `JobRefusal` retains attempted boards and raw
reports; callers must use a fresh evidence directory for a later authorized run.
`JobStopped` propagates the caller's deadline or cancellation.

`acquisition_local_return.produce_loops` owns the eight-leg custody chain.
Each leg binds its input and output hashes, helper hashes, native endpoint
identities, trial geometry, reports and execution receipts. Original native
nodes remain intact. An exact existing segment with the same net, layer, width
and undirected endpoints is reused with its original UUID; other segments get
new UUIDs. Native reload verifies every segment and via, including via type,
span, drill, diameter, net and centre. Source, reports and copied outputs are
checked again before adoption. Successful receipts use `status: seeded` and
`acceptance_assessed: false`.
Each via trial names its `perpendicular` or `ic-rail` proposal. Final replay
requires the exact corresponding plan regenerated from unchanged pad frames,
including layers, via centres, board thickness and vertical length. Unknown or
relabelled proposals refuse. Historical unnamed receipts must still match the
original perpendicular plan exactly.

Front-layer proposals include direct, orthogonal and diagonal paths plus bounded
rectangle and native convex-polygon visibility searches. Proposals are ordered
by length and checked against the remaining allowance before native DRC. The
polygon search refuses foreign front zones and nonconvex contours, fills holes
conservatively, and limits itself to 4,096 native items, 32,768 obstacle vertices,
2,048 search vertices and 25 million actual containment/crossing edge tests.
Rectangle search permits at most 1,024 boxes and 512 nodes. Search, native checks
and adoption share the caller's clock and CANCEL directory. These searches
propose paths; native DRC decides clearance legality.

The legacy `acquisition_local_return.produce` entry point retains its original
single C3.2–U2.3 front-layer operation and five endpoint-derived proposals.
Placement uses the complete local-routing producer above.

## Native checks

Each leg obtains a fresh native DRC baseline on a byte-identical private copy
of its current input, then checks every proposed board. Custom rules retain the
native rule-loading witness. The required report schema is
`https://schemas.kicad.org/drc.v1.json`, in millimetres, with all three severities:
error, warning and exclusion. Missing or malformed ignored-check metadata,
unknown ignored checks, errors, exclusions, conductive warnings and parity
findings refuse. Pre-route opens are retained and counted; final routing must
close them.

The project must have absent or empty `rule_severities` and `drc_exclusions`.
Custom rules cannot override severity. The producer never rewrites these settings.
Only `dc_rail.NONCONDUCTIVE_IGNORES` categories may be ignored, with these limits:

- Missing courtyards remain a separate placement/body/access obligation.
- Footprint filter/type metadata stays unchanged; schematic parity remains required.
- Via-centering preferences do not suppress clearance or connectivity checks.
  Native seed verification requires the two generated vias and adjoining segment
  endpoints at their exact declared centres. Vertical length is counted.
- Plain segments and through-vias do not introduce tuning profiles.

Apart from deferred artwork warnings, a baseline may contain only warning-level
`lib_footprint_issues` with the exact missing-library diagnostic. Every item must
bind the original footprint UUID, reference, position and library nickname.
Each trial must retain the identical complete-record multiset: changed, missing,
new or duplicated library records refuse. This does not repair the library setup.
Warning-only `silk_overlap` and `silk_over_copper` records are retained for the
required artwork stage. Their differences remain visible in the receipts.

Raw reports must be ordinary unaliased files. Their exact hashes, baseline
copies, selected trials and adopted copies are checked around native work and
before publication. A stale receipt cannot excuse changed report bytes.

## Ground strips and final validation

After the eight legs, the top-level producer derives B.Cu track/via reservations
from the captured geometry model's orthogonal ground strips. Each rectangular
strip includes the checker's 0.1 mm half-width plus the declared ground-plane
clearance. Duplicate rectangles are coalesced; at most 32 areas are emitted.
UUIDs bind the model hash and rectangle. The areas permit pads, footprints and
copper pours. Native DRC must still pass, and native reload verifies every area
name, UUID, layer, polygon and keepout flag. The receipt binds the exact model,
plane bytes, generated board and native report.

The mechanics declaration enables this producer automatically after eligible
placement. Preparation and checkpoint identities include the local-return,
geometry and local-routing helper hashes. Evidence is retained under
`local-routing-SUFFIX`. The producer consumes the candidate's existing equal
share of remaining job time; only the unspent share reaches global routing.
Refusal aborts without an unseeded fallback.

Global routing uses `preserve_existing` (`--protect existing`). Placement's
`final_checks` retains its original placement, rule and footprint comparisons.
Generated reservation UUIDs enter the protected-node comparison's ownership list
only after native verification bound to the original placement, mechanics and
plane bytes. Final refill and the other consumers are followed by another
verification of the delivered board. All local segments, vias, areas, receipts
and retained reports must survive. Native DRC, final planes, locality, block
relationships, mechanics and annotated views retain their separate checks.

A seeded board or eligible placement is not engineering acceptance. Fresh
judgments, full engineering review and the package release gate remain required.
