# Owned telemetry-pod symbols

`../libs/Pod.kicad_sym` contains original rectangular drawings for PESD1CAN-UX
and LSM6DSO32 (AGENTEE-168). No vendor or KiCad library graphics were copied.
Neither symbol uses arcs or inheritance. KiCad 10.0.7 supplied format defaults;
the PESD1CAN names retain the spaces in the datasheet table.

`capture.json` is the existing `agent-ee.symbol-capture/1` receipt. The library
is stored in the capture writer's canonical form: capturing both symbols again
reproduces the same bytes. `capture-manifest.json` binds that single file as
both source and subset, the receipt, and each datasheet/facts pair by SHA-256.
The datasheets are cited, not committed. The URLs identify the publisher and
the mirror from which the pinned PDF bytes were retrieved; a URL alone does
not authenticate a document.

| Part | Datasheet citation | PDF SHA-256 |
| --- | --- | --- |
| PESD1CAN-UX | [Nexperia PESD1CAN-U](https://assets.nexperia.com/documents/data-sheet/PESD1CAN-U.pdf), Rev. 1, 27 March 2013, physical page 1, section 2, Table 2 | `4f524ce85507734cfa700935dae241de2c7d6e0cc1ce55731e0cff9f783ae226` |
| LSM6DSO32 | [ST LSM6DSO32](https://www.st.com/resource/en/datasheet/lsm6dso32.pdf), DocID032891 Rev 1, March 2020, physical page 24, section 3, Table 2 | `4402a39b852970a820db832682ca2d1c9704243a927c440dc4751995bffe4c8c` |

The sibling `pesd1can-ux/datasheet-facts.json` and
`lsm6dso32/datasheet-facts.json` contain only authored citation metadata and
pin names/numbers. They use the existing `agent-ee.datasheet-facts/1` format.
Replay with the `datasheet_facts` check requires the exact PDFs at the declared
capture-relative paths; an absent or changed PDF remains unknown coverage.
The ordinary fixture tests check the metadata, not PDF presence or authenticity.

`pins.json` uses the existing `agent-ee.symbol-query/1` declaration with an
`expected` name/type contract for every pin. Electrical types are an authored
mapping of the pin-table functions to KiCad types, not verbatim datasheet text.
The generic IMU symbol represents both table modes and all main bus interfaces;
it does not assert that a particular circuit configures those modes correctly.
Pin 1 uses the table's SDO name; SA0 is its alternate address-input function.

| Part | Pin numbers | KiCad type | Basis for mapping |
| --- | --- | --- | --- |
| PESD1CAN-UX | 1, 2, 3 | `passive` | Protection-diode terminals |
| LSM6DSO32 | 1 | `bidirectional` | SDO output or SA0 input |
| LSM6DSO32 | 2, 3 | `bidirectional` | Auxiliary master signals, tied high/low in mode 1 |
| LSM6DSO32 | 4, 9 | `bidirectional` | Interrupt outputs with input functions in other modes |
| LSM6DSO32 | 5, 6, 7, 8 | `power_in` | I/O supply, grounds and supply |
| LSM6DSO32 | 10, 11 | `no_connect` | NC pins remain physical solder pads |
| LSM6DSO32 | 12, 13 | `input` | Mode select and main serial clock |
| LSM6DSO32 | 14 | `bidirectional` | Main serial data input/output |

`assignments.json` and `footprint-libraries.json` pin the stock KiCad 10.0.7
`Package_TO_SOT_SMD:SOT-323_SC-70` and
`Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y` packages. Tests read their
bytes from `/usr/share/kicad/footprints` at runtime, check their hashes and exact
3/14 electrical pad sets, and retain NC pads in the comparison. Stock footprint
bytes are not committed. This verifies pad/pin parity, not physical land-pattern
suitability or mode-specific circuit behavior.

Run from the repository root (the existing tests import helpers from `tests`):

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest \
  tests.test_pod_symbols tests.test_footprint_assignment \
  tests.test_schematic_emitter.NativeEmissionTests.test_pod_owned_symbols -v
```

The pin-swap controls rehash a modified library, so they exercise the pin
contract rather than merely the library hash gate. Missing or changed library
bytes remain unknown. Attribute expectations join the immutable scope/2 and
scope/3 baseline. The emitter test is a pin exercise, not a functional pod
circuit: native KiCad ERC has zero violations, native XML checks both complete
pin inventories and four net terminal sets, and forced re-save preserves the
sheet apart from the generator and KiCad's space-to-underscore pin-name encoding.
