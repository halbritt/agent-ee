# AGENTEE-524 U1 reader investigation — agent-569, 2026-10-25

**Finding: the refusal is a corner-correspondence bug in the reader.** The
captured C2862740 rectangle outlines agree with their declared geometry within
the existing 200 nm bound. Lexicographic sorting pairs the wrong vertices when
nearly equal X coordinates differ slightly. A private experiment changing only
that correspondence predicate parses all five pads without raising tolerance,
dropping a pad or changing the emitted declared geometry. No production change
is made by this report. Chief agent-564 owns the repair/integration decision.

## Exact source and scope

Investigation checkout: `fdedf17e2736a37a58a2250f8a59abfcb9cd1e82`. The package
design inspected was `57bcc8f0f344952b33f4f7256139f3f0d284c554`, specifically
`docs/planning/2026-10-25-recovery/contracts/AGENTEE-524-package.md` from that
commit. The dispatch was a new, bounded investigation with no production edits,
project uploads, orders or telemetry-pod work. Investigation stopped once the
counterexample and real-input replay established the cause; no alternate CAD
source or comprehensive CAD audit was needed.

Primary supplier source: **JLCEDA/EasyEDA Official Library**, associated with
LCSC C2862740 / TPS7A2033PDBVR. The official sites are
[JLCEDA](https://lceda.cn/) and [EasyEDA](https://easyeda.com/).

* URL: [C2862740 component response](https://easyeda.com/api/products/C2862740/components?version=6.4.19.5).
* One local GET, HTTP 200, retrieved `2026-10-25T19:40:31.632730+00:00`.
* Complete response: 18,786 bytes, SHA-256
  `ce47f305121ff5bf7311c52cf7a872932429c4b08429b3fa53f4296cf66cf972`.
* Exact UTF-8 `result` object byte span, not reserialized: SHA-256
  `fe9f93d390fb07e1df75380447c0a6788dbeecbaf6010591ed87a25619b1c03f`.
* Component UUID `2e31b5861a784133b2ecb561012a4cf5`, package UUID
  `0578b39d94674c0886345dcbf3a746c7`; package title
  `SOT-23-5_L2.9-W1.6-P0.95-LS2.8-BR`.

Private retained directory:
`/home/halbritt/.cache/agent-verification/agent-ee-569-u1-reader-20261025/`.
It contains the complete response, exact result slice, capture URL/time/hash,
official format page and capture metadata, `probe.py`, `probe-result.json`, and
`parser-tests.log`. The directory is mode 0700. Anonymous Set-Cookie headers
were omitted from metadata. Raw third-party CAD is not committed or published;
origin/licence clearance for fixture publication remains unestablished.

Tool: `/usr/bin/python3`, Python 3.12.3. Reader:
`src/agent_ee/easyeda_footprint.py`, SHA-256
`bc22a90a6d4fa4978b884a7fabbe70c3082d6492953d5d3a1a6193f1f1dfb1fe`.

## Geometry and first divergence

The [official Standard PCB format, PAD section](https://docs.easyeda.com/en/DocumentFormat/3-EasyEDA-PCB-File-Format/index.html#pad)
identifies the shape, centre, width/height, rectangle outline points and rotation
as separate PAD fields. The reader's indices agree with that format. There is
no evidence here of an index or 90-degree width/height interpretation error.
Canvas units are 254,000 nm, with Y down, as used by this reader.

All five pads declare RECT with the same dimensions and a 90-degree rotation.
The declared width/height are 599,998.8 / 999,998 nm before rotation; the expected
axis spans after rotation are therefore 999,998 / 599,998.8 nm. Their outlines
contain four absolute-coordinate points. Centre, dimensions and outline are not
bit-identical redundant descriptions, but their correctly paired corner errors
are all below 200 nm. That existing contract already allows this disagreement.

For pad 3, the outline bounding-box centre differs from the declared centre by
114.3 nm in X and -76.2 nm in Y; its spans differ by 76.2 nm in X and 50.8 nm in
Y. The maximum coordinate error over a valid one-to-one corner correspondence
is 152.4 nm. This does not justify replacing its outline or increasing tolerance.

`_pad` currently compares `zip(sorted(corners), expected)` where `expected` is
also lexicographically sorted. On one vertical edge, slight X differences put
the upper vertex before the lower one, while the ideal rectangle's equal X
coordinates make Y select the opposite order. The first divergence is this
correspondence selection, before conversion to the land-pattern primitives.

| Pad / record | Maximum error using current sorted pairing, nm | Minimum maximum error over all bijections, nm | Existing reader at 200 nm |
| --- | ---: | ---: | --- |
| 2 / gge307 | 127 | 127 | pass |
| 3 / gge308 | 600100.4 | 152.4 | refuses |
| 1 / gge309 | 101.6 | 101.6 | pass |
| 4 / gge310 | 600049.6 | 127 | refuses |
| 5 / gge311 | 127 | 127 | pass |

The full production reader stops at pad 3 with `source_inconsistent/malformed`
at both 200 and 25,400 nm. Pad 4 would encounter the same matching defect.
This explains why raising tolerance to 25,400 nm did not help: the incorrectly
paired vertices are about 600,000 nm apart. Under the existing 200 nm contract,
the observed response does not establish the claimed source inconsistency.

## Decisive experiment and regression contract

The private probe loaded an in-memory copy of the unchanged reader, replacing
only the RECT correspondence condition with an exhaustive four-corner bijection
using the existing `_near` predicate. The production module and source file
were untouched. It evaluated at most 24 permutations and retained the existing
per-coordinate tolerance, dimensions, centre, rotation and refusal paths.

On the exact result bytes at 200 nm:

* Production reader: the expected pad-3 refusal above.
* Private correspondence-only experiment: parses 23 records, five conductive
  pads and no holes. All other records retain their existing noncopper inventory.
* Assertions verify that every emitted pad centre, dimension and angle equals
  the original declared fields under the existing origin/unit transform. No
  geometry is fitted, moved, rounded, omitted or newly approximated.

The probe also passed **124 owned synthetic assertions**: 96 exact rectangles
covering all four quarter turns and all 24 input orders; 24 orders of one
slightly perturbed rectangle; and four negative/boundary controls. These are
diagnostic assertions on the private experiment, not landed regression tests.
The synthetic example uses centre `(0,0)`, width 4, height 2 canvas units and
rotation 0. Moving only the upper-left corner's X by -0.0001 units leaves a
25.4 nm disagreement but reverses the sorted Y pairing. Production refuses at
200 nm; the bijective predicate accepts every input order. It needs no supplier
coordinates or third-party fixture.

The proposed production regression belongs beside
`tests/test_land_pattern.py::SupplierParserTests`. Acceptance for a focused repair:

1. The owned near-rectangle counterexample fails before the repair and passes
   after it, under the unchanged 200 nm bound, for all point orders. Include the
   quarter-turn cases and prove declared output geometry remains unchanged.
2. A genuinely excessive displacement (0.0008 units = 203.2 nm) still refuses at
   200 nm. Duplicated vertices with a missing corner refuse; nearest-neighbour
   existence alone is insufficient because correspondence must be one-to-one.
3. Preserve the inclusive boundary: 0.0005 units passes at exactly 127 nm;
   0.0005001 units refuses at that bound. These exact decimal cases avoid
   floating-point ambiguity.
4. The exact privately retained C2862740 result parses all five pads at 200 nm,
   with unchanged identity, centre/size/angle output and complete record accounting.
   Keep this replay private until origin clearance permits fixture publication.
5. Existing supplier-parser tests remain green; unknown copper, malformed data,
   unqualified shapes and actual source disagreement still refuse. Broader
   land-pattern/CPL native checks follow the justified repair under the supported
   runtime; this dispatch does not claim those passes.

Executed baseline: `PYTHONPATH=src /usr/bin/python3 -m unittest
tests.test_land_pattern.SupplierParserTests -v` — seven tests passed, no skips.
Private probe command: `PYTHONPATH=src /usr/bin/python3
/home/halbritt/.cache/agent-verification/agent-ee-569-u1-reader-20261025/probe.py`.
The 124 assertions and raw-response replay completed successfully. No native
PCB or full orientation check was run, and no production code was changed.

## Recommendation and remaining proof

Recommend the focused order-independent RECT correspondence repair, preserving
the existing tolerance and exact geometry output, then the tests above. A
second CAD source is unnecessary to explain this refusal. Retaining the current
refusal until the repair is accepted remains the safe production behaviour;
neither dropping pad 3 nor loosening tolerance is supported by this evidence.
This report does not qualify other shape readers or change their semantics.

Parsing is only the prerequisite for U1 orientation assessment. After the
accepted repair, run the complete existing `cpl_rotation` comparison against
the exact acquisition native candidate and declared assembly profile, including
pad-set/numbering, pin-1 frame, side, anchor and policy binding. Supplier preview
remains pending. The other 16 placements have **not** been orientation-assessed
by this investigation; other packages parsing is not that proof.

Pincite retrieval-state gate passed against release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. Evidence-backed packet
`pkt-8fa367d8e683e842`, content hash
`8fa367d8e683e842e1127340752c59aa38b6e4496cc26e644fac3f3cc21d2daf`, uses
`universal-evidence-before-intervention`: the matched counterexample distinguishes
reader repair from alternate-source substitution. Runtime concurrency, incident
restoration, broad modernization and unrelated shape qualification obligations
are nonmaterial to this bounded diagnosis. Full orientation and production repair
acceptance remain material and unperformed. Reopen if source bytes/format change
or the focused repair fails a preserved refusal or downstream native check.
