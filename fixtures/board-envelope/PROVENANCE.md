# Board envelope fixture provenance

`rc.envelope.json` is an original `agent-ee.envelope/1` record written for
AGENTEE-85. It describes the hand-made [RC fixture](../rc/PROVENANCE.md) board
`../rc/control/rc_filter.kicad_pcb` (unchanged, sha256
`f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de`).

Facts taken from that saved board, and compared field by field by
`agent_ee.board_envelope.round_trip`:

- the four Edge.Cuts `gr_line` segments of the 20 mm x 12 mm rectangle
  from (96, 96) to (116, 108) mm; the edge names are envelope labels;
- copper layers `F.Cu` and `B.Cu`, and `(general (thickness 1.6))`;
- J1 on `F.Cu` with origin `(at 100 100)` and rotation 0, which is 8 mm along
  the `left` edge (from (96, 108) toward (96, 96)) and 4 mm inward.

Owned assumptions that the saved board does not record (it has no stackup
section and KiCad saves no copper weight): the 0.035 mm / 1 oz copper and 1.53 mm
core split of the 1.6 mm total, J1's `from_top` mating direction for a vertical
pin header, and the 0 mm tolerance. They are illustrative fixture values, not
fabricator facts. `fab_profile` is `null` because no real fabricator's
AGENTEE-75 profile has been captured, so validation reports
`fab_profile_selected` as unresolved rather than supplying defaults. Tests
reference owned synthetic profiles written at run time, not fixture facts.

## JLCPCB 2-layer profile and RC variant (2026-10-19)

`jlc-2layer/fab-profile.json` is an original `agent-ee.fab-profile/2` record
(`jlc-2layer-fr4-1oz`). It follows the telemetry-pod pattern. On 2026-10-19 the
host fetched JLCPCB pages with Python `urllib.request.urlopen`; this was a capture
step, not a check. The worker reduced the responses to
`jlc-2layer/sources/jlcpcb-2layer.txt`, which holds numbers and names only, with
no vendor prose or graphics. `jlc-2layer/source-capture.json` records each URL,
UTC time, response SHA-256 and byte count, the table locators and the
interpretation limits. Raw responses stay in the worker's scratch directory and
are not committed.

JLCPCB publishes 1 oz (35 µm) outer copper, the 1.6 mm nominal and one FR-4
dielectric for 2-layer boards, but no dielectric thickness. The manifest lists
the pages searched under `stackup_authority`. On 2026-10-22 (AGENTEE-85 G1) the
profile gained a partial `stackup` that cites four consecutive lines of the
unchanged recorded source. It declares the 1.6 mm nominal and two 1 oz, 0.035 mm
copper layers, with the `core` thickness and physical total `null`
(unpublished). No new capture was made, and the source bytes are unchanged.
Without that member, the profile bytes are exactly the 2026-10-19 record.

`rc-jlc-2layer.envelope.json` is `rc.envelope.json` with only `fab_profile`
changed, to reference that profile's name and SHA-256. Its 1 oz weights,
0.035 mm copper and 1.6 mm total now match the profile's cited facts. Its
1.53 mm core remains the owned assumption described above, not a fabricator
fact, and `stackup/1` reports it as unknown.

## Constraint provenance fixture (AGENTEE-266)

`provenance/envelope.json` is `rc.envelope.json` with a 12.5 mm
`max_component_height_mm` and an original `constraint_provenance` declaration.
`provenance/owner-statement.md` is owned synthetic text written for the tests.
It is not a real owner's statement. The outline and fab profile are `assumed`,
the layer count and height are `owner_stated`, and the stackup is `derived`
from the layer count. J1 is `owner_stated` but soft, because its quote says
"ideally". The envelope cites the statement by its SHA-256, so editing the
statement requires updating the envelope.
