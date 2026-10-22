# Packet navigation reviewer A/B (AGENTEE-12)

This page describes the capped live comparison of initial reviewers with and
without [factual packet navigation](result-reference.md#factual-packet-navigation),
and what it measured on 2026-10-22. It reports measurements. It makes no savings,
latency or detection claim.

## Authority

The AGENTEE-12 Plane comment records the approval. It is owner-relayed through
agent-519 and is quoted verbatim: "Owner decision (2026-10-22, owner via agent-519
decision quiz): APPROVED: a capped live reviewer A/B run (at most 12 subscription
calls, per its plan). Evidence goes to the private Garage bucket; no third-party
publication." The plan, scoring rule and matrix below are agent-drafted. The owner
has not confirmed them.

## Design

`src/agent_ee/packet_ab.py` and `scripts/packet_index_ab.py` run the comparison.

- **Matched snapshot.** Each fixture is captured once with `review --checks-only`
  semantics. Both arms reuse the same capture: candidate, checks, `run_id` and
  launch expectation. The indexed arm gets the packet that `build_packet` writes.
  The baseline arm gets `historical_packet`: the same packet without `navigation`,
  the `navigation`/`binding` evidence-access entries and the
  `work/.navigation/` citation files. Its copy has no `work/.navigation/`
  directory. `verify_navigation` already accepts such packets as historical.
- **Matrix.** The RC `seeded` fixture (R1 saved as 100k) and the RC `control`
  fixture, two replicates per fixture, one initial `circuit` reviewer per cell.
  That makes 8 cells, run one after another. Arm order alternates ABBA across
  pairs. The reviewer is the committed `profiles/claude-baseline.json` circuit
  definition: Claude subscription, `claude-fable-5`, high effort, 900 s,
  investigate tools. There is no adjudicator, so a cell is one call plus at most
  one bounded output resubmission. The preregistered 2026-10-17 pilot used the
  Codex profile. This run uses the Claude route, which is the worker's
  authenticated subscription. The two runs are not comparable to each other.
- **Stop rules.** A cell starts only while the ledger has budget. The plan stops
  after an `unavailable` execution or two failed cells in a row. A failed cell is
  kept and never replaced. There is no fallback provider, model or profile.
- **Scoring.** The rule is mechanical and fixed in the plan before any call. A
  seeded review detects the seed when a finding names `R1` and matches
  `100\s*k|time[ -]constant|\btau\b|τ|0\.09\d*` in its title, location, condition,
  mechanism or reasoning. A completed seeded review with no such finding is a
  miss. On the control, every finding with assessment `defect` is a false alarm.
  A cell without an accepted review is a process failure, not a miss.

## Call ledger

`evidence/packet-index-ab/call-ledger.jsonl` (`agent-ee.call-ledger/1`) is
append-only JSONL, held under an exclusive `flock`. Its first line is the header
`{"schema", "event": "header", "cap", "created_utc"}`. The metered dispatch
appends `{"event": "reserve", "seq", "kind": "real"|"fake", "cell", "label",
"plan_id", "at_utc", "real_calls_after"}` and fsyncs it before it launches the
provider. It appends a `settle` line with the execution state afterwards.

- Every resubmission or retry is its own reservation.
- At the cap (12) a `refused` line is written. The dispatch then returns
  `not_run` and launches nothing.
- A line that does not parse counts as a real call.
- A crash can leave the last line without its newline. The next open validates
  the header, then terminates that line in place and appends a
  `tail_terminated` record before anything else. A complete reservation in the
  tail stays a reservation; a partial one stays a malformed line and is
  charged. The tail is never removed or rewritten, and the next reservation is
  always written as a separate line. `state()` reports `tails_terminated`.
- A ledger whose first line is not a valid header is refused without writing.
- A ledger whose header cap differs from the request is refused.
- Live mode refuses a ledger under `/tmp`.

The ledger lives in the repository, so the count survives restarts and fresh
clones. Deleting or reverting the file would reset it, and nothing here detects
that.

`tests/test_packet_ab.py` covers the refusal of the 13th call across restarts, a
refused reservation never launching, the reservation being on disk before the
provider runs, retries counting, torn lines, and the volatile-ledger refusal.
Two regressions write a ledger with ten settled calls and a crashed eleventh
reservation, once as complete JSON without its newline and once as partial JSON.
Each then runs three calls, each in a new Python process. One launches and
settles; the next two are refused. The tests check that the damaged tail is
kept byte for byte and that no reservation is lost. A torn header is refused
and the file is left untouched. It
also covers `historical_packet`, the scoring boundaries and a native dry run of
the full plan with the fake provider.

```sh
PYTHONPATH=src python3 scripts/packet_index_ab.py plan
AGENT_EE_SKIP_VISUALS=1 python3 scripts/packet_index_ab.py prepare ROOT
AGENT_EE_SKIP_VISUALS=1 python3 scripts/packet_index_ab.py run ROOT \
  --ledger evidence/packet-index-ab/call-ledger.jsonl --mode dry-run   # or --mode live
python3 scripts/packet_index_ab.py report ROOT/run-live.json --out evidence/packet-index-ab/report.json
python3 scripts/packet_index_ab.py ledger evidence/packet-index-ab/call-ledger.jsonl
```

## 2026-10-22 run

The full plan ran first in dry-run mode with the fake provider, through the real
preflight. That added 8 `fake` reservations and 0 real calls. The live run then
used source commit `5a1ec187951b896abfd5e3355ec9b97fd77b863e`, the plan identity
`ed4fa71989995ccee5839123189cc9eb0ef5cbd24ee2f609c364d8c8e88df54c` and
`AGENT_EE_SKIP_VISUALS=1` in both arms. Every planned cell ran: 9 real calls,
0 refused, none unsettled. The 3 unused calls were not spent.
[report.json](../evidence/packet-index-ab/report.json) holds every figure. The
report summary was regenerated from the retained run record after a later commit
added accepted-only medians.

Prompt tokens are the provider `modelUsage` sum of uncached input, cache reads and
cache writes over all agent turns. Time is the executor wall time of the call.

| Pair | Baseline (no index) | Indexed |
| --- | --- | --- |
| seeded r1 | ok; 1,193,380 prompt / 13,038 output tokens; 174.4 s; seed detected (defect) | ok; 2,514,037 / 17,598; 244.8 s; seed detected (defect) |
| control r1 | ok; 1,853,377 / 19,613; 269.1 s; 0 false alarms | ok; 2,112,568 / 21,201; 291.6 s; 0 false alarms |
| seeded r2 | refused after 2 calls; 2,998,380 / 23,758; 340.9 s; not scored | ok; 1,662,036 / 17,544; 246.0 s; seed detected (defect) |
| control r2 | ok; 2,451,813 / 20,767; 306.3 s; 0 false alarms | ok; 2,970,926 / 25,638; 369.3 s; 0 false alarms |

- **Packet size.** The indexed packet adds about 12.1 KB to the prompt: 315,032
  against 302,912 bytes for seeded.
- **Index use.** No accepted review cited `work/.navigation/`.
- **Missed defects.** There were none among the 3 scored seeded reviews.
- **Comparable pairs.** In all three, the indexed arm used more prompt tokens and
  more time. That is descriptive. One small fixture and n = 1 per comparable pair
  cannot separate an index effect from run-to-run variance.
- **Cache confound.** Each of the four replicate-1 cells wrote 202,626 to
  220,171 cache tokens. The replicate-2 cells wrote 11,348 to 22,440, so
  prompt-cache state is a confound.
- **The refused cell.** In `seeded-r2-baseline`, the first response failed the
  existing citation validator (`lines: citation lines out of range`). The bounded
  resubmission changed the finding set, and the validator refused it. Both
  rejected responses named R1 = 100k, but they are not scored.

The `rc-step` check already reports the seed in the packet. Detection therefore
measures whether the reviewer reports it, not independent discovery. The
harness `costUSD` is an estimate, not subscription billing.

## Raw evidence

[manifest.json](../evidence/packet-index-ab/manifest.json) records two objects
under `packet-index-ab/` in the private `agent-ee-evidence` Garage bucket:

- a deterministic tar.gz of the whole run root, 9,651 files;
- its per-file SHA-256 manifest.

Both objects were fetched back and checked with `sha256sum -c`. The extracted
archive matched the per-file manifest. `evidence_fetch.py upload` refuses this
store because Garage ignores `If-None-Match`. The worker therefore checked for
absence with `head-object` and wrote each new key once with the AWS CLI. The
archive contains captured T1/T6 inputs and is not for publication.
