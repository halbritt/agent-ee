# Research request and constraints

Research and develop a plan for GPU placement and routing, run bounded experiments,
and present the findings to agent-508 for implementation. Use peecee-wsl's GPU;
the owner authorizes unloading Ollama. Use Sol subagents for independent work and
limit token/experiment expenditure.

This follows the source-verified PCB routability plan and the owner's additional
unified architecture and next-generation concepts briefs. The destination is the
owned backend, dispensing with upstream router dependencies. agent-ee and
kicad-route may eventually merge. Include the semantic architect in that design.

Preserve electrical/mechanical constraints, native KiCad final authority and
determinism. Separate kernel parity from native board correctness. Do not implement
production algorithm changes during this research. Preserve scripts, inputs,
raw results and identities; report negative results and limitations.
