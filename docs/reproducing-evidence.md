# Restoring and reproducing evidence

Restoring exact recorded bytes, checking record consistency, and repeating an
engineering experiment are different operations. The first two do not rerun
tools or model judgments. A new review performs new execution and may produce
different findings/prose while satisfying the same interpretation contract.

## Recorded acceptance

The [acceptance index](../evidence/README.md) maps all ten required demonstrations
to actual records. [evaluation-plan.md](evaluation-plan.md) was committed at
`d3f9222` before implementation. It independently defines the passive control,
functional seed, geometry limit and expected execution/freshness behavior.

| Item | Expected / observed |
| --- | --- |
| Control | Native R1=10k, C1=100n; ideal unloaded 1 V step; 1 ms response within 0.62–0.65 V; actual measured about 0.6321201 V; completed review satisfies criteria |
| Seed | Saved R1=100k in schematic and board; ERC/DRC remain clean; measured about 0.0951626 V violates original response requirement; actual tools and agents detect it |
| PCB requirement | Saved straight track width at least 0.20 mm; actual five eligible segments are 0.25 mm; native geometry/connectivity and source locations retained |
| Repair | One disposable native value repair to 10k; fresh full review and measurements pass unchanged limits; closure links native and agent findings |
| Freshness | Temporary saved-value change to 22k makes the prior passing result refuse verification; exact restoration only restores the original candidate |
| Failure/resource cases | Malformed actual reviewer response and interrupted actual seed runs remain incomplete; deterministic cases supplement unavailable/failed/unsupported/malformed/budget handling |
| Donor | Available Universal 112 baseline reports actual defects/unknowns; original hierarchy issue remains partial; explicit disposable alias restores complete traversal without changing source |

One seed was detected and no seed was missed in the completed run. Control
observations remain non-defect notes or duplicates. Earlier rejected/interrupted
attempts are preserved as process failures, not erased or counted as completed
engineering misses. This is a small capability demonstration, not a general
hardware correctness benchmark.

## Restore a fresh checkout

From the checkout, run:

```sh
python3 scripts/evidence_archive.py restore rc-verified
python3 scripts/evidence_archive.py restore donor-verified
```

The helper validates the archive checksum, member paths/types, destination links
and existing files before extracting missing files. Identical existing files
are left alone; changed ones cause refusal. It does not create live links or
overwrite modified evidence. Archive checksums, lengths, implementation refs
and recorded candidate/run outcomes are in
[archives/manifest.json](../evidence/archives/manifest.json).

Expanded inputs, generated native configuration and caches are excluded from
ordinary Git tracking to avoid duplicating large library trees and transient
files. Their exact bytes are in these versioned archives. Readable check/log/
review records remain directly visible in the repository. Do not assume that a
fresh clone without restoration contains a complete hash-verifiable run tree.

The original final records use Python policy from commit `30b2256`; later documentation
commits preserve those module bytes. In the demonstrated runtime, these commands
check retained candidate/evidence consistency without consulting the old source
locations:

```sh
./bin/agent-ee verify evidence/rc-verified/control --snapshot-only
./bin/agent-ee verify evidence/rc-verified/seed --snapshot-only
./bin/agent-ee verify evidence/rc-verified/repair --snapshot-only
./bin/agent-ee verify evidence/donor-verified --snapshot-only
```

They also require matching current runtime fingerprints, including resolved
executable paths. A different machine/tool upgrade can cause a legitimate
runtime refusal despite an intact archive. This repository does not distribute
a hermetic historical OS image. In that case inspect the records/checksums as
historical evidence, or reproduce the scope with current tools in a new run.
Do not alter expected runtime hashes to claim historical acceptance.

The [fresh-checkout experiment](../evidence/verification/fresh-checkout-replay.json)
restored both final archives into a clean detached Git worktree and verified all
four final candidate records. The [source/status check](../evidence/verification/final-freshness-and-source-status.json)
also verified normal freshness at the original source locations and confirmed
the donor/library checkouts were clean.

## Repeat the live control, defect and repair

With native prerequisites, stock resources at the fixture's declared paths and
working subscription authentication:

```sh
python3 scripts/demo.py --profile profiles/codex.json --out build/repeated-demo --seconds-per-run 1200
```

The output directory must be new. The script records the evaluation-plan hash,
runs doctor, then performs actual control and seed reviews. It only attempts
repair when the control satisfies criteria and the native transient defect has
an accepted canonical finding. It copies the seed into `disposable/`, changes
exactly one saved Value property in each native schematic/board file, retains
`repair.patch` and `repair.json`, and reviews that new candidate.

If the repair satisfies criteria, the script links each canonical accepted
native response finding to the new review and records `observed.json`. It does
not automatically close separately accepted agent findings describing the same
mechanism; inspect and link those explicitly when warranted. The recorded final
demonstration includes that additional agent-finding closure.

There are no automatic retries, provider fallbacks or repair sweeps. Unmet
prerequisites/failures remain on disk and the script exits unsuccessfully. The
fixture's native source is never repaired in place. Fresh model reviews can
raise new questions; investigate them against declared requirements rather than
editing the limits to force the expected result.

## Repeat the freshness mutation

This helper requires a genuinely passing repair and explicitly limits its
temporary source changes to the supplied disposable directory:

```sh
python3 scripts/freshness_demo.py build/repeated-demo/repair \
  --disposable-root build/repeated-demo/disposable \
  --from-value 10k --to-value 22k --out build/freshness-observation.json
```

It verifies the baseline, changes the unique saved Value field in both native
files, requires old verification to refuse with exit 2, restores original bytes
in a `finally` block, and verifies the original candidate again. It records
before/changed/restored outcomes and input hashes. A hard kill during mutation
can bypass ordinary cleanup; the files are disposable, and their original
hashes/captured copies remain available. Never run this on a real project path.

## Donor baseline and historical attempts

The [baseline analysis](../evidence/BASELINE.md) explains the source/library
revisions, filename discrepancy, case-alias input, native results, DNP population
and unknown module/harness behavior. `scopes/donor.json` and
`scopes/donor-case-alias.json` are workstation-specific executable scopes. Resolve
their original source paths locally before a new baseline review; do not mistake
restored captured inputs for current live source acceptance.

| Evidence group | Python policy ref | Interpretation |
| --- | --- | --- |
| `rc-demonstration` | `6efecc7` | Malformed-role control and interrupted seed |
| `donor-baseline` | `6efecc7` | Required packet artifact/configuration failure |
| `donor-baseline-v2` | `6efecc7` | Interrupted iteration before via-query correction |
| `rc-final` | `2a0e7f0` | Earlier control incorrectly blocked by non-defect concern acceptance; interrupted actual seed |
| `donor-final` | `2a0e7f0` | Original as-received hierarchy remains incomplete |
| `rc-verified` | `30b2256` | Final control, seed and verified repair |
| `donor-verified` | `30b2256` | Complete explicit-alias baseline with rule failures/open questions |

Each historical group can be restored by its exact name. Inspect it with the
matching source ref in a separate worktree when policy verification is needed;
tool/runtime matching is still required. The current CLI intentionally refuses
older policy evidence as current acceptance. The [via correction](via-probe-correction.md)
and [disposition decision](decisions.md) explain changes motivated by these
iterations without erasing their outcomes.

The recorded test, interruption, archive round-trip, malformed-review, policy
refusal and live freshness evidence is linked from the acceptance index.
Replaying deterministic tests supplements, and does not replace, the actual
engineering-tool and subscription-review demonstrations.

## Subscription profile evaluation archives

The [follow-up index](../evidence/MODEL-SELECTION.md) maps each new group to its
implementation commit. `model-release` uses `5d01de9`. To check the archived
release independently of newer code:

```sh
python3 scripts/evidence_archive.py restore model-release
git worktree add --detach build/replay-release 5d01de9
PYTHONPATH=build/replay-release/src python3 -m agent_ee.cli verify \
  evidence/model-release/repair-demo/control --snapshot-only
PYTHONPATH=build/replay-release/src python3 -m agent_ee.cli verify \
  evidence/model-release/baseline --snapshot-only
```

The worktree supplies matching policy code; archive restoration supplies recorded
inputs. Current machine runtime fingerprints must also match. The documented
[separate-worktree replay](../evidence/model-validation/fresh-checkout.json)
verified all five release results. The [freshness mutation](../evidence/model-validation/freshness-mutation.json)
changed both native files on the disposable repair and confirmed refusal, then
restored their original bytes and verified again.

To produce and grade a new comparison:

```sh
python3 scripts/compare_reviewers.py --out build/comparison-new
python3 scripts/comparison_summary.py build/comparison-new \
  --grades /path/to/manually-reviewed-grades.json --out build/comparison-summary.json
python3 scripts/compare_adjudicators.py --candidates build/comparison-new \
  --out build/adjudicators-new
```

Do not reuse the recorded grade ledger for different review bytes. It deliberately
rejects stale hashes. Without `--grades`, the summary reports execution and
findings as ungraded. All comparison reviews remain incomplete as acceptance
runs because they intentionally omit adjudication. The score report separately
identifies execution failures, unrun cases and engineering overclaims.

## Reproduce scope/2 verification

These commands use the current worktree and require the native environment. They
create fresh local evidence and do not restore old acceptance:

```sh
./bin/agent-ee review fixtures/closed-loop/control.scope.json \
  --checks-only --out build/scope2-native --max-seconds 120
./bin/agent-ee verify build/scope2-native
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
```

The first command intentionally exits 1 because checks-only omits adversarial
review. Run the subsequent verification even though a shell `&&` chain would
stop at that exit. On a compatible native installation the control's declared
checks pass/info, while the full review outcome remains incomplete. Verification
exit 0 confirms fresh evidence, not completion of missing agent judgments.

For an explicit live campaign, use:

```sh
./bin/agent-ee campaign fixtures/closed-loop/campaign.json --out build/scope2-live
./bin/agent-ee verify-campaign build/scope2-live
```

This configuration selects an actual subscription profile and spends inference.
A new model response can fail schema/citation validation, so do not assume a
passing control guarantees a completed campaign. The [recorded scope/2 live attempt](closed-loop.md#live-subscription-failure-path-demonstration)
stopped on an invalid adjudicator citation before implementation. Complete repair
and empirical continuation are exercised in integration tests with scripted model
replies. The synthetic samples in those tests are not instrument measurements.

The new closed-loop local build runs are not entries in the historical archive
manifest. Archive complete runs only after committing their exact implementation
and verifying the matching policy/runtime. Campaign history and feedback contain
absolute run/bundle/scope-source paths; the existing archive helper does not
relocate these links or provide portable whole-campaign restoration. Preserve the
full campaign plus referenced feedback and source trees at their recorded paths,
or inspect relocated copies as historical records without claiming equivalent
verification.

## Evidence relocation preparation (AGENTEE-20)

Relocation is prepared locally; retained evidence remains in this checkout.
The owner has not created a destination store or approved third-party publication.
No upload, evidence removal or history rewrite has occurred. The working tree
therefore still exceeds the requested 20 MB limit. Even after a future removal
commit, historical Git objects keep clone size large until a separately approved
history rewrite (or another owner-chosen distribution strategy).

[files-manifest.json](../evidence/files-manifest.json) is the immutable Git-recorded
per-file inventory of the existing 20 evidence directories: 4,857 files,
325,214,213 bytes. Each exact group-relative path has its byte length and SHA-256.
This includes the existing archives and their manifest as ordinary files; it
neither expands those archives nor invents missing captured inputs. File modes,
timestamps and empty directories are outside this byte inventory. Top-level
human summaries and the new relocation metadata remain in main. Regenerating a
manifest requires a new output path; the generator refuses to overwrite one:

```sh
python3 scripts/evidence_fetch.py manifest --evidence evidence --out build/new-files-manifest.json
```

[verify-baseline-20261017.json](../evidence/verify-baseline-20261017.json) records
actual current-source and snapshot-only CLI verification for every existing
retained `agent-ee.result/1` run, with exact commands, exits and diagnostics.
All 73 runs currently exit 2 in both modes with `candidate snapshot changed;
evidence is stale`: expanded inputs are absent. Independent diagnostics also
record all 73 policy mismatches and one changed source scope; selected source
file bytes match where checked. These diagnostics do not bypass the first CLI
refusal or establish runtime compatibility. No evidence was changed to force a pass. Older archived inputs may require the archive
restore step above, and historical acceptance still requires matching policy and
runtime. The baseline is a relocation comparison receipt, not fresh acceptance.
The [local fetch receipt](../evidence/fetch-local-check-20261017.json) records an
all-group `file:` round trip: every path/size/hash matched, and all 73 runs
produced identical exits/stdout/stderr in both modes. It does not validate a remote
store or restore expanded inputs. Record a new baseline when the integrator
changes policy or environment:

```sh
/usr/bin/python3 scripts/evidence_verify_baseline.py --out build/new-verify-baseline.json
```

After the owner creates and approves a store, an authorized operator must upload
exact group/path bytes and independently check them against the committed
manifest. The fetch command requires an explicit base URL and an existing output
parent. Set `EVIDENCE_STORE_URL` to that real approved store; no store is configured
or implied by this documentation:

```sh
mkdir -p build/fetched-evidence
python3 scripts/evidence_fetch.py fetch rc-verified \
  --manifest evidence/files-manifest.json --base-url "$EVIDENCE_STORE_URL" \
  --destination build/fetched-evidence
```

The store layout is `BASE_URL/GROUP/EXACT_RELATIVE_FILE_PATH`; URL components are
encoded without interpreting filenames as URL instructions. HTTPS, HTTP and local
`file:` URLs are supported. HTTP and file fixtures exercise the machinery locally;
no real remote store has been validated. Keep the manifest in a trusted checkout:
its hashes establish identity relative to that manifest, not publisher authenticity
or permission to publish. This explicit outer tool uses network access outside
agent/tool sandboxes; sandbox network policy is unchanged.

On Linux with `/proc` and libc `renameat2`, fetch stages the whole named group in
a private temporary directory beside the destination. Each streamed file is size
bounded and hash checked before a single no-replace directory rename publishes
the group. Missing, truncated, oversized or corrupted files leave no final group.
Unsafe paths, file/directory conflicts and destination ancestor symlinks are
refused. Every existing destination (including an empty directory, an identical
prior fetch or a dangling link) is a conflict; choose a fresh output parent.
A concurrent creator also causes refusal without replacing its directory.
Unsupported publication primitives fail rather than using an incremental fallback.
A killed process can leave hidden `.evidence-fetch-*` staging directories. They
are never complete groups. This is atomic visibility, not a power-loss durability
or hostile-host guarantee; the owner must protect the destination parent from
other processes running with the same privileges.

Before removing any tracked evidence, fetch every approved group into a clean
location, independently compare every path/size/hash and record verification with
the same policy/runtime. Compare failures as well as successes with the baseline;
absolute source/campaign paths and unavailable inputs can prevent portable replay.
Do not repair those records or reinterpret a mismatch as a pass. Keep the readable
summaries and update their links only when the actual approved relocation is known.
Store creation, publication rights, upload, real post-fetch comparison, removal,
the under-20-MB criterion and owner review remain outstanding.
