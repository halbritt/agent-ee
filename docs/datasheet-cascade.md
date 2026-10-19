# Datasheet source cascade

AGENTEE-248 adds a host-side producer, `agent_ee.datasheet_cascade`. It
resolves datasheet URLs for declared Stage 2 parts across an ordered cascade,
then acquires candidates through [datasheet acquisition](datasheet-capture.md).
It records the outcome as a complete
[`agent-ee.datasheet-source-selection/1`](binding-fill.md#captured-datasheet-source-selection)
decision before `part_record` and `binding_fill` run.

It is preparation only. Checks never run it, and no check or offline module
imports it. Exa search never runs inside a check sandbox.

## Cascade order

For each part key, candidates are tried in this order:

| Tier | Source | URL field |
|---|---|---|
| `digikey` | a found part in a replayed [DigiKey capture](digikey-capture.md) | `DatasheetUrl` (`primary_datasheet_url`) |
| `jlc` | the exact row in a replayed [JLCPCB stock capture](jlc-stock-capture.md) | `dataManualUrl` |
| `catalog` | the pinned [catalog identity index](catalog-identity.md) row | `datasheet_url` |
| `exa` | one local `exa-search` query, only with `--allow-network` | result `url`, in rank order |

Tiers 1–2 are re-derived offline from their retained bytes. A declared source
that yields no URL is recorded as an `unavailable` tier with its reason. This
covers a failed or not-found request, a missing URL field and an LCSC id absent
from the index. A tier is never skipped silently. A tampered or invalid capture
is refused before any output.

Each candidate URL is fetched once, in order, through `datasheet_capture`'s
owned, deadline-bounded fetch child. A candidate advances the cascade when:

- it **fails**: there is no complete `%PDF-…%%EOF` envelope, for example an HTML
  bot challenge, a 404, a redirect to an invalid URL or a timeout; or
- it is **rejected**: its PDF SHA-256 matches a `rejected` hash for the same key
  in a declared prior source-selection file, for example a wrong-family catalog.

The first remaining PDF is selected. Later candidates are recorded as
`not_attempted`. A part with no acceptable PDF is `unresolved`. Its selection
entry keeps `selected: []`, which `part_record`, `parts_manifest` and binding
fill all refuse. Unknown never becomes pass.

## Exa search tier

The Exa tier runs only when the cascade reaches it and the owner passes
`--allow-network` (`allow_network=True`). Without the opt-in, the tier is
recorded as `unavailable` with the reason `network not enabled`, and the
executable is never started.

When the tier is enabled, the producer runs the existing `exa-search` CLI with
`--max-results N [--include-domains a,b] -- <query>`. It runs as an owned child
process under a hard elapsed-time deadline (`--search-timeout`, at most 120 s),
using the same child ownership and cleanup as datasheet acquisition. Queries
are separated by at least `--min-interval` seconds (default 1 s, minimum 0.5 s).

The raw stdout is retained byte for byte as `search/<n>.json`, up to 1 MiB, with
its SHA-256, size and UTC `fetched_at`. A deadline, a nonzero exit or
unrecognised output is recorded as a failed search with its reason. The query,
domain list and result bound (1–10) are explicit in the request; nothing is
inferred from prose. Result URLs outside `include_domains` are dropped. The
producer adds no Python dependency and makes no Exa request of its own. API
credentials stay with the `exa-search` CLI.

## Request

`agent-ee.datasheet-cascade-request/1`. Paths are relative to `--root`, and
every tier key is present, `null` when not declared:

```json
{
  "schema": "agent-ee.datasheet-cascade-request/1",
  "prior_selection": {"path": "decisions/source-selection.json", "sha256": "<file hash>"},
  "parts": [{
    "key": "C38695",
    "digikey": {"capture": "digikey/digikey-capture.json", "key": "TJA1051T-3"},
    "jlc": {"capture": "jlc/catalog.json", "lcsc": "C38695"},
    "catalog": {"directory": "identity", "manifest_sha256": "<manifest hash>", "lcsc": "C38695"},
    "exa": {"query": "NXP TJA1051T/3 datasheet pdf", "include_domains": ["nxp.com"], "max_results": 5}
  }]
}
```

Only the prior file's `rejected` hashes are used. Its selections never carry
over, and its rejections are copied into the new entry, so a rejection is never
lost.

## Output

One new directory, published by a single rename (nothing is published on an
infrastructure failure):

```text
<out>/datasheets/manifest.json   agent-ee.datasheet-capture/1, every attempted candidate
<out>/datasheets/<sha256(url)>.pdf
<out>/search/<n>.json            raw exa-search stdout
<out>/source-selection.json      agent-ee.datasheet-source-selection/1
<out>/cascade.json               agent-ee.datasheet-cascade/1
```

Acquisition IDs are `<key>#digikey`, `<key>#jlc`, `<key>#catalog` and
`<key>#exa-<rank>`. Each source-selection entry contains:

- `selected`: zero or one source, with the PDF hash and the acquisition
  `{path, sha256, locator: "parts[id=…]"}`;
- `rejected`: the prior rejections, plus each candidate whose hash matched
  one of them;
- `failed`: each attempted candidate with no PDF, with its URL and reason and
  the locator of its missing acquisition record.

`failed` is an optional list in the source-selection contract. A selected ID
that also appears in `failed` is refused. Files without the list keep their
existing meaning.

`cascade.json` embeds the request and records every tier's status, source
provenance and URLs. For DigiKey and JLCPCB this is the capture and raw-response
path and hash. For the catalog it is the index manifest and database hash and
the row provenance. For Exa it is the search record. The file also lists every
attempt with its outcome, PDF hash and reason, the search records, the
acquisition and selection pins, and the input dependency map.

## Commands

```sh
PYTHONPATH=src python3 -m agent_ee.datasheet_cascade resolve --root build \
  --request cascade-request.json --out cascade
PYTHONPATH=src python3 -m agent_ee.datasheet_cascade resolve --root build \
  --request cascade-request.json --out cascade-exa --allow-network
PYTHONPATH=src python3 -m agent_ee.datasheet_cascade validate --root build \
  --cascade cascade/cascade.json
```

`resolve` exits 2 when a part is unresolved or an input is invalid. `validate`
replays everything without network. It re-derives tiers 1–2 from their
captures, Exa URLs from the retained search bytes, and each attempt outcome
from the acquisition and prior rejections. It rebuilds the selection entry,
checks that a search ran exactly when the cascade reached it with the opt-in,
and prints the dependency map.

## Downstream

Pass the selection to `part_record` with
`--source-selection <out>/source-selection.json --source-part-key <key>`. Then
put the record's `artifact`, `datasheets`, `datasheet_id` and
`source_selection` into the parts manifest, as described in
[binding fill](binding-fill.md#captured-datasheet-source-selection).
`qualify_manifest` and binding fill replay the selected acquisition and pin
the selection file and the acquisition manifest in the immutable closure.

## Limits

- A selected PDF is the first acquired, non-rejected candidate. Selection does
  not verify family, ordering-suffix correspondence, ratings or applicability.
  Those still need a reviewed rejection or later evidence.
- Rejection is by exact PDF hash only. The producer does not infer a wrong
  family from URLs, filenames or prose.
- The Exa and DigiKey response mappings are not yet qualified against live
  responses. The tests use owned loopback stubs and a stub `exa-search`.
