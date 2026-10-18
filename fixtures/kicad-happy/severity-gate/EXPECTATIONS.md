# Severity policy fixtures

`generate.py` authors the symbols, pads, tracks, outline and zone from scratch.
No external design or library geometry is copied. `OwnedGroundLink2` is the
identifier of this original two-pad fixture, not a manufacturer MPN. No
manufacturer datasheet or device qualification is claimed.

Both projects have one connector J1, two ground pads, a 0.5 mm front trace and
a 20 mm square outline. `warning` also has a back ground zone; `error` omits it.
Neither is a fabrication design. Generate with system Python and pcbnew:

```sh
/usr/bin/python3 fixtures/kicad-happy/severity-gate/generate.py
```

Independent policy expectations for pinned kicad-happy 2.3.1:

- `warning`: no error findings. Schematic DS-002 warns about absent datasheets;
  EMC VS-001 warns about via stitching. The two exact native finding IDs have
  original author dispositions in `dispositions.json`. With those dispositions
  the gate passes; an empty file or either missing ID produces a defect.
- `error`: omitting the ground zone emits EMC GP-002 (no ground plane), an error.
  Even dispositions for every warning and an entry naming GP-002 cannot waive
  that error. The gate reports a defect.

All four analyzers run: schematic, full PCB, cross-analysis and EMC. An empty
cross-analysis findings array here says nothing about complete rule coverage;
its retained `checks_run` manifest documents checks skipped for lack of applicable
input. Thermal, SPICE, lifecycle/network lookups and fabrication are outside this
fixture contract. Invalid analyzer output stays unknown.
