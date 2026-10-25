# Explicit single-ended group length matching — 2026-10-25

Later correction: [exact group input and replay](group-input-2026-10-25.md)
closes the leading-hyphen input gap recorded below. This report preserves the
original baseline and its observed limitations.

Status: baseline qualified through the public placement command and actual build producer.
Router revision: `21c08eca654e249ad24a5d6035757bc64602ed23`. The accepted
17-part acquisition release `ef2609e2` remains unchanged. The broader goal is
unfinished. This qualifies explicit two-terminal groups on the declared article;
the public leading-hyphen input gap remains the next correction.

## Behavior and engineering boundary

Normal placement/build requests accept `router.length_match` with exact net
groups, geometric tolerance and meander amplitude; arm spacing defaults to two
track widths. Group controls remain separate from intra-pair controls. Missing,
duplicated, out-of-scope, coupled-pair, multipoint and same-net zone/graphical
copper members refuse. A scoped single-net repair cannot drop required members.
The existing routing engine inserts meanders; native KiCad remains authoritative.

After final save/refill, both router and agent-ee independently check native
terminal-to-terminal `fromTo` paths. Each group gets a verification-only project
copy. A zero-length rule deliberately emits a native measurement for every
required path; a separate skew limit grades the result at full precision.
Measurement findings establish coverage and are not original-board defects.
Original rules are unchanged and separately graded. Missing or ambiguous
measurements, ignored required checks, opens and excessive skew fail.

A retained negative demonstrates why this matters: a 48 mm path with a connected
2 mm loop passes the original whole-net rule but still has a useful path of
48 mm. The independent check reports 2.0233 mm skew against the 0.1 mm limit.
See [KiCad's native length rules](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html).
Captured KiCad 10.0.7 source and hashes are under `build/group-matching/sources`.

This article proves geometric software behavior. It does not establish signal
integrity or manufacturing acceptance. DDR auto-grouping, package lengths,
absolute length-window coverage, propagation time, AC composition, inter-pair
matching, multipoint path selection and broader multilayer cases remain goal
obligations. A native probe with `-DQ0` exposes a remaining public-input gap: the CLI
interprets the name as an option. It refuses safely, but that required group
cannot currently be expressed. The raw backend encoder handles the name; its
test does not close the public path. `option-name-probe` retains the input and
refusal. The next correction is an unambiguous public group input, before
broader delay-matching qualification.

## Native article and matched stock comparison

The captured `kicad-route/fixtures/group_matching` project has four fixed header
lanes on a 70 × 50 mm two-layer board, with direct lengths 50, 48, 46 and 44 mm.
Minimum width is 0.25 mm, class clearance 0.2 mm and maximum group skew 0.1 mm.
These are explicit agent-selected qualification assumptions. Each lane has a
functional block, and native libraries and original constraints are preserved.

Stock main `4cf6d29ddbe7d19b511352d0abbb26842f609be6` was observed at
2026-10-25T11:00:36Z. All 1,799 captured source files, the compiled module and
its 14 Rust source files were verified. Both tools consume identical preserved
native inputs, a 0.1 mm grid, 3 mm amplitude, two-width spacing and a 180-second
backend budget. After the exact-name repair, the owned run was repeated on the
same inputs/runtime; the unchanged stock result was retained.

| Final measurement | Owned `21c08ec` | Stock `4cf6d29` |
| --- | ---: | ---: |
| Opens / original native findings | 0 / 0 | 0 / 0 |
| Required paths within 0.1 mm skew | 4 / 4 | 4 / 4 |
| Segments / vias | 28 / 0 | 28 / 0 |
| Copper length | 199.9525 mm | 199.9525 mm |
| Backend time | 1.4883 s | 2.6890 s |

This demonstrates parity on the article. Single measurements with overlapping
local work establish no speed or reliability superiority. Native board hashes
differ; geometric metrics agree. The first comparison aggregator mistakenly
included the board hash in geometric equality; correcting that assertion needed
no routing repeat. Original records and the correction are retained in
`matched-results.json`, `matched-results-v2.json`, `matched-environment.json`,
`stock-source-verification.json` and `matched-{input,owned,owned-v2,stock}`.

## Public workflow and required gates

The final build-producer PCB is `3a7e6152aae010419fc8031ec270fbef1ee0a8232b69acf6ce914e2ce7d38f19`, at
`build/group-matching/public-v2/build-stage/placement-routing/candidate/group.kicad_pcb`.
It has zero opens/native findings, four independently checked paths, 28 segments,
199.9525 mm copper and zero vias. Local library dependencies remain unchanged.
The public connected-loop job rejects despite zero original native DRC errors.
Changed matching controls refuse resume without replacing the saved checkpoint.

All 997 required consumer cases in 52 modules pass without skips. Individual
case rosters and source bindings are retained in `consumer-gate-closure.json`.
The strict-suite container exited 0 after 1,359 seconds. Its outer exec wrapper
returned 143 afterward, with an unestablished signal cause; this is retained in
`gate-process-result.json`, not reported as wrapper success. Per-case results,
container exit and saved native artifacts independently establish gate completion.
The clean image is `agent-ee:exec-84ec42de6c28c2e0`, ID
`sha256:77d3d0eaa27b621a5bcf3410d38b235cd1455024d90b48f86c27b3347ac50557`.
Its build passed 103 placement-engine tests. Observed tools: KiCad 10.0.7,
router 0.23.1, NumPy 2.5.4, SciPy 1.18.1, Shapely 2.2.0 and Pillow 13.0.0.

The original router integration `1534798` closed 168 cases: 17 matching cases
and 151 pair, bus, interface and baseline regressions. The exact-name correction
then passed all 19 matching cases. The 151 unaffected cases are retained from
`1534798`; no fresh full-router-suite claim is made. The earlier complete bus
smoke remains available. Cases cover insufficient space, connected loops,
ignored checks, overlapping groups, scope/refusal, coupled-pair coexistence,
revalidation and literal/hierarchical name selection.

Retained corrections include:

- Native revalidation found checker configuration writes inside the candidate.
  Checker writes now stay in a separate verification directory.
- Routing-filter escaping was invalid for the distinct whole-name matcher.
  Leading `!` missed members and `DQ[*]` also selected `DQ.`. The maintained
  matcher now uses case-sensitive shell globs, with exact public-name encoding.
  Raw `?` deliberately means one character; `[?]` means a literal question mark.
  The corrected native test uses exact `fromTo` conditions because KiCad's
  `A.NetName == 'DQ*'` itself expands a wildcard.
- Initial tests needed an optional project key and corrected pair-status enum.
  One interface case lacked the verified Freerouting runtime; its rerun passed.
- The first consumer gate had a stale documentation digest and twelve skips
  from absent historical receipts. The digest was regenerated and byte-exact
  retained fixtures restored. The final required gate has no unresolved cases.

All initial logs remain under `build/group-matching`. Doctrine decision
`pkt-a3f2b7aca0156a33` (SHA-256
`a3f2b7aca0156a33168012167d4fa9bf0a51e3ca6d1406b454c6eee364dc94c9`)
supports the bounded reuse/native-verification choice. Material obligations and
24 nonmaterial generic obligations have individual dispositions. New name-boundary
findings and their rationale are in `exact-name-repair.json`. Neither record
confers circuit or manufacturing acceptance.

## Reproduction

Generate fresh inputs and exercise the public job plus build producer:

```sh
ae-ci-exec . -- env AGENT_EE_LENGTH_EVIDENCE=/opt/agent-ee/build/length-reproduction \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_length_groups
```

Replay the captured request in the supported environment:

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/group-matching/public-v2/inputs/job.json --out build/length-replay
```

The full gate command and required module roster are retained with the evidence.
The accepted acquisition's supplier-preview and physical-validation obligations
remain explicit in its release package; neither activity was performed here.
