# Placement-job tooling repair and esp_prog run — 2026-10-24

The repaired workflow placed and routed the frozen esp_prog U2-v2 fixture in
139.065 seconds. Final KiCad DRC reports zero errors and zero unconnected items.
The native placement gate promoted seed 10, and the final integrity check preserved
its component identities, geometry and positions. This is an end-to-end tooling
result, not manufacturing acceptance or a matched performance comparison.

The owner directly replaced the multi-agent campaign with one agent on 2026-10-24.
Agent-544 recovered and reviewed the unfinished 515c changes, completed the repair,
and ran the checks without launching other agents. No telemetry-pod run occurred.

## Changes and checks

- Native capture and Rust preparation now share bounded copper/drill contour
  limits: 2,048 vertices per contour, 16 contours per item, 16,384 vertices total.
  Native geometry is preserved. The general planner retains its prior limit.
- Nonconvex copper is triangulated once during preparation. Exact overlap uses
  prepared convex pieces, and clearance uses original edges. On the retained
  board, evaluation fell from 53.817 seconds to 0.018 seconds in the pinned native
  runtime. This is evaluation time, not a whole-workflow speedup claim.
- Owned refusals produce terminal reports with reasons, timings and runtime
  identities. Foreign resumes, including interruption before identity validation,
  preserve existing evidence. Incumbents survive refusal.
- All 96 Rust tests and 73 focused native Python tests passed. The external
  failing input was required, not skipped. A separate retained Rust test checked
  region equality for U2. Exact-reference tests cover rotations, contact and
  clearance boundaries. Documentation and origins checks passed.

## Board run

| Measurement | Result |
| --- | --- |
| Input board SHA-256 | `b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7` |
| Evaluated code | `276b0b4c` (source bytes unchanged between run and commit) |
| Runtime | KiCad 10.0.7, Python 3.12.3, pinned kicad-route `b5774496` |
| Search | Seeds 0–5 scattered, 6–11 clustered; 10,000 steps each; shortlist 6 |
| Overall budget | 600 seconds |
| Search / routing / final checks | 101.936 / 34.437 / 0.723 seconds |
| Valid incumbent | Seed 10; zero native placement errors |
| Final native errors / opens | 0 / 0 |
| Final integrity | Preserved |
| Track length / vias | 273.1942 mm / 21 |
| Final board SHA-256 | `0b82238d0ec1ce794b724d4879a49459bc012aa9f0bec88365df423ecb4b88f9` |

The 1,000-step preliminary run also completed all phases, but had eight placement
errors and six opens. It remains labelled diagnostic in the retained evidence.
It finished before an attempted cancellation; the later CANCEL marker was removed.

## Limits

The final run still reports 262 warnings (silkscreen, text and library checks),
six geometric via-in-pad sites, and `accepted: false, clean: false`. CON1 and CON2
have east-edge declarations but their nearest edge is south; their captured
rules have no hard maximum setback. These measurements are visible in the final
report and are not full intent or assembly qualification. USB1's nearest edge is
west, with 0.15 mm overhang within its declared interval. No fabrication package,
independent engineering review, pod readiness or superiority claim is made.

## Evidence and reproduction

Private evidence is retained at
`~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/`.
`board-run/job-report.json`, `board-run/final-a1/native-drc.json`, and
`board-run/final-a1/board.kicad_pcb` are the final artifacts. `SHA256SUMS` seals the
bundle. Third-party fixture bytes remain outside version control.

From this repository, with the installed native test environment:

```sh
mkdir -p build/agent544
cp -a ~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/inputs build/agent544/
cp ~/.cache/agent-ee-evidence/agent544-tooling-repair-20261024/board-config.json build/agent544/
ae-ci-exec . -- env PYTHONPATH=src:tests python3 -m agent_ee.placement_job run   build/agent544/board-config.json --out build/agent544/reproduction
```

Use a fresh output directory. Runtime and input identities are retained by the
job; differences in runtime or source mean a new run, not replay of this result.
