# Result and evidence format

Version strings identify record formats; they are not signatures or proof of
correct electrical conclusions. There is no automatic migration or import of
old acceptance. JSON records are written with sorted keys, indentation, finite
numbers and atomic temporary-file replacement. Identity JSON uses sorted keys
and compact separators before SHA-256 hashing.

## Run directory

```text
RUN/
  candidate.json                 selected input manifest and scope
  inputs/                        exact captured source/library/requirement bytes
  work/CHECK_ID/                  isolated native scratch and raw evidence
  checks/CHECK_ID.json            interpreted check result
  packet.json                    bounded evidence supplied to initial reviewers
  reviews/ROLE.json              validated initial judgment
  review-executions/ROLE.json     role-level adapter outcome
  review-work/ROLE/               raw harness stdout/stderr/execution
  review-work/adjudicator/        raw adjudicator execution
  adjudicator-execution.json      interpreted adjudicator outcome
  events/                        adjudication and correction history
  result.json                    current projection and integrity inventory
  REPORT.md                      human-readable projection
  error.log                      traceback when the CLI crash boundary catches an error
```

Some paths do not exist if capture or execution stops early. Each native work
directory normally has `execution.json`, `stdout.log`, `stderr.log` and combined
`console.log`. Native programs can also produce configuration/cache files in
their scratch. The reviewer home is removed after its invocation; credential
contents are never copied into the retained run.

## Candidate and run identities

`candidate.json` has schema `agent-ee.candidate/1`:

| Field | Meaning |
| --- | --- |
| `scope` | Full parsed scope document |
| `files` | Candidate-relative path to SHA-256 of each captured file |
| `sources` | Candidate-relative path to resolved original source path |
| `source_revisions` | Input destination to source Git HEAD, or null |
| `scope_path`, `scope_sha256` | Original scope path and exact file digest |
| `candidate_id` | SHA-256 identity of the parsed `scope` and `files` mapping |

Candidate identity includes input path mappings and scope settings. A changed
source path in the scope can create a distinct candidate even if bytes match.
Scope whitespace can change the exact scope-file digest without changing the
parsed-scope identity; normal source verification still notices that edit.
Git revision metadata is not the candidate hash. Changed bytes matter even when
HEAD stays unchanged. A fresh run also gets a UUID `run_id`; separate executions
of the same candidate have distinct run identities.

`packet_sha256` identifies the constructed packet before adding its own digest
field. `packet_file_sha256` in the result hashes the actual retained packet file.
Review records must match candidate, run and packet IDs exactly.

## Evidence packet

`packet.json` has schema `agent-ee.packet/1`. It retains `candidate_id`, `run_id`,
scope text, declared `criteria`, `declared_limitations`, interpreted `checks`,
`artifacts`, `packet_limits` and `packet_sha256`. Each supplied text artifact has
`path`, `sha256`, `bytes_total`, `bytes_included`, `extent` (`whole` or `prefix`)
and `content`. The digest identifies the complete source file; `content` can be
only its prefix. Binary omissions have notices rather than readable content.
Packet identity cannot establish that omitted data was inspected.

## Result projection

`result.json` uses `agent-ee.result/1` and contains:

| Field group | Fields |
| --- | --- |
| Identity and scope | `run_id`, `candidate_id` when captured, `name`, `scope`, `previous_run` |
| Lifecycle | `state`, `outcome`, `started_utc`, `finished_utc` when finalized |
| Work inventory | `expected_check_ids`, `expected_review_roles`, `checks`, `reviews`, `review_executions` |
| Findings | `findings`, `adjudicated`, `limitations` |
| Freshness | `policy_files`, `runtime_files`, `artifact_hashes`, `manifest_sha256`, `packet_file_sha256` when available |
| Diagnostics | `error` when present; `prior_errors` when a manual update completes a previously failed projection |

State is `running`, `complete`, `incomplete` or `interrupted`. State `complete`
does not imply acceptable design or complete engineering coverage. Outcome is:

| Outcome | Interpretation |
| --- | --- |
| `incomplete` | Run not finalized with all required execution/reviews/adjudication, or some required check/review has unknown/failed coverage |
| `defects_or_unresolved` | Required execution completed, but a declared check failed or a finding is accepted, unresolved or deferred |
| `criteria_satisfied` | Required execution/reviews/adjudication completed, all checks are pass/info, and no blocking dispositions remain |

At least two initial reviews are required. Every declared check and reviewer
role must appear. Non-defect noted observations can remain in a satisfying
review. An actual executable check failure blocks satisfaction regardless of an
LLM's favorable prose or later attempted disposition.

## Check record

Fields are `id`, `kind`, `candidate_id`, `execution`, `outcome`, `summary`,
`evidence`, `details`, `limitations`. Evidence is normally a list of run-relative
artifact path strings; collection also understands evidence citation objects.
Details are check-specific facts, including native counts, source board digest,
geometry, generated element identities and measured bounds as applicable.

| Execution | Meaning |
| --- | --- |
| `ok` | The handler obtained the evidence it requires and interpreted the check |
| `failed` | Invoked tool/provider or solver failed |
| `unavailable` | Required executable/credential or launch facility unavailable |
| `timeout` | Invocation exceeded its time allowance |
| `malformed` | Required report/review/trace/identity or coverage was invalid |
| `not_run` | Prerequisite/result absent or work was never invoked |
| `interrupted` | Operator/deadline interrupted work or prevented a check from starting |
| `unsupported` | Requested kind, model interpretation or analysis is not implemented |

For check records, any execution other than `ok` requires `outcome=unknown`.
Successful checks use `pass`, `defect` or informational `info`. Reviewer adapter
records use their own diagnostics and may not have check-style `outcome` fields.
An executable missing inside Bubblewrap can appear as a failed wrapped process
rather than a direct `unavailable` launch exception; consult its console.

Execution records retain original `argv`, full `sandbox_argv`, `timeout_s`,
`execution`, nullable `exit_code`, `duration_s` and `console_sha256`. stdout and
stderr are separate; combined console concatenates stdout then stderr and does
not preserve interleaved timing.

## Initial review

Schema `agent-ee.review/1` has exactly these top-level keys:

`schema`, `candidate_id`, `run_id`, `packet_sha256`, `role`, `coverage`,
`limitations`, `findings`.

Coverage and limitations are lists of nonempty strings; empty lists are
structurally allowed. Findings may be empty. Each finding requires:

| Field | Meaning |
| --- | --- |
| `id` | Reviewer-local ID: letters/digits then letters/digits/underscore/dot/hyphen, at most 128 characters, no `..` |
| `title` | Concrete claim |
| `location` | Component/pin/net/UUID/design or verification location |
| `condition` | Applicable operating condition or criterion |
| `mechanism` | How the alleged failure occurs |
| `severity` | `blocking`, `major`, `minor`, or `info` |
| `assessment` | `defect` or `concern` |
| `evidence` | List of `{ "path": "...", "locator": "..." }` |
| `reasoning` | Relationship between condition, source evidence and conclusion |

Required text fields must be nonempty. Optional normalization fields are
`downgraded_from` and `validation_note`. A defect with no evidence is visibly
downgraded to concern, retaining why it was downgraded. Bad identities, invalid
paths, duplicate local IDs and malformed structures invalidate the review.
Merely having citations does not prove the claim is true or that the reviewer
received the entire cited document.

Citations must name actual regular files under `inputs/`, `checks/` or
`work/CHECK_ID/` or `review-work/ROLE/experiments/` inside this run. Absolute paths, backslashes, traversal and
symlink traversal are refused. Citation objects contain exactly `path` and a
nonempty `locator`; use a native UUID/pin/net, page/section, measurement name or
specific report item rather than a vague file-level claim. Reviewer prose alone is not independent engineering evidence; experiment citations
should identify the actual command, input, measurement or fetched source.

Adapter records identify the backend, requested model, subscription route,
execution/diagnostics, harness binary digest and, on successful parsing,
model-usage/session/API-duration metadata supplied by the harness. A requested
model name is not independent attestation of the provider's internal routing.

## Collected findings and decisions

Collection keeps every valid initial claim. IDs become `ROLE/LOCAL_ID`; failed
native criteria become `check/CHECK_ID`. Check defects default to blocking and
remain distinct from physical-failure claims. Findings begin unresolved and
retain their original evidence, source, assessment and attribution.

Manual decision files have schema `agent-ee.decisions/1`, exact `candidate_id`
and `run_id`, and a `decisions` list. Each item requires `finding_id`,
`disposition`, `reason`, `evidence`. Optional keys are `duplicate_of`, `checks`
and `previous_run`. This is a structural example requiring real identities,
an existing finding and engineer-checked counter-evidence before application:

```json
{
  "schema": "agent-ee.decisions/1",
  "candidate_id": "COPY_CURRENT_CANDIDATE_ID",
  "run_id": "COPY_CURRENT_RUN_ID",
  "decisions": [
    {
      "finding_id": "circuit/EXISTING_LOCAL_ID",
      "disposition": "dismissed",
      "reason": "Replace with the specific counter-evidence and its engineering implication.",
      "evidence": [
        {
          "path": "work/netlist/connectivity.json",
          "locator": "Replace with the exact component, population and net relationship."
        }
      ]
    }
  ]
}
```

| Disposition | Requirements and effect |
| --- | --- |
| `accepted` | Established `assessment=defect`, explanation and nonempty decision evidence; blocks satisfaction |
| `noted` | Evidence-supported minor/info concern with its own original evidence and decision citations; does not block satisfaction |
| `dismissed` | Explanation; a supported/alleged defect needs counter-evidence; native check defects cannot be dismissed |
| `deferred` | Explained unresolved uncertainty; blocks satisfaction; native check defects cannot be deferred |
| `duplicate` | Existing surviving canonical finding; self-links/cycles and hiding defects behind non-defect notes/dismissals are refused |
| `closed` | Library-level cross-candidate closure validation; same-candidate repair closure is invalid; CLI users should use `close-correction` |

Unsupported/downgraded allegations, major/blocking concerns and check defects
cannot be merely noted. Duplicate chains are validated, including their final
target's disposition; a vote cannot erase a singleton. Decisions append finding
events containing sequence, identities, reason and evidence. Current
`status`/`disposition`, `decision`, `disposition_reason` and `decision_evidence`
are projections; earlier events are retained.

## Correction events and integrity

Dedicated correction events use `agent-ee.correction/1` and retain finding ID,
reason, old/new candidate/run IDs, new run path, affected checks, target result
digest and changed input hashes. They are stored under the old run's `events/`
without rewriting its original defect into a pass. Absolute target paths aid
local inspection but may require manual location after moving evidence.

`artifact_hashes` covers all regular files under inputs, native work, check,
review and reviewer-execution directories, plus events and the adjudicator
execution record. The manifest and packet have separate hashes. `REPORT.md`,
the top-level `error.log` and `result.json` itself are not signed or externally
anchored acceptance evidence. Python policy hashes cover each `*.py` module in
`src/agent_ee`; runtime fingerprints cover resolved kicad-cli, ngspice, bwrap,
system Python, Claude/Codex/Agy binaries, the Codex tool host and the `_pcbnew` extension. They do not hash the entire
OS/shared-library image. See [security](security.md) for the guarantees this
provides and the host-modification limits.
