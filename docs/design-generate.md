# Requirements to a retained board build

`agent-ee design generate` asks a subscription author to turn requirements into
an `agent-ee.design/1` statement, captures the selected parts, compiles the typed
bundle and builds through package. Run native work inside the production image
under `native-admit`, with the pinned router, planner and subscription login
mounts required by the deployment. The command does not provide admission or
install a native runtime itself. Capture/search download vendor bytes; they
never upload project files or place orders.

```sh
agent-ee design generate REQUIREMENTS.md --out NEW_RUN
agent-ee design benchmark fixtures/design-requirements --out NEW_BENCHMARK \
  --max-attempts 3 --max-seconds 1200
```

## Options

Both commands take the same options. Bounds apply to each design.

| Option | Default | Meaning |
|---|---|---|
| `REQUIREMENTS` | required | `generate`: one UTF-8 Markdown file of at most 32768 bytes with at least one sentence outside headings; a larger file is refused with the limit named. `benchmark`: a directory; each `*.md` file is one design. |
| `--out DIR` | required | A new output directory. |
| `--author HARNESS:MODEL:EFFORT` | `codex:gpt-6.1-sol:high` | The author seat. Harness `codex` or `agy`; effort `low`, `medium`, `high` or `xhigh`. Subscription login only. |
| `--max-attempts N` | `4` | Attempts per design, 1 to 100. An author turn, an automatic search retry and a timed-out author turn each use one attempt. |
| `--max-seconds S` | `2400` | Wall time per design, 1 to 86400, shared by every author turn, phase, retry and review. |
| `--author-seconds S` | `600` | Limit of one author turn, 1 to 86400, cut to the time the design has left. |
| `--search-retries N` | `1` | Automatic extra-seed attempts per design, 0 to 8. |
| `--review-profile FILE` | none | Run the full engineering review on each attempt that passes file release, within the same bounds. Allow roughly 20 extra minutes in `--max-seconds`. |
| `--context-root DIR` | this checkout | The checkout with `docs/design-compile.md` and `fixtures/design-examples`. |
| `--kicad-root DIR` | `/usr/share/kicad` | Local CAD libraries for capture and for the author's helpers. The author sandbox shows the host `/usr`; a root elsewhere must exist and is bound read-only into the sandbox at `/kicad`. |

Invalid options are refused before any output exists; a benchmark then runs no
design.

## The author seat

The default author needs a subscription login, not an API key. Set
`AGENT_EE_CODEX_AUTH_HOME` to the mounted login directory and
`AGENT_EE_CODEX_BINARY` to the native binary. There is no fallback on login,
quota or harness failure: the design ends `seat_failed` and the benchmark stops.
The same holds for review seats: with `--review-profile`, a reviewer execution in
the review result, or the adjudicator's execution, that is not ok also ends the
design `seat_failed`. For either
seat, a timeout, a seat the stopped run never reached and a structured-output
refusal are not seat failures. The retained consoles of the first benchmark
rounds held no login or quota failure, and the harness reports any provider
error as the same generic failure, so the tool cannot tell a login or quota
failure from another stream failure. It therefore stops on every seat failure.

An author turn that reaches `--author-seconds` is not a seat failure. While
attempts and time remain, another author turn starts with the timed-out turn's
experiments. The design ends `author_timeout` only when the last attempt timed
out and no attempt or time is left. A turn whose console passes 32 MiB is stopped
and counts as a failed attempt; the next turn is told why.

The author receives the current compiler docs, example statements, schema
validators and a structured envelope schema (`context/author-schema.json`). The
statement is JSON text inside that envelope because its parts/nets maps have
design-dependent keys. The next author turn reads the experiments of the most
recent author turn, also after an automatic search retry, at
`/candidate/prior-author-experiments`; links are not copied, and capture still
refreshes stock and downloads.

Remaining risk: a seat can read the login mounted for its turn, and it could copy
it into its experiments or console. Nothing scans or removes such a copy, and
the next author turn receives the experiments. Retained experiments stay local
in the run directory and unpublished; these commands never upload them.

## Requirement trace

A requirement item is one sentence of the requirements, or one row of a
Markdown table (without its outer pipes). A table is a line followed by a
delimiter row, read by the same rule as the spec parser (`agent_ee.spec_record`):
cells of dashes with optional colons and at least one pipe, outer pipes
optional. Lines that start with a pipe form a table too. A table runs to a blank
line, heading or list item. A sentence ends at `.`, `!` or `?`
(with any closing quote or bracket) followed by a space and a character that is
not a lowercase letter. A sentence never spans a blank line, heading, list item
or table row. Headings, and table rows above a table's separator row, are
titles: they may be quoted but need no trace and cover no other item. The
prompt lists the numbered items.

- `trace` pairs a quote with the design field that carries it. A quote is at
  least three whole words copied from inside one item, or the whole item when it
  is shorter; line breaks and spacing may differ. A field is a path through the
  statement's objects and lists, such as `board.width_mm` or `bench[0]`, never a
  character position inside a text value.
- A quote covers an item only when it lies inside exactly one item, or when the
  entry's `item` names the item's number. A quote whose words occur in more than
  one item, with no `item`, is refused: a shared quote never covers two items.
  `item` is null or absent otherwise; null is valid in the schema each harness
  receives as well as in the validator.
- Intent and every `functional[i].text` need a trace.
- Every item needs a trace quote or an `untraced` entry. An `untraced` entry
  quotes the item and gives status `unsupported` or `not_applicable` and a
  reason. An item with neither is refused.
- `agent_selected` lists every discretionary choice, each a nonblank label,
  separately from requirement facts.

The sentence split is a heuristic. An abbreviation followed by a capital or a
digit ends a sentence (`approx. 5 V` gives two items), and a sentence that
starts with a lowercase letter joins the one before it. Neither drops text, but
an author may need a correction turn to quote such items.

`provenance.json` keeps the trace, the untraced items and the choices, labelled
as agent-authored and not owner or datasheet rulings. They are text
traceability, not proof that the prose or engineering is correct. A whole design
the compiler subset cannot express is reported with envelope status
`unsupported`.

## Helpers in the author seat

```sh
python3 -m agent_ee.design_tools search 'NE555' --out /work/experiments/search-1
python3 -m agent_ee.design_tools symbol Timer:NE555D
python3 -m agent_ee.design_tools footprint Package_SO:SOIC-8_3.9x4.9mm_P1.27mm
```

Search takes a keyword of 1 to 120 characters, retains one bounded JLC response
page (and the exchange, also when it fails) and returns stock, tier, MPN,
package, unit price and candidate datasheet URLs. Requests sharing a parent
output directory are spaced five seconds apart. Stocked parts sort before
out-of-stock parts, then parts with datasheet URLs, then Basic, Preferred and
Extended tiers, then price. A URL is not proof of a usable PDF: capture verifies
that later. Pin and pad helpers use the existing shared readers; custom pads
refuse before any native helper is invoked. Downloads and library bytes stay in
the retained run, outside the public repository. The seat has network access;
"downloads only" is an instruction to the author, not an enforced boundary.

## Attempts and feedback

An author attempt keeps `prompt.txt`, `response.json`, `statement.json`,
`refusal.txt`, `provenance.json`, `attempt.json`, the seat archive under
`review-attempts/author/`, its read-only `inputs/`, and the capture, bundle,
build, review and `*-execution` logs of the phases that ran. Compiler/capture
refusal fields form stable `refusal_classes`; the exact message is retained. An
unlocated capture or compile refusal is flagged as a tool gap.

What goes back to the author is bounded:

- The current statement is echoed up to 20000 characters. A longer or invalid
  statement is cut and marked; the complete text is
  `/candidate/current-statement.txt`.
- Feedback is cut at 14000 characters and marked; the full refusal stays in the
  attempt.
- Phase logs enter reasons and feedback only as the last 6000 bytes of each log.
- A failed engineering review leads with its blocking findings (accepted,
  deferred or unresolved), then a short excerpt of the review report. The report
  itself stays in `review/report.json`.
- Build failures carry the failed check records and placement findings. A
  placement summary comes first, before the check records: for each placement
  job, its status, `engine_feasible`, `validity`, the engine's hard violations
  by part and term (with the seeds that showed each), native error types, opens,
  edge-intent violations and locality defects. The detailed placement findings
  follow. The feedback leaves out the provenance fields `declaration`,
  `source_records` and `manufacturing_floor`; the attempt and job reports keep
  them.

A placement search failure first uses an automatic search retry while
`--search-retries` allows: no model call, the same statement and captures, and
twice the placement seeds up to the compiler's 64. When the seeds are already 64
the retry is skipped (`search_retry_skipped`) and the next author turn is told
why. A job status of `placement_infeasible` (no incumbent and no hard-feasible
engine state) is not retried either: more seeds would repeat the same search,
so the next author turn gets the violations. A retry attempt writes
`automatic-retry.json`, which names its source attempt and the changed seed
count; it writes no prompt and no response. It uses the same attempt and time
bounds.

## Acceptance and claims

`accepted` requires the build's `accepted` flag and typed qualification with
`file_release: pass`, and, with `--review-profile`, an engineering review that
passed. The top-level `claims` describe the final outcome only. With an accepted
attempt, `file_release` is true and `engineering_review` is that attempt's
review result (`not_run` without a profile). Without one, `file_release` is
false and `engineering_review` is `not_claimed` when a review ran on a rejected
attempt, else `not_run`; each attempt row keeps its own results. Supplier
preview and physical test are never claimed.

## Final states

`generate-report.json` has schema `agent-ee.design-generate/1`, one row per
attempt, explicit bounds, durations, failed check records, the run identity and
one of these final states:

| State | Meaning | Benchmark |
|---|---|---|
| `accepted` | An attempt passed file release (and review when requested). | continues |
| `unsupported` | The author reported that the compiler subset cannot express the requirements. | continues |
| `author_timeout` | The last author turn timed out and no attempt or time was left. | continues |
| `review_incomplete` | The engineering review ended without a usable outcome and no review seat failed; no further seat was called. | continues |
| `time_exhausted` | `--max-seconds` ran out. | continues |
| `attempts_exhausted` | `--max-attempts` ran out without acceptance. | continues |
| `error` | An unexpected error; it is retained and propagated. | continues with an `error` row |
| `seat_failed` | The author seat or a review seat failed (login, quota, transport or stream). | stops; later designs are `not_run` |
| `runtime_refused` | The build refused the native runtime or router pin. | stops; later designs are `not_run` |
| `interrupted` | SIGINT or SIGTERM stopped the run. | stops; see below |

Attempt rows use `refused`, `failed`, `author_timeout`, `seat_failed`,
`unsupported`, `review_incomplete`, `time_exhausted`, `accepted`, `interrupted`
and `error`.

## Benchmark

`benchmark-report.json` uses schema `agent-ee.design-benchmark/1`. Designs run in
filename order, one at a time. One design's error, before or after its report
exists, becomes a row with status `error` and the reason; the other designs
still run. The summary records the run identity under `run`: the UTC start
time, a digest of every file of the imported agent-ee package and the checkout
HEAD when there is one, the packaged kicad-route pin, the author, the bounds,
the review profile and the roots.

Every row has the same keys, with null where a value does not apply: `name`,
`requirements_sha256`, `status`, `reason`, `attempts`, `attempts_to_accepted`
(null unless accepted), `duration_s`, `report`, `refusal_classes`,
`failed_checks`, `placement_findings`, `failing_stages`, `untraced_requirements`
and `outline_utilisation`. A row's status is the design's final state, or
`error`, `interrupted` (the design in flight when the run stopped) or `not_run`
(a design not started).

The benchmark ends `complete` (every design has a row from its own run, which
can be a failure), `stopped_seat_failed`, `stopped_runtime_refused`,
`interrupted` or `error`.

## Exit codes

| Exit | When |
|---|---|
| `0` | `generate`: the design is accepted. `benchmark`: the benchmark is `complete` and every design is accepted. |
| `1` | The command returned a final report that is not that: any other final state of `generate`, or any other benchmark outcome. Also an unexpected internal error of a type the CLI does not handle, which Python reports with a traceback; any report it reached is retained with status `error`. |
| `2` | The CLI printed `agent-ee: MESSAGE`. This is a refused option or, for `generate`, a refused requirements file, both before any output. It is also any internal `ValueError`, `OSError`, `KeyError` or `TypeError` that escaped the command, for example from a phase; any report it reached is retained with status `error`. In a benchmark, one design's error is a row, not an exit. |
| `130` | SIGINT or SIGTERM interrupted the run; its reports are final. |

Each row reports a final-coordinate outline utilisation proxy: the projected
union of linear courtyard bounding boxes, divided by the stated rectangular
outline area. This is not exact polygon occupancy, routing density or proof that
the outline can shrink. Missing native candidates are explicitly unavailable.

## Stopping a run

Every SIGTERM, as sent by `docker stop` or `native-admit`, interrupts the run as
an operator's SIGINT does, whatever handler SIGINT has. A caller's own SIGINT
handler is neither called nor replaced, and the previous SIGTERM handler is
restored when the command returns. A SIGTERM that arrives while a phase or seat
is being launched is held until the launch returns its process, whatever handler
SIGINT has, so that process is stopped too. While a final record is written,
SIGINT and SIGTERM are held and arrive right after it. Every report is finalized: the
attempt in flight is `interrupted`, the generate report is `interrupted`, the
benchmark lists the design in flight as `interrupted` (or with its own final
state if it had just finished) and every design not started as `not_run`, and
the CLI exits 130. This handling is installed only when the command runs on the
main thread.

Each capture, compile, build and review phase runs as a host process in its own
session and process group, whose id is the phase's own PID. On a stop or a phase
timeout that one group receives SIGTERM and, 5 seconds later, SIGKILL; when a
phase finishes, whatever is left in its group is killed. Nothing is signalled by
a process listing, and no other process is signalled. The author sandbox is
killed with the seat call, and it dies with the coordinator (`bwrap
--die-with-parent`).

Limit: a descendant that leaves the phase's process group or session (a
sandbox launcher or host run that starts its own session does) can outlive a
stopped run outside a container. In the documented container deployment,
container teardown ends it.

Limit: a SIGTERM that arrives in the few statements between the end of a
phase's launch and the start of its supervision in `native_run` is raised
before supervision can stop the new group. The run is still reported
`interrupted`, but outside a container that phase group can keep running after
the coordinator exits. Container teardown ends it in the documented deployment.

A SIGKILL of the coordinator cannot be handled: its reports stay `running`, and
its phase groups are not stopped. In the documented container deployment,
container teardown ends every process.
