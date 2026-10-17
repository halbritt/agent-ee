# agent-ee

`agent-ee` reviews a captured KiCad candidate with native checks and complementary
fresh agent judgments. It retains evidence, unresolved claims and correction
history. Review never changes the source design.

The [full manual](docs/README.md) covers installation, operator workflows, every
CLI command and scope field, native/engineering checks, result formats,
isolation, troubleshooting, extension and evidence reproduction.

```sh
cd ~/git/agent-ee
./scripts/install.sh
agent-ee doctor
agent-ee review scopes/donor.json --profile profiles/claude.json --out build/donor-review --max-seconds 1200
agent-ee inspect build/donor-review
agent-ee verify build/donor-review
```

The installer links the CLI into `~/.local/bin` and its skill into existing agent
discovery locations. It refuses to replace unrelated files. No Python packages
are needed; `/usr/bin/python3` must provide the installed KiCad `pcbnew` bindings.
The editable Python package entry point is also available from `pyproject.toml`.

Source remote: [halbritt/agent-ee](https://github.com/halbritt/agent-ee), private.
Clone and authentication prerequisites are in [installation](docs/installation.md).

Select every reviewer and the adjudicator explicitly using a
[versioned profile](docs/reviewer-profiles.md). There is no implicit model default.

## Prerequisites and integration

The demonstrated environment is Linux with KiCad 10.0.7, ngspice 42, Bubblewrap
0.9.0 and Claude Code 2.1.302 authenticated through Claude Max. The CLI uses only
explicit Claude, Codex or Agy subscription profiles; an auth/quota error stops that call
and leaves the review incomplete. It does not fall back to another provider.
Native prerequisite/version probes are separate from integration proof.

KiCad supports JSON ERC/DRC and schematic parity checks; this project records
their native output and validates coverage instead of interpreting exit 0 alone
as acceptance. See the [KiCad 10 CLI manual](https://docs.kicad.org/10.0/en/cli/cli.html).
ngspice conclusions require applicable models, explicit stimulus and finite,
present measurements; see its [manual index](https://ngspice.sourceforge.io/docs.html).

The concurrent FOME-MC setup supplied useful validated wrapper patterns and an
RC fixture. `agent-ee` adapts those methods without importing the changing live
toolchain or writing to its build directory. KiBot and kicad-happy remain
available specialist tools; the initial bounded checks do not require them.
Their presence does not count as exercised review coverage.

## Declaring a scope

Use an `agent-ee.scope/1` JSON document. `docs/contracts.md` describes the fields;
the examples under `scopes/` and `fixtures/` are executable configurations.
Paths are relative to the scope file unless absolute or beginning with `~`.
`inputs` declares source trees/files and their destinations inside the candidate.
Everything selected is copied and hashed, including uncommitted files. Declare
external libraries explicitly at their effective native library-table paths.
Undeclared live symlinks and overlapping destinations are refused.

The operator owns input completeness. Include requirements, models, library
tables, libraries, native project settings and custom rules that affect the
scope. Omitted external behavior remains unreviewed. Native KiCad files remain
authoritative; exported connectivity and geometry retain source paths/hashes.

`packet_inputs` chooses relevant native text for the initial agent reviewers.
`requirements` supplies captured requirement files. Text limits and binary
omissions are recorded in the packet and report. A scope should be narrow enough
for its declared evidence and coverage; broaden it by adding checks and sources,
not by changing the meaning of a passing result.

## Results and failure handling

Each fresh output directory contains the captured `inputs/`, `candidate.json`,
native `work/` outputs and commands, validated `checks/`, initial `reviews/`,
review execution records, `packet.json`, disposition `events/`, `result.json`
and `REPORT.md`. Findings remain traceable to the saved candidate. Initial
reviewers see the same evidence packet under different lenses and cannot read
each other's output. The adjudicator sees all claims and must explain each
disposition through evidence. No majority vote removes a finding.

`accepted` means an established defect. `noted` preserves a supported minor/info
observation that violates no declared criterion; it does not block satisfaction
of those criteria. Major/blocking concerns stay deferred until evidence resolves
them. Defects cannot be hidden as noted observations or duplicates of them.

| Result | Meaning |
| --- | --- |
| `criteria_satisfied` | Applicable declared execution and reviews completed; no accepted defects or unresolved findings remain. Noted observations may remain. |
| `defects_or_unresolved` | Review completed with defects or open questions. |
| `incomplete` | Execution, evidence, review, adjudication or budget left work unfinished. |

Check `execution` and design `outcome` are separate. A successful tool reporting
a violation is `ok` / `defect`; missing tools, malformed output, timeout and
solver failure are unknown coverage. A checks-only run is always incomplete as
an adversarial review. For `review` and `resume`, exit 0 means
`criteria_satisfied`, exit 1 another review outcome, and exit 2 an invocation
refusal. `verify` checks freshness: exit 0 means its integrity/freshness checks
passed, including for an honestly incomplete or defective review. Check the
printed outcome or `result.json`; freshness alone is not acceptance. A freshness
refusal exits 2.

Inspect partial work with `agent-ee inspect RUN`. Interrupting or exhausting the
wall-clock budget preserves useful completed records. `agent-ee resume RUN
--out NEW` recaptures and reruns all checks and reviewers. This intentionally
spends more work than selectively reusing results, while avoiding stale evidence.
No automatic retries or autonomous source repairs occur.

`verify RUN` checks the captured input identity, current declared sources and
scope, implementation hashes, and retained evidence hashes. Source changes make
old evidence historical, never acceptance of the new source. `verify RUN
--snapshot-only` checks a preserved historical candidate without requiring the
old source tree still exist unchanged.

`packet_artifacts` optionally names required generated evidence to place early
in a reviewer packet. Each path must be among the evidence actually produced by
this run's checks. A missing required artifact makes the run incomplete before
reviewers are invoked; correct the scope or failed prerequisite and use a fresh
run. With no such declaration, failed checks remain visible in the packet and
reviewers can investigate the evidence that is available.

## Isolation guarantees

Every native tool and reviewer subprocess runs in Bubblewrap. Captured inputs,
application code and system tool directories are mounted read-only; only that
invocation's scratch is writable. The sandbox omits the host home directory,
other reviewers' work directories, host `/tmp` and runtime sockets. Native tools
have no network. Reviewers have ordinary investigation tools and network access, with fresh settings
and separate workspaces. The installed system tools are used; no hermetic environment
is claimed. The subscription credential is a read-only
mount during the call; its contents are never copied into the retained run.

This protects inputs from these subprocesses. It does not constrain the owner,
another privileged host process, a compromised provider or kernel. Before/after
hash checks detect captured/source changes at verification boundaries; they do
not make a multi-file capture an atomic filesystem transaction. A stable copied
candidate is the thing reviewed. Arbitrary intentionally hostile inputs and
undisclosed dependencies are outside the demonstrated assurance.

## Findings and correction closure

Read original source/check evidence when disputing a finding. Schema validation
checks provenance and record consistency; it cannot prove an electrical claim.
Use the decision format in `docs/contracts.md` and run:

```sh
agent-ee adjudicate RUN decisions.json
agent-ee close-correction OLD_RUN NEW_RUN \
  --finding 'check/response' --checks 'erc,drc,response' \
  --reason 'Saved component value restored; fresh native and response checks pass.'
```

Closure requires a distinct candidate, an accepted old finding, unchanged
requirements/check criteria, a completed passing new review and fresh passing
affected checks. It appends a correction link; the old defect remains true of
the old candidate. For repair, copy to an isolated disposable candidate and
change its native files only with authorization. The CLI never rewrites native
circuits, performs general redesign, orders parts or approves fabrication.

## Extending and testing

Add a check kind in `checks.py`, its explicit parameters/limits in a scope, and
independent expected outcomes and failure cases in tests. Preserve native/source
locators, simulator/model limitations and execution-vs-design status. Reviewer
instructions, executable rules and limits are part of run identity; changing
them invalidates acceptance under the old implementation.
See [coverage and extension instructions](docs/coverage.md) for current check
parameters, reviewer configuration, packet limits and unsupported coverage.

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

The [evaluation plan](docs/evaluation-plan.md) records the independent expected
control and seeded responses before code. Recorded demonstrations, the donor
baseline and implementation decisions are indexed in
[recorded acceptance evidence](evidence/README.md) and [decisions](docs/decisions.md).
This small exercise demonstrates observed behavior; it is not a general hardware
correctness benchmark. Prototype and bench validation remain necessary.

Recorded input trees are stored in compressed archives alongside readable reports
and check/reviewer records. Restore a group in a fresh checkout with
`python3 scripts/evidence_archive.py restore rc-verified` (or `donor-verified`).
Restoration checks the archive checksum and refuses to overwrite changed files.
The archive manifest pins each group's implementation commit and recorded
outcomes; historical failure attempts remain incomplete. Use a matching source
version when replaying their integrity checks. To run the actual control, seed
and disposable repair demonstration again, use
`python3 scripts/demo.py --profile profiles/claude.json --out build/my-demo` from the repository root.
