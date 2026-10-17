# Physical initial review

Candidate: 851abfc5517464c4ee2208a5a5125b552af3905075d86462e03839dd5115f8d1
Run: 76dba052-e6e0-4810-bb10-f390747c4877
Packet SHA-256: 40281a011d34429f8f4cde01d6e61861d4f647761999f748a949dfeec49ad1da

## Requirement and assumptions

The packet scope and inputs/requirements.txt:1 require every saved copper track
segment to be at least 0.20 mm wide. This is a saved-geometry comparison, with no
fabrication tolerance subtraction or current-carrying inference. Via diameters,
silkscreen strokes, edge strokes and zone minimum thickness are different objects
and are not counted as track segments. Saved arcs were also enumerated: none.
Candidate files were only read. No prior reviewer experiments were consulted.

## Commands and versions

Initial inventory: `pwd`; `rg --files -g AGENTS.md -g '*.kicad_pro' -g '*.kicad_dru' -g requirements.txt /work /candidate /evidence`;
`ls -la /evidence/checks /evidence/work /candidate/project /work/experiments`.
No AGENTS.md or custom .kicad_dru was found by the inventory.

`kicad-cli version`: exit 0, version 10.0.7.
`/usr/bin/python3 -c 'import sys, pcbnew; print(sys.version); print(pcbnew.Version())'`:
exit 0, Python 3.12.3 (GCC 13.3.0), pcbnew 10.0.7. The import printed three
PROPERTY_ENUM assertion diagnostics, also present in work/geometry/console.log.
These do not establish a candidate violation; the independent width audit below
does not import pcbnew or depend on the geometry probe implementation.

Native and record inspection used `cat` on inputs/requirements.txt,
inputs/project/rc_filter.kicad_pro, checks/{geometry,drc,erc}.json,
work/{drc/drc,erc/erc,netlist/connectivity}.json, work/drc/execution.json,
work/geometry/console.log; and `sed -n '609,737p'` and
`sed -n '730,970p'` on inputs/project/rc_filter.kicad_pcb.
`rg -n` located top-level segments, via, zone, filled_polygon, net assignments,
widths and UUIDs. The supplied complete schematic excerpt and native XML netlist
were reviewed for the connector/resistor/capacitor topology.

Independent calculation command:
`/usr/bin/python3 /work/experiments/physical_audit.py > /work/experiments/physical_audit.json`
Exit 0; script and complete JSON result are retained beside this note. It parses
native S-expressions with Python's standard library and compares widths using
Decimal. It also compares all PCB pad net assignments with the exported netlist
and all track UUID/width pairs with the supplied geometry extraction.

## Outcomes and locators

- Native board SHA-256 independently recomputed as
  f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de,
  matching the packet/checks.
- inputs/project/rc_filter.kicad_pcb:667-706 contains all five saved track
  segments. Each is 0.25 mm, so each margin is 0.25 - 0.20 = 0.05 mm.
  All five UUID/width pairs agree with work/geometry/geometry.json:tracks.
  The segment UUIDs and individual calculations are in physical_audit.json:tracks.
- All seven pads agree with work/netlist/connectivity.json:nets and
  work/netlist/design.xml:export/nets: IN is J1.1--R1.1; OUT is
  R1.2--C1.1--J1.2; GND is C1.2--J1.3.
- Manual routing trace: J1.1 (100,100) reaches R1.1 (104.0875,100).
  R1.2 (105.9125,100) reaches C1.1 (109.05,100); the J1.2 branch
  reaches that horizontal track at (107.5,100). C1.2 (110.95,100)
  reaches the GND via at (113,100), then the saved B.Cu GND zone and
  through-hole J1.3 (100,105.08). The native saved polygon and thermal
  geometry were inspected at lines 715 onward; no refill was performed.
  work/drc/drc.json:unconnected_items is empty, corroborating saved connectivity.
- The 20 x 12 mm outline closes at (96,96), (116,96), (116,108), (96,108)
  (native board lines 627-666). The footprints lie inside the outline and
  have separate courtyards. All three have native courtyard geometry, so the
  ignored missing_courtyard check does not itself expose a missing courtyard.
  Via and GND track meet at the same coordinate. SMD/THT pad attributes agree
  with the associated footprint types. No tuning profile is present.
- work/drc/drc.json has zero violations, unconnected items and schematic-parity
  issues; work/erc/erc.json has zero sheet violations. These arrays, and the
  check exclusions, were inspected rather than relying solely on exit codes.
  No new native ERC/DRC run was needed to duplicate these fresh records.
- checks/geometry.json explicitly says no geometric requirement was applied.
  Its info outcome alone cannot establish width compliance; this review applied
  the requirement directly to every saved segment instead.

## Coverage omissions and limits

No zone refill, Gerber/drill export comparison, fabricated-board measurement,
assembly trial or manufacturer process qualification was performed. Saved copper
and stored native rules are the physical evidence; pad net agreement alone does
not prove connectivity, and native DRC cannot establish electrical performance.

Native ignored DRC checks: missing_courtyard, track_not_centered_on_via,
tuning_profile_track_geometries, footprint_filters_mismatch,
footprint_type_mismatch. Native ignored ERC checks: single_global_label,
four_way_junction, simulation_model_issue, footprint_filter. This review did not
rerun the native engine with those checks enabled; basic applicable geometry was
manually inspected as described above.

No current/voltage/load/temperature assumptions, ampacity calculation, thermal
model, signal-integrity simulation, component-rating qualification, RC-response
simulation or bench measurement was introduced. Those are not established by a
width check and are outside the stated exercise. No network retrieval was needed.

No evidence-supported defect or concern was identified within this scope.
