# Reviewer profiles and model selection

Full reviews require an explicit profile; there is no implicit reviewer or
adjudicator model. Pass `--profile profiles/claude.json`, `profiles/codex.json`,
or `profiles/agy.json`, or embed the object under `review_profile` in the scope.
These three profiles are evaluation candidates, not an established ranking.

```sh
agent-ee review fixtures/rc/control.scope.json \
  --profile profiles/codex.json --out build/control-codex --max-seconds 1200
agent-ee review scopes/donor-case-alias.json \
  --profile profiles/agy.json --out build/donor-agy --max-seconds 1200
```

The external profile path and SHA-256 are recorded. Its parsed content is part
of candidate identity. Editing it invalidates live-source verification; resume
recaptures its current contents. Historical evidence is not silently upgraded.
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
best. Roles must be distinct safe lowercase IDs; `adjudicator` is reserved.
At least two reviewers are required. Optional reviewer `lens` replaces its
built-in circuit/physical lens. Harness, native model, effort and timeout are
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

The existing Bubblewrap mounts protect `/candidate` and separate initial
reviewers. `/evidence/work` and `/evidence/checks` expose completed native results
read-only. Each reviewer has its own writable `/work`. The adjudicator also gets
previous experiment directories read-only under `/experiments/<role>`. No new
hermetic tool environment is built: system tools, libraries and networking are
used. Models may still omit useful investigations; inspect coverage and logs.

`fixed_packet` disables Claude and Codex tools for a deliberately limited
comparison. Agy lacks a complete tool-disable flag: it uses plan mode and any
observed tool activity invalidates its response. This mode is not the normal
engineering workflow. The model-selection experiment uses `investigate`.

## Subscription adapters

| Harness | Credential lookup | Execution and response |
| --- | --- | --- |
| Claude | `~/.claude/.credentials.json` | Safe mode, explicit ordinary tools, no user hooks/settings/MCP; JSONL tools and terminal structured response |
| Codex | `~/.codex/auth.json`; `AGENT_EE_CODEX_AUTH_HOME` selects an existing auth directory explicitly | ChatGPT login required, user config/rules ignored, ephemeral session, native JSONL and strict response schema |
| Agy | `~/.gemini/antigravity-cli/antigravity-oauth-token` | Fresh settings/home, native tools, JSONL and terminal `structured_output`; requested model/session checked |

Credentials are read-only mounts, not copied to artifacts. The parent environment
is cleared. There is no API-key/OpenRouter route, automatic account switch or
model substitution. Execution failures stop further same-harness reviewers in
the run; the comparison campaign stops that harness on any invalid execution.
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
