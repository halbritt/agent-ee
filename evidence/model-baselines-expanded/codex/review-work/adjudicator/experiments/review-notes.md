# Adjudication evidence log

Candidate: 69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4
Run: c91cb6bf-3f83-487a-885c-c8f087d4b1af

## Commands and provenance

All generated artifacts are confined to /work/experiments. No candidate changes,
zone refill, repair experiment, or credential-file reads were performed.
Initial discovery used pwd, ls, and rg --files on /candidate/project, /evidence,
/experiments and /work, including an AGENTS.md search (none found).
Read the full /evidence/work ERC, DRC and netlist JSON/XML, native project JSON,
native schematic/board files, project library tables, and the supplied check
library tables. Selected raw excerpts were read with sed (PCB 21665–21735 and
wbo-can-trans schematic 2660–2780). The local output schema was inspected.

`python3 /work/experiments/adjudicate.py > /work/experiments/audit-console.log 2>&1`
is the reproducible audit entry point; the script and commands.json record exact
check/export arguments, duration, exit codes and log names. Python is 3.12.3;
kicad-cli is 10.0.7. Stock library environment variables point to /candidate/stock;
only the supplied global symbol/footprint tables were copied into the disposable
configuration. Native project tables remain authoritative. Read-only parsing uses
a small S-expression tokenizer and Python's XML/JSON parsers.

`kicad-cli pcb export drill --help` was saved in drill-help.txt. The executed drill
export is recorded in drill-command.json, with drill-export.log and drill-report.txt.
The preliminary extraction of full native error records and project rules is in
report-audit.json; audit.json records the independent native/check comparison and
calculated annuli/conductance. No external reference or simulation was necessary
to decide the declared ERC/DRC violations; neither would establish the actual
attached module or harness configuration.

## Outcomes and interpretation

- Fresh ERC reproduces every supplied sheet/violation record exactly: 10 instances,
  353 violations, five errors and 348 warnings. Native embedded symbol pin types
  agree with the five root-sheet errors. These establish a failure of the declared
  ERC criterion. They do not establish physically absent supplies, independent
  regulators in collision, or a requirement to use the fault output.
- Fresh DRC reproduces all 142 violation records, 114 parity records and zero
  unconnected items exactly, with copper as saved. There are 13 error records,
  all explicitly excluded with blank comments: three one-spoke thermals and ten
  padstack/annular errors. Record exclusions are preserved; a clean electrical
  connectivity count does not satisfy the separate rule/parity criterion.
- J1 contains five unnumbered thru_hole circular pads with size equal to drill
  (three 4 mm and two 2.2 mm). (Copper diameter - drill diameter)/2 is 0 mm in all
  five cases, versus the saved project's 0.10 mm constraint. The independent drill
  export classifies these as plated. This supports the specific manufacturing-data
  defect without asserting connector misfit or fabrication impossibility.
- The VR filename alias is byte-identical to the lowercase source. Fresh export
  has 139 components, 199 nets and 10 sheets; every exported net matches the supplied
  connectivity. The saved board hash matches the supplied check hash.
- Both R36 and R37 are 120 ohm direct branches across CAN2 and DNP in schematic,
  netlist and board. The populated direct carrier resistor conductance is 0 S.
  CAN2 reaches U20/U21, J2.73/74 and J1.15/11. The module symbols/connector pins
  do not describe attached internal termination. The actual full-bus termination,
  topology, bitrate and resulting communication margin remain undetermined.
  Preserve the major CAN2 uncertainty as deferred, with the second CAN2 finding
  linked as a duplicate. Resolve using the intended module/harness circuitry and
  assembled-bus resistance/topology or appropriately specified waveform tests.
- The specific five-error ERC finding duplicates the accepted native ERC failure.
  The specific connector annulus finding remains accepted with its own native
  dimensional evidence, also included in the accepted broader DRC check failure.

## Limits

This adjudication does not approve manufacturing or integrated FOME-MC coverage.
No module/harness hardware, powered measurements, waveforms, validated transient
simulation, thermal/EMC qualification or full functional qualification was supplied
or inferred. No hypothetical requirement for carrier-local CAN termination was
introduced. Excluded thermal records do not alone prove electrical opens. The
original as-received filename issue is outside this explicitly aliased candidate.
