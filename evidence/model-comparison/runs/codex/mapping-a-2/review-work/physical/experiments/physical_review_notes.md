# Physical review record

Scope: inputs/requirements.txt, connector contract J1.1 input, J1.2 filtered output, J1.3 ground. No additional ratings or qualification requirements assumed. Candidate files were only read; no board save, refill, or scratch design modification was performed. No previous reviewer experiments were read. No external references were needed.

## Commands and environment

- `command -v kicad-cli` returned `/usr/bin/kicad-cli`.
- `/usr/bin/python3 -c 'import sys; print(sys.version); import pcbnew; print(pcbnew.Version())'`: Python 3.12.3, GCC 13.3.0; pcbnew 10.0.7. Import emitted three PROPERTY_ENUM assertions also seen in work/geometry/console.log.
- Read native requirements, project settings, supplied native PCB/schematic excerpts, checks/{erc,drc,netlist,geometry}.json, work/{erc,drc}/reports, work/netlist/{design.xml,connectivity.json}, and work/geometry/geometry.json. Inspected execution command records supplied in the packet. The reports have empty violation lists, DRC also empty unconnected and schematic-parity lists; these are static rule results, not analog measurements.
- `rg -n` on native pad/net/segment/via/zone/filled_polygon/symbol/label declarations located the source anchors below.
- `/usr/bin/python3 /work/experiments/physical_review.py > /work/experiments/physical_review.stdout 2> /work/experiments/physical_review.stderr`: exit 0, produced saved_copper_graph.json. Full source is retained. It loads the unmodified saved board, checks every pair of copper-item effective shapes on common layers WITHOUT net-name filtering, and computes connected groups. It uses the saved filled polygon, not its unfilled boundary. It separately compares the exported schematic node groups to the required groups. The contact results, not exit status, support the conclusion. The via width accessor also emitted a missing-layer-argument warning; its returned 0.6 mm agrees with native lines 707 onward. This width is not used to decide contacts; GetEffectiveShape is called with explicit layers.

## Native source anchors and results

- inputs/project/rc_filter.kicad_pcb:587,596,605: J1 pads 1,2,3 at (100,100), (100,102.54), (100,105.08) mm, assigned /IN,/OUT,GND respectively. Pad 1 is square; orientation/numbering is explicit in the footprint.
- inputs/project/rc_filter.kicad_pcb:359,367: R1 pads /IN and /OUT; lines 210,218: C1 pads /OUT and GND. Saved values are R1=10k and C1=100n.
- inputs/project/rc_filter.kicad_pcb:667 onward: 4.0875 mm /IN segment reaches R1.1. Three /OUT segments join R1.2, C1.1, J1.2. In particular (107.5,100) is in the INTERIOR of the R1.2-to-C1.1 horizontal segment, so the output branch really contacts it; endpoint-only checking would miss this connection.
- inputs/project/rc_filter.kicad_pcb:699,707,715,734: C1.2 has a 2.05 mm front-copper segment to the plated via at (113,100), which contacts the saved B.Cu ground fill. That same single saved filled outline contacts J1.3. saved_copper_graph.json/contacts_ignoring_net_names lists both fill contacts. The saved polygon has 187 vertices and one outline.
- All three physical pad groups equal the contract-derived groups: {J1.1,R1.1}; {J1.2,R1.2,C1.1}; {J1.3,C1.2}. No copper contact between different assigned nets was detected. The board SHA256 equals f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de, as in the packet.
- inputs/project/rc_filter.kicad_sch:142,160,178 plus J1 instance at line 1091 onward: connector pins at local (-5.08,+2.54),(-5.08,0),(-5.08,-2.54) with instance at (60.96,63.5), angle 180. Their sheet positions are (66.04,66.04),(66.04,63.5),(66.04,60.96). The wires and labels at lines 805,835 and ground symbol at 853 onward give pin1 IN, pin2 OUT, pin3 GND. This agrees with work/netlist/design.xml/nets and connectivity.json/nets, so connector rotation has not swapped pins.
- work/drc/drc.json:33-36: no parity issues, no unconnected items, no violations. work/erc/erc.json:sheets[0].violations empty. Ignored checks are explicitly reported; not treated as covered.

## Geometry calculations from saved dimensions

These are nominal dimensional checks, not manufacturing acceptance or ampacity results.

- Board Edge.Cuts at x=96,116 and y=96,108 forms a closed 20 x 12 mm rectangle (native PCB lines 630-666).
- Courtyard rectangles: J1 x=[98.23,101.77], y=[98.23,106.85]; R1 x=[103.32,106.68], y=[99.05,100.95]; C1 x=[108.3,111.7], y=[99.02,100.98]. No rectangle overlaps. Minimum J1-to-R1 x gap=103.32-101.77=1.55 mm; R1-to-C1 x gap=108.3-106.68=1.62 mm. Minimum courtyard-to-edge gap is 108-106.85=1.15 mm, at J1 bottom. Real selected component bodies and mating access are unspecified.
- J1 nominal radial annulus=(1.7-1.0)/2=0.35 mm; ground via=(0.6-0.3)/2=0.15 mm. Drill/plating tolerances and fabricator capability are not supplied, so no production qualification follows.
- R1 copper-pad gap=1.825-1.025=0.800 mm; C1 gap=1.9-1.0=0.900 mm; adjacent J1 pad gap=2.54-1.7=0.840 mm. No voltage-withstand rating is inferred.
- All saved tracks are 0.25 mm wide. No load current, copper thickness, allowed rise, ambient, or part ratings are given; no current or thermal qualification was attempted.

## Verification challenge and omissions

The native rule checks compare the saved design and the supplied schematic; agreement could share the same wrong connector assumption. The explicit contract-to-pad-group assertion and manual symbol-rotation check address that specific shared assumption. The contact graph still shares KiCad's geometry engine with DRC. It assumes the saved plated barrels and a connected single filled outline represent manufactured copper. It does not test a physical assembly.

ERC ignored single_global_label, four_way_junction, simulation_model_issue, footprint_filter. DRC ignored missing_courtyard, track_not_centered_on_via, tuning_profile_track_geometries, footprint_filters_mismatch, footprint_type_mismatch. All three footprints do have courtyard rectangles and the single via is centered on its track endpoint; the other ignored categories were not independently qualified in full.

No zone refill/fresh-fill equivalence check; fabrication/Gerber/drill export; independent geometry kernel; hardware continuity; assembly fit/mating connector validation; thermal/ampacity analysis; parasitic/EMC analysis; analog transient/AC simulation; tolerance/load/source-impedance study; or environmental/ECU qualification. The packet contains static checks and no electrical stimulus or measured transfer response. Those omissions do not contradict the limited saved-connectivity criterion.

Conclusion: no evidence-supported defect or specific unresolved concern within the stated scope.
