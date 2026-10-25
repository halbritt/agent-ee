# AGENTEE-523 / REC-31 sibling comparison evidence

Agent-567, 2026-10-25. Base: `86cb840f42173453e6737a69239215727a1ef3ad`.
Assignment: Cairn `7cb1fb3f-0974-46ad-8204-7b331afef084@1` from chief agent-564.
This records a bounded proof repair and one diagnostic native run. Chief review,
integration and issue disposition remain separate.

## Change and preservation boundary

Native KiCad serialization reordered otherwise unchanged artwork siblings. The
old positional comparison reported `silk identity changed` on the retained
esp_prog proposal described in
[the probe contract](contracts/AGENTEE-523-artwork-reuse-probe.md).

`routed_artwork.write_scope` now pairs supported siblings by kind and decoded
UUID within their existing parent. The supported parent/child pairs are:

| Parent | Children eligible for UUID pairing |
| --- | --- |
| Board root | `footprint`, supported `gr_*` graphics |
| Direct board footprint | `property`, supported `fp_*` graphics |

Supported graphics remain the existing line, arc, rect, circle, poly and text
kinds. The set of paired slots and each parent's identity/kind inventory must
remain equal. Global UUID uniqueness validation still runs before comparison.
No parsed tree is reordered or mutated. The paired raw nodes still undergo the
existing exact content and translation checks. Unkeyed supported nodes retain
positional comparison; unknown syntax retains exact raw spelling and position.
Pads, copper, parent poses, style, text content, unauthorized fields and
non-translation edits retain refusal behavior. Explicit field poses are checked
against independently located source/destination fields before comparison.

The change adds no relocation policy. `build_workflow`, qualification,
`placement_job` and planner code are unchanged. The build stage's field-only
default and explicit graphics opt-in retain their existing behavior.

## Verification

The new positive sibling-order test failed on the base implementation. After
repair, this command passed all **35 tests**: five new adversarial tests, 27
existing routed-artwork tests and three native build-artwork tests.

```sh
ae-ci-exec . -- env PYTHONPATH=src:tests python3 -m unittest \
  test_routed_artwork_siblings test_routed_artwork.RoutedArtworkTest \
  test_build_artwork -v
```

The new controls combine reordered siblings with pad/track edits, footprint pose
changes, changed UUID/kind/text/style/layer/angle, graphic reshaping, cross-parent
moves, added/deleted items, unknown syntax edits/moves and raw token respelling.
They also reject duplicate UUIDs on otherwise unchanged input, UUID-bearing
unknown syntax, supported-looking nodes inside unknown parents, wrong explicit
field destinations and unselected field edits. Existing malformed-input refusal
messages and stale-render-cache refusal tests pass.

Runtime: Python 3.12.3, KiCad CLI 10.0.7, pcbnew
`10.0.7-10.0.7~ubuntu24.04.1`, image
`sha256:939f4bfb6bb45eaff5fc47d27b94f905ec979e31ec66a190fe6fb57ff96753e1`.
The native logs include the existing KiCad enum assertions/debug messages;
the final test process exits zero.

## Retained proof and fresh diagnostic run

The seven retained artifact hashes named by the contract were verified before
use. The exact retained before/after pair now allows **85 translated identities**.

| Board | SHA-256 |
| --- | --- |
| Original | `dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1` |
| Retained and fresh proposal | `128c7967e7775873472297e927f6867ca652bff0f5b80590e6d6194b3e5982e6` |

Exactly one fresh `produce(..., targets=None)` diagnostic ran on that same
original, with its adjacent project, read-only source mounts, separate writable
output, caller UID, no network, native-admit and a 180-second bound. It took
7.593 seconds, preserved the original, and wrote the same proposal bytes as the
retained run. The scope proof allows the same 85 identities. The wrapper exits
**1** because production then refuses:
`artwork introduced a non-silk native DRC finding`.

The unchanged comparison gate counts two translated `text_height` findings as
added. Both already exist on the original, at the same 0.508 mm height against
the board's 0.800 mm minimum. Their reported positions change:

| USB1 text UUID | Before (mm) | After (mm) |
| --- | --- | --- |
| `43596a4e-6638-4bf5-aca5-99e16d344e98` (D+) | (118.5, 100.1) | (116.5, 100.1) |
| `f3c3ec99-e38a-4e54-8391-fafbafffeaa0` (D-) | (118.5, 99.35) | (118.5, 95.1) |

No comparison-policy change or second native produce attempt was made. Native
silk findings remain: 14 `silk_over_copper` and three `silk_edge_clearance`;
`silk_overlap` falls from 165 to zero. This is diagnostic evidence, with no
published producer receipt or acceptance report because production refused.
Final source rechecks both retained and fresh saved pairs without another native
run.

## Evidence and limits

Private evidence root:
`/home/halbritt/.cache/agent-evidence/agent-ee-523-agent-567/`.
It holds `retained-pins.json`, `retained-proof.json`, the red and final native
test logs, runtime versions, and `fresh/` with the wrapper, exit status, summary,
saved boards, native DRC/containment reports, `fresh-proof.json` and the offline
comparison reconstructed from those reports. `artifact-hashes.json` binds the
selected evidence files. Input artifacts were retained unchanged.

The sibling-order failure is repaired; complete native production still refuses
at a separate DRC gate. The proof does not establish legibility, classification,
rigid-group correctness, via processing, final DRC/render acceptance or M1.
Those obligations and AGENTEE-523 closure remain with the chief's campaign.
