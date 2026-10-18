# Reference block interface (AGENTEE-89)

`python -m agent_ee.reference_blocks capture REQUEST --repo LOCAL --commit COMMIT --out NEW_DIRECTORY`
captures explicit files from a local Git object tree, without checkout updates or
network access. Local Git replacement refs are ignored: the recorded commit
identifies the original committed blobs. Missing objects in a partial clone are
refused without contacting its promisor remote; supply a complete local object
tree before capture. Git transports are disabled during capture.
`python -m agent_ee.reference_blocks validate DIRECTORY` validates
structure, file inventory, native hierarchical labels and referenced sealed runs.
Exit 0 means verified; exit 1 means structurally valid but unverified; exit 2
means refused, including malformed referenced run records such as a missing `state`.
These records never return the unverified exit code. Exit 3 reports an unexpected
internal error with a traceback on stderr. Capture may return 1 and still
produce a private capture.

The request becomes `block.json` (`agent-ee.block/1`). Required fields are `schema`,
`id`, `version`, `role`, `source`, `sheet`, `parts_manifest`, `boundary_ports`,
`requirements`, `files`, and `runs`; optional `layout` names a captured PCB.
Capture supplies `source.repository`, `source.commit` and `files` hashes. The
request supplies `source.license` as a source statement, not permission to
publish. Every file entry maps a safe directory-relative path to SHA-256. The `source.paths` mapping binds payload paths to pinned repository paths.
No files may be unlisted.
`parts_manifest` identifies a captured JSON parts/1 subset; this lane preserves
its bytes and validates the complete structure with
`parts_manifest.validate_manifest`, including required fields, exact supplier
identities and unique native selectors. Malformed manifests are refused before
run qualification. Structural validity alone does not establish membership.

Every boundary port declares `name`, `net_name`, `direction`, `voltage_domain`
and `current_a` (nonnegative finite declared bound). `name` and `direction` must
match an actual top-level native `hierarchical_label` and its `shape` in `sheet`.
No electrical ports are inferred from symbol names, net names or parent labels.
All hierarchical labels must be covered exactly once. Refusals name each
missing, extra or direction-mismatched port. A block's declared net
name/domain/current are interface assertions, not measured electrical facts.

Each requirement has `id`, `statement` and a nonempty unique `checks` list.
Each run reference has `run_id`, `candidate_id`, `baseline_id` and `files`, a
mapping from every block payload path to its captured candidate-relative path.
Run locations are supplied separately to validation as `--run RUN_ID=PATH`;
block metadata cannot select arbitrary host run directories. There is no copied
status or self-authored evidence seal. Verification calls the existing public
run verifier in snapshot mode, requires its existing baseline and evidence seal,
and compares exact captured file bytes and mapped baseline requirement/check
identities. Only a complete `criteria_satisfied` run with passing mapped checks
can contribute coverage. Every declared run reference must verify and pass. Missing, incomplete or
escalated runs keep the block unverified. A bad
run seal, identity, mapping or changed captured sheet is refused.

This is provenance validation, not a new check registry or campaign authority.
An instantiated board still needs fresh board checks. The ordinary local-storage
trust limits in [security](security.md) apply. Captured third-party sheets,
models, assets and run records stay private until owner publication approval;
licence text never supplies that approval. No evidence store is created here.

`block-contract.json` is generated at capture and listed in `files`. Its schema
is `agent-ee.block-contract/1`, with all block descriptive fields except `schema`,
`files` and `runs`. This avoids a circular self-hash while binding source,
ports, requirements and parts path to the exact run candidate. Reauthoring the
contract and refreshing local hashes still requires a fresh run. Its bytes must
be covered by each run's file mapping, just like the sheet and parts subset.

Capture requests supply `source.paths` as a mapping of payload paths to pinned
repository paths, and `source.license` as the observed licence statement.
`--parts LOCAL_JSON` supplies an explicit local parts/1 subset when none exists
in that repository. The parts supplement is hashed and run-covered, but its
complete structure must satisfy the shared parts/1 validator. The request's
`files` field is replaced with the observed hashes. The generated contract must
not appear in `source.paths`. Use a fresh private directory for every capture;
existing destinations and symlink ancestors are refused. Validation writes no
files; redirect the JSON report outside the block directory.

Private acquisition may use JSON `null` for an unknown `voltage_domain` or
`current_a`. Such a block remains unverified even if referenced runs pass. This
lets acquisition record missing owner declarations without inventing electrical
limits from net names. Qualification requires explicit non-null declarations.

Capture membership alone does not establish native coverage. Credited checks are
currently limited to `hardware_interface`: the sealed native netlist producer
must export the scope's actual schematic, include the mapped block sheet in its
complete hierarchy, and contain the block's reconstructed native occurrence IDs.
Every invariant mapped to the credited requirement must be satisfied and its
resolved component witnesses must belong to those occurrences and to symbol
UUIDs in the actual captured block sheet. This supports standalone sheets and
captured enclosing hierarchies; a passing shadow sheet cannot qualify an unused
captured block. Scope/3 informational requirements cannot supply block coverage.

Verified blocks also require at least one passing, sealed `parts_subset` check
in a complete referenced run. Its configured manifest path must be the run's
mapping for this block's parts file, and its report must bind that file's exact
hash and the native export hash. The export must cover the actual block sheet
and hierarchy occurrences, with passing physical component witnesses whose
UUIDs belong to that sheet. A check against another manifest path or a shadow
sheet cannot qualify membership. The shared parts check rederives catalog
selection, CAD and datasheet dependencies under the existing coordinator seal;
this command does not run a second qualification pipeline.

The report's `parts_membership` contains a status and an `evidence` list of
`run_id`, `check_id` and `manifest_sha256` bindings. Missing membership evidence
keeps the overall status unverified even when all interface requirements pass.
Declared membership is not authenticated owner approval, live availability,
procurement permission or hardware acceptance.

Run references identify individual runs. They do not establish the current
state of a containing campaign; a later campaign escalation does not rewrite an
individual run's historical evidence. Check campaign status separately before
using a block in a campaign decision.

Other requirement check families and optional layouts currently have no
qualified subject binding here and therefore remain unverified, even when their runs pass. Missing
coverage is not inferred from hashes, check names, or author-written subject
lists. This validator consumes existing sealed coordinator evidence; it does not
execute another engine or grant board acceptance.

The owned controls in `tests/test_reference_blocks.py` exercise pinned capture
with a dirty checkout and replacement refs, a partial clone whose missing blob
must not be fetched, native port refusals, and sealed run qualification. Native
tests use actual KiCad exports and the public verifier; reviewer transport is
scripted and establishes no model-review quality claim. Run them on the host:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_reference_blocks -v
```

No FOME-MC sheet is distributed with this library. The proposed `psu` and `wbo1`
entries still need authorized source acquisition, explicit electrical
declarations, parts qualification and complete passing sealed runs. Owned
controls establish capture behavior, not qualification of those actual blocks.

[Block adaptation](block-adaptation.md) instantiates a captured flat block into a
pre-created child sheet and maps its requirements to fresh board checks. The
instance and source remain immutable; the ordinary repair writer edits the child.
`block_provenance` reports semantic changes and checks native boundary bindings.
Source qualification never supplies board check results.
