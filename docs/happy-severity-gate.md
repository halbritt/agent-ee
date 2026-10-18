# Installed analyzer severity gate

`kicad_happy_severity_gate` implements the owner's 2026-10-18 criterion-4
policy. It runs pinned kicad-happy **2.3.1** schematic, full PCB, cross-analysis
and EMC scripts in the existing networkless sandbox. All four output envelopes
must be **1.4.0**. It invokes no thermal, SPICE-enhanced, lifecycle or network
option. The separate [`kicad_happy`](kicad-happy.md) kind retains its declared-rule
policy and its schematic/PCB/thermal/EMC stages.

Use a scope/2 or scope/3 baseline. The complete parameter object has three fields:

```json
{
  "installation": "/absolute/path/to/kicad-happy",
  "installation_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "dispositions": "requirements/analyzer-dispositions.json"
}
```

Replace the illustrative installation path and hash with the installed checkout
and `agent_ee.kicad_happy.installation(path)['sha256']`. The shared helper pins
all files under `skills/` and `.claude-plugin/` except bytecode/test caches,
rejects symlinks and unsupported plugin metadata, checks before/after identity
and records executed Python source hashes. Public `verify` and preflight detect
installation drift for both kinds.

`dispositions` names a captured file also listed in `scope.requirements`.
It must be immutable: the baseline freezes its bytes and check parameters;
the implementation writer cannot change it. Record the disposition author and
reason in the file. `recorded_by` is attribution text, not an authenticated
signature. The file is an owned review record; no new owner evidence store is
created. An empty file must still contain the explicit schema and empty list.

```json
{
  "schema": "agent-ee.happy-dispositions/1",
  "dispositions": [
    {
      "finding_id": "schematic:DS-002:j1",
      "disposition": "accepted_risk",
      "rationale": "Original fixture has no manufacturer datasheets; this run makes no device qualification claim.",
      "recorded_by": "fixture author"
    }
  ]
}
```

The closed disposition values are `accepted_risk` and `false_positive`. Each
entry needs a unique nonempty `finding_id`, nonempty `rationale` and nonempty
`recorded_by`; extra fields and unknown schema/values are refused. This example
only illustrates one entry. Every warning emitted by the actual candidate needs
its own entry. Use exact native IDs, including analyzer prefix and any `#N`
suffix. Both native `source:token` and `source:rule:locator` forms are supported.
IDs are not reconstructed from rule names or matched by message text.

With valid output from all four analyzers:

- Any `error` finding produces `execution: ok`, `outcome: defect`, even if a
  disposition names its ID.
- Any `warning` without an exact matching disposition also produces a defect.
- With no errors and every warning dispositioned, the result is `ok/pass`.
  `info` findings require no disposition. Entries absent from the current warning
  set are retained in `unused_disposition_ids`; they cannot waive another ID.

Unknown envelopes, missing/duplicate finding identities, unknown severities,
suppressed findings, missing/contradictory severity totals, filtered reports
and explicitly unknown results are `unsupported/unknown`.
Invalid or missing JSON is `malformed/unknown`; a crash is `failed/unknown`;
a missing installation is `unavailable/unknown`; pin mismatches are
`unsupported/unknown`. An unknown result takes precedence over known violations
from another analyzer. Reports use strict JSON parsing for this kind, including
rejection of duplicate object keys and non-JSON numbers.

The check retains raw reports, argv, logs, installation inventory, executed-file
hashes and `normalized.json` in `work/<id>/`. The normalized record cites each
native finding's raw JSON path/pointer, and each matched warning's
`dispositions.json` pointer. The check-owned `dispositions.json` is an exact copy
of the captured bytes, with captured source path/hash in
`details.dispositions_source`. It enters the existing evidence seal with the
other artifacts. Tampering invalidates public verification. Baseline changes
require recapture; a disposition is never a live writer-editable waiver.

Scope/3 publishes `happy-severity:no-errors/1` and
`happy-severity:warnings-dispositioned/1` as executable predicates. The gate can
witness an undispositioned-warning or error defect through the existing
requirement/check mapping. Central adjudication, independent initial reviewers,
sealed coordinator findings and the single writer remain unchanged.

This policy grades emitted heuristic findings. It does not establish that all
upstream rules ran or that the design is electrically correct or EMC compliant.
For example, cross-analysis's retained `checks_run` may document skipped checks
with no applicable signal tracks. Empty findings do not qualify those rules.
SPICE, thermal analysis, manufacturer qualification and lab evidence remain
separate requirements.

The original [owned fixtures](../fixtures/kicad-happy/severity-gate/EXPECTATIONS.md)
exercise a warning-only project, missing warning dispositions, and a native
error caused by removing its ground zone. Run the native regression with:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_happy_severity_gate -v
```

Set `AGENT_EE_KICAD_HAPPY` when the pinned installation is outside
`$HOME/git/kicad-happy`. The strict host run also includes the legacy analyzer,
registration, baseline, checks and preflight tests. Container qualification is
separate from this host evidence.
