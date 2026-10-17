# Security, data exposure and trust

## Candidate permissions

The native tools and model harness run inside Bubblewrap. Inputs and package
code are read-only mounts. Each invocation gets its own writable work directory,
a private temporary directory, process/IPC/UTS namespaces, and a minimal visible
filesystem. The host home, host temporary files, runtime sockets and other
reviewer workspaces are absent. Native tools also get a separate network
namespace. A read-only prompt is not the enforcement mechanism.

System `/usr` and `/etc` are visible read-only, with system executable/library
paths exposed as required. This is a host tool execution boundary, not a
container image containing only captured dependencies. Inspect `sandbox_argv`
in an execution record to see the actual mounts and arguments.

In the [container image](container.md), the same Bubblewrap boundary runs inside
the container, and `/usr` and `/etc` are the image's. The image does not replace
that boundary. To let Bubblewrap create namespaces, the container must run with
Docker's seccomp filter, `docker-default` AppArmor profile and `/proc` masks
disabled. The container user stays non-root, and no capabilities, devices or
network are added. These relaxations widen what a process escaping Bubblewrap
could reach inside the container, compared with default Docker confinement. The
[container reference](container.md#bubblewrap-inside-the-container) lists what
each one exposes. The documented commands also disable the container's network
for checks-only work.

The parent CLI performs capture and record writes on the host under the owner's
permissions. Bubblewrap does not constrain the owner, another host process with
sufficient access, a compromised kernel/provider, or a malicious change to the
parent application. Before/after hashing detects changes at boundaries; it
cannot prevent privileged mutation or turn capture into an atomic transaction.

## Inference and credentials

Reviewers have network access for subscription inference. The selected artifact
text, declared criteria and check details are supplied to the provider. The
adjudicator additionally sees initial judgments and collected findings. The
current adapter is not an offline local-model path. Review only information
appropriate for the configured provider/account.

The parent locates the selected harness subscription credential (see
[profiles](reviewer-profiles.md)) and mounts it read-only during
the child call. Its contents are never copied into retained run files, and the
temporary reviewer home is removed after the call. The execution record names
the credential mount path, which is not the credential value. Parent API keys
are omitted by the child's cleared environment. No subscription error enables
an OpenRouter fallback.

Harness settings, tool policies and subscription paths are explicit in the
[profile documentation](reviewer-profiles.md). Ordinary tools are enabled. These settings reduce accidental cross-run coupling;
they do not independently attest every provider implementation detail. Harness
usage metadata is retained when available. Do not add host runtime sockets or
credential copies to scratch as a convenience fix.

## Evidence trust

Design prose and extracted documents are untrusted evidence, not authority to
change the review rules. Fixed instructions tell reviewers and adjudication to
ignore such attempts. The candidate is read-only and experiments are separate, but
this does not prove immunity to every prompt-injection or model judgment error.

Citation validation proves that a path is allowed, exists in this run and has
an explicit locator. It does not prove the electrical argument, datasheet
interpretation, component identity or operating condition. A supported singleton
must survive a favorable majority; conversely, repeated unsupported prose is
not corroborating measurement.

SHA-256 inventories detect accidental/stale modification against trusted
records. They are not signed by an external authority. A party able to edit
candidate, evidence, result and all expected hashes can forge internal
consistency. This implementation does not claim hostile-host tamper evidence,
cross-machine binary reproducibility or a qualified hardware verification chain.

## Storage and publishing

Scopes copy explicit inputs, including uncommitted files. Directory selections
can accidentally include private material; inspect them. Retained evidence may
contain proprietary design text, absolute local paths, model outputs and harness
session identifiers. Keep it under the appropriate repository visibility.
This project's remote is public. Repository visibility does not establish
redistribution rights for captured third-party material or authorize hardware
fabrication. Publication of additional design files and evidence remains subject
to the owner's approval.

The archive helper refuses links and files named `.credentials.json`, normalizes
archive ownership/timestamps, verifies checksums on restore and refuses changed
existing files. It is not a general secret scanner. The recorded hygiene probe
is a bounded filename/pattern check, not proof of every possible secret's absence.
Preserve raw evidence bytes rather than editing logs to make them look clean.

The separate `scripts/evidence_fetch.py` outer tool requires an explicit store
URL and a trusted per-file manifest. It checks sizes and hashes in private
staging and atomically publishes a complete named directory without replacing
any existing destination. It refuses destination ancestor symlinks and unsafe
manifest paths. It does not grant sandbox network access, authenticate a publisher,
scan all secrets, establish publication rights or protect against a hostile host.
See the [relocation workflow](reproducing-evidence.md#evidence-relocation-preparation-agentee-20)
for Linux prerequisites, historical verification limitations and owner gates.

## Engineering trust boundary

`criteria_satisfied` covers the named candidate and declared checks. It does not
assert all component tolerances, failure states, active-device behavior, current
limits, thermal paths, mechanical constraints or vehicle conditions were tested.
An undeclared condition is not automatically a defect, and missing necessary
coverage is not a pass. State consequential unknowns and obtain project-specific
requirements and physical validation before expanding the conclusion.

Tool-enabled reviewers are trusted to use mounted subscription credentials only
for inference. Read-only mounts prevent credential changes, not reads by their
processes. The parent does not copy credentials into evidence; no claim of
credential isolation from arbitrary reviewer shell commands is made.

## Scope/2 evidence and implementation authority

Scope/2 adds a coordinator-created baseline and evidence seal. Full requirements,
target/reference sources and verification program inputs are captured. A reviewer
cannot promote an arbitrary claim to an accepted defect: acceptance requires a
failed coordinator check, a mapped requirement, a check citation and unchanged
sealed raw evidence. Unsupported concerns can remain unresolved; merely adding a
path to a file does not establish their mechanism.

Generic measurement production and parsing are separate invocations. Producer
argv and installed executable hash are baseline criteria; captured program/parser
bytes are immutable inputs. Requests are mounted read-only for each stage, and
the parser's raw-output mount is read-only. The coordinator rechecks retained
hashes. A malicious or incorrectly qualified declared producer/parser can still
compute misleading values; the operator supplies and qualifies that toolchain.

Campaign implementation receives the prior candidate read-only plus a disposable
writable design copy. After the call, the coordinator checks exact file inventory,
no symlinks, unchanged baseline files and unchanged bytes outside `mutable_inputs`.
The initial captured scope owns that allowlist; campaign verification re-derives
it. The implementation cannot broaden it by editing state or adding a file.
This protects the supported proposal path, not arbitrary host filesystem writes.

The implementation call receives baseline and campaign history in its prompt.
That is additional inference exposure compared with an initial reviewer. The
same subscription credential/network limitations apply to it. Initial reviewers
receive neither implementation prose nor historical reviewer opinions.

## External routing tool

The [routing adapter](routing-adapter.md) runs kicad-route, an external
checkout agent-ee does not control, as a coordinator step. The checkout and its
installed backend state are read-only mounts. The step refuses to start unless
a write probe into them fails at the OS level and the tool code matches the
pinned blob hashes. The router gets a fixed argv and an environment rebuilt
from constants: no inherited variables, credentials, backend arguments or
backend environment, and no network. Its outputs are untrusted data. The
adapter discovers the single attempt directory itself, never follows `latest`
or a reported path, refuses links, and delivers only a board whose hash the
router validated. `verify` checks its report against the pin, the fixed argv
and environment and the retained bytes; only the AGENTEE-95 receipt
authenticates those bytes. Read-only mounts do not stop other host processes from
changing the checkout. Before/after identities detect content changes during
an attempt; they do not prevent them.

## Leases and persistent history

Supported mutation entry points acquire nonblocking process writer leases. A
`.campaign-root` marker makes descendant run mutations acquire the parent campaign
lease as well. Concurrent attempts fail rather than waiting indefinitely. Lock
files are persistent adjacent files; do not unlink them as a stale-lock remedy.
A process crash releases the OS lock, while the file remains. Read operations
are not guaranteed to observe one atomic transaction spanning all run files.

`verify-campaign` retains and checks original result snapshots, baseline equality,
write authority, calibration transitions and convergence projections. Feedback
can supersede current acceptance while preserving the original snapshot. This
is trusted-local-storage consistency, not a signed append-only ledger resistant
to an owner rewriting every record and hash. Campaign and feedback records also
contain absolute paths; moving a tree can break those links.

## Empirical provenance

Bench ingestion checks raw-file hashes, sample equality, candidate identity,
operating condition, units, applicable model/program hashes and residual arithmetic.
It does not authenticate an instrument, validate a calibration certificate,
parse every provenance field semantically, or prove a prototype matches its
claimed candidate. Real engineering use requires those controls outside this
software interface.

Cumulative bundles retain source tool predictions and raw samples. Prior reviewer
judgments are excluded from those prediction records to preserve independent
initial review contexts. New model knowledge changes the calibration epoch, not
the immutable requirement baseline. Once feedback is registered, a later failure
cannot silently restore the old acceptance. Recovery must preserve the evidence
and the supersession relationship.

Datasheet fact authors are untrusted. Citation verification establishes exact
text occurrence in the captured PDF, not applicability or correct table/conditions
selection. Engineer-owned check parameters select components and facts. Scope/2
binds the facts and all referenced PDFs into immutable baseline files; the
networkless installed extractor is hash/version declared and rechecked by verify.
See [datasheet facts](datasheet-facts.md) for its limits.

## Active SPICE model data

The [active-device check](active-spice.md) validates captured model closures before
ngspice: only explicitly declared immutable model files can be included, and
control, analysis, file-I/O and unsupported directives/functions are refused.
Generated model copies are mounted read-only in the existing networkless tool
sandbox. This bounded dialect does not qualify arbitrary vendor model syntax,
model accuracy or publication rights. Retained decks, traces and bindings use
the existing coordinator evidence seal.


## Dedicated concern replay

The [evidence-request lane](evidence-requests.md) uses a separate networkless,
read-only, single-process Python sandbox. Requests, receipts and graduation
exports carry no acceptance authority and never modify reviewed runs. This lane
does not change provider-network access for existing reviewer harness tools.

## Bench transaction recovery

Opt-in bench transactions hold the source, journal, declared review target and
campaign ancestry leases through publication and the launch boundary. Ordinary
run verification uses shared existing leases and refuses a live writer or a
source-side publication intent whose event/result/report projection is partial.
Transaction status is read-only. Recovery never dispatches inference or controls
instruments; an uncertain start needs an explicit new attempt and fresh output.
Journal receipts and source publication markers detect inconsistent trusted-local
state; they are not signatures against a host owner who rewrites every record.
