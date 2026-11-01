# Acquisition reverse-blocking development reference

`acquisition-control-reverse-blocking-r1` derives a new power circuit from the
exact original acquisition recipe. It replaces U1 with ADP7104ARDZ-3.3-R7,
D1 with B0520LW-7-F and R5 with 1 kΩ; R6 is 2.2 kΩ. It retains 17 physical
references and all other connections. This is a development reference, with
no production build plan or accepted package. The unchanged-reference preparer
and frozen campaign cases do not select it.

The [derivation module](../src/agent_ee/acquisition_reverse_blocking.py) generates
the symbol, footprints and circuit intent. U1 connects OUT and SENSE to V3V3,
VIN and EN to VIN_PROTECTED, and both ground pins and the exposed pad to GND.
NC and unused PG remain unconnected. The pin mapping follows
[ADI's ADP7104 data sheet](https://www.analog.com/media/en/technical-documentation/data-sheets/ADP7104.pdf),
Table 5. SENSE shares the output net; this does not establish a Kelvin route to
the ADC. The final board must establish the delivered voltage and return paths.

`locality()` preserves existing relationships and distance limits while changing
the relevant U1 endpoints: C1 uses VIN8/GND6; C2 uses OUT1/GND3. An unknown old
U1 endpoint refuses. This transforms constraints, not final placement evidence.

The [source manifest](../fixtures/acquisition-reverse-blocking/sources.json)
records the original circuit and exact supplier objects. The generated footprint
rotates the supplier geometry into a top-left pin-1 orientation and preserves
its exposed pad, four via annuli and four drills. Native coordinates are rounded
to KiCad's nanometre grid. Manufacturer package facts are attributed to
[RD-8-4](https://www.analog.com/media/en/package-pcb-resources/package/pkg_pdf/soic_edrd/rd-8-4.pdf).
Unavailable raw manufacturer captures are explicitly marked unavailable.
The manifest retains identities, URLs and hashes; raw vendor CAD and documents
stay in the external development capture. `inputs(original, regulator_supplier,
diode_supplier)` requires the exact captured bytes and refuses changed sources.

D1 has its own generated footprint from the complete captured C155367 pattern:
two rectangular pads, approximately 1.2 × 1.2 mm at ±1.635 mm, with cathode
pad 1 at the left. The source symbol and cathode marking establish that
orientation. This replaces the provisional stock footprint, whose pad geometry
differs. The maximum body envelope follows Diodes DS30051 Rev. 18-2, page 4.
Supplier geometry preservation does not establish assembly or CPL acceptance.

The complete supplier parser represents these vias using strict
[contained-via ownership](land-pattern.md): all four belong to exposed pad 9.
Native geometry comparison checks both copper surfaces, all drills and pin
agreement; omitted vias, changed drills and wrong pin numbers fail. The original
unsupported result remains in the retained development evidence. Geometry
verification does not qualify the stencil or a via-in-pad assembly process.
The exposed pad has nine separate paste apertures, with the four thermal vias
left open and unfilled. Copper, drills and mask openings retain their source
geometry. The pattern covers approximately 61.3% of the exposed-pad copper;
the smallest drawn clearance to a drill is approximately 0.137 mm. This follows
[JLCPCB's guidance](https://jlcpcb.com/blog/solder-paste-layer-how-to-create-and-optimize-it)
to divide thermal-pad paste into smaller openings with 50-70% coverage and keep
paste off open vias. It does not establish a resulting solder-joint or void area.

Generated `power-assembly.json` records the U1 design and a 0.15 mm stencil.
The smallest aperture area ratio is 0.70, or 0.69 when each edge moves inward by
the supplier's stated 0.003 mm cutting tolerance. This exceeds the 0.66 guidance
in [JLCPCB's stencil design notes](https://jlcpcb.com/blog/smt-stencil-design-tips-for-improving-solder-paste-deposition).
The supplier lists 0.15 mm among its
[standard stencil thicknesses](https://jlcpcb.com/capabilities/pcb-stencil-manufacturing).
The final board must also establish suitable apertures for its other parts.

The assembly declaration requires preserving these EP apertures. JLCPCB's
[default CAM process](https://jlcpcb.com/help/article/opening-process-standard-of-stencil)
can change apertures, so the supplied geometry alone does not prove what a
manufacturer will produce. The open vias are not represented as tented, plugged
or capped; those are distinct
[fabrication processes](https://jlcpcb.com/help/article/pcb-via-covering).
No purchase, fabrication or assembler process approval follows from this design.

The [tests](../tests/test_acquisition_reverse_blocking.py) check the independent
pin mapping, preservation of other topology, source drift refusal, capacitor
locality endpoints, and native pad/drill geometry. With
`AGENT_EE_REVERSE_BLOCKING_SOURCES` naming the directory containing the two
pinned `.raweasy.json` captures, supplier and footprint tests run. With
`AGENT_EE_ACQUISITION_INPUTS` naming retained acquisition libraries, they also
emit the complete schematic, export its native netlist and check ERC. That
optional fixture can be retained at a new directory using
`AGENT_EE_REVERSE_BLOCKING_ARTIFACTS`. Native tests require normal workload
admission and the KiCad toolchain.

The [stencil regression](../tests/test_acquisition_stencil.py) measures real
KiCad Gerber output for aperture coverage, separation, drill clearance and
area ratio. Its retained original-draft reproduction has one full-pad opening
that covers every thermal-via hole. `AGENT_EE_STENCIL_ARTIFACTS` retains the
native board, Gerbers, drill output and measurements for one test run.

Release still requires complete source-bound preparation, the selected model
and its application evidence, current part/assembly qualification, generated
placement and routing, all native and manufacturing checks, and fresh independent
judgments and review. The module returns these as pending and always reports
`accepted:false` and `file_release:not_assessed`.
