# Preparing the telemetry pod campaign

`python -m agent_ee.pod_driver PLAN --out NEW_DIRECTORY` prepares an ordinary
scope/3 campaign from explicit captured pod planning inputs. It does not choose
parts, generate an approved requirement map, or turn the RC fixture into a pod.
The actual pod plan depends on the requirement map, envelope, parts, schematic
binding, placement and plane producers. Those inputs must exist and pass their
ordinary validators before this driver can prepare a production campaign.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.pod_driver \
  /scratch/pod/plan.json --out /scratch/pod/prepared --dry-run
# Explicit inference launch, when the prepared pod inputs and subscription profile are ready:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.cli campaign \
  /scratch/pod/prepared/campaign.json --out /scratch/pod/campaign
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.cli verify-campaign \
  /scratch/pod/campaign
```

Use a pinned isolated checkout for tools and a separate new output directory.
Preparation never changes a source input. It refuses output beneath a declared
source directory and never overwrites an existing output. Keep generated native
files in this owned workspace until review and integration into fome-mc.

Without a mode flag the driver prepares only. `--dry-run` also runs native
checks, without models or implementation steps, against the empty scaffold.
`--run` instead explicitly invokes the existing campaign engine after preparing
and propagates its exit code. There is no provider fallback. Preparing a config
or a successful dry-run invocation does not mean that the pod is accepted.

The current state of the actual pod inputs against this driver is recorded in
[the pod composition record](pod-composition.md).

## Explicit plan

A plan is `agent-ee.pod-plan/1` with these fields; unknown fields are refused.

| Field | Meaning |
|---|---|
| `scope` | Path relative to the plan, or absolute path, to the compiled scope/3 planning file |
| `envelope`, `parts` | Candidate-relative paths to the ordinary envelope and approved parts records |
| `spec`, `spec_record` | Candidate-relative canonical pod spec and exact `spec_record` capture |
| `writer_inputs` | Exactly `{intent: PATH, deviations: PATH}`; both captured JSON paths |
| `implementation_steps` | Existing stage-step declarations for the five producers listed below |
| `routing_adapter` | Existing `agent-ee.routing-adapter/1` declaration; command/pin/exit semantics remain owned by that adapter |
| `placement_adapter` | Optional existing `agent-ee.placement-adapter/1` declaration for the owned placer; replaces the generic `place-board` step |
| `envelope_content` | Optional object declaring the envelope-content pre-receipt's extra inputs: any of `intent`, `layout`, `antenna_facts`, `captured_rule_areas` (captured paths) and `imu_reference`; see [envelope content](#envelope-content) |
| `review_profile` | Explicit subscription profile with independent reviewers and adjudicator |
| `implementation` | Explicit subscription writer definition |
| `routing_budget_s` | Integer matching the routing adapter's budget |
| `review_budget_s` | Explicit time reserved for reviews |
| `max_iterations` | Optional, default 10; minimum 8, maximum 100 |
| `max_seconds` | Optional, defaults to two full receipt timeouts plus review time; may increase up to the engine's 86400-second ceiling |

Each routing timeout must be at least twice the routing budget plus 300 seconds.
For budget 300, timeout 900 and review reserve 3600, the minimum campaign time
is 6000 seconds: each receipt includes the adapter timeout plus its 300-second
coordinator margin. `max_schema_resubmissions` is fixed at one in the generated
campaign. Stage `finding_retry_limit` is validated by the existing loader and
must be at least two.

The scope captures all dependencies needed by its checks and producers, including
the project template, libraries and initial intent/deviation records. Its native
mutable paths are the planned schematic/child sheets and board; the shared
scaffolder validates that boundary. The driver then adds the two writer inputs
and the declared coordinator outputs, all of which must already be captured after
scaffolding. Baseline inputs stay immutable. It preserves requirements, baseline
mappings, targets, reference architectures and limitations rather than inventing
coverage. The spec and its exact capture must be immutable baseline sources.
`POD-AC-8` and `POD-AC-9` must remain explicit `BENCH REQUIRED` limitations.
Additional captured accounting artifacts, such as an evidence-index criterion
map, can be included in baseline targets without a new driver field.

The ordered generic producers are:

| Stage | Step | Authority |
|---|---|---|
| schematic | `binding-fill` | Declared derived binding outputs |
| schematic | `emit-schematic` | Native schematic output |
| schematic | `forward-annotate` | Native board output |
| placement | `place-board` | Native board output; omitted when `placement_adapter` is declared |
| routing | `add-planes` | Only the native board; exit 0 only |

The owned placer is a campaign adapter, not a generic step, and the campaign
refuses an adapter combined with placement steps. With `placement_adapter` the
driver therefore requires the four other steps, validates the declaration with
`placement_adapter.validate_declaration`, binds it to the scaffolded candidate
with `bind_declaration` (captured immutable policy, mutable board), and copies
it unchanged into `campaign.json`. Placement semantics, helper pin and receipts
remain owned by that adapter. The owned `binding_fill.configuration` producer
must use its separate `output` form: the writer intent is its declared input and
the bound intent is the coordinator output that emission reads.

All generic producers adopt exit 0 only. Their complete argv, mounts, outputs
and timeouts are supplied by the owned producer modules, not guessed by the
driver. On routing turns the dedicated `routing_adapter` follows `add-planes`.
It retains its `[0, 2]` adoption contract: a validated incomplete route is an
eligible partial proposal, and fresh checks still report remaining defects.
The adapter's result/snapshot/preservation and receipt verification are unchanged.

The stage order is exactly `schematic, placement, routing, verification`.
This driver deliberately excludes a separate fab stage. The scope must still
carry its ordinary `generation` marker and `fab_package` done check, in
verification. AGENTEE-146 already supplies declared fab-output grading on the
current base; adding that stage here requires its complete producer contract
and inputs, rather than assuming a write-free or unchanged-board representation.

`mutable_inputs` is the engine's coordinator write authority, which includes
native outputs. The generated campaign separately freezes `writer_editable` to
exactly the captured intent and deviation files. Every writer attempt is checked
against that allowlist before any producer runs, including placement turns and
verification turns with no scheduled steps. Scheduled step outputs also retain
the engine's `writer_step_output_edit` guard. A routing turn additionally requires
the writer to leave the whole captured tree unchanged before any plane or router
producer. Immutable verification inputs remain protected throughout.

Retained verification checks the writer boundary against the first producer's
recorded input tree, or the anchored delivered tree when no producer ran. Every
ordinary successor must be captured from that implementation's proposal; explicit
bench transitions retain their separate verification. Missing receipt indexes
cannot conceal a successful or nonterminal writer turn. Campaigns without the
optional `writer_editable` declaration keep their prior behavior.

## Scaffold and validation

The driver first runs the shared `scaffold.scaffold` preparation into a temporary
directory. It invokes `cli.validate_scope`, `stages.declaration`,
`compile_baseline`, the ordinary mutable-input guard and all declared step/route
validators before publishing a run directory. It then captures the source tree
and reruns that same scaffold producer through a networkless `run_step`.
The adopted files must equal the validated preparation byte for byte.

The scaffold is an in-place candidate transition: its receipt's after-tree equals
the complete first candidate. The ordinary `pre_receipts` declaration binds
`scaffold-receipt/receipt.json` by its digest and `{pre_receipt: 1}`. Campaign
start imports it and campaign verification rechecks it. No parent temporary
preparation is used as acceptance evidence.

`campaign.json` is written only after the sandbox scaffold succeeds, its complete
input/output binding passes, and the final scope passes the validators again.
If scaffold execution fails, its failed receipt and a `status: preparing` driver
record remain, without a usable campaign file or a prepared claim.

The pod's optional `placement_adapter` must declare both a helper and a captured
immutable packing policy. Null declarations are refused by `validate_plan`.
`validate_configuration` validates the policy JSON with the existing packer's
schema and value validator before output creation or campaign iteration 1;
references are checked against the declared placement subjects because the
scaffold is initially empty. Policy-supplied `fixed_overhangs` are refused:
those allowances come only from captured envelopes. Invalid policies report
`invalid pod placement policy PATH: REASON`; valid declarations retain their
existing configuration and receipt format.

## Envelope content

When the plan declares `envelope_content`, the shared
[stage-three producer](scaffold.md#stage-three-envelope-content-agentee-177)
`scaffold.content_step` runs as the second pre-receipt, after `write-scaffold`
and before the campaign. Its envelope and parts are the plan's own `envelope`
and `parts`; its board is the scope's board and its only write. The declared
optional inputs are passed verbatim. Plans without the field are unchanged:
one scaffold pre-receipt, no content receipt.

Before any output exists, the driver refuses an unknown `envelope_content`
field, a non-canonical or uncaptured input, an input naming the native board
or schematic, and an envelope with fixed footprints but no declared `intent`
and `layout`, or an antenna keepout without declared `antenna_facts`. After adopting the scaffold, it runs the content step through
`run_step` with binding `{pre_receipt: 2}` and adopts its board. A failed
producer retains `content-receipt/` and a `status: preparing` driver record,
without `campaign.json`.

`campaign.json` declares `[scaffold-receipt, content-receipt]` in that order.
`stage_receipts.validate_pre_receipt_chain` requires the content receipt's input
tree to equal the scaffold receipt's exact after-tree. The content receipt's
after-tree must equal the prepared candidate (`first_capture_binding`). The
driver record keeps both links as `scaffold` and `envelope_content`. Campaign
start revalidates the ordered chain; verification binds its last receipt to
run 1. A reordered, rebound, changed or omitted receipt is refused, and so is
a board edited after preparation.

The step reads the intent and layout as captured at preparation. Writer edits
to them during the campaign do not rerun this step; forward annotation and
the fresh checks grade the board that results.

## Retained output

| Path | Meaning |
|---|---|
| `plan.json` | Exact supplied plan bytes |
| `inputs/` | Captured inputs plus the receipted empty native scaffold |
| `scope.json`, `campaign.json` | Ordinary scope/3 and campaign/1, only after final validation |
| `scaffold-plan/` | Read-only recipe and sandbox input paths |
| `scaffold-receipt/` | Ordinary sandbox receipt, tool identities and retained output |
| `content-receipt/` | Envelope-content sandbox receipt, only when `envelope_content` is declared |
| `driver-receipt.json` | Source hashes, validation identities, config/scope hashes, scaffold binding and inference estimate note |
| `dry-run/` | Ordinary checks-only review, when requested |
| `dry-run-record.json` | Exact check results, stage reachability and sealed result hash; always `done: false`, `inference: false` |
| `campaign/` | Ordinary campaign run only with explicit `--run` |

The dry run reaches the empty schematic's `stage_not_reached` evidence. Later
stages retain their actual `stage_not_reached` or `stage_blocked` status; those
statuses are not changed into passing checks. Native tool failures remain failures
or unknown coverage. Neither a dry run nor this receipt supplies bench evidence,
owner DFM approval, stock evidence or a completed generated pod.

The record repeats the ticket's historical inference estimate: rung 1 used an
estimated $10–15 review plus $1–3.6 writing per iteration on three parts; pod work
may multiply that estimate. This is not measured pod cost or spending authority.

## Routing predecessor integrity

Campaigns using the dedicated routing adapter may declare one `add-planes`
routing predecessor, writing only the board and adopting exit 0. No duplicate
router or unrelated routing producer is accepted. The existing step planner runs
that producer before the adapter only when routing is the earliest failed stage.

Before the first producer, the live candidate must equal the captured placed
source. Before each later producer, it must equal the preceding adopted receipt's
after-tree. Every receipt must consume that exact expected tree. A failed plane
producer stops before routing; an unreceipted between-step edit is refused.
The dedicated router's semantic verifier still runs before adoption.

Retained campaign verification independently reconstructs the same chain from
captured source through the ordered anchored receipts. It refuses mismatched
inputs, a producer after a failed predecessor, a forged intermediate tree, or a
completed proposal differing from the adopted result. Individual valid receipts
cannot be combined into an unrelated chain.

## Verification

`tests.test_pod_driver` runs the shared scaffold in the real receipt sandbox and
executes the checks-only path using an explicitly synthetic parts fixture. Its
production driver has no fixture fallback. Tests also cover malformed plans,
small budgets, subscription-route refusal, removed bench obligations, widened
writes, tampered scaffold receipts and failed scaffold execution. They also
prepare a plan with the owned placement adapter, pass the engine's own campaign
start validation (without launching an iteration), and refuse the adapter beside
a generic placement step or with an uncaptured policy. With `envelope_content`
they run the real content producer after the scaffold, start the campaign, capture
run 1 and verify the chain. They also refuse reordered, rebound, changed or omitted
receipts, a later board edit, uncaptured or undeclared inputs and a failed producer.
`tests.test_routing_campaign.RoutingPredecessorTests` tests real sandbox producers
and the owned router protocol against the existing synthetic router; it exercises
live and retained chain tampering and earlier-stage dispatch. These tests prove
configuration and custody behavior, not a functioning telemetry pod.

## Writer repair of invalid intent

The campaign can return verified unknown-pin and unaccounted-pin diagnostics
from the owned emitter to another writer attempt, within the existing stage
finding limit and campaign deadline. The driver’s `writer_editable` restriction
continues to apply. Failed producer outputs are never adopted; see
[intent repair](intent-repair.md) for the exact recovery and replay contract.
