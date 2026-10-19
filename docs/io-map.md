# Compile a pinout CSV

`agent_ee.io_map` compiles a declared CSV into the existing
`agent-ee.interface/1` contract, checked by `hardware_interface` against a native
KiCad netlist. Compilation runs offline before capture. It does not select pins
or establish that a design meets electrical, radio or firmware requirements.

Reproduce the [pod contract](../fixtures/telemetry-pod/io-map.interface.json):

```sh
PYTHONPATH=src python3 -m agent_ee.io_map fixtures/telemetry-pod/io-map.csv \
  --csv-path io-map.csv --requirement POD-AC-1 --source spec.md \
  --locator 'Acceptance criteria / item 1' \
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

## CSV and bindings

Required headers are `Connector_Pin, Signal_Name, Net, MCU_Ref, MCU_Pin,
External_Destination`. Extra declared columns are retained as input bytes;
`Direction_Class=No_Connect` and `Signal_Name=NO_CONNECT` declare a no-connect.
Such a row must have `Net=N/C` and both MCU fields `N/A`. Wiring one is refused.
Duplicate headers, duplicate connector positions, empty cells and ragged rows
are refused with the CSV line number (the final physical line of a quoted row).
`MCU_Pin=N/A` means no MCU terminal is applicable; `TBD` means unresolved.

Concrete MCU rows require the extra column `MCU_Path` with value `direct`.
This explicitly declares that the MCU pin and connector share a conductor.
Missing or `TBD` paths on concrete MCU rows are refused. Other path values
are refused with the row number, signal and path name. For example,
`MCU_Path=TJA1051T/3 TXD/RXD -> CANH/CANL` reports
`row 2: unsupported indirect MCU_Path 'TJA1051T/3 TXD/RXD -> CANH/CANL' for CAN_H`.
The caller must use a separately authored contract with the intermediate part's
pins and distinct conductors for that circuit. The compiler does not infer
connectivity from signal names or Notes. Use `N/A` in this column for rows
without an MCU terminal.

The pod's CAN transceiver and sensor ADC paths are indirect. Filling in their
MCU pins in the pinned CSV alone is refused; those paths must never be declared
`direct`. CAN TXD/RXD do not share a conductor with CANH/CANL, and an MCP3204
analog input does not share a conductor with its MCU SPI connection. Named
indirect paths require a separate contract; this compiler does not compile them.

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

`no_connect` is satisfied only for a known disconnected native terminal. A
conductor is a violation; ambiguous generated singleton names stay unknown under
the existing source-geometry qualification. No-connect does not prove a physical
cavity plug is installed. Unknown bindings never pass or become absence defects.

`tests.test_io_map` exercises native CAN polarity at J1, a wired no-connect and
missing bindings. It also reproduces four CAN1 named-net obligations from the
[pinned fome-mc excerpt](../fixtures/telemetry-pod/fome-mc-can1.interface.json),
derived from `contracts/psu.interface.json` at `d31a690`. The probe schematic is
original test material; it is not a proposed pod design.
