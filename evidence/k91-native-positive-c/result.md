# AGENTEE-442 result — positive-c: the one bounded native K91 positive (consumer boundary)
Status: BLOCKED

**Plain statement: the native positive was NOT RUN. The run was refused at the runtime-identity stop, before any router process, so nothing was ACCEPTED. It is INCOMPLETE, not a REFUSED positive.** I did not attempt it, because the dispatched runtime is not the qualified one, and because that pin cannot produce the evidence the positive contract requires (details below). The one authorized attempt is therefore **unspent**. No router run, receipt, result.json, adapter-report, control or adoption exists.

Work done by: Claude Opus 5.5 (claude-opus-5-5), worker route opus, default effort. Host: proximal.

- Worktree: /home/halbritt/.cache/agent-worktrees/agent-ee/ee442-native-positive-c
- Branch: wt/ee442-native-positive-c
- Base: efb77f64ca33e8f8441452e05604a6e1f8801477, plus the worker marker 1c175eeaf5e49505f96a59c3a76c40654811d14f
- Commits:
  - 1274245ce36b3019008e9f7f470ff7d9613e94b1 — composition merge (`git merge --no-ff d2a25162`); tree 1b56784922a96a2c351b7be7d7b542296bf65804; parents 1c175eeaf5e49505f96a59c3a76c40654811d14f and d2a25162d0263955864d64996da8a285cd73c88f;
  - 1daab5f62c7dbaaa4d4ef43c91c31b61fc0bab2d — refusal evidence under the NEW path `evidence/k91-native-positive-c/`, plus its file-origins rule;
  - the result commit, which adds this file as `evidence/k91-native-positive-c/result.md`.

## STEP 1 — composition (DONE)
`git merge --no-ff d2a25162` conflicted only in `docs/routing-adapter.md`, in one hunk (HEAD lines 1088–1099 against theirs lines 1101–1189).
- I DOCS-UNIONED it: both sides kept, only the three marker lines deleted.
- `git diff HEAD -- docs/routing-adapter.md` shows 89 insertions and 0 removed lines; the 89 lines are exactly theirs.
- No other conflict. The conflict-marker grep printed nothing.
- Union artifact for the reviewer: both sides continue the same sentence, so the paragraph now reads with two endings. That is inherent in a pure union with "no other edits".

## STEP 2 — runtime recompute (REFUSED)
Probe: `evidence/k91-native-positive-c/runtime_probe.py`. It uses the v1 harness helpers `engine_identity`, `sha256` and `attempt.checkout_commit`, and it never routes. I ran it once via:

```
AGENT_EE_SKIP_VISUALS=1 ~/.local/bin/ae-ci-exec . -- sh -c 'python3 evidence/k91-native-positive-c/runtime_probe.py > evidence/k91-native-positive-c/runtime-probe.json 2> evidence/k91-native-positive-c/runtime-probe.stderr; rc=$?; printf "\nexit=%s\n" $rc'
# exit=0 / outer-exit=0
```

The image ae-ci-exec selected was `agent-ee:exec-758b370fecf753c0`, id sha256:e977faff65f2f0bb8a6f080d0bcf2c173a7873f1892cd22b02ad49bc3a9eff39 (`docker image inspect`). The 466 image is `agent-ee:ee466-gate-b29cead1`, id sha256:d96f8e00a50a1bb6fab119ac8973a083af9373e2c9c235f3df20f655cfc3b5b7. These are different images.

| Identity | Selected (recomputed) | 466-qualified | Match |
|---|---|---|---|
| commit (checkout HEAD, manifest) | b5774496d8466b67ffa0362b6665fee2108155a3 | b5774496d8466b67ffa0362b6665fee2108155a3 | yes |
| engine source sha256 | 9cfc5d5e9369fd044ac6555afe57bdc4ccc829421e5496d42599c2d4a71951b2 | 9cfc5d5e9369fd044ac6555afe57bdc4ccc829421e5496d42599c2d4a71951b2 | yes |
| extension grid_router.so sha256 (file and manifest) | 03806afa07ad635cf4c4416628c7edde1bc67c308992acec837c939f0a1b85a1 | ec5fb733d321343456917b2925ec275af233e3a44d34b2f332800d51352b2cd9 | **NO** |
| manifest generation / drift | null / [] | null | yes |
| interpreter | /opt/kicad-route/.venv-krt/bin/python -> python3 -> /usr/bin/python3.12 (3.12.3) | — | — |
| venv packages sha256 | 0dc7c913210ed2d7f0afe996b4f936eaee539c19ca5e6e5d76949d13eaf304ae | not stated in brief | — |
| manifest.json sha256 | 34a74cd0eb7b26b9c793a8d86094d1240586ae55dd7af627bebee0de6b9fd8d0 | — | — |

The qualified extension digest ec5fb733… comes from the 466 review's `image-verify.log` ("SELECTED EXTENSION ec5fb733…", reviews/ee466-repin-runtime-sol.md:272). That was image d96f8e00.

ae-ci-exec builds its own `exec-<input hash>` image for this worktree and cannot select d96f8e00. Its extracted router copies (`~/.cache/ae-ci-exec/kicad-route/758b370fecf753c0` and `/5823d89949a3eebc`, both at b5774496) both carry 03806afa…, which the host `sha256sum` confirmed. The Rust extension appears not to be reproducible across image builds. **Per the brief ("If the selected runtime is not exactly that, REFUSE and report; never relabel"), I refused.**

**Second, independent blocker (a premise gap in the dispatch).** b5774496 has no routability-experiment/1 producer:
- `src/kicad_route/experiment.py` is absent (probe `producer_modules`). Only the `experiment_policy.py` validator library exists.
- The selected `src/kicad_route/cli.py` contains zero occurrences of "experiment" (`grep -c` = 0), so there is no `--experiment-policy` flag. Its sha256 is d43569e4d81a4355712d356efec35ec40167f3318c9155c8172792164dcd01b1, equal to `PINNED_CODE['src/kicad_route/cli.py']` as printed by both commands.
- The pin commit's own subject is "Record custody landing and remaining experiment qualification holds".
- The production adapter passes `--experiment-policy` whenever a policy is declared (routing_adapter.py:101). The v2 positive contract (`k91_classify.positive_accepted`) needs `experiment.status == 'retained'`. On this pin a declared-policy run can therefore only be a usage refusal (exit 4) with an absent experiment record and no candidate. That outcome is `positive_accepted: false`, and both b-tamper controls would be "skipped", so INCOMPLETE.
- `docs/routing-adapter.md` ("**Pin.**" paragraph) already says this, read from source.

Running the single attempt would have spent it on a predetermined non-positive.

A probe mistake, disclosed: the `cli_help` and `cli_experiment_policy_flag` fields called the subcommand `route`, but the CLI's subcommand is `run`. Those fields are NOT evidence about the flag; the cli.py bytes above are. The resulting kicad-route usage-refusal records were written into the worktree's gitignored `build/`. I moved them into `evidence/k91-native-positive-c/probe-refusals/` (three files, including refusal-latest.json), so no stray state is left.

The old isolated pin is NOT used. The earlier plan's pin was kicad-route bd1e633f2e585f4fc931ceab001299b78d0d8bb7, with engine source cb003b30… and extension 610b3acd… (v1 defaults). It differs from b5774496: bd1e633f is not an ancestor of b5774496, and the image checkout is a single-commit snapshot that cannot name it. bd1e633 does carry the K91 producer (experiment.py), which is the only reason the v1 attempt could produce a sidecar at all.

## STEP 3 — the attempt (NOT RUN)
- Full command: none was executed for the attempt. The intended command would have been `scripts/k91_native_positive_v2.py --checkout /opt/kicad-route … --router-limit-s 900 --total-limit-s 2400` under ae-ci-exec. I never started it, because its expected `--extension-sha256 ec5fb733…` would itself have stopped it with exit 3.
- Attempt receipt, result.json and adapter-report hashes: none exist.
- v2 classification (classifier `agent-ee.k91-native-positive/verdict-2r4`, k91_classify.py sha256 55563ea17c7e4ba725219717904a95beb0a5814676558f5690c0500f102dd706). Every layer is "not attempted":

| Run | consumer run_step | consumer verify | consumer adopt | direct/bare adopt | v2 state |
|---|---|---|---|---|---|
| positive | not attempted | not attempted | not attempted | n/a | INCOMPLETE (not run) |
| (a) mismatched runtime | not attempted | not attempted | not attempted | not attempted | INCOMPLETE (not run) |
| (b) candidate-board tamper | not attempted | not attempted | not attempted | not attempted | INCOMPLETE (not run) |
| (b') sidecar tamper (separate) | not attempted | not attempted | not attempted | not attempted | INCOMPLETE (not run) |
| (c) stale input | not attempted | not attempted | not attempted | not attempted | INCOMPLETE (not run) |

- Wall times: router 0 s (never started). The runtime probe's admitted run was queued about 13 min behind the pod-capture-j6b2 suite lease and ran in seconds.
- Production boards: no adoption into any board, production or disposable. No repin, no pin or default change.

## Criteria
| Criterion (positive-c brief) | State | Evidence |
|---|---|---|
| Compose efb77f64 + d2a25162, docs-union only | MET | 1274245c… / tree 1b567849…; 0 removed lines in routing-adapter.md; marker grep empty |
| Recompute the selected runtime identities | MET | runtime-probe.json, table above |
| Selected runtime is exactly the 466-qualified one | UNMET → REFUSED | extension 03806afa… ≠ ec5fb733… |
| One native step→run_step→verify→adopt through campaign.run_implementation_steps | BLOCKED | the runtime refusal; and b5774496 cannot emit routability-experiment/1 |
| v2 classification of positive and controls (a), (b), (c), sidecar | BLOCKED | nothing to classify |
| Preserve receipt 9352b0f1, evidence/k91-native-positive/ and v1 harness bytes | MET | `sha256sum -c --quiet SHA256SUMS` in evidence/k91-native-positive → exit 0 (13 entries); `git diff --quiet 553e4151 HEAD -- scripts/k91_native_positive.py` → 0; `git diff --quiet 7552f6db HEAD -- evidence/k91-native-positive` → 0; v1 harness sha256 97d6055d148ef40ae110a09ab6c906f28d0ceea65afc4949f44ca531242e600f |
| New evidence path with SHA256SUMS | MET | evidence/k91-native-positive-c/SHA256SUMS; `sha256sum -c` → all OK |

## Validation
- There are no unit suites (brief TEST POLICY). The only executable change is a new probe script and an origins rule.
- Host, diagnostic only: `python3 scripts/check_origins.py` → errors [], and `python3 scripts/check_docs.py` → errors [].
- Gating, on proximal, on composition + evidence commit 1daab5f62c7dbaaa4d4ef43c91c31b61fc0bab2d:
  ```
  AGENT_EE_SKIP_VISUALS=1 ~/.local/bin/ae-ci-exec . -- sh -c 'python3 scripts/check_docs.py ...; python3 scripts/check_origins.py ...; env PYTHONPATH=src:tests python3 -m unittest -v tests.test_tree_hygiene ...'
  # check_docs errors [] ; check_origins errors [] ; test_tree_hygiene: Ran 6 tests, OK, 0 skips
  # docs-exit=0 origins-exit=0 hygiene-exit=0
  # outer-exit=0
  ```
  The logs are in scratch/ (evidence-checks.*.log, checks.log).

## Changes
- `docs/routing-adapter.md`: the union only (from the merge).
- The merge brings in the v2 harness files: scripts/k91_classify.py, k91_consumer.py, k91_control_matrix.py and k91_native_positive_v2.py, plus tests test_k91_control_matrix, test_k91_harness_classifier and test_k91_reason_probe.
- `docs/file-origins.json`: a new rule, `evidence/k91-native-positive-c/` (retained-output, AE-RUN).
- `evidence/k91-native-positive-c/` contains:
  - NOTE.txt
  - runtime_probe.py
  - runtime-probe.json and runtime-probe.stderr (empty)
  - probe-refusals/ (three files)
  - SHA256SUMS
  - result.md
- There are no src/, test-expectation, pin, default or registration changes.

## Unmet and gaps — coordinator decision needed (not an owner gate)
A native K91 positive needs a router runtime that is BOTH qualified AND carries the K91 producer. No such runtime exists today:
- b5774496 is qualified (in image d96f8e00 only) but has no producer;
- bd1e633 has the producer but is the old, unqualified isolated pin that the brief forbids.

Possible paths, for agent-449/agent-521:
1. kicad-route lands the K91 producer (experiment.py and `--experiment-policy`) on a commit that is then qualified and repinned. The positive-c attempt runs after that, and the attempt stays unspent until then.
2. Explicitly authorize the isolated proposed pin (bd1e633 or a successor) again, as v1 did, with its own runtime binding.

Separately, every ae-ci-exec image rebuild changes the extension digest. Either qualify by the selected image (d96f8e00 cannot be selected through ae-ci-exec), or record the rebuilt extension as a new runtime boundary that needs its own verification.

## Reviewer risks
- Probe: is refusing on the extension digest correct, or is 03806afa… an equivalent qualified build? It is not equivalent by bytes, and no qualification of 03806afa… exists that I could find (grep of reviews/notes shows only ec5fb733…).
- Confirm b5774496 has no experiment producer from the image copy: `grep -c experiment ~/.cache/ae-ci-exec/kicad-route/758b370fecf753c0/src/kicad_route/cli.py` → 0; experiment.py absent.
- Check the docs-union paragraph reads as a pure concatenation, with no content edits.
- Check that the probe-refusal files are only kicad-route usage refusals from `--help` calls: argv `["route","--help"]` and `["route","--experiment-policy","/nonexistent","--help"]`.
