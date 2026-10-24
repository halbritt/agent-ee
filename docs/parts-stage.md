# Parts production into native connectivity

`agent-ee parts compile` replays a captured parts manifest and projects its
selected identities into ordinary connectivity intent. It replaces the
three-role RC projection at this boundary; it does not synthesize a circuit
from prose or complete the full build workflow.

```sh
agent-ee parts compile --bundle CAPTURED_BUNDLE \
  --manifest parts.json --circuit circuit.json --layout layout.json \
  --out NEW_DIRECTORY
```

Inputs are the existing [parts/1 manifest](approved-parts.md), its complete
catalog, selection, datasheet and CAD dependencies, a connectivity-intent/1 or
/2 circuit, and its schematic-layout/1 record. Paths other than the output
directory are relative to the captured bundle. An empty circuit footprint
requests the selected footprint. A nonempty footprint, symbol, supplier property
or passive nominal value that disagrees with the qualified selection
refuses production. The circuit's topology, values and pin selectors remain
engineering inputs; no approval authority is inferred from them.

For a named resistor or capacitor, the producer rechecks the exact purchased
row in the captured supplier response and requires one parseable `Resistance`
or `Capacitance` attribute matching the circuit value. Missing, duplicate,
malformed and contradictory claims refuse; zero-ohm resistors are allowed.
`report.json` retains each checked nominal, raw attribute, purchased identity,
response path and hash. This is a supplier-claim consistency check; tolerance,
rating applicability and circuit function still need their own checks. The
same captured-row reader supplies the existing passive-rating derivation.

The compiler uses the existing `parts_manifest`, `binding_fill`, connectivity
resolver and emitter identity calculation. It derives part references from exact
native sheet/reference allocations. Native child paths are calculated with the
same UUID routine as schematic emission. A required missing allocation or
datasheet, stale captured file, extra physical component or unaccounted pin
refuses before publishing a compiled intent. Optional absent instances are
listed explicitly. Resolved power symbols remain virtual.

`--deviations PATH` supplies the existing captured assembly deviation record;
omitting it means no assembly deviations. `--ratings PATH` supplies the existing
source-derived passive rating inputs. Both records and their dependencies are
bound in the production report. The compiler reuses their existing validators;
it does not infer hand assembly or manufacture rating evidence.
The [build workflow](build-workflow.md) carries the adopted parts receipt and
report into the final candidate and binds them to assembly checks automatically.
Independent builds must capture that exact evidence and declare the existing
`rating_provenance` parameter; a native rating field alone is insufficient.

A successful output contains `connectivity-intent.json` and `report.json`.
The report records all source hashes, selected instance bindings, datasheet
acquisition identities, optional absences, output hash and limitations. It keeps
`acceptance_ready:false`. Independent emitted-native membership, electrical,
physical and manufacturing checks remain necessary. Refusal returns exit 1 and
a report without a compiled intent; malformed invocation or output-directory
failure returns exit 2. Existing output directories are never overwritten.

`parts_stage.configuration` declares a normal sandboxed `parts` step with one
output intent. The schematic stage consumes that exact output through ordinary
receipts and adoption. The bundle remains immutable. Its source snapshot and
receipt include the same implementation custody as other stage producers.

For a workflow whose parts dependencies and native outputs share one captured
root, use `bundle='.'` and the schematic stage's `captures='.'`. The parts step
binds its adoption target and refuses a target that would overwrite any source
dependency or its parent directory. The native integration case uses this same
root directly for the independent membership check; it requires no rewritten
manifest paths or manually assembled intermediate capture.

The targeted native exercise uses original synthetic catalog/PDF/CAD captures,
not real device qualification. It carries three selected parts on a child
sheet through parts production, public [scaffolding](scaffold.md), schematic
emission, native netlist export and forward annotation. The scaffold consumes
the actual bound intent, includes a nested empty sheet and supplies every native
file; the test does not copy placeholder native files. Native ERC loads all
three empty sheets before emission. It checks exact supplier identities on both
schematic and PCB, unchanged document identities and project rules, sheet paths
and consecutive receipt hashes. Negative cases include a
consequential resistor-value mismatch, contradictory CAD/supplier fields,
missing required datasheet, wrong sheet allocation, unaccounted pin and stale
library, a conflicting scaffold project and a layout that would be overwritten
by native output. The old binding producer fails the new hierarchy test by reporting
zero bound components. Its local-ID lookup has been corrected to use the
resolver's sheet-qualified component identity.

The native integration invocation passed 168 tests with no skips in 78.214
seconds. The existing probe, copper-feature and part-binding suite passed 60
tests with no skips in 48.257 seconds. A fresh strengthened native exercise
then independently checked all three expected net memberships and passed the
existing `parts_subset` checker against the emitted hierarchy (3.358 seconds).
These invocations overlap. Earlier failures in test harness API/path handling
are retained alongside the corrected runs; they were not accepted evidence.

The subsequent scaffold identity integration passed 142 tests with no skips in
124.992 seconds, including parts production, public scaffold, native schematic
stages, producer-rule checks and legacy generation consumers. An earlier run
refused a concurrent source edit through `LivePackageSourceError`; the stable
rerun supplies the accepted result. Its evidence is retained under
`build/agent545/scaffold-emission-*`.

```sh
ae-ci-exec . -- env PYTHONPATH=src:tests AGENT_EE_REQUIRE_TOOLCHAIN=1 \
  python3 -m unittest -v tests.test_parts_stage tests.test_binding_fill \
  tests.test_approved_parts tests.test_parts_subset tests.test_schematic_stage \
  tests.test_schematic_hierarchy tests.test_schematic_emitter \
  tests.test_emitter_parity tests.test_cli
```

Working evidence is under `build/agent545/parts-stage-*`. The broader
[acceptance map](system-acceptance.md) remains open: public build orchestration,
fresh sourced multiblock circuit production, routing, final acceptance and
manufacturing packages must still be demonstrated together.

The durable evidence copy is
`~/.cache/agent-ee-qualification/agent545/parts-stage-20261024/`, including
source identity, native outputs, source captures, receipts, independent
membership report, failure logs and a verified `SHA256SUMS` inventory.
