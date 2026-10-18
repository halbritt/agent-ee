# Telemetry pod fabrication inputs (AGENTEE-160)

The [JLCPCB profile](../fixtures/telemetry-pod/fab-profile.json) records eight
source-cited limits for JLC04161H-7628: four layers, nominal 1.6 mm, 1 oz outer
and 0.5 oz inner copper. Its machine name is `jlc04161h-7628` because profile
identifiers accept lowercase letters. It uses `agent-ee.fab-profile/2` and the
existing compiler. No check, parser or schema has been added.

| Minimum | Captured value | Applicability |
| --- | --- | --- |
| Trace / spacing | 0.09 / 0.09 mm | Multilayer, 1 oz track rule; pair-specific pad and hole clearances remain separate |
| Via drill / diameter | 0.15 / 0.25 mm | Ordinary multilayer, 1.6 mm; excludes the special process for boards no thicker than 1 mm |
| Copper to edge | 0.2 mm | Routed outline; excludes V-cut |
| Hole to hole | 0.45 mm | Pad-hole rule applied conservatively to all holes; the via-only relaxation is omitted |
| Maximum dimensions | 663 × 593 mm | Ordinary four-layer FR-4 option, thickness at least 0.8 mm, fixed saved axes |
| Maximum layers | 4 | Specializes this profile to the named four-layer stackup |

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

The tests compile all eight limits, change one byte of each source, refuse
missing/wrong citations, exercise the four-layer specialization and feed the
profile through `scaffold.produced_rules`. All six project minima and the four
Default netclass fields are present. There are no preferred limits, so Default
clearance is the 0.09 mm minimum with or without AGENTEE-153. That item's
separate change controls preferred-clearance policy; this fixture changes no
scaffold behavior.

## Envelope remains unresolved

[envelope-inputs.json](../fixtures/telemetry-pod/envelope-inputs.json) retains
the spec's 80 × 50 mm maximum, four layers, four corner holes, 12 mm height,
connector identities and the published stackup numbers. It binds those facts
to the revision-2 [spec record](../fixtures/telemetry-pod/spec-record.json) by
requirement ID, byte locator and SHA-256. It is blocked metadata, not an
`agent-ee.envelope/1` record, and the envelope validator refuses it. There is no
production `envelope.json` claiming that the missing values are settled.

The seven published copper/dielectric layer thicknesses add to **1.5862 mm**,
not the spec's nominal **1.6 mm**. `board_envelope.validate_stackup` requires
exact equality. The tests reproduce that refusal. No extra dielectric thickness,
soldermask layer or nominal-thickness tolerance has been inferred. Resolving
this requires an explicit representation/authority decision; it must not weaken
the existing exact-sum contract silently.

The spec gives neither hole diameter/inset/plating nor fixed connector anchors,
rotations and insertion directions. The ticket names M3 but does not settle the
clearance drill or inset. The approved TE 776228-1 identity does not determine
its board coordinates. These fields remain unresolved instead of becoming
uncited owner decisions. Envelope/1 also has no height field, so the 12 mm
obligation is retained here without claiming executable acceptance.

## Antenna citations

[antenna-facts.json](../fixtures/telemetry-pod/antenna-facts.json) uses the
existing `agent-ee.datasheet-facts/1` format. It cites the 18 mm by 6 mm antenna
area in physical/printed page 45, Figure 11-1 of the [ESP32-S3-WROOM-1 datasheet
v1.8](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf).
`envelope-inputs.json` records the resulting module-relative rectangle and its
fact IDs. It has no board transform and is not a board keepout acceptance claim.
The vendor PDF is hashed in the source manifest but not committed. Supplying
the captured PDF as `sources/esp32-module.pdf` in a candidate enables the existing
networkless `datasheet_facts` check; without it the result stays unknown.

The current [hardware design guidelines](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/pcb-layout-design.html#general-principles-of-pcb-layout-for-modules-positioning-a-module-on-a-base-board)
separately address base-board placement/cutouts and recommend 15 mm clearance
in the housing. Their current PDF puts the keepout illustration in Figure 26,
physical page 34 (printed 31). That housing recommendation is not substituted
for a board polygon. A placed module and an explicit base-board keepout choice
are still required for ticket criterion 4.
