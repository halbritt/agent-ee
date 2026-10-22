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

Most retained evidence bytes, including every archive, now live in the owner's
private evidence store ([relocation](#evidence-relocation-agentee-20)). With the
credentials file in place, fetch the archives and the recorded groups into a new
directory and restore there:

```sh
mkdir -p build/fetched-evidence
for group in archives rc-verified donor-verified; do
  python3 scripts/evidence_fetch.py fetch "$group" \
    --base-url s3://agent-ee-evidence --destination build/fetched-evidence
done
python3 scripts/evidence_archive.py restore rc-verified --evidence build/fetched-evidence
python3 scripts/evidence_archive.py restore donor-verified --evidence build/fetched-evidence
```

Without `--evidence`, restore reads this checkout's `evidence/archives/` and
refuses with exit 3 (`refused (archive relocated)`) because the archive bytes are
no longer tracked. The helper validates the archive checksum, member paths/types, destination links
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
locations. Run them on the groups fetched and restored above:

```sh
./bin/agent-ee verify build/fetched-evidence/rc-verified/control --snapshot-only
./bin/agent-ee verify build/fetched-evidence/rc-verified/seed --snapshot-only
./bin/agent-ee verify build/fetched-evidence/rc-verified/repair --snapshot-only
./bin/agent-ee verify build/fetched-evidence/donor-verified --snapshot-only
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
mkdir -p build/fetched-release
for group in archives model-release; do
  python3 scripts/evidence_fetch.py fetch "$group" \
    --base-url s3://agent-ee-evidence --destination build/fetched-release
done
python3 scripts/evidence_archive.py restore model-release --evidence build/fetched-release
git worktree add --detach build/replay-release 5d01de9
PYTHONPATH=build/replay-release/src python3 -m agent_ee.cli verify \
  build/fetched-release/model-release/repair-demo/control --snapshot-only
PYTHONPATH=build/replay-release/src python3 -m agent_ee.cli verify \
  build/fetched-release/model-release/baseline --snapshot-only
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

## Evidence relocation (AGENTEE-20)

### Destination

On 2026-10-22 the owner approved a private bucket on the owner's own Garage S3
service on the host `proximal` (single node, LUKS-backed storage). There is no
third-party publication and no Git history rewrite. The bucket is
`agent-ee-evidence`. It allows no anonymous or website access. Objects use the
layout `GROUP/EXACT_RELATIVE_FILE_PATH`. Every file of all 20 groups in
[files-manifest.json](../evidence/files-manifest.json) is in the bucket: 4,857
objects, 325,214,213 bytes. Fetches use a read-only access key. Uploads use a
separate key that the owner holds. The S3 API listens on `127.0.0.1:3900` on
proximal. Hosts on the owner's tailnet reach it through a port 3900 forward on
proximal's tailnet address. It is not reachable from the public internet.
[relocation.json](../evidence/relocation.json) records the store, the relocated
groups and the files that stay in the tree.

### Credentials

The fetch tool reads `~/.config/agent-ee/evidence-s3.env`. The
`AGENT_EE_EVIDENCE_CREDENTIALS` variable or `--credentials FILE` selects another
file. The file is outside every checkout. It must be a regular file with mode
`0600` that holds `KEY=VALUE` lines:

```text
AWS_ACCESS_KEY_ID=<read-only key id from the owner>
AWS_SECRET_ACCESS_KEY=<its secret>
AWS_DEFAULT_REGION=garage
AGENT_EE_EVIDENCE_S3_BUCKET=agent-ee-evidence
AGENT_EE_EVIDENCE_S3_ENDPOINT=http://127.0.0.1:3900
```

On proximal the endpoint is the loopback origin shown. On another tailnet host,
set it to `http://<proximal tailnet address>:3900`, or pass `--endpoint ORIGIN`
for a single command. Ask the owner for the file. Never commit it or copy it into
a checkout, image or evidence directory.

### Fetch a group

```sh
mkdir -p build/fetched-evidence
python3 scripts/evidence_fetch.py fetch model-comparison \
  --base-url s3://agent-ee-evidence --destination build/fetched-evidence
```

`s3://` fetches sign each GET with AWS Signature Version 4, using only the
Python standard library. A missing, unreadable, malformed or group- or
other-accessible credentials file is a typed refusal: exit 3 and
`evidence_fetch: refused (store unavailable): ...`. Nothing is written in that
case. A missing or corrupt object fails the whole group, as described below.
Nothing falls back to a partial or unverified group, and no check treats absent
evidence as a pass.

The owner uploads with the separate key:

```sh
python3 scripts/evidence_fetch.py upload GROUP --evidence evidence \
  --base-url s3://agent-ee-evidence --credentials ~/.config/agent-ee/evidence-s3-upload.env
```

Upload never replaces an existing object, including one that another writer
creates between the existence check and the write. Every PUT therefore carries
`If-None-Match: *` (create only if absent) and the manifest SHA-256 as its
signed payload hash, so the store rejects a body that changed in transit. An
object that exists, or whose conditional PUT the store refuses with 412 or 409,
is accepted only when a GET returns exactly the manifest bytes; otherwise the
upload fails and the object is left as it is.

The precondition only protects evidence if the store enforces it. Before any
evidence PUT, upload writes one fresh random key under
`.agent-ee-create-probe/` twice with `If-None-Match: *`, then deletes that key.
If the second write is not refused, upload stops with exit 3 and
`evidence_fetch: refused (no atomic create-if-absent): ...`, without writing
any evidence. On 2026-10-22 the owner's Garage v2.3.0 ignored the precondition:
a repeated conditional PUT, and 8 concurrent conditional PUTs to each of 30
fresh probe keys, all returned 200 and the last write won. Upload to this store
therefore refuses. All 20 groups were already in the bucket before this check
was added. Adding evidence needs a store that enforces conditional writes, or an
owner-run upload while no other writer can be active, which this tool does not
offer.

### What stays in the tree

The tree keeps these files. Each is byte-identical to the manifest, and
`tests/test_evidence_relocation.py` checks this.

- Whole groups: `model-selection`, `model-validation` and `verification`. These
  are small human-readable records, about 157 KB together.
- `fome-block-review-runs`, which is newer than the manifest. Tests read it.
- In the other 17 groups, 38 files (4,182,003 bytes): the files that tests or
  fixture tools read, the files linked from README/docs or the top-level
  evidence summaries, and `archives/manifest.json`. The read files include the
  connectivity and DRC records cited by `tests/test_findings.py`, the cached
  upstream Rev C sheet used by `fixtures/fome-mc/wbo1/parity.py` and the netlist
  used by `tests/test_netlist_parity.py`.

The tree no longer tracks the other 4,753 files of those 17 groups. No test,
gate or fixture tool reads them. Thus the release gate and the workers need
neither the store nor the credentials to run the suite. Reports inside a
relocated group can link to sibling files; fetch the group to follow those links.

The file-origin check still classifies every relocated path. The paths come from
the hash-pinned manifest. The facts that the check reads from bytes (content
markers and the origins of captured candidate inputs) were recorded in
`relocation.json` from a hash-verified fetch. The record
(`agent-ee.relocated-origin-facts/2`) holds a digest of the marker patterns and
capture sources and the files manifest SHA-256. It has an explicit entry for
every file of the 17 groups: an empty marker list is a recorded negative. Every
`candidate.json` also has an entry, which is null when it has no sources table.
The check refuses, and reads nothing as clean, when any of these holds:

- a table or entry is missing, or names a file outside the groups;
- a value is malformed or uses an unknown marker or origin;
- the patterns, sources or manifest changed;
- a retained file's recorded facts disagree with its bytes.

These facts are trusted offline attestations from the recorded fetch, not a
fresh inspection of the absent bytes. A well-formed but false entry cannot be
detected without fetching the group. To record the facts again from a fresh,
hash-verified fetch:

```sh
python3 scripts/check_origins.py --relocated-facts build/fetched-evidence
```

### Verification and size

The [store fetch receipt](../evidence/fetch-garage-check-20261022.json) records
two round trips on proximal: all 20 groups fetched from the bucket over loopback
and over the tailnet forward. Every path, size and SHA-256 matched the manifest.
For all 73 retained `agent-ee.result/1` runs, `agent-ee verify` gave the same
exit, stdout, stderr and diagnostics in source and `--snapshot-only` modes as the
[pre-relocation baseline](../evidence/verify-baseline-20261022.json). All 73 runs
exit 2 both before and after relocation, because their expanded inputs are
absent and their policy is older. This is recorded, not repaired. The
[2026-10-17 baseline](../evidence/verify-baseline-20261017.json) and
[local fetch receipt](../evidence/fetch-local-check-20261017.json) record the
earlier preparation.

The tracked tree fell from 358.7 MB to about 38.9 MB. This does not meet the
20 MB criterion, because files outside `evidence/` (source, fixtures, docs and
tests) now total about 30.1 MB. Clone size does not fall: Git history keeps every
earlier evidence object until the owner decides on a separate history rewrite.

### Fetch mechanics

[files-manifest.json](../evidence/files-manifest.json) is the immutable Git-recorded
per-file inventory of the 20 groups in the store. Each exact group-relative
path has its byte length and SHA-256. It includes the archives and their manifest
as ordinary files. It neither expands those archives nor invents missing
captured inputs. File modes, timestamps and empty directories are outside this
byte inventory. Regenerating a manifest requires a new output path. The generator
refuses to overwrite one:

```sh
python3 scripts/evidence_fetch.py manifest --evidence evidence --out build/new-files-manifest.json
```

Record a new verify baseline when the integrator changes policy or environment:

```sh
/usr/bin/python3 scripts/evidence_verify_baseline.py --out build/new-verify-baseline.json
```

`https:`, `http:` and local `file:` base URLs remain supported without
credentials. URL components are encoded without interpreting filenames as URL
instructions. Keep the manifest in a trusted checkout. Its hashes establish
identity relative to that manifest, not publisher authenticity or permission to
publish. This explicit outer tool uses network access outside agent/tool
sandboxes. Sandbox network policy is unchanged.

On Linux with `/proc` and libc `renameat2`, fetch stages the whole named group in
a private temporary directory beside the destination. Each streamed file is size
bounded and hash checked before a single no-replace directory rename publishes
the group. Missing, truncated, oversized or corrupted files leave no final group.
Unsafe paths, file/directory conflicts and destination ancestor symlinks are
refused. Every existing destination (including an empty directory, an identical
prior fetch or a dangling link) is a conflict; choose a fresh output parent.
Because relocated groups keep some files in `evidence/`, fetch into a directory
such as `build/fetched-evidence`, not into `evidence/`.
A concurrent creator also causes refusal without replacing its directory.
Unsupported publication primitives fail rather than using an incremental fallback.
A killed process can leave hidden `.evidence-fetch-*` staging directories. They
are never complete groups. This is atomic visibility, not a power-loss durability
or hostile-host guarantee; the owner must protect the destination parent from
other processes running with the same privileges.
