# Pod CAN reference sets (AGENTEE-175)

[`fixtures/fome-mc/pod-can/block/`](../fixtures/fome-mc/pod-can/block/block.json)
captures U307 (TJA1051T/3) and C308 (100 nF) from `wbo1`, plus D4
(PESD1CAN-UX) and R9 (120 ohm, C22787) from `psu`. Both sheets come from
fome-mc Git objects at `df304f3321296f7e517e0d2e2aa52e1380d52993`.
The block requirements record the owner's accepted departure, composed from two
sheets (2026-10-18). The block source records the combined upstream origins and
licences under the existing [fome-mc notice](../fixtures/fome-mc/LICENSE-NOTICE.md)
and origin T16; the repository's MIT proposal does not cover those captures.

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

## Missing standalone composition and provenance

This capture is a reference set, not a standalone pod subcircuit. The native
audit retains all unselected source-sheet components, including the PSU's
MCP2551 transceivers. It provides no bus-load or functional CAN evidence.

The requested eight logical ports and voltage domains are listed in the
captured `requirements.md`. They are not native standalone boundary ports.
The block format requires `boundary_ports` to equal the unchanged primary
sheet's hierarchical labels, which include unrelated wideband signals.
Supply nets, TXD/RXD and grounded S are not all hierarchical labels in that
sheet. VIO keeps its source name `+3V3_WBO1`; it has not been renamed to `+3V3`.
No new voltage or current limits are inferred for unrelated source ports.

`block_adaptation` copies and probes one flat source sheet. It now refuses a
block with multiple source instances before writing a child or instance record,
so the secondary PSU sheet cannot silently disappear from a provenance claim.
The regression exercises that refusal against this actual two-instance capture.
No passing composed `block_provenance` result is claimed. Completing AGENTEE-175
requires a selected-reference composition producer with native logical ports
and provenance evidence bound to both source occurrences.

`pending_requirements` records this missing coverage. Draft parts, undeclared
boundary limits and absent complete sealed source runs also keep this block
`unverified`. No AGENTEE-149 review result is assumed or imported. Fresh board
checks, independent review, ERC/DRC and engineering qualification remain
necessary; passing the audit does not establish a verified block or pod design.
