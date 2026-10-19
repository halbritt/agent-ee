# FOME block host review attempts (AGENTEE-149)

Both 2026-10-18 host attempts ended `incomplete`. The standard circuit and
physical reviewers each returned a response, but all four responses failed the
existing citation validator with `citation pointer requires a valid JSON document`.
Neither run reached centralized adjudication. No malformed response earns review
coverage, and neither block can leave `unverified` on this evidence.
All four responses included `pointer: ""` on native `.kicad_sch` text, which
cannot be parsed as JSON. The receipts' `citation-refusals.json` records the
existing validator's per-citation errors without copying document text.

## Captured identities

The source was the assigned worktree at `a3b5fe78a02ceead0a94918ddb76cbc2a0e13c53`.
Both calls used the unchanged `profiles/codex-baseline.json`: Codex subscription,
requested model `gpt-6.1-sol`, high effort, 900 seconds per role and a 3600-second
run budget. Codex JSONL does not attest the serving model identity.
The harness isolated initial reviewers and sealed coordinator check evidence.
There was no implementation writer, prompt edit, verdict edit, model substitution,
inference replay or manual adjudication.

| Block | Run | Reviewed baseline | Candidate |
| --- | --- | --- | --- |
| wbo1 | `0f41455d-4252-48d9-b43d-e79dfa3df22d` | `75f7d6eccce9627f0ce521306673149f0583b82b3e2ada3f4a949126ef068177` | `46310254a330cbb063e68fcc16e7d081352143919b963630c5fc51c9aa5b09d0` |
| PSU | `877a075f-51d6-4cf2-a627-8ee5fb441305` | `985999e2ca757f18288362526bb83b16d54d049b1da312d9a1fceecc4cdb8c48` | `1d1a619470a38dc6646e7af098b799792d7236f34ba2aca4f40d899504c331b2` |

The ticket names historical baseline prefixes `93376f23` and `9641a875`.
Integration changed the committed records' origin fields, and the wideband scope
is now scope/3. This attempt reviewed the actual committed baselines above;
it supplies no run on either historical identity. The wideband reviewed baseline
matched the committed `expected-baseline.json` before the attempt. Adding its run
reference changes the live reference-architecture hash; the updated live fixture
baseline is `e5707d9ef29182d36d509b76a3082e34fdd949c6d20597746e02a9cf6da94690`.
The retained run baseline is immutable. Use snapshot verification after the ledger
update; current-source verification correctly reports changed inputs.

## Native observations

| Block | Executed checks | Result |
| --- | --- | --- |
| wbo1 | `erc`, `netlist`, `interface` | ERC: 834 warnings and 2 errors, all unwaived. Native export: 116 physical components in 3 sheet instances. Interface: pass. |
| PSU | All 11 declared checks | ERC and interface pass. Native export: 43 components in 3 sheet instances. Eight functional checks remain unsupported/unknown. |

The PSU unknown checks are `input-filter`, `tvs-clamp`, `reverse-block`,
`c12-part`, `c4-rating`, `brownout-floor`, `rail-regulation` and
`capacitor-ratings`. Unknown coverage supplies no electrical qualification.

Local pinned Git objects verify all four wbo1 and all ten PSU external citations.
PSU uses the mapped label `fome-mc=/home/halbritt/git/fome-mc`. Wideband retains
the legacy absolute identity `/home/halbritt/git/fome-mc`, which also serves as
its default location on this host. Its explicit mapping uses that absolute
identity as the key. No source working-tree file is read. The mapping cannot
cure missing reviewer/adjudicator coverage. A missing object store keeps either
block's citations unverified; PSU also requires its label mapping. CI has no
mapped/local store. The parked R5 origin question does not prevent these attempts
or grant electrical acceptance.

## Retained evidence

[Metadata receipts](../evidence/fome-block-review-runs/README.md) retain exact
candidate, baseline, coordinator result/check records, check seals and reviewer
execution records. Each `retention.json` inventories every raw file by SHA-256
and byte count and identifies the full host directory. These are partial
retention receipts; they cannot serve as run directories.

Full raw runs remain unchanged under the assigned scratch root:

```text
/tmp/agent-ee-burndown-20261017/workers/ee149-fome-block-review-runs/scratch/raw-runs/wbo1
/tmp/agent-ee-burndown-20261017/workers/ee149-fome-block-review-runs/scratch/raw-runs/psu
```

Both investigations retained third-party web text. The PSU reviewers also saved
vendor datasheets and text extractions. The assignment forbids committing vendor
text, so complete evidence cannot be committed under this authorization. No
archive or owner evidence store was created. No raw evidence was removed or
resealed to conceal this retention gap. The public verifier passed on each full
run before and after relocation; its exit 0 establishes integrity, while the
stored outcome remains `incomplete`.

Host replay, without model calls:

```sh
raw=/tmp/agent-ee-burndown-20261017/workers/ee149-fome-block-review-runs/scratch/raw-runs
PYTHONPATH=src python3 -m agent_ee.cli verify "$raw/wbo1" --snapshot-only
PYTHONPATH=src python3 -m agent_ee.cli verify "$raw/psu" --snapshot-only
PYTHONPATH=src python3 -m agent_ee.reference_blocks validate \
  fixtures/fome-mc/wbo1/blocks/wbo1 \
  --repository /home/halbritt/git/fome-mc=/home/halbritt/git/fome-mc \
  --run "0f41455d-4252-48d9-b43d-e79dfa3df22d=$raw/wbo1"
PYTHONPATH=src python3 -m agent_ee.reference_blocks validate \
  fixtures/fome-mc/psu/capture/psu \
  --repository fome-mc=/home/halbritt/git/fome-mc \
  --run "877a075f-51d6-4cf2-a627-8ee5fb441305=$raw/psu"
```

The block commands return 1: run status `unverified`, no credited requirements,
and overall `unverified`. No host evidence mapping yields run status `unavailable`.
The validator requires every listed run to pass. Appending a later passing run
while these incomplete references remain listed cannot qualify either record.

## Requirements left open

The coordinator's native `check/erc` finding remains established check evidence
for wbo1, without an adjudicated disposition. Restore acceptable external power
conditions and obtain a fresh passing ERC result without introducing blanket
waivers. The existing active-model, parts and geometry requirements remain open.

The rejected responses also suggest investigations. These are unvalidated
hypotheses, never accepted defects: wbo1 analog/digital supply sequencing; PSU
feedback tolerance versus rail accuracy, D1 reverse-battery current, C12 selection,
U3 pin-6 identity, missing falling UVLO, C6 procurement identity, sensor reference
accuracy and VBOOST transient peaks. Reproduce each against the pinned native
candidate with an applicable captured check before accepting a defect. Existing
PSU pending requirements cover most of these subjects; pin-6 identity and
transient peak stress need explicit coverage when the block is repaired.

Completion still needs valid independent reviewer responses and adjudication,
authorized complete evidence retention, and any engineering repairs required by
those results. This item changes no design payload or acceptance criterion.
