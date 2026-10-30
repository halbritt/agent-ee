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

The pinned original `ENGINEERING-REVIEW.md` remains a mechanical recipe input,
outside fresh reviewer mounts. After the existing source/model corrections,
preparation removes its inherited board-verdict body from the generated source
and omits its archival copy. The edit ledger retains the original hash and an
explicit `after: null` omission record. The eight known judgment source references
are rebound to operative calculations, operating instructions and model/limit
declarations; requirement spans, checks, thresholds and model provenance remain
unchanged. Unknown source references or scope-description text refuse before
publication. Normal typed accounting and candidate capture bind the new source;
no existing baseline or qualification can be reused by editing its inputs.

An inherited review in a nested role payload, an archived copy, or an exact copy
of the original body under another name also refuses. Role captures must exclude
such board testimony **before** their own source qualification. Preparation does
not prune or reseal admitted role payloads. Genuine source-role qualification
records and model derivation/convergence evidence remain ordinary source inputs.
Filtering `packet_inputs` alone would leave readable copies in `/candidate` and
retained stage evidence, so it is not the isolation boundary.

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
By default the emitted plan retains the captured file-only placement path.
The explicit `--final-groups` option below derives the strict boardless source;
native identity and final artwork/package proof still require the public build.
Its final artwork/package revalidation guard must remain effective.
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
The original recipe admission, capacitance range, mechanics, local-routing helper
pins, review-profile/timeout context and typed accounting stay in force. No
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

The context may additionally contain `supplier_capture` with four blob refs:
`provenance`, `raw`, `http_response`, `receipt`. This bounded extension is only
for exact C468683 supplier orientation evidence. The existing supplier parser
must validate its LCSC, package association, exact ADS1115IDGST manufacturer part,
raw hash/size and retained HTTP result-byte span. Capture takes place separately
under explicit admission; preparation itself remains offline. A selected part
without exact supplier-footprint coverage refuses; primary data is never
relabeled or reused for the alternative, and no fetch occurs implicitly.

The preparer derives a fresh owned copy of the original admitted `cpl` added-input
tree, adds the exact new raw/provenance bytes, and records the resulting full
inventory in preparation identity. The original tree and original overlay stay
unchanged. Before sealing, the CPL check's supplier provenance list is recomputed
from the actual selected allocations. Every selected ID must have an exact parsed
supplier source. Unselected historical captures remain in custody. Rotation policy,
tolerances, polarity evidence and native pin/pad checks retain their original rules.
Supplier coverage does not prove a native row's orientation or supplier approval.

`assembly-release-ids.json` is now an operative selected purchasing-allocation
roster, regenerated from the qualified parts manifest. Its prior bytes are archived
under `history/acquisition-preparation-original/` when changed; the edit ledger and
`preparation/selected-part-input-closure.json` record source/roster hashes and explicit
added/removed IDs. The ACQ-MECH-4 applicability question names the operative roster
hash and current purchasing decision/catalog; the old roster is labeled historical.
It continues to require independent process/applicability judgment and expressly
does not establish supplier approval or physical validation.

The selected purchasing decision does not become the parts-membership requirement.
The original authored membership citation is retained through selection. Before
archive/seal, the preparer derives the ordinary typed requirement accounting,
requires its one parts check/mapped requirement to match that authored citation's
spec hash and locator, and binds the citation to the normal compiler provenance
path. The edit ledger records this namespace binding. Per-part selection records
still pin the purchasing decision separately. No requirement or consumer guard is
weakened, and no compiler receipt/native acceptance is manufactured. The normal
requirements stage later captures the same operative spec bytes into its reserved
namespace; the input revision does not occupy that namespace itself.

## Optional exact-part source supplement

The finite captured source correction is supplied through the existing option:

```sh
--added-input source-supplement=/absolute/path/reviewed-source-supplement
```

The original 198-file recipe and original additive captures are still admitted
exactly. The optional directory must match the complete private capture inventory
in `fixtures/acquisition-typed/source-supplement.json`. The repository stores
hashes, not manufacturer documents. The directory contains full original Samsung
HTML (including the historical page cited by the calculations), the exact embedded
JSON span, chart-definition script and request custody; exact Murata product
association/PDF and PDF text extraction provenance; and the ADC source analysis.
No new fetch or PDF extraction occurs during preparation. Recorded extraction
tool versions describe provenance; installed tool versions are not admission gates.

Before the existing typed overlay seals inputs, the producer corrects only C4/C5
geometry attribution to exact GRM1885C1H103JA01D reference-sheet page 2. Their
1.7/0.9/0.9 mm maxima and every numerical geometry field remain unchanged.
Mechanics supports the historical model/checker pair and exactly this corrected
model with the same checker and mandatory captured Murata source dependencies;
missing/changed sources or another model/checker still refuse. The full supplement,
its inventory descriptor, helper hash, model and source declarations enter the
preparation/input identities. Changed originals are archived in the existing
history tree and listed in the edit ledger before sealing.

The generated ADC analysis retains single-shot 128 SPS and labels the 8-SPS/25 C
gain maximum and single-ended offset typical as conditional source values, with
no applicable guaranteed 128-SPS total accuracy. Ideal quantization and typical
shorted-input noise are distinguished without double-counting quantization RMS
or PGA/reference error. Calculation citations resolve to actual captured bytes.
Fresh judgment briefs cite the supplement; none of the 14 judgments is supplied
or accepted by preparation. Typical Samsung curves do not establish guaranteed
biased capacitance/ESR bounds. Power-model/corner corrections, new native results,
independent judgments, supplier and physical obligations remain separate.

Omitting this optional input preserves the historical recipe path. It composes
with the existing captured carrier context and supported 100–150 pF briefs.

## Explicit R6 power-repair exception

The finite R6 repair uses the same public preparer and original captured recipe:

```sh
--added-input power-repair=/absolute/path/reviewed-power-repair
```

It requires the captured ADC carrier context and exact `source-supplement`.
Omitting it preserves the original R6 and no-repair path. This input is an
explicit exception to the retained original-reference adoption sentence: its
separately hashed `activation.txt` permits only R6 = YAGEO RC0603FR-07620RL,
C137695, 620 ohm, 1%, 0603, and the reviewed seven-case power analysis. The
complete owned input closure must match
`fixtures/acquisition-typed/power-repair.json`; missing, changed or unrelated
sources refuse. The original 198-file admission, brief and specification remain
unchanged. No arbitrary part selector, patch language or caller-qualified flag
is introduced.

Preparation applies ADC selection, then this R6 exception, then the exact source
supplement. It finalizes the combined supplier list, selected-allocation roster,
assembly question and authored membership binding after both selections, before
archiving originals, mechanics binding and the existing typed seal. R3/R4 retain
C114662/2.2 kohm; all 17 references/instances, CAD assignments, footprints and nets
remain. Exact C137695 supplier raw/provenance is carried through the owned CPL
added-input revision; policy, tolerances and native pad/rotation rules remain.
No supplier assembly approval follows from stock or raw supplier flags.

The existing two-parent algebra combines the unchanged ADC 12-observation union
with the retained single-ID R6 capture. The resulting 13-observation catalog
keeps every original response/fetch time and the conservative parent window.
All named records and parts replay against this catalog. The new exact YAGEO PDF
has a derived capture manifest preserving its actual earlier HTTP timestamp;
it is not a new download. Parent/HTTP/source custody remains captured.

R6's nominal-25 C interval is 613.8–626.2 ohm. The input-current equation counts
two 2.2 kohm pullups plus the independent output bleed. Exact rational budget
values are retained with displayed decimals. Every output-bleed model uses the
adverse 626.2 ohm value. Four removal cases use the reviewed source-only derivative
and zero minimum ADC load: two retained 200 microampere stresses and two explicitly
extrapolated 20/25 microampere sensitivities. Those sensitivities are not
manufacturer guarantees below 1.6 V. Three startup cases retain the original
12.65 microfarad COUT case and the 3 microfarad/100 and 5 milliohm brackets.
Only the 100 milliohm case adopts the separately authorized 1 microsecond step;
its historical convergence evidence used old R6/4.752 V and grants no new pass.
There is no C_PAD, solver regularization, threshold or capacitor-value change.
All seven checks use the specification's 4.75 V minimum supply.

The larger load withdraws the old 0.45 V-at-10 mA diode transfer. The calculation
uses a **conditional** monotonic interpretation of the captured 1 V-at-250 mA
point at nominal 25 C and the 20 mA supply contract: 3.75 V after the diode,
3.74 V after the retained 10 mV wiring allowance, versus the DBV accuracy floor
of 3.6 V. The 1 mA loaded ground-current allowance is assumed; TI provides no
loaded maximum. Conditional U1 power/thermal rows use DBV 187.1 C/W and explicitly
retain board/temperature limitations. A supporting W-valued interval assertion,
`u1-dissipation-25c-screen`, is added under the existing rail requirement. It
bounds that same enclosure by 0.53447 W, which is (125 C recommended TJ maximum −
25 C) / 187.1 C/W rounded down. It keeps the 1 mA external calculation reserve
while the actual J2 load is zero, and uses the assumed 1 mA loaded ground current.
`engineering-calculations.json` records the exact derivation, and the captured TI
SBVS338H text, extraction manifest and PDF are hash-bound. This is a conditional
JEDEC-reference screen at 25 C only. It is not a board thermal or junction-temperature
guarantee, and the dissipation source-evaluation judgment remains. Geometry keeps all body sizes and tolerances,
changes R6 attribution to its exact sheet, and uses the revised current estimate
in the existing rail-drop model. Only the finite combined model/source version
is admitted; numerical limits are not relaxed.

The activation, complete captures, helper/descriptor pins, decisions, catalog
parents, before/after diagnostic motivation, edits and original archive enter
source identity. All source and copied capture bytes are rechecked before atomic
publication. Generated narrative and judgment briefs expose the exception; no
judgment, witness, native result or qualification is produced. Fresh native
620 ohm/4.75 V checks, independent review and supplier/physical obligations remain.


## Selected 470 ohm margin repair

The same public `--added-input power-repair=DIR` accepts a second exact finite
inventory, pinned by `fixtures/acquisition-typed/power-repair-470.json`. It does
not add a selector flag: the complete captured inventory and explicit activation
identify the admitted exception. The original 620 ohm context and source path
remain supported. Original reference adoption and the 100–150 pF envelope remain.

This version selects R6 alone as C114669 / YAGEO RC0603FR-07470RL, 470 ohm 1%,
0603. Its RC-series ordering and RC0603 dimensional/rating tables explicitly
cover the part. The derived datasheet manifest binds that family source to the
selected identity and preserves its original capture date. Historical exact
620 ohm documents remain historical. Exact C114669 supplier raw data, HTTP span,
provenance and parsed package identity enter the existing CPL closure; neither
same package nor stock substitutes for supplier orientation evidence.

The nominal 25 C output-bleed interval becomes 465.3–474.7 ohm, with adverse
474.7 ohm in every modeled case. Output budget is 11.624952 mA and conditional
input budget 12.865998 mA, including the explicitly assumed 1 mA loaded ground
allowance; this is not a manufacturer maximum. R6 dissipation is 24.349787 mW.
Exact fractions, revised thermal estimates and source pins are regenerated.
The finite geometry model retains every dimension/copper assumption and updates
its current operand, so the existing rail-drop check uses the increased load.
The 16.5 mV limit remains. Python and Rust admit the corresponding exact model
and source pins alongside the older supported versions.

The seven typical/startup controls remain. An eighth required check,
`power-removal-uvlo111-lowcin`, retains 200 microampere stress, 2 microfarad CIN,
original 12.65 microfarad COUT plus native C3, all ESR and the 0.300 V limit.
It stages the independently reviewed derivative `3b725409…` separately, preserving
all its bytes. The one-sided comparator gives falling 1.110 V and counterfactual
rising 1.135 V; this check is restricted to removal from initialized high state,
not startup or re-enable qualification. Root selected 2 microseconds, supported
by the retained hypothetical 470 ohm model's 0.5 microsecond refinement. Its
1 microsecond case failed numerically and remains UNKNOWN. No regularization or
claim that every timestep passes is introduced. Typical models remain unchanged.

Those diagnostics motivate the source change; they do not qualify a changed
board. New judgment briefs require the actual production compiled scope and
candidate. Historical R4 drafts and injected diagnostic scopes grant no credit.
Parts, named records, conservative catalog union, operative allocation roster,
CPL sources, calculation/model pins, original archive and edit ledger are
regenerated before the existing typed seal. All captured dates remain unchanged;
freshness, supplier preview, physical confirmation and new native evaluation
remain obligations. No raw private capture is added to tracked fixtures.


## Finite ADC ADDR development source selection

`--addr-tie GND|V3V3` (API `addr_tie`) selects only authored U2 ADS1115
pin 1, after purchasing/source-supplement decisions and before archive/seal.
Omitting it preserves the existing GND/address0x48 recipe. Explicit GND records
a control selection; V3V3 intentionally creates a development defect. The
unchanged `adc-ties.json` oracle still requires GND/address0x48. Neither choice
claims board completion or electrical acceptance. Unknown choices, ambiguous
U2/pin/net identities and an existing output refuse. Published bundles and
native CAD are never edited. `preparation/addr-selection.json` and the existing
edit ledger retain exact before/after circuit hashes and the finite selection.

For deterministic preparation-decision replay, pass `--evaluation-context`
with a previously captured `preparation.json` and its independently retained
`--evaluation-context-sha256` (API `evaluation_context` and
`evaluation_context_sha256`). This requires the same explicit alternative
context and exact recipe: brief, original source/plan, additions, producer and
helpers, catalog closure, profile and verification budget. Only the finite ADDR
choice and replay provenance may differ. The prior report must retain its
valid recipe identity. A pin identifies trusted captured bytes; it does not
authenticate a report fabricated by the caller. There is no free `as_of` option.
The captured decision anchor must be valid, nonfuture and admissible against the
unchanged catalog freshness/completion rules; incompatible or changed context
refuses atomically. The report bytes are retained under
`preparation/evaluation-context.json` after the common purchasing decision.
Replay preserves a historical preparation decision, not a fresh stock claim:
ordinary assembly review still evaluates freshness at the actual run start.

Each choice needs a fresh output. Existing typed-overlay manifests retain the
owned temporary selected-CPL source path, so that manifest's observation may
vary between identical replays; compiled immutable baselines and common parts
decision/dependency bytes must remain exactly equal. Production circuit hashes
and explicit selection/edit lineage intentionally differ. Native functional
rejection, seven-stage production and independent review remain separate work.


## Fixed expected-GND intent for a new ADDR development case

The optional fixed oracle is for authoring a NEW case before either candidate
exists. Root first captures the unchanged GND control under its captured
preparation evaluation context, using the current unchanged parts producer and
an ordinary retained stage receipt. This requires a separate native admission;
calling preparation does not grant or perform that capture. Old R5 output is
not a substitute: its parts/context/producer binding differs. The control
preparation itself uses no fixed-oracle option. Its `preparation.json` supplies
the later independently pinned evaluation context.

Supply all four new CLI options (or their matching Python keyword arguments):
`--expected-intent`, `--expected-intent-sha256`, `--expected-parts-receipt`, and
`--expected-parts-receipt-sha256`. The intent path must name that exact retained
receipt's `run/out/connectivity-intent.json`; its adopted target must be
`build-generated/connectivity-intent.json`. An explicit `--addr-tie` and the
control evaluation context/hash are required. The complete ordinary receipt is
replayed through the existing validator without running its tool/version probe.
It must be completed/exit0, an ordinary current-source compile-parts declaration,
and bind its full control input identity, output, report, current parts/circuit/
layout/ratings dependencies and captured preparation context. The only extra
control inputs admitted beyond the original prepared inventory are the existing
requirements-stage source copies and, for the build path, its exact empty parts
target placeholder. This is an explicit local root-pin trust boundary, not
cryptographic authentication against a host that can forge the receipt and pin.

Before archive/seal and after common purchasing/supplement processing, the
preparer captures the original output bytes under the fixed immutable path
`fixedexpected/connectivity-intent-addr-gnd.json`, alongside copied receipt/report
provenance. It redirects exactly all six existing parity checks to that one
intent. Their kind, predicate, multiset, netlist prerequisite, search limits,
other parameters and requirement bindings remain unchanged. The fixed path
cannot be a native/mutable stage subject. The existing edit ledger, recipe and
full prepared input inventory bind the expectation, receipt identity/binding
and control context. No validator or mutable-input exception is added.

Both GND and V3V3 preparations use the same fixed expectation. The wrong ADDR
case may fail both parity and adc-ties; the functional defect need only survive
native ERC/DRC. The corrected generated GND intent must byte-equal the independent
expected intent. Complete AUTHORED acceptance baselines must match across
variants/replay. Existing `rating_lineage.strip` alone defines that authored
view; derived rating receipts and their derived baseline IDs are recorded
separately and may differ per run. No new normalization is permitted.

Thermal source admission validates the two TI manufacturer-text pins against
`_load_overlay`'s already admitted additive inventory before the overlay copies
those files, while hashing the two existing PDF/manifest sources in the owned
revision. The later typed seal revalidates/copies the additive bytes. All four
original source identities remain in calculations and the judgment brief;
missing/drifted bytes still refuse. This changes source availability handling,
not the conditional25C screen or its engineering limitations.

Pure fixture receipts simulate process execution while computing real parts
intent/report bytes. They are software controls, never a native control receipt,
a target preparation, G2 acceptance or a full campaign repair-loop proof.

## Explicit final-group source derivation

Add `--final-groups` to the same preparation command (Python `final_groups=True`)
to emit `inputs/acquisition-block-source.json` and select it with the existing
`build-plan.json` field `placement.block_source`. Omitting the option retains
the ordinary preparation path. No caller-authored block JSON is accepted.

The finite derivation preserves the captured power/analog/host groups of 7/7/3
physical references, with member anchors J1/J3/J2. Each connector remains in its
existing group; explicit fixed/shared allocation lists are empty. Existing native
connector locks and mechanics checks still apply. Eligibility retains the
existing all-front mechanics contract, and the nominal stackup stays 1.2 mm.
The original five locality obligations and their eight 3 mm physical pad legs
remain unchanged. Ten additional named `single-leg` relationships capture
coordinator root706's engineering placement choices adopted 2026-10-29:

| Pad pair | Native net | Maximum |
| --- | --- | --- |
| J1.1 to D1.2 | /VIN | 5 mm |
| D1.1 to U1.1 | /VIN_PROTECTED | 5 mm |
| R5.1 to C1.1 | /VIN_PROTECTED | 3 mm |
| R6.1 to C2.1 | /V3V3 | 3 mm |
| J3.1 to R1.1 | /ANALOG0_IN | 5 mm |
| J3.3 to R2.1 | /ANALOG1_IN | 5 mm |
| R1.2 to U2.4 | /ANALOG0 | 3 mm |
| R2.2 to U2.5 | /ANALOG1 | 3 mm |
| R3.2 to J2.2 | /SDA | 5 mm |
| R4.2 to J2.3 | /SCL | 5 mm |

These additions are coordinator engineering choices, not owner or datasheet
mandates. `preparation/final-group-engineering.txt` captures attribution and the
exact single-quantity quotes, such as `J1.1 to D1.2 pad distance shall be at most
5 mm.` Each pair has both a top-level citation and a captured-source provenance
citation. All 18 legs must classify as hard; advisory downgrades refuse. The
producer requires complete coverage of all 17 electrical members, and updates
both operative locality copies before sealing. One existing `same-face-only`
template is selected per leg. No universal radius, clearance relaxation or
qualified-role claim is introduced. Native search, packing and routing remain
unverified. The same additive choices must precede the fresh control, wrong
variant and repaired case freeze; old published runs are unchanged.

Before sealing, the producer verifies exact membership/anchors and selected
physical instances, resolves physical pin/net identities against the captured
symbol libraries, and refuses missing, foreign, duplicated or changed legs,
limits, source citations or thickness. It runs existing block-source admission.
The generated file enters the requirement map before the operative map and
mechanics hash are computed. Its source hashes and output hash enter the edit
ledger; the helper and its dependencies enter the preparation identity. There
is no map/mechanics/source hash cycle and no authored native board identity.
Before publication, the sealed copy must equal the admitted record and its
derivation from the sealed source inputs; source admission runs on that copy.

The ordinary public build then runs its existing schematic-stage block-source
producer, native placement binding, final artwork proof and package replay.
Source preparation and source preflight establish none of those native results.
Use a fresh prepared bundle and fresh build under separate coordinator admission;
never add this file to an old sealed run. Candidate-specific judgments, independent
verification, full engineering review, supplier and physical obligations remain.
This option composes with the finite ADDR/fixed-expectation context. The same
option and final source must be used for the independently captured control and
both later candidates; source changes invalidate the old producer receipt.

The complete 17-member pure grading regression requires
`AGENT_EE_GROUP_FACTS=/absolute/path/retained/placement-probe.json` from the
acquisition control. It transforms four footprint fact records into the reviewed
constructive geometry, exercises full `judge`/`check_complete` and retains the
original eight-leg unknown control. This authored fact transform is not a saved
board, native DRC, search convergence or routing result.

### Explicit v4 acquisition roles

`--role-context CONTEXT.json --role-context-sha256 SHA256 --role-source ROOT`
opts into the finite three-role consumer. All three arguments are required. The
context schema is `agent-ee.acquisition-role-context/1`, with `reference` equal to
`acquisition-control`, a complete `files` path/SHA256 closure, and exactly the keys
`acq-tps7a2033-power`, `acq-ads1115-two-channel`, and `acq-jst-ph-i2c` in `roles`.
Each role entry has `block`, `block_sha256`, `composition` and `report` blob pins,
plus `run_locations` and `repository_locations` for the existing source verifier.
The files closure contains the entire immutable blocks, their metadata, composition
plans and exact source reports. Original run records stay in their retained lanes.

`host_observation` supplies the coordinator's independently admitted
`policy_files` and `runtime_files` observation through the existing verifier host
API. This producer does not probe native tools or silently invent an observation.
The policy observation must equal current source rules; this option cannot waive
implementation drift. The observation and all context bytes are pinned into preparation identity.
`reference_blocks.validate_block` replays actual declared source runs and must
return the exact pinned verified report; a report or status flag by itself cannot
qualify an input. The original v4 captures with `runs: []` therefore refuse until
the coordinator supplies fresh immutable metadata binding their actual qualified
run. Historical files must remain preserved.

`application` explicitly contains `j2_external_load_a: 0`,
`j2_external_drive: false`, `bus_capacitance_pf: 100`, and `temperature_c: 25`.
This version refuses another bus bound, load, drive or temperature. In particular,
a 150 pF ordinary recipe remains supported without roles; v4's captured 100 pF
role contract does not automatically expand to that variant. Supplier/physical
obligations and the existing engineering judgments remain mandatory.

Before the typed seal the consumer captures `reference-roles/`, emits three
ordinary composition instance records and fresh interface/membership/model/layout
check declarations, and maps every source and all six deferred rows. Source
conditions remain captured and appear in `reference-roles/admission.json`.
The existing source-evaluation, return-path and host-bus judgment briefs cite this admission;
no historical judgment is copied. The optional `scope.block_instances` and overlay
`block_requirements` transport use the existing derived-source accounting; missing,
extra, colliding or post-seal rows refuse. Existing spec-span obligations remain.

Root provenance is limited to one explicit root source with no hierarchical
boundary ports and an exact captured membership selector. Each role compares its
selected references and all unit UUIDs; the union of all selected roles must equal
the complete fresh native physical inventory. Outside parts and overlaps refuse.
Ordinary child composition retains its whole-occurrence behavior. A successful
source preparation is not native role, board, engineering, assembly or G4 acceptance.


### Finite v5/v6 capacitance cohorts

Role contexts now admit only these ordered `(power, analog, host)` version sets:

| Cohort | Declared capacitance | R1 and R2 | Host role operand |
| --- | --- | --- | --- |
| `(4,4,4)` | exactly 100 pF | original captured allocations | 100 pF |
| `(5,5,5)` | 100 through 150 pF | C22548 | conservative 150 pF |
| `(5,6,5)` | 100 through 150 pF | both C118155, PANASONIC ERJ3EKF1001V | conservative 150 pF |

The existing role context options and schema remain unchanged. For the new
cohorts, each block captures the exact bytes of
`roles/adaptation-policy-150.json`, matching the installed
`data/acquisition-role-policy-150.json`. Its own application contract cites this
policy and its 150 pF boundary. The finite policy retains every original selected
allocation except the two explicitly admitted analog resistors in revision 6.
Revision 6 must be genuinely recaptured and independently source-qualified; a
caller cannot select it by renaming revision 5 or by setting a qualified flag.
Different versions across roles are permitted only in the listed cohort.

The selected application capacitance must equal the parsed brief exactly.
Literal context values are canonical integer/decimal text in 100..150; booleans, floats, exponents,
nonfinite values and excess precision refuse. Nominal temperature is integer
25 C; zero external J2 load and no external drive remain mandatory. The original
specification's 100 pF statement and R5's closed-incomplete evidence stay in
lineage. The new envelope cites delegated engineering adaptation policy, not a
manufacturer rating or a rewritten historical requirement.

For positive pullup resistance, `.8473*R*C` increases with capacitance. The host
role keeps the unchanged four interval equations with a fixed conservative
150 pF operand (worst rise 282.40509 ns against 300 ns); the full-board interval
continues to use the exact declared capacitance, including interior values such
as 125 pF. This imposes no physical minimum capacitance. Both operands are labeled
in `reference-roles/admission.json` and supplied to the fresh host-bus judgment.
All six deferred rows, existing checks and fourteen judgments remain required.
The exact parts matcher, all17 physical union/UUID check and supplier/physical
obligations are unchanged.

Block, composition and report paths must be isolated for each role. A new analog
source may coexist with the exact qualified v5 power/host captures even though
its source repository and native schematic differ; only each block's own
application contract is tested against the common policy. Every capture and its
actual run/report still must pass the ordinary source verifier. All source code
and data must be integrated and frozen before qualification because
`policy_files()` remains exact. Unverified v5 captures with empty runs refuse;
these source changes and CPU tests confer no native or engineering acceptance.


For a common frozen context reused across public brief cases, only the new
150 pF cohorts also permit this finite source declaration:

```json
{"bus_capacitance_pf": {"from": "public-brief", "policy": "acquisition-150pf-v5"}}
```

`read()` first replays that cohort's captured policy and actual native source
qualification, then returns an explicitly derived document whose application
contains the canonical brief capacitance. `application_binding` preserves the
original source application and records the public-brief binding. The public
preparer retains the original context bytes separately in `context-input.json`;
`context.json` and `admission.json` carry the derived application. Both primary
and alternate role closures can therefore stay byte-identical across cases.
This declaration cannot supply a different bound, another policy or extra fields;
v4 refuses it. Literal mismatches still refuse. No qualification, stock clock or
source observation is regenerated by this binding.

## Finite filter-resistor selection and public catalog exclusions

The optional source-only resistor path admits exactly Panasonic
`ERJ3EKF1001V` / `C118155` as the alternative for **both** R1 and R2,
replacing `RC0603FR-071KL` / `C22548`. It retains Device:R, the existing
0603 footprint, 1 kohm / 1%, pin selectors, references, unchanged 1.2 mm board,
and every existing electrical, placement and manufacturing limit. ADC carrier
selection and the existing R6 repair run first and retain their existing rules.

The Python `prepare` API adds these optional keyword arguments, also exposed
as their corresponding `--kebab-case` CLI flags:

- `resistor_context`, `resistor_context_sha256`, `resistor_source`: all three
  are required together. This common source pool requires the existing captured
  ADC context and selects its own role context; do not also supply `role_context`.
- `public_context`, `public_context_sha256`: a separate, pinned public case
  policy. An absent file or `catalog_policy: null` keeps the ordinary selected
  parts unchanged. Neither implies an observed stockout.

The common resistor context is an exact-field JSON document:

```json
{
  "schema": "agent-ee.acquisition-resistor-context/1",
  "reference": "acquisition-control",
  "files": {"<relative captured path>": "<exact SHA-256>"},
  "role_contexts": {
    "C22548": {
      "root": "cohorts/base/source",
      "context": {"path": "cohorts/base/context.json", "sha256": "<SHA-256>"}
    },
    "C118155": {
      "root": "cohorts/resistors/source",
      "context": {"path": "cohorts/resistors/context.json", "sha256": "<SHA-256>"}
    }
  }
}
```

The finite `files` inventory contains exactly the ten `EVIDENCE` files in
`acquisition_resistor.py`, both context documents and both complete role-source
closures prefixed by their `root`. Put this outer context document outside that
inventory. Read the actual role-context `files` maps to construct those closures;
do not infer qualification from filenames or copy unrelated campaign material.
Each common cohort is consumed through `acquisition_roles.read`, which replays
its actual source qualification. The allowed production combinations are
`(power5, analog5, host5)` and `(power5, analog6, host5)`. The latter derives
from the same 100..150 pF adaptation policy. Power and host block hashes must
be identical across both contexts. Analog6 must carry the exact Panasonic
manufacturer PDF, native purchasing identity and independently qualified
membership. Source qualification and later native generation remain separate
owner-coordinated steps; this preparer creates neither reports nor native receipts.

Both common qualified cohorts and all captured catalog/datasheet/supplier bytes
are identical across cases. A public brief may differ; the public policy is the
only catalog-scenario difference. Required source-role qualification evidence
is permitted. Earlier campaign/package acceptance testimony and private evaluator
material are not producer inputs. The declared-capacitance application binding
must follow the role consumer's admitted policy; a stale or mismatched context
refuses rather than changing captured qualification metadata.

The public policy uses the exact `agent-ee.case-context/1` protocol:

```json
{
  "schema": "agent-ee.case-context/1",
  "catalog_policy": {
    "kind": "evaluation_exclusion",
    "label": "evaluation scenario; not an observed supplier stockout",
    "exclude": [{"lcsc": "C22548", "mpn": "RC0603FR-071KL"}]
  }
}
```

Entries must be unique exact `(lcsc, mpn)` identities present in the frozen common
catalog. Unknown fields, wrong labels/kinds, empty exclusions, mismatched parts
or source hashes refuse. Exclusion makes an identity ineligible across the
entire selected board. This finite path reselects both filter instances from
its qualified pair; any other excluded selected allocation without an admitted
qualified alternative refuses. It never silently substitutes an unqualified part.

The source revision follows R6 and source-supplement geometry finalization and
precedes selected-CPL closure, membership binding, final grouping and role
application. It derives a conservative full-board stock union without changing
any raw response or parent observation date. Supplier data retains the exact
HTTP result span and retrieval provenance; the CPL namespace change is an
explicit derived provenance record, not a new supplier fetch. Decisions record
each affected instance, excluded/selected identities and actual source
qualification record ID. The policy label and source hash are retained in
`preparation/catalog-policy.json`, `preparation/resistor/decision.json`,
`preparation/selected-part-input-closure.json`, the preparation report and fresh
assembly judgment sources. Downstream package evidence must retain these inputs.

The Panasonic maximum body is 1.75 x 0.95 x 0.55 mm for both R1 and R2. The
only admitted geometry derivatives are those of the existing exact 470-ohm and
620-ohm R6 models, with every other model value and checker hash retained.
Mechanics admission requires the original Murata/R6 custody plus the exact
Panasonic manufacturer PDF and mounting-guide bytes. No geometric limit,
mechanical constraint or physical/supplier obligation is waived.

### Generating the alternate source before qualification

Target `prepare` requires both genuine common qualified cohorts. Root can first
create the alternate source through the separate finite, unqualified entry:

```python
from agent_ee import acquisition_resistor, requirement_map, storage

seed_pin = storage.identity(requirement_map.input_inventory(base_v5_source))
report = acquisition_resistor.derive_source(
    base_v5_source, new_analog6_source,
    source_inventory_sha256=seed_pin,
    capture_source=panasonic_capture_root,
)
```

`base_v5_source` is an explicitly frozen, owned source-generation seed containing
the complete acquisition inputs, its existing R6 repair, source-corrected body
model, captured libraries, layout and original project sheet. Root retains the
inventory pin independently; a changed seed, capture, output collision or alias
refuses. `panasonic_capture_root` contains the ten exact `EVIDENCE` paths.

This entry does **not** consume a public case policy or an analog6 qualification
record. It derives the captured part/stock/datasheet records and correct body
model, then invokes the existing `parts_stage.compile_parts` and pure
`schematic_emitter.emit_schematic` without a native text workspace. The emitted
schematic carries the actual selected purchasing properties. All17 physical
references, library/footprint identities and placed unit UUIDs must remain equal
to the seed. The existing project settings and PCB remain unchanged **native
seeds**; no regenerated PCB or native export is claimed. The dated stock is
source documentation, not live eligibility. The diagnostic emission and parts
production records are real pure-producer outputs, not native qualification.

Root must next run the separately admitted native generation/export/check and
source-qualification workflow, refresh analog6's own application/interface/
interval/composition/file citations, and assemble the two genuine common
cohorts. `preparation/resistor/source-generation.json` explicitly remains
`unqualified`; it cannot stand in for target-selection or CPL admission.

Public protocol basis: `agent-ee.case-context/1`, contract v2,
SHA-256 `61cd3283f108dd629bd6acf1cb6976cb317034dcd6a3a40736b5e49b828b2fee`.
Section E permits ordinary genuine common source-role qualification evidence
and differing public briefs; earlier board/campaign acceptance testimony and
private evaluator material remain prohibited.

### PH connector body correction

Fresh preparation corrects the captured JST PH body model before final grouping,
role binding and sealing. The top-entry body depth is 4.5 mm, with the pin row
1.7 mm from one edge: local Y therefore spans -1.7 to +2.8 mm. J1, J2 and J3
use a +0.55 mm Y center. The previous +0.25 mm center displaced the modeled body
by 0.30 mm. The captured manufacturer sheet and footprint F.Fab body rectangles
agree on the corrected bounds.

`acquisition_connector_geometry.py` admits only exact derivatives of the six
existing geometry models. It retains each preceding model at
`preparation/connector-geometry/original-model.json` and records the correction
in the preparation report. Admission requires the pinned JST text, PDF,
manifests and both captured footprints, in addition to each preceding model's
source custody. The original source archive remains unchanged.

Both placement mechanics and the unchanged native engineering geometry checker
consume the corrected `analysis/geometry-model.json`. Widths, depth, height,
other packages, clearance limits and the checker are preserved. A new model
hash requires fresh checks and independent judgments; it does not revise an
earlier run's outcome or establish physical connector clearance.
The Rust placement engine independently checks the admitted model and complete
source declaration. Rebuild it with `scripts/build_placement_engine.py` when
adding model versions; updating only the Python producer leaves the native
consumer unable to admit the new model.

### Refreshing separately captured resistor stock

The base ADC catalog refresh does not refresh R6 or the optional Panasonic
R1/R2 alternative. Their separate catalogs contribute their original timestamps
to the final conservative union. All selected captures must remain within the
declared stock-age limit through final review.

The finite October 30, 2026 refresh preserves the exact selected identities
C114669 / RC0603FR-07470RL and C118155 / ERJ3EKF1001V. It changes only the two
catalog/response pins for R6 and the catalog, response and regenerated named
part-record pins for Panasonic. Manufacturer and supplier documents, activation,
SPICE models, component values, geometry sources and age limits remain intact.
Use the corresponding new captured input directories and rebuild qualification
and context records under the new source policy. Historical captures and failed
runs retain their original bytes and outcomes. A fresh capture establishes a
dated stock observation; supplier and physical qualification remain separate.
