# AGENTEE-524 design: exact sealed-package geometry and assembly orientation

Design only. There are no production edits in this commit. Agent-566 (Opus 5.5)
wrote it for chief integrator agent-564 at immutable input `dab756e5`. It covers
REC-15, REC-16 and REC-07 (package semantics) and decisions RD-01 to RD-05.
Implementation waits for the AGENTEE-521 claims contract. Section 5 lists the
interface this design assumes from it.

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

## 2. Smallest change, part A: a roundtrip bound to the sealed package (REC-16)

**Contract.** `fab_roundtrip` params take exactly one of `export_check` (current behaviour, unchanged) or `package_check`. Both still allow the optional `model_libraries`. With `package_check`:

1. **Ordering without a cycle.** `fab_package.require_last` also admits `fab_roundtrip` checks whose `params.package_check` names this package, the same pattern as `jlc_dfm`. The roundtrip comes after the package and the package never reads the roundtrip, so there is no cycle. The relative order of the bound `jlc_dfm` and the bound roundtrip is free.
2. **Byte and candidate binding.** The check resolves the prior `fab_package` result (same candidate, `ok/pass`) and calls `artifact_record(run_dir, prior)`. It refuses (`malformed`) unless:
   - the recomputed `artifacts_id` equals `prior.details.artifacts.artifacts_id`;
   - `manifest.board_sha256` equals the captured board digest;
   - every Gerber, drill and zip file it parses has the digest the sealed record lists for it.
   The stage directory is `work/<pkg>/fab/`. `verify_tool` and `check_zip` are reused unchanged.
3. **Comparison.** The comparison body of `check_fab_roundtrip` is unchanged. The limitations text stays verbatim and the 1000 nm tolerance and the slot/attribution refusals stay as they are. Only the input resolution is split out into a small function, `_resolve_input(ctx) -> (eid, export_result, stage, binding)`.
4. **Result.** The result adds `details.package_check`, `details.artifacts_id` and `details.gerbers_zip_sha256`, plus the predicate `fab-package:sealed-roundtrip/1`. The existing predicate `fab-package:roundtrip/1` stays.
5. **Release binding.** `build_workflow._package` requires exactly one passing roundtrip bound to the package, with matching `artifacts_id`. It records the roundtrip in `accepted-package.json` as `verifications: [{check_id, kind, artifacts_id, result_sha256}]`. `fab_roundtrip` joins `MANDATORY` for build plans only. Bare-board and diagnostic scopes do not get it automatically.

It reuses `artifact_record`, `verify_tool`, `inventory`, `check_zip`, `parse_gerber`/`parse_excellon`, `expected_counts`/`expected_copper` and `compare_copper`. It adds no exporter and no new geometry predicate.

## 3. Smallest change, part B: orientation assessment by profile (REC-15)

**Profile selection (M1).** The selected assembly profile is the declared `cpl_rotation` check's captured policy (`policy_id`, `sha256`, `schema_version` 1) applied to the JLC-template export that `fab_export` already verifies (`verify_jlc_templates`). `jlcpcb-cpl/1` is the only supported profile. This design adds no scope-level profile key for M1 (see open question Q1).

**Assessment record.** Add a pure function, `fab_package.orientation_assessment(rotation, report, sealed_cpl_text)`. It returns `agent-ee.assembly-orientation/1`, a `details.assembly_orientation` field derived only from the sealed, recomputed `rotation_record`:

| Case | Per-designator `basis` | Overall `state` |
| --- | --- | --- |
| No `cpl_rotation` (`not_declared`) | `unassessed`, for both angle and Mid X/Y (anchor, not pad centroid) | `unassessed` |
| Passing report, row with no policy rule or a 0° rule, pin-1 agrees with delta 0 | `native_supported` (native angle kept, pin-1 evidence cited) | |
| Passing report, row with a nonzero rule, pin-1 agrees with the delta | `corrected` (rule id, before/after angle, centroid Mid X/Y) | |
| All rows `native_supported` or `corrected` | | `assessed`, `supplier_preview: required` (status `PREVIEW_REQUIRED`) |
| `ok` | not reachable in M1. It needs a captured preview-evidence kind that does not exist yet. | |

A native angle passes only as `native_supported`, and only with that pin-1 evidence. A failing or incomplete rotation report already makes `fab_package` fail or come back `unsupported`, and that behaviour stays. `DONE_ROTATION_STATUSES`, the preview obligations and their sealing stay the same, so bare-board and diagnostic done gates are unaffected. The assessment only feeds the separate supplier claim in section 5.

## 4. Decisive focused tests (synthetic, offline, extending the existing suites)

**`tests/test_fab_roundtrip.py`, sealed-package mode:**

| # | Case | Expected |
| --- | --- | --- |
| T1 | Pass on a sealed package | Pass; `artifacts_id` equals the package's |
| T2 | One Gerber byte flipped after sealing | Refuses (digest) |
| T3 | Gerber edited and manifest re-hashed consistently | Refuses (`artifacts_id` mismatch with the prior result) |
| T4 | Pad removed from an F.Cu plot, all hashes rewritten | Conservation `defect` |
| T5 | PTH drill diameter changed | `drill_histogram` defect |
| T6 | Zip member differs from the staged file | `zip` refusal |
| T7 | Stale board digest | Refusal |
| T8 | `package_check` naming a later or non-package check | `unsupported` |
| T9 | Both `export_check` and `package_check` | `unsupported` |
| T10 | Existing `export_check` tests | Unchanged |

**`tests/test_fab_package.py`:**

| # | Case | Expected |
| --- | --- | --- |
| P1 | `require_last` | Admits a bound roundtrip; refuses an unbound one |
| P2 | `orientation_assessment` for `not_declared` | `unassessed`, with every CPL designator listed |
| P3 | Passing report with mixed rules | Correct `native_supported`/`corrected` split; designators equal the sealed CPL |
| P4 | Report where a CPL row is missing from it | Refusal |
| P5 | Changed CPL coordinate or angle after sealing | `artifact_record` refusal |
| P6 | Unsupported profile (policy schema 2 or unknown key) | Existing `cpl_rotation` refusal, so the package is `unsupported` |

**New `tests/test_build_package.py` (no existing test exercises `build_workflow._package`; `test_build_review.py`/`test_build_artwork.py` cover the neighbouring stages):**

| # | Case | Expected |
| --- | --- | --- |
| B1 | `_package` without a bound passing roundtrip | Refused |
| B2 | Roundtrip with a mismatched `artifacts_id` | Refused |
| B3 | `accepted-package.json` | Carries `verifications` and `assembly_orientation`, never `assembly_ready: true` |

**Native acceptance (needs the AGENTEE-519 runtime).** Recheck the retained acquisition release package under the pinned image with a bound roundtrip. Expected: pass on the sealed files, orientation `unassessed`. Then run T2, T4 and T5 controls on a copy.

## 5. Interface assumed from AGENTEE-521 (chief to reconcile)

| 521 claim state | Inputs from this design | Ceiling in M1 |
| --- | --- | --- |
| `file_release` | `fab_package` pass plus the bound roundtrip pass with equal `artifacts_id` | Can pass |
| `supplier_assembly` | `assembly_orientation.state`; `unassessed` blocks it | At most `pending_preview`, with the designator list. Never `ready` without captured preview evidence. |
| `physical` | None from this design | Unchanged: `physical_validation_performed=False` |

If 521 names these states differently, only the mapping changes. Sections 2 to 4 stand.

## 6. Reuse assessment: AGENTEE-449 (`wt/ee449e-root`, `1356da4b`, unaccepted)

- **Reusable as a pattern, not as code:** `require_last` admitting bound post-package checks, the `assembly_ledger` that keeps preview obligations as open entries, and `artifact_record` with `final=False`.
- **Not reused:** `board_fab` does not parse geometry or orientation, so it does not cover this item.
- **Conflict risk:** if 449 is later integrated, its `require_last` change and part A's change edit the same predicate. Merge them as one predicate over a set of bound kinds.

## 7. Blockers and open questions

- **Q1 (chief/521):** confirm that "selected profile" means the declared rotation policy for M1, not a new scope key.
- **Acquisition orientation:** acquisition has no captured rotation policy or EasyEDA footprints for its 17 CPL rows. Without them it stays `unassessed`, which is honest but not assembly-ready. Assessing it needs a bounded capture of public supplier footprint data plus an engineering-authored policy. That is a separate dispatch decision, with no supplier upload.
- **Runtime:** native requalification depends on AGENTEE-519.
