# Telemetry pod evidence index

The index maps the pod's declared numbered acceptance criteria to existing sealed
baseline requirements and check results. It reports what the mapped checks
establish and what remains open. It does not accept a board, judge whether the
mapping covers the specification, launch tools or models, or change retained
evidence.

## Capture the mapping before the run

Legacy Rev-2 maps are JSON objects with exactly the keys `1` through `9`. Each value lists
existing baseline requirement IDs. Empty lists are permitted and remain **not
met**. Every baseline requirement must appear in the mapping; a requirement may
support more than one numbered criterion. The IDs below are examples, not a
qualified pod mapping:

```json
{
  "1": ["interfaces"],
  "2": ["parts"],
  "3": ["native-rules"],
  "4": ["analysis"],
  "5": ["stock"],
  "6": ["fabrication"],
  "7": ["mechanics"],
  "8": [],
  "9": []
}
```

For Rev-3, use the additive versioned format `agent-ee.pod-criteria/2`.
`declared_criteria` is the full criterion set from the captured requirements;
`criteria` must map exactly that set to existing baseline requirement IDs.
For example, an eleven-criterion declaration is:

```json
{
  "schema": "agent-ee.pod-criteria/2",
  "declared_criteria": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
  "criteria": {
    "1": ["interfaces"], "2": ["parts"], "3": ["native-rules"],
    "4": ["analysis"], "5": ["stock"], "6": ["fabrication"],
    "7": ["mechanics"], "8": [], "9": [], "10": [], "11": []
  }
}
```

These IDs are examples too. The declaration uses unique positive decimal strings
without leading zeros. Output rows are ordered numerically. No criterion is
silently padded or truncated: a missing or undeclared mapping raises
`CriteriaMapError` (a `ValueError`); the CLI refuses with exit 2 and names the
missing and undeclared keys. Unknown schema versions, extra fields and malformed
declarations also refuse. A versioned nine-criterion declaration preserves the
legacy mapped statuses and obligations. The reader validates the declaration
against its mapping, not against external or uncaptured requirements prose;
the scope compiler/owner must declare the complete authoritative set.

Capture this file as `requirements/pod-criteria.json` in the ordinary scope
inputs and list it in `baseline.targets`. This makes the map an immutable
baseline input. The reader refuses a mapping outside the captured baseline;
editing a mapping requires a new candidate. Requirement statements, source
citations and check associations come from the verified baseline, not from an
index-specific evidence format.

## Generate the index

Use the completed review directory, including a campaign's selected `runs/N`
directory. `--criteria` is relative to the captured `inputs/` tree.

```sh
PYTHONPATH=src python3 -m agent_ee.evidence_index \
  --run /path/to/pod/campaign/runs/3 \
  --criteria requirements/pod-criteria.json \
  --out /path/to/pod-evidence-index
```

The new output directory contains `index.md` and `index.json`. It must be
outside the retained run and its enclosing campaign. An existing output
directory is refused. Without `--out`, the directory is a sibling named
`<run-or-campaign>-evidence-index`. Exit 0 means the index was written; exit 2
means verification, mapping or output creation failed. Exit 0 is not a design
pass.

## Reading the result

Each criterion lists requirement citations, check kind and outcome, the check
result SHA-256, referenced evidence hashes, declared captured input references,
and check limitations. A nonempty set of mapped checks is **met** only when
the process is complete and every mapped check is `execution: ok` and
`outcome: pass`. This describes mapped check status, not completeness of the
mapping or board acceptance. Unknown, failed, missing, or empty coverage is
never converted to a pass. A missing or changed seal refuses the index.

All captured input paths and hashes are also listed globally, including stock
captures and owner-saved DFM evidence when present. Their presence is provenance;
the bound stock and DFM checks must supply their results. The reader does not
infer stock freshness or DFM approval from filenames or writer prose.

For a run inside a campaign, the existing campaign verifier validates the
receipt history under a read lease. The index includes the imported pre-receipts
and earlier implementation receipts leading to the selected run. Their recorded
applicability is retained. Later implementation receipts are excluded. Each criterion links pre-campaign
capture receipts and receipts for its checks' declared stages; this is stage
context, not proof that the receipt satisfies the criterion. Receipt history never changes a criterion's check outcome. A standalone run reports no
campaign receipts.

Passing `fab_package` checks rederive the existing fab artifact record and its
rotation obligations. `PREVIEW_REQUIRED` designators and sides remain owner
obligations to confirm in JLCPCB's placement preview before ordering. Their
absence from an incomplete or failed fab check is not permission to order.

Criteria 8 and 9 always remain built-board bench obligations in this file-based
index: CAN/ECU rates and logging, then RaceChrono BLE operation. A separate
physical validation workflow must discharge them. This reader does not infer
physical success from a file check. When declared, Rev-3 criteria 10 and 11 also
remain built-board obligations: concurrent analog/wheel acquisition and fault
tests, then standalone logging, interrupted power, full media and digital tire
sensors. Passing mapped file checks cannot discharge any of these obligations.

The limitations-bearing completion record is the final `runs/N/result.json`,
linked by path and hash from verified campaign history. Its limitations must
begin with every captured scope limitation verbatim, including order and
duplicates. Operational limitations may follow. Ordinary verification and the
index refuse deletion, rewording or reordering of that captured list. The index
repeats it in JSON and Markdown even when the declared checks are satisfied.
`stage_done` continues to describe producer receipt custody; it does not replace
this completion record or certify the limitations as met.

The ordinary run verifier checks snapshot, policy/runtime compatibility, check
projections and the existing `agent-ee.evidence/1` seal. The index reads the
captured snapshot, so a later change to the original source directory does not
alter the historical index. Files outside sealed evidence, including writer
notes and `REPORT.md`, are listed under `ignored_unsealed` without parsing
their contents. Additional files inside a sealed evidence namespace cause the
ordinary verifier to refuse changed evidence.

## Verification scope

`tests/test_evidence_index.py` exercises synthetic sealed records through the
real capture, seal and verification APIs, plus a real native fab/CPL fixture for
preview obligations. These tests establish reader behavior. They are not a
telemetry-pod run, electrical review, fabrication order or physical validation.
The first real pod index remains dependent on the actual pod campaign.
