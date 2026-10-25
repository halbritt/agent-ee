# Grouping implementation and preflight repair — chief verification

Chief agent-564 accepted the bounded implementation through `b2a21094` on
2026-10-25. AGENTEE-479 and M1 remain open. The original `esp_prog` dialect now
has an explicit adapter, signed pad-edge measurements, source-bound rail
classification, group-link objectives and bounded member refinement. Native
rules, original declarations, fixed identities and advisory default limits
remain distinct from hard acceptance constraints. The accepted design is
[the 479 contract](contracts/AGENTEE-479-group-intent-contract.md).

The final adapter/solver delivery was `abdaecf3`, rebased without conflicts onto
`e67d1cd9` as the stack ending at `05b086a4`. All implementation bytes stayed
identical; the origins register incorporated current main and one trailing
blank line was removed from a test. The old stack remains locally reachable
through `keep/ee479-abdaecf3`; its evidence is preserved outside the worktree.

## Corrected counterexamples and compatibility

The chief reproduced two defects at `dfc47810`: a modern input without pad
extents silently scored centre distances, and an explicit non-copper aperture
changed an anchor link. The corrected modern `/2` contract requires explicit,
valid two-axis extents on every pad. `[0, 0]` denotes an aperture and never joins
a copper-link candidate set. Empty endpoints produce no invented link or
integer sentinel. Original `/1` records retain labelled legacy centre scoring
and zero group-link weight; a modern record cannot silently borrow that mode.

Agent-566 independently inspected the correction and retained parity evidence
in Cairn result `d35d7d83-c1fa-415a-b59f-8527508e847d@1`. On the original
`build/agent545/retained-problem.json`, SHA-256
`2ccd682b4bf36ea2aa2d2dcc8bca05f1770cd5927df6eb67142ba70bbf626f11`,
the old and new binaries preserve every old numerical term, feasibility,
violation and pose at the captured pose and one perturbed pose. This is a
bounded compatibility check, not an exhaustive search or performance claim.
The saved comparator's aggregate equality flag is false because the new output
adds labelled diagnostics; the old numerical fields are equal.

## Chief verification and discovered baseline regression

The first native run at `05b086a4` executed 119 tests in eight modules with
no skips. Two failed: the chief harness omitted
`AGENT_EE_ESP_PROG_PREPARED`, and an existing intent-preflight test reached a
missing requirement-map file before refusing unsupported intent. The latter
also reproduced on main `e67d1cd9`; it was introduced by the qualification
integration, not by grouping. Both failed logs remain retained.

Separate commit `2f51dee3` restores intent validation before the map-schema
read, retaining source inventory and both validations before output creation.
No assertion or test was weakened. Chief commit `b2a21094` corrects its comment
to acknowledge the preceding source inventory read.

The chief then supplied the missing prepared fixture and passed 61 native tests
in five modules, with no skips: placement job, placement intent, qualification,
qualification matrix and build review. The 93 passing tests in the other six
modules from the first run remain applicable: all 13 grouping source/test/doc
hashes are unchanged. These are two separately identified runs, not a claim
that the initial 119-test run passed. Worker Rust verification separately
reports 114 passing tests; the chief exercised the packaged engine in the
native Python regressions. Documentation, origins and diff checks passed.

Both actual chief containers used image
`sha256:23c3ba34990094319b6f6cdfae99c47d31f0763d898e49a3c94b4302913f20c9`,
Python 3.12.3 and KiCad 10.0.7. The packaged engine SHA-256 was
`94c542444216ce3dab26c461820b7067bb9cd01c18ed6532e106430c7e65b918`.
Container identity, exact command, live admission-lease label, worktree mount,
nonce and runtime were checked before allowing each test command to run.

Durable evidence is under
`~/.cache/agent-evidence/agent-ee-564-integration/grouping-479-05b086a4/`
and `grouping-479-b2a21094/`. The latter contains `chief-verification.json`,
source/input pins, final native log/exit, runtime, container and documentation
checks. Original worker artifacts remain under
`~/.cache/agent-evidence/agent-ee-565/479-contract-dfc47810/` and `479-fix/`.

## Remaining delivery

Wire the already integrated `group_views.produce` helper into final checks,
binding views and metrics to the same final saved board and preserving the raw
input-board identity separately. Then verify the representative workflow and
its functional placement results against the accepted contract. This integration
does not establish improved placement on a full run, final artwork readiness,
manufacturing acceptance or M1 completion. Telemetry-pod remains held.
