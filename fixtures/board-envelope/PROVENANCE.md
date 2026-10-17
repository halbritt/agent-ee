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
fabricator facts. `fab_profile` is `null` because no captured AGENTEE-75
profile exists on main, so validation reports the profile predicates as
unresolved rather than supplying defaults.
