# Circuit review command and assumptions log

Candidate 69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4; run f3c237c2-3a08-4617-aaba-f04c47bbe98f.
Only candidate read operations performed. No prior reviewer judgments or credential files read. No scratch modification asserted to fix the design.

## Tools and commands
- python3 --version: Python 3.12.3.
- kicad-cli version: 10.0.7.
- ngspice --version: ngspice-42, KLU; no simulation yet performed.
- rg --files on /candidate/project: native candidate hierarchy and embedded library sources inventoried.
- Python JSON reads of /evidence/work/{erc,drc}/{erc,drc}.json: full records inspected; ERC 353 total; all 13 DRC error entries carry excluded=true. Native violations remain native violations, regardless of command status or exclusions.
- python3 /work/experiments/native_audit.py: independently parsed native saved board and original exported XML into component_pin_audit.json and board_pin_audit.json. Parser source retained. No board writes/refill.
- python3 /work/experiments/fetch_refs.py: attempted official datasheets with urllib; URLs, hashes and failures recorded in references/fetch_log.json when completed.
- python3 /work/experiments/fetch_modules.py: fetched public module sources at pinned upstream commits; references/modules_fetch_log.json records URL, hash, bytes and local artifact path. External module revision matching to actual assembly is NOT established.
- pdftotext -layout references/Polygonus_export_V1.6_polygonus-Shortage-Version.pdf references/Polygonus_v1.6.txt.
- kicad-cli sch export netlist --format kicadxml -o /work/experiments/wideband_module.xml /work/experiments/references/wideband_hardware_board_module_wideband_controller.kicad_sch: exit 0. This is a fetched reference, not the candidate; no functional behavior inferred from export success.

## Preliminary cross-checks
- Both U20/U21 have VCC +5V, VIO +3.3V, S grounded, TXD/RXD to the corresponding U1/U2 MCU interface, CANH/CANL to /CAN2+ and /CAN2-. Native pin identities agree with TJA1051T/3 manufacturer pin definitions.
- R36 and R37 each 120 ohm directly across CAN2; both DNP in schematic, netlist, and saved-board attributes. No populated base-board resistor terminates that pair. Upstream Polygonus v1.6 PDF has one 120-ohm CAN2 termination R10; its assembly/revision is external and not specified by candidate. No bus timing/eye measurement supplied.
- U7 is identified as MAX9142 using LM393 symbol. Manufacturer MAX9142 pin table matches this comparator pinout; this library-name/value difference is not itself a pin-identity defect.
- U10 MAX9924 default ZERO_EN high through JP1 bridged12 and INT_THRS high selects mode A1. EXT floating and external BIAS divider are consistent with A1; mode A2 BIAS grounding warning is inapplicable to saved default.
- VNL5090 drain pad 2 is left unrouted while tab 4 is routed. Manufacturer package identity needs comparison; do not infer an open load merely from unconnected pad 2.
- TLE9201 SPI inputs are unused; assess internal pulls before alleging floating-input dysfunction.

## Boundaries
Stated base rails +5V, +3.3V and 12V_MR/12V_KEY labels only; no invented automotive surge envelope, coil energy, sensor amplitude/frequency, bus cable length, temperature, or harness requirements. Module internals and firmware are not part of the native donor candidate. ERC/DRC are static rules, not operating-point/transient or bus-decoding tests. No hardware measurements available.

## Final experiment commands/results
- Simulator command: `ngspice -b /work/experiments/reset_bias.cir > /work/experiments/reset_bias.log 2>&1`, exit0. The model is native-value ideal resistor network only; source+5V, no current drawn by driver/stepper, MCU undriven. Calculated and ngspice operating-point result2.5V; does not simulate real transistor conduction. Detailed assumptions/outcome in driver_analysis.md section2.
- Python comparison of all numbered pads parsed directly from candidate board vs XML connectivity:694 numbered pads,0 net-name mismatches; result parity_electrical.json. Duplicate numbers on subdivided pads are included. Copper continuity depends on saved copper/DRC; matching net names alone does not prove copper behavior.
- Python SHA256 reads of original native files: native_hashes.json; board matches packet4e059d57...; uppercase/lowercase VR files byte-identical. No candidate was edited.
- Official ST PDF direct urllib downloads timed out; web retrieval succeeded and the rendered official pin/limit records were retained using apply_patch in references/web_driver_sources.json and web_pin_sources.json. These are fetched-reference extracts, not locally generated electrical measurements. Manufacturer URLs and exact tables retained in driver_analysis.md.
- Supplier's public Chinese storefront HTML downloaded and its schema.org Product record extracted to references/LCSC_C2680415_product.json: SKU C2680415,MPN VNL5050N3TR-E. Public English storefront was blocked to urllib(403), but web tool retrieved it. Both agree. No authenticated session or credentials used.
- Extra public product lookups C18567=MAX9142ESA+T, C143633=MAX9924UAUB+, C36533657=NC7SB3157P6X(Tudi), no incompatibility asserted from names alone. Their details were not exhaustively qualified.
- Analysis, calculations, model restrictions, findings/non-findings and omissions are consolidated in driver_analysis.md. No VNL, MCU, CAN or wideband dynamic macro-model used. No hardware experiment or fitted board measurement performed.

## Coverage omissions
No module-revision/firmware matching; no Atlas internals or integrated FOME-MC verification; no complete vehicle harness model; no end-to-end CAN waveform/bit-error/termination-impedance measurement; no VR waveform sweep, comparator recovery or latch startup simulation; no full5V/3.3V worst-case rail budget or sequencing; no ignition/actuator energy, inductive switching or thermal/EMC qualification; no physical manufacturing/assembly approval. No zone refill. Detailed physical DRC/layout adjudication is outside this circuit role. Static native criteria were inspected, not all warnings separately investigated. Reset/brownout/shutdown electrical behavior remains unmeasured except the restricted passive DC reset-bias calculation.

## Additional reference verification
- `kicad-cli sch export netlist --format kicadxml -o /work/experiments/Polygonus_mcu.xml /work/experiments/references/Polygonus_mcu.kicad_sch` exit0. Parsed reference MCU nets into Polygonus_mcu_interface.json: LS9..LS12 map directly to STM32F767ZITx U26 PB5..PB8, IGN9..IGN12 to PG5..PG2; VDD pins connect+3V3. This verifies public module direct3.3V interface rather than assuming5V ignition buffers. Actual installed revision is still not supplied.
- Supplier-linked ST manufacturer datasheet successfully downloaded: references/VNL5050_supplier.pdf/.txt. Fetch URL, SHA256, size and pdftotext outcome retained in references/VNL5050_supplier_fetch.json. This is ST DocID15917 Rev6 published data mirrored by the part supplier. Tables2,6,8 confirm input/supply tie,3.5V minimum and threshold1..3.5V. Public ST direct web extraction also retained in references/web_vnl5050_sources.json.

- Parsed original native IGBT outputs.kicad_sch cached symbol pin identity and instances; igbt_native_identity.json retains mapping. No ignition transient or energy model evaluated.
