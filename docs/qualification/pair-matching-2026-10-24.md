# Intra-pair matching through the supported workflow

Agent-545, 2026-10-24. **Qualified capability milestone.**
This is one capability milestone after the accepted acquisition release. It does
not extend that design's engineering contract or change its delivered files.

## Behavior and engineering boundary

The existing coupled router can add meanders. Its supported wrapper previously
had no matching control; `--backend-arg` is intentionally incompatible with auto
pairs. The correction exposes `--diff-pair-intra-match`,
`--length-match-tolerance` and `--meander-amplitude`, then carries explicit
`router.intra_pair_match` settings through the normal placement job and build
placement producer. Default behavior remains unchanged.

The independent grade measures emitted native copper, checks the requested
matching tolerance, and retains original KiCad project/custom rules. Native DRC
and identity/preservation checks remain required. Connectivity and matching have
separate results: a connected pair outside tolerance fails validation. Backend
success or a historical matching report cannot establish the final result.
Existing protected copper is graded without retuning. A single-net recovery that
cannot carry pair coupling/matching is not substituted for a pair operation.

## Representative article

Two fixed two-pad terminals form an asymmetric pair on a 45 by 40 mm rectangle.
J1 is at (45,50) mm; J2 is at (75,70) mm, rotated 90 degrees. Four copper layers
are enabled; the measured route uses F.Cu only. Every footprint belongs to the
`interface` functional block. The unchanged native constraints are 0.25 mm width,
0.35 mm gap, 0.20 mm clearance, 70 mm maximum length, 0.15 mm maximum skew and
6 mm maximum uncoupled extent. These are agent-selected software qualification
assumptions, not owner requirements or a CAN hardware design contract.

The source board, project and custom-rule hashes are respectively:

- `3deacdaf715ddd17af62237690d7078a558934f13a38916a92641680fa37c043`
- `43b807354f129afab690a23b9fe44e93a3a4a17e1b40ddfb2e57d99056b676b9`
- `2e77929d0278ba561cb67adfe74ddc271baf6631344338d35c930925412daffd`

The initial normal placement job connects all nets but correctly rejects
2.783810 mm skew. Enabling the existing matcher at its 1 mm amplitude default
still fails. Increasing the algorithm's allowed amplitude to 2 mm, without
changing any native requirement or anchor, permits a passing owned-backend
route. This finite experiment justified interface integration; no backend
algorithm was replaced and no manual copper repair was used.

This exercises the intra-pair portion of capability **D08** in the retained
[KRT source map](../research/krt-2026-10-24/krt-latest-capability-map.md).
AC-coupled legs and pair-to-pair groups in that row remain unqualified; D06
group matching and D07 time matching are also still open.

## Matched backend comparison

Retained in `build/pair-matching/{owned,stock}-match[-a2]`. Both backends use the
same native inputs, a 0.025 mm grid, 0.1 mm requested matching tolerance,
180-second allowance and the same KiCad 10.0.7 execution image. Automatic rule
changes, component moves and input-copper destruction are disabled. The stock
source is revision `d24a7602012b96c5cfa610ea05a171646ad601bd`, Rust 0.23.7;
the owned source is `e69ec315403ef8fb2de24f2b3074098aa007006c`, Rust 0.23.1.
Exact stock source/binary provenance is retained with the native-rule comparison.

| Backend / amplitude | P length (mm) | N length (mm) | Saved skew (mm) | Native acceptance |
| --- | ---: | ---: | ---: | --- |
| Owned / 1 mm | 45.949591 | 46.935175 | 0.985583 | Reject: skew |
| Stock / 1 mm | 46.338019 | 46.935175 | 0.597156 | Reject: skew |
| Owned / 2 mm | 46.934388 | 46.935175 | 0.000787 | Pass |
| Stock / 2 mm | 47.132915 | 46.935175 | 0.197740 | Reject: skew |

Measurement clarification: the table sums saved planar track/arc copper.
KiCad's native electrical-length/skew rules are a separate acceptance check;
the listed difference is not a native electrical-path or propagation-time
measurement. The original input, output and acceptance records are unchanged.

All four have zero opens and vias. Owned 2 mm has 22 segments and 93.869562 mm
total copper, no native violations or warnings, and preserved anchors/rules.
Its final board is `fdfe12ec918a9d69b713a6a904e2268fca8d553d9cb41e219685acf6932a6424`.
The stock backend honestly regrades its final copper as unmatched; it does not
claim success. The paired 2 mm runs take 3.424 and 3.513 seconds respectively,
including this diagnostic harness's invocation. A single observation does not
establish a performance advantage or broad reliability. The demonstrated
advantage is this article's final skew/correctness under the matched settings.

## Verification and remaining work

The first public negative exposed a result-contract mismatch: the implementation
reported routing INCOMPLETE for connected copper that failed matching. This was
corrected to fail validation while preserving the connectivity result. Retained
failed tests are not counted as qualification. Eight focused public-router cases
then pass, including real positive/negative native routes, revalidation, invalid
controls, and a clean-DRC candidate rejected against a tighter matching target.

The normal `agent-ee place run` succeeds in 26.030 seconds, final PCB
`be3329112d56ab09e3a2df861892e12fc4bba8135468421ec792f32dc019b3e3`.
The real build placement producer also succeeds in 27.756 seconds, PCB
`166bbfa947d09e89ca6b1e3e2a7689813dd753adc94db801cf7df0953c64e68b`.
Both have zero opens/findings, 22 segments, no vias and preserved anchors,
native classes and rule bytes. A 1 mm amplitude request through the same public
entrypoint returns `verification_failed`, with zero opens and a native skew
violation. Invalid controls and changed matching settings on resume reject.
Six new native qualification checks pass without skips.

The router/interface regressions have passing evidence for all 134 cases: 131
passed the full isolated run, and three original rejection assertions were
restored after an accidental test edit and pass in the nine-case focused gate.
The router land gate repeats those nine successfully. The initial broad run had
an unprovisioned Freerouting test and unavailable cached terminal evidence;
provisioning the existing backend and using a fresh isolated output resolves
both. Earlier source-changing trials are diagnostic only. Detailed failed logs,
original backend diagnostics and all final regression artifacts survive; the
first scratch worktree's raw intermediate run directories were removed by land
before archival and are not claimed retained.

The clean image `agent-ee:exec-f2de12b2f61d6457` is
`sha256:d18b710ccfdebc089b95e6a4dc491d2e511a05c97db67f82889eee32b82dd474`.
Its offline installation checks and 103 Rust tests pass. All 46 required
agent-ee modules / 958 distinct tests have passing evidence: the initial
45-module, 952-test gate found only three stale pin/documentation expectations;
those were corrected, and both affected modules pass again in native strict
mode (12 and 9 cases). The six new public qualification cases also pass. No
required case was skipped or missing. Raw failed and corrected reports remain
separate. A further public command rejects a matching request with no detected
pair before invoking the backend, so empty coverage cannot establish success.

kicad-route is integrated/pushed at
`1c895b67dd6ca36620c0b5f757d6402e9692bf78`. The owned backend source is unchanged
from the matched comparison; only its public controls/grading changed. The
agent-ee revision and verified source hashes are recorded in
`build/pair-matching/integration.json`. All retained evidence is copied to the
main checkout before worktree removal and then sealed under
`~/.cache/agent-ee-qualification/agent545/pair-matching-20261024-<revision>`.
The original acquisition package stays bound to its accepted revision and bytes;
this opt-in feature does not require rebuilding that unchanged design.

Generate and exercise the article from tracked source:

```sh
ae-ci-exec . -- env AGENT_EE_MATCHING_EVIDENCE=/opt/agent-ee/build/pair-matching-reproduction python3 scripts/strict_suite.py --jobs 1 test_placement_job_matching
```

Replay the captured public input (use a new output directory):

```sh
ae-ci-exec . -- bin/agent-ee place run build/pair-matching/public-qualification/inputs/job.json --out build/pair-matching-public-replay
```

No schematic, component selection, assembly or manufacturing acceptance is claimed
for this software article. Multi-layer pair transitions, time matching, controlled
impedance, buses, return paths and BGA/QFN escape remain separate required work.
Supplier previews and physical validation for the accepted acquisition package
remain unperformed as stated in its delivery contract.
