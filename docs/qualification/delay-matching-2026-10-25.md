# Native propagation-delay matching qualification — 2026-10-25

Status: router integrated at `48f02187bef97971b724975fa178fa41c9aca8b2`;
normal agent-ee job/build-producer cases and all required consumer pin gates
pass. This change integrates that qualified router through agent-ee. This is a nominal timing qualification,
not a manufacturing acceptance for the test article. The accepted 17-part
acquisition package remains unchanged.

The existing group operation now accepts `tolerance_ps` instead of
`tolerance_mm`. The ordinary job carries authored native timing profiles through
placement, plane generation, routing and the final save/refill. It checks
original dielectric inputs before native defaults can fill omissions, and
independently grades every final useful path with native KiCad time-domain
`fromTo` length/skew constraints. The measurement report binds the PCB, project
and requirement. Missing coverage never passes.

The engine reuses its meander machinery. Two defects found on the actual layered
article were corrected: already-connected in-scope group members now set the
target, and delay estimation uses the signal's used via span with native copper
endpoint semantics. Profile rates replace heuristic dielectric defaults for this
operation. Existing valid geometry is preserved. An additional public regression
corrects rejection of valid whitespace around native dielectric values.

## Article and engineering basis

The native article is `kicad-route/fixtures/delay_matching`: two signal paths and
one ground connection on six layers, with fixed terminals and ground references
on In1.Cu/In3.Cu. One path starts unrouted on F.Cu. The other has 44 mm on In2.Cu,
two 2 mm F.Cu escapes and two through vias carrying the signal only between F.Cu
and In2.Cu. The normal agent-ee preparation declares reference planes as captured
plane intent, consumed by its existing plane producer. Input-pour placement is
still a separate limitation; no hidden repair enables this test.

Er=4 is an explicit agent-selected qualification assumption, not a supplier
specification. The stack is 1.6 mm including six 35 um copper layers and
0.2/0.2/0.59/0.2/0.2 mm dielectric layers. The fixture generator records the
calculation and native library dependencies. For a 0.25 mm outer trace above
0.2 mm dielectric, the stated uncoated microstrip approximation gives
5.739552911 ps/mm. Homogeneous stripline and the nominal via model give
6.671281904 ps/mm. The used via height is 0.4875 mm, giving a calculated reference
path delay of 322.999115 ps. The inactive impedance calculator target is zero;
no impedance or signal-integrity claim is made.

KiCad 10 profile values are in attoseconds/mm. Its native calculation uses the
actual used via height; internal endpoint copper contributes half thickness.
The profile and layer identifiers are resolved through the native net settings.
Older net-settings schema migration clears tuning-profile assignments, which
is detected as missing effective input rather than silently grading zero delay.

Primary sources: [KiCad 10 time-domain tuning](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html),
[KiCad 10.0.7 delay calculation](https://gitlab.com/kicad/code/kicad/-/blob/10.0.7/pcbnew/length_delay_calculation/tuning_profile_parameters_user_defined.cpp),
[stackup distance](https://gitlab.com/kicad/code/kicad/-/blob/10.0.7/pcbnew/board_stackup_manager/board_stackup.cpp).
Exact retrieved source hashes are retained in `build/delay-matching/sources/source-identities.json`.

## Matched stock comparison

Current stock KRT was observed at
`e358107f6e4c24c6186e0c4dbc447dc8c3e6e9e8`. All 1,873 source files were captured;
its complete 14-file Rust tree matches the previously built stock tree, so the
verified existing binary was reused. Python source uses the new observed revision.
`matched-module-resolution.json` records the actual loaded binaries and proves
complete owned Rust source continuity with its build cache as well.
The comparison preserves identical native inputs, dependencies, routing flags,
0.1 mm grid, 3 mm meander amplitude, spacing of two track widths, and a declared
180-second budget per engine. Each engine gets its own input copy. Initial input
copies were unchanged after execution.

| Observed final property | Owned router | Stock KRT |
| --- | --- | --- |
| Connected paths | 2/2 | 2/2 |
| Native outer path delay | 323.78 ps | 365.90 ps |
| Native inner path delay | 323.00 ps | 323.00 ps |
| Skew against authored 1 ps limit | 0.78 ps, PASS | 42.90 ps, FAIL |
| Native DRC findings / opens | 0 / 0 | 1 timing finding / 0 |
| Track segments / vias | 14 / 2 | 17 / 2 |
| Total planar copper length | 104.412 mm | 111.751 mm |

Both use the same fixed placement and zero manual route intervention. Stock's
route fails the mandatory timing requirement; geometry measurements are not
used to promote that route to acceptance.

This demonstrates better timing correctness on this article. There is one timed
run per engine, with unrelated local build activity, so no speed or reliability
superiority is claimed. Raw elapsed times, input hashes, commands, environment,
original stock output and native findings are retained. The first comparison
retains an inactive calculator target of 50; the final comparison uses the
corrected zero target and reproduces the timing result. Final grading uses
isolated runtime state so a retained, operator-selected old generation in the
shared checkout cannot select a different checker implementation. No shared
runtime setting was changed.

## Executed checks and evidence

Router integration closes 177 distinct focused cases, followed by all 13 native
delay cases after the whitespace repair, with zero skips. The original failed
probes and repairs are retained. The five new consumer cases pass in a clean
image through the public job and existing build producer: positive timing and
planes, connected timing rejection, original missing-dielectric rejection,
changed-requirement resume rejection, and native project/library preservation.
All 998 prior consumer cases in 52 modules pass, giving 1,003 distinct required
cases in 53 modules with the new timing cases, and zero skips. The test process
and container both exit zero. `consumer-gate-closure.json` checks the individual
case roster, final source identity and retained native outputs.
The standalone placement output retains five footprint-library warnings. The
complete build producer carries the local libraries and has zero native findings;
no warning severity is changed.

Evidence is under `build/delay-matching`:

- `comparison-final.json`, `final-owned`, `final-stock` and `final-*-native-v2.json`.
- `router-gate-closure.json`, `router-gates-v2.xml`, `whitespace-green.xml` and both router land logs.
- `consumer-focused.log`, `consumer-pin.log`, runtime build log and container exit evidence.
- Captured public job/build inputs and final native files in the consumer worktree's
  `build/delay-matching/public-focused`, archived at `build/delay-matching/public`.

The public final PCB is `8eb3acae6763c5b28b703a623ad5cd28e210f0a7380f42a330ad49258aede1f8`;
the complete build candidate is `bf18e6405bbe7901558c780642aa5e6039334d819053b704b74e667b5da0757b`.
Both bind the authored project at `c65ff37a7dbdaeac5ceb6d6ed0e1fc90435a4f1ed68480d3dbc0725561bbb78b`.

The new image is `agent-ee:exec-953a18267b1df721`, ID
`sha256:c22cd17f076cb52d445aac1cbeb4ef56a1be6a9ced2c13978f48eeeb1982c87e`.
Its clean build took 233 seconds. Native KiCad is 10.0.7; the owned router's Rust
crate reports 0.23.1, with numpy 2.5.4, scipy 1.18.1, shapely 2.2.0 and Pillow 13.0.0.

Reproduce the public feature cases in a fresh output location:

```sh
ae-ci-exec . -- env AGENT_EE_DELAY_EVIDENCE=/opt/agent-ee/build/delay-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_delay_groups
```

Profile values still require engineering substantiation against actual material,
width, reference geometry and process variation. This operation does not
calibrate them or prove signal integrity. KiCad does not refresh delays when the
stackup changes. Inter-pair/AC composition, package-delay qualification, broader
return-path behavior and other open capabilities remain in the acceptance map.
No package is accepted on this timing evidence alone.
