# Engineering references

These references support interpretation; they do not create unstated project
requirements or replace native design evidence.

- [KiCad 10.0 CLI manual](https://docs.kicad.org/10.0/en/cli/cli.html), documentation
  inspected for KiCad 10.0.7. DRC JSON, schematic parity, all-severity reporting
  and saved-vs-refilled zones are relevant to the native wrappers. The installed
  command help was checked against the commands actually exercised.
- [ngspice documentation](https://ngspice.sourceforge.io/docs.html), installed
  ngspice 42. The demonstration uses ideal passive elements and an explicit step;
  its independent calculation and limitations are in the fixture requirements.
- [DRCY paper](https://arxiv.org/abs/2603.15672), inspected as a starting point for
  agentic hardware review. No performance claim from that work is transferred to
  this implementation or used as acceptance evidence.
- [NXP TJA1051 datasheet](https://www.nxp.com/docs/en/data-sheet/TJA1051.pdf?pspll=1),
  revision 9, 28 November 2017, downloaded 2026-10-16 as `TJA1051.pdf`.
  SHA-256: `6637cd500dda3c1c951ce29bed7fe909f11f9a3bef64b096dbb486827add9f11`.
  PDF page 9, Table 7, characterizes dominant differential output under stated
  supply/temperature conditions for a 45–65 Ω load (also a separate 45–70 Ω row).
  This is a characterization condition, not a damage threshold. It does not
  establish the donor's fitted termination or prove an assembled bus will fail.
- LCSC/JLCPCB public product pages for C1525, C14663, C9055, C51118, C20917,
  C7593 and C1340704, read 2026-10-17. Only their package strings are recorded,
  in the [package map](package-map/kicad-10.0.7-lcsc-1.json) observations. They
  qualify those examples, not a full catalog feed.
- [Espressif ESP32-S3-WROOM-1 & ESP32-S3-WROOM-1U Datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf),
  version 1.8, captured via `agent_ee.datasheet_capture` as
  `dbdfd4ddc180acc1e28909cf69518d22af59605031728a9765a38ff746a1d9b8.pdf`.
  SHA-256: `27d71971da07c280c6068d08c74720d1a25b8f20cf8494dc1765bdd28d40d435`.
  PDF page 45, Section 11.1 PCB Land Pattern, Figure 11-1 (`ESP32-S3-WROOM-1
  Recommended PCB Land Pattern`), specifies a `18 mm` × `25.5 mm` module body,
  a `6 mm` on-board PCB antenna depth at the top edge, and a `15 mm` base-board
  keepout clearance on the left, right and top of the antenna area (`x: -24..24 mm`,
  `y: -6.75..-27.75 mm` in module-centered coordinates). Only numerical dimension
  tokens are recorded in `fixtures/telemetry-pod/esp32-s3-wroom-1/datasheet-facts.json`;
  the PDF bytes are not distributed in the repository.

For the carrier baseline, fitted state must be checked against native `dnp`
properties and the saved board. A resistor's nominal value and connectivity
alone do not establish an assembled parallel load. In particular the donor's
carrier R10 and an attached brain-module R10 are distinct components.
