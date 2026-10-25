# Acquisition delivery closure — 2026-10-24

Immediate milestone: the existing 17-part acquisition design, through the normal
supported workflow to an accepted fabrication/assembly package. This table is
an execution checklist. The original specification explicitly calls its choices
agent-selected, not owner rulings. The owner requires a functional manufacturable
qualification design and reproducible integrated workflow; the component,
electrical safety and fabrication constraints below remain mandatory.

Original source: `build/requirement-clauses/acquisition-inputs/spec.md`, SHA-256
`d10c9e0be819e80c42497f3637688d331c9a1942c7a806e38e33248a388175dc`, captured in `spec-record.json` and the sealed
`requirement-clauses-20261024` evidence bundle. Byte offsets below are zero-based,
end-exclusive and identify the exact original statement. Original records are
preserved. No row is closed merely by writing this plan.

| Requirement and exact statement/source | Authority | Failure protected against | Closure disposition | Smallest adequate closure action/evidence |
| --- | --- | --- | --- | --- |
| `ACQ-ELEC-1` — “J1 shall accept 4.75 to 5.25 V DC from an external 20 mA current-limited supply, pin 1 positive and pin 2 ground. Nominal operation is at 25 C; operation from 0 to 50 C requires explicit applicability verification. Hot plugging, surge immunity, and external signal drive while unpowered are not assumed safe.” (`spec.md` bytes 1074–1382) | Agent-selected operating contract; manufacturer voltage/temperature limits apply. | Overvoltage, wrong polarity, source current collapse, unsupported environmental claims. | Closed: source review/operating contract plus final `engineering-intervals` and topology pass. | Document supply polarity/current-limit and valid operating envelope; compare captured absolute/recommended limits. Limit this milestone to stated nominal-temperature function, retain broader-temperature validation as explicit follow-up unless component safety requires it. |
| `ACQ-ELEC-2` — “U1 shall provide 3.3 V within 2 percent at U2 and J2 pin 4 with external J2 load no greater than 1 mA, both I2C lines held low, and all internal loads included. Capacitor bias, temperature, tolerance, startup, dropout, and dissipation shall be evaluated from captured sources.” (`spec.md` bytes 1385–1661) | Agent-selected 3.3 V ±2% function/load; manufacturer LDO stability/ratings; derived current and thermal margins. | Wrong rail at ADC/host, oscillation, startup failure or overheating. | Closed: margin review, eight corner cases and final `engineering-intervals`, `engineering-geometry`, `power-startup`/`power-removal` pass. Model limits remain explicit. | Use guaranteed regulator accuracy/minimum-load/dropout bounds, actual BOM and copper, conservative stated load/thermal allocation and captured capacitor bias data. Reuse qualified startup simulation with justified effective-capacitance cases. Treat engineering estimates honestly; change a part only if margin is inadequate. |
| `ACQ-ELEC-3` — “The D1/R5/R6 network shall limit an unpowered regulator input under reversed J1 voltage to its absolute-maximum bounds for the declared operating conditions. Output-to-input reverse voltage during power removal shall be evaluated with effective capacitance bounds. No signal or host back-power is allowed by the operating contract.” (`spec.md` bytes 1664–1995) | Agent-selected reverse-polarity function; manufacturer input/reverse-voltage limits; derived discharge condition. | Negative input or output-to-input stress damages U1. | Closed: leakage calculation, removal corners and final topology/model recheck pass. | Calculate reverse-leakage/R5 voltage against absolute limit; inspect D1 polarity and R6 load in native design; exercise existing power-removal model over justified capacitance/bleed bounds. Repair network if stress exceeds limit. |
| `ACQ-ELEC-4` — “U2 shall measure two single-ended signals at J3 pins 1 and 3 relative to adjacent ground pins 2 and 4, from 0 to 2 V and DC to 10 Hz. Each source impedance shall be at most 100 ohm. R1/C4 and R2/C5 are RF input filters; they are not an antialias guarantee. Native connectivity, input loading, settling and applicable ADC error shall be evaluated.” (`spec.md` bytes 1998–2344) | Agent-selected measurement range/bandwidth/source impedance; manufacturer ADC input and error behavior. | Input overdrive, loading error, unsettled conversion or misleading accuracy claim. | Closed: source-based loading/settling/error review and final native connectivity pass; no total-accuracy guarantee invented. | Calculate filter/load effects from captured ADC/passive sources for actual configuration; state an explicit achievable error estimate and its limits, without inventing a precision guarantee or antialias claim. Confirm native channel/ground topology. |
| `ACQ-ELEC-5` — “The host shall use 3.3 V open-drain I2C at no more than 400 kHz and at most 100 pF total line capacitance, address 0x48. R3/R4 shall meet rise-time and low-level sink constraints. The host configures single-shot conversion at 128 samples/s and the plus/minus 2.048 V PGA range, selecting AIN0 or AIN1 versus ground; default register values are not this application configuration.” (`spec.md` bytes 2347–2726) | Agent-selected host interface; manufacturer I2C/configuration constraints. | Bus timing/sink failure, wrong address, gain, mode or channel. | Closed: operating instructions/config words reviewed; final arithmetic, topology and independent ADC ties pass. | Retain existing rise/sink arithmetic, complete manufacturer timing/voltage conditions, specify exact configuration words and conversion-ready/read sequence. Verify address/config bit derivation against captured register tables; physical bus test remains follow-up. |
| `ACQ-ELEC-6-REMAINING` — “and host/analog outputs shall remain high impedance until the board supply is valid. No load or external supply may drive J2 pin 4.” (`spec.md` bytes 2808–2940) | Necessary derived anti-back-power condition in agent-selected external-host contract. | Unpowered ADC/rail is driven through signal or supply pins. | Closed for file release: external operating contract and final native interface pass. Physical host compliance is not claimed. | Deliver pinout and mandatory power-up/down sequencing/no-drive conditions; review native interfaces and absence of claimed isolation. Do not require fabricated evidence that an unspecified external host has executed those instructions. |
| `ACQ-MECH-1` — “The board shall be 45 by 35 mm, two copper layers, nominal 1.6 mm FR4, with a rectangular closed outline. It is a bench board with no mounting holes or enclosure claim. The selected manufacturing profile must confirm the final stackup and minimum dimensions.” (`spec.md` bytes 2971–3229) | Agent-selected board geometry; manufacturer connector thickness range and fabricator stack/tolerances are binding. | Unmanufacturable board or connector/stack mismatch. | Closed by explicit correction to 1.2 mm and suitable resistor selection; final native stackup/profile/outline checks pass. Original 1.6 mm declaration is superseded, not passed. | Compare actual board/profile with sourced stack capability and connector thickness interval; correct nominal thickness if the selected tolerance exceeds a component limit. Re-run only affected native/profile/export checks. |
| `ACQ-MECH-2` — “Every footprint shall have exactly one functional block or explicit fixed/shared role. J1 anchors power on the left, J2 anchors host on the right, and J3 anchors analog on the top. Shared ground and 3.3 V shall not merge the blocks. These connectors are fixed on the front with vertical mating access.” (`spec.md` bytes 3232–3533) | Agent-selected functional blocks/anchors implementing owner general grouping requirement; manufacturer mating direction. | Inaccessible connector or blocks lost during placement/routing. | Closed: final model reports all 17 memberships, sides and access; three fixed anchors and eight hard locality distances pass. Annotated final views retained. | Inspect final saved coordinates against existing groups/placement report, retain annotated views and membership/side/distances. Dimension vertical connector access with actual bodies; no new grouping engine needed. |
| `ACQ-MECH-3-REMAINING` — “These are agent-selected placement limits, not datasheet-proven guarantees; final routing shall preserve short corresponding loops and return paths.” (`spec.md` bytes 3703–3852) | Agent-selected 3 mm placement limits; manufacturer decoupling/layout advice; derived current-return quality. | Long decoupling/filter current loops or broken ground return. | Closed: final saved-board copper model and native checks pass; removed-plane negative rejects. Bounded low-current layout judgment is recorded. | Inspect and measure actual routed pad connections and filled B.Cu return around U1/U2/filters with native views and conductor geometry. Record bounded board-specific judgment; no generalized plane-resistance solver needed unless this inspection identifies a real unresolved margin. |
| `ACQ-MECH-4` — “Bodies and courtyards shall remain within the board and shall not overlap on the same side. Installed height including mated JST PH connectors shall be at most 8 mm, with clear vertical connector access. Exact component geometry and assembly process applicability require captured-source checks.” (`spec.md` bytes 3855–4150) | Agent-selected 8 mm height/access; manufacturer body/mating geometry and assembly constraints. | Body interference, unusable mating access or unsupported assembly process. | Closed by dimensioned review and explicit mated-height scope correction; final bodies, access, model height and courtyards pass. Supplier process confirmation remains follow-up. | Compare source package/body and PH mating dimensions to final board. Explicitly correct any unnecessary absolute 8 mm ceiling (no enclosure is claimed), retain original record, and provide practical clearance margin. Separate TH manual/selective solder from SMT assembly as declared. |
| `ACQ-DELIV-2` — “A clean documented setup shall reproduce the workflow through agent-ee build run; interrupted work shall resume from verified stage outputs. The retained inputs, commands, versions and accepted artifacts must suffice for another operator.” (`spec.md` bytes 4462–4700) | Owner overall reproducibility requirement, instantiated by agent-selected build specification. | Another operator cannot build, resume or identify accepted files. | Closed: fresh captured-input normal build accepted, CANCEL interruption and verified resume succeeded, accepted resume revalidated; exact package/input/runtime evidence retained. | Deliver captured inputs, installed-environment/version command and final run; interrupt/resume one affected real build checkpoint, use existing valid regressions for unchanged stages, and verify exact package/native identity. |

Release decisions will be recorded below with evidence, corrected input hashes
and affected checks. A correction to an agent-selected assumption supersedes its
original declaration; it does not claim that the original requirement passed.

Supplier preview, purchasing/ordering, physical inspection and powered bench
validation have not occurred. They are delivery follow-up obligations, not
software-file acceptance evidence. Any observed design defect still requires
correction before release. Broader KRT parity and the two-sided fine-pitch plane
failure remain unresolved subsequent work.

## Executed engineering work

The saved public build-review change is integrated at `466e6feb`. Its real
wrong-address negative fails the ADC tie rule while ERC and topology parity pass;
independent verification succeeds. See [its qualification report](public-build-review-2026-10-24.md).

Current work is in `build/acquisition-release/`: `inputs/ENGINEERING-REVIEW.md`
and `OPERATING.md` give the per-obligation conclusions and external contract.
The original inputs remain captured under `history/initial-qualification`.
Thickness is1.2mm with1.08–1.32mm declared fabrication tolerance. Six YAGEO
RC0603 equivalents replace UNI-ROYAL parts whose manufacturer calls for
consultation below1.6mm board thickness. Values, tolerance and footprint size
are preserved; fresh stock capture covers every selected ID.

The input-current estimate is7.177410mA against20mA, including an explicitly
estimated1mA regulator ground-current allowance. Eight real native power-model
cases pass and verify. I2C rise is188.27ns against300ns. The retained routed
board's native geometry model measures7.194mV DC drop against16.5mV, no body
overlap and0.20mm minimum package gap before the declared placement allowance.
These are engineering models with stated applicability, not physical tests.

The [captured geometry model source](../../fixtures/acquisition-qualification/README.md)
uses the existing measurements workflow and native KiCad polygons. Positive
geometry passes; removing the GND plane produces a ground-strip defect, and
moving R5 onto U1 produces body/placement defects. All three evidence records
verify. No generalized plane solver or new check framework was added.

Final generation is accepted. `release-build-v7` completed all seven normal
stages using production code at `466e6feb859e2cd9f34693155e24ccfe3b0aca8f`.
Its 38 checks are 37 passes and the expected informational native netlist result;
13 interval relations pass individually. There are zero unconnected items and
zero findings under the unchanged native DRC severities. The recorded ignored
KiCad categories remain visible in the result; no severity was changed for this
release. All package checks execute with no unknown or skipped requirement.

The retained earlier attempts corrected input-binding defects before routing:
shared check IDs forbidden by source-specific coverage contracts, a discontinuous
source quote, a missing generation package gate, and an old embedded placement
source record after the specification correction. Their refusals remain evidence.
The final build was deliberately interrupted after the schematic stage using
`CANCEL`, then resumed to acceptance. A second accepted resume revalidated the
receipts without repeating routing. Changing the copied CPL's C1 X coordinate
from 110.15 to 111.15 mm makes ordinary `verify` reject stale evidence.

The accepted PCB SHA-256 is
`d0a898a2a254c090ba1a4185320938241c255e79c587c82abb27a0bcafcd66e7`;
the schematic is `fc552de4568240bb7e4093817c060c11bc00d832459e5c5ea004e5575f5cf294`.
Package artifacts ID:
`df866200d8a83d0fc8bd72b7c51ac80672c390ba54e1a48e3ec3765a12877e9a`.
Its nine plots, two drill files, 12 BOM rows and 17 placements match the native
project. No assembly references are excluded. Native render logs confirm all
three views completed; 17/17 models resolve and source bytes remain unchanged.
Top, bottom and isometric images were inspected. KiCad rendered 1568×1176 pixels
for the requested 1600×1200; that image-size difference is recorded.

Delivery: `build/acquisition-release/delivery/` contains the exact accepted
native tree, manufacturing exports, engineering review, operating contract,
annotated placement views, checks, receipts, captured reproduction inputs and
README commands. `acquisition-accepted.zip` is a convenient copy of that tree;
its `SHA256SUMS` verifies every file. Full attempts and supplementary evidence
are retained in the acquisition release seal beside the previous sealed runs.

The calculations and engineering judgments establish file-release acceptance
under the stated nominal-temperature operating contract. Supplier preview,
placement/process confirmation, and physical inspection/powered testing remain
unperformed. This milestone establishes one integrated engineering delivery;
it does not establish broad KRT parity or resolve the rejected two-sided plane
case. Those remain the next programme work.
