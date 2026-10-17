# Architecture and lifecycle

agent-ee keeps review execution in a small standard-library Python application.
The skill tells an existing coding agent how to invoke it; the CLI owns capture,
native checks, fresh model calls, records and acceptance interpretation.
FOME-MC-specific input choices stay in scopes and references.

## Data flow

```mermaid
flowchart TD
  A[Explicit scope and source files] --> B[Copy and hash candidate]
  B --> C[Isolated native checks]
  C --> D[Persist raw evidence and interpreted checks]
  D --> E[Construct bounded evidence packet]
  E --> F[Fresh circuit judgment]
  E --> G[Fresh physical judgment]
  F --> H[Retain every finding]
  G --> H
  D --> H
  H --> I[Fresh evidence adjudication]
  I --> J[Verify source, snapshot, policy and runtime]
  J --> K[Result and report]
  K --> L[Authorized saved change in separate candidate]
  L --> B
```

The two initial judgments are separate contexts. They execute sequentially in
the current adapter, not concurrently. The adjudicator receives both only after
initial review. The diagram's branches represent information separation, not
parallel model execution or proof of epistemic independence.

## Modules and seams

| Module | Responsibility |
| --- | --- |
| [candidate.py](../src/agent_ee/candidate.py) | Resolve explicit sources, select files, copy/hash, verify snapshot/current source |
| [executor.py](../src/agent_ee/executor.py) | Construct Bubblewrap view; run/cancel subprocess groups; persist raw execution |
| [checks.py](../src/agent_ee/checks.py) | Hierarchy validation, native report interpretation, criteria and passive deck generation |
| [geometry_probe.py](../src/agent_ee/geometry_probe.py) | Saved pcbnew geometry extraction inside the sandbox |
| [reviewers.py](../src/agent_ee/reviewers.py) | Bounded packets, subscription adapter, initial lenses and adjudicator prompts/schemas |
| [findings.py](../src/agent_ee/findings.py) | Validate review/citation records, collect all claims, adjudicate without voting, closure invariants |
| [storage.py](../src/agent_ee/storage.py) | Canonical identities, digests and atomic JSON writes |
| [cli.py](../src/agent_ee/cli.py) | Command parser, work lifecycle, result projections, freshness and operator mutations |

There is no database, daemon, queue service, automatic repair engine or hidden
remote agent coordinator. Run directories are the persistence interface.
Installing another model CLI does not add an adapter. The repository does not
vendor the concurrent FOME-MC toolchain or execute it from its changing checkout.

## Capture and isolation

Capture hashes selected source files, copies each file, verifies the copied
digest, then compares current source selection/hashes and scope bytes with the
initial state. Candidate identity covers parsed scope and captured file hashes.
Source revisions are recorded separately. This detects many races but is not an
atomic multi-file filesystem snapshot.

Each subprocess receives the captured inputs read-only at `/candidate`, package
source read-only at `/app`, and its own writable scratch at `/work`. Native tools
have no network. Review inference has network and a read-only credential mount,
with ordinary tools enabled and fresh harness settings. Other reviewers' scratch and the source checkout
are absent. These are OS-enforced child-process permissions; the privileged host
remains outside this boundary. Details are in [security](security.md).

## Persistence and completion

The run starts with an explicit `running` result. Each check is written to its
own JSON record immediately after interpretation; each validated review and
adapter outcome is also persisted. Check/review collection and result/report
projections are saved at lifecycle boundaries. On a caught interruption/error,
the CLI recovers durable work and inserts unknown placeholders for unfinished
checks/roles. It does not manufacture a successful empty review.

Finalization verifies captured and current source identity, loaded/current
Python policy, and runtime fingerprints. A complete process state can still
produce incomplete outcome when required execution coverage is unknown.
The [result rules](result-reference.md) define acceptance separately from process
completion. Provider schema failure, native malformed output and solver failure
cannot become an electrical pass through aggregation.

An adjudication appends an event and regenerates projections. Correction
linkage additionally binds a distinct changed candidate, unchanged criteria and
requirement bytes, fresh affected checks, and a wholly satisfying new review.
The semantic relationship between repair and finding still needs engineering
inspection. The CLI does not automatically derive repair impact.

## Freshness strategy

The implementation conservatively reruns rather than selectively reusing old
checks. `resume` preserves the old run and starts a fresh capture/execution.
Changing relevant selected bytes invalidates normal freshness even if the source
Git revision stays unchanged. Changing any Python module or fingerprinted tool
invalidates prior policy/runtime freshness. An unselected dependency can still
escape this inventory; input completeness is an explicit operator responsibility.

Artifact inventories bind retained raw evidence and projections to recorded
hashes. They are consistency checks on trusted storage, not an independent
signing system against an owner who can rewrite all records. Historical
`--snapshot-only` verification preserves the original candidate while skipping
live-source availability. It still checks implementation and runtime equality.

## Design tradeoffs

The [decision record](decisions.md) gives the original reasoning. The practical
costs are intentional and visible: full reruns spend inference again; reviewers may omit useful tool investigations;
explicit model profiles still can share blind spots; and whole-review closure is
stricter than closing one local defect while unrelated blockers remain.
Broader analyses need explicit check/model/extraction additions and their own
independent expectations, not longer unsupported reviewer assertions.
