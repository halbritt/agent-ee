# Stage receipts

A stage receipt records one writer-side generation step that the coordinator ran
itself: the exact argv, the tool identity, the input and output file hashes, the exit
status, the timing and the sandbox profile. Receipts are provenance (decisions 5 and 6 in
[implementation decisions](decisions.md#generator-direction-2026-10-17)).
A receipt never makes a check pass. Acceptance still comes only from fresh
sealed checks of the captured proposal. Since the owner decision of 2026-10-18
([done requires each stage through its declared step](decisions.md#done-requires-each-stage-through-its-declared-step-amends-decision-1)),
receipts are also part of the done rule: passing checks are not done when a declared
stage's graded content is not its step receipt's output
([Done through declared steps](#done-through-declared-steps)). The module is
[stage_receipts.py](../src/agent_ee/stage_receipts.py) (AGENTEE-95).

## Authority

Only the coordinator creates a receipt. It copies the design, runs the
declared step through the ordinary executor sandbox (Bubblewrap with
`--unshare-net`, a read-only `/candidate` and one writable `/work`), and hashes the result
itself. Campaign receipts live in `implementations/N/receipts/`. That directory is outside
the implementation writer's writable mount, `review-work/implementation`. Writer prose
and writer-made JSON are never read as receipts, wherever the writer puts them.
Neither is a tool report the writer copied. The validator recomputes every derivable
field from the retained coordinator records. Changing a claimed output hash, filling in a
version, removing network isolation or relabelling a failed exit is refused,
even when the forger also recomputes the receipt identity.

Review and adjudication of a stage use the same [structured citation
contract](result-reference.md#structured-citations) as other reviews. Fresh `/2`
responses require a resolving `pointer`, `lines` or `xpath` on every citation;
retained `/1` records may keep descriptive locators alone. Any supplied structured
field is checked even in `/1`. A resolving location does not make a receipt into
sealed check evidence or replace the accepted-defect witness gate.

## Step declaration

```json
{"schema": "agent-ee.stage-step/1", "stage": "placement", "step": "pack-helper",
 "argv": ["/usr/bin/python3", "/tool/pack.py"],
 "mounts": [{"source": "/opt/pack-helper", "at": "/tool"}], "source": "/tool",
 "version_argv": ["/usr/bin/python3", "/tool/pack.py", "--version"],
 "writes": {"project/board.kicad_pcb": "design/project/board.kicad_pcb"},
 "artifacts": [], "adoptable_exits": [0], "timeout_s": 120}
```

| Field | Rule |
| --- | --- |
| `stage` | `scaffold`, `parts`, `schematic`, `placement`, `routing`, `verification` or `fab` |
| `argv` | The executable must be under a declared mount or a read-only system path (`/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`). Never `/work` or `/candidate` |
| `mounts` | Read-only binds; `at` cannot overlap another mount or any sandbox system path |
| `source` | Optional mount path whose file tree identifies a Python tool |
| `version_argv` | Optional version probe, run in the same sandbox |
| `backend_report` | Optional `{artifact, key}`: copy that key of a declared JSON artifact verbatim. For kicad-route, `result.json` key `backend` |
| `writes` | Required map of candidate path → work path that becomes it. In-place writes use `design/<same path>`. Out-of-place writes name a produced file such as `out/candidate/...`. May be `{}` when `artifacts` is nonempty |
| `artifacts` | Work paths retained and hashed, for example `out/result.json`, `out/validate` |
| `adoptable_exits` | Must include 0. kicad-route's partial-but-valid exit 2 would be listed here. A failed validation (3) never is |

Native `pcbnew.SaveBoard` can also rewrite sibling `.kicad_pro`/`.kicad_prl`
files. The runner refuses undeclared changes in `run/design`; it does not
restore project siblings automatically. A board-saving wrapper must restore
their captured bytes and inventory before exiting, or write to a staging copy
outside `run/design` and declare only the staged board in `writes`. Follow the
[campaign writer rule](campaign-guide.md#saving-boards-without-changing-project-siblings)
without broadening the campaign's mutable inputs. Receipts remain provenance.

A step must declare at least one write or artifact. An artifact-only step, such as
a fab export, declares `"writes": {}` and lists its retained work paths in
`artifacts`. Its receipt records the input tree and artifact hashes with
`"outputs": {}` and `"changed": []`. Adoption still validates the receipt and
requires the design to match its inputs, then leaves the design untouched.
Any added, deleted or changed design file is refused under the empty write scope.
Every recorded artifact entry must have a hash: a missing artifact, empty declared
directory, link or special entry adds `missing_artifact:<path>`. An otherwise
adoptable exit is then `refused`, adoption fails and the receipt is not applicable.

## Python tool source snapshots

The fab, `rung1_campaign`, `rung1_qualified`, `rung1_stages`, pod scaffold and
net-constraint, envelope-content, schematic emission/annotation, `add_planes`
and `binding_fill` step builders capture Python source with
`tool_source.snapshot` before declaring `/tool/agent-ee`. Host imports continue
using the original source tree. Capture excludes directories named `__pycache__`;
all other regular files, including standalone `.pyc` files, are copied. Links,
special files, empty sources and source changes during capture refuse preparation.
The receipt identity algorithm is unchanged: it hashes every mounted file and
link target. Added `.py`, `.pyc` or other files in the mounted snapshot still
cause `mount_changed` and `source_changed` and refuse adoption.

Host validation refuses a mount whose resolved source is the running package's
`src` tree, an ancestor containing it, or any descendant directory or file
(AGENTEE-327/329), except the qualified single helper files listed below.
Symlink and parent-directory aliases receive the same
`LivePackageSourceError` (`ValueError`) with violation `live_package_source:<at>`.
`run_step` validates before tool observation or launch and creates no receipt for
an invalid declaration. This applies to every mount, regardless of its sandbox
name or the optional `source` field. Use a snapshot for package tools.
`validate_step(host=False)` remains structural validation for retained receipt
replay; it does not inspect today's host source paths.

AGENTEE-329 also refuses device/inode aliases of `src`, its ancestors and descendants,
including bind mounts whose resolved spelling is outside the checkout. Execution
opens each validated resolved source with `O_PATH | O_CLOEXEC | O_NOFOLLOW`
(and `O_DIRECTORY` for directories), compares the descriptor device/inode with
the validated identity, and passes it to bwrap using `--ro-bind-fd` and
`pass_fds`. Some bwrap versions, including the qualified host's 0.9.0,
canonicalize fd sources back to pathnames. Therefore both the version command
and main step also run a fixed isolated Python prelude inside the sandbox. It
compares every mounted root's device/inode with the opened descriptor identity
before native `execvp` of the declared command. The prelude restores signals
ignored by Python and keeps bwrap's native script execution behavior. A rename that redirects bwrap's mount
cannot cause the declared command to run against that unexpected object.
Descriptors close on success, failure and interruption. The retained specification
keeps its original declaration. Both tool observations read declared mount trees,
mounted executable bytes and the optional source tree through held descriptors,
including after an A–B–A rename of a source root (AGENTEE-349). Host path strings
remain descriptive; they are never resolved back from a descriptor to read bytes.
Single-file mounts keep their original basename in the identity digest.
The resolved source device/inode is also checked after initial observation,
before the main step, and after execution; replacement or disappearance raises
`MountSourceChangedError` (`ValueError`) with violation `mount_source_changed:<at>`.
A post-run refusal leaves executor records for diagnosis and no adoptable receipt. A missing
source at initial validation raises `ValueError("mount source does not exist: …")`.
A failure before initial observation finishes removes the partial receipt and
launches no tool. Retained replay remains independent of host paths.

The prelude emits a fixed success line before executing the tool. The executor
consumes that line, records `execution.mount_guard` as a boolean, and keeps tool
stdout/version text unchanged. No success line means no verified mount: the
receipt adds `mount_guard_failed:run` or `mount_guard_failed:version`, even when
the guard's exit code 125 is in `adoptable_exits`. A tool that actually runs and
returns 125 keeps the ordinary declared exit policy. Retained guarded commands
require the exact prelude and a coordinator guard observation; routing replay
also checks the complete declared fd mount set and identity payload. Historical
path-form receipts retain their original verification behavior: only fd-form
receipts carry the mount-guard observation. Replay still accepts historical
path-form receipts without that observation. This compatibility is a deliberate
downgrade in assurance; replay does not establish that an old path bind was
descriptor-bound or protected from a source rename (AGENTEE-349).

The explicit live-helper allow-list is `agent_ee/kicad_route_attempt.py`,
`agent_ee/net_binding.py` and `agent_ee/symbol_library.py` relative to `src`.
`routing_adapter._step` mounts these three standalone files to run the pinned
routing adapter and its binding/parser imports. Only their canonical resolved
paths as regular files qualify; a directory containing them or a bind alias of
one of them does not qualify. All other package tools must use snapshots.
`tests.test_tool_source` discovers and exercises every package-source step builder;
`tests.test_stage_receipts` covers helper admission and descendant/alias refusal.

The identity guard compares the mount source with live ancestors and compares
the source and its ancestors with every entry of the live source tree. It walks
without following tree symlinks into external directories and propagates lookup
errors rather than admitting an uninspected tree. It does not cache this inventory.
Directory sources containing a descendant mountpoint in `/proc/self/mountinfo`
are refused with `mount_source_submount:<at>`, including same-device bind mounts
and mountpoints with escaped names. After opening a directory source, a no-follow
walk relative to that descriptor also compares directory and entry mount IDs
from `/proc/self/fdinfo`. Every inventoried entry is also compared by device/inode
against the live source inventory, refusing hard links into live `src` as
`live_package_source:<at>`, including links to qualified helpers embedded in a
directory. A byte-for-byte independent copy is allowed. An ancestor rename cannot redirect this inventory;
file bind mounts and directory binds are both refused. This conservative rule also refuses benign
submounts: registered builders need plain tool trees, never nested mount
hierarchies. A mountpoint at the source itself is allowed only after the ordinary
live-tree identity checks. A caller able to change the coordinator's mount
namespace after validation is outside this guard; sandboxed builders have no
authority in that namespace. Tree entries and file bytes remain mutable in place:
the existing before
and after tool identity checks still refuse observed content changes. This
guard requires Linux `/proc/self/mountinfo`, `/proc/self/fdinfo`, `O_PATH`, and bwrap support for
`--ro-bind-fd`; unavailable facilities fail closed.

Snapshots live under `AGENT_EE_TOOL_SOURCE_CACHE`, or
`~/.cache/agent-ee/tool-source` by default, in a directory named for the ordinary
receipt tree digest. Creation is locked and published by rename; reuse verifies
the complete tree and refuses corruption rather than repairing it. Equal captured
bytes use the same path within a cache root, including when the original source
paths differ or host bytecode changes. The declaration still binds the absolute
mount path: changing the cache root changes `spec_sha256`; changing source bytes
changes both the snapshot path and `spec_sha256`. Keep the cache root stable for
reproducible declarations. Snapshots remain available for subsequent declared
steps. An empty cache environment value uses the default; a relative value is
refused. Cache and snapshot directories have mode 0700, files 0400 and the lock
0600, regardless of umask. Verified older cache entries have these modes tightened.
These permissions prevent accidental writes; the owning host user can still chmod
or replace files, so byte verification remains necessary.

For every mount whose directory name is a 64-character lowercase SHA256 digest,
receipt construction and retained validation compare the initial mount observation
with that name. If `source` names that mount, its recorded `source_tree.sha256`
must agree too. Mismatch adds `snapshot_hash_mismatch:<mount>` and refuses an
otherwise adoptable execution. Adoption revalidates this recorded comparison;
it does not depend on the live cache still existing. The original in-step
`mount_changed` and `source_changed` checks remain unchanged.

Between campaigns, prune snapshots by last verified use with:

```sh
PYTHONPATH=src python3 -m agent_ee.tool_source prune --older-than-days 30
```

The command prints removed paths. The snapshot directory's modification time
records its last successful capture or verified reuse; corrupted reuse never
refreshes it. No timestamp file is added to the hashed source tree. Prune removes
only digest-named directories older than the threshold and abandoned `.capture-*`
directories. Unrelated entries remain. Prune and capture take the exclusive cache
lock. A running stage step holds shared locks on its mounted snapshot cache roots
from before initial tool observation until after receipt publication, including
version probes, failures and cancellation. Prune waits for those steps; capture
also waits, so a long step can delay preparation of another step using that cache.
Locks are released on exceptions and by the OS on process exit. Retained receipt
validation and adoption do not need the live cache.

There is no automatic eviction. A prepared declaration between executions is not
an in-flight step: use pruning between campaigns to keep snapshots needed by
future declared steps. Cache roots are locked in path order for steps with multiple
roots. Capture cleans abandoned `.capture-*` directories under the same exclusive
lock; no live publisher can hold one then. Links at cleanup paths refuse removal
rather than being followed.

Fab refusal messages include receipt violations and the retained receipt path.
The declared-fab, generate, fab-package and rung-one test fixtures copy refused receipts to
the absolute `AGENT_EE_REFUSED_RECEIPTS` directory when set, otherwise
`build/refused-step-receipts/` when writable. A read-only checkout falls back to
an atomically created per-user `agent-ee-refused-<uid>-*` directory under the
system temporary directory, with mode 0700. The assertion or cleanup diagnostic
reports the chosen receipt path. Cleanup retains receipts on fixture exceptions
and finalization too. If copying fails, the diagnostic reports the retention
error and original path without replacing the refusal assertion or an active
fixture exception. A relative or empty override reports a configuration error;
an unusable explicit override is reported rather than silently redirected.
These are local test diagnostics, not release evidence; remove them after diagnosis.
`tests.test_receipt_diagnostics` covers destination selection and retention failure
boundaries; `tests.test_tool_source` validates copied native refusal receipts.

## Receipt fields and status

Schema `agent-ee.stage-receipt/1`, `receipts/<k>-<step>/receipt.json`, retained with
`inputs/` (the pre-step design, mounted at `/candidate`), `run/` (executor records, the
writable `design/` copy and declared outputs) and `version/`.

- `tool.executable`: sandbox path, resolved host path and sha256, observed before the step.
  `tool_after` repeats the observation after the step. A change, or a missing executable, adds the violation `tool_changed`.
  Mount trees and the Python source tree are compared the same way.
- `tool.reported_version`: `reported` with the probe's first output line, or `unknown` with
  `value: null` and a reason. The reasons are: no probe declared, a failing probe, empty output, output
  that is not UTF-8 or larger than 1 MB, or output that is not a regular file. An unknown identity is never filled in.
- `tool.backend`: the declared report key copied verbatim. A `null` backend version stays `null`.
  The report is parsed as strict JSON. Any of the following records the backend as `unknown` with a
  specific reason: a missing, linked or special file, more than 1 MB, invalid UTF-8, a parse error,
  duplicate keys, `NaN` or `Infinity` (including float overflow such as `1e400`), nesting deeper than
  64, or an integer beyond the interpreter limit. No backend is guessed, and the process receipt
  is still written. A malformed report alone does not change the step status: the backend is provenance.
- `inputs` and `outputs`: sha256 by candidate path. `changed` lists outputs whose bytes differ.
- `violations`: `inventory_changed`, `write_outside_scope`, `missing_output`,
  `missing_artifact:<path>` (a null artifact entry when `writes` is empty), `unsafe_output`
  (the write source is behind a link or special entry), `executor_record_unsafe:<dir>/<file>` (an
  executor record in `run/` or `version/` is a link or special file), `executor_record_missing:<dir>`
  (the executor was entered but no trusted record exists), `tool_changed`, `mount_changed` and `source_changed`.
  `mount_changed` means a mount's file bytes differed between the observations before and
  after the step. Anything that probes a mounted tool checkout must therefore leave it
  byte-identical: agent-ee's own git identity probes run with `--no-optional-locks` and
  never use `git status` without it or `git describe --dirty`, which rewrite `.git/index`
  when its stat data is stale, as on first use in a fresh container (AGENTEE-269).
  Host Python imports can also add `__pycache__` files while another process runs
  a step. Those additions correctly refuse adoption; the sandbox's read-only
  mount does not prevent host writes. The fab-stage tests use a private source
  copy without shared bytecode (AGENTEE-299), and a deterministic real-executor
  regression confirms that bytecode added to the actual mount still refuses.
- `status`, recomputed from the recorded execution and violations:

| Status | Meaning | Adoptable |
| --- | --- | --- |
| `completed` | exit 0, no violation | yes |
| `partial` | exit in `adoptable_exits` other than 0, no violation | yes |
| `failed` | another exit, including a crash or a missing executable | no |
| `timeout`, `unavailable` | executor result | no |
| `cancelled` | interrupted; partial outputs are hashed and the receipt is written before the interrupt propagates | no |
| `refused` | an adoptable exit with a violation, or no trusted executor record together with an `executor_record_*` violation | no |

## Links and special files planted by the tool

The tool can create links, FIFOs and other entries anywhere in its writable `/work`. A link
target string is resolved differently in the coordinator's namespace: `/work/link -> /home/...` dangles inside the
sandbox but would name a real host directory outside it. The coordinator therefore never resolves
a path below the run directory by name.

- Every lookup of a write source, artifact, backend report or executor record opens one component
  at a time, relative to its parent, without following links. The leaf must be a regular file.
  A link or special entry at any component is reported, and its target is never read or hashed.
- A write source behind a link is `unsafe_output`, so the step is `refused`. A declared artifact, or an entry
  inside a declared artifact directory, behind a link or special entry is recorded as `null`. Artifact
  links are allowed for write-bearing steps, for example kicad-route's `latest` link.
  In artifact-only steps every null entry is a `missing_artifact` violation, including
  the trailing-slash entry recorded for an empty declared directory.
- The design copy, the retained inputs and artifact directories are walked through directory
  descriptors, iteratively. Directories deeper than 128 levels are recorded as special entries rather
  than entered. A link or special entry in the design copy is an `inventory_changed` violation.
- `adopt` first revalidates the retained receipt and refuses any receipt object that differs from it.
  A writer-supplied or edited copy cannot authorize adoption. It then reads each output without following
  links, checks the sha256 of the bytes it read, and writes those same bytes beneath the design, again
  without following links. A replaced output or ancestor is refused even when its bytes match.
- The executor writes `stdout.log`, `stderr.log`, `console.log` and `execution.json` into `/work`
  after the tool exits, without following or writing through any planted entry
  ([security](security.md#candidate-permissions)). An entry the tool created or changed at one of those
  names is moved aside and listed in the record's `displaced_records`. That, or a record that is later a
  link or special file, is `executor_record_unsafe` and is never read through.
  If the executor cannot write its records (for example the tool left `/work` unwritable), or an interrupt
  arrives before it does, no record on disk is trusted. The receipt is kept with `execution: null`,
  `executor_record_missing` and status `refused`. A non-interrupt executor error is then re-raised. A
  record left on disk after an interrupt is used only if it is a well-formed `interrupted` record, so at most it gives
  `cancelled`.

Tool descendants do not outlive the step. Bubblewrap's PID namespace is torn down when the step
exits or is killed on timeout or cancellation, so nothing in the sandbox can swap a path while the
coordinator reads it. The descriptor-relative reads and the hash-then-write adoption do not depend on that.

## Exact applicability

A receipt's after-tree is its `inputs` with its `outputs` substituted. Against the
delivered design, each receipt is classified as follows:

- `applicable`: the delivered tree equals the after-tree exactly, every file included, not only the outputs.
- `superseded`: a later receipt in the same index consumed exactly this after-tree and is itself
  applicable or superseded, so no writer edit came between the two steps.
- `not_applicable`: any other relation. The index lists the differing paths. A later writer edit to the board
  therefore makes a routing receipt inapplicable, as AGENTEE-93 requires. An unadoptable status
  is never applicable.

## Campaign integration

`agent-ee.campaign/1` accepts an optional `implementation_steps` list of step
declarations. Every declaration is validated before the campaign directory is created, and the list is part of the
hashed `config.json`. Each implementation runs its own step plan (`agent-ee.step-plan/1`),
which the coordinator derives from the frozen configuration and the verified source run.
In a staged campaign the plan holds only the steps of the earliest failing stage, followed by the
[placement adapter](placement-adapter.md) on placement turns. Unstaged campaigns, and campaigns
created before the plan existed, run every declared step as before. Receipt indexes follow the
plan's order. After the writer session, the coordinator first applies the existing
proposal checks to the writer's design. In staged campaigns it also refuses writer changes to any
candidate path in a planned step's `writes`, with `writer_step_output_edit` naming the step and paths.
The comparison is against the source candidate's exact bytes, including out-of-place step outputs.
Refusal stops the implementation as `incomplete` before any step executes or a proposal is captured;
there is no step receipt for a step that never ran. The immutable-input guard is unchanged.
It then runs each planned step on that design, and checks that every write target
is in `mutable_inputs` before launch. An adoptable step's exact outputs are copied back only
while the design still equals the receipt's inputs. An unadoptable step stops the repair as
`incomplete`. No default output is substituted and no proposal is captured. Afterwards the
existing inventory, immutable-input, baseline and adapter checks run again.

The exact writer prompt is retained at `implementations/N/implementation-prompt.txt`, outside the
writer's writable directory. When a staged campaign declares `implementation_steps`, the prompt lists
every declaration's `stage`, `step` and `writes`, marks the steps scheduled for this implementation,
and names their candidate-relative output paths. Declared steps own their stages: the writer prepares
other mutable inputs and must not perform a stage a declared step will run. It may return a no-op
summary when the planned steps cover the repair. Deferred steps remain gated; their outputs may
still be inputs prepared in an earlier stage. Unstaged prompts retain their exact previous bytes,
even when steps are declared, and unstaged steps retain their previous overwrite behavior.

`CampaignStageReceiptTests` in `tests/test_stage_receipts.py` exercises retained prompts, a no-op writer
followed by exact coordinator outputs, named refusal before execution, input preparation for deferred
steps, staged repairs without steps, and byte-for-byte unstaged prompt compatibility. These tests use
scripted writers and original synthetic measurement tools in the real executor sandbox.

Every implementation in a new campaign writes `implementations/N/stage-receipts.json`
(`agent-ee.stage-receipt-index/1`), even when it holds no receipts. The index lists every receipt with its
sha256 and applicability, together with the delivered design tree. Its digest is anchored in
`state.json["stage_receipts"]`. `verify-campaign` refuses the campaign in these cases:

- a missing, changed or unanchored index, or a receipt directory that is not indexed;
- a receipt that fails validation, is bound to a different implementation or source candidate, or
  does not match the step that the re-derived plan puts at its index;
- a delivered proposal without a receipt for every planned step;
- recorded applicability that differs from the recomputed applicability;
- a delivered tree that differs from the next run's captured proposal. A receipt that claims to apply
  while its outputs differ from the captured proposal fails with that message.

Campaigns created before this change have no anchors and no index, and they verify as before.

## Receipts before the first capture

`agent-ee.campaign/1` accepts an optional `pre_receipts` list (AGENTEE-139):

```json
{"pre_receipts": [{"path": "scaffold-receipt/receipt.json",
                   "sha256": "<sha256 of receipt.json>", "binding": {"pre_receipt": 1}}]}
```

Each entry has exactly `path`, `sha256` and `binding`. The path names `receipt.json`,
relative to the configuration file or absolute. The digest is 64 lowercase hex characters;
the nonempty binding object must equal the retained coordinator receipt's binding exactly.
Start refuses missing, changed, invalid or unadoptable receipts before creating the campaign.
Declared order is custody order (AGENTEE-224): `validate_pre_receipt_chain` requires each
later receipt's input tree to equal its predecessor's exact after-tree, so reordered entries
or an unreceipted edit between two pre-receipts refuse start.
It copies each complete retained receipt directory into `<campaign>/pre-receipts/N/`,
preserving artifact links without following them, and revalidates the copy. The frozen
`config.json` replaces the source path with `pre-receipts/N/receipt.json` and retains the
declared digest and binding. `state.json.pre_receipts` maps these local paths to their
digests. Replay needs neither the original receipt directory nor the live host tool tree.

`verify-campaign`, through `verify_campaign_receipts`, requires the imported inventory and
state anchors to agree with the frozen configuration, revalidates every receipt and requires
its after-tree to equal run 1's capture exactly in every top-level folder represented by its
inputs or outputs. Missing or changed files and extra captured files within those folders
refuse verification. Other captured folders, such as stock libraries absent from the receipt,
are permitted. Verification revalidates the same ordered chain; the last pre-receipt's
after-tree must bind that first capture, and each earlier one binds it through the chain.
A single pre-receipt therefore binds exactly as before. Dropping a later receipt leaves an
earlier after-tree that differs from the capture and refuses. A receipt without a first
capture is unbound and refuses verification.

Pre-receipts do not become `implementation_steps` and do not supply the declared-stage
`stage_done` binding. In rung 1, `scaffold` remains outside the scope's stage order. Its
pre-receipt binds only the initial empty candidate; the existing schematic and routing
receipts must still produce the final graded content. Campaigns without pre-receipts retain
their earlier verification behavior; an undeclared imported directory or state anchor refuses.

The digest in the frozen configuration also anchors the receipt's recorded tool identity.
Rewriting the retained tool hashes, receipt identity and state anchor cannot update that
configuration digest. This is integrity within the campaign's existing trust boundary,
not an external signature or a retained copy of the tool binaries.

## Coordinator rating provenance

A campaign whose `binding-fill` step runs with `--ratings` writes `AGENT_EE_Ratings`
fields that `assembly_readiness` grades `unknown` without
[receipt-bound provenance](assembly-readiness.md#receipt-bound-rating-provenance)
(AGENTEE-242). The sealed scope cannot name that receipt before the step runs. The
optional `agent-ee.campaign/1` field `rating_provenance` (AGENTEE-256) names one
top-level candidate directory, for example `"rating-provenance"`, that only the
coordinator writes. The [pod driver](pod-driver.md) sets it whenever a
`binding-fill --ratings` step feeds an `assembly_readiness` check. Start refuses the
field without an `assembly_readiness` check or a declared `binding-fill` step with
`--ratings`, when a captured input already lies under that directory, and when the
sealed scope already declares `rating_provenance`. A sealed declaration would be
borrowed, not derived from the campaign's lineage.

When an implementation adopts a `binding-fill --ratings` receipt, the coordinator:

1. selects that implementation's last adoptable `binding-fill --ratings` receipt from
   its anchored receipt index, revalidates it with its implementation binding, and
   requires its recorded `out/binding-fill.json` artifact hash to equal the retained
   `run/out/binding-fill.json`;
2. copies those two files into
   `implementations/N/rating-provenance/<directory>/binding-fill.receipt.json` and
   `binding-fill.json`, and computes `receipt_sha256` from the copied bytes. The
   copy must equal the anchored receipt digest. No hash or field written by the
   writer or the tool is used;
3. writes the new `proposal.scope.json`, which captures that directory in place of
   any inherited copy. The declaration is added to every `assembly_readiness`
   check. The sealed campaign scope and earlier proposals are never edited.

An implementation that does not run the step leaves the inherited directory and
declaration unchanged. The writer cannot edit or add them, because they are outside
`mutable_inputs` and the inventory is frozen. The first candidate has none.

Verification re-derives the declaration owed to every run. Run 1 is owed none. A run
captured from implementation N's proposal is owed N's adopted receipt when the step
ran. Any other run, including a bench-transition run, inherits the previous run's
declaration and capture bytes exactly. A different pin, a receipt from another
candidate or campaign, an inherited declaration after a new adoption, or changed
receipt or report bytes refuses `record_iteration` and `verify-campaign`. The derived
candidate's own `baseline.json` includes the declaration and the two pinned files. The
campaign compares the candidate's baseline recompiled without the declaration with the
sealed campaign baseline. Every other criterion, requirement and immutable input
therefore stays exactly as sealed. Receipt-index verification compares the captured
proposal with the delivered design outside this directory only. The directory itself
is verified by the lineage rule above. `assembly_readiness` still validates the receipt,
pin and report itself, so a tampered capture stays `unknown`, never `pass`.

## Done through declared steps

Decision 1's done (every check passes on one captured candidate and, for a generation scope, the
fab artifacts are sealed) has a third condition (AGENTEE-144,
[stage_done.py](../src/agent_ee/stage_done.py)). The declared steps are `implementation_steps` and
the `routing_adapter` step. In a staged campaign only steps of stages in the scope's
`stages.order` count. The step plan never runs another stage, so such a step is listed in
`excluded`. For example, rung 1's `export-fab` has stage `fab`, which is not a rung-1 scope stage.
For every candidate path a counted step writes:

- every stage with a counted step writing the path has an adoptable receipt of that step, adopted
  in an implementation whose proposal the next run captured;
- the final run's graded bytes of the path equal the output of the path's last counted step
  receipt;
- after a stage's last receipt, nothing else changes the path. A writer edit in any later turn is
  refused, including turns of stages with no declared step, and so is a change the coordinator
  did not receipt or a run not captured from an implementation. A writer edit before the stage's
  last receipt is an input the step consumed. The one exception is the
  [placement adapter](placement-adapter.md): its writer moves and helper receipt may change the
  path after the receipts of stages that precede placement. Its retained record already limits
  them to footprint transforms. Rung 1 qualified forward-annotates the board, places it through
  the adapter and then routes it.

Paths no counted step writes, and campaigns that declare no step, are unaffected. There the
writer remains the producer. The rule works on whole files, as receipts do. A step that keeps its
input bytes in place, such as an in-place pass-through, vouches for those bytes.

The coordinator applies the rule when a run reaches `criteria_satisfied`. A pass is recorded as
`state.json` `stage_done` (`agent-ee.stage-done/1`). For each bound path it lists the graded
sha256, the producing receipt, and each stage's last receipt by implementation, index and receipt
sha256. A failure leaves the campaign `incomplete`, with exit 1, no `fab_artifacts` and an
error naming the reason:

- `stage_step_unreceipted: <stage>/<step> has no adopted receipt producing <path>`: the stage's
  content was writer-made (or initial) and passed its checks without the step.
- `writer_made_stage_content: <path> changed by <writer|adapter_writer|coordinator|outside_implementation>
  in implementation N (turn <stage>) after the last receipt of declared stage <stage>`, or graded
  content that differs from its last step output.

`verify-campaign` recomputes the binding from the anchored indexes and receipts, after it has
validated every receipt. It refuses a `criteria_satisfied` state whose `stage_done` is missing,
forged or different from the recomputed binding. A state relabelled `criteria_satisfied` after a
refusal fails with the refusal's reason. A changed receipt output hash fails receipt validation
first. Initial content is never receipted, so a campaign whose initial candidate already passes a
declared stage is not done. Pre-receipts bind the initial capture separately and do not satisfy
this rule. `tests/test_stage_done.py` covers the rule on synthetic records and in real
sandboxed campaigns: a receipted routing step passes, a hand edit of the routing output in a
later turn fails although every check passes, and a campaign without steps is unaffected.

## Limits

- When `stages.order` includes `fab` and a fab step is declared, the fab checks
  require its sealed `out/fab` output. Before execution they stay unknown with
  `fab_step_pending`; only otherwise complete checks and review can schedule
  the coordinator turn. Fresh exports and CPL correction must match the step
  output before acceptance. This resolves the shape D/F1/G1 scheduling trap;
  a synthetic history without a fab receipt still fails
  `stage_step_unreceipted`. See [declared fab steps](fab-export.md#declared-fab-steps).

- Steps run after the writer session, on its delivered design. A tool the writer runs inside its
  own sandbox produces no receipt. Its effect counts only after capture, as before.
- Mount and source tree identities are observations at execution time. A later host
  change is not rechecked against retained bytes, because tool trees are not copied.
- The [routing adapter](routing-adapter.md) runs declared kicad-route steps through
  the campaign writer, with routing-specific verification before adoption and on replay.
  Exit 2 is a partial proposal requiring fresh checks; exits 1, 3 and 4 stop the
  implementation without a proposal. The AGENTEE-54 placer runs through the
  [placement adapter](placement-adapter.md). Generic step tests use an original test program.
- The [rung 1 driver](rung1-campaign.md) and [rung1_qualified](rung1-qualified.md) run the
  shared `write-scaffold` step (stage `scaffold`, builder `scaffold.native_files`) in place
  through `run_step` before the campaign exists, so its receipt is kept beside the campaign in
  `RUNDIR/scaffold-receipt/`, not in an implementation. The driver-written generation inputs and
  requirements (including `requirements/fab-package.md`) are receipt inputs; the empty project and
  builder template are its outputs. The rung 1 record's `done` requires the receipt's after-tree to
  equal the first captured candidate on `project/`, `generation/` and `requirements/`, both ways.
- New rung 1 campaigns import the scaffold receipt as a pre-receipt. Older campaigns without
  that declaration retain their historical verification behavior; only their rung 1 record
  checks the scaffold binding. Import paths are relative, but other campaign history records
  still contain absolute paths; this does not establish general campaign relocation support.
- A completed write-bearing receipt with all writes present but missing declared artifacts or an unknown backend is still
  adoptable. A tool adapter such as AGENTEE-93 must require its own result and validation artifacts, and
  the qualified statuses, before it adopts a proposal. `adoptable_exits` does not encode a tool's exit semantics.

## Declared authored inputs

An optional step `inputs` list names unique candidate-relative files separate
from `writes`. A missing input refuses a receipt as `missing_input:<path>`.
At done, each such producer needs an adopted receipt whose input hashes equal
the final captured files; changed authored inputs fail as `stage_step_stale_input`.
The full input tree is still retained and hashed. Declarations without this
field retain their historical normalization and done behavior.
The [mutable-intent schematic stage](intent-schematic-stage.md) uses this binding
for the writer intent and block seeds consumed by emission and forward annotation.

### Plane preparation before the owned routing adapter

A campaign may combine its dedicated `routing_adapter` with one routing-stage
`add-planes` step that writes only the board and adopts exit 0. The writer must
leave the entire captured source unchanged on that turn. The coordinator and
retained verifier require an exact input/after-tree chain through the plane
receipt and the adapter receipt. The dedicated adapter's semantic verifier and
`[0, 2]` complete/validated-partial policy remain in force. Other generic routing
predecessors and duplicate router steps are refused. See
[pod campaign preparation](pod-driver.md#routing-predecessor-integrity).

## Writer permission and coordinator output permission

A campaign may declare `writer_editable` as a unique list of canonical captured
paths within `mutable_inputs`. Empty means the writer may change no files.
Omitting the field preserves the existing writer contract. The pod driver always
sets it to its captured intent and deviations. Coordinator steps retain their
ordinary declared output authority within `mutable_inputs`.

Every writer attempt is checked before producers, including attempts returning
malformed output and turns without scheduled steps. Retained verification checks
the first receipt's input tree against the captured source outside the allowlist;
for no-step turns it checks the ordinary anchored delivered tree. An ordinary
successor must come from that proposal, and missing indexes are allowed only for
a final incomplete implementation without a proposal. Dedicated routing still
requires an entirely unchanged writer tree before its producers.

The [binding-fill producer](binding-fill.md) may precede schematic emission.
It writes only the intent and uses the ordinary captured receipt/adoption
contract. Its manifest, explicit component part references and captured source
closure stay immutable; a failed binding leaves no adoptable output.

## Owned schematic pin-resolution repair

A failed owned emit step still adopts no output. Staged campaigns with a frozen
writer allowlist may retain that failed attempt and return its verified pin
diagnostics to a bounded writer repair attempt. See [intent repair](intent-repair.md)
for eligibility, budgets and offline custody. Other step failures keep their
existing terminal behavior.
