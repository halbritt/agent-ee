# Schematic-lint fixture provenance

Every file under this directory is original to agent-ee and was authored for
the AGENTEE-45 schematic readability lint on 2026-10-17. No stock KiCad symbol,
footprint, template or third-party design file is copied here.

- `Lint.kicad_sym`: owned symbols (two-terminal resistor and capacitor, LED,
  three-terminal regulator, an off-centre transform probe, and +3V3, VCC_3V3
  and GND power ports). A scratch generator wrote it; `kicad-cli sym upgrade
  --force` (KiCad 10.0.7) normalised it once, and a second native save is
  byte-identical.
- `generate.py`: the test-only schematic builder, the control design and its
  named mutations, and hand-derived body boxes and pin positions for the
  rotated and mirrored transform probe.
