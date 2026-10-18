# Design-rule fixture provenance

Every file under this directory is original to agent-ee. It was written for
the AGENTEE-62 design-rule cards on 2026-10-18.

- `pack.json`: the owned rule pack `mcu-peripheral-basics`. Its field set
  follows the MIT circuit-synth `DesignRule` dataclass (`rule_id`,
  `description`, `applies_to`, `rationale`, `examples`, `violations_cause`,
  `priority`). The I2C pull-up window follows the idea in atopile's
  `faebryk/library/I2C.py`. Every card text is original. No circuit-synth or
  atopile code, rule text or design file is copied.
- `generate.py`: writes a single-sheet schematic with fictional embedded
  symbols (`Owned:R`, `Owned:C`, `Owned:XTAL4`, `Owned:SENSOR`, `Owned:MCU`,
  `Owned:HEADER3`). It also writes the owner role bindings and fictional
  one-line datasheet statements for the parts `OWN-TS1` and `OWN-MCU1`. Tests
  print these statements into an original PDF with
  `tests/test_datasheet_facts.text_pdf`. They are not citations of any real
  datasheet.

The acceptance probes against circuit-synth's `control_board.net` and its
ESP32-C6 reference read those files in place, or from a local scratch copy.
They are not part of this tree.
