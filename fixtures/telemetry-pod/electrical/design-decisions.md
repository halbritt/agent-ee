# Actual pod core electrical inputs

## Scope and authority

These are engineering choices made under the owner's delegation for AGENTEE-207.
They preserve the canonical specification, SHA-256
`09e02c8d9471aabfa5d25935c87f436370cf1be367a96425b35305a53ce93493`.
They are neither owner waivers nor a claim that the pod meets its specification.
The eight-part subset is an input to the eventual complete design. No complete
schematic, PCB, campaign or acceptance evidence is produced here.

## Core selection decisions

| Reference | Captured selection | Reason and source applicability |
| --- | --- | --- |
| U1 | ESP32-S3-WROOM-1-N8, C2913198 | N8 provides the specified 8 MB flash without PSRAM. Espressif module data sheet v1.8, ordering table and pin definitions; exact PDF matches the existing antenna capture. Its 85 C rating conflicts with the separate 105 C requirement. |
| U2 | LSM6DSO32TR, C1864162 | ST's TR order code denotes the tape-and-reel form of the selected LGA14 device. Current supplier-hosted ST PDF has the same pin table as the retained168 source; the older PDF/fact hash is not rewritten. |
| U3 | NEO-M9N-00B, C5119087 | Exact u-blox module identity, supplied as a stocked named part in this observation. A hand-fit exception is not presumed necessary or accepted by this record. R08 data sheet identifies the pins and 25 Hz capability; R10 integration manual supplies unused-pin and supply guidance. |
| U4 | MCP3204-BI/SL, C107363 | Microchip ordering table identifies B accuracy grade, industrial temperature, and the SL SOIC14 package. Its four inputs and VREF remain conditioned, ratiometric boundaries; direct connection to 5 V sensor signals is prohibited. |
| U100 | TJA1051T/3/1J, C38695 | Preserve the exact captured supplier ordering string. NXP's TJA1051T/3 family table describes the SO8 device with VIO on pin 5; the supplier suffix is not silently shortened in native identity. The family document does not itself decode every shipping suffix. |
| D100 | TECH PUBLIC PESD1CAN-UX, C907839 | Preserve the specified LCSC identity and its actual manufacturer. The supplier links a TECH PUBLIC PDF titled PESD1CAN-UX-TP; applicability is a supplier association plus the pin/package/rating comparison below, not Nexperia authorship. |
| R100 | UNI-ROYAL 0603WAF1200T5E, C22787 | Supplier record and series resistance-code table identify 120 ohms in 0603. Preserve the source 120-ohm termination; the 176 60-ohm test is not this selection. |
| C100 | Samsung CL05B104KO5NNNC, C1525 | The captured Samsung catalog identifies 0402, 100 nF, 10%, X7R, 16 V; it retains the source CAN decoupling choice. |

The exact manufacturer's string, package, stock, request timestamp, raw response
hash, acquisition URL and PDF hash are rederived by `part_record.prepare_record`.
Raw stock observations are historical, not inventory reservations. A later
generation must obtain generation-day stock evidence. No source is purchased.

### CAN protection comparison

The scanned four-page TECH PUBLIC source was read visually. Its SOT323 pin
diagram has protection channels between 1/3 and2/3. The retained native source
connects D4.1 to CANL, D4.2 to CANH and D4.3 to GND. The captured stock footprint
has these three pad numbers and the nominal 0.65 mm pitch of the package.

Its electrical table specifies 24 V working voltage, 25 V minimum breakdown at
1 mA,50 V maximum clamp at 3 A for an 8/20 microsecond pulse, and 10 pF typical
capacitance. The rating table lists 3 A/150 W pulse limits and a 125 C operating
ceiling. These claims support retaining the selected supplier item for design
review. They do not prove automotive pulse qualification, continuous fault
survival, production provenance, or circuit-level CAN differential protection.
The NXP transceiver absolute limits include 58 V on either bus pin and 27 V
differential: a 50 V single-channel clamp alone does not prove all combined
conditions are safe. Native and electrical review remain required.

Nexperia C152727 was also captured with zero stock. It is comparison evidence
only and is absent from this selection. The owner has not granted a protection
diode hand-fit exception. No TECH PUBLIC row is renamed Nexperia.

### CAN source and binding adaptation

Reviewed 175 selects U307/C308 from the pinned wbo1 instance and D4/R9 from the
pinned psu instance at source commit
`df304f3321296f7e517e0d2e2aa52e1380d52993`. Reviewed 176 maps them to
U100/C100/D100/R100. Both source sheets and their existing capture/interface/
composition records remain external pinned inputs under `can-source/`.

The derived library bytes have exactly the hashes emitted by reviewed 176. The
included 176 instance record is its retained control, not an actual pod receipt.
A future actual pod composition must derive its own instance from these sources
and its real surrounding terminals. S remains explicitly GND. The source
`+3V3_WBO1` becomes `+3V3`. The source 5 V rail becomes the dedicated `+5V_CAN`
rail, with U100.3 and C100.1 together; it must never be supplied by the 6.3 V
pre-regulator.

The existing source contains blank Datasheet and extra Description/PN/LCSC_ext
properties.174 correctly refuses them when they conflict with its typed binding.
The eventual composition must explicitly report replacement of those properties
by the captured canonical binding; neither omission nor writer text proves an
accepted adaptation. The source properties remain available for comparison.
Source-review qualification under 149/204 remains unverified.

## Pin allocation decisions

`pin-allocation.json` assigns all 108 physical core pins against the exact captured
libraries. The recipe checks the full pin inventory, not just the connected
pins. This is a design input; it is not a native netlist or electrical verdict.

MCU GPIO4/5 serve TWAI transmit/receive. GPIO12/11/13 form the shared SCK/MOSI/MISO
bus; distinct chip selects use GPIO10 and GPIO9. GPIO6 carries the IMU interrupt,
GPIO7 the GNSS pulse, and GPIO17/18 the GNSS UART. GPIO0/EN and UART0 require the
separate programming-pad/pull-up/reset network. Firmware must configure these
GPIO routes. GPIO46 is grounded for UART download when GPIO0 is low; GPIO45 is
unused with its documented internal pull-down. GPIO3 is floating, not internally
pulled: the default unprogrammed eFuse configuration selects USB JTAG regardless
of this pin. No eFuse programming is authorized here.

IMU secondary-interface pins 2/3 are grounded for the primary SPI configuration;
unused INT2 and NC pins remain open. The GNSS uses UART with D_SEL high, USB
data open and V_USB grounded, V_BCKP tied to VCC, and its unused interface and
reserved pins open. The active antenna proposal uses LNA_EN and a separate
current-limited 3V3 feed; VCC_RF remains open. These treatments are tied to the
captured GNSS manual, not inferred from the symbol graphic.

The four ADC input nets are outputs of the still-incomplete protected/divided
analog front end. VREF derives from half the actual sensor rail at the connector.
The precision supply's proposed REF5050 reference must not replace this
ratiometric ADC reference. Harness signal nets are separate from ADC input nets.

## Open obligations

- The full power, analog, antenna, decoupling and programming networks are not
  part of this eight-component manifest. Their parts require real stock/CAD/
  datasheet captures and explicit engineering adoption.
- The independent power proposal splits the precision sensor rail and CAN rail:
  a 200 mA sensor allocation plus the TJA1051's 110 mA bus-short maximum exceeds a
  single 300 mA tracker. Separate 5 V trackers avoid consuming the sensor budget.
  This core record names that boundary but does not certify the supply design.
- Four named core devices stop at 85 C despite the requirement for 105 C parts.
  The contradiction remains unwaived and visible.
- TE lists776228-1 as vertical; the specification calls the same identity
  right-angle. Header orientation, height, footprint qualification, associated
  harness accessories and hand-fit evidence are unresolved.
- No native ERC/DRC, full requirement-map composition, source qualification,
  four-layer routing, fabrication/assembly acceptance or bench criteria 8/9 is
  satisfied by these records. PREVIEW_REQUIRED and owner DFM actions remain.
