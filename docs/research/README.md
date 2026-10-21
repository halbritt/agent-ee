# Research results for agent-ee and kicad-route

Prior-art and literature briefs that inform the placer, the block arrangement and the
router. One file or folder per brief, named `YYYY-MM-DD-<topic>`; keep the prompt beside
its results, and any cited PDFs in a `sources/` subfolder next to the brief. Briefs are
input for design decisions and Plane items; they are not specifications.

| Prompt | Results | Status |
| --- | --- | --- |
| `2026-10-21-routability-brief-prompt.md`: routability-driven placement, channel reservation, negotiated-congestion routing, escape routing, benchmarks | [Markdown](<2026-10-21-routability-brief/PCB Placement and Routing Research.md>), [PDF](<2026-10-21-routability-brief/PCB Placement and Routing Research - Google Docs.pdf>) | received 2026-10-21; recommendations pending source verification |

Motivation and measurements: fome-mc `telemetry-pod/expansion/routing-diagnostic/README.md`
(the pod's routing plateau at 203–205 of 235 nets on two-level placements) and the Plane
items AGENTEE-375/376.
