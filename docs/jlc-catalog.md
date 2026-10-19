# JLC catalog schema (`agent-ee.jlc-catalog/1`)

This is the offline evidence format emitted by the host-side
[JLCPCB stock producer](jlc-stock-capture.md) and consumed by
[`assembly_readiness`](assembly-readiness.md). It records JLCPCB assembly
stock from its public component search, per exact LCSC id. LCSC warehouse
stock, including `wmsc.lcsc.com` and `stockNumber`, is not JLCPCB assembly
stock.

The JSON must have exactly the keys below. Duplicate keys and non-JSON numbers
are refused. Unknown keys at the capture, source, request, response or part
level are refused. All timestamps are ISO-8601 UTC strings ending in `+00:00`.

## Capture object

| Key | Contract |
|---|---|
| `schema` | Exactly `agent-ee.jlc-catalog/1`. |
| `as_of` | UTC time before the first request; the conservative start of the capture. |
| `completed_at` | UTC time after the last request, at or after `as_of`. |
| `stale_before` | Exactly `null`; evaluation policy belongs to the run. |
| `source` | Producer identity and request contract below. |
| `requests` | 1–256 unique LCSC request records, including failed exchanges. Required. |
| `parts` | Object keyed by LCSC id, containing exactly the parts re-derived from `found` responses. |

`source` has exactly these keys:

| Key | Contract |
|---|---|
| `endpoint` | Source URL. For assembly stock evidence, exactly `https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`. The producer's diagnostic `--endpoint` override does not authorize another source as JLCPCB evidence. |
| `method` | Exactly `POST`. |
| `request_body` | Exactly the canonical JSON template `{"currentPage":1,"keyword":"{lcsc}","pageSize":25}` as a string. Replace `{lcsc}` with the request's id to reproduce the body. |
| `parser` | Producer/parser identity: exactly `agent-ee.jlc-search-response/1`, implemented by `agent_ee.jlc_stock_capture`. |
| `min_interval_seconds` | Finite number at least 1, declaring the producer's gap between exchanges. |

The schema and parser identifier define the producer contract. The producer
sends `agent-ee-jlc-stock-capture/1 (owner-run, one request per LCSC id)` as its
HTTP User-Agent; no credentials are captured. Hashes establish byte integrity
and reproducibility, not remote-server authentication or proof of when a server
sent those bytes. The response parser remains unqualified against live JLCPCB
responses until an owner-run capture establishes that mapping.

## Request and response objects

Each request has exactly these keys:

| Key | Contract |
|---|---|
| `lcsc` | Unique string matching `^C[1-9][0-9]*$`. |
| `fetched_at` | Observation time (`checked_at`), within `as_of..completed_at`. |
| `status` | `found`, `not_found` or `failed`. |
| `http_status` | Integer or `null`. `found` and `not_found` require 200. |
| `response` | Retained response descriptor below, or `null` only for `failed`. |
| `reason` | String for `failed`, otherwise `null`. |

A response descriptor has exactly `path`, `sha256` and `size_bytes`:

- `path` is exactly `responses/<lcsc>.json`, relative to `catalog.json`.
- `sha256` is the lowercase 64-digit SHA-256 of the raw response bytes
  (`response_sha256`).
- `size_bytes` is an integer from 0 through 2097152, excluding booleans.

Every retained response must be a captured immutable baseline input. The
consumer checks its size and hash before parsing. Each checked response has a
`response_verdicts[lcsc]` entry with `path`, `sha256` and `verdict`:
`verified`, `mismatch`, `missing` or `unverified`. A verification failure
produces `execution="failed", outcome="unknown"`, with the available hash
verdicts retained in `assembly-readiness.json`. The entire capture is refused;
no part from that capture can yield a stock pass. `verified` alone means the
bytes match, not that their status, fields, source or freshness passed.
For a failed request with no retained bytes, `path` and `sha256` are null
and the verdict is `missing`. Symlinks in any response path are refused.

## Part objects

A found part has exactly `lcsc`, `stock`, `library_tier`, `mfr`, `mpn`,
`package`, `category`, `price1` and `last_seen`. Its key and `lcsc` agree.
`last_seen` is the request's `fetched_at`. The other fields must equal the
shared response parser's output exactly:

| Part field | Verified response field |
|---|---|
| `stock` | Non-negative integer `stockCount`; never `stockNumber`. |
| `library_tier` | `componentLibraryType`: `base` → Basic; `expand` → Preferred if `preferredComponentFlag` is true, otherwise Extended. |
| `mfr` / `mpn` | `componentBrandEn` / `componentModelEn`, trimmed strings, empty when absent or null. |
| `package` / `category` | `componentSpecificationEn` / `firstSortName`, same string rules. |
| `price1` | Price at the lowest `startNumber` in `componentPrices`, or null. |

The response envelope requires `code: 200` and
`data.componentPageInfo.list` (null is empty). Exactly one row must match
`componentCode == lcsc` for `found`; none means `not_found`; ambiguous rows,
missing stock, unknown library types and malformed responses fail. Extra
vendor response fields are allowed, but only the fields above are read. The
summary cannot override the verified bytes. A response row's `dataManualUrl`
is not a part key; it is read from the verified bytes only by the
[datasheet URL cross-check](catalog-identity.md#captured-jlcpcb-datasheet-urls). `source_url`, `request_body`,
`checked_at` and `response_sha256` are represented by the source/request
fields above, not accepted as additional part keys.

## Evaluation time and compatibility

Campaign checks derive `as_of` from the completion of the latest anchored
`scaffold` or `schematic` stage receipt that writes the captured schematic.
The shared receipt validator revalidates the retained execution, status and
hashes; the schematic output must match the candidate. Pre-campaign receipts
use the existing `pre_receipts` mechanism. A missing receipt, changed anchor,
unadoptable receipt or changed schematic refuses the stock evidence. A later
placement or fab receipt does not reset the stock clock.

The coordinator passes this receipt context through review and check execution;
campaign replay recomputes its selection. The check seals
`stock-evaluation.json` with the context, evaluation instant and a declared
`max_age_hours: 24` (a tighter explicit limit is preserved). It overrides a
caller's `as_of`; a caller cannot backdate a run or relax the 24-hour limit.
Standalone checks use the coordinator's review start time, or the current UTC
instant when invoking `run_checks` directly. Direct Python calls to
`evaluate_assembly_readiness` remain an explicit evaluation API requiring
`as_of` and staleness for JLC captures; they do not assert a campaign binding.

A capture more than 24 hours old at evaluation is refused as stale/unknown.
A future capture is refused. A populated id that was never requested or whose
request failed is unknown evidence, never proof of absence. Only a verified
`not_found` observation supports an unknown-LCSC defect.

A `jlc-catalog/1` file without `requests` is invalid; there is no fallback to
legacy snapshot rules. Legacy offline catalogs remain explicitly named
`agent-ee.catalog-snapshot/1` or `agent-ee.catalog/1`. They do not assert the
JLC response-verification contract. Unknown part keys are refused in JSON
snapshots too, so provenance fields cannot silently ride along unverified.
