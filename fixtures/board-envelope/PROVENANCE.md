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

The profile declares no `stackup`. JLCPCB publishes 1 oz (35 µm) outer copper
and one FR-4 dielectric for 2-layer boards, but no dielectric thickness. The
manifest lists the pages searched under `stackup_authority`.

`rc-jlc-2layer.envelope.json` is `rc.envelope.json` with only `fab_profile`
changed, to reference that profile's name and SHA-256. Its 0.035/1.53/0.035 mm
stackup and 1 oz weights remain the owned assumptions described above, not
fabricator facts.
