# Retained esp_prog intent compatibility

Decision by chief integrator agent-564, 2026-10-25, while executing RD-02 and AGENTEE-520. This corrects one inconsistent slice acceptance criterion; it does not narrow M1 or change the representative design.

## Observation

Agent-568 reported that the exact retained esp_prog intent is a KRT `floorplan-intent` schema-1 document with declarations the public agent-ee job never consumes. Agent-564 inspected `build/agent545/inputs/intent.json`, `groups.json`, and `_capture_native` at `95af4de1`. The original input hashes remain recorded in `baseline.json`.

The intent declares front assembly, front-facing headers, an envelope, a zero out-of-bounds budget, empty keepout/block/lock declarations, fixed mechanical fiducials, edge conditions and proximity relations. The groups file includes qualitative `near` relations and explanations. `_capture_native` consumes selected fixed/edge/proximity fields and group name/anchor/refs. Passing the full original file through a strict supported-field boundary while continuing to ignore those other fields would contradict REC-01.

## Decision and acceptance allocation

AGENTEE-520 first implements truthful refusal. Its positive controls are acquisition and inputs that use the supported contract. The exact original esp_prog file is a required negative until its declarations have explicit handling. Neither a filtered input file nor an undocumented conversion counts as unchanged compatibility. This corrects the earlier 520 criterion that demanded both refusal of unsupported intent and immediate success of this richer input.

AGENTEE-479 owns the representative compatibility mapping together with functional-group enforcement. Keep the original board, project, intent and groups immutable. A versioned derived input may change representation only with a source hash and a field-by-field disposition explaining equivalence. Bind the derived representation and original declarations into capture and final evidence. Unknown or unmapped operative semantics must refuse the dependent release. The original article remains the M1 case; do not substitute a smaller or easier board.

| Retained declaration | Required handling before M1 |
| --- | --- |
| Native identity, outline and envelope | Preserve the native source and prove the declared rectangular envelope against it; report the original tolerance and authority. Do not redraw the board or loosen its boundaries. |
| `assembly.sides`, connector `side`, fixed-pose side | Check the original permitted/native face and final saved face. A declaration compatible with preserved faces does not require adding side-flip search. A conflict must refuse. Preserve the distinction between observed baseline and authored requirements. |
| `legality_budget.oob_count = 0` | Bind to the actual applicable native/placement no-outside predicate and describe its geometry semantics. Do not assert equivalence from the matching number alone. |
| Empty `keepouts`, `blocks`, `must_lock`, `decaps` | Explicitly validate that they are empty and preserve that declared absence. Nonempty unsupported forms remain refusals. |
| Edge conditions, fixed poses and pad proximity | Preserve each supported constraint, its units, provenance and original metadata. Verify native metric equivalence; an upstream pad-edge metric cannot silently become pad-center distance. Derived defaults retain their advisory authority unless an explicit engineering justification changes it. |
| Group membership, roles and qualitative `near` relationships | Preserve membership and relationships in search/reporting. Any hard quantitative criterion needs a predeclared source or engineering rationale. Qualitative `near` is not permission to invent a universal distance or claim a passing hard check. |
| Foreign schema/min-reader identifiers | Identify the supported adapter/version explicitly; do not pretend to implement the entire upstream reader. Unknown versions refuse. |
| Context, provenance, prior dispositions and unmeasured physical conditions | Retain them as source evidence with explicit assessment status. Metadata must not be mistaken for executed checks; required unresolved obligations still block their dependent claim. |

The table is an implementation obligation, not a claim that all mappings already exist or are equivalent. If inspection finds a mapping needs a capability outside 479, the chief records that concrete prerequisite and adjusts the affected dependency. It remains a blocker to M1 rather than an excuse to drop the field. AGENTEE-515 must verify the final mapping and all original operative declarations.

## Runtime boundary

AGENTEE-519 owns runtime/input access and the harness. It should use the existing public `place run` cancellation boundary to capture an initial checkpoint before search, recording cancellation honestly, rather than create a second input-validation implementation. Its recorded raw esp_prog preflight may become an explicit unsupported-input refusal after 520; that is the recovered truthful baseline, not a runtime regression. Successful M1 placement remains required after the compatibility work.

No implementation or criterion here authorizes telemetry-pod work, new manufacturing limits, relaxed native rules, or deleting the retained originals.
