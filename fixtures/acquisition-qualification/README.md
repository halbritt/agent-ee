# Acquisition engineering geometry model

This is the source for the captured `analysis/` inputs used by the acquisition
qualification build. The normal `measurements` producer/parser workflow runs it
against the generated native `project/acquisition.kicad_pcb`; it does not install
a new check kind or replace native DRC. Its current supported case is this
orthogonal, front-populated bench design with fixed connector anchors.

`geometry_model.py ROOT OUTPUT.json` uses native KiCad polygons and coordinates
to report package envelopes, clearance, connector access, functional membership,
continuous B.Cu conductor strips, local routed paths and a conservative DC drop
estimate. `geometry_adapter.py` converts those actual outputs into the existing
candidate-bound metric format. Acceptance limits belong to the captured scope,
not the parser. Unknown inventory, sides, rotations, routing primitives and
missing local paths fail. The geometry is never repaired by these scripts.

The model JSON records engineer-selected package dimensions, plane witnesses,
placement allowance and material/contact estimates. It is an explicit analysis
model, not proof of supplier tolerance, high-frequency impedance or physical
performance. Source applicability and limits are discussed in the captured
`ENGINEERING-REVIEW.md` and [closure report](../../docs/qualification/acquisition-closure-2026-10-24.md).
Native polygon operations require the documented KiCad execution environment;
there is no Shapely dependency.

The retained positive native exercise passes every declared metric. Removing
the saved GND zone produces a `ground_strip_outside` defect through the same
normal measurement check; both evidence sets pass `agent-ee verify`. Final
qualification additionally runs the model through `agent-ee build run`, after
routing and artwork, against the emitted board. The raw native board digest and
model digest are present in every producer report.

All code and declarations here were written for agent-ee. Manufacturer PDFs,
stock responses, KiCad libraries and vendor SPICE models stay in the captured
qualification bundle with their original provenance; they are not copied here.
