# Independent review dispositions

Argon agent-414 returned Cairn result `7386dc4f-cccf-480d-a142-bc80422e6077`
version 1. The coordinator checked each claim against current source and tests;
reviewer severity labels are not treated as established findings.

| Claim | Disposition and evidence |
| --- | --- |
| 1. SPICE prerequisite evidence rejected | Accepted and fixed. Sealing now permits evidence already sealed by an earlier check. The version-2 native RC control executed ERC, DRC, netlist, geometry and SPICE successfully. |
| 2A. Raw-only citations rejected at acceptance | The rejection is intentional under the strict contract. Reviewer workspace instructions already name the required check citation; the adjudicator prompt now repeats that requirement explicitly. Scripted integration exercises accepted check defects through the real adjudication gate. |
| 2B. Unwitnessed reviewer defects invalidate the review | Intentional fail-closed contract, not proof of a defect. Reviewers must label unreproduced claims as concerns. Automatic silent downgrading would conceal a reviewer contract violation. Coverage of useful claims from a malformed response remains a usability limitation. |
| 3. Implementation sandbox lacks candidate; top-level files fail capture | Not supported by current source. `implement` copies `previous/inputs` into its runner before the call. Evidence mounts are conditional on existence. `selected_files` explicitly handles files; `at='.'` is refused by the original scope contract. The campaign integration test completes proposal capture and fresh review. The live subscription campaign later reached adjudication but stopped on an invalid model citation before implementation. Actual subscription implementation execution remains unestablished; the complete coordinator path is covered with scripted agent replies. |
| 4. `bench_review` passes an unsupported `previous` argument | Refuted: `run_review` has that keyword in both the original and current implementation. The bench integration test executes ingestion, fresh checks, fresh reviews and adjudication successfully. |
| 5. Execution failures labeled as design trade-offs | Accepted and fixed. The convergence reducer returns `incomplete` for execution failure; it does not produce a physical trade-off diagnostic. |
| 6. Multi-metric progress ignored | Accepted in part and fixed. Previously passing metric bounds are protected. A still-failing check may advance if at least one negative margin improves and none worsen, with a hard iteration bound. Lower but still-compliant positive margins are not requirement violations. |
| 7. Receipt internals not rechecked by `verify` | Strengthened: `verify` now reconstructs the complete seal from retained checks and artifacts and compares it, in addition to the artifact inventory hashes. |
| 8. Tests require `PYTHONPATH=src` | Not a supported-command defect: the README's documented invocation explicitly sets it. No package-discovery workaround added. |

The report contains several signatures and snippets that do not match inspected
source. Reproduction and integration evidence take precedence over those claims.
Fable agent-413 returned Cairn result `178c8a4d-8a2f-43a1-a08e-d924e861494e`
version 1, with executable reproductions in `/tmp/agent-ee-review-413`.

| Fable claim | Disposition |
| --- | --- |
| F1. A failed check launders unrelated reviewer prose | Accepted and fixed. In baseline-bound runs only coordinator check findings may be accepted. Reviewer explanations cannot become separate established defects; implementation receives accepted check findings only. A regression test attempts to attach RC evidence to an invented thermal defect and is refused. |
| F2. Implementation changes libraries or plants history | Accepted and fixed. Campaigns admit only explicit `mutable_inputs` (default schematic and board). All other bytes and the exact captured file inventory must remain unchanged. Model/library edits and planted files have regression tests. |
| F3. Nested run locks bypass campaign lease | Accepted and fixed for concurrent writes. Campaign root markers cause child mutations to acquire the ancestor lease; the owning thread can re-enter it. A separate-process test exercises refusal. `verify-campaign` now verifies historical result hashes and recomputes convergence, including at driver exit. |
| F4. Malformed witness voids a review | Kept fail-closed, as for Argon 2B. Execution failure is now classified separately from physical trade-offs. |
| F5. Inner tool provenance and domains self-declared | Accepted and repaired. The coordinator now executes a pinned declared tool separately, records execution and output hashes, and mounts those outputs read-only for the parser. Domain comes from baseline configuration. Tool-specific parser/model qualification remains an operator obligation. |
| F6. Concern witness shape unvalidated | Accepted and repaired: witness shapes are validated for concerns as well as defects. |
| F7. Missing files and lock contention produce traceback | Requires reproduction through the supported CLI, which catches OSError and ValueError. No integrity bypass demonstrated. |

Fable disclosed authorship of earlier native checks and fixtures and scoped its
independent review to the new coordinator code. Fable's final read-only review (Cairn result
`f1cae877-23ad-4a70-a10a-4498d563220c` version 1) verified the earlier F1/F2/F3/F5
repairs, nested leases, reconstructed seals, margin protection and empirical
transition gates. It reported three further issues:

| Claim | Repair and regression coverage |
| --- | --- |
| M1. Parser can rewrite the retained coordinator request | Both producer and parser now receive read-only request files; the coordinator also compares its request hash after parsing. The regression attempts request writes from each sandbox. |
| L1. Mutable-file authority in campaign state is not re-derived | Campaign verification compares `state.mutable_inputs` against the captured first scope before accepting history or resuming implementation. A test broadens the stored allowlist and requires refusal. |
| L2. A legacy measurement check without a tool raises TypeError on verify | Verification now explicitly refuses the missing pinned tool declaration with ValueError. A CLI test requires exit2 and a diagnostic without traceback. |

Tool-specific parser/model qualification and authenticity of an operator-supplied
bench sample remain declared trust boundaries. No successful live repair claim
is made: the actual subscription campaign stopped on a malformed adjudicator
citation, as recorded in the closed-loop verification report.
