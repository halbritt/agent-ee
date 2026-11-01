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
its exposed pad, four via annuli and four drills. The generator writes coordinates
on KiCad's nanometre grid, rounding to the nearest nanometre with ties away from
zero. The production library reader rejects fractional nanometres. Manufacturer package facts are attributed to
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

## Production part bindings

The [parts source producer](../src/agent_ee/acquisition_power_parts.py)
exposes `inputs(base, selected, context_bytes)`. It returns files and a provenance
report for the existing `parts_stage.compile_parts` consumer. `base` supplies the
exact retained public-control purchasing inputs, identified by `BASE` hashes.
The original topology still comes from the committed base circuit. The output
retains ADS1115IDGST/C468683 for U2, instead of restoring the original circuit's
IDGSR display value. R5 and R6 reuse the captured 1 kΩ and 2.2 kΩ selections
already used by R1 and R3, with separate physical allocations.

`selected` supplies an `agent-ee.acquisition-power-parts-sources/1` context:
`reference` names this new reference; `parts` and `supplier` each map U1 and D1
to path/SHA-256 records; `files` is their complete dependency closure. Every path
uses the `power-reference/` namespace. Named records must replay against their
actual captured stock responses and datasheet acquisition records. Extra or
missing dependencies, changed bytes, different purchasing identities and other
datasheet revisions refuse. The producer does not acquire or refresh sources.

The selected regulator PDF is the supplier's **Rev. I, October 2019**, SHA-256
`958844711dc322d705b9a22c04d50633fb86d62184e638b591d251cd3160ddef`.
Its SOIC pin table and automatic reverse-blocking description support the
generated connectivity. It is distinct from the web-read Rev. L data sheet;
this capture does not establish custody of Rev. L or replace model applicability
review. D1 uses the retained DS30051 Rev. 18-2 PDF through the existing historical
import format, preserving its acquisition history.

The producer rebuilds the part decisions, symbol/footprint assignments, CAD
member hashes and passive-rating inputs. It explicitly declares pad 9's four
thermal vias as part of U1's SMD assembly. It carries forward all 18 locality
relationships with the new regulator pad numbers. Old power-role checks,
simulation decks and review judgments are not part of the output. Retained
source documents and catalog observations are evidence, not refreshed stock or
approval of the new circuit.

The [production regression](../tests/test_acquisition_power_parts.py) uses the
normal parts compiler, schematic emitter and forward annotator. It checks 17
bound native components, zero ERC violations, and all five pad-9 copper objects
on GND. The resulting PCB is an **unplaced, unrouted development board**.
Set `AGENT_EE_ACQUISITION_INPUTS` to the pinned retained public-control source,
`AGENT_EE_POWER_PART_SOURCES` to the selected capture directory containing
`context.json`, and optionally `AGENT_EE_POWER_PART_ARTIFACTS` to a new directory
to retain outputs, including failed native attempts. Apply normal native workload
admission. The old unchanged-reference preparer remains separate; this producer
does not yet emit a complete typed build plan or new qualified power role.

Release still requires complete source-bound preparation, the selected model
and its application evidence, current part/assembly qualification, generated
placement and routing, all native and manufacturing checks, and fresh independent
judgments and review. The module returns these as pending and always reports
`accepted:false` and `file_release:not_assessed`.

## Power-model application

The [power analysis producer](../src/agent_ee/acquisition_power_analysis.py)
accepts the exact generated production circuit, its parts manifest, and the
captured ADI ADP7104-3.3 model and symbol as bytes. `inputs()` returns a finite
LTspice deck and a declaration binding their hashes, model pin order and selected
conditions. It launches no solver. The ordinary parts compiler and native
netlist checks remain responsible for source qualification and connectivity.

The case uses a non-sinking 20 mA source, a 4.355 V protected-input target and
25 C. It exercises startup with the host inactive, a loaded interval with both
I2C lines low, and unloaded discharge. The load and source target fall together
at 20 ms; the loads reach zero at 20.001 ms. This evaluates the high-impedance
discharge state, not compliance with the external instruction to release the
host **before** removing power. There is no automatic sequencing circuit.
The source then has a zero-volt target, rather than a physically open connection.
It can clamp a slightly negative input by supplying current. With that source
law, positive current occurs only at negative input voltage, so it delivers no
positive electrical power after removal. This termination must remain explicit
when applying the result to a board.

The declared sensitivity uses R5 = 990 ohm, R6 = 2222 ohm, C1 = 2 uF,
C2 = 12.65 uF and separate C3 = 0.1265 uF. Each capacitor has a constant
0.05 ohm series resistance. These are explicit analysis inputs, not certified
joint capacitance or ESR bounds. The 300 uA ADC allowance plus 1 mA engineering
reserve applies only during the loaded interval. Zero current after removal
examines slower discharge; it does not model actual ADC consumption or authorize
an external J2 load. The protected-input target represents the D1/path drop;
diode reverse-input leakage needs its separate source-based evaluation.

`measurements(raw_bytes, log_bytes)` reads completed native ASCII output. It
binds columns by name and unit, checks finite ordered samples, the complete
80 ms interval and the 2 us maximum step, and hashes the original trace and log
bytes. It reports sampled rail, source-current and absolute-rating predicates,
plus the remaining output voltage. No discharge deadline is invented. The
ADP7104's independent OUT rating applies; its typical 55 mV reverse-blocking
threshold is not a guaranteed clamp, and the TPS7A20 differential limit is not
reused. Successful predicates always retain `accepted:false` and
`file_release:not_assessed`.

The [regression](../tests/test_acquisition_power_analysis.py) uses the actual
retained model output, including LTspice's device-current column order. It checks
source drift, wrong purchased parts, truncated/nonfinite output and unsuccessful
logs. Set `AGENT_EE_POWER_PRODUCTION` to the generated candidate source,
`AGENT_EE_ADI_MODEL_LIBRARY` to the captured `lib` directory, and
`AGENT_EE_POWER_REMOVAL_TRACE` to the directory containing the native
`high-z-removal.raw` and `.log`. The tests replay artifacts; they do not start
LTspice. Running the model requires a separately admitted, bounded native job
with captured runtime, command, source hashes and terminal output.

The retained development run completed in 267 seconds. It reached 3.234 V at
4.325 ms, held about 3.299676 V during the loaded interval and used at most
10.6664 mA in that interval. The output was 33.908 mV at 80 ms, 60 ms after
removal began. These are model observations for the declared case, not guaranteed
startup/discharge times or final-board measurements. Final voltage delivery,
capacitor applicability, thermal paths and independent engineering acceptance
remain required.

## Complete development build input

`acquisition_power_prepare.prepare(base, selected, extra, out)` creates a new
`build-plan.json`, `inputs/` and preparation receipt. `base` is the exact retained
acquisition build source with its adjacent build plan; `selected` holds the
captured U1/D1 part context; `extra` holds the files listed in
`fixtures/acquisition-reverse-blocking/build-extra-pins.json`. The producer
checks those inputs before the ordinary typed build consumes them. It reads
captured sources without launching a native CAD tool or network request. Input
hash drift and an existing or overlapping output directory raise `ValueError`;
failed preparation output remains available for diagnosis.

The extra inputs include UTF-8 text from all 25 pages of the selected ADP7104
Rev. I data sheet and all five pages of the B0520LW Rev. 18-2 data sheet under
`power-reference/manufacturer-text/`. Their capture manifest binds the original
PDFs and acquisition manifests, extraction binaries, and page byte offsets.
The ordinary judgment citation validator can therefore cite those primary
passages. PDF presence alone did not provide eligible text citations. Machine
extraction leaves figures and table interpretation for review; it does not
establish electrical or assembly applicability.

The specification and authored obligation spans remain unchanged. The distinct
reference replaces the eight TPS-specific model checks with explicit ADP
interval calculations and a replay through the existing `measurements` check.
The replay binds the current circuit, selected electrical parts, original
execution inputs, model/deck and waveform. It calculates new metrics from the
retained waveform; it does not run a simulator. Its limits are declared in the
requirement map, outside the parser. Changed circuit or waveform bytes refuse.

The source includes new regulator geometry, all 17 components and all 18
placement relationships. Python and Rust recognize the same exact mechanical
model and source hashes. The captured Rev. I RD-8-2 exposed-pad dimensions match
the generated footprint. The generic SOIC STEP body is captured for views and
height assessment; it omits the exposed pad. Actual pad9 geometry, paste and
thermal conclusions require their separate checks. Native pin-frame comparison
sets the new regulator's CPL correction to 270 degrees and the diode's to zero.
The validated mechanical source identity also selects the local copper
endpoints: C2.1 to U1.1 and C2.2 to U1.3. The producer records this reference
through its seed chain, and final verification rejects a different reference.
The original TPS reference keeps its existing endpoints.

Stock observations keep their original dates and the 24-hour age limit. Old
power-role qualifications and judgment verdicts are not part of this new
reference. Fresh judgment briefs address the original spans, including nominal
25 C power applicability and final return paths. The output remains unaccepted
until native manufacturing checks, candidate-bound judgments and full review
pass. New reusable roles must be qualified from the resulting design before
adaptation or G4 credit; this preparation does not replace the frozen G4 cases.

The new integration tests consume actual retained sources, a generated native
board and the production-image measurement sandbox. Configure
`AGENT_EE_ACQUISITION_INPUTS`, `AGENT_EE_POWER_PART_SOURCES`,
`AGENT_EE_POWER_BUILD_EXTRA` and `AGENT_EE_POWER_BUILD_NATIVE`; the last names a
completed native build through the schematic checkpoint. The checked-in
measurement executable hash requires the retained production image.

The [nominal power applicability decision](acquisition-power-applicability.md)
records the subsequent source correction, native copper observations and bounded
film-capacitor experiment. That experiment has not changed the production parts.
