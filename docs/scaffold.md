# Empty generation candidates

`agent-ee scaffold ENVELOPE PARTS --scope PLAN --out DIR` creates an empty
native project and a relocatable generation scope from explicit captured inputs.
`ENVELOPE` uses `agent-ee.envelope/1`; `PARTS` uses `agent-ee.parts/1`.
Experimental rung-1 formats are refused by the existing validators.

```sh
PYTHONPATH=src python3 -m agent_ee.cli scaffold \
  planning/envelope.json planning/parts.json \
  --scope planning/generation.scope.json --out build/empty
PYTHONPATH=src python3 -m agent_ee.cli review build/empty/scope.json \
  --out build/empty-checks --checks-only
```

`--scope` takes an explicit stage-one scope/2 or scope/3. Neither the
[envelope](board-envelope.md) nor the [parts manifest](approved-parts.md) defines
all requirement/check mappings, targets, reference architectures or qualified
project rules. With `--scope` the command preserves these from `PLAN`; it does
not invent requirements or lower their coverage. `--plan` instead runs the
[stage-two producer](#stage-two-producer), which derives the checks, mappings,
stage policy and project rules. Exactly one of the two is required.

## Planning inputs

The plan's `inputs` must capture the envelope, parts manifest, its entire pinned
dependency closure, requirement sources and a `.kicad_pro` template. Parts
manifest paths are relative to this captured inventory, as they are for checks.
`schematic` and `board` must share a project basename. The corresponding
`.kicad_pro` must already be captured; native board and schematic files may be
absent from the planning inputs. Existing native files at those paths are
replaced with empty files in the output, leaving the sources unchanged.

The parts closure is qualified through the existing catalog/selection and CAD
readers. An invalid pin, unsupported schema or missing dependency refuses the
operation. Missing required datasheet provenance is retained explicitly in the
receipt; it is not converted into qualified evidence or owner approval.

The project template supplies design rules and net classes. Scaffolding changes
only project filename and sheet metadata. The board receives the envelope's
exact straight-edge outline, even copper-layer count, total thickness and each
stackup layer's thickness. It contains no components, nets, tracks, vias or
zones until the stage-three pre-receipt below writes the declared mechanics.
Scaffolding does not claim those envelope obligations pass. Copper weights
remain declarations because native board files have no corresponding field.

Every non-root native UUID path in the parts selectors produces a child sheet,
including intermediate ancestors. Files are named `block-UUID.kicad_sch` beside
the root. Each parent links its pre-created children, and every sheet has a title
block. The scaffold owns the root UUID, independently of the experimental
schematic emitter. Up to 128 sheets are supported, with at most 35 direct child
sheets per A4 page. Reused sheet UUIDs and noncanonical UUID selectors are refused.

Local symbol and footprint tables point through `${KIPRJMOD}` into the copied
CAD closure. Libraries, tables, project rules, parts, blocks and requirement
sources remain immutable. Only the root schematic, pre-created child schematics
and board appear in `mutable_inputs`. A writer still cannot add or delete a
captured file, or edit an immutable file.

### Shared builder

`agent_ee.scaffold.native_files` is the only empty-scaffold builder on main. Given
a validated envelope record, it builds the root and pre-created child schematics,
the board and the project file metadata. Both modes of this command use it, and so
do the rung-1 drivers ([RC campaign](rung1-campaign.md#empty-scaffold) and
[rung1_qualified](rung1-qualified.md#empty-scaffold), AGENTEE-134). Those drivers
supply the root UUID that their schematic emitter derives, together with stock
library tables. They run the builder as an in-place `write-scaffold` stage step
that is imported as a campaign pre-receipt. The physical layer order and
byte-identity tests here are the single source of truth for the board it emits.


## Stage-three envelope content (AGENTEE-177)

After outline preparation and before campaign run 1, `content_step` declares an
`agent-ee.stage-step/1` with `stage: scaffold`. Run it through `run_step` and
`adopt`, then pass its receipt as a campaign `pre_receipts` entry with binding
`{"pre_receipt": 1}`. It is preparation, outside `implementation_steps`.
The write source is `design/BOARD`: ee139's in-place layout. The board is its
only write; captured planning records, libraries and project rules stay intact.

```python
from agent_ee.scaffold import content_step
from agent_ee.stage_receipts import run_step, adopt
from agent_ee.storage import digest

step = content_step(repository, "envelope.json", "parts.json", "project/design.kicad_pcb",
                    intent="intent.json", layout="layout.json",
                    imu_reference="U3", antenna_facts="antenna-facts.json")
receipt = run_step(receipt_dir, scaffold_inputs, step, {"pre_receipt": 1})
adopt(receipt_dir, receipt, scaffold_inputs)  # Refuses a failed receipt.
pre_receipt = {"path": str(receipt_dir / "receipt.json"),
               "sha256": digest(receipt_dir / "receipt.json"), "binding": {"pre_receipt": 1}}
```

All paths except `repository`, `receipt_dir` and `scaffold_inputs` are relative
to the captured input inventory. `intent` and `layout` are the existing
connectivity-intent and schematic-layout records used by the schematic emitter.
The writer calls that emitter to obtain native paths, including sheet and unit
UUIDs; it does not implement a second UUID recipe. Each fixed reference must
select one manifest instance with matching symbol and footprint identity.
The existing captured manifest qualifier verifies its catalog replay and CAD
closure before native footprint loading. Library footprint bytes supply all
pads and graphics. The declared position, rotation and front/back side are
applied with pcbnew, then the footprint is locked. Forward annotation binds by
native path and retains the footprint UUID and transform.

Mounting holes are constructed directly as locked board-only footprints, with
one round drilled pad, the declared reference, position, drill and plating,
and BOM/position-file exclusions. They have no netlist path or schematic symbol.
`Mechanical:MountingHole` has no pins: putting it in connectivity intent still
produces `empty_pins`; this preparation step does not relax the resolver.
NPTH pad size equals drill size. Declared PTH holes receive a 0.3 mm annulus;
fabrication checks still judge that generated choice. The pod contract fixture
uses four 3.2 mm NPTH holes at a 4 mm inset, the owner-authorized default; a
production generator must retain that default as a recorded deviation.

Polygon keepouts become named board-level rule areas with the declared layers
and forbid set. The legacy dynamic antenna generation path requires captured
facts, but AGENTEE-211 deliberately refuses deriving a manufacturer clearance
from module dimensions. It cannot publish antenna content until a separately
explicit construction contract is supplied. Captured CAD geometry is not a
manufacturer-derived minimum and does not qualify antenna placement.

`imu_reference` explicitly selects one fixed footprint. The writer places
`IMU_AXES  X forward  Y left  Z up` on F.SilkS, 4 mm below that anchor in board
coordinates. It does not infer IMU pin/axis mapping or choose a harness part.
The 776228-1 versus 776087-1 identity/orientation question remains open in
AGENTEE-209. The declared captured footprint and transform are used verbatim.

The native entry point is `python -m agent_ee.scaffold --inputs INPUTS
--envelope ENVELOPE --parts PARTS --board BOARD [--intent INTENT --layout LAYOUT]
[--imu-reference REF] [--antenna-facts FACTS]`; `content_step` runs it through
`/usr/bin/python3` in the networkless executor. Fixed content requires both
emitter inputs. Missing members, inconsistent identities, unknown references,
non-quarter-turn antenna transforms and populated input boards are refused.
The initial board must match the declared outline and stackup. Construction
uses a private native board and replaces the file only after a successful save;
a failed step publishes no changed board. Repeated preparation from identical
inputs produces identical bytes. Reapplying to a populated board is refused.

`verify-campaign` imports and verifies the stage receipt against run 1's exact
capture, including input and output hashes. A changed receipt or different
initial board is refused. The stage-one scaffold receipt predates this content;
its candidate hash is not the new board's candidate hash. Keep both records,
and use the stage receipt for campaign binding.

[test_scaffold.py](../tests/test_scaffold.py) exercises original mechanical
stand-ins for AMPSEAL, SMA, IMU and radio on the pod envelope, native hole and
keepout checks, front/back transforms, byte identity, forward annotation with a
real KiCad XML export, and campaign pre-receipt verification. Model responses
in the campaign protocol test are scripted; native tools and receipts are real.
These tests establish generator mechanics, not physical component suitability,
12 mm height, IMU isolation or a complete telemetry-pod campaign.

## Stage-two producer

`agent-ee scaffold ENVELOPE PARTS --plan PLAN --out DIR` (AGENTEE-141) derives
the scope/2 and the `.kicad_pro` design rules through the same module, then
publishes exactly as stage one does. `PLAN` is an `agent-ee.scaffold-plan/1`
with these required fields, none of which the envelope or parts manifest states:

```json
{
  "schema": "agent-ee.scaffold-plan/1",
  "name": "rc filter", "scope": "Native validity, parts membership and fab rules.",
  "limitations": [],
  "inputs": [{"path": "planning/envelope.json", "at": "envelope.json"}],
  "project": "project/design",
  "template": "templates/native.kicad_pro",
  "fab_profile": "fab/profile.json",
  "native_requirement": {"id": "native", "statement": "Native ERC and DRC pass.",
                         "source": {"path": "native.md", "locator": "line 1"}},
  "targets": ["targets.md"], "reference_architectures": ["reference.md"],
  "finding_retry_limit": 3
}
```

`inputs` must capture everything stage one needs except the project file, plus
the template, the fab profile and the profile's cited sources. `project` is the
shared basename of the schematic, board and `.kicad_pro`.

Derived from the inputs:

| Output | Derivation |
| --- | --- |
| Checks | `erc`, `netlist`, `parts` (`parts_subset` over the manifest), `drc`, `fab` (`fab_profile` over the referenced profile), in that order |
| Requirements | The plan's native requirement maps `erc` and `drc`. `parts-membership` maps `netlist` and `parts` at the manifest's `declaration_source`. The profile's single limit requirement maps `fab` at `PROFILE /limits` |
| Stages | `schematic` (erc, netlist, parts), `placement` (drc), `routing` (fab). Symbol and footprint subjects are the manifest's required selectors; the routing subject is one saved track, because the `fab_profile` check's native rule-loading witness needs a track |
| Project rules | `board.design_settings.rules` `min_clearance`, `min_track_width`, `min_via_diameter`, `min_through_hole_diameter`, `min_copper_edge_clearance` and `min_hole_to_hole` from the profile's minimum limits, through `fab_profile.compile_profile` |
| Default net class | `clearance` from the minimum limit, because KiCad DRC enforces it as a hard floor. `track_width`, `via_diameter` and `via_drill` from the preferred limit when present, otherwise the minimum: they are routing defaults, and DRC grades the board minimum rules instead. See [Default class clearance](#default-class-clearance) |
| Board | Outline, layer count and stackup from the envelope, as in stage one |

### Per-net classes

AGENTEE-184 adds two optional plan fields, required together:

```json
{
  "net_constraints": "requirements/net-constraints.json",
  "netclass_qualification": "templates/qualification.json"
}
```

Both paths must be captured by `inputs`. The first holds an
[`agent-ee.net-constraints/1`](net-constraints.md) document; the second holds the
[qualified KiCad 10.0.7 record](../fixtures/netclass-completeness/qualification.json).
The template still carries only Default, with empty patterns and assignments.
The producer derives the board minimums and Default first, then calls
`net_constraints.emit` with that project and the captured envelope. The same
emitter supplies the named classes, exact escaped net patterns and DRC width
rules. No constraints means the existing Default-only output and five checks.

The output contains `<project>.kicad_dru` when the emitter supplies rules and
`<project>.net-constraints.record.json`. The receipt's `derivation.net_constraints`
lists the input path/hash, derived class records, exact net-to-class map and
record path. The record binds the final project bytes, including sheet metadata,
and the emitted rule bytes. The constraints and record are added to the scope's
immutable requirement inputs; native-rule consumers bind the project and rules.
The captured qualification is also immutable.

Declared widths, clearances and differential-pair track/via gaps below the fab
minimums refuse production before any output is published. Width declarations
are checked before rounding, and the emitted classes are checked again so a
current-derived width below the minimum is also refused. A preferred class
width is a routing default, not a fabrication minimum: a 0.15 mm class width is
allowed with a 0.127 mm fab minimum and a 0.2 mm preferred width. Both the fab
minimum via pad and the derived Default via pad must exceed their respective
drills, independently; valid preferred dimensions cannot mask degenerate minima.

The derived `netclasses` check uses `netclass_completeness`, maps to the native
requirement and runs in the placement stage before DRC. It checks qualified
Default field presence only. Stage-presence gates still apply to an empty
candidate; an isolated project-check run can establish completeness without
claiming the board has reached placement.

The shared emitter's limits remain: a differential pair needs native `+`/`-`
or `P`/`N` suffixes (`CAN_H`/`CAN_L` is refused); impedance is recorded without
computed geometry. Classes such as CAN, RF_50 and PWR carry captured dimensions,
not inferred electrical requirements. The tests' dimensions are owned synthetic
assumptions, not approved telemetry-pod routing values.

### Default class clearance

The owner decided on 2026-10-18 that the Default clearance is the fab minimum, and
that the router targets preferred values where it can
([decisions](decisions.md), "Default net class clearance"). A native probe found
that KiCad 10 DRC enforces the Default class `clearance` as a hard floor, but not
the class track width, via diameter or via drill. Those three are graded only
against the board minimum rules. A preferred clearance in the class would fail
boards that meet the fab minimum, so the board rules and Default use fab minimums:

- the six project rules and the Default `clearance` always use the minimum tier;
- the Default `track_width`, `via_diameter` and `via_drill` use the preferred tier
  when the profile has one. They are what KiCad routing uses by default, and
  avoiding the surcharge is the router target;
- a preferred clearance is never written to the project. The receipt records it as
  a router target that is not applied. The pinned
  [kicad-route adapter](routing-adapter.md#router-targets-from-the-scaffold-producer)
  passes no clearance target, so no router consumes it yet.

The receipt's `derivation.netclasses` gives each class value its tier, limit and a
`basis` that cites the decision. `derivation.router_targets` lists every preferred
limit for the four class keys, with the class setting it was applied to, or `null`,
and its `consumer`.
A preferred value below its minimum is still refused, including clearance.
`NativeProducerTests` checks this in KiCad. On the produced project, two 0.15 mm
tracks 0.175 mm apart pass DRC. That gap is between the 0.15 mm minimum and the
0.2 mm preferred clearance. A 0.45/0.25 mm via on the same board, smaller than
the preferred Default via, also passes. The same board fails when the class
clearance is set to the preferred value, and a 0.1 mm gap still fails.

The template supplies every other project setting. It must be a complete
`kicad-10.0.7-net-settings-5` project, such as the owned
[native project](../fixtures/netclass-completeness/native.kicad_pro), whose only
net class is a complete Default. The derived `.kicad_pro` and the profile are
immutable baseline criteria, so a repair cannot relax them.

The producer refuses, naming the cause, and publishes nothing when:

- the envelope's `fab_profile` is null, the profile bytes differ from its
  reference, or any envelope predicate stays unresolved against the capture
  (for example no minimum `max_layers` limit, or an unverified source);
- any profile limit is not source-verified, one of the six rule keys has no
  minimum limit, a preferred value of a class key is below its minimum, the
  minimum or Default via pad does not exceed its drill, or the limits name more than one
  requirement;
- the parts manifest lacks required datasheet provenance, or an envelope fixed
  footprint is not a declared parts instance;
- a `.kicad_pro` or `.kicad_dru` is captured at the project path, or the template
  has other net classes, patterns or assignments, an `ignore` severity for a
  derived rule type, DRC exclusions, missing Default fields or another settings
  version;
- the plan has missing or unknown fields, a requirement ID collides with a
  derived one, or `finding_retry_limit` is outside 2 to 100.

If a required selector is on a child sheet, the staged presence checker cannot
observe it. The producer then emits no stage policy and records why in the
receipt. Per-net classes require the optional captured constraints described above.
KiCad rule settings outside the six keys keep the template values. The producer
derives no `fab_package` done gate and no scope/3 envelope acceptance.

## fome-mc severity template

[`fixtures/fome-mc/rules/pod.kicad_pro`](../fixtures/fome-mc/rules/pod.kicad_pro) is
the telemetry-pod producer template (AGENTEE-189). It is the owned
[native project](../fixtures/netclass-completeness/native.kicad_pro) with fome-mc's
ERC and DRC `rule_severities` maps added, and nothing else from fome-mc. Pod
criterion 3 grades ERC and DRC "under the fome-mc toolchain rules".
[`severities.json`](../fixtures/fome-mc/rules/severities.json) is the
`agent-ee.rule-severities/1` record of both maps. Its pinned-external source is
`fome-mc.kicad_pro` at fome-mc `df304f3321296f7e517e0d2e2aa52e1380d52993`, with
blob `8f749a3` and SHA-256 `17a1d6b8…`. `agent_ee.rule_severities` writes both
files:

```sh
PYTHONPATH=src python3 -m agent_ee.rule_severities --repo ~/git/fome-mc \
  --commit df304f3321296f7e517e0d2e2aa52e1380d52993 --path fome-mc.kicad_pro \
  --repository-label fome-mc --base fixtures/netclass-completeness/native.kicad_pro \
  --out build/rules --name pod
```

Capture reads one committed blob from the object store; it never reads the working
tree or fetches. It refuses an abbreviated commit, an uncommitted path, a severity
other than `error`, `warning` or `ignore`, and nonempty `erc_exclusions` or
`drc_exclusions`. An exclusion silences a single violation instance, which makes it
a waiver rather than a severity. The template step refuses a base that already
has severities or exclusions, so the record is the template's only severity
source.

None of the six derived rule types is `ignore` in fome-mc, so the producer
accepts the template. Its existing refusal still applies: editing the template
to silence a derived rule is refused. The derived `.kicad_pro` has fome-mc's
severities and the fab-profile rules. fome-mc's own rule values, net classes,
ERC pin map and other settings are not carried. The receipt's
`derivation.template` names the template bytes, which bind to the record through
`test_rule_severities`. With KiCad 10.0.7 defaults as the baseline, fome-mc
ignores four more ERC rules (`endpoint_off_grid`, `footprint_link_issues`,
`lib_symbol_issues`, `lib_symbol_mismatch`) and three more DRC rules
(`npth_inside_courtyard`, `pth_inside_courtyard`, `solder_mask_bridge`). It
raises `footprint_type_mismatch` from ignored to error. KiCad already ignores
`missing_courtyard` and `footprint_filters_mismatch` by default. Rules that
fome-mc leaves unset keep KiCad's defaults, as they do in fome-mc itself.

A severity map waives no violation. Every item kicad-cli still reports, warning
or error, is a [check](check-reference.md#drc-and-schematic-parity) defect unless
matched by a separate captured [owner-approved exact disposition](violation-waivers.md).
The owner admitted this mechanism on 2026-10-18; agents may never grant waivers.
The producer does not add dispositions or native project exclusions.

## Output and evidence

`DIR/inputs/` contains the complete selected inventory; `DIR/scope.json` uses
relative paths into it. `DIR/scaffold-receipt.json` records input and output
hashes, candidate and baseline identities, root/sheet identities, Python version
and hashes of the agent-ee policy modules. With `--plan` the receipt is
`agent-ee.scaffold-receipt/2`: it records `plan_sha256` in place of
`scope_sha256`, and a `derivation` naming the envelope, profile, template and
manifest hashes, any per-net constraint derivation, each derived rule's setting, value, limit ID, tier and
`/limits/N` locator, each Default class value's `basis`, and the `router_targets`. It has no timestamp or absolute
output path. Identical planning bytes and tool versions produce byte-identical
outputs and the same candidate hash at different output paths.

The receipt is provenance only. A normal `review` captures `candidate.json`,
runs the configured checks and seals their evidence. A checks-only run remains
incomplete because it has no independent reviewers or adjudication. An existing
`agent-ee.campaign/1` configuration can point its `scope` at the generated
`scope.json`; the standard campaign owns reviewers, adjudication and the single
writer. No campaign or model call is launched by scaffolding.

Existing output directories are refused. Output cannot be nested in a selected
source directory. Changed inputs during preparation refuse the operation, and a
failed preparation removes only the directory created by that invocation.

## Coverage limits

The plan retains its [staged policy](staged-campaign.md) if present. Flat RC
plans with expected R1/C1/J1 subjects produce native-observed
`stage_not_reached` defects and enter repair on the first campaign iteration.
An unstaged plan retains its existing convergence behavior; scaffolding does
not silently add a stage policy.

Hierarchical files can be pre-created and loaded by KiCad, but the existing
staged presence checker refuses hierarchy. It remains unknown coverage; a
hierarchical staged campaign is not qualified by this command. The production
envelope baseline continues to refuse uncovered obligations. AGENTEE-161 enables
stackup coverage through the declared profile's source-verified authority;
holes, keepouts and unresolved generator mechanics still need bindings.
Supplying an envelope as an uncited generation input does not make it an
acceptance authority. Every obligation needs mapped executable coverage before
capture can accept the envelope.

[test_scaffold_producer.py](../tests/test_scaffold_producer.py) covers the
producer on owned synthetic envelope, profile and parts fixtures: byte-identical
outputs, the derived mappings, rules, class tiers, router targets and stages,
every refusal above, the fome-mc severity template, and native runs. In the
first, pcbnew loads the derived rules and Default class from the produced project,
and each of the five checks reports `stage_not_reached` on the empty candidate.
The second is the clearance DRC run described above. In the third, kicad-cli ERC
and DRC get the derived fome-mc project, and their `ignored_checks` match the record.
The per-net tests check exact CAN, RF_50 and PWR values/patterns, byte identity,
immutable constraint evidence, refusal boundaries and a sealed passing
`netclass_completeness` result on the derived project with the pod severity
template. A native probe resolves all six declared nets to their classes while
near-matching and undeclared names remain Default; DRC flags a 0.25 mm power
track against its emitted 1.012 mm rule.
[test_rule_severities.py](../tests/test_rule_severities.py) recaptures the
committed record from the local fome-mc object store, rebuilds the template from
it byte for byte, and covers the capture refusals.

The original synthetic tests in [test_scaffold.py](../tests/test_scaffold.py)
exercise byte identity and capture hashes, native ERC/DRC, local library tables,
nested sheet creation, populated native library lookup with broken-URI controls,
immutable project rules, preparation refusal and the
existing inventory guard. The campaign test uses scripted model responses with
real native presence probes, coordinator seals, adjudication and writer guards.
It establishes the first repair transition, not live-model review quality or a
completed generation campaign.

Both scaffold modes accept [named IC/module entries](approved-parts.md#named-ics-and-modules).
The shared manifest qualifier replays each part record and freezes its response,
datasheet and decision sources in the baseline. The producer derives the same
membership check, selectors and fab rules for mixed generic/named manifests.
This does not emit a completed IC schematic or establish component suitability.
