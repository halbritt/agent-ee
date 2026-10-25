# Geometric bus routing qualification — 2026-10-25

Status: native router and public job/build-producer exercises pass; broad
regression gates and trunk integration are still pending. Router candidate is
`7a8a150ae646a283cee21386ee08fd8ed8fadcf2`. The accepted acquisition release
`ef2609e2` and integrated stripline milestone `87ce8bcb` remain unchanged.

## Implemented behavior and boundary

The existing single-ended backend already detects geometric groups, orders
members from the middle outward, plans a shared corridor, attracts neighboring
routes and completes deferred multipoint taps. The integration adds opt-in
`--bus` controls to the public router and `router.bus` to the normal placement
job. Automatic differential members remain protected and excluded from the
single-ended pass. Default behavior and native rules are unchanged.

Actual groups, corridor observations and the hashed backend net-story diagnostic
are retained in `process.bus_routing`. Missing diagnostics cannot establish a
successful bus operation. These groups are search heuristics, not authoritative
electrical bus identities or timing, impedance and coupling requirements.

The native article has eight D0–D7 lanes between fixed 2.54 mm headers, a third
terminal on D3, a separate parallel AUX net and an F.Cu routing-only obstacle.
The 70 × 65 mm outline, two copper layers, 0.25 mm track-width rule and 0.2 mm
class clearance are explicit input constraints. The 20 mm detection radius and
minimum three raw members are agent-selected search preferences. All five
footprints belong to the bus or auxiliary functional block. Their positions,
original rules, board outline and rule area survive placement and routing.

## Native and public evidence

The public placement command and actual build producer both complete. They
record exactly D0–D7 in one planned corridor, exclude AUX and connect all three
D3 terminals. Final saved metrics are 35 track segments, 502.1307 mm copper,
four vias and zero via centers in pads. The complete build candidate has zero
opens and native findings and retains captured footprint libraries byte for
byte. The standalone job has baseline missing-library warnings in its temporary
board context; its completion does not establish manufacturing acceptance.

The public board SHA-256 is
`2799a102233b7ce107011e02803c1b5cd4ed30fecef695458a3ab86ee2ee7952`.
The build-producer board SHA-256 is
`b317b799fd0cab53c10acfb0c8857d37fc8e35b86cc7d0033bef9ade714f8759`, at
`build/bus/public/build-stage/placement-routing/candidate/bus.kicad_pcb`.

Ten router cases pass, including a mixed differential-pair/bus board, native
rejection after a saved lane is removed and invalid-control refusals. The
134 existing pair/interface cases also pass. Four consumer cases have passing
evidence: public operation, build production, invalid controls and changed-
control resume. The original negative test expected a bus-specific refusal for
NaN; existing canonical JSON rejects it earlier with `ValueError`. The test now
checks that actual ingress refusal and that no job directory is created. Only
the affected method was rerun (40.758 seconds, its normal native fixture); the
original error and 45.052-second public run remain retained.

The clean image is `agent-ee:exec-7d4d4050d2a00395`, ID
`sha256:299e7a280fd80c46a9d3661be039cbaccc5df817456de4a7094ff72228f63e5a`.
All 103 placement-engine Rust tests passed during its build. Native checks use
KiCad 10.0.7. The complete 1,074-case router smoke run and 50-module previous
consumer roster remain in progress; they must finish before integration.

## Matched stock comparison

Stock main `4cf6d29ddbe7d19b511352d0abbb26842f609be6` was observed again on
October 25. All 1,799 source files match the captured upstream archive. The
compiled module and its 14 Rust source hashes were checked against their
retained build record. Both tools consume identical native inputs, controls,
0.1 mm grid, preservation settings and a 180-second backend budget.

| Saved result | Owned | Stock KRT |
| --- | ---: | ---: |
| Opens / native findings | 0 / 0 | 0 / 0 |
| Track length | 502.1307 mm | 502.1307 mm |
| Track segments / vias | 35 / 4 | 35 / 4 |
| Detected members / unrelated AUX | D0–D7 / excluded | D0–D7 / excluded |
| D3 terminals connected | 3 / 3 | 3 / 3 |
| Backend time | 2.3362 s | 2.3988 s |

One run supports parity on this article, without speed, reliability or broad
superiority claims. Native layer plots were inspected; the upper lanes go
around the obstacle, the third-terminal branch is connected and AUX remains
separate. This completed baseline is not length/time matched or optimized.
`build/bus/matched-results.json`, `stock-source-verification.json` and
`views/review.json` retain measurements, identities and bounded observations.

## Reproduction and remaining obligations

From the agent-ee checkout, generate fresh native inputs from the installed
fixture and exercise the public job and build producer:

```sh
ae-ci-exec . -- env AGENT_EE_BUS_EVIDENCE=/opt/agent-ee/build/bus-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_bus
```

Replay the captured public request:

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/bus/public/inputs/job.json --out build/bus-replay
```

This interconnect article is not a circuit or manufacturing release. Timing,
length matching, SI, arbitrary bus topologies and experimental topological bus
routing are not discharged by this result. Broad regressions, final evidence
sealing and trunk integration remain pending. No unchanged acquisition build
was repeated.
