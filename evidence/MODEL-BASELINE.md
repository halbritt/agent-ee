# Tool-enabled donor baseline follow-up

The available Universal 112 donor remains a modular carrier, not the completed
integrated FOME-MC ECU. Reviews preserved both real source repositories:
`Polygonus-Universal-112` at `ddcf0f77fecf3ea7f093954ce1becb7c4a26e970` and
`kicad-libraries` at `ab9c5f8a1517a04b9cada6880d67d190bfd48c05`.
[Source-state record](model-baselines/source-state.json) records the initial state.
The case-alias scope adds a byte-identical uppercase VR sheet filename only inside
the disposable capture. It does not repair the original checkout.

The current [release report](model-release/baseline/REPORT.md),
[result](model-release/baseline/result.json),
[separate root-agent decisions](model-release/baseline/operator-decisions.json)
and [original automatic adjudicator execution](model-release/baseline/adjudicator-execution.json)
are the primary result. Outcome is `defects_or_unresolved`, with assisted
adjudication after the automatic response failed validation. This is not ECU
acceptance or a manufacturing release.

## Established findings

**Native checks fail their declared criteria.** ERC reports 353 violations,
including five errors. DRC reports 142 violations, 114 schematic-parity issues
and zero unconnected items. All thirteen error-level DRC violations are excluded
in the saved project: exclusions do not erase the physical geometry or make the
review's criterion pass. Native rule/modeling failures alone do not establish
missing physical supplies or actuator malfunction. Full reports, commands and
independent adjudicator reruns are retained under the release baseline.

**Five J1 mounting holes have zero annular copper.** The actual saved board
contains five unnumbered plated through-hole pads: three 4.0 mm pad/drill pairs
and two 2.2 mm pairs. Equal pad and drill diameters give zero annulus against the
saved 0.1 mm rule. A direct drill export places them in the PTH file. This supports
a saved-rule and manufacturing-intent discrepancy, not a claim that a particular
fabricator will reject them or that connector fit/plating will fail.

**Three saved GND thermal reliefs violate the spoke minimum.** C15.2, U7.4 and
C14.2 each resolve one spoke against the configured minimum of two. The saved
copper and fresh DRC establish the discrepancy. Ground opens, exact impedance,
joint reliability and overheating are not established.

**Eight lowside switches have conflicting component identities.** U11–U14 and
U16–U19 specify value `VNL5090` while their LCSC field is `C2680415`. The SKU
owner identifies that code as `VNL5050N3TR-E`; both the native schematic and saved
PCB carry the conflict. No alternate-part decision was found. This is a concrete
component-definition ambiguity within the review's component-identity scope.
It does not by itself establish an unsafe substitution. See the
[SKU owner's page](https://www.lcsc.com/product-detail/C2680415.html) and retained
`vnl-reference-web.txt` / adjudicator `source-details.json` in the release run.
The earlier network-enabled run also retains the supplier's original HTML and
structured product record, not only a model-written summary.

## Material unresolved questions

**Lowside GPIO drive and reset state.** The native circuit routes module controls
through 1 kohm series branches to SOT-223 N3 switch inputs, with 1 kohm pulldowns.
The ST N3 input is also its control supply; the specified operating minimum is
3.5 V. An ideal 3.3 V GPIO is already 0.2 V below that minimum before series or
GPIO voltage drop. This supports a conditional interface incompatibility. The
retrieved upstream Polygonus module has direct MCU outputs on these pins, but
its source is reference evidence rather than proof of the exact mated assembly.
Actual module revision, output levels, fitted switch identity and behavior must
be established before asserting actual output failure.

Sources: [ST VNL5090](https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf),
Tables 2, 6 and 8; [ST VNL5050](https://www.st.com/resource/en/datasheet/vnl5050n3-e.pdf),
Tables 2, 6 and 8. Do not apply the SO-8 S5 logic-input specification to the N3
input/supply. The network-enabled run retains a complete supplier-hosted ST
VNL5050 PDF, its text extraction, and retrieved ST VNL5090 passages.

For LS9/LS10 specifically, native R2/R1 pull up to +5 V while RN1 pulls down;
equal 1 kohm resistors produce **2.5 V unloaded bias** with **1.5 kohm Thevenin
resistance at the driver**, including the series resistor. The recorded ngspice
operating-point calculation confirms only this ideal passive network. It contains
no validated VNL input/switch model. Actual conduction and actuator activity during
reset are unresolved; a 2.5 V node is not proof of a startup pulse.

The earlier network run classified drive compatibility as a defect under the
3.3 V condition; the root agent deferred its application to the actual assembly.
The release review independently classified it as a major concern. Both records
survive. They agree on the conditional voltage bound, not on proof of actual
hardware malfunction.

**Internal CAN2 termination.** Both carrier resistors R36/R37 are DNP in schematic
and board. Their carrier contribution is zero; fitting both would give 60 ohms,
but that is not the saved assembly. Upstream module R10 shows one possible 120 ohm
termination. That does not identify the actual module/harness termination or prove
bus waveforms, timing or error-free operation. CAN2 remains deferred pending the
real assembly and operating conditions. No automatic majority or speculative
upstream compatibility closes it.

**Heater returns and ETB paths.** The expanded Claude review measured long 0.6 mm
heater-return traces and 0.8 mm ETB supply necks, with actual saved connectivity.
Its resistance/IPC-2221 estimates assume currents, copper thickness and thermal
conditions not established for the real assembly. Preserve them as investigations
requiring load, copper, duty-cycle and temperature evidence. No current-carrying
qualification or automatic redesign follows from those widths.

## Coverage and omissions

Reviewers inspected saved native connectivity, geometry, DNP populations, selected
pin mappings and datasheet limits, reset bias, rule results and verification gaps.
The release investigators independently reproduced native reports and drill
classification. Retrieved external references and experiments remain linked to
those runs; they do not replace captured native design files.

Missing or incomplete inputs include the exact mated module revision and assembly,
harness/loading and operating waveforms, integration requirements for the single
PCB FOME-MC design, and complete resolvable project libraries. Existing cached
symbols/footprints allow useful native work but do not establish that a clean
library refresh reproduces the saved design. The review does not cover full
ignition energy, wideband control stability, brownout/load-dump transients, EMC,
thermal qualification, fabrication acceptance or physical bench validation.

Claude and Agy baseline timeouts are preserved as incomplete reviews, not clean
results. Three automatic donor adjudications required separate root-agent
reconciliation. See [selection results](MODEL-SELECTION.md) for exact series and
implementation versions. No source circuit repairs, fabrication orders or new ECU
architecture were authorized or performed.
