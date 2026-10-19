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
only illustrates one entry. Every warning and every assumption-bearing finding
emitted by the actual candidate needs its own entry. Use exact native IDs, including analyzer prefix and any `#N`
suffix. Both native `source:token` and `source:rule:locator` forms are supported.
IDs are not reconstructed from rule names or matched by message text.

With valid output from all four analyzers:

- Any `error` finding produces `execution: ok`, `outcome: defect`, even if a
  disposition names its ID.
- Any `warning` without an exact matching disposition also produces a defect.
- At every severity, nonempty native `assumptions` or `source_path: assumed`
  requires an exact matching disposition. An undispositioned assumption-bearing
  `info` produces `ok/defect`; an exact match attaches the disposition and can
  pass if no other blocking finding remains. An error always stays blocking.
- With no errors and every warning and assumption dispositioned, the result is
  `ok/pass`. Plain `info` needs no disposition; an entry targeting plain info
  stays in `unused_disposition_ids`, as does any mismatched/absent ID.

For compatibility, `undispositioned_warning_ids` lists both undispositioned
warnings and assumption-bearing findings, including info. The existing
`happy-severity:warnings-dispositioned/1` predicate covers this stricter policy;
no schema, registry or evidence-sealing change is introduced.

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
native finding's raw JSON path/pointer, and each matched disposition's
`dispositions.json` pointer. The check-owned `dispositions.json` is an exact copy
of the captured bytes, with captured source path/hash in
`details.dispositions_source`. It enters the existing evidence seal with the
other artifacts. Tampering invalidates public verification. Baseline changes
require recapture; a disposition is never a live writer-editable waiver.

Scope/3 publishes `happy-severity:no-errors/1` and
`happy-severity:warnings-dispositioned/1` as executable predicates. The gate can
witness an undispositioned-warning, assumption or error defect through the existing
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

## Known analyzer limitations (pinned 2.3.1)

Two pinned heuristics require further topology and assumption analysis
(AGENTEE-219, found on the native-v5 pod criterion-4 run):

- PP-001 (`audit_power_pin_dc_paths`) counts a net as supplied only by name,
  connector or a passive bridge. It ignores another component's `power_out`
  pin (a buck switch node feeding an inductor, a reference buffer output) and
  never crosses a P-MOSFET whose gate is held at ground.
- PR-001 (`validate_i2c_bus`) builds an I2C bus from SDA/SCL pin names. A
  pod sensor has a driven chip-select and shares clock/data nets with an SPI
  peer, yet it is reported as an I2C bus without pull-ups.

These errors stay blocking. They are recorded known limitations, never a pass:
no disposition waives an error, and the pod circuit is not changed to silence
them. The [owned reproducer](../fixtures/kicad-happy/ac4-false-positives/EXPECTATIONS.md)
triggers each mechanism plus real violations that must keep failing, including
the two agent-469 adversarial controls: a P-MOSFET gate divider with a competing
pull-up to the source, and an I2C device whose chip-select has both a passive
pull-up and an MCU GPIO.

`tests.test_happy_ac4_false_positives` asserts the pinned errors through the
real gate. It also asserts what a proposed correction does with them. The
correction is an exact kicad-happy commit recorded in the fixture's
`expected.json`. It is conservative: unknown is never pass.

- A PP-001 pin it sources only through another component's `power_out` pin
  (whose owner must itself be powered) or through a P-MOSFET whose gate the
  netlist holds at ground becomes an `info` finding. Its `assumptions` list
  names what is trusted, not proven: `power_out` regulation, `|VGS(th)|` and
  clamp margins.
- A competing gate pull-up or a driven gate keeps the error and names the
  unresolved conduction (with the divider ratio when values are known).
- A dual-mode device counts as SPI only with a driven chip-select and an SPI
  clock/data pin pair on one peer, on distinct SCL/SDA nets. I2C aliases and
  known I2C part roles on the peer cannot establish SPI. Otherwise its I2C
  pull-up errors stay, next to an `info` note that the mode is unresolved.

The earlier proposal (b8ab8b4) cleared both controls and is recorded as
superseded.

The correction reuses a PP-001 limitation's finding ID for its info note
(same rule and component, new severity). Such an assumption-bearing note now
requires and accepts an exact disposition. Dispositions are immutable captured
requirements; changing analyzer pins or native assumptions requires reviewing
the dispositions against that candidate and recapturing the baseline. ID equality
does not verify datasheet evidence. The native regression proves a missing or
mismatched ID gives a defect, and an exact disposition on an isolated owned
assumption fixture can pass. No real-pod dispositions are authored here.

The real pod has six assumption-bearing info findings needing datasheet-backed
adjudication by the pod lane before AC4 can pass with the proposed analyzer:
`schematic:PP-001:u4`, `schematic:PP-001:u5`, `schematic:PP-001:u6`,
`schematic:PP-001:u7`, `schematic:PP-001:u11`, `schematic:PP-001:u12`.
The two SPI pull-up errors clear only by positive topology evidence. This is
not pin adoption or a passing pod acceptance run.

The test exports the correction read-only from the `AGENT_EE_KICAD_HAPPY` Git
object store. It is on this host because the kicad-happy work tree shares that
store. Alternatively, `AGENT_EE_KICAD_HAPPY_CORRECTED` names a checkout of
exactly that commit. Both are held to the recorded installation identity. If
the commit is missing (another host, or the branch was deleted and
garbage-collected), the test fails under `AGENT_EE_REQUIRE_TOOLCHAIN=1` with
the commit, branch and store in the message; it never skips. To fix it, fetch
that commit into the store or set `AGENT_EE_KICAD_HAPPY_CORRECTED`.

The correction is not adopted. Adopting it changes the analyzer pin and
requires requalification plus a criterion-4 rerun.
