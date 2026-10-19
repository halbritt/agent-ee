# Recovering from schematic pin-resolution errors

AGENTEE-205 closes the producer-feedback gap from AGENTEE-173. In a staged
campaign with `writer_editable`, the coordinator can return an owned emitter's
unknown-pin or unaccounted-pin findings to the writer. The writer may repair only
its existing allowed inputs. A finding does not authorize choosing a no-connect
or changing an electrical requirement.

## Eligible failures

Recovery requires the exact `schematic_stage.configuration` emit declaration,
including command arguments, declared inputs, writes, artifacts and exit policy.
The receipt's source and mount hashes must match the recorded owned source tree;
a matching step name or plausible `emission.json` is insufficient. Policy hashes
still bind that source to the campaign's launch environment.

The failed receipt must have exit 1, no output, only its expected missing-output
violation, a completed version probe and an ordinary failed process. The retained
input chain must match every successful predecessor's adopted output. The
coordinator reruns the pure emitter against retained inputs and requires the
entire emission diagnostic to match. Only `resolution_unknown_pin` and
`resolution_unaccounted_pin`, with their `resolution_rejected` summary, qualify.

Other resolution problems, malformed records, changed libraries or tool identity,
timeout, cancellation, unauthorized edits and unrelated producer failures retain
the existing terminal behavior. Placement and routing do not enter this path.

## Attempt budget and inputs

The frozen stage `finding_retry_limit` bounds the total number of writer attempts
for one implementation turn, including the initial attempt. A limit of three
permits at most two additional design-repair calls. Each call retains its existing,
separate JSON schema resubmission allowance; schema resubmission still forbids
changing the design. The maximum launch count is the attempt limit multiplied
by `1 + max_schema_resubmissions`; execution failures do not receive a schema
retry. The campaign deadline bounds every call and producer run.
A previously failed intent hash stops recovery, even if the remaining attempt
budget would allow another call.

Each retry starts from the last reviewed candidate. Only the preceding writer's
allowed inputs are carried forward, taken from the first producer receipt's
input snapshot. Successfully adopted predecessor output is not carried into the
writer's authority boundary. The complete scheduled schematic sequence runs
again after the writer, including any preceding binding-fill producer.

Every writer call passes the existing `writer_editable` and immutable-input
guards. The guards apply during schema resubmissions and before producers. This
feature defines no second allowlist and does not relax stage adoption or done.

## Retained evidence and replay

The latest attempt remains at `implementations/N/`. A failed attempt that causes
a retry is atomically retired to `intent-retries/N/K/` before the next call. Its full tree hash
is anchored in the campaign state's optional `intent_retries[N]` list. Attempts
that end recovery remain the ordinary final incomplete implementation.

Each list entry has `path`, `tree_sha256` and `feedback`. Feedback uses
`agent-ee.intent-repair/1` and contains:

- `candidate_id`, the reviewed source candidate;
- `attempt` and `archive`, the failure's ordered custody location;
- `receipt` and `receipt_sha256`, relative to that archive;
- `emission` and `emission_sha256`, relative to that archive;
- `intent` and `intent_sha256`, the failing producer input;
- `findings`, the exact replayed diagnostic records.

The next attempt receives that record at `work/intent-feedback.json`, mounted as
`/evidence/work/intent-feedback.json`, and in its implementation prompt. Feedback
is advisory evidence. It is not an accepted check finding or successful output.

Before producers, `intent-producer-source.json` records
`agent-ee.intent-producer-source/1`: exactly `schema` and `files`, a relative
source-path-to-SHA256 map. Its Python module inventory must equal the frozen
campaign policy. The complete inventory hashes to the receipt's mount/source
identity. This preserves exact producer identity without depending on later
bytecode-cache creation or the continued existence of an old mount pathname.

Offline campaign verification checks archive inventory and hashes, each writer
launch, the frozen plan's failed receipt prefix, producer source identity,
input-chain continuity, the existing writer boundary, diagnostic rederivation,
feedback delivery, distinct failed intents and the attempt bound. It requires
the matching owned source installation, as ordinary campaign policy verification
already does; it launches no model or native producer during this replay.

Only the final successful attempt supplies an ordinary complete receipt index
and proposal for a fresh review. Failed attempts cannot satisfy `stage_done`. An interruption between attempts
may leave a verified final incomplete record with no following writer call.
Campaigns without retry archives retain their existing verification path.
