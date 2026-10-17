# Implementation decisions

## Small standalone Python CLI and skill

The owner requested a working reusable review capability and authorized ordinary
implementation decisions. Fable (`agent-413`, Claude Max) and Argon (`agent-414`,
Google AI Ultra through `agy`) inspected the local toolchain independently in
Herdr. Their [Fable](fable-assessment.md) and [Argon](argon-assessment.md) assessments
supported native check reuse, explicit candidate identity, isolated initial
reviews, and evidence-based dispositions. The coordinator selected the final
interfaces in [contracts.md](contracts.md); unresolved implementation details
were settled through executable probes and integration tests.

The new repository had no language/build conventions. Python 3.12 with standard
library orchestration uses the same system interpreter as installed `pcbnew`.
An executable source launcher avoids a mandatory virtual environment. Native
KiCad remains the design authority. The skill is a short operator entry point;
the CLI owns actual execution and records.

## Adapt native validation methods, keep project ownership separate

The concurrently developed FOME-MC `toolchain/eda.py` already handled native
reports, hierarchy coverage, silently skipped parity, and ngspice measurement
validation. Its snapshot code used live external-library links, and its CLI
wrote into the active FOME-MC checkout. Calling it in place would couple review
identity to another changing workspace. Copying the entire project toolchain
would add unrelated export/bootstrap and project configuration responsibilities.

`agent-ee` adapts the required validation methods and fixture with provenance;
it calls installed native tools directly. KiBot and kicad-happy can inform later
specialist work, but no check is credited merely because either is installed.
FOME-MC requirements stay in scopes/references rather than reusable orchestration.

## Copied candidates and filesystem isolation

Each scope explicitly selects files, including external libraries and relevant
requirements/models/configuration. Capture copies bytes, refuses undeclared
symlinks, and compares sources and copies before/after capture. The candidate
hash covers selected file hashes and exact scope. Run identity, packet identity,
implementation hashes and runtime binary hashes are separate recorded values.

Bubblewrap exposes the candidate read-only, a separate writable scratch for each
invocation, and no other reviewer workspace or host home. An actual write probe
was refused by the OS. Hash checks detect external changes at verification
boundaries; neither prompt wording nor hashes are described as write prevention.
This boundary does not protect against privileged host mutation or a compromised
kernel/provider. Shared libraries, OS, firmware and physical environment are
outside a fully reproducible machine-image guarantee.

## Initial judgments and later adjudication

The original implementation used Fable for both roles and a hard-coded Fable
adjudicator without comparative engineering evaluation. The owner rejected this
choice. Explicit versioned profiles now select every harness/model/effort/timeout,
including the adjudicator. Claude, Codex and Agy are supported subscription routes.
The [selection experiment](model-evaluation-plan.md) measures engineering findings,
control allegations and uncertainty separately from execution reliability.

The owner also rejected disabling tools and excessive sandbox construction.
Ordinary investigation tools are enabled in separate workspaces, with the existing
read-only candidate mounts. No hermetic environment is required. Fixed packets
remain an optional limited experiment mode, not the normal workflow. Initial
judgments cannot see each other's findings; adjudication can inspect their
retained experiments. Model diversity alone does not establish independence.

Every claim is retained before adjudication. A fresh adjudicator sees both
judgments and must explain acceptance, dismissal or deferral against evidence.
Structural citation validation checks identity and paths; it does not prove the
semantics of the argument. The runtime policy prohibits majority disposal.
Tests exercise singleton survival and uncertainty; observed model judgments are
retained so failures can be assessed rather than hidden.

## Bounded runs and conservative rerun

The default wall-clock work budget is 1,200 seconds; individual reviewer limits
come from the explicit profile (300 seconds in small evaluation profiles, 900 in
donor profiles). The run deadline further bounds every call. A run has a finite declared check/reviewer list, no
automatic provider fallback/retry and no autonomous source repair. Completed
records are saved immediately. A crash, interruption, invalid output or exhausted
budget leaves incomplete coverage.

Resume creates a new run and reruns from a fresh capture. Selective evidence reuse
could save work, but its dependency/invalidation rules would add complexity before
this small set of checks needs it. Correction closure appends a link from an
accepted old finding to a distinct saved candidate and fresh passing review/checks,
with original criteria preserved. Repair demonstration is on disposable native
files; the real donor and active FOME-MC source remain protected.

## Distinguish supported observations from established defects

The full control exercise at `2a0e7f0` passed every native and simulation
criterion, but the adjudicator accepted several minor/info concerns as accurate
observations. Treating every accepted claim as a defect falsely blocked the
control even though no claim established a violated criterion.

The corrected contract adds `noted` for evidence-supported minor/info concerns.
`accepted` requires an established defect; major/blocking uncertainty remains
deferred. Unsupported allegations and defects cannot be converted to notes, and
duplicate links cannot hide defects behind notes. This is a general disposition
rule; the fixture, its electrical limits and its geometry requirements did not
change. The observed failure and subsequent fresh runs are retained separately.

## Profile evaluation and provisional allocation

[Observed comparison and baseline results](../evidence/MODEL-SELECTION.md) support
a provisional Codex/high starting configuration. Selection is explicit and
versioned, including role, harness, model, effort and deadline. No model family
is treated as independent proof. Agy timeouts leave a comparison gap; Claude and
Codex donor adjudications exposed protocol failures despite passing the simple
adjudicator trials. Preserve those failures and separate assisted recovery from
automatic completion.

Ordinary system tools and network access remain available. Codex initially had
a redundant inner workspace sandbox that broke DNS despite the outer workspace
allowing networking; the native Codex investigation call now uses full access
inside the existing read-only candidate boundary. A real HTTP and write-refusal
probe verified the behavior. No hermetic runtime or provider fallback was added.

Correction closure now requires changed captured input bytes in addition to a
new candidate identity, fresh passing evidence and unchanged criteria. Changing
only reviewer allocation cannot be represented as a saved-design repair.
