# AGENTEE-524 design: exact sealed-package geometry and assembly orientation

Design only, with no production edits. Agent-566 wrote it for chief integrator
agent-564 at immutable input `dab756e5`. Revision 2 answers the chief review of
`40c070de`. The model in this session's native context is `claude-opus-5-5`
(Opus 5.5). The registry lists Fable 5.1; that record is the chief's to correct.

Covers REC-15, REC-16 and REC-07 (package semantics) and decisions RD-01 to
RD-05. Captured inputs stay immutable. No blanket rotation transform is applied.
There is no supplier upload or order.

## 1. Current checker facts (at `dab756e5`)

| Fact | Source |
| --- | --- |
| `fab_roundtrip` resolves its input only through `fab_consistency.export_prerequisite`. It needs an earlier check of kind `fab_export` and reads `work/<export>/fab/`. It cannot name a `fab_package`. | `fab_roundtrip.py:741-743`, `fab_consistency.py:133-162` |
| `fab_package` runs `fab_export` inside itself, into `work/<pkg>/fab/`, then runs `fab_consistency.gate` on that tree, then seals `agent-ee.fab-artifacts/1`: gerbers.zip, the drill files, bom.csv, cpl.csv (or the corrected CPL) and manifest.json. | `fab_package.py:282-357` |
| `require_last` lets only `jlc_dfm` checks with `params.fab_check == <pkg>` follow `fab_package`. A roundtrip after the package is refused today, and a roundtrip before the package checks a different export. | `fab_package.py:143-148` |
| `artifact_record(run_dir, check)` recomputes the sealed record from evidence. It checks the manifest digests (`verify_manifest`), sealing, candidate binding and `require_final`, and returns `artifacts_id`. `jlc_dfm` already consumes the package through this function. | `fab_package.py:223-279`, `jlc_dfm.py:141-163` |
| `sealed_versions`/`verify_tool` look up `work/<eid>/**/execution.json` in the given result's evidence. The package's internal export writes under `work/<pkg>/`, so both work unchanged with `eid=<pkg>` and the `fab_package` result. | `fab_consistency.py:250-296` |
| The roundtrip only checks count, diameter and plating, pad extents, track/arc/zone counts and straight outline bounds within 1000 nm. Slots and unattributed plots are `unsupported`. Rendering, hole positions and drill maps are not compared. | `fab_roundtrip.py:1-4, 829-831` |
| Rotation statuses are `not_declared` (no `cpl_rotation` check: the native anchor/angle CPL is sealed), `ok` and `PREVIEW_REQUIRED`. `cpl_rotation` always emits `PREVIEW_REQUIRED` with `physical_validation: false`, so `ok` cannot be reached today. `DONE_ROTATION_STATUSES` admits all three for a generation done gate. | `fab_package.py:53-58`, `cpl_rotation.py:663-689` |
| A passing `cpl_rotation` report has complete coverage. Every CPL row has board and EasyEDA pin-1 classification that agrees with the policy delta (`supplier_missing`, `pin1_*`, `pad_set_mismatch` and `polarity_unknown` are `unsupported`; reflection, rotation conflict and polarity conflict are defects). It replaces the anchor Mid X/Y with the pad-box centroid. | `cpl_rotation.py:76-86, 395-422, 485-492, 640-671` |
| The retained acquisition package (`release-build-v7`, artifacts `df866200…`) declares `netlist`, `geometry`, `fab_package` and no `fab_export`, `fab_roundtrip` or `cpl_rotation`. Its rotation is `not_declared` and its CPL has 17 rows, all Top. No rotation policy or EasyEDA supplier footprints were captured in its candidate. Its geometry probe includes every fact the roundtrip needs (`flash_layers`, `drill_nm`, vias, `zone_fill_outlines`, `copper_conductor_shapes`, proven outline). | `build/acquisition-release/release-build-v7/stages/07-package-*/scope.json`, `accepted-package.json`, `review/work/geometry/geometry.json` |
| `build_workflow._package` writes `accepted-package.json` from the single passing `fab_package`, with `supplier_preview_performed=False`. It does not check that the exports' geometry was preserved. | `build_workflow.py:200-217` |


## 2. Part A: a roundtrip bound to the exact released bytes (REC-16)

**Contract.** `fab_roundtrip` params take exactly one of `export_check` (current behaviour, unchanged) or `package_check`. Both still allow the optional `model_libraries`.

1. **Ordering without a cycle.** `fab_package.require_last` also admits a `fab_roundtrip` whose `params.package_check` names this package, the same pattern as `jlc_dfm`. The package never reads the roundtrip.
2. **Binding to the prior result.** The check resolves the same-candidate `ok/pass` `fab_package` result and calls `artifact_record(run_dir, prior)`. It refuses unless the recomputed `artifacts_id` equals `prior.details.artifacts.artifacts_id` and `manifest.board_sha256` equals the captured board digest.
3. **Comparing exactly the released bytes.** Today the parsers read **staged files** a second time: `read_text` → `read_bytes` runs after `inventory` has already hashed them (`fab_consistency.py:420-466`). `check_zip` then compares archive members with the staged files, and it runs only when no file is missing (`fab_roundtrip.py:822-824`). In sealed mode the sealed set is `gerbers.zip` plus `drill/*`, so this changes to:
   - Read `gerbers.zip` once and check its sha256 against the sealed record.
   - Parse each Gerber and drill **archive member from those in-memory bytes**. Recording the member sha256 is the retained proof.
   - Require each member's sha256 to equal the sealed digest of the same-named staged file, and the archive to have no extra or duplicate members (reusing the `check_zip` rules).
   - Read each sealed `drill/*.drl` once, hash the exact bytes, and require them to equal the zip member.

   `details.compared` lists `{member, member_sha256, sealed_path, sealed_sha256}` for every parsed input. Any mismatch refuses before a verdict is reached. `export_check` mode keeps its current path.
4. **The geometry reference is independent of the package.** The expected counts come from the earlier sealed `geometry` checks: pcbnew facts of the captured native board, from `probe_facts`. They do not come from the export. Whether the package bytes are honest and whether they preserve native geometry are therefore separate questions.
5. **Reuse.**
   - Unchanged: `artifact_record`, `verify_tool` and `sealed_versions` (both work with `eid=<pkg>` because the package's export evidence lives under `work/<pkg>/`), `parse_gerber`, `parse_excellon`, `expected_counts`, `expected_copper`, `compare_copper`, `histograms_match`, `plot_layer`, `reconcile_layers` and `board_facts`.
   - Factored: the member-safety rules of `check_zip` become a reader that returns the member bytes.
   - New: only `_resolve_input` and `_sealed_members`.
6. **Result.** Adds `details.package_check`, `artifacts_id`, `gerbers_zip_sha256` and `compared`, plus the predicate `fab-package:sealed-roundtrip/1`. The limitations text and the 1000 nm tolerance stay verbatim. This is an explicit **file-release verification** record. It makes no claim beyond what the checker supports.

## 3. Part B: explicit, profile-bound orientation assessment (REC-15)

### 3.1 The profile is explicit evidence

A new captured, immutable `agent-ee.assembly-profile/1` file is declared on `fab_package` by a new param, `assembly_profile`. `export_params` strips it, the same way it strips `model_libraries`. Fields:

| Field | Content |
| --- | --- |
| `profile_id` | `jlcpcb-smt-cpl/1`, the only supported id |
| `supplier`, `service` | The supplier and service the profile applies to |
| `cpl_convention` | Position is the pad-box centroid in absolute X with Y negated; rotation is the native angle plus the policy delta, with the bottom side mirrored as in `cpl_rotation` |
| `rotation_check`, `policy_sha256` | The `cpl_rotation` check this profile uses and the policy it must have applied |
| `applicability` | Allowed sides, and whether through-hole parts are in scope |
| `sources` | Citations for every convention claim |

`orientation_assessment(profile, rotation_record, report, sealed_cpl)` refuses `unsupported` in each of these cases:
- the profile id is unknown;
- the profile's `policy_sha256` differs from the sealed rotation policy;
- a CPL row's side or mount type is outside `applicability`;
- the profile is missing while assembly orientation is requested.

The presence or absence of a rotation check never stands in for a profile.

### 3.2 States (sealed in `details.assembly_orientation`)

| Per-designator `basis` | Meaning |
| --- | --- |
| `unassessed` | No profile, or no `cpl_rotation` (`not_declared`). Covers both the angle and the anchor Mid X/Y. |
| `native_supported` | No rule or a 0° rule, and the board/EasyEDA pin-1 frames agree with delta 0 in a passing, complete report |
| `corrected` | A nonzero per-footprint or per-designator rule, with pin-1 frames agreeing with that delta |

| Overall field | Value |
| --- | --- |
| `software_assessment` | `complete` only when every sealed CPL row is `native_supported` or `corrected` under the profile |
| `supplier_confirmation` | Always `pending_preview` in M1 (`PREVIEW_REQUIRED`, with `preview_obligations` unchanged). `confirmed` needs a captured preview-evidence kind that M1 does not create. |

`DONE_ROTATION_STATUSES` and the bare-board and diagnostic done gates are unchanged.

### 3.3 Real acquisition inputs (probed 2026-10-25, read-only, nothing captured into the candidate)

The acquisition CPL has 17 rows, all Top, with no exclusions, covering 11 unique LCSC parts. I fetched the public EasyEDA endpoint `https://easyeda.com/api/products/<LCSC>/components?version=6.4.19.5` once per part and ran the existing strict reader (`parse_package(result, lcsc, 200)`) on the returned `result` object:

| LCSC | Rows | EasyEDA package | Reader |
| --- | --- | --- | --- |
| C15850 | C1, C2 | C0805 | parses |
| C14663 | C3 | C0603 | parses |
| C85973 | C4, C5 | C0603 | parses |
| C83152 | D1 | SOD-123 | parses (D* polarity cross-check applies) |
| C131337 | J1 | CONN-TH B2B-PH-K-S | parses (through-hole: applicability must say so) |
| C131334 | J2, J3 | CONN-TH B4B-PH-K-S | parses (through-hole) |
| C22548, C114662, C114065 | R1–R6 | R0603 | parses |
| C37593 | U2 | MSOP-10 (KiCad TSSOP-10_3x3mm_P0.5mm) | parses; a pad-set match is unproven until the run |
| **C2862740** | **U1** | SOT-23-5 | **refused**: `PAD gge308 number '3': outline differs from declared rectangle`. Still refused at 25,400 nm, so this is a genuine record disagreement, not a tolerance problem. |

**Bounded capture and policy work. This is M1 work, not deferred.**
1. Capture each `result` object byte-exact as `supplier/<LCSC>.raweasy.json`, with `agent-ee.supplier-footprint/1` provenance (real `source_url`, `retrieved_at`, `retrieval`). The captures go in a **new** captured input directory. The retained `release-build-v7` stays untouched. This is a fetch only, with no upload.
2. **Policy.** Write one rule per footprint prefix, and only where the board-vs-supplier pin-1 cycle difference is nonzero. Each rule cites its pin-1 evidence and the source of the EasyEDA frame. Footprints with a zero difference get no rule and come out `native_supported`. No blanket transform. If a rule rests only on the pin-1 frame comparison, its provenance says so, and the profile labels that basis software-assessed only.
3. **U1 (the blocked subset is exactly one row).** The strict reader refuses C2862740, so `cpl_rotation` reports `supplier_missing`/unsupported and coverage is `incomplete`. Software assessment therefore cannot be `complete`, and the result is honest refusal, not a pass. Alternatives that would be sufficient under the profile, in order:
   - (a) A focused reader change that classifies this pad shape exactly, with a regression on the captured bytes. Pad 3 must not be dropped or approximated.
   - (b) A second public CAD source for the same LCSC footprint (for example the LCSC/EasyEDA Pro export of the same part) that the reader accepts, cited in the profile.
   - (c) An owner-saved JLC placement-preview record for U1. This would be supplier confirmation, not software assessment, and needs owner action because the preview uses a project upload.

   Without one of these, M1 records "16 of 17 assessed, U1 unsupported" and the finding stays open. The design does not call it complete.
4. **Real-design proof.** Run `cpl_rotation` plus the profile on the retained acquisition release inputs. Expected:
   - 16 rows `native_supported` or `corrected`, and U1 unsupported until (a), (b) or (c);
   - a full pass after (a) or (b);
   - the sealed CPL is the corrected file, and the native CPL is retained.

## 4. Decisive focused tests

**`tests/test_fab_roundtrip.py`, sealed mode.** Each geometry test asserts that the binding gates **passed**, so it cannot fail at the digest gate by accident:

| # | Case | Expected |
| --- | --- | --- |
| T1 | Honest package | Pass; `compared` lists every member with sha256 equal to its sealed file; `artifacts_id` matches |
| T2 | Tamper after sealing (flip a byte in a staged Gerber or zip member) | Digest refusal and no geometry verdict. Asserts `compared` is absent |
| T3 | Gerber edited, then manifest re-hashed without updating the prior result | `artifacts_id` mismatch refusal |
| T4 | **Exporter fault, honestly sealed.** Fixture: build the export staging, delete one F.Cu pad flash from the Gerber, then regenerate the manifest, zip, sealed evidence and `fab_package` result with the existing helpers, so every hash and `artifacts_id` is self-consistent. Asserts `artifact_record` succeeds and `compared` hashes all match. | Conservation `defect` against the independent native `geometry.json` (pad count/extents on F.Cu) |
| T5 | Same setup with one PTH tool diameter changed | `drill_histogram` defect, under the same pass assertions on the binding gates |
| T6 | Staged drill file differs from its zip member, both sealed | Refusal naming the member |
| T7 | Stale board digest | Refusal |
| T8 | `package_check` naming a non-package or later check | `unsupported` |
| T9 | Both params given | `unsupported` |
| T10 | Existing `export_check` tests | Unchanged |

**`tests/test_fab_package.py`:**

| # | Case | Expected |
| --- | --- | --- |
| P1 | `require_last` | Admits a bound roundtrip; refuses an unbound one |
| P2 | No profile, or `not_declared` | Every row `unassessed` |
| P3 | Unknown `profile_id`, `policy_sha256` mismatch, row outside applicability | `unsupported` |
| P4 | Mixed rules | `native_supported`/`corrected` split; designators equal the sealed CPL |
| P5 | Passing report with one `supplier_missing` row | Unreachable: the report is incomplete, so the package fails. Tests that incomplete coverage cannot produce `complete` |
| P6 | Altered sealed CPL coordinate or angle | `artifact_record` refusal |
| P7 | `supplier_confirmation` | Stays `pending_preview` whatever the inputs |

**Native.**
- N1: sealed-mode roundtrip on the retained acquisition package (expect pass) under the AGENTEE-519 runtime.
- N2: the section 3.3 real-design orientation run.
- N3: T4/T5-style faults injected into a **copy** of that package.

## 5. AGENTEE-521 boundary

The 521 states are independent qualification dimensions, not a ladder. 524 produces two package-level records and touches **no** `build_workflow` code:

- `fab_roundtrip.details` (file-release verification, part A);
- `fab_package.details.assembly_orientation` (part B: `software_assessment` and `supplier_confirmation`).

How the build stage consumes them is integrated after 521 lands, with a single owner for that stage. The earlier proposal to edit `MANDATORY` and `_package` is withdrawn.

## 6. Reuse assessment: AGENTEE-449 (`wt/ee449e-root`, `1356da4b`, unaccepted)

- **Reusable as a pattern, not as code:** the bound post-package `require_last` set and the `assembly_ledger` that keeps preview obligations as open entries.
- **Not reused:** `board_fab` does not parse geometry or orientation.
- **Conflict risk:** if 449 is integrated later, merge its `require_last` change with part A's as one predicate over a set of bound kinds.

## 7. Recommended smallest implementation handoff

| Slice | Content | Dependencies |
| --- | --- | --- |
| Slice 1 (builder) | Part A only: `fab_roundtrip.py` sealed mode, factoring the `check_zip` member reader, and `require_last`, with T1–T10 and P1. | Needs only the 519 runtime for N1 |
| Slice 2 (capture) | The 11 acquisition captures with provenance in a new input directory, plus the pin-1 survey report that becomes the policy evidence. | No production code |
| Slice 3 (builder) | Part B: the profile schema and `orientation_assessment`, with P2–P7, then N2. | After slice 2 and the 521 contract |
| U1 | Decide among 3.3 (a), (b) and (c). (a) is a focused reader change with its own regression. | Chief's choice |
