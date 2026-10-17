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

The power flags in the regulator intent are explicit intent declarations that
`J1` brings `VIN` and `GND` from off the board. The emitter never adds a flag,
power symbol or other component that the intent does not declare.
