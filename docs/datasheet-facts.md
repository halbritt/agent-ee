# Captured datasheet facts

`datasheet_facts` checks textual support for authored, untrusted facts.
`params.facts` names a captured `agent-ee.datasheet-facts/1` JSON file with a
nonempty `facts` list. Each fact has a unique `id`, `part_number`, `kind`,
`value` (a string preserving the source spelling), `unit` (possibly empty),
`conditions`, and `source`. A `pin_function` also needs a `pin` string.
The source contains captured PDF `path`, lowercase SHA-256 `sha256`, physical
PDF `page` (one-based), human `locator`, and an exact `quote`.
See the [TJA1051 fact fixture](../fixtures/datasheet-pins/facts.json).

`params.extractor` declares `path: "/usr/bin/pdftotext"`, its installed binary
`sha256`, and the exact first line of `pdftotext -v` as `version`. Both extraction
and version probing run in the existing networkless Bubblewrap sandbox.
The binary hash is checked before and after extraction, and again by `verify`.
The fixture scopes pin Poppler 24.02.0 from the demonstrated environment; other
installations require an explicit new declaration and a fresh candidate.
System shared libraries are provided by the host sandbox, not individually
pinned by this adapter. Extractor outputs and invocation records are retained.

Verification compares the PDF hash to the captured manifest, finds the exact
quote on the cited physical page, and checks that the value occurs in the quote.
Pin-function facts additionally require the pin token in the quote. Whitespace
and case are significant; there is no OCR, normalization or model-generated
replacement text. `facts.json` in the check workspace records each fact's status
and reason. Incorrect hashes, absent values, altered quotes and wrong pages
produce specific rejection reasons. A blank/scanned page with no extractable
text is `unsupported`. An out-of-range page is rejected. Any unverified fact
makes the check `execution: unsupported`, `outcome: unknown`, preventing citation
failure from becoming an accepted electrical defect.

Citation verification proves text occurrence. It does **not** prove correct row
or conditions selection, unit interpretation, component identity, datasheet
applicability, or engineering correctness. Repeating a PDF's exact words cannot
establish that it applies to the fitted device.

`pin_map` requires preceding `params.facts_check` and `params.netlist_check`
checks of kinds `datasheet_facts` and `netlist`. `params.symbols` is a nonempty
list of engineer-supplied applicability records: `reference`, `part_number`,
and a nonempty `facts` list of fact IDs. This immutable check declaration owns
applicability; the authored facts file cannot select design components.
Only named pins are in scope. Each selected fact must be verified, have kind
`pin_function`, and match the engineer's declared part number.

The consumer reads the actual native XML component's `libsource` and its
`libparts/libpart/pins/pin` number/name metadata, including unconnected pins.
It compares names exactly. It does not infer names from reference prefixes,
net labels, symbol prose or a fixture-specific mapping. Missing prerequisites,
missing component/pin metadata or unverified facts yield unsupported coverage.
Complete comparisons pass or report a defect, with component reference, pin,
symbol locator, expected value and native name in `pin-map.json`.

For scope/2, the captured facts file and every PDF it references are compiled
into immutable baseline files. Baseline load re-derives those dependencies from
the captured facts, so campaign writers cannot change a PDF or fact to bless a
repair. Old check kinds retain their historical baseline file sets. There is
no silent scope migration; policy changes still require fresh evidence.
Coordinator sealing covers extraction and comparison artifacts before review.

The [fixture requirements](../fixtures/datasheet-pins/requirements.md) describe
its original symbol/board and narrow coverage. Both native control and swapped
fixtures pass ERC/DRC with schematic parity. The swapped symbol reverses CANL
and CANH and fails `pin_map`. This demonstrates a symbol definition defect that
native rule checks miss, not a measured CAN bus failure. No new third-party PDF
or design files are published by this change. Owner review remains separate
from these automated checks.

```sh
agent-ee review fixtures/datasheet-pins/swapped.scope.json --checks-only \
  --out build/swapped-pins
agent-ee verify build/swapped-pins
```

Checks-only execution exits 1 because independent review is unfinished. Inspect
`checks/pin-map.json` for the declared defect and the retained native reports
for the clean ERC/DRC results.

## Scope/3 coverage

The facts check verifies citations and is not defect-capable. It cannot alone
satisfy an acceptance requirement. A pin-map declaration can provide executable
coverage for its explicitly selected reference/pin comparisons, provided its
facts and netlist prerequisites precede it. Capture rejects missing or
inapplicable fact IDs, repeated pins, empty symbol selections and unpinned
extractors. Facts and all referenced PDFs remain immutable verification inputs.
This audit checks the declared contract; actual PDF extraction and native pin
comparison must still execute successfully.
