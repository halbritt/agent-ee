# Compile a design statement

`agent-ee design` is the general path from explicit circuit and board decisions to
a captured engineering build. For requirements text, use the bounded
[`agent-ee design generate` loop](design-generate.md). Design text generation is
performed via `agent-ee design generate`; `agent_ee.generate` is an internal
module (not a root CLI command), and `generate.py` remains an internal driver.

```sh
agent-ee design capture fixtures/design-examples/i2c-sensor.json --out captures/i2c
agent-ee design compile fixtures/design-examples/i2c-sensor.json \
  --captures captures/i2c --out bundles/i2c
agent-ee build run bundles/i2c/build-plan.json --out runs/i2c
```

Use new output directories. Capture needs network access and local KiCad libraries
(default `/usr/share/kicad`; override with `--kicad-root`). Compile needs neither
network nor a native KiCad process. Native builds need the documented
[build runtime](build-workflow.md), including its router and placement engine.
In this campaign, run native jobs through `native-admit`; the retained run script
in the evidence directory records the exact production image and mounts.

## Statement format

The schema is `agent-ee.design/1`. The complete examples are
[`i2c-sensor.json`](../fixtures/design-examples/i2c-sensor.json) and
[`555-oscillator.json`](../fixtures/design-examples/555-oscillator.json).
Unknown fields are refused.

| Field | Design decision |
| --- | --- |
| `name` | Short project identifier, also used for native filenames. |
| `requirement_prefix` | 1–16 uppercase ASCII letters/digits, beginning with a letter; used for generated source requirement IDs. |
| `intent` | One paragraph describing the design and its limits. |
| `board` | `width_mm`, `height_mm`, `thickness_mm`, `copper_layers`, `max_component_height_mm`. |
| `parts` | Object keyed by reference; each part has `lcsc`, `value`, `lib_id`, `footprint`, `group`, and optional `connector: {rows, columns}`, `datasheet: {url, sha256}` (see [Datasheet sources](#datasheet-sources)) and `cpl_registration` (below). Assembly type comes from the native footprint attribute. |
| `nets` | Object mapping each net name to a list of `REF.PIN` strings. Names have no native `/` prefix. Each net needs at least two physical terminals. |
| `no_connects` | List of `REF.PIN` strings. Every symbol pin needs one net or a no-connect entry. |
| `power_flags` | Unique net names that need virtual `PWR_FLAG` symbols. |
| `ground` | The declared ground net; currently `GND`. A virtual GND symbol supplies its native power name. |
| `connectors` | List of `{reference, edge, position_mm, inset_mm, rotation_deg, insertion}` plus `local_mating_axis` for `through_edge`. Edges are `top`, `right`, `bottom`, `left`; insertion is `from_top` or `through_edge`. `position_mm` is measured from the edge's start corner along its direction. Seen from the front, the edges run clockwise: `top` starts at the top-left corner and runs east, `right` starts at the top-right corner and runs south, `bottom` starts at the bottom-right corner and runs west, and `left` starts at the bottom-left corner and runs north. Rotations are multiples of 90 degrees and are normalized modulo 360. |
| `decoupling` | List of `{capacitor, device, supply, return, max_distance_mm}`. Each pair is `[DEVICE.PIN, CAPACITOR.PIN]`; both endpoints must share a net. |
| `groups` | List of `{name, anchor, title}`. Members come from the parts' `group` fields. |
| `plane` | `{net, layer}`; choose a declared net and `F.Cu` or `B.Cu`. |
| `functional` | Nonempty list of `{text, witnesses}`. Each requirement needs independent topology witnesses. |
| `bench` | Nonempty list of physical follow-ups. These remain physical obligations, separate from executable file checks. |
| `placement` | Optional `{budget_s, seeds, steps_per_seed}`; defaults are 600 seconds, 4 seeds, and 100000 steps per seed. |
| `mechanics` | Optional `{access_owners, packages}`; see below. Omitted means no declared body/access model. |

The initial sourced fab template supports conventional FR-4, two copper layers,
1 oz outer copper and nominal 1.2 mm thickness. Other layer counts and nominal
thicknesses are refused explicitly. Multi-unit symbols and custom pads are also
outside this compiler's current subset. Symbol and footprint pad-number sets
must agree. Repeated physical lands with one number are allowed in that set,
but a repeated-number **decoupling endpoint** is refused: the compiler cannot yet
derive the native land UUID. Choose an unambiguous package or extend that
compiler capability; no pad is guessed.

## Supplier pad correspondence and orientation

Supplier pad numbers may differ from the symbol/native footprint numbers. The
compiler can register footprints without a pad named `1`, with composite supplier
pad numbers, or with differently numbered repeated plated lands. The ordinary
pin-1 frame and polarity checks remain in use for previously supported parts.

Orientation considers all four quarter turns without using the author's map or
circuit nets to select one. Independent pad identities or a single admissible
copper registration can establish the turn. Ambiguous geometry requires matching
captured supplier and native symbol pin names; an author declaration cannot
resolve it. When at least two unique pad numbers occur on both sides on identical
copper surfaces, their coordinate-wise median centre displacement supplies the
translation datum. Otherwise it centres the copper bounding boxes. Every native and supplier land needs
certified positive copper overlap on the same surfaces. Possible copper contact
with an unrelated identity on any shared surface refuses. These checks establish
registration, not physical assembly suitability; the existing overlap diagnostic
and its warning threshold remain separate.

Electrical identity has an explicit basis in `design-compile-report.json`:

- Equal pad numbers must overlap only the same native terminal number. Geometry
  cannot relabel a swapped electrical pin.
- A composite supplier number must be an exact concatenation of the overlapping
  native terminal numbers. All distinct terminals in one supplier land must have
  the same connected net. Missing nets and no-connects cannot authorize a short.
- A differently numbered plated land can be located automatically only when
  it corresponds to one land of a repeated native number, with uniquely
  overlapping drilled apertures. Each aperture must have unambiguous ownership
  at its pad centre under the existing `100000` nm tolerance. At least two identified
  electrical terminal groups must establish the automatic orientation. This is
  positional evidence for the repeated land, not proof of its physical function.
  A separately listed supplier symbol pin cannot acquire its identity by this
  positional rule.
- Other numbering needs a declaration. It records an author-supplied electrical
  identity, checked against the captured copper and circuit nets; it does not
  independently authenticate that identity against a manufacturer datasheet.

An optional part field declares a complete map from supplier numbers to lists of
native numbers. For example, an original
connector whose supplier calls one joined land `JOIN` could declare:

```json
"cpl_registration": {
  "pad_map": {"JOIN": ["K1", "K2"], "S0": ["S0"], "S1": ["S1"]}
}
```

The map must cover every supplier number and agree with every overlapping land.
It cannot override a conflicting common pad number or merge distinct circuit
nets. An optional legacy `rotation_degrees` can assert the independently derived
quarter turn, but never selects it. Registrations requiring reflection, uncovered lands and ambiguous
rotations refuse. Geometrically indistinguishable reflections cannot be detected.
Polarized parts still require their existing pin-1 polarity gate; this declaration
cannot replace it.

The compiled CPL policy retains each registration in `pad_registrations` by
designator. At package time, `cpl_rotation` rechecks it against the captured board
and supplier bytes and the sealed board pad nets, and checks the policy angle.
Its `pin1` record names `electrical_pad_correspondence` as the orientation reference
and retains the pad map, physical land pairs and the evidence used at that boundary.
`orientation_proof` records independent pad identities, unique copper geometry or
captured symbol pin names. Compiler-derived aliases are checked as a supplied
policy map at package time, so their package identity proof is
`declared_map_and_copper`; it does not repeat the compiler's automatic plated-land
proof. Native symbol-name fallback uses captured board pin functions or the
sealed native netlist with matching part and pad-net bindings.
Supplier placement preview and physical validation remain required.

A footprint may mix SMD and plated through-hole terminals under one explicit
native assembly attribute. CAD assignment records the mixed pad technology and
uses that attribute as the primary assembly type. Numbered contacts stay
electrical for symbol parity unless a captured mechanical declaration says
otherwise. Stock `MP` pads do not count as connector positions, preserving the
existing position-count rule. Shared-number SMD/through-hole lands still need the
existing thermal-via declaration; missing or competing assembly attributes refuse.

Supplier `POLYGON` pads use exact decimal vertices from the
[EasyEDA standard PAD record](https://docs.easyeda.com/en/DocumentFormat/3-EasyEDA-PCB-File-Format/#pad).
Conversion removes consecutive duplicate endpoints and the closing duplicate,
preserves concavity, and refuses self-intersections or zero area. The pad's centre
must match the outline bounding-box centre within the source tolerance, and its
size, after the declared quarter turn, must match that box. These checks also
precede placement of a plated drill at the recorded centre. Non-quarter-turn pad
rotations refuse. Polygon vertices are already in canvas coordinates and are not
rotated a second time; the checked centre remains the position used for pin-1
classification and registration anchors. The geometry
kernel uses the resulting polygon exactly, including under registration; it
does not replace it with a rectangle or convex hull.

Supplier-parser refusals during capture name every affected `parts.<ref>.lcsc`,
the LCSC id and the parser mode. Unsupported supplier capability carries
`[tool_gap]`; malformed or inconsistent source metadata does not. Missing-model
capture and independently unresolved orientation also carry `[tool_gap]`.
Feedback keeps the part field and records the tool gap. The `captures` infrastructure
path is not classified as a design field. The full supplier response remains
in the failed capture directory for diagnosis.

## Functional witnesses

Wiring and intended function are separate statements. This example keeps the
connector's intended SDA pin fixed even when a circuit author changes `nets`:

```json
{
  "text": "J2 pin 3 shall carry SDA with a 4.7 k pull-up to V3V3.",
  "witnesses": [
    {"id": "j2-sda", "type": "pin_net", "terminal": "J2.3", "net": "SDA"},
    {"id": "sda-pullup", "type": "element_between", "class": "resistor",
     "nets": ["SDA", "V3V3"], "value": "4.7k"}
  ]
}
```

`pin_net` asserts a reference/pin on a named net. `element_between` asserts an
element class and value between two distinct nets. It can specify `lib_id` and
`pins` (two physical pin numbers); otherwise the compiler uses the corresponding
standard `Device` symbol with pins 1 and 2. Supported undirected classes are `resistor`,
`capacitor`, `inductor`, `ferrite`, and `fuse`. Polarized capacitors, diodes and
LEDs need a separate `pin_net` witness for each pin; `element_between` cannot
establish direction. The compiler derives classes from the `Device` R, C, C_Polarized, L, D, LED,
Fuse, Polyfuse and FerriteBead name families, including underscore variants, and refuses
class contradictions. Polarized variants remain polarized.
Other explicit classifications are design declarations, not independent
identification of a symbol's electrical meaning. Rule IDs must be unique across
the design. The compiler converts label nets to `/NAME` and keeps the GND power-symbol net bare.

Each functional requirement maps to the generated `topology_rules` check. Exact
netlist parity maps only to the separate native topology requirement. Witness
values and pins are not inferred from `nets`; changing the wiring therefore
does not rewrite the functional oracle. The [topology rule evaluator](topology-rules.md)
checks only those pin assignments and class/value relationships. "Independent"
means authored separately from `nets`; it does not mean independently reviewed.
The tool cannot establish that witnesses imply the requirement's prose. Generated
functional clauses name only the witness assertion as executable, and label the
original text in a separate prose context section as a design-stated purpose
that the witnesses do not establish. Its obligation is `context`, not `executable`.

Put frequency, accuracy, surge survival, suitability and other engineering or
bench claims in `bench`. For example, keep "U1.1 is GND" as a functional witness
and put "Measure 1 kHz at 0.1% and test 100 V surge survival" in `bench`. A sentence
that combines topology and behavior must be split across these fields. Bench
spans are physical obligations and have no executable topology check. A passing
witness never proves omitted behavior.

## Capture and compilation

Capture downloads exact JLC stock responses, datasheets, supplier component
records and models. [Datasheet sources](#datasheet-sources) gives the order of
datasheet sources, the optional declared source, and the refusal for a part
without a complete PDF. Partial captures remain on disk after failure; each
capture needs a new directory.
Stock timestamps are generated by `jlc_stock_capture` and never edited. Builds
require stock evidence no older than 24 hours. Compilation refuses future
catalog times and prints the hours remaining under that policy, including a
negative value for stale stock. A successful compile does not reserve stock.

Compile requires `capture.json` and compares its complete file/hash inventory
before using captured inputs. Missing, changed or added files refuse. Supplier
response-span receipts are mandatory as well as provenance. This detects
accidental edits; it does not authenticate a vendor response against forgery.

Local symbol subsets and footprints are retained in `cad/`. Model downloads use
the footprints' stock model references through the public
`models3d.fetch_stock_references` function; no placed board is required. If a
model file is missing from the model repository at every revision tried, capture
refuses. The refusal names each part, its footprint, the model file and the
revisions tried; `models-report.json` keeps every model's result. Supplier
capture retains the full response, the exact UTF-8 byte span of its top-level
`result` object, and provenance plus byte offsets and hashes. Nothing uploads
project files or places an order.

If a footprint references a model absent from the stock repository, capture can
take `--model-sources FILE`. The JSON object maps exact referenced model paths to
`{"source_url": "https://…", "sha256": "…"}` records. Only HTTPS URLs without
embedded credentials or fragments are accepted, and paths absent from the
selected footprints refuse. The declared URL takes precedence for that path;
other references still use the stock resolver. Capture retains the declaration
in `model-sources.json` and verifies the downloaded bytes against its digest.
An incorrect digest refuses before publishing the final cache entry and leaves
the downloaded bytes in a separate `.model-rejected-*` file for diagnosis.
The model receipt records `source: declared-url`, the URL, size and digest;
compile retains and verifies that receipt and the model bytes. This verifies
agreement with the declared content digest and records the URL. It does not
authenticate the source's identity or establish model dimensions or suitability.
The generate loop does not yet forward this option; a missing stock model still
refuses there, with a tool-gap classification.

Compile generates the spec and byte-located record, selected-part records, CAD
closure, circuit, layout, envelope, sourced project rules, groups, placement and
locality contracts, planes, supplier CPL policy, assembly profile, typed
obligations, requirement map and `agent-ee.build-plan/2`. It replays the existing
parts producer, pure schematic emitter and requirement compiler in a temporary
preflight before publishing the bundle. It never publishes a partial bundle.
The same design and captured bytes produce identical bundle bytes, regardless
of the output directory. Capture itself records real timestamps and is not
expected to reproduce identical bytes across network runs.

The JLC fab profile and original selected-fact transcription are package data.
The instantiated profile cites only `fab-sources/selected-facts.txt`; no vendor
PDF or captured vendor page ships in the repository. Project rule keys and
minima come from `scaffold.produced_rules`, including the native
`min_copper_edge_clearance` key. Default clearance uses the sourced fab floor under the recorded owner decision.
Default track width is at least the pinned native routing default (0.2 mm);
routing via sizes also preserve native defaults and meet drill and annular-width rules.
These routing defaults are not fabrication limits. `project-rule-derivation.json` binds both sources and the selected values. CPL rotations come from footprint-local geometry
and captured supplier pin-1 geometry before a board exists. Supplier preview
remains a physical delivery obligation. Compile and package call the same
`cpl_rotation.select_polarity` function: polarity is required for `D#`/`LED#`
references and two-pad parts numbered 1/2 whose board pin 1 names a polarity.
Both use `check_pin_polarity` for the selected supplier pin. Thus a battery with
board pin 1 named `+` and unresolved supplier polarity is refused at compile,
with its reference. The `C` cathode alias applies only to `D#`/`LED#`, so a
phototransistor collector does not select this gate.

An unnamed board pin 1 establishes no polarity, including on
`Device:C_Polarized` and `Device:Fuse_Polarized`. No family convention is
inferred. For references outside `D#`/`LED#`, these parts can compile; their
compile report and package record explicitly say `assessment: "ungraded"`.
Passing pin-number geometry does not verify their physical polarity. Named
pins come from the captured symbol at compile and the board pinfunction or
guarded sealed-netlist fallback at package.
The compiler also shares pad-set comparison with the CPL gate, refuses reflections and incompatible pad sets, and
uses component overrides when shared footprints need different rotations.

The compiler refuses explicitly marked standard passive size mismatches (such as
catalog 0603 and `C_0805_2012Metric`). `design-compile-report.json` records measured
copper/hole overlap at the declared CPL registration. This is diagnostic; no
universal overlap threshold has been justified, and no new package claim is made.
Copper overlap below 0.5 produces a part-named warning in the report and on stderr;
it remains diagnostic, including for the intended BME280 footprint. Size agreement
is a compile refusal, not a claim established by the bound `parts` check.
The report also warns about footprints with no model reference, whose height
cannot be established at package. CAD assignment checks do not prove full land
pattern suitability.

The manufacturing clause binds the generated `fab_package` and a following
`fab_roundtrip` check of that sealed package, as required for typed file
qualification. The roundtrip is an additional check; the package gate remains
the generation done check.

## Datasheet sources

Capture needs one complete PDF for each purchased part (`lcsc`). A complete PDF
starts with the PDF header and ends with the `%%EOF` end-of-file marker
(trailing whitespace is ignored). This is an envelope check, not a PDF parse:
a file damaged inside that still ends with `%%EOF` passes. Nothing checks that
a PDF describes the purchased part. Any source, the cascade included, can serve
another part's datasheet, and a declared `sha256` fixes the exact bytes, not
their applicability. For each LCSC id, capture tries these sources in order and
stops at the first complete PDF:

1. the source declared in `parts.<ref>.datasheet` (below);
2. JLC's hosted file: `dataManualFileAccessIdUrl` in the captured JLC row;
3. the manufacturer link in the same row: `dataManualOfficialLink`;
4. the [datasheet source cascade](datasheet-cascade.md), which reads
   `dataManualUrl` from the captured JLC row. Capture declares only the
   cascade's JLC tier. It has no DigiKey capture or catalog index for the part,
   and it does not run Exa search.

A part with a declared source uses that source only. Every other part goes
through sources 2 to 4 on its own, so the result for one part does not change
the sources tried for another.

### Declared source

`datasheet` is an optional part field. For example, as `parts.Q1`:

```json
{
  "lcsc": "C20917",
  "value": "AO3400A",
  "lib_id": "Transistor_FET:AO3400A",
  "footprint": "Package_TO_SOT_SMD:SOT-23",
  "group": "switch",
  "datasheet": {
    "url": "https://www.example.com/ao3400a.pdf",
    "sha256": "<SHA-256 of the PDF you checked, as 64 lowercase hex digits>"
  }
}
```

- `url` is required. Use an `http` or `https` URL without credentials or a
  fragment. Capture applies the same rule to redirect targets.
- `sha256` is optional. Give it to fix one recorded version. Capture refuses a
  PDF with a different hash and keeps that PDF under
  `datasheet-attempts/declared/` for inspection.
- Capture gets datasheets per LCSC id, so one declaration applies to every part
  with the same `lcsc`. Parts with the same `lcsc` that both declare a source
  must declare the same one.
- Capture does not fall back to the JLC sources for a declared part. If the
  declared source fails, the capture refuses for that part.
- Compile refuses a capture whose datasheet for the part has another URL, or
  another hash when `sha256` is given. Capture again into a new directory.

### Evidence

```text
datasheet-attempts/declared/      declared sources
datasheet-attempts/signed/        JLC's hosted files
datasheet-attempts/official/      manufacturer links
datasheet-attempts/cascade-<n>/   one datasheet cascade output directory
datasheets/                       the selected PDF of each part and its manifest
datasheet-sources.json            agent-ee.design-datasheet-sources/1
```

An attempt directory exists only when capture tried that source for at least
one part. Each cascade directory has the [cascade output](datasheet-cascade.md#output)
layout, for at most 64 parts. `datasheets/manifest.json` is an
`agent-ee.datasheet-capture/1` manifest whose record IDs are the LCSC ids.
Capture writes `datasheets/` only when every part has a PDF.

`datasheet-sources.json` lists, for each LCSC id, its references, the declared
source or `null`, the outcome (`selected` or `unresolved`), the selected source
and every attempt. An attempt records the source, the JLC field, the URL, the
outcome (`selected`, `failed` or `unavailable`), the reason, the failure class,
whether the failure can be temporary (`transient`), the HTTP status, the
declared `Content-Length` and the received byte count, the PDF hash, both
downloads (`fetches`) when capture downloaded the source twice, and the
acquisition manifest that holds the record. Of these datasheet files, compile
copies only `datasheets/` into the bundle. The attempts and
`datasheet-sources.json` stay in the capture directory, and `capture.json`
records their hashes.

### Failures and refusal

| Failure | Meaning | Can be temporary |
| --- | --- | --- |
| `truncated` | A PDF that stops before its end-of-file marker, with every declared byte received, so the hosted file itself is cut. Without a declared length, capture downloads the source once more, and `truncated` needs the same truncated bytes twice. | no |
| `not_pdf` | No PDF header, for example an HTML page. | no |
| `too_large` | More than the 20 MiB limit. | no |
| `invalid_url` | The URL or a redirect target is not an allowed HTTP(S) URL, for example a redirect to `file:`. | no |
| `tls_verification` | The server's TLS certificate fails verification, for example because it has expired, is untrusted or names another host. | no |
| `http_status` | An HTTP error status. | 408, 425, 429, 500, 502, 503 and 504 only |
| `hash_mismatch` | The PDF's SHA-256 differs from the declared `sha256`. | no |
| `no_url` | The source gives no URL for the part. | no |
| `incomplete_transfer` | Fewer bytes arrived than the server declared. Or a response without a declared length stopped before its end-of-file marker, and a second download returned other truncated bytes or failed in a way that can be temporary. | yes |
| `network` | A DNS, connection, socket timeout, HTTP protocol or other TLS failure. | yes |
| `deadline` | The 30-second fetch deadline expired. Both downloads of one URL share it. | yes |

When the second download of a truncated PDF without a declared length is
complete, capture uses it. A second download that fails in a way that repeats
keeps that class and its advice, for example `tls_verification` or
`invalid_url`. One that runs out of time is `deadline`. `fetches` records both
downloads.

A part without a complete PDF refuses the capture. The refusal has one line for
each such part, under `parts.<ref>.datasheet` for the part's first reference.
The line names the LCSC id and its references. Then it gives each source tried:
its URL without the query string, why it failed, and `[can be temporary]` or
`[repeats on every download]`. The next action follows from those failures:

- A declared `sha256` differs: inspect the copy kept under
  `datasheet-attempts/declared/`, then set `sha256` to its hash, or point `url`
  at the recorded version.
- A failure can be temporary: retry capture into a new directory. If it
  repeats, correct the declared `url`; for a part without a declaration,
  declare a complete copy or choose another part.
- A declared source fails in a way that repeats: correct its `url`, or remove
  the declaration to use the JLC sources.
- Every failure of a part without a declaration repeats: a new capture fails
  in the same way. To keep the part, declare a complete copy in
  `parts.<ref>.datasheet`; otherwise choose another part.

When a TLS certificate fails verification, the line also says to use another
URL with a valid certificate, or to correct the capture host's trust store.
Capture never turns verification off.

For example:

```text
Design refused:
- parts.Q1.datasheet: no complete datasheet PDF for C20917 (Q1); sources tried: (1) JLC's hosted file (dataManualFileAccessIdUrl) https://files.jlc.example/C20917.pdf?...: response is not a complete PDF envelope: the PDF stops after 491520 bytes without its %%EOF end-of-file marker; the file is truncated [repeats on every download]; (2) the manufacturer link in the JLC row (dataManualOfficialLink): the JLC row gives none; (3) the datasheet cascade (dataManualUrl) https://www.lcsc.example/datasheet/C20917.pdf: HTTP Error 404: Not Found [repeats on every download]. Next: every failure repeats on each download, so a new capture fails in the same way; to keep this part, declare a complete copy of its datasheet in parts.Q1.datasheet as {"url": "https://...", "sha256": "<64 lowercase hex digits>"}; otherwise choose another part. datasheet-sources.json in the capture directory lists every source with its byte counts
```

## Declared mechanics

Add `"mechanics": {"access_owners": ["J1"]}` to request the existing
[design-declared mechanics](build-workflow.md#declared-mechanics) path. Owners
must be declared fixed connectors. `packages` can override individual bodies
with `{size_mm: [x, y, z], offset_mm: [x, y]}` in the footprint's **local frame**.
Unspecified bodies use the bounding box of front courtyard line/rectangle
endpoints and the design's maximum component height. Curved or missing
courtyards require explicit sizes. The generated model and its cited
`analysis/body-basis.txt` plainly mark these dimensions as measurement stand-ins;
they are not measured physical bodies.

## Refusal feedback

Independent shape errors are collected before semantic validation; semantic
errors are also collected before compilation. For example:

```text
Design refused:
- board.copper_layers: only two copper layers are supported; set 2
- functional[0].witnesses: add independent pin_net or element_between assertions
```

Correct the named fields and compile again into a new output directory. Missing
captures, absent symbol pins, symbol/footprint mismatches, duplicate terminal
assignments, one-terminal nets, and off-edge connector positions are refused.
Decoupling citations include each stated distance in millimetres. Preflight
requires the existing source-authority checker to qualify them as hard
engineering requirements; an advisory citation cannot satisfy a file requirement.
These limits are labelled "Agent-selected measurement requirement; not an owner
or datasheet ruling". Here `hard` means the build enforces the number stated in
the design; it does not promote that number to an owner or datasheet limit.
Capacitor and device must differ, supply and return terminals must be distinct,
supply and return nets must differ, and the locality limit must be less than the board diagonal.

Each decoupling limit must also be reachable by its two parts. With the front
courtyard boxes apart (touching is allowed) and every quarter-turn rotation,
the validator finds the smallest value of the longer of the two legs. A
`max_distance_mm` below it is refused, at capture's CAD check before any
download and again at compile; the message names both legs and gives that
value. For example, when the two device pads are diagonal corner pins of
`Package_SO:SOIC-8_3.9x4.9mm_P1.27mm` (pins 4 and 8, or 1 and 5), a
`Capacitor_SMD:C_0603_1608Metric` capacitor needs at least 5.335 mm, so a 5 mm
limit is refused. The check uses only these two parts: other parts, the outline
and copper clearance can raise the distance a real placement needs, so passing
it does not prove a placement exists. The placer keeps a movable part's
courtyard box clear of every other part's box, so the check needs at least one
movable part. No check is made when both parts are declared connectors: the
placer does not separate two fixed parts, and their courtyards can be apart
while their boxes overlap. No check is made either when a footprint's front
courtyard is missing or curved, when a land has a drill offset, a chamfer, a
per-layer padstack or a shape other than rect, roundrect, circle or oval, or
when the relation already has another refusal.

A connector's rotated front courtyard must reach within 1 mm of its declared
edge. Along the edge, the courtyard must stay between the edge's two corners.
Compile refuses a courtyard that runs past either corner. The message names
the edge's start corner and direction and gives the range of `position_mm`
values that fit. The fixed-pose gate checks position, rotation and insertion side. Through-edge
connectors require `local_mating_axis` (`x+`, `x-`, `y+`, `y-`) in the footprint
local frame. Compile refuses a rotation that does not face the declared edge; the
retained predicate also checks facing. The axis is a design declaration checked
against pose; physical mouth geometry and mating access remain unverified.
Older envelope records may omit the axis without changing their identity or
meaning; their generated statements do not claim a mating axis.
Board width/height follow the profile's 670/600 mm maxima and must be exact to
one nanometre. The compiler supports height envelopes up to 1000 mm; that bound
is a compiler support limit, not a manufacturer allowance. Intent must parse as a single paragraph in the generated spec. Markdown structure
is refused with the `intent` field name; plain text such as `1.5 V board` is valid.
Producer or native-stage failures retain their own evidence; a successful compile
does not predict a successful physical design.

## Real-run example and claims

The examples are an I2C sensor board and a NE555 astable oscillator with 10 k
resistors and 100 nF capacitors. Round 2 measurements were invalidated by a router runtime mismatch. Their retained
logs under `evidence-r2` are historical failures of verification, not current
router evidence. That round also incorrectly lowered Default track width and
covered only eight exact Device symbols, rather than their name families.
Round 3 uses router pin `995891ae8852f6524815d5f06ad3a062f40ed8e0` from
`track-overshoot-r1/final/router-runtime-r1/checkout`, with native tests and builds
admitted in the production image. The pin file must not be edited to match a runtime.
Round 3 evidence is retained under
`~/.local/share/agent-ee/zeroasic-20261029-agent707/design-compile-r1/evidence-r3`;
its report records actual outcomes, timings, widths and route attempts.

`accepted` means only the typed software file claim. These runs do not establish
supplier confirmation, physical operation, full component or land-pattern
suitability, real body dimensions or mouth direction, independent engineering
review, installation, or interrupted-build resume. Physical, operational and
supplier qualification remain pending.
