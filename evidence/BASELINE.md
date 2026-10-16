# Universal 112 donor baseline for FOME-MC

The real checkout and shared libraries were preserved. This is a carrier-board
review of the existing modular Universal 112 dual-wideband design basis. It does
not establish the integrated FOME-MC ECU's functional or physical qualification.

The [as-received run](donor-final/REPORT.md) records the original filename issue
and incomplete hierarchy coverage. The [disposable alias run](donor-verified/REPORT.md)
adds only `project/VR Conditioner.kicad_sch`, copied byte-for-byte from the
existing lowercase file. It changes no native circuit or board content and
does not fix the original checkout. Its exact input mapping is
[declared in the scope](../scopes/donor-case-alias.json).

The candidate identities, file hashes, source locations and repository revisions
are in each `candidate.json`; the alias run's current outcome and explicit
dispositions are in [result.json](donor-verified/result.json). The captured shared
library commit matches the donor's `ab9c5f8` submodule pin, although the original
submodule was uninitialized and the snapshot explicitly supplied its files.

## Native results and engineering interpretation

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| Original root refers to `VR Conditioner.kicad_sch`; checkout has `VR conditioner.kicad_sch`. | A real, case-sensitive project-integrity defect prevents complete original ERC/netlist coverage. | That the board's VR hardware is electrically absent. |
| Alias run: 10 sheet instances, 139 exported components, 199 nets, 10 DNP components. | The explicitly changed disposable candidate restores complete native hierarchy traversal/export. | That the original source has been corrected or that all functions work. |
| Alias ERC: 353 violations, including 5 error-level entries. | Native rule criteria fail on the captured schematic. | Five independently established physical circuit failures. |
| DRC: 142 violations, 0 unconnected items, 114 parity issues. | Native saved-board rules/parity report these items; source/check artifacts identify their locations. | Full function, current/thermal margins, or manufacturing approval. |
| All 13 error-level DRC entries are excluded in the saved project: 5 padstack, 5 annular-width, 3 starved-thermal. | The native all-severity audit retains existing exclusions instead of silently discarding them. | That excluded mechanical/thermal conditions are necessarily fabrication or operational failures. Their engineering rationale is not supplied. |
| The 15 original extra-footprint parity entries become field mismatches after the alias; the parity total remains 114. | Identical aggregate counts can describe different checks/results. The VR section becomes visible to parity. | Interchangeability of old and new acceptance evidence. |

The five ERC errors identify `#PWR01` GND, `A1` pin 8 VMOT, and `J2` pin 90
12V_IGN as power inputs not driven by a power-output pin; `A1` pin 2 FLT as
unconnected; and the two `J2` 5V_SENSOR_1 pins 40/41 as connected power outputs.
These are the tool's symbol/connectivity checks. No supply-voltage measurement,
stepper fault-monitoring requirement, or module circuit analysis was performed
to establish physical failure from those messages.
See the complete [native ERC report](donor-verified/work/erc/erc.json) and
[native DRC report](donor-verified/work/drc/drc.json).

## CAN2 population and saved-board implementation

Both nominal 120 Ω carrier terminations, R36 and R37, are DNP in the saved
schematic and board. Their pads connect to `/CAN2+` and `/CAN2-`:

| Footprint | Saved pad locations, mm | Population |
| --- | --- | --- |
| R36 | pin 1 (175.0144, 142.1286); pin 2 (175.0144, 139.2036) | DNP |
| R37 | pin 1 (80.1552, 140.5500); pin 2 (83.0803, 140.5500) | DNP |

The nominal calculation 120 Ω || 120 Ω = 60 Ω applies only if both are fitted.
It does not describe the specified carrier population. Carrier R10 is a populated
100 kΩ resistor in `/VR Module/`, between `Net-(U4-IO1)` and `Net-(U6B--)`;
it is not a third carrier CAN2 termination. A similarly named part on a different
brain module is a different design location. No triple-120 Ω assembled-load
defect is established by this snapshot.

The assembled bus's actual termination remains unresolved: the attached brain,
modules, harness and population must be identified. The internal carrier alone
does not establish their fitted load. Review the native
[schematic](<donor-verified/inputs/project/wbo-can-trans.kicad_sch>),
[derived connectivity with native provenance](donor-verified/work/netlist/connectivity.json),
and [saved-board geometry](donor-verified/work/geometry/geometry.json).

Saved-board inspection also confirms five unnumbered, no-net J1 pads with
pad size equal to drill size (4.0 mm or 2.2 mm), explaining the zero-annulus
geometry behind the excluded padstack/annular warnings. That observation does
not substitute for a connector mechanical drawing or fabrication decision.
The snapshot contains 2,207 track segments, 1,224 vias, 719 pads and 18 zones;
the corrected layer-aware via query emits no deprecated-width assertion.

## Coverage and follow-up

The generated report retains complementary initial reviews, duplicates,
established native/project defects, non-defect notes and open questions. Its
dispositions are evidence judgments; no majority vote establishes correctness.
The full artifacts and their source relationship remain available for inspection.
The completed alias review accepts the native ERC/DRC criteria failures and
defers the major CAN2 population/termination question. It retains minor/info
notes about VR input polarity without a harness polarity requirement, protection
at the CAN1 carrier boundary without module internals, footprint/library
differences, off-grid endpoints and the alias dependency. These notes do not
establish additional physical defects.

Full integrated ECU coverage requires the actual single-board FOME-MC candidate,
module internals, assembly population and applicable requirements. Dedicated
checks/models and bench work remain for reset/brownout output safety, ignition
energy, stepper and injector loads, wideband loop behavior, automotive transients,
EMC, potting/thermal margins and mechanical/manufacturing constraints. None of
those absent checks is credited as passing. No real-project repair, fabrication
or component purchase was performed or approved.
