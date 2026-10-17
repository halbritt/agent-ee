# Physical review experiment notes

Scope: external contract in `inputs/requirements.txt`: J1.2 input,
J1.1 filtered output, J1.3 ground. No unstated electrical ratings applied.

All candidate access was read-only. No save, native edit, scratch repair or zone
refill was performed. `verify_physical.py` records the repeatable experiment;
`commands.json` records commands, environment overrides and exit codes.
Versions are in `kicad-version.txt` and `physical-connectivity.json`.

Initial reconnaissance commands: `pwd`; `rg --files -g AGENTS.md` over /work,
/candidate and /evidence (none found); `ls -la` of /work, /candidate/project,
/evidence/work and /evidence/checks; `command -v python3 kicad-cli ngspice`;
`/usr/bin/python3` version and pcbnew version/API inspection;
`kicad-cli version`; `ngspice --version` (42; no simulation performed).
Read requirements, native schematic, native PCB, project rule settings and
supplied ERC/DRC/netlist/geometry records. Native line numbers were inspected
with `nl -ba` and `sed`. No credential files were read.

Reconnaissance limitations: `import shapely` failed (module absent); no Shapely
calculation was used. pcbnew emitted three PROPERTY_ENUM assertion messages
on import, as in the supplied geometry log. Before explicitly building
connectivity, GetConnectedPads returned empty; even after Build it returned
empty. Therefore the experiment uses Build followed by GetConnectedItems,
which exposes the full saved copper connected item sets. These are checked
against a separate exact-integer centerline graph ignoring net names for the
front copper. These API observations are not candidate defects.

The graph handles the T-junction at (107.5,100) on the middle of track
69a2edc1-cbed-4fb8-a0fc-ac38c986e5f5. Saved ground return is C1.2 to the
(113,100) via, through B.Cu fill to plated J1.3. The original saved polygon
and native DRC are inspected alongside KiCad connectivity.

Outcome: J1.1 is on R1's series-only side. J1.2 shares copper with C1.1 and
R1.2. Thus J1 pins 1 and 2 are reversed relative to the external contract.
Both the exported schematic and saved board contain the same reversal.
ERC/DRC/parity do not assert the external pin contract. Geometry has no
contract assertion either. Their successful results therefore do not
establish correct external filter behavior. The ideal calculation in the
JSON illustrates the consequence with explicitly stated source/load
assumptions; it is not a hardware measurement or a new operating requirement.

Coverage: all 7 pads, 5 tracks, 1 via, 1 saved zone; connector pin assignment;
front centerline paths and ground return; native edge/courtyard placement
inspection and configured native DRC, ERC and schematic parity.

Omissions: no fabrication/assembly inspection, actual part or mating connector
fit validation, Gerber/drill-output audit, refill validation, impedance/parasitic
extraction, specified source/load sweep, voltage/current/ampacity/temperature
qualification, or ECU qualification. No such operating requirements or part
ratings were supplied; track width alone is not used to infer capability.
