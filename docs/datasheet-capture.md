# Datasheet acquisition

`agent_ee.datasheet_capture` fetches explicit part-binding Datasheet URLs during
preparation, before candidate capture. Checks never invoke acquisition. It uses
stdlib urllib, with no new dependency, scraper, model inference or network-enabled
check sandbox. Retained URLs are source claims, not authentication of a vendor,
part identity, document applicability or engineering correctness.

The input is a JSON list of unique part IDs and URLs (null or empty means missing):

```json
[
  {"id": "U1", "datasheet_url": "https://example.org/device.pdf"},
  {"id": "U2", "datasheet_url": null}
]
```

```sh
PYTHONPATH=src python3 -m agent_ee.datasheet_capture bindings.json --out build/datasheets
```

The output parent must already exist. The destination must be new, with no
symlink or traversal in its parent. IDs and remote filenames never become local
paths. Each unique exact URL is fetched once, including failures. Each successful
file is named by the SHA-256 of its URL plus `.pdf`; its bytes have a separate
SHA-256. Different URLs remain separate even when their content matches.
The manifest records every part's ID, original URL, observed final redirect URL,
UTC fetch-attempt timestamp, content hash, relative path, missing flag and failure
reason. A missing URL has a null timestamp; an attempted URL has a timestamp. A deadline before a
response is retained has a null final URL. Failed downloads have no file or hash.

HTTP and HTTPS are allowed, including local HTTP for owned test fixtures.
Requests identify the client as `agent-ee/1 (public datasheet acquisition)`.
This is an explicit client identity, not browser impersonation. Ambient proxies
remain disabled; source refusal remains missing coverage.

Credentials, fragments, control characters, unsupported schemes and invalid ports
are rejected; redirect targets receive the same validation. Ambient proxy
settings are ignored. This is an owner-invoked downloader: HTTP(S) may reach
private addresses, so do not feed it untrusted URL lists without reviewing them.
Downloads must start with `%PDF-` and end with `%%EOF` after whitespace removal.
This rejects HTML and truncated envelopes; actual PDF parsing and citation support
remain obligations of the existing extractor. Envelope acceptance alone does not
establish a well-formed or safe PDF.

Each URL runs in an owned capture subprocess, with a hard elapsed-time deadline
(default 30 seconds, maximum 60), including startup, DNS, redirects and reads.
Timeout and catchable cancellation terminate and reap the owned child before
returning or propagating the original exception. Cleanup targets only that child,
rechecking its process start time and working directory; an already-exited child
is reaped without signalling. The child also uses the socket timeout. Responses are limited to 20 MiB by default;
`--max-bytes` can lower that limit. At most 256 bindings are accepted. The parent
publishes PDFs and `manifest.json` together after processing all bindings.
An interrupted or failed write never publishes a completed manifest: ordinary
exceptions remove owned staging output; a hard process death may leave an empty
reserved destination and hidden `.datasheet-*` staging directory for manual
inspection. Existing output is never reused or overwritten. URL failures publish
explicit missing records alongside successful PDFs. CLI exit 0 means every PDF
was captured; exit 2 means missing coverage or invalid input. Neither is acceptance.

For native inventory alignment, `bindings_from_native(native_xml, properties)`
uses the existing full `read_native_inventory` API. `properties` maps native
references to explicit `{"Datasheet": "https://..."}` declarations; absent
references are retained as missing and unknown references are refused.
`--native-inventory design.xml` selects this input format in the module CLI.
There is no new schematic parser or inferred applicability. The current catalog has no dedicated URL column; the opt-in preparation adapters
below preserve exact raw `Datasheet` metadata. Approved-part authority remains
work for its owning lane.

To consume the output, include the directory in ordinary scope `inputs`, then
point each authored fact's `source.path` and `source.sha256` to the captured PDF
under that input mount. Keep the part applicability declaration in `pin_map`
parameters. Normal [datasheet facts](datasheet-facts.md) extraction, candidate
hashing, immutable scope/2 baseline dependencies and coordinator sealing apply.
A missing PDF cannot verify a fact or pass `pin_map`; scope/2 or scope/3 may refuse
capture when its declared immutable PDF dependency is absent. The acquisition
manifest does not supply facts or substitute for those checks.

The focused tests serve original minimal PDFs over local HTTP, deduplicate shared
URLs, follow and reject redirects, reject HTML/oversize/truncated responses, bound
slow responses, refuse unsafe/existing outputs, and inject a publication failure.
Real-child lifecycle regressions cover cancellation, identity-lookup interruption,
pipe-read failure, an already-exited child, successful retry and timeout reaping.
An actual native netlist and Poppler citation check consume the captured original
PDF after the HTTP server shuts down, through checks-only review and public
verification. Deleting that PDF makes facts and pin-map coverage unknown. No
third-party PDFs are added; real TJA1051 Internet acquisition remains unqualified.

## Selection preparation seam

The pure adapters in [datasheet_parts.py](../src/agent_ee/datasheet_parts.py)
normalize provenance into `agent-ee.datasheet-bindings/1`. They grant no approval:

- `bindings_from_selection(snapshot_directory, manifest_sha256, requirements,
  selection, references)` reruns canonical catalog/1 selection from captured
  bytes and rejects any changed chosen row or selection record. `references`
  maps every requirement ID to an explicit list of native references with exactly
  its per-board multiplicity. Duplicate references refuse. The only URL metadata
  key is the exact raw attribute `Datasheet`; missing winners/attributes retain
  null URLs. MPN comes only from the chosen row. The qualifier is `selection-only`.
- `bindings_from_rung1(manifest, source_root)` consumes the committed
  `37d96d50979f2e8590db76af888d0915feb7fef2` experimental shape. It validates
  catalog bytes, full chosen row/part identity, reference sets, selection IDs and
  assembly exclusions. Only the relative catalog path under `source_root` is
  read; absolute provenance and footprint paths confer no read authority.
  The complete original manifest and its canonical identity are retained.
  The qualifier remains `experimental-unqualified`. This CSV has no Datasheet
  or MPN column, so every reference, including the hand-soldered header, remains
  a missing obligation. BOM usage quantity is not stock availability.

`prepare_datasheets(bindings, required_facts, out)` is the single preparation
callable. `required_facts` maps **every** reference to a nonempty list of distinct
fact IDs. It invokes the existing downloader, then publishes a new directory
containing `bindings.json`, `acquisition/manifest.json`, successful PDFs and an
empty [facts/2 envelope](datasheet-facts.md#per-part-missing-coverage).
It returns `outcome`, `qualification`, source paths and ordinary scope `inputs`
fragments. Add those fragments without changing their `at` paths: envelope
locators are candidate-root-relative. Missing records return `outcome: missing`;
`captured` means acquisition completed, not that facts or approval exist.
Normal exceptions/cancellation remove preparation staging; an existing output
is refused. No campaign or frozen baseline is changed.

`bindings_from_native(native_xml)` without a second argument extracts actual
Datasheet values through the shared XML reader. The legacy two-argument join
remains an explicit caller declaration, not authenticated native extraction.
`validate_native_bindings(native_xml, bindings)` requires identical full reference
sets and exact Datasheet text, without filtering population, board or BOM flags.
Absent and empty values remain distinct. Actual KiCad 10.0.7 native tests qualify
`fields/field[@name='Datasheet']`; dedicated elements and custom properties have
parser-boundary controls, including contradiction/duplicate/structured rejection.
Emitter URL propagation is covered under [Generation path](#generation-path).

## Generation path

The rung-1 driver ([generate.md](generate.md#parts-rule)) runs
`capture_datasheets` in stage 2 on every selected part's `Datasheet` and
publishes `stages/2-parts/datasheets/` as a stage output; the receipt lists
captured URLs with sha256 and fetch time, and every missing part. The
[schematic emitter](schematic-emit.md) copies a non-null manifest `Datasheet`
onto the symbol, so KiCad's native netlist carries the selected URL and
`bindings_from_native(design.xml)` reproduces it. A focused test runs that chain
from stage-2 part records through the emitter, a sandboxed `kicad-cli` netlist
export and capture of an owned PDF over loopback HTTP: two parts sharing one URL
cause one fetch, and the header without a URL stays missing. The fome-mc BOM has
no datasheet column, so an actual rung-1 run captures nothing and records every
part as missing.

The future approved-binding contract (AGENTEE-86) belongs to its owning lane.
The preparation caller must retain the original catalog/requirements and
approval inputs, map every obligation into immutable baseline requirements, and
request a fresh capture/review after a declaration changes. Neither adapter
upgrades selection into owner approval. The rung-1 scope declares no
`datasheet_facts` check, so the stage-2 capture is not yet mounted into its
review inputs.

## Offline historical import (AGENTEE-306)

Retained bytes with incomplete redirect history use `agent-ee.datasheet-capture/2`.
Version 1 remains the live-capture contract, including its required successful
final URL and fetch timestamp. Version 2 contains only successful historical
imports, with outcome `imported`; mixing live and imported records is refused.
This separate version prevents a missing final URL from becoming valid live evidence.

```sh
PYTHONPATH=src python3 -m agent_ee.datasheet_capture --import-historical \
  --pdf retained.pdf --source-root retained-sources \
  --source-note sources.json --source-note-sha256 "$NOTE_SHA256" \
  --entry 0 --id J1 --out build/historical-acquisition
```

The callable is `import_historical_pdf(pdf, source_root, source_note, part_id, out)`.
`source_note` is `{path, sha256, entry}`: a contained relative JSON path, its
externally supplied SHA-256, and a zero-based entry index. The note is a JSON
list. The selected entry must contain exactly `file`, `sha256`, `url` and
`captured_at_utc`, with optional `fetched_at`. The PDF hash in that entry must
match the retained bytes; `file` is a relative historical locator, not a fetch
instruction. Both recorded times require explicit timezones. The importer checks
the 20 MiB byte limit and `%PDF-`/`%%EOF` framing, performs no network operation,
and publishes the PDF, exact source-note bytes and manifest together to a new
directory. It never edits the retained originals.

Each imported record carries `acquisition_method: historical_import`,
`redirect_provenance: unknown`, `final_url: null`, `captured_at` copied verbatim
from `captured_at_utc`, and `fetched_at` copied only when recorded (otherwise
null). Its `source_note` pointer is relative to the acquisition manifest and
pins both the complete note and selected entry. No import timestamp replaces a
historical capture or fetch time. Invented redirects, altered notes or PDFs,
unbound hashes, and claims that an import was live are refused on replay.

`load_acquisition` replays both versions offline and includes the note in the
immutable dependency closure. It reads each PDF once and checks the `%PDF-`/`%%EOF`
envelope on exactly the bytes it hashed (AGENTEE-454). `datasheet_facts` retains the full acquisition
records in both its report and check details, so citation receipts expose the
import and unknown redirect provenance. Part records and source selections pin
the versioned acquisition manifest and record ID. Textual citation checks may
verify imported PDF text; they do not verify redirect history. Any policy
requiring redirect provenance must treat `unknown` as unknown coverage, never
as a pass. Release acceptance remains the owner policy question AGENTEE-271.
The original-fixture regressions are in `tests/test_datasheet_historical.py`.

### Consumer receipt provenance (AGENTEE-311)

The shared acquisition loader preserves historical-import records and adds
`acquisition_method: "live"` to replayed `/1` records. Live records report
`redirect_provenance: "observed"` when a final URL was retained, otherwise
`"unknown"`. These fields describe acquisition only; they add no acceptance
predicate or verdict authority. The on-disk `/1` manifest format stays unchanged.

Facts, pin-map and pin-contract reports and check details carry an `acquisitions`
map keyed by acquisition part ID. Capture-time capability descriptors for these
checks retain that map when a facts/2 obligation envelope is present. Facts/1
has no acquisition manifest to replay and its consumers carry an empty map.

New named and external part records carry the replayed record under `acquisition`;
qualification compares it against the pinned manifest. Older records without
this optional field remain readable. Datasheet source-selection return receipts
carry `acquisition`, and binding-fill component receipts carry it alongside the
projected fields. The structured native `PARAM_Datasheet` binding keeps its
existing schema; provenance belongs to the receipt. Historical receipts preserve
`historical_import`, unknown redirects, null final URL, recorded timestamps and
the pinned source-note reference. Source-note paths remain relative to the
acquisition manifest directory.

A cascade is a live-resolution receipt: replay requires a
`agent-ee.datasheet-capture/1` acquisition, even when a historical `/2` import of
the same PDF and URL has been fully repinned into the cascade and selection.
Historical imports remain usable through the ordinary offline consumer paths.
Malformed source-note `file` values raise `ValueError` before path handling;
the import CLI reports exit 2 without publishing output or printing a traceback.
