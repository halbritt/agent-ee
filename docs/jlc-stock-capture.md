# JLCPCB stock capture

`agent_ee.jlc_stock_capture` is the owner-run producer of JLCPCB stock
evidence. It implements the owner decision of 2026-10-18 ("JLCPCB public search
endpoint, scraped per LCSC id"; see [decisions](decisions.md)). It sends one
request per LCSC id to the public JLCPCB parts-search endpoint. It keeps each
raw response, hashed and time-stamped, and publishes an `agent-ee.jlc-catalog/1`
capture. [`assembly_readiness`](assembly-readiness.md) consumes that capture
offline.

Like [datasheet acquisition](datasheet-capture.md), it is a host-side
preparation step that runs before candidate capture. Checks never invoke it,
and the check sandbox keeps its no-network rule. It uses only the stdlib
(`urllib`). It sends no credentials and does not crawl in bulk.

```sh
PYTHONPATH=src python3 -m agent_ee.jlc_stock_capture --allow-network \
    --netlist design.xml --out build/jlc-stock
PYTHONPATH=src python3 -m agent_ee.jlc_stock_capture --allow-network C25804 C17024 --out build/jlc-stock
```

## Request contract

- **Opt-in.** Without `--allow-network` (`allow_network=True` in
  `capture_jlc_stock`), it refuses before validating anything else, makes no
  request and creates no output.
- **Ids.** There are three input forms: explicit ids, `--ids-file` (a JSON
  list) or `--netlist` (every populated LCSC id of a native KiCad XML netlist,
  read with the `assembly_readiness` netlist reader). Ids must match
  `^C[1-9][0-9]*$` and be unique. A capture takes 1 to 256 ids. Requests run
  in numeric id order.
- **One request per id.** Each request is a `POST` to the endpoint (default
  `https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`,
  `--endpoint` to override). The body is exactly
  `{"currentPage":1,"keyword":"<id>","pageSize":25}`, and the capture records
  that template.
- **Rate limit.** The producer waits `--min-interval` seconds (default 5,
  minimum 1) from the end of one exchange to the start of the next request.
  The endpoint therefore sees at least that gap between requests.
- **No credentials.** Endpoint URLs with user info, fragments, control
  characters or non-HTTP(S) schemes are refused (the shared
  `datasheet_capture.validate_url`). There is no cookie or auth handler.
  Ambient proxy settings are ignored, and redirects are refused.
- **Bounded.** Each request runs in an owned child process with a hard deadline
  (`--timeout`, default 30 s, maximum 60 s). The child is reaped through the
  shared `datasheet_capture.run_owned_child`. Responses are capped by
  `--max-bytes` (default and maximum 2 MiB).

## Capture layout

The output is a new directory, published by one rename after the last request.
An existing output is refused:

```
catalog.json          agent-ee.jlc-catalog/1
responses/C25804.json raw response bytes, exactly as received
```

`catalog.json` has exactly these keys:

| Key | Meaning |
|---|---|
| `as_of` | UTC timestamp taken before the first request; the capture is as old as this. |
| `completed_at` | UTC timestamp taken after the last request. |
| `stale_before` | Always `null`. The consuming run declares staleness. |
| `source` | `endpoint`, `method` (`POST`), `request_body` template, `parser` (`agent-ee.jlc-search-response/1`), `min_interval_seconds`. |
| `requests` | One record per requested id: `lcsc`, `fetched_at` (UTC), `status`, `http_status`, `response` (`path`, `sha256`, `size_bytes`, or `null` if nothing was received), `reason` (only on `failed`). |
| `parts` | The parsed observation for every `found` id, with `last_seen` equal to its `fetched_at`. |

`status` takes one of three values:

- `found`: exactly one row with an identical `componentCode`.
- `not_found`: a well-formed response with no such row.
- `failed`: any other result. That includes a transport error, the deadline,
  a redirect, a non-200 status, an over-limit body or an unrecognised shape.
  The capture keeps every response body it received, failures included. A
  failure is recorded, never dropped and never turned into a part.

## Response parser

`parse_search_response(raw, lcsc)` accepts strict JSON with `code: 200` and a
`data.componentPageInfo.list` (where `null` means empty). It maps one exact row
as follows:

- `stockCount` becomes `stock`, which must be a non-negative integer. This is
  JLCPCB stock. No LCSC stock figure is read (owner decision).
- `componentLibraryType` sets `library_tier`: `base` maps to Basic, and
  `expand` maps to Extended, or to Preferred when `preferredComponentFlag` is
  true.
- `componentBrandEn` becomes `mfr`, `componentModelEn` becomes `mpn`,
  `componentSpecificationEn` becomes `package` and `firstSortName` becomes
  `category`.
- The lowest-quantity price in `componentPrices` becomes `price1`.

The parser refuses any other shape: duplicate keys, `NaN`, ambiguous duplicate
rows, a missing or boolean stock, or an unknown library type. That refusal
makes the request `failed`.

The field mapping is written against owned synthetic responses. **It is not yet
qualified against a live JLCPCB response.** The live path is exercised only by
an owner-run capture. If the live shape differs, the affected requests become
`failed` and the check refuses the capture (see below). Nothing guesses a
value.

## Consumption by `assembly_readiness`

To use a capture, include the capture directory in the scope `inputs` and
point `params.catalog` at its `catalog.json`. The capture's `responses/*.json`
files become immutable baseline inputs, next to the catalog. The check then
applies the following rules:

1. **Re-derivation.** Every retained response must exist under the captured
   inputs and match its size and SHA-256. Re-parsing the bytes must reproduce
   each recorded status and the `parts` object exactly. A forged summary or
   altered bytes give `execution="failed"`.
2. **Bound to the run.** `params.as_of` is required, and it is the run's
   evaluation instant. The capture's own `as_of` is never used as the
   evaluation date. A staleness must also be declared, through
   `max_age_hours`, `max_stock_age_days` or `stale_before`. Missing either one
   gives `execution="unsupported"`.
3. **Staleness.** The check refuses a capture whose `as_of` is after the run
   `as_of`, or older than the declared staleness (`execution="failed"`). The
   per-part `last_seen` rules then apply as for any catalog.
4. **Coverage.** Each populated LCSC id needs an observation. An id that was
   never requested, or whose request `failed`, refuses the capture
   (`execution="failed"`). The message names the ids and the failure reasons.
   Only an observed `not_found` is evidence of absence, and it gives the usual
   `unknown_lcsc_in_catalog` defect.

The report records `catalog.capture` (`as_of`, `completed_at`, `requested`,
`not_found`, `failed`). The producer's stdout prints the catalog path, its
SHA-256 (for `catalog_sha256`) and the capture `as_of`.

A looser `agent-ee.jlc-catalog/1` document without a `requests` list keeps the
generic JSON-snapshot rules in [assembly readiness](assembly-readiness.md).

## Tests

`tests/test_jlc_stock_capture.py` runs the producer against a loopback stub
endpoint that serves original synthetic responses. The tests cover:

- one request per id, with the exact body and no credential headers;
- an observed gap of at least the minimum interval;
- raw bytes retained with their hashes;
- found, not-found, HTTP-error and unrecognised responses;
- a refused redirect and a deadline;
- the opt-in and limit refusals, made before any request;
- the CLI exit status;
- parser refusals;
- tamper refusal in `validate_capture`.

`tests/test_assembly_readiness.py` consumes producer captures through native
`kicad-cli` netlists. It checks:

- a pass, an insufficient-stock defect and a not-found defect;
- refusals: no run `as_of`, no staleness, a stale or future capture, an
  unrequested or failed id, altered bytes, a forged summary and a missing
  response;
- the response files as baseline inputs through `capture` and `run_checks`.

No test contacts JLCPCB.
