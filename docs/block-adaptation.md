# Block adaptation (AGENTEE-90)

The [fome-mc wideband fixture](fome-wbo1.md) supplies a captured 58-part source
and golden second channel. Its fixture writer uses this instantiation seam,
then applies references +100, net suffixes and the declared PB1/PA8 straps.
Native regression tests compare every adapted physical terminal and exercise
fresh provenance and interface checks.

A captured [reference block](reference-block-interface.md) can be instantiated
before candidate capture and adapted by the ordinary campaign writer. Its source
files and instance record are immutable verification inputs. Its copied child
sheet is a mutable design file. No result from a source run enters the board's
check results, even when the child bytes match the source exactly.

## Instantiate before capture

The scaffold caller prepares a candidate directory containing the captured block,
a root schematic that references one pre-created empty child sheet, and the board's
unstaged scope/2 or scope/3 checks and baseline. The scope's input selections must capture
these files, the block directory and the new instance record. The adapter fills
the existing child byte-for-byte; it does not create a schematic hierarchy or
choose board checks. Full empty-candidate generation belongs to AGENTEE-87.

Call `agent_ee.block_adaptation.instantiate(candidate_directory, scope, request)`
to get a new scope without mutating the supplied scope object. A CLI writes that
scope to a new file and resolves its input paths relative to the original scope:

```sh
PYTHONPATH=src python3 -m agent_ee.block_adaptation request.json \
  --candidate scaffold --scope scaffold/scope.json --out board.scope.json
```

Example request (paths are candidate-relative; the UUID is the native **sheet
symbol** path, without the root document UUID):

```json
{
  "id": "filter",
  "block": "blocks/rc",
  "sheet": "project/filter.kicad_sch",
  "instance_path": "/62e7fa6a-3fb5-4e82-953c-0c76f667cf92/",
  "bindings": {"VIN": "/IN", "VOUT": "/OUT", "GND": "/GND"},
  "requirements": {"response": ["board-response"]},
  "record": "instances/filter.json",
  "provenance_check": "filter-provenance",
  "netlist_check": "board-netlist"
}
```

The record's parent directory must exist. `bindings` names every source boundary
port exactly once and gives its required, exact board-native net name. The
`requirements` mapping names every block requirement and selects fresh board
checks already declared in the scope. Those checks must account for board net
names and instance identities; the adapter does not infer engineering limits or
rewrite arbitrary check parameters. A provenance check cannot cover a block's
functional requirement.

The adapter writes `agent-ee.block-instance/1`, retaining the request's instance
ID, block directory, sheet, UUID path, bindings and requirement mapping, plus
`block_id`, `version` and the source sheet's `sha256`. It appends:

- One baseline requirement per block requirement, preserving its statement,
  sourcing it from `block.json`, mapping it to the selected board checks, and
  prefixing its ID with the instance ID.
- A boundary requirement sourced from the instance record, bound to the new
  `block_provenance` check.
- The block descriptor as a reference architecture and the child as a mutable
  input.

All block payloads, `block.json` and the instance record are bound into the
baseline in scope/2 and scope/3. The existing campaign proposal guard refuses
changes to those files; a changed binding contract needs a new baseline. The
writer may repair values, parts and wiring in the copied child. A lost or rebound
boundary remains a defect until repaired against the frozen binding contract.
Instantiation refuses a nonempty or repeated destination, unsafe paths and
incomplete mappings. Validation failures restore the empty child and remove the
new record. Source blocks with unavailable source runs remain provenance inputs;
no source acceptance is inferred. Source parts manifests must still satisfy the
shared parts/1 structural validator; malformed manifests are refused before the
child is written. Source membership qualification remains separate from fresh
board acceptance.

## Fresh provenance check

The check takes exactly `instance` and `netlist_check`. The latter must name an
earlier `netlist` check over the board's scope schematic. It consumes that
candidate's full native XML and checks its connectivity projection and hierarchy
coverage. It exports the captured source sheet in the networkless executor,
reads both through the shared native inventory reader, and compares corresponding
physical components by symbol UUID. Added, removed and changed components retain
before/after properties, including resistor values. Nets retain changed physical
terminal sets and names, allowing for declared source-to-board bindings and the
child sheet's namespace. Derived `Sheetname` and `Sheetfile` properties are
excluded from component changes.

A byte-identical child reports `same as block vX, sha256 ...`; fresh board checks
still execute. Component and net changes are informational. A missing child
hierarchical label, missing parent sheet pin, changed port direction, or native
binding to a different net is a defect.

Boundary net observations use a retained scratch copy of the captured hierarchy.
The adapter adds original passive one-pin probes at the native hierarchical-label
anchors and exports this copy with KiCad. It compares the export with the board's
unaltered native graph after removing the probes. Every original component,
property and net membership must match exactly, excluding the exporter's derived
`Sheetname` and `Sheetfile` metadata. Only then can each probe witness
the port's actual board net. A port must connect to an original physical terminal
on its declared net. This catches moved/disconnected labels as well as renamed
parent nets; source metadata alone cannot establish connectivity.

`work/CHECK_ID/provenance.json` contains the instance identity, byte comparison,
component/net changes and per-port observations. Source XML, instrumented sheets,
boundary XML and executor receipts are retained and sealed by the existing
coordinator evidence mechanism. Failed exports, absent or malformed artifacts,
unsupported identity mappings and changed physical graphs leave coverage unknown.
A source run's passing status is never an input to this decision.

Registration changes the whole-file `checks.py` fingerprint used by
[passive-corner qualification](passive-corners.md). The shipped contract's parser
pin is refreshed after comparing the unchanged producer/parser definitions and
model, and rerunning the native analytic acceptance corpus. Its policy bounds
remain fixed.

## Qualified scope

The adapter supports a flat source block instantiated as one unique child-file
occurrence in a captured hierarchy. It refuses source blocks declaring multiple
`instances`, before writing the child or instance record; exporting only their
primary sheet cannot establish provenance for the other source instances.
The [pod CAN capture](pod-can.md) exercises this refusal. It does not support
nested blocks or repeated uses of the same child file. The strict native reader's
component and terminal
limits apply, including the temporary boundary probes. Native XML omits virtual power markers, so ERC and other declared
board checks remain necessary. The semantic report covers native physical
components and connectivity; it does not grade graphical layout or PCB geometry.
The standalone adapter is the scaffold integration seam; the experimental rung-1
generator does not invoke it automatically. A scaffold that uses staged campaigns
must assign and order the new provenance check in its stage declaration after
instantiation; passing an already staged scope to this adapter is unsupported.

These controls use original synthetic fixtures, native KiCad exports and ngspice.
They establish software contracts and the ideal RC response, not physical-board
qualification or third-party publication rights. No owner evidence store is
created.

## Explicit selected-reference composition (AGENTEE-175)

A scope/3 request may additionally name a captured `composition` JSON path.
Its `agent-ee.block-composition/1` plan covers every captured source instance,
selected reference and native net; it declares source hashes, source-to-delivered
reference/net maps, eight or other explicitly enumerated ports, and reasons.
The [pod CAN plan](../fixtures/fome-mc/pod-can/composition.json) is the supported
native example. The plan must be captured as an immutable input. The existing
instance/1 record adds `composition: {path, sha256, emitted_sha256}`; absent means
legacy copying. No source block schema or verified status changes.

Only flat, explicit single-unit symbols with resolved visible pins are supported.
The source-native net map is injective within each source; cross-source joins are
explicit shared destinations. Port aliases must name a canonical port, and their
board bindings must agree. Distinct canonical ports cannot share a board binding.
Explicit no-connect terminals are dispositions, not source nets or ports. The
source must retain one isolated marker on each such physical pin. Its native
membership must be exactly the single physical terminal in an auto-unconnected
net, with the reader's `no_connect_marker` witness from `+no_connect` pintype.
Missing membership is a violation, never evidence of isolation. A generated
`unconnected-(...)` name or a library pin's `no_connect` type alone cannot supply
this evidence. All physical-pin and source-geometry conditions remain required.
Missing or ambiguous source geometry refuses composition. The emitted child
retains the markers; fresh provenance treats a lost marker or newly connected NC
pin as a defect. A proved connection is classified before geometry isolation,
so a retained marker touching its new conductor cannot hide the defect or other
provenance findings. Otherwise unsupported geometry leaves coverage unknown.
Source netlist-only requirements remain informational and must map to fresh native
netlist checks. All other source requirements still need fresh acceptance checks.
The producer performs native source exports before writing the child; subsequent
baseline failures restore the empty child and remove the new instance record.

Composition provenance grades selected physical/pin differences as defects and
retains both source exports. Legacy single-source adaptations continue reporting
component/net changes as information. Multi-source requests without an explicit
plan remain unsupported. Neither mode supplies source qualification or electrical
suitability from provenance alone.

For root selected-reference compositions, a `Device:R` or `Device:C` whose only
native difference is `AGENT_EE_Ratings` may carry refreshed catalog evidence.
The checker resolves both native fields through their captured producer receipts
and reports. The original receipt comes from the validated source block's
`build-generated/parts-ratings` capture; the current receipt must be declared by
the current assembly checks. Both catalog responses must match captured hashes.
Only catalog-response digests may differ in the full derived records: part
identity, rating value and units, source paths and attributes, limitations, and
all other component properties remain exact. Datasheet changes are not catalog
refreshes. The report retains these before/after changes as `rating_refreshes`;
other component and pin-net changes remain defects. This records derivation
custody and does not replace fresh assembly or source qualification.

## Flat intent composition (AGENTEE-176)

`block_adaptation.compose_flat(root, intent, blocks, bindings,
captured_libraries=...)` composes selected captured components into the existing
`agent-ee.connectivity-intent/1`. It returns an `intent`, a path-to-bytes `captures`
map, and immutable `block_instances` sidecars. It writes no project files. The
caller publishes the returned bytes into its owned scaffold, captures the
sidecars and source dependencies, and uses the ordinary schematic stage to emit
and later repair the mutable intent. Intent/2 hierarchy is outside this path.

Each block request contains exactly `id`, `block`, `composition`,
`reference_ranges`, `sheet`, and `requirements`. `block` and `composition` name
captured paths beneath `root`; `sheet` names the eventual flat root schematic.
`requirements` maps every original block requirement to fresh board checks, as
in hierarchical instantiation. The current path requires an explicit selected
reference composition plan, including every source instance and pin net; it
does not infer a selection or silently import a whole source sheet.

For example, `reference_ranges` can be
`{"C": [100, 109], "D": [100, 109], "R": [100, 109], "U": [100, 109]}`.
Numeric source-reference order allocates each prefix from its declared lower
bound. Existing references anywhere in the range, overlapping ranges between
instances, insufficient ranges and logical component-ID collisions refuse.
Flattened logical IDs are `<instance-id>/<selected-reference>`, such as
`can/R9`; the display reference can become `R100`. Input ordering does not change
the result. The caller must explicitly choose ranges; allocation never hunts
for an apparently free reference outside them.

`bindings` maps each instance ID to all its logical ports and named intent nets.
The CAN block requires eight entries, including `S` and `GND` bound to the same
net. It retains the source ground strap. Distinct canonical source nets cannot
merge implicitly. A boundary with only one selected physical pin must gain a
real surrounding-board terminal before the complete intent can resolve. Missing
connections are refused; the producer adds no dummy components and does not
weaken the resolver's singleton or pin-accounting checks.
Captured no-connects instead become explicit `intent.no_connects` terminals, with
every physical pin retained. They are not singleton nets and cannot be exposed as
composition ports. Flat provenance also rejects lost markers or connected NC pins;
other adaptations retain the reporting behavior below.

Symbols come from the captured native source definitions, never ambient symbol
search. Derived libraries use deterministic instance-specific namespaces and
hashes; the returned capture map must be published with the intent. Old
`pin_names` hide-token ordering is serialized in the existing emitter's order.
Native empty pin names and datasheet values are preserved explicitly, preventing
legacy `~` placeholders or embedded-library defaults from silently changing
source metadata. The generated libraries remain derived from the recorded
upstream sources. No new licensing permission follows from composition.

Legacy source properties containing only `~` mean empty text before KiCad's
20250318 format boundary. Composition writes those as explicit empty strings,
so the derived library and modern emitted schematic have the same meaning.
Libraries derived only from those legacy sources retain version 20241209.
If a library also includes definitions from a source at or after 20250318, it
uses the 20250318 library format so literal tildes remain literal. This is a
text-semantics boundary, not an unconditional upgrade to the installed KiCad
library version or a claim of support for arbitrary future format features.

### Immutable flat instance record

Each `agent-ee.flat-block-instance/1` sidecar records:

- Original block ID, version, primary sheet hash, and captured plan path/hash.
- `source_parts`: original sheet, native source instance and source reference,
  keyed by selected reference. Both CAN source instances remain accountable.
- `references` and `reference_ranges`: selected-to-board reference mapping and
  the reserved ranges.
- `bindings`: named intent nets, and `native_bindings`: their exact expected
  KiCad XML names. Ordinary flat local labels receive the native `/` prefix;
  resolved virtual power symbols make their nets global. The receipt declares
  the distinction instead of stripping prefixes during comparison.
- The flat root `sheet`, `instance_path: "/"`, and original requirement mapping.

Keep these sidecars immutable across writer turns. They are separate from the
mutable intent and are not opaque ignored intent fields. Add an ordinary
`block_provenance` check with `params.instance` naming the sidecar and
`params.netlist_check` naming a prior fresh native netlist check. Capture the
original block, plan, sidecar, root schematic and fresh board requirement inputs.
Preserve the original requirement statements and source locators when binding
fresh checks; native source-export-only requirements remain informational.

The flat checker freshly exports both source sheets, compares mapped component
identities and all selected physical pin memberships with the delivered root
netlist, and names component and net changes as **adaptations**. Generated UUIDs
are not compared to source UUIDs; reference and source-instance mappings supply
custody. Unexpected components inside the reserved reference ranges are also
reported. Unrelated board components outside those ranges belong to the board's
other requirements.

Verdicts have a narrow meaning:

| Observation | Outcome |
| --- | --- |
| Source identities/topology and boundary bindings unchanged | `pass` for this provenance predicate |
| Component adaptation, such as R9 120 ohm to 60 ohm, with boundaries intact | `info`, never approval |
| Missing/rebound selected boundary terminal | `defect`, with the topology adaptation retained |
| Lost no-connect marker or newly connected captured no-connect pin | `defect` |
| Missing, unreadable or inconsistent source/capture/native evidence | Unsupported/unknown coverage |

A reported adaptation does not qualify the source or approve the changed circuit.
A 60-ohm resistor still fails the fresh interface requirement that declares
120 ohms. Part-decision authority, source qualification under AGENTEE-149/204 and
all other board requirements remain independent. The existing hierarchical
selected-composition path continues to treat source-component changes as defects.

### Part selection handoff to AGENTEE-174

The ticket's `part_ref` vocabulary is distinct from captured source provenance.
Intent/1 uses its existing `part_binding` fields; `source_parts` identifies the
upstream source component. A source reference or LCSC string does not select a
supply-manifest offer.

The coordinated binding-fill interface uses a separate typed immutable
`agent-ee.part-refs/1` sidecar with `components` mapping exact intent component IDs
to `{ "part_ref": "<explicit parts/1 part id>" }`. The caller explicitly maps
`can/R9` and every other non-power component to its manifest choice.
AGENTEE-174 owns exhaustive coverage validation and supplied-binding mismatch
refusal. This producer does not implement or bypass that validator, invent part
IDs, or add fields that intent/1 ignores.

`tests/test_block_no_connects.py` exercises offline Git capture, synthetic XML
reading, composition, connectivity resolution and schematic emission on the CPU.
Only the native process boundary is stubbed. It covers both library NC pins and
ordinary pins with explicit NC markers, and rejects connected/missing terminals,
absent memberships, missing export marker witnesses, unproven geometry, false
boundary ports and claimed port currents. The hierarchical control compares the
emitted child with rebound references, UUIDs and sheet paths in authored XML;
removed-marker and connected-with-marker mutations produce defects, retaining
other provenance findings. This XML is a CPU substitution, never export proof.
Actual source and flat/hierarchical exports must independently demonstrate the
auto-unconnected singleton and `+no_connect` witness before native admission.
These tests do not qualify KiCad export behavior or an acquisition role. The retained acquisition
source capture and its open obligations are described in
[the source record](qualification/acquisition-block-source-2026-10-28.md).

## Exact role membership and deferred requirements

`parts_subset` optionally accepts `reference_set`: a sorted, nonempty list of
`{"sheet":"/native/occurrence/","reference":"U1","uuids":["unit-uuid"]}`.
Occurrence segments and every placed-unit UUID must be canonical UUIDs. Root
occurrence is `/`. The complete manifest declaration must equal this set;
all selected units must match the native export. The report retains the full
native inventory/hash and sheet census, and reports selected and outside counts
separately. Outside components are outside this predicate, not qualified.
Omitting the parameter preserves whole-inventory grading, including unexpected
populated components. Ambiguous global parsing or exporter omissions still block.

Explicit-instance reference blocks require exact real-manifest membership and
exact passing native rows for every declared occurrence/reference/unit. A source
membership requirement can be credited only by those independently checked scoped
parts results. A full-board manifest, one matching row or a different role cannot
supply that credit. Both source membership and interface requirements still need
a complete, ordinarily reviewed and sealed passing run. No such acquisition run
is created by these changes.

A requirement may declare `verification_phase` as `source` or `adaptation`;
absence means `source`. Source qualification reports adaptation requirements as
`mandatory_on_adaptation` / `not_evaluated`, never covered. They retain nonempty
check contracts, captured source citations and explicit conditions. Ordinary
pending requirements still prevent source qualification. Adapting a block with
legacy pending requirements refuses; authors need a new version with recorded
explicit dispositions. Historical versions are not rewritten or promoted.

Every flat and hierarchical instance mapping must name the exact union of source
and adaptation IDs, with nonempty unique fresh check lists. Adaptation requirements
always have acceptance intent and require scope/3 executable coverage. Readers
and the existing provenance capability repeat these checks. Declared check
families and parameters must survive rebinding of captured reference, occurrence,
net and source-file identities. Interface/interval assets must preserve their
invariants/algebra and bind the current requirement ID and block source locator.
Fresh scoped parts manifests must preserve allocation and part identity and use
unit UUIDs from the authored current sheet. Checker names alone, unrelated ERC,
netlist-only or provenance checks do not discharge deferred obligations. Existing
capability/baseline compilation remains authoritative; no source run result is
copied into board evidence.

This transports immutable conditions; it does not decide whether a new application
satisfies them. The finite acquisition role consumer remains a separate missing
edge. `acquisition_prepare` still accepts its original strict interface and does
not consume these roles. Its unknown role-selection inputs remain refused. A later
consumer must verify the selected context, including zero external J2 load and no
external drive, against the source-bound power/rail/bus conditions before seal.
Return-path and engineering judgments remain existing qualification obligations;
there is no `judgment` check kind, and a check template is not a judgment result.

Standing CAN topology regressions derive `1-topology-regression-2` in owned test
setup, preserving the archived block byte-for-byte and its exact historical
`pod-can-standalone` capability blocker in hashed lineage. The new fixture requires
both the fresh interface acceptance and separate generated two-source provenance
acceptance. Its topology/native assertions remain mandatory; this fixture does
not establish electrical, supplier, physical or reference qualification. The
archived pending version still refuses through both ordinary adaptation paths.
