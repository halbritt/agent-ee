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
The complete operative locality document and citations are copied unchanged:
five obligations contain eight physical pad legs, each bounded by the existing
3 mm requirement. One existing `same-face-only` template is selected per leg.
No new radius, clearance, geometry limit or qualified-role claim is introduced.

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
