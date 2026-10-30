# Reviewer profiles and model selection

Full reviews require an explicit profile; there is no implicit reviewer or
adjudicator model. Pass `--profile profiles/claude.json`, `profiles/codex.json`,
or `profiles/agy.json`, or embed the object under `review_profile` in the scope.
These three profiles are explicit options. The [recorded evaluation](../evidence/MODEL-SELECTION.md)
supports a provisional Codex starting configuration, with material adjudication
and coverage limits; it does not establish a general model ranking.

```sh
agent-ee review fixtures/rc/control.scope.json \
  --profile profiles/codex.json --out build/control-codex --max-seconds 1200
agent-ee review scopes/donor-case-alias.json \
  --profile profiles/agy-baseline.json --out build/donor-agy --max-seconds 3600
```

The external profile path and SHA-256 are recorded. Its parsed content is part
of candidate identity. Editing it invalidates live-source verification; resume
recaptures its current contents. Historical evidence is not silently upgraded.
Changing only a profile cannot count as a repair: correction closure also requires
changed captured input bytes and fresh passing checks/review.

Old scopes using top-level `reviewers` must migrate to `review_profile` or pass
an explicit profile. Checks-only runs do not require a profile.

## Format

```json
{
  "schema": "agent-ee.profile/1",
  "name": "explicit-mixed-review-v1",
  "tool_mode": "investigate",
  "reviewers": [
    {"role": "circuit", "harness": "codex", "model": "gpt-6.1-sol", "effort": "high", "timeout_s": 300},
    {"role": "physical", "harness": "agy", "model": "gemini-4-argon-high", "effort": "high", "timeout_s": 300}
  ],
  "adjudicator": {"harness": "claude", "model": "claude-fable-5", "effort": "high", "timeout_s": 300}
}
```

This example demonstrates configuration, not a claim that this allocation is
best. The `rf` role is the [dedicated RF reviewer](rf-review.md): its brief is
mandatory (a `lens` only adds emphasis) and it returns a structured
`rf_assessment`. A design whose captured sources carry RF indicators cannot
complete without it. [`profiles/pod-review.json`](../profiles/pod-review.json)
adds it for the telemetry pod; see [budget implications](rf-review.md#profiles-pod-wiring-and-budget). Roles must be distinct safe lowercase IDs; `adjudicator` is reserved.
At least two reviewers are required. Optional reviewer `lens` replaces its
built-in circuit/physical lens. The [placement purpose brief](purpose-review.md)
remains mandatory by default for every initial role even with a custom lens; supported
area and routability suggestions remain advisory beside correctness findings.

For an explicitly captured source qualification, a profile may declare
`"purpose_review": {"mode": "source_topology", "reason": "<nonempty scope rationale>"}`.
The finite modes are `board` and `source_topology`; omission preserves board review.
Source mode requires a scope/3 verification-only symbols subject, only `netlist`,
`parts_subset` and topology-only `hardware_interface` checks, and captured sibling
`block-contract.json` declarations binding the exact source-phase criteria to the
immutable baseline. Adaptation checks cannot execute or earn source credit in this
mode. Every deferred requirement, condition and source remains captured and is
listed as mandatory and not evaluated. Board/package acceptance scopes and mixed
check families refuse this mode. The required board remains captured context;
source purpose coverage establishes no electrical, thermal, layout, return-path,
supplier or board qualification. A custom lens alone cannot select this exception.
Harness, native model, effort and timeout are
required for every reviewer and adjudicator. Timeouts are finite 1–3600 seconds,
further bounded by the run deadline. Supported effort values are low/medium/high/
max for Claude, and low/medium/high/xhigh/max for Codex and Agy. Agy's model suffix
must match its effort. Local validation does not prove provider availability.

## Tools and workspaces

`investigate` is the default. Reviewers can read the complete saved candidate,
run installed tools and calculations, and fetch sources. The initial text packet
is an entry point, not the entire available evidence. Tool calls and outputs
remain in native harness logs. Reviewers are instructed to save experiments,
retrieved references and command/measurement notes under `/work/experiments`;
validated citations can name `review-work/<role>/experiments/<file>`.
The packet's `citation_files` inventory names coordinator evidence formats and
locator choices. Reviewers use line ranges for native KiCad S-expressions and
other text, JSON pointers for JSON, and XPath for XML. They must omit unused
locator fields. [Citation errors](result-reference.md#structured-citations)
identify the file and supplied/required kinds for bounded output resubmission.
The archived citation schema binds each captured path to locator kinds classified
from its actual bytes, including JSON/XML documents with unconventional suffixes.
New experiments use line citations in the caller's own canonical experiment path;
extract binary evidence to UTF-8 text first. The adjudicator may additionally cite
already retained reviewer experiments using their actual formats. Schema admission
does not establish that a locator resolves: the existing semantic validator checks
every supplied locator, range, containment and link. Formatting retry uses the
same schema and preserves finding IDs; no citation is automatically repaired.

Repeated captured directories with identical complete filename sets share a
factored literal path expression. This preserves exactly the same finite path
set and locator-format partitions. The complete producer schema must still fit
the 120 KiB transport limit, including RF and adjudicator fields; an inventory
that cannot fit is refused without dropping paths or increasing that limit.
The provider copy omits only the finite path patterns, retaining path lengths,
types, locator branches and the short own-experiment pattern. Large finite
patterns failed the subscription transport even with a tiny prompt. Local
admission checks exact raw path membership and locator field sets against the
complete archived schema before semantic resolution. Successful attempt replay
repeats that check against its original schema and response. Existing but
uncaptured files and peer experiments cannot enter through the simpler provider
schema; the adjudicator retains only its explicitly captured peer evidence.

The existing Bubblewrap mounts protect `/candidate` and separate initial
reviewers. `/evidence/work` and `/evidence/checks` expose completed native results
read-only, and `/evidence/packet.json` is the complete review packet. Each
reviewer has its own writable `/work`. The inline packet target is 400,000
characters, leaving room for instructions and other evidence (Codex refuses
turns over 1,048,576 characters). For larger packets, an `investigate` reviewer
or adjudicator gets that packet with
identity and the smaller sections inline and its largest sections replaced by
JSON-pointer references, with item indexes, into the mounted file. The prompt
states the file and packet hashes. Findings and initial reviews stay inline for
the adjudicator; the packet target does not bound the entire prompt. A
`fixed_packet` reviewer has no tools, so it always receives
the complete packet inline. The adjudicator also gets
previous experiment directories read-only under `/experiments/<role>`. No new
hermetic tool environment is built: system tools, libraries and networking are
used. Codex runs without a second inner filesystem/network sandbox; Bubblewrap
continues to enforce the read-only candidate. A live fetch/write probe verified
HTTP 200 for a source document and refusal of a candidate overwrite. Models may
still omit useful investigations; inspect coverage and logs.

`fixed_packet` disables Claude and Codex tools for a deliberately limited
comparison. Agy lacks a complete tool-disable flag: it uses plan mode and any
observed tool activity invalidates its response. This mode is not the normal
engineering workflow. The model-selection experiment uses `investigate`.

## Subscription adapters

| Harness | Credential lookup | Execution and response |
| --- | --- | --- |
| Claude | `~/.claude/.credentials.json` | Safe mode, explicit ordinary tools, no user hooks/settings/MCP; JSONL tools and terminal structured response |
| Codex | `~/.codex/auth.json`; `AGENT_EE_CODEX_AUTH_HOME` selects an existing auth directory explicitly | ChatGPT login required, user config/rules ignored, ephemeral session, ordinary tools plus live web search, native JSONL and strict response schema |
| Agy | `~/.gemini/antigravity-cli/antigravity-oauth-token` | Fresh settings/home, native tools, JSONL and terminal `structured_output`; requested model/session checked |

Credentials are read-only mounts, not copied to artifacts. The parent environment
is cleared. There is no API-key/OpenRouter route, automatic account switch or
model substitution. Initial reviews run concurrently, at most two calls at once.
Call admission and harness-failure publication share a coordinator lock: a call
admitted before a peer reports failure is already in flight and may settle.
Unavailable, failed, timed-out or interrupted calls prevent admission of further
same-harness reviewers in the run, including authentication/quota failures.
Queued affected roles retain `not_run` records. No admitted peer is cancelled
because another role fails; each retains its own logs and partial experiments.
Owner SIGINT cancels the whole initial-review operation: queued roles remain
`not_run`, active process groups are killed, and each role's execution logs and
partial experiments are retained before interrupted coordinator finalization.
Every `review-executions/<role>.json` publication includes its role, including
settled structured calls before the reviewer worker returns (AGENTEE-461).
The shared JSON writer fsyncs a same-directory temporary file, atomically
renames it into place, then fsyncs the directory. Cancellation before the
rename leaves the previous complete record or no record; after the rename,
readers see the complete replacement. Retained attempts and execution failures
keep their existing meanings.
Cancellation is checked during launch and at 50 ms intervals while waiting;
output draining and worker cleanup have one- and two-second bounds respectively.
A cleanup overrun is retained explicitly rather than reported as a clean review.
A malformed initial response stays invalid while the other role may still run.
Campaigns can request a separately recorded [bounded resubmission](campaign-guide.md#bounded-output-resubmission)
with the same profile and exact validation error; standalone CLI reviews keep a
single call.
Returned reviews and execution records follow profile role order, regardless of
completion order. Every call is bounded by its profile timeout and the remaining
shared deadline at admission. Adjudication begins only after all initial roles
settle and every declared role has a valid review; unfinished required roles
keep the result incomplete. The comparison campaign stops that harness after
any case with invalid execution.
Other independent harness campaigns may finish. Retain the failed attempt and
fix access before starting a new run.

Codex's host launcher has coordination hooks. The adapter therefore invokes the
installed native binary, with its sibling `codex-code-mode-host` for tools.
On this Linux x86-64 installation it discovers the binary beneath
`~/.npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/`.
For another layout, set `AGENT_EE_CODEX_BINARY` to the native ELF executable.
`AGENT_EE_AGY_BINARY` can select Agy's native executable. Script wrappers are
refused for these two adapters. Both binary and Codex tool-host hashes are
recorded. This is installation-specific discovery, not a portable package lock.

Claude records observed model metadata and usage; Agy checks its reported model
and conversation identity. Codex JSONL does not attest the serving model, so its
record explicitly marks observed model unknown and retains the requested model,
command, binary hash, session and reported usage. Do not misrepresent requested
identity as server attestation.

## Selection evidence and reruns

[The preregistered plan](model-evaluation-plan.md) specifies independently
justified cases, held-out coverage, repeated trials and separate adjudication.
Run the actual comparison with:

```sh
python3 scripts/compare_reviewers.py --out build/model-comparison
```

Each run records native checks, two initial judgments and execution records.
These are intentionally **incomplete reviews** because comparison does not run
adjudication. Per-harness progress files distinguish complete comparison trials
from acceptance. Inspect engineering reasoning against `expectations.json` and
the plan; no keyword classifier or model majority supplies the answer key.

A changed model, effort, prompt, harness or tool policy requires new evidence
before transferring a recommendation. Model-family diversity can reduce shared
misses, but it is not proof of independent engineering verification.

The donor profiles explicitly allow 900 seconds per call; use a 3600-second run
budget. This did not guarantee completion: see the retained Claude/Agy timeouts
and rejected donor adjudications in the evaluation. Raising a budget creates a
new attempt and must not overwrite or rescore the failed attempt.

## Profiles in campaigns and empirical re-review

A campaign embeds the complete review profile object in its configuration and a
separate `implementation` definition with the adjudicator's harness/model/effort/
timeout fields. It writes the review profile to the campaign root and captures
that allocation in each run. The implementation uses investigative tools even
if the review profile selects `fixed_packet`. It is the sole candidate proposal
writer; its native model call still uses the same subscription sandbox and
failure policy.

Scope/2 prompts and schemas add the full immutable baseline and requirement/check
witnesses. This does not authorize a reviewer to edit requirements or replace
coordinator checks with prose. The adjudicator can accept only executable check
findings under that contract; see [scope/2 decisions](result-reference.md#version-2-witnesses-and-evidence-seal).

Campaign feedback passes its captured campaign profile. Standalone `bench-review`
preserves the source candidate's captured profile, including one supplied through
`--profile FILE`: capture embeds its parsed content in the scope. Bench re-entry
does not reload the external file. The bench commands have no profile override
flag. Initial reviewers receive tool-only empirical source
predictions and raw samples, without prior reviewer judgments. The implementation
agent alone receives the broader campaign convergence history when further
repair is allowed.
