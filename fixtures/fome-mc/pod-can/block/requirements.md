# CAN composition

Owner decision: composed from two sheets (2026-10-18), recorded in docs/decisions.md (second and third batches) and fixtures/telemetry-pod/spec.md revision 2, Component decisions / CAN.

Select U307 TJA1051T/3 and its 100 nF VCC decoupler C308 from wbo1, plus D4 PESD1CAN-UX and R9 120 ohm (C22787) from psu. Preserve VCC pin 3 on +5V, VIO pin 5 on the 3.3 V source rail +3V3_WBO1, D4 pin 3 and U307 pins 2/8 on GND. CAN+ is U307:7/D4:2/R9:1; CAN- is U307:6/D4:1/R9:2. U307:1 is TXD and U307:4 is RXD. S (pin 8) remains grounded in the pinned source.

Logical ports requested for a standalone block:

| Port | Direction | Voltage domain | Source terminal |
| --- | --- | --- | --- |
| CAN+ | bidirectional | CAN bus | U307:7, D4:2, R9:1 |
| CAN- | bidirectional | CAN bus | U307:6, D4:1, R9:2 |
| TXD | input | 3.3 V logic | U307:1 |
| RXD | output | 3.3 V logic | U307:4 |
| S | input | 3.3 V logic; source tied to GND | U307:8 |
| +5V | input | 5 V | U307:3, C308:1 |
| +3V3 | input | 3.3 V; source name +3V3_WBO1 | U307:5 |
| GND | passive | ground | U307:2, D4:3, C308:2 |

No current limits have been declared. block.json retains the actual primary sheet's hierarchical boundary labels; these logical ports are not yet native standalone ports. Both full source sheets are captured unchanged from Git objects. The native audit connects their CAN terminals in an original parent but retains unselected components, including PSU MCP2551 transceivers. It proves the listed connections, values and supply separation only. No bus simulation or standalone composition is claimed.

Upstream origin and licence are recorded in block.json and block-contract.json, with the common notice at fixtures/fome-mc/LICENSE-NOTICE.md (origin T16). The source instances remain unverified; no AGENTEE-149 review run is imported. Draft parts, undeclared boundary limits, standalone extraction and multi-instance provenance remain missing coverage. A passing hardware_interface audit is not a verified block.
