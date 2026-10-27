# AGENTEE-521 acquisition evidence — agent-674, 2026-10-27

Three missing candidate-independent judgments are authored and source-pinned:
RF-filter scope, bench-board scope, and placement-limit rationale. All three
remain pending independent witnesses. The current accounting is **three
records with existing concurrence, four authored records awaiting witnesses,
and seven missing records**. AGENTEE-521 and M1 remain incomplete.

This is a bounded delivery for agent-669's non-pod recovery fleet, based on
`64da514a09ca7f5b152ed78a594ff4d68120847e`. Agent-674 is the author,
observed model `gpt-6.1-sol`, profile `.codex-harm`, host `proximal`.
The live Plane issue was read on 2026-10-27; its `updated_at` was
`2026-10-27T19:11:37.302449Z`. The historical snapshot and
[accepted evidence contract](../planning/2026-10-25-recovery/contracts/claims-evidence-qualification.md)
remain the acceptance references. No production code or test is changed.

## Retained evidence

The complete delivery is under:

`/home/halbritt/.local/share/agent-ee/fleet-20261027-agent669/lane-521/`

The prepared source was copied from
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/acquisition-operating-prepared/`.
Its inventory contains 240 files, including manufacturer text and the operating
supplement. This differs from the original 198-file acquisition capture scope;
it does not replace that capture or create a new accepted build.

The seven-file prior evidence bundle was copied from
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/acquisition-authored-evidence/`.
The four prior judgments and three prior witnesses are byte-identical.
The new `authored-evidence/` contains seven judgments and those same three
witnesses. Original source and bundle inventories were rechecked unchanged.

| Artifact in the delivery | Contents |
| --- | --- |
| `source-inventory.json`, `source/` | Exact prepared source pins and retained source bytes |
| `prior-bundle-inventory.json` | Exact four prior judgment and three witness pins |
| `bundle-inventory.json`, `authored-evidence/` | Proposed unsealed bundle, including three new records |
| `admission-results.json` | All fourteen source spans, admission outcomes, source/runtime identities |
| `preservation.json` | Source and prior-bundle preservation result |
| `admission_probe.py`, `prepare_evidence.py`, `run_probe.py` | Exact authoring/probe/launcher-observation scripts |
| `runtime-custody-3.json`, `probe-execution-3.json` | Successful probe's actual container, image, admission lease and exit status |
| `admission-probe.log`, `admission-probe-2.log`, `admission-probe-3.log` | Two harness setup failures followed by the successful corrected probe |
| `live-issue.json` | Live Plane readback |

## Fourteen-judgment reconciliation

Names below omit the common `judgments/` prefix and `.json` suffix. The
existing concurrence comes from agent-566's retained witnesses; this delivery
creates no independent concurrence or formal engineering-review claim.

| Judgment | Actual current state | Remaining evidence/action |
| --- | --- | --- |
| `acq-elec-1-temperature-applicability` | Missing | Trace component limits and interval operands at 25 C; preserve the conditional temperature-range non-claim |
| `acq-elec-1-unqualified-conditions` | Recorded / existing concur | Preserve exact record and witness; later package freeze |
| `acq-elec-2-source-evaluation` | Missing | Exact-part capacitor bias/applicability, source operands and load/dissipation evaluation; finite sensitivity points do not supply a guaranteed capacitance range |
| `acq-elec-3-back-power-contract` | Missing | Operating restrictions and interface nets plus justified power-removal capacitance bounds; restrictions alone do not answer the full brief |
| `acq-elec-4-error-evaluation` | Missing | Loading, settling and ADC error at the declared configuration, with typical/guaranteed distinctions |
| `acq-elec-4-filter-scope` | Newly authored / witness missing | Independent review of the exact RF-filter record and source calculations |
| `acq-elec-5-configuration` | Previously authored / witness missing | Independent review of retained `b60deeb4…` configuration/timing instructions |
| `acq-elec-5-host-bus-contract` | Recorded / existing concur | Preserve exact record and witness; later package freeze |
| `acq-elec-6-high-impedance-contract` | Recorded / existing concur | Preserve exact record and witness; later package freeze |
| `acq-mech-1-bench-scope` | Newly authored / witness missing | Independent review of the scope correction and thickness interval |
| `acq-mech-3-limit-rationale` | Newly authored / witness missing | Independent review of the existing limits' rationale and explicit non-guarantee |
| `acq-mech-3-return-paths` | Missing; candidate-dependent | Actual final copper/return inspection and removed-plane negative bound to final board/scope hashes |
| `acq-mech-4-assembly-applicability` | Missing | Exact populated-part body/land/source applicability and assembly process; Murata approval-sheet gap remains explicit |
| `acq-mech-4-mated-height-scope` | Missing; candidate-dependent | Source scope correction plus actual final mating-column/cable-exit clearance; the height non-claim alone is insufficient |

The previous four-record status and three witness hashes match the
[2026-10-25 operating-judgment record](acquisition-operating-judgments-2026-10-25.md).
The newly authored records have these immutable handoff hashes:

| Record | SHA-256 |
| --- | --- |
| `acq-elec-4-filter-scope` | `96eab770ad6b60065b673d606e667457f94733af8ccb1854b2cb36b5aa561e62` |
| `acq-mech-1-bench-scope` | `dcb692f98a7864e76204b930d5abfc5fbb9c9c45f8b84b3fe0e85de3410b7095` |
| `acq-mech-3-limit-rationale` | `704a0a081ce0bb6bcca40eb3896c49513abffe653bf725e8882c278a99612d17` |

## Basis and limits of the three new records

The filter record uses the captured 1 kohm / 10 nF circuit values, declared
resistor/capacitor/source-impedance operands, operating instructions and
ADS1115 SBAS444E section 9.1.5. Its ideal nominal unloaded corner is about
15.915 kHz; the conditional operand range gives about 13.656–16.922 kHz.
These RC calculations exclude ADC switching and do not establish exact-part
limits or an antialias guarantee. The record distinguishes the 128 SPS output
data rate from TI's modulator-frequency aliasing discussion.

The bench-scope record cites the retained correction, selected fabrication
tolerance, operating instructions and JST ePH physical page 1. The selected
1.2 mm nominal at +/-10% yields 1.08–1.32 mm, wholly inside the captured
0.8–1.6 mm header range. The superseded 1.6 mm nominal interval extends to
1.76 mm. This is source-level process applicability; final native stackup,
actual manufactured thickness and supplier confirmation remain unassessed.

The limit-rationale record preserves the existing 3 mm pad limits and
16/10/10/12 mm input/output/ADC/filter loop limits. It cites the historical
engineering rationale, TPS7A20 SBVS338H section 7.4.1 and ADS1115 section 11.1.
The numeric limits remain agent-selected guardrails, with no datasheet-proven
performance guarantee or new threshold. Final routing measurements and
candidate-bound return-path adequacy remain separate obligations.

Manufacturer text, PDF, source-manifest and physical-page hashes were verified
against the retained text manifest. Each new record pins its applicable input
files, exact specification byte span and envelope. Its `candidate` is `null`;
none claims to inspect a current final PCB. No captured PDF or primary source
was replaced or downloaded.

## Verification and runtime

The supported launcher executed `python3 build/lane521/admission_probe.py`
in the isolated worktree with `AE_CI_MAX_WAIT=300`. The successful probe used:

- Image `agent-ee:exec-7a405961fba05ae1`, actual image ID
  `sha256:63cb53f57f8ca334f7bb202564247c20fa9ff3799aef052840e109733ebbe4bb`.
- Python 3.12.3; KiCad `10.0.7-10.0.7~ubuntu24.04.1`.
- Unchanged `qualification.py` SHA-256
  `a5c8df8a0da1b733323b7e9dbcc91800c3f0a07eecb3ad33b6eacd8fd5b7b7b5`.
- Unchanged `requirement_map.py` SHA-256
  `2bd0c017f02eea62582eba1b65d0dbefddd45560609657f044b56b2441d51448`.

The probe used actual source/span/envelope pins and actual retained witnesses
through `qualification.admit_judgment`. Three records returned
`recorded/concur`; four returned `pending`, `record_status: recorded`,
`witness: missing`; seven have no record. Fourteen negatives altered an input
pin or cited source text on the seven actual authored records; all failed
admission. No witness was fabricated for the negatives. No test was skipped.
This is a focused evidence probe, not a standing test-suite pass or native
board qualification. No suite, public build, simulation or routing campaign ran.

Two temporary-harness setup failures preceded that result: an unclosed Python
parenthesis, then a relative candidate-directory argument rejected by the
retained-path guard. Each was corrected before its next attempt. Neither
failure established an engineering or production-code defect. KiCad emitted
three property-enum assertions on import; the successful probe exited zero.

## Required next action

Agent-669 should send the three new immutable records and the retained
configuration record to an independent witness, then obtain Argon review of
this frozen delivery before serial integration. Witnesses must review the
substance and applicability of these exact bytes; metadata admission alone
does not supply concurrence. Source-gap and candidate-dependent judgments
remain assigned work, with no substituted thresholds or requirement waivers.

For a later authorized build, the complete `authored-evidence/` is a proposed
source bundle. Its internal `judgments/` and `verification/` paths follow the
existing consumer contract. It must be declared in the typed plan and frozen
by a new package stage; the original prepared source still contains no newly
imported record. Every input path and hash must also resolve against the actual
final candidate inventory: admission against the prepared source alone does
not establish that binding. Changed generated inputs require an explicitly
revised record and fresh witness review. Existing packages and history remain
unchanged. Actual typed
acquisition A/B cancellation/resume, required negatives and the remaining M1
workflow criteria are still required. Telemetry-pod, orders and uploads remain
held.

## Doctrine provenance

Validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` served packet
`pkt-8993a1d8ee8953d3`, content SHA-256
`8993a1d8ee8953d3a65d848c1dacae443b26ff5e3a47dff93307cc903722fa85`;
corpus `corpus-2026-07-12-a11702cc9217`, doctrine
`doctrine-f6bbb5196a3f8bf9`, retriever `retriever-ec995ecdd083b2c8`.
The retained packet, typed evidence, citation-consumption receipt and individual
obligation dispositions are in the delivery directory. Four concepts guided
scope, preservation and evidence handling; primary sources and the repository
contract govern the engineering statements. Forty generic unmet obligations
are classified nonmaterial to this evidence-only delivery, which makes no
production-code, architecture, performance or release-acceptance claim.
The missing witnesses, sources, candidate evidence and operational proof remain
material to the issue and are explicitly withheld here.
