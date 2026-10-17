# Concern evidence requests and replay

These standalone operations make unresolved reviewer concerns actionable without
changing acceptance. They never append to a reviewed run, change a disposition,
install a check, or edit a baseline. Each `--out` must be fresh and separate from
its source run and other operation directories. Destination ancestry is checked
for retained run/operation records independently of unvalidated request fields.
Malformed requests, missing run locators and tampered locators cannot select a
nested output in retained evidence. Unsafe output paths are invocation errors
without receipts or directory creation; fresh independent output paths can retain
failed diagnostic receipts. Request export requires a settled
run that passes current policy/runtime and snapshot verification. Historical
scope/1 requests have `baseline_id: null` and acquire no baseline witness authority.

## Request export

```sh
agent-ee evidence-request RUN --finding circuit/question \
  --spec request-spec.json --script experiment.py --out NEW_REQUEST
agent-ee verify-evidence-operation NEW_REQUEST
```

The specification has exactly these fields:

```json
{
  "mode": "experiment",
  "requirement_id": null,
  "check_id": null,
  "condition": "1 V input step, nominal components",
  "observable": "voltage at 1 ms",
  "unit": "V",
  "rationale": "Explain the retained unresolved concern and missing measurement",
  "interpreter": "/usr/bin/python3",
  "resources": {
    "wall_seconds": 10,
    "cpu_seconds": 5,
    "memory_bytes": 134217728,
    "log_bytes": 65536
  }
}
```

Use an existing baseline requirement ID or explicit `null` for no existing
requirement. A check ID must be declared and mapped to that requirement for
baseline-bound runs, including scope/3 audited mappings. Other modes are
`declared_check` (requires `check_id`) and `new_check_proposal`. They export an
actionable `REQUEST.md`; they have no automatic replay adapter. Set interpreter
to `null` and resources to `{}` when no experiment is proposed. The Python script
is optional for these two modes, required for experiments, and limited to 1 MB.
Request JSON is limited to 2 MB. Requests preserve the complete concern and its citations, source record hashes,
script/runtime hashes and content-derived request identity. An author cannot
choose an origin role or substitute a check finding for an initial reviewer concern.

A concern matching an already failed check must still be dispositioned centrally
as a duplicate/deferred concern; only `check/ID` can become an accepted defect
through [normal sealed witness validation](scope-reference.md#version-2-baseline).
For fresh declared verification, use an ordinary `review SCOPE --out NEW_RUN
--checks-only`, retaining the complete ordered prerequisites. Do not run checks
inside a sealed run. Verify that the new candidate/baseline identities match;
changed inputs require a newly bound request. Checks-only work does not replace
fresh independent initial reviews and central adjudication for acceptance.

## Dedicated replay contract

```sh
agent-ee evidence-replay NEW_REQUEST --out NEW_REPLAY
agent-ee verify-evidence-operation NEW_REPLAY
```

The first adapter is deliberately limited to Python on Linux x86-64, using the
captured `/usr/bin/python3` binary identity. It invokes no model, provider, shell,
external tool or subprocess from the experiment. No new Python package or native
library is required. The executable hash does not capture every system library;
this is not a cross-machine hermetic runtime. Other runtimes and unenforceable resource requests produce
retained `unsupported` receipts, never fallback execution.

Bubblewrap mounts only the candidate, package code, captured script/launcher and
standard read-only system runtime. It hides host home, temporary files, sockets,
credentials, source run metadata, check results and all reviewer scratch. Its
network namespace is private; all filesystem mounts are read-only, including
scratch, `/tmp`, `/dev`, `/proc` and the root. Output consists only of stdout and
stderr pipes. Scripts cannot create artifact files or dependencies; captured
candidate files supply any additional input data. Shells, external simulators,
writable artifacts and imports from arbitrary custom bundles are unsupported.

Before loading the script, the launcher applies hard `RLIMIT_AS`, `RLIMIT_CPU`,
zero file-size/core limits and a 32-descriptor ceiling. A Linux seccomp filter
denies process/thread creation, exec, sockets, io_uring, namespace changes and
selected process-control syscalls. It rejects alternate syscall architectures
and x32. The [kernel seccomp documentation](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)
explains this filter interface and its architecture guard requirement. The launcher reports policy readiness before reading the script; absent
readiness leaves `enforced_limits: null`. A parent wall deadline or cancellation
kills and reaps the entire Bubblewrap process group. An unconfirmed reap is
reported as `cleanup_failed`, with a retained cleanup error. The namespace and
`--die-with-parent` also constrain descendants. Retained combined stdout/stderr
has a byte ceiling; overflow kills the operation, preserves the bounded prefix
and sets `logs_truncated`. Unretained bytes are not claimed as hashed evidence.

Capture and source verification occur outside the child execution wall budget.
Script access to system time and randomness is not deterministic replay evidence.
Supported ranges are 0.05–60 wall seconds, 1–30 integer CPU seconds, 64–512 MiB
address space, and 128–1,048,576 combined log bytes. Unknown budget fields are
unsupported, including requested aggregate memory/directory guarantees. Address
space and CPU limits cover the single script process; they are not an aggregate
kernel-memory/cgroup limit on the parent, Bubblewrap or host. Read-only mounts
prevent script-created storage, rather than claiming a general writable-directory
quota. Admission is one active replay per coordinator process; separate CLI
processes do not share an aggregate admission budget. There are no host-wide
limit changes or privileged cgroups. See [security](security.md) for host/kernel
trust limits; this is not protection against a compromised kernel or owner.

The script's complete stdout must be UTF-8 JSON with exactly `assertions`, at most
100 entries, each containing only a 1–200 character `name` and Boolean `passed`:

```python
import json
from pathlib import Path
content = Path("/candidate/project/rc_filter.kicad_sch").read_bytes()
print(json.dumps({"assertions": [{"name": "schematic nonempty", "passed": bool(content)}]}))
```

Execution, exit code, parsing and assertion values remain separate. Exit 1,
a failed assertion, or exit 0 with passed assertions never creates a defect or
resolves the concern. Malformed output can have `execution: ok` and
`parsing: malformed`, but receipt `status: failed`. `status: completed` means
execution and parsing completed, even when a script reports false assertions.
The replay command exits 0 for completed, 1 for failed/unsupported, 130 for
interrupted; invocation errors exit 2. Every receipt states
`acceptance_authority: none` and `concern_status: unresolved`.

Receipts retain candidate/input, request, script, launcher, interpreter and raw
log hashes, command/policy, enforced limits, execution, parsing and assertion
results. Candidate/request freshness is rechecked after execution. Tampering,
timeout, denied writes/network/syscalls, malformed JSON and missing runtimes
remain failures/unsupported. A denied attempt is diagnosed only by its observable
error output; a sandbox failure alone is not proof of an attempted attack.
`verify-evidence-operation` checks exact retained file inventory and hashes. It
is an integrity check, not source freshness or acceptance, and has the same
unsigned-owner/host trust limits as other repository SHA-256 records.

## Inert graduation proposal

```sh
agent-ee evidence-graduation NEW_REQUEST --proposal proposal.json \
  --replay NEW_REPLAY --out NEW_GRADUATION
```

`--replay` is optional. The proposal must contain exactly these nonempty fields:
`adapter_contract`, `supported_scope`, `unsupported_scope`, `requirement_mapping`,
`numerical_criteria`, `expected_control`, `expected_seed`, `limitations`. These
are unaccepted proposal data; the exporter does not qualify their engineering
truth or authorize their thresholds. The bundle retains concern/input provenance,
script, optional request-bound receipt and its operation identity. It is labeled
`proposed_unaccepted` with no acceptance authority. Replay artifacts remain
verifiable in their separate operation directory; the export is not a runnable
check installer or a self-contained qualified toolchain.

An authorized maintainer must approve and implement the real handler/descriptor
or qualified measurement adapter and explicit criteria. A new declared baseline
and campaign must execute it through ordinary checks, sealing, fresh independent
reviews and centralized adjudication. Only a fresh failed coordinator check can
supply an accepted witness. The export never changes scope, registry, baseline,
campaign state or old concern dispositions. Owner review remains separate from
peer review.

Initial reviewer contexts and experiment discovery are unchanged: no peer
requests/replay mounts enter initial reviews. Historical ad-hoc reviewer tools
still run with provider-network transport under their existing harness policy;
this dedicated replay lane does not retrospectively make those tools networkless.
