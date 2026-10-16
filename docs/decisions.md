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

The reusable default uses two fresh Claude subscription calls, with circuit and
physical/verification lenses. Tools, MCP, hooks and cross-review outputs are
unavailable to those calls. The same model can share blind spots; agent count or
model diversity is not independent engineering proof. The requested Herdr Fable
and Argon agents built/challenged the implementation; those builder conversations
do not count as the separate initial fixture reviews.

Text reviewers receive original native excerpts and machine-check results in a
bounded packet. Omitted text/binary artifacts stay explicit. This is cheaper and
more inspectable than giving reviewers broad autonomous tool access; the cost is
that broader or image-dependent investigations require added evidence/checks or
an external engineering investigation. A native result and calculation remain
available to the operator in full.

Every claim is retained before adjudication. A fresh adjudicator sees both
judgments and must explain acceptance, dismissal or deferral against evidence.
Structural citation validation checks identity and paths; it does not prove the
semantics of the argument. The runtime policy prohibits majority disposal.
Tests exercise singleton survival and uncertainty; observed model judgments are
retained so failures can be assessed rather than hidden.

## Bounded runs and conservative rerun

The default wall-clock work budget is 1,200 seconds; individual reviewer calls
are capped at 300 seconds. A run has a finite declared check/reviewer list, no
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
