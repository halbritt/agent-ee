# Series-part differential matching qualification — 2026-10-25

Status: paused at the owner's request. Router
`c4af095ed07593b63d7886c139fcb7e5c2e774f4` is integrated and pushed, with
263 distinct required cases passing and no skips. The initial broad run lacked
Freerouting in the worktree; its affected case passed after restoring that
prerequisite. All initial failures remain in the evidence.

Consumer integration is committed as unfinished work. Its five new public/build
cases and the existing 54-module, 1,008-case pin gate have not executed: the image
build and queued gate were stopped on the owner's pause. This record does not
close the capability. The accepted 17-part acquisition project and manufacturing
package remain unchanged.

`router.ac_match` declares complete chains by their populated series-part
references, tolerance and allowed tuning amplitude. The normal placement command
and build placement producer carry this request. Native preflight checks the
actual pads, population and connectivity. Final verification independently
measures every useful terminal-to-terminal copper path in the emitted PCB and
sums the P and N contributions. A clean DRC or successful backend exit cannot
substitute for the composed-path requirement.

## Article and requirement basis

The native `kicad-route/fixtures/ac_matching` article contains four fixed
footprints on a four-layer board: two 2.54 mm terminal pairs and two populated
0402 capacitors. C1 connects DPA_P to DPB_P; C2 connects DPA_N to DPB_N.
Each named section spans approximately 30 mm. Routing uses F.Cu, native
0.15 mm width and 0.25 mm gap, a 0.2 mm local skew limit and a 4 mm terminal
escape limit. The composed copper-path skew must be at most 0.1 mm.

These are explicit agent-selected software qualification assumptions. They
do not assert a protocol, capacitor internal delay, component tolerances,
impedance, physical signal integrity or manufacturing acceptance. The exact
native dependencies, authoring script and original rules are retained.

## Defects and corrections

The previous matcher could not add the small missing length without overshooting:
its full folded meander has a minimum added length set by four chamfers and two
risers. The shared length tuner now uses a clearance-checked triangular bump for
corrections below that minimum. It preserves the declared amplitude and the
actual segment's width, layer and net.

A second native case showed that already-routed chain sections were omitted
from the tuning target. Existing copper now contributes its measured length
while remaining immutable; only new sections can receive meanders. Each
section's effective native width and gap also survive tuning. These corrections
operate in the shared routing implementation, without manually substituted
copper or fixture-specific geometry.

The public operation requires explicit chain references. Symmetric same-polarity
bridges and exactly two native terminals per segment must form one unambiguous
chain. Missing/DNP parts, crossed polarities, duplicate bridges, branches,
cycles, shared membership and undeclared two-pad continuations refuse. Backend
automatic detection is restricted to the declared chains.

The final checker appends measurement-only `fromTo` rules on an isolated copy,
retaining the authored rules. It requires one identifiable result for every
segment and rejects ignored/missing coverage or opens. Summed P/N skew plus the
conservative printed-rounding bound must meet the request. Native PCB, project
and rule hashes bind the report. Ordinary final DRC and preservation checks
remain separate mandatory conditions.

## Matched comparison

Stock KRT `e358107f6e4c24c6186e0c4dbc447dc8c3e6e9e8`, observed on
2026-10-25, and the repaired owned engine use identical native input bytes,
flags, dependency environment and a 180-second execution budget. The retained
stock observation is reused because its source, environment and inputs did not
change. Original inputs remain unchanged.

| Result | Repaired owned engine | Stock KRT |
| --- | --- | --- |
| Native findings / opens | 0 / 0 | 0 / 0 |
| Summed useful P path | 62.3307 mm | 62.3307 mm |
| Summed useful N path | 62.3307 mm | 62.2145 mm |
| Skew / rounding bound | 0.0000 / 0.0002 mm | 0.1162 / 0.0002 mm |
| Required 0.1 mm bound | PASS | FAIL |
| Segments / vias | 23 / 0 | 20 / 0 |
| Planar copper | 124.895685 mm | 124.779562 mm |
| Manual route intervention | None | None |

This demonstrates better compliance with this requested skew bound. One timed
run per engine does not establish runtime or reliability superiority. Stock's
zero exit and clean ordinary DRC do not detect its composed-path failure.

Evidence is retained under `build/ac-matching`, including the original failures,
`comparison-final.json`, independent path measurements and native projects.
The earlier projectless upstream example had width-rule failures; a partially
generated initial article was also invalid. Both are preserved and explicitly
excluded from qualification.

## Qualification and remaining scope

The qualification exercises public routing/revalidation, fully existing copper,
a partially routed chain, differing native pair classes, wrong polarity, DNP,
missing parts, repeated/unlisted bridges, ignored measurement checks, replay
and conflicting shared controls. Connected-but-unmatched output must reject.
Consumer cases additionally exercise the normal public placement command, real
build producer, captured libraries and changed-request resume refusal.

Reproduce consumer qualification with a fresh output directory:

```sh
ae-ci-exec . -- env AGENT_EE_AC_MATCHING_EVIDENCE=/opt/agent-ee/build/ac-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_ac_matching
```

The captured `inputs/job.json` is runnable with
`bin/agent-ee place run inputs/job.json --out OUTPUT`. This is a routing feature
article; it is not another full engineering-intent-to-manufacturing design.

AC, coupled-group and intra-pair matching share tolerance and amplitude controls;
combined requests must agree and all final requirements must pass independently.
Different solved impedance geometries, package/internal delay, broader
multilayer composition and physical validation remain open. The larger goal
also retains circuit automation, fine-pitch escape, mechanical/placement and
representative-suite obligations.
