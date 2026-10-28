# Research results for agent-ee and kicad-route

Prior-art and literature briefs that inform the placer, the block arrangement and the
router. One file or folder per brief, named `YYYY-MM-DD-<topic>`; keep the prompt beside
its results, and any cited PDFs in a `sources/` subfolder next to the brief. Briefs are
input for design decisions and Plane items; they are not specifications.

| Prompt | Results | Status |
| --- | --- | --- |
| [Zero ASIC research request](2026-10-28-zeroasic-prompt.md) | [Source and code findings](2026-10-28-zeroasic-autonomy.md), [local work items](2026-10-28-zeroasic-work-items.md) | researched 2026-10-28; mapped to existing recovery work; Plane unavailable |
| `2026-10-21-routability-brief-prompt.md`: routability-driven placement, channel reservation, negotiated-congestion routing, escape routing, benchmarks | [Research reports and PDF exports](2026-10-21-routability-brief/README.md) | received 2026-10-21; [source-verified design and implementation plan](2026-10-21-routability-design.md), including two later owner briefs |
| [GPU placement/routing request](2026-10-21-gpu-pcb/PROMPT.md) | [GPU experiments and implementation gates](2026-10-21-gpu-pcb/README.md) | measured on peecee-wsl RTX 3090 Ti; exact score, pattern and dense-field CPU/GPU comparisons; implementation report for agent-508 |

Motivation and measurements: fome-mc `telemetry-pod/expansion/routing-diagnostic/README.md`
(historical reports of 203–205 of 235 nets; the integrated plan qualifies board identity, native connection counts, policy and preservation failures) and the Plane
items AGENTEE-375/376.
