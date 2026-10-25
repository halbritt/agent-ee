# Native routing feedback for fine-pitch ground planes

Normal two-sided and four-layer jobs pass, and the complete required pin
regression passes. This is a routing capability milestone; it does not
replace or expand the accepted acquisition manufacturing package.

## Defect and correction

The retained acquisition variant requires ground pours on both copper layers.
Its original seed-6 input contains no tracks or vias. The former `protect existing`
policy disabled even the router's generated-copper rip-up. Allowing owned copper
to move completed every net, but native KiCad reported one missing thermal spoke
at U1.2. Reserving every thermal pad had previously obstructed two signal nets.

The public router now accepts repeatable `--reserve-spoke-pad UUID@LAYER` options
(kicad-route `f261c793445a3d40a7a5cb35cffeb66b35951d0b`). It uses the existing native
spoke planner and temporary corridor reservation, validates exact identities,
and removes temporary geometry before unchanged native grading. Default
all-pad behavior is unchanged.

The placement job proves its staged input is track-free before allowing its own
generated copper to move. For plane jobs, a new, attributed thermal violation
may trigger one retry from the original placement with targeted corridors. A
remaining dangling track may trigger one public reroute of the identified net,
only after a pair-free routing result and otherwise preserved, fully connected
board. Each strategy runs at most once within the candidate's remaining budget.
The original rules, components, placement and plane plan stay authoritative.
All trial results remain available. Native router rejection cannot produce a
completed placement job.

## Retained evidence

Initial experiments are under `build/two-sided-plane` in the main agent-ee
checkout. Their source input SHA-256 is
`e7788efe22703efc2f4970275926cfad73121a396f2a19eaf935f809730f0985`.
Both planes use 0.2 mm clearance/minimum width, 0.25 mm thermal gap/spoke width
and the unchanged minimum of two spokes. Track grid is 0.025 mm.

| Attempt | Result |
| --- | --- |
| Original protected-copper route | Incomplete route and native thermal failure; retained unchanged |
| `owned-no-spokes` | All nets connected; U1.2 has one of two required F.Cu spokes |
| `targeted-spokes-v2` | All nets and thermals pass; one new 0.035 mm dangling power-track warning |
| `dangling-net-reroute-v2` | Scoped public reroute resolves that warning; zero opens, all preservation checks pass; 18 pre-existing silkscreen findings remain |

The final diagnostic board is
`eb7137fd69a0f227da1e0e881155d2419f93d4bbd41226563e5dec3d6fc9da96`.
These diagnostic commands establish the proposed recovery mechanism. They do
not substitute for qualification of automatic recovery through the public job.

Eight native spoke regressions pass, including default builtin routing,
selection, native reservation/strip and exact unknown-identity refusal. Five
routing-option replay checks pass. Three Freerouting-dependent cases did not
run because that backend is absent from the supported native image. There is
no Freerouting equivalence claim. The first broader regression invocation
could not import pytest; its corrected, isolated test environment and complete
log are retained as `spokes-native-regression-v2.log`.

Public job inputs and results are retained under `build/plane-feedback`; the
six original seeds and engineering constraints are unchanged. The first 900-second
job failed: seed 6 completed thermal routing but exhausted its candidate share
before native grading; seeds 9 and 4 retained opens. The bounded second job uses
an explicitly agent-selected 1,200-second budget, recorded in
`budget-decision.json`:

```sh
ae-ci-exec . -- bin/agent-ee place run build/plane-feedback/place-1200.json \
  --out build/plane-feedback/public-job-1200
```

The second public job completes in 576.922 seconds, with 237.632 seconds in
routing and its two automatic retries. It reproduces the original seed-6 input
hash, then independently verifies the final board after all repairs:
`5d1164e05801aed5f8aea5b174bd495df243ee6a96640329176d5c093e8ef448`.
Both plane checks pass, every declared locality/anchor check passes, protected
native content is unchanged, and there are zero opens or native errors.
Native measurements: 82 track segments, seven vias, 135.9763 mm summed track
length and no via centre inside a pad. These are observations, not optimization
or superiority claims. The standalone placement check still reports 18
pre-existing silkscreen findings and 17 footprint-library findings; this case
is not an accepted manufacturing package.

The accepted acquisition regression also completed all seven ordinary build
stages under the changed pin and policy. Its fresh PCB is
`10c7b9c167f2f715f2c921af3076db01f44340b0767ca004dfc668a94371240f`;
the bound package artifact ID is
`45a2bf7e1a31460b77986fecffe712a1a2475a3426f0cc2e09e0a294ae5531aa`.
All 38 checks execute: 37 pass and the native netlist is informational. Final
native ERC/DRC have zero findings/opens under unchanged authored severities.
Native and export receipt hashes were independently read back. The checks-only
review record explicitly retains its generic incomplete state: independent
agent reviews/adjudication, physical validation and supplier preview did not
occur. Software package acceptance uses the mandatory engineering criteria;
those unperformed activities are not claimed. See the full per-check record in
`acquisition-regression-summary.json` and the unchanged acquisition engineering
review and operating contract.

The required four-layer inner-plane regression exposed an older acceptance
defect: the router completed all nets but emitted two 0.50 mm vias below
KiCad's implicit 0.60 mm Default class. Its project intentionally contains `{}`.
The old placement job overlooked this wrapper rejection; the new gate correctly
reports `verification_failed`. The retained reproduction is
`inner-plane-diagnostic-v2`; unchanged rules and zero native DRC errors alone
did not satisfy the net-class preservation requirement.

kicad-route `e69ec315403ef8fb2de24f2b3074098aa007006c` corrects the fallback
diameter to 0.60 mm. A regression against the actual KiCad API fails before the
correction and passes afterwards. All 42 configuration/default tests then pass,
including the actual backend emitter; the native rule-floor exercise also passes.
Rerouting the exact retained four-layer placement through the public router
produces zero opens and all preservation checks pass, PCB
`ddf6b75c5a66250072a361525bd2e4335e0ebd3c2f48874f713e4a3a78572cef`.
Evidence is in the kicad-route checkout's `build/native-default-vias`.

The two-sided positive and acquisition regression already resolved explicit
0.60/0.30 mm vias, as their effective-configuration records show. They do not
use the changed fallback; their accepted evidence is retained without another
unchanged full build. The final agent-ee pin is `e69ec31`. The normal four-layer
public placement job now completes in 44.762 seconds, with zero opens/findings,
preserved native identity and a passing saved inner-plane check. Its PCB is
`af16a58ada96e30d68beba77c8a264f7ac6ce975f272459bf38bc73cab37a308`;
see `inner-plane-qualified-summary.json`. This small routing fixture allows
vias in pads and does not claim assembly acceptance. The complete required pin
gate passes: 44 modules, 945 tests, no skips. The final image is
`agent-ee:exec-37b6d3e631ea3bb5`, ID
`sha256:6357243f06fc6283ded04617e9e716164b81660f195f41d28b34c290a8ba87d6`.
Its installed engine manifest and native tool versions pass offline verification.
The two/four-layer plane tests pass again within this final gate. See
`final-pin-summary.md`, `final-pin-suite.log` and `final-runtime.json`.

The first 44-module pin gate also found absent external ESP/K91 fixtures and
two unexercised source-mount builders. Exact fixture copies restore the existing
18-test ESP and 64-test boundary modules without skips. The source test now
constructs both actual builders and verifies their snapshot mounts; all 27 tests
pass. Initial failures/skips remain recorded and are not counted as passes.
The original delivered acquisition package remains unchanged.

## Matched stock-KRT routing exercise

`build/plane-feedback/matched-plane` runs the production agent-ee routing
function and stock KRT `d24a7602012b96c5cfa610ea05a171646ad601bd` on the
same saved placement/planes (`e7788efe…`), project, 0.025 mm grid and 300-second
routing budget. Both preserve the original rules, forbid automatic rule
relaxation/component movement and use the same native image and Python packages.
Stock's compiled Rust version is 0.23.7; its complete crate source matches the
previous retained build. The owned version is 0.23.1 at `e69ec31`. This isolates
routing behavior; the separate public-job success above establishes integration.

| Saved-output observation | Stock KRT | Agent-ee feedback |
| --- | --- | --- |
| Open connections | 0 | 0 |
| Native errors | 1 starved thermal | 0 |
| Final placement/locality and native identity | Pass | Pass |
| Native project/geometry preservation | Pass | Pass |
| Tracks / vias | 88 / 6 | 82 / 7 |
| Summed track length | 137.195 mm | 135.976 mm |
| Observed routing elapsed time | 74.491 s | 217.831 s |
| Manual board changes | 0 | 0 |

Stock exits zero, but independent native inspection rejects its missing thermal
spoke. Agent-ee automatically reserves the affected corridors and reroutes the
remaining dangling net; the final candidate passes those checks. This demonstrates
successful recovery for this input. Shared CPU activity, different amounts of
internal native grading and one run per side preclude performance or reliability
superiority claims. Both standalone outputs retain the disclosed library/silk
warnings; neither is a manufacturing release.

Final stock PCB: `e736072c8e71e6c80e363a13f5851e6d27205580eb65020c5eb47527cfe7901d`.
Final agent-ee PCB: `f661ef8995480f63a05172de9abad875a84297d8b531a049b08bb32c5c91b23e`.
`summary.json` retains all measurements. The first comparison preservation grade
mistakenly counted auxiliary baseline reports as native project outputs; both
sides were regraded using exact native siblings, with original records and the
explicit harness correction retained. No routing or board repair was repeated
for that grading correction.
