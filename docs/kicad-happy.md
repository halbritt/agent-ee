# Installed kicad-happy adapters

The `kicad_happy` check runs the installed checkout, never a vendored analyzer.
The exercised installation is `/home/halbritt/git/kicad-happy` commit
`06840467ad0f5d76af45f64449b1f5ecb10f052f`, plugin metadata **2.3.1**, with
schematic, PCB, thermal and EMC output envelopes **1.4.0**. Installation alone
is not qualification. The original [fixture assumptions](../fixtures/kicad-happy/qualified/requirements.md)
and [RC expectations](../fixtures/kicad-happy/EXPECTATIONS.md) define the
exercised envelope.

All stages run in the existing networkless native-tool sandbox against the
immutable candidate, with the installation read-only. Datasheet synchronization,
distributor lookups and SPICE-enhanced analysis are not invoked. Native reports,
commands, logs, conservative installed-file inventory and executed source hashes
are retained. Every file under `skills/` and `.claude-plugin/` is pinned except
bytecode/test caches; this intentionally includes unexecuted helpers/data.
Symlinks and unrecognized plugin metadata are unsupported. The runner ignores
installed bytecode. Before/after identity and public `verify` detect installation
changes; they do not defend against privileged concurrent host mutation.

## Declarations and evidence

`params.installation` names the checkout; `installation_sha256` is the
`installation(path)['sha256']` inventory identity. `rules`, `counted_rules` and
`blocking_rules` are explicit lists. Blocking IDs must be counted, declared and
unique. Every rule has a unique `id`, `analyzer`, exact `rule_id`, native
`components`, and finite inclusive `min` and/or `max` bounds.

Historical RC declarations retain `field: cutoff_hz|time_constant_s`,
`analyzer: schematic`, `rule_id: RC-DET`; exactly one unsuppressed low-pass
calculation record must match the component set. Their scope/2 interpretation
and baseline serialization are unchanged.

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
| `usb-vbus-presence/1` / schematic `UC-002` | Binary native `usb_compliance.connectors[i].checks.vbus_esd_protection` pass/fail, cross-checked against the exact USB connector's VBUS pin/net and native recognized diode/IC inventory. Assumptions: `connector`, `vbus_net`, `subcheck` exactly `vbus_esd_protection`; bounds min=max=1. | Missing VBUS/subcheck, ambiguous connectors or contradictory device classification are unsupported. `usb_esd_ic: info` is never relabeled pass. Unrecognized protection gives a presence violation when the native branch explicitly fails. This is a keyword/connectivity heuristic, not ESD withstand or surge-rating evidence. |
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
IDs and captured immutable source path/SHA256 metadata for later scope/3
integration. It reads no installation or candidate files and runs no tools.
It is **not registered** into scope/3 on this branch; scope/3 is absent from this
base. Static capability is not runtime coverage or electrical qualification.

All other native findings remain informational. These narrow adapters do not
qualify all power, protection, derating, thermal or EMC rules, manufacturer
ratings, active simulation, lab validation or hardware certification.
