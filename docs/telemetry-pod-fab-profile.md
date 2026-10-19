# Telemetry pod fabrication inputs (AGENTEE-160)

The [JLCPCB profile](../fixtures/telemetry-pod/fab-profile.json) records eight
source-cited limits for JLC04161H-7628: four layers, nominal 1.6 mm, 1 oz outer
and 0.5 oz inner copper. Its machine name is `jlc04161h-7628` because profile
identifiers accept lowercase letters. It uses `agent-ee.fab-profile/2` and the
existing compiler. Its additional source-bound finished-thickness tolerance is
consumed by the envelope validator; it emits no native DRC rule or predicate.
The fab-check descriptor also refuses an unverified tolerance citation.

| Minimum | Captured value | Applicability |
| --- | --- | --- |
| Trace / spacing | 0.09 / 0.09 mm | Multilayer, 1 oz track rule; pair-specific pad and hole clearances remain separate |
| Via drill / diameter | 0.15 / 0.25 mm | Ordinary multilayer, 1.6 mm; excludes the special process for boards no thicker than 1 mm |
| Copper to edge | 0.2 mm | Routed outline; excludes V-cut |
| Hole to hole | 0.45 mm | Pad-hole rule applied conservatively to all holes; the via-only relaxation is omitted |
| Maximum dimensions | 663 × 593 mm | Ordinary four-layer FR-4 option, thickness at least 0.8 mm, fixed saved axes |
| Maximum layers | 4 | Specializes this profile to the named four-layer stackup |
| Finished thickness tolerance | ±10% | Nominal thickness at least 1.0 mm; 1.6 mm example permits 1.44–1.76 mm |

These are the published values observed on 2026-10-18, not a live quote or a
complete DFM approval. The profile does not express the via annular-ring
relation, pair-specific pad/hole clearances, surcharge policy, assembly, stock
or impedance acceptance. Passing it cannot establish pod specification
criterion 6. The owner's separate supplier DFM report remains required.

## Sources and replay

[source-capture.json](../fixtures/telemetry-pod/source-capture.json) records the
URLs, UTC capture times, response hashes, byte counts, capture tool and table
locators. The host fetched the responses using Python `urllib.request.urlopen`.
The committed UTF-8 files under `fixtures/telemetry-pod/sources/` are original
records containing numbers and names only, reduced by the worker from those
responses. They contain no vendor page text or graphics. Each profile limit
cites one exact line and the recorded file's SHA-256. The raw vendor responses
remain in the worker's allowed scratch directory; they are neither committed
nor copied into a new owner evidence store.

The original sources are JLCPCB's [capabilities page](https://jlcpcb.com/capabilities/pcb-capabilities)
and [stackup page](https://jlcpcb.com/impedance). A raw-response hash in the
manifest records provenance but cannot authenticate a vendor or replay a page
that is absent from the repository. The compiler verifies occurrence in the
recorded numeric fixture. A future source refresh requires a new host capture,
new date and reviewed values; checks never access the network.

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_pod_fab_profile -v
```

The tests compile all eight native/geometry limits and the tolerance, change
one byte of each source, refuse
missing/wrong citations, exercise the four-layer specialization and feed the
profile through `scaffold.produced_rules`. All six project minima and the four
Default netclass fields are present. There are no preferred limits, so Default
clearance is the 0.09 mm minimum with or without AGENTEE-153. That item's
separate change controls preferred-clearance policy; this fixture changes no
scaffold behavior.

## Nominal thickness and unbound mechanics

[envelope.json](../fixtures/telemetry-pod/envelope.json) is an
`agent-ee.envelope/2` record. It selects an 80 × 50 mm outline, four copper
layers and the captured JLC04161H-7628 stack. It records the ordered
`nominal_thickness_mm: 1.6` separately from `physical_thickness_mm: 1.5862`.
Every physical layer has a citation, including copper weights. The seven
thicknesses remain exactly as published; no dielectric padding is added.

The validator requires the layers to sum exactly to the physical thickness,
then checks that sum against the source-verified finished-thickness tolerance
in the referenced profile. The compiler verifies the tolerance's source with
the same path/hash/date/page/quote helper as native limits. Its applicability
starts at 1.0 mm nominal. Missing or changed evidence leaves the validation
outcome unknown; an out-of-tolerance physical sum is refused. The validated
`stackup.thickness_mm` consumed by scaffold, native board thickness and fab
remains the nominal 1.6 mm. A native pcbnew load/save test proves that both
values survive without padding. Envelope/1 keeps its exact-sum contract and
refuses the new shape; there is no silent migration.

The coordinator's continuation records two decisions from agent-423 under the
owner's standing direction on 2026-10-18: distinguish nominal and physical
thickness using the published tolerance, and leave mechanical choices to the
generator and checks. These decisions supersede the earlier blockers. They
remain subject to owner correction.

The envelope carries five `obligations`, bound by requirement IDs to revision 2:

| Obligation | Requirement IDs | Remaining work |
| --- | --- | --- |
| Four corner mounting holes | POD-ME-1, POD-AC-7 | Generator selects drill, plating, inset and coordinates |
| Height at most 12 mm | POD-ME-1 | Check component heights |
| Harness connector on an edge | POD-CD-HARNESS-CONNECTOR, POD-ME-2 | Generator selects TE 776228-1 anchor and orientation |
| Module antenna keepout | POD-CD-MCU-AND-RADIO, POD-AC-7 | Bind module transform and board-coordinate keepout |
| IMU away from tall/heavy parts | POD-ME-4 | Generate and check separation |

These remain unbound until AGENTEE-177/179. Empty geometry lists mean those
choices have not been made. The validator returns `structural: pass`,
`outcome: unknown`, `acceptance_ready: false`; deriving requirements preserves
every obligation. Mapping one to an existing check cannot make it bound.
Any declared hole or keepout outside the outline is still refused.
[envelope-inputs.json](../fixtures/telemetry-pod/envelope-inputs.json) retains
the original spec-record hashes, requirement byte locators and module-relative
antenna facts as supporting capture metadata. Its earlier blocked status is
superseded by envelope/2; it remains invalid as an envelope input itself.

## Antenna citations

[antenna-facts.json](../fixtures/telemetry-pod/antenna-facts.json) uses the
existing `agent-ee.datasheet-facts/1` format. It cites the 18 mm by 6 mm antenna
area in physical/printed page 45, Figure 11-1 of the [ESP32-S3-WROOM-1 datasheet
v1.8](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf).
`envelope-inputs.json` records the resulting module-relative rectangle and its
fact IDs. The envelope antenna obligation cites the same page, figure, fact IDs
and module rectangle. Neither supplies a board transform or claims board keepout
acceptance.
The vendor PDF is hashed in the source manifest but not committed. Supplying
the captured PDF as `sources/esp32-module.pdf` in a candidate enables the existing
networkless `datasheet_facts` check; without it the result stays unknown.

The current [hardware design guidelines](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/pcb-layout-design.html#general-principles-of-pcb-layout-for-modules-positioning-a-module-on-a-base-board)
separately address base-board placement/cutouts and recommend 15 mm clearance
in the housing. Their current PDF puts the keepout illustration in Figure 26,
physical page 34 (printed 31). That housing recommendation is not substituted
for a board polygon. A placed module and an explicit base-board keepout choice
remain explicit AGENTEE-177/179 obligations under the revised criterion 4.
