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
zones. Holes, keepouts and fixed components remain work for later design stages;
scaffolding does not claim those envelope obligations pass. Copper weights
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

## Stage-two producer

`agent-ee scaffold ENVELOPE PARTS --plan PLAN --out DIR` (AGENTEE-141) derives
the scope/2 and the `.kicad_pro` design rules through the same module, then
publishes exactly as stage one does. `PLAN` is an `agent-ee.scaffold-plan/1`
with exactly these fields, none of which the envelope or parts manifest states:

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

### Default class clearance

The owner decided on 2026-10-18 that the Default clearance is the fab minimum, and
that the router targets preferred values where it can
([decisions](decisions.md), "Default net class clearance"). A native probe found
that KiCad 10 DRC enforces the Default class `clearance` as a hard floor, but not
the class track width, via diameter or via drill. Those three are graded only
against the board minimum rules. A preferred clearance in the class would fail
boards that meet the fab minimum, so every DRC floor the producer writes is a fab
minimum:

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
  Default via pad does not exceed its drill, or the limits name more than one
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
receipt. Net classes other than Default wait on AGENTEE-47 net constraints.
KiCad rule settings outside the six keys keep the template values. The producer
derives no `fab_package` done gate and no scope/3 envelope acceptance.

## Output and evidence

`DIR/inputs/` contains the complete selected inventory; `DIR/scope.json` uses
relative paths into it. `DIR/scaffold-receipt.json` records input and output
hashes, candidate and baseline identities, root/sheet identities, Python version
and hashes of the agent-ee policy modules. With `--plan` the receipt is
`agent-ee.scaffold-receipt/2`: it records `plan_sha256` in place of
`scope_sha256`, and a `derivation` naming the envelope, profile, template and
manifest hashes, each derived rule's setting, value, limit ID, tier and
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
envelope baseline also continues to refuse uncovered stackup and other
obligations. Supplying an envelope as an uncited generation input does not make
it an acceptance authority. Complete production generation needs those coverage
gaps and producer migrations resolved before the envelope can become binding.

[test_scaffold_producer.py](../tests/test_scaffold_producer.py) covers the
producer on owned synthetic envelope, profile and parts fixtures: byte-identical
outputs, the derived mappings, rules, class tiers, router targets and stages,
every refusal above, and two native runs. In the first, pcbnew loads the derived
rules and Default class from the produced project, and each of the five checks
reports `stage_not_reached` on the empty candidate. The second is the clearance
DRC run described above.

The original synthetic tests in [test_scaffold.py](../tests/test_scaffold.py)
exercise byte identity and capture hashes, native ERC/DRC, local library tables,
nested sheet creation, populated native library lookup with broken-URI controls,
immutable project rules, preparation refusal and the
existing inventory guard. The campaign test uses scripted model responses with
real native presence probes, coordinator seals, adjudication and writer guards.
It establishes the first repair transition, not live-model review quality or a
completed generation campaign.
