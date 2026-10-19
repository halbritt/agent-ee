# Block-first schematic integration (AGENTEE-46)

`agent_ee.block_first` checks each authored IC block before publishing a proposed
hierarchical design. The caller chooses the architecture and parts, prepares the
sheets with the existing [block adapter](block-adaptation.md) or schematic writer,
and supplies ordinary scope/2 or scope/3 files. This gate runs offline and uses
the existing capture, native check scheduler, Bubblewrap executor and coordinator
evidence seal. It introduces no check kind or alternate check registry.

## Check each block

Each block scope selects a root harness with exactly one child sheet.
All physical components must belong to that child, as established by the sealed
native inventory. The harness may supply virtual power flags for external rails.
The child is the block capture unit. KiCad requires this harness: hierarchical
labels on a root sheet report a nonexistent-parent ERC error. The harness's
sheet pins must cover every child hierarchical label exactly once, with matching
directions. Native ERC then checks their electrical connections. Nested block
hierarchies and repeated occurrences of one child file are refused.

Configure `erc`, `netlist` and `netlist_parity` with `predicate: topology` and
`multiset: exactly`. The parity source must be an independently authored captured
intent or golden netlist, as required by the existing
[parity contract](netlist-parity.md). Add `pin_contracts`, `hardware_interface`,
parts checks and simulations for the block's declared obligations. Existing
[pin contracts](pin-contracts.md) and [interface contracts](hardware-interface.md)
carry electrical assertions; names such as I2C, SPI and USB do not establish
protocol or voltage correctness by themselves. All configured checks run, and
any defect, unknown result or incomplete execution prevents admission.

```sh
PYTHONPATH=src python3 -m agent_ee.block_first verify \
  --scope generation/power.scope.json --out runs/power-1
PYTHONPATH=src python3 -m agent_ee.block_first verify \
  --scope generation/sensor.scope.json --out runs/sensor-1
```

Each output is a fresh ordinary candidate directory with `candidate.json`,
`baseline.json`, `inputs/`, `checks/` and `work/verification/evidence.json`.
`block-verification.json` adds the subject sheet path and SHA-256, hierarchical
ports, candidate and baseline identities, captured dependency hashes and native
check results. Status is `verified`, `defect` or `missing_coverage`.

A `missing_coverage` report is written before capture or native execution. A
crash cannot leave a previous success because an existing output directory is
refused. Interruption, deadline exhaustion, unavailable tools and missing native
outputs retain unknown coverage. Independent block callers can run concurrently
in separate directories; native workers see only their own read-only capture and
writable scratch, with network disabled. This command does not launch model
workers or grant additional design writers.

## Gate integration

The sole design writer authors the proposed top sheet and child sheets. Its
scope must select every block dependency, including each block harness, parts,
contracts, expected topology, libraries and source requirements, under the same
candidate-relative paths used by the block scopes. It also declares fresh
integrated ERC and exact topology parity checks plus the board's other checks.
Baseline and scope/3 executable-coverage rules still apply.

```sh
PYTHONPATH=src python3 -m agent_ee.block_first integrate \
  --scope generation/board.scope.json --out runs/integrated-1 \
  --block project/power.kicad_sch=runs/power-1 \
  --block project/sensor.kicad_sch=runs/sensor-1
```

`--block` must cover every direct child of the top sheet exactly once. Missing,
extra, repeated or nested subjects refuse integration. The gate takes read
leases on the block runs, revalidates their snapshots and evidence seals, and
recomputes their reports. A copied `verified` status cannot supply coverage.
Each child and every dependency must match the checked capture. Parent pin
names and directions must exactly match the child's captured interface.

A changed sheet, interface contract, parts manifest, library or expected topology
invalidates that block's evidence. `block-integration.json` identifies the block
as missing coverage, lists changed paths with `re-check required`, and queues
the sheet in the machine-readable `recheck_required` list for its caller. Run
`verify` into a new directory and supply that run on the next integration
attempt. Editing an accepted report or its artifacts is refused.

Only after every block passes does the gate run the top scope's checks. It then
revalidates the captures and seals and publishes their exact bytes in
`OUT/design/`. Failed attempts retain inputs and diagnostics without that
published design directory. `block-integration.json` retains each block's
candidate, baseline, sheet and evidence-seal identities, and the fresh integrated
results. Both commands exit 0 only for `verified`; a refused or incomplete
attempt exits 1. `--timeout-s` bounds the check budget, defaulting to 900 seconds.

This is schematic admission evidence. It does not establish independent review,
central adjudication, a campaign's `criteria_satisfied` outcome, fabrication
readiness or hardware acceptance. Submit the published proposal to the ordinary
review/campaign path for those gates. The original candidate and immutable
baseline stay intact.

## Validation

`tests.test_block_first` uses two original synthetic IC blocks, authored library
symbols and independent topology expectations. Native KiCad checks exercise
block hashes, complete integration, ERC and parity defects, worker failure,
interface invalidation and re-check, evidence tampering, missing coverage and
fresh integrated checking. No third-party design or library is embedded in this
fixture. Mechanism references from the ticket are atopile
`.claude/skills/planning/SKILL.md` Phase 3 steps 7–10 and SchGen's named module
library concept. No source-project code, text or assets were copied.
