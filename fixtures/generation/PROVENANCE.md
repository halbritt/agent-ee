# Generation fixture provenance

Every file under this directory is original to agent-ee and was authored for
the AGENTEE-40 schematic emitter and AGENTEE-52 forward-annotation tests on
2026-10-17. No stock KiCad symbol, footprint or template file is copied here.

- `project/lib/Owned_Gen.kicad_sym` and `project/lib/Owned_Power.kicad_sym`:
  owned symbols (regulator, capacitor, three-way connector, an asymmetric
  transform probe, a dual-unit part with common pins, a global ground and a
  power flag). They were written by a scratch generator and then normalised
  once with `kicad-cli sym upgrade --force` (KiCad 10.0.7); a second native
  save is byte-identical.
- `project/lib/OwnedFP.pretty/*.kicad_mod`: owned land patterns normalised once
  with `kicad-cli fp upgrade --force` (KiCad 10.0.7). The UUIDs inside were
  assigned by that native save.
- `*.intent.json`, `*.layout.json`: emitter inputs. Library hashes bind the
  exact bytes above.
- `regulator.expected.json`: independently specified native connectivity
  (KiCad local-label nets carry the root-sheet `/` prefix; power and flag
  symbols are not netlist components; the regulator's pin 4 is an explicit
  no-connect). It was written before any emitter output existed.

- `regulator-core.*` (the regulator without power or flag symbols, for
  AGENTEE-41 parity) and `dual.*` (a two-unit part with a six-pad footprint,
  for multi-unit binding) were added later. `footprints.manifest.json` declares
  every owned footprint with its hash. `DUAL_6PAD.kicad_mod` was normalised
  with `kicad-cli fp upgrade --force` like the other footprints.
- `two_level.*` (AGENTEE-40 hierarchy, `connectivity-intent/2`): a root sheet
  and one `filter` child with two declared ports and an internal net of the
  same name on each sheet. `two_level.expected.json` gives the physical nets,
  labels and sheet pins and was written before any hierarchical emitter output
  existed. `three_level.*` adds a pass-through middle sheet with no parts and a
  sibling sheet; it has no hand-written expectations, and its tests compare
  against the fresh resolution through AGENTEE-41 parity and native ERC.
- `regulator.kicad_pcb`: an owned 30 x 20 mm two-layer board built once with
  pcbnew 10.0.7 by a scratch script. It places J1, U1 and C1 from the owned
  footprints, with native paths derived from the regulator layout namespace
  through the emitter identity contract (`/` + uuid5 of `["symbol", id, 1]`).
  It has 0.25 mm tracks, two GND vias and a filled B.Cu GND zone. Native
  `kicad-cli pcb drc --schematic-parity` against the emitted regulator
  schematic reports no violations, no unconnected items and no parity issues.

The power flags in the regulator intent are explicit intent declarations that
`J1` brings `VIN` and `GND` from off the board. The emitter never adds a flag,
power symbol or other component that the intent does not declare.
