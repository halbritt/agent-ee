# Saved JLCPCB DFM report binding

AGENTEE-156 implements the owner's 2026-10-18 decision: the owner uploads the
sealed Gerber zip once and saves the DFM result; agent-ee binds that evidence
offline. `dfm_geometry` remains a separate geometric pre-check.

```json
{"id": "dfm", "kind": "jlc_dfm",
 "params": {"fab_check": "fab", "report": "dfm/report.json"}}
```

Params are exactly `fab_check` and `report`. `fab_check` names a preceding
[`fab_package`](fab-package.md) check. Only `jlc_dfm` checks naming that package
may follow it. Every other check still precedes the package. Generation scopes
retain their `fab_package` done marker and map the DFM check to an acceptance
requirement when DFM is required for acceptance.

## Owner capture

1. Obtain `gerbers.zip` from a passing, sealed `fab_package` result and record
   its SHA-256. Upload those exact bytes at JLCPCB outside agent-ee.
2. Save the resulting report locally, including its module/item, layer and
   individual result locations. Retain the original bytes as a scope input.
3. Author the following normalized JSON alongside it, using the uploaded zip
   digest and the saved report digest. Capture both files through `scope.inputs`.
   `source.path` is relative to the candidate input root, just like `report`.
4. Rerun the checks over the same design with the DFM inputs captured. The
   package is regenerated and verified; its actual zip digest must still match.
   A changed design or tool output needs evidence for its new zip.

The JSON is agent-ee's owner-normalized format, **not a JLCPCB export schema**.
The [public result-view description](https://jlcpcb.com/help/article/dfm-analysis-result-view-help)
groups observations by module, item and layer, with located results and alert
levels. This reference informed the locator and severity fields; no supplier
report text or assets are included in the fixtures. The public view can limit
retained observations. Set `status` to `complete` only when the saved capture
and normalization cover the required analysis; partial, pending, or truncated
coverage must remain unknown. Normalize danger/error observations to `error`,
warning observations to `warning`, and non-failing observations to `info`.
Unknown alert levels need clarification before normalization.

```json
{
  "schema": "agent-ee.jlc-dfm-report/1",
  "supplier": "JLCPCB",
  "gerber_zip_sha256": "<64 lowercase hex digits for the uploaded zip>",
  "source": {
    "path": "dfm/saved-report.pdf",
    "sha256": "<64 lowercase hex digits for the saved source capture>"
  },
  "status": "complete",
  "findings": [
    {
      "id": "owner-item-1",
      "severity": "error",
      "message": "Owner description of the manufacturing issue",
      "page": 1,
      "locator": "module / item / layer / result index"
    }
  ]
}
```

Objects have exactly the fields shown. Digests must be lowercase SHA-256.
Finding IDs are unique, text is nonempty and trimmed, pages are positive
integers, and severities are exactly `error`, `warning`, or `info`. For an
unpaginated capture use page 1 and a stable result locator. `findings: []` is
valid only with the complete declaration. Duplicate JSON fields, nonfinite
numbers and unsupported schema/fields are refused. Each input must be nonempty
and at most 16 MiB; at most 10,000 findings are supported.

Both files become immutable baseline inputs in scope/2 and scope/3; the writer
cannot alter a report to make a candidate pass. Source discovery requires the
normalization bytes during baseline compilation. Missing or unsafe capture
inputs are refused there. Scope/3 lists both immutable inputs and the earlier
`fab_package` prerequisite, with predicates `jlc-dfm:sealed-gerber-binding` and
`jlc-dfm:no-errors`.

## Execution and results

The coordinator recomputes `fab_package.artifact_record` and requires it to
equal the preceding result's artifact record, candidate ID and captured board
hash. A failed package, changed artifact, missing manifest or inconsistent
record is `unsupported/unknown`. The parser then runs as
`/usr/bin/python3 -B -m agent_ee.jlc_dfm REPORT` through the existing Bubblewrap
executor with a read-only candidate and no network. Preflight already requires
Python and Bubblewrap for every check; the preceding package requires KiCad.
No upload, HTTP request, provider call or supplier API occurs inside the check.

The parser verifies the normalization against its captured hash, parses the
strict format, and verifies the saved source against both its declared hash
and captured hash. The coordinator requires both retained report copies to exist and match their
captured hashes, then compares the declared zip hash with the verified package
artifact hash before grading any findings.

| Condition | Execution / outcome |
| --- | --- |
| No captured report or no verified preceding package | `unsupported / unknown` |
| Missing source, source hash disagreement, malformed JSON/fields, unknown severity or incomplete report | `malformed / unknown` at runtime; an unreadable normalization can be refused at capture or capability discovery |
| Declared zip digest differs from the sealed package | `unsupported / unknown`, even when the report lists errors |
| Parser crash, deadline, unavailable sandbox, or missing parser output | Failed execution status and `unknown`; never a pass |
| Matched complete report with one or more errors | `ok / defect` |
| Matched complete report with warnings, info, or no findings | `ok / pass`; warnings are advisory limitations |

`work/ID/report.json` retains the exact normalization, `saved-report` retains
the exact source bytes, and `parsed.json` retains the parser's result.
`expected.json`, the execution receipt, console log and package artifacts are
also check evidence. The ordinary evidence seal binds these files to the
candidate and baseline. Changes to retained reports invalidate verification.

`details` records the artifact identity, sealed and declared zip hashes,
normalization SHA-256, source report SHA-256, and parsed findings. Every matched
finding carries the source report hash, page, locator and retained evidence
path. Error locations are also in the coordinator defect's `location`. Error
findings use the existing requirement/check witness pipeline; warnings do not
create accepted defects. A zip mismatch retains ungraded parser facts with
`binding: mismatch` and cannot create a coordinator defect.

## Limits and validation

The owner supplies the normalization and declares completeness. Hash binding
identifies the exact bytes attached as evidence; it cannot authenticate supplier origin or
prove that a transcription omitted nothing. This check does not extract text
from arbitrary PDFs, HTML, screenshots, or proprietary vendor formats. It
retains them as source evidence and parses the documented normalization.
Pass covers the uploaded Gerber zip's declared DFM observations. Assembly
preview, stock, electrical function and physical qualification need separate
evidence. Captures for changed Gerbers need a new owner upload.

`tests.test_jlc_dfm` uses original synthetic report text and the real owned
fab-export board. It verifies native package re-export stability, networkless
parsing, report and candidate binding, warning/error grading, baseline freezing,
sealed witnesses and tamper refusal. Missing inputs, changed zip/source/report,
incomplete status, unknown severity, parser crash, missing output and changed
retained report copies all remain
unknown. No actual JLCPCB upload or third-party design is needed by these tests.
