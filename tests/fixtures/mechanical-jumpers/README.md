# Telemetry-pod jumper attachment regression

AGENTEE-489 U1, captured from fome-mc commit
`83fd325d0d494ba02ff8f8a35514ed1b29ada394`.
The source board `telemetry-pod/telemetry-pod.kicad_pcb` has SHA-256
`fa7c2ca67e7ba3b832c0b89268052c6701695454bb17756af01854ef3ecaf46e`.

`capture.json` retains only JP1-JP5 from a native placement probe of that complete
board. `jumpers.kicad_pcb` retains the same five footprints, including their
identities, nets and all pad primitives, plus board settings and outline. Other
footprints and routing are omitted; the reduced board has its own hash. Native
tests require its pad records to equal the original full-board capture.

`reservations.json` materializes the ten required JP rows from
`telemetry-pod/expansion/agentee-490-mechanical-inputs/bound-reservations.json`
(SHA-256 `f354678d13df88bd4aba5fce571347ff7d4e3fe07039715700f377969a852d8e`).
The original source packet's reservations hash is
`106d697696d5ed14bb8bb213158c6728b7d18564446c649c962aff57ff633da9`;
its `unmaterialized.json` lists these ten rows. Each local polygon is the full
native pad copper bounding rectangle expanded by half the captured 1.3 x 0.4 mm
chisel rectangle, in the pad frame. The original capture has zero-degree pad
angles. No anchor-only pad approximation or historical world vertices are used.

The recipe declares tip-only geometry and unknown full iron height/access. Those
unknowns remain in the fixture. Tests use explicitly labelled 1 nm intervals to
isolate frame transformations; those variants establish no production clearance.

The reduced board incorporates KiCad stock jumper footprints (T6, CC-BY-SA-4.0
with the KiCad library exception) from the owner-authorized fome-mc input (T16).
See the repository origin inventory and `fixtures/fome-mc/LICENSE-NOTICE.md`.
