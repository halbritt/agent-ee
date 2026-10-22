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

**Invariant: every ledger line is either a validated known record or is counted
against the cap.** `LEDGER_RECORDS` in `src/agent_ee/packet_ab.py` is the strict
schema. It lists the required and optional fields of each event, with their
types and allowed values. Every other field, and every other event, is invalid.

| event | required fields | optional |
|---|---|---|
| `header` | `schema` = `agent-ee.call-ledger/1`, `cap` int 1..12, `created_utc` | |
| `reserve` | `seq` int, `kind` `real`/`fake`, `cell`, `label`, `plan_id` (non-empty strings), `at_utc`, `real_calls_after` int | |
| `settle` | `seq` int, `at_utc`, `execution` (non-empty string or null) | `duration_s` (finite, non-negative or null), `error` (non-empty string) |
| `refused` | `kind` = `real`, `cell`, `label`, `plan_id`, `at_utc`, `real_calls` int | |
| `tail_terminated` | `line` int, `at_utc` | |

Timestamps are ISO 8601 with an offset. Before anything is counted, written,
recovered or launched, every line is checked in order, along with its context:

- it is ASCII JSON in canonical form, exactly as `json.dumps(record,
  sort_keys=True)` writes it;
- the header is the first line, and only there;
- a reservation's `seq` is its own line index, and its `real_calls_after` is
  the running real count, never above the cap;
- a settlement names an earlier, still unsettled reservation;
- a refusal records the count at the cap;
- `tail_terminated.line` is its own line index.

The rule for a line that fails any check is uniform. Unknown or missing
events, invalid or missing kinds, missing, extra or mistyped fields,
undecodable or non-ASCII bytes, non-canonical spacing, broken sequence and a
torn tail are all treated the same way: the line is charged as the whole
remaining budget. Reading raises `LedgerRefused` (a `ValueError`), so nothing is
reserved, settled or launched, and the file is left byte for byte as it is. No
line is ever skipped, repaired, rewritten or removed. The rule is refusal, not
charging one call: a single damaged byte can fuse two lines into one, so a
one-call charge could hide a reservation. The writer validates each record
before appending it, and `settle` refuses a sequence number that is not an
unsettled reservation, so the code never writes a line it would then refuse.

- A crash can leave the last line without its newline. If that tail is a
  complete, valid record, the next open terminates it in place and appends a
  `tail_terminated` record before anything else. A kept reservation stays
  charged, and the next record cannot fuse with it. `state()` reports
  `tails_terminated`. A torn, invalid tail is refused like any other invalid
  line.
- A ledger whose header cap differs from the request is refused.
- Live mode refuses a ledger under `/tmp`.

Recovering a refused ledger takes an operator decision outside this tool. The
tool never makes that decision itself.

The ledger lives in the repository, so the count survives restarts and fresh
clones. Deleting or reverting the file would reset it, and nothing here detects
that.

`tests/test_packet_ab.py` covers:

- the refusal of the 13th call across restarts, a refused reservation never
  launching, the reservation being on disk before the provider runs, retries
  counting, and the volatile-ledger refusal;
- a complete eleventh reservation left without its newline. Of three calls,
  each in a new Python process, one launches and the next two are refused.
- refusal, in process and in restarted processes, for each of the following,
  with the bytes kept: partial JSON (terminated and not), invalid UTF-8 in any
  field, valid-UTF-8 damage to `kind`, `event` or their keys, and wrong `seq`,
  `real_calls_after`, types or fields;
- unknown events, non-objects, blank or `\r` lines, a double settlement, a
  settlement of an unknown `seq`, an early refusal, a misplaced
  `tail_terminated` or header, a bad timestamp and non-canonical spacing;
- a property sweep. Every bit flip, plus 16 replacement bytes, is applied at
  every byte position of a reservation line, about 11,800 ledgers in all. The
  line is placed three ways: as the unsettled tail with its newline, as the
  tail without it, and as an interior line followed by its settlement and later
  records. Each damaged ledger then gets three calls, each in a fresh
  interpreter. No ledger ever reaches a thirteenth real call: each one is
  either refused with its bytes intact, or still charges the same call.
- every single bit flip over a ledger holding every record type. Each one is
  refused or leaves the state unchanged.
- the retained 9-call live ledger, which validates without a byte changing.

It also covers `historical_packet`, the scoring boundaries and a native dry run
of the full plan with the fake provider.

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
