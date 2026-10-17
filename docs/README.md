# agent-ee manual

This manual describes agent-ee 0.1.0. The implementation is a Python CLI and an
agent discovery skill. Native KiCad files are the design authority. Review
outputs apply to a captured candidate and declared criteria, not to an entire
product's hardware qualification.

| Task | Read |
| --- | --- |
| Install, authenticate, upgrade or remove links | [Installation](installation.md) |
| Run a review, investigate findings and verify a repair | [Operator guide](operator-guide.md) |
| Look up a command, flag, exit code or work budget | [CLI reference](cli-reference.md) |
| Select inputs, libraries, reviewers and packet contents | [Scope reference](scope-reference.md) |
| Understand each native/engineering check | [Check reference](check-reference.md) |
| Parse records or understand finding dispositions | [Result reference](result-reference.md) |
| Understand capture, isolation and orchestration | [Architecture](architecture.md) |
| Assess permissions, inference data exposure and trust | [Security and trust](security.md) |
| Diagnose a refusal, incomplete run or tool failure | [Troubleshooting](troubleshooting.md) |
| Change checks, adapters, schemas or tests | [Maintainer guide](maintainer-guide.md) |
| Restore or reproduce the evaluation | [Evidence reproduction](reproducing-evidence.md) |

The [acceptance index](../evidence/README.md) links all ten demonstrations,
including actual native tools and agent reviews. The [donor baseline](../evidence/BASELINE.md)
reports what the available Universal 112 snapshot establishes for FOME-MC.
The [coverage overview](coverage.md) gives a shorter account of supported checks
and omitted engineering areas.

## Decisions and historical records

[Implementation decisions](decisions.md) explains the architecture and tradeoffs.
[Independent evaluation expectations](evaluation-plan.md) was committed before
the implementation. [Fixture provenance](../fixtures/rc/PROVENANCE.md) identifies
the adapted native fixture and its source.

The following are builder records, not the current operator reference:

- [Original implementation agreement](contracts.md).
- [Fable assessment](fable-assessment.md) and [Argon assessment](argon-assessment.md).
- [Argon module agreement](contracts-argon.md) and [integration review](integration-review-argon.md).
- [Via-query correction](via-probe-correction.md), including actual measured evidence.

The current command parser and validators are the final authority if a historical
agreement differs. The manual references their actual behavior and names limits
that remain unenforced or outside the demonstrated scope.

Reviewer routing and comparison: [Profiles](reviewer-profiles.md), [evaluation plan](model-evaluation-plan.md), [observed results and provisional allocation](../evidence/MODEL-SELECTION.md), and [donor follow-up](../evidence/MODEL-BASELINE.md).
