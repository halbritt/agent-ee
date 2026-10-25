# Acquisition additive primary-text capture — 2026-10-25

This verified input revision adds reproducible manufacturer text to the accepted
typed acquisition overlay. It prepares citation material for the fourteen
judgment briefs; it authors no judgment, witness, engineering evaluation or M1
acceptance. The chief independently verified the capture and preparation as described below.

The prior accepted overlay is `f2d9f97a`, SHA-256
`0749a6ba90c83ed956394775abee0e67cdf5ae2618c62408c78f4c9a90a82592`.
The primary-text revision added only `added_inputs.manufacturer-text`. Its 14 pins name
thirteen extracted text files and their manifest. The original 198-file input
tree, all 25 CPL files, specification, parts and electrical parameters stay
unchanged. Final D1 polarity remains
`23a0b7986e20c676c1ada1b5c09647743eb854b987b320104be741137468e851`.
The older prepared tree and receipt are preserved as a separate input identity.
The current preparation command also supplies the later
[operating supplement](acquisition-operating-supplement-2026-10-25.md); its
instruction correction is a separate additive input revision.

## Capture procedure and custody

The committed metadata in
[`manufacturer-text-sources.json`](../../fixtures/acquisition-typed/manufacturer-text-sources.json)
pins P1–P13 from source-readiness result `4a6027fc@1`: each original PDF path,
SHA-256, originating capture manifest/hash, part ID and physical page count.
It also pins the installed `/usr/bin/pdftotext` and `/usr/bin/pdfinfo` binaries
and their Poppler 24.02.0 version strings. No source is fetched.

Run the committed, CPU-only procedure on the immutable base capture, writing a
new private directory whose parent already exists:

```sh
python3 fixtures/acquisition-typed/capture_manufacturer_text.py \
  /path/to/accepted/acquisition-inputs \
  /path/to/new-parent/manufacturer-text
```

The procedure verifies the complete base inventory, each selected PDF against
its existing manifest, and both extraction tools before creating the output.
It refuses an existing output or an output nested with the base. For each PDF,
it runs `pdfinfo` for the page count, then `pdftotext -f 1 -l N -layout -enc UTF-8
-eol unix SOURCE -`, with `LC_ALL=C.UTF-8` and `TZ=UTC`. Both invocations have a
120-second bound. The manifest retains their exact argument arrays, exit codes,
diagnostics, PDF metadata, procedure/descriptor hashes and output hashes.

The text file contains exact stdout bytes, with one form-feed byte after every
physical page. All offsets are half-open UTF-8 byte offsets. Each page record
has `byte_start`, `content_byte_end` (before the form feed), `byte_end` (after
it), the hash of that complete page segment, and a nonempty-text flag. These
ranges partition the complete output. No stripping, replacement decoding,
reflow, OCR or diagram transcription occurs. Strict decoding and a
`read_text(encoding='utf-8').encode('utf-8')` comparison establish that the
consumer's newline handling preserves each text file's raw hash.

A nonzero tool result, incorrect page segmentation, changed hash or wholly
empty extraction refuses capture. Once the output is created, a manifest
records expected failures, retains partial output and stays `failed`; an
unexpected exception leaves `incomplete`. Preflight refusals create no output.
Neither state is eligible for the pinned overlay. Text-empty individual pages
remain recorded, without treating them as blank pages or inventing OCR.

The completed capture contains 13 PDFs / 574 physical pages. TPS7A20 physical
page 59 has no extracted text; the other 573 pages have non-whitespace text.
All tool exit codes are zero and diagnostics are empty. This measures extraction
availability, not the completeness or correctness of a manufacturer's figures.
The full capture was rerun into a second new directory: all 14
output hashes, including the manifest, match byte-for-byte.

The freshly reproduced BAT46W and JST text hashes happen to match historical
references in `engineering-calculations.json`. Those historical text files
were neither read nor copied. The new manifest establishes custody directly
from the already captured PDF bytes.

## New preparation and compilation

Copy the original base, accepted 25-file CPL tree and new 14-file text tree into
the isolated worktree at the input paths below. Copy the retained v7 scaffold
inputs to `build/source-preparation/scaffold`. The output `prepared/` and receipt
must be new. From that worktree, the tested invocation uses the existing generic
preparer through the supported `ae-ci-exec` environment and its admission control:

```sh
AE_CI_WEIGHT=1 AE_CI_MAX_WAIT=120 ae-ci-exec . -- \
  python3 -m agent_ee.typed_overlay prepare build/acquisition-release/inputs \
  fixtures/acquisition-typed/overlay.json --out build/source-preparation/prepared \
  --added-input cpl=build/acquisition-release/cpl-inputs \
  --added-input manufacturer-text=build/acquisition-release/manufacturer-text \
  --added-input operating-supplement=fixtures/acquisition-typed/operating-supplement \
  --compile-against build/source-preparation/scaffold \
  --receipt build/source-preparation/receipt.json
```

The preparer needs no implementation change. Compared with the older accepted
prepared revision, its derived map changes only by adding the
`manufacturer-text` input mount to `scope.inputs`. All requirement mappings,
spans, check definitions and obligation declarations remain equal; obligations
and re-bound electrical intervals are byte-identical. All 196 previously
unchanged base files and all 25 CPL files remain byte-identical.

| New receipt field | SHA-256 |
| --- | --- |
| Overlay | `790414c9222e10c0c8932dc0379ff17463bb359d7e80f2f05b37fb8582d6321d` |
| Capture manifest | `ee1c4d695cdd0ba20527a50bd73516dc2e955559a9bd59829edd5d35748fe43f` |
| Scope | `661d817bc93614d6a9d485aa4bf2860cc931e50abd1943a502dfb8fc3c2af701` |
| Baseline | `0490c492ae268342de0a605dd829ba354bbce37f5e5ed914b742d62331413fe5` |
| Coverage | `e91f6b1ba0da0835fee7644a97b83f82c33e227103cbddf19aac1e5d07ecdd6b` |
| Derived map | `74bcb4141ac061430985f7ac834a9d8c565f4e3f5aeaf1a6cfa6a269a87cc770` |
| Unchanged obligations | `851029c041ec92aaa96133f7720e507b95217c6989b85ecf8e3c8863ce7bbdc2` |

The compiled receipt still has **47 rows and fourteen absent/pending judgments**,
one pending operational obligation and two pending physical obligations. No
check results or engineering conclusions have been fabricated; executable
coverage also remains pending. The unchanged coverage identity describes
requirement accounting, while scope and baseline identify this new input set.

## Verification and remaining gaps

Five strict modules pass under the supported native image: `test_typed_overlay`,
`test_requirement_map`, `test_requirement_map_envelope`, `test_requirement_scope`
and `test_spec_record`: **89 tests, no skips**. The existing real-capture test
now receives the additional tree (default
`build/acquisition-release/manufacturer-text`, or
`AGENT_EE_ACQUISITION_TEXT_INPUTS`). It checks all page partitions and hashes,
full-text byte/newline identity, and real citations from every document through
`source_authority.verified_reference`. A separate probe resolves actual
TPS7A20/ADS1115 absolute-maximum headings, BAT46W electrical-characteristics
heading and JST specifications heading by full-text hash and exact quote;
wrong hashes return `integrity`. These checks establish quote resolution only.

An existing-output negative preserves the complete capture. A changed PDF in a
fresh private copy of the base refuses before extraction. The real 198-file
inventory is rechecked afterwards. No historical source or input is overwritten.

Remaining limitations are explicit:

- Diagrams, image-only assertions, numerical graph readings and the empty
  TPS7A20 page require separate inspection; machine text does not verify them.
- Exact-part Samsung bias applicability and Murata's exact approval sheet remain
  unresolved. The older catalogue/viewer text is identified as such. No missing
  source has been fetched and no historical Samsung product HTML has been adopted.
- Candidate copper/return paths, removed-plane controls, body/mating/cable
  clearance, supplier confirmation and physical qualification remain later-stage
  evidence. Extraction does not establish electrical or mechanical adequacy.

## Retained evidence

Private capture and verification root:
`/home/halbritt/.cache/agent-verification/agent-ee-569-manufacturer-text-20261025/`.
It retains the initial capture, final `manufacturer-text/`, identical
`manufacturer-text-reproduced/`, exact source inventories, logs, negative,
`quote-resolution.json`, `coverage.json`, `preparation-summary.json`,
`runtime-binding.json`, new `prepared/`, `receipt.json` and the compile-root copy.
PDFs and extracted source text remain outside Git.

The strict run and preparation used image `agent-ee:exec-af30a0917f4a62f5`, ID
`sha256:939f4bfb6bb45eaff5fc47d27b94f905ec979e31ec66a190fe6fb57ff96753e1`.
Its build-input hash/label was recomputed and matched. The extraction ran on the
host using the separately pinned CPU tools. Tested source hashes are retained.
There was no native board campaign, source fetch, model-provider call, judgment,
upload, purchase, issue closure or main push.

Artifact index: `artifact-sha256.json`, 749 files, SHA-256
`012754a79c601794efbd1c4786407738604b5e81e5e055b3f7740227a73b1308`. Doctrine provenance is retained with packet
`pkt-6b0152a23d613af9`, content SHA-256
`6b0152a23d613af984ea332383904d3bde21e25c02cba3cc2712605f34ad0522`;
only byte/text custody and authority limits were applied.


## Chief independent verification

The chief reviewed metadata delivery `32ae67df`, rebased it onto the integrated
qualification consumer, and verified eight delivered source/test hashes and all
749 indexed worker artifacts. Running the committed CPU capture procedure directly
against the original 198-file capture reproduced all 14 output hashes exactly,
including manifest `ee1c4d69…` and all 574 physical-page records.

A separate supported native strict run passed **89 tests in five modules with no
skips**. The chief then invoked the public preparer with the reproduced text, the
unchanged 25 CPL inputs and a copy of the retained v7 scaffold. It produced the
same scope, baseline and coverage identities listed above. All 47 requirement
rows and 14 pending judgments remain. Four real source quotes resolved, while
four wrong-hash controls returned integrity refusals.

The original 198 files were compared with the mounted test copy. In the new
prepared tree, 196 original files remain byte-identical; the map changes only by
the added text mount relative to the preceding accepted typed preparation, and
the already-derived intervals, spec, spec record and obligations are unchanged.
All CPL files and final D1 polarity are unchanged. The earlier prepared identity
is retained separately.

Chief evidence is retained at
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/manufacturer-text/`:
reproduced text/manifest, source bindings, strict log, new prepared tree, compile
root, receipt and `chief-verification.json`. This verification accepts source
capture and input preparation only. The engineering, supplier, physical and M1
limitations above remain in force.
