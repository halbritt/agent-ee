# RF geometry

`rf_geometry` checks the routed copper of one declared RF net on the saved
board against a declared structure. It has three sections:

- **Grounded CPW**: track width, the gap to coplanar ground on the signal
  layer, and ground copper under the trace and both gaps on the reference
  layer. Targets may be read from a captured calculation record.
- **Stitching**: ground vias on both sides of each declared branch, with no gap
  along the branch larger than the declared pitch.
- **Branch topology and length**: an explicit trunk and named stubs. The routed
  copper must form exactly that tree, and each branch's routed length is
  checked against its own bound.

This is planar copper geometry on the candidate board. It is not RF
qualification. No field solver, TDR, VNA or bench measurement is claimed, and
no proxy (guard width, "90% image current") is graded. Impedance stays
unverified. A structure that is absent is reported as absent. It is never
graded as microstrip instead.

## Check declaration

```json
{"id": "rf", "kind": "rf_geometry", "params": {"declaration": "project/rf-geometry.json"}}
```

`params` must contain only `declaration`, the candidate-relative path to a
captured `agent-ee.rf-geometry/1` record. The declaration is an immutable input.
So are the calculation record named by `source.path`, which is bound to its
captured SHA-256, and the solver record named by
`electrical_length.effective_permittivity_source.path`. A source that is not a captured input refuses capture. Launch
preflight requires `pcbnew`.

The handler writes `rf-request.json` and runs this command in the sandbox
(600 s ceiling):

```
/usr/bin/python3 -B -m agent_ee.rf_geometry BOARD --request /work/rf-request.json --out /work/rf-native.json
```

The probe records facts only, with no judgment. It lists the RF net's tracks
(segments and arcs), RF pads with their per-layer copper polygons, RF and
ground vias, and RF-net zones. It also builds united pcbnew copper polygons,
clipped to the RF corridor plus a margin:

- ground copper on the signal layer;
- ground pours and tracks on the signal layer, without pads or vias;
- copper of every other net on the signal layer;
- ground copper on the reference layer.

It also lists unfilled zones and the board's `max_error`. The coordinator then
evaluates the native report in pure Python ([rf_geometry.py](../src/agent_ee/rf_geometry.py)).
It first checks that the report binds the candidate board by SHA-256. The
check keeps `rf-request.json`, `rf-native.json` and the candidate-bound
`rf-geometry.json`.

## Declaration

```json
{"schema": "agent-ee.rf-geometry/1", "net": "/POD_GNSS_RF", "ground_net": "GND",
 "signal_layer": "F.Cu", "reference_layer": "In1.Cu",
 "source": {"path": "project/rf-calculation.json", "unit": "mil",
            "width_pointer": "/geometry_mil/W1", "gap_pointer": "/geometry_mil/D1"},
 "cpw": {"width_tolerance_mm": 0.0127, "gap_tolerance_mm": 0.0254},
 "stitching": {"max_pitch_mm": 2.5, "band_mm": [0.5, 1.5]},
 "topology": {"trunk": {"from": "J2.1", "to": "U3.11", "max_length_mm": 30.0},
              "stubs": [{"name": "esd", "terminal": "D3.1", "max_length_mm": 3.5},
                        {"name": "bias", "terminal": "L3.2", "max_length_mm": 3.5}]},
 "electrical_length": {"effective_permittivity": 3.1, "frequency_hz": 1575420000,
                       "basis": "declared ErEff, reported only"},
 "basis": "free text"}
```

- `net`, `ground_net`, `signal_layer` and `reference_layer` are required. The
  nets must differ, and so must the layers. Layers are KiCad copper layer names.
  At least one of `cpw`, `stitching` or `topology` must be present.
- `cpw` requires `width_tolerance_mm` and `gap_tolerance_mm`, each a positive
  number or `null`. Targets are given either as `width_mm` and `gap_mm`, or
  through `source`, never both. `source` uses JSON pointers (RFC 6901) into the
  captured record. `unit` is `mm` (the default) or `mil`, converted by 0.0254.
  The AGENTEE-186 back-calculation's W1 = 13.166015625 mil and D1 = 8 mil
  resolve to 0.334416796875 mm and 0.2032 mm. Optional `sample_step_mm`
  (default 0.05) sets the edge-sample step. Optional `search_mm` (default 1.0)
  sets how far from the trace edge the check looks for a coplanar conductor.
- `topology` requires a `trunk` with `from` and `to` terminals and a `stubs`
  list. Terminals are written `REF.PAD`. Each stub has a unique `name`, a
  `terminal` and `max_length_mm`. Its optional `from` names an earlier branch
  (default `trunk`). No terminal may appear twice.
- `stitching` requires `topology`. It needs `max_pitch_mm` (positive or `null`)
  and `band_mm`, given as `[inner, outer]` offsets from the centreline with
  `0 <= inner < outer`. Optional `branches` limits the check to the named
  branches.
- `electrical_length` is reported only. Each branch gets an electrical length
  in degrees from the effective permittivity and `frequency_hz`. No verdict is
  drawn from it. It needs exactly one of:
  - `effective_permittivity`, a declared positive number;
  - `effective_permittivity_source`, a binding to a captured solver record:
    `{"path": ..., "sha256": ..., "pointer": ...}`. `path` must be a captured
    input. `sha256` is the record's declared SHA-256 (64 lowercase hex digits);
    if the captured bytes hash differently, capture refuses. `pointer` is an RFC
    6901 JSON pointer. A non-numeric or non-positive value refuses. A pointer
    that does not resolve, or resolves to `null`, makes the value unknown:
    `resolved.electrical_length` then has `effective_permittivity: null`,
    `status: "unknown"` and a reason, and every branch reports
    `electrical_length_deg: null`. Branch and check verdicts are unchanged
    either way.

  The pod's captured W2 solver record is
  [rf-w2-calc-4-result.json](../fixtures/telemetry-pod/rf-w2-calc-4-result.json).
  It is bound like this:

  ```json
  "electrical_length": {"effective_permittivity_source": {
                          "path": "project/rf-w2-calc-4-result.json",
                          "sha256": "d05e15b93f02b3cc12b9b967c093503c60a3c72338a2f35ddccd96d4998b89af",
                          "pointer": "/impedance_calc_result/dErEff"},
                        "frequency_hz": 1575420000, "basis": "captured W2 solver ErEff"}
  ```

  This resolves to 2.94025199985. It is the solver's modelled value for its own
  geometry, not a measurement of the routed board.

Coverage predicates are `rf-geometry:cpw-structure`, `rf-geometry:cpw-reference`,
`rf-geometry:cpw-width`, `rf-geometry:cpw-gap`, `rf-geometry:stitching-pitch`,
`rf-geometry:branch-topology` and `rf-geometry:branch-length:NAME`. A predicate
whose tolerance, pitch or bound is `null` is not offered, and that item reports
`unknown`.

## Evaluation

**CPW** (`sections.cpw.items`):

- `width`: every RF track on the signal layer must be within tolerance of the
  target, using exact native widths.
- `layer`: RF tracks off the signal layer are a defect.
- `structure`: each track side needs a ground pour or track on the signal layer
  within `search_mm` of some graded edge sample. Ground vias or pads alone do
  not count. A side with no such conductor gives `coplanar_ground_absent`, a
  defect stating that the structure is not grounded CPW there.
- `gap`: two measurements against `target ± tolerance`.
  - The minimum distance from each track to signal-layer ground copper is
    exact on pcbnew polygons, transitions included. Below the lower bound is
    `gap_below_tolerance`.
  - Points on both trace edges, one every `sample_step_mm`, are measured to the
    nearest ground copper. A point with no ground within `search_mm` is
    `coplanar_ground_missing`. A point farther than the upper bound is
    `gap_above_tolerance`. A point where other-net copper is nearer than ground
    is `foreign_copper_in_gap`.
  - Points within the gap of other RF-net copper are counted as transitions and
    exempt from the maximum-gap test. These are bends, junctions and pad or via
    launches.
  - A tolerance that does not exceed the native polygon error makes the gap
    `unknown`.
- `reference`: for every track segment, the strip of half-width
  `width/2 + gap` must lie wholly inside ground copper on the reference layer.
  This is exact convex containment. Holes give `reference_void`. No ground
  under any strip gives `reference_absent`.

**Topology** (`sections.topology`): tracks are split at T-junctions. Pieces are
joined at shared endpoints, pads and RF vias. Then each branch is routed by
shortest path: the trunk from terminal to terminal, and each stub from its
parent branch's path to its terminal. Arc length is the true arc. Via barrels
are counted (`via_transitions`), not measured. Each of these is a defect:

- an RF pad that no branch declares (`undeclared_terminal`);
- a declared terminal missing from the net (`terminal_absent_from_net`);
- copper shared by two branches (`branch_overlap`);
- copper outside every branch (`undeclared_copper`).

A branch longer than its bound is a defect, compared exactly in nanometres.
These are unknown:

- an unrouted or disconnected terminal;
- RF-net zone copper;
- a `null` bound.

**Stitching** (`sections.stitching`): only ground vias that span both the signal
and reference layers count. A via is projected onto the branch path. It counts
if its projection lies strictly inside the path and its distance falls within
`band_mm`. The cross product gives its side. On each side, the gaps between
consecutive vias, and from each path end, are compared with `max_pitch_mm`.
Each violating interval is located along the path.

## Outcome

The check outcome is the worst over sections, in the order defect, unknown,
pass. `unknown` uses execution `unsupported`.

- Unfilled zones on the signal layer make `structure` and `gap` unknown.
- Unfilled ground zones on the reference layer make `reference` unknown.
- A declared net absent from the board is unknown.
- A declared layer absent from the board makes the CPW structure a defect.

Each determinate defect gets a `gate_findings` identity:

| Defect | Entity | Kind |
| --- | --- | --- |
| CPW item | `NET:cpw` | `rf-geometry/cpw-ITEM` |
| Stitching branch | `NET:BRANCH` | `rf-geometry/stitching-pitch` |
| Branch length | `NET:BRANCH` | `rf-geometry/branch-length` |
| Topology finding | `NET:topology` | `rf-geometry/REASON` |

Unknowns are never findings and never pass. A crash, a missing native report
or a board hash mismatch is `malformed`.

## Limits

- Maximum gap is sampled at the declared step. Minimum gap and reference
  containment are exact within `max_error`.
- Stitching vias are graded by net, layer span and position. Zone connection is
  not checked, and the check does not judge which branch a via physically
  serves.
- Pad entry geometry is not graded for width.
- There is no impedance, loss or coupling calculation.

The check covers the CPW, stitching and branch-length paths that AGENTEE-257b
found without a native check. AGENTEE-258 and AGENTEE-259 (per-net layers and
RF wiring) are separate. Tests use owned generated boards
([rf_geometry_fixture.py](../tests/rf_geometry_fixture.py)) with compliant and
violating geometry.
