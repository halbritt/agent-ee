# AGENTEE-521 consumer verification — 2026-10-25

The qualification consumer now derives separate checks, file-release, operational,
engineering-review, judgment, supplier and physical states from captured evidence.
A refused later invocation preserves the earlier build records. Corrupted retained
evidence withdraws the current acceptance claim and archives the exact previous
root records. Historical runs without a captured plan refuse migration rather than
silently acquiring a new assessment contract.

This accepts the implementation slice. AGENTEE-521 remains open for its typed
acquisition qualification and negative cases; M1 is not complete.

## Inspected implementation and independent review

Agent-568 delivered `36da8f71` followed by correction
`400360e975cd937d1fd583c143403afc428c49ab`. The chief rebased both onto
`12528327`, producing `8fcf070ab4ace4d82564e6c9fa97798e5a36a12e` with all seven
delivered source, test and documentation file hashes unchanged.

Independent Fable review `cf4eeee9-3625-4659-b00d-a490b82d1026@1` identified
historical root-record mutation in the first delivery. Fixed-revision review
`b0e653ca-767c-4435-a80b-9e6192970055@1` found that correction separates external
invocation refusal from retained-evidence corruption. These were source reviews;
neither substitutes for the chief's native verification.

The chief inspected the refusal ordering, archive publication, assessment states,
regression tests and committed resume probe. The implementation checks retained
source/stage/judgment custody before comparing new invocation identity. Missing
required assessment remains pending; demonstrated defects remain failed. Ignored
sourced-rule evaluation remains unavailable and non-passing, consistent with
`design_rule_provenance.require_evaluated_limits`; the required release gate
remains closed. No native rule, threshold, reviewer requirement or supplier/physical
claim was relaxed.

## Chief verification

The supported runtime was
`sha256:939f4bfb6bb45eaff5fc47d27b94f905ec979e31ec66a190fe6fb57ff96753e1`.
The chief ran:

```sh
AE_CI_MAX_WAIT=60 ae-ci-exec WORKTREE -- python3 scripts/strict_suite.py \
  --jobs 1 --module-timeout 600 \
  test_qualification test_parts_stage test_build_review
```

All **43 tests in three modules passed with no skips**. These include the real
public legacy and typed requirements/parts/scaffold/schematic paths, checkpoint
and resume behavior, judgment custody and actual ordinary native checks.
Documentation, origins and diff checks also passed.

The chief separately reproduced the committed `qualification_resume_probe.py`
under native admission using an isolated copy of acquisition `release-build-v7`.
All seven original stage trees were verified before copying. The private fixture
received a fresh actual package review, the captured plan and a current runtime
binding; historical attempt records remained unchanged. This is an explicit test
fixture adaptation, **not a fresh public end-to-end build or operational proof**.
No passing check rows, engineering judgments or witnesses were fabricated.

The fresh native package evaluation returned **37 pass and one info**, with board
SHA-256 `d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7`
unchanged. Legacy file-release passed with current method claims withheld;
engineering review, judgments and operational status remained not assessed, and
supplier/physical status remained pending.

| Control | Observed result |
| --- | --- |
| Unchanged private accepted fixture | Public resume exits 0 |
| Changed external source, plan or real runtime | Each exits 2; prior report and qualification bytes remain identical; separate refusal recorded |
| Historical no-plan migration | Exits 2; previous report bytes remain identical; no new qualification claim |
| Corrupted retained source, final-stage board or ordinary review | Each exits 2; current acceptance withdrawn; exact previous root bytes archived |
| Original retained design and all seven stage trees | Unchanged after the complete probe |

Typed frozen-judgment refusal is covered by the strict suite. A genuinely accepted
typed target run still requires the 14 independent author/concurrence judgments;
this legacy probe does not establish that acceptance.

## Retained evidence and remaining work

Chief evidence:
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/qualification-521/`.
The source manifests bind 12 inspected source/test/runtime-input files, the strict
suite log, launcher command and isolated native result. The native proof file
`native/verification.json` has SHA-256
`021a28bba57c488fee234830dfdb8eb2623d754d2c00655cb414ab8b5d65981c`.
Original records, refusal receipts and supersession archives remain beside it.

The first private launcher stopped before any Docker/native work because its
Python verification subprocess closed the inherited admission descriptor. The
chief corrected descriptor inheritance in that private launcher and retained the
setup note. The admission mechanism and runtime guards were unchanged. The
successful invocation used Docker's outer init; the image's nested tini emitted
an informational PID-1 warning. The named container exited and was removed.

Remaining acceptance follows the [claims contract](claims-evidence-qualification.md):
actual typed acquisition A/B/B2/R/N cases, cancellation/resume under unchanged
identity, independent candidate-bound engineering judgments, topology negatives,
and final package/decision evidence. The original esp_prog workflow, functional
group views and semantic artwork acceptance remain separately tracked. Telemetry-pod
and owner-reserved actions remain held.
