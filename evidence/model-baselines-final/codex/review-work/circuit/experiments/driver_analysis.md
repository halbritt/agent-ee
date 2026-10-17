# Circuit calculations and conclusions

## 1. Added lowside drive compatibility

Native topology: in both instances of `inputs/project/Lowside Adder.kicad_sch`, RN2/RN4 are independent 1k series resistors from LS9..12 and IGN9..12 to U11..14/U16..19 pin 1 (INPUT). RN1/RN3 are independent 1k pulldowns at the MCU side. Uxx pin3 is GND; tab4 routes the output; pin2 is deliberately not routed. Saved board pin audit agrees with native XML on every compared numbered pad (parity_electrical.json). This rules out a schematic-only finding.

Polygonus J2 pins 12..15 and 24..27 are direct MCU outputs, per pinned module README output section and pinout (references/Polygonus_README.md) and pinned reference schematic PDF. The declared module uses a 3.3V MCU, not 5V buffer outputs on these channels. The IGN1..8 buffered channels are different pins 16..23 and are not the channels under this finding. These reference module files are external sources, not additional candidate native source, and actual assembled module revision/firmware is unspecified.

SOT-223 means the N3 variant. ST VNL5090N3-E datasheet DS8791 Rev10 Table2 says INPUT internally connects to Vsupply; Table6 gives operating Vsupply 3.5..5.5V; Table8 gives input threshold 1..3.5V at VDS=VIN, ID=1mA. The 2.1V VIH in Table10 belongs to the SO-8 S5 variant and must not be used for the N3 in this design. Datasheet source: https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf ; rendered extraction retained in references/web_driver_sources.json. Direct PDF retrieval attempts timed out, while web retrieval succeeded.

Even an ideal 3.3V-high MCU output and zero input current give V_input <=3.3V. With series resistance R=1k and nonnegative driver input current Iin, V_input=V_GPIO-1000*Iin. Therefore 3.3V-3.5V=-0.2V operating-range margin even before GPIO output drop and resistor drop. No particular Iin at 3.3V is assumed. Typical/on-state current specified at 5V is not extrapolated. Low-side switch turn-on, on-resistance and protection behavior are not guaranteed from this drive. This is an operating-range/interface defect; no claim of inevitable damage, exact load current, temperature, switching time or failure on every sample.

The candidate LCSC field is C2680415 on all eight driver instances. Supplier product mapping says VNL5050N3TR-E, not VNL5090N3TR-E (references/LCSC_C2680415_product.json; original supplier HTML retained). ST VNL5050N3-E datasheet DocID15917 Rev6 Table2 likewise connects INPUT to Vsupply, Table6 requires 3.5..5.5V and Table8 permits threshold up to3.5V. Thus the 3.3V drive defect exists under either component identity. Source: https://www.st.com/resource/en/datasheet/vnl5050n3-e.pdf ; rendered extraction retained in references/web_vnl5050_sources.json and complete ST document mirrored by the supplier retained as references/VNL5050_supplier.pdf (Tables2,6,8). The part identity conflict itself is a distinct documentation/procurement defect, with no unsupported assumption that the larger-current part causes harm.

## 2. Undriven LS9/LS10 bias (concern)

Native root R2=1k connects +5V to /LS9; R1=1k connects +5V to /LS10. Each net has a 1k RN1 pulldown and 1k RN2 series path to U11/U12 input. The stepper A1 input is also connected. These passives are populated in schematic and board. An undriven/Hi-Z J2.24/J2.25 is the explicit condition; no particular firmware is assumed.

Unloaded calculation: V_control=5*(1000/(1000+1000))=2.5V; R_Thevenin=1000||1000=500ohm; driver-input source R=500+1000=1500ohm. For unknown input current I, V_control=2.5-500*I and V_driver=2.5-1500*I. The native pull-up does not preserve a zero-volt/off state. The unloaded 2.5V lies within the manufacturer's 1..3.5V device-threshold interval; conduction cannot be excluded across samples. Exact conduction is unresolved because no validated low-voltage transistor/input model, fitted part or bench measurement is supplied.

Command: ngspice -b /work/experiments/reset_bias.cir > /work/experiments/reset_bias.log 2>&1 ; exit0, v(control)=2.500000V and v(driver)=2.500000V. ngspice42 default27C is immaterial to ideal-resistor DC calculation; no temperature qualification inferred. Simulation includes only native R values, exact +5V rail and Hi-Z/unloaded inputs. It is NOT a VNL switch model or a loaded actuator simulation, so it proves passive bias only. No startup duration, actual load current or transient actuation proved. Manufacturer threshold makes this a startup/reset concern, not a proven actuator event.

## 3. Internal CAN2 default assembly and coverage boundary

Original native `inputs/project/wbo-can-trans.kicad_sch` UUID d69380f9-5487-4782-9ce9-0690df62e543 is reused as R36 and R37. Each is120ohm across CAN2+/- and both have dnp=yes, in_bom=no. Board attributes are dnp/exclude_from_bom/exclude_from_pos_files on both. Removing DNP parts from the resistor graph leaves zero base-board differential terminating resistors. Its conductance contribution is0S; fitted count0. Fitting both would give120||120=60ohm, but this is NOT the saved assembly and no scratch assembly is claimed as a fix.

Upstream Polygonus v1.6.1 PDF includes R10=120ohm across CAN2, as a reference implementation. Actual J2 module revision and its fitted termination are unspecified. No second terminator or external harness terminator should be invented, and no mandatory cable length/bitrate/termination topology is provided for the internal short PCB bus. Neither successful ERC/DRC/netlist exports nor zero unconnected pads prove CAN waveforms or error-free communication. No deterministic CAN failure alleged. End-to-end termination and bus dynamic behavior remain coverage omissions.

## 4. Other inspected circuit identities and defaults

- TJA1051T/3 U20/U21 native pins1TXD,2GND,3VCC,4RXD,5VIO,6CANL,7CANH,8S align with manufacturer reference. VCC5V, VIO3.3V, S=GND. Nominal rail identities are compatible. References/TJA1051.pdf retained. No bus model/simulation supplied.
- U7 valueMAX9142, cached LM393 symbol: comparator pin identities match MAX9142 datasheet pin table. LCSC C18567 lookup agrees with MAX9142ESA+T. No pin mismatch inferred from generic symbol name or its open-collector ERC type.
- MAX9924 U10 is default modeA1: INT_THRS=5V; ZERO_EN=5V through JP1 default bridge1-2; externalBIAS=2.5V via R30/R31; EXT floating. This agrees with mode table; no misapplication of modeA2-only BIAS-grounding recommendation. U10 pin3 NC. C24 bypass differs from recommended two-cap example but no noise/rail stimulus is declared; no behavioral allegation.
- VNL N3 drain pin2 and tab4 are internally common. Pin2 unconnected does not by itself establish an open output.
- TLE9201 U3/U15: unusedCSN has internal pullup; unusedSCK/SI have pulldowns per official datasheet Table4; DIS has native10k pullup plus internal pullup. Nominal3.3V control exceeds2.0V input-high minimum. No floating-CSN defect inferred. No brownout waveform, motor model or supply-decoupling impedance measurement.

## Verification limitations

Native check outputs are ERC/DRC, XML netlist and saved-board geometry. They have no time-domain electrical models, supply sequencing stimulus, actuator loads, decoded bus traffic or analog performance measurements. Only the explicitly documented passive DC check was added here. All13 DRC error entries are excluded native violations; exclusions are not tool failures, nor are they evidence of operating electrical failure. ERC5errors checked against actual net connections: connector-supplied power is not absent merely because ERC has no power-output flag; same-name sensor rail output pins belong to a module; unused FLT alone does not prove loss of stepper function. No full ERC-clean or DRC-clean claim.
