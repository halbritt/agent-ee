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
spelling is retained in the generated specification. Only values numerically
equal to **100 or 150 pF** are supported. Missing reference adoption or a missing
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

The Python interface is
`acquisition_prepare.prepare(brief_path, build_plan_path, output_path,
added_sources={'cpl': cpl_directory, 'manufacturer-text': text_directory})`.
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
