# Declared planes through the ordinary workflow, 2026-10-24

The normal `agent-ee build run` and `agent-ee place run` entrypoints now carry a
captured plane plan through native production, routing, refill and independent
saved-board verification. Previously the public placement command silently
ignored an `inputs.planes` declaration and could report completion without any
zone. The retained red test reproduces that omission.

The correction reuses `add_planes` and `final_planes`. Each routing attempt owns
its copy of the selected placement and its generated zones. The immutable plan
selects deterministic zone UUIDs; only those zones may differ during protected
native-content comparison. Footprints, pads, poses, outline, project rules and
other native content retain their existing checks. Producer and verifier child
processes use the same cancellation/deadline owner as the router. Plane tool
identity participates in resume identity. Unknown placement input names and
ambiguous duplicate JSON keys are refused.

`placement.planes` carries the plan through the build. The final captured DRC
scope binds it with `plane_plans: [{path, sha256}]`. Existing campaign
producer/composition bindings remain mandatory when those reports are captured;
a direct plan cannot hide them. No native file is substituted by hand.

## Native exercises and strict gate

In `agent-ee:exec-b5ff7ad833e5ea01`, KiCad 10.0.7 and kicad-route
`80e144437b48a3de77b226d0e9465c16b9ab8b7e`:

| Exercise | Observed result |
| --- | --- |
| Original public job with declared outer plane | Red: reported completion but omitted the zone |
| Corrected two-layer public job | Completes; actual router input and final saved board have the declared filled F.Cu GND zone; zero opens and preserved native identities |
| Corrected four-layer public job | Completes; actual router input and final saved board have the declared filled In1.Cu GND zone; zero opens and preserved native identities |
| Declared polygon entirely outside the board | Public job fails; no routed incumbent promoted; source bytes unchanged |
| Duplicate JSON plan keys | Captured binding refuses the ambiguity |
| Legacy plane production, deferred connectivity and final consumer | Existing positive and disconnected/empty/wrong-net/missing/policy-change negative cases pass |
| Ordinary placement, parts, native build and interruption/resumption | Relevant regressions pass, including unchanged retained esp_prog capture |

The final strict gate ran **111 tests in nine modules, no skips**. Earlier
individual runs are retained separately; their counts are not added to the
strict total. Documentation and origins checks pass. This was the relevant
integration gate, not a claim that every project test or whole-system acceptance
criterion has passed.

```sh
ae-ci-exec . -- env PYTHONPATH=src:tests \
  AGENT_EE_ESP_PROG_PREPARED=/opt/agent-ee/build/plane-workflow/esp-prog-prepared \
  python3 scripts/strict_suite.py --jobs 3 \
  test_placement_job test_placement_job_planes test_placement_job_guards \
  test_placement_job_refusal test_add_planes test_final_planes \
  test_final_plane_binding test_late_plane_binding test_parts_stage
```

## Multiblock acquisition failure and bounded diagnosis

The original sourced manufacturing input was copied with its specification,
parts, pin mapping, placement constraints and unresolved requirements intact.
An explicitly recorded agent selection added F.Cu and B.Cu GND pours, with
0.2 mm clearance/minimum width and 0.25 mm thermal gap/spoke width. These were
engineering defaults, not owner-authored requirements.

The ordinary build completed requirements, parts, scaffold and schematic, then
correctly failed at placement/routing. Three eligible placements were tried
inside a 900 s total budget; the job finished after 515.111 s:

| Placement seed | Final opens | Native thermal errors | Job result |
| --- | ---: | ---: | --- |
| 6 | 2 | 1 | Rejected |
| 9 | 1 | 1 | Rejected |
| 4 | 2 | 2 | Rejected |

Seed 6's initial filled planes had no native errors. Routing disconnected GND
and cut thermal relief at the ADC. Seeds 9 and 4 also had an incomplete thermal
relief before routing. Plan geometry/fill checks alone did not establish clean
DRC; the complete final native checks rejected every candidate. No manufacturing
package was produced from this failed build.

Two matched-input diagnostic retries used the existing `--reserve-spokes`:
39/39 temporary corridors were reserved/removed for seed 9, and 40/40 for seed 6.
They preserved ground connectivity but left other nets open. Seed 6's saved
candidate had no native error-severity violations but still had two opens and
new warning-severity dangling copper; it remained rejected. Enabling owned
rip-up on the verified track-free seed 6 input did not improve this outcome.
Neither experimental policy was adopted automatically. No rule or severity was
relaxed. A Freerouting diagnostic did not execute because that backend is absent
from the supported image; it cannot currently serve as a fallback there.

## Fresh bottom-plane build and package checks

A subsequent captured engineering selection uses a B.Cu GND plane, retaining
front-side escape space for the ADC. It removes no original requirement and does
not discharge the failed two-sided fine-pitch plane case. All six ordinary build
stages complete. Seed 6 routes in a 291.585 s whole placement job: 105 segments,
six vias, 175.1355 mm conductor length, zero opens, zero native errors and zero
via centers inside SMD pads. These are observations on different engineering
inputs, not matched optimization or superiority claims.

The final artwork PCB is
`7eeedc50a26a4de7c86d11482802eca4719eec14ae4edc74b8d50704efb33606`.
Independent review candidate:
`0b3a494a6c5332e0bea267b1fc90f72f2639510bd68b690b34792f089c3ad7c9`.
All 20 declared checks execute: 19 return `ok/pass`, and native netlist export
returns its prerequisite `ok/info`. Final DRC consumes the captured B.Cu plan;
all three fixed anchors, eight hard pin-locality constraints, containment,
courtyards, copper geometry, selected fabrication limits and SMD-via policy pass.
Evidence verification and the checks-only gate both return zero. Checks-only
review returns one, as expected for incomplete full review/acceptance.

The diagnostic fabrication package has nine layer plots, two drill files,
12 BOM rows and 17 placements. Independent consistency checks bind 17 files and
eight sealed artifacts to the exact final board (seal prefix `c2df3dc49021`).
Supplier-specific CPL rotation approval, supplier preview and physical tests
have not occurred. Default package resumption correctly refuses all eleven
unresolved original electrical, mechanical and delivery requirements.

A separate review copy with C1's CPL X coordinate shifted by 1 mm returns
`verify` exit two: prior acceptance is stale. The positive review is unchanged.
Native KiCad top, bottom and isometric renders complete for this exact final
board, with all 17 models resolved, empty native stderr logs and unchanged
source hashes. The images are previews, not physical-fit or bench evidence.

```sh
ae-ci-exec . -- bin/agent-ee build run \
  build/plane-workflow/acquisition-bottom-build.json \
  --out build/plane-workflow/acquisition-bottom-build-native --through artwork

# Rebind the generated scaffold scope's input selector to this build's actual
# artwork candidate, using build_workflow._scope; then review that same candidate.
ae-ci-exec . -- env PYTHONPATH=src:tests \
  python3 build/plane-workflow/review-acquisition-bottom.py

# Expected refusal while the original eleven engineering obligations remain open.
ae-ci-exec . -- bin/agent-ee build run \
  build/plane-workflow/acquisition-bottom-build.json \
  --out build/plane-workflow/acquisition-bottom-build-native --resume

ae-ci-exec . -- bin/agent-ee render3d \
  build/plane-workflow/acquisition-bottom-final-review \
  --out build/plane-workflow/acquisition-bottom-renders \
  --models build/plane-workflow/models-pinned \
  --width 1600 --height 1200 --jobs 2 --timeout 240
```

## Limits and next acceptance

The observed KRT map is pinned at `d24a7602012b96c5cfa610ea05a171646ad601bd`
([base matrix](../research/krt-2026-10-24/krt-latest-capability-map.md),
[observed revision delta](../research/krt-2026-10-24/d24a760/delta.md)). This
iteration supplies the following agent-ee evidence, without claiming parity:

| Mapped capability | Agent-ee exercise and remaining gap |
| --- | --- |
| W03 plane creation/replacement | `test_placement_job_planes` exercises normal-path outer/inner plane creation and saved refill. Existing-zone placement and changed-plan replacement remain unqualified. |
| R11 / W05 reconnect and disconnected-region repair | Four-layer inner-plane job connects its initially deferred plane. Native bad-plane negatives reject. Actual two-sided fine-pitch acquisition repair fails and remains open. |
| W04 multiple plane nets on a layer | Existing producer can declare multiple zones; this ordinary-workflow qualification does not exercise different nets sharing one layer. |
| W06–W08 thermal arrays, stitching and return vias | Not established by a filled plane or by the diagnostic spoke-reservation attempts; targeted native qualification remains required. |
| W10 plane/power-path calculation | Existing ideal-copper `dc_path/2` rejects zones; plan production does not extend that electrical model. |

Existing zones/keepouts on the placement input are still refused; this path
creates declared zones after placement. Final planes require island removal
`always`. Fine-pitch outer-plane routing/repair, broader power/return-path cases,
zone-aware electrical copper bounds and the remaining KRT suite still need
qualification. The acquisition circuit's eleven original electrical,
mechanical and delivery obligations remain unresolved. No complete design or
manufacturing package is accepted by this change, and no comparative superiority
is claimed.

The bounded implementation decision follows Pincite packet
`pkt-cabb87aa9a92807c`, content SHA-256
`cabb87aa9a92807c7ba1e7af7971e0fe205736e80522733b1f8fc9adc126d386`.
Five typed evidence records, the decision receipt and eleven explicitly
nonmaterial obligations are retained with the evidence. The packet supports
reuse and preservation of the existing lifecycle; it does not grant authority
or establish whole-system acceptance.
