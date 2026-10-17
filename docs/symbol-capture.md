# Large-symbol capture

The shared symbol reader refuses libraries above 32 MiB, 128 nesting levels or
two million S-expression nodes. KiCad 10.0.7's `MCU_ST_STM32H7` and
`FPGA_Xilinx_Virtex7` libraries exceed the node limit, so no symbol in them
resolves directly. `agent_ee.symbol_capture` copies the needed symbols into a
small subset library that the same reader then accepts under its usual limits.
It does not raise those limits or add a second pin interpreter.

```sh
PYTHONPATH=src python3 -m agent_ee.symbol_capture \
  /usr/share/kicad/symbols/MCU_ST_STM32H7.kicad_sym \
  --symbol STM32H723ZGTx --out build/captures/stm32h7
```

The command reads the source library and never writes to it, to a global
library table or to an existing directory. Output is published only when it
is complete. It creates the new directory with two files:

- `MCU_ST_STM32H7.kicad_sym`: the subset library;
- `capture.json`: the capture receipt.

The library nickname defaults to the source file stem, so
`MCU_ST_STM32H7:STM32H723ZGTx` keeps its original `lib_id`. Use `--library` to
choose another nickname. Repeat `--symbol` to capture several symbols from one
library. Exit 0 means published. A refusal prints its reason, exits 1 and
publishes nothing.

## What is checked

A lexical scan reads the whole source without building its tree. It applies the
shared reader's grammar for whitespace, `;` comments, quoted strings and
backslash escapes. The scan must find all of these, or it refuses the library:

- valid UTF-8 throughout;
- exactly one balanced `kicad_symbol_lib` root;
- terminated strings and escapes;
- a unique string identity for every top-level symbol;
- only `version`, `generator` and `generator_version` as other library-level
  nodes, each at most once.

A malformed suffix after the selected symbol refuses the whole capture. A
symbol name found only in a comment or a quoted string is not structure.

The closure is the selected symbols plus every transitive `extends` ancestor.
The shared `parse_sexpr` reads each closure member's own span. A missing
ancestor, a cycle or a symbol with several `extends` declarations refuses.

The subset holds the source header items, then the closure members. Each keeps
its exact source bytes, in source order, so request order cannot change the
output. The subset then goes through the unchanged `parse_library` and
`resolve_symbol`. A representation that `resolve_symbol` does not support (for
example, a derived symbol with replacement pins) refuses the capture.

Separate bounds apply. Callers may lower them but never raise them:

| Bound | Limit |
| --- | --- |
| Source bytes | 32 MiB |
| Source scan work | 8,000,000 nodes |
| Nesting depth | 128 levels |
| Closure size | 64 symbols |
| Closure source bytes | 4 MiB |
| Closure nodes | 1,000,000 |

## Receipt and binding

The `agent-ee.symbol-capture/1` receipt records the following:

- the implementation module and its source SHA-256;
- the library nickname and the source file name;
- the SHA-256 and byte length of the full source library;
- the SHA-256 of each header item;
- the selected names;
- for each captured symbol: its role (`selected` or `ancestor`), its parent,
  and the SHA-256, byte and node count of its raw source span;
- the subset path, SHA-256 and length;
- the limits applied;
- a `canonical_sha256` over the receipt.

`verify_capture(receipt, captured, source)` rederives the capture from source
bytes. It refuses any receipt, byte or source difference.

An intent names the subset by its own SHA-256, like any captured library.
`bind_resolved(receipt, resolved)` first validates a passing
`resolved-connectivity` record. It then requires exactly one library entry
with the subset's hash, nickname and file name. Every symbol source that uses
that library must lie within the capture. The `agent-ee.symbol-capture-binding/1`
result binds the resolved record hash to the source library hash and the raw
symbol hashes. The connectivity record and its identities are unchanged.

The receipt is informational capture provenance. It is not an owner-approved
electrical contract and it grants no part selection. Captured stock bytes keep
their KiCad library licence. They stay private until their publication is
authorized.

## Qualification

The tests use owned synthetic libraries:

- an inherited `extends` chain;
- escaped quotes, parentheses and `;` inside strings, and comments inside a
  symbol;
- malformed suffixes;
- cycles, missing parents, duplicate and ambiguous identities;
- each resource bound;
- a generated comparison against the shared reader.

An owned library is padded above the node limit. The whole library fails closed
with the numeric limit and this extraction route. Its capture resolves, binds
and rederives.

An owned over-limit regulator capture produces a sheet byte-identical to
emission from the original owned library. Native KiCad 10.0.7 ERC reports no
violations on that sheet, and its netlist matches the independent expectation.
A control that moves one library pin makes ERC report `lib_symbol_mismatch`,
which shows that ERC read the captured library.

The installed KiCad 10.0.7 library has SHA-256
`6f095b497a944f57015012ea26e0da0259957673919c15471d024330e9bbc249`. Tests read
it at run time and assert the following:

- the whole library fails closed at 2,000,000 nodes;
- the captured `STM32H723ZGTx` span equals an independent byte slice;
- repeated capture is byte-identical;
- all 144 physical pins resolve, with type counts and key pins taken from the
  raw source text, and bind to the source hash;
- a native forced re-save (`kicad-cli sym upgrade --force`) reproduces the
  subset byte for byte.

Strict mode (`AGENT_EE_REQUIRE_TOOLCHAIN=1`) fails rather than skips when that
library is missing.

Not yet qualified: native ERC of an emitter-generated sheet that embeds
`STM32H723ZGTx`. The symbol places 9 VSS pins on one connection point. The
current [schematic emitter](schematic-emitter.md) refuses any shared pin
endpoint (`impossible_pin_endpoint`). Every symbol in both over-limit stock
libraries has such stacked pins. Supporting them is an emitter change.
Generated FOME consumption also remains with the generator and block-library
owners.
