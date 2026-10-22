# Pod CAN reference sets (AGENTEE-175)

[`fixtures/fome-mc/pod-can/block/`](../fixtures/fome-mc/pod-can/block/block.json)
captures U307 (TJA1051T/3) and C308 (100 nF) from `wbo1`, plus D4
(PESD1CAN-UX) and R9 (120 ohm, C22787) from `psu`. Both sheets come from
fome-mc Git objects at `df304f3321296f7e517e0d2e2aa52e1380d52993`.
The block requirements record the owner's accepted departure, composed from two
sheets (2026-10-18). The block source records the combined upstream origins and
licences under the existing [fome-mc notice](../fixtures/fome-mc/LICENSE-NOTICE.md)
and origin T16; the repository's MIT license does not cover those captures.

## Reproduce the capture and native audit

The fixture helper uses `reference_blocks.capture_block`. Source sheet bytes
come from the pinned object store; generated supplements come from the committed
block. Use new destinations for both commands:

```sh
mkdir -p build
PYTHONPATH=src python3 fixtures/fome-mc/pod-can/audit.py capture \
  --repo ~/git/fome-mc --out build/pod-can-capture
PYTHONPATH=src python3 fixtures/fome-mc/pod-can/audit.py prepare \
  --out build/pod-can-audit
PYTHONPATH=src python3 -m agent_ee.cli review build/pod-can-audit/scope.json \
  --out build/pod-can-run --checks-only --max-seconds 180
AGENT_EE_REQUIRE_TOOLCHAIN=1 AGENT_EE_FOME_MC_CHECKOUT=$HOME/git/fome-mc \
  PYTHONPATH=src python3 -m unittest tests.test_pod_can -v
```

The capture helper prints `unverified`. This is successful acquisition with
missing qualification. The checks-only review exits 1 with `incomplete` because
independent reviews and adjudication have not run. Inspect its check results:
`netlist` should report `ok/info`, and `interface` should report `ok/pass`.
The regression captures twice inside the existing networkless executor sandbox
and compares every byte with the committed block.

The native audit uses an original parent to connect the two unchanged sheets.
On KiCad 10.0.7 it exports 97 components across three sheet occurrences. The
selected four components retain their native identities, populated states,
values and pin nets. The interface contract checks these connections:

| Logical signal | Native terminals | Audit net |
| --- | --- | --- |
| CAN+ | U307:7, D4:2, R9:1 | `/CAN+` |
| CAN- | U307:6, D4:1, R9:2 | `/CAN-` |
| VCC | U307:3, C308:1 | `+5V` |
| VIO | U307:5 | `+3V3_WBO1` (source's 3.3 V rail) |
| GND | U307:2, U307:8, D4:3, C308:2 | `GND` |
| TXD | U307:1 | `/wbo1/CAN_TX` |
| RXD | U307:4 | `/wbo1/CAN_RX` |

VCC and VIO must occupy different native nets. The negative control disconnects
only U307:5's source wire and joins that pin to `+5V`; a fresh native export then
reports both the VIO named-net and supply-separation violations. Disconnecting
the wideband bus from the PSU bus fails the cross-sheet join predicates.

## Selected standalone composition

The source reference-set capture remains unchanged and unverified. Use the
explicit [composition plan](../fixtures/fome-mc/pod-can/composition.json) and
[producer helper](../fixtures/fome-mc/pod-can/compose.py) to emit only U307,
C308, D4 and R9 into a single child:

```sh
PYTHONPATH=src python3 fixtures/fome-mc/pod-can/compose.py --out build/pod-can-selected
PYTHONPATH=src python3 -m agent_ee.cli review build/pod-can-selected/scope.json \
  --out build/pod-can-selected-run --checks-only --max-seconds 180
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest \
  tests.test_block_composition -v
```

The generated child has eight hierarchical ports: CAN+, CAN-, TXD, RXD, S,
+5V, +3V3 and GND. There are seven electrical nets: **S remains grounded**,
exposed as an explicit GND alias, not an independent control. The plan declares
`+3V3_WBO1 -> +3V3`, `/CAN_TX -> TXD`, `/CAN_RX -> RXD`, and both sources'
CANH/CANL aliases. Voltage domains are declared requirements, not measurements;
current limits remain unspecified. Parent supply labels preserve the exact bare
native names `+5V`, `+3V3`, `GND`; signal names are `/CAN+`, `/CAN-`, `/TXD`, `/RXD`.

The helper uses ordinary `block_adaptation.instantiate` with a captured
`composition` path. Every source instance, selected reference, native source net,
reference rename and port alias must be explicit. The producer exports both
immutable source sheets through the networkless native executor, resolves pins
using shared symbol/geometry helpers and emits the selected symbols with their
embedded definitions, UUIDs, properties and connectivity. Missing or duplicate
references, conflicting libraries, unconnected/unmapped selected pins, nested
sources, inherited symbols, hidden pins and multi-unit symbols are refused.

`block_provenance` independently re-exports both sources and compares all selected
physical identities/properties and pin nets against the delivered native XML.
Component and connectivity changes in this mode are defects; they cannot be
reported as an unchanged composition. Eight existing native boundary probes also
check the actual parent/child joins, including S/GND. The instance record binds
the plan hash and deterministic emitted-sheet hash; the provenance report retains
both source hashes, reference/net maps, pinned commit and native export evidence.
Missing tools or malformed sources stay unknown. A composition-free multi-source
request still refuses before any adaptation write. Legacy single-source copying
and informational adaptation differences retain their existing behavior.

The checks-only run has four components across two sheet occurrences; native
interface and both-source provenance pass. Changing VIO to +5V yields interface
and provenance defects; changing either source payload invalidates custody. The
separate 97-component whole-sheet audit above remains source audit evidence only.

## Qualification gates

The original block's `pending_requirements` describes the unchanged reference-set
capture, whose primary native boundary still contains unrelated wideband ports.
The separate captured plan and generated child now supply the selected composition;
they do not rewrite that historical block or fabricate a qualified source run.
Draft parts, undeclared source boundary limits and absent complete sealed source
reviews keep the capture `unverified`. AGENTEE-149/204 source-review qualification,
fresh board checks, ERC/DRC, independent engineering review and physical checks
remain open. This producer is an input for AGENTEE-176, not an accepted pod design.

## Flat staged-generator consumer

[Flat composition](block-adaptation.md#flat-intent-composition-agentee-176) converts
the explicit four-part capture into intent/1 with deterministic reference ranges,
immutable both-source provenance and fresh native adaptation reporting. The
positive control connects a real seven-pin board interface; it is not a completed
pod board. Source review and full-pod qualification remain open.
