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
