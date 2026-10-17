# Circuit review command and outcome notes

Scope: explicit saved copper track segment width >= 0.30 mm. No ampacity assertion or unstated electrical operating conditions applied. Candidate files were read only. No prior reviewer experiments used and no references fetched.

Initial inspection commands (all exit 0 except as stated):

```text
pwd
rg --files -g AGENTS.md -g '!home/**' /work /candidate /evidence
ls -la /candidate/project /evidence/work /evidence/checks /work/experiments
cat /candidate/requirements.txt /candidate/project/rc_filter.kicad_pro
cat /evidence/checks/erc.json /evidence/checks/drc.json /evidence/checks/geometry.json /evidence/checks/netlist.json
cat /evidence/work/erc/erc.json /evidence/work/drc/drc.json /evidence/work/geometry/console.log /evidence/work/netlist/connectivity.json
nl -ba /candidate/project/rc_filter.kicad_pcb | sed -n '825,940p'
kicad-cli version
```

`rg` returned 1: no matching AGENTS.md found. The first numbered PCB read requested lines beyond EOF and returned no text; subsequent programmatic read covered the entire file and located segments at lines 667, 675, 683, 691 and 699. KiCad CLI version is 10.0.7.

Reproducible audit:

```text
/usr/bin/python3 -B /work/experiments/review.py > /work/experiments/review.stdout.log 2> /work/experiments/review.stderr.log
cat /work/experiments/measurements.json /work/experiments/review.stderr.log
```

Python 3.12.3 and pcbnew 10.0.7. The script writes only experiment outputs. The script reads the entire native board, hashes both native board and schematic (matching packet hashes), parses each saved segment width as a decimal and compares it against decimal 0.30 mm. Native UUIDs and widths agree with both pcbnew and supplied geometry output. There are five segments and no saved track arcs. All five are 0.25 mm, a 0.05 mm deficit. The via diameter and zone minimum thickness are not track segment widths.

Exported native XML component values and net memberships agree with board footprints/pads and supplied connectivity JSON: J1.1 -> R1.1 (/IN), R1.2/C1.1/J1.2 (/OUT), C1.2/J1.3 (GND). Native schematic excerpts show generic passive resistor, unpolarized capacitor and connector symbols, R1=10k and C1=100n. No specified part model or electrical operating conditions support further limit or interface findings.

Supplied ERC and DRC JSON contain no violations and DRC contains no unconnected items or schematic parity issues; ignored checks remain excluded as listed in those original reports. All four check execution console hashes were independently verified. Geometry is informational and explicitly says no declared geometric requirement was applied. Therefore successful tool execution and clean configured DRC do not contradict failure of the separate exercise width criterion.

The original geometry console and this independent pcbnew load emit PROPERTY_ENUM assertions. Widths are supported independently by the original native text and exact decimal comparison. The first script run also emitted a via GetWidth-without-layer assertion; the script was adjusted to supply F.Cu for the via diameter read and rerun. This does not change the five segment measurements. Final stdout, stderr and measurements are preserved alongside the script.

Coverage omissions: no independent ERC/DRC rerun; no re-enabling ignored checks; no zone refill; no SPICE models, stimulus or measurements in the supplied checks to validate; no circuit simulation or hardware test; no ampacity, component ratings/tolerances, source/load compatibility, startup/shutdown, reset or brownout assessment because relevant operating conditions and requirements were not supplied. No ECU qualification evaluated.
