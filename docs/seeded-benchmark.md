# Synthetic seeded benchmark

AGENTEE-21 adds a scratch-only fetch, mutation, checks-only runner and report
utility. Its [expectations](../benchmarks/seeded/expectations.json) identify five
pinned public boards and 30 synthetic mutations. This ledger freezes assembly
values, conductor relationships and connector mating geometry. It does not
establish 30 functional electrical failures or native-clean controls. Owner
review and complete dependency qualification remain outstanding.

The corpus has nine value substitutions, five schematic wire swaps, nine removed
schematic wires, five physical pad-geometry substitutions and two removed RFID
protection components. Wiring variants intentionally retain original PCB copper.
The footprint variants transplant actual 1.27 mm library pad geometry in place of
2.54 mm pads, preserving pad 1, placement and electrical pad identities; original
footprint graphics remain. A renamed library property alone would be rejected.
The fuse variant bridges its former pads and merges copper nets. Both protection
variants must preserve remaining schematic and saved-copper connectivity groups.

## Sources and publication

The manifest records upstream repository, exact commit, archive and native-file
SHA-256, license SHA-256/Git blob identity, attribution and source URLs. SparkFun
hardware uses CC BY-SA 4.0; its software MIT grant does not license its boards.
The other selected boards declare CERN-OHL-P-2.0 or CERN-OHL-S-2.0. These are
upstream hardware grants, not a change to agent-ee's code license.

Downloads, original license files, libraries, generated candidates and review
artifacts stay outside this repository. Publication of third-party designs or
modified derivatives still requires owner approval. The repository contains
metadata and small independent numerical expectations only. The
[dependency inventory](../benchmarks/seeded/dependency-qualification.json) records
embedded symbols, model references, and library/license file hashes. Several
custom libraries and models are absent; complete dependency and redistribution
closure is unqualified. The utility never installs replacement parts or overrides
a license to make a run work.

## Preparation and temporal ordering

The following commands use the system Python that provides pcbnew. Replace the
scratch paths with fresh directories outside the checkout. Existing output paths,
symlinks, archive links/traversal, stale input hashes and ambiguous native UUIDs
are refused. Fetch is an explicit host acquisition step; review tools have no
network access.

```sh
python3 scripts/seeded_benchmark.py fetch benchmarks/seeded/expectations.json /tmp/agent-ee-21-cache
python3 scripts/seeded_benchmark.py prepare benchmarks/seeded/expectations.json /tmp/agent-ee-21-cache /tmp/agent-ee-21-prepared
```

`fetch --archives DIRECTORY` can reuse the pinned original `.tar.gz` archives
already acquired locally; archive, native-file and license hashes are still
checked. Alternate electrical pads come from the installed, hash-pinned KiCad
footprint library and remain in the scratch cache. The
[alternate-library supplement](../benchmarks/seeded/alternate-library.json) records
the exact installed package, attribution and original license hash; fetch retains
that license in the cache. Its upstream source commit has not been independently
recovered from the binary package, and remains a qualification gap.

Preparation performs source parsing and structural validation only. It does not
run ERC, DRC, netlist checks, analyzers or benchmark reviews. Wire edits compare
embedded-pin conductor partitions, including labels, power symbols and junctions;
redundant removals and unintended shorts fail. Flat boards refuse hierarchies, buses
and inherited symbols; [hierarchical boards](#fome-mc-corpus-agentee-108) need a
declared single-level hierarchy. Native `BuildConnectivity/GetConnectedItems` verifies saved
copper preservation for protection removal without refill or DRC. It does not
establish current capacity, fuse behavior or ESD performance.

Commit the actual expectation manifest before any control or mutation review.
Record its full commit ID in the execution request. The runner requires that
commit to be reachable and its manifest bytes unchanged; it also requires a clean,
committed implementation. Corrected expectations need a new commit and subsequent
runs, preserving the original predictions and results. Do not amend earlier
expectations to agree with observations.

## Execution and comparison

`run` uses the manifest's immutable Docker image ID, with an archived implementation
commit mounted read-only at `/opt/agent-ee`. The chosen image has the strict
[qualified toolchain](container.md); the archive pins the newer benchmark source
without rebuilding or changing that toolchain. The execution request records both
identities, prepared-file inventory and command. The container uses `--init`, an
AGENTEE-21-specific name, `--network none`, and the documented outer AppArmor
profile. Native tools still execute through the ordinary networkless sandbox.
No model credentials or model passes are needed.

```sh
python3 scripts/seeded_benchmark.py run benchmarks/seeded/expectations.json /tmp/agent-ee-21-prepared /tmp/agent-ee-21-run1 --expectation-commit FULL_EXPECTATION_COMMIT --image sha256:IMAGE_ID
python3 scripts/seeded_benchmark.py run benchmarks/seeded/expectations.json /tmp/agent-ee-21-prepared /tmp/agent-ee-21-run2 --expectation-commit FULL_EXPECTATION_COMMIT --image sha256:IMAGE_ID
python3 scripts/seeded_benchmark.py report benchmarks/seeded/expectations.json /tmp/agent-ee-21-run1 --second /tmp/agent-ee-21-run2
```

The runner probes the image with `doctor --checks-only`, sets
`AGENT_EE_REQUIRE_TOOLCHAIN=1`, performs all 35 ordinary checks-only reviews and
calls public `verify` on every run. Each board's control and six variants share
the same immutable requirement/criterion baseline. It retains native reports,
sealed findings and execution failures. Checks-only overall results remain
incomplete because initial independent reviews and centralized adjudication have
not occurred. This is expected and does not establish an individual check result.

Reports retain detections, misses, unsupported/not-evaluated cases, execution
errors and unresolved control conditions per predicted check kind. All 30 seeds
remain in the end-to-end denominator. The evaluated-subset denominator is also
explicit. Hardware-interface credit requires the exact invariant to fail with its
paired control satisfied. DRC credit requires a new native violation in the
predicted section at the exact target footprint/pad UUID; generic defects and
unchanged control findings do not count as seed hits. A pre-existing violation of
the same native rule at the target pad remains unresolved even if its witness
wording changes. Checks without a prediction
have zero scored opportunities, not perfect recall.

Control findings require independent engineering adjudication. The report counts
unresolved control findings per kind and leaves false-alarm rates unavailable
until negative opportunities and decisions exist. Zero confirmed false alarms
before that work does not mean the controls are defect-free. Model results, if
added later, must stay in a separate report.
Reviewer false-positive rates are measured separately by the
[false-positive calibration](fp-calibration.md) (AGENTEE-70).

Reruns compare check execution/outcome, details, native violation records,
numerical values and locators. Native finding/item array order is incidental and
is sorted; different witness UUIDs remain differences. Normalization removes
timing, candidate/run IDs,
evidence hashes and native report provenance/date fields; the original sealed
artifacts remain available. Numerical or locator differences remain differences.
The report checks retained snapshot/evidence integrity; the runner's public
verification provides the full runtime/source verification inside the pinned
image. Generated boards and raw runs are not publication artifacts.

## fome-mc corpus (AGENTEE-108)

The fome-mc expectations use the
same `agent-ee.seeded-benchmark/1` format, temporal rule and credit rules for 16
seeds drawn from fome-mc review history (FOMEMC-1..20). Its plan,
`docs/SEEDED_DEFECT_BENCHMARK.md` in fome-mc, catalogues 31 classes F01..F31. The
corpus covers the first batch (F01, F03, F04, F05, F16, F26) plus F02, F07, F08,
F12, F15, F20, F21, F22, F24 and F28. The control is `5954c3a`, the first fome-mc
commit after FOMEMC-17..20. Open findings at `c582b29` are real defects for
AGENTEE-68, not seeds.

fome-mc is the owner's repository and has no licence file. The board record
therefore declares `hardware_grant: null` with an explicit status, and copies stay
scratch-only. `fetch` reads a local clone with `git archive` of only the pinned paths.
It checks the commit and tree identities and every SHA-256, and never writes to the
repository. Shared `../kicad-libraries` are recorded by Git object identity only,
never copied.

### Private store (AGENTEE-118)

The expectations and the run-1 reports are not in this repository. They embed
fome-mc design structure: sheet files, reference designators, pin-to-net maps,
wire coordinates and UUIDs, footprint paths and SHA-256 inventories of the
native files. Publication is the AGENTEE-118 owner decision. They were first
committed on the unpublished local branch `impl/ee108-fome-benchmark`: the
expectations in `f5dbaba`, before any run, and the reports in `0bafbd3`. Those
commits are not for publication.

The private home is `build/fome-mc-benchmark` (ignored) or the directory named by
`AGENT_EE_FOME_MC_BENCHMARK`. It must be a Git checkout of its own. `run` proves
preregistration by Git ancestry in the checkout that tracks the manifest, and
refuses a manifest that is outside Git or untracked (a plain copy under `build/`
included). A copy without Git history would lose the temporal evidence. From a
local clone that holds `0bafbd3`, place and verify the store:

```sh
PYTHONPATH=src python3 scripts/fome_mc_benchmark_store.py place --source PATH_TO_CLONE
PYTHONPATH=src python3 scripts/fome_mc_benchmark_store.py verify
```

`place` makes a shallow, blob-filtered fetch from the clone, down to `f5dbaba`,
and sparse-checks-out only the three files. The clone stays unchanged and is the
store's promisor remote. `verify` checks that the store is its own Git top level
and clean for these files, checks each SHA-256 against the script's manifest, and
replays `committed_manifest` at `f5dbaba`. A later preregistration is a new commit
in the store, made before its run. With the store in place:

```sh
STORE=build/fome-mc-benchmark/benchmarks/seeded
python3 scripts/seeded_benchmark.py fetch $STORE/fome-mc-expectations.json /tmp/ee108-cache --git-source fome-mc=$HOME/git/fome-mc
python3 scripts/seeded_benchmark.py prepare $STORE/fome-mc-expectations.json /tmp/ee108-cache /tmp/ee108-prepared
python3 scripts/seeded_benchmark.py run $STORE/fome-mc-expectations.json /tmp/ee108-prepared /tmp/ee108-run1 --expectation-commit FULL_EXPECTATION_COMMIT --image sha256:IMAGE_ID
python3 scripts/seeded_benchmark.py report $STORE/fome-mc-expectations.json /tmp/ee108-run1
```

`run` records `expectation_store` (`agent-ee` or `private-git`) in its request.
The strict suite tests the store mechanics and the fome-mc operations on owned
manifests and snippets only. `FomeCorpusTests` checks the private corpus itself.
It is collected only when `AGENT_EE_FOME_MC_BENCHMARK` is set, and a store that is
missing or fails verification is then an error, not a skip:

```sh
AGENT_EE_FOME_MC_BENCHMARK=$PWD/build/fome-mc-benchmark AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_seeded_operations -v
```

### Hierarchical addressing

fome-mc has 21 sheet instances from 15 files. A board that declares
`"hierarchy": "single-level sheet instances"` must pin every sheet file in each
case. A selector names the sheet `file`, its native `instance_path` and its
`native_sheet` netlist path; the reference comes from the symbol's instance
record. A sheet file shared by several instances is refused, because one edit
would change every instance, and nested sheets are refused. Hierarchical seeds are
verified by complete terminal partitions from a sandboxed `kicad-cli sch export
netlist`, read by the hardware-interface inventory reader. The flat geometry reader
still serves flat boards. AGENTEE-21 receipts are unchanged.

### Operations

Each operation compares the schematic conductor partition, and the saved-copper pad
partition from `pcbnew` where copper is in scope, with its declared delta.
A difference is refused before registration:

| Operation | Edit | Required connectivity |
|---|---|---|
| `replace_exact_field` | one non-identity field (for example `LCSC`) in schematic and/or PCB; `Reference`, `Value`, `Footprint` and power symbols refused | unchanged |
| `set_assembly_state` | `dnp` and/or `exclude_from_bom` Booleans (`in_bom`, footprint `attr`); other attributes refused | unchanged |
| `remove_component` | the symbol and its footprint | remaining groups minus the removed terminals and pads; a pad that joins other copper is refused |
| `project_rule_edit` | one scalar under `board.design_settings.rules` or `net_settings`, by exact text, then a whole-JSON comparison | unchanged (native netlist with the project file) |
| `pcb_track_width` | every segment/arc of one net at the declared width | unchanged copper partition |
| `pcb_move_footprint` | the footprint `at`; tracks stay | only the moved pads may lose copper; nothing gains a connection |
| `pcb_delete_zone` | one zone by UUID, net and layer | unchanged copper partition |
| `fab_file_edit` | one non-key cell of one uniquely keyed CSV row; native design files refused | no design file changes |
| `rename_exact_label` | one label text | the declared isolate/merge/move delta exactly |
| `move_wire_endpoints` | declared wire endpoints | the declared isolate/merge/move delta exactly |

The last two serve F16 and F04. A catalogue swap that a straight two-point wire
cannot make without crossing other pins is replaced by one of them. Each case
records the substitution. Where a catalogue pin has no wire, as in F02,
the case seeds a wired pin of the same class instead.

### Planned detectors and native visibility

An expectation of kind `planned` names its AGENTEE detector with
`expected_execution: unsupported`. The scope declares no such check, so the report
scores it as unsupported and refuses it if a record ever appears. ERC credit needs
the predicted native violation type at a registered schematic item UUID that the
paired control does not report. DRC `unconnected_items` predictions may name pads
of the selected footprint; their retained UUIDs are the targets.

Each case records `native_visibility.real_defect`, the catalogue judgement of
whether native ERC/DRC would see the real defect. The report's
`native_visibility` separates those seeds from the rest. For each seed it lists
native predictions credited and any new native ERC/DRC records against the
control. Schematic-only wiring seeds keep PCB copper, so their parity records are
seed artifacts rather than evidence that a synchronized real defect is visible.

### First run (2026-10-18)

The expectations were committed at `f5dbabac9c81bbee1fce6df0188475c555c2dd94`
before any fome-mc run. Run 1 used that implementation in image
`sha256:d6094c3b…0234` with no network and no model pass. All 68 declared checks in
the 17 reviews (control plus 16 seeds) executed `ok`. The run 1 report
(`fome-mc-report-run1.json` in the [private store](#private-store-agentee-118)) is
retained as produced.

| Kind | Detection | Not evaluated | Unsupported |
|---|---|---|---|
| `hardware_interface` | 9 | 0 | 0 |
| `erc` | 1 | 0 | 0 |
| `drc` | 4 (6 corrected) | 2 (0 corrected) | 0 |
| `planned` | 0 | 0 | 22 |

There were no misses, execution errors or control-unresolved predictions. Eleven of
16 seeds have a credited prediction. The other five (F07, F12, F20, F21, F22) have
only planned detectors and stay in the denominator. The control's
hardware-interface invariants were all satisfied and ERC reported nothing. DRC
reported 484 control records (199 `lib_footprint_mismatch`, 172 `silk_overlap`,
100 `silk_over_copper` and 13 `lib_footprint_issues`), all unresolved and none
adjudicated.

The two F01 and F04 parity predictions were `not_evaluated` because the matcher
required the predicted pins on every selector footprint. Those cases also select
other components for their interface relations. The matcher now targets the predicate's
named component, and AGENTEE-21 cases already selected only that component. The
corrected report (`fome-mc-report-run1-corrected.json`, private store) applies
the fix to the same retained run-1 artifacts; no run was repeated.

Native visibility: F02 is the only seed whose real defect native tools see. ERC
credits its `pin_not_connected` at the seeded pin. Among the 15 natively invisible
real defects, six gained native records, and each record is a seed artifact.
F01, F04 and F16 are schematic-only, so they gained `net_conflict` parity records.
F04 also gained `pin_not_connected`/`pin_not_driven` for the IN- pin it leaves
floating. F15 left `unconnected_wire_endpoint` and `track_dangling` records. The
copper left by F26 and F28 produced unconnected, clearance and shorting records.
Ten of the 15 were credited by non-native or artifact predictions. The five
planned-only seeds were not.

