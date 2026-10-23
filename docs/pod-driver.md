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

## Operating-temperature acceptance migration (AGENTEE-310)

The pod lane must replace operating-temperature `exemptions` for U1
ESP32-S3-WROOM-1-N8, U2 LSM6DSO32TR and U4 MCP3208-BI/SL with
[`reduced_acceptance`](assembly-readiness.md#digikey-operating-temperature).
Keep every reference in the DigiKey `parts` binding and declare its accepted
-40 to 85 degC range against the board's 105 degC requirement. Capture a
hash-pinned `operating-temperature-acceptances.txt` with one canonical owner
sentence per reference, for example
`U1 is accepted for operating temperature -40 to 85 degC.` Repeat for U2 and U4.
Capture real thermal validation separately in `operating-temperature-thermal.txt`
and declare each reference in `thermal_evidence`. Until that evidence exists,
a covering 85 degC rating is unknown, pending thermal validation; an inadequate
rating or expected temperature exceeding it is a defect. Never manufacture a
validation summary to clear this gate. The old exemption file cannot pass.
Use already captured local DigiKey evidence; live DigiKey capture remains
owner-gated. The driver does not infer or synthesize these declarations.

U3 NEO-M9N and J3 DM3AT-SF-PEJM5 are not owner acceptances. Their 85 degC
exceptions are the recorded D-PCB-9 and D-PCB-12 decisions in
`expansion/design-rules/constraints.json`, made under the repository's
`AGENTS.md` delegation. Declare them as
[`delegated_acceptance`](assembly-readiness.md#delegated-engineering-acceptance)
records citing both files, with the decided ranges -40 to 85 and -25 to 85
degC. The delegation names `github.com/halbritt/fome-mc` and the owner's grant
commit `3f959131abca2dd9d40400496f3f6ebac9d363f5`. The decisions authorize
exactly u-blox NEO-M9N-00B and Hirose Electric Co Ltd DM3AT-SF-PEJM5. Never
add U3 or J3 lines to `operating-temperature-acceptances.txt`. They need the
same DigiKey binding and thermal evidence. Two obligations stay open. The pod
has no captured thermal validation, so every accepted part stays unknown. J3's
rating comes only from the LCSC listing and is not yet captured, so J3 stays
unknown until a rating is captured.

## Explicit plan

A plan is `agent-ee.pod-plan/1` with these fields; unknown fields are refused.

| Field | Meaning |
|---|---|
| `scope` | Path relative to the plan, or absolute path, to the compiled scope/3 planning file |
| `envelope`, `parts` | Candidate-relative paths to the ordinary envelope and approved parts records |
| `spec`, `spec_record` | Candidate-relative canonical pod spec and exact `spec_record` capture |
| `requirement_source` | Name of a pinned authoritative source in `pod_driver.SOURCES`; the spec and capture must be its pinned bytes; see [requirement set](#requirement-set-agentee-197) |
| `writer_inputs` | Exactly `{intent: PATH, deviations: PATH}`; both captured JSON paths |
| `implementation_steps` | Existing stage-step declarations for the producers listed below |
| `routing_adapter` | Existing `agent-ee.routing-adapter/1` declaration; command/pin/exit semantics remain owned by that adapter |
| `placement_adapter` | Optional existing `agent-ee.placement-adapter/1` declaration for the owned placer; replaces the generic `place-board` step |
| `net_constraints` | Optional captured constraint document; applies project/rules under receipt and registers native assignment verification; see [application](#net-constraint-application) |
| `envelope_content` | Optional object declaring the envelope-content pre-receipt's extra inputs: any of `intent`, `layout`, `antenna_facts`, `captured_rule_areas` (captured paths) and `imu_reference`; see [envelope content](#envelope-content) |
| `review_profile` | Explicit subscription profile with independent reviewers and adjudicator |
| `implementation` | Explicit subscription writer definition |
| `routing_budget_s` | Integer matching the routing adapter's budget |
| `review_budget_s` | Explicit time reserved for reviews |
| `max_iterations` | Optional, default 10; any integer in the campaign engine's 1–100 range (`campaign.validate_iterations`) |
| `max_seconds` | Optional, defaults to two full receipt timeouts plus review time; may increase up to the engine's 86400-second ceiling |

Each routing timeout must be at least twice the routing budget plus 300 seconds.
For budget 300, timeout 900 and review reserve 3600, the minimum campaign time
is 6000 seconds: each receipt includes the adapter timeout plus its 300-second
coordinator margin. `max_schema_resubmissions` is fixed at one in the generated
campaign. Stage `finding_retry_limit` is validated by the existing loader and
must be at least two.

The driver adds no iteration minimum of its own (AGENTEE-197, agent-509
production decision 2026-10-22): an explicit `max_iterations` such as 2 is
accepted when it lies in the engine's 1–100 range, and the default stays 10.
Zero, more than 100, non-integers and booleans are refused before any output,
as is any profile or writer outside the explicit subscription harnesses.
`max_seconds` is the campaign wall clock, not a placement or routing timeout.
It is derived from the routing adapter's budget/timeout and the review reserve
and never changes them, nor `max_iterations`. The driver receipt records the
derivation as `budget_derivation`: the iteration value with its source
(`plan` or `default`) and the engine range, the routing budget, timeout, step
margin and two reserved turns, the review reserve, and `max_seconds` with its
source (`plan` or `derived`), minimum and formula.

The scope captures all dependencies needed by its checks and producers, including
the project template, libraries and initial intent/deviation records. Its native
mutable paths are the planned schematic/child sheets and board; the shared
scaffolder validates that boundary. The driver then adds the two writer inputs
and the declared coordinator outputs, all of which must already be captured after
scaffolding. Baseline inputs stay immutable. It preserves requirements, baseline
mappings, targets, reference architectures and limitations rather than inventing
coverage. The spec and its exact capture must be immutable baseline sources.

### Requirement set (AGENTEE-197)

The campaign baseline enters at `validate_configuration`, so the driver binds
it there to the complete acceptance set of an authoritative source. The
generic `compile_baseline` knows nothing of the pod, and the evidence index
runs after the fact; neither is the entry boundary.

The plan's `requirement_source` names one entry of `pod_driver.SOURCES`, a
committed, reviewed registry. Each entry pins the sha256 of the captured spec
and of its exact `spec_record` capture, the capture's `spec.name`, the
revision and the bench obligations. The supplied record must first verify as
the exact capture of the supplied spec bytes. `requirement_set` then requires
the spec hash to be the pin and the record hash, computed over the verified
record with only `spec.name` replaced by the pinned name, to be the record
pin. The filename is not source authority: the capture CLI records the input
basename as `spec.name`, so a byte-identical spec recaptured under another
filename is the same source, while every other field still has to match.
The source is never selected by supplied prose: a
recapture with an edited `Revision N`, a dropped, renamed or added criterion
is another source and is refused. Only after the hashes match are the
acceptance IDs and their byte locators read from the verified capture, and
the spec's own `POD-DOC-P1` must declare the pinned revision.

| Source | Captured files | Revision, criteria | Bench obligations | Scope contract |
|---|---|---|---|---|
| `telemetry-pod-rev2` | `fixtures/telemetry-pod/spec.md` sha256 `09e02c8d9471aabfa5d25935c87f436370cf1be367a96425b35305a53ce93493`, `spec-record.json` sha256 `58f07c5ab3206aad70fc31561d873200d18ff9145b97cc0b1570c465a84fd250`, name `spec.md` | 2, `POD-AC-1`..`9` | `POD-AC-8`, `POD-AC-9` | Legacy, unchanged: each bench criterion appears in a `BENCH REQUIRED` limitation |
| `telemetry-pod-rev3` | fome-mc `1157b91899fd9f420089fbec4264b823310f0ad9` `telemetry-pod/expansion/requirements/compiled/requirements-map/spec.md` sha256 `e71dcca744f4b520fc4698402202d7d9efff2d316f8ad3c5eb6403cdf9d036c6`, `spec-record.json` sha256 `1d978fc1ddeddfe64a6900a719c437dabb31b2999feb258265b0fbad1ab4820b`, name `REQUIREMENTS.md`; `POD-DOC-P1` bytes 30-421, `POD-AC-10` bytes 21943-22224, `POD-AC-11` bytes 22229-22495 | 3, `POD-AC-1`..`11` | `POD-AC-8`..`11` | Every criterion is a baseline requirement ID or the prefix `POD-AC-n: ` of a limitation; each bench criterion has a limitation starting `POD-AC-n: BENCH REQUIRED` |

Refusals raise `RequirementSetError` with `reason` and `requirement`:
`undeclared-source` (the plan names no pinned source; refused with the plan,
before scaffolding), `downgraded-source` (the supplied capture is the pin of
an earlier revision), `unapproved-source` (any other spec or record bytes),
`revision-mismatch` (the pinned capture does not declare its revision),
`unaccounted-acceptance-criterion` (the scope drops it) and
`missing-bench-obligation`. The source name, revision, revision locator, the
spec hash, the supplied record hash and capture name, the authoritative record
pin, criteria and their locators are recorded as `requirement_set` in the
driver receipt's validation. This proves only that every criterion is carried;
it is not acceptance. Thermal, owner DFM/preview and bench limitations stay in
the prepared scope. `test_pod_driver` reads the actual Rev-3 capture
read-only from the pinned fome-mc store and checks both pins against the
captured files.

Admitting a new source, or changing a pin, is a coordinator action: capture
the spec with `python3 -m agent_ee.spec_record capture`, take both hashes from
`sha256sum` of the captured spec and record, add a `SOURCES` entry naming its
capture name, revision and bench obligations in a reviewed commit, extend
`test_pinned_sources_are_the_captured_files`, and select it by name in the
plan. A plan never supplies its own hashes.
Additional captured accounting artifacts, such as an evidence-index criterion
map, can be included in baseline targets without a new driver field.

The ordered generic producers are:

| Stage | Step | Authority |
|---|---|---|
| schematic | `binding-fill` | Declared derived binding outputs |
| schematic | `emit-schematic` | Native schematic output |
| schematic | `forward-annotate` | Native board output |
| schematic | `block-variants` | Stage-owned native candidate sets; required only with `placement_adapter.blocks.stage_owned` |
| placement | `place-board` | Native board output; omitted when `placement_adapter` is declared |
| routing | `add-planes` | Only the native board; exit 0 only |

The owned placer is a campaign adapter, not a generic step, and the campaign
refuses an adapter combined with placement steps. With `placement_adapter` the
driver therefore requires the other declared steps, validates the declaration with
`placement_adapter.validate_declaration`, binds it to the scaffolded candidate
with `bind_declaration` (captured immutable policy, mutable board), and copies
it unchanged into `campaign.json`. Placement semantics, helper pin and receipts
remain owned by that adapter. The owned `binding_fill.configuration` producer
must use its separate `output` form: the writer intent is its declared input and
the bound intent is the coordinator output that emission reads.

### Stage-owned block variants (AGENTEE-499)

When forward annotation creates the first native board, opt into the producer
with `placement_adapter.blocks.stage_owned`. Historical immutable block requests
keep their existing behavior. The new step runs last in the schematic stage,
right after `forward-annotate`; placement remains the owned adapter on the next
placement turn.

Example `blocks` section (all paths are candidate-relative):

```json
{
  "netlist": "blocks/native.xml",
  "allocation": "blocks/allocation.json",
  "arrangement": "blocks/arrangement.json",
  "candidates": ["blocks/power.json", "blocks/sensor.json"],
  "side_eligibility": "blocks/eligibility.json",
  "contract": "blocks/contract.json",
  "stage_owned": {
    "eligibility": "blocks/eligibility-template.json",
    "contract": "blocks/contract-template.json",
    "report": "blocks/producer.json"
  }
}
```

Capture the allocation (`agent-ee.block-allocation/1`), arrangement, eligibility
template and contract template as immutable inputs. The eligibility template
has the ordinary supported side-eligibility schema and explicit permitted faces,
but its `sources.board_sha256` is absent or null. The contract template is an
ordinary `agent-ee.block-contract/1`; its embedded eligibility must equal that
template, its allocation's `board_sha256` is absent or null, and its relationships
must equal the locality document bound by the placement policy. A historical
board-bound template is refused, even when it names the current board. No
historical `/2` candidate is converted or relabelled.

Capture empty-byte placeholders for the netlist, bound eligibility, bound
contract and every candidate output. Capture the report placeholder as
`{"schema":"agent-ee.block-variant-producer/1","status":"pending"}`.
These paths are coordinator-owned mutable outputs, never writer-editable files.
The candidate list must have one path per derived block, in derivation order.

Create the step with `agent_ee.block_variant_stage.step(source_root, scope=scope,
declaration=placement_adapter, mutable_inputs=final_mutable_inputs)` and insert
it after forward annotation. `source_root` is the agent-ee `src` directory.
The final mutable set includes native mutable paths, writer inputs and all
coordinator output paths. The driver checks the step's exact context, argv and
write map against the scope and placement declaration. The step uses the same
policy binding function as placement, exports the current native schematic's
netlist, recaptures the board, binds copies of the templates to its SHA256, and
calls `block_variants.produce` for every block and every eligible face. It adds
no fixed poses or side locks. Native flip refusals remain explicit variant
results, and failed steps are not adopted.

The `agent-ee.block-variant-producer/1` report records the immutable input hash
map, policy-context hash, board and schematic paths/hashes, and every generated
output hash. The ordinary stage receipt records source/tool identity, input and
output hashes and adoption. `placement_adapter.binding` keeps the campaign's
frozen binding tied to immutable inputs. At placement consumption,
`block_variant_stage.consume` checks the report against the captured manifest,
current native inputs and output bytes; `placement_adapter.blocks_binding`
checks candidate-set, eligibility, contract, allocation and exact request-policy
bindings. The helper then checks fresh netlist identity and reproduces native
variant evidence. A pending report or a move, rotation or flip after production
refuses before launching placement; regeneration is required. These provenance
checks do not grant placement or board acceptance.

When the `binding-fill` step passes `--ratings` and the scope has an
`assembly_readiness` check, the driver records `rating_provenance:
"rating-provenance"` in the receipt's validation and in `campaign.json`
(AGENTEE-256). The campaign then captures the adopted receipt and report into
each derived candidate scope; see
[coordinator rating provenance](stage-receipts.md#coordinator-rating-provenance).
The sealed scope must not declare `rating_provenance` itself, and preparation
refuses one that does.

A plan may declare `layer_policy_derivation` (AGENTEE-260). The driver validates
it against the scope and the routing adapter and copies it into `campaign.json`. A
plan without it keeps the routing adapter's declaration. The
pour area-loss bound is then derived by the coordinator on each routing turn; see
[derived at the pre-routing boundary](routing-adapter.md#derived-at-the-pre-routing-boundary-agentee-260).
A `/2` `via_budget` from the [via-budget pilot](routing-adapter.md#via-budget-pilot-agentee-265)
is copied unchanged, so a relative `pilot.path` resolves against the prepared
`campaign.json` directory. Campaign start re-derives it.

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
scaffold is initially empty. Policy-supplied `fixed_overhangs` and
`constraint_provenance` are refused: those allowances and sources come only
from captured envelopes. So are per-obstacle `provenance` and per-group
`constraint_provenance` (AGENTEE-365), through the adapter's shared
`refuse_policy_sources`; the same unsourced geometry remains a valid policy. Invalid policies report
`invalid pod placement policy PATH: REASON`; valid declarations retain their
existing configuration and receipt format.

## Envelope content

When the plan declares `envelope_content`, the shared
[stage-three producer](scaffold.md#stage-three-envelope-content-agentee-177)
`scaffold.content_step` runs after `write-scaffold` and any constraint-application pre-receipt
and before the campaign. Its envelope and parts are the plan's own `envelope`
and `parts`; its board is the scope's board and its only write. The declared
optional inputs are passed verbatim. Plans without the field are unchanged:
no content receipt; the optional constraint receipt is independent.

Before any output exists, the driver refuses an unknown `envelope_content`
field, a non-canonical or uncaptured input, an input naming the native board
or schematic, and an envelope with fixed footprints but no declared `intent`
and `layout`, or an antenna keepout without declared `antenna_facts`. After adopting the scaffold, it runs the content step through
`run_step` with binding `{pre_receipt: 2}` and adopts its board. A failed
producer retains `content-receipt/` and a `status: preparing` driver record,
without `campaign.json`.

`campaign.json` declares `[scaffold-receipt, content-receipt]` in that order when constraints are absent. With constraints it declares `[scaffold-receipt, constraints-receipt, content-receipt]`.
`stage_receipts.validate_pre_receipt_chain` requires the content receipt's input
tree to equal its predecessor receipt's exact after-tree. The content receipt's
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
| `constraints-receipt/` | Constraint application receipt, only when `net_constraints` is declared |
| `content-receipt/` | Envelope-content sandbox receipt, only when `envelope_content` is declared |
| `driver-receipt.json` | Source hashes, validation identities, budget derivation, config/scope hashes, scaffold binding and inference estimate note |
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
iteration budgets outside the engine's 1–100 range (0 and 101 refused before
output, by the driver and by `run_campaign`), a real two-iteration plan to
campaign to driver receipt, small wall-clock budgets, subscription-route refusal, removed bench obligations, widened
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

## Net constraint application

With `net_constraints`, the driver applies the captured
[constraint document](net-constraints.md) after scaffold generation and before
optional envelope content. The project template must meet the existing emitter
contract: complete Default class, schema 5, no custom classes or assignments.
A captured sibling rules template is extended. The step writes the final
project, sibling `.kicad_dru` and sibling `.net-constraints.record.json`; record
collisions with captured inputs are refused. Source inputs remain unchanged.

`constraints-receipt/` hashes the scaffolded project input, constraints,
envelope, tool source and all outputs. The emitter records the exact final
project hash without post-generation mutation. `driver-receipt.json` retains
this receipt as `net_constraints`. Campaign custody verifies it in the same
ordered pre-receipt chain as scaffold and envelope content.

The driver adds `net-constraint-assignment` (`net_class_assignment`) at placement,
mapped to every class's existing baseline requirement ID. Unmapped IDs and check
ID collisions are refused before publishing output. The check freezes the
constraints, record, project and rules and uses fresh native evidence to verify
the actual functional nets after forward annotation. Empty-scaffold absence is
a defect, never permission to omit verification. Impedance remains recorded only.
Plans without `net_constraints` retain their existing receipt chain and checks.
The pod lane owns adoption of this field in its production campaign plan.
