# Acquisition block source capture, 2026-10-28

Agent-692 captured three **unverified** acquisition reference sets under the
coordinator's source-only ZA-03a dispatch, based on
`0c405eec9fe0a0d5efa50a7cfccdce84ea22a591`. This establishes private source
custody and structural capture. Native calibration, electrical/model applicability,
independent judgments, generated-board acceptance and release remain separate.

All 198 base inputs match `fixtures/recovery/inputs.json`. The historical package
review's 209 candidate files were checked against its exact candidate manifest
before copying. Its native source and dependencies, the accepted ADS1115 operating
supplement and fresh application declarations are pinned in the local private
Git object tree at commit `9268a4240febf7ce4272fc972760000441c58822`.
No source, PDF, manufacturer text, model or native capture was added to this
repository or pushed. Original input and sealed-review bytes remain unchanged.

The private evidence directory is
`/home/halbritt/.local/share/agent-ee/zeroasic-20261028-agent688/implementation-blocks/`.
It retains `capture_reference.py`, `capture-receipt.json`, `private-source/` and
`captures/`. The source script uses the existing `reference_blocks.capture_block`
interface; it executes no native tool. Each retained `block.json` can also be
supplied to `python -m agent_ee.reference_blocks capture` with the private Git
repository, exact commit and a new output directory. An exit of 1 is expected
for an unverified capture; it is not qualification success.

| Role | Selected references | State |
| --- | --- | --- |
| `acq-tps7a2033-power` | U1, J1, D1, C1, C2, R5, R6 | Unverified |
| `acq-ads1115-two-channel` | U2, J3, C3, C4, C5, R1, R2 | Unverified |
| `acq-jst-ph-i2c` | J2, R3, R4 | Unverified |

Each record uses `agent-ee.block/1` with an explicit root reference set, an exact
parts/1 subset and a pending applicability requirement. Its fresh application
contract retains exact part/pin identities and byte-bound engineering declarations
for the input supply, valid rail, host and analog conditions. These declarations
are not manufacturer guarantees. Source topology checks and fresh native parts
witnesses remain missing; the named interface requirement has no passing run.
Composition plans map the captured connected nets explicitly and keep
`current_a: null`. They do not establish rail/load admission or source qualification.

The J2 wording remains an unresolved boundary for nonzero-load adaptation. The
specification allows an external load of at most 1 mA but also says no load or
external supply may “drive” J2.4. The operating contract separately permits a
sinking load and prohibits sourcing current into the board. Both exact clauses
and their source identities are retained. This capture does not silently choose
an interpretation for a new nonzero-load variant. Zero external load avoids that
selection; a capacitance-only ZA-02 variant does not settle it either.

The application records also retain the thermal package attribution correction:
captured TPS7A20 SBVS338H section 5.4 gives DBV 187.1 °C/W and YCK 201.4 °C/W.
The old review called 201.4 °C/W the DBV value. Its 400 K/W sensitivity remains
an engineering assumption. The correction is recorded for a fresh version;
historical inputs and sealed results were not edited. Capacitor effective corners,
loaded regulator ground current and source/model temperature applicability retain
their original limitations. No passive guarantee is inferred from a typical curve.

The composition repair preserves U1.4 and U2.2 as explicit no-connects through
the existing connectivity IR. CPU synthetic controls establish this source behavior
only. Native follow-up must independently export the captured and composed
schematics, retain all 17 parts and two NC dispositions, run the attributable
negative cases, and complete the remaining role/interface/model checks after
calibration and separate admission. No new preparation command or broad brief
grammar is implemented by this slice.
