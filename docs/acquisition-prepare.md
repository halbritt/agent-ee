# Acquisition brief preparation

The source-only preparer accepts the captured acquisition control reference and
one total I2C line-capacitance bound. It generates a complete input revision and
`agent-ee.build-plan/2` for the existing public build. It calls no model, native
tool, network service or catalog. Preparation does not qualify the reference,
judge the engineering, or establish file release.

Write this ordinary UTF-8 brief, changing `100` to `150` for the development
variant:

```text
Use acquisition reference acquisition-control unchanged.
Total I2C line capacitance shall be at most 100 pF per line, including board, host and cable.
```

Each nonblank line must be exactly one of these sentences. Surrounding whitespace
and blank lines are permitted. The quantity accepts ordinary decimal spelling
(`150.00 pF`) and whitespace including a nonbreaking space before `pF`. Its exact
spelling is retained in the generated specification. Finite decimal bounds in
the continuous **100 through 150 pF inclusive** envelope are supported. The
existing interval checker limits the canonical decimal coefficient to 28 digits
(three integer digits and up to 25 fractional digits). Leading integer zeros and
trailing fractional zeros are ignored for that check, so previously accepted
zero-padded 100/150 spellings remain accepted. Excess precision refuses with
`unsupported_fixed_recipe_bound`; it is never silently rounded. Missing reference adoption or a missing
bound is unresolved. Unknown text, another reference, cable-only capacitance,
ambiguous values and additional changes refuse. Duplicate or conflicting bounds
refuse. There is no implicit default, unanswered decision, or generic prose parser.

`acquisition-control` names the **original captured recipe**, not a claim of
qualification. The existing recovery manifest pins its build plan, all 198
source files, profile and native budgets. The preparer requires those exact bytes;
it does not silently adopt later typed or previously prepared inputs.

Run from the repository, with the existing source and additive captures:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.acquisition_prepare \
  --brief /absolute/path/acquisition-150.md \
  --build-plan /absolute/path/acquisition-release/build.json \
  --added-input cpl=/absolute/path/cpl-inputs-rev3 \
  --added-input manufacturer-text=/absolute/path/manufacturer-text \
  --out /absolute/path/new-acquisition-150
```

The build plan resolves its existing `source` directory. The two additive trees
must match `fixtures/acquisition-typed/overlay.json` exactly. The committed
`fixtures/acquisition-typed/operating-supplement` is supplied automatically; an
explicit `--added-input operating-supplement=DIR` may select an exact copy.
Missing/drifted source bytes or additions stop preparation without downloading
replacements. Manufacturing/profile and placement/verification budget settings
come from the captured plan and source; this slice introduces no budget schema.

To request the existing full engineering review at the first package attempt,
pass an explicit subscription review profile as caller context:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.acquisition_prepare \
  --brief /absolute/path/acquisition-150.md \
  --build-plan /absolute/path/acquisition-release/build.json \
  --added-input cpl=/absolute/path/cpl-inputs-rev3 \
  --added-input manufacturer-text=/absolute/path/manufacturer-text \
  --review-profile /absolute/path/pod-review.json \
  --verification-timeout-s 3600 \
  --out /absolute/path/new-acquisition-150-reviewed
```

The existing profile validator checks the profile without launching a provider.
Its exact bytes are captured as `inputs/preparation/review-profile.json`, and
the typed plan names `preparation/review-profile.json` in `review_profile`.
The unchanged reference adoption still names the circuit recipe. This explicit
review request grants no inherited engineering acceptance and authors no
judgment or verifier witness. Without `--review-profile`, behavior stays as above.

`--verification-timeout-s` is optional and requires `--review-profile`. It sets
the existing build-plan field, validated as an integer from 1 through 86400.
Omitting it retains the captured plan's 1800 seconds; there is no automatic
increase or reset. The example explicitly requests 3600 seconds; execution still
needs the coordinator's finite grant. Preparation performs no reviewer/tool
admission and does not establish that the requested budget is sufficient.

Profile bytes, the validator's source hash, and requested/effective timeout are
bound into preparation identity. `inputs/preparation/review-context.json` and
the edit ledger record this context, and the final input inventory binds the
captured profile. Even a whitespace-only profile change invalidates preparation
and downstream build input identity. Missing, malformed, linked or hard-linked
profiles, linked ancestors and profile drift during preparation refuse before
publication, leaving no partial bundle. Exact original recipe admission stays
in force; this option cannot revise parts, geometry, stock or electrical limits.

The Python interface is
`acquisition_prepare.prepare(brief_path, build_plan_path, output_path,
added_sources={'cpl': cpl_directory, 'manufacturer-text': text_directory})`.
Its optional keyword arguments are `review_profile=profile_path` and
`verification_timeout_s=3600`, with the same rules as the CLI.
It returns the preparation report. `PreparationRefused.report` contains a
structured unresolved/refused outcome, diagnostic code and exact UTF-8 byte
locator. The CLI emits this JSON and exits 1 for a refused brief, exits 2 for
unavailable/malformed sources or output errors, and exits 0 only for preparation.
No runnable bundle is published on failure. Existing outputs, including empty
directories and links, and paths aliasing/nesting with inputs are refused.

The new output directory contains:

- `build-plan.json`: typed public plan with map/2, obligations/1 and the declared
  late-bound judgment directory. It retains the existing native budget settings.
- `inputs/`: the complete captured engineering dependency tree, revised spec,
  parts/locality citations, interval operand, operating text and calculations.
  Circuit/topology, layout, groups, ratings, ADC ties and manufacturer sources
  retain their original bytes. `preparation/brief.md` preserves the raw brief.
- `inputs/preparation/source-map.json` and `edit-ledger.json`: source and derived
  locators, chosen capacitance, source inventories and the old/new hashes for
  the engineering revision before typed overlay preparation. The overlay then
  derives the final typed requirement map and interval bindings;
  `inputs/overlay-manifest.json` records that second transformation, and
  `preparation.json.inputs` identifies every final input. Changed original files
  are archived under
  `inputs/history/acquisition-preparation-original/`.
- `overlay.json`, `inputs/overlay-manifest.json` and `preparation.json`: the
  actual derived overlay, typed accounting, complete input inventory, identity
  and unresolved engineering obligations. No judgment or witness is authored.

The adapter owns the ten operative file revisions documented in
[the earlier 525 transcript](qualification/acquisition-variant-525.md). It
recaptures the real spec and translates every existing partition and citation
around the quantity edit, including changed byte lengths and UTF-8. It preserves
the authored interval equation, pullup bounds and 300 ns limit; it changes the
total bus-capacitance operand. The retained worst-case RC model predicts
188.27006 ns at 100 pF and 282.40509 ns at 150 pF. These calculations do not prove
physical host/cable compliance or broader model applicability. A rejected
200 pF request concerns this fixed recipe, not all possible acquisition designs.
For positive fixed pullup resistance, the authored relation
`rise = .8473 * R * C` is nondecreasing in total capacitance. The existing
interval checker proves this monotonicity over 100..150 pF and the retained
2178..2222 ohm pullup interval; the maximum is 282.40509 ns at the upper
endpoint, below the unchanged 300 ns limit. This justifies interpolation within
the declared model, not new components, temperature, load or host applicability.
The preparer uses exact canonical operand text and a local 40-digit calculation
context with inexact arithmetic trapped: the 28-digit capacitance coefficient
and two four-digit factors need at most 36 digits. Ambient Decimal precision
therefore cannot silently round the captured operand or RC transcription.
Control 100 and variant 150 are known development cases, not unseen evaluation.

Only the operating supplement's old 100 pF bound is superseded by the selected
variant. Its power-up and conversion sequencing instructions stay in force, and
the supplement itself is preserved byte-for-byte. Historical review text is
archived; its revised transcription explicitly withholds independent-review
credit. Nonzero J2 load interpretation, exact-part/model applicability and
physical/supplier obligations remain unverified.

Every raw brief byte change changes the preparation identity and the captured
public-build source inventory, even when numerical meaning is unchanged. The
identity also binds the producer, original plan/source, overlay and additions.
Outputs are assembled in owned sibling staging and published with the existing
no-replace atomic directory operation only after typed accounting succeeds.

After separate native authorization, the ordinary downstream command is:

```sh
bin/agent-ee build run /absolute/path/new-acquisition-150/build-plan.json \
  --out /absolute/path/fresh-native-run --through artwork
```

Source preflight consumes this bundle; no native run is implied by preparation.
The emitted plan retains the captured file-only placement path and does not
invent a strict block source/contract or native identity. Selecting and proving
the current strict generated-board block bridge remains a separate consumer
step. Its final artwork/package revalidation guard must remain effective.
Required judgments need actual independent concurrence, and ZA-10's full
engineering review remains separate. The preparer does not supply stage
receipts, ERC/DRC, file release, original esp_prog/M1 closure, supplier preview or
physical validation. The telemetry-pod hold remains.

Pure-source regression tests use the existing retained-input environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  AGENT_EE_ACQUISITION_INPUTS=/absolute/path/acquisition-release/inputs \
  AGENT_EE_ACQUISITION_PLAN=/absolute/path/acquisition-release/build.json \
  AGENT_EE_ACQUISITION_CPL_INPUTS=/absolute/path/cpl-inputs-rev3 \
  AGENT_EE_ACQUISITION_TEXT_INPUTS=/absolute/path/manufacturer-text \
  python3 -m unittest test_acquisition_prepare -v
```

Without those retained captures, the integration cases report explicit skips;
the brief-parser cases still run. Such skips cannot qualify the real recipe.
`AGENT_EE_ACQUISITION_PLAN` defaults to `build.json` beside the input directory;
set it explicitly when a test capsule relocates the plan and inputs separately.

## Optional captured ADC carrier context

The same public preparer can automatically retain the primary ADS1115IDGSR or
select its source-backed ADS1115IDGST carrier alternative. Supply all three
explicit options together:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.acquisition_prepare \
  --brief /absolute/path/supported-brief.md \
  --build-plan /absolute/path/original-captured/acquisition-release/build.json \
  --added-input cpl=/absolute/path/captured-cpl \
  --added-input manufacturer-text=/absolute/path/captured-manufacturer-text \
  --alternative-context /absolute/path/reviewed-context.json \
  --alternative-context-sha256 EXACT_CONTEXT_SHA256 \
  --alternative-source /absolute/path/read-only-retained-context-root \
  --out /absolute/path/fresh-output
```

Omitting the context preserves the original part choice and preparation path.
Supplying a source/hash without a context, missing either companion option,
a context hash mismatch or an aliased/changed context refuses without publishing.
The original recipe admission, capacitance range, mechanics, local-return helper
pin, review-profile/timeout context and typed accounting stay in force. No
actual native/build operation is part of preparation.

The finite `agent-ee.acquisition-alternative-context/1` document contains exactly
`schema`, `reference` (`acquisition-control`), `files` (a 1..64 path/hash closure),
`catalog`, `carrier_page`, `ordering_text`, `parents` (exactly two catalog blobs)
and `derivation`. Blobs use canonical captured `{path,sha256}` references. Paths
are relative to the explicit source root; every referenced file and raw response
must be pinned, and unrelated extra files refuse. The reviewed context is an
explicit evidence input, not a caller-qualified flag or a manually chosen part.

The catalog must equal the ordered union of two disjoint retained parent
captures with the same source contract: exact requests, rows and response bytes,
earliest parent as_of and latest completed_at. Original fetch times and the
external derivation/parent identities are retained. No new capture is made and
no freshness clock is reset. The preparer's actual UTC evaluation anchor and
all context/producer/helper pins enter preparation identity; normal run-bound
stock checks remain downstream obligations. Source/context drift is rechecked
before atomic publication.

The admitted closure is copied below `preparation/alternative-context/`, with
its exact context document at `preparation/alternative-context.json`. Source
selection and `preparation/alternative-report.json` are produced before the
existing typed seal. The assembly catalog path/hash is rebound without relaxing
its authored policy. `edit-ledger.json` includes the four source-file changes,
archived original circuit/component selections, source-capture hashes and
selected catalog; old catalogs and historical choices stay untouched.
When the original parts citation names the later requirements-stage namespace,
its identical operative spec citation is first rebound to the actual `spec.md`;
no missing namespace or generated receipt is invented. The overlay map pin and
mechanics/typed accounting bind the revised map. Nothing is appended to inputs
after sealing.

Selection regenerates named records, full parts and purchasing-reference
bindings through existing contracts. Fresh ordinary production/native binding,
rating provenance, electrical/package review, supplier preview and physical
validation remain pending. Preparation provides no G2/ZA04/G4 or assembly
acceptance. See [the narrow carrier contract](acquisition-alternative.md).
