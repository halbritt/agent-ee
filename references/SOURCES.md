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

For the carrier baseline, fitted state must be checked against native `dnp`
properties and the saved board. A resistor's nominal value and connectivity
alone do not establish an assembled parallel load. In particular the donor's
carrier R10 and an attached brain-module R10 are distinct components.
