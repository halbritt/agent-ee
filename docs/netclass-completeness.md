# Captured netclass completeness

`netclass_completeness` checks the presence of the complete Default netclass
fields in captured `.kicad_pro` bytes. Parameters are exactly `project` and
`qualification`, each a captured relative path. Capture the supplied
[qualification](../fixtures/netclass-completeness/qualification.json) as an
immutable verification dependency. The project remains mutable candidate input.
Map the check to an explicit requirement to retain a complete Default class.
Scope/3 declares the `netclass:default-completeness` predicate.

The qualified profile is KiCad 10.0.7, `net_settings.meta.version == 5`.
The [empty native project](../fixtures/netclass-completeness/native.kicad_pro)
was generated through `pcbnew.SETTINGS_MANAGER.LoadProject/SaveProject` using
10.0.7-10.0.7~ubuntu24.04.1. Its [generator](../fixtures/netclass-completeness/generate.py)
uses no third-party design. Qualification records its hash and the exact primary
source hashes. The writer and loader are in
[common/project/net_settings.cpp](https://github.com/KiCad/kicad/blob/10.0.7/common/project/net_settings.cpp)
(lines 38, 65–224); the initialized defaults are in
[common/netclass.cpp](https://github.com/KiCad/kicad/blob/10.0.7/common/netclass.cpp)
(lines 58–99). Downloaded source is not redistributed.

Default is selected by exact name, independent of list order. Every native
writer field is required on Default. Missing Default violates this explicit
completeness policy, even though KiCad can supply implicit defaults in a new
project. Custom classes may omit inherited fields. Duplicate names and invalid
JSON/container shapes are malformed; unsupported settings versions are unknown.
`wire_width: 0` is present and passes. This check evaluates presence only: values,
types, physical suitability and rendering behavior are outside its predicate.

`netclasses.json` retains the candidate, project and qualification hashes and
per-class missing keys. Existing coordinator evidence sealing and public
verification cover this report. Unreadable or malformed projects produce
`execution=malformed`, `outcome=unknown`; they cannot establish a defect witness.

The tests exercise the native fixture, each missing Default field, zero width,
reordering and inheritance, missing/duplicate Default, malformed bytes,
unsupported versions, mutable project/immutable policy ownership and tampering.
They establish structural conformance. Junction stripping, wire plotting and
absence of native ERC findings have not been reproduced by this qualification.
