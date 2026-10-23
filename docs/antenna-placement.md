# ESP32 outside-board antenna placement

`antenna_placement` (AGENTEE-453) is a bare-board fabrication gate for one
case: the ESP32-S3-WROOM-1 PCB antenna placed outside the base board. It checks
the saved native board for U1's final pose, which board edge the antenna
crosses, the antenna feed-point side and corner, and the module-owned copper
and placement rule area. Every criterion is exact polygon arithmetic on saved
pcbnew geometry against hash-pinned sources. If an input is missing, the result
is `unknown`, never `pass`.

Sourced geometry and agent policy are kept apart. The antenna and body
rectangles, the feed-point side, the outside-board case and the footprint come
from the pinned sources below. The feed-side corner position does not:
Espressif marks corner positions in Fig. 24/25 but prints no corner distance.
Manufacturer corner compliance is therefore always `unknown`. The corner is
graded only under an explicitly declared agent-derived class default (see
[Authority](#authority)).

It is not installed, enclosure or RF qualification. These stay open physical or
bench obligations in every report (`obligations`), and a pass here never
closes them:

| Obligation | Ledger |
| --- | --- |
| `housing-pocket` | physical |
| `housing-clearance` (the guidelines' separate 15 mm in-housing recommendation) | physical |
| `nearby-metal` | physical |
| `support` | physical |
| `rf-performance` (throughput and range testing of the end product) | bench |

## Sources

All three source files are captured candidate inputs bound to reviewed
SHA-256 pins. Vendor PDFs are never committed. A candidate supplies them, and
the check reads them networklessly.

| Source | Pin | Used for |
| --- | --- | --- |
| Espressif ESP32-S3 Hardware Design Guidelines (current PDF) | `ESP32_GUIDELINES_SHA256` | Section 1.4.7, physical page 33 (printed 30), Fig. 24, and page 34 (printed 31), Fig. 25: the selected case and its marked positions |
| ESP32-S3-WROOM-1 & WROOM-1U Datasheet v1.8 | `ESP32_DATASHEET_SHA256` | Page 45, Figure 11-1: 18 mm body width, 25.5 mm body length, 6 mm antenna area. Page 42, Figure 10-1 top view: feed-point side |
| Stock KiCad 10.0.7 `RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod` | `ESP32_FOOTPRINT_SHA256` | Pad identity and the module-owned keepout rule area |

The guidance is Espressif's text, quoted from section 1.4.7
("General Principles of PCB Layout for Modules (Positioning a Module on a
Base Board)"), page 33:

> It is suggested to place the module’s on-board PCB antenna outside the base
> board, and the feed point of the antenna close to the edge of the base
> board. In the following example figures, positions with mark ✓ are strongly
> recommended, while positions without a mark are not recommended.

Fig. 24 (feed point on the right) marks positions 3 and 4. Fig. 25 (feed point
on the left) marks positions 1 and 5. The datasheet Figure 10-1 top view
draws the antenna trace joining the module body at its right end (the pin 40
side). This is the same antenna drawing as Fig. 24, "feed point on the right".
The check records this reading as `feed_point` with both locators. It is an
agent reading of two figures, not a printed sentence, so it needs independent
engineering review.

The guidelines give no numeric clearance for this case. The Figure 11-1
magenta `15` is pin 15, not a clearance (AGENTEE-211). The 15 mm figure in
section 1.4.7 applies inside the end-product housing only, and stays a
physical obligation. The cutout case (Fig. 26) is not this check:
declarations must carry the exact reviewed `outside_board` policy.

## Check declaration

```json
{"id": "antenna", "kind": "antenna_placement", "params": {"declaration": "project/antenna.json"}}
```

The declaration is an exact `agent-ee.antenna-placement-declaration/1` record:

```json
{
  "schema": "agent-ee.antenna-placement-declaration/1",
  "reference": "U1",
  "policy": {"schema": "agent-ee.antenna-placement-policy/1", "part_number": "ESP32-S3-WROOM-1",
             "placement_case": "outside_board",
             "source": {"path": "project/sources/esp32-guidelines.pdf",
                        "sha256": "057f6df63e10a2dfc155cf19d942adf5780127d839626e2beb4c1a61efeaf3a5",
                        "page": 33, "locator": "Section 1.4.7 RF: module antenna outside the base board"}},
  "module_datasheet": {"path": "project/sources/esp32-s3-wroom-1-datasheet.pdf",
                       "sha256": "27d71971da07c280c6068d08c74720d1a25b8f20cf8494dc1765bdd28d40d435"},
  "footprint": {"path": "project/cad/RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod",
                "sha256": "d0cfbb31fef0c47396c0d061ec5a3bff60f0567d8d4ea8dd4769c84f52bd656f"},
  "edge": {"start_mm": [0, 0], "end_mm": [90, 0]},
  "corner_policy": {"id": "agent-ee.esp32-outside-board-corner-slot/1",
                    "authority": "agent-derived-class-default", "slot": "...", "rule": "...", "basis": "..."}
}
```

`policy` must equal `esp32_s3_wroom_1_placement_policy("outside_board",
pdf_path=...)`. Another case, page, locator or hash refuses capture. Each
source hash must equal its reviewed pin. If a source is captured but its bytes
differ from the pin, capture is refused, for example a lookalike PDF. A source
that is declared but not captured is allowed, and every section that depends
on it becomes `unknown`. `edge` names the straight board-outline segment the
antenna must cross, in board millimetres. If it is `null`, the corner section
is `unknown`. `corner_policy` is `null` or exactly
`agent_ee.antenna_placement.CORNER_POLICY`; any other value, including one that
claims manufacturer authority or another slot, refuses capture. If it is
`null`, the corner section is `unknown` and no corner predicate is advertised. The declaration and the captured sources are immutable inputs.
Launch preflight requires `pcbnew`.

## Native measurement

The handler writes `antenna-request.json` and runs, in the networkless sandbox
(300 s ceiling):

```
/usr/bin/python3 -B -m agent_ee.antenna_placement BOARD --request /work/antenna-request.json --out /work/antenna-native.json
```

The probe never writes the board. It records the board SHA-256 and enabled
copper layers. It records the outer outline from `GetBoardPolygonOutlines`. For
the declared reference it records the pose (FPID, anchor, orientation, side).
It loads the captured footprint separately at the same pose and records its
pads and rule areas next to the saved ones. Pads are compared both as a
canonical record (number, centre, size, shape, layers) and natively: each
saved pad's copper polygon on every enabled copper layer must equal the
captured pad's polygon at the same pose (`pad_geometry_mismatches`), so a pad
rotated in place is a footprint defect while a whole-module rotation is not.
It first checks that its pure
module-frame transform reproduces pcbnew's own pad placement. Then it measures,
with pcbnew polygon booleans:

- `antenna_on_board_nm2`: the Figure 11-1 antenna area intersected with board
  material;
- `body_off_board_nm2`: the module body minus board material;
- `antenna_outside_keepout_nm2`: the antenna area outside the captured keepout;
- `intrusions`: every saved copper item (fills, tracks, vias, flashed pads,
  graphics) on a keepout layer that intersects the captured keepout polygon.

## Grading

The coordinator checks that the report binds the candidate board. It then
grades each section in pure Python:

| Section | Pass | Defect | Unknown |
| --- | --- | --- | --- |
| `sources` | guidance, datasheet and footprint all captured | — | any one not captured |
| `footprint` | exactly one reference, FPID `RF_Module:ESP32-S3-WROOM-1`, saved pads equal to the captured footprint at the pose | reference absent or duplicated, other FPID, differing pads | back side or non-quarter-turn orientation |
| `antenna_outside_board` | zero antenna area on board material | any overlap, including an antenna turned into the board | no single closed outline |
| `feed_point_edge` | body fully on board, so the antenna/body boundary (and the feed point) lies on one straight outline segment | — | body beyond the edge: `feed_point_offset_nm` is reported, but the guidance gives no bound for "close" |
| `feed_side_corner` (class default) | under the declared `corner_policy`: the declared edge is the crossed edge and the feed-side gap along it is below the slot and no larger than the other side | another existing outline edge is crossed; under the policy, the feed-side gap is at least the slot | no `corner_policy`, no declared edge, declared edge not in the outline, or both corners within the slot with the non-feed one nearer |
| `keepout_retained` | the saved module rule area equals the captured one (layers projected onto the enabled layers), on every copper layer, forbidding footprints, pads, tracks, vias and fills, and covering the antenna area | removed, altered or not covering | captured footprint without one straight rule area |
| `keepout_clear` | no saved copper inside the captured keepout | listed `items` | captured keepout absent |

Without the guidance or the datasheet capture, no section can pass. A section
whose measurement passed is reported as `unknown`, with the measured reason
kept.

The overall outcome is `defect` if any section is a defect, otherwise
`unknown` if any section is unknown (execution `unsupported`), otherwise
`pass`. Each defect section is a gate finding
`<reference>:<section>` / `antenna-placement/<section>`. The check keeps
`antenna-request.json`, `antenna-native.json` and the candidate-bound
`antenna-placement.json`. That report records `candidate_id`, `board_sha256`,
`declaration_sha256`, the guidance record and its sealed
`antenna_placement_context`, `feed_point`, `geometry` and `obligations`.

## Authority

Each report carries `authorities` (section to authority class),
`verdict_authority` and `manufacturer_corner_compliance` (always `unknown`).
Every section except `feed_side_corner` is `manufacturer-sourced`: it grades
sourced figures and captured files. The corner slot is
`agent-derived-class-default` (`CORNER_POLICY`): the 18 mm module body width
from datasheet Figure 11-1, chosen by the agent to separate corner from
mid-edge positions. It is neither an Espressif acceptance distance nor a
clearance. `verdict_authority` is `manufacturer-sourced` only when the overall
status is unchanged with the class-default section treated as `unknown`.
Otherwise it is `agent-derived-class-default`. Every overall pass and every
defect decided only by the corner slot therefore carries the class-default
authority. A reviewed sourced basis for a corner distance is needed before
manufacturer corner compliance can be anything but `unknown`.

## Board-fab composition

`agent_ee.antenna_placement.bare_board_claim(run_dir, checks, check_id)` is the
composition point for the board-fab attestation (AGENTEE-449). `checks` is the
complete retained check set of the run. The claim refuses unless:

- the candidate snapshot is unchanged (`candidate.verify_snapshot`: every
  captured input, including each source PDF and the footprint, re-hashed
  against the manifest and the candidate identity) and the immutable baseline
  exists and is unchanged;
- the evidence seal exists and `verify_evidence(required=True)` accepts the
  complete check set, so every retained result and artifact equals its sealed
  digest;
- the named result is a passing `antenna_placement` result of this candidate,
  declared by its scope, with the report and the native measurement in its
  sealed evidence;
- the sealed report equals the report re-derived from the sealed
  `antenna-native.json`, the captured declaration and the captured sources,
  and the result details carry the same values. A coherently re-sealed report
  or result that relabels a verdict or drops obligations is refused here;
- the report is a pass carrying the full obligation ledger.

It returns `{claim: bare-board-antenna-placement, ..., authority_class,
authorities, class_default_policy, manufacturer_corner_compliance: unknown,
open_obligations}`. A pass is a class-default authority claim, never a
manufacturer one. The returned obligations are open by construction: the
attestation must carry them forward and must not mark installed or product
qualification complete.

## Pod status

On fome-mc `8c42f2ee`, `telemetry-pod/telemetry-pod.kicad_pcb` places U1 at
(72.5, 6.5) mm with rotation 0. Run with the real pinned guidance and datasheet
bytes, the check reports `unknown` with one open section, `feed_point_edge`.
The module body overhangs the top edge by 0.25 mm (`feed_point_offset_nm`
250000), so the feed point sits off the edge. Every other section passes,
including `feed_side_corner` with an 8.5 mm feed-side gap. The same board with
U1 at y = 6.75 mm passes all seven sections. These measurements were taken
before the AGENTEE-453 review corrections; under the current check the corner
pass needs the declared class-default `corner_policy` and carries that
authority. The placement choice belongs to
the pod lane and its production capture.
