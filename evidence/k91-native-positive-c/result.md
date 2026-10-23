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
  - f01b1601bdc554fc7a9422337d4461acede38c12 — adds this file as `evidence/k91-native-positive-c/result.md` (the committed copy predates this SHA line and is not in SHA256SUMS).

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

---

# bd1 preflight and attempt (resume 2026-10-22/23, agent-449 relay of main + agent-521 runtime disposition d514d9f3)

Status of this section: **INCOMPLETE**. The preflight was EXACT, and the ONE attempt ran (no retry).
- **The native positive was ACCEPTED.** It went through the actual consumer `campaign.run_implementation_steps` (run_step, then verify_campaign_receipt, then adopt), into the disposable OUT/positive/design copy only.
  - **R1 scope qualifiers (agent-510, verbatim):**
    - The RC fixture was ALREADY CONNECTED. baseline_unconnected_total=0 and unconnected_total=0; new_tracks, new_vias and new_track_length_mm are all 0; backend successful, failed and total_iterations are all 0; gained, lost and worsened are empty; terminal metrics are 3 nets, 0 incomplete, 0 deficit; native DRC 0 violations, parity and preservation PASS.
    - So the positive proves real run_step → verify → adopt PLUMBING on the bd1 runtime. It is NOT evidence of routing work or algorithmic improvement. Changed board/tree hashes must not be read as newly routed copper.
    - Bare stage_receipts.adopt ACCEPTS a sidecar-tampered result, while the full consumer refuses it. That is the known AGENTEE-465 boundary and must be retained as a stated limitation.
    - Control A stays INCOMPLETE.
- **Controls:**
  - (b) candidate-board bytes: REFUSED.
  - (c) stale input: REFUSED.
  - The separate sidecar-bytes control: REFUSED.
  - (a) runtime mismatch: **INCOMPLETE under the unchanged v2 classifier.** The production callable did refuse it, but the refusal message does not match the contract's anchored reason pattern (see "Control (a)" below).
- Therefore `controls_refused` is false, and the harness exited 1.
- I did not relax the classifier and did not retry.

Work done by: Claude Opus 5.5 (claude-opus-5-5), the same positive-c worker. Host proximal; no GCP.

## Composition
- I merged the accepted composition head 73d0aa31246e77d92f8445f51bcb4692a45a6d5c with `git merge --no-ff`. The result is merge commit **1e9255632668ebfa97ca2c0cbd1105c9d1fe5b6a**, tree **3a406d0e0f628856fcdaee24070647b698479928**.
- The only conflict was `docs/routing-adapter.md`, a docs file. My side's only change to it was the earlier unreviewed union (1274245c) of the same two sources. 73d0aa31's version already contains both sides, so I took 73d0aa31's file: `git diff --quiet 73d0aa31 -- docs/routing-adapter.md` returned 0.
- The conflict-marker grep printed nothing.
- `git diff --stat 73d0aa31 1e925563` shows only my positive-c evidence and its file-origins rule (10 files, 307 insertions). Everything else is exactly 73d0aa31.
- Attempt and evidence commit: see the final commit list at the end of this section.

## Copy verification
- `cp -a /home/halbritt/git/kicad-route/build/k91-consumer-runtime-agent507 build/k91-isolated-bd1`; build/ is gitignored, 759M.
- Script: `evidence/k91-native-positive-c/bd1/verify_packet.py` (host, read-only hashing). Output: `packet-verification.json`; exit=0, problems [].
  - Copy against the original: 17983 = 17983 entries (type, mode, symlink target and bytes); copy_mismatch [].
  - code-map.json: 313 entries, all equal on disk AND as committed blobs at HEAD bd1e633f2e585f4fc931ceab001299b78d0d8bb7.
  - Canonical map sha256 63939cc4e806e458c2afe5ce7935c49490b15dbf5ae6f1a9c846eb4ab2d331b1, reproduced as sha256(json.dumps(sort_keys=True)).
  - venv: 4293 = 4293 entries found = venv-files.json (4289 files by bytes+sha256, 4 symlinks); venv_mismatch [].
  - Symlinks: .venv-krt/lib64 -> lib; bin/python -> /usr/bin/python3; bin/python3 -> python; bin/python3.12 -> python.
  - venv-files.json sha256 48df7e0789aa54eaa278e0768bd2eee9aae11ac82d4c709c1939724964408320; manifest.json sha256 33cd344e95733941590ffc6cc63e6002ca638077450c0568d3a6ae30f82a3212.
  - runtime-L git status clean; no alternates; .git is a directory.
- A self-inflicted deviation, repaired: my first verification run's `git status` refreshed the COPY's `runtime-L/.git/index` stat cache (16c38d2a… against the original 5ecaf6b3…). `git ls-files -s` was identical in both.
  - I restored the copy's index from the original with `cp -a`; both then print 5ecaf6b3ab423789d62bc6a5e50e573cd1972c23b3f92e149bfd6a257ff2bfef.
  - The script now runs git against a scratch GIT_INDEX_FILE.
  - The original packet was only read.
- The same check ran inside the container (`packet-verification-in-container.json`, problems [], packet-exit=0). It ran again on the host after the attempt (`packet-verification-post-attempt.json`, exit 0, problems [], copy_mismatch []). The router did not write to the packet.

## pinned() diff
`git diff 73d0aa31 HEAD -- scripts/k91_consumer.py` is **empty (0 lines)**. No source change was needed.
- `pinned(out, pin, code)` is already parameterized. The v2 harness passes `--commit bd1e633f…` and `v1.code_map(runtime-L, commit)`, which is read from runtime-L's git blobs and equals the packet's code-map.json (preflight item `code_matches_packet`).
- It then writes the real adapter copy and patches PIN, PINNED_CODE and ADAPTER_SOURCE only.
- The classifier, controls, frozen input and policy hashes are byte-identical to d2a25162: `git diff --quiet d2a25162 HEAD -- scripts/k91_classify.py scripts/k91_consumer.py scripts/k91_native_positive_v2.py` returned 0.

The adapter copy actually mounted (`bd1/out/pinned-adapter/kicad_route_attempt.py`, sha256 214696fbe3127a92f400e8f1433720ea25674bd228c0591a0097214e9e7ecdee) against production `src/agent_ee/kicad_route_attempt.py` (sha256 a1704d4d39c93eaef0cd5a513d5177ac066996d7ff7b022f6beeb931bca96e18) has exactly two hunks:
```
39c39   < PIN = 'b5774496d8466b67ffa0362b6665fee2108155a3'   > PIN = 'bd1e633f2e585f4fc931ceab001299b78d0d8bb7'
41,349c41   < PINNED_CODE = {...b577 map...}   > PINNED_CODE = {...313-entry bd1 map...}
```
The production pin, the code map and the defaults on disk are unchanged.

## Preflight (in the selected container), all EXACT
Command (admitted after 160 s queue; ran 01:26:42Z–01:26:47Z; packet-exit=0, preflight-exit=0, outer-exit=0):
```
AGENT_EE_SKIP_VISUALS=1 ~/.local/bin/ae-ci-exec . -- sh -c 'python3 -I evidence/k91-native-positive-c/bd1/verify_packet.py /opt/agent-ee/build/k91-isolated-bd1 > build/k91-positive-c-packet-in-container.json; ...; python3 -B evidence/k91-native-positive-c/bd1/preflight.py /opt/agent-ee/build/k91-isolated-bd1 /opt/agent-ee/build/k91-positive-c-preflight ...'
```
Container image: `agent-ee:exec-758b370fecf753c0`, id sha256:e977faff65f2f0bb8a6f080d0bcf2c173a7873f1892cd22b02ad49bc3a9eff39, ENTRYPOINT ["/usr/bin/tini","--"]. The tag was recomputed with ae-ci-exec's own input-hash code for this tree: 758b370fecf753c032a7329f23c47de070b3d4519d9bfceb25c081b8bea16d82. Output: `bd1/preflight.json` (`exact: true`, problems {}).

| Item | Expected | Selected (recomputed in container) |
|---|---|---|
| commit (runtime-L HEAD; manifest krt.commit) | bd1e633f2e585f4fc931ceab001299b78d0d8bb7 | bd1e633f2e585f4fc931ceab001299b78d0d8bb7 |
| engine source (L, and frozen-router) | cb003b30f75f7b97859dcaaae6097ff1cbdeca00e7e38c2b1ab4b4a11e4790c2 | same, both |
| extension grid_router.so | 610b3acdfa0b5a2ee95fedd2a3526ff23b457c115c2f93bbac9fedf2ffcd99d8 | same (file and manifest) |
| venv packages | 0dc7c913210ed2d7f0afe996b4f936eaee539c19ca5e6e5d76949d13eaf304ae | same |
| Python, pyvenv.cfg | 3.12.3 | 3.12.3 |
| Python, actual container interpreter | 3.12.3 | 3.12.3. `runtime-L/.venv-krt/bin/python -I -c 'import grid_router'` OK: SOABI cpython-312-x86_64-linux-gnu, module …/runtime-L/.venv-krt/lib/python3.12/site-packages/grid_router.so (**the extension loads; ABI OK**). The interpreter resolves to /usr/bin/python3.12 |
| KiCad (`kicad-cli version`) | 10.0.7 | 10.0.7 |
| producer experiment.py | 2fe4ed96615e0b8a5e321b647fd71b0a54408a941d3e72bcca1c1887a521ef23 | 2fe4ed96615e0b8a5e321b647fd71b0a54408a941d3e72bcca1c1887a521ef23 |
| `kicad-route run --help` has --experiment-policy | exit 0, true | exit 0, true |
| manifest sha256 / state_dir / generation / drift | 33cd344e…3212 / /opt/kicad-route / null / [] | same |
| harness runtime mismatch (v1.identities + v2.runtime_mismatch, the harness's own check) | {} | {} |
| code map (bd1 blobs) | 313 entries = packet code-map.json; verify_code [] | same |
| production `routing_adapter.preflight(runtime-L)` under the bd1 pin | problems [] | [] |
| production binding (`consumer.binding`: validate_declaration, then declaration_spec, then validate_campaign_step with preflight) | accepted | accepted |
| step mount at /opt/kicad-route (read-only: every step mount is --ro-bind-fd, executor extra_ro) | runtime-L only | /opt/agent-ee/build/k91-isolated-bd1/runtime-L |
| step adapter mount at /opt/agent-ee-routing/kicad_route_attempt.py | the pinned copy | the pinned copy |
| input tree (v1.tree_record of the fixtures/rc design copy) | 0fdad94e4d260be007b438926d80e7d7ad087a4792b93aae3a0ab4a38d7d87b9 | same |
| input board rc_filter.kicad_pcb | f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de | same |
| policy rule_areas_sha256 / canonical policy sha256 | 4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945 / eac2b8ec630bd62a5c6356ea323285a9dc4488cfa7e084009fa03cd3a25c8056 | same |
| mismatch M differs (commit and extension) | true | true |

Notes on the preflight inputs and scope:
- The fixed input values come from the sealed v1 record `evidence/k91-native-positive/evidence-manifest.json` (receipt 9352b0f1's attempt: positive.design_before, sidecar identity.input.board.sha256, and policy.declared). I recomputed them from the current fixture; they were not guessed.
- **Mismatch control M, captured before use** (preflight `mismatch_M`): /opt/kicad-route, the image's own runtime.
  - commit b5774496d8466b67ffa0362b6665fee2108155a3, source 9cfc5d5e9369fd044ac6555afe57bdc4ccc829421e5496d42599c2d4a71951b2, extension 03806afa07ad635cf4c4416628c7edde1bc67c308992acec837c939f0a1b85a1.
  - packages 0dc7c913…, Python 3.12.3, drift [], generation null, manifest 34a74cd0eb7b26b9c793a8d86094d1240586ae55dd7af627bebee0de6b9fd8d0.
  - It is not runtime-L, and nothing was relabelled.
- **Binary provenance, kept separate** (packet runtime-disposition.json): the 610b extension's original binary generation is 6f67a866…, built from source 9cfc5d5e… at commit 53a07c02. The packet's binary-origin/ is copied unchanged. Neither I nor the receipt calls it bd1's build. The receipt's `tool.backend.value.build` is "owned-cb003b30f75f", the router's own report for the executed source.
- **Accepted scope unchanged.** Against efb77f64, routing_adapter.py and kicad_route_attempt.py are unchanged. stage_receipts.py differs only by main's AGENTEE-458 `skip_visuals` plumbing (commit 1e13414e, merged on the 73d0aa31 stack); the staging, ancestry and seal code is untouched. The v2 scope (classifier, consumer, harness) is byte-identical to d2a25162.
- I ran no broad suite.

## Selected consumer, source, runtime and full invocation
- Consumer: agent-ee 1e9255632668ebfa97ca2c0cbd1105c9d1fe5b6a, recorded as `agent_ee_head` in the manifest.
- Harness hashes:
  - harness k91_native_positive_v2.py: d6b2cadd9bae70a1bfb21688663662e037c0b035fe2e21cf83bd67d5063fce3c;
  - classifier k91_classify.py (verdict-2r4): 55563ea17c7e4ba725219717904a95beb0a5814676558f5690c0500f102dd706;
  - consumer k91_consumer.py: 5bc18b6e305d25dcad284c5fa64839960cc1a2cb8cc421df82c7eb073470b5d0;
  - base v1 helper k91_native_positive.py: 97d6055d148ef40ae110a09ab6c906f28d0ceea65afc4949f44ca531242e600f, imported and unchanged.
- Production adapter a1704d4d…, pinned copy 214696fb…. The production pin stays b5774496.
- Runtime: runtime-L = bd1e633f / cb003b30 / 610b3acd / packages 0dc7c913 / Python 3.12.3 / KiCad 10.0.7, inside image e977faff….
- Full invocation (proximal; `AE_CI_WEIGHT=4`, weight-4 admission lease 640e4eebdb32):
```
AE_CI_WEIGHT=4 AGENT_EE_SKIP_VISUALS=1 ~/.local/bin/ae-ci-exec . -- sh -c 'date -u +admitted=%FT%TZ >> build/k91-positive-c-attempt.times; python3 -B scripts/k91_native_positive_v2.py --checkout /opt/agent-ee/build/k91-isolated-bd1/runtime-L --frozen-source /opt/agent-ee/build/k91-isolated-bd1/frozen-router --mismatch /opt/kicad-route --out /opt/agent-ee/build/k91-positive-c-out --commit bd1e633f2e585f4fc931ceab001299b78d0d8bb7 --source-sha256 cb003b30f75f7b97859dcaaae6097ff1cbdeca00e7e38c2b1ab4b4a11e4790c2 --extension-sha256 610b3acdfa0b5a2ee95fedd2a3526ff23b457c115c2f93bbac9fedf2ffcd99d8 --packages-sha256 0dc7c913210ed2d7f0afe996b4f936eaee539c19ca5e6e5d76949d13eaf304ae --python-version 3.12.3 --input-tree-sha256 0fdad94e4d260be007b438926d80e7d7ad087a4792b93aae3a0ab4a38d7d87b9 --board-sha256 f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de --rule-areas-sha256 4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945 --router-limit-s 900 --total-limit-s 2400 > build/k91-positive-c-attempt.stdout 2> build/k91-positive-c-attempt.stderr; rc=$?; date -u +finished=%FT%TZ >> build/k91-positive-c-attempt.times; printf "\nharness-exit=%s\n" $rc'
# harness-exit=1   outer-exit=0
```
Budget 300 s (the default) < router limit 900 s <= total 2400 s. The harness stderr (attempt.stderr) was empty.

## Times
| | value |
|---|---|
| Queued (host) | 2026-10-23T01:27:19Z |
| Admitted (in container) | 2026-10-23T01:27:25Z (queue wait about 6 s; native-admit: "admitted after 5s") |
| Finished (in container) | 2026-10-23T01:28:07Z |
| Admitted wall | about 42 s; harness `wall_s` 42.3 (limit 2400) |
| Router step (positive receipt duration_s) | 25.542 s (limit 900) |
| Control (a) low-level direct run_step (router not started: in-sandbox adapter not_run) | 3.118 s |

The preflight was a separate admitted run: queued 01:24:01Z, admitted 01:26:42Z, finished 01:26:47Z.

## Attempt records and hashes (sha256, printed by sha256sum)
| File | sha256 |
|---|---|
| positive receipt.json (receipt_id 6458705d-1932-4d07-9571-d49c5c5eed32; exit 0, completed) | e78941b30b2c1f305cfff7ae4fbc9cc526f1172f6a7461f0bfc5295970adccb0 |
| run/routing/adapter-report.json | 07634700a4e7727244d3d98c809b56223608bfc007ce0dcc9fc60f8c35b4ff0e |
| run/routing/attempt/result.json | aea27925a47c99c51cb00e800b8752c338a73cc3ab8a75b159570b03dd771d49 |
| run/routing/attempt/routability-experiment.json (sidecar) | 0fafa49a16058ef45dabb3bb474cd5de07858286c35e661096297cc9cda7ec19 |
| evidence-manifest.json | 8acd9dff0c2ccfc3f860c8d28333b581fdbe6e357ab8a543355aac062d12ce53 |
| binding.json | 46af70bcac20e3b2d7dab3fac94623cea749d553a511192ebc8370f2b8d80c83 |
| delivered and adopted disposable board positive/design/project/rc_filter.kicad_pcb | a46c5dca9f102c513f97e41627ffcb13228bc9302d8a2fa203ce0de41c5e5232 |

What the positive verified:
- Adapter: classification complete, proposal eligible, coverage routing_complete.
  - **R1 scope qualifiers (agent-510, verbatim):**
    - The RC fixture was ALREADY CONNECTED. baseline_unconnected_total=0 and unconnected_total=0; new_tracks, new_vias and new_track_length_mm are all 0; backend successful, failed and total_iterations are all 0; gained, lost and worsened are empty; terminal metrics are 3 nets, 0 incomplete, 0 deficit; native DRC 0 violations, parity and preservation PASS.
    - So the positive proves real run_step → verify → adopt PLUMBING on the bd1 runtime. It is NOT evidence of routing work or algorithmic improvement. Changed board/tree hashes must not be read as newly routed copper.
    - Bare stage_receipts.adopt ACCEPTS a sidecar-tampered result, while the full consumer refuses it. That is the known AGENTEE-465 boundary and must be retained as a stated limitation.
    - Control A stays INCOMPLETE.
- Experiment: `status: retained`, `qualified: false`, schema routability-experiment/1. The sidecar is comparison_eligible true with ineligible_reasons [].
- policy_qualification: actual trace layers [F.Cu], via spans [[F.Cu, B.Cu]], coverage board-and-footprint.
- Declared policy sha eac2b8ec…; producer policy_sha256 3899a9ee….
- The receipt's backend execution identity is extension 610b3acd…, source cb003b30…, packages 0dc7c913…, commit bd1e633f…, grid_router 0.23.1.
- Design tree before 511739fb… and after 1e96471a… (the disposable positive/design only).

## v2 layer verdicts (classifier agent-ee.k91-native-positive/verdict-2r4, unchanged)
| Run | consumer | consumer_run_step | consumer_verify | consumer_adopt | direct (bare) adopt | design unchanged | v2 state | low_level |
|---|---|---|---|---|---|---|---|---|
| **positive** | accepted | executed, accepted (6458705d) | accepted (complete/eligible; experiment retained, qualified false) | **accepted (adopted into the disposable copy)** | n/a | changed (adopted) | **positive_accepted: true** | — |
| (a) runtime mismatch M | refused: `ValueError: kicad-route does not qualify: checkout HEAD b5774496… is not the pinned bd1e633f…; …; tool file differs from pin: …; pinned file missing: src/kicad_route/experiment.py; …` (origin routing_adapter.py:validate_campaign_step) | not called | not called | not called | not attempted (no receipt retained) | yes | **incomplete** | refused (direct run_step: sandbox not_run; verify not_run; bare adopt refused "stage step … failed") |
| (b) candidate-board bytes (offset 9859; a46c5dca… to 0c7ba421…) | refused: `stage receipt outputs changed` (stage_receipts.py:validate_receipt) | replayed, accepted | refused | not called | refused | yes | **refused** | refused |
| (b') sidecar bytes, separate (offset 9458; 0fafa49a… to e5e5d7be…) | refused: `retained routing artifacts differ from the adapter report` (routing_adapter.py:_verify) | replayed, accepted | refused | not called | **ACCEPTED** (bare `stage_receipts.adopt` on its own disposable direct-design copy) | yes | **refused** | incomplete (bare adopt accepted) |
| (c) stale input (board f8264d95… to b03a501b…) | refused: `routing receipt chain input differs from preceding adopted output` | replayed, accepted | not called | not called | refused ("design changed during stage step") | yes | **refused** | refused (low-level verify: "routing attempt inputs differ from the captured pre-step candidate") |

Verdict: `{"positive_accepted": true, "controls_refused": false, "required": [a, b_tampered_candidate, c]}`. Harness exit 1.

**Control (a), why it is INCOMPLETE and not refused.** CONTRACTS['a_mismatched_runtime'].reason is `kicad-route does not qualify: (tool file differs from pin|owned engine source digest differs)`. `_is_refusal` applies it with `re.match`, which is anchored at the message start. That pattern was written for a same-commit, changed-code mismatch.
- The dispatched M (the image's b5774496 runtime) has a different commit, so the production refusal message lists `checkout HEAD … is not the pinned …` first. "tool file differs from pin" follows later in the same message.
- The origin (routing_adapter.py, validate_campaign_step), the ValueError type, the layers (all three not called), design_unchanged and executed all hold.
- `host_binding_refused` fails for the same reason: same message, same pattern.
- The refusal is real and observed. The verdict nonetheless stays INCOMPLETE, because the rules forbid relaxing the classifier and forbid a retry.
- For the coordinator: either the contract admits the commit-mismatch reason, or the matrix uses a same-commit M (as the historical edited-source control did). Both are decisions for the existing review, not for this worker.

**Bare-adopt observation, kept visible.** `stage_receipts.adopt` alone does not check the routing sidecar. It adopted the sidecar-tampered replay into a disposable direct-design copy. The production consumer refused it at verify_campaign_receipt, and the consumer's own design was unchanged.

## Preservation (sha256sum -c, after the attempt)
- `evidence/k91-native-positive/`: `sha256sum -c --quiet SHA256SUMS`, v1-sums=0 (receipt 9352b0f1 untouched).
- `scripts/k91_native_positive.py`: `git diff --quiet 553e4151 HEAD`, 0.
- positive-c NOT-RUN evidence (`evidence/k91-native-positive-c/SHA256SUMS`): notrun-sums=0.
- agent-507's original packet: read only (cp -a source, walked for comparison).
- No production pin or install, no production board adoption, no K96, no GCP, no extra campaign, no retry.

## Retained evidence (committed)
`evidence/k91-native-positive-c/bd1/` contains:
- the scripts verify_packet.py and preflight.py;
- packet-verification{,-in-container,-post-attempt}.json and preflight.json;
- the attempt records: attempt.stdout/stderr, attempt-times-{host,in-container}.txt, attempt-admission.log, preflight-admission.log, preflight.stderr;
- out/: the 35 JSON records of OUT (evidence manifest, binding, receipts, adapter reports, router result, sidecar, policy copies, attempt.json) plus the pinned adapter copy;
- out-all-files.sha256: a hash of every one of the 238 OUT files;
- SHA256SUMS, 50 entries, `sha256sum -c` OK.

The full OUT, including design copies and router work, stays in the worktree's gitignored `build/k91-positive-c-out/` for the reviewer, together with `build/k91-isolated-bd1/`. The docs/file-origins.json rule note for `evidence/k91-native-positive-c/` is updated. Host check_origins and check_docs both report errors [].

## Plain statement
**INCOMPLETE.**
- The native K91 positive was **ACCEPTED**: it was verified and adopted through the production consumer into the disposable RC copy, with routability-experiment/1 evidence retained, unqualified and comparison-eligible, on the exact bd1e633f / cb003b30 / 610b3acd / 0dc7c913 / Python 3.12.3 / KiCad 10.0.7 runtime.
- Controls (b) candidate-board, (c) stale input and the separate sidecar-bytes control were **REFUSED** by contract.
- Control (a) runtime mismatch was refused by the production callable, but is **INCOMPLETE** under the unchanged v2 contract (reason-pattern mismatch).
- I made no retry.
