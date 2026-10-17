# Geometry kernel focused validation

This report covers the original mathematical fixtures for the
[geometry kernel](land-geometry.md), implemented from worktree baseline
`58418741fea997e32504f0acf44b154f2e5b97f7` on `impl/land-geometry`.
Before this change there was no `agent_ee.land_geometry` module. The new module
adds exact rational polygon Boolean areas, certified curved-set bounds, and
pin correspondence under an externally fixed registration. It has no native
parser or coordinator registration.

On 2026-10-17, host Python 3.12 passed all 18 focused tests:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p test_land_geometry.py -v
```

The final 18-test focused suite and documentation checker also passed with
`AGENT_EE_REQUIRE_TOOLCHAIN=1` in the existing pinned image
`sha256:3991dd6e9ef4a001fe6e8e7e19329885804219dd2f40a06b87f616f2097c9e77`
under Python 3.12.3. That run used an init process, no container network and a
read-only mount of this worktree. These tests use no native EDA tool.

The tests exercise public functions. Initial tracer tests failed because the
module, area-bound function and comparison function did not yet exist. A later
boundary test exposed arbitrary correspondence in an ambiguous all-conflicting
pin graph. The implementation now returns a proved conflict with an empty pair
list; that test passed after the correction. No existing tests were changed.
An independent budget probe then found that a final IoU ratio could exceed the
numeric bit limit even when its individual areas fit. A failing 67-bit boundary
test reproduced the 70-bit output. Both final ratio endpoints now pass through
the same numeric-size guard, and the new refusal test passes.

## Numerical observations

All underlying endpoints are rational records; decimals below are display-only
approximations and never decide the threshold.

| Owned control | Observed result |
| --- | --- |
| Two overlapping 2-by-2 rectangles, union 3-by-2 | Exact area 6 |
| Same union with a 1-by-1 aperture | Exact net area 5 |
| Centred unit square versus centred 2-by-2 square | Exact IoU 1/4; defect at threshold 1/4 because passing is strict |
| Square and slanted diamond | Exact intersection 7/2; exact union 5 |
| Fixed asymmetric three-pad layout with two numbers exchanged | Copper IoU 1; pin_conflict |
| Coincident pads permitting both agreeing and disagreeing bijections | pin_mapping_unavailable |
| Registered graph where greedy first choice blocks the only full match | Unique pairs (0,1) and (1,0), pin_agreement |

For unit-radius circles with centre distance one, an independent analytic
control uses intersection `2*pi/3 - sqrt(3)/2`. Its oracle uses a 50-place pi
interval and integer-square-root enclosure, independently of polygon generation.
The kernel's bounds contain that oracle interval at refinement 16.

| Refinement n | IoU lower, approximately | IoU upper, approximately | Decision at threshold 0.25 |
| --- | --- | --- | --- |
| 1 | 0.083333333 | 0.571428571 | unknown |
| 2 | 0.187969925 | 0.292753623 | unknown |
| 4 | 0.228339720 | 0.255055832 | unknown |
| 8 | 0.239249353 | 0.246019791 | defect |
| 16 | 0.242072607 | 0.243752775 | defect |

Independent capsule, rounded-rectangle and annulus area equations also lie
inside the reported bounds. Tests assert nested area/IoU bounds under refinement,
not just closeness to a floating reference. Malformed geometry, nonfinite or
floating inputs, unsupported shape/rotation, empty copper and every computational
budget boundary produce explicit refusals. Identical nonempty curved geometries
can establish IoU one through structural set identity without assigning a finite
polygon the exact analytic area of a curve.

## Limits of this evidence

These are owned synthetic mathematical controls. They establish neither native
KiCad shape interpretation nor supplier applicability. General decimal rotation
is explicitly unsupported. Native reload/front/back/drill controls, captured
C11702 observations, parser coverage and candidate-bound check/witness integration
remain outstanding for the full AGENTEE-31 ticket. Owner review and peer review
are separate from these test results. No third-party design fixtures or retained
native run artifacts are included in this report.
