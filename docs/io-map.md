# Compile a pinout CSV

`agent_ee.io_map` compiles a declared CSV into the existing
`agent-ee.interface/1` contract, checked by `hardware_interface` against a native
KiCad netlist. Compilation runs offline before capture. It does not select pins
or establish that a design meets electrical, radio or firmware requirements.

Compile the [pod contract](../fixtures/telemetry-pod/io-map.interface.json):

```sh
PYTHONPATH=src python3 -m agent_ee.io_map fixtures/telemetry-pod/io-map.csv \
  --csv-path io-map.csv --requirement POD-AC-1 --source spec.md \
  --source-file fixtures/telemetry-pod/spec.md --locator 'Acceptance criteria / item 1' \
  --spec-record fixtures/telemetry-pod/spec-record.json --out /tmp/pod.interface.json
```

The [CSV](../fixtures/telemetry-pod/io-map.csv) is copied byte for byte from
fome-mc `e33c0e1` (FOMEMC-30). Its full commit and blob identities are in
[file-origins.json](file-origins.json). The contract preserves 29 rows: 23
AMPSEAL cavities (16 used, seven no-connect), radio, SMA and UART entries.
`--spec-record` requires every captured `POD-IF-*` ID to appear in row Notes
and refuses unknown IDs. This is traceability; a reference to a radio requirement
does not prove BLE functionality. The pinned CSV leaves native references and
MCU pins undecided, so its compiled fixture produces unknown coverage.

## Requirements source provenance

The CLI records `source.sha256`, lowercase hexadecimal SHA-256 of
the exact requirement file bytes, on every invariant. The CLI reads `--source`
relative to its working directory; `--source-file FILE` supplies those bytes from
another location without changing the captured path. A missing source, with or
without `--require-source-sha256`, exits 2 with a typed
`missing-source (FileNotFoundError)` error on stderr before writing any contract.
Other read failures also fail. The flag remains accepted for existing callers.
Existing digest-free contracts and Python API compilation without source bytes
remain valid under `agent-ee.interface/1`. The example above equals the pinned
fixture apart from the added source digest fields.

For example, run from a captured project root:

```sh
PYTHONPATH=/path/to/agent-ee/src python3 -m agent_ee.io_map telemetry-pod/io-map.csv \
  --csv-path telemetry-pod/io-map.csv --requirement POD-AC-1-pinout \
  --source telemetry-pod/REQUIREMENTS.md --locator 'Acceptance criteria / item 1' \
  --require-source-sha256 --out pod.interface.json
```

The Python API accepts `source_bytes=raw_bytes` and
`require_source_sha256=True`; strict compilation requires bytes to compute the
digest. An already declared digest must match those bytes.
`validate_contract(contract, source_bytes={captured_path: raw_bytes},
require_source_sha256=True)` requires local source digests and verifies them.
Every local source path must use one digest on all its invariants, or omit it
on all of them, even when bytes are unavailable. Different paths may use
different digests or independently omit them. When bytes are supplied,
every hashed local source must have an entry. File bytes are never decoded or
newline-normalized for hashing. These options do not change pinned-external
citations, which retain their separate source contract.

## Requirement qualification

Each `io_map.requirements` entry is either a bare ID string (legacy) or an
object with an enforced `qualification`: `unqualified-carryover` (`id`,
`qualification`) or `sourced` (`id`, `qualification`, `source` = `path`,
`sha256`, `locator`). A bare ID is read as `unqualified-carryover`, never as
sourced, so existing contracts and the pod fixture validate unchanged. A
`sourced` entry without a complete source, a missing or unknown status, an
`unqualified-carryover` entry carrying a source, or a `sourced` source that is
the inherited io-map CSV itself is refused by `validate_contract`. `capability`
also requires every sourced source to be a captured input with the declared
`sha256`, and makes it immutable. An ID is reported sourced only if all its
citations are sourced.

### Qualification-bound contracts (historical provenance)

Legacy mode cannot detect lost or rewritten statuses, so a carried table (for
example v9 citations) is compiled with `--history FILE`
(`agent-ee.io-map-history/1`: `origin` = original `path`, `revision`, `sha256`,
`locator`; optional `rows` = connector pin to per-row locator; `sourced` = IDs
promoted with a current source) plus `--record-path` (the captured path the
contract declares) and `--record-out`. The compiler then writes an
`agent-ee.io-map-qualification/1` record binding every compiled row (row number,
connector pin, signal, external destination, complete requirement entries with
status) to its original provenance, and the contract declares
`qualification_record` `{path, sha256}`.

The scope's `hardware_interface` check params must pin the same record with the
optional `qualification_record` `{path, sha256}`; the contract may not declare a
record the scope does not pin, and stripping the declaration while the scope
pins one is refused. A present but null, empty or wrong-typed pin or
declaration is refused, never read as absent.

#### One record loader

Every consumer obtains a record through one typed loader:
`load_qualification_record(declaration, raw) -> QualificationRecord`. It takes
the record's exact bytes, checks their sha256 against the declared pin, parses
strict JSON (UTF-8, unique keys, no NaN) and validates the
`agent-ee.io-map-qualification/1` schema unconditionally. A parsed `null`,
empty or wrong-typed record or field is refused; absent means no bytes were
supplied, and a bound contract then refuses. Every presence, type and value
constraint is checked at runtime on every call, for every consumer. Python
type hints are not validation: a bool or float row, a numeric string, a
whitespace-only value, an uppercase or short digest, an unsafe path, an extra
key and a near-miss status are all refused. `bind_qualification_record` takes
the record bytes and runs the loader itself, so no pre-built record object
bypasses the schema. It then binds the record to the contract and to captured
bytes. Every citation
must equal its record row: row number, connector pin, signal, external
destination, and the complete requirement entries with their statuses. Every
record row must be cited. The captured io-map CSV must match the record's
`io_map` sha256. Each record row must equal the CSV row it names: descriptive
values and the `POD-IF-*` IDs in its Notes. Each `sourced` source must be
captured with its declared sha256.

The consumers are `verify_contract`, `validate_contract` (which returns only
the contract), `capability` (and through it `compile_coverage` and the native
check) and `compile_io_map`. They all call `verify_contract(contract,
qualification=raw_bytes, captured={path: bytes})`; `capability` supplies its
captured inputs on demand. A bound contract therefore validates only with the
record bytes and the captured CSV and sources. `validate_contract_shape` and
`load_contract_unverified` are the explicitly UNVERIFIED shape-only forms.
They are used for input discovery and never for a qualification verdict.

#### Original provenance

Origins are verified per distinct (`path`, `sha256`) pair. One origin path
must declare one revision and sha256 on every row. Conflicting declarations
are refused before any row is labelled, in any row order, whether or not that
path is captured. Each row's label comes from its own pair. The label is
`verified` only when that pair's captured bytes match; it is
`declared-unverified` when the path is not captured, and mismatching captured
bytes refuse. A verified origin becomes an immutable input. Authenticating an
origin proves those bytes only. It never qualifies a requirement or confers
authority: a carried ID stays an `unqualified historical assertion` under a
verified origin, and a `sourced` entry may not cite an origin path. An
uncaptured origin is never dropped; each of its rows is reported
`declared-unverified`. Native structural grading is unchanged.
`--history-source FILE` (API `origin_bytes=`) supplies the origin bytes to the
compiler, and `--captured PATH` (API `captured=`) supplies each sourced
requirement source. The compiler verifies its own output with the same loader,
so a history whose sourced source is missing or mismatching is refused. The
CLI refuses a JSON input file whose value is not an object, for example
`null`. `requirement_history.rows` reports per row whether the origin is
`verified` or `declared-unverified`, and whether each ID is `sourced` or an
`unqualified historical assertion`. The native check reports this as
`details.requirement_history`.
Contracts without a declaration remain valid legacy inputs, reported
`qualification_bound: false`. They cannot stand in for a bound contract under
a pinned scope.

#### Mutation matrix

`tests/test_qualification_matrix.py` is the regression gate for this boundary.
It runs every case against the validator, capability, coverage and compiler,
and every refusal case must refuse in all of them. The cases cover null,
empty, wrong-typed and missing records and fields, declarations and pins,
loss, upgrade and bare-ID citation changes, and changed descriptions with the
CSV unchanged. They also cover records omitted, substituted or changed under
an unchanged pin, conflicting origins in every order, legacy stand-ins and the
reviewer probes. The compiler consumer accepts a bundle only if it would emit
that contract and record. The validator and compiler have no scope input, so
the scope-pin cases do not apply to them. `python3 -m
tests.test_qualification_matrix` prints the per-consumer verdict table.

`--qualifications FILE` (API `qualifications=`) is the lighter form: it marks
sourced IDs and emits the rest explicitly, without a record. A mapping for an ID
that no row cites is refused.

### Consumers

Unqualified citations are traceability only. The hardware_interface check
records `details.requirement_qualification` (`sourced`, `unqualified-carryover`)
and a limitation naming them. `compile_coverage` adds, per check, a
`requirement_qualification` (the same lists, `qualification_bound` and the
original `origins` per ID) and a report-level `requirement_citations`
(`qualified`, `unqualified-carryover` per check, `counted_as_coverage: false`).
Carry-over never counts as qualified coverage; structural predicates keep their
independently bound requirement source and grading. With `--spec-record`, the
CLI prints `spec traceability: qualified=... unqualified-carryover=...`, and the
API fills `report=` with the same split. Traceability still requires every
`POD-IF-*` ID to be cited; it no longer implies that token presence is
qualified.

## CSV and bindings

Required headers are `Connector_Pin, Signal_Name, Net, MCU_Ref, MCU_Pin,
External_Destination`. Extra declared columns are retained as input bytes;
`Direction_Class=No_Connect` and `Signal_Name=NO_CONNECT` declare a no-connect.
Such a row must have `Net=N/C` and both MCU fields `N/A`. Wiring one is refused.
Duplicate headers, duplicate connector positions, empty cells and ragged rows
are refused with the CSV line number (the final physical line of a quoted row).
`MCU_Pin=N/A` means no MCU terminal is applicable; `TBD` means unresolved.

Concrete MCU rows require the extra column `MCU_Path`. The value `direct`
explicitly declares that the MCU pin and connector share a conductor. Missing or
`TBD` paths on concrete MCU rows are refused. Without `--paths`, other values
are refused with the row number, signal and path name. For example,
`MCU_Path=TJA1051T/3 TXD/RXD -> CANH/CANL` reports
`row 2: unsupported indirect MCU_Path 'TJA1051T/3 TXD/RXD -> CANH/CANL' for CAN_H`.
The compiler does not infer connectivity from signal names or Notes. Use `N/A`
in this column for rows without an MCU terminal.

The pod's CAN transceiver and sensor ADC paths are indirect. Filling in their
MCU pins in the pinned CSV alone is refused; those paths must never be declared
`direct`. CAN TXD/RXD do not share a conductor with CANH/CANL, and an MCP3204
analog input does not share a conductor with its MCU SPI connection.

## Indirect MCU paths

`--paths paths.json` supplies owner-declared indirect paths:

```json
{"schema": "agent-ee.io-map-paths/1", "paths": {"can-tx": {
  "terminals": ["J1-1", "U1:3", "U1:1", "U3:1"],
  "pin_names": ["Pin_1", "CANH", "TXD", "IO1"],
  "devices": [{"component": "xcvr", "value": "TJA1051T-3", "role": "CAN transceiver"}],
  "directions": ["bidirectional", "toward_connector"]}}}
```

On such a row, `MCU_Path` lists path IDs separated by `;` and `MCU_Pin` lists
the same number of MCU pins in the same order. For example, `MCU_Pin=1;2` with
`MCU_Path=can-tx;can-rx`. Each path must start at the row's `Connector_Pin`
terminal and end at `MCU_Ref:MCU_Pin`. Each path is used by exactly one row and
pin. Undeclared and unused paths are refused, as are TBD MCU identities and rows
without a connector net. Path terminals are looked up in `--bindings` like other
terminals; an unbound one stays explicitly unresolved and its path
`not_evaluated`. Every `devices` component must be declared in the bindings, or
the row is refused as an undeclared intermediate device.

Each declared path compiles to one `indirect_path` invariant
`<Connector_Pin>:mcu-path:<id>` with the row's `io_map` citation. It replaces the
row's MCU `named_net` and `same_net`, so connector and MCU are never asserted to
share a conductor. Every intermediate part also receives a populated predicate.
The [interface reference](hardware-interface.md#declared-indirect-paths) defines
the native evaluation: each segment is one native conductor, each part has its
declared value and pin names, no part is bypassed, and pin types agree with
directions. Topology success makes no transfer, accuracy, stability, CAN-rate,
firmware or BLE claim. Radio rows without a net remain unresolved.

Supply `--bindings existing.interface.json` to reuse that validated contract's
component and terminal declarations. Its predicates and allowed edits are not
copied. Terminal IDs must equal the row's `Connector_Pin`, or `MCU_Ref:MCU_Pin`
for an MCU terminal. An MCU pin shared by several rows must name the same net.
Unused bindings are refused, including attempts to bind a TBD MCU pin. No
reference prefix or sheet path is inferred. Component declarations retain the
ordinary exact native location and approved symbol rules.

For each electrical position the compiler emits `named_net` or `no_connect`;
for each concrete MCU pin with `MCU_Path=direct` it emits `named_net` and a
`same_net` relation to the connector. A TBD MCU identity or net emits an
`unresolved` MCU predicate without asserting net equality. The pinned fixture
contains 37 invariants, including eight unresolved MCU obligations. Each
referenced component receives a populated predicate. A
missing binding becomes a terminal with exactly `{"unresolved": "reason"}`.
A radio row without an electrical net emits an `unresolved` predicate with a
reason; net `TBD` also remains unresolved. These declarations always evaluate
as `not_evaluated`, keeping the enclosing check `unsupported/unknown`.

Every generated invariant has an `io_map` citation with captured CSV `path`,
`sha256`, physical `row`, `connector_pin`, `signal`, `external_destination` and
the list of interface `requirements` cited in Notes. Component predicates cite
the first row that references the part. Destinations and interface IDs are text
traceability, not graded predicates. Requirement authority still comes from the
ordinary invariant `requirement` and exact `source`; version 1 retains one
requirement per check. Use the pinout acceptance requirement for this contract.

## Capture and evaluate

Capture the compiled contract, CSV and requirement source as scope inputs.
Configure the ordinary `hardware_interface` check with `contract` and an earlier
`netlist_check`. Each CSV path must be captured and match its citation's hash.
The capability includes the CSV in immutable inputs, so candidate repairs cannot
change it. Changing its bytes requires recompilation and a new baseline.
If an invariant carries `source.sha256`, capability validation also compares it
with the captured requirement bytes. Its `path` and `locator` must still match
the baseline source exactly; the scope baseline source shape is unchanged.

`no_connect` is satisfied only for a known disconnected native terminal. A
conductor is a violation; ambiguous generated singleton names stay unknown under
the existing source-geometry qualification. No-connect does not prove a physical
cavity plug is installed. Unknown bindings never pass or become absence defects.

`tests.test_io_map` exercises native CAN polarity at J1, a wired no-connect and
missing bindings. It also reproduces four CAN1 named-net obligations from the
[pinned fome-mc excerpt](../fixtures/telemetry-pod/fome-mc-can1.interface.json),
derived from `contracts/psu.interface.json` at `d31a690`. The probe schematic is
original test material; it is not a proposed pod design.

`tests.test_io_map.IndirectPathTests` covers compiling paths on an original
synthetic transceiver/resistor/ADC probe, together with refusals and evaluator
violations. These include severed, bypassed and shorted segments, wrong parts,
pin names and directions, and dropped intermediates. Absent native evidence
stays unknown. `NativeIndirectPathTests` runs kicad-cli on the probe. The
declared paths pass, while a severed segment and a changed transceiver value are
native defects.
