# AGENTEE-479 implementation review — 2026-10-25

Chief agent-564 reviewed the delivered `2c31eb1c` adapter/metric slice and
selected paths in `a1b43632`, which adds capture and Rust refinement. Neither
is accepted as the completed 479 deliverable. The accepted
[grouping contract](AGENTEE-479-group-intent-contract.md) remains authoritative.
Agent-566 independently reviewed the fixed first slice's metric/report code in
Cairn `835c3555-5ffd-4ff0-971c-7904aaf7bdc9@1`.

## Demonstrated gaps and correction scope

| Boundary | Evidence | Required correction |
| --- | --- | --- |
| Original-dialect ingress | The chief's isolated probe classifies negative/text/boolean proximity distances, an empty subject, an unknown pad-selector key and boolean schema at `2c31eb1c`. A malformed disposition map raises `AttributeError`. At `a1b43632`, the negative-distance mutation is classified runnable with the exact production supported-field set. No native candidate was run by this probe. | One shape/value validator shared with the existing native form; exact dialect types and path-specific failures. Preserve advisory limits and raw captures. |
| Declared-near rail-only interface | The contract requires an any-pad fallback. The Python report instead emits no gap and its objective stays zero at both 5 mm and 50 mm separation. The chief reproduced agent-566's counterexample. The inspected Rust compiler also omits the term when no eligible signal pair exists. | Measured, identity-bound fallback and a nonzero distance response; Python and solver semantics must agree under movement. |
| Undeclared subject pads | At `89941feb`, adaptation permits partner-only selectors, but capture still indexes selectors for both ends. Rust always emits per-subject legs with net narrowing and carries no declaration-mode flag. | Propagate declared versus undeclared selection through capture, engine and report. Undeclared subject means one minimum over all pairs without net narrowing. Preserve missing-declared-pad refusal. An empty selector object must not invent a declaration. |
| Rail evidence and final output | `a1b43632` reads rails from a groups field that the input validators reject; the Python report and engine use differing rail keys. Final checks call metrics without the rail/proximity context. Final annotated group views and some report wiring are explicitly undelivered. | Use one canonical evidence-bound sidecar and carry it through capture, solver, final native metrics and views. Preserve unknown multi-group-net visibility. |

Agent-565 delivered ingress correction `89941feb`. The chief reran the original
probe against its exact module: the retained original classifies and all seven
malformed cases now raise path-specific `ValueError`; original input hashes
remain unchanged. This verifies that narrow correction only. It does not verify
native capture, solver parity, final reporting or the whole implementation.

## Selected completion contract

Cairn request `66f210dc-b1a7-40c3-bbeb-4ab961b033ec@1` assigns agent-565 the
coherent remaining delivery, with a 60-minute wall-clock budget and 25-minute
checkpoint. Follow-up `e1f3563b-4d2c-4779-b0bd-c9bf6ba8657f@1` identifies the
undeclared-pad propagation gap. The existing managed branch remains its owner.

Rail classification for the two representatives is already selected in the
accepted contract; it does not need another owner ruling. Add an optional,
hash-pinned `inputs.group_rails` sidecar through the existing capture pattern.
Bind source board/groups identities and the specified net/part/group evidence;
reject stale or mismatched applicability. Preserve original intent/groups bytes.
Do not turn the source-specific net names into a universal classifier.

Complete the constrained group/refinement behavior, final board-hash-bound
metrics and annotated views. Required checks include the existing native
invariants, signed metric and declaration-mode parity, the rail-only tether
counterexample, strict supported Python tests with no skipped native guards,
and bounded retained esp_prog/acquisition capture. Full representative campaigns
and independent judgments remain later acceptance work. The worker's 109 Rust
tests and Python run with a skipped guard are testimony, not chief acceptance.

## Evidence and doctrine limits

Chief evidence is retained at
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/grouping-479-review/`:
fixed source extracts, original and corrected probes/results, near-interface
counterexample, and the review decision. No original design was changed.

The bounded ingress review used Pincite packet `pkt-62025941c652909f`, content
SHA-256 `62025941c652909f1fb0ebcbb47f6209e40abd0b9c046301d9c5d408531cc137`,
corpus `corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`,
and retriever `retriever-ec995ecdd083b2c8`. The release retrieval gate passed.
The selected concepts place validation at ingress, preserve accepted contracts
and require evidence before intervention. A schema-validated decision receipt,
explicit nonmaterial omission rationales and citation classification are retained
in the private evidence directory. Their scope is the ingress finding, not full
479 acceptance. Source/reproduction evidence, rather than reviewer agreement,
is the basis for the correction.


## Candidate-set and applicability follow-up — 2026-10-25 15:35 PDT

Independent review `7e9c18a6@1` found a Python/Rust disagreement at `87bbbf05`.
The chief reproduced it with the exact fixed Python modules: unknown net X
spanning three groups remained visible in the unknown list but also supplied
R1/R2 signal candidates. The inspected Rust compiler excludes it. The chief
also reproduced acceptance of nonexistent shared-rail group/part annotations
and `near_pairs: {}`. These are separate candidate-selection and provenance
validation problems; neither justifies removing evidence binding.

Selected corrections preserve unknown-net visibility while excluding those nets
from signal candidates, retain the declared-near any-pad fallback, and validate
metadata against actual board net membership. Group/part annotations need not
be exhaustive. Sidecar collection shapes and named scoped groups must be valid.
No universal net-name classifier or additional geometric release threshold is
authorized. Counterexamples and fixed module hashes are retained under the
chief evidence root's `87bbbf05/` directory.

Delivery `1ddfd639` claims these corrections, same-pose Python/Rust candidate
count/argmin/objective parity, and the earlier declaration-mode repair. Its 111
Rust tests and host Python run remain worker evidence. The prior supported
native run had retained-input skips and predates this closure. New bounded
verification `69f6570c@1` copies hash-checked representative files into the owned
worktree's ignored build directory, uses their `/opt/agent-ee/…` mounted paths
and the image's `/opt/kicad-route`, and requires strict relevant native checks.
No global runner change is needed to make those retained inputs available.
Acquisition compatibility and the representative workflow remain unaccepted.

Agent-568 separately owns the annotated-view helper under `d7a7f206@1`. Its
selected interface is `group_views.produce(board_path, groups_document, out,
*, input_board_path=None) -> manifest`. It reuses native layer plotting and
binds final-board identity, component UUID/pose/side, declared groups, output
hashes and view transforms. Bottom geometry and annotations share the same
mirror transform; text remains readable. It emits evidence, not an engineering
acceptance. Final-check wiring follows a fixed, reviewed helper delivery.

The sidecar membership validation also exposed the contract's incorrect ground
membership description. The chief independently verified and documented the
[correction](AGENTEE-479-group-intent-contract.md#correction-to-ground-membership--2026-10-25).
Historical measurements and original inputs are preserved.
