# Coupled pair-to-pair matching qualification — 2026-10-25

Status: router `13303bcf92b46428ee285bd73c8ac398d9b4a808` is integrated.
Five normal agent-ee placement/build cases and all required consumer pin gates
pass. This change integrates the qualified router through agent-ee. This is a routing capability qualification. The accepted 17-part
acquisition project and manufacturing package remain unchanged.

The existing `router.length_match` operation accepts groups containing both
members of at least two differential pairs. The router retains their centerlines
in one routing operation until group tuning finishes. It then independently
checks every useful P/N path in the saved native board. A connected route with
insufficient matching or violated native geometry is rejected.

A defect found during qualification affected heterogeneous pair classes:
initial routing used each pair's correct width and gap, but meander reconstruction
used the global defaults. Route results now retain the effective pair geometry,
and both group and intra-pair tuning use it. This repairs the shared operation;
the input board does not receive a manual copper substitution.

## Native article and requirement basis

`kicad-route/fixtures/inter_pair_matching` contains the editable four-layer PCB,
project, native rules, local footprint library and authoring script. Four fixed
terminal footprints form two functional blocks. One pair spans 50 mm and the
other 60 mm. The normal consumer cases capture these actual inputs and route
on F.Cu with a 0.025 mm grid.

The initial article uses 0.25 mm width and 0.35 mm gap. Its heterogeneous variant
uses a second native class with 0.30 mm width and gap. Native rules require those
widths, a 0.02 mm gap interval, 0.1 mm intra-pair skew, 0.15 mm group skew and
at most 3 mm uncoupled extent. The public operation additionally requires all
four useful paths within 0.1 mm, using at most 8 mm meander amplitude.

These are explicit agent-selected software qualification assumptions, without
a component, protocol or owner electrical requirement. The original arbitrary
1 mm uncoupled limit rejected the native meander: three bends produced 4.1666 mm
uncoupled extent; one produced 2.0833 mm. The retained scope correction adopts
3 mm, about 5% of the nominal 60 mm length. It does not assert physical signal
integrity. The original 1 mm rule remains a mandatory rejection case and is
never reported as passed. No other native geometry or skew requirement changed.

All four measured paths pass: A_P 59.8736 mm, A_N 59.8402 mm and both B paths
59.8125 mm. The span is 0.0611 mm. The complete build producer preserves the
native project, rules and footprint dependencies and has zero native findings
or opens. Standalone placement retains four library-configuration warnings;
the build includes those libraries and has none. This PCB-only article does not
exercise schematic parity or establish a manufacturable circuit.

## Matched stock comparison

Stock KRT revision `e358107f6e4c24c6186e0c4dbc447dc8c3e6e9e8` was observed
on 2026-10-25. Both engines use identical input bytes, dependencies, routing
flags, fixed placement, and a 180-second budget. Each operates on an isolated
copy. Original inputs remain unchanged. Source/binary identities and the prior
verified Rust source continuity are retained with the comparison.

| Final property | Owned router | Stock KRT |
| --- | --- | --- |
| Equal-class native findings / opens | 0 / 0 | 0 / 0 |
| Heterogeneous-class native findings / opens | 0 / 0 | 33 / 0 |
| Heterogeneous findings by type | None | 18 width, 14 gap, 1 uncoupled extent |
| Heterogeneous track segments / vias | 18 / 0 | 20 / 0 |
| Heterogeneous planar copper | 239.339 mm | 239.347 mm |
| Manual route intervention | None | None |

The heterogeneous case demonstrates better preservation of required native pair
geometry. One timed run per engine and article, with other local activity, does
not establish speed or reliability superiority. Equal-class geometry is equal.
An initial command used an owned-only flag absent upstream; that invocation is
retained, excluded from route comparisons, and the flag was removed from both
matched commands.

## Executed checks and reproduction

Router evidence closes 249 distinct cases without skips. The 240-case baseline
preceded the geometry repair; all 93 affected pair/group/delay cases ran after
it, including nine new native cases. The individual roster and source hashes
are retained. Only three verified help strings changed after that final gate.

Five consumer cases pass through the public placement command and actual build
producer: matching success, insufficient-room rejection despite connectivity,
strict authored coupling rejection, changed-requirement resume rejection, and
heterogeneous native geometry/library preservation. All prior 1,003 consumer cases in 53 modules pass, giving 1,008 distinct required
cases in 54 modules with the new cases, and zero skips. The test process and
container exit zero. `consumer-gate-closure.json` checks every individual case,
final source identity and native artifact; successful counts alone do not
establish acceptance.

Reproduce the new public cases with a fresh output directory:

```sh
ae-ci-exec . -- env AGENT_EE_INTER_PAIR_EVIDENCE=/opt/agent-ee/build/inter-pair-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_inter_pair
```

The clean runtime image is `agent-ee:exec-ae6de0276dff865a`, built in 250 seconds,
ID `sha256:9831643e8f7ff13f63ed99c8ac4e064fbef858356ea66b555b534f361fbc0c94`.
Native KiCad is 10.0.7. Matched backend dependencies are Python 3.12.3, numpy
2.5.4, scipy 1.18.1, shapely 2.2.0 and Pillow 13.0.0; compiled engine identities
and source continuity are recorded in `matched-module-resolution.json`.

Evidence under `build/inter-pair` includes original failed attempts,
`scope-correction.json`, `comparison-final.json`, `owned-hetero`, `stock-hetero`,
`router-gate-closure.json`, source archives, logs and public native artifacts.
The feature harness captures `inputs/job.json` and invokes the supported
`python3 -m agent_ee.cli place run INPUTS/job.json --out OUTPUT` command.
The build case calls the existing placement-routing stage with captured native
inputs; it is not an additional full intent-to-manufacturing qualification.

The public PCB is `1128cd46539e9b37e69fb982b59e02da93c491b1f757df19ad6ccf32d25056ff`;
the build PCB is `5e015e807d058696c73ea4e69a0a613f310b2d4b8febd4b5ddc1ed4390715546`.

## Remaining scope

Complete pairs are mandatory; partial or mixed single-ended/coupled groups
refuse. If group and intra-pair tuning are requested together, the backend's
shared tolerance and amplitude must agree. Differing solved impedance geometries
and coupled time-domain groups still refuse explicitly. Existing connected
geometry is preserved and contributes to the matching target; failure of one
new pair discards the complete batch. Ordinary sequential pair routing and
single-ended matching remain regression obligations.

AC-composed pairs, package delays, broader multilayer matching and physical
signal-integrity validation remain open. This result does not close those
capabilities or the overall engineering-to-manufacturing goal.
