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

The command requires an explicit stage-one scope/2 or scope/3. Neither the
[envelope](board-envelope.md) nor the [parts manifest](approved-parts.md) defines
all requirement/check mappings, targets, reference architectures or qualified
project rules. The command preserves these from `PLAN`; it does not invent
requirements or lower their coverage. The two-positional-argument interface
without `--scope` and automatic compilation of fab-profile rules are not yet
available.

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

## Output and evidence

`DIR/inputs/` contains the complete selected inventory; `DIR/scope.json` uses
relative paths into it. `DIR/scaffold-receipt.json` records input and output
hashes, candidate and baseline identities, root/sheet identities, Python version
and hashes of the agent-ee policy modules. It has no timestamp or absolute
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

The original synthetic tests in [test_scaffold.py](../tests/test_scaffold.py)
exercise byte identity and capture hashes, native ERC/DRC, local library tables,
nested sheet creation, populated native library lookup with broken-URI controls,
immutable project rules, preparation refusal and the
existing inventory guard. The campaign test uses scripted model responses with
real native presence probes, coordinator seals, adjudication and writer guards.
It establishes the first repair transition, not live-model review quality or a
completed generation campaign.
