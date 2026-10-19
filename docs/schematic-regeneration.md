# Stable schematic regeneration

`agent_ee.schematic_regenerate.regenerate_schematic` (AGENTEE-43) updates a
captured flat, wire-free schematic from its last emitted intent and a new intent.
It reuses the [emitter](schematic-emitter.md), shared S-expression reader and
[design diff](design-diff.md). It runs offline, adds no dependency, and returns
diagnostic evidence. Native ERC, netlist parity and independent review still
decide whether the repair meets requirements.

```python
from agent_ee.schematic_regenerate import regenerate_schematic

report, files = regenerate_schematic(
    previous_schematic_bytes, previous_effective_intent_bytes,
    new_intent_bytes, captured_library_bytes, layout_bytes)
if files is None:
    raise ValueError(report['findings'])
```

The library mapping and layout use the emitter's existing contracts. Both
intents resolve afresh against captured libraries; changed or missing captures
are refused. Nothing is written or mutated by this function. `files` contains
the new schematic and a scaffold project file; a campaign adopts only its
declared outputs and keeps its captured project rules.

## Correspondence and placement

Components match by reference first. Remaining components match only when their
library identity, units and pin-to-net-name/no-connect signature identify a
unique prior component. If both a reference and its connectivity changed, an
otherwise unclaimed identical logical ID is the final continuity anchor.
Values do not break ties. Ambiguous correspondence
refuses the whole update. A reference match with different library identity or
units also refuses; replacement of a symbol's pin geometry is not qualified.

Matched units keep their actual position, rotation, mirror, symbol UUID and pin
UUIDs, including IDs assigned by KiCad. The root UUID remains unchanged. Fields
keep their actual placement, visibility, formatting and custom properties.
An intent field change updates its text in place; if a human changed that same
field differently, the update reports `human_edit_conflict` and returns no files.
A human-only field change remains in the schematic; it does not rewrite intent.

New units occupy grid cells clear of every retained unit's conservative bounds,
pin labels and visible fields, with a 5.08 mm gap. The smallest supported paper
at least as large as the previous paper is retained or selected. Existing units
never move to make room. These bounds do not establish schematic readability.
Deleted components lose their generated pin labels and no-connect markers;
remaining pin decorations are regenerated at retained endpoints. ERC and native
connectivity must still be run after removal.

A prefix-only reference, such as `R` or `C`, keeps its previous number for the
same logical ID. New prefixes receive the first free positive number in
logical-ID order, reserving all prior and explicit new references. A connectivity
match also retains its prior reference when the new request is prefix-only.
The returned `effective_intent` contains final references; retain it as the next
`previous_intent`. An explicit new numbered reference is reported as a rename.
Existing references are never reassigned to make room for a new prefix.

## Captured evidence and conflicts

The `agent-ee.schematic-regeneration/1` report contains:

- `previous_schematic_sha256`, exact hashes under `inputs` for both delivered
  intents and the layout (canonical JSON for Python objects), and canonical JSON
  `previous_intent_sha256`, `intent_sha256` and `layout_sha256`.
- `implementation` with this module's hash; library hashes remain in the intents.
- `changes` with every retained unit (`kept` or `renamed`), added unit and removed
  component. Each entry names the before/after references. No retained unit moves.
- `diff`, the existing `agent-ee.design-diff/1` report, including typed changes,
  structural/placement/identity hashes and byte hashes of both schematics.
- `effective_intent`, generated file hashes and the report's `canonical_sha256`.

Before updating, the helper reconstructs the old emission in the candidate's
actual transforms. Changes outside symbol fields, transforms and valid UUIDs
(including wires, pin labels, embedded libraries, flags or reference inventory)
are conflicts. The report includes `conflict_diff` for those differences. It
does not merge native connectivity edits into intent. A malformed or truncated
prior schematic, duplicate UUID, unresolved intent, ambiguous match or lack of
space yields `execution: failed`, `outcome: unknown`, named findings and no files.
It never retries with a full overwrite. A duplicate UUID introduced by a new
component is also refused.

## Campaign stage

Opt into the existing [mutable-intent stage](intent-schematic-stage.md):

```python
from agent_ee.schematic_stage import configuration

stage = configuration(
    '/absolute/agent-ee/src', board='project/pod.kicad_pcb',
    schematic='project/pod.kicad_sch',
    regeneration_intent='generation/emitted-intent.json')
```

Pre-create `generation/emitted-intent.json` in the initial captured tree with
the intent that produced the initial schematic. `emit-schematic` adopts the
updated schematic and the new effective intent through the existing receipt
write map. The previous intent is a step-owned output, protected from writer
edits like the schematic. Its consumed bytes are bound by the whole delivered
tree and the regeneration report; it is not an immutable baseline input.
`forward-annotate` consumes the adopted state, verifies a repeated update gives
the same schematic, then uses native XML and the existing forward annotator.
Without `regeneration_intent`, the stage retains its original full-emission mode.

## Qualification and limits

[tests/test_schematic_regenerate.py](../tests/test_schematic_regenerate.py) uses
original owned symbols, including an authored passive resistor. It checks
value-only stability, prefix allocation across turns, renames after logical-ID
changes, human field conflicts, translated/rotated/mirrored placement, custom
fields and UUIDs, ambiguity, parse failure and missing capture coverage. Native
Bubblewrap runs verify added and removed components, stable instance UUIDs and
one-connection repairs with zero ERC violations and exact exported terminals.
[tests/test_schematic_stage.py](../tests/test_schematic_stage.py) checks receipted
schematic/state adoption, repeated updates, native forward annotation and
failure without output adoption.

Regeneration supports flat intent/1 emissions in the default `rows`
presentation. A layout that selects the AGENTEE-222
[functional presentation](schematic-emitter.md#functional-presentation) is
refused (`layout_schema`), because its wires and frames are not retained. Units of one component must
share an orientation; different unit positions are retained. Hand-wired or
hierarchical schematics, changed library captures, arbitrary graphic edits and
library/unit replacement fail closed. This report is not a registered check or
a coordinator finding. Review independence, adjudication, baseline immutability
and the single design writer are unchanged.

The ticket's mechanism reference is circuit-synth; its local `LICENSE` declares
MIT (checked 2026-10-18). This implementation uses agent-ee's existing emission
and diff modules. No upstream code, prose or design files were copied.
