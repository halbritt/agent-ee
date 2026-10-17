# Native and engineering checks

For version-2 requirements, measurement/coupling interfaces, campaigns and bench
feedback, see the [closed-loop contract and command reference](closed-loop.md).

All handlers execute against the captured candidate, not the source checkout.
Their working directories are separate. The original tool commands, sandbox
arguments, exit status and console output remain in `work/CHECK_ID/`.
[checks.py](../src/agent_ee/checks.py) interprets native output and applies the
criteria; [geometry_probe.py](../src/agent_ee/geometry_probe.py) extracts saved
board facts through pcbnew.

## Execution and engineering result

Every result separates `execution` from `outcome`. A tool can run successfully
and report a design violation: `execution=ok`, `outcome=defect`. A crash, missing
output, unsupported component or absent measurement means unknown coverage,
not a confirmed electrical defect and not a passing check.

The native commands below show sandbox paths; `/candidate` is the captured
`inputs/`, `/work` is this check's scratch. They document the emitted arguments,
not commands to paste into a host shell with nonexistent sandbox paths.

| Kind | Native/helper arguments | Individual ceiling |
| --- | --- | --- |
| `erc` | `kicad-cli sch erc --format json --severity-all --units mm -o /work/erc.json SCHEMATIC` | 900 s |
| `drc` | `kicad-cli pcb drc --format json --severity-all --schematic-parity --units mm -o /work/drc.json BOARD` | 900 s |
| `netlist` | `kicad-cli sch export netlist --format kicadxml -o /work/design.xml SCHEMATIC` | 600 s |
| `hardware_interface` | Coordinator compares a captured owner contract with preceding full native XML; no additional tool launched | Enclosing run deadline |
| `fab_consistency` | Coordinator reads the earlier export's sealed staging tree and version run, the captured board, the sealed netlist and earlier sealed geometry; no tool launched, no export regenerated ([details](fab-consistency.md)) | Enclosing run deadline |
