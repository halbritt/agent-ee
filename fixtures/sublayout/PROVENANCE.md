# Sub-layout reuse fixture provenance

Every file under this directory is original to agent-ee and was authored for
the AGENTEE-56 sub-layout reuse tests on 2026-10-17. No stock KiCad symbol,
footprint or template file is copied here.

- `generate.py`: the scratch generator, kept so the board can be rebuilt. It
  loads the owned footprints and the owned board setup from
  `fixtures/generation/` with pcbnew 10.0.7 and seeds KiCad's deterministic
  UUID generator, so a second run saves identical bytes.
- `top.kicad_pcb`: a 140 x 40 mm two-layer outline with three unplaced
  instances (`J1 U1 C1`, `J2 U2 C2`, `J3 U3 C3`) of the owned regulator module
  in a staging row right of the outline, and one locked header `J9` already
  placed. Instances A and B carry `/A/...` and `/B/...` nets; instance C
  leaves its input pins without a net, so the sub-board's VIN copper has no
  top-board net. The native `.kicad_pro` and `.kicad_prl` that `SaveBoard`
  writes beside the board were deleted; DRC therefore runs with KiCad defaults,
  and the footprints' `OwnedFP` library is not in any table, which KiCad
  reports as one `lib_footprint_issues` warning per footprint.

The module's proven layout is `fixtures/generation/regulator.kicad_pcb`; it is
used in place and not copied.
