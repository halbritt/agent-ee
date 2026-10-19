# Actual pod candidate electrical inputs

These are engineering selections under the owner's delegated authority. They
are not new owner component preferences, a qualified circuit, or a fabrication
release. The canonical specification remains unchanged. The immutable core
bundle and its two-source CAN capture remain under `core/`.

## Circuit decisions

- Adopt the independently reviewed power proposal v1 and passive supplement v2
  as candidate topology and identities. Keep their captured source documents,
  calculations, exact ordering-code qualifications, and stock timestamps.
- Use separate tracking regulators for the 200 mA sensor reservation and CAN
  supply. The transceiver source port `+5V` binds to `+5V_CAN`; it is not the
  sensor rail. The ADC reference follows the sensor rail at the connector.
- The selected 6.3 V buck plus precision reference/tracking-LDO topology is an
  explicit adaptation of the specification's direct sensor-supply buck wording.
  Its budget remains a calculation with unqualified dynamic and wiring reserves.
- Passive v2 implements 53 kohm and 23 kohm feedback resistances as explicit
  51k+2k and 22k+1k series networks. No other hidden series/parallel substitutions.
- Preserve source CAN C100, D100, R100 and U100 identities, 120 ohm termination,
  and source S=GND strap. Native bus names are POD_CAN_P/POD_CAN_N; physical
  signal identities remain CAN_H/CAN_L. Source-bound adaptation reporting must
  describe canonical delivered part fields instead of treating source blanks
  as verified purchasing identities.
- POD_SENSOR_RETURN is a connector-local sense location on continuous GND.
  Mapping the four return names to GND is an explicit naming adaptation. Net
  equality does not prove Kelvin pickup, copper resistance or analog accuracy.
- `support-decisions.json` adds exact captured local decoupling, MCU reset RC,
  boot pullup and default deselected SPI chip-select pullups. These reuse real
  captured passive identities; the source locators and engineering choices
  remain distinguishable. Reset timing and physical placement need validation.
- The pod's own 2 A inline fuse is a harness obligation upstream of J1.3,
  consistent with the source I/O map. No unsupported board fuse is invented.
- Replace the historical direct GPIO46 ground strap with R33, a captured 10k
  pull-down on MCU_BOOT46. GPIO46 remains its true I/O/T type. This preserves
  the low download strap without a hard short if firmware later drives it high.
  ESP32 module Tables4-1/4-3 govern boot use; timing still requires validation.
- `mode_cad.py` derives a separate primary-SPI LSM6DSO32 model from the retained
  generic source, changing only SDx/SCx pins2/3 to input. DocID032891 Rev1
  Table2 p24 permits ground in Mode1; Table19 p55 describes default inputs.
  Sensor-hub master mode must remain disabled. The generic source stays intact.
- U9 uses an explicit OPA320 reference-supply role variant: pin1 power_out,
  all physical pins unchanged, generic ordinary-output variant retained. The
  circuit keeps direct feedback and470pF plus ADC/trace load. Classification
  is not stability, settling, noise or drive-capacity qualification; native
  conflicting-output probes and independent review govern this model change.

## Original CAD

`power_cad.py` supplies original rectangular symbols and numerical land patterns
from captured manufacturer tables. DDA0008B and DDA0008J-C02 have distinct mask
openings. EP maps explicitly to native pad9; eight holes share that pad number.
Q1's drain tab shares pad2. No prose `EP` or `tab` identifier becomes an intent
pin. Four stencil windows totaling about65% of each EP mask opening are an
explicit original process proposal, not manufacturer or fabricator approval.
Via filling/tenting, stencil, solderability and thermal performance remain open.
RF SOD882BD uses the separate copper/mask/paste dimensions in Figure11.
The later orientation delta adds only F.Fab pin1 dots to both DDA footprints
and Q1; copper, mask, paste, pin numbering and dimensions are unchanged.

Stock KiCad CAD and manufacturer PDFs stay external and retain their upstream
origins. Numeric metadata and original recipe code grant no new redistribution
rights to those sources or the fome-derived CAN/AMPSEAL content.

## Unresolved acceptance conditions

- Selected core MCU/IMU/GNSS/ADC identities do not establish the specified105°C
  capability. No verified all-constraints substitution set has been adopted.
- TE776228-1 is vertical and approximately34.25mm tall when ordinarily mounted;
  this conflicts with the right-angle/12mm wording. Exact part identity does not
  waive orientation or enclosure requirements.
- Manufacturer notice now establishes the exact22uF TDK legacy/catalog ordering
  correspondence; the1uF/100nF suffixes remain open. Samsung application/reflow guidance,
  effective capacitance at bias/temperature/aging and the selected R26 hot-fault
  derating remain qualification tasks. Preserve C100's Samsung caveat too.
- ISO7637, hot TVS clamp, reverse-input behavior, thermal rise, power stability,
  ratiometry, acquisition/settling, EMC/RF/antenna/enclosure performance and bench
  acceptance remain open. Stock captures establish historical observations only.
- Source review149/204, actual requirements/check composition, owner preview,
  assembly and fabrication gates remain independent of these input artifacts.

## Connector sources

J1 TE776228-1/C498958 and J2 Molex732511150/C841205 both have positive captured
stock. Neither uses an invented NO_LCSC or unavailable-part hand-fit exception.
J2 manufacturer orientation is Right Angle / Edge Mount, per the captured
product sheet and additive orientation erratum. The installed1153 footprint
remains a shared-family proposal for1150; complete manufacturer land-pattern
comparison is open. Its two plated pad2 holes are electrical ground stitching,
recorded through existing thermal_via_pads naming without a thermal claim.
All11 electrical copper pads remain. Assembly capability,1.60mm edge fit,
retention and enclosure sealing remain unqualified.


## J1 nominal candidate placement exclusion

The root-adopted source-bound candidate model uses the TE776228 C14 sheet1
nominal flange reference dimensions60.8×32.1mm, centered at contact12 as registered
by sheet2 contact/peg pattern. `j1-placement-exclusion.json` binds exact sources,
reviewed geometry/registration and the explicit decision. Add only F.CrtYd at
x±30.4/y±16.05mm, **zero added margin**; preserve original CAD,27 pads, FPID and
TE776228-1/C498958 identity. This is a front-only candidate placement exclusion,
not a tolerance-qualified manufacturer courtyard or hardware acceptance.

Tolerance maximums, assembly/tool/mating-plug/wire/panel/seal clearance, backside
occupancy and fastener access remain unknown. Placement must remain front-only
until a separate backside policy is adopted. The selected part is vertical,
not the requested right-angle connector. Installed height abovePCB is unknown;
19.25+15=34.25mm is an axial-reference sum with no qualified seating/engagement
datum, so12mm compliance remains unresolved. Sheet2 recommends1.57mm PCB.
At the retained pose, the nominal rectangle overhangs the bottom by2.55mm and
has0.50mm separation from U1. The geometry does not authorize overhang, choose
a packing gap, or erase antenna/SMA/physical/bench holds. These limitations must
remain captured and visible even if native polygon checks pass.
