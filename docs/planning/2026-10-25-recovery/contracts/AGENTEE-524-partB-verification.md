# AGENTEE-524 part B: explicit assembly-profile assessment, real acquisition run

Agent-566 (session model Fable 5.1 at the time of this record) wrote this on
2026-10-25 for chief agent-564. It is evidence for review, not acceptance;
AGENTEE-524 stays open until the package/orientation consumer (AGENTEE-521,
agent-568) accepts the record. No supplier contact, upload or order occurred.

## What was implemented

- `src/agent_ee/assembly_profile.py`: `validate_profile`, `mount_types` (from
  sealed pcbnew pad plating), `orientation_assessment`, `unassessed_record`.
- `src/agent_ee/fab_package.py`: optional `assembly_profile` param (captured,
  immutable, requires a declared `cpl_rotation`, stripped from the export with
  `model_libraries`); `details.assembly_orientation` =
  `agent-ee.assembly-orientation/1`. `artifact_record` and `artifacts_id` are
  unchanged, so part A's sealed-roundtrip binding is unaffected.
- `tests/test_assembly_profile.py` (9 tests, real export, `cpl_rotation` and
  done gate on the owned CPL control): P2 unassessed record with the artifact
  record unchanged; P3 unknown id, stale policy digest and wrong rotation check
  are `unsupported`, and a Top/smd-only profile lists through-hole and bottom
  rows as `unassessed_rows`; P4/P7 `corrected` vs `native_supported` taken from
  the real report, with `anchor.changed` reported separately (a through-hole pin
  header keeps its native angle while its centroid differs from its anchor);
  P5 missing row, incomplete coverage, unsupported row and pin-1 disagreement
  refuse; P6 a moved sealed corrected CPL is `malformed` before assessment.
- Docs: `docs/fab-package.md` profile section, `docs/check-reference.md` row,
  and corrections to the stale wording in `AGENTEE-524-package.md`.
- The result-field contract was sent to agent-568 (Cairn notice `582742a9`).

## Private input revision (not committed: third-party CAD)

`~/.cache/agent-audits/agent-ee-566-20261025/cpl-inputs-rev2/`:

- `supplier/`: the same 11 byte-exact EasyEDA captures and provenance records
  as the survey (no refetch); originals in `easyeda-probe/` unchanged.
- `jlc-cpl-policy.json` (`36b349c7…db4b`), policy
  `agent-ee-acquisition-jlc-cpl-v1`: four footprint-prefix rules, each equal to
  the supplier-to-board pin-1 offset the zero-rule survey measured
  (`JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical` 180, `JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical`
  180, `SOT-23-5` 180, `TSSOP-10_3x3mm_P0.5mm` 270). Rows with a measured 0°
  offset have no rule. The provenance cites the survey report hash.
- `assembly-profile.json` (`jlcpcb-cpl-software/1`): applicability Top with
  `smd` and `through_hole`, with a labelled engineering rationale that the three
  JST PH connectors are through-hole and that this does not claim JLCPCB will
  assemble them; `rotation_check` `cpl`; `policy_sha256` as above.

## Real-design run (`acq-partb-2`)

| Item | Value |
| --- | --- |
| Source | `877b4153` plus the staged part B edits (`diff.patch` retained) |
| Image | `sha256:65332166…8e7e`, `--network none --pull never`, checkout and retained build read-only |
| Scope | The v7 package scope verbatim plus `fab-export`, `cpl` (`cpl_rotation`, tolerance 200 nm unchanged) before `fab`, the rev2 inputs captured at `cpl/`, and `fab.params.assembly_profile`. Candidate `371b4c18…5ed7`; board `d0a898a2…` unchanged |
| Command | `agent-ee review --checks-only`, 56 s |
| Evidence | `checks/cpl.json` `6a16f957…`, `checks/fab.json` `efd25975…`, `work/cpl/cpl-rotation.json` `a47b4e65…`, `candidate.json` `c000f63c…`, `result.json` `0afc8ef0…` |

Per-row result of the real `cpl_rotation` report:

| Rows | Result | Pin-1 evidence |
| --- | --- | --- |
| C1–C5, R1–R6 (11) | pass, no rule, offset 0° | board `leftside_top` = supplier `leftside_top` |
| J1, J2, J3 | pass, rule 180° applied | offset 180° (THT; in profile by labelled rationale) |
| U1 (SOT-23-5, C2862740) | pass, rule 180° applied | offset 180°; parses after the reader repair |
| U2 (TSSOP-10 vs MSOP-10, C37593) | pass, rule 270° applied | offset 270°; 10 pads matched, no pad-set mismatch |
| **D1 (SOD-123, C83152)** | **unsupported: `polarity_unknown`** | offset 0°, but board pin-1 function `None` and supplier pin 1 named only `"1"` |

Consequences, all retained rather than relaxed: the report is `unsupported` /
`unknown` / `incomplete`; the done gate is `unsupported` ("declared cpl_rotation
'cpl' has no passing result"); no assembly orientation record is produced for
acquisition. 16 of 17 rows have complete frame evidence, but the whole-17-row
software assessment is **not complete**, and this record does not call it so.

The first run (`acq-partb-1`) matched no rule because I wrote the prefixes with
the library name; `match_policy` compares library-less footprint names. The
corrected policy is the one hashed above. That run is retained.

## Decisive blocker and smallest remedy

D1's polarity cross-check needs both sides. The board side exists in sealed
evidence the package already requires: the native XML netlist records
`D_Schottky` pin 1 `K`, pin 2 `A`, while the generated board has no
`pinfunction` on any pad. The supplier side does not exist in captured CAD:
the EasyEDA symbol pins for C83152 are named `1` and `2`.

Smallest remedy, in order of preference, none implemented here:

1. `cpl_rotation` reads the board pin-1 function from the sealed native XML
   netlist when the board pad lacks `pinfunction` (exposes an existing captured
   fact; a focused change, with a regression on this board). This resolves the
   board side only.
2. For the supplier side, a captured, sourced per-designator polarity
   declaration (for example datasheet BAT46W-7-F pin 1 = cathode and the
   LCSC/EasyEDA footprint pad-1 marking) that `cpl_rotation` accepts only with
   both the board fact and a citation. This is new behaviour and needs its own
   dispatch; a declaration without a source must stay `polarity_unknown`.
3. Otherwise, an owner-saved JLCPCB placement preview for D1 is supplier
   confirmation, not software assessment, and involves an upload.

## Limits

- The assessment proves pin-1 frame agreement and anchor handling; it does not
  render artwork or confirm supplier behaviour.
- Through-hole applicability is an engineering rationale in the profile, not a
  supplier commitment.
- The full strict suite was not run; the affected suites were.

## Chief integration verification

Agent-564 independently ran the five affected modules in the pinned native
container: assembly profile, fab package, sealed roundtrip, JLC DFM and CPL
rotation. The strict gate passed 95 tests with no skips. The chief replaced
a vacuous profile-capture assertion with a check of the real immutable-input
descriptor and a missing-capture negative; that amended test also passed in
the native container. Logs are retained under
`~/.cache/agent-evidence/agent-ee-564-integration/assembly-profile-native.log`
and `assembly-profile-capture-guard.log` beside it.

The chief read and recomputed the hashes of the real acquisition CPL check,
package check, rotation report and candidate record above. They confirm the
16 passing rows and D1's unresolved polarity; this is a verified refusal,
not completed acquisition orientation qualification. A separate bounded
source investigation is assigned to agent-566 before a polarity reader change
is selected. No preview or supplier upload was authorized.
