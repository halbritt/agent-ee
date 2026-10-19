# AGENTEE-149 metadata receipts

Both attempted FOME reviews are incomplete. All four initial responses were
refused for invalid JSON pointer citations, and neither adjudicator ran.
See [the host run record](../../docs/fome-block-review-runs.md).

`receipts/wbo1/` and `receipts/psu/` contain exact coordinator records, candidate
and baseline manifests, check results/seals and reviewer execution records.
`retention.json` lists the full raw file inventory, copied records and the
unchanged host-only run location. The raw directories retain all evidence,
including refused responses and investigation logs. Their seals verified before
and after relocation into the assigned worker scratch directory.

These receipts omit raw snapshots and third-party investigation documents. They
are not complete runs and cannot be used as `validate --run` locations. No
archive was created because both investigations retained third-party web text,
and PSU investigations also retained vendor datasheets and extractions.
Committing those bytes requires authorization beyond this assignment.

The run references in the two source blocks preserve the actual failed attempts.
They give no passing credit, even with the mapped fome-mc object store. T16
records the pinned fixture identity and embedded origins; no vendor text or
fome-mc planning-document bytes are included here.
