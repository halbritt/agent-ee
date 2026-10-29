# Requirements-to-board execution record — 2026-10-28

Coordinator/integrator: **agent-688**. Independent gate verifier: **agent-699, Argon High**. Baseline: `9e4f241a0850e9d1e116e60727ea785609b10fc8` (includes previous fleet landing `09bd4e74`). This record activates the [campaign plan](../../research/2026-10-28-zeroasic-campaign-plan.md), preserving its engineering gates.

## Current scope and pause — 2026-10-29

Direct owner instruction: **"remove the ESP-Prog requirements"**. The
[amended campaign plan](../../research/2026-10-28-zeroasic-campaign-plan.md#owner-scope-amendment--2026-10-29)
excludes ESP-Prog from campaign deliverables, dispatches and acceptance gates.
Only acquisition-applicable qualification, negative/resume evidence and shared
regressions remain dependencies; broader AGENTEE-515/M1 closure is not required.
Existing issue parentage does not import those broader acceptance obligations.

Execution remains **paused**. G4 remains **0/4**, with no measured case started.
This scope amendment authorizes plan/tracker maintenance, not a board run.
All earlier ESP-Prog dispatches, admissions and campaign goal wording below
are superseded. Historical outcomes, failed attempts and artifacts remain
retained without a passing qualification or release claim. The four-case
acquisition acceptance series and its engineering gates remain required.

The following dispatch and progress sections are historical snapshots; they
do not authorize resumed execution or restore the removed scope.

## Current progress — 2026-10-29 12:41 UTC

**G4 remains 0/4; the measured series has not started.** The owner goal remains
active. G2 development R2 is closed incomplete, with no package attempt. Its
six public stages completed on `5f7919c6` in 335.168 seconds, producing board
`74a883fd`. The separate checks-only review executed all 39 checks successfully
(38 pass and one informational result), including zero native errors, opens and
parity findings. Independent review of the 14 authored judgments nevertheless
returned 12 dissents and two concurrences. Checks alone did not establish
engineering acceptance. All 7,732 files and the producer's `latest` symlink
are inventoried without modification in the external R2 closure manifest
`bf3579d7`; disposition `a736b117` forbids resuming R2 after source/input changes.

The dissent exposed incorrect capacitor-corner prose, incomplete source
provenance and unsupported application of ADC data-sheet conditions. Seven
corrected drafts are mechanically admissible but await new witnesses; five
retain unresolved findings. Newly captured exact Murata documentation supports
the existing C4/C5 maximum dimensions, independently reviewed as `cb28812e`.
Exact Samsung bias and ESR curves are now captured, but remain typical data.
The unchanged 128-SPS ADC clause requires an honest error evaluation; it does
not state a guaranteed total-accuracy ceiling. A bounded independent engineering
review is checking the proposed sensitivity corners and claim limits before
fresh producer inputs and a new run. No requirement, source condition or
manufacturer guarantee is silently extended. Supplier and physical obligations
remain separate and unperformed.

The custom-SMD via-clearance repair is integrated through `6577f4ef` after
independent source/native review `e5bf871b`. The integrated source, tests and
documentation match the tested worker byte-for-byte. Its 13 native tests and
actual 565-vertex pad controls passed. The original routed ESP board still has
12 real clearance defects across 1,848 assessed pairs under the unchanged
0.1 mm engineering policy; unsupported geometry is now empty. This is a
qualified check capability, not a repaired or accepted board.

For original ESP, both prior limited reroutes remain failed and retained. One
new native preparation of its actual selected, unrouted public materialization
completed in 4.536 seconds. Independent audit `13396fc0` confirms only the
authorized metadata and 20 text-size changes, unchanged electrical partitions,
and exact schematic/project custody. Prepared PCB `3f64e6dd` has no copper,
46 expected opens and 242 warnings. A single fresh public placement/routing
operation is admitted: fixed original connector and mark poses, 13 movable
functional components, two seeds, shortlist one, 1,200-second job budget and
zero retries. Final full schematic parity, all-site DFM, grouping and readable
artwork must apply to its actual resulting board. No M1 acceptance is claimed.

Evidence is under `g2-acquisition-fresh-r2/` and `esp-original-native-r1/` in
the host campaign root. The full strict gate, actual full engineering review,
remaining M1 operations, private G4 evaluation, reproducible release and trunk
landing/cleanup remain open. This snapshot supersedes the progress statements
below while preserving their failed-attempt history.

## Earlier progress — 2026-10-29 11:02 UTC

The goal remains active. **G4 is 0/4 and no measured case has started.** This
snapshot supersedes the earlier progress summaries without replacing failures.

The automatic carrier selector, public preparation wiring and selected CPL
closure are integrated as `739b5e73`, `6ef7d9f9` and `d6bbf04a`. One real
supplier-footprint GET captured C468683 / ADS1115IDGST, including the exact raw
HTTP result span and provenance. The existing preparer now derives the selected
supplier list and operative assembly allocation roster before sealing. Independent
review `696f9d02` approves this source closure; it does not qualify native CPL
orientation. The integrated focused source run passed 36 tests without skips.
Two earlier root test invocations had missing or mismatched fixture environment
paths; their results and the unintended disposable output are retained.

The first fresh preparation invocation failed before publication because root
omitted the required existing parent directory. An explicit one-invocation
correction produced preparation identity `8ed6df1d` in 1.963 seconds. The ensuing
public native build exited 1 in 5.845 seconds: requirements and parts completed,
then scaffold creation refused because the parts declaration source and its
mapped membership requirement disagree. This is a producer binding defect.
Agent-692 owns its bounded correction; the ordinary `parts_subset` guard remains
authoritative. No routing, package attempt or reviewer call occurred. The failed
attempt is retained under `g2-acquisition-fresh-r1`, including a separately
verified copy of all 1,812 preparation/run files (297,973,090 bytes). A new source
revision requires fresh preparation and an explicit native grant.

The actual esp_prog size operation completed through the retained stage executor.
All 20 undersized texts became exactly 0.8 mm; independent replay and complete
raw-token comparison establish that every other board token is unchanged.
Operation-custody review `d08d8df4` approves output `fd99c17f`, not artwork
acceptance. Full native parity and opens remain zero. Twenty text-height findings
disappeared, but six silk overlaps and one silk-over-copper finding were added;
242 warnings remain. All original inputs and both warning sets are retained.

Root selected the existing 0.1 mm full-via-disk/SMD-copper clearance policy as
delegated engineering judgment, explicitly not a supplier rule or an owner-sourced
constraint. Agent-695 is preparing a supported fresh routing invocation with that
captured project setting while preserving original placement and connectivity.
The existing DFM check's custom-pad limitation must also be resolved for U2.2;
unassessed geometry cannot discharge M1. No new routing run has been admitted.

Independent case review found that C1 and C3 cannot count the same observed ADC
stockout twice. Before any new catalog capture or measured-series freeze, the
case rule is: if C37593 becomes eligible, retain it for the control and establish
a separately bound unavailable-choice contrast; otherwise, a distinct qualified
role/alternative is needed for C3. Raw stock and freshness remain unchanged.
An imposed constraint must be labelled as such and still satisfy the exact
campaign contract. The next capture is not yet authorized. Case feasibility and
implementation must be settled before freezing a measured series; a resulting
capability change cannot be slipped into an already counted series.

M1, all 14 candidate judgments and independent witnesses, full subscription
review, full strict qualification, the private evaluation, reproducible release,
trunk integration and cleanup remain open. Plane comments on AGENTEE-559 and
AGENTEE-523 record these results without marking either accepted.

## Earlier progress — 2026-10-29 10:36 UTC

The owner's goal remains active through actual G4 acceptance. **Acceptance is
0/4; the measured series has not started.** The following supersedes older
snapshots below without replacing their retained failures.

The integration branch contains the approved source body/access planner and
strict mechanics package replay. Review found and corrected a rejection of
declared-fixed but unlocked components. The corrected package bridge is
integrated as `1c95e254` and `4c9bdc26`. The first new runtime build failed
before native tests because a Rust test's included geometry fixture was absent
from the container build context. Correction `9f763aea` copies and hashes that
fixture in the build receipt. The next build passed all 119 Rust tests; 127
selected Python checks, including native placement, routing, cancellation and
same-identity resume, passed. The complete invocation exited 0 in 304.864 seconds.
This is source/runtime qualification, not an actual target or full strict pass.

An actual local-return producer control on a retained pre-route acquisition
board exposed a native UTF8-wrapper/string mismatch hidden by its test double.
Independently approved correction `ebbaef83` converts that native value to
text. The next control exited 0 in 3.278 seconds: native DRC rejected two
shorting/mask-bridge proposals, then accepted a 3.825 mm seed with unchanged
library warnings. Native preservation passed for that seed and refused the
unseeded original. All rejected boards and reports remain. The selected board
still has 36 unconnected items because this control performed no routing;
it is not a target improvement or accepted package.

The automatic ADC carrier selector and existing public preparer wiring are
implemented but remain blocked from integration. Earlier private-fixture skips
and permanent test guards were corrected. Review of `d2acebac` then found the
selected C468683 lacks its own supplier footprint in the CPL declaration and
the operative assembly roster still names the previous part/catalog. Agent-692
owns that precise input correction and one bounded source capture through the
existing supplier producer. No stock freshness, orientation or assembly
predicate is waived. A fresh G2 run will stop at artwork for all 14 candidate
judgments and independent witnesses before its first package attempt.

For original esp_prog, the attributable legacy schematic was found and
converted through installed eeschema's GUI. Independent review confirmed all
16 functional identities and 24 electrical net partitions. The first usable
native parity check exposed 35 metadata findings. A separately authorized
derivative corrected the CON2 footprint assignment, synchronized captured
metadata and automatic net names, and marked the five known noncomponent
footprints board-only. Its native parity reports zero findings and zero opens;
255 physical/artwork/library warnings remain. That derivative is undergoing
independent custody review; original sources, project and selected board are
unchanged. This is not original M1 acceptance.

The bounded text-size producer and receipt replay are independently approved
and integrated as `2c245951`. All 30 selected checks passed in the supported
native environment, including the previously blocked native import test.
Actual esp_prog text-size repair, final artwork, selected process-policy site
assessment and the remaining M1 operational checks are still open. Existing
DFM and router clearance capabilities can support the process-policy work;
no captured filled/capped supplier entitlement has been established.

Current evidence is retained beneath the host campaign root in
`mechanics-runtime-native-r1`, `mechanics-runtime-native-r2`,
`local-return-native-r1`, `local-return-native-r2`,
`acquisition-alternative-producer-agent692/`, and `esp-original-native-r1/`.
Full strict qualification, the private four-case evaluator, release evidence,
trunk integration and owned-worktree cleanup remain required before completion.

## Direct authority

Owner, directly to agent-688, 2026-10-28:

> load this: docs/research/2026-10-28-zeroasic-campaign-plan.md and go to work.  put the work items in plan, validate the plan, and proceed.  Spawn a dozen or so agents in herdr, sol 6.1 across the 3 codex subscriptions, opus 5.5 from the claude subscription, and argon from the gemini ultra subscription.
>
> Don't wait for me for questions, I delegate decision authority to you

“plan” is implemented as Plane, the repository's tracker, with the committed local graph as its dependency record. Routine choices and launch/integration are delegated. The campaign's explicit telemetry-pod hold, file/review/supplier/physical distinctions, no orders/uploads, and engineering acceptance boundaries remain. No new cloud, purchases or supplier actions are needed for this campaign.

## Validated plan and live reconciliation

An independent source review found the ordering sound for G0 and bounded preparation. It confirmed remaining normal final group gating (`placement_job.py` final eligibility), checks-only package review (`retained_run.py`), absent brief-only preparation, and three benchmark gaps: live inference isolation, independent refusal grading, and enclosing budgets. Detailed current tracker reconciliation is in [reconciliation.md](reconciliation.md).

Historical drafting statements that Plane was unavailable are superseded: the CLI/API works. Project native epic types remain disabled. Modules were enabled to group outcomes while preserving existing parentage. Native prerequisite relation operations are unavailable through the inspected CLI/tools; **native edges are pending, not created or verified**. The [local dependency graph](dependencies.json) governs execution and supplements retained recovery dependencies, including ZA-05's existing 520/521 contracts. Four existing items are reused; eleven new distinct items include the three separately verifiable ZA-11 obligations. Done machinery items stay Done; no unsupported reopening or completion occurred.

| Local key | Plane | Disposition |
| --- | --- | --- |
| ZA-02 | AGENTEE-525 | Reused; existing parent preserved |
| ZA-05 | AGENTEE-479 | Reused; existing parent preserved |
| ZA-06 | AGENTEE-489 | Reused; existing parent preserved |
| ZA-07 | AGENTEE-516 | Reused; existing parent preserved |
| ZA-01 | AGENTEE-559 | Distinct remaining deliverable |
| ZA-03a | AGENTEE-560 | Distinct remaining deliverable |
| ZA-03b | AGENTEE-561 | Distinct remaining deliverable |
| ZA-04 | AGENTEE-562 | Distinct remaining deliverable |
| ZA-08 | AGENTEE-563 | Distinct remaining deliverable |
| ZA-09 | AGENTEE-564 | Distinct remaining deliverable |
| ZA-10 | AGENTEE-565 | Distinct remaining deliverable |
| ZA-11 | AGENTEE-566 | Distinct remaining deliverable |
| ZA-11a | AGENTEE-567 | Distinct remaining deliverable |
| ZA-11b | AGENTEE-568 | Distinct remaining deliverable |
| ZA-11c | AGENTEE-569 | Distinct remaining deliverable |

ZA-01 belongs to 515; 03a/03b/04 to 525; 08 to 512; 09 to 517; 10 to 83; 11 to 69, and 11a/b/c to 11. ZA-11 does not inherit the broad extension dependencies of 517. Creation initially ignored the raw `parent` field. The explicit `--parent`/`--state` flags corrected all eleven new items, followed by readback against the intended values. See [mapping.json](mapping.json) for UUIDs and readback states. Five modules represent dependable reference/geometry, first accepted brief, adaptation, physical/electrical feedback, and optional routing search.

## Fleet and exclusive writers

Twelve independent Herdr sessions were launched, each labeled with its own supplied Cairn identity. They initially have read-only assignments with retained evidence outside Git. Existing unrelated sessions and the old `ee479-views` worktree are untouched.

| Route | Herdr lanes | Initial outcome |
| --- | --- | --- |
| Sol 6.1 high, `.codex` | `za688-reference`, `za688-brief`, `za688-budgets` | Public reference, brief interface, enclosing budget |
| Sol 6.1 high, `.codex-harm` | `za688-groups`, `za688-composition`, `za688-runtime` | Final group contract, composition/catalog, runtime admission |
| Sol 6.1 high, `.codex-3` | `za688-blocks`, `za688-m1`, `za688-extensions` | Qualified roles, original esp_prog/M1, queued extensions |
| Opus 5.5 high, Claude Max | `za688-review-bind`, `za688-evaluator` | Review binding and independent evaluator protocol |
| Argon high, Gemini subscription | `za688-verifier` | Independent plan/gate review and later integrated verification |

All three Codex profiles report ChatGPT authentication and have distinct account-ID fingerprints; Claude reports first-party subscription authentication. Agy advertises `gemini-4-argon-high`; live selected model is checked separately from mere route availability. Subscription failures stop the affected operation without model/provider fallback. Two Codex starts exited before a task because the explicit bypass flag duplicated the existing shell alias; the same empty panes were launched with the corrected command. No task delivery was replayed. Claude's installed Cairn development-channel startup confirmation was accepted within the authorized session launch.

At most two implementation builders may operate concurrently. Initial proposed slots are normal-path reference/group protection and qualified block entries, after their preflight/contracts are admitted. Other lanes are investigation, independent review or evaluator preparation; launching twelve sessions does not authorize twelve writers. Shared `build_workflow`/package/qualification interfaces have one owner at a time. Worktrees use `git-wt` under the cache; coordinator serially integrates reviewed commits, runs appropriate checks, pushes main, then removes owned branches/worktrees. No worker independently lands or declares release.

## G0 status and bounded dispatch

**G0 is in progress, not accepted.** Source and tracker reconciliation, role separation, subscription login checks, captured-input verification and one actual runtime admission are complete. The admitted build failed during schematic annotation; runtime compatibility through placement, routing, artwork and package remains unverified. Dependent G1 native work is held. Worker reports are proposals until independently verified. The historical source full sweep plus correction rerun is not a fresh clean full invocation.

Initial read-only dispatch cap per lane: 30 minutes, at most 60 model/tool rounds, zero nested model calls and zero native jobs. Token usage unavailable from an interface remains unknown, never zero. No provider cap is claimed to be mechanically enforced by a prose assignment. Before native dispatch, record an enforcing command, one host reservation, maximum four module workers, an enclosing wall/work budget and reserved final-grading time. Calibrate once through ZA-01 and record actual values; do not invent a calibrated budget from source-only inspection.

G1 consumes current reference + final saved-coordinate mutation + three qualified reference roles. G2 joins brief-only preparation and actual independent review/repair. G3 validates composition and catalog change. Under the 2026-10-29 scope amendment, G4 requires current acquisition qualification, all required judgments and full review, three supported successes and one independently correct refusal, at most two complete generation attempts per case, full accounting, clean release gate and fresh independent reproduction. Broader M1 closure is not required. G5/G6 do not block G4 and have no implementation/native authorization in the initial dispatch.

The evaluator author holds exact unseen case inputs/bounds outside the writer's mutable store. Public protocol discussions must not include holdout answers. The final reproducer must have authored neither generator nor evaluator. No measured series starts until G0 admission and the case/runtime/catalog/manufacturing/model/tool/repair pins are frozen. Every attempt after admission remains in the denominator.

## Verified observations at 2026-10-28 10:55 PDT

The calibration used source `0c405eec9fe0a0d5efa50a7cfccdce84ea22a591`, with all 204 original manifest inputs matching before and after execution. Actual admission on proximal granted one unit, 3 CPUs and 16384 PIDs. The container used network mode `none`, cached image `sha256:63cb53f57f8ca334f7bb202564247c20fa9ff3799aef052840e109733ebbe4bb`, build-input key `7a405961fba05ae10fcb46de2e9bd1b0ebdef14da7d3df2bfe980e6cbca9e534`, and router `c4af095ed07593b63d7886c139fcb7e5c2e774f4`. These are observed pins for the failed calibration, not qualification of a future source tree.

The one legacy acquisition build exited 1 after 9.509 seconds. Requirements, parts and scaffold completed; schematic annotation's inner Bubblewrap process refused namespace creation before netlist export. Placement, routing, artwork and package were not reached. This legacy calibration is not typed engineering acceptance. Original esp_prog was not run. Argon independently verified the retained inventory and cleanup; exact grading is Cairn `c7c6c981-1b1f-4b8e-90c8-fbc42e6a2a29@1` and `verifier/g0-calibration-grading.md` under the host evidence root.

The failure location is established; its underlying host/namespace cause is not. A retained historical success used the same inner command and sandbox arguments, with matching Bubblewrap, Python and KiCad executable hashes. The old container's effective policy is unknown. The subsequent bounded diagnostic admitted one job but ran neither of its two intended test arms: its recorder omitted the parent directory for the policy marker. That setup failure, cleanup and absent arm results are retained separately in `nested-sandbox-probe/agent697/REPORT.md` and Cairn `09ac4afd-7066-4c4d-a944-f36ba8f3bd0d@1`. No policy relaxation or repeated full build was authorized.

The independent calibration review also found two driver defects to correct before another run: the recorder exited 0 after the productive build failed, and reserve exhaustion during bulk hashing could skip worktree cleanup. Actual cleanup completed in this attempt; the defective timeout path was not exercised. The admitted interval through cleanup was 13.749 seconds; 131.589 seconds includes later report/checksum writing. Neither timing proves recovery under forced exhaustion. Original failed scripts and receipts remain unchanged; fresh revisions must propagate productive failure, preserve essential evidence and attempt owned-resource cleanup under the enclosing deadline before optional bulk hashing.

Source work proceeds within the two-writer cap:

| Slice | Candidate and evidence | State at this snapshot |
| --- | --- | --- |
| ZA-05 strict placement contract | `2348b358929bd9ae8aba5dad1b3804e50b1c50dd`; `implementation-groups/REPORT.md` | Opus requested revisions; agent-691 owns slot A. Early package refusal, accurate generated-board binding limits and parent-side CPU coverage are required. Native final-coordinate and package evidence remain open. |
| ZA-03a explicit no-connect preservation | `56642f9dbcc624f22e8f7124ec498749081eac2d`; `implementation-blocks/REPORT.md` | Thirty focused CPU tests passed; independent Opus source review is pending. Agent-692's writing is suspended while the clean candidate waits. All three private role captures remain unverified. |
| Runtime orchestration repair | `orchestration-v2/dispatch.txt` | Agent-697 holds slot B for fresh external driver preparation and mocked failure-path checks. This grant authorizes no real native run or repository source change. |

Both source candidates await coordinator integration; neither is a completed campaign deliverable. CPU tests do not establish native connectivity, final geometry, electrical applicability or release acceptance. Private source captures stay outside the public repository. Acquisition judgment accounting remains three records with existing concurrence, four authored awaiting witnesses and seven absent; see [reconciliation.md](reconciliation.md). Nonzero J2 load authority remains unresolved. The capacitance-only development case does not resolve that authority or count as an unseen benchmark case.

## Evidence and next actions

Host evidence root: `/home/halbritt/.local/share/agent-ee/zeroasic-20261028-agent688/`. It retains launch commands/results, dispatch text, session routes, tracker snapshots, module membership readback, lane reports, failed-run artifacts and native delivery acknowledgements. These are operational artifacts, not a release package. Exact task input access is supplied by the captured-input manifest and retained runtime receipts; no private inputs are copied into public documentation. Later dispatches and outcomes are recorded in Plane and this evidence root; the dated snapshot above does not authorize another native attempt.

Next: review the corrected drivers, finish independent source review and corrections, and select a bounded namespace discriminator under a fresh grant. Any resulting runtime repair must preserve the outer sandbox and receive appropriate verification before another calibration. The first full calibration, final saved-coordinate controls, role qualification and G2-G4 acceptance remain outstanding. Keep incomplete gates and provider failures explicit. No generated-board or campaign completion is claimed by this execution record.

## Product progress at 2026-10-28 19:25 PDT

The owner's direct correction was: `"validation infrastructure" is what concerns me.`
The coordinator froze additional controller, runtime-profile and generic harness
development. Work now targets the public acquisition path and demonstrated
production failures. The earlier driver work above is historical, not the next
delivery task.

The brief preparation interface at `82f87fb4`, with documentation correction
`b13c0d2b`, produced captured 100 pF control and 150 pF development inputs.
Its 45 pure tests passed. The real control then completed requirements, parts,
scaffold, schematic, placement-routing and artwork in 335.7 seconds through the
existing public build command. The final board is
`0b55ccae56c9e907b5c1dc0728f7323840140816578ed6568f827a0d48d4fdbc`.
Retained final-artwork DRC reports zero errors, zero unconnected items and 17
library-footprint warnings. Artwork cleared 19 silkscreen warnings. Group and
electrical-locality evidence binds the pre-artwork board; final group acceptance
and the package stage remain open.

The control exited 1 after those stages because qualification treated two normal
retained router aliases as build inputs. The narrow fix was independently reviewed
and integrated as `020cc848`; 13 pure regression tests passed. Assessment with the
fix reached unaccepted, unassessed dimensions on the retained real run. A coordinator
preview unexpectedly rewrote root qualification; the original failure record was
restored byte-identical against its previously recorded SHA-256. The preview and
incident are retained separately. This does not replace the original failed run
or constitute another generation attempt.

The full strict run at `87b85d6f` finished with exit 1: 369 modules, 6514 tests,
48 problems and no missing-input tests. Both RC controls passed; the wrapper also
reported a separate failed cleanup observation and exit 74. Two concrete test
assumptions were repaired in `fc8c384a`, independently reviewed, and passed all
three focused native tests. A reproduced existing plane test retained the exact
stderr digest from the full run: the plane child cannot create a nested sandbox
namespace. Agent-697 owns a bounded repair to that stage execution path; host
policy is unchanged. No full-suite pass is claimed.

The existing full-review engine is now connected to `build review --profile`
in `07319088`, following independent source review and 13 focused pure tests.
Diagnostic review remains `accepted:false` and cannot grant file release.
Live validation is pending: the host has KiCad 10.0.7 and the requested subscription
harnesses, but the generated geometry check pins a different Python binary from
the container. That pin has not been changed. No new review bridge or provider
fallback is authorized by a failed compatibility check.

These changes are composed in the isolated campaign branch, not landed on main.
The four-case acceptance series has not started and remains **0/4 accepted**.
Full engineering review, candidate judgments, package qualification and both M1
targets remain open. Evidence is under the host root above in
`acquisition-control-build-r1`, `test-assumptions-native-r1`,
`add-planes-existing-test-r1`, `za10a-implementation-agent700` and
`host-review-doctor-r1`.

## Product progress at 2026-10-28 20:00 PDT

The real acquisition board completed its first full subscription review attempt
through `build review --profile`. One invocation of the existing CI image kept
the exact pinned Python and native tools, with read-only source, retained build
and subscription credentials. No scope pin or host policy changed. The attempt
ended with exit 1 after 968.1 seconds; its original build-report and qualification
hashes are unchanged and its owned container was removed.

The RF reviewer completed. The circuit reviewer was refused before model work:
the 1,665,642-character prompt exceeded Codex's 1,048,576-character limit. The
physical reviewer made 49 response updates and 248 tool updates, then exhausted
its 900-second allowance. No authentication or quota failure was observed.
Adjudication was not reached and engineering review remains unassessed.

The complete native review produced 36 passing checks, one informational netlist
check and two failed executions. Assembly correctly refused a 97.4-hour-old
catalog capture against the unchanged 24-hour freshness requirement. The pinned
geometry tool ran but could not find the declared local trace path from C3 pad 2
to U2 pad 3. Agent-693 is examining the actual topology and measurement contract,
and the existing path to obtain fresh catalog evidence. Neither requirement is
waived. Native fabrication, CPL and package checks passed within this diagnostic
review; a sealed public package and release acceptance remain outstanding.

Two demonstrated software failures now have independently reviewed repairs in
the campaign branch:

- The plane stage uses its existing outer sandbox for native children. DRC gets
  a disposable project whose exact preferences file may change, while the
  original inputs and copied board, project and rules remain checked. The first
  revision failed two of four native tests; the corrected revision passed both
  failed tests in 26.6 seconds. Commits `28a77b66` and `94576b5e` contain the repair.
- Large investigate-mode review prompts reference sections of the complete
  read-only packet, preserving its bytes, identity and citation inventory.
  Fixed-packet mode stays inline, and initial reviewer isolation is unchanged.
  Commit `d1c608ef` contains the repair; `75b3a3fe` clarifies that the 400,000-character
  packet target does not bound the whole prompt. Five focused pure tests passed.
  The two native tests excluded by the worker's pure guard both passed under the
  normal CI wrapper in 55.6 seconds. A live corrected-prompt review remains pending.

The next productive step is to resolve the actual assembly and geometry blockers
before another full review. Additional generic controller and validation-framework
development remains frozen. No full-suite pass, trunk landing, M1 acceptance or
campaign case acceptance is claimed: **0/4 cases remain accepted**. Evidence is
retained in `acquisition-full-review-r1`, `add-planes-native-r2` and
`prompt-transport-native-r1` beneath the host evidence root.

### Actual defect repair and stock update

Both existing public build/resume tests, including typed method binding and the
default checks-only build review, passed on `58e74a43`: exit 0, two tests, 83.4
seconds. The full strict suite has not been rerun.

The geometry investigation distinguishes a measurement limitation from an actual
design failure. Its track graph omits plane traversal and plated-pad transitions;
the net is connected. However, the U2 ground branch travels about 14.07 mm before
reaching the plane, already exceeding the complete 10 mm ADC bypass-loop budget
before adding the 5.43 mm supply leg. The same retained physical investigation
found C2 overlapping J1's access column by 0.055 mm². A coordinated small repair
must also preserve the minimum U1/C2 body gap.

The coordinator selected one documented defect/control pair under ZA-10's
two-repair-iteration limit. Agent-693 has at most two 600-second native repair
attempts on a disposable candidate, with the original board, source pins,
requirements and all design limits unchanged. Its grant and replayable actions
are retained under `acquisition-repair-r2`. This is diagnostic board work;
external board checks cannot replace seven-stage production receipts. Agent-700
is inspecting the smallest integration through the existing campaign and stage
receipt interfaces. The three-preparation development cap has not been reset,
and no fourth `acquisition_prepare` invocation was authorized.

A single fresh stock capture completed in 58.4 seconds at
`2026-10-29T03:10:37Z`. All eleven IDs were found, raw response hashes matched,
and manufacturer, MPN and package were unchanged. **C37593, ADS1115IDGSR, now has
zero JLCPCB stock**; the other ten selections have positive quantities. The new
catalog hash is `7764a1253de771ed8d94ef6d025b64e51c747f756c8c0850190c95925e7501bf`.
The old capture remains intact. Agent-692 is investigating qualified alternatives
under ZA-04, bounded to three discovery queries and three per-ID captures. No
substitution, order, project upload or assembly acceptance is claimed.

### Repair limit reached; package review connected

The two authorized saved-board repairs are complete and **the candidate is
rejected**. The second candidate meets the unchanged ADC-loop limit at 8.8681 mm
and clears the connector access obstruction. It still has three raw KiCad
clearance errors: 0.1750, 0.1754 and 0.1877 mm against 0.2 mm. The repository's
existing numeric-advisory classification produces eight focused passes and
exit 0; that does not meet this dispatch's explicit zero-error requirement.
Both attempts and the original inputs are retained. An independent audit
verified their hashes, changes and rejection. No third repair is authorized,
and no edited board was substituted for a production-stage receipt.

The stock investigation found C468683, ADS1115IDGST, with 3,552 units in the
JLC capture at `2026-10-29T03:17:16Z`. TI identifies R/T as the same component
on different reel sizes. Independent inspection verified the retained
manufacturer evidence, package, pinout, operating limits and stock response.
JLC assembly eligibility remains unresolved because the response also has
`assemblyComponentFlag:false`; its meaning is not established by stock count.
The original zero-stock C37593 selection remains unchanged. The follow-up
exhausted the original three-query allowance without finding a primary field
definition. The same flag tuple appears on all eleven fresh rows and the
historical original ADC, so it does not establish a candidate-specific ban.
Immediate order-specific assembly eligibility remains unknown; no further
search, substitution, procurement or project upload was selected.

Commit `ad962ca3` connects a plan-requested full review to the package stage.
The optional typed-plan `review_profile` is captured and pinned with ordinary
inputs. When requested, acceptance requires both file release and the package's
own completed engineering review under that profile. A failed or incomplete
review withholds acceptance while preserving separately valid file evidence.
The default checks-only path and judgment-freeze rules remain unchanged. The
supported sequence is through artwork, new bound judgments and witnesses, then
the first package attempt. A new repair API remains deferred.

Independent source review approved the change. Eighteen focused tests passed
under the existing CI/native wrapper in 20.4 seconds, including native build,
resume, custody and qualification regressions. Those native fixtures use
checks-only review. A subsequent eight-test native run on `8112bf52` passed in
14.1 seconds and exercises full-review capture, verification and qualification
with completed and failed scripted reviewers. The first run failed because the
synthetic fixture omitted its scaffold scope; that failure is retained, and the
four-line fixture correction was independently reviewed. The control's empty
package marker is explicitly invalid, and both cases assert the real missing
manufacturing-coverage blocker. This verifies review custody, not successful
package production: stage inventory and runtime fingerprints are synthetic,
and the package producer is not called. Positive combined file release remains
covered only by pure tests. No new validation framework is being built.

Evidence is in `acquisition-repair-r2/agent693-board-repair`,
`acquisition-repair-r2/alternative-part-agent692`,
`za10b-package-profile-agent700` and `package-review-native-r1` beneath the host
evidence root. Full strict qualification, trunk landing, engineering acceptance,
the sealed public package and M1 remain open. The measured four-case series has
not started: **0/4 accepted**.

### Existing failure diagnostic after the repairs

One run of the existing strict runner selected the 18 modules covering the 44
previously unresolved problems. On `44c5d067`, it completed 365 tests in 328.6
seconds with exit 1 and 24 counted problems; no required inputs were missing.
All 20 formerly failing plane cases have explicit passing results. This is a
selected diagnostic, not a full strict-suite pass. The fabrication module's
invalid result remains a failure even though its process header says exit 0.

The current fabrication netlist and geometry stderr hashes match the retained
Bubblewrap namespace denial. Agent-697 has submitted a bounded repair to run
those children inside the existing receipted stage confinement; independent
source review and native verification remain pending. Standalone sandboxing,
fabrication checks and host policy must stay unchanged.

A separate two-test diagnostic on `bd10ab31` retained the custom-pad CLI's
structured failure and used the existing rating-test retention hook. It failed
as expected in 2.1 seconds. Custom-pad loading also has a confirmed nested
namespace denial; agent-690 owns a bounded repair to that existing call path.
The rating evidence proves only failure of its `pdftotext` version guard. Legacy
forward annotation fails during planning before apply, but its lower child
diagnostics were not retained. Those two causes remain unresolved.

Records are in `existing-failures-diagnostic-r1`, `child-failure-diagnostics-r1`,
`fab-stage-execution-repair-agent697` and `custom-pad-execution-repair-agent690`
beneath the host evidence root. Original failures remain retained. Full strict
qualification and trunk landing remain open, and no board or campaign acceptance
follows from this test progress.

### Retained fabrication, annotation and citation failures

Independent source review approved agent-697's fabrication change, integrated
as `2776ba8a`. Follow-up `d81fe1de` maps the captured baseline argument and
updates two existing negative-control wrappers to use the private stage entry.
Twenty-three pure tests passed. An earlier invocation also named a nonexistent
test module and remains recorded as failed; its removal corrected the command.

The first existing native fabrication test on `d81fe1de` failed in 4.4 seconds.
A second diagnostic on `e7c9e4ad` retained the fixture before cleanup and ran the
existing schematic and rating cases as well. All three failed; the command
exited 1 in 10.1 seconds. No acceptance predicate was relaxed. The retained
fabrication evidence shows that netlist and geometry now execute successfully,
but the native stats call changes its private captured input tree. The mutation
guard correctly refuses the package. Its exact changed files require inspection
before any further repair; ignoring input mutations is not an acceptable fix.

The same diagnostic now proves nested Bubblewrap namespace denial in both
legacy forward-annotation planning and the citation extractor's version probe.
`e7c9e4ad` preserves the actual probe record and console in the existing binding
failure reason. The declared extractor version has not been changed, and this
failure is not evidence of a different installed version. No generic retention
or execution framework was added.

Evidence is in `fab-stage-native-r1` and `retained-child-failures-r2` beneath the
host evidence root. The latter retains 3,317 fixture files with hashes. Both
native invocations have exited and admission is free. Agent-690's custom-pad
repair remains separate. Full strict qualification and landing remain open;
the board repair cap remains exhausted and campaign acceptance remains **0/4**.

### Custom-pad native repair verified

Agent-690's `4361bb16` passed independent source review and was integrated as
`c8766548`. The existing footprint CLI now runs its native child within its
verified enclosing sandbox. Ordinary standalone use retains the previous
sandbox route; unknown mount layouts and changed captured inputs still refuse.
The native pad inspector and its identity/outline validators are unchanged.

The original failing custom-pad CLI case passed on `c8766548` in 1.0 seconds.
Retained outer and child records show actual KiCad 10.0.7 execution, exit 0,
assignment outcome `pass` and pad numbers `1` and `2`. Eleven files retain the
captured member, native output, execution records and assignment report. A
subsequent 77-test selection passed in 22.5 seconds with no skips, covering
custom-pad geometry and refusal cases, library checks, footprint regressions
and the new pure execution controls. This closes the observed custom-pad CLI
failure; the full strict suite has not been repeated or passed.

Evidence is in `custom-pad-execution-repair-agent690`, `custom-pad-native-r1`
and `custom-pad-regressions-r1` beneath the host evidence root. Agent-697 owns
the bounded fabrication follow-up for its disposable KiCad project copy.
Agent-696 now owns a bounded legacy annotation repair using the existing
schematic stage with distinct symbol and footprint capture roots. Both source
assignments reserve native verification and integration to the coordinator;
they add no board-generation attempts. Citation and visual execution failures,
full qualification, trunk landing, M1 and the accepted package remain open.

### Fabrication control passes; variant path remains open

Agent-697's disposable-project follow-up `d677db5a` passed independent source
review and was integrated as `d20528f7`. The original declared-fabrication
control passed under native KiCad in 9.0 seconds. Its retained receipt is
completed with exit 0; fabrication export, CPL and package checks pass, while
netlist and geometry remain informational. Four disposable export copies
contain KiCad preferences; the captured input tree has no added preferences.
The 488 retained fixture files include the native outputs and package evidence.

A three-module regression run then completed 56 tests in 107.3 seconds with
two failures and no missing required inputs. The existing D/F1/G1 fabrication
campaign test and all package-module tests pass. Both remaining failures are
the receipted BOM-variant cases. A single retained diagnostic of the FULL case
failed in 2.5 seconds and established the exact cause: schematic netlist export
exits 0 but adds `project/cpl.kicad_prl`. All thirteen captured files, including
the variant project file, are unchanged. The integrity guard refuses that
addition before BOM export is reached. No project-file mutation exception is
justified, and BOM export behavior is still unobserved in this path.

Evidence is in `fab-stage-native-r2`, `fab-regressions-r1` and
`fab-variant-diagnostic-r1` beneath the host evidence root. The next correction
must preserve captured inputs while handling the demonstrated schematic-export
preferences side effect. Agent-690 separately has a bounded citation-extractor
source repair, with native verification reserved to the coordinator. The full
gate, trunk landing and board acceptance remain open; acceptance stays **0/4**.


### Schematic, fabrication variants and legacy annotation pass selected native checks

The schematic-export correction `a8fcf4ae` extends the existing disposable
project copy to the exact netlist and BOM commands. It permits only the selected
design's KiCad preferences file to change in that copy; captured inputs and
project files remain strict. The FULL/LITE variant test now binds the recorded
concrete BOM input to the original schematic by hash. Independent source review
approved this bounded correction.

Agent-696's legacy annotation repair `73939a40` passed independent source review
and was integrated as `c55250b4`. It uses the existing guarded schematic stage
with separate symbol and footprint capture roots. The emitter and shared native
execution guard remain unchanged.

On `c55250b46deca3dd342c44fe5886adfee0b772b7`, all three original failing
controls passed under native KiCad: the bound FULL/LITE BOM variant, the
population-change refusal, and the legacy schematic turn. The invocation exited
0 in 20.6 seconds and retained 4,344 fixture files with hashes. Those fixtures
include intentional negative-test mutations and do not constitute an accepted
board or package. A subsequent selected strict run passed **7 modules and 153
tests, no skips**, exiting 0 in 95.8 seconds. It covers the qualified-rung,
rung-stage, schematic-stage, schematic execution, fabrication execution,
fabrication stage and tool-source modules. This is a selected regression pass;
the full strict gate has not passed. Prior failed attempts remain preserved.

Evidence is in `fab-legacy-native-r1` and `schematic-fab-regressions-r1` beneath
the host evidence root. The selected strict summary SHA256 is
`2db2c44142403c13fe5afe06cac05a18ec4c4ea93b75fd32b87b7ee3b2ce7f78`.
Its actual exit and times are retained in `entry-result.json`.

Independent review blocked the first citation repair because its pdftotext-only
adapter also intercepted unrelated custom-pad qualification. Agent-690 returned
child `35638486`, which limits the adapter to citation verification; that child
is undergoing independent re-review and has no native pass yet. Agent-697 has a
bounded source assignment for the demonstrated candidate-imaging namespace
failure, preserving the existing renderer, standalone sandbox, image identities
and ordinary-placement skip policy. No generic validation framework is authorized.

Campaign acceptance remains **0/4**. Main is unchanged. The board repair cap is
exhausted, the alternate ADC's assembly eligibility is unresolved, and full-suite
qualification, trunk integration, an accepted public package and M1 remain open.


### Citation repair passes native propagation and selected regressions

Independent re-review approved agent-690's scope correction `35638486`; the
combined citation repair was integrated as `d5a59a46` and `aebd08a4`. The two
original native propagation tests passed on `aebd08a4a47a31a834bccdd8e6b519d3b4244323`
in 5.3 seconds. Their retained records show successful pdftotext version and PDF
extraction inside the existing receipted sandbox. The test assertions cover the
separate bound intent and propagation through native symbols, board fields and
receipt-backed assembly checks. The 355 retained receipt files are fixture
evidence, not the campaign board's engineering acceptance.

The first selected regression invocation completed all 72 tests without skips,
then exited 1 because the coordinator had omitted the summary output's parent
directory. That failed invocation is retained in `rating-regressions-r1` and
has not been regraded. With the output directory created, the same three-module
selection on unchanged source exited 0 in 28.9 seconds: **72 tests, no skips**.
Evidence is in `rating-native-r1` and `rating-regressions-r2` beneath the host
evidence root. This closes the observed citation execution failure; full-suite
qualification remains open.

A source-and-log reconciliation leaves five original counted problems without
fresh rerun evidence: two generator fabrication controls, one generator BOM
variant control, and the two visual controls assigned to agent-697. The generator
BOM test still expects the old virtual input path; its correction must preserve
the full variant checks and verify the actual copied schematic's identity.
This list is a remaining verification queue, not proof that only five defects
exist. Campaign acceptance remains **0/4**, main is unchanged, and the board,
supplier, full-gate and integration limits above still apply.


### Remaining generator fabrication controls pass

The generator BOM-variant test now verifies the actual disposable schematic
path and its hash against the original reviewed input (`b850f958`). Independent
source review confirmed that FULL variant, registered-project, exit-code and
project-input assertions remain intact; production code was not changed.

On `b850f958b26e0bfc27c0d49e5b8b87dcfe47b2a9`, the two existing generator
fabrication controls and the registered BOM-variant control all passed under
native KiCad, actual exit 0 in 11.7 seconds. Evidence and real stage receipts
are retained in `generator-fab-native-r1` beneath the host evidence root.
The corrected package fixture still requires supplier preview, and it is not
the campaign board. Of the original 24 counted diagnostic problems, 22 now
have direct rerun evidence; the two visual controls remain pending. Agent-697
has finished its source assignment and its waiting native notice will be read
at the next turn boundary. Full strict, landing and campaign acceptance remain
open; acceptance is still **0/4**.


### Visual source review requires two bounded corrections

Agent-697's visual candidate `691e52b6` was read and its native notice explicitly
acknowledged. The candidate is not integrated. Independent source review verified
its pins and retained 16 pure passing tests, but blocked approval on two concrete
boundaries: a replaced output ancestor could escape the final link check, and
the outer imaging timeout could kill its Python supervisor before separately
grouped render/SVG children were cleaned up. These are static call-boundary
findings; no fresh native run or live counterexample is claimed.

The worker has a ten-minute, thirty-round, source-only correction grant in its
existing worktree. It must revalidate output ancestry and reuse existing render
cancellation and Host stop-grace behavior, preserving the renderer, placement
policy, shared executor guard and standalone sandbox. No new process framework
or host-policy change is authorized. The original report and failed review stay
retained under `visual-execution-repair-agent697`, with the correction under
`review-followup`. Review report SHA256:
`7b66bd8adff67800d69c065a4cfa2656b10501cc141a4fb8a6453424b7d838f3`.
Root retains native verification and integration. The two visual controls, full
strict gate and campaign acceptance remain open; acceptance remains **0/4**.


The focused timeout regression is committed as `d9cae1a3`, but has not run.
It exercises blocked render and SVG children through the actual enclosing
imaging path, shortens only the test's outer deadline, and checks owned
PID/start identities before the enclosing executor exits. Its authored PNGs
are process-lifecycle fixtures, not native image-quality evidence. The drafting
reviewer hit model capacity; the coordinator reviewed the retained test and
verified syntax of the module and embedded programs without a provider fallback.
The worker's correction notice is now waiting for native delivery. Source
review and native success/refusal/cleanup verification remain prerequisites to
integration of the production patch. No native invocation ran in this review
turn; the two visual failures remain unclosed.


### Native visual success and cleanup pass; captured placement route remains

The corrected visual source `5bd99b05` passed independent re-review and was
integrated through `662767d9`. Audit SHA256 is
`35af829c3d7832150f04c69906f48dedee338738e09f9140569a341cbe82dc3a`.
The audit was corrected to note that render_board re-raises KeyboardInterrupt
after cleanup; it does not proceed to SVG plotting after renderer interruption.

The four-control native run on `662767d9e4a62050c5d284dedd478f2aca56a464`
exited 1 in 32.3 seconds: three controls passed and one failed. Real placement
rendering through the enclosing /app route retained four candidate-bound
images. Ordinary placement skipped rendering. The new timeout control also
passed both phases: blocked real render/SVG child processes were gone before
the enclosing executor exited, with no owned PID/start identities remaining.
The shortened outer test deadline was eight seconds; recorded durations were
8.164 seconds for the render phase and 8.064 for SVG. The SVG phase used authored
PNG data only to reach the process-lifecycle boundary. This does not establish
image quality or determinism. All 846 fixture files and raw failed evidence
are retained under `visual-native-r1` beneath the host evidence root.

The remaining explicit-refusal control imports the captured helper source from
`/placement-tool`, not `/app`. Its outer executor completed, but the imaging
route still attempted nested Bubblewrap and received the original namespace
denial before reaching the deliberate renderer refusal. Its marker assertion
correctly failed. An independent read-only review confirmed the exact route
and its read-only `/placement-tool` and `/placement-request` mounts.

Agent-697 has a ten-minute, thirty-round, source-only follow-up to recognize
that exact existing placement view using the same parser and require both
extra mounts read-only. Default custom-pad recognition stays /app-only. The
imaging child must use the captured /placement-tool source for this route.
No configurable profile, copied parser, public trust option or mount redesign
is authorized. The prior failed run remains failed. Of the original 24 counted
problems, 23 now have direct rerun evidence. Full strict, landing, accepted
package and campaign acceptance remain open; campaign acceptance stays **0/4**.


### All original diagnostic controls pass; full qualification is next

Agent-697's exact placement-route correction `3794f9d8` passed independent
source review and was integrated as `9f4d5656`. Its audit SHA256 is
`60b88711ecc8099971b36f6e2aa316ae19cfa207022bcafc4b0983acffd57876`.
The three focused native controls passed in 16.4 seconds: the captured helper
reached the intentional renderer refusal and retained its marker, ordinary
placement skipped rendering, and the /app route still produced real images.
The 794 retained fixture files are under `visual-native-r2`. The earlier failed
run remains preserved and failed.

A subsequent selected strict run on
`9f4d56566508cab6382330fbff36a621843e63e4` passed **6 modules, 149 tests, no skips**,
actual exit 0 in 76.8 seconds. It covers candidate visuals, placement adapter,
visual execution, custom-pad execution, footprint library and library checks.
Evidence is in `visual-regressions-r1`. All 24 original counted diagnostic
problems now have passing rerun evidence. This is not a full-suite pass or
campaign acceptance. Existing CI checks, including the full suite and RC
control/seeded gates, remain prerequisites to trunk landing.

Direct owner instruction, 2026-10-29: "set a goal to continue until campaign
acceptance is complete". The active goal now explicitly covers G4's three
supported successes and correct refusal, current acquisition and original
imported esp_prog M1 closure, independent verification and reproducible release
evidence. Passing tests or landing code cannot complete that goal. The owner’s
constraint against new generic validation infrastructure and all declared
acceptance/custody boundaries remain in force. Current acceptance is **0/4**.

### CI launch correction and current original esp_prog result

Existing CI preflight on `f19f73dcdf4c9cc42fdaba02c1af58f8d8750725`
passed cheap checks, doctor, sandbox admission and the RC control/seeded action
gates: actual exit 0 in 33.85 seconds. Checks-only review's expected nonzero
status was evaluated by the existing action gate, not treated as acceptance.
Evidence is under `ci-preflight-r1` at the host evidence root.

The coordinator omitted the retained private-input environment from the next
full strict invocation. Required acquisition tests therefore skipped. R5 was
stopped rather than allowed to consume the remaining budget: actual exit 143
after 836.62 seconds. Its partial logs, exact command and cancellation are
retained under `full-strict-r5`; the owned container was removed and admission
returned to zero. This is **not a full-suite pass**. The next authoritative full
run must include the staged input environment and explicit acquisition plan
locator, after the pending production changes are integrated. No new controller
or runtime profile is needed.

One separately admitted public `place run` then consumed the **original**
esp_prog board `b4aa540d031e18283216d806aa6288cf51260e7328f9aaeb13b55d4eac93eef7`
and its original project, intent and groups on the same frozen source. Only four
config paths were rebound to the staged exact copies. Original seeds,
100000 steps per seed, shortlist six, rules and 1200-second job budget were
preserved; the enclosing grant was 1800 seconds with a 300-second grading
reserve. Actual outer exit was 0 in 620.54 seconds. All four original input
hashes remained unchanged. The job performed six search seeds and two routing
trials inside one invocation; its first trial left one open and remains failed.

The selected board is
`875d0bd2526e05d367bed3c1ed819d611c6c63b14b0d9482d0de92ca45511071`.
Its saved native DRC has zero errors and zero opens. CON1 and CON2 are on the
declared east edge; USB1 is on the declared west edge. Native identity checks
report preserved integrity. There are **255 warnings**: 21 library, 6 silk/edge,
46 silk/copper, 162 silk overlaps and 20 text-height findings. Group views are
retained but explicitly have `acceptance_assessed: false`; locality and strict
grouping are not declared by this original input. The job itself reports
`accepted: false` and `clean: false`. Readable artwork, group/relationship
acceptance, assembly/profile/site/cable evidence and M1 release remain open.
Evidence is under `esp-original-native-r1`; job-report SHA256 is
`50e73814ec18b1c34a4442e18fb33db922a4ee585218c0c838acecb879442f12`.
Native admission is free after completion. Agent-695 owns a bounded read-only
independent outcome audit; no second placement run is granted.

Two source implementation grants now address demonstrated product gaps:
agent-691 reuses the existing native block-contract evaluator to bind final
artwork to strict package qualification, and agent-693 captures an explicitly
requested review profile through the public acquisition preparer. Each works
in an isolated worktree; neither may run native jobs or land independently.
Their source reviews and target verification remain pending. The acquisition
access/body-gap and local-return production repair are still unresolved;
independent audits reject treating fixed poses as movement commands or giving
zone traversal zero length. No third saved-board repair is admitted.

Agent-692 produced one separate offline alternative-input draft with the
existing catalog and part validators: one invocation, exit 0 in 0.468 seconds.
The derived catalog preserves both original observation windows and all twelve
raw responses; it does not reset freshness or claim a new network capture.
Independent source review approved only that draft's custody and purchasing
bindings (audit SHA256
`71ea6466db1199a93d3248e89278dbd8a067754c029bd058483e0e43732a088a`).
It is not adopted into a run and does not prove automatic selection, assembly
eligibility or a fresh accepted board. Supplier/physical statuses remain
pending. Old preparation, stock-query and saved-board repair limits remain
exhausted. G4 is unstarted and campaign acceptance remains **0/4**.

### Preparation inputs integrated; final geometry remains the critical path

The public acquisition preparer now captures an explicitly supplied review
profile, including its exact bytes and requested/effective verification timeout,
in the preparation identity. Agent-693's `4036c58a` passed independent source
review and was integrated as `fd93d85c`. The corrected adjacent pure run passed
23 tests without skips in 6.52 seconds. Its earlier failed invocation remains
retained. No actual acquisition preparation or full review ran under this grant.

The same preparer now supports finite decimal capacitance bounds throughout
100–150 pF, integrated as `014f91e6` from `e3ef1595`. One scalar drives the
captured specification, interval operand, calculation, operating text and
source mappings. The existing checker permits 28 canonical coefficient digits;
excess precision refuses instead of rounding. The fixed 2222 ohm worst-case
pullup and 300 ns limit are unchanged. The upper bound is 282.40509 ns under
the captured RC model. This is arithmetic applicability, not waveform evidence.
Independent source review approved the patch; 27 pure tests passed without
skips, and integrated source/helper hashes match the tested candidate. Public
development values are 100, 125 and 150 pF. The independent unseen case has
not been selected or disclosed. Evidence is in
`preparation-capacitance-range-agent693` beneath the host evidence root.

Independent review blocked strict package candidate `16a9cb0b`: its report
validator accepted matching identities and a pass status even when membership,
roles, blocks and relationship measurements were absent. Rehashing the incomplete
report and its artwork custody could bypass the boundary. Agent-691 has a
15-minute correction grant with at most two pure test commands. The candidate
is not integrated. Actual native positive and attributable final-coordinate
negative evidence remain required after the correction.

The placement input audit found that the acquisition's source body and access
models do not reach the actual Rust placement job. Agent-697 now owns a bounded
source patch to carry those models through existing capture, search and saved
coordinate evaluation: exact 0.2 mm Euclidean body gap, in-board bodies and
connector access columns expanded by the existing 0.5 mm rule. Native locked
J1/J2/J3, courtyard/copper occupancy, grouping and electrical locality remain
required. This is a production constraint change, with no new controller or
general validation framework. Its 40-minute grant permits four focused CPU
test commands and no actual target preparation, KiCad/router or model execution.
The separate ADC routed-loop failure still requires a production correction.

Agent-695 completed the independent original esp_prog outcome audit. It
confirmed the exact selected `875d0bd2` candidate and retained raw native
results; it did not accept artwork, grouping or assembly. The next offline
assignment prepares actual-candidate inputs for the existing campaign and
routed-artwork producer. It grants no second placement job and does not
transfer historical artwork evidence to this board.

The judgment source audit also corrected inherited narrative claims: TPS7A20
DBV thermal resistance is 187.1 degrees C/W, while 201.4 belongs to YCK; the
16.5 mV rail allowance consumes the remaining two-percent budget; and the
connector's mounting-height description is not a dimensioned mated drawing.
Those corrections must inform fresh candidate-specific judgments. None of the
14 required judgment records has been accepted. Evaluator gaps and automatic
alternative selection remain open. All original exhausted attempt limits and
failed artifacts are preserved; campaign acceptance remains **0/4**.

### Strict proof correction integrated; native control exposed input problems

Agent-691 corrected the incomplete-proof boundary by retaining the actual saved
native capture and replaying it through the existing block grader. Package
verification requires exact complete report equality. Independent re-review
approved `f806789b`; it and its parent were integrated as `ead3e128` and
`b4a41a2a`. The final pure run passed 78 tests; the earlier import failure is
retained. Actual package success remains unproven.

A separate 51-file, three-part synthetic input bundle adds honest manufacturing
coverage to the retained strict reference control. This is software composition
evidence preparation, not an acquisition or G4 case. Two actual public build
invocations on `b4a41a2a` failed and remain retained:

- `strict-package-native-r1`: exit 1 in 1.402 seconds at requirements. The
  coordinator copied frozen read-only directory modes into the runtime input;
  the candidate could not create its requirements provenance directory.
- `strict-package-native-r2`: exit 1 in 2.887 seconds after requirements and
  parts. A fresh owned runtime copy had the same 51 content hashes and corrected
  write permissions. The standalone netlist-export requirement had no executable
  bound coverage: the exporter supplies data for later checks and is not itself
  an acceptance predicate. No placement, artwork or package ran.

Both jobs and their staging errors are preserved. Native admission is free.
The two-invocation control cap is exhausted; a bounded offline correction now
combines native-netlist evidence with its actual parts-membership obligation.
No third native invocation is admitted by that preparation assignment.

The two active implementation builders are agent-697 for source-bound body/access
constraints and agent-693 for a bounded local ADC return operation. They have
separate file ownership. The latter must use freshly resolved pads and unchanged
width/clearance rules before global routing; the existing final checker still
measures both fresh supply and return legs against 10 mm. No zero-cost plane
edge, copied old coordinates or renewed saved-board repair is permitted.

The actual original esp_prog finalization draft exposed three remaining product
behaviors under AGENTEE-523: explicit applicability for a PCB-only article without
a supplied schematic, source-bound formatting of the 20 undersized markings,
and a selected process policy with individual via/pad-site dispositions. Its
source snapshot is frozen at `0c0dbd01`; it correctly flagged later integration
changes and is not runtime-ready. No draft execution, new placement run, supplier
approval or artwork acceptance is implied. The direct owner goal stays active
through G4 and current M1 closure; acceptance is still **0/4**.

### Native strict-package control passes; acquisition mechanics integrated

At 2026-10-29 09:37 UTC, campaign acceptance remains **0/4**. Two separately
admitted development controls followed the retained R1/R2 failures:

- R3 exited 1 in 27.133 seconds. Requirements through schematic completed,
  and routing produced zero native DRC errors and zero opens. The strict block
  gate correctly returned unknown because the artificial footprint sources
  omitted courtyard geometry. Artwork and package did not run.
- R4 added explicit closed front courtyards to the two owned artificial
  footprint sources, using unchanged pad bounds plus a 0.25 mm synthetic
  margin. Existing CAD and parts producers regenerated their affected source
  receipts. One public build on `d61456dd` completed all seven stages, exit 0
  in 57.102 seconds. Its final saved block report passes with complete geometry
  and no defects or unknowns. The package binds that proof and final board
  `c9fabe2531452be4ded87a1a2079f43169dca20bb64bd90c27e2a18696c7106a`.
  Package DRC has zero violations, opens and parity mismatches. Native lease
  and container cleanup were verified before releasing the source freeze.

R4 is a synthetic software integration control. Its legacy qualification has
`file_release: pass`, but explicitly withholds requirement claims: all eleven
legacy requirement coverage rows remain unknown. The checks-only review reports
incomplete; engineering review, judgments and operational qualification were
not assessed. Supplier and physical validation remain pending. This result
proves the final-artwork/package bridge executes successfully; it does not
establish acquisition, M1 or G4 acceptance. No late-group-mutation negative was
run under that grant. Evidence: `strict-package-native-r3` and
`strict-package-native-r4` beneath the host evidence root.

Independent review approved mechanics candidate `940e8b5a`. It is integrated
as `4de87d18`, preserving the capacitance-range helper and adding the mechanics
helper to source identity. The actual captured-fixture preparation and adjacent
pure checks passed 39 tests without skips. The first run's obsolete assertion
that the placement plan remained identical failed and is retained; the revised
assertion checks the new mechanics declaration and its sealed source hashes.
Worker evidence separately retains 39 passing Rust tests and 13 Python tests.
No new actual acquisition preparation or native target run occurred.

Agent-697 now owns a 20-minute, source-only serial bridge patch in an isolated
worktree. Package replay must authenticate the original mechanics recipe,
capture and problem, the passing final placement result, and the existing
artwork receipt's preservation of physical geometry. This reuses the existing
package and artwork surfaces. Root retains integration and qualification.

The local-return helper remains unintegrated. Its first correction closed the
missing/suppressed DRC-coverage gap, but independent review found that rejecting
every warning also rejects the acquisition's unchanged missing-library metadata
in the raw helper context. Agent-693 has one 12-minute correction grant: capture
a fresh same-input baseline and permit only the exact unchanged missing-library
warning records, while preserving all conductive, unknown and suppression
refusals. The existing clock bounds that baseline and at most five seed paths.
No old failed board or rule-loading witness may serve as the baseline.

The original esp_prog schematic-provenance investigation is read-only and
bounded to eight minutes. It checks for an already retained matching schematic
before introducing PCB-only applicability. The original routed candidate stays
unchanged. Old preparation 3/3, saved-board repair 2/2 and supplier-query 3/3
allowances remain exhausted. Full review, M1, frozen G4 evaluation, authoritative
full-suite qualification, release and trunk integration remain open.
