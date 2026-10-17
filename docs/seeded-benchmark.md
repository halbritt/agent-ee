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
redundant removals and unintended shorts fail. Hierarchies, buses and inherited
symbols are refused. Native `BuildConnectivity/GetConnectedItems` verifies saved
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

Reruns compare check execution/outcome, details, native violation records,
numerical values and locators. Native finding/item array order is incidental and
is sorted; different witness UUIDs remain differences. Normalization removes
timing, candidate/run IDs,
evidence hashes and native report provenance/date fields; the original sealed
artifacts remain available. Numerical or locator differences remain differences.
The report checks retained snapshot/evidence integrity; the runner's public
verification provides the full runtime/source verification inside the pinned
image. Generated boards and raw runs are not publication artifacts.
