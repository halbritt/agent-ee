# PCBSchemaGen Tier-2 Compiler Backlog and Enhancement Roadmap
## 1. Executive Summary
This document provides the definitive catalog and architectural engineering roadmap for the **Tier-2 Compiler Backlog**, encompassing all tasks from the authoritative **PCBSchemaGen** benchmark dataset inventory that are excluded from the initial Tier-1 benchmark evaluation suite.
### 1.1 Dataset Inventory
The PCBSchemaGen benchmark corpus comprises **227 total tasks** across two distinct benchmark suites:
- **PCBBench (`pcbbench.jsonl`)**: **62 tasks** (17 Easy, 28 Medium, 17 Hard) spanning 41 commercial discrete ICs across power conversion, current/voltage sensing, motor drive, digital communications, and memory.
- **OpenSchematics (`open_schematics.jsonl`)**: **165 tasks** (67 Easy, 63 Medium, 35 Hard) extracted from real-world open-hardware repositories, representing 439 commercial components, microcontrollers, and module carriers.
- **Tier-1 Evaluation Suite**: **25 discrete-IC tasks** (18 from PCBBench, 7 from OpenSchematics) strictly compatible with the current `agent-ee` compiler subset (2-layer FR-4, single-unit symbols, <=48V DC, no pre-built modules/breakouts, standard 2.54 mm pin header interfaces).
- **Tier-2 Excluded Compiler Backlog**: **202 tasks** (44 from PCBBench, 158 from OpenSchematics) requiring compiler enhancements across board stackup, symbol representation, thermal geometry, isolation rules, and modular encapsulation.

### 1.2 Backlog Category Breakdown
The 202 excluded benchmark tasks have been systematically triaged into **six documented blocker categories** based on root-cause compiler and physical layout limitations:
| # | Blocker Category | PCBBench (62) | OpenSchematics (165) | Total Backlog (202) | % of Backlog | Primary Engineering Challenge |
|---|---|---|---|---|---|---|
| 1 | Complex modules / DevKit carrier boards | 1 | 44 | **45** | 22.3% | Daughterboard multi-pin footprints, pre-built breakout dependencies |
| 2 | High voltage / isolation barriers (e.g. 400V) | 19 | 2 | **21** | 10.4% | Creepage/clearance rules, isolated planes, 400V safety barriers |
| 3 | Multi-unit symbols (e.g. quad op-amps, logic gates) | 0 | 19 | **19** | 9.4% | KiCad multi-unit symbol splitting (quad op-amps, dual logic gates) |
| 4 | >2 layer boards / high-speed / BGA | 3 | 13 | **16** | 7.9% | 4-layer/6-layer stackups, impedance matching, dense BGA escape |
| 5 | Custom thermal pads / exposed geometry | 6 | 6 | **12** | 5.9% | Heatsink tabs, Kelvin sources (TO-247-4, TOLL), PowerPAD custom vias |
| 6 | Other domain-specific blockers | 15 | 74 | **89** | 44.1% | Multi-IC system complexity, passive matrices, RF modules |
| **Total** | **Excluded Tier-2 Backlog Tasks** | **44** | **158** | **202** | **100.0%** | Comprehensive triage across both benchmark suites |
| *Ref* | *Tier-1 Benchmark Tasks (Active)* | *18* | *7* | *25* | — | *Directly compilable on v1 compiler* |
| **Sum** | **Total Benchmark Dataset Pool** | **62** | **165** | **227** | — | — |

---

## 2. Documented Blocker Categories
### 2.1 Category 1: Complex Modules & DevKit Carrier Boards (45 tasks, 22.3%)
- **Category Tally**: 45 tasks (1 from PCBBench, 44 from OpenSchematics).
- **Technical Description**: Benchmark tasks in this category treat pre-assembled third-party breakout boards, devkits, or daughterboard modules as monolithic components. Physical footprint geometries include dual-row 2.54 mm or 2.0 mm header pinouts, castellated edge modules, and proprietary breakout board dimensions.
- **Compiler Failure Reason**: `agent-ee`'s compiler pipeline (`design_compile.py`) is designed for bare-metal integrated circuit board compilation. It expects standard electronic components with complete schematic netlists and pin definitions. Compiling devkit modules as monolithic ICs forces `agent-ee` to produce carrier breakout shields rather than integrated bare-metal circuit boards. Furthermore, the internal schematic topology and routing of the daughterboard are absent, preventing meaningful electrical DRC and automated placement optimization.
- **Sample Benchmark Tasks**:
  - `Task 7` (PCBBench): `MJISO_DCDC_GATE_SUPPLY` (Murata MGJ2D121505 isolated dual-output DC-DC power converter module).
  - `Task 102` (OpenSchematics): `WINDOW_MONITOR` (ESP32-DevKitC carrier board with reed switches and alert LEDs).
  - `Task 103` (OpenSchematics): `BUZZER_ALERT` (Seeed Studio XIAO ESP32-C3 buzzer and battery charger module).
  - `Task 106` (OpenSchematics): `DUAL_MIC_CAPTURE` (ESP32-S3-DevKitC dual PDM microphone audio capture shield).
  - `Task 112` (OpenSchematics): `OMNI_ROBOT_CTRL` (Raspberry Pi Pico omnidirectional robot motor and sensor controller).
  - `Task 118` (OpenSchematics): `STEPPER_A4988_IF` (Pololu Breakout A4988 stepper driver carrier interface).
  - `Task 123` (OpenSchematics): `SMART_GLOVE_IF` (WeMos D1 mini Wi-Fi glove sensor interface).
  - `Task 128` (OpenSchematics): `ENV_DATA_LOGGER` (Arduino Uno R3 environmental shield with BMP280 and DHT11).
- **Affected Components**: `ESP32-DevKitC`, `ESP32-C3-XIAO`, `ESP32-S3-DevKitC`, `Raspberry Pi Pico`, `Arduino Nano`, `Arduino Uno R3`, `WeMos D1 mini`, `Pololu A4988`, `MGJ2D121505`, `Xiao-RP2040`, `Nice_Nano`.
- **Compiler Remediation Path**: Implement a formal `ModuleAdapter` architecture in `circuit()` that allows external daughterboards to be declared as modular connector sockets. Synthesize dual-row mating headers with Courtyard keepout reservations and clear interface boundaries while abstracting away internal module topology.

### 2.2 Category 2: High Voltage & Galvanic Isolation Barriers (21 tasks, 10.4%)
- **Category Tally**: 21 tasks (19 from PCBBench, 2 from OpenSchematics).
- **Technical Description**: Circuits operating at high voltages (100V DC up to 400V DC-link) or featuring galvanic safety isolation barriers separating high-voltage power domains from low-voltage logic (3.3V / 5V).
- **Compiler Failure Reason**: High-voltage circuits mandate strict safety creepage and clearance distances (typically >=2.5 mm to 4.0 mm under IEC 60664-1), physical milling slots in the FR-4 substrate beneath optocouplers and digital isolators, and disjoint ground domains (e.g., `EARTH`, `PGND`, `GND_ISO`, `AGND`). Currently, `design_compile.py` enforces a single continuous `GND` copper plane on `B.Cu` without creepage DRC or routed isolation keepout slots. Routing high voltage across a single continuous ground plane violates safety isolation rules.
- **Sample Benchmark Tasks**:
  - `Task 2` (PCBBench): `ISO_AMP_HV_SENSE` (AMC1350 isolated voltage amplifier for 400V DC-link sensing).
  - `Task 4` (PCBBench): `ISENSE_HALL_ISO` (ACS37010 galvanically isolated 50A Hall-effect current sensor).
  - `Tasks 8-11` (PCBBench): `HB_TO2473_STAGE`, `HB_TO2474K_SIC_STAGE`, `HB_TOLL_STAGE`, `HB_TOLT_TOPCOOL_STAGE` (400V SiC/MOSFET half-bridge switching stages).
  - `Tasks 15-16` (PCBBench): `DRV_ISOLATED_GATE`, `DRV_PROTECTED_DESAT` (UCC5390E, UCC21710 isolated gate drivers with DESAT protection).
  - `Tasks 17-23` (PCBBench): `CONV_BUCK_SYNC`, `CONV_DAB_ISOLATED`, `CONV_LLC_RESONANT`, `DRIVE_3PH_MOTOR`, `INV_GRID_1PH` (100V-400V DC-DC converters, inverters, and motor drives).
  - `Task 24` (PCBBench): `FILTER_HV_PI` (400V DC-link differential Pi filter).
  - `Task 140` (OpenSchematics): `ISO_5V_PSU` (CUI PDS1-S5-S5-D isolated 5V power supply).
  - `Task 161` (OpenSchematics): `EXT_ESP32C3_BASICDISTANCE` (AC mains power input with bridge rectifier and PoE isolation transformer).
- **Affected Components**: `AMC1350`, `ACS37010`, `UCC5390E`, `UCC21710`, `IMZA65R015M2H`, `ISO7741`, `ADUM1250`, `PDS1-S5-S5-D`, `EL817`, `PC817`.
- **Compiler Remediation Path**: Introduce multi-zone copper plane flood support (`plane` array specifying multiple distinct return nets), keepout geometry generation across isolation boundaries, substrate milling slot router primitives, and clearance-class assignment in native DRC.

### 2.3 Category 3: Multi-Unit Symbols (19 tasks, 9.4%)
- **Category Tally**: 19 tasks (0 from PCBBench, 19 from OpenSchematics).
- **Technical Description**: Integrated circuits represented in KiCad libraries as multi-unit symbols (e.g., dual op-amps with Unit A and Unit B, quad op-amps with Units A-D, hex inverters, quad logic gates, and separate power units).
- **Compiler Failure Reason**: `design_compile.py` expects a strict 1-to-1 correspondence between symbol pins and physical footprint lands (`symbol and footprint pad-number sets must agree`). When a multi-unit symbol is declared in `agent-ee.design/1`, the individual unit symbol exposes only a subset of the physical footprint's pads (e.g., Unit A has 3 pins, whereas the SOIC-8 footprint has 8 pads), causing an immediate compiler rejection.
- **Sample Benchmark Tasks**:
  - `Task 121` (OpenSchematics): `RGB_LAMP_CTRL` (HEF4093BP quad 2-input NAND Schmitt trigger).
  - `Task 122` (OpenSchematics): `ESP_POWER_BOARD` (74AHCT1G08 logic gate).
  - `Task 127` (OpenSchematics): `SFP_ANALOG_IF` (74HC14 hex inverting Schmitt trigger + TL071 op-amp).
  - `Task 129` (OpenSchematics): `ROVER_MOTOR_CTRL` (LM358 dual operational amplifiers in motor sense circuit).
  - `Task 149` (OpenSchematics): `LOGIC_BUS_ADDR_DECODER` (74HC541, 74LS32, 74LS08, 74HCT573 bus decoders).
  - `Task 152` (OpenSchematics): `LINE_FOLLOW_ROBOT_CTRL` (LM358 dual op-amp + 74HC14 hex inverter).
  - `Task 167` (OpenSchematics): `EXT_OPENOBLONG` (74HC04 hex inverter + 74LS08 quad AND gates).
  - `Task 171` (OpenSchematics): `EXT_PICO_ENCODER` (74LS32 quad OR + 74LS00 quad NAND gates).
  - `Task 186` (OpenSchematics): `EXT_MILLENNIUM` (4011 quad 2-input NAND gates).
  - `Task 196` (OpenSchematics): `EXT_SEEDSIGNER_LUCKFOX` (Opamp_Dual dual audio pre-amplifier).
  - `Task 201` (OpenSchematics): `HEAT_CTRL` (LM358 thermal comparator circuit).
  - `Task 231` (OpenSchematics): `OPTOGENETIC_SENSE` (MCP6004 quad operational amplifiers).
- **Affected Components**: `LM358`, `LM324`, `74HC14`, `74HC04`, `74LS32`, `74LS08`, `74LS00`, `4011`, `HEF4093BP`, `MCP6004`, `Opamp_Dual`, `74AHCT125`.
- **Compiler Remediation Path**: Enhance `circuit()` and `design_shape.py` to support unit suffixes (e.g. `U1A.1`, `U1B.7`, `U1.VCC`) and aggregate multi-unit declarations into a single physical component footprint during netlist generation.

### 2.4 Category 4: >2 Layer Boards & High-Speed / BGA (16 tasks, 7.9%)
- **Category Tally**: 16 tasks (3 from PCBBench, 13 from OpenSchematics).
- **Technical Description**: Advanced microcontrollers, FPGAs, high-speed memory interfaces, and high-frequency communication links requiring multi-layer board stackups (4-layer, 6-layer) and controlled-impedance trace geometries.
- **Compiler Failure Reason**: `design_compile.py` line 69 explicitly enforces a 2-copper-layer limit (`only two copper layers are supported; set 2`). High-speed parallel buses (SDRAM), differential pairs (PCIe x1, USB 2.0 High-Speed 480 Mbps), and fine-pitch BGA or dense QFN-56 packages cannot route cleanly or meet signal integrity constraints on 2 layers without continuous reference planes and microvia escape routing.
- **Sample Benchmark Tasks**:
  - `Task 59` (PCBBench): `SDRAM_INTERFACE_W9825G6` (Winbond W9825G6KH 16-bit 166 MHz SDRAM parallel memory bus).
  - `Task 60` (PCBBench): `PCIE_X1_ENDPOINT` (PCIe Gen2/3 differential high-speed edge connector).
  - `Task 65` (PCBBench): `MCU_SDRAM_SYSTEM` (STM32F103 + W9825G6KH SDRAM memory subsystem).
  - `Task 101` (OpenSchematics): `SFP2SMA_CTRL` (RP2040 dual-core MCU in dense QFN-56 with external SPI flash).
  - `Task 134` (OpenSchematics): `RP2040_UVC_ADAPTER` (RP2040 high-density USB video class interface).
  - `Task 160` (OpenSchematics): `CY7C68013A_USB_RFM69_RX` (Cypress CY7C68013A High-Speed USB 2.0 FX2LP controller).
  - `Task 199` (OpenSchematics): `EXT_RIO_ICEBREAKER3X` (Lattice iCE40 FPGA carrier).
  - `Task 202` (OpenSchematics): `STM32_DECOUPLE` (STM32L431 64-pin dense microcontroller).
  - `Task 206` (OpenSchematics): `STM32_BLUET_PILL` (STM32F103 minimum system with TagConnect SWD).
  - `Task 238` (OpenSchematics): `COLORLIGHT_I5_MB` (Colorlight i5 FPGA module carrier).
  - `Task 241` (OpenSchematics): `ECP5_FPGA` (Lattice ECP5 256-ball fine-pitch BGA FPGA board).
  - `Task 252` (OpenSchematics): `STM32G0_DEVBOARD` (STM32G0 ARM Cortex-M0+ development board).
  - `Task 257` (OpenSchematics): `STM32F407_BOARD` (STM32F407 168 MHz ARM Cortex-M4 100-pin LQFP board).
- **Affected Components**: `W9825G6KH`, `RP2040`, `CY7C68013A`, `STM32F407`, `STM32L431`, `Lattice ECP5 BGA-256`, `iCE40`, `PCIe-x1`.
- **Compiler Remediation Path**: Integrate standard 4-layer and 6-layer stackup templates (`JLC04161H-7628`, `JLC06161H-7628`) into `design_compile.py`, add inner-layer plane floods (`In1.Cu` GND, `In2.Cu` Power), and incorporate differential pair impedance rules into `kicad-route`.

### 2.5 Category 5: Custom Thermal Pads & Exposed Geometry (12 tasks, 5.9%)
- **Category Tally**: 12 tasks (6 from PCBBench, 6 from OpenSchematics).
- **Technical Description**: Power semiconductor packages featuring exposed copper thermal pads (PowerPAD, D2PAK, TO-220 heatsink tabs, TO-247-4 with Kelvin source, TOLL, TOLT) requiring thermal via arrays or specialized land geometries.
- **Compiler Failure Reason**: High-power switching transistors and motor drivers dissipate substantial heat through bottom or top exposed pads that must be stitched with thermal via arrays to inner or opposite copper planes. In packages like TO-247-4, a separate Kelvin source terminal is required to eliminate $L \cdot di/dt$ inductive gate bounce. `design_compile.py` refuses footprints where pad numbers cannot be derived deterministically, and cannot yet synthesize custom polygon thermal pads or via stitching.
- **Sample Benchmark Tasks**:
  - `Task 8` (PCBBench): `HB_TO2473_STAGE` (Infineon IMW65R015M2H TO-247-3 power MOSFET).
  - `Task 9` (PCBBench): `HB_TO2474K_SIC_STAGE` (Infineon IMZA65R015M2H TO-247-4 SiC MOSFET with separate Kelvin source pin).
  - `Task 10` (PCBBench): `HB_TOLL_STAGE` (Infineon IMT65R033M2H TOLL leadless power package).
  - `Task 11` (PCBBench): `HB_TOLT_TOPCOOL_STAGE` (Infineon IMLT65R015M2H TOLT top-side cooled power package).
  - `Task 12` (PCBBench): `HB_QFN_INTEGRATED_STAGE` (Infineon BSC052N08NS5 dual integrated half-bridge QFN-8x8).
  - `Task 57` (PCBBench): `STEPPER_DRIVER_FULL` (TI DRV8825 HTSSOP-28 with exposed thermal PowerPAD).
  - `Task 151` (OpenSchematics): `RC_CAR_ESP32_MOTOR_CTRL` (Power diodes and discrete power stages).
  - `Task 156` (OpenSchematics): `ESP32S2_ROBOT_MOTOR_CTRL` (BD249 power transistors + LM350 TO-220 with heatsink tabs).
  - `Task 157` (OpenSchematics): `ATMEGA_3CH_MOSFET_CTRL` (IRF540N TO-220 power MOSFETs).
  - `Task 158` (OpenSchematics): `LM2596_ADJ_BUCK_REG` (LM2596 TO-263 / D2PAK exposed thermal pad).
  - `Task 216` (OpenSchematics): `BMS_SWITCH` (IRLB8721PBF TO-220 power MOSFET switch).
  - `Task 261` (OpenSchematics): `AUTO_HIGHSIDE_SW` (Infineon BTS700n-1EPP PG-TSDSO-14 exposed pad smart switch).
- **Affected Components**: `IMW65R015M2H`, `IMZA65R015M2H`, `IMT65R033M2H`, `IMLT65R015M2H`, `BSC052N08NS5`, `DRV8825`, `BD249`, `LM350_TO220`, `IRF540N`, `LM2596`, `IRLB8721PBF`, `BTS700n-1EPP`.
- **Compiler Remediation Path**: Implement thermal via matrix generators in `placement()` and add Kelvin-source topology awareness in net generation.

### 2.6 Category 6: Other Domain-Specific Blockers (89 tasks, 44.1%)
- **Category Tally**: 89 tasks (15 from PCBBench, 74 from OpenSchematics).
- **Technical Description**: Circuits presenting domain-specific constraints outside the core active IC compilation model:
  1. **Complex Multi-IC Subsystems (42 tasks)**: Multi-chip systems (e.g. BMS AFE + MCU + CAN transceiver, isolated DAQ with 4 ADCs and voltage refs) with high component counts (>30-50 parts) and multiple clock domains that exceed initial single-seed placement runtime bounds.
  2. **Pure Passive / Matrix / Connector-Only Circuits (28 tasks)**: Circuits consisting entirely of passive resistor/capacitor networks, switch matrices (e.g. 42-key mechanical keyboard), or connector breakout adapters without an active discrete IC.
  3. **Specialized RF / Wireless Subsystems (19 tasks)**: Sub-GHz or microwave RF modules (e.g. RFM69CW FSK transceiver), GPS/GNSS timing receivers, and high-frequency edge fingers requiring coplanar waveguide routing and antenna impedance matching.
- **Compiler Failure Reason**: Passive-only circuits fail the IC-centric compilation model. Large multi-IC systems encounter placement annealing timeouts and routing congestion without hierarchical block decomposition. RF circuits lack controlled-impedance coplanar waveguide trace generators in `kicad-route`.
- **Sample Benchmark Tasks**:
  - `Task 1` (PCBBench): `VDIV_BUS_SENSE` (Resistor voltage divider).
  - `Task 26` (PCBBench): `SENSE_NTC_TEMP` (NTC thermistor temperature sense divider).
  - `Task 36` (PCBBench): `MCU_CRYSTAL_NETWORK` (Passive crystal load capacitor network).
  - `Task 49` (PCBBench): `BMS_5S_FULL_SYSTEM` (BQ76920 + BQ76200 + CAN transceiver full BMS system).
  - `Task 50` (PCBBench): `BLDC_FOC_CONTROLLER` (STM32 + 6 gate drivers + inverter stage).
  - `Task 51` (PCBBench): `ISOLATED_DAQ_4CH` (4-channel ADC + digital isolator + precision ref).
  - `Task 61` (PCBBench): `REVERSE_POLARITY_PMOS` (P-channel MOSFET polarity protection circuit).
  - `Task 109` (OpenSchematics): `COAX_BLANKING` (Coaxial blanking connector distributor).
  - `Task 126` (OpenSchematics): `AVR_RADIO_NODE` (ATmega328P + FT232RL + RFM69CW RF transceiver).
  - `Task 130` (OpenSchematics): `MECH_KBD_CTRL` (42-key mechanical keyboard diode-switch matrix).
  - `Task 175` (OpenSchematics): `EXT_SPARKFUN_GNSSDO` (SparkFun GPS Disciplined Oscillator timing board).
  - `Task 182` (OpenSchematics): `EXT_FAMICOM_EXPANSION_PORT` (Famicom expansion port connector card).
- **Affected Components**: `BQ76920`, `BQ76200`, `BQ25895`, `W5500`, `TPS3808`, `RFM69CW`, `SW_PUSH-MX`, `Jack-DC`.
- **Compiler Remediation Path**: Implement hierarchical sub-circuit placement clustering, multi-seed parallel annealing, and coplanar waveguide RF routing rules in `kicad-route`.

---

## 3. Complete Itemized Catalog of the 202 Excluded Tasks

The table below catalogs every single excluded benchmark task from `pcbbench.jsonl` (44 tasks) and `open_schematics.jsonl` (158 tasks), documenting its dataset origin, difficulty level, primary component(s), blocker category, and concrete technical exclusion reason.

| # | Task ID | Dataset | Submodule Name | Level | Primary Component(s) | Blocker Category | Technical Exclusion Reason |
|---|---|---|---|---|---|---|---|
| 1 | `1` | PCBBench | `VDIV_BUS_SENSE` | Easy | Resistors | Other domain-specific blockers | Pure passive divider/network or crystal load circuit lacking an active discrete IC. |
| 2 | `2` | PCBBench | `ISO_AMP_HV_SENSE` | Easy | AMC1350 | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 3 | `4` | PCBBench | `ISENSE_HALL_ISO` | Easy | ACS37010 | High voltage / isolation barriers | High operating voltage (50AV) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 4 | `7` | PCBBench | `MJISO_DCDC_GATE_SUPPLY` | Medium | MGJ2D121505 | Complex modules / DevKit carrier boards | Through-hole modular DC-DC brick converter (MGJ2D121505); requires modular package adapter. |
| 5 | `8` | PCBBench | `HB_TO2473_STAGE` | Medium | IMW65R015M2H | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 6 | `9` | PCBBench | `HB_TO2474K_SIC_STAGE` | Medium | IMZA65R015M2H | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 7 | `10` | PCBBench | `HB_TOLL_STAGE` | Medium | IMT65R033M2H | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 8 | `11` | PCBBench | `HB_TOLT_TOPCOOL_STAGE` | Medium | IMLT65R015M2H | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 9 | `12` | PCBBench | `HB_QFN_INTEGRATED_STAGE` | Medium | BSC052N08NS5 | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 10 | `15` | PCBBench | `DRV_ISOLATED_GATE` | Medium | UCC5390E | High voltage / isolation barriers | High operating voltage (3.3V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 11 | `16` | PCBBench | `DRV_PROTECTED_DESAT` | Medium | UCC21710 | High voltage / isolation barriers | High operating voltage (5V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 12 | `17` | PCBBench | `CONV_BUCK_SYNC` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (200V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 13 | `18` | PCBBench | `CONV_BOOST_SYNC` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (100V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 14 | `19` | PCBBench | `CONV_4SW_BUCKBOOST` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (200V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 15 | `20` | PCBBench | `CONV_DAB_ISOLATED` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 16 | `21` | PCBBench | `CONV_LLC_RESONANT` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 17 | `22` | PCBBench | `DRIVE_3PH_MOTOR` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 18 | `23` | PCBBench | `INV_GRID_1PH` | Hard | IMZA65R015M2H | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 19 | `24` | PCBBench | `FILTER_HV_PI` | Easy | C_film, Inductor_power | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 20 | `26` | PCBBench | `SENSE_NTC_TEMP` | Easy | NTC, R, C | Other domain-specific blockers | Pure passive divider/network or crystal load circuit lacking an active discrete IC. |
| 21 | `34` | PCBBench | `MCU_STM32_MIN_SYSTEM_PWR` | Medium | STM32F103C8T6 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 22 | `35` | PCBBench | `MCU_STM32_MIN_SYSTEM` | Medium | STM32F103C8T6 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 23 | `36` | PCBBench | `MCU_CRYSTAL_NETWORK` | Medium | Crystal, C_load | Other domain-specific blockers | Pure passive divider/network or crystal load circuit lacking an active discrete IC. |
| 24 | `39` | PCBBench | `USB_UART_BRIDGE` | Medium | CP2102N, TPD4E05U06 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 25 | `40` | PCBBench | `ISO_SPI_4CH` | Medium | ISO7741 | High voltage / isolation barriers | High operating voltage (3.3V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 26 | `41` | PCBBench | `ISO_I2C_BIDIR` | Medium | ADUM1250 | High voltage / isolation barriers | High operating voltage (3.3V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 27 | `45` | PCBBench | `PWR_TREE_12V_3V3D_3V3A` | Medium | TPS54302, TLV1117-33 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 28 | `46` | PCBBench | `CAN_ISOLATED_FULL` | Medium | ISO7741, SN65HVD230, NXE1S0303MC | High voltage / isolation barriers | High operating voltage (3.3V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 29 | `47` | PCBBench | `BMS_AFE_5S_BQ76920` | Medium | BQ76920 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 30 | `49` | PCBBench | `BMS_5S_FULL_SYSTEM` | Hard | BQ76920, BQ76200, SN65HVD230 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 31 | `50` | PCBBench | `BLDC_FOC_CONTROLLER` | Hard | STM32F103C8T6, UCC5390E, IMZA65R015M2... | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 32 | `51` | PCBBench | `ISOLATED_DAQ_4CH` | Hard | ADS7042, ISO7741, NXE1S0303MC, REF3033 | High voltage / isolation barriers | High operating voltage (3.3V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 33 | `52` | PCBBench | `IOT_SENSOR_NODE` | Hard | STM32F103C8T6, SN65HVD230, TPS54302, ... | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 34 | `54` | PCBBench | `LI_ION_CHARGER_FULL` | Hard | BQ25895, INA226, TPD4E05U06 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 35 | `55` | PCBBench | `DAB_WITH_CONTROL` | Hard | STM32F103C8T6, SN65HVD230, INA240A1, ... | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 36 | `56` | PCBBench | `ETHERNET_SPI_W5500` | Hard | W5500 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 37 | `57` | PCBBench | `STEPPER_DRIVER_FULL` | Medium | DRV8825, TPS54302 | Custom thermal pads / exposed geometry | Exposed thermal pad (PowerPAD, TO-247-4 with Kelvin source, TOLL/TOLT); requires thermal via stitching and custom land pattern. |
| 38 | `58` | PCBBench | `VOLTAGE_SUPERVISOR` | Medium | TPS3808 | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 39 | `59` | PCBBench | `SDRAM_INTERFACE_W9825G6` | Medium | W9825G6KH | >2 layer boards / high-speed / BGA | Parallel bus (SDRAM) or differential PCIe high-speed signaling; requires >=4 copper layers with internal power/ground planes. |
| 40 | `60` | PCBBench | `PCIE_X1_ENDPOINT` | Medium | Connector | >2 layer boards / high-speed / BGA | Parallel bus (SDRAM) or differential PCIe high-speed signaling; requires >=4 copper layers with internal power/ground planes. |
| 41 | `61` | PCBBench | `REVERSE_POLARITY_PMOS` | Medium | PMOS, R, C | Other domain-specific blockers | Pure passive divider/network or crystal load circuit lacking an active discrete IC. |
| 42 | `62` | PCBBench | `MCU_SYNC_BUCK_SYSTEM` | Hard | STM32F103C8T6, UCC5390E, IMZA65R015M2... | Other domain-specific blockers | Complex multi-IC subsystem with extensive power tree and high component count exceeding single-seed placement limits. |
| 43 | `64` | PCBBench | `MCU_3PH_INVERTER_FULL` | Hard | STM32F103C8T6, UCC5390E, IMZA65R015M2... | High voltage / isolation barriers | High operating voltage (400V) or galvanic isolation barrier; requires IEC 60664-1 clearance/creepage and disjoint ground domains. |
| 44 | `65` | PCBBench | `MCU_SDRAM_SYSTEM` | Hard | STM32F103C8T6, W9825G6KH, TLV1117-33 | >2 layer boards / high-speed / BGA | Parallel bus (SDRAM) or differential PCIe high-speed signaling; requires >=4 copper layers with internal power/ground planes. |
| 45 | `101` | OpenSchematics | `SFP2SMA_CTRL` | Medium | 1x RP2040, 1x W25Q32JVSS, 1x Crystal_... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 46 | `102` | OpenSchematics | `WINDOW_MONITOR` | Easy | 1x ESP32-DevKitC, 3x LED, 3x SW_Reed | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 47 | `103` | OpenSchematics | `BUZZER_ALERT` | Easy | 1x ESP32-C3-XIAO, 1x TP4056_5V_1A, 1x... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 48 | `105` | OpenSchematics | `PHOTO_SENSOR` | Easy | 1x Q_Photo_NPN, 1x Conn_01x06_Socket | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 49 | `106` | OpenSchematics | `DUAL_MIC_CAPTURE` | Easy | 1x ESP32-S3-DevKitC, 1x WeMos_D1_mini... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 50 | `107` | OpenSchematics | `HEARING_SENSOR` | Hard | 1x ESP32DEVKITV1, 1x module_mpu6050, ... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 51 | `108` | OpenSchematics | `IMU_BREAKOUT` | Medium | 1x LSM6DS3, 2x R_Small, 2x C_Small, 1... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 52 | `109` | OpenSchematics | `BLANKING_DIST` | Medium | 6x Conn_Coaxial, 1x Conn_01x07_Female | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 53 | `110` | OpenSchematics | `SMART_BIN_KEYPAD` | Hard | 1x XIAO-RP2040-DIP, 9x SW_Push, 9x D,... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 54 | `111` | OpenSchematics | `RTC_DS3231` | Hard | 1x DS3231-keyboard, 1x C_Small-device | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 55 | `112` | OpenSchematics | `OMNI_ROBOT_CTRL` | Medium | 1x Pico, 14x Conn_01x04, 1x Conn_01x0... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 56 | `113` | OpenSchematics | `USBC_TOUCH_BREAKOUT` | Medium | 1x USB_C_Receptacle_PowerOnly_6P, 2x ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 57 | `114` | OpenSchematics | `OPTO_LED_DRIVER_8CH` | Medium | 24x BC847, 8x EL817, 8x LED, 48x R | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 58 | `115` | OpenSchematics | `PCB_BUSINESS_CARD` | Medium | 1x ESP32-C3-MINI-1, 1x SSD1306-SPI-OL... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 59 | `116` | OpenSchematics | `TTL_BLANK_REPEATER` | Easy | 2x Q_NPN_CBE, 2x SW_SPDT, 4x D, 4x R,... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 60 | `117` | OpenSchematics | `OLED_BTN_MODULE` | Medium | 1x OLED_SSD1306_128X64, 2x B3W-4050, ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 61 | `118` | OpenSchematics | `STEPPER_A4988_IF` | Hard | 1x Pololu_Breakout_A4988, 3x Conn_01x... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 62 | `119` | OpenSchematics | `RGB_MACROPAD` | Medium | 1x XIAO-RP2040-DIP, 9x SW_Push, 9x 1N... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 63 | `120` | OpenSchematics | `BLDC_DRYVE_IF` | Easy | 1x Igus_dryve_D1, 1x Brushless_Motor_... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 64 | `121` | OpenSchematics | `RGB_LAMP_CTRL` | Medium | 1x HEF4093BP_652, 1x LED, 1x R, 1x US... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 65 | `122` | OpenSchematics | `ESP_POWER_BOARD` | Medium | 16x Conn_01x01, 8x Resistor_0603, 4x ... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 66 | `123` | OpenSchematics | `SMART_GLOVE_IF` | Easy | 1x WeMos_D1_mini, 4x Conn_01x05_Pin, ... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 67 | `124` | OpenSchematics | `IGNITION_SENSE` | Easy | 5x RES_0603, 3x IgnitionScrewTerminal... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 68 | `125` | OpenSchematics | `GPIO_EXP_INPUT` | Hard | 14x R, 8x SW_Push, 4x TestPoint, 1x P... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 69 | `126` | OpenSchematics | `AVR_RADIO_NODE` | Medium | 12x C, 5x R, 3x LED, 1x ATmega328P-A,... | Other domain-specific blockers | RF/wireless or non-standard coplanar waveguide routing requiring controlled RF trace geometry. |
| 70 | `127` | OpenSchematics | `SFP_ANALOG_IF` | Easy | 27x R, 25x C, 8x L, 4x 74HC14, 3x TL0... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 71 | `128` | OpenSchematics | `ENV_DATA_LOGGER` | Hard | 2x BMP280, 1x Arduino_UNO_R3, 1x DHT1... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 72 | `129` | OpenSchematics | `ROVER_MOTOR_CTRL` | Hard | 11x C_Small, 9x R_US, 6x LM358, 6x C_... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 73 | `130` | OpenSchematics | `MECH_KBD_CTRL` | Hard | 42x SW_PUSH-MX_W_LED, 22x D_Small, 21... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 74 | `131` | OpenSchematics | `MULTI_SENSOR_DAQ` | Medium | 4x R, 1x Arduino_Nano, 1x LSM6DSOX, 1... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 75 | `132` | OpenSchematics | `STEPPER_ULTRASONIC` | Medium | 4x R, 1x A4988_STEPPER_MOTOR_DRIVER_C... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 76 | `133` | OpenSchematics | `LED_ARRAY_CTRL` | Hard | 20x LED_Filled, 8x TestPoint, 5x R, 1... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 77 | `134` | OpenSchematics | `RP2040_UVC_ADAPTER` | Medium | 17x C_Small, 11x R_Small, 8x Mounting... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 78 | `135` | OpenSchematics | `FLIGHT_PWR_MGMT` | Hard | 11x R, 6x C, 3x SW_SPST, 3x Conn_01x0... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 79 | `136` | OpenSchematics | `STM32_CAN_IF` | Medium | 11x Csmall, 5x RSMALL, 2x Mount_Hole_... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 80 | `137` | OpenSchematics | `PLANT_WATER_CTRL` | Easy | 11x C, 8x Conn_01x01_Pin, 6x R, 4x C_... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 81 | `138` | OpenSchematics | `RPI_AUDIO_DAQ` | Hard | 3x MCP3201, 3x Adafruits MAX4466 , 3x... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 82 | `139` | OpenSchematics | `ROBOT_EXPANSION` | Hard | 6x C, 6x R, 2x DMG3414U, 2x DMG2301L,... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 83 | `140` | OpenSchematics | `ISO_5V_PSU` | Hard | 6x C, 1x PDS1-S5-S5-D, 1x L78L05_TO92... | High voltage / isolation barriers | Galvanic isolation or mains power interface; requires isolation slot routing and split ground plane flood. |
| 84 | `141` | OpenSchematics | `ESP12E_IR_REMOTE_CTRL` | Medium | {'R': 5, 'Conn_01x01_Socket': 2, 'Mou... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 85 | `142` | OpenSchematics | `ESP32_EBIKE_CTRL` | Easy | {'R': 3, 'C': 2, 'Conn_01x03': 2, 'Sp... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 86 | `143` | OpenSchematics | `W5500_ETH_RJ45_SHIELD` | Easy | {'R': 22, 'C': 9, 'C_Small': 6, 'Test... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 87 | `144` | OpenSchematics | `CHARLIEPLEX_LED_CHARGER` | Medium | {'R': 10, 'C': 4, 'SS-52400-002': 2, ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 88 | `145` | OpenSchematics | `BLDC_DRV10983_DRIVER` | Hard | {'R': 13, 'C': 6, 'LED': 4, 'Screw_Te... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 89 | `147` | OpenSchematics | `USB_NEOPIXEL_LEVELSHIFT` | Hard | {'R_Small': 7, 'C_Small': 3, 'BSS138'... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 90 | `148` | OpenSchematics | `WIFI_BIT_NAND_CARRIER` | Hard | {'Conn_01x01': 36, 'TestPoint': 4, 'R... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 91 | `149` | OpenSchematics | `LOGIC_BUS_ADDR_DECODER` | Medium | {'VN2106': 8, '74HC541': 4, '74LS32':... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 92 | `150` | OpenSchematics | `CPLD_FLASH_LOGIC_BOARD` | Easy | {'C_Small': 5, 'R_Small_US': 5, 'Conn... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 93 | `151` | OpenSchematics | `RC_CAR_ESP32_MOTOR_CTRL` | Medium | {'Screw_Terminal_01x02': 8, 'R': 7, '... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 94 | `152` | OpenSchematics | `LINE_FOLLOW_ROBOT_CTRL` | Hard | {'R': 6, 'LM358': 5, '74HC14': 4, 'C'... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 95 | `153` | OpenSchematics | `ESP32_SENSOR_HUB` | Hard | {'TestPoint': 18, 'C': 9, 'R': 8, 'MA... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 96 | `154` | OpenSchematics | `ATMEGA32U4_USB_HID` | Easy | {'C': 10, 'R': 6, 'Polyfuse': 1, 'ATm... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 97 | `155` | OpenSchematics | `CH32V003_LED_MATRIX_CTRL` | Hard | {'R': 20, 'LED': 15, 'SW_Push': 5, 'B... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 98 | `156` | OpenSchematics | `ESP32S2_ROBOT_MOTOR_CTRL` | Hard | {'R': 14, 'LED': 4, 'BD249': 3, 'LM35... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 99 | `157` | OpenSchematics | `ATMEGA_3CH_MOSFET_CTRL` | Medium | {'IRF540N': 3, 'CP1-Device': 3, 'ATme... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 100 | `158` | OpenSchematics | `LM2596_ADJ_BUCK_REG` | Medium | {'C_Polarized_Small': 3, 'C_Small': 3... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 101 | `159` | OpenSchematics | `LEVEL_XLAT_UART_GPIO` | Medium | {'2.2uF_0603_10V_20%': 8, '0.1uF_0603... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 102 | `160` | OpenSchematics | `CY7C68013A_USB_RFM69_RX` | Medium | {'C_Small': 11, 'R_Small': 7, 'Jumper... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 103 | `161` | OpenSchematics | `EXT_ESP32C3_BASICDISTANCE` | Easy | 19x R_US, 16x C, 4x D, 2x D_Bridge_+-... | High voltage / isolation barriers | Galvanic isolation or mains power interface; requires isolation slot routing and split ground plane flood. |
| 104 | `162` | OpenSchematics | `EXT_SPACE_INVADER_280` | Medium | 3x R, 3x CAP, 2x SW_Push, 1x Crystal,... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 105 | `163` | OpenSchematics | `EXT_OMEGAFLEX` | Medium | 10x Fuse, 10x 74AHCT125, 8x Jumper_3_... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 106 | `164` | OpenSchematics | `EXT_RADIO_SI4735` | Hard | 10x R, 6x C, 3x Conn_01x12, 2x C_Pola... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 107 | `165` | OpenSchematics | `EXT_HOKIE_SAT_EXPERIMENTAL` | Hard | 8x R, 5x C, 2x D, 2x Conn_01x02_Pin, ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 108 | `166` | OpenSchematics | `EXT_DARKPASSENGER` | Hard | 8x R, 4x MountingHole, 4x C_Small, 3x... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 109 | `167` | OpenSchematics | `EXT_OPENOBLONG` | Medium | 12x C_Small, 8x TestPoint, 7x 74HC04,... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 110 | `168` | OpenSchematics | `EXT_KLOR` | Medium | 16x 0603R, 14x 0603C, 5x 0402R, 5x 04... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 111 | `169` | OpenSchematics | `EXT_BREADBOARD_POWER_SUPPLY` | Hard | 14x C, 12x R, 7x LED, 4x Conn_01x03_P... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 112 | `170` | OpenSchematics | `EXT_DAISYSEEDPROJECTS` | Medium | 7x R_US, 5x C, 4x C_Polarized, 2x R, ... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 113 | `171` | OpenSchematics | `EXT_PICO_ENCODER` | Hard | 10x C, 10x 74LS32, 5x 74LS00, 2x Jump... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 114 | `172` | OpenSchematics | `EXT_DYNAMIC_SPEED_GOVERNOR` | Medium | 25x C, 23x R, 15x MountingHole, 11x S... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 115 | `173` | OpenSchematics | `EXT_CYCLOPE` | Medium | 5x R, 4x Conn_01x04, 4x MountingHole,... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 116 | `174` | OpenSchematics | `EXT_6502_MACROPROCESSOR` | Medium | 8x R, 4x MountingHole_Pad, 3x C, 2x E... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 117 | `175` | OpenSchematics | `EXT_SPARKFUN_GNSSDO` | Medium | 25x C, 12x R, 2x D_Schottky, 2x Conn_... | Other domain-specific blockers | RF/wireless or non-standard coplanar waveguide routing requiring controlled RF trace geometry. |
| 118 | `177` | OpenSchematics | `EXT_ARDUINO_NANO_SCRATCH` | Medium | 8x 0603ZC104JAT2A, 4x MCT06030C1002FP... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 119 | `178` | OpenSchematics | `EXT_TAKE_HOME_MAGLEV_LAB` | Medium | 25x C, 16x R, 6x L, 5x Conn_01x02_Pin... | Other domain-specific blockers | RF/wireless or non-standard coplanar waveguide routing requiring controlled RF trace geometry. |
| 120 | `179` | OpenSchematics | `EXT_PRACTICUM` | Hard | 4x Standoff, 4x 1k_0603, 4x Fiducial_... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 121 | `180` | OpenSchematics | `EXT_SOLARSYSTEM` | Hard | 11x R, 8x C, 4x MountingHole, 4x Sold... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 122 | `182` | OpenSchematics | `EXT_FAMICOM_EXPANSION_PORT` | Easy | 26x R, 10x C, 6x C_Polarized_Small, 6... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 123 | `183` | OpenSchematics | `EXT_NIXIE_TUBE_CLOCK` | Easy | 17x C_Small, 8x R_Small, 7x choc_SW_H... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 124 | `184` | OpenSchematics | `EXT_LIQUID_ENGINE_CONTROLLER` | Easy | 11x Conn_01x01_Pin, 8x C, 5x R_US, 4x... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 125 | `185` | OpenSchematics | `EXT_OPENGAMMAKIT` | Hard | 9x C_Small, 5x R_US, 5x PINHD-1X2, 5x... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 126 | `186` | OpenSchematics | `EXT_MILLENNIUM` | Medium | 15x R, 8x C, 7x FerriteBead, 5x 4011,... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 127 | `187` | OpenSchematics | `EXT_Z31_DIGIDASH` | Medium | 6x 0.1uF_0603_25V_20%, 5x 10k_0402, 2... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 128 | `188` | OpenSchematics | `EXT_SCOMS_TACNPR` | Medium | 20x C, 10x R, 4x MountingHole_Pad, 2x... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 129 | `189` | OpenSchematics | `EXT_DMX2MOTION` | Easy | 8x R, 6x Conn_01x02, 4x C, 4x Mountin... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 130 | `190` | OpenSchematics | `EXT_LIGHTRELAY4CAMPING` | Hard | 9x 0603R, 4x HOLE_3.2mm, 3x 0603C, 2x... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 131 | `191` | OpenSchematics | `EXT_SELF_BALANCING_ROBOT` | Hard | 6x RC0402FR-071KL, 6x CL05B104KO5NNNC... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 132 | `192` | OpenSchematics | `EXT_PUTM_EV_FRONTBOX` | Easy | 10x R, 5x CL10B104KC8NNNC, 5x C, 4x C... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 133 | `193` | OpenSchematics | `EXT_LINUXCNC_TESTBED` | Easy | 22x C, 13x R, 13x Hidden, 9x WS2812B-... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 134 | `194` | OpenSchematics | `EXT_ACTIVESPICE` | Hard | 4x HEADER_MALE_2X1, 4x 0603C, 2x SS-3... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 135 | `195` | OpenSchematics | `EXT_LEVITAS_PCB` | Hard | 13x C, 9x R_Small, 2x COM-09032, 2x A... | Other domain-specific blockers | RF/wireless or non-standard coplanar waveguide routing requiring controlled RF trace geometry. |
| 136 | `196` | OpenSchematics | `EXT_SEEDSIGNER_LUCKFOX` | Easy | 22x R_US, 20x C_Polarized_US, 4x Opam... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 137 | `197` | OpenSchematics | `EXT_LINEARKEYBOARD` | Easy | 16x R, 10x C, 7x INVERTER, 5x AND, 5x... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 138 | `198` | OpenSchematics | `EXT_JTCORES` | Hard | 3x Conn_01x01, 2x Conn_01x08, 2x C121... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 139 | `199` | OpenSchematics | `EXT_RIO_ICEBREAKER3X` | Easy | 13x R_US-Device-Zooids-rescue-Zooids-... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 140 | `200` | OpenSchematics | `EXT_ESP32_BASED_PCB` | Medium | 5x R, 4x C_Polarized, 2x Conn_01x04_M... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 141 | `201` | OpenSchematics | `HEAT_CTRL` | Easy | 1x R, 2x LM358, 1x 1N4148, 1x Conn_01... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 142 | `202` | OpenSchematics | `STM32_DECOUPLE` | Easy | 1x STM32L431RCTx, 9x C, 1x FerriteBead | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 143 | `203` | OpenSchematics | `SDEX2M2` | Easy | 1x 10130616-067R2LF, 1x Micro_SDex_PC... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 144 | `204` | OpenSchematics | `RTD_MONITOR` | Easy | 1x MAX31865xAP, 1x Conn_01x02_Mountin... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 145 | `205` | OpenSchematics | `CHEARRING_LED` | Easy | 1x APG0603SEC-E-TT | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 146 | `206` | OpenSchematics | `STM32_BLUET_PILL` | Easy | 1x Conn_ARM_SWD_TagConnect_TC2030-NL,... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 147 | `207` | OpenSchematics | `TSAR_FLIGHT` | Easy | 3x R, 1x LED_Dual_KAKA, 2x TestPoint,... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 148 | `209` | OpenSchematics | `DUAL_RTC` | Easy | 2x DS3232M | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 149 | `210` | OpenSchematics | `SPRINKLER_CTRL` | Easy | 1x Screw_Terminal_01x02, 1x Screw_Ter... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 150 | `211` | OpenSchematics | `TRIPLE_LDO` | Easy | 3x HX9193, 15x C, 1x Conn_01x08 | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 151 | `212` | OpenSchematics | `DEBUG_PROBE_LVL` | Easy | 1x C_Polarized, 3x SN74LVC2T45DCUR, 2... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 152 | `213` | OpenSchematics | `CAN_CRUSHER` | Easy | 1x Conn_01x02, 4x MountingHole, 1x LC... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 153 | `214` | OpenSchematics | `BUCK_5V` | Easy | 6x C, 1x TestPoint, 3x R, 1x TPSM8290x | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 154 | `215` | OpenSchematics | `AMIGA_HDDLW` | Easy | 3x Conn_01x04, 2x Conn_02x17_Odd_Even... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 155 | `216` | OpenSchematics | `BMS_SWITCH` | Easy | 3x R, 1x D_TVS, 2x IRLB8721PBF, 1x LT... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 156 | `217` | OpenSchematics | `CHRIS_KEYBOARD` | Easy | 1x Conn_02x15_Counter_Clockwise, 1x E... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 157 | `219` | OpenSchematics | `MOTOR_DRIVER_BD` | Easy | 4x C, 4x MountingHole_Pad, 1x TB6612F... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 158 | `220` | OpenSchematics | `TREMOLO_555` | Easy | 1x NE555D, 1x C, 2x R, 1x R_Potentiom... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 159 | `221` | OpenSchematics | `IR2110_GATE_DRV` | Easy | 1x IR2110, 4x R_US, 3x C_Small, 3x D,... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 160 | `222` | OpenSchematics | `WWW_LED` | Easy | 1x Conn_01x04, 1x SK6812 | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 161 | `223` | OpenSchematics | `SIPM_ARRAY` | Easy | 8x D_SiPM, 8x R | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 162 | `224` | OpenSchematics | `KICAD_DEMO` | Easy | 4x MountingHole, 1x Logo_Open_Hardwar... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 163 | `225` | OpenSchematics | `USB_AUDIO_CTRL` | Easy | 2x C_Polarized, 1x AM90, 5x C, 2x R, ... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 164 | `226` | OpenSchematics | `EURORACK_IF` | Easy | 1x Conn_02x08_Odd_Even, 1x Conn_02x04... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 165 | `227` | OpenSchematics | `INTERTIDAL_CONN` | Easy | 1x CONN_1x40-lsts-conn | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 166 | `228` | OpenSchematics | `SPICE_TEST` | Easy | 1x VSOURCE, 2x R, 1x D, 1x C | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 167 | `229` | OpenSchematics | `OPENMOWER_SHIELD` | Easy | 1x Conn_01x10_Female, 2x Conn_01x08_F... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 168 | `230` | OpenSchematics | `DEIMOS_POWER` | Easy | 3x C_Small, 4x Screw_Terminal_01x12, ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 169 | `231` | OpenSchematics | `OPTOGENETIC_SENSE` | Easy | 5x R, 4x C, 5x MCP6004 | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 170 | `232` | OpenSchematics | `GAMMA_DETECTOR` | Easy | 1x Crystal_Small, 7x R, 6x C, 2x SW_P... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 171 | `233` | OpenSchematics | `GDC_UNICOMP3` | Easy | 2x C_Small, 2x MountingHole, 1x Conn_... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 172 | `234` | OpenSchematics | `COMET_VMEBUS` | Easy | 5x Jumper_2_Open, 3x DIN41612_03x32_A... | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 173 | `235` | OpenSchematics | `HACKPAD_MACRO` | Easy | 6x D, 6x SW_Push_45deg, 1x MCU_Seeed_... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 174 | `236` | OpenSchematics | `DC_JACK` | Easy | 1x Jack-DC | Other domain-specific blockers | Passive switch matrix, connector breakout, or passive network without central discrete IC. |
| 175 | `237` | OpenSchematics | `EASYCONTROLS` | Easy | 1x ARDUINO_NANO, 1x Conn_02x20_Odd_Ev... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 176 | `238` | OpenSchematics | `COLORLIGHT_I5_MB` | Easy | 1x XC6206PxxxMR, 1x FerriteBead, 1x C... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 177 | `239` | OpenSchematics | `ENERGY_HARVEST` | Easy | 1x LTC3108, 1x ATtiny85V-10P | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 178 | `240` | OpenSchematics | `FLIGHTSTICK_IN` | Easy | 2x SW_Push, 1x Conn_01x08, 1x SKRHABE... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 179 | `241` | OpenSchematics | `SOLDIERCRAB_FPGA` | Easy | 2x ECP5-BGA256 | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 180 | `242` | OpenSchematics | `VIDEO_BREAKOUT` | Easy | 4x BNC, 1x DB9 | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 181 | `243` | OpenSchematics | `PDM_PROTECT` | Medium | 2x R_Small_US, 1x D_Zener_Small, 1x C... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 182 | `244` | OpenSchematics | `CPLD_BUS_ADAPTER` | Medium | 3x Jumper_2_Bridged, 2x Conn_01x01, 8... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 183 | `245` | OpenSchematics | `CHARGE_PUMP_PM18` | Medium | 1x LT1026, 1x UUQ1H470MCL1GS, 4x EEE-... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 184 | `247` | OpenSchematics | `CRT_DRIVER` | Medium | 10x C, 2x L, 2x D_Schottky, 8x R_US, ... | Other domain-specific blockers | RF/wireless or non-standard coplanar waveguide routing requiring controlled RF trace geometry. |
| 185 | `248` | OpenSchematics | `SLIMSPLAYDY_KB` | Medium | 1x Conn_01x02_Socket, 17x PG1316S, 1x... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 186 | `249` | OpenSchematics | `IGNIS_LED_LAMP` | Medium | 7x C, 1x C_Polarized, 7x R, 1x D_Scho... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 187 | `250` | OpenSchematics | `ODROID_MITX` | Medium | 2x C, 5x CD74HCT125M96, 2x TS-1187A-B... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 188 | `251` | OpenSchematics | `HACKPAD_7KEY` | Medium | 7x SW_Push, 4x LED, 4x MountingHole, ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 189 | `252` | OpenSchematics | `STM32G0_DEVBOARD` | Medium | 6x C, 3x Conn_01x04_Pin, 5x R, 1x Cry... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 190 | `253` | OpenSchematics | `EDGEBLINK_CAM` | Medium | 1x Battery_Cell, 1x mp1584, 1x SW_SPD... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 191 | `254` | OpenSchematics | `CAN_RAILROAD` | Medium | 1x TCAN332DR, 1x RJ45, 1x C, 1x CP1, ... | Multi-unit symbols | Multi-unit KiCad symbol (logic gates or dual/quad op-amps); requires symbol unit suffix parsing (U1A/U1B) and pad land aggregation. |
| 192 | `255` | OpenSchematics | `BIKE_LIGHTS` | Medium | 11x R, 1x KBP206, 1x Arduino_Nano_Eve... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 193 | `256` | OpenSchematics | `PD_POWERBANK` | Medium | 10x R, 1x INDUCTOR, 5x LED, 19x C, 1x... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 194 | `257` | OpenSchematics | `STM32F407_BOARD` | Medium | 22x C, 1x Conn_02x02_Odd_Even, 1x SPX... | >2 layer boards / high-speed / BGA | High-density MCU/FPGA (RP2040, STM32, iCE40, ECP5) or USB 2.0 High-Speed; requires >=4 copper layers for routing escape. |
| 195 | `258` | OpenSchematics | `EURORACK_SYNTH` | Medium | 3x C_Polarized_US, 1x Eurorack_2x8_Po... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 196 | `259` | OpenSchematics | `APOLLO_GUIDANCE` | Medium | 2x MPU-6000, 1x Battery, 1x Arduino_N... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 197 | `260` | OpenSchematics | `STEPPER_DRIVER` | Medium | 6x Jumper_2_Open, 2x POLOLU_DRV8825, ... | Complex modules / DevKit carrier boards | Monolithic devkit/module carrier dependency (e.g. ESP32, Xiao, Pico, Arduino, A4988); requires carrier socket abstraction. |
| 198 | `261` | OpenSchematics | `AUTO_HIGHSIDE_SW` | Medium | 6x R_Small_US, 4x C_Small, 1x D_Zener... | Custom thermal pads / exposed geometry | Power packages with exposed heatsink tabs (TO-220, D2PAK, PG-TSDSO-14); requires thermal via matrix and exposed pad geometry. |
| 199 | `262` | OpenSchematics | `CUBESAT_PMIC` | Medium | 6x R_Small, 5x C_Small, 1x NDS8434, 1... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 200 | `263` | OpenSchematics | `IR_THERMOMETER` | Medium | 1x PIC16LF19156-I_SP-PIC16LF19156-I_S... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 201 | `264` | OpenSchematics | `GMSL_SERIALIZER` | Medium | 3x PESD4USB5U-TTS, 2x R_4k7_0402, 1x ... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |
| 202 | `265` | OpenSchematics | `BIOROBOT_CHARGER` | Medium | 4x TPH11006NL,LQ, 6x C, 15x R, 13x Te... | Other domain-specific blockers | Multi-IC subsystem or peripheral interface requiring hierarchical placement partitioning. |

---

## 4. Prioritized 4-Phase Compiler Enhancement Roadmap

To methodically expand `agent-ee` compiler coverage and systematically unlock the 202 excluded Tier-2 benchmark tasks, the following **4-phase prioritized enhancement roadmap** is established:

### 4.1 Phase 1: Multi-Unit Symbol Resolution & Exposed Pad Geometry Handling
- **Target Blocker Categories**: Category 3 (Multi-unit symbols, 19 tasks) and Category 5 (Custom thermal pads / exposed geometry, 12 tasks).
- **Direct Backlog Unlocked**: **31 tasks** (15.3% of Tier-2 backlog).
- **Cumulative Coverage**: 56 tasks (24.7% of total benchmark pool including Tier-1).
- **Core Technical Deliverables**:
  1. **Multi-Unit Symbol Syntax**: Update `circuit()` and `design_shape.py` to parse symbol unit designators (e.g. `U1A`, `U1B`, `U1.VCC`) and aggregate multi-unit pin assignments into a unified physical footprint land pattern during netlist emission.
  2. **Thermal Via Matrix Synthesis**: Implement an automated thermal via array generator in `placement()` that deposits a parameterized matrix of through-hole thermal vias (0.3 mm drill, 0.6 mm pad) directly into exposed thermal ground pads (PowerPAD, D2PAK, TOLL).
  3. **Kelvin Source Support**: Add Kelvin-source net synthesis in `circuit()` to preserve isolated gate return loops for TO-247-4 and SiC devices.
- **Verification Fixtures**: LM358 dual op-amp, 74HC14 hex inverter, and DRV8825 PowerPAD test suites.

### 4.2 Phase 2: 4-Layer Board Stackups & Internal Plane Support
- **Target Blocker Category**: Category 4 (>2 layer boards / high-speed / BGA, 16 tasks).
- **Direct Backlog Unlocked**: **16 tasks** (7.9% of Tier-2 backlog).
- **Cumulative Backlog Unlocked**: **47 tasks** (23.3% of Tier-2 backlog; 72 tasks total across benchmark pool).
- **Core Technical Deliverables**:
  1. **Standard 4-Layer Stackup**: Incorporate `JLC04161H-7628` standardized 4-layer stackup (`F.Cu` Signal, `In1.Cu` GND plane, `In2.Cu` Power plane, `B.Cu` Signal) into `design_compile.py`.
  2. **Internal Plane Fill**: Enable automatic plane flood generation for inner copper layers (`In1.Cu` ground return, `In2.Cu` VCC power distribution).
  3. **High-Speed & Impedance Rules**: Implement differential pair 90-ohm (USB) and 100-ohm (PCIe) trace width/spacing geometry calculations in `kicad-route`.
  4. **Dense QFN/BGA Escape**: Enhance the maze autorouter with dogbone via escape routing for QFN-56 and fine-pitch BGA packages.
- **Verification Fixtures**: RP2040 microcontroller board (`Task 101`) and W9825G6KH SDRAM interface (`Task 59`).

### 4.3 Phase 3: Modular Carrier Symbols & DevKit Integration
- **Target Blocker Category**: Category 1 (Complex modules / DevKit carrier boards, 45 tasks).
- **Direct Backlog Unlocked**: **45 tasks** (22.3% of Tier-2 backlog).
- **Cumulative Backlog Unlocked**: **92 tasks** (45.5% of Tier-2 backlog; 117 tasks total across benchmark pool).
- **Core Technical Deliverables**:
  1. **`ModuleAdapter` Architectural Primitive**: Add a modular carrier abstraction in `design_compile.py` that ingests popular devkit pinout contracts (ESP32-DevKitC, Seeed XIAO, Raspberry Pi Pico, Arduino Nano) and synthesizes standard dual-row mating pin socket headers (`PinHeader_1x...` or `SocketStrip_1x...`).
  2. **Courtyard Keepout Boundaries**: Enforce accurate component body keepout polygons representing the daughterboard physical envelope to prevent collision during component placement.
  3. **Pass-Through Bus Routing**: Optimize router heuristics for multi-signal parallel escape from dense carrier header pins.
- **Verification Fixtures**: ESP32-DevKitC window monitor (`Task 102`) and Raspberry Pi Pico omni-robot controller (`Task 112`).

### 4.4 Phase 4: High-Voltage Isolation Rules & Creepage/Clearance Constraints
- **Target Blocker Category**: Category 2 (High voltage / isolation barriers, 21 tasks).
- **Direct Backlog Unlocked**: **21 tasks** (10.4% of Tier-2 backlog).
- **Cumulative Backlog Unlocked**: **113 tasks** (55.9% of Tier-2 backlog; 138 tasks total across benchmark pool).
- **Core Technical Deliverables**:
  1. **Multi-Zone Copper Floods**: Support multiple distinct polygon copper plane fills on `B.Cu` with independent return net designations (`EARTH`, `PGND`, `GND_ISO`, `AGND`).
  2. **IEC 60664-1 Clearance/Creepage Classes**: Add voltage-dependent net class clearance constraints in native KiCad DRC enforcing >=2.5 mm to 4.0 mm spacing across isolation boundaries.
  3. **Isolation Slot Substrate Milling**: Synthesize physical Edge.Cuts substrate isolation milling slots beneath optical isolators, Hall sensors, and flyback transformers.
- **Verification Fixtures**: AMC1350 400V isolated voltage sense (`Task 2`) and UCC5390E isolated gate driver (`Task 15`).

