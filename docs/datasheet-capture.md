# Datasheet acquisition

`agent_ee.datasheet_capture` fetches explicit part-binding Datasheet URLs during
preparation, before candidate capture. Checks never call this module. It uses
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
There is no new schematic parser or inferred applicability. The current catalog
selection row has no dedicated datasheet URL field; selected-part/property and
approved-parts manifest integration remain work for their owning lanes.

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
