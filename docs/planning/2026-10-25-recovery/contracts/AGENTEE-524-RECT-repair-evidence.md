# AGENTEE-524 RECT correspondence repair — 2026-10-25

Agent-569 implemented the scoped repair from
`5b714a6d8109ba277664f37e78d0548d5a006f0d`, following the
[landed investigation](AGENTEE-524-U1-reader-investigation.md).
Chief integration and independent acceptance remain pending.

`easyeda_footprint._pad` now accepts a RECT outline only when a one-to-one
correspondence with its four expected corners satisfies the existing `_near`
bound. It checks at most 24 permutations. The change removes lexicographic
pairing; it does not change tolerance, its inclusive boundary, declared output
geometry, source-error classification or any other shape path.

## Verification

* **Red:** the owned 25.4 nm corner-perturbation regression failed before the
  repair with `source_inconsistent/malformed`, as predicted.
* **Green:** the same real `parse_package` path passed after the repair.
  The final data-driven regression exercises all 24 point orders at all four
  quarter turns, for exact and perturbed outlines: 192 successful variants.
  It asserts declared centre/size/angle, full inventory and raw locators.
* Four additional variants preserve the exact inclusive 127 nm boundary,
  refuse the next decimal outside it, refuse 203.2 nm at a 200 nm bound and
  refuse a duplicated corner with a missing corner. These use owned geometry,
  no mocks and no supplier fixture. The supplier-parser class passes nine tests.
* **Supported native runtime:** both full affected modules passed through
  `ae-ci-exec` and the strict runner: `test_land_pattern` 27 tests,
  `test_cpl_rotation` 28 tests; **55 tests, no skips**. This is the full content
  of those two modules, not a full repository-suite result.

The native run used `AE_CI_WEIGHT=1 AE_CI_MAX_WAIT=120`, an outer 1800-second
limit, and `python3 scripts/strict_suite.py --jobs 1 --module-timeout 600
test_land_pattern test_cpl_rotation`. A metadata preamble verified the imported
reader hash and native tool identities before executing the runner. Cached image:
`agent-ee:exec-b446111f323d3d93`, ID
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`.
Its build-input label was recomputed from this worktree and matched.
Python 3.12.3 and KiCad/pcbnew 10.0.7 were used. The native and host reader hash
both equal `29031da374ae9c52148f9cae2c418e793f6115717b7af6d503c2d0dedfb9d78d`;
the tested test-file hash is
`2f8dc39422267c1885db51a24f179e0b5a0a9bd2e64ac2910f90969746b38d04`.

The initial native admission attempt inferred weight 12 and refused against the
host maximum 4, before tests ran. That failure is retained; the documented
weight override of 1 matches the actual single-worker runner. Native import
also printed three `PROPERTY_ENUM` diagnostics; the retained log includes them,
and both test modules exited zero without skips. No diagnostics were filtered
from the retained execution log.

## Private supplier replay and retained artifacts

The actual repaired reader parsed the investigation's exact C2862740 result at
the unchanged 200 nm bound. Both the complete response and exact result-slice
hashes were rechecked:

* Response: `ce47f305121ff5bf7311c52cf7a872932429c4b08429b3fa53f4296cf66cf972`.
* Result slice: `fe9f93d390fb07e1df75380447c0a6788dbeecbaf6010591ed87a25619b1c03f`.

Assertions establish five pads, 23 accounted records, zero holes, all retained
noncopper categories, unchanged raw pad locators and exactly declared
centre/size/rotation output. Raw supplier CAD remains private; nothing was fetched
again or published. Parsing is not a full U1 orientation assessment, and this
repair does not assess the other 16 acquisition placements.

Evidence directory:
`/home/halbritt/.cache/agent-verification/agent-ee-569-u1-rect-repair-20261025/`.
It retains `red.log`, `green-tracer.log`, `parser-green.log`,
`native-admission-refused.log`, `native-modules.log`, `runtime-binding.json`,
`replay.py`, `private-replay.json` and `private-replay.log`.
The original response remains in the investigation's private evidence directory.

Test Guard and AI failure-mode checks found no new mocks, copied matcher oracle,
swallowed exceptions, fallback or unrelated shape changes. The fixed-size
permutation check and hand-worked geometry variants keep this repair bounded.
The chief's next step is independent review/integration, followed separately by
the candidate-bound orientation/profile checks already required by AGENTEE-524.
