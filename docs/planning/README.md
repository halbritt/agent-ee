# Planning records (frozen)

Each dated directory here holds the working files of one planning round:
proposals, discussion notes, decision receipts, dispatch lists, checkpoints
and tracker read-backs. They are kept in place because tests and scripts read
some of them (`tests/test_recovery_runtime.py`, `scripts/strict_selection.py`)
and because the manual links to several.

They are records, not instructions and not templates. Do not follow their
dispatch, review or receipt rules, do not add a new round in this form, and do
not edit the existing files: a changed file can break the tests that read it.
Current guidance is in [AGENTS.md](../../AGENTS.md); owner decisions are in
[decisions.md](../decisions.md).

| Directory | Round |
| --- | --- |
| `2026-10-17/` | Enhancement backlog: three explorations, discussion and an agreed order |
| `2026-10-25-recovery/` | Recovery plan, finding register, backlog and contracts |
| `2026-10-28-zeroasic/` | Execution, reconciliation and recovery notes of the 2026-10-28 plan |
