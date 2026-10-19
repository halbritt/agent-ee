# Schematic campaigns from mutable intent

`agent_ee.schematic_stage` (AGENTEE-173) declares two coordinator steps for a
schematic turn. The sole writer edits captured connectivity intent and optional
block seeds. `emit-schematic` resolves that turn's raw intent and emits the
schematic; `forward-annotate` exports its native XML and applies it to the board
through the existing [forward annotator](forward-annotation.md). Both steps run
in the ordinary networkless [receipt sandbox](stage-receipts.md). Native checks
and independent review still grade their outputs.

Prepare an initial captured tree with these files:

- `generation/connectivity-intent.json`: raw `agent-ee.connectivity-intent/1`
- `generation/schematic-layout.json`: `agent-ee.schematic-layout/1`
- `generation/footprints.json`: `agent-ee.footprint-libraries/1`
- `libraries/`: explicitly captured symbol and footprint members
- Native schematic, board and project files, including any library tables
- The requirements, target specifications and reference architecture for the scope

The files, including every optional block seed, must already be in the capture.
The campaign's existing inventory guard refuses additions, deletions and links.
This module does not compile a pod specification or choose components.

```python
from agent_ee.schematic_stage import configuration

stage = configuration(
    '/absolute/agent-ee/src',
    board='project/pod.kicad_pcb', schematic='project/pod.kicad_sch',
    seeds=['generation/can-block-seed.json'])
scope['mutable_inputs'] = stage['mutable_inputs']
campaign['implementation_steps'] = stage['implementation_steps']
```

Use an ordinary scope/2 or scope/3 with a declared `schematic` stage and its
checks. Add later stages and their declared steps to that campaign as needed.
The configuration helper accepts explicit paths for `intent`, `layout`,
`captures` and `footprints`. It returns declarations rather than changing a
scope, requirements or baseline. Block seeds are writer context, not another
connectivity syntax; the writer incorporates their connectivity in the intent.

`mutable_inputs` includes the intent, seeds, schematic and board because the
coordinator needs write authority for its native outputs. On a schematic turn,
the existing `writer_step_output_edit` guard refuses writer changes to either
native file before any step runs. Layout, libraries, footprint manifest, project
rules and all baseline sources remain immutable. Rung 1 keeps its existing
compiled immutable intent and step declarations unchanged.

The retained writer prompt lists the declared step inputs, including the raw
intent and block seeds, alongside the step-owned outputs.

Each step receipt hashes the entire delivered input tree. The optional step
`inputs` list additionally requires that the final intent, seeds, layout and
footprint manifest equal the bytes consumed by each producer's last adopted
receipt. A later input edit fails done as `stage_step_stale_input`. Every producer
with declared inputs needs its own receipt, even when two steps share a stage.
Legacy step declarations without `inputs` keep their existing meaning.

The annotation step re-emits the current raw intent in memory and compares it
byte-for-byte with the delivered schematic before exporting XML. A stale
schematic is refused. It uses the existing native inventory reader, footprint
manifest loader and sandboxed pcbnew plan/apply path, preserving retained
placement and copper. Its receipt also binds the current intent to the saved
board. Generated project files are retained artifacts; the captured project
rules are never adopted from the emission.

An unresolved pin, an unaccounted pin or missing library coverage produces named
findings in the sealed `out/emission.json` artifact, with outcome `unknown` and
no schematic output. The step exits 1; adoption refuses it and the campaign
stops incomplete. These are diagnostic stage findings for the writer/operator,
not adjudicated check defects, and no previous schematic is substituted. This
path does not automatically retry a failed producer. Fresh checks and reviews
are required after a corrected proposal.

The adapter currently supports flat intent. Hierarchical emission remains in the
standalone emitter; this adapter refuses multiple schematic outputs so the
campaign inventory and declared write scope remain exact. Placement, routing,
fabrication and the telemetry pod's full acceptance scope are separate work.

Tests use original regulator fixtures and scripted model responses with actual
KiCad XML exports, sandbox receipts and forward annotation:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest \
  tests.test_campaign.MutableIntentSchematicCampaignTests \
  tests.test_schematic_stage tests.test_stage_done -v
```
