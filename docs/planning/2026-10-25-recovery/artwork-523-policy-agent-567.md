# AGENTEE-523: semantic artwork policy and intrinsic finding comparison

Agent-567, 2026-10-25. Base `aa6bffc9074ba80e18df66c74c7b5c7c97f52609`.
Chief selection: Cairn `4b44b0ec-02aa-4d20-910f-d901f451e0be@1`;
reviewed continuation `2a1cd20a-af45-451b-880a-bc42adb140ba@1`.
This repairs bounded finalization and evidence behavior. It does not accept
esp_prog artwork, waive native findings or close AGENTEE-523/M1.

## Policy enforced by producer and receipt replay

The earlier [sibling proof](artwork-523-sibling-proof.md) preserves primitive
shape and style, but can accept independently translated segments of a connected
component outline. Graphics now remain anchored unless an explicit
`agent-ee.artwork-policy/1` request classifies them as a free rigid group.
Missing native group objects and primitive kinds confer no free status.

The request contains `source_sha256` and `free_groups`. Each group names an exact
`owner_uuid` (null for board drawings) and a nonempty list of member UUIDs.
The producer checks source bytes, membership uniqueness, supported visible silk
graphics, owner applicability and common side. Every member must retain one
common translation and its exact relative geometry/style; missing or changed
members refuse. Unlisted graphics stay anchored. No resize, hiding or deletion
authority is introduced. An empty free-group list explicitly anchors all graphics.

`step(..., artwork_policy=policy)` records the canonical request in
`--artwork-policy`. `receipt_scope` decodes it, and existing `qualify_receipt`
recomputes `write_scope` against the receipt's exact source/output. Group-member
removal, independent movement and changed membership are rejected during replay.
The existing field targets and exact field-pose requests remain separate,
exclusive scopes. `build_workflow`, qualification modules and `placement_job`
are unchanged; the workflow's explicit field-only targets retain their behavior.

Native group search reuses the existing 0.25 mm / 10 mm bounded search, obstacle
views, courtyards and proven outline. All members receive the same offset and
are tested together, excluding only their own group from obstacles. Failure
restores the complete group and retains unresolved member identities. Component,
body and polarity artwork is anchored by default; free classification is an
explicit source-specific decision, not a geometric inference by this search.

## Intrinsic text findings and complete residuals

`compare_artwork_drc` independently rechecks the complete raw write scope and
the saved native DRC pins. Pins bind each board, adjacent project/custom-rule
payload and complete report. Evaluated severities and ignored-check settings
must remain equal. Only moved single-item `text_height` findings with unchanged
text/owner/style/angle qualify for intrinsic comparison. An explicitly allowed
field rotation does not become a translation-only comparison exception.

The comparison excludes only the proved item position. Type, severity, exact
description (including rule, limit and actual), item identity/description,
provenance and finding multiplicity remain in the key. Changed pins, metadata,
source style or copper refuse or remain added findings. The original warnings
remain in the native baseline/candidate/advisory views and final grading. Generic
numeric, clearance and collision comparison is unchanged.

Final artwork uses `ARTWORK_CONFLICTS`, including `silk_edge_clearance`, for
selection and open residuals. Shared `SILK_CONFLICTS` and placement carry semantics
are unchanged. Edge-only residuals keep the producer verdict at `defect`.

## Verification and retained-board evidence

The new authored controls cover policy/source/owner/inventory binding, common
group translation, anchored outlines, malformed/hidden members, canonical and
exclusive receipt scopes, severity/rule/actual/identity/count/provenance/settings
and board/report/rule pin counterexamples. Native controls demonstrate grouped
movement and receipt rejection, plus exactly two baseline height findings kept
visible alongside an unresolved edge defect. Existing authored graphic fixtures
now opt in explicitly; imported pod fixtures are not reclassified by the helper.
The old damaged 85-primitive proposal is no longer valid under anchored policy.

Private evidence root:
`/home/halbritt/.cache/agent-evidence/agent-ee-523-policy-agent-567/`.
It retains original gate failure, reviewed patch, corrected sequence, test logs,
source/runtime/artifact hashes and the single retained producer verification.
Native tests use `ae-ci-exec` / `native-admit`, Python 3.12.3 and KiCad 10.0.7 image
`sha256:939f4bfb6bb45eaff5fc47d27b94f905ec979e31ec66a190fe6fb57ff96753e1`.

Final validation passes **133 tests without skips**: 106 tests in six official
strict-suite modules, plus 27 selected `RoutedArtworkTest` cases with skipped,
expected-failure and unexpected-success results explicitly refused. Historical
`RealPodArtworkTest` cases are outside this esp_prog repair and were not run.

Exactly one 180-second-bounded retained producer verification ran after the
initial focused tests. It completed in 2.676 seconds with `verdict=defect`, no added
findings, no moved identities and an output byte-identical to the original:
`dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1`.
Its explicit source-bound policy has no free groups. All graphics remain anchored;
the earlier 85-primitive proposal refuses. The complete open residuals are 165
`silk_overlap`, 49 `silk_over_copper` and three `silk_edge_clearance` (217 total),
including all 17 defects localized in the prior investigation. Thirteen fields
retain `text_below_declared_minimum` diagnostics and 94 cited graphics retain
`anchored_artwork` diagnostics. The reduced defect count in the old independently
moved proposal is not semantic artwork acceptance. No second attempt ran.

The native two-warning positive is a separate authored fixture with two explicit
free text groups; the retained anchored run performs no intrinsic normalization
because it moves nothing. Both native findings remain visible in that fixture,
and its unresolved edge marking still gives a defect verdict.

Post-run review corrected a visibility parser case: a visible label whose text
is `hide` must not be mistaken for a visibility attribute. The final tree passes
the complete scoped tests, including a native label with that exact content.
No second retained producer ran. The final source independently recomputes the
exact saved raw proof and complete DRC comparison from the sole native run;
`retained-verification/final-source-replay.json` records the native-run and final
source hashes separately. The native run used an empty free-group list; the
changed visibility check was not exercised in that retained run.

The first origins invocation excluded the new untracked authored test from
`git ls-files`; work stopped as dispatched and the draft was preserved. The
chief reviewed the exact failure and authorized restoring the exact patch,
staging the test and its precise existing declaration, and rerunning the
unchanged gate. That corrected check passes. No origin rule was broadened and
no checker was modified.

## Remaining formatting and outline decisions

The existing minimum-size guards are preserved. The retained native profile's
text minima and actual dimensions are recorded with its project/rule hashes;
these runtime settings are not newly inferred owner requirements. A future
formatting repair must select an explicit sourced text profile and authorize
style changes through a separate proof contract. Translating 0.7 mm reference
fields does not satisfy the current 0.8 mm native minimum; shrinking to fit or
silently dropping that guard is not a repair.

For the one retained run, `BOARD.GetDesignSettings` under KiCad 10.0.7 gives
0.800 mm minimum text height, 0.080 mm minimum text thickness and 0 mm native
silk clearance. The unchanged conservative search still uses its 0.200 mm margin
floor. The adjacent captured project SHA-256 is
`9a9ab694fc8a9a456a6791060d998c01a171e30308a170e1b4f97d864b2b6395`;
no custom-rule file is present. These exact loaded settings, source/runtime hashes
and unchanged rule payloads are recorded in `retained-verification/summary.json`
and the producer's DRC pins. All native warnings retain their original grading.

CON1, U1 and USB1 body/polarity outlines require a coherent source-artwork
decision. Moving independent segments breaks them; moving a complete body outline
off its pads can lose its meaning even when relative geometry is preserved.
Any future footprint-silk redesign must preserve assembly/polarity information,
clear mask/edge constraints, and have explicit edit authority. This slice supplies
no such redesign or deletion permission. Final readable renders, classification
approval, native DRC, via/pad assembly assessment and M1 acceptance remain separate.
