# Installed kicad-happy adapters

The separate [severity gate](happy-severity-gate.md) runs schematic, PCB,
cross-analysis and EMC with an error/warning policy. This page describes the
existing `kicad_happy` declared-rule check.

The `kicad_happy` check runs the installed analyzer, never a vendored copy.
Each run uses the version that is installed and records it (see
[Versions](#versions)). The owned fixtures were exercised with releases 2.3.1
(commit `06840467ad0f5d76af45f64449b1f5ecb10f052f`) and 2.3.2 (commit
`2fd9aa0fe2d620f36627d9221e8b1393987ae38e`). Both write schematic, PCB, thermal
and EMC output envelopes **1.4.0**, and their reports on every owned fixture are
equal apart from run ids and input paths. Installation alone is not qualification. The original
[fixture assumptions](../fixtures/kicad-happy/qualified/requirements.md)
and [RC expectations](../fixtures/kicad-happy/EXPECTATIONS.md) define the
exercised envelope.

All stages run in the existing networkless native-tool sandbox against the
immutable candidate, with the installation read-only. Datasheet synchronization,
distributor lookups and SPICE-enhanced analysis are not invoked. Native reports,
commands, logs, conservative installed-file inventory and executed source hashes
are retained. The installed-file identity covers every file under `skills/` and
`.claude-plugin/` except bytecode/test caches; this intentionally includes
unexecuted helpers/data. Symlinks and unrecognized plugin metadata are
unsupported. The runner ignores installed bytecode. A before/after identity
check refuses an installation that changes while the analyzers run, and a run
refuses one that changes before the run completes. These checks do not defend
against privileged concurrent host mutation.

## Versions

The image installs the newest release of the 2.x line when it is built:
[docker/kicad-happy/install.py](../docker/kicad-happy/install.py) takes the
highest `vX.Y.Z` tag (pre-releases and the moving `v2` and `v2.3` tags are
ignored), applies the AGENTEE-219
[correction](../fixtures/kicad-happy/ac4-false-positives/EXPECTATIONS.md), and
runs every analyzer on the owned severity-gate project, plus the schematic
analyzer on the AGENTEE-219 reproducer. It writes
`/opt/kicad-happy.install.json` with exactly these fields: schema, source, tag,
commit, version, output format, the release and installed-file identities, and
each correction's origin and SHA-256. On 2026-11-03 the build installed v2.3.2.

Each check records what it used in `details.installation` and in its evidence
file `work/<id>/installation.json`. The record has the root, the version, the
tag and commit from the install record (`null` for a plain checkout without one),
the installed-file identity `sha256`, the file inventory, and the install record
with its measured path and SHA-256. The record's own content never supplies
those two measurements. The install record's raw bytes are kept as
`work/<id>/install-record.json`. The installed version is not compared with any
fixed version. Versions are `X.Y.Z` releases or `X.Y.Z-pre` prereleases, and
runs accept a prerelease of the line. These cases are refused as `unsupported`:

- a version outside the 2.x line or not in that form. A different major release
  can change keys under the same envelope format;
- any analyzer envelope whose `schema_version` is not listed in
  [kicad-happy-contract.json](../src/agent_ee/data/kicad-happy-contract.json)
  (today only `1.4.0`);
- a field that acceptance reads in another shape, under any envelope number:
  a finding's `rule_id` and `severity`; `finding_id`, `source_path`, `status` and
  `type` as strings; `suppressed` as a boolean; `assumptions` as a list of strings;
  `components`, `nets` and `pins` as lists; and severity-gate summary counts that
  match the findings ([kicad_happy_format.py](../src/agent_ee/kicad_happy_format.py));
- a PP-001, RS-001, RS-002 or RS-003 finding without `source_path`. Every
  emission of these rules in 2.3.1, 2.3.2 and the correction states it, so a
  missing `source_path` is a changed representation, not a plain note;
- an install record with other fields, malformed values or an unlisted output
  format, or one that does not describe the installed files.

To update, rebuild the image. The upstream ref listing is the cache key of the
image's `kicad-happy` build stage, so a rebuild after an upstream release
installs that release, and an unchanged release keeps the later layers cached.
The build runs the same report checks as a run. It fails if the correction no
longer applies or a probe report fails those checks, for example a renamed
findings list or a changed field shape under the same format number. It also
checks meaning on the owned projects, from their fixture expectations: the
AGENTEE-219 notes that state assumptions must keep an `assumed` `source_path`,
their assumptions and the severity gate's disposition requirement, and the
severity-gate fixture's dispositioned warnings must stay warnings. Renaming a
field on those findings therefore fails the build. A changed representation on a
rule and field that these checks do not name and the owned projects do not
exercise is not detected. After a failed build, the update needs a re-derived
correction, or adapter and gate checks before the contract file or the checks
are extended. A host checkout is updated with Git; runs record whatever it holds,
with tag and commit unknown.

`agent-ee verify` checks a finished run's analyzer record against that run's own
evidence. The record must be one the recording code could write: its fields,
SHA-256 digests, and an identity that its file inventory gives. Its version, tag,
commit and identity must equal the nested install record's, and a successful
check must name a version of the 2.x line and its executed helpers. The saved
`installation.json`, `executed.json` and raw `install-record.json` must equal the
record and the recorded install-record digest, and every executed helper must be
in the inventory. The installation now on the host is not consulted, so old runs
stay verifiable after an update. These are consistency checks over unkeyed
digests: a writer who rewrites every saved record and seal consistently is not
detected, and resisting one needs independent custody of the evidence.

## Declarations and evidence

`params.installation` names the installation; the run records its identity (see
[Versions](#versions)). Scopes written earlier may carry `installation_sha256`.
It is accepted and recorded as `details.declared_installation_sha256`, never
required; a value that differs from the installed identity adds a limitation.
`rules`, `counted_rules` and
`blocking_rules` are explicit lists. Blocking IDs must be counted, declared and
unique. Every rule has a unique `id`, `analyzer`, exact `rule_id`, native
`components`, and finite inclusive `min` and/or `max` bounds.

Historical RC declarations retain `field: cutoff_hz|time_constant_s`,
`analyzer: schematic`, `rule_id: RC-DET`; exactly one unsuppressed low-pass
calculation record must match the component set. Each declared target must also
match exactly one native `schematic.components` record with explicit
`dnp: false` and `on_board: true`, an unsuppressed identity and native
reference/value/library fields. Missing, malformed, duplicated, suppressed, DNP
or off-board targets leave the calculation `not_evaluated`. BOM exclusion alone
(`in_bom: false`) remains eligible. Successful evaluations retain raw pointers
to the calculation and each target inventory record. Baseline serialization is
unchanged; `assumptions` and `assumptions_source` are accepted only on adapter
rules, where they are validated.

New rules use a closed versioned `adapter` plus exact `assumptions` and
`assumptions_source: {"path": "requirements.md", "locator": "section"}`.
The source must be a captured requirement. Scope/2 already freezes the full
requirements and all check parameters into its baseline; changing an operating
assumption, bound or source creates a different baseline. Source text supplies
an auditable normative reference, not automatic datasheet verification.
`normalized.json` identifies the candidate, rule, target, value, evaluation status,
assumptions and precise raw JSON pointers. Ideal feedback and thermal arithmetic use decimal input
representations before conversion to JSON numbers, avoiding binary intermediate
roundoff at inclusive bounds. Multiple native sources retain both
calculation and connectivity provenance. No arbitrary JSON-pointer predicate or
native severity can become an acceptance criterion.

A missing, ambiguous, suppressed, nonfinite, contradictory or unsupported target
has `not_evaluated` with a reason. Any counted missing evaluation produces
`execution: unsupported`, `outcome: unknown`, even alongside proven violations.
Unrecognized envelopes are unsupported; invalid JSON is malformed; a crashed
stage fails; a missing installation is unavailable. Qualified violations become
coordinator check defects. Acceptance still requires the existing central
adjudication, requirement mapping, sealed evidence and witness validation.
Owner review remains separate from passing tests and peer review.

## Qualified narrow contracts

| Adapter / native identity | Criterion and supported envelope | Missing/unsupported boundaries |
| --- | --- | --- |
| `regulator-feedback/1` / schematic `PR-DET` | Ideal `Vref*(1+Rtop/Rbottom)` for a six-pin ground-referenced TPS7A4501, noninverting adjustable LDO; exactly identified regulator/top/bottom references, four distinct input/output/feedback/ground nets, positive native resistances and exact six-pin connectivity, including SHDN tied to input and both ground pins. Captured `vref_v`, `input_v`, `load_a`, net names and references. Native lookup Vref and `divider_plus_lookup` heuristic provenance must agree with the captured ideal Vref; native rounded voltage must agree with the formula. | Fixed suffix, heuristic Vref sweep, zero/missing/ambiguous resistors, missing divider, alternate topology and contradictory provenance are unsupported. The native record's top-level deterministic confidence does not qualify its voltage. The captured primary reference is TI SLVS720F page 3 (1.21 V to ground), not a generic native lookup. LM317 output-referenced adjust topologies are rejected. Adjust-pin current, tolerance, dropout/load regulation and physical regulator operation are outside this ideal calculation. |
| `power-pin-inventory/1` / schematic coordinator `power-pin-inventory` | Binary 1 iff the **complete** native non-ground `power_in`/`power_out` inventory matches declared pins/net domains/roles. Assumptions: `ground_net` plus `pins` with `component`, `pin`, `net`, `role`, `current_a`. Every declared rail has one declared source. Bounds must be exactly min=max=1. | Extra/lost native eligible pins, incomplete inventories and duplicated pin identities are unsupported. A known pin in the wrong domain is a violation. Declared currents are retained operating assumptions, not a supply-capacity calculation. Hidden/untyped power pins, keyword load estimates and whole-board power adequacy are outside this contract. |
| `usb-vbus-presence/1` / schematic `UC-002` | Binary native `usb_compliance.connectors[i].checks.vbus_esd_protection` pass/fail, cross-checked against the exact USB connector's VBUS pin/net and native recognized diode/IC inventory. Assumptions: `connector`, `vbus_net`, `subcheck` exactly `vbus_esd_protection`; bounds min=max=1. | Missing VBUS/subcheck, malformed or duplicate VBUS pin identities, ambiguous component inventories or contradictory device classification are unsupported. `usb_esd_ic: info` is never relabeled pass. Unrecognized protection gives a presence violation when the native branch explicitly fails. This is a keyword/connectivity heuristic, not ESD withstand or surge-rating evidence. |
| `capacitor-voltage/1` / schematic coordinator `capacitor-voltage` | Operating/rated voltage fraction for one native two-pin power/ground capacitor. Explicit unique value-string voltage rating and dielectric must agree with a native regulator `output_capacitors` entry. Assumptions: `operating_v`, `rail_net`, `ground_net`, `dielectric`, `regulator`. Dielectrics limited to X7R/X5R/C0G/NP0. | Unknown rating/dielectric/rail or contradictory native rating is unsupported. Operating voltage comes from immutable requirements, not estimated rail names. This coordinator calculation is distinct from upstream VD-001's estimated-voltage heuristic; resistor/IC derating and capacitance/DC-bias qualification remain outside coverage. |
| `thermal-ldo-estimate/1` / thermal `TH-DET` | Estimated `ambient + (input-output)*load*Rtheta` for one LDO assessment; native voltages/cap-proxy load/dissipation and rounded temperature/margin must agree with captured assumptions. Only generic SOT-223=60 C/W or SOT-23=250 C/W, PCB correction=1, package-table resistance, explicitly marked default125 C Tjmax. Assumptions: `ambient_c`, `input_v`, `output_v`, `load_a`, `load_source: output_cap_proxy`, `rtheta_source: package_table`, `tj_max_source: default_125`. | Missing assessment, unknown/default resistance, other packages/corrections and contradictory provenance are unsupported. Native cap-proxy load is explicitly accepted as a heuristic, never substituted for measured or datasheet load. The default Tjmax is recorded, not an acceptance limit. No device thermal rating or physical heat-flow qualification is claimed. |
| `esd-distance/1` / EMC `ES-001` | Native footprint-center distance proxy for an exact native supported protection class and electrically associated uniquely nearest external connector. Assumptions: `protection`, `connector`, `protected_net`. Exact native max=15 mm, no min. Reconstructs branch eligibility and requires emitted ES-001 exactly when distance exceeds 15 mm. | Missing/duplicate positions, unsupported protection types, unrelated/tied nearest connectors PCB pad-net association, and distance >50 mm are unsupported. Native recommendation prose says <10 mm, but the actual rule threshold is 15 mm. Empty vias provide **no ES-002 coverage**. Neither routing inductance, ESD behavior nor EMC compliance is established. |

`params.ambient_c` defaults to the historical native 25 C; an explicit finite
value in [-55,150] is passed to the thermal stage and must match every thermal
rule's captured ambient. Other stages receive no fabricated operating inputs.

The source branches inspected are installed `skills/kicad/scripts/signal_detectors.py`
(`detect_power_regulators`, `detect_protection_devices`),
`skills/kicad/scripts/analyze_schematic.py` (`analyze_usb_compliance`,
`analyze_voltage_derating`), `skills/kicad/scripts/analyze_thermal.py`
(`_estimate_all_power_dissipation`, `_compute_junction_temps`), and
`skills/emc/scripts/emc_rules.py` (`_connector_refs`, `check_esd_protection_path`).
The power inventory adapter consumes actual schematic `nets`/`components`, not
`build_power_tree`'s default loads or power-budget keyword estimates.
`detection_schema.py` does not recompute estimated Vout when recalculating the
divider ratio; the coordinator independently checks the formula.

## Qualification and integration boundary

Original owned fixtures exercise real installed output for control/seed pairs in
every listed category. Independent expectations are 5 V vs 6 V feedback,
complete vs wrong-domain power pins, USB presence vs removed diode, 0.3125 vs
0.833333333 capacitor utilization, 67 C vs 200 C generic package temperature,
and 2 mm vs 20 mm protection distance. The source check rejected a stale native TPS7A49 lookup; the captured primary reference agrees with the qualified TPS7A4501 lookup. Negative parser tests mutate real reports
to exercise skipped, missing, ambiguous, malformed and contradictory branches.
These mutations are test inputs, never claimed native observations.

The new simplified layouts are deliberately unrouted and lack production library
bindings. Native ERC/DRC execute and report defects on both controls and seeds;
they are retained without converting them to passes. The original RC control
and seed supply the separate demonstrated criterion where native ERC/DRC pass
while the functional calculation detects the seed. No third-party hardware was
imported or published. The generator authors original simple symbols and pads.

`describe_capability(params, files)` is a pure helper returning bounded predicate
IDs and captured immutable source path/SHA256 metadata. The scope/3 registry
validates captured schematic and board inputs and requires each adapter source
to be an immutable requirement. It reads no installation or candidate files and
runs no tools. Static capability is not runtime coverage or electrical qualification.

All other native findings remain informational. These narrow adapters do not
qualify all power, protection, derating, thermal or EMC rules, manufacturer
ratings, active simulation, lab validation or hardware certification.
