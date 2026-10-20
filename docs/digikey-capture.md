# DigiKey capture

`agent_ee.digikey_capture` is the owner-run producer of DigiKey Product
Information API v4 evidence for Stage 2 part preparation (AGENTEE-246, under
the AGENTEE-79 parts epic). Use it for external or hand-fitted parts that have
no LCSC code. It also gives a direct manufacturer datasheet URL where the
JLCPCB/LCSC link is missing, wrapped in HTML or for the wrong family, and it
records DigiKey's parametric ratings and seated height.

It follows the [JLCPCB stock capture](jlc-stock-capture.md) contract. It runs
on the host only, before candidate capture. It needs an explicit network
opt-in, keeps every raw response hashed and time-stamped, and publishes the
whole capture atomically. Checks never run it. The check sandbox has no network
and a cleared environment, so it cannot reach DigiKey or see credentials. The
module uses only the stdlib (`urllib`).

**Live capture needs owner-provided DigiKey API credentials and the explicit
`--allow-network` opt-in.** The repository holds no credentials. Its tests use
only an owned loopback stub with synthetic v4 responses. The response mapping
below is not yet qualified against a live DigiKey response. Two live shapes
reported by an owner-run attempt (AGENTEE-262: a zero-stock part without a
top-level `QuantityAvailable`, and a connector without `Package / Case`) are
modelled by owned synthetic regression fixtures; a passing live run is still
owner-gated.

```sh
export DIGIKEY_CLIENT_ID=...       # owner's Product Information v4 app
export DIGIKEY_CLIENT_SECRET=...
PYTHONPATH=src python3 -m agent_ee.digikey_capture capture --allow-network \
    776228-1 NEO-M9N --out build/digikey
PYTHONPATH=src python3 -m agent_ee.digikey_capture capture --allow-network \
    --secrets-file ~/.config/agent-ee/digikey.env --queries-file queries.json --out build/digikey
PYTHONPATH=src python3 -m agent_ee.digikey_capture validate --root build --capture digikey/digikey-capture.json
PYTHONPATH=src python3 -m agent_ee.digikey_capture project --root build \
    --capture digikey/digikey-capture.json --key 776228-1 --out build/j1-identity
```

## Request contract

- **Opt-in first.** Without `--allow-network` (`allow_network=True` in
  `capture_digikey`), the producer refuses before it validates anything else.
  It opens no socket, starts no child and creates no output.
- **Credentials.** The producer reads `DIGIKEY_CLIENT_ID` and
  `DIGIKEY_CLIENT_SECRET` from the host environment, or from `--secrets-file`.
  That file holds `KEY=VALUE` lines (with an optional `export ` prefix and
  quotes). It must be a regular file, not a link, owned by the current user,
  with no group or other permission bits. Missing or malformed credentials
  refuse before any socket or output. Error messages and the `Credentials`
  repr never show the values.
- **Token exchange.** The 2-legged `client_credentials` grant is a form POST to
  `<base>/v1/oauth2/token`. It runs before the output directory is created, so
  credentials the endpoint rejects leave no output. The token is renewed before
  it expires (30 s margin, or half its lifetime if shorter). A renewal that
  fails aborts the capture, and nothing is published. Each successful exchange
  is recorded as `fetched_at`, `http_status` and `expires_in` only.
- **Queries.** A query is `{key, kind, query}`. Positional numbers are
  `product` queries keyed by the number; `--queries-file` takes a JSON list.
  Keys match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and are unique. A capture
  takes 1 to 100 queries, and requests run in key order.
  - `product`: `GET <base>/products/v4/search/<query>/productdetails`.
  - `keyword`: `POST <base>/products/v4/search/keyword` with exactly
    `{"Keywords":"<query>","Limit":10,"Offset":0}`.

  Every lookup sends `Authorization: Bearer`, `X-DIGIKEY-Client-Id` and fixed
  locale headers (`en`, `USD`, `US`).
- **Secrets stay out of argv and records.** Each exchange runs in an owned child
  (`python -m agent_ee.digikey_capture --fetch`). The child gets the request,
  credentials included, on its stdin pipe only, through the shared
  `datasheet_capture.run_owned_child`. Its argv carries no secret. If a response
  contains the client id, client secret or access token, it is recorded as
  `failed`, and its bytes are not retained. Child error text is redacted. Before
  publishing, the producer scans the manifest bytes and refuses to write one
  that contains a secret.
- **Origin.** `--base-url` (default `https://api.digikey.com`) must be an HTTPS
  origin with no path, query, user info or fragment. Plain HTTP is admitted only
  for loopback hosts (owned test stubs). Redirects are refused, and ambient
  proxy settings are ignored.
- **Rate limit.** At least `--min-interval` seconds (default 1, minimum 0.5)
  pass from the end of one exchange to the start of the next, token exchanges
  included. A `Retry-After` header (for example on HTTP 429) widens the next gap
  to that many seconds, capped at 60. The limited request is recorded as
  `failed`, and the producer does not retry it.
- **Bounded.** Each exchange has a hard child deadline (`--timeout`, default
  30 s, maximum 60 s). The child is killed and reaped at the deadline.
  Responses are capped by `--max-bytes` (default and maximum 4 MiB).

## Capture layout

The output is a new directory, published by one rename after the last request.
An existing output is refused.

```
digikey-capture.json      agent-ee.digikey-capture/1
responses/<key>.json      raw response bytes, exactly as received
```

| Key | Meaning |
|---|---|
| `schema` | `agent-ee.digikey-capture/1` |
| `as_of` / `completed_at` | UTC timestamps taken before the first exchange and after the last. |
| `source` | `base_url`, the three API paths, the `keyword_body` template, `locale`, `parser` (`agent-ee.digikey-product-v4/2`; a `/1` capture, which lacks `package_source`, is refused) and `min_interval_seconds`. |
| `token_exchanges` | One record per successful exchange: `fetched_at`, `http_status` and `expires_in`. No token is recorded. |
| `requests` | One record per query: `key`, `kind`, `query`, `fetched_at` (UTC), `status` (`found`, `not_found` or `failed`), `http_status`, `response` (`path`, `sha256`, `size_bytes`, or `null`) and `reason` (only when `failed`). |
| `parts` | The derived record for every `found` key, with `last_seen` equal to its `fetched_at`. |

A transport error, deadline, redirect, non-200 status (404 included),
over-limit body, credential echo or unrecognised shape makes a request
`failed`. The capture keeps every response body it received, failures
included. A failure is never dropped and never turned into a part.

## Response parser

`parse_response(raw, kind, query)` accepts strict JSON (duplicate keys and
`NaN` are refused).

- A `product` response needs a `Product` whose `ManufacturerProductNumber` or a
  `ProductVariations[].DigiKeyProductNumber` equals the query. The comparison
  is case-insensitive. Any other product is refused as wrong-part.
- A `keyword` response needs `Products`. It reads `Products` and
  `ExactMatches`, and counts only rows with an exactly matching number. No
  match is `not_found`. Several distinct matching products are refused as
  ambiguous.

Each derived record holds these fields:

| Field | Source |
|---|---|
| `mpn` | `ManufacturerProductNumber` |
| `manufacturer` | `Manufacturer.Name` |
| `digikey_part_number` | The queried variation's number, else the variation with the lowest `MinimumOrderQuantity`, ties broken by number. `null` without variations. |
| `package` | The first rule that applies, from exact parameters only: `Package / Case`; else `Supplier Device Package`; else, for a connector, the verbatim `name: value` pairs of `Connector Type`, `Number of Positions`, `Number of Rows` (only if present), `Pitch - Mating` and `Mounting Type`, joined by `; `. The four connector parameters other than rows are all required. Otherwise `null` (unknown). Values `-` and empty count as absent. |
| `package_source` | `{rule, parameters}` naming the rule (`package_case`, `supplier_device_package` or `connector_parameters`) and the exact parameter names quoted; `null` when `package` is `null`. |
| `primary_datasheet_url` | `DatasheetUrl`. A protocol-relative `//` link becomes `https:`, and the URL must pass `datasheet_capture.validate_url`. Empty means `null`. |
| `quantity_available` | `QuantityAvailable`, a non-negative integer. This is historical stock at `last_seen`. DigiKey omits the key on some zero-stock parts; an omitted or `null` value is recorded as `null` (unknown stock), never as zero and never summed from `ProductVariations`. Any other non-integer, negative or boolean value is refused. |
| `unit_price` | `UnitPrice`, a finite non-negative number or `null`. |
| `product_status` | `ProductStatus.Status` |
| `seated_height` | Parameter `Height - Seated (Max)` |
| `operating_temperature` | Parameter `Operating Temperature` |
| `parameters` | Every `{ParameterText, ValueText}` as `{parameter, value}`, in response order. |

A named parameter with two different values is refused. The exception is a
package fallback parameter (`Supplier Device Package` or a connector
parameter): a conflict there leaves `package` unknown instead, so a response
that the `/1` parser accepted with a `null` package is not newly refused. A
`Package / Case` conflict still refuses. The parser reads no other field and
infers no value.

## Offline validation

`validate_capture(doc, read_response, expected=None, allow_failed=False)` and
`load_capture(root, path)` run without network. `load_capture` reads the
manifest and responses under `root` with the shared no-link
`parts_manifest.read_captured` and a strict JSON parser, and returns
`(doc, summary, dependencies)`.

1. The manifest, `source`, token records, requests and responses must have
   exactly the documented keys and the producer's paths, body, locale and
   parser. The base URL must pass the same origin rule as the producer.
2. Timestamps must be UTC (`+00:00`). Token and request times must fall within
   `as_of..completed_at`, and request times must be in order.
3. Every retained response must match its `size_bytes` and SHA-256. Re-parsing
   it must reproduce the recorded status. Re-deriving every found record must
   reproduce `parts` byte for byte in canonical JSON, so `1`, `1.0` and `true`
   differ.
4. A `failed` request refuses the capture unless the caller passes
   `allow_failed=True`. Only the producer's own check before publishing passes
   it. `expected`, when given, must equal the recorded queries exactly, so a
   dropped or unrequested item refuses. A part in `parts` without a found
   request also refuses.

The result has `requested`, `found`, `not_found`, `failed` and per-response
`response_verdicts` (`verified`, `mismatch` or `missing`).

## Stage 2 projection

`project_external(root, path, key)` is a deterministic, offline adapter. It
writes nothing, and it refuses a capture that does not validate, a key with no
found record, or a record whose `package` is unknown (`null`). It returns:

- `identity`: an `agent-ee.external-part-identity/1` declaration (`mpn`,
  `manufacturer`, `package`, `evidence_refs`). The `evidence_refs` cite the
  manifest (`parts.<key>`) and the retained response, both pinned by SHA-256.
  Write it as a file and pass it, with locator `/`, to
  `part_record.prepare_external_record`. `verify_external_identity` replays a
  declaration against the capture it cites and refuses any change.
- `package_provenance`: the capture and response (pinned by SHA-256), the key,
  the locator (`Product.Parameters` or the keyword row's `Parameters`) and the
  record's `package_source` `rule` and `parameters`. A connector package from
  `connector_parameters` is therefore traceable to the exact quoted parameters.
- `datasheet_binding`: `{id: <key>, datasheet_url}`, a binding for
  [datasheet acquisition](datasheet-capture.md)
  (`capture_datasheets([binding], out)`). A `null` URL stays a missing
  acquisition. `datasheet_provenance` names the capture, response and field the
  URL came from.
- `hand_fit_evidence`: a `purchase_source` text (DigiKey part number,
  manufacturer, MPN, capture time, historical quantity and price; unknown stock
  reads `quantity_available=unknown (QuantityAvailable not reported)`) and
  `evidence_refs` (`{path, locator}`) for an assembly deviation in
  `agent-ee.part-deviations/1`. The owner still declares `requirement_id`,
  `reason`, `assembly`, `stock_deviation` and any `policy`. The adapter decides
  none of them. [Assembly readiness](assembly-readiness.md) then reports the
  deviation as usual.
- `ratings`: `seated_height` and `operating_temperature`, as source text with
  their capture provenance. Nothing here judges suitability.
- `dependencies`: every read capture path and its SHA-256.

`project --out DIR` writes `external-identity.json`, `datasheet-bindings.json`
(input for `python -m agent_ee.datasheet_capture`) and `projection.json` into a
new directory.

## Limits

- The response mapping is written against owned synthetic v4 documents. It is
  not qualified against a live DigiKey response. If the live shape differs, the
  affected requests are `failed`, and consumers refuse the capture.
- DigiKey stock and price are historical observations. They are not JLCPCB
  assembly stock, and `assembly_readiness` does not treat them as stock.
- Live use needs owner-provided credentials and the explicit network opt-in.
  No credential or capture is stored in the repository.
