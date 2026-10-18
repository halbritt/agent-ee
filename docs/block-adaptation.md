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
no source acceptance is inferred.

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
occurrence in a captured hierarchy. It does not support nested blocks or repeated
uses of the same child file. The strict native reader's component and terminal
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
