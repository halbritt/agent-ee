# Authored portable placement fixture

AGENTEE-623 uses synthetic inputs authored from the repository's original
`test_placement_job.build_board` and `test_intent_dialect.minimal_dialect` controls.
These files are not recovered historical captures, telemetry-pod inputs, ESP-Prog
qualification, or manufacturing acceptance. `coverage.json` maps all seven
historical test IDs to their portable behavior. Their names remain for full-suite
comparison; no external ESP-Prog environment variables or former campaign paths
are needed.

`sources.json` binds the authored intent, groups and geometry byte strings.
`portable_placement.py` verifies those bytes before staging them. The original
JSON sources are retained alongside working copies. Native boards are generated
only inside the admitted test runtime, using the supported pcbnew API. Their
UUIDs/serialized bytes are generated per run and receive actual byte identities;
they are never presented as historical hashes. The generator/base-builder hashes
and exact source/runtime/input receipts accompany the test evidence.

The 20 x 12 mm board has three movable parts, two fixed copper marks with repeated
native labels, and two silk-only footprints with empty labels. Seven native UUIDs
must remain distinct. The original authored builder supplies R1/C1 fab+pads and
U1 silk+pads occupancy, explicitly labelled `derived_occupancy`; these are not
package-body clearance evidence. The final board moves R1 from (5,6) to (6,6), so
raw and final geometry and hashes differ.

The raw pad rectangles give four analytical signed gaps:

| Declaration | Pad pair | Gap (mm) |
| --- | --- | --- |
| C1 near U1 | 1 to 3 (A) | 3.0 |
| C1 near U1 | 2 to 1 (B) | -0.1 |
| R1 near C1 | 1 to 1 (A) | 4.2 |
| R1 near U1 | 2 to 2 (GND) | 3.85 |

R1/C1 pads are 0.8 x 0.9 mm, at local x=-0.75/+0.75. U1 pad1 is 0.6 x
0.5 mm at (-1.2,-0.6); pad2 is the same size at (-1.2,+0.6); pad3 is 0.7 x
1.2 mm at (+1.5,0). For example C1 pad1's right edge is 9.65 and U1 pad3's
left edge is 12.65, giving 3.0 mm. C1 pad2 and U1 pad1 overlap by 0.25 in x
and 0.1 in y, giving -0.1 mm. These expectations are fixed in geometry.json,
independent of production measurement results.

Groups are core={U1,C1} and port={R1}. GND is a shared rail. A is explicitly
scoped to core and the core/port near pair; B remains an intra-core signal.
Core's A/B link gaps are 3.0/-0.1 mm. The C1-to-U1 anchor link is B pad2-to-pad1
at -0.1 mm. Final core-to-port interface gap is 3.2 mm after R1 moves. Rail
sidecars bind the actual board and original group bytes; stale identities refuse.
Measurements remain advisory with no automatic group pass.

The public metrics CLI must report an `edge_identity` violation for R1: declared
east, nearest west, with the `body:fab` basis, and exit 1 while reporting every
declared leg. The authored outline and R1 centre give distances west=5, east=15,
north=south=6 mm; a pure guard checks the unique nearest edge and the declared
mismatch independently. The report covers edge identity, not setback compliance.
Three distinct unsourced interface
claims stay unresolved. Withheld decap distance, zone containment and overlap
claims remain withheld. The raw dialect is classified rather than silently
accepted by the direct native validator.

Each target uses the runtime-owned `RetainedTemporaryDirectory` API. With
`AGENT_EE_EVIDENCE_DIR` selected by the normal runner, sources, raw/final boards,
problems, capture records, metrics, view requests/manifests/images, command stdin/
stdout/stderr and timing/refusal records are copied before workspace cleanup.
Missing/changed-input controls preserve pristine originals and damaged copies.
A failed or timed-out child retains diagnostics and propagates its failure.

Historical hashes, 21/16 footprint/member counts, ESP-specific references and
2.9704/3.3268/0.658/-0.075 mm observations describe different retired boards.
They remain historical evidence in retained campaign logs and Git history; using
them as oracles for this authored fixture would be false. The portable tests
preserve custody, classification, measurement, view binding, refusal and pose
preparation/evaluation behavior. Pure source checks do not qualify the native
fixture. All affected modules still require a separate native grant and normal
`ae-ci-exec`/`strict_suite.py` execution, independent review and the composed gate.
