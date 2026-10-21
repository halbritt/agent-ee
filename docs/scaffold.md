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

The project template supplies design rules and net classes. Scaffolding adds
project filename, sheet metadata and the provenance binding. A
[declared constraint source](#declared-design-rule-sources-agentee-331) can replace
sourced rule values. The board receives the envelope's
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

A footprint nickname spanning multiple captured directories is consolidated into
`PROJECT/captured-footprints/NAME.pretty`. Only declared members are copied,
byte for byte, after verifying their hashes; original captures and manifests
remain unchanged. Single-directory libraries keep their existing paths.
`captured-footprints/manifest.json` (`agent-ee.scaffold-library-copies/1`) records
the source CAD manifest pin and each original FPID, generated path, and source
path/hash. The scaffold receipt inventories this mapping and every generated
member; they remain immutable in the resulting scope. Duplicate footprint
identities, occupied generated library namespaces, and library-output collisions
with captured or native output paths refuse preparation. No library member or
acceptance requirement is inferred from a directory listing.

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
`{"pre_receipt": 1}`. It is preparation, outside `implementation_steps`. The [pod driver](pod-driver.md#envelope-content)
runs it as its second pre-receipt (`{"pre_receipt": 2}`), chained after `write-scaffold`.
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
When annotation adds hidden symbol fields to a fixed footprint, each new field
UUID is seeded by the retained footprint UUID and field name. Existing UUIDs
remain unchanged; see the [forward-annotation evidence](forward-annotation.md#evidence)
for the independent repeated-input and fresh-process reload control.

Mounting holes are constructed directly as locked board-only footprints, with
one round drilled pad, the declared reference, position, drill and plating,
and BOM/position-file exclusions. Their footprint IDs are
`AgentEE_Mechanical_MountingHole_NPTH` and `AgentEE_Mechanical_MountingHole_PTH`,
respectively. These are local footprint names with an empty library nickname:
they identify agent-ee's generated parametric mechanical forms without claiming
a linked library member or vendor catalog part. Native DRC therefore does not
look for a nonexistent mechanical library. Dimensions remain in the
captured envelope and embedded footprint geometry; the ID does not assert a
part number or approved binding. This identity assignment changes no hole
geometry. They have no netlist path or schematic symbol.
Each has a straight square courtyard on F.CrtYd and B.CrtYd around its pad
circle (AGENTEE-179), so the placer and `courtyard-overlap/1` treat it as a
fixed occupant on both sides. No fastener head or standoff size is declared,
so the courtyard reserves none.
`Mechanical:MountingHole` has no pins: putting it in connectivity intent still
produces `empty_pins`; this preparation step does not relax the resolver.
NPTH pad size equals drill size. Declared PTH holes receive a 0.3 mm annulus;
fabrication checks still judge that generated choice. Each hole pad carries every
copper layer plus F.Mask and B.Mask, built on an owned copy of KiCad's shared
`LSET.AllCuMask()`: mutating that static set in place makes later saves fold
`*.Mask` into `*.Cu`, so pads reload without solder-mask openings. The pod contract fixture
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
coordinates. The registered [IMU axes check](placement.md#imu-axes) can grade
that marking and the declared transform against a verified part/footprint axis
fact. The scaffold does not infer IMU pin/axis mapping or choose a harness part.
AGENTEE-209 carries the unresolved IMU choice and header orientation. The
header part is TE 776228-1 per the owner decision; the declared captured footprint
and transform are used verbatim.

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
keepout checks, front/back transforms, byte identity, exact pad layer sets
(copper and mask) after a fresh-process reload, forward annotation with a
real KiCad XML export, and campaign pre-receipt verification. Model responses
in the campaign protocol test are scripted; native tools and receipts are real.
These tests establish generator mechanics, not physical component suitability,
12 mm height, IMU isolation or a complete telemetry-pod campaign.

### Explicit captured antenna CAD retention

The optional `captured_rule_areas="retained.json"` argument to `content_step`
(or CLI `--captured-rule-areas retained.json`) enables **generation only** from
exact captured CAD. It does not qualify manufacturer clearance or satisfy a
check/prerequisite. Without it, the legacy dynamic derivation remains refused.
The existing envelope, facts-check reference and unresolved obligations are
preserved. In particular, `board_envelope` `keepouts/1` remains **unknown** for
the dynamic antenna declaration after successful construction.

The input is strict JSON with exactly these fields:

| Field | Value/contract |
| --- | --- |
| `schema` | `agent-ee.captured-rule-areas/1` |
| `purpose` | `retain-captured-cad-only` |
| `envelope` | `{path, sha256}` for the exact writer envelope |
| `parts` | `{path, sha256}` for the exact qualified parts manifest |
| `items` | Nonempty array covering every dynamic antenna item exactly once |

Each item has exactly:

| Field | Value/contract |
| --- | --- |
| `keepout_id` | Original dynamic envelope keepout ID |
| `reference` | Its unique fixed native component reference |
| `footprint` | Exact parts/CAD identity `RF_Module:ESP32-S3-WROOM-1` |
| `pose` | `{anchor_mm, rotation_deg, side}` exactly as normalized by `board_envelope.validate` |
| `source` | `{path, sha256}` of the unique captured footprint manifest member |
| `rule_areas` | Complete `probe_rule_areas` result recomputed from that source, including original zone polygon, flags, layers and digest |
| `policy` | Exact `esp32_s3_wroom_1_placement_policy("outside_board", pdf_path=...)` declaration |
| `context` | Complete `antenna_placement_context` result recomputed using captured guideline bytes |

This bounded path supports the actual front/zero-degree pose only. Other poses
or policy cases refuse; they need separately reviewed generation coverage.
The source must contain exactly one rule area. No selector may hide extra
zones. Enabled copper-layer applicability and the forbid set must equal the
original envelope declaration. The source can cover additional disabled
copper layers; its full original layer set remains in the captured CAD/probe.
KiCad's saved native zone must cover exactly the source's enabled-board layers.
The saved board's enabled copper layers must still equal the captured envelope. Every item, source,
part membership, transform and context must match; extra fields (including
minimum-clearance or accepted-status assertions), ambiguous or missing members,
symlinks, changed bytes, weakened restrictions and incomplete coverage refuse.

Capture the record, footprint, parts/CAD dependency closure and the exact pinned
guideline PDF as ordinary immutable scope inputs. The stage step explicitly
requires the record, and its receipt retains the entire input tree. The writer
rechecks every retention dependency before publication and refuses collisions
with its sole board write. No PDFs or stock vendor CAD are embedded in this
repository. Test policy sources are explicitly synthetic and cannot establish
real source qualification.

Construction retains the footprint-owned source zone already loaded with the
fixed component. It adds no derived antenna area or board-level duplicate,
and never clips the polygon at Edge.Cuts. Before replacing the destination,
it reloads the saved native board and compares the zone geometry, exact enabled
layer applicability, flags, ownership, FPID and pose with a separately loaded
captured member at that pose. Only the expected layer set is intersected with
the envelope's enabled layers; the saved zone is never filtered. Missing an
enabled layer still refuses. Geometry must survive native serialization exactly. This
is CAD fidelity evidence; the source probe/context digests are not review seals
or accepted check results.

For the actual pod, reuse the bound intent's emitter layout so fixed components
have the same native paths as subsequent forward annotation. A pathless preview
board would duplicate fixed electrical parts. The expected eventual inventory
is 108 electrical footprints plus four board-only mounting holes, not a claim
that placement has completed. Normal netlist/parts/probe checks and receipts
remain necessary. Source placement applicability, feed-point/corner geometry,
housing clearance, physical overhang/containment, RF performance and bench
obligations remain unresolved. The captured 48 x 21 mm polygon is not a
manufacturer minimum, and the datasheet's pin label 15 is never a clearance.

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
| Default net class | `clearance` from the minimum limit, because KiCad DRC enforces it as a hard floor. `track_width`, `via_diameter` and `via_drill` from the preferred limit when present, otherwise the minimum: they are routing defaults, and DRC grades the board minimum rules instead. A Default via inside a joint small-via surcharge band takes that rule's diameter bound. See [Default class clearance](#default-class-clearance) and [Default via and joint surcharge rules](#default-via-and-joint-surcharge-rules) |
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

### Default via and joint surcharge rules

AGENTEE-272: a `fab-profile/2` [conjunctive rule](fab-profile.md#conjunctive-surcharge-rules-fab-profile2)
charges extra for a via only when its drill and diameter are both strictly under
the rule's bounds. After the scalar tiers are applied, the producer tests the
Default `via_drill` and `via_diameter` against each source-verified joint rule.
A Default via inside the band gets `via_diameter` raised to the rule's
`min_via_pad_mm` bound. A via exactly at a bound is outside the band, as in
KiCad's native `min`. The drill is kept, and the class record cites the joint
rule (`tier` `preferred`, its `limit_id` and locator) with a `basis` naming the band.
`derivation.router_targets` gains the same entry. The six project rules are
unchanged: the minimum tier remains the DRC floor. An unverified joint rule
refuses production like any other unverified limit.

The [telemetry-pod profile](telemetry-pod-fab-profile.md) records JLC's
preferred 0.2 mm drill and the 0.2/0.25 mm drill, diameter <0.45 mm surcharge.
Its four-layer scaffold therefore gets a 0.45/0.20 mm Default via, the same size
the pinned router uses at `--fab-tier standard`. Previously it got the
0.25/0.15 mm minimum, whose 0.05 mm annular ring is below KiCad's 0.1 mm
default. The board minimum rules stay 0.25/0.15 mm.
`ProducerTests.test_four_layer_pod_profile_scaffold_produces_standard_default_via`
runs the whole producer on that profile. In
`NativeKicadRouteTest.test_scaffold_four_layer_default_via_routes_without_annular_errors`,
the pinned KRT routes an original four-layer board under the produced project
and the pod (fome-mc severity) template. It places only 0.45/0.20 mm vias, and
native DRC reports neither `annular_width` nor `hole_clearance`. The same routed
vias resized to 0.25/0.15 mm do fail `annular_width`.

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

## Packed-board silkscreen policy (AGENTEE-276)

Each copper entry in the envelope stackup may declare `role: "plane"` or
`role: "signal"` (AGENTEE-282). The scaffold emits KiCad `power` or `signal`
respectively, including on six-layer boards with inner signal layers. An omitted
role preserves the legacy convention: signal outer copper and power inner copper.
Dielectrics cannot declare a role. This changes no thickness, net assignment or
copper geometry. Plane nets and fills still come from the captured plane plan
and need fresh native checks. Plane-typed layers export as Specctra `power`;
declare signal layers explicitly when routing must use them. Native reload,
round-trip role comparison and Specctra export are covered by the six-layer test.

Board-only mounting-hole references are on F.Fab, preserving their identities
without printing text over the drill. Fixed footprints with envelope-declared
`courtyard_overhangs` have their silkscreen graphics and fields moved to the
corresponding F.Fab/B.Fab layer before saving. This avoids printing the portion
outside the substrate and retains the complete source drawing for fabrication
review. This policy moves all original silk on those footprints, including pin-one
marks. The content writer additionally places a same-side board-level callout
`ASM J1 90deg`, for example, identifying the reference and its declared native
KiCad footprint angle. Back-side text is mirrored for back-side reading. This
is an orientation callout; the complete footprint drawing on Fab retains the
pin-one geometry. It does not infer a manufacturer's pin numbering or mounting
direction from pad names.

The callout searches from the declared footprint anchor towards the centre of the
outline bounds in 200 equal steps. Its complete native text bounding box must be
inside the outline with at least 0.5 mm edge clearance, and at least 1.5 mm from
same-side footprint and courtyard bounds, pads on that side and existing
same-side silk. Courtyard bounds are included explicitly because the native
body bounding box alone does not establish clearance to the courtyard. The
1.5 mm spacing accommodates the 0.2 mm marker reservation plus ordinary
0.5/1 mm packing gaps; a larger declared gap can still refuse placement.
Opposite-side through pads also obstruct the callout. If no position fits,
generation refuses before replacing the board; a crowded or concave board can
need a changed placement plan. This bounded search does not guarantee finding
every feasible position. These board callouts have deterministic UUIDs and are
reserved by the placer alongside IMU markings. No copper, courtyard or antenna
geometry is clipped. Native DRC continues to grade every callout, including
interactions with copper zones or other board geometry outside the search's
obstacle inventory. Regenerate existing candidates to obtain these marks.

The shared producer policy moves only the Reference field of exact standard
`Resistor_SMD:R_0402_1005Metric`, `R_0603_1608Metric`, the corresponding
`Capacitor_SMD:C_*` and `Inductor_SMD:L_*` footprints from silkscreen to the
same-side fabrication layer. Other sizes, custom footprints and hand-solder
variants retain their source fields. Stage-three fixed-footprint loading and
forward annotation of newly added footprints both apply it. Forward annotation
preserves existing footprint presentation. Regenerate older packed candidates
from their captured inputs to adopt the policy; an existing board does not gain
new evidence automatically.

The placer derives same-side obstacles from visible board-level `IMU_AXES` and
`ASM` callout text on F.SilkS/B.SilkS. Each is its native bounding box with a 0.2 mm margin, plus
the packing policy's ordinary gap. The resulting plan records the obstacle's
text UUID and polygon in its effective policy. The source board and captured
policy remain unchanged. A fixed component conflicting with this reservation
refuses placement; the marker is never silently removed or moved.

The DRC grading rules, severity settings, exclusions and waivers are unchanged.
Native DRC continues to grade the complete saved board. The native tests in
[test_silkscreen.py](../tests/test_silkscreen.py) exercise exact identity selection,
front/back layers, unchanged pads, same-side reservation refusal, opposite-side
placement, unplaceable-callout refusal and real before/after silkscreen DRC
findings. The scaffold tests also load saved hole fields and copper-layer types
through pcbnew and construct J1/J2/U1 callouts on original pod-sized stand-ins,
with native DRC and repeated-byte identity. These are mechanics evidence, not
physical assembly approval of the actual pod parts. Ordinary warnings for
1206/1210 passives, ICs, SOT-323 and test points remain DRC findings; the dense
reference policy is deliberately unchanged.

## Copper feature generation boundary

Fixed-footprint content generation refuses a selected `copper_feature` with a
typed `ValueError` before emitting board content. Copper has no purchased MPN;
this scaffold stage does not synthesize its value, flags or binding fields.

## Declared design-rule sources (AGENTEE-331)

A producer plan may name a captured `constraint_source`, for example
`"constraint_source": "design-rules/constraints.json"`. Stage-one Python callers
can pass `constraint_source=Path(...)` to `scaffold`. The document uses the design
lane's existing `fome-mc.constraint-provenance/1` format. `board_setup_rules`
entries contain `value_mm`, `class` and a nonempty citation `basis`.
`net_class_default` can declare clearance, track width, via diameter and via drill
in millimetres. Classes are `owner`, `manufacturer`, `datasheet`, `engineering`
and `default`. Importing a citation does not certify its applicability. A declared
non-default net-class setting requires one explicit native `Default` class in
the project template; the importer refuses an implicit or ambiguous class.

The importer applies declared non-default values. A declared default does not
replace the template value. The captured source is immutable. The receipt's
`constraint_provenance` inventories every emitted board rule and numeric net-class
setting with its value, class, citation and enforcement. Values without a matching
declaration or verified producer source become `default`, with a null citation.
Source-verified producer fabricator limits retain their manufacturer citation. The same record is bound
into the immutable project's `agent_ee_constraint_provenance` field so native
staging and review can resolve the settings without searching for a document.
A change to a recorded setting refuses evaluation. KiCad may materialize
previously absent native defaults when it saves a board; these additions are
reported as unsourced advisories and acquire no hard authority.

Legacy template values remain available to native DRC as advisories. For a
scaffold with this binding, the packing probe uses only sourced board and
net-class margins in memory; it does not rewrite the saved project or pad shapes.
The native polygon calls receive explicit sourced margins, so cached native
net-class values and uncited local overrides cannot restore advisory floors.
Physical copper/drill exclusions and native owner custom rules still apply.
Unbound standalone packing probes preserve their previous reservations.

Native DRC reports retain every finding. Numeric findings resolve against the
reported rule name and the inventory's applicable settings. The measured actual
value is compared against every applicable sourced minimum, even when KiCad
names a larger default limit. The strictest violated sourced minimum supplies
the finding's class and citation; all violated sourced settings are retained in
`constraint_provenance.limits`. Four-decimal native values use the same
0.00005 mm comparison tolerance as printed-limit matching.
Class-specific limits use native conductor UUID/net facts and the existing exact-name
assignment contract. Legacy ambiguous identities or general/composite class patterns
remain unresolved. Ambiguous or absent provenance is `default` and advisory. Captured owner custom
rules have class `owner`, citing the file, SHA-256 and rule name. A declaration
that assigns another class to an owner rule raises `OwnerRuleReclassification`
before scaffold publication or native grading. An owner-class declaration cannot
replace the captured owner-file citation.
Generated net rules require an exact declaration to acquire hard authority.
Unclassified generated rules remain advisory, including when composed with owner
rules. The native gate's structural invariants retain engineering citations from
AGENTEE-318/325. See the [publication gate](placer.md#through-hole-reservations-and-native-publication-gate-agentee-318).

A captured owner `.kicad_dru` remains immutable. With generated per-net rules, the
scaffold preserves its original bytes in a sibling `.owner.kicad_dru`, then emits
one native `.kicad_dru` containing generated rules followed by the owner rules.
Both files are immutable requirements. Owner rules retain their native last-rule
precedence. A name already present in the captured owner file keeps its owner
authority when the generated prefix uses the same name. The receipt cites the preserved owner file and digest, and the net
constraint record hashes the final project and composed rule file.
Native rule files use KiCad's `#` comment syntax; semicolon comments are refused.

The document's `solder_mask_min_width` is retained as a manufacturing obligation;
it is not an invented board-setup field. The native candidate comparison includes
classified baseline and candidate findings, including silk and text violations.
Only sourced added numeric violations and the structural invariants
block publication. Co-located drilled holes and negative copper-edge clearance
(copper beyond the outline) are structural engineering findings, independent
of numeric defaults. Explicitly ignoring the co-located-drill check refuses
evaluation as unavailable structural coverage. The captured DRC check also retains raw counts and reports,
with classified evidence in `drc-constraints.json`; unsourced numeric violations
alone do not fail it. Tool failures, malformed output, parity, connectivity and
non-numeric defects keep their existing treatment. Ignoring a sourced numeric
evaluation is unavailable coverage and refuses; existing ignored-category and
rule-level ignore refusals remain. Review packets include the
source inventory and budgeted classified native evidence; reviewer and adjudicator prompts
require each blocking limit's class and citation.

The post-placement artwork pass uses the same captured source inventory for its
pre-artwork, relocation and final native comparisons. Saving the artwork cannot
promote materialized defaults or drop a sourced limit's class and citation.
Advisory numeric findings remain in the comparison and cannot alone refuse
publication after relocation; sourced numeric and structural regressions still
refuse. The artwork pass's carried legibility defects retain their separate
[downstream gate](silk-artwork.md#placement-feasibility-and-fab-legibility-agentee-333).

Plane production and the experimental board-build row stand-in also classify
native findings against the captured sources. Plane receipts retain raw native
DRC plus `drc/constraints.json` and `drc.classified_findings`; only numeric
advisories leave the planned-zone refusal set. A sourced or structural finding
refuses even at native warning/exclusion severity. Structural findings, malformed
metadata and isolated-copper policy refusals remain. Board-build parity records
retain `violations` and `classified_findings`, with `graded_violations` counting
only graded findings; the row stand-in uses that count for its clean flag.
Board-build carries the template provenance binding alongside its imported
board rules and net classes.

The router and native zone filler still consume template defaults when choosing
geometry. Classification changes no saved native rule value; resolving that
geometry policy and reconciling class authority with verified references are
separate follow-up work (AGENTEE-331 rulings G4/G5).

Thermal-spoke counts are dimensionless numeric limits. A native
`starved_thermal` finding with an unsourced minimum remains advisory. KiCad's
count report omits custom-rule names, so the source reader also inventories
unconditional integer `thermal_spoke_count` minima from captured owner rules.
It compares the measured count against that source even if the report prints a
different default. Count comparisons are exact; the millimetre rounding tolerance
does not apply. Missing count measurements refuse evaluation. Conditional or
layer-scoped sourced count rules cannot be attributed from these native reports;
when a count finding is present, their cited source is retained and evaluation
refuses as unavailable coverage. This is distinct from an unsourced default.
