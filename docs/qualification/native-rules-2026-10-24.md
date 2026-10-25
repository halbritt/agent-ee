# Native rules through placement and paired routing

The public placement command and the full build's actual placement producer
now carry native netclasses and `.kicad_dru` files through materialization,
routing and final grading. This is a bounded routing qualification; the accepted
17-part acquisition manufacturing package remains unchanged.

## Corrections and their evidence

Previously, placement refused non-default netclasses and custom rules, while
later file transfers omitted `.kicad_dru`. The job now binds the same-stem
project/rules by SHA-256, preserves their bytes, and rejects changed or missing
routed companions before grading with the authored rules. The build derives
the rule binding from its preceding native output. A changed rule file cannot
resume an earlier job identity. Search uses the largest native class clearance
conservatively; actual conditional rules remain KiCad's responsibility.

The first new public article exposed an independent Rust panic when no component
was movable. A fully anchored board now retains its one unchanged placement,
with zero search moves, and receives the normal native judging/routing checks.
The Rust regression also checks the deadline path. No anchor was unlocked to
make the article run.

The malformed-rule negative then exposed a real acceptance defect: KiCad 10.0.7
returned zero and an empty violation list after discarding an unknown constraint.
The job now reuses the existing native routing checker’s rule-loading witness
on a disposable copy before search. A diagnostic track ensures this proof also
works on track-free inputs. Only that copy gains the witness and its severity
override. Its findings prove rule loading, not design correctness; the authored
project and all actual grading severities remain unchanged. The malformed input
now refuses before routing. Three further public-command probes also reject an
unclosed form, an unknown condition property and an invalid unit before routing
(`parser-variants/result.json`). Initial refusals, panic and false success are retained.

## Representative article and results

`tests/test_placement_job_rules.py` generates a four-layer software article from
the existing original CAN pair fixture: two fixed terminals 10 mm apart, one
functional group, class `CAN`, 0.25 mm pair width, 0.35 mm gap, 0.2 mm clearance,
15.1 mm maximum length and 0.15 mm maximum skew. These are agent-selected
qualification constraints, not a substantiated CAN product specification.

The normal command completes in 22.999 seconds. Saved PCB:
`aa622335da16fecf23e8cec934cd1339f4b4156f5bd6a801ed7310aff5ca31b4`.
Native inspection finds zero opens, errors or warnings; exact anchors,
netclasses and native rule files survive. Both coupled F.Cu segments measure
9.8125 mm, with zero skew and no vias. The build-stage producer independently
completes in 20.864 seconds and adopts its exact verified PCB
`a3a6728d1ed929b957be240342f672085bd101503d0f25639e187d15374bdf14`.
That stage test explicitly uses diagnostic mode because the software article
has no schematic, selected components or manufacturing requirement map.

| Negative | Required observed rejection |
| --- | --- |
| Omitted custom-rule binding or wrong native companion path | Refused before routing |
| Missing/changed routed rules or altered project | Final integrity failure, even when restored-rule DRC is clean |
| Connected pair with one leg extended by 5 mm | Native skew violation; final verification fails |
| Authored 9 mm maximum on the 10 mm terminal span | Public job retains zero opens but fails native length verification |
| Unknown native constraint | Rule-loading witness fails; public job refuses before routing |
| Changed rule bytes/hash after cancellation | Resume refuses different job identity; earlier checkpoint unchanged |
| 0.8 mm non-default clearance versus a 0.3 mm copper gap | Existing native and search controls both reject |

Evidence is under `build/native-rules`. The initial 12-module regression runs
106 tests and exposes six subcase errors in an outdated mock call sequence.
The eleven unaffected modules pass without skips. The corrected guards and new
native-rule module then pass all 19 checks without skips: 113 distinct Python
checks across 13 modules after correction. The native image build runs all 103
Rust tests without failures or ignored cases. The focused gate is
`rule-loading-summary.md`; the earlier failures remain in `regression.log`.
Image `agent-ee:exec-ba937b930622ad80` has ID
`sha256:cb54c06ffe5e4c3c4f20b9fdb5fe9ac23b9ad19df0a2a0d5a83d0b95d1e49e2c`.

Recreate the feature article and its public-command negatives:

```sh
ae-ci-exec . -- env \
  AGENT_EE_RULE_JOB_EVIDENCE=/opt/agent-ee/build/native-rules-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_rules
```

The retained positive can also run directly into a fresh directory:

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/native-rules/rule-loading-fix/inputs/job.json \
  --out build/native-rules-public-reproduction
```

## Matched stock exercise and limits

`build/native-rules/matched` runs stock KRT
`d24a7602012b96c5cfa610ea05a171646ad601bd` (Rust 0.23.7) on the exact
public job's saved placement, project and rules, using the same native image,
0.025 mm grid and 236-second routing allowance. Input PCB:
`ede167b5e53416cf966df2c5bf616a56b83c05edd60b39e26e3e50652eae5740`.
Both preserve the native rules/placement and produce two 9.8125 mm legs, zero
skew, no vias, no opens and no native findings. Stock output PCB:
`6c00efcc7487608b69292aa635663fbf99466ca0421d3be23aff445ca70d42ac`.
The first stock invocation refused an owned-only option before routing; its
log is retained. The corrected invocation uses stock's ordinary interface.
This path exceeds stock's short-pair deferral threshold and routes coupled.

Observed routing elapsed times are 2.054 seconds stock and 18.854 seconds owned;
the latter includes additional native grading, so this is not a comparative
performance claim. One run establishes this case, not general reliability.
All copper in this article is on F.Cu despite four enabled layers. Pair layer
transitions, meander tuning, impedance/time matching, bus routing and broader
KRT equivalence remain unfinished. The next capability is intra-pair length
matching on an asymmetric article, preserving the original authored skew rule
and independently checking the resulting copper.
